"""场景图(正向/反向,白模关闭项目的 A 方案,2026-09-17):模式解析、反向需求判定、组 prompt 接线与机检。"""
import json
from pathlib import Path

from modules import scene_plates as sp


def _proj(tmp_path: Path, mode='auto', scene_mode='inherit', plate_views=('front', 'reverse'), with_reverse=True, whitebox=False):
    base = tmp_path / 'proj'
    (base / 'assets/concepts/scenes/SCN-1').mkdir(parents=True)
    (base / 'assets/concepts/characters/CHAR-1').mkdir(parents=True)
    (base / 'assets/concepts/props/PROP-1').mkdir(parents=True)
    (base / 'directing/ep01').mkdir(parents=True)
    (base / 'assets/prompts/ep01').mkdir(parents=True)
    (base / 'settings.json').write_text(json.dumps({'output': {'spatial_blocking': whitebox, 'scene_plates': mode}}))
    for f in ('assets/concepts/scenes/SCN-1/main_01.png', 'assets/concepts/scenes/SCN-1/layout_top.png', 'assets/concepts/characters/CHAR-1/sheet.png',
              'assets/concepts/props/PROP-1/scale_ref_01.png') + (('assets/concepts/scenes/SCN-1/reverse_01.png',) if with_reverse else ()):
        (base / f).write_bytes(b'x')
    rec = {'schema_version': sp.SCHEMA, 'scene_id': 'SCN-1', 'mode': scene_mode,
           'front': {'file': 'main_01.png', 'standing_en': 'just inside the west door', 'looking_en': 'east across the hall',
                     'in_frame_en': ['the idol on the east wall', 'the kang under the south windows'], 'behind_en': ['the west door']},
           'reverse': ({'file': 'reverse_01.png', 'standing_en': 'at the east wall', 'looking_en': 'west back at the door',
                        'in_frame_en': ['the west door', 'the kang on the left']} if with_reverse else None)}
    (base / 'assets/concepts/scenes/SCN-1/scene_plates.json').write_text(json.dumps(rec))
    shots = [{'shot_id': f'ep01-sh00{i+1}', 'group_id': 'grp001', 'scene_id': 'SCN-1', 'plate_view': pv} for i, pv in enumerate(plate_views)]
    (base / 'directing/ep01/shot_list.json').write_text(json.dumps({
        'shots': shots, 'generation_groups': [{'group_id': 'grp001', 'scene_id': 'SCN-1', 'shots': [s['shot_id'] for s in shots]}]}))
    vp = ('Overall visual style: x. 甲@Image 1:一个人。 ' + ' '.join(f'Shot {i+1}: 固定机位。空间:standing by the door. 音效:<x>。' for i in range(len(shots)))
          + ' Global constraints: no watermark, split panels.')
    (base / 'assets/prompts/ep01/grp001.json').write_text(json.dumps({
        'refs': ['assets/concepts/characters/CHAR-1/sheet.png', 'assets/concepts/scenes/SCN-1/main_01.png', 'assets/concepts/scenes/SCN-1/layout_top.png',
                 'assets/concepts/props/PROP-1/scale_ref_01.png'],
        'video_prompt': vp, 'shots': [s['shot_id'] for s in shots]}))
    return base


def test_modes_and_reverse_needed(tmp_path):
    base = _proj(tmp_path)
    rec = sp.load_scene_plates(base, 'SCN-1')
    assert sp.project_mode(base) == 'auto' and sp.effective_mode(base, rec) == 'auto'
    need = sp.reverse_needed(base, 'SCN-1', rec)
    assert need['needed'] and need['needed_by'] == ['ep01/ep01-sh002']
    base2 = _proj(tmp_path / 'b', mode='single')
    assert not sp.reverse_needed(base2, 'SCN-1', sp.load_scene_plates(base2, 'SCN-1'))['needed']
    base3 = _proj(tmp_path / 'c', mode='single', scene_mode='pair', plate_views=('front',))
    assert sp.effective_mode(base3, sp.load_scene_plates(base3, 'SCN-1')) == 'pair'
    assert sp.reverse_needed(base3, 'SCN-1', sp.load_scene_plates(base3, 'SCN-1'))['needed']
    base4 = _proj(tmp_path / 'd', plate_views=('front', 'front'))
    assert not sp.reverse_needed(base4, 'SCN-1', sp.load_scene_plates(base4, 'SCN-1'))['needed']


def test_sync_writes_refs_block_and_lines(tmp_path):
    base = _proj(tmp_path)
    r = sp.sync_episode(base, 'ep01', None, write=True)
    assert r['updated_prompts'] == ['grp001'] and not r['errors'], r
    pack = json.loads((base / 'assets/prompts/ep01/grp001.json').read_text())
    assert pack['refs'] == ['assets/concepts/characters/CHAR-1/sheet.png', 'assets/concepts/scenes/SCN-1/main_01.png',
                            'assets/concepts/scenes/SCN-1/reverse_01.png', 'assets/concepts/props/PROP-1/scale_ref_01.png']
    vp = pack['video_prompt']
    assert '甲@Image 1' in vp
    assert 'Scene plates: [Image 2] is the front view' in vp and '[Image 3] is the reverse view' in vp
    assert 'Shot 1: Scene plate: this shot uses [Image 2] (the front view, standing just inside the west door, looking east across the hall) and not [Image 3].' in vp
    assert 'Shot 2: Scene plate: this shot uses [Image 3] (the reverse view' in vp and 'and not [Image 2].' in vp
    assert vp.index('Scene plates:') < vp.index('Shot 1:') < vp.index('Shot 2:') < vp.index('Global constraints:')
    assert vp.rstrip().endswith(sp.GC_EXTRA + '.')
    # 幂等:再跑一次不变、机检通过
    r2 = sp.sync_episode(base, 'ep01', None, write=True)
    assert r2['updated_prompts'] == [] and not r2['errors']
    assert vp == json.loads((base / 'assets/prompts/ep01/grp001.json').read_text())['video_prompt']
    assert (base / 'directing/ep01/scene_plates_backups/grp001.json').is_file()


def test_single_mode_uses_front_only_and_check_fails_before_write(tmp_path):
    base = _proj(tmp_path, mode='single')
    chk = sp.sync_episode(base, 'ep01', None, write=False)
    assert chk['errors'] and any('layout_top' in e or '其它图' in e for e in chk['errors'])
    r = sp.sync_episode(base, 'ep01', None, write=True)
    pack = json.loads((base / 'assets/prompts/ep01/grp001.json').read_text())
    assert 'reverse_01.png' not in pack['refs'] and 'layout_top.png' not in pack['refs']
    assert 'Shot 2: Scene plate: this shot uses [Image 2] (the front view' in pack['video_prompt'] and ' and not [Image' not in pack['video_prompt']
    assert any('single' in w for w in r['warnings'])


def test_reverse_missing_falls_back_to_front(tmp_path):
    base = _proj(tmp_path, with_reverse=False)
    r = sp.sync_episode(base, 'ep01', None, write=True)
    pack = json.loads((base / 'assets/prompts/ep01/grp001.json').read_text())
    assert 'reverse_01.png' not in pack['refs'] and any('反向图未出' in w for w in r['warnings'])
    st = sp.status(base, 'ep01')
    assert st['errors'] and 'missing_reverse' in st['scenes'][0]['state']


def test_legacy_main_without_registry(tmp_path):
    base = _proj(tmp_path)
    (base / 'assets/concepts/scenes/SCN-1/scene_plates.json').unlink()
    rec = sp.load_scene_plates(base, 'SCN-1')
    assert rec.get('legacy') and rec['front']['file'] == 'main_01.png'
    r = sp.sync_episode(base, 'ep01', None, write=True)
    pack = json.loads((base / 'assets/prompts/ep01/grp001.json').read_text())
    assert 'Scene plates: [Image 2] is the front view: an empty wide-angle photograph' in pack['video_prompt']
    assert any('旧版 main_01.png' in w for w in r['warnings'])


def test_reverse_stale_when_front_changes(tmp_path):
    base = _proj(tmp_path)
    rec = sp.load_scene_plates(base, 'SCN-1')
    rec['reverse']['front_sha256'] = sp.front_digest(base, 'SCN-1', rec)
    sp.save_scene_plates(base, 'SCN-1', rec)
    assert not sp.reverse_stale(base, 'SCN-1', sp.load_scene_plates(base, 'SCN-1'))
    assert sp.status(base, 'ep01')['scenes'][0]['state'] == 'ok'
    (base / 'assets/concepts/scenes/SCN-1/main_01.png').write_bytes(b'new front')      # 正向图重出
    rec = sp.load_scene_plates(base, 'SCN-1')
    assert sp.reverse_stale(base, 'SCN-1', rec)
    st = sp.status(base, 'ep01')
    assert st['scenes'][0]['state'] == 'stale_reverse' and any('过期' in e for e in st['errors'])
    r = sp.sync_episode(base, 'ep01', None, write=True)
    pack = json.loads((base / 'assets/prompts/ep01/grp001.json').read_text())
    assert 'reverse_01.png' not in pack['refs'] and any('过期' in w for w in r['warnings'])
    # 未记哈希的旧记录不判过期
    rec['reverse'].pop('front_sha256'); sp.save_scene_plates(base, 'SCN-1', rec)
    assert not sp.reverse_stale(base, 'SCN-1', sp.load_scene_plates(base, 'SCN-1'))


def _with_lighting(base: Path, group_scheme='L-NIGHT', base_scheme='L-DAY'):
    """母版正向图登记为日景方案;本组光照方案另设。"""
    (base / 'bible/scenes/SCN-1').mkdir(parents=True, exist_ok=True)
    (base / 'bible/scenes/SCN-1/lighting.json').write_text(json.dumps({'schemes': [
        {'scheme_id': 'L-DAY', 'condition': {'time_of_day': 'day'}, 'prompt_fragment_en': 'Lighting: soft daylight from the south windows'},
        {'scheme_id': 'L-NIGHT', 'condition': {'time_of_day': 'night'}, 'prompt_fragment_en': 'a single oil lamp on the table, deep shadows'}]}))
    rec = sp.load_scene_plates(base, 'SCN-1')
    if base_scheme:
        rec['front']['lighting_scheme_id'] = base_scheme
    sp.save_scene_plates(base, 'SCN-1', rec)
    sl = json.loads((base / 'directing/ep01/shot_list.json').read_text())
    sl['generation_groups'][0]['lighting_scheme_id'] = group_scheme
    (base / 'directing/ep01/shot_list.json').write_text(json.dumps(sl))


def test_lighting_variant_needed_per_view_and_fallback(tmp_path):
    base = _proj(tmp_path)
    _with_lighting(base)
    rec = sp.load_scene_plates(base, 'SCN-1')
    need = sp.variants_needed(base, 'SCN-1', rec)
    assert [(v['view'], v['scheme']) for v in need] == [('front', 'L-NIGHT'), ('reverse', 'L-NIGHT')]
    assert need[1]['needed_by'] == ['ep01/ep01-sh002']
    st = sp.status(base, 'ep01')
    assert st['scenes'][0]['state'] == 'missing_variant' and sum('缺光照变体' in e for e in st['errors']) == 2
    # 变体未出:接线暂用母版并 WARN
    r = sp.sync_episode(base, 'ep01', None, write=True)
    pack = json.loads((base / 'assets/prompts/ep01/grp001.json').read_text())
    assert 'assets/concepts/scenes/SCN-1/main_01.png' in pack['refs'] and any('光照变体' in w for w in r['warnings'])
    # 同方案 / single 模式 / 母版未登记方案
    base2 = _proj(tmp_path / 'b'); _with_lighting(base2, group_scheme='L-DAY')
    assert sp.variants_needed(base2, 'SCN-1', sp.load_scene_plates(base2, 'SCN-1')) == []
    base3 = _proj(tmp_path / 'c', mode='single'); _with_lighting(base3)
    assert [v['view'] for v in sp.variants_needed(base3, 'SCN-1', sp.load_scene_plates(base3, 'SCN-1'))] == ['front']
    base4 = _proj(tmp_path / 'd'); _with_lighting(base4, base_scheme=None)
    assert sp.variants_needed(base4, 'SCN-1', sp.load_scene_plates(base4, 'SCN-1')) == []
    assert any('lighting_scheme_id' in w for w in sp.status(base4, 'ep01')['warnings'])


def test_lighting_variant_render_wire_and_stale(tmp_path, monkeypatch):
    import modules.genmedia as gm
    from contextlib import nullcontext
    calls = []
    monkeypatch.setattr(gm, 'generate_image', lambda prompt, out, **kw: (calls.append((prompt, kw)), Path(out).write_bytes(b'v' + str(len(calls)).encode())))
    monkeypatch.setattr(gm, 'get_config', lambda kind: {'provider': 'fake', 'model': 'm'})
    monkeypatch.setattr(gm, 'image_pref_env', lambda kind: nullcontext())
    base = _proj(tmp_path)
    _with_lighting(base)
    res = sp.render_needed(base, 'ep01', log=lambda *a: None)
    assert res['rendered'] == ['SCN-1:front×L-NIGHT', 'SCN-1:reverse×L-NIGHT']
    prompt, kw = calls[0]
    assert 'oil lamp' in prompt and 'soft daylight' in prompt and kw['refs'][0].endswith('main_01.png')
    assert calls[1][1]['refs'][0].endswith('reverse_01.png')
    assert sp.status(base, 'ep01')['scenes'][0]['state'] == 'ok'
    sp.sync_episode(base, 'ep01', None, write=True)
    pack = json.loads((base / 'assets/prompts/ep01/grp001.json').read_text())
    assert pack['refs'][1:3] == ['assets/concepts/scenes/SCN-1/main_01__L-NIGHT.png', 'assets/concepts/scenes/SCN-1/reverse_01__L-NIGHT.png']
    assert pack['scene_plates']['lighting_variants'] == {'front': 'L-NIGHT', 'reverse': 'L-NIGHT'}
    assert not sp.sync_episode(base, 'ep01', None, write=False)['errors']
    # 已有且母版未变:不重出;母版重出 → 变体过期、自动重出
    assert sp.render_needed(base, 'ep01', log=lambda *a: None)['rendered'] == []
    (base / 'assets/concepts/scenes/SCN-1/main_01.png').write_bytes(b'new front')
    assert sp.variant_stale(base, 'SCN-1', sp.load_scene_plates(base, 'SCN-1'), 'L-NIGHT', 'front')
    assert sp.status(base, 'ep01')['scenes'][0]['state'] in ('stale_variant', 'ok') and any('过期' in e for e in sp.status(base, 'ep01')['errors'])
    assert 'SCN-1:front×L-NIGHT' in sp.render_needed(base, 'ep01', log=lambda *a: None)['rendered']
