"""Exercise continuation planning, actual media extraction and submission guards offline."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from modules import continuity_refs as cr
from modules.whitebox_refs import apply_prompt as whitebox_prompt, plan_refs


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.fixture
def project(tmp_path):
    base = tmp_path/'demo'
    write(base/'settings.json', {'duration': {'long_take': True}, 'output': {'spatial_blocking': False}})
    write(base/'directing/ep01/shot_list.json', {'generation_groups': [
        {'group_id': 'grp001', 'scene_id': 'room'}, {'group_id': 'grp002', 'scene_id': 'room'}]})
    write(base/'directing/ep01/continuity.json', {'group_transitions': [
        {'from_group': 'grp001', 'to_group': 'grp002', 'anchor': 'last_frame', 'boundary_type': 'continuous'}]})
    write(base/'assets/group_settings/ep01/grp002.json', {'video_model': 'bytedance/seedance-2.0', 'provider': 'fal'})
    write(base/'assets/prompts/ep01/grp002.json', {'group_id': 'grp002', 'refs': ['assets/hero.png'],
        'video_prompt': 'Hero@Image 1. Shot 1: The hero keeps walking. Global constraints: no text.'})
    (base/'assets/hero.png').write_bytes(b'image')
    return base


def edit(path, fn):
    d = json.loads(path.read_text()); fn(d); write(path, d)


def media(base):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg unavailable')
    path = base/'assets/clips/ep01/grp001.mp4'
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                    'color=c=red:s=128x72:r=24:d=6', '-c:v', 'libx264', str(path)], check=True)
    return path


def test_selection_legacy_off_scene_transition(project):
    assert cr.plan(project, 'ep01', 'grp001')['mode'] == 'none'
    assert cr.plan(project, 'ep01', 'grp002')['mode'] == 'tail_video'
    cp = project/'directing/ep01/continuity.json'
    edit(cp, lambda d: d['group_transitions'][0].pop('boundary_type'))
    assert cr.plan(project, 'ep01', 'grp002')['mode'] == 'last_frame'
    edit(cp, lambda d: d['group_transitions'][0].update(transition={'type': 'dissolve'}))
    assert cr.plan(project, 'ep01', 'grp002')['mode'] == 'none'
    edit(project/'settings.json', lambda d: d['duration'].update(long_take=False))
    assert cr.plan(project, 'ep01', 'grp002')['mode'] == 'none'


@pytest.mark.parametrize('model,provider', [('minimax/h3-max-turbo', 'fal'), ('fal-ai/kling-video/v3/pro', 'fal'),
    ('MiniMax-H3', 'comfyui'), ('unknown', 'fal')])
def test_unsupported_falls_back(project, model, provider):
    p = cr.plan(project, 'ep01', 'grp002', budget={'model': model, 'provider': provider,
                'max_videos': 3, 'max_total_s': 15})
    assert p['mode'] == 'last_frame'


def test_missing_previous_is_pending_not_independent(project):
    row = cr.sync_group(project, 'ep01', 'grp002', write=True)
    assert row['mode'] == 'tail_video' and not row['ready']
    assert not cr.sync_group(project, 'ep01', 'grp002')['updated']
    with pytest.raises(ValueError, match='前组视频'):
        cr.sync_group(project, 'ep01', 'grp002', prepare=True)


def test_real_tail_prepare_guard_and_invalidation(project):
    source = media(project)
    row = cr.sync_group(project, 'ep01', 'grp002', prepare=True)
    assert row['ready'] and row['mode'] == 'tail_video'
    assert cr.probe(project/row['video']) == pytest.approx(3, abs=.04)
    path = project/'assets/prompts/ep01/grp002.json'
    pj = json.loads(path.read_text())
    cfg = {'provider': 'fal', 'model': 'bytedance/seedance-2.0'}
    output = project/'assets/clips/ep01/grp002.mp4'
    cr.validate_request(output, pj['video_prompt'], pj['refs'], pj['video_refs'], cfg)
    with pytest.raises(ValueError, match='不一致'):
        cr.validate_request(output, pj['video_prompt'], pj['refs'], [], cfg)
    assert not cr.sync_group(project, 'ep01', 'grp002', prepare=True)['updated']
    assert not cr.sync_group(project, 'ep01', 'grp002')['updated']
    source.touch()
    with pytest.raises(ValueError, match='重生成'):
        cr.validate_request(output, pj['video_prompt'], pj['refs'], pj['video_refs'], cfg)


def test_short_last_shot_falls_back_and_stays_valid(project):
    source = media(project)
    write(source.with_suffix('.meta.json'), {'boundaries_s': [5.]})
    row = cr.sync_group(project, 'ep01', 'grp002', prepare=True)
    assert row['mode'] == 'last_frame' and row['ready']
    assert (project/row['image']).is_file()
    assert cr.plan(project, 'ep01', 'grp002')['mode'] == 'last_frame'
    pj = json.loads((project/'assets/prompts/ep01/grp002.json').read_text())
    cr.validate_request(project/'assets/clips/ep01/grp002.mp4', pj['video_prompt'], pj['refs'],
                        pj['video_refs'], {'provider': 'fal', 'model': 'bytedance/seedance-2.0'})


def whitebox(base, duration):
    edit(base/'settings.json', lambda d: d['output'].update(spatial_blocking=True))
    write(base/'directing/ep01/whitebox/episode.json', {'groups': [{'group_id': 'grp002', 'actors': []}]})
    files = ['assets/whitebox/ep01/grp002/camera.mp4']
    for f in files:
        p = base/f; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b'video')
    write(base/'assets/whitebox/ep01/grp002/manifest.json', {'files': files, 'duration_s': duration})


def test_shared_budget_and_video_indices(project):
    whitebox(project, 7)
    assert cr.plan(project, 'ep01', 'grp002')['mode'] == 'tail_video'
    assert len(plan_refs(project, 'ep01', 'grp002')['videos']) == 1  # 7+7+3 > 15
    cr.sync_group(project, 'ep01', 'grp002', write=True)
    pj = json.loads((project/'assets/prompts/ep01/grp002.json').read_text())
    assert pj['video_refs'][0].endswith('camera.mp4')
    assert 'Extend [Video 2]' in pj['video_prompt']
    assert '向后延长 [Video 2]' in pj['video_prompt']
    assert not cr.sync_group(project, 'ep01', 'grp002')['updated']
    whitebox(project, 15)
    assert cr.plan(project, 'ep01', 'grp002')['mode'] == 'last_frame'
    cr.sync_group(project, 'ep01', 'grp002', write=True)
    pj = json.loads((project/'assets/prompts/ep01/grp002.json').read_text())
    assert len(pj['video_refs']) == 1 and pj['refs'][-1].endswith(cr.TAIL_IMAGE)


def test_remove_middle_tail_image_renumbers_identity():
    p = {'refs': ['a.png', 'old.last_frame.png', 'b.png'], 'video_prompt':
         'opening continues from [Image 2]. Alice@Image 1. Bob@Image 3. Shot 1: walking.'}
    result = cr.apply_prompt(p, {'mode': 'tail_video', 'video': 'tail.continuation.mp4'})
    assert result['refs'] == ['a.png', 'b.png'] and 'Bob@Image 2' in result['video_prompt']
    assert cr.apply_prompt(result, result['continuity_ref']) == result


def test_continuous_rejects_cut_instruction(project):
    p = {'refs': [], 'video_prompt': 'Shot 1: cut to a reverse angle.'}
    with pytest.raises(ValueError, match='切镜'):
        cr.apply_prompt(p, cr.plan(project, 'ep01', 'grp002'))


def test_disable_removes_only_continuation(project):
    cr.sync_group(project, 'ep01', 'grp002', write=True)
    edit(project/'settings.json', lambda d: d['duration'].update(long_take=False))
    cr.sync_group(project, 'ep01', 'grp002', write=True)
    pj = json.loads((project/'assets/prompts/ep01/grp002.json').read_text())
    assert pj['video_refs'] == [] and pj['refs'] == ['assets/hero.png']
    assert cr.START not in pj['video_prompt']


def test_actual_cut_detection_without_metadata(project):
    path = media(project)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
        'color=c=red:s=128x72:r=24:d=5', '-f', 'lavfi', '-i', 'color=c=blue:s=128x72:r=24:d=1',
        '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0[v]', '-map', '[v]',
        '-c:v', 'libx264', str(path)], check=True)
    row = cr.sync_group(project, 'ep01', 'grp002', prepare=True)
    assert row['mode'] == 'last_frame'
    assert row['duration_s'] == pytest.approx(1, abs=.05)


def test_black_tail_blocks_invalid_frame_fallback(project):
    path = media(project)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
        'color=c=red:s=128x72:r=24:d=5', '-f', 'lavfi', '-i', 'color=c=black:s=128x72:r=24:d=1',
        '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0[v]', '-map', '[v]',
        '-c:v', 'libx264', str(path)], check=True)
    with pytest.raises(ValueError, match='黑场'):
        cr.plan(project, 'ep01', 'grp002', prepare=True)


def test_prepare_cli(project):
    import sys
    media(project)
    script = Path(__file__).resolve().parents[1]/'code/sync_continuity_refs.py'
    command = [sys.executable, str(script), '--out-root', str(project), '--ep', 'ep01', 'grp002']
    result = subprocess.run(command+['--prepare'], check=True, capture_output=True, text=True)
    assert json.loads(result.stdout)['ready']
    subprocess.run(command, check=True, capture_output=True, text=True)


def test_genmedia_cli_dry_run_uses_prepared_inputs(project, tmp_path):
    import os
    import sys
    media(project)
    cr.sync_group(project, 'ep01', 'grp002', prepare=True)
    pj = json.loads((project/'assets/prompts/ep01/grp002.json').read_text())
    config = tmp_path/'genconfig.json'
    write(config, {'video': {'provider': 'fal', 'fal': {'model': 'bytedance/seedance-2.0', 'api_key': 'offline-test'}}})
    script = Path(__file__).resolve().parents[1]/'modules/genmedia.py'
    args = [sys.executable, str(script), 'video', '--dry-run', '--prompt', pj['video_prompt'],
            '--output', str(project/'assets/clips/ep01/grp002.mp4'), '--duration', '5', '--resolution', '480p',
            '--ref', str(project/pj['refs'][0]), '--ref-video', str(project/pj['video_refs'][0])]
    env = dict(os.environ, VIDEOAGENTS_CONFIG_PATH=str(config))
    env.pop('VIDEOAGENTS_PROJECT', None)
    result = subprocess.run(args, env=env, cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'reference-to-video' in result.stdout
    result = subprocess.run(args[:-2], env=env, cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode != 0 and '不一致' in result.stderr


def test_tail_shortens_to_two_seconds_for_camera_budget(project):
    media(project)
    whitebox(project, 13)
    row = cr.sync_group(project, 'ep01', 'grp002', prepare=True)
    assert row['mode'] == 'tail_video' and row['duration_s'] == 2
    assert cr.probe(project/row['video']) == pytest.approx(2, abs=.04)
    assert not cr.sync_group(project, 'ep01', 'grp002')['updated']


def test_model_budget_honors_global_limit_and_ignores_stale_provider(project, monkeypatch):
    from modules import genmedia
    from modules.whitebox_refs import video_budget
    monkeypatch.setattr(genmedia, 'get_config', lambda _: {'provider': 'volcengine', 'model': 'doubao-seedance-2-5'})
    edit(project/'settings.json', lambda d: d.update(shot_group={'max_ref_videos': 1}))
    budget = video_budget(project, 'ep01', 'grp002')
    assert budget['source'] == 'global' and budget['provider'] == 'volcengine'
    assert budget['max_videos'] == 1 and budget['max_total_s'] == 30
    # comfyui/runninghub 按工作流运行:非 H3 Ref2VA 工作流不接参考视频,回退尾帧
    monkeypatch.setattr(genmedia, 'get_config', lambda _: {'provider': 'comfyui', 'model': 'minimax-h3'})
    monkeypatch.setattr(genmedia, '_is_h3_ref2va_workflow', lambda cfg: False)
    assert cr.plan(project, 'ep01', 'grp002')['mode'] == 'last_frame'
    # H3 Ref2VA 工作流(本地/RunningHub 同一 MiniMaxH3ReferenceToVideo 节点):≤3 段/≤15s,可挂视频尾段
    monkeypatch.setattr(genmedia, '_is_h3_ref2va_workflow', lambda cfg: True)
    budget = video_budget(project, 'ep01', 'grp002')
    assert budget['provider'] == 'comfyui' and (budget['max_videos'], budget['max_total_s']) == (1, 15)
    assert cr.plan(project, 'ep01', 'grp002')['mode'] == 'tail_video'


def test_whitebox_can_export_before_continuity_plan(project):
    whitebox(project, 7)
    (project/'directing/ep01/continuity.json').unlink()
    assert plan_refs(project, 'ep01', 'grp002')['videos'] == ['assets/whitebox/ep01/grp002/camera.mp4']
    with pytest.raises(ValueError, match='连戏规划'):
        cr.sync_group(project, 'ep01', 'grp002', write=True)


def test_legacy_two_view_manifest_still_attaches_camera_only(project):
    # 2026-09-08 前导出的 manifest 记两路文件;top.mp4 已不再维护(可能缺失),只要 camera.mp4 在即照挂
    whitebox(project, 7)
    write(project/'assets/whitebox/ep01/grp002/manifest.json',
          {'files': ['assets/whitebox/ep01/grp002/top.mp4', 'assets/whitebox/ep01/grp002/camera.mp4'], 'duration_s': 7})
    plan = plan_refs(project, 'ep01', 'grp002')
    assert plan['videos'] == ['assets/whitebox/ep01/grp002/camera.mp4'] and 'top' not in plan
    cr.sync_group(project, 'ep01', 'grp002', write=True)
    pj = json.loads((project/'assets/prompts/ep01/grp002.json').read_text())
    assert 'top-down' not in pj['video_prompt'] and 'shooting direction' not in pj['video_prompt']
    assert {k: v for k, v in pj['whitebox_refs'].items() if k != 'cast'} == {
        'camera': 'assets/whitebox/ep01/grp002/camera.mp4', 'skipped_reason': '',
        'model': pj['whitebox_refs']['model'], 'source': 'sync_whitebox_refs.v1'}
    assert pj['whitebox_refs']['cast'] == {'visible': [], 'hidden': {}, 'dropped_refs': [], 'source': 'whitebox_cast.v1'}
