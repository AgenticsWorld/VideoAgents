# -*- coding: utf-8 -*-
"""去人声 / 去环境声(modules/audio_separation.py + post_plan / mix_manifest 接线)的离线测试:不加载模型、不联网。"""
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("VIDEOAGENTS_DATA_DIR", tempfile.mkdtemp(prefix="va-sep-"))
sys.path.insert(0, str(ROOT / "modules"))

import audio_separation as asep  # noqa: E402
import mix_manifest as mb  # noqa: E402
import post_plan as pp  # noqa: E402


def test_stft_roundtrip():
    rng = np.random.default_rng(0)
    x = rng.standard_normal((2, asep.CHUNK)) * 0.1
    spec = asep.stft(x)
    assert spec.shape == (2, asep.N_BINS, asep.DIM_T)
    y = asep.istft(spec)
    assert y.shape == x.shape
    # 去掉两端半窗(反射填充区重建不保证),中段应无损重建
    assert np.max(np.abs(y[:, asep.TRIM:-asep.TRIM] - x[:, asep.TRIM:-asep.TRIM])) < 1e-9


def test_model_io_layout():
    spec = np.zeros((2, asep.N_BINS, asep.DIM_T), dtype=complex)
    spec[0] += 1 + 2j
    spec[1] += 3 + 4j
    x = asep._to_model(spec)
    assert x.shape == (1, 4, asep.DIM_F, asep.DIM_T) and x.dtype == np.float32
    assert np.all(x[0, :, :3] == 0)                                   # 最低 3 个频点清零
    assert [float(x[0, c, 10, 0]) for c in range(4)] == [1.0, 2.0, 3.0, 4.0]
    back = asep._from_model(x)
    assert back.shape == spec.shape and back[1, 10, 0] == 3 + 4j and back[0, asep.DIM_F, 0] == 0


def test_keep_gain():
    assert asep.keep_gain(-60) == 0.0 and asep.keep_gain(-80) == 0.0 and asep.keep_gain(None) == 0.0
    assert abs(asep.keep_gain(-20) - 0.1) < 1e-9
    assert asep.keep_gain(6) == 1.0


def test_range_mask_and_render():
    n = asep.SR * 4
    w = asep.range_mask(n, 1.0, 3.0)
    assert w[0] == 0 and w[int(0.9 * asep.SR)] == 0 and w[int(2.0 * asep.SR)] == 1 and w[-1] == 0
    assert 0 < w[int((1.0 + asep.RAMP_S / 2) * asep.SR)] < 1         # 段首渐变
    assert np.all(asep.range_mask(n, None, None) == 1)
    # 预览窗口从组内 2 s 开始:段 [1, 3) 落在窗口的前 1 s
    w2 = asep.range_mask(asep.SR * 2, 1.0, 3.0, offset_s=2.0)
    assert w2[int(0.5 * asep.SR)] == 1 and w2[int(1.5 * asep.SR)] == 0

    bed = np.full((2, n), 0.2)
    voice = np.full((2, n), 0.5)
    mix = bed + voice
    whole = asep.render(mix, bed, voice, [{"kind": "remove_vocals", "params": {"keep_db": -60}, "scope": {"level": "group"}}])
    assert np.allclose(whole, bed)
    amb = asep.render(mix, bed, voice, [{"kind": "remove_ambience", "params": {"keep_db": -20}, "scope": {"level": "scene"}}])
    assert np.allclose(amb, voice + 0.1 * bed)
    ranged = asep.render(mix, bed, voice, [{"kind": "remove_vocals", "params": {"keep_db": -60},
                                            "scope": {"level": "range", "t0": 1.0, "t1": 3.0}}])
    assert np.allclose(ranged[:, :int(0.9 * asep.SR)], mix[:, :int(0.9 * asep.SR)])     # 段外不动
    assert np.allclose(ranged[:, int(2.0 * asep.SR)], bed[:, 0])
    with pytest.raises(asep.SeparationError):
        asep.render(mix, bed, voice, [{"kind": "basic", "params": {}, "scope": {"level": "group"}}])


def test_separate_with_fake_session():
    class Fake:                                    # 「模型」原样返回输入:bed ≈ 输入(最低 3 频点被清零)× 补偿系数
        def get_inputs(self):
            return [type("I", (), {"name": "input"})()]

        def run(self, _out, feed):
            return [feed["input"]]

    t = np.arange(int(asep.SR * 7.3)) / asep.SR     # 跨两个分块,验证拼接与补零裁剪
    mix = np.stack([0.3 * np.sin(2 * np.pi * 440 * t), 0.2 * np.sin(2 * np.pi * 880 * t)])
    bed, voice = asep.separate(Fake(), mix)
    assert bed.shape == mix.shape and voice.shape == mix.shape
    # 模型输入会清掉 14 Hz 以下的 3 个频点:合成正弦从静音突然起振 / 截止处的次声瞬态不参与比较
    mid = slice(asep.SR, -asep.SR)
    assert np.max(np.abs(bed / asep.COMPENSATE - mix)[:, mid]) < 1e-3
    assert np.allclose(bed + voice, mix)


def test_catalog_and_cost():
    assert pp.SEPARATION_KINDS == {"remove_vocals", "remove_ambience"} == set(asep.KINDS)
    for kid in pp.SEPARATION_KINDS:
        k = pp.KIND_BY_ID[kid]
        assert k["section"] == "sound" and k["exec"] == "ffmpeg" and "range" in k["scopes"] and "episode" in k["scopes"]
    r = pp.make_recipe("remove_vocals", {"level": "group", "group_id": "grp001"}, {"keep_db": -200}, None, "去掉对白")
    assert r["layer"] == "sound" and r["params"]["keep_db"] == -60 and pp.cost_estimate(r) == "本机模型 · ¥0"
    assert pp.cost_estimate({"kind": "basic", "exec": "ffmpeg"}) == "ffmpeg 本机 · ¥0"


def test_sound_version_chain():
    plan = pp.empty_plan("ep01")
    plan["versions"]["grp001"] = [{"v": 1, "base_v": 0, "file": "a"}, {"v": 2, "base_v": 1, "file": "b", "sound": True},
                                  {"v": 3, "base_v": 2, "file": "c"}, {"v": 4, "base_v": 0, "file": "d"}]
    assert pp.sound_version(plan, "grp001") == 0
    assert pp.sound_version(plan, "grp001", 1) == 0
    assert pp.sound_version(plan, "grp001", 2) == 2
    assert pp.sound_version(plan, "grp001", 3) == 2      # 在去人声版本上再调色:声轨仍来自 v2
    assert pp.sound_version(plan, "grp001", 4) == 0      # 另起于母本的版本:原声


def test_mix_check_fails_when_sound_changed():
    res = {"status": mb.STATUS_VERSIONS_CHANGED, "boundary_status": mb.BND_NONE, "sound_changed": ["grp001"], "detail": "x"}
    assert mb.check_row(res)[0] == "FAIL"
    res["sound_changed"] = []
    assert mb.check_row(res)[0] == "WARN"
    assert mb.check_row({"status": mb.STATUS_CURRENT, "boundary_status": mb.BND_NONE, "sound_changed": []})[0] == "PASS"
