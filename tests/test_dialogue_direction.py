"""台词演法(modules/dialogue_direction.py):目标时长计算与收口、写入校验、台词改动后作废、机检,以及对白语音库 / 合成层按演法走。"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules import dialogue_direction as dd  # noqa: E402
from modules import dialogue_tts as dt  # noqa: E402
from modules import genmedia as g  # noqa: E402

LINE = "畜生!你打死三太子,事尚未定,又惹这等大祸!"


def make_project(tmp_path: Path, shot_s: float = 4.95) -> Path:
    base = tmp_path / "proj"
    for d in (base / "directing" / "ep01", base / "bible" / "characters" / "CHAR-0001", base / "story" / "episodes" / "ep01",
              base / "assets" / "audio" / "voice" / "refs"):
        d.mkdir(parents=True)
    (base / "settings.json").write_text(json.dumps({"output": {"dialogue_tts": True}}))
    (base / "bible" / "characters" / "index.json").write_text(json.dumps(
        {"characters": [{"id": "CHAR-0001", "canonical_name": "李靖"}]}, ensure_ascii=False))
    (base / "bible" / "characters" / "CHAR-0001" / "voice.json").write_text(json.dumps({
        "canonical_name": "李靖", "gender": "男", "pitch": "中低。说话基频约 98-132 Hz",
        "timbre": "中低沉稳的成年男声。亮度中偏低(4/10),句尾一律下压。"}, ensure_ascii=False))
    (base / "story" / "episodes" / "ep01" / "script_breakdown.json").write_text(json.dumps({"scenes": [{"lines": [
        {"speaker": "CHAR-0001", "text": LINE, "emotion": "暴怒", "paren": None}]}]}, ensure_ascii=False))
    sl = {"shots": [
        {"shot_id": "sh001", "duration_s": shot_s, "scene_id": "SCN-0001", "content_brief": "李靖暴怒",
         "poses": {"CHAR-0001": {"pose": "stand", "action": "手按案上呵斥"}},
         "dialogue_lines": [{"speaker": "CHAR-0001", "text": LINE, "est_duration_s": 4.91}]},
        {"shot_id": "sh002", "duration_s": 3.0, "dialogue_lines": [{"speaker": "CHAR-0001", "text": "你自去回话!", "est_duration_s": 1.2}]}],
        "generation_groups": [{"group_id": "grp001", "shots": ["sh001", "sh002"]}]}
    (base / "directing" / "ep01" / "shot_list.json").write_text(json.dumps(sl, ensure_ascii=False, indent=2) + "\n")
    (base / "assets" / "audio" / "voice" / "refs" / "CHAR-0001_voiceprint.mp3").write_bytes(b"vp")
    return base


def shot_line(base: Path, shot: str = "sh001") -> dict:
    sl = json.loads((base / "directing" / "ep01" / "shot_list.json").read_text())
    return next(s for s in sl["shots"] if s["shot_id"] == shot)["dialogue_lines"][0]


def test_paced_target_counts_chars_and_pauses():
    assert dd.effective_chars(LINE) == 18 and dd.inner_pauses(LINE) == 3
    assert dd.paced_target(LINE, "fast") == 5.5            # 0.7 起止余量 + 18/5.0 + 3×0.4(时间尺 modules/time_cost.py,2026-10-03)
    assert dd.paced_target(LINE, "slow") > dd.paced_target(LINE, "medium") > dd.paced_target(LINE, "fast")


def test_resolve_target_clamps_to_shot_and_rate():
    t, notes = dd.resolve_target(LINE, "slow", None, limit_s=4.85)
    assert t == 4.85 and "超出本镜可用" in notes[0]
    t, notes = dd.resolve_target(LINE, "", 1.0, limit_s=4.85)      # 1 秒念 18 个字不现实
    assert t == dd.bounds(LINE)[0] and "太短" in notes[0]
    t, notes = dd.resolve_target(LINE, "fast", None, limit_s=1.5)  # 镜头装不下:按最快出声并报装不下
    assert t == dd.bounds(LINE)[0] and "装不下" in notes[0]


def test_context_carries_script_emotion_and_action(tmp_path):
    rows = dd.context(make_project(tmp_path), "ep01")
    r = rows[0]
    assert (r["emotion"], r["action"], r["status"], r["limit_s"]) == ("暴怒", "手按案上呵斥", "missing", 4.85)
    assert r["pace_seconds"]["fast"] == 5.5 and rows[1]["prev_line"]["text"] == LINE


def test_apply_writes_delivery_and_only_touches_dialogue_lines(tmp_path):
    base = make_project(tmp_path)
    before = json.loads((base / "directing" / "ep01" / "shot_list.json").read_text())
    res = dd.apply(base, "ep01", [{"shot_id": "sh001", "direction": "状态暴怒,“畜生”两个字炸开", "scene": "夜里,厅上,对儿子。", "pace": "fast"}])
    assert res["written"] == 1 and res["backfilled_emotion"] == 1 and not res["errors"]
    ln = shot_line(base)
    assert ln["emotion"] == "暴怒" and ln["delivery"]["target_s"] == 4.85 and ln["delivery"]["text_sha"] == dd.text_sha(LINE)   # 5.5 被本镜可用 4.85 收口
    after = json.loads((base / "directing" / "ep01" / "shot_list.json").read_text())
    assert "emotion" not in after["shots"][1]["dialogue_lines"][0]          # 没写演法的句子不补情绪(免得无端过期)
    for a, b in zip(before["shots"], after["shots"]):
        assert {k: v for k, v in a.items() if k != "dialogue_lines"} == {k: v for k, v in b.items() if k != "dialogue_lines"}
    assert dd.check(base, "ep01")["fails"] == ["sh002/l00 李靖:没有演法"]


def test_apply_rejects_quoted_sentences_and_bad_pace(tmp_path):
    base = make_project(tmp_path)
    res = dd.apply(base, "ep01", [{"shot_id": "sh001", "direction": "状态暴怒", "scene": "儿子刚说“箭是我射的你能怎样”。"}])
    assert res["written"] == 0 and "引号" in res["errors"][0]
    assert dd.apply(base, "ep01", [{"shot_id": "sh001", "direction": "状态暴怒", "pace": "quick"}])["errors"]
    assert dd.apply(base, "ep01", [{"shot_id": "sh009", "direction": "状态暴怒"}])["errors"]
    assert "delivery" not in shot_line(base)                               # 任何一条不合法整批不写


def test_direction_goes_stale_when_text_changes(tmp_path):
    base = make_project(tmp_path)
    dd.apply(base, "ep01", [{"shot_id": "sh001", "direction": "状态暴怒", "pace": "fast"}])
    p = base / "directing" / "ep01" / "shot_list.json"
    sl = json.loads(p.read_text())
    sl["shots"][0]["dialogue_lines"][0]["text"] = "畜生!又惹大祸!"
    p.write_text(json.dumps(sl, ensure_ascii=False))
    assert dd.line_delivery(shot_line(base)) is None
    assert any("演法已作废" in f for f in dd.check(base, "ep01")["fails"])


def test_library_key_and_synthesis_follow_direction(tmp_path, monkeypatch):
    monkeypatch.setattr(dt, "tts_channel", lambda: ("volcengine", "seed-audio-1.0"))
    base = make_project(tmp_path)
    keys0 = {e["shot_id"]: e["key"] for e in dt.plan(base, "ep01")["lines"]}
    dd.apply(base, "ep01", [{"shot_id": "sh001", "direction": "状态暴怒", "scene": "夜里,厅上。", "pace": "fast"}])
    lines = {e["shot_id"]: e for e in dt.plan(base, "ep01")["lines"]}
    assert lines["sh001"]["key"] != keys0["sh001"] and lines["sh002"]["key"] == keys0["sh002"]   # 没演法的句子 key 不变
    assert (lines["sh001"]["direction"], lines["sh001"]["target_s"]) == ("状态暴怒", 4.85)
    calls = {}
    monkeypatch.setattr("modules.genmedia.generate_tts", lambda *a: calls.setdefault(a[1].rsplit("/", 1)[-1], a))
    dt._default_tts(base)(dict(lines["sh001"], tts_voice=""), base / "x_sh001.mp3")
    assert calls["x_sh001.mp3"][8:11] == ("状态暴怒", "夜里,厅上。", 4.85)


def test_direct_prompt_uses_brief_timbre_and_target(tmp_path):
    base = make_project(tmp_path)
    name, brief = g._seedaudio_char_brief("CHAR-0001", "", str(base), "x.mp3")
    assert (name, brief) == ("李靖", "中低沉稳的成年男声")                   # 只取音色头一句;整段声学规格不进提示词
    prompt = g._seedaudio_direct_prompt(LINE, "状态暴怒,放开嗓子喝骂。", "夜里,厅上。", 4.2, name, brief, True)
    assert "只取音色" in prompt and "场景:夜里,厅上。" in prompt and "全长约 4.2 秒" in prompt
    assert "状态暴怒,放开嗓子喝骂,说道:“" + LINE + "”" in prompt and "Hz" not in prompt
    no_ref = g._seedaudio_direct_prompt(LINE, "状态暴怒", "", None, name, brief, False)
    assert "@音频1" not in no_ref and "全长约" not in no_ref and "场景:" not in no_ref
