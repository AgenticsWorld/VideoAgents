#!/usr/bin/env python3
"""把一张九宫格格子(或它的无 AI 派生图:⧉ 复制的副本、✂ 裁剪 / 翻转过的、画板只做过「调整」后设为最终版的 _revN)
用场景图像模型重绘放大(2026-11-06;docs/shot_plates.md「格子重绘放大」)。

分镜预览页「🔁 换图」换上还没放大的格子时,服务端后台起本命令;也可手动跑。逻辑与出图收尾、视频生成前同一入口
modules/shot_plates.ensure_grid9_tile_upscaled:按格加锁,已放大 / 别处刚放完的直接沿用,同一模型此前机检拒收的不再花钱;
通过漂移机检即原地覆盖格子文件(集索引 / 组 prompt 不用动),不过则沿用插值格。

  python code/upscale_grid9_tile.py --project <slug> --scene SCN-0002 --key LGT-0002-01_grid9_t1
  python code/upscale_grid9_tile.py --project <slug> --ep ep01 --shot sh0010 [--role end]     # 按镜找它正用着的格子
退出码:0 已放大 / 沿用已放大 / 原生已够大;6 没放大成、沿用插值格(原因见输出 [grid9_upscale_fallback]);1 参数或找不到。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args  # noqa: E402
from modules import shot_plates as sp  # noqa: E402
from modules.whitebox import component  # noqa: E402


def main():
    def configure(ap):
        ap.add_argument('--scene', default='', help='场景 id(与 --key 一起用)')
        ap.add_argument('--key', default='', help='格子的库条目 key')
        ap.add_argument('--shot', default='', help='按镜找它正用着的格子(与 --ep 一起用)')
        ap.add_argument('--role', default='start', choices=['start', 'end'])
        ap.add_argument('--source', default='cli', help='记进库条目 upscale.source 的触发来源(服务端换图后台传 swap)')
    args, base = parse_args(__doc__, configure=configure)
    sid, key = component(args.scene) if args.scene else '', args.key.strip()
    if args.shot:
        rec = sp.load_episode_index(base, component(args.ep))['shots'].get(component(args.shot)) or {}
        slot = next((p for p in rec.get('plates') or [] if isinstance(p, dict) and p.get('role') == args.role), None)
        if not slot:
            print(f'错误: {args.ep}/{args.shot} 没有 {args.role} 背景图', file=sys.stderr)
            return 1
        sid, key = component(str(rec.get('scene_id') or '')), slot.get('key') or ''
    if not sid or not key:
        print('错误: 须给 --scene + --key,或 --ep + --shot', file=sys.stderr)
        return 1
    res = sp.ensure_grid9_tile_upscaled(base, sid, key, source=args.source)
    print(json.dumps({'grid9_upscale': {'scene_id': sid, 'key': key, **{k: v for k, v in res.items() if k != 'attempted'}}},
                     ensure_ascii=False), flush=True)
    if res['status'] in ('done', 'reused', 'not_needed'):
        return 0
    print(f"[grid9_upscale_fallback] {base.name}/{sid}/{key} 没能放大,沿用插值格:{res.get('reason')}", flush=True)
    return 6


if __name__ == '__main__':
    sys.exit(main())
