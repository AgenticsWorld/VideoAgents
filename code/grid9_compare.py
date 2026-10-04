#!/usr/bin/env python3
"""九宫格模式「选格 vs 补图」结果比较页(2026-09-26 试验;补图逻辑见 modules/shot_plates.py「九宫格补图」)。

按场景把本集每镜的四样东西并排:本镜白模帧(机位真值)、九宫格自动选出的格子(含打分分量与合适/不合适判定)、
不合适时按本镜机位单独出的补图(俯视图 + 九宫格整图为参考)、以及切九宫格前全景图模式出的母图(库 plates/_archive_pano_*/,
按母图制规则 find_master 反推本镜当时会引用哪张,反推不到时按母图台账 created_by 兜底),最终采用的一张打勾;顶部是九宫格整图、阈值与统计。
输出静态 HTML 到项目 qa/grid9_compare/<scene>.html(图片走相对路径),可直接用浏览器打开,或经控制台文件路由访问:
  http://localhost:8630/api/v1/projects/<slug>/artifacts/qa/grid9_compare/<scene>.html

用法:
  python code/grid9_compare.py --project alices2 --ep ep01 --scene SCN-long-hall
"""
import html
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args  # noqa: E402
from modules import shot_plates as sp  # noqa: E402
from modules.whitebox import component, read, render_format  # noqa: E402

CSS = """
body{font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;margin:0;background:#111;color:#ddd}
header{padding:16px 20px;border-bottom:1px solid #333;background:#181818;position:sticky;top:0;z-index:5}
h1{font-size:18px;margin:0 0 6px}h2{font-size:15px;margin:18px 20px 8px}
.meta{color:#999;font-size:12px}.meta code{color:#bbb}
.stats{display:flex;gap:14px;flex-wrap:wrap;margin-top:8px}.stat{background:#222;border:1px solid #333;border-radius:6px;padding:6px 10px}
.stat b{font-size:18px;display:block}.filters{margin-top:8px}.filters button{background:#2a2a2a;color:#ddd;border:1px solid #444;border-radius:4px;padding:4px 10px;margin-right:6px;cursor:pointer}
.filters button.on{background:#3b6;color:#000;border-color:#3b6}
.sheet{padding:12px 20px}.sheet img{max-width:100%;border:1px solid #333}
.tiles{display:grid;grid-template-columns:repeat(9,1fr);gap:6px;padding:0 20px 12px}.tiles figure{margin:0;font-size:11px;color:#aaa}.tiles img{width:100%;border:1px solid #333}
table{border-collapse:collapse;width:100%;font-size:12px}th,td{border-top:1px solid #2a2a2a;padding:8px 8px;vertical-align:top;text-align:left}
th{background:#1c1c1c;position:sticky;top:0;z-index:1;color:#bbb}tr.fb{background:#161a16}tr.hide{display:none}
td.img{width:23%}td.img img{width:100%;border:1px solid #333;cursor:zoom-in;display:block}
.pick{outline:2px solid #3b6;outline-offset:2px}.cap{color:#999;font-size:11px;margin-top:4px;word-break:break-all}
.ok{color:#3b6}.bad{color:#e75}.tag{display:inline-block;padding:1px 6px;border-radius:3px;font-size:11px;margin-right:4px}
.tag.ok{background:#1f3a2a}.tag.bad{background:#3a2a1f}.tag.new{background:#2a2f3a;color:#9bf}.tag.lib{background:#332a3a;color:#d9f}
.shot{white-space:nowrap}.shot b{font-size:13px}.desc{color:#aaa;max-width:260px}
#zoom{position:fixed;inset:0;background:rgba(0,0,0,.92);display:none;align-items:center;justify-content:center;z-index:9;cursor:zoom-out}#zoom img{max-width:96vw;max-height:96vh}
dl{margin:0;font-size:11px;color:#aaa}dl div{display:flex;gap:6px}dt{color:#777;min-width:52px}
"""

JS = """
function zoom(src){const z=document.getElementById('zoom');z.querySelector('img').src=src;z.style.display='flex'}
document.addEventListener('DOMContentLoaded',()=>{document.getElementById('zoom').onclick=()=>{document.getElementById('zoom').style.display='none'};
document.querySelectorAll('.filters button').forEach(b=>b.onclick=()=>{document.querySelectorAll('.filters button').forEach(x=>x.classList.remove('on'));b.classList.add('on');
const f=b.dataset.f;document.querySelectorAll('tbody tr').forEach(tr=>{tr.classList.toggle('hide',f!=='all'&&tr.dataset.kind!==f)})})});
"""


def esc(x) -> str:
    return html.escape('' if x is None else str(x))


def main():
    def configure(ap):
        ap.add_argument('--scene', required=True, help='场景 id,如 SCN-long-hall')
        ap.add_argument('--out', default=None, help='输出 HTML 路径(缺省 qa/grid9_compare/<scene>.html)')
    args, base = parse_args(__doc__, configure=configure)
    ep, sid = component(args.ep), args.scene
    idx = read(base/'directing'/ep/'shot_plates.json', {}) or {}
    lib = sp.load_library(base, sid)
    by_key = {e['key']: e for e in lib.get('plates', [])}
    shots_meta = {s['shot_id']: s for s in (read(base/'directing'/ep/'shot_list.json', {}) or {}).get('shots', [])}
    out = Path(args.out) if args.out else base/'qa'/'grid9_compare'/f'{sid}.html'
    out.parent.mkdir(parents=True, exist_ok=True)
    rel = Path('..') / '..'   # qa/grid9_compare/ → 项目根

    def url(p):
        return str(rel / p) if p else ''

    # 全景图模式存档(切九宫格模式时库图被归档到 plates/_archive_pano_<日期>/,同名 .json 台账仍在):按母图制规则反推每镜会引用的母图
    fmt = render_format(read(base/'settings.json', {}))
    aspect = fmt['width'] / fmt['height']
    archives = sorted((base/'assets/concepts/scenes'/sid/'plates').glob('_archive_pano_*'))
    pano_entries = []
    for adir in archives:
        for jf in sorted(adir.glob('*.json')):
            try:
                e = json.loads(jf.read_text(encoding='utf-8'))
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(e, dict) or not e.get('key') or not e.get('master'):
                continue
            png = adir / f"{e['key']}.png"
            if not png.is_file():
                continue
            e['archive_file'] = str(png.relative_to(base))
            for k in ('whitebox_frame',):
                f = adir / f"{e['key']}.whitebox.jpg"
                e['archive_whitebox'] = str(f.relative_to(base)) if f.is_file() else None
            pf = adir / f"{e['key']}.pano.jpg"
            e['archive_pano'] = str(pf.relative_to(base)) if pf.is_file() else None
            pano_entries.append(e)
    pano_by_shot = {}
    for e in pano_entries:
        cb = e.get('created_by') or {}
        if cb.get('shot_id'):
            pano_by_shot.setdefault((cb['shot_id'], cb.get('role') or 'start'), e)

    def pano_for(shot_id, role, facts, scheme):
        """母图制规则(同机位范围 + 视锥装得下)反推;反推不到用台账 created_by 兜底。返回 (条目, 来源, view_info)。"""
        if not pano_entries or not facts:
            return None, None, None
        m = sp.find_master({'plates': pano_entries}, scheme, facts, aspect, base, require_file=False)
        if m is not None:
            return m, 'rule', sp.view_info(m, facts)
        m = pano_by_shot.get((shot_id, role))
        if m is not None:
            return m, 'created_by', sp.view_info(m, facts)
        return None, None, None

    rows = []
    for shot_id, s in sorted(idx.get('shots', {}).items()):
        if s.get('scene_id') != sid:
            continue
        for p in s.get('plates', []):
            view = p.get('view') or {}
            g9 = view.get('grid9') or {}
            fb = view.get('fallback')
            tile_key = g9.get('key') or (p['key'] if p.get('reuse') == 'grid9' else '')
            tile = by_key.get(tile_key) or {}
            fallback_entry = by_key.get(fb['key']) if fb else None
            if p.get('reuse') == 'grid9_fallback' and not fallback_entry:
                fallback_entry = by_key.get(p['key'])
            reasons = (fb or {}).get('reasons') or sp.grid9_unfit_reasons(g9)
            pano_e, pano_src, pano_view = pano_for(shot_id, p.get('role'), p.get('camera') or {}, s.get('lighting_scheme_id'))
            rows.append({'pano': pano_e, 'pano_src': pano_src, 'pano_view': pano_view,'shot_id': shot_id, 'group_id': s.get('group_id'), 'role': p.get('role'), 'movement': s.get('movement'),
                         'camera': p.get('camera') or {}, 'whitebox': p.get('whitebox_frame'), 'g9': g9, 'tile': tile, 'tile_key': tile_key,
                         'fit': not reasons, 'reasons': reasons, 'fallback': fallback_entry, 'fb_meta': fb, 'reuse': p.get('reuse'),
                         'final_key': p.get('key'), 'final_file': p.get('file'), 'desc': (shots_meta.get(shot_id) or {}).get('camera_position', '')})
    if not rows:
        print(f'{sid}: 集索引 {ep} 里没有该场景的背景图记录', file=sys.stderr)
        return 1
    sheet = next((e.get('pano_ref', {}).get('sheet') for e in lib.get('plates', []) if e.get('grid9')), None)
    tiles = sorted([e for e in lib.get('plates', []) if e.get('grid9')], key=lambda e: int(e['pano_ref'].get('tile', 0)))
    n_fit = sum(1 for r in rows if r['fit'])
    n_fb_new = sum(1 for r in rows if r['fb_meta'] and r['fb_meta'].get('source') == 'new')
    n_fb_lib = sum(1 for r in rows if r['fb_meta'] and r['fb_meta'].get('source') == 'library')
    n_unfit_no_fb = sum(1 for r in rows if not r['fit'] and not r['fallback'])
    fb_files = {r['fallback']['key'] for r in rows if r['fallback']}
    tile_use = Counter(r['g9'].get('tile') for r in rows if r['fit'])
    n_pano = sum(1 for r in rows if r['pano'])
    pano_files = {r['pano']['key'] for r in rows if r['pano']}
    fit = sp.GRID9_FIT

    h = [f'<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>九宫格选格 vs 补图 · {esc(sid)} · {esc(ep)}</title><style>{CSS}</style></head><body>']
    h.append(f'<header><h1>九宫格选格 vs 补图 · {esc(args.project)} / {esc(ep)} / {esc(sid)}</h1>'
             f'<div class="meta">规则:每镜先按白模机位从九格里选最近的一格;最近格的 朝向差 &gt; {fit["bearing_deg"]:g}° 或 俯仰差 &gt; {fit["pitch_deg"]:g}° 或 '
             f'机位距 &gt; {fit["distance_m"]:g} m 或 机高档差 ≥ {fit["height_class_delta"]} 即视为不合适,改以 <code>俯视图 + 九宫格整图</code> 为参考按本镜机位单独出图'
             f'(相近机位:朝向 ±{sp.GRID9_FB_TOLERANCE["bearing_deg"]:g}° / 距 ≤{sp.GRID9_FB_TOLERANCE["distance_m"]:g} m / 机高 ±{sp.GRID9_FB_TOLERANCE["height_m"]:g} m / '
             f'俯仰 ±{sp.GRID9_FB_TOLERANCE["pitch_deg"]:g}° / 视场 ±{sp.GRID9_FB_TOLERANCE["fov_deg"]:g}° 复用同一张补图)。绿框 = 最终采用。</div>'
             f'<div class="stats"><div class="stat"><b>{len(rows)}</b>背景图需求(镜首/镜尾)</div><div class="stat"><b class="ok">{n_fit}</b>直接用格子</div>'
             f'<div class="stat"><b class="bad">{len(rows) - n_fit}</b>格子不合适</div><div class="stat"><b>{n_fb_new}</b>补图·新出</div>'
             f'<div class="stat"><b>{n_fb_lib}</b>补图·复用相近机位</div><div class="stat"><b>{len(fb_files)}</b>补图实际张数</div>'
             + (f'<div class="stat"><b class="bad">{n_unfit_no_fb}</b>不合适但尚无补图</div>' if n_unfit_no_fb else '')
             + (f'<div class="stat"><b>{n_pano}</b>有全景图模式旧图可对照(母图 {len(pano_files)} 张,存档 {", ".join(a.name for a in archives)})</div>' if pano_entries else '')
             + '</div>'
             f'<div class="filters"><button class="on" data-f="all">全部</button><button data-f="fb">仅补图的镜</button><button data-f="tile">仅用格子的镜</button></div></header>')
    if sheet:
        h.append(f'<h2>九宫格整图(补图的 [Image 2])</h2><div class="sheet"><img src="{esc(url(sheet))}" onclick="zoom(this.src)"></div>')
    if tiles:
        h.append('<div class="tiles">' + ''.join(
            f'<figure><img src="{esc(url(e["file"]))}" onclick="zoom(this.src)"><figcaption>第 {int(e["pano_ref"].get("tile", 0)) + 1} 格 · '
            + (f'朝 {esc(e["pano_ref"].get("view", {}).get("card"))}' if e["pano_ref"].get("layout") == "center" else   # 中心点九宫格(2026-10-04):同一站位,只列方向
               f'{esc(e["pano_ref"].get("view", {}).get("camera_from"))}→{esc(e["pano_ref"].get("view", {}).get("looking_at"))}')
            + f' · {esc(e["camera"].get("facing"))} '
            f'h={esc(e["camera"].get("height_m"))}m · 直接用于 {tile_use.get(int(e["pano_ref"].get("tile", 0)) + 1, 0)} 镜</figcaption></figure>' for e in tiles) + '</div>')
    h.append('<h2>逐镜对照</h2><table><thead><tr><th>镜</th><th>本镜白模帧(机位真值)</th><th>九宫格选格</th><th>补图(俯视图 + 九宫格整图为参考)</th>'
             + ('<th>全景图模式旧图(切换前的母图,存档)</th>' if pano_entries else '') + '</tr></thead><tbody>')
    for r in rows:
        c, g9 = r['camera'], r['g9']
        kind = 'fb' if r['fallback'] or not r['fit'] else 'tile'
        tile_pick = 'pick' if r['fit'] else ''
        fb_pick = 'pick' if r['fallback'] and r['reuse'] == 'grid9_fallback' else ''
        cam_dl = (f'<dl><div><dt>朝向</dt><dd>{esc(c.get("facing"))} ({esc(c.get("bearing_deg"))}°)</dd></div>'
                  f'<div><dt>机高</dt><dd>{esc(c.get("height_m"))} m · {esc(c.get("height_word"))}</dd></div>'
                  f'<div><dt>俯仰</dt><dd>{esc(c.get("pitch_deg"))}°</dd></div><div><dt>镜头</dt><dd>{esc(c.get("lens_mm_equiv"))}mm · 水平 {esc(c.get("fov_h_deg"))}°</dd></div>'
                  f'<div><dt>主体距</dt><dd>{esc(c.get("subject_distance_m"))} m · {esc(c.get("standing"))}</dd></div></dl>')
        h.append(f'<tr class="{kind}" data-kind="{kind}"><td class="shot"><b>{esc(r["shot_id"])}</b> {esc(r["role"])}<br>{esc(r["group_id"])} · {esc(r["movement"])}'
                 f'<div class="desc" style="white-space:normal">{esc(r["desc"])}</div></td>')
        h.append(f'<td class="img">' + (f'<img src="{esc(url(r["whitebox"]))}" loading="lazy" onclick="zoom(this.src)">' if r['whitebox'] else '<i>无白模帧</i>') + cam_dl + '</td>')
        verdict = ('<span class="tag ok">合适 · 采用</span>' if r['fit'] else '<span class="tag bad">不合适</span>' + ''.join(f'<span class="tag bad">{esc(x)}</span>' for x in r['reasons']))
        g9_dl = (f'<dl><div><dt>格</dt><dd>第 {esc(g9.get("tile"))} 格 · score {esc(g9.get("score"))}</dd></div><div><dt>朝向差</dt><dd>{esc(g9.get("bearing_delta_deg"))}°</dd></div>'
                 f'<div><dt>机位距</dt><dd>{esc(g9.get("distance_m"))} m</dd></div><div><dt>机高档差</dt><dd>{esc(g9.get("height_class_delta"))}</dd></div>'
                 f'<div><dt>俯仰差</dt><dd>{esc(g9.get("pitch_delta_deg"))}°</dd></div>'
                 + (f'<div><dt>格机位</dt><dd>{esc(r["tile"].get("camera", {}).get("facing"))} h={esc(r["tile"].get("camera", {}).get("height_m"))}m '
                    f'{esc(r["tile"].get("camera", {}).get("lens_mm_equiv"))}mm</dd></div>' if r['tile'] else '') + '</dl>') if g9 else ''
        h.append(f'<td class="img">' + (f'<img class="{tile_pick}" src="{esc(url(r["tile"]["file"]))}" loading="lazy" onclick="zoom(this.src)">' if r['tile'] else '<i>无</i>')
                 + f'<div class="cap">{verdict}</div>{g9_dl}</td>')
        if r['fallback']:
            fbe = r['fallback']; src = (r['fb_meta'] or {}).get('source')
            tag = '<span class="tag new">新出</span>' if src == 'new' else '<span class="tag lib">复用相近机位</span>' if src == 'library' else ''
            fb_cam = fbe.get('camera') or {}
            fb_dl = (f'<dl><div><dt>key</dt><dd>{esc(fbe.get("key"))}</dd></div><div><dt>补图机位</dt><dd>{esc(fb_cam.get("facing"))} h={esc(fb_cam.get("height_m"))}m '
                     f'{esc(fb_cam.get("lens_mm_equiv"))}mm 俯仰 {esc(fb_cam.get("pitch_deg"))}°</dd></div>'
                     f'<div><dt>参考图</dt><dd>{esc(", ".join(Path(x).name for x in fbe.get("refs", [])))}</dd></div>'
                     f'<div><dt>模型</dt><dd>{esc((fbe.get("channel") or {}).get("provider"))} {esc((fbe.get("channel") or {}).get("model"))} · seed {esc(fbe.get("seed"))}</dd></div></dl>')
            h.append(f'<td class="img"><img class="{fb_pick}" src="{esc(url(fbe["file"]))}" loading="lazy" onclick="zoom(this.src)"><div class="cap">{tag}'
                     + ('<span class="tag ok">采用</span>' if fb_pick else '') + f'</div>{fb_dl}</td>')
        elif not r['fit']:
            h.append('<td class="img"><i>不合适但尚未补图(跑 render_shot_plates.py --grid-fallback)</i></td>')
        else:
            h.append('<td class="img"><span class="meta">— 格子合适,不补图</span></td>')
        if pano_entries:
            pe, pv = r['pano'], r['pano_view'] or {}
            if pe:
                pc = pe.get('camera') or {}; pr = pe.get('pano_ref') or {}
                src_tag = '<span class="tag lib">按母图制规则反推</span>' if r['pano_src'] == 'rule' else '<span class="tag new">台账 created_by 兜底</span>'
                pano_dl = (f'<dl><div><dt>母图</dt><dd>{esc(pe.get("key"))}</dd></div><div><dt>母图机位</dt><dd>{esc(pc.get("facing"))} h={esc(pc.get("height_m"))}m '
                           f'{esc(pc.get("lens_mm_equiv"))}mm 俯仰 {esc(pc.get("pitch_deg"))}°</dd></div>'
                           f'<div><dt>本镜偏差</dt><dd>朝向 {esc(pv.get("bearing_delta_deg"))}° · 俯仰 {esc(pv.get("pitch_delta_deg"))}° · 距 {esc(pv.get("distance_m"))} m · 本镜视场占母图 {esc(pv.get("fraction"))}</dd></div>'
                           f'<div><dt>全景锚点</dt><dd>{esc(pr.get("anchor_id"))} · 空洞 {esc(pr.get("hole_fraction"))} · 距锚点 {esc(pr.get("distance_from_anchor_m"))} m</dd></div>'
                           + (f'<div><dt>重投影</dt><dd><a href="{esc(url(pe["archive_pano"]))}" target="_blank">[Image 1] pano.jpg</a></dd></div>' if pe.get('archive_pano') else '')
                           + (f'<div><dt>手工裁切</dt><dd>{len(pe["manual_crops"])} 次</dd></div>' if pe.get('manual_crops') else '') + '</dl>')
                h.append(f'<td class="img"><img src="{esc(url(pe["archive_file"]))}" loading="lazy" onclick="zoom(this.src)"><div class="cap">{src_tag}</div>{pano_dl}</td>')
            else:
                h.append('<td class="img"><i>全景图模式当时没有能派生本镜的母图</i></td>')
        h.append('</tr>')
    h.append('</tbody></table><div id="zoom"><img></div>')
    h.append(f'<script>{JS}</script></body></html>')
    out.write_text('\n'.join(h), encoding='utf-8')
    print(json.dumps({'grid9_compare': {'html': str(out.relative_to(base)) if out.is_relative_to(base) else str(out), 'plates': len(rows), 'fit': n_fit,
                      'fallback_new': n_fb_new, 'fallback_library': n_fb_lib, 'fallback_images': len(fb_files), 'unfit_without_fallback': n_unfit_no_fb, 'pano_archive_matched': n_pano,
                      'url': f'/api/v1/projects/{args.project}/artifacts/{out.relative_to(base)}' if out.is_relative_to(base) else None}}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
