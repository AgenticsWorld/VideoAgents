#!/usr/bin/env python3
"""render_blocking_map.py — 分镜组人物动线图:把组级 blocking_map(逐角色 起点/动线/终点)
标注渲染到该场景的俯视空间布局图上,并做 blocking_map 机检(blocking_map_present /
blocking_map_landmarks_valid / scene_layout_pack_ok)。

背景(WORKFLOW.md §4 Phase 4 / Phase 6,2026-08-19):
  - environment-concept 每场景出「俯视空间布局图」`assets/concepts/scenes/<sid>/layout_top.png`
    + 「9 宫格多角度场景图」`grid_9views.png` + 文字事实源 `layout.json`(地标归一化坐标 xy、
    九格机位语义 views[1..9]);
  - storyboard 每个生成组草案写 `blocking_map`(组内每个出场角色的 start / path / end,均引用
    layout.json 的地标 id,可选 xy 微调;附英文动线句 `route_en`),shot-planning 定稿组时继承进
    shot_list.generation_groups[].blocking_map;
  - 本脚本把标注画到 layout_top.png 上,产出 `directing/epNN/blocking_maps/<group_id>.png`
    (storyboard 草案期 `--source storyboard` 写 `blocking_maps/draft/<scene_no>_g<NN>.png`),
    prompt agent 把定稿图列入组 refs,视频模型按图中人物位置标注与动线安排画面(机检
    layout_map_bound,脚本 code/layout_map_bound_check.py)。

图例:每角色一色;● 实心圆 = 起点(圆内字母 A/B/C/D 为角色序号 = blocking_map.characters 数组顺序,
      左上图例只写「字母 = 角色编号 CHAR-xxxx」,不写中文名——渲染字体无 CJK 字形;名字↔编号↔字母
      的对应由 prompt 的 Map markers 句承担,视频模型结合文字与图上字母理解人物位置);
      ■ 实心方块 = 终点;箭头折线 = 动线(经过 path 各点);无移动的角色只画起点圆。
      渲染尺寸 = 布局图原尺寸(布局图 ≥2560x1440,产物天然满足视频参考像素下限)。

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
            errs.append(f"{sid}: landmark {lid} 缺 name_en(prompt 地标英文词)")
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
            errs.append(f"{gid}/{cid}: 缺 route_en(英文动线句,prompt 逐字注入)")
        elif not route_en.isascii():
            errs.append(f"{gid}/{cid}: route_en 须为纯英文 —— {route_en!r}")
        elif len(route_en.split()) > 40:
            warns.append(f"{gid}/{cid}: route_en 超 40 词({len(route_en.split())}),建议精简")
        routes.append((cid, ch.get("label") or cid, start, path, end))
    if chars_union is not None:
        a, b = set(seen), set(chars_union)
        if a != b:
            errs.append(f"{gid}: blocking_map 角色集合 {sorted(a)} ≠ 组 characters_union {sorted(b)}")
    if len(routes) > len(LETTERS):
        errs.append(f"{gid}: 角色数 {len(routes)} 超出图例上限 {len(LETTERS)}")
    return routes, errs, warns


# ---------------------------------------------------------------- render
def _font(size: int):
    from PIL import ImageFont
    for cand in ("/System/Library/Fonts/Helvetica.ttc",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                 "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
                 "C:/Windows/Fonts/arialbd.ttf"):
        try:
            return ImageFont.truetype(cand, size)
        except Exception:  # noqa: BLE001
            continue
    return ImageFont.load_default()


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

    for idx, (cid, label, start, path, end) in enumerate(routes):
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
    # 图例(左上,半透明底;画在独立图层再 alpha 合成——若与标记同层,底色像素会整体
    # 覆盖其下的标记;分层后落在图例框内的标记以半透明透出,位置信息不丢,2026-08-20)
    # 图例只写「字母 = 角色编号」(2026-08-19 二订):渲染字体无 CJK 字形,中文名会成方块;
    # 名字↔编号↔字母的对应关系改由 prompt 文字承担(Map markers 句,机检 layout_map_bound)
    lines = [f"{title}  |  circle = start, square = end, arrow = path"]
    for idx, (cid, _label, start, path, end) in enumerate(routes):
        mv = "moves" if end else "stays"
        cid_ascii = cid if cid.isascii() else cid.encode("ascii", "ignore").decode() or f"char{idx+1}"
        lines.append(f"{LETTERS[idx]} = {cid_ascii}  [{mv}]")
    lg = Image.new("RGBA", im.size, (0, 0, 0, 0))
    dl = ImageDraw.Draw(lg)
    pad = int(R * 0.6)
    tw = max(dl.textbbox((0, 0), t, font=lg_font)[2] for t in lines)
    th = dl.textbbox((0, 0), "Ag", font=lg_font)[3]
    box_h = pad * 2 + len(lines) * int(th * 1.35)
    dl.rectangle([pad, pad, pad * 3 + tw + R * 1.4, pad + box_h], fill=(0, 0, 0, 135))
    y = pad * 2
    for idx, t in enumerate(lines):
        if idx == 0:
            dl.text((pad * 2, y), t, fill=(255, 255, 255, 255), font=lg_font)
        else:
            col = PALETTE[(idx - 1) % len(PALETTE)]
            r = int(th * 0.45)
            dl.ellipse([pad * 2, y + th * 0.15, pad * 2 + 2 * r, y + th * 0.15 + 2 * r], fill=col + (255,))
            dl.text((pad * 2 + 2 * r + pad, y), t, fill=(255, 255, 255, 255), font=lg_font)
        y += int(th * 1.35)
    out = Image.alpha_composite(Image.alpha_composite(im, ov), lg).convert("RGB")
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
                for cid, _lbl, start, _path, _end in routes:
                    if start and cid in prev_end and prev_end[cid]:
                        px, py = prev_end[cid]
                        if math.hypot(start[0] - px, start[1] - py) > 0.05:
                            note = notes.get(cid, {}).get("continuity_note") if isinstance(notes.get(cid), dict) else None
                            msg = (f"{gid}/{cid}: start {start} 与前组终点 {prev_end[cid]} 不接"
                                   + (f"(continuity_note: {note})" if note else "(跨组位置跳变;确属离场再入场请在 start 写 continuity_note)"))
                            (all_warns if note else all_errs).append(msg)
            prev_scene = sid
            prev_end = {cid: (end or start) for cid, _l, start, _p, end in routes}
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
