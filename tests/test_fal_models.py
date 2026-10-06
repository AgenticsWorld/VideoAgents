"""Fal 模型发现:目录归并、端点解析、按参数表整形请求体;全程用假的目录接口,不联网。"""
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import modules.fal_models as fm  # noqa: E402
import modules.genmedia as g  # noqa: E402

U = lambda p: f"URL:{p}"  # noqa: E731


def _openapi(endpoint, props, required=("prompt",), schemas=None):
    return {"paths": {"/" + endpoint: {"post": {"requestBody": {"content": {"application/json": {
                "schema": {"$ref": "#/components/schemas/TheInput"}}}}}}},
            "components": {"schemas": {"TheInput": {"type": "object", "properties": props, "required": list(required)},
                                       **(schemas or {})}}}


STR = {"type": "string"}
NULLABLE_INT = {"anyOf": [{"type": "integer"}, {"type": "null"}]}
ENDPOINTS = {
    "acme/vid": ("text-to-video", _openapi("acme/vid", {
        "prompt": STR, "duration": {"type": "string", "enum": ["4s", "6s", "8s"]},
        "resolution": {"type": "string", "enum": ["720p", "1080p", "4k"]},
        "aspect_ratio": {"type": "string", "enum": ["16:9", "9:16"]},
        "generate_audio": {"type": "boolean"}, "seed": NULLABLE_INT})),
    "acme/vid/image-to-video": ("image-to-video", _openapi("acme/vid/image-to-video", {
        "prompt": STR, "image_url": STR, "duration": {"type": "string", "enum": ["5", "10"]}},
        required=("prompt", "image_url"))),
    "acme/vid/first-last-frame-to-video": ("image-to-video", _openapi("acme/vid/first-last-frame-to-video", {
        "prompt": STR, "first_frame_url": STR, "last_frame_url": STR,
        "duration": {"type": "integer", "minimum": 3, "maximum": 10}},
        required=("prompt", "first_frame_url", "last_frame_url"))),
    "acme/vid/reference-to-video": ("image-to-video", _openapi("acme/vid/reference-to-video", {
        "prompt": STR, "image_urls": {"type": "array", "items": STR}})),
    "acme/img": ("text-to-image", _openapi("acme/img", {
        "prompt": STR, "seed": NULLABLE_INT, "output_format": {"type": "string", "enum": ["jpeg", "png"]},
        "image_size": {"anyOf": [{"$ref": "#/components/schemas/ImageSize"},
                                 {"type": "string", "enum": ["square_hd", "landscape_16_9", "portrait_16_9"]}]}},
        schemas={"ImageSize": {"type": "object", "properties": {"width": {"type": "integer"}, "height": {"type": "integer"}}}})),
    "acme/img/edit": ("image-to-image", _openapi("acme/img/edit", {
        "prompt": STR, "negative_prompt": STR, "image_urls": {"type": "array", "items": STR},
        "aspect_ratio": {"type": "string", "enum": ["auto", "1:1", "16:9", "9:16"]},
        "resolution": {"type": "string", "enum": ["1K", "2K", "4K"]}})),
    "acme/single/image-to-image": ("image-to-image", _openapi("acme/single/image-to-image", {
        "prompt": STR, "image_url": STR}, required=("prompt", "image_url"))),
}


@pytest.fixture
def catalog(monkeypatch, tmp_path):
    calls = []

    def fake_get(params, api_key="", timeout=30):
        calls.append((dict(params), api_key))
        eid = params.get("endpoint_id")
        if eid:
            if eid == "acme/shell":             # 不存在的端点:接口回一条没有 metadata 的空壳
                return {"models": [{"endpoint_id": eid, "openapi": {"error": "not found"}}]}
            if eid not in ENDPOINTS:
                return {"models": []}
            category, openapi = ENDPOINTS[eid]
            return {"models": [{"endpoint_id": eid, "metadata": {"display_name": eid, "category": category}, "openapi": openapi}]}
        rows = [{"endpoint_id": e, "metadata": {"display_name": f"Acme {e.rsplit('/', 1)[-1].replace('-', ' ').title()}",
                                               "category": c, "kind": "inference", "description": f"desc {e}",
                                               "updated_at": "2026-10-01"}}
                for e, (c, _) in ENDPOINTS.items() if c == params["category"]
                and (not params.get("q") or params["q"] in e)]
        return {"models": rows}
    monkeypatch.setattr(fm, "_get", fake_get)
    monkeypatch.setattr(fm, "CACHE_DIR", tmp_path / "fal_models")
    fm._SEARCH_CACHE.clear()
    return calls


def test_search_groups_task_endpoints_into_one_model(catalog):
    rows = {r["id"]: r for r in fm.search("video")}
    assert set(rows) == {"acme/vid"}
    assert rows["acme/vid"]["tasks"] == ["first-last-frame-to-video", "image-to-video", "reference-to-video", "text-to-video"]
    assert rows["acme/vid"]["description"] == "desc acme/vid"
    images = {r["id"]: r["tasks"] for r in fm.search("image")}
    assert images == {"acme/img": ["edit", "text-to-image"], "acme/single": ["image-to-image"]}
    n = len(catalog)
    fm.search("video")                                   # 10 分钟内同一查询走缓存
    assert len(catalog) == n
    assert [r["id"] for r in fm.search("image", "single")] == ["acme/single"]
    with pytest.raises(ValueError):
        fm.search("music")


def test_family_name_drops_task_words():
    assert fm._family_name("MiniMax H3 Max Text to Video") == "MiniMax H3 Max"
    assert fm._family_name("Kling 3.0 Pro (Image to Video)") == "Kling 3.0 Pro"
    assert fm._family_name("Nano Banana Pro Edit") == "Nano Banana Pro"
    assert fm._family_name("Kling Video v3 Text to Video [Pro]") == "Kling Video v3 [Pro]"
    assert fm._family_name("Text to Video") == "Text to Video"       # 全是任务词时不删空


def test_lookup_caches_hits_and_misses_on_disk(catalog):
    assert fm.lookup("acme/vid")["category"] == "text-to-video"
    assert fm.lookup("acme/nope") is None and fm.lookup("acme/shell") is None
    n = len(catalog)
    assert fm.lookup("acme/vid")["input"]["required"] == ["prompt"]
    assert fm.lookup("acme/nope") is None
    assert len(catalog) == n                              # 命中与「不存在」都读盘,不再请求
    fm.lookup("acme/vid", refresh=True, api_key="k")
    assert catalog[-1] == ({"endpoint_id": "acme/vid", "expand": "openapi-3.0"}, "k")


def test_resolve_video_picks_endpoint_by_input(catalog):
    assert fm.resolve_video("acme/vid", "text-to-video")["endpoint_id"] == "acme/vid"          # 文生端点就是前缀本身
    assert fm.resolve_video("acme/vid", "image-to-video")["endpoint_id"] == "acme/vid/image-to-video"
    assert fm.resolve_video("acme/vid", "image-to-video", needs_last=True)["endpoint_id"] == "acme/vid/first-last-frame-to-video"
    assert fm.resolve_video("acme/vid", "reference-to-video")["endpoint_id"] == "acme/vid/reference-to-video"
    assert fm.resolve_video("acme/vid/image-to-video", "text-to-video")["endpoint_id"] == "acme/vid/image-to-video"
    assert fm.resolve_video("nobody/private", "text-to-video") is None


def test_shape_video_follows_each_endpoints_spelling(catalog):
    body, notes = fm.shape_video(fm.resolve_video("acme/vid", "text-to-video"), prompt="p", duration=5,
                                 resolution="2k", aspect="21:9", seed=7, gen_audio=False)
    assert body == {"prompt": "p", "duration": "4s", "resolution": "1080p", "aspect_ratio": "16:9",
                    "seed": 7, "generate_audio": False}
    assert len(notes) == 3 and "4s" in notes[0]
    body, notes = fm.shape_video(fm.resolve_video("acme/vid", "image-to-video"), prompt="p", duration=10,
                                 resolution="720p", aspect="16:9", seed=1, gen_audio=True, first="a.png", to_url=U)
    assert body == {"prompt": "p", "duration": "10", "image_url": "URL:a.png"}      # 不收的字段不发
    assert notes == ["该端点没有有声 / 无声开关,--generate-audio 已忽略"]
    body, notes = fm.shape_video(fm.resolve_video("acme/vid", "image-to-video", needs_last=True), prompt="p",
                                 duration=12.4, first="a.png", last="b.png", to_url=U)
    assert body == {"prompt": "p", "duration": 10, "first_frame_url": "URL:a.png", "last_frame_url": "URL:b.png"}
    body, _ = fm.shape_video(fm.resolve_video("acme/vid", "reference-to-video"), prompt="p", refs=["a.png", "b.png"], to_url=U)
    assert body == {"prompt": "p", "image_urls": ["URL:a.png", "URL:b.png"]}


def test_shape_video_refuses_inputs_the_endpoint_cannot_take(catalog):
    i2v = fm.resolve_video("acme/vid/image-to-video", "image-to-video")
    with pytest.raises(RuntimeError, match="不支持尾帧图"):
        fm.shape_video(i2v, prompt="p", first="a.png", last="b.png")
    with pytest.raises(RuntimeError, match="必填参数 image_url"):
        fm.shape_video(i2v, prompt="p")
    ref = fm.resolve_video("acme/vid", "reference-to-video")
    with pytest.raises(RuntimeError, match="收不下本次的参考视频"):
        fm.shape_video(ref, prompt="p", refs=["a.png"], video_refs=["v.mp4"])
    with pytest.raises(RuntimeError, match="不收首帧图"):
        fm.shape_video(fm.resolve_video("acme/vid", "text-to-video"), prompt="p", first="a.png")


def test_shape_image_sizes_and_refs(catalog):
    body, _ = fm.shape_image(fm.resolve_image("acme/img", False), prompt="p", negative="blur", width=2560, height=1440,
                             seed=3, fmt="png")
    assert body == {"prompt": "p\nAvoid: blur", "image_size": {"width": 2560, "height": 1440}, "seed": 3, "output_format": "png"}
    edit = fm.resolve_image("acme/img", True)
    assert edit["endpoint_id"] == "acme/img/edit"
    body, _ = fm.shape_image(edit, prompt="p", negative="blur", width=1440, height=2560, seed=3, fmt="webp",
                             refs=["a.png", "b.png"], to_url=U)
    assert body == {"prompt": "p", "negative_prompt": "blur", "aspect_ratio": "9:16", "resolution": "2K",
                    "image_urls": ["URL:a.png", "URL:b.png"]}
    single = fm.resolve_image("acme/single", True)
    assert fm.shape_image(single, prompt="p", width=1024, height=1024, refs=["a.png"], to_url=U)[0] == {
        "prompt": "p", "image_url": "URL:a.png"}
    with pytest.raises(RuntimeError, match="只有单个 image_url 位"):
        fm.shape_image(single, prompt="p", width=1024, height=1024, refs=["a.png", "b.png"])
    assert fm.resolve_image("acme/single", False) is None      # 没有文生图端点


def test_enum_only_image_size_picks_closest_named_size():
    entry = {"endpoint_id": "x", "input": {"required": [], "props": {
        "prompt": {"types": ["string"]}, "image_size": {"types": ["string"], "enum": ["square_hd", "landscape_16_9", "portrait_16_9"]}}}}
    assert fm.shape_image(entry, prompt="p", width=1080, height=1920)[0]["image_size"] == "portrait_16_9"


def test_genmedia_uses_catalog_for_unknown_models_only(catalog, monkeypatch):
    cfg = {"model": "acme/vid", "api_key": "fal-key"}
    ep, body = g._fal_video_body(cfg, "p", "a.png", "b.png", 8, "720p", "16:9", 5, [], [], None, [], U, U)
    assert ep == "acme/vid/first-last-frame-to-video" and body["last_frame_url"] == "URL:b.png" and body["duration"] == 8
    assert all(key == "fal-key" for _, key in catalog)                       # 有 Key 就带上
    ep, body = g._fal_image_body({"model": "acme/img", "api_key": ""}, "p", "", ["a.png"], 1024, 1024, 1, "o.png", U)
    assert ep == "acme/img/edit" and body["image_urls"] == ["URL:a.png"]
    n = len(catalog)
    ep, _ = g._fal_video_body({"model": "fal-ai/kling-video/v3/pro", "api_key": ""}, "p", "a.png", "", 5, "720p", "16:9",
                              None, [], [], None, [], U, U)
    assert ep == "fal-ai/kling-video/v3/pro/image-to-video" and len(catalog) == n     # 内置家族不查目录


def test_genmedia_falls_back_when_endpoint_is_not_in_catalog_or_catalog_is_down(catalog, monkeypatch):
    private = {"model": "nobody/private", "api_key": ""}
    ep, body = g._fal_video_body(private, "p", "a.png", "", 5, "720p", "16:9", 1, [], [], None, [], U, U)
    assert ep == "nobody/private/image-to-video"
    assert body == {"prompt": "p", "duration": 5, "resolution": "720p", "aspect_ratio": "16:9", "seed": 1, "image_url": "URL:a.png"}

    def down(params, api_key="", timeout=30):
        raise fm.CatalogUnavailable("目录暂时不可用")
    monkeypatch.setattr(fm, "_get", down)
    ep, body = g._fal_image_body({"model": "acme/other", "api_key": ""}, "p", "", [], 1024, 768, 2, "o.png", U)
    assert ep == "acme/other" and body["image_size"] == {"width": 1024, "height": 768}


def test_get_retries_rate_limits_and_reports_other_http_errors(monkeypatch):
    seq = [urllib.error.HTTPError("u", 429, "Too Many", {"Retry-After": "0"}, None), b'{"models": [], "has_more": false}']
    sent = []

    class Resp:
        def __init__(self, data): self.data = data
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return self.data

    def fake_urlopen(req, timeout=0):
        sent.append(req)
        item = seq.pop(0)
        if isinstance(item, Exception):
            raise item
        return Resp(item)
    monkeypatch.setattr(fm.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(fm.time, "sleep", lambda s: None)
    monkeypatch.setattr(urllib.error.HTTPError, "read", lambda self: b"limited", raising=False)
    assert fm._get({"category": "text-to-video"}, api_key="k") == {"models": [], "has_more": False}
    assert len(sent) == 2 and sent[0].get_header("Authorization") == "Key k"
    seq[:] = [urllib.error.HTTPError("u", 404, "nf", {}, None)]
    assert fm._get({"endpoint_id": "x"}) == {"models": []}
    seq[:] = [urllib.error.HTTPError("u", 400, "bad", {}, None)]
    with pytest.raises(fm.CatalogUnavailable, match="HTTP 400"):
        fm._get({"category": "x"})
    sent.clear()
    seq[:] = [OSError("network down")] * 5                 # 连不上:只快速重试一次,不做长退避
    with pytest.raises(fm.CatalogUnavailable, match="暂时不可用"):
        fm._get({"category": "x"})
    assert len(sent) == 2
