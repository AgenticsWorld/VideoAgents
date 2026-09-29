#!/usr/bin/env python3
"""layout_map_bound 机检(2026-09-09 改版):组 prompt 不再挂场景俯视图 / 九宫格,逐角色动线句 route_en 逐字拼入 video_prompt。

规则(WORKFLOW.md §7A / prompt SOUL「空间布局按组确定性注入」,2026-08-19 立;2026-09-07、2026-09-09 两次改版):
  - shot_list.generation_groups[].blocking_map 非空的组:
      ① refs **不得**含场景俯视图 `assets/concepts/scenes/<sid>/layout_top*.png`(俯视图 2026-09-09 起只供分镜预览页查看,
         不进视频参考图)、**不得**含九宫格 `grid_9views*.png`(九宫格已退役、不再生成)、**不得**含已退役的动线标注图
         `directing/epNN/blocking_maps/*.png`(2026-09-07);
         场景空间由白模摄影机视频(whitebox_ref_bound)+ 分镜背景图(shot_plate_bound,code/sync_shot_plates.py)承担;
      ② 正文残留 `Spatial layout:` / `Map usage:` 俯视图声明句按 WARN(跑 code/sync_shot_plates.py --write 自动清理);
      ③ blocking_map.characters[].route_en 逐字出现在 video_prompt(比对忽略大小写与连续空白);
         route_en 自身混入裁决批注/坐标/时间码/白模术语(prose_clean,2026-09-29)= VIOLATION,回派上游清理;
      ④ 主体定义句用同一个词:video_prompt 须含 "<label>@Image N"(label 逐字取 blocking_map.characters[].label
         短规范名,全集同角色同词;骑乘态生物并入骑手条目、独立态生物条目同规则)。
  - blocking_map 为空/缺失的组按 WARN(存量项目;--strict 按 FAIL)。

用法:python3 code/layout_map_bound_check.py --project <slug> --ep ep01           # 查全批
     python3 code/layout_map_bound_check.py --project <slug> --ep ep01 grp002 …  # 只查指定组
退出码:0=通过(可含 WARN),1=有违规。prompt 批产出后必须全批跑;video-generation 开跑前单组复核。
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules.prose_hygiene import annotation_hits, describe, prompt_annotation_hits  # noqa: E402
from _common import parse_args, spatial_blocking_enabled  # noqa: E402

# 不要求紧跟 [Image:图号 token 被 remap 删掉后残留的「Spatial layout: is the …」空悬句也要测出(2026-09-14)
SPATIAL_RE = re.compile(r"Spatial layout:", re.I)
MAPUSE_RE = re.compile(r"Map usage:", re.I)


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def is_scene_map(ref: str) -> bool:
    name = Path(ref).name
    return ref.startswith("assets/concepts/scenes/") and (name.startswith("layout_top") or name.startswith("grid_9views"))


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
    refs = [r for r in (pj.get("refs") or []) if isinstance(r, str)]
    vp = pj.get("video_prompt", "")
    # ① 场景俯视图 / 九宫格 / 动线标注图不得进 refs
    for r in refs:
        if "/blocking_maps/" in r:
            errs.append(f"{gid}: refs 含已退役的动线标注图 {r}(2026-09-07 起人物位置由白模参考视频承担)")
        elif is_scene_map(r):
            errs.append(f"{gid}: refs 含场景俯视图/九宫格 {r}(2026-09-09 起俯视图仅供分镜预览、九宫格已退役,不进视频参考图;"
                        "场景空间由白模视频 + 分镜背景图承担,跑 code/sync_shot_plates.py --write 清理)")
    # ② 残留声明句
    if SPATIAL_RE.search(vp) or MAPUSE_RE.search(vp):
        warns.append(f"{gid}: 正文残留 Spatial layout / Map usage 俯视图声明句(跑 code/sync_shot_plates.py --write 清理)")
    # ③ route_en 逐字 + ④ 主体定义句同词
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
        hits = annotation_hits(route)
        if hits:
            # prose_clean(2026-09-29,前科 fengshen3 ep07 grp009):源头混了批注就不能照抄,也不能擅改——回派上游清理
            errs.append(f"{gid}/{cid}: 上游 route_en 混入批注/数值({describe(hits)}),不得逐字抄进 prompt——"
                        "回派 whitebox-staging(裁决套用)/ shot-planning 清理 shot_list blocking_map 后重写本组")
        elif norm(route) not in hay:
            errs.append(f"{gid}/{cid}: 动线句未逐字命中 video_prompt —— \"{route}\"")
    return errs, warns


def main():
    args, proj_root = parse_args(
        "layout_map_bound 机检:组 prompt 不挂俯视图/九宫格 + route_en 逐字核对 + 主体定义句同词",
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
