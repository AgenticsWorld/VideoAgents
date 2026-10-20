#!/usr/bin/env python3
"""HunyuanWorld 世界模型 CLI(2026-10-10;规则见 modules/hunyuan_world.py 顶部注释、docs/hunyuan_world.md)。

按场景全景,经远程 GPU 主机 worker(1.0 重建 + 3DGS)生成可漫游世界模型,落盘与 Marble 相同的
assets/concepts/scenes/<sid>/world/worlds/<key>/ 契约。场景预览页「🌍 世界模型」板块在 provider =
hunyuan 时由宿主后台调用本脚本(仅项目「白模」选项开启时显示,每次带 --new),也可手工跑:

  python code/hunyuan_world.py --project <slug> --scene SCN-0001                            # 场景还没有世界模型时:全景图(首个锚点/方案)→ world
  python code/hunyuan_world.py --project <slug> --scene SCN-0001 --new --anchor A2 --scheme day   # 用指定锚点/光照方案的全景再生成一个
  python code/hunyuan_world.py --project <slug> --scene SCN-0001 --resume <job_id> --world W2     # 断网/中断后续接 W2 的下载
  python code/hunyuan_world.py --project <slug> --scene SCN-0001 --list                      # 列出已有世界模型(JSON)
  python code/hunyuan_world.py --project <slug> --scene SCN-0001 --set-default W2            # 设默认世界模型(背景图截图 / 导演台用)
  python code/hunyuan_world.py --project <slug> --scene SCN-0001 --sources                   # 列出可用全景来源(JSON)
  python code/hunyuan_world.py --check                                                       # worker 自检(GPU/env/磁盘)
可选:--classes outdoor|indoor(缺省按场景自动)--views N --steps N --smoke(袖珍自检:4 视图 200 步)
Key/地址:控制台「🎨 生成模型」→「🌍 世界模型」→ HunyuanWorld(远程);也可用环境变量
HUNYUAN_WORKER_URL / HUNYUAN_WORKER_TOKEN。
退出码:0 完成;1 出错;2 项目「白模」选项关闭。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args, spatial_blocking_enabled  # noqa: E402
from modules import hunyuan_world as hw  # noqa: E402
from modules import worldlabs as wl  # noqa: E402
from modules.whitebox import component  # noqa: E402


def log(msg: str):
    print(msg, flush=True)


def main() -> int:
    def configure(ap):
        ap.add_argument('--scene', help='场景 id,如 SCN-0001')
        ap.add_argument('--source', choices=('scene_pano',), default='scene_pano',
                        help='world 输入全景:HunyuanWorld 仅支持场景全景图(与宿主派发命令行保持同形)')
        ap.add_argument('--anchor', default=None, help='全景锚点 id(缺省首个)')
        ap.add_argument('--scheme', default=None, help='scene_pano 的光照方案 slug(缺省该锚点首个已成全景)')
        ap.add_argument('--seed', type=int, default=None)
        ap.add_argument('--resume', metavar='JOB_ID', default=None, help='worker 任务 id,断网/中断后续接下载')
        ap.add_argument('--world', metavar='KEY', default=None, help='--resume 续接的世界模型 key(W1、W2…)')
        ap.add_argument('--new', action='store_true', help='已有世界模型时再生成一个(不动已有的)')
        ap.add_argument('--force', action='store_true', help='同 --new(旧调用兼容)')
        ap.add_argument('--list', action='store_true', help='列出已有世界模型(JSON)后退出')
        ap.add_argument('--set-default', metavar='KEY', default=None, help='把该世界模型设为场景默认后退出')
        ap.add_argument('--sources', action='store_true', help='列出可用全景来源(JSON)后退出')
        ap.add_argument('--check', action='store_true', help='worker 自检(GPU/env/磁盘/忙闲)')
        ap.add_argument('--smoke', action='store_true', help='袖珍自检任务(4 视图 200 步)')
        ap.add_argument('--classes', choices=('outdoor', 'indoor'), help='场景类型(缺省按白模自动)')
        ap.add_argument('--views', type=int, help='轨迹渲染视图数(缺省取配置)')
        ap.add_argument('--steps', type=int, help='3DGS 训练步数(缺省取配置)')
    args, base = parse_args(__doc__, ep=False, configure=configure)
    if args.check:
        print(json.dumps(hw.check_health(), ensure_ascii=False, indent=1))
        return 0
    if not args.scene:
        print('--scene 必填', file=sys.stderr)
        return 1
    sid = component(args.scene)
    if not base.is_dir():
        print(f'项目不存在:{base}', file=sys.stderr)
        return 1
    if args.sources:
        print(json.dumps(wl.list_sources(base, sid), ensure_ascii=False, indent=2))
        return 0
    if args.list:
        dk = wl.default_key(base, sid)
        print(json.dumps([{'key': w['key'], 'default': w['key'] == dk, 'dir': str(w['dir'].relative_to(base)),
                           **{k: w['record'].get(k) for k in ('world_id', 'provider', 'model', 'written_at')},
                           'input': {k: (w['record'].get('input') or {}).get(k) for k in ('source', 'anchor_id', 'scheme')}}
                          for w in wl.list_worlds(base, sid)], ensure_ascii=False, indent=2))
        return 0
    if args.set_default:
        print(f'default={wl.set_default(base, sid, component(args.set_default))}')
        return 0
    if not spatial_blocking_enabled(base):
        print(f'[hunyuan] {args.project}: skipped: spatial_blocking off(项目输出设置「白模」已关闭)')
        return 2
    if args.resume:
        if not args.world:
            print('--resume 须带 --world <key>(续接哪个世界模型目录)', file=sys.stderr)
            return 1
        key = component(args.world)
        rec = hw.finish_world(base, sid, args.resume, log=log, out=wl.pending_world_dir(base, sid, key), key=key)
    elif wl.list_worlds(base, sid) and not (args.new or args.force):
        have = ', '.join(w['key'] for w in wl.list_worlds(base, sid))
        log(f'跳过:{sid} 已有世界模型 {have}(--new 再生成一个)')
        rec = wl.read_world(base, sid) or {}
    else:
        overrides = {}
        if args.classes:
            overrides['classes'] = args.classes
        if args.views:
            overrides['views'] = args.views
        if args.steps:
            overrides['gs_steps'] = args.steps
        if overrides:
            log(f'参数覆盖:{json.dumps(overrides, ensure_ascii=False)}')
        key, out = wl.new_world_dir(base, sid)
        try:
            rec = hw.submit_world(base, sid, anchor_id=args.anchor, scheme=args.scheme, seed=args.seed,
                                  smoke=args.smoke, overrides=overrides or None, log=log, out=out, key=key)
        except Exception:  # noqa: BLE001
            # 与 Marble 同口径:还没提交 worker 的目录自动清掉;已提交(有 generate.json)的留着可 --resume
            import shutil
            if not (out / 'generate.json').is_file():
                shutil.rmtree(out, ignore_errors=True)
            raise
    print(json.dumps({k: rec.get(k) for k in ('key', 'world_id', 'provider', 'model', 'files')},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except hw.HunyuanWorldError as e:
        print(f'[hunyuan] error: {e}', file=sys.stderr)
        sys.exit(1)
