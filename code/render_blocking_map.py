#!/usr/bin/env python3
"""render_blocking_map.py — 分镜组人物动线图:把组级 blocking_map(逐角色 起点/动线/终点)
标注渲染到该场景的俯视空间布局图上,并做 blocking_map 机检(blocking_map_present /
blocking_map_landmarks_valid / scene_layout_pack_ok)。

背景(WORKFLOW.md §4 Phase 4 / Phase 6,2026-08-19):
  - environment-concept 每场景出「俯视空间布局图」`assets/concepts/scenes/<sid>/layout_top.png`
    + 「9 宫格多角度场景图」`grid_9views.png` + 文字事实源 `layout.json`(地标归一化坐标 xy、
    九格机位语义 views[1..9]);
  - storyboard 每个生成组草案写 `blocking_map`(组内每个出场角色的 start / path / end,均引用
    layout.json 的地标 id,可选 xy 微调;附动线句 `route_en`——内容语言随界面语言,
    2026-08-24 三订:图例支持 CJK 渲染,角色名 label 与 route_en 动线句会上图),
    shot-planning 定稿组时继承进
    shot_list.generation_groups[].blocking_map;
  - 本脚本把标注画到 layout_top.png 上,产出 `directing/epNN/blocking_maps/<group_id>.png`
    (storyboard 草案期 `--source storyboard` 写 `blocking_maps/draft/<scene_no>_g<NN>.png`),
    prompt agent 把定稿图列入组 refs,视频模型按图中人物位置标注与动线安排画面(机检
    layout_map_bound,脚本 code/layout_map_bound_check.py)。

图例:每角色一色;● 实心圆 = 起点(圆内字母 A/B/C/D 为角色序号 = blocking_map.characters 数组顺序,
      图例条附加在原图**下方**的纯黑横条内(2026-08-24 三订调整:不覆盖原图,避免遮挡布局信息),
      写「字母 = 角色名 label (CHAR 编号) [moves/stays]」并附该角色动线句 route_en 折行
      ——_font 优先加载系统 CJK 字体(PingFang/Hiragino/Noto Sans CJK/微软雅黑),
      中文名与中文动线句可直接上图;系统无 CJK 字体时回退拉丁字体并 WARN(中文会缺字)。
      prompt 的 Map markers 句照旧写名字↔编号↔字母对应,图文双保险,机检 layout_map_bound 不变);
      ■ 实心方块 = 终点;箭头折线 = 动线(经过 path 各点);无移动的角色只画起点圆。
      渲染尺寸 = 布局图宽 x (布局图高 + 底部图例条高)(布局图 ≥2560x1440,产物天然满足
      视频参考像素下限)。

用法:
  python3 code/render_blocking_map.py --project <slug> --ep ep01                 # 定稿:shot_list 全组
  python3 code/render_blocking_map.py --project <slug> --ep ep01 grp005 grp006   # 只渲染指定组
  python3 code/render_blocking_map.py --project <slug> --ep ep01 --source storyboard   # 草案期
  python3 code/render_blocking_map.py --project <slug> --ep ep01 --check-only    # 只机检不落图
  python3 code/render_blocking_map.py --project <slug> --scene SCN-0012 --check-only  # 只查场景布局包
退出码:0=通过(可含 WARN),1=有违规。
"""
import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args, spatial_blocking_enabled  # noqa: E402

MIN_PIXELS = 3_686_400          # 火山视频参考图像素硬限(WORKFLOW.md §9)
LAYOUT_SCHEMA = "scene_layout.v1"
PALETTE = [(230, 57, 70), (29, 120, 216), (46, 160, 67), (245, 158, 11),
           (142, 68, 173), (0, 172, 193), (233, 30, 99), (121, 85, 72)]
LETTERS = "ABCDEFGH"


# ---------------------------------------------------------------- layout pack
def load_layout(proj_root: Path, sid: str):
    """返回 (layout_dict|None, errs, warns);同时做 scene_layout_pack_ok 机检。"""
    d = proj_root / "assets" / "concepts" / "scenes" / sid
    errs, warns = [], []
    lj = d / "layout.json"
    if not lj.is_file():
        return None, [f"{sid}: 缺 layout.json(场景布局包未产出,回派 environment-concept)"], warns
    try:
        lay = json.loads(lj.read_text())
    except Exception as e:  # noqa: BLE001
        return None, [f"{sid}: layout.json 不是合法 JSON({e})"], warns
    if lay.get("schema_version") != LAYOUT_SCHEMA:
        warns.append(f"{sid}: layout.json schema_version={lay.get('schema_version')!r},期望 {LAYOUT_SCHEMA}")
    for key, default in (("layout_top", "layout_top.png"), ("grid_9views", "grid_9views.png")):
        f = d / (lay.get(key) or default)
        if not f.is_file():
            errs.append(f"{sid}: 缺 {key} 图 {f.name}")
            continue
        try:
            from PIL import Image
            with Image.open(f) as im:
                w, h = im.size
            if w * h < MIN_PIXELS:
                errs.append(f"{sid}: {f.name} 仅 {w}x{h}={w*h} 像素 < {MIN_PIXELS}(视频参考硬限)")
        except Exception as e:  # noqa: BLE001
            warns.append(f"{sid}: 无法读取 {f.name}({e})")
    lms = lay.get("landmarks") or []
    ids = set()
    for lm in lms:
        lid = lm.get("id")
        xy = lm.get("xy")
        if not lid:
            errs.append(f"{sid}: landmarks 存在缺 id 的条目")
            continue
        if lid in ids:
            errs.append(f"{sid}: landmark id 重复 {lid}")
        ids.add(lid)
        if not (isinstance(xy, (list, tuple)) and len(xy) == 2
                and all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in xy)):
            errs.append(f"{sid}: landmark {lid} 的 xy 须为 [0..1, 0..1] 归一化坐标,得到 {xy!r}")
        if not lm.get("name_en"):
            errs.append(f"{sid}: landmark {lid} 缺 name_en(prompt 地标词唯一词源;语言随界面语言,2026-08-24 二订)")
    if len(ids) < 3:
        errs.append(f"{sid}: landmarks 少于 3 个({len(ids)}),无法定位人物站位")
    tiles = sorted(v.get("tile") for v in (lay.get("views") or []) if isinstance(v, dict))
    if tiles != list(range(1, 10)):
        errs.append(f"{sid}: views 须恰为 tile 1..9 各一条,得到 {tiles}")
    else:
        for v in lay["views"]:
            if not v.get("desc_en"):
                errs.append(f"{sid}: views tile {v.get('tile')} 缺 desc_en")
    return lay, errs, warns


# ---------------------------------------------------------------- groups
def iter_groups(proj_root: Path, ep: str, source: str):
    """统一产出 (group_key, scene_id, characters_union|None, blocking_map|None, out_rel)。"""
    if source == "shot_list":
        sl = json.loads((proj_root / "directing" / ep / "shot_list.json").read_text())
        for g in sl.get("generation_groups") or []:
            gid = g.get("group_id")
            if not gid:
                continue
            yield gid, g.get("scene_id"), g.get("characters_union"), g.get("blocking_map"), \
                f"directing/{ep}/blocking_maps/{gid}.png"
    else:
        sb = json.loads((proj_root / "directing" / ep / "storyboard.json").read_text())
        for sc in sb.get("scenes") or []:
            sno = sc.get("scene_no") or sc.get("screenplay_ref") or "S"
            for g in sc.get("groups_draft") or []:
                go = g.get("group_order")
                key = f"{sno}_g{int(go):02d}" if go is not None else f"{sno}_g?"
                yield key, g.get("scene_id") or sc.get("scene_id"), None, g.get("blocking_map"), \
                    f"directing/{ep}/blocking_maps/draft/{key}.png"


def resolve_pt(pt, landmarks: dict, gid: str, cid: str, what: str, errs: list):
    """start/path[i]/end → 像素前的归一化 (x, y);地标 id 优先,xy 覆盖。"""
    if pt is None:
        return None
    if isinstance(pt, str):
        pt = {"landmark": pt}
    xy = pt.get("xy")
    lid = pt.get("landmark")
    if lid and lid not in landmarks:
        errs.append(f"{gid}/{cid}: {what} 引用的地标 {lid!r} 不在 layout.json#landmarks")
        return None
    if xy is None and lid:
        xy = landmarks[lid].get("xy")
    if not (isinstance(xy, (list, tuple)) and len(xy) == 2):
        errs.append(f"{gid}/{cid}: {what} 既无合法地标也无 xy")
        return None
    return float(xy[0]), float(xy[1])


def validate_map(gid: str, bm, chars_union, landmarks: dict):
    """blocking_map 结构机检;返回 (routes, errs, warns)。routes=[(cid, label, start, path, end)]。"""
    errs, warns, routes = [], [], []
    if not isinstance(bm, dict) or not isinstance(bm.get("characters"), list):
        return routes, [f"{gid}: blocking_map 缺 characters 数组"], warns
    seen = set()
    for i, ch in enumerate(bm["characters"]):
        cid = ch.get("id") or f"#{i}"
        if cid in seen:
            errs.append(f"{gid}: blocking_map 角色重复 {cid}")
        seen.add(cid)
        start = resolve_pt(ch.get("start"), landmarks, gid, cid, "start", errs)
        if start is None and "start" not in ch:
            errs.append(f"{gid}/{cid}: 缺 start(起点必填)")
        path = [p for p in (resolve_pt(p, landmarks, gid, cid, f"path[{k}]", errs)
                            for k, p in enumerate(ch.get("path") or [])) if p]
        end = resolve_pt(ch.get("end"), landmarks, gid, cid, "end", errs)
        if path and end is None:
            errs.append(f"{gid}/{cid}: 有 path 但无 end(有动线必须写终点)")
        route_en = ch.get("route_en")
        if not route_en or not isinstance(route_en, str):
            errs.append(f"{gid}/{cid}: 缺 route_en(动线句,prompt 逐字注入;语言随界面语言)")
        # 纯英文校验已取消(2026-08-24 二订):内容语言随界面语言(三订起图例支持 CJK,route_en 上图)
        elif (len(route_en.split()) > 40) if route_en.isascii() else (len(route_en) > 60):
            warns.append(f"{gid}/{cid}: route_en 过长(限 ≤40 英文词或 ≤60 字),建议精简")
        routes.append((cid, ch.get("label") or cid, start, path, end,
                       route_en if isinstance(route_en, str) else ""))
    if chars_union is not None:
        a, b = set(seen), set(chars_union)
        if a != b:
            errs.append(f"{gid}: blocking_map 角色集合 {sorted(a)} ≠ 组 characters_union {sorted(b)}")
    if len(routes) > len(LETTERS):
        errs.append(f"{gid}: 角色数 {len(routes)} 超出图例上限 {len(LETTERS)}")
    return routes, errs, warns


# ---------------------------------------------------------------- render
# CJK 优先(2026-08-24 三订:图例写角色名与 route_en 动线句,中文/GBK 范围汉字按 unicode 渲染);
# 系统无 CJK 字体时回退拉丁字体,render() 对含中文的图例打 WARN(中文会缺字)
_FONT_CANDIDATES = (
    ("/System/Library/Fonts/PingFang.ttc", True),                        # macOS(部分版本 PIL 打不开,顺延)
    ("/System/Library/Fonts/STHeiti Medium.ttc", True),                  # macOS
    ("/System/Library/Fonts/Hiragino Sans GB.ttc", True),                # macOS
    ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", True),      # macOS
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", True),       # Linux
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", True),
    ("/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc", True),
    ("C:/Windows/Fonts/msyhbd.ttc", True),                               # Windows 微软雅黑
    ("C:/Windows/Fonts/msyh.ttc", True),
    ("C:/Windows/Fonts/simhei.ttf", True),
    ("/System/Library/Fonts/Helvetica.ttc", False),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", False),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", False),
    ("C:/Windows/Fonts/arialbd.ttf", False),
)


def _font(size: int):
    from PIL import ImageFont
    for cand, cjk in _FONT_CANDIDATES:
        try:
            f = ImageFont.truetype(cand, size)
            f.cjk_capable = cjk
            return f
        except Exception:  # noqa: BLE001
            continue
    f = ImageFont.load_default()
    try:
        f.cjk_capable = False
    except Exception:  # noqa: BLE001
        pass
    return f


def _wrap_px(dl, text, font, max_w):
    """图例动线句按像素宽度折行:ASCII 词(连尾随空格)整词换行,CJK 逐字换行。"""
    toks = re.findall(r"[\x21-\x7e]+\s*|.", text or "")
    lines, cur = [], ""
    for tk in toks:
        if cur and dl.textlength(cur + tk.rstrip(), font=font) > max_w:
            lines.append(cur.rstrip())
            cur = tk.lstrip()
        else:
            cur += tk
    if cur.strip():
        lines.append(cur.rstrip())
    return lines


def _arrow_head(draw, p0, p1, size, color):
    ang = math.atan2(p1[1] - p0[1], p1[0] - p0[0])
    a1, a2 = ang + math.radians(150), ang - math.radians(150)
    pts = [p1, (p1[0] + size * math.cos(a1), p1[1] + size * math.sin(a1)),
           (p1[0] + size * math.cos(a2), p1[1] + size * math.sin(a2))]
    draw.polygon(pts, fill=color, outline=(255, 255, 255))


def render(layout_png: Path, routes, out_png: Path, title: str):
    from PIL import Image, ImageDraw
    im = Image.open(layout_png).convert("RGBA")
    W, H = im.size
    ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    R = max(18, int(W * 0.018))          # 标记半径
    LW = max(6, int(W * 0.005))          # 线宽
    fnt = _font(int(R * 1.3))
    lg_font = _font(int(R * 0.9))
    to_px = lambda p: (p[0] * W, p[1] * H)  # noqa: E731

    for idx, (cid, label, start, path, end, _route) in enumerate(routes):
        col = PALETTE[idx % len(PALETTE)]
        letter = LETTERS[idx]
        pts = [to_px(p) for p in ([start] if start else []) + path + ([end] if end else [])]
        # 动线:白描边 + 彩色主线,末端箭头
        if len(pts) >= 2:
            # 末段收短到终点方块边缘,箭头露在方块外
            (x0, y0), (x1, y1) = pts[-2], pts[-1]
            seg = math.hypot(x1 - x0, y1 - y0) or 1.0
            cut = min(R * 1.25, seg * 0.6)
            tip = (x1 - (x1 - x0) / seg * cut, y1 - (y1 - y0) / seg * cut)
            line = pts[:-1] + [tip]
            d.line(line, fill=(255, 255, 255, 230), width=LW + 6, joint="curve")
            d.line(line, fill=col + (255,), width=LW, joint="curve")
            _arrow_head(d, pts[-2], tip, R * 1.4, col + (255,))
        # 起点圆
        if start:
            x, y = to_px(start)
            d.ellipse([x - R, y - R, x + R, y + R], fill=col + (255,), outline=(255, 255, 255, 255), width=4)
            bb = d.textbbox((0, 0), letter, font=fnt)
            d.text((x - (bb[2] - bb[0]) / 2 - bb[0], y - (bb[3] - bb[1]) / 2 - bb[1]), letter,
                   fill=(255, 255, 255, 255), font=fnt)
        # 终点方块
        if end:
            x, y = to_px(end)
            d.rectangle([x - R, y - R, x + R, y + R], fill=col + (255,), outline=(255, 255, 255, 255), width=4)
            bb = d.textbbox((0, 0), letter, font=fnt)
            d.text((x - (bb[2] - bb[0]) / 2 - bb[0], y - (bb[3] - bb[1]) / 2 - bb[1]), letter,
                   fill=(255, 255, 255, 255), font=fnt)
    # 图例条(2026-08-24 三订调整:附加在原图**下方**的纯黑横条,画布加高——不再左上角半透明
    # 覆盖,避免遮挡布局图信息;图例写「字母 = 角色名 (CHAR 编号) [moves/stays]」并附动线句
    # route_en 折行,_font 带 CJK 候选,中文名/中文动线句可直接上图;prompt 的 Map markers 句
    # 照旧必写,图文双保险,机检 layout_map_bound 不变)
    rt_font = _font(int(R * 0.72))
    pad = int(R * 0.6)
    th = d.textbbox((0, 0), "Ag", font=lg_font)[3]
    rh = d.textbbox((0, 0), "Ag", font=rt_font)[3]
    sw = int(th * 0.6)                         # 角色色块边长
    indent = sw + pad                          # char/route 行文字相对左边距的缩进(色块之后)
    max_text_w = W - pad * 4 - indent          # 图例文字最大像素宽(横条整宽可用),超出折行
    rows = [("title", f"{title}  |  circle = start, square = end, arrow = path", 0)]
    for idx, (cid, label, start, path, end, route) in enumerate(routes):
        mv = "moves" if end else "stays"
        name = f"{label} ({cid})" if label and label != cid else cid
        rows.append(("char", f"{LETTERS[idx]} = {name}  [{mv}]", idx))
        for seg in _wrap_px(d, route, rt_font, max_text_w):
            rows.append(("route", seg, idx))
    if any(not t.isascii() for _k, t, _i in rows) and not getattr(lg_font, "cjk_capable", False):
        print(f"WARN {title}: 图例含中文但系统无可用 CJK 字体"
              "(PingFang/Hiragino/Noto Sans CJK/微软雅黑均未找到),中文将缺字")
    line_h, route_h = int(th * 1.35), int(rh * 1.3)
    band_h = pad * 3 + sum(route_h if k == "route" else line_h for k, _t, _i in rows)
    out = Image.new("RGB", (W, H + band_h), (0, 0, 0))
    out.paste(Image.alpha_composite(im, ov).convert("RGB"), (0, 0))
    db = ImageDraw.Draw(out)
    y = H + pad * 2
    for kind, t, idx in rows:
        if kind == "title":
            db.text((pad * 2, y), t, fill=(255, 255, 255), font=lg_font)
            y += line_h
        elif kind == "char":
            col = PALETTE[idx % len(PALETTE)]
            y0 = y + int(th * 0.2)
            db.rectangle([pad * 2, y0, pad * 2 + sw, y0 + sw], fill=col)
            db.text((pad * 2 + indent, y), t, fill=(255, 255, 255), font=lg_font)
            y += line_h
        else:
            db.text((pad * 2 + indent, y), t, fill=(210, 210, 210), font=rt_font)
            y += route_h
    out_png.parent.mkdir(parents=True, exist_ok=True)
    out.save(out_png, optimize=True)
    return out.size


# ---------------------------------------------------------------- main
def main():
    args, proj_root = parse_args(
        "分镜组人物动线图渲染 + blocking_map / scene_layout_pack 机检",
        configure=lambda ap: (
            ap.add_argument("groups", nargs="*", help="只处理指定组(shot_list 用 grpNNN;storyboard 用 S03_g01),缺省全部"),
            ap.add_argument("--source", choices=("shot_list", "storyboard"), default="shot_list",
                            help="读 shot_list.generation_groups(定稿,默认)或 storyboard.groups_draft(草案)"),
            ap.add_argument("--scene", action="append", default=[], help="只机检指定场景布局包(可多次),不处理分组"),
            ap.add_argument("--check-only", action="store_true", help="只机检不渲染"),
            ap.add_argument("--strict", action="store_true", help="组缺 blocking_map 也按违规(新产出批次用)"),
        ),
    )
    if not spatial_blocking_enabled(proj_root):
        print(f"[blocking_map] {args.project}: skipped: spatial_blocking off(项目输出设置「人物精确空间位置」已关闭,走单张场景概念图流程)-> PASS")
        sys.exit(0)
    all_errs, all_warns = [], []
    if args.scene:
        for sid in args.scene:
            _, e, w = load_layout(proj_root, sid)
            all_errs += e
            all_warns += w
    else:
        src = proj_root / "directing" / args.ep / ("shot_list.json" if args.source == "shot_list" else "storyboard.json")
        if not src.is_file():
            print(f"VIOLATION 缺 {src.relative_to(proj_root)}")
            sys.exit(1)
        only = set(args.groups)
        layouts = {}
        n = 0
        prev_scene, prev_end = None, {}     # 跨组动线连续:同场景相邻组 start 须接前组 end
        for gid, sid, chars, bm, out_rel in iter_groups(proj_root, args.ep, args.source):
            if only and gid not in only:
                prev_scene, prev_end = sid, {}   # 跳过的组不作连续性基准
                continue
            n += 1
            if not sid:
                all_errs.append(f"{gid}: 缺 scene_id")
                continue
            if sid not in layouts:
                layouts[sid] = load_layout(proj_root, sid)
                all_errs += layouts[sid][1]
                all_warns += layouts[sid][2]
            lay = layouts[sid][0]
            if bm is None:
                msg = f"{gid}: 缺 blocking_map(回派 storyboard/shot-planning 补标注)"
                (all_errs if args.strict else all_warns).append(msg)
                prev_scene, prev_end = sid, {}
                continue
            if lay is None:
                all_errs.append(f"{gid}: 场景 {sid} 无可用布局包,无法渲染动线图")
                continue
            landmarks = {lm["id"]: lm for lm in lay.get("landmarks") or [] if lm.get("id")}
            routes, e, w = validate_map(gid, bm, chars, landmarks)
            all_errs += e
            all_warns += w
            # 同场景相邻组:角色 start 须等于前组该角色 end(无移动=start),偏差 >5% 画幅报违规;
            # start 带 continuity_note(如角色中途出画后从别处入画)时降为 WARN
            if sid == prev_scene:
                notes = {c.get("id"): (c.get("start") or {}) for c in bm.get("characters", []) if isinstance(c, dict)}
                for cid, _lbl, start, _path, _end, _rt in routes:
                    if start and cid in prev_end and prev_end[cid]:
                        px, py = prev_end[cid]
                        if math.hypot(start[0] - px, start[1] - py) > 0.05:
                            note = notes.get(cid, {}).get("continuity_note") if isinstance(notes.get(cid), dict) else None
                            msg = (f"{gid}/{cid}: start {start} 与前组终点 {prev_end[cid]} 不接"
                                   + (f"(continuity_note: {note})" if note else "(跨组位置跳变;确属离场再入场请在 start 写 continuity_note)"))
                            (all_warns if note else all_errs).append(msg)
            prev_scene = sid
            prev_end = {cid: (end or start) for cid, _l, start, _p, end, _r in routes}
            if e or args.check_only:
                continue
            layout_png = proj_root / "assets" / "concepts" / "scenes" / sid / (lay.get("layout_top") or "layout_top.png")
            try:
                size = render(layout_png, routes, proj_root / out_rel, gid)
                print(f"RENDERED {out_rel} {size[0]}x{size[1]} ({len(routes)} 角色)")
            except Exception as ex:  # noqa: BLE001
                all_errs.append(f"{gid}: 渲染失败 {ex}")
        if n == 0:
            all_warns.append(f"{args.source} 无匹配的生成组")
    for w in all_warns:
        print("WARN", w)
    for e in all_errs:
        print("VIOLATION", e)
    tag = "scene_layout_pack" if args.scene else "blocking_map"
    print(f"[{tag}] {args.project}/{args.ep if not args.scene else ','.join(args.scene)}: "
          f"违规 {len(all_errs)} 条, WARN {len(all_warns)} 条 -> {'FAIL' if all_errs else 'PASS'}")
    sys.exit(1 if all_errs else 0)


if __name__ == "__main__":
    main()
