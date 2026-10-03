# -*- coding: utf-8 -*-
"""后期配音 dub_group.py 纯函数 + mix_manifest 的 dub 指纹接线(离线,不调 ffmpeg / 模型 / TTS)。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))
sys.path.insert(0, str(ROOT / "modules"))

import dub_group as dg  # noqa: E402
import mix_manifest as mb  # noqa: E402
import voice_activity as va  # noqa: E402


def _lines(*pairs):
    return [{"shot_id": s, "text": t, "speaker": "CHAR-0001"} for s, t in pairs]


def test_assign_by_shots_keeps_lines_inside_their_shot_window():
    runs = [[0.5, 1.5], [3.0, 4.0], [7.0, 9.0]]           # 第二段其实是音效漏网(sh02 没台词)
    lines = _lines(("sh01", "你好"), ("sh03", "我们走吧"))
    windows = {"sh01": (0.0, 2.0), "sh02": (2.0, 6.0), "sh03": (6.0, 10.0)}
    segs, notes = dg.assign_by_shots(runs, lines, windows, pad=0.5, total=10.0)
    assert segs == [[0.5, 1.5], [7.0, 9.0]]
    assert notes == []


def test_assign_by_shots_splits_within_shot_by_text_weight():
    runs = [[1.0, 4.0]]
    lines = _lines(("sh01", "一"), ("sh01", "二二二"))
    segs, _ = dg.assign_by_shots(runs, lines, {"sh01": (0.0, 5.0)}, pad=0.0, total=5.0)
    assert segs[0][0] == 1.0 and segs[1][1] == 4.0
    assert segs[0][1] == pytest.approx(1.75, abs=1e-3)      # 1 + 3 × 1/4


def test_assign_by_shots_falls_back_to_global_when_window_empty():
    runs = [[0.5, 1.5], [2.0, 3.0]]
    lines = _lines(("sh01", "a"), ("sh09", "b"))
    windows = {"sh01": (0.0, 2.0), "sh09": (8.0, 10.0)}
    segs, notes = dg.assign_by_shots(runs, lines, windows, pad=0.5, total=10.0)
    assert segs == [[0.5, 1.5], [2.0, 3.0]]
    assert any("sh09" in n for n in notes) and any("全局" in n for n in notes)


def test_assign_by_shots_requires_runs():
    with pytest.raises(ValueError):
        dg.assign_by_shots([], _lines(("sh01", "a")), {"sh01": (0, 1)}, 0.5, 5.0)


def test_fit_room_and_overflow_onset_policy():
    segs = [[1.0, 2.0], [4.0, 5.0]]
    assert dg.fit_room(segs, 0, 10.0) == 3.0        # 到下一句起点
    assert dg.fit_room(segs, 1, 10.0) == 6.0        # 到 clip 末尾
    assert not dg.fit_overflow(2.9, 3.0)            # 比开口时段长但没撞下一句 = 不 overflow
    assert dg.fit_overflow(3.2, 3.0)


def test_shot_windows_prefers_boundary_map_then_cumulative():
    group = {"shots": ["sh01", "sh02", "sh03"]}
    shots = {"sh01": {"duration_s": 2}, "sh02": {"duration_s": 3}, "sh03": {"duration_s": 1}}
    meta = {"boundary_map": [{"shot_id": "sh02", "start_s": 1.8, "end_s": 5.2}]}
    w = dg.shot_windows(group, shots, meta)
    assert w["sh01"] == (0.0, 2.0) and w["sh02"] == (1.8, 5.2) and w["sh03"] == (5.0, 6.0)


def test_runs_from_mask_merges_and_filters():
    mask = [False, True, True, False, True, True, True, True, True, True, False]
    runs = va.runs_from_mask(mask, hop=0.05, merge_gap=0.04, min_run=0.12)
    assert runs == [[0.2, 0.5]]                     # 0.05–0.15(0.10s)太短被剔,0.2–0.5 保留
    assert va.runs_from_mask(mask, hop=0.05, merge_gap=0.06, min_run=0.12) == [[0.05, 0.5]]   # 间隙 0.05 ≤ 0.06 合并


def test_dub_fingerprint_changes_with_segments_and_vocal_removal():
    man = {"dubbed_at": "2026-10-03T10:00:00", "vocal_removal": {"status": "removed"},
           "lines": [{"line": 0, "segment": {"start": 1.0}, "fit_duration_s": 2.0}]}
    a = mb.dub_fingerprint(man)
    assert a and mb.dub_fingerprint(None) is None
    man2 = {**man, "lines": [{"line": 0, "segment": {"start": 1.2}, "fit_duration_s": 2.0}]}
    man3 = {**man, "vocal_removal": {"status": "fallback_duck"}}
    assert a != mb.dub_fingerprint(man2) != mb.dub_fingerprint(man3)


def test_dub_predates_version_walks_base_chain():
    plan = {"current": {"g": 2}, "versions": {"g": [
        {"v": 1, "base_v": 0, "created_at": "2026-10-01 09:00:00"},
        {"v": 2, "base_v": 1, "created_at": "2026-10-03 12:00:00"}]}}
    man = {"dubbed_at": "2026-10-03T10:00:00"}
    assert mb.dub_predates_version(plan, "g", man)              # v2 建于配音后,但它的根 v1 早于配音
    plan["versions"]["g"][0]["created_at"] = "2026-10-03 11:00:00"
    assert not mb.dub_predates_version(plan, "g", man)
    assert not mb.dub_predates_version({"current": {"g": 0}, "versions": {}}, "g", man)
    assert not mb.dub_predates_version(plan, "g", None)


def test_check_row_fails_on_dub_changed_or_stale():
    base = {"status": mb.STATUS_CURRENT, "boundary_status": mb.BND_NONE, "detail": "x"}
    assert mb.check_row(base)[0] == "PASS"
    assert mb.check_row({**base, "dub_changed": ["g"]})[0] == "FAIL"
    assert mb.check_row({**base, "dub_stale_versions": ["g"]})[0] == "FAIL"


# ---------------------------------------------------------------- 声画分离(2026-10-03):画外 / V.O. 句不进组配音

def _stub_offscreen(monkeypatch):
    import types
    try:
        from modules import offscreen_lines  # noqa: F401
        return
    except ImportError:
        pass
    m = types.ModuleType("offscreen_lines")
    m.placement = lambda ln: (str(ln.get("placement") or "on").lower() if str(ln.get("placement") or "on").lower() in ("on", "os", "vo") else "on")
    monkeypatch.setitem(sys.modules, "offscreen_lines", m)
    monkeypatch.setitem(sys.modules, "modules.offscreen_lines", m)


def test_load_group_excludes_offscreen_but_keeps_idx(tmp_path, monkeypatch):
    _stub_offscreen(monkeypatch)
    import json
    proj = tmp_path / "p"
    (proj / "directing" / "ep01").mkdir(parents=True)
    sl = {"shots": [
        {"shot_id": "sh001", "duration_s": 4, "dialogue_lines": [
            {"speaker": "CHAR-0001", "text": "甲"},
            {"speaker": "CHAR-0002", "text": "乙(画外)", "placement": "os", "heard_in": ["sh002"]},
            {"speaker": "CHAR-0001", "text": "丙"}]},
        {"shot_id": "sh002", "duration_s": 3, "dialogue_lines": [
            {"speaker": "CHAR-0002", "text": "丁(心声)", "placement": "vo"}]}],
        "generation_groups": [{"group_id": "grp001", "shots": ["sh001", "sh002"], "audio_plan": "dialogue"}]}
    (proj / "directing" / "ep01" / "shot_list.json").write_text(json.dumps(sl, ensure_ascii=False))
    g, lines, shots = dg.load_group(proj, "ep01", "grp001")
    # os/vo 句被剔除,但 idx 仍按镜内全部有台词句累加((shot_id, idx) 与对白语音库口径一致)
    assert [(l["shot_id"], l["idx"], l["text"]) for l in lines] == [("sh001", 0, "甲"), ("sh001", 2, "丙")]
    assert all(l["placement"] == "on" for l in lines)
    _, all_lines, _ = dg.load_group(proj, "ep01", "grp001", keep_offscreen=True)
    assert [(l["idx"], l["placement"]) for l in all_lines] == [(0, "on"), (1, "os"), (2, "on"), (0, "vo")]
