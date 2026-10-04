"""场景全景锚点(modules/scene_panos.py)纯函数测试:服务判定、贪心覆盖、渠道能力、等距柱状方向往返、legacy 复用排除。"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules import scene_panos as sp  # noqa: E402
from modules import shot_plates  # noqa: E402


def box(oid, pos, size):
    return {'id': oid, 'position': list(pos), 'size_m': list(size), 'yaw': 0}


SCENE = {'dimensions_m': [20, 4, 12],
         'objects': [box('wall', (0, 2, -6), (20, 4, 0.2)),           # 北墙
                     box('pillar', (0, 2, 0), (0.6, 4, 0.6)),          # 场中一根柱
                     box('bench', (5, 0.25, 3), (3, 0.5, 0.6))]}      # 矮凳(不挡视线、不算贴墙)


def cam(shot, pos, tgt, fov=27.0, ep='ep01', role='start'):
    return {'ep': ep, 'shot_id': shot, 'role': role, 'position': list(pos), 'target': list(tgt), 'fov': fov, 'scheme': 'L1'}


def test_can_serve_distance_and_occlusion():
    c = cam('sh001', (-6, 1.5, 3), (-6, 1.2, -5))      # 看北墙,主体距 8 m → 允许 4 m
    assert sp.can_serve([-6, 1.6, 1], c, SCENE)          # 2 m
    assert not sp.can_serve([-6, 1.6, -2.5], c, SCENE)   # 5.5 m > 4
    c2 = cam('sh002', (-3, 1.5, 0), (3, 1.2, 0))         # 视线穿过场中柱
    assert not sp.can_serve([-2, 1.5, 0], c2, SCENE)
    assert sp.can_serve([-2.5, 1.5, 1.5], cam('sh003', (-3, 1.5, 1.5), (3, 1.2, 1.5)), SCENE)   # 柱旁绕过


def test_plan_anchors_covers_all_with_few_anchors():
    cams = [cam('sh001', (-6, 1.5, 3), (-6, 1.2, -5)), cam('sh002', (-5, 1.5, 2), (-6, 1.2, -5)),
            cam('sh003', (-7, 1.5, 3.5), (-5, 1.2, -5)), cam('sh004', (7, 1.5, 4), (7, 1.2, -5))]
    anchors = sp.plan_anchors(SCENE, cams)
    served = {k for a in anchors for k in a['serves']}
    assert served == {sp._cam_key(c) for c in cams}
    assert 1 <= len(anchors) <= 2                      # 西边三机位一个锚点,东边一个
    for a in anchors:
        assert not any(sp._inside(a['position'], o) for o in SCENE['objects'])
        assert a['anchor_id'].startswith('A')
    assert anchors[0]['position'][1] == 1.6            # 锚点高 = max(1.6, 机高中位数)


def test_plan_anchors_keeps_locked_and_uses_camera_position_when_boxed_in():
    locked = {'anchor_id': 'M1', 'position': [-6, 1.5, 1], 'yaw_deg': 0, 'locked': True, 'source': 'manual', 'serves': [], 'panos': {}}
    boxed = {'dimensions_m': [20, 4, 12], 'objects': [box('ring%d' % i, p, s) for i, (p, s) in enumerate(
        [((8, 2, 4), (0.2, 4, 3)), ((6, 2, 4), (0.2, 4, 3)), ((7, 2, 2.6), (2, 4, 0.2)), ((7, 2, 5.4), (2, 4, 0.2))])]}
    cams = [cam('sh001', (-6, 1.5, 3), (-6, 1.2, -5)), cam('sh009', (7, 1.5, 4), (7, 1.2, 3))]   # sh009 被四面墙围死(网格候选都因贴墙被排除)
    anchors = sp.plan_anchors(boxed, cams, existing=[locked])
    assert anchors[0]['anchor_id'] == 'M1' and anchors[0]['locked']
    assert 'ep01/sh001:start' in anchors[0]['serves']
    own = [a for a in anchors if a['serves'] == ['ep01/sh009:start']]
    assert len(own) == 1 and own[0]['position'][0] == 7 and own[0]['position'][2] == 4   # 机位自身作候选兜底


def test_anchor_height_follows_standing_surface_on_wall_top():
    # fengshen3 SCN-0036:机位在 12 m 关墙顶,锚点不能落在绝对 2 m 的墙体实心里
    wall = {'dimensions_m': [60, 30, 60], 'objects': [box('wall', (0, 6, 0), (6, 12, 50)), box('house', (-15, 2, 0), (6, 4, 4))]}
    top = [cam('sh140', (0, 13.6, -8), (0, 13.6, 5)), cam('sh141', (0, 13.6, 2), (0, 13.6, 8))]
    ground = cam('sh001', (-10, 1.5, 10), (-15, 1.5, 0))
    assert sp.standing_surface(wall, top[0]['position']) == 12 and sp.standing_surface(wall, ground['position']) == 0
    assert sp.default_anchor_height(top, wall) == 1.6
    anchors = sp.plan_anchors(wall, top + [ground])
    assert {k for a in anchors for k in a['serves']} == {sp._cam_key(c) for c in top + [ground]}
    for a in anchors:
        assert not any(sp._inside(a['position'], o) for o in wall['objects'])
        on_top = any(k.startswith('ep01/sh14') for k in a['serves'])
        assert a['position'][1] == (13.6 if on_top else 1.6)
        assert not (on_top and 'ep01/sh001:start' in a['serves'])          # 墙顶锚点不服务墙下机位
    assert sp.anchor_pos_at(wall, 0, 0, top, 1.6) == [0, 13.6, 0]             # 手动点在墙顶 → 上墙顶
    assert sp.anchor_pos_at(wall, -10, 10, [ground], 1.6) == [-10, 1.6, 10]
    assert 'raised 12' in sp._lens_height_words(wall, [0, 13.6, 0]) and sp._lens_height_words(wall, [-10, 1.6, 10]) == '1.6 m above the floor'


def test_pano_support_matrix():
    assert sp.pano_support({'provider': 'volcengine', 'model': 'doubao-seedream-5-0-pro-260628'})[0]
    assert sp.pano_support({'provider': 'fal', 'model': 'fal-ai/bytedance/seedream/v5/lite'})[0]
    assert not sp.pano_support({'provider': 'fal', 'model': 'fal-ai/nano-banana-pro'})[0]
    assert not sp.pano_support({'provider': 'openrouter', 'model': 'google/gemini-3-pro-image'})[0]
    assert sp.pano_support({'provider': 'comfyui', 'model': 'wf'})[0]


def test_equirect_direction_roundtrip():
    """_pano_points 的像素→方向与 _sample_pano 的方向→像素互为逆(yaw 0,同一锚点重投影应落回同一像素)。"""
    W, H = 64, 32
    for (col, row) in ((0, 0), (16, 8), (32, 16), (48, 24), (63, 31)):
        theta = (col + .5) / W * 2 * math.pi - math.pi
        phi = (row + .5) / H * math.pi
        dx = math.sin(phi) * math.sin(theta); dy = math.cos(phi); dz = -math.cos(theta) * math.sin(phi)
        t2 = math.atan2(dx, -dz); p2 = math.acos(max(-1, min(1, dy)))
        assert int((t2 + math.pi) / (2 * math.pi) * W) == col
        assert int(p2 / math.pi * H) == row


def test_scheme_slug_and_legacy_reuse_excluded():
    assert sp.scheme_slug('LGT-0002-01') == 'LGT-0002-01'
    assert sp.scheme_slug('', '下午') != 'default' and sp.scheme_slug('', None) == 'default'
    facts = {'position': [0, 1.5, 0], 'target': [10, 1.5, 0], 'bearing_deg': 90.0, 'pitch_deg': 0.0, 'fov_v_deg': 27.0, 'height_class': 2}
    legacy = {'key': 'k1', 'file': 'x.png', 'lighting_scheme_id': 'L1', 'camera': dict(facts)}
    per_shot = {'key': 'k2', 'file': 'y.png', 'lighting_scheme_id': 'L1', 'camera': dict(facts), 'pano_ref': {'anchor_id': 'A1'}}
    master = {'key': 'k3', 'file': 'z.png', 'master': True, 'lighting_scheme_id': 'L1', 'camera': {**facts, 'fov_v_deg': 55.0},
              'pano_ref': {'anchor_id': 'A1'}}
    # 2026-09-14 母图制:白模帧直出与逐镜全景直出的旧图都是 legacy,只有母图可被派生
    assert shot_plates.is_legacy(legacy) and shot_plates.is_legacy(per_shot) and not shot_plates.is_legacy(master)
    assert shot_plates.find_master({'plates': [legacy, per_shot]}, 'L1', facts, 16/9, Path('.'), require_file=False) is None
    assert shot_plates.find_master({'plates': [legacy, per_shot, master]}, 'L1', facts, 16/9, Path('.'), require_file=False) is master


def test_build_prompt_uses_pano_reference_only():
    facts = {'position': [0, 1.5, 0], 'target': [0, 1.2, 5], 'fov_v_deg': 55.0, 'fov_h_deg': 85.4, 'lens_mm_equiv': 23.0, 'height_m': 1.5,
             'height_word': 'eye level', 'tilt_word': 'level horizon, no tilt', 'standing': 'on the floor', 'facing': 'north',
             'facing_cardinal': 'north', 'facing_desc': '', 'frame_left': 'west', 'frame_right': 'east', 'behind': 'south', 'behind_desc': '',
             'horizon_pct_from_top': 50}
    p = shot_plates.build_prompt(facts, ['a shelf on the left'], {'size_code': 'MS'}, {'time_of_day': 'afternoon'},
                                 {'scene_id': 'S', 'name': 'Hall'}, {}, 'film; shallow depth of field bokeh; grain', 'soft light', '', 'start')
    assert '[Image 1] is a photograph of this exact location re-projected' in p
    assert 'whitebox' not in p and 'top-down layout map' not in p
    # 母图口径(2026-09-14):镜头按视场写、不用景别词;视场不外扩;风格串去浅景深
    assert 'wide-angle view, 23.0mm-equivalent lens' in p and 'medium framing' not in p
    assert 'do not add a ceiling' in p and 'deep focus' in p
    assert 'Style: film; grain' in p and 'bokeh' not in p.split('Style:')[1]
    p_end = shot_plates.build_prompt(facts, [], {'size_code': 'MS'}, {}, {'scene_id': 'S'}, {}, '', '', '', 'end')
    assert '[Image 2] is the finished master background plate of the same shot at the start' in p_end


# ---------------- 预览页「创建全景图」(2026-09-13):手动锚点 + only 模式只出这一张 ----------------
def _fake_scene_env(tmp_path, monkeypatch):
    from modules import whitebox
    monkeypatch.setattr(whitebox, 'load_scene', lambda base, sid: SCENE)
    monkeypatch.setattr(sp, 'is_indoor', lambda base, sid: False)
    return tmp_path


def test_add_manual_anchor_clamps_and_serves_only_unserved(tmp_path, monkeypatch):
    base = _fake_scene_env(tmp_path, monkeypatch)
    cams = [cam('sh001', (-6, 1.5, 3), (-6, 1.2, -5)), cam('sh002', (8, 1.5, 3), (8, 1.2, -5))]
    # 已有锚点 A1 服务 sh001
    idx = sp.load_index(base, 'SCN-0001')
    idx['anchors'].append({'anchor_id': 'A1', 'position': [-6, 1.6, 1], 'yaw_deg': 0, 'source': 'auto', 'locked': False,
                           'serves': [sp._cam_key(cams[0])], 'panos': {}})
    sp.save_index(base, 'SCN-0001', idx)
    a = sp.add_manual_anchor(base, 'SCN-0001', 40, 2, 30, cameras=cams)     # x 超出地面 → 夹回 w/2-.5
    assert a['anchor_id'] == 'A2' and a['locked'] and a['source'] == 'manual'
    assert a['position'] == [9.5, 1.6, 2.0] and a['yaw_deg'] == 30
    assert a['serves'] == [sp._cam_key(cams[1])]            # 只接管尚无锚点服务的机位
    idx = sp.load_index(base, 'SCN-0001')
    assert [x['anchor_id'] for x in idx['anchors']] == ['A1', 'A2'] and idx['anchors'][0]['serves'] == [sp._cam_key(cams[0])]


def test_scene_scheme_options_prefers_camera_schemes_then_bible(tmp_path):
    (tmp_path / 'bible/scenes/SCN-0001').mkdir(parents=True)
    (tmp_path / 'bible/scenes/SCN-0001/lighting.json').write_text('{"schemes":[{"scheme_id":"L1"},{"scheme_id":"N1","time_of_day":"night"}]}')
    opts = sp.scene_scheme_options(tmp_path, 'SCN-0001', [cam('sh001', (0, 1.5, 3), (0, 1, -5))])
    assert [(o['scheme'], o['in_use']) for o in opts] == [('L1', True), ('N1', False)]
    assert sp.scene_scheme_options(tmp_path, 'SCN-0002', [])[0]['scheme'] == 'default'


def test_scheme_summary_readable_label():
    """下拉显示名:时段 · 空间 — 主光 / 方向 / 色温,括号里的补充说明去掉;编号只留尾段;两种 lighting.json 写法都认。"""
    nested = {'scheme_id': 'LGT-SCN-0036-DUSK-EAST-A', 'condition': {'time_of_day': '黄昏', 'weather': ['海雾', '薄阴']}, 'contrast': '高反差(剪影级)',
              'key_light': {'source': '日光·低角直射(落日沉入西侧叠山)', 'direction': '侧逆光', 'azimuth': '落日在画右后方的西天;人物走向画右即走向逆光',
                            'color_temp': '暖黄(~2900K)'}}
    r = sp.scheme_summary(nested, 'LGT-SCN-0036-DUSK-EAST-A')
    assert r == {'label': '黄昏 — 日光·低角直射 / 侧逆光 / 暖黄', 'code': 'DUSK-EAST-A', 'weather': '海雾、薄阴',
                 'azimuth': '落日在画右后方的西天;人物走向画右即走向逆光', 'contrast': '高反差'}
    flat = {'id': 'LGT-0231-07', 'condition': {'time_of_day': '深夜', 'weather': '夜间无雨', 'sub_space': '一楼厨房'},
            'key_source': '荧光灯管', 'direction': '顶光', 'color_temp': '混色(暖+冷)'}
    r = sp.scheme_summary(flat, 'LGT-0231-07')
    assert (r['label'], r['code']) == ('深夜 · 一楼厨房 — 荧光灯管 / 顶光 / 混色', '07')
    assert sp.scheme_summary({}, 'default') == {'label': '', 'code': '', 'weather': '', 'azimuth': '', 'contrast': ''}
    assert sp.scheme_summary({}, 'night', 'night')['label'] == 'night'
    assert len(sp.scheme_summary({'key_source': '案上烛台的余烬' * 10}, 'x')['label']) <= 18


def test_ensure_only_renders_new_anchor_without_touching_others(tmp_path, monkeypatch):
    base = _fake_scene_env(tmp_path, monkeypatch)
    rendered, generated = [], []
    monkeypatch.setattr(sp, 'render_whitebox_pano', lambda base, sid, a, **kw: rendered.append(a['anchor_id']))
    monkeypatch.setattr(sp, 'whitebox_pano_stale', lambda base, sid, a: a['anchor_id'] == 'A2')
    monkeypatch.setattr(sp, 'check_pano_support', lambda *a, **kw: {})
    monkeypatch.setattr(sp, 'generate_pano', lambda base, sid, idx, a, s, **kw: generated.append((a['anchor_id'], s)))
    monkeypatch.setattr(sp, 'pano_ready', lambda base, sid, a, s: a['anchor_id'] == 'A1')
    idx = sp.load_index(base, 'SCN-0001')
    idx['anchors'].append({'anchor_id': 'A1', 'position': [-6, 1.6, 1], 'yaw_deg': 0, 'source': 'auto', 'locked': False, 'serves': [], 'panos': {}})
    sp.save_index(base, 'SCN-0001', idx)
    a = sp.add_manual_anchor(base, 'SCN-0001', 2, 2, cameras=[])
    # 没有机位也允许(only + 显式方案);A1 不重渲、不重出
    stats = sp.ensure_scene_panos(base, 'SCN-0001', cameras=[], schemes={'L1': None}, only=[a['anchor_id']])
    assert rendered == ['A2'] and generated == [('A2', 'L1')] and stats['new'] == 1
    assert [x['anchor_id'] for x in sp.load_index(base, 'SCN-0001')['anchors']] == ['A1', 'A2']
    import pytest
    with pytest.raises(sp.PanoError):
        sp.ensure_scene_panos(base, 'SCN-0001', cameras=[], schemes={'L1': None}, only=['A9'])


# ---- 2026-09-20 投影保障:外景引导线 / 风格段过滤 / 投影机检
def test_pano_style_drops_single_view_composition_clauses():
    from modules import scene_panos as sp
    out = sp.pano_style('cinematic 3D CG, hair solved strand by strand; cool base with warm accents; strong backlight rimming the subject '
                        'with god rays, shadows never crushed to black; four to six layers of depth; fine film grain')
    assert 'cool base with warm accents' in out and 'fine film grain' in out and 'shadows never crushed' in out
    for bad in ('subject', 'layers of depth', 'hair', 'backlight'):
        assert bad not in out


def test_projection_check_separates_equirect_from_wide_angle(tmp_path):
    import numpy as np
    from PIL import Image
    from modules import scene_panos as sp
    rng = np.random.default_rng(1)
    h, w = 512, 1024
    tex = rng.integers(0, 255, (h, w, 3)).astype('uint8')
    photo = tmp_path / 'photo.png'; Image.fromarray(tex).save(photo)                      # 通幅同样清晰 = 广角照片
    eq = tex.copy()
    for r in list(range(0, 40)) + list(range(h - 40, h)):                                 # 两极横向抹平 = 拉伸
        eq[r] = eq[r].mean(axis=0, keepdims=True)
    pano = tmp_path / 'pano.png'; Image.fromarray(eq).save(pano)
    assert sp.projection_check(photo)['verdict'] == 'FAIL'
    assert sp.projection_check(pano)['verdict'] != 'FAIL'


def test_projection_guides_only_touch_free_sky_and_ground():
    import numpy as np
    from PIL import Image
    from modules import scene_panos as sp
    h, w = 128, 256
    depth = np.zeros((h, w), dtype='float32'); valid = np.zeros((h, w), dtype=bool)
    depth[40:90, 100:140] = 0.8; valid[40:90, 100:140] = True                            # 一块几何(比镜头高度近,不会与地面距离重合)
    out = np.asarray(sp.draw_projection_guides(Image.new('RGB', (w, h), (200, 200, 200)), valid, depth, 1.8, 0.0), dtype=int)
    assert (out[40:90, 100:140] == 200).all()
    assert (out[:30] != 200).any() and (out[-30:] != 200).any()
    assert (out[:, w // 2] == 200).sum() > h * .7                                         # 没有整幅高的竖线穿过天顶/天底


# ---- 2026-09-20 锚点可见性:室内外混合场景(洞内锚点 + 墙外云台)
def _mixed_scene():
    wall = lambda i, x, z, sx, sz: {'id': i, 'shape': 'box', 'position': [x, 2, z], 'size_m': [sx, 4, sz]}
    return {'dimensions_m': [40, 4, 40], 'objects': [
        wall('wall_n', 0, -5, 10.4, .4), wall('wall_s', 0, 5, 10.4, .4), wall('wall_w', -5, 0, .4, 10.4), wall('wall_e', 5, 0, .4, 10.4),
        {'id': 'furnace', 'shape': 'box', 'position': [2, .75, -2], 'size_m': [1.5, 1.5, 1.5]},
        {'id': 'inner_pond', 'shape': 'box', 'position': [-2, 0, -2], 'size_m': [2, .04, 2]},
        {'id': 'old_pine', 'shape': 'box', 'position': [0, 3, 14], 'size_m': [2, 6, 2]}]}


_MIXED_LAYOUT = {'landmarks': [{'id': 'furnace', 'name': '丹炉', 'xy': [.55, .45]}, {'id': 'inner_pond', 'name': '洞内莲池', 'xy': [.45, .45]},
                               {'id': 'old_pine', 'name': '门前古松', 'xy': [.5, .85]}],
                 'orientation': {'bottom_of_map': '山道端——门前古松与石阶'}}


def test_anchor_view_hides_objects_behind_walls_and_keeps_floor_flush_ones():
    from modules import scene_panos as sp
    view = sp.anchor_view(_mixed_scene(), {'position': [0, 1.6, 0], 'yaw_deg': 0}, True)
    assert view['enclosed']
    assert view['objects']['old_pine']['px'] == 0
    assert view['objects']['furnace']['px'] > sp.VIS_MIN_PX and view['objects']['inner_pond']['px'] > sp.VIS_MIN_PX
    inv = ' '.join(sp.object_inventory(_mixed_scene(), _MIXED_LAYOUT, {'position': [0, 1.6, 0], 'yaw_deg': 0}, view))
    assert '丹炉' in inv and '洞内莲池' in inv and '古松' not in inv
    assert '古松' not in sp.sector_sentence(_mixed_scene(), _MIXED_LAYOUT, view, 'behind')


def test_indoor_clauses_drop_outside_only_clauses():
    from modules import scene_panos as sp
    view = sp.anchor_view(_mixed_scene(), {'position': [0, 1.6, 0], 'yaw_deg': 0}, True)
    vis, hid = sp.visible_landmark_words(_mixed_scene(), _MIXED_LAYOUT, view)
    out = sp.indoor_clauses('洞内只有丹炉的一团火,星月把云海照成冷白,反光经洞门渗进来', vis, hid, fine=True)
    assert '丹炉' in out and '洞门' in out and '云海' not in out
    assert '古松' not in sp.indoor_clauses('丹炉:青铜半哑光; 门前古松:树皮龟裂厚鳞', vis, hid)


def test_conformity_check_flags_image_that_ignores_whitebox(tmp_path):
    import cv2
    import numpy as np
    from modules import scene_panos as sp
    h, w = 512, 1024
    depth = np.full((h, w), 8.0, dtype='float32'); depth[150:400, 300:500] = 3.0; depth[200:380, 700:860] = 4.0
    np.save(tmp_path / 'd.npy', depth)
    good = np.full((h, w, 3), 90, dtype='uint8'); good[150:400, 300:500] = 200; good[200:380, 700:860] = 30
    bad = np.full((h, w, 3), 90, dtype='uint8'); cv2.circle(bad, (150, 256), 90, (220, 220, 220), -1)
    cv2.imwrite(str(tmp_path / 'good.png'), good); cv2.imwrite(str(tmp_path / 'bad.png'), bad)
    assert sp.conformity_check(tmp_path / 'good.png', tmp_path / 'd.npy')['verdict'] == 'PASS'
    assert sp.conformity_check(tmp_path / 'bad.png', tmp_path / 'd.npy')['verdict'] == 'WARN'      # 只警不拦:不当花钱重出的闸门


def test_adopt_rejected_restores_latest_rejected_pano(tmp_path):
    import json
    from PIL import Image
    from modules import scene_panos as sp
    d = tmp_path / 'assets/concepts/scenes/S/panos'; (d / 'A1').mkdir(parents=True)
    (d / 'index.json').write_text(json.dumps({'schema_version': sp.SCHEMA, 'scene_id': 'S', 'anchors': [
        {'anchor_id': 'A1', 'position': [0, 1.6, 0], 'yaw_deg': 0, 'serves': [], 'panos': {}}]}), encoding='utf-8')
    src = d / 'A1' / 'LGT-X.rejected-projection-20260920-194953.png'
    Image.new('RGB', (400, 200), (90, 120, 150)).save(src)
    src.with_suffix('.json').write_text(json.dumps({'seed': 7, 'prompt': 'p', 'mode': 'fresh', 'rejected': 'projection'}), encoding='utf-8')
    rec = sp.adopt_rejected(tmp_path, 'S', 'A1', log=lambda *a: None)
    assert (d / 'A1' / 'LGT-X.png').is_file() and not src.exists()
    assert rec['seed'] == 7 and rec['adopted']['rejected_as'] == 'projection'
    assert sp.load_index(tmp_path, 'S')['anchors'][0]['panos']['LGT-X']['file'] == 'LGT-X.png'


def test_discard_archived_hides_image_from_preview_and_adopt(tmp_path):
    import json
    import pytest
    from PIL import Image
    from modules import scene_panos as sp
    d = tmp_path / 'assets/concepts/scenes/S/panos'; (d / 'A1').mkdir(parents=True)
    anchor = {'anchor_id': 'A1', 'position': [0, 1.6, 0], 'yaw_deg': 0, 'serves': [], 'panos': {}}
    (d / 'index.json').write_text(json.dumps({'schema_version': sp.SCHEMA, 'scene_id': 'S', 'anchors': [anchor]}), encoding='utf-8')
    src = d / 'A1' / 'LGT-X.rejected-projection-20260920-194953.png'
    Image.new('RGB', (400, 200), (90, 120, 150)).save(src)
    src.with_suffix('.json').write_text(json.dumps({'seed': 7, 'rejected': 'projection'}), encoding='utf-8')
    Image.new('RGB', (400, 200), (90, 120, 150)).save(d / 'A1' / 'LGT-X.png')            # 正式全景:不受理
    assert [r['file'] for r in sp.archived_panos(tmp_path, 'S', anchor)] == [src.name]
    rec = sp.discard_archived(tmp_path, 'S', 'A1', src.name, log=lambda *a: None)
    assert rec['moved_to'] == f'discarded/{src.name}'
    assert not src.exists() and not src.with_suffix('.json').exists()
    assert (d / 'A1' / 'discarded' / src.name).is_file() and (d / 'A1' / 'discarded' / src.name).with_suffix('.json').is_file()
    assert sp.archived_panos(tmp_path, 'S', anchor) == []
    (d / 'A1' / 'LGT-X.png').unlink()
    with pytest.raises(sp.PanoError):
        sp.adopt_rejected(tmp_path, 'S', 'A1', log=lambda *a: None)                       # 弃用后 --adopt 也不再认
    Image.new('RGB', (400, 200), (90, 120, 150)).save(d / 'A1' / 'LGT-X.png')
    for bad in ('LGT-X.png', '../A1/LGT-X.png', 'missing.redo-20260920-194953.png'):
        with pytest.raises(sp.PanoError):
            sp.discard_archived(tmp_path, 'S', 'A1', bad, log=lambda *a: None)
    assert (d / 'A1' / 'LGT-X.png').is_file()


# ---- 2026-09-20 接缝朝向 + 按服务机位视域判局部缺陷
def _cam(shot, pos, tgt, fov=30):
    return {'ep': 'ep01', 'shot_id': shot, 'role': 'start', 'position': pos, 'target': tgt, 'fov': fov}


def test_pick_seam_yaw_points_seam_away_from_cameras():
    from modules import scene_panos as sp
    south = [_cam('s1', [0, 1.5, 0], [0, 1.5, 10]), _cam('s2', [1, 1.5, 0], [2, 1.5, 9])]      # 都朝 +Z(yaw 0 的接缝方向)看
    yaw = sp.pick_seam_yaw(south)
    assert yaw == 180.0                                                                         # 接缝转到 -Z,画面中心朝机位方向
    anchor = {'yaw_deg': yaw, 'serves': [sp._cam_key(c) for c in south]}
    assert sp.anchor_usage(anchor, south)['seam'] == []
    assert len(sp.anchor_usage({**anchor, 'yaw_deg': 0.0}, south)['seam']) == 2
    assert sp.pick_seam_yaw([]) == 0.0


def test_projection_check_local_defects_follow_camera_usage(tmp_path):
    import numpy as np
    from PIL import Image
    from modules import scene_panos as sp
    h, w = 512, 1024
    rows = np.random.default_rng(2).integers(0, 120, (h, 1))                                    # 逐行随机 = 纵向细节足、横向拉丝
    img = np.repeat((rows + np.linspace(0, 130, w)[None, :])[..., None], 3, axis=2).astype('uint8')  # 再叠横向渐变:左右缘差到底
    path = tmp_path / 'seam.png'; Image.fromarray(img).save(path)
    assert sp.projection_check(path)['verdict'] == 'WARN'                                       # 不知道机位:照旧 WARN
    free = sp.projection_check(path, usage={'known': True, 'cameras': 3, 'seam': [], 'nadir': []})
    assert free['verdict'] == 'PASS' and free['notes']
    assert sp.projection_check(path, usage={'known': True, 'cameras': 3, 'seam': ['ep01/s1:start'], 'nadir': []})['verdict'] == 'FAIL'


def test_add_manual_anchor_persist_false_leaves_index_untouched(tmp_path, monkeypatch):
    import json
    from modules import scene_panos as sp
    d = tmp_path / 'assets/concepts/scenes/S/panos'; d.mkdir(parents=True)
    (d / 'index.json').write_text(json.dumps({'schema_version': sp.SCHEMA, 'scene_id': 'S', 'anchors': []}), encoding='utf-8')
    before = (d / 'index.json').read_text(encoding='utf-8')
    monkeypatch.setattr('modules.whitebox.load_scene', lambda base, sid: {'dimensions_m': [20, 4, 20], 'objects': []})
    a = sp.add_manual_anchor(tmp_path, 'S', -1, 0.85, cameras=[], persist=False)
    assert a['anchor_id'] == 'A1' and a['position'][0] == -1.0
    assert (d / 'index.json').read_text(encoding='utf-8') == before
    sp.add_manual_anchor(tmp_path, 'S', -1, 0.85, cameras=[])
    assert [x['anchor_id'] for x in sp.load_index(tmp_path, 'S')['anchors']] == ['A1']


def test_cameras_outside_floor_share_one_anchor():
    from modules import scene_panos as sp
    scene = {'dimensions_m': [40, 10, 20], 'objects': []}
    far = [_cam(f'f{i}', [x, 5, z], [0, 1, 0]) for i, (x, z) in enumerate(((-2, 40), (3, 80), (1, 126)))]   # 都在南缘外,夹回点相距 ≤ 5 m
    plan = sp.plan_anchors(scene, far)
    assert len(plan) == 1 and len(plan[0]['serves']) == 3


def test_outdoor_serve_radius_scales_with_floor_short_side():
    from modules import scene_panos as sp
    assert sp.serve_min_m({'dimensions_m': [40, 10, 22.5]}) == sp.SERVE_MIN_M                       # 没标室外 = 室内口径
    assert sp.serve_min_m({'dimensions_m': [40, 10, 22.5], '_outdoor': True}) == 5.62
    assert sp.serve_min_m({'dimensions_m': [60, 10, 8], '_outdoor': True}) == sp.SERVE_MIN_M         # 狭长街道按短边,不放宽
    assert sp.serve_min_m({'dimensions_m': [320, 10, 180], '_outdoor': True}) == sp.OUTDOOR_SERVE_MIN_MAX_M
    cam = _cam('c', [5, 1.5, 0], [5, 1.5, -2])                                                     # 近景:主体 2 m → 室内半径 3 m
    assert not sp.can_serve([0, 1.6, 0], cam, {'dimensions_m': [40, 10, 22.5], 'objects': []})
    assert sp.can_serve([0, 1.6, 0], cam, {'dimensions_m': [40, 10, 22.5], 'objects': [], '_outdoor': True})


# ---- 2026-09-20 室内外判定:读结构化字段,不对整份 JSON 做子串搜索(旧版搜到的是字段名 "indoor" 自己)
def _bible(tmp_path, sid, lighting=None, entry=None):
    import json
    b = tmp_path / 'bible/scenes'; (b / sid).mkdir(parents=True, exist_ok=True)
    if lighting is not None:
        (b / sid / 'lighting.json').write_text(json.dumps(lighting, ensure_ascii=False), encoding='utf-8')
    idx = json.loads((b / 'index.json').read_text(encoding='utf-8')) if (b / 'index.json').is_file() else {'scenes': []}
    idx['scenes'].append({'id': sid, **(entry or {})})
    (b / 'index.json').write_text(json.dumps(idx, ensure_ascii=False), encoding='utf-8')


def test_scene_indoor_reads_explicit_flag_not_key_name(tmp_path):
    from modules import scene_panos as sp
    _bible(tmp_path, 'SCN-0001', {'indoor': False, 'enclosure': '室外·全开阔', 'schemes': [{'note': '总兵府前厅室内门窗天光'}]})
    _bible(tmp_path, 'SCN-0002', {'indoor': True, 'enclosure': '室内'})
    _bible(tmp_path, 'SCN-0003', {'indoor': False, 'enclosure': '内外交替·洞内(室内)与门前平台'})
    _bible(tmp_path, 'SCN-0004', None, {'int_ext': 'INT/EXT'})
    _bible(tmp_path, 'SCN-0005', None, {'name': '总兵府前厅正堂(内景)', 'parent': 'SCN-0001'})
    _bible(tmp_path, 'SCN-0006', None, {'name': '河湾浅滩', 'parent': 'SCN-0001'})
    _bible(tmp_path, 'SCN-0007', {'schemes': [{'condition': {'weather': ['室内']}}]})                      # 旧版设定:没有 indoor 字段
    got = {s: sp.scene_indoor(tmp_path, s) for s in ('SCN-0001', 'SCN-0002', 'SCN-0003', 'SCN-0004', 'SCN-0005', 'SCN-0006', 'SCN-0007')}
    assert got == {'SCN-0001': False, 'SCN-0002': True, 'SCN-0003': None, 'SCN-0004': None, 'SCN-0005': True, 'SCN-0006': False, 'SCN-0007': True}
    assert sp.is_indoor(tmp_path, 'SCN-0003') is False


def test_anchor_indoor_by_enclosure_in_mixed_scene(tmp_path):
    from modules import scene_panos as sp
    _bible(tmp_path, 'SCN-0009', None, {'int_ext': 'INT/EXT'})
    scene = _mixed_scene()                                                                          # 10×10 通顶墙围出的室 + 墙外古松
    assert sp.anchor_indoor(tmp_path, 'SCN-0009', scene, {'position': [0, 1.6, 0], 'yaw_deg': 0}) is True
    assert sp.anchor_indoor(tmp_path, 'SCN-0009', scene, {'position': [0, 1.6, 15], 'yaw_deg': 0}) is False
    assert sp.anchor_indoor(tmp_path, 'SCN-0009', scene, {'position': [0, 1.6, 15], 'yaw_deg': 0}, override=True) is True


def test_mixed_scene_serve_radius_is_per_point():
    from modules import scene_panos as sp
    scene = {**_mixed_scene(), '_mixed': True}                                   # 40×40 地面:中间 10×10 通顶墙围出的室,墙外开阔
    assert sp.point_walled(scene, [0, 1.6, 0]) >= sp.WALLED_MIN > sp.point_walled(scene, [0, 1.6, 15])
    assert sp.serve_min_m(scene, [0, 1.6, 0], [1, 1.5, 1]) == sp.SERVE_MIN_M     # 室内锚点 + 室内机位:3 m
    assert sp.serve_min_m(scene, [-12, 1.6, 15], [-8, 1.5, 15]) > sp.SERVE_MIN_M # 室外锚点 + 室外机位:放宽
    assert sp.serve_min_m(scene, [0, 1.6, 9], [0, 1.5, 0]) == sp.SERVE_MIN_M     # 室外锚点服务室内机位:不放宽
    assert sp.serve_min_m(scene, [0, 1.6, 9], [0, 1.5, 12]) <= 4.0               # 贴着外墙的室外锚点:不超过到墙的距离
    near = _cam('n', [-8, 1.5, 15], [-8, 1.5, 17])                               # 近景(主体 2 m)
    assert sp.can_serve([-12, 1.6, 15], near, scene) and not sp.can_serve([-12, 1.6, 15], near, {**_mixed_scene()})


def test_ground_plan_is_projected_under_the_camera():
    import numpy as np
    from PIL import Image
    from modules import scene_panos as sp
    h, w = 128, 256
    plan = Image.new('RGB', (400, 400), (150, 140, 120)); plan.paste((20, 50, 70), (0, 0, 400, 200))      # 上半(-Z)是水,下半是沙
    depth = np.zeros((h, w), dtype='float32'); valid = np.zeros((h, w), dtype=bool)
    grey = Image.new('RGB', (w, h), (200, 200, 200))
    kw = dict(ground_plan=plan, floor_wd=(40, 40))
    in_water = np.asarray(sp.draw_projection_guides(grey, valid, depth, 1.8, 0.0, cam_xz=(0, -10), **kw), dtype=int)
    on_sand = np.asarray(sp.draw_projection_guides(grey, valid, depth, 1.8, 0.0, cam_xz=(0, 10), **kw), dtype=int)
    assert in_water[-6:, :, 2].mean() > in_water[-6:, :, 0].mean() + 20            # 站在水里:天底偏蓝
    assert on_sand[-6:, :, 0].mean() > on_sand[-6:, :, 2].mean() + 10              # 站在沙上:天底偏暖
    assert (in_water[: h // 2 - 8] == np.asarray(sp.draw_projection_guides(grey, valid, depth, 1.8, 0.0), dtype=int)[: h // 2 - 8]).all()   # 天空不受影响


# ---- 2026-09-21 DEF-p6-pano-001:提示词列位须与白模全景同一套旋转;认领不拿别的位姿出的图
def test_object_inventory_column_matches_rendered_pano_at_every_yaw():
    import re
    from modules import scene_panos as sp
    scene = {'dimensions_m': [40, 4, 40], 'objects': [{'id': 'rock', 'shape': 'box', 'position': [6, 1, -3], 'size_m': [1, 2, 1]}]}
    for yaw in (0, 90, 180, 270):
        anchor = {'position': [0, 1.6, 0], 'yaw_deg': yaw}
        cols = sp.anchor_view(scene, anchor, False)['objects']['rock']['cols']      # 白模全景里实际落在哪些列
        seen = float(cols.mean()) / sp.VIS_SIZE[0] * 100
        said = int(re.search(r'about (\d+)% across', sp.object_inventory(scene, {}, anchor)[0]).group(1))
        assert abs(said - seen) < 3, (yaw, said, seen)


def _write_pano(d, aid, scheme, rec):
    import json
    (d / aid).mkdir(parents=True, exist_ok=True)
    (d / aid / f'{scheme}.json').write_text(json.dumps(rec), encoding='utf-8')


def test_adopt_rejected_skips_other_pose_and_honours_pick(tmp_path):
    import json, os
    from PIL import Image
    from modules import scene_panos as sp
    d = tmp_path / 'assets/concepts/scenes/S/panos'; (d / 'A2').mkdir(parents=True)
    (d / 'index.json').write_text(json.dumps({'schema_version': sp.SCHEMA, 'scene_id': 'S', 'anchors': [
        {'anchor_id': 'A2', 'position': [4, 1.8, -5.75], 'yaw_deg': 270, 'serves': [], 'panos': {}}]}), encoding='utf-8')
    for i, (stamp, yaw) in enumerate((('20260921-084915', 270), ('20260921-085624', 270), ('20260921-090318', 180))):
        f = d / 'A2' / f'LGT-X.rejected-projection-{stamp}.png'
        Image.new('RGB', (400, 200), (90, 120, 150)).save(f)
        f.with_suffix('.json').write_text(json.dumps({'seed': i, 'anchor': {'position': [4, 1.8, -5.75], 'yaw_deg': yaw}}), encoding='utf-8')
        os.utime(f, (1000 + i, 1000 + i))
    rec = sp.adopt_rejected(tmp_path, 'S', 'A2', log=lambda *a: None, pick='084915')
    assert rec['seed'] == 0 and rec['adopted']['from'].endswith('084915.png')
    (d / 'A2' / 'LGT-X.png').unlink()
    idx = sp.load_index(tmp_path, 'S'); idx['anchors'][0]['panos'] = {}; sp.save_index(tmp_path, 'S', idx)
    rec = sp.adopt_rejected(tmp_path, 'S', 'A2', log=lambda *a: None)             # 最新一张是 yaw 180 的:跳过,取 085624
    assert rec['seed'] == 1


# ---- 2026-10-04 去掉链式补洞:别的锚点已有同方案全景也不当参考,每张独立出图(fengshen3 SCN-0036 A5 整张照抄 A1 的城外视角)
def test_generate_pano_never_references_other_anchor_panos(tmp_path, monkeypatch):
    import contextlib, json
    from PIL import Image
    from modules import genmedia, scene_panos as sp, whitebox
    d = sp.panos_dir(tmp_path, 'S')
    for aid in ('A1', 'A5'):
        (d / aid).mkdir(parents=True)
        Image.new('RGB', (64, 32), (128, 128, 128)).save(d / aid / 'whitebox_pano.jpg')
        (d / aid / 'depth_pano.json').write_text(json.dumps({'indoor': False, 'guides': sp.GUIDES_VERSION}), encoding='utf-8')
    Image.new('RGB', (400, 200), (40, 60, 90)).save(d / 'A1' / 'L.png')
    _write_pano(d, 'A1', 'L', {'mode': 'fresh', 'file': 'L.png'})
    a1 = {'anchor_id': 'A1', 'position': [0, 2, 0], 'yaw_deg': 0, 'serves': [], 'panos': {'L': {'file': 'L.png', 'mode': 'fresh'}}}
    a5 = {'anchor_id': 'A5', 'position': [3, 62, 3], 'yaw_deg': 0, 'serves': [], 'panos': {}}
    idx = {'schema_version': sp.SCHEMA, 'scene_id': 'S', 'anchors': [a1, a5]}
    sent = {}

    def fake_image(prompt, target, **kw):
        sent.update(prompt=prompt, refs=kw['refs'])
        Image.new('RGB', (400, 200), (40, 60, 90)).save(target)
    monkeypatch.setattr(genmedia, 'generate_image', fake_image)
    monkeypatch.setattr(genmedia, 'get_config', lambda kind: {'provider': 'x', 'model': 'y'})
    monkeypatch.setattr(genmedia, 'image_pref_env', lambda kind: contextlib.nullcontext())
    monkeypatch.setattr(whitebox, 'load_scene', lambda base, sid: {'dimensions_m': [20, 4, 20], 'objects': []})
    monkeypatch.setattr(sp, 'anchor_view', lambda scene, anchor, indoor: {'enclosed': False})
    monkeypatch.setattr(sp, 'pano_prompt', lambda *a, **kw: 'BODY')
    monkeypatch.setattr(sp, 'projection_check', lambda *a, **kw: None)
    monkeypatch.setattr(sp, 'conformity_check', lambda *a, **kw: None)
    rec = sp.generate_pano(tmp_path, 'S', idx, a5, 'L', indoor=False, seed=1, log=lambda *a: None, cameras=[])
    assert rec['mode'] == 'fresh' and rec['parent'] is None
    assert [Path(r).name for r in sent['refs']] == ['whitebox_pano.jpg']              # 只有本锚点白模(本例无俯视图)
    assert 'another standpoint' not in sent['prompt']
    assert not list((d / 'A5').glob('*.chain_*'))


# ---- 2026-10-04 地面水陆:按俯视图颜色 + 纹理判水;陆上锚点明写「脚下是陆地」,图幅外陆地一侧不再延伸成暗青色(fengshen3 SCN-0036 A5)
def _water_plan():
    """上半幅平滑海面;下半幅 = 左:偏青但纹理粗的背阴林地(颜色与水面相近),右:暖色沙地。"""
    import numpy as np
    from PIL import Image
    rng = np.random.default_rng(7)
    a = np.zeros((288, 512, 3), dtype=np.float64)
    a[:144] = (93, 113, 120)
    forest = np.repeat(np.repeat(rng.uniform(.45, 1.55, (36, 64)), 4, axis=0), 4, axis=1)[..., None] * np.array([56, 66, 67.])
    a[144:, :256] = forest[:, :256]
    a[144:, 256:] = (176, 164, 154)
    return Image.fromarray(np.clip(a + rng.normal(0, 1.5, a.shape), 0, 255).astype('uint8'))


def test_water_mask_separates_sea_from_shaded_forest_and_sand():
    m = sp.water_mask(_water_plan())
    h, w = m.shape
    assert m[: h // 2 - 6].mean() > .95                     # 海面
    assert m[h // 2 + 6:, : w // 2 - 6].mean() < .05        # 背阴林地:同样偏青,靠纹理排除
    assert m[h // 2 + 6:, w // 2 + 6:].mean() == 0          # 沙地
    import numpy as np
    from PIL import Image
    assert not sp.water_mask(Image.fromarray(np.full((90, 160, 3), (205, 216, 230), dtype='uint8'))).any()   # 云海 / 白玉地面:太亮
    assert not sp.water_mask(Image.fromarray(np.full((90, 160, 3), (8, 10, 14), dtype='uint8'))).any()       # 黑边


def test_ground_cover_asserts_only_dry_land():
    m = sp.water_mask(_water_plan())
    land = sp.ground_cover(m, (40, 22.5), [-15, 1.8, 8])
    sea = sp.ground_cover(m, (40, 22.5), [0, 1.8, -8])
    shore = sp.ground_cover(m, (40, 22.5), [10, 1.8, 0.3])
    assert (land['nadir'], sea['nadir'], shore['nadir']) == ('land', 'water', 'shore') and land['offmap_land']
    assert 'dry land directly beneath the camera' in sp.ground_cover_rule(land) and 'unmapped dry land' in sp.ground_cover_rule(land)
    for cover in (sea, shore):                                # 判为水 / 岸边:不下断言,沿用「贴图显示是水就画水」的条件句
        rule = sp.ground_cover_rule(cover)
        assert rule.startswith('Where it shows water directly beneath the camera') and 'not water' not in rule
    assert not sp.ground_cover(m[: m.shape[0] // 2 - 6], (40, 10), [0, 1.8, 0])['offmap_land']     # 四边全是水:图幅外没有陆地段


def test_offmap_ground_is_earth_toned_on_land_side_and_stays_water_on_water_side():
    import numpy as np
    from PIL import Image
    plan = _water_plan(); m = sp.water_mask(plan)
    w, h = 256, 128
    valid = np.zeros((h, w), dtype=bool); depth = np.full((h, w), 1e9)
    grey = Image.new('RGB', (w, h), (200, 200, 200))
    kw = dict(ground_plan=plan, floor_wd=(40, 22.5))
    # 相机在林地一角(西南角内 1 m),yaw 0:画面中心朝北(海),身后(左右缘)与左侧朝图幅外的陆地
    new = np.asarray(sp.draw_projection_guides(grey, valid, depth, 1.8, 0.0, cam_xz=(-19, 10.25), water=m, **kw), dtype=int)
    old = np.asarray(sp.draw_projection_guides(grey, valid, depth, 1.8, 0.0, cam_xz=(-19, 10.25), **kw), dtype=int)
    band = slice(h // 2 + 6, h // 2 + 24)
    assert old[band, :8, 2].mean() >= old[band, :8, 0].mean()                    # 旧画法:图幅外延伸林地边缘,偏青
    assert new[band, :8, 0].mean() > new[band, :8, 2].mean() + 15                # 新画法:图幅外陆地偏暖土色
    # 相机在海里靠北缘:朝北的图幅外仍是水(延伸边缘像素),与旧画法逐像素相同
    sea_new = np.asarray(sp.draw_projection_guides(grey, valid, depth, 1.8, 0.0, cam_xz=(0, -10.25), water=m, **kw), dtype=int)
    sea_old = np.asarray(sp.draw_projection_guides(grey, valid, depth, 1.8, 0.0, cam_xz=(0, -10.25), **kw), dtype=int)
    mid = slice(w // 2 - 20, w // 2 + 20)
    assert (sea_new[band, mid] == sea_old[band, mid]).all() and sea_new[band, mid, 2].mean() > sea_new[band, mid, 0].mean() + 10
