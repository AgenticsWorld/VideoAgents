#!/usr/bin/env python3
"""prop_facing_field 机检(p6 构图层):持读镜的道具朝向片段「该填没填」复核。

规则(composition SOUL 职责 5 / prop SOUL 职责 7,2026-08-25):
  - bible/props.json 里可手持的单面可读道具(手机/文书/照片/地图/典籍/符等)带
    `readable_face` 标记;
  - 某镜 composition.json 的 subjects 同时含 readable_face 道具与角色时,若该镜是
    持读镜(角色手持并看/读该道具),道具 subject 必填 `facing_fragment_en`
    (可读面朝向持读角色、非可读面朝镜头的物理朝向句,供 prompt 逐字拼入);
  - 持读与否是语义判断,机检不武断:缺片段一律 WARN 提示复核(--strict 时计 FAIL),
    由构图工位确认是否持读镜后补写或豁免;
  - 片段本身含运镜词或数值坐标 → VIOLATION(朝向声明不写运镜与数值,
    数值属模型无效信息,同 canonical_size 不进 prompt 的口径);
  - 整库无任何 readable_face 标记 → SKIP(存量项目未回补,建议回补后再跑),exit 0。

用法:python3 code/prop_facing_field_check.py --project <slug> --ep ep01
退出码:0=通过/SKIP(可含 WARN),1=有违规。
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args

CAMERA_WORDS = re.compile(
    r"\b(pan|dolly|zoom|tilt|track(?:ing)?|crane|push[- ]?in|pull[- ]?back|orbit)\b"
    r"|[推拉摇移]镜|运镜|变焦",
    re.IGNORECASE,
)
NUMERIC_COORD = re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)?\s*(?:px|cm|mm|m|%|deg|°)|[xy]\s*=\s*\d", re.IGNORECASE)


def load_sensitive_props(proj_root: Path):
    pf = proj_root / "bible" / "props.json"
    if not pf.is_file():
        return None, f"缺 {pf.relative_to(proj_root)}"
    pj = json.loads(pf.read_text())
    props = pj.get("props", pj if isinstance(pj, list) else [])
    return {p["id"] for p in props if isinstance(p, dict) and p.get("id") and p.get("readable_face")}, None


def subjects_of(comp: dict):
    if isinstance(comp.get("subjects"), list):
        return [s for s in comp["subjects"] if isinstance(s, dict)]
    subj = comp.get("subject")
    return [subj] if isinstance(subj, dict) else []


def main():
    args, proj_root = parse_args(
        "prop_facing_field 机检:持读镜道具 subject 的 facing_fragment_en 该填没填",
        configure=lambda ap: ap.add_argument(
            "--strict", action="store_true",
            help="缺 facing_fragment_en 也按违规计(新产出批次用)"),
    )
    sensitive, err = load_sensitive_props(proj_root)
    if err:
        print(f"[prop_facing_field] {args.project}: {err} -> SKIP")
        sys.exit(0)
    if not sensitive:
        print(f"[prop_facing_field] {args.project}: bible/props.json 无 readable_face 标记"
              "(存量项目未回补,建议给可手持的可读道具补标) -> SKIP")
        sys.exit(0)

    shots_dir = proj_root / "directing" / args.ep / "shots"
    errs, warns = [], []
    files = sorted(shots_dir.glob("*/composition.json"))
    for cf in files:
        shot_id = cf.parent.name
        try:
            comp = json.loads(cf.read_text())
        except Exception as e:
            warns.append(f"{shot_id}: composition.json 解析失败({e}),跳过")
            continue
        subs = subjects_of(comp)
        prop_subs = [s for s in subs if s.get("id") in sensitive]
        has_char = any(re.match(r"(?:CHAR|c\d)", str(s.get("id", ""))) for s in subs)
        for s in prop_subs:
            pid = s.get("id")
            frag = s.get("facing_fragment_en")
            if not frag:
                if has_char:
                    warns.append(f"{shot_id}: {pid} 与角色同镜但无 facing_fragment_en"
                                 "(复核是否持读镜:角色手持在看则回派 composition 补写)")
                continue
            if CAMERA_WORDS.search(frag):
                errs.append(f"{shot_id}: {pid} facing_fragment_en 含运镜词 —— \"{frag}\"")
            if NUMERIC_COORD.search(frag):
                errs.append(f"{shot_id}: {pid} facing_fragment_en 含数值坐标/尺寸 —— \"{frag}\"")
            if len(frag.split()) > 25 and len(frag) > 80:
                warns.append(f"{shot_id}: {pid} facing_fragment_en 超 25 词,建议精简 —— \"{frag}\"")
    if args.strict:
        errs += [w for w in warns if "无 facing_fragment_en" in w]
        warns = [w for w in warns if "无 facing_fragment_en" not in w]
    for w in warns:
        print("WARN", w)
    for e in errs:
        print("VIOLATION", e)
    print(f"[prop_facing_field] {args.project}/{args.ep}: {len(files)} 镜核对, "
          f"违规 {len(errs)} 条, WARN {len(warns)} 条 -> {'FAIL' if errs else 'PASS'}")
    sys.exit(1 if errs else 0)


if __name__ == "__main__":
    main()
