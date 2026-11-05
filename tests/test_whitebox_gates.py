"""白模体检第三批(2026-11-05):视频生成前核对白模参考视频是否最新(whitebox_ref_fresh)、H3W 拦未调度 / 编译报错的组、
待决项汇总的人物集合与编译同口径、导演台交互修复与 POST 不自动重试。"""
import json
import re
from pathlib import Path

import pytest

from modules.whitebox import compile_episode

ROOT = Path(__file__).resolve().parents[1]


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')


def stand(t, x):
    return {'t': t, 'position': [x, 0, 0], 'pose': 'stand', 'yaw': 0}


@pytest.fixture
def project(tmp_path):
    base = tmp_path / 'demo'
    write(base / 'settings.json', {'output': {'spatial_blocking': True}})
    write(base / 'assets/concepts/scenes/SCN-1/layout.json', {
        'scene_name': 'Hall', 'landmarks': [{'id': 'a', 'xy': [.3, .5]}], 'views': [{'tile': 1, 'camera_from': 'a', 'looking_at': 'a'}]})
    write(base / 'bible/scenes/SCN-1/whitebox.json', {'dimensions_m': [10, 3, 8], 'objects': []})
    shots = [{'shot_id': f'sh{i}', 'scene_id': 'SCN-1', 'duration_s': 2, 'characters': ['CHAR-1'], 'view_tile': 1} for i in (1, 2)]
    groups = [{'group_id': f'grp{i}', 'scene_id': 'SCN-1', 'scene_no': 'S1', 'shots': [f'sh{i}'], 'total_duration_s': 2,
               'characters_union': ['CHAR-1'], 'blocking_map': {'characters': [{'id': 'CHAR-1', 'label': 'A', 'start': {'landmark': 'a'}}]}}
              for i in (1, 2)]
    write(base / 'directing/ep01/shot_list.json', {'shots': shots, 'generation_groups': groups})
    for i in (1, 2):
        write(base / f'directing/ep01/whitebox_plans/grp{i}.json', {
            'actors': [{'id': 'CHAR-1', 'size_m': [.48, 1.7, .38], 'keyframes': [stand(0, 0), stand(2, 1)]}],
            'cameras': [{'shot_id': f'sh{i}', 'start': 0, 'duration_s': 2, 'movement': 'static', 'keyframes': [
                {'t': 0, 'position': [0, 1.5, 6], 'target': [0, 1, 0], 'fov': 40}, {'t': 2, 'position': [0, 1.5, 6], 'target': [0, 1, 0], 'fov': 40}]}]})
    return base


def land_and_export(base, gid='grp1'):
    """落盘编译结果 + 写一个对应当前白模的 camera.mp4 manifest(不真渲染)。"""
    from modules.whitebox_export import fingerprint
    episode = compile_episode(base, 'ep01')
    write(base / 'directing/ep01/whitebox/episode.json', episode)
    group = next(g for g in episode['groups'] if g['group_id'] == gid)
    video = base / f'assets/whitebox/ep01/{gid}/camera.mp4'
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b'x' * 64)
    write(video.parent / 'manifest.json', {'group_id': gid, 'duration_s': 2, 'source_sha256': fingerprint(episode, group),
                                           'files': [f'assets/whitebox/ep01/{gid}/camera.mp4']})
    return episode


def test_video_refs_must_match_current_whitebox(project):
    from modules.whitebox_refs import check_prompt, plan_refs
    land_and_export(project)
    plan = plan_refs(project, 'ep01', 'grp1', continuation={'mode': 'none'}, prompt={})
    assert plan['videos'] and plan['export_fresh'] is True
    errs, _ = check_prompt({'video_refs': plan['videos'], 'video_prompt': 'x'}, plan, 'grp1')
    assert not any('whitebox_ref_fresh' in e for e in errs)
    # 签字导出后又改了白模、重新落盘,但没重新导出
    p = project / 'directing/ep01/whitebox_plans/grp1.json'
    data = json.loads(p.read_text())
    data['actors'][0]['keyframes'][1]['position'] = [2, 0, 0]
    write(p, data)
    write(project / 'directing/ep01/whitebox/episode.json', compile_episode(project, 'ep01'))
    plan = plan_refs(project, 'ep01', 'grp1', continuation={'mode': 'none'}, prompt={})
    assert plan['export_fresh'] is False
    errs, _ = check_prompt({'video_refs': plan['videos'], 'video_prompt': 'x'}, plan, 'grp1')
    assert any(e.startswith('grp1: whitebox_ref_fresh') and '不要退回提示词' in e for e in errs)


def test_h3w_readiness_blocks_unplanned_and_compile_errors(project):
    from services.runtime import core
    assert core._whitebox_sign_readiness(project, 'ep01') == []
    (project / 'directing/ep01/whitebox_plans/grp2.json').unlink()
    reasons = core._whitebox_sign_readiness(project, 'ep01')
    assert len(reasons) == 1 and '未经白模调度' in reasons[0] and 'grp2' in reasons[0]
    write(project / 'directing/ep01/whitebox_plans/grp2.json', {'actors': [{'id': 'CHAR-9', 'keyframes': []}]})
    reasons = core._whitebox_sign_readiness(project, 'ep01')
    assert len(reasons) == 1 and '编译报错' in reasons[0] and 'grp2' in reasons[0]


def test_issue_collect_accepts_scene_cast_and_mounts(project):
    from modules.whitebox_issues import collect
    src = json.loads((project / 'directing/ep01/shot_list.json').read_text())
    src['generation_groups'][0]['blocking_map']['characters'][0]['mounted'] = 'CRE-1'
    src['generation_groups'][0]['creatures_union'] = ['CRE-1']
    src['generation_groups'][1]['characters_union'] = ['CHAR-1', 'CHAR-2']
    src['generation_groups'][1]['blocking_map']['characters'].append({'id': 'CHAR-2', 'label': 'B', 'start': {'landmark': 'a'}})
    write(project / 'directing/ep01/shot_list.json', src)
    issue = {'issue_id': 'WBI-ep01-grp1-001', 'kind': 'presence', 'severity': 'advisory', 'question': 'q?', 'provisional': 'p',
             'options': [{'id': 'A', 'label': 'a'}], 'recommended': 'A', 'shots': ['sh1'], 'actors': ['CRE-1', 'CHAR-2'],
             't_range_s': [0, 1], 'status': 'open'}
    p = project / 'directing/ep01/whitebox_plans/grp1.json'
    data = json.loads(p.read_text())
    data['issues'] = [issue]
    write(p, data)
    summary = collect(project, 'ep01')['summary']   # CRE-1 是坐骑,CHAR-2 是同场次在场人物:都算本组人物
    assert summary['errors'] == [] and summary['total'] == 1


DIRECTOR = (ROOT / 'apps/web/static/preview_director.html').read_text(encoding='utf-8')
UI = (ROOT / 'apps/web/static/whitebox-ui.js').read_text(encoding='utf-8')


def test_director_edit_paths_respect_locks_groups_and_versions():
    assert re.search(r"async function pushOverride\(p,kindKey,data,gid=SEL\)", DIRECTOR)
    assert "if(gid!==SEL){renderLeft();return}" in DIRECTOR                      # 回包写回发起的组,不串到当前组
    assert "function flyInfo(){\n  if(!editable(PICK))return null;" in DIRECTOR   # WASD 不绕过锁定
    assert "async function deleteWaypoint(idx){\n  if(!editable(PICK))return;" in DIRECTOR
    assert "const f=FLY;FLY=null;commit(f.p,f.info,f.before,f.gid)" in DIRECTOR   # 延迟提交用开始飞行时的对象与组
    assert "const undoable=()=>EDIT&&VERSION==='live';" in DIRECTOR and "async function undo(){if(!undoable())return;" in DIRECTOR
    for fn in ('function selectGroup(gid,force){', 'function selectVersion(version){'):
        body = DIRECTOR[DIRECTOR.index(fn):DIRECTOR.index('\n}', DIRECTOR.index(fn))]
        assert 'flushFly();clearUndo();' in body
    assert 'RA.group&&RA.group.cameras' in DIRECTOR                              # 改机位改的是渲染器手里的组
    assert 'function syncCamPick(){' in DIRECTOR


def test_whitebox_ui_does_not_retry_posts():
    body = UI[UI.index('async function json(url,options={}){'):UI.index('async function previewData')]
    assert "const retryable=['GET','HEAD'].includes(" in body and 'if(transient&&retryable&&attempt<1)continue;' in body
