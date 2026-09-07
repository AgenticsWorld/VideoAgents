#!/usr/bin/env python3
"""白模参考视频接线机检 / 回写(whitebox_ref_bound,2026-09-07)。

项目「输出设置 → 人物精确空间位置」开启时,每个已导出白模视频的分镜组,其组 prompt 必须:
  ① `video_refs` 以 camera.mp4(画面视角)开头,预算允许时紧随 top.mp4(俯视);
  ② video_prompt `Shot 1:` 前含固定段 `Whitebox reference: [Video N] … camera-view … [Video M] … top-down …`
     与 `Whitebox legend: <color> figure = <label> (<CHAR id>); …; eyes/nose tip = facing; dark camera box + line = shooting direction`;
  ③ `Global constraints:` 含禁白模外观句(no whitebox look …)。
不带 --write 只机检(退出码 1 = 有违规);--write 幂等回写以上三项(原 prompt 首次备份到
directing/<ep>/whitebox/prompt_backups/)。render_whitebox.py 导出后会自动 --write 一次;prompt 工位产出 prompt 后
再跑一次 --write 即可补齐。开关关闭时报 skipped。

用法:python3 code/sync_whitebox_refs.py --project <slug> --ep ep01 [grp…]            # 机检
     python3 code/sync_whitebox_refs.py --project <slug> --ep ep01 [grp…] --write    # 回写 + 机检
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args, spatial_blocking_enabled  # noqa: E402
from modules.whitebox_refs import sync_episode  # noqa: E402


def main():
    args, base = parse_args(__doc__, configure=lambda p: (
        p.add_argument('groups', nargs='*'),
        p.add_argument('--write', action='store_true', help='回写 video_refs / Whitebox reference 段 / Global constraints')))
    if not spatial_blocking_enabled(base):
        print(f"[whitebox_ref_bound] {args.project}: skipped: spatial_blocking off(项目输出设置「人物精确空间位置」已关闭,不接白模参考视频)-> PASS")
        return 0
    result = sync_episode(base, args.ep, args.groups, args.write)
    for w in result['warnings']:
        print('WARN', w)
    for e in result['errors']:
        print('VIOLATION', e)
    attached = sum(1 for r in result['groups'] if r['video_refs'])
    print(json.dumps({'groups': len(result['groups']), 'with_whitebox_video': attached,
                      'updated_prompts': result['updated_prompts']}, ensure_ascii=False))
    print(f"[whitebox_ref_bound] {args.project}/{args.ep}: 违规 {len(result['errors'])} 条, WARN {len(result['warnings'])} 条 -> {'FAIL' if result['errors'] else 'PASS'}")
    return 1 if result['errors'] else 0


if __name__ == '__main__':
    sys.exit(main())
