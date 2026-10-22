# -*- coding: utf-8 -*-
"""code/voice_f0_check.py:对白组逐说话人声学快检(#148)。合成谐波信号,自包含(tmp_path,不读私有数据、不联网)。"""
import json
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))
sys.path.insert(0, str(ROOT / "modules"))

import voice_f0_check as vf  # noqa: E402

SR = 16000
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要 ffmpeg")


def _tone(f0, dur, amp=0.25):
    """带 5 个谐波、轻微颤音的「浊音」;f0=None 给静音。"""
    t = np.arange(int(SR * dur)) / SR
    if f0 is None:
        return np.zeros_like(t)
    phase = 2 * np.pi * np.cumsum(f0 * (1 + 0.01 * np.sin(2 * np.pi * 5 * t))) / SR
    return amp * sum(np.sin(k * phase) / k for k in range(1, 6))


def _write_wav(path: Path, x):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


def _write_clip(path: Path, x):
    """音频装进 mp4(AAC),和真实组 clip 一样经 ffmpeg 抽轨。"""
    wav = path.with_suffix(".src.wav")
    _write_wav(wav, x)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(wav), "-c:a", "aac", "-b:a", "128k", str(path)], check=True)
    wav.unlink()


def _project(tmp_path, *, shots, refs, clip=None, audio_plan="dialogue", mode=None, meta=None, audio_refs=None):
    """shots=[(shot_id, dur, [(speaker, text), …])];refs={CHAR: f0};clip=波形(None 不出片)。"""
    base = tmp_path / "proj"
    (base / "directing" / "ep01").mkdir(parents=True)
    sl = {"shots": [{"shot_id": sid, "duration_s": dur, "is_dialogue": bool(lines),
                     "dialogue_lines": [{"speaker": s, "text": t} for s, t in lines]} for sid, dur, lines in shots],
          "generation_groups": [{"group_id": "grp001", "shots": [s[0] for s in shots], "audio_plan": audio_plan}]}
    (base / "directing" / "ep01" / "shot_list.json").write_text(json.dumps(sl, ensure_ascii=False))
    if mode:
        (base / "settings.json").write_text(json.dumps({"output": {"dialogue_voice": mode}}))
    ref_paths = []
    for ch, f0 in refs.items():
        rel = f"assets/audio/voice/refs/{ch}_voiceprint.wav"
        _write_wav(base / rel, _tone(f0, 3.0))
        ref_paths.append(rel)
    prompt = {"group_id": "grp001", "audio_plan": audio_plan, "audio_refs": ref_paths if audio_refs is None else audio_refs}
    (base / "assets" / "prompts" / "ep01").mkdir(parents=True)
    (base / "assets" / "prompts" / "ep01" / "grp001.json").write_text(json.dumps(prompt))
    if clip is not None:
        _write_clip(base / "assets" / "clips" / "ep01" / "grp001.mp4", clip)
        if meta is not None:
            (base / "assets" / "clips" / "ep01" / "grp001.meta.json").write_text(json.dumps(meta))
    return base


def _run(base, *extra):
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = vf.main(["--project", base.name, "--out-root", str(base), "--ep", "ep01", *extra, "--json"])
    return rc, json.loads(buf.getvalue())


def _one_speaker(tmp_path, clip_f0):
    clip = np.concatenate([_tone(None, 0.5), _tone(clip_f0, 2.5), _tone(None, 1.0)])
    return _project(tmp_path, shots=[("sh001", 4.0, [("CHAR-0001", "你来了。")])], refs={"CHAR-0001": 120.0}, clip=clip)


@needs_ffmpeg
@pytest.mark.parametrize("clip_f0,status,rc", [(125.0, "PASS", 0), (150.0, "WARN", 0), (200.0, "FAIL", 1)])
def test_deviation_bands(tmp_path, clip_f0, status, rc):
    base = _one_speaker(tmp_path, clip_f0)
    code, rep = _run(base, "--group", "grp001")
    assert (code, rep["status"]) == (rc, status)
    spk = rep["speakers"][0]
    assert spk["speaker"] == "CHAR-0001" and spk["status"] == status
    assert spk["ref_f0_median_hz"] == pytest.approx(120, rel=0.02)
    assert spk["f0_median_hz"] == pytest.approx(clip_f0, rel=0.02)
    assert spk["segments"][0]["shot_id"] == "sh001"
    assert rep["pass"] is (status != "FAIL")


@needs_ffmpeg
def test_noise_or_unvoiced_segment_is_skip(tmp_path):
    rng = np.random.default_rng(7)
    clip = np.concatenate([rng.normal(0, 0.05, SR * 2), np.zeros(SR * 2)])
    base = _project(tmp_path, shots=[("sh001", 2.0, [("CHAR-0001", "嗯。")]), ("sh002", 2.0, [("CHAR-0001", "走吧。")])],
                    refs={"CHAR-0001": 120.0}, clip=clip)
    code, rep = _run(base, "--group", "grp001")
    assert code == 0 and rep["status"] == "SKIP"
    assert rep["speakers"][0]["status"] == "SKIP" and rep["speakers"][0]["voiced_frames"] < vf.MIN_VOICED_FRAMES


@pytest.mark.parametrize("kw,reason", [
    ({"audio_plan": "ambient_only"}, "audio_plan=ambient_only"),
    ({"mode": "dubbing"}, "后期配音"),
    ({"audio_refs": []}, "audio_refs"),
])
def test_not_applicable_groups_skip_with_exit_0(tmp_path, kw, reason):
    """不满足三条适用条件的组整体 SKIP,不需要 clip、不调 ffmpeg。"""
    base = _project(tmp_path, shots=[("sh001", 4.0, [("CHAR-0001", "你来了。")])], refs={"CHAR-0001": 120.0}, **kw)
    code, rep = _run(base, "--group", "grp001")
    assert code == 0 and rep["status"] == "SKIP" and reason in rep["reason"] and rep["speakers"] == []


def test_group_without_onscreen_lines_skips(tmp_path):
    base = _project(tmp_path, shots=[("sh001", 4.0, [])], refs={"CHAR-0001": 120.0}, audio_plan=None)
    code, rep = _run(base, "--group", "grp001")
    assert code == 0 and rep["status"] == "SKIP" and "画内台词" in rep["reason"]


@pytest.mark.parametrize("argv", [
    ["--project", "../evil", "--ep", "ep01", "--group", "grp001", "--json"],
    ["--project", "proj", "--ep", "../ep01", "--group", "grp001", "--json"],
    ["--project", "proj", "--ep", "ep01", "--group", "grp001/../../x", "--json"],
])
def test_path_like_names_rejected(argv, capsys):
    assert vf.main(argv) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "ERROR" and "不能是路径" in out["error"]


@needs_ffmpeg
def test_clip_or_ref_outside_project_rejected(tmp_path):
    base = _one_speaker(tmp_path, 125.0)
    outside = tmp_path / "other.wav"
    _write_wav(outside, _tone(120.0, 2.0))
    code, rep = _run(base, str(outside), "--seg", "0:2:CHAR-0001")
    assert code == 2 and "越出项目" in rep["error"]
    code, rep = _run(base, "assets/clips/ep01/grp001.mp4", "--seg", "0.5:3:CHAR-0001", "--ref", "CHAR-0001=../other.wav")
    assert code == 2 and "越出项目" in rep["error"]


def test_symlinked_clip_dir_outside_project_rejected(tmp_path):
    """组 clip 经符号链接指出项目目录:resolve 后越界 = 拒绝(退出码 2),不读外面的文件。"""
    base = _project(tmp_path, shots=[("sh001", 4.0, [("CHAR-0001", "你来了。")])], refs={"CHAR-0001": 120.0})
    outside = tmp_path / "outside" / "ep01"
    outside.mkdir(parents=True)
    (outside / "grp001.mp4").write_bytes(b"not a clip")
    (base / "assets" / "clips").symlink_to(tmp_path / "outside", target_is_directory=True)
    code, rep = _run(base, "--group", "grp001")
    assert code == 2 and "越出项目" in rep["error"]


def _two_speakers(tmp_path, f0_a, f0_b, ref_b=190.0):
    """两说话人(样本 120 / ref_b Hz);时窗按 meta boundary_map(与 shot_list 时长 2 / 4 s 不同)。"""
    clip = np.concatenate([_tone(f0_a, 3.0), _tone(None, 0.5), _tone(f0_b, 2.5)])
    meta = {"boundary_map": [{"shot_id": "sh001", "start_s": 0.0, "end_s": 3.2},
                             {"shot_id": "sh002", "start_s": 3.2, "end_s": 6.0}]}
    return _project(tmp_path, shots=[("sh001", 2.0, [("CHAR-0001", "你是谁?")]), ("sh002", 4.0, [("CHAR-0002", "过路的。")])],
                    refs={"CHAR-0001": 120.0, "CHAR-0002": ref_b}, clip=clip, meta=meta)


@needs_ffmpeg
@pytest.mark.parametrize("f0_a,f0_b,ref_b", [
    (175.0, 130.0, 190.0),     # 两人偏离都只在 WARN 档
    (150.0, 130.0, 150.0),     # 一人 WARN、一人单看偏离是 PASS
])
def test_two_speakers_swapped_voices_fail(tmp_path, f0_a, f0_b, ref_b):
    """两人互相更接近对方的样本 → 疑似对调,两人都 FAIL(单看偏离都没到 FAIL 档)。"""
    code, rep = _run(_two_speakers(tmp_path, f0_a, f0_b, ref_b), "--group", "grp001")
    rows = {r["speaker"]: r for r in rep["speakers"]}
    assert rows["CHAR-0001"]["segments"][0]["end_s"] == 3.2
    assert max(r["deviation"] for r in rows.values()) <= vf.FAIL_DEV
    for me, other in (("CHAR-0001", "CHAR-0002"), ("CHAR-0002", "CHAR-0001")):
        r = rows[me]
        assert r["status"] == "FAIL" and r["swap_with"] == other and "对调" in r["reason"]
    assert (code, rep["status"]) == (1, "FAIL")


@needs_ffmpeg
def test_one_sided_nearest_stays_warn(tmp_path):
    """只有一方更接近别人的样本(样本与演绎常有固定落差):只记 nearest_ref,不升档。"""
    code, rep = _run(_two_speakers(tmp_path, 118.0, 145.0), "--group", "grp001")
    rows = {r["speaker"]: r for r in rep["speakers"]}
    assert rows["CHAR-0001"]["status"] == "PASS"
    b = rows["CHAR-0002"]
    assert b["status"] == "WARN" and b["nearest_ref"] == "CHAR-0001" and "swap_with" not in b
    assert (code, rep["status"]) == (0, "WARN")


@needs_ffmpeg
def test_single_clip_mode_with_seg(tmp_path):
    """issue 里 Agent 习惯的形态:<clip> --seg 起:止:CHAR;样本缺省取同名组 prompt 的 audio_refs。"""
    base = _one_speaker(tmp_path, 125.0)
    code, rep = _run(base, "assets/clips/ep01/grp001.mp4", "--seg", "0.5:3.0:CHAR-0001")
    assert code == 0 and rep["status"] == "PASS"
    assert rep["speakers"][0]["ref"] == "assets/audio/voice/refs/CHAR-0001_voiceprint.wav"
    code, rep = _run(base, "assets/clips/ep01/grp001.mp4", "--seg", "bad")
    assert code == 2 and "--seg" in rep["error"]


def test_plan_segments_split_and_offscreen():
    """一镜多人按估时切;画外句不进;boundary_map 优先、缺的镜按时长累计。"""
    shots = {
        "sh1": {"duration_s": 4, "dialogue_lines": [{"speaker": "CHAR-0001", "text": "甲", "est_duration_s": 1},
                                                    {"speaker": "CHAR-0002", "text": "乙", "est_duration_s": 3}]},
        "sh2": {"duration_s": 2, "dialogue_lines": [{"speaker": "CHAR-0003", "text": "画外", "placement": "os"}]},
        "sh3": {"duration_s": 3, "dialogue_lines": [{"speaker": "CHAR-0001", "text": "丙"}]},
    }
    group = {"shots": ["sh1", "sh2", "sh3"]}
    win = vf.shot_windows(group, shots, {"boundary_map": [{"shot_id": "sh1", "start_s": 0, "end_s": 5}]})
    assert win == {"sh1": (0.0, 5.0), "sh2": (4.0, 6.0), "sh3": (6.0, 9.0)}
    segs = vf.plan_segments(group, shots, win)
    assert [(s["speaker"], s["start_s"], s["end_s"], s["split"]) for s in segs] == [
        ("CHAR-0001", 0.0, 1.25, True), ("CHAR-0002", 1.25, 5.0, True), ("CHAR-0001", 6.0, 9.0, False)]


def test_deviation_is_symmetric():
    assert vf.deviation(150, 120) == pytest.approx(0.25)
    assert vf.deviation(120, 150) == pytest.approx(0.25)
    assert vf.classify(0.2) == "PASS" and vf.classify(0.21) == "WARN" and vf.classify(vf.FAIL_DEV + 0.01) == "FAIL"
