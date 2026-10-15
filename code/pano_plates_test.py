#!/usr/bin/env python3
"""全景图截图当分镜背景图的试验(2026-10-04):本集某场景每条背景图需求(镜首/镜尾)都从该场景已出的全景图里按本镜方向直接截一张。

口径同场景预览页全景 360° 视窗的「💾 背景图」:视点 = 全景锚点(原地转头,不做深度重投影),只取本镜机位的位置(= 选离它水平最近的
已出图锚点)和方向(朝向 + 俯仰),不取镜头焦段(视场固定为视窗的垂直 75°,--fov 可改)也不取机高(锚点高度是多少就是多少)。
同一锚点同一方向的需求共用一张截图。纯本地重采样,不调图像模型、不计费。

试验链路:截图单独放 assets/concepts/scenes/<sid>/pano_plates_test/,比较页 qa/pano_plates_compare/<sid>.html,
不写背景图库 plates/index.json、不改集索引 directing/<ep>/shot_plates.json、不接组 prompt refs。

比较页逐镜并排:白模帧(机位真值)/ 全景截图(本试验)/ 世界模型截图(跑过 code/world_plates_test.py 才有)/ 现行采用图 /
九宫格最近格 / 四宫格最近格(跑过 code/grid4_test.py 才有)/ 全景图模式存档母图(plates/_archive_pano_*)。

用法:
  python code/pano_plates_test.py --project alices --ep ep01 --scene SCN-long-hall
  python code/pano_plates_test.py --project alices --ep ep01 --scene SCN-long-hall --compare-only   # 不重截,只重出比较页
  可选:--fov 75(垂直视场°)、--scheme <光照方案 id>、--force(已有截图也重截)、--whitebox(改从锚点的白模全景截,核对方向用)。
"""
import json
import math
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args  # noqa: E402
from grid4_test import CARD_ZH, CSS, JS, REUSE_ZH, esc  # noqa: E402
from world_plates_test import DIRNAME as WORLD_DIRNAME, archived_masters, image_stats  # noqa: E402
from modules import scene_panos  # noqa: E402
from modules import shot_plates as sp  # noqa: E402
from modules.whitebox import component, read, render_format  # noqa: E402

DIRNAME = 'pano_plates_test'
VIEWER_FOV_V = 75.0          # pano-viewer.js 的 FOV0(「💾 背景图」默认存的视场)
GRADE_ZH = {'match': '构图对得上白模帧', 'partial': '方向对、但机位/机高差得明显', 'off': '对不上(离锚点远 / 贴地 / 被近处物体挡住)'}


def capture_key(anchor_id: str, facts: dict) -> tuple:
    return (anchor_id, round(facts['bearing_deg']), round(facts['pitch_deg']))


def nearest_anchor(anchors: list, pos) -> tuple[dict, float]:
    """离本镜机位水平最近的锚点(不看高度)。"""
    best = min(anchors, key=lambda a: math.hypot(a['position'][0] - pos[0], a['position'][2] - pos[2]))
    return best, math.hypot(best['position'][0] - pos[0], best['position'][2] - pos[2])


def pano_view(pano, yaw_deg: float, direction, fov_v: float, width: int, height: int):
    """等距柱状全景 → 原地朝 direction 看的直线透视画面(投影约定同 scene_panos._equirect_rays:中心列 = 锚点 yaw)。"""
    import cv2
    import numpy as np
    _, dirs = scene_panos._view_rays({'position': [0, 0, 0], 'target': list(direction), 'fov_v_deg': fov_v}, width, height)
    yaw = math.radians(yaw_deg); cy, sy = math.cos(yaw), math.sin(yaw)
    dx = cy * dirs[:, 0] - sy * dirs[:, 2]; dz = sy * dirs[:, 0] + cy * dirs[:, 2]
    theta = np.arctan2(dx, -dz); phi = np.arccos(np.clip(dirs[:, 1], -1, 1))
    ph, pw = pano.shape[:2]
    u = ((theta + np.pi) / (2 * np.pi) * pw - .5).astype(np.float32).reshape(height, width)
    v = (phi / np.pi * ph - .5).astype(np.float32).reshape(height, width)
    return cv2.remap(pano, u, v, cv2.INTER_CUBIC, borderMode=cv2.BORDER_WRAP)


def capture(base: Path, sid: str, jobs: list, scheme_key: str, *, fov: float, force=False, whitebox=False, log=print) -> dict:
    import cv2
    out = base / 'assets/concepts/scenes' / sid / DIRNAME
    rel = f'assets/concepts/scenes/{sid}/{DIRNAME}'
    pidx = scene_panos.load_index(base, sid)
    pdir = scene_panos.panos_dir(base, sid)
    anchors = [a for a in pidx.get('anchors', []) if (pdir / a['anchor_id'] / f'{scheme_key}.png').is_file()]
    if not anchors:
        raise FileNotFoundError(f'{sid}: 方案 {scheme_key} 还没有已出图的全景锚点')
    pw, ph = sp.plate_size()   # 截图一律 16:9(sp.PLATE_FMT,2026-10-09),不随项目画幅
    idx = read(out / 'index.json', {}) or {}
    if idx.get('fov_v_deg') != fov or idx.get('whitebox') != whitebox or idx.get('size') != [pw, ph]:
        idx = {}
    shots = {} if force else dict(idx.get('shots') or {})
    panos, done, n_new = {}, {}, 0
    for j in jobs:
        name = f"{j['shot_id']}_{j['role']}"
        f = j['facts']
        a, dist = nearest_anchor(anchors, f['position'])
        k = capture_key(a['anchor_id'], f)
        prev = shots.get(name)
        if prev and (base / prev['file']).is_file() and prev.get('capture_key') == list(k):
            done.setdefault(k, prev['file'])
            continue
        file = done.get(k)
        if file is None:
            file = done[k] = f'{rel}/{name}.jpg'
            if a['anchor_id'] not in panos:
                src = pdir / a['anchor_id'] / ('whitebox_pano.jpg' if whitebox else f'{scheme_key}.png')
                panos[a['anchor_id']] = cv2.imread(str(src), cv2.IMREAD_COLOR)
            d = sp.sub(f['target'], f['position'])
            img = pano_view(panos[a['anchor_id']], float(a.get('yaw_deg') or 0), d, fov, pw, ph)
            (base / file).parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(base / file), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
            n_new += 1
        shots[name] = {'shot_id': j['shot_id'], 'role': j['role'], 'group_id': j['group_id'], 'file': file, 'capture_key': list(k),
                       'anchor_id': a['anchor_id'], 'anchor_position_m': a['position'], 'anchor_distance_m': round(dist, 2),
                       'height_delta_m': round(f['height_m'] - a['position'][1], 2),
                       'view': {'bearing_deg': f['bearing_deg'], 'pitch_deg': f['pitch_deg'], 'fov_v_deg': fov},
                       'shot_fov_v_deg': f['fov_v_deg'], 'shot_height_m': f['height_m']}
    log(f'== {sid} 全景截图:{len(jobs)} 条需求 → 新截 {n_new} 张 {pw}x{ph},锚点 {[a["anchor_id"] for a in anchors]},垂直视场固定 {fov:g}°(不看焦段/机高)')
    stats = {}
    for s in shots.values():
        if (base / s['file']).is_file():
            s.update(stats.setdefault(s['file'], image_stats(base / s['file'])))
    idx = {'schema_version': 'pano_plates_test.v1', 'scene_id': sid, 'scheme': scheme_key, 'fov_v_deg': fov, 'size': [pw, ph], 'whitebox': whitebox,
           'anchors': [{'anchor_id': a['anchor_id'], 'position': a['position'], 'yaw_deg': a.get('yaw_deg')} for a in anchors], 'shots': shots}
    out.mkdir(parents=True, exist_ok=True)
    (out / 'index.json').write_text(json.dumps(idx, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return idx


def write_compare(base: Path, project: str, sid: str, ep: str, idx: dict, jobs: list, frames: dict, scheme_key: str, fmt: dict) -> Path:
    out = base / 'qa' / 'pano_plates_compare' / f'{sid}.html'
    out.parent.mkdir(parents=True, exist_ok=True)

    def url(p):
        return str(Path('..') / '..' / p) if p else ''

    sdir = base / 'assets/concepts/scenes' / sid
    lib = sp.load_library(base, sid)
    by_key = {e['key']: e for e in lib.get('plates', [])}
    tiles9 = sp.grid9_entries(lib, scheme_key)
    g4 = read(sdir / 'grid4' / 'index.json', {}) or {}
    tiles4 = sorted([e for e in g4.get('tiles', []) if (e.get('pano_ref') or {}).get('scheme') == scheme_key], key=lambda e: e['pano_ref']['tile'])
    world = (read(sdir / WORLD_DIRNAME / 'index.json', {}) or {}).get('shots') or {}
    world_grades = read(sdir / WORLD_DIRNAME / 'grades.json', {}) or {}
    masters = archived_masters(base, sid)
    by_creator = {}
    for e in masters:
        cb = e.get('created_by') or {}
        if cb.get('shot_id'):
            by_creator.setdefault((cb['shot_id'], cb.get('role') or 'start'), e)
    aspect = fmt['width'] / fmt['height']
    current = (read(base / 'directing' / ep / 'shot_plates.json', {}) or {}).get('shots', {})
    shots = idx.get('shots') or {}
    grades = read(sdir / DIRNAME / 'grades.json', {}) or {}   # 目视分级(可选):{"<shot>_<role>": match|partial|off, "_note": "页头说明"}
    note = grades.pop('_note', '')
    by_file = {s['file']: grades[n] for n, s in shots.items() if n in grades}
    rows = []
    for j in sorted(jobs, key=lambda j: (j['shot_id'], j['role'] == 'end')):
        name = f"{j['shot_id']}_{j['role']}"
        s = shots.get(name) or {}
        cur = next((p for p in (current.get(j['shot_id']) or {}).get('plates', []) if p.get('role') == j['role']), None) or {}
        e9, i9 = sp.pick_grid9_tile(tiles9, j['facts'])
        e4, i4 = sp.pick_grid9_tile(tiles4, j['facts']) if tiles4 else (None, {})
        m = sp.find_master({'plates': masters}, j['scheme'], j['facts'], aspect, base, require_file=False) if masters else None
        rows.append({'job': j, 'name': name, 'pano': s, 'grade': by_file.get(s.get('file')), 'world': world.get(name) or {}, 'cur': cur,
                     'g9': (e9, i9), 'g4': (e4, i4), 'master': m or by_creator.get((j['shot_id'], j['role']))})
    files = {r['pano'].get('file') for r in rows if r['pano'].get('file')}
    n_grade = Counter(r['grade'] for r in rows if r['grade'])
    use = Counter(r['pano'].get('anchor_id') for r in rows)
    dists = sorted(r['pano'].get('anchor_distance_m', 0) for r in rows)
    low = sum(1 for r in rows if r['pano'].get('height_delta_m', 0) <= -0.8)
    h = [f'<!doctype html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
         f'<title>全景截图背景图试验 · {esc(sid)} · {esc(ep)}</title><style>{CSS}table{{min-width:1750px}}td.img{{width:13.5%}}</style>'
         f'<script>{JS}</script></head><body><div id="zoom"><img></div>']
    h.append(f'<header><h1>全景图截图当分镜背景图 · {esc(project)} / {esc(ep)} / {esc(sid)}</h1>'
             f'<div class="meta">每条背景图需求选离本镜机位水平最近的全景锚点,在那张全景里原地朝本镜方向(朝向 + 俯仰)截一张,不看镜头焦段和机高:'
             f'垂直视场固定 {esc(idx.get("fov_v_deg"))}°(全景 360° 视窗「💾 背景图」的默认口径),视点高度 = 锚点高度。'
             f'全景 2880×1440,截图放大到 {esc("×".join(str(x) for x in idx.get("size", [])))};本地重采样不计费。试验数据不进背景图库、不改集索引。'
             + (f'<br><b>{esc(note)}</b>' if note else '') + '</div>'
             f'<div class="stats"><div class="stat"><b>{len(rows)}</b>背景图需求(镜首/镜尾)</div><div class="stat"><b>{len(files)}</b>实际截图张数</div>'
             + ''.join(f'<div class="stat"><b>{n}</b>用锚点 {esc(a)}</div>' for a, n in sorted(use.items()))
             + f'<div class="stat"><b>{dists[len(dists) // 2]} m</b>机位离锚点(中位;最远 {dists[-1]} m)</div>'
             f'<div class="stat"><b>{low}</b>本镜机高比锚点低 ≥ 0.8 m</div>'
             + ''.join(f'<div class="stat"><b class="{c}">{n_grade.get(g, 0)}</b>目视:{GRADE_ZH[g]}</div>'
                       for g, c in (('match', 'ok'), ('partial', ''), ('off', 'bad')) if grades) + '</div>'
             + ('<div class="filters"><button class="on" data-f="all">全部</button>'
                + ''.join(f'<button data-f="{g}">{GRADE_ZH[g]}</button>' for g in GRADE_ZH) + '</div>' if grades else '') + '</header>')
    h.append('<h2>逐镜对照</h2><div class="wrap"><table><thead><tr><th>镜</th><th>本镜白模帧(机位真值)</th><th>全景截图(本试验)</th><th>世界模型截图</th>'
             '<th>现行采用图</th><th>九宫格最近格</th><th>四宫格最近格</th><th>全景图模式存档母图</th></tr></thead><tbody>')

    def img(rel, cap=''):
        if not rel or not (base / rel).is_file():
            return '<td class="img"><i>无</i></td>'
        return f'<td class="img"><img src="{esc(url(rel))}" loading="lazy" onclick="zoom(this.src)">{cap}</td>'

    def tile_cap(e, i, zh):
        if not e:
            return ''
        reasons = sp.grid9_unfit_reasons(i)
        verdict = '<span class="tag ok">合适</span>' if not reasons else '<span class="tag bad">不合适</span>'
        label = f'第 {i["tile"]} 格' + (f' · 朝{CARD_ZH[e["pano_ref"]["view"]["card"]]}' if zh else '')
        return f'<div class="cap">{verdict}{label} · 朝向差 {esc(i["bearing_delta_deg"])}° · 距 {esc(i["distance_m"])} m</div>'

    world_zh = {'sharp': '清晰可用', 'smeared': '拉丝发糊', 'unusable': '不可用'}
    for r in rows:
        j, c, p, cur = r['job'], r['job']['facts'], r['pano'], r['cur']
        frame = frames.get((j['shot_id'], j['role']))
        h.append(f'<tr data-kind="{esc(r["grade"] or "")}"><td class="shot"><b>{esc(j["shot_id"])}</b> {esc(j["role"])}<br>{esc(j["group_id"])} · {esc(j["tier"].get("category"))}'
                 f'<div class="desc">{esc((j.get("shot") or {}).get("camera_position", ""))}</div></td>')
        h.append(img(frame, f'<dl><div><dt>朝向</dt><dd>{esc(c.get("bearing_deg"))}°</dd></div><div><dt>机高</dt><dd>{esc(c.get("height_m"))} m</dd></div>'
                            f'<div><dt>俯仰</dt><dd>{esc(c.get("pitch_deg"))}°</dd></div><div><dt>镜头</dt><dd>{esc(c.get("lens_mm_equiv"))}mm · 垂直 {esc(c.get("fov_v_deg"))}°</dd></div></dl>'))
        shared = '' if (p.get('file') or '').endswith(f'{r["name"]}.jpg') else f'<div><dt>共用</dt><dd>{esc(Path(p.get("file") or "").stem)}</dd></div>'
        tag = (f'<div class="cap"><span class="tag {"ok" if r["grade"] == "match" else "bad" if r["grade"] == "off" else "cur"}">目视:{GRADE_ZH[r["grade"]]}</span></div>'
               if r['grade'] else '')
        h.append(img(p.get('file'), f'<dl><div><dt>锚点</dt><dd>{esc(p.get("anchor_id"))} · 离本镜机位 {esc(p.get("anchor_distance_m"))} m</dd></div>'
                                    f'<div><dt>机高差</dt><dd>本镜 {esc(p.get("shot_height_m"))} m,锚点 {esc((p.get("anchor_position_m") or [0, 0, 0])[1])} m</dd></div>'
                                    f'<div><dt>视场</dt><dd>垂直 {esc(idx.get("fov_v_deg"))}°(本镜 {esc(p.get("shot_fov_v_deg"))}°)</dd></div>{shared}</dl>{tag}'))
        w = r['world']
        wg = world_grades.get(r['name'])
        h.append(img(w.get('file'), (f'<div class="cap"><span class="tag {"ok" if wg == "sharp" else "bad" if wg == "unusable" else "cur"}">目视:{world_zh[wg]}</span></div>' if wg else '')))
        h.append(img(cur.get('file'), f'<div class="cap"><span class="tag cur">{esc(REUSE_ZH.get(cur.get("reuse"), cur.get("reuse")))}</span>{esc(cur.get("key"))}</div>'
                     + (f'<div class="cap">原图来源:{esc((by_key.get(cur.get("key")) or {}).get("pano_ref", {}).get("kind"))}</div>' if by_key.get(cur.get('key')) else '')))
        h.append(img(r['g9'][0] and r['g9'][0]['file'], tile_cap(r['g9'][0], r['g9'][1], False)))
        h.append(img(r['g4'][0] and r['g4'][0]['file'], tile_cap(r['g4'][0], r['g4'][1], True)))
        m = r['master']
        h.append(img(m and m['archive_file'], f'<div class="cap">{esc(m["key"])}</div>' if m else ''))
        h.append('</tr>')
    h.append('</tbody></table></div></body></html>')
    out.write_text('\n'.join(h), encoding='utf-8')
    return out


def main():
    def configure(ap):
        ap.add_argument('--scene', required=True)
        ap.add_argument('--fov', type=float, default=VIEWER_FOV_V, help='截图垂直视场°(缺省 75 = 全景视窗默认;不随本镜焦段变)')
        ap.add_argument('--scheme', default=None, help='光照方案 id,缺省本集该场景用得最多的')
        ap.add_argument('--force', action='store_true', help='已有截图也重截')
        ap.add_argument('--whitebox', action='store_true', help='改从锚点的白模全景截(核对方向换算用)')
        ap.add_argument('--compare-only', action='store_true', help='不截图,只重出比较页')
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
    if args.compare_only:
        idx = read(base / 'assets/concepts/scenes' / sid / DIRNAME / 'index.json', {}) or {}
        if not idx.get('shots'):
            print(f'{sid}: 还没有全景截图,先去掉 --compare-only', file=sys.stderr)
            return 1
    else:
        idx = capture(base, sid, jobs, scheme_key, fov=args.fov, force=args.force, whitebox=args.whitebox)
    frames, reqs = {}, []
    for j in jobs:
        name = f'{j["shot_id"]}_{j["role"]}.whitebox.jpg'
        rel = next((p for p in (f'qa/grid4_compare/frames/{sid}/{name}', f'qa/world_plates_compare/frames/{sid}/{name}') if (base / p).is_file()),
                   f'qa/pano_plates_compare/frames/{sid}/{name}')
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
    page = write_compare(base, args.project, sid, ep, idx, jobs, frames, scheme_key, plan['fmt'])
    shots = idx.get('shots') or {}
    use = Counter(s['anchor_id'] for s in shots.values())
    print(f"{sid} {ep}:背景图需求 {len(jobs)} 条,全景截图 {len({s['file'] for s in shots.values()})} 张(垂直视场 {idx.get('fov_v_deg')}°);"
          f"各锚点承担 {dict(sorted(use.items()))}")
    print(f'compare: {page}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
