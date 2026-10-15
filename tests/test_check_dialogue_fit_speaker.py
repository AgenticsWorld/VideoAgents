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


def _slug_project(tmp_path, lines, screenplay):
    cdir = tmp_path / "bible" / "characters"
    for cid, speed in (("CHAR-jie-rui-er", 300), ("CHAR-alice", 200), ("CHAR-alice-sister", 240)):
        (cdir / cid).mkdir(parents=True)
        (cdir / cid / "voice.json").write_text(json.dumps({"speed_cpm": speed}))
    (cdir / "index.json").write_text(json.dumps({"characters": [
        {"id": "CHAR-jie-rui-er", "name": "杰瑞尔", "original_name": "Jerril"},
        {"id": "CHAR-alice", "name": "Alice"}, {"id": "CHAR-alice-sister", "name": "Alice's Sister"}]}, ensure_ascii=False))
    d = tmp_path / "directing" / "ep01"
    d.mkdir(parents=True)
    d.joinpath("shot_list.json").write_text(json.dumps({
        "shots": [{"shot_id": "sh001", "duration_s": 10, "dialogue_lines": lines}],
        "generation_groups": [{"group_id": "grp001", "shots": ["sh001"], "audio_plan": "dialogue",
                               "total_duration_s": 10}]}, ensure_ascii=False))
    s = tmp_path / "story" / "episodes" / "ep01"
    s.mkdir(parents=True)
    s.joinpath("screenplay.md").write_text(screenplay)
    return tmp_path


def test_slug_ids_speed_and_source_match(tmp_path):
    """#117:slug 编号(CHAR-jie-rui-er)剧本括注与 shot_list 两侧同 key,取得到语速;-v1 形态后缀归到已登记编号。"""
    sp = ("## S01\n\n### 对白\n\n- **Jerril (CHAR-jie-rui-er)**: First time here? {pace: medium}\n"
          "- **Alice (CHAR-alice-v1)**: Curiouser and curiouser. {pace: medium}\n"
          "- **Alice's Sister (CHAR-alice-sister)**: Wake up. {pace: medium}\n")
    lines = [{"speaker": "CHAR-jie-rui-er", "text": "First time here?"},
             {"speaker": "CHAR-alice", "text": "Curiouser and curiouser."},
             {"speaker": "CHAR-alice-sister", "text": "Wake up."}]
    rep = cdf.run(_slug_project(tmp_path, lines, sp), "ep01")
    assert not any("lines_text_match_source" in e for e in rep["errors"]), rep["errors"]
    assert not any("speaker_speed_unknown" in w or "source_lines_covered" in w for w in rep["warnings"]), rep["warnings"]
    assert {ln["speaker"]: ln["cpm"] for ln in rep["groups"][0]["lines"]} == {
        "CHAR-jie-rui-er": 300, "CHAR-alice": 200, "CHAR-alice-sister": 240}


def test_numeric_suffix_speaker_stays_numeric(tmp_path):
    """#117 回归:数字编号带形态后缀(CHAR-0007-v1)仍归到 CHAR-0007(2026-10-08 之前的口径)。"""
    sp = "## S01\n\n### 对白\n\n- **掌柜(CHAR-0007-v1)**:再对一遍 {est_duration_s: 0.8}\n"
    lines = [{"speaker": "CHAR-0007", "text": "再对一遍", "est_duration_s": 0.8}]
    rep = cdf.run(_project(tmp_path, lines, sp), "ep01")
    assert not any("lines_text_match_source" in e for e in rep["errors"]), rep["errors"]
    assert not any("source_lines_covered" in w for w in rep["warnings"]), rep["warnings"]


def test_unknown_speaker_zero_est_is_not_trusted(tmp_path):
    """#127:无语速设定的说话人,初稿占位 est 0 不当记录,按项目中位语速估(--write-est 才能回写真值)。"""
    sp = "## S01\n\n### 对白\n\n- **路人甲**:今天的账对不上啊 {est_duration_s: 0}\n"
    lines = [{"speaker": "路人甲", "text": "今天的账对不上啊", "est_duration_s": 0}]
    rep = cdf.run(_project(tmp_path, lines, sp), "ep01")
    est = rep["groups"][0]["lines"][0]["est_s"]
    assert est > 1.0, rep["groups"][0]["lines"]
    sp_lines = cdf.parse_screenplay_lines(sp)
    assert cdf.recorded_est(sp_lines[0]["est_recorded"]) is None and cdf.recorded_est("1.5") == 1.5


def test_name_index_registers_id_and_original_name(tmp_path):
    """#117:英文原名(original_name)与编号本身也能反查到编号;规范名 / 别名优先。"""
    c = tmp_path / "bible" / "characters"
    c.mkdir(parents=True)
    (c / "index.json").write_text(json.dumps({"characters": [
        {"id": "CHAR-hari-seldon", "name": "哈里・谢顿", "original_name": "Hari Seldon"},
        {"id": "cao-cao", "name": "曹操"}]}, ensure_ascii=False))
    names = dialogue_tts.name_index(tmp_path)
    assert names["Hari Seldon"] == "CHAR-hari-seldon" and names["哈里・谢顿"] == "CHAR-hari-seldon"
    assert names["cao-cao"] == "cao-cao"
    assert dialogue_tts.resolve_speaker({"speaker": "Hari Seldon"}, names)[0] == "CHAR-hari-seldon"
