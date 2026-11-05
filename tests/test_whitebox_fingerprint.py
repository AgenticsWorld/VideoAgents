"""白模指纹口径(2026-11-05,第二批):视频 / 导演台版本指纹只算渲染相关字段(r1),旧口径存量照认;
样片按片段指纹判过期;机位审查失效的组可看不可导;导演台覆盖层先于组间继承套用。"""
import copy
import json
import re
from pathlib import Path

import pytest

from modules.whitebox import compile_episode

ROOT = Path(__file__).resolve().parents[1]


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')


def stand(t, x, z=0.0, yaw=0.0):
    return {'t': t, 'position': [x, 0, z], 'pose': 'stand', 'yaw': yaw}


def cam(sid, start, dur):
    return {'shot_id': sid, 'start': start, 'duration_s': dur, 'movement': 'static', 'basis': '机位说明',
            'keyframes': [{'t': 0, 'position': [0, 1.5, 6], 'target': [0, 1, 0], 'fov': 40},
                          {'t': dur, 'position': [0, 1.5, 6], 'target': [0, 1, 0], 'fov': 40}]}


@pytest.fixture
def project(tmp_path):
    base = tmp_path / 'demo'
    write(base / 'assets/concepts/scenes/SCN-1/layout.json', {
        'scene_name': 'Hall', 'landmarks': [{'id': 'a', 'xy': [.3, .5], 'label': '门'}],
        'views': [{'tile': 1, 'camera_from': 'a', 'looking_at': 'a'}]})
    write(base / 'bible/scenes/SCN-1/whitebox.json', {'dimensions_m': [10, 3, 8], 'objects': [
        {'id': 'desk', 'size_m': [1, .75, .5], 'position': [2, .375, 0], 'note': '书桌', 'collision_role': 'furniture'}]})
    shots = [{'shot_id': 'sh1', 'scene_id': 'SCN-1', 'duration_s': 2, 'characters': ['CHAR-1'], 'view_tile': 1},
             {'shot_id': 'sh2', 'scene_id': 'SCN-1', 'duration_s': 2, 'characters': ['CHAR-1'], 'view_tile': 1}]
    route = {'id': 'CHAR-1', 'label': 'A', 'start': {'landmark': 'a'}}
    groups = [{'group_id': 'grp1', 'scene_id': 'SCN-1', 'scene_no': 'S1', 'shots': ['sh1'], 'total_duration_s': 2,
               'characters_union': ['CHAR-1'], 'blocking_map': {'characters': [route]}},
              {'group_id': 'grp2', 'scene_id': 'SCN-1', 'scene_no': 'S1', 'shots': ['sh2'], 'total_duration_s': 2,
               'characters_union': ['CHAR-1'], 'blocking_map': {'characters': [route]}, 'continuity_from': 'grp1'}]
    write(base / 'directing/ep01/shot_list.json', {'shots': shots, 'generation_groups': groups})
    plan1 = {'actors': [{'id': 'CHAR-1', 'size_m': [.48, 1.7, .38], 'keyframes': [stand(0, 0), stand(2, 1)]}],
             'props': [{'id': 'cup', 'size_m': [.1, .1, .1], 'position': [2, .8, 0], 'color': '#d62828', 'label': '杯子'}],
             'cameras': [cam('sh1', 0, 2)], 'basis': ['计划说明']}
    plan2 = {'actors': [{'id': 'CHAR-1', 'size_m': [.48, 1.7, .38], 'keyframes': [stand(0, 1), stand(2, 1.5)]}],
             'cameras': [cam('sh2', 0, 2)], 'continuity': {'actors': 'validate'}}
    write(base / 'directing/ep01/whitebox_plans/grp1.json', plan1)
    write(base / 'directing/ep01/whitebox_plans/grp2.json', plan2)
    return base


def groups_of(base):
    episode = compile_episode(base, 'ep01')
    assert not episode['errors'], episode['errors']
    return episode, {g['group_id']: g for g in episode['groups']}


def test_r1_ignores_text_and_metadata_but_tracks_visuals(project):
    from modules.whitebox_export import fingerprint
    episode, g = groups_of(project)
    base_fp = fingerprint(episode, g['grp1'])
    assert base_fp.startswith('r1:')
    noise = copy.deepcopy(episode)
    n = next(x for x in noise['groups'] if x['group_id'] == 'grp1')
    n['warnings'] = ['新警告'];n['continuity_from'] = 'grp0';n['scene_cast'] = ['CHAR-9'];n['overrides'] = {'actors': ['CHAR-1']}
    n['cameras'][0].update(basis='改了说明', source_fingerprint='v2:x', placement_fingerprint='v3:y', overridden=True, framing_check='ok')
    n['actors'][0].update(label='新名字', letter='Z', presence={'state': 'present', 'reason': '在场'})
    n['actors'][0]['keyframes'][0]['note'] = '起点'
    n['actors'][0]['keyframes'][1]['position'] = [1.0, 0.0, 0.0]          # 1 → 1.0、0 → 0.0:等值数值不算变
    n['props'][0]['label'] = '茶杯'
    sc = noise['scenes']['SCN-1']
    sc['name'] = '大厅';sc['warnings'] = ['x'];sc['landmarks'][0]['label'] = '大门'
    sc['objects'][0]['note'] = '新说明';sc['objects'][0]['collision_role'] = 'solid'
    assert fingerprint(noise, n) == base_fp
    for mutate in (lambda x, s: x['actors'][0]['keyframes'][1].__setitem__('position', [1.2, 0, 0]),
                   lambda x, s: x['props'][0].__setitem__('color', '#1d78d8'),
                   lambda x, s: x['cameras'][0]['keyframes'][0].__setitem__('fov', 35),
                   lambda x, s: x['cameras'][0].__setitem__('visible_actor_ids', []),
                   lambda x, s: s['objects'][0].__setitem__('size_m', [1.2, .75, .5]),
                   lambda x, s: x['actors'][0].__setitem__('color', '#000000')):
        changed = copy.deepcopy(episode)
        x = next(y for y in changed['groups'] if y['group_id'] == 'grp1')
        mutate(x, changed['scenes']['SCN-1'])
        assert fingerprint(changed, x) != base_fp


def test_legacy_manifest_fingerprints_still_match(project):
    from modules.whitebox_export import _fingerprint, fingerprint_matches
    episode, g = groups_of(project)
    legacy = _fingerprint(episode, g['grp1'])                 # 2026-11-05 前写进 manifest 的无前缀整份口径
    assert not legacy.startswith('r1:') and fingerprint_matches(legacy, episode, g['grp1'])
    assert fingerprint_matches(_fingerprint(episode, g['grp1'], review_marks=True), episode, g['grp1'])
    assert not fingerprint_matches('r1:' + '0' * 64, episode, g['grp1']) and not fingerprint_matches('', episode, g['grp1'])


def test_neighbour_edit_no_longer_stales_the_next_group(project):
    from modules.whitebox_export import fingerprint
    episode, g = groups_of(project)
    before = fingerprint(episode, g['grp2'])
    plan = project / 'directing/ep01/whitebox_plans/grp1.json'
    data = json.loads(plan.read_text())
    data['actors'][0]['keyframes'][1]['position'] = [3, 0, 0]   # grp1 终点挪走 → grp2 多一条「与 grp1 不连续」警告
    write(plan, data)
    episode, g = groups_of(project)
    assert any('不连续' in w for w in g['grp2']['warnings'])
    assert fingerprint(episode, g['grp2']) == before


def test_render_fingerprint_covers_every_field_the_renderer_reads():
    """扫 whitebox-renderer.js:按变量名找它从数据对象上读的属性,必须都在 r1 白名单里(新读字段忘了登记 = 指纹漏变化)。"""
    from modules import whitebox_export as wx
    src = (ROOT / 'apps/web/static/whitebox-renderer.js').read_text(encoding='utf-8')
    allowed = {
        'sceneData': set(wx.RENDER_SCENE_KEYS) | {'layout_top'},         # layout_top 只给预览「实景图」视图,导出不加载
        'group': set(wx.RENDER_GROUP_KEYS),
        'actor': set(wx.RENDER_ACTOR_KEYS),
        r'a\.data': set(wx.RENDER_ACTOR_KEYS),
        'obj': set(wx.RENDER_OBJECT_KEYS),
        'data': set(wx.RENDER_OBJECT_KEYS) | set(wx.RENDER_ACTOR_KEYS),
        r'screen\.data': set(wx.RENDER_OBJECT_KEYS),
        'k': set(wx.RENDER_KEYFRAME_KEYS),
        'shot': set(wx.RENDER_CAMERA_KEYS),
    }
    missing = {}
    for var, keys in allowed.items():
        found = set(re.findall(r'(?<![\w.])%s\??\.([a-z_]+)' % var, src))
        extra = found - keys
        if extra:
            missing[var] = sorted(extra)
    assert not missing, f'渲染器读了未登记进视频指纹的字段: {missing}'


def test_director_sha_uses_r1_and_keeps_legacy_records(project):
    from modules import director as dm
    from modules.whitebox_export import fingerprint
    episode, g = groups_of(project)
    assert dm.group_sha(episode, g['grp1']) == fingerprint(episode, g['grp1'])
    legacy = dm._legacy_group_sha(episode, g['grp1'])
    assert dm.group_sha_matches(legacy, episode, g['grp1']) and dm.group_sha_matches(dm.group_sha(episode, g['grp1']), episode, g['grp1'])
    doc = {'approvals': {'grp1': {'sha': dm.group_sha(episode, g['grp1'])}, 'grp2': {'sha': dm._legacy_group_sha(episode, g['grp2'])}}}
    restamped = copy.deepcopy(episode)
    for x in restamped['groups']:
        x['cameras'][0]['source_fingerprint'] = 'v2:restamped'      # 上游只改文字、重打审查指纹
    assert dm.approvals_public(doc, episode)['grp2']['stale'] is False   # 旧口径记录:白模一字未变照认
    pub = dm.approvals_public(doc, restamped)
    assert pub['grp1']['stale'] is False                             # r1 批准不因重打审查指纹而过期
    assert pub['grp2']['stale'] is True                              # 旧口径整份哈希含审查指纹,重打后无从还原(只对新记录修好)
    moved = copy.deepcopy(episode)
    next(x for x in moved['groups'] if x['group_id'] == 'grp1')['actors'][0]['keyframes'][1]['position'] = [4, 0, 0]
    assert dm.approvals_public(doc, moved)['grp1']['stale'] is True


def test_overrides_apply_before_continuity_inherit(project):
    from modules.whitebox_export import fingerprint
    plan2 = project / 'directing/ep01/whitebox_plans/grp2.json'
    data = json.loads(plan2.read_text())
    data['continuity'] = {'actors': 'inherit'}
    write(plan2, data)
    write(project / 'directing/ep01/whitebox/director/overrides.json', {'schema': 'director_overrides.v1', 'groups': {'grp1': {
        'actors': {'CHAR-1': {'keyframes': [stand(0, 0), stand(2, 0.0, 3.0)]}}}}})
    episode, g = groups_of(project)
    assert g['grp2']['actors'][0]['keyframes'][0]['position'] == [0.0, 0, 3.0]   # 后组继承的是覆盖后的前组终点
    # 固化:覆盖层原样写进计划(整数 / 浮点写法不同)并删除覆盖层 → 两组指纹都不变
    fp = {k: fingerprint(episode, v) for k, v in g.items()}
    plan1 = project / 'directing/ep01/whitebox_plans/grp1.json'
    data = json.loads(plan1.read_text())
    data['actors'][0]['keyframes'] = [stand(0, 0), {'t': 2, 'position': [0, 0, 3], 'pose': 'stand', 'yaw': 0}]
    write(plan1, data)
    (project / 'directing/ep01/whitebox/director/overrides.json').unlink()
    episode, g = groups_of(project)
    assert {k: fingerprint(episode, v) for k, v in g.items()} == fp


def test_review_stale_group_is_viewable_but_not_exportable(project, monkeypatch, capsys):
    import importlib.util
    import sys
    from modules.whitebox_camera import placement_fingerprint
    plan = project / 'directing/ep01/whitebox_plans/grp1.json'
    data = json.loads(plan.read_text())
    data['cameras'][0]['placement_fingerprint'] = placement_fingerprint(project, 'ep01', 'grp1')
    write(plan, data)
    write(project / 'directing/ep01/shots/sh1/camera.json', {'movement': 'static', 'whitebox_contract': {'version': 1}})
    from modules.whitebox_camera import source_fingerprint
    shot = json.loads((project / 'directing/ep01/shot_list.json').read_text())['shots'][0]
    data['cameras'][0]['source_fingerprint'] = source_fingerprint(project, 'ep01', shot)
    write(plan, data)
    assert not compile_episode(project, 'ep01')['errors']
    data['props'][0]['position'] = [2, .9, 0]          # 摆位变了、没复核 → placement 审查失效
    write(plan, data)
    episode = compile_episode(project, 'ep01')
    group = next(g for g in episode['groups'] if g['group_id'] == 'grp1')
    assert 'placement changed' in group['review_errors'][0] and [e['group_id'] for e in episode['errors']] == ['grp1']
    monkeypatch.syspath_prepend(str(ROOT / 'code'))
    spec = importlib.util.spec_from_file_location('render_whitebox_fp_test', ROOT / 'code/render_whitebox.py')
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(cli, 'ensure_videos', lambda *a, **k: pytest.fail('exported a review-stale group'))
    monkeypatch.setattr(sys, 'argv', ['render_whitebox', '--project', 'demo', '--out-root', str(project), '--ep', 'ep01'])
    assert cli.main() == 1
    head = json.loads(capsys.readouterr().out.splitlines()[0])
    assert head['groups'] == 1 and head['source_groups'] == 2 and head['errors'][0]['group_id'] == 'grp1'
