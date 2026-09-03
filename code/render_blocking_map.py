#!/usr/bin/env python3
"""render_blocking_map.py — 分镜组人物动线图:把组级 blocking_map(逐角色 起点/动线/终点)
标注渲染到该场景的俯视空间布局图上,并做 blocking_map 机检(blocking_map_present /
blocking_map_landmarks_valid / scene_layout_pack_ok)。

背景(WORKFLOW.md §4 Phase 4 / Phase 6,2026-08-19):
  - environment-concept 每场景出「俯视空间布局图」`assets/concepts/scenes/<sid>/layout_top.png`
    + 「9 宫格多角度场景图」`grid_9views.png` + 文字事实源 `layout.json`(地标归一化坐标 xy、
    九格机位语义 views[1..9]);
  - storyboard 每个生成组草案写 `blocking_map`(组内每个出场角色的 start / path / end,均引用
    layout.json 的地标 id,可选 xy 微调;附动线句 `route_en`——内容语言随界面语言;
    `label` = 该角色的短规范名,全集同一角色同一个词,下游 prompt 主体定义句与 Map markers 句逐字用它),
    shot-planning 定稿组时继承进
    shot_list.generation_groups[].blocking_map;
  - 本脚本把标注画到 layout_top.png 上,产出 `directing/epNN/blocking_maps/<group_id>.png`
    (storyboard 草案期 `--source storyboard` 写 `blocking_maps/draft/<scene_no>_g<NN>.png`),
    prompt agent 把定稿图列入组 refs,视频模型按图中人物位置标注与动线安排画面(机检
    layout_map_bound,脚本 code/layout_map_bound_check.py)。

图上标注(2026-08-27 四订,**只画字母与动线,不带任何文字**):每条目一色;角色 ● 实心圆 = 起点(圆内
      大写拉丁字母 A/B/C… = 条目序号 = blocking_map.characters 数组顺序,含生物条目)、■ 实心方块 = 终点;
      **生物条目(id `CRE-*`,独立态,2026-08-27)◆ 菱形 = 起点、▲ 三角 = 终点**,与角色形状区分;
      箭头折线 = 动线(经过 path 各点);无移动的条目只画起点标记。**不画图例、标题、角色名、CHAR 编号、
      动线句**——视频模型读不准参考图里的小字,烤进图的文字反而是入画泄漏口;字母↔角色的对应
      由 prompt 的 `Map markers: A = <label> (<CHAR id>), …` 句承担(机检 layout_map_bound)。
      字体仅需拉丁大写字母,不依赖 CJK 字体。渲染尺寸 = 布局图尺寸(≥2560x1440,平台统一出图规格)。
      本脚本是宿主 CLI:Agent 只准按下方用法调用,禁止复制/改写到项目 code/ 或自绘替代。

label 机检(label_ok,2026-08-27):每角色 `label` 必填,= 短规范名——≤8 个 CJK 字或 ≤3 个英文词;
      不得是代词(他/她/它/他们/她们/he/she/they…)、不得含括号/方括号/书名号/冒号/顿点等说明性标点
      (状态、服装、"画外"之类说明写进 route_en 或 continuity,不进 label);同一角色在本集所有组
      的 label 必须是同一个词(prompt 主体定义句 `<label>@Image N` 与 Map markers 句逐字用它)。

生物两态(creature_blocking_ok,2026-08-27):组 creatures_union 内每个生物必须二选一——
      ①**独立态**(牵引/拴着/独自入画/被处置):作为 blocking_map.characters[] 的一条(id `CRE-*`、label 短规范名、
        自有 start/path/end/route_en,route 写清相对主人的位置),自占一个字母;
      ②**骑乘态**(人在它背上):骑手条目 `mounted: "CRE-*"`,不占字母、不画(人兽同点同轨迹);
      同组同一生物不得两态并存;mounted 值须在 creatures_union 内;creatures_union 有生物却两态皆无 =
      --strict 违规 / 否则 WARN(生物在图上无锚)。字母池 A–L 共 12 个(含生物条目)。

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

MIN_PIXELS = 3_686_400          # 布局包出图规格下限(2560x1440 当量,平台统一出图规格;旧火山硬限 2026-09-01 已废止)
LAYOUT_SCHEMA = "scene_layout.v1"
PALETTE = [(230, 57, 70), (29, 120, 216), (46, 160, 67), (245, 158, 11),
           (142, 68, 173), (0, 172, 193), (233, 30, 99), (121, 85, 72)]
LETTERS = "ABCDEFGHIJKL"   # 2026-08-27:扩到 12 个,生物独立态条目也占字母
# label_ok(2026-08-27):短规范名;代词与说明性标点均不合格(见文件头)
_PRONOUNS = {"他", "她", "它", "他们", "她们", "它们", "我", "你", "我们", "你们",
             "he", "she", "it", "they", "him", "her", "them", "i", "we", "you", "me", "us"}
_LABEL_BAD_PUNCT = re.compile(r"[()()\[\]【】「」『』《》〈〉<>{}:：;;,,、·/|\\]")
LABEL_MAX_CJK, LABEL_MAX_WORDS = 8, 3


def check_label(label) -> str | None:
    """返回不合格原因;合格返回 None。"""
    if not isinstance(label, str) or not label.strip():
        return "缺 label(短规范名必填)"
    t = label.strip()
    if t.lower() in _PRONOUNS:
        return f"label {t!r} 是代词,须写角色的短规范名"
    if _LABEL_BAD_PUNCT.search(t):
        return f"label {t!r} 含括号/冒号/顿点等说明性标点(状态/服装/画外等说明写进 route_en,不进 label)"
    if t.isascii():
        if len(t.split()) > LABEL_MAX_WORDS:
            return f"label {t!r} 超过 {LABEL_MAX_WORDS} 个英文词"
    elif len(t) > LABEL_MAX_CJK:
        return f"label {t!r} 超过 {LABEL_MAX_CJK} 个字"
    return None


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
                errs.append(f"{sid}: {f.name} 仅 {w}x{h}={w*h} 像素 < {MIN_PIXELS}(布局包出图规格)")
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
    """统一产出 (group_key, scene_id, characters_union|None, creatures_union|None, blocking_map|None, out_rel)。"""
    if source == "shot_list":
        sl = json.loads((proj_root / "directing" / ep / "shot_list.json").read_text())
        for g in sl.get("generation_groups") or []:
            gid = g.get("group_id")
            if not gid:
                continue
            yield gid, g.get("scene_id"), g.get("characters_union"), g.get("creatures_union"), \
                g.get("blocking_map"), f"directing/{ep}/blocking_maps/{gid}.png"
    else:
        sb = json.loads((proj_root / "directing" / ep / "storyboard.json").read_text())
        for sc in sb.get("scenes") or []:
            sno = sc.get("scene_no") or sc.get("screenplay_ref") or "S"
            for g in sc.get("groups_draft") or []:
                go = g.get("group_order")
                key = f"{sno}_g{int(go):02d}" if go is not None else f"{sno}_g?"
                yield key, g.get("scene_id") or sc.get("scene_id"), None, g.get("creatures"), \
                    g.get("blocking_map"), f"directing/{ep}/blocking_maps/draft/{key}.png"


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


def validate_map(gid: str, bm, chars_union, landmarks: dict, creatures_union=None, strict: bool = False):
    """blocking_map 结构机检;返回 (routes, errs, warns)。
    routes=[(id, label, start, path, end, route_en, kind)],kind ∈ {"character", "creature"}(id 以 CRE- 开头为生物)。"""
    errs, warns, routes = [], [], []
    if not isinstance(bm, dict) or not isinstance(bm.get("characters"), list):
        return routes, [f"{gid}: blocking_map 缺 characters 数组"], warns
    seen, mounted = set(), {}
    for i, ch in enumerate(bm["characters"]):
        cid = ch.get("id") or f"#{i}"
        if cid in seen:
            errs.append(f"{gid}: blocking_map 条目重复 {cid}")
        seen.add(cid)
        kind = "creature" if cid.startswith("CRE-") else "character"
        m = ch.get("mounted")
        if m:
            if kind == "creature":
                errs.append(f"{gid}/{cid}: 生物条目不得带 mounted")
            elif not str(m).startswith("CRE-"):
                errs.append(f"{gid}/{cid}: mounted {m!r} 须是 CRE-* 生物 ID")
            else:
                mounted[m] = cid
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
        bad = check_label(ch.get("label"))
        if bad:
            errs.append(f"{gid}/{cid}: {bad}")
        routes.append((cid, (ch.get("label") or cid).strip() if isinstance(ch.get("label"), str) else cid,
                       start, path, end, route_en if isinstance(route_en, str) else "", kind))
    char_ids = {c for c in seen if not c.startswith("CRE-")}
    cre_ids = {c for c in seen if c.startswith("CRE-")}
    if chars_union is not None:
        a, b = char_ids, set(chars_union)
        if a != b:
            errs.append(f"{gid}: blocking_map 角色集合 {sorted(a)} ≠ 组 characters_union {sorted(b)}")
    # 生物两态(creature_blocking_ok,2026-08-27)
    for cre, rider in mounted.items():
        if cre in cre_ids:
            errs.append(f"{gid}/{cre}: 同组既有独立态条目又被 {rider} mounted(两态不得并存)")
    if creatures_union is not None:
        cu = set(creatures_union)
        for cre in sorted(cre_ids - cu):
            errs.append(f"{gid}/{cre}: blocking_map 生物条目不在组 creatures_union {sorted(cu)}")
        for cre, rider in mounted.items():
            if cre not in cu:
                errs.append(f"{gid}/{rider}: mounted {cre} 不在组 creatures_union {sorted(cu)}")
        for cre in sorted(cu - cre_ids - set(mounted)):
            msg = (f"{gid}/{cre}: 生物在 creatures_union 却未登记站位——独立态入 blocking_map 条目或骑乘态由骑手 mounted"
                   "(图上无锚,回派 storyboard/shot-planning)")
            (errs if strict else warns).append(msg)
    if len(routes) > len(LETTERS):
        errs.append(f"{gid}: 条目数 {len(routes)}(角色+生物)超出字母池上限 {len(LETTERS)}")
    return routes, errs, warns


# ---------------------------------------------------------------- render
# 四订(2026-08-27):图上只画字母标记与动线,字体仅需拉丁大写字母;不再加载 CJK 字体
_FONT_CANDIDATES = (
    "/System/Library/Fonts/Helvetica.ttc",                               # macOS
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",              # Linux
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",                                      # Windows
)


def _font(size: int):
    from PIL import ImageFont
    for cand in _FONT_CANDIDATES:
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


def render(layout_png: Path, routes, out_png: Path):
    """把动线标注画到布局图上:只有字母圆点/方块与箭头折线,无任何文字图例(2026-08-27 四订)。
    返回 (尺寸, WARN 列表);标记按数组逆序绘制(A 在最上层),近乎重合的标记报 WARN。"""
    from PIL import Image, ImageDraw
    im = Image.open(layout_png).convert("RGBA")
    W, H = im.size
    ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    R = max(18, int(W * 0.018))          # 标记半径
    LW = max(6, int(W * 0.005))          # 线宽
    fnt = _font(int(R * 1.3))
    to_px = lambda p: (p[0] * W, p[1] * H)  # noqa: E731

    def letter_at(x, y, letter):
        bb = d.textbbox((0, 0), letter, font=fnt)
        d.text((x - (bb[2] - bb[0]) / 2 - bb[0], y - (bb[3] - bb[1]) / 2 - bb[1]), letter,
               fill=(255, 255, 255, 255), font=fnt)

    warns = []
    # 先画全部动线,再按**逆序**画标记:数组靠前(A)的角色压在上面,不被后序角色盖住
    for idx, (_cid, _label, start, path, end, _route, _kind) in enumerate(routes):
        col = PALETTE[idx % len(PALETTE)]
        pts = [to_px(p) for p in ([start] if start else []) + path + ([end] if end else [])]
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
    markers = []   # (letter, x, y) 用于重叠检测
    def marker(kind, is_end, x, y, col):
        fill, ol = col + (255,), (255, 255, 255, 255)
        if kind == "creature":
            r = R * 1.25
            if is_end:   # ▲ 三角(字母略下移到三角重心)
                d.polygon([(x, y - r), (x + r, y + r * 0.8), (x - r, y + r * 0.8)], fill=fill, outline=ol, width=4)
            else:        # ◆ 菱形
                d.polygon([(x, y - r), (x + r, y), (x, y + r), (x - r, y)], fill=fill, outline=ol, width=4)
        elif is_end:     # ■ 方块
            d.rectangle([x - R, y - R, x + R, y + R], fill=fill, outline=ol, width=4)
        else:            # ● 圆
            d.ellipse([x - R, y - R, x + R, y + R], fill=fill, outline=ol, width=4)

    for idx in range(len(routes) - 1, -1, -1):
        _cid, _label, start, _path, end, _route, kind = routes[idx]
        col = PALETTE[idx % len(PALETTE)]
        letter = LETTERS[idx]
        if start:
            x, y = to_px(start)
            marker(kind, False, x, y, col)
            letter_at(x, y, letter)
            markers.append((letter, x, y))
        if end:
            x, y = to_px(end)
            marker(kind, True, x, y, col)
            letter_at(x, y + (R * 0.25 if kind == "creature" else 0), letter)
            markers.append((letter, x, y))
    for i, (la, xa, ya) in enumerate(markers):
        for lb, xb, yb in markers[i + 1:]:
            if la != lb and math.hypot(xa - xb, ya - yb) < R * 1.2:
                warns.append(f"标记 {la} 与 {lb} 几乎重合(距离 < 0.6 标记直径),字母可能被遮挡;"
                             "建议 storyboard/shot-planning 给靠后的角色 xy 微调错开")
    out = Image.alpha_composite(im, ov).convert("RGB")
    out_png.parent.mkdir(parents=True, exist_ok=True)
    out.save(out_png, optimize=True)
    return out.size, sorted(set(warns))


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
        label_of = {}                        # label_ok:同一角色全集 label 须同一个词(cid → (label, 首见组))
        for gid, sid, chars, cres, bm, out_rel in iter_groups(proj_root, args.ep, args.source):
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
            routes, e, w = validate_map(gid, bm, chars, landmarks, cres, args.strict)
            all_errs += e
            all_warns += w
            for cid, lbl, *_ in routes:
                if cid in label_of and label_of[cid][0] != lbl:
                    all_errs.append(f"{gid}/{cid}: label {lbl!r} 与 {label_of[cid][1]} 的 {label_of[cid][0]!r} 不一致"
                                    "(同一角色全集须同一个短规范名;状态变化写 route_en)")
                label_of.setdefault(cid, (lbl, gid))
            # 同场景相邻组:角色 start 须等于前组该角色 end(无移动=start),偏差 >5% 画幅报违规;
            # start 带 continuity_note(如角色中途出画后从别处入画)时降为 WARN
            if sid == prev_scene:
                notes = {c.get("id"): (c.get("start") or {}) for c in bm.get("characters", []) if isinstance(c, dict)}
                for cid, _lbl, start, _path, _end, _rt, _k in routes:
                    if start and cid in prev_end and prev_end[cid]:
                        px, py = prev_end[cid]
                        if math.hypot(start[0] - px, start[1] - py) > 0.05:
                            note = notes.get(cid, {}).get("continuity_note") if isinstance(notes.get(cid), dict) else None
                            msg = (f"{gid}/{cid}: start {start} 与前组终点 {prev_end[cid]} 不接"
                                   + (f"(continuity_note: {note})" if note else "(跨组位置跳变;确属离场再入场请在 start 写 continuity_note)"))
                            (all_warns if note else all_errs).append(msg)
            prev_scene = sid
            prev_end = {cid: (end or start) for cid, _l, start, _p, end, _r, _k in routes}
            if e or args.check_only:
                continue
            layout_png = proj_root / "assets" / "concepts" / "scenes" / sid / (lay.get("layout_top") or "layout_top.png")
            try:
                size, rw = render(layout_png, routes, proj_root / out_rel)
                all_warns += [f"{gid}: {m}" for m in rw]
                ncre = sum(1 for r in routes if r[6] == "creature")
                print(f"RENDERED {out_rel} {size[0]}x{size[1]} ({len(routes) - ncre} 角色" + (f" + {ncre} 生物" if ncre else "") + ")")
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
