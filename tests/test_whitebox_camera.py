import copy
import json
from pathlib import Path

from modules.whitebox_camera import check_camera, check_matches, source_fingerprint, placement_fingerprint


def camera_fixture(tmp_path, movement='static'):
    shot = {'shot_id': 'sh1', 'duration_s': 4, 'camera': {'movement': movement, 'focal_mm': 50}}
    folder = tmp_path/'directing/ep01/shots/sh1'; folder.mkdir(parents=True)
    source = {'movement': movement, 'whitebox_contract': {'height_m': [1.1, 1.3], 'pitch_deg': [-1, 1],
                                                       'origin_m': [0, 1.2, 3], 'view_axis': [0, 0, -1]}}
    (folder/'camera.json').write_text(json.dumps(source))
    camera = {'shot_id': 'sh1', 'duration_s': 4, 'movement': movement,
              'keyframes': [{'t': t, 'position': [0, 1.2, 3], 'target': [0, 1.2, 0], 'fov': 26.99146656} for t in [0, 4]]}
    camera['source_fingerprint'] = source_fingerprint(tmp_path, 'ep01', shot)
    return shot, camera, folder, source


def test_locked_camera_rejects_transient_drift_and_wrong_lens(tmp_path):
    shot, camera, _, _ = camera_fixture(tmp_path)
    assert not check_camera(tmp_path, 'ep01', shot, camera)
    camera['keyframes'].insert(1, {**copy.deepcopy(camera['keyframes'][0]), 't': 2, 'position': [.1, 1.2, 3]})
    assert any('locked camera' in e for e in check_camera(tmp_path, 'ep01', shot, camera))
    camera['keyframes'].pop(1); camera['keyframes'][-1]['fov'] = 45
    assert any('FOV' in e for e in check_camera(tmp_path, 'ep01', shot, camera))


def test_composition_edit_invalidates_previously_reviewed_staging(tmp_path):
    shot, camera, folder, _ = camera_fixture(tmp_path)
    (folder/'composition.json').write_text(json.dumps({'perspective': {'mode': 'low_angle'}}))
    assert any('source changed' in e for e in check_camera(tmp_path, 'ep01', shot, camera))


def test_whole_locked_track_cannot_be_moved_or_reversed(tmp_path):
    shot, camera, _, _ = camera_fixture(tmp_path)
    moved = copy.deepcopy(camera)
    for key in moved['keyframes']:
        key['position'][0] += 1; key['target'][0] += 1
    assert any('origin differs' in e for e in check_camera(tmp_path, 'ep01', shot, moved))
    for key in camera['keyframes']:
        key['target'][2] = 6
    assert any('view axis differs' in e for e in check_camera(tmp_path, 'ep01', shot, camera))


def test_single_axis_checks_distance_direction_timing_and_easing(tmp_path):
    shot, camera, folder, source = camera_fixture(tmp_path, 'slow_push_in')
    source['whitebox_contract'].update(fixed_axis=True, translation={'axis': [0, 0, -1], 'offsets': [[0, 0], [1, 0], [3, .5], [4, .5]]})
    (folder/'camera.json').write_text(json.dumps(source))
    camera['keyframes'] = [{'t': t, 'position': [0, 1.2, z], 'target': [0, 1.2, 0], 'fov': 26.99146656} for t,z in [(0,3),(1,3),(3,2.5),(4,2.5)]]
    camera['source_fingerprint'] = source_fingerprint(tmp_path, 'ep01', shot)
    assert not check_camera(tmp_path, 'ep01', shot, camera)
    wrong = copy.deepcopy(camera);wrong['keyframes'][1]['t'] = .5
    assert any('phase differs' in e for e in check_camera(tmp_path, 'ep01', shot, wrong))
    wrong = copy.deepcopy(camera);wrong['keyframes'][1]['easing'] = 'smooth'
    assert any('easing' in e for e in check_camera(tmp_path, 'ep01', shot, wrong))
    wrong = copy.deepcopy(camera);wrong['keyframes'][2]['position'][0] = .1
    assert any('optical axis' in e for e in check_camera(tmp_path, 'ep01', shot, wrong))


def test_match_cut_checks_world_camera_not_just_movement_label(tmp_path):
    shot, camera, folder, source = camera_fixture(tmp_path)
    other = copy.deepcopy(camera);other['shot_id'] = 'sh2'
    p = folder.parent/'sh2';p.mkdir()
    (p/'camera.json').write_text(json.dumps({'whitebox_contract': {'match': {'shot_id': 'sh1'}}}))
    groups = [{'group_id': 'g1', 'cameras': [camera]}, {'group_id': 'g2', 'cameras': [other]}]
    assert not check_matches(tmp_path, 'ep01', groups)
    other['keyframes'][0]['position'][0] = .2
    assert check_matches(tmp_path, 'ep01', groups)[0]['group_id'] == 'g2'


def test_actor_changes_require_new_framing_review(tmp_path):
    shot, camera, folder, _ = camera_fixture(tmp_path)
    plan_dir = folder.parents[1]/'whitebox_plans'; plan_dir.mkdir()
    plan = plan_dir/'g1.json'; plan.write_text(json.dumps({'actors': [{'id': 'CHAR-A', 'position': [0, 0, 0]}]}))
    camera['placement_fingerprint'] = placement_fingerprint(tmp_path, 'ep01', 'g1')
    assert not check_camera(tmp_path, 'ep01', shot, camera, 'g1')
    plan.write_text(json.dumps({'actors': [{'id': 'CHAR-A', 'position': [2, 0, 0]}]}))
    assert any('placement changed' in e for e in check_camera(tmp_path, 'ep01', shot, camera, 'g1'))


def test_numeric_json_roundtrip_preserves_camera_review(tmp_path):
    shot, camera, folder, source = camera_fixture(tmp_path)
    shot['duration_s'] = 4.0
    source['whitebox_contract']['origin_m'] = [0.0, 1.2, 3.0]
    (folder/'camera.json').write_text(json.dumps(source))
    (folder/'blocking.json').write_text(json.dumps({'beats': [{'t': 0.0, 'yaw': -0.0}]}))
    camera['source_fingerprint'] = source_fingerprint(tmp_path, 'ep01', shot)
    shot['duration_s'] = 4
    source['whitebox_contract']['origin_m'] = [0, 1.2, 3]
    (folder/'camera.json').write_text(json.dumps(source))
    (folder/'blocking.json').write_text(json.dumps({'beats': [{'t': 0, 'yaw': 0}]}))
    assert not check_camera(tmp_path, 'ep01', shot, camera)
    shot['duration_s'] = 4.001
    assert any('source changed' in e for e in check_camera(tmp_path, 'ep01', shot, camera))


def test_numeric_json_roundtrip_preserves_placement_review(tmp_path):
    shot, camera, folder, _ = camera_fixture(tmp_path)
    ep = folder.parents[1]
    (ep/'whitebox_plans').mkdir()
    plan = ep/'whitebox_plans/g1.json'
    plan.write_text(json.dumps({'actors': [{'id': 'A', 'keyframes': [{'t': 0.0, 'position': [-0.0, 0, 1.0]}]}]}))
    camera['placement_fingerprint'] = placement_fingerprint(tmp_path, 'ep01', 'g1')
    plan.write_text(json.dumps({'actors': [{'id': 'A', 'keyframes': [{'t': 0, 'position': [0, 0.0, 1]}]}]}))
    assert not check_camera(tmp_path, 'ep01', shot, camera, 'g1')
    plan.write_text(json.dumps({'actors': [{'id': 'A', 'keyframes': [{'t': 0, 'position': [0, 0, 1.001]}]}]}))
    assert any('placement changed' in e for e in check_camera(tmp_path, 'ep01', shot, camera, 'g1'))


def test_legacy_review_still_loads_but_does_not_accept_changed_source(tmp_path):
    shot, camera, folder, _ = camera_fixture(tmp_path)
    camera['source_fingerprint'] = source_fingerprint(tmp_path, 'ep01', shot, legacy=True)
    camera['placement_fingerprint'] = placement_fingerprint(tmp_path, 'ep01', 'g1', legacy=True)
    assert not check_camera(tmp_path, 'ep01', shot, camera, 'g1')
    (folder/'composition.json').write_text(json.dumps({'subject': 'different actor'}))
    assert any('source changed' in e for e in check_camera(tmp_path, 'ep01', shot, camera, 'g1'))


def test_fingerprints_keep_booleans_and_strings_distinct_from_numbers(tmp_path):
    shot, _, folder, _ = camera_fixture(tmp_path)
    hashes = []
    for value in [True, 1, '1']:
        (folder/'blocking.json').write_text(json.dumps({'visible': value}))
        hashes.append(source_fingerprint(tmp_path, 'ep01', shot))
    assert len(set(hashes)) == 3


def _scene_fixture(tmp_path):
    """g1 引用 SCN-1;场景 whitebox.json 含说明字段(workflow_notes 等)。"""
    shot, camera, folder, _ = camera_fixture(tmp_path)
    (tmp_path/'directing/ep01/shot_list.json').write_text(json.dumps(
        {'generation_groups': [{'group_id': 'g1', 'scene_id': 'SCN-1', 'shots': ['sh1']}]}))
    scene_path = tmp_path/'bible/scenes/SCN-1/whitebox.json'; scene_path.parent.mkdir(parents=True)
    scene = {'dimensions_m': [10, 3, 8], 'inferred': False, 'scale_basis': 'layout', 'workflow_notes': ['first pass'],
             'objects': [{'id': 'desk', 'size_m': [1, .75, .5], 'xy': [.7, .5], 'note': 'oak'}]}
    scene_path.write_text(json.dumps(scene))
    return shot, camera, scene_path, scene


def test_placement_fingerprint_ignores_descriptive_scene_fields(tmp_path):
    """#65:只改 workflow_notes / scale_basis / inferred / 物体 note,v3 placement 指纹不变,已审机位不失效;改几何仍失效。"""
    shot, camera, scene_path, scene = _scene_fixture(tmp_path)
    before = placement_fingerprint(tmp_path, 'ep01', 'g1')
    assert before.startswith('v3:')
    camera['placement_fingerprint'] = before
    scene.update(workflow_notes=['second pass', 'moved nothing'], scale_basis='re-measured', inferred=True,
                 modeling_notes='x', revision={'date': '2026-09-29'})
    scene['objects'][0]['note'] = 'pine'
    scene_path.write_text(json.dumps(scene))
    assert placement_fingerprint(tmp_path, 'ep01', 'g1') == before
    assert not check_camera(tmp_path, 'ep01', shot, camera, 'g1')
    scene['objects'][0]['xy'] = [.6, .5]
    scene_path.write_text(json.dumps(scene))
    assert any('placement changed' in e for e in check_camera(tmp_path, 'ep01', shot, camera, 'g1'))


def test_stored_v2_and_legacy_placement_fingerprints_still_match(tmp_path):
    """升 v3 后存量 v2(含说明字段整份算)与无前缀 legacy 指纹照旧匹配;但旧口径下改说明字段仍按旧规则判变化。"""
    import hashlib
    from modules.whitebox_camera import _canonical_numbers, _placement_payload
    shot, camera, scene_path, scene = _scene_fixture(tmp_path)
    payload = _placement_payload(tmp_path, 'ep01', 'g1', full_scene=True)
    assert payload['scene']['workflow_notes'] == ['first pass']
    v2 = 'v2:' + hashlib.sha256(json.dumps(_canonical_numbers(payload), ensure_ascii=False, sort_keys=True,
                                           separators=(',', ':')).encode()).hexdigest()
    for saved in (v2, placement_fingerprint(tmp_path, 'ep01', 'g1', legacy=True)):
        camera['placement_fingerprint'] = saved
        assert not check_camera(tmp_path, 'ep01', shot, camera, 'g1')
    camera['placement_fingerprint'] = v2
    scene['objects'][0]['size_m'] = [2, .75, .5]
    scene_path.write_text(json.dumps(scene))
    assert any('placement changed' in e for e in check_camera(tmp_path, 'ep01', shot, camera, 'g1'))
