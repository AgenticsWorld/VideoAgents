"""白模人物参考图规约(2026-09-09):只有在本组白模摄影机视频里实际出现的人物,其参考图才进组 refs。"""
import copy
import json

from modules.scene_cast import scene_reference_rows, complete_prompt_cast, check_prompt_cast
from modules.whitebox_refs import hidden_cast_mentions
from modules.whitebox_refs import (appearing_cast, apply_prompt, cast_filter, check_prompt, hidden_cast_refs,
                                   in_frame, legend_rows, plan_refs, strip_cast_refs)


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')


def actor(cid, x, z, **extra):
    return {'id': cid, 'label': cid, 'letter': 'A', 'color': '#e63946', 'kind': 'person', 'size_m': [0.5, 1.7, 0.4],
            'keyframes': [{'t': 0, 'position': [x, 0, z], 'pose': 'stand', 'yaw': 0},
                          {'t': 4, 'position': [x, 0, z], 'pose': 'stand', 'yaw': 0}], **extra}


def camera(shot, start, **extra):
    # 机位在原点、看向 -Z,fov 40°:正前方 5m 处的人物入画,画左/右 10m 外或身后的不入画
    return {'shot_id': shot, 'start': start, 'duration_s': 2,
            'keyframes': [{'t': 0, 'position': [0, 1.5, 0], 'target': [0, 1.2, -5], 'fov': 40}], **extra}


def group(**overrides):
    g = {'group_id': 'grp001', 'duration_s': 4,
         'actors': [actor('CHAR-A', 0, -5), actor('CHAR-B', 12, -5), actor('CHAR-C', 0, 6)],
         'extras': [], 'cameras': [camera('sh001', 0), camera('sh002', 2)], 'scene_cast': ['CHAR-A', 'CHAR-B', 'CHAR-C', 'CHAR-D']}
    g.update(overrides)
    return g


RENDER = {'aspect_ratio': '16:9', 'width': 960, 'height': 540}


def test_box_frustum_test_matches_camera_geometry():
    cam = {'position': [0, 1.5, 0], 'target': [0, 1.5, -5], 'fov': 40}

    def seen(x, z):
        a = actor('CHAR-A', x, z)
        return in_frame(a, a['keyframes'][0], cam, 16 / 9)
    assert seen(0, -5)
    assert not seen(12, -5)                       # 画右远处
    assert not seen(0, 6)                         # 摄影机身后
    assert seen(5 * 0.364 * 1.78 + 0.2, -5)       # 中心出画、包围盒半宽仍压边 → 算入画


def test_appearing_cast_by_geometry_visibility_presence_and_shot_filter():
    cast = appearing_cast(group(), RENDER)
    assert cast['visible'] == ['CHAR-A']
    assert cast['hidden']['CHAR-B'] == '整组不在任一镜的摄影机画幅内'
    assert cast['hidden']['CHAR-C'] == '整组不在任一镜的摄影机画幅内'
    assert cast['hidden']['CHAR-D'] == '不在本组白模人物列表(缺席/远程)'   # scene_cast 里的缺席者
    # 关键帧整组 visible:false / presence absent / 各镜 visible_actor_ids 排除
    g = group()
    for k in g['actors'][0]['keyframes']:
        k['visible'] = False
    assert appearing_cast(g, RENDER)['hidden']['CHAR-A'].startswith('整组关键帧 visible:false')
    g = group(); g['actors'][0]['presence'] = {'state': 'remote', 'reason': '电话'}
    assert appearing_cast(g, RENDER)['hidden']['CHAR-A'] == 'scene_presence=remote(本组不在场)'
    g = group(cameras=[camera('sh001', 0, visible_actor_ids=[]), camera('sh002', 2, visible_actor_ids=['CHAR-B'])])
    assert appearing_cast(g, RENDER)['hidden']['CHAR-A'] == '各镜 visible_actor_ids 均未列入(镜头外在场人物)'
    # 移动人物在第二镜走进画幅即算出现;群演同规则
    g = group()
    g['actors'][1]['keyframes'][1]['position'] = [0, 0, -5]
    g['extras'] = [dict(actor('EXTRA-1', 1, -6), letter='')]
    cast = appearing_cast(g, RENDER)
    assert cast['visible'] == ['CHAR-A', 'CHAR-B', 'EXTRA-1']
    # 旧编译无机位数据:保守视为全部出现
    assert appearing_cast(group(cameras=[]), RENDER)['visible'] == ['CHAR-A', 'CHAR-B', 'CHAR-C']


def test_legend_and_hidden_refs_follow_appearing_cast():
    g = group(); cast = appearing_cast(g, RENDER)
    assert legend_rows(g) != legend_rows(g, cast)
    assert legend_rows(g, cast) == ['red figure = CHAR-A (CHAR-A)']
    refs = ['assets/concepts/characters/CHAR-A/sheet.png', 'assets/concepts/characters/CHAR-B/sheet_travel.png',
            'assets/concepts/creatures/CRE-X/sheet.png', 'assets/concepts/scenes/S1/plates/p.png', 'assets/keyframes/x.last_frame.png']
    assert hidden_cast_refs(refs, cast) == refs[1:3]
    assert hidden_cast_refs(refs, None) == []


def test_strip_cast_refs_renumbers_and_refuses_when_prose_still_cites():
    prompt = {'refs': ['assets/concepts/characters/CHAR-A/sheet.png', 'assets/concepts/characters/CHAR-B/sheet.png', 'plate.png'],
              'video_prompt': 'A@Image 1: CHAR-A，本场次人物的身份与服装参考。 B@Image 2: CHAR-B，本场次人物的身份与服装参考。 '
                              'Shot 1: hero [Image 1] on plate [Image 3].',
              'scene_cast_refs': [{'id': 'CHAR-B', 'ref': 'assets/concepts/characters/CHAR-B/sheet.png'}]}
    out, ok = strip_cast_refs(prompt, ['assets/concepts/characters/CHAR-B/sheet.png'])
    assert ok and out['refs'] == ['assets/concepts/characters/CHAR-A/sheet.png', 'plate.png']
    assert out['video_prompt'] == 'A@Image 1: CHAR-A，本场次人物的身份与服装参考。\n\nShot 1: hero [Image 1] on plate [Image 2].'
    assert out['scene_cast_refs'][0]['ref'] is None
    cited = dict(prompt, video_prompt=prompt['video_prompt'] + ' B looks like [Image 2].')
    same, ok = strip_cast_refs(cited, ['assets/concepts/characters/CHAR-B/sheet.png'])
    assert not ok and same == cited


def project(tmp_path, spatial=True):
    base = tmp_path / 'demo'
    write(base / 'settings.json', {'output': {'spatial_blocking': spatial}})
    write(base / 'directing/ep01/shot_list.json', {'generation_groups': [
        {'group_id': 'grp001', 'scene_id': 'S1', 'scene_no': 'S1', 'characters_union': ['CHAR-A'],
         'scene_cast': ['CHAR-A', 'CHAR-B', 'CHAR-C', 'CHAR-D']}]})
    write(base / 'directing/ep01/whitebox/episode.json', {'render': RENDER, 'groups': [group()]})
    for cid in ('CHAR-A', 'CHAR-B', 'CHAR-C', 'CHAR-D'):
        p = base / f'assets/concepts/characters/{cid}/sheet.png'
        p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b'png')
    write(base / 'assets/prompts/ep01/grp001.json', {
        'group_id': 'grp001',
        'refs': ['assets/concepts/characters/CHAR-A/sheet.png', 'assets/concepts/characters/CHAR-B/sheet.png', 'plate.png'],
        'video_prompt': 'Style. Shot 1: hero [Image 1] on plate [Image 3]. Global constraints: no text.'})
    return base


def test_scene_cast_sync_only_attaches_whitebox_visible_cast(tmp_path):
    base = project(tmp_path)
    source = json.loads((base / 'directing/ep01/shot_list.json').read_text())
    rows = scene_reference_rows(base, 'ep01', source)['grp001']
    by_id = {r['id']: r for r in rows}
    assert by_id['CHAR-A']['ref'] == 'assets/concepts/characters/CHAR-A/sheet.png'
    assert by_id['CHAR-B']['ref'] is None and by_id['CHAR-B']['whitebox_hidden'] == '整组不在任一镜的摄影机画幅内'
    assert by_id['CHAR-B']['missing'] is False   # 未出现者不算缺图
    prompt = json.loads((base / 'assets/prompts/ep01/grp001.json').read_text())
    errors = check_prompt_cast(prompt, rows)
    assert len(errors) == 1 and 'whitebox_cast_ref: CHAR-B' in errors[0]
    updated = complete_prompt_cast(prompt, rows)
    assert updated['refs'] == ['assets/concepts/characters/CHAR-A/sheet.png', 'plate.png']
    assert '[Image 2]' in updated['video_prompt'] and 'CHAR-B' not in updated['video_prompt']
    assert check_prompt_cast(updated, rows) == []
    assert complete_prompt_cast(updated, rows) == updated
    # 白模链关闭 / 本组未编译:不限制,沿用同场次全员关联
    (base / 'directing/ep01/whitebox/episode.json').unlink()
    rows = scene_reference_rows(base, 'ep01', source)['grp001']
    assert all(r['ref'] for r in rows) and not any(r.get('whitebox_hidden') for r in rows)
    assert cast_filter(project(tmp_path / 'off', spatial=False), 'ep01', 'grp001') is None


def test_cast_block_removal_survives_lost_newlines():
    rows = [{'id': 'CHAR-A', 'name': 'A', 'ref': 'a.png', 'missing': False}]
    first = complete_prompt_cast({'refs': ['a.png'], 'video_prompt': 'Style. Shot 1: go.'}, rows)
    assert first['video_prompt'].count('Scene presence references:') == 1
    # 白模段插入时 rstrip 吃掉段尾换行后再次同步,不得堆叠第二段
    damaged = dict(first, video_prompt=first['video_prompt'].replace('references.\nShot 1', 'references. Whitebox reference: x. Shot 1'))
    again = complete_prompt_cast(damaged, rows)
    assert again['video_prompt'].count('Scene presence references:') == 1
    assert complete_prompt_cast(again, rows) == again


def test_whitebox_sync_drops_hidden_cast_and_reports(tmp_path):
    base = project(tmp_path)
    write(base / 'assets/whitebox/ep01/grp001/manifest.json', {'files': ['assets/whitebox/ep01/grp001/camera.mp4'], 'duration_s': 4})
    (base / 'assets/whitebox/ep01/grp001/camera.mp4').write_bytes(b'v')
    plan = plan_refs(base, 'ep01', 'grp001')
    assert plan['cast']['visible'] == ['CHAR-A']
    prompt = json.loads((base / 'assets/prompts/ep01/grp001.json').read_text())
    errs, _ = check_prompt(prompt, plan, 'grp001')
    assert any('未在本组白模出现的人物图 assets/concepts/characters/CHAR-B/sheet.png' in e for e in errs)
    out = apply_prompt(prompt, plan)
    assert out['refs'] == ['assets/concepts/characters/CHAR-A/sheet.png', 'plate.png']
    assert out['whitebox_refs']['cast']['dropped_refs'] == ['assets/concepts/characters/CHAR-B/sheet.png']
    assert 'Whitebox legend: red figure = CHAR-A (CHAR-A);' in out['video_prompt'] and 'CHAR-B' not in out['video_prompt']
    assert 'plate [Image 2]' in out['video_prompt']
    assert check_prompt(out, plan, 'grp001') == ([], [])
    assert apply_prompt(out, plan) == out
    # 正文仍引用被移除图:不动 refs,机检继续报违规
    cited = copy.deepcopy(prompt); cited['video_prompt'] += ' B looks like [Image 2].'
    kept = apply_prompt(cited, plan)
    assert kept['refs'] == prompt['refs'] and kept['whitebox_refs']['cast']['dropped_refs'] == []
    assert any('CHAR-B' in e for e in check_prompt(kept, plan, 'grp001')[0])


def test_fixed_blocks_keep_canonical_order_across_resyncs(tmp_path):
    base = project(tmp_path)
    write(base / 'assets/whitebox/ep01/grp001/manifest.json', {'files': ['assets/whitebox/ep01/grp001/camera.mp4'], 'duration_s': 4})
    (base / 'assets/whitebox/ep01/grp001/camera.mp4').write_bytes(b'v')
    plan = plan_refs(base, 'ep01', 'grp001')
    source = json.loads((base / 'directing/ep01/shot_list.json').read_text())
    rows = scene_reference_rows(base, 'ep01', source)['grp001']
    prompt = json.loads((base / 'assets/prompts/ep01/grp001.json').read_text())
    prompt['video_prompt'] = prompt['video_prompt'].replace('Shot 1:', 'Shot plates: [Image 3] is the plate of Shot 1. Shot 1:')

    def order(text):
        import re
        return [m.group(0) for m in re.finditer(r'Scene presence references:|Whitebox reference:|Shot plates:|Shot 1:', text)]

    a = complete_prompt_cast(apply_prompt(prompt, plan), rows)
    b = apply_prompt(complete_prompt_cast(prompt, rows), plan)
    expected = ['Scene presence references:', 'Whitebox reference:', 'Shot plates:', 'Shot 1:']
    assert order(a['video_prompt']) == order(b['video_prompt']) == expected
    assert ' '.join(a['video_prompt'].split()) == ' '.join(b['video_prompt'].split())   # 仅段间空白可能不同
    # 任一 sync 重跑都不再改动正文
    assert apply_prompt(a, plan)['video_prompt'] == a['video_prompt']
    assert complete_prompt_cast(a, rows)['video_prompt'] == a['video_prompt']


def test_hidden_mention_ignores_frozen_dialogue_lines():
    """#95 #97:{} 冻结台词里提到缺席人物的名字不算把人写进画面;{} 外的描写照报。"""
    group = {'actors': [{'id': 'CHAR-0002', 'label': '李靖'}, {'id': 'CHAR-0001', 'label': '哪吒'}]}
    cast = {'visible': ['CHAR-0001'], 'hidden': {'CHAR-0002': '整组不在任一镜画幅内'}}
    quoted = 'Shot 1: 哪吒拱手:{请伯父李靖不必上本。}随后转身。'
    assert hidden_cast_mentions(quoted, group, cast) == []
    assert hidden_cast_mentions(quoted + '李靖站在阶下。', group, cast) == [('CHAR-0002', '李靖')]
