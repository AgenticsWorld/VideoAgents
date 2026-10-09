"""Portable whitebox geometry, timing, continuity, API and encoding regression tests."""
import copy
import json
import math
from pathlib import Path

import pytest

from modules.whitebox import compile_episode, image_size, load_scene, sample, validate_keys, xyz


def test_whitebox_localization():
    """Exercise dynamic preview text using the real runtime and every locale."""
    import shutil
    import subprocess
    if not shutil.which('node'):
        pytest.skip('Node unavailable')
    subprocess.run(['node', str(Path(__file__).with_name('whitebox_i18n_check.mjs'))],
                   check=True, capture_output=True, text=True)


def write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data),encoding='utf-8')


@pytest.fixture
def project(tmp_path):
    base=tmp_path/'demo';scene='SCN-1'
    write(base/'assets/concepts/scenes'/scene/'layout.json',{
        'scene_name':'Room','landmarks':[{'id':'door','xy':[.1,.5]}, {'id':'desk','xy':[.7,.5]}],
        'views':[{'tile':1,'camera_from':'door','looking_at':'desk'}]})
    write(base/'bible/scenes'/scene/'whitebox.json',{'dimensions_m':[10,3,8], 'inferred':False,'objects':[
        {'id':'desk','size_m':[1,.75,.5],'xy':[.7,.5]}]})
    shot={'shot_id':'sh1','scene_id':scene,'duration_s':4,'characters':['CHAR-1'],'view_tile':1}
    group={'group_id':'grp1','scene_id':scene,'scene_no':'S1','shots':['sh1'],'total_duration_s':4,
           'characters_union':['CHAR-1'],'blocking_map':{'characters':[
               {'id':'CHAR-1','label':'A','start':{'landmark':'door'},'end':{'landmark':'desk'}}]}}
    write(base/'directing/ep01/shot_list.json',{'shots':[shot],'generation_groups':[group]})
    return base


def source(base):
    path=base/'directing/ep01/shot_list.json'
    return path,json.loads(path.read_text())


def test_scene_scale_matches_map(project):
    scene=load_scene(project,'SCN-1')
    assert scene['dimensions_m']==[10,3,8]
    assert scene['objects'][0]['position']==pytest.approx([2,.375,0])
    assert xyz([0,0],[10,3,8])==[-5,0,-4]
    assert xyz([1,1],[10,3,8])==[5,0,4]


def test_layout_aspect_warning_and_image_size(project,tmp_path):
    """dimensions_m X:Z must match the layout_top image aspect (the map is stretched over the floor); JPEG/PNG headers both read."""
    PIL=pytest.importorskip('PIL.Image')
    folder=project/'assets/concepts/scenes/SCN-1'
    PIL.new('RGB',(160,90)).save(folder/'layout_top.png',format='JPEG')
    assert image_size(folder/'layout_top.png')==(160,90)
    PIL.new('RGB',(32,32)).save(tmp_path/'square.png');assert image_size(tmp_path/'square.png')==(32,32)
    assert image_size(project/'assets/concepts/scenes/SCN-1/layout.json') is None
    write(project/'bible/scenes/SCN-1/whitebox.json',{'dimensions_m':[16,3,9],'objects':[]})
    assert not any('宽高比' in w for w in load_scene(project,'SCN-1')['warnings'])
    write(project/'bible/scenes/SCN-1/whitebox.json',{'dimensions_m':[12,3,10],'objects':[]})
    assert any('宽高比' in w and '6.750' in w for w in load_scene(project,'SCN-1')['warnings'])


def test_group_time_and_cast(project):
    result=compile_episode(project,'ep01');assert not result['errors']
    group=result['groups'][0];assert group['duration_s']==4
    actor=group['actors'][0];assert (actor['letter'],actor['color'])==('A','#e63946')
    assert sample(actor['keyframes'],2)['position']==pytest.approx([-1,0,0])
    assert group['cameras'][0]['keyframes'][0]=={**group['cameras'][0]['keyframes'][-1],'t':0}


def test_authored_scene_actor_cannot_copy_an_occupied_color(project):
    path, data = source(project)
    data['scene_table'] = [{'scene_no': 'S1', 'scene_id': 'SCN-1', 'cast': ['CRE-028']}]
    write(path, data)
    first = compile_episode(project, 'ep01')['groups'][0]['actors'][0]
    corpse = {**copy.deepcopy(first), 'id': 'CRE-028', 'kind': 'creature'}
    plan = {'scene_actors': [corpse]}
    write(project/'directing/ep01/whitebox_plans/grp1.json', plan)
    result = compile_episode(project, 'ep01')
    assert not result['errors']
    actors = result['groups'][0]['actors']
    assert [a['color'] for a in actors] == ['#e63946', '#1d78d8']
    assert actors[1]['keyframes'] == corpse['keyframes']
    del corpse['color']
    write(project/'directing/ep01/whitebox_plans/grp1.json', plan)
    assert compile_episode(project, 'ep01')['groups'][0]['actors'] == actors


def test_actor_colors_are_unique_including_mounts():
    from modules.whitebox import PALETTE, free_color, palette_color, validate_actor_colors
    actors = [{'id': 'CHAR-1', 'color': PALETTE[0]},
              {'id': 'CRE-1', 'kind': 'creature', 'color': PALETTE[1], 'rider': 'CHAR-1'}]
    validate_actor_colors(actors)
    actors[1]['color'] = PALETTE[0]   # 2026-09-19:坐骑与骑手同色也算撞色
    with pytest.raises(ValueError, match='share color'):
        validate_actor_colors(actors)
    # 调色板用尽不报错:按黄金角生成新色,且与已有色不重复
    extra = free_color([{'color': c} for c in PALETTE])
    assert extra == palette_color(len(PALETTE)) and extra.lower() not in {c.lower() for c in PALETTE}
    assert len({palette_color(i).lower() for i in range(40)}) == 40


def test_actor_colors_are_fixed_across_groups(project):
    """整集固定身份色(2026-09-11):同一人物在不同分镜组同色,不随组内 blocking_map 顺序变化;
    只作坐骑的生物不占色位、与骑手同色;场次名单人物也进整集表。"""
    path, data = source(project)
    shot2 = {'shot_id': 'sh2', 'scene_id': 'SCN-1', 'duration_s': 3, 'characters': ['CHAR-2', 'CHAR-1'], 'view_tile': 1}
    shot3 = {'shot_id': 'sh3', 'scene_id': 'SCN-1', 'duration_s': 3, 'characters': ['CHAR-3'], 'view_tile': 1}
    data['shots'] += [shot2, shot3]
    data['generation_groups'][0]['blocking_map']['characters'][0]['mounted'] = 'CRE-horse'
    data['generation_groups'] += [
        {'group_id': 'grp2', 'scene_id': 'SCN-1', 'scene_no': 'S1', 'shots': ['sh2'], 'total_duration_s': 3,
         'characters_union': ['CHAR-2', 'CHAR-1'], 'blocking_map': {'characters': [
             {'id': 'CHAR-2', 'label': 'B', 'start': {'landmark': 'desk'}, 'end': {'landmark': 'door'}},
             {'id': 'CHAR-1', 'label': 'A', 'start': {'landmark': 'door'}, 'end': {'landmark': 'desk'}}]}},
        {'group_id': 'grp3', 'scene_id': 'SCN-1', 'scene_no': 'S2', 'shots': ['sh3'], 'total_duration_s': 3,
         'characters_union': ['CHAR-3'], 'blocking_map': {'characters': [
             {'id': 'CHAR-3', 'label': 'C', 'start': {'landmark': 'door'}, 'end': {'landmark': 'desk'}}]}}]
    write(path, data)
    result = compile_episode(project, 'ep01'); assert not result['errors']
    colors = result['actor_colors']
    assert colors == {'CHAR-1': '#e63946', 'CRE-horse': '#f59e0b', 'CHAR-2': '#1d78d8', 'CHAR-3': '#2ea043'}
    for group in result['groups']:
        for actor in group['actors']:
            assert actor['color'] == colors[actor['id']], (group['group_id'], actor['id'])
    grp2 = {a['id']: a['color'] for a in result['groups'][1]['actors']}
    assert grp2 == {'CHAR-2': '#1d78d8', 'CHAR-1': '#e63946', 'CRE-horse': '#f59e0b'}  # 顺序反了颜色不变;坐骑自有色(排在人物之后)


def test_scene_cast_enables_physical_visibility_and_explicit_exceptions(project):
    path, data = source(project)
    data['generation_groups'][0]['scene_cast'] = ['CHAR-1']
    write(path, data)
    camera = compile_episode(project, 'ep01')['groups'][0]['cameras'][0]
    camera['visible_actor_ids'] = []
    plan_path = project/'directing/ep01/whitebox_plans/grp1.json'
    write(plan_path, {'cameras': [camera]})
    group = compile_episode(project, 'ep01')['groups'][0]
    assert 'visible_actor_ids' not in group['cameras'][0]
    camera['visibility_override_reason'] = 'Intentional prop-only VFX plate'
    write(plan_path, {'cameras': [camera]})
    assert compile_episode(project, 'ep01')['groups'][0]['cameras'][0]['visible_actor_ids'] == []


def test_timed_trajectory_and_pose(project):
    write(project/'directing/ep01/shots/sh1/blocking.json',{'characters':[
        {'id':'CHAR-1','xy_start':[.1,.5],'xy_end':[.7,.5],
         'path':[{'t':1,'xy':[.1,.5]},{'t':3,'xy':[.7,.5]}],
         'beats':[{'t':3,'pose':'sit'}]}]})
    actor=compile_episode(project,'ep01')['groups'][0]['actors'][0]
    assert sample(actor['keyframes'],.5)['position']==pytest.approx([-4,0,0])
    assert sample(actor['keyframes'],2)['position']==pytest.approx([-1,0,0])
    assert sample(actor['keyframes'],3)['pose']=='sit'
    assert sample(actor['keyframes'],4)['pose']=='sit'


def test_shot_list_poses_feed_keyframes(project):
    """体位优先级(2026-09-14):blocking.json pose → shot_list 每镜 poses → 文字粗推;六态都合法。"""
    path,data=source(project);data['shots'][0]['poses']={'CHAR-1':{'pose':'kneel','action':'磕头'}};write(path,data)
    actor=compile_episode(project,'ep01')['groups'][0]['actors'][0]
    assert sample(actor['keyframes'],0)['pose']=='kneel' and sample(actor['keyframes'],4)['pose']=='kneel'
    write(project/'directing/ep01/shots/sh1/blocking.json',{'characters':[{'id':'CHAR-1','pose':'crouch','start_pos':'门口'}]})
    actor=compile_episode(project,'ep01')['groups'][0]['actors'][0]
    assert sample(actor['keyframes'],0)['pose']=='crouch'
    for pose in ('stand','sit','lie','kneel','crouch','prone'):
        validate_keys([{'t':0,'position':[0,0,0],'pose':pose},{'t':4,'position':[1,0,0],'pose':pose}],4)
    from modules.whitebox import pose_from
    assert [pose_from(t) for t in ('趴在地上','跪在炉前','蹲下身','侧卧','落座','站定','face down on the floor','kneels')]==['prone','kneel','crouch','lie','sit','stand','prone','kneel']
    # #60:同句他人的体位不归给本人
    assert pose_from('甲站在门旁，乙在他面前跪下。', ['乙']) == 'stand'
    assert pose_from('甲站在门旁，乙在他面前跪下。') == 'kneel'   # 不传他人名时维持旧行为
    assert pose_from('A stands by the door, CHAR-2 kneels', ['CHAR-2']) == 'stand'
    assert pose_from('甲跪在门旁，乙站着。', ['乙']) == 'kneel'
    assert pose_from('甲蹲在乙身旁拔箭', ['乙']) == 'crouch'   # 他人名在体位词之后=宾语/方位,仍归本人
    assert pose_from('站在高坡,随年长家将一同跪下', ['年长家将']) == 'kneel'   # 伴随介词后的他人名不是主语


def test_route_pose_ignores_other_cast_clause(project):
    path,data=source(project);g=data['generation_groups'][0]
    g['characters_union']=['CHAR-1','CHAR-2'];data['shots'][0]['characters']=['CHAR-1','CHAR-2']
    g['blocking_map']['characters'][0]['route_en']='甲站在门旁，乙在他面前跪下。'
    g['blocking_map']['characters'][0]['label']='甲'
    g['blocking_map']['characters'].append({'id':'CHAR-2','label':'乙','start':{'landmark':'desk'},'route_en':'乙跪下'})
    write(path,data)
    group=compile_episode(project,'ep01')['groups'][0]
    poses={a['id']:a['keyframes'][0]['pose'] for a in group['actors']}
    assert poses=={'CHAR-1':'stand','CHAR-2':'kneel'}
    assert any(w.startswith('CHAR-1:') and '体位词' in w for w in group['warnings'])


def test_mounted_creature_scale(project):
    path,data=source(project);g=data['generation_groups'][0]
    g['creatures_union']=['CRE-1'];g['blocking_map']['characters'][0]['mounted']='CRE-1'
    write(path,data);result=compile_episode(project,'ep01');assert not result['errors']
    rider,mount=result['groups'][0]['actors']
    assert mount['letter']=='' and mount['kind']=='creature'
    assert (rider['color'],mount['color'])==('#e63946','#1d78d8') and result['actor_colors']['CRE-1']=='#1d78d8'
    assert rider['keyframes'][0]['pose']=='sit'
    assert rider['keyframes'][0]['position'][1]-mount['keyframes'][0]['position'][1]==1.45


def test_creature_color_fixed_across_independent_and_mounted_groups():
    from modules.whitebox import episode_actor_colors
    src={'generation_groups':[
        {'group_id':'g1','shots':[],'blocking_map':{'characters':[{'id':'CHAR-1','mounted':'CRE-2'},{'id':'CRE-1'}]}},
        {'group_id':'g2','shots':[],'blocking_map':{'characters':[{'id':'CHAR-2','mounted':'CRE-1'}]}}]}
    colors=episode_actor_colors(src,{})
    # 独立角色按出场序占位,只作坐骑的 CRE-2 排在最后;CRE-1 被骑乘时不随骑手变色
    assert list(colors)==['CHAR-1','CRE-1','CHAR-2','CRE-2'] and len(set(colors.values()))==4


def test_extra_color_clash_is_reassigned(project):
    first=compile_episode(project,'ep01')['groups'][0]['actors'][0]
    horse={**copy.deepcopy(first),'id':'EXTRA-HORSE-1','label':'horse','kind':'creature'}
    write(project/'directing/ep01/whitebox_plans/grp1.json',{'extras':[horse]})
    group=compile_episode(project,'ep01')['groups'][0]
    assert group['extras'][0]['color']!=first['color'] and any('EXTRA-HORSE-1' in w for w in group['warnings'])


def test_extra_auto_color_avoids_episode_identity_colors(project):
    """#70:群演自动色避开整集登记身份色(含其他组人物),同一群演跨组同色。"""
    path, data = source(project)
    data['shots'].append({'shot_id': 'sh2', 'scene_id': 'SCN-1', 'duration_s': 3, 'characters': ['CHAR-2'], 'view_tile': 1})
    data['generation_groups'].append(
        {'group_id': 'grp2', 'scene_id': 'SCN-1', 'scene_no': 'S2', 'shots': ['sh2'], 'total_duration_s': 3,
         'characters_union': ['CHAR-2'], 'blocking_map': {'characters': [
             {'id': 'CHAR-2', 'label': 'B', 'start': {'landmark': 'desk'}, 'end': {'landmark': 'door'}}]}})
    write(path, data)
    first = compile_episode(project, 'ep01')['groups'][0]['actors'][0]
    extra = {k: v for k, v in copy.deepcopy(first).items() if k != 'color'}
    extra.update(id='EXTRA-1', label='passer')
    write(project/'directing/ep01/whitebox_plans/grp1.json', {'extras': [extra]})
    extra2 = copy.deepcopy(extra)
    extra2['keyframes'] = [{**k, 't': k['t'] * 3 / 4} for k in extra2['keyframes']]
    extra2['color'] = '#e63946'   # 手填成 grp1 的 CHAR-1 身份色(本组没有 CHAR-1)
    write(project/'directing/ep01/whitebox_plans/grp2.json', {'extras': [extra2]})
    result = compile_episode(project, 'ep01'); assert not result['errors']
    registered = {c.lower() for c in result['actor_colors'].values()}
    got = [g['extras'][0]['color'] for g in result['groups']]
    assert all(c.lower() not in registered for c in got), got
    assert got[0] == got[1]


def test_missing_cast_blocks_instead_of_disappearing(project):
    path,data=source(project);data['generation_groups'][0]['creatures_union']=['CRE-1'];write(path,data)
    result=compile_episode(project,'ep01')
    assert not result['groups'] and 'CRE-1' in result['errors'][0]['error']


@pytest.mark.parametrize('bad',[-1,float('nan'),float('inf'),True])
def test_invalid_scale_rejected(project,bad):
    path=project/'bible/scenes/SCN-1/whitebox.json';data=json.loads(path.read_text());data['dimensions_m'][0]=bad;write(path,data)
    assert compile_episode(project,'ep01')['errors']


def test_seq_sum_is_interpreter_independent():
    # #63:组时长须是朴素左到右累加(3.10 语义),不能随 3.12+ 的补偿 sum() 变,也不能取整。
    from modules.whitebox import _seq_sum
    assert _seq_sum([1.8,1.0,2.4,1.8,1.0]) == 7.999999999999999
    assert _seq_sum([2, 3]) == 5 and isinstance(_seq_sum([2, 3]), int)
    assert _seq_sum(x for x in []) == 0


def test_shot_duration_mismatch(project):
    path,data=source(project);data['generation_groups'][0]['total_duration_s']=7;write(path,data)
    assert 'duration' in compile_episode(project,'ep01')['errors'][0]['error']


def test_invalid_landmark(project):
    path,data=source(project);data['generation_groups'][0]['blocking_map']['characters'][0]['start']={'landmark':'missing'};write(path,data)
    assert 'Unknown landmark' in compile_episode(project,'ep01')['errors'][0]['error']


def test_cut_and_inherit(project):
    path,data=source(project);g2=copy.deepcopy(data['generation_groups'][0]);g2.update(group_id='grp2',continuity_from='grp1')
    data['generation_groups'].append(g2);write(path,data)
    before=compile_episode(project,'ep01');assert any('不连续' in w for w in before['groups'][1]['warnings'])
    write(project/'directing/ep01/whitebox_plans/grp2.json',{'continuity':{'actors':'inherit','camera':'inherit'}})
    after=compile_episode(project,'ep01');assert not after['errors']
    assert after['groups'][1]['actors'][0]['keyframes'][0]['position']==after['groups'][0]['actors'][0]['keyframes'][-1]['position']
    g2['scene_no']='S2';write(path,data)
    assert 'cannot inherit' in compile_episode(project,'ep01')['errors'][0]['error']


def test_camera_cut_intervals_and_override(project):
    path,data=source(project);sh2={**data['shots'][0],'shot_id':'sh2','duration_s':2};data['shots'].append(sh2)
    data['generation_groups'][0].update(shots=['sh1','sh2'],total_duration_s=6);write(path,data)
    write(project/'directing/ep01/shots/sh2/camera.json',{'movement':'push_in','whitebox_keyframes':[
        {'t':0,'position':[1,2,3],'target':[0,1,0],'fov':45},
        {'t':2,'position':[0,2,2],'target':[0,1,0],'fov':45}]})
    cameras=compile_episode(project,'ep01')['groups'][0]['cameras']
    assert [c['start'] for c in cameras]==[0,4]
    assert cameras[1]['keyframes'][0]['position']==[1,2,3]


@pytest.mark.parametrize('keys',[
    [{'t':0,'position':[0,0,0]},{'t':0,'position':[1,0,0]}],
    [{'t':1,'position':[0,0,0]},{'t':4,'position':[1,0,0]}],
    [{'t':0,'position':[0,0,0]},{'t':5,'position':[1,0,0]}],
    [{'t':0,'position':[0,0,0],'pose':'fly'},{'t':4,'position':[1,0,0]}],
])
def test_malformed_tracks(keys):
    with pytest.raises(ValueError):validate_keys(keys,4)


def test_yaw_shortest_path_and_hold():
    keys=[{'t':0,'position':[0,0,0],'yaw':math.radians(170)},
          {'t':2,'position':[2,0,0],'yaw':math.radians(-170)}]
    assert sample(keys,1)['yaw']==pytest.approx(math.pi)
    keys[0]['hold']=True
    assert sample(keys,1)['position']==[0,0,0]
    assert sample(keys,2)['position']==[2,0,0]


def test_standing_bend_defaults_interpolation_and_validation():
    keys=[{'t':0,'position':[0,0,0],'pose':'stand'},
          {'t':2,'position':[0,0,0],'pose':'stand','bend':1.2},
          {'t':4,'position':[0,0,0],'pose':'stand'}]
    validate_keys(keys,4)
    assert sample(keys,1)['bend']==pytest.approx(.6)
    assert sample(keys,3)['bend']==pytest.approx(.6)
    for invalid in [-.01, math.pi, float('nan'), True]:
        bad=copy.deepcopy(keys);bad[1]['bend']=invalid
        with pytest.raises(ValueError):validate_keys(bad,4)


def test_javascript_interpolation_matches_compiler():
    import shutil
    import subprocess
    if not shutil.which('node'):pytest.skip('Node unavailable')
    keys=[{'t':0,'position':[0,0,0],'yaw':2.9,'pose':'stand','easing':'smooth'},
          {'t':2,'position':[2,0,0],'yaw':-2.9,'pose':'sit','hold':True,'bend':1.2},
          {'t':4,'position':[3,0,2],'yaw':0,'pose':'lie'}]
    times=[0,.5,1,1.999,2,3,4]
    module=(Path(__file__).resolve().parents[1]/'apps/web/static/whitebox-renderer.js').as_uri()
    script=f'import {{sample}} from {json.dumps(module)}; console.log(JSON.stringify({json.dumps(times)}.map(t=>sample({json.dumps(keys)},t))));'
    actual=json.loads(subprocess.check_output(['node','--input-type=module','-e',script],text=True))
    for time,value in zip(times,actual):
        expected=sample(keys,time)
        assert value['position']==pytest.approx(expected['position'])
        assert value['yaw']==pytest.approx(expected['yaw'])
        assert value['pose']==expected['pose']
        assert value.get('bend',0)==pytest.approx(expected.get('bend',0))


def test_bend_preserves_feet_and_resets_when_scrubbing():
    import shutil
    import subprocess
    if not shutil.which('node'):pytest.skip('Node unavailable')
    static=Path(__file__).resolve().parents[1]/'apps/web/static'
    script='''
import assert from 'node:assert/strict';
import * as T from THREE_MODULE;
import {WhiteboxRenderer} from RENDERER_MODULE;
const r=Object.create(WhiteboxRenderer.prototype);
Object.assign(r,{width:960,height:540,scene:null,controls:null,camera:new T.PerspectiveCamera(),overview:new T.PerspectiveCamera(),top:new T.OrthographicCamera()});
r.load({dimensions_m:[8,3,6],objects:[]},{duration_s:2,actors:[{id:'a',kind:'person',color:'#cc4444',size_m:[.48,1.7,.38],keyframes:[{t:0,position:[0,0,0],pose:'stand'},{t:1,position:[0,0,0],pose:'stand',bend:1.2},{t:2,position:[0,0,0],pose:'stand'}]}],cameras:[{start:0,duration_s:2,keyframes:[{t:0,position:[0,2,5],target:[0,1,0],fov:45}]}]});
const a=r.actors[0];
function state(t){r.setTime(t);r.scene.updateMatrixWorld(true);return {head:a.head.getWorldPosition(new T.Vector3()),feet:a.legs.map(l=>l.shin.getWorldPosition(new T.Vector3()))};}
const upright=state(0),bent=state(1);
assert.ok(bent.head.y<upright.head.y && bent.head.z>upright.head.z);
for(let i=0;i<2;i++)assert.ok(bent.feet[i].distanceTo(upright.feet[i])<1e-9);
for(const t of [2,0,1,0]){const s=state(t);if(t!==1)assert.ok(s.head.distanceTo(upright.head)<1e-9);}
r.disposeScene();
'''.replace('THREE_MODULE',json.dumps((static/'vendor/three/three.module.js').as_uri()))\
   .replace('RENDERER_MODULE',json.dumps((static/'whitebox-renderer.js').as_uri()))
    subprocess.run(['node','--input-type=module','-e',script],check=True,capture_output=True,text=True)


def test_open_bottom_scene_floor_and_grid(project):
    """#76:场景级 floor none / grid false 透传;缺省时场景 JSON 不带这两个键(指纹不变),非法值报错。"""
    import shutil
    import subprocess
    assert 'floor' not in load_scene(project,'SCN-1') and 'grid' not in load_scene(project,'SCN-1')
    path=project/'bible/scenes/SCN-1/whitebox.json';data=json.loads(path.read_text())
    write(path,{**data,'floor':'none','grid':False})
    scene=load_scene(project,'SCN-1');assert scene['floor']=='none' and scene['grid'] is False
    for bad in [{'floor':'glass'},{'grid':'no'}]:
        write(path,{**data,**bad})
        with pytest.raises(ValueError):load_scene(project,'SCN-1')
    if not shutil.which('node'):pytest.skip('Node unavailable')
    static=Path(__file__).resolve().parents[1]/'apps/web/static'
    script='''
import assert from 'node:assert/strict';
import * as T from THREE_MODULE;
import {WhiteboxRenderer} from RENDERER_MODULE;
function make(extra){const r=Object.create(WhiteboxRenderer.prototype);
Object.assign(r,{width:960,height:540,scene:null,controls:null,camera:new T.PerspectiveCamera(),overview:new T.PerspectiveCamera(),top:new T.OrthographicCamera()});
r.load({dimensions_m:[8,3,6],objects:[{id:'wall',size_m:[1,3,.2],position:[0,1.5,0]}],...extra},null);return r;}
const names=r=>r.solids.map(m=>m.name);
assert.deepEqual(names(make({})),['ground-floor','ground-grid','wall']);
const open=make({floor:'none',grid:false});
assert.deepEqual(names(open),['wall']);
assert.equal(open.scene.getObjectByName('ground-floor'),undefined);
assert.equal(open.scene.getObjectByName('ground-grid'),undefined);
assert.deepEqual(names(make({floor:'none'})),['ground-grid','wall']);
'''.replace('THREE_MODULE',json.dumps((static/'vendor/three/three.module.js').as_uri()))\
   .replace('RENDERER_MODULE',json.dumps((static/'whitebox-renderer.js').as_uri()))
    out=subprocess.run(['node','--input-type=module','-e',script],capture_output=True,text=True)
    assert out.returncode==0,out.stderr


def test_lean_shoulder_matches_renderer_and_reach_warnings():
    """#77:肩点算法 Python/JS 一致;非前俯姿态写 bend、手够不着只报 WARN。"""
    import shutil
    import subprocess
    from modules.whitebox import HAND_SIDES, pose_channel_warnings, shoulder_point
    size=[.48,1.7,.38]
    # #121:手部通道按人物解剖学左右——面朝 +Z(yaw=0)时 right_hand 挂局部 −X 肩、left_hand 挂 +X 肩
    sides=dict(HAND_SIDES)
    assert shoulder_point(size,{},sides['right_hand'])[0]<0<shoulder_point(size,{},sides['left_hand'])[0]
    cases=[{'pose':p,**({'bend':b} if b is not None else {})} for p in ('stand','crouch','kneel','sit','lie') for b in (None,0,.7,1.4)]
    py=[shoulder_point(size,k,s) for k in cases for s in (-1,1)]
    kneel=shoulder_point(size,{'pose':'kneel','bend':1.2},1)
    assert kneel[1]==pytest.approx(.175*1.7+(.58-.175)*1.7*math.cos(1.2)) and kneel[2]>0
    assert shoulder_point(size,{'pose':'sit','bend':1.2},1)==shoulder_point(size,{'pose':'sit'},1)
    if shutil.which('node'):
        module=(Path(__file__).resolve().parents[1]/'apps/web/static/whitebox-renderer.js').as_uri()
        script=f'import {{shoulderPoint}} from {json.dumps(module)};const c={json.dumps(cases)};console.log(JSON.stringify(c.flatMap(k=>[-1,1].map(s=>shoulderPoint({json.dumps(size)},k,s)))));'
        js=json.loads(subprocess.check_output(['node','--input-type=module','-e',script],text=True,stderr=subprocess.DEVNULL))
        for a,b in zip(py,js):assert a==pytest.approx(b,abs=1e-12)
        js_sides=json.loads(subprocess.check_output(['node','--input-type=module','-e',f'import {{HAND_SIDES}} from {json.dumps(module)};console.log(JSON.stringify(HAND_SIDES));'],text=True,stderr=subprocess.DEVNULL))
        assert [tuple(x) for x in js_sides]==list(HAND_SIDES)
    far=[-.2,.05,1.6]
    actor={'id':'CHAR-1','kind':'person','size_m':size,'keyframes':[
        {'t':0,'position':[0,0,0],'pose':'sit','bend':.5,'right_hand':[-.25,.9,.3]},
        {'t':2,'position':[0,0,0],'pose':'kneel','bend':1.2,'right_hand':far}]}
    warns=pose_channel_warnings(actor)
    assert any('sit' in w and 'bend' in w for w in warns)
    assert any('right_hand' in w and 't=2' in w for w in warns)
    actor['keyframes'][1]['right_hand']=[-.25,.3,.6]
    actor['keyframes'][0].pop('bend')
    assert pose_channel_warnings(actor)==[]
    # 同一目标写在左手通道:+X 肩够不着(手停在伸直方向)才报,说明可达性按解剖学一侧的肩算
    mirrored=[.25,.3,.6]
    assert math.dist(mirrored,shoulder_point(size,{'pose':'kneel','bend':1.2},sides['left_hand']))<2*.21*1.7
    actor['keyframes']=[{**k,'left_hand':[-.6,.9,.3] if k['t']==0 else [-.6,.3,.6]} for k in actor['keyframes']]
    assert any('left_hand' in w for w in pose_channel_warnings(actor))


def test_actor_bounds_follow_lean():
    from modules.whitebox_refs import actor_bounds
    actor={'size_m':[.48,1.7,.38]}
    upright=actor_bounds(actor,{'position':[0,0,0],'pose':'kneel'})
    assert upright==actor_bounds(actor,{'position':[0,0,0],'pose':'kneel','bend':0})   # 存量不变
    bent=actor_bounds(actor,{'position':[0,0,0],'pose':'kneel','bend':1.2,'yaw':0})
    assert bent[1][1]<upright[1][1]            # 高度压低
    assert bent[0][2]>0 and bent[0][0]==pytest.approx(0)   # 沿 +Z(yaw 0 面朝)前移
    head_z=(.725-.175)*1.7*math.sin(1.2)+.17
    assert bent[0][2]+bent[1][2]>=head_z-1e-9  # 前缘罩住头
    side=actor_bounds(actor,{'position':[0,0,0],'pose':'kneel','bend':1.2,'yaw':math.pi/2})
    assert side[0][0]>0 and side[0][2]==pytest.approx(0,abs=1e-9)
    assert actor_bounds(actor,{'position':[0,0,0],'pose':'crouch'})==actor_bounds(actor,{'position':[0,0,0],'pose':'crouch','torso_yaw':.3})

def test_kneel_bend_keeps_knees_and_arms_fixed_length():
    """#77:跪姿 bend 绕 0.175h 髋前俯、膝不动;手臂两段定长,够不着停在伸直方向。"""
    import shutil
    import subprocess
    if not shutil.which('node'):pytest.skip('Node unavailable')
    static=Path(__file__).resolve().parents[1]/'apps/web/static'
    script='''
import assert from 'node:assert/strict';
import * as T from THREE_MODULE;
import {WhiteboxRenderer,shoulderPoint} from RENDERER_MODULE;
const r=Object.create(WhiteboxRenderer.prototype);
Object.assign(r,{width:960,height:540,scene:null,controls:null,camera:new T.PerspectiveCamera(),overview:new T.PerspectiveCamera(),top:new T.OrthographicCamera()});
const h=1.7,size=[.48,h,.38],far=[-.2,.05,1.6];
r.load({dimensions_m:[8,3,6],objects:[]},{duration_s:2,actors:[{id:'a',kind:'person',color:'#cc4444',size_m:size,keyframes:[
  {t:0,position:[0,0,0],pose:'kneel',right_hand:[-.25,.5,.2]},{t:2,position:[0,0,0],pose:'kneel',bend:1.2,right_hand:far}]}],
  cameras:[{start:0,duration_s:2,keyframes:[{t:0,position:[0,2,5],target:[0,1,0],fov:45}]}]});
const a=r.actors[0],arm=a.arms[0];
assert.equal(arm.key,'right_hand');assert.equal(arm.side,-1);   // #121: the actor's right hand hangs from the local -X shoulder
const st=t=>{r.setTime(t);r.scene.updateMatrixWorld(true);return {head:a.head.getWorldPosition(new T.Vector3()),knees:a.legs.map(l=>l.thigh.getWorldPosition(new T.Vector3()))};};
const up=st(0),bent=st(2);
assert.ok(bent.head.y<up.head.y-.2&&bent.head.z>up.head.z+.3);
for(let i=0;i<2;i++)assert.ok(bent.knees[i].distanceTo(up.knees[i])<1e-9);
const sh=new T.Vector3(...shoulderPoint(size,{pose:'kneel',bend:1.2},-1));
assert.ok(sh.x<0);
assert.ok(new T.Vector3(0,-.5,0).applyMatrix4(arm.upper.matrix).distanceTo(sh)<1e-9);
for(const m of [arm.upper,arm.lower])assert.ok(Math.abs(m.scale.y-.21*h)<1e-12);
assert.ok(Math.abs(arm.hand.position.distanceTo(sh)-.42*h)<1e-9);
r.disposeScene();
'''.replace('THREE_MODULE',json.dumps((static/'vendor/three/three.module.js').as_uri()))\
   .replace('RENDERER_MODULE',json.dumps((static/'whitebox-renderer.js').as_uri()))
    out=subprocess.run(['node','--input-type=module','-e',script],capture_output=True,text=True)
    assert out.returncode==0,out.stderr


def test_face_direction_follows_actor_turn_pose_and_altitude():
    """Use real Three.js scene graphs without a GPU to check both actor types."""
    import shutil
    import subprocess
    if not shutil.which('node'):pytest.skip('Node unavailable')
    static=Path(__file__).resolve().parents[1]/'apps/web/static'
    script='''
import assert from 'node:assert/strict';
import * as THREE from THREE_MODULE;
import {WhiteboxRenderer} from RENDERER_MODULE;
for(const kind of ['character','creature']) {
  const renderer=Object.create(WhiteboxRenderer.prototype);
  Object.assign(renderer,{width:960,height:540,scene:null,controls:null,
    camera:new THREE.PerspectiveCamera(),overview:new THREE.PerspectiveCamera(),
    top:new THREE.OrthographicCamera()});
  renderer.load({dimensions_m:[10,3,8],objects:[]},{duration_s:2,actors:[{
    id:'actor',kind,color:'#e63946',size_m:[.5,1.7,.4],keyframes:[
      {t:0,position:[0,0,0],yaw:0,pose:'stand'},
      {t:1,position:[2,4,3],yaw:Math.PI/2,pose:'sit'},
      {t:2,position:[3,8,4],yaw:Math.PI,pose:'lie'}]}],cameras:[{
    shot_id:'shot',start:0,duration_s:2,keyframes:[
      {t:0,position:[0,2,5],target:[0,1,0],fov:45},
      {t:2,position:[0,10,5],target:[0,8,0],fov:45}]}]});
  const actor=renderer.actors[0],face=actor.head.getObjectByName('face-direction');
  assert.ok(face,kind+' must have facial markers');
  const nose=face.getObjectByName('face-forward');
  assert.ok(nose.position.z>1.7*.1,'nose must protrude from the head');
  for(const [time,expected] of [[0,[0,0,1]],[1,[1,0,0]],[2,[0,1,0]]]) {
    renderer.setTime(time);renderer.scene.updateMatrixWorld(true);
    const base=nose.localToWorld(new THREE.Vector3(0,0,0));
    const tip=nose.localToWorld(new THREE.Vector3(0,1,0));
    assert.ok(tip.sub(base).normalize().distanceTo(new THREE.Vector3(...expected))<1e-8,
      kind+' face direction at '+time);
    assert.ok(actor.head.getWorldPosition(new THREE.Vector3()).y>=time*4,
      'face must follow airborne actor');
  }
  face.traverse(o=>{
    assert.ok(!o.isSprite,'face must not billboard toward the viewer');
    assert.ok(o.layers.test(renderer.camera.layers),'face must appear in camera exports');
  });
  renderer.disposeScene();
}
'''.replace('THREE_MODULE',json.dumps((static/'vendor/three/three.module.js').as_uri()))\
   .replace('RENDERER_MODULE',json.dumps((static/'whitebox-renderer.js').as_uri()))
    subprocess.run(['node','--input-type=module','-e',script],check=True,capture_output=True,text=True)


def test_override_keeps_identity(project):
    group=compile_episode(project,'ep01')['groups'][0];actor=group['actors'][0]
    actor.update(color='#ffffff',letter='Z')
    write(project/'directing/ep01/whitebox_plans/grp1.json',{'actors':[actor]})
    actual=compile_episode(project,'ep01')['groups'][0]['actors'][0]
    assert (actual['color'],actual['letter'])==('#e63946','A')


def test_background_performers_props_and_departure(project):
    extra={'id':'EXTRA-passenger','label':'Passenger','kind':'person','color':'#8899aa',
           'size_m':[.5,1.7,.4],'keyframes':[
               {'t':0,'position':[0,0,0],'pose':'sit','visible':True},
               {'t':2,'position':[1,0,0],'pose':'stand','visible':False},
               {'t':4,'position':[2,0,0],'pose':'stand','visible':False}]}
    prop={'id':'book','shape':'box','size_m':[.2,.03,.3],'position':[0,.8,0],'shot_ids':['sh1']}
    plan={'extras':[extra],'props':[prop]}
    path=project/'directing/ep01/whitebox_plans/grp1.json';write(path,plan)
    result=compile_episode(project,'ep01');assert not result['errors']
    group=result['groups'][0]
    assert [a['id'] for a in group['actors']]==['CHAR-1']
    assert group['extras']==[extra] and group['props']==[prop]
    assert sample(extra['keyframes'],1.99)['visible'] is True
    assert sample(extra['keyframes'],2)['visible'] is False
    # Real scene-graph behavior: extras render, departed performers disappear,
    # and camera-only cast filtering preserves the spatial overview.
    import shutil
    import subprocess
    if shutil.which('node'):
        static=Path(__file__).resolve().parents[1]/'apps/web/static'
        group['cameras'][0]['visible_actor_ids']=['EXTRA-passenger']
        script=f'''
import assert from 'node:assert/strict';
import * as THREE from {json.dumps((static/'vendor/three/three.module.js').as_uri())};
import {{WhiteboxRenderer}} from {json.dumps((static/'whitebox-renderer.js').as_uri())};
const r=Object.create(WhiteboxRenderer.prototype);
Object.assign(r,{{width:960,height:540,scene:null,controls:null,
  camera:new THREE.PerspectiveCamera(),overview:new THREE.PerspectiveCamera(),top:new THREE.OrthographicCamera()}});
r.load({json.dumps(result['scenes']['SCN-1'])},{json.dumps(group)});
assert.equal(r.actors.length,2);assert.equal(r.props.length,1);
assert.equal(r.actors[0].root.layers.test(r.camera.layers),false);
assert.equal(r.actors[0].root.layers.test(r.top.layers),true);
assert.equal(r.actors[1].root.layers.test(r.camera.layers),true);
assert.equal(r.props[0].mesh.visible,true);
r.setTime(1.99);assert.equal(r.actors[1].root.visible,true);
r.setTime(2);assert.equal(r.actors[1].root.visible,false);
r.props[0].data.shot_ids=[];r.setTime(0);assert.equal(r.props[0].mesh.visible,false);
r.disposeScene();
'''
        subprocess.run(['node','--input-type=module','-e',script],check=True,capture_output=True,text=True)
    extra['id']='CHAR-1';write(path,plan)
    assert 'EXTRA-' in compile_episode(project,'ep01')['errors'][0]['error']
    extra['id']='EXTRA-passenger';extra['keyframes'][0]['visible']='false';write(path,plan)
    assert 'boolean' in compile_episode(project,'ep01')['errors'][0]['error']
    extra['keyframes'][0]['visible']=True;prop['shot_ids']=['unknown'];write(path,plan)
    assert 'shot_ids' in compile_episode(project,'ep01')['errors'][0]['error']
    prop['shot_ids']=['sh1'];plan['cameras']=copy.deepcopy(group['cameras'])
    plan['cameras'][0]['visible_actor_ids']=['CHAR-missing'];write(path,plan)
    assert 'visible_actor_ids' in compile_episode(project,'ep01')['errors'][0]['error']


def test_api_read_only_and_validation(project,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from services.api import whitebox
    monkeypatch.setattr(whitebox.core,'PROJECTS_DIR',project.parent)
    app=FastAPI();app.include_router(whitebox.router,prefix='/api/v1')
    before=set(project.rglob('*'))
    client=TestClient(app)
    result=client.get('/api/v1/projects/demo/whitebox/ep01')
    assert result.status_code==200 and len(result.json()['groups'])==1
    assert result.json()['groups'][0]['artifact_status']=={'plan':False,'preview':False,'video':False}
    assert result.json()['scenes']['SCN-1']['artifact_status']=={'model':True}
    assert set(project.rglob('*'))==before
    assert client.get('/api/v1/projects/demo/whitebox/ep%2E01').status_code==400
    assert client.get('/api/v1/projects/missing/whitebox/ep01').status_code==404
    assert client.post('/api/v1/projects/demo/whitebox/ep01/exports/missing').status_code==422


def test_api_artifact_status_tracks_files_not_preview_availability(project,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from services.api import whitebox
    monkeypatch.setattr(whitebox.core,'PROJECTS_DIR',project.parent)
    app=FastAPI();app.include_router(whitebox.router,prefix='/api/v1')
    client=TestClient(app)
    (project/'bible/scenes/SCN-1/whitebox.json').unlink()
    url='/api/v1/projects/demo/whitebox/ep01'
    data=client.get(url).json()
    assert len(data['groups'])==1 and data['scenes']['SCN-1']['inferred']
    assert data['scenes']['SCN-1']['artifact_status']=={'model':False}
    assert client.get('/api/v1/projects/demo/whitebox/scenes/SCN-1').json()['artifact_status']=={'model':False}
    write(project/'directing/ep01/whitebox_plans/grp1.json',{'continuity':{}})
    write(project/'directing/ep01/whitebox/episode.json',data)
    folder=project/'assets/whitebox/ep01/grp1'
    write(folder/'manifest.json',{'files':['assets/whitebox/ep01/grp1/camera.mp4']})
    assert client.get(url).json()['groups'][0]['artifact_status']=={'plan':True,'preview':True,'video':False}
    (folder/'camera.mp4').write_bytes(b'video fixture')
    assert client.get(url).json()['groups'][0]['artifact_status']['video'] is True


@pytest.mark.parametrize('aspect,width,height', [('16:9',256,144),('9:16',144,256),('1:1',128,128)])
def test_export_encoding_camera_view_only(project,monkeypatch,aspect,width,height):
    """Exercise real FFmpeg encode/duration with deterministic synthetic GPU frames (camera.mp4 only, 2026-09-08)."""
    import base64
    import io
    import shutil
    import subprocess
    from PIL import Image
    import playwright.sync_api
    from modules.whitebox_export import render_videos
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):pytest.skip('FFmpeg unavailable')
    write(project/'settings.json',{'output':{'aspect_preset':'custom','aspect_custom':aspect}})
    im=Image.new('RGB',(width,height),'blue');buf=io.BytesIO();im.save(buf,format='JPEG');frame=base64.b64encode(buf.getvalue()).decode()
    class FakePage:
        def add_init_script(self,*a):pass
        def goto(self,*a):pass
        def wait_for_function(self,*a,**kw):pass
        def evaluate(self,fn,arg):return frame if '.frame(' in fn else None
    class FakeBrowser:
        def new_page(self,**kw):return FakePage()
        def close(self):pass
    class FakePlaywright:
        chromium=None
        def __init__(self):self.chromium=self
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def launch(self,**kw):return FakeBrowser()
    monkeypatch.setattr(playwright.sync_api,'sync_playwright',FakePlaywright)
    result=render_videos(project,compile_episode(project,'ep01'),width=width,height=height,fps=2)
    assert result[0]['frames']==8
    assert result[0]['aspect_ratio']==aspect
    assert result[0]['files']==['assets/whitebox/ep01/grp1/camera.mp4']
    folder=project/'assets/whitebox/ep01/grp1'
    assert not (folder/'top.mp4').exists()
    path=project/result[0]['files'][0]
    info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(path)]))['streams'][0]
    assert float(info['duration'])==4 and info['width']==width and info['height']==height
    pixels=subprocess.check_output(['ffmpeg','-v','error','-i',str(path),'-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','-'])
    assert pixels[2]>200
    # A failed update must retain the previous complete video and its manifest.
    previous={p.name:p.read_bytes() for p in folder.iterdir() if p.is_file()}
    def broken_frame(self,fn,arg):
        if '.frame(' in fn:raise RuntimeError('GPU frame failed')
    monkeypatch.setattr(FakePage,'evaluate',broken_frame)
    with pytest.raises(RuntimeError,match='GPU frame failed'):
        render_videos(project,compile_episode(project,'ep01'),width=width,height=height,fps=2)
    assert {p.name:p.read_bytes() for p in folder.iterdir() if p.is_file()}==previous
    assert not list(folder.glob('.render-*'))


def whitebox_cli(monkeypatch, project, *args):
    import importlib.util
    import sys
    root=Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root/'code'))
    spec=importlib.util.spec_from_file_location('whitebox_cli_test',root/'code/render_whitebox.py')
    cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    monkeypatch.setattr(sys,'argv',['render_whitebox','--project','demo','--out-root',str(project),'--ep','ep01',*args])
    return cli


def test_cli_automatically_saves_videos_without_export_flag(project,monkeypatch):
    cli=whitebox_cli(monkeypatch,project)
    calls=[]
    def save(base,episode,groups,**options):
        calls.append((base,episode,groups,options));return {'rendered':['grp1'],'skipped':[]}
    monkeypatch.setattr(cli,'ensure_videos',save)
    assert cli.main()==0
    assert len(calls)==1 and calls[0][2] is None and calls[0][3]['fps']==24
    assert (project/'directing/ep01/whitebox/episode.json').is_file()
    assert (project/'assets/concepts/scenes/SCN-1/whitebox.scene.json').is_file()


def test_cli_check_only_has_no_export_or_writes(project,monkeypatch):
    cli=whitebox_cli(monkeypatch,project,'--check-only')
    monkeypatch.setattr(cli,'ensure_videos',lambda *a,**kw:pytest.fail('check-only exported'))
    assert cli.main()==0
    assert not (project/'directing/ep01/whitebox').exists()


def test_cli_verify_export_is_read_only(project,monkeypatch,capsys):
    """#85:--verify-export 只在内存里比对,不覆盖已落盘的编译 JSON(字节与 mtime 都不变)。"""
    import os
    episode_json=project/'directing/ep01/whitebox/episode.json'
    scene_json=project/'assets/concepts/scenes/SCN-1/whitebox.scene.json'
    for path in (episode_json,scene_json):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('{"groups": [1.0]}\n',encoding='utf-8')   # 刻意与重编译序列化结果不同
        os.utime(path,(1_000_000_000,1_000_000_000))
    before={p:(p.read_bytes(),p.stat().st_mtime_ns) for p in (episode_json,scene_json)}
    cli=whitebox_cli(monkeypatch,project,'--verify-export')
    monkeypatch.setattr(cli,'ensure_videos',lambda *a,**kw:pytest.fail('verify-export exported'))
    monkeypatch.setattr(cli,'sync_episode',lambda *a,**kw:pytest.fail('verify-export synced refs'))
    assert cli.main()==1   # 无 camera.mp4 → missing
    assert 'whitebox_videos_exported' in capsys.readouterr().out
    assert {p:(p.read_bytes(),p.stat().st_mtime_ns) for p in (episode_json,scene_json)}==before


def test_scene_update_exports_only_referencing_groups(project,monkeypatch):
    import shutil
    path,data=source(project)
    shutil.copytree(project/'assets/concepts/scenes/SCN-1',project/'assets/concepts/scenes/SCN-2')
    data['shots'].append({**data['shots'][0],'shot_id':'sh2','scene_id':'SCN-2'})
    data['generation_groups'].append({**data['generation_groups'][0],'group_id':'grp2','scene_id':'SCN-2','shots':['sh2']})
    write(path,data)
    cli=whitebox_cli(monkeypatch,project,'--scene','SCN-1')
    calls=[]
    monkeypatch.setattr(cli,'ensure_videos',lambda base,e,g,**kw:calls.append(g) or {'rendered':g,'skipped':[]})
    assert cli.main()==0 and calls==[['grp1']]


def test_auto_export_cache_tracks_scene_actor_format_renderer_and_missing_files(project,monkeypatch):
    from modules import whitebox_export as export
    episode=compile_episode(project,'ep01');calls=[]
    def save(base,e,ids,**kw):
        calls.append(list(ids));records=[]
        for group in e['groups']:
            gid=group['group_id']
            if gid not in ids:continue
            files=[f'assets/whitebox/ep01/{gid}/camera.mp4']
            for rel in files:
                p=base/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'video')
            record={'group_id':gid,'fps':kw['fps'],**export.render_format({},kw['width'],kw['height']),
                    'files':files,'source_sha256':export.fingerprint(e,group),'renderer_sha256':export.renderer_fingerprint()}
            write(base/f'assets/whitebox/ep01/{gid}/manifest.json',record);records.append(record)
        return records
    monkeypatch.setattr(export,'render_videos',save)
    assert export.ensure_videos(project,episode)['rendered']==['grp1']
    assert export.ensure_videos(project,episode)['skipped']==['grp1'] and len(calls)==1
    episode['groups'][0]['actors'][0]['keyframes'][0]['yaw']=1
    assert export.ensure_videos(project,episode)['rendered']==['grp1']
    episode['scenes']['SCN-1']['objects'][0]['position'][0]+=1
    assert export.ensure_videos(project,episode)['rendered']==['grp1']
    assert export.ensure_videos(project,episode,width=256,height=144,fps=12)['rendered']==['grp1']
    assert export.ensure_videos(project,episode,width=256,height=144,fps=12)['skipped']==['grp1']
    (project/'assets/whitebox/ep01/grp1/camera.mp4').unlink()
    assert export.ensure_videos(project,episode,width=256,height=144,fps=12)['rendered']==['grp1']
    monkeypatch.setattr(export,'renderer_fingerprint',lambda:'new-renderer')
    assert export.ensure_videos(project,episode,width=256,height=144,fps=12)['rendered']==['grp1']
    assert export.ensure_videos(project,episode,width=256,height=144,fps=12,force=True)['rendered']==['grp1']


def test_cli_propagates_export_failure_instead_of_claiming_saved(project,monkeypatch):
    cli=whitebox_cli(monkeypatch,project)
    def fail(*a,**kw):raise RuntimeError('encoder failed')
    monkeypatch.setattr(cli,'ensure_videos',fail)
    with pytest.raises(RuntimeError,match='encoder failed'):cli.main()
    assert not (project/'assets/whitebox/ep01/grp1/manifest.json').exists()


@pytest.mark.parametrize('output,expected',[
    ({},('16:9',960,540)),
    ({'aspect_preset':'youtube'},('16:9',960,540)),
    ({'aspect_preset':'douyin'},('9:16',540,960)),
    ({'aspect_preset':'cinema'},('21:9',952,408)),
    ({'aspect_preset':'custom','aspect_custom':'4:3'},('4:3',960,720)),
    ({'aspect_preset':'custom','aspect_custom':'1:1'},('1:1',960,960)),
    ({'aspect_preset':'custom','aspect_custom':'2.39 : 1'},('2.39:1',956,400)),
])
def test_project_camera_and_export_format(project,output,expected):
    from modules.whitebox import render_format
    from modules.output_format import resolve_output
    write(project/'settings.json',{'output':output})
    fmt=render_format({'output':output})
    assert (fmt['aspect_ratio'],fmt['width'],fmt['height'])==expected
    assert resolve_output({'output':output})[0]==fmt['aspect_ratio']
    data=compile_episode(project,'ep01')
    assert data['render']==fmt and data['scenes']['SCN-1']['render']==fmt


@pytest.mark.parametrize('width,height',[(960,540),(128,128),(None,960),(541,960)])
def test_export_cannot_override_portrait_aspect(width,height):
    from modules.whitebox import render_format
    with pytest.raises(ValueError):render_format({'output':{'aspect_preset':'douyin'}},width,height)


def test_portrait_full_hd_allowed():
    from modules.whitebox import render_format
    assert render_format({'output':{'aspect_preset':'douyin'}},1080,1920)['height']==1920


@pytest.mark.parametrize('cid', ['CHAR-1','CRE-1'])
def test_airborne_route_and_altitude_beats(project,cid):
    path,data=source(project)
    route=data['generation_groups'][0]['blocking_map']['characters'][0]
    route.update(id=cid,start={'xy':[.1,.5],'altitude_m':2},end={'xy':[.7,.5],'altitude_m':6})
    data['generation_groups'][0]['characters_union']=[cid]
    data['shots'][0]['characters']=[cid]
    write(path,data)
    # A 2D per-shot correction must preserve altitude; altitude-only beats work.
    write(project/'directing/ep01/shots/sh1/blocking.json',{'characters':[
        {'id':cid,'xy_start':[.2,.5],'xy_end':[.8,.5],
         'beats':[{'t':2,'altitude_m':10},{'t':3,'xy':[.7,.6]}]}]})
    result=compile_episode(project,'ep01');assert not result['errors']
    group=result['groups'][0];keys=group['actors'][0]['keyframes']
    assert keys[0]['position'][1]==2 and keys[-1]['position'][1]==6
    assert sample(keys,1)['position'][1]==6
    assert sample(keys,2)['position'][1]==10
    assert sample(keys,3)['position'][1]==8
    assert group['cameras'][0]['keyframes'][0]['target'][1]>2


def test_three_axis_world_positions(project):
    path,data=source(project)
    route=data['generation_groups'][0]['blocking_map']['characters'][0]
    route.update(start={'position':[-1,3,2]},end={'position':[1,7,8]})
    write(path,data)
    result=compile_episode(project,'ep01');assert not result['errors']
    keys=result['groups'][0]['actors'][0]['keyframes']
    assert sample(keys,2)['position']==[0,5,5]


def test_explicit_airborne_shot_endpoints(project):
    write(project/'directing/ep01/shots/sh1/blocking.json',{'characters':[
        {'id':'CHAR-1','position_start':[-1,3,2],'position_end':[1,7,8],
         'beats':[{'t':2,'xy':[.5,.5]}]}]})
    result=compile_episode(project,'ep01');assert not result['errors']
    keys=result['groups'][0]['actors'][0]['keyframes']
    assert sample(keys,2)['position']==[0,5,0]


def test_browser_format_matches_python():
    import shutil
    import subprocess
    from modules.whitebox import render_format
    if not shutil.which('node'):pytest.skip('Node unavailable')
    configs=[{'output':{'aspect_preset':preset,'aspect_custom':ratio}}
             for preset,ratio in [('youtube',''),('douyin',''),('custom','4:3'),('custom','2.39:1')]]
    module=(Path(__file__).resolve().parents[1]/'apps/web/static/whitebox-format.js').as_uri()
    script=f'import {{projectRenderFormat}} from {json.dumps(module)}; console.log(JSON.stringify({json.dumps(configs)}.map(projectRenderFormat)));'
    actual=json.loads(subprocess.check_output(['node','--input-type=module','-e',script],text=True))
    assert actual==[render_format(c) for c in configs]


# ---------------- 整集白模样片(2026-09-08,原名白模合辑;2026-09-11 烧入字幕) ----------------
def _tiny_clip(path, seconds, size='64x36', fps=24):
    import shutil, subprocess
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([shutil.which('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i',
                    f'color=c=gray:s={size}:r={fps}', '-t', str(seconds), '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(path)],
                   check=True)


@pytest.fixture
def reel_project(tmp_path):
    import shutil
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('ffmpeg unavailable')
    base = tmp_path/'demo'
    # 白模开关(output.spatial_blocking)2026-09-16 起缺省=关,白模项目须显式开启
    write(base/'settings.json', {'output': {'spatial_blocking': True}})
    groups = [('grp002', 3), ('grp001', 2), ('grp003', 4)]   # shot_list 顺序故意不按字典序
    write(base/'directing/ep02/shot_list.json', {'shots': [], 'generation_groups': [
        {'group_id': g, 'scene_id': 'SCN-1', 'shots': [f'sh{i}'], 'total_duration_s': d} for i, (g, d) in enumerate(groups)]})
    for g, d in groups:
        _tiny_clip(base/'assets/whitebox/ep02'/g/'camera.mp4', d)
        write(base/'assets/whitebox/ep02'/g/'manifest.json', {'group_id': g, 'duration_s': d, 'fps': 24, 'width': 64, 'height': 36,
                                                              'source_sha256': 'src-'+g, 'files': [f'assets/whitebox/ep02/{g}/camera.mp4']})
    return base


def test_concat_episode_orders_groups_and_tracks_staleness(reel_project):
    from modules.whitebox_export import concat_episode, episode_reel_status
    base = reel_project
    before = episode_reel_status(base, 'ep02')
    assert not before['exists'] and before['groups_missing'] == [] and [g['group_id'] for g in before['order']] == ['grp002', 'grp001', 'grp003']
    (base/'assets/whitebox/ep02/ep02-top.mp4').write_bytes(b'stale')   # 旧版俯视合辑残留应被清掉
    manifest = concat_episode(base, 'ep02')
    reel = base/'assets/whitebox/ep02/ep02-camera.mp4'
    assert reel.is_file() and reel.stat().st_size > 0
    assert manifest['mode'] == 'copy' and manifest['groups'] == 3 and abs(manifest['duration_s']-9) < 0.2
    assert manifest['group_order'] == ['grp002', 'grp001', 'grp003'] and manifest['missing_groups'] == []
    assert manifest['group_sources'] == {'grp001': 'src-grp001', 'grp002': 'src-grp002', 'grp003': 'src-grp003'}
    assert not (base/'assets/whitebox/ep02/ep02-top.mp4').exists()
    saved = json.loads((base/'assets/whitebox/ep02/episode-manifest.json').read_text())
    assert saved['reels'][0]['path'] == 'assets/whitebox/ep02/ep02-camera.mp4' and saved['reels'][0]['bytes'] == reel.stat().st_size
    after = episode_reel_status(base, 'ep02')
    assert after['exists'] and not after['stale']
    # 某组重出(源指纹变)→ 合辑过期
    m = base/'assets/whitebox/ep02/grp001/manifest.json'
    write(m, {**json.loads(m.read_text()), 'source_sha256': 'src-grp001-v2'})
    assert episode_reel_status(base, 'ep02')['stale'] is True


def test_concat_episode_missing_group_requires_allow_missing(reel_project):
    from modules.whitebox_export import concat_episode, episode_reel_status
    base = reel_project
    (base/'assets/whitebox/ep02/grp003/camera.mp4').unlink()
    assert episode_reel_status(base, 'ep02')['groups_missing'] == ['grp003']
    with pytest.raises(ValueError, match='grp003'):
        concat_episode(base, 'ep02')
    assert not (base/'assets/whitebox/ep02/ep02-camera.mp4').exists()
    manifest = concat_episode(base, 'ep02', allow_missing=True)
    assert manifest['groups'] == 2 and manifest['missing_groups'] == ['grp003'] and abs(manifest['duration_s']-5) < 0.2


def test_concat_episode_reencodes_mixed_specs(reel_project):
    from modules.whitebox_export import concat_episode
    base = reel_project
    _tiny_clip(base/'assets/whitebox/ep02/grp003/camera.mp4', 4, size='128x72')
    m = base/'assets/whitebox/ep02/grp003/manifest.json'
    write(m, {**json.loads(m.read_text()), 'width': 128, 'height': 72})
    manifest = concat_episode(base, 'ep02')
    assert manifest['mode'] == 'reencode' and manifest['groups'] == 3 and abs(manifest['duration_s']-9) < 0.2


def test_preview_videos_reports_whitebox_reel(reel_project, monkeypatch):
    from services.runtime import core
    from modules.whitebox_export import concat_episode
    base = reel_project
    monkeypatch.setattr(core, 'PROJECTS_DIR', base.parent)
    w = core._preview_videos('demo', 'ep02')['whitebox']
    assert w['exists'] is False and w['groups_total'] == 3 and w['groups_ready'] == 3 and w['agent'] == '07-directing/whitebox-staging'
    concat_episode(base, 'ep02')
    w = core._preview_videos('demo', 'ep02')['whitebox']
    assert w['exists'] and not w['stale'] and w['name'] == 'ep02-camera.mp4' and w['groups'] == 3
    assert w['url'].startswith('/projects/demo/assets/whitebox/ep02/ep02-camera.mp4?v=')


def test_animated_scene_prop_tilts_without_duplicate_and_resets(project):
    import shutil, subprocess
    keys=[{'t':0,'position':[0,.8,0]}, {'t':2,'position':[1,.9,0],'pitch':.4,'roll':1.2},
          {'t':4,'position':[0,.8,0]}]
    validate_keys(keys,4)
    assert sample(keys,1)['roll']==pytest.approx(.6)
    assert sample(keys,3)['pitch']==pytest.approx(.2)
    bad=copy.deepcopy(keys);bad[1]['roll']=float('nan')
    with pytest.raises(ValueError):validate_keys(bad,4)
    prop={'id':'pot','shape':'cylinder','size_m':[.5,.3,.5],'position':[0,.8,0],'keyframes':keys}
    write(project/'directing/ep01/whitebox_plans/grp1.json',{'props':[prop]})
    e=compile_episode(project,'ep01');assert not e['errors']
    if not shutil.which('node'):pytest.skip('Node unavailable')
    static=Path(__file__).resolve().parents[1]/'apps/web/static'
    scene=e['scenes']['SCN-1'];scene['objects'].append({k:v for k,v in prop.items() if k!='keyframes'})
    script=f'''
import assert from 'node:assert/strict';
import * as T from {json.dumps((static/'vendor/three/three.module.js').as_uri())};
import {{WhiteboxRenderer,sample}} from {json.dumps((static/'whitebox-renderer.js').as_uri())};
const r=Object.create(WhiteboxRenderer.prototype);
Object.assign(r,{{width:960,height:540,scene:null,controls:null,camera:new T.PerspectiveCamera(),overview:new T.PerspectiveCamera(),top:new T.OrthographicCamera()}});
r.load({json.dumps(scene)},{json.dumps(e['groups'][0])});
let count=0;r.scene.traverse(o=>{{if(o.name==='pot')count++;}});assert.equal(count,1);
const p=r.scene.getObjectByName('pot');
r.setTime(1);assert.ok(Math.abs(p.rotation.z-.6)<1e-9);assert.ok(Math.abs(p.rotation.x-.2)<1e-9);assert.equal(p.position.x,.5);
r.setTime(2);assert.ok(Math.abs(p.rotation.z-1.2)<1e-9);
for(const t of [4,0]){{r.setTime(t);assert.equal(p.rotation.x,0);assert.equal(p.rotation.z,0);}}
assert.ok(Math.abs(sample({json.dumps(keys)},3).roll-.6)<1e-9);
r.disposeScene();
'''
    subprocess.run(['node','--input-type=module','-e',script],check=True,capture_output=True,text=True)


def test_projection_screen_validates_cast_and_fixed_plane(project):
    prop = {'id':'curtain','shape':'box','position':[0,1,-1], 'size_m':[2,2,.02],
            'projection_screen':{'actor_ids':['CHAR-1'],'shot_ids':['sh1']}}
    path=project/'directing/ep01/whitebox_plans/grp1.json'
    write(path,{'props':[prop]})
    compiled=compile_episode(project,'ep01')
    assert not compiled['errors']
    assert compiled['groups'][0]['props'][0]['projection_screen']==prop['projection_screen']
    for bad in [{**prop,'yaw':.1}, {**prop,'projection_screen':{'actor_ids':['missing']}},
                {**prop,'projection_screen':{'actor_ids':['CHAR-1'],'shot_ids':['missing']}}]:
        write(path,{'props':[bad]})
        assert compile_episode(project,'ep01')['errors']


# ---------------- 白模样片字幕(2026-09-11) ----------------
@pytest.fixture
def subtitled_reel_project(reel_project):
    """在 reel_project 上加对白/旁白源:shot_list shots(dialogue_lines/narration_ref)+narration_anchors、narration.md、人物名、白模编译镜段。"""
    base = reel_project
    for g, d in (('grp002', 3), ('grp001', 2), ('grp003', 4)):   # 字幕要看得见,组视频放大到 320x180
        _tiny_clip(base/'assets/whitebox/ep02'/g/'camera.mp4', d, size='320x180')
        m = base/'assets/whitebox/ep02'/g/'manifest.json'
        write(m, {**json.loads(m.read_text()), 'width': 320, 'height': 180})
    write(base/'directing/ep02/shot_list.json', {
        'shots': [
            {'shot_id': 'sh0', 'duration_s': 3, 'narration_ref': ['N-01']},
            {'shot_id': 'sh1', 'duration_s': 2, 'dialogue_lines': [
                {'speaker': 'CHAR-0001', 'text': '施主,请留步!', 'est_duration_s': 1.5},
                {'speaker': 'CHAR-0002', 'text': '叫我?', 'est_duration_s': 0.5}]},
            {'shot_id': 'sh2', 'duration_s': 4, 'dialogue_lines': [{'speaker': 'CHAR-0002', 'text': '算命?'}]}],
        'generation_groups': [{'group_id': g, 'scene_id': 'SCN-1', 'shots': [f'sh{i}'], 'total_duration_s': d}
                              for i, (g, d) in enumerate((('grp002', 3), ('grp001', 2), ('grp003', 4)))],
        'narration_anchors': [{'narration_id': 'N-01', 'anchor_shots': ['sh0'], 'anchor_group': 'grp002', 'est_duration_s': 2.0}]})
    (base/'story/episodes/ep02').mkdir(parents=True, exist_ok=True)
    (base/'story/episodes/ep02/narration.md').write_text(
        '# EP02\n\n```\n[N-01 | anchor: S01开场 | est_duration_s: 2.0 | source: ch001#p001]\n我刚出生,道士就说我仙缘深厚。\n```\n', encoding='utf-8')
    write(base/'bible/characters/index.json', {'characters': [{'id': 'CHAR-0001', 'canonical_name': '老道儿'}, {'id': 'CHAR-0002', 'name': '王三合'}]})
    # grp001 有编译镜段(镜从 0.5s 起),其余组走 shot_list 累加兜底
    write(base/'directing/ep02/whitebox/episode.json', {'groups': [
        {'group_id': 'grp001', 'cameras': [{'shot_id': 'sh1', 'start': 0.5, 'duration_s': 1.5}]}]})
    return base


def _frame_band_colors(video, t, height_frac=0.15):
    """取样片 t 秒一帧底部字幕带的像素集合(去重,便于断言有无黄/紫字)。"""
    import shutil, subprocess, tempfile
    from PIL import Image
    with tempfile.TemporaryDirectory() as td:
        png = Path(td)/'f.png'
        subprocess.run([shutil.which('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-y', '-ss', str(t), '-i', str(video),
                        '-frames:v', '1', str(png)], check=True)
        im = Image.open(png).convert('RGB')
        w, h = im.size
        return {im.getpixel((x, y)) for x in range(0, w, 2) for y in range(int(h*(1-height_frac)), h, 2)}


def test_episode_subtitle_cues_from_shot_list_and_narration(subtitled_reel_project):
    from modules.whitebox_export import episode_reel_status
    st = episode_reel_status(subtitled_reel_project, 'ep02')
    cues = st['cues']
    assert [c['kind'] for c in cues] == ['narration', 'dialogue', 'dialogue', 'dialogue']
    nar = cues[0]
    assert nar['text'] == '我刚出生,道士就说我仙缘深厚。' and (nar['start'], nar['end']) == (0.0, 2.0) and nar['group_id'] == 'grp002'
    # grp001 从 3s 起,编译镜段 sh1 起点 0.5s → 3.5s 起;两句按 1.5:0.5 分 1.5s
    d1, d2, d3 = cues[1:]
    assert d1['text'] == '老道儿:施主,请留步!' and (d1['start'], d1['end']) == (3.5, 4.625)
    assert d2['text'] == '王三合:叫我?' and (d2['start'], d2['end']) == (4.625, 5.0)
    assert d3['text'] == '王三合:算命?' and (d3['start'], d3['end']) == (5.0, 9.0)   # grp003 无编译结果 → shot_list 累加
    assert st['subtitles_sha256'] and not st['exists'] and st['stale_reason'] == ''


def test_concat_episode_burns_subtitles_and_tracks_subtitle_staleness(subtitled_reel_project):
    from modules.whitebox_export import concat_episode, episode_reel_status
    base = subtitled_reel_project
    manifest = concat_episode(base, 'ep02')
    assert manifest['mode'] == 'burn' and abs(manifest['duration_s']-9) < 0.2
    assert manifest['subtitles']['cues'] == 4 and manifest['subtitles']['dialogue'] == 3 and manifest['subtitles']['narration'] == 1
    assert manifest['subtitles']['sha256'] == episode_reel_status(base, 'ep02')['subtitles_sha256']
    reel = base/'assets/whitebox/ep02/ep02-camera.mp4'
    yellow = lambda px: px[0] > 180 and px[1] > 140 and px[2] < 110
    purple = lambda px: px[0] > 140 and px[1] < 160 and px[2] > 180
    assert any(purple(px) for px in _frame_band_colors(reel, 1.0))      # 旁白紫
    assert any(yellow(px) for px in _frame_band_colors(reel, 4.0))      # 对白黄
    gap = _frame_band_colors(reel, 2.6)                                  # 2.0–3.5s 无字幕:底部仍是源视频灰
    assert not any(yellow(px) or purple(px) for px in gap) and all(abs(px[0]-px[1]) < 12 and abs(px[1]-px[2]) < 12 for px in gap)
    st = episode_reel_status(base, 'ep02')
    assert st['exists'] and not st['stale']
    # 对白文本改了 → 样片按字幕过期;组视频没变
    sl = base/'directing/ep02/shot_list.json'
    data = json.loads(sl.read_text()); data['shots'][2]['dialogue_lines'][0]['text'] = '算命?这词儿低了些。'; write(sl, data)
    st = episode_reel_status(base, 'ep02')
    assert st['stale'] and st['stale_reason'] == 'subtitles'
    # --no-subtitles:同规格流拷贝,清单字幕为 0,状态提示需重出以补字幕
    manifest = concat_episode(base, 'ep02', subtitles=False)
    assert manifest['mode'] == 'copy' and manifest['subtitles']['cues'] == 0
    assert episode_reel_status(base, 'ep02')['stale_reason'] == 'subtitles'


def test_preview_pages_report_whitebox_reel_subtitles(subtitled_reel_project, monkeypatch):
    from services.runtime import core
    from modules.whitebox_export import concat_episode
    base = subtitled_reel_project
    monkeypatch.setattr(core, 'PROJECTS_DIR', base.parent)
    w = core._preview_storyboard('demo', 'ep02')['whitebox_reel']
    assert w['exists'] is False and w['subtitle_cues'] == 4 and w['subtitle_dialogue'] == 3 and w['subtitle_narration'] == 1
    concat_episode(base, 'ep02')
    w = core._preview_videos('demo', 'ep02')['whitebox']
    assert w['exists'] and w['mode'] == 'burn' and w['subtitles']['cues'] == 4 and w['stale_reason'] == ''
    assert core._preview_storyboard('demo', 'ep02')['whitebox_reel']['url'] == w['url']


def test_preview_storyboard_shot_poses(subtitled_reel_project, monkeypatch):
    """分镜预览人物动作(2026-09-15):shot_list 每镜 poses → 出名字+中文体位;缺则回落故事板草稿 poses;都没有给 []。"""
    from services.runtime import core
    base = subtitled_reel_project
    monkeypatch.setattr(core, 'PROJECTS_DIR', base.parent)
    sl = base/'directing/ep02/shot_list.json'; data = json.loads(sl.read_text())
    data['shots'][1]['poses'] = {'CHAR-0001': {'pose': 'Sit', 'action': '低头'}, 'CHAR-0002': 'stand'}
    data['shots'][2]['storyboard_ref'] = 'S01/order:1'
    write(sl, data)
    write(base/'directing/ep02/storyboard.json', {'scenes': [{'scene_no': 'S01', 'shots_draft': [
        {'order': 1, 'content': 'x', 'poses': {'CHAR-0002': {'pose': 'kneel', 'action': ''}}}]}]})
    shots = {s['shot_id']: s for s in core._preview_storyboard('demo', 'ep02')['shots']}
    assert shots['sh0']['poses'] == []
    assert shots['sh1']['poses'] == [
        {'id': 'CHAR-0001', 'name': '老道儿', 'pose': 'sit', 'pose_zh': '坐', 'action': '低头'},
        {'id': 'CHAR-0002', 'name': '王三合', 'pose': 'stand', 'pose_zh': '站', 'action': ''}]
    assert shots['sh2']['poses'] == [{'id': 'CHAR-0002', 'name': '王三合', 'pose': 'kneel', 'pose_zh': '跪', 'action': ''}]


def test_stills_sample_times_cover_edges_keyframes_and_cap():
    from modules.whitebox_stills import sample_times
    static=sample_times({'start':6.0,'duration_s':6.0,'keyframes':[{'t':0},{'t':6.0}]})
    assert len(static)==3 and 6.0<static[0]<6.1 and static[1]==9.0 and 11.9<static[2]<12.0
    moving=sample_times({'start':0,'duration_s':4.0,'keyframes':[{'t':0},{'t':1.5},{'t':4.0}]})
    assert 1.5 in moving and len(moving)==3
    dense=sample_times({'start':0,'duration_s':10.0,'keyframes':[{'t':i} for i in range(11)]})
    assert len(dense)==5 and dense[0]<0.1 and dense[-1]>9.9 and dense==sorted(dense)


def test_cli_stills_compiles_and_writes_contact_sheet_without_export(project,monkeypatch):
    """--stills = compile-only + one contact sheet per selected group; never exports camera.mp4."""
    import base64
    import io
    from PIL import Image
    import playwright.sync_api
    buf=io.BytesIO();Image.new('RGB',(96,54),'blue').save(buf,format='JPEG');frame=base64.b64encode(buf.getvalue()).decode()
    views=[]
    class FakePage:
        def add_init_script(self,*a):pass
        def goto(self,url):assert url.endswith('whitebox-stills.html')
        def wait_for_function(self,*a,**kw):pass
        def evaluate(self,fn,arg):
            if '.frame(' in fn:views.append(arg[1]);return frame
    class FakeBrowser:
        def new_page(self,**kw):return FakePage()
        def close(self):pass
    class FakePlaywright:
        def __init__(self):self.chromium=self
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def launch(self,**kw):return FakeBrowser()
    monkeypatch.setattr(playwright.sync_api,'sync_playwright',FakePlaywright)
    cli=whitebox_cli(monkeypatch,project,'--stills','grp1')
    monkeypatch.setattr(cli,'ensure_videos',lambda *a,**kw:pytest.fail('--stills exported video'))
    assert cli.main()==0
    sheet=project/'directing/ep01/whitebox/stills/grp1.jpg'
    assert sheet.is_file() and (project/'directing/ep01/whitebox/episode.json').is_file()
    assert not (project/'assets/whitebox').exists()
    shots=len(compile_episode(project,'ep01')['groups'][0]['cameras'])
    assert views.count('overview')==shots and views.count('camera')>=3*shots
    with Image.open(sheet) as im:assert im.width>448 and im.height>=shots*200


def test_export_renderer_fingerprint_ignores_stills_page():
    """Stills page must stay out of the export fingerprint, or every saved camera.mp4 turns stale."""
    import inspect
    from modules import whitebox_export
    assert 'whitebox-stills' not in inspect.getsource(whitebox_export.renderer_fingerprint)


def test_export_fingerprint_ignores_review_fingerprints():
    """#108:镜头上的审查指纹(source/placement_fingerprint)重打不让已导出视频过期;旧口径 manifest 一字未变时仍认。"""
    from modules import whitebox_export as we
    cam = {'shot_id': 'sh001', 'keyframes': [{'t': 0, 'position': [0, 1.5, 4], 'target': [0, 1, 0], 'fov': 27}],
           'source_fingerprint': 'aaa', 'placement_fingerprint': 'bbb'}
    group = {'group_id': 'grp001', 'scene_id': 'SCN-0001', 'cameras': [cam], 'issues': [{'id': 'x'}]}
    episode = {'scenes': {'SCN-0001': {'dimensions_m': [8, 3, 8]}}}
    restamped = {**group, 'cameras': [{**cam, 'source_fingerprint': 'ccc', 'placement_fingerprint': 'ddd'}]}
    assert we.fingerprint(episode, restamped) == we.fingerprint(episode, group)
    moved = {**group, 'cameras': [{**cam, 'keyframes': [{**cam['keyframes'][0], 'fov': 35}]}]}
    assert we.fingerprint(episode, moved) != we.fingerprint(episode, group)
    legacy = we._fingerprint(episode, group, review_marks=True)
    assert legacy != we.fingerprint(episode, group)
    assert we.fingerprint_matches(legacy, episode, group) and we.fingerprint_matches(we.fingerprint(episode, group), episode, group)
    assert not we.fingerprint_matches(legacy, episode, moved) and not we.fingerprint_matches('', episode, group)
    assert cam['source_fingerprint'] == 'aaa'                                   # 不改入参
