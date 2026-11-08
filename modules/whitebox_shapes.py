"""白模自定义形状(2026-11-08,docs/whitebox.md「自定义形状:连续软体 tube 与模型库 model」)。

起因:inlove ep01 的白蛇(湖中白蛇 / 风蛇 / 大白蛇)在白模里只能用 6–9 个各自带关键帧的球串起来——球与球各自插值、
互不相连,参考视频里是一串分开的扁椭球,Seedance 照着画成一串圆;提示词图例还逐个写「off-white sphere = 大白蛇」。
白模调度当时开了待决项(WBI-ep01-grp0050-001「白模没有蛇形体型」),可选的只有「接受道具链」或「等宿主加体型」。

本模块让白模调度按剧情自己声明形状(只写 JSON,不写渲染代码),宿主负责校验、渲染、入画判定、碰撞与图例:
- 计划 props 的 `shape: "tube"`:一条连续、按半径剖面逐渐变细的软体(蛇、龙、绳、长布、尾巴、藤蔓、触手)。
  `follow: "head"` 时只写头部轨迹,身体沿头走过的路拖在后面;否则用 `path`(道具局部坐标的中心线,可逐关键帧变形)。
- 计划 props 的 `model: "MDL-…"`:引用项目模型库 `directing/whitebox_models/<MDL-…>.json`,由基础形状
  (box/sphere/cylinder/cone/tube/lathe 旋转体/extrude 挤出轮廓)组成的零件树,零件可带铰链(关键帧 `joints` 转动)。

几何口径与 apps/web/static/whitebox-renderer.js 同(tubeCenterline / buildModel),改动须两边同步;
tests/test_whitebox_shapes.py 用 Node 加载渲染器逐时刻比对中心线。
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

from modules.whitebox import _mat_mul, _mat_vec, _rot_x, _rot_y, component, number, prop_state, sample, vector

MODEL_DIR = 'directing/whitebox_models'
MODEL_SCHEMA = 'whitebox_model.v1'
MODEL_ID_RE = re.compile(r'MDL-[A-Za-z0-9_-]{1,60}')
PART_SHAPES = ('box', 'sphere', 'cylinder', 'cone', 'tube', 'lathe', 'extrude')
PART_MATERIALS = ('solid', 'ghost')
TUBE_HEADS = ('none', 'eyes')
HINGE_AXES = ('x', 'y', 'z')
MAX_PARTS = 200
MAX_POINTS = 64
MAX_RADII = 32
LEGEND_MAX = 120
# 串珠提醒:同名(label / ref)≥ CHAIN_MIN 个简单几何体道具首尾相接(中心距 ≤ CHAIN_GAP × 两半径之和)排成一串
CHAIN_MIN = 3
CHAIN_GAP = 1.3
CHAIN_SPREAD = 3.0      # 串的总跨度 ≥ 平均半径 × 此值才算「一串」(挤成一团的不算)
SPHERE_STEP_M = .5      # 入画判定把软体中心线按这个间距细分成球
_EPS = 1e-6


def euler(pitch=0.0, yaw=0.0, roll=0.0):
    """three.js Euler 'XYZ' 的旋转矩阵 Rx·Ry·Rz(与 whitebox_probe.object_shape 同)。"""
    c, s = math.cos(roll), math.sin(roll)
    rz = [[c, -s, 0], [s, c, 0], [0, 0, 1]]
    return _mat_mul(_rot_x(pitch), _mat_mul(_rot_y(yaw), rz))


def _rot_of(prop, key):
    """关键帧缺朝向时回退道具顶层(同渲染器)。"""
    return euler(*(float(key[a]) if key.get(a) is not None else float(prop.get(a) or 0) for a in ('pitch', 'yaw', 'roll')))


def _add(a, b):
    return [x + y for x, y in zip(a, b)]


def _sub(a, b):
    return [x - y for x, y in zip(a, b)]


def _scale(a, s):
    return [x * s for x in a]


def _norm(a):
    return math.sqrt(sum(x * x for x in a))


# ---------------------------------------------------------------- tube: radius profile / polyline
def radius_profile(value):
    """radius_m:一个数 = 等粗;数组 = 从头到尾均匀分布的半径(线性插值)。"""
    if isinstance(value, list):
        return [float(v) for v in value] if len(value) > 1 else [float(value[0])] * 2
    return [float(value), float(value)]


def radius_at(profile, f):
    f = min(1.0, max(0.0, f))
    x = f * (len(profile) - 1)
    i = min(len(profile) - 2, int(math.floor(x)))
    return profile[i] + (profile[i + 1] - profile[i]) * (x - i)


def poly_length(points):
    return sum(math.dist(a, b) for a, b in zip(points, points[1:]))


def cut_polyline(points, length):
    """从起点沿折线走 length 米为止的那一段(末点按比例插值);length ≤ 0 只剩起点。"""
    out = [list(points[0])]
    left = max(0.0, length)
    for a, b in zip(points, points[1:]):
        seg = math.dist(a, b)
        if seg <= _EPS:
            continue
        if left <= seg + _EPS:
            if left > _EPS:
                out.append([x + (y - x) * min(1.0, left / seg) for x, y in zip(a, b)])
            return out
        out.append(list(b))
        left -= seg
    return out


def tube_heading(prop):
    """follow 软体在轨迹起点之前的身体朝向(单位向量,指向头的前方):首帧 yaw/pitch → 道具顶层 yaw/pitch →
    第一段运动方向 → +Z。与渲染器 tubeHeading 同。"""
    keys = prop.get('keyframes') or []
    first = keys[0] if keys else prop
    for src in (first, prop):
        if src.get('yaw') is not None or src.get('pitch') is not None:
            y, p = float(src.get('yaw') or 0), float(src.get('pitch') or 0)
            return [math.sin(y), -math.sin(p) * math.cos(y), math.cos(p) * math.cos(y)]
    if keys:
        p0 = keys[0]['position']
        for key in keys[1:]:
            d = _sub(key['position'], p0)
            n = _norm(d)
            if n > 1e-4:
                return _scale(d, 1 / n)
    return [0.0, 0.0, 1.0]


def tube_centerline(prop, key, t):
    """软体在 t 时刻的中心线(未平滑折线,头在前)。返回 {points, radii, length, full, travel}:
    points 为道具局部坐标(follow:相对头部;path:道具自身坐标系),length = 可见长度,full = 半径剖面对应的全长,
    travel = follow 时头沿轨迹已走的距离(波动用)。与渲染器 tubeCenterline 同口径。"""
    extent = key.get('extent', prop.get('extent', 1))
    extent = min(1.0, max(0.0, float(1 if extent is None else extent)))
    profile = radius_profile(prop['radius_m'])
    if prop.get('follow') == 'head':
        head = list(key.get('position') or prop.get('position') or [0, 0, 0])
        trail = [head]
        for k in reversed(prop.get('keyframes') or []):
            if float(k['t']) < t - 1e-9 and math.dist(k['position'], trail[-1]) > _EPS:
                trail.append(list(k['position']))
        travel = poly_length(trail)
        full = float(prop['length_m'])
        trail.append(_sub(trail[-1], _scale(tube_heading(prop), full)))
        body = cut_polyline(trail, full * extent)
        points = [_sub(p, head) for p in body]
    else:
        path = key.get('path') or prop.get('path')
        full = poly_length(path)
        points = cut_polyline(path, full * extent)
        travel = 0.0
    radii, s = [], 0.0
    for i, p in enumerate(points):
        if i:
            s += math.dist(points[i - 1], p)
        radii.append(radius_at(profile, s / full if full > _EPS else 0))
    return {'points': points, 'radii': radii, 'length': s, 'full': full, 'travel': travel}


def _subdivide(points, radii, step=SPHERE_STEP_M, limit=400):
    out_p, out_r = [list(points[0])], [radii[0]]
    for (a, b), (ra, rb) in zip(zip(points, points[1:]), zip(radii, radii[1:])):
        n = max(1, min(limit, int(math.ceil(math.dist(a, b) / step))))
        for i in range(1, n + 1):
            u = i / n
            out_p.append([x + (y - x) * u for x, y in zip(a, b)])
            out_r.append(ra + (rb - ra) * u)
    return out_p, out_r


# ---------------------------------------------------------------- validation helpers
def _hex(value, name):
    if not isinstance(value, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', value):
        raise ValueError(f'{name} must be a hex color like #f2f0e6')
    return value


def _points(value, name, dims=3, low=2, high=MAX_POINTS):
    if not isinstance(value, list) or not low <= len(value) <= high:
        raise ValueError(f'{name}: expected {low}..{high} points')
    return [vector(p, name, dims) for p in value]


def _radius(value, name):
    if isinstance(value, list):
        if not 1 <= len(value) <= MAX_RADII:
            raise ValueError(f'{name}: expected 1..{MAX_RADII} radii (head → tail)')
        return [number(v, name, .001) for v in value]
    return number(value, name, .001)


def _extent(value, name):
    if not 0 <= number(value, name) <= 1:
        raise ValueError(f'{name} must be 0..1')


def validate_tube(prop, duration):
    """计划 props 的 shape "tube"(结构错误 = 编译报错);补 size_m(派生包围尺寸,只作诊断/包围盒)。"""
    pid = prop['id']
    if 'radius_m' not in prop:
        raise ValueError(f'{pid}: tube needs radius_m (number or head→tail list)')
    _radius(prop['radius_m'], f'{pid}: tube.radius_m')
    if prop.get('head', 'none') not in TUBE_HEADS:
        raise ValueError(f'{pid}: tube.head must be ' + '/'.join(TUBE_HEADS))
    keys = prop.get('keyframes') or []
    if 'extent' in prop:
        _extent(prop['extent'], f'{pid}: tube.extent')
    for key in keys:
        if 'extent' in key:
            _extent(key['extent'], f'{pid}: keyframe extent')
    rmax = max(radius_profile(prop['radius_m']))
    follow = prop.get('follow')
    if follow is not None and follow != 'head':
        raise ValueError(f'{pid}: tube.follow must be "head"')
    if follow == 'head':
        length = number(prop.get('length_m'), f'{pid}: tube.length_m', .01)
        if 'path' in prop or any('path' in k for k in keys):
            raise ValueError(f'{pid}: follow tube takes no path (the body trails behind the head keyframes)')
        if prop.get('attach'):
            raise ValueError(f'{pid}: follow tube cannot be attached to a hand')
        if not keys and 'position' not in prop:
            raise ValueError(f'{pid}: follow tube needs head keyframes or position')
        wave = prop.get('wave')
        if wave is not None:
            if not isinstance(wave, dict):
                raise ValueError(f'{pid}: tube.wave must be {{amplitude_m, wavelength_m}}')
            number(wave.get('amplitude_m'), f'{pid}: wave.amplitude_m', 0)
            number(wave.get('wavelength_m'), f'{pid}: wave.wavelength_m', .05)
            if wave.get('axis', 'side') not in ('side', 'up'):
                raise ValueError(f'{pid}: wave.axis must be "side" (horizontal) or "up" (vertical)')
        prop['size_m'] = [round(2 * rmax, 4), round(2 * rmax, 4), round(length, 4)]
        return
    if 'length_m' in prop or 'wave' in prop:
        raise ValueError(f'{pid}: length_m / wave only apply to follow tubes (follow: "head")')
    with_path = [k for k in keys if 'path' in k]
    if with_path and len(with_path) != len(keys):
        raise ValueError(f'{pid}: tube keyframes must all have path, or none')
    paths = ([prop['path']] if 'path' in prop else []) + [k['path'] for k in with_path]
    if not paths:
        raise ValueError(f'{pid}: tube needs path (local centerline, head first) or follow: "head"')
    counts = {len(_points(p, f'{pid}: tube.path')) for p in paths}
    if len(counts) != 1:
        raise ValueError(f'{pid}: every tube path needs the same number of points')
    if all(poly_length(p) <= _EPS for p in paths):
        raise ValueError(f'{pid}: tube.path has zero length')
    lo = [min(p[i] for path in paths for p in path) - rmax for i in range(3)]
    hi = [max(p[i] for path in paths for p in path) + rmax for i in range(3)]
    prop['size_m'] = [round(b - a, 4) for a, b in zip(lo, hi)]
    prop['tube_bounds'] = [lo, hi]


# ---------------------------------------------------------------- model library
def model_path(base, mid):
    return Path(base) / MODEL_DIR / f'{mid}.json'


def model_ids(base):
    folder = Path(base) / MODEL_DIR
    return sorted(p.stem for p in folder.glob('MDL-*.json') if MODEL_ID_RE.fullmatch(p.stem)) if folder.is_dir() else []


def _part_box(part):
    """零件几何在其自身(offset 之前)坐标系里的包围盒。"""
    shape = part['shape']
    if shape in ('box', 'sphere', 'cylinder', 'cone'):
        half = [v / 2 for v in part['size_m']]
        return [-h for h in half], half
    if shape == 'tube':
        r = max(radius_profile(part['radius_m']))
        return ([min(p[i] for p in part['path']) - r for i in range(3)],
                [max(p[i] for p in part['path']) + r for i in range(3)])
    if shape == 'lathe':
        r = max(p[0] for p in part['profile'])
        return [-r, min(p[1] for p in part['profile']), -r], [r, max(p[1] for p in part['profile']), r]
    xs, ys = [p[0] for p in part['outline']], [p[1] for p in part['outline']]
    d = part['depth_m'] / 2
    return [min(xs), min(ys), -d], [max(xs), max(ys), d]


def _polygon_area(points):
    return .5 * sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:] + points[:1]))


def validate_model(doc, mid):
    """模型库文件 → 渲染用 model_def(只含画面相关字段,说明文字不进视频指纹)+ 元数据。结构错误 = ValueError。"""
    if not isinstance(doc, dict):
        raise ValueError(f'{mid}: model file must be a JSON object')
    if doc.get('schema_version', MODEL_SCHEMA) != MODEL_SCHEMA:
        raise ValueError(f'{mid}: schema_version must be {MODEL_SCHEMA}')
    if doc.get('id', mid) != mid:
        raise ValueError(f'{mid}: model id must match the file name')
    parts = doc.get('parts')
    if not isinstance(parts, list) or not 1 <= len(parts) <= MAX_PARTS:
        raise ValueError(f'{mid}: parts must list 1..{MAX_PARTS} parts')
    out, frames, hinges = [], {}, []
    lo, hi = [math.inf] * 3, [-math.inf] * 3
    for raw in parts:
        if not isinstance(raw, dict):
            raise ValueError(f'{mid}: every part must be an object')
        pid = component(raw.get('id'))
        where = f'{mid}/{pid}'
        if pid in frames:
            raise ValueError(f'{where}: duplicate part id')
        shape = raw.get('shape', 'box')
        if shape not in PART_SHAPES:
            raise ValueError(f'{where}: part.shape must be ' + '/'.join(PART_SHAPES))
        part = {'id': pid, 'shape': shape}
        parent = raw.get('parent')
        if parent is not None:
            if parent not in frames:
                raise ValueError(f'{where}: parent must name an earlier part')
            part['parent'] = parent
        for name in ('position', 'offset'):
            if name in raw:
                part[name] = vector(raw[name], f'{where}: {name}')
        for axis in ('yaw', 'pitch', 'roll'):
            if raw.get(axis) is not None:
                part[axis] = number(raw[axis], f'{where}: {axis}')
        if 'color' in raw:
            part['color'] = _hex(raw['color'], f'{where}: color')
        if 'material' in raw:
            if raw['material'] not in PART_MATERIALS:
                raise ValueError(f'{where}: material must be ' + '/'.join(PART_MATERIALS))
            part['material'] = raw['material']
        if raw.get('hinge') is not None:
            hinge = raw['hinge']
            if not isinstance(hinge, dict) or hinge.get('axis') not in HINGE_AXES:
                raise ValueError(f'{where}: hinge needs axis x/y/z')
            h = {'axis': hinge['axis']}
            for bound in ('min', 'max'):
                if hinge.get(bound) is not None:
                    h[bound] = number(hinge[bound], f'{where}: hinge.{bound}')
            if 'min' in h and 'max' in h and h['min'] > h['max']:
                raise ValueError(f'{where}: hinge.min > hinge.max')
            part['hinge'] = h
            hinges.append(pid)
        if shape in ('box', 'sphere', 'cylinder', 'cone'):
            part['size_m'] = vector(raw.get('size_m'), f'{where}: size_m', positive=True)
        elif shape == 'tube':
            part['path'] = _points(raw.get('path'), f'{where}: path')
            if poly_length(part['path']) <= _EPS:
                raise ValueError(f'{where}: tube path has zero length')
            if 'radius_m' not in raw:
                raise ValueError(f'{where}: tube needs radius_m')
            part['radius_m'] = _radius(raw['radius_m'], f'{where}: radius_m')
            if raw.get('head', 'none') not in TUBE_HEADS:
                raise ValueError(f'{where}: head must be ' + '/'.join(TUBE_HEADS))
            if raw.get('head', 'none') != 'none':
                part['head'] = raw['head']
        elif shape == 'lathe':
            part['profile'] = _points(raw.get('profile'), f'{where}: profile [[r, y], …]', 2)
            if any(p[0] < 0 for p in part['profile']) or max(p[0] for p in part['profile']) <= 0:
                raise ValueError(f'{where}: lathe profile radii must be >= 0 with one > 0')
        else:
            part['outline'] = _points(raw.get('outline'), f'{where}: outline [[x, y], …]', 2, 3)
            if abs(_polygon_area(part['outline'])) < 1e-6:
                raise ValueError(f'{where}: extrude outline has no area')
            part['depth_m'] = number(raw.get('depth_m'), f'{where}: depth_m', .001)
        # 静止姿态(铰链 0)下的模型坐标:父零件铰链节点 ∘ 平移 position ∘ 旋转 euler,几何再平移 offset
        rot, origin = frames[parent] if parent is not None else ([[1, 0, 0], [0, 1, 0], [0, 0, 1]], [0, 0, 0])
        origin = _add(origin, _mat_vec(rot, part.get('position', [0, 0, 0])))
        rot = _mat_mul(rot, euler(part.get('pitch', 0), part.get('yaw', 0), part.get('roll', 0)))
        frames[pid] = (rot, origin)
        a, b = _part_box(part)
        off = part.get('offset', [0, 0, 0])
        for corner in ([x, y, z] for x in (a[0], b[0]) for y in (a[1], b[1]) for z in (a[2], b[2])):
            w = _add(origin, _mat_vec(rot, _add(corner, off)))
            lo = [min(u, v) for u, v in zip(lo, w)]
            hi = [max(u, v) for u, v in zip(hi, w)]
        out.append(part)
    model_def = {'parts': out}
    for key in ('color', 'material'):
        if key in doc:
            model_def[key] = _hex(doc[key], f'{mid}: color') if key == 'color' else doc[key]
    if model_def.get('material', 'solid') not in PART_MATERIALS:
        raise ValueError(f'{mid}: material must be ' + '/'.join(PART_MATERIALS))
    legend = doc.get('legend_en')
    if legend is not None and (not isinstance(legend, str) or not legend.strip() or len(legend) > LEGEND_MAX
                               or re.search(r'[一-鿿]', legend)):
        raise ValueError(f'{mid}: legend_en must be a short English phrase (<= {LEGEND_MAX} chars)')
    meta = {'label': doc.get('label') if isinstance(doc.get('label'), str) else '',
            'legend_en': legend.strip() if legend else '', 'hinges': hinges,
            'hinge_limits': {p['id']: p['hinge'] for p in out if 'hinge' in p},
            'bounds': [[round(v, 4) for v in lo], [round(v, 4) for v in hi]]}
    return model_def, meta


def load_model(base, mid):
    if not isinstance(mid, str) or not MODEL_ID_RE.fullmatch(mid):
        raise ValueError(f'model id must look like MDL-xxx: {mid!r}')
    path = model_path(base, mid)
    if not path.is_file():
        raise ValueError(f'{mid}: model file missing ({MODEL_DIR}/{mid}.json)')
    try:
        doc = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as error:
        raise ValueError(f'{mid}: invalid JSON ({error})') from error
    return validate_model(doc, mid)


def resolve_model(base, prop, duration, cache=None):
    """计划 props 的 model 引用:读模型库、内联 model_def(进视频指纹,模型改了已导出视频即过期)、补 size_m / model_bounds、
    缺 label 时用模型 label;校验关键帧 joints。"""
    pid = prop['id']
    if prop.get('shape', 'model') != 'model':
        raise ValueError(f'{pid}: a model prop takes shape "model" (or no shape)')
    mid = prop['model']
    cache = {} if cache is None else cache
    if mid not in cache:
        cache[mid] = load_model(base, mid)
    model_def, meta = cache[mid]
    prop['shape'] = 'model'
    prop['model_def'] = json.loads(json.dumps(model_def))
    lo, hi = meta['bounds']
    prop['size_m'] = [round(max(b - a, .001), 4) for a, b in zip(lo, hi)]
    prop['model_bounds'] = [lo, hi]
    if meta['legend_en']:
        prop['model_legend'] = meta['legend_en']
    if not prop.get('label') and meta['label']:
        prop['label'] = meta['label']
    if 'color' in prop:
        _hex(prop['color'], f'{pid}: color')
    limits = meta['hinge_limits']
    for key in prop.get('keyframes') or []:
        joints = key.get('joints')
        if joints is None:
            continue
        if not isinstance(joints, dict):
            raise ValueError(f'{pid}: keyframe joints must map hinged part ids to radians')
        for name, value in joints.items():
            if name not in limits:
                raise ValueError(f'{pid}: joints.{name} is not a hinged part of {mid}')
            value = number(value, f'{pid}: joints.{name}')
            low, high = limits[name].get('min'), limits[name].get('max')
            if (low is not None and value < low - 1e-9) or (high is not None and value > high + 1e-9):
                raise ValueError(f'{pid}: joints.{name}={value:g} outside hinge limits')


def validate_shape_fields(prop):
    """非 tube / model 道具不得带它们的专用键(写错 shape 时早报)。"""
    pid = prop['id']
    shape = prop.get('shape', 'box')
    if 'model' in prop and shape != 'model':
        raise ValueError(f'{pid}: a model prop takes shape "model" (or no shape), not {shape!r}')
    if shape != 'tube':
        for name in ('radius_m', 'length_m', 'follow', 'path', 'wave', 'head', 'extent'):
            if name in prop:
                raise ValueError(f'{pid}: {name} only applies to shape "tube"')
    for key in prop.get('keyframes') or []:
        if shape != 'tube' and ('path' in key or 'extent' in key):
            raise ValueError(f'{pid}: keyframe path / extent only apply to shape "tube"')
        if 'joints' in key and 'model' not in prop:
            raise ValueError(f'{pid}: keyframe joints only apply to model props')
    if prop.get('form_of') is not None:
        component(prop['form_of'])


# ---------------------------------------------------------------- world approximations
def _primitive_sphere(prop, key, center):
    scale = key.get('scale') or prop.get('scale') or [1, 1, 1]
    return 0.5 * math.sqrt(sum((float(a) * float(b)) ** 2 for a, b in zip(prop['size_m'], scale)))


def prop_spheres(prop, by_id, t):
    """道具在 t 时刻的近似包围球 [(世界中心, 半径)](入画判定用;普通几何体与旧口径完全相同:中心 + 半对角线)。
    第二个返回值 = 是否可见,第三个 = 是否挂在手上。"""
    key = sample(prop['keyframes'], t) if prop.get('keyframes') else prop
    center, attached, visible = prop_state(prop, by_id, t)
    scale = [float(s) for s in (key.get('scale') or prop.get('scale') or [1, 1, 1])]
    shape = prop.get('shape') or 'box'
    if shape == 'tube':
        line = tube_centerline(prop, key, t)
        pts, radii = _subdivide(line['points'], line['radii'])
        grow = max(scale)
        margin = float((prop.get('wave') or {}).get('amplitude_m') or 0) if prop.get('follow') == 'head' else 0
        if attached:
            reach = max(_norm([p * s for p, s in zip(pt, scale)]) + r * grow for pt, r in zip(pts, radii))
            return [(center, reach)], visible, attached
        rot = [[1, 0, 0], [0, 1, 0], [0, 0, 1]] if prop.get('follow') == 'head' else _rot_of(prop, key)
        return [(_add(center, _mat_vec(rot, [p * s for p, s in zip(pt, scale)])), r * grow + margin)
                for pt, r in zip(pts, radii)], visible, attached
    if shape == 'model' and prop.get('model_bounds'):
        lo, hi = prop['model_bounds']
        mid = [(a + b) / 2 * s for a, b, s in zip(lo, hi, scale)]
        half = _norm([(b - a) / 2 * s for a, b, s in zip(lo, hi, scale)])
        if attached:
            return [(center, _norm(mid) + half)], visible, attached
        return [(_add(center, _mat_vec(_rot_of(prop, key), mid)), half)], visible, attached
    return [(center, _primitive_sphere(prop, key, center))], visible, attached


def prop_boxes(prop, by_id, t):
    """道具在 t 时刻的碰撞近似:[{center, size_m, yaw, pitch, roll, loose}](whitebox_probe.object_shape 的输入)。
    普通几何体与旧口径相同(一个有向长方体);软体按中心线逐段一个长方体;模型取静止姿态的包围盒。"""
    key = sample(prop['keyframes'], t) if prop.get('keyframes') else prop
    center, attached, visible = prop_state(prop, by_id, t)
    if not visible:
        return []
    scale = [float(s) for s in (key.get('scale') or prop.get('scale') or [1, 1, 1])]
    rot_keys = {axis: key[axis] if key.get(axis) is not None else prop.get(axis, 0) for axis in ('yaw', 'pitch', 'roll')}
    shape = prop.get('shape') or 'box'
    if shape == 'tube':
        if attached:
            (c, r), = prop_spheres(prop, by_id, t)[0]
            return [{'center': c, 'size_m': [2 * r] * 3, 'yaw': 0, 'pitch': 0, 'roll': 0, 'loose': True}]
        spheres, _, _ = prop_spheres(prop, by_id, t)
        out = []
        for (a, ra), (b, rb) in zip(spheres, spheres[1:]):
            d = _sub(b, a)
            n = _norm(d)
            if n <= _EPS:
                continue
            u = _scale(d, 1 / n)
            yaw = math.asin(max(-1.0, min(1.0, u[0])))
            pitch = math.atan2(-u[1], u[2]) if math.cos(yaw) > 1e-9 else 0.0
            r = max(ra, rb)
            out.append({'center': _scale(_add(a, b), .5), 'size_m': [2 * r, 2 * r, n], 'yaw': yaw, 'pitch': pitch,
                        'roll': 0, 'loose': False})
        return out
    if shape == 'model' and prop.get('model_bounds'):
        lo, hi = prop['model_bounds']
        mid = [(a + b) / 2 * s for a, b, s in zip(lo, hi, scale)]
        size = [max((b - a) * s, .001) for a, b, s in zip(lo, hi, scale)]
        if attached:
            r = _norm(mid) + _norm(size) / 2
            return [{'center': center, 'size_m': [2 * r] * 3, 'yaw': 0, 'pitch': 0, 'roll': 0, 'loose': True}]
        return [{'center': _add(center, _mat_vec(_rot_of(prop, key), mid)), 'size_m': size, **rot_keys, 'loose': False}]
    size = [s * c for s, c in zip(prop['size_m'], scale)]
    return [{'center': center, 'size_m': size, **rot_keys, 'loose': attached}]


# ---------------------------------------------------------------- bead-chain warning
CHAIN_DEFORM_M = .15    # 块与块两两距离在组内变化超过此值才算「身体在弯 / 伸缩」;刚体拼装(船、桌椅)平移转动都不改两两距离,不报


def _deforms(members, ids, times):
    by_id = {p['id']: p for p in members}

    def at(pid, t):
        p = by_id[pid]
        key = sample(p['keyframes'], t) if p.get('keyframes') else p
        return key.get('position') or p.get('position') or [0, 0, 0]
    pairs = [(a, b) for i, a in enumerate(ids) for b in ids[i + 1:]]
    ref = {pair: math.dist(at(pair[0], times[0]), at(pair[1], times[0])) for pair in pairs}
    return any(abs(math.dist(at(a, t), at(b, t)) - ref[(a, b)]) > CHAIN_DEFORM_M for t in times for a, b in pairs)


def chain_warnings(props, duration):
    """同名简单几何体道具首尾相接排成一串、一起运动 → WARN(疑似拼连续物体,视频模型会照参考视频画成一串珠子)。"""
    groups = {}
    for prop in props:
        if prop.get('shape', 'box') not in ('box', 'sphere', 'cylinder', 'cone') or prop.get('attach'):
            continue
        name = prop.get('label') or prop.get('ref')
        if name:
            groups.setdefault(name, []).append(prop)
    out = []
    for name, members in groups.items():
        if len(members) < CHAIN_MIN:
            continue
        times = sorted({float(k['t']) for p in members for k in p.get('keyframes') or []} | {0.0, float(duration)})
        times = times[:: max(1, len(times) // 40)]
        best = []
        for t in times:
            nodes = []
            for p in members:
                key = sample(p['keyframes'], t) if p.get('keyframes') else p
                if key.get('visible') is False:
                    continue
                scale = key.get('scale') or p.get('scale') or [1, 1, 1]
                r = max(float(a) * float(b) for a, b in zip(p['size_m'], scale)) / 2
                nodes.append((p['id'], list(key.get('position') or p.get('position') or [0, 0, 0]), r))
            seen, comps = set(), []
            for i in range(len(nodes)):
                if i in seen:
                    continue
                stack, comp = [i], []
                seen.add(i)
                while stack:
                    j = stack.pop()
                    comp.append(j)
                    for k in range(len(nodes)):
                        if k not in seen and math.dist(nodes[j][1], nodes[k][1]) <= CHAIN_GAP * (nodes[j][2] + nodes[k][2]):
                            seen.add(k)
                            stack.append(k)
                comps.append(comp)
            for comp in comps:
                if len(comp) < CHAIN_MIN:
                    continue
                spread = max(math.dist(nodes[a][1], nodes[b][1]) for a in comp for b in comp)
                mean_r = sum(nodes[a][2] for a in comp) / len(comp)
                ids = sorted(nodes[a][0] for a in comp)
                if spread >= CHAIN_SPREAD * mean_r and len(comp) > len(best) and _deforms(members, ids, times):
                    best = ids
        if best:
            shape = members[0].get('shape', 'box')
            out.append(f"{name}: {len(best)} 个 {shape} 道具({', '.join(best[:6])}{'…' if len(best) > 6 else ''})首尾相接排成一串——"
                       '视频模型会照参考视频把它画成一串珠子 / 一节节方块。连续的身体、绳、长布、尾巴改用一个 shape "tube"'
                       '(follow: "head" 让身体沿头部轨迹游动),多部件物体用模型库 model(docs/whitebox.md「自定义形状」);'
                       '确是多个独立物体可忽略。')
    return out


# ---------------------------------------------------------------- legend
def legend_shape(prop, plural=False):
    """图例里描述白模形状的英文名词短语(颜色另加)。"""
    shape = prop.get('shape') or 'box'
    if shape == 'tube':
        profile = radius_profile(prop.get('radius_m', 1))
        words = ['long smooth', 'tapering' if max(profile) - min(profile) > 1e-6 else '', 'tube']
        phrase = ' '.join(w for w in words if w)
        if prop.get('head') == 'eyes':
            phrase += ' with eyes at its head end'
        return phrase
    if shape == 'model':
        return prop.get('model_legend') or 'multi-part model'
    if not plural:
        return shape
    return {'box': 'boxes'}.get(shape, shape + 's')


def legend_note(prop, names=None):
    """图例括注里的附加说明(连续体 / 另一形态)。"""
    notes = []
    if prop.get('shape') == 'tube':
        notes.append('one continuous body')
    owner = prop.get('form_of')
    if owner:
        label = (names or {}).get(owner)
        notes.append(f"it is {label} ({owner}) in another form" if label and label != owner else f'it is {owner} in another form')
    return notes
