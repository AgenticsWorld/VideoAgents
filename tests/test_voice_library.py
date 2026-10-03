"""TTS 语音模式与音色库(modules/voice_library.py):模式推断、按模式生效的模型、性别/年龄识别、打分选型、
默认音色兜底、存量配置迁移、对白语音库按模式判未选角。"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules import dialogue_tts as dt  # noqa: E402
from modules import voice_library as vl  # noqa: E402


def test_voice_mode_inference_and_single_mode_providers():
    assert vl.voice_mode("volcengine", {"model": "seed-audio-1.0"}) == "design"
    assert vl.voice_mode("volcengine", {"model": "seed-tts-2.0"}) == "library"
    assert vl.voice_mode("volcengine", {"model": "seed-tts-2.0", "voice_mode": "design"}) == "design"
    assert vl.voice_mode("comfyui", {"mode": "local", "design_workflow": "comfy/tts-design.json"}) == "design"
    assert vl.voice_mode("comfyui", {"mode": "rh_ai", "design_workflow": "x", "rh_design_workflow_id": ""}) == "library"
    assert vl.voice_mode("agentics", {}) == "design"
    assert vl.voice_mode("agentics", {"voice_mode": "library"}) == "library"
    # 只有音色库的渠道:存了 design 也不认
    assert vl.voice_mode("elevenlabs", {"voice_mode": "design"}) == "library"
    assert vl.voice_library_kind("agentics", {"voice_mode": "library"}) == "local"
    assert vl.voice_library_kind("minimax", {}) == "provider"
    assert vl.voice_library_kind("volcengine", {"voice_mode": "design"}) == ""


def test_effective_model_follows_mode():
    assert vl.effective_model("volcengine", {"voice_mode": "design", "model": "seed-tts-2.0"}) == "seed-audio-1.0"
    assert vl.effective_model("volcengine", {"voice_mode": "library", "model": "seed-tts-1.0"}) == "seed-tts-1.0"
    # 存量配置把音频生成 / 声音复刻存在 model 里:音色库模式回落 Seed-TTS 2.0
    assert vl.effective_model("volcengine", {"voice_mode": "library", "model": "seed-icl-2.0"}) == "seed-tts-2.0"
    assert vl.effective_model("agentics", {"clone": "x"}) == ""
    assert vl.effective_model("elevenlabs", {"model": "eleven_v3"}) == "eleven_v3"


def test_guess_gender_age_handles_whole_words_and_negation():
    assert vl.guess_gender("English_radiant_girl") == "female"
    assert vl.guess_gender("magnetic_voiced_man") == "male"
    assert vl.guess_gender("Mature_Woman") == "female"          # woman 里的 man 不算
    assert vl.guess_gender("Kore") == ""
    assert vl.guess_age("童声清亮") == "child"
    assert vl.guess_age("少年男声,不作童声、不作女童声") == "young"
    assert vl.guess_age("中年男老师") == "middle"


def _entries():
    return [vl._entry("f_cold", "林潇", "女", "青年", ["zh-cn"], "声线清冷干净的青年女声"),
            vl._entry("f_old", "婆婆", "女", "老年", ["zh-cn"], "慈祥的长辈女声"),
            vl._entry("f_yue", "粤语女声", "女", "青年", ["zh-cn"], "清冷的青年女性粤语声音"),
            vl._entry("f_en", "Emily", "female", "young", ["en"], "cold young lady"),
            vl._entry("m_deep", "云舟", "男", "青年", ["zh-cn"], "磁性低沉的男声"),
            vl._entry("unknown", "Harper", "", "", [], "")]


def test_rank_filters_gender_prefers_age_language_and_card_words():
    profile = {"gender": "female", "age": "young", "voice_text": "清亮偏冷的青年女声,清冷", "text": ""}
    ranked = vl.rank(_entries(), profile, lang="zh")
    ids = [e["id"] for e in ranked]
    assert "m_deep" not in ids                                   # 性别对不上直接出局
    assert ids[0] == "f_cold"
    assert ids.index("f_old") > ids.index("f_cold")              # 老年音色给年轻人物要降权
    assert ids.index("f_yue") > ids.index("f_cold")              # 普通话人物不取粤语音色
    assert "须听辨" in next(e for e in ranked if e["id"] == "unknown")["reason"]


def test_rank_puts_taken_voices_last():
    profile = {"gender": "female", "age": "young", "voice_text": "清冷", "text": ""}
    ranked = vl.rank(_entries(), profile, lang="zh", taken={"f_cold": "CHAR-0002"})
    assert ranked[-1]["id"] == "f_cold" and "CHAR-0002" in ranked[-1]["reason"]


def test_voice_card_age_reads_card_fields_not_story_text(tmp_path):
    card = tmp_path / "bible" / "characters" / "CHAR-0001"
    card.mkdir(parents=True)
    (card / "voice.json").write_text(json.dumps({
        "age_band": "年轻成熟(25-30)", "timbre": "成年男声",
        "age_variants": [{"variant": "kid", "timbre": "稚嫩童声"}]}, ensure_ascii=False))
    assert vl.voice_card_age(tmp_path, "CHAR-0001") == "young"
    assert vl.voice_card_age(tmp_path, "CHAR-0001", "kid") == "child"
    assert vl.voice_card_age(tmp_path, "CHAR-0404") == ""


def test_default_voice_legacy_then_auto(monkeypatch):
    lib = {"entries": [vl._entry("Kore", "Kore", "female"), vl._entry("narr", "解说小明", "男", "", ["zh-cn"], "纪录片旁白")]}
    monkeypatch.setattr(vl, "load_library", lambda *a, **k: lib)
    assert vl.default_voice("minimax", {"voice": "old_voice"}, "speech-2.8-hd") == "old_voice"
    assert vl.default_voice("minimax", {}, "speech-2.8-hd", "话说陈塘关") == "narr"
    # OpenRouter 各模型音色互不通用:旧默认音色不属于当前模型就不用
    assert vl.default_voice("openrouter", {"voice": "eve"}, "google/gemini", "hello") in ("Kore", "narr")
    assert vl.default_voice("openrouter", {"voice": "Kore"}, "google/gemini", "hello") == "Kore"


def test_volc_credentials_prefer_tts_section(monkeypatch, tmp_path):
    cfg = tmp_path / "genconfig.json"
    cfg.write_text(json.dumps({"storage": {"tos": {"access_key": "TOSAK", "secret_key": "TOSSK"}}}))
    monkeypatch.setattr(vl, "CONFIG_PATH", cfg)
    monkeypatch.delenv("VOLC_ACCESSKEY", raising=False)
    monkeypatch.delenv("VOLC_SECRETKEY", raising=False)
    assert vl.volc_credentials({"access_key": "AK", "secret_key": "SK"}) == ("AK", "SK")
    assert vl.volc_credentials({}) == ("TOSAK", "TOSSK")        # 存量安装兜底


def test_migration_sets_mode_and_moves_volc_keys():
    from services.runtime import core
    cfg = {"tts": {"provider": "volcengine",
                   "volcengine": {"api_key": "k", "model": "seed-audio-1.0", "voice": "zh_x"},
                   "comfyui": {"mode": "local", "workflow": "comfy/tts-indextts2-api.json", "design_workflow": ""},
                   "agentics": {"design": "d", "clone": "c"}},
           "storage": {"tos": {"access_key": "AK", "secret_key": "SK"}}}
    core._migrate_genconfig(cfg)
    v = cfg["tts"]["volcengine"]
    assert (v["voice_mode"], v["model"], v["access_key"], v["secret_key"]) == ("design", "seed-tts-2.0", "AK", "SK")
    assert v["voice"] == "zh_x"                                  # 旧默认音色留着作兜底
    assert cfg["tts"]["comfyui"]["voice_mode"] == "library"
    assert cfg["tts"]["agentics"]["voice_mode"] == "design"
    cfg2 = {"tts": {"volcengine": {"model": "seed-icl-2.0"}}}
    core._migrate_genconfig(cfg2)
    assert (cfg2["tts"]["volcengine"]["voice_mode"], cfg2["tts"]["volcengine"]["model"]) == ("library", "seed-tts-2.0")


def _project(tmp_path: Path, sample: bool) -> Path:
    base = tmp_path / "proj"
    for d in (base / "directing" / "ep01", base / "bible" / "characters" / "CHAR-0001", base / "assets" / "audio" / "voice" / "refs"):
        d.mkdir(parents=True)
    (base / "settings.json").write_text(json.dumps({"output": {"dialogue_tts": True}}))
    (base / "bible" / "characters" / "index.json").write_text(json.dumps({"characters": [{"id": "CHAR-0001"}]}))
    (base / "bible" / "characters" / "CHAR-0001" / "voice.json").write_text(json.dumps({"gender": "男", "pitch": "中"}))
    (base / "directing" / "ep01" / "shot_list.json").write_text(json.dumps({"shots": [
        {"shot_id": "sh001", "dialogue_lines": [{"speaker": "CHAR-0001", "text": "走。", "est_duration_s": 0.5}]}]}))
    if sample:
        (base / "assets" / "audio" / "voice" / "refs" / "CHAR-0001_voiceprint.mp3").write_bytes(b"vp")
    return base


@pytest.mark.parametrize("mode,sample,status", [("design", False, "unbound"), ("design", True, "missing"),
                                                ("library", False, "missing")])
def test_dialogue_library_binding_follows_voice_mode(tmp_path, monkeypatch, mode, sample, status):
    """Agentics:音色设计模式没样本=未选角(Voice Clone 必须有参考);音色库(本地)模式没登记也能合成(生成层自动选)。"""
    monkeypatch.setattr(dt, "tts_channel", lambda: ("agentics", ""))
    monkeypatch.setattr(vl, "load_tts_config", lambda: {"provider": "agentics", "agentics": {"voice_mode": mode}})
    line = dt.plan(_project(tmp_path, sample), "ep01")["lines"][0]
    assert line["status"] == status
