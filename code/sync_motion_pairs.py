#!/usr/bin/env python3
"""成对运镜机检 / 回写(motion_pair_bound,三期 2026-09-26;docs/transition_design.md)。

把 directing/<ep>/shot_list.json 里组边界的 `transition_in.motion_pair {out, in, speed}` 写成两句固定标记句进两侧组的 video_prompt
(modules/motion_pairs.py):前组**最后一个** Shot 段末尾写「【运镜对接】本镜结尾以…带出画面…」,本组**第一个** Shot 段末尾写
「【运镜对接】本镜开头延续上一组的…接入…」(非中文界面为 `Motion pair:` 英文句)。幂等:先剔除旧标记句再写;设计撤了就只剔除。
机检:有 motion_pair 的边界两侧组 prompt 都含与当前 out/in/speed 一致的句子且落在正确的 Shot 段;残留过期句 = 违规;组 prompt 未写 = skipped。
不带 --write 只机检(退出码 1 = 有违规);--write 回写 + 机检(原 prompt 首次备份到 directing/<ep>/whitebox/prompt_backups/)。

用法:python3 code/sync_motion_pairs.py --project <slug> --ep ep01 [grp…]            # 机检
     python3 code/sync_motion_pairs.py --project <slug> --ep ep01 [grp…] --write    # 回写 + 机检
     VIDEOAGENTS_UI_LANG=en …                                                    # 强制句子语言(测试用)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args  # noqa: E402
from modules.motion_pairs import sync_episode  # noqa: E402

CHECK = "motion_pair_bound"


def main():
    args, base = parse_args(__doc__, configure=lambda p: (
        p.add_argument("groups", nargs="*"),
        p.add_argument("--write", action="store_true", help="把运镜对接句写进两侧组 prompt(幂等)")))
    result = sync_episode(base, args.ep, args.groups, args.write)
    for p in result["pairs"]:
        mp = p["motion_pair"]
        print(f"PAIR {p['from_group']} → {p['to_group']}: {mp['out']} → {mp['in']} ({mp['speed']})")
    for r in result["groups"]:
        if r["skipped"]:
            print(f"SKIP {r['group_id']}: {r['skipped']}")
        elif r["updated"]:
            print(f"WRITE {r['group_id']} ({r['role']})")
    for e in result["errors"]:
        print("VIOLATION", e)
    print(json.dumps({"pairs": len(result["pairs"]), "updated_prompts": result["updated_prompts"]}, ensure_ascii=False))
    print(f"[{CHECK}] {args.project}/{args.ep}: 边界 {len(result['pairs'])} 处, 违规 {len(result['errors'])} 条 -> "
          f"{'FAIL' if result['errors'] else 'PASS'}")
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
