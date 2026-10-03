"""CI smoke tests: package imports and release version consistency.

A minimal check that keeps CI meaningful on its own; the rest of the
regression suite in this directory is versioned alongside it and must stay
self-contained (no private project data, no network).
"""

import asyncio
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_api_package_imports():
    from services.api import __version__, app

    assert __version__
    assert app.app.title


def test_web_server_imports():
    import apps.web.server  # noqa: F401


def test_release_versions_consistent():
    from services.api import __version__

    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE)
    assert match and match.group(1) == __version__

    root_pkg = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert root_pkg["version"] == __version__

    desktop_pkg = json.loads((ROOT / "apps/desktop/package.json").read_text(encoding="utf-8"))
    assert desktop_pkg["version"] == __version__

    citation = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    match = re.search(r"^version:\s*(\S+)", citation, re.MULTILINE)
    assert match and match.group(1) == __version__


def test_web_streaming_deltas_are_coalesced():
    html = (ROOT / "apps/web/static/index.html").read_text(encoding="utf-8")
    assert "function appendLiveText(container,text)" in html
    assert "appendLiveText(d,ev.text)" in html
    assert "t.className='livetext';t.textContent=ev.text" not in html
    assert ".run .eng{" in html and "text-overflow:ellipsis" in html
    assert '<span class="eng" title="${esc(engLabel)}">' in html


def test_api_event_stream_stops_on_shutdown():
    from services.api import app as api_app

    async def probe():
        api_app._shutdown_event = asyncio.Event()
        response = await api_app.events()
        stream = response.body_iterator
        assert "hello" in await anext(stream)
        waiting = asyncio.create_task(anext(stream))
        api_app.request_shutdown()
        try:
            await asyncio.wait_for(waiting, timeout=1)
        except StopAsyncIteration:
            pass
        else:
            raise AssertionError("SSE stream did not close during shutdown")
        finally:
            api_app._shutdown_event = None

    asyncio.run(probe())


def test_agentics_is_first_generation_provider_and_desktop_only_account_ui():
    models = (ROOT / "apps/web/static/models.html").read_text(encoding="utf-8")
    index = (ROOT / "apps/web/static/index.html").read_text(encoding="utf-8")

    assert "image:{providers:['agentics','openrouter'" in models
    assert "video:{providers:['agentics','openrouter'" in models
    assert "music:{providers:['agentics','openrouter'" in models
    assert "tts:{providers:['agentics','openrouter'" in models
    assert "const TEXT_TABS={agentics:'Agentics'" in models
    assert "window.videoagentsDesktop" in models
    assert "AgenticsLLM 暂不兼容网页版" in models
    assert "models?modality=media" in models
    assert "deepagents:[['agentics','Agentics']" in index


def test_agentics_video_generation_skill_is_conditional_and_valid():
    from services.runtime import core

    skill_id = "08-video-gen/video-generation/agentics-media-generation"
    skill = ROOT / core.AGENTICS_VIDEO_SKILL
    assert core.SKILL_ACTIVATIONS[skill_id] == {
        "kind": "conditional", "condition": "视频渠道为 AgenticsLLM"}
    assert skill.is_file()
    text = skill.read_text(encoding="utf-8")
    assert "modules/genmedia.py video" in text
    assert "不手工请求 Agentics REST 接口" in text


def test_agentics_media_catalog_is_fetched_once_and_grouped_by_type(monkeypatch):
    from services.runtime import core

    calls = []
    monkeypatch.setattr(core, "resolve_agentics_connection", lambda: {
        "api_origin": "https://devapi.agentics.world", "api_key": "desktop-jwt",
        "base_url": "https://devapi.agentics.world/wrapper/openrouter/api/v1",
    })

    def get_json(url, headers=None, timeout=15):
        calls.append((url, headers, timeout))
        return {"code": 0, "msg": "", "data": {"profiles": [
            {"profile_code": "video-a", "name": "Video A", "media_type": 1},
            {"profile_code": "image-a", "name": "Image A", "media_type": 2},
            {"profile_code": "music-a", "name": "Music A", "media_type": 3},
            {"profile_code": "tts-a", "name": "TTS A", "media_type": 4},
        ]}}

    monkeypatch.setattr(core, "_http_get_json", get_json)
    result = asyncio.run(core.api_agentics_models("media"))

    assert len(calls) == 1
    assert calls[0][0] == "https://devapi.agentics.world/v1/media_generation/profiles"
    assert calls[0][1] == {"Authorization": "Bearer desktop-jwt"}
    assert result["media"]["video"]["models"][0]["id"] == "video-a"
    assert result["media"]["image"]["models"][0]["id"] == "image-a"
    assert result["media"]["music"]["models"][0]["id"] == "music-a"
    assert result["media"]["tts"]["models"][0]["id"] == "tts-a"


def test_agentics_generation_uses_summary_list_then_cached_profile_detail(monkeypatch):
    from modules import genmedia

    genmedia._AGENTICS_PROFILE_LIST_CACHE = None
    genmedia._AGENTICS_PROFILE_DETAIL_CACHE.clear()
    calls = []

    def api(path, payload=None, extra_headers=None, timeout=30):
        calls.append(path)
        if path == "/v1/media_generation/profiles":
            return {"profiles": [
                {"contract_version": "media-generation/v1", "profile_code": "video-a",
                 "name": "Video A", "media_type": 1, "updated_at": "2026-09-03T01:00:00Z"},
                {"contract_version": "media-generation/v1", "profile_code": "image-a",
                 "name": "Image A", "media_type": 2, "updated_at": "2026-09-03T01:00:00Z"},
            ]}
        code = path.rsplit("/", 1)[-1]
        media_type = 1 if code == "video-a" else 2
        return {"profile": {
            "contract_version": "media-generation/v1", "profile_code": code,
            "media_type": media_type, "updated_at": "2026-09-03T01:00:00Z",
            "token_schema": {"version": 1, "media_type": (
                "video" if media_type == 1 else "image"),
                "parameters": {"prompt": {"required": True, "targets": [{}]}},
                "files": {}},
        }}

    monkeypatch.setattr(genmedia, "_agentics_json", api)
    assert genmedia._agentics_profile("video", "video-a")["token_schema"]
    assert genmedia._agentics_profile("image", "image-a")["token_schema"]
    assert genmedia._agentics_profile("video", "video-a")["token_schema"]

    assert calls.count("/v1/media_generation/profiles") == 1
    assert calls.count("/v1/media_generation/profiles/video-a") == 1
    assert calls.count("/v1/media_generation/profiles/image-a") == 1


def test_agentics_schema_filters_parameters_and_maps_uploaded_files(monkeypatch, tmp_path):
    from modules import genmedia

    reference = tmp_path / "reference.png"
    reference.write_bytes(b"image")
    uploaded = []
    monkeypatch.setattr(genmedia, "_agentics_upload", lambda token, path: uploaded.append(
        (token, path)) or {"token": token, "url": "https://files.example/ref"})
    profile = {
        "profile_code": "image-demo",
        "media_type": 2,
        "token_schema": {
            "version": 1,
            "media_type": "image",
            "parameters": {
                "prompt": {"required": True, "targets": [{}]},
                "steps": {"default": 20, "targets": [{}]},
            },
            "files": {"input_images": {
                "required": True, "min_items": 1, "max_items": 1,
                "targets": [{"index": 0}],
            }},
        },
    }

    parameters, files = genmedia._agentics_payload(
        profile, {"prompt": "hello", "seed": 7}, {"input_images": [str(reference)]})

    # Profile defaults are applied by the service; the client sends only explicit values.
    assert parameters == {"prompt": "hello"}
    assert files == [{"token": "input_images", "url": "https://files.example/ref",
                      "index": 0}]
    assert uploaded == [("input_images", str(reference))]


def test_agentics_schema_uses_fixed_fields_and_server_side_repeat_last(monkeypatch, tmp_path):
    from modules import genmedia

    reference = tmp_path / "reference.png"
    reference.write_bytes(b"image")
    uploads = []
    monkeypatch.setattr(genmedia, "_agentics_upload", lambda token, path: uploads.append(
        (token, path)) or {"token": token, "url": "https://files.example/ref", "key": "ref"})
    profile = {"profile_code": "video-a", "media_type": 1, "token_schema": {
        "version": 1, "media_type": "video",
        "parameters": {
            "prompt": {"required": True, "targets": [{}]},
            "duration": {"default": 10, "targets": [{}]},
            "resolution": {"value_map": {"480P": 0.4, "768P": 0.9},
                           "targets": [{}]},
        },
        "files": {"reference_images": {
            "required": True, "min_items": 1, "max_items": 3,
            "fill": "repeat_last",
            "targets": [{"index": 0}, {"index": 1}, {"index": 2}],
        }},
    }}

    parameters, files = genmedia._agentics_payload(
        profile, {"prompt": "hello", "duration": 5, "resolution": "480p"},
        {"reference_images": [str(reference)]})

    assert parameters == {"prompt": "hello", "duration": 5, "resolution": "480P"}
    assert uploads == [("reference_images", str(reference))]
    assert files == [{"token": "reference_images", "url": "https://files.example/ref",
                      "key": "ref", "index": 0}]


def test_agentics_schema_enforces_fixed_constraints_and_profile_file_limits(tmp_path):
    from modules import genmedia

    profile = {"profile_code": "video-a", "media_type": 1, "token_schema": {
        "version": 1, "media_type": "video",
        "parameters": {"prompt": {"required": True, "targets": [{}]},
                       "fps": {"targets": [{}]}},
        "files": {"reference_images": {
            "min_items": 1, "max_items": 1, "targets": [{"index": 0}]}}
    }}
    with pytest.raises(RuntimeError, match="fps.*120"):
        genmedia._agentics_payload(
            profile, {"prompt": "hello", "fps": 121},
            {"reference_images": [str(tmp_path / "a.png")]})
    with pytest.raises(RuntimeError, match="最多接受 1"):
        genmedia._agentics_payload(
            profile, {"prompt": "hello", "fps": 24},
            {"reference_images": [str(tmp_path / "a.png"), str(tmp_path / "b.png")]})


def test_agentics_generation_entrypoints_use_fixed_contract_fields(monkeypatch):
    from modules import genmedia

    calls = []
    monkeypatch.setattr(genmedia, "_forbid_dispatch_layer", lambda kind: None)
    monkeypatch.setattr(genmedia, "get_config", lambda kind: {
        "provider": "agentics", "profile_code": f"{kind}-profile",
        "model": f"{kind}-profile"})
    monkeypatch.setattr(genmedia, "_agentics_generate",
                        lambda kind, cfg, values, files, **kwargs: calls.append(
                            (kind, values, files)) or b"result")
    monkeypatch.setattr(genmedia, "_save", lambda data, output: output)

    genmedia.generate_image("image", "out.jpg", refs=["input.png"])
    genmedia.generate_video(
        "video", "out.mp4", duration=5, resolution="480p",
        refs=["ref.png"], audio_refs=["ref.wav"], video_refs=["ref.mp4"])
    genmedia.generate_music("music", "out.wav", 30, lyrics="la la")

    image = calls[0]
    assert image[1]["num_images"] == 1
    assert image[1]["output_format"] == "jpeg"
    assert "n" not in image[1] and "size" not in image[1]
    assert image[2] == {"input_images": ["input.png"]}

    video = calls[1]
    assert video[1]["duration"] == 5
    assert video[2]["reference_images"] == ["ref.png"]
    assert video[2]["reference_audios"] == ["ref.wav"]
    assert video[2]["reference_videos"] == ["ref.mp4"]

    music = calls[2]
    assert music[1]["lyrics"] == "la la"
    assert music[1]["instrumental"] is False
    assert music[1]["output_format"] == "wav"


def test_agentics_image_negative_merges_into_prompt_without_negative_slot(monkeypatch):
    """profile 未声明 negative_prompt 时 --negative 并入正面提示词(同 RunningHub 直绑写法),声明了则原样传。"""
    from modules import genmedia

    calls = []
    params = {"prompt": {}}
    monkeypatch.setattr(genmedia, "get_config", lambda kind: {
        "provider": "agentics", "t2i": "t2i-demo", "i2i": "i2i-demo"})
    monkeypatch.setattr(genmedia, "_agentics_profile", lambda kind, code, refresh=False: {
        "profile_code": code, "token_schema": {"parameters": params}})
    monkeypatch.setattr(genmedia, "_agentics_generate",
                        lambda kind, cfg, values, files, **kwargs: calls.append(values) or b"result")
    monkeypatch.setattr(genmedia, "_save", lambda data, output: output)

    genmedia._generate_image("a cat", "out.png", "dogs", refs=["ref.png"])
    assert calls[0]["prompt"] == "a cat\nExclude from the image: dogs"
    assert calls[0]["negative_prompt"] == ""

    params["negative_prompt"] = {}
    genmedia._generate_image("a cat", "out.png", "dogs", refs=["ref.png"])
    assert calls[1]["prompt"] == "a cat" and calls[1]["negative_prompt"] == "dogs"


def test_agentics_media_task_is_polled_and_downloaded_with_idempotency(monkeypatch):
    from modules import genmedia

    profile = {"profile_code": "image-demo", "media_type": 2, "token_schema": {
        "version": 1, "media_type": "image",
        "parameters": {"prompt": {"required": True, "targets": [{}]}}, "files": {},
    }}
    calls = []
    monkeypatch.setattr(genmedia, "_agentics_profile", lambda kind, code: profile)

    def api(path, payload=None, extra_headers=None, timeout=30):
        calls.append((path, payload, extra_headers, timeout))
        if path == "/v1/media_generation/task":
            return {"task": {"task_id": "task-1", "status": 0}}
        return {"task": {"task_id": "task-1", "status": 2,
                         "result": {"artifacts": [{"url": "https://files.example/out.png"}]}}}

    monkeypatch.setattr(genmedia, "_agentics_json", api)
    monkeypatch.setattr(genmedia.time, "sleep", lambda _: None)
    monkeypatch.setattr(genmedia, "_request", lambda url, timeout=120, **_: b"result")

    result = genmedia._agentics_generate(
        "image", {"profile_code": "image-demo"}, {"prompt": "hello"}, {})

    assert result == b"result"
    assert calls[0][0] == "/v1/media_generation/task"
    assert calls[0][1]["profile_code"] == "image-demo"
    assert calls[0][2]["Idempotency-Key"]
    assert calls[1][0] == "/v1/media_generation/task/task-1"


def test_agentics_poll_tls_timeout_keeps_waiting_same_task(monkeypatch):
    from modules import genmedia

    profile = {"profile_code": "video-demo", "media_type": 1, "token_schema": {
        "version": 1, "media_type": "video",
        "parameters": {"prompt": {"required": True, "targets": [{}]}}, "files": {},
    }}
    calls = []
    monkeypatch.setattr(genmedia, "_agentics_profile", lambda kind, code: profile)

    def api(path, payload=None, extra_headers=None, timeout=30):
        calls.append(path)
        if path == "/v1/media_generation/task":
            return {"task": {"task_id": "task-1", "status": 0}}
        if calls.count("/v1/media_generation/task/task-1") == 1:
            raise genmedia._TransportError(
                "https://devapi.agentics.world", TimeoutError("TLS handshake timed out"))
        return {"task": {"task_id": "task-1", "status": 2,
                         "result": {"artifacts": [{"url": "https://files.example/out.mp4"}]}}}

    monkeypatch.setattr(genmedia, "_agentics_json", api)
    monkeypatch.setattr(genmedia.time, "sleep", lambda _: None)
    monkeypatch.setattr(genmedia, "_request", lambda url, timeout=120, **_: b"video")

    result = genmedia._agentics_generate(
        "video", {"profile_code": "video-demo"}, {"prompt": "hello"}, {})

    assert result == b"video"
    assert calls.count("/v1/media_generation/task") == 1
    assert calls.count("/v1/media_generation/task/task-1") == 2


def test_agentics_completed_artifact_download_retries_transport_error(monkeypatch):
    from modules import genmedia

    requests = []

    def request(url, timeout=120, **kwargs):
        requests.append(url)
        if len(requests) == 1:
            raise genmedia._TransportError(url, TimeoutError("TLS handshake timed out"))
        return b"video"

    monkeypatch.setattr(genmedia, "_request", request)
    monkeypatch.setattr(genmedia.time, "sleep", lambda _: None)

    result = genmedia._agentics_wait("video", "task-1", {
        "task_id": "task-1", "status": 2,
        "result": {"artifacts": [{"url": "https://files.example/out.mp4"}]}})

    assert result == b"video"
    assert requests == ["https://files.example/out.mp4", "https://files.example/out.mp4"]


def test_request_classifies_wrapped_tls_timeout_as_transport_error(monkeypatch):
    from modules import genmedia

    monkeypatch.setattr(genmedia.urllib.request, "urlopen", lambda *args, **kwargs: (
        (_ for _ in ()).throw(genmedia.urllib.error.URLError(
            TimeoutError("handshake operation timed out")))))

    with pytest.raises(genmedia._TransportError, match="handshake operation timed out"):
        genmedia._request("https://devapi.agentics.world/health", timeout=1)


def test_reclaim_accepts_agentics_uuid_without_creating_new_task(monkeypatch):
    from modules import genmedia

    task_id = "c267c93d-b965-42f5-9a84-6c0ecf8a110e"
    waited = []
    monkeypatch.setattr(genmedia, "_forbid_dispatch_layer", lambda kind: None)
    monkeypatch.setattr(genmedia, "_agentics_wait",
                        lambda kind, current: waited.append((kind, current)) or b"video")
    monkeypatch.setattr(genmedia, "_save", lambda data, output: output)

    result = genmedia.reclaim_video(task_id, "out.mp4")

    assert result == "out.mp4"
    assert waited == [("video", task_id)]


def test_agentics_video_local_deadline_preserves_task_for_reclaim(monkeypatch):
    from modules import genmedia

    monkeypatch.setattr(genmedia, "AGENTICS_VIDEO_TIMEOUT", 0)
    monkeypatch.setattr(genmedia, "_agentics_cancel", lambda task_id: (
        (_ for _ in ()).throw(AssertionError("video deadline must not cancel task"))))

    with pytest.raises(RuntimeError, match="任务未被取消.*reclaim"):
        genmedia._agentics_wait("video", "task-1", {"task_id": "task-1", "status": 1})


def test_agentics_tts_voice_id_profile_does_not_select_reference_audio(monkeypatch):
    from modules import genmedia

    profile = {"profile_code": "voice-id", "media_type": 4, "token_schema": {
        "version": 1, "media_type": "tts",
        "parameters": {"text": {"required": True, "targets": [{}]},
                       "voice_id": {"required": True, "targets": [{}]}},
        "files": {},
    }}
    captured = {}
    monkeypatch.setattr(genmedia, "get_config", lambda kind: {
        "provider": "agentics", "profile_code": "voice-id", "model": "voice-id"})
    monkeypatch.setattr(genmedia, "_agentics_profile", lambda kind, code: profile)
    monkeypatch.setattr(genmedia, "_resolve_tts_reference", lambda *args, **kwargs: (
        (_ for _ in ()).throw(AssertionError("text-only profile must not select audio"))))
    monkeypatch.setattr(genmedia, "_agentics_generate", lambda kind, cfg, values, files,
                        **kwargs: captured.update(values=values, files=files, kwargs=kwargs) or b"audio")
    monkeypatch.setattr(genmedia, "_save", lambda data, output: output)

    result = genmedia.generate_tts("hello", "out.mp3", voice="voice-123")

    assert result == "out.mp3"
    assert captured["values"]["voice_id"] == "voice-123"
    assert captured["values"]["output_format"] == "mp3"
    assert captured["files"] == {"reference_audios": []}


def test_agentics_tts_reference_profile_uses_fixed_indexed_file(monkeypatch, tmp_path):
    from modules import genmedia

    reference = tmp_path / "voice.wav"
    reference.write_bytes(b"voice")
    profile = {"profile_code": "index-tts", "media_type": 4, "token_schema": {
        "version": 1, "media_type": "tts",
        "parameters": {"text": {"required": True, "targets": [{}]}},
        "files": {"reference_audios": {
            "required": True, "min_items": 1, "max_items": 1,
            "targets": [{"index": 0}]}}
    }}
    uploads = []
    monkeypatch.setattr(genmedia, "_agentics_upload", lambda token, path: uploads.append(
        (token, path)) or {"token": token, "url": "https://files.example/voice", "key": "voice"})

    parameters, files = genmedia._agentics_payload(
        profile, {"text": "hello"}, {"reference_audios": [str(reference)]})

    assert parameters == {"text": "hello"}
    assert uploads == [("reference_audios", str(reference))]
    assert files == [{"token": "reference_audios", "url": "https://files.example/voice",
                      "key": "voice", "index": 0}]


def test_agentics_tts_selects_audio_only_when_profile_declares_file(monkeypatch, tmp_path):
    from modules import genmedia

    reference = tmp_path / "selected.wav"
    reference.write_bytes(b"voice")
    profile = {"profile_code": "reference-tts", "media_type": 4, "token_schema": {
        "version": 1, "media_type": "tts",
        "parameters": {"text": {"required": True, "targets": [{}]}},
        "files": {"reference_audios": {
            "required": True, "min_items": 1, "max_items": 1,
            "targets": [{"index": 0}]}}
    }}
    captured = {}
    monkeypatch.setattr(genmedia, "get_config", lambda kind: {
        "provider": "agentics", "profile_code": "reference-tts", "model": "reference-tts"})
    monkeypatch.setattr(genmedia, "_agentics_profile", lambda kind, code: profile)
    monkeypatch.setattr(genmedia, "_narrator_card", lambda project, output: ({}, None))
    monkeypatch.setattr(genmedia, "_resolve_tts_reference", lambda *args, **kwargs: {
        "path": str(reference), "file": reference.name, "score": 1, "reason": "test"})
    monkeypatch.setattr(genmedia, "_agentics_generate", lambda kind, cfg, values, files,
                        **kwargs: captured.update(values=values, files=files, kwargs=kwargs) or b"audio")
    monkeypatch.setattr(genmedia, "_save", lambda data, output: output)

    result = genmedia.generate_tts("hello", "out.mp3")

    assert result == "out.mp3"
    assert captured["files"] == {"reference_audios": [str(reference)]}
    assert captured["kwargs"]["profile"] is profile


def test_agentics_validation_error_is_not_retried(monkeypatch):
    from modules import genmedia

    profile = {"profile_code": "video-a", "media_type": 1, "token_schema": {
        "version": 1, "media_type": "video",
        "parameters": {"prompt": {"required": True, "targets": [{}]}}, "files": {},
    }}
    calls = []
    monkeypatch.setattr(genmedia, "_agentics_profile", lambda kind, code: profile)

    def api(path, payload=None, extra_headers=None, timeout=30):
        calls.append(path)
        raise genmedia.AgenticsServiceError(422, 42201, "validation failed", {
            "reason": "constraint_violation", "location": "parameters", "field": "duration",
            "message": "duration is invalid",
        })

    monkeypatch.setattr(genmedia, "_agentics_json", api)
    with pytest.raises(genmedia.AgenticsServiceError, match="parameters.duration"):
        genmedia._agentics_generate(
            "video", {"profile_code": "video-a"}, {"prompt": "hello"}, {})
    assert calls == ["/v1/media_generation/task"]


def test_legacy_empty_openrouter_desktop_config_migrates_to_agentics(monkeypatch):
    from services.runtime import core

    monkeypatch.setenv("VIDEOAGENTS_USER_JWT", "signed-in")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    config = {
        "image": {"provider": "openrouter", "openrouter": {"api_key": ""}},
        "deepagents": {"provider": "openrouter", "openrouter": {"api_key": ""}},
    }
    core._migrate_genconfig(config)
    assert config["image"]["provider"] == "agentics"
    assert config["deepagents"]["provider"] == "agentics"

    monkeypatch.setenv("OPENROUTER_API_KEY", "user-owned")
    explicit = {"image": {"provider": "openrouter", "openrouter": {"api_key": ""}}}
    core._migrate_genconfig(explicit)
    assert explicit["image"]["provider"] == "openrouter"


def test_openrouter_never_implicitly_uses_agentics_account(monkeypatch):
    from services.runtime import core

    monkeypatch.setenv("VIDEOAGENTS_USER_JWT", "signed-in")
    monkeypatch.setenv(
        "VIDEOAGENTS_OPENROUTER_WRAPPER_URL",
        "https://api.agentics.world/wrapper/openrouter",
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    connection = core.resolve_openrouter_connection("")

    assert connection == {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "",
        "uses_wrapper": False,
    }


def test_agentics_image_config_splits_text_to_image_and_image_to_image(monkeypatch, tmp_path):
    """图像 Agentics 渠道分文生图/图生图两个 profile:无参考图用 t2i、带参考图用 i2i,显式 --model 优先。"""
    from modules import genmedia

    cfg_path = tmp_path / "genconfig.json"
    cfg_path.write_text(json.dumps({"image": {
        "provider": "agentics",
        "agentics": {"t2i": "flux2-dev-t2i", "i2i": "flux2-dev-i2i-10ref"}}}))
    monkeypatch.setattr(genmedia, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(genmedia, "_agentics_connection", lambda: ("https://api.agentics.world", "jwt"))
    monkeypatch.delenv("VIDEOAGENTS_IMAGE_PROVIDER", raising=False)
    monkeypatch.delenv("VIDEOAGENTS_IMAGE_MODEL", raising=False)
    monkeypatch.setattr(genmedia, "_forbid_dispatch_layer", lambda kind: None)

    cfg = genmedia.get_config("image")
    assert cfg["t2i"] == "flux2-dev-t2i" and cfg["i2i"] == "flux2-dev-i2i-10ref"
    assert cfg["model"] == "" and cfg["profile_code"] == ""

    calls = []
    monkeypatch.setattr(genmedia, "_agentics_generate",
                        lambda kind, cfg, values, files, **kwargs: calls.append(
                            (cfg["profile_code"], files)) or b"result")
    monkeypatch.setattr(genmedia, "_save", lambda data, output: output)
    genmedia._generate_image("hello", "out.png")
    genmedia._generate_image("hello", "out.png", refs=["ref.png"])
    assert calls[0][0] == "flux2-dev-t2i" and calls[0][1] == {"input_images": []}
    assert calls[1][0] == "flux2-dev-i2i-10ref" and calls[1][1] == {"input_images": ["ref.png"]}

    # 显式 --model(环境变量)优先于两侧默认
    monkeypatch.setenv("VIDEOAGENTS_IMAGE_MODEL", "custom-i2i")
    assert genmedia.get_config("image")["profile_code"] == "custom-i2i"


def test_genconfig_migrates_legacy_agentics_image_profile_and_empty_defaults():
    from services.runtime import core

    legacy = {"image": {"agentics": {"profile_code": "old-image"}}}
    core._migrate_genconfig(legacy)
    assert legacy["image"]["agentics"] == {"t2i": "old-image", "i2i": "old-image"}

    empty = {"image": {"agentics": {"profile_code": "", "t2i": "", "i2i": ""}},
             "deepagents": {"agentics": {"model": "", "custom_model": ""}}}
    core._migrate_genconfig(empty)
    merged = core._merge(core.DEFAULT_GENCONFIG, empty)
    assert merged["image"]["agentics"] == {"t2i": "flux2-dev-t2i", "i2i": "flux2-dev-i2i-10ref"}
    assert merged["deepagents"]["agentics"]["model"] == "z-ai/glm-5.3"


def test_agentics_tts_config_splits_voice_design_and_voice_clone(monkeypatch, tmp_path):
    """TTS Agentics 渠道分 Voice Design / Voice Clone 两个 profile:出嗓音样本(*_voiceprint.*)用 design 并带嗓音描述,
    其余台词用 clone;design profile 未声明描述参数时回落 clone。"""
    from modules import genmedia

    cfg_path = tmp_path / "genconfig.json"
    cfg_path.write_text(json.dumps({"tts": {
        "provider": "agentics",
        "agentics": {"design": "qwen3tts-voicedesign", "clone": "qwen3tts-clone"}}}))
    monkeypatch.setattr(genmedia, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(genmedia, "_agentics_connection", lambda: ("https://api.agentics.world", "jwt"))
    monkeypatch.setattr(genmedia, "_forbid_dispatch_layer", lambda kind: None)

    cfg = genmedia.get_config("tts")
    assert cfg["design"] == "qwen3tts-voicedesign" and cfg["clone"] == "qwen3tts-clone"
    assert cfg["model"] == "" and cfg["profile_code"] == ""

    profiles = {
        "qwen3tts-voicedesign": {"profile_code": "qwen3tts-voicedesign", "token_schema": {
            "parameters": {"text": {}, "instruct": {}}, "files": {}}},
        "qwen3tts-clone": {"profile_code": "qwen3tts-clone", "token_schema": {
            "parameters": {"text": {}}, "files": {}}},
    }
    monkeypatch.setattr(genmedia, "_agentics_profile", lambda kind, code, refresh=False: profiles[code])
    monkeypatch.setattr(genmedia, "_agentics_profile_kind", lambda profile, schema: "tts")
    monkeypatch.setattr(genmedia, "_seedaudio_desc", lambda *a: ("女性,音高中低", "平静自然"))
    calls = []
    monkeypatch.setattr(genmedia, "_agentics_generate",
                        lambda kind, cfg, values, files, **kwargs: calls.append(
                            (cfg["profile_code"], values)) or b"result")
    monkeypatch.setattr(genmedia, "_save", lambda data, output: output)

    genmedia.generate_tts("样本句", "refs/CHAR-0001_voiceprint.mp3", character="CHAR-0001")
    genmedia.generate_tts("台词句", "lines/ep01_line.mp3", voice="narrator-id")
    assert calls[0][0] == "qwen3tts-voicedesign" and calls[0][1]["instruct"] == "女性,音高中低"
    assert calls[1][0] == "qwen3tts-clone" and "instruct" not in calls[1][1]

    # design profile 未声明嗓音描述参数 → 回落 Voice Clone profile
    profiles["qwen3tts-voicedesign"]["token_schema"]["parameters"] = {"text": {}}
    genmedia.generate_tts("样本句", "refs/CHAR-0001_voiceprint.mp3", voice="narrator-id")
    assert calls[2][0] == "qwen3tts-clone"


def test_genconfig_migrates_legacy_agentics_tts_profile_and_empty_defaults():
    from services.runtime import core

    legacy = {"tts": {"agentics": {"profile_code": "old-tts"}}}
    core._migrate_genconfig(legacy)
    assert legacy["tts"]["agentics"] == {"design": "old-tts", "clone": "old-tts", "voice_mode": "design"}

    empty = {"tts": {"agentics": {"profile_code": "", "design": "", "clone": ""}}}
    core._migrate_genconfig(empty)
    merged = core._merge(core.DEFAULT_GENCONFIG, empty)
    assert merged["tts"]["agentics"] == {"design": "qwen3tts-voicedesign", "clone": "qwen3tts-clone",
                                         "voice_mode": "design"}
