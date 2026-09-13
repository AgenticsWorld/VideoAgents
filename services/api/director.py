"""导演台 API(/preview/director,2026-09-13):注释台账 / 批量提交 / 版本快照。

前缀 /api/v1/projects/{project}/director/{ep};3D 数据仍走 /whitebox/{ep}(编译结果)与 /previews/storyboard(分镜信息),
本路由只管宿主台账(modules/director.py)。派单与后期页同款:api_chat → POST /api/v1/runs,运行结束后 GET 时回收落版本。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException

from modules import director as dm
from modules.whitebox import compile_episode, component, read
from services.runtime import core

router = APIRouter(prefix='/projects/{project}/director/{ep}', tags=['artifacts'])


def project_path(project, *ids):
    try:
        for name in (project, *ids):
            component(name)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    base = (core.PROJECTS_DIR / project).resolve()
    if not base.is_relative_to(core.PROJECTS_DIR.resolve()) or not base.is_dir():
        raise HTTPException(404, 'Project not found')
    return base


async def checked(fn, *args, **kw):
    try:
        return await asyncio.to_thread(fn, *args, **kw)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except KeyError as e:
        raise HTTPException(404, f'not found: {e}') from e
    except (ValueError, TypeError) as e:
        raise HTTPException(422, str(e)) from e


def _episodes(base: Path) -> list:
    plan = read(base / 'story' / 'episode_plan.json', {}) or {}
    titles = {e.get('ep'): e.get('title', '') for e in plan.get('episodes', []) if isinstance(e, dict) and e.get('ep')}
    eps = set(titles)
    for sub in ('story/episodes', 'directing'):
        d = base / sub
        if d.is_dir():
            eps |= {x.name for x in d.iterdir() if x.is_dir() and not x.name.startswith('.')}
    return [{'ep': e, 'title': titles.get(e, ''),
             'has_whitebox': (base / 'directing' / e / 'whitebox' / 'episode.json').is_file() or (base / 'directing' / e / 'whitebox_plans').is_dir(),
             'has_notes': (base / dm.LEDGER_REL.format(ep=e)).is_file()} for e in sorted(eps)]


def _reconcile(base: Path, ep: str, doc: dict, episode: dict) -> list:
    created = dm.reconcile(base, ep, doc, episode, core._post_run_alive)
    if created:
        dm.save_ledger(base, ep, doc)
        core.HUB.publish({'type': 'director', 'project': base.name, 'ep': ep,
                          'versions': [{'group_id': next((g for g, vs in doc['versions'].items() if rec in vs), None), 'v': rec['v']} for rec in created]})
    return created


def _public(base: Path, ep: str, doc: dict, episode: dict) -> dict:
    ids = set()
    for g in episode.get('groups', []):
        for a in g.get('actors', []) + g.get('extras', []) + g.get('props', []):
            ids.add(a.get('id'))
        for a in g.get('scene_cast', []) or []:
            ids.add(a if isinstance(a, str) else (a or {}).get('id'))
    avatars = {}
    for oid in sorted(i for i in ids if i):
        rel = dm.avatar_rel(base, oid)
        if rel:
            avatars[oid] = f'/api/v1/projects/{quote(base.name)}/artifacts/{rel}'
    settings = read(base / 'settings.json', {}) or {}
    return {'project': base.name, 'ep': ep, 'episodes': _episodes(base), 'agent': dm.AGENT_ID,
            'whitebox_enabled': (settings.get('output') or {}).get('spatial_blocking', True) is not False,
            'ledger': {k: doc.get(k) for k in ('notes', 'batches', 'versions', 'current', 'updated_at')},
            'summary': dm.summary(doc), 'avatars': avatars,
            'labels': {'target': dm.TARGET_LABEL, 'note_status': dm.NOTE_STATUS_LABEL, 'batch_status': dm.BATCH_STATUS_LABEL}}


def _load(project: str, ep: str):
    base = project_path(project, ep)
    doc = dm.load_ledger(base, ep)
    episode = compile_episode(base, ep)
    return base, doc, episode


@router.get('')
async def director_get(project: str, ep: str):
    def _do():
        base, doc, episode = _load(project, ep)
        _reconcile(base, ep, doc, episode)
        return _public(base, ep, doc, episode)
    return await checked(_do)


@router.post('/notes')
async def note_create(project: str, ep: str, body: dict):
    def _do():
        base = project_path(project, ep)
        doc = dm.load_ledger(base, ep)
        note = dm.add_note(doc, dm.make_note(str(body.get('group_id') or ''), body.get('target'), body.get('text'),
                                            t=body.get('t'), shot_id=body.get('shot_id'), base_v=body.get('base_v'), view=body.get('view')))
        dm.save_ledger(base, ep, doc)
        return {'ok': True, 'note': note, 'summary': dm.summary(doc)}
    if not isinstance(body, dict):
        raise HTTPException(422, 'body must be an object')
    return await checked(_do)


@router.put('/notes/{nid}')
async def note_update(project: str, ep: str, nid: str, body: dict):
    def _do():
        base = project_path(project, ep)
        doc = dm.load_ledger(base, ep)
        note = dm.find_note(doc, nid)
        if not note:
            raise KeyError(nid)
        dm.update_note(note, body)
        dm.save_ledger(base, ep, doc)
        return {'ok': True, 'note': note, 'summary': dm.summary(doc)}
    if not isinstance(body, dict):
        raise HTTPException(422, 'body must be an object')
    return await checked(_do)


@router.delete('/notes/{nid}')
async def note_delete(project: str, ep: str, nid: str):
    def _do():
        base = project_path(project, ep)
        doc = dm.load_ledger(base, ep)
        dm.delete_note(doc, nid)
        dm.save_ledger(base, ep, doc)
        return {'ok': True, 'summary': dm.summary(doc)}
    return await checked(_do)


@router.post('/submit')
async def submit(project: str, ep: str, body: dict | None = None):
    """把 draft 注释(可按组/按 id 筛)+ 已裁决待决项打成一个批次派给白模调度 Agent;一批 = 一次运行 = 一个版本。"""
    body = body or {}
    base, doc, episode = await checked(_load, project, ep)
    prepared = await checked(dm.prepare_batch, base, ep, doc, episode,
                             group_ids=body.get('group_ids') or None, note_ids=body.get('note_ids') or None)
    if body.get('dry_run'):
        return {'ok': True, 'dry_run': True, 'group_ids': prepared['group_ids'], 'notes': [n['id'] for n in prepared['notes']],
                'issues': {k: [i.get('issue_id') for i in v] for k, v in prepared['issues'].items()}, 'message': prepared['message']}
    if any(b.get('status') == 'running' and set(b.get('group_ids', [])) & set(prepared['group_ids']) for b in doc.get('batches', [])):
        raise HTTPException(409, '这些组已有批次在运行,等它回收后再提交')
    try:
        res = await core.api_chat({'agent': dm.AGENT_ID, 'message': prepared['message'], 'project': base.name, 'source': 'user'})
    except core.ServiceError as e:
        raise HTTPException(e.status_code, e.detail) from e

    def _commit():
        doc2 = dm.load_ledger(base, ep)
        batch = dm.commit_batch(base, ep, doc2, episode, prepared, run_id=res.get('run_id'))
        dm.save_ledger(base, ep, doc2)
        return {'ok': True, 'batch': batch, 'run_id': res.get('run_id'), 'agent': dm.AGENT_ID, 'summary': dm.summary(doc2)}
    return await checked(_commit)


@router.post('/snapshot')
async def snapshot(project: str, ep: str, body: dict | None = None):
    """手动把某组当前编译结果存为版本(比对基线 / 留档)。"""
    body = body or {}

    def _do():
        base, doc, episode = _load(project, ep)
        gid = str(body.get('group_id') or '')
        component(gid)
        group = next((g for g in episode['groups'] if g['group_id'] == gid), None)
        if group and dm.latest_sha(doc, gid) == dm.group_sha(episode, group):
            raise ValueError('当前白模与最新版本相同,不必重复存版')
        rec = dm.snapshot(base, ep, doc, episode, gid, label=str(body.get('label') or '手动存版')[:80])
        dm.save_ledger(base, ep, doc)
        return {'ok': True, 'version': rec, 'versions': dm.versions_of(doc, gid)}
    return await checked(_do)


@router.get('/versions/{gid}/{v}')
async def version_get(project: str, ep: str, gid: str, v: int):
    base = project_path(project, ep, gid)
    return await checked(dm.load_version, base, ep, gid, v)
