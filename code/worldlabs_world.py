#!/usr/bin/env python3
"""World Labs 世界模型 CLI(2026-09-12;规则见 modules/worldlabs.py 顶部注释、docs/worldlabs_world.md)。
渠道(2026-10-09):Marble(默认)或 Atlas(Marble 2 beta,modules/worldlabs_atlas.py),缺省取生成模型页「🌍 世界模型」选中的标签页。

按场景的全景生成可漫游 3D world(高斯泼溅)。一个场景可以有多个世界模型(2026-10-04),每个落盘
assets/concepts/scenes/<sid>/world/worlds/<key>/(key = W1、W2…;旧版放在 world/ 根下的那个算 W1)。场景预览页「🌍 世界模型」板块的
「生成世界模型」按钮由宿主后台调用本脚本(仅项目「白模」选项开启时显示,每次带 --new),也可手工跑:

  python code/worldlabs_world.py --project <slug> --scene SCN-0001                            # 场景还没有世界模型时:全景图(首个锚点/方案)→ world
  python code/worldlabs_world.py --project <slug> --scene SCN-0001 --new --anchor A2 --scheme day   # 用指定锚点/光照方案的全景再生成一个
  python code/worldlabs_world.py --project <slug> --scene SCN-0001 --source depth2rgb          # 白模深度全景 → Marble depth_to_rgb → world(计费)
  python code/worldlabs_world.py --project <slug> --scene SCN-0001 --resume <operation_id> --world W2   # 中断后续接 W2 的下载
  python code/worldlabs_world.py --project <slug> --scene SCN-0001 --list                      # 列出已有世界模型(JSON)
  python code/worldlabs_world.py --project <slug> --scene SCN-0001 --set-default W2            # 设默认世界模型(背景图截图 / 导演台用)
  python code/worldlabs_world.py --project <slug> --scene SCN-0001 --sources                   # 列出可用全景来源(JSON)
  python code/worldlabs_world.py --project <slug> --scene SCN-0001 --prompt-only               # 打印缺省提示词
  python code/worldlabs_world.py --project <slug> --scene SCN-0001 --new --provider atlas      # 用 Atlas 生成(白模深度 + 全景切图 → 多机位视图 → 融合)
  python code/worldlabs_world.py --credits                                                     # 只查余额(Marble;Atlas 没有余额接口,改为验证 Key)
可选:--model marble-1.1|marble-1.1-plus|marble-1.0|marble-1.0-draft(Marble,缺省取生成模型页配置)--seed N --prompt-file <txt>
      --new(已有世界模型时再生成一个,不动已有的;--force 同义,保留给旧调用)
      --provider marble|atlas(缺省取生成模型页选中的标签页)
      --views N(Atlas 目标机位数,缺省 28 = 单次上限;越少越省)--voxel-size X(Atlas 深度体素化,缺省不设)
Key/模型:控制台「🎨 生成模型」→「🌍 世界模型」;Key 也可设环境变量 WORLDLABS_API_KEY(Marble)/ ATLAS_API_KEY(Atlas)。
退出码:0 完成;1 出错;2 项目「白模」选项关闭。
"""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args, spatial_blocking_enabled  # noqa: E402
from modules import worldlabs as wl  # noqa: E402
from modules import worldlabs_atlas as atlas  # noqa: E402
from modules.whitebox import component, read  # noqa: E402


def log(msg: str):
    print(msg, flush=True)


def main() -> int:
    def configure(ap):
        ap.add_argument('--scene', help='场景 id,如 SCN-0001')
        ap.add_argument('--source', choices=list(wl.SOURCES), default='scene_pano',
                        help='world 输入全景:scene_pano=场景全景图(锚点全景,首选);depth2rgb=白模深度全景经 Marble depth_to_rgb 出全景')
        ap.add_argument('--anchor', default=None, help='全景锚点 id(缺省首个)')
        ap.add_argument('--scheme', default=None, help='scene_pano 的光照方案 slug(缺省该锚点首个已成全景)')
        ap.add_argument('--model', default=None, help='Marble 模型(Atlas 忽略)')
        ap.add_argument('--provider', choices=list(wl.PROVIDERS), default=None, help='世界模型渠道(缺省取生成模型页选中的标签页)')
        ap.add_argument('--views', type=int, default=None, help=f'Atlas 目标机位数(缺省 {atlas.max_targets(len(atlas.SOURCE_YAWS) + 1)})')
        ap.add_argument('--voxel-size', type=float, default=None, help='Atlas atlasChisel voxelSize(0–0.4,缺省不设)')
        ap.add_argument('--seed', type=int, default=None)
        ap.add_argument('--prompt-file', default=None)
        ap.add_argument('--prompt-only', action='store_true')
        ap.add_argument('--resume', metavar='OPERATION_ID', default=None)
        ap.add_argument('--world', metavar='KEY', default=None, help='--resume 续接的世界模型 key(W1、W2…)')
        ap.add_argument('--new', action='store_true', help='已有世界模型时再生成一个(不动已有的)')
        ap.add_argument('--force', action='store_true', help='同 --new(旧调用兼容)')
        ap.add_argument('--list', action='store_true', help='列出已有世界模型(JSON)后退出')
        ap.add_argument('--set-default', metavar='KEY', default=None, help='把该世界模型设为场景默认后退出')
        ap.add_argument('--sources', action='store_true', help='列出可用全景来源(JSON)后退出')
        ap.add_argument('--credits', action='store_true', help='只查余额')
    args, base = parse_args(__doc__, ep=False, configure=configure)
    provider = args.provider or wl.world_provider()
    if args.credits:
        if provider == 'atlas':
            print(f"atlas key ok: {json.dumps(atlas.check_key(), ensure_ascii=False)}")
        else:
            print(f'remaining_credits={wl.get_credits()}')
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
                           **{k: w['record'].get(k) for k in ('world_id', 'model', 'written_at')},
                           'input': {k: (w['record'].get('input') or {}).get(k) for k in ('source', 'anchor_id', 'scheme')}}
                          for w in wl.list_worlds(base, sid)], ensure_ascii=False, indent=2))
        return 0
    if args.set_default:
        print(f'default={wl.set_default(base, sid, component(args.set_default))}')
        return 0
    if not spatial_blocking_enabled(base):
        print(f'[worldlabs] {args.project}: skipped: spatial_blocking off(项目输出设置「白模」已关闭)')
        return 2
    prompt = Path(args.prompt_file).read_text(encoding='utf-8').strip() if args.prompt_file else wl.default_prompt(base, sid, panorama=provider != 'atlas')
    if args.prompt_only:
        print(prompt)
        return 0
    if args.resume:
        if not args.world:
            print('--resume 须带 --world <key>(生成时日志里打印的世界模型编号)', file=sys.stderr)
            return 1
        key = component(args.world)
        out = wl.pending_world_dir(base, sid, key)
        finish = atlas.finish_world if (read(out / 'generate.json', None) or {}).get('provider') == 'atlas' else wl.finish_world
        rec = finish(base, sid, args.resume, log=log, out=out, key=key)
    elif wl.list_worlds(base, sid) and not (args.new or args.force):
        have = ', '.join(w['key'] for w in wl.list_worlds(base, sid))
        log(f'跳过:{sid} 已有世界模型 {have}(--new 再生成一个)')
        rec = wl.read_world(base, sid) or {}
    else:
        if provider == 'atlas':
            if args.source != 'scene_pano':
                print('Atlas 世界模型须基于场景全景图(--source scene_pano);depth2rgb 只有 Marble 有', file=sys.stderr)
                return 1
            atlas.api_key()
            log(f'渠道 Atlas({atlas.TASK},{atlas.base_url()})')
        else:
            log(f'渠道 Marble · 余额 {wl.get_credits()} credits')
        key, out = wl.new_world_dir(base, sid)
        log(f'世界模型 {key} → {out}')
        try:
            wl.prepare_pano(base, sid, source=args.source, anchor_id=args.anchor, scheme=args.scheme, text_prompt=prompt,
                            seed=args.seed, force=True, log=log, out=out)
            if provider == 'atlas':
                rec = atlas.generate_world(base, sid, prompt, out=out, key=key, seed=args.seed, views=args.views,
                                           voxel_size=args.voxel_size, log=log)
            else:
                rec = wl.generate_world(base, sid, prompt, model=args.model, seed=args.seed, log=log, out=out, key=key)
        except BaseException:
            # 还没提交 worlds:generate 就失败 → 目录里只有输入全景,清掉不留空壳;已提交的留着,可 --resume <operation_id> --world <key> 续接
            if not (out / 'generate.json').is_file():
                shutil.rmtree(out, ignore_errors=True)
            else:
                op = (json.loads((out / 'generate.json').read_text(encoding='utf-8')) or {}).get('operation_id')
                log(f'世界模型 {key} 未完成;可续接:--resume {op} --world {key}')
            raise
        if provider != 'atlas':
            log(f'余额 {wl.get_credits()} credits')
    print(json.dumps({k: rec.get(k) for k in ('key', 'provider', 'world_id', 'model', 'world_marble_url', 'files')}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except wl.WorldLabsError as e:
        print(f'错误:{e}', file=sys.stderr)
        sys.exit(1)
