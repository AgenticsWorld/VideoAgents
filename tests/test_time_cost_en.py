"""2026-10-20 英文估时校准的周边:旁白估时由宿主算、校准工具(音节诊断 / 拟合)、出片台词核查的合并间隔、直播打包。"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
from modules import time_cost as tc  # noqa: E402

import narration_est as ne  # noqa: E402

NARR_MD = """# EP01 旁白稿

[N-01 | anchor: S01开场 | est_duration_s: 0 | source: ch001#p01]
陕南乡村,一栋三层小楼。那年寒假,父母回到家中。

[N-02 | anchor: scene start | est_duration_s: 9.9 | group: grp002 | source: ch001#p02 | tone: calm]
It was 1984, and nobody in the village had ever seen the sea.
```
[N-03 | anchor: S03 | est_duration_s: 2.0 | source: ch001#p03]

"""


def _proj(tmp_path, md=NARR_MD):
    base = tmp_path / "p"
    (base / "story" / "episodes" / "ep01").mkdir(parents=True)
    (base / "story" / "episodes" / "ep01" / "narration.md").write_text(md, encoding="utf-8")
    return base


def test_narration_parse_and_estimate():
    rows = ne.parse_entries(NARR_MD)
    assert [r["id"] for r in rows] == ["N-01", "N-02", "N-03"]
    assert rows[0]["text"].startswith("陕南乡村") and rows[1]["text"].startswith("It was 1984") and rows[2]["text"] == ""
    assert ne.narration_est(rows[0]["text"]) == round(tc.line_est(rows[0]["text"], "medium") * ne.NARRATION_DEFAULT_FACTOR, 1)
    assert ne.narration_est(rows[1]["text"], 1.0) == round(tc.line_est(rows[1]["text"], "medium"), 1)   # 英文按词


def test_narration_cli_write_and_check(tmp_path, capsys):
    base = _proj(tmp_path)
    argv = ["--project", "p", "--ep", "ep01", "--out-root", str(base)]
    assert ne.main(argv) == 1                                        # 记录值不一致 + N-03 无正文
    out = capsys.readouterr().out
    assert "N-01 narration_est_consistent" in out and "N-03" in out and "没有旁白正文" in out
    assert ne.main(argv + ["--write"]) == 1                          # 回写了,但 N-03 无正文仍 FAIL
    md = (base / "story" / "episodes" / "ep01" / "narration.md").read_text(encoding="utf-8")
    rows = ne.parse_entries(md)
    assert rows[0]["est"] == ne.narration_est(rows[0]["text"]) and rows[1]["est"] == ne.narration_est(rows[1]["text"])
    assert "| group: grp002 | source: ch001#p02 | tone: calm]" in md and md.count("\n") == NARR_MD.count("\n")   # 只改数字
    (base / "story" / "episodes" / "ep01" / "narration.md").write_text(md.replace("[N-03 | anchor: S03 | est_duration_s: 2.0 | source: ch001#p03]\n", ""), encoding="utf-8")
    assert ne.main(argv) == 0


def test_narration_factor_learns_from_other_episodes(tmp_path):
    base = _proj(tmp_path)
    assert ne.narration_factor(base, "ep01")[0] == ne.NARRATION_DEFAULT_FACTOR
    segs = []
    for i in range(6):                                               # 慢声线:实测 = 估时 × 1.3
        text = f"第{i}段旁白,讲一件很久以前的事情。"
        segs.append({"num": f"N-0{i}", "text": text, "duration_s": round(tc.line_est(text, "medium") * 1.3, 3)})
    segs.append({"num": "N-99", "text": "变速过的段不算。", "duration_s": 99.0, "tempo": 1.2})
    d = base / "assets" / "audio" / "narration" / "ep02"
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps({"segments": segs}, ensure_ascii=False), encoding="utf-8")
    k, src = ne.narration_factor(base, "ep01")
    assert abs(k - 1.3) < 0.02 and "6 段" in src
    assert ne.narration_factor(base, "ep02")[0] == ne.NARRATION_DEFAULT_FACTOR      # 不拿本集自己的实测


def test_calibrate_syllable_diagnostics():
    import calibrate_time_cost as cal
    words = {"the": 1, "make": 1, "table": 2, "troubled": 2, "called": 1, "jumped": 1, "wanted": 2, "boxes": 2,
             "makes": 1, "piano": 3, "nation": 2, "curious": 3, "precious": 2, "actual": 3, "being": 2, "likely": 2,
             "trembling": 3, "tongue": 1, "player": 2, "played": 1, "pigeon": 2, "video": 3, "yes": 1, "every": 2,
             "conversations": 4, "extraordinary": 5, "isn't": 2, "don't": 1, "it'll": 2, "Alice's": 3, "well-known": 2}
    assert {w: cal.word_syllables(w) for w in words} == words
    assert cal.syllables("It's 1984, and the train leaves at 7:45.") == 16
    assert cal.syllables("The FBI and NASA both called.") == 9 and cal.syllables("Room No. 5, Dr. Hardin.") == 8
    assert cal.pause_split("No. Not now. Not ever.") == (2, 0) and cal.pause_split("Feet under the bar — there. Every one.") == (1, 1)
    assert cal.pause_split("It costs 3.5 credits, Mr. Hardin.") == (0, 1)


def test_calibrate_fit_solves_default_wpm(tmp_path):
    """按时间尺现行结构造样本(词速 150):拟合出的默认词速回到 150,现行默认 / 校准前 130 的误差各自报告。"""
    import calibrate_time_cost as cal
    texts = ["What is the use of a book without pictures or conversations?", "Hush, Alice dear.",
             "No. Not now. Not ever.", "It's 1984, and the train leaves at 7:45.", "Off with her head! Off with his head!",
             "Extraordinary, isn't it? Absolutely, unmistakably extraordinary.", "Who. Are. You?",
             "Consider your verdict, gentlemen of the jury — carefully, deliberately, and without hurry.",
             "I can't explain myself, I'm afraid, sir, because I'm not myself, you see.", "Yes, please.",
             "Gate 47 closes at 11:15. After that, it's a long wait for the next shuttle.", "Mary Ann! Fetch me a fan — quick!"]
    samples = [{"id": f"en{i:03d}", "project": "nope", "speaker": "CHAR-x", "text": t, "duration_s": round(cal._with_wpm(t, 150.0), 4)}
               for i, t in enumerate(texts)]
    sp = tmp_path / "s.json"
    sp.write_text(json.dumps({"schema": cal.SCHEMA, "provider": "fake", "model": "fake", "samples": samples}), encoding="utf-8")
    rep = tmp_path / "r.json"
    assert cal.main(["fit", "--samples", str(sp), "--report", str(rep)]) == 0
    r = json.loads(rep.read_text(encoding="utf-8"))
    assert abs(r["default_wpm"]["least_squares"] - 150) < 0.5 and abs(r["default_wpm"]["median_unbiased"] - 150) < 0.5
    assert r["errors"]["median_unbiased_default"]["mae_s"] < 0.01 and r["errors"]["legacy_130wpm_default"]["bias_s"] < 0
    assert set(r["diagnostics"]) == {"words_free", "syllables_free", "words_and_syllables_free"} and len(r["lines"]) == len(texts)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="需要 ffmpeg")
def test_dialogue_audible_merges_sentence_pauses(tmp_path, capsys):
    """英文碎句(句间停顿 0.65s)不再被切成几段误报整句缺失;真的没声音照样 FAIL。"""
    import check_dialogue_audible as cda
    base = tmp_path / "p"
    clips = base / "assets" / "clips" / "ep01"
    clips.mkdir(parents=True)
    tone = "sine=frequency=180:duration=0.9"
    gap = "anullsrc=r=16000:cl=mono,atrim=duration=0.65"
    fc = ("[0][1][2][3][4][5]concat=n=6:v=0:a=1[a]")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono,atrim=duration=0.5",
                    "-f", "lavfi", "-i", tone, "-f", "lavfi", "-i", gap, "-f", "lavfi", "-i", tone,
                    "-f", "lavfi", "-i", gap, "-f", "lavfi", "-i", tone + ",apad=pad_dur=6",
                    "-filter_complex", fc, "-map", "[a]", "-c:a", "aac", str(clips / "grp001.mp4")], check=True)
    line = {"speaker": "CHAR-x", "text": "No ground. Not anywhere. It's all city.", "est_duration_s": 3.5}
    sl = {"shots": [{"shot_id": "sh001", "duration_s": 4.0, "is_dialogue": True, "dialogue_lines": [line]},
                    {"shot_id": "sh002", "duration_s": 5.0, "is_dialogue": True,
                     "dialogue_lines": [{"speaker": "CHAR-x", "text": "Silence everywhere, nothing at all.", "est_duration_s": 3.5}]}],
          "generation_groups": [{"group_id": "grp001", "shots": ["sh001", "sh002"]}]}
    (base / "directing" / "ep01").mkdir(parents=True)
    (base / "directing" / "ep01" / "shot_list.json").write_text(json.dumps(sl), encoding="utf-8")
    argv = ["--project", "p", "--ep", "ep01", "--out-root", str(base), "--no-report", "--pad", "0.2"]
    assert cda.main(argv + ["--merge-gap", "0.5"]) == 1               # 旧合并间隔:三段各 0.9s,sh001 误报
    out = capsys.readouterr().out
    assert "sh001" in out and "sh002" in out
    assert cda.main(argv) == 1                                        # 默认 0.8s:sh001 通过,sh002(无声)仍 FAIL
    out = capsys.readouterr().out
    assert "FAIL grp001/sh002" in out and "FAIL grp001/sh001" not in out


def test_live_split_packs_english_by_whole_piece():
    from modules import live_stream as ls
    text = "Look down. That's just the roof. Nine-tenths is under there. Most people never come up here."
    pieces = ls._split_script(text, 8)
    assert " ".join(pieces) == text
    assert all(ls._speech_est_s(p) <= 8 for p in pieces) and len(pieces) < 4     # 合并时起止只算一次
    assert ls._speech_est_s("哪吒,来!") == 3 * 0.25 + 2 * 0.07                     # 中文口径不变
