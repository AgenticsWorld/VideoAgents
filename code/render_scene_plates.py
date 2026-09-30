#!/usr/bin/env python3
"""场景图(正向/反向)出图与状态(白模关闭项目的场景一致性 A 方案;规则见 modules/scene_plates.py、docs/scene_plates.md)。

正向图由 06-art/environment-concept 在 p6-env-concept(每集开头,本集用到的场景)出图并登记 assets/concepts/scenes/<sid>/scene_plates.json;本脚本负责:
  --status                 机检 scene_plates_complete:每个场景正向图在、按生效模式需要反向图的场景反向图在、各集用到的光照变体在且未过期(退出码 1 = 缺)
  (默认)                   按生效模式给需要反向图的场景出图(auto:各集 shot_list 有镜 plate_view=reverse 才出;pair:全出;single:不出),
                           以正向图为母版(--ref);再按各集组 lighting_scheme_id 补光照变体(≠ 母版 front.lighting_scheme_id 的方案,
                           main_01__<方案>.png / reverse_01__<方案>.png,以母版为 --ref 只改光照,2026-09-30);出完写回登记并对已产出的组 prompt 自动 --write 接线
  --scene SID [--force]    只处理该场景(--force 已有也重出,旧图移入 candidates/)
  --dry-run                只写 reverse_01.prompt.txt 不出图
项目「白模」开启时报 skipped(那条链由 render_shot_plates.py 负责,本脚本不触碰)。

用法:python3 code/render_scene_plates.py --project <slug> --ep ep01 --status
     python3 code/render_scene_plates.py --project <slug> --ep ep01
     python3 code/render_scene_plates.py --project <slug> --scene SCN-0006 --force
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args, spatial_blocking_enabled  # noqa: E402
from modules import scene_plates as sp  # noqa: E402
from modules.whitebox import component  # noqa: E402


def main():
    def configure(ap):
        ap.add_argument('--scene', nargs='*', default=None, help='只处理这些场景 id')
        ap.add_argument('--status', action='store_true', help='机检 scene_plates_complete,不出图')
        ap.add_argument('--force', action='store_true', help='已有反向图也重出')
        ap.add_argument('--dry-run', action='store_true')
        ap.add_argument('--seed', type=int, default=None)
        ap.add_argument('--no-sync', action='store_true', help='出图后不回写组 prompt')
    args, base = parse_args(__doc__, configure=configure)
    if spatial_blocking_enabled(base):
        print(f"[scene_plates_complete] {args.project}: skipped: spatial_blocking on(项目「白模」已开启,场景一致性走白模视频 + 分镜背景图链,本脚本不参与)-> PASS")
        return 0
    scenes = [component(s) for s in args.scene] if args.scene else None
    ep = component(args.ep) if args.ep and not args.scene else None
    if args.status:
        st = sp.status(base, ep, scenes)
        for r in st['scenes']:
            print(f"   {r['scene_id']}: {r['state']:15s} mode={r.get('mode')}/{r.get('effective')} front={r.get('front')} reverse={r.get('reverse')}"
                  + (f" needed_by={r['needed_by']}" if r.get('needed_by') else ''))
        for w in st['warnings']:
            print('WARN', w)
        for e in st['errors']:
            print('VIOLATION', e)
        print(f"[scene_plates_complete] {args.project}: 项目模式 {st['project_mode']},场景 {len(st['scenes'])},缺 {len(st['errors'])} -> {'FAIL' if st['errors'] else 'PASS'}")
        return 1 if st['errors'] else 0
    res = sp.render_needed(base, ep, scenes, force=args.force, dry_run=args.dry_run, seed=args.seed)
    print(json.dumps({'rendered': res['rendered'], 'skipped': [f'{s}:{r}' for s, r in res['skipped']]}, ensure_ascii=False))
    if args.dry_run:
        return 2
    if res['rendered'] and not args.no_sync:
        eps = [args.ep] if args.ep else sorted(p.parent.name for p in (base / 'directing').glob('ep*/shot_list.json'))
        for e in eps:
            r = sp.sync_episode(base, e, None, write=True)
            if r['updated_prompts']:
                print(f"   已回写 {e} 组 prompt:{r['updated_prompts']}")
    st = sp.status(base, ep, scenes)
    for e in st['errors']:
        print('VIOLATION', e)
    return 1 if st['errors'] else 0


if __name__ == '__main__':
    sys.exit(main())
