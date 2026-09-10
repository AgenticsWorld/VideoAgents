"""分镜背景图(shot plate,2026-09-09):按白模镜首/镜尾机位出「空场景」实拍感背景图,作组视频生成的参考图。

流程位置(workflow.yaml):p6-whitebox(白模调度,只编译不导出)→ g6w 用户签字「H3W-白模确认」→ p6-whitebox-export
(同一白模调度 Agent 导出 camera.mp4)→ p6-shot-plates(本模块)→ g6「H3A-分镜确认」→ p7-prompt(sync 把背景图接进组 refs)。

数据:
  - 库(按机位指纹,不按分镜):assets/concepts/scenes/<sid>/plates/index.json + <key>.png + <key>.json + <key>.whitebox.jpg
      key = <lighting_scheme_id>_b<朝向°>_h<机高档>_x<机位x>_z<机位z>_f<fov°>,由首个生成它的机位命名;
      复用按容差:同场景、同光照方案、同机高档、朝向 ±20°、机位 6 m 内、fov ±15°;同轴更宽的库图按 fov 比例中心裁切复用
      (裁切文件 <key>.crop_f<fov>.png),反打(朝向差 180°)/换时段/跨机高档一律不复用。
  - 集索引(分镜只记指纹):directing/<ep>/shot_plates.json —— 每镜 plates[]{role:start|end, key, file, reuse:new|library|crop, camera}
  - 运镜分档(按 camera.json movement + 白模机位几何):静态/推拉变焦/摇俯仰 = 只出镜首一张;横移跟拍 = 位移 < 机位到主体距离的
    10% 按静态、否则镜首 + 镜尾两张;复杂轨迹 = 镜首 + 镜尾。镜尾图以镜尾白模帧为第一参考图、镜首成图为第二参考图、同 seed。
  - 出图:refs = 白模干净帧(隐藏人物)→ 场景俯视图 [→ 镜首成图];提示词 = 空场景声明 + 场景描述(architecture)+ 光照方案
    prompt_fragment_en + 机位事实(罗盘朝向/画左画右/机高/焦距/水平线)+ 画内自左向右清单 + 风格串;分辨率长边 1920 按项目画幅;
    渠道 = 控制台默认图像模型(modules.genmedia.generate_image)。
  - 接线(shot_plate_bound,code/sync_shot_plates.py):组 prompt refs 在角色/生物 sheet 之后挂本组各镜背景图(俯视图/九宫格
    不再进 refs,残留自动剔除并重排 [Image N]),`Shot 1:` 前固定段 `Shot plates:` 逐镜写明「[Image N] = Shot k 起点/终点背景图」;
    两张图都走 refs,不走首尾帧模式(多镜组里首尾帧与参考图互斥)。
"""
from __future__ import annotations

import base64
import copy
import datetime as dt
import json
import math
import os
import re
from pathlib import Path

from modules.whitebox import component, read, render_format

SCHEMA_LIBRARY = 'shot_plate_library.v1'
SCHEMA_EPISODE = 'shot_plates.v1'
PLATES_DIR = 'plates'
BLOCK_KEY = 'Shot plates:'
GC_KEY = 'Global constraints:'
TOLERANCE = {'bearing_deg': 20.0, 'distance_m': 6.0, 'fov_deg': 15.0}
MIN_CROP_FRACTION = 0.33          # 裁切后短边不低于原图 1/3(1080p → 360px)
TRACK_STATIC_RATIO = 0.10         # 横移/跟拍位移 < 机位到主体距离的 10% 按静态

COMPASS16 = ['north', 'north-north-east', 'north-east', 'east-north-east', 'east', 'east-south-east',
             'south-east', 'south-south-east', 'south', 'south-south-west', 'south-west', 'west-south-west',
             'west', 'west-north-west', 'north-west', 'north-north-west']
CARD = {'north': (0, 1), 'east': (1, 0), 'south': (0, -1), 'west': (-1, 0)}
SIZE_WORDS = {'ECU': 'extreme close-up', 'CU': 'close-up', 'MCU': 'medium close-up', 'MS': 'medium',
              'MLS': 'medium long', 'FS': 'full', 'WS': 'wide', 'EWS': 'extreme wide'}
SYNONYMS = {'canopy': 'tree', 'crown': 'tree', 'trunk': 'tree', 'apartments': 'apartment', 'buildings': 'building'}
NEGATIVE_EXTRA = ('people, person, human figure, silhouette, crowd, pedestrian, grey boxes, untextured 3D blocks, wireframe, '
                  "top-down view, bird's-eye view, map, tiled grid, split screen, collage, contact sheet")


# ---------------------------------------------------------------- vector helpers
def sub(a, b): return [x - y for x, y in zip(a, b)]
def dot(a, b): return sum(x * y for x, y in zip(a, b))
def cross(a, b): return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]
def norm(a):
    n = math.sqrt(dot(a, a)) or 1e-9
    return [x / n for x in a]


def angle_diff(a, b):
    return abs((a - b + 180) % 360 - 180)


# ---------------------------------------------------------------- orientation / compass
def orientation_axes(layout):
    """布局图方位 → 白模 +x/+z 轴的罗盘向量(E,N)与四边说明文字。缺省 上北下南左西右东。"""
    o = layout.get('orientation') or {}
    def word(key, default):
        text = str(o.get(key) or '').strip().lower()
        for w in CARD:
            if text.startswith(w):
                return w
        return default
    right, bottom = word('right_of_map', 'east'), word('bottom_of_map', 'south')
    texts = {word(k, d): str(o.get(k) or '') for k, d in
             (('top_of_map', 'north'), ('bottom_of_map', 'south'), ('left_of_map', 'west'), ('right_of_map', 'east'))}
    return CARD[right], CARD[bottom], texts


def bearing_deg(dx, dz, ex, ez):
    e = dx*ex[0] + dz*ez[0]; n = dx*ex[1] + dz*ez[1]
    return (math.degrees(math.atan2(e, n)) + 360) % 360


def compass(deg): return COMPASS16[int((deg + 11.25) // 22.5) % 16]
def cardinal(deg): return ['north', 'east', 'south', 'west'][int((deg + 45) // 90) % 4]


def strip_compass(text):
    for sep in (' — ', ' - ', '—', ':'):
        if sep in text:
            return text.split(sep, 1)[1].strip()
    return text.strip()


def height_class(h):
    """机高档:0 贴地(<0.5m)、1 低机位(<1.2m)、2 人眼(<2.0m)、3 高机位。"""
    return 0 if h < .5 else 1 if h < 1.2 else 2 if h < 2.0 else 3


# ---------------------------------------------------------------- camera facts / projection
def camera_facts(key, fmt, ex, ez, texts):
    pos, tgt, fov = key['position'], key['target'], key['fov']
    d = sub(tgt, pos); horiz = math.hypot(d[0], d[2]) or 1e-6
    b = bearing_deg(d[0], d[2], ex, ez)
    pitch = math.degrees(math.atan2(d[1], horiz))
    aspect = fmt['width'] / fmt['height']
    hfov = 2*math.degrees(math.atan(math.tan(math.radians(fov/2))*aspect))
    mm = 24 / (2*math.tan(math.radians(fov/2)))
    h = pos[1]
    height_word = ('lens almost on the ground, ground-level view' if h < .5 else
                   'low angle below eye level' if h < 1.2 else
                   'eye level' if h < 2.0 else 'raised high vantage point')
    tilt_word = ('level horizon, no tilt' if abs(pitch) < 3 else
                 f'tilted {"down" if pitch < 0 else "up"} about {abs(round(pitch))} degrees')
    f = norm(d); r = norm(cross(f, [0, 1, 0])); u = cross(r, f)
    fh = norm([f[0], 0, f[2]]); far = [pos[0]+fh[0]*1000, pos[1], pos[2]+fh[2]*1000]
    dd = sub(far, pos); z = dot(dd, f); ndc_y = dot(dd, u)/(z*math.tan(math.radians(fov/2))) if z > 0 else 0
    return {'position': [round(x, 3) for x in pos], 'target': [round(x, 3) for x in tgt], 'fov_v_deg': round(fov, 2),
            'fov_h_deg': round(hfov, 1), 'lens_mm_equiv': round(mm, 1), 'height_m': round(h, 2), 'height_class': height_class(h),
            'pitch_deg': round(pitch, 1), 'bearing_deg': round(b, 1), 'facing': compass(b), 'facing_cardinal': cardinal(b),
            'frame_left': compass((b-90) % 360), 'frame_right': compass((b+90) % 360), 'behind': compass((b+180) % 360),
            'facing_desc': strip_compass(texts.get(cardinal(b), '')),
            'behind_desc': strip_compass(texts.get(cardinal((b+180) % 360), '')),
            'left_desc': strip_compass(texts.get(cardinal((b-90) % 360), '')),
            'right_desc': strip_compass(texts.get(cardinal((b+90) % 360), '')),
            'height_word': height_word, 'tilt_word': tilt_word,
            'horizon_pct_from_top': round(max(0, min(100, (1-ndc_y)/2*100))),
            'subject_distance_m': round(math.dist(pos, tgt), 2)}


def projector(key, fmt):
    pos, tgt = key['position'], key['target']
    f = norm(sub(tgt, pos)); r = norm(cross(f, [0, 1, 0])); u = cross(r, f)
    tv = math.tan(math.radians(key['fov']/2)); th = tv*fmt['width']/fmt['height']
    def project(p):
        d = sub(p, pos); z = dot(d, f)
        if z <= .05:
            return None
        return dot(d, r)/(z*th), dot(d, u)/(z*tv), z
    return project


def box_samples(obj):
    sx, sy, sz = obj['size_m']; cx, cy, cz = obj['position']; yaw = obj.get('yaw', 0) or 0
    c, s = math.cos(yaw), math.sin(yaw); pts = []
    nx, nz = (max(2, min(41, math.ceil(v/.5)+1)) for v in (sx, sz))
    for i in range(nx):
        for j in range(nz):
            ox, oz = (i/(nx-1)-.5)*sx, (j/(nz-1)-.5)*sz
            wx, wz = cx + ox*c + oz*s, cz - ox*s + oz*c
            for k in (0, .5, 1):
                pts.append([wx, cy - sy/2 + sy*k, wz])
    return pts


def base_name(obj_id):
    name = re.sub(r'\d+$', '', obj_id).strip('_')
    name = re.sub(r'_(?:[a-z]|left|right|north|south|east|west|arm|top)$', '', name)
    return SYNONYMS.get(name, name)


def resolve_landmark(base, landmarks):
    if base in landmarks:
        return base
    for lid in landmarks:
        for tok in lid.split('_'):
            if len(tok) >= 3 and len(base) >= 3 and (tok.startswith(base) or base.startswith(tok)):
                return lid
    return None


def x_word(xmin, xmax, xmean):
    if xmin < -.8 and xmax > .8:
        return 'spanning the full width of the frame'
    if xmin < -.8 and xmax > .2:
        return 'from the left edge across to the centre-right'
    if xmax > .8 and xmin < -.2:
        return 'from the centre-left across to the right edge'
    x = xmean
    return ('at the far left edge' if x < -.66 else 'on the left' if x < -.2 else 'in the centre'
            if x <= .2 else 'on the right' if x <= .66 else 'at the far right edge')


def y_word(ymin, ymax):
    """竖向占幅:ndc_y → 自顶向下百分比,如 'filling the frame vertically from the top edge down to 65% of the height'。"""
    top = round((1 - ymax) / 2 * 100); bottom = round((1 - ymin) / 2 * 100)
    a = 'the top edge' if top <= 3 else f'{top}% of the height'
    b = 'the bottom edge' if bottom >= 97 else f'{bottom}% of the height'
    return f'filling the frame vertically from {a} down to {b}'


def dist_word(z):
    return 'in the near foreground' if z < 4 else 'in the middle distance' if z < 20 else 'far in the distance'


def inventory(scene, layout, key, fmt):
    """画内自左向右清单:白模几何按基名/地标归并 + 未被几何覆盖的布局点地标。"""
    project = projector(key, fmt)
    landmarks = {lm['id']: lm for lm in layout.get('landmarks', []) if 'xy' in lm}
    names = {lid: lm.get('name_en') or lm.get('name') or lid for lid, lm in landmarks.items()}
    dims = scene['dimensions_m']
    merged = {}
    for obj in scene.get('objects', []):
        hits = [p for p in (project(p) for p in box_samples(obj)) if p and abs(p[0]) <= 1.05 and abs(p[1]) <= 1.05]
        if not hits:
            continue
        b = base_name(obj['id']); lid = resolve_landmark(b, landmarks)
        entry = merged.setdefault(lid or b, {'id': lid or b, 'name': names.get(lid, b.replace('_', ' ')), 'landmark': lid,
                                             'objects': [], 'xs': [], 'xmin': 1, 'xmax': -1, 'ymin': 1, 'ymax': -1, 'z': 1e9})
        xs = [h[0] for h in hits]; ys = [h[1] for h in hits]
        entry['objects'].append(obj['id']); entry['xs'].extend(xs)
        entry['xmin'] = min(entry['xmin'], max(-1, min(xs))); entry['xmax'] = max(entry['xmax'], min(1, max(xs)))
        entry['ymin'] = min(entry['ymin'], max(-1, min(ys))); entry['ymax'] = max(entry['ymax'], min(1, max(ys)))
        entry['z'] = min(entry['z'], min(h[2] for h in hits))
    items = []
    view = norm([key['target'][0]-key['position'][0], 0, key['target'][2]-key['position'][2]])
    axis_of = {}
    for obj in scene.get('objects', []):
        sx, _, sz = obj['size_m']
        if max(sx, sz) >= 8 and max(sx, sz) >= 3*min(sx, sz):      # 细长地面物(马路/人行道/绿化带/围墙)
            yaw = obj.get('yaw', 0) or 0
            ax = [math.cos(yaw), 0, -math.sin(yaw)] if sx >= sz else [math.sin(yaw), 0, math.cos(yaw)]
            axis_of[obj['id']] = abs(math.degrees(math.acos(max(-1, min(1, abs(dot(ax, view)))))))
    for entry in merged.values():
        entry['x'] = sum(entry['xs'])/len(entry['xs']); entry['z'] = round(entry['z'], 1); entry.pop('xs')
        angles = [axis_of[o] for o in entry['objects'] if o in axis_of]
        if angles:
            a = min(angles)
            entry['orientation'] = ('crosses the frame from side to side, seen broadside — it does not recede into the distance' if a > 60
                                    else 'runs away from the camera into the depth of the frame' if a < 30
                                    else 'runs diagonally across the frame')
        items.append(entry)
    covered = {it['landmark'] for it in items if it['landmark']}
    for lid, lm in landmarks.items():
        if lid in covered or lm.get('kind') == 'space':
            continue
        p = project([(lm['xy'][0]-.5)*dims[0], 1.0, (lm['xy'][1]-.5)*dims[2]])
        if not p or abs(p[0]) > 1.05:
            continue
        items.append({'id': lid, 'name': names[lid], 'landmark': lid, 'objects': [], 'x': p[0], 'xmin': p[0], 'xmax': p[0],
                      'ymin': p[1], 'ymax': p[1], 'z': round(p[2], 1), 'direction': lm.get('kind') == 'direction'})
    items.sort(key=lambda it: it['x'])
    in_frame_ids = {it['landmark'] for it in items if it['landmark']}
    out_of_frame = []
    for lid, lm in landmarks.items():
        if lid in in_frame_ids or lm.get('kind') in ('space', 'direction', 'ground', 'path', 'vegetation', 'boundary'):
            continue   # 只列点状地标(门/建筑/灯杆/道具);面状的绿化带/围墙靠几何清单判断,避免与画内清单自相矛盾
        p = project([(lm['xy'][0]-.5)*dims[0], 1.0, (lm['xy'][1]-.5)*dims[2]])
        if not p or abs(p[0]) > 1.05:
            out_of_frame.append(names[lid])
    phrases = []
    for it in items:
        if it.get('direction'):
            if abs(it['x']) > .6:
                continue   # 视轴不沿马路时,路端地标只会落在画幅边缘,写成「路向远处延伸」反而诱导画出纵深马路
            phrases.append(f"the road runs away into the distance toward {it['name']} {x_word(it['xmin'], it['xmax'], it['x'])}")
        else:
            count = len({re.sub(r'\D', '', o) or o for o in it['objects']})
            plural = f" ({count} of them)" if count > 1 else ''
            phrases.append(f"{it['name']}{plural} {x_word(it['xmin'], it['xmax'], it['x'])}, {dist_word(it['z'])} (nearest {it['z']} m)"
                           + (f", {it['orientation']}" if it.get('orientation') else '')
                           + (f", {y_word(it['ymin'], it['ymax'])}" if it.get('objects') else ''))
    return items, phrases, out_of_frame


def standing_on(scene, layout, key):
    px, pz = key['position'][0], key['position'][2]
    names = {lm['id']: lm.get('name_en') or lm.get('name') or lm['id'] for lm in layout.get('landmarks', [])}
    best = None
    for obj in scene.get('objects', []):
        sx, _, sz = obj['size_m']; cx, _, cz = obj['position']; yaw = obj.get('yaw', 0) or 0
        c, s = math.cos(-yaw), math.sin(-yaw); dx, dz = px-cx, pz-cz
        lx, lz = dx*c + dz*s, -dx*s + dz*c
        if abs(lx) <= sx/2 and abs(lz) <= sz/2 and (best is None or sx*sz < best[1]):
            best = (names.get(obj['id'], obj['id'].replace('_', ' ')), sx*sz)
    if best:
        return f"on {best[0]}"
    dims = scene['dimensions_m']; near = None
    for lm in layout.get('landmarks', []):
        if 'xy' not in lm or lm.get('kind') in ('direction', 'space'):
            continue
        d = math.hypot((lm['xy'][0]-.5)*dims[0]-px, (lm['xy'][1]-.5)*dims[2]-pz)
        if near is None or d < near[1]:
            near = (names[lm['id']], d)
    return f"about {round(near[1], 1)} m from {near[0]}" if near else 'inside the location'


def sun_relative(sun_compass, cam_bearing):
    if sun_compass.lower() not in COMPASS16:
        raise ValueError(f'sun 须为 16 向罗盘词之一,如 west / north-west;收到 {sun_compass!r}')
    delta = (COMPASS16.index(sun_compass.lower())*22.5 - cam_bearing + 360) % 360
    where = ('ahead of the camera, the picture is shot into the low sun' if delta < 45 or delta >= 315 else
             'off to the right of frame' if delta < 135 else 'behind the camera' if delta < 225 else 'off to the left of frame')
    shadows = ('toward the camera' if delta < 45 or delta >= 315 else 'toward frame left' if delta < 135 else
               'away from the camera into the depth of the frame' if delta < 225 else 'toward frame right')
    return {'compass': sun_compass.lower(), 'relative': where, 'shadows': shadows}


# ---------------------------------------------------------------- movement tiers
def movement_category(movement: str, a: dict, b: dict) -> str:
    """static | push_pull | pan_tilt | track | complex;命名优先,未知命名按白模机位几何判。"""
    m = (movement or 'static').lower()
    if any(k in m for k in ('push', 'pull', 'dolly', 'zoom')):
        return 'push_pull'
    if any(k in m for k in ('pan', 'tilt')):
        return 'pan_tilt'
    if any(k in m for k in ('track', 'truck', 'follow', 'lateral', 'slide')):
        return 'track'
    if m in ('static', 'fixed', 'locked', 'none') or 'handheld' in m or 'micro' in m:
        return 'static'
    if any(k in m for k in ('crane', 'orbit', 'rise', 'arc', 'boom', 'jib')):
        return 'complex'
    disp = math.dist(a['position'], b['position'])
    da, db = sub(a['target'], a['position']), sub(b['target'], b['position'])
    turn = angle_diff(math.degrees(math.atan2(da[0], da[2])), math.degrees(math.atan2(db[0], db[2])))
    if disp < .05 and turn < 1 and abs(a['fov']-b['fov']) < .5:
        return 'static'
    if disp < .05:
        return 'pan_tilt'
    return 'complex'


def plate_roles(cam: dict) -> dict:
    """本镜要出几张背景图:{'category', 'roles': ['start'] | ['start','end'], 'reason'}。"""
    kf = cam['keyframes']; a, b = kf[0], kf[-1]
    cat = movement_category(cam.get('movement'), a, b)
    disp = math.dist(a['position'], b['position'])
    subject = math.dist(a['position'], a['target']) or 1e-6
    if cat in ('static', 'push_pull', 'pan_tilt'):
        return {'category': cat, 'roles': ['start'], 'displacement_m': round(disp, 2),
                'reason': {'static': '固定机位', 'push_pull': '推拉/变焦同一视轴,起止共用镜首图',
                           'pan_tilt': '摇/俯仰机位不动,起止共用镜首图'}[cat]}
    if cat == 'track':
        if disp < TRACK_STATIC_RATIO * subject:
            return {'category': cat, 'roles': ['start'], 'displacement_m': round(disp, 2),
                    'reason': f'横移/跟拍位移 {disp:.2f} m < 机位到主体距离 {subject:.1f} m 的 {int(TRACK_STATIC_RATIO*100)}%,按静态处理'}
        return {'category': cat, 'roles': ['start', 'end'], 'displacement_m': round(disp, 2),
                'reason': f'横移/跟拍位移 {disp:.2f} m ≥ 机位到主体距离 {subject:.1f} m 的 {int(TRACK_STATIC_RATIO*100)}%,出镜首 + 镜尾'}
    if disp < .05 and math.dist(a['target'], b['target']) < .05 and abs(a['fov']-b['fov']) < .5:
        return {'category': cat, 'roles': ['start'], 'displacement_m': round(disp, 2), 'reason': '复杂轨迹但起止机位一致'}
    return {'category': cat, 'roles': ['start', 'end'], 'displacement_m': round(disp, 2), 'reason': '复杂轨迹,出镜首 + 镜尾'}


# ---------------------------------------------------------------- library
def library_dir(base: Path, sid: str) -> Path:
    return base/'assets/concepts/scenes'/component(sid)/PLATES_DIR


def load_library(base: Path, sid: str) -> dict:
    lib = read(library_dir(base, sid)/'index.json', None)
    if not isinstance(lib, dict) or lib.get('schema_version') != SCHEMA_LIBRARY:
        lib = {'schema_version': SCHEMA_LIBRARY, 'scene_id': sid, 'plates': []}
    lib['plates'] = [p for p in lib.get('plates', []) if isinstance(p, dict) and p.get('key')]
    return lib


def save_library(base: Path, sid: str, lib: dict):
    d = library_dir(base, sid); d.mkdir(parents=True, exist_ok=True)
    tmp = d/'index.json.tmp'
    tmp.write_text(json.dumps(lib, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    os.replace(tmp, d/'index.json')


def plate_key(scheme: str, facts: dict) -> str:
    scheme = re.sub(r'[^A-Za-z0-9_-]+', '-', scheme or 'nolight')
    p = facts['position']
    return f"{scheme}_b{int(round(facts['bearing_deg'])):03d}_h{facts['height_class']}_x{int(round(p[0]))}_z{int(round(p[2]))}_f{int(round(facts['fov_v_deg']))}"


def find_reusable(lib: dict, scheme: str, facts: dict, base: Path, sid: str, require_file: bool = True):
    """按容差找可复用库图:返回 (entry, mode) —— mode ∈ {'library','crop'};无则 (None, None)。"""
    best = None
    for e in lib.get('plates', []):
        c = e.get('camera') or {}
        if e.get('lighting_scheme_id') != scheme or c.get('height_class') != facts['height_class']:
            continue
        if require_file and not e.get('pending') and not (base/e['file']).is_file():
            continue
        ang = angle_diff(c.get('bearing_deg', 0), facts['bearing_deg'])
        dist = math.hypot(c['position'][0]-facts['position'][0], c['position'][2]-facts['position'][2])
        if ang > TOLERANCE['bearing_deg'] or dist > TOLERANCE['distance_m']:
            continue
        dfov = c.get('fov_v_deg', 0) - facts['fov_v_deg']
        if abs(dfov) <= TOLERANCE['fov_deg']:
            mode = 'library'
        elif dfov > 0:
            frac = math.tan(math.radians(facts['fov_v_deg']/2))/math.tan(math.radians(c['fov_v_deg']/2))
            if frac < MIN_CROP_FRACTION:
                continue
            mode = 'crop'
        else:
            continue
        score = ang + 2*dist + abs(dfov)/5 + (10 if mode == 'crop' else 0)
        if best is None or score < best[0]:
            best = (score, e, mode)
    return (best[1], best[2]) if best else (None, None)


def crop_plate(base: Path, entry: dict, fov: float) -> str:
    """同轴更宽的库图按 fov 比例中心裁切 → plates/<key>.crop_f<fov>.png(已存在则复用)。"""
    from PIL import Image
    src = base/entry['file']
    out_rel = str(Path(entry['file']).with_name(f"{entry['key']}.crop_f{int(round(fov))}.png"))
    out = base/out_rel
    if out.is_file() and out.stat().st_mtime >= src.stat().st_mtime:
        return out_rel
    frac = math.tan(math.radians(fov/2))/math.tan(math.radians(entry['camera']['fov_v_deg']/2))
    im = Image.open(src); w, h = im.size
    cw, ch = max(2, int(round(w*frac))), max(2, int(round(h*frac)))
    x0, y0 = (w-cw)//2, (h-ch)//2
    im.crop((x0, y0, x0+cw, y0+ch)).save(out)
    return out_rel


# ---------------------------------------------------------------- whitebox clean frames
def render_clean_frames(base: Path, episode: dict, requests: list, width: int, height: int):
    """一次浏览器会话渲多帧干净白模(隐藏人物/群演,保留几何与道具)。requests=[{'group_id','t','output'(Path)}]。"""
    from modules.whitebox_export import STATIC
    from playwright.sync_api import sync_playwright
    by_group = {}
    for r in requests:
        by_group.setdefault(r['group_id'], []).append(r)
    groups = []
    for gid in by_group:
        g = copy.deepcopy(next(x for x in episode['groups'] if x['group_id'] == gid))
        g['actors'] = []; g['extras'] = []
        for prop in g.get('props', []):
            prop.pop('projection_screen', None)
        groups.append(g)
    scenes = {g['scene_id']: episode['scenes'][g['scene_id']] for g in groups}
    payload = {'episode': {'scenes': scenes, 'groups': groups}, 'width': width, 'height': height}
    kwargs = {'headless': True, 'args': ['--enable-webgl', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
                                          '--allow-file-access-from-files']}
    if os.environ.get('VIDEOAGENTS_CHROMIUM'):
        kwargs['executable_path'] = os.environ['VIDEOAGENTS_CHROMIUM']
    with sync_playwright() as p:
        browser = p.chromium.launch(**kwargs)
        try:
            page = browser.new_page(viewport={'width': width, 'height': height})
            page.add_init_script('window.whiteboxExportData = ' + json.dumps(payload, ensure_ascii=False) + ';')
            page.goto((STATIC/'whitebox-export.html').as_uri())
            page.wait_for_function('window.whiteboxReady === true', timeout=60000)
            for gid, reqs in by_group.items():
                page.evaluate('(gid)=>window.whiteboxExport.load(gid)', gid)
                for r in reqs:
                    frame = page.evaluate('(t)=>window.whiteboxExport.frame(t)', r['t'])
                    Path(r['output']).parent.mkdir(parents=True, exist_ok=True)
                    Path(r['output']).write_bytes(base64.b64decode(frame))
        finally:
            browser.close()


# ---------------------------------------------------------------- prompt
def lighting_fragment(base: Path, sid: str, scheme_id: str) -> str:
    doc = read(base/'bible/scenes'/sid/'lighting.json', {}) or {}
    for s in doc.get('schemes', []):
        if s.get('scheme_id') == scheme_id or s.get('id') == scheme_id:
            return s.get('prompt_fragment_en') or ''
    return ''


def scene_description(base: Path, sid: str) -> tuple[str, str]:
    """(场景描述, 场景负面) —— 取 architecture.json 的 form/arch_style/scale/materials/details/negative。"""
    arch = read(base/'bible/scenes'/sid/'architecture.json', {}) or {}
    def flat(v):
        if isinstance(v, str):
            return v.strip()
        if isinstance(v, list):
            return '; '.join(flat(x) for x in v if flat(x))
        if isinstance(v, dict):
            return '; '.join(flat(x) for x in v.values() if flat(x))
        return ''
    parts = [flat(arch.get(k)) for k in ('form', 'arch_style', 'era_region', 'scale', 'materials', 'details')]
    desc = ' '.join(p.rstrip('.。;') + '.' for p in parts if p)
    return desc[:1200], flat(arch.get('negative'))[:600]


def build_prompt(facts, phrases, shot, group, scene, layout, style, lighting, desc, role, sun=None, out_of_frame=None, sibling=False):
    size = SIZE_WORDS.get(shot.get('size_code'), 'wide')
    name = re.sub(r'[(（].*?[)）]', '', layout.get('scene_name_en') or scene.get('name') or scene['scene_id']).strip()
    head = f"Empty location background plate for one film shot, photographed with nobody present. Location: {name}."
    head += f" Time of day: {group.get('time_of_day', '')}."
    if lighting:
        head += f" Lighting: {lighting}."
    cam = (f"Camera ({'end of the camera move' if role == 'end' else 'start of the shot'}): {size} framing, "
           f"{facts['lens_mm_equiv']}mm-equivalent lens, camera height {facts['height_m']} m ({facts['height_word']}), "
           f"{facts['tilt_word']}, standing {facts['standing']}, facing {facts['facing']}. ")
    if facts['facing_desc']:
        cam += f"Looking {facts['facing_cardinal']}: {facts['facing_desc']}. "
    cam += f"Frame left is {facts['frame_left']}, frame right is {facts['frame_right']}; behind the camera, out of frame, lies {facts['behind']}"
    cam += (f" ({facts['behind_desc']})" if facts['behind_desc'] else '') + '.'
    if sun:
        cam += f" The low sun is in the {sun['compass']}, {sun['relative']}; long shadows fall {sun['shadows']}."
    lines = [head, cam,
             f"[Image 1] is a grey untextured 3D whitebox render of this exact location from this exact camera: reproduce its perspective, "
             f"its horizon line (about {facts['horizon_pct_from_top']}% down from the top edge), its camera height and the position and scale of "
             f"every volume in frame exactly, then dress every volume with real materials; do not reproduce its grey CG blocks or flat shading."]
    if role == 'end':
        lines.append("[Image 2] is the finished background plate of the same shot at the start of the camera move: keep exactly the same "
                     "location, materials, set dressing, weather, light direction and color grade, seen from this new camera; "
                     "do not copy its framing.")
        lines.append("[Image 3] is the top-down layout map of this location: use it only to identify what each whitebox volume is and where it "
                     "stands relative to the camera; never reproduce the map, its top-down viewpoint or its colors.")
    elif sibling:
        lines.append("[Image 2] is the finished background plate of another shot of the same scene, photographed minutes earlier from a different "
                     "camera: keep exactly the same location, building facades, materials, vegetation, set dressing, weather, light direction "
                     "and color grade, so the two plates read as one place; do not copy its framing or camera.")
        lines.append("[Image 3] is the top-down layout map of this location: use it only to identify what each whitebox volume is and where it "
                     "stands relative to the camera; never reproduce the map, its top-down viewpoint or its colors.")
    else:
        lines.append("[Image 2] is the top-down layout map of this location: use it only to identify what each whitebox volume is and where it "
                     "stands relative to the camera; never reproduce the map, its top-down viewpoint or its colors.")
    if phrases:
        lines.append("In frame from left to right: " + '; '.join(phrases) + '.')
    if out_of_frame:
        lines.append("Not visible in this frame (behind or beside the camera, do not paint them in): " + '; '.join(out_of_frame) + '.')
    if facts.get('standing_hidden'):
        lines.append(f"The camera stands {facts['standing']}, but that surface lies below the bottom edge of the frame and is not visible — "
                     "the frame starts at the far kerb line; do not put any road or ground in the foreground.")
    if desc:
        lines.append("General location description for materials and era only (only the elements listed above are in frame): " + desc)
    lines.append("Empty location plate: no people, no characters, no human figures or silhouettes, no animals, no moving vehicles, "
                 "no text, no watermark, no grid lines, no split screen, one single full-frame photograph.")
    if style:
        lines.append("Style: " + style)
    return '\n'.join(lines)


# ---------------------------------------------------------------- episode index
def episode_index_path(base: Path, ep: str) -> Path:
    return base/'directing'/component(ep)/'shot_plates.json'


def load_episode_index(base: Path, ep: str) -> dict:
    idx = read(episode_index_path(base, ep), None)
    if not isinstance(idx, dict) or idx.get('schema_version') != SCHEMA_EPISODE:
        idx = {'schema_version': SCHEMA_EPISODE, 'ep': component(ep), 'shots': {}}
    idx.setdefault('shots', {})
    return idx


def save_episode_index(base: Path, ep: str, idx: dict):
    p = episode_index_path(base, ep); p.parent.mkdir(parents=True, exist_ok=True)
    idx['written_at'] = dt.datetime.now().isoformat(timespec='seconds')
    tmp = p.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(idx, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    os.replace(tmp, p)


def plate_size(fmt: dict) -> tuple[int, int]:
    """出图尺寸:长边 1920 按项目画幅(偶数)。"""
    w, h = fmt['width'], fmt['height']; scale = 1920/max(w, h)
    return int(round(w*scale/2))*2, int(round(h*scale/2))*2


def camera_stale(recorded: dict, current: dict) -> bool:
    """分镜索引里记录的机位与当前 episode.json 是否不一致(白模重调度后背景图过期)。"""
    if not recorded or not current:
        return True
    return (angle_diff(recorded.get('bearing_deg', 0), current['bearing_deg']) > 1
            or math.dist(recorded.get('position', [0, 0, 0]), current['position']) > .05
            or abs(recorded.get('fov_v_deg', 0) - current['fov_v_deg']) > .5)


# ---------------------------------------------------------------- generation driver
def plan_episode(base: Path, ep: str, only=None) -> dict:
    """算出本集每镜需要的背景图与复用/裁切/新出决策(不出图)。only = 组号或镜号集合。"""
    ep = component(ep)
    episode = read(base/'directing'/ep/'whitebox'/'episode.json', {})
    if not episode:
        raise FileNotFoundError('缺少 directing/<ep>/whitebox/episode.json,请先 python code/render_whitebox.py --compile-only')
    source = read(base/'directing'/ep/'shot_list.json', {}) or {}
    shots = {s['shot_id']: s for s in source.get('shots', [])}
    raw_groups = {g['group_id']: g for g in source.get('generation_groups', [])}
    fmt = render_format(read(base/'settings.json', {}))
    only = set(only or [])
    layouts, axes, libs = {}, {}, {}
    jobs = []   # 逐镜逐角色(start/end)的需求,后按 fov 从宽到窄排序以便窄景别裁切复用
    for group in episode['groups']:
        gid = group['group_id']
        if only and gid not in only and not any(c['shot_id'] in only for c in group['cameras']):
            continue
        sid = group['scene_id']; scene = episode['scenes'][sid]
        if sid not in layouts:
            layouts[sid] = read(base/'assets/concepts/scenes'/sid/'layout.json', {}) or {}
            axes[sid] = orientation_axes(layouts[sid])
            libs[sid] = load_library(base, sid)
        ex, ez, texts = axes[sid]
        raw = raw_groups.get(gid, {})
        scheme = str(raw.get('lighting_scheme_id') or '')
        for cam in group['cameras']:
            shot_id = cam['shot_id']
            if only and gid not in only and shot_id not in only:
                continue
            roles = plate_roles(cam)
            for role in roles['roles']:
                key = cam['keyframes'][0] if role == 'start' else cam['keyframes'][-1]
                t = cam['start'] if role == 'start' else cam['start'] + cam['duration_s'] - 1e-3
                facts = camera_facts(key, fmt, ex, ez, texts)
                facts['standing'] = standing_on(scene, layouts[sid], key)
                jobs.append({'group_id': gid, 'shot_id': shot_id, 'scene_id': sid, 'role': role, 'keyframe': key, 't': t,
                             'facts': facts, 'scheme': scheme, 'tier': roles, 'shot': shots.get(shot_id, {}), 'raw_group': raw})
    return {'episode': episode, 'fmt': fmt, 'jobs': jobs, 'layouts': layouts, 'axes': axes, 'libs': libs, 'shots': shots}


def run_episode(base: Path, ep: str, only=None, *, dry_run=False, force=False, sun='', seed=None, log=print, max_new=None) -> dict:
    """出图主流程:查库 → 裁切/复用 → 渲白模帧 → 出图 → 入库 → 写集索引。返回统计。"""
    ep = component(ep)
    plan = plan_episode(base, ep, only)
    episode, fmt, libs, layouts = plan['episode'], plan['fmt'], plan['libs'], plan['layouts']
    width, height = plate_size(fmt)
    try:
        wb_fmt = render_format(read(base/'settings.json', {}), width, height)
    except ValueError:
        wb_fmt = fmt
    style_doc = read(base/'bible/style.json', {}) or {}
    style = style_doc.get('style_fragment_en') or ''
    idx = load_episode_index(base, ep)
    stats = {'shots': 0, 'plates': 0, 'new': 0, 'library': 0, 'crop': 0, 'skipped_fresh': 0, 'errors': [], 'pending_new': 0}
    # 先决定每个 job 的来源;起点先于终点;同场景按 fov 从宽到窄,窄景别可裁宽图
    jobs = sorted(plan['jobs'], key=lambda j: (j['scene_id'], j['role'] == 'end', -j['facts']['fov_v_deg']))
    by_shot = {}
    for j in jobs:
        by_shot.setdefault(j['shot_id'], []).append(j)
    pending_frames = []
    decisions = []
    pending = {}   # sid -> 本次运行里决定新出、尚未落盘的库条目(供后续镜位复用判断)
    for shot_id, shot_jobs in by_shot.items():
        shot_jobs.sort(key=lambda j: j['role'] == 'end')
        prev = idx['shots'].get(shot_id) or {}
        prev_plates = {p['role']: p for p in prev.get('plates', []) if isinstance(p, dict)}
        for j in shot_jobs:
            sid, scheme, facts = j['scene_id'], j['scheme'], j['facts']
            lib = libs[sid]
            old = prev_plates.get(j['role'])
            if (old and not force and not camera_stale(old.get('camera'), facts) and (base/old['file']).is_file()
                    and old.get('key') and any(e['key'] == old['key'] for e in lib['plates'])):
                decisions.append({**j, 'mode': 'fresh', 'entry': next(e for e in lib['plates'] if e['key'] == old['key']),
                                  'file': old['file'], 'crop': old.get('crop'), 'reuse': old.get('reuse') or 'library'})
                stats['skipped_fresh'] += 1
                continue
            view = {'plates': lib['plates'] + pending.get(sid, [])}   # 本次运行里已决定新出的机位也参与复用判断
            # --force:目标镜不查库、重出新图(同指纹 key 覆盖原库条目);其余镜照常复用
            entry, mode = (None, None) if force else find_reusable(view, scheme, facts, base, sid, require_file=not dry_run)
            if entry is not None:
                decisions.append({**j, 'mode': mode, 'entry': entry, 'file': None, 'crop': None, 'reuse': mode})
                continue
            key = plate_key(scheme, facts)
            if any(e['key'] == key for e in view['plates']) and not (force and any(e['key'] == key for e in lib['plates'])):
                key = f"{key}_{j['shot_id']}{'e' if j['role'] == 'end' else ''}"
            # 白模干净帧:真跑落库(与成图同名);dry-run 落工作目录 directing/<ep>/whitebox/plate_frames/,不污染场景库
            wb_rel = (f"assets/concepts/scenes/{sid}/{PLATES_DIR}/{key}.whitebox.jpg" if not dry_run
                      else f"directing/{ep}/whitebox/plate_frames/{key}.whitebox.jpg")
            pending_frames.append({'group_id': j['group_id'], 't': j['t'], 'output': base/wb_rel})
            pending.setdefault(sid, []).append({'key': key, 'file': f"assets/concepts/scenes/{sid}/{PLATES_DIR}/{key}.png",
                                                'camera': facts, 'lighting_scheme_id': scheme, 'pending': True})
            decisions.append({**j, 'mode': 'new', 'entry': None, 'key': key, 'whitebox_frame': wb_rel, 'file': None, 'crop': None,
                              'reuse': 'new'})
    if pending_frames:   # 白模干净帧本地渲染无成本,dry-run 也渲,便于核对构图
        render_clean_frames(base, episode, pending_frames, wb_fmt['width'], wb_fmt['height'])
    def flush_shot(shot_id):
        """某镜全部决策落地后立即写集索引(进程中途被杀也不丢已出图;重跑按索引/库续跑)。"""
        ds = [d for d in decisions if d['shot_id'] == shot_id and d.get('file')]
        if dry_run or not ds:
            return
        j0 = by_shot[shot_id][0]
        idx['shots'][shot_id] = {
            'group_id': j0['group_id'], 'scene_id': j0['scene_id'], 'lighting_scheme_id': j0['scheme'],
            'movement': next((c.get('movement') for g in episode['groups'] for c in g['cameras'] if c['shot_id'] == shot_id), None),
            'tier': j0['tier'],
            'plates': [{'role': d['role'], 'key': d['entry']['key'], 'file': d['file'], 'reuse': d['reuse'],
                        'crop': d.get('crop'), 'camera': d['facts'], 'whitebox_frame': d['entry'].get('whitebox_frame')}
                       for d in ds],
            'written_at': dt.datetime.now().isoformat(timespec='seconds')}
        save_episode_index(base, ep, idx)

    # 逐决策落地(新出图 → 入库);起点先于终点(终点需要起点成图)
    generated = {}     # (shot_id, 'start') -> entry
    done_shots = set()
    budget_hit = False
    by_key = {}        # 本次运行生成/复用到的库条目 key -> entry
    group_first = {}   # group_id -> 本组最先落定的背景图条目(同组后续新图以它为第二参考图,保证同组各镜是同一处地方)
    forced = {d['shot_id'] for d in decisions if d['mode'] == 'new'} if force else set()
    for g in episode['groups']:
        for shot_id in [c['shot_id'] for c in g['cameras']]:
            if shot_id in forced or g['group_id'] in group_first:
                continue
            for p in (idx['shots'].get(shot_id) or {}).get('plates', []):
                if p.get('role') == 'start' and (base/p['file']).is_file():
                    group_first[g['group_id']] = {'key': p['key'], 'file': p['file']}   # 单镜重出时以同组已有成图为第二参考图
                    break
    channel = None
    for d in decisions:
        sid, shot_id, role = d['scene_id'], d['shot_id'], d['role']
        lib = libs[sid]
        stats['plates'] += 1
        if d['mode'] == 'fresh':
            generated[(shot_id, role)] = d['entry']; by_key[d['entry']['key']] = d['entry']
            group_first.setdefault(d['group_id'], d['entry'])
            continue
        if d['mode'] in ('library', 'crop'):
            entry = d['entry']
            if entry.get('pending'):
                entry = by_key.get(entry['key'])
                if entry is None:
                    stats['errors'].append(f"{shot_id}/{role}: 依赖的背景图 {d['entry']['key']} 本次未能生成")
                    stats['plates'] -= 1
                    continue
                d['entry'] = entry
            if d['mode'] == 'crop' and not dry_run:
                d['file'] = crop_plate(base, entry, d['facts']['fov_v_deg'])
            elif d['mode'] == 'crop':
                d['file'] = str(Path(entry['file']).with_name(f"{entry['key']}.crop_f{int(round(d['facts']['fov_v_deg']))}.png"))
            else:
                d['file'] = entry['file']
            if d['mode'] == 'crop':
                d['crop'] = {'from_fov': entry['camera']['fov_v_deg'], 'fov': d['facts']['fov_v_deg'],
                             'fraction': round(math.tan(math.radians(d['facts']['fov_v_deg']/2))/math.tan(math.radians(entry['camera']['fov_v_deg']/2)), 3)}
            stats[d['mode']] += 1
            generated[(shot_id, role)] = entry; by_key[entry['key']] = entry
            group_first.setdefault(d['group_id'], entry)
            continue
        # new
        if budget_hit or (max_new is not None and stats['new'] >= max_new):
            budget_hit = True
            stats['pending_new'] += 1
            continue
        layout = layouts[sid]; scene = episode['scenes'][sid]
        items, phrases, out_of_frame = inventory(scene, layout, d['keyframe'], fmt)
        stand = d['facts'].get('standing', '')
        d['facts']['standing_hidden'] = bool(stand.startswith('on ')) and not any(it['name'] == stand[3:] for it in items)
        lighting = lighting_fragment(base, sid, d['scheme'])
        desc, scene_neg = scene_description(base, sid)
        sun_rel = sun_relative(sun, d['facts']['bearing_deg']) if sun else None
        sibling = group_first.get(d['group_id']) if role == 'start' else None
        if sibling is not None and not dry_run and not (base/sibling['file']).is_file():
            sibling = None
        prompt = build_prompt(d['facts'], phrases, d['shot'], d['raw_group'], scene, layout, style, lighting, desc, role, sun_rel, out_of_frame,
                              sibling=sibling is not None)
        negative = ', '.join(x for x in (style_doc.get('negative_prompt_en') or '', scene_neg, NEGATIVE_EXTRA) if x)
        sref = d['raw_group'].get('scene_refs') or {}
        top = sref.get('layout_top') or f"assets/concepts/scenes/{sid}/{layout.get('layout_top', 'layout_top.png')}"
        refs = [d['whitebox_frame']]
        start_entry = generated.get((shot_id, 'start')) if role == 'end' else None
        if role == 'end':
            if not start_entry:
                stats['errors'].append(f'{shot_id}: 镜尾图缺镜首成图')
                continue
            refs.append(start_entry['file'])
        elif sibling is not None:
            refs.append(sibling['file'])
        refs.append(top)
        missing = [r for r in refs if not (base/r).is_file() and not (dry_run and ((start_entry and r == start_entry['file']) or (sibling and r == sibling['file'])))]
        if missing:
            stats['errors'].append(f'{shot_id}/{role}: 参考图缺失 {missing}')
            continue
        use_seed = (start_entry or {}).get('seed') if role == 'end' else seed
        if use_seed is None:
            import random
            use_seed = random.randint(1, 2**31-1)
        out_rel = f"assets/concepts/scenes/{sid}/{PLATES_DIR}/{d['key']}.png"
        entry = {'key': d['key'], 'file': out_rel, 'whitebox_frame': d['whitebox_frame'], 'lighting_scheme_id': d['scheme'],
                 'time_of_day': d['raw_group'].get('time_of_day'), 'camera': d['facts'], 'size': f'{width}x{height}', 'seed': use_seed,
                 'refs': refs, 'prompt': prompt, 'negative': negative, 'in_frame': items,
                 'created_by': {'ep': ep, 'shot_id': shot_id, 'group_id': d['group_id'], 'role': role},
                 'written_at': dt.datetime.now().isoformat(timespec='seconds')}
        log(f"== {shot_id} {role} ({d['group_id']}) new plate {d['key']} facing {d['facts']['facing']} h={d['facts']['height_m']}m lens≈{d['facts']['lens_mm_equiv']}mm")
        if dry_run:
            entry['dry_run'] = True
            log(prompt); log('refs: ' + json.dumps(refs, ensure_ascii=False))
        else:
            from modules.genmedia import generate_image, get_config
            if channel is None:
                cfg = get_config('image'); channel = {'provider': cfg.get('provider'), 'model': cfg.get('model')}
            entry['channel'] = channel
            try:
                generate_image(prompt, str(base/out_rel), negative=negative, refs=[str(base/r) for r in refs],
                               aspect=fmt['aspect_ratio'], size=f'{width}x{height}', seed=use_seed)
            except Exception as error:  # noqa: BLE001
                stats['errors'].append(f'{shot_id}/{role}: 出图失败 {error}')
                continue
            (base/out_rel).with_suffix('.json').write_text(json.dumps(entry, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
            lib['plates'] = [e for e in lib['plates'] if e['key'] != entry['key']] + [entry]   # --force 同 key 覆盖
            save_library(base, sid, lib)
            for stale in (base/out_rel).parent.glob(f"{entry['key']}.crop_f*.png"):
                stale.unlink()   # 源图重出后旧裁切失效
        d['entry'] = entry; d['file'] = out_rel
        generated[(shot_id, role)] = entry; by_key[entry['key']] = entry
        group_first.setdefault(d['group_id'], entry)
        stats['new'] += 1
        log(f"saved: {out_rel}")
        flush_shot(shot_id)   # 本镜到此为止已落地的图先写索引(终点图若后续才出,再次 flush 覆盖)
    # 写集索引:按镜落盘(每镜全部决策处理完即写,避免整批跑完才写、中途被杀全丢)
    for shot_id in by_shot:
        flush_shot(shot_id)
    stats['shots'] = len({d['shot_id'] for d in decisions if d.get('file')}) if not dry_run else len(by_shot)
    stats['index'] = str(episode_index_path(base, ep).relative_to(base))
    return stats


# ---------------------------------------------------------------- prompt refs 接线(shot_plate_bound)
_SPATIAL_RE = re.compile(r'\s*Spatial layout:.*?do not copy its tiling\.', re.S)
_MAPUSE_RE = re.compile(r'\s*Map usage:.*?(?:described in that shot\.|in that shot\.)', re.S)
_TILE_RE = re.compile(r',?\s*framed like tile\s*\d+\s*of\s*\[Image\s*\d+\]', re.I)
# 段落以自带结尾句终止(三种口径各一句),兜底到 Shot 1 段头或文末;H3 段内含「[Shot 1]」字样,不能拿段头当终止符
_BLOCK_RE = re.compile(r'\s*Shot plates:.*?(?:described in each [Ss]hot\.|按各 Shot 段描述。|(?=\n?\[?Shot\s*1\s*[:：｜|])|$)', re.S)
_IMG_RE = re.compile(r'\[Image\s*(\d+)\]|@Image\s*(\d+)(?!\d)')


def group_is_v25(base: Path, ep: str, gid: str) -> bool:
    """本组生效视频模型是否 Seedance 2.5:组级覆盖(assets/group_settings)→ 项目提示词技能快照 → genmedia 当前视频模型。
    2.5 的场景图绑定必须按其官方结构写(【场景】分组 + 逐镜激活),2.0 式 Shot plates 段对 2.5 无效(dzg6 grp010 实测,2026-09-09)。"""
    def v25(text):
        t = str(text or '').lower()
        return 'seedance-2-5' in t or 'seedance-2.5' in t or 'sd25' in t
    gs = read(base/'assets/group_settings'/component(ep)/f'{component(gid)}.json', {}) or {}
    if v25(gs.get('video_model')) or v25((gs.get('effective') or {}).get('skill_id')) or v25((gs.get('effective') or {}).get('resolved_from')):
        return True
    if gs.get('video_model'):
        return False
    eff = ((read(base/'settings.json', {}) or {}).get('prompt_skill') or {}).get('effective') or {}
    if v25(eff.get('skill_id')) or v25(eff.get('resolved_from')):
        return True
    try:
        from modules.whitebox_refs import video_budget
        return v25(video_budget(base, ep, gid).get('model'))
    except Exception:  # noqa: BLE001
        return False


_ACT_RE = re.compile(r'\s*场景激活：[^。]*。')
_H3_ANCHOR_RE = re.compile(r'\s*Plate anchor:[^.]*\.(?:[^.]*not used in this shot\.)?')


def group_is_h3(base: Path, ep: str, gid: str) -> bool:
    """本组生效视频模型是否 MiniMax H3(引擎无关:模型 id / ComfyUI 工作流名同时含 minimax 与 h3)。
    H3 的图片绑定用官方 <Picture N> 关键帧/构图锚语法逐镜写,2.0 式 Shot plates 段不适用。"""
    def h3(text):
        t = str(text or '').lower()
        return ('minimax' in t and 'h3' in t) or t.endswith('/h3-prompt-writing')
    gs = read(base/'assets/group_settings'/component(ep)/f'{component(gid)}.json', {}) or {}
    if h3(gs.get('video_model')) or h3((gs.get('effective') or {}).get('skill_id')) or h3((gs.get('effective') or {}).get('resolved_from')):
        return True
    if gs.get('video_model'):
        return False
    eff = ((read(base/'settings.json', {}) or {}).get('prompt_skill') or {}).get('effective') or {}
    if h3(eff.get('skill_id')) or h3(eff.get('resolved_from')):
        return True
    try:
        from modules.whitebox_refs import video_budget
        b = video_budget(base, ep, gid)
        return h3(b.get('model')) or h3(b.get('reason'))
    except Exception:  # noqa: BLE001
        return False


def is_scene_map(ref: str) -> bool:
    name = Path(ref).name
    return ref.startswith('assets/concepts/scenes/') and (name.startswith('layout_top') or name.startswith('grid_9views'))


def plan_group_refs(base: Path, ep: str, gid: str, idx: dict | None = None, episode: dict | None = None) -> dict:
    """本组各镜按镜序应挂的背景图:{'plates': [{shot_id, role, key, file, stale}], 'missing': [shot…], 'group': raw}。"""
    ep, gid = component(ep), component(gid)
    idx = idx if idx is not None else load_episode_index(base, ep)
    episode = episode if episode is not None else (read(base/'directing'/ep/'whitebox'/'episode.json', {}) or {})
    source = read(base/'directing'/ep/'shot_list.json', {}) or {}
    raw = next((g for g in source.get('generation_groups', []) if g.get('group_id') == gid), None)
    if not raw:
        return {'plates': [], 'missing': [], 'group': None}
    wb_group = next((g for g in episode.get('groups', []) if g.get('group_id') == gid), None)
    current = {}
    if wb_group:
        sid = wb_group['scene_id']
        layout = read(base/'assets/concepts/scenes'/sid/'layout.json', {}) or {}
        ex, ez, texts = orientation_axes(layout)
        fmt = render_format(read(base/'settings.json', {}))
        for cam in wb_group['cameras']:
            current[(cam['shot_id'], 'start')] = camera_facts(cam['keyframes'][0], fmt, ex, ez, texts)
            current[(cam['shot_id'], 'end')] = camera_facts(cam['keyframes'][-1], fmt, ex, ez, texts)
    plates, missing = [], []
    for k, shot_id in enumerate(raw.get('shots') or [], 1):
        rec = idx['shots'].get(shot_id)
        if not rec or not rec.get('plates'):
            missing.append(shot_id)
            continue
        for p in rec['plates']:
            if not (base/p['file']).is_file():
                missing.append(shot_id)
                continue
            plates.append({'shot_id': shot_id, 'shot_no': k, 'role': p['role'], 'key': p['key'], 'file': p['file'],
                           'stale': camera_stale(p.get('camera'), current.get((shot_id, p['role']))) if current else False})
    return {'plates': plates, 'missing': missing, 'group': raw}


SCENE_LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'


def scene_labels(plates: list) -> dict:
    """按 refs 顺序给每张背景图一个场景字母:{file: 'A'}(同一文件复用同一字母)。"""
    labels = {}
    for p in plates:
        if p['file'] not in labels:
            labels[p['file']] = SCENE_LETTERS[len(labels) % 26]
    return labels


def build_block_v25(plates: list) -> str:
    """Seedance 2.5 口径:按官方规范把背景图定义成独立场景槽位(【场景】分组),逐镜激活由 Shot 段的「场景激活：」句承担。"""
    labels = scene_labels(plates)
    lines = []
    seen = set()
    for p in plates:
        if p['file'] in seen:
            continue
        seen.add(p['file'])
        users = [f"Shot {q['shot_no']}" + ('落幅' if q['role'] == 'end' else '') for q in plates if q['file'] == p['file']]
        lines.append(f"场景{labels[p['file']]}（{'、'.join(users)} 的机位，空场景 background plate）参考 [Image {p['index']}]，"
                     "只采用空间布局、建筑、材质和光线，不采用图中任何人物。")
    return (BLOCK_KEY + ' 【场景】' + ''.join(lines)
            + '各场景只在点名的镜头里激活；同一地点的不同机位是不同场景槽位，不得合并、不得把一个镜头的场景带进另一个镜头。'
            '背景图只作场景参照：画面不得停在空场，人物与动作按各 Shot 段描述。')


def activation_line(plates: list, shot_no: int) -> str:
    """某镜的「场景激活：」句:使用本镜的场景(两张图的镜写起幅/落幅),不采用同组其它场景。"""
    labels = scene_labels(plates)
    mine = [p for p in plates if p['shot_no'] == shot_no]
    others = sorted({labels[p['file']] for p in plates if p['shot_no'] != shot_no} - {labels[p['file']] for p in mine})
    use = '与'.join(f"场景{labels[p['file']]}（[Image {p['index']}]" + ('，落幅' if p['role'] == 'end' else ('，起幅' if len(mine) > 1 else '')) + '）' for p in mine)
    text = f"场景激活：使用{use}"
    if others:
        text += '；不采用' + '、'.join(f"场景{o}（[Image {next(p['index'] for p in plates if labels[p['file']] == o)}]）" for o in others)
    return text + '。'


def build_block_h3(plates: list) -> str:
    """MiniMax H3 Ref2VA 口径:背景图 = 各镜的构图锚 <Picture N>(官方 2.2:图片作某镜首帧/关键帧/构图锚时用独立 <Picture N> 条目,
    并写明映射到哪个镜头);[Image N] 并列保留供项目机检。"""
    parts = []
    for p in plates:
        n = p['index']
        role = 'end-of-move composition anchor' if p['role'] == 'end' else 'composition anchor'
        parts.append(f"<Picture {n}> ([Image {n}]) is the empty background plate and {role} of [Shot {p['shot_no']}], photographed from "
                     f"that shot's exact camera with nobody in it — reference generation for architecture, set dressing, lighting and camera space only")
    return (BLOCK_KEY + ' ' + '; '.join(parts) + '. Each shot follows only its own plate for set and framing; the plates are set references, '
            'never frames to hold on — keep the subjects and actions described in each shot.')


def anchor_line_h3(plates: list, shot_no: int) -> str:
    mine = [p for p in plates if p['shot_no'] == shot_no]
    others = [p for p in plates if p['shot_no'] != shot_no]
    use = ' and '.join(f"<Picture {p['index']}> ([Image {p['index']}])" + (' at the end of the move' if p['role'] == 'end' else '') for p in mine)
    text = f"Plate anchor: this shot's set and framing correspond to {use}."
    if others:
        seen = []
        for p in others:
            tag = f"<Picture {p['index']}> ([Image {p['index']}])"
            if tag not in seen:
                seen.append(tag)
        text += ' ' + ', '.join(seen) + ' not used in this shot.'
    return text


def build_block(plates: list) -> str:
    parts = []
    for p in plates:
        n = p['index']
        if p['role'] == 'start':
            two = any(q['shot_id'] == p['shot_id'] and q['role'] == 'end' for q in plates)
            parts.append(f"[Image {n}] is the empty background plate of Shot {p['shot_no']}"
                         + (" (start plate, where the camera move begins)" if two else '')
                         + ", photographed from that shot's exact camera with nobody in it — match its framing, perspective, camera height, "
                           "set dressing and lighting, then add the characters")
        else:
            parts.append(f"[Image {n}] is the end plate of Shot {p['shot_no']} (where the camera move ends); the shot travels from the "
                         "start plate's framing to this framing")
    return (BLOCK_KEY + ' ' + '; '.join(parts) + '. Each Shot uses only its own plate for its background and camera angle — do not carry one '
            "Shot's plate into another Shot. Background plates are set references only: never freeze the shot on them, keep the characters and "
            'motion described in each Shot.')


def _remap_images(text: str, old: list, new: list) -> tuple[str, list]:
    """按路径映射重排 [Image N]/@Image N;引用已移除素材的 token 直接删除并记入 warnings。"""
    warns = []
    def replace(m):
        n = int(m.group(1) or m.group(2))
        if n < 1 or n > len(old) or old[n-1] not in new:
            warns.append(f'正文引用已移除的素材 {m.group(0)}(已删除该引用,请核对上下文)')
            return ''
        i = new.index(old[n-1]) + 1
        return f'[Image {i}]' if m.group(1) else f'@Image {i}'
    return _IMG_RE.sub(replace, text), warns


def apply_prompt(prompt: dict, plan: dict, v25: bool = False, h3: bool = False) -> tuple[dict, list]:
    """幂等回写:剔除俯视图/九宫格 refs 与其声明句,角色/生物 sheet 之后插入本组背景图,重排编号,写 Shot plates 段。
    v25=True(Seedance 2.5):Shot plates 段改写为【场景】分组,并在每个 Shot 段头插入「场景激活：」句(逐镜点名激活/不采用)。"""
    out = copy.deepcopy(prompt)
    old = [r for r in (out.get('refs') or []) if isinstance(r, str)]
    vp = out.get('video_prompt') or ''
    vp = _SPATIAL_RE.sub('', vp); vp = _MAPUSE_RE.sub('', vp); vp = _TILE_RE.sub('', vp); vp = _BLOCK_RE.sub(' ', vp)
    vp = _ACT_RE.sub('', vp); vp = _H3_ANCHOR_RE.sub('', vp)
    keep = [r for r in old if not is_scene_map(r) and f'/{PLATES_DIR}/' not in r]
    plates = [p['file'] for p in plan['plates']]
    plates = list(dict.fromkeys(plates))
    cut = 0
    for i, r in enumerate(keep):
        if r.startswith('assets/concepts/characters/') or r.startswith('assets/concepts/creatures/'):
            cut = i + 1
    new = keep[:cut] + plates + keep[cut:]
    vp, warns = _remap_images(vp, old, new)
    if plates:
        indexed = []
        for p in plan['plates']:
            indexed.append({**p, 'index': new.index(p['file']) + 1})
        block = build_block_v25(indexed) if v25 else build_block_h3(indexed) if h3 else build_block(indexed)
        m = re.search(r'\[?Shot\s*1\s*[:：｜|\]]', vp)
        vp = (vp[:m.start()].rstrip() + ' ' + block + ' ' + vp[m.start():]) if m else (vp.rstrip() + ' ' + block)
        after = vp.find(block) + len(block)   # 段头只在 Shot plates 段之后找(H3 段文本里含「[Shot k]」字样)
        if h3:
            for shot_no in sorted({p['shot_no'] for p in indexed}):
                head = re.compile(r'\[?Shot\s*%d\s*(?:[:：\]]|[｜|][^。\n]*。)' % shot_no).search(vp, after)
                if head:
                    vp = vp[:head.end()] + ' ' + anchor_line_h3(indexed, shot_no) + vp[head.end():]
        if v25:
            # 每个 Shot 段头(Shot k: / Shot k｜标题。)之后插入机器持有的「场景激活：」句,Agent 自己的「使用：/不采用：」清单不动
            for shot_no in sorted({p['shot_no'] for p in indexed}):
                head = re.compile(r'Shot\s*%d\s*(?:[:：]|[｜|][^。\n]*。)' % shot_no).search(vp, after)
                if head:
                    vp = vp[:head.end()] + activation_line(indexed, shot_no) + vp[head.end():]
    vp = re.sub(r'[ \t]{2,}', ' ', vp).strip()
    out['refs'] = new
    out['video_prompt'] = vp
    out['shot_plates'] = {'plates': [{k: p[k] for k in ('shot_id', 'role', 'key', 'file')} for p in plan['plates']],
                          'missing': plan['missing'], 'source': 'sync_shot_plates.v1'}
    note = f"分镜背景图自动接线(code/sync_shot_plates.py):refs 挂 {len(plates)} 张背景图" + (f";缺图的镜:{plan['missing']}" if plan['missing'] else '') + ';俯视图/九宫格不再进 refs'
    notes = [n for n in (out.get('notes') or []) if not str(n).startswith('分镜背景图自动接线(')]
    notes.append(note); out['notes'] = notes
    return out, warns


def check_prompt(prompt: dict, plan: dict, gid: str, strict: bool = False, v25: bool = False, h3: bool = False) -> tuple[list, list]:
    errs, warns = [], []
    refs = [r for r in (prompt.get('refs') or []) if isinstance(r, str)]
    vp = prompt.get('video_prompt') or ''
    for r in refs:
        if is_scene_map(r):
            errs.append(f"{gid}: refs 含场景俯视图/九宫格 {r}(2026-09-09 起俯视图仅供分镜预览、九宫格已退役,不进视频参考图;跑 code/sync_shot_plates.py --write 清理)")
    if _SPATIAL_RE.search(vp) or _MAPUSE_RE.search(vp):
        warns.append(f"{gid}: 正文残留 Spatial layout / Map usage 俯视图声明句,建议 --write 清理")
    for shot_id in plan['missing']:
        (errs if strict else warns).append(f"{gid}/{shot_id}: 尚无分镜背景图(白模签字并导出后由 p6-shot-plates 生成:code/render_shot_plates.py)")
    if not plan['plates']:
        if BLOCK_KEY in vp:
            warns.append(f"{gid}: 正文含 {BLOCK_KEY} 段但本组无背景图,建议 --write 清理")
        return errs, warns
    block = _BLOCK_RE.search(vp).group(0) if BLOCK_KEY in vp else ''
    body_text = _BLOCK_RE.sub(' ', vp)   # 段头只在 Shot plates 段之外找
    for p in plan['plates']:
        if p['file'] not in refs:
            errs.append(f"{gid}/{p['shot_id']}: refs 未挂 {p['role']} 背景图 {p['file']}(跑 code/sync_shot_plates.py --write)")
            continue
        n = refs.index(p['file']) + 1
        if v25:
            if not re.search(r'场景[A-Z]（[^）]*background plate）参考 \[Image\s*%d\]' % n, block):
                errs.append(f"{gid}/{p['shot_id']}: Seedance 2.5 口径 {BLOCK_KEY} 段缺【场景】槽位「场景X（… background plate）参考 [Image {n}]」(跑 code/sync_shot_plates.py --write)")
            shot_no = p['shot_no']
            seg = re.search(r'Shot\s*%d\s*(?:[:：]|[｜|][^。\n]*。)(.*?)(?=Shot\s*\d+\s*[:：｜|]|Global constraints:|$)' % shot_no, body_text, re.S)
            body = seg.group(1) if seg else ''
            if not re.search(r'场景激活：使用[^。]*\[Image\s*%d\]' % n, body):
                errs.append(f"{gid}/{p['shot_id']}: Shot {shot_no} 段缺「场景激活：使用场景X（[Image {n}]）…」句(2.5 逐镜激活;跑 --write)")
        elif h3:
            if not re.search(r'<Picture\s*%d>\s*\(\[Image\s*%d\]\)[^.;]*composition anchor of \[Shot\s*%d\]' % (n, n, p['shot_no']), block):
                errs.append(f"{gid}/{p['shot_id']}: H3 口径 {BLOCK_KEY} 段缺「<Picture {n}> ([Image {n}]) … composition anchor of [Shot {p['shot_no']}]」(跑 code/sync_shot_plates.py --write)")
            seg = re.search(r'\[?Shot\s*%d\s*(?:[:：\]]|[｜|][^。\n]*。)(.*?)(?=\[?Shot\s*\d+\s*[:：｜|\]]|Global constraints:|overall_soundscape:|$)' % p['shot_no'], body_text, re.S)
            body = seg.group(1) if seg else ''
            if not re.search(r'Plate anchor:[^.]*<Picture\s*%d>' % n, body):
                errs.append(f"{gid}/{p['shot_id']}: Shot {p['shot_no']} 段缺「Plate anchor: … <Picture {n}>」句(H3 逐镜构图锚;跑 --write)")
        elif not re.search(r'\[Image\s*%d\][^.;]*(background plate|end plate)' % n, block):
            errs.append(f"{gid}/{p['shot_id']}: {BLOCK_KEY} 段缺 [Image {n}] 的 {p['role']} 背景图说明句")
        if p.get('stale'):
            warns.append(f"{gid}/{p['shot_id']}: {p['role']} 背景图机位与当前白模不一致(白模重调度后过期),重跑 code/render_shot_plates.py")
    if BLOCK_KEY not in vp:
        errs.append(f"{gid}: video_prompt 缺 \"{BLOCK_KEY}\" 段")
    return errs, warns


def sync_group(base: Path, ep: str, gid: str, write: bool = False, strict: bool = False, idx=None, episode=None) -> dict:
    ep, gid = component(ep), component(gid)
    plan = plan_group_refs(base, ep, gid, idx, episode)
    pp = base/'assets/prompts'/ep/f'{gid}.json'
    prompt = read(pp, None)
    result = {'group_id': gid, 'plates': [p['file'] for p in plan['plates']], 'missing': plan['missing'],
              'has_prompt': isinstance(prompt, dict), 'updated': False, 'errors': [], 'warnings': []}
    if plan['group'] is None:
        result['errors'].append(f'{gid}: shot_list 无此组')
        return result
    if not isinstance(prompt, dict):
        return result
    v25 = group_is_v25(base, ep, gid)
    h3 = (not v25) and group_is_h3(base, ep, gid)
    result['seedance_25'] = v25; result['minimax_h3'] = h3
    if write:
        updated, warns = apply_prompt(prompt, plan, v25, h3)
        result['warnings'].extend(f'{gid}: {w}' for w in warns)
        if updated != prompt:
            backup = base/'directing'/ep/'whitebox'/'prompt_backups'/pp.name
            backup.parent.mkdir(parents=True, exist_ok=True)
            if not backup.exists():
                backup.write_bytes(pp.read_bytes())
            tmp = pp.with_suffix('.json.tmp')
            tmp.write_text(json.dumps(updated, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
            os.replace(tmp, pp)
            result['updated'] = True
        prompt = updated
    e, w = check_prompt(prompt, plan, gid, strict, v25, h3)
    result['errors'].extend(e); result['warnings'].extend(w)
    return result


def sync_episode(base: Path, ep: str, groups=None, write: bool = False, strict: bool = False) -> dict:
    ep = component(ep)
    source = read(base/'directing'/ep/'shot_list.json', {}) or {}
    ids = [g['group_id'] for g in source.get('generation_groups', []) if g.get('group_id')]
    unknown = sorted(set(groups or []) - set(ids))
    if groups:
        ids = [g for g in ids if g in set(groups)]
    idx = load_episode_index(base, ep)
    episode = read(base/'directing'/ep/'whitebox'/'episode.json', {}) or {}
    rows = [sync_group(base, ep, gid, write, strict, idx, episode) for gid in ids]
    errors = [f'{g}: unknown group' for g in unknown] + [e for r in rows for e in r['errors']]
    warnings = [w for r in rows for w in r['warnings']]
    return {'groups': rows, 'updated_prompts': [r['group_id'] for r in rows if r['updated']],
            'errors': errors, 'warnings': warnings}


# ---------------------------------------------------------------- coverage status(验收机检 shot_plates_complete)
def status_episode(base: Path, ep: str, only=None) -> dict:
    """每镜背景图覆盖状态:ok / partial(缺镜尾) / missing / stale(机位与当前白模不一致) / file_missing。
    验收以此为准,不采信 Agent 自述;退出码由调用方按 problems 判。"""
    plan = plan_episode(base, ep, only)
    idx = load_episode_index(base, ep)
    need = {}
    for j in plan['jobs']:
        need.setdefault(j['shot_id'], {})[j['role']] = j['facts']
    shots = {}
    for shot_id, roles in need.items():
        rec = idx['shots'].get(shot_id) or {}
        have = {p['role']: p for p in rec.get('plates', []) if isinstance(p, dict)}
        state = 'ok'
        for role, facts in roles.items():
            p = have.get(role)
            if not p:
                state = 'missing' if role == 'start' else ('partial' if state == 'ok' else state)
            elif not (base/p['file']).is_file():
                state = 'file_missing'
            elif camera_stale(p.get('camera'), facts) and state == 'ok':
                state = 'stale'
        shots[shot_id] = {'state': state, 'need': sorted(roles), 'have': sorted(have),
                          'files': [p['file'] for p in have.values()]}
    problems = {k: v['state'] for k, v in shots.items() if v['state'] != 'ok'}
    return {'ep': component(ep), 'shots_total': len(shots), 'shots_ok': len(shots) - len(problems),
            'plates_needed': sum(len(v['need']) for v in shots.values()), 'problems': problems, 'shots': shots}
