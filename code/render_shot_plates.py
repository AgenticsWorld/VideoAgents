#!/usr/bin/env python3
"""分镜背景图生成(shot plates,2026-09-09;规则与数据结构见 modules/shot_plates.py 顶部注释、docs/shot_plates.md)。

流程位置:白模调度(p6-whitebox,只编译)→ 用户签字 H3W-白模确认 → 导出 camera.mp4(p6-whitebox-export)→ 本脚本(p6-shot-plates)。
本脚本要求所选组的白模视频已导出(assets/whitebox/<ep>/<grp>/manifest.json),否则拒跑——保证背景图只在用户确认白模之后生成。

每镜按运镜分档决定出几张(静态/推拉/摇俯仰 = 镜首一张;横移跟拍/复杂轨迹按位移出镜首 + 镜尾),先查场景背景图库
(assets/concepts/scenes/<sid>/plates/,按机位指纹容差复用、同轴更宽的库图裁切复用),缺的才渲白模干净帧 + 场景俯视图出新图
(长边 1920,控制台默认图像模型),入库并写集索引 directing/<ep>/shot_plates.json;最后自动跑 code/sync_shot_plates.py --write
把背景图接进已有组 prompt 的 refs。

用法:
  python code/render_shot_plates.py --project <slug> --ep ep01                 # 全集
  python code/render_shot_plates.py --project <slug> --ep ep01 grp002 sh010    # 只处理指定组/镜
  python code/render_shot_plates.py --project <slug> --ep ep01 --dry-run       # 只算决策与提示词、渲白模帧,不调图像模型
  python code/render_shot_plates.py --project <slug> --ep ep01 --force         # 无视集索引里的现有记录重新决策(库图仍复用)
  python code/render_shot_plates.py --project <slug> --ep ep01 --max-new 6   # 分批:每次最多新出 6 张即返回(退出码 3=还有待出),前台循环直到 0
  python code/render_shot_plates.py --project <slug> --ep ep01 --status      # 验收机检 shot_plates_complete:逐镜覆盖状态,不齐退出码 1
  可选 --sun west:把太阳罗盘方位换算成相对机位的方向写进提示词;--seed N:新出图固定种子。

纪律(2026-09-09,前科 dzg6 p6-shot-plates-ep01-s01s02:Agent 把本脚本丢后台就结单,进程随之被杀,16 镜一张没出):
  本脚本必须在派发任务内前台同步跑完;禁止 nohup/&/后台派发;每出一张即打印 saved: 并按镜落盘索引与库,
  中途被杀不丢已出图,重跑自动续;长集用 --max-new 分批,退出码 3 表示还有待出,继续在前台跑;结单前跑 --status 作验收依据。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args, spatial_blocking_enabled  # noqa: E402
from modules.shot_plates import run_episode, status_episode, sync_episode  # noqa: E402
from modules.whitebox import component, read  # noqa: E402


def main():
    def configure(ap):
        ap.add_argument('targets', nargs='*', help='组号 grpNNN 或镜号 shNNN,缺省全集')
        ap.add_argument('--dry-run', action='store_true')
        ap.add_argument('--force', action='store_true')
        ap.add_argument('--sun', default='')
        ap.add_argument('--seed', type=int, default=None)
        ap.add_argument('--allow-unexported', action='store_true', help='跳过「白模视频已导出」前置检查(仅调试)')
        ap.add_argument('--max-new', type=int, default=None, help='本次最多新出 N 张后停止(索引已按镜落盘);还有待出图时退出码 3,Agent 在前台循环再跑直到 0')
        ap.add_argument('--status', action='store_true', help='机检 shot_plates_complete:逐镜覆盖状态(ok/partial/missing/stale),有问题退出码 1;验收以此为准')
    args, base = parse_args(__doc__, configure=configure)
    ep = component(args.ep)
    if not spatial_blocking_enabled(base):
        print(f"[shot_plates] {args.project}: skipped: spatial_blocking off(项目输出设置「人物精确空间位置」已关闭,不出分镜背景图)")
        return 0
    episode = read(base/'directing'/ep/'whitebox'/'episode.json', {})
    if not episode:
        print('缺少 directing/<ep>/whitebox/episode.json,请先 python code/render_whitebox.py --compile-only', file=sys.stderr)
        return 1
    targets = set(args.targets)
    for t in targets:
        component(t)
    groups = [g['group_id'] for g in episode.get('groups', [])
              if not targets or g['group_id'] in targets or any(c['shot_id'] in targets for c in g['cameras'])]
    if not groups:
        print('没有匹配的组/镜', file=sys.stderr)
        return 1
    if not args.allow_unexported:
        unexported = [g for g in groups if not (base/'assets/whitebox'/ep/g/'manifest.json').is_file()]
        if unexported:
            print(f"以下组的白模视频尚未导出,分镜背景图须在用户签字「H3W-白模确认」并导出 camera.mp4 之后生成:{unexported}"
                  "(python code/render_whitebox.py --project <slug> --ep <ep>)", file=sys.stderr)
            return 1
    if args.status:
        st = status_episode(base, ep, targets or None)
        print(json.dumps({'shot_plates_complete': st}, ensure_ascii=False), flush=True)
        print(f"[shot_plates_complete] {args.project}/{ep}: {st['shots_ok']}/{st['shots_total']} 镜齐全,问题 {len(st['problems'])} 镜 "
              f"-> {'FAIL' if st['problems'] else 'PASS'}", flush=True)
        return 1 if st['problems'] else 0
    stats = run_episode(base, ep, targets or None, dry_run=args.dry_run, force=args.force, sun=args.sun, seed=args.seed,
                        max_new=args.max_new)
    print(json.dumps({'shot_plates': stats}, ensure_ascii=False), flush=True)
    if not args.dry_run:
        sync = sync_episode(base, ep, groups, write=True)
        print(json.dumps({'shot_plate_refs': {'updated_prompts': sync['updated_prompts'],
                          'errors': sync['errors'], 'warnings': sync['warnings']}}, ensure_ascii=False), flush=True)
    if stats['errors']:
        return 1
    if stats.get('pending_new'):
        print(f"[shot_plates] 本次已达 --max-new 上限,仍有 {stats['pending_new']} 张待出:请在前台再次运行同一命令直到退出码 0", flush=True)
        return 3
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:  # noqa: BLE001
        print(f'shot_plates: {error}', file=sys.stderr)
        sys.exit(1)
