#!/usr/bin/env python3
"""四方向平视背景图(cardinal plates,实验 v5;规则见 modules/cardinal_plates.py 顶部注释、docs/cardinal_plates.md)。

视点固定在场景俯视图正中心(落在实体内则吸附到最近空点),朝东/南/西/北用 12 mm 等效直线透视超广角各出一张不倾斜的平视空场景图,
四张整图直接作场景级分镜背景图(不按镜裁切);参考图只有标点俯视图一张,几何/内容靠文字;再按白模机位朝向/运镜给每镜选 1~多张。
实验链路,不接进组 prompt refs。

用法:
  python code/render_cardinal_plates.py --project dzg6 --ep ep01 --scene SCN-0075 --dry-run          # 视点 + 标点图 + 提示词,不出图
  python code/render_cardinal_plates.py --project dzg6 --ep ep01 --scene SCN-0075 --seed 20260912    # 出四张(缺哪张出哪张)+ 逐镜选图
  python code/render_cardinal_plates.py --project dzg6 --ep ep01 --scene SCN-0075 --only north --force   # 只重出北图
  python code/render_cardinal_plates.py --project dzg6 --ep ep01 --scene SCN-0075 --assign-only      # 只重算逐镜选图
  可选:--at=-3.5,4(改视点,白模米制,负数用 = 写法;缺省俯视图正中心)、--height 1.5、--lens 12、--size 2560x1440、
       --sun west-north-west(16 向罗盘)、--groups grp005 grp006 / --scene-no S02(只按这些组/该场次的机位定站点/选图)、
       --force-geometry(重算视点/重画标点图)。
产物:assets/concepts/scenes/<sid>/cardinal/(index.json、<dir>.png、viewpoint_<dir>.jpg、review.html)
     directing/<ep>/cardinal_plates.json
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args  # noqa: E402
from modules import cardinal_plates as cp  # noqa: E402
from modules.whitebox import component  # noqa: E402


def main():
    def configure(ap):
        ap.add_argument('--scene', required=True)
        ap.add_argument('--dry-run', action='store_true')
        ap.add_argument('--force', action='store_true', help='已有的方向图也重出(旧图改名 .prev-*.png)')
        ap.add_argument('--force-geometry', action='store_true', help='重算视点/重画标点图')
        ap.add_argument('--assign-only', action='store_true', help='不出图,只按现有四张重算逐镜选图')
        ap.add_argument('--only', default=None, help='只出这些方向,逗号分隔,如 north,south')
        ap.add_argument('--at', default=None, help='视点 x,z(白模米制;负数写 --at=-3,4);缺省俯视图正中心')
        ap.add_argument('--height', type=float, default=None, help='视点镜头高度 m(缺省机高中位数,≥1.4)')
        ap.add_argument('--lens', type=float, default=cp.DEFAULT_LENS_MM, help='等效焦距 mm,缺省 12')
        ap.add_argument('--size', default=cp.PLATE_SIZE, help=f'成图尺寸,缺省 {cp.PLATE_SIZE}')
        ap.add_argument('--scheme', default=None, help='光照方案 id,缺省 lighting.json 第一个')
        ap.add_argument('--sun', default=None, help='太阳方位 16 向罗盘词,如 west-north-west')
        ap.add_argument('--seed', type=int, default=None)
        ap.add_argument('--groups', nargs='*', default=None)
        ap.add_argument('--scene-no', default=None, help='只按该场次的机位定镜高/选图,如 S02')
    args, base = parse_args(__doc__, configure=configure)
    sid, ep = component(args.scene), component(args.ep)
    at = [float(v) for v in args.at.split(',')] if args.at else None
    only = [d.strip() for d in args.only.split(',')] if args.only else None
    kw = dict(at=at, height=args.height, lens_mm=args.lens, size=args.size, scheme_id=args.scheme, sun=args.sun,
              groups=args.groups, scene_no=args.scene_no, force_geometry=args.force_geometry)
    if not args.assign_only:
        cp.run(base, sid, ep, only=only, seed=args.seed, dry_run=args.dry_run, force=args.force, **kw)
    doc = cp.assign_episode(base, sid, ep, args.groups, args.scene_no)
    cp.write_review(base, sid, ep)
    missing = sorted({p['dir'] for s in doc['shots'].values() if s['scene_id'] == sid for p in s['picks'] if p['missing']})
    if missing:
        print(f'缺图:{", ".join(missing)}(dry-run 或未出);逐镜选图已按方向记录')
        return 3 if not args.dry_run else 0
    return 0


if __name__ == '__main__':
    sys.exit(main())
