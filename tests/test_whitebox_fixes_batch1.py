"""白模体检第一批修复(2026-11-05):卧/趴入画与朝向、蹲姿 bend 插值、关键帧缺通道、道具顶层朝向回退、
切点容差、道具图例名字回退、placement 指纹按组缓存、导演台插帧保留 hold / easing、键盘监听只注册一次、存版按钮 id。"""
import json
import math
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from modules.whitebox import compile_episode, sample

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'apps/web/static'
RENDERER = (STATIC / 'whitebox-renderer.js').as_uri()


def node(script):
    if not shutil.which('node'):
        pytest.skip('Node unavailable')
    return json.loads(subprocess.check_output(['node', '--input-type=module', '-e', script], text=True, stderr=subprocess.DEVNULL))


# ---------------------------------------------------------------- 卧 / 趴:入画包围盒与朝向句
def test_lying_bounds_extend_from_feet_along_body():
    from modules.whitebox_refs import actor_bounds
    actor = {'size_m': [.4, 1.6, .3]}
    center, half = actor_bounds(actor, {'pose': 'lie', 'position': [1, .5, 2], 'yaw': 0})
    assert center[0] == pytest.approx(1) and center[2] == pytest.approx(2 - .8)          # 沿 −Z 一个身长
    assert half[0] == pytest.approx(.2) and half[2] == pytest.approx(.8)
    center, half = actor_bounds(actor, {'pose': 'prone', 'position': [0, 0, 0], 'yaw': math.pi / 2})
    assert center[0] == pytest.approx(-.8) and half[0] == pytest.approx(.8) and half[2] == pytest.approx(.2)


def test_lying_head_only_close_up_counts_as_appearing():
    from modules.whitebox_refs import appearing_cast
    actor = {'id': 'CHAR-1', 'size_m': [.4, 1.6, .3], 'keyframes': [
        {'t': 0, 'position': [0, 0, 0], 'pose': 'lie', 'yaw': 0}, {'t': 2, 'position': [0, 0, 0], 'pose': 'lie', 'yaw': 0}]}
    # 俯拍头部特写:头在 z≈−1.5,镜头只看 z∈[−1.9,−1.1],看不到脚锚点 ±0.8 m 的旧包围盒
    cam = {'shot_id': 'sh1', 'start': 0, 'duration_s': 2, 'keyframes': [
        {'t': 0, 'position': [0, 1.2, -1.5], 'target': [0, 0, -1.501], 'fov': 30},
        {'t': 2, 'position': [0, 1.2, -1.5], 'target': [0, 0, -1.501], 'fov': 30}]}
    assert appearing_cast({'actors': [actor], 'cameras': [cam]}, {'width': 960, 'height': 540})['visible'] == ['CHAR-1']


@pytest.mark.parametrize('pose,yaw,cam,expected', [
    ('prone', 0.0, [0, 1, 5], ('prone-feet', '')),        # 脚朝 +Z 机位:脚朝镜头(旧逻辑会写成正脸)
    ('prone', math.pi, [0, 1, 5], ('prone-head', '')),
    ('lie', 0.0, [0, 1, -5], ('lie-head', '')),
    ('lie', math.pi / 2, [0, 1, 5], ('lie-side', 'left')),
    ('stand', 0.0, [0, 1, 5], ('front', '')),
])
def test_lying_facing_buckets(pose, yaw, cam, expected):
    from modules.whitebox_refs import FACING_PHRASES, facing_bucket
    got = facing_bucket({'pose': pose, 'yaw': yaw, 'position': [0, 0, 0]}, {'position': cam, 'target': [0, 0, 0], 'fov': 40})
    assert got == expected
    assert FACING_PHRASES[got[0]].format(side=got[1])


# ---------------------------------------------------------------- 插值:蹲姿 bend、缺通道,Python / JS 一致
def test_crouch_bend_default_and_missing_channels_match_renderer():
    keys = [{'t': 0, 'position': [0, 0, 0], 'pose': 'crouch', 'yaw': .5},
            {'t': 1, 'position': [1, 0, 0], 'pose': 'crouch', 'bend': .3},          # 缺 yaw:沿用前一帧
            {'t': 2, 'position': [2, 0, 0], 'pose': 'stand', 'yaw': 1.0, 'bend': .2},
            {'t': 3, 'position': [3, 0, 0], 'pose': 'stand', 'yaw': 1.0}]
    times = [0, .001, .5, 1, 1.5, 2.5, 3]
    py = [sample(keys, t) for t in times]
    assert py[1]['bend'] == pytest.approx(.6 - .3 * .001)   # 从缺省 0.6 出发,不再从 0 弹起
    assert py[2]['bend'] == pytest.approx(.45) and py[2]['yaw'] == pytest.approx(.5)
    assert py[5]['bend'] == pytest.approx(.1)                 # 站姿缺省 0
    js = node(f'import {{sample}} from {json.dumps(RENDERER)};console.log(JSON.stringify({json.dumps(times)}.map(t=>sample({json.dumps(keys)},t))));')
    for a, b in zip(py, js):
        assert b['position'] == pytest.approx(a['position'])
        assert b.get('bend', 0) == pytest.approx(a.get('bend', 0)) and b.get('yaw', 0) == pytest.approx(a.get('yaw', 0))


# ---------------------------------------------------------------- 渲染器:道具顶层朝向回退、切点容差
def test_renderer_prop_top_level_rotation_and_shot_boundary():
    cams = [{'shot_id': f'sh{i}', 'start': s, 'duration_s': d, 'keyframes': [
        {'t': 0, 'position': [0, 1, 5], 'target': [0, 1, 0], 'fov': 40}, {'t': d, 'position': [0, 1, 5], 'target': [0, 1, 0], 'fov': 40}]}
        for i, (s, d) in enumerate([(0, 1.1), (1.1, 2.2), (1.1 + 2.2, 1.0)], 1)]
    group = {'group_id': 'grp1', 'duration_s': 1.1 + 2.2 + 1.0, 'actors': [], 'extras': [], 'cameras': cams, 'props': [
        {'id': 'stone', 'size_m': [1, .5, .5], 'yaw': -.2, 'scale': [1, 2, 1],
         'keyframes': [{'t': 0, 'position': [0, .25, 0]}, {'t': 4.3, 'position': [0, .25, 0]}]}]}
    scene = {'dimensions_m': [10, 3, 10], 'objects': []}
    out = node(f'''
import * as THREE from {json.dumps((STATIC / 'vendor/three/three.module.js').as_uri())};
import {{WhiteboxRenderer}} from {json.dumps(RENDERER)};
const r=Object.create(WhiteboxRenderer.prototype);
Object.assign(r,{{width:960,height:540,scene:null,controls:null,camera:new THREE.PerspectiveCamera(),overview:new THREE.PerspectiveCamera(),top:new THREE.OrthographicCamera()}});
r.load({json.dumps(scene)},{json.dumps(group)});
r.setTime(1);const m=r.props[0].mesh;const a={{yaw:m.rotation.y,scale:m.scale.toArray()}};
r.setTime(3.3);const shot=r.shotId;r.setTime(1.1);const shot2=r.shotId;r.disposeScene();
console.log(JSON.stringify({{...a,shot,shot2}}));''')
    assert out['yaw'] == pytest.approx(-.2) and out['scale'] == [1, 2, 1]
    assert out['shot'] == 'sh3' and out['shot2'] == 'sh2'


# ---------------------------------------------------------------- 道具图例:名字回退道具库
def test_prop_legend_label_falls_back_to_bible_name():
    from modules.whitebox_refs import prop_legend_rows
    group = {'props': [{'id': 'PROP-0024', 'size_m': [.2, .2, .2], 'position': [0, 0, 0], 'color': '#d62828'},
                       {'id': 'cone', 'ref': 'PROP-0003', 'shape': 'cone', 'size_m': [.05, .1, .05], 'position': [0, 0, 0]},
                       {'id': 'curtain', 'size_m': [1, 2, .05], 'position': [0, 0, 0], 'label': '帘子'},
                       {'id': 'misc', 'size_m': [1, 1, 1], 'position': [0, 0, 0]}]}
    rows = prop_legend_rows(group, None, ['PROP-0024', 'cone', 'curtain', 'misc'], {'PROP-0024': '杏黄旗', 'PROP-0003': '冰淇淋球'})
    assert rows[0] == 'red box = 杏黄旗 (PROP-0024)' and '= 冰淇淋球 (cone)' in rows[1]
    assert '= 帘子 (curtain)' in rows[2] and rows[3].endswith('= misc (misc)')


# ---------------------------------------------------------------- placement 指纹按组缓存:文件一变即重算
def test_placement_cache_invalidates_on_file_change(tmp_path):
    from modules import whitebox_camera as wc
    base = tmp_path / 'demo'
    (base / 'directing/ep01/whitebox_plans').mkdir(parents=True)
    (base / 'bible/scenes/SCN-1').mkdir(parents=True)
    shot_list = {'generation_groups': [{'group_id': 'grp1', 'scene_id': 'SCN-1', 'blocking_map': {'characters': []}}]}
    (base / 'directing/ep01/shot_list.json').write_text(json.dumps(shot_list))
    plan = base / 'directing/ep01/whitebox_plans/grp1.json'
    plan.write_text(json.dumps({'props': [{'id': 'a', 'size_m': [1, 1, 1], 'position': [0, 0, 0]}]}))
    scene = base / 'bible/scenes/SCN-1/whitebox.json'
    scene.write_text(json.dumps({'dimensions_m': [5, 3, 5], 'objects': []}))
    fp = wc.placement_fingerprint(base, 'ep01', 'grp1')
    assert wc._matches_placement(fp, base, 'ep01', 'grp1')
    assert wc._matches_placement(fp, base, 'ep01', 'grp1')            # 命中缓存
    plan.write_text(json.dumps({'props': [{'id': 'a', 'size_m': [1, 1, 1], 'position': [0, 0, 10.5]}]}))
    assert not wc._matches_placement(fp, base, 'ep01', 'grp1')
    scene.write_text(json.dumps({'dimensions_m': [5, 3, 5], 'objects': [{'id': 'wall', 'size_m': [1, 1, 1], 'position': [0, 0, 0]}]}))
    assert not wc._matches_placement(wc.placement_fingerprint(base, 'ep01', 'grp1') + 'x', base, 'ep01', 'grp1')
    assert wc._matches_placement(wc.placement_fingerprint(base, 'ep01', 'grp1'), base, 'ep01', 'grp1')
    # 存量旧口径(无前缀)指纹仍按整份场景比对
    legacy = wc.placement_fingerprint(base, 'ep01', 'grp1', legacy=True)
    assert wc._matches_placement(legacy, base, 'ep01', 'grp1')


# ---------------------------------------------------------------- 导演台页面
DIRECTOR = (STATIC / 'preview_director.html').read_text(encoding='utf-8')


def test_director_ids_and_keyboard_listener_registered_once():
    assert len(re.findall(r'id="snap"', DIRECTOR)) == 1 and 'id="snapver"' in DIRECTOR and "$('#snapver')" in DIRECTOR
    # 顶层的 Escape 关菜单监听只执行一次;renderMonitorShell 里(首载 / 每次切集都重跑)的那处必须带只注册一次的保护
    shell = re.search(r'function renderMonitorShell\(\)\{.*?\n\}', DIRECTOR, re.S).group(0)
    assert shell.count("document.addEventListener('keydown'") == 1
    assert "if(!KEYS_BOUND){KEYS_BOUND=true;document.addEventListener('keydown'" in shell


def test_director_inserted_keyframe_keeps_hold_and_easing():
    body = re.search(r'function keyIndexAt\(keys,t\)\{.*?\n\}', DIRECTOR, re.S).group(0)
    keys = [{'t': 0, 'position': [0, 0, 0], 'hold': True}, {'t': 4, 'position': [4, 0, 0], 'easing': 'smooth'},
            {'t': 8, 'position': [8, 0, 0]}]
    out = node(f'''import {{sample}} from {json.dumps(RENDERER)};
{body}
const k={json.dumps(keys)};const before=[1,3,3.9,5,7].map(t=>sample(k,t).position[0]);
keyIndexAt(k,2);keyIndexAt(k,6);
const after=[1,3,3.9,5,7].map(t=>sample(k,t).position[0]);
console.log(JSON.stringify({{before,after,keys:k}}));''')
    assert out['after'][:3] == out['before'][:3] == [0, 0, 0]          # 停住段插帧后仍停住,到点瞬移
    inserted = {k['t']: k for k in out['keys']}
    assert inserted[2].get('hold') is True and inserted[6].get('easing') == 'smooth'
