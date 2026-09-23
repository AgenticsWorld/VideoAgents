#!/usr/bin/env python3
"""镜次时长机检 / 回写(shot_timing_bound,2026-09-23)。

把 directing/<ep>/shot_list.json 的逐镜 duration_s 按本组生效视频模型的官方写法写进组级 video_prompt(modules/shot_timing.py):
  Seedance 2.5 / Wan 3.0 → 连续整数秒时间段,写在每段第一镜的 `Shot N:` 段头后(`0-2秒：` / `8-10秒（Shot 5–Shot 8）：`;
                            非中文界面 `0-2s:` / `8-10s (Shot 5–Shot 8):`),亚秒快切的相邻镜并段;
  MiniMax H3            → Shot k(k≥2)段头后写切点 `At MM:SS.mmm,` = 前 k-1 镜累计时长,首镜不写;
  Seedance 2.0          → 官方指南「精确时间段支持不稳定」,不写;存量标签剔除;
  其他/未知模型          → skipped。
机检:该写的组每段/切点齐全;段连续不重叠、自 0 起、末段止于 round(total_duration_s)(H3 切点 = 累计);
     组 total_duration_s 与 Σ duration_s 一致(±0.5s);每段长度与覆盖各镜之和相差 ≤1s。
不带 --write 只机检(退出码 1 = 有违规);--write 幂等回写(原 prompt 首次备份到 directing/<ep>/whitebox/prompt_backups/)。
组总时长仍由 video-generation 以 `--duration` 传参,正文不写「生成 N 秒视频」。

用法:python3 code/sync_shot_timing.py --project <slug> --ep ep01 [grp…]            # 机检
     python3 code/sync_shot_timing.py --project <slug> --ep ep01 [grp…] --write    # 回写 + 机检
     VIDEOAGENTS_UI_LANG=en …                                                   # 强制标签语言(测试用)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args  # noqa: E402
from modules.shot_timing import sync_episode  # noqa: E402

CHECK = 'shot_timing_bound'


def main():
    args, base = parse_args(__doc__, configure=lambda p: (
        p.add_argument('groups', nargs='*'),
        p.add_argument('--write', action='store_true', help='按生效模型回写/剔除时长标签')))
    result = sync_episode(base, args.ep, args.groups, args.write)
    for r in result['groups']:
        if r['skipped']:
            print(f"SKIP {r['group_id']}: {r['skipped']}")
    for w in result['warnings']:
        print('WARN', w)
    for e in result['errors']:
        print('VIOLATION', e)
    kinds = {}
    for r in result['groups']:
        kinds[r['kind'] or 'unknown'] = kinds.get(r['kind'] or 'unknown', 0) + 1
    print(json.dumps({'groups': len(result['groups']), 'kinds': kinds, 'updated_prompts': result['updated_prompts']},
                     ensure_ascii=False))
    print(f"[{CHECK}] {args.project}/{args.ep}: 违规 {len(result['errors'])} 条, WARN {len(result['warnings'])} 条 -> "
          f"{'FAIL' if result['errors'] else 'PASS'}")
    return 1 if result['errors'] else 0


if __name__ == '__main__':
    sys.exit(main())
