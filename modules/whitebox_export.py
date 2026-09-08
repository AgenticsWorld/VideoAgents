"""Offline Three.js frame rendering -> one exact-duration H.264 MP4 camera-view reference (camera.mp4).

2026-09-08:不再导出俯视 top.mp4(用户指令:白模参考视频仅摄影机视角);俯视只在预览页交互查看。
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from modules.whitebox import component, read, render_format

STATIC = Path(__file__).resolve().parents[1] / 'apps/web/static'
_EXPORT_LOCK = threading.Lock()


def fingerprint(episode, group):
    scene = episode['scenes'][group['scene_id']]
    return hashlib.sha256(json.dumps([scene, group], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def renderer_fingerprint():
    digest = hashlib.sha256()
    digest.update(Path(__file__).read_bytes())
    for name in ('whitebox-renderer.js', 'whitebox-export.html', 'vendor/three/three.module.js',
                 'vendor/three/three.core.js', 'vendor/three/OrbitControls.js'):
        path = STATIC/name
        if path.is_file():
            digest.update(name.encode()); digest.update(path.read_bytes())
    return digest.hexdigest()


def ensure_videos(base, episode, group_ids=None, *, width=None, height=None, fps=24, progress=None, force=False):
    """Save missing/stale camera videos automatically after a modeling update."""
    fmt = render_format(read(base/'settings.json', {}), width, height)
    if isinstance(fps, bool) or not isinstance(fps, int) or not 1 <= fps <= 60:
        raise ValueError('Export fps must be an integer within 1..60')
    if episode.get('render', {}).get('aspect_ratio', fmt['aspect_ratio']) != fmt['aspect_ratio']:
        raise ValueError('Project aspect changed; recompile the episode before exporting')
    ep = component(episode['ep'])
    groups = episode['groups'] if group_ids is None else [g for g in episode['groups'] if g['group_id'] in group_ids]
    if not groups or (group_ids is not None and set(group_ids) != {g['group_id'] for g in groups}):
        raise ValueError('Requested groups are unavailable; check compilation errors')
    renderer_hash = renderer_fingerprint()
    pending, skipped = [], []
    for group in groups:
        gid = component(group['group_id'])
        folder = base/'assets/whitebox'/ep/gid
        try:
            record = read(folder/'manifest.json', {})
        except (ValueError, OSError):
            record = {}
        files = [f'assets/whitebox/{ep}/{gid}/camera.mp4']
        current = (isinstance(record, dict) and record.get('source_sha256') == fingerprint(episode, group)
                   and record.get('renderer_sha256') == renderer_hash and record.get('fps') == fps
                   and all(record.get(k) == v for k, v in fmt.items())
                   and record.get('files') == files
                   and all((base/path).is_file() and (base/path).stat().st_size > 0 for path in files))
        (skipped if current and not force else pending).append(gid)
    rendered = render_videos(base, episode, pending, width=width, height=height, fps=fps, progress=progress) if pending else []
    return {'rendered': [r['group_id'] for r in rendered], 'skipped': skipped}


def render_videos(base, episode, group_ids=None, *, width=None, height=None, fps=24, progress=None):
    fmt = render_format(read(base / 'settings.json', {}), width, height)
    if episode.get('render', {}).get('aspect_ratio', fmt['aspect_ratio']) != fmt['aspect_ratio']:
        raise ValueError('Project aspect changed; recompile the episode before exporting')
    width, height = fmt['width'], fmt['height']
    if isinstance(fps, bool) or not isinstance(fps, int) or not 1 <= fps <= 60:
        raise ValueError('Export fps must be an integer within 1..60')
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('未找到 FFmpeg，请先安装并加入 PATH。')
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError('缺少 Playwright，请安装项目依赖并运行 python -m playwright install chromium。') from e
    ep = component(episode['ep'])
    groups = episode['groups'] if group_ids is None else [g for g in episode['groups'] if g['group_id'] in group_ids]
    if not groups or (group_ids is not None and set(group_ids) != {g['group_id'] for g in groups}):
        raise ValueError('Requested groups are unavailable; check compilation errors')
    payload = {'episode': episode, 'width': width, 'height': height}
    # The renderer is entirely local: no temporary HTTP service or network access.
    with _EXPORT_LOCK:
        results=[]
        with sync_playwright() as p:
            kwargs={'headless':True, 'args':['--enable-webgl', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--allow-file-access-from-files']}
            executable=os.environ.get('VIDEOAGENTS_CHROMIUM')
            if executable:
                kwargs['executable_path']=executable
            browser=p.chromium.launch(**kwargs)
            try:
                page=browser.new_page(viewport={'width':width,'height':height})
                page.add_init_script('window.whiteboxExportData = '+json.dumps(payload,ensure_ascii=False)+';')
                page.goto((STATIC/'whitebox-export.html').as_uri())
                page.wait_for_function('window.whiteboxReady === true',timeout=60000)
                for group in groups:
                    gid=component(group['group_id']);duration=group['duration_s'];frames=math.ceil(duration*fps-1e-7)
                    output=base/'assets/whitebox'/ep/gid;output.mkdir(parents=True,exist_ok=True)
                    page.evaluate('(gid)=>window.whiteboxExport.load(gid)',gid)
                    # Stage the camera video before publishing; failed jobs retain prior valid exports.
                    with tempfile.TemporaryDirectory(prefix='.render-',dir=output) as staging:
                        staging=Path(staging)
                        command=[ffmpeg,'-hide_banner','-loglevel','error','-y','-f','image2pipe','-vcodec','mjpeg','-framerate',str(fps),'-i','pipe:0',
                                 '-c:v','libx264','-preset','fast','-crf','20','-pix_fmt','yuv420p','-t',str(duration),'-movflags','+faststart',str(staging/'camera.mp4')]
                        with tempfile.TemporaryFile() as errors:
                            proc=subprocess.Popen(command,stdin=subprocess.PIPE,stderr=errors)
                            try:
                                for i in range(frames):
                                    frame=page.evaluate('(t)=>window.whiteboxExport.frame(t)',i/fps)
                                    proc.stdin.write(base64.b64decode(frame))
                                    if progress and (i % fps == 0 or i == frames-1):progress(gid,round(100*(i+1)/frames))
                                proc.stdin.close()
                                if proc.wait(timeout=60):
                                    errors.seek(0);raise RuntimeError(errors.read().decode(errors='replace')[-3000:])
                            finally:
                                if proc.stdin and not proc.stdin.closed:proc.stdin.close()
                                if proc.poll() is None:proc.kill();proc.wait()
                        record={'schema_version':'whitebox_export.v1','group_id':gid,'duration_s':duration,'fps':fps,'frames':frames,
                                **fmt,'source_sha256':fingerprint(episode,group),'renderer_sha256':renderer_fingerprint(),
                                'files':[f'assets/whitebox/{ep}/{gid}/camera.mp4']}
                        os.replace(staging/'camera.mp4',output/'camera.mp4')
                        # 旧版双视角导出遗留的 top.mp4 不再维护,顺手清掉以免被误当参考视频
                        stale=output/'top.mp4'
                        if stale.exists():stale.unlink()
                        (staging/'manifest.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
                        os.replace(staging/'manifest.json',output/'manifest.json');results.append(record)
            finally:
                browser.close()
        return results
