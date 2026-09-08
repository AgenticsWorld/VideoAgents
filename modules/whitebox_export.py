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
    """Save missing/stale video pairs automatically after a modeling update."""
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
        files = [f'assets/whitebox/{ep}/{gid}/{view}.mp4' for view in ('top', 'camera')]
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
                                **fmt,'source_sha256':fingerprint(episode,group),'renderer_sha256':renderer_fingerprint(),
                                'files':[f'assets/whitebox/{ep}/{gid}/top.mp4',f'assets/whitebox/{ep}/{gid}/camera.mp4']}
                        for name in ('top.mp4','camera.mp4'):os.replace(staging/name,output/name)
                        (staging/'manifest.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
                        os.replace(staging/'manifest.json',output/'manifest.json');results.append(record)
            finally:
                browser.close()
        return results


# ---------------- 整集白模合辑(2026-09-08) ----------------
# 视频预览页「白模合辑」板块:把本集全部分镜组的 camera.mp4 按 shot_list 组序拼成一份
# 整集摄影机视角视频 assets/whitebox/<ep>/<ep>-camera.mp4,便于连续查看;
# 清单 episode-manifest.json 记录组序/各组源指纹,预览页据此判断合辑是否过期。
EPISODE_MANIFEST = 'episode-manifest.json'


def episode_reel_paths(ep):
    ep = component(ep)
    return {'video': f'assets/whitebox/{ep}/{ep}-camera.mp4',
            'manifest': f'assets/whitebox/{ep}/{EPISODE_MANIFEST}'}


def episode_group_order(base, ep):
    """本集分镜组顺序:shot_list generation_groups 为准;没有 shot_list 时按目录名排序。"""
    ep = component(ep)
    try:
        source = read(base/'directing'/ep/'shot_list.json', {}) or {}
    except (ValueError, OSError):
        source = {}
    groups = [g for g in source.get('generation_groups', []) if isinstance(g, dict) and g.get('group_id')]
    if groups:
        return [{'group_id': component(g['group_id']), 'shots': len(g.get('shots') or [])} for g in groups]
    folder = base/'assets/whitebox'/ep
    if not folder.is_dir():
        return []
    return [{'group_id': d.name, 'shots': 0} for d in sorted(folder.iterdir())
            if d.is_dir() and (d/'camera.mp4').is_file()]


def episode_reel_status(base, ep):
    """合辑现状(预览页/机检共用):文件是否存在、组就绪数、与当前各组 camera.mp4 指纹是否一致。"""
    ep = component(ep)
    paths = episode_reel_paths(ep)
    order = episode_group_order(base, ep)
    ready, missing, sources = [], [], {}
    for item in order:
        gid = item['group_id']
        video = base/'assets/whitebox'/ep/gid/'camera.mp4'
        if video.is_file() and video.stat().st_size > 0:
            ready.append(gid)
            try:
                record = read(base/'assets/whitebox'/ep/gid/'manifest.json', {}) or {}
            except (ValueError, OSError):
                record = {}
            sources[gid] = record.get('source_sha256') or f'mtime:{int(video.stat().st_mtime)}'
        else:
            missing.append(gid)
    video = base/paths['video']
    try:
        manifest = read(base/paths['manifest'], {}) or {}
    except (ValueError, OSError):
        manifest = {}
    exists = video.is_file() and video.stat().st_size > 0
    stale = bool(exists and (manifest.get('group_sources') != sources or manifest.get('group_order') != [g['group_id'] for g in order]))
    return {'ep': ep, 'path': paths['video'], 'manifest_path': paths['manifest'], 'exists': exists,
            'stale': stale, 'groups_total': len(order), 'groups_ready': ready, 'groups_missing': missing,
            'sources': sources, 'order': order, 'manifest': manifest if exists else {}}


def _probe_duration(path):
    ffprobe = shutil.which('ffprobe')
    if not ffprobe:
        return None
    try:
        out = subprocess.run([ffprobe, '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(path)],
                             capture_output=True, text=True, timeout=60)
        return float(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None
    except (ValueError, subprocess.SubprocessError):
        return None


def concat_episode(base, ep, *, allow_missing=False):
    """把本集各组 camera.mp4 按组序拼成 <ep>-camera.mp4;同规格流直接 copy 拼接,规格不一致时重编码。

    缺组默认报错(合辑必须是整集);allow_missing=True 时跳过缺组并在清单里记录。
    先在临时目录成片再 os.replace 发布,失败保留旧合辑。
    """
    ep = component(ep)
    status = episode_reel_status(base, ep)
    if not status['groups_total']:
        raise ValueError(f'{ep} 没有分镜组或白模视频,无法生成合辑')
    if status['groups_missing'] and not allow_missing:
        raise ValueError(f"{ep} 缺少分镜组白模视频: {', '.join(status['groups_missing'])};先用 render_whitebox.py 补出,或 --allow-missing 跳过")
    if not status['groups_ready']:
        raise ValueError(f'{ep} 没有任何分镜组白模视频可合并')
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('未找到 FFmpeg，请先安装并加入 PATH。')
    folder = base/'assets/whitebox'/ep
    folder.mkdir(parents=True, exist_ok=True)
    clips = [folder/gid/'camera.mp4' for gid in status['groups_ready']]
    records = {}
    for gid in status['groups_ready']:
        try:
            records[gid] = read(folder/gid/'manifest.json', {}) or {}
        except (ValueError, OSError):
            records[gid] = {}
    specs = {(r.get('width'), r.get('height'), r.get('fps')) for r in records.values()}
    uniform = len(specs) == 1 and None not in next(iter(specs))
    fmt = records[status['groups_ready'][0]] if uniform else render_format(read(base/'settings.json', {}), None, None)
    fps = int(fmt.get('fps') or 24)
    width, height = fmt.get('width'), fmt.get('height')
    expected = sum(float(r.get('duration_s') or 0) for r in records.values())
    paths = episode_reel_paths(ep)
    with _EXPORT_LOCK, tempfile.TemporaryDirectory(prefix='.reel-', dir=folder) as staging:
        staging = Path(staging)
        listing = staging/'concat.txt'
        listing.write_text(''.join(f"file '{c.resolve().as_posix()}'\n" for c in clips), encoding='utf-8')
        target = staging/f'{ep}-camera.mp4'
        base_cmd = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', str(listing)]
        if uniform:
            cmd = base_cmd + ['-c', 'copy', '-movflags', '+faststart', str(target)]
            mode = 'copy'
        else:
            # 组视频规格不一(项目画幅/帧率中途变过):统一缩放到当前项目规格后重编码
            cmd = base_cmd + ['-vf', f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps}',
                              '-c:v', 'libx264', '-preset', 'fast', '-crf', '20', '-pix_fmt', 'yuv420p', '-an', '-movflags', '+faststart', str(target)]
            mode = 'reencode'
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if proc.returncode != 0 or not target.is_file() or target.stat().st_size == 0:
            raise RuntimeError(f'ffmpeg 合并失败({mode}): {proc.stderr.strip()[-3000:]}')
        actual = _probe_duration(target)
        if actual is not None and expected and abs(actual-expected) > max(1.0, 0.02*expected):
            raise RuntimeError(f'合辑时长 {actual:.2f}s 与各组之和 {expected:.2f}s 不符,已放弃发布')
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        duration = actual if actual is not None else expected
        manifest = {'schema_version': 'whitebox_episode_export.v1', 'project': base.name, 'ep': ep,
                    'groups': len(status['groups_ready']), 'shots': sum(g['shots'] for g in status['order'] if g['group_id'] in records),
                    'duration_s': round(duration, 3), 'width': width, 'height': height, 'fps': fps, 'audio': False, 'mode': mode,
                    'reels': [{'path': paths['video'], 'duration_s': round(duration, 3), 'frames': round(duration*fps),
                               'bytes': target.stat().st_size, 'sha256': digest}],
                    'group_order': [g['group_id'] for g in status['order']],
                    'group_sources': status['sources'], 'missing_groups': status['groups_missing']}
        (staging/EPISODE_MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(target, base/paths['video'])
        os.replace(staging/EPISODE_MANIFEST, base/paths['manifest'])
    # 旧版整集俯视合辑不再维护,顺手清掉以免被误当参考
    stale = folder/f'{ep}-top.mp4'
    if stale.exists():
        stale.unlink()
    return manifest
