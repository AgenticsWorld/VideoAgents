#!/usr/bin/env python3
"""场景全景锚点(scene panos,2026-09-10;规则见 modules/scene_panos.py 顶部注释、docs/scene_panos.md)。

按场景在白模内少数锚点出 360° 等距柱状全景(每个光照方案一张),分镜背景图(code/render_shot_plates.py)一律由全景按本镜机位
重投影后二次生成。render_shot_plates.py 会自动保证全景齐备,本脚本用于:预跑/看锚点规划、手动指定锚点、按方案补全景、
查状态。锚点数量由机位覆盖决定(贪心集合覆盖),不按分镜数;同方案第二个锚点起链式补洞、同锚点第二个方案起保结构重打光。

用法:
  python code/render_scene_panos.py --project <slug> --scene SCN-0002 --dry-run     # 规划锚点 + 渲白模全景,不调图像模型
  python code/render_scene_panos.py --project <slug> --scene SCN-0002               # 出缺的全景(各集机位所用光照方案)
  python code/render_scene_panos.py --project <slug> --scene SCN-0002 --ep ep01     # 只按 ep01 的机位规划/出图
  python code/render_scene_panos.py --project <slug> --scene SCN-0002 --anchor -8,5 --force   # 手动加锚点(x,z[,yaw°],锁定)并全部重出
  python code/render_scene_panos.py --project <slug> --scene SCN-0002 --anchor -8,5 --only-new --scheme L1   # 只加锚点并只出它这一张(预览页「创建全景图」走这条;不规划、其它锚点不动;没有机位也可)
  python code/render_scene_panos.py --project <slug> --scene SCN-0002 --replan --force        # 重新规划(保留锁定锚点)并重出
  python code/render_scene_panos.py --project <slug> --scene SCN-0002 --redo A2 A3  # 只重出这两个锚点的全景(其余不动,链式参考其它已成全景)
  python code/render_scene_panos.py --project <slug> --scene SCN-0002 --status      # 机检 scene_panos_ready:每 (锚点, 方案) 是否齐
  可选 --indoor / --outdoor 覆盖室内外判定(室内渲全景补天花板);--seed N 固定种子。

退出码:0 完成;1 出错;2 当前图像模型不支持 2:1 全景(打印 [pano_unsupported],请用户到控制台「🎨 生成模型」换图像模型,
Agent 不得自行换模型);3 成图不是等距柱状投影(打印 [pano_projection_fail],成图已改名 .rejected-projection-*,本批停下;
重出 --only <锚点>,次数计入用户设定的重跑次数,用尽上报用户)。全景与分镜背景图一样在派发任务内前台跑完,禁止丢后台。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args, reexec_with_host_python, spatial_blocking_enabled  # noqa: E402
from modules import scene_panos as sp  # noqa: E402
from modules.whitebox import component  # noqa: E402


def main():
    def configure(ap):
        ap.add_argument('--scene', required=True, help='场景 id,如 SCN-0002')
        ap.add_argument('--dry-run', action='store_true')
        ap.add_argument('--force', action='store_true', help='重渲白模全景并重出全部方案全景(旧全景作废)')
        ap.add_argument('--replan', action='store_true', help='重新规划锚点(locked 锚点保留)')
        ap.add_argument('--anchor', action='append', default=[], help='手动锚点 x,z[,yaw°](白模米制坐标,锁定;可多次)')
        ap.add_argument('--only-new', action='store_true', help='配合 --anchor:不重新规划,只给本次新加的锚点出全景(其它锚点/背景图不动)')
        ap.add_argument('--scheme', default=None, help='只出这个光照方案(slug,见 --status 的 schemes);--only-new 时缺省取机位在用的第一个方案')
        ap.add_argument('--redo', nargs='+', default=None, help='只作废并重出这些锚点的全景(如 --redo A2 A3;旧图改名 .redo-<时间>.png 保留)')
        ap.add_argument('--indoor', action='store_true')
        ap.add_argument('--outdoor', action='store_true')
        ap.add_argument('--seed', type=int, default=None)
        ap.add_argument('--status', action='store_true', help='机检 scene_panos_ready:各 (锚点, 光照方案) 全景是否齐,缺则退出码 1')
    args, base = parse_args(__doc__, configure=configure)
    reexec_with_host_python()   # 缺 Playwright 时换宿主解释器重跑(_common)
    sid = component(args.scene)
    if not spatial_blocking_enabled(base):
        print(f"[scene_panos] {args.project}: skipped: spatial_blocking off(项目输出设置「人物精确空间位置」已关闭)")
        return 0
    eps = [component(args.ep)] if args.ep and '--ep' in sys.argv else None
    cams = sp.scene_cameras(base, sid, eps)
    if not cams and not (args.anchor and args.only_new):
        print(f'{sid}: 没有白模机位(先 python code/render_whitebox.py --project {args.project} --ep <ep> --compile-only)', file=sys.stderr)
        return 1
    idx = sp.load_index(base, sid)
    only = None
    schemes = None
    if args.scheme:
        opts = {o['scheme']: o for o in sp.scene_scheme_options(base, sid, cams)}
        if args.scheme not in opts:
            print(f'--scheme {args.scheme} 不在本场景方案里:{sorted(opts)}', file=sys.stderr)
            return 1
        schemes = {args.scheme: opts[args.scheme].get('time_of_day')}
    if args.anchor and args.only_new:
        # 预览页「创建全景图」:只加锚点、只出它这一张;不规划、不动其它锚点
        only = []
        for spec in args.anchor:
            parts = [float(v) for v in spec.split(',')]
            if len(parts) < 2:
                print(f'--anchor 须为 x,z[,yaw]:{spec}', file=sys.stderr)
                return 1
            a = sp.add_manual_anchor(base, sid, parts[0], parts[1], parts[2] if len(parts) > 2 else 0.0, cameras=cams)
            only.append(a['anchor_id'])
            print(f"  + {a['anchor_id']} (manual, locked) 中心 x={a['position'][0]} z={a['position'][2]} 高 {a['position'][1]} m yaw {a['yaw_deg']}° 服务 {len(a['serves'])} 机位", flush=True)
        if schemes is None:
            o = sp.scene_scheme_options(base, sid, cams)[0]
            schemes = {o['scheme']: o.get('time_of_day')}
    elif args.anchor:
        height = sp.default_anchor_height(cams)
        for spec in args.anchor:
            parts = [float(v) for v in spec.split(',')]
            if len(parts) < 2:
                print(f'--anchor 须为 x,z[,yaw]:{spec}', file=sys.stderr)
                return 1
            n = len(idx['anchors']) + 1
            used = {a['anchor_id'] for a in idx['anchors']}
            while f'A{n}' in used:
                n += 1
            idx['anchors'].append({'anchor_id': f'A{n}', 'position': [parts[0], height, parts[1]], 'yaw_deg': parts[2] if len(parts) > 2 else 0.0,
                                   'source': 'manual', 'locked': True, 'serves': [], 'panos': {}})
        sp.save_index(base, sid, idx)
        args.replan = True
    indoor = True if args.indoor else False if args.outdoor else None
    if args.status:
        idx = sp.load_index(base, sid)
        schemes = sorted({c['scheme'] for c in cams})
        missing = [f"{a['anchor_id']}/{s}" for a in idx['anchors'] for s in schemes if not sp.pano_ready(base, sid, a, s)]
        served = {k for a in idx['anchors'] for k in a.get('serves', [])}
        unserved = [sp._cam_key(c) for c in cams if sp._cam_key(c) not in served]
        st = {'scene_id': sid, 'anchors': [a['anchor_id'] for a in idx['anchors']], 'schemes': schemes, 'missing': missing,
              'unserved_cameras': unserved, 'blocked': idx.get('blocked')}
        print(json.dumps({'scene_panos_ready': st}, ensure_ascii=False), flush=True)
        ok = idx['anchors'] and not missing and not idx.get('blocked')
        print(f"[scene_panos_ready] {args.project}/{sid}: 锚点 {len(idx['anchors'])},方案 {schemes},缺 {len(missing)},未覆盖机位 {len(unserved)} -> {'PASS' if ok else 'FAIL'}", flush=True)
        return 0 if ok else 1
    try:
        stats = sp.ensure_scene_panos(base, sid, cameras=cams, schemes=schemes, dry_run=args.dry_run, force=args.force, replan=args.replan, indoor=indoor,
                                      seed=args.seed, redo=args.redo, only=only)
    except sp.PanoUnsupported as error:
        print(f"[pano_unsupported] {error}", file=sys.stderr, flush=True)
        return 2
    except sp.PanoProjectionError as error:
        print(f"[pano_projection_fail] {error}", file=sys.stderr, flush=True)
        return 3
    idx = sp.load_index(base, sid)
    for a in idx['anchors']:
        p = a['position']
        print(f"  {a['anchor_id']} ({a.get('source')}{', locked' if a.get('locked') else ''}) 中心 x={p[0]} z={p[2]} 高 {p[1]} m yaw {a.get('yaw_deg', 0)}° "
              f"服务 {len(a.get('serves', []))} 机位;全景 {sorted(a.get('panos', {}))}")
    print(json.dumps({'scene_panos': stats}, ensure_ascii=False), flush=True)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:  # noqa: BLE001
        print(f'scene_panos: {error}', file=sys.stderr)
        sys.exit(1)
