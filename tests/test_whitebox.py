"""Portable whitebox geometry, timing, continuity, API and encoding regression tests."""
import copy
import json
import math
from pathlib import Path

import pytest

from modules.whitebox import compile_episode, load_scene, sample, validate_keys, xyz


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


def test_group_time_and_cast(project):
    result=compile_episode(project,'ep01');assert not result['errors']
    group=result['groups'][0];assert group['duration_s']==4
    actor=group['actors'][0];assert (actor['letter'],actor['color'])==('A','#e63946')
    assert sample(actor['keyframes'],2)['position']==pytest.approx([-1,0,0])
    assert group['cameras'][0]['keyframes'][0]=={**group['cameras'][0]['keyframes'][-1],'t':0}


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


def test_javascript_interpolation_matches_compiler():
    import shutil
    import subprocess
    if not shutil.which('node'):pytest.skip('Node unavailable')
    keys=[{'t':0,'position':[0,0,0],'yaw':2.9,'pose':'stand','easing':'smooth'},
          {'t':2,'position':[2,0,0],'yaw':-2.9,'pose':'sit','hold':True},
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


def test_override_keeps_identity(project):
    group=compile_episode(project,'ep01')['groups'][0];actor=group['actors'][0]
    actor.update(color='#ffffff',letter='Z')
    write(project/'directing/ep01/whitebox_plans/grp1.json',{'actors':[actor]})
    actual=compile_episode(project,'ep01')['groups'][0]['actors'][0]
    assert (actual['color'],actual['letter'])==('#e63946','A')


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
    assert set(project.rglob('*'))==before
    assert client.get('/api/v1/projects/demo/whitebox/ep%2E01').status_code==400
    assert client.get('/api/v1/projects/missing/whitebox/ep01').status_code==404
    assert client.post('/api/v1/projects/demo/whitebox/ep01/exports/missing').status_code==422


def test_export_encoding_two_views(project,monkeypatch):
    """Exercise real FFmpeg mux/crop/duration with deterministic synthetic GPU frames."""
    import base64
    import io
    import shutil
    import subprocess
    from PIL import Image
    import playwright.sync_api
    from modules.whitebox_export import render_videos
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):pytest.skip('FFmpeg unavailable')
    im=Image.new('RGB',(128,256),'red');im.paste('blue',(0,128,128,256));buf=io.BytesIO();im.save(buf,format='JPEG');frame=base64.b64encode(buf.getvalue()).decode()
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
    result=render_videos(project,compile_episode(project,'ep01'),width=128,height=128,fps=2)
    assert result[0]['frames']==8
    for rel,color in zip(result[0]['files'],['red','blue']):
        path=project/rel
        info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(path)]))['streams'][0]
        assert float(info['duration'])==4 and info['width']==128 and info['height']==128
        pixels=subprocess.check_output(['ffmpeg','-v','error','-i',str(path),'-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','-'])
        assert pixels[0]>200 if color=='red' else pixels[2]>200
