"""视频提示词接线的修订(2026-09-14,liaozhai3 S04-09 反例;同日的全景/母图两处修改实测效果不佳已回退):
① 夜间洞口暗面规则(modules/scene_panos 帮助函数,只供视频提示词【场景】段);
② 视频提示词逐镜画内/画外/背景/构图层次机器句,幂等回写(modules/shot_plates.apply_prompt)。"""
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules import scene_panos as sc  # noqa: E402
from modules import shot_plates as sp  # noqa: E402

FMT = {'width': 960, 'height': 540, 'aspect_ratio': '16:9'}


def room_scene():
    """9×6 m 房间:北墙整面,东墙带一扇窗(窗台 1.05 m,过梁自 2.15 m 起),西墙一扇门(过梁自 2.15 m 起),案头贴北墙,烛台在案上。"""
    return {'scene_id': 'S', 'dimensions_m': [9, 3.6, 6], 'objects': [
        {'id': 'room-n-end', 'position': [0, 1.8, -2.1], 'size_m': [5.76, 3.6, 0.18]},
        {'id': 'room-e-dong_chuang-before', 'position': [2.97, 1.8, -1.54], 'size_m': [0.18, 3.6, 1.12]},
        {'id': 'room-e-dong_chuang-sill', 'position': [2.97, 0.525, -0.48], 'size_m': [0.18, 1.05, 1.0]},
        {'id': 'room-e-dong_chuang-lintel', 'position': [2.97, 2.875, -0.48], 'size_m': [0.18, 1.45, 1.0]},
        {'id': 'room-w-xi_ban_men-lintel', 'position': [-2.79, 2.875, 0.1], 'size_m': [0.18, 1.45, 1.0]},
        {'id': 'an_tou', 'position': [-0.1, 0.4, -1.7], 'size_m': [1.6, 0.8, 0.5]},
        {'id': 'tong_zhu_tai', 'position': [-0.8, 1.0, -1.7], 'size_m': [0.15, 0.4, 0.15]},
        {'id': 'ming_fu', 'position': [0, 3.3, 0.2], 'size_m': [5.7, 0.3, 0.3]},
    ]}


def room_layout():
    return {'orientation': {'top_of_map': '北后檐粉墙(整面抹白灰的粉墙,无洞口)'},
            'landmarks': [
                {'id': 'bei_fen_qiang', 'name': '北后檐粉墙', 'kind': 'feature', 'xy': [0.5, 0.15]},
                {'id': 'an_tou', 'name': '案头', 'kind': 'furniture', 'xy': [0.49, 0.22]},
                {'id': 'tong_zhu_tai', 'name': '铜烛台', 'kind': 'light', 'xy': [0.41, 0.22]},
                {'id': 'ming_fu', 'name': '老木明栿', 'kind': 'feature', 'xy': [0.5, 0.53]},
                {'id': 'kong_di', 'name': '室中空地', 'kind': 'feature', 'xy': [0.45, 0.6]},
                {'id': 'xi_ban_men', 'name': '西双扇板门', 'kind': 'entrance', 'xy': [0.19, 0.52]},
                {'id': 'dong_chuang', 'name': '东侧窗户', 'kind': 'opening', 'xy': [0.83, 0.42]},
            ]}


NIGHT = {'condition': {'time_of_day': '入夜'}, 'key_source': '烛火(案角铜烛台)', 'direction': '低位侧后'}
DAY = {'condition': {'time_of_day': '午后'}, 'key_source': '窗外日光', 'direction': '侧光'}
SHOT_CAM = {'position': [-2.4, 1.25, 0.2], 'target': [-1, 1.15, -1.2], 'fov': 33.4}


# ---------------------------------------------------------------- ① 洞口规则(帮助函数)
def test_opening_apertures_from_wall_segments():
    aps = {a['id']: a for a in sc.opening_apertures(room_scene(), room_layout())}
    assert set(aps) == {'dong_chuang', 'xi_ban_men'}
    win = aps['dong_chuang']
    assert win['name'] == '东侧窗户' and win['kind'] == 'opening'
    assert abs(win['size_m'][1] - 1.1) < 1e-6 and abs(win['position'][1] - 1.6) < 1e-6      # 窗台顶 1.05 → 过梁底 2.15
    door = aps['xi_ban_men']
    assert door['kind'] == 'entrance' and abs(door['size_m'][1] - 2.15) < 1e-6 and abs(door['position'][1] - 1.075) < 1e-6


def test_openings_rule_only_for_night_without_window_light():
    aps = sc.opening_apertures(room_scene(), room_layout())
    rule, neg = sc.openings_rule(NIGHT, aps)
    assert '东侧窗户' in rule and '西双扇板门' in rule and 'dark and unlit' in rule and 'glowing window' in neg
    assert sc.openings_rule(DAY, aps) == ('', '')                                   # 白天不写
    night_window = dict(NIGHT, key_source='窗外月光')
    assert sc.openings_rule(night_window, aps) == ('', '')                          # 主光来自窗则不写
    assert sc.openings_rule(NIGHT, []) == ('', '')
    assert '东侧窗户' in sc.openings_rule_zh(NIGHT, aps) and sc.openings_rule_zh(DAY, aps) == ''


# ---------------------------------------------------------------- ② 逐镜画内/画外机器句
def test_shot_view_extras_lists_backdrop_and_out_of_frame():
    ex = sp.shot_view_extras(room_scene(), room_layout(), SHOT_CAM, FMT)
    assert ex['in_frame'][0] == '铜烛台' and '案头' in ex['in_frame'] and '北后檐粉墙' in ex['in_frame']
    assert ex['backdrop'].startswith('北后檐粉墙')                      # 视轴正对的墙,不是最近的案头
    assert '东侧窗户' in ex['out_of_frame'] and '西双扇板门' in ex['out_of_frame']
    assert '室中空地' not in ex['out_of_frame'] and '老木明栿' not in ex['out_of_frame']   # 概念地标/头顶屋梁不列


def plates_for_prompt():
    return {'plates': [
        {'shot_id': 'S1', 'shot_no': 1, 'role': 'start', 'key': 'K1', 'file': 'assets/concepts/scenes/S/plates/K1.png', 'stale': False,
         'view': {'fraction': 0.79, 'bearing_delta_deg': 0.0, 'pitch_delta_deg': 0.0}, 'in_frame': ['铜烛台', '北后檐粉墙', '案头'],
         'out_of_frame': ['东侧窗户', '西双扇板门'], 'backdrop': '北后檐粉墙(整面抹白灰的粉墙,无洞口)', 'centre': ['北后檐粉墙', '案头'],
         'layers': {'fg': '案沿。老木边棱', 'bg': '北后檐粉墙落进近黑;上缘是暗梁区'}},
        {'shot_id': 'S2', 'shot_no': 2, 'role': 'start', 'key': 'K2', 'file': 'assets/concepts/scenes/S/plates/K2.png', 'stale': False,
         'view': {'fraction': 0.9}, 'in_frame': ['西墙', '西双扇板门'], 'out_of_frame': ['案头'], 'backdrop': '西墙', 'centre': ['西墙'], 'layers': {}},
    ], 'missing': [], 'group': {'group_id': 'g'}, 'openings_zh': '本场为夜景，所有窗户与门口都是不透光的暗面', 'openings_en': 'Every window is dark.'}


def test_apply_prompt_v25_injects_machine_sentences_idempotently():
    prompt = {'refs': ['assets/concepts/characters/C/sheet.png', 'assets/concepts/props/P/main.png'],
              'video_prompt': '【生成目标】两镜。 人物@Image 1。 道具@Image 2。\n\nShot 1: 她把镜奁放上案头。\n\nShot 2: 她退回帘后。\n\nGlobal constraints: no text.'}
    plan = plates_for_prompt()
    once, warns = sp.apply_prompt(prompt, plan, v25=True)
    vp = once['video_prompt']
    assert once['refs'] == ['assets/concepts/characters/C/sheet.png', 'assets/concepts/scenes/S/plates/K1.png',
                            'assets/concepts/scenes/S/plates/K2.png', 'assets/concepts/props/P/main.png']
    assert '道具@Image 4' in vp and not warns
    assert '约占母图宽高的8成' in vp and '镜头取景与它基本一致' in vp and '本场为夜景' in vp
    assert '场景激活：使用场景A（[Image 2]）；不采用场景B（[Image 3]）。本镜画内自左向右：铜烛台、北后檐粉墙、案头。画外不入画（不得画进本镜）：东侧窗户、西双扇板门。本镜背景：北后檐粉墙(整面抹白灰的粉墙,无洞口)。构图层次：前景案沿；老木边棱；背景北后檐粉墙落进近黑;上缘是暗梁区。' in vp
    assert vp.count('本镜画内自左向右') == 2 and vp.count('构图层次：') == 1
    twice, _ = sp.apply_prompt(once, plan, v25=True)
    assert twice['video_prompt'] == vp and twice['refs'] == once['refs']
    errs, _ = sp.check_prompt(twice, plan, 'g', v25=True)
    assert errs == []
    # 手工删掉机器句 → 机检报错,--write 补回
    broken = copy.deepcopy(twice)
    broken['video_prompt'] = broken['video_prompt'].replace('本镜画内自左向右：铜烛台、北后檐粉墙、案头。画外不入画（不得画进本镜）：东侧窗户、西双扇板门。本镜背景：北后檐粉墙(整面抹白灰的粉墙,无洞口)。构图层次：前景案沿；老木边棱；背景北后檐粉墙落进近黑;上缘是暗梁区。', '')
    errs, _ = sp.check_prompt(broken, plan, 'g', v25=True)
    assert any('本镜画内' in e for e in errs)
    fixed, _ = sp.apply_prompt(broken, plan, v25=True)
    assert fixed['video_prompt'] == vp


def test_apply_prompt_v20_and_h3_carry_lists_idempotently():
    prompt = {'refs': ['assets/concepts/characters/C/sheet.png'],
              'video_prompt': 'Intro. Shot 1: she places the box. Shot 2: she leaves. Global constraints: no text.'}
    plan = plates_for_prompt()
    v20, _ = sp.apply_prompt(prompt, plan, v25=False, h3=False)
    assert 'In frame left to right: 铜烛台, 北后檐粉墙, 案头' in v20['video_prompt'] and 'Every window is dark.' in v20['video_prompt']
    assert 'about 79% of its width and height' in v20['video_prompt']
    assert sp.apply_prompt(v20, plan)[0]['video_prompt'] == v20['video_prompt']
    h3, _ = sp.apply_prompt(prompt, plan, v25=False, h3=True)
    assert 'Plate anchor:' in h3['video_prompt'] and 'Not in frame (never paint them into this shot): 东侧窗户, 西双扇板门.' in h3['video_prompt']
    assert sp.apply_prompt(h3, plan, h3=True)[0]['video_prompt'] == h3['video_prompt']
    assert sp.check_prompt(h3, plan, 'g', h3=True)[0] == []


def test_h3_anchor_line_excludes_own_plate_when_shared_across_shots():
    """同一张背景图被本组多个镜共用(grp010 反例):本镜的「not used in this shot」不得列出本镜自己的图。"""
    plan = plates_for_prompt()
    shared = dict(plan['plates'][0], shot_id='S3', shot_no=3)
    plan['plates'].append(shared)
    prompt = {'refs': ['assets/concepts/characters/C/sheet.png'],
              'video_prompt': 'Intro. Shot 1: she places the box. Shot 2: she leaves. Shot 3: she returns. Global constraints: no text.'}
    out, _ = sp.apply_prompt(prompt, plan, v25=False, h3=True)
    vp = out['video_prompt']
    assert out['refs'] == ['assets/concepts/characters/C/sheet.png', 'assets/concepts/scenes/S/plates/K1.png', 'assets/concepts/scenes/S/plates/K2.png']
    shot1 = vp[vp.index('Shot 1:'):vp.index('Shot 2:')]
    shot2 = vp[vp.index('Shot 2:'):vp.index('Shot 3:')]
    shot3 = vp[vp.index('Shot 3:'):]
    assert "corresponds to <Picture 2> ([Image 2])" in shot1 and '<Picture 3> ([Image 3]) not used in this shot.' in shot1
    assert '<Picture 2> ([Image 2]) not used' not in shot1
    assert "corresponds to <Picture 3> ([Image 3])" in shot2 and '<Picture 2> ([Image 2]) not used in this shot.' in shot2
    assert "corresponds to <Picture 2> ([Image 2])" in shot3 and '<Picture 3> ([Image 3]) not used in this shot.' in shot3
    assert '<Picture 2> ([Image 2]) not used' not in shot3
    assert sp.apply_prompt(out, plan, h3=True)[0]['video_prompt'] == vp
    assert sp.check_prompt(out, plan, 'g', h3=True)[0] == []
