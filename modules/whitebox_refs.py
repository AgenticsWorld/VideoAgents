"""白模参考视频 → 组视频生成参考视频的接线(2026-09-07)。

项目「输出设置 → 人物精确空间位置」(output.spatial_blocking)开启时,白模调度导出的
`assets/whitebox/<ep>/<grp>/{camera.mp4,top.mp4}` 自动成为该分镜组视频生成的参考视频:
  - 写进组 prompt `assets/prompts/<ep>/<grp>.json` 的 `video_refs`(video-generation 按序传 --ref-video);
  - 在 video_prompt 的 `Shot 1:` 之前插入固定英文锚点段 `Whitebox reference: … Whitebox legend: …`
    (两路视频各自作用、颜色↔人物、眼睛/鼻尖=朝向、深色摄像机盒与射线=机位与镜头方向、禁复现白模外观),
    并在 `Global constraints:` 并入禁白模外观句;
  - 机检 whitebox_ref_bound(code/sync_whitebox_refs.py 不带 --write)。
预算:按本组生效视频模型的参考视频数量/总时长上限取舍——camera.mp4 优先(画面视角,定人物在画面里的位置),
默认只挂 camera(项目 output.whitebox_top_video 默认 false,2026-09-07 用户指令);置 true 且额度允许时才追加 top.mp4
(俯视,只用于理解空间关系);渠道不支持参考视频时不接、退回干净俯视图口径。
数据源:directing/<ep>/whitebox/episode.json(actors/extras 的颜色与 label,render_whitebox.py 编译落盘)。
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys
from pathlib import Path

from modules.whitebox import component, read

COLOR_NAMES = {'#e63946': 'red', '#1d78d8': 'blue', '#2ea043': 'green', '#f59e0b': 'orange',
               '#8e44ad': 'purple', '#00acc1': 'cyan', '#e91e63': 'pink', '#795548': 'brown'}
BLOCK_KEY = 'Whitebox reference:'
LEGEND_KEY = 'Whitebox legend:'
GC_KEY = 'Global constraints:'
GC_SENTENCE = 'No whitebox look: no grey boxes, no placeholder figures, no color-coded people, no camera icon or sight line.'
_BLOCK_RE = re.compile(r'\s*Whitebox reference:.*?(?=Shot\s*1\s*:)', re.S)


def color_name(hex_color):
    return COLOR_NAMES.get(str(hex_color or '').lower(), str(hex_color or 'unknown-color'))


# ---------------------------------------------------------------- inputs
def whitebox_group(base: Path, ep: str, gid: str):
    """返回 (group_from_episode_json, manifest) ;任一缺失/视频文件不全返回 (group|None, None)。"""
    ep, gid = component(ep), component(gid)
    episode = read(base/'directing'/ep/'whitebox'/'episode.json', {}) or {}
    group = next((g for g in episode.get('groups', []) if g.get('group_id') == gid), None)
    manifest = read(base/'assets/whitebox'/ep/gid/'manifest.json', None)
    if not isinstance(manifest, dict):
        return group, None
    files = manifest.get('files') or []
    if len(files) != 2 or not all((base/f).is_file() and (base/f).stat().st_size > 0 for f in files):
        return group, None
    return group, manifest


def _model_caps(model: str):
    m = (model or '').lower()
    if 'seedance-2-5' in m or 'seedance-2.5' in m or 'seedance-2_5' in m:
        return {'max_ref_videos': 10, 'max_total_s': 30}
    if 'seedance-2' in m or 'seedance2' in m:
        return {'max_ref_videos': 3, 'max_total_s': 15}
    if 'minimax' in m and 'h3' in m:
        return {'max_ref_videos': 3, 'max_total_s': 15}
    return None


def video_budget(base: Path, ep: str, gid: str) -> dict:
    """本组生效视频模型的参考视频预算:{max_videos, max_total_s, model, provider, source}。
    组级覆盖 assets/group_settings/<ep>/<grp>.json 优先;全局模型经宿主服务解析(不可用时按项目
    「视频模型设置」shot_group.max_ref_videos / max_group_s 回落)。comfyui/runninghub 渠道不支持参考视频。"""
    settings = read(base/'settings.json', {}) or {}
    sg = settings.get('shot_group') or {}
    ov = read(base/'assets/group_settings'/component(ep)/f'{component(gid)}.json', {}) or {}
    model, provider, source = str(ov.get('video_model') or ''), str(ov.get('provider') or ''), 'group'
    if not model:
        source = 'global'
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
            from services.runtime import core  # noqa: WPS433
            cand = core.group_video_candidates(base.name, core.load_genconfig())
            model, provider = str(cand.get('global_model') or ''), str(cand.get('provider') or '')
        except Exception:  # noqa: BLE001 — 宿主服务不可导入时按项目设定回落
            model, provider, source = '', '', 'project_settings'
    caps = _model_caps(model)
    if provider in ('comfyui', 'runninghub'):
        return {'max_videos': 0, 'max_total_s': 0, 'model': model, 'provider': provider, 'source': source,
                'reason': f'渠道 {provider} 不支持参考视频(--ref-video)'}
    if caps:
        return {'max_videos': caps['max_ref_videos'], 'max_total_s': caps['max_total_s'], 'model': model,
                'provider': provider, 'source': source, 'reason': ''}
    return {'max_videos': int(sg.get('max_ref_videos', 3) or 0), 'max_total_s': float(sg.get('max_group_s', 15) or 15),
            'model': model, 'provider': provider, 'source': source, 'reason': ''}


def plan_refs(base: Path, ep: str, gid: str) -> dict:
    """决定本组挂哪些白模视频:{camera, top, videos[], duration_s, budget, skipped_reason}。"""
    group, manifest = whitebox_group(base, ep, gid)
    ep, gid = component(ep), component(gid)
    if group is None or manifest is None:
        return {'camera': None, 'top': None, 'videos': [], 'group': group, 'budget': None,
                'skipped_reason': '白模视频未导出(先跑 code/render_whitebox.py)'}
    budget = video_budget(base, ep, gid)
    # output.whitebox_top_video(默认 False = 只挂 camera.mp4,预算再宽也不追加 top.mp4;2026-09-07 用户指令):置 True 才按预算追加 top
    top_wanted = ((read(base/'settings.json', {}) or {}).get('output') or {}).get('whitebox_top_video', False) is True
    dur = float(manifest.get('duration_s') or group.get('duration_s') or 0)
    cam, top = f'assets/whitebox/{ep}/{gid}/camera.mp4', f'assets/whitebox/{ep}/{gid}/top.mp4'
    videos, skipped = [], ''
    if budget['max_videos'] <= 0 or dur > budget['max_total_s'] + 1e-6:
        skipped = budget['reason'] or (f'参考视频预算不足(模型 {budget["model"] or "?"}:≤{budget["max_videos"]} 个/总时长 ≤{budget["max_total_s"]}s,组时长 {dur}s)')
    else:
        videos.append(cam)
        if not top_wanted:
            skipped = 'top.mp4 未挂:项目输出设置 whitebox_top_video 未开(默认只挂 camera.mp4)'
        elif budget['max_videos'] >= 2 and 2*dur <= budget['max_total_s'] + 1e-6:
            videos.append(top)
        else:
            skipped = f'top.mp4 未挂:参考视频总时长上限 {budget["max_total_s"]}s 装不下两路 {dur}s 视频,只挂 camera.mp4'
    return {'camera': cam if cam in videos else None, 'top': top if top in videos else None, 'videos': videos,
            'group': group, 'budget': budget, 'duration_s': dur, 'skipped_reason': skipped}


# ---------------------------------------------------------------- prompt text
def legend_rows(group: dict) -> list:
    rows = []
    riders = {a['id']: a for a in group.get('actors', []) if a.get('rider')}
    for a in group.get('actors', []):
        if a.get('rider'):
            continue
        rider_mounts = [m for m in riders.values() if m.get('rider') == a['id']]
        row = f"{color_name(a.get('color'))} figure = {a.get('label') or a['id']} ({a['id']})"
        if a.get('kind') == 'creature':
            row = f"{color_name(a.get('color'))} creature = {a.get('label') or a['id']} ({a['id']})"
        for m in rider_mounts:
            row += f", riding the same-colored creature {m.get('label') or m['id']} ({m['id']})"
        rows.append(row)
    for x in group.get('extras', []) or []:
        rows.append(f"{color_name(x.get('color'))} figure = {x.get('label') or x.get('id')} ({x.get('id')}, background extra)")
    return rows


def build_block(plan: dict) -> str:
    videos = plan['videos']
    if not videos:
        return ''
    ci = videos.index(plan['camera']) + 1
    parts = [f"{BLOCK_KEY} [Video {ci}] is the camera-view whitebox previs of this exact group — grey placeholder geometry "
             "rendered from the real camera of every shot with the same cuts and timing; follow it for camera position, framing, "
             "each character's screen position, depth, facing and movement timing."]
    if plan['top']:
        ti = videos.index(plan['top']) + 1
        parts.append(f"[Video {ti}] is the top-down whitebox previs of the same group — use it only to understand where everyone "
                     "stands and walks in the space, never as a viewpoint.")
    legend = '; '.join(legend_rows(plan['group'] or {}))
    facing = 'the white eyes and nose tip show where a figure faces'
    cam = ('the dark camera box is the camera and the dark line from it is the shooting direction (top view only)'
           if plan['top'] else 'the camera itself is never drawn in the camera view')
    parts.append(f"{LEGEND_KEY} {legend}; {facing}; {cam}.")
    parts.append("Do not reproduce the whitebox look: no grey boxes, no placeholder figures, no color-coded people, "
                 "no camera icon or sight line — render the real characters, set and lighting from the reference images.")
    return ' '.join(parts)


def apply_prompt(prompt: dict, plan: dict) -> dict:
    """幂等回写:video_refs(白模视频在前,保留其他非白模参考视频)、正文锚点段、Global constraints、notes、whitebox_refs。"""
    out = copy.deepcopy(prompt)
    others = [v for v in (out.get('video_refs') or []) if isinstance(v, str) and '/whitebox/' not in v]
    vp = out.get('video_prompt') or ''
    vp = _BLOCK_RE.sub('', vp)
    if plan['videos']:
        out['video_refs'] = plan['videos'] + others
        block = build_block(plan)
        m = re.search(r'Shot\s*1\s*:', vp)
        vp = (vp[:m.start()].rstrip() + ' ' + block + ' ' + vp[m.start():]) if m else (vp.rstrip() + ' ' + block)
        gi = vp.rfind(GC_KEY)
        if gi >= 0 and 'whitebox' not in vp[gi:].lower():
            vp = vp.rstrip()
            vp += ('' if vp.endswith(('.', '。', ';', ';')) else '.') + ' ' + GC_SENTENCE
        elif gi < 0:
            vp = vp.rstrip() + ' ' + GC_KEY + ' ' + GC_SENTENCE
    else:
        if others:
            out['video_refs'] = others
        else:
            out.pop('video_refs', None)
    out['video_prompt'] = vp
    out['whitebox_refs'] = {'camera': plan['camera'], 'top': plan['top'], 'skipped_reason': plan.get('skipped_reason') or '',
                            'model': (plan.get('budget') or {}).get('model', ''), 'source': 'sync_whitebox_refs.v1'}
    note = (f"白模参考视频自动接线(code/sync_whitebox_refs.py):video_refs={plan['videos']}"
            + (f";未挂:{plan['skipped_reason']}" if plan.get('skipped_reason') else ''))
    notes = [n for n in (out.get('notes') or []) if not str(n).startswith('白模参考视频自动接线(')]
    notes.append(note)
    out['notes'] = notes
    return out


def check_prompt(prompt: dict, plan: dict, gid: str) -> tuple[list, list]:
    errs, warns = [], []
    vrefs = [v for v in (prompt.get('video_refs') or []) if isinstance(v, str)]
    vp = prompt.get('video_prompt') or ''
    if not plan['videos']:
        stale = [v for v in vrefs if '/whitebox/' in v]
        if stale:
            errs.append(f"{gid}: video_refs 含白模视频 {stale} 但本组当前不应挂({plan.get('skipped_reason')})")
        if BLOCK_KEY in vp:
            warns.append(f"{gid}: 正文含 {BLOCK_KEY} 段但本组未挂白模视频({plan.get('skipped_reason')}),建议 --write 清理")
        return errs, warns
    for i, v in enumerate(plan['videos']):
        if i >= len(vrefs) or vrefs[i] != v:
            errs.append(f"{gid}: video_refs[{i}] 应为 {v},实际 {vrefs[i] if i < len(vrefs) else '(缺)'}(白模视频须在前、camera 先于 top;跑 code/sync_whitebox_refs.py --write)")
    if BLOCK_KEY not in vp:
        errs.append(f"{gid}: video_prompt 缺 \"{BLOCK_KEY}\" 段(两路白模视频作用/颜色↔人物图例/摄像机与镜头方向含义)")
        return errs, warns
    block = vp[vp.index(BLOCK_KEY):]
    ci = plan['videos'].index(plan['camera']) + 1
    if not re.search(r'\[Video\s*%d\][^.]*camera-view' % ci, block):
        errs.append(f"{gid}: {BLOCK_KEY} 段缺 [Video {ci}] 的 camera-view 说明句")
    if plan['top']:
        ti = plan['videos'].index(plan['top']) + 1
        if not re.search(r'\[Video\s*%d\][^.]*top-down' % ti, block):
            errs.append(f"{gid}: {BLOCK_KEY} 段缺 [Video {ti}] 的 top-down 说明句")
        if 'shooting direction' not in block:
            errs.append(f"{gid}: {LEGEND_KEY} 缺摄像机盒与镜头方向射线的含义说明(shooting direction)")
    if LEGEND_KEY not in block:
        errs.append(f"{gid}: 缺 \"{LEGEND_KEY}\" 颜色↔人物图例")
    else:
        legend = block[block.index(LEGEND_KEY):]
        for a in (plan['group'] or {}).get('actors', []):
            if a.get('rider'):
                continue
            if a['id'] not in legend or color_name(a.get('color')) not in legend:
                errs.append(f"{gid}: 图例缺 {color_name(a.get('color'))} = {a.get('label') or a['id']} ({a['id']})")
        if 'faces' not in legend and 'facing' not in legend:
            errs.append(f"{gid}: 图例缺眼睛/鼻尖=朝向说明")
    gi = vp.rfind(GC_KEY)
    if gi < 0 or 'whitebox' not in vp[gi:].lower():
        errs.append(f"{gid}: {GC_KEY} 缺禁白模外观句(no whitebox look …)")
    return errs, warns


# ---------------------------------------------------------------- sync
def sync_group(base: Path, ep: str, gid: str, write: bool = False) -> dict:
    ep, gid = component(ep), component(gid)
    plan = plan_refs(base, ep, gid)
    pp = base/'assets/prompts'/ep/f'{gid}.json'
    prompt = read(pp, None)
    result = {'group_id': gid, 'video_refs': plan['videos'], 'skipped_reason': plan.get('skipped_reason') or '',
              'has_prompt': isinstance(prompt, dict), 'updated': False, 'errors': [], 'warnings': []}
    if not isinstance(prompt, dict):
        return result
    if write:
        updated = apply_prompt(prompt, plan)
        if updated != prompt:
            backup = base/'directing'/ep/'whitebox'/'prompt_backups'/pp.name
            backup.parent.mkdir(parents=True, exist_ok=True)
            if not backup.exists():
                backup.write_bytes(pp.read_bytes())
            tmp = pp.with_suffix('.json.tmp')
            tmp.write_text(json.dumps(updated, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
            os.replace(tmp, pp)
            result['updated'] = True
        prompt = updated
    e, w = check_prompt(prompt, plan, gid)
    result['errors'], result['warnings'] = e, w
    return result


def sync_episode(base: Path, ep: str, groups=None, write: bool = False) -> dict:
    ep = component(ep)
    source = read(base/'directing'/ep/'shot_list.json', {}) or {}
    ids = [g['group_id'] for g in source.get('generation_groups', []) if g.get('group_id')]
    if groups:
        unknown = sorted(set(groups) - set(ids))
        ids = [g for g in ids if g in set(groups)]
    else:
        unknown = []
    rows = [sync_group(base, ep, gid, write) for gid in ids]
    errors = [f'{g}: unknown group' for g in unknown] + [e for r in rows for e in r['errors']]
    warnings = [w for r in rows for w in r['warnings']]
    return {'groups': rows, 'updated_prompts': [r['group_id'] for r in rows if r['updated']],
            'errors': errors, 'warnings': warnings}
