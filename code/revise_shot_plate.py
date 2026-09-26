#!/usr/bin/env python3
"""按用户修改意见重出一张分镜背景图(2026-09-26;分镜预览页「🖼 分镜背景图 → ✏️ 修改」→ 修改师代行 08-video-gen/shot-plates)。

与 render_shot_plates.py --force 的区别:--force 按机位重新决策、从全景/九宫格/世界模型再出一张,用户的意见进不去;
本脚本**以本镜当前这张背景图为 [Image 1]**(机位/构图/陈设/光线的权威参考),只改用户点名的地方,出一张新图入库
(key = <原 key>_rev<N>,条目 revised=True、pano_ref.kind=revision 记来源图与修改要求),再把**仅本镜该角色**(起点/终点)的
集索引条目改指向新图(reuse=revised,记 revised_from),并同步本组 prompt refs / Shot plates 段。
原图不动:不管它来自九宫格拆格、九宫格补图、全景截图、世界模型截图、母图还是手工截取,库里原条目与文件原样保留,
引用同一张原图的其它镜不受影响;分镜预览「🔁 换图」随时可以换回。
非 --force 的 render_shot_plates 按「记录仍新鲜」保留修订图;--status 对 revised 条目不报 legacy WARN。

用法:
  python code/revise_shot_plate.py --project <slug> --ep ep01 --shot sh012 --change "remove the red lantern above the door"
  python code/revise_shot_plate.py --project <slug> --ep ep01 --shot sh012 --role end --change "…" --note "用户原话"
  python code/revise_shot_plate.py --project <slug> --ep ep01 --shot sh012 --change "…" --dry-run     # 只看提示词/参考图,不出图不改索引
  可选 --seed N 固定种子。--change 写英文、只写要改的内容(不复述整张图);--note 放用户原话(会附在提示词末尾)。
  图像渠道 = 场景预览页顶栏「🎨 图像模型」的选择,空则控制台默认图像模型(与母图/补图同一口径)。
退出码:0 完成;1 出错(镜/角色/原图找不到、出图失败)。
纪律:前台跑完等退出码(一张约 1 分钟);一条修改意见出一张,不赛马、不多出候选;用户不满意再发修改单再出一张(链式 _rev2、_rev3…)。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args, spatial_blocking_enabled  # noqa: E402
from modules.shot_plates import revise_shot_plate  # noqa: E402
from modules.whitebox import component  # noqa: E402


def main():
    def configure(ap):
        ap.add_argument('--shot', required=True, help='镜号 shNNN')
        ap.add_argument('--role', default='start', choices=['start', 'end'], help='起点(默认)或终点背景图')
        ap.add_argument('--change', required=True, help='修改要求(英文,只写要改的内容)')
        ap.add_argument('--note', default='', help='用户原话(可选,附在提示词末尾)')
        ap.add_argument('--dry-run', action='store_true')
        ap.add_argument('--seed', type=int, default=None)
    args, base = parse_args(__doc__, configure=configure)
    if not spatial_blocking_enabled(base):
        print(f"[revise_shot_plate] {args.project}: skipped: spatial_blocking off(项目输出设置「人物精确空间位置」已关闭,没有分镜背景图)")
        return 0
    try:
        res = revise_shot_plate(base, component(args.ep), args.shot, args.role, args.change, note=args.note, dry_run=args.dry_run, seed=args.seed)
    except ValueError as error:
        print(f'错误: {error}', file=sys.stderr)
        return 1
    except Exception as error:  # noqa: BLE001
        print(f'错误: 出图失败 {error}', file=sys.stderr)
        return 1
    print(json.dumps({'revise_shot_plate': {k: v for k, v in res.items() if k not in ('prompt', 'negative')}}, ensure_ascii=False), flush=True)
    sync = res.get('sync') or {}
    if sync.get('errors'):
        for e in sync['errors']:
            print('VIOLATION', e)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
