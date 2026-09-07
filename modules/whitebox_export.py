"""Offline Three.js frame rendering -> two exact-duration H.264 MP4 references."""
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

from modules.whitebox import component

STATIC = Path(__file__).resolve().parents[1] / 'apps/web/static'
_EXPORT_LOCK = threading.Lock()


def fingerprint(episode, group):
    scene = episode['scenes'][group['scene_id']]
    return hashlib.sha256(json.dumps([scene, group], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def render_videos(base, episode, group_ids=None, *, width=960, height=540, fps=24, progress=None):
    if not 128 <= width <= 1920 or not 128 <= height <= 1080 or width % 2 or height % 2 or not 1 <= fps <= 60:
        raise ValueError('Export requires even 128..1920 × 128..1080 dimensions, fps 1..60')
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
                page=browser.new_page(viewport={'width':width,'height':height*2})
                page.add_init_script('window.whiteboxExportData = '+json.dumps(payload,ensure_ascii=False)+';')
                page.goto((STATIC/'whitebox-export.html').as_uri())
                page.wait_for_function('window.whiteboxReady === true',timeout=60000)
                for group in groups:
                    gid=component(group['group_id']);duration=group['duration_s'];frames=math.ceil(duration*fps-1e-7)
                    output=base/'assets/whitebox'/ep/gid;output.mkdir(parents=True,exist_ok=True)
                    page.evaluate('(gid)=>window.whiteboxExport.load(gid)',gid)
                    # Stage both views before publishing; failed jobs retain prior valid exports.
                    with tempfile.TemporaryDirectory(prefix='.render-',dir=output) as staging:
                        staging=Path(staging)
                        command=[ffmpeg,'-hide_banner','-loglevel','error','-y','-f','image2pipe','-vcodec','mjpeg','-framerate',str(fps),'-i','pipe:0',
                                 '-filter_complex',f'[0:v]split=2[a][b];[a]crop={width}:{height}:0:0[top];[b]crop={width}:{height}:0:{height}[cam]']
                        for name,label in [('top','top'),('camera','cam')]:
                            command+=['-map',f'[{label}]','-c:v','libx264','-preset','fast','-crf','20','-pix_fmt','yuv420p','-t',str(duration),'-movflags','+faststart',str(staging/f'{name}.mp4')]
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
                                'width':width,'height':height,'source_sha256':fingerprint(episode,group),
                                'files':[f'assets/whitebox/{ep}/{gid}/top.mp4',f'assets/whitebox/{ep}/{gid}/camera.mp4']}
                        for name in ('top.mp4','camera.mp4'):os.replace(staging/name,output/name)
                        (staging/'manifest.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
                        os.replace(staging/'manifest.json',output/'manifest.json');results.append(record)
            finally:
                browser.close()
        return results
