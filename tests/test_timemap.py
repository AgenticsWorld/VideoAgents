# -*- coding: utf-8 -*-
"""modules/timemap.py:时长编辑映射(插黑 / 定格 / 删段)的纯函数与 ffmpeg 声轨重映射测试(合成 lavfi 音频,不依赖真实项目)。"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "modules"))
sys.path.insert(0, str(ROOT / "code"))

import timemap as tm  # noqa: E402

HAS_FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")


def test_normalize_drops_noop_and_sorts():
    ops = tm.normalize_ops([{"src_t0": 5, "src_t1": 7, "out_len": 2.0},          # 换段:无时长变化 → 丢
                            {"src_t0": 9, "src_t1": 9, "out_len": 0.5, "audio": "weird"},
                            {"src_t0": 2, "src_t1": 3, "out_len": 0}])
    assert [o["src_t0"] for o in ops] == [2.0, 9.0]
    assert ops[1]["audio"] == "sustain"          # 非法策略回退
    assert tm.total_delta(ops) == pytest.approx(-0.5)


def test_map_time_insert_and_delete():
    ops = [{"src_t0": 10, "src_t1": 10, "out_len": 0.5}, {"src_t0": 20, "src_t1": 22, "out_len": 0}]
    assert tm.map_time(ops, 9.99) == pytest.approx(9.99)
    assert tm.map_time(ops, 10.0) == pytest.approx(10.5)      # 插入点上的时刻排在插入块之后(下一组首帧)
    assert tm.map_time(ops, 15.0) == pytest.approx(15.5)
    assert tm.map_time(ops, 21.0) == pytest.approx(20.5)      # 被删区间内折到区间输出起点
    assert tm.map_time(ops, 30.0) == pytest.approx(28.5)
    for t in (0.0, 9.5, 10.0, 15.0, 22.0, 30.0):
        assert tm.inverse_time(ops, tm.map_time(ops, t)) == pytest.approx(t)


def test_compose_layers():
    post = [{"src_t0": 10, "src_t1": 10, "out_len": 0.5}, {"src_t0": 20, "src_t1": 22, "out_len": 0}]
    # 第二层以第一层输出为基准:输出 15.5 = 源 15;输出 28.5 = 源 30
    pads = [{"src_t0": 15.5, "src_t1": 15.5, "out_len": 1.0}, {"src_t0": 28.5, "src_t1": 28.5, "out_len": 0.25}]
    total = tm.compose(post, pads)
    assert [o["src_t0"] for o in total] == [10.0, 15.0, 20.0, 30.0]
    assert tm.map_time(total, 15.0) == pytest.approx(16.5)
    assert tm.map_time(total, 30.0) == pytest.approx(29.75)
    assert tm.total_delta(total) == pytest.approx(-0.25)


def test_srt_remap_piecewise():
    ops = [{"src_t0": 2.0, "src_t1": 2.0, "out_len": 0.75}]
    text = "1\n00:00:01,000 --> 00:00:01,500\nA\n\n2\n00:00:03,000 --> 00:00:03,500\nB\n\n"
    out = tm.remap_srt_text(text, tm.map_fn(ops, 1.0))    # 另加 1 s 片头
    assert "00:00:02,000 --> 00:00:02,500" in out
    assert "00:00:04,750 --> 00:00:05,250" in out


def test_audio_plan_fade_marks_neighbours():
    ops = [{"src_t0": 4.0, "src_t1": 4.0, "out_len": 0.5, "audio": "fade"}]
    plan = tm.build_audio_plan(ops, 10.0)
    assert [s["kind"] for s in plan] == ["src", "pad", "src"]
    assert plan[0]["fade_out"] == pytest.approx(0.15) and plan[2]["fade_in"] == pytest.approx(0.15)


@pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg")
def test_remap_audio_lengths(tmp_path):
    src = tmp_path / "a.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=440:d=6", "-ac", "2", "-ar", "48000", str(src)], check=True)
    ops = [{"src_t0": 2.0, "src_t1": 2.0, "out_len": 0.5, "audio": "sustain"},
           {"src_t0": 3.0, "src_t1": 4.0, "out_len": 0.0},
           {"src_t0": 5.0, "src_t1": 5.0, "out_len": 0.25, "audio": "mute"}]
    res = tm.remap_audio(src, tmp_path / "b.wav", ops)
    assert res["out_duration"] == pytest.approx(6 + 0.5 - 1.0 + 0.25, abs=0.02)
    # 缓存:同源同表第二次不重算
    p, res2 = tm.remap_audio_cached(src, tmp_path / "c.wav", ops)
    p2, res3 = tm.remap_audio_cached(src, tmp_path / "c.wav", ops)
    assert p == p2 and res2 == res3


def test_stretch_op_maps_proportionally_and_plans_stretch_segment():
    """慢动作(2026-09-23):区间 [1,2) 拉成 2s → 区间内时刻按比例映射,声轨计划出 stretch 段,atempo 链式拆分。"""
    ops = tm.normalize_ops([{"src_t0": 1.0, "src_t1": 2.0, "out_len": 2.0, "audio": "stretch", "kind": "slow_motion"}])
    assert ops[0]["audio"] == "stretch" and tm.total_delta(ops) == pytest.approx(1.0)
    assert tm.map_time(ops, 1.5) == pytest.approx(2.0)          # 区间中点 → 输出 1.0 + 0.5×2
    assert tm.map_time(ops, 2.0) == pytest.approx(3.0) and tm.map_time(ops, 3.0) == pytest.approx(4.0)
    assert tm.inverse_time(ops, 2.0) == pytest.approx(1.5)
    plan = tm.build_audio_plan(ops, 4.0)
    assert [s["kind"] for s in plan] == ["src", "stretch", "src"] and plan[1]["len"] == pytest.approx(2.0)
    # 插入块(t0 == t1)不能 stretch → 回退 sustain
    assert tm.normalize_ops([{"src_t0": 1, "src_t1": 1, "out_len": 0.5, "audio": "stretch"}])[0]["audio"] == "sustain"
    assert tm.atempo_chain(0.25) == "atempo=0.5,atempo=0.500000" and tm.atempo_chain(0.5) == "atempo=0.500000"
    assert "变速" in tm.describe(ops)


@pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg")
def test_remap_audio_stretch_length(tmp_path):
    src = tmp_path / "a.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=440:d=4", "-ar", "48000", str(src)], check=True)
    ops = [{"src_t0": 1.0, "src_t1": 2.0, "out_len": 3.0, "audio": "stretch"}]        # ×3 慢动作
    res = tm.remap_audio(src, tmp_path / "b.wav", ops, src_dur=4.0)
    assert res["out_duration"] == pytest.approx(6.0, abs=0.05) and res["pads"] == 0


def test_transition_contract_accepts_pads():
    from check_generation_groups import check_transitions, transition_of, pad_of
    sl = {"budget_s": 100, "generation_groups": [
        {"group_id": "g1"},
        {"group_id": "g2", "transition_in": {"type": "hard_cut", "hold_s": 0.5, "freeze_s": 0.25, "hold_audio": "sustain", "reason": "beat"}},
        {"group_id": "g3", "transition_in": {"type": "dip_black", "duration_s": 0.5, "hold_s": 0.5, "hold_audio": "mute",
                                              "intent": "scene_change", "reason": "r"}}]}
    assert check_transitions(sl) == []
    assert pad_of(transition_of(sl["generation_groups"][1])) == (0.25, 0.5, "sustain")
    bad = {"budget_s": 100, "generation_groups": [
        {"group_id": "g1"},
        {"group_id": "g2", "transition_in": {"type": "dissolve", "duration_s": 0.5, "hold_s": 0.5, "reason": "x", "intent": "other"}},
        {"group_id": "g3", "transition_in": {"type": "hard_cut", "hold_s": 4.0}}]}
    errs = check_transitions(bad)
    assert any("只配" in e for e in errs) and any("∉ [0,3]" in e for e in errs) and any("须写 reason" in e for e in errs)
