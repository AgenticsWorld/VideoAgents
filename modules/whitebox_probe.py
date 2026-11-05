"""白模宿主实测(2026-11-05,docs/whitebox.md「宿主实测:code/whitebox_probe.py」)。

白模调度此前每个项目都自写碰撞 / 朝向 / 取景测量脚本(fengshen3 25 个、jidi 8 个、dzg6 十几个;cartoon1 一单 135 分钟里
自写了 192×108 射线求交测天空占比)。这里给出三项统一实测,全部只读、本地、无生成费用:

- collide:人物 / 生物 / 群演按姿态的有向脚印(站、坐腿前伸、跪小腿后伸、蹲、卧沿身长)沿完整插值轨迹逐帧扫掠,
  对其他人物、场景物件、实心道具算平面净距;台阶 / 路缘(顶面不高于脚下 0.25 m)与身下的座椅 / 床自动当支撑豁免,
  坐骑与骑手、挂在手上的道具与持有人、ghost 效果体不查。手臂不计(手部接触请看 frame 或画面)。
- facing:按计划 facing_targets、逐镜 blocking.json 的 body_facing_target_id、组动线 facing_target_id 与 --pair
  声明的「谁面向谁」逐帧算身体(yaw+torso_yaw;卧 / 趴取头的指向)与头(再加 head_yaw)的偏差角、达标时长占比;
  附每镜开场的相对镜头朝向。
- frame:用预览 / 导出同一个渲染器(whitebox-renderer.js)在无头浏览器里按对象着色离屏渲染摄影机视角,数像素得到
  天空(背景)占比、每个人物 / 道具的入画、占画面比、被遮挡后的可见比、画面位置与是否出画边,以及占画最多的场景物件。
  ghost 效果体不算遮挡。
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

from modules.whitebox import component, prop_state, read, render_format, sample

STEP_S = 1 / 24
ACTOR_GAP_M = .20        # 人物之间参考净距(SOUL「防穿模必检」)
OBSTACLE_GAP_M = .15     # 人物与障碍物参考净距
STEP_TOL_M = .25         # 站姿:顶面不高于脚下这么多的物体当台阶 / 路缘(可踩上、可跨过)
SEAT_FRAC = .6           # 坐 / 卧 / 跪 / 蹲:身下(含锚点或脚印中心)且顶面不高于脚下 0.6 身高的物体当座椅 / 床
FACING_TOL_DEG = 3.0     # 直接相向交谈的身体朝向误差上限(docs「对话身体朝向」)
LOW_POSES = ('sit', 'lie', 'prone', 'kneel', 'crouch')
PASSABLE_ROLES = ('decor', 'backdrop', 'soft', 'water')   # 场景物件 collision_role 为这些时不算障碍(装饰、远景片、帘幔软物、水面)


# ---------------------------------------------------------------- 平面几何
def _rect(cx, cz, yaw, x0, x1, z0, z1):
    c, s = math.cos(yaw), math.sin(yaw)
    return [(cx + c * x + s * z, cz - s * x + c * z) for x, z in ((x0, z0), (x1, z0), (x1, z1), (x0, z1))]


def _axes(poly):
    out = []
    for i in range(len(poly)):
        (x1, z1), (x2, z2) = poly[i], poly[(i + 1) % len(poly)]
        out.append((z1 - z2, x2 - x1))
    return out


def _overlap(P, Q):
    for ax, az in _axes(P) + _axes(Q):
        a = [x * ax + z * az for x, z in P]
        b = [x * ax + z * az for x, z in Q]
        if max(a) < min(b) - 1e-9 or max(b) < min(a) - 1e-9:
            return False
    return True


def _point_seg(p, a, b):
    ax, az = b[0] - a[0], b[1] - a[1]
    n = ax * ax + az * az
    u = 0 if n < 1e-12 else max(0, min(1, ((p[0] - a[0]) * ax + (p[1] - a[1]) * az) / n))
    return math.hypot(p[0] - a[0] - u * ax, p[1] - a[1] - u * az)


def poly_gap(P, Q):
    """两个凸多边形(xz)的 (净距 m, 是否重叠);重叠时净距记 0。"""
    if _overlap(P, Q):
        return 0.0, True
    edges = lambda R: [(R[i], R[(i + 1) % len(R)]) for i in range(len(R))]
    best = min(_point_seg(p, a, b) for p in P for a, b in edges(Q))
    return min(best, min(_point_seg(q, a, b) for q in Q for a, b in edges(P))), False


def _inside(point, poly):
    sign = None
    for i in range(len(poly)):
        (x1, z1), (x2, z2) = poly[i], poly[(i + 1) % len(poly)]
        cross = (x2 - x1) * (point[1] - z1) - (z2 - z1) * (point[0] - x1)
        if abs(cross) < 1e-12:
            continue
        if sign is None:
            sign = cross > 0
        elif sign != (cross > 0):
            return False
    return True


def _bound(poly):
    cx = sum(p[0] for p in poly) / len(poly)
    cz = sum(p[1] for p in poly) / len(poly)
    return (cx, cz), max(math.hypot(p[0] - cx, p[1] - cz) for p in poly)


# ---------------------------------------------------------------- 占位体
def actor_footprint(actor: dict, key: dict):
    """(xz 有向矩形, 底 y, 顶 y):按姿态取脚印(与 whitebox-renderer.js 腿 / 躯干几何同比例),不含手臂。"""
    w, h, d = (list(actor.get('size_m') or [.5, 1.7, .4]) + [.4])[:3]
    pose = key.get('pose') or 'stand'
    x0, x1, z0, z1, top = -w / 2, w / 2, -d / 2, d / 2, h
    if actor.get('kind') == 'creature':
        pass
    elif pose in ('lie', 'prone'):
        z0, z1, top = -h, 0, .35 * h
    elif pose == 'sit':
        z1, top = max(d / 2, .175 * h + .4 * d), .8 * h
    elif pose in ('stand', 'kneel', 'crouch'):
        from modules.whitebox import lean_bend, lean_hip
        bend, hip = lean_bend(key), lean_hip(pose) * h
        head = (.725 if pose != 'stand' else .9) * h - hip
        z1 = max(z1, head * math.sin(bend) + .1 * h)
        if pose == 'kneel':
            z0, top = min(z0, -.175 * h), .825 * h
        elif pose == 'crouch':
            z1, top = max(z1, .175 * h + .4 * d), .73 * h
    pos = key['position']
    return _rect(pos[0], pos[2], float(key.get('yaw') or 0), x0, x1, z0, z1), pos[1], pos[1] + top


def _hull(points):
    pts = sorted(set((round(x, 9), round(z, 9)) for x, z in points))
    if len(pts) <= 2:
        return pts
    cross = lambda o, a, b: (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def object_shape(obj: dict, center=None, oid=None, kind='scene', loose=False):
    """场景物件 / 道具按有向长方体(球、圆柱取外接长方体)处理:xz 投影凸包 + 局部坐标系,供 column() 求任意竖线上的上下沿
    (斜放的坡道、扶手按真实斜面判踩上 / 头顶,而不是整块包围盒)。loose=True(挂在手上、朝向随人)取外接球的保守范围。"""
    sx, sy, sz = obj['size_m']
    c = list(center or obj['position'])
    if loose:
        r = .5 * math.sqrt(sx * sx + sy * sy + sz * sz)
        sx = sy = sz = 2 * r
        rot = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    else:
        from modules.whitebox import _mat_mul, _rot_x, _rot_y
        a = float(obj.get('roll') or 0)
        rz = [[math.cos(a), -math.sin(a), 0], [math.sin(a), math.cos(a), 0], [0, 0, 1]]
        rot = _mat_mul(_rot_x(float(obj.get('pitch') or 0)), _mat_mul(_rot_y(float(obj.get('yaw') or 0)), rz))   # three.js Euler XYZ
    half = [sx / 2, sy / 2, sz / 2]
    corners = [[c[i] + sum(rot[i][j] * v[j] for j in range(3)) for i in range(3)]
               for v in ([x, y, z] for x in (-half[0], half[0]) for y in (-half[1], half[1]) for z in (-half[2], half[2]))]
    poly = _hull([(p[0], p[2]) for p in corners])
    return {'id': oid or obj.get('id'), 'kind': kind, 'role': obj.get('collision_role'), 'poly': poly,
            'y0': min(p[1] for p in corners), 'y1': max(p[1] for p in corners), 'bound': _bound(poly),
            'frame': (c, rot, half)}


def column(shape: dict, x: float, z: float):
    """世界竖线 (x, z) 穿过该长方体的 [下沿 y, 上沿 y];不穿过返回 None(slab 法,在局部坐标里求交)。"""
    c, rot, half = shape['frame']
    rel = [x - c[0], -c[1], z - c[2]]
    origin = [sum(rot[j][i] * rel[j] for j in range(3)) for i in range(3)]   # R^T · (p − c),p 取 y = 0
    direction = [rot[1][i] for i in range(3)]                                 # R^T · (0, 1, 0)
    lo, hi = -math.inf, math.inf
    for i in range(3):
        if abs(direction[i]) < 1e-12:
            if abs(origin[i]) > half[i] + 1e-9:
                return None
            continue
        a, b = (-half[i] - origin[i]) / direction[i], (half[i] - origin[i]) / direction[i]
        lo, hi = max(lo, min(a, b)), min(hi, max(a, b))
    return (lo, hi) if lo <= hi else None


def _nearest_on(poly, point):
    best = None
    for i in range(len(poly)):
        a, b = poly[i], poly[(i + 1) % len(poly)]
        ax, az = b[0] - a[0], b[1] - a[1]
        n = ax * ax + az * az
        u = 0 if n < 1e-12 else max(0, min(1, ((point[0] - a[0]) * ax + (point[1] - a[1]) * az) / n))
        q = (a[0] + u * ax, a[1] + u * az)
        d = math.dist(q, point)
        if best is None or d < best[0]:
            best = (d, q)
    return best[1]


def span_near(shape: dict, foot: list, centroid):
    """该物体在人物脚印范围(脚印角点与中心;不在物体上方时取物体边上离人最近的一点)的上下沿 [min 下沿, max 上沿]。"""
    cols = [col for col in (column(shape, x, z) for x, z in list(foot) + [centroid]) if col]
    if not cols:
        q = _nearest_on(shape['poly'], centroid)
        cx, cz = shape['bound'][0]
        q = (q[0] + (cx - q[0]) * 1e-3, q[1] + (cz - q[1]) * 1e-3)   # 往物体里挪 1 mm,避开边界数值误差
        col = column(shape, *q)
        cols = [col] if col else [(shape['y0'], shape['y1'])]
    return min(c[0] for c in cols), max(c[1] for c in cols)


# ---------------------------------------------------------------- collide
def _times(group, step):
    duration = float(group['duration_s'])
    n = max(1, int(math.ceil(duration / step)))
    times = {round(duration * i / n, 4) for i in range(n + 1)}
    for item in list(group.get('actors') or []) + list(group.get('extras') or []) + list(group.get('props') or []):
        times.update(round(float(k['t']), 4) for k in item.get('keyframes') or [] if 0 <= float(k['t']) <= duration)
    return sorted(times)


def _shot_at(group, t):
    cams = group.get('cameras') or []
    return next((c['shot_id'] for c in cams if t < c['start'] + c['duration_s'] - 1e-9), cams[-1]['shot_id'] if cams else None)


def _allowed(allow, a, b, t):
    for x, y, t0, t1 in allow:
        if (x in (a, '*') and y in (b, '*')) or (x in (b, '*') and y in (a, '*')):
            if t0 is None or t0 - 1e-9 <= t <= t1 + 1e-9:
                return True
    return False


def parse_allow(items) -> list:
    """`A:B` / `A:B@1.2-3.5` / `A:*` → [(a, b, t0, t1)]。"""
    out = []
    for item in items or []:
        pair, _, window = str(item).partition('@')
        a, sep, b = pair.partition(':')
        if not sep or not a or not b:
            raise ValueError(f'--allow 写成 A:B 或 A:B@t0-t1:{item}')
        t0 = t1 = None
        if window:
            lo, _, hi = window.partition('-')
            t0, t1 = float(lo), float(hi)
        out.append((a.strip(), b.strip(), t0, t1))
    return out


def collide_group(group: dict, scene: dict, *, step=STEP_S, actor_gap=ACTOR_GAP_M, obstacle_gap=OBSTACLE_GAP_M, allow=()):
    people = [a for a in list(group.get('actors') or []) + list(group.get('extras') or []) if a.get('keyframes')]
    by_id = {a['id']: a for a in people}
    mounts = {frozenset((a['id'], a['rider'])) for a in people if a.get('rider')}
    statics = []
    prop_ids = {p['id'] for p in group.get('props') or []}
    for obj in scene.get('objects') or []:
        if obj['id'] in prop_ids or obj.get('collision_role') in PASSABLE_ROLES:
            continue   # 组内道具覆盖同 id 场景物件(拿起道具时原位不留一份);装饰 / 远景 / 软物 / 水面不算障碍
        statics.append(object_shape(obj))
    hits = {}
    for t in _times(group, step):
        bodies = []
        for actor in people:
            key = sample(actor['keyframes'], t)
            if key.get('visible') is False:
                continue
            poly, y0, y1 = actor_footprint(actor, key)
            bodies.append((actor, key, poly, y0, y1, _bound(poly)))
        dynamic = []
        for prop in group.get('props') or []:
            if prop.get('material') == 'ghost':
                continue
            center, attached, visible = prop_state(prop, by_id, t)
            if not visible:
                continue
            k = sample(prop['keyframes'], t) if prop.get('keyframes') else prop
            size = [s * c for s, c in zip(prop['size_m'], k.get('scale') or [1, 1, 1])]
            shape = object_shape({'size_m': size, 'yaw': k.get('yaw', 0), 'pitch': k.get('pitch', 0), 'roll': k.get('roll', 0)},
                                 center, prop['id'], 'prop', loose=attached)   # 挂手时朝向随上身转,取外接球
            shape['holder'] = (prop.get('attach') or {}).get('actor') if attached else None
            dynamic.append(shape)
        for i, (a, ka, pa, a0, a1, ba) in enumerate(bodies):
            h = (a.get('size_m') or [.5, 1.7, .4])[1]
            pose = ka.get('pose') or 'stand'
            probes = [(ka['position'][0], ka['position'][2]), ba[0]]
            for b, kb, pb, b0, b1, bb in bodies[i + 1:]:
                if frozenset((a['id'], b['id'])) in mounts or a1 <= b0 or b1 <= a0:
                    continue
                if math.dist(ba[0], bb[0]) - ba[1] - bb[1] >= actor_gap:
                    continue
                gap, overlap = poly_gap(pa, pb)
                if gap < actor_gap and not _allowed(allow, a['id'], b['id'], t):
                    hits.setdefault((a['id'], b['id'], 'actor'), []).append((t, gap, overlap))
            for shape in statics + dynamic:
                if shape.get('holder') == a['id']:
                    continue   # 持有人与自己手里的道具
                bo = shape['bound']
                if math.dist(ba[0], bo[0]) - ba[1] - bo[1] >= obstacle_gap:
                    continue
                o0, o1 = span_near(shape, pa, ba[0])
                if o0 >= a1 or o1 <= a0 + (STEP_TOL_M if pose not in LOW_POSES else 0):
                    continue   # 头顶以上 / 脚下可踩的地面、台阶、路缘、坡面
                if pose in LOW_POSES and o1 <= a0 + SEAT_FRAC * h and any(_inside(p, shape['poly']) for p in probes):
                    continue   # 身下的座椅 / 床 / 垫子
                gap, overlap = poly_gap(pa, shape['poly'])
                if gap < obstacle_gap and not _allowed(allow, a['id'], shape['id'], t):
                    hits.setdefault((a['id'], shape['id'], shape['kind'] if shape['kind'] == 'prop' else 'scene'), []).append(
                        (t, gap, overlap, shape.get('role')))
    rows = []
    for (a, b, kind), samples in hits.items():
        spans, current = [], [samples[0]]
        for item in samples[1:]:
            if item[0] - current[-1][0] <= step * 1.5 + 1e-6:
                current.append(item)
            else:
                spans.append(current)
                current = [item]
        spans.append(current)
        for span in spans:
            worst = min(span, key=lambda x: x[1])
            role = next((x[3] for x in span if len(x) > 3 and x[3]), None)
            rows.append({'a': a, 'b': b, 'kind': kind, **({'role': role} if role else {}), 'shot_id': _shot_at(group, worst[0]),
                         't_range_s': [round(span[0][0], 3), round(span[-1][0], 3)],
                         'min_clearance_m': round(worst[1], 3), 't_min_s': round(worst[0], 3),
                         'overlap': any(x[2] for x in span),
                         'required_m': actor_gap if kind == 'actor' else obstacle_gap})
    rows.sort(key=lambda r: (r['t_range_s'][0], r['a'], r['b']))
    return rows


# ---------------------------------------------------------------- facing
def _wrap_deg(a):
    return abs((math.degrees(a) + 180) % 360 - 180)


def facing_windows(base: Path, ep: str, group: dict, pairs=()):
    """声明的「谁在何时面向谁」:计划 facing_targets、逐镜 blocking body_facing_target_id、组动线 facing_target_id、--pair。"""
    gid = group['group_id']
    out = []
    plan = read(base / 'directing' / ep / 'whitebox_plans' / f'{gid}.json', {}) or {}
    for row in plan.get('facing_targets') or []:
        if isinstance(row, dict) and row.get('actor_id') and row.get('target_id'):
            out.append((row['actor_id'], row['target_id'], float(row.get('start', 0)),
                        float(row.get('end', group['duration_s'])), 'plan.facing_targets'))
    for cam in group.get('cameras') or []:
        doc = read(base / 'directing' / ep / 'shots' / cam['shot_id'] / 'blocking.json', {}) or {}
        for entry in (doc.get('characters') or []) + (doc.get('creatures') or []):
            if isinstance(entry, dict) and entry.get('id') and entry.get('body_facing_target_id'):
                out.append((entry['id'], entry['body_facing_target_id'], float(cam['start']),
                            float(cam['start'] + cam['duration_s']), f"blocking:{cam['shot_id']}"))
    source = read(base / 'directing' / ep / 'shot_list.json', {}) or {}
    raw = next((g for g in source.get('generation_groups') or [] if g.get('group_id') == gid), {})
    for route in (raw.get('blocking_map') or {}).get('characters') or []:
        if isinstance(route, dict) and route.get('id') and route.get('facing_target_id'):
            out.append((route['id'], route['facing_target_id'], 0.0, float(group['duration_s']), 'blocking_map'))
    for item in pairs or []:
        pair, _, shot = str(item).partition('@')
        a, sep, b = pair.partition(':')
        if not sep:
            raise ValueError(f'--pair 写成 A:B 或 A:B@<镜号>:{item}')
        cams = [c for c in group.get('cameras') or [] if not shot or c['shot_id'] == shot]
        if shot and not cams:
            continue
        t0 = min(c['start'] for c in cams) if cams else 0.0
        t1 = max(c['start'] + c['duration_s'] for c in cams) if cams else float(group['duration_s'])
        out.append((a.strip(), b.strip(), float(t0), float(t1), 'pair'))
    return out


def facing_group(base: Path, ep: str, group: dict, render: dict, *, pairs=(), tol=FACING_TOL_DEG, step=STEP_S):
    from modules.whitebox_refs import facing_rows
    by_id = {a['id']: a for a in list(group.get('actors') or []) + list(group.get('extras') or [])}
    rows = []
    for actor_id, target_id, t0, t1, source in facing_windows(base, ep, group, pairs):
        row = {'actor': actor_id, 'target': target_id, 'source': source, 't_range_s': [round(t0, 3), round(t1, 3)]}
        if actor_id not in by_id or target_id not in by_id:
            rows.append({**row, 'error': 'actor 或 target 不在本组白模人物里'})
            continue
        n = max(1, int(math.ceil((t1 - t0) / step)))
        body, head = [], []
        for i in range(n + 1):
            t = t0 + (t1 - t0) * i / n
            a, b = sample(by_id[actor_id]['keyframes'], t), sample(by_id[target_id]['keyframes'], t)
            if a.get('visible') is False or b.get('visible') is False:
                continue
            want = math.atan2(b['position'][0] - a['position'][0], b['position'][2] - a['position'][2])
            yaw = float(a.get('yaw') or 0) + float(a.get('torso_yaw') or 0)
            if (a.get('pose') or 'stand') in ('lie', 'prone'):
                yaw += math.pi   # 卧 / 趴时身体沿局部 −Z 躺下,头指向 yaw 的反方向(whitebox-renderer.js body 绕 X 转 −π/2)
            body.append((_wrap_deg(want - yaw), t))
            head.append((_wrap_deg(want - yaw - float(a.get('head_yaw') or 0)), t))
        if not body:
            rows.append({**row, 'error': '窗口内两人从未同时在场'})
            continue
        worst = max(body)
        rows.append({**row, 'body_max_deg': round(worst[0], 2), 'body_mean_deg': round(sum(x for x, _ in body) / len(body), 2),
                     't_max_s': round(worst[1], 3), 'head_max_deg': round(max(head)[0], 2),
                     'within_tol_pct': round(100 * sum(1 for x, _ in body if x <= tol + 1e-9) / len(body), 1),
                     'ok': worst[0] <= tol + 1e-9})
    camera = [{'shot_id': r['shot_id'], 'actors': {a['id']: a['phrase'] for a in r['actors']}}
              for r in facing_rows(group, None, render)]
    return rows, camera


# ---------------------------------------------------------------- frame(无头渲染)
STATIC = Path(__file__).resolve().parents[1] / 'apps/web/static'
PROBE_SCALE = .5


def frame_probe(base: Path, episode: dict, gid: str, samples: list, *, objects=(), scale=PROBE_SCALE):
    """samples: [(shot_id, t_group_s)]。返回每个时刻的 {shot_id, t, sky_pct, objects{id: …}, scene_top[[id, pct]…]}。"""
    from modules.whitebox_export import _EXPORT_LOCK
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError('缺少 Playwright,请安装项目依赖并运行 python -m playwright install chromium。') from e
    group = next((g for g in episode['groups'] if g['group_id'] == gid), None)
    if group is None:
        raise ValueError(f'{gid}: group unavailable; check compilation errors')
    fmt = episode.get('render') or render_format(read(base / 'settings.json', {}))
    width = max(64, int(round(fmt['width'] * scale / 2)) * 2)
    height = max(36, int(round(fmt['height'] * scale / 2)) * 2)
    payload = {'episode': episode, 'width': width, 'height': height}
    out = []
    with _EXPORT_LOCK, sync_playwright() as p:
        kwargs = {'headless': True, 'args': ['--enable-webgl', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
                                             '--allow-file-access-from-files']}
        if os.environ.get('VIDEOAGENTS_CHROMIUM'):
            kwargs['executable_path'] = os.environ['VIDEOAGENTS_CHROMIUM']
        browser = p.chromium.launch(**kwargs)
        try:
            page = browser.new_page(viewport={'width': width, 'height': height})
            page.add_init_script('window.whiteboxExportData = ' + json.dumps(payload, ensure_ascii=False) + ';')
            page.goto((STATIC / 'whitebox-probe.html').as_uri())
            page.wait_for_function('window.whiteboxReady === true', timeout=60000)
            page.evaluate('(gid)=>window.whiteboxProbe.load(gid)', component(gid))
            for shot_id, t in samples:
                row = page.evaluate('([t,ids])=>window.whiteboxProbe.measure(t,ids)', [t, list(objects)])
                out.append({'shot_id': shot_id, 't': round(t, 3), **row})
        finally:
            browser.close()
    return {'group_id': gid, 'width': width, 'height': height, 'samples': out}


def default_frame_samples(base: Path, ep: str, group: dict, shots=None, times=None):
    """缺省采样 = 自检静帧同一套时刻(每镜首 / 中 / 尾、机位关键帧 + evt 剧情动作时刻);--t 给组内秒时按所在镜归属。"""
    from modules.whitebox_stills import event_times, sample_times
    cams = [c for c in group.get('cameras') or [] if not shots or c['shot_id'] in shots]
    if times:
        return [(_shot_at(group, t), float(t)) for t in sorted(times) if not shots or _shot_at(group, t) in shots]
    rows = {s.get('shot_id'): s for s in (read(base / 'directing' / ep / 'shot_list.json', {}) or {}).get('shots', [])}
    out = []
    for cam in cams:
        base_t = sample_times(cam)
        out += [(cam['shot_id'], t) for t in sorted(base_t + event_times(cam, group, rows.get(cam['shot_id']), base_t))]
    return out
