#!/usr/bin/env python3
"""Compile whiteboxes and automatically save every changed camera-view video (camera.mp4; no top view since 2026-09-08).

2026-09-09 流程:白模调度 Agent 用 --compile-only 只编译落盘 episode.json 供预览页审看;用户签字「H3W-白模确认」后,
白模导出 Agent 再不带该参数运行本脚本导出 camera.mp4 并自动接线;导出完成后才生成分镜背景图(code/render_shot_plates.py)。"""
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from _common import parse_args, reexec_with_host_python
from modules.whitebox import compile_episode, component
from modules.whitebox_export import concat_episode, ensure_videos, episode_reel_status
from modules.whitebox_refs import sync_episode


def main():
    def configure(parser):
        parser.add_argument('groups',nargs='*')
        parser.add_argument('--export',action='store_true',help='Compatibility alias: video export is now automatic')
        parser.add_argument('--force',action='store_true',help='Re-render even when saved videos match current inputs')
        parser.add_argument('--scene',help='Update all groups using this scene in the episode')
        parser.add_argument('--check-only',action='store_true')
        parser.add_argument('--verify-export',action='store_true',help='机检 whitebox_videos_exported:编译后核对所选组 camera.mp4/manifest 存在且源指纹为当前值,不导出;缺/过期退出码 1')
        parser.add_argument('--compile-only',action='store_true',help='只编译并落盘 episode.json / whitebox.scene.json,不导出视频、不接线(白模调度阶段,待用户签字后再导出)')
        parser.add_argument('--stills',action='store_true',help='白模自检(Agent 高级设置「白模自检」开启时):同 --compile-only 编译落盘后,无头渲染所选组每镜若干时刻的摄影机视角+空间视角,拼成联系表 directing/<ep>/whitebox/stills/<grp>.jpg 供 Agent 读图核对取景;不导出 camera.mp4、不接线')
        parser.add_argument('--width',type=int,help='Override together with --height; must preserve project aspect')
        parser.add_argument('--height',type=int,help='Default dimensions follow project aspect (960px long edge)')
        parser.add_argument('--fps',type=int,default=24)
    args,base=parse_args(__doc__,configure=configure)
    component(args.project);component(args.ep)
    if not (args.check_only or args.verify_export or (args.compile_only and not args.stills)):
        # 静帧/导出要无头 Chromium:当前解释器缺 Playwright 就换宿主解释器重跑(Agent 写 python3 落到别的环境的前科)
        reexec_with_host_python()
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
    # prose_clean(2026-09-29,前科 fengshen3 ep07):套用裁决/导演台批次回写源文件时,space_fragment_en / route_en
    # 只能是画面散文(下游逐字拼进视频 prompt);裁决出处/坐标/秒数/visible 写进这两处 = 交付不通过
    from modules.prose_hygiene import scan_episode, describe
    for row in scan_episode(base,args.ep):
        if not scoped or row['group_id'] in selected:
            errors.append({'group_id':row['group_id'],'error':f"prose_clean: {row['where']} {row['field']} 混入批注/数值({describe(row['hits'])});"
                           "该字段会被逐字拼进视频 prompt,只写画面散文——裁决出处写 director_decisions / issues[].applied.note,数值写 keyframes,时序写 beats[].t"})
    missing=selected-{g['group_id'] for g in episode['groups']}-{e['group_id'] for e in errors}
    errors.extend({'group_id':g,'error':'Unknown group'} for g in sorted(missing))
    print(json.dumps({'groups':len(episode['groups']),'scenes':len(episode['scenes']),'errors':errors},ensure_ascii=False),flush=True)
    # 待决项汇总(docs/whitebox.md「待决项与用户裁决」):回执须原样带上;阻断级未清时 H3W 签字会被拒
    from modules.whitebox_issues import format_summary
    summary=episode.get('issues_summary') or {}
    print(json.dumps({'issues':{k:summary.get(k) for k in ('total','open','blocking_open','decided','applied','stale','waived','groups_open','blocking_ids')},
                      'issues_text':format_summary(summary)},ensure_ascii=False),flush=True)
    if errors:return 1
    if args.check_only:return 0
    output=base/'directing'/args.ep/'whitebox';output.mkdir(parents=True,exist_ok=True)
    (output/'episode.json').write_text(json.dumps(episode,ensure_ascii=False,indent=2),encoding='utf-8')
    for sid,scene in episode['scenes'].items():
        (base/'assets/concepts/scenes'/sid/'whitebox.scene.json').write_text(json.dumps(scene,ensure_ascii=False,indent=2),encoding='utf-8')
    if args.verify_export:
        from modules.whitebox_export import fingerprint
        report={}
        for g in episode['groups']:
            if scoped and g['group_id'] not in selected:continue
            folder=base/'assets/whitebox'/args.ep/g['group_id']
            try:record=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
            except Exception:record={}
            video=folder/'camera.mp4'
            report[g['group_id']]=('ok' if record.get('source_sha256')==fingerprint(episode,g) and video.is_file() and video.stat().st_size>0
                                   else 'stale' if video.is_file() else 'missing')
        bad={k:v for k,v in report.items() if v!='ok'}
        print(json.dumps({'whitebox_videos_exported':{'groups':len(report),'ok':len(report)-len(bad),'problems':bad}},ensure_ascii=False),flush=True)
        print(f"[whitebox_videos_exported] {args.project}/{args.ep}: {len(bad)} 组缺/过期 -> {'FAIL' if bad else 'PASS'}",flush=True)
        return 1 if bad else 0
    if args.stills:
        from modules.whitebox_stills import render_stills
        sheets=render_stills(base,episode,sorted(selected) if scoped else None,progress=lambda gid:print(f'{gid}: stills ok',flush=True))
        print(json.dumps({'compiled':[g['group_id'] for g in episode['groups']],'stills':sheets,'videos':'skipped(--stills:仅自检静帧,不导出)'},ensure_ascii=False),flush=True)
        return 0
    if args.compile_only:
        print(json.dumps({'compiled':[g['group_id'] for g in episode['groups']],'videos':'skipped(--compile-only:待用户签字 H3W-白模确认后再导出)'},ensure_ascii=False),flush=True)
        return 0
    last={}
    def progress(gid,pct):
        bucket=pct//20
        if last.get(gid)!=bucket:print(f'{gid}: {pct}%',flush=True);last[gid]=bucket
    result=ensure_videos(base,episode,sorted(selected) if scoped else None,
                         width=args.width,height=args.height,fps=args.fps,progress=progress,force=args.force)
    print(json.dumps({'videos':result,'directory':f'assets/whitebox/{args.ep}/'},ensure_ascii=False),flush=True)
    # 整集白模样片(2026-09-08,视频预览页/分镜预览页「白模样片」板块,原名白模合辑):已生成过样片且本次有组重出时自动刷新,
    # 保持样片与各组 camera.mp4 一致(含对白/旁白字幕);从未生成过的不主动出,由用户在预览页点「重新生成白模样片」派单
    if result['rendered']:
        status=episode_reel_status(base,args.ep)
        if status['exists']:
            try:
                reel=concat_episode(base,args.ep,allow_missing=True)
                print(json.dumps({'reel':{'path':reel['reels'][0]['path'],'groups':reel['groups'],'duration_s':reel['duration_s'],
                                  'missing_groups':reel['missing_groups']}},ensure_ascii=False),flush=True)
            except Exception as error:
                print(json.dumps({'reel':{'error':str(error),'stale':True}},ensure_ascii=False),flush=True)
    # 白模视频 → 组视频生成参考视频自动接线(2026-09-07,仅「人物精确空间位置」开启时):
    # 已有组 prompt 的写 video_refs + Whitebox reference 段;尚无 prompt 的组由 prompt 工位产出后再跑 sync_whitebox_refs.py --write
    from _common import spatial_blocking_enabled
    if spatial_blocking_enabled(base):
        sync=sync_episode(base,args.ep,sorted(selected) if scoped else None,write=True)
        print(json.dumps({'whitebox_refs':{'updated_prompts':sync['updated_prompts'],
                          'attached':[r['group_id'] for r in sync['groups'] if r['video_refs']],
                          'skipped':{r['group_id']:r['skipped_reason'] for r in sync['groups'] if r['skipped_reason']},
                          'errors':sync['errors']}},ensure_ascii=False),flush=True)
    return 0


if __name__=='__main__':
    try:sys.exit(main())
    except Exception as error:print(f'whitebox: {error}',file=sys.stderr);sys.exit(1)
