#!/usr/bin/env python3
"""Concatenate every group camera.mp4 of an episode into assets/whitebox/<ep>/<ep>-camera.mp4 (episode whitebox reel, 白模样片).

2026-09-11: dialogue/narration subtitles (shot_list dialogue_lines + narration.md via narration_anchors) are burned in
by default (mode=burn); --no-subtitles keeps the plain stream-copy concat.
"""
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
        parser.add_argument('--no-subtitles',action='store_true',help='Do not burn dialogue/narration subtitles (plain concat)')
    args,base=parse_args(__doc__,configure=configure)
    component(args.project);component(args.ep)
    status=episode_reel_status(base,args.ep)
    if args.status:
        out={k:status[k] for k in ('ep','path','exists','stale','stale_reason','groups_total','groups_ready','groups_missing')}
        out['subtitle_cues']=len(status['cues'])
        print(json.dumps(out,ensure_ascii=False),flush=True)
        return 0
    manifest=concat_episode(base,args.ep,allow_missing=args.allow_missing,subtitles=not args.no_subtitles)
    print(json.dumps({'reel':manifest['reels'][0]['path'],'groups':manifest['groups'],'duration_s':manifest['duration_s'],
                      'mode':manifest['mode'],'subtitles':manifest['subtitles'],'missing_groups':manifest['missing_groups']},ensure_ascii=False),flush=True)
    return 0


if __name__=='__main__':
    try:sys.exit(main())
    except Exception as error:print(f'whitebox reel: {error}',file=sys.stderr);sys.exit(1)
