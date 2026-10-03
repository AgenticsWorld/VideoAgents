"""白模逐镜相对镜头朝向句(Whitebox facing:,2026-09-09):按计划机位与人物 yaw 推导 front/三分正/侧面/三分背/back。"""
import math

from modules.whitebox_refs import (FACING_KEY, apply_prompt, build_block, check_prompt, facing_bucket, facing_rows,
                                   facing_sentence)

RENDER = {'aspect_ratio': '16:9', 'width': 960, 'height': 540}
CAM = {'position': [0, 1.5, 0], 'target': [0, 1.2, -5], 'fov': 40}   # 原点看向 -Z;right = +X


def actor(cid, keys, **extra):
    return {'id': cid, 'label': cid, 'color': '#e63946', 'kind': 'person', 'size_m': [0.5, 1.7, 0.4], 'keyframes': keys, **extra}


def still(cid, x, z, yaw, t1=4, **extra):
    return actor(cid, [{'t': 0, 'position': [x, 0, z], 'pose': 'stand', 'yaw': yaw},
                       {'t': t1, 'position': [x, 0, z], 'pose': 'stand', 'yaw': yaw}], **extra)


def group(actors, cameras=None):
    return {'group_id': 'grp001', 'duration_s': 4, 'actors': actors, 'extras': [],
            'cameras': cameras or [{'shot_id': 'sh001', 'start': 0, 'duration_s': 4, 'keyframes': [dict(CAM, t=0)]}]}


def plan(g):
    cam = 'assets/whitebox/ep01/grp001/camera.mp4'
    return {'videos': [cam], 'camera': cam, 'group': g, 'cast': None, 'render': RENDER, 'budget': {'model': 'm'}, 'skipped_reason': ''}


def test_bucket_follows_renderer_yaw_convention():
    k = lambda yaw: {'position': [0, 0, -5], 'yaw': yaw}
    assert facing_bucket(k(0), CAM) == ('front', '')                 # 脸朝本地 +Z,人物在 -Z 处 → 朝向原点的摄影机
    assert facing_bucket(k(math.pi), CAM) == ('back', '')
    assert facing_bucket(k(math.pi / 2), CAM) == ('profile', 'right')   # 脸朝 +X = 画右
    assert facing_bucket(k(-math.pi / 2), CAM) == ('profile', 'left')
    assert facing_bucket(k(math.radians(50)), CAM) == ('three-quarter front', 'right')
    assert facing_bucket(k(math.radians(-130)), CAM) == ('three-quarter back', 'left')
    # 头部转向叠加在身体朝向上
    assert facing_bucket(dict(k(0), head_yaw=math.pi), CAM) == ('back', '')


def test_rows_phrase_only_opening_facing_and_drop_flicker():
    turning = actor('A', [{'t': 0, 'position': [0, 0, -5], 'pose': 'stand', 'yaw': 0},
                          {'t': 4, 'position': [0, 0, -5], 'pose': 'stand', 'yaw': math.pi}])
    rows = facing_rows(group([turning]), None, RENDER)
    a = rows[0]['actors'][0]
    assert a['phases'][0] == ('front', '') and a['phases'][-1] == ('back', '')   # 相位序列仍记全
    assert a['phrase'] == 'facing the camera (front view, face fully visible)'     # 句子只写开场,镜内转身由正文写
    # 开场背对、中途回头再转回:句子只写开场背对,不写首尾
    glance = actor('A', [{'t': 0, 'position': [0, 0, -5], 'pose': 'stand', 'yaw': math.pi},
                         {'t': 1.5, 'position': [0, 0, -5], 'pose': 'stand', 'yaw': math.pi},
                         {'t': 2, 'position': [0, 0, -5], 'pose': 'stand', 'yaw': math.pi, 'head_yaw': math.pi / 2},
                         {'t': 3, 'position': [0, 0, -5], 'pose': 'stand', 'yaw': math.pi, 'head_yaw': math.pi / 2},
                         {'t': 4, 'position': [0, 0, -5], 'pose': 'stand', 'yaw': math.pi}])
    assert facing_rows(group([glance]), None, RENDER)[0]['actors'][0]['phrase'].startswith('with back to the camera')
    # 切点前 0.1s 才转身:属于下一镜,本镜只报 front
    late = actor('A', [{'t': 0, 'position': [0, 0, -5], 'pose': 'stand', 'yaw': 0},
                       {'t': 1.9, 'position': [0, 0, -5], 'pose': 'stand', 'yaw': 0},
                       {'t': 2, 'position': [0, 0, -5], 'pose': 'stand', 'yaw': math.pi, 'hold': True},
                       {'t': 4, 'position': [0, 0, -5], 'pose': 'stand', 'yaw': math.pi}])
    cams = [{'shot_id': 'sh001', 'start': 0, 'duration_s': 2, 'keyframes': [dict(CAM, t=0)]},
            {'shot_id': 'sh002', 'start': 2, 'duration_s': 2, 'keyframes': [dict(CAM, t=0)]}]
    rows = facing_rows(group([late], cams), None, RENDER)
    assert [(r['shot_id'], r['actors'][0]['phases']) for r in rows] == [('sh001', [('front', '')]), ('sh002', [('back', '')])]


def test_rows_skip_occluded_out_of_frame_and_camera_inside_body():
    near, far = still('A', 0, -5, 0), still('B', 0, -15, 0)          # B 正好在 A 身后同一视线上 → 被 A 完全挡住
    rows = facing_rows(group([near, far]), None, RENDER)
    assert [a['id'] for a in rows[0]['actors']] == ['A']
    beside = still('B', 1.5, -15, 0)                                    # 挪开就露出来
    assert [a['id'] for a in facing_rows(group([near, beside]), None, RENDER)[0]['actors']] == ['A', 'B']
    inside = still('C', 0.05, -0.1, math.pi)                            # 摄影机在他身体里(dzg6 grp011 sh018 的王三合)
    assert [a['id'] for a in facing_rows(group([near, inside]), None, RENDER)[0]['actors']] == ['A']
    offscreen = still('D', 12, -5, 0)
    assert [a['id'] for a in facing_rows(group([offscreen]), None, RENDER)] == []
    hidden_all = still('E', 0, -5, 0)
    for k in hidden_all['keyframes']:
        k['visible'] = False
    assert facing_rows(group([hidden_all]), None, RENDER) == []
    # cast 过滤:图例外人物不进朝向句;visible_actor_ids 排除的人物也不进
    assert facing_rows(group([near, beside]), {'visible': ['B'], 'hidden': {}}, RENDER)[0]['actors'][0]['id'] == 'B'
    cams = [{'shot_id': 'sh001', 'start': 0, 'duration_s': 4, 'keyframes': [dict(CAM, t=0)], 'visible_actor_ids': ['B']}]
    assert [a['id'] for a in facing_rows(group([near, beside], cams), None, RENDER)[0]['actors']] == ['B']


def test_sentence_uses_lowercase_cut_headers_and_block_and_checks_round_trip():
    g = group([still('A', 0, -5, math.pi)])
    p = plan(g)
    line = facing_sentence(p)
    assert line.startswith(FACING_KEY) and 'at the start of each cut' in line and 'shot 1 of 1 (sh001) A with back to the camera' in line
    assert 'Shot 1' not in line                                         # 不得撞各机检的 `Shot N:` 切段正则
    block = build_block(p)
    assert 'Whitebox legend:' in block and line in block and block.index(line) < block.index('Do not reproduce')
    prompt = {'refs': [], 'video_refs': [], 'video_prompt': 'Style. Shot 1: he stands there. Global constraints: none.'}
    out = apply_prompt(prompt, p)
    errs, warns = check_prompt(out, p, 'grp001')
    assert errs == [] and len(warns) == 1 and '背对镜头' in warns[0] and 'sh001/A' in warns[0]   # 背影镜正文无背影字样 → WARN
    said = dict(out, video_prompt=out['video_prompt'].replace('he stands there', 'he stands with his back to the camera'))
    assert check_prompt(said, p, 'grp001') == ([], [])
    assert apply_prompt(said, p) == said                                 # 幂等
    # 旧 prompt 没有朝向句 → WARN 提示 --write;机位改了(朝向句过期)→ FAIL
    old = dict(said, video_prompt=said['video_prompt'].replace(line + ' ', ''))
    _, w = check_prompt(old, p, 'grp001')
    assert any(FACING_KEY in x and '--write' in x for x in w)
    p2 = plan(group([still('A', 0, -5, 0)]))
    e, _ = check_prompt(said, p2, 'grp001')
    assert any('不一致' in x for x in e)
    # 没有机位数据的旧编译:不写朝向句、不报
    assert facing_sentence(plan({'group_id': 'g', 'actors': [still('A', 0, -5, 0)], 'cameras': []})) == ''
