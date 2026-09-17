#!/usr/bin/env python3
"""场景图接线机检 / 回写(scene_plate_bound,2026-09-17;白模关闭项目的场景一致性 A 方案)。

项目「输出设置 → 白模」关闭时,每个组 prompt 必须:
  ① refs 在角色/生物 sheet 之后挂该场景的正向图(有镜标 reverse 且反向图已出时再挂反向图),不得再挂该场景其它概念图/俯视图/九宫格;
  ② video_prompt `Shot 1:` 前含固定段 `Scene plates: [Image N] is the front view … [Image M] is the reverse view …`;
  ③ 每个 Shot 段头含 `Scene plate: this shot uses [Image N] (…) and not [Image M].`(按 shot_list 该镜 plate_view;缺 plate_view 按 front 并 WARN)。
不带 --write 只机检(退出码 1 = 有违规);--write 幂等回写(原 prompt 首次备份到 directing/<ep>/scene_plates_backups/)。
render_scene_plates.py 出反向图后自动 --write 一次;prompt 工位产出 prompt 后再跑一次 --write。白模开启时报 skipped(那条链走 sync_shot_plates.py)。

用法:python3 code/sync_scene_plates.py --project <slug> --ep ep01 [grp…]            # 机检
     python3 code/sync_scene_plates.py --project <slug> --ep ep01 [grp…] --write    # 回写 + 机检
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args, spatial_blocking_enabled  # noqa: E402
from modules.scene_plates import sync_episode  # noqa: E402


def main():
    args, base = parse_args(__doc__, configure=lambda p: (
        p.add_argument('groups', nargs='*'),
        p.add_argument('--write', action='store_true', help='回写 refs / Scene plates 段 / 逐镜句'),
        p.add_argument('--strict', action='store_true', help='场景尚无正向图也按违规计')))
    if spatial_blocking_enabled(base):
        print(f"[scene_plate_bound] {args.project}: skipped: spatial_blocking on(项目「白模」已开启,场景一致性走白模视频 + 分镜背景图链 sync_shot_plates.py)-> PASS")
        return 0
    result = sync_episode(base, args.ep, args.groups, args.write, args.strict)
    for w in result['warnings']:
        print('WARN', w)
    for e in result['errors']:
        print('VIOLATION', e)
    with_front = sum(1 for r in result['groups'] if r.get('front'))
    print(json.dumps({'groups': len(result['groups']), 'with_front': with_front, 'with_reverse': sum(1 for r in result['groups'] if r.get('reverse')),
                      'updated_prompts': result['updated_prompts']}, ensure_ascii=False))
    print(f"[scene_plate_bound] {args.project}/{args.ep}: 违规 {len(result['errors'])} 条, WARN {len(result['warnings'])} 条 -> {'FAIL' if result['errors'] else 'PASS'}")
    return 1 if result['errors'] else 0


if __name__ == '__main__':
    sys.exit(main())
