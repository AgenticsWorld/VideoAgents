# -*- coding: utf-8 -*-
"""字幕烧录宿主 CLI(issue #133):code/burn_subtitles.py。合成 lavfi 小片,不依赖真实项目 / libass。"""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))

import burn_subtitles as bs  # noqa: E402

HAS_FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg/ffprobe")

SRT = "1\n00:00:00,500 --> 00:00:01,500\n第一句字幕\nSecond line here\n\n2\n00:00:02,500 --> 00:00:03,500\n<i>第二句</i>\n"


def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _project(tmp_path, srt=SRT, base_srt=None, offset=0.0):
    ed = tmp_path / "proj" / "edit" / "ep01"
    ed.mkdir(parents=True)
    # 4s 成片,两条音轨(验证全部流拷贝)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=24:d=4",
                    "-f", "lavfi", "-i", "sine=f=440:d=4", "-f", "lavfi", "-i", "sine=f=660:d=4",
                    "-map", "0:v", "-map", "1:a", "-map", "2:a", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    str(ed / "final.mp4")], check=True)
    (ed / "subtitles_final.srt").write_text(srt, encoding="utf-8")
    if base_srt is not None:
        (ed / "subtitles.srt").write_text(base_srt, encoding="utf-8")
    (ed / "final_layout.json").write_text(json.dumps({"cut_offset_s": offset}), encoding="utf-8")
    return ed


def _main(tmp_path, *args):
    return bs.main([*args, "--project", "proj", "--ep", "ep01", "--out-root", str(tmp_path / "proj")])


def test_parse_subs_srt_and_ass(tmp_path):
    p = tmp_path / "a.srt"
    p.write_text(SRT, encoding="utf-8")
    cues = bs.parse_subs(p)
    assert [(c["start"], c["end"]) for c in cues] == [(0.5, 1.5), (2.5, 3.5)]
    assert cues[0]["text"] == "第一句字幕\nSecond line here" and cues[1]["text"] == "第二句"
    a = tmp_path / "a.ass"
    a.write_text("[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
                 "Dialogue: 0,0:00:01.00,0:00:02.50,Default,,0,0,0,,{\\an2}甲,乙\\N丙\n", encoding="utf-8")
    assert bs.parse_subs(a) == [{"start": 1.0, "end": 2.5, "text": "甲,乙\n丙"}]


@pytest.mark.parametrize("size", [(1920, 1080), (1080, 1920), (1280, 720)])
def test_layout_within_subtitle_soul_limits(tmp_path, size):
    w, h = size
    lay = bs.Layout(w, h)
    cues = [{"start": 0.0, "end": 1.0, "text": "这是一句两行字幕\n第二行也不短"}]
    _, metas = bs.build_track(cues, lay, 2.0, tmp_path / "s")
    m = metas[0][3]
    assert m["lines"] == 2
    assert m["char_h_px"] <= h * 0.04 + 0.5 and m["char_h_px"] <= w * 0.05 + 0.5
    assert 0.02 <= lay.margin / h <= 0.04
    assert h - m["band_top_px"] <= h * 0.12 + 0.5          # 含描边的字幕区 ≤12%
    assert h - m["band_bottom_px"] >= lay.margin - 2 * m["stroke_px"]   # 贴底但不出画


def test_overlong_cue_is_flagged_not_hidden(tmp_path):
    lay = bs.Layout(640, 360)
    long = "很长的一句字幕" * 30
    _, metas = bs.build_track([{"start": 0.0, "end": 1.0, "text": long}], lay, 1.5, tmp_path / "s")
    assert metas[0][3]["lines"] > 2 and metas[0][3]["size"] < lay.size      # 先缩字号,仍超 2 行如实报告


@needs_ffmpeg
def test_burn_copy_keeps_final_and_passes_check(tmp_path):
    ed = _project(tmp_path)
    before = _sha(ed / "final.mp4")
    assert _main(tmp_path, "burn") == 0
    out = ed / "final_sub.mp4"
    assert out.is_file() and _sha(ed / "final.mp4") == before                 # 干净版不动
    assert bs._video_packets(out) == bs._video_packets(ed / "final.mp4")     # 逐帧等长
    auds = [s["codec_name"] for s in bs._streams(out) if s["codec_type"] == "audio"]
    assert auds == ["aac", "aac"]                                             # 两条音轨都在
    led = json.loads((ed / "subtitles_burn.json").read_text(encoding="utf-8"))
    chk = led["outputs"]["final_sub.mp4"]["check"]
    assert chk["result"] == "PASS" and {i["check"] for i in chk["items"]} >= {
        "subs_source_ok", "style_lines_le_2", "style_char_height", "style_bottom_margin", "style_band_height",
        "duration_match", "audio_stream_copy", "first_cue_frame"}
    assert _main(tmp_path, "check") == 0


@needs_ffmpeg
def test_refuses_unshifted_or_wrong_target(tmp_path):
    ed = _project(tmp_path, base_srt=SRT, offset=3.0)        # 有片头而成片基准字幕 = 正片基准 → 未平移
    with pytest.raises(SystemExit, match="未平移"):
        _main(tmp_path, "burn")
    assert not (ed / "final_sub.mp4").exists()
    with pytest.raises(SystemExit, match="不得覆盖"):
        _main(tmp_path, "burn", "--out", "final.mp4")
    with pytest.raises(SystemExit, match="char-pct"):
        _main(tmp_path, "burn", "--char-pct", "5")
    (ed / "subtitles_final.srt").unlink()
    with pytest.raises(SystemExit, match="缺成片基准字幕"):
        _main(tmp_path, "burn")


@needs_ffmpeg
def test_lang_variant_and_overrun(tmp_path):
    ed = _project(tmp_path)
    (ed / "subtitles_final_en.srt").write_text("1\n00:00:00,500 --> 00:00:01,500\nHello there\n", encoding="utf-8")
    assert _main(tmp_path, "burn", "--lang", "en") == 0 and (ed / "final_sub.en.mp4").is_file()
    (ed / "subtitles_final.srt").write_text("1\n00:00:01,000 --> 00:00:09,000\n太长\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="超出成片"):
        _main(tmp_path, "burn")
