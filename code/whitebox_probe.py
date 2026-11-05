#!/usr/bin/env python3
"""白模宿主实测(2026-11-05,docs/whitebox.md「宿主实测:code/whitebox_probe.py」):碰撞扫掠 / 朝向误差 / 画面实测。

  python code/whitebox_probe.py --project <slug> --ep <ep> collide [grp…] [--step 0.0417] [--allow A:B[@t0-t1]] …
  python code/whitebox_probe.py --project <slug> --ep <ep> facing  [grp…] [--pair A:B[@镜号]] [--tol 3]
  python code/whitebox_probe.py --project <slug> --ep <ep> frame   <grp…> [--shot sh…] [--t 组内秒 …] [--object 场景物件id …] [--scale 0.5]

全部只读:现场编译(含导演台覆盖层)后在内存里量,不写编译产物、不出视频、无生成费用。collide / facing 纯计算;frame 用无头
Chromium 加载预览 / 导出同一渲染器(依赖同 render_whitebox.py --stills)。结果打印 JSON;发现问题只报告、不改退出码,
编译报错或参数错误才非零退出。白模调度做防穿模 / 对话朝向 / 取景实测时用它,不再自写检查脚本。
"""
import json
import sys

from _common import parse_args, reexec_with_host_python  # noqa: E402


def configure(ap):
    ap.add_argument('mode', choices=('collide', 'facing', 'frame'))
    ap.add_argument('groups', nargs='*', help='组号;collide / facing 缺省整集,frame 必填')
    ap.add_argument('--step', type=float, default=None, help='采样步长秒(缺省 1/24)')
    ap.add_argument('--allow', action='append', default=[], help='collide 接触例外 A:B、A:B@t0-t1(组内秒)、A:*;可重复')
    ap.add_argument('--actor-gap', type=float, default=None, help='人物间参考净距 m(缺省 0.20)')
    ap.add_argument('--obstacle-gap', type=float, default=None, help='人物与障碍物参考净距 m(缺省 0.15)')
    ap.add_argument('--pair', action='append', default=[], help='facing 额外检查 A 面向 B:A:B 或 A:B@镜号;可重复')
    ap.add_argument('--tol', type=float, default=None, help='facing 身体朝向误差上限度数(缺省 3)')
    ap.add_argument('--shot', action='append', default=[], help='frame 只量这些镜')
    ap.add_argument('--t', action='append', type=float, default=[], help='frame 指定组内秒(缺省同自检静帧的采样时刻)')
    ap.add_argument('--object', action='append', default=[], help='frame 另量这些场景物件 id(人物 / 群演 / 道具总是量)')
    ap.add_argument('--scale', type=float, default=None, help='frame 渲染分辨率相对项目画幅的比例(缺省 0.5)')


def main():
    args, base = parse_args(__doc__, configure=configure)
    reexec_with_host_python()
    from modules import whitebox_probe as wp
    from modules.whitebox import compile_episode
    episode = compile_episode(base, args.ep)
    known = {g['group_id'] for g in episode['groups']}
    wanted = args.groups or ([] if args.mode == 'frame' else sorted(known, key=[g['group_id'] for g in episode['groups']].index))
    if args.mode == 'frame' and not wanted:
        raise SystemExit('frame 须指定组号')
    errors = [e for e in episode['errors'] if e['group_id'] in wanted]
    unknown = [g for g in wanted if g not in known and not any(e['group_id'] == g for e in errors)]
    errors += [{'group_id': g, 'error': 'Unknown group'} for g in unknown]
    groups = [g for g in episode['groups'] if g['group_id'] in wanted]
    step = args.step or wp.STEP_S
    out = {'mode': args.mode, 'errors': errors}
    if args.mode == 'collide':
        allow = wp.parse_allow(args.allow)
        rows = [{'group_id': g['group_id'], 'violations': wp.collide_group(
                    g, episode['scenes'][g['scene_id']], step=step, allow=allow,
                    actor_gap=args.actor_gap if args.actor_gap is not None else wp.ACTOR_GAP_M,
                    obstacle_gap=args.obstacle_gap if args.obstacle_gap is not None else wp.OBSTACLE_GAP_M)} for g in groups]
        total = sum(len(r['violations']) for r in rows)
        out.update(groups=rows, step_s=round(step, 4), allow=args.allow,
                   text=f"[whitebox_probe collide] {args.project}/{args.ep}: {len(rows)} 组,{total} 段净距不足"
                        + (' -> WARN' if total else ' -> PASS') + '(不含手臂;接触例外用 --allow 记录并在回执写明)')
    elif args.mode == 'facing':
        rows = []
        for g in groups:
            checks, camera = wp.facing_group(base, args.ep, g, episode.get('render') or {}, pairs=args.pair, step=step,
                                             tol=args.tol if args.tol is not None else wp.FACING_TOL_DEG)
            rows.append({'group_id': g['group_id'], 'checks': checks, 'camera_facing': camera})
        bad = sum(1 for r in rows for c in r['checks'] if c.get('ok') is False or c.get('error'))
        out.update(groups=rows, text=f"[whitebox_probe facing] {args.project}/{args.ep}: 声明的朝向 "
                   f"{sum(len(r['checks']) for r in rows)} 项,超差或无法核对 {bad} 项" + (' -> WARN' if bad else ' -> PASS')
                   + '(走位/转身过渡期超差属正常,对照 t_max_s 判断)')
    else:
        rows = []
        for g in groups:
            samples = wp.default_frame_samples(base, args.ep, g, args.shot or None, args.t or None)
            rows.append(wp.frame_probe(base, episode, g['group_id'], samples, objects=args.object,
                                       scale=args.scale if args.scale is not None else wp.PROBE_SCALE))
        out.update(groups=rows, text=f"[whitebox_probe frame] {args.project}/{args.ep}: {sum(len(r['samples']) for r in rows)} 个时刻已量")
    print(json.dumps(out, ensure_ascii=False, indent=1), flush=True)
    return 1 if errors else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as error:
        print(f'whitebox_probe: {error}', file=sys.stderr)
        sys.exit(1)
