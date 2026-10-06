"""genmedia sfx:渠道 / Key 选取、参数校验、请求体、后处理(裁首尾静音 + 峰值归一)与循环音直落。"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import modules.genmedia as g  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="需要 ffmpeg / ffprobe")


@pytest.fixture
def config(tmp_path, monkeypatch):
    path = tmp_path / "genconfig.json"
    monkeypatch.setattr(g, "CONFIG_PATH", path)
    for name in ("ELEVENLABS_API_KEY", "FAL_KEY", "VIDEOAGENTS_AGENT"):
        monkeypatch.delenv(name, raising=False)

    def write(data):
        path.write_text(json.dumps(data), encoding="utf-8")
    return write


def _mp3(tmp_path, lead=0.4, tone=0.6, trail=0.5, volume=0.1) -> bytes:
    """静音 + 440 Hz 正弦 + 静音 的 mp3(电平故意偏低,用来验证归一)。"""
    out = tmp_path / "fixture.mp3"
    graph = (f"anullsrc=r=44100:cl=mono:d={lead}[a];sine=f=440:r=44100:d={tone},volume={volume}[b];"
             f"anullsrc=r=44100:cl=mono:d={trail}[c];[a][b][c]concat=n=3:v=0:a=1")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", graph, str(out)], check=True)
    return out.read_bytes()


def test_auto_provider_prefers_elevenlabs_then_fal(config):
    config({"tts": {"elevenlabs": {"api_key": "el-key"}}, "video": {"fal": {"api_key": "fal-key"}}})
    assert g._sfx_config() == {"provider": "elevenlabs", "api_key": "el-key", "model": g.SFX_EL_MODEL}
    assert g._sfx_config("fal")["api_key"] == "fal-key"
    config({"image": {"fal": {"api_key": "fal-key"}}})
    assert g._sfx_config()["provider"] == "fal"


def test_missing_key_reports_where_to_fill(config, monkeypatch):
    config({})
    with pytest.raises(RuntimeError, match="未找到可用的 API Key"):
        g._sfx_config()
    with pytest.raises(RuntimeError, match="Fal 标签页"):
        g._sfx_config("fal")
    monkeypatch.setenv("FAL_KEY", "env-key")
    assert g._sfx_config()["api_key"] == "env-key"


def test_outputs_for_count():
    assert g._sfx_outputs("a/hit.wav", 1) == ["a/hit.wav"]
    assert g._sfx_outputs("a/hit.wav", 3) == ["a/hit_1.wav", "a/hit_2.wav", "a/hit_3.wav"]


@pytest.mark.parametrize("kwargs, message", [
    ({"output": "x.aac"}, "扩展名"),
    ({"count": 5}, "--count"),
    ({"prompt_influence": 1.5}, "--prompt-influence"),
    ({"duration_s": 25, "provider": "fal"}, "--duration"),
    ({"duration_s": 0.2}, "--duration"),
    ({"prompt": "x" * 451, "provider": "fal"}, "450"),
])
def test_validation_rejects_before_any_request(config, monkeypatch, tmp_path, kwargs, message):
    config({"music": {"elevenlabs": {"api_key": "el"}}, "image": {"fal": {"api_key": "fal"}}})
    monkeypatch.setattr(g, "_request", lambda *a, **k: pytest.fail("不该发请求"))
    monkeypatch.setattr(g, "_fal_queue_run", lambda *a, **k: pytest.fail("不该发请求"))
    args = {"prompt": "door slam", "output": str(tmp_path / "x.mp3"), **kwargs}
    if "output" in kwargs:
        args["output"] = str(tmp_path / kwargs["output"])
    with pytest.raises(RuntimeError, match=message):
        g.generate_sfx(**args)


def test_dispatch_layer_is_refused(config, monkeypatch, tmp_path):
    config({"music": {"elevenlabs": {"api_key": "el"}}})
    monkeypatch.setenv("VIDEOAGENTS_AGENT", "00-orchestration/producer")
    with pytest.raises(SystemExit):
        g.generate_sfx("door slam", str(tmp_path / "x.mp3"))


def test_loop_mp3_is_saved_untouched_via_elevenlabs(config, monkeypatch, tmp_path):
    config({"music": {"elevenlabs": {"api_key": "el"}}})
    sent = {}

    def fake_request(url, data=None, headers=None, **kw):
        sent.update(url=url, body=json.loads(data), headers=headers)
        return b"ID3-raw-loop-bytes"
    monkeypatch.setattr(g, "_request", fake_request)
    monkeypatch.setattr(g, "_sfx_analyze", lambda path: {"duration_s": 10.0, "peak_db": -6.0})
    out = tmp_path / "bed.mp3"
    assert g.generate_sfx("low wind over snowfield", str(out), duration_s=10, loop=True) == [str(out)]
    assert out.read_bytes() == b"ID3-raw-loop-bytes"
    assert sent["url"].endswith(f"/v1/sound-generation?output_format={g.SFX_SOURCE_FORMAT}")
    assert sent["headers"]["xi-api-key"] == "el"
    assert sent["body"] == {"text": "low wind over snowfield", "model_id": g.SFX_EL_MODEL,
                            "prompt_influence": 0.3, "loop": True, "duration_seconds": 10}
    meta = json.loads(out.with_suffix(".meta.json").read_text(encoding="utf-8"))["sfx"]
    assert meta["loop"] is True and meta["audio"]["postprocessed"] is False
    assert meta["license_source"].startswith("ElevenLabs Sound Effects v2")
    assert not list(tmp_path.glob(".*.src.mp3"))


def test_fal_body_and_count(config, monkeypatch, tmp_path):
    config({"video": {"fal": {"api_key": "fal"}}})
    calls = []

    def fake_queue(cfg, endpoint, body, timeout, label, kind="视频", poll=None):
        calls.append((endpoint, body, label, kind))
        return {"audio": {"url": "data:audio/mpeg;base64,QUJD"}}
    monkeypatch.setattr(g, "_fal_queue_run", fake_queue)
    monkeypatch.setattr(g, "_sfx_analyze", lambda path: {"duration_s": 1.0, "peak_db": -3.0})
    out = tmp_path / "hit.mp3"
    saved = g.generate_sfx("sword clash", str(out), duration_s=1, count=2, postprocess=False)
    assert saved == [str(tmp_path / "hit_1.mp3"), str(tmp_path / "hit_2.mp3")]
    assert [c[2] for c in calls] == ["hit_1.mp3", "hit_2.mp3"]
    endpoint, body, _, kind = calls[0]
    assert endpoint == g.SFX_FAL_ENDPOINT and kind == "音效"
    assert body == {"text": "sword clash", "prompt_influence": 0.3, "loop": False,
                    "output_format": g.SFX_SOURCE_FORMAT, "duration_seconds": 1}
    assert Path(saved[0]).read_bytes() == b"ABC"


@needs_ffmpeg
def test_postprocess_trims_edges_and_normalizes_peak(config, monkeypatch, tmp_path):
    config({"music": {"elevenlabs": {"api_key": "el"}}})
    data = _mp3(tmp_path)
    monkeypatch.setattr(g, "_request", lambda *a, **k: data)
    out = tmp_path / "hit.wav"
    g.generate_sfx("sine beep", str(out))
    audio = json.loads(out.with_suffix(".meta.json").read_text(encoding="utf-8"))["sfx"]["audio"]
    assert audio["postprocessed"] is True
    assert audio["before"]["leading_silence_s"] == pytest.approx(0.4, abs=0.08)
    assert audio["before"]["trailing_silence_s"] == pytest.approx(0.5, abs=0.08)
    assert audio["after"]["duration_s"] == pytest.approx(0.6, abs=0.1)
    assert audio["after"]["peak_db"] == pytest.approx(g.SFX_PEAK_DBFS, abs=0.6)
    assert audio["after"]["leading_silence_s"] < 0.05
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=sample_rate", "-of", "csv=p=0",
                            str(out)], capture_output=True, text=True, check=True)
    assert probe.stdout.strip() == str(g.SFX_SAMPLE_RATE)


@needs_ffmpeg
def test_postprocess_keeps_gaps_between_hits(config, monkeypatch, tmp_path):
    """中间的停顿不能被吃掉:两声之间隔 0.5 s,处理后总长仍含这段间隔。"""
    config({"music": {"elevenlabs": {"api_key": "el"}}})
    src = tmp_path / "two.mp3"
    graph = ("anullsrc=r=44100:cl=mono:d=0.3[a];sine=f=440:r=44100:d=0.3[b];anullsrc=r=44100:cl=mono:d=0.5[c];"
             "sine=f=660:r=44100:d=0.3[d];anullsrc=r=44100:cl=mono:d=0.3[e];[a][b][c][d][e]concat=n=5:v=0:a=1")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", graph, str(src)], check=True)
    monkeypatch.setattr(g, "_request", lambda *a, **k: src.read_bytes())
    out = tmp_path / "knock.wav"
    g.generate_sfx("two knocks", str(out))
    audio = json.loads(out.with_suffix(".meta.json").read_text(encoding="utf-8"))["sfx"]["audio"]
    assert audio["after"]["duration_s"] == pytest.approx(1.1, abs=0.12)


@needs_ffmpeg
def test_silent_result_is_reported(config, monkeypatch, tmp_path):
    config({"music": {"elevenlabs": {"api_key": "el"}}})
    src = tmp_path / "silent.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=1", str(src)],
                   check=True)
    monkeypatch.setattr(g, "_request", lambda *a, **k: src.read_bytes())
    out = tmp_path / "none.wav"
    with pytest.raises(RuntimeError, match="静音"):
        g.generate_sfx("nothing", str(out))
    assert not out.exists() and not list(tmp_path.glob(".*.src.mp3"))
