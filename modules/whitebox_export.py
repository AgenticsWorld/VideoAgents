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
from modules.whitebox_subtitles import cues_fingerprint, episode_dialogue_placements, episode_subtitle_cues, write_subtitle_track

STATIC = Path(__file__).resolve().parents[1] / 'apps/web/static'
_EXPORT_LOCK = threading.Lock()


def fingerprint(episode, group):
    scene = episode['scenes'][group['scene_id']]
    # 待决项/裁决状态不影响画面,不进视频指纹(否则用户每答一题所有组视频都会显示过期)
    group = {k: v for k, v in group.items() if k != 'issues'}
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


# ---------------- 整集白模样片(2026-09-08,原名白模合辑;2026-09-11 改名并烧入对白/旁白字幕) ----------------
# 分镜预览页「白模样片」板块(成片发布页 2026-09-13 起不再展示):把本集全部分镜组的 camera.mp4 按 shot_list 组序拼成一份
# 整集摄影机视角视频 assets/whitebox/<ep>/<ep>-camera.mp4,便于连续查看;
# 清单 episode-manifest.json 记录组序/各组源指纹/字幕指纹,预览页据此判断样片是否过期。
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
    # 字幕(2026-09-11):对白/旁白字幕烧进样片,源文本或镜段时间变了同样算过期;旧清单没有 subtitles 段而现在有字幕可烧,也提示重出
    durations = {}
    for gid in ready:
        try:
            durations[gid] = float((read(base/'assets/whitebox'/ep/gid/'manifest.json', {}) or {}).get('duration_s') or 0)
        except (ValueError, OSError, TypeError):
            durations[gid] = 0.0
    # 对白语音库(2026-09-13,输出设置「生成对白语音」):库里可用的逐句音频按镜起点排到样片时间轴,作对白轨;
    # 字幕按实际音频起止显示;库指纹进清单,任一句音频换了样片判过期(stale_reason=audio)。这里只读库不合成,合成在 concat_episode
    from modules import dialogue_tts as dt
    audio_on = dt.enabled(base)
    lib = dt.load_manifest(base, ep) if audio_on else None
    try:
        placements, overflow = episode_dialogue_placements(base, ep, ready, durations, dt.line_audio(base, ep, lib, paced=True) if lib else None)
    except Exception:  # noqa: BLE001
        placements, overflow = [], []
    audio_sha = dt.library_fingerprint(lib) if lib else ''
    try:
        cues = episode_subtitle_cues(base, ep, ready, durations, placements)
    except Exception:  # noqa: BLE001  字幕源坏了不拦合辑状态
        cues = []
    subtitles_sha = cues_fingerprint(cues) if cues else ''
    stale_reason = ''
    if exists:
        rec_audio = manifest.get('audio') if isinstance(manifest.get('audio'), dict) else {}
        if manifest.get('group_sources') != sources or manifest.get('group_order') != [g['group_id'] for g in order]:
            stale_reason = 'groups'
        elif (manifest.get('subtitles') or {}).get('sha256', '') != subtitles_sha:
            stale_reason = 'subtitles'
        # #75:无对白轨的无声样片(发布时无可用逐句音频、如全部 unbound)当下也无可排音频 → 声轨不会变,不判过期;
        # 只有样片带过对白轨(lines>0)或现在有可排音频时,才按库指纹比对(存量无声清单记 audio=False 同样自愈)
        elif audio_on and (placements or rec_audio.get('lines')) and rec_audio.get('sha256', '') != audio_sha:
            stale_reason = 'audio'
    return {'ep': ep, 'path': paths['video'], 'manifest_path': paths['manifest'], 'exists': exists,
            'stale': bool(stale_reason), 'stale_reason': stale_reason, 'groups_total': len(order), 'groups_ready': ready,
            'groups_missing': missing, 'sources': sources, 'order': order, 'manifest': manifest if exists else {},
            'durations': durations, 'cues': cues, 'subtitles_sha256': subtitles_sha,
            'dialogue_audio': {'enabled': audio_on, 'lines': len(placements), 'sha256': audio_sha, 'overflow': overflow,
                               'placements': placements}}


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


def concat_episode(base, ep, *, allow_missing=False, subtitles=True):
    """把本集各组 camera.mp4 按组序拼成 <ep>-camera.mp4(白模样片);同规格且无字幕时流 copy 拼接,
    有对白/旁白字幕时烧入字幕带重编码(mode=burn),规格不一致时统一缩放后重编码。

    缺组默认报错(样片必须是整集);allow_missing=True 时跳过缺组并在清单里记录。
    先在临时目录成片再 os.replace 发布,失败保留旧样片。
    """
    ep = component(ep)
    # 对白语音库开着时先惰性同步(只补缺/过期句),样片才挂到最新台词的语音
    from modules import dialogue_tts as dt
    if dt.enabled(base):
        dt.ensure(base, ep)
    status = episode_reel_status(base, ep)
    if not status['groups_total']:
        raise ValueError(f'{ep} 没有分镜组或白模视频,无法生成样片')
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
    cues = status['cues'] if subtitles else []
    with _EXPORT_LOCK, tempfile.TemporaryDirectory(prefix='.reel-', dir=folder) as staging:
        staging = Path(staging)
        listing = staging/'concat.txt'
        listing.write_text(''.join(f"file '{c.resolve().as_posix()}'\n" for c in clips), encoding='utf-8')
        target = staging/f'{ep}-camera.mp4'
        base_cmd = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', str(listing)]
        # 对白轨(2026-09-13):对白语音库逐句音频按镜起点混成一条 wav,作最后一路输入;没有库时样片仍无声(-an)
        dialogue = status.get('dialogue_audio') or {}
        placements = dialogue.get('placements') or []
        audio_wav = None
        if placements:
            from modules.dialogue_track import build_track
            audio_wav = build_track(placements, expected, staging/'dialogue.wav')
        audio_args = ['-c:a', 'aac', '-b:a', '160k'] if audio_wav else ['-an']
        encode = ['-c:v', 'libx264', '-preset', 'fast', '-crf', '20', '-pix_fmt', 'yuv420p'] + audio_args + ['-movflags', '+faststart']
        normalize = f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps}'
        track = None
        if cues:
            # 字幕带:PIL 画透明 PNG 按时段排片(本机 ffmpeg 无 subtitles/drawtext 滤镜),作第二路输入 overlay 到样片上
            track = write_subtitle_track(cues, int(width), int(height), expected, staging/'subs')
            chain = (f'[0:v]{normalize}[base];' if not uniform else '[0:v]null[base];') + '[base][1:v]overlay=0:0:format=auto,format=yuv420p[v]'
            cmd = base_cmd + ['-f', 'concat', '-safe', '0', '-i', str(track['list'])]
            if audio_wav:
                cmd += ['-i', str(audio_wav)]
            cmd += ['-filter_complex', chain, '-map', '[v]'] + (['-map', '2:a'] if audio_wav else []) + encode + [str(target)]
            mode = 'burn'
        elif uniform:
            cmd = base_cmd + (['-i', str(audio_wav), '-map', '0:v', '-map', '1:a', '-c:v', 'copy'] + audio_args if audio_wav
                              else ['-c', 'copy']) + ['-movflags', '+faststart', str(target)]
            mode = 'copy'
        else:
            # 组视频规格不一(项目画幅/帧率中途变过):统一缩放到当前项目规格后重编码
            cmd = base_cmd + (['-i', str(audio_wav), '-map', '0:v', '-map', '1:a'] if audio_wav else []) + ['-vf', normalize] + encode + [str(target)]
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
                    'duration_s': round(duration, 3), 'width': width, 'height': height, 'fps': fps, 'mode': mode,
                    # 生成对白语音开着时无论有没有出声轨都记下同步时的库指纹(lines=0 即无声,预览页仍显示「无声」)
                    'audio': ({'kind': 'dialogue_tts', 'lines': len(placements) if audio_wav else 0, 'sha256': dialogue.get('sha256', ''),
                               'overflow': dialogue.get('overflow') or []} if audio_wav or dialogue.get('enabled') else False),
                    'subtitles': {'cues': len(cues), 'dialogue': sum(1 for c in cues if c['kind'] == 'dialogue'),
                                  'narration': sum(1 for c in cues if c['kind'] == 'narration'),
                                  'sha256': cues_fingerprint(cues) if cues else '',
                                  'frames': track['frames'] if track else 0},
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
