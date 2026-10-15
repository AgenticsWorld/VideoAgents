#!/usr/bin/env python3
"""把某镜的起点/终点背景图换成本场景背景图库里已有的一张(2026-10-05,#98;与分镜预览页「🔁 换图」同一入口)。

典型用法:用户在分镜预览页对某镜背景图提「改为用另一镜(同场景同光照)的背景图」—— 不出新图、不花钱,
只把集索引 directing/<ep>/shot_plates.json 本镜该角色条目改指向库里那张(reuse=manual,记换前 key),
再自动 sync_shot_plates --write 本组,把新图接进组 prompt refs / Shot plates 段。本镜机位指纹(camera)不动:
非 --force 的 render_shot_plates 按「记录仍新鲜」保留手选;--force 才按机位重新决策(会覆盖手选)。

  python code/swap_shot_plate.py --project <slug> --ep ep01 --shot sh012 --list                 # 列本场景库里可选的图(key / 光照方案 / 哪些镜在用)
  python code/swap_shot_plate.py --project <slug> --ep ep01 --shot sh012 --from-shot sh008         # 改用 sh008 起点图(--from-role end 取其终点图)
  python code/swap_shot_plate.py --project <slug> --ep ep01 --shot sh012 --role end --key L1_b090_h2_x3_z-2_w70
退出码:0 完成;1 参数/找不到(镜、角色、库条目、文件)或 sync 机检有违规。
纪律:同场景才能换(库按场景分);光照方案与本组不一致时打印 WARN,由用户裁决、不自行阻拦;不得直接编辑 shot_plates.json 绕过。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args, spatial_blocking_enabled  # noqa: E402
from modules import shot_plates as sp  # noqa: E402
from modules.whitebox import component  # noqa: E402


def _used_by(base: Path, scene_id: str) -> dict:
    """本场景库 key → 引用它的镜;只算本场景的镜(九宫格等 key 按光照方案命名,不同场景库里会重名)。"""
    out: dict = {}
    for f in sorted((base / 'directing').glob('ep*/shot_plates.json')):
        try:
            idx = json.loads(f.read_text(encoding='utf-8'))
        except Exception:  # noqa: BLE001
            continue
        for sid, r in (idx.get('shots') or {}).items():
            if not isinstance(r, dict) or r.get('scene_id') != scene_id:
                continue
            for p in r.get('plates') or []:
                if isinstance(p, dict) and p.get('key'):
                    out.setdefault(p['key'], []).append(f"{f.parent.name}/{sid}" + ('(end)' if p.get('role') == 'end' else ''))
    return out


def main():
    def configure(ap):
        ap.add_argument('--shot', required=True, help='要换图的镜号 shNNN')
        ap.add_argument('--role', default='start', choices=['start', 'end'], help='换本镜的起点(默认)或终点背景图')
        ap.add_argument('--key', default='', help='库条目 key(--list 可查)')
        ap.add_argument('--from-shot', default='', help='改用另一镜正在用的背景图(须同场景)')
        ap.add_argument('--from-role', default='start', choices=['start', 'end'], help='取另一镜的起点(默认)或终点图')
        ap.add_argument('--list', action='store_true', help='只列本场景库里可选的图,不改')
    args, base = parse_args(__doc__, configure=configure)
    if not spatial_blocking_enabled(base):
        print(f"[swap_shot_plate] {args.project}: skipped: spatial_blocking off(项目输出设置「人物精确空间位置」已关闭,没有分镜背景图)")
        return 0
    ep, shot = component(args.ep), component(args.shot)
    idx = sp.load_episode_index(base, ep)
    rec = idx['shots'].get(shot)
    if not isinstance(rec, dict) or not rec.get('plates'):
        print(f'错误: {ep}/{shot} 还没有分镜背景图记录', file=sys.stderr)
        return 1
    sid = component(str(rec.get('scene_id') or ''))
    if args.list:
        used = _used_by(base, sid)
        rows = [{'key': e['key'], 'file': e.get('file'), 'lighting_scheme_id': e.get('lighting_scheme_id'), 'master': bool(e.get('master')),
                 'manual': bool(e.get('manual')), 'legacy': sp.is_legacy(e), 'used_by': used.get(e['key'], [])}
                for e in sp.load_library(base, sid)['plates'] if e.get('file') and (base / e['file']).is_file()]
        print(json.dumps({'swap_shot_plate': {'ep': ep, 'shot': shot, 'scene_id': sid, 'lighting_scheme_id': rec.get('lighting_scheme_id'),
                                              'current': {p.get('role'): p.get('key') for p in rec['plates'] if isinstance(p, dict)},
                                              'plates': rows}}, ensure_ascii=False, indent=1))
        return 0
    key = args.key
    if args.from_shot:
        src = idx['shots'].get(component(args.from_shot))
        if not isinstance(src, dict) or not src.get('plates'):
            print(f'错误: {ep}/{args.from_shot} 没有分镜背景图记录', file=sys.stderr)
            return 1
        if component(str(src.get('scene_id') or '')) != sid:
            print(f"错误: {args.from_shot} 在场景 {src.get('scene_id')},与 {shot} 的 {sid} 不同,背景图不能跨场景换", file=sys.stderr)
            return 1
        slot = next((p for p in src['plates'] if isinstance(p, dict) and p.get('role') == args.from_role), None)
        if not slot or not slot.get('key'):
            print(f'错误: {args.from_shot} 没有 {args.from_role} 背景图条目', file=sys.stderr)
            return 1
        key = slot['key']
    if not key:
        print('错误: 须给 --key 或 --from-shot(或用 --list 查看可选)', file=sys.stderr)
        return 1
    entry = next((e for e in sp.load_library(base, sid)['plates'] if e.get('key') == key), None) or {}
    if entry.get('lighting_scheme_id') and rec.get('lighting_scheme_id') and entry['lighting_scheme_id'] != rec['lighting_scheme_id']:
        print(f"WARN {ep}/{shot}: 库图 {key} 的光照方案 {entry['lighting_scheme_id']} 与本组 {rec['lighting_scheme_id']} 不同(用户裁决,照换)")
    try:
        res = sp.swap_shot_plate(base, ep, shot, args.role, key)
    except sp.PlateSwapError as error:
        print(f'错误: {error}', file=sys.stderr)
        return 1
    print(json.dumps({'swap_shot_plate': res}, ensure_ascii=False), flush=True)
    sync = res.get('sync') or {}
    for w in sync.get('warnings') or []:
        print('WARN', w)
    if sync.get('errors'):
        for e in sync['errors']:
            print('VIOLATION', e)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
