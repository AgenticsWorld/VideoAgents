#!/usr/bin/env python3
"""World Labs(Marble)世界模型 CLI(2026-09-12;规则见 modules/worldlabs.py 顶部注释、docs/worldlabs_world.md)。

按场景的全景生成可漫游 3D world(高斯泼溅),落盘 assets/concepts/scenes/<sid>/world/;场景预览页「🌍 世界模型」板块的
「生成世界模型」按钮由宿主后台调用本脚本(仅项目「白模」选项开启时显示),也可手工跑:

  python code/worldlabs_world.py --project <slug> --scene SCN-0001                            # 场景全景图(首个锚点/方案)→ world
  python code/worldlabs_world.py --project <slug> --scene SCN-0001 --anchor A2 --scheme day    # 指定锚点/光照方案的全景
  python code/worldlabs_world.py --project <slug> --scene SCN-0001 --source depth2rgb          # 白模深度全景 → Marble depth_to_rgb → world(计费)
  python code/worldlabs_world.py --project <slug> --scene SCN-0001 --resume <operation_id>     # 中断后续接 world 下载
  python code/worldlabs_world.py --project <slug> --scene SCN-0001 --sources                   # 列出可用全景来源(JSON)
  python code/worldlabs_world.py --project <slug> --scene SCN-0001 --prompt-only               # 打印缺省提示词
  python code/worldlabs_world.py --credits                                                     # 只查余额
可选:--model marble-1.1|marble-1.1-plus|marble-1.0|marble-1.0-draft(缺省取生成模型页配置)--seed N --prompt-file <txt>
      --force(已有 world 时归档到 world/variants/<时间>/ 后重出;depth2rgb 重出全景)
Key/模型:控制台「🎨 生成模型」→「🌍 世界模型」;Key 也可设环境变量 WORLDLABS_API_KEY。
退出码:0 完成;1 出错;2 项目「白模」选项关闭。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args, spatial_blocking_enabled  # noqa: E402
from modules import worldlabs as wl  # noqa: E402
from modules.whitebox import component  # noqa: E402


def log(msg: str):
    print(msg, flush=True)


def main() -> int:
    def configure(ap):
        ap.add_argument('--scene', help='场景 id,如 SCN-0001')
        ap.add_argument('--source', choices=list(wl.SOURCES), default='scene_pano',
                        help='world 输入全景:scene_pano=场景全景图(锚点全景,首选);depth2rgb=白模深度全景经 Marble depth_to_rgb 出全景')
        ap.add_argument('--anchor', default=None, help='全景锚点 id(缺省首个)')
        ap.add_argument('--scheme', default=None, help='scene_pano 的光照方案 slug(缺省该锚点首个已成全景)')
        ap.add_argument('--model', default=None)
        ap.add_argument('--seed', type=int, default=None)
        ap.add_argument('--prompt-file', default=None)
        ap.add_argument('--prompt-only', action='store_true')
        ap.add_argument('--resume', metavar='OPERATION_ID', default=None)
        ap.add_argument('--force', action='store_true')
        ap.add_argument('--sources', action='store_true', help='列出可用全景来源(JSON)后退出')
        ap.add_argument('--credits', action='store_true', help='只查余额')
    args, base = parse_args(__doc__, ep=False, configure=configure)
    if args.credits:
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
    if not spatial_blocking_enabled(base):
        print(f'[worldlabs] {args.project}: skipped: spatial_blocking off(项目输出设置「白模」已关闭)')
        return 2
    prompt = Path(args.prompt_file).read_text(encoding='utf-8').strip() if args.prompt_file else wl.default_prompt(base, sid)
    if args.prompt_only:
        print(prompt)
        return 0
    if args.resume:
        wl.finish_world(base, sid, args.resume, log=log)
        return 0
    out = wl.world_dir(base, sid)
    if (out / 'world.json').is_file() and not args.force:
        log(f'跳过:已有 {out / "world.json"}(--force 归档后重出)')
    else:
        log(f'余额 {wl.get_credits()} credits')
        if args.force:
            wl.archive_world(base, sid, log=log)
        wl.prepare_pano(base, sid, source=args.source, anchor_id=args.anchor, scheme=args.scheme, text_prompt=prompt,
                        seed=args.seed, force=args.force, log=log)
        wl.generate_world(base, sid, prompt, model=args.model, seed=args.seed, log=log)
        log(f'余额 {wl.get_credits()} credits')
    rec = wl.read_world(base, sid) or {}
    print(json.dumps({k: rec.get(k) for k in ('world_id', 'model', 'world_marble_url', 'files')}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except wl.WorldLabsError as e:
        print(f'错误:{e}', file=sys.stderr)
        sys.exit(1)
