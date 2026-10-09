"""对白语音库:语速默认值 / casting 键兼容 / 静音修剪(2026-09-15)。"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from modules import dialogue_tts as dt


def write(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------- 纯逻辑

def test_num_speed_accepts_numbers_rejects_descriptions():
    assert dt.num_speed(1.15) == 1.15
    assert dt.num_speed("1.2") == 1.2
    assert dt.num_speed("常态(未传 --speed;声纹卡 speed_cpm=[175,210])") is None
    assert dt.num_speed(None) is None
    assert dt.num_speed("") is None
    assert dt.num_speed(True) is None
    assert dt.num_speed(3.0) is None      # 超出 SPEED_RANGE
    assert dt.num_speed(0) is None


def test_trim_plan_head_tail_and_pause():
    # 0-0.4 静音, 0.4-1.5 语音, 1.5-2.7 停顿, 2.7-3.0 语音, 3.0-3.14 静音
    sil = [(0.0, 0.4), (1.5, 2.7), (3.0, 3.14)]
    tp = dt.trim_plan(sil, 3.14)
    assert tp["keep"] == [(0.3, 3.14)]            # 尾静音 0.14 < keep_tail 0.15 → 不裁
    assert tp["lead_s"] == 0.3 and tp["tail_s"] == 0.0 and tp["pause_s"] == 0.0
    tp = dt.trim_plan([(0.0, 0.4), (2.7, 4.0)], 4.0)
    assert tp["keep"] == [(0.3, 2.85)]
    assert tp["tail_s"] == pytest.approx(1.15, abs=1e-3)
    tp = dt.trim_plan(sil, 3.14, max_pause=0.5)
    assert tp["pause_s"] == pytest.approx(0.7, abs=1e-3)
    assert len(tp["keep"]) == 2
    a, b = tp["keep"]
    assert a[0] == 0.3 and b[1] == pytest.approx(3.14, abs=0.02)
    assert (b[0] - a[1]) == pytest.approx(0.7, abs=1e-3)


def test_trim_plan_no_silence_or_all_silence_keeps_whole():
    assert dt.trim_plan([], 2.0)["keep"] == [(0.0, 2.0)]
    tp = dt.trim_plan([(0.0, 2.0)], 2.0)
    assert tp["keep"] == [(0.0, 2.0)] and tp["lead_s"] == 0 and tp["tail_s"] == 0


def test_load_casting_accepts_entries_key(tmp_path):
    write(tmp_path / "assets/audio/voice/casting.json",
          {"entries": [{"character_id": "CHAR-0001", "variant": "default", "speed": "常态", "tts_voice": None}]})
    c = dt.load_casting(tmp_path)
    assert ("CHAR-0001", "default") in c
    write(tmp_path / "assets/audio/voice/casting.json",
          {"castings": [{"char_id": "CHAR-0002", "speed": 1.1}]})
    assert ("CHAR-0002", "default") in dt.load_casting(tmp_path)


# ---------------------------------------------------------------- plan / sync

@pytest.fixture
def project(tmp_path):
    base = tmp_path / "demo"
    write(base / "settings.json", {"output": {"dialogue_tts": True, "dialogue_tts_speed": 1.2}})
    write(base / "assets/audio/voice/casting.json", {"entries": [
        {"character_id": "CHAR-0001", "variant": "default", "speed": "常态(未传 --speed)"},
        {"character_id": "CHAR-0002", "variant": "default", "speed": 0.9},
    ]})
    for c in ("CHAR-0001", "CHAR-0002"):
        write(base / "bible/characters" / c / "voice.json", {"id": c})
    write(base / "directing/ep01/shot_list.json", {"shots": [
        {"shot_id": "S01-01", "dialogue_lines": [{"speaker": "CHAR-0001", "text": "你好"},
                                                 {"speaker": "CHAR-0002", "text": "再见"}]}]})
    return base


def test_plan_speed_defaults_from_settings_and_casting(project):
    p = dt.plan(project, "ep01", channel=("volcengine", "seed-audio-1.0"))
    by = {e["speaker"]: e for e in p["lines"]}
    assert p["speed"] == 1.2
    assert by["CHAR-0001"]["speed"] == 1.2      # casting 描述文字 → 项目默认
    assert by["CHAR-0002"]["speed"] == 0.9      # casting 数字优先
    p2 = dt.plan(project, "ep01", channel=("volcengine", "seed-audio-1.0"), speed=1.5)
    by2 = {e["speaker"]: e for e in p2["lines"]}
    assert by2["CHAR-0001"]["speed"] == 1.5 and by2["CHAR-0002"]["speed"] == 0.9
    assert by2["CHAR-0001"]["key"] != by["CHAR-0001"]["key"]   # speed 进 key
    assert by2["CHAR-0002"]["key"] == by["CHAR-0002"]["key"]


def _make_padded_tone(out: Path, lead=0.5, tone=0.6, tail=1.0):
    """lead 静音 + tone 秒 440Hz + tail 静音 的 mp3。"""
    f = (f"aevalsrc=0:d={lead}[a];sine=frequency=440:duration={tone}[b];aevalsrc=0:d={tail}[c];"
         f"[a][b][c]concat=n=3:v=0:a=1[out]")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-filter_complex", f,
                    "-map", "[out]", "-c:a", "libmp3lame", "-b:a", "128k", str(out)], check=True)


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg unavailable")
def test_sync_trims_silence_and_retrims_without_resynth(project, monkeypatch):
    monkeypatch.setattr(dt, "tts_channel", lambda: ("volcengine", "seed-audio-1.0"))
    calls = []

    def fake_tts(entry, out):
        calls.append((entry["speaker"], entry["speed"], out.name))
        _make_padded_tone(out)

    m = dt.sync(project, "ep01", tts=fake_tts)
    assert m["summary"]["ok"] == 2 and m["synthesized"] == 2
    assert m["default_speed"] == 1.2 and m["trim"] == {"enabled": True, "max_pause": 0.0}
    ldir = dt.lib_dir(project, "ep01")
    for e in m["lines"]:
        assert (ldir / e["file"]).is_file() and (ldir / dt.RAW_DIR / e["file"]).is_file()
        assert 0.75 <= e["duration_s"] <= 0.95, e            # 0.6 音 + 0.10 头 + 0.15 尾
        assert e["trim"]["lead_s"] == pytest.approx(0.4, abs=0.08)
        assert e["trim"]["tail_s"] == pytest.approx(0.85, abs=0.08)
    raw_dur = dt.probe_duration(ldir / dt.RAW_DIR / m["lines"][0]["file"])
    assert raw_dur == pytest.approx(2.1, abs=0.1)
    # 语速按 casting/设置传给合成器
    assert sorted((c[0], c[1]) for c in calls) == [("CHAR-0001", 1.2), ("CHAR-0002", 0.9)]

    # 再同步:全 fresh,不重合成、不重裁
    calls.clear()
    m2 = dt.sync(project, "ep01", tts=fake_tts)
    assert calls == [] and m2["synthesized"] == 0 and m2["retrimmed"] == 0
    assert [e["duration_s"] for e in m2["lines"]] == [e["duration_s"] for e in m["lines"]]

    # 修剪参数变(--no-trim):从 _raw/ 还原,不重合成
    m3 = dt.sync(project, "ep01", tts=fake_tts, trim=False)
    assert calls == [] and m3["synthesized"] == 0
    # trim=False 不改动已修剪文件(params 差异只在 trim=True 时触发重裁)
    assert m3["lines"][0]["trim"] == m["lines"][0]["trim"]

    # max_pause 变:重裁(仍不重合成)
    m4 = dt.sync(project, "ep01", tts=fake_tts, max_pause=0.5)
    assert calls == [] and m4["retrimmed"] == 2
    assert m4["lines"][0]["trim"]["params"]["max_pause"] == 0.5


def test_sync_status_only_manifest_without_trim_gets_trimmed_in_place(project, monkeypatch):
    """旧库(台账无 trim 段、无 _raw/)首次同步:就地裁库文件并补 _raw/,不调 TTS。"""
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("ffmpeg unavailable")
    monkeypatch.setattr(dt, "tts_channel", lambda: ("volcengine", "seed-audio-1.0"))
    p = dt.plan(project, "ep01")
    ldir = dt.lib_dir(project, "ep01")
    ldir.mkdir(parents=True)
    lines = []
    for e in p["lines"]:
        _make_padded_tone(ldir / e["file"])
        lines.append({**{k: v for k, v in e.items() if k != "speaker_raw"}, "status": "ok",
                      "duration_s": 2.1, "generated_at": "x"})
    write(ldir / dt.MANIFEST, {"schema": dt.SCHEMA, "lines": lines})
    calls = []
    m = dt.sync(project, "ep01", tts=lambda e, o: calls.append(e))
    assert calls == [] and m["retrimmed"] == 2
    for e in m["lines"]:
        assert e["duration_s"] < 1.0 and (ldir / dt.RAW_DIR / e["file"]).is_file()
