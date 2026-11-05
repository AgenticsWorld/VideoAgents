"""白模道具扩展(2026-11-05):锥体、ghost 半透明、挂手 attach、剧情道具覆盖、道具图例、静帧 evt 格、桌面包带 docs。"""
import copy
import json
import math
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from modules.whitebox import (ATTACH_JUMP_M, compile_episode, hand_anchor, prop_coverage, prop_state, sample)

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'apps/web/static'


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')


HANDS = {'right_hand': [-.12, .95, .3], 'left_hand': [.2, .8, .05]}
KEYS = [
    {'t': 0, 'position': [0, 0, 0], 'pose': 'stand', 'yaw': 0, **HANDS},
    {'t': 1, 'position': [1, 0, .5], 'pose': 'stand', 'yaw': 1.2, 'bend': .6, 'torso_yaw': .4,
     'right_hand': [-.3, 1.2, .5], 'left_hand': [.2, .8, .05]},
    {'t': 2, 'position': [1, 0, .5], 'pose': 'sit', 'yaw': -2, 'right_hand': [-.5, .1, 1.5], 'left_hand': [.2, .8, .05]},
    {'t': 3, 'position': [1, .5, .5], 'pose': 'lie', 'yaw': .5, 'body_roll': .3, **HANDS},
    {'t': 4, 'position': [1, .5, .5], 'pose': 'prone', 'yaw': .5, **HANDS},
]
CAMERA = {'shot_id': 'sh1', 'start': 0, 'duration_s': 4, 'movement': 'static',
          'keyframes': [{'t': 0, 'position': [0, 1.2, 6], 'target': [0, .8, 0], 'fov': 40},
                        {'t': 4, 'position': [0, 1.2, 6], 'target': [0, .8, 0], 'fov': 40}]}


@pytest.fixture
def project(tmp_path):
    base = tmp_path / 'demo'
    scene = 'SCN-1'
    write(base / 'assets/concepts/scenes' / scene / 'layout.json', {
        'scene_name': 'Street', 'landmarks': [{'id': 'door', 'xy': [.1, .5]}, {'id': 'desk', 'xy': [.7, .5]}],
        'views': [{'tile': 1, 'camera_from': 'door', 'looking_at': 'desk'}]})
    write(base / 'bible/scenes' / scene / 'whitebox.json', {'dimensions_m': [10, 3, 8], 'inferred': False, 'objects': [
        {'id': 'well', 'ref': 'PROP-WELL', 'size_m': [1, .75, 1], 'xy': [.7, .5]}]})
    shot = {'shot_id': 'sh1', 'scene_id': scene, 'duration_s': 4, 'characters': ['CHAR-1'], 'view_tile': 1,
            't_range_s': [10, 14], 'props': ['PROP-CONE', 'PROP-WAND', 'PROP-WELL', 'PROP-SET'],
            'content': '球一歪、滑落(10.0–10.5);她一挥(10.3–10.8,与滑落重叠),泡泡兜回(10.8–11.7)。'}
    group = {'group_id': 'grp1', 'scene_id': scene, 'scene_no': 'S1', 'shots': ['sh1'], 'total_duration_s': 4,
             'characters_union': ['CHAR-1'], 'blocking_map': {'characters': [
                 {'id': 'CHAR-1', 'label': 'Boy', 'start': {'landmark': 'door'}, 'end': {'landmark': 'desk'}}]}}
    write(base / 'directing/ep01/shot_list.json', {'shots': [shot], 'generation_groups': [group]})
    write(base / 'bible/props.json', {'props': [
        {'id': 'PROP-CONE', 'name': '冰淇淋球', 'is_plot_prop': True},
        {'id': 'PROP-WAND', 'name': '泡泡圈', 'is_plot_prop': True},
        {'id': 'PROP-WELL', 'name': '井', 'is_plot_prop': True},
        {'id': 'PROP-SET', 'name': '路边花箱', 'is_plot_prop': False}]})
    return base


def plan_path(base):
    return base / 'directing/ep01/whitebox_plans/grp1.json'


def cone(**extra):
    return {'id': 'cone', 'ref': 'PROP-CONE', 'label': '甜筒', 'shape': 'cone', 'size_m': [.05, .11, .05], 'pitch': math.pi,
            'color': '#c8a165', 'attach': {'actor': 'CHAR-1', 'hand': 'right_hand', 'offset': [.02, .06, -.01]}, **extra}


def compiled(base, plan):
    write(plan_path(base), plan)
    return compile_episode(base, 'ep01')


def error_of(base, plan):
    result = compiled(base, plan)
    assert result['errors'], 'expected a compile error'
    return result['errors'][0]['error']


def test_cone_ghost_and_attach_compile(project):
    bubble = {'id': 'bubble', 'label': '小泡泡', 'shape': 'sphere', 'material': 'ghost', 'size_m': [.3, .3, .3],
              'keyframes': [{'t': 0, 'position': [0, 1, 0], 'visible': False}, {'t': 4, 'position': [0, 2, 0]}]}
    result = compiled(project, {'actors': [{'id': 'CHAR-1', 'size_m': [.36, 1.07, .28], 'keyframes': KEYS}],
                                'props': [cone(), bubble], 'cameras': [CAMERA]})
    assert not result['errors'], result['errors']
    props = {p['id']: p for p in result['groups'][0]['props']}
    assert props['cone']['shape'] == 'cone' and 'position' not in props['cone']   # 只挂手、无关键帧可省 position
    assert props['bubble']['material'] == 'ghost'


@pytest.mark.parametrize('mutate,needle', [
    (lambda p: p['props'][0].update(material='glass'), 'material'),
    (lambda p: p['props'][0].update(shape='torus'), 'primitive'),
    (lambda p: p['props'][0].update(ref=''), 'ref'),
    (lambda p: p['props'][0]['attach'].update(actor='CHAR-9'), 'attach.actor'),
    (lambda p: p['props'][0]['attach'].update(hand='tail'), 'hand'),
    (lambda p: p['props'][0]['attach'].update(offset=[0, 1]), 'offset'),
    (lambda p: p['actors'][0].update(keyframes=[{k: v for k, v in key.items() if k != 'right_hand'} for key in KEYS]),
     'no right_hand channel'),
    (lambda p: p['props'][0].update(keyframes=[{'t': 0, 'position': [0, 1, 0], 'attached': 'no'},
                                               {'t': 4, 'position': [0, 1, 0]}]), 'attached must be a boolean'),
    (lambda p: p['props'][0].pop('attach') and p['props'][0].update(
        position=[0, 1, 0], keyframes=[{'t': 0, 'position': [0, 1, 0], 'attached': False}, {'t': 4, 'position': [0, 1, 0]}]),
     'needs prop.attach'),
    (lambda p: p.update(not_modeled=[{'id': 'PROP-WAND', 'reason': ' '}]), 'not_modeled'),
])
def test_prop_extension_validation(project, mutate, needle):
    plan = {'actors': [{'id': 'CHAR-1', 'size_m': [.36, 1.07, .28], 'keyframes': copy.deepcopy(KEYS)}],
            'props': [cone()], 'cameras': [CAMERA]}
    mutate(plan)
    assert needle in error_of(project, plan)


def test_scene_objects_keep_original_shapes(project):
    write(project / 'bible/scenes/SCN-1/whitebox.json', {'dimensions_m': [10, 3, 8], 'objects': [
        {'id': 'hat', 'shape': 'cone', 'size_m': [.3, .2, .3], 'xy': [.5, .5]}]})
    assert 'Unsupported primitive' in compile_episode(project, 'ep01')['errors'][0]['error']


def test_attached_prop_matches_renderer(project):
    """Python hand_anchor/prop_state 与 whitebox-renderer.js 场景图逐时刻一致(含 bend、torso_yaw、坐、卧、趴、够不着)。"""
    if not shutil.which('node'):
        pytest.skip('Node unavailable')
    result = compiled(project, {'actors': [{'id': 'CHAR-1', 'size_m': [.36, 1.07, .28], 'keyframes': KEYS}],
                                'props': [cone()], 'cameras': [CAMERA]})
    assert not result['errors'], result['errors']
    group = result['groups'][0]
    by_id = {a['id']: a for a in group['actors']}
    times = [0, .5, 1, 1.5, 2, 2.5, 3, 3.5, 4]
    script = f'''
import * as THREE from {json.dumps((STATIC / 'vendor/three/three.module.js').as_uri())};
import {{WhiteboxRenderer}} from {json.dumps((STATIC / 'whitebox-renderer.js').as_uri())};
const r=Object.create(WhiteboxRenderer.prototype);
Object.assign(r,{{width:960,height:540,scene:null,controls:null,
  camera:new THREE.PerspectiveCamera(),overview:new THREE.PerspectiveCamera(),top:new THREE.OrthographicCamera()}});
r.load({json.dumps(result['scenes']['SCN-1'])},{json.dumps(group)});
const out=[];
for(const t of {json.dumps(times)}){{r.setTime(t);const m=r.props[0].mesh;out.push({{p:m.getWorldPosition(new THREE.Vector3()).toArray(),
  attached:r.props[0].attachedNow,visible:m.visible}});}}
console.log(JSON.stringify(out));r.disposeScene();
'''
    js = json.loads(subprocess.check_output(['node', '--input-type=module', '-e', script], text=True, stderr=subprocess.DEVNULL))
    for t, row in zip(times, js):
        center, attached, visible = prop_state(group['props'][0], by_id, t)
        assert attached and row['attached'] and visible == row['visible'] is True
        assert row['p'] == pytest.approx(center, abs=1e-6), t
    # 够不着时手停在伸直方向:挂点离肩恰为两段臂长
    from modules.whitebox import ARM_SEGMENT, HAND_SIDES, shoulder_point
    far = sample(by_id['CHAR-1']['keyframes'], 2)
    point, rot = hand_anchor(by_id['CHAR-1'], far, 'right_hand')
    local = [far['right_hand'][0], far['right_hand'][1], far['right_hand'][2]]
    assert math.dist(local, shoulder_point([.36, 1.07, .28], far, dict(HAND_SIDES)['right_hand'])) > 2 * ARM_SEGMENT * 1.07


def test_detach_and_regrab_with_jump_warning(project):
    actor = {'id': 'CHAR-1', 'size_m': [.36, 1.07, .28], 'keyframes': [
        {'t': 0, 'position': [0, 0, 0], 'pose': 'stand', 'yaw': .7, **HANDS},
        {'t': 4, 'position': [0, 0, 0], 'pose': 'stand', 'yaw': .7, **HANDS}]}
    by_id = {'CHAR-1': {**actor, 'kind': 'person'}}
    anchor = prop_state(cone(), by_id, 1)[0]
    keys = [{'t': 0, 'position': [9, 9, 9]},
            {'t': 1, 'position': anchor, 'attached': False},              # 脱手帧 = 挂点:无跳位
            {'t': 2, 'position': [anchor[0], .1, anchor[2]], 'attached': False},
            {'t': 3, 'position': [5, 5, 5], 'attached': True},           # 回手帧离挂点很远:WARN
            {'t': 4, 'position': [5, 5, 5]}]
    result = compiled(project, {'actors': [actor], 'props': [cone(keyframes=keys)], 'cameras': [CAMERA]})
    assert not result['errors'], result['errors']
    group = result['groups'][0]
    jumps = [w for w in group['warnings'] if '跳位' in w]
    assert len(jumps) == 1 and 't=3s 回到手上处' in jumps[0]
    expected = prop_state(cone(), by_id, 3)[0]
    assert all(f'{v:.3f}' in jumps[0] for v in expected)
    prop = group['props'][0]
    assert prop_state(prop, by_id, .5)[1] is True and prop_state(prop, by_id, .5)[0] == pytest.approx(prop_state(cone(), by_id, .5)[0])
    mid, attached, visible = prop_state(prop, by_id, 1.5)
    assert (attached, visible) == (False, True) and mid == pytest.approx([anchor[0], (anchor[1] + .1) / 2, anchor[2]])
    assert prop_state(prop, by_id, 3.5)[1] is True
    assert ATTACH_JUMP_M < math.dist(expected, [5, 5, 5])


def test_prop_coverage_statuses_and_fingerprint_exclusion(project):
    base_plan = {'actors': [{'id': 'CHAR-1', 'size_m': [.36, 1.07, .28], 'keyframes': KEYS}], 'cameras': [CAMERA]}
    # 未调度组(无计划)不判
    assert 'prop_coverage' not in compile_episode(project, 'ep01')['groups'][0]
    group = compiled(project, base_plan)['groups'][0]
    cov = group['prop_coverage']
    # PROP-SET 道具库标为非剧情道具 → 不要求;PROP-WELL 由场景物件 ref 覆盖
    assert {r['id']: r['status'] for r in cov['items']} == {'PROP-CONE': 'missing', 'PROP-WAND': 'missing', 'PROP-WELL': 'modeled'}
    assert cov['missing'] == ['PROP-CONE', 'PROP-WAND'] and '冰淇淋球' in cov['messages'][0]
    plan = {**base_plan, 'props': [cone()], 'not_modeled': [{'id': 'PROP-WAND', 'reason': '整组被她身体挡住'}]}
    group = compiled(project, plan)['groups'][0]
    assert {r['id']: r['status'] for r in group['prop_coverage']['items']} == {
        'PROP-CONE': 'modeled', 'PROP-WAND': 'declared', 'PROP-WELL': 'modeled'}
    assert group['prop_coverage']['missing'] == [] and group['prop_coverage']['items'][1]['reason'] == '整组被她身体挡住'
    # 诊断不进视频指纹与导演台版本指纹
    from modules.director import group_sha
    from modules.whitebox_export import fingerprint
    episode = compile_episode(project, 'ep01')
    g = episode['groups'][0]
    stripped = {k: v for k, v in g.items() if k != 'prop_coverage'}
    altered = {**g, 'prop_coverage': {'items': [], 'missing': ['X'], 'messages': ['x']}}
    assert fingerprint(episode, g) == fingerprint(episode, stripped) == fingerprint(episode, altered)
    assert group_sha(episode, g) == group_sha(episode, stripped) == group_sha(episode, altered)
    # 直接调用:镜头表没有 props 时返回 None
    assert prop_coverage({'shots': ['sh1']}, {'sh1': {}}, {'props': []}, {'objects': []}, {}) is None


def test_cli_reports_prop_coverage_on_third_line(project, monkeypatch, capsys):
    import importlib.util
    import sys
    write(plan_path(project), {'actors': [{'id': 'CHAR-1', 'size_m': [.36, 1.07, .28], 'keyframes': KEYS}], 'cameras': [CAMERA]})
    monkeypatch.syspath_prepend(str(ROOT / 'code'))
    spec = importlib.util.spec_from_file_location('render_whitebox_props_test', ROOT / 'code/render_whitebox.py')
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(sys, 'argv', ['render_whitebox', '--project', 'demo', '--out-root', str(project), '--ep', 'ep01', '--check-only'])
    assert cli.main() == 0   # 只 WARN,不改退出码
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()[:3]]
    assert lines[0]['errors'] == [] and 'issues' in lines[1]
    assert [m['id'] for m in lines[2]['props_coverage']['missing']] == ['PROP-CONE', 'PROP-WAND']
    assert lines[2]['props_coverage_text'].endswith('2 项剧情道具未进白模 -> WARN')


def test_prop_legend_lists_visible_props_only(project):
    from modules.whitebox_refs import (appearing_props, build_block, check_prompt, prop_color_name, prop_legend_rows)
    scoop = {'id': 'scoop', 'ref': 'PROP-CONE', 'label': '冰淇淋球', 'shape': 'sphere', 'size_m': [.07, .07, .07],
             'color': '#a8e6cf', 'position': [0, 1, 0]}
    bubble = {'id': 'bubble', 'label': '小泡泡', 'shape': 'sphere', 'material': 'ghost', 'size_m': [.3, .3, .3], 'position': [.5, 1, 0]}
    behind = {'id': 'crate', 'label': '箱子', 'size_m': [.4, .4, .4], 'position': [0, .2, 20]}           # 在机位身后
    hidden = {'id': 'note', 'label': '纸条', 'size_m': [.1, .01, .1], 'position': [0, 1, 0], 'shot_ids': []}
    in_hand = cone(shot_ids=['sh1'])
    actor = {'id': 'CHAR-1', 'size_m': [.36, 1.07, .28], 'keyframes': [
        {'t': 0, 'position': [0, 0, 0], 'pose': 'stand', 'yaw': 0, **HANDS},
        {'t': 4, 'position': [0, 0, 0], 'pose': 'stand', 'yaw': 0, **HANDS}]}
    result = compiled(project, {'actors': [actor], 'props': [scoop, bubble, behind, hidden, in_hand], 'cameras': [CAMERA]})
    assert not result['errors'], result['errors']
    group, render = result['groups'][0], result['render']
    assert appearing_props(group, render) == ['scoop', 'bubble', 'cone']
    rows = prop_legend_rows(group, render)
    assert rows == ['mint-green sphere = 冰淇淋球 (scoop)',
                    'translucent pale-blue sphere = 小泡泡 (bubble, a see-through effect, not a solid object)',
                    f"{prop_color_name('#c8a165')} cone = 甜筒 (cone)"]
    # 持有人被本镜 visible_actor_ids 排除:挂在他手上的道具也不进图例
    group2 = copy.deepcopy(group)
    group2['cameras'][0]['visible_actor_ids'] = []
    assert 'cone' not in appearing_props(group2, render)
    plan = {'videos': ['assets/whitebox/ep01/grp1/camera.mp4'], 'camera': 'assets/whitebox/ep01/grp1/camera.mp4',
            'group': group, 'render': render, 'cast': None, 'props_shown': ['scoop', 'bubble', 'cone']}
    block = build_block(plan)
    assert all(row in block for row in rows)
    legend_only = block.replace('; ' + rows[0], '')
    _, warns = check_prompt({'video_refs': plan['videos'], 'video_prompt': legend_only + ' Shot 1: x. Global constraints: No whitebox look.'},
                            plan, 'grp1')
    assert any('图例缺白模里出现的道具' in w and rows[0] in w for w in warns)
    assert prop_color_name('#e63946') == 'red' and prop_color_name('#a7b1b8') == 'grey'


def test_stills_event_times_follow_content_timecodes_and_prop_motion():
    from modules.whitebox_stills import TIMECODE_RE, event_times, sample_times
    assert TIMECODE_RE.findall('滑落(4.0–4.5);一挥(4.3–4.8,与(球)重叠)(6.0 - 7.0 秒)(第3–5镜)') == [('4.0', '4.5'), ('6.0', '7.0')]
    camera = {'shot_id': 'sh2', 'start': 4, 'duration_s': 3, 'keyframes': [{'t': 0}, {'t': 3}]}
    shot = {'t_range_s': [4.0, 7.0], 'content': '一歪、滑落(4.0–4.5);一蘸(4.3–4.8,与球重叠)。兜回(4.8–5.7)。远处(1.0–2.0)'}
    taken = sample_times(camera)
    assert event_times(camera, {'props': []}, shot, taken) == [4.25, 4.55, 5.25]   # 区间中点;(1.0–2.0)不在本镜 t_range_s 内
    group = {'props': [{'id': 'scoop', 'keyframes': [
        {'t': 0, 'position': [0, 1, 0]}, {'t': 4.0, 'position': [0, 1, 0]}, {'t': 4.5, 'position': [0, .3, 0], 'attached': False},
        {'t': 7, 'position': [0, .3, 0]}]}]}
    events = event_times(camera, group, None, taken)
    assert events == [4.25, 4.5]
    many = event_times(camera, group, shot, taken)
    assert len(many) <= 3 and all(abs(t - u) >= .1 for t in many for u in taken)


def test_desktop_package_ships_docs_referenced_by_agents():
    pkg = json.loads((ROOT / 'apps/desktop/package.json').read_text(encoding='utf-8'))
    entry = next(e for e in pkg['build']['extraResources'] if e.get('to') == 'backend/docs')
    assert entry['from'] == '../../docs' and '**/*.md' in entry['filter']
    refs = set()
    for soul in ('07-directing/whitebox-staging', '05-scenes/scene-modeling'):
        refs.update(re.findall(r'docs/[A-Za-z0-9_.\-]+\.md', (ROOT / 'agents' / soul / 'SOUL.md').read_text(encoding='utf-8')))
    assert 'docs/whitebox.md' in refs
    missing = sorted(r for r in refs if not (ROOT / r).is_file())
    assert not missing, f'SOUL 引用的规约文档不存在: {missing}'
