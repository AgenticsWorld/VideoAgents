"""白模自定义形状(2026-11-08):连续软体 tube、模型库 model、串珠提醒、图例、入画 / 碰撞近似、预览与指纹。

起因:inlove ep01 白蛇用一串各自插值的球拼,参考视频里是一串扁椭球,视频模型画成一串圆。
"""
import copy
import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

from modules.whitebox import compile_episode, sample
from modules import whitebox_shapes as ws

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'apps/web/static'


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')


ACTOR_KEYS = [{'t': 0, 'position': [-3, 0, 0], 'pose': 'stand', 'yaw': 0}, {'t': 4, 'position': [-3, 0, 0], 'pose': 'stand', 'yaw': 0}]
CAMERA = {'shot_id': 'sh1', 'start': 0, 'duration_s': 4, 'movement': 'static',
          'keyframes': [{'t': 0, 'position': [0, 1.5, 9], 'target': [0, 1, 0], 'fov': 50},
                        {'t': 4, 'position': [0, 1.5, 9], 'target': [0, 1, 0], 'fov': 50}]}


@pytest.fixture
def project(tmp_path):
    base = tmp_path / 'demo'
    scene = 'SCN-1'
    write(base / 'assets/concepts/scenes' / scene / 'layout.json', {
        'scene_name': 'Lake', 'landmarks': [{'id': 'bank', 'xy': [.2, .5]}, {'id': 'bridge', 'xy': [.7, .5]}],
        'views': [{'tile': 1, 'camera_from': 'bank', 'looking_at': 'bridge'}]})
    write(base / 'bible/scenes' / scene / 'whitebox.json', {'dimensions_m': [20, 4, 12], 'inferred': False, 'objects': []})
    shot = {'shot_id': 'sh1', 'scene_id': scene, 'duration_s': 4, 'characters': ['CHAR-1'], 'view_tile': 1}
    group = {'group_id': 'grp1', 'scene_id': scene, 'scene_no': 'S1', 'shots': ['sh1'], 'total_duration_s': 4,
             'characters_union': ['CHAR-1'], 'blocking_map': {'characters': [
                 {'id': 'CHAR-1', 'label': '白娘子', 'start': {'landmark': 'bank'}}]}}
    write(base / 'directing/ep01/shot_list.json', {'shots': [shot], 'generation_groups': [group]})
    return base


def plan_path(base):
    return base / 'directing/ep01/whitebox_plans/grp1.json'


def snake(**extra):
    return {'id': 'snake', 'label': '大白蛇', 'form_of': 'CHAR-1', 'shape': 'tube', 'follow': 'head', 'head': 'eyes',
            'length_m': 5, 'radius_m': [.2, .25, .15, .03], 'color': '#f2f0e6',
            'keyframes': [{'t': 0, 'position': [-6, 1, 0], 'yaw': math.pi / 2, 'extent': .1},
                          {'t': 1, 'position': [-6, 1, 0], 'extent': .1},
                          {'t': 2, 'position': [-2, 1.2, 1]},
                          {'t': 4, 'position': [3, 1, -1]}], **extra}


BOAT = {'schema_version': 'whitebox_model.v1', 'id': 'MDL-boat', 'label': '小船', 'legend_en': 'small boat with an arched canopy',
        'color': '#8a6a45', 'parts': [
            {'id': 'hull', 'shape': 'extrude', 'outline': [[-2, .4], [-1.6, 0], [1.6, 0], [2, .4]], 'depth_m': .9, 'yaw': math.pi / 2},
            {'id': 'canopy', 'shape': 'lathe', 'parent': 'hull', 'profile': [[.5, 0], [.35, .4], [0, .5]], 'position': [0, .4, 0]},
            {'id': 'oar', 'shape': 'cylinder', 'parent': 'hull', 'size_m': [.05, 2.4, .05], 'position': [-1.9, .4, 0],
             'offset': [0, -1, 0], 'roll': .9, 'hinge': {'axis': 'y', 'min': -.6, 'max': .6}},
            {'id': 'rope', 'shape': 'tube', 'parent': 'hull', 'path': [[1.9, .4, 0], [2.3, .1, .1]], 'radius_m': .02}]}


def boat_prop(**extra):
    return {'id': 'boat', 'model': 'MDL-boat', 'keyframes': [{'t': 0, 'position': [2, 0, -2], 'joints': {'oar': -.5}},
                                                            {'t': 4, 'position': [2, 0, -2], 'joints': {'oar': .5}}], **extra}


def compiled(base, props, **plan):
    write(plan_path(base), {'actors': [{'id': 'CHAR-1', 'size_m': [.48, 1.7, .37], 'keyframes': ACTOR_KEYS}],
                            'props': props, 'cameras': [CAMERA], **plan})
    return compile_episode(base, 'ep01')


def ok(result):
    assert not result['errors'], result['errors']
    return result['groups'][0]


# ---------------------------------------------------------------- compile / validation
def test_tube_and_model_compile_with_derived_sizes(project):
    write(project / 'directing/whitebox_models/MDL-boat.json', BOAT)
    path_tube = {'id': 'rope', 'shape': 'tube', 'radius_m': .03, 'path': [[0, 0, 0], [0, -.5, .2], [0, -1, 0]], 'position': [0, 2, 0]}
    group = ok(compiled(project, [snake(), path_tube, boat_prop()]))
    props = {p['id']: p for p in group['props']}
    assert props['snake']['size_m'] == [.5, .5, 5]                       # follow:2×最大半径、身长
    assert props['rope']['size_m'] == pytest.approx([.06, 1.06, .26])     # path 包围 + 半径
    boat = props['boat']
    assert boat['shape'] == 'model' and boat['label'] == '小船' and boat['model_legend'] == 'small boat with an arched canopy'
    assert [p['id'] for p in boat['model_def']['parts']] == ['hull', 'canopy', 'oar', 'rope']
    assert 'basis' not in json.dumps(boat['model_def']) and 'label' not in boat['model_def']   # 说明文字不进渲染数据
    lo, hi = boat['model_bounds']
    assert boat['size_m'] == pytest.approx([b - a for a, b in zip(lo, hi)], abs=1e-3)
    assert hi[2] - lo[2] > 4.2 and hi[0] - lo[0] < 1.2 and lo[2] <= -2.3          # yaw π/2:船长(局部 X)转到 Z,缆绳伸出船头


@pytest.mark.parametrize('mutate,needle', [
    (lambda s: s.pop('radius_m'), 'radius_m'),
    (lambda s: s.pop('length_m'), 'length_m'),
    (lambda s: s.update(path=[[0, 0, 0], [1, 0, 0]]), 'takes no path'),
    (lambda s: s.update(attach={'actor': 'CHAR-1', 'hand': 'right_hand'}), 'cannot be attached'),
    (lambda s: s.update(wave={'amplitude_m': .2}), 'wavelength_m'),
    (lambda s: s.update(wave={'amplitude_m': .2, 'wavelength_m': 2, 'axis': 'diagonal'}), 'wave.axis'),
    (lambda s: s.update(head='horns'), 'tube.head'),
    (lambda s: s['keyframes'][1].update(extent=1.4), 'extent'),
    (lambda s: s.update(follow='tail'), 'follow'),
    (lambda s: s.update(radius_m=[]), 'radii'),
])
def test_follow_tube_validation(project, mutate, needle):
    prop = snake()
    mutate(prop)
    result = compiled(project, [prop])
    assert result['errors'] and needle in result['errors'][0]['error']


@pytest.mark.parametrize('prop,needle', [
    ({'id': 'r', 'shape': 'tube', 'radius_m': .1, 'position': [0, 1, 0]}, 'needs path'),
    ({'id': 'r', 'shape': 'tube', 'radius_m': .1, 'position': [0, 1, 0], 'path': [[0, 0, 0]]}, '2..64 points'),
    ({'id': 'r', 'shape': 'tube', 'radius_m': .1, 'position': [0, 1, 0], 'path': [[0, 0, 0], [0, 0, 0]]}, 'zero length'),
    ({'id': 'r', 'shape': 'tube', 'radius_m': .1, 'length_m': 2, 'position': [0, 1, 0], 'path': [[0, 0, 0], [1, 0, 0]]}, 'only apply to follow'),
    ({'id': 'r', 'shape': 'tube', 'radius_m': .1, 'path': [[0, 0, 0], [1, 0, 0]],
      'keyframes': [{'t': 0, 'position': [0, 1, 0], 'path': [[0, 0, 0], [1, 0, 0], [2, 0, 0]]}, {'t': 4, 'position': [0, 1, 0]}]},
     'must all have path'),
    ({'id': 'r', 'shape': 'tube', 'radius_m': .1, 'path': [[0, 0, 0], [1, 0, 0]],
      'keyframes': [{'t': 0, 'position': [0, 1, 0], 'path': [[0, 0, 0], [1, 0, 0], [2, 0, 0]]},
                    {'t': 4, 'position': [0, 1, 0], 'path': [[0, 0, 0], [1, 0, 0], [2, 0, 0]]}]}, 'same number of points'),
    ({'id': 'b', 'shape': 'sphere', 'size_m': [.2, .2, .2], 'position': [0, 1, 0], 'radius_m': .1}, 'only applies to shape "tube"'),
    ({'id': 'b', 'shape': 'sphere', 'size_m': [.2, .2, .2], 'keyframes': [{'t': 0, 'position': [0, 1, 0], 'joints': {'a': 1}},
                                                                          {'t': 4, 'position': [0, 1, 0]}]}, 'joints only apply'),
    ({'id': 'b', 'shape': 'box', 'model': 'MDL-boat', 'position': [0, 0, 0]}, 'takes shape "model"'),
    ({'id': 'b', 'model': 'boat', 'position': [0, 0, 0]}, 'MDL-xxx'),
    ({'id': 'b', 'model': 'MDL-none', 'position': [0, 0, 0]}, 'model file missing'),
    ({'id': 'b', 'shape': 'model', 'position': [0, 0, 0]}, 'needs model'),
    (boat_prop(keyframes=[{'t': 0, 'position': [0, 0, 0], 'joints': {'hull': .2}}, {'t': 4, 'position': [0, 0, 0]}]), 'not a hinged part'),
    (boat_prop(keyframes=[{'t': 0, 'position': [0, 0, 0], 'joints': {'oar': 1.2}}, {'t': 4, 'position': [0, 0, 0]}]), 'outside hinge limits'),
    ({'id': 'b', 'shape': 'sphere', 'size_m': [.2, .2, .2], 'position': [0, 1, 0], 'form_of': 'CHAR 1'}, 'Invalid identifier'),
])
def test_path_tube_and_model_prop_validation(project, prop, needle):
    write(project / 'directing/whitebox_models/MDL-boat.json', BOAT)
    result = compiled(project, [prop])
    assert result['errors'] and needle in result['errors'][0]['error'], result['errors']


@pytest.mark.parametrize('mutate,needle', [
    (lambda m: m['parts'][1].update(parent='oar'), 'earlier part'),
    (lambda m: m['parts'][0].update(shape='torus'), 'part.shape'),
    (lambda m: m['parts'].append(dict(m['parts'][0])), 'duplicate part'),
    (lambda m: m['parts'][1].update(profile=[[-.1, 0], [.3, .2]]), 'radii must be >= 0'),
    (lambda m: m['parts'][0].update(outline=[[0, 0], [1, 0], [2, 0]]), 'no area'),
    (lambda m: m['parts'][2]['hinge'].update(min=1, max=0), 'hinge.min > hinge.max'),
    (lambda m: m['parts'][2].update(hinge={'axis': 'w'}), 'axis x/y/z'),
    (lambda m: m.update(legend_en='一条小船'), 'legend_en'),
    (lambda m: m.update(id='MDL-other'), 'match the file name'),
    (lambda m: m['parts'][0].update(color='brown'), 'hex color'),
    (lambda m: m.update(parts=[]), '1..200 parts'),
])
def test_model_file_validation(mutate, needle):
    model = copy.deepcopy(BOAT)
    mutate(model)
    with pytest.raises(ValueError, match=None) as error:
        ws.validate_model(model, 'MDL-boat')
    assert needle in str(error.value)


# ---------------------------------------------------------------- renderer parity (Node)
def _node(script):
    if not shutil.which('node'):
        pytest.skip('Node unavailable')
    out = subprocess.check_output(['node', '--input-type=module', '-e', script], text=True, stderr=subprocess.DEVNULL)
    return json.loads(out)


def _renderer_prelude():
    return f'''
import * as THREE from {json.dumps((STATIC / 'vendor/three/three.module.js').as_uri())};
import * as R from {json.dumps((STATIC / 'whitebox-renderer.js').as_uri())};
const r=Object.create(R.WhiteboxRenderer.prototype);
Object.assign(r,{{width:960,height:540,scene:null,controls:null,
  camera:new THREE.PerspectiveCamera(),overview:new THREE.PerspectiveCamera(),top:new THREE.OrthographicCamera()}});
'''


def test_tube_centerline_and_sampling_match_renderer(project):
    """Python tube_centerline / sample(path, extent, joints)与渲染器 tubeCenterline / sample 逐时刻一致。"""
    follow = snake()
    no_heading = snake()
    no_heading['keyframes'][0].pop('yaw')                       # 朝向回退:第一段运动方向
    bent = {'id': 'rope', 'shape': 'tube', 'radius_m': [.05, .02], 'position': [0, 2, 0],
            'keyframes': [{'t': 0, 'position': [0, 2, 0], 'path': [[0, 0, 0], [0, -.5, .2], [0, -1, 0]], 'extent': .3,
                           'joints': {'a': 1}},
                          {'t': 2, 'position': [0, 2, 0], 'path': [[0, 0, 0], [.4, -.4, 0], [.8, -.6, .3]], 'hold': True},
                          {'t': 4, 'position': [0, 2, 0], 'path': [[0, 0, 0], [0, -1, 0], [0, -2, 0]], 'joints': {'b': -1}}]}
    times = [0, .5, 1, 1.5, 2, 2.5, 3, 4]
    props = [follow, no_heading, bent]
    js = _node(_renderer_prelude() + f'''
const props={json.dumps(props)},times={json.dumps(times)},out=[];
for(const p of props)for(const t of times){{const k=R.sample(p.keyframes,t);out.push({{line:R.tubeCenterline(p,k,t),k:{{path:k.path||null,extent:k.extent??null,joints:k.joints||null}}}});}}
console.log(JSON.stringify(out));''')
    i = 0
    for prop in props:
        for t in times:
            key = sample(prop['keyframes'], t)
            line = ws.tube_centerline(prop, key, t)
            got = js[i]['line']
            assert len(got['points']) == len(line['points']), (prop['id'], t)
            for a, b in zip(got['points'], line['points']):
                assert a == pytest.approx(b, abs=1e-9), (prop['id'], t)
            assert got['radii'] == pytest.approx(line['radii'], abs=1e-9)
            for name in ('length', 'full', 'travel'):
                assert got[name] == pytest.approx(line[name], abs=1e-9), (prop['id'], t, name)
            if 'path' in key:
                assert [v for p in js[i]['k']['path'] for v in p] == pytest.approx([v for p in key['path'] for v in p], abs=1e-12)
            else:
                assert js[i]['k']['path'] is None
            assert js[i]['k']['extent'] == (pytest.approx(key['extent']) if 'extent' in key else None)
            assert js[i]['k']['joints'] == (pytest.approx(key['joints']) if 'joints' in key else None)
            i += 1
    # 头在关键帧位置;extent 0.1 时只露 0.5 m;朝向回退到第一段运动方向(+X):尾巴在 −X
    key = sample(follow['keyframes'], .5)
    assert ws.tube_centerline(follow, key, .5)['length'] == pytest.approx(.5)
    pts = ws.tube_centerline(no_heading, sample(no_heading['keyframes'], 0), 0)['points']
    assert pts[0] == [0, 0, 0] and pts[-1][0] < 0 and abs(pts[-1][2]) < .15
    # 身体沿头部走过的轨迹:t=3 时身体经过 t=2 的头部位置
    key = sample(follow['keyframes'], 3)
    line, head = ws.tube_centerline(follow, key, 3), key['position']
    assert any(math.dist([h + p for h, p in zip(head, pt)], [-2, 1.2, 1]) < 1e-9 for pt in line['points'])


def test_renderer_builds_tube_and_model(project):
    write(project / 'directing/whitebox_models/MDL-boat.json', BOAT)
    result = compiled(project, [snake(wave={'amplitude_m': .3, 'wavelength_m': 2, 'axis': 'up'}), boat_prop()])
    group = ok(result)
    js = _node(_renderer_prelude() + f'''
r.load({json.dumps(result['scenes']['SCN-1'])},{json.dumps(group)});
const out=[];
for(const t of [0,1,2,3,4]){{
  r.setTime(t);const tube=r.props[0].tube,pos=tube.geo.attributes.position.array;
  let finite=true;for(const v of pos)if(!Number.isFinite(v))finite=false;
  r.props[1].mesh.rotation.set(0,0,0);r.props[1].mesh.position.set(0,0,0);r.props[1].mesh.updateMatrixWorld(true);
  const joint=r.props[1].joints.oar.node.rotation.y;
  out.push({{finite,eyes:tube.eyes.length,head:r.props[0].mesh.position.toArray(),body:tube.body.visible,joint,
    radius:tube.geo.boundingSphere.radius}});
}}
for(const p of r.props[1].joints?[r.props[1]]:[])p.joints.oar.node.rotation.set(0,0,0);
r.props[1].mesh.updateMatrixWorld(true);
const box=new THREE.Box3().setFromObject(r.props[1].mesh,true);
console.log(JSON.stringify({{out,box:[box.min.toArray(),box.max.toArray()],parts:r.props[1].mesh.children.length}}));''')
    for row, t in zip(js['out'], [0, 1, 2, 3, 4]):
        assert row['finite'] and row['eyes'] == 2 and row['body']
        assert row['head'] == pytest.approx(sample(group['props'][0]['keyframes'], t)['position'])
        assert row['joint'] == pytest.approx(-.5 + .25 * t)
    assert js['out'][0]['radius'] < js['out'][3]['radius']            # extent 0.1 → 全身
    lo, hi = group['props'][1]['model_bounds']                         # Python 静止姿态包围盒 = 渲染器几何包围盒(lathe 多边形近似内切)
    assert js['box'][0] == pytest.approx(lo, abs=.03) and js['box'][1] == pytest.approx(hi, abs=.03)


# ---------------------------------------------------------------- fingerprints
def test_model_contents_drive_video_and_placement_fingerprints(project):
    from modules.whitebox_camera import _placement_payload, placement_fingerprint
    from modules.whitebox_export import fingerprint, render_payload
    write(project / 'directing/whitebox_models/MDL-boat.json', BOAT)
    episode = compile_episode(project, 'ep01')
    assert 'models' not in _placement_payload(project, 'ep01', 'grp1')     # 没引用模型:payload / 指纹与旧口径相同
    result = compiled(project, [boat_prop()])
    group = ok(result)
    assert render_payload(result['scenes']['SCN-1'], group)[1]['props'][0]['model_def'] == group['props'][0]['model_def']
    video, place = fingerprint(result, group), placement_fingerprint(project, 'ep01', 'grp1')
    changed = copy.deepcopy(BOAT)
    changed['basis'] = '只改说明'
    write(project / 'directing/whitebox_models/MDL-boat.json', changed)
    again = compile_episode(project, 'ep01')
    assert fingerprint(again, again['groups'][0]) == video                  # 说明文字不进视频指纹
    changed['parts'][2]['size_m'] = [.05, 3, .05]
    write(project / 'directing/whitebox_models/MDL-boat.json', changed)
    again = compile_episode(project, 'ep01')
    assert fingerprint(again, again['groups'][0]) != video                  # 形状变 → 已导出视频过期
    assert placement_fingerprint(project, 'ep01', 'grp1') != place           # 机位审查须重做
    assert episode['groups'][0].get('shape_hints') is None


# ---------------------------------------------------------------- bead-chain hint
def _bead(i, keys):
    return {'id': f'bead{i}', 'label': '大白蛇', 'shape': 'sphere', 'size_m': [.4, .4, .4], 'keyframes': keys}


def test_bead_chain_hint_flags_bending_beads_not_rigid_assemblies(project):
    from modules.director import group_sha
    from modules.whitebox import FINGERPRINT_EXCLUDED_KEYS
    from modules.whitebox_export import fingerprint
    # 一串球沿弧线游动(两两距离会变)
    beads = [_bead(i, [{'t': 0, 'position': [-4 + .4 * i, 1, 0]}, {'t': 4, 'position': [2 * math.cos(i * .5), 1, 2 * math.sin(i * .5)]}])
             for i in range(5)]
    group = ok(compiled(project, beads))
    assert len(group['shape_hints']) == 1 and '大白蛇: 5 个 sphere' in group['shape_hints'][0] and 'tube' in group['shape_hints'][0]
    assert not any('串' in w for w in group['warnings'])                     # 不进 warnings(旧口径指纹含 warnings)
    assert 'shape_hints' in FINGERPRINT_EXCLUDED_KEYS
    episode = compile_episode(project, 'ep01')
    g = episode['groups'][0]
    stripped = {k: v for k, v in g.items() if k != 'shape_hints'}
    assert fingerprint(episode, g) == fingerprint(episode, stripped) and group_sha(episode, g) == group_sha(episode, stripped)
    # 刚体拼装:整体平移 + 转动,两两距离不变 → 不报
    def rigid(i, angle, shift):
        x, z = -1 + .45 * i, 0
        return [x * math.cos(angle) - z * math.sin(angle) + shift, 1, x * math.sin(angle) + z * math.cos(angle)]
    boat = [_bead(i, [{'t': 0, 'position': rigid(i, 0, 0)}, {'t': 4, 'position': rigid(i, .8, 3)}]) for i in range(5)]
    assert 'shape_hints' not in ok(compiled(project, boat))
    # 各自独立、互不相接的同名泡泡不报;tube 本身不报
    apart = [_bead(i, [{'t': 0, 'position': [-6 + 3 * i, 1, 0]}, {'t': 4, 'position': [-6 + 3 * i, 2, 0]}]) for i in range(4)]
    assert 'shape_hints' not in ok(compiled(project, apart + [snake()]))


def test_cli_reports_shape_hints_on_third_line(project, monkeypatch, capsys):
    import importlib.util
    import sys
    beads = [_bead(i, [{'t': 0, 'position': [-4 + .4 * i, 1, 0]}, {'t': 4, 'position': [2 * math.cos(i * .5), 1, 2 * math.sin(i * .5)]}])
             for i in range(4)]
    compiled(project, beads)
    monkeypatch.syspath_prepend(str(ROOT / 'code'))
    spec = importlib.util.spec_from_file_location('render_whitebox_shapes_test', ROOT / 'code/render_whitebox.py')
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(sys, 'argv', ['render_whitebox', '--project', 'demo', '--out-root', str(project), '--ep', 'ep01', '--check-only'])
    assert cli.main() == 0
    line = json.loads(capsys.readouterr().out.splitlines()[2])
    assert line['props_coverage_text'].endswith('PASS')
    assert [h['group_id'] for h in line['shape_hints']] == ['grp1'] and line['shape_hints_text'].endswith('-> WARN')


# ---------------------------------------------------------------- legend / framing / collision / stills
def test_legend_merges_parts_and_describes_tubes_and_models(project):
    from modules.whitebox_refs import appearing_props, prop_legend_rows
    write(project / 'directing/whitebox_models/MDL-boat.json', BOAT)
    parts = [{'id': f'hat{i}', 'label': '斗笠', 'shape': 'cone', 'size_m': [.4, .2, .4], 'color': '#d2b48c',
              'position': [-1 + .5 * i, 1.6, 0]} for i in range(2)]
    wind = snake(id='wind', label='风蛇', material='ghost', color='#fafafa', form_of=None)
    wind.pop('form_of')
    group = ok(compiled(project, parts + [snake(), wind, boat_prop(), {'id': 'lone', 'label': '石头', 'shape': 'sphere',
                                                                        'size_m': [.3, .3, .3], 'position': [1, .2, 2]}]))
    shown = appearing_props(group, {'aspect_ratio': '16:9'})
    assert shown == ['hat0', 'hat1', 'snake', 'wind', 'boat', 'lone']
    rows = prop_legend_rows(group, {'aspect_ratio': '16:9'}, shown)
    assert rows == ['tan cones = 斗笠 (hat0, hat1)',
                    'off-white long smooth tapering tube with eyes at its head end = 大白蛇 (snake, one continuous body, '
                    'it is 白娘子 (CHAR-1) in another form)',
                    'translucent white long smooth tapering tube with eyes at its head end = 风蛇 (wind, one continuous body, '
                    'a see-through effect, not a solid object)',
                    'brown small boat with an arched canopy = 小船 (boat)',
                    'off-white sphere = 石头 (lone)']


def test_tube_counts_as_in_frame_when_only_its_body_crosses_the_frame(project):
    from modules.whitebox_refs import appearing_props
    # 头在机位身后(画外),身体横穿画面
    crossing = {'id': 'crossing', 'shape': 'tube', 'radius_m': .2, 'position': [0, 1, 12],
                'path': [[0, 0, 0], [0, 0, -14]]}
    behind = {'id': 'behind', 'shape': 'tube', 'radius_m': .2, 'position': [0, 1, 12], 'path': [[0, 0, 0], [0, 0, 2]]}
    group = ok(compiled(project, [crossing, behind]))
    assert appearing_props(group, {'aspect_ratio': '16:9'}) == ['crossing']
    spheres, visible, attached = ws.prop_spheres(group['props'][0], {}, 0)
    assert visible and not attached and len(spheres) > 20                   # 14 m 按 0.5 m 细分


def test_probe_collides_with_tube_segments_and_skips_ghost(project):
    from modules.whitebox_probe import collide_group
    body = {'id': 'body', 'shape': 'tube', 'radius_m': .3, 'position': [-3, .3, -2], 'path': [[0, 0, 0], [0, 0, 4]]}
    result = compiled(project, [body])
    group = ok(result)
    hits = collide_group(group, result['scenes']['SCN-1'])
    assert any({h['a'], h['b']} == {'CHAR-1', 'body'} for h in hits)
    ghost = dict(body, material='ghost')
    result = compiled(project, [ghost])
    assert not collide_group(ok(result), result['scenes']['SCN-1'])
    # 普通几何体仍是一个有向长方体(旧口径)
    box = {'id': 'crate', 'size_m': [.5, .5, .5], 'position': [0, .25, 0], 'yaw': .3}
    assert ws.prop_boxes(box, {}, 0) == [{'center': [0, .25, 0], 'size_m': [.5, .5, .5], 'yaw': .3, 'pitch': 0, 'roll': 0, 'loose': False}]


def test_stills_event_times_include_extent_path_and_joint_changes():
    from modules.whitebox_stills import event_times
    camera = {'shot_id': 'sh1', 'start': 0, 'duration_s': 4, 'keyframes': [{'t': 0}, {'t': 4}]}
    group = {'props': [{'id': 's', 'keyframes': [{'t': 0, 'position': [0, 0, 0], 'extent': .1},
                                                 {'t': 1, 'position': [0, 0, 0], 'extent': 1},
                                                 {'t': 3, 'position': [0, 0, 0], 'extent': 1, 'joints': {'a': 1}}]}]}
    assert event_times(camera, group, None, [0.042, 3.958]) == [.5, 2.0]


# ---------------------------------------------------------------- model library CLI helpers
def test_models_describe_and_preview_payload(project):
    from modules.whitebox_models import describe, preview_payload
    write(project / 'directing/whitebox_models/MDL-boat.json', BOAT)
    write(project / 'directing/whitebox_models/MDL-bad.json', {'id': 'MDL-bad', 'parts': []})
    compiled(project, [boat_prop(), {'id': 'ghost', 'model': 'MDL-gone', 'position': [0, 0, 0]}])
    result = describe(project)
    assert [m['id'] for m in result['models']] == ['MDL-boat'] and result['models'][0]['used_by'] == ['ep01/grp1']
    assert result['models'][0]['hinges'] == ['oar']
    assert {e['id'] for e in result['errors']} == {'MDL-bad', 'MDL-gone'}
    model_def, meta = ws.load_model(project, 'MDL-boat')
    payload, views = preview_payload(model_def, meta, 'MDL-boat')
    names = [v[0].split(' (origin')[0] for v in views]
    assert names == ['front', 'right', 'back-left 3/4', 'top', 'hinges min', 'hinges max']
    group = payload['episode']['groups'][0]
    prop, figure = group['props'][0], group['actors'][0]
    assert [k['joints']['oar'] for k in prop['keyframes'][:6]] == [0, 0, 0, 0, -.6, .6]
    lo, hi = meta['bounds']
    fx, _, fz = figure['keyframes'][0]['position']
    assert fx - .24 > hi[0] and fz + .2 < lo[2]                                 # 参考人在模型外侧,不重叠
    assert len(group['cameras']) == 6 and group['duration_s'] == 6
