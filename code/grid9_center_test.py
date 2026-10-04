#!/usr/bin/env python3
"""中心点九宫格背景图试验(2026-10-04):九格共用一个站位(像全景锚点那样原地转头),8 格各朝一个方向(每 45° 一格)+ 1 格仰拍。

与现行九宫格(layout.json#views 的 9 个语义机位)的区别:站位、机高、仰拍格都按本集该场景的白模机位推出来——
  站位 = 本集该场景各镜机位的水平中位点(落在实体内吸附到最近空点,--at 可改);
  机高 = 各镜机高中位数,夹在 0.9–1.6 m(--height 可改);
  第 1–8 格 = 北/东北/东/东南/南/西南/西/西北 平视(方位按场景布局图自己的叫法,见 grid4_test.plan_edges);
  第 9 格 = 仰拍镜(俯仰 > 20°)最集中的那个方向,仰角取这些镜的中位数(没有仰拍镜时改为朝机位最多的方向再出一格平视)。
参考图 = 标点俯视图(红点 + 八向箭头)+ 宫格版式模板;一次出图,按版式拆成 9 张,用九宫格同一套打分给本集每镜选格。

试验链路:产物单独放 assets/concepts/scenes/<sid>/grid9_center/ 与 qa/grid9_center_compare/,不写背景图库 plates/index.json、
不改集索引 directing/<ep>/shot_plates.json、不接组 prompt refs。

用法:
  python code/grid9_center_test.py --project alices --ep ep01 --scene SCN-long-hall --dry-run
  python code/grid9_center_test.py --project alices --ep ep01 --scene SCN-long-hall --seed 20261004
  python code/grid9_center_test.py --project alices --ep ep01 --scene SCN-long-hall --compare-only
  可选:--at x,z、--height 0.9、--fov 55(每格垂直视场°)、--scheme、--force(重出宫格,旧图改名 .prev-*)。
"""
import datetime as dt
import json
import math
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args  # noqa: E402
import grid4_test as g4  # noqa: E402
from grid4_test import CSS, JS, REUSE_ZH, esc  # noqa: E402
from world_plates_test import DIRNAME as WORLD_DIRNAME, archived_masters  # noqa: E402
from pano_plates_test import DIRNAME as PANO_DIRNAME  # noqa: E402
from modules import scene_panos  # noqa: E402
from modules import shot_plates as sp  # noqa: E402
from modules.whitebox import component, read, render_format  # noqa: E402

DIRNAME = 'grid9_center'
DIRS8 = ['north', 'north-east', 'east', 'south-east', 'south', 'south-west', 'west', 'north-west']
DIR_ZH = {'north': '北', 'north-east': '东北', 'east': '东', 'south-east': '东南', 'south': '南', 'south-west': '西南', 'west': '西', 'north-west': '西北'}
HEIGHT_RANGE = (0.9, 1.6)
TILE_FOV_V = 55.0
CLEARANCE_M = 0.6


def dir_vectors(edges: dict) -> dict:
    """八个方向 → 白模 (dx, dz) 单位向量(按场景布局图自己的方位叫法)。"""
    out = {}
    for name in DIRS8:
        vx = sum(g4.EDGE_VEC[edges[c]][0] for c in name.split('-'))
        vz = sum(g4.EDGE_VEC[edges[c]][1] for c in name.split('-'))
        n = math.hypot(vx, vz)
        out[name] = (vx / n, vz / n)
    return out


def name_of(vec, vectors: dict) -> str:
    return max(vectors, key=lambda k: vectors[k][0] * vec[0] + vectors[k][1] * vec[1])


def plan_side(name: str, edges: dict) -> str:
    """方向 → 布局图上朝哪:'the left edge' / 'the bottom-left corner'。"""
    es = [edges[c] for c in name.split('-')]
    if len(es) == 1:
        return f'the {es[0]} edge'
    v = next(e for e in es if e in ('top', 'bottom')); h = next(e for e in es if e in ('left', 'right'))
    return f'the {v}-{h} corner'


def auto_station(scene: dict, jobs: list, at, height) -> tuple[list, str]:
    hs = [j['facts']['height_m'] for j in jobs]
    h = height if height is not None else round(min(HEIGHT_RANGE[1], max(HEIGHT_RANGE[0], statistics.median(hs))), 2)
    if at:
        return [round(at[0], 2), h, round(at[1], 2)], '--at'
    x = statistics.median(j['facts']['position'][0] for j in jobs); z = statistics.median(j['facts']['position'][2] for j in jobs)
    solids = g4.blocking(scene, h)
    dx, _, dz = scene['dimensions_m']
    best = None
    for i in range(-int(dx * 2), int(dx * 2) + 1):
        for k in range(-int(dz * 2), int(dz * 2) + 1):
            px, pz = i * 0.25, k * 0.25
            if any(g4._inside(px, pz, o, CLEARANCE_M) for o in solids):
                continue
            d = math.hypot(px - x, pz - z)
            if best is None or d < best[0]:
                best = (d, px, pz)
    return [best[1], h, best[2]], f'本集机位水平中位点 [{x:.1f}, {z:.1f}] → 最近空网格点;机高 = 各镜机高中位数夹在 {HEIGHT_RANGE[0]}–{HEIGHT_RANGE[1]} m'


def ray_reach(scene: dict, pos, vec, h: float) -> float:
    far = 100.0
    hits = [g4._segment_hit(pos[0], pos[2], pos[0] + vec[0] * far, pos[2] + vec[1] * far, o) for o in g4.blocking(scene, h)]
    hits = [t * far for t in hits if t is not None and t * far > 0.05]
    return round(min(hits), 1) if hits else far


def tilt_tile(jobs: list, vectors: dict, ex, ez) -> dict:
    """第 9 格:仰/俯拍镜(|俯仰| > 20°)最集中的方向 + 这些镜的俯仰中位数;没有则朝机位最多的方向平视。"""
    def name_for(f):
        d = sp.sub(f['target'], f['position'])
        n = math.hypot(d[0], d[2]) or 1
        return name_of((d[0] / n, d[2] / n), vectors)
    tilted = [j['facts'] for j in jobs if j['facts']['pitch_deg'] > 20]
    if not tilted:
        return {'card': Counter(name_for(j['facts']) for j in jobs).most_common(1)[0][0], 'pitch': 0.0, 'basis': '无仰拍镜,取机位最多的方向'}
    card, n = Counter(name_for(f) for f in tilted).most_common(1)[0]
    pitch = round(statistics.median(f['pitch_deg'] for f in tilted if name_for(f) == card))
    return {'card': card, 'pitch': float(pitch), 'basis': f'{len(tilted)} 条仰拍镜里 {n} 条朝{DIR_ZH[card]},仰角中位 {pitch}°'}


def views9(scene: dict, layout: dict, fmt: dict, axes, pos, fov: float, edges: dict, tilt: dict) -> list[dict]:
    ex, ez, texts = axes
    vectors = dir_vectors(edges)
    landmarks = [lm for lm in layout.get('landmarks', []) if isinstance(lm, dict) and 'xy' in lm]
    dims = scene['dimensions_m']
    floor_scene = {**scene, 'objects': [o for o in scene.get('objects', []) if o['position'][1] - o['size_m'][1] / 2 <= 0.3]}
    out = []
    for i, (card, pitch) in enumerate([(c, 0.0) for c in DIRS8] + [(tilt['card'], tilt['pitch'])]):
        ux, uz = vectors[card]
        c = math.cos(math.radians(pitch))
        key = {'position': list(pos), 'target': [pos[0] + ux * 10 * c, pos[1] + 10 * math.sin(math.radians(pitch)), pos[2] + uz * 10 * c], 'fov': fov}
        facts = sp.camera_facts(key, fmt, ex, ez, texts)
        facts['standing'] = sp.standing_on(floor_scene, layout, key)
        project = sp.projector(key, fmt)
        seen, hidden = [], []
        for lm in landmarks:
            p = [(lm['xy'][0] - .5) * dims[0], 1.0, (lm['xy'][1] - .5) * dims[2]]
            name = lm.get('name_en') or lm.get('name') or lm['id']
            dist = math.hypot(p[0] - pos[0], p[2] - pos[2])
            if dist < 1.0 or lm.get('kind') in ('zone', 'space', 'direction'):
                continue
            q = project(p)
            if q and abs(q[0]) <= 1.0 and abs(q[1]) <= 1.2:
                seen.append((q[0], f"{name} ({sp.x_word(q[0], q[0], q[0])}, about {dist:.0f} m away)"))
            else:
                hidden.append(name)
        right = (-uz, ux)
        out.append({'tile': i + 1, 'card': card, 'pitch': pitch, 'plan_side': plan_side(card, edges), 'key': key, 'facts': facts,
                    'ahead_m': ray_reach(scene, pos, (ux, uz), pos[1]),
                    'left_card': name_of((-right[0], -right[1]), vectors), 'right_card': name_of(right, vectors),
                    'seen': [s for _, s in sorted(seen)], 'hidden': hidden, 'objects': g4.visible_objects(scene, key, fmt)})
    return out


def marked_plan(plan_file: Path, scene: dict, pos, edges: dict, output: Path) -> Path:
    from PIL import Image, ImageDraw, ImageFont
    im = Image.open(plan_file).convert('RGBA')
    W, H = im.size
    dx, _, dz = scene['dimensions_m']
    px, py = (pos[0] / dx + .5) * W, (pos[2] / dz + .5) * H
    ov = Image.new('RGBA', im.size, (0, 0, 0, 0))
    dr = ImageDraw.Draw(ov)
    try:
        font = ImageFont.truetype('/System/Library/Fonts/Helvetica.ttc', max(24, W // 60))
    except Exception:  # noqa: BLE001
        font = ImageFont.load_default()
    L, r = min(W, H) * 0.10, max(8, W // 200)
    sx, sz = W / dx, H / dz
    for name, (ux, uz) in dir_vectors(edges).items():
        n = math.hypot(ux * sx, uz * sz); vx, vy = ux * sx / n, uz * sz / n     # 图上方向(俯视图非等比时按像素比例)
        ex, ey = px + vx * L, py + vy * L
        dr.line([px, py, ex, ey], fill=(255, 30, 30, 235), width=5)
        nx, ny = -vy, vx
        dr.polygon([(ex + vx * 20, ey + vy * 20), (ex + nx * 11, ey + ny * 11), (ex - nx * 11, ey - ny * 11)], fill=(255, 30, 30, 235))
        ch = ''.join(w[0].upper() for w in name.split('-'))
        tx, ty = px + vx * (L + 52), py + vy * (L + 52)
        tw, th = dr.textbbox((0, 0), ch, font=font)[2:]
        dr.rectangle([tx - tw / 2 - 7, ty - th / 2 - 5, tx + tw / 2 + 7, ty + th / 2 + 7], fill=(255, 255, 255, 235))
        dr.text((tx - tw / 2, ty - th / 2), ch, fill=(220, 20, 20, 255), font=font)
    dr.ellipse([px - r, py - r, px + r, py + r], fill=(255, 20, 20, 255), outline=(255, 255, 255, 255), width=3)
    output.parent.mkdir(parents=True, exist_ok=True)
    Image.alpha_composite(im, ov).convert('RGB').save(output, quality=92)
    return output


def build_prompt(layout: dict, scene: dict, views: list, geom: dict, pos, style: str, lighting: str, desc: str, time_of_day: str, edges: dict) -> str:
    name = re.sub(r'[(（].*?[)）]', '', layout.get('scene_name_en') or layout.get('scene_name') or scene.get('name') or scene['scene_id']).strip()
    name = re.sub(r'^SCN-', '', name).replace('-', ' ')
    f0 = views[0]['facts']
    lines = [(f"One image that is a {geom['cols']} by {geom['rows']} grid of nine separate photographs of the exact same location, all taken from "
              f"one single camera standpoint: the camera stays on the same spot and only turns. Tiles 1 to 8 look level toward north, north-east, "
              f"east, south-east, south, south-west, west and north-west in turn, one photograph every 45 degrees, so that together they cover "
              f"the full 360-degree turn around that standpoint; tile 9 is the same standpoint with the camera tilted upward. Laid out as three "
              f"rows of three equal rectangular tiles with thin straight pure-white gutters between them, exactly matching the blank tiling "
              f"template in [Image 2]. Location: {name}. Time of day: {time_of_day or ''}." + (f" Lighting: {lighting}." if lighting else ''))]
    plan = ("[Image 1] is the top-down plan of this location, with the camera standpoint marked as a red dot and eight red arrows lettered "
            "N, NE, E, SE, S, SW, W, NW showing the viewing directions. Use it only as the spatial layout reference: which wall, door, column, "
            "object and open floor lies in each direction, how far away it is and what is beside it. Never reproduce the plan, its top-down "
            "viewpoint, the red dot, the arrows or the letters; no tile may be a top-down, overhead, bird's-eye or plan view. On the plan, "
            + ', '.join(f"{c} is toward the {edges[c]} edge" for c in g4.CARDS) + '.')
    lms = [f"{(lm.get('name_en') or lm.get('name') or lm['id'])} ({sp._plan_pos_word(lm['xy'])})" for lm in layout.get('landmarks', []) if 'xy' in lm]
    if lms:
        plan += " Landmarks on the plan: " + '; '.join(lms) + '.'
    lines.append(plan)
    lines.append(f"Every tile uses the same camera: standing {f0['standing']}, lens {pos[1]:g} m above the floor ({f0['height_word']}), "
                 f"{f0['lens_mm_equiv']:g}mm-equivalent rectilinear wide-angle lens with about {f0['fov_h_deg']:g} degrees horizontal field of view. "
                 f"In tiles 1 to 8 the lens axis is perfectly horizontal — not looking up, not looking down, no roll — so the horizon is a level "
                 f"line at mid-height and every vertical edge stays vertical; straight edges stay straight, no fisheye. Neighbouring directions "
                 f"overlap: what is at the edge of one tile is near the centre of the next.")
    lines.append("The architecture, materials, set dressing, weather, light direction and colour grade are identical in every tile; only the "
                 "direction the camera faces changes from tile to tile. Finish every tile at full sharpness with deep focus, no shallow depth "
                 "of field, no bokeh, no vignetting.")
    for v in views:
        t = f"Tile {v['tile']} ({sp.grid_tile_word(geom, v['tile'] - 1)}): looking {v['card']}, toward {v['plan_side']} of the plan"
        if v['pitch']:
            t += (f", with the camera tilted up about {v['pitch']:g} degrees: the frame is dominated by the upper part of the wall in that direction "
                  f"and the ceiling above it, the floor is out of frame.")
        else:
            t += f". The nearest solid surface straight ahead is about {v['ahead_m']:g} m away."
        t += f" Frame left is {v['left_card']}, frame right is {v['right_card']}."
        if v['seen']:
            t += ' In view from left to right: ' + '; '.join(v['seen']) + '.'
        if v['objects']:
            t += ' Set dressing in this view: ' + '; '.join(v['objects']) + '.'
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


def ensure_grid(base: Path, sid: str, scheme_id: str, scheme_key: str, time_of_day: str, plan: dict, jobs: list, *, at=None, height=None,
                fov=TILE_FOV_V, force=False, dry_run=False, seed=None, log=print) -> dict:
    scene, layout, fmt, axes = plan['episode']['scenes'][sid], plan['layouts'][sid], plan['fmt'], plan['axes'][sid]
    out = base / 'assets/concepts/scenes' / sid / DIRNAME
    rel = f'assets/concepts/scenes/{sid}/{DIRNAME}'
    idx = read(out / 'index.json', {}) or {}
    stem = f'{scheme_key}_grid9c'
    have = [e for e in idx.get('tiles', []) if (e.get('pano_ref') or {}).get('scheme') == scheme_key]
    if len(have) == 9 and not force and not dry_run and all((base / e['file']).is_file() for e in have):
        log(f'== {sid} 中心点九宫格 {stem} 已有,跳过出图(--force 重出)')
        return idx
    plan_file = base / 'assets/concepts/scenes' / sid / (layout.get('layout_top') or 'layout_top.png')
    edges = g4.plan_edges(layout)
    pos, basis = auto_station(scene, jobs, at, height)
    tilt = tilt_tile(jobs, dir_vectors(edges), axes[0], axes[1])
    views = views9(scene, layout, fmt, axes, pos, fov, edges, tilt)
    geom = sp.grid_geometry(9, fmt)
    pw, ph = sp.plate_size(fmt)
    style_doc = read(base / 'bible/style.json', {}) or {}
    desc, scene_neg = sp.scene_description(base, sid)
    prompt = build_prompt(layout, scene, views, geom, pos, style_doc.get('style_fragment_en') or '', sp.lighting_fragment(base, sid, scheme_id),
                          desc, time_of_day, edges)
    negative = ', '.join(x for x in (sp.plate_negative(style_doc.get('negative_prompt_en') or ''), scene_neg, sp.NEGATIVE_GRID, sp.NEGATIVE_MASTER,
                                     'panorama, cube map, dutch angle, converging verticals, keystone distortion') if x)
    marked_rel, tmpl_rel, sheet_rel = f'{rel}/{stem}.plan.jpg', f'{rel}/{stem}.template.jpg', f'{rel}/{stem}.png'
    marked_plan(plan_file, scene, pos, edges, base / marked_rel)
    sp.compose_grid_sheet([], geom, base / tmpl_rel)
    (out / f'{stem}.prompt.txt').write_text(prompt + '\n\nNEGATIVE: ' + negative + '\n', encoding='utf-8')
    log(f"== {sid} 中心点九宫格 {stem}:站位 {pos}({basis});每格垂直视场 {fov:g}°(水平 {views[0]['facts']['fov_h_deg']}°);"
        f"第 9 格 朝{DIR_ZH[tilt['card']]} 仰 {tilt['pitch']:g}°({tilt['basis']});宫格 {geom['width']}x{geom['height']},格 {geom['tile_w']}x{geom['tile_h']} → 拆后 {pw}x{ph}")
    if dry_run:
        log(prompt)
        return {}
    from modules.genmedia import generate_image, get_config, image_pref_env
    with image_pref_env('scenes'):
        cfg = get_config('image')
    channel = {'provider': cfg.get('provider'), 'model': cfg.get('model')}
    use_seed = seed if seed is not None else __import__('random').randint(1, 2**31 - 1)
    if (base / sheet_rel).is_file():
        stamp = dt.datetime.now().strftime('%H%M%S')
        for f in [base / sheet_rel] + [out / f'{stem}_t{i}.png' for i in range(1, 10)]:
            if f.is_file():
                f.rename(f.with_name(f'{f.stem}.prev-{stamp}.png'))
    log(f"   出图 {channel['provider']}/{channel['model']} {geom['width']}x{geom['height']} seed {use_seed},参考图 = 标点俯视图 + 版式模板 …")
    generate_image(prompt, str(base / sheet_rel), negative=negative, refs=[str(base / marked_rel), str(base / tmpl_rel)],
                   size=f"{geom['width']}x{geom['height']}", seed=use_seed)
    now = dt.datetime.now().isoformat(timespec='seconds')
    tiles = [{'key': f"{stem}_t{v['tile']}", 'grid9_center': True, 'master': False, 'file': f"{rel}/{stem}_t{v['tile']}.png",
              'lighting_scheme_id': scheme_id, 'time_of_day': time_of_day, 'camera': v['facts'], 'size': f'{pw}x{ph}', 'seed': use_seed,
              'refs': [marked_rel, tmpl_rel], 'channel': channel,
              'pano_ref': {'kind': 'grid9_center', 'sheet': sheet_rel, 'cols': geom['cols'], 'rows': geom['rows'], 'tile': v['tile'] - 1,
                           'scheme': scheme_key, 'view': {'card': v['card'], 'pitch_deg': v['pitch'], 'ahead_m': v['ahead_m']}},
              'written_at': now} for v in views]
    results = sp.split_grid_sheet(base / sheet_rel, geom, [base / e['file'] for e in tiles], (pw, ph))
    for e, r in zip(tiles, results):
        e['pano_ref']['box'] = r['box']
    idx = {'schema_version': 'grid9_center_test.v1', 'scene_id': sid, 'scheme': scheme_key, 'station': {'position_m': pos, 'basis': basis},
           'tilt_tile': tilt, 'fov_v_deg': fov, 'plan_edges': edges, 'geometry': geom, 'sheet': sheet_rel, 'marked_plan': marked_rel,
           'seed': use_seed, 'channel': channel, 'prompt': prompt, 'negative': negative, 'written_at': now, 'tiles': tiles}
    (out / 'index.json').write_text(json.dumps(idx, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    log(f'saved: {sheet_rel}(+9 格)')
    return idx


def tile_label(e: dict) -> str:
    v = e['pano_ref']['view']
    return f"第 {e['pano_ref']['tile'] + 1} 格 · 朝{DIR_ZH[v['card']]}" + (f" 仰 {v['pitch_deg']:g}°" if v.get('pitch_deg') else '')


def write_compare(base: Path, project: str, sid: str, ep: str, idx: dict, jobs: list, frames: dict, scheme_key: str, fmt: dict) -> tuple[Path, dict]:
    out = base / 'qa' / 'grid9_center_compare' / f'{sid}.html'
    out.parent.mkdir(parents=True, exist_ok=True)

    def url(p):
        return str(Path('..') / '..' / p) if p else ''

    sdir = base / 'assets/concepts/scenes' / sid
    tiles_c = sorted(idx.get('tiles', []), key=lambda e: e['pano_ref']['tile'])
    lib = sp.load_library(base, sid)
    tiles9 = sp.grid9_entries(lib, scheme_key)
    gi = read(sdir / 'grid4' / 'index.json', {}) or {}
    tiles4 = sorted([e for e in gi.get('tiles', []) if (e.get('pano_ref') or {}).get('scheme') == scheme_key], key=lambda e: e['pano_ref']['tile'])
    world = (read(sdir / WORLD_DIRNAME / 'index.json', {}) or {}).get('shots') or {}
    pano = (read(sdir / PANO_DIRNAME / 'index.json', {}) or {}).get('shots') or {}
    masters = archived_masters(base, sid)
    by_creator = {((e.get('created_by') or {}).get('shot_id'), (e.get('created_by') or {}).get('role') or 'start'): e for e in reversed(masters)}
    aspect = fmt['width'] / fmt['height']
    current = (read(base / 'directing' / ep / 'shot_plates.json', {}) or {}).get('shots', {})
    grades = read(sdir / DIRNAME / 'grades.json', {}) or {}     # 目视(可选):{"t<格号>": "说明", "_note": "页头说明"}
    rows = []
    for j in sorted(jobs, key=lambda j: (j['shot_id'], j['role'] == 'end')):
        name = f"{j['shot_id']}_{j['role']}"
        ec, ic = sp.pick_grid9_tile(tiles_c, j['facts'])
        e9, i9 = sp.pick_grid9_tile(tiles9, j['facts'])
        e4, i4 = sp.pick_grid9_tile(tiles4, j['facts']) if tiles4 else (None, {})
        cur = next((p for p in (current.get(j['shot_id']) or {}).get('plates', []) if p.get('role') == j['role']), None) or {}
        m = sp.find_master({'plates': masters}, j['scheme'], j['facts'], aspect, base, require_file=False) if masters else None
        rows.append({'job': j, 'name': name, 'c': (ec, ic, sp.grid9_unfit_reasons(ic)), 'g9': (e9, i9, sp.grid9_unfit_reasons(i9)),
                     'g4': (e4, i4, sp.grid9_unfit_reasons(i4)), 'cur': cur, 'pano': pano.get(name) or {}, 'world': world.get(name) or {},
                     'master': m or by_creator.get((j['shot_id'], j['role']))})
    stats = {'n': len(rows), 'center': sum(1 for r in rows if not r['c'][2]), 'grid9': sum(1 for r in rows if not r['g9'][2]),
             'grid4': sum(1 for r in rows if r['g4'][0] and not r['g4'][2]),
             'why': dict(Counter(x.split(' ')[0] for r in rows for x in r['c'][2])),
             'use': dict(sorted(Counter(r['c'][1]['tile'] for r in rows).items()))}
    fit = sp.GRID9_FIT
    st = idx.get('station', {}); tilt = idx.get('tilt_tile', {})
    h = [f'<!doctype html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
         f'<title>中心点九宫格试验 · {esc(sid)} · {esc(ep)}</title><style>{CSS}table{{min-width:2000px}}td.img{{width:11.8%}}'
         f'.tiles{{grid-template-columns:repeat(3,1fr)}}</style><script>{JS}</script></head><body><div id="zoom"><img></div>']
    h.append(f'<header><h1>中心点九宫格(同一站位 8 向 + 1 格仰拍)试验 · {esc(project)} / {esc(ep)} / {esc(sid)}</h1>'
             f'<div class="meta">站位 {esc(st.get("position_m"))}({esc(st.get("basis"))});第 1–8 格每 45° 一格平视,第 9 格朝{DIR_ZH.get(tilt.get("card"), "")} 仰 '
             f'{esc(tilt.get("pitch"))}°({esc(tilt.get("basis"))});每格垂直视场 {esc(idx.get("fov_v_deg"))}°。参考图 = 标点俯视图 + 版式模板;'
             f'{esc((idx.get("channel") or {}).get("provider"))} {esc((idx.get("channel") or {}).get("model"))} · seed {esc(idx.get("seed"))}。'
             f'选格与「合适」判定沿用九宫格同一套:朝向差 ≤ {fit["bearing_deg"]:g}°、俯仰差 ≤ {fit["pitch_deg"]:g}°、机位距 ≤ {fit["distance_m"]:g} m、机高档差 &lt; {fit["height_class_delta"]}'
             f'(差一档机高算合适:0.9 m 的格子配贴地镜头也过)。试验数据不进背景图库、不改集索引。'
             + (f'<br><b>{esc(grades.get("_note"))}</b>' if grades.get('_note') else '') + '</div>'
             f'<div class="stats"><div class="stat"><b>{stats["n"]}</b>背景图需求(镜首/镜尾)</div>'
             f'<div class="stat"><b class="ok">{stats["center"]}</b>中心点九宫格合适</div><div class="stat"><b>{stats["grid9"]}</b>现行九宫格合适</div>'
             f'<div class="stat"><b>{stats["grid4"]}</b>四宫格合适</div>'
             + ''.join(f'<div class="stat"><b>{n}</b>不合适原因:{esc(k)}</div>' for k, n in stats['why'].items()) + '</div>'
             f'<div class="filters"><button class="on" data-f="all">全部</button><button data-f="fit">中心点九宫格合适</button><button data-f="unfit">不合适</button></div></header>')
    h.append(f'<h2>中心点九宫格整图 与 标点俯视图</h2><div class="sheet"><img src="{esc(url(idx.get("sheet")))}" onclick="zoom(this.src)">'
             f'<img src="{esc(url(idx.get("marked_plan")))}" onclick="zoom(this.src)"></div>')
    h.append('<div class="tiles">' + ''.join(
        f'<figure><img src="{esc(url(e["file"]))}" onclick="zoom(this.src)"><figcaption>{tile_label(e)} · 正前方 {esc(e["pano_ref"]["view"]["ahead_m"])} m · '
        f'被选中 {stats["use"].get(e["pano_ref"]["tile"] + 1, 0)} 次' + (f'<br>目视:{esc(grades.get("t" + str(e["pano_ref"]["tile"] + 1)))}' if grades.get(f't{e["pano_ref"]["tile"] + 1}') else '')
        + '</figcaption></figure>' for e in tiles_c) + '</div>')
    h.append('<h2>逐镜对照</h2><div class="wrap"><table><thead><tr><th>镜</th><th>本镜白模帧(机位真值)</th><th>中心点九宫格选格(本试验)</th><th>现行采用图</th>'
             '<th>现行九宫格最近格</th><th>四宫格最近格</th><th>全景截图</th><th>世界模型截图</th><th>全景图模式存档母图</th></tr></thead><tbody>')

    def img(rel, cap=''):
        if not rel or not (base / rel).is_file():
            return '<td class="img"><i>无</i></td>'
        return f'<td class="img"><img src="{esc(url(rel))}" loading="lazy" onclick="zoom(this.src)">{cap}</td>'

    def tile_cap(t, label):
        e, i, reasons = t
        if not e:
            return ''
        verdict = '<span class="tag ok">合适</span>' if not reasons else '<span class="tag bad">不合适</span>' + ''.join(f'<span class="tag bad">{esc(x)}</span>' for x in reasons)
        return (f'<div class="cap">{verdict}</div><div class="cap">{label} · 朝向差 {esc(i["bearing_delta_deg"])}° · 距 {esc(i["distance_m"])} m · '
                f'机高档差 {esc(i["height_class_delta"])} · 俯仰差 {esc(i["pitch_delta_deg"])}°</div>')

    for r in rows:
        j, c, cur = r['job'], r['job']['facts'], r['cur']
        frame = frames.get((j['shot_id'], j['role']))
        h.append(f'<tr data-kind="{"fit" if not r["c"][2] else "unfit"}"><td class="shot"><b>{esc(j["shot_id"])}</b> {esc(j["role"])}<br>{esc(j["group_id"])} · {esc(j["tier"].get("category"))}'
                 f'<div class="desc">{esc((j.get("shot") or {}).get("camera_position", ""))}</div></td>')
        h.append(img(frame, f'<dl><div><dt>朝向</dt><dd>{esc(c.get("bearing_deg"))}°</dd></div><div><dt>机高</dt><dd>{esc(c.get("height_m"))} m</dd></div>'
                            f'<div><dt>俯仰</dt><dd>{esc(c.get("pitch_deg"))}°</dd></div><div><dt>镜头</dt><dd>{esc(c.get("lens_mm_equiv"))}mm · 垂直 {esc(c.get("fov_v_deg"))}°</dd></div></dl>'))
        h.append(img(r['c'][0] and r['c'][0]['file'], tile_cap(r['c'], tile_label(r['c'][0]) if r['c'][0] else '')))
        h.append(img(cur.get('file'), f'<div class="cap"><span class="tag cur">{esc(REUSE_ZH.get(cur.get("reuse"), cur.get("reuse")))}</span>{esc(cur.get("key"))}</div>'))
        h.append(img(r['g9'][0] and r['g9'][0]['file'], tile_cap(r['g9'], f'第 {r["g9"][1].get("tile")} 格')))
        h.append(img(r['g4'][0] and r['g4'][0]['file'], tile_cap(r['g4'], f'第 {r["g4"][1].get("tile")} 格')))
        h.append(img(r['pano'].get('file'), f'<div class="cap">锚点 {esc(r["pano"].get("anchor_id"))} · 离本镜 {esc(r["pano"].get("anchor_distance_m"))} m</div>'))
        h.append(img(r['world'].get('file'), f'<div class="cap">离世界模型锚点 {esc(r["world"].get("anchor_distance_m"))} m</div>'))
        m = r['master']
        h.append(img(m and m['archive_file'], f'<div class="cap">{esc(m["key"])}</div>' if m else ''))
        h.append('</tr>')
    h.append('</tbody></table></div></body></html>')
    out.write_text('\n'.join(h), encoding='utf-8')
    return out, stats


def main():
    def configure(ap):
        ap.add_argument('--scene', required=True)
        ap.add_argument('--dry-run', action='store_true')
        ap.add_argument('--force', action='store_true', help='重出宫格(旧图改名 .prev-*.png)')
        ap.add_argument('--compare-only', action='store_true', help='不出图,只重算选格并重出比较页')
        ap.add_argument('--at', default=None, help='站位 x,z(白模米制);缺省本集机位水平中位点')
        ap.add_argument('--height', type=float, default=None, help='机高 m;缺省各镜机高中位数夹在 0.9–1.6')
        ap.add_argument('--fov', type=float, default=TILE_FOV_V, help='每格垂直视场°,缺省 55')
        ap.add_argument('--scheme', default=None)
        ap.add_argument('--seed', type=int, default=None)
    args, base = parse_args(__doc__, configure=configure)
    sid, ep = component(args.scene), component(args.ep)
    plan = sp.plan_episode(base, ep)
    jobs = [j for j in plan['jobs'] if j['scene_id'] == sid]
    if not jobs:
        print(f'{sid}: {ep} 白模里没有该场景的镜', file=sys.stderr)
        return 1
    scheme_id = args.scheme or Counter(j['scheme'] for j in jobs).most_common(1)[0][0]
    tod = next((j['raw_group'].get('time_of_day') for j in jobs if j['raw_group'].get('time_of_day')), '') or ''
    scheme_key = scene_panos.scheme_slug(scheme_id, tod)
    at = [float(v) for v in args.at.split(',')] if args.at else None
    if args.compare_only:
        idx = read(base / 'assets/concepts/scenes' / sid / DIRNAME / 'index.json', {}) or {}
    else:
        idx = ensure_grid(base, sid, scheme_id, scheme_key, tod, plan, jobs, at=at, height=args.height, fov=args.fov, force=args.force,
                          dry_run=args.dry_run, seed=args.seed)
    if args.dry_run:
        return 0
    if not idx.get('tiles'):
        print(f'{sid}: 还没有中心点九宫格,先去掉 --compare-only 出图', file=sys.stderr)
        return 1
    frames, reqs = {}, []
    for j in jobs:
        name = f'{j["shot_id"]}_{j["role"]}.whitebox.jpg'
        rel = next((p for p in (f'qa/grid4_compare/frames/{sid}/{name}',) if (base / p).is_file()), f'qa/grid9_center_compare/frames/{sid}/{name}')
        frames[(j['shot_id'], j['role'])] = rel
        if not (base / rel).is_file():
            reqs.append({'group_id': j['group_id'], 't': j['t'], 'output': base / rel})
    if reqs:
        try:
            w, h = sp.plate_size(plan['fmt'])
            wb = render_format(read(base / 'settings.json', {}), w // 2, h // 2)
            sp.render_clean_frames(base, plan['episode'], reqs, wb['width'], wb['height'])
        except Exception as error:  # noqa: BLE001
            print(f'白模帧渲染失败({error}),比较页不带白模帧', file=sys.stderr)
    page, stats = write_compare(base, args.project, sid, ep, idx, jobs, frames, scheme_key, plan['fmt'])
    print(f"{sid} {ep}:背景图需求 {stats['n']} 条;中心点九宫格合适 {stats['center']}(不合适原因 {stats['why']});"
          f"现行九宫格合适 {stats['grid9']};四宫格合适 {stats['grid4']};九格被选次数 {stats['use']}")
    print(f'compare: {page}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
