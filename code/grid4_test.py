#!/usr/bin/env python3
"""四宫格背景图试验(2026-10-04):把九宫格(layout.json#views 的 9 个语义机位)换成「同一视点朝东/南/西/北各一格」的 2x2 宫格。

一个场景一个光照方案出一张 2x2 宫格:视点 = 俯视图正中心(落在实体内吸附到最近空点,--at 可改),机高 1.6 m、镜头水平不俯不仰,
四格各朝一个正方向,水平视场 90°(四格合起来正好一圈)。参考图 = 标点俯视图(红点 + 四向箭头)+ 宫格版式模板;出图后按版式拆成 4 张,
再用九宫格同一套打分(shot_plates.pick_grid9_tile / GRID9_FIT)给本集每镜选格,并与现行九宫格选格、现行采用图并排出比较页。

试验链路:产物单独放 assets/concepts/scenes/<sid>/grid4/ 与 qa/grid4_compare/,不写背景图库 plates/index.json、不改集索引
directing/<ep>/shot_plates.json、不接组 prompt refs —— 现行九宫格数据原样不动。

「东南西北」按场景布局图自己的方位叫法:layout.json#orientation 写了哪条边是哪个方向就按它(含 top_of_map 文字里的
「north end is at the left edge」这类说明);没写的边按对边/顺时针补齐。库里机位事实的 bearing 仍用 shot_plates.orientation_axes 的口径(打分只看差值)。

用法:
  python code/grid4_test.py --project alices --ep ep01 --scene SCN-long-hall --dry-run     # 视点 + 标点图 + 提示词,不出图
  python code/grid4_test.py --project alices --ep ep01 --scene SCN-long-hall --seed 20261004
  python code/grid4_test.py --project alices --ep ep01 --scene SCN-long-hall --compare-only  # 只重算选格 + 重出比较页
  可选:--at x,z(白模米制)、--height 1.6、--hfov 90、--scheme <光照方案 id>、--force(重出宫格,旧图改名 .prev-*)。
"""
import datetime as dt
import html
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args  # noqa: E402
from modules import scene_panos  # noqa: E402
from modules import shot_plates as sp  # noqa: E402
from modules.whitebox import component, read, render_format  # noqa: E402

DIRNAME = 'grid4'
CARDS = ['north', 'east', 'south', 'west']                       # 格序:左上 北、右上 东、左下 南、右下 西
EDGES = ['top', 'right', 'bottom', 'left']                       # 顺时针,缺省 上北右东下南左西
EDGE_VEC = {'top': (0, -1), 'right': (1, 0), 'bottom': (0, 1), 'left': (-1, 0)}   # 布局图边 → 白模 (dx, dz)
OPPOSITE = {'north': 'south', 'south': 'north', 'east': 'west', 'west': 'east'}
STATION_HEIGHT_M = 1.6
DEFAULT_HFOV = 90.0
CLEARANCE_M = 0.6
NEGATIVE_LEVEL = ('tilted camera, high angle, low angle, looking up, looking down, dutch angle, converging verticals, keystone distortion, '
                  'panorama, cube map')


# ---------------------------------------------------------------- orientation / station
def plan_edges(layout: dict) -> dict:
    """布局图四条边各是哪个方向 → {'north': 'left', …}。只认 layout.json#orientation 明写的;缺的按对边、再按顺时针补齐。"""
    o = layout.get('orientation') or {}
    edges = {}
    for edge in EDGES:
        text = str(o.get(f'{edge}_of_map') or '').strip().lower()
        word = next((c for c in CARDS if text.startswith(c)), None)
        if word:
            edges[edge] = word
    prose = ' '.join(str(v) for v in o.values() if isinstance(v, str)).lower()
    for m in re.finditer(r'\b(north|east|south|west)\b[^.;,]*?\b(?:at|on|toward|towards)\s+the\s+(top|right|bottom|left)\s+edge', prose):
        edges.setdefault(m.group(2), m.group(1))
    for a, b in (('top', 'bottom'), ('left', 'right')):
        if a in edges and b not in edges:
            edges[b] = OPPOSITE[edges[a]]
        if b in edges and a not in edges:
            edges[a] = OPPOSITE[edges[b]]
    if len(edges) < 4 or len(set(edges.values())) < 4:
        top = edges.get('top') or (OPPOSITE[edges['bottom']] if 'bottom' in edges else 'north')
        i = CARDS.index(top)
        edges = {e: CARDS[(i + k) % 4] for k, e in enumerate(EDGES)}
    return {card: edge for edge, card in edges.items()}


def blocking(scene: dict, h: float) -> list:
    """挡住机高这一层的白模实体(吊灯这类悬空件、矮道具不算)。"""
    out = []
    for o in scene.get('objects', []):
        bottom, top = o['position'][1] - o['size_m'][1] / 2, o['position'][1] + o['size_m'][1] / 2
        if bottom < h + 0.2 and top > h - 0.6 and not (o.get('yaw') or 0):
            out.append(o)
    return out


def _inside(x, z, o, margin=0.0) -> bool:
    return abs(x - o['position'][0]) <= o['size_m'][0] / 2 + margin and abs(z - o['position'][2]) <= o['size_m'][2] / 2 + margin


def station(scene: dict, at, h: float) -> tuple[list, str]:
    """视点 = 俯视图正中心;落在实体(含净距)内则吸附到最近的 0.25 m 空网格点。"""
    solids = blocking(scene, h)
    if at:
        return [round(at[0], 2), h, round(at[1], 2)], '--at'
    if not any(_inside(0, 0, o, CLEARANCE_M) for o in solids):
        return [0.0, h, 0.0], '俯视图正中心'
    dx, _, dz = scene['dimensions_m']
    best = None
    for i in range(-int(dx * 2), int(dx * 2) + 1):
        for j in range(-int(dz * 2), int(dz * 2) + 1):
            x, z = i * 0.25, j * 0.25
            if abs(x) > dx / 2 - 0.5 or abs(z) > dz / 2 - 0.5 or any(_inside(x, z, o, CLEARANCE_M) for o in solids):
                continue
            d = math.hypot(x, z)
            if best is None or d < best[0]:
                best = (d, x, z)
    if best is None:
        return [0.0, h, 0.0], '俯视图正中心(找不到空点,未吸附)'
    return [best[1], h, best[2]], f'俯视图正中心落在实体内,吸附到最近空点 [{best[1]}, {best[2]}]'


def reach(scene: dict, pos, vec, h: float) -> float:
    """从视点沿 vec(轴向单位向量)到第一个挡住机高层的实体表面的距离;没挡到 → 到布局图边。"""
    x0, z0 = pos[0], pos[2]
    dx, _, dz = scene['dimensions_m']
    best = (dx / 2 - x0 * vec[0]) if vec[0] else (dz / 2 - z0 * vec[1])
    for o in blocking(scene, h):
        cx, cz = o['position'][0], o['position'][2]; sx, sz = o['size_m'][0], o['size_m'][2]
        if vec[0]:
            if abs(z0 - cz) > sz / 2:
                continue
            d = (cx - x0) * vec[0] - sx / 2
        else:
            if abs(x0 - cx) > sx / 2:
                continue
            d = (cz - z0) * vec[1] - sz / 2
        if 0 < d < best:
            best = d
    return max(0.0, best)


_STRUCTURAL = re.compile(r'wall|lintel|masonry|vault|floor|ceiling|ground')


def _segment_hit(x0, z0, x1, z1, o) -> float | None:
    """线段 (x0,z0)→(x1,z1) 进入实体 o 平面包围盒的参数 t(0..1);不相交返回 None。"""
    t0, t1 = 0.0, 1.0
    for a, b, c, half in ((x0, x1, o['position'][0], o['size_m'][0] / 2), (z0, z1, o['position'][2], o['size_m'][2] / 2)):
        d = b - a
        if abs(d) < 1e-9:
            if abs(a - c) > half:
                return None
            continue
        lo, hi = sorted(((c - half - a) / d, (c + half - a) / d))
        t0, t1 = max(t0, lo), min(t1, hi)
        if t0 > t1:
            return None
    return t0


def visible_objects(scene: dict, key: dict, fmt: dict) -> list[str]:
    """本格画内、没被机高层实体挡住的陈设(门/灯/桌…,结构件与小零件不列),按基名归并计数,自左向右。"""
    pos = key['position']
    project = sp.projector(key, fmt)
    solids = blocking(scene, pos[1])
    groups = {}
    for o in scene.get('objects', []):
        sx, sy, sz = o['size_m']; cx, cy, cz = o['position']
        if _STRUCTURAL.search(o['id']) or max(sx, sz) >= 6 or max(sx, sz) < 0.35:
            continue
        dist = math.hypot(cx - pos[0], cz - pos[2])
        q = project([cx, cy, cz]) if dist >= 0.6 else None
        if not q or abs(q[0]) > 1.0:
            continue
        hits = [_segment_hit(pos[0], pos[2], cx, cz, s) for s in solids if s is not o]
        if any(t is not None and t * dist < dist - 0.5 for t in hits):
            continue
        name = sp._PART_SUFFIX.sub('', o['id'])
        name = re.sub(r'_[a-z]?\d+$', '', name).replace('_', ' ')
        g = groups.setdefault(name, {'ids': set(), 'xs': [], 'd': [], 'bottom': 1e9, 'top': 0.0})
        g['ids'].add(sp._PART_SUFFIX.sub('', o['id'])); g['xs'].append(q[0]); g['d'].append(dist)
        g['bottom'] = min(g['bottom'], cy - sy / 2); g['top'] = max(g['top'], cy + sy / 2)
    out = []
    for name, g in sorted(groups.items(), key=lambda kv: sum(kv[1]['xs']) / len(kv[1]['xs'])):
        n = len(g['ids']); xs = g['xs']
        where = 'left and right of centre' if n > 1 and min(xs) < -.2 and max(xs) > .2 and not (min(xs) < -.8 and max(xs) > .8) \
            else sp.x_word(min(xs), max(xs), sum(xs) / len(xs))
        out.append(f"{n} {name}{'s' if n > 1 else ''} ({where}, {min(g['d']):.0f}"
                   + (f"–{max(g['d']):.0f}" if n > 1 and max(g['d']) - min(g['d']) >= 1 else '') + ' m away'
                   # 四宫格实测:吊灯被画成落地灯、0.6 m 矮帘被画成落地长帘 → 悬空件与矮物写明高度
                   + (f", hanging from the ceiling at {g['bottom']:.1f} m, not standing on the floor" if g['bottom'] >= 1.8 else
                      f", low, only {g['top']:.1f} m tall" if g['top'] <= 1.0 else '') + ')')
    return out


def marked_plan(plan_file: Path, scene: dict, pos, edges: dict, output: Path) -> Path:
    """俯视图叠视点红点 + 四向箭头与字母(N/E/S/W 按本场景方位)。"""
    from PIL import Image, ImageDraw, ImageFont
    im = Image.open(plan_file).convert('RGBA')
    W, H = im.size
    dx, _, dz = scene['dimensions_m']
    px, py = (pos[0] / dx + .5) * W, (pos[2] / dz + .5) * H
    ov = Image.new('RGBA', im.size, (0, 0, 0, 0))
    dr = ImageDraw.Draw(ov)
    try:
        font = ImageFont.truetype('/System/Library/Fonts/Helvetica.ttc', max(28, W // 45))
    except Exception:  # noqa: BLE001
        font = ImageFont.load_default()
    L, r = min(W, H) * 0.11, max(8, W // 200)
    for card in CARDS:
        ux, uz = EDGE_VEC[edges[card]]
        ex, ey = px + ux * L, py + uz * L
        dr.line([px, py, ex, ey], fill=(255, 30, 30, 235), width=6)
        nx, ny = -uz, ux
        dr.polygon([(ex + ux * 22, ey + uz * 22), (ex + nx * 13, ey + ny * 13), (ex - nx * 13, ey - ny * 13)], fill=(255, 30, 30, 235))
        ch = card[0].upper()
        tx, ty = px + ux * (L + 58), py + uz * (L + 58)
        tw, th = dr.textbbox((0, 0), ch, font=font)[2:]
        dr.rectangle([tx - tw / 2 - 8, ty - th / 2 - 6, tx + tw / 2 + 8, ty + th / 2 + 8], fill=(255, 255, 255, 235))
        dr.text((tx - tw / 2, ty - th / 2), ch, fill=(220, 20, 20, 255), font=font)
    dr.ellipse([px - r, py - r, px + r, py + r], fill=(255, 20, 20, 255), outline=(255, 255, 255, 255), width=3)
    output.parent.mkdir(parents=True, exist_ok=True)
    Image.alpha_composite(im, ov).convert('RGB').save(output, quality=92)
    return output


# ---------------------------------------------------------------- views / prompt
def grid4_views(scene: dict, layout: dict, fmt: dict, axes, pos, hfov: float, edges: dict) -> list[dict]:
    ex, ez, texts = axes
    vfov = 2 * math.degrees(math.atan(math.tan(math.radians(hfov / 2)) * fmt['height'] / fmt['width']))
    landmarks = [lm for lm in layout.get('landmarks', []) if isinstance(lm, dict) and 'xy' in lm]
    dims = scene['dimensions_m']
    views = []
    for i, card in enumerate(CARDS):
        ux, uz = EDGE_VEC[edges[card]]
        key = {'position': list(pos), 'target': [pos[0] + ux * 10, pos[1], pos[2] + uz * 10], 'fov': vfov}
        facts = sp.camera_facts(key, fmt, ex, ez, texts)
        floor_scene = {**scene, 'objects': [o for o in scene.get('objects', []) if o['position'][1] - o['size_m'][1] / 2 <= 0.3]}   # 悬空件(吊灯)不算脚下
        facts['standing'] = sp.standing_on(floor_scene, layout, key)
        project = sp.projector(key, fmt)
        seen, hidden = [], []
        for lm in landmarks:
            p = [(lm['xy'][0] - .5) * dims[0], 1.0, (lm['xy'][1] - .5) * dims[2]]
            name = lm.get('name_en') or lm.get('name') or lm['id']
            dist = math.hypot(p[0] - pos[0], p[2] - pos[2])
            if dist < 1.5 or lm.get('kind') in ('zone', 'space', 'direction'):
                continue
            q = project(p)
            if q and abs(q[0]) <= 1.0:
                seen.append((q[0], f"{name} ({sp.x_word(q[0], q[0], q[0])}, about {dist:.0f} m away)"))
            else:
                hidden.append(name)
        right = (-uz, ux)
        views.append({'tile': i + 1, 'card': card, 'edge': edges[card], 'key': key, 'facts': facts,
                      'ahead_m': round(reach(scene, pos, (ux, uz), pos[1]), 1),
                      'left_m': round(reach(scene, pos, (-right[0], -right[1]), pos[1]), 1),
                      'right_m': round(reach(scene, pos, right, pos[1]), 1),
                      'left_card': next(c for c in CARDS if EDGE_VEC[edges[c]] == (-right[0], -right[1])),
                      'right_card': next(c for c in CARDS if EDGE_VEC[edges[c]] == right),
                      'seen': [s for _, s in sorted(seen)], 'hidden': hidden, 'objects': visible_objects(scene, key, fmt)})
    return views


def build_prompt(layout: dict, scene: dict, views: list, geom: dict, pos, hfov: float, style: str, lighting: str, desc: str,
                 time_of_day: str, edges: dict) -> str:
    name = re.sub(r'[(（].*?[)）]', '', layout.get('scene_name_en') or layout.get('scene_name') or scene.get('name') or scene['scene_id']).strip()
    name = re.sub(r'^SCN-', '', name).replace('-', ' ')
    mm = views[0]['facts']['lens_mm_equiv']
    lines = [(f"One image that is a {geom['cols']} by {geom['rows']} grid of four separate photographs of the exact same location, all taken "
              f"from one single camera standpoint with the camera turned to face north, east, south and west in turn, so that the four views "
              f"together cover the full 360-degree turn around that standpoint. Laid out as two rows of two equal rectangular tiles with thin "
              f"straight pure-white gutters between them, exactly matching the blank tiling template in [Image 2]. Location: {name}. "
              f"Time of day: {time_of_day or ''}." + (f" Lighting: {lighting}." if lighting else ''))]
    plan = ("[Image 1] is the top-down plan of this location, with the camera standpoint marked as a red dot and four red arrows lettered "
            "N, E, S, W showing the four viewing directions. Use it only as the spatial layout reference: which wall, door, column, object "
            "and open floor lies in each direction, how far away it is and what is beside it. Never reproduce the plan, its top-down "
            "viewpoint, the red dot, the arrows or the letters; no tile may be a top-down, overhead, bird's-eye or plan view. On the plan, "
            + ', '.join(f"{c} is toward the {edges[c]} edge" for c in CARDS) + '.')
    lms = [f"{(lm.get('name_en') or lm.get('name') or lm['id'])} ({sp._plan_pos_word(lm['xy'])})" for lm in layout.get('landmarks', []) if 'xy' in lm]
    if lms:
        plan += " Landmarks on the plan: " + '; '.join(lms) + '.'
    lines.append(plan)
    lines.append(f"Every tile uses the same camera: standing {views[0]['facts']['standing']}, lens {pos[1]:g} m above the floor (eye level), "
                 f"{mm:g}mm-equivalent rectilinear wide-angle lens with about {hfov:g} degrees horizontal field of view. The lens axis is "
                 f"perfectly horizontal in every tile — not looking up, not looking down, no roll — so the horizon is a level line at "
                 f"mid-height and every vertical edge stays vertical; straight edges stay straight, no fisheye. Each tile looks squarely in "
                 f"its own direction; the right edge of one tile continues at the left edge of the next direction clockwise.")
    lines.append("The architecture, materials, set dressing, weather, light direction and colour grade are identical in every tile; only the "
                 "direction the camera faces changes from tile to tile. Finish every tile at full sharpness with deep focus, no shallow depth "
                 "of field, no bokeh, no vignetting.")
    for v in views:
        t = (f"Tile {v['tile']} ({sp.grid_tile_word(geom, v['tile'] - 1)}): looking due {v['card']}, straight toward the {v['edge']} edge of the "
             f"plan. The nearest solid surface straight ahead is about {v['ahead_m']:g} m away; the space is open for about {v['left_m']:g} m "
             f"to the left ({v['left_card']}) and about {v['right_m']:g} m to the right ({v['right_card']}).")
        if v['seen']:
            t += ' In view from left to right: ' + '; '.join(v['seen']) + '.'
        if v['objects']:
            t += ' Set dressing in this view: ' + '; '.join(v['objects']) + '.'
        if v['hidden']:
            t += ' Behind or beside the camera, not in this tile: ' + '; '.join(v['hidden']) + '.'
        lines.append(t)
    if desc:
        lines.append("General location description for materials and era: " + desc)
    lines.append("Every tile is an empty location plate: no people, no characters, no human figures or silhouettes, no animals, no moving "
                 "vehicles, no text, no numbers, no labels, no watermark. Keep the gutters thin, straight and pure white; never merge two "
                 "tiles into one picture and never draw anything across a gutter.")
    style = sp.plate_style(sp.strip_dof(style))
    if style:
        lines.append("Style: " + style)
    return '\n'.join(lines)


# ---------------------------------------------------------------- generate
def ensure_grid4(base: Path, sid: str, scheme_id: str, scheme_key: str, time_of_day: str, plan: dict, *, at=None, height=STATION_HEIGHT_M,
                 hfov=DEFAULT_HFOV, force=False, dry_run=False, seed=None, log=print) -> dict:
    scene, layout, fmt, axes = plan['episode']['scenes'][sid], plan['layouts'][sid], plan['fmt'], plan['axes'][sid]
    out = base / 'assets/concepts/scenes' / sid / DIRNAME
    rel = f'assets/concepts/scenes/{sid}/{DIRNAME}'
    idx_file = out / 'index.json'
    idx = read(idx_file, {}) or {}
    stem = f'{scheme_key}_grid4'
    have = [e for e in idx.get('tiles', []) if (e.get('pano_ref') or {}).get('scheme') == scheme_key]
    if len(have) == 4 and not force and not dry_run and all((base / e['file']).is_file() for e in have):
        log(f'== {sid} 四宫格 {stem} 已有,跳过出图(--force 重出)')
        return idx
    plan_file = base / 'assets/concepts/scenes' / sid / (layout.get('layout_top') or 'layout_top.png')
    if not plan_file.is_file():
        raise FileNotFoundError(f'{sid}: 缺俯视图 {plan_file.relative_to(base)}')
    edges = plan_edges(layout)
    pos, basis = station(scene, at, height)
    views = grid4_views(scene, layout, fmt, axes, pos, hfov, edges)
    geom = sp.grid_geometry(4, fmt)
    pw, ph = sp.plate_size(fmt)
    style_doc = read(base / 'bible/style.json', {}) or {}
    desc, scene_neg = sp.scene_description(base, sid)
    prompt = build_prompt(layout, scene, views, geom, pos, hfov, style_doc.get('style_fragment_en') or '', sp.lighting_fragment(base, sid, scheme_id),
                          desc, time_of_day, edges)
    negative = ', '.join(x for x in (sp.plate_negative(style_doc.get('negative_prompt_en') or ''), scene_neg, sp.NEGATIVE_GRID, sp.NEGATIVE_MASTER,
                                     NEGATIVE_LEVEL) if x)
    marked_rel, tmpl_rel, sheet_rel = f'{rel}/{stem}.plan.jpg', f'{rel}/{stem}.template.jpg', f'{rel}/{stem}.png'
    marked_plan(plan_file, scene, pos, edges, base / marked_rel)
    sp.compose_grid_sheet([], geom, base / tmpl_rel)
    (out / f'{stem}.prompt.txt').write_text(prompt + '\n\nNEGATIVE: ' + negative + '\n', encoding='utf-8')
    log(f"== {sid} 四宫格 {stem}:视点 {pos}({basis}),水平视场 {hfov:g}°(垂直 {views[0]['facts']['fov_v_deg']}°,{views[0]['facts']['lens_mm_equiv']}mm);"
        f"宫格 {geom['width']}x{geom['height']},格 {geom['tile_w']}x{geom['tile_h']} → 拆后 {pw}x{ph};"
        + ';'.join(f"第 {v['tile']} 格 {v['card']}→布局图{v['edge']}边(前 {v['ahead_m']} m,库内朝向 {v['facts']['bearing_deg']}°)" for v in views))
    if dry_run:
        log(prompt)
        log('refs: ' + json.dumps([marked_rel, tmpl_rel], ensure_ascii=False))
        return {}
    from modules.genmedia import generate_image, get_config, image_pref_env
    with image_pref_env('scenes'):
        cfg = get_config('image')
    channel = {'provider': cfg.get('provider'), 'model': cfg.get('model')}
    use_seed = seed if seed is not None else __import__('random').randint(1, 2**31 - 1)
    stamp = dt.datetime.now().strftime('%H%M%S')
    if (base / sheet_rel).is_file():
        for f in [base / sheet_rel] + [out / f'{stem}_t{i}.png' for i in range(1, 5)]:
            if f.is_file():
                f.rename(f.with_name(f'{f.stem}.prev-{stamp}.png'))
    log(f"   出图 {channel['provider']}/{channel['model']} {geom['width']}x{geom['height']} seed {use_seed},参考图 = 标点俯视图 + 版式模板 …")
    generate_image(prompt, str(base / sheet_rel), negative=negative, refs=[str(base / marked_rel), str(base / tmpl_rel)],
                   size=f"{geom['width']}x{geom['height']}", seed=use_seed)
    now = dt.datetime.now().isoformat(timespec='seconds')
    tiles = []
    for v in views:
        tiles.append({'key': f"{stem}_t{v['tile']}", 'grid4': True, 'master': False, 'file': f"{rel}/{stem}_t{v['tile']}.png",
                      'lighting_scheme_id': scheme_id, 'time_of_day': time_of_day, 'camera': v['facts'], 'size': f'{pw}x{ph}',
                      'seed': use_seed, 'refs': [marked_rel, tmpl_rel], 'channel': channel,
                      'pano_ref': {'kind': 'grid4', 'sheet': sheet_rel, 'template': tmpl_rel, 'cols': geom['cols'], 'rows': geom['rows'],
                                   'tile': v['tile'] - 1, 'scheme': scheme_key,
                                   'view': {'card': v['card'], 'plan_edge': v['edge'], 'ahead_m': v['ahead_m']}},
                      'written_at': now})
    results = sp.split_grid_sheet(base / sheet_rel, geom, [base / e['file'] for e in tiles], (pw, ph))
    for e, r in zip(tiles, results):
        e['pano_ref']['box'] = r['box']
    idx = {'schema_version': 'grid4_test.v1', 'scene_id': sid, 'scheme': scheme_key, 'station': {'position_m': pos, 'basis': basis},
           'hfov_deg': hfov, 'plan_edges': edges, 'geometry': geom, 'sheet': sheet_rel, 'marked_plan': marked_rel, 'seed': use_seed,
           'channel': channel, 'prompt': prompt, 'negative': negative, 'written_at': now,
           'tiles': [e for e in idx.get('tiles', []) if (e.get('pano_ref') or {}).get('scheme') != scheme_key] + tiles}
    idx_file.write_text(json.dumps(idx, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    log(f'saved: {sheet_rel}(+4 格)')
    return idx


# ---------------------------------------------------------------- per-shot picks + compare page
def assign(base: Path, sid: str, ep: str, plan: dict, idx: dict, scheme_key: str, *, log=print) -> list[dict]:
    """本集本场景每条背景图需求(镜首/镜尾):四宫格选格 + 同一套打分下的九宫格选格 + 集索引里现行采用的图。"""
    tiles4 = sorted([e for e in idx.get('tiles', []) if (e.get('pano_ref') or {}).get('scheme') == scheme_key], key=lambda e: e['pano_ref']['tile'])
    lib = sp.load_library(base, sid)
    tiles9 = sp.grid9_entries(lib, scheme_key)
    by_key = {e['key']: e for e in lib.get('plates', [])}
    current = (read(base / 'directing' / ep / 'shot_plates.json', {}) or {}).get('shots', {})
    rows = []
    for j in sorted((j for j in plan['jobs'] if j['scene_id'] == sid), key=lambda j: (j['shot_id'], j['role'] == 'end')):
        e4, i4 = sp.pick_grid9_tile(tiles4, j['facts'])
        e9, i9 = sp.pick_grid9_tile(tiles9, j['facts'])
        cur = next((p for p in (current.get(j['shot_id']) or {}).get('plates', []) if p.get('role') == j['role']), None) or {}
        rows.append({'shot_id': j['shot_id'], 'group_id': j['group_id'], 'role': j['role'], 't': j['t'], 'facts': j['facts'],
                     'movement': j['tier'].get('category'), 'desc': (j.get('shot') or {}).get('camera_position', ''),
                     'grid4': {'entry': e4, 'info': i4, 'reasons': sp.grid9_unfit_reasons(i4)} if e4 else None,
                     'grid9': {'entry': e9, 'info': i9, 'reasons': sp.grid9_unfit_reasons(i9)} if e9 else None,
                     'current': {'key': cur.get('key'), 'file': cur.get('file'), 'reuse': cur.get('reuse'),
                                 'camera': (by_key.get(cur.get('key')) or {}).get('camera')} if cur else None})
    return rows


CSS = """
body{font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;margin:0;background:#111;color:#ddd}
header{padding:16px 20px;border-bottom:1px solid #333;background:#181818}
h1{font-size:18px;margin:0 0 6px}h2{font-size:15px;margin:18px 20px 8px}
.meta{color:#999;font-size:12px}.meta code{color:#bbb}
.stats{display:flex;gap:14px;flex-wrap:wrap;margin-top:8px}.stat{background:#222;border:1px solid #333;border-radius:6px;padding:6px 10px}
.stat b{font-size:18px;display:block}.filters{margin-top:8px}.filters button{background:#2a2a2a;color:#ddd;border:1px solid #444;border-radius:4px;padding:4px 10px;margin-right:6px;cursor:pointer}
.filters button.on{background:#3b6;color:#000;border-color:#3b6}
.sheet{padding:12px 20px;display:grid;grid-template-columns:2fr 1fr;gap:12px}.sheet img{width:100%;border:1px solid #333;cursor:zoom-in}
.tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;padding:0 20px 12px}.tiles figure{margin:0;font-size:12px;color:#aaa}.tiles img{width:100%;border:1px solid #333;cursor:zoom-in}
.wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;min-width:900px;font-size:12px}th,td{border-top:1px solid #2a2a2a;padding:8px;vertical-align:top;text-align:left}
th{background:#1c1c1c;color:#bbb}tr.hide{display:none}
td.img{width:22%}td.img img{width:100%;border:1px solid #333;cursor:zoom-in;display:block}
.cap{color:#999;font-size:11px;margin-top:4px;word-break:break-all}
.ok{color:#3b6}.bad{color:#e75}.tag{display:inline-block;padding:1px 6px;border-radius:3px;font-size:11px;margin:0 4px 2px 0}
.tag.ok{background:#1f3a2a}.tag.bad{background:#3a2a1f}.tag.cur{background:#2a2f3a;color:#9bf}
.shot b{font-size:13px}.desc{color:#aaa;max-width:240px}
#zoom{position:fixed;inset:0;background:rgba(0,0,0,.92);display:none;align-items:center;justify-content:center;z-index:9;cursor:zoom-out}#zoom img{max-width:96vw;max-height:96vh}
dl{margin:4px 0 0;font-size:11px;color:#aaa}dl div{display:flex;gap:6px}dt{color:#777;min-width:52px}
"""

JS = """
function zoom(src){const z=document.getElementById('zoom');z.querySelector('img').src=src;z.style.display='flex'}
document.addEventListener('DOMContentLoaded',()=>{document.getElementById('zoom').onclick=()=>{document.getElementById('zoom').style.display='none'};
document.querySelectorAll('.filters button').forEach(b=>b.onclick=()=>{document.querySelectorAll('.filters button').forEach(x=>x.classList.remove('on'));b.classList.add('on');
const f=b.dataset.f;document.querySelectorAll('tbody tr').forEach(tr=>{tr.classList.toggle('hide',f!=='all'&&tr.dataset.kind!==f)})})});
"""
CARD_ZH = {'north': '北', 'east': '东', 'south': '南', 'west': '西'}
EDGE_ZH = {'top': '上', 'right': '右', 'bottom': '下', 'left': '左'}
REUSE_ZH = {'grid9': '九宫格格子', 'grid9_fallback': '九宫格补图', 'manual': '手选', 'revised': '修改版'}


def esc(x) -> str:
    return html.escape('' if x is None else str(x))


def write_compare(base: Path, project: str, sid: str, ep: str, idx: dict, rows: list, frames: dict, scheme_key: str) -> Path:
    out = base / 'qa' / 'grid4_compare' / f'{sid}.html'
    out.parent.mkdir(parents=True, exist_ok=True)

    def url(p):
        return str(Path('..') / '..' / p) if p else ''

    tiles4 = sorted([e for e in idx.get('tiles', []) if (e.get('pano_ref') or {}).get('scheme') == scheme_key], key=lambda e: e['pano_ref']['tile'])
    fit = sp.GRID9_FIT
    n4 = sum(1 for r in rows if r['grid4'] and not r['grid4']['reasons'])
    n9 = sum(1 for r in rows if r['grid9'] and not r['grid9']['reasons'])
    use4 = Counter(r['grid4']['info']['tile'] for r in rows if r['grid4'])
    why4 = Counter(x.split(' ')[0] for r in rows if r['grid4'] for x in r['grid4']['reasons'])
    st = idx.get('station', {})
    h = [f'<!doctype html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
         f'<title>四宫格试验 · {esc(sid)} · {esc(ep)}</title><style>{CSS}</style><script>{JS}</script></head><body><div id="zoom"><img></div>']
    h.append(f'<header><h1>四宫格(东南西北各一格)试验 · {esc(project)} / {esc(ep)} / {esc(sid)}</h1>'
             f'<div class="meta">一张 2×2 宫格:视点 {esc(st.get("position_m"))}({esc(st.get("basis"))}),镜头水平,水平视场 {esc(idx.get("hfov_deg"))}°;'
             f'参考图 = 标点俯视图 + 版式模板;{esc((idx.get("channel") or {}).get("provider"))} {esc((idx.get("channel") or {}).get("model"))} · seed {esc(idx.get("seed"))}。'
             f'选格与「合适」判定沿用九宫格同一套:朝向差 ≤ {fit["bearing_deg"]:g}°、俯仰差 ≤ {fit["pitch_deg"]:g}°、机位距 ≤ {fit["distance_m"]:g} m、机高档差 &lt; {fit["height_class_delta"]}。'
             f'试验数据不进背景图库、不改集索引。</div>'
             f'<div class="stats"><div class="stat"><b>{len(rows)}</b>背景图需求(镜首/镜尾)</div>'
             f'<div class="stat"><b class="ok">{n4}</b>四宫格直接合适</div><div class="stat"><b class="bad">{len(rows) - n4}</b>四宫格不合适</div>'
             f'<div class="stat"><b>{n9}</b>九宫格直接合适(对照)</div>'
             + ''.join(f'<div class="stat"><b>{n}</b>不合适原因:{esc(k)}</div>' for k, n in why4.most_common()) + '</div>'
             f'<div class="filters"><button class="on" data-f="all">全部</button><button data-f="fit">四宫格合适</button><button data-f="unfit">四宫格不合适</button></div></header>')
    h.append(f'<h2>四宫格整图 与 标点俯视图</h2><div class="sheet"><img src="{esc(url(idx.get("sheet")))}" onclick="zoom(this.src)">'
             f'<img src="{esc(url(idx.get("marked_plan")))}" onclick="zoom(this.src)"></div>')
    h.append('<div class="tiles">' + ''.join(
        f'<figure><img src="{esc(url(e["file"]))}" onclick="zoom(this.src)"><figcaption>第 {e["pano_ref"]["tile"] + 1} 格 · 朝{CARD_ZH[e["pano_ref"]["view"]["card"]]}'
        f'(布局图{EDGE_ZH[e["pano_ref"]["view"]["plan_edge"]]}边)· 正前方 {esc(e["pano_ref"]["view"]["ahead_m"])} m · 被选中 {use4.get(e["pano_ref"]["tile"] + 1, 0)} 次</figcaption></figure>'
        for e in tiles4) + '</div>')
    h.append('<h2>逐镜对照</h2><div class="wrap"><table><thead><tr><th>镜</th><th>本镜白模帧(机位真值)</th><th>四宫格选格</th><th>九宫格选格(同一套打分)</th>'
             '<th>现行采用图</th></tr></thead><tbody>')

    def pick_cell(g, zh):
        if not g:
            return '<td class="img"><i>无</i></td>'
        i, e = g['info'], g['entry']
        verdict = ('<span class="tag ok">合适</span>' if not g['reasons'] else '<span class="tag bad">不合适</span>'
                   + ''.join(f'<span class="tag bad">{esc(x)}</span>' for x in g['reasons']))
        label = f'第 {i["tile"]} 格' + (f' · 朝{CARD_ZH[e["pano_ref"]["view"]["card"]]}' if zh else '')
        return (f'<td class="img"><img src="{esc(url(e["file"]))}" loading="lazy" onclick="zoom(this.src)"><div class="cap">{verdict}</div>'
                f'<dl><div><dt>格</dt><dd>{label} · score {esc(i["score"])}</dd></div><div><dt>朝向差</dt><dd>{esc(i["bearing_delta_deg"])}°</dd></div>'
                f'<div><dt>机位距</dt><dd>{esc(i["distance_m"])} m</dd></div><div><dt>机高档差</dt><dd>{esc(i["height_class_delta"])}</dd></div>'
                f'<div><dt>俯仰差</dt><dd>{esc(i["pitch_delta_deg"])}°</dd></div></dl></td>')

    for r in rows:
        c = r['facts']
        kind = 'fit' if r['grid4'] and not r['grid4']['reasons'] else 'unfit'
        frame = frames.get((r['shot_id'], r['role']))
        h.append(f'<tr data-kind="{kind}"><td class="shot"><b>{esc(r["shot_id"])}</b> {esc(r["role"])}<br>{esc(r["group_id"])} · {esc(r["movement"])}'
                 f'<div class="desc">{esc(r["desc"])}</div></td>'
                 f'<td class="img">' + (f'<img src="{esc(url(frame))}" loading="lazy" onclick="zoom(this.src)">' if frame else '<i>无白模帧</i>')
                 + f'<dl><div><dt>朝向</dt><dd>{esc(c.get("bearing_deg"))}°</dd></div><div><dt>机高</dt><dd>{esc(c.get("height_m"))} m</dd></div>'
                 f'<div><dt>俯仰</dt><dd>{esc(c.get("pitch_deg"))}°</dd></div><div><dt>镜头</dt><dd>{esc(c.get("lens_mm_equiv"))}mm · 水平 {esc(c.get("fov_h_deg"))}°</dd></div>'
                 f'<div><dt>位置</dt><dd>{esc(c.get("standing"))}</dd></div></dl></td>')
        h.append(pick_cell(r['grid4'], True))
        h.append(pick_cell(r['grid9'], False))
        cur = r['current']
        if cur and cur.get('file') and (base / cur['file']).is_file():
            h.append(f'<td class="img"><img src="{esc(url(cur["file"]))}" loading="lazy" onclick="zoom(this.src)"><div class="cap">'
                     f'<span class="tag cur">{esc(REUSE_ZH.get(cur.get("reuse"), cur.get("reuse")))}</span>{esc(cur.get("key"))}</div></td>')
        else:
            h.append('<td class="img"><i>无</i></td>')
        h.append('</tr>')
    h.append('</tbody></table></div></body></html>')
    out.write_text('\n'.join(h), encoding='utf-8')
    return out


def main():
    def configure(ap):
        ap.add_argument('--scene', required=True)
        ap.add_argument('--dry-run', action='store_true')
        ap.add_argument('--force', action='store_true', help='重出宫格(旧图改名 .prev-*.png)')
        ap.add_argument('--compare-only', action='store_true', help='不出图,只重算选格并重出比较页')
        ap.add_argument('--at', default=None, help='视点 x,z(白模米制);缺省俯视图正中心')
        ap.add_argument('--height', type=float, default=STATION_HEIGHT_M)
        ap.add_argument('--hfov', type=float, default=DEFAULT_HFOV, help='每格水平视场°,缺省 90(四格正好一圈)')
        ap.add_argument('--scheme', default=None, help='光照方案 id,缺省本集该场景用得最多的')
        ap.add_argument('--seed', type=int, default=None)
        ap.add_argument('--no-frames', action='store_true', help='比较页不渲本镜白模帧')
    args, base = parse_args(__doc__, configure=configure)
    sid, ep = component(args.scene), component(args.ep)
    plan = sp.plan_episode(base, ep)
    jobs = [j for j in plan['jobs'] if j['scene_id'] == sid]
    if not jobs:
        print(f'{sid}: {ep} 白模里没有该场景的镜', file=sys.stderr)
        return 1
    scheme_id = args.scheme or Counter(j['scheme'] for j in jobs).most_common(1)[0][0]
    tod = next((j['raw_group'].get('time_of_day') for j in jobs if j['scheme'] == scheme_id and j['raw_group'].get('time_of_day')), '') or ''
    scheme_key = scene_panos.scheme_slug(scheme_id, tod)
    at = [float(v) for v in args.at.split(',')] if args.at else None
    if args.compare_only:
        idx = read(base / 'assets/concepts/scenes' / sid / DIRNAME / 'index.json', {}) or {}
    else:
        idx = ensure_grid4(base, sid, scheme_id, scheme_key, tod, plan, at=at, height=args.height, hfov=args.hfov, force=args.force,
                           dry_run=args.dry_run, seed=args.seed)
    if args.dry_run:
        return 0
    if not idx.get('tiles'):
        print(f'{sid}: 还没有四宫格,先去掉 --compare-only 出图', file=sys.stderr)
        return 1
    rows = assign(base, sid, ep, plan, idx, scheme_key)
    frames = {}
    if not args.no_frames:
        reqs = []
        for r in rows:
            rel = f'qa/grid4_compare/frames/{sid}/{r["shot_id"]}_{r["role"]}.whitebox.jpg'
            frames[(r['shot_id'], r['role'])] = rel
            if not (base / rel).is_file():
                reqs.append({'group_id': r['group_id'], 't': r['t'], 'output': base / rel})
        if reqs:
            try:
                w, h = sp.plate_size(plan['fmt'])
                wb = render_format(read(base / 'settings.json', {}), w // 2, h // 2)
                sp.render_clean_frames(base, plan['episode'], reqs, wb['width'], wb['height'])
            except Exception as error:  # noqa: BLE001
                print(f'白模帧渲染失败({error}),比较页不带白模帧', file=sys.stderr)
                frames = {k: v for k, v in frames.items() if (base / v).is_file()}
    picks = [{'shot_id': r['shot_id'], 'group_id': r['group_id'], 'role': r['role'], 'camera': r['facts'],
              'grid4': r['grid4'] and {**r['grid4']['info'], 'reasons': r['grid4']['reasons']},
              'grid9': r['grid9'] and {**r['grid9']['info'], 'reasons': r['grid9']['reasons']}, 'current': r['current']} for r in rows]
    (base / 'assets/concepts/scenes' / sid / DIRNAME / f'picks_{ep}.json').write_text(json.dumps(picks, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    page = write_compare(base, args.project, sid, ep, idx, rows, frames, scheme_key)
    n4 = sum(1 for r in rows if r['grid4'] and not r['grid4']['reasons'])
    n9 = sum(1 for r in rows if r['grid9'] and not r['grid9']['reasons'])
    use4 = Counter(f"{r['grid4']['info']['tile']}" for r in rows if r['grid4'])
    why4 = Counter(x.split(' ')[0] for r in rows if r['grid4'] for x in r['grid4']['reasons'])
    print(f'{sid} {ep}:背景图需求 {len(rows)} 条;四宫格合适 {n4} / 不合适 {len(rows) - n4}(原因 {dict(why4)});九宫格合适 {n9}(同一套打分);'
          f'四格被选次数 {dict(sorted(use4.items()))}')
    print(f'compare: {page}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
