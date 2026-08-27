#!/usr/bin/env python3
"""layout_map_bound 机检:核对组 prompt 是否挂上并绑定了「人物动线俯视图 + 9 宫格场景图」,
且逐角色动线句 route_en 逐字拼入 video_prompt。

规则(WORKFLOW.md §7A / prompt SOUL「空间布局按组确定性注入」,2026-08-19):
  - shot_list.generation_groups[].blocking_map 非空的组:
      ① refs 必含该组动线图 `directing/epNN/blocking_maps/<grp>.png`(由 code/render_blocking_map.py
         从 storyboard/shot-planning 的 blocking_map 渲染,文件必须存在);
      ② refs 必含该场景 9 宫格图 `assets/concepts/scenes/<sid>/grid_9views.png`
         (场景有昼夜等变体时可为 grid_9views_<cond>.png,同目录即可);
      ③ video_prompt 含固定空间布局声明句:引用动线图的 `[Image N]`(N=refs 下标+1)且同句含
         "top-down layout map"、引用 9 宫格图的 `[Image M]` 且同句含 "3x3 multi-angle";
         并含"do not render the map"类免责(防止把箭头/字母标记画进成片);
      ④ blocking_map.characters[].route_en 逐字出现在 video_prompt(比对忽略大小写与连续空白);
      ⑤ 图上标记映射句:每个角色按 blocking_map.characters 数组顺序对应字母 A/B/C…,video_prompt 须含
         "<字母> = <label> (<CHAR id>)"——label 逐字取 blocking_map.characters[].label(短规范名);
         2026-08-27 四订起俯视图上**只有字母与动线、无任何文字**,字母↔角色的对应完全靠这句,
         本机检只读 prompt 文本、不读图;
      ⑥ 主体定义句用同一个词:video_prompt 须含 "<label>@Image N"(角色主体定义句与 Map markers 句、
         动线图字母三者以 label 为唯一键;label 换词 = 模型对不上号);
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
LETTERS = "ABCDEFGH"   # 与 code/render_blocking_map.py 一致:blocking_map.characters 数组顺序 → 图上字母


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
    map_rel = f"directing/{ep}/blocking_maps/{gid}.png"
    # ① 动线图
    if not (proj_root / map_rel).is_file():
        errs.append(f"{gid}: 动线图未落盘 {map_rel}(先跑 code/render_blocking_map.py)")
    map_idx = next((i for i, r in enumerate(refs) if r == map_rel), None)
    if map_idx is None:
        errs.append(f"{gid}: refs 未列入动线图 {map_rel}")
    # ② 9 宫格
    sdir = proj_root / "assets" / "concepts" / "scenes" / sid
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
            errs.append(f"{gid}: video_prompt 缺动线图绑定句(同句含 {tag} 与 \"{MAP_KEY}\")")
        if not DISCLAIM_RE.search(vp):
            errs.append(f"{gid}: video_prompt 缺 \"do not render the map ...\" 免责句(防标记入画)")
    if grid_idx is not None:
        tag = f"[Image {grid_idx + 1}]"
        if not any(tag in s and GRID_KEY in s.lower() for s in sents):
            errs.append(f"{gid}: video_prompt 缺 9 宫格绑定句(同句含 {tag} 与 \"{GRID_KEY}\")")
    # ④ route_en 逐字 + ⑤ 字母↔角色映射句
    hay = norm(vp)
    for idx, ch in enumerate(bm.get("characters") or []):
        cid = ch.get("id", "?")
        letter = LETTERS[idx] if idx < len(LETTERS) else None
        label = (ch.get("label") or "").strip()
        if letter and cid != "?":
            pat = re.compile(r"(?<![A-Za-z])" + letter + r"\s*=\s*" + (re.escape(label) + r"\s*[((]\s*" if label else r"[^,;.。;]*")
                             + re.escape(cid))
            if not pat.search(vp):
                errs.append(f"{gid}/{cid}: video_prompt 缺图上标记映射 \"{letter} = {label or '<label>'} ({cid})\""
                            "(Map markers 句;label 逐字取 blocking_map)")
        if not label:
            errs.append(f"{gid}/{cid}: blocking_map 缺 label(短规范名;回派 shot-planning,先过 render_blocking_map.py 的 label_ok)")
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
        "layout_map_bound 机检:组 prompt 动线图/9 宫格 refs 绑定 + route_en 逐字核对",
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
