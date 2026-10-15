"""DigitalHuman service contract regressions; all uploads/tasks are mocked."""
import copy
from pathlib import Path

import pytest

from modules import digitalhuman as d
from modules import genmedia as g


@pytest.fixture
def profile():
    # Minimal service v1 mapping (synthetic, not a live RunningHub export).
    return {
        "profile_code": "infiniteTalk-1char", "media_type": 5,
        "token_schema": {
            "version": 1, "media_type": "digital_human",
            "parameters": {
                "audio_guidance_scale": {"default": 1},
                "audio_strength": {"default": 1},
                "audio_mode": {"default": "parallel",
                               "value_map": {"parallel": "para", "sequential": "add"}},
                "prompt": {}, "seed": {}, "fps": {"default": 25},
            },
            "files": {
                "avatar_image": {"required": True, "min_items": 1, "max_items": 1},
                "driving_audios": {"required": True, "min_items": 1, "max_items": 1},
            },
        },
    }


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("unexpected network access")
    monkeypatch.setattr(g, "_request", forbidden)
    monkeypatch.setattr(g, "_agentics_json", forbidden)


@pytest.fixture
def uploads(monkeypatch):
    calls = []

    def upload(token, path):
        calls.append((token, path))
        return {"token": token, "key": "temp/" + path,
                "url": "https://uploads.example/" + path}

    monkeypatch.setattr(g, "_agentics_upload", upload)
    return calls


def test_infinitetalk_profile_and_explicit_audio_parameters(profile, uploads):
    values = {"audio_guidance_scale": 1.5, "audio_strength": 0.8,
              "audio_mode": "parallel", "fps": 23.976}
    params, files = g._agentics_payload(profile, values, {
        "avatar_image": ["avatar.png"], "driving_audios": ["speech.wav"],
    })
    assert params == values
    assert [(f["token"], f["index"]) for f in files] == [
        ("avatar_image", 0), ("driving_audios", 0)]
    assert len(uploads) == 2


def test_defaults_remain_server_owned(profile, uploads):
    params, _ = g._agentics_payload(profile, {}, {
        "avatar_image": ["avatar.png"], "driving_audios": ["speech.wav"],
    })
    assert params == {}


def test_files_only_profile(profile, uploads):
    profile["token_schema"]["parameters"] = {}
    params, files = g._agentics_payload(profile, {}, {
        "avatar_image": ["avatar.png"], "driving_audios": ["speech.wav"],
    })
    assert params == {} and len(files) == 2


def test_empty_visual_prompt_is_valid(profile, uploads):
    profile["token_schema"]["parameters"]["prompt"] = {"required": True}
    params, _ = g._agentics_payload(profile, {"prompt": ""}, {
        "avatar_image": ["avatar.png"], "driving_audios": ["speech.wav"],
    })
    assert params == {"prompt": ""}


@pytest.mark.parametrize("value", [-1, "1", True])
def test_invalid_audio_guidance_rejected_before_upload(profile, uploads, value):
    with pytest.raises(RuntimeError, match="audio_guidance_scale"):
        g._agentics_payload(profile, {"audio_guidance_scale": value}, {
            "avatar_image": ["avatar.png"], "driving_audios": ["speech.wav"],
        })
    assert uploads == []


def test_unknown_parameter_still_rejected(profile, uploads):
    profile["token_schema"]["parameters"]["invented_parameter"] = {}
    with pytest.raises(RuntimeError, match="invented_parameter"):
        g._agentics_payload(profile, {}, {})
    assert uploads == []


def test_dual_speaker_indexes_and_soundtrack(profile, uploads):
    mappings = profile["token_schema"]["files"]
    mappings["driving_audios"].update(min_items=2, max_items=2)
    mappings["dialogue_audio"] = {"required": True, "min_items": 1, "max_items": 1}
    _, files = g._agentics_payload(profile, {}, {
        "avatar_image": ["scene.png"], "driving_audios": ["left.wav", "right.wav"],
        "dialogue_audio": ["complete.wav"],
    })
    assert [(f["token"], f["index"], f["key"]) for f in files] == [
        ("avatar_image", 0, "temp/scene.png"),
        ("driving_audios", 0, "temp/left.wav"),
        ("driving_audios", 1, "temp/right.wav"),
        ("dialogue_audio", 0, "temp/complete.wav"),
    ]


@pytest.mark.parametrize("required_duration", [False, True])
def test_single_speaker_adapter_submits_service_tokens(
        profile, uploads, monkeypatch, tmp_path, required_duration):
    profile["token_schema"]["parameters"]["duration"] = {"required": required_duration}
    original = copy.deepcopy(profile)
    monkeypatch.setattr(d, "_agentics_profile", lambda *a: profile)

    def duration(path):
        assert required_duration, "optional duration must stay controlled by the workflow"
        return 0.75

    monkeypatch.setattr(d, "probe_duration", duration)
    requests = []

    def create(path, payload, headers, **kwargs):
        requests.append(payload)
        assert path == "/v1/media_generation/task"
        assert headers["Idempotency-Key"]
        return {"task": {"task_id": "mock-task"}}

    monkeypatch.setattr(g, "_agentics_json", create)
    monkeypatch.setattr(g, "_agentics_wait", lambda *a, **kw: b"mock-video")
    out = tmp_path / "clip.mp4"
    d._agentics("avatar.png", "speech.wav", str(out), "natural speaking", 42,
                {"profile_code": "infiniteTalk-1char"})
    assert out.read_bytes() == b"mock-video"
    assert len(requests) == 1
    assert requests[0]["parameters"] == {
        "prompt": "natural speaking", "seed": 42,
        **({"duration": 0.75} if required_duration else {}),
    }
    assert [f["token"] for f in requests[0]["files"]] == ["avatar_image", "driving_audios"]
    assert profile == original


def test_single_speaker_adapter_rejects_dual_profile_before_upload(
        profile, uploads, monkeypatch, tmp_path):
    profile["token_schema"]["files"]["driving_audios"].update(min_items=2, max_items=2)
    monkeypatch.setattr(d, "_agentics_profile", lambda *a: profile)
    with pytest.raises(RuntimeError, match="driving_audios"):
        d._agentics("avatar.png", "speech.wav", str(tmp_path / "clip.mp4"), "", 42,
                    {"profile_code": "infiniteTalk-2char"})
    assert uploads == []


def test_video_contract_stays_separate():
    with pytest.raises(RuntimeError, match="audio_guidance_scale"):
        g._agentics_fixed_value("video", "audio_guidance_scale", 1, {})
    with pytest.raises(RuntimeError, match="不能为空"):
        g._agentics_fixed_value("video", "prompt", "", {})


@pytest.mark.parametrize("cfg, adapter", [
    ({"provider": "heygen"}, "_heygen"),
    ({"provider": "klingai"}, "_kling"),
    ({"provider": "comfyui", "mode": "local"}, "_comfy"),
    ({"provider": "comfyui", "mode": "cloud"}, "_comfy"),
    ({"provider": "comfyui", "mode": "rh_ai"}, "_runninghub"),
    ({"provider": "comfyui", "mode": "rh_cn"}, "_runninghub"),
])
def test_other_channels_never_enter_agentics(cfg, adapter, monkeypatch, tmp_path):
    image, audio, output = [str(tmp_path / name) for name in
                            ("avatar.png", "speech.wav", "clip.mp4")]
    Path(image).write_bytes(b"image")
    Path(audio).write_bytes(b"audio")
    monkeypatch.delenv("VIDEOAGENTS_AGENT", raising=False)
    monkeypatch.setattr(d, "get_config", lambda: cfg)

    def forbidden(*args, **kwargs):
        pytest.fail("entered an unrelated channel or Agentics validation")

    for name in ("_agentics", "_heygen", "_kling", "_comfy", "_runninghub",
                 "_agentics_profile", "_agentics_generate"):
        monkeypatch.setattr(d, name, forbidden)
    monkeypatch.setattr(g, "_agentics_payload", forbidden)
    monkeypatch.setattr(g, "_agentics_fixed_value", forbidden)
    calls = []

    def generate(*args):
        calls.append(args)
        Path(output).write_bytes(b"mock-video")
        return output

    monkeypatch.setattr(d, adapter, generate)
    assert d.generate_avatar(image, audio, output, "hello", 42) == output
    expected = ((image, audio, output, "hello", cfg, None)
                if adapter in {"_heygen", "_kling"}
                else (image, audio, output, "hello", 42, cfg, None))
    assert calls == [expected]


@pytest.mark.parametrize("kind", ["video", "image", "music", "tts", "upscale"])
def test_other_agentics_media_keep_existing_validation(kind, uploads):
    schema_kind = "scale" if kind == "upscale" else kind
    field = "text" if kind == "tts" else "prompt"
    profile = {
        "profile_code": "other-media", "media_type": g.AGENTICS_MEDIA_TYPES[kind],
        "token_schema": {"version": 1, "media_type": schema_kind,
                         "parameters": {field: {"required": True}}, "files": {}},
    }
    assert g._agentics_payload(profile, {field: "hello"}, {}) == ({field: "hello"}, [])
    with pytest.raises(RuntimeError, match="缺少必填"):
        g._agentics_payload(profile, {field: ""}, {})
    with pytest.raises(RuntimeError, match="audio_guidance_scale"):
        g._agentics_fixed_value(kind, "audio_guidance_scale", 1, {})
    profile["token_schema"]["parameters"] = {}
    with pytest.raises(RuntimeError, match="映射结构无效"):
        g._agentics_payload(profile, {}, {})
    assert uploads == []
