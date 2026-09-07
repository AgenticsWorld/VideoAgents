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
    return await checked(load_scene,project_path(project,sid),sid)


@router.get('/{ep}')
async def episode(project: str,ep: str):
    return await checked(compile_episode,project_path(project,ep),ep)


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
