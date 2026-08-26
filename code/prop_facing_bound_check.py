#!/usr/bin/env python3
"""prop_facing_bound 机检(p7 prompt 层,出片前硬门禁):持读镜道具朝向片段逐字命中核对。

规则(prompt SOUL 手持可读道具朝向锚,2026-08-25):
  - composition.json 道具 subject 带 `facing_fragment_en` 的镜(持读镜:角色手持
    并看/读手机/合同/照片等可读道具),组 prompt 该镜对应 Shot 段必须**逐字**包含
    该片段(比对忽略大小写与连续空白,其余一字不差)——严禁自行改写朝向散文,
    不逐字必被正面平铺参考图带走(前科:offer ep01 grp009 合同条款正对镜头,
    composition 写了朝向但被 prompt 丢弃);
  - 组内存在持读镜时,video_prompt 的 Global constraints 必含朝向恒定句(全片唯一
    写法,按子串匹配):readable faces of documents, photos and screens keep their
    established orientation, never turning to face the camera;
  - 朝向约束只写在 negative 字段 = 没写(negative 不进生成请求);
  - Shot 段与镜的对应:组 prompt 的 Shot i 对应 grpNNN.json shots[i-1];Shot 段数
    与镜数不符时降级为全文匹配并给出 WARN;
  - 判定完全由 composition 字段存在与否驱动(确定性,无语义猜测);上游缺片段
    归 prop_facing_field 机检管,本脚本不重复报。

用法:python3 code/prop_facing_bound_check.py --project <slug> --ep ep01           # 查全批
     python3 code/prop_facing_bound_check.py --project <slug> --ep ep01 grp009 …  # 只查指定组
退出码:0=通过(可含 WARN),1=有违规(逐条打印)。
prompt 批产出后必须全批跑一遍;video-generation 开跑前对单组复核。
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args

CONSTANT_SENTENCE = ("readable faces of documents, photos and screens keep their "
                     "established orientation, never turning to face the camera")


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


def facing_fragments(comp: dict):
    """取本镜所有带 facing_fragment_en 的 subject → [(id, fragment)]。"""
    subs = comp.get("subjects") if isinstance(comp.get("subjects"), list) else \
        ([comp["subject"]] if isinstance(comp.get("subject"), dict) else [])
    out = []
    for s in subs:
        if isinstance(s, dict) and s.get("facing_fragment_en"):
            out.append((s.get("id", "?"), s["facing_fragment_en"]))
    return out


def check_group(pf: Path, proj_root: Path, ep: str):
    pj = json.loads(pf.read_text())
    gid = pj.get("group_id", pf.stem)
    raw_shots = pj.get("shots", [])
    shots = [s.get("shot_id") if isinstance(s, dict) else s for s in raw_shots]
    vp = pj.get("video_prompt", "")
    errs, warns = [], []
    segs = shot_segments(vp, len(shots))
    if segs is None and shots:
        warns.append(f"{gid}: Shot 段数与镜数({len(shots)})不符,降级为全文匹配")
    group_has_reading_shot = False
    for i, shot_id in enumerate(shots, start=1):
        if not shot_id:
            warns.append(f"{gid}: shots[{i-1}] 缺 shot_id,跳过")
            continue
        cf = proj_root / "directing" / ep / "shots" / shot_id / "composition.json"
        if not cf.is_file():
            warns.append(f"{gid}/{shot_id}: 无 composition.json,跳过")
            continue
        try:
            comp = json.loads(cf.read_text())
        except Exception as e:
            warns.append(f"{gid}/{shot_id}: composition.json 解析失败({e}),跳过")
            continue
        frags = facing_fragments(comp)
        if not frags:
            continue
        group_has_reading_shot = True
        haystack = norm(segs[i]) if segs else norm(vp)
        where = f"Shot {i}" if segs else "全文"
        for pid, frag in frags:
            if norm(frag) not in haystack:
                errs.append(f"{gid}/{shot_id}: {pid} 朝向片段未逐字命中{where} —— \"{frag}\"")
    if group_has_reading_shot and norm(CONSTANT_SENTENCE) not in norm(vp):
        errs.append(f"{gid}: 组含持读镜但 Global constraints 缺朝向恒定句 —— \"{CONSTANT_SENTENCE}\"")
    return errs, warns


def main():
    args, proj_root = parse_args(
        "prop_facing_bound 机检:组 prompt 持读镜朝向句 vs composition.facing_fragment_en 逐字核对 + 恒定句",
        configure=lambda ap: ap.add_argument("groups", nargs="*", help="只查指定组(如 grp009),缺省全批"),
    )
    files = sorted((proj_root / "assets" / "prompts" / args.ep).glob("grp*.json"))
    files = [f for f in files if not f.name.endswith(".meta.json")]
    if args.groups:
        only = set(args.groups)
        files = [f for f in files if f.stem in only]
    all_errs, all_warns = [], []
    for f in files:
        errs, warns = check_group(f, proj_root, args.ep)
        all_errs += errs
        all_warns += warns
    for w in all_warns:
        print("WARN", w)
    for e in all_errs:
        print("VIOLATION", e)
    print(f"[prop_facing_bound] {args.project}/{args.ep}: {len(files)} 组核对, "
          f"违规 {len(all_errs)} 条, WARN {len(all_warns)} 条 -> {'FAIL' if all_errs else 'PASS'}")
    sys.exit(1 if all_errs else 0)


if __name__ == "__main__":
    main()
