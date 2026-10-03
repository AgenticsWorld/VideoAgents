"""超分统一设置(2026-09-23):路由只看 genconfig.upscale、目标尺寸换算、样片模式请求体、渠道失败不降级。"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import modules.genmedia as g  # noqa: E402


@pytest.fixture
def genconfig(tmp_path, monkeypatch):
    cfg_path = tmp_path / "genconfig.json"

    def write(upscale=None, video=None):
        data = {"video": video or {"provider": "volcengine",
                                    "volcengine": {"api_key": "k", "model": "doubao-seedance-2-5-260628"},
                                    "minimax": {"api_key_io": "", "api_key_cn": "", "api_base": "https://api.minimax.io"}}}
        if upscale is not None:
            data["upscale"] = upscale
        cfg_path.write_text(json.dumps(data), encoding="utf-8")
        return cfg_path

    monkeypatch.setattr(g, "CONFIG_PATH", cfg_path)
    monkeypatch.delenv("VIDEOAGENTS_PROJECT", raising=False)
    return write


def test_upscale_config_defaults_when_section_missing(genconfig):
    genconfig(upscale=None)
    up = g._upscale_config()
    assert up["provider"] == "ffmpeg"
    assert "fallback" not in up
    assert up["ffmpeg"] == {"filter": "lanczos", "crf": 18, "preset": "slow"}


def test_upscale_config_sanitizes_values(genconfig):
    genconfig(upscale={"provider": "comfyui",
                       "ffmpeg": {"filter": "nope", "crf": "99", "preset": "x"}})
    up = g._upscale_config()
    assert up["provider"] == "comfyui"
    assert up["ffmpeg"] == {"filter": "lanczos", "crf": 51, "preset": "slow"}


def test_upscale_config_rejects_unknown_provider(genconfig):
    genconfig(upscale={"provider": "magic"})
    with pytest.raises(RuntimeError):
        g._upscale_config()


def test_upscale_dimensions_2k_and_portrait():
    assert g._upscale_dimensions("16:9", "1080p") == (1920, 1080)
    assert g._upscale_dimensions("16:9", "2k") == (2560, 1440)
    assert g._upscale_dimensions("9:16", "4k") == (2160, 3840)
    assert g._upscale_target("", "1080p", "") == (1920, 1080, "16:9")
    with pytest.raises(RuntimeError):
        g._upscale_dimensions("16:9", "5k")


def test_draft_mode_only_for_ark_seedance25_at_480p(genconfig, capsys):
    genconfig(upscale={"provider": "volcengine"})
    cfg = {"provider": "volcengine", "model": "doubao-seedance-2-5-260628"}
    assert g._draft_mode_active(cfg, "480p") is True
    assert g._draft_mode_active(cfg, "720p") is False
    assert g._draft_mode_active({"provider": "fal", "model": "bytedance/seedance-2.5"}, "480p") is False
    assert g._draft_mode_active({"provider": "volcengine", "model": "doubao-seedance-2-0-260128"}, "480p") is False
    genconfig(upscale={"provider": "ffmpeg"})
    assert g._draft_mode_active(cfg, "480p") is False


def test_ark_body_draft_puts_resolution_top_level(genconfig):
    genconfig(upscale={"provider": "volcengine"})
    cfg = {"provider": "volcengine", "model": "doubao-seedance-2-5-260628", "api_key": "k"}
    body = g._ark_video_body(cfg, "a girl", "", "", 5, "480p", "16:9", None, [], [], None, False,
                             draft=True, to_url=lambda p: f"file://{p}")
    assert body["draft"] is True
    assert body["resolution"] == "480p"
    assert "--resolution" not in body["content"][0]["text"]
    assert "--dur 5" in body["content"][0]["text"]
    plain = g._ark_video_body(cfg, "a girl", "", "", 5, "480p", "16:9", None, [], [], None, False,
                              to_url=lambda p: f"file://{p}")
    assert "draft" not in plain and "--resolution 480p" in plain["content"][0]["text"]
    with pytest.raises(RuntimeError):
        g._ark_video_body(cfg, "a girl", "", "", 5, "720p", "16:9", None, [], [], None, False, draft=True,
                          to_url=lambda p: f"file://{p}")


def test_volc_draft_task_reads_meta_and_expiry(tmp_path):
    clip = tmp_path / "grp001.mp4"
    clip.write_bytes(b"x")
    with pytest.raises(RuntimeError, match="Draft"):
        g._volc_draft_task(str(clip), "")
    meta = {"draft_task": {"id": "cgt-1", "provider": "volcengine", "model": "doubao-seedance-2-5-260628",
                           "created_ts": 0}}
    (tmp_path / "grp001.meta.json").write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(RuntimeError, match="7 天"):
        g._volc_draft_task(str(clip), "")
    import time
    meta["draft_task"]["created_ts"] = int(time.time())
    (tmp_path / "grp001.meta.json").write_text(json.dumps(meta), encoding="utf-8")
    d = g._volc_draft_task(str(clip), "")
    assert d["id"] == "cgt-1"
    assert g._volc_draft_task(str(clip), "cgt-override")["id"] == "cgt-override"


def test_record_draft_task_merges_meta(tmp_path):
    out = tmp_path / "grp002.mp4"
    (tmp_path / "grp002.meta.json").write_text(json.dumps({"usage": {"total_tokens": 1}}), encoding="utf-8")
    g._record_draft_task(str(out), "cgt-9", {"provider": "volcengine", "model": "doubao-seedance-2-5-260628"})
    meta = json.loads((tmp_path / "grp002.meta.json").read_text(encoding="utf-8"))
    assert meta["usage"] == {"total_tokens": 1}
    assert meta["draft_task"]["id"] == "cgt-9"
    assert meta["draft_task"]["resolution"] == "480p"


def test_generate_upscale_provider_failure_is_not_downgraded(genconfig, tmp_path, monkeypatch):
    src = tmp_path / "grp003.mp4"
    src.write_bytes(b"x")
    out = tmp_path / "grp003_up.mp4"
    calls = []

    def fake_ffmpeg(ff, inp, output, w, h):
        calls.append((ff["filter"], w, h))
        Path(output).write_bytes(b"y")
        return str(output)

    monkeypatch.setattr(g, "_upscale_ffmpeg", fake_ffmpeg)
    monkeypatch.setattr(g, "_probe_video_meta", lambda p: {"width": 854, "height": 480, "fps": 24.0,
                                                           "frames": 120, "has_audio": True})
    monkeypatch.setattr(g, "_forbid_dispatch_layer", lambda kind: None)
    # minimax 未配 Key → 预检失败;没有降级开关,直接抛错、不落 ffmpeg、不写 meta
    genconfig(upscale={"provider": "minimax", "ffmpeg": {"filter": "bicubic"}})
    with pytest.raises(RuntimeError, match="不降级"):
        g.generate_upscale(str(src), str(out), resolution="1080p")
    assert not calls
    assert not out.exists()
    assert not out.with_suffix(".meta.json").exists()


def test_generate_upscale_ffmpeg_writes_meta(genconfig, tmp_path, monkeypatch):
    src = tmp_path / "grp004.mp4"
    src.write_bytes(b"x")
    out = tmp_path / "grp004_up.mp4"
    monkeypatch.setattr(g, "_upscale_ffmpeg", lambda ff, i, o, w, h: (Path(o).write_bytes(b"y"), str(o))[1])
    probes = {str(src): {"width": 854, "height": 480, "fps": 24.0, "frames": 120, "has_audio": True},
              str(out): {"width": 1920, "height": 1080, "fps": 24.0, "frames": 120, "has_audio": True}}
    monkeypatch.setattr(g, "_probe_video_meta", lambda p: probes.get(str(p)))
    monkeypatch.setattr(g, "_forbid_dispatch_layer", lambda kind: None)
    genconfig(upscale={"provider": "ffmpeg", "ffmpeg": {"filter": "lanczos", "crf": 20, "preset": "fast"}})
    g.generate_upscale(str(src), str(out), resolution="1080p", aspect="16:9")
    meta = json.loads(out.with_suffix(".meta.json").read_text(encoding="utf-8"))
    assert meta["upscale"]["method"] == "ffmpeg"
    assert meta["upscale"]["target"] == "1920x1080"
    assert meta["upscale"]["output_resolution"] == "1920x1080"
    assert meta["upscale"]["params"] == {"filter": "lanczos", "crf": 20, "preset": "fast"}


def test_minimax_upscale_config_reads_upscale_section(genconfig, monkeypatch):
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    genconfig(upscale={"provider": "minimax",
                       "minimax": {"api_key_io": "io-key", "api_key_cn": "cn-key",
                                   "api_base": "https://api.minimaxi.com", "model": "MiniMax-H3"}})
    cfg = g._minimax_upscale_config()
    assert cfg["api_key"] == "cn-key"
    assert cfg["api_base"] == "https://api.minimaxi.com"
    genconfig(upscale={"provider": "minimax", "minimax": {"api_key_io": "", "api_key_cn": ""}})
    with pytest.raises(RuntimeError, match="超分段"):
        g._minimax_upscale_config()


# ---------------- AgenticsLLM 超分渠道(2026-09-25,media_type=Scale,profile 如 scale-seedvr2) ----------------

def test_upscale_config_accepts_agentics_and_reads_profile(genconfig):
    genconfig(upscale={"provider": "agentics", "agentics": {"profile_code": "scale-seedvr2"}})
    up = g._upscale_config()
    assert up["provider"] == "agentics"
    assert up["agentics"] == {"profile_code": "scale-seedvr2"}
    cfg = g._agentics_upscale_config()
    assert cfg == {"provider": "agentics", "profile_code": "scale-seedvr2", "model": "scale-seedvr2"}


def test_agentics_upscale_config_defaults_profile(genconfig):
    genconfig(upscale={"provider": "agentics"})
    assert g._agentics_upscale_config()["profile_code"] == g.AGENTICS_UPSCALE_DEFAULT_PROFILE


def test_agentics_media_types_include_upscale_and_long_kind():
    from services.runtime import core
    assert g.AGENTICS_MEDIA_TYPES["upscale"] == core.AGENTICS_MEDIA_TYPES["upscale"]
    assert "upscale" in g.AGENTICS_LONG_KINDS
    assert "agentics" in g.UPSCALE_PROVIDERS and "agentics" in core.UPSCALE_PROVIDERS
    assert core.DEFAULT_GENCONFIG["upscale"]["agentics"] == {"profile_code": "scale-seedvr2"}


def _scale_profile(token="input_video", extra_params=None):
    params = {"resolution": {"targets": [{}]}, "width": {"targets": [{}]},
              "height": {"targets": [{}]}, "seed": {"targets": [{}]}}
    params.update(extra_params or {})
    return {"contract_version": "media-generation/v1", "profile_code": "scale-seedvr2",
            "media_type": g.AGENTICS_MEDIA_TYPES["upscale"], "updated_at": "2026-09-25T00:00:00Z",
            "token_schema": {"version": 1, "media_type": "scale", "parameters": params,
                             "files": {token: {"required": True, "min_items": 1, "max_items": 1,
                                               "targets": [{"index": 0}]}}}}


def test_agentics_upscale_payload_uses_declared_video_token_and_fixed_fields(tmp_path, monkeypatch):
    src = tmp_path / "grp001.mp4"
    src.write_bytes(b"video")
    uploaded = []
    monkeypatch.setattr(g, "_agentics_upload", lambda token, path: uploaded.append((token, path))
                        or {"token": token, "url": "https://files.example/src"})
    profile = _scale_profile("reference_videos")
    parameters, files = g._agentics_payload(
        profile, {"resolution": "1080p", "width": 1920, "height": 1080, "seed": 7,
                  "aspect_ratio": "16:9", "scale": 2.0},
        {"reference_videos": [str(src)]})
    assert parameters == {"resolution": "1080p", "width": 1920, "height": 1080, "seed": 7}
    assert uploaded == [("reference_videos", str(src))]
    assert files[0]["token"] == "reference_videos" and files[0]["index"] == 0


def test_generate_upscale_agentics_submits_and_records_meta(genconfig, tmp_path, monkeypatch):
    genconfig(upscale={"provider": "agentics", "agentics": {"profile_code": "scale-seedvr2"}})
    src = tmp_path / "grp001.mp4"
    src.write_bytes(b"video")
    out = tmp_path / "grp001.1080p.mp4"
    monkeypatch.setattr(g, "_agentics_profile", lambda kind, code, refresh=False: _scale_profile())
    monkeypatch.setattr(g, "_probe_video_meta", lambda path: {"width": 1920, "height": 1080, "fps": 24, "duration": 5.0})
    submitted = {}

    def fake_generate(kind, cfg, values, files, profile=None, on_submit=None):
        submitted.update(kind=kind, cfg=cfg, values=values, files=files)
        on_submit("11111111-2222-3333-4444-555555555555")
        return b"upscaled"

    monkeypatch.setattr(g, "_agentics_generate", fake_generate)
    monkeypatch.setattr(g, "_rescale_to", lambda path, w, h: False)
    saved = g.generate_upscale(str(src), str(out), resolution="1080p", aspect="16:9", seed=9)
    assert Path(saved).read_bytes() == b"upscaled"
    assert submitted["kind"] == "upscale"
    assert submitted["cfg"]["profile_code"] == "scale-seedvr2"
    assert submitted["files"] == {"input_video": [str(src)]}
    assert submitted["values"]["width"] == 1920 and submitted["values"]["seed"] == 9
    meta = json.loads(out.with_suffix(".meta.json").read_text(encoding="utf-8"))
    rec = meta["upscale"]
    assert rec["provider"] == "agentics" and rec["model"] == "scale-seedvr2"
    assert rec["params"]["task_id"] == "11111111-2222-3333-4444-555555555555"
    assert rec["params"]["video_token"] == "input_video"


def test_agentics_upscale_rejects_profile_without_video_slot(genconfig, tmp_path, monkeypatch):
    genconfig(upscale={"provider": "agentics"})
    src = tmp_path / "grp001.mp4"
    src.write_bytes(b"video")
    profile = _scale_profile()
    profile["token_schema"]["files"] = {}
    monkeypatch.setattr(g, "_agentics_profile", lambda kind, code, refresh=False: profile)
    with pytest.raises(RuntimeError, match="未声明源视频附件位"):
        g._upscale_agentics(g._agentics_upscale_config(), str(src), str(tmp_path / "o.mp4"),
                            "1080p", "16:9", 1920, 1080, 1)
