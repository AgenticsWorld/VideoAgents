#!/usr/bin/env python3
"""Concatenate every group camera.mp4 of an episode into assets/whitebox/<ep>/<ep>-camera.mp4 (episode whitebox reel)."""
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from _common import parse_args
from modules.whitebox import component
from modules.whitebox_export import concat_episode, episode_reel_status


def main():
    def configure(parser):
        parser.add_argument('--allow-missing',action='store_true',help='Skip groups without camera.mp4 instead of failing')
        parser.add_argument('--status',action='store_true',help='Only report reel status (exists/stale/missing groups); no rendering')
    args,base=parse_args(__doc__,configure=configure)
    component(args.project);component(args.ep)
    status=episode_reel_status(base,args.ep)
    if args.status:
        print(json.dumps({k:status[k] for k in ('ep','path','exists','stale','groups_total','groups_ready','groups_missing')},ensure_ascii=False),flush=True)
        return 0
    manifest=concat_episode(base,args.ep,allow_missing=args.allow_missing)
    print(json.dumps({'reel':manifest['reels'][0]['path'],'groups':manifest['groups'],'duration_s':manifest['duration_s'],
                      'mode':manifest['mode'],'missing_groups':manifest['missing_groups']},ensure_ascii=False),flush=True)
    return 0


if __name__=='__main__':
    try:sys.exit(main())
    except Exception as error:print(f'whitebox reel: {error}',file=sys.stderr);sys.exit(1)
