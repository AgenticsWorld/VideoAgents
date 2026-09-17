#!/usr/bin/env python3
"""场景正/反向双图(scene pair plates,实验 v1;规则见 modules/scene_pair_plates.py 顶部注释、docs/scene_pair_plates.md)。

正向:站在入口内一步朝屋内看;反向:以正向图为母版,站在屋子远端朝入口回望,补全门窗那面墙。两张整图替换组 prompt 里的
俯视动线图 + 九宫格,每个 Shot 在正文里点名用哪一张,人物位置/镜头角度靠文字。不依赖白模,只用 layout.json。
实验链路,不接进工作流;--write-prompt / --run-video 的产物落 --out 目录,不动 assets/prompts 与 assets/clips。

用法:
  python code/render_scene_pair_plates.py --project offer --ep ep02 --scene SCN-0006 --dry-run             # 站位 + 标点图 + 提示词,不出图
  python code/render_scene_pair_plates.py --project offer --ep ep02 --scene SCN-0006 --seed N --sun south-west   # 出正向图,再以正向图为母版出反向图
  python code/render_scene_pair_plates.py --project offer --ep ep02 --scene SCN-0006 --only reverse --force --no-mirror-ref   # 反向图照抄正向构图时,只挂标点图重出
  python code/render_scene_pair_plates.py --project offer --ep ep02 --scene SCN-0006 --assign grp003        # 逐镜选图 → directing/<ep>/pair_plates.json
  python code/render_scene_pair_plates.py --project offer --ep ep02 --scene SCN-0006 --write-prompt grp003 --out qa/reports/scene_pair_spike/grp003
  python code/render_scene_pair_plates.py --project offer --ep ep02 --scene SCN-0006 --run-video grp003 --out qa/reports/scene_pair_spike/grp003
可选:--dims 8,7(图幅折米,缺省读 layout.json#orientation.scale_m)、--lens 12、--size 2560x1440、--height 1.5、--scheme SCN-0006-L01、
     --front-at u,v / --front-look u,v|地标id / --reverse-at u,v / --reverse-look u,v|地标id、--tiles ep02-sh006=4,ep02-sh008=1、
     --resolution 480p --aspect 16:9(出片)。
退出码:0 完成;2 dry-run;3 有图未出。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args  # noqa: E402
from modules import scene_pair_plates as sp  # noqa: E402
from modules.whitebox import component  # noqa: E402


def _xy(v):
    if v is None:
        return None
    if ',' in v:
        a, b = v.split(',', 1)
        return [float(a), float(b)]
    return v


def main():
    def configure(ap):
        ap.add_argument('--scene', required=True)
        ap.add_argument('--dry-run', action='store_true')
        ap.add_argument('--force', action='store_true', help='已有的图也重出(旧图改名 .prev-*.png)')
        ap.add_argument('--force-geometry', action='store_true', help='重画标点图')
        ap.add_argument('--no-mirror-ref', action='store_true', help='反向图不挂正向成图,只挂标点图')
        ap.add_argument('--only', default=None, help='只出 front 或 reverse')
        ap.add_argument('--dims', default=None, help='图幅折米 x,y,如 8,7')
        ap.add_argument('--lens', type=float, default=sp.DEFAULT_LENS_MM)
        ap.add_argument('--size', default=sp.PLATE_SIZE)
        ap.add_argument('--height', type=float, default=sp.STATION_HEIGHT_M)
        ap.add_argument('--scheme', default=None)
        ap.add_argument('--time-of-day', default=None)
        ap.add_argument('--sun', default=None, help='太阳方位 16 向罗盘词,如 south-west')
        ap.add_argument('--seed', type=int, default=None)
        ap.add_argument('--front-at', default=None); ap.add_argument('--front-look', default=None)
        ap.add_argument('--reverse-at', default=None); ap.add_argument('--reverse-look', default=None)
        ap.add_argument('--assign', default=None, metavar='GRP', help='只做逐镜选图')
        ap.add_argument('--write-prompt', default=None, metavar='GRP', help='生成 spike prompt.json 到 --out')
        ap.add_argument('--with-map', action='store_true', help='B 路线:主视角一张 + 标注俯视图(--out 缺省 qa/reports/scene_pair_spike_map/<grp>)')
        ap.add_argument('--both-plates', action='store_true', help='C 路线:与 --with-map 连用,正反两张 + 俯视图(--out 缺省 qa/reports/scene_pair_spike_c/<grp>)')
        ap.add_argument('--draw-map', nargs='*', default=None, metavar='GRP', help='只画标注俯视图 pair/blocking_<grp>.jpg')
        ap.add_argument('--compare', nargs='*', default=None, metavar='GRP', help='A/B 两路线成片同秒抽帧对照表')
        ap.add_argument('--run-video', default=None, metavar='GRP', help='按 --out/prompt.json 出片到 --out')
        ap.add_argument('--out', default=None, help='spike 产物目录(项目根相对),缺省 qa/reports/scene_pair_spike/<grp>')
        ap.add_argument('--tiles', default=None, help='镜→九格视角号覆盖,如 ep02-sh006=4,ep02-sh008=1')
        ap.add_argument('--resolution', default='480p'); ap.add_argument('--aspect', default='16:9')
    args, base = parse_args(__doc__, configure=configure)
    sid, ep = component(args.scene), component(args.ep)
    tiles = dict(kv.split('=', 1) for kv in args.tiles.split(',')) if args.tiles else None
    gid = args.assign or args.write_prompt or args.run_video
    spike_root = 'qa/reports/scene_pair_spike_c' if (args.with_map and args.both_plates) else 'qa/reports/scene_pair_spike_map' if args.with_map else 'qa/reports/scene_pair_spike'
    out_dir = base / (args.out or f'{spike_root}/{gid}') if gid else None

    if args.draw_map is not None:
        for g in args.draw_map:
            sp.draw_group_map(base, sid, component(g))
        return 0
    if args.compare is not None:
        for g in args.compare:
            g = component(g)
            sp.compare_sheets(base, g, base / 'qa/reports/scene_pair_spike' / g, base / 'qa/reports/scene_pair_spike_map' / g,
                              base / 'qa/reports/scene_pair_spike_map' / f'compare_{g}.png')
        return 0
    if args.run_video:
        sp.run_video(base, component(args.run_video), out_dir, resolution=args.resolution, aspect=args.aspect)
        return 0
    if args.write_prompt:
        _, warns = sp.write_prompt(base, sid, ep, component(args.write_prompt), out_dir, tiles=tiles, with_map=args.with_map, both_plates=args.both_plates)
        return 0
    if args.assign:
        sp.assign_group(base, sid, ep, component(args.assign), tiles=tiles)
        return 0

    dims = [float(v) for v in args.dims.split(',')] if args.dims else None
    only = [x.strip() for x in args.only.split(',')] if args.only else None
    st_opts = {'front_at': _xy(args.front_at), 'front_look': _xy(args.front_look), 'reverse_at': _xy(args.reverse_at), 'reverse_look': _xy(args.reverse_look)}
    idx = sp.run(base, sid, only=only, seed=args.seed, dry_run=args.dry_run, force=args.force, mirror_ref=not args.no_mirror_ref,
                 dims=dims, lens_mm=args.lens, size=args.size, height=args.height, scheme_id=args.scheme, time_of_day=args.time_of_day,
                 sun=args.sun, stations_opts=st_opts, force_geometry=args.force_geometry)
    if args.dry_run:
        return 2
    missing = [r for r in sp.ROLES if not (sp.pair_dir(base, sid) / f'{r}.png').is_file()]
    if missing:
        print(f'缺图:{", ".join(missing)}')
        return 3
    return 0


if __name__ == '__main__':
    sys.exit(main())
