#!/usr/bin/env python3
"""给某镜的一张分镜背景图写「采用口径」(2026-10-05,#113):Shot plates 机器段里该图的采用句按此生成,--write 幂等,手改不会被冲掉。

场景:用户手工换图/截图后,新图与本组光照方案不一致、或画面局部带有本镜不该入画的内容(海面、眩光),需要逐图收窄采用口径。
字段写在集索引 directing/<ep>/shot_plates.json 本镜该角色条目:
  adopt        采用项子集,可选 layout(空间布局)/ architecture(建筑)/ materials(材质)/ lighting(光线);缺省四项全采
  exclude_note 追加的不采用说明(一句,随界面语言;机器句原样拼在采用句后,如「不采用画面右侧的海面与眩光」)

  python code/plate_adopt.py --project <slug> --ep ep01 --shot sh012 --adopt layout,architecture,materials          # 不采用光线
  python code/plate_adopt.py --project <slug> --ep ep01 --shot sh012 --role end --exclude-note "不采用画面右侧的海面与眩光"
  python code/plate_adopt.py --project <slug> --ep ep01 --shot sh012 --clear                                         # 回到缺省口径
改完自动 sync_shot_plates --write 本组。退出码:0 完成;1 参数/找不到或 sync 机检有违规。
纪律:只写用户点名的收窄,不替用户决定;不得直接手改 prompt 的 Shot plates 段(机器段,--write 会重建)。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args, spatial_blocking_enabled  # noqa: E402
from modules import shot_plates as sp  # noqa: E402


def main():
    def configure(ap):
        ap.add_argument('--shot', required=True, help='镜号 shNNN')
        ap.add_argument('--role', default='start', choices=['start', 'end'])
        ap.add_argument('--adopt', default=None, help='采用项,逗号分隔:layout,architecture,materials,lighting(全写 = 缺省)')
        ap.add_argument('--exclude-note', default=None, help='追加的不采用说明(空串 = 删除)')
        ap.add_argument('--clear', action='store_true', help='删除 adopt 与 exclude_note,回到缺省口径')
    args, base = parse_args(__doc__, configure=configure)
    if not spatial_blocking_enabled(base):
        print(f"[plate_adopt] {args.project}: skipped: spatial_blocking off(没有分镜背景图)")
        return 0
    if not args.clear and args.adopt is None and args.exclude_note is None:
        print('错误: 须给 --adopt / --exclude-note 之一,或 --clear', file=sys.stderr)
        return 1
    try:
        res = sp.set_plate_adopt(base, args.ep, args.shot, args.role, adopt=args.adopt, exclude_note=args.exclude_note, clear=args.clear)
    except ValueError as error:
        print(f'错误: {error}', file=sys.stderr)
        return 1
    print(json.dumps({'plate_adopt': res}, ensure_ascii=False), flush=True)
    sync = res.get('sync') or {}
    for w in sync.get('warnings') or []:
        print('WARN', w)
    if sync.get('errors'):
        for e in sync['errors']:
            print('VIOLATION', e)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
