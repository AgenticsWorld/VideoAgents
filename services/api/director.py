"""导演台 API(/preview/director,2026-09-13):注释台账 / 批量提交 / 版本快照。

前缀 /api/v1/projects/{project}/director/{ep};3D 数据仍走 /whitebox/{ep}(编译结果)与 /previews/storyboard(分镜信息),
本路由只管宿主台账(modules/director.py)。派单与后期页同款:api_chat → POST /api/v1/runs,运行结束后 GET 时回收落版本。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

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
             'has_shots': (base / 'directing' / e / 'shot_list.json').is_file(),
             'has_whitebox': (base / 'directing' / e / 'whitebox' / 'episode.json').is_file() or (base / 'directing' / e / 'whitebox_plans').is_dir(),
             'has_notes': (base / dm.LEDGER_REL.format(ep=e)).is_file()} for e in sorted(eps)]


def _write_compiled(base: Path, ep: str, episode: dict) -> None:
    """与 render_whitebox.py --compile-only 同款落盘:覆盖层改动后其它页面/导出立即看到。"""
    import json
    out = base / 'directing' / ep / 'whitebox'
    out.mkdir(parents=True, exist_ok=True)
    (out / 'episode.json').write_text(json.dumps(episode, ensure_ascii=False, indent=2), encoding='utf-8')
    for sid, scene in episode['scenes'].items():
        d = base / 'assets/concepts/scenes' / sid
        if d.is_dir():
            (d / 'whitebox.scene.json').write_text(json.dumps(scene, ensure_ascii=False, indent=2), encoding='utf-8')


def _reconcile(base: Path, ep: str, doc: dict, episode: dict) -> list:
    plain = None
    if any(b.get('status') == 'running' for b in doc.get('batches', [])) and dm.load_overrides(base, ep).get('groups'):
        plain = compile_episode(base, ep, apply_overrides=False)
    created = dm.reconcile(base, ep, doc, episode, core._post_run_alive, plain_episode=plain)
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
            'whitebox_enabled': (settings.get('output') or {}).get('spatial_blocking') is True,
            'ledger': {k: doc.get(k) for k in ('notes', 'batches', 'versions', 'current', 'updated_at')},
            'overrides': dm.load_overrides(base, ep).get('groups', {}),
            'marks': dm.load_marks(base, ep).get('scenes', {}),
            'approvals': dm.approvals_public(doc, episode),
            'gate': _gate(base, ep),
            'routes': dm.ROUTES,
            'summary': dm.summary(doc), 'avatars': avatars,
            'labels': {'target': dm.TARGET_LABEL, 'note_status': dm.NOTE_STATUS_LABEL, 'batch_status': dm.BATCH_STATUS_LABEL}}


def _gate(base: Path, ep: str) -> dict:
    """H3W 白模签字状态(同后期页 _post_gate 口径):dag 里 g6w 节点 + 待答复的 H3W 签字卡。"""
    node = None
    for n in core._dag_load_nodes(base / 'runs' / 'dag.json'):
        nid = str(n.get('id') or '')
        if nid == 'g6w' or (nid.startswith('g6w-') and ep in nid) or (str(n.get('checkpoint') or '').upper().startswith('H3W') and ep in nid):
            node = n
            break
    cards = []
    for c in core.CONFIRMS.values():
        if c.get('answer') is not None or c.get('kind') != 'sign' or c.get('project') != base.name:
            continue
        cp, q = str(c.get('checkpoint') or ''), str(c.get('question') or '')
        if not (cp.upper().startswith('H3W') or 'H3W' in q or 'g6w' in str(c.get('gate_id') or '')):
            continue
        gid = str(c.get('gate_id') or '')
        if ep in gid or ep in q or (not gid and ep not in q) or gid == 'g6w':
            cards.append({k: c.get(k) for k in ('id', 'question', 'options', 'default', 'gate_id', 'checkpoint')})
    signed = bool(node and node.get('state') in core._DONE_STATES)
    return {'gate_id': (node or {}).get('id') or 'g6w', 'state': (node or {}).get('state'), 'in_dag': bool(node),
            'pending': cards, 'signed': signed, 'checkpoint': 'H3W-白模确认'}


def _load(project: str, ep: str):
    base = project_path(project, ep)
    doc = dm.load_ledger(base, ep)
    episode = compile_episode(base, ep)
    return base, doc, episode


@router.get('')
async def director_get(project: str, ep: str):
    def _do():
        base = project_path(project, ep)
        try:
            base, doc, episode = _load(project, ep)
        except FileNotFoundError as e:
            # 该集还没有分镜表/白模:404 仍带分集列表,页面才能出选集下拉切到有数据的集
            return JSONResponse({'detail': str(e), 'episodes': _episodes(base)}, status_code=404)
        _reconcile(base, ep, doc, episode)
        return _public(base, ep, doc, episode)
    return await checked(_do)


@router.post('/notes')
async def note_create(project: str, ep: str, body: dict):
    def _do():
        base = project_path(project, ep)
        doc = dm.load_ledger(base, ep)
        dm.require_plan(base, ep, [component(str(body.get('group_id') or ''))])
        note = dm.add_note(doc, dm.make_note(str(body.get('group_id') or ''), body.get('target'), body.get('text'),
                                            t=body.get('t'), shot_id=body.get('shot_id'), base_v=body.get('base_v'), view=body.get('view'),
                                            target_agent=body.get('target_agent')))
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
    """把 draft 注释(可按组/按 id 筛)+ 已裁决待决项 + 覆盖层打成批次派单;按路由拆批:白模调度一批(带待决项/覆盖层),
    其它工位各一批(只带该路由注释)。一批 = 一次运行;白模批次回收时按编译指纹落版本。"""
    body = body or {}
    base, doc, episode = await checked(_load, project, ep)
    group_ids, note_ids = body.get('group_ids') or None, body.get('note_ids') or None
    agents = dm.draft_agents(doc, group_ids=group_ids, note_ids=note_ids)
    if dm.AGENT_ID not in agents:
        agents.insert(0, dm.AGENT_ID)   # 没有白模注释也可能有待决项/覆盖层要提交
    prepared_all, skipped = [], []
    for agent in agents:
        try:
            prepared_all.append(await checked(dm.prepare_batch, base, ep, doc, episode, group_ids=group_ids, note_ids=note_ids, agent=agent))
        except HTTPException as e:
            if e.status_code == 422:
                skipped.append({'agent': agent, 'reason': e.detail})
                continue
            raise
    if not prepared_all:
        raise HTTPException(422, skipped[0]['reason'] if skipped else '没有可提交的内容')
    await checked(dm.require_plan, base, ep, [g for p in prepared_all for g in p['group_ids']])
    if body.get('dry_run'):
        return {'ok': True, 'dry_run': True, 'batches': [{'agent': p['agent'], 'group_ids': p['group_ids'], 'notes': [n['id'] for n in p['notes']],
                                                         'issues': {k: [i.get('issue_id') for i in v] for k, v in p['issues'].items()}, 'message': p['message']} for p in prepared_all],
                'skipped': skipped}
    running = [b for b in doc.get('batches', []) if b.get('status') == 'running']
    for p in prepared_all:
        if any((b.get('agent') or dm.AGENT_ID) == p['agent'] and set(b.get('group_ids', [])) & set(p['group_ids']) for b in running):
            raise HTTPException(409, f"「{dm.ROUTES[p['agent']]}」在这些组已有批次在运行,等它回收后再提交")
    out = []
    for p in prepared_all:
        try:
            res = await core.api_chat({'agent': p['agent'], 'message': p['message'], 'project': base.name, 'source': 'user'})
        except core.ServiceError as e:
            raise HTTPException(e.status_code, e.detail) from e

        def _commit(p=p, res=res):
            doc2 = dm.load_ledger(base, ep)
            batch = dm.commit_batch(base, ep, doc2, episode, p, run_id=res.get('run_id'), agent=p['agent'])
            dm.save_ledger(base, ep, doc2)
            return {'batch': batch, 'run_id': res.get('run_id'), 'agent': p['agent']}
        out.append(await checked(_commit))
    doc3 = dm.load_ledger(base, ep)
    return {'ok': True, 'batches': out, 'batch': out[0]['batch'], 'run_id': out[0]['run_id'], 'agent': out[0]['agent'],
            'skipped': skipped, 'summary': dm.summary(doc3)}


@router.post('/snapshot')
async def snapshot(project: str, ep: str, body: dict | None = None):
    """手动把某组当前编译结果存为版本(比对基线 / 留档)。"""
    body = body or {}

    def _do():
        base, doc, episode = _load(project, ep)
        gid = str(body.get('group_id') or '')
        component(gid)
        dm.require_plan(base, ep, [gid])
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


# ---- 覆盖层(v2):拖动/数值改定的整条关键帧,按组按对象盖在 Agent 计划之上 ----
def _compiled_group(episode: dict, gid: str) -> dict:
    g = next((g for g in episode['groups'] if g['group_id'] == gid), None)
    if not g:
        err = next((e['error'] for e in episode.get('errors', []) if e.get('group_id') == gid), '本组没有白模编译结果')
        raise ValueError(f'{gid}:{err}')
    return g


@router.put('/overrides/{gid}')
async def overrides_set(project: str, ep: str, gid: str, body: dict):
    """body = {actors:{id:{keyframes}|null}, extras:{}, props:{}, cameras:{shot:{keyframes}|null}};值 null 删除该对象覆盖。
    保存后立即重编译落盘 episode.json,返回该组编译结果(含 overrides 标记)。"""
    if not isinstance(body, dict):
        raise HTTPException(422, 'body must be an object')

    def _do():
        base = project_path(project, ep, gid)
        dm.require_plan(base, ep, [gid])
        ov = dm.load_overrides(base, ep)
        group_ov = dm.set_group_overrides(ov, gid, body)
        dm.save_overrides(base, ep, ov)
        episode = compile_episode(base, ep)
        _write_compiled(base, ep, episode)
        group = _compiled_group(episode, gid)
        core.HUB.publish({'type': 'director', 'project': base.name, 'ep': ep, 'overrides': [gid]})
        return {'ok': True, 'group': group, 'scene': episode['scenes'][group['scene_id']], 'overrides': group_ov,
                'actor_colors': episode.get('actor_colors') or {}}
    return await checked(_do)


@router.delete('/overrides/{gid}')
async def overrides_clear(project: str, ep: str, gid: str, kind: str = '', id: str = ''):
    """丢弃覆盖(整组,或 ?kind=actors&id=CHAR-0001 单对象)→ 回到 Agent 计划。"""
    def _do():
        base = project_path(project, ep, gid)
        if kind and kind not in dm.OVERRIDE_KINDS:
            raise ValueError('kind 须为 ' + '|'.join(dm.OVERRIDE_KINDS))
        ov = dm.load_overrides(base, ep)
        dm.clear_group_overrides(ov, gid, kind or None, id or None)
        dm.save_overrides(base, ep, ov)
        episode = compile_episode(base, ep)
        _write_compiled(base, ep, episode)
        group = _compiled_group(episode, gid)
        core.HUB.publish({'type': 'director', 'project': base.name, 'ep': ep, 'overrides': [gid]})
        return {'ok': True, 'group': group, 'scene': episode['scenes'][group['scene_id']], 'overrides': ov.get('groups', {}).get(gid, {})}
    return await checked(_do)


# ---- v3:地面定位标记 / 组级批准 / H3W 签字 ----
@router.put('/marks/{sid}')
async def marks_set(project: str, ep: str, sid: str, body: dict):
    if not isinstance(body, dict):
        raise HTTPException(422, 'body must be an object')

    def _do():
        base = project_path(project, ep, sid)
        doc = dm.load_marks(base, ep)
        marks = dm.set_scene_marks(doc, sid, body.get('marks') or [])
        dm.save_marks(base, ep, doc)
        return {'ok': True, 'scene_id': sid, 'marks': marks}
    return await checked(_do)


@router.post('/approve')
async def approve(project: str, ep: str, body: dict | None = None):
    body = body or {}

    def _do():
        base, doc, episode = _load(project, ep)
        gid = str(body.get('group_id') or '')
        component(gid)
        if body.get('approve', True):
            dm.require_plan(base, ep, [gid])
        group = next((g for g in episode['groups'] if g['group_id'] == gid), None)
        sha = dm.group_sha(episode, group) if group else None
        rec = dm.set_approval(doc, gid, sha, bool(body.get('approve', True)))
        dm.save_ledger(base, ep, doc)
        # 批准本组 = 该组未答复的待决项按当前白模接受(waived),流程不再卡在这些项上;取消批准则撤回
        from modules.whitebox_issues import collect, waive_group
        waived = waive_group(base, ep, gid, bool(body.get('approve', True)))
        col = collect(base, ep) if waived else None
        out = {'ok': True, 'group_id': gid, 'approval': rec, 'approvals': dm.approvals_public(doc, episode), 'summary': dm.summary(doc), 'waived': waived}
        if col:
            out['issues'] = next((g['issues'] for g in col['groups'] if g['group_id'] == gid), [])
            out['issues_summary'] = col['summary']
        return out
    return await checked(_do)


@router.post('/signoff')
async def signoff(project: str, ep: str, body: dict | None = None):
    """答复 H3W 白模签字卡(与控制台同一条);阻断级待决项未清时宿主拒签(409)。"""
    body = body or {}
    base = project_path(project, ep)
    cid = str(body.get('confirm_id') or '')
    answer = core.canonical_choice(body.get('answer'), 'sign')   # 各语言译文归一;缺答复不默认成签字
    if not answer:
        raise HTTPException(400, 'answer must be 签字 or 暂缓')
    if not cid or cid not in core.CONFIRMS:
        raise HTTPException(404, 'sign-off card not found (it may have been answered from the console)')
    try:
        res = await core.api_confirm_answer(cid, {'answer': answer})
    except core.ServiceError as e:
        raise HTTPException(e.status_code, e.detail) from e
    return {'ok': True, 'confirm': res, 'gate': _gate(base, ep)}
