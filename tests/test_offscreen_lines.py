# -*- coding: utf-8 -*-
"""声画分离 modules/offscreen_lines.py:窗口 / 机检 / 写回 / 合成流程(离线:TTS、ffmpeg、探测全部注入)。"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "modules"))
sys.path.insert(0, str(ROOT))

import offscreen_lines as ol  # noqa: E402


def _project(tmp_path: Path, mode="auto", narration=None) -> Path:
    base = tmp_path / "proj"
    (base / "directing" / "ep01").mkdir(parents=True)
    (base / "bible" / "characters").mkdir(parents=True)
    (base / "settings.json").write_text(json.dumps({"output": {"sound_split": mode}}), encoding="utf-8")
    (base / "bible" / "characters" / "index.json").write_text(json.dumps({"characters": [
        {"id": "CHAR-0001", "canonical_name": "林昭"}, {"id": "CHAR-0002", "canonical_name": "老执事"}]}), encoding="utf-8")
    sl = {
        "shots": [
            {"shot_id": "sh001", "duration_s": 3.0, "characters": ["CHAR-0001"], "is_dialogue": True,
             "dialogue_lines": [{"speaker": "CHAR-0001", "text": "你来了。", "est_duration_s": 1.2}]},
            {"shot_id": "sh002", "duration_s": 4.0, "characters": ["CHAR-0002"], "is_dialogue": True,
             "dialogue_lines": [
                 {"speaker": "CHAR-0001", "text": "师父说过,经书不可外传。", "est_duration_s": 3.0, "placement": "os",
                  "heard_in": ["sh002", "sh003"], "source_fx": "door", "offset_s": 0.5,
                  "placement_reason": {"trigger": "D1", "evidence": "听者反应"}, "placement_source": "directing"},
                 {"speaker": "CHAR-0002", "text": "哦。", "est_duration_s": 0.8}]},
            {"shot_id": "sh003", "duration_s": 3.0, "characters": ["CHAR-0002"], "is_dialogue": False, "dialogue_lines": []},
            {"shot_id": "sh004", "duration_s": 5.0, "characters": [], "is_dialogue": False,
             "dialogue_lines": [{"speaker": "CHAR-0002", "text": "那一夜我终究没有睡着。", "est_duration_s": 2.6, "placement": "vo",
                                 "placement_source": "script", "placement_reason": {"trigger": "S-inner", "evidence": "ch03 心理描写"}}]},
        ],
        "generation_groups": [
            {"group_id": "grp001", "shots": ["sh001", "sh002", "sh003"], "audio_plan": "dialogue", "total_duration_s": 10},
            {"group_id": "grp002", "shots": ["sh004"], "audio_plan": "voice_over", "total_duration_s": 5},
        ],
        "narration_anchors": narration or [],
    }
    (base / "directing" / "ep01" / "shot_list.json").write_text(json.dumps(sl, ensure_ascii=False, indent=2), encoding="utf-8")
    return base


def test_placement_helpers_default_on():
    assert ol.placement({"text": "a"}) == "on"
    assert ol.placement({"text": "a", "placement": "OS"}) == "os"
    assert ol.placement({"text": "a", "placement": "weird"}) == "on"
    assert ol.heard_in({"placement": "os"}, "sh001") == ["sh001"]
    assert ol.heard_in({"placement": "os", "heard_in": ["sh002", "sh002", "sh003"]}, "sh001") == ["sh002", "sh003"]
    assert ol.source_fx({"placement": "vo"}) == "inner" and ol.source_fx({"placement": "os"}) == "plain"
    assert ol.source_fx({"placement": "on", "source_fx": "phone"}) == ""


def test_collect_windows_and_sequential_placement(tmp_path):
    base = _project(tmp_path)
    recs = ol.collect(base, "ep01")
    assert [(r["shot_id"], r["idx"], r["placement"]) for r in recs] == [("sh002", 0, "os"), ("sh004", 0, "vo")]
    r = recs[0]
    assert r["group_id"] == "grp001" and r["heard_in"] == ["sh002", "sh003"] and r["source_fx"] == "door"
    # 窗口 = sh002+sh003 = [3,10];起点 3+0.5;可用 = 10 − 3.5 − 画内 0.8
    assert r["window"]["start_s"] == 3.0 and r["window"]["end_s"] == 10.0
    assert r["t_in_group_s"] == pytest.approx(3.5)
    assert r["window"]["on_s"] == pytest.approx(0.8)
    assert r["window"]["available_s"] == pytest.approx(10.0 - 3.5 - 0.8, abs=0.01)
    v = recs[1]
    assert v["source_fx"] == "inner" and v["offset_s"] == ol.DEFAULT_OFFSET_S and v["t_in_group_s"] == pytest.approx(0.4)


def test_expected_audio_plan_four_values():
    shots = {"a": {"dialogue_lines": [{"text": "x", "placement": "os"}]}, "b": {"dialogue_lines": [{"text": "y"}]}, "c": {"dialogue_lines": []}}
    assert ol.expected_audio_plan({"group_id": "g", "shots": ["a", "b"]}, shots) == "dialogue"
    assert ol.expected_audio_plan({"group_id": "g", "shots": ["a"]}, shots) == "voice_over"
    assert ol.expected_audio_plan({"group_id": "g", "shots": ["c"]}, shots) == "ambient_only"
    assert ol.expected_audio_plan({"group_id": "g", "shots": ["c"]}, shots, [{"anchor_group": "g"}]) == "voice_over"
    assert ol.audio_plan_ok("narration_over", "voice_over") and ol.audio_plan_ok("voice_over", "voice_over")
    assert not ol.audio_plan_ok("dialogue", "voice_over")


def test_validate_passes_clean_project(tmp_path):
    base = _project(tmp_path)
    assert ol.validate(base, "ep01") == []


def test_validate_mode_off_and_script_only(tmp_path):
    base = _project(tmp_path, mode="off")
    msgs = ol.validate(base, "ep01")
    assert len(msgs) == 2 and all("placement_valid" in m and "已关闭" in m for m in msgs)
    base2 = _project(tmp_path / "b", mode="script_only")
    msgs = ol.validate(base2, "ep01")
    assert any("placement_valid" in m and "directing" in m for m in msgs)
    assert not any("sh004" in m for m in msgs)          # 剧本层标记在 script_only 下合法


def test_validate_reason_and_cast(tmp_path):
    base = _project(tmp_path)
    p = base / "directing" / "ep01" / "shot_list.json"
    sl = json.loads(p.read_text(encoding="utf-8"))
    ln = sl["shots"][1]["dialogue_lines"][0]
    ln.pop("placement_reason")
    ln["speaker"] = "CHAR-0009"
    p.write_text(json.dumps(sl, ensure_ascii=False), encoding="utf-8")
    msgs = ol.validate(base, "ep01")
    assert any("placement_reason_valid" in m for m in msgs)
    assert any("placement_speaker_is_cast" in m and "未在 bible index 登记" in m for m in msgs)


def test_validate_window_overflow_and_narration_overlap(tmp_path):
    base = _project(tmp_path, narration=[{"narration_id": "N-01", "anchor_shots": ["sh003"], "anchor_group": "grp001", "est_duration_s": 5.0}])
    msgs = ol.validate(base, "ep01")
    assert any("post_voice_no_overlap" in m for m in msgs)
    p = base / "directing" / "ep01" / "shot_list.json"
    sl = json.loads(p.read_text(encoding="utf-8"))
    sl["narration_anchors"] = []
    sl["shots"][1]["dialogue_lines"][0]["est_duration_s"] = 9.0
    p.write_text(json.dumps(sl, ensure_ascii=False), encoding="utf-8")
    msgs = ol.validate(base, "ep01")
    assert any("offscreen_fit" in m for m in msgs) and not any("post_voice_no_overlap" in m for m in msgs)


def test_check_prompts_detects_brace_and_audio_ref(tmp_path):
    base = _project(tmp_path)
    pdir = base / "assets" / "prompts" / "ep01"
    pdir.mkdir(parents=True)
    (pdir / "grp001.json").write_text(json.dumps({
        "video_prompt": "Shot 1: 林昭 {你来了。} Shot 2: 老执事 {师父说过,经书不可外传。}",
        "audio_refs": ["assets/audio/voice/refs/CHAR-0001_voiceprint.mp3"]}, ensure_ascii=False), encoding="utf-8")
    msgs = ol.check_prompts(base, "ep01")
    assert len(msgs) == 1 and "进了 prompt" in msgs[0]           # CHAR-0001 本组也有画内句 → audio_ref 合法
    (pdir / "grp002.json").write_text(json.dumps({"video_prompt": "Shot 1: silent", "audio_refs": ["x/CHAR-0002_voiceprint.mp3"]}), encoding="utf-8")
    msgs = ol.check_prompts(base, "ep01")
    assert any("只有画外句却挂了 audio_refs" in m for m in msgs)


def test_set_line_roundtrip_and_validation(tmp_path):
    base = _project(tmp_path)
    ln = ol.set_line(base, "ep01", "sh001", 0, placement="os", heard_in=["sh002"], source_fx="phone", offset_s=1.0, reason="手动")
    assert ln["placement"] == "os" and ln["heard_in"] == ["sh002"] and ln["placement_reason"]["trigger"] == "U"
    assert ln["placement_source"] == "user"
    with pytest.raises(ValueError):
        ol.set_line(base, "ep01", "sh001", 0, heard_in=["sh004"])      # 跨组
    with pytest.raises(ValueError):
        ol.set_line(base, "ep01", "sh001", 0, source_fx="megaphone")
    with pytest.raises(ValueError):
        ol.set_line(base, "ep01", "sh001", 5, placement="os")
    back = ol.set_line(base, "ep01", "sh001", 0, placement="on")
    assert "placement" not in back and "heard_in" not in back and "source_fx" not in back
    sl = json.loads((base / "directing" / "ep01" / "shot_list.json").read_text(encoding="utf-8"))
    assert "placement" not in sl["shots"][0]["dialogue_lines"][0]


def test_current_fingerprint_changes_with_placement(tmp_path):
    base = _project(tmp_path)
    a = ol.current_fingerprint(base, "ep01")
    ol.set_line(base, "ep01", "sh002", 0, source_fx="phone")
    b = ol.current_fingerprint(base, "ep01")
    assert a and b and a != b
    ol.set_line(base, "ep01", "sh002", 0, placement="on")
    ol.set_line(base, "ep01", "sh004", 0, placement="on")
    assert ol.current_fingerprint(base, "ep01") is None


def test_synth_offline_flow(tmp_path, monkeypatch):
    base = _project(tmp_path)
    (base / "assets" / "audio" / "voice").mkdir(parents=True)
    (base / "assets" / "audio" / "voice" / "casting.json").write_text(json.dumps({"castings": [
        {"character_id": "CHAR-0001", "variant": "default", "tts_model": "m", "tts_voice": "v1"},
        {"character_id": "CHAR-0002", "variant": "default", "tts_model": "m", "tts_voice": "v2"}]}), encoding="utf-8")
    # offscreen_lines 优先 `from modules import dialogue_tts`,与顶层 `dialogue_tts` 是两个模块对象:两处都打补丁
    import modules.dialogue_tts as dt_pkg
    dt_top = sys.modules.get("dialogue_tts")          # 其它测试可能先把 code/ 入 sys.path,此名可能指向 CLI 脚本
    for dt in {id(m): m for m in (dt_pkg, dt_top) if m is not None and hasattr(m, "tts_channel")}.values():
        monkeypatch.setattr(dt, "tts_channel", lambda: ("minimax", "speech-2.8-hd"))
        monkeypatch.setattr(dt, "trim_file", lambda raw, dst, max_pause=0.0, probe=None: (dst.write_bytes(raw.read_bytes()), {"lead_s": 0, "tail_s": 0, "pause_s": 0, "params": {}})[1])
        monkeypatch.setattr(dt, "default_max_tempo", lambda base: 1.0)        # 关闭节奏贴合,直接判 overflow
    monkeypatch.setattr(ol, "apply_fx", lambda src, dst, fx: (dst.write_bytes(src.read_bytes()), ["stub"])[1])
    durs = {"sh002_l00_CHAR-0001.mp3": 2.0, "sh004_l00_CHAR-0002.mp3": 9.0}       # 第二句故意超窗(5s 镜 − 0.4 偏移)
    calls = []

    def fake_tts(entry, out):
        calls.append(entry["text"])
        Path(out).write_bytes(b"x" * 10)

    def fake_probe(p):
        p = Path(p)
        for k, v in durs.items():
            if p.name.startswith(k[:-4]):
                return v
        return 1.0

    man = ol.synth(base, "ep01", tts=fake_tts, probe=fake_probe, log=lambda m: None)
    assert len(calls) == 2 and man["summary"]["ok"] == 1 and man["summary"]["overflow"] == 1
    by = {(e["shot_id"], e["idx"]): e for e in man["lines"]}
    assert by[("sh002", 0)]["status"] == "ok" and by[("sh002", 0)]["t_in_group_s"] == pytest.approx(3.5)
    assert by[("sh004", 0)]["status"] == "overflow" and by[("sh004", 0)]["overflow_s"] > 0
    assert man["source_fingerprint"] == ol.current_fingerprint(base, "ep01")
    assert ol.fingerprint(man) and not ol.is_stale(base, "ep01", man)
    # 第二次同步:key 不变不重合成
    calls.clear()
    man2 = ol.synth(base, "ep01", tts=fake_tts, probe=fake_probe, log=lambda m: None)
    assert calls == [] and man2["summary"]["synthesized"] == 0
    # 混音行:绝对时刻 = 组起点 + 组内时刻;time_ops 删段内的句子 dropped
    rows = [{"group_id": "grp001", "cum_start_s": 100.0, "time_ops": []}, {"group_id": "grp002", "cum_start_s": 110.0,
             "time_ops": [{"src_t0": 0.0, "src_t1": 5.0, "out_len": 0.0}]}]
    mr = ol.mix_rows(base, "ep01", rows)
    assert mr[0]["t0"] == pytest.approx(103.5) and mr[0]["file"].endswith(".fx.wav")
    assert mr[1].get("dropped") and mr[1]["t0"] is None
    # 改了声源效果 → 台账落后
    ol.set_line(base, "ep01", "sh002", 0, source_fx="phone")
    assert ol.is_stale(base, "ep01")
    res = ol.check(base, "ep01")
    assert res["checks"]["offscreen_synced"] == "FAIL"


def test_check_skipped_when_no_lines(tmp_path):
    base = _project(tmp_path, mode="off")
    for sid, i in (("sh002", 0), ("sh004", 0)):
        p = base / "directing" / "ep01" / "shot_list.json"
        sl = json.loads(p.read_text(encoding="utf-8"))
        for s in sl["shots"]:
            if s["shot_id"] == sid:
                s["dialogue_lines"][i].pop("placement")
        p.write_text(json.dumps(sl, ensure_ascii=False), encoding="utf-8")
    res = ol.check(base, "ep01")
    assert res["count"] == 0 and all(v == "skipped: sound_split off" for v in res["checks"].values())
