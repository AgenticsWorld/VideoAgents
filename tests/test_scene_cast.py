import copy
import json
from pathlib import Path

from modules.scene_cast import scene_cast_groups, scene_reference_rows, complete_prompt_cast, check_prompt_cast
from modules.whitebox import complete_scene_actors


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding='utf-8')


def source():
    return {'generation_groups': [
        {'group_id': 'g1', 'scene_id': 'ROOM', 'scene_no': 'S1', 'characters_union': ['CHAR-A']},
        {'group_id': 'g2', 'scene_id': 'ROOM', 'scene_no': 'S1', 'characters_union': ['CHAR-B']},
        {'group_id': 'g3', 'scene_id': 'ROOM', 'scene_no': 'S2', 'characters_union': ['CHAR-C']},
        {'group_id': 'g4', 'scene_id': 'ELSEWHERE', 'scene_no': 'S1', 'characters_union': ['CHAR-D']},
    ]}


def test_scene_cast_includes_silent_listener_and_isolates_time_and_place():
    data = source(); before = copy.deepcopy(data)
    contexts = scene_cast_groups(data)
    assert contexts['g1']['actor_ids'] == contexts['g2']['actor_ids'] == ['CHAR-A', 'CHAR-B']
    assert contexts['g3']['actor_ids'] == ['CHAR-C']
    assert contexts['g4']['actor_ids'] == ['CHAR-D']
    assert data == before


def test_missing_scene_numbers_do_not_merge_by_location():
    data = source()
    for g in data['generation_groups']:
        g.pop('scene_no')
    assert scene_cast_groups(data)['g1']['actor_ids'] == ['CHAR-A']


def test_scene_table_cast_and_removal_of_previously_derived_cast():
    data = source()
    data['scene_table'] = [{'scene_no': 'S1', 'scene_id': 'ROOM', 'cast': ['CHAR-SILENT']}]
    data['generation_groups'][0].update(scene_cast=['CHAR-STALE'], scene_cast_source='scene_cast.v1')
    assert scene_cast_groups(data)['g1']['actor_ids'] == ['CHAR-A', 'CHAR-B', 'CHAR-SILENT']


def test_reference_reuses_same_scene_costume_and_keeps_image_numbers(tmp_path):
    data = source()
    travel = 'assets/concepts/characters/CHAR-B/travel.png'
    (tmp_path/travel).parent.mkdir(parents=True); (tmp_path/travel).write_bytes(b'image')
    write(tmp_path/'assets/prompts/ep02/g2.json', {'refs': [travel]})
    rows = scene_reference_rows(tmp_path, 'ep02', data)['g1']
    assert rows[1]['ref'] == travel
    p = {'refs': ['scene.png'], 'video_prompt': 'Identity lock: exactly 1 character on screen; no third character. Shot 1: [Image 1]'}
    updated = complete_prompt_cast(p, rows)
    assert updated['refs'] == ['scene.png', travel]
    assert 'CHAR-B@Image 2' in updated['video_prompt']
    assert updated['video_prompt'].index('CHAR-B@Image 2') < updated['video_prompt'].index('Shot 1:')
    assert 'exactly 1' not in updated['video_prompt']
    assert complete_prompt_cast(updated, rows) == updated
    assert 'CHAR-A' in check_prompt_cast(updated, rows)[0]  # Missing assets remain explicit.


def test_missing_explicit_costume_never_falls_back_to_wrong_identity_sheet(tmp_path):
    data = source(); data['generation_groups'][0]['costumes_by_char'] = {'CHAR-A': 'COS-2'}
    p = tmp_path/'assets/concepts/characters/CHAR-A'
    p.mkdir(parents=True); (p/'sheet.png').write_bytes(b'wrong clothes')
    rows = scene_reference_rows(tmp_path, 'ep01', data)['g1']
    assert rows[0]['missing'] and rows[0]['ref'] is None


def actor(cid, x, visible=True):
    return {'id': cid, 'color': '#e63946', 'keyframes': [
        {'t': 0, 'position': [x, 0, 0], 'pose': 'sit', 'visible': visible},
        {'t': 4, 'position': [x+1, 0, 0], 'pose': 'sit', 'visible': visible}]}


def test_whitebox_carries_last_position_and_preserves_departure():
    data = source(); data['generation_groups'] = data['generation_groups'][:2]
    data['generation_groups'].append({**data['generation_groups'][1], 'group_id': 'g3'})
    groups = [
        {'group_id': 'g1', 'actors': [actor('CHAR-A', 2, False), actor('CHAR-B', 8)], 'duration_s': 4, 'warnings': []},
        {'group_id': 'g2', 'actors': [actor('CHAR-B', 9)], 'duration_s': 7, 'warnings': []},
        {'group_id': 'g3', 'actors': [actor('CHAR-B', 9)], 'duration_s': 2, 'warnings': []}]
    errors = []
    complete_scene_actors(groups, scene_cast_groups(data), {g['group_id']: g for g in data['generation_groups']}, errors)
    added = groups[1]['actors'][1]
    assert not errors
    assert added['scene_inherited_from'] == 'g1'
    assert [k['t'] for k in added['keyframes']] == [0, 7]
    assert all(k['position'] == [3, 0, 0] and k['visible'] is False for k in added['keyframes'])
    assert added['color'] != groups[1]['actors'][0]['color']
    assert [k['t'] for k in groups[2]['actors'][1]['keyframes']] == [0, 2]
    assert added is not groups[2]['actors'][1]


def test_whitebox_missing_anchor_is_error_not_invented_origin():
    data = source(); data['generation_groups'] = data['generation_groups'][:2]
    groups = [{'group_id': 'g1', 'actors': [actor('CHAR-A', 0)], 'duration_s': 4, 'warnings': []}]
    errors = []
    complete_scene_actors(groups, scene_cast_groups(data), {g['group_id']: g for g in data['generation_groups']}, errors)
    assert 'CHAR-B' in errors[0]['error']
    assert len(groups[0]['actors']) == 1
