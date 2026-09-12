"""四方向平视背景图(cardinal plates,实验 v5,2026-09-12)。

思路:视点固定在场景俯视图的正中心(白模米制 [0, 0],落在实体内则吸附到最近空点;--at 可改),朝东/南/西/北各出一张
12 mm 等效直线透视超广角平视空场景图(水平视场 ≈113°);参考图只有一张「标点俯视图」(俯视图叠视点红点 + 本向视锥 + 方位字母),
几何/内容全靠文字(方位描述 + 逐物体距离/画面横向位置 + 严格一点透视 · 不倾斜措辞);四张整图直接作该场景的分镜背景图(不按镜裁切),
每个分镜按白模机位朝向 + 运镜方式从四张里选 1~多张。与现行「全景制」分镜背景图(modules/scene_panos.py / shot_plates.py)并行,不接进组 prompt refs。

v1(2026-09-11 上午):只给标点俯视图,直接出四张 —— 方位随机偏转,12 mm 是直线能守住的上限。
v2(2026-09-11 下午):俯视图 → 一张 2:1 全景 → 纯几何投四向 —— 模型不按平面图距离画(院墙绕 200°、南侧缺、接缝硬)。
v3(2026-09-12 凌晨):站点渲白模全景 → 几何投四张 16 mm 白模帧作 [Image 1] —— 方位对了,但站点离实体太近,模型自行偏转机位。
v4(2026-09-12 上午):视点按方向退到被实体/边界挡住前的最远空点 + 透视相机直出白模帧 + 严格一点透视措辞 + 取消裁切。
v5(本文件,2026-09-12 用户定):① 去掉白模帧参考图(不再渲白模、不挂 [Image 1] 白模帧),只用标点俯视图 + 文字;
    ② 视点不再按方向后退,四向统一放在俯视图正中心;③ 提示词明确要求画面视角不倾斜(不俯不仰不滚转不侧转,地平线水平居中,竖直边保持竖直),
    负面词加 tilted camera / high angle / low angle / looking up / looking down / converging verticals。

数据:assets/concepts/scenes/<sid>/cardinal/
  index.json                  schema cardinal_plates.v5:station(中心视点)、stations{north|east|south|west}(与 station 同点,保留结构)、
                              lens_mm_equiv、fov_h_deg、fov_v_deg、plate_size、scheme_id、time_of_day、sun、
                              plates{dir}{file, bearing_deg, camera, refs, prompt, seed, channel, size, written_at}
  viewpoint_<dir>.jpg         俯视图叠视点红点 + 本向视锥 + 方位字母(唯一参考图)
  <dir>.png / <dir>.json      成图与记录
集索引:directing/<ep>/cardinal_plates.json(schema cardinal_plates_episode.v5):每镜 category、bearing/pitch/fov、picks[]{dir, role, file, delta_deg, fits}
坐标约定同白模:x = (u-0.5)·dims_x,z = (v-0.5)·dims_z(俯视图整幅铺满地面);罗盘按 layout.json orientation(缺省上北下南)。
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re
from pathlib import Path

from modules.whitebox import component, read

SCHEMA = 'cardinal_plates.v5'
SCHEMA_EPISODE = 'cardinal_plates_episode.v5'
DIRNAME = 'cardinal'
DIRS = ['north', 'east', 'south', 'west']
BEARINGS = {'north': 0.0, 'east': 90.0, 'south': 180.0, 'west': 270.0}
ORDER = ['north', 'south', 'east', 'west']      # 出图顺序(各张互不引用)
DEFAULT_LENS_MM = 12.0
SENSOR_W_MM = 36.0
PLATE_SIZE = '2560x1440'
STATION_HEIGHT_M = 1.5
CLEARANCE_M = 0.8
FIT_MARGIN_DEG = 2.0            # 本镜视窗超出单张平视图边缘超过此角度 → 取相邻两张

NEGATIVE = ('people, person, human figure, silhouette, crowd, pedestrian, animals, vehicles in motion, text, watermark, logo, '
            'grid lines, wireframe, grey untextured blocks, 3D render look, CGI, map, floor plan, top-down view, bird\'s-eye view, '
            'split screen, collage, black borders, fisheye, barrel distortion, curved horizon, bent straight lines, tilted horizon, '
            'panorama, cube map, tiled grid, oblique view, angled view, three-quarter view, rotated camera, yawed camera, '
            'dutch angle, diagonal wall, two-point perspective, converging horizontal lines, tilted camera, high angle, low angle, '
            'looking up, looking down, upward tilt, downward tilt, converging verticals, keystone distortion')

MAP_RULE = (
    "[Image {n}] is the top-down plan of this location with the camera standpoint marked as a red dot at the exact centre of the plan "
    "and this picture's field of view drawn as a red wedge pointing {dir}: use it to decide what lies in each direction, how far away "
    "it is and where it falls across the frame. It is the only reference image. Never reproduce the plan, its top-down viewpoint, "
    "colours, wedge, letters or graphics. ")


def _now():
    return dt.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')


def cardinal_dir(base: Path, sid: str) -> Path:
    return base / 'assets/concepts/scenes' / component(sid) / DIRNAME


def load_index(base: Path, sid: str) -> dict:
    return read(cardinal_dir(base, sid) / 'index.json', {}) or {}


def save_index(base: Path, sid: str, idx: dict):
    d = cardinal_dir(base, sid)
    d.mkdir(parents=True, exist_ok=True)
    idx['schema_version'] = SCHEMA
    idx['scene_id'] = sid
    (d / 'index.json').write_text(json.dumps(idx, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


# ---------------------------------------------------------------- lens geometry
def lens_fov(lens_mm: float, width: int, height: int) -> tuple[float, float]:
    """等效焦距 → (水平视场°, 垂直视场°),全画幅 36 mm 宽、按画幅比例取高。"""
    hfov = 2 * math.degrees(math.atan(SENSOR_W_MM / 2 / lens_mm))
    sensor_h = SENSOR_W_MM * height / width
    vfov = 2 * math.degrees(math.atan(sensor_h / 2 / lens_mm))
    return round(hfov, 2), round(vfov, 2)


def hfov_from_vfov(vfov: float, width: int, height: int) -> float:
    return 2 * math.degrees(math.atan(math.tan(math.radians(vfov / 2)) * width / height))


# ---------------------------------------------------------------- station
def _inside(p, obj, margin=0.0) -> bool:
    cx, _, cz = obj['position']; sx, _, sz = obj['size_m']
    return abs(p[0] - cx) <= sx / 2 + margin and abs(p[1] - cz) <= sz / 2 + margin


def tall_objects(scene: dict, height: float = 0.5) -> list:
    return [o for o in scene['objects'] if o['position'][1] + o['size_m'][1] / 2 > height]


def scene_cameras(base: Path, sid: str, ep: str, groups=None, scene_no=None) -> list:
    """本集本场景白模里的机位(镜首 + 镜尾各一条)。"""
    from modules.shot_plates import plate_roles
    episode = read(base / 'directing' / ep / 'whitebox/episode.json', {}) or {}
    if not episode:
        raise FileNotFoundError(f'{ep}: directing/{ep}/whitebox/episode.json 不存在,先编译白模(render_whitebox.py --compile-only)')
    out = []
    for g in episode.get('groups', []):
        if g.get('scene_id') != sid or (groups and g['group_id'] not in groups) or (scene_no and g.get('scene_no') != scene_no):
            continue
        for cam in g.get('cameras', []):
            info = plate_roles(cam)
            for role in info['roles']:
                key = cam['keyframes'][0] if role == 'start' else cam['keyframes'][-1]
                out.append({'group_id': g['group_id'], 'shot_id': cam['shot_id'], 'role': role, 'movement': cam.get('movement'),
                            'category': info['category'], 'reason': info['reason'],
                            'position': list(key['position']), 'target': list(key['target']), 'fov': float(key['fov']),
                            'keyframes': cam['keyframes']})
    return out


def auto_station(scene: dict, cameras: list, height: float | None = None) -> dict:
    """视点 = 场景俯视图正中心(白模米制 [0, 0]);落在实体(含 CLEARANCE)内则吸附到最近的 0.25 m 空网格点;
    高度 = 本场景机高中位数(≥1.4 m,人眼平视),无机位时 STATION_HEIGHT_M。"""
    hs = sorted(c['position'][1] for c in cameras)
    h = height if height is not None else (max(1.4, hs[len(hs) // 2]) if hs else STATION_HEIGHT_M)
    xz = snap_free([0.0, 0.0], scene)
    basis = '俯视图正中心' if xz == [0.0, 0.0] else f'俯视图正中心落在实体内,吸附到最近空点 {xz}'
    return {'position': xz + [round(h, 2)], 'source': 'auto', 'basis': basis}


def snap_free(p, scene: dict) -> list:
    tall = tall_objects(scene)
    dx, _, dz = scene['dimensions_m']
    best, best_d = None, 1e9
    for i in range(-int(dx * 2), int(dx * 2) + 1):
        for j in range(-int(dz * 2), int(dz * 2) + 1):
            q = [i * 0.25, j * 0.25]
            if abs(q[0]) > dx / 2 - 0.5 or abs(q[1]) > dz / 2 - 0.5:
                continue
            if any(_inside(q, o, CLEARANCE_M) for o in tall):
                continue
            d = math.hypot(q[0] - p[0], q[1] - p[1])
            if d < best_d:
                best, best_d = q, d
    return [round(best[0], 2), round(best[1], 2)] if best else [round(p[0], 2), round(p[1], 2)]


def station_record(scene: dict, layout_size, pos) -> dict:
    """[x, z, y] → index 记录:米制 / 归一化 / 像素。"""
    x, z, y = pos
    dx, _, dz = scene['dimensions_m']
    u, v = x / dx + 0.5, z / dz + 0.5
    rec = {'position_m': [round(x, 3), round(y, 3), round(z, 3)], 'xy_norm': [round(u, 4), round(v, 4)], 'dimensions_m': scene['dimensions_m']}
    if layout_size:
        rec['px'] = [int(round(u * layout_size[0])), int(round(v * layout_size[1]))]
        rec['layout_top_px'] = list(layout_size)
    return rec


# ---------------------------------------------------------------- viewpoint maps
def draw_viewpoint_maps(base: Path, sid: str, layout_top: Path, stations: dict, hfov: float, *, log=print) -> dict:
    """俯视图叠视点红点(四向同一点,俯视图正中心)+ 四向字母;每个方向一张,本向视锥填色。"""
    from PIL import Image, ImageDraw, ImageFont
    out = cardinal_dir(base, sid)
    im0 = Image.open(layout_top).convert('RGB')
    W, H = im0.size
    r = max(6, W // 220)
    L = min(W, H) * 0.32
    try:
        font = ImageFont.truetype('/System/Library/Fonts/Helvetica.ttc', max(28, W // 45))
    except Exception:
        font = ImageFont.load_default()
    letters = {'north': ('N', 0, -1), 'east': ('E', 1, 0), 'south': ('S', 0, 1), 'west': ('W', -1, 0)}
    files = {}
    for d in DIRS:
        px, py = stations[d]['px']
        im = im0.copy()
        ov = Image.new('RGBA', im.size, (0, 0, 0, 0))
        dr = ImageDraw.Draw(ov)
        # 视锥扇形(图上北 = 上,东 = 右)
        a0, a1 = BEARINGS[d] - hfov / 2 - 90, BEARINGS[d] + hfov / 2 - 90    # PIL 角度:0 = 右(东),顺时针
        dr.pieslice([px - L, py - L, px + L, py + L], a0, a1, fill=(255, 40, 40, 70), outline=(255, 30, 30, 230), width=4)
        for k, (ch, ux, uz) in letters.items():
            ex, ey = px + ux * L * 0.55, py + uz * L * 0.55
            dr.line([px, py, ex, ey], fill=(255, 30, 30, 230), width=5)
            tw, th = dr.textbbox((0, 0), ch, font=font)[2:]
            dr.rectangle([ex - tw / 2 - 8, ey - th / 2 - 6, ex + tw / 2 + 8, ey + th / 2 + 8], fill=(255, 255, 255, 235))
            dr.text((ex - tw / 2, ey - th / 2), ch, fill=(220, 20, 20, 255), font=font)
        dr.ellipse([px - r, py - r, px + r, py + r], fill=(255, 20, 20, 255), outline=(255, 255, 255, 255), width=3)
        im = Image.alpha_composite(im.convert('RGBA'), ov).convert('RGB')
        f = out / f'viewpoint_{d}.jpg'
        im.save(f, quality=90)
        files[d] = f.name
    log(f'   标点俯视图:{", ".join(files.values())}')
    return files


# ---------------------------------------------------------------- prompt
def _flat(v):
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, list):
        return '; '.join(x for x in (_flat(i) for i in v) if x)
    if isinstance(v, dict):
        return '; '.join(x for x in (_flat(i) for i in v.values()) if x)
    return ''


NAME_HINTS = {'wall': 'the red brick boundary wall', 'gate': 'a gate post of the iron railing gate', 'tree': 'a roadside tree',
              'pole': 'the concrete utility pole', 'lamp': 'the old-style street lamp', 'apartments': 'the low apartment blocks',
              'shop': 'the small corner shop', 'road': 'the two-lane asphalt road'}


def _obj_name(oid, layout):
    """物体名:地标同名/前缀 → 地标英文名;否则基名(常见基名给固定英文短语)。"""
    from modules.shot_plates import base_name, resolve_landmark
    landmarks = {lm['id']: lm for lm in layout.get('landmarks', []) if 'xy' in lm}
    bn = base_name(oid)
    lid = resolve_landmark(bn, landmarks)
    if lid and (lid == bn or ((lid.startswith(bn + '_') or bn.startswith(lid)) and bn not in NAME_HINTS)):
        return landmarks[lid].get('name_en') or landmarks[lid].get('name') or lid
    return NAME_HINTS.get(bn, bn.replace('_', ' '))


def view_inventory(scene: dict, layout: dict, station, bearing: float, hfov: float) -> tuple[list, list]:
    """(视场内物体句子, 视场外物体名)。横向位置按直线透视 tan 比例换算成画面百分比。"""
    from modules.shot_plates import bearing_deg, compass, orientation_axes, angle_diff
    ex, ez, _ = orientation_axes(layout)
    objs = {o['id']: o for o in scene['objects']}
    sx0, sz0 = station[0], station[2]
    hf = math.tan(math.radians(hfov / 2))
    inside, outside = [], []
    for oid, o in objs.items():
        if oid.endswith('_back') and oid[:-5] in objs:
            continue
        m = re.match(r'canopy(\d*)$', oid)
        if m and f'tree{m.group(1)}' in objs:      # 树冠并入树干一条
            continue
        cx, cy, cz = o['position']; sx, sy, sz = o['size_m']
        top = cy + sy / 2
        m = re.match(r'tree(\d*)$', oid)
        if m and f'canopy{m.group(1)}' in objs:
            c = objs[f'canopy{m.group(1)}']
            top = max(top, c['position'][1] + c['size_m'][1] / 2)
        dx, dz = cx - sx0, cz - sz0
        dist = math.hypot(dx, dz)
        name = _obj_name(oid, layout)
        if dist < 0.6:
            inside.append(f'{name} directly beneath the camera.')
            continue
        b = bearing_deg(dx, dz, ex, ez)
        delta = ((b - bearing + 180) % 360) - 180
        long = max(sx, sz)
        half_ang = math.degrees(math.atan2(long / 2, dist)) if long >= 3 else 0
        if abs(delta) - half_ang > hfov / 2:
            if name not in outside:
                outside.append(name)
            continue
        size = f'{long:.0f} m long, ' if long >= 1 else ''
        desc = f'{name}: {size}top {top:.1f} m above the ground, centred {dist:.0f} m away to the {compass(b)}'
        if abs(delta) <= hfov / 2:
            col = int(round((math.tan(math.radians(delta)) / hf + 1) / 2 * 100))
            desc += f' (about {col}% across the frame from the left edge)'
        else:
            desc += ' (its centre is just outside the frame, only its near end shows at the ' + ('right' if delta > 0 else 'left') + ' edge)'
        if long >= 3 * min(sx, sz) and long >= 2:
            oyaw = float(o.get('yaw') or 0)
            axis = [math.cos(oyaw), -math.sin(oyaw)] if sx >= sz else [math.sin(oyaw), math.cos(oyaw)]
            b1 = bearing_deg(axis[0], axis[1], ex, ez)
            rel = min(angle_diff(b1, bearing), angle_diff((b1 + 180) % 360, bearing))
            desc += f', its long side running {compass(b1)}–{compass((b1 + 180) % 360)}'
            desc += ' (seen broadside, crossing the frame horizontally)' if rel > 60 else ' (receding straight away from the camera toward the vanishing point)' if rel < 30 else ' (crossing the frame diagonally)'
        inside.append(desc + '.')
    return inside, outside


def build_prompt(base: Path, sid: str, scene: dict, layout: dict, station: dict, d: str, hfov: float, vfov: float, lens_mm: float,
                 scheme: dict, time_of_day: str, sun: str | None) -> str:
    from modules.shot_plates import orientation_axes, strip_compass, standing_on, sun_relative, compass
    bearing = BEARINGS[d]
    _, _, texts = orientation_axes(layout)
    left, right, behind = DIRS[(DIRS.index(d) - 1) % 4], DIRS[(DIRS.index(d) + 1) % 4], DIRS[(DIRS.index(d) + 2) % 4]
    name = re.sub(r'[(（].*?[)）]', '', layout.get('scene_name_en') or scene.get('name') or sid).strip()
    arch = read(base / 'bible/scenes' / component(sid) / 'architecture.json', {}) or {}
    style = (read(base / 'bible/style.json', {}) or {}).get('style_fragment_en') or ''
    pos = station['position_m']
    standing = standing_on(scene, layout, {'position': pos, 'target': pos})
    parts = [f'Empty location background plate, one single full-frame photograph with nobody present and nothing moving, '
             f'realistic live-action film look. Location: {name}. Time of day: {time_of_day}.',
             f'Camera: {lens_mm:.0f}mm-equivalent rectilinear ultra-wide lens on a full-frame camera (horizontal field of view about '
             f'{hfov:.0f} degrees, vertical about {vfov:.0f} degrees), architectural straight-line perspective — every straight edge '
             f'stays perfectly straight, no fisheye or barrel distortion. '
             f'The camera stands {standing} at the exact centre of the site (the red dot in the middle of the plan), lens {pos[1]} m '
             f'above the ground, looking due {d}.',
             f'The view is NOT tilted in any way: the lens axis is perfectly horizontal (pitch 0°, not looking up, not looking down), '
             f'not rolled (no dutch angle) and not turned sideways, so the horizon is a level line at the exact mid-height of the frame '
             f'and every vertical edge (walls, posts, poles, door frames, tree trunks) stays perfectly vertical and parallel to the '
             f'sides of the frame with no keystone or converging verticals.',
             f'Strict one-point perspective, dead square-on to the {d} side, like an architectural elevation photograph: the lens axis '
             f'is exactly perpendicular to everything that runs {left}–{right} (the boundary wall, building fronts, kerbs, pavements, '
             f'lane markings), so every one of them is drawn as a perfectly horizontal line parallel to the top and bottom edges of '
             f'the frame, the same distance from the camera at the left edge as at the right edge, never converging toward one side. '
             f'Things that run {d}–{behind} recede straight toward the single vanishing point at the exact centre of the frame. '
             f'The camera is not turned, not rotated and not tilted: no oblique or three-quarter view, no two-point perspective, '
             f'no diagonal walls, no dutch angle.']
    parts.append(MAP_RULE.format(n=1, dir=d).strip())
    if texts.get(d):
        parts.append(f'Straight ahead (centre of the frame, {d}): ' + strip_compass(texts[d]) + '.')
    if texts.get(left):
        parts.append(f'Frame left is {left} (that side continues out of frame to the left): ' + strip_compass(texts[left]) + '.')
    if texts.get(right):
        parts.append(f'Frame right is {right} (that side continues out of frame to the right): ' + strip_compass(texts[right]) + '.')
    if texts.get(behind):
        parts.append(f'Behind the camera, {behind}, NOT visible in this frame: ' + strip_compass(texts[behind]) + '.')
    inside, outside = view_inventory(scene, layout, pos, bearing, hfov)
    if inside:
        parts.append('Exact placement of everything in this frame, measured from this camera at the centre of the plan (frame column 0% = left edge, 50% = centre, 100% = right edge): '
                     + ' '.join(inside))
    if outside:
        parts.append('Not visible in this frame (behind or beside the camera, do not paint them in): ' + ', '.join(outside) + '.')
    if sun:
        rel = sun_relative(sun, bearing)
        parts.append(f"The low sun is in the {rel['compass']}, {rel['relative']}; long shadows fall {rel['shadows']}.")
    if scheme.get('prompt_fragment_en'):
        parts.append('Lighting: ' + scheme['prompt_fragment_en'] + '.')
    desc = ' '.join(p.rstrip('.。;') + '.' for p in (_flat(arch.get(k)) for k in ('form', 'arch_style', 'era_region', 'scale', 'materials', 'details')) if p)
    if desc:
        parts.append('Materials and era (reference only; only the elements listed above are in frame): ' + desc[:1200])
    parts.append('Empty location plate: no people, no characters, no human figures or silhouettes, no animals, no moving vehicles, '
                 'no text, no watermark, no grid lines, no split screen, one single rectilinear photograph.')
    if style:
        parts.append('Style: ' + style)
    return '\n'.join(p.strip() for p in parts if p and p.strip())


def lighting_scheme(base: Path, sid: str, scheme_id: str | None) -> dict:
    doc = read(base / 'bible/scenes' / component(sid) / 'lighting.json', {}) or {}
    for s in doc.get('schemes', []):
        if not scheme_id or s.get('scheme_id') == scheme_id or s.get('id') == scheme_id:
            return s
    return {}


# ---------------------------------------------------------------- generation
def generate_plate(base: Path, sid: str, idx: dict, d: str, prompt: str, refs: list, size: str, seed: int, *, log=print) -> dict:
    from PIL import Image
    from modules.genmedia import generate_image, get_config, image_pref_env
    with image_pref_env('scenes'):
        cfg = get_config('image')
    out = cardinal_dir(base, sid)
    target = out / f'{d}.png'
    if target.is_file():
        target.rename(out / f'{d}.prev-{dt.datetime.now().strftime("%H%M%S")}.png')
    log(f"   出 {d} 图 {cfg.get('provider')}/{cfg.get('model')} {size} seed {seed},参考图 {len(refs)} 张 …")
    generate_image(prompt, str(target), negative=NEGATIVE, refs=[str(r) for r in refs], size=size, seed=seed)
    im = Image.open(target); rw, rh = im.size
    rec = {'file': target.name, 'dir': d, 'compass': d, 'bearing_deg': BEARINGS[d], 'size': [rw, rh], 'seed': seed,
           'channel': {'provider': cfg.get('provider'), 'model': cfg.get('model')},
           'refs': [str(Path(r).relative_to(base)) if str(r).startswith(str(base)) else str(r) for r in refs],
           'prompt': prompt, 'negative': NEGATIVE, 'written_at': _now()}
    (out / f'{d}.json').write_text(json.dumps(rec, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    log(f'saved: {target.relative_to(base)} ({rw}x{rh})')
    return rec


def prepare(base: Path, sid: str, ep: str, *, at=None, height=None, lens_mm=DEFAULT_LENS_MM, size=PLATE_SIZE, scheme_id=None,
            time_of_day=None, sun=None, groups=None, scene_no=None, log=print, force_geometry=False) -> tuple[dict, dict, dict]:
    """中心视点(俯视图正中心)+ 标点俯视图 + 索引骨架(不出图)。返回 (idx, scene, layout)。"""
    from modules.whitebox import load_scene, image_size
    scene = load_scene(base, sid)
    sdir = base / 'assets/concepts/scenes' / component(sid)
    layout = read(sdir / 'layout.json', {}) or {}
    layout_top = sdir / (layout.get('layout_top') or 'layout_top.png')
    width, height_px = (int(x) for x in size.lower().split('x'))
    hfov, vfov = lens_fov(lens_mm, width, height_px)
    cams = scene_cameras(base, sid, ep, groups, scene_no)
    idx = load_index(base, sid)
    if idx and idx.get('schema_version') != SCHEMA:
        log(f"   旧索引 {idx.get('schema_version')} ≠ {SCHEMA},按新版重算(旧图文件保留)")
        idx = {}
    if at:
        h = height if height is not None else (idx.get('station', {}).get('position_m', [0, STATION_HEIGHT_M, 0])[1])
        st = {'position': [round(at[0], 2), round(at[1], 2), round(h, 2)], 'source': 'manual', 'basis': '--at'}
    elif idx.get('station') and not force_geometry:
        p = idx['station']['position_m']
        st = {'position': [p[0], p[2], p[1]], 'source': idx['station'].get('source', 'index'), 'basis': idx['station'].get('basis', '')}
    else:
        st = auto_station(scene, cams, height)
    tall = tall_objects(scene)
    for o in tall:
        if _inside(st['position'][:2], o):
            raise ValueError(f"站点 {st['position'][:2]} 落在白模实体 {o['id']} 内,换 --at")
    lsize = image_size(layout_top)
    station = station_record(scene, lsize, st['position'])
    station.update({'source': st['source'], 'basis': st['basis']})
    stations = {d: dict(station_record(scene, lsize, st['position']), mode='centre') for d in DIRS}   # 四向同一视点 = 俯视图正中心
    changed = (idx.get('station', {}).get('position_m') != station['position_m'] or idx.get('lens_mm_equiv') != lens_mm
               or idx.get('plate_size') != [width, height_px]
               or {d: v['position_m'] for d, v in (idx.get('stations') or {}).items()} != {d: v['position_m'] for d, v in stations.items()})
    log(f"视点(俯视图正中心){station['position_m']} (norm {station['xy_norm']}, px {station.get('px')}) [{st['source']} {st['basis']}];"
        f" 16:9 {width}x{height_px},{lens_mm:.0f} mm 等效 → 水平 {hfov}°/垂直 {vfov}°;四向朝 " + ' / '.join(f'{d} {BEARINGS[d]:.0f}°' for d in DIRS))
    out = cardinal_dir(base, sid)
    if changed or force_geometry or not all((out / f'viewpoint_{d}.jpg').is_file() for d in DIRS):
        maps = draw_viewpoint_maps(base, sid, layout_top, stations, hfov, log=log)
        keep = {}
        if changed and idx.get('plates'):
            same_lens = idx.get('lens_mm_equiv') == lens_mm and idx.get('plate_size') == [width, height_px]
            for d, rec in idx['plates'].items():
                if same_lens and (idx.get('stations') or {}).get(d, {}).get('position_m') == stations[d]['position_m']:
                    keep[d] = rec
            gone = [d for d in idx['plates'] if d not in keep]
            if gone:
                log(f"   视点/焦距/尺寸变了,已成图作废(文件保留,索引移除):{', '.join(gone)}" + (f";视点未动保留:{', '.join(keep)}" if keep else ''))
        idx = {'station': station, 'stations': stations, 'viewpoint_maps': maps, 'plates': keep}
    else:
        idx.setdefault('plates', {})
    scheme = lighting_scheme(base, sid, scheme_id)
    idx.update({'lens_mm_equiv': lens_mm, 'fov_h_deg': hfov, 'fov_v_deg': vfov, 'plate_size': [width, height_px],
                'scheme_id': scheme.get('scheme_id') or scheme_id, 'time_of_day': time_of_day or (scheme.get('condition') or {}).get('time_of_day') or '',
                'sun': sun, 'layout_top': layout_top.name, 'planned_at': _now()})
    save_index(base, sid, idx)
    return idx, scene, layout


def run(base: Path, sid: str, ep: str, *, only=None, seed=None, dry_run=False, force=False, log=print, **kw) -> dict:
    idx, scene, layout = prepare(base, sid, ep, log=log, **kw)
    out = cardinal_dir(base, sid)
    scheme = lighting_scheme(base, sid, idx.get('scheme_id'))
    width, height = idx['plate_size']
    if seed is None:
        import random
        seed = idx.get('seed') or random.randint(1, 2 ** 31 - 1)
    idx['seed'] = seed
    todo = [d for d in ORDER if (not only or d in only)]
    for d in todo:
        if idx['plates'].get(d) and (out / idx['plates'][d]['file']).is_file() and not force:
            log(f'   {d}: 已有,跳过(--force 重出)')
            continue
        # 参考图只有本向标点俯视图(v5 用户定:去掉白模帧),不挂其它方向的成图(挂了会照抄别向构图);
        # 四张的材质/光线一致性靠同一 seed + 同一段光照/材质文字
        refs = [out / idx['viewpoint_maps'][d]]
        prompt = build_prompt(base, sid, scene, layout, idx['stations'][d], d, idx['fov_h_deg'], idx['fov_v_deg'], idx['lens_mm_equiv'],
                              scheme, idx['time_of_day'], idx.get('sun'))
        (out / f'{d}.prompt.txt').write_text(prompt + '\n\nNEGATIVE: ' + NEGATIVE + '\n', encoding='utf-8')
        if dry_run:
            log(f'   [dry-run] {d}: 参考图 {[r.name for r in refs]},提示词 {len(prompt)} 字 → {d}.prompt.txt')
            continue
        rec = generate_plate(base, sid, idx, d, prompt, refs, f'{width}x{height}', seed, log=log)
        rec.update({'fov_h_deg': idx['fov_h_deg'], 'fov_v_deg': idx['fov_v_deg'], 'lens_mm_equiv': idx['lens_mm_equiv'],
                    'viewpoint_map': idx['viewpoint_maps'][d],
                    'station': idx['stations'][d],
                    'camera': {'position': idx['stations'][d]['position_m'], 'bearing_deg': BEARINGS[d], 'pitch_deg': 0.0,
                               'height_m': idx['stations'][d]['position_m'][1]}})
        idx['plates'][d] = rec
        save_index(base, sid, idx)
    return idx


# ---------------------------------------------------------------- per-shot picks(整图,不裁切)
def shot_view(key: dict, layout: dict, width: int, height: int) -> dict:
    """白模关键帧 → 罗盘朝向 / 俯仰 / 水平视场。"""
    from modules.shot_plates import bearing_deg, orientation_axes
    ex, ez, _ = orientation_axes(layout)
    pos, tgt = key['position'], key['target']
    dx, dy, dz = tgt[0] - pos[0], tgt[1] - pos[1], tgt[2] - pos[2]
    horiz = math.hypot(dx, dz) or 1e-6
    vfov = float(key['fov'])
    return {'bearing_deg': round(bearing_deg(dx, dz, ex, ez), 1), 'pitch_deg': round(math.degrees(math.atan2(dy, horiz)), 1),
            'fov_v_deg': round(vfov, 2), 'fov_h_deg': round(hfov_from_vfov(vfov, width, height), 1),
            'lens_mm_equiv': round(24 / (2 * math.tan(math.radians(vfov / 2))), 1),
            'position': [round(v, 3) for v in pos], 'target': [round(v, 3) for v in tgt]}


def dirs_covering(bearing: float, hfov_shot: float, hfov_plate: float) -> list:
    """本镜水平视窗落在哪几张平视图里:整窗在一张里 → 一张;跨边 → 相邻两张。"""
    lo, hi = bearing - hfov_shot / 2, bearing + hfov_shot / 2
    best = min(DIRS, key=lambda d: abs(((bearing - BEARINGS[d] + 180) % 360) - 180))
    delta = ((bearing - BEARINGS[best] + 180) % 360) - 180
    if abs(delta) + hfov_shot / 2 <= hfov_plate / 2 + FIT_MARGIN_DEG:
        return [best]
    other = DIRS[(DIRS.index(best) + (1 if delta > 0 else -1)) % 4]
    return [best, other]


def arc_dirs(b0: float, b1: float) -> list:
    """摇镜起点到终点短弧扫过的正方向(含端点最近向)。"""
    d = ((b1 - b0 + 180) % 360) - 180
    out = []
    steps = max(1, int(abs(d) / 5))
    for i in range(steps + 1):
        b = (b0 + d * i / steps) % 360
        best = min(DIRS, key=lambda x: abs(((b - BEARINGS[x] + 180) % 360) - 180))
        if best not in out:
            out.append(best)
    return out


def plate_fit(view: dict, plate_hfov: float, d: str) -> dict:
    """本镜视窗相对方向 d 平视图的偏角与是否整窗落在图内(仅记录,不裁切)。"""
    delta = ((view['bearing_deg'] - BEARINGS[d] + 180) % 360) - 180
    return {'delta_deg': round(delta, 1), 'fits': abs(delta) + view['fov_h_deg'] / 2 <= plate_hfov / 2 + FIT_MARGIN_DEG}


def pick_plates(cam: dict, layout: dict, idx: dict) -> dict:
    width, height = idx['plate_size']
    plate_hfov = idx['fov_h_deg']
    kf = cam['keyframes']
    v0, v1 = shot_view(kf[0], layout, width, height), shot_view(kf[-1], layout, width, height)
    cat = cam['category']
    if cat in ('static', 'push_pull'):
        picks = [(d, 'start') for d in dirs_covering(v0['bearing_deg'], v0['fov_h_deg'], plate_hfov)]
        reason = f"{cat}:朝向 {v0['bearing_deg']}° 视场 {v0['fov_h_deg']}° → {len(picks)} 张"
    elif cat == 'pan_tilt':
        ds = []
        for d in dirs_covering(v0['bearing_deg'], v0['fov_h_deg'], plate_hfov) + arc_dirs(v0['bearing_deg'], v1['bearing_deg']) \
                + dirs_covering(v1['bearing_deg'], v1['fov_h_deg'], plate_hfov):
            if d not in ds:
                ds.append(d)
        picks = [(d, 'sweep') for d in ds]
        reason = f"pan_tilt:{v0['bearing_deg']}° → {v1['bearing_deg']}° 扫过 {len(picks)} 张"
    else:
        picks = [(d, 'start') for d in dirs_covering(v0['bearing_deg'], v0['fov_h_deg'], plate_hfov)]
        for d in dirs_covering(v1['bearing_deg'], v1['fov_h_deg'], plate_hfov):
            if all(d != p[0] for p in picks):
                picks.append((d, 'end'))
        reason = f"{cat}:起点 {v0['bearing_deg']}° / 终点 {v1['bearing_deg']}° → {len(picks)} 张"
    out = []
    for d, role in picks:
        p = idx.get('plates', {}).get(d) or {}
        view = v1 if role == 'end' else v0
        entry = {'dir': d, 'role': role, 'file': f"{DIRNAME}/{p.get('file', d + '.png')}", 'missing': not p}
        entry.update(plate_fit(view, plate_hfov, d))
        out.append(entry)
    return {'group_id': cam['group_id'], 'shot_id': cam['shot_id'], 'scene_id': idx['scene_id'], 'movement': cam.get('movement'),
            'category': cat, 'bearing_start': v0['bearing_deg'], 'bearing_end': v1['bearing_deg'], 'pitch_start': v0['pitch_deg'],
            'fov_v_deg': v0['fov_v_deg'], 'fov_h_deg': v0['fov_h_deg'], 'lens_mm_equiv': v0['lens_mm_equiv'],
            'camera_start': {'position': v0['position'], 'target': v0['target']},
            'camera_end': {'position': v1['position'], 'target': v1['target']},
            'station_distance_m': round(math.hypot(v0['position'][0] - idx['station']['position_m'][0], v0['position'][2] - idx['station']['position_m'][2]), 2),
            'picks': out, 'reason': reason, 'written_at': _now()}


def episode_index_path(base: Path, ep: str) -> Path:
    return base / 'directing' / ep / 'cardinal_plates.json'


def assign_episode(base: Path, sid: str, ep: str, groups=None, scene_no=None, *, log=print) -> dict:
    """本集本场景各镜选图(整图,不裁切)→ directing/<ep>/cardinal_plates.json。"""
    idx = load_index(base, sid)
    if not idx.get('station'):
        raise FileNotFoundError(f'{sid}: cardinal/index.json 无站点,先 prepare/出图')
    layout = read(base / 'assets/concepts/scenes' / component(sid) / 'layout.json', {}) or {}
    cams = {}
    for c in scene_cameras(base, sid, ep, groups, scene_no):
        cams.setdefault(c['shot_id'], c)
    path = episode_index_path(base, ep)
    doc = read(path, {}) or {}
    if doc.get('schema_version') and doc['schema_version'] != SCHEMA_EPISODE:
        bak = path.with_name(f"cardinal_plates.{doc['schema_version'].rsplit('.', 1)[-1]}.json")
        path.rename(bak)
        log(f'   旧集索引 {doc["schema_version"]} 已改名为 {bak.name}')
        doc = {}
    doc.update({'schema_version': SCHEMA_EPISODE, 'ep': ep})
    shots = doc.setdefault('shots', {})
    # 本次没重算的同场景旧条目(按组/场次过滤时留下的)只刷新缺图标记,免得 CLI 误报缺图
    for rec in shots.values():
        if rec.get('scene_id') == sid:
            for p in rec.get('picks', []):
                pl = idx.get('plates', {}).get(p['dir']) or {}
                p['missing'] = not pl
                p['file'] = f"{DIRNAME}/{pl.get('file', p['dir'] + '.png')}"
    for sid_, cam in sorted(cams.items()):
        rec = pick_plates(cam, layout, idx)
        shots[sid_] = rec
        names = ', '.join(f"{p['dir']}" + ('' if p['fits'] else f"(Δ{p['delta_deg']}° 出边)") + ('(缺图)' if p['missing'] else '') for p in rec['picks'])
        log(f"   {sid_} {rec['group_id']} {rec['category']:9s} 朝向 {rec['bearing_start']:6.1f}° {rec['lens_mm_equiv']:5.1f}mm → {names}   {rec['reason']}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    log(f'saved: {path.relative_to(base)}')
    return doc


# ---------------------------------------------------------------- review page
def write_review(base: Path, sid: str, ep: str, *, log=print) -> Path:
    idx = load_index(base, sid)
    doc = read(episode_index_path(base, ep), {}) or {}
    out = cardinal_dir(base, sid)
    st = idx.get('station', {})
    rows = []
    for shot_id, s in sorted((doc.get('shots') or {}).items()):
        if s.get('scene_id') != sid:
            continue
        cells = ''.join(
            f"<figure><img src='{Path(p['file']).name}'><figcaption>{p['dir']} · {p['role']} · 整图 · Δ{p['delta_deg']}°"
            f"{'' if p['fits'] else ' · 本镜视窗出边'}</figcaption></figure>"
            for p in s['picks'])
        rows.append(f"<section><h3>{shot_id} <small>{s['group_id']} · {s['category']} · 朝向 {s['bearing_start']}°"
                    f"{' → ' + str(s['bearing_end']) + '°' if s['bearing_end'] != s['bearing_start'] else ''} · {s['lens_mm_equiv']} mm · "
                    f"俯仰 {s['pitch_start']}° · 离站点 {s['station_distance_m']} m</small></h3><div class=row>{cells}</div><p class=why>{s['reason']}</p></section>")
    sts = idx.get('stations') or {}
    plates = ''.join(
        f"<figure><img src='{idx['plates'][d]['file']}'><figcaption>{d} · {BEARINGS[d]:.0f}° · 视点 {sts.get(d, {}).get('position_m')} · seed {idx['plates'][d].get('seed')}</figcaption></figure>"
        if idx.get('plates', {}).get(d) else f"<figure><div class=missing>{d}:未出</div></figure>" for d in DIRS)
    maps = ''.join(f"<figure><img src='{idx['viewpoint_maps'][d]}'><figcaption>标点 {d}</figcaption></figure>" for d in DIRS if idx.get('viewpoint_maps'))
    html = f"""<!doctype html><meta charset=utf-8><title>cardinal plates {sid} {ep}</title>
<style>body{{font:14px/1.5 -apple-system,sans-serif;margin:20px;background:#111;color:#ddd}}h2,h3{{margin:18px 0 6px}}small{{color:#9ab;font-weight:400}}
.row{{display:flex;flex-wrap:wrap;gap:10px}}figure{{margin:0;max-width:600px}}figure img{{width:100%;display:block;background:#000}}figcaption{{color:#9ab;font-size:12px}}
.missing{{width:560px;height:315px;display:flex;align-items:center;justify-content:center;border:1px dashed #555;color:#777}}.why{{color:#8a9;font-size:12px}}</style>
<h2>{sid} · 视点 = 俯视图正中心 {st.get('position_m')} m (norm {st.get('xy_norm')}) · {idx.get('lens_mm_equiv')} mm ≈ {idx.get('fov_h_deg')}°×{idx.get('fov_v_deg')}° · {idx.get('time_of_day')} · 太阳 {idx.get('sun')}</h2>
<h3>四向平视图</h3><div class=row>{plates}</div>
<h3>标点俯视图(唯一参考图)</h3><div class=row>{maps}</div>
<h2>{ep} 逐镜选图</h2>{''.join(rows)}"""
    f = out / 'review.html'
    f.write_text(html, encoding='utf-8')
    log(f'review: {f}')
    return f
