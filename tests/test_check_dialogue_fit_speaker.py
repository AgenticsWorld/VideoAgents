"""#58:对白装载机检说话人解析认 speaker_char / character_id 与规范名/别名,与逐句语音库同口径。"""
import importlib.util
import json
from pathlib import Path

from modules import dialogue_tts

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("check_dialogue_fit", ROOT / "code" / "check_dialogue_fit.py")
cdf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cdf)


def _project(tmp_path, lines, screenplay=None):
    cdir = tmp_path / "bible" / "characters"
    (cdir / "CHAR-0007").mkdir(parents=True)
    (cdir / "CHAR-0008").mkdir(parents=True)
    (cdir / "index.json").write_text(json.dumps({"characters": [
        {"id": "CHAR-0007", "canonical_name": "王掌柜", "aliases": ["掌柜"]},
        {"id": "CHAR-0008", "canonical_name": "小伙计"}]}, ensure_ascii=False))
    (cdir / "CHAR-0007" / "voice.json").write_text(json.dumps({"speed_cpm": 300}))
    (cdir / "CHAR-0008" / "voice.json").write_text(json.dumps({"speed_cpm": 200}))
    d = tmp_path / "directing" / "ep01"
    d.mkdir(parents=True)
    d.joinpath("shot_list.json").write_text(json.dumps({
        "shots": [{"shot_id": "sh001", "duration_s": 10, "dialogue_lines": lines}],
        "generation_groups": [{"group_id": "grp001", "shots": ["sh001"], "audio_plan": "dialogue",
                               "total_duration_s": 10}]}, ensure_ascii=False))
    if screenplay:
        s = tmp_path / "story" / "episodes" / "ep01"
        s.mkdir(parents=True)
        s.joinpath("screenplay.md").write_text(screenplay)
    return tmp_path


def test_resolve_speaker_order():
    names = {"掌柜": "CHAR-0007", "王掌柜": "CHAR-0007"}
    r = dialogue_tts.resolve_speaker
    assert r({"speaker": "王掌柜(CHAR-0009)"}, names) == ("CHAR-0009", "王掌柜(CHAR-0009)")
    assert r({"speaker": "账房先生", "speaker_char": "CHAR-0010"}, names)[0] == "CHAR-0010"
    assert r({"speaker": "掌柜", "character_id": "CHAR-0011"}, names)[0] == "CHAR-0011"
    assert r({"speaker": "掌柜(压低声)"}, names)[0] == "CHAR-0007"
    assert r({"speaker": "路人"}, names) == ("", "路人")


def test_speaker_char_and_alias_known_speed(tmp_path):
    lines = [{"speaker": "账房先生", "speaker_char": "CHAR-0008", "text": "今天的账对不上", "est_duration_s": 2.1},
             {"speaker": "掌柜", "text": "再对一遍", "est_duration_s": 0.8}]
    rep = cdf.run(_project(tmp_path, lines), "ep01")
    assert not any("speaker_speed_unknown" in w for w in rep["warnings"]), rep["warnings"]
    got = {ln["speaker"]: ln["cpm"] for ln in rep["groups"][0]["lines"]}
    assert got == {"CHAR-0008": 200, "CHAR-0007": 300}


def test_display_name_source_still_matches(tmp_path):
    """对白层写显示名、别名表未登记、shot_list 用 speaker_char:不得新增 lines_text_match_source FAIL。"""
    sp = "## S01\n\n### 对白\n\n- **账房先生**:今天的账对不上 {est_duration_s: 2.1}\n"
    lines = [{"speaker": "账房先生", "speaker_char": "CHAR-0008", "text": "今天的账对不上", "est_duration_s": 2.1}]
    rep = cdf.run(_project(tmp_path, lines, sp), "ep01")
    assert not any("source_unparsed" in w for w in rep["warnings"]), rep["warnings"]
    assert not any("lines_text_match_source" in e for e in rep["errors"]), rep["errors"]
    assert not any("source_lines_covered" in w for w in rep["warnings"]), rep["warnings"]
