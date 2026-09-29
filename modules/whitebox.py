"""Meter-based scene and deterministic shot-group timeline compiler (no GPU required).

Authoring contract: docs/whitebox.md. Existing layout/blocking/camera data remains
canonical; inferred legacy values are always reported, never written back to it.
"""
from __future__ import annotations

import copy
import json
import math
import re
from fractions import Fraction
from pathlib import Path

from modules.entity_ids import is_creature_id
from modules.output_format import resolve_output
from modules.scene_cast import scene_cast_groups

# 人物身份色(2026-09-11 改为整集固定):调色板前 8 色与 modules/whitebox_refs.COLOR_NAMES、
# preview_storyboard.html BM_COLORS 同序;后 8 色为整集人物超过 8 人时的命名备色。
PALETTE = ['#e63946', '#1d78d8', '#2ea043', '#f59e0b', '#8e44ad', '#00acc1', '#e91e63', '#795548',
           '#ffd60a', '#0d9488', '#1e3a8a', '#84cc16', '#b5179e', '#6b8e23', '#800000', '#ff7f50']
LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'


def palette_color(index):
    """整集第 index 位人物的身份色:命名调色板用尽后按黄金角等距取 HSL 色,永不循环复用。"""
    if index < len(PALETTE):
        return PALETTE[index]
    import colorsys
    n = index - len(PALETTE)
    r, g, b = colorsys.hls_to_rgb((n * 0.618033988749895) % 1.0, 0.40 + 0.18 * (n % 2), 0.72)
    return '#%02x%02x%02x' % (round(r * 255), round(g * 255), round(b * 255))


def _seq_sum(values):
    """显式从左到右逐项累加(#63):Python 3.12+ 内置 sum() 对浮点做补偿求和,末位随解释器变化,
    会让组时长/取景中心进指纹后跨版本判 stale。此处固定为 3.10 的朴素累加语义,不取整(取整会让存量有效导出全体失效)。"""
    total = 0
    for value in values:
        total += value
    return total


def free_color(actors, exclude=()):
    """本组尚未被任何 actor 占用、且不在 exclude(整集已登记身份色,#70)里的下一个身份色(仅供整集配色表漏项时兜底)。"""
    used = {a.get('color', '').lower() for a in actors} | {str(c).lower() for c in exclude}
    index = 0
    while palette_color(index).lower() in used:
        index += 1
    return palette_color(index)


def episode_actor_colors(source, contexts=None):
    """整集人物/生物 → 固定身份色(2026-09-11)。

    同一人物在本集所有分镜组里同色:按 generation_groups 顺序、组内 blocking_map.characters 顺序、
    再按同场次名单顺序记首次出场,依次取 palette_color。生物与人物一样有自己的整集固定色
    (2026-09-19):独立态、骑乘态同色,不再随骑手变色。只作坐骑(从不作独立角色)的生物排在
    全部独立角色之后取色,存量人物色位不因此移动。
    """
    contexts = contexts if contexts is not None else scene_cast_groups(source)
    order = []
    groups = source.get('generation_groups', [])
    routes = [r for g in groups for r in (g.get('blocking_map') or {}).get('characters', []) if isinstance(r, dict)]
    independent = {r.get('id') for r in routes}
    mounts = []
    for route in routes:
        mount = route.get('mounted')
        if isinstance(mount, str) and mount and mount not in independent and mount not in mounts:
            mounts.append(mount)
    for group in groups:
        ids = [r.get('id') for r in (group.get('blocking_map') or {}).get('characters', []) if isinstance(r, dict)]
        ids += contexts.get(group.get('group_id'), {}).get('actor_ids', [])
        for cid in ids:
            if isinstance(cid, str) and cid and cid not in order and cid not in mounts:
                order.append(cid)
    return {cid: palette_color(i) for i, cid in enumerate(order + mounts)}


def validate_actor_colors(actors):
    by_id = {a['id']: a for a in actors}
    owners = {}
    for actor in actors:
        color = actor.get('color', '')
        if not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
            raise ValueError(f"{actor['id']}: actor.color must be a hex color")
        rider = actor.get('rider')
        if rider and (rider not in by_id or actor.get('kind') != 'creature'):
            raise ValueError(f"{actor['id']}: mount needs a creature kind and a rider in the group")
        # 2026-09-19:坐骑也有自己的整集固定色,与骑手同色同样算撞色。
        previous = owners.setdefault(color.lower(), actor['id'])
        if previous != actor['id']:
            raise ValueError(f"{previous}/{actor['id']}: actors share color {color}")


def component(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise ValueError(f'Invalid identifier: {value!r}')
    return value


def read(path, default=None):
    if not path.is_file():
        return copy.deepcopy(default)
    return json.loads(path.read_text(encoding='utf-8'))


def number(value, name, minimum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{name}: expected finite number')
    if minimum is not None and value < minimum:
        raise ValueError(f'{name}: must be >= {minimum}')
    return value


def vector(value, name, size=3, positive=False):
    if not isinstance(value, list) or len(value) != size:
        raise ValueError(f'{name}: expected {size} numbers')
    return [number(x, name, 0.001 if positive else None) for x in value]


def render_format(settings, width=None, height=None):
    """Keep camera, preview and encoded pixels at the project's native aspect."""
    aspect, _, _ = resolve_output(settings)
    try:
        a, b = (Fraction(x) for x in aspect.split(':'))
        if a <= 0 or b <= 0:
            raise ValueError()
        ratio = a / b
    except (ValueError, ZeroDivisionError) as error:
        raise ValueError(f'Invalid project aspect ratio: {aspect}') from error
    a, b = ratio.numerator, ratio.denominator
    if width is None and height is None:
        scale = max(1, 960 // (2 * max(a, b)))
        width, height = 2 * a * scale, 2 * b * scale
    elif width is None or height is None:
        raise ValueError('Specify both width and height, or neither')
    if any(isinstance(v, bool) or not isinstance(v, int) or v % 2 or not 128 <= v <= 1920 for v in (width, height)):
        raise ValueError('Render dimensions must be even integers within 128..1920')
    if width * b != height * a:
        raise ValueError(f'Export dimensions must match project aspect {aspect}')
    return {'aspect_ratio': aspect, 'width': width, 'height': height}


def image_size(path):
    """Width/height of a PNG or JPEG (layout_top.png is often JPEG data) from the header, no decoder; None when unreadable."""
    try:
        with open(path, 'rb') as fh:
            head = fh.read(24)
            if head[:8] == b'\x89PNG\r\n\x1a\n' and head[12:16] == b'IHDR':
                width, height = int.from_bytes(head[16:20], 'big'), int.from_bytes(head[20:24], 'big')
                return (width, height) if width and height else None
            if head[:2] != b'\xff\xd8':
                return None
            fh.seek(2)
            for _ in range(256):  # walk marker segments (APPn blocks can exceed 64 KB) until a SOFn frame header
                seg = fh.read(4)
                if len(seg) < 4 or seg[0] != 0xFF:
                    return None
                marker, length = seg[1], int.from_bytes(seg[2:4], 'big')
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    frame = fh.read(5)
                    height, width = int.from_bytes(frame[1:3], 'big'), int.from_bytes(frame[3:5], 'big')
                    return (width, height) if width and height else None
                if marker in (0xD8, 0xD9) or length < 2:
                    return None
                fh.seek(length - 2, 1)
    except OSError:
        return None
    return None


def xyz(xy, dimensions, y=0):
    x, z = vector(xy, 'xy', 2)
    return [(x - .5) * dimensions[0], number(y, 'altitude_m'), (z - .5) * dimensions[2]]


def point(pt, landmarks, dimensions):
    if isinstance(pt, str):
        pt = {'landmark': pt}
    if not isinstance(pt, dict):
        raise ValueError('Position needs xy or landmark')
    if 'position' in pt:
        return vector(pt['position'], 'position')
    if pt.get('landmark') and pt['landmark'] not in landmarks:
        raise ValueError(f"Unknown landmark {pt['landmark']}")
    xy = pt.get('xy', landmarks.get(pt.get('landmark'), {}).get('xy'))
    return xyz(xy, dimensions, pt.get('altitude_m', pt.get('height_m', 0)))


def load_scene(base: Path, sid: str):
    component(sid)
    layout = read(base / 'assets/concepts/scenes' / sid / 'layout.json')
    if not layout:
        raise FileNotFoundError(f'{sid}: missing scene layout.json')
    authored = read(base / 'bible/scenes' / sid / 'whitebox.json', {})
    dimensions = authored.get('dimensions_m') or layout.get('dimensions_m') or [20, 3, 12]
    vector(dimensions, 'dimensions_m', positive=True)
    warnings = []
    if not authored.get('dimensions_m') and not layout.get('dimensions_m'):
        warnings.append('场景尺寸暂按 20 × 12 m、层高 3 m 推断，请由场景建模 Agent 校准。')
    layout_top = base / 'assets/concepts/scenes' / sid / layout.get('layout_top', 'layout_top.png')
    size = image_size(layout_top)
    if size:
        # 俯视图整幅铺满 X×Z 地面(实景图视图/机检 whitebox_layout_ok):X:Z 与图幅不同比会把图单向拉伸
        ratio_model, ratio_image = dimensions[0] / dimensions[2], size[0] / size[1]
        if abs(ratio_model - ratio_image) / ratio_image > .02:
            warnings.append(f'dimensions_m X:Z={dimensions[0]}:{dimensions[2]} 与俯视图 {size[0]}x{size[1]} 宽高比不同,'
                            f'铺地后图会被单向拉伸;应保持 X 并把 Z 改为 {dimensions[0] / ratio_image:.3f}(或按 Z 反推 X),'
                            '再用 code/whitebox_layout_check.py 核墙线。')
    objects = authored.get('objects')
    if objects is None:
        warnings.append('几何体由布局地标推断；物体范围与高度需对照俯视图校准。')
        objects = []
        for lm in layout.get('landmarks', []):
            kind = lm.get('kind')
            if kind in ('direction', 'space', 'entrance', 'opening'):
                continue
            size = {'ground': [dimensions[0] * .8, .02, 2], 'path': [dimensions[0] * .8, .06, 1.2],
                    'boundary': [dimensions[0] * .35, 2.4, .2], 'building': [4, 3, 3],
                    'vegetation': [1.5, 2.5, 1.5], 'furniture': [1.2, .75, .7],
                    'structure': [.4, 2.8, .4]}.get(kind, [.4, .8, .4])
            objects.append({'id': lm['id'], 'shape': 'box', 'size_m': size,
                            'position': xyz(lm['xy'], dimensions, size[1] / 2)})
    seen = set()
    for obj in objects:
        if obj['id'] in seen:
            raise ValueError(f"Duplicate object {obj['id']}")
        seen.add(obj['id'])
        if obj.get('shape', 'box') not in ('box', 'sphere', 'cylinder'):
            raise ValueError(f"Unsupported primitive {obj['shape']}")
        vector(obj['size_m'], 'size_m', positive=True)
        if 'position' not in obj and 'xy' in obj:
            obj['position'] = xyz(obj['xy'], dimensions, obj.get('elevation_m', obj['size_m'][1]/2))
        vector(obj['position'], 'position')
        number(obj.get('yaw', 0), 'yaw')
    # 开底/悬空场景(#76):场景级 floor "none"|"slab"(默认 slab)、grid 布尔(默认 true)。只在 whitebox.json
    # 显式写了才透传,默认场景的 JSON 与指纹不变。
    ground = {}
    if 'floor' in authored:
        if authored['floor'] not in ('none', 'slab'):
            raise ValueError(f'{sid}: floor must be "none" or "slab"')
        ground['floor'] = authored['floor']
    if 'grid' in authored:
        if not isinstance(authored['grid'], bool):
            raise ValueError(f'{sid}: grid must be a boolean')
        ground['grid'] = authored['grid']
    return {**ground, 'schema_version': 'whitebox_scene.v1', 'scene_id': sid,
            'name': layout.get('scene_name', sid), 'units': 'meters',
            'dimensions_m': dimensions, 'objects': objects,
            'render': render_format(read(base / 'settings.json', {})),
            'landmarks': layout.get('landmarks', []), 'views': layout.get('views', []),
            'layout_top': f'assets/concepts/scenes/{sid}/{layout.get("layout_top", "layout_top.png")}',
            'inferred': authored.get('inferred', not bool(authored)),
            'scale_basis': authored.get('scale_basis', 'layout.dimensions_m' if layout.get('dimensions_m') else 'legacy default'),
            'warnings': warnings}


# 姿态受控枚举(2026-09-14 扩为六态;与 modules/storyboard_board.POSE_ENUM 同一套):
# 站/坐/躺沿用,跪/蹲/趴新增——渲染器 whitebox-renderer.js 与包围盒 whitebox_refs.actor_bounds 同步支持。
POSES = ('stand', 'sit', 'lie', 'kneel', 'crouch', 'prone')
_POSE_TEXT_RE = r'坐|躺|卧|趴|跪|蹲|seat|sitting|lying|kneel|crouch|squat|prone'


_POSE_ANY_RE = re.compile(r'趴|俯卧|匍匐|prone|face.?down|跪|kneel|蹲|crouch|squat|躺|卧|lying|lies|reclin|坐|落座|seat|sitting|sits', re.I)


def _pose_clauses(text, other_names=()):
    """按「，。；,;」分句,其他人物名/ID 出现在该分句体位词之前(=他人作主语)的分句剔除(#60)。

    他人名只出现在体位词之后(「蹲在碧云身旁」这类宾语/方位),或紧跟在介词/伴随词后(「随年长家将一同跪下」
    「在乙身旁蹲下」)时不算他人作主语,不剔除。返回 (本人分句文本, 被剔除分句文本)。"""
    names = [n for n in other_names if isinstance(n, str) and n.strip()]
    kept, dropped = [], []
    for clause in re.split(r'[，。；,;]', text or ''):
        hits = [m.start() for n in names for m in re.finditer(re.escape(n), clause)
                if not re.search(r'(随|跟|跟着|和|与|同|向|朝|对|替|给|为|把|在|于|beside|with|to|near|by|behind)\s*$', clause[:m.start()], re.I)]
        pose = _POSE_ANY_RE.search(clause)
        (dropped if hits and (not pose or min(hits) < pose.start()) else kept).append(clause)
    return '，'.join(c for c in kept if c), '，'.join(c for c in dropped if c)


def pose_ambiguous(text, other_names=()):
    """被剔除的他人分句里有体位词:兜底推导可能漏掉/误归属,调用方应出歧义诊断(#60)。"""
    return pose_from(_pose_clauses(text, other_names)[1]) != 'stand'


def other_cast_names(own, candidates):
    """同组其他人物的名字/ID(给 pose_from 剔除他人分句);是本人名/ID 子串的候选不算,免得误删本人分句。"""
    own = [n for n in own if isinstance(n, str) and n]
    out = []
    for name in candidates:
        if isinstance(name, str) and name.strip() and name not in out and not any(name in o for o in own):
            out.append(name)
    return out


def pose_from(text, other_names=()):
    """文字姿态粗推(兜底;优先级低于 blocking.json `pose` 与 shot_list 每镜 `poses`)。

    other_names:同组其他人物名/ID;含这些名字的分句不参与推导(#60,避免把同句他人的体位归给本人)。"""
    text, _ = _pose_clauses(text, other_names)
    if re.search(r'趴|俯卧|匍匐|prone|face.?down', text, re.I):
        return 'prone'
    if re.search(r'跪|kneel', text, re.I):
        return 'kneel'
    if re.search(r'蹲|crouch|squat', text, re.I):
        return 'crouch'
    if re.search(r'躺|卧|lying|lies|reclin', text, re.I):
        return 'lie'
    if re.search(r'坐|落座|seat|sitting|sits', text, re.I):
        return 'sit'
    return 'stand'


# 上身前俯与手臂可达性(#77;与 whitebox-renderer.js leanBend/leanHip/shoulderPoint/ARM_SEGMENT 同一算法,改动须两边同步):
# stand 默认 bend 0、髋 0.35h;crouch 默认 0.6 rad、髋 0.175h;kneel 默认 0、髋 0.175h(大腿顶,膝点不动);
# 其余姿态不前俯。肩点随 bend 绕髋旋转;每段臂长 0.21h,手目标离肩 > 0.42h 即够不着(渲染停在伸直方向)。
LEAN_POSES = ('stand', 'crouch', 'kneel')
ARM_SEGMENT = .21


def lean_bend(key):
    pose = key.get('pose') or 'stand'
    if pose in ('stand', 'kneel'):
        return key.get('bend') or 0
    if pose == 'crouch':
        return key['bend'] if key.get('bend') is not None else .6
    return 0


def lean_hip(pose):
    return .175 if pose in ('crouch', 'kneel') else .35


def shoulder_point(size_m, key, side):
    """肩点(人物本体局部坐标,未计 torso_yaw/yaw);side 左 -1 右 +1。"""
    pose = key.get('pose') or 'stand'
    h = size_m[1]
    bend, hip = lean_bend(key), h*lean_hip(pose)
    y = h*(.58 if pose in ('sit', 'kneel', 'crouch') else .77) - hip
    return [side*size_m[0]*.52, hip + y*math.cos(bend), y*math.sin(bend)]


def pose_channel_warnings(actor):
    """bend 写在不支持前俯的姿态上 → 提示被忽略;手目标超出两段臂长 → 可达性 WARN(不改数据、不报错)。"""
    out = []; ignored = set(); reach = {}
    size = actor.get('size_m') or [.5, 1.7, .4]
    for key in actor.get('keyframes', []):
        pose = key.get('pose') or 'stand'
        if key.get('bend') and pose not in LEAN_POSES:
            ignored.add(pose)
        if actor.get('kind', 'person') == 'creature':
            continue
        for side, name in ((-1, 'left_hand'), (1, 'right_hand')):
            if isinstance(key.get(name), list) and len(key[name]) == 3:
                gap = math.dist(key[name], shoulder_point(size, key, side)) - 2*ARM_SEGMENT*size[1]
                if gap > 1e-6 and gap > reach.get(name, (0, 0))[1]:
                    reach[name] = (key['t'], gap)
    if ignored:
        out.append(f"{actor['id']}: bend 只对 stand/crouch/kneel 生效,{'/'.join(sorted(ignored))} 姿态上的 bend 已忽略。")
    for name, (t, gap) in reach.items():
        out.append(f"{actor['id']}: {name} 在 t={t:g}s 距肩超出臂长 {gap:.2f} m(两段臂各 {ARM_SEGMENT:g}h),"
                   '渲染时手停在伸直方向上;请加 bend 前俯、移近人物或改手部坐标。')
    return out


def shot_pose_of(shot, cid):
    """shot_list 每镜 `poses[<id>]`(分镜层 storyboard shots_draft[].poses 继承而来,2026-09-14)的体位;无/非法返回 None。"""
    poses = shot.get('poses') if isinstance(shot, dict) else None
    if not isinstance(poses, dict):
        return None
    rec = poses.get(cid)
    pose = rec if isinstance(rec, str) else (rec.get('pose') if isinstance(rec, dict) else None)
    pose = str(pose or '').strip().lower()
    return pose if pose in POSES else None


def sample(keys, t):
    if t <= keys[0]['t']:
        return copy.deepcopy(keys[0])
    for a, b in zip(keys, keys[1:]):
        if t < b['t']:
            u = (t-a['t'])/(b['t']-a['t'])
            if a.get('hold'):
                u = 0
            if a.get('easing') == 'smooth':
                u = u*u*(3-2*u)
            out = copy.deepcopy(a)
            for key in ('position', 'target', 'left_hand', 'right_hand', 'scale'):
                if key in a:
                    out[key] = [x+(y-x)*u for x, y in zip(a[key], b[key])]
            for key in ('fov', 'yaw'):
                if key in a:
                    delta = b[key]-a[key]
                    if key == 'yaw':
                        delta = (delta+math.pi) % (2*math.pi)-math.pi
                    out[key] = a[key]+delta*u
            out['t'] = t
            for key in ('bend', 'pitch', 'roll', 'head_pitch', 'head_yaw', 'torso_yaw', 'body_roll', 'neck_extension', 'expression', 'morph'):
                if key in a or key in b:
                    out[key] = a.get(key, 0) + (b.get(key, 0)-a.get(key, 0))*u
            return out
    return copy.deepcopy(keys[-1])


def validate_keys(keys, duration, camera=False):
    if not isinstance(keys, list) or not keys:
        raise ValueError('Empty keyframes')
    channels = [name for name in ('left_hand', 'right_hand', 'scale') if any(name in k for k in keys)]
    previous = -1
    for key in keys:
        t = number(key['t'], 'keyframe.t', 0)
        if t <= previous or t > duration + 1e-6:
            raise ValueError('Keyframe times must increase within duration')
        previous = t
        vector(key['position'], 'position')
        for name in channels:
            vector(key.get(name), name, positive=name == 'scale')
        if camera:
            vector(key['target'], 'target')
            if math.dist(key['position'], key['target']) < .001:
                raise ValueError('Camera position equals target')
            if not 1 <= number(key['fov'], 'fov') <= 150:
                raise ValueError('fov must be 1..150 degrees')
        else:
            if key.get('pose', 'stand') not in POSES:
                raise ValueError('pose must be ' + '/'.join(POSES))
            number(key.get('yaw', 0), 'yaw')
            for axis in ('pitch', 'roll'):
                number(key.get(axis, 0), axis)
            number(key.get('head_pitch', 0), 'head_pitch')
            for name in ('head_yaw', 'torso_yaw', 'body_roll'):
                if abs(number(key.get(name, 0), name)) > math.pi/2:
                    raise ValueError(f'{name} must be -pi/2..pi/2 radians')
            if not 0 <= number(key.get('neck_extension', 0), 'neck_extension') <= .3:
                raise ValueError('neck_extension must be 0..0.3 meters')
            for name in ('expression', 'morph'):
                if not 0 <= number(key.get(name, 0), name) <= 1:
                    raise ValueError(f'{name} must be 0..1')
            if not 0 <= number(key.get('bend', 0), 'bend') <= math.pi/2:
                raise ValueError('bend must be 0..pi/2 radians')
            if 'visible' in key and not isinstance(key['visible'], bool):
                raise ValueError('visible must be a boolean')
    if abs(keys[0]['t']) > 1e-6 or abs(keys[-1]['t']-duration) > 1e-6:
        raise ValueError('Keyframes must cover 0..duration')


def compile_group(base, ep, group, shots, scene, colors=None):
    colors = colors or {}
    gid = component(group['group_id'])
    duration = _seq_sum(number(shots[s]['duration_s'], 'duration_s', .001) for s in group['shots'])
    declared = group.get('total_duration_s', duration)
    if abs(declared-duration) > .05:
        raise ValueError(f'{gid}: group duration differs from shot durations')
    dims = scene['dimensions_m']; landmarks = {x['id']: x for x in scene['landmarks']}
    plan = read(base / 'directing' / ep / 'whitebox_plans' / f'{gid}.json', {})
    warnings = list(scene['warnings']); actors = []
    routes = (group.get('blocking_map') or {}).get('characters', [])
    docs = {s: read(base / 'directing' / ep / 'shots' / s / 'blocking.json', {}) for s in group['shots']}
    for index, route in enumerate(routes):
        pts = [route['start'], *(route.get('path') or []), route.get('end') or route['start']]
        positions = [point(p, landmarks, dims) for p in pts]
        times = [0] + [p.get('t', duration*i/(len(pts)-1)) if isinstance(p, dict) else duration*i/(len(pts)-1)
                       for i, p in enumerate(pts[1:-1], 1)] + [duration]
        cid = component(route['id'])
        # #60:同组其他人物名/ID(不含本人坐骑)所在分句不参与文字体位推导
        others = other_cast_names([cid, route.get('label')], [
            x for r in routes if isinstance(r, dict) and r is not route
            for x in (r.get('id'), r.get('label'), r.get('mounted'))] + [
            x for x in group.get('characters_union', []) + group.get('creatures_union', []) if x != cid])
        posture = route.get('pose') or pose_from(route.get('route_en', ''), others)
        if not route.get('pose') and pose_ambiguous(route.get('route_en', ''), others):
            warnings.append(f'{cid}: route_en 中含其他人物的分句带体位词,已不计入本人;兜底姿态按本人分句推导为 {posture},'
                            '主语不明时请在 blocking_map/blocking.json 写结构化 pose。')
        height = route.get('height_m', 1.4 if is_creature_id(cid) else 1.7)
        keys = []
        for i, (pos, t) in enumerate(zip(positions, times)):
            nxt = positions[min(i+1, len(positions)-1)]
            yaw = math.atan2(nxt[0]-pos[0], nxt[2]-pos[2]) if nxt != pos else (keys[-1]['yaw'] if keys else 0)
            keys.append({'t': t, 'position': pos, 'pose': posture, 'yaw': yaw})
        # Numeric per-shot anchors and beats override the legacy evenly spaced route.
        offset = 0; keyed = {k['t']: k for k in keys}; pose_events = {0: posture}
        for sid in group['shots']:
            sd = shots[sid]['duration_s']; doc = docs[sid]
            entry = next((x for x in doc.get('characters', [])+doc.get('creatures', []) if x.get('id') == cid), {})
            for suffix, t in [('start', offset), ('end', offset+sd)]:
                key = {**sample(keys, t), 't': t}
                if entry.get(f'position_{suffix}') is not None:
                    key['position'] = vector(entry[f'position_{suffix}'], 'position')
                else:
                    if entry.get(f'xy_{suffix}') is not None:
                        key['position'] = xyz(entry[f'xy_{suffix}'], dims, key['position'][1])
                    if entry.get(f'altitude_{suffix}_m') is not None:
                        key['position'][1] = number(entry[f'altitude_{suffix}_m'], 'altitude_m')
                if any(field in entry for field in (f'position_{suffix}', f'xy_{suffix}', f'altitude_{suffix}_m')):
                    keyed[t] = key
            # 体位优先级:blocking.json 该角色 `pose` → shot_list 该镜 `poses[id].pose`(分镜层结构化字段)→ start_pos 文字粗推
            shot_pose = shot_pose_of(shots[sid], cid)
            own_start = _pose_clauses(entry.get('start_pos', ''), others)[0]
            initial_pose = entry.get('pose') or shot_pose or pose_from(own_start)
            if not entry.get('pose') and not shot_pose and pose_ambiguous(entry.get('start_pos', ''), others):
                warnings.append(f'{sid}/{cid}: start_pos 中含其他人物的分句带体位词,已不计入本人;请写结构化 pose。')
            if entry.get('pose') or shot_pose or re.search(_POSE_TEXT_RE, own_start, re.I):
                keyed[offset] = {**keyed.get(offset, sample(keys, offset)), 't': offset, 'pose': initial_pose}
                pose_events[offset] = initial_pose
            for beat in entry.get('path', []) + entry.get('beats', []):
                if not isinstance(beat.get('t'), (float, int)) or not 0 <= beat['t'] <= sd:
                    continue
                t = offset+beat['t']; k = {**keyed.get(t, sample([keyed[x] for x in sorted(keyed)], t)), 't': t}
                if beat.get('position') is not None:
                    k['position'] = vector(beat['position'], 'position')
                elif beat.get('xy') is not None:
                    k['position'] = xyz(beat['xy'], dims, k['position'][1])
                elif beat.get('landmark'):
                    k['position'] = point({**beat, 'altitude_m': k['position'][1]}, landmarks, dims)
                if 'altitude_m' in beat or 'height_m' in beat:
                    k['position'] = list(k['position'])
                    k['position'][1] = number(beat.get('altitude_m', beat.get('height_m')), 'altitude_m')
                if beat.get('pose'):
                    k['pose'] = beat['pose']
                    pose_events[t] = beat['pose']
                if any(field in beat for field in ('position', 'xy', 'landmark', 'altitude_m', 'height_m', 'pose')):
                    keyed[t] = k
            offset += sd
        keys = [keyed[t] for t in sorted(keyed)]
        for key in keys:
            key['pose'] = pose_events[max(t for t in pose_events if t <= key['t'])]
        actor = {'id': cid, 'label': route.get('label', cid), 'letter': LETTERS[index % 26],
                 'color': colors.get(cid) or palette_color(index), 'kind': 'creature' if is_creature_id(cid) else 'person',
                 'size_m': route.get('size_m', [height*.28, height, height*.22]), 'keyframes': keys}
        actors.append(actor)
        if route.get('mounted'):
            actors.append({'id': component(route['mounted']), 'label': route['mounted'], 'letter': '',
                           'color': colors.get(route['mounted'], ''), 'kind': 'creature', 'size_m': [0.65, 1.5, 2.1],
                           'rider': cid, 'keyframes': copy.deepcopy(keys)})
            for key in actor['keyframes']:
                key['position'][1] += 1.45
                key['pose'] = 'sit'
    for actor in actors:   # 整集配色表漏项(坐骑等)从本组未用色兜底
        if not actor['color']:
            actor['color'] = free_color(actors, colors.values())
    if 'actors' not in plan:
        warnings.append('存量人物身高、文字姿态及未标时动线按规约推断；精确节拍可在白模计划中覆盖。')
    cameras = []; offset = 0
    for sid in group['shots']:
        shot = shots[sid]; sd = shot['duration_s']
        cam = read(base / 'directing' / ep / 'shots' / sid / 'camera.json', {})
        view = next((v for v in scene['views'] if v.get('tile') == shot.get('view_tile')), None)
        if not view:
            if not scene['views']:
                raise ValueError(f'{sid}: missing layout views')
            view = scene['views'][0]
            warnings.append(f'{sid}: 缺少 view_tile，暂取布局的第一个机位，请校准。')
        pos = point(view['camera_from'], landmarks, dims)
        target = point(view['looking_at'], landmarks, dims)
        text = shot.get('camera_position', '')
        pos[1] = .25 if re.search(r'贴地|ground', text, re.I) else (dims[1]*.85 if re.search(r'高机位|俯拍|high', text, re.I) else 1.6)
        target[1] = .15 if pos[1] == .25 else 1.2
        lens = re.search(r'(\d+(?:\.\d+)?)\s*mm', text)
        fov = math.degrees(2*math.atan(24/(2*float(lens[1])))) if lens else 50
        # Layout views establish the axis; they are not calibrated lens positions.
        # Frame the shot's subjects along that axis when no numeric camera track exists.
        subjects = [a for a in actors if a['id'] in shot.get('characters', []) + shot.get('creatures', [])]
        if subjects and not cam.get('whitebox_keyframes'):
            centers = [sample(a['keyframes'], offset)['position'] for a in subjects]
            direction = [target[0]-pos[0], target[2]-pos[2]]
            length = math.hypot(*direction) or 1
            low = pos[1] == .25
            altitude = _seq_sum(p[1] for p in centers)/len(centers)
            pos[1] += altitude
            target = [_seq_sum(p[0] for p in centers)/len(centers), altitude + (.16 if low else 1.2),
                      _seq_sum(p[2] for p in centers)/len(centers)]
            frame_height = {'ECU':.35,'CU':.7,'MCU':1.2,'MS':2.1,'MLS':2.8,'FS':3.4,'WS':5,'EWS':9}.get(shot.get('size_code'),3.4)
            spread = max((math.dist(a,b) for a in centers for b in centers),default=0)
            fmt = scene['render']
            distance = max(frame_height,spread/(fmt['width']/fmt['height'])*1.2)/(2*math.tan(math.radians(fov/2)))
            pos = [target[0]-direction[0]/length*distance, pos[1], target[2]-direction[1]/length*distance]
            warnings.append(f'{sid}: 沿布局视轴按景别对准镜首主体，机距为推断值。')
        a = {'t': 0, 'position': pos, 'target': target, 'fov': fov}
        b = copy.deepcopy(a); b['t'] = sd
        move = cam.get('movement', 'static')
        delta = [target[i]-pos[i] for i in range(3)]
        if move in ('push_in', 'pull_out'):
            sign = .2 if move == 'push_in' else -.2
            b['position'] = [p+d*sign for p, d in zip(pos, delta)]
        elif move in ('follow', 'track_right', 'track_left'):
            if move == 'follow' and actors:
                start, end = sample(actors[0]['keyframes'], offset), sample(actors[0]['keyframes'], offset+sd)
                shift = [y-x for x, y in zip(start['position'], end['position'])]
            else:
                length = math.hypot(delta[0], delta[2]) or 1
                sign = 1 if move == 'track_right' else -1
                shift = [-delta[2]/length*sign, 0, delta[0]/length*sign]
            b['position'] = [x+y for x, y in zip(pos, shift)]
            b['target'] = [x+y for x, y in zip(target, shift)]
        elif move != 'static':
            warnings.append(f'{sid}: 运镜 {move} 需白模计划提供数值关键帧，暂按固定机位显示。')
        if move != 'static':
            warnings.append(f'{sid}: 运镜幅度由 {move} 推断，请校准数值轨迹。')
        cameras.append({'shot_id': sid, 'start': offset, 'duration_s': sd, 'movement': move,
                        'keyframes': cam.get('whitebox_keyframes') or [a, b]})
        offset += sd
    if 'actors' in plan:
        # Replace tracks while preserving the established letter/color assignment.
        overrides = {a['id']: a for a in plan['actors']}
        if len(overrides) != len(plan['actors']) or set(overrides) != {a['id'] for a in actors}:
            raise ValueError(f'{gid}: plan actor IDs must match blocking map (including mounts)')
        actors = [{**a, **{k:v for k,v in overrides[a['id']].items() if k not in ('color', 'letter', 'id')}} for a in actors]
    # Scene occupants are separate from shot subjects / blocking-map letters.
    for actor in plan.get('scene_actors', []):
        if actor['id'] not in group.get('scene_cast', []) or actor['id'] in {a['id'] for a in actors}:
            raise ValueError('scene_actors must be unique scene cast outside the blocking-map cast')
        actor = copy.deepcopy(actor)
        actor['color'] = colors.get(actor['id']) or free_color(actors, colors.values())
        actor['letter'] = ''
        actors.append(actor)
    if 'cameras' in plan:
        cameras = plan['cameras']
        warnings = [w for w in warnings if not any(w.startswith(s+':') for s in group['shots'])]
    warnings.extend(plan.get('warnings', []))
    expected = set(group.get('characters_union', [])) | set(group.get('creatures_union', []))
    if expected - {a['id'] for a in actors}:
        raise ValueError(f'{gid}: cast missing from blocking map: {sorted(expected-{a["id"] for a in actors})}')
    # Background performers remain separate from the registered cast. They are
    # needed for shots whose only subjects are unnamed passengers, for example.
    extras = plan.get('extras', [])
    seen = {a['id'] for a in actors}
    for extra in extras:
        eid = component(extra['id'])
        if not eid.startswith('EXTRA-') or eid in seen:
            raise ValueError('Extra IDs must be unique EXTRA- identifiers outside the cast')
        seen.add(eid)
        if extra.get('kind', 'person') not in ('person', 'creature'):
            raise ValueError('Extra kind must be person/creature')
    for actor in actors + extras:
        vector(actor['size_m'], 'actor.size_m', positive=True)
        if 'faceless' in actor and not isinstance(actor['faceless'], bool):
            raise ValueError('actor.faceless must be boolean')
        if 'morph_target' in actor:
            vector(actor['morph_target']['size_m'], 'morph_target.size_m', positive=True)
            if not re.fullmatch(r'#[0-9a-fA-F]{6}', actor['morph_target'].get('color', '')):
                raise ValueError('morph_target.color must be a hex color')
        validate_keys(actor['keyframes'], duration)
        warnings.extend(pose_channel_warnings(actor))
    props = plan.get('props', [])
    prop_ids = set()
    for prop in props:
        pid = component(prop['id'])
        if pid in prop_ids or prop.get('shape', 'box') not in ('box', 'sphere', 'cylinder'):
            raise ValueError('Props need unique IDs and supported primitive shapes')
        prop_ids.add(pid)
        vector(prop['size_m'], 'prop.size_m', positive=True)
        vector(prop['position'], 'prop.position')
        number(prop.get('yaw', 0), 'prop.yaw')
        for axis in ('pitch', 'roll'):
            number(prop.get(axis, 0), 'prop.'+axis)
        if set(prop.get('shot_ids', [])) - set(group['shots']):
            raise ValueError('Prop shot_ids must belong to the group')
        if 'projection_screen' in prop:
            screen = prop['projection_screen']
            if not isinstance(screen, dict) or not isinstance(screen.get('actor_ids'), list) or not screen['actor_ids'] or any(not isinstance(cid, str) or cid not in seen for cid in screen['actor_ids']):
                raise ValueError('projection_screen.actor_ids must reference group actors')
            if prop.get('shape', 'box') != 'box' or any(prop.get(axis, 0) for axis in ('yaw', 'pitch', 'roll')) or prop.get('keyframes'):
                raise ValueError('projection_screen requires a fixed axis-aligned box')
            if set(screen.get('shot_ids', [])) - set(group['shots']):
                raise ValueError('projection_screen shot_ids must belong to the group')
        if prop.get('keyframes') is not None:
            validate_keys(prop['keyframes'], duration)
    cursor = 0
    if [c['shot_id'] for c in cameras] != group['shots']:
        raise ValueError(f'{gid}: cameras must match group shot order')
    for camera in cameras:
        sd = shots[camera['shot_id']]['duration_s']
        if 'visible_actor_ids' in camera:
            visible_ids = camera['visible_actor_ids']
            if not isinstance(visible_ids, list) or any(not isinstance(cid, str) for cid in visible_ids) or set(visible_ids) - seen - set(group.get('scene_cast', [])):
                raise ValueError('visible_actor_ids must reference actors or extras in the group')
            if group.get('scene_cast_enabled') and not str(camera.get('visibility_override_reason') or '').strip():
                camera.pop('visible_actor_ids')
                warnings.append(f"{camera['shot_id']}: 已忽略无明确原因的人物过滤，按实际机位显示在场人物。")
        if abs(camera['start']-cursor) > 1e-6 or abs(camera['duration_s']-sd) > 1e-6:
            raise ValueError(f'{gid}: camera intervals must match shot timing')
        validate_keys(camera['keyframes'], sd, True); cursor += sd
        from modules.whitebox_camera import check_camera
        camera_errors = check_camera(base, ep, shots[camera['shot_id']], camera, gid)
        if camera_errors:
            raise ValueError('; '.join(camera_errors))
    # 待决项(2026-09-09,docs/whitebox.md「待决项与用户裁决」):Agent 调度时拿不准的取舍,结构校验后随组透传给预览页
    from modules.whitebox_issues import validate_issues
    issues = validate_issues(plan, ep, gid, duration, group['shots'], seen | set(group.get('scene_cast', [])))
    return {'schema_version': 'whitebox_group.v1', 'group_id': gid, 'scene_id': group['scene_id'],
            'scene_no': group.get('scene_no'), 'duration_s': duration, 'actors': actors, 'extras': extras,
            'props': props, 'cameras': cameras,
            'continuity_from': group.get('continuity_from'), 'continuity': plan.get('continuity', {}),
            'warnings': warnings, 'authored': bool(plan), 'issues': issues}


def complete_scene_actors(groups, contexts, raw_groups, errors, colors=None):
    colors = colors or {}
    # Snapshot authored tracks before filling holes so a later authored entrance
    # or departure, rather than a synthetic placeholder, remains authoritative.
    anchors = {}
    for i, group in enumerate(groups):
        context = contexts[group['group_id']]
        key = context['key']
        for actor in group['actors']:
            presence = context.get('presence', {}).get(actor['id'])
            if presence:
                if presence['state'] != 'present' or (actor.get('scene_inherited_from')
                        and all(frame.get('visible') is False for frame in actor['keyframes'])):
                    for frame in actor['keyframes']:
                        frame['visible'] = presence['state'] == 'present'
                actor['presence'] = copy.deepcopy(presence)
            anchors.setdefault((key, actor['id']), []).append((i, copy.deepcopy(actor), group['group_id']))
    for i, group in enumerate(groups):
        gid = group['group_id']; context = contexts[gid]
        group['scene_cast'] = context['actor_ids']
        present = {a['id'] for a in group['actors']}
        for cid in context['actor_ids']:
            if cid in present:
                continue
            candidates = anchors.get((context['key'], cid), [])
            if not candidates:
                if context.get('presence', {}).get(cid, {}).get('state') in ('absent', 'remote'):
                    continue  # An absent body needs no invented spatial anchor.
                errors.append({'group_id': gid, 'error': f'{cid}: 同场次缺少空间锚点，请补 scene_actors'})
                continue
            previous = [entry for entry in candidates if entry[0] < i]
            index, actor, origin = previous[-1] if previous else candidates[0]
            actor = copy.deepcopy(actor)
            anchor = copy.deepcopy(actor['keyframes'][-1 if index < i else 0])
            actor['keyframes'] = [{**copy.deepcopy(anchor), 't': t} for t in (0, group['duration_s'])]
            actor['letter'] = ''
            actor['color'] = colors.get(cid) or free_color(group['actors'], colors.values())
            actor['scene_inherited_from'] = origin
            group['actors'].append(actor)
            group['warnings'].append(f'{cid}: 同场次在场人物，沿用 {origin} 的'+('尾' if index < i else '首')+'姿态与位置；补充走位可写 scene_actors。')
        for cid, presence in (raw_groups[gid].get('scene_presence') or {}).items():
            if cid not in context['actor_ids'] or not isinstance(presence, dict) or presence.get('state') not in ('absent', 'remote', 'present') or not presence.get('reason'):
                errors.append({'group_id': gid, 'error': f'{cid}: scene_presence requires a scene actor, state and reason'})
        for cid, presence in context.get('presence', {}).items():
            for actor in group['actors']:
                if actor['id'] == cid:
                    if presence['state'] != 'present' or (actor.get('scene_inherited_from')
                            and all(frame.get('visible') is False for frame in actor['keyframes'])):
                        for key in actor['keyframes']:
                            key['visible'] = presence['state'] == 'present'
                    actor['presence'] = copy.deepcopy(presence)


def compile_episode(base: Path, ep: str, apply_overrides: bool = True):
    """apply_overrides=False 时不合并导演台覆盖层(directing/<ep>/whitebox/director/overrides.json),用于判断覆盖是否已固化进计划。"""
    component(ep)
    source = read(base / 'directing' / ep / 'shot_list.json')
    if not source:
        raise FileNotFoundError(f'{ep}: missing shot_list.json')
    shots = {s['shot_id']: s for s in source.get('shots', [])}
    contexts = scene_cast_groups(source)
    colors = episode_actor_colors(source, contexts)   # 整集固定身份色:同一人物各组同色
    raw_groups = {g['group_id']: g for g in source.get('generation_groups', [])}
    scenes = {}; groups = []; errors = []; by_id = {}
    for raw in source.get('generation_groups', []):
        gid = raw.get('group_id', '?')
        try:
            sid = component(raw['scene_id'])
            if sid not in scenes:
                scenes[sid] = load_scene(base, sid)
            group = compile_group(base, ep, {**raw, 'scene_cast': contexts[gid]['actor_ids'],
                                  'scene_cast_enabled': 'scene_cast' in raw}, shots, scenes[sid], colors)
            prev = by_id.get(group['continuity_from'])
            policy = group['continuity']
            for key in ('actors', 'camera'):
                if policy.get(key, 'validate' if key == 'actors' else 'cut') not in ('cut', 'validate', 'inherit'):
                    raise ValueError('continuity policy must be cut/validate/inherit')
            if any(v == 'inherit' for v in policy.values()) and (not prev or prev['scene_id'] != sid or prev['scene_no'] != group['scene_no']):
                raise ValueError(f'{gid}: cannot inherit across scene/time boundaries or missing predecessor')
            if prev and prev['scene_id'] == sid and prev['scene_no'] == group['scene_no']:
                before = {a['id']: a for a in prev['actors']}
                for actor in group['actors']:
                    if actor['id'] not in before or policy.get('actors') == 'cut':
                        continue
                    last = before[actor['id']]['keyframes'][-1]; first = actor['keyframes'][0]
                    if policy.get('actors') == 'inherit':
                        actor['keyframes'][0] = {**copy.deepcopy(last), 't': 0}
                    elif math.dist(last['position'], first['position']) > .05 or last.get('pose') != first.get('pose'):
                        group['warnings'].append(f"{actor['id']}: 与 {prev['group_id']} 的位置/姿态不连续，请确认切换或继承。")
                last = prev['cameras'][-1]['keyframes'][-1]
                if policy.get('camera') == 'inherit':
                    group['cameras'][0]['keyframes'][0] = {**copy.deepcopy(last), 't': 0}
                elif policy.get('camera') == 'validate' and any(group['cameras'][0]['keyframes'][0][k] != last[k] for k in ('position', 'target', 'fov')):
                    group['warnings'].append(f"机位与 {prev['group_id']} 不连续。")
            by_id[gid] = group; groups.append(group)
        except (ValueError, KeyError, TypeError, FileNotFoundError) as error:
            errors.append({'group_id': gid, 'error': str(error)})
    complete_scene_actors(groups, contexts, raw_groups, errors, colors)
    if apply_overrides:
        # 导演台覆盖层(2026-09-13):用户拖动/改数值的整条关键帧按对象盖在 Agent 计划之上;无效覆盖只在 warnings 里报
        from modules.director import apply_overrides as _apply_ov, load_overrides
        ov_groups = load_overrides(base, ep).get('groups', {})
        for group in groups:
            ov = ov_groups.get(group['group_id'])
            if ov:
                _apply_ov(group, ov)
    registered = {c.lower() for c in colors.values()}
    extra_colors = {}   # 同一群演 ID 跨组沿用首次改派的颜色(#70)
    for group in groups:
        # 群演(人/生物)颜色是计划手填的:缺色或与登记角色撞色时宿主改派未用色;群演之间可共用一色。
        # #70:撞色判定与改派都避开整集 actor_colors 的全部登记身份色,而不只是本组 actors。
        taken = {a.get('color', '').lower() for a in group['actors']} | registered
        for extra in group.get('extras', []):
            color = str(extra.get('color') or '')
            if not re.fullmatch(r'#[0-9a-fA-F]{6}', color) or color.lower() in taken:
                kept = extra_colors.get(extra['id'])
                extra['color'] = kept if kept and kept.lower() not in taken else free_color(group['actors'] + group['extras'], registered)
                extra_colors[extra['id']] = extra['color']
                group['warnings'].append(f"{extra['id']}: 群演颜色{'缺失' if not color else '与登记角色撞色'}，宿主改为 {extra['color']}。")
        try:
            validate_actor_colors(group['actors'])
        except ValueError as error:
            errors.append({'group_id': group['group_id'], 'error': str(error)})
    from modules.whitebox_camera import check_matches
    errors.extend(check_matches(base, ep, groups))
    # 用户裁决(directing/<ep>/whitebox/decisions.json)合并进各组 issues,并给整集汇总
    from modules.whitebox_issues import load_decisions, merge_decision, summarize
    decisions = load_decisions(base, ep)
    for group in groups:
        group['issues'] = [merge_decision(i, decisions.get(i['issue_id'])) for i in group.get('issues', [])]
    return {'schema_version': 'whitebox_episode.v1', 'staging_version': 3, 'project': base.name, 'ep': ep,
            'render': render_format(read(base / 'settings.json', {})),
            'scenes': scenes, 'groups': groups, 'errors': errors, 'actor_colors': colors,
            'issues_summary': summarize(groups),
            'source_group_count': len(source.get('generation_groups', []))}
