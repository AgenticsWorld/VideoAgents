"""生成模型页「🧊 3D 模型」板块(2026-11-25):配置段 model3d 的默认值 / 保存校验、连接验证(Tripo 余额、
局域网 Hunyuan3D-2 api_server)、Fal / RH 模型目录的 3D 类别。全部离线:HTTP 走本机临时服务或打桩。"""
import asyncio
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from modules import fal_models, model3d, rh_models
from services.runtime import core

ROOT = Path(__file__).resolve().parent.parent
MODELS_HTML = ROOT / "apps" / "web" / "static" / "models.html"


@pytest.fixture
def cfg_file(tmp_path, monkeypatch):
    path = tmp_path / "genconfig.json"
    monkeypatch.setattr(core, "GENCONFIG_PATH", path)
    monkeypatch.delenv("VIDEOAGENTS_USER_JWT", raising=False)
    monkeypatch.setattr(core, "save_state", lambda *a, **k: None)
    monkeypatch.setitem(core.STATE, "ui_lang", "")

    async def no_refresh(cfg):
        return []
    monkeypatch.setattr(core, "_refresh_rh_wf_caches", no_refresh)
    return path


def _save(body):
    return asyncio.run(core.api_genconfig_set(body))


# ---------------------------------------------------------------- 配置段

def test_defaults_and_page_lists_agree():
    d = core.DEFAULT_GENCONFIG["model3d"]
    assert d["provider"] == "tripo"
    assert set(d) - {"provider"} == {"tripo", "fal", "rhapi", "hunyuan3d"}
    assert d["tripo"]["model"] == model3d.TRIPO_MODELS[0]
    assert d["hunyuan3d"]["model"] == model3d.HUNYUAN3D_MODELS[0]
    assert d["hunyuan3d"]["base_url"] == "http://127.0.0.1:8080"
    html = MODELS_HTML.read_text(encoding="utf-8")
    assert f"const H3D_DEFAULT_URL='{d['hunyuan3d']['base_url']}'" in html   # 旧版实例缺 model3d 段时页面按此填

    def first_id(const):
        m = re.search(rf"const {const}=\[[^\n]*\n\s*\['([^']+)'", html)
        assert m, const
        return m.group(1)
    # 页面下拉首项 = 新配置默认值(fillSelect 没存过值时选首项)
    assert first_id("TRIPO_MODELS") == d["tripo"]["model"]
    assert first_id("FAL_3D_MODELS") == d["fal"]["model"]
    assert first_id("H3D_LOCAL_MODELS") == d["hunyuan3d"]["model"]
    page_tripo = re.findall(r"\['((?:v\d|P\d)[^']*)'", html.split("const TRIPO_MODELS=")[1].split("];")[0])
    assert tuple(page_tripo) == model3d.TRIPO_MODELS
    page_h3d = re.findall(r"\['(hunyuan3d-dit-[^']+)'", html.split("const H3D_LOCAL_MODELS=")[1].split("];")[0])
    assert tuple(page_h3d) == model3d.HUNYUAN3D_MODELS
    # Fal 内置清单:设置页下拉与生成窗口(model3d.FAL_MODELS)同一批、同序
    page_fal = re.findall(r"^\s*\['([^']+)',", html.split("const FAL_3D_MODELS=")[1].split("];")[0], re.M)
    assert page_fal == [m for m, _, _ in model3d.FAL_MODELS]


def test_old_config_without_model3d_gets_defaults(cfg_file):
    cfg_file.write_text(json.dumps({"world": {"provider": "hunyuan"}}))
    cfg = core.load_genconfig()
    assert cfg["model3d"] == core.DEFAULT_GENCONFIG["model3d"]
    assert cfg["world"]["provider"] == "hunyuan"


def test_save_validates_provider_site_and_texture(cfg_file):
    out = _save({"model3d": {"provider": "hunyuan3d",
                             "hunyuan3d": {"base_url": "http://192.168.1.2:8080", "texture": "yes"},
                             "tripo": {"site": "cn", "api_key_cn": "k"}}})
    m = out["config"]["model3d"]
    assert m["provider"] == "hunyuan3d" and m["hunyuan3d"]["texture"] is False   # 只认布尔 true
    assert m["tripo"]["site"] == "cn" and m["tripo"]["model"] == model3d.TRIPO_MODELS[0]   # 未带的键保留默认
    saved = json.loads(cfg_file.read_text())["model3d"]
    assert saved["hunyuan3d"]["base_url"] == "http://192.168.1.2:8080"
    assert _save({"model3d": {"hunyuan3d": {"texture": True}}})["config"]["model3d"]["hunyuan3d"]["texture"] is True
    with pytest.raises(core.ServiceError) as ei:
        _save({"model3d": {"provider": "meshy"}})
    assert ei.value.status_code == 400 and "model3d.provider" in str(ei.value.detail)
    for prov in ("tripo", "rhapi"):
        with pytest.raises(core.ServiceError) as ei:
            _save({"model3d": {prov: {"site": "us"}}})
        assert f"model3d.{prov}.site" in str(ei.value.detail)


# ---------------------------------------------------------------- 连接验证(本机临时 HTTP 服务)

class _Handler(BaseHTTPRequestHandler):
    routes: dict = {}

    def do_GET(self):  # noqa: N802
        status, body, auth = self.routes.get(self.path, (404, b"{}", None))
        if auth and self.headers.get("Authorization") != auth:
            status, body = 401, json.dumps({"code": 2, "status": "error", "message": "Authentication required"}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def local_http():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_port}", _Handler.routes
    srv.shutdown()
    _Handler.routes = {}


def test_tripo_balance_ok_and_bad_key(local_http, monkeypatch):
    base, routes = local_http
    routes["/v3/account/balance"] = (200, json.dumps({"code": 0, "data": {"balance": 123.5, "frozen": 2}}).encode(),
                                     "Bearer good")
    monkeypatch.setitem(model3d.TRIPO_SITES, "cn", {**model3d.TRIPO_SITES["cn"], "base": base + "/v3"})
    assert model3d.tripo_balance("cn", "good") == {"balance": 123.5, "frozen": 2}
    with pytest.raises(model3d.Model3DError, match="Key 无效"):
        model3d.tripo_balance("cn", "bad")
    with pytest.raises(model3d.Model3DError, match="Invalid or unauthorized"):
        model3d.tripo_balance("cn", "bad", zh=False)
    with pytest.raises(model3d.Model3DError, match="未填写"):
        model3d.tripo_balance("cn", "  ")
    assert model3d.tripo_site("tripo3d.com") == "cn" and model3d.tripo_site("") == "ai"


def test_hunyuan3d_check(local_http):
    base, routes = local_http
    spec = {"openapi": "3.1.0", "paths": {p: {} for p in model3d.HUNYUAN3D_PATHS}}
    routes["/openapi.json"] = (200, json.dumps(spec).encode(), None)
    assert model3d.hunyuan3d_check(base + "/")["base_url"] == base
    assert model3d.hunyuan3d_check(base.replace("http://", ""))["base_url"] == base   # 省略 http:// 也行
    routes["/openapi.json"] = (200, json.dumps({"paths": {"/generate": {}}}).encode(), None)
    with pytest.raises(model3d.Model3DError, match="缺接口"):
        model3d.hunyuan3d_check(base)
    routes["/openapi.json"] = (200, b"<html>", None)
    with pytest.raises(model3d.Model3DError, match="不是 JSON"):
        model3d.hunyuan3d_check(base)
    with pytest.raises(model3d.Model3DError, match="Server address is empty"):
        model3d.hunyuan3d_check("", zh=False)


def test_test_endpoints_return_ok_flag(monkeypatch):
    monkeypatch.setattr(core, "ui_lang_code", lambda: "zh")
    monkeypatch.setattr(model3d, "tripo_balance", lambda site, key, timeout, zh: {"balance": 9, "frozen": 0})
    assert asyncio.run(core.api_test_tripo({"site": "cn", "api_key": "k"})) == {
        "ok": True, "provider": "tripo", "site": "cn", "balance": 9, "frozen": 0}

    def boom(*a, **k):
        raise model3d.Model3DError("连不上")
    monkeypatch.setattr(model3d, "hunyuan3d_check", boom)
    r = asyncio.run(core.api_test_hunyuan3d({"base_url": "http://10.0.0.2:8080"}))
    assert r == {"ok": False, "provider": "hunyuan3d", "error": "连不上"}


# ---------------------------------------------------------------- 模型目录

def test_rh_3d_task_classification():
    cases = {
        "hunyuan3d-v3.1/text-to-3d": "text-to-3d",
        "hunyuan3d-v3.1/image-to-3d": "image-to-3d",
        "tripo-h/image-to-3d-v3.1": "image-to-3d",
        "tripo-p/multiview-to-3d-P2": "multiview-to-3d",
        "tripo-h/text-to-3d-v2.5": "text-to-3d",
        "hitem3d-v3/multi-image-to-3d": "multiview-to-3d",
        "miora-3d/text-to-3d": "text-to-3d",
        # 不是出模型的端点
        "tripo/convert": "", "tripo/image-to-splat": "", "tripo/retopology-v2.0": "", "tripo/texture-v3.0": "",
    }
    for path, want in cases.items():
        assert rh_models._task_of("其他", path) == want, path
    # 文档分类兜底(端点末段看不出任务时)
    assert rh_models._task_of("3D 生成与处理 > image-to-3D", "vendor/foo") == "image-to-3d"
    assert rh_models.kind_of("multiview-to-3d") == "model3d"
    assert rh_models.kind_of("text-to-image") == "image" and rh_models.kind_of("image-to-video") == "video"


def _write_cache(cache_dir: Path, site: str, models: list, version=rh_models.CATALOG_VERSION):
    cache_dir.mkdir(parents=True, exist_ok=True)
    doc = {"fetched_at": 9e18, "models": models}
    if version is not None:
        doc["version"] = version
    (cache_dir / f"{site}.json").write_text(json.dumps(doc), encoding="utf-8")


def _row(mid, category, task):
    return {"id": mid, "name": mid, "category": category, "task": task, "kind": rh_models.kind_of(task),
            "schema": {"properties": {"prompt": {"type": "string"}}, "required": []}}


def test_rh_lists_split_3d_from_image_and_video(tmp_path, monkeypatch):
    monkeypatch.setattr(rh_models, "CACHE_DIR", tmp_path)
    rh_models._CACHED_LIST_MEMO.clear()
    _write_cache(tmp_path, "cn", [_row("hunyuan3d-v3.1/text-to-3d", "3D > text-to-3D", "text-to-3d"),
                                  _row("tripo-h/multiview-to-3d-v3.1", "其他", "multiview-to-3d"),
                                  _row("seedream/text-to-image", "图像 > text-to-image", "text-to-image"),
                                  _row("kling/image-to-video", "视频 > image-to-video", "image-to-video")])
    ids = lambda rows: sorted(r["id"] for r in rows)   # noqa: E731
    assert ids(rh_models.list_models("cn", "model3d")) == ["hunyuan3d-v3.1/text-to-3d", "tripo-h/multiview-to-3d-v3.1"]
    assert ids(rh_models.list_models("cn", "image")) == ["seedream/text-to-image"]
    assert ids(rh_models.list_models("cn", "video")) == ["kling/image-to-video"]
    assert ids(rh_models.cached_list("cn", "model3d")) == ["hunyuan3d-v3.1/text-to-3d", "tripo-h/multiview-to-3d-v3.1"]
    assert ids(rh_models.cached_list("cn", "video")) == ["kling/image-to-video"]


def test_rh_old_cache_refetched_only_for_3d_list_and_kept_on_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(rh_models, "CACHE_DIR", tmp_path)
    _write_cache(tmp_path, "ai", [_row("kling/image-to-video", "视频 > image-to-video", "image-to-video")], version=None)
    calls = []

    def fail(site):
        calls.append(site)
        raise RuntimeError("offline")
    monkeypatch.setattr(rh_models, "_fetch_catalog", fail)
    # 图像 / 视频(含生成时的 find_entry)照旧按有效期用旧格式缓存,不多拉一次
    assert [m["id"] for m in rh_models.list_models("ai", "video")] == ["kling/image-to-video"]
    assert rh_models.find_entry("ai", "kling/image-to-video")["id"] == "kling/image-to-video"
    assert calls == []
    # 列 3D:旧格式缓存(没收 3D 端点)强制重拉;拉取失败照用旧缓存
    assert rh_models.list_models("ai", "model3d") == []
    assert calls == ["ai"]
    monkeypatch.setattr(rh_models, "_fetch_catalog",
                        lambda site: [_row("hunyuan3d-v3.1/image-to-3d", "3D > image-to-3D", "image-to-3d")])
    assert [m["id"] for m in rh_models.list_models("ai", "model3d")] == ["hunyuan3d-v3.1/image-to-3d"]
    assert json.loads((tmp_path / "ai.json").read_text())["version"] == rh_models.CATALOG_VERSION
    monkeypatch.setattr(rh_models, "_fetch_catalog", fail)
    calls.clear()
    assert [m["id"] for m in rh_models.list_models("ai", "model3d")] == ["hunyuan3d-v3.1/image-to-3d"]
    assert calls == []                                                             # 新格式缓存在有效期内不重拉


def test_fal_3d_endpoints_fold_into_model_prefixes(monkeypatch):
    assert fal_models.split_endpoint("tripo3d/h3.1/multiview-to-3d", "model3d") == ("tripo3d/h3.1", "multiview-to-3d")
    assert fal_models.split_endpoint("fal-ai/trellis-2", "model3d") == ("fal-ai/trellis-2", "")
    assert fal_models._family_name("Hyper3D - Rodin V2.5 - Text to 3D - Fast") == "Hyper3D - Rodin V2.5 - Fast"
    assert fal_models._family_name("Meshy 7.1 Multi Image to 3D") == "Meshy 7.1"
    pages = {
        "image-to-3d": [{"endpoint_id": "tripo3d/h3.1/image-to-3d", "metadata": {"display_name": "Tripo H3.1 Image to 3D"}},
                        {"endpoint_id": "tripo3d/h3.1/multiview-to-3d", "metadata": {"display_name": "Tripo H3.1 Multiview to 3D"}},
                        {"endpoint_id": "fal-ai/trellis-2", "metadata": {"display_name": "Trellis 2", "category": "image-to-3d"}}],
        "text-to-3d": [{"endpoint_id": "tripo3d/h3.1/text-to-3d", "metadata": {"display_name": "Tripo H3.1 Text to 3D"}}],
    }
    seen = []

    def fake_get(params, api_key="", timeout=30):
        seen.append(params["category"])
        return {"models": pages.get(params["category"], [])}
    monkeypatch.setattr(fal_models, "_get", fake_get)
    fal_models._SEARCH_CACHE.clear()
    rows = fal_models.search("model3d", "", refresh=True)
    assert seen == ["image-to-3d", "text-to-3d"]
    assert [(r["id"], r["name"], r["tasks"]) for r in rows] == [
        ("tripo3d/h3.1", "Tripo H3.1", ["image-to-3d", "multiview-to-3d", "text-to-3d"]),
        ("fal-ai/trellis-2", "Trellis 2", ["image-to-3d"])]


def test_model_list_endpoints_accept_model3d(cfg_file, monkeypatch):
    monkeypatch.setattr(fal_models, "search", lambda kind, q, limit, refresh, key: [
        {"id": "tripo3d/h3.1", "name": "Tripo H3.1", "tasks": ["text-to-3d"], "description": ""}])
    out = asyncio.run(core.api_fal_models("model3d"))
    assert out["models"] == [{"id": "tripo3d/h3.1", "name": "Tripo H3.1", "tasks": ["text-to-3d"], "description": ""}]
    assert "builtin" in asyncio.run(core.api_fal_models("video"))["models"][0]       # 图像 / 视频照旧带 builtin
    monkeypatch.setattr(rh_models, "list_models", lambda site, kind, refresh: [{"id": "x/text-to-3d", "kind": kind}])
    out = asyncio.run(core.api_rhapi_models("cn", "model3d"))
    assert out["kind"] == "model3d" and out["models"] == [{"id": "x/text-to-3d", "kind": "model3d"}]
    with pytest.raises(core.ServiceError):
        asyncio.run(core.api_rhapi_models("cn", "audio"))
    with pytest.raises(core.ServiceError):
        asyncio.run(core.api_fal_models("audio"))
