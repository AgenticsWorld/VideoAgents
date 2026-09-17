"""场景正/反向双图(scene pair plates,实验 v1,2026-09-14)。

思路(用户定,offer SCN-0006 神庙偏殿首测):
  1. 先出一张「正向」平视广角空场景图:站在入口(门洞)内一步,朝殿内看(西→东),整间屋的主体陈设一次入画。
  2. 再以正向图为母版,出一张「反向」回望图:站在屋子远端,朝入口回望,补全正向图缺的那面墙(门、窗等开口的位置尤其要对)。
  3. 分镜组的视频 prompt 只挂这两张整图(替换掉俯视动线图 + 九宫格),每个 Shot 在正文里点名用哪一张,
     人物位置与镜头角度靠文字约束(站位句 / 机位句 / 布局图 views[].desc_en)。
不依赖白模(SCN-0006 没有白模与全景,现行 shot_plates 母图链跑不起来);几何只用 layout.json(地标归一化坐标 + 比例尺)。
与 modules/cardinal_plates.py(四向、视点在正中心、只挂标点图)并行,都是实验链路,不接进 p6/p7 工作流、不改机检。

数据:assets/concepts/scenes/<sid>/pair/
  index.json                 schema scene_pair_plates.v1:dims_m、lens_mm_equiv、fov_h_deg/fov_v_deg、plate_size、height_m、
                             scheme_id、time_of_day、sun、seed、stations{front|reverse}{at_norm, at_m, look_norm, look_id, bearing_deg, px}、
                             viewpoint_maps{role}、plates{role}{file, refs, prompt, negative, seed, channel, size, written_at}
  viewpoint_<role>.jpg       俯视图叠站位红点 + 本向视锥 + 方位字母(标点图)
  <role>.png / <role>.json / <role>.prompt.txt
集索引:directing/<ep>/pair_plates.json(schema scene_pair_plates_episode.v1):每镜 tile、bearing、pick、delta_deg、fits、weak、view
坐标约定:layout.json 归一化 (u, v),u 向右、v 向下,原点左上;米制按 orientation.scale_m(或 --dims)折算;罗盘按 orientation 四边(缺省上北下南)。
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re
import subprocess
import sys
import time
from pathlib import Path

from modules.whitebox import component, read, image_size
from modules.shot_plates import (orientation_axes, bearing_deg, compass, cardinal, strip_compass, sun_relative,
                                 _SPATIAL_RE, _GRID_RE, _MAPUSE_RE, _MAPONLY_RE, _TILE_RE, _remap_images, GC_KEY)
from modules.cardinal_plates import lens_fov, NEGATIVE as CARDINAL_NEGATIVE
from modules.prompt_layout import paragraphize

SCHEMA = 'scene_pair_plates.v1'
SCHEMA_EPISODE = 'scene_pair_plates_episode.v1'
DIRNAME = 'pair'
ROLES = ['front', 'reverse']
DEFAULT_LENS_MM = 12.0
PLATE_SIZE = '2560x1440'
STATION_HEIGHT_M = 1.5
FRONT_INSET_M = 0.6           # 正向站位:入口向屋内推进的距离
REVERSE_BACKOFF_M = 1.0       # 反向站位:沿正向轴线离看向地标退回的距离
FIT_MARGIN_DEG = 2.0
WEAK_DELTA_DEG = 60.0         # 镜的朝向与所选图偏角超过此值 → weak(文字承担构图)
DEFAULT_SHOT_LENS_MM = 50.0
BLOCK_KEY = 'Scene plates:'
LINE_KEY = 'Scene plate:'
REGISTER_BY_TOD = {'日': 'day', '昼': 'day', '白天': 'day', '黄昏': 'dusk', '傍晚': 'dusk', '夜': 'night', '夜晚': 'night',
                   '黎明': 'dawn', '清晨': 'dawn'}
GC_EXTRA = "no top-down or bird's-eye view, no map or floor-plan imagery, no tiled grid or contact sheet"
GC_EXTRA_MAP = "no diagram, no coloured blocks, no labels, letters or arrows from the plan"

MAP_RULE = ("[Image {n}] is the top-down plan of this location with the camera standpoint marked as a red dot and this picture's field "
            "of view drawn as a red wedge pointing {compass}: use it only to decide what lies in each direction, how far away it is and "
            "where it falls across the frame. Never reproduce the plan, its top-down viewpoint, colours, wedge, letters or graphics.")
MIRROR_RULE = ("[Image 1] is this exact same room photographed from the opposite end — standing just inside the {entrance} looking in. "
               "This picture is taken from the far end of the room looking back at that {entrance}: everything that was far away in "
               "[Image 1] is now close to the camera, and the {entrance} that was behind the camera in [Image 1] is now in the middle of "
               "this frame. Match [Image 1] exactly in materials, colours, weathering, dust, light quality and every fixed element "
               "(walls, floor, beams, furniture, idols), but do NOT copy its composition or viewpoint — this is the reverse angle.")

_MARKERS_RE = re.compile(r'\s*Map markers:[^.]*\.')
_ROUTE_MAP_RE = re.compile(r' route on the map:')
_BLOCK_RE = re.compile(r'\s*' + re.escape(BLOCK_KEY) + r'.*?(?:floor-plan view of the room\.(?:\s*\[Image\s*\d+\] is a schematic top-down floor plan.*?never shows the room from above\.)?|never show the room from above\.)', re.S)
_LINE_RE = re.compile(r'\s*' + re.escape(LINE_KEY) + r' this shot (?:uses|looks the other way from) \[Image\s*\d+\].*?Camera of this shot:[^.]*\.(?:[^.]*from the plate\.)?(?:[^.]*follow the plan \[Image\s*\d+\]\.)?', re.S)
_TILE_WORD_RE = re.compile(r'tile\s*(\d)(?!\s*of\s*\[)(?!（)')


def _now():
    return dt.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')


OVERRIDE_FILE = 'layout_override.json'


def load_layout(base: Path, sid: str) -> dict:
    """layout.json 与 pair/layout_override.json 合并:覆盖文件可整体替换 landmarks / views,并给
    entrance_beyond(门洞外看到什么)、architecture_desc(替换建筑描述)、extra_rules(追加句)。只影响出图与本链路的选图,不改 bible。"""
    sdir = base / 'assets/concepts/scenes' / component(sid)
    layout = read(sdir / 'layout.json', {}) or {}
    ov = read(sdir / DIRNAME / OVERRIDE_FILE, {}) or {}
    if ov:
        merged = dict(layout)
        for k in ('landmarks', 'views', 'orientation', 'scene_name', 'scene_name_en'):
            if k in ov:
                merged[k] = ov[k]
        merged['_override'] = {k: ov[k] for k in ('entrance_beyond', 'architecture_desc', 'extra_rules', 'note', 'shots', 'substitutions', 'global_constraints_extra', 'groups') if k in ov}
        merged['_override_file'] = str((sdir / DIRNAME / OVERRIDE_FILE).relative_to(base))
        return merged
    return layout


def pair_dir(base: Path, sid: str) -> Path:
    return base / 'assets/concepts/scenes' / component(sid) / DIRNAME


def load_index(base: Path, sid: str) -> dict:
    return read(pair_dir(base, sid) / 'index.json', {}) or {}


def save_index(base: Path, sid: str, idx: dict):
    d = pair_dir(base, sid)
    d.mkdir(parents=True, exist_ok=True)
    idx['schema_version'] = SCHEMA
    idx['scene_id'] = sid
    (d / 'index.json').write_text(json.dumps(idx, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


# ---------------------------------------------------------------- geometry(layout.json only)
def dims_m(layout: dict, override=None) -> list:
    """图幅折米:--dims 优先;否则从 orientation.scale_m 文字里取前两个「N m」。"""
    if override:
        return [float(override[0]), float(override[1])]
    text = str((layout.get('orientation') or {}).get('scale_m') or '')
    nums = re.findall(r'(\d+(?:\.\d+)?)\s*(?:m\b|米)', text)
    if len(nums) >= 2:
        return [float(nums[0]), float(nums[1])]
    raise ValueError('layout.json#orientation.scale_m 里找不到「N m × M m」,请用 --dims 8,7 指定图幅折米')


def to_m(p, dims):
    return [p[0] * dims[0], p[1] * dims[1]]


def to_norm(m, dims):
    return [round(m[0] / dims[0], 4), round(m[1] / dims[1], 4)]


def bearing_between(a_norm, b_norm, dims, ex, ez) -> float:
    dx, dz = (b_norm[0] - a_norm[0]) * dims[0], (b_norm[1] - a_norm[1]) * dims[1]
    return bearing_deg(dx, dz, ex, ez)


def dist_between(a_norm, b_norm, dims) -> float:
    return math.hypot((b_norm[0] - a_norm[0]) * dims[0], (b_norm[1] - a_norm[1]) * dims[1])


def signed_delta(bearing: float, axis: float) -> float:
    return ((bearing - axis + 180) % 360) - 180


def landmarks_of(layout: dict) -> list:
    return [lm for lm in layout.get('landmarks', []) if isinstance(lm.get('xy'), list) and len(lm['xy']) == 2]


def entrance_of(layout: dict) -> dict | None:
    lms = landmarks_of(layout)
    for lm in lms:
        if str(lm.get('kind') or '').lower() == 'entrance':
            return lm
    for lm in lms:
        if re.search(r'door|gate|entrance', str(lm.get('name_en') or ''), re.I):
            return lm
    return None


def default_stations(layout: dict, dims: list, ex, ez, opts: dict) -> dict:
    """正向:入口内推 FRONT_INSET_M,看向离入口最远的 point 型地标;反向:沿同一轴线离看向地标退 REVERSE_BACKOFF_M,回望入口。
    opts 里的 front_at/front_look/reverse_at/reverse_look(归一化坐标或地标 id)逐项覆盖。"""
    lms = {lm['id']: lm for lm in landmarks_of(layout)}

    def resolve(v):
        if v is None:
            return None, None
        if isinstance(v, str):
            if v in lms:
                return list(lms[v]['xy']), v
            raise ValueError(f'地标 {v!r} 不在 layout.json#landmarks')
        return [float(v[0]), float(v[1])], None

    ent = entrance_of(layout)
    ent_xy = list(ent['xy']) if ent else [0.5, 0.5]
    front_look, front_look_id = resolve(opts.get('front_look'))
    if front_look is None:
        far = max((lm for lm in lms.values() if str(lm.get('geom') or 'point') == 'point' and lm is not ent),
                  key=lambda lm: dist_between(ent_xy, lm['xy'], dims), default=None)
        if far is None:
            raise ValueError('layout.json 没有可作看向点的 point 型地标,请用 --front-look 指定')
        front_look, front_look_id = list(far['xy']), far['id']
    front_at, _ = resolve(opts.get('front_at'))
    if front_at is None:
        a, b = to_m(ent_xy, dims), to_m(front_look, dims)
        d = math.hypot(b[0] - a[0], b[1] - a[1]) or 1e-6
        front_at = to_norm([a[0] + (b[0] - a[0]) / d * FRONT_INSET_M, a[1] + (b[1] - a[1]) / d * FRONT_INSET_M], dims)
    reverse_look, reverse_look_id = resolve(opts.get('reverse_look'))
    if reverse_look is None:
        reverse_look, reverse_look_id = ent_xy, (ent['id'] if ent else None)
    reverse_at, _ = resolve(opts.get('reverse_at'))
    if reverse_at is None:
        a, b = to_m(front_at, dims), to_m(front_look, dims)
        d = math.hypot(b[0] - a[0], b[1] - a[1]) or 1e-6
        reverse_at = to_norm([b[0] - (b[0] - a[0]) / d * REVERSE_BACKOFF_M, b[1] - (b[1] - a[1]) / d * REVERSE_BACKOFF_M], dims)
    out = {}
    for role, at, look, look_id in (('front', front_at, front_look, front_look_id), ('reverse', reverse_at, reverse_look, reverse_look_id)):
        out[role] = {'at_norm': [round(at[0], 4), round(at[1], 4)], 'at_m': [round(v, 2) for v in to_m(at, dims)],
                     'look_norm': [round(look[0], 4), round(look[1], 4)], 'look_id': look_id,
                     'bearing_deg': round(bearing_between(at, look, dims, ex, ez), 1),
                     'entrance_id': ent['id'] if ent else None}
    return out


def nearest_landmark(layout: dict, at_norm, dims, exclude=()) -> dict | None:
    cands = [lm for lm in landmarks_of(layout) if lm['id'] not in exclude]
    return min(cands, key=lambda lm: dist_between(at_norm, lm['xy'], dims), default=None)


# ---------------------------------------------------------------- schematic plan(布局覆盖时代替旧俯视图)
KIND_COLOR = {'furniture': (150, 110, 60), 'prop': (120, 60, 60), 'feature': (170, 130, 40), 'ground': (225, 215, 195)}
KIND_SIZE_M = {'furniture': (0.9, 0.9), 'prop': (1.0, 1.0), 'feature': (0.8, 0.8), 'ground': (3.0, 3.0)}


def draw_schematic_plan(layout: dict, dims: list, out: Path, size=(2560, 1440), *, log=print) -> Path:
    """按地标坐标画干净的平面示意图:浅色地面、深色墙、门窗在墙上开缺口、陈设按类别画色块(area 型按 3 m 方块);无文字。
    布局覆盖后旧 layout_top.png 画的是旧陈设,会与文字打架,标点图改叠在这张图上。"""
    im = schematic_image(layout, dims, size)
    im.save(out, quality=92)
    log(f'   示意平面图:{out.name}(按 {layout.get("_override_file", "layout")} 地标现画,无文字)')
    return out


def schematic_image(layout: dict, dims: list, size=(2560, 1440)):
    from PIL import Image, ImageDraw
    W, H = size
    im = Image.new('RGB', (W, H), (235, 228, 214))
    dr = ImageDraw.Draw(im)
    t = max(10, W // 90)                                     # 墙厚(px)
    dr.rectangle([0, 0, W - 1, H - 1], outline=(60, 50, 40), width=t)
    dr.rectangle([t, t, W - t, H - t], fill=(200, 190, 172))
    ppm = (W / dims[0], H / dims[1])                          # 每米像素
    for lm in landmarks_of(layout):
        u, v = lm['xy']
        kind = str(lm.get('kind') or '').lower()
        px, py = u * W, v * H
        if kind in ('entrance', 'opening'):
            wall = cardinal(bearing_between([0.5, 0.5], lm['xy'], dims, [1, 0], [0, -1]))
            half = (0.55 if kind == 'entrance' else 0.7) * (ppm[0] if wall in ('north', 'south') else ppm[1])
            col = (250, 250, 240) if kind == 'entrance' else (255, 235, 150)
            if wall in ('north', 'south'):
                y0, y1 = (0, t) if wall == 'north' else (H - t, H)
                dr.rectangle([px - half, y0, px + half, y1], fill=col)
            else:
                x0, x1 = (0, t) if wall == 'west' else (W - t, W)
                dr.rectangle([x0, py - half, x1, py + half], fill=col)
            continue
        if kind == 'ground':
            sw, sh = KIND_SIZE_M['ground']
            dr.rectangle([px - sw / 2 * ppm[0], py - sh / 2 * ppm[1], px + sw / 2 * ppm[0], py + sh / 2 * ppm[1]], fill=KIND_COLOR['ground'])
            continue
        if kind == 'furniture' and str(lm.get('geom') or 'point') == 'area':
            # 沿墙的面状家具(炕):贴最近的墙,长 4.4 m、进深 1.8 m
            wall = cardinal(bearing_between([0.5, 0.5], lm['xy'], dims, [1, 0], [0, -1]))
            L, D = 4.4 * ppm[0], 1.8 * ppm[1]
            if wall == 'south':
                dr.rectangle([px - L / 2, H - t - D, px + L / 2, H - t], fill=KIND_COLOR['furniture'])
            elif wall == 'north':
                dr.rectangle([px - L / 2, t, px + L / 2, t + D], fill=KIND_COLOR['furniture'])
            elif wall == 'east':
                dr.rectangle([W - t - D, py - L / 2, W - t, py + L / 2], fill=KIND_COLOR['furniture'])
            else:
                dr.rectangle([t, py - L / 2, t + D, py + L / 2], fill=KIND_COLOR['furniture'])
            continue
        sw, sh = KIND_SIZE_M.get(kind, (0.8, 0.8))
        box = [px - sw / 2 * ppm[0], py - sh / 2 * ppm[1], px + sw / 2 * ppm[0], py + sh / 2 * ppm[1]]
        if kind == 'prop':
            dr.ellipse(box, fill=KIND_COLOR['prop'], outline=(50, 30, 30), width=3)
        else:
            dr.rectangle(box, fill=KIND_COLOR.get(kind, (120, 120, 120)), outline=(50, 40, 30), width=3)
    return im


LABEL_WORDS = ['chair', 'kang', 'idol', 'drum', 'door', 'window', 'table', 'sofa', 'bed', 'stage', 'gate', 'tree', 'wall']
CHAR_COLORS = [(220, 40, 40), (40, 90, 220), (30, 160, 70), (230, 140, 20), (150, 40, 180)]


def _short_label(lm: dict, layout: dict, dims: list, ex, ez) -> str:
    kind = str(lm.get('kind') or '').lower()
    name = str(lm.get('name_en') or lm.get('id') or '').lower()
    if kind == 'entrance':
        return 'door'
    if kind == 'opening':
        wall = cardinal(bearing_between([0.5, 0.5], lm['xy'], dims, ex, ez))
        return f'{wall[0].upper()} window'
    for w in LABEL_WORDS:
        if w in name:
            return w
    return name.split()[-1] if name else lm.get('id', '')


def draw_blocking_plan(layout: dict, dims: list, gid: str, blocking: list, out: Path, size=(2560, 1440), *, log=print) -> Path:
    """B 路线标注俯视图(纯 PIL,零生成):示意平面图 + 道具英文标签 + 角色起点(圆)/终点(方)字母标记与箭头 + 罗盘 N。
    blocking[] = {id, label, letter, start_xy, end_xy}(归一化坐标,按当前布局手写)。"""
    from PIL import ImageDraw, ImageFont
    im = schematic_image(layout, dims, size)
    W, H = im.size
    dr = ImageDraw.Draw(im)
    r = max(30, W // 55)                       # 角色标记半径
    try:
        font = ImageFont.truetype('/System/Library/Fonts/Helvetica.ttc', max(26, W // 60))
        big = ImageFont.truetype('/System/Library/Fonts/Helvetica.ttc', int(r * 1.3))
    except Exception:  # noqa: BLE001
        font = big = ImageFont.load_default()
    ex, ez, _ = orientation_axes(layout)
    t = max(10, W // 90)

    def tag(x, y, text, fnt=font, fg=(20, 20, 20), bg=(255, 255, 255)):
        tw, th = dr.textbbox((0, 0), text, font=fnt)[2:]
        x = min(max(x, tw / 2 + 6), W - tw / 2 - 6); y = min(max(y, th / 2 + 6), H - th / 2 - 6)
        dr.rectangle([x - tw / 2 - 8, y - th / 2 - 5, x + tw / 2 + 8, y + th / 2 + 7], fill=bg, outline=(60, 60, 60), width=2)
        dr.text((x - tw / 2, y - th / 2), text, fill=fg, font=fnt)

    for lm in landmarks_of(layout):
        kind = str(lm.get('kind') or '').lower()
        if kind == 'ground':
            continue
        u, v = lm['xy']; px, py = u * W, v * H
        label = _short_label(lm, layout, dims, ex, ez)
        if kind in ('entrance', 'opening'):
            wall = cardinal(bearing_between([0.5, 0.5], lm['xy'], dims, ex, ez))
            off = t + 40
            px, py = {'north': (px, off), 'south': (px, H - off), 'west': (off + 40, py), 'east': (W - off - 40, py)}[wall]
        elif not (kind == 'furniture' and str(lm.get('geom') or 'point') == 'area'):
            ppm_y = H / dims[1]
            py = py - KIND_SIZE_M.get(kind, (0.8, 0.8))[1] / 2 * ppm_y - 30      # 点状陈设:标签贴色块上沿,给角色标记让位
        tag(px, py, label)
    # 起点 / 终点重合的角色合并成一个标记(字母并排),箭头各画各的
    markers = []                              # [x, y, shape, letters, color]
    def add_marker(x, y, shape, letter, col):
        for m in markers:
            if m[2] == shape and math.hypot(m[0] - x, m[1] - y) < r * 1.2:
                m[3] += letter                            # 同形同位:字母并排
                return
        while any(math.hypot(m[0] - x, m[1] - y) < r * 1.6 for m in markers):
            y += r * 1.9                                  # 异形同位(起点撞终点):往下错开
        markers.append([x, y, shape, letter, col])
    for i, ch in enumerate(blocking or []):
        col = CHAR_COLORS[i % len(CHAR_COLORS)]
        letter = ch.get('letter') or 'ABCDE'[i]
        sx, sy = ch['start_xy'][0] * W, ch['start_xy'][1] * H
        ex_, ey_ = (ch.get('end_xy') or ch['start_xy'])[0] * W, (ch.get('end_xy') or ch['start_xy'])[1] * H
        moved = math.hypot(ex_ - sx, ey_ - sy) > r
        if moved:
            dr.line([sx, sy, ex_, ey_], fill=col, width=6)
            ang = math.atan2(ey_ - sy, ex_ - sx)
            for d in (0.5, -0.5):
                dr.line([ex_, ey_, ex_ - r * 1.4 * math.cos(ang + d), ey_ - r * 1.4 * math.sin(ang + d)], fill=col, width=6)
        add_marker(sx, sy, 'circle', letter, col)
        if moved:
            add_marker(ex_, ey_, 'square', letter, col)
    for x, y, shape, letters, col in markers:
        tw, th = dr.textbbox((0, 0), letters, font=big)[2:]
        rr = max(r, tw / 2 + 14)
        x = min(max(x, rr + t), W - rr - t); y = min(max(y, rr + t), H - rr - t)
        if shape == 'circle':
            dr.ellipse([x - rr, y - rr, x + rr, y + rr], fill=col, outline=(255, 255, 255), width=4)
        else:
            dr.rectangle([x - rr, y - rr, x + rr, y + rr], fill=col, outline=(255, 255, 255), width=4)
        dr.text((x - tw / 2, y - th / 2 - 4), letters, fill=(255, 255, 255), font=big)
    # 罗盘 N(右上角)
    cx, cy, L = W - t - 110, t + 130, 80
    dr.line([cx, cy + L, cx, cy - L], fill=(200, 30, 30), width=6)
    dr.polygon([(cx, cy - L - 20), (cx - 22, cy - L + 30), (cx + 22, cy - L + 30)], fill=(200, 30, 30))
    tag(cx, cy - L - 60, 'N', big, fg=(200, 30, 30))
    im.save(out, quality=92)
    log(f'   标注俯视图:{out.name}(道具标签 + 角色 ' + ', '.join((c.get('letter') or 'ABCDE'[i]) + '=' + str(c.get('label') or c.get('id')) for i, c in enumerate(blocking or [])) + ')')
    return out


# ---------------------------------------------------------------- viewpoint map
def draw_viewpoint_map(layout_top: Path, out: Path, at_norm, bearing: float, hfov: float, role: str, *, log=print) -> str:
    from PIL import Image, ImageDraw, ImageFont
    im = Image.open(layout_top).convert('RGB')
    W, H = im.size
    px, py = at_norm[0] * W, at_norm[1] * H
    r = max(6, W // 220)
    L = min(W, H) * 0.32
    try:
        font = ImageFont.truetype('/System/Library/Fonts/Helvetica.ttc', max(28, W // 45))
    except Exception:  # noqa: BLE001
        font = ImageFont.load_default()
    ov = Image.new('RGBA', im.size, (0, 0, 0, 0))
    dr = ImageDraw.Draw(ov)
    a0, a1 = bearing - hfov / 2 - 90, bearing + hfov / 2 - 90          # PIL 角度:0 = 右(东),顺时针;图上北 = 上
    dr.pieslice([px - L, py - L, px + L, py + L], a0, a1, fill=(255, 40, 40, 70), outline=(255, 30, 30, 230), width=4)
    for ch, ux, uz in (('N', 0, -1), ('E', 1, 0), ('S', 0, 1), ('W', -1, 0)):
        ex_, ey_ = px + ux * L * 0.55, py + uz * L * 0.55
        dr.line([px, py, ex_, ey_], fill=(255, 30, 30, 200), width=4)
        tw, th = dr.textbbox((0, 0), ch, font=font)[2:]
        dr.rectangle([ex_ - tw / 2 - 8, ey_ - th / 2 - 6, ex_ + tw / 2 + 8, ey_ + th / 2 + 8], fill=(255, 255, 255, 235))
        dr.text((ex_ - tw / 2, ey_ - th / 2), ch, fill=(220, 20, 20, 255), font=font)
    dr.ellipse([px - r, py - r, px + r, py + r], fill=(255, 20, 20, 255), outline=(255, 255, 255, 255), width=3)
    im = Image.alpha_composite(im.convert('RGBA'), ov).convert('RGB')
    f = out / f'viewpoint_{role}.jpg'
    im.save(f, quality=90)
    log(f'   标点图:{f.name}(站位 px {int(px)},{int(py)},朝向 {bearing:.0f}°)')
    return f.name


# ---------------------------------------------------------------- prompt
def _flat(v):
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, list):
        return '; '.join(x for x in (_flat(i) for i in v) if x)
    if isinstance(v, dict):
        return '; '.join(x for x in (_flat(i) for i in v.values()) if x)
    return ''


def _lm_name(lm: dict) -> str:
    return re.sub(r'[(（].*?[)）]', '', str(lm.get('name_en') or lm.get('name') or lm.get('id'))).strip()


def inventory(layout: dict, dims: list, ex, ez, at_norm, bearing: float, hfov: float) -> dict:
    """逐地标:画内(含横向百分比)/边缘/画外;开口(entrance/opening)单列。"""
    hf = math.tan(math.radians(hfov / 2))
    inside, edge, outside, openings = [], [], [], []
    for lm in landmarks_of(layout):
        name = _lm_name(lm)
        d = dist_between(at_norm, lm['xy'], dims)
        if d < 0.5:
            inside.append(f'{name} right at the camera position.')
            continue
        b = bearing_between(at_norm, lm['xy'], dims, ex, ez)
        delta = signed_delta(b, bearing)
        kind = str(lm.get('kind') or '').lower()
        geom = str(lm.get('geom') or 'point')
        half = 12.0 if geom == 'area' else 0.0            # area 型地标给一点角度余量
        where = None
        if abs(delta) <= hfov / 2:
            col = int(round((math.tan(math.radians(delta)) / hf + 1) / 2 * 100))
            col = max(0, min(100, col))
            where = f'about {col}% across the frame from the left edge'
            inside.append(f'{name}: {d:.1f} m away to the {compass(b)}, {where}.')
        elif abs(delta) - half <= hfov / 2 + 8:
            side = 'right' if delta > 0 else 'left'
            where = f'just outside the {side} edge of the frame, only its near part shows at the {side} edge'
            edge.append(f'{name}: {d:.1f} m away to the {compass(b)}, {where}.')
        else:
            where = 'behind or beside the camera, not visible'
            outside.append(name)
        if kind in ('entrance', 'opening'):
            wall = cardinal(bearing_between([0.5, 0.5], lm['xy'], dims, ex, ez))
            beyond = (layout.get('_override') or {}).get('entrance_beyond') or 'through it the larger main hall beyond is visible'
            what = (f'an open doorway with no door leaf; {beyond}' if kind == 'entrance' else 'a window opening')
            openings.append(f'{name} ({what}) in the {wall} wall, {d:.1f} m from the camera to the {compass(b)} — {where}.')
    return {'inside': inside, 'edge': edge, 'outside': outside, 'openings': openings}


def wall_summary(layout: dict, dims: list, ex, ez) -> str:
    """四面墙各有哪些开口;没有开口的墙明说(实测不说就会被补一扇窗)。"""
    walls = {w: [] for w in ('north', 'east', 'south', 'west')}
    for lm in landmarks_of(layout):
        if str(lm.get('kind') or '').lower() in ('entrance', 'opening'):
            walls[cardinal(bearing_between([0.5, 0.5], lm['xy'], dims, ex, ez))].append(_lm_name(lm))
    parts = []
    for w, names in walls.items():
        parts.append(f'the {w} wall has {len(names)} opening{"s" if len(names) != 1 else ""}' + (f' ({", ".join(names)})' if names else ' — a solid wall with no door and no window'))
    return 'Wall by wall: ' + '; '.join(parts) + '.'


def _style(base: Path, time_of_day: str) -> tuple[str, str]:
    """(风格串, 风格负面串)。优先 style_fragment_en;否则 style_prompt_en.base(去人物/服饰/皮肤分句)+ register_<档>;
    两者都按逗号分句剔除浅景深/虚化(背景图须全幅清晰)。"""
    style = read(base / 'bible/style.json', {}) or {}
    neg = style.get('negative_prompt_en') or (style.get('style_prompt_en') or {}).get('negative') or ''
    if isinstance(neg, list):
        neg = ', '.join(str(x) for x in neg)
    frag = style.get('style_fragment_en')
    if not frag:
        sp = style.get('style_prompt_en') or {}
        reg = sp.get('register_' + REGISTER_BY_TOD.get(str(time_of_day or '').strip(), 'day')) or ''
        frag = ', '.join(x for x in (str(sp.get('base') or ''), str(reg)) if x)
    drop = re.compile(r'character|skin|hair|garment|fabric|embroider|face|costume|depth of field|bokeh', re.I)
    frag = ', '.join(c.strip() for c in str(frag).split(',') if c.strip() and not drop.search(c))
    return frag, str(neg)


def lighting_scheme(base: Path, sid: str, scheme_id: str | None) -> dict:
    doc = read(base / 'bible/scenes' / component(sid) / 'lighting.json', {}) or {}
    for s in doc.get('schemes', []):
        if not scheme_id or s.get('scheme_id') == scheme_id or s.get('id') == scheme_id:
            return s
    return {}


def _architecture(base: Path, sid: str, layout: dict | None = None) -> tuple[str, str]:
    """(材质/年代参考文字, 建筑负面串)。只取材质、屋面、开口样式、陈设基调——不取 details 里的方位描述(位置以 layout 为准)。"""
    arch = read(base / 'bible/scenes' / component(sid) / 'architecture.json', {}) or {}
    interior = arch.get('interior') or {}
    roof = arch.get('roof') or {}
    parts = [_flat(arch.get('form')), _flat(arch.get('materials')), _flat(arch.get('openings_style')),
             _flat(interior.get('furniture_tone')), _flat({k: roof.get(k) for k in ('form', 'covering')})]
    desc = ' '.join(p.rstrip('.。;') + '.' for p in parts if p)
    neg = arch.get('negative') or []
    ov = ((layout or {}).get('_override') or {}).get('architecture_desc')
    if ov:
        desc = str(ov).strip()
    return desc[:1500], ', '.join(str(x) for x in neg) if isinstance(neg, list) else str(neg)


def build_prompt(base: Path, sid: str, layout: dict, idx: dict, role: str, *, mirror_ref: bool = True) -> tuple[str, str]:
    """返回 (prompt, negative)。反向图 mirror_ref=True 时 [Image 1] = 正向成图、[Image 2] = 标点图;否则 [Image 1] = 标点图。"""
    st = idx['stations'][role]
    dims = idx['dims_m']
    ex, ez, texts = orientation_axes(layout)
    hfov, vfov, lens = idx['fov_h_deg'], idx['fov_v_deg'], idx['lens_mm_equiv']
    bearing = st['bearing_deg']
    ahead = cardinal(bearing)
    order = ['north', 'east', 'south', 'west']
    left, right, behind = order[(order.index(ahead) - 1) % 4], order[(order.index(ahead) + 1) % 4], order[(order.index(ahead) + 2) % 4]
    name = re.sub(r'[(（].*?[)）]', '', layout.get('scene_name_en') or layout.get('scene_name') or sid).strip()
    lms = {lm['id']: lm for lm in landmarks_of(layout)}
    look_name = _lm_name(lms[st['look_id']]) if st.get('look_id') in lms else f'the {compass(bearing)} side of the room'
    ent = lms.get(st.get('entrance_id') or '')
    ent_name = _lm_name(ent) if ent else 'entrance'
    near = nearest_landmark(layout, st['at_norm'], dims)
    near_d = dist_between(st['at_norm'], near['xy'], dims) if near else 0
    standing = (f'{near_d:.1f} m from {_lm_name(near)}' if near else 'inside the room')
    scheme = lighting_scheme(base, sid, idx.get('scheme_id'))
    style, style_neg = _style(base, idx.get('time_of_day'))
    arch_desc, arch_neg = _architecture(base, sid, layout)
    inv = inventory(layout, dims, ex, ez, st['at_norm'], bearing, hfov)
    n_map = 2 if (role == 'reverse' and mirror_ref) else 1

    parts = [f'Empty location background plate, one single full-frame still with nobody present and nothing moving. '
             f'Location: {name}. Time of day: {idx.get("time_of_day") or "day"}.',
             f'Camera: {lens:.0f}mm-equivalent rectilinear ultra-wide lens on a full-frame camera (horizontal field of view about '
             f'{hfov:.0f} degrees, vertical about {vfov:.0f} degrees), architectural straight-line perspective — every straight edge '
             f'stays perfectly straight, no fisheye or barrel distortion. The camera stands {standing}, lens {idx["height_m"]} m above '
             f'the floor, looking {compass(bearing)} straight at {look_name}.']
    if role == 'front':
        parts.append(f'This is the FRONT view of the room: the camera has just stepped in through {ent_name} and looks into the room, '
                     f'so {ent_name} is directly behind the camera and not visible.')
    else:
        parts.append(f'This is the REVERSE view of the room: the camera stands at the far end and looks back toward {ent_name}, '
                     f'which is near the middle of the frame; this picture shows the wall the front view could not.')
    parts.append('The view is NOT tilted in any way: the lens axis is perfectly horizontal (pitch 0°, not looking up, not looking down), '
                 'not rolled (no dutch angle), so the horizon is a level line at the exact mid-height of the frame and every vertical edge '
                 '(walls, posts, door frames, window mullions) stays perfectly vertical and parallel to the sides of the frame with no '
                 'keystone or converging verticals.')
    if role == 'reverse' and mirror_ref:
        parts.append(MIRROR_RULE.format(entrance=ent_name))
    parts.append(MAP_RULE.format(n=n_map, compass=compass(bearing)))
    # 四边文字只在 orientation 分别给出四边时才用(offer 这类只有 top_of_map 一句写全四面的,整句挂到某一边会误导)
    sides = {k: strip_compass(v) for k, v in texts.items() if v} if len([v for v in texts.values() if v]) >= 3 else {}
    parts.append(f'Straight ahead is the {ahead} side of the room' + (f': {sides[ahead]}' if sides.get(ahead) else '')
                 + f'. Frame left is {left}' + (f': {sides[left]}' if sides.get(left) else '')
                 + f'. Frame right is {right}' + (f': {sides[right]}' if sides.get(right) else '')
                 + f'. The {behind} side is behind the camera and NOT visible' + (f': {sides[behind]}' if sides.get(behind) else '') + '.')
    if inv['inside']:
        parts.append('Exact placement of everything in this frame, measured from the camera (frame column 0% = left edge, 50% = centre, '
                     '100% = right edge): ' + ' '.join(inv['inside']))
    if inv['edge']:
        parts.append('At the very edge of the frame: ' + ' '.join(inv['edge']))
    if inv['openings']:
        parts.append('Doors and windows — their walls and positions must match the plan exactly, one opening each, nothing added: '
                     + ' '.join(inv['openings']) + ' ' + wall_summary(layout, dims, ex, ez))
    singles = [_lm_name(lm) for lm in landmarks_of(layout)
               if str(lm.get('geom') or 'point') == 'point' and str(lm.get('kind') or '').lower() in ('prop', 'furniture', 'feature')]
    if singles:
        parts.append('Exactly one of each, never duplicated anywhere else in the room: ' + ', '.join(singles) + '.')
    if inv['outside']:
        parts.append('Not visible in this frame (behind or beside the camera, do not paint them in): ' + ', '.join(inv['outside']) + '.')
    for rule in (layout.get('_override') or {}).get('extra_rules') or []:
        parts.append(str(rule).strip())
    if idx.get('sun'):
        rel = sun_relative(idx['sun'], bearing)
        parts.append(f"The sun is in the {rel['compass']}, {rel['relative']}; shadows fall {rel['shadows']}.")
    if scheme.get('prompt_fragment_en'):
        parts.append('Lighting: ' + re.sub(r'^\s*lighting:\s*', '', str(scheme['prompt_fragment_en']).strip(), flags=re.I).rstrip('.') + '.')
    if arch_desc:
        parts.append('Materials and era (reference only; positions follow the plan and the list above): ' + arch_desc)
    parts.append('The floor is one continuous unbroken floor from wall to wall: no grate, no hatch, no trapdoor, no pit, no opening of any kind '
                 'in the floor — the bright slots drawn along the edges of the plan are windows standing in the walls, never anything on the floor.')
    parts.append('Empty location plate: no people, no characters, no human figures or silhouettes, no animals, no text, no watermark, '
                 'no grid lines, no split screen, one single rectilinear photograph, everything in sharp focus front to back.')
    if style:
        parts.append('Style: ' + style)
    prompt = '\n'.join(p.strip() for p in parts if p and p.strip())
    negative = ', '.join(x for x in (CARDINAL_NEGATIVE, 'floor grate, floor hatch, trapdoor, pit in the floor, skylight in the floor, window drawn on the floor', style_neg, arch_neg) if x)
    return prompt, negative


# ---------------------------------------------------------------- prepare / generate
def prepare(base: Path, sid: str, *, dims=None, lens_mm=DEFAULT_LENS_MM, size=PLATE_SIZE, height=STATION_HEIGHT_M, scheme_id=None,
            time_of_day=None, sun=None, stations_opts=None, force_geometry=False, log=print) -> tuple[dict, dict]:
    sdir = base / 'assets/concepts/scenes' / component(sid)
    layout = load_layout(base, sid)
    if not layout:
        raise FileNotFoundError(f'{sid}: {sdir / "layout.json"} 不存在')
    if layout.get('_override_file'):
        log(f"   布局覆盖:{layout['_override_file']}(只用于出图/选图,不改 bible)")
    layout_top = sdir / (layout.get('layout_top') or 'layout_top.png')
    if not layout_top.is_file():
        raise FileNotFoundError(f'{sid}: 俯视图 {layout_top} 不存在')
    width, height_px = (int(x) for x in size.lower().split('x'))
    hfov, vfov = lens_fov(lens_mm, width, height_px)
    ex, ez, _ = orientation_axes(layout)
    d = dims_m(layout, dims)
    if layout.get('_override_file') and 'landmarks' in (read(sdir / DIRNAME / OVERRIDE_FILE, {}) or {}):
        (sdir / DIRNAME).mkdir(parents=True, exist_ok=True)
        layout_top = draw_schematic_plan(layout, d, sdir / DIRNAME / 'plan_override.jpg', log=log)
    idx = load_index(base, sid)
    if idx and idx.get('schema_version') != SCHEMA:
        log(f"   旧索引 {idx.get('schema_version')} ≠ {SCHEMA},重算(旧图保留)")
        idx = {}
    stations = default_stations(layout, d, ex, ez, stations_opts or {})
    lsize = image_size(layout_top)
    for st in stations.values():
        st['px'] = [int(round(st['at_norm'][0] * lsize[0])), int(round(st['at_norm'][1] * lsize[1]))] if lsize else None
    old = idx.get('stations') or {}
    changed = (not old or any(old.get(r, {}).get('at_norm') != stations[r]['at_norm'] or old.get(r, {}).get('bearing_deg') != stations[r]['bearing_deg']
                              for r in ROLES) or idx.get('lens_mm_equiv') != lens_mm or idx.get('plate_size') != [width, height_px])
    out = pair_dir(base, sid)
    out.mkdir(parents=True, exist_ok=True)
    if changed or force_geometry or not all((out / f'viewpoint_{r}.jpg').is_file() for r in ROLES):
        maps = {r: draw_viewpoint_map(layout_top, out, stations[r]['at_norm'], stations[r]['bearing_deg'], hfov, r, log=log) for r in ROLES}
        keep = {}
        if changed and idx.get('plates'):
            gone = [r for r in idx['plates'] if old.get(r, {}).get('at_norm') != stations[r]['at_norm'] or old.get(r, {}).get('bearing_deg') != stations[r]['bearing_deg']]
            keep = {r: v for r, v in idx['plates'].items() if r not in gone}
            if gone:
                log(f"   站位/朝向变了,已成图作废(文件保留,索引移除):{', '.join(gone)}")
        idx = {**idx, 'stations': stations, 'viewpoint_maps': maps, 'plates': keep}
    else:
        idx.setdefault('plates', {})
        idx['stations'] = stations
    scheme = lighting_scheme(base, sid, scheme_id)
    idx.update({'dims_m': d, 'lens_mm_equiv': lens_mm, 'fov_h_deg': hfov, 'fov_v_deg': vfov, 'plate_size': [width, height_px],
                'height_m': height, 'scheme_id': scheme.get('scheme_id') or scheme_id,
                'time_of_day': time_of_day or (scheme.get('condition') or {}).get('time_of_day') or '',
                'sun': sun, 'layout_top': layout_top.name, 'planned_at': _now()})
    for r in ROLES:
        st = stations[r]
        log(f"   {r:7s} 站位 {st['at_norm']} ({st['at_m']} m) → 看向 {st['look_id'] or st['look_norm']},朝向 {st['bearing_deg']}° {compass(st['bearing_deg'])}")
    log(f'   {lens_mm:.0f} mm 等效 → 水平 {hfov}° / 垂直 {vfov}°,{width}x{height_px},站高 {height} m,光照 {idx["scheme_id"]},时段 {idx["time_of_day"]}')
    save_index(base, sid, idx)
    return idx, layout


def generate_plate(base: Path, sid: str, idx: dict, role: str, prompt: str, negative: str, refs: list, seed: int, *, log=print) -> dict:
    from PIL import Image
    from modules.genmedia import generate_image, get_config, image_pref_env
    with image_pref_env('scenes'):
        cfg = get_config('image')
    out = pair_dir(base, sid)
    target = out / f'{role}.png'
    if target.is_file():
        target.rename(out / f'{role}.prev-{dt.datetime.now().strftime("%H%M%S")}.png')
    width, height = idx['plate_size']
    log(f"   出 {role} 图 {cfg.get('provider')}/{cfg.get('model')} {width}x{height} seed {seed},参考图 {[Path(r).name for r in refs]} …")
    generate_image(prompt, str(target), negative=negative, refs=[str(r) for r in refs], size=f'{width}x{height}', seed=seed)
    im = Image.open(target)
    rec = {'file': target.name, 'role': role, 'bearing_deg': idx['stations'][role]['bearing_deg'], 'size': list(im.size), 'seed': seed,
           'channel': {'provider': cfg.get('provider'), 'model': cfg.get('model')},
           'refs': [str(Path(r).resolve().relative_to(base.resolve())) if str(Path(r).resolve()).startswith(str(base.resolve())) else str(r) for r in refs],
           'prompt': prompt, 'negative': negative, 'station': idx['stations'][role], 'written_at': _now()}
    (out / f'{role}.json').write_text(json.dumps(rec, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    log(f'saved: {target.relative_to(base)} ({im.size[0]}x{im.size[1]})')
    return rec


def run(base: Path, sid: str, *, only=None, seed=None, dry_run=False, force=False, mirror_ref=True, log=print, **kw) -> dict:
    idx, layout = prepare(base, sid, log=log, **kw)
    out = pair_dir(base, sid)
    if seed is None:
        import random
        seed = idx.get('seed') or random.randint(1, 2 ** 31 - 1)
    idx['seed'] = seed
    for role in ROLES:
        if only and role not in only:
            continue
        if idx['plates'].get(role) and (out / idx['plates'][role]['file']).is_file() and not force:
            log(f'   {role}: 已有,跳过(--force 重出)')
            continue
        use_mirror = mirror_ref and role == 'reverse'
        if use_mirror and not dry_run and not (out / 'front.png').is_file():
            raise FileNotFoundError('反向图以正向图为母版,先出 front.png(或 --no-mirror-ref)')
        refs = ([out / 'front.png'] if use_mirror else []) + [out / idx['viewpoint_maps'][role]]
        prompt, negative = build_prompt(base, sid, layout, idx, role, mirror_ref=use_mirror)
        (out / f'{role}.prompt.txt').write_text(prompt + '\n\nREFS: ' + ', '.join(r.name for r in refs) + '\n\nNEGATIVE: ' + negative + '\n', encoding='utf-8')
        if dry_run:
            log(f'   [dry-run] {role}: 参考图 {[r.name for r in refs]},提示词 {len(prompt)} 字 → {role}.prompt.txt')
            continue
        rec = generate_plate(base, sid, idx, role, prompt, negative, refs, seed, log=log)
        rec['mirror_ref'] = use_mirror
        idx['plates'][role] = rec
        save_index(base, sid, idx)
    save_index(base, sid, idx)
    return idx


# ---------------------------------------------------------------- per-shot picks
def group_record(base: Path, ep: str, gid: str) -> dict:
    sl = read(base / 'directing' / component(ep) / 'shot_list.json', {}) or {}
    g = next((g for g in sl.get('generation_groups', []) if g.get('group_id') == gid), None)
    if not g:
        raise KeyError(f'{ep}/{gid}: shot_list.json 里没有这个组')
    return g


def shot_tile(base: Path, ep: str, shot_id: str, prompt_text: str = '', override: dict | None = None) -> int | None:
    """镜的九格视角号:--tiles 覆盖 → composition.json#view_tile_relation → camera.json#view_tile → 组 prompt 里 framed like tile N。"""
    if override and shot_id in override:
        return int(override[shot_id])
    sdir = base / 'directing' / component(ep) / 'shots' / component(shot_id)
    comp = read(sdir / 'composition.json', {}) or {}
    m = re.search(r'tile\s*(\d)', str(comp.get('view_tile_relation') or comp.get('view_tile') or ''))
    if m:
        return int(m.group(1))
    cam = read(sdir / 'camera.json', {}) or {}
    if cam.get('view_tile'):
        m = re.search(r'(\d)', str(cam['view_tile']))
        if m:
            return int(m.group(1))
    return None


def shot_lens_mm(prompt_text: str, shot_no: int) -> float:
    seg = re.search(r'Shot\s*%d\s*[:：](.*?)(?=Shot\s*\d+\s*[:：]|Global constraints:|$)' % shot_no, prompt_text, re.S)
    m = re.search(r'(\d{2,3})\s*mm', seg.group(1) if seg else '')
    return float(m.group(1)) if m else DEFAULT_SHOT_LENS_MM


def pick_plate(bearing: float, shot_hfov: float, idx: dict) -> dict:
    best = min(ROLES, key=lambda r: abs(signed_delta(bearing, idx['stations'][r]['bearing_deg'])))
    delta = signed_delta(bearing, idx['stations'][best]['bearing_deg'])
    other = [r for r in ROLES if r != best][0]
    return {'pick': best, 'other': other, 'delta_deg': round(delta, 1),
            'fits': abs(delta) + shot_hfov / 2 <= idx['fov_h_deg'] / 2 + FIT_MARGIN_DEG, 'weak': abs(delta) > WEAK_DELTA_DEG}


def assign_group(base: Path, sid: str, ep: str, gid: str, *, tiles: dict | None = None, prompt_pack: dict | None = None, log=print) -> list:
    idx = load_index(base, sid)
    if not idx.get('stations'):
        raise FileNotFoundError(f'{sid}: pair/index.json 无站位,先 prepare/出图')
    layout = load_layout(base, sid)
    views = {int(v['tile']): v for v in layout.get('views', []) if v.get('tile') is not None}
    lms = {lm['id']: lm for lm in landmarks_of(layout)}
    g = group_record(base, ep, gid)
    if g.get('scene_id') not in (None, sid):
        raise ValueError(f"{gid} 的场景是 {g.get('scene_id')},不是 {sid}")
    pack = prompt_pack if prompt_pack is not None else (read(base / 'assets/prompts' / component(ep) / f'{gid}.json', {}) or {})
    vp = pack.get('video_prompt') or ''
    width, height = idx['plate_size']
    picks = []
    ov_shots = (layout.get('_override') or {}).get('shots') or {}
    grp_ov = ((layout.get('_override') or {}).get('groups') or {}).get(gid) or {}
    shot_ids = grp_ov.get('shots') or g.get('shots') or []      # 覆盖可把一镜拆成多镜(虚拟 shot id,tile 由 shots[id].tile 给)
    if grp_ov.get('shots'):
        log(f"   组覆盖:{gid} 镜列表 {shot_ids}(拆镜,原 {g.get('shots')})")
    for k, shot_id in enumerate(shot_ids, 1):
        tiles_k = dict(tiles or {})
        if ov_shots.get(shot_id, {}).get('tile'):
            tiles_k[shot_id] = ov_shots[shot_id]['tile']
        tile = shot_tile(base, ep, shot_id, vp, tiles_k)
        if tile is None:
            m = re.findall(r'framed like tile\s*(\d)', vp)
            tile = int(m[k - 1]) if len(m) >= k else None
        if tile is None or tile not in views:
            raise ValueError(f'{shot_id}: 找不到九格视角号(composition.json#view_tile_relation),用 --tiles {shot_id}=N 指定')
        v = views[tile]
        bearing = float(v.get('axis_bearing_deg'))
        lens = shot_lens_mm(vp, k)
        hfov = lens_fov(lens, width, height)[0]
        rec = {'group_id': gid, 'shot_id': shot_id, 'shot_no': k, 'scene_id': sid, 'tile': tile, 'bearing_deg': bearing,
               'lens_mm_equiv': lens, 'fov_h_deg': hfov,
               'view': {'camera_from': v.get('camera_from'), 'camera_from_en': _lm_name(lms[v['camera_from']]) if v.get('camera_from') in lms else v.get('camera_from'),
                        'looking_at': v.get('looking_at'), 'looking_at_en': _lm_name(lms[v['looking_at']]) if v.get('looking_at') in lms else v.get('looking_at'),
                        'axis_compass': v.get('axis_compass') or compass(bearing), 'desc_en': v.get('desc_en') or ''},
               'written_at': _now()}
        rec.update(pick_plate(bearing, hfov, idx))
        so = ((layout.get('_override') or {}).get('shots') or {}).get(shot_id) or {}
        if so.get('plate') in ROLES:
            forced = so['plate']
            delta = signed_delta(bearing, idx['stations'][forced]['bearing_deg'])
            rec.update({'pick': forced, 'other': [r for r in ROLES if r != forced][0], 'delta_deg': round(delta, 1),
                        'fits': abs(delta) + hfov / 2 <= idx['fov_h_deg'] / 2 + FIT_MARGIN_DEG, 'weak': abs(delta) > WEAK_DELTA_DEG, 'forced': True})
        if so.get('camera_en'):
            rec['view']['camera_en'] = str(so['camera_en']).strip().rstrip('.')
        rec['file'] = f"assets/concepts/scenes/{sid}/{DIRNAME}/{rec['pick']}.png"
        rec['other_file'] = f"assets/concepts/scenes/{sid}/{DIRNAME}/{rec['other']}.png"
        picks.append(rec)
        log(f"   Shot {k} {shot_id} tile {tile} 朝向 {bearing:6.1f}° {lens:.0f}mm → {rec['pick']:7s} Δ{rec['delta_deg']:+.1f}°"
            + ('' if rec['fits'] else ' 出边') + (' weak' if rec['weak'] else '') + (' [覆盖指定]' if rec.get('forced') else ''))
    path = base / 'directing' / component(ep) / 'pair_plates.json'
    doc = read(path, {}) or {}
    doc.update({'schema_version': SCHEMA_EPISODE, 'ep': ep})
    shots = doc.setdefault('shots', {})
    for rec in picks:
        shots[rec['shot_id']] = rec
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    log(f'saved: {path.relative_to(base)}')
    return picks


# ---------------------------------------------------------------- prompt rewrite
def _is_scene_map(ref: str) -> bool:
    name = Path(ref).name
    return ('/blocking_maps/' in ref or name.startswith('layout_top') or name.startswith('grid_9views')
            or f'/{DIRNAME}/' in ref and ref.startswith('assets/concepts/scenes/'))


def _plate_sentence(idx: dict, layout: dict, role: str, n: int) -> str:
    st = idx['stations'][role]
    dims = idx['dims_m']
    ex, ez, _ = orientation_axes(layout)
    lms = {lm['id']: lm for lm in landmarks_of(layout)}
    inv = inventory(layout, dims, ex, ez, st['at_norm'], st['bearing_deg'], idx['fov_h_deg'])
    names = [s.split(':')[0] for s in inv['inside']] + [s.split(':')[0] + ' (edge)' for s in inv['edge']]
    ent = _lm_name(lms[st['entrance_id']]) if st.get('entrance_id') in lms else 'the entrance'
    look = _lm_name(lms[st['look_id']]) if st.get('look_id') in lms else compass(st['bearing_deg'])
    where = (f'standing just inside {ent} looking {compass(st["bearing_deg"])} into the room toward {look}' if role == 'front'
             else f'standing at the far end of the room looking back {compass(st["bearing_deg"])} at {ent}')
    return (f'[Image {n}] is the {role} view: {where} — in frame: ' + ', '.join(names) + '.')


def build_block(idx: dict, layout: dict, n_front: int, n_reverse: int) -> str:
    return (f'{BLOCK_KEY} [Image {n_front}] and [Image {n_reverse}] are two empty wide-angle photographs of this one location with nobody in them. '
            + _plate_sentence(idx, layout, 'front', n_front) + ' ' + _plate_sentence(idx, layout, 'reverse', n_reverse)
            + ' Each Shot below names the one plate it uses: take only the architecture, openings, furniture placement, materials and lighting '
              'from that plate; the shot is a tighter view inside or beside it — frame it exactly as the Shot text describes and place the '
              'characters where the position text says. Never render a plate as-is, never freeze on an empty set, and never show a '
              'top-down, map or floor-plan view of the room.')


def plan_sentence(idx: dict, layout: dict, n_map: int, blocking: list) -> str:
    legend = ', '.join(f"{c.get('letter') or 'ABCDE'[i]} = {c.get('label') or c.get('id')}" for i, c in enumerate(blocking or []))
    props = ', '.join(sorted({_short_label(lm, layout, idx['dims_m'], *orientation_axes(layout)[:2]) for lm in landmarks_of(layout)
                              if str(lm.get('kind') or '').lower() != 'ground'}))
    return (f'[Image {n_map}] is a schematic top-down floor plan of the same room — a flat diagram with coloured blocks, labels and letters, '
            f'NOT a photograph and NOT a camera view. Use it only for spatial relationships: where the {props} stand relative to each other, '
            f'and where each character starts (circle) and ends (square): {legend}. Never render the plan, its top-down viewpoint, colours, '
            f'letters, labels or arrows; every shot is filmed from the ground-level camera described in that Shot, and never shows the room from above.')


def build_block_with_map(idx: dict, layout: dict, main_role: str, n_plate: int, n_map: int, blocking: list) -> str:
    legend = ', '.join(f"{c.get('letter') or 'ABCDE'[i]} = {c.get('label') or c.get('id')}" for i, c in enumerate(blocking or []))
    props = ', '.join(sorted({_short_label(lm, layout, idx['dims_m'], *orientation_axes(layout)[:2]) for lm in landmarks_of(layout)
                              if str(lm.get('kind') or '').lower() != 'ground'}))
    return (f'{BLOCK_KEY} ' + _plate_sentence(idx, layout, main_role, n_plate).replace(f'[Image {n_plate}] is the {main_role} view:',
            f'[Image {n_plate}] is an empty wide-angle photograph of this location with nobody in it, its {main_role} view:')
            + f' [Image {n_map}] is a schematic top-down floor plan of the same room — a flat diagram with coloured blocks, labels and letters, '
              f'NOT a photograph and NOT a camera view. Use it only for spatial relationships: where the {props} stand relative to each other, '
              f'and where each character starts (circle) and ends (square): {legend}. Never render the plan, its top-down viewpoint, colours, '
              f'letters, labels or arrows; every shot is filmed from the ground-level camera described in that Shot. '
              f'Take walls, floor, openings, furniture, materials and lighting from [Image {n_plate}], lay them out by the plan, frame each shot '
              f'exactly as its text describes and place the characters where the plan and the position text say. Never render the photograph '
              f'as-is, never freeze on an empty set, never show the room from above.')


def shot_line_map(rec: dict, idx: dict, main_role: str, n_plate: int, n_map: int) -> str:
    st = idx['stations'][main_role]
    where = (f'front view, looking {compass(st["bearing_deg"])} in from the entrance' if main_role == 'front'
             else f'reverse view, looking back {compass(st["bearing_deg"])} at the entrance from the far end')
    v = rec['view']
    cam = v['camera_en'].replace('.', ';') if v.get('camera_en') else (
        f'standing at {v["camera_from_en"]} looking {v["axis_compass"]} toward {v["looking_at_en"]} — {v["desc_en"].rstrip(".")}')
    if rec['pick'] == main_role:
        head = f'{LINE_KEY} this shot uses [Image {n_plate}] ({where}). Camera of this shot: {cam}'
    else:
        head = (f'{LINE_KEY} this shot looks the other way from [Image {n_plate}] ({where}) — take walls, materials, light and props from '
                f'[Image {n_plate}] and lay them out by the plan [Image {n_map}]. Camera of this shot: {cam}')
    return head + f'; character and prop positions follow the plan [Image {n_map}].'


def shot_line(rec: dict, idx: dict, n_pick: int, n_other: int) -> str:
    st = idx['stations'][rec['pick']]
    where = (f'front view, looking {compass(st["bearing_deg"])} in from the entrance' if rec['pick'] == 'front'
             else f'reverse view, looking back {compass(st["bearing_deg"])} at the entrance from the far end')
    v = rec['view']
    if v.get('camera_en'):
        return (f'{LINE_KEY} this shot uses [Image {n_pick}] ({where}) and not [Image {n_other}]. '
                f'Camera of this shot: {v["camera_en"].replace(".", ";")}.')
    text = (f'{LINE_KEY} this shot uses [Image {n_pick}] ({where}) and not [Image {n_other}]. '
            f'Camera of this shot: standing at {v["camera_from_en"]} looking {v["axis_compass"]} toward {v["looking_at_en"]} — {v["desc_en"].rstrip(".")}.')
    if rec.get('weak'):
        text += (f' {v["looking_at_en"]} lies mostly outside that plate\'s frame: build this framing from the text above, '
                 'and keep only the room\'s materials, walls and light from the plate.')
    return text


def apply_group_prompt(prompt: dict, picks: list, idx: dict, layout: dict, sid: str, gid: str | None = None, *, map_file: str | None = None, main_role: str | None = None, blocking: list | None = None, both_plates: bool = False, drop_refs: list | None = None) -> tuple[dict, list]:
    import copy
    out = copy.deepcopy(prompt)
    old = [r for r in (out.get('refs') or []) if isinstance(r, str)]
    vp = out.get('video_prompt') or ''
    for rx in (_SPATIAL_RE, _GRID_RE, _MAPUSE_RE, _MAPONLY_RE, _TILE_RE, _MARKERS_RE, _BLOCK_RE, _LINE_RE):
        vp = rx.sub('', vp)
    vp = _ROUTE_MAP_RE.sub(' route:', vp)
    ov = layout.get('_override') or {}
    subs_log = []
    grp_ov = (ov.get('groups') or {}).get(gid or '') or {}
    if grp_ov.get('shot_text'):
        m1 = re.search(r'Shot\s*1\s*[:：]', vp); m2 = re.search(r'Global constraints:', vp)
        if m1 and m2 and m1.start() < m2.start():
            vp = vp[:m1.start()].rstrip() + '\n\n' + str(grp_ov['shot_text']).strip() + '\n\n' + vp[m2.start():]
            subs_log.append('shot_text 整段替换(拆镜)')
    for a, b in (ov.get('substitutions') or []):
        if a in vp:
            vp = vp.replace(a, b); subs_log.append(a)
    for rec in picks:
        so = (ov.get('shots') or {}).get(rec['shot_id']) or {}
        for a, b in (so.get('substitutions') or []):
            seg = re.search(r'(Shot\s*%d\s*[:：])(.*?)(?=Shot\s*\d+\s*[:：]|Global constraints:|$)' % rec['shot_no'], vp, re.S)
            if seg and a in seg.group(2):
                vp = vp[:seg.start(2)] + seg.group(2).replace(a, b) + vp[seg.end(2):]; subs_log.append(f"Shot {rec['shot_no']}: {a}")
    views = {int(v['tile']): v for v in layout.get('views', []) if v.get('tile') is not None}
    vp = _TILE_WORD_RE.sub(lambda m: f'tile {m.group(1)}（{views[int(m.group(1))]["desc_en"]}）' if int(m.group(1)) in views else m.group(0), vp)
    front = f'assets/concepts/scenes/{sid}/{DIRNAME}/front.png'
    reverse = f'assets/concepts/scenes/{sid}/{DIRNAME}/reverse.png'
    keep = [r for r in old if not _is_scene_map(r)]
    cut = 0
    for i, r in enumerate(keep):
        if r.startswith('assets/concepts/characters/') or r.startswith('assets/concepts/creatures/'):
            cut = i + 1
    map_mode = bool(map_file)
    route_c = map_mode and both_plates
    if route_c:
        new = keep[:cut] + [front, reverse, map_file] + keep[cut:]
    elif map_mode:
        main_role = main_role if main_role in ROLES else max(ROLES, key=lambda r: sum(1 for p in picks if p['pick'] == r))
        plate = front if main_role == 'front' else reverse
        new = keep[:cut] + [plate, map_file] + keep[cut:]
    else:
        new = keep[:cut] + [front, reverse] + keep[cut:]
    dropped = []
    for r in (drop_refs or []):                          # 超 9 张时按覆盖文件指定砍掉的参考图(C 路线 grp012)
        if len(new) > 9 and r in new:
            new.remove(r); dropped.append(r)
    vp, warns = _remap_images(vp, old, new)
    if route_c:
        n_front, n_reverse, n_map = new.index(front) + 1, new.index(reverse) + 1, new.index(map_file) + 1
        block = build_block(idx, layout, n_front, n_reverse).rstrip('.') + '. ' + plan_sentence(idx, layout, n_map, blocking or [])
    elif map_mode:
        n_plate, n_map = new.index(plate) + 1, new.index(map_file) + 1
        block = build_block_with_map(idx, layout, main_role, n_plate, n_map, blocking or [])
    else:
        n_front, n_reverse = new.index(front) + 1, new.index(reverse) + 1
        block = build_block(idx, layout, n_front, n_reverse)
    m = re.search(r'Shot\s*1\s*[:：]', vp)
    vp = (vp[:m.start()].rstrip() + '\n\n' + block + '\n\n' + vp[m.start():]) if m else (vp.rstrip() + '\n\n' + block)
    after = vp.find(block) + len(block)
    for rec in picks:
        head = re.compile(r'Shot\s*%d\s*[:：]' % rec['shot_no']).search(vp, after)
        if not head:
            warns.append(f"Shot {rec['shot_no']} 段头没找到,未插 {LINE_KEY} 句")
            continue
        if route_c:
            n_pick = n_front if rec['pick'] == 'front' else n_reverse
            n_other = n_reverse if rec['pick'] == 'front' else n_front
            line = shot_line(rec, idx, n_pick, n_other).rstrip('.') + f'; character and prop positions follow the plan [Image {n_map}].'
        elif map_mode:
            line = shot_line_map(rec, idx, main_role, n_plate, n_map)
        else:
            n_pick = n_front if rec['pick'] == 'front' else n_reverse
            n_other = n_reverse if rec['pick'] == 'front' else n_front
            line = shot_line(rec, idx, n_pick, n_other)
        vp = vp[:head.end()] + ' ' + line + vp[head.end():]
    if GC_KEY in vp and GC_EXTRA not in vp:
        vp = vp.rstrip()
        vp = (vp[:-1] if vp.endswith('.') else vp) + ', ' + GC_EXTRA + '.'
    if map_mode and GC_KEY in vp and GC_EXTRA_MAP not in vp:
        vp = vp.rstrip()
        vp = (vp[:-1] if vp.endswith('.') else vp) + ', ' + GC_EXTRA_MAP + '.'
    gce = str(ov.get('global_constraints_extra') or '').strip().rstrip('.')
    if GC_KEY in vp and gce and gce not in vp:
        vp = vp.rstrip()
        vp = (vp[:-1] if vp.endswith('.') else vp) + ', ' + gce + '.'
    vp = paragraphize(re.sub(r'[ \t]{2,}', ' ', vp))
    out['refs'] = new
    out['video_prompt'] = vp
    out['scene_pair_plates'] = {'route': 'C' if route_c else 'B' if map_mode else 'A', 'front': front, 'reverse': reverse, 'source': 'scene_pair_plates.v1', 'substitutions_applied': subs_log,
                                'layout_override': layout.get('_override_file'), 'main_plate': main_role if (map_mode and not route_c) else None, 'map': map_file, 'dropped_refs': dropped,
                                'picks': [{k: p[k] for k in ('shot_id', 'shot_no', 'tile', 'bearing_deg', 'pick', 'delta_deg', 'fits', 'weak')} for p in picks]}
    notes = [n for n in (out.get('notes') or []) if not str(n).startswith('场景正/反向双图接线(')]
    notes.append(f"场景正/反向双图接线(code/render_scene_pair_plates.py --write-prompt):refs 换成 front/reverse 两张整图,俯视动线图与九宫格移出;"
                 f"逐镜选图 " + ', '.join(f"Shot {p['shot_no']}→{p['pick']}(Δ{p['delta_deg']:+.0f}°{',weak' if p['weak'] else ''})" for p in picks))
    out['notes'] = notes
    if len(new) > 9:
        warns.append(f'refs {len(new)} 张 > 9(Seedance 2.0 上限)')
    return out, warns


def draw_group_map(base: Path, sid: str, gid: str, *, log=print) -> Path:
    layout = load_layout(base, sid)
    idx = load_index(base, sid)
    grp_ov = ((layout.get('_override') or {}).get('groups') or {}).get(gid) or {}
    blocking = grp_ov.get('blocking') or []
    if not blocking:
        raise ValueError(f'{gid}: layout_override.json#groups.{gid}.blocking 未写角色站位(按当前布局手写 start_xy/end_xy)')
    out = pair_dir(base, sid) / f'blocking_{gid}.jpg'
    draw_blocking_plan(layout, idx['dims_m'], gid, blocking, out, log=log)
    return out


def write_prompt(base: Path, sid: str, ep: str, gid: str, out_dir: Path, *, tiles=None, with_map: bool = False, both_plates: bool = False, log=print) -> tuple[Path, list]:
    idx = load_index(base, sid)
    layout = load_layout(base, sid)
    src = base / 'assets/prompts' / component(ep) / f'{gid}.json'
    pack = read(src, {}) or {}
    if not pack:
        raise FileNotFoundError(f'{src} 不存在')
    picks = assign_group(base, sid, ep, gid, tiles=tiles, prompt_pack=pack, log=log)
    for role in ROLES:
        if not (pair_dir(base, sid) / f'{role}.png').is_file():
            raise FileNotFoundError(f'{role}.png 未出,先出图')
    grp_ov = ((layout.get('_override') or {}).get('groups') or {}).get(gid) or {}
    if with_map:
        map_path = draw_group_map(base, sid, gid, log=log)
        new, warns = apply_group_prompt(pack, picks, idx, layout, sid, gid, map_file=str(map_path.relative_to(base)),
                                        main_role=grp_ov.get('main_plate'), blocking=grp_ov.get('blocking'),
                                        both_plates=both_plates, drop_refs=grp_ov.get('drop_refs'))
    else:
        new, warns = apply_group_prompt(pack, picks, idx, layout, sid, gid)
    if grp_ov.get('shots'):
        new['shots_split'] = grp_ov['shots']
    new['derived_from'] = str(src.relative_to(base))
    new['attempt'] = int(pack.get('attempt') or 0) + 1
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / 'prompt.json'
    target.write_text(json.dumps(new, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    (out_dir / 'video_prompt_sent.txt').write_text(new['video_prompt'] + '\n', encoding='utf-8')
    log(f'saved: {target.relative_to(base)}(route {new["scene_pair_plates"]["route"]},refs {len(new["refs"])} 张' + (f',砍掉 {new["scene_pair_plates"]["dropped_refs"]}' if new["scene_pair_plates"].get("dropped_refs") else '') + f',正文 {len(new["video_prompt"])} 字)')
    for w in warns:
        log(f'   WARN {w}')
    return target, warns


# ---------------------------------------------------------------- video
def _ffprobe(path: Path) -> dict:
    out = subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration:stream=codec_type,width,height,avg_frame_rate',
                                   '-of', 'json', str(path)]).decode()
    d = json.loads(out)
    info = {'duration_s': round(float(d['format']['duration']), 3), 'audio': False, 'fps': None, 'width': None, 'height': None}
    for s in d['streams']:
        if s['codec_type'] == 'video':
            info['width'], info['height'] = s.get('width'), s.get('height')
            n, dd = (s.get('avg_frame_rate') or '0/1').split('/')
            info['fps'] = round(float(n) / float(dd), 3) if float(dd) else None
        elif s['codec_type'] == 'audio':
            info['audio'] = True
    return info


def run_video(base: Path, gid: str, out_dir: Path, *, resolution='480p', aspect='16:9', log=print) -> dict:
    """按 spike prompt.json 出片到 out_dir(不动 assets/clips);参数与 offer code/run_video_ep02.py 同口径。"""
    pack = read(out_dir / 'prompt.json', {}) or {}
    if not pack:
        raise FileNotFoundError(f'{out_dir / "prompt.json"} 不存在,先 --write-prompt')
    for x in pack['refs'] + (pack.get('audio_refs') or []):
        if not (base / x).is_file():
            raise FileNotFoundError(f'refs/audio_refs 缺失: {x}')
    genmedia = Path(__file__).resolve().parent / 'genmedia.py'
    mp4 = out_dir / f'{gid}.mp4'
    tail = out_dir / f'{gid}.last_frame.png'
    if mp4.is_file():
        ts = time.strftime('%Y%m%d-%H%M%S', time.localtime(mp4.stat().st_mtime))
        for f in (mp4, tail, out_dir / 'meta.json'):
            if f.is_file():
                f.rename(out_dir / f'{f.stem}.prev-{ts}{f.suffix}')
    cmd = [sys.executable, str(genmedia), 'video', '--prompt', pack['video_prompt'], '--output', str(mp4),
           '--ref'] + [str(base / x) for x in pack['refs']]
    if pack.get('audio_refs'):
        cmd += ['--audio-ref'] + [str(base / x) for x in pack['audio_refs']]
    cmd += ['--generate-audio', 'on', '--return-last-frame', str(tail), '--duration', str(int(pack['total_duration_s'])),
            '--aspect', aspect, '--resolution', resolution]
    logf = out_dir / 'run.log'
    t0 = time.time()
    log(f'   出片 {gid}:{int(pack["total_duration_s"])}s / refs {len(pack["refs"])} / audio_refs {len(pack.get("audio_refs") or [])} / {resolution} {aspect}')
    with logf.open('a', encoding='utf-8') as lf:
        lf.write(f'[{_now()}] {" ".join(c if len(c) < 200 else c[:60] + "…" for c in cmd)}\n')
        p = subprocess.Popen(cmd, cwd=str(base), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in p.stdout:
            line = line.rstrip()
            if line:
                lf.write(line + '\n'); lf.flush()
                log('   | ' + line)
        rc = p.wait()
    el = round(time.time() - t0, 1)
    if rc != 0 or not mp4.is_file():
        raise RuntimeError(f'genmedia video 失败 rc={rc}({el}s),见 {logf}')
    info = _ffprobe(mp4)
    meta = {'group_id': gid, 'clip': str(mp4.relative_to(base)), 'last_frame': str(tail.relative_to(base)) if tail.is_file() else None,
            'shots': pack.get('shots'), 'target_duration_s': pack.get('total_duration_s'), 'probe': info, 'elapsed_s': el,
            'request_digest': {'resolution': resolution, 'aspect': aspect, 'refs': pack['refs'], 'audio_refs': pack.get('audio_refs') or [],
                               'prompt_pack': str((out_dir / 'prompt.json').relative_to(base)), 'prompt_len': len(pack['video_prompt'])},
            'scene_pair_plates': pack.get('scene_pair_plates'), 'generated_at': _now()}
    (out_dir / 'meta.json').write_text(json.dumps(meta, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    log(f"saved: {mp4.relative_to(base)} {info['duration_s']}s/{info['fps']}fps/{info['width']}x{info['height']}/audio={info['audio']}({el}s)")
    return meta


# ---------------------------------------------------------------- A/B 对照表
def compare_sheets(base: Path, gid: str, a_dir: Path, b_dir: Path, out: Path, *, step: float = 1.0, log=print) -> Path:
    """两条路线成片按同秒抽帧,上行 A、下行 B,拼一张对照表(PIL)。"""
    import tempfile
    from PIL import Image, ImageDraw, ImageFont
    rows = []
    for label, d in (('A: plates only', a_dir), ('B: plate + plan', b_dir)):
        mp4 = d / f'{gid}.mp4'
        if not mp4.is_file():
            raise FileNotFoundError(mp4)
        dur = _ffprobe(mp4)['duration_s']
        ts = [round(0.5 + i * step, 2) for i in range(int((dur - 0.5) / step) + 1)]
        frames = []
        with tempfile.TemporaryDirectory() as td:
            for t in ts:
                f = Path(td) / f'{t}.png'
                subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-ss', str(t), '-i', str(mp4), '-frames:v', '1', str(f)], check=True)
                frames.append(Image.open(f).convert('RGB'))
        rows.append((label, ts, frames))
    w, h = rows[0][2][0].size
    scale = min(1.0, 320 / w)
    w, h = int(w * scale), int(h * scale)
    cols = max(len(r[2]) for r in rows)
    pad, head = 6, 36
    sheet = Image.new('RGB', (cols * (w + pad) + pad, len(rows) * (h + pad + head) + pad), (20, 20, 20))
    dr = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype('/System/Library/Fonts/Helvetica.ttc', 22)
    except Exception:  # noqa: BLE001
        font = ImageFont.load_default()
    for r, (label, ts, frames) in enumerate(rows):
        y0 = pad + r * (h + pad + head)
        dr.text((pad, y0 + 6), f'{gid} · {label}', fill=(230, 230, 230), font=font)
        for c, (t, fr) in enumerate(zip(ts, frames)):
            x0 = pad + c * (w + pad)
            sheet.paste(fr.resize((w, h)), (x0, y0 + head))
            dr.text((x0 + 6, y0 + head + 4), f'{t:.1f}s', fill=(255, 255, 0), font=font)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    log(f'saved: {out.relative_to(base)}')
    return out
