#!/usr/bin/env python3
"""blocking_bound 机检:核对组 prompt 的每镜空间位置句是否逐字拼入 blocking 的站位片段。

规则(WORKFLOW §7A / prompt SOUL 空间站位注入,2026-07-23):
  - blocking.json 每个入画角色带 `space_fragment_en`(英文站位片段:场景地标关系
    + 屏侧方位 + 朝向,由 blocking agent 产出);
  - prompt agent 写组级 video_prompt 时,该镜对应 Shot 段必须**逐字**包含该镜每个
    入画角色的 space_fragment_en(比对忽略大小写与连续空白,其余一字不差)——
    严禁自行改写站位散文,自由翻译就是空间漂移入口(前科:ep01 grp007→grp008
    福瓦德门外瞬移入门内);
  - Shot 段与镜的对应:组 prompt 的 Shot i 对应 grpNNN.json shots[i-1];Shot 段数
    与镜数不符时降级为全文匹配并给出 WARN。
  - 存量 blocking.json 无 space_fragment_en 的按 WARN 报(待回派 blocking 补写),
    加 --strict 时按 FAIL。

用法:python3 code/blocking_bound_check.py --project <slug> --ep ep05           # 查全批
     python3 code/blocking_bound_check.py --project <slug> --ep ep05 grp002 …  # 只查指定组
退出码:0=通过(可含 WARN),1=有违规(逐条打印)。
prompt 批产出后必须全批跑一遍;video-generation 开跑前对单组复核。
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def shot_segments(video_prompt: str, n_shots: int):
    """按 `Shot N:` 切分;段数与镜数一致返回 {shot_index: 段文本},否则返回 None(降级全文匹配)。"""
    parts = re.split(r"Shot\s*(\d+)\s*[:：]", video_prompt)
    segs = {}
    for i in range(1, len(parts) - 1, 2):
        segs[int(parts[i])] = parts[i + 1]
    if set(segs) != set(range(1, n_shots + 1)):
        return None
    return segs


def check_group(pf: Path, proj_root: Path, ep: str, strict: bool):
    pj = json.loads(pf.read_text())
    gid = pj.get("group_id", pf.stem)
    shots = pj.get("shots", [])
    vp = pj.get("video_prompt", "")
    errs, warns = [], []
    segs = shot_segments(vp, len(shots))
    if segs is None and shots:
        warns.append(f"{gid}: Shot 段数与镜数({len(shots)})不符,降级为全文匹配")
    for i, shot_id in enumerate(shots, start=1):
        bf = proj_root / "directing" / ep / "shots" / shot_id / "blocking.json"
        if not bf.is_file():
            warns.append(f"{gid}/{shot_id}: 无 blocking.json,跳过")
            continue
        bj = json.loads(bf.read_text())
        haystack = norm(segs[i]) if segs else norm(vp)
        where = f"Shot {i}" if segs else "全文"
        for ch in bj.get("characters", []):
            cid = ch.get("id", "?")
            frag = ch.get("space_fragment_en")
            if not frag:
                warns.append(f"{gid}/{shot_id}: {cid} 缺 space_fragment_en(回派 blocking 补写)")
                continue
            if norm(frag) not in haystack:
                errs.append(f"{gid}/{shot_id}: {cid} 站位片段未逐字命中{where} —— \"{frag}\"")
    if strict:
        errs += [w for w in warns if "缺 space_fragment_en" in w]
        warns = [w for w in warns if "缺 space_fragment_en" not in w]
    return errs, warns


def main():
    args, proj_root = parse_args(
        "blocking_bound 机检:组 prompt 空间位置句 vs blocking.space_fragment_en 逐字核对",
        configure=lambda ap: (
            ap.add_argument("groups", nargs="*", help="只查指定组(如 grp002),缺省全批"),
            ap.add_argument("--strict", action="store_true",
                            help="缺 space_fragment_en 也按违规计(新产出批次用)"),
        ),
    )
    files = sorted((proj_root / "assets" / "prompts" / args.ep).glob("grp*.json"))
    files = [f for f in files if not f.name.endswith(".meta.json")]
    if args.groups:
        only = set(args.groups)
        files = [f for f in files if f.stem in only]
    all_errs, all_warns = [], []
    for f in files:
        errs, warns = check_group(f, proj_root, args.ep, args.strict)
        all_errs += errs
        all_warns += warns
    for w in all_warns:
        print("WARN", w)
    for e in all_errs:
        print("VIOLATION", e)
    print(f"[blocking_bound] {args.project}/{args.ep}: {len(files)} 组核对, "
          f"违规 {len(all_errs)} 条, WARN {len(all_warns)} 条 -> {'FAIL' if all_errs else 'PASS'}")
    sys.exit(1 if all_errs else 0)


if __name__ == "__main__":
    main()
