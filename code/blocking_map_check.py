#!/usr/bin/env python3
"""blocking_map_check.py — 分镜组人物动线数据机检(blocking_map_present / creature_blocking_ok /
label_ok / scene_layout_pack_ok)。

背景(WORKFLOW.md §4 Phase 4 / Phase 6,2026-08-19;2026-09-07 改版):
  - 一个 SCN 只登记一个空间(2026-09-07,机检 scene_single_space):layout.json 不得含 landmarks_<space> /
    views_<space> / orientation_<space> 子空间键,多空间须拆 ID 各出布局包;
  - environment-concept 每场景出「俯视空间布局图」`assets/concepts/scenes/<sid>/layout_top.png`
    + 文字事实源 `layout.json`(地标归一化坐标 xy、机位语义 views[]);**2026-09-09 起九宫格
    `grid_9views.png` 退役**:不再生成、不进视频参考图,存量文件不删也不核;
  - storyboard 每个生成组草案写 `blocking_map`(组内每个出场角色的 start / path / end,均引用
    layout.json 的地标 id,可选 xy 微调;附动线句 `route_en`——内容语言随界面语言;
    `label` = 该角色的短规范名,全集同一角色同一个词,下游 prompt 主体定义句 `<label>@Image N`
    与白模参考视频的人物图例逐字用它),shot-planning 定稿组时继承进
    shot_list.generation_groups[].blocking_map;
  - **2026-09-07 起本脚本只做数据机检、不再渲染动线图**:原「把起点/动线/终点字母标注叠加到
    layout_top.png 上产出 directing/epNN/blocking_maps/<grp>.png 并挂进组 refs」的流程退役——
    人物在场景中的空间位置与动线改由 3D 白模参考视频(docs/whitebox.md,`code/render_whitebox.py`
    产出 assets/whitebox/<ep>/<grp>/camera.mp4)+ 分镜背景图(2026-09-09,code/render_shot_plates.py,
    机检 shot_plate_bound)承担;俯视图只供分镜预览、不进组 refs(机检 layout_map_bound 改为核对
    refs 不含俯视图/九宫格 + route_en 逐字,脚本 code/layout_map_bound_check.py)。
    blocking_map 数据本身仍是白模编译(modules/whitebox.py)与 route_en 逐字注入的事实源,故保留机检。
    本脚本是宿主 CLI:Agent 只准按下方用法调用,禁止复制/改写到项目 code/。

label 机检(label_ok,2026-08-27):每角色 `label` 必填,= 短规范名——≤8 个 CJK 字或 ≤3 个英文词;
      不得是代词(他/她/它/他们/她们/he/she/they…)、不得含括号/方括号/书名号/冒号/顿点等说明性标点
      (状态、服装、"画外"之类说明写进 route_en 或 continuity,不进 label);同一角色在本集所有组
      的 label 必须是同一个词(prompt 主体定义句 `<label>@Image N` 逐字用它)。

生物两态(creature_blocking_ok,2026-08-27):组 creatures_union 内每个生物必须二选一——
      ①**独立态**(牵引/拴着/独自入画/被处置):作为 blocking_map.characters[] 的一条(id `CRE-*`、label 短规范名、
        自有 start/path/end/route_en,route 写清相对主人的位置);
      ②**骑乘态**(人在它背上):骑手条目 `mounted: "CRE-*"`(人兽同点同轨迹);
      同组同一生物不得两态并存;mounted 值须在 creatures_union 内;creatures_union 有生物却两态皆无 =
      --strict 违规 / 否则 WARN(生物在空间中无锚)。

用法:
  python3 code/blocking_map_check.py --project <slug> --ep ep01                 # 定稿:shot_list 全组
  python3 code/blocking_map_check.py --project <slug> --ep ep01 grp005 grp006   # 只查指定组
  python3 code/blocking_map_check.py --project <slug> --ep ep01 --source storyboard   # 草案期
  python3 code/blocking_map_check.py --project <slug> --scene SCN-0012          # 只查场景布局包
退出码:0=通过(可含 WARN),1=有违规。
"""
import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args, spatial_blocking_enabled  # noqa: E402
from modules.entity_ids import is_creature_id  # noqa: E402

MIN_PIXELS = 3_686_400          # 布局包出图规格下限(2560x1440 当量,平台统一出图规格;旧火山硬限 2026-09-01 已废止)
LAYOUT_SCHEMA = "scene_layout.v1"
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
    # scene_single_space(2026-09-07):一个 SCN 只登记一个空间。layout.json 出现 landmarks_<space> /
    # views_<space> / orientation_<space> 子空间键,即同一 ID 塞了第二个空间(前科 dzg6 SCN-0075/0076/0079:
    # 机舱/后厨/新加坡街道各挂一套附加包,机检与动线只认主包,ep02 grp005/grp019 因此无法解析);
    # 处置=回派 05-scenes/scene 拆 ID,再按新 ID 各出一套布局包,不得改脚本兼容子空间。
    sub_keys = sorted(k for k in lay if isinstance(k, str)
                      and (k.startswith("landmarks_") or k.startswith("views_") or k.startswith("orientation_")))
    if sub_keys:
        errs.append(f"{sid}: layout.json 含子空间键 {sub_keys}(scene_single_space:一个 SCN 只登记一个空间;"
                    f"第二个空间须由 05-scenes/scene 另立 SCN 再出独立布局包,不得以附加包并入)")
    # 2026-09-09:九宫格 grid_9views.png 退役(不再生成、不进视频参考图),布局包只要求俯视图 + layout.json
    for key, default in (("layout_top", "layout_top.png"),):
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
    """统一产出 (group_key, scene_id, characters_union|None, creatures_union|None, blocking_map|None)。"""
    if source == "shot_list":
        sl = json.loads((proj_root / "directing" / ep / "shot_list.json").read_text())
        for g in sl.get("generation_groups") or []:
            gid = g.get("group_id")
            if not gid:
                continue
            yield gid, g.get("scene_id"), g.get("characters_union"), g.get("creatures_union"), g.get("blocking_map")
    else:
        sb = json.loads((proj_root / "directing" / ep / "storyboard.json").read_text())
        for sc in sb.get("scenes") or []:
            sno = sc.get("scene_no") or sc.get("screenplay_ref") or "S"
            for g in sc.get("groups_draft") or []:
                go = g.get("group_order")
                key = f"{sno}_g{int(go):02d}" if go is not None else f"{sno}_g?"
                yield key, g.get("scene_id") or sc.get("scene_id"), None, g.get("creatures"), g.get("blocking_map")


def resolve_pt(pt, landmarks: dict, gid: str, cid: str, what: str, errs: list):
    """start/path[i]/end → 归一化 (x, y);地标 id 优先,xy 覆盖。"""
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
    routes=[(id, label, start, path, end, route_en, kind)],kind ∈ {"character", "creature"}(id 以 CRE-/cre_ 开头为生物,不区分大小写,见 modules/entity_ids)。"""
    errs, warns, routes = [], [], []
    if not isinstance(bm, dict) or not isinstance(bm.get("characters"), list):
        return routes, [f"{gid}: blocking_map 缺 characters 数组"], warns
    seen, mounted = set(), {}
    for i, ch in enumerate(bm["characters"]):
        cid = ch.get("id") or f"#{i}"
        if cid in seen:
            errs.append(f"{gid}: blocking_map 条目重复 {cid}")
        seen.add(cid)
        kind = "creature" if is_creature_id(cid) else "character"
        m = ch.get("mounted")
        if m:
            if kind == "creature":
                errs.append(f"{gid}/{cid}: 生物条目不得带 mounted")
            elif not is_creature_id(m):
                errs.append(f"{gid}/{cid}: mounted {m!r} 须是生物 ID(CRE-* / cre_*)")
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
        # 纯英文校验已取消(2026-08-24 二订):内容语言随界面语言
        elif (len(route_en.split()) > 40) if route_en.isascii() else (len(route_en) > 60):
            warns.append(f"{gid}/{cid}: route_en 过长(限 ≤40 英文词或 ≤60 字),建议精简")
        bad = check_label(ch.get("label"))
        if bad:
            errs.append(f"{gid}/{cid}: {bad}")
        routes.append((cid, (ch.get("label") or cid).strip() if isinstance(ch.get("label"), str) else cid,
                       start, path, end, route_en if isinstance(route_en, str) else "", kind))
    char_ids = {c for c in seen if not is_creature_id(c)}
    cre_ids = {c for c in seen if is_creature_id(c)}
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
                   "(空间中无锚,回派 storyboard/shot-planning)")
            (errs if strict else warns).append(msg)
    return routes, errs, warns


# ---------------------------------------------------------------- main
def main():
    args, proj_root = parse_args(
        "分镜组人物动线数据机检(blocking_map / scene_layout_pack)",
        configure=lambda ap: (
            ap.add_argument("groups", nargs="*", help="只处理指定组(shot_list 用 grpNNN;storyboard 用 S03_g01),缺省全部"),
            ap.add_argument("--source", choices=("shot_list", "storyboard"), default="shot_list",
                            help="读 shot_list.generation_groups(定稿,默认)或 storyboard.groups_draft(草案)"),
            ap.add_argument("--scene", action="append", default=[], help="只机检指定场景布局包(可多次),不处理分组"),
            ap.add_argument("--check-only", action="store_true", help="兼容旧用法,无实际作用(本脚本只机检,2026-09-07 起不再渲染)"),
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
        for gid, sid, chars, cres, bm in iter_groups(proj_root, args.ep, args.source):
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
                all_errs.append(f"{gid}: 场景 {sid} 无可用布局包,无法核对动线地标")
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
