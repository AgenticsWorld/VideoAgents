# -*- coding: utf-8 -*-
"""声桥 modules/sound_bridge.py(构建渲染器注入,离线)+ 设置规范化 + 与声画分离 carry=line 的窗口放宽。"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))
sys.path.insert(0, str(ROOT / "modules"))
sys.path.insert(0, str(ROOT))

import offscreen_lines as ol  # noqa: E402
import sound_bridge as sbm  # noqa: E402
import transition_design as td  # noqa: E402


def test_settings_sound_bridge_defaults_and_validation():
    st = td.normalize_settings({"mode": "cinematic"})
    assert st["sound_bridge_s"] == td.SOUND_BRIDGE_S and st["sound_bridge_carry"] == "bed"
    st = td.normalize_settings({"mode": "classic", "sound_bridge_s": "1.2", "sound_bridge_carry": "line"})
    assert st["sound_bridge_s"] == 1.2 and st["sound_bridge_carry"] == "line"
    with pytest.raises(ValueError):
        td.normalize_settings({"mode": "classic", "sound_bridge_s": 2.0})
    with pytest.raises(ValueError):
        td.normalize_settings({"mode": "classic", "sound_bridge_carry": "voice"})
    cm = td.normalize_settings({"mode": "custom", "custom_map": {"scene_change": "l_cut"}})["custom_map"]
    assert cm["scene_change"] == "l_cut"


def _rows():
    return [{"group_id": "grp001", "src": "assets/clips/ep01/grp001.mp4", "duration_s": 10.0, "cum_start_s": 0.0, "time_ops": []},
            {"group_id": "grp002", "src": "assets/clips/ep01/grp002.mp4", "duration_s": 8.0, "cum_start_s": 10.0, "time_ops": []},
            {"group_id": "grp003", "src": "assets/clips/ep01/grp003.mp4", "duration_s": 6.0, "cum_start_s": 18.0, "time_ops": []}]


def _bounds():
    return [{"from_group": "grp001", "to_group": "grp002", "freeze_s": 0, "hold_s": 0, "insert_s": 0, "total_s": 0, "audio": "sustain",
             "type": "hard_cut", "inserts": [], "sound_bridge": {"kind": "j", "s": 0.5, "carry": "bed"}},
            {"from_group": "grp002", "to_group": "grp003", "freeze_s": 0, "hold_s": 0, "insert_s": 0, "total_s": 0, "audio": "sustain",
             "type": "dissolve", "inserts": [], "sound_bridge": {"kind": "l", "s": 0.8, "carry": "line"}}]


def _proj(tmp_path: Path) -> Path:
    base = tmp_path / "p"
    (base / "assets" / "clips" / "ep01").mkdir(parents=True)
    for g in ("grp001", "grp002", "grp003"):
        (base / "assets" / "clips" / "ep01" / f"{g}.mp4").write_bytes(b"\x00" * 64)
    return base


def test_expected_build_check_and_sources_rows(tmp_path):
    base = _proj(tmp_path)
    rows, bounds = _rows(), _bounds()
    exp = sbm.expected(base, "ep01", rows, bounds)
    assert [(e["id"], e["kind"], e["carry"]) for e in exp] == [("grp001-grp002", "j", "bed"), ("grp002-grp003", "l", "line")]
    assert exp[0]["src_to"] == "assets/clips/ep01/grp002.mp4" and exp[0]["cum_start_s"] == 10.0
    calls = []

    def fake_render(src, dst, kind, s, *, src_duration=None, log=print):
        calls.append((src.name, kind, s))
        Path(dst).write_bytes(b"RIFF")
        return {"status": "removed", "model": "mdx", "duration_s": s}

    man = sbm.build(base, "ep01", rows=rows, bounds=bounds, renderer=fake_render)
    assert calls == [("grp002.mp4", "j", 0.5)]                 # L/line 不出文件
    by = {b["id"]: b for b in man["bridges"]}
    assert by["grp001-grp002"]["status"] == "built" and by["grp001-grp002"]["file"] == "grp001-grp002.j.wav"
    assert by["grp002-grp003"]["status"] == "line" and by["grp002-grp003"]["file"] is None
    assert sbm.fingerprint(man) and not sbm.is_stale(base, "ep01", man, rows, bounds)
    # 幂等
    calls.clear()
    man2 = sbm.build(base, "ep01", rows=rows, bounds=bounds, renderer=fake_render)
    assert calls == [] and man2["summary"]["rendered_now"] == 0
    # sources 行
    out = sbm.rows_for_sources(base, "ep01", bounds)
    assert out[0]["sound_bridge"]["status"] == "built" and out[0]["sound_bridge"]["file"].endswith("grp001-grp002.j.wav")
    assert out[1]["sound_bridge"]["status"] == "line" and out[1]["sound_bridge"]["duck_db"] == sbm.L_DUCK_DB
    res = sbm.check(base, "ep01", rows, bounds)
    assert res["checks"]["sound_bridge_built"] == "PASS"
    # 改参数 → stale / check FAIL
    bounds[0]["sound_bridge"]["s"] = 0.8
    assert sbm.is_stale(base, "ep01", man, rows, bounds)
    assert sbm.check(base, "ep01", rows, bounds)["checks"]["sound_bridge_built"] == "FAIL"
    assert sbm.expected(base, "ep01", rows, bounds)[0]["key"] != exp[0]["key"]


def test_fallback_render_status_and_failed_source(tmp_path):
    base = _proj(tmp_path)
    rows, bounds = _rows(), _bounds()
    (base / "assets" / "clips" / "ep01" / "grp002.mp4").unlink()

    def fake_render(src, dst, kind, s, *, src_duration=None, log=print):
        Path(dst).write_bytes(b"RIFF")
        return {"status": "fallback", "reason": "no onnxruntime", "duration_s": s}

    man = sbm.build(base, "ep01", rows=rows, bounds=bounds, renderer=fake_render)
    by = {b["id"]: b for b in man["bridges"]}
    assert by["grp001-grp002"]["status"] == "failed" and "取源文件缺失" in by["grp001-grp002"]["error"]
    (base / "assets" / "clips" / "ep01" / "grp002.mp4").write_bytes(b"\x00" * 64)
    man = sbm.build(base, "ep01", rows=rows, bounds=bounds, renderer=fake_render)
    by = {b["id"]: b for b in man["bridges"]}
    assert by["grp001-grp002"]["status"] == "fallback" and by["grp001-grp002"]["warning"]


def _sl_with_bridges(tmp_path: Path, j_line=True, l_line=True) -> Path:
    base = tmp_path / "ss"
    (base / "directing" / "ep01").mkdir(parents=True)
    (base / "settings.json").write_text(json.dumps({"output": {"sound_split": "auto"}}), encoding="utf-8")
    sl = {"shots": [
        {"shot_id": "s1", "duration_s": 4.0, "dialogue_lines": [{"speaker": "CHAR-0001", "text": "好。", "est_duration_s": 0.8}]},
        {"shot_id": "s2", "duration_s": 3.0, "dialogue_lines": [{"speaker": "CHAR-0002", "text": "别走。", "est_duration_s": 1.0, "placement": "os",
                                                                "heard_in": ["s2"], "placement_source": "script",
                                                                "placement_reason": {"trigger": "S-exit", "evidence": "x"}}]},
        {"shot_id": "s3", "duration_s": 5.0, "dialogue_lines": [{"speaker": "CHAR-0001", "text": "是谁在说话。", "est_duration_s": 1.5, "placement": "os",
                                                                "heard_in": ["s3"], "placement_source": "script",
                                                                "placement_reason": {"trigger": "S-door", "evidence": "x"}}]},
        {"shot_id": "s4", "duration_s": 4.0, "dialogue_lines": []}],
        "generation_groups": [
            {"group_id": "grp001", "shots": ["s1", "s2"], "audio_plan": "dialogue"},
            {"group_id": "grp002", "shots": ["s3", "s4"], "audio_plan": "voice_over",
             "transition_in": {"type": "hard_cut", "reason": "r", "sound_bridge": {"kind": "j" if j_line else "l", "s": 0.6, "carry": "line"}}}],
        "narration_anchors": []}
    if l_line:
        sl["generation_groups"][1]["transition_in"]["sound_bridge"] = {"kind": "l", "s": 0.6, "carry": "line"}
    (base / "directing" / "ep01" / "shot_list.json").write_text(json.dumps(sl, ensure_ascii=False), encoding="utf-8")
    return base


def test_offscreen_allowance_for_j_line(tmp_path):
    base = _sl_with_bridges(tmp_path, j_line=True, l_line=False)
    allow = ol.bridge_allowances(json.loads((base / "directing" / "ep01" / "shot_list.json").read_text()))
    assert allow["grp002"]["lead_s"] == 0.6 and allow["grp001"]["tail_s"] == 0.0
    recs = {r["shot_id"]: r for r in ol.collect(base, "ep01")}
    r = recs["s3"]
    assert r["window"]["bridge_lead_s"] == 0.6 and r["window"]["start_s"] == pytest.approx(-0.6)
    assert r["offset_s"] == pytest.approx(-0.6) and r["t_in_group_s"] == pytest.approx(-0.6)   # 缺省就从切点前 s 秒起
    assert ol.validate(base, "ep01") == []
    # 普通边界上写负偏移 → placement_valid
    ol.set_line(base, "ep01", "s2", 0, offset_s=-0.3)
    msgs = ol.validate(base, "ep01")
    assert any("s2" in m and "placement_valid" in m and "为负" in m for m in msgs)


def test_offscreen_allowance_for_l_line(tmp_path):
    base = _sl_with_bridges(tmp_path, j_line=False, l_line=True)
    allow = ol.bridge_allowances(json.loads((base / "directing" / "ep01" / "shot_list.json").read_text()))
    assert allow["grp001"]["tail_s"] == 0.6 and allow["grp002"]["lead_s"] == 0.0
    recs = {r["shot_id"]: r for r in ol.collect(base, "ep01")}
    r = recs["s2"]
    assert r["window"]["bridge_tail_s"] == 0.6 and r["window"]["end_s"] == pytest.approx(7.0 + 0.6)
    assert recs["s3"]["window"]["start_s"] == 0.0 and recs["s3"]["offset_s"] == ol.DEFAULT_OFFSET_S
