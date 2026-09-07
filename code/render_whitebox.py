#!/usr/bin/env python3
"""Compile scenes/timelines, validate, and optionally export both reference MP4s."""
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from _common import parse_args
from modules.whitebox import compile_episode, component
from modules.whitebox_export import render_videos


def main():
    def configure(parser):
        parser.add_argument('groups',nargs='*')
        parser.add_argument('--export',action='store_true',help='Render top + camera MP4 with Three.js/Chromium/FFmpeg')
        parser.add_argument('--check-only',action='store_true')
        parser.add_argument('--width',type=int,default=960)
        parser.add_argument('--height',type=int,default=540)
        parser.add_argument('--fps',type=int,default=24)
    args,base=parse_args(__doc__,configure=configure)
    component(args.project);component(args.ep)
    episode=compile_episode(base,args.ep)
    selected=set(args.groups)
    errors=[e for e in episode['errors'] if not selected or e['group_id'] in selected]
    missing=selected-{g['group_id'] for g in episode['groups']}-{e['group_id'] for e in errors}
    errors.extend({'group_id':g,'error':'Unknown group'} for g in sorted(missing))
    print(json.dumps({'groups':len(episode['groups']),'scenes':len(episode['scenes']),'errors':errors},ensure_ascii=False),flush=True)
    if errors:return 1
    if args.check_only:return 0
    output=base/'directing'/args.ep/'whitebox';output.mkdir(parents=True,exist_ok=True)
    (output/'episode.json').write_text(json.dumps(episode,ensure_ascii=False,indent=2),encoding='utf-8')
    for sid,scene in episode['scenes'].items():
        (base/'assets/concepts/scenes'/sid/'whitebox.scene.json').write_text(json.dumps(scene,ensure_ascii=False,indent=2),encoding='utf-8')
    if args.export:
        last={}
        def progress(gid,pct):
            bucket=pct//20
            if last.get(gid)!=bucket:print(f'{gid}: {pct}%',flush=True);last[gid]=bucket
        render_videos(base,episode,args.groups or None,width=args.width,height=args.height,fps=args.fps,progress=progress)
    return 0


if __name__=='__main__':
    try:sys.exit(main())
    except Exception as error:print(f'whitebox: {error}',file=sys.stderr);sys.exit(1)
