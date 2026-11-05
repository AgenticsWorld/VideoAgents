"""白模第二阶段(2026-11-05):道具挂手占位 code/whitebox_props.py(B)与宿主实测 code/whitebox_probe.py(E)。"""
import json
import math
import os
from pathlib import Path

import pytest

from modules.whitebox import compile_episode

ROOT = Path(__file__).resolve().parents[1]


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')


def stand(t, x, z=0.0, yaw=0.0, **extra):
    return {'t': t, 'position': [x, 0, z], 'pose': 'stand', 'yaw': yaw, **extra}


CAMERA = {'shot_id': 'sh1', 'start': 0, 'duration_s': 4, 'movement': 'static',
          'keyframes': [{'t': 0, 'position': [0, 1.2, 7], 'target': [0, 1, 0], 'fov': 40},
                        {'t': 4, 'position': [0, 1.2, 7], 'target': [0, 1, 0], 'fov': 40}]}


@pytest.fixture
def project(tmp_path):
    base = tmp_path / 'demo'
    write(base / 'assets/concepts/scenes/SCN-1/layout.json', {
        'scene_name': 'Hall', 'landmarks': [{'id': 'a', 'xy': [.2, .5]}, {'id': 'b', 'xy': [.8, .5]}],
        'views': [{'tile': 1, 'camera_from': 'a', 'looking_at': 'b'}]})
    write(base / 'bible/scenes/SCN-1/whitebox.json', {'dimensions_m': [12, 4, 10], 'objects': [
        {'id': 'step', 'size_m': [1, .15, 1], 'position': [-1, .075, 0]},                       # 路上的矮台阶:可踩
        {'id': 'ramp', 'size_m': [3, .4, 2], 'position': [1, -.15, 0], 'roll': .1},             # 斜坡:顶面最高约 0.2 m,可踩
        {'id': 'wall', 'size_m': [.2, 3, 4], 'position': [2.4, 1.5, 0], 'collision_role': 'wall'},              # A 终点面朝墙,净距 0.11 m
        {'id': 'chair', 'size_m': [.5, .9, .5], 'position': [-3, .45, 2]},                     # C 坐在上面
        {'id': 'banner', 'size_m': [2, 1, .05], 'position': [0, 1, .3], 'collision_role': 'decor'},
        {'id': 'beam', 'size_m': [6, .2, .3], 'position': [0, 2.6, 0]}]})                      # 头顶横梁
    shot = {'shot_id': 'sh1', 'scene_id': 'SCN-1', 'duration_s': 4, 'characters': ['CHAR-A', 'CHAR-B', 'CHAR-C'],
            'view_tile': 1, 'props': ['PROP-WAND', 'PROP-BOOK', 'PROP-NOOWNER', 'PROP-AWAY']}
    routes = [{'id': cid, 'label': cid, 'start': {'landmark': 'a'}} for cid in ('CHAR-A', 'CHAR-B', 'CHAR-C')]
    group = {'group_id': 'grp1', 'scene_id': 'SCN-1', 'scene_no': 'S1', 'shots': ['sh1'], 'total_duration_s': 4,
             'characters_union': ['CHAR-A', 'CHAR-B', 'CHAR-C'], 'blocking_map': {'characters': routes}}
    write(base / 'directing/ep01/shot_list.json', {'shots': [shot], 'generation_groups': [group]})
    write(base / 'bible/props.json', {'props': [
        {'id': 'PROP-WAND', 'name': '魔杖', 'owners': [{'character_id': 'CHAR-A', 'how': '左手握着'}],
         'scale': {'canonical_size': '全长约40cm,杖身粗约1.5cm;杖头宝石直径约3cm'}, 'palette': '乌木黑、宝石红'},
        {'id': 'PROP-BOOK', 'name': 'book', 'name_en': 'a book covered in cloth', 'owners': [{'character_id': 'CHAR-C'}],
         'scale': {'canonical_size': 'about 20 x 13 cm, 4.5 cm thick; about 700 g'}},
        {'id': 'PROP-NOOWNER', 'name': '花瓶', 'owners': []},
        {'id': 'PROP-AWAY', 'name': '怀表', 'owners': [{'character_id': 'CHAR-Z'}]}]})
    return base


def plan(**extra):
    hands = {'right_hand': [-.15, .9, .3], 'left_hand': [.15, .9, .3]}
    return {'actors': [
        {'id': 'CHAR-A', 'size_m': [.48, 1.7, .38], 'keyframes': [stand(0, -2, **hands), stand(4, 2, yaw=math.pi / 2, **hands)]},
        {'id': 'CHAR-B', 'size_m': [.48, 1.7, .38], 'keyframes': [stand(0, 0, .1, yaw=math.pi), stand(4, 0, .1, yaw=math.pi)]},
        {'id': 'CHAR-C', 'size_m': [.48, 1.7, .38], 'keyframes': [
            {'t': 0, 'position': [-3, 0, 2], 'pose': 'sit', 'yaw': 0}, {'t': 4, 'position': [-3, 0, 2], 'pose': 'sit', 'yaw': 0}]}],
        'cameras': [CAMERA], **extra}


def compiled(base, data):
    write(base / 'directing/ep01/whitebox_plans/grp1.json', data)
    result = compile_episode(base, 'ep01')
    assert not result['errors'], result['errors']
    return result


# ---------------------------------------------------------------- B:道具占位
def test_bible_parsing_helpers():
    from modules.whitebox_props import bible_color, bible_dims, placeholder_geometry, preferred_hand
    assert bible_dims({'scale': {'canonical_size': 'about 20 x 13 cm, 4.5 cm thick; about 700 g'}}) == pytest.approx([.2, .13, .045])
    assert bible_dims({'scale': {'canonical_size': '甜筒长约11cm、口径约5.5cm;冰淇淋球直径约6.5cm'}}) == pytest.approx([.11, .055])
    assert bible_dims({'scale': {'canonical_size': '约 1.2 米;重 2kg'}}) == pytest.approx([1.2])
    assert bible_dims({}) == []
    assert placeholder_geometry([.2, .13, .045]) == ('box', [.13, .2, .045])
    assert placeholder_geometry([.11, .055]) == ('cylinder', [.055, .11, .055])
    assert placeholder_geometry([.95, .6, .35, .1, .02, .005]) == ('cylinder', [.03, .95, .03])   # 杆状最细 3 cm
    assert placeholder_geometry([1.2, .035, .025]) == ('cylinder', [.035, 1.2, .035])
    assert placeholder_geometry([]) == ('box', [.1, .1, .1])
    assert bible_color({'palette': '甜筒金褐、冰淇淋浅薄荷绿'}) == '#c8a165'
    assert bible_color({'palette': '冰淇淋浅薄荷绿、甜筒金褐'}) == '#a8e6cf'
    assert bible_color({'name_en': 'a book covered in cloth'}) != '#d62828'      # covered 不算 red
    assert bible_color({'name_en': 'the golden key'}) == '#d4a537'
    assert preferred_hand({'owners': [{'character_id': 'X', 'how': 'holds it in the left hand'}]}, 'X') == 'left_hand'
    assert preferred_hand({'scale': {'relative_anchor': '右手握着'}}, 'X') == 'right_hand'


def test_placeholders_attach_follow_skip_and_write(project):
    from modules.whitebox import prop_state
    from modules.whitebox_props import suggest, write as write_plans
    compiled(project, plan())
    result = suggest(project, 'ep01')
    row = result['groups'][0]
    added = {p['id']: p for p in row['added']}
    skipped = {s['id']: s['reason'] for s in row['skipped']}
    wand = added['PROP-WAND']
    assert wand['ref'] == 'PROP-WAND' and wand['shape'] == 'cylinder' and wand['size_m'] == pytest.approx([.03, .4, .03])
    assert wand['attach'] == {'actor': 'CHAR-A', 'hand': 'left_hand', 'offset': [0, .16, .02]} and wand['color'] == '#1a1a1a'
    book = added['PROP-BOOK']   # CHAR-C 没有手部通道 → 跟着人走的关键帧
    assert book['placeholder_mode'] == 'follow' and 'attach' not in book and book['shape'] == 'box'
    assert [k['t'] for k in book['keyframes']] == [0, 4]
    assert 'owners' in skipped['PROP-NOOWNER'] and 'CHAR-Z' in skipped['PROP-AWAY']
    assert 'PROP-WAND' not in skipped
    written = write_plans(project, 'ep01', result['groups'])
    assert written[0]['props'] == ['PROP-WAND', 'PROP-BOOK'] and (project / written[0]['backup']).is_file()
    episode = compile_episode(project, 'ep01')
    assert not episode['errors'], episode['errors']
    group = episode['groups'][0]
    cov = {r['id']: r['status'] for r in group['prop_coverage']['items']}
    assert cov == {'PROP-WAND': 'modeled', 'PROP-BOOK': 'modeled', 'PROP-NOOWNER': 'missing', 'PROP-AWAY': 'missing'}
    by_id = {a['id']: a for a in group['actors']}
    center, attached, _ = prop_state(next(p for p in group['props'] if p['id'] == 'PROP-WAND'), by_id, 2)
    assert attached and abs(center[0]) < 1   # 跟着 CHAR-A 的左手走到场中
    # 再跑一次不会重复添加
    assert write_plans(project, 'ep01', suggest(project, 'ep01')['groups']) == []


def test_props_cli_dry_run_then_write(project, monkeypatch, capsys):
    import importlib.util
    import sys
    compiled(project, plan())
    monkeypatch.syspath_prepend(str(ROOT / 'code'))
    spec = importlib.util.spec_from_file_location('whitebox_props_cli_test', ROOT / 'code/whitebox_props.py')
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    path = project / 'directing/ep01/whitebox_plans/grp1.json'
    before = path.read_bytes()
    monkeypatch.setattr(sys, 'argv', ['whitebox_props', '--project', 'demo', '--out-root', str(project), '--ep', 'ep01'])
    assert cli.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out['written'] == [] and '可生成占位 2 个、需手工处理 2 个' in out['text'] and path.read_bytes() == before
    monkeypatch.setattr(sys, 'argv', ['whitebox_props', '--project', 'demo', '--out-root', str(project), '--ep', 'ep01', '--write'])
    assert cli.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out['written'][0]['group_id'] == 'grp1' and 'placement' in out['next']
    assert [p['id'] for p in json.loads(path.read_text())['props']] == ['PROP-WAND', 'PROP-BOOK']


# ---------------------------------------------------------------- E:collide
def violations(project, data, **kw):
    from modules.whitebox_probe import collide_group
    result = compiled(project, data)
    group = result['groups'][0]
    return collide_group(group, result['scenes']['SCN-1'], **kw)


def test_collide_reports_crossing_and_wall_but_not_supports(project):
    rows = violations(project, plan())
    pairs = {(r['a'], r['b']) for r in rows}
    assert ('CHAR-A', 'CHAR-B') in pairs                  # A 从 B 身上穿过
    crossing = next(r for r in rows if (r['a'], r['b']) == ('CHAR-A', 'CHAR-B'))
    assert crossing['overlap'] and crossing['kind'] == 'actor' and crossing['t_range_s'][0] < 2 < crossing['t_range_s'][1]
    wall = next(r for r in rows if r['b'] == 'wall')     # 走到 x=2 时离墙 0.2 m 内
    assert wall['kind'] == 'scene' and wall['role'] == 'wall' and wall['min_clearance_m'] < .15 and wall['t_range_s'][1] == 4
    assert not pairs & {('CHAR-A', 'step'), ('CHAR-A', 'ramp'), ('CHAR-C', 'chair'), ('CHAR-A', 'banner'),
                        ('CHAR-A', 'beam'), ('CHAR-B', 'beam'), ('CHAR-B', 'banner')}
    # 接触例外
    allowed = violations(project, plan(), allow=[('CHAR-A', 'CHAR-B', None, None), ('CHAR-A', '*', 3.5, 4.0)])
    assert not any((r['a'], r['b']) == ('CHAR-A', 'CHAR-B') for r in allowed)
    assert all(r['t_range_s'][1] < 3.5 for r in allowed if r['b'] == 'wall')


def test_collide_props_holder_ghost_and_allow_parser(project):
    from modules.whitebox_probe import parse_allow
    held = {'id': 'staff', 'shape': 'cylinder', 'size_m': [.04, 1.2, .04],
            'attach': {'actor': 'CHAR-B', 'hand': 'right_hand'}}
    bubble = {'id': 'bubble', 'shape': 'sphere', 'material': 'ghost', 'size_m': [1, 1, 1], 'position': [-3, 1, 2]}
    data = plan(props=[held, bubble])
    data['actors'][1]['keyframes'] = [{**k, 'right_hand': [-.15, .9, .3]} for k in data['actors'][1]['keyframes']]
    rows = violations(project, data)
    assert not any(r['b'] == 'staff' and r['a'] == 'CHAR-B' for r in rows)   # 持有人与手里的道具
    assert any(r['b'] == 'staff' and r['a'] == 'CHAR-A' and r['kind'] == 'prop' for r in rows)
    assert not any(r['b'] == 'bubble' for r in rows)
    assert parse_allow(['A:B', 'A:*@1-2.5']) == [('A', 'B', None, None), ('A', '*', 1.0, 2.5)]
    with pytest.raises(ValueError):
        parse_allow(['AB'])


def test_column_follows_tilted_box():
    from modules.whitebox_probe import column, object_shape
    ramp = object_shape({'id': 'ramp', 'size_m': [4, .2, 2], 'position': [0, 0, 0], 'roll': math.atan(.25)})
    low, high = column(ramp, -1.5, 0), column(ramp, 1.5, 0)
    assert low[1] < high[1] and high[1] - low[1] == pytest.approx(3 * .25, abs=.01)
    assert column(ramp, 0, 5) is None


# ---------------------------------------------------------------- E:facing
def test_facing_declared_targets_and_lying_head(project):
    from modules.whitebox_probe import facing_group
    data = plan(facing_targets=[{'actor_id': 'CHAR-B', 'target_id': 'CHAR-A', 'start': 0, 'end': 1}])
    data['actors'][0]['keyframes'] = [stand(0, 0, -2, yaw=0.0), stand(4, 0, -2, yaw=0.0)]                   # A 面朝 +Z,正对 B
    data['actors'][1]['keyframes'] = [stand(0, 0, 0, yaw=math.pi + math.radians(10)), stand(4, 0, 0, yaw=math.pi + math.radians(10))]
    write(project / 'directing/ep01/shots/sh1/blocking.json', {'characters': [{'id': 'CHAR-A', 'body_facing_target_id': 'CHAR-B'}]})
    result = compiled(project, data)
    rows, camera = facing_group(project, 'ep01', result['groups'][0], result['render'], pairs=['CHAR-C:CHAR-A@sh1', 'CHAR-A:CHAR-Z'])
    by = {(r['actor'], r['source']): r for r in rows}
    assert by[('CHAR-A', 'blocking:sh1')]['body_max_deg'] == pytest.approx(0, abs=.01) and by[('CHAR-A', 'blocking:sh1')]['ok']
    b = by[('CHAR-B', 'plan.facing_targets')]
    assert b['body_max_deg'] == pytest.approx(10, abs=.01) and not b['ok'] and b['within_tol_pct'] == 0 and b['t_range_s'] == [0, 1]
    assert 'error' in by[('CHAR-A', 'pair')]                     # CHAR-Z 不在本组
    assert camera and camera[0]['shot_id'] == 'sh1'
    # 卧姿:头指向 yaw 的反方向
    data['actors'][1]['keyframes'] = [{'t': t, 'position': [0, 0, 0], 'pose': 'lie', 'yaw': 0.0} for t in (0, 4)]
    result = compiled(project, data)
    rows, _ = facing_group(project, 'ep01', result['groups'][0], result['render'])
    assert next(r for r in rows if r['actor'] == 'CHAR-B')['body_max_deg'] == pytest.approx(0, abs=.01)


# ---------------------------------------------------------------- E:frame(真浏览器)
def chromium_or_skip():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip('Playwright unavailable')
    kwargs = {'headless': True}
    if os.environ.get('VIDEOAGENTS_CHROMIUM'):
        kwargs['executable_path'] = os.environ['VIDEOAGENTS_CHROMIUM']
    try:
        with sync_playwright() as p:
            p.chromium.launch(**kwargs).close()
    except Exception:
        pytest.skip('Chromium unavailable (set VIDEOAGENTS_CHROMIUM)')


def test_frame_probe_counts_pixels_per_object(project):
    chromium_or_skip()
    from modules.whitebox_probe import frame_probe
    write(project / 'settings.json', {'output': {'aspect_preset': 'youtube'}})
    scene = json.loads((project / 'bible/scenes/SCN-1/whitebox.json').read_text())
    scene['objects'] = [o for o in scene['objects'] if o['id'] in ('wall', 'beam')]   # 横幅、斜坡在画面上真会挡住 A(脚);本例只留墙和横梁
    write(project / 'bible/scenes/SCN-1/whitebox.json', scene)
    data = plan(props=[{'id': 'bubble', 'shape': 'sphere', 'material': 'ghost', 'size_m': [1.2, 1.2, 1.2], 'position': [0, 1, .1]}])
    data['actors'][0]['keyframes'] = [stand(0, 0, 0), stand(4, 0, 0)]                     # A 在画面正中,泡泡罩着
    data['actors'][1]['keyframes'] = [stand(0, 0, -1.5), stand(4, 0, -1.5)]               # B 在 A 正后方,被挡住大半
    data['actors'][2]['keyframes'] = [stand(0, 30, 0), stand(4, 30, 0)]                   # C 在画外
    result = compiled(project, data)
    out = frame_probe(project, result, 'grp1', [('sh1', 1.0)], objects=['wall', 'nope'], scale=.25)
    sample = out['samples'][0]
    a, b, c, bubble = (sample['objects'][k] for k in ('CHAR-A', 'CHAR-B', 'CHAR-C', 'bubble'))
    assert a['in_frame'] and a['visible_pct'] == 100 and abs(a['center_ndc'][0]) < .05 and a['cut'] == []
    assert b['in_frame'] and b['visible_pct'] < 60
    assert c['in_frame'] is False
    assert bubble['ghost'] and bubble['in_frame']
    assert 0 < sample['sky_pct'] < 100 and sample['unknown'] == ['nope']
    assert sample['objects']['wall']['kind'] == 'scene'
    assert any(k == '_floor' for k, _ in sample['scene_top'])
