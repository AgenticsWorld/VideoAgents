#!/usr/bin/env python3
"""storyboard_narration_check.py — 旁白挂点机检(narration_ref_ok,2026-09-15)。

背景:旁白稿 `story/episodes/<ep>/narration.md` 在 Phase 1 定稿,每条只锚到「场次 | 场景 ID | 事件 ID | 剧本动作行」,
  不到镜。分镜师在 `directing/<ep>/storyboard.json` 每镜 `shots_draft[].narration_ref: ["N-01"]` 把每条旁白落到
  **起播镜**(旁白 riding over 后续镜,只挂首镜),用户在「📋 故事板」页 H3S 签字前就能核对旁白落在哪一镜;
  shot-planning 以此为起点定 `narration_anchors`(窗口 ≥ est×1.15 的硬校验仍在那一层)。
  存量项目没写 narration_ref 时,宿主按锚点引文推定挂点(modules/storyboard_board.attach_narration),本脚本缺省只 WARN。

规则:
  - narration_ref 引的 id 必须在旁白稿里(FAIL);同一条挂到多镜 WARN;
  - 引用它的镜所在场 ≠ 锚点场次(FAIL,挂错场);
  - 旁白稿每条都要有镜引用:缺省 WARN(存量项目),`--strict` FAIL(新项目交付前);
  - 所挂镜时长建议明显装不下估时 WARN(定稿窗口由 shot-planning 复核)。
  - 旁白稿声明本集无旁白 / 没有旁白稿:直接通过。

用法:
  python3 code/storyboard_narration_check.py --project <slug> --ep ep01            # 分镜师交付前
  python3 code/storyboard_narration_check.py --project <slug> --ep ep01 --strict   # 新项目:漏挂也 FAIL
退出码:0 通过(可含 WARN)、1 有违规、2 源文件缺失。宿主机检脚本,Agent 只准调用、不得复制/改写到项目 code/。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules import storyboard_board as sbb  # noqa: E402


def main() -> int:
    args, root = parse_args("旁白挂点机检 narration_ref_ok", configure=lambda ap: ap.add_argument(
        "--strict", action="store_true", help="旁白稿条目没有任何镜引用也算 FAIL(新项目交付前用)"))
    ep = args.ep
    if not (root / "directing" / ep / "storyboard.json").is_file():
        print(f"[narration_ref_ok] 缺 directing/{ep}/storyboard.json", file=sys.stderr)
        return 2
    narration = sbb.load_narration(root, ep)
    if not narration:
        print(f"[narration_ref_ok] {ep}: 没有旁白稿条目(story/episodes/{ep}/narration.md)——本集无旁白,通过")
        return 0
    board = sbb.load_board(root, ep)
    errs, warns = sbb.check_narration(board, narration, strict=args.strict)
    summ = board.get("narration") or {}
    for w in warns:
        print(f"WARN  {w}")
    for e in errs:
        print(f"FAIL  {e}")
    print(f"[narration_ref_ok] {ep}: 旁白 {summ.get('items', 0)} 条 · 挂点来源 {summ.get('by_source') or {}}"
          f" · 未定位 {len(summ.get('unplaced') or [])} · FAIL {len(errs)} · WARN {len(warns)}")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
