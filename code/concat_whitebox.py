#!/usr/bin/env python3
"""Build the episode whitebox reel (白模样片) assets/whitebox/<ep>/<ep>-camera.mp4 from every group's camera-view video.

2026-09-11: dialogue/narration subtitles (shot_list dialogue_lines + narration.md via narration_anchors) are burned in
by default (mode=burn); --no-subtitles keeps the plain stream-copy concat.

2026-10-10(H3W 签字前出样片):默认先按当前白模补出「正式版缺失或已过期」的组的预览版摄影机视角视频
(directing/<ep>/whitebox/preview/<grp>/,本机渲染,不写 assets/whitebox 的正式 manifest、不接进 prompt、没有生成费用),
再合并——用户签字前就能连续看整集白模。正式导出仍只由 render_whitebox.py 在签字后做(预览版指纹没变会被直接转正,不重渲)。
--no-render 只拼现有组视频(不需要 Chromium)。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from _common import parse_args, reexec_with_host_python
from modules.whitebox import compile_episode, component
from modules.whitebox_export import concat_episode, ensure_videos, episode_reel_status


def ensure_previews(base,ep):
    """按当前白模(现场编译,只在内存、不落盘)补出预览版组视频;编译报错的组补不出,原样列在 compile_errors。"""
    episode=compile_episode(base,ep)
    errors=[{'group_id':e['group_id'],'error':e['error']} for e in episode['errors']]
    if not episode['groups']:
        return {'rendered':[],'skipped':[],'compile_errors':errors}
    last={}
    def progress(gid,pct):
        bucket=pct//20
        if last.get(gid)!=bucket:print(f'{gid}: {pct}%',flush=True);last[gid]=bucket
    result=ensure_videos(base,episode,None,progress=progress,preview=True)
    return {'rendered':result['rendered'],'skipped':result['skipped'],'compile_errors':errors}


def main():
    def configure(parser):
        parser.add_argument('--allow-missing',action='store_true',help='Skip groups without camera.mp4 instead of failing')
        parser.add_argument('--status',action='store_true',help='Only report reel status (exists/stale/missing groups); no rendering')
        parser.add_argument('--no-subtitles',action='store_true',help='Do not burn dialogue/narration subtitles (plain concat)')
        parser.add_argument('--no-render',action='store_true',help='只拼现有组视频,不补出缺失/过期组的预览版(不需要 Chromium;白模编译不过又要先看旧样片时用)')
    args,base=parse_args(__doc__,configure=configure)
    component(args.project);component(args.ep)
    if not (args.status or args.no_render):
        # 补预览版要无头 Chromium:当前解释器缺 Playwright 就换宿主解释器重跑(同 render_whitebox.py)
        reexec_with_host_python()
    if args.status:
        status=episode_reel_status(base,args.ep)
        out={k:status[k] for k in ('ep','path','exists','stale','stale_reason','groups_total','groups_ready','groups_missing','preview_groups','whitebox_changed')}
        out['subtitle_cues']=len(status['cues'])
        print(json.dumps(out,ensure_ascii=False),flush=True)
        return 0
    if not args.no_render:
        try:
            previews=ensure_previews(base,args.ep)
        except Exception as error:
            raise RuntimeError(f'补出预览版组视频失败: {error}(只想拼现有组视频可加 --no-render)') from error
        print(json.dumps({'preview_videos':previews},ensure_ascii=False),flush=True)
    manifest=concat_episode(base,args.ep,allow_missing=args.allow_missing,subtitles=not args.no_subtitles)
    print(json.dumps({'reel':manifest['reels'][0]['path'],'groups':manifest['groups'],'duration_s':manifest['duration_s'],
                      'mode':manifest['mode'],'subtitles':manifest['subtitles'],'missing_groups':manifest['missing_groups'],
                      'preview_groups':manifest['preview_groups']},ensure_ascii=False),flush=True)
    return 0


if __name__=='__main__':
    try:sys.exit(main())
    except Exception as error:print(f'whitebox reel: {error}',file=sys.stderr);sys.exit(1)
