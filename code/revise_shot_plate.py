#!/usr/bin/env python3
"""按用户修改意见重出一张分镜背景图(2026-09-26;分镜预览页「🖼 分镜背景图 → ✏️ 修改」→ 修改师代行 08-video-gen/shot-plates;
2026-10-09 加场景预览页「分镜背景图」板块每张库图的「✏️ 修改」→ 直发 08-video-gen/shot-plates,见下「库图模式」)。

与 render_shot_plates.py --force 的区别:--force 按机位重新决策、从全景/九宫格/世界模型再出一张,用户的意见进不去;
本脚本**以当前这张背景图为 [Image 1]**(机位/构图/陈设/光线的权威参考),只改用户点名的地方,出一张新图入库
(key = <原 key>_rev<N>,条目 revised=True、pano_ref.kind=revision 记来源图与修改要求),尺寸同九宫格补图(1920x1080,16:9)。
原图不动:不管它来自九宫格拆格、九宫格补图、全景截图、世界模型截图、母图还是手工截取,库里原条目与文件原样保留;
分镜预览「🔁 换图」随时可以换回。非 --force 的 render_shot_plates 按「记录仍新鲜」保留修订图;--status 对 revised 条目不报 legacy WARN。

两种模式:
  按镜模式(--ep --shot [--role]):只把**本镜该角色**(起点/终点)的集索引条目改指向新图,引用同一原图的其它镜不动。
  库图模式(--scene --key):以场景库里这张图为参考出新图,再把**引用这张原图的分镜**全部改指向新图(各集集索引,reuse=revised);
    --only ep01/sh010,ep01/sh012(end) 只换点名的镜(ep01/sh010 = 该镜引用它的起点与终点;(end)/:end 只换终点),
    --library-only 一个都不换(只入库,之后在分镜预览「换图」手选)。用户没说范围时按默认全部替换。
参考图:--ref <图片路径> 可重复(最多 4 张;用户在修改浮窗用「+」附的本机图片,消息末尾「附件」段的绝对路径),以 [Image 2…] 传给
  图像模型,复制进 assets/concepts/scenes/<sid>/plates/revision_refs/ 留档;--change 里用 [Image 2] 等写明要从参考图取什么。

用法:
  python code/revise_shot_plate.py --project <slug> --ep ep01 --shot sh012 --change "remove the red lantern above the door"
  python code/revise_shot_plate.py --project <slug> --ep ep01 --shot sh012 --role end --change "…" --note "用户原话"
  python code/revise_shot_plate.py --project <slug> --scene SCN-0012 --key L1_grid9_t3 --change "replace the door with the carved door in [Image 2]" \\
      --ref /Users/me/Desktop/door.jpg --note "用户原话"
  python code/revise_shot_plate.py --project <slug> --scene SCN-0012 --key L1_grid9_t3 --change "…" --only ep01/sh010
  任一模式加 --dry-run:只看提示词/参考图/将替换的镜,不出图不改索引。可选 --seed N 固定种子。
  --change 写英文、只写要改的内容(不复述整张图);--note 放用户原话(会附在提示词末尾)。
  图像渠道 = 场景预览页顶栏「🎨 图像模型」的选择,空则控制台默认图像模型(与母图/补图同一口径)。
退出码:0 完成;1 出错(镜/角色/库图/原图/参考图找不到或不合格、出图失败、sync 违规)。
纪律:前台跑完等退出码(一张约 1 分钟);一条修改意见出一张,不赛马、不多出候选;用户不满意再发修改单再出一张(链式 _rev2、_rev3…)。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args, spatial_blocking_enabled  # noqa: E402
from modules.shot_plates import revise_library_plate, revise_shot_plate  # noqa: E402
from modules.whitebox import component  # noqa: E402


def main():
    def configure(ap):
        ap.add_argument('--shot', default='', help='按镜模式:镜号 shNNN')
        ap.add_argument('--role', default='start', choices=['start', 'end'], help='按镜模式:起点(默认)或终点背景图')
        ap.add_argument('--scene', default='', help='库图模式:场景编号(与 --key 同用)')
        ap.add_argument('--key', default='', help='库图模式:场景背景图库里的 key')
        ap.add_argument('--only', action='append', default=[], help='库图模式:只替换这些镜(ep01/sh010、ep01/sh010(end),逗号分隔或重复)')
        ap.add_argument('--library-only', action='store_true', help='库图模式:只入库,不替换任何镜')
        ap.add_argument('--change', required=True, help='修改要求(英文,只写要改的内容)')
        ap.add_argument('--note', default='', help='用户原话(可选,附在提示词末尾)')
        ap.add_argument('--ref', action='append', default=[], help='用户参考图路径(可重复,最多 4 张)')
        ap.add_argument('--dry-run', action='store_true')
        ap.add_argument('--seed', type=int, default=None)
    args, base = parse_args(__doc__, configure=configure)
    if not spatial_blocking_enabled(base):
        print(f"[revise_shot_plate] {args.project}: skipped: spatial_blocking off(项目输出设置「人物精确空间位置」已关闭,没有分镜背景图)")
        return 0
    library = bool(args.scene or args.key)
    if library and not (args.scene and args.key):
        print('错误: 库图模式须同时给 --scene 与 --key', file=sys.stderr)
        return 1
    if library and args.shot:
        print('错误: --shot(按镜模式)与 --scene/--key(库图模式)只能二选一', file=sys.stderr)
        return 1
    if not library and not args.shot:
        print('错误: 须给 --shot(按镜模式)或 --scene + --key(库图模式)', file=sys.stderr)
        return 1
    if not library and (args.only or args.library_only):
        print('错误: --only / --library-only 只用于库图模式', file=sys.stderr)
        return 1
    try:
        if library:
            res = revise_library_plate(base, args.scene, args.key, args.change, note=args.note, refs=args.ref, only=args.only,
                                       library_only=args.library_only, dry_run=args.dry_run, seed=args.seed)
        else:
            res = revise_shot_plate(base, component(args.ep), args.shot, args.role, args.change, note=args.note, refs=args.ref,
                                    dry_run=args.dry_run, seed=args.seed)
    except ValueError as error:
        print(f'错误: {error}', file=sys.stderr)
        return 1
    except Exception as error:  # noqa: BLE001
        print(f'错误: 出图失败 {error}', file=sys.stderr)
        return 1
    print(json.dumps({'revise_shot_plate': {k: v for k, v in res.items() if k not in ('prompt', 'negative')}}, ensure_ascii=False), flush=True)
    errors = [e for s in ([res.get('sync')] + list(res.get('syncs') or [])) if s for e in s.get('errors') or []]
    for e in errors:
        print('VIOLATION', e)
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
