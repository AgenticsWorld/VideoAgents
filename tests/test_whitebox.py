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


def test_actor_colors_allow_only_explicit_mount_sharing():
    from modules.whitebox import PALETTE, unused_actor_color, validate_actor_colors
    actors = [{'id': 'CHAR-1', 'color': PALETTE[0]},
              {'id': 'CRE-1', 'kind': 'creature', 'color': PALETTE[0], 'rider': 'CHAR-1'}]
    validate_actor_colors(actors)
    del actors[1]['rider']
    with pytest.raises(ValueError, match='share color'):
        validate_actor_colors(actors)
    with pytest.raises(ValueError, match='exhausted'):
        unused_actor_color([{'color': c} for c in PALETTE])


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


def test_mounted_creature_scale(project):
    path,data=source(project);g=data['generation_groups'][0]
    g['creatures_union']=['CRE-1'];g['blocking_map']['characters'][0]['mounted']='CRE-1'
    write(path,data);result=compile_episode(project,'ep01');assert not result['errors']
    rider,mount=result['groups'][0]['actors']
    assert mount['letter']=='' and mount['kind']=='creature'
    assert rider['keyframes'][0]['pose']=='sit'
    assert rider['keyframes'][0]['position'][1]-mount['keyframes'][0]['position'][1]==1.45


def test_missing_cast_blocks_instead_of_disappearing(project):
    path,data=source(project);data['generation_groups'][0]['creatures_union']=['CRE-1'];write(path,data)
    result=compile_episode(project,'ep01')
    assert not result['groups'] and 'CRE-1' in result['errors'][0]['error']


@pytest.mark.parametrize('bad',[-1,float('nan'),float('inf'),True])
def test_invalid_scale_rejected(project,bad):
    path=project/'bible/scenes/SCN-1/whitebox.json';data=json.loads(path.read_text());data['dimensions_m'][0]=bad;write(path,data)
    assert compile_episode(project,'ep01')['errors']


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


# ---------------- 整集白模合辑(2026-09-08) ----------------
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
