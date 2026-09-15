#!/usr/bin/env python3
"""storyboard_pose_check.py — 人物姿态/动作机检(pose_present,2026-09-14)。

背景:分镜层 `directing/<ep>/storyboard.json` 每镜 `shots_draft[].poses` 登记每个出场角色的身体状态——
  `{ "<CHAR-id>": { "pose": "stand|sit|lie|kneel|crouch|prone", "action": "挥剑" } }`
  pose 为受控枚举(宿主映射成英文进草图提示词;白模关键帧同一套六态),action 中文直通(不翻译,2026-09-14 用户拍板)。
  该字段逐层继承:shot-planning 每镜照抄进 shot_list.json `shots[].poses`;blocking 按它写 blocking.json 每角色 `pose`
  并让 `space_fragment_en` 以体位开头(blocking_bound 逐字注入视频 prompt);白模编译取 blocking `pose` → shot_list `poses` → 文字粗推。
  存量项目没有 `poses` 时草图退回关键词推导(modules/storyboard_board.pose_hint),本脚本缺省只 WARN。

规则:
  - 写了 `poses` 的镜:每个出场角色(CHAR-*)必有条目且 pose 在枚举内(漏角色/枚举外 = FAIL);生物(CRE-*)缺条目 WARN;
    poses 里出现不在本镜/本场出场名单的 id WARN;
  - 整镜没写 `poses`:缺省 WARN(存量项目),`--strict` 按 FAIL(新项目交付前用);
  - `--source shot_list`:查 shot_list.json 每镜 `poses`(同规则);
  - `--blocking`:另查每镜 blocking.json 各角色 `pose` 在枚举内、与 shot_list 该镜 `poses` 一致,`space_fragment_en`
    含该体位的可辨认写法(站/坐/躺·卧/跪/蹲/趴 或英文;不含 = WARN,片段应以体位开头)。

用法:
  python3 code/storyboard_pose_check.py --project <slug> --ep ep01                     # 查 storyboard.json(分镜师交付前)
  python3 code/storyboard_pose_check.py --project <slug> --ep ep01 --strict            # 新项目:缺 poses 也 FAIL
  python3 code/storyboard_pose_check.py --project <slug> --ep ep01 --source shot_list  # 查镜头表定稿
  python3 code/storyboard_pose_check.py --project <slug> --ep ep01 --source shot_list --blocking   # 连 blocking.json 一起查
退出码:0 通过(可含 WARN)、1 有违规、2 源文件缺失。宿主机检脚本,Agent 只准调用、不得复制/改写到项目 code/。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules import storyboard_board as sbb  # noqa: E402


def _read(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def board_from_shot_list(sl: dict) -> dict:
    """把 shot_list.json 的 shots[] 摆成 check_poses 认得的伪故事板(一场装全部镜;场次名单 = 全部出场并集)。"""
    shots, cast_all = [], []
    for sh in sl.get("shots") or []:
        if not isinstance(sh, dict):
            continue
        cast = [c for c in (sh.get("characters") or []) + (sh.get("creatures") or []) if isinstance(c, str)]
        cast_all += [c for c in cast if c not in cast_all]
        shots.append({"key": sh.get("shot_id") or "", "cast": cast, "poses": sbb.normalize_poses(sh.get("poses"))})
    return {"scenes": [{"cast": cast_all, "creatures": [], "shots": shots}]}


def check_blocking(root: Path, ep: str, sl: dict, strict: bool) -> tuple[list[str], list[str]]:
    errs, warns = [], []
    for sh in sl.get("shots") or []:
        sid = sh.get("shot_id") or ""
        doc = _read(root / "directing" / ep / "shots" / sid / "blocking.json")
        if not isinstance(doc, dict):
            continue          # 尚未调度的镜不查
        poses = sbb.normalize_poses(sh.get("poses"))
        for entry in (doc.get("characters") or []) + (doc.get("creatures") or []):
            if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
                continue
            cid = entry["id"]
            pose = str(entry.get("pose") or "").strip().lower()
            shot_pose = (poses.get(cid) or {}).get("pose") or ""
            if pose and pose not in sbb.POSE_ENUM:
                errs.append(f"{sid}/{cid}: blocking pose {pose!r} 不在枚举 {'/'.join(sbb.POSE_ENUM)} 内")
                continue
            if pose and shot_pose and pose != shot_pose:
                errs.append(f"{sid}/{cid}: blocking pose {pose} ≠ shot_list poses {shot_pose}(体位以分镜层为准,要改先改 storyboard/shot_list)")
            if not pose:
                (errs if strict else warns).append(f"{sid}/{cid}: blocking.json 缺 pose(体位)")
            eff = pose or shot_pose
            frag = str(entry.get("space_fragment_en") or "").lower()
            if eff and frag and not any(w.lower() in frag for w in sbb.POSE_WORDS.get(eff, ())):
                warns.append(f"{sid}/{cid}: space_fragment_en 未体现体位 {eff}(片段应以体位开头,如「跪在炉前…」)")
    return errs, warns


def main() -> int:
    def configure(ap):
        ap.add_argument("--source", choices=("storyboard", "shot_list"), default="storyboard", help="查 storyboard.json(缺省)还是 shot_list.json")
        ap.add_argument("--strict", action="store_true", help="整镜缺 poses / blocking 缺 pose 也按 FAIL(新项目交付前)")
        ap.add_argument("--blocking", action="store_true", help="另查每镜 blocking.json 的 pose 与 space_fragment_en(需 shot_list)")
    args, root = parse_args("人物姿态/动作机检 pose_present", configure=configure)
    ep = args.ep
    sl = _read(root / "directing" / ep / "shot_list.json")
    if args.source == "storyboard":
        if not (root / "directing" / ep / "storyboard.json").is_file():
            print(f"MISSING directing/{ep}/storyboard.json")
            return 2
        board = sbb.load_board(root, ep)
    else:
        if not isinstance(sl, dict):
            print(f"MISSING directing/{ep}/shot_list.json")
            return 2
        board = board_from_shot_list(sl)
    errs, warns = sbb.check_poses(board, strict=args.strict)
    if args.blocking:
        if not isinstance(sl, dict):
            print(f"MISSING directing/{ep}/shot_list.json(--blocking 需要镜头表)")
            return 2
        e2, w2 = check_blocking(root, ep, sl, args.strict)
        errs += e2
        warns += w2
    for w in warns:
        print(f"WARN {w}")
    for e in errs:
        print(f"FAIL {e}")
    n = sum(len(sc.get("shots") or []) for sc in board.get("scenes") or [])
    print(f"{'FAIL' if errs else 'OK'} pose_present {args.source} {ep}:{n} 镜,{len(errs)} 违规,{len(warns)} 提醒")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
