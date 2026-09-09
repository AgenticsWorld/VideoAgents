"""Whitebox preview and bounded background export API."""
from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException

from modules.whitebox import compile_episode, component, load_scene, read
from modules.whitebox_export import render_videos
from modules.whitebox_refs import sync_episode
from services.runtime import core

router=APIRouter(prefix='/projects/{project}/whitebox',tags=['artifacts'])
_jobs={}
_lock=threading.Lock()


def project_path(project, *ids):
    try:
        for name in (project,*ids):component(name)
    except ValueError as e:raise HTTPException(400,str(e)) from e
    base=(core.PROJECTS_DIR/project).resolve()
    if not base.is_relative_to(core.PROJECTS_DIR.resolve()) or not base.is_dir():
        raise HTTPException(404,'Project not found')
    return base


async def checked(fn,*args):
    try:return await asyncio.to_thread(fn,*args)
    except FileNotFoundError as e:raise HTTPException(404,str(e)) from e
    except (ValueError,KeyError,TypeError) as e:raise HTTPException(422,str(e)) from e


@router.get('/scenes/{sid}')
async def scene(project: str,sid: str):
    base=project_path(project,sid)
    result=await checked(load_scene,base,sid)
    result['artifact_status']={'model':(base/'bible/scenes'/sid/'whitebox.json').is_file()}
    return result


@router.get('/{ep}')
async def episode(project: str,ep: str):
    base=project_path(project,ep)
    result=await checked(compile_episode,base,ep)
    for sid, item in result['scenes'].items():
        item['artifact_status']={'model':(base/'bible/scenes'/sid/'whitebox.json').is_file()}
    preview=(base/'directing'/ep/'whitebox/episode.json').is_file()
    for group in result['groups']:
        gid=group['group_id']
        group['artifact_status']={
            'plan':(base/'directing'/ep/'whitebox_plans'/f'{gid}.json').is_file(),
            'preview':preview,
            'video':(base/'assets/whitebox'/ep/gid/'camera.mp4').is_file(),
        }
    return result


# ---- 待决项裁决(docs/whitebox.md「待决项与用户裁决」,2026-09-09):预览页组卡按钮 → 写 directing/<ep>/whitebox/decisions.json ----
@router.get('/{ep}/issues')
async def issues(project: str,ep: str):
    """不经完整编译直接读计划 + decisions 汇总(阻断级未清则 H3W 签字被拒;同 code/whitebox_issues.py --status)。"""
    from modules.whitebox_issues import collect,format_summary
    base=project_path(project,ep)
    data=await checked(collect,base,ep)
    data['text']=format_summary(data['summary'])
    return data


@router.post('/{ep}/issues/{issue_id}/decision')
async def decide_issue(project: str,ep: str,issue_id: str,body: dict):
    """写一条用户裁决:choice ∈ 选项 id | provisional | custom(note 必填)。返回合并后的 issue 与整集汇总。"""
    from modules.whitebox_issues import collect,decide,format_summary
    base=project_path(project,ep)
    if not isinstance(body,dict):raise HTTPException(422,'body must be an object')
    issue=await checked(decide,base,ep,issue_id,str(body.get('choice') or ''),str(body.get('note') or ''),'user:page')
    summary=(await checked(collect,base,ep))['summary']
    return {'issue':issue,'summary':summary,'text':format_summary(summary)}


def file_links(project,record):
    return [{'name':Path(path).name,'url':f'/api/v1/projects/{quote(project)}/artifacts/{path}'} for path in record.get('files',[])]


@router.get('/{ep}/exports/{gid}')
async def export_status(project: str,ep: str,gid: str):
    base=project_path(project,ep,gid)
    with _lock:job=dict(_jobs.get((str(base),ep,gid),{}))
    if job:return job
    record=read(base/'assets/whitebox'/ep/gid/'manifest.json')
    if record and all((base/path).is_file() for path in record.get('files',[])):
        return {'status':'complete','progress':100,'files':file_links(project,record)}
    return {'status':'missing'}


@router.post('/{ep}/exports/{gid}',status_code=202)
async def start_export(project: str,ep: str,gid: str):
    base=project_path(project,ep,gid)
    data=await checked(compile_episode,base,ep)
    group=next((g for g in data['groups'] if g['group_id']==gid),None)
    if not group:
        error=next((e['error'] for e in data['errors'] if e['group_id']==gid),'Group not found')
        raise HTTPException(422,error)
    key=(str(base),ep,gid)
    with _lock:
        if any(j['status']=='running' for j in _jobs.values()):
            raise HTTPException(409,'已有白模导出任务运行，请完成后重试。')
        # Only keep active/recent jobs; completed exports are recoverable from disk.
        _jobs.clear();_jobs[key]={'status':'running','progress':0}
    def worker():
        try:
            def progress(_,pct):
                with _lock:_jobs[key]['progress']=pct
            records=render_videos(base,data,[gid],progress=progress)
            # 与 render_whitebox.py 同步:导出即接成该组视频生成的参考视频(仅「人物精确空间位置」开启,2026-09-07)
            sync=sync_episode(base,ep,[gid],write=True) if (read(base/'settings.json',{}) or {}).get('output',{}).get('spatial_blocking',True) is not False else None
            with _lock:_jobs[key]={'status':'complete','progress':100,'files':file_links(project,records[0]),
                                   'whitebox_refs':(sync['groups'][0] if sync and sync['groups'] else None)}
        except Exception as e:
            with _lock:_jobs[key]={'status':'failed','error':str(e)}
    threading.Thread(target=worker,daemon=True,name=f'whitebox-{gid}').start()
    return {'status':'running','progress':0}
