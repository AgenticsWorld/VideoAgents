"""Plan and prepare inter-group continuation, without calling a generation API.

Only an explicit boundary_type=continuous opts into video extension. Legacy
last_frame boundaries remain cuts. Prepared references are invalidated when the
predecessor changes; generated media is never modified by this module.
"""
from __future__ import annotations

import copy
import json
import os
import re
import subprocess
from pathlib import Path

from modules.whitebox import component, read

START, END = 'Continuation reference:', 'End continuation reference.'
BLOCK = re.compile(r'\s*Continuation reference:.*?End continuation reference\.', re.S)
TAIL_IMAGE = '.last_frame.png'
TAIL_VIDEO = '.continuation.mp4'


def fingerprint(path):
    s = Path(path).stat()
    return {'size': s.st_size, 'mtime_ns': s.st_mtime_ns}


def probe(path):
    p = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                        '-of', 'json', str(path)], capture_output=True, text=True, check=True, timeout=120)
    return float(json.loads(p.stdout)['format']['duration'])


def local(base, value):
    p = (base / value).resolve()
    if not p.is_relative_to(base.resolve()):
        raise ValueError('参考素材路径越出项目目录')
    return p


def tail_window(source, meta):
    """Inspect actual final frames, never include a detected cut or black interval."""
    duration = probe(source)
    start = max(0., duration - 3.)
    cuts = [float(v) for v in meta.get('boundaries_s', []) if 0 < float(v) < duration]
    # Scan the last three seconds in decode order (not keyframe stream-copy).
    result = subprocess.run(['ffmpeg', '-hide_banner', '-ss', str(start), '-i', str(source),
        '-an', '-vf', "blackdetect=d=0.02:pix_th=0.10,select='gt(scene,0.3)',showinfo",
        '-f', 'null', '-'], capture_output=True, text=True, check=True, timeout=120)
    cuts += [start + float(v) for v in re.findall(r'pts_time:([\d.]+)', result.stderr)]
    black_ends = [start + float(v) for v in re.findall(r'black_end:([\d.]+)', result.stderr)]
    if black_ends and duration - max(black_ends) < .1:
        raise ValueError('前组结尾为黑场，尾段和尾帧均不适合续接；先修复前组或标记为转场断点')
    cuts += black_ends
    start = max([start] + cuts)
    return duration, start, duration - start


def video_caps(model, provider):
    m = str(model).lower()
    if provider not in ('volcengine', 'byteplus', 'fal', 'minimax'):
        return None
    if m.endswith(('/image-to-video', '/text-to-video')) or 'turbo' in m and 'h3' in m:
        return None
    if 'seedance-2' in m:
        return (10, 30.) if ('seedance-2-5' in m or 'seedance-2.5' in m) else (3, 15.)
    if 'minimax' in m and 'h3' in m:
        return 3, 15.
    return None


def plan(base, ep, gid, prepare=False, budget=None):
    base = Path(base)
    ep, gid = component(ep), component(gid)
    result = {'mode': 'none', 'from_group': None, 'reason': '', 'ready': False}
    settings = read(base/'settings.json', {}) or {}
    if (settings.get('duration') or {}).get('long_take') is not True:
        result['reason'] = '长镜头关闭'
        return result
    sl = read(base/'directing'/ep/'shot_list.json', {}) or {}
    groups = sl.get('generation_groups', [])
    index = next((i for i, g in enumerate(groups) if g.get('group_id') == gid), None)
    if index is None:
        raise ValueError(f'{gid}: 分镜组不存在')
    if index == 0:
        result['reason'] = '组链起点'
        return result
    group, prev = groups[index], groups[index-1]
    cont = (read(base/'directing'/ep/'continuity.json', None)
            or read(base/'directing'/ep/'continuity_plan.json', {}) or {})
    tr = next((t for t in cont.get('group_transitions', []) if t.get('to_group') == gid), {})
    if tr.get('from_group') and tr['from_group'] != prev['group_id']:
        raise ValueError(f'{gid}: continuity 前组与实际组序不符')
    transition = (group.get('transition_in') or tr.get('transition') or {}).get('type', 'hard_cut')
    if (tr.get('anchor') == 'none' or tr.get('same_scene') is False
            or group.get('scene_id') != prev.get('scene_id')
            or transition not in ('hard_cut', 'match_cut', 'smash_cut')):
        result['reason'] = '跨场景、转场或显式断点'
        return result
    if not tr:
        raise ValueError(f'{gid}: 缺 group_transitions，先完成连戏规划')
    prev_id = component(prev['group_id'])
    result.update(mode='last_frame', from_group=prev_id,
                  image=f'assets/clips/{ep}/{prev_id}{TAIL_IMAGE}', reason='切镜连戏/旧版边界')
    if tr.get('boundary_type') == 'continuous' and transition == 'hard_cut':
        if budget is None:
            from modules.whitebox_refs import video_budget
            budget = video_budget(base, ep, gid)
        caps = video_caps(budget['model'], budget['provider'])
        if caps:
            result.update(mode='tail_video', video=f'assets/continuity/{ep}/{prev_id}{TAIL_VIDEO}',
                          duration_s=3., reason='连续动作，优先视频尾段')
            source = base/f'assets/clips/{ep}/{prev_id}.mp4'
            meta_path = source.with_suffix('.meta.json')
            if source.is_file():
                result['source_fingerprint'] = fingerprint(source)
                result['meta_fingerprint'] = fingerprint(meta_path) if meta_path.exists() else None
                analysis_path = base/f'assets/continuity/{ep}/{prev_id}.continuation.analysis.json'
                cached = read(analysis_path, {}) or {}
                if (cached.get('source_fingerprint') == result['source_fingerprint']
                        and cached.get('meta_fingerprint') == result['meta_fingerprint']):
                    result.update({k: cached[k] for k in ('duration_s', 'start_s') if k in cached})
                elif prepare:
                    duration, start, length = tail_window(source, read(meta_path, {}) or {})
                    result.update(duration_s=length, start_s=start)
                    analysis_path.parent.mkdir(parents=True, exist_ok=True)
                    analysis_path.write_text(json.dumps({k: result[k] for k in
                        ('source_fingerprint', 'meta_fingerprint', 'duration_s', 'start_s')}))
            elif prepare:
                raise ValueError(f'{gid}: 前组视频尚未生成: {source}')
            if result['duration_s'] < 2.:
                result.update(mode='last_frame', reason='最后连续镜头不足 2 秒，回退尾帧')
            else:
                # Reserve the mandatory camera whitebox before optional top video.
                from modules.whitebox_refs import whitebox_group
                _, manifest = whitebox_group(base, ep, gid)
                camera_s = float((manifest or {}).get('duration_s') or 0) if (
                    settings.get('output') or {}).get('spatial_blocking', True) else 0.
                pj = read(base/f'assets/prompts/{ep}/{gid}.json', {}) or {}
                others = [v for v in pj.get('video_refs', [])
                          if '/whitebox/' not in v and not v.endswith(TAIL_VIDEO)]
                other_s = sum(probe(local(base, v)) for v in others)
                slots = min(caps[0], budget['max_videos'])
                seconds = min(caps[1], budget['max_total_s'])
                available_s = seconds - other_s - camera_s
                if available_s >= 2. and available_s < result['duration_s']:
                    if 'start_s' in result:
                        result['start_s'] += result['duration_s'] - available_s
                    result['duration_s'] = available_s
                if (len(others) + bool(camera_s) + 1 > slots
                        or other_s + camera_s + result['duration_s'] > seconds + 1e-6):
                    result.update(mode='last_frame', reason='参考视频预算不足，保留摄影机白模并回退尾帧')
        else:
            result['reason'] = '当前模型/渠道不支持视频参考，回退尾帧'
    source = base/f'assets/clips/{ep}/{prev_id}.mp4'
    if source.is_file():
        result['source_fingerprint'] = fingerprint(source)
    if prepare:
        source = base/f'assets/clips/{ep}/{prev_id}.mp4'
        if not source.is_file():
            raise ValueError(f'{gid}: 等待前组视频 {source}')
        result['source_fingerprint'] = fingerprint(source)
        if result['mode'] == 'tail_video':
            dest = local(base, result['video'])
            dest.parent.mkdir(parents=True, exist_ok=True)
            cache = dest.with_suffix('.json')
            old = read(cache, {}) or {}
            if not (dest.is_file() and old == result):
                tmp = dest.with_name(dest.stem + '.tmp.mp4')
                try:
                    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', str(result['start_s']),
                        '-i', str(source), '-t', str(result['duration_s']), '-map', '0:v:0',
                        '-an', '-c:v', 'libx264', '-crf', '18', '-pix_fmt', 'yuv420p',
                        '-movflags', '+faststart', str(tmp)], check=True, capture_output=True, timeout=120)
                    measured = probe(tmp)
                    if not 2. <= measured <= 3.05:
                        raise ValueError(f'尾段实际时长不合规: {measured}')
                    os.replace(tmp, dest)
                    cache.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
                finally:
                    tmp.unlink(missing_ok=True)
        else:
            dest = local(base, result['image'])
            tmp = dest.with_name(dest.stem + '.tmp.png')
            try:
                subprocess.run(['ffmpeg', '-v', 'error', '-y', '-sseof', '-0.2', '-i', str(source),
                    '-update', '1', str(tmp)], check=True, capture_output=True, timeout=120)
                os.replace(tmp, dest)
            finally:
                tmp.unlink(missing_ok=True)
        result['ready'] = True
    return result


def remap(text, kind, old, new):
    rx = re.compile(r'\['+kind+r'\s*(\d+)\]|@'+kind+r'\s*(\d+)(?!\d)')
    def replace(m):
        n = int(m.group(1) or m.group(2))
        if n < 1 or n > len(old) or old[n-1] not in new:
            raise ValueError(f'正文仍引用已移除/越界素材 {m.group(0)}，请先调整 prompt')
        index = new.index(old[n-1]) + 1
        return f'[{kind} {index}]' if m.group(1) else f'@{kind} {index}'
    return rx.sub(replace, text)


def apply_prompt(prompt, continuation):
    out = copy.deepcopy(prompt)
    text = BLOCK.sub('', out.get('video_prompt') or '').strip()
    old_i, old_v = out.get('refs') or [], out.get('video_refs') or []
    images = [v for v in old_i if not v.endswith(TAIL_IMAGE)]
    videos = [v for v in old_v if not v.endswith(TAIL_VIDEO)]
    # Remove only the legacy opening declaration; never discard a full Shot block.
    for i, value in enumerate(old_i, 1):
        if value.endswith(TAIL_IMAGE):
            text = re.sub(r'(?:opening continues from|same location and lighting as)\s*\[Image\s*'
                          +str(i)+r'\]\s*[,.;:]?', '', text, flags=re.I)
    c = continuation
    if c['mode'] == 'tail_video':
        images_new, videos_new = images, videos + [c['video']]
    else:
        images_new = images + ([c['image']] if c['mode'] == 'last_frame' else [])
        videos_new = videos
    text = remap(remap(text, 'Image', old_i, images_new), 'Video', old_v, videos_new)
    text = text.strip()
    if c['mode'] == 'tail_video':
        if re.search(r'cut to|reverse angle|切镜|反打', text.split('Shot 2:')[0], re.I):
            raise ValueError('连续动作边界的开场仍有切镜指令，请调整首镜 prompt/分镜')
        n = videos_new.index(c['video']) + 1
        clause = (f'向后延长 [Video {n}]。Extend [Video {n}] forward from its final frame. '
                  'Generate only the new continuation, never replay the supplied clip. '
                  'Keep pose, props, lighting, camera framing, motion direction and speed continuous at the join; '
                  'no cut or restart at the opening. Character images define identity and detail. '
                  'Whitebox references guide subsequent staging, never override the continuation boundary. '
                  'The continuation clip is visual only; follow this group’s dialogue and sound instructions.')
    elif c['mode'] == 'last_frame':
        clause = f'opening continues from [Image {len(images_new)}]. Preserve identity, lighting and prop state.'
        if c.get('reason') == '切镜连戏/旧版边界':
            clause = f'same location and lighting as [Image {len(images_new)}]. Follow the planned opening framing.'
    else:
        clause = ''
    out.update(refs=images_new, video_refs=videos_new,
               video_prompt=(f'{START} {clause} {END}\n{text}' if clause else text),
               continuity_ref=c)
    return out


def sync_group(base, ep, gid, write=False, prepare=False):
    path = Path(base)/f'assets/prompts/{component(ep)}/{component(gid)}.json'
    prompt = read(path, None)
    if not isinstance(prompt, dict):
        raise ValueError(f'{gid}: 缺组 prompt')
    c = plan(base, ep, gid, prepare=prepare)
    saved = prompt.get('continuity_ref') or {}
    if not prepare and saved.get('ready') and {k: v for k, v in saved.items() if k != 'ready'} == {k: v for k, v in c.items() if k != 'ready'}:
        target = c.get('video') if c['mode'] == 'tail_video' else c.get('image')
        c['ready'] = bool(target and local(Path(base), target).is_file())
    updated = apply_prompt(prompt, c)
    # Re-budget and renumber whitebox refs after continuation reservation.
    if ((read(Path(base)/'settings.json', {}) or {}).get('output') or {}).get('spatial_blocking', True):
        from modules.whitebox_refs import apply_prompt as apply_whitebox, plan_refs
        updated = apply_whitebox(updated, plan_refs(Path(base), ep, gid, continuation=c, prompt=updated))
    changed = updated != prompt
    if write or prepare:
        if changed:
            backup = Path(base)/f'directing/{ep}/continuity/prompt_backups/{gid}.json'
            backup.parent.mkdir(parents=True, exist_ok=True)
            if not backup.exists():
                backup.write_bytes(path.read_bytes())
            tmp = path.with_suffix('.json.tmp')
            tmp.write_text(json.dumps(updated, ensure_ascii=False, indent=2)+'\n')
            os.replace(tmp, path)
    elif changed:
        raise ValueError(f'{gid}: 续接配置需同步，运行 sync_continuity_refs.py --write / --prepare')
    return {'group_id': gid, **c, 'updated': changed}


def validate_request(output, prompt, refs, videos, cfg, first='', last=''):
    """Fail before billing if a managed group has stale or omitted continuation."""
    path = Path(output).resolve()
    if path.parent.parent.name != 'clips' or path.parent.parent.parent.name != 'assets':
        return
    base, ep, gid = path.parents[3], path.parent.name, path.stem
    if '.' in gid:  # explicit subclip/fallback outputs are not whole groups
        return
    if not (base/'settings.json').is_file():
        return
    settings = read(base/'settings.json', {}) or {}
    pj = read(base/f'assets/prompts/{ep}/{gid}.json', {}) or {}
    if (settings.get('duration') or {}).get('long_take') is not True and not pj.get('continuity_ref'):
        return
    c = plan(base, ep, gid)
    saved = pj.get('continuity_ref') or {}
    if c['mode'] == 'tail_video' and c.get('meta_fingerprint') != saved.get('meta_fingerprint'):
        raise ValueError('前组切点已改变；重新运行 sync_continuity_refs.py --prepare')
    if c['mode'] != saved.get('mode') or c['from_group'] != saved.get('from_group'):
        raise ValueError('续接计划未同步；先运行 sync_continuity_refs.py --prepare 并重新读取组 prompt')
    if c['mode'] != 'none':
        source = base/f'assets/clips/{ep}/{c["from_group"]}.mp4'
        if not saved.get('ready') or not source.exists() or saved.get('source_fingerprint') != fingerprint(source):
            raise ValueError('续接素材未准备或前组已重生成；运行 sync_continuity_refs.py --prepare')
        if first or last:
            raise ValueError('组级自动续接与首尾帧兜底互斥；拆段兜底请使用独立子片段输出路径')
        if c['mode'] == 'tail_video' and not video_caps(cfg['model'], cfg['provider']):
            raise ValueError('当前实际模型不支持视频续接，请重新准备续接素材')
    def paths(values):
        return [(local(base, v) if str(v).startswith(('assets/', 'directing/')) else Path(v).resolve())
                for v in values or []]
    if any(not p.is_file() for p in paths(refs) + paths(videos)):
        raise ValueError('参考素材文件缺失；重新准备后再生成')
    if (prompt != pj.get('video_prompt') or paths(refs) != paths(pj.get('refs'))
            or paths(videos) != paths(pj.get('video_refs'))):
        raise ValueError('生成请求与已同步组 prompt 不一致；重新读取 JSON 并按顺序提交参考素材')
