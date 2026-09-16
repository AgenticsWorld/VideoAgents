"""白模参考视频 → 组视频生成参考视频的接线(2026-09-07)。

项目「输出设置 → 人物精确空间位置」(output.spatial_blocking)开启时,白模调度导出的
`assets/whitebox/<ep>/<grp>/camera.mp4` 自动成为该分镜组视频生成的参考视频:
  - 写进组 prompt `assets/prompts/<ep>/<grp>.json` 的 `video_refs`(video-generation 按序传 --ref-video);
  - 在 video_prompt 的 `Shot 1:` 之前插入固定英文锚点段 `Whitebox reference: … Whitebox legend: …`
    (视频作用、颜色↔人物、眼睛/鼻尖=朝向、禁复现白模外观),
    **2026-09-09 起段内再写 `Whitebox facing:` 逐镜开场相对镜头朝向句**(由计划里每镜机位与人物 yaw(+torso_yaw+head_yaw)推导:
    front / three-quarter front / profile / three-quarter back / back + 脸朝画左/画右;只写开场,镜内转头/转身/运镜的变化由 Shot 段正文写;见 facing_rows()),
    背影镜而正文无背影字样时机检 WARN——白模里背影=没有眼鼻标记,模型读不出,必须靠文字;
    并在 `Global constraints:` 并入禁白模外观句;
  - 机检 whitebox_ref_bound(code/sync_whitebox_refs.py 不带 --write)。
  - **白模人物参考图规约(2026-09-09)**:只有在本组白模摄影机视频里实际出现的人物/生物,其参考图
    (`assets/concepts/characters|creatures/<id>/…`)才进本组 refs。「出现」= 该 actor 在至少一镜里
    presence 非 absent/remote、关键帧 visible 非 false、未被该镜 `visible_actor_ids` 排除,且**包围盒**落在
    该镜摄影机画幅内(画幅外 10% 容差,不算遮挡);见 appearing_cast()。
    **2026-09-14 起入画判定改用按姿态的轴对齐包围盒**(站姿=size_m 全高、坐姿=0.8 身高+腿前伸、卧姿=0.35 身高×身长),
    不再用包围球——1.7 m 人物的包围球半径 0.9 m,横向外扩近 4 倍身宽,把明明坐在画幅外的人(liaozhai3 ep01 grp014 范生)也判成入画。
    sync_scene_cast 不再为其余人物补图,--write 把已挂的多余人物图移出 refs 并重排 `[Image N]`,`Whitebox legend:` 也只列出现者;
    正文仍引用被移除图时不动 refs、按违规上报由人工先改正文。**未出现者也不得写进正文**(2026-09-14):
    Shot 段 staging、【人物】/【主体设定】、Global constraints 里都不得提及其 id 或白模 label——模型只按文字生成,
    写了「画幅外坐着」它也会把人画进来;机检 whitebox_hidden_mention 在宿主固定段之外发现其 id/label 即报违规。
预算:按本组生效视频模型的参考视频数量/总时长上限判 camera.mp4(画面视角,定人物在画面里的位置)是否装得下;
2026-09-08 起白模只导出摄影机视角、不再有 top.mp4 俯视视频(原 output.whitebox_top_video 开关废止);
渠道不支持参考视频时不接、退回干净俯视图口径(comfyui/runninghub 仅 MiniMax-H3 Ref2VA 工作流可接)。
数据源:directing/<ep>/whitebox/episode.json(actors/extras 的颜色与 label,render_whitebox.py 编译落盘)。
"""
from __future__ import annotations

import copy
import json
import math
import os
import re
from pathlib import Path

from modules.prompt_layout import paragraphize
from modules.whitebox import component, read, sample

# 与 modules/whitebox.PALETTE 同序(2026-09-11 扩到 16 色:整集固定身份色,人物多于 8 人时启用后 8 色)
COLOR_NAMES = {'#e63946': 'red', '#1d78d8': 'blue', '#2ea043': 'green', '#f59e0b': 'orange',
               '#8e44ad': 'purple', '#00acc1': 'cyan', '#e91e63': 'pink', '#795548': 'brown',
               '#ffd60a': 'yellow', '#0d9488': 'teal', '#1e3a8a': 'navy', '#84cc16': 'lime',
               '#b5179e': 'magenta', '#6b8e23': 'olive', '#800000': 'maroon', '#ff7f50': 'coral'}
BLOCK_KEY = 'Whitebox reference:'
LEGEND_KEY = 'Whitebox legend:'
FACING_KEY = 'Whitebox facing:'
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


def actor_bounds(actor: dict, key: dict) -> tuple:
    """人物在该关键帧的轴对齐包围盒(世界坐标):(center, half_extents)。按姿态取占位——
    stand: size_m 全高、脚印 max(w,d);sit: 0.8 身高(头顶≈0.725h+头半径,与 whitebox-renderer.js 同比例)、脚印加腿前伸 0.2h;
    lie / prone: 0.35 身高、脚印 0.5 身长;kneel: 0.825 身高(小腿平贴地面,头顶 0.725h+头半径)、脚印加小腿后伸 0.175h;
    crouch: 0.73 身高(髋在 0.175h、上身前俯 0.6 rad)、脚印加前俯 0.31h(2026-09-14 六态)。脚印按 max(w,d) 取方形,与 yaw 无关(略保守)。"""
    size = actor.get('size_m') or [0.5, 1.7, 0.4]
    w = float(size[0]); h = float(size[1]) if len(size) > 1 else 1.7; d = float(size[2]) if len(size) > 2 else w
    half_w = 0.5 * max(w, d)
    pose = key.get('pose') or 'stand'
    if pose == 'sit':
        height, half_w = 0.8 * h, max(half_w, 0.2 * h)
    elif pose in ('lie', 'prone'):
        height, half_w = 0.35 * h, max(half_w, 0.5 * h)
    elif pose == 'kneel':
        height, half_w = 0.825 * h, max(half_w, 0.175 * h)
    elif pose == 'crouch':
        height, half_w = 0.73 * h, max(half_w, 0.31 * h)
    else:
        height = h
    pos = key.get('position') or [0, 0, 0]
    center = [float(pos[0]), float(pos[1]) + height / 2, float(pos[2])]
    return center, [half_w, height / 2, half_w]


def box_in_frame(center, half, cam, aspect, margin=FRAME_MARGIN):
    """轴对齐包围盒(世界坐标)是否与该时刻摄影机视锥相交(不算遮挡):8 个角点全部落在视锥某一个面之外 → 不相交,否则算相交。
    cam: 采样后的摄影机关键帧(position/target/fov 竖向角度)。"""
    basis = _basis(cam)
    if basis is None:
        return False
    tv = math.tan(math.radians(float(cam.get('fov') or 50)) / 2) * (1 + margin)
    th = tv * float(aspect or 16 / 9)
    corners = []
    for sx in (-1, 1):
        for sy in (-1, 1):
            for sz in (-1, 1):
                p = [center[0] + sx * half[0], center[1] + sy * half[1], center[2] + sz * half[2]]
                d = [c - q for c, q in zip(p, cam['position'])]
                corners.append([sum(a * b for a, b in zip(d, axis)) for axis in basis])
    planes = (lambda x, y, z: z - CAMERA_NEAR_M,
              lambda x, y, z: z * th - x, lambda x, y, z: z * th + x,
              lambda x, y, z: z * tv - y, lambda x, y, z: z * tv + y)
    return not any(all(plane(*c) < 0 for c in corners) for plane in planes)


def in_frame(actor: dict, key: dict, cam, aspect, margin=FRAME_MARGIN) -> bool:
    """该人物在该关键帧是否入画(包围盒 × 视锥,不计遮挡)。"""
    center, half = actor_bounds(actor, key)
    return box_in_frame(center, half, cam, aspect, margin)


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
    按姿态的包围盒落在该镜摄影机画幅内(FRAME_MARGIN 容差;不计几何遮挡;见 actor_bounds/box_in_frame)。
    scene_cast 里没有 actor 的人物(缺席/远程)也记 hidden。"""
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
                if in_frame(actor, k, sample(ckeys, t - start), aspect):
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
    if (settings.get('output') or {}).get('spatial_blocking') is not True:
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


MENTION_MIN_LABEL = 2   # 单字 label(如「婢」)太易误报,只按 id 查
# 句内出现这些词=「明令不出现」的否定句(SOUL §7A 既有做法:前组人物本组不该在时明令 must not appear),不算把人写进画面
ABSENCE_WORDS = ('不出现', '不出场', '不在场', '不入画', '不入镜', '不进画', '未入场', '未入画', '未出场', '已离场', '已退场', '离场后',
                 '不得出现', '不得生成', '不生成', '不在画面', '不在本组', '缺席', '不再出现',
                 'must not appear', 'does not appear', 'do not appear', 'not appear', 'never appears', 'not present',
                 'absent', 'off-screen', 'offscreen', 'no longer in')
_SENT_SPLIT_RE = re.compile(r'(?<=[。．.!?！？;；\n])')


DIRECTOR_NOTE_RE = re.compile(r"Director's note(?: \(user instruction, must follow\))?:.*?(?=\n\n|Global constraints:|$)", re.S)


def verbatim_fragments(base: Path, ep: str, gid: str) -> list:
    """正文里须逐字保留、且允许提到未出场人物的片段:shot_list 组 blocking_map 各角色/生物的 route_en(常含用户修正原文,
    如「S04-19/20 少妇逐步变为 CRE-002」);机检 whitebox_hidden_mention 扫描前先剔除。"""
    sl = read(Path(base) / 'directing' / component(ep) / 'shot_list.json', {}) or {}
    g = next((x for x in sl.get('generation_groups', []) if x.get('group_id') == component(gid)), None) or {}
    out = []
    for rows in (g.get('blocking_map') or {}).values():
        if isinstance(rows, list):
            out += [str(r['route_en']).strip() for r in rows if isinstance(r, dict) and r.get('route_en')]
    return [x for x in out if x]


def hidden_cast_mentions(text: str, group: dict | None, cast, verbatim=()) -> list:
    """正文把未在本组白模出现的人物写进了画面:[(id, 命中词)…]。命中词 = actor id,或白模 label(≥2 字);同一人物只报一次。
    按句判断:含 ABSENCE_WORDS 的否定句(「X 已离场,不出现在画面中」)不算;别的人物 label 包含本 label 时不按子串误报。
    扫描前剔除:宿主固定段 Scene presence / Whitebox reference(其中的不在场声明与图例由宿主生成)、用户导演注释
    `Director's note …`(原句必须保留)、verbatim 里的逐字片段(blocking_map route_en 等用户原文)。"""
    if not cast or not text:
        return []
    from modules.scene_cast import CAST_BLOCK_RE
    body = DIRECTOR_NOTE_RE.sub('', _BLOCK_RE.sub('', CAST_BLOCK_RE.sub('', text)))
    for frag in verbatim or ():
        if frag:
            body = body.replace(frag, '')
    labels = {a.get('id'): str(a.get('label') or '').strip()
              for a in list((group or {}).get('actors') or []) + list((group or {}).get('extras') or []) if a.get('id')}
    sentences = [x for x in _SENT_SPLIT_RE.split(body) if x.strip()]
    out = []
    for cid in cast.get('hidden') or {}:
        terms = [cid]
        label = labels.get(cid) or ''
        if len(label) >= MENTION_MIN_LABEL:
            terms.append(label)
        # 别的人物 label 包含本 label(韩生 ⊂ 韩生妻、画皮鬼 ⊂ 画皮鬼本相)时先抹掉更长的那个,避免子串误报
        longer = [o for o in labels.values() if o and o != label and label and label in o]
        hit = None
        for sent in sentences:
            probe = sent
            for o in longer:
                probe = probe.replace(o, '')
            term = next((t for t in terms if t and t in probe), None)
            if term and not any(w in sent for w in ABSENCE_WORDS):
                hit = term
                break
        if hit:
            out.append((cid, hit))
    return out


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
    out['video_prompt'] = paragraphize(text)
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
    if 'wan-3' in m or 'wan3' in m:
        return {'max_ref_videos': 5, 'max_total_s': 15}   # Fal alibaba/wan-3.0:参考视频 ≤5,合计 ≤15s
    return None


def video_budget(base: Path, ep: str, gid: str) -> dict:
    """本组生效视频模型的参考视频预算:{max_videos, max_total_s, model, provider, source, ref_caps}。
    ref_caps = 模型/工作流自身的 [段数, 合计秒数] 上限(不含项目设置的下调),不支持参考视频时为 None。
    组级覆盖 assets/group_settings/<ep>/<grp>.json 优先,其次集级 <ep>/episode.json;全局模型按 genmedia 的提交配置解析(不可用时按项目
    「视频模型设置」shot_group.max_ref_videos / max_group_s 回落)。comfyui/runninghub 渠道按所配工作流:
    含 MiniMaxH3ReferenceToVideo 节点(H3 Ref2VA)时 ≤3 段/≤15s(官方节点上限),其他工作流不支持参考视频。"""
    settings = read(base/'settings.json', {}) or {}
    sg = settings.get('shot_group') or {}
    sdir = base/'assets/group_settings'/component(ep)
    cfg = None
    try:
        # Use the same layered provider/model resolution as submission (global → episode → group),
        # without importing the API service (pygit2/FastAPI are unnecessary for this media CLI).
        from modules.genmedia import get_config, resolve_video_override, video_cfg_for
        cfg = get_config('video')
        r = resolve_video_override(sdir, component(gid), str(cfg.get('provider') or ''), str(cfg.get('model') or ''))
        model, provider, source = r['video_model'], r['provider'], r['source']
        # 覆盖生效时必须换成该渠道的整份配置:全局(如 volcengine)那份没有 mode/workflow 字段,
        # 拿它问 comfy_h3_ref_video_caps 会一律判成「本渠道不支持参考视频」(DEF-p7-video-011)
        cfg = video_cfg_for(cfg, r)
    except (RuntimeError, KeyError, ValueError, OSError):
        # 提交配置不可用(无 Key 等):只按覆盖文件里的模型判上限,渠道未知
        cfg = None
        ov = read(sdir/f'{component(gid)}.json', {}) or {}
        source = 'group'
        if not ov.get('video_model'):
            ov = read(sdir/'episode.json', {}) or {}
            source = 'episode'
        model, provider = str(ov.get('video_model') or ''), str(ov.get('provider') or '')
        if not model:
            model, provider, source = '', '', 'project_settings'
    caps = _model_caps(model)
    if provider in ('comfyui', 'runninghub'):
        h3 = None
        if cfg is not None:
            from modules.genmedia import comfy_h3_ref_video_caps
            h3 = comfy_h3_ref_video_caps(cfg)
        if not h3:
            # cfg 为 None = 生成模型配置读不到(无 Key 等),工作流未知,如实说明,别让调用方据此删已冻结的参考视频
            reason = (f'渠道 {provider} 当前工作流不是 MiniMax-H3 Ref2VA,不支持参考视频(--ref-video)'
                      if cfg is not None else
                      f'渠道 {provider} 的生成模型配置读不到,无法判定工作流是否支持参考视频;'
                      f'先在「🎨 生成模型」页确认该渠道配置再同步')
            return {'max_videos': 0, 'max_total_s': 0, 'model': model, 'provider': provider, 'source': source,
                    'ref_caps': None, 'reason': reason}
        caps = {'max_ref_videos': h3[0], 'max_total_s': h3[1]}
    if caps:
        max_videos = caps['max_ref_videos']
        if source != 'group' and 'max_ref_videos' in sg:
            max_videos = min(max_videos, int(sg['max_ref_videos'] or 0))
        return {'max_videos': max_videos, 'max_total_s': caps['max_total_s'], 'model': model,
                'provider': provider, 'source': source, 'reason': '',
                'ref_caps': ([caps['max_ref_videos'], float(caps['max_total_s'])]
                             if caps['max_ref_videos'] else None)}
    return {'max_videos': int(sg.get('max_ref_videos', 3) or 0), 'max_total_s': float(sg.get('max_group_s', 15) or 15),
            'model': model, 'provider': provider, 'source': source, 'reason': '', 'ref_caps': None}


def plan_refs(base: Path, ep: str, gid: str, continuation=None, prompt=None) -> dict:
    """决定本组是否挂白模摄影机视频:{camera, videos[], duration_s, budget, skipped_reason}。"""
    group, manifest = whitebox_group(base, ep, gid)
    ep, gid = component(ep), component(gid)
    cast = cast_filter(base, ep, gid)   # 本组白模实际出现的人物(None=项目未开白模链/本组未编译,不限制)
    if group is None or manifest is None:
        return {'camera': None, 'videos': [], 'group': group, 'budget': None, 'cast': cast, 'render': {},
                'verbatim': verbatim_fragments(base, ep, gid), 'skipped_reason': '白模视频未导出(先跑 code/render_whitebox.py)'}
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
    render = (read(base/'directing'/ep/'whitebox'/'episode.json', {}) or {}).get('render') or {}
    return {'camera': cam if cam in videos else None, 'videos': videos, 'cast': cast, 'render': render,
            'verbatim': verbatim_fragments(base, ep, gid),
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


# ---------------------------------------------------------------- camera-relative facing
FACING_BUCKETS = ((30, 'front'), (70, 'three-quarter front'), (110, 'profile'), (150, 'three-quarter back'), (181, 'back'))
FACING_PHRASES = {
    'front': 'facing the camera (front view, face fully visible)',
    'three-quarter front': 'in three-quarter front view, face turned toward screen-{side}',
    'profile': 'in side profile, facing screen-{side}',
    'three-quarter back': 'in three-quarter back view, mostly seen from behind, head turned toward screen-{side}',
    'back': 'with back to the camera (seen from behind, face not visible)',
}
BACK_VIEW_WORDS = ('背对镜头', '背对着镜头', '背影', '背向镜头', 'back to the camera', 'back to camera', 'seen from behind', 'from behind')
MIN_PHASE_S = 0.5        # 短于 min(0.5s, 20% 镜长)的朝向相位视为切点抖动/擦边,不报


def facing_bucket(actor_key: dict, cam_key: dict) -> tuple | None:
    """人物在某一时刻相对摄影机的朝向:(bucket, side)。
    脸朝人偶本地 +Z、root.rotation.y=yaw、头再叠 torso_yaw+head_yaw(与 whitebox-renderer.js / whitebox.py 轨迹 yaw=atan2(dx,dz) 同约定);
    夹角 θ = 脸的朝向与「人物→摄影机」连线的夹角(水平面):≤30° front / ≤70° 三分正 / ≤110° 侧面 / ≤150° 三分背 / 其余 back;
    side = 脸朝画左还是画右(脸的朝向在摄影机 right 轴上的分量)。"""
    basis = _basis(cam_key)
    if basis is None:
        return None
    right = basis[0]
    yaw = float(actor_key.get('yaw') or 0) + float(actor_key.get('torso_yaw') or 0) + float(actor_key.get('head_yaw') or 0)
    face = [math.sin(yaw), 0.0, math.cos(yaw)]
    pos = actor_key.get('position') or [0, 0, 0]
    to_cam = [float(cam_key['position'][0]) - float(pos[0]), 0.0, float(cam_key['position'][2]) - float(pos[2])]
    n = math.sqrt(to_cam[0] ** 2 + to_cam[2] ** 2)
    if n < 1e-6:
        return None
    cos_t = max(-1.0, min(1.0, (face[0] * to_cam[0] + face[2] * to_cam[2]) / n))
    theta = math.degrees(math.acos(cos_t))
    side = 'right' if (face[0] * right[0] + face[2] * right[2]) >= 0 else 'left'
    bucket = next(name for limit, name in FACING_BUCKETS if theta <= limit)
    return bucket, (side if bucket not in ('front', 'back') else '')


def _screen_rect(actor: dict, key: dict, cam_key: dict):
    """人物包围盒在摄影机里的角坐标矩形 (l, r, b, t, depth);摄影机在人物脚下投影范围内/身后 → None(渲染里看不见自己身处的盒子)。"""
    basis = _basis(cam_key)
    if basis is None:
        return None
    right, up, fwd = basis
    size = actor.get('size_m') or [0.5, 1.7, 0.4]
    half_w = 0.5 * max(float(size[0]), float(size[2]) if len(size) > 2 else 0.0)
    height = float(size[1]) if len(size) > 1 else 1.7
    pos = key.get('position') or [0, 0, 0]
    rel = [float(pos[i]) - float(cam_key['position'][i]) for i in range(3)]
    depth = sum(a * b for a, b in zip(rel, fwd))
    if depth <= max(half_w, CAMERA_NEAR_M):
        return None
    lat = sum(a * b for a, b in zip(rel, right)) / depth
    base = sum(a * b for a, b in zip(rel, up)) / depth
    return (lat - half_w / depth, lat + half_w / depth, base, base + height / depth, depth)


def _occluded(rect, others) -> bool:
    """rect 是否被某个更近的矩形完全盖住(粗判:只判包围盒完全包含,不判部分遮挡)。"""
    l, r, b, t, d = rect
    return any(o[4] < d and o[0] <= l and o[1] >= r and o[2] <= b and o[3] >= t for o in others)


def facing_phrase(phases: list) -> str:
    """一镜内的朝向相位序列 → 一句英文:只报开场相位(2026-09-09 用户定:镜内转头/转身/运镜带来的变化由 Shot 段正文描述)。"""
    b, side = phases[0]
    return FACING_PHRASES[b].format(side=side)


def _compress_phases(spans: list, duration: float) -> list:
    """[(bucket, side, t0, t1)…] → 去掉过短相位后的 [(bucket, side)…](相邻同值合并)。"""
    min_s = min(MIN_PHASE_S, 0.2 * duration) if duration > 0 else 0.0
    kept = [sp for sp in spans if sp[3] - sp[2] >= min_s - 1e-9] or (spans[-1:] if spans else [])
    out = []
    for b, side, _t0, _t1 in kept:
        if not out or out[-1] != (b, side):
            out.append((b, side))
    return out


def facing_rows(group: dict, cast: dict | None = None, render: dict | None = None) -> list:
    """逐镜、逐(在画内的)人物相对摄影机朝向:[{shot_no, shot_id, actors: [{id, label, phases, phrase}]}];
    phases 记整镜相位序列(结构化数据),phrase 只取开场相位。
    只算图例里的人物/生物(非骑手、白模里实际出现者);采样点取该镜 [start, start+duration) 内、关键帧 visible 非 false、
    该镜 visible_actor_ids 允许、包围盒在画幅内、摄影机不在其身体里、且未被更近人物的包围盒完全遮住的时刻;
    短于 min(0.5s, 20% 镜长)的相位不报(切点抖动/擦边)。"""
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
    shown = set(cast['visible']) if cast else None
    everyone = [a for a in list(group.get('actors') or []) + list(group.get('extras') or [])
                if a.get('id') and not a.get('rider') and (a.get('keyframes') or [])]
    people = [a for a in everyone if shown is None or a['id'] in shown]
    rows = []
    for i, cam in enumerate(group.get('cameras') or []):
        ckeys = cam.get('keyframes') or []
        if not ckeys:
            continue
        start = float(cam.get('start') or 0); duration = float(cam.get('duration_s') or 0)
        allowed = cam.get('visible_actor_ids')
        in_shot = [a for a in everyone if not (isinstance(allowed, list) and a['id'] not in allowed)]
        times = [t for t in _sample_times(start, duration, *[a['keyframes'] for a in in_shot]) if t < start + duration - 1e-9]
        # 每个采样时刻:所有在镜人物的屏幕矩形(供遮挡判定)
        frames = []
        for t in times:
            ck = sample(ckeys, t - start)
            rects = {}
            for a in in_shot:
                k = sample(a['keyframes'], t)
                if k.get('visible') is False:
                    continue
                rect = _screen_rect(a, k, ck)
                if rect:
                    rects[a['id']] = (rect, k)
            frames.append((t, ck, rects))
        entry = {'shot_no': i + 1, 'shot_id': cam.get('shot_id') or f'shot{i + 1}', 'actors': []}
        for a in people:
            if a not in in_shot:
                continue
            spans = []
            for t, ck, rects in frames:
                if a['id'] not in rects:
                    continue
                rect, k = rects[a['id']]
                if not in_frame(a, k, ck, aspect):
                    continue
                if _occluded(rect, [r for cid, (r, _k) in rects.items() if cid != a['id']]):
                    continue
                fb = facing_bucket(k, ck)
                if not fb:
                    continue
                if spans and spans[-1][:2] == fb:
                    spans[-1] = (fb[0], fb[1], spans[-1][2], t)
                else:
                    spans.append((fb[0], fb[1], t, t))
            phases = _compress_phases(spans, duration)
            if phases:
                entry['actors'].append({'id': a['id'], 'label': a.get('label') or a['id'], 'phases': phases,
                                        'phrase': facing_phrase(phases)})
        if entry['actors']:
            rows.append(entry)
    return rows


def facing_sentence(plan: dict, rows: list | None = None) -> str:
    """固定英文朝向句(进 Whitebox reference 段;机检逐字核对):
    `Whitebox facing: … shot 1 of 3 (sh018) 老道儿 facing the camera (…); shot 2 of 3 (sh019) …`——只写每镜开场朝向,
    镜内转头/转身/运镜的变化由 Shot 段正文写。
    段头故意用小写 `shot k of n`,避免被各机检的 `Shot N:` / `Shot N｜` 切段正则当成镜头段。"""
    videos = plan.get('videos') or []
    if not videos or not plan.get('camera'):
        return ''
    rows = facing_rows(plan.get('group') or {}, plan.get('cast'), plan.get('render')) if rows is None else rows
    if not rows:
        return ''
    ci = videos.index(plan['camera']) + 1
    n = len((plan.get('group') or {}).get('cameras') or [])
    cuts = '; '.join(f"shot {r['shot_no']} of {n} ({r['shot_id']}) " + ', '.join(f"{a['label']} {a['phrase']}" for a in r['actors'])
                     for r in rows)
    return (f"{FACING_KEY} how each figure faces the camera at the start of each cut of [Video {ci}], computed from the previs "
            f"geometry and authoritative for that opening moment — every Shot paragraph opens with the figure in this facing, and any "
            f"turn of the head or body, or change of camera angle, after that moment is described in the Shot paragraph itself: {cuts}.")


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
    facing_line = facing_sentence(plan)
    if facing_line:
        parts.append(facing_line)
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
    out['video_prompt'] = paragraphize(vp)
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
    facing = facing_rows(plan.get('group') or {}, plan.get('cast'), plan.get('render')) if plan['videos'] else []
    if facing:
        out['whitebox_refs']['facing'] = [{'shot_no': r['shot_no'], 'shot_id': r['shot_id'],
                                           'actors': {a['id']: a['phrase'] for a in r['actors']}} for r in facing]
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
    for cid, term in hidden_cast_mentions(vp, plan.get('group'), cast, plan.get('verbatim') or ()):
        errs.append(f"{gid}: whitebox_hidden_mention: 正文提及未在本组白模出现的人物 {cid}(命中「{term}」;{cast_hidden_reason(cast, cid)});"
                    f"画幅外/未入画人物不进视频提示词——删去 Shot 段 staging、【人物】/【主体设定】与 Global constraints 里对其的描述")
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
    rows = facing_rows(plan.get('group') or {}, cast, plan.get('render'))
    expected = facing_sentence(plan, rows)
    if expected:
        if FACING_KEY not in block:
            warns.append(f"{gid}: {BLOCK_KEY} 段缺 \"{FACING_KEY}\" 逐镜相对镜头朝向句(2026-09-09 起由白模机位与人物 yaw 推导);跑 code/sync_whitebox_refs.py --write 补写")
        elif expected not in block:
            errs.append(f"{gid}: \"{FACING_KEY}\" 朝向句与白模计划推导结果不一致(白模改过机位/朝向后未刷新);跑 code/sync_whitebox_refs.py --write")
        body = vp[:vp.index(BLOCK_KEY)] + _BLOCK_RE.sub('', vp[vp.index(BLOCK_KEY):])
        backs = [(r['shot_id'], a['label']) for r in rows for a in r['actors'] if a['phases'][0][0] == 'back']
        if backs and not any(w in body for w in BACK_VIEW_WORDS):
            warns.append(f"{gid}: 白模里 {', '.join(f'{s}/{l}' for s, l in backs)} 开场背对镜头,但正文 Shot 段没有一句背影字样"
                         f"({' / '.join(BACK_VIEW_WORDS[:2] + BACK_VIEW_WORDS[4:6])});模型只按文字理解朝向——该 Shot 段须写明"
                         f"背对镜头(机位在人物身后、只见后脑后背、不转身不回头),并把眉/唇/眼等只有正脸才可见的表演节拍改成背影可见的身体语言")
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
