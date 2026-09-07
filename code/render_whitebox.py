#!/usr/bin/env python3
"""Compile whiteboxes and automatically save every changed top/camera video pair."""
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from _common import parse_args
from modules.whitebox import compile_episode, component
from modules.whitebox_export import ensure_videos


def main():
    def configure(parser):
        parser.add_argument('groups',nargs='*')
        parser.add_argument('--export',action='store_true',help='Compatibility alias: video export is now automatic')
        parser.add_argument('--force',action='store_true',help='Re-render even when saved videos match current inputs')
        parser.add_argument('--scene',help='Update all groups using this scene in the episode')
        parser.add_argument('--check-only',action='store_true')
        parser.add_argument('--width',type=int,help='Override together with --height; must preserve project aspect')
        parser.add_argument('--height',type=int,help='Default dimensions follow project aspect (960px long edge)')
        parser.add_argument('--fps',type=int,default=24)
    args,base=parse_args(__doc__,configure=configure)
    component(args.project);component(args.ep)
    if args.scene:
        component(args.scene)
        if args.groups:raise ValueError('Use either group IDs or --scene, not both')
    episode=compile_episode(base,args.ep)
    selected=set(args.groups)
    if args.scene:
        from modules.whitebox import read
        source=read(base/'directing'/args.ep/'shot_list.json',{})
        selected={g['group_id'] for g in source.get('generation_groups',[]) if g['scene_id']==args.scene}
        if not selected:raise ValueError('No storyboard groups reference this scene in the episode')
    scoped=bool(args.groups or args.scene)
    errors=[e for e in episode['errors'] if not scoped or e['group_id'] in selected]
    missing=selected-{g['group_id'] for g in episode['groups']}-{e['group_id'] for e in errors}
    errors.extend({'group_id':g,'error':'Unknown group'} for g in sorted(missing))
    print(json.dumps({'groups':len(episode['groups']),'scenes':len(episode['scenes']),'errors':errors},ensure_ascii=False),flush=True)
    if errors:return 1
    if args.check_only:return 0
    output=base/'directing'/args.ep/'whitebox';output.mkdir(parents=True,exist_ok=True)
    (output/'episode.json').write_text(json.dumps(episode,ensure_ascii=False,indent=2),encoding='utf-8')
    for sid,scene in episode['scenes'].items():
        (base/'assets/concepts/scenes'/sid/'whitebox.scene.json').write_text(json.dumps(scene,ensure_ascii=False,indent=2),encoding='utf-8')
    last={}
    def progress(gid,pct):
        bucket=pct//20
        if last.get(gid)!=bucket:print(f'{gid}: {pct}%',flush=True);last[gid]=bucket
    result=ensure_videos(base,episode,sorted(selected) if scoped else None,
                         width=args.width,height=args.height,fps=args.fps,progress=progress,force=args.force)
    print(json.dumps({'videos':result,'directory':f'assets/whitebox/{args.ep}/'},ensure_ascii=False),flush=True)
    return 0


if __name__=='__main__':
    try:sys.exit(main())
    except Exception as error:print(f'whitebox: {error}',file=sys.stderr);sys.exit(1)
