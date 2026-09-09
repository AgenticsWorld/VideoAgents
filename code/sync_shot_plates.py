#!/usr/bin/env python3
"""分镜背景图接线机检 / 回写(shot_plate_bound,2026-09-09)。

项目「输出设置 → 人物精确空间位置」开启时,每个已生成背景图的分镜组,其组 prompt 必须:
  ① refs 不含场景俯视图 layout_top*.png / 九宫格 grid_9views*.png(俯视图仅供分镜预览,九宫格已退役);
  ② refs 在角色/生物 sheet 之后按镜序挂本组各镜背景图(assets/concepts/scenes/<sid>/plates/<key>[.crop_fN].png;
     两张图的镜按 start、end 两张都挂,不走首尾帧模式);
  ③ video_prompt `Shot 1:` 前含固定段 `Shot plates: [Image N] is the empty background plate of Shot k …`
     (镜尾图为 `[Image M] is the end plate of Shot k …`),每张背景图都有说明句;
  ④ 背景图记录的机位与当前白模一致(不一致 WARN「已过期」,重跑 code/render_shot_plates.py)。
不带 --write 只机检(退出码 1 = 有违规);--write 幂等回写(剔除俯视图/九宫格 refs 与其声明句、插入背景图并重排 [Image N]、
写 Shot plates 段;原 prompt 首次备份到 directing/<ep>/whitebox/prompt_backups/)。render_shot_plates.py 出图后自动 --write 一次;
prompt 工位产出 prompt 后再跑一次 --write 即可补齐。尚无背景图的镜按 WARN(--strict 按违规)。开关关闭时报 skipped。

用法:python3 code/sync_shot_plates.py --project <slug> --ep ep01 [grp…]            # 机检
     python3 code/sync_shot_plates.py --project <slug> --ep ep01 [grp…] --write    # 回写 + 机检
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args, spatial_blocking_enabled  # noqa: E402
from modules.shot_plates import sync_episode  # noqa: E402


def main():
    args, base = parse_args(__doc__, configure=lambda p: (
        p.add_argument('groups', nargs='*'),
        p.add_argument('--write', action='store_true', help='回写 refs / Shot plates 段'),
        p.add_argument('--strict', action='store_true', help='尚无背景图的镜也按违规计')))
    if not spatial_blocking_enabled(base):
        print(f"[shot_plate_bound] {args.project}: skipped: spatial_blocking off(项目输出设置「人物精确空间位置」已关闭,不接分镜背景图)-> PASS")
        return 0
    result = sync_episode(base, args.ep, args.groups, args.write, args.strict)
    for w in result['warnings']:
        print('WARN', w)
    for e in result['errors']:
        print('VIOLATION', e)
    attached = sum(1 for r in result['groups'] if r['plates'])
    print(json.dumps({'groups': len(result['groups']), 'with_plates': attached,
                      'updated_prompts': result['updated_prompts']}, ensure_ascii=False))
    print(f"[shot_plate_bound] {args.project}/{args.ep}: 违规 {len(result['errors'])} 条, WARN {len(result['warnings'])} 条 -> {'FAIL' if result['errors'] else 'PASS'}")
    return 1 if result['errors'] else 0


if __name__ == '__main__':
    sys.exit(main())
