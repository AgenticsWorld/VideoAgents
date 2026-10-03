"""场景页平面/全景两块图像模型偏好(2026-09-12):路径判类 + panos 独立不回退到 scenes。"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules import genmedia  # noqa: E402


def test_kind_of_output_panos_vs_scenes():
    k = genmedia.image_kind_of_output
    assert k('p/assets/concepts/scenes/SCN-0002/panos/A1/day.png') == 'panos'
    assert k('p/assets/concepts/scenes/SCN-0002/plates/k1.png') == 'scenes'
    assert k('assets/concepts/scenes/SCN-1/layout_top.png') == 'scenes'
    assert k('assets/concepts/props/P1/a.png') == 'props'
    assert k('foo/bar.png') == ''
    assert 'panos' in genmedia.IMAGE_PREF_KINDS


def test_panos_pref_independent_of_scenes(tmp_path, monkeypatch):
    st = tmp_path / 'state.json'
    st.write_text(json.dumps({'image_model_prefs': {'scenes': {'provider': 'fal', 'model': 'flux2'},
                                                   'panos': {'provider': '', 'model': ''}}}))
    cfg = tmp_path / 'genconfig.json'
    cfg.write_text(json.dumps({'image': {'provider': 'volcengine', 'fal': {'api_key': 'x'}, 'volcengine': {}}}))
    monkeypatch.setattr(genmedia, 'STATE_PATH', st)
    monkeypatch.setattr(genmedia, 'CONFIG_PATH', cfg)
    for k in ('VIDEOAGENTS_IMAGE_PROVIDER', 'VIDEOAGENTS_IMAGE_MODEL'):
        monkeypatch.delenv(k, raising=False)
    assert genmedia.image_model_pref('scenes') == {'provider': 'fal', 'model': 'flux2'}
    assert genmedia.image_model_pref('panos') == {'provider': '', 'model': ''}
    with genmedia.image_pref_env('p/assets/concepts/scenes/S1/panos/A1/day.png') as kind:
        assert kind == ''                       # 全景跟随全局:不套平面模型
        assert not os.environ.get('VIDEOAGENTS_IMAGE_PROVIDER')
    with genmedia.image_pref_env('p/assets/concepts/scenes/S1/plates/k.png') as kind:
        assert kind == 'scenes'
        assert os.environ.get('VIDEOAGENTS_IMAGE_PROVIDER') == 'fal'
    assert not os.environ.get('VIDEOAGENTS_IMAGE_PROVIDER')
