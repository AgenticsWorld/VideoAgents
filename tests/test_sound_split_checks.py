# -*- coding: utf-8 -*-
"""声画分离一期(2026-10-03,docs/sound_split.md)——机检侧:
剧本层 (O.S.)/(V.O.) 句不再被剔除并带 placement;对白装载只累计画内句;speakers_le_3 只数画内说话人;
拆解表把人物 V.O. 留在对白里;台词演法的时长上限按画外窗口算。离线,不调 ffmpeg / TTS。"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "code"))

from modules import dialogue_direction as dd  # noqa: E402
from modules import script_breakdown as sb  # noqa: E402


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "code" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cdf = _load("check_dialogue_fit")
cgg = _load("check_generation_groups")

SCREENPLAY = """---
generated_at: 2026-10-03
---
## S01 | INT | SCN-0001 书房 | 夜
**[事件] ev0001 | [出场] CHAR-0001, CHAR-0002 | [时长] 20s**
动作:林昭推门。
- **林昭(CHAR-0001)**:师父,这卷经书…… {emotion: 疑惑, pace: medium, est_duration_s: 2.5}
- **老道儿(CHAR-0002)**(O.S.):放下。 {emotion: 冷压, pace: slow, est_duration_s: 1.6}
- **林昭(CHAR-0001)**(V.O.):他从未这样对我说过话。 {emotion: 低落, pace: slow, est_duration_s: 3.0}
### 旁白
- **旁白(V.O.)**:那一夜之后,一切都变了。
"""


def _project(tmp_path, lines, *, sound_split="auto", screenplay=SCREENPLAY, audio_plan="dialogue", shot_s=10):
    cdir = tmp_path / "bible" / "characters"
    for cid in ("CHAR-0001", "CHAR-0002"):
        (cdir / cid).mkdir(parents=True)
        (cdir / cid / "voice.json").write_text(json.dumps({"speed_cpm": 240}))
    (cdir / "index.json").write_text(json.dumps({"characters": [
        {"id": "CHAR-0001", "canonical_name": "林昭"}, {"id": "CHAR-0002", "canonical_name": "老道儿"}]}, ensure_ascii=False))
    (tmp_path / "settings.json").write_text(json.dumps({"output": {"sound_split": sound_split}}))
    d = tmp_path / "directing" / "ep01"
    d.mkdir(parents=True)
    d.joinpath("shot_list.json").write_text(json.dumps({
        "generated_at": "2026-10-03",
        "shots": [{"shot_id": "sh001", "duration_s": shot_s, "dialogue_lines": lines}],
        "generation_groups": [{"group_id": "grp001", "shots": ["sh001"], "audio_plan": audio_plan,
                               "has_dialogue": audio_plan == "dialogue", "total_duration_s": shot_s}]}, ensure_ascii=False))
    s = tmp_path / "story" / "episodes" / "ep01"
    s.mkdir(parents=True)
    s.joinpath("screenplay.md").write_text(screenplay)
    return tmp_path


# ---------------------------------------------------------------- 剧本层解析
def test_screenplay_keeps_os_vo_lines_with_placement():
    lines = cdf.parse_screenplay_lines(SCREENPLAY)
    assert [(x["speaker"], x["placement"]) for x in lines] == [("CHAR-0001", "on"), ("CHAR-0002", "os"), ("CHAR-0001", "vo")]
    # ### 旁白 小节的旁白者文本不是人物台词
    assert all("一切都变了" not in x["text"] for x in lines)


def test_source_placement_marks():
    assert cdf.source_placement("林昭(CHAR-0001)", "(V.O.)") == "vo"
    assert cdf.source_placement("林昭(CHAR-0001)", "(O.S.)") == "os"
    assert cdf.source_placement("林昭(CHAR-0001)", "(画外音)") == "vo"      # 画外音 ≠ 画外
    assert cdf.source_placement("林昭(CHAR-0001)", "(画外)") == "os"
    assert cdf.source_placement("林昭(CHAR-0001)", "(四下张望)") == "on"


# ---------------------------------------------------------------- 对白装载只累计画内句
def test_fit_sums_only_onscreen_lines(tmp_path):
    lines = [
        {"speaker": "CHAR-0001", "text": "师父,这卷经书……", "pace": "medium", "est_duration_s": 2.5},
        {"speaker": "CHAR-0002", "text": "放下。", "pace": "slow", "placement": "os", "heard_in": ["sh001"],
         "placement_source": "script", "est_duration_s": 1.6},
        {"speaker": "CHAR-0001", "text": "他从未这样对我说过话。", "pace": "slow", "placement": "vo", "heard_in": ["sh001"],
         "placement_source": "script", "est_duration_s": 3.0},
    ]
    rep = cdf.run(_project(tmp_path, lines, shot_s=4), "ep01", legacy_override=False)
    g = rep["groups"][0]
    assert g["onscreen_lines"] == 1 and g["offscreen_lines"] == 2
    assert [x["placement"] for x in g["lines"]] == ["on", "os", "vo"]
    # 镜长 4s 只装画内一句:Σ画内估时 = 该句估时,不含画外两句
    on_est = next(x["est_s"] for x in g["lines"] if x["placement"] == "on")
    assert g["shots"][0]["est_s"] == round(on_est, 1) and g["shots"][0]["onscreen_lines"] == 1
    assert not [e for e in rep["errors"] if "dialogue_fit_shot" in e]
    assert rep["checks"]["placement_matches_source"] == "PASS" and rep["checks"]["placement_valid"] == "PASS"
    assert rep["checks"]["lines_text_match_source"] == "PASS"       # 画外句也必须在剧本对白层找得到


def test_script_marked_offscreen_cannot_be_turned_onscreen(tmp_path):
    lines = [
        {"speaker": "CHAR-0001", "text": "师父,这卷经书……", "pace": "medium"},
        {"speaker": "CHAR-0002", "text": "放下。", "pace": "slow"},                 # 剧本标 (O.S.),分镜写成画内
        {"speaker": "CHAR-0001", "text": "他从未这样对我说过话。", "pace": "slow", "placement": "vo"},
    ]
    rep = cdf.run(_project(tmp_path, lines), "ep01", legacy_override=False)
    assert rep["checks"]["placement_matches_source"] == "FAIL"
    assert any("剧本标记画外" in e for e in rep["errors"])


def test_directing_offscreen_requires_reason_and_mode(tmp_path):
    base_lines = [
        {"speaker": "CHAR-0001", "text": "师父,这卷经书……", "pace": "medium", "placement": "os", "heard_in": ["sh001"]},
        {"speaker": "CHAR-0002", "text": "放下。", "pace": "slow", "placement": "os"},
        {"speaker": "CHAR-0001", "text": "他从未这样对我说过话。", "pace": "slow", "placement": "vo"},
    ]
    rep = cdf.run(_project(tmp_path / "a", base_lines), "ep01", legacy_override=False)
    assert any("placement_matches_source" in e and "placement_reason" in e for e in rep["errors"])
    ok_lines = [dict(base_lines[0], placement_source="directing", placement_reason={"trigger": "D1", "evidence": "听者反应"})] + base_lines[1:]
    rep = cdf.run(_project(tmp_path / "b", ok_lines), "ep01", legacy_override=False)
    assert rep["checks"]["placement_matches_source"] == "PASS"
    # 仅剧本标记模式:分镜层转画外不许
    rep = cdf.run(_project(tmp_path / "c", ok_lines, sound_split="script_only"), "ep01", legacy_override=False)
    assert rep["checks"]["placement_valid"] == "FAIL"
    # 关闭模式:任何 os/vo 都 FAIL,且按画内累计
    rep = cdf.run(_project(tmp_path / "d", ok_lines, sound_split="off"), "ep01", legacy_override=False)
    assert rep["checks"]["placement_valid"] == "FAIL"
    assert rep["groups"][0]["offscreen_lines"] == 0 and rep["groups"][0]["onscreen_lines"] == 3


def test_audio_plan_mismatch_for_offscreen_only_group(tmp_path):
    lines = [{"speaker": "CHAR-0001", "text": "他从未这样对我说过话。", "pace": "slow", "placement": "vo", "placement_source": "script"}]
    rep = cdf.run(_project(tmp_path, lines, audio_plan="dialogue"), "ep01", legacy_override=False)
    assert any("audio_plan_mismatch" in w and "voice_over" in w for w in rep["warnings"])


def test_trim_target_only_counts_onscreen():
    grec = {"group_id": "g", "capacity_s": 2.0, "est_s": 4.0, "over_s": 2.0, "total_duration_s": 10, "lines": [
        {"shot_id": "s", "speaker": "A", "text": "一二三四五六七八", "chars": 8, "cpm": 240, "est_s": 4.0, "placement": "on"},
        {"shot_id": "s", "speaker": "B", "text": "画外画外画外画外", "chars": 8, "cpm": 240, "est_s": 4.0, "placement": "os"}]}
    t = cdf._trim_target(grec)
    assert [x["speaker"] for x in t["lines"]] == ["A"]


# ---------------------------------------------------------------- speakers_le_3 / audio_plan
def _sl(lines_by_shot, audio_plan="dialogue", has_dialogue=True):
    shots = [{"shot_id": sid, "duration_s": 3, "scene_id": "SCN-0001", "is_dialogue": True, "characters": [], "dialogue_lines": lns}
             for sid, lns in lines_by_shot.items()]
    return {"shots": shots, "generation_groups": [{"group_id": "grp001", "shots": list(lines_by_shot), "audio_plan": audio_plan,
                                                   "has_dialogue": has_dialogue, "total_duration_s": 3 * len(shots),
                                                   "characters_union": [], "continuity_from": None}]}


def test_speakers_le_3_counts_onscreen_only():
    four_on = _sl({"sh001": [{"speaker": f"CHAR-000{i}", "text": "嗯"} for i in range(1, 5)]})
    errs = cgg.check_speakers(four_on)
    assert len(errs) == 1 and "speakers_le_3" in errs[0] and "4>3" in errs[0]
    three_on_one_os = _sl({"sh001": [{"speaker": f"CHAR-000{i}", "text": "嗯"} for i in range(1, 4)]
                           + [{"speaker": "CHAR-0004", "text": "插话", "placement": "os", "heard_in": ["sh001"]}]})
    assert cgg.check_speakers(three_on_one_os) == []


def test_check_7d_expects_voice_over_for_offscreen_only_group():
    sl = _sl({"sh001": [{"speaker": "CHAR-0001", "text": "心声", "placement": "vo"}]}, audio_plan="dialogue", has_dialogue=False)
    errs = cgg.check_7d(sl, None, narration_on=False)
    assert any("audio_plan_consistent" in e and "voice_over" in e for e in errs)
    sl["generation_groups"][0]["audio_plan"] = "voice_over"
    assert not [e for e in cgg.check_7d(sl, None, narration_on=False) if "audio_plan" in e]
    # has_dialogue 与画内句事实不符
    sl["generation_groups"][0]["has_dialogue"] = True
    assert any("has_dialogue_consistent" in e for e in cgg.check_7d(sl, None, narration_on=False))


def test_propose_groups_has_dialogue_ignores_offscreen():
    shots = [{"shot_id": "sh001", "duration_s": 5, "scene_id": "S", "is_dialogue": True, "characters": [],
              "dialogue_lines": [{"speaker": "CHAR-0001", "text": "心声", "placement": "vo"}]}]
    assert cgg.propose_groups(shots, 1)[0]["has_dialogue"] is False


# ---------------------------------------------------------------- 拆解表
def test_breakdown_keeps_char_vo_as_dialogue_with_placement():
    sp = sb.parse_screenplay(SCREENPLAY)
    s1 = sp["scenes"][0]
    assert [(d["speaker_id"], d.get("placement")) for d in s1["dialogue"]] == [
        ("CHAR-0001", None), ("CHAR-0002", "os"), ("CHAR-0001", "vo")]
    assert len(s1["narration_candidates"]) == 1 and "一切都变了" in s1["narration_candidates"][0]["text"]


# ---------------------------------------------------------------- 台词演法上限
def test_direction_limit_for_offscreen_line():
    on = {"speaker": "CHAR-0001", "text": "师父", "est_duration_s": 2.0}
    os_ln = {"speaker": "CHAR-0002", "text": "放下", "placement": "os", "heard_in": ["sh001", "sh002"], "offset_s": 0.4}
    sh1 = {"shot_id": "sh001", "duration_s": 4.0, "dialogue_lines": [on, os_ln]}
    sh2 = {"shot_id": "sh002", "duration_s": 3.0, "dialogue_lines": []}
    by_id = {"sh001": sh1, "sh002": sh2}
    # 画内句上限只扣其它画内句(没有)→ 镜长 − 镜首留白
    assert dd._line_limit(sh1, [on, os_ln], 0, by_id) == round(4.0 - dd.LEAD_S, 2)
    # 画外句上限 = Σ heard_in 镜长 7 − 画内估时 2 − offset 0.4
    assert dd._line_limit(sh1, [on, os_ln], 1, by_id) == 4.6


# ---------------------------------------------------------------- 旁白条目头(#141)

NARR_HEADS = {
    # issue 原样:ID 后先插场景元数据,再写 anchor / est_duration_s
    "scene_meta": "[N-01 | scene: S01 | scene_id: SCN-0001 | anchor: 林昭推门 | est_duration_s: 4.4 | source: ch001#p001]\n那一夜之后,一切都变了。\n",
    # 故事板 SOUL 规定的四段锚点
    "four_seg": "[N-01 | anchor: S01 | SCN-0001 | ev0001 | 场首「林昭推门」一镜 | est_duration_s: 4.4 | source: ch001#p001]\n那一夜之后,一切都变了。\n",
    # 字段值里带方括号(旧正则认得,换解析后不能丢)
    "bracket": "[N-01 | anchor: S01 | est_duration_s: 4.4 | source: screenplay S01 `[旁白候选]`]\n那一夜之后,一切都变了。\n",
}


def _narr_sl(est=4.4):
    return {"shots": [{"shot_id": "sh001", "duration_s": 8}],
            "generation_groups": [{"group_id": "grp001", "shots": ["sh001"], "audio_plan": "voice_over", "total_duration_s": 8}],
            "narration_anchors": [{"narration_id": "N-01", "anchor_group": "grp001", "anchor_shots": ["sh001"],
                                   "est_duration_s": est, "window_s": 6}]}


@pytest.mark.parametrize("head", sorted(NARR_HEADS))
def test_check_7d_reads_narration_heads_with_extra_fields(head):
    """#141:条目头 ID 之后字段任意顺序 / 多出元数据 / 四段锚点都解析得到 ID 与 est_duration_s,不再误报「解析不到任何条目」。"""
    assert cgg.check_7d(_narr_sl(), NARR_HEADS[head]) == []
    errs = cgg.check_7d(_narr_sl(est=3.0), NARR_HEADS[head])                 # 估时照常核对
    assert any("N-01 est_mismatch" in e and "4.4" in e for e in errs) and not any("解析不到" in e for e in errs)


def test_check_7d_narration_head_without_est_is_named():
    md = NARR_HEADS["scene_meta"] + "\n[N-02 | anchor: S01 | audio_duration_s: 3.0]\n第二句。\n"
    sl = _narr_sl()
    sl["narration_anchors"].append({"narration_id": "N-02", "anchor_group": "grp001", "anchor_shots": ["sh001"], "window_s": 6})
    errs = cgg.check_7d(sl, md)
    assert errs == ["narration_anchors_cover_all: 条目头缺 est_duration_s ['N-02']"]
    assert cgg.check_7d(_narr_sl(), "# 旁白稿\n(待写)\n") == [
        "narration_anchors_cover_all: narration.md 解析不到任何条目(条目头格式应为 [N-xx | anchor: … | est_duration_s: …])"]


def test_check_generation_groups_cli_accepts_scene_meta_head(tmp_path):
    """issue 复现命令:python3 code/check_generation_groups.py <shot_list.json> --narration <narration.md>。"""
    (tmp_path / "settings.json").write_text(json.dumps({"output": {"narration_enabled": True}}))
    d = tmp_path / "directing" / "ep01"
    d.mkdir(parents=True)
    sl = _narr_sl()
    sl["shots"][0].update({"scene_id": "SCN-0001", "scene_no": "S01"})
    sl["generation_groups"][0].update({"scene_id": "SCN-0001", "scene_no": "S01", "characters_union": []})
    (d / "shot_list.json").write_text(json.dumps(sl, ensure_ascii=False), encoding="utf-8")

    def run(md):
        (tmp_path / "narration.md").write_text(md, encoding="utf-8")
        return subprocess.run([sys.executable, str(ROOT / "code" / "check_generation_groups.py"), str(d / "shot_list.json"),
                               "--narration", str(tmp_path / "narration.md")], capture_output=True, text=True, cwd=tmp_path)

    r = run(NARR_HEADS["scene_meta"])
    assert r.returncode == 0 and "解析不到任何条目" not in r.stdout, r.stdout + r.stderr
    r = run("# 旁白稿\n")                                                     # 对照:真没有条目时照报
    assert r.returncode == 1 and "解析不到任何条目" in r.stdout, r.stdout + r.stderr

