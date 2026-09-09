"""白模参考视频 → 组视频生成参考视频的接线(2026-09-07)。

项目「输出设置 → 人物精确空间位置」(output.spatial_blocking)开启时,白模调度导出的
`assets/whitebox/<ep>/<grp>/camera.mp4` 自动成为该分镜组视频生成的参考视频:
  - 写进组 prompt `assets/prompts/<ep>/<grp>.json` 的 `video_refs`(video-generation 按序传 --ref-video);
  - 在 video_prompt 的 `Shot 1:` 之前插入固定英文锚点段 `Whitebox reference: … Whitebox legend: …`
    (视频作用、颜色↔人物、眼睛/鼻尖=朝向、禁复现白模外观),
    并在 `Global constraints:` 并入禁白模外观句;
  - 机检 whitebox_ref_bound(code/sync_whitebox_refs.py 不带 --write)。
  - **白模人物参考图规约(2026-09-09)**:只有在本组白模摄影机视频里实际出现的人物/生物,其参考图
    (`assets/concepts/characters|creatures/<id>/…`)才进本组 refs。「出现」= 该 actor 在至少一镜里
    presence 非 absent/remote、关键帧 visible 非 false、未被该镜 `visible_actor_ids` 排除,且包围球落在
    该镜摄影机画幅内(画幅外 10% 容差,不算遮挡);见 appearing_cast()。sync_scene_cast 不再为其余人物
    补图,--write 把已挂的多余人物图移出 refs 并重排 `[Image N]`,`Whitebox legend:` 也只列出现者;
    正文仍引用被移除图时不动 refs、按违规上报由人工先改正文。
预算:按本组生效视频模型的参考视频数量/总时长上限判 camera.mp4(画面视角,定人物在画面里的位置)是否装得下;
2026-09-08 起白模只导出摄影机视角、不再有 top.mp4 俯视视频(原 output.whitebox_top_video 开关废止);
渠道不支持参考视频时不接、退回干净俯视图口径。
数据源:directing/<ep>/whitebox/episode.json(actors/extras 的颜色与 label,render_whitebox.py 编译落盘)。
"""
from __future__ import annotations

import copy
import json
import math
import os
import re
from pathlib import Path

from modules.whitebox import component, read, sample

COLOR_NAMES = {'#e63946': 'red', '#1d78d8': 'blue', '#2ea043': 'green', '#f59e0b': 'orange',
               '#8e44ad': 'purple', '#00acc1': 'cyan', '#e91e63': 'pink', '#795548': 'brown'}
BLOCK_KEY = 'Whitebox reference:'
LEGEND_KEY = 'Whitebox legend:'
GC_KEY = 'Global constraints:'
GC_SENTENCE = 'No whitebox look: no grey boxes, no placeholder figures, no color-coded people, no camera icon or sight line.'
_BLOCK_RE = re.compile(r'Whitebox reference:.*?(?:render the real characters, set and lighting from the reference images\.\s*|(?=Shot\s*1\s*:))', re.S)


CAST_REF_RE = re.compile(r'^assets/concepts/(characters|creatures)/([^/]+)/')
FRAME_MARGIN = 0.10      # 画幅外 10% 以内仍算入画:防采样/建模尺寸误差把贴边人物漏掉
SAMPLE_STEP_S = 1 / 12   # 入画判定采样步长
CAMERA_NEAR_M = 0.025    # 与 whitebox-renderer.js PerspectiveCamera near 一致
CAST_SOURCE = 'whitebox_cast.v1'


def color_name(hex_color):
    return COLOR_NAMES.get(str(hex_color or '').lower(), str(hex_color or 'unknown-color'))


# ---------------------------------------------------------------- appearing cast
def _basis(cam):
    """摄影机坐标系(right, up, forward);与 three.js lookAt(up=+Y)同约定,俯仰接近竖直时换 up=-Z。"""
    f = [b - a for a, b in zip(cam['position'], cam['target'])]
    n = math.sqrt(sum(x * x for x in f))
    if n < 1e-8:
        return None
    f = [x / n for x in f]
    up = [0, 1, 0] if abs(f[1]) < 0.999 else [0, 0, -1]
    r = [f[1] * up[2] - f[2] * up[1], f[2] * up[0] - f[0] * up[2], f[0] * up[1] - f[1] * up[0]]
    rn = math.sqrt(sum(x * x for x in r)) or 1.0
    r = [x / rn for x in r]
    u = [r[1] * f[2] - r[2] * f[1], r[2] * f[0] - r[0] * f[2], r[0] * f[1] - r[1] * f[0]]
    return r, u, f


def sphere_in_frame(center, radius, cam, aspect, margin=FRAME_MARGIN):
    """包围球(世界坐标)是否与该时刻摄影机视锥相交(不算遮挡)。cam: 采样后的摄影机关键帧(position/target/fov 竖向角度)。"""
    basis = _basis(cam)
    if basis is None:
        return False
    r_axis, u_axis, f_axis = basis
    d = [c - p for c, p in zip(center, cam['position'])]
    x = sum(a * b for a, b in zip(d, r_axis))
    y = sum(a * b for a, b in zip(d, u_axis))
    z = sum(a * b for a, b in zip(d, f_axis))
    if z + radius < CAMERA_NEAR_M:
        return False
    tv = math.tan(math.radians(float(cam.get('fov') or 50)) / 2) * (1 + margin)
    th = tv * float(aspect or 16 / 9)
    for value, tan_half in ((x, th), (y, tv)):
        norm = math.sqrt(1 + tan_half * tan_half)
        if (z * tan_half - value) / norm < -radius or (z * tan_half + value) / norm < -radius:
            return False
    return True


def _sample_times(start, duration, *keyframe_lists):
    times = {start, start + duration}
    steps = max(1, int(math.ceil(duration / SAMPLE_STEP_S)))
    times.update(start + duration * i / steps for i in range(steps + 1))
    for keys in keyframe_lists:
        times.update(k['t'] for k in keys if start - 1e-6 <= k['t'] <= start + duration + 1e-6)
    return sorted(times)


def appearing_cast(group: dict, render: dict | None = None) -> dict:
    """本组白模摄影机视频里实际出现的人物/生物(含群演):{'visible': [id…], 'hidden': {id: 原因}, 'source'}。
    出现 = 至少一镜满足:presence 非 absent/remote、该时刻关键帧 visible 非 false、未被该镜 visible_actor_ids 排除、
    包围球落在该镜摄影机画幅内(FRAME_MARGIN 容差;不计几何遮挡)。scene_cast 里没有 actor 的人物(缺席/远程)也记 hidden。"""
    render = render or {}
    aspect = None
    try:
        if render.get('width') and render.get('height'):
            aspect = float(render['width']) / float(render['height'])
        elif render.get('aspect_ratio'):
            a, b = (float(x) for x in str(render['aspect_ratio']).split(':'))
            aspect = a / b
    except (ValueError, ZeroDivisionError, TypeError):
        aspect = None
    visible, hidden = [], {}
    cameras = group.get('cameras') or []
    for actor in list(group.get('actors') or []) + list(group.get('extras') or []):
        aid = actor.get('id')
        if not aid:
            continue
        state = (actor.get('presence') or {}).get('state')
        if state in ('absent', 'remote'):
            hidden[aid] = f'scene_presence={state}(本组不在场)'
            continue
        keys = actor.get('keyframes') or []
        if keys and all(k.get('visible') is False for k in keys):
            hidden[aid] = '整组关键帧 visible:false(已离场/尚未入场)'
            continue
        size = actor.get('size_m') or [0.5, 1.7, 0.4]
        radius = 0.5 * math.sqrt(sum(float(v) * float(v) for v in size))
        half_h = float(size[1]) / 2 if len(size) > 1 else 0.85
        excluded_everywhere, seen_frame = bool(cameras), not cameras   # 无机位数据(旧编译)时保守视为出现
        for cam in cameras:
            allowed = cam.get('visible_actor_ids')
            if isinstance(allowed, list) and aid not in allowed:
                continue
            excluded_everywhere = False
            start = float(cam.get('start') or 0); duration = float(cam.get('duration_s') or 0)
            ckeys = cam.get('keyframes') or []
            if not ckeys or not keys:
                seen_frame = True   # 无几何数据时保守视为出现
                break
            for t in _sample_times(start, duration, keys):
                k = sample(keys, t)
                if k.get('visible') is False:
                    continue
                pos = k.get('position') or [0, 0, 0]
                center = [float(pos[0]), float(pos[1]) + half_h, float(pos[2])]
                if sphere_in_frame(center, radius, sample(ckeys, t - start), aspect):
                    seen_frame = True
                    break
            if seen_frame:
                break
        if seen_frame:
            visible.append(aid)
        elif excluded_everywhere:
            hidden[aid] = '各镜 visible_actor_ids 均未列入(镜头外在场人物)'
        else:
            hidden[aid] = '整组不在任一镜的摄影机画幅内'
    present = {a.get('id') for a in group.get('actors') or []}
    for cid in group.get('scene_cast') or []:
        if cid not in present and cid not in hidden:
            hidden[cid] = '不在本组白模人物列表(缺席/远程)'
    return {'visible': visible, 'hidden': hidden, 'source': CAST_SOURCE}


def cast_filter(base: Path, ep: str, gid: str):
    """项目开白模链且本组已编译进 directing/<ep>/whitebox/episode.json 时返回 appearing_cast 结果,否则 None(不限制)。"""
    settings = read(Path(base) / 'settings.json', {}) or {}
    if (settings.get('output') or {}).get('spatial_blocking', True) is False:
        return None
    ep, gid = component(ep), component(gid)
    episode = read(Path(base) / 'directing' / ep / 'whitebox' / 'episode.json', {}) or {}
    group = next((g for g in episode.get('groups', []) if g.get('group_id') == gid), None)
    if group is None:
        return None
    return appearing_cast(group, episode.get('render') or {})


def cast_ref_id(ref):
    m = CAST_REF_RE.match(ref) if isinstance(ref, str) else None
    return m.group(2) if m else None


def hidden_cast_refs(refs, cast) -> list:
    """refs 里属于未在本组白模出现的人物/生物的参考图(cast=appearing_cast 结果;None=不限制)。"""
    if not cast:
        return []
    allowed = set(cast.get('visible') or [])
    return [r for r in refs if cast_ref_id(r) and cast_ref_id(r) not in allowed]


def cast_hidden_reason(cast, cid):
    return (cast or {}).get('hidden', {}).get(cid) or '不在本组白模人物列表'


def strip_cast_refs(prompt: dict, drop: list):
    """从 prompt 移除 drop 里的人物图并重排 [Image N](含删除 sync_scene_cast 写的绑定句);正文仍引用被移除图时返回 (prompt, False)。"""
    from modules.continuity_refs import remap
    old = [r for r in (prompt.get('refs') or []) if isinstance(r, str)]
    new = [r for r in old if r not in drop]
    if new == old:
        return prompt, True
    text = prompt.get('video_prompt') or ''
    for r in drop:
        n = old.index(r) + 1
        text = re.sub(r'[^\s@]+@Image\s*%d:\s*%s，本场次人物的身份与服装参考。\s*' % (n, re.escape(cast_ref_id(r) or '')), '', text)
    try:
        text = remap(text, 'Image', old, new)
    except ValueError:
        return prompt, False
    out = copy.deepcopy(prompt)
    out['refs'] = new
    out['video_prompt'] = text
    if isinstance(out.get('scene_cast_refs'), list):
        for row in out['scene_cast_refs']:
            if isinstance(row, dict) and row.get('ref') in drop:
                row['ref'] = None
    return out, True


# ---------------------------------------------------------------- inputs
def whitebox_group(base: Path, ep: str, gid: str):
    """返回 (group_from_episode_json, manifest) ;任一缺失/视频文件不全返回 (group|None, None)。"""
    ep, gid = component(ep), component(gid)
    episode = read(base/'directing'/ep/'whitebox'/'episode.json', {}) or {}
    group = next((g for g in episode.get('groups', []) if g.get('group_id') == gid), None)
    manifest = read(base/'assets/whitebox'/ep/gid/'manifest.json', None)
    if not isinstance(manifest, dict):
        return group, None
    files = manifest.get('files') or []
    cam = f'assets/whitebox/{ep}/{gid}/camera.mp4'
    # 只要求 camera.mp4(2026-09-08 起唯一导出);旧版 manifest 里的 top.mp4 记录忽略
    if cam not in files or not ((base/cam).is_file() and (base/cam).stat().st_size > 0):
        return group, None
    return group, manifest


def _model_caps(model: str):
    m = (model or '').lower()
    if 'seedance-2-5' in m or 'seedance-2.5' in m or 'seedance-2_5' in m:
        return {'max_ref_videos': 10, 'max_total_s': 30}
    if 'seedance-2' in m or 'seedance2' in m:
        return {'max_ref_videos': 3, 'max_total_s': 15}
    if 'turbo' in m and 'h3' in m or 'kling' in m:
        return {'max_ref_videos': 0, 'max_total_s': 0}
    if 'minimax' in m and 'h3' in m:
        return {'max_ref_videos': 3, 'max_total_s': 15}
    return None


def video_budget(base: Path, ep: str, gid: str) -> dict:
    """本组生效视频模型的参考视频预算:{max_videos, max_total_s, model, provider, source}。
    组级覆盖 assets/group_settings/<ep>/<grp>.json 优先;全局模型按 genmedia 的提交配置解析(不可用时按项目
    「视频模型设置」shot_group.max_ref_videos / max_group_s 回落)。comfyui/runninghub 渠道不支持参考视频。"""
    settings = read(base/'settings.json', {}) or {}
    sg = settings.get('shot_group') or {}
    ov = read(base/'assets/group_settings'/component(ep)/f'{component(gid)}.json', {}) or {}
    model, provider, source = str(ov.get('video_model') or ''), str(ov.get('provider') or ''), 'group'
    try:
        # Use the same provider/model resolution as submission, without importing
        # the API service (pygit2/FastAPI are unnecessary for this media CLI).
        from modules.genmedia import get_config
        cfg = get_config('video')
        global_model, global_provider = str(cfg.get('model') or ''), str(cfg.get('provider') or '')
        if not model or global_provider == 'comfyui' or (provider and provider != global_provider):
            model, provider, source = global_model, global_provider, 'global'
        else:
            provider = global_provider
    except (RuntimeError, KeyError, ValueError, OSError):
        if not model:
            model, provider, source = '', '', 'project_settings'
    caps = _model_caps(model)
    if provider in ('comfyui', 'runninghub'):
        return {'max_videos': 0, 'max_total_s': 0, 'model': model, 'provider': provider, 'source': source,
                'reason': f'渠道 {provider} 不支持参考视频(--ref-video)'}
    if caps:
        max_videos = caps['max_ref_videos']
        if source != 'group' and 'max_ref_videos' in sg:
            max_videos = min(max_videos, int(sg['max_ref_videos'] or 0))
        return {'max_videos': max_videos, 'max_total_s': caps['max_total_s'], 'model': model,
                'provider': provider, 'source': source, 'reason': ''}
    return {'max_videos': int(sg.get('max_ref_videos', 3) or 0), 'max_total_s': float(sg.get('max_group_s', 15) or 15),
            'model': model, 'provider': provider, 'source': source, 'reason': ''}


def plan_refs(base: Path, ep: str, gid: str, continuation=None, prompt=None) -> dict:
    """决定本组是否挂白模摄影机视频:{camera, videos[], duration_s, budget, skipped_reason}。"""
    group, manifest = whitebox_group(base, ep, gid)
    ep, gid = component(ep), component(gid)
    cast = cast_filter(base, ep, gid)   # 本组白模实际出现的人物(None=项目未开白模链/本组未编译,不限制)
    if group is None or manifest is None:
        return {'camera': None, 'videos': [], 'group': group, 'budget': None, 'cast': cast,
                'skipped_reason': '白模视频未导出(先跑 code/render_whitebox.py)'}
    budget = video_budget(base, ep, gid)
    from modules.continuity_refs import plan as continuation_plan, probe, local, TAIL_VIDEO
    if continuation is None:
        try:
            continuation = continuation_plan(base, ep, gid, budget=budget)
        except ValueError:
            # Whitebox export precedes the final continuity plan; continuation sync
            # and generation validation will enforce it when the group is ready.
            continuation = {'mode': 'none'}
    prompt = prompt if prompt is not None else (read(base/f'assets/prompts/{ep}/{gid}.json', {}) or {})
    others = [v for v in prompt.get('video_refs', []) if '/whitebox/' not in v and not v.endswith(TAIL_VIDEO)]
    reserved_s = sum(probe(local(base, v)) for v in others)
    reserved_n = len(others)
    if continuation['mode'] == 'tail_video':
        reserved_s += continuation['duration_s']
        reserved_n += 1
    budget = dict(budget, max_videos=max(0, budget['max_videos']-reserved_n),
                  max_total_s=max(0, budget['max_total_s']-reserved_s))
    # 2026-09-08 起白模只导出摄影机视角 camera.mp4(不再有 top.mp4),预算只判这一路装不装得下
    dur = float(manifest.get('duration_s') or group.get('duration_s') or 0)
    cam = f'assets/whitebox/{ep}/{gid}/camera.mp4'
    videos, skipped = [], ''
    if budget['max_videos'] <= 0 or dur > budget['max_total_s'] + 1e-6:
        skipped = budget['reason'] or (f'参考视频预算不足(模型 {budget["model"] or "?"}:≤{budget["max_videos"]} 个/总时长 ≤{budget["max_total_s"]}s,组时长 {dur}s)')
    else:
        videos.append(cam)
    return {'camera': cam if cam in videos else None, 'videos': videos, 'cast': cast,
            'group': group, 'budget': budget, 'duration_s': dur, 'skipped_reason': skipped}


# ---------------------------------------------------------------- prompt text
def legend_rows(group: dict, cast: dict | None = None) -> list:
    """颜色↔人物图例;cast(appearing_cast 结果)给出时只列在摄影机视频里实际出现的人物/生物/群演。"""
    rows = []
    shown = set(cast['visible']) if cast else None
    riders = {a['id']: a for a in group.get('actors', []) if a.get('rider')}
    for a in group.get('actors', []):
        if a.get('rider') or (shown is not None and a['id'] not in shown):
            continue
        rider_mounts = [m for m in riders.values() if m.get('rider') == a['id']]
        row = f"{color_name(a.get('color'))} figure = {a.get('label') or a['id']} ({a['id']})"
        if a.get('kind') == 'creature':
            row = f"{color_name(a.get('color'))} creature = {a.get('label') or a['id']} ({a['id']})"
        for m in rider_mounts:
            row += f", riding the same-colored creature {m.get('label') or m['id']} ({m['id']})"
        rows.append(row)
    for x in group.get('extras', []) or []:
        if shown is not None and x.get('id') not in shown:
            continue
        rows.append(f"{color_name(x.get('color'))} figure = {x.get('label') or x.get('id')} ({x.get('id')}, background extra)")
    return rows


def build_block(plan: dict) -> str:
    videos = plan['videos']
    if not videos:
        return ''
    ci = videos.index(plan['camera']) + 1
    parts = [f"{BLOCK_KEY} [Video {ci}] is the camera-view whitebox previs of this exact group — grey placeholder geometry "
             "rendered from the real camera of every shot with the same cuts and timing; follow it for camera position, framing, "
             "each character's screen position, depth, facing and movement timing."]
    legend = '; '.join(legend_rows(plan['group'] or {}, plan.get('cast')))
    facing = 'the white eyes and nose tip show where a figure faces'
    cam = 'the camera itself is never drawn in the camera view'
    parts.append(f"{LEGEND_KEY} {legend}; {facing}; {cam}.")
    parts.append("Do not reproduce the whitebox look: no grey boxes, no placeholder figures, no color-coded people, "
                 "no camera icon or sight line — render the real characters, set and lighting from the reference images.")
    return ' '.join(parts)


def apply_prompt(prompt: dict, plan: dict) -> dict:
    """幂等回写:video_refs(白模视频在前,保留其他非白模参考视频)、正文锚点段、Global constraints、notes、whitebox_refs。"""
    out = copy.deepcopy(prompt)
    others = [v for v in (out.get('video_refs') or []) if isinstance(v, str) and '/whitebox/' not in v]
    vp = out.get('video_prompt') or ''
    vp = _BLOCK_RE.sub('', vp)
    from modules.continuity_refs import remap
    vp = remap(vp, 'Video', out.get('video_refs') or [], plan['videos'] + others)
    if plan['videos']:
        out['video_refs'] = plan['videos'] + others
        block = build_block(plan)
        # 固定段规范顺序:Scene presence → Whitebox reference → Shot plates → Shot 1;本段锚在 Shot plates 段之前(无则 Shot 1 前)
        m = re.search(r'Shot\s*1\s*:', vp)
        at = min([i for i in (vp.find('Shot plates:'), m.start() if m else -1) if i >= 0], default=-1)
        head = vp[:at] if at >= 0 else vp
        sep = '' if (not head or head[-1].isspace()) else ' '   # 保留段前原有换行/空格,重跑不改动正文
        vp = head + sep + block + ((' ' + vp[at:]) if at >= 0 else '')
        gi = vp.rfind(GC_KEY)
        if gi >= 0 and 'whitebox' not in vp[gi:].lower():
            vp = vp.rstrip()
            vp += ('' if vp.endswith(('.', '。', ';', ';')) else '.') + ' ' + GC_SENTENCE
        elif gi < 0:
            vp = vp.rstrip() + ' ' + GC_KEY + ' ' + GC_SENTENCE
    else:
        if others:
            out['video_refs'] = others
        else:
            out.pop('video_refs', None)
    out['video_prompt'] = vp
    # 白模人物参考图规约:未在本组白模出现的人物/生物图移出 refs(正文仍引用时不动,由 check_prompt 报违规)
    drop = hidden_cast_refs([r for r in (out.get('refs') or []) if isinstance(r, str)], plan.get('cast'))
    # 幂等:已移出的记录保留(只要该图仍不在 refs 里),本次新移出的并入
    dropped = [r for r in (((out.get('whitebox_refs') or {}).get('cast') or {}).get('dropped_refs') or [])
               if isinstance(r, str) and r not in (out.get('refs') or [])]
    if drop:
        out, ok = strip_cast_refs(out, drop)
        if ok:
            dropped += [r for r in drop if r not in dropped]
    out['whitebox_refs'] = {'camera': plan['camera'], 'skipped_reason': plan.get('skipped_reason') or '',
                            'model': (plan.get('budget') or {}).get('model', ''), 'source': 'sync_whitebox_refs.v1'}
    if plan.get('cast'):
        out['whitebox_refs']['cast'] = {'visible': list(plan['cast']['visible']), 'hidden': dict(plan['cast']['hidden']),
                                        'dropped_refs': dropped, 'source': plan['cast'].get('source', CAST_SOURCE)}
    note = (f"白模参考视频自动接线(code/sync_whitebox_refs.py):video_refs={plan['videos']}"
            + (f";未挂:{plan['skipped_reason']}" if plan.get('skipped_reason') else '')
            + (f";移出白模未出现人物图:{dropped}" if dropped else ''))
    # 原位更新自家备注(其他 sync 也各自追加备注,若每次移到末尾会与之互换位置、重跑不幂等)
    notes = list(out.get('notes') or [])
    slots = [i for i, n in enumerate(notes) if str(n).startswith('白模参考视频自动接线(')]
    if slots:
        notes[slots[0]] = note
        notes = [n for i, n in enumerate(notes) if i not in slots[1:]]
    else:
        notes.append(note)
    out['notes'] = notes
    return out


def check_prompt(prompt: dict, plan: dict, gid: str) -> tuple[list, list]:
    errs, warns = [], []
    vrefs = [v for v in (prompt.get('video_refs') or []) if isinstance(v, str)]
    vp = prompt.get('video_prompt') or ''
    cast = plan.get('cast')
    for r in hidden_cast_refs([x for x in (prompt.get('refs') or []) if isinstance(x, str)], cast):
        cid = cast_ref_id(r)
        errs.append(f"{gid}: refs 含未在本组白模出现的人物图 {r}({cid}:{cast_hidden_reason(cast, cid)});"
                    f"白模项目只挂白模里出现的人物参考图——删去正文对该图的引用后跑 code/sync_whitebox_refs.py --write 移出")
    if not plan['videos']:
        stale = [v for v in vrefs if '/whitebox/' in v]
        if stale:
            errs.append(f"{gid}: video_refs 含白模视频 {stale} 但本组当前不应挂({plan.get('skipped_reason')})")
        if BLOCK_KEY in vp:
            warns.append(f"{gid}: 正文含 {BLOCK_KEY} 段但本组未挂白模视频({plan.get('skipped_reason')}),建议 --write 清理")
        return errs, warns
    for i, v in enumerate(plan['videos']):
        if i >= len(vrefs) or vrefs[i] != v:
            errs.append(f"{gid}: video_refs[{i}] 应为 {v},实际 {vrefs[i] if i < len(vrefs) else '(缺)'}(白模视频须在前;跑 code/sync_whitebox_refs.py --write)")
    if BLOCK_KEY not in vp:
        errs.append(f"{gid}: video_prompt 缺 \"{BLOCK_KEY}\" 段(白模摄影机视频作用/颜色↔人物图例)")
        return errs, warns
    block = vp[vp.index(BLOCK_KEY):]
    ci = plan['videos'].index(plan['camera']) + 1
    if not re.search(r'\[Video\s*%d\][^.]*camera-view' % ci, block):
        errs.append(f"{gid}: {BLOCK_KEY} 段缺 [Video {ci}] 的 camera-view 说明句")
    if LEGEND_KEY not in block:
        errs.append(f"{gid}: 缺 \"{LEGEND_KEY}\" 颜色↔人物图例")
    else:
        legend = block[block.index(LEGEND_KEY):]
        shown = set(cast['visible']) if cast else None
        for a in (plan['group'] or {}).get('actors', []):
            if a.get('rider') or (shown is not None and a['id'] not in shown):
                continue
            if a['id'] not in legend or color_name(a.get('color')) not in legend:
                errs.append(f"{gid}: 图例缺 {color_name(a.get('color'))} = {a.get('label') or a['id']} ({a['id']})")
        if 'faces' not in legend and 'facing' not in legend:
            errs.append(f"{gid}: 图例缺眼睛/鼻尖=朝向说明")
    gi = vp.rfind(GC_KEY)
    if gi < 0 or 'whitebox' not in vp[gi:].lower():
        errs.append(f"{gid}: {GC_KEY} 缺禁白模外观句(no whitebox look …)")
    return errs, warns


# ---------------------------------------------------------------- sync
def sync_group(base: Path, ep: str, gid: str, write: bool = False) -> dict:
    ep, gid = component(ep), component(gid)
    plan = plan_refs(base, ep, gid)
    pp = base/'assets/prompts'/ep/f'{gid}.json'
    prompt = read(pp, None)
    result = {'group_id': gid, 'video_refs': plan['videos'], 'skipped_reason': plan.get('skipped_reason') or '',
              'cast': plan.get('cast'), 'dropped_refs': [],
              'has_prompt': isinstance(prompt, dict), 'updated': False, 'errors': [], 'warnings': []}
    if not isinstance(prompt, dict):
        return result
    if write:
        updated = apply_prompt(prompt, plan)
        if updated != prompt:
            backup = base/'directing'/ep/'whitebox'/'prompt_backups'/pp.name
            backup.parent.mkdir(parents=True, exist_ok=True)
            if not backup.exists():
                backup.write_bytes(pp.read_bytes())
            tmp = pp.with_suffix('.json.tmp')
            tmp.write_text(json.dumps(updated, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
            os.replace(tmp, pp)
            result['updated'] = True
        result['dropped_refs'] = list(((updated.get('whitebox_refs') or {}).get('cast') or {}).get('dropped_refs') or [])
        prompt = updated
    e, w = check_prompt(prompt, plan, gid)
    result['errors'], result['warnings'] = e, w
    return result


def sync_episode(base: Path, ep: str, groups=None, write: bool = False) -> dict:
    ep = component(ep)
    source = read(base/'directing'/ep/'shot_list.json', {}) or {}
    ids = [g['group_id'] for g in source.get('generation_groups', []) if g.get('group_id')]
    if groups:
        unknown = sorted(set(groups) - set(ids))
        ids = [g for g in ids if g in set(groups)]
    else:
        unknown = []
    rows = [sync_group(base, ep, gid, write) for gid in ids]
    errors = [f'{g}: unknown group' for g in unknown] + [e for r in rows for e in r['errors']]
    warnings = [w for r in rows for w in r['warnings']]
    return {'groups': rows, 'updated_prompts': [r['group_id'] for r in rows if r['updated']],
            'errors': errors, 'warnings': warnings}
