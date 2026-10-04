#!/usr/bin/env python3
"""世界模型截图当分镜背景图的试验(2026-10-04):本集某场景每条背景图需求(镜首/镜尾)都在该场景的世界模型里按白模机位直接截一张。

口径同场景预览页世界模型视窗的「💾 背景图」:只取本镜机位的位置、方向(朝向 + 俯仰)和高度,不取镜头焦段——视场固定为视窗的
垂直 60°(--fov 可改)。位置/方向相同的需求共用一张截图。纯本地渲染(无头 Chromium + Spark),不调图像模型、不计费。

试验链路:截图单独放 assets/concepts/scenes/<sid>/world_plates_test/,比较页 qa/world_plates_compare/<sid>.html,
不写背景图库 plates/index.json、不改集索引 directing/<ep>/shot_plates.json、不接组 prompt refs。

比较页逐镜并排:白模帧(机位真值)/ 世界模型截图 / 现行采用图(手选、九宫格格子或补图、修改版)/ 九宫格最近格 /
四宫格最近格(跑过 code/grid4_test.py 才有)/ 全景图模式存档母图(plates/_archive_pano_*,按母图制规则反推)。

用法:
  python code/world_plates_test.py --project alices --ep ep01 --scene SCN-long-hall
  python code/world_plates_test.py --project alices --ep ep01 --scene SCN-long-hall --compare-only   # 不重截,只重出比较页
  可选:--fov 60(垂直视场°)、--res full_res|500k|150k|100k(splats 精度,缺省最高)、--force(已有截图也重截)。
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
from modules import scene_panos, worldlabs  # noqa: E402
from modules import shot_plates as sp  # noqa: E402
from modules.whitebox import component, read, render_format  # noqa: E402

DIRNAME = 'world_plates_test'
VIEWER_FOV_V = 60.0          # world-viewer.js 视窗相机的垂直视场(「💾 背景图」存的就是它)
GRADE_ZH = {'sharp': '清晰可用', 'smeared': '看得出结构但拉丝发糊', 'unusable': '不可用(空白 / 糊成一片 / 出了世界模型范围)'}
NEAR_ANCHOR_M = 6.0          # 比较页「近 / 远」分档:机位离世界模型生成锚点的水平距离


def capture_key(facts: dict) -> tuple:
    """位置 5 cm、朝向/俯仰 1° 内视为同一机位(不看焦段)。"""
    p = facts['position']
    return (round(p[0] * 20), round(p[1] * 20), round(p[2] * 20), round(facts['bearing_deg']), round(facts['pitch_deg']))


def image_stats(path: Path) -> dict:
    """清晰度(灰度拉普拉斯方差,缩到宽 480 后算)与近黑像素占比(世界模型没覆盖到的空洞)。"""
    import numpy as np
    from PIL import Image
    im = np.asarray(Image.open(path).convert('L').resize((480, 270)), dtype=np.float32)
    lap = im[1:-1, 1:-1] * 4 - im[:-2, 1:-1] - im[2:, 1:-1] - im[1:-1, :-2] - im[1:-1, 2:]
    return {'sharpness': round(float(lap.var()), 1), 'dark_fraction': round(float((im < 12).mean()), 3)}


def capture(base: Path, sid: str, jobs: list, fmt: dict, *, fov: float, res=None, force=False, log=print) -> dict:
    out = base / 'assets/concepts/scenes' / sid / DIRNAME
    rel = f'assets/concepts/scenes/{sid}/{DIRNAME}'
    idx = read(out / 'index.json', {}) or {}
    if idx.get('fov_v_deg') != fov:
        idx = {}
    shots = {} if force else dict(idx.get('shots') or {})
    wj = worldlabs.read_world(base, sid) or {}
    anchor = ((wj.get('alignment') or {}).get('camera') or {}).get('position') or [0, 0, 0]
    pw, ph = sp.plate_size(fmt)
    by_key, requests = {}, []
    for j in jobs:
        name = f"{j['shot_id']}_{j['role']}"
        f = j['facts']
        prev = shots.get(name)
        if prev and (base / prev['file']).is_file() and prev.get('capture_key') == list(capture_key(f)):
            by_key.setdefault(capture_key(f), prev['file'])
            continue
        k = capture_key(f)
        file = by_key.get(k)
        if file is None:
            file = by_key[k] = f'{rel}/{name}.jpg'
            requests.append({'camera': {'position': f['position'], 'target': f['target'], 'fov_v_deg': fov}, 'output': base / file, 'name': name})
        shots[name] = {'shot_id': j['shot_id'], 'role': j['role'], 'group_id': j['group_id'], 'file': file, 'capture_key': list(k),
                       'camera': {'position': f['position'], 'target': f['target'], 'fov_v_deg': fov, 'bearing_deg': f['bearing_deg'],
                                  'pitch_deg': f['pitch_deg'], 'height_m': f['height_m']},
                       'shot_fov_v_deg': f['fov_v_deg'],
                       'anchor_distance_m': round(math.hypot(f['position'][0] - anchor[0], f['position'][2] - anchor[2]), 2)}
    if requests:
        log(f'== {sid} 世界模型截图:{len(jobs)} 条需求 → 新截 {len(requests)} 张 {pw}x{ph},垂直视场固定 {fov:g}°(不看焦段)')
        recs = worldlabs.render_world_views(base, sid, requests, width=pw, height=ph, res=res, log=log)
        errors = {r['name']: rec['error'] for r, rec in zip(requests, recs) if rec.get('error')}
        for name, s in shots.items():
            src = next((r['name'] for r in requests if str(base / s['file']) == str(r['output'])), None)
            if src in errors:
                s['error'] = errors[src]
        idx.update({'res': recs[0].get('res') if recs else res, 'render_s': round(sum(r.get('render_s') or 0 for r in recs), 1),
                    'world_id': wj.get('world_id')})
        if errors:
            log(f'   截图失败 {len(errors)} 张:{list(errors.items())[:3]}')
    stats = {}
    for s in shots.values():
        f = base / s['file']
        if f.is_file() and not s.get('error'):
            s.update(stats.setdefault(s['file'], image_stats(f)))
    idx.update({'schema_version': 'world_plates_test.v1', 'scene_id': sid, 'fov_v_deg': fov, 'size': [pw, ph], 'anchor_position_m': anchor,
                'anchor_id': (wj.get('input') or {}).get('anchor_id'), 'shots': shots})
    out.mkdir(parents=True, exist_ok=True)
    (out / 'index.json').write_text(json.dumps(idx, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return idx


def archived_masters(base: Path, sid: str) -> list:
    """切九宫格前全景图模式出的母图(库图被归档到 plates/_archive_pano_<日期>/,同名 .json 台账仍在)。"""
    out = []
    for adir in sorted((base / 'assets/concepts/scenes' / sid / 'plates').glob('_archive_pano_*')):
        for jf in sorted(adir.glob('*.json')):
            try:
                e = json.loads(jf.read_text(encoding='utf-8'))
            except Exception:  # noqa: BLE001
                continue
            png = adir / f"{e.get('key')}.png" if isinstance(e, dict) else None
            if png and e.get('master') and png.is_file():
                e['archive_file'] = str(png.relative_to(base))
                out.append(e)
    return out


def write_compare(base: Path, project: str, sid: str, ep: str, idx: dict, jobs: list, frames: dict, scheme_key: str, fmt: dict) -> Path:
    out = base / 'qa' / 'world_plates_compare' / f'{sid}.html'
    out.parent.mkdir(parents=True, exist_ok=True)

    def url(p):
        return str(Path('..') / '..' / p) if p else ''

    lib = sp.load_library(base, sid)
    by_key = {e['key']: e for e in lib.get('plates', [])}
    tiles9 = sp.grid9_entries(lib, scheme_key)
    g4 = read(base / 'assets/concepts/scenes' / sid / 'grid4' / 'index.json', {}) or {}
    tiles4 = sorted([e for e in g4.get('tiles', []) if (e.get('pano_ref') or {}).get('scheme') == scheme_key], key=lambda e: e['pano_ref']['tile'])
    masters = archived_masters(base, sid)
    by_creator = {}
    for e in masters:
        cb = e.get('created_by') or {}
        if cb.get('shot_id'):
            by_creator.setdefault((cb['shot_id'], cb.get('role') or 'start'), e)
    aspect = fmt['width'] / fmt['height']
    current = (read(base / 'directing' / ep / 'shot_plates.json', {}) or {}).get('shots', {})
    shots = idx.get('shots') or {}
    rows = []
    for j in sorted(jobs, key=lambda j: (j['shot_id'], j['role'] == 'end')):
        s = shots.get(f"{j['shot_id']}_{j['role']}") or {}
        cur = next((p for p in (current.get(j['shot_id']) or {}).get('plates', []) if p.get('role') == j['role']), None) or {}
        e9, i9 = sp.pick_grid9_tile(tiles9, j['facts'])
        e4, i4 = sp.pick_grid9_tile(tiles4, j['facts']) if tiles4 else (None, {})
        m = sp.find_master({'plates': masters}, j['scheme'], j['facts'], aspect, base, require_file=False) if masters else None
        m = m or by_creator.get((j['shot_id'], j['role']))
        rows.append({'job': j, 'world': s, 'cur': cur, 'g9': (e9, i9), 'g4': (e4, i4), 'master': m})
    files = {r['world'].get('file') for r in rows if r['world'].get('file')}
    # 目视分级(可选):world_plates_test/grades.json = {"<shot>_<role>": "sharp" | "smeared" | "unusable"},有则比较页按分级筛选
    grades = read(base / 'assets/concepts/scenes' / sid / DIRNAME / 'grades.json', {}) or {}
    by_file = {s['file']: grades[n] for n, s in shots.items() if n in grades}
    for r in rows:
        r['grade'] = by_file.get(r['world'].get('file'))
    n_grade = Counter(r['grade'] for r in rows if r['grade'])
    near = [r for r in rows if r['world'].get('anchor_distance_m', 99) <= NEAR_ANCHOR_M]
    far = [r for r in rows if r['world'].get('anchor_distance_m', 99) > NEAR_ANCHOR_M]

    def mean(rs, k):
        vs = [r['world'][k] for r in rs if r['world'].get(k) is not None]
        return round(sum(vs) / len(vs), 1 if k == 'sharpness' else 3) if vs else '—'

    h = [f'<!doctype html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
         f'<title>世界模型截图背景图试验 · {esc(sid)} · {esc(ep)}</title><style>{CSS}table{{min-width:1500px}}td.img{{width:15.5%}}</style>'
         f'<script>{JS}</script></head><body><div id="zoom"><img></div>']
    h.append(f'<header><h1>世界模型截图当分镜背景图 · {esc(project)} / {esc(ep)} / {esc(sid)}</h1>'
             f'<div class="meta">每条背景图需求按本镜白模机位的位置、方向(朝向 + 俯仰)和高度在世界模型里直接截一张,不看镜头焦段:'
             f'垂直视场固定 {esc(idx.get("fov_v_deg"))}°(世界模型视窗「💾 背景图」的口径)。世界模型由全景锚点 {esc(idx.get("anchor_id"))} '
             f'{esc(idx.get("anchor_position_m"))} 一张全景生成,splats 精度 {esc(idx.get("res"))};本地渲染不计费。'
             f'清晰度 = 灰度拉普拉斯方差(越大越清晰);近黑占比 = 世界模型没覆盖到的空洞。试验数据不进背景图库、不改集索引。</div>'
             f'<div class="stats"><div class="stat"><b>{len(rows)}</b>背景图需求(镜首/镜尾)</div><div class="stat"><b>{len(files)}</b>实际截图张数</div>'
             f'<div class="stat"><b>{esc(idx.get("render_s"))} s</b>截图总耗时</div>'
             f'<div class="stat"><b>{len(near)}</b>机位离锚点 ≤ {NEAR_ANCHOR_M:g} m · 清晰度均值 {mean(near, "sharpness")}</div>'
             f'<div class="stat"><b>{len(far)}</b>机位离锚点 &gt; {NEAR_ANCHOR_M:g} m · 清晰度均值 {mean(far, "sharpness")}</div>'
             + (''.join(f'<div class="stat"><b class="{c}">{n_grade.get(g, 0)}</b>目视:{GRADE_ZH[g]}</div>'
                        for g, c in (('sharp', 'ok'), ('smeared', ''), ('unusable', 'bad'))) if grades else '') + '</div>'
             + ('<div class="filters"><button class="on" data-f="all">全部</button>'
                + ''.join(f'<button data-f="{g}">{GRADE_ZH[g]}</button>' for g in GRADE_ZH) + '</div></header>' if grades else
                f'<div class="filters"><button class="on" data-f="all">全部</button><button data-f="near">离锚点 ≤ {NEAR_ANCHOR_M:g} m</button>'
                f'<button data-f="far">离锚点 &gt; {NEAR_ANCHOR_M:g} m</button></div></header>'))
    h.append('<h2>逐镜对照</h2><div class="wrap"><table><thead><tr><th>镜</th><th>本镜白模帧(机位真值)</th><th>世界模型截图(本试验)</th><th>现行采用图</th>'
             '<th>九宫格最近格</th><th>四宫格最近格</th><th>全景图模式存档母图</th></tr></thead><tbody>')

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
        return (f'<div class="cap">{verdict}{label} · 朝向差 {esc(i["bearing_delta_deg"])}° · 距 {esc(i["distance_m"])} m · 机高档差 {esc(i["height_class_delta"])} · '
                f'俯仰差 {esc(i["pitch_delta_deg"])}°</div>')

    for r in rows:
        j, c, w, cur = r['job'], r['job']['facts'], r['world'], r['cur']
        kind = r['grade'] if grades else 'near' if w.get('anchor_distance_m', 99) <= NEAR_ANCHOR_M else 'far'
        frame = frames.get((j['shot_id'], j['role']))
        h.append(f'<tr data-kind="{kind}"><td class="shot"><b>{esc(j["shot_id"])}</b> {esc(j["role"])}<br>{esc(j["group_id"])} · {esc(j["tier"].get("category"))}'
                 f'<div class="desc">{esc((j.get("shot") or {}).get("camera_position", ""))}</div></td>')
        h.append(img(frame, f'<dl><div><dt>朝向</dt><dd>{esc(c.get("bearing_deg"))}°</dd></div><div><dt>机高</dt><dd>{esc(c.get("height_m"))} m</dd></div>'
                            f'<div><dt>俯仰</dt><dd>{esc(c.get("pitch_deg"))}°</dd></div><div><dt>镜头</dt><dd>{esc(c.get("lens_mm_equiv"))}mm · 垂直 {esc(c.get("fov_v_deg"))}°</dd></div>'
                            f'<div><dt>位置</dt><dd>{esc(c.get("standing"))}</dd></div></dl>'))
        if w.get('error'):
            h.append(f'<td class="img"><i class="bad">截图失败:{esc(w["error"])}</i></td>')
        else:
            shared = '' if (w.get('file') or '').endswith(f"{j['shot_id']}_{j['role']}.jpg") else f'<div><dt>共用</dt><dd>{esc(Path(w.get("file") or "").stem)}</dd></div>'
            h.append(img(w.get('file'), f'<dl><div><dt>视场</dt><dd>垂直 {esc(w.get("camera", {}).get("fov_v_deg"))}°(本镜 {esc(w.get("shot_fov_v_deg"))}°)</dd></div>'
                                        f'<div><dt>离锚点</dt><dd>{esc(w.get("anchor_distance_m"))} m</dd></div><div><dt>清晰度</dt><dd>{esc(w.get("sharpness"))}</dd></div>'
                                        f'<div><dt>近黑占比</dt><dd>{esc(w.get("dark_fraction"))}</dd></div>{shared}</dl>'
                         + (f'<div class="cap"><span class="tag {"ok" if r["grade"] == "sharp" else "bad" if r["grade"] == "unusable" else "cur"}">'
                            f'目视:{GRADE_ZH[r["grade"]]}</span></div>' if r.get('grade') else '')))
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
        ap.add_argument('--fov', type=float, default=VIEWER_FOV_V, help='截图垂直视场°(缺省 60 = 世界模型视窗相机;不随本镜焦段变)')
        ap.add_argument('--res', default=None, help='splats 精度 full_res|500k|150k|100k,缺省最高')
        ap.add_argument('--force', action='store_true', help='已有截图也重截')
        ap.add_argument('--compare-only', action='store_true', help='不截图,只重出比较页')
    args, base = parse_args(__doc__, configure=configure)
    sid, ep = component(args.scene), component(args.ep)
    plan = sp.plan_episode(base, ep)
    jobs = [j for j in plan['jobs'] if j['scene_id'] == sid]
    if not jobs:
        print(f'{sid}: {ep} 白模里没有该场景的镜', file=sys.stderr)
        return 1
    if worldlabs.world_missing(base, sid):
        print(f'[world_missing] {sid}: 还没有世界模型,请先在场景预览页「🌍 世界模型」板块生成', file=sys.stderr)
        return 4
    scheme_id = Counter(j['scheme'] for j in jobs).most_common(1)[0][0]
    tod = next((j['raw_group'].get('time_of_day') for j in jobs if j['raw_group'].get('time_of_day')), '') or ''
    scheme_key = scene_panos.scheme_slug(scheme_id, tod)
    if args.compare_only:
        idx = read(base / 'assets/concepts/scenes' / sid / DIRNAME / 'index.json', {}) or {}
        if not idx.get('shots'):
            print(f'{sid}: 还没有世界模型截图,先去掉 --compare-only', file=sys.stderr)
            return 1
    else:
        idx = capture(base, sid, jobs, plan['fmt'], fov=args.fov, res=args.res, force=args.force)
    frames, reqs = {}, []
    for j in jobs:
        rel = next((p for p in (f'qa/grid4_compare/frames/{sid}/{j["shot_id"]}_{j["role"]}.whitebox.jpg',) if (base / p).is_file()),
                   f'qa/world_plates_compare/frames/{sid}/{j["shot_id"]}_{j["role"]}.whitebox.jpg')
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
    errs = [n for n, s in shots.items() if s.get('error')]
    print(f"{sid} {ep}:背景图需求 {len(jobs)} 条,世界模型截图 {len({s['file'] for s in shots.values()})} 张"
          f"(垂直视场 {idx.get('fov_v_deg')}°,splats {idx.get('res')},耗时 {idx.get('render_s')} s)" + (f";失败 {len(errs)}:{errs[:5]}" if errs else ''))
    print(f'compare: {page}')
    return 1 if errs else 0


if __name__ == '__main__':
    sys.exit(main())
