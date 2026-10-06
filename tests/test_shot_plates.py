"""分镜背景图(modules/shot_plates.py)纯函数测试:运镜分档、指纹容差复用、prompt 接线重排。"""
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules import shot_plates as sp  # noqa: E402

FMT = {'width': 960, 'height': 540, 'aspect_ratio': '16:9'}
AXES = ((1, 0), (0, -1), {})   # 上北下南左西右东


def cam(pos, tgt, fov=27.0):
    return {'position': list(pos), 'target': list(tgt), 'fov': fov}


def test_bearing_maps_map_axes_to_compass():
    ex, ez, _ = AXES
    assert sp.compass(sp.bearing_deg(0, -1, ex, ez)) == 'north'   # -z = 图上方 = 北
    assert sp.compass(sp.bearing_deg(1, 0, ex, ez)) == 'east'
    assert sp.compass(sp.bearing_deg(0, 1, ex, ez)) == 'south'


def test_plate_roles_by_movement():
    static = {'movement': 'static', 'keyframes': [cam((0, 1.5, 0), (0, 1.2, 10)), cam((0, 1.5, 0), (0, 1.2, 10))]}
    assert sp.plate_roles(static)['roles'] == ['start']
    push = {'movement': 'push_in', 'keyframes': [cam((0, 1.5, 0), (0, 1.2, 10)), cam((0, 1.5, 2), (0, 1.2, 10))]}
    assert sp.plate_roles(push)['roles'] == ['start']
    pan = {'movement': 'pan_left', 'keyframes': [cam((0, 1.5, 0), (0, 1.2, 10)), cam((0, 1.5, 0), (-10, 1.2, 0))]}
    assert sp.plate_roles(pan)['roles'] == ['start']
    small_track = {'movement': 'track_right', 'keyframes': [cam((0, 1.5, 0), (0, 1.2, 10)), cam((0.5, 1.5, 0), (0.5, 1.2, 10))]}
    assert sp.plate_roles(small_track)['roles'] == ['start']          # 0.5 m < 10 m 的 10%
    big_track = {'movement': 'follow', 'keyframes': [cam((0, 1.5, 0), (0, 1.2, 10)), cam((8, 1.5, 0), (8, 1.2, 10))]}
    assert sp.plate_roles(big_track)['roles'] == ['start', 'end']
    crane = {'movement': 'crane_up', 'keyframes': [cam((0, 1.5, 0), (0, 1.2, 10)), cam((0, 4, 0), (0, 1.2, 10))]}
    assert sp.plate_roles(crane)['roles'] == ['start', 'end']


def facts(pos, tgt, fov=27.0):
    ex, ez, texts = AXES
    return sp.camera_facts(cam(pos, tgt, fov), FMT, ex, ez, texts)


ASPECT = FMT['width'] / FMT['height']


def turned(pos, deg, pitch_deg=0.0, reach=10.0):
    """朝北偏 deg°(顺时针)、俯仰 pitch_deg 的目标点。"""
    b = math.radians(deg); p = math.radians(pitch_deg)
    return (pos[0] + math.sin(b)*math.cos(p)*reach, pos[1] + math.sin(p)*reach, pos[2] - math.cos(b)*math.cos(p)*reach)


def test_find_master_requires_fit_same_spot_and_scheme(tmp_path):
    base = tmp_path
    m = facts((0, 1.5, 0), (0, 1.5, -10), 55)   # 母图:朝北,55° 垂直视场(16:9 水平 ≈85°)
    master = {'key': 'M', 'file': 'plates/M.png', 'master': True, 'camera': m, 'lighting_scheme_id': 'LGT-1', 'pano_ref': {'anchor_id': 'A1'}}
    legacy = {'key': 'L', 'file': 'plates/L.png', 'camera': m, 'lighting_scheme_id': 'LGT-1', 'pano_ref': {'anchor_id': 'A1'}}   # 逐镜直出旧图:不再派生
    lib = {'plates': [master, legacy]}
    assert sp.is_legacy(legacy) and not sp.is_legacy(master)
    # 同机位、同轴、40 mm 级(33°)→ 派生
    assert sp.find_master(lib, 'LGT-1', facts((0, 1.5, 0), (0, 1.5, -10), 33), ASPECT, base, require_file=False) is master
    # 偏转 12°、俯 3°、33° 视场仍装得下 → 派生;偏转 40° 出画 → 不派生
    assert sp.find_master(lib, 'LGT-1', facts((0, 1.5, 0), turned((0, 1.5, 0), 12, -3), 33), ASPECT, base, require_file=False) is master
    assert sp.find_master(lib, 'LGT-1', facts((0, 1.5, 0), turned((0, 1.5, 0), 40), 33), ASPECT, base, require_file=False) is None
    # 比母图还宽 → 不派生;换方案 → 不派生
    assert sp.find_master(lib, 'LGT-1', facts((0, 1.5, 0), (0, 1.5, -10), 60), ASPECT, base, require_file=False) is None
    assert sp.find_master(lib, 'LGT-2', facts((0, 1.5, 0), (0, 1.5, -10), 33), ASPECT, base, require_file=False) is None
    # 机位范围(2026-09-14 二订):1.5 m 内、机高差 0.4 m 可派生;2.5 m 外不派生;主体只有 1 m 远时 1.5 m 的位移不派生;贴地机位不与人眼合
    assert sp.find_master(lib, 'LGT-1', facts((1.5, 1.1, 0), (1.5, 1.1, -10), 33), ASPECT, base, require_file=False) is master
    assert sp.find_master(lib, 'LGT-1', facts((2.5, 1.5, 0), (2.5, 1.5, -10), 33), ASPECT, base, require_file=False) is None
    assert sp.find_master(lib, 'LGT-1', facts((1.5, 1.5, 0), (1.5, 1.5, -1), 33), ASPECT, base, require_file=False) is None
    assert sp.find_master(lib, 'LGT-1', facts((0, .3, 0), (0, .3, -10), 33), ASPECT, base, require_file=False) is None
    assert sp.find_master(lib, 'LGT-1', facts((0, 2.2, 0), (0, 2.2, -10), 33), ASPECT, base, require_file=False) is None
    # 反打 → 不派生
    assert sp.find_master(lib, 'LGT-1', facts((0, 1.5, 0), (0, 1.5, 10), 33), ASPECT, base, require_file=False) is None


def test_plan_master_averages_fitting_peers_and_drops_outliers():
    def job(deg, fov=33.0):
        return {'facts': facts((0, 1.5, 0), turned((0, 1.5, 0), deg), fov)}
    j = job(0)
    # 同伴偏 ±15°:母图朝向取均值(≈0°),三镜都装得下
    m = sp.plan_master(j, [job(15), job(-15)], ASPECT)
    assert m['fov'] == sp.MASTER_FOV_V_DEG
    mf = facts(m['position'], m['target'], m['fov'])
    assert sp.angle_diff(mf['bearing_deg'], 0) < 1
    for p in (j, job(15), job(-15)):
        assert sp.view_fits(sp.cam_of_facts(mf), sp.cam_of_facts(p['facts']), ASPECT)
    # 同伴 +20° 与 +90°:90° 装不下被剔除,母图朝向落在 0°~20° 之间
    m = sp.plan_master(j, [job(20), job(90)], ASPECT)
    mf = facts(m['position'], m['target'], m['fov'])
    assert 0 < mf['bearing_deg'] < 20
    assert sp.view_fits(sp.cam_of_facts(mf), sp.cam_of_facts(j['facts']), ASPECT)
    assert not sp.view_fits(sp.cam_of_facts(mf), sp.cam_of_facts(job(90)['facts']), ASPECT)
    # 本镜比母图默认视场还宽 → 母图视场 = 本镜 + 余量
    wide = job(0, 60.0)
    assert sp.plan_master(wide, [], ASPECT)['fov'] == 60.0 + sp.MASTER_FOV_MARGIN_DEG
    # 同向、机位相距 1 m 的三镜(用户裁定应合一张):母图位置取质心
    def at(x, z):
        return {'facts': facts((x, 1.2, z), turned((x, 1.2, z), 0), 33)}
    m = sp.plan_master(at(-1.9, .85), [at(-1.35, 1.3), at(-1.8, 1.75)], ASPECT)
    assert abs(m['position'][0] - (-1.9-1.35-1.8)/3) < 1e-6 and abs(m['position'][2] - (.85+1.3+1.75)/3) < 1e-6
    # 2.5 m 外的同向镜不并入质心
    m = sp.plan_master(at(0, 0), [at(2.5, 0)], ASPECT)
    assert m['position'][0] == 0


def test_master_size_respects_pixel_cap():
    w, h = sp.master_size({'width': 1920, 'height': 1080})
    assert w*h <= sp.MASTER_MAX_PIXELS <= 4_624_220 and w % 2 == 0 and h % 2 == 0 and abs(w/h - 16/9) < .01
    w, h = sp.master_size({'width': 1080, 'height': 1920})
    assert w*h <= sp.MASTER_MAX_PIXELS and h > w


def test_lens_word_and_strip_dof():
    assert sp.lens_word(85) == 'wide-angle view'
    assert sp.lens_word(56) == 'normal-lens view'
    assert sp.lens_word(24).startswith('long-lens')
    s = 'photoreal 35mm film; shallow depth of field rendering backgrounds into soft bokeh; moderate film grain'
    assert sp.strip_dof(s) == 'photoreal 35mm film; moderate film grain'


def test_plate_style_and_negative_drop_person_clauses():
    s = ('cinematic 3D CG, hair solved strand by strand, coarse hemp and leather against silk, surfaces translucent; '
         'strong backlight rimming the subject, shadows never crushed to black; fine film grain.')
    assert sp.plate_style(s) == 'cinematic 3D CG, surfaces translucent; shadow areas always keep visible detail and colour; fine film grain.'
    n = ('gothic architecture, waxy skin, white beard, elderly face, hunched old man, doll button eyes, modern clothing, '
         'no scale reference, empty establishing shot without foreground frame, watermark')
    assert sp.plate_negative(n) == 'gothic architecture, watermark'


def test_apply_prompt_replaces_scene_maps_and_renumbers():
    prompt = {
        'refs': ['assets/concepts/characters/CHAR-0001/sheet.png',
                 'assets/concepts/scenes/S/layout_top.png',
                 'assets/concepts/scenes/S/grid_9views.png',
                 'assets/concepts/props/P/scale_ref_01.png'],
        'video_prompt': ('Overall visual style: x. 王三合@Image 1: a man. Spatial layout: [Image 2] is the clean top-down layout map '
                         '— do not render the map itself. [Image 3] is the 3x3 multi-angle sheet — do not copy its tiling. '
                         'Map usage: [Image 2] is a spatial position reference only, never the picture — every shot is filmed from '
                         'the ground-level camera described in that shot. Shot 1: he walks, framed like tile 7 of [Image 3]. '
                         'prop@Image 4 on the table. Global constraints: no text.'),
    }
    plan = {'plates': [{'shot_id': 'sh003', 'shot_no': 1, 'role': 'start', 'key': 'k1', 'file': 'assets/concepts/scenes/S/plates/k1.png', 'stale': False},
                       {'shot_id': 'sh004', 'shot_no': 2, 'role': 'start', 'key': 'k2', 'file': 'assets/concepts/scenes/S/plates/k2.png', 'stale': False},
                       {'shot_id': 'sh004', 'shot_no': 2, 'role': 'end', 'key': 'k3', 'file': 'assets/concepts/scenes/S/plates/k3.png', 'stale': False}],
            'missing': [], 'group': {}}
    out, warns = sp.apply_prompt(prompt, plan)
    assert out['refs'] == ['assets/concepts/characters/CHAR-0001/sheet.png',
                           'assets/concepts/scenes/S/plates/k1.png', 'assets/concepts/scenes/S/plates/k2.png',
                           'assets/concepts/scenes/S/plates/k3.png', 'assets/concepts/props/P/scale_ref_01.png']
    vp = out['video_prompt']
    assert 'Spatial layout:' not in vp and 'Map usage:' not in vp and 'framed like tile' not in vp
    assert 'prop@Image 5' in vp and '王三合@Image 1' in vp
    assert vp.index('Shot plates:') < vp.index('Shot 1:')
    assert '[Image 2] is the empty background plate of Shot 1' in vp
    assert '[Image 4] is the end plate of Shot 2' in vp
    assert not warns
    # 幂等
    again, _ = sp.apply_prompt(out, plan)
    assert again['refs'] == out['refs'] and again['video_prompt'] == out['video_prompt']
    errs, w = sp.check_prompt(out, plan, 'grpX')
    assert not errs


def test_apply_prompt_strips_dangling_map_sentences():
    """2026-09-14 liaozhai3 ep01:图号已被剔、「tiling, panel divisions …」逗号收尾的残留声明句也要删干净,且机检能测出。"""
    vp = ('Identity lock: exactly 1 character. Spatial layout: is the top-down layout map of this location, a clean unannotated '
          'scene plan; do not render the map, its layout, labels or grid lines in the picture. is the 3x3 multi-angle sheet of the '
          'same location (tiles numbered 1-9, left-to-right, top-to-bottom) — reference only: use it for spatial layout; do not copy '
          'its tiling, panel divisions or numbers into the frame. Every character position is written from the camera — follow the '
          'shot text as the picture, and use the map only to keep those positions consistent. Map usage: [Image 9] is a spatial '
          'position reference only, never the picture; use the specified shot camera and do not render the map. A = 甲. Shot 1: x.')
    plan = {'plates': [], 'missing': [], 'group': {}}
    _, warns = sp.check_prompt({'refs': [], 'video_prompt': vp}, plan, 'grpX')
    assert any('Spatial layout' in w for w in warns)
    out, _ = sp.apply_prompt({'refs': [], 'video_prompt': vp}, plan)
    assert out['video_prompt'] == ('Identity lock: exactly 1 character. Every character position is written from the camera — '
                                   'follow the shot text as the picture. A = 甲.\n\nShot 1: x.')
    _, warns = sp.check_prompt(out, plan, 'grpX')
    assert not any('Spatial layout' in w for w in warns)


def test_check_prompt_flags_scene_maps_and_missing_plate():
    prompt = {'refs': ['assets/concepts/scenes/S/layout_top.png'], 'video_prompt': 'Shot 1: x'}
    plan = {'plates': [{'shot_id': 'sh003', 'shot_no': 1, 'role': 'start', 'key': 'k1', 'file': 'assets/concepts/scenes/S/plates/k1.png', 'stale': True}],
            'missing': ['sh004'], 'group': {}}
    errs, warns = sp.check_prompt(prompt, plan, 'grpX')
    assert any('俯视图/九宫格' in e for e in errs)
    assert any('未挂 start 背景图' in e for e in errs)
    assert any('sh004' in w for w in warns)


def test_apply_prompt_v25_scene_slots_and_activation_idempotent():
    prompt = {
        'refs': ['assets/concepts/characters/CHAR-0001/sheet.png', 'assets/concepts/scenes/S/layout_top.png'],
        'video_prompt': ('Overall visual style: x. 王三合@Image 1: a man. Shot 1｜双人中景。使用：王三合。不采用：无。他走。 '
                         'Shot 2: 近景。使用：王三合。不采用：无。他停。 Global constraints: no text.'),
    }
    plan = {'plates': [{'shot_id': 'sh003', 'shot_no': 1, 'role': 'start', 'key': 'k1', 'file': 'assets/concepts/scenes/S/plates/k1.png', 'stale': False},
                       {'shot_id': 'sh004', 'shot_no': 2, 'role': 'start', 'key': 'k2', 'file': 'assets/concepts/scenes/S/plates/k2.png', 'stale': False}],
            'missing': [], 'group': {}}
    out, warns = sp.apply_prompt(prompt, plan, v25=True, zh=True)
    vp = out['video_prompt']
    assert out['refs'] == ['assets/concepts/characters/CHAR-0001/sheet.png', 'assets/concepts/scenes/S/plates/k1.png', 'assets/concepts/scenes/S/plates/k2.png']
    assert '【场景】场景A（Shot 1 的机位，空场景 background plate）参考 [Image 2]' in vp
    assert '场景B（Shot 2 的机位，空场景 background plate）参考 [Image 3]' in vp
    assert 'Shot 1｜双人中景。场景激活：使用场景A（[Image 2]）；不采用场景B（[Image 3]）。使用：王三合。' in vp
    assert 'Shot 2:场景激活：使用场景B（[Image 3]）；不采用场景A（[Image 2]）。' in vp.replace('Shot 2: ', 'Shot 2:')
    assert 'Global constraints: no text.' in vp          # Shot 1｜ 段头不再被 Shot plates 段吞掉(2026-09-09 事故)
    assert not sp.check_prompt(out, plan, 'g', v25=True)[0]
    again, _ = sp.apply_prompt(out, plan, v25=True, zh=True)
    assert again['video_prompt'] == vp and again['refs'] == out['refs']
    # 2.0 口径的段对 2.5 组判违规,反之亦然
    assert sp.check_prompt(out, plan, 'g', v25=False)[0]


def test_apply_prompt_h3_picture_anchors_idempotent():
    prompt = {
        'refs': ['assets/concepts/characters/CHAR-0001/sheet.png', 'assets/concepts/scenes/S/grid_9views.png'],
        'video_prompt': ('Overall visual style: x. subject_definitions: <Subject 1> is the man in [Image 1]. summary: s. retention_analysis: r. '
                         'detailed_description: Shot 1: he walks. Shot 2: he stops. overall_soundscape: wind. non_diegetic_music: N/A. Global constraints: no text.'),
    }
    plan = {'plates': [{'shot_id': 'sh003', 'shot_no': 1, 'role': 'start', 'key': 'k1', 'file': 'assets/concepts/scenes/S/plates/k1.png', 'stale': False},
                       {'shot_id': 'sh004', 'shot_no': 2, 'role': 'start', 'key': 'k2', 'file': 'assets/concepts/scenes/S/plates/k2.png', 'stale': False},
                       {'shot_id': 'sh004', 'shot_no': 2, 'role': 'end', 'key': 'k3', 'file': 'assets/concepts/scenes/S/plates/k3.png', 'stale': False}],
            'missing': [], 'group': {}}
    out, warns = sp.apply_prompt(prompt, plan, h3=True)
    vp = out['video_prompt']
    assert out['refs'][1:] == ['assets/concepts/scenes/S/plates/k1.png', 'assets/concepts/scenes/S/plates/k2.png', 'assets/concepts/scenes/S/plates/k3.png']
    assert '<Picture 2> ([Image 2]) is the empty background plate and composition anchor of [Shot 1]' in vp
    assert '<Picture 4> ([Image 4]) is the empty background plate and end-of-move composition anchor of [Shot 2]' in vp
    assert "Shot 1: Plate anchor: this shot's set corresponds to <Picture 2> ([Image 2]) — the shot frames almost exactly this view. <Picture 3> ([Image 3]), <Picture 4> ([Image 4]) not used in this shot." in vp
    assert "Shot 2: Plate anchor: this shot's set corresponds to <Picture 3> ([Image 3]) and <Picture 4> ([Image 4]) at the end of the move — the shot frames almost exactly this view. <Picture 2> ([Image 2]) not used in this shot." in vp
    assert 'overall_soundscape: wind.' in vp and 'Global constraints: no text.' in vp
    assert not sp.check_prompt(out, plan, 'g', h3=True)[0]
    again, _ = sp.apply_prompt(out, plan, h3=True)
    assert again['video_prompt'] == vp


# ---------------------------------------------------------------- 背景图模式(2026-09-22):全景图 | 世界模型
def test_plate_mode_project_default_and_scene_override(tmp_path):
    import json
    base = tmp_path
    assert sp.project_plate_mode(base) == 'grid'                       # 没有 settings.json → 默认九宫格(2026-09-26)
    (base / 'settings.json').write_text(json.dumps({'output': {'plate_mode': 'world'}}), encoding='utf-8')
    assert sp.project_plate_mode(base) == 'world'
    assert sp.scene_plate_mode(base, 'SCN-0001') == 'inherit'          # 没有库文件 → 跟随全局
    assert sp.effective_plate_mode(base, 'SCN-0001') == 'world'
    sp.set_scene_plate_mode(base, 'SCN-0001', 'pano')                  # 场景级覆盖存库 index.json#mode
    assert sp.scene_plate_mode(base, 'SCN-0001') == 'pano'
    assert sp.effective_plate_mode(base, 'SCN-0001') == 'pano'
    lib = sp.load_library(base, 'SCN-0001')
    assert lib['mode'] == 'pano' and lib['plates'] == []
    sp.set_scene_plate_mode(base, 'SCN-0001', 'inherit')               # 回到跟随全局:键删除
    assert 'mode' not in sp.load_library(base, 'SCN-0001')
    assert sp.effective_plate_mode(base, 'SCN-0001') == 'world'
    (base / 'settings.json').write_text(json.dumps({'output': {'plate_mode': 'bogus'}}), encoding='utf-8')
    assert sp.project_plate_mode(base) == 'grid'                       # 非法值回落默认
    import pytest
    with pytest.raises(ValueError):
        sp.set_scene_plate_mode(base, 'SCN-0001', 'bogus')


def _facts_min():
    return {'fov_h_deg': 85.0, 'lens_mm_equiv': 23, 'height_m': 1.6, 'height_word': 'eye level', 'tilt_word': 'level',
            'standing': 'on the floor', 'facing': 'north', 'facing_desc': '', 'facing_cardinal': 'north', 'frame_left': 'the west wall',
            'frame_right': 'the east wall', 'behind': 'the door', 'behind_desc': '', 'horizon_pct_from_top': 50}


def test_build_prompt_ref_kind_wording():
    scene = {'scene_id': 'SCN-0001', 'name': 'Hall'}
    pano = sp.build_prompt(_facts_min(), [], {}, {'time_of_day': 'day'}, scene, {}, '', '', '', 'start')
    world = sp.build_prompt(_facts_min(), [], {}, {'time_of_day': 'day'}, scene, {}, '', '', '', 'start', ref_kind='world')
    assert 're-projected to this exact camera from the scene\'s 360 panorama' in pano and '3D world model' not in pano
    assert '3D world model' in world and 're-projected' not in world
    # 两种口径都保留「视场以 [Image 1] 为准」与全幅深焦要求
    for text in (pano, world):
        assert 'The field of view is exactly what [Image 1] covers' in text and 'deep focus' in text


def test_world_missing_lists_scenes_and_world_missing_check(tmp_path):
    err = sp.WorldMissing(['SCN-0002', 'SCN-0005'])
    assert err.scenes == ['SCN-0002', 'SCN-0005'] and 'SCN-0005' in str(err) and '世界模型' in str(err)
    from modules import worldlabs
    assert worldlabs.world_missing(tmp_path, 'SCN-0002')                # 没有 world.json
    wdir = tmp_path / 'assets/concepts/scenes/SCN-0002/world'
    wdir.mkdir(parents=True)
    import json
    (wdir / 'world.json').write_text(json.dumps({'world_id': 'w1', 'files': {'splats': {'500k': 'splats_500k.spz'}}}), encoding='utf-8')
    assert worldlabs.world_missing(tmp_path, 'SCN-0002')                # 有记录但 splats 文件不在
    (wdir / 'splats_500k.spz').write_bytes(b'x')
    assert not worldlabs.world_missing(tmp_path, 'SCN-0002')
    assert worldlabs._pick_splat(tmp_path, 'SCN-0002', json.loads((wdir / 'world.json').read_text()))[0] == '500k'


def test_multiple_worlds_per_scene(tmp_path):
    """一个场景多个世界模型:根下旧版算 W1 原地读取,新的进 world/worlds/<key>/;默认 = index.json#default,没记取第一个。"""
    import json
    from modules import worldlabs as wl
    sid = 'SCN-0003'
    assert wl.list_worlds(tmp_path, sid) == [] and wl.default_key(tmp_path, sid) is None and wl.read_world(tmp_path, sid) is None
    assert wl.world_dir(tmp_path, sid) == tmp_path / 'assets/concepts/scenes' / sid / 'world'
    # 没有旧版时第一个新世界模型就是 W1
    key, out = wl.new_world_dir(tmp_path, sid)
    assert key == 'W1' and out == tmp_path / 'assets/concepts/scenes' / sid / 'world/worlds/W1'
    assert wl.list_worlds(tmp_path, sid) == []                       # 没有 world.json = 还没生成完,不列
    (out / 'world.json').write_text(json.dumps({'world_id': 'a', 'key': 'W1', 'files': {'splats': {'500k': 's.spz'}},
                                                'input': {'source': 'scene_pano', 'anchor_id': 'A1', 'scheme': 'day'}}), encoding='utf-8')
    (out / 's.spz').write_bytes(b'x'); (out / 'thumbnail.jpg').write_bytes(b'x')
    key2, out2 = wl.new_world_dir(tmp_path, sid)
    assert key2 == 'W2'
    assert wl.new_world_dir(tmp_path, sid)[0] == 'W3'                # 半途留下的空目录不复用
    (out2 / 'world.json').write_text(json.dumps({'world_id': 'b', 'key': 'W2', 'files': {'splats': {'500k': 's.spz'}}, 'alignment': {'scale_fix': 1.2},
                                                 'input': {'source': 'scene_pano', 'anchor_id': 'A3', 'scheme': 'dusk'}}), encoding='utf-8')
    (out2 / 's.spz').write_bytes(b'x')
    assert [w['key'] for w in wl.list_worlds(tmp_path, sid)] == ['W1', 'W2']
    assert wl.default_key(tmp_path, sid) == 'W1' and wl.read_world(tmp_path, sid)['world_id'] == 'a' and not wl.world_missing(tmp_path, sid)
    assert wl.set_default(tmp_path, sid, 'W2') == 'W2'
    assert wl.read_world(tmp_path, sid)['world_id'] == 'b' and wl.world_dir(tmp_path, sid) == out2 and wl.world_dir(tmp_path, sid, 'W1') == out
    assert wl._pick_splat(tmp_path, sid, wl.read_world(tmp_path, sid))[1] == 's.spz'
    summary = wl.worlds_summary(tmp_path, sid, '/projects/p')
    assert [(w['key'], w['default'], w['input']['anchor_id']) for w in summary] == [('W1', False, 'A1'), ('W2', True, 'A3')]
    assert summary[0]['previews']['thumbnail'].startswith(f'/projects/p/assets/concepts/scenes/{sid}/world/worlds/W1/thumbnail.jpg?v=')
    assert summary[1]['dir'] == f'assets/concepts/scenes/{sid}/world/worlds/W2' and summary[1]['base_url'].endswith('/world/worlds/W2/')
    assert wl.preview_summary(tmp_path, sid, '/projects/p')['key'] == 'W2' and wl.preview_summary(tmp_path, sid, '/projects/p', 'W1')['key'] == 'W1'
    # 对齐微调写回该世界模型的 world.json
    al = wl.update_alignment(tmp_path, sid, 'W2', yaw_fix_deg=7, scale_fix=1.05)
    assert (al['yaw_fix_deg'], al['scale_fix']) == (7.0, 1.05) and json.loads((out2 / 'world.json').read_text())['alignment']['yaw_fix_deg'] == 7.0
    import pytest
    with pytest.raises(wl.WorldLabsError):
        wl.set_default(tmp_path, sid, 'W9')
    with pytest.raises(wl.WorldLabsError):
        wl.update_alignment(tmp_path, sid, 'W2', scale_fix=0)
    assert wl.pending_world_dir(tmp_path, sid, 'W3').name == 'W3'


def test_legacy_root_world_reads_as_w1(tmp_path):
    """旧版放在 world/ 根下的世界模型原地当 W1 读取;再生成的排到 W2,默认仍是旧的那个。"""
    import json
    from modules import worldlabs as wl
    sid = 'SCN-0004'
    root = tmp_path / 'assets/concepts/scenes' / sid / 'world'
    root.mkdir(parents=True)
    (root / 'world.json').write_text(json.dumps({'world_id': 'old', 'files': {'splats': {'500k': 's.spz'}}}), encoding='utf-8')
    (root / 's.spz').write_bytes(b'x')
    assert [(w['key'], w['dir']) for w in wl.list_worlds(tmp_path, sid)] == [('W1', root)]
    assert wl.read_world(tmp_path, sid)['key'] == 'W1' and wl.worlds_summary(tmp_path, sid, '/projects/p')[0]['base_url'].endswith(f'/{sid}/world/')
    key, out = wl.new_world_dir(tmp_path, sid)
    assert key == 'W2' and out == root / 'worlds/W2'
    (out / 'world.json').write_text(json.dumps({'world_id': 'new', 'files': {}}), encoding='utf-8')
    assert [w['key'] for w in wl.list_worlds(tmp_path, sid)] == ['W1', 'W2'] and wl.default_key(tmp_path, sid) == 'W1'


def _v25_fixture():
    prompt = {
        'refs': ['assets/concepts/characters/CHAR-0001/sheet.png', 'assets/concepts/scenes/S/layout_top.png'],
        'video_prompt': ('Overall visual style: x. Wang@Image 1: a man. Shot 1｜Two-shot. Use: Wang. Not used: None. He walks. '
                         'Shot 2: Close-up. Use: Wang. Not used: None. He stops. Global constraints: no text.'),
    }
    plan = {'plates': [{'shot_id': 'sh003', 'shot_no': 1, 'role': 'start', 'key': 'k1', 'file': 'assets/concepts/scenes/S/plates/k1.png', 'stale': False,
                        'in_frame': ['Wang', 'the well'], 'out_of_frame': ['the gate'], 'backdrop': 'white wall. plaster',
                        'layers': {'fg': 'well rim', 'bg': 'wall'}},
                       {'shot_id': 'sh004', 'shot_no': 2, 'role': 'start', 'key': 'k2', 'file': 'assets/concepts/scenes/S/plates/k2.png', 'stale': False}],
            'missing': [], 'group': {}, 'openings_en': 'Doorways read as dark voids at night'}
    return prompt, plan


def test_apply_prompt_v25_english_when_ui_not_chinese():
    """2026-09-23 用户裁决:宿主注入的 2.5 机器句随界面语言,非中文界面出英文;机检两套都认;幂等;中英互切能干净重写。"""
    prompt, plan = _v25_fixture()
    out, warns = sp.apply_prompt(prompt, plan, v25=True, zh=False)
    vp = out['video_prompt']
    assert '【Scene】Scene A (camera position of Shot 1; empty background plate) reference [Image 2]' in vp
    assert 'Scene B (camera position of Shot 2; empty background plate) reference [Image 3]' in vp
    assert 'Doorways read as dark voids at night. ' in vp and vp.count('described in each Shot.') == 1
    assert 'Shot 1｜Two-shot. Scene activation: use Scene A ([Image 2]); do not use Scene B ([Image 3]). In frame left to right: Wang, the well. ' \
           'Not in frame (never paint them into this shot): the gate. Backdrop behind the subject: white wall; plaster. ' \
           'Composition layers: foreground well rim; background wall. Use: Wang.' in vp
    assert 'Scene activation: use Scene B ([Image 3]); do not use Scene A ([Image 2]).' in vp
    assert '场景激活' not in vp and '【场景】' not in vp
    assert not sp.check_prompt(out, plan, 'g', v25=True)[0]
    again, _ = sp.apply_prompt(out, plan, v25=True, zh=False)
    assert again['video_prompt'] == vp
    # 切回中文界面:英文机器句整段剔除、改写为中文,不残留
    back, _ = sp.apply_prompt(out, plan, v25=True, zh=True)
    bvp = back['video_prompt']
    assert 'Scene activation' not in bvp and 'In frame left to right' not in bvp and 'Composition layers' not in bvp and '【Scene】' not in bvp
    assert '场景激活：使用场景A（[Image 2]）；不采用场景B（[Image 3]）。本镜画内自左向右：Wang、the well。' in bvp
    assert not sp.check_prompt(back, plan, 'g', v25=True)[0]
    # 再切回英文也干净
    fwd, _ = sp.apply_prompt(back, plan, v25=True, zh=False)
    assert fwd['video_prompt'] == vp


def test_ui_lang_is_zh_env_and_state(tmp_path, monkeypatch):
    monkeypatch.setenv('VIDEOAGENTS_UI_LANG', 'en'); assert sp.ui_lang_is_zh() is False
    monkeypatch.setenv('VIDEOAGENTS_UI_LANG', 'zh'); assert sp.ui_lang_is_zh() is True
    monkeypatch.delenv('VIDEOAGENTS_UI_LANG')
    monkeypatch.setenv('VIDEOAGENTS_RUNTIME_DIR', str(tmp_path))
    assert sp.ui_lang_is_zh() is True                       # 无 state.json → 缺省中文
    (tmp_path / 'state.json').write_text('{"ui_lang": "ja"}', encoding='utf-8')
    assert sp.ui_lang_is_zh() is False
    (tmp_path / 'state.json').write_text('{}', encoding='utf-8')
    (tmp_path / 'genconfig.json').write_text('{"ui_language": "en"}', encoding='utf-8')
    assert sp.ui_lang_is_zh() is False


# ---------------------------------------------------------------- 九宫格模式(2026-09-25,用户方案:俯视图出宫格 → 拆九张 → 按白模机位选格)
def test_grid_modes_and_layout():
    assert sp.PLATE_MODES == ('pano', 'world', 'grid', 'grid_manual') and sp.SCENE_PLATE_MODES == ('inherit',) + sp.PLATE_MODES
    assert sp.GRID_PLATE_MODES == ('grid', 'grid_manual') and sp.DEFAULT_PLATE_MODE == 'grid'
    geom = sp.grid_geometry(9, FMT)
    assert geom['cols'] == geom['rows'] == 3 and geom['slots'] == 9
    assert geom['width'] * geom['height'] <= sp.GRID_MAX_PIXELS
    assert abs(geom['tile_w'] / geom['tile_h'] - 16 / 9) < 0.01 and geom['tile_w'] % 2 == 0 and geom['tile_h'] % 2 == 0
    assert geom['width'] == 3 * geom['tile_w'] + 4 * geom['gutter']
    x0, y0, _, _ = sp.grid_tile_box(geom, 4)
    assert (x0, y0) == (geom['gutter'] * 2 + geom['tile_w'], geom['gutter'] * 2 + geom['tile_h'])
    assert sp.grid_tile_word(geom, 0) == 'top-left' and sp.grid_tile_word(geom, 4) == 'centre' and sp.grid_tile_word(geom, 8) == 'bottom-right'


def test_grid_geometry_follows_selected_model_max_pixels():
    from modules import genmedia
    pro = {'provider': 'volcengine', 'model': 'doubao-seedream-5-0-pro-260628'}
    lite = {'provider': 'byteplus', 'model': 'seedream-5-0-lite-260128'}
    assert genmedia.image_max_pixels(pro) == 4_624_220 and genmedia.image_max_pixels(lite) == 4096 * 4096
    assert genmedia.image_max_pixels({'provider': 'volcengine', 'model': 'doubao-seedream-4-5-251128'}) == 4096 * 4096
    assert genmedia.image_max_pixels({'provider': 'fal', 'model': 'fal-ai/bytedance/seedream/v5/lite'}) == 4096 * 4096
    for unknown in ({'provider': 'volcengine', 'model': 'my-custom-endpoint'}, {'provider': 'openrouter', 'model': 'bytedance-seed/seedream-4.5'},
                    {'provider': 'fal', 'model': 'fal-ai/flux-2-pro'}, {'provider': 'comfyui'}):
        assert genmedia.image_max_pixels(unknown) is None and sp.grid_max_pixels(unknown) == sp.GRID_MAX_PIXELS
    assert sp.grid_max_pixels(None) == sp.GRID_MAX_PIXELS and sp.grid_max_pixels(lite) == 4096 * 4096
    base, big = sp.grid_geometry(9, FMT), sp.grid_geometry(9, FMT, sp.grid_max_pixels(lite))
    assert base['gutter'] == sp.GRID_GUTTER_PX and sp.grid_geometry(9, FMT, sp.grid_max_pixels(pro))['gutter'] == sp.GRID_GUTTER_PX
    assert sp.GRID_MAX_PIXELS < big['width'] * big['height'] <= 4096 * 4096 and big['tile_w'] > 1.8 * base['tile_w']
    assert big['gutter'] % 2 == 0 and abs(big['gutter'] / big['width'] - base['gutter'] / base['width']) < 0.0005   # 白线占比不变
    assert big['width'] == 3 * big['tile_w'] + 4 * big['gutter'] and big['height'] == 3 * big['tile_h'] + 4 * big['gutter']
    assert sp.grid_tile_size(base, FMT) == sp.grid_tile_size(big, FMT) == sp.plate_size(FMT)       # 16:9 下单格原生仍小于 1920
    wide = {'width': 2390, 'height': 1000}
    tw, th = sp.grid_tile_size(sp.grid_geometry(9, wide, 4096 * 4096), wide)                       # 原生比分镜图规格大:按原生存,不往下缩
    assert tw > 1920 and tw % 2 == 0 and th % 2 == 0 and abs(tw / th - 2.39) < 0.01


def test_ensure_grid9_requests_model_max_and_splits_by_new_geometry(tmp_path, monkeypatch):
    import json
    from PIL import Image
    from modules import genmedia
    base = tmp_path; sid = 'SCN-hall'
    sdir = base / 'assets/concepts/scenes' / sid; sdir.mkdir(parents=True)
    Image.new('RGB', (480, 200), (40, 40, 40)).save(sdir / 'layout_top.png')
    calls = []

    def fake_generate(prompt, output, negative='', refs=None, aspect='', size='', seed=None):
        w, h = (int(x) for x in size.split('x'))
        tmpl = Image.open(refs[1]); assert tmpl.size == (w, h)                       # 版式模板与请求尺寸同一份 geom
        geom = sp.grid_geometry(9, FMT, w * h + 1)
        sheet = Image.new('RGB', (w, h), (255, 255, 255))
        for i in range(9):
            sheet.paste((20 * i + 10, 0, 0), sp.grid_tile_box(geom, i))
        sheet.save(output, format='JPEG', quality=95); calls.append((w, h, geom))
        return output
    monkeypatch.setattr(genmedia, 'generate_image', fake_generate)
    monkeypatch.setattr(genmedia, 'get_config', lambda kind: {'provider': 'volcengine', 'model': 'doubao-seedream-5-0-lite-260128'})
    tiles = sp.ensure_grid9(base, sid, 'L1', 'L1', scene=_hall_scene(), layout={'scene_id': sid}, axes=AXES, fmt=FMT, style_doc={}, time_of_day='day',
                            lighting='', seed=3, log=lambda *_: None, cameras=_hall_cams())
    (w, h, geom), = calls
    assert sp.GRID_MAX_PIXELS < w * h <= 4096 * 4096
    want = sp.grid_geometry(9, FMT, 4096 * 4096)
    assert (w, h) == (want['width'], want['height']) and geom['tile_w'] == want['tile_w'] and geom['gutter'] == want['gutter']
    meta = json.loads((sdir / 'plates/L1_grid9.json').read_text())
    assert meta['max_pixels'] == 4096 * 4096 and meta['geometry'] == want and meta['channel']['model'] == 'doubao-seedream-5-0-lite-260128'
    for i, t in enumerate(tiles):
        x0, y0, x1, y1 = t['pano_ref']['box']; bx0, by0, bx1, by1 = sp.grid_tile_box(want, i)
        assert bx0 < x0 < x1 < bx1 and by0 < y0 < y1 < by1                           # 拆格框落在新版式的格子内(内缩去白线)
        assert t['pano_ref']['sheet_size'] == f'{w}x{h}' and t['pano_ref']['tile_native'] == f"{want['tile_w']}x{want['tile_h']}"
        im = Image.open(base / t['file']); assert im.size == sp.plate_size(FMT) and t['size'] == '1920x1080'
        for xy in ((2, 2), (im.width - 3, im.height - 3), (im.width // 2, im.height // 2)):
            r, g, b = im.getpixel(xy); assert abs(r - (20 * i + 10)) <= 6 and g <= 6 and b <= 6   # 没切到白线、没串格


def test_grid_compose_split_roundtrip(tmp_path):
    from PIL import Image
    geom = sp.grid_geometry(9, FMT)
    tmpl = sp.compose_grid_sheet([], geom, tmp_path / 'tmpl.jpg')       # 纯版式模板:白线 + 浅灰格
    im = Image.open(tmpl); assert im.size == (geom['width'], geom['height'])
    assert im.getpixel((2, 2)) == (255, 255, 255)
    x0, y0, _, _ = sp.grid_tile_box(geom, 8); assert im.getpixel((x0 + 5, y0 + 5)) == (235, 235, 235)
    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (0, 255, 255), (255, 0, 255), (20, 20, 20), (120, 120, 120), (200, 90, 30)]
    frames = []
    for i, c in enumerate(colors):
        f = tmp_path / f'f{i}.jpg'; Image.new('RGB', (320, 180), c).save(f); frames.append(f)
    sheet = sp.compose_grid_sheet(frames, geom, tmp_path / 'sheet.jpg')
    outs = [tmp_path / f't{i}.png' for i in range(9)]
    res = sp.split_grid_sheet(sheet, geom, outs, (640, 360))
    assert [r['tile'] for r in res] == list(range(9))
    for o, c in zip(outs, colors):
        t = Image.open(o); assert t.size == (640, 360)
        assert all(abs(a - b) <= 8 for a, b in zip(t.getpixel((320, 180)), c))
        assert all(abs(a - b) <= 8 for a, b in zip(t.getpixel((1, 1)), c))   # 内缩后边缘不带白线
    small = tmp_path / 'small.jpg'; Image.open(sheet).resize((geom['width'] // 2, geom['height'] // 2)).save(small)
    assert sp.split_grid_sheet(small, geom, outs[:1], (320, 180))[0]['native'][0] < geom['tile_w']
    bad = tmp_path / 'bad.jpg'; Image.new('RGB', (geom['width'], geom['width']), (0, 0, 0)).save(bad)
    try:
        sp.split_grid_sheet(bad, geom, outs[:1], (320, 180)); assert False
    except ValueError:
        pass


def _layout9():
    lms = [{'id': 'door', 'name_en': 'main door', 'xy': [0.5, 0.95]}, {'id': 'hearth', 'name_en': 'fireplace', 'xy': [0.5, 0.05]},
           {'id': 'table', 'name_en': 'long table', 'xy': [0.5, 0.5]}, {'id': 'win', 'name_en': 'window', 'xy': [0.05, 0.5]}]
    pairs = [('door', 'hearth', 'wide', 'eye'), ('hearth', 'door', 'wide', 'eye'), ('win', 'table', 'medium', 'eye'), ('door', 'table', 'wide', 'high_oblique'),
             ('table', 'hearth', 'close', 'low'), ('table', 'win', 'medium', 'eye'), ('hearth', 'win', 'wide', 'eye'), ('win', 'door', 'close', 'low'), ('door', 'win', 'detail', 'low')]
    views = [{'tile': i + 1, 'camera_from': a, 'looking_at': b, 'size': sz, 'angle': an, 'desc_en': f'view {i + 1}'} for i, (a, b, sz, an) in enumerate(pairs)]
    return {'scene_id': 'SCN-0001', 'scene_name_en': 'Hall', 'landmarks': lms, 'views': views, 'orientation': {'top_of_map': 'north wall'}}


def test_grid9_views_positions_and_errors():
    layout = _layout9(); scene = {'scene_id': 'SCN-0001', 'dimensions_m': [10, 4, 20], 'objects': []}
    vs = sp.grid9_views(layout, scene)
    assert [v['tile'] for v in vs] == list(range(1, 10))
    v1 = vs[0]                                                          # door(0.5,0.95) → hearth(0.5,0.05):机位在南端、看向北
    assert abs(v1['position'][0]) < 1e-9 and abs(v1['position'][2] - 9.0) < 1e-9 and v1['position'][1] == sp.GRID9_HEIGHT['eye']
    assert v1['fov'] == sp.GRID9_FOV['wide'] and v1['from_name'] == 'main door' and v1['desc'] == 'view 1'
    v4 = vs[3]                                                          # high_oblique:按俯角 30° 抬高
    assert v4['position'][1] > sp.GRID9_HEIGHT['high_oblique'] - 1e-9 and v4['angle'] == 'high_oblique'
    assert vs[4]['position'][1] == sp.GRID9_HEIGHT['low'] and vs[4]['fov'] == sp.GRID9_FOV['close']
    bad = dict(layout); bad['views'] = layout['views'][:8]
    try:
        sp.grid9_views(bad, scene); assert False
    except sp.Grid9LayoutError:
        pass
    bad2 = dict(layout); bad2['views'] = [dict(v) for v in layout['views']]; bad2['views'][2]['looking_at'] = 'nope'
    try:
        sp.grid9_views(bad2, scene); assert False
    except sp.Grid9LayoutError:
        pass


def test_pick_grid9_tile_prefers_bearing_then_distance_height():
    ex, ez, texts = AXES
    def tile(i, pos, tgt, fov=55.0):
        f = sp.camera_facts({'position': list(pos), 'target': list(tgt), 'fov': fov}, FMT, ex, ez, texts)
        return {'key': f't{i}', 'grid9': True, 'camera': f, 'pano_ref': {'kind': 'grid9', 'tile': i - 1}}
    tiles = [tile(1, (0, 1.6, 9), (0, 1.4, -9)),      # 南端看北
             tile(2, (0, 1.6, -9), (0, 1.4, 9)),      # 北端看南
             tile(3, (0, 0.4, 9), (0, 0.6, -9)),      # 南端看北,贴地
             tile(4, (-4, 1.6, 0), (0, 1.4, 0), 40)]  # 西侧看东,中景
    shot = facts((0.5, 1.5, 8), (0.5, 1.2, -5), 30.0)                    # 南端看北、人眼
    e, info = sp.pick_grid9_tile(tiles, shot)
    assert e['key'] == 't1' and info['tile'] == 1 and info['bearing_delta_deg'] < 5 and info['height_class_delta'] == 0
    shot_low = facts((0.5, 0.3, 8), (0.5, 0.2, -5), 30.0)                # 同向但贴地 → 第 3 格
    assert sp.pick_grid9_tile(tiles, shot_low)[0]['key'] == 't3'
    shot_e = facts((-3, 1.5, 0.5), (5, 1.3, 0.5), 20.0)                  # 看东 → 第 4 格,且格视场 40 ≥ 20 不罚
    e4, info4 = sp.pick_grid9_tile(tiles, shot_e)
    assert e4['key'] == 't4' and not info4['narrower_than_shot']
    shot_wide = facts((-3, 1.5, 0.5), (5, 1.3, 0.5), 60.0)               # 本镜比格宽 → 记 narrower 但仍选它(其它格朝向差 90°)
    assert sp.pick_grid9_tile(tiles, shot_wide)[1]['narrower_than_shot'] is True
    assert sp.pick_grid9_tile([], shot) == (None, {})


def test_build_grid9_prompt_wording():
    layout = _layout9(); scene = {'scene_id': 'SCN-0001', 'dimensions_m': [10, 4, 20], 'objects': []}
    vs = sp.grid9_views(layout, scene); geom = sp.grid_geometry(9, FMT)
    ex, ez, texts = AXES
    fb = {v['tile']: sp.camera_facts({'position': v['position'], 'target': v['target'], 'fov': v['fov']}, FMT, ex, ez, texts) for v in vs}
    p = sp.build_grid9_prompt(layout, scene, vs, geom, fb, 'cinematic; shallow depth of field', 'low sun', 'stone walls', 'dusk', {'north': 'north wall'})
    assert p.startswith('One image that is a 3 by 3 grid of nine separate photographs')
    assert '[Image 1] is the top-down plan' in p and '[Image 2]' in p and 'No tile may be drawn as a top-down' in p
    assert 'main door (bottom centre of the plan)' in p and 'fireplace (top centre of the plan)' in p
    assert 'Tile 1 (top-left): wide establishing view from main door looking toward fireplace, eye-level camera, facing north. view 1.' in p
    assert 'Tile 5 (centre): close shot from long table looking toward fireplace, lens close to the ground, low angle' in p
    assert 'Tile 4 (middle-left)' in p and 'raised camera looking down' in p
    assert 'Style: cinematic' in p and 'depth of field' not in p.split('Style:')[-1]
    assert 'tiled grid' not in sp.NEGATIVE_GRID and 'plan view' in sp.NEGATIVE_GRID


def test_grid9_entries_not_legacy_and_sorted():
    lib = {'plates': [{'key': 'day_grid9_t3', 'grid9': True, 'pano_ref': {'kind': 'grid9', 'scheme': 'day', 'tile': 2}},
                      {'key': 'day_grid9_t1', 'grid9': True, 'pano_ref': {'kind': 'grid9', 'scheme': 'day', 'tile': 0}},
                      {'key': 'night_grid9_t1', 'grid9': True, 'pano_ref': {'kind': 'grid9', 'scheme': 'night', 'tile': 0}},
                      {'key': 'm', 'master': True, 'pano_ref': {'kind': 'pano'}}, {'key': 'old', 'file': 'x.png'}]}
    assert [e['key'] for e in sp.grid9_entries(lib, 'day')] == ['day_grid9_t1', 'day_grid9_t3']
    assert not sp.is_legacy(lib['plates'][0]) and not sp.is_legacy(lib['plates'][3]) and sp.is_legacy(lib['plates'][4])


# ---------------------------------------------------------------- 九宫格补图(2026-09-26)
def test_grid9_unfit_reasons_thresholds():
    assert sp.grid9_unfit_reasons({}) == []
    assert sp.grid9_unfit_reasons({'bearing_delta_deg': 12, 'pitch_delta_deg': 5, 'distance_m': 3, 'height_class_delta': 1}) == []
    r = sp.grid9_unfit_reasons({'bearing_delta_deg': 50.8, 'pitch_delta_deg': 86.4, 'distance_m': 0.1, 'height_class_delta': 1})
    assert len(r) == 2 and r[0].startswith('朝向差') and r[1].startswith('俯仰差')
    assert any(x.startswith('机高档差') for x in sp.grid9_unfit_reasons({'bearing_delta_deg': 0, 'pitch_delta_deg': 0, 'distance_m': 0, 'height_class_delta': 2}))
    assert any(x.startswith('机位距') for x in sp.grid9_unfit_reasons({'bearing_delta_deg': 0, 'pitch_delta_deg': 0, 'distance_m': 6.5, 'height_class_delta': 0}))


def test_find_grid9_fallback_reuses_near_camera_only(tmp_path):
    shot = facts((6.0, 0.2, -0.9), (5.5, 0.2, -3.0), 46.4)
    near = facts((6.6, 0.3, -1.0), (6.0, 0.3, -3.2), 46.4)      # 距 0.6 m、朝向差小、机高差 0.1
    far = facts((9.0, 0.2, -0.9), (8.5, 0.2, -3.0), 46.4)       # 距 3 m
    high = facts((6.0, 1.3, -0.9), (5.5, 1.3, -3.0), 46.4)      # 机高差 1.1 m
    def fb(key, f, scheme='S', pending=False):
        e = {'key': key, 'grid9_fallback': True, 'file': f'assets/concepts/scenes/X/plates/{key}.png', 'camera': f,
             'pano_ref': {'kind': 'grid9_fallback', 'scheme': scheme}}
        if pending:
            e['pending'] = True
        return e
    tile = {'key': 't1', 'grid9': True, 'camera': near, 'pano_ref': {'kind': 'grid9', 'scheme': 'S', 'tile': 0}}
    entries = [tile, fb('far', far), fb('high', high), fb('other', near, scheme='T'), fb('near', near, pending=True)]
    assert sp.find_grid9_fallback(entries, 'S', shot, tmp_path)['key'] == 'near'          # 九格/远/高/别的方案都不算
    assert sp.find_grid9_fallback(entries[:-1], 'S', shot, tmp_path) is None
    assert sp.find_grid9_fallback([fb('near2', near)], 'S', shot, tmp_path) is None       # 文件不存在且非 pending
    assert sp.find_grid9_fallback([fb('near2', near)], 'S', shot, tmp_path, require_file=False)['key'] == 'near2'
    assert sp.is_legacy(fb('near', near)) is False


def test_build_grid9_fallback_prompt_wording():
    ex, ez, texts = AXES
    shot = sp.camera_facts({'position': [6.0, 0.15, -0.3], 'target': [5.5, 2.5, -0.5], 'fov': 53.0}, FMT, ex, ez, texts)
    shot['standing'] = 'on the floor'
    tiles = [{'key': 't3', 'grid9': True, 'camera': facts((9.7, 0.4, -3.3), (5.7, 0.6, 0), 28.0),
              'pano_ref': {'kind': 'grid9', 'tile': 2, 'view': {'camera_from': 'curtain', 'looking_at': 'glass_table', 'size': 'close', 'angle': 'low'}}}]
    p = sp.build_grid9_fallback_prompt(shot, ['the glass table in the centre'], ['the curtain'], {'scene_id': 'SCN-x', 'name': 'Hall'},
                                       {'landmarks': [{'id': 'glass_table', 'name_en': 'the glass table', 'xy': [.5, .5]}]},
                                       'Style X', 'lamps', 'stone hall', 'start', 'noon', {'north': 'the west wall'}, tiles, tiles[0])
    assert p.startswith('Empty location background plate') and 'Location: Hall.' in p
    assert '[Image 1] is the top-down plan' in p and 'The top edge of the plan is the west wall' in p
    assert '[Image 2] is a 3 by 3 contact sheet' in p and 'tile 3 (top-right): close shot from curtain looking toward glass_table' in p
    assert 'The nearest viewpoint is tile 3' in p and 'do not copy any tile' in p and 'tilted up about' in p
    assert 'In frame from left to right: the glass table in the centre.' in p and 'Not visible in this frame' in p
    assert '[Image 3]' not in p and 'Style: Style X' in p
    p_end = sp.build_grid9_fallback_prompt(shot, [], [], {'scene_id': 'SCN-x'}, {}, '', '', '', 'end', '', {}, tiles, {})
    assert '[Image 3] is the finished background plate of the same shot at the start' in p_end and 'nearest viewpoint' not in p_end


def test_copy_count_parts_are_one_item():
    assert sp.copy_count(['glass_table_top', 'glass_table_leg_1', 'glass_table_leg_2', 'glass_table_leg_3']) == 1
    assert sp.copy_count(['glass_table', 'glass_table_top']) == 1
    assert sp.copy_count(['chair_1', 'chair_2']) == 2 and sp.copy_count(['bench_left', 'bench_right']) == 2


def test_split_grid_sheet_saves_jpeg_under_png_name(tmp_path):
    from PIL import Image
    geom = sp.grid_geometry(9, FMT, max_pixels=400_000)
    sheet = tmp_path / 'sheet.png'
    Image.new('RGB', (geom['width'], geom['height']), (120, 90, 60)).save(sheet)
    outs = [tmp_path / f't{i}.png' for i in range(9)]
    res = sp.split_grid_sheet(sheet, geom, outs, (192, 108))
    assert len(res) == 9 and all(o.is_file() for o in outs)
    assert outs[0].read_bytes()[:3] == b'\xff\xd8\xff' and Image.open(outs[0]).size == (192, 108)


def test_revision_key_chains_from_root():
    lib = {'plates': [{'key': 'X'}, {'key': 'X_rev1'}, {'key': 'X_rev3'}, {'key': 'Y_rev9'}]}
    assert sp.revision_key(lib, 'X') == 'X_rev4'
    assert sp.revision_key(lib, 'X_rev1') == 'X_rev4'       # 链式修改不嵌套 _rev1_rev1
    assert sp.revision_key(lib, 'Z') == 'Z_rev1'
    assert not sp.is_legacy({'key': 'X_rev1', 'revised': True, 'pano_ref': {'kind': 'revision'}})


def test_revise_shot_plate_keeps_source_and_swaps_only_this_role(tmp_path, monkeypatch):
    """以当前图为 [Image 1] 出一张 _rev1 入库;只改本镜该角色索引条目,原图/原条目/另一角色不动。"""
    import json
    from PIL import Image
    from modules import genmedia
    base = tmp_path
    sid, ep = 'SCN-1', 'ep01'
    (base/'settings.json').write_text(json.dumps({'output': {'spatial_blocking': True}}))
    pdir = base/'assets/concepts/scenes'/sid/'plates'; pdir.mkdir(parents=True)
    src_rel = f'assets/concepts/scenes/{sid}/plates/L1_grid9_t8.png'
    Image.new('RGB', (192, 108), 'gray').save(base/src_rel, format='JPEG')
    f = facts((0, 1.5, 0), (0, 1.5, -10), 27)
    src_entry = {'key': 'L1_grid9_t8', 'file': src_rel, 'grid9': True, 'master': False, 'camera': f, 'lighting_scheme_id': 'L1',
                 'pano_ref': {'kind': 'grid9', 'tile': 7, 'scheme': 'L1'}}
    sp.save_library(base, sid, {'schema_version': sp.SCHEMA_LIBRARY, 'scene_id': sid, 'plates': [src_entry]})
    idx = {'schema_version': sp.SCHEMA_EPISODE, 'ep': ep, 'shots': {'sh001': {'group_id': 'grp001', 'scene_id': sid, 'lighting_scheme_id': 'L1', 'plates': [
        {'role': 'start', 'key': 'L1_grid9_t8', 'file': src_rel, 'reuse': 'grid9', 'camera': f, 'whitebox_frame': None},
        {'role': 'end', 'key': 'L1_grid9_t8', 'file': src_rel, 'reuse': 'grid9', 'camera': f, 'whitebox_frame': None}]}}}
    sp.save_episode_index(base, ep, idx)
    calls = []

    def fake_generate(prompt, output, negative='', refs=None, aspect='', size='', seed=None):
        calls.append({'prompt': prompt, 'output': output, 'refs': refs, 'size': size})
        Image.new('RGB', (192, 108), 'blue').save(output, format='JPEG')
        return output
    monkeypatch.setattr(genmedia, 'generate_image', fake_generate)
    monkeypatch.setattr(genmedia, 'get_config', lambda kind: {'provider': 'test', 'model': 'm'})
    dry = sp.revise_shot_plate(base, ep, 'sh001', 'start', 'remove the lantern', dry_run=True, log=lambda *_: None)
    assert dry['dry_run'] and dry['key'] == 'L1_grid9_t8_rev1' and not calls
    assert sp.load_episode_index(base, ep)['shots']['sh001']['plates'][0]['key'] == 'L1_grid9_t8'   # dry-run 不改索引
    res = sp.revise_shot_plate(base, ep, 'sh001', 'start', 'remove the lantern', note='去掉灯笼', seed=7, log=lambda *_: None)
    assert len(calls) == 1 and calls[0]['refs'] == [str(base/src_rel)] and calls[0]['size'] == '192x108'
    assert 'Requested change: remove the lantern.' in calls[0]['prompt'] and '去掉灯笼' in calls[0]['prompt']
    assert res['key'] == 'L1_grid9_t8_rev1' and (base/res['file']).is_file() and (base/res['file']).with_suffix('.json').is_file()
    assert (base/src_rel).is_file()                                                   # 原图不动
    lib = sp.load_library(base, sid)['plates']
    assert [e['key'] for e in lib] == ['L1_grid9_t8', 'L1_grid9_t8_rev1'] and lib[0] == src_entry   # 原条目原样
    new = lib[1]
    assert new['revised'] and not new['master'] and new['pano_ref']['kind'] == 'revision' and new['pano_ref']['source_key'] == 'L1_grid9_t8'
    assert new['pano_ref']['source_kind'] == 'grid9' and new['seed'] == 7 and not sp.is_legacy(new)
    shots = sp.load_episode_index(base, ep)['shots']['sh001']['plates']
    assert shots[0]['key'] == 'L1_grid9_t8_rev1' and shots[0]['reuse'] == 'revised' and shots[0]['revised_from']['key'] == 'L1_grid9_t8'
    assert shots[0]['camera'] == f                                                    # 机位指纹不动(非 --force 重出保留)
    assert shots[1]['key'] == 'L1_grid9_t8' and shots[1]['reuse'] == 'grid9'          # 另一角色不动
    # 链式:再改一次 → _rev2,来源是 _rev1
    res2 = sp.revise_shot_plate(base, ep, 'sh001', 'start', 'make it dusk', seed=8, log=lambda *_: None)
    assert res2['key'] == 'L1_grid9_t8_rev2' and calls[-1]['refs'] == [str(base/res['file'])]
    assert sp.load_episode_index(base, ep)['shots']['sh001']['plates'][0]['revised_from']['key'] == 'L1_grid9_t8_rev1'


def test_copy_key_chains_from_root():
    lib = {'plates': [{'key': 'X'}, {'key': 'X_copy1'}, {'key': 'X_copy3'}, {'key': 'Y_copy9'}]}
    assert sp.copy_key(lib, 'X') == 'X_copy4'
    assert sp.copy_key(lib, 'X_copy1') == 'X_copy4'        # 副本的副本不嵌套 _copy1_copy1
    assert sp.copy_key(lib, 'Z') == 'Z_copy1'


def test_copy_and_flip_plate(tmp_path):
    """复制 = 库里紧跟原图新增副本(不带自动选图标记、原图原条目不动);翻转 = 水平镜像原地覆盖 + 首次留 .orig,翻两次回正。"""
    import json
    import pytest
    from PIL import Image
    base = tmp_path
    sid = 'SCN-1'
    pdir = base/'assets/concepts/scenes'/sid/'plates'; pdir.mkdir(parents=True)
    rel = f'assets/concepts/scenes/{sid}/plates/L1_grid9_fb1.png'
    im = Image.new('RGB', (192, 108), 'black')
    im.paste((255, 255, 255), (0, 0, 96, 108))             # 左半白、右半黑
    im.save(base/rel, format='PNG')
    f = facts((0, 1.5, 0), (0, 1.5, -10), 27)
    src = {'key': 'L1_grid9_fb1', 'file': rel, 'grid9_fallback': True, 'master': False, 'camera': f, 'lighting_scheme_id': 'L1', 'size': '192x108',
           'pano_ref': {'kind': 'grid9_fallback', 'scheme': 'L1'}}
    other = {'key': 'M', 'file': f'assets/concepts/scenes/{sid}/plates/M.png', 'master': True, 'camera': f, 'lighting_scheme_id': 'L1',
             'pano_ref': {'kind': 'pano', 'anchor_id': 'A1'}}
    sp.save_library(base, sid, {'schema_version': sp.SCHEMA_LIBRARY, 'scene_id': sid, 'plates': [src, other]})
    (base/rel).with_suffix('.json').write_text(json.dumps(src))

    new = sp.copy_plate(base, sid, 'L1_grid9_fb1')
    assert new['key'] == 'L1_grid9_fb1_copy1' and (base/new['file']).is_file() and (base/new['file']).with_suffix('.json').is_file()
    assert (base/new['file']).read_bytes() == (base/rel).read_bytes()
    lib = sp.load_library(base, sid)['plates']
    assert [e['key'] for e in lib] == ['L1_grid9_fb1', 'L1_grid9_fb1_copy1', 'M'] and lib[0] == src     # 紧跟原图;原条目原样
    assert new['copy'] and not new['master'] and 'grid9_fallback' not in new and new['camera'] == f
    assert new['pano_ref']['kind'] == 'copy' and new['pano_ref']['source_key'] == 'L1_grid9_fb1' and new['pano_ref']['source_kind'] == 'grid9_fallback'
    assert not sp.is_legacy(new) and not sp.is_grid9_fallback(new)
    assert sp.find_grid9_fallback(lib, 'L1', f, base)['key'] == 'L1_grid9_fb1'                        # 副本不参与补图复用
    assert sp.copy_plate(base, sid, 'L1_grid9_fb1_copy1')['key'] == 'L1_grid9_fb1_copy2'
    assert sp.copy_plate(base, sid, 'L1_grid9_fb1')['key'] == 'L1_grid9_fb1_copy3'
    assert [e['key'] for e in sp.load_library(base, sid)['plates']] == ['L1_grid9_fb1', 'L1_grid9_fb1_copy1', 'L1_grid9_fb1_copy2', 'L1_grid9_fb1_copy3', 'M']
    with pytest.raises(LookupError):
        sp.copy_plate(base, sid, 'nope')
    with pytest.raises(LookupError):
        sp.flip_plate(base, sid, 'M')                       # 条目在、文件缺

    def left_is_white(path):
        with Image.open(path) as i:
            return i.convert('RGB').getpixel((10, 50))[0] > 200
    e = sp.flip_plate(base, sid, 'L1_grid9_fb1_copy1')
    assert e['mirrored'] and not left_is_white(base/new['file']) and left_is_white(base/rel)           # 只翻副本,原图不动
    orig = base/e['original_file']
    assert orig.name == 'L1_grid9_fb1_copy1.orig.png' and left_is_white(orig)
    with Image.open(base/new['file']) as i:
        assert i.format == 'PNG' and i.size == (192, 108)
    assert json.loads((base/new['file']).with_suffix('.json').read_text())['mirrored'] is True
    assert next(x for x in sp.load_library(base, sid)['plates'] if x['key'] == e['key'])['mirrored'] is True
    e2 = sp.flip_plate(base, sid, 'L1_grid9_fb1_copy1')
    assert not e2['mirrored'] and left_is_white(base/new['file']) and left_is_white(orig)               # 翻回;备份仍是最初那张


def test_v25_activation_after_timing_tag_order_independent():
    """issue #89/#90:sync_shot_plates 与 sync_shot_timing 谁先跑结果一致,时间段标签始终紧跟段头。"""
    from modules import shot_timing as st
    plan = {'plates': [{'shot_id': 'sh003', 'shot_no': 1, 'role': 'start', 'key': 'k1', 'file': 'assets/concepts/scenes/S/plates/k1.png', 'stale': False},
                       {'shot_id': 'sh004', 'shot_no': 2, 'role': 'start', 'key': 'k2', 'file': 'assets/concepts/scenes/S/plates/k2.png', 'stale': False}],
            'missing': [], 'group': {}}
    for zh in (True, False):
        base = {'refs': ['assets/concepts/characters/CHAR-0001/sheet.png'],
                'video_prompt': 'Overall visual style: x. Shot 1: 近景。他走。 Shot 2: 近景。他停。 Global constraints: no text.'}
        t_first = dict(base, video_prompt=st.apply_prompt(base['video_prompt'], [2, 3], 5, st.KIND_SD25, zh))
        a, _ = sp.apply_prompt(t_first, plan, v25=True, zh=zh)                       # timing → plates
        p_first, _ = sp.apply_prompt(dict(base), plan, v25=True, zh=zh)
        b = st.apply_prompt(p_first['video_prompt'], [2, 3], 5, st.KIND_SD25, zh)   # plates → timing
        assert a['video_prompt'] == b, (zh, a['video_prompt'], b)
        assert re.search(r'Shot 1: 0-2(秒：|s: )(场景激活|Scene activation)', b), b
        assert st.check_prompt(b, [2, 3], 5, st.KIND_SD25, 'g') == ([], [])
        # 标签被挤到段中间 → WARN
        bad = b.replace('Shot 1: 0-2秒：', 'Shot 1: 他走。 0-2秒：') if zh else b.replace('Shot 1: 0-2s: ', 'Shot 1: he walks. 0-2s: ')
        assert any('未紧跟段头' in w for w in st.check_prompt(bad, [2, 3], 5, st.KIND_SD25, 'g')[1])


# ---------------------------------------------------------------- 中心点九宫格 + 九宫格手动补图(2026-10-04)
def _hall_scene():
    """20 x 6 m 的长厅(x 向长),四面墙 + 桌子 + 吊灯 + 矮帘。"""
    objs = [{'id': 'wall_n', 'position': [0, 1.5, -3.25], 'size_m': [20, 3, 0.5]}, {'id': 'wall_s', 'position': [0, 1.5, 3.25], 'size_m': [20, 3, 0.5]},
            {'id': 'wall_w', 'position': [-10.25, 1.5, 0], 'size_m': [0.5, 3, 7]}, {'id': 'wall_e', 'position': [10.25, 1.5, 0], 'size_m': [0.5, 3, 7]},
            {'id': 'table_top', 'position': [4, 0.7, 0], 'size_m': [1, 0.05, 1]}, {'id': 'table_leg_1', 'position': [4, 0.35, 0], 'size_m': [0.05, 0.7, 0.05]},
            {'id': 'lamp_01', 'position': [6, 2.6, 0], 'size_m': [0.4, 0.4, 0.4]}, {'id': 'lamp_02', 'position': [8, 2.6, 0], 'size_m': [0.4, 0.4, 0.4]},
            {'id': 'curtain', 'position': [8, 0.3, -2.9], 'size_m': [1.8, 0.6, 0.05]}]
    return {'scene_id': 'SCN-hall', 'dimensions_m': [24, 3, 10], 'objects': objs}


def _hall_cams():
    def f(pos, tgt):
        return sp.camera_facts(cam(pos, tgt, 40.0), FMT, *AXES)
    return [f((4, 0.3, 1), (9, 0.3, 1)), f((6, 0.3, 0), (6, 2.3, -3)), f((6, 0.5, 1), (6, 2.5, -2)), f((7, 1.4, -1), (2, 1.4, -1)), f((6, 1.3, 0), (0, 1.3, 0))]


def test_plan_edges_reads_keys_prose_and_defaults():
    assert sp.plan_edges({}) == {'north': 'top', 'east': 'right', 'south': 'bottom', 'west': 'left'}
    assert sp.plan_edges({'orientation': {'top_of_map': 'east side', 'right_of_map': 'south'}})['north'] == 'left'
    # alices SCN-long-hall:只写了上边 + 文字里说北端在左(镜像布局)→ 其余按对边补
    e = sp.plan_edges({'orientation': {'top_of_map': "west wall (the low curtain); the hall's north end is at the left edge, the south end at the right edge"}})
    assert e == {'west': 'top', 'north': 'left', 'south': 'right', 'east': 'bottom'}
    v = sp.grid9c_vectors(e)
    assert v['north'] == (-1.0, 0.0) and v['west'] == (0.0, -1.0) and abs(v['north-east'][0] + v['north-east'][1]) < 1e-9


def test_grid9c_station_tilt_and_views():
    scene = _hall_scene(); cams = _hall_cams()
    layout = {'scene_id': 'SCN-hall', 'landmarks': [{'id': 'door', 'name_en': 'east door', 'xy': [0.9, 0.5]}]}
    plan = sp.grid9c_views(scene, layout, FMT, AXES, cams)
    pos = plan['station']
    assert pos[1] == sp.GRID9C_HEIGHT_RANGE[0]                           # 机高中位数 0.5 → 夹到下限 0.9
    assert abs(pos[0] - 6) <= 0.5 and abs(pos[2]) <= 1.0                 # 机位水平中位点附近的空网格点
    assert not any(sp._in_box(pos[0], pos[2], o, sp.GRID9C_CLEARANCE_M) for o in sp.eye_band_solids(scene, pos[1]))
    assert plan['tilt']['card'] == 'north' and plan['tilt']['pitch'] > 20      # 两条仰拍镜都朝北(-z)
    vs = plan['views']
    assert [v['tile'] for v in vs] == list(range(1, 10)) and [v['card'] for v in vs[:8]] == list(sp.GRID9C_DIRS)
    assert all(v['pitch'] == 0 and abs(v['facts']['pitch_deg']) < 0.1 for v in vs[:8]) and vs[8]['facts']['pitch_deg'] > 20
    assert [round(v['facts']['bearing_deg']) for v in vs[:8]] == [0, 45, 90, 135, 180, 225, 270, 315]
    north, east = vs[0], vs[2]
    assert abs(north['ahead_m'] - (3.0 + pos[2])) < 0.01 and abs(east['ahead_m'] - (10.0 - pos[0])) < 0.01    # 正前方到墙面的距离
    assert (north['left_card'], north['right_card']) == ('west', 'east')
    assert any('east door' in s for s in east['seen'])
    joined = ' '.join(east['objects'])
    assert 'hanging from the ceiling' in joined and 'lamp' in joined          # 吊灯写明悬挂
    assert any('curtain' in o and 'only 0.6 m tall' in o for v in vs for o in v['objects'])   # 矮帘写明高度
    assert not any('wall' in o for v in vs for o in v['objects'])             # 结构件不列
    no_tilt = sp.grid9c_tilt([c for c in cams if c['pitch_deg'] <= 20], sp.grid9c_vectors(plan['edges']))
    assert no_tilt['pitch'] == 0.0
    down = sp.camera_facts(cam((6, 2.5, 0), (8, 0.2, 0), 40.0), FMT, *AXES)                 # 没有仰拍镜时看俯拍镜
    assert sp.grid9c_tilt([c for c in cams if c['pitch_deg'] <= 20] + [down], sp.grid9c_vectors(plan['edges']))['pitch'] < -20


def test_ensure_grid9_center_dry_run_and_fallback_wording(tmp_path):
    from PIL import Image
    base = tmp_path; sid = 'SCN-hall'
    sdir = base / 'assets/concepts/scenes' / sid; sdir.mkdir(parents=True)
    Image.new('RGB', (480, 200), (40, 40, 40)).save(sdir / 'layout_top.png')
    scene = _hall_scene(); layout = {'scene_id': sid}
    logs = []
    tiles = sp.ensure_grid9(base, sid, 'L1', 'L1', scene=scene, layout=layout, axes=AXES, fmt=FMT, style_doc={}, time_of_day='day', lighting='',
                            dry_run=True, log=logs.append, cameras=_hall_cams())
    assert len(tiles) == 9 and all(t['pending'] and t['grid9'] and t['pano_ref']['layout'] == 'center' for t in tiles)
    assert tiles[0]['refs'][0].endswith('L1_grid9.plan.jpg') and tiles[0]['pano_ref']['view']['card'] == 'north'
    assert tiles[8]['pano_ref']['view']['pitch_deg'] > 20 and tiles[0]['pano_ref']['station'] == tiles[8]['pano_ref']['station']
    prompt = tiles[0]['prompt']
    assert 'one single camera standpoint' in prompt and 'Tile 9 (bottom-right): looking north' in prompt and 'tilted up' in prompt
    assert 'eight red arrows' in prompt and 'north is toward the top edge' in prompt
    assert not (sdir / 'plates').exists() or not list((sdir / 'plates').glob('*.png'))      # dry-run 不落图
    try:                                                                                     # 中心点需要机位
        sp.ensure_grid9(base, sid, 'L1', 'L1', scene=scene, layout=layout, axes=AXES, fmt=FMT, style_doc={}, time_of_day='', lighting='', dry_run=True)
        assert False
    except sp.Grid9LayoutError:
        pass
    try:                                                                                     # views 方式仍要 layout.json#views
        sp.ensure_grid9(base, sid, 'L1', 'L1', scene=scene, layout=layout, axes=AXES, fmt=FMT, style_doc={}, time_of_day='', lighting='', dry_run=True,
                        layout_kind='views')
        assert False
    except sp.Grid9LayoutError:
        pass
    shot = sp.camera_facts(cam((6, 0.2, 0), (9, 0.2, 0), 40.0), FMT, *AXES); shot['standing'] = 'on the floor'
    fb = sp.build_grid9_fallback_prompt(shot, [], [], scene, layout, '', '', '', 'start', 'day', {}, tiles, tiles[2])
    assert 'one common standpoint' in fb and 'tile 9 (bottom-right): facing north, tilted up' in fb and 'nine other camera positions' not in fb


def test_find_manual_capture_fit_scheme_and_copies(tmp_path):
    base = tmp_path
    shot = sp.camera_facts(cam((0, 1.5, 0), (0, 1.5, -5), 40.0), FMT, *AXES)
    def entry(key, pos, tgt, **kw):
        rel = f'assets/concepts/scenes/X/plates/{key}.png'
        (base / rel).parent.mkdir(parents=True, exist_ok=True); (base / rel).write_bytes(b'x')
        return {'key': key, 'manual': True, 'master': True, 'file': rel, 'camera': sp.camera_facts(cam(pos, tgt, 60.0), FMT, *AXES),
                'lighting_scheme_id': 'L1', 'pano_ref': {'kind': 'pano_manual', 'scheme': 'L1'}, **kw}
    near = entry('near', (1, 1.6, 0), (1, 1.6, -5)); nearer = entry('nearer', (0.2, 1.5, 0), (0.2, 1.5, -5))
    far = entry('far', (9, 1.6, 0), (9, 1.6, -5)); side = entry('side', (0, 1.5, 0), (5, 1.5, 0))
    other = entry('other', (0, 1.5, 0), (0, 1.5, -5)); other['pano_ref']['scheme'] = 'L2'; other['lighting_scheme_id'] = 'L2'
    nolight = entry('nolight', (0.5, 1.5, 0), (0.5, 1.5, -5)); nolight['pano_ref'] = {'kind': 'world_manual'}; nolight['lighting_scheme_id'] = 'nolight'
    cp = entry('cp', (0, 1.5, 0), (0, 1.5, -5), copy=True)
    tile = {'key': 't1', 'grid9': True, 'file': near['file'], 'camera': shot, 'pano_ref': {'kind': 'grid9', 'scheme': 'L1', 'tile': 0}}
    e, info = sp.find_manual_capture([far, side, other, cp, tile, near, nearer], 'L1', shot, base)
    assert e['key'] == 'nearer' and info['distance_m'] == 0.2 and not sp.grid9_unfit_reasons(info)     # 远 / 侧向 / 别的方案 / 副本 / 九格都不算
    assert sp.find_manual_capture([far, side, other, cp, tile], 'L1', shot, base) == (None, {})
    assert sp.find_manual_capture([nolight], 'L1', shot, base)[0]['key'] == 'nolight'                  # 不分方案的手工截图通用
    (base / near['file']).unlink()
    assert sp.find_manual_capture([near], 'L1', shot, base) == (None, {})                              # 文件不在
    assert sp.is_manual_capture(near) and not sp.is_manual_capture(cp) and not sp.is_manual_capture(tile)
    assert sp.pick_grid9_tile([tile], shot)[1] == {'tile': 1, 'key': 't1', **sp.plate_camera_fit(shot, shot)}


def _manual_project(tmp_path, monkeypatch, mode):
    """最小项目:一个场景两镜(一镜正对北、一镜贴地朝东),库里九格已齐(中心点),背景图模式 = mode。"""
    import json
    base = tmp_path; ep = 'ep01'; sid = 'SCN-hall'
    (base / 'settings.json').write_text(json.dumps({'output': {'plate_mode': mode, 'spatial_blocking': True}}), encoding='utf-8')
    scene = _hall_scene()
    def camrec(shot_id, pos, tgt):
        return {'shot_id': shot_id, 'movement': 'static', 'start': 0.0, 'duration_s': 2.0, 'keyframes': [cam(pos, tgt, 40.0)]}
    episode = {'scenes': {sid: scene}, 'groups': [{'group_id': 'grp001', 'scene_id': sid,
               'cameras': [camrec('sh001', (6, 1.0, 0), (6, 1.0, -3)), camrec('sh002', (-8, 0.2, 2), (-3, 0.2, 2))]}]}
    (base / 'directing' / ep / 'whitebox').mkdir(parents=True)
    (base / 'directing' / ep / 'whitebox' / 'episode.json').write_text(json.dumps(episode), encoding='utf-8')
    (base / 'directing' / ep / 'shot_list.json').write_text(json.dumps({'shots': [], 'generation_groups': [{'group_id': 'grp001', 'lighting_scheme_id': 'L1'}]}), encoding='utf-8')
    pdir = base / 'assets/concepts/scenes' / sid / 'plates'; pdir.mkdir(parents=True)
    plan = sp.grid9c_views(scene, {}, FMT, AXES, [sp.camera_facts(cam((6, 1.0, 0), (6, 1.0, -3), 40.0), FMT, *AXES)])
    tiles = []
    for v in plan['views']:
        rel = f'assets/concepts/scenes/{sid}/plates/L1_grid9_t{v["tile"]}.png'; (base / rel).write_bytes(b'x')
        tiles.append({'key': f'L1_grid9_t{v["tile"]}', 'grid9': True, 'master': False, 'file': rel, 'camera': v['facts'], 'lighting_scheme_id': 'L1',
                      'pano_ref': {'kind': 'grid9', 'layout': 'center', 'scheme': 'L1', 'tile': v['tile'] - 1, 'sheet': f'assets/concepts/scenes/{sid}/plates/L1_grid9.png'}})
    lib = sp.load_library(base, sid); lib['plates'] = tiles
    sp.save_library(base, sid, lib)
    monkeypatch.setattr(sp, 'render_clean_frames', lambda *a, **k: None)
    return base, ep, sid


def test_grid_manual_mode_marks_needed_then_picks_manual_capture(tmp_path, monkeypatch):
    base, ep, sid = _manual_project(tmp_path, monkeypatch, 'grid_manual')
    called = []
    monkeypatch.setattr('modules.genmedia.generate_image', lambda *a, **k: called.append(a), raising=False)
    stats = sp.run_episode(base, ep, log=lambda *_: None)
    assert stats['modes'][sid] == 'grid_manual' and stats['manual_needed'] == ['sh002:start'] and not stats['errors'] and not called
    shots = sp.load_episode_index(base, ep)['shots']
    assert shots['sh001']['plates'][0]['reuse'] == 'grid9' and 'manual_needed' not in (shots['sh001']['plates'][0]['view'] or {})
    p2 = shots['sh002']['plates'][0]
    assert p2['reuse'] == 'grid9' and p2['view']['manual_needed']['reasons'] and p2['key'].startswith('L1_grid9_t')      # 最近格占位
    st = sp.status_episode(base, ep)
    assert st['problems'] == {'sh002': 'manual_needed'} and st['shots']['sh001']['state'] == 'ok'
    # 用户手工截了一张贴合 sh002 机位的图 → 重跑自动选用,不再待补
    rel = f'assets/concepts/scenes/{sid}/plates/L1_hand1.png'; (base / rel).write_bytes(b'x')
    lib = sp.load_library(base, sid)
    lib['plates'].append({'key': 'L1_hand1', 'manual': True, 'master': True, 'file': rel, 'lighting_scheme_id': 'L1',
                          'camera': sp.camera_facts(cam((-8, 0.3, 2), (-3, 0.3, 2), 60.0), FMT, *AXES), 'pano_ref': {'kind': 'world_manual', 'scheme': 'L1'}})
    sp.save_library(base, sid, lib)
    stats2 = sp.run_episode(base, ep, log=lambda *_: None)
    assert not stats2.get('manual_needed') and stats2.get('grid9_manual_library') == 1 and not called
    p2 = sp.load_episode_index(base, ep)['shots']['sh002']['plates'][0]
    assert p2['key'] == 'L1_hand1' and p2['reuse'] == 'grid9_manual' and p2['view']['manual']['kind'] == 'world_manual'
    assert sp.status_episode(base, ep)['problems'] == {}
    assert sp.run_episode(base, ep, log=lambda *_: None)['skipped_fresh'] == 2            # 再跑:两镜都按记录保留


def test_status_manual_needed_only_in_manual_mode(tmp_path, monkeypatch):
    import json
    base, ep, sid = _manual_project(tmp_path, monkeypatch, 'grid_manual')
    sp.run_episode(base, ep, log=lambda *_: None)
    assert sp.status_episode(base, ep)['problems'] == {'sh002': 'manual_needed'}
    (base / 'settings.json').write_text(json.dumps({'output': {'plate_mode': 'grid', 'spatial_blocking': True}}), encoding='utf-8')
    assert sp.status_episode(base, ep)['problems'] == {}            # 切回自动补图:占位标记不再算问题(重跑会自动补图)
    sp.set_scene_plate_mode(base, sid, 'grid_manual')               # 场景级覆盖为手动补图
    assert sp.status_episode(base, ep)['problems'] == {'sh002': 'manual_needed'}



def test_composition_layer_naming_hidden_cast_is_dropped_with_warning(tmp_path, monkeypatch):
    """#96:composition 的层次描述点名了白模判为本组不出现的人物 → 该层不进「构图层次」机器句,sync 报 WARN 指回上游。"""
    import json
    import modules.whitebox_refs as wr
    base = tmp_path
    (base/'directing/ep01/shots/sh001').mkdir(parents=True)
    (base/'directing/ep01/shot_list.json').write_text(json.dumps(
        {'generation_groups': [{'group_id': 'grp001', 'shots': ['sh001']}]}, ensure_ascii=False))
    (base/'directing/ep01/shots/sh001/composition.json').write_text(json.dumps(
        {'layers': {'fg': '案上的香炉', 'mg': '供桌,其上李靖的脸', 'bg': '西壁神龛'}}, ensure_ascii=False))
    (base/'plate.png').write_bytes(b'x')
    idx = {'shots': {'sh001': {'plates': [{'role': 'start', 'key': 'k1', 'file': 'plate.png'}]}}}
    wb_group = {'group_id': 'grp001', 'scene_id': 'SCN-0001', 'cameras': [],
                'actors': [{'id': 'CHAR-0001', 'label': '哪吒'}, {'id': 'CHAR-0002', 'label': '李靖'}]}
    episode = {'groups': [wb_group], 'scenes': {}}
    monkeypatch.setattr(wr, 'cast_filter', lambda *a: {'visible': ['CHAR-0001'], 'hidden': {'CHAR-0002': '整组不在画幅内'}})
    plan = sp.plan_group_refs(base, 'ep01', 'grp001', idx, episode)
    assert plan['plates'][0]['layers'] == {'fg': '案上的香炉', 'bg': '西壁神龛'}
    assert plan['layer_drops'] == [('sh001', 'mg', '李靖')]
    monkeypatch.setattr(wr, 'cast_filter', lambda *a: None)                     # 白模未开:原样写
    plan = sp.plan_group_refs(base, 'ep01', 'grp001', idx, episode)
    assert set(plan['plates'][0]['layers']) == {'fg', 'mg', 'bg'} and plan['layer_drops'] == []


def test_view_extras_skip_geometry_without_landmark():
    """#99:没登记成布局地标的白模辅助几何(wall_ne / cushion)不进视频提示词的画内/背景清单;出图用的 inventory 不受影响。"""
    scene = {'dimensions_m': [10, 4, 10], 'objects': [
        {'id': 'wall_ne', 'position': [2, 1.5, -4], 'size_m': [3, 3, .2]},
        {'id': 'cushion_1', 'position': [-1, .2, -2], 'size_m': [.6, .2, .6]},
        {'id': 'altar', 'position': [0, .6, -3], 'size_m': [1.6, 1.2, .8]}]}
    layout = {'landmarks': [{'id': 'altar', 'name': '香案', 'xy': [.5, .2], 'kind': 'prop'}]}
    key = {'position': [0, 1.5, 4], 'target': [0, 1.0, -3], 'fov': 50.0}
    assert {i['name'] for i in sp.inventory(scene, layout, key, FMT)[0]} == {'cushion', '香案', 'wall ne'}
    extras = sp.shot_view_extras(scene, layout, key, FMT)
    assert extras['in_frame'] == ['香案'] and extras['centre'] == ['香案']
    assert 'wall' not in extras['backdrop'] and 'cushion' not in extras['backdrop']


def test_swap_shot_plate_points_slot_at_library_entry(tmp_path, monkeypatch):
    """#98:换图只改本镜该角色的集索引条目(reuse=manual、记换前 key),库与其它镜不动;找不到/同图按 PlateSwapError 报。"""
    import json
    base = tmp_path
    sid, ep = 'SCN-1', 'ep01'
    pdir = base/'assets/concepts/scenes'/sid/'plates'; pdir.mkdir(parents=True)
    for k in ('L1_a', 'L1_b'):
        (pdir/f'{k}.png').write_bytes(b'x')
    f = facts((0, 1.5, 0), (0, 1.5, -10), 27)
    lib = {'schema_version': sp.SCHEMA_LIBRARY, 'scene_id': sid, 'plates': [
        {'key': 'L1_a', 'file': f'assets/concepts/scenes/{sid}/plates/L1_a.png', 'master': True, 'camera': f, 'lighting_scheme_id': 'L1'},
        {'key': 'L1_b', 'file': f'assets/concepts/scenes/{sid}/plates/L1_b.png', 'master': True, 'camera': f, 'lighting_scheme_id': 'L1'}]}
    sp.save_library(base, sid, lib)
    idx = {'schema_version': sp.SCHEMA_EPISODE, 'ep': ep, 'shots': {
        'sh001': {'group_id': 'grp001', 'scene_id': sid, 'lighting_scheme_id': 'L1', 'plates': [
            {'role': 'start', 'key': 'L1_a', 'file': lib['plates'][0]['file'], 'reuse': 'library', 'camera': f},
            {'role': 'end', 'key': 'L1_a', 'file': lib['plates'][0]['file'], 'reuse': 'library', 'camera': f}]},
        'sh002': {'group_id': 'grp001', 'scene_id': sid, 'lighting_scheme_id': 'L1', 'plates': [
            {'role': 'start', 'key': 'L1_b', 'file': lib['plates'][1]['file'], 'reuse': 'new', 'camera': f}]}}}
    sp.save_episode_index(base, ep, idx)
    synced = []
    monkeypatch.setattr(sp, 'sync_group', lambda b, e, g, write=False, **kw: synced.append((g, write)) or {'group_id': g, 'errors': [], 'warnings': [], 'updated': True})
    res = sp.swap_shot_plate(base, ep, 'sh001', 'start', 'L1_b')
    assert res['key'] == 'L1_b' and res['previous'] == {'key': 'L1_a', 'file': lib['plates'][0]['file'], 'reuse': 'library'}
    assert synced == [('grp001', True)]
    after = sp.load_episode_index(base, ep)['shots']
    start, end = after['sh001']['plates']
    assert start['key'] == 'L1_b' and start['reuse'] == 'manual' and start['swapped_from']['key'] == 'L1_a' and start['camera'] == f
    assert end['key'] == 'L1_a' and after['sh002']['plates'][0]['key'] == 'L1_b'          # 另一角色 / 另一镜不动
    assert [e['key'] for e in sp.load_library(base, sid)['plates']] == ['L1_a', 'L1_b']   # 库不动
    import pytest
    with pytest.raises(sp.PlateSwapError) as ex:
        sp.swap_shot_plate(base, ep, 'sh001', 'start', 'L1_b')
    assert ex.value.status == 400
    with pytest.raises(sp.PlateSwapError) as ex:
        sp.swap_shot_plate(base, ep, 'sh001', 'start', 'nope')
    assert ex.value.status == 404
    with pytest.raises(sp.PlateSwapError) as ex:
        sp.swap_shot_plate(base, ep, 'sh002', 'end', 'L1_a')
    assert ex.value.status == 404


def test_plate_adopt_fields_drive_adoption_sentence_and_survive_rewrite():
    """#113:单图 adopt / exclude_note 决定采用句(2.5 中英、H3、2.0);缺省文案不变;--write 幂等;字段未写进段时机检 WARN。"""
    base_plates = [{'shot_id': 'sh003', 'shot_no': 1, 'role': 'start', 'key': 'k1', 'file': 'assets/concepts/scenes/S/plates/k1.png', 'stale': False},
                   {'shot_id': 'sh004', 'shot_no': 2, 'role': 'start', 'key': 'k2', 'file': 'assets/concepts/scenes/S/plates/k2.png', 'stale': False,
                    'adopt': ['layout', 'architecture', 'materials'], 'exclude_note': '不采用画面右侧的海面与眩光'}]
    plan = {'plates': base_plates, 'missing': [], 'group': {}}
    prompt = {'refs': ['assets/concepts/characters/CHAR-0001/sheet.png'],
              'video_prompt': 'Overall visual style: x. 王三合@Image 1: a man. Shot 1: 他走。 Shot 2: 他停。 Global constraints: no text.'}
    out, _ = sp.apply_prompt(prompt, plan, v25=True, zh=True)
    vp = out['video_prompt']
    assert '参考 [Image 2]，' in vp and '只采用空间布局、建筑、材质和光线，不采用图中任何人物' in vp                       # 缺省图文案不变
    assert '参考 [Image 3]，' in vp and '只采用空间布局、建筑和材质，不采用图中的光线，不采用图中任何人物' in vp
    assert '另：不采用画面右侧的海面与眩光。' in vp
    assert not sp.check_prompt(out, plan, 'g', v25=True)[0] and not [w for w in sp.check_prompt(out, plan, 'g', v25=True)[1] if '采用口径' in w]
    again, _ = sp.apply_prompt(out, plan, v25=True, zh=True)
    assert again['video_prompt'] == vp
    en, _ = sp.apply_prompt(prompt, plan, v25=True, zh=False)
    assert 'use only its spatial layout, architecture and materials; do not take its lighting from the plate; do not use any person' in en['video_prompt']
    assert 'Also: 不采用画面右侧的海面与眩光. ' in en['video_prompt']
    h3, _ = sp.apply_prompt({'refs': [], 'video_prompt': 'detailed_description: Shot 1: a. Shot 2: b. Global constraints: x.'}, plan, h3=True)
    assert 'that are not in the plate, do not take its lighting from the plate, 不采用画面右侧的海面与眩光' in h3['video_prompt']
    v20, _ = sp.apply_prompt(prompt, plan)
    assert 'then add the characters, do not take its lighting from the plate, 不采用画面右侧的海面与眩光' in v20['video_prompt']
    # 字段改了但段还是旧文案 → WARN 提示 --write
    stale_plan = {'plates': [dict(base_plates[0]), dict(base_plates[1], exclude_note='也不采用左侧的旗杆')], 'missing': [], 'group': {}}
    assert any('采用口径' in w for w in sp.check_prompt(out, stale_plan, 'g', v25=True)[1])
    assert sp.normalize_adopt('光线, layout') == ['layout', 'lighting'] and sp.normalize_adopt('layout,architecture,materials,lighting') is None
    import pytest
    with pytest.raises(ValueError):
        sp.normalize_adopt('sky')


def test_set_plate_adopt_writes_index_and_syncs(tmp_path, monkeypatch):
    import json
    base, ep = tmp_path, 'ep01'
    f = facts((0, 1.5, 0), (0, 1.5, -10), 27)
    idx = {'schema_version': sp.SCHEMA_EPISODE, 'ep': ep, 'shots': {'sh001': {'group_id': 'grp001', 'scene_id': 'SCN-1', 'plates': [
        {'role': 'start', 'key': 'k', 'file': 'x.png', 'reuse': 'manual', 'camera': f}]}}}
    sp.save_episode_index(base, ep, idx)
    synced = []
    monkeypatch.setattr(sp, 'sync_group', lambda b, e, g, write=False, **kw: synced.append(g) or {'group_id': g, 'errors': [], 'warnings': [], 'updated': True})
    res = sp.set_plate_adopt(base, ep, 'sh001', 'start', adopt='layout,architecture,materials', exclude_note='不采用右侧海面')
    assert res['adopt'] == ['layout', 'architecture', 'materials'] and res['exclude_note'] == '不采用右侧海面' and synced == ['grp001']
    slot = sp.load_episode_index(base, ep)['shots']['sh001']['plates'][0]
    assert slot['adopt'] == ['layout', 'architecture', 'materials'] and slot['exclude_note'] == '不采用右侧海面' and slot['camera'] == f
    sp.set_plate_adopt(base, ep, 'sh001', 'start', exclude_note='')
    slot = sp.load_episode_index(base, ep)['shots']['sh001']['plates'][0]
    assert slot['adopt'] and 'exclude_note' not in slot
    sp.set_plate_adopt(base, ep, 'sh001', 'start', clear=True)
    slot = sp.load_episode_index(base, ep)['shots']['sh001']['plates'][0]
    assert 'adopt' not in slot and 'exclude_note' not in slot
