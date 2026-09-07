#!/usr/bin/env python3
"""layout_map_bound 机检:核对组 prompt 是否挂上并绑定了「场景干净俯视图 + 9 宫格场景图」,
且逐角色动线句 route_en 逐字拼入 video_prompt。

规则(WORKFLOW.md §7A / prompt SOUL「空间布局按组确定性注入」,2026-08-19;2026-09-07 改版):
  - shot_list.generation_groups[].blocking_map 非空的组:
      ① refs 必含该场景干净俯视空间布局图 `assets/concepts/scenes/<sid>/layout_top.png`
         (environment-concept 布局包原图,直接引用、不叠加人物位置标注;场景有变体时可为
         layout_top_<cond>.png,同目录即可;文件必须存在)——**2026-09-07 起原「本组人物动线俯视图
         directing/epNN/blocking_maps/<grp>.png」退役**,人物空间位置与动线改由 3D 白模参考视频承担
         (docs/whitebox.md),refs 里出现 blocking_maps/ 路径按违规报;
      ② refs 必含该场景 9 宫格图 `assets/concepts/scenes/<sid>/grid_9views.png`
         (场景有昼夜等变体时可为 grid_9views_<cond>.png,同目录即可);
      ③ video_prompt 含固定空间布局声明句:引用俯视图的 `[Image N]`(N=refs 下标+1)且同句含
         "top-down layout map"、引用 9 宫格图的 `[Image M]` 且同句含 "3x3 multi-angle";
         并含"do not render the map"类免责(防止把平面图画进成片);
         **map_reference_only(2026-09-03,用户指令「不要将俯视图直接用于画面,俯视图仅用于空间位置参考」)**:
         另含一句同句带俯视图 `[Image N]` 与 "spatial position reference only" 的用途限定句
         (SOUL 固定句 `Map usage: [Image N] is a spatial position reference only, never the picture — …`),
         且 `Global constraints:` 段含 "bird's-eye"(`no top-down or bird's-eye view, no map or floor-plan imagery`);
      ④ blocking_map.characters[].route_en 逐字出现在 video_prompt(比对忽略大小写与连续空白);
      ⑤ 主体定义句用同一个词:video_prompt 须含 "<label>@Image N"(label 逐字取 blocking_map.characters[].label
         短规范名,全集同角色同词;骑乘态生物并入骑手条目、独立态生物条目同规则);
      (原 ⑤ `Map markers: A = <label> (<CHAR id>)` 字母映射句与 ⑧ `Blocking table:` 全局站位表段随动线标注图
       一并退役,2026-09-07:prompt 不再写,存量 prompt 含有也不报错)
  - blocking_map 为空/缺失的组按 WARN(存量项目;--strict 按 FAIL);场景无布局包按 WARN 并提示回派。

用法:python3 code/layout_map_bound_check.py --project <slug> --ep ep01           # 查全批
     python3 code/layout_map_bound_check.py --project <slug> --ep ep01 grp002 …  # 只查指定组
退出码:0=通过(可含 WARN),1=有违规。prompt 批产出后必须全批跑;video-generation 开跑前单组复核。
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args, spatial_blocking_enabled  # noqa: E402

MAP_KEY = "top-down layout map"
GRID_KEY = "3x3 multi-angle"
DISCLAIM_RE = re.compile(r"do not (render|draw|reproduce) the map", re.I)
REF_ONLY_KEY = "spatial position reference only"     # map_reference_only(2026-09-03):俯视图仅作空间位置参考句
BIRDSEYE_RE = re.compile(r"bird'?s[- ]?eye", re.I)     # Global constraints 须含 no top-down or bird's-eye view


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def sentences(text: str):
    return re.split(r"(?<=[.。;;!?])\s+", text or "")


def check_group(pf: Path, groups: dict, proj_root: Path, ep: str, strict: bool):
    pj = json.loads(pf.read_text())
    gid = pj.get("group_id", pf.stem)
    g = groups.get(gid)
    errs, warns = [], []
    if g is None:
        warns.append(f"{gid}: shot_list 无此组,跳过")
        return errs, warns
    bm = g.get("blocking_map")
    if not bm or not (bm.get("characters") or []):
        (errs if strict else warns).append(f"{gid}: shot_list 组缺 blocking_map(回派 storyboard/shot-planning 补标注)")
        return errs, warns
    sid = g.get("scene_id") or ""
    refs = [r for r in (pj.get("refs") or []) if isinstance(r, str)]
    vp = pj.get("video_prompt", "")
    sdir = proj_root / "assets" / "concepts" / "scenes" / sid
    # 退役的动线标注图(2026-09-07):不得再挂
    for r in refs:
        if "/blocking_maps/" in r:
            errs.append(f"{gid}: refs 含已退役的动线标注图 {r}(2026-09-07 起改挂干净 layout_top.png,人物位置由白模参考视频承担)")
    # ① 干净俯视图
    map_idx = next((i for i, r in enumerate(refs)
                    if r.startswith(f"assets/concepts/scenes/{sid}/layout_top") and r.endswith(".png")), None)
    if map_idx is None:
        errs.append(f"{gid}: refs 未列入场景干净俯视图 assets/concepts/scenes/{sid}/layout_top*.png"
                    + ("" if sdir.is_dir() and any(sdir.glob("layout_top*.png")) else "(场景无布局包,回派 environment-concept)"))
    elif not (proj_root / refs[map_idx]).is_file():
        errs.append(f"{gid}: refs 所列俯视图不存在 {refs[map_idx]}")
    # ② 9 宫格
    grid_idx = next((i for i, r in enumerate(refs)
                     if r.startswith(f"assets/concepts/scenes/{sid}/grid_9views") and r.endswith(".png")), None)
    if grid_idx is None:
        if sdir.is_dir() and any(sdir.glob("grid_9views*.png")):
            errs.append(f"{gid}: refs 未列入场景 9 宫格图 assets/concepts/scenes/{sid}/grid_9views*.png")
        else:
            warns.append(f"{gid}: 场景 {sid} 无 grid_9views 图(存量场景,回派 environment-concept 补布局包)")
    elif not (proj_root / refs[grid_idx]).is_file():
        errs.append(f"{gid}: refs 所列 9 宫格图不存在 {refs[grid_idx]}")
    # ③ 固定声明句
    sents = sentences(vp)
    if map_idx is not None:
        tag = f"[Image {map_idx + 1}]"
        if not any(tag in s and MAP_KEY in s.lower() for s in sents):
            errs.append(f"{gid}: video_prompt 缺俯视图绑定句(同句含 {tag} 与 \"{MAP_KEY}\")")
        if not DISCLAIM_RE.search(vp):
            errs.append(f"{gid}: video_prompt 缺 \"do not render the map ...\" 免责句(防平面图入画)")
        # map_reference_only(2026-09-03):俯视图仅作空间位置参考,不得直接用于画面
        if not any(tag in s and REF_ONLY_KEY in s.lower() for s in sents):
            errs.append(f"{gid}: video_prompt 缺俯视图用途限定句(同句含 {tag} 与 \"{REF_ONLY_KEY}\";"
                        "固定句 Map usage: [Image N] is a spatial position reference only, never the picture — …)")
        gc = vp.split("Global constraints:", 1)[1] if "Global constraints:" in vp else ""
        if not BIRDSEYE_RE.search(gc):
            errs.append(f"{gid}: Global constraints 缺 \"no top-down or bird's-eye view, no map or floor-plan imagery\"(俯视图不得直接用于画面)")
    if grid_idx is not None:
        tag = f"[Image {grid_idx + 1}]"
        if not any(tag in s and GRID_KEY in s.lower() for s in sents):
            errs.append(f"{gid}: video_prompt 缺 9 宫格绑定句(同句含 {tag} 与 \"{GRID_KEY}\")")
    # ④ route_en 逐字 + ⑤ 主体定义句同词
    hay = norm(vp)
    for ch in bm.get("characters") or []:
        cid = ch.get("id", "?")
        label = (ch.get("label") or "").strip()
        if not label:
            errs.append(f"{gid}/{cid}: blocking_map 缺 label(短规范名;回派 shot-planning,先过 blocking_map_check.py 的 label_ok)")
        elif not re.search(re.escape(label) + r"\s*@\s*Image\s*\d+", vp):
            errs.append(f"{gid}/{cid}: video_prompt 缺主体定义句 \"{label}@Image N\"(主体定义句须与 blocking_map.label 同一个词)")
        route = ch.get("route_en")
        if not route:
            errs.append(f"{gid}/{cid}: blocking_map 缺 route_en(回派 storyboard 补写)")
            continue
        if norm(route) not in hay:
            errs.append(f"{gid}/{cid}: 动线句未逐字命中 video_prompt —— \"{route}\"")
    return errs, warns


def main():
    args, proj_root = parse_args(
        "layout_map_bound 机检:组 prompt 俯视图/9 宫格 refs 绑定 + route_en 逐字核对",
        configure=lambda ap: (
            ap.add_argument("groups", nargs="*", help="只查指定组(如 grp002),缺省全批"),
            ap.add_argument("--strict", action="store_true", help="组缺 blocking_map 也按违规计(新产出批次用)"),
        ),
    )
    if not spatial_blocking_enabled(proj_root):
        print(f"[layout_map_bound] {args.project}: skipped: spatial_blocking off(项目输出设置「人物精确空间位置」已关闭,走单张场景概念图流程)-> PASS")
        sys.exit(0)
    sl_path = proj_root / "directing" / args.ep / "shot_list.json"
    if not sl_path.is_file():
        print(f"VIOLATION 缺 {sl_path}")
        sys.exit(1)
    sl = json.loads(sl_path.read_text())
    groups = {g.get("group_id"): g for g in sl.get("generation_groups") or [] if isinstance(g, dict)}
    files = sorted((proj_root / "assets" / "prompts" / args.ep).glob("grp*.json"))
    files = [f for f in files if not f.name.endswith(".meta.json")]
    if args.groups:
        only = set(args.groups)
        files = [f for f in files if f.stem in only]
    all_errs, all_warns = [], []
    for f in files:
        e, w = check_group(f, groups, proj_root, args.ep, args.strict)
        all_errs += e
        all_warns += w
    for w in all_warns:
        print("WARN", w)
    for e in all_errs:
        print("VIOLATION", e)
    print(f"[layout_map_bound] {args.project}/{args.ep}: {len(files)} 组核对, "
          f"违规 {len(all_errs)} 条, WARN {len(all_warns)} 条 -> {'FAIL' if all_errs else 'PASS'}")
    sys.exit(1 if all_errs else 0)


if __name__ == "__main__":
    main()
