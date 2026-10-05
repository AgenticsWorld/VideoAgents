"""performance_bound ②(2026-10-05 改订):触发点用位置说法绑定、`{}` 外不引台词原词、一句台词不拆成多个 `{}`。
前科 fengshen3 ep08 sh006:拆段 + 「说到『愚弄百姓』时」夹在两个 `{}` 之间,成片把「愚弄百姓」念了两遍。"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PERFORMANCE = "08-video-gen/prompt/performance-direction"
LINE = "畜生!你生前扰害父母,死后愚弄百姓!"
END = "说完后牙关咬紧,目光钉住金身不眨眼"
OLD = ("Shot 1: 近景。李靖咬住下颌,『畜生』两字短而重:{畜生!你生前扰害父母,}说到『愚弄百姓』时眉心压痕更深:"
       "{死后愚弄百姓!}" + END + "。")
NEW = "Shot 1: 近景。李靖咬住下颌:{" + LINE + "}开头两个字短而重;说到句尾四个字时眉心压痕更深。" + END + "。"


@pytest.fixture
def mod(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEOAGENTS_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setattr(sys, "argv", ["performance_bound_check.py"])
    spec = importlib.util.spec_from_file_location("performance_bound_t", ROOT / "code/performance_bound_check.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def project(tmp_path, prompt, anchor=None, lines=None):
    (tmp_path / "settings.json").write_text(json.dumps({"project_skills": {"overrides": {PERFORMANCE: True}}}))
    d = tmp_path / "directing/ep01"
    (d / "shots/sh001").mkdir(parents=True)
    shot = {"shot_id": "sh001", "dialogue_lines": lines or [{"speaker": "CHAR-0018", "text": LINE}]}
    (d / "shot_list.json").write_text(json.dumps({
        "shots": [shot], "generation_groups": [{"group_id": "grp001", "audio_plan": "dialogue", "shots": ["sh001"]}]},
        ensure_ascii=False))
    (d / "shots/sh001/blocking.json").write_text(json.dumps({"characters": [{"id": "CHAR-0018", "performance": {
        "goal": "骂", "arc_from": "压着牙的怒", "arc_to": "压不住", "trigger": {"line_ref": "sh001-L1", "word": "愚弄百姓"},
        "forbidden_early": ["抬臂"], "end_state": END}}]}, ensure_ascii=False))
    entry = {"shot_id": "sh001", "char_id": "CHAR-0018", "trigger_word": "愚弄百姓", "end_state": END}
    if anchor:
        entry["trigger_anchor"] = anchor
    pf = tmp_path / "grp001.json"
    pf.write_text(json.dumps({"group_id": "grp001", "audio_plan": "dialogue", "shots": ["sh001"],
                              "performance": [entry], "video_prompt": prompt}, ensure_ascii=False))
    return pf


def run(mod, tmp_path, pf, fresh=False):
    groups = mod.load_groups(tmp_path, "ep01")
    errs, warns, skipped = mod.check_group(pf, tmp_path, "ep01", groups, False, mod.load_shots(tmp_path, "ep01"), fresh)
    assert not skipped
    return errs, warns


def test_new_style_passes(mod, tmp_path):
    errs, warns = run(mod, tmp_path, project(tmp_path, NEW, anchor="句尾四个字"), fresh=True)
    assert errs == [] and warns == []


def test_legacy_prompt_only_warns(mod, tmp_path):
    errs, warns = run(mod, tmp_path, project(tmp_path, OLD))
    assert errs == []
    text = "\n".join(warns)
    assert "line_split" in text and "line_words_quoted" in text and "『愚弄百姓』" in text and "trigger_anchor" in text


def test_legacy_prompt_fails_when_fresh(mod, tmp_path):
    errs, _ = run(mod, tmp_path, project(tmp_path, OLD), fresh=True)
    text = "\n".join(errs)
    assert "line_split" in text and "line_words_quoted" in text and "缺 trigger_anchor" in text


def test_anchor_entry_makes_violations_fail(mod, tmp_path):
    errs, _ = run(mod, tmp_path, project(tmp_path, OLD, anchor="句尾四个字"))
    text = "\n".join(errs)
    assert "line_split" in text and "line_words_quoted" in text and "未逐字出现" in text


def test_anchor_must_not_carry_the_trigger_word(mod, tmp_path):
    errs, _ = run(mod, tmp_path, project(tmp_path, NEW.replace("句尾四个字", "愚弄百姓四个字"), anchor="愚弄百姓四个字"))
    assert any("含触发词原文" in e for e in errs)


def test_unquoted_say_word_is_reported(mod, tmp_path):
    errs, _ = run(mod, tmp_path, project(tmp_path, NEW.replace("说到句尾四个字时", "句尾四个字上,说到愚弄百姓时"), anchor="句尾四个字"))
    assert any("说到愚弄百姓" in e for e in errs)


def test_native_lead_remainder_and_host_mark_are_not_violations(mod, tmp_path):
    prompt = ("Shot 1: 近景。李靖咬住下颌:{你生前扰害父母,死后愚弄百姓!}说到句尾四个字时眉心压痕更深。" + END + "。"
              "【原生先入】本镜开场时李靖正说到一半,上一镜画外已说出的「畜生!」不再重说,接着说完。")
    errs, warns = run(mod, tmp_path, project(tmp_path, prompt, anchor="句尾四个字"))
    assert errs == [] and warns == []


def test_two_lines_in_one_shot_stay_two_braces(mod, tmp_path):
    lines = [{"speaker": "CHAR-0018", "text": "畜生!"}, {"speaker": "CHAR-0018", "text": "你生前扰害父母,死后愚弄百姓!"}]
    prompt = "Shot 1: 近景。{畜生!}停半拍。{你生前扰害父母,死后愚弄百姓!}说到句尾四个字时眉心压痕更深。" + END + "。"
    errs, warns = run(mod, tmp_path, project(tmp_path, prompt, anchor="句尾四个字", lines=lines))
    assert errs == [] and warns == []
