# -*- coding: utf-8 -*-
"""集尾收束(episode_close,2026-09-25):契约 / 生效解析 / render_transitions plan+render+check / finalize 声轨淡出(合成 lavfi 小视频,不依赖真实项目)。"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_TMP = tempfile.mkdtemp(prefix="va-close-")
os.environ.setdefault("VIDEOAGENTS_DATA_DIR", _TMP)
sys.path.insert(0, str(ROOT / "modules"))
sys.path.insert(0, str(ROOT / "code"))
sys.path.insert(0, str(ROOT))

import check_generation_groups as cgg  # noqa: E402
from modules import transition_design as td  # noqa: E402

HAS_FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
DATA = Path(os.environ["VIDEOAGENTS_DATA_DIR"])


def _clip(path: Path, color: str, seconds: float = 2.0, audio=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s=320x180:r=24:d={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=f=440:d={seconds}", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", str(path)]
    subprocess.run(cmd, check=True)


def _gray(path: Path, t: float) -> float:
    out = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1", "-vf", "scale=16:9,format=gray",
                          "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    return sum(out) / max(1, len(out))


def _rms(path: Path, t0: float, t1: float) -> float:
    import struct
    out = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t0:.3f}", "-t", f"{t1 - t0:.3f}", "-i", str(path), "-vn", "-ac", "1", "-ar", "8000",
                          "-f", "s16le", "-"], capture_output=True, check=True).stdout
    n = len(out) // 2
    if not n:
        return 0.0
    vals = struct.unpack(f"<{n}h", out[:n * 2])
    return (sum(v * v for v in vals) / n) ** 0.5 / 32768.0


def _project(name: str, settings: dict, episode_close=None, audio=True):
    base = DATA / "projects" / name
    if base.exists():
        shutil.rmtree(base)
    (base / "directing" / "ep01").mkdir(parents=True)
    (base / "edit" / "ep01").mkdir(parents=True)
    groups = [("grp001", "red"), ("grp002", "green")]   # 末组用绿(亮度 ~150),便于区分「仍是内容帧」与近黑
    sl = {"episode": "ep01", "budget_s": 4, "generation_groups": [
        {"group_id": g, "scene_id": "SCN-0001", "scene_no": "S01", "shots": [f"sh{i + 1:03d}"], "total_duration_s": 2} for i, (g, _c) in enumerate(groups)],
        "shots": [{"shot_id": f"sh{i + 1:03d}", "duration_s": 2} for i in range(2)]}
    if episode_close is not None:
        sl["episode_close"] = episode_close
    (base / "directing" / "ep01" / "shot_list.json").write_text(json.dumps(sl, ensure_ascii=False))
    (base / "settings.json").write_text(json.dumps({"transitions": settings}, ensure_ascii=False))
    tracks, cum = [], 0.0
    for g, c in groups:
        _clip(base / "assets" / "clips" / "ep01" / f"{g}.mp4", c, audio=audio)
        tracks.append({"group_id": g, "src": f"assets/clips/ep01/{g}.mp4", "in": 0.0, "out": 2.0})
        cum += 2
    (base / "edit" / "ep01" / "timeline.json").write_text(json.dumps({"tracks": {"video": tracks}}))
    # 粗成片 = 两组 concat(流拷贝)
    lst = base / "edit" / "ep01" / ".cat.txt"
    lst.write_text("".join(f"file '{(base / 'assets' / 'clips' / 'ep01' / (g + '.mp4')).resolve()}'\n" for g, _c in groups))
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(base / "edit" / "ep01" / "cut_v1.mp4")], check=True)
    return base


# ---------------------------------------------------------------- 契约

def test_contract_normalize_and_check():
    t = cgg.normalize_episode_close({"type": "fade_black", "duration_s": 1, "hold_s": 0.5})
    assert t["duration_s"] == 1.0 and t["hold_audio"] == "fade" and cgg.check_episode_close(t) == []
    assert cgg.check_episode_close(cgg.normalize_episode_close({"type": "fade_black", "duration_s": 5}))
    assert cgg.check_episode_close(cgg.normalize_episode_close({"type": "nope"}))
    cut = cgg.normalize_episode_close({"type": "cut_black"})
    assert cut["duration_s"] == 0.0 and cut["hold_s"] == 1.0 and cut["hold_audio"] == "mute" and cgg.check_episode_close(cut) == []
    assert cgg.check_episode_close(cgg.normalize_episode_close({"type": "cut_black", "hold_s": 0}))
    assert cgg.normalize_episode_close(None) is None
    assert cgg.normalize_episode_close({"type": "hard_cut"})["type"] == "hard_cut"
    errs = cgg.check_transitions({"generation_groups": [{"group_id": "g1"}], "episode_close": {"type": "fade_white", "duration_s": 0.1}})
    assert any("transition_close_valid" in e for e in errs)
    assert not [e for e in cgg.check_transitions({"generation_groups": [{"group_id": "g1"}]}) if "close" in e]


def test_settings_default_and_effective(tmp_path):
    st = td.normalize_settings({"mode": "minimal"})
    assert st["episode_close"] == {"type": "fade_black", "duration_s": 1.0, "hold_s": 0.5, "hold_audio": "fade"}
    assert td.normalize_settings({"mode": "classic", "episode_close": {"type": "hard_cut"}})["episode_close"] == {"type": "hard_cut"}
    with pytest.raises(ValueError):
        td.normalize_settings({"mode": "classic", "episode_close": {"type": "fade_black", "duration_s": 9}})
    base = tmp_path / "p"
    (base / "directing" / "ep01").mkdir(parents=True)
    (base / "settings.json").write_text(json.dumps({"transitions": {"mode": "classic", "episode_close": {"type": "cut_black", "hold_s": 1.5}}}))
    sl = {"generation_groups": [{"group_id": "g1"}]}
    (base / "directing" / "ep01" / "shot_list.json").write_text(json.dumps(sl))
    eff = td.effective_episode_close(base, "ep01")
    assert eff["type"] == "cut_black" and eff["hold_s"] == 1.5 and eff["hold_audio"] == "mute" and eff["source"].startswith("settings")
    # shot_list 覆盖:显式 hard_cut = 不处理;fade_white 补默认
    assert td.set_episode_close(base, "ep01", {"type": "hard_cut"}) is None
    e2 = td.set_episode_close(base, "ep01", {"type": "fade_white"})
    assert e2["type"] == "fade_white" and e2["duration_s"] == 1.0 and e2["hold_s"] == 0.5 and e2["source"] == "user"   # 跨族:按淡出族默认
    assert td.set_episode_close(base, "ep01", None)["type"] == "cut_black"      # 删键 = 跟随项目
    with pytest.raises(ValueError):
        td.set_episode_close(base, "ep01", {"type": "cut_black", "hold_s": 0})
    assert td.close_fingerprint({"type": "fade_black", "duration_s": 1.0, "hold_s": 0.5, "hold_audio": "fade", "source": "a"}) == \
        td.close_fingerprint({"type": "fade_black", "duration_s": 1.0, "hold_s": 0.5, "hold_audio": "fade", "source": "b", "reason": "x"})


# ---------------------------------------------------------------- plan

def test_plan_appends_close_entry(tmp_path):
    import render_transitions as rt
    base = tmp_path / "p"
    (base / "directing" / "ep01").mkdir(parents=True)
    (base / "settings.json").write_text(json.dumps({"transitions": {"mode": "classic"}}))
    sl = {"generation_groups": [{"group_id": "g1", "shots": ["s1"]}, {"group_id": "g2", "shots": ["s2"]}]}
    tl = {"tracks": {"video": [{"group_id": "g1", "in": 0, "out": 2, "timeline_in_s": 0, "timeline_out_s": 2},
                               {"group_id": "g2", "in": 0, "out": 3, "timeline_in_s": 2, "timeline_out_s": 5}]}}
    entries, policy, problems = rt.make_plan(sl, tl, base)
    assert not problems
    close = [e for e in entries if rt._is_close(e)]
    assert len(close) == 1 and close[0]["from_group"] == "g2" and close[0]["to_group"] is None
    assert close[0]["type"] == "fade_black" and close[0]["hold_s"] == 0.5 and close[0]["cut_time_s"] == 5.0 and close[0]["at_shot"] == "s2->episode_close"
    assert policy["episode_close"]["type"] == "fade_black" and policy["boundaries"] == 1 and policy["renderable"] == 0
    assert rt.pad_ops(entries, 24) == []                       # 停留不进 timemap
    sl["episode_close"] = {"type": "hard_cut"}
    entries, policy, _ = rt.make_plan(sl, tl, base)
    assert not [e for e in entries if rt._is_close(e)] and policy["episode_close"] is None


# ---------------------------------------------------------------- render + check + finalize

@pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg/ffprobe")
@pytest.mark.parametrize("close,expect_fade", [
    ({"type": "fade_black", "duration_s": 0.5, "hold_s": 0.5, "hold_audio": "fade"}, True),
    ({"type": "cut_black", "hold_s": 1.0, "hold_audio": "mute"}, False),
])
def test_render_check_finalize(close, expect_fade):
    import render_transitions as rt
    import finalize_episode as fe
    base = _project("closetest_" + close["type"], {"mode": "minimal", "episode_close": close})
    src, out = base / "edit" / "ep01" / "cut_v1.mp4", base / "edit" / "ep01" / "cut_v2.mp4"
    res = rt.do_render(base, "ep01", src, out)
    assert res == out and out.is_file()
    fps = 24
    n_src, n_out = rt.count_frames_of(str(src)), rt.count_frames_of(str(out))
    hold_f = round(close["hold_s"] * fps)
    assert n_out == n_src + hold_f
    od = rt.probe_duration(out)
    # 末帧近黑;停留中点近黑;全黑之前 1 帧:淡出类近黑、切黑类仍是蓝(源末帧)
    assert _gray(out, od - 1 / fps) < 24
    assert _gray(out, od - close["hold_s"] / 2) < 24
    before = _gray(out, od - close["hold_s"] - 1 / fps)
    assert (before < 24) if expect_fade else (before > 40)
    # 源 cut 声轨延长到画面长,停留段静音
    ok, results = rt.do_check(base, "ep01", src, out)
    byname = {r["check"]: r for r in results}
    assert ok, [r for r in results if r["result"] == "FAIL"]
    assert byname["duration_as_planned"]["result"] == "PASS" and byname["audio_stream_intact"]["result"] == "PASS"
    assert "duration_unchanged" not in byname
    assert _rms(out, od - close["hold_s"] + 0.1, od - 0.05) < 0.01
    led = json.loads((base / "edit" / "ep01" / "transitions_render.json").read_text())
    assert led["episode_close"]["type"] == close["type"] and abs(led["episode_close"]["fade_end_s"] - (od - close["hold_s"])) < 0.05
    assert led["timemap"]["ops"] == [] and led["pads"] == []
    assert any(w["type"] == close["type"] for w in led["black_frame_whitelist"])
    # finalize:外挂声轨(比画面短 hold_s,与真实混音一致)在全黑时刻淡出/切断,停留段静音
    wav = base / "assets" / "audio" / "final" / "ep01.wav"
    wav.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=330:d=4", "-ac", "2", "-ar", "48000", str(wav)], check=True)
    notes = []
    segs = fe.resolve_layout(base, "ep01", ["cut"], "cut_v2.mp4", None, notes)
    assert segs[0]["episode_close"]["type"] == close["type"]
    assert not any("相差" in n for n in notes)                 # 画面比声轨长恰好 hold_s,不再告警
    final = base / "edit" / "ep01" / "final.mp4"
    fe.do_assemble(base, "ep01", segs, final)
    fd = rt.probe_duration(final)
    assert abs(fd - od) < 0.1
    black_at = od - close["hold_s"]
    ref = _rms(wav, 1.0, 1.5)                                        # 外挂声轨自身响度作基准
    assert ref > 0.01
    assert _rms(final, black_at + 0.05, fd - 0.05) < ref * 0.05      # 停留段静音
    assert _rms(final, black_at - 1.5, black_at - 1.0) > ref * 0.7   # 淡出/切断之前有声
    if expect_fade:
        assert _rms(final, black_at - 0.12, black_at - 0.02) < ref * 0.45   # 淡出末尾已明显变小
    else:
        assert _rms(final, black_at - 0.12, black_at - 0.03) > ref * 0.7    # 切黑:到黑前一刻仍满响


@pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg/ffprobe")
def test_hard_cut_close_keeps_old_behaviour():
    import render_transitions as rt
    base = _project("closetest_none", {"mode": "minimal", "episode_close": {"type": "hard_cut"}})
    src, out = base / "edit" / "ep01" / "cut_v1.mp4", base / "edit" / "ep01" / "cut_v2.mp4"
    assert rt.do_render(base, "ep01", src, out) is None and not out.exists()     # 全片硬切 + 不收束 = 不产出 cut_v2
    ok, _ = rt.do_check(base, "ep01", src, out)
    assert ok


@pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg/ffprobe")
def test_preview_episode_close():
    import render_transitions as rt
    base = _project("closetest_prev", {"mode": "minimal"})
    outs = rt.do_preview(base, "ep01", base / "edit" / "ep01" / "cut_v1.mp4", {"episode_close"})
    assert len(outs) == 1 and outs[0].name == "preview.mp4" and outs[0].parent.name == "episode_close"
    assert rt.count_frames_of(str(outs[0])) == 48 + 12          # 末组尾 2s + 停留 0.5s
    assert _gray(outs[0], 2.4) < 24
    pl = td.payload(base, "ep01")
    assert pl["episode_close"]["effective"]["type"] == "fade_black" and pl["episode_close"]["thumbs"]["preview"]
    assert pl["episode_close"]["render_state"] == "pending"
