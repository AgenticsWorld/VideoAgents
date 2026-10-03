# -*- coding: utf-8 -*-
"""片头平移与机检 intro_offset_ok(finalize_episode.py,WORKFLOW §9B)。

前科:片头接到正片之前后,字幕 / 外挂声轨没有跟着后移一个片头时长(2026-07-18 thedoor ep01–06 字幕整体偏早)。
这里锁两件事:① 平移本身算得对(SRT/ASS 文本、各段起点);② 机检能抓住「没平移」——字幕原样拷贝、声轨仍从 0 秒起。
素材全部用 lavfi 合成,不依赖真实项目。
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "modules"))
sys.path.insert(0, str(ROOT / "code"))
sys.path.insert(0, str(ROOT))

import finalize_episode as fe  # noqa: E402

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg/ffprobe")

INTRO_S = 2.0
CUT_S = 12.0      # 互相关窗口 20 s、要求窗口内至少一半有声 → 声轨须 ≥ 10 s 才会真正实测

SRT = """1
00:00:01,000 --> 00:00:02,500
第一句

2
00:00:03,000 --> 00:00:04,250 X1:40 X2:600 Y1:20 Y2:50
第二句,分两行
写着 00:00:09,000 的正文不是时间行

3
00:59:59,900 --> 01:00:00,400
跨小时
"""

ASS = """[Script Info]
Title: t

[V4+ Styles]
Format: Name, Fontname, Fontsize
Style: Default,Arial,48

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:02.50,Default,,0,0,0,,你好,世界
Comment: 0,0:00:03.00,0:00:04.25,Default,,0,0,0,,备注
Dialogue: 1,0:59:59.90,1:00:00.40,Default,,0,0,0,,{\\an8}跨小时
"""


# ---------------------------------------------------------------- 时间码与文本平移(纯函数)

@pytest.mark.parametrize("text,seconds", [
    ("00:00:01,000", 1.0),
    ("01:02:03,456", 3723.456),
    ("1:02:03.5", 3723.5),          # 点分隔、不足三位的毫秒按左对齐补零
    ("00:00:00,04", 0.04),
])
def test_srt_timecode_parse(text, seconds):
    assert fe.srt_to_s(text) == pytest.approx(seconds, abs=1e-9)


def test_srt_timecode_rejects_garbage():
    with pytest.raises(ValueError):
        fe.srt_to_s("1:02")


@pytest.mark.parametrize("seconds,text", [
    (0.0, "00:00:00,000"),
    (3723.456, "01:02:03,456"),
    (59.9996, "00:01:00,000"),      # 进位到下一秒/分,不出现 ,1000
    (-3.0, "00:00:00,000"),         # 负时刻钳到 0,不产出非法时间码
])
def test_srt_timecode_format(seconds, text):
    assert fe.s_to_srt(seconds) == text


def test_shift_srt_moves_every_cue_and_nothing_else():
    out = fe.shift_srt_text(SRT, 6.375)
    lines, src = out.splitlines(), SRT.splitlines()
    assert len(lines) == len(src)
    assert lines[1] == "00:00:07,375 --> 00:00:08,875"
    assert lines[5] == "00:00:09,375 --> 00:00:10,625 X1:40 X2:600 Y1:20 Y2:50"   # 时间行尾部的定位参数保留
    assert lines[10] == "01:00:06,275 --> 01:00:06,775"
    changed = [i for i, (a, b) in enumerate(zip(src, lines)) if a != b]
    assert changed == [1, 5, 10]                                                  # 序号、正文、空行一字不动


def test_shift_srt_is_additive():
    once = fe.shift_srt_text(fe.shift_srt_text(SRT, 1.5), 2.25)
    assert once == fe.shift_srt_text(SRT, 3.75)


def test_shift_ass_moves_events_only():
    out = fe.shift_ass_text(ASS, 6.37)
    lines, src = out.splitlines(), ASS.splitlines()
    assert len(lines) == len(src)
    assert "Dialogue: 0,0:00:07.37,0:00:08.87,Default,,0,0,0,,你好,世界" in lines    # 正文里的逗号不影响字段切分
    assert "Comment: 0,0:00:09.37,0:00:10.62,Default,,0,0,0,,备注" in lines
    assert "Dialogue: 1,1:00:06.27,1:00:06.77,Default,,0,0,0,,{\\an8}跨小时" in lines
    untouched = [i for i, line in enumerate(src) if not line.startswith(("Dialogue:", "Comment:"))]
    assert all(lines[i] == src[i] for i in untouched)                             # Style / Format / 段头不动


def test_parse_reads_bom_and_crlf(tmp_path):
    srt = tmp_path / "subtitles.srt"
    srt.write_bytes(b"\xef\xbb\xbf" + SRT.replace("\n", "\r\n").encode("utf-8"))
    assert fe.parse_srt(srt) == [(1.0, 2.5), (3.0, 4.25), (3599.9, 3600.4)]
    ass = tmp_path / "subtitles.ass"
    ass.write_bytes(b"\xef\xbb\xbf" + ASS.replace("\n", "\r\n").encode("utf-8"))
    assert fe.parse_ass(ass) == [(1.0, 2.5), (3.0, 4.25), (3599.9, 3600.4)]


# ---------------------------------------------------------------- 段起点

def _seg(name, dur):
    return {"name": name, "dur": dur}


def test_offsets_cut_starts_after_intro():
    offs, total = fe.offsets_of([_seg("intro", 6.375), _seg("cut", 120.0), _seg("outro", 4.0), _seg("teaser", 10.5)])
    assert offs == {"intro": 0.0, "cut": 6.375, "outro": 126.375, "teaser": 130.375}
    assert total == pytest.approx(140.875)


def test_offsets_without_intro_is_zero():
    offs, total = fe.offsets_of([_seg("cut", 120.0), _seg("outro", 4.0)])
    assert offs["cut"] == 0.0 and total == pytest.approx(124.0)


# ---------------------------------------------------------------- shift 落盘

def _subs(proj: Path, srt=SRT, ass=ASS):
    ed = proj / "edit" / "ep01"
    ed.mkdir(parents=True, exist_ok=True)
    (ed / "subtitles.srt").write_text(srt, encoding="utf-8")
    if ass is not None:
        (ed / "subtitles.ass").write_text(ass, encoding="utf-8")
    return ed


def test_do_shift_writes_final_subtitles(tmp_path):
    ed = _subs(tmp_path)
    done = fe.do_shift(tmp_path, "ep01", 6.375)
    assert [(p.name, n) for p, n in done] == [("subtitles_final.srt", 3), ("subtitles_final.ass", 3)]
    base, fin = fe.parse_srt(ed / "subtitles.srt"), fe.parse_srt(ed / "subtitles_final.srt")
    assert fin == [pytest.approx((a + 6.375, b + 6.375), abs=1e-3) for a, b in base]
    base, fin = fe.parse_ass(ed / "subtitles.ass"), fe.parse_ass(ed / "subtitles_final.ass")
    assert fin == [pytest.approx((a + 6.375, b + 6.375), abs=0.011) for a, b in base]   # ASS 精度到厘秒
    assert (ed / "subtitles.srt").read_text(encoding="utf-8") == SRT                    # 正片基准文件不被改写


def test_do_shift_without_intro_is_plain_copy(tmp_path):
    ed = _subs(tmp_path, ass=None)
    fe.do_shift(tmp_path, "ep01", 0.0)
    assert (ed / "subtitles_final.srt").read_text(encoding="utf-8") == SRT
    assert not (ed / "subtitles_final.ass").exists()


def test_do_shift_without_subtitles_is_noop(tmp_path):
    (tmp_path / "edit" / "ep01").mkdir(parents=True)
    assert fe.do_shift(tmp_path, "ep01", 6.0) == []


# ---------------------------------------------------------------- 逐条 cue 对位

def _cue_check(base, fin, intro_s, fn=None):
    got = []
    fe._check_cues("subtitle_offset_all_cues", lambda name, ok, detail="", warn=False: got.append((ok, warn, detail)),
                   base, fin, intro_s, fn)
    assert len(got) == 1
    return got[0]


BASE_CUES = [(1.0, 2.5), (3.0, 4.25), (100.0, 101.0)]


def test_cues_shifted_pass():
    ok, warn, _ = _cue_check(BASE_CUES, [(a + 6.375, b + 6.375) for a, b in BASE_CUES], 6.375)
    assert ok and not warn


def test_cues_within_tolerance_pass():
    fin = [(a + 6.375 + 0.19, b + 6.375 - 0.19) for a, b in BASE_CUES]
    assert _cue_check(BASE_CUES, fin, 6.375)[0]


def test_cues_copied_unshifted_fail():
    ok, _warn, detail = _cue_check(BASE_CUES, list(BASE_CUES), 6.375)
    assert not ok and "6375ms" in detail and "未平移" in detail


def test_single_late_cue_fails_and_is_named():
    fin = [(a + 6.375, b + 6.375) for a, b in BASE_CUES]
    fin[1] = (fin[1][0] + 0.5, fin[1][1] + 0.5)
    ok, _warn, detail = _cue_check(BASE_CUES, fin, 6.375)
    assert not ok and "cue #2" in detail


def test_cue_count_mismatch_fail():
    ok, _warn, detail = _cue_check(BASE_CUES, [(a + 6.375, b + 6.375) for a, b in BASE_CUES[:2]], 6.375)
    assert not ok and "条数不一致" in detail


def test_empty_base_is_warn_not_fail():
    ok, warn, _ = _cue_check([], [], 6.375)
    assert ok and warn


def test_cues_follow_mapping_fn():
    # 正片带时长编辑表时按映射对位:4 s 处插了 1 s 黑场,其后的 cue 多移 1 s
    fn = lambda t: t + 6.375 + (1.0 if t >= 4.0 else 0.0)  # noqa: E731
    fin = [(fn(a), fn(b)) for a, b in BASE_CUES]
    assert _cue_check(BASE_CUES, fin, 6.375, fn)[0]
    assert not _cue_check(BASE_CUES, [(a + 6.375, b + 6.375) for a, b in BASE_CUES], 6.375, fn)[0]


# ---------------------------------------------------------------- 整链:assemble → shift → check(合成素材)

def _video(path: Path, color: str, seconds: float, audio: bool):
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s=320x180:r=24:d={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=f=440:d={seconds}", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True)


def _build(proj: Path, intro_audio=True, settings=None):
    """片头 2 s + 正片 12 s(画面无音轨)+ 外挂声轨 12 s 粉噪(非周期信号,互相关峰唯一)+ 正片基准字幕。"""
    ed = proj / "edit" / "ep01"
    _video(ed / "intro_outro" / "intro.mp4", "red", INTRO_S, intro_audio)
    _video(ed / "cut_v1.mp4", "blue", CUT_S, False)
    wav = proj / "assets" / "audio" / "final" / "ep01.wav"
    wav.parent.mkdir(parents=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"anoisesrc=d={CUT_S}:c=pink:r=48000:a=0.5:seed=7",
                    "-ac", "2", str(wav)], check=True)
    (ed / "subtitles.srt").write_text("1\n00:00:01,000 --> 00:00:02,500\n第一句\n\n2\n00:00:09,000 --> 00:00:11,000\n第二句\n",
                                      encoding="utf-8")
    if settings is not None:
        (proj / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    return ed


def _layout(proj: Path):
    notes = []
    return fe.resolve_layout(proj, "ep01", ["intro", "cut", "outro", "teaser"], "cut_v1.mp4", None, notes), notes


def _items(proj: Path):
    led = json.loads((proj / "edit" / "ep01" / "final_layout.json").read_text(encoding="utf-8"))
    return led, {c["name"]: c for c in led["check"]["items"]}


@pytest.fixture(scope="module")
def assembled(tmp_path_factory):
    if not HAS_FFMPEG:
        pytest.skip("需要 ffmpeg/ffprobe")
    proj = tmp_path_factory.mktemp("finalize") / "demo"
    ed = _build(proj)
    segs, _ = _layout(proj)
    fe.do_assemble(proj, "ep01", segs, ed / "final.mp4", preset="ultrafast")
    return proj, ed, segs


@needs_ffmpeg
def test_layout_measures_intro_as_cut_offset(assembled):
    _proj, _ed, segs = assembled
    assert [s["name"] for s in segs] == ["intro", "cut"]          # 不存在的 outro / teaser 自动跳过
    offs, total = fe.offsets_of(segs)
    assert offs["cut"] == pytest.approx(INTRO_S, abs=0.05) and total == pytest.approx(INTRO_S + CUT_S, abs=0.1)
    cut = segs[1]
    assert cut["audio"].endswith("ep01.wav") and cut["dur"] == pytest.approx(CUT_S, abs=0.05)


@needs_ffmpeg
def test_assembled_episode_passes_check(assembled):
    proj, ed, segs = assembled
    fe.do_shift(proj, "ep01", fe.offsets_of(segs)[0]["cut"])
    assert fe.do_check(proj, "ep01", segs, [], ed / "final.mp4")
    led, items = _items(proj)
    assert led["check"]["result"] == "PASS" and led["cut_offset_s"] == pytest.approx(INTRO_S, abs=0.05)
    assert [s["name"] for s in led["segments"]] == ["intro", "cut"] and led["segments"][1]["start_s"] == led["cut_offset_s"]
    for name in ("final_duration_layout", "subtitles_final_present", "subtitle_offset_all_cues",
                 "subtitle_within_final", "audio_offset_measured"):
        assert items[name]["status"] == "PASS", items[name]
    first = fe.parse_srt(ed / "subtitles_final.srt")[0]
    assert first == pytest.approx((1.0 + INTRO_S, 2.5 + INTRO_S), abs=0.05)


@needs_ffmpeg
def test_audio_lag_equals_intro_in_every_window(assembled):
    _proj, ed, segs = assembled
    cut = segs[1]
    res = fe.measure_audio_lag(ed / "final.mp4", cut["audio"], cut["a_dur"], INTRO_S, win_s=2.0)
    assert [r["ref_t"] for r in res] == [0.0, 5.0, 10.0]          # 首 / 中 / 尾三个窗口
    for r in res:
        assert r["lag_s"] == pytest.approx(INTRO_S, abs=0.08) and r["ncc"] > 0.5, r


@needs_ffmpeg
def test_check_fails_when_subtitles_copied_unshifted(assembled):
    proj, ed, segs = assembled
    shutil.copyfile(ed / "subtitles.srt", ed / "subtitles_final.srt")      # 前科:直接把正片基准 SRT 当成片字幕
    try:
        assert not fe.do_check(proj, "ep01", segs, [], ed / "final.mp4", write=False)
    finally:
        fe.do_shift(proj, "ep01", fe.offsets_of(segs)[0]["cut"])
    assert fe.do_check(proj, "ep01", segs, [], ed / "final.mp4", write=False)


@needs_ffmpeg
def test_check_fails_when_final_subtitles_missing(assembled):
    proj, ed, segs = assembled
    (ed / "subtitles_final.srt").unlink(missing_ok=True)
    try:
        assert not fe.do_check(proj, "ep01", segs, [], ed / "final.mp4", write=False)
    finally:
        fe.do_shift(proj, "ep01", fe.offsets_of(segs)[0]["cut"])


@needs_ffmpeg
def test_check_fails_when_audio_not_shifted(assembled):
    # 画面拼了片头,声轨却仍从 0 秒起(手工 ffmpeg 忘了 -itsoffset 的典型产物)
    proj, ed, segs = assembled
    fe.do_shift(proj, "ep01", fe.offsets_of(segs)[0]["cut"])
    bad = ed / "ep01_final_bad.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(ed / "final.mp4"), "-i", segs[1]["audio"],
                    "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", str(bad)], check=True)
    try:
        assert not fe.do_check(proj, "ep01", segs, [], bad)
        _led, items = _items(proj)
        assert items["audio_offset_measured"]["status"] == "FAIL"
        assert "没有随之后移" in items["audio_offset_measured"]["detail"]
        assert items["subtitle_offset_all_cues"]["status"] == "PASS"       # 字幕没问题,只有声轨项报错
    finally:
        bad.unlink()
        fe.do_check(proj, "ep01", segs, [], ed / "final.mp4")              # 台账恢复为正确成片的结论


@needs_ffmpeg
def test_check_fails_when_intro_dropped_from_final(assembled):
    # 成片其实没拼片头(直接拿正片 + 声轨封装):总时长对不上,声轨滞后实测为 0
    proj, ed, segs = assembled
    fe.do_shift(proj, "ep01", fe.offsets_of(segs)[0]["cut"])
    bad = ed / "ep01_final_nointro.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(ed / "cut_v1.mp4"), "-i", segs[1]["audio"],
                    "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", str(bad)], check=True)
    try:
        assert not fe.do_check(proj, "ep01", segs, [], bad, write=False)
    finally:
        bad.unlink()


@needs_ffmpeg
def test_silent_intro_still_offsets_audio(tmp_path):
    # 片头没有音轨时补静音,正片声轨照样后移一个片头
    proj = tmp_path / "demo"
    ed = _build(proj, intro_audio=False)
    segs, _ = _layout(proj)
    assert segs[0]["has_audio"] is False
    fe.do_assemble(proj, "ep01", segs, ed / "final.mp4", preset="ultrafast")
    fe.do_shift(proj, "ep01", fe.offsets_of(segs)[0]["cut"])
    assert fe.do_check(proj, "ep01", segs, [], ed / "final.mp4")
    _led, items = _items(proj)
    assert items["audio_offset_measured"]["status"] == "PASS", items["audio_offset_measured"]


@needs_ffmpeg
def test_disabled_intro_means_zero_offset(tmp_path):
    proj = tmp_path / "demo"
    ed = _build(proj, settings={"packaging": {"intro_enabled": False}})
    segs, notes = _layout(proj)
    assert [s["name"] for s in segs] == ["cut"] and any("已禁用" in n for n in notes)
    assert fe.offsets_of(segs)[0]["cut"] == 0.0
    fe.do_shift(proj, "ep01", 0.0)
    assert (ed / "subtitles_final.srt").read_text(encoding="utf-8") == (ed / "subtitles.srt").read_text(encoding="utf-8")


@needs_ffmpeg
def test_placement_mismatch_is_warn_and_measured_wins(tmp_path):
    # placement.json 声明的片头时长写错:以实测为准、只 WARN,不影响平移量
    proj = tmp_path / "demo"
    ed = _build(proj)
    (ed / "intro_outro" / "placement.json").write_text(json.dumps({"intro": {"file": "intro.mp4", "duration_s": 5.0}}),
                                                       encoding="utf-8")
    segs, _ = _layout(proj)
    assert segs[0]["declared"] == 5.0 and fe.offsets_of(segs)[0]["cut"] == pytest.approx(INTRO_S, abs=0.05)
    fe.do_assemble(proj, "ep01", segs, ed / "final.mp4", preset="ultrafast")
    fe.do_shift(proj, "ep01", fe.offsets_of(segs)[0]["cut"])
    assert fe.do_check(proj, "ep01", segs, [], ed / "final.mp4")
    _led, items = _items(proj)
    assert items["placement_declared_match"]["status"] == "WARN"
