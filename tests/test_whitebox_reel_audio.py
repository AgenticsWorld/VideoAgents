"""#75:开着「生成对白语音」、库里全是 unbound 句时,刚发布的无声白模样片不应立即判 audio 过期。"""
import json
import shutil
import subprocess

import pytest

from modules import dialogue_tts as dt
from modules.whitebox_export import concat_episode, episode_reel_paths, episode_reel_status


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding='utf-8')


def _tiny_clip(path, seconds):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([shutil.which('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i',
                    'color=c=gray:s=64x36:r=24', '-t', str(seconds), '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(path)],
                   check=True)


@pytest.fixture
def silent_reel_project(tmp_path, monkeypatch):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('ffmpeg unavailable')
    base = tmp_path/'demo'
    write(base/'settings.json', {'output': {'spatial_blocking': True, 'dialogue_tts': True, 'dialogue_voice': 'native'}})
    write(base/'directing/ep01/shot_list.json', {'shots': [
        {'shot_id': 'sh1', 'duration_s': 2, 'dialogue': [{'speaker': '路人', 'text': '你好'}]}],
        'generation_groups': [{'group_id': 'grp001', 'scene_id': 'SCN-1', 'shots': ['sh1'], 'total_duration_s': 2}]})
    _tiny_clip(base/'assets/whitebox/ep01/grp001/camera.mp4', 2)
    write(base/'assets/whitebox/ep01/grp001/manifest.json', {'group_id': 'grp001', 'duration_s': 2, 'fps': 24, 'width': 64,
                                                             'height': 36, 'source_sha256': 'src'})
    # 非空、但全是 unbound 的对白语音库(无可排音频)
    write(dt.lib_dir(base, 'ep01')/dt.MANIFEST, {'lines': [
        {'shot_id': 'sh1', 'idx': 0, 'status': 'unbound', 'key': 'k1', 'reason': 'speaker 不是人物/生物编号:路人'}]})
    monkeypatch.setattr(dt, 'ensure', lambda *a, **kw: None)   # 不做真合成
    return base


def test_all_unbound_library_silent_reel_is_current(silent_reel_project):
    base = silent_reel_project
    assert dt.library_fingerprint(dt.load_manifest(base, 'ep01'))   # 库指纹非空,正是旧 bug 的触发条件
    concat_episode(base, 'ep01')
    status = episode_reel_status(base, 'ep01')
    assert status['exists'] and not status['stale'] and status['stale_reason'] == ''
    audio = status['manifest']['audio']
    assert audio['kind'] == 'dialogue_tts' and audio['lines'] == 0 and audio['sha256']


def test_legacy_silent_manifest_self_heals(silent_reel_project):
    """存量无声样片清单记 audio=False:当下仍无可排音频时不判过期。"""
    base = silent_reel_project
    concat_episode(base, 'ep01')
    manifest_path = base/episode_reel_paths('ep01')['manifest']
    record = json.loads(manifest_path.read_text(encoding='utf-8'))
    record['audio'] = False
    manifest_path.write_text(json.dumps(record), encoding='utf-8')
    assert not episode_reel_status(base, 'ep01')['stale']


def test_reel_with_dialogue_track_still_goes_stale_when_library_empties(silent_reel_project):
    """带过对白轨(lines>0)的样片,库变了(即便现在全 unbound)仍须判 audio 过期。"""
    base = silent_reel_project
    concat_episode(base, 'ep01')
    manifest_path = base/episode_reel_paths('ep01')['manifest']
    record = json.loads(manifest_path.read_text(encoding='utf-8'))
    record['audio'] = {'kind': 'dialogue_tts', 'lines': 1, 'sha256': 'old-library', 'overflow': []}
    manifest_path.write_text(json.dumps(record), encoding='utf-8')
    status = episode_reel_status(base, 'ep01')
    assert status['stale'] and status['stale_reason'] == 'audio'
