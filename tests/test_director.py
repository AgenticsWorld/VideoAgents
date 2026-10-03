"""导演台台账 / 版本快照 / 批次回收 —— docs/whitebox.md「导演台」(2026-09-13)。"""
import json
from pathlib import Path

import pytest

from modules import director as dm
from modules.whitebox import compile_episode


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')


@pytest.fixture
def project(tmp_path):
    base = tmp_path / 'demo'
    write(base / 'settings.json', {'output': {'aspect_preset': 'youtube'}})
    write(base / 'bible/scenes/SCN-1/whitebox.json', {'schema_version': 'whitebox_scene.v1', 'scene_id': 'SCN-1',
                                                      'dimensions_m': [8, 3, 4.5], 'objects': []})
    write(base / 'assets/concepts/scenes/SCN-1/layout.json', {
        'scene_name': 'Room', 'landmarks': [{'id': 'door', 'xy': [.1, .5]}, {'id': 'desk', 'xy': [.7, .5]}],
        'views': [{'tile': 1, 'camera_from': 'door', 'looking_at': 'desk'}]})
    write(base / 'directing/ep01/shot_list.json', {
        'shots': [{'shot_id': 'sh1', 'scene_id': 'SCN-1', 'scene_no': 'S1', 'duration_s': 4, 'characters': ['CHAR-1'], 'view_tile': 1}],
        'generation_groups': [{'group_id': 'grp1', 'scene_id': 'SCN-1', 'scene_no': 'S1', 'shots': ['sh1'], 'total_duration_s': 4,
                               'characters_union': ['CHAR-1'],
                               'blocking_map': {'characters': [{'id': 'CHAR-1', 'label': '甲', 'start': {'landmark': 'door'}, 'end': {'landmark': 'desk'}}]}}]})
    write(base / 'directing/ep01/whitebox_plans/grp1.json', {
        'schema_version': 'whitebox_plan.v1', 'group_id': 'grp1',
        'actors': [{'id': 'CHAR-1', 'label': '甲', 'size_m': [.5, 1.7, .4],
                    'keyframes': [{'t': 0, 'position': [-1, 0, 0], 'yaw': 0, 'pose': 'stand'}, {'t': 4, 'position': [1, 0, 0], 'yaw': 0, 'pose': 'stand'}]}],
        'cameras': [{'shot_id': 'sh1', 'start': 0, 'duration_s': 4, 'movement': 'static',
                     'keyframes': [{'t': 0, 'position': [0, 1.5, 4], 'target': [0, 1, 0], 'fov': 40}, {'t': 4, 'position': [0, 1.5, 4], 'target': [0, 1, 0], 'fov': 40}]}]})
    return base


def test_note_lifecycle_and_message(project):
    base, ep = project, 'ep01'
    doc = dm.load_ledger(base, ep)
    n = dm.add_note(doc, dm.make_note('grp1', {'kind': 'actor', 'id': 'CHAR-1', 'label': '甲'}, '往北挪 1 米', t=2, shot_id='sh1', view='top'))
    assert n['id'] == 'N-0001' and n['status'] == 'draft'
    with pytest.raises(ValueError):
        dm.make_note('grp1', {'kind': 'ghost', 'id': 'x'}, 'x')
    with pytest.raises(ValueError):
        dm.make_note('grp1', {'kind': 'actor', 'id': 'CHAR-1'}, '   ')
    dm.save_ledger(base, ep, doc)
    assert dm.ledger_path(base, ep).is_file()
    episode = compile_episode(base, ep)
    prep = dm.prepare_batch(base, ep, doc, episode, group_ids=['grp1'])
    assert prep['group_ids'] == ['grp1'] and [x['id'] for x in prep['notes']] == ['N-0001']
    msg = prep['message']
    assert '[N-0001]' in msg and '人物 甲(CHAR-1)' in msg and 't=2.0s' in msg and '--compile-only grp1' in msg
    assert 'whitebox/director/' in msg  # 禁改台账
    with pytest.raises(ValueError):
        dm.prepare_batch(base, ep, doc, episode, group_ids=['grp9'])


def test_commit_baseline_and_reconcile(project):
    base, ep = project, 'ep01'
    doc = dm.load_ledger(base, ep)
    dm.add_note(doc, dm.make_note('grp1', {'kind': 'camera', 'id': 'sh1', 'label': 'sh1'}, '机高降到 1.2m'))
    episode = compile_episode(base, ep)
    prep = dm.prepare_batch(base, ep, doc, episode)
    batch = dm.commit_batch(base, ep, doc, episode, prep, run_id='run-1')
    assert batch['id'] == 'B-0001' and batch['status'] == 'running' and batch['base_v'] == {'grp1': 1}
    assert [v['label'] for v in dm.versions_of(doc, 'grp1')] == ['提交前基线']
    assert doc['notes'][0]['status'] == 'submitted' and doc['notes'][0]['batch_id'] == 'B-0001'
    snap = dm.load_version(base, ep, 'grp1', 1)
    assert snap['schema'] == dm.VERSION_SCHEMA and snap['group']['group_id'] == 'grp1' and snap['scene']['scene_id'] == 'SCN-1'
    # 运行还在跑:不回收
    assert dm.reconcile(base, ep, doc, episode, lambda r: True) == [] and batch['status'] == 'running'
    # 结束但白模没变:nochange + failed
    assert dm.reconcile(base, ep, doc, episode, lambda r: False) == []
    assert batch['status'] == 'nochange' and doc['notes'][0]['status'] == 'failed' and '没有变化' in doc['notes'][0]['error']
    # 再提交一次,Agent 改了计划:新版本 + applied
    doc['notes'][0]['status'] = 'draft'
    prep = dm.prepare_batch(base, ep, doc, episode)
    b2 = dm.commit_batch(base, ep, doc, episode, prep, run_id='run-2')
    plan_p = base / 'directing/ep01/whitebox_plans/grp1.json'
    plan = json.loads(plan_p.read_text())
    plan['cameras'][0]['keyframes'][0]['position'][1] = 1.2
    plan_p.write_text(json.dumps(plan))
    ep2 = compile_episode(base, ep)
    created = dm.reconcile(base, ep, doc, ep2, lambda r: False)
    assert [c['v'] for c in created] == [2] and b2['status'] == 'done' and b2['versions'] == {'grp1': 2}
    assert doc['notes'][0]['status'] == 'applied' and doc['notes'][0]['version_after'] == 2
    assert dm.current_version(doc, 'grp1') == 2
    s = dm.summary(doc)
    assert s['applied'] == 1 and s['versions'] == 2 and s['running'] == 0
    # 只有 draft 可删
    with pytest.raises(ValueError):
        dm.delete_note(doc, 'N-0001')


def test_decided_issue_joins_batch(project):
    base, ep = project, 'ep01'
    plan_p = base / 'directing/ep01/whitebox_plans/grp1.json'
    plan = json.loads(plan_p.read_text())
    plan['issues'] = [{'issue_id': 'WBI-ep01-grp1-001', 'kind': 'facing', 'severity': 'advisory', 'question': '朝向以谁为准?',
                       'provisional': '按坐标', 'options': [{'id': 'A', 'label': '信坐标'}, {'id': 'B', 'label': '信文字'}],
                       'recommended': 'A', 'shots': ['sh1'], 'actors': ['CHAR-1']}]
    plan_p.write_text(json.dumps(plan, ensure_ascii=False))
    from modules.whitebox_issues import decide
    decide(base, ep, 'WBI-ep01-grp1-001', 'B', '', 'user:page')
    episode = compile_episode(base, ep)
    doc = dm.load_ledger(base, ep)
    prep = dm.prepare_batch(base, ep, doc, episode, group_ids=['grp1'])   # 没有注释,只有裁决
    assert prep['notes'] == [] and list(prep['issues']) == ['grp1']
    assert 'WBI-ep01-grp1-001' in prep['message'] and '选「B. 信文字」' in prep['message']
    # 提交本集(不指定组):只回答了提问、没注释没直改的组同样进批次
    prep_all = dm.prepare_batch(base, ep, doc, episode)
    assert prep_all['group_ids'] == ['grp1'] and list(prep_all['issues']) == ['grp1'] and prep_all['notes'] == []


def test_avatar_rel(project):
    d = project / 'assets/concepts/characters/CHAR-1'
    d.mkdir(parents=True)
    (d / 'sheet.png').write_bytes(b'x')
    assert dm.avatar_rel(project, 'CHAR-1') == 'assets/concepts/characters/CHAR-1/sheet.png'
    assert dm.avatar_rel(project, 'EXTRA-1') is None


def test_overrides_merge_submit_and_consolidate(project):
    base, ep = project, 'ep01'
    ov = dm.load_overrides(base, ep)
    keys = [{'t': 0, 'position': [-1, 0, 1], 'yaw': 0.5, 'pose': 'stand'}, {'t': 4, 'position': [2, 0, 1], 'yaw': 0.5, 'pose': 'stand'}]
    g = dm.set_group_overrides(ov, 'grp1', {'actors': {'CHAR-1': {'keyframes': keys}},
                                             'cameras': {'sh1': {'keyframes': [{'t': 0, 'position': [0, 1.2, 4], 'target': [0, 1, 0], 'fov': 40},
                                                                               {'t': 4, 'position': [0, 1.2, 4], 'target': [0, 1, 0], 'fov': 40}]}}})
    assert set(g) >= {'actors', 'cameras'}
    dm.save_overrides(base, ep, ov)
    assert dm.overrides_path(base, ep).is_file()
    # 编译合并:位置/机高来自覆盖层,并打 overrides 标记
    e = compile_episode(base, ep)
    grp = e['groups'][0]
    assert grp['actors'][0]['keyframes'][1]['position'] == [2, 0, 1] and grp['cameras'][0]['keyframes'][0]['position'][1] == 1.2
    assert grp['overrides'] == {'actors': ['CHAR-1'], 'cameras': ['sh1']} and grp['cameras'][0].get('overridden')
    plain = compile_episode(base, ep, apply_overrides=False)
    assert plain['groups'][0]['actors'][0]['keyframes'][1]['position'] != [2, 0, 1] and 'overrides' not in plain['groups'][0]
    # 无效覆盖只警告不套用
    ov2 = dm.load_overrides(base, ep)
    dm.set_group_overrides(ov2, 'grp1', {'actors': {'CHAR-1': {'keyframes': [{'t': 0, 'position': [0, 0, 0]}, {'t': 3, 'position': [1, 0, 0]}]}}})
    dm.save_overrides(base, ep, ov2)
    e_bad = compile_episode(base, ep)
    assert any('覆盖层 actors/CHAR-1 无效' in w for w in e_bad['groups'][0]['warnings']) and 'actors' not in e_bad['groups'][0].get('overrides', {})
    dm.save_overrides(base, ep, ov)   # 恢复有效覆盖
    e = compile_episode(base, ep)
    # 没有注释也能提交:覆盖层本身就是改动;指令附覆盖层 JSON
    doc = dm.load_ledger(base, ep)
    prep = dm.prepare_batch(base, ep, doc, e)
    assert prep['group_ids'] == ['grp1'] and 'actors/CHAR-1' in prep['message'] and '"position":[2.0,0.0,1.0]' in prep['message']
    batch = dm.commit_batch(base, ep, doc, e, prep, run_id='run-ov')
    assert batch['override_objects'] == {'grp1': ['actors/CHAR-1', 'cameras/sh1']}
    # Agent 只固化了人物、没固化机位 → 人物覆盖自动清掉,机位覆盖保留并记 overrides_pending
    plan_p = base / 'directing/ep01/whitebox_plans/grp1.json'
    plan = json.loads(plan_p.read_text())
    plan['actors'][0]['keyframes'] = keys
    plan_p.write_text(json.dumps(plan))
    e2 = compile_episode(base, ep)
    plain2 = compile_episode(base, ep, apply_overrides=False)
    created = dm.reconcile(base, ep, doc, e2, lambda r: False, plain_episode=plain2)
    ov3 = dm.load_overrides(base, ep)['groups'].get('grp1', {})
    assert 'actors' not in ov3 and 'sh1' in ov3.get('cameras', {})
    assert batch['overrides_pending'] == {'grp1': ['cameras/sh1']}
    # 画面没变(覆盖层早已生效)→ 不落新版本,但固化了对象 → 批次 done
    assert created == [] and batch['status'] == 'done'
    # 单对象删除 + 整组清空
    dm.clear_group_overrides(ov3_doc := dm.load_overrides(base, ep), 'grp1', 'cameras', 'sh1')
    assert 'grp1' not in ov3_doc['groups']
    dm.save_overrides(base, ep, ov3_doc)
    assert not dm.overrides_path(base, ep).is_file()


def test_routing_marks_approvals(project):
    base, ep = project, 'ep01'
    doc = dm.load_ledger(base, ep)
    with pytest.raises(ValueError):
        dm.make_note('grp1', {'kind': 'scene', 'id': 'SCN-1'}, 'x', target_agent='nobody/agent')
    dm.add_note(doc, dm.make_note('grp1', {'kind': 'scene', 'id': 'SCN-1', 'label': '场景'}, '东墙往外挪 0.5 m', target_agent='05-scenes/scene-modeling'))
    dm.add_note(doc, dm.make_note('grp1', {'kind': 'actor', 'id': 'CHAR-1', 'label': '甲'}, '走慢一点'))
    assert dm.draft_agents(doc) == ['07-directing/whitebox-staging', '05-scenes/scene-modeling']
    episode = compile_episode(base, ep)
    p_wb = dm.prepare_batch(base, ep, doc, episode)
    p_sc = dm.prepare_batch(base, ep, doc, episode, agent='05-scenes/scene-modeling')
    assert [n['id'] for n in p_wb['notes']] == ['N-0002'] and [n['id'] for n in p_sc['notes']] == ['N-0001']
    assert '发给 场景建模' in p_sc['message'] and '东墙' in p_sc['message'] and p_sc['issues'] == {} and p_sc['overrides'] == {}
    b = dm.commit_batch(base, ep, doc, episode, p_sc, run_id='run-sc', agent='05-scenes/scene-modeling')
    assert b['agent'] == '05-scenes/scene-modeling'
    dm.reconcile(base, ep, doc, episode, lambda r: False)
    n1 = dm.find_note(doc, 'N-0001')
    assert b['status'] == 'done' and n1['status'] == 'applied' and n1.get('verified') is False
    # 批准:记指纹,白模变了即过期
    g = episode['groups'][0]
    dm.set_approval(doc, 'grp1', dm.group_sha(episode, g), True)
    assert dm.approvals_public(doc, episode)['grp1']['stale'] is False
    plan_p = base / 'directing/ep01/whitebox_plans/grp1.json'
    plan = json.loads(plan_p.read_text())
    plan['actors'][0]['keyframes'][1]['position'] = [1.5, 0, 0]
    plan_p.write_text(json.dumps(plan))
    assert dm.approvals_public(doc, compile_episode(base, ep))['grp1']['stale'] is True
    dm.set_approval(doc, 'grp1', None, False)
    assert 'grp1' not in doc['approvals'] and dm.summary(doc)['approved'] == 0
    # 定位标记
    md = dm.load_marks(base, ep)
    out = dm.set_scene_marks(md, 'SCN-1', [{'name': '门口', 'position': [-3, 0, 1]}, {'id': 'desk', 'name': '桌边', 'position': [2, 0, 0.5]}])
    assert [m['id'] for m in out] == ['M01', 'desk']
    with pytest.raises(ValueError):
        dm.set_scene_marks(md, 'SCN-1', [{'name': '', 'position': [0, 0, 0]}])
    dm.save_marks(base, ep, md)
    assert dm.load_marks(base, ep)['scenes']['SCN-1'][1]['name'] == '桌边'


def test_require_plan_blocks_unstaged_groups(project):
    # 没有 whitebox_plans/<grp>.json 的组只是宿主自动推断的草稿:注释/覆盖层/批准/存版/提交一律拒收
    base = project
    dm.require_plan(base, 'ep01', ['grp1', 'grp1'])
    with pytest.raises(ValueError, match='grp9'):
        dm.require_plan(base, 'ep01', ['grp1', 'grp9'])
    (base / 'directing/ep01/whitebox_plans/grp1.json').unlink()
    with pytest.raises(ValueError, match='尚未白模调度'):
        dm.require_plan(base, 'ep01', ['grp1'])
