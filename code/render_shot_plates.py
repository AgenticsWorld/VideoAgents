#!/usr/bin/env python3
"""分镜背景图生成(shot plates,2026-09-09;规则与数据结构见 modules/shot_plates.py 顶部注释、docs/shot_plates.md)。

流程位置:白模调度(p6-whitebox,只编译)→ 用户签字 H3W-白模确认 → 白模调度导出 camera.mp4(p6-whitebox-export)→ 本脚本(p6-shot-plates)。
本脚本要求所选组的白模视频已导出(assets/whitebox/<ep>/<grp>/manifest.json),否则拒跑——保证背景图只在用户确认白模之后生成。

每镜按运镜分档决定出几张(静态/推拉/摇俯仰 = 镜首一张;横移跟拍/复杂轨迹按位移出镜首 + 镜尾),先查场景母图库
(assets/concepts/scenes/<sid>/plates/;母图制 2026-09-14:同一机位范围(水平距 ≤2 m 且 ≤主体距离 80%、机高差 ≤0.5 m)、同方案且本镜视锥落在母图画幅内 → 直接引用该广角母图整图,
不按本镜焦距裁窄(裁窄后信息太少视频模型会自行发挥),不出图),缺母图的机位才出一张广角母图(垂直视场 ≥55°、长边 2880 且面积 ≤4.6 MP,控制台默认图像模型)
入库并写集索引 directing/<ep>/shot_plates.json;最后自动跑 code/sync_shot_plates.py --write 把背景图接进已有组 prompt 的 refs。
窄焦距直接按全景重投影出图的反例见 docs/shot_plates.md「母图制」(参考图只剩一块纹理时模型把整间屋重造)。

全景制(2026-09-10,docs/scene_panos.md):新图一律由场景全景按本镜机位重投影后二次生成——脚本先保证场景全景齐备
(modules/scene_panos.py:按本集机位规划少数锚点 → 白模深度全景 → 图像模型出 2:1 全景,每个光照方案一张;可单独用
code/render_scene_panos.py 预跑/改锚点),再把全景重投影成 <key>.pano.jpg 作 [Image 1] 出图;不再给白模帧 / 俯视图作参考图
(多张背景图互不一致的根因)。当前图像模型不支持 2:1 全景时脚本退出码 2 并打印 [pano_unsupported],一张都不出——
Agent 须原文上报,请用户到控制台「🎨 生成模型」换图像模型后重跑,不得自行换模型或绕过。
背景图模式(2026-09-22,项目输出设置「背景图模式」,白模开启时显示;场景预览页「分镜背景图」板块可按场景覆盖):
  全景图(默认)= 上述全景制;世界模型 = 用户在场景预览页自选锚点创建全景图 → 基于它生成世界模型(World Labs Marble),
  本脚本在 world 里按母图机位截图作 [Image 1] 二次生成。世界模型模式的场景没有 world 时退出码 4 并打印 [world_missing],
  一张都不出——Agent 须原文上报,请用户到场景预览页该场景「🌍 世界模型」板块生成(计费)或改回全景图模式;不得自行生成世界模型。
  九宫格(2026-09-25;2026-10-04 起出图方式默认「中心点九宫格」)= 不出全景/不用世界模型/不出母图:每场景每光照方案以俯视图为参考出一张
  3x3 宫格 <方案>_grid9.png,拆成 9 张背景图入库(pano_ref.kind=grid9),每镜按白模机位自动从九格里选最合适的一格(朝向/距离/机高/俯仰打分,
  日志逐镜打印选格依据)。九格共用一个站位、原地转头:站位 = 本集该场景各镜机位的水平中位点,机高 = 机高中位数夹在 0.9–1.6 m,
  第 1–8 格每 45° 一格平视,第 9 格朝仰拍镜最集中的方向按其仰角中位数仰拍(pano_ref.layout=center;参考图 = 标点俯视图 <方案>_grid9.plan.jpg + 版式模板)。
  库里已有该方案九格时直接复用不重出;--grid-layout views 改回原方案(按 layout.json#views 的 9 个语义机位,缺 views/地标抛 Grid9LayoutError)。
  **最近格不合适**(朝向差 >30° / 俯仰差 >20° / 机位距 >6 m / 机高档差 ≥2 任一)的镜按背景图模式分两种处理:
    grid = 九宫格自动补图(默认):自动以俯视图 + 九宫格整图为参考按本镜机位单独出一张补图(pano_ref.kind=grid9_fallback,相近机位复用;
      日志「格子不合适(…)」逐镜给原因),--max-new 同样限补图张数;--no-grid-fallback 关闭;
    grid_manual = 九宫格手动补图(2026-10-04):不自动出图。库里有对本镜合适的手工截图(用户在场景预览页全景 360° 视窗 / 世界模型视窗
      「💾 背景图」截取,库条目 manual=True)就选用(集索引 reuse=grid9_manual);没有 → 暂用最近格占位、集索引记 view.manual_needed,
      脚本退出码 5 并打印 [manual_plate_needed] 列出待补的镜——Agent 须原文上报,请用户去截图(或在分镜预览页对该镜「换图」手选)后重跑;
      不得自行出图、不得改背景图模式绕过。--status 对这些镜报 manual_needed(FAIL)。
库里非母图的旧图(2026-09-10 前白模帧直出、2026-09-14 前逐镜全景直出,legacy)不再被新决策复用;--status 列出仍指向 legacy 图的镜
(WARN 不算 FAIL);--repano 把这些镜整体按母图制重出(有费用,仅用户明确要求时用)。

用法:
  python code/render_shot_plates.py --project <slug> --ep ep01                 # 全集
  python code/render_shot_plates.py --project <slug> --ep ep01 grp002 sh010    # 只处理指定组/镜
  python code/render_shot_plates.py --project <slug> --ep ep01 --dry-run       # 只算决策与提示词、渲白模帧,不调图像模型
  python code/render_shot_plates.py --project <slug> --ep ep01 --force         # 无视集索引里的现有记录重新决策(库图仍复用)
  python code/render_shot_plates.py --project <slug> --ep ep01 --max-new 6   # 分批:每次最多新出 6 张即返回(退出码 3=还有待出),前台循环直到 0
  python code/render_shot_plates.py --project <slug> --ep ep01 --status      # 验收机检 shot_plates_complete:逐镜覆盖状态,不齐退出码 1
  python code/render_shot_plates.py --project <slug> --ep ep01 grp027 --repano  # 把仍指向 legacy(非全景制)图的镜重出(用户明确要求时)
  python code/render_shot_plates.py --project <slug> --ep ep01 --no-grid-fallback  # 九宫格模式关掉自动补图(只选格;加 --force 才重出宫格本身)
  可选 --sun west:把太阳罗盘方位换算成相对机位的方向写进提示词;--seed N:新出图固定种子。
  退出码:0 完成;1 出错/机检不过(含 [pano_sparse]:手动加的悬空稀疏锚点待出全景,未出图,原文上报用户裁决);2 图像模型不支持全景(请用户换模型);3 已达 --max-new 上限还有待出;4 世界模型模式的场景尚未生成世界模型(请用户生成);
        5 九宫格手动补图模式下有镜待用户手工截取背景图(其余镜已落盘)。

纪律(2026-09-09,前科 dzg6 p6-shot-plates-ep01-s01s02:Agent 把本脚本丢后台就结单,进程随之被杀,16 镜一张没出):
  本脚本必须在派发任务内前台同步跑完;禁止 nohup/&/后台派发;每出一张即打印 saved: 并按镜落盘索引与库,
  中途被杀不丢已出图,重跑自动续;长集用 --max-new 分批,退出码 3 表示还有待出,继续在前台跑;结单前跑 --status 作验收依据。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args, reexec_with_host_python, spatial_blocking_enabled  # noqa: E402
from modules.scene_panos import PanoProjectionError, PanoSparseError, PanoUnsupported  # noqa: E402
from modules.shot_plates import WorldMissing, run_episode, status_episode, sync_episode  # noqa: E402
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
        ap.add_argument('--repano', action='store_true', help='集索引里仍指向 legacy(非全景制)库图的镜视为需重做,按全景制重出(有费用,仅用户明确要求时)')
        ap.add_argument('--grid-layout', choices=('center', 'views'), default='center',
                        help='九宫格出图方式(仅在该场景该方案还没有九宫格、或显式重出宫格时生效):center(默认,2026-10-04)= 同一站位 8 向 + 1 格仰拍,'
                        '站位/机高按本集机位推;views = 原方案,按 layout.json#views 的 9 个语义机位')
        ap.add_argument('--grid-fallback', action=argparse.BooleanOptionalAction, default=True,
                        help='九宫格模式补图(2026-09-26 起为默认流程,--no-grid-fallback 关闭):最近格朝向/俯仰/距离/机高任一分量超限的镜,'
                        '改以俯视图 + 九宫格整图为参考按本镜机位单独出图(相近机位复用);索引里已用不合适格子的镜也会重新决策(不动已出宫格)。'
                        '补图开着时 --force 只重出目标镜的补图,要重出整张宫格须显式 --no-grid-fallback --force(覆盖该方案全部 9 格,影响同场景其它镜)')
    args, base = parse_args(__doc__, configure=configure)
    reexec_with_host_python()   # 缺 Playwright 时换宿主解释器重跑(_common)
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
        if st.get('legacy_shots'):
            print(f"[shot_plates_complete] WARN: {len(st['legacy_shots'])} 镜仍用非母图制背景图(legacy,2026-09-14 前逐镜直出/白模帧直出):"
                  f"{st['legacy_shots']};按母图制重出请用户确认后跑 --repano", flush=True)
        return 1 if st['problems'] else 0
    try:
        stats = run_episode(base, ep, targets or None, dry_run=args.dry_run, force=args.force, sun=args.sun, seed=args.seed,
                            max_new=args.max_new, repano=args.repano, grid_fallback=args.grid_fallback, grid_layout=args.grid_layout)
    except PanoUnsupported as error:
        print(f"[pano_unsupported] {error}", file=sys.stderr, flush=True)
        print(json.dumps({'shot_plates': {'blocked': 'pano_unsupported', 'detail': str(error)}}, ensure_ascii=False), flush=True)
        return 2
    except PanoProjectionError as error:
        print(f"[pano_projection_fail] {error}", file=sys.stderr, flush=True)
        print(json.dumps({'shot_plates': {'blocked': 'pano_projection_fail', 'detail': str(error)}}, ensure_ascii=False), flush=True)
        return 3
    except PanoSparseError as error:   # 手动加的悬空稀疏锚点待出全景:不花钱,原文上报用户(确认后 render_scene_panos.py --redo <锚点> --allow-sparse)
        print(f"[pano_sparse] {error}", file=sys.stderr, flush=True)
        print(json.dumps({'shot_plates': {'blocked': 'pano_sparse', 'detail': str(error)}}, ensure_ascii=False), flush=True)
        return 1
    except WorldMissing as error:
        print(f"[world_missing] {error}", file=sys.stderr, flush=True)
        print(json.dumps({'shot_plates': {'blocked': 'world_missing', 'scenes': error.scenes, 'detail': str(error)}}, ensure_ascii=False), flush=True)
        return 4
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
    if stats.get('manual_needed') and not args.dry_run:
        # 九宫格手动补图(2026-10-04):这些镜没有合适的格子、库里也没有合适的手工截图,索引里暂用最近格占位。不自动出图,由用户补
        print(f"[manual_plate_needed] {args.project}/{ep}: {len(stats['manual_needed'])} 张背景图待用户手动补图:{', '.join(stats['manual_needed'])};"
              "请用户在场景预览页对应场景的全景 360° 视窗或世界模型视窗里按本镜机位用「💾 背景图」截取(或在分镜预览页对该镜「换图」手选),"
              "之后重跑本命令;Agent 不得自行出图、不得改背景图模式绕过", flush=True)
        return 5
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:  # noqa: BLE001
        print(f'shot_plates: {error}', file=sys.stderr)
        sys.exit(1)
