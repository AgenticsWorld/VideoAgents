"""说话人嗓音形态判定(modules/voice_variants.py):样本名带下划线的形态、章节范围、唯一已登记形态、未登记拦截。"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules import dialogue_tts as dt  # noqa: E402
from modules import voice_variants as vv  # noqa: E402

CH = "CHAR-0007"


def make_project(tmp_path: Path, *, samples=("pre_lotus_child",), casting=("pre_lotus_child",), chapters=("ch013",),
                 prompt_refs=None, card=True) -> Path:
    base = tmp_path / "proj"
    refs = base / "assets" / "audio" / "voice" / "refs"
    for d in (base / "directing" / "ep07", base / "bible" / "characters" / CH, refs, base / "assets" / "prompts" / "ep07",
              base / "story"):
        d.mkdir(parents=True)
    (base / "settings.json").write_text(json.dumps({"output": {"dialogue_tts": True}}))
    (base / "bible" / "characters" / "index.json").write_text(json.dumps({"characters": [{"id": CH, "canonical_name": "哪吒"}]}))
    voice = {"gender": "男", "pitch": "高", "timbre": "亮"}
    if card:
        voice["age_variants"] = [
            {"variant": "pre_lotus_child", "chapter_range": {"from": "ch012", "to": "ch013"}, "pitch": "略高"},
            {"variant": "post_lotus_youth", "chapter_range": {"from": "ch014", "to": "ch099"}}]
    (base / "bible" / "characters" / CH / "voice.json").write_text(json.dumps(voice, ensure_ascii=False))
    (base / "story" / "episode_plan.json").write_text(json.dumps({"episodes": [{"ep": "ep07", "chapters": list(chapters)}]}))
    for v in samples:
        (refs / (f"{CH}_{v}_voiceprint.mp3" if v != "default" else f"{CH}_voiceprint.mp3")).write_bytes(v.encode())
    (base / "assets" / "audio" / "voice" / "casting.json").write_text(json.dumps({"entries": [
        {"character_id": CH, "variant": v, "tts_model": "seed-audio-1.0", "tts_voice": "", "voice_desc": v} for v in casting]}))
    sl = {"shots": [{"shot_id": "sh001", "dialogue_lines": [{"speaker": CH, "text": "我不服。", "est_duration_s": 1.0}]},
                    {"shot_id": "sh002", "dialogue_lines": [{"speaker": CH, "text": "她在哪里?", "est_duration_s": 1.0}]}],
          "generation_groups": [{"group_id": "grp001", "shots": ["sh001"]}, {"group_id": "grp002", "shots": ["sh002"]}]}
    (base / "directing" / "ep07" / "shot_list.json").write_text(json.dumps(sl, ensure_ascii=False))
    for gid, names in (prompt_refs or {}).items():
        (base / "assets" / "prompts" / "ep07" / f"{gid}.json").write_text(json.dumps(
            {"audio_refs": [f"assets/audio/voice/refs/{n}" for n in names]}))
    return base


@pytest.fixture(autouse=True)
def desc_channel(monkeypatch):
    monkeypatch.setattr(dt, "tts_channel", lambda: ("volcengine", "seed-audio-1.0"))


def test_parse_voiceprint_allows_underscores_in_variant():
    assert vv.parse_voiceprint("assets/audio/voice/refs/CHAR-0007_pre_lotus_child_voiceprint.mp3") == (CH, "pre_lotus_child")
    assert vv.parse_voiceprint("CHAR-0018_voiceprint.mp3") == ("CHAR-0018", "default")
    assert vv.parse_voiceprint("CRE-0003_adult_voiceprint.wav") == ("CRE-0003", "adult")
    assert vv.parse_voiceprint("NARRATOR_voiceprint.mp3") is None


def test_prompt_sample_name_wins_and_library_anchors_it(tmp_path):
    base = make_project(tmp_path, prompt_refs={"grp001": [f"{CH}_pre_lotus_child_voiceprint.mp3"]}, chapters=())
    lines = dt.plan(base, "ep07")["lines"]
    assert [(e["variant"], e["variant_source"]) for e in lines] == [("pre_lotus_child", "prompt"), ("pre_lotus_child", "episode")]
    assert all(e["anchored"] and e["status"] == "missing" for e in lines)


def test_timeline_resolves_before_prompts_exist(tmp_path):
    base = make_project(tmp_path)                       # 白模/动态样片阶段:还没有组 prompt
    r = vv.Resolver(base, "ep07").resolve("grp001", CH)
    assert (r["variant"], r["source"], r["problem"]) == ("pre_lotus_child", "timeline", "")


def test_sole_registered_form_when_card_has_no_chapters(tmp_path):
    base = make_project(tmp_path, card=False)
    r = vv.Resolver(base, "ep07").resolve("grp001", CH)
    assert (r["variant"], r["source"]) == ("pre_lotus_child", "sole")


def test_unregistered_form_is_unbound_not_synthesized(tmp_path):
    base = make_project(tmp_path, chapters=("ch014",))  # 本集已是莲花化身之后,但该形态还没出样本
    calls = []
    m = dt.sync(base, "ep07", tts=lambda e, out: calls.append(e), probe=lambda p: 1.0)
    assert calls == [] and m["summary"]["unbound"] == 2
    assert "post_lotus_youth" in m["lines"][0]["reason"]


def test_missing_form_falls_back_to_base_sample(tmp_path):
    base = make_project(tmp_path, samples=("default",), casting=("default",), chapters=("ch014",))
    e = dt.plan(base, "ep07")["lines"][0]
    assert (e["variant"], e["status"], e["anchored"]) == ("post_lotus_youth", "missing", True)


def test_ambiguous_episode_spanning_two_forms(tmp_path):
    base = make_project(tmp_path, samples=("pre_lotus_child", "post_lotus_youth"),
                        casting=("pre_lotus_child", "post_lotus_youth"), chapters=("ch013", "ch014"))
    r = vv.Resolver(base, "ep07").resolve("grp001", CH)
    assert r["source"] == "default" and "判定不了" in r["problem"]


def test_prompt_vs_timeline_mismatch_only_warns(tmp_path):
    base = make_project(tmp_path, samples=("pre_lotus_child", "post_lotus_youth"),
                        casting=("pre_lotus_child", "post_lotus_youth"),
                        prompt_refs={"grp001": [f"{CH}_post_lotus_youth_voiceprint.mp3"]})
    r = vv.Resolver(base, "ep07").resolve("grp001", CH)
    assert (r["variant"], r["source"], r["problem"]) == ("post_lotus_youth", "prompt", "") and "pre_lotus_child" in r["warning"]
