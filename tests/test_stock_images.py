"""参考文件页「🔎 图库」(2026-10-23):modules/stock_images.py 的检索 / 许可过滤 / 24 小时缓存 / 下载缩放 / 中译英,
以及 core 的导入接口(落盘、来源记录、NOTES.md / CREDITS.md、去重)。不联网:请求一律打桩。"""
import asyncio
import io
import json
import urllib.parse

import pytest
from PIL import Image

from modules import stock_images as st


class FakeResp:
    def __init__(self, body: bytes, headers: dict | None = None):
        self._buf = io.BytesIO(body)
        self.headers = headers or {}

    def read(self, n=-1):
        return self._buf.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _img_bytes(size, fmt="JPEG", mode="RGB"):
    out = io.BytesIO()
    Image.new(mode, size, (200, 120, 40, 128) if mode == "RGBA" else (200, 120, 40)).save(out, fmt)
    return out.getvalue()


OV_RESULTS = [
    {"id": "aaaa1111-0000-0000-0000-000000000001", "title": "Bronze Ding", "url": "https://upload.example.org/ding.jpg",
     "thumbnail": "https://api.openverse.org/v1/images/aaaa1111/thumb/", "width": 4000, "height": 3000,
     "creator": "Gary", "creator_url": "", "foreign_landing_url": "https://commons.example.org/ding",
     "source": "wikimedia", "license": "by", "license_version": "2.0",
     "license_url": "https://creativecommons.org/licenses/by/2.0/",
     "attribution": '"Bronze Ding" by Gary is licensed under CC BY 2.0.'},
    {"id": "bbbb2222-0000-0000-0000-000000000002", "title": "Temple", "url": "https://live.example.com/temple.png",
     "thumbnail": "https://api.openverse.org/v1/images/bbbb2222/thumb/", "width": 800, "height": 600,
     "creator": "Ann", "source": "flickr", "license": "cc0", "license_version": "1.0"},
    {"id": "cccc3333-0000-0000-0000-000000000003", "title": "Share-alike", "url": "https://x.example.com/sa.jpg",
     "source": "flickr", "license": "by-sa", "license_version": "2.0"},
]


@pytest.fixture
def net(monkeypatch, tmp_path):
    """按 URL 应答的假网络;calls 记下每次请求的 URL。"""
    monkeypatch.setattr(st, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(st, "FOOTAGE_CONFIG", tmp_path / "footageconfig.json")
    monkeypatch.setattr(st, "CONFIG_PATH", tmp_path / "genconfig.json")
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
    monkeypatch.setattr(st, "_ITEMS", {})
    monkeypatch.setattr(st.time, "sleep", lambda s: None)
    st.set_lang("zh")
    routes, calls = {}, []

    def fake_open(req, timeout=st.TIMEOUT):
        url = req.full_url
        calls.append(url)
        for prefix, resp in routes.items():
            if url.startswith(prefix):
                return resp(req) if callable(resp) else resp
        raise st.StockError(f"unexpected {url}", 0)

    monkeypatch.setattr(st, "_open", fake_open)
    return routes, calls


def _ov_search(routes, results=OV_RESULTS, page_count=12):
    routes["https://api.openverse.org/v1/images/?"] = lambda req: FakeResp(
        json.dumps({"result_count": 240, "page_count": page_count, "results": results}).encode(),
        {"x-ratelimit-limit-anon_burst": "20/min", "x-ratelimit-available-anon_burst": "19",
         "x-ratelimit-limit-anon_sustained": "200/day", "x-ratelimit-available-anon_sustained": "199"})


# ---------------------------------------------------------------- 检索
def test_openverse_search_keeps_cc0_pdm_by_only_and_hides_download_url(net):
    routes, calls = net
    _ov_search(routes)
    r = st.search("openverse", "  bronze   ding ", orientation="horizontal", kind="photo")
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(calls[0]).query)
    assert q["q"] == ["bronze ding"] and q["license"] == ["cc0,pdm,by"] and q["aspect_ratio"] == ["wide"]
    assert q["category"] == ["photograph"] and q["extension"] == ["jpg,png,webp"]
    assert [x["license_label"] for x in r["results"]] == ["CC BY 2.0", "CC0 1.0"]            # BY-SA 不收
    assert r["results"][0]["attribution_required"] and not r["results"][1]["attribution_required"]
    assert r["results"][0]["site"] == "Wikimedia Commons" and "download" not in r["results"][0]
    assert r["rate"]["sustained"]["available"] == "199" and r["has_more"] and not r["cached"]
    assert st._ITEMS[("openverse", OV_RESULTS[0]["id"])]["download"] == OV_RESULTS[0]["url"]


def test_search_is_cached_for_24h(net, monkeypatch):
    routes, calls = net
    _ov_search(routes)
    st.search("openverse", "ding")
    st._ITEMS.clear()
    again = st.search("openverse", "ding")
    assert again["cached"] and len(calls) == 1 and len(again["results"]) == 2
    assert ("openverse", OV_RESULTS[1]["id"]) in st._ITEMS                     # 缓存命中也能导入
    now = st.time.time()
    monkeypatch.setattr(st.time, "time", lambda: now + st.CACHE_TTL + 5)
    assert not st.search("openverse", "ding")["cached"] and len(calls) == 2


def test_openverse_anonymous_page_cap(net):
    routes, _ = net
    _ov_search(routes, page_count=50)
    assert not st.search("openverse", "ding", page=12)["has_more"]
    with pytest.raises(st.StockError) as e:
        st.search("openverse", "ding", page=13)
    assert e.value.status == 400


def test_pexels_pixabay_need_key(net):
    ready = {p["id"]: p["ready"] for p in st.providers()}
    assert ready == {"openverse": True, "pexels": False, "pixabay": False}
    for p in ("pexels", "pixabay"):
        with pytest.raises(st.StockError) as e:
            st.search(p, "temple")
        assert e.value.status == 400 and "API Key" in str(e.value)


def test_pexels_and_pixabay_normalize(net, monkeypatch):
    routes, calls = net
    monkeypatch.setenv("PEXELS_API_KEY", "pk")
    st.FOOTAGE_CONFIG.write_text(json.dumps({"pixabay_api_key": "xk"}), encoding="utf-8")   # 与素材库共用 Key
    assert all(p["ready"] for p in st.providers())
    seen = {}

    def pexels(req):
        seen["pexels"] = req.headers.get("Authorization")
        return FakeResp(json.dumps({"total_results": 1, "next_page": "x", "photos": [
            {"id": 42, "width": 6000, "height": 4000, "url": "https://www.pexels.com/photo/42/", "photographer": "Lee",
             "photographer_url": "https://www.pexels.com/@lee", "alt": "temple",
             "src": {"original": "https://images.pexels.com/42.jpeg", "large2x": "https://images.pexels.com/42-l.jpeg",
                     "medium": "https://images.pexels.com/42-m.jpeg"}}]}).encode())

    routes["https://api.pexels.com/v1/search?"] = pexels
    routes["https://pixabay.com/api/?"] = FakeResp(json.dumps({"total": 900, "totalHits": 500, "hits": [
        {"id": 7, "pageURL": "https://pixabay.com/photos/7/", "tags": "temple, asia", "imageWidth": 5000,
         "imageHeight": 2500, "webformatURL": "https://pixabay.com/get/7_640.jpg",
         "largeImageURL": "https://pixabay.com/get/7_1280.jpg", "user": "kim", "user_id": 9}]}).encode())
    px = st.search("pexels", "寺庙", orientation="square")["results"][0]
    assert seen["pexels"] == "pk" and "locale=zh-CN" in calls[-1] and "orientation=square" in calls[-1]
    assert px["license_label"] == "Pexels License" and px["attribution"] == "Photo by Lee on Pexels"
    assert st._ITEMS[("pexels", "42")]["download"] == "https://images.pexels.com/42.jpeg"
    pb = st.search("pixabay", "寺庙", orientation="square", kind="illustration")
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(calls[-1]).query)
    assert q["lang"] == ["zh"] and q["image_type"] == ["illustration"] and "orientation" not in q   # 无方图档
    assert q["safesearch"] == ["true"] and pb["has_more"]
    it = pb["results"][0]
    assert (it["width"], it["height"]) == (1280, 640) and it["author_url"] == "https://pixabay.com/users/kim-9/"


# ---------------------------------------------------------------- 下载
def test_fetch_downscales_big_and_keeps_small(net):
    routes, _ = net
    routes["https://img.example.com/big.jpg"] = lambda req: FakeResp(_img_bytes((4000, 3000)))
    small = _img_bytes((800, 600), "PNG")
    routes["https://img.example.com/small.png"] = lambda req: FakeResp(small)
    routes["https://img.example.com/alpha.png"] = lambda req: FakeResp(_img_bytes((3000, 1000), "PNG", "RGBA"))
    data, ext = st.fetch({"download": "https://img.example.com/big.jpg"})
    assert ext == ".jpg" and Image.open(io.BytesIO(data)).size == (2560, 1920)
    assert st.fetch({"download": "https://img.example.com/small.png"}) == (small, ".png")     # 不超限原字节
    data, ext = st.fetch({"download": "https://img.example.com/alpha.png"})
    assert ext == ".png" and Image.open(io.BytesIO(data)).size == (2560, 853)                 # 透明图仍存 PNG


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://127.0.0.1:8640/x.jpg", "http://192.168.1.2/x.jpg",
                                 "http://localhost/x.jpg"])
def test_fetch_rejects_local_urls(net, url):
    with pytest.raises(st.StockError) as e:
        st.fetch({"download": url})
    assert e.value.status == 400


def test_fetch_rejects_huge_and_non_images(net):
    routes, _ = net
    routes["https://img.example.com/x.html"] = lambda req: FakeResp(b"<html>nope</html>")
    with pytest.raises(st.StockError):
        st.fetch({"download": "https://img.example.com/x.html"})
    with pytest.raises(st.StockError) as e:
        st.fetch({"download": "https://img.example.com/x.jpg", "width": 20000, "height": 10000})
    assert e.value.status == 400


# ---------------------------------------------------------------- 中译英
def test_clean_keywords():
    assert st.clean_keywords("<think>商代…</think>\nbronze ding") == "bronze ding"
    assert st.clean_keywords("reasoning here</think>\n\nWhite Horse") == "white horse"
    assert st.clean_keywords("Keywords: ancient palace night.") == "ancient palace night"
    assert st.clean_keywords("Here are the keywords:\n\"temple\", \"mist\"") == "temple mist"
    assert st.clean_keywords("一 二 三") == ""


def test_translate_query_bypasses_proxy_for_local_model(net, monkeypatch):
    sent = {}

    class Opener:
        def open(self, req, timeout=0):
            sent.update(url=req.full_url, body=json.loads(req.data), auth=req.headers.get("Authorization"))
            return FakeResp(json.dumps({"choices": [{"message": {"content": "<think>x</think>Bronze Ding"}}]}).encode())

    monkeypatch.setattr(st.urllib.request, "build_opener", lambda *h: Opener())
    monkeypatch.setattr(st.urllib.request, "urlopen", lambda *a, **k: pytest.fail("本机模型不应走系统代理"))
    out = st.translate_query("商代青铜鼎", {"base_url": "http://127.0.0.1:1234/v1/", "api_key": "lm", "model": "qwen"})
    assert out == "bronze ding" and sent["url"] == "http://127.0.0.1:1234/v1/chat/completions"
    assert sent["body"]["model"] == "qwen" and sent["body"]["messages"][1]["content"] == "商代青铜鼎"
    assert sent["auth"] == "Bearer lm"
    with pytest.raises(st.StockError) as e:
        st.translate_query("鼎", {"base_url": "http://127.0.0.1:1234/v1", "model": ""})
    assert e.value.status == 400


# ---------------------------------------------------------------- 导入参考文件(core)
@pytest.fixture
def runtime(monkeypatch, tmp_path, net):
    from services.runtime import core

    monkeypatch.setattr(core, "PROJECTS_DIR", tmp_path / "projects")
    core.PROJECTS_DIR.mkdir()
    core.ensure_project("p")
    monkeypatch.setattr(core, "ui_lang_code", lambda cfg=None: "zh")
    monkeypatch.setattr(core, "_stock_openverse_creds", lambda: ("", ""))
    routes, calls = net
    _ov_search(routes)
    routes["https://upload.example.org/ding.jpg"] = lambda req: FakeResp(_img_bytes((4000, 3000)))
    routes["https://live.example.com/temple.png"] = lambda req: FakeResp(_img_bytes((800, 600), "PNG"))
    return core, core.PROJECTS_DIR / "p" / "refs", calls


def _imp(core, **kw):
    return asyncio.run(core.api_refs_import({"project": "p", "provider": "openverse", **kw}))


def test_import_writes_file_source_notes_and_credits(runtime):
    core, refs, calls = runtime
    asyncio.run(core.api_stock_search({"provider": "openverse", "q": "ding"}))
    by, cc0 = OV_RESULTS[0]["id"], OV_RESULTS[1]["id"]
    r = _imp(core, id=by, category="props", note="鼎的形制参考", query="ding")
    assert r["path"] == "props/openverse_aaaa1111.jpg" and not r["existed"]
    assert Image.open(refs / r["path"]).size == (2560, 1920)
    ann = json.loads((refs / "annotations.json").read_text(encoding="utf-8"))[r["path"]]
    assert ann["note"] == "鼎的形制参考" and ann["source"]["license"] == "by" and ann["source"]["query"] == "ding"
    assert "- `props/openverse_aaaa1111.jpg`:鼎的形制参考(图库:Wikimedia Commons · CC BY 2.0)" \
        in (refs / "NOTES.md").read_text(encoding="utf-8")
    credits = (refs / "CREDITS.md").read_text(encoding="utf-8")
    assert "CC BY 2.0(需署名)" in credits and "署名:\"Bronze Ding\" by Gary" in credits

    again = _imp(core, id=by, category="props", subdir="ding")     # 旧页面带来的 subdir 不再生效
    assert again["existed"] and again["path"] == r["path"]
    n_downloads = sum(u.endswith("ding.jpg") for u in calls)
    style = _imp(core, id=by, category="style")                      # 别的板块照常加一份
    assert style["path"] == "style/openverse_aaaa1111.jpg" and sum(u.endswith("ding.jpg") for u in calls) == n_downloads + 1
    plain = _imp(core, id=cc0, category="scenes")                     # 无注释:不进 NOTES.md,进 CREDITS.md
    assert plain["path"] == "scenes/openverse_bbbb2222.png"
    assert "bbbb2222" not in (refs / "NOTES.md").read_text(encoding="utf-8")
    assert "CC0 1.0(免署名)" in (refs / "CREDITS.md").read_text(encoding="utf-8")

    files = {f["path"]: f for c in asyncio.run(core.api_refs_list("p"))["categories"] for f in c["files"]}
    assert "CREDITS.md" not in files and files[plain["path"]]["source"]["site"] == "Flickr"
    assert files["props/openverse_aaaa1111.jpg"]["note"] == "鼎的形制参考"


def test_import_dedupes_against_legacy_subdir(runtime):
    core, refs, calls = runtime
    asyncio.run(core.api_stock_search({"provider": "openverse", "q": "ding"}))
    by = OV_RESULTS[0]["id"]
    (refs / "props" / "ding").mkdir()                                  # 存量项目:改版前导入到子目录里的同一张图
    (refs / "props" / "ding" / "openverse_aaaa1111.jpg").write_bytes(_img_bytes((64, 48)))
    (refs / "annotations.json").write_text(json.dumps({"props/ding/openverse_aaaa1111.jpg": {
        "note": "", "source": {"provider": "openverse", "id": by}}}), encoding="utf-8")
    calls.clear()
    r = _imp(core, id=by, category="props")
    assert r["existed"] and r["path"] == "props/ding/openverse_aaaa1111.jpg" and not calls
    assert not _imp(core, id=by, category="style")["existed"]          # 别的板块照常下载


def test_note_edit_keeps_source_and_delete_updates_credits(runtime):
    core, refs, _ = runtime
    asyncio.run(core.api_stock_search({"provider": "openverse", "q": "ding"}))
    rel = _imp(core, id=OV_RESULTS[0]["id"], category="props", note="旧注释")["path"]
    asyncio.run(core.api_refs_note({"project": "p", "path": rel, "note": ""}))
    ann = json.loads((refs / "annotations.json").read_text(encoding="utf-8"))
    assert ann[rel]["note"] == "" and ann[rel]["source"]["id"] == OV_RESULTS[0]["id"]
    asyncio.run(core.api_refs_note({"project": "p", "path": rel, "note": "新注释"}))
    assert json.loads((refs / "annotations.json").read_text(encoding="utf-8"))[rel]["source"]
    asyncio.run(core.api_refs_delete({"project": "p", "path": rel}))
    assert not (refs / "CREDITS.md").exists()                         # 最后一张图库图删掉,CREDITS.md 一并删


def test_import_falls_back_to_detail_lookup_and_validates(runtime):
    core, refs, calls = runtime
    detail = OV_RESULTS[1]
    from modules import openverse
    calls.clear()
    st._ITEMS.clear()                                                  # 服务重启过:按 id 回源取一次
    st_open = st._open

    def open_with_detail(req, timeout=st.TIMEOUT):
        if req.full_url == f"{openverse.API_BASE}/images/{detail['id']}/":
            calls.append(req.full_url)
            return FakeResp(json.dumps(detail).encode())
        return st_open(req, timeout)

    st._open = open_with_detail
    try:
        r = _imp(core, id=detail["id"], category="characters")
    finally:
        st._open = st_open
    assert r["path"] == "characters/openverse_bbbb2222.png" and calls[0].endswith(f"/images/{detail['id']}/")
    with pytest.raises(core.ServiceError) as e:
        _imp(core, id=detail["id"], category="music")                  # 只收图片板块
    assert e.value.status_code == 400
    with pytest.raises(core.ServiceError) as e:
        _imp(core, id="../../etc", category="style")
    assert e.value.status_code == 400
    sa = OV_RESULTS[2]["id"]
    st._open = lambda req, timeout=0: FakeResp(json.dumps(OV_RESULTS[2]).encode())
    try:
        with pytest.raises(core.ServiceError) as e:
            _imp(core, id=sa, category="style")                        # BY-SA 回源也拒
    finally:
        st._open = st_open
    assert e.value.status_code == 404


def test_search_and_translate_api_errors(runtime, monkeypatch):
    core, _, _ = runtime
    with pytest.raises(core.ServiceError) as e:
        asyncio.run(core.api_stock_search({"provider": "pexels", "q": "x"}))
    assert e.value.status_code == 400
    monkeypatch.setattr(core, "resolve_deepagents", lambda cfg=None: {"provider": "local", "base_url": "http://127.0.0.1:9/v1",
                                                                     "api_key": "", "model": "m"})

    def down(text, conn):
        raise st.StockError("连不上文本模型", 502)

    monkeypatch.setattr(st, "translate_query", down)
    with pytest.raises(core.ServiceError) as e:
        asyncio.run(core.api_stock_translate({"text": "鼎"}))
    assert e.value.status_code == 502 and "本地模型" in e.value.detail
    monkeypatch.setattr(st, "translate_query", lambda text, conn: "bronze ding")
    assert asyncio.run(core.api_stock_translate({"text": "鼎"})) == {"text": "bronze ding", "provider": "local", "model": "m"}


def test_wikimedia_items_use_wikimedia_thumbnails():
    url = "https://upload.wikimedia.org/wikipedia/commons/f/fa/Bronze_Ding%2C_Mid_Western_Zhou.jpg"
    it = st._from_openverse({"id": "x1", "url": url, "thumbnail": "https://api.openverse.org/v1/images/x1/thumb/",
                             "width": 5184, "height": 3456, "license": "cc0", "source": "wikimedia"})
    assert it["thumb"] == ("https://upload.wikimedia.org/wikipedia/commons/thumb/f/fa/Bronze_Ding%2C_Mid_Western_Zhou.jpg"
                           "/500px-Bronze_Ding%2C_Mid_Western_Zhou.jpg")
    assert it["preview"].endswith("/1280px-Bronze_Ding%2C_Mid_Western_Zhou.jpg")
    assert it["thumb_alt"] == url and it["download"] == url                    # 下载仍取原图
    small = st._from_openverse({"id": "x2", "url": url, "thumbnail": "https://api.openverse.org/t/", "width": 400,
                                "license": "cc0"})
    assert small["thumb"] == "https://api.openverse.org/t/" and small["preview"] == url   # 原图不比缩略档宽就不改


def test_key_from_settings_page_wins_and_is_shared_with_footage(net, monkeypatch, tmp_path):
    routes, calls = net
    monkeypatch.setenv("PEXELS_API_KEY", "env-key")
    st.CONFIG_PATH.write_text(json.dumps({"stock": {"pexels": {"api_key": " page-key "}, "pixabay": {"api_key": ""}}}),
                              encoding="utf-8")
    st.FOOTAGE_CONFIG.write_text(json.dumps({"pixabay_api_key": "footage-key"}), encoding="utf-8")
    assert st.api_key("pexels") == "page-key"                         # 「存储与图库」页 > 环境变量
    assert st.api_key("pixabay") == "footage-key"                     # 页面留空 → 环境变量 → footageconfig
    from modules import footage
    monkeypatch.setattr(footage, "_GENCONFIG_PATH", st.CONFIG_PATH)
    monkeypatch.setattr(footage, "_CONFIG_PATH", st.FOOTAGE_CONFIG)
    assert footage._api_key("pexels") == "page-key" and footage._api_key("pixabay") == "footage-key"


def test_test_key_uses_form_key_and_skips_cache(net):
    routes, calls = net
    seen = []

    def pexels(req):
        seen.append(req.headers.get("Authorization"))
        return FakeResp(json.dumps({"total_results": 8000, "photos": []}).encode())

    routes["https://api.pexels.com/v1/search?"] = pexels
    with pytest.raises(st.StockError) as e:
        st.test_key("pexels", "")                                        # 表单空、也没保存过
    assert e.value.status == 400 and "存储与图库" in str(e.value)
    assert st.test_key("pexels", " typed ") == {"ok": True, "provider": "pexels", "total": 8000}
    assert st.test_key("pexels", "typed")["total"] == 8000 and seen == ["typed", "typed"]   # 不走缓存
    assert not list(st.CACHE_DIR.glob("*.json")) if st.CACHE_DIR.exists() else True
    with pytest.raises(st.StockError):
        st.test_key("openverse", "x")

    def denied(req):
        raise st.StockError("HTTP 401: nope", 401)

    routes["https://api.pexels.com/v1/search?"] = denied
    with pytest.raises(st.StockError) as e:
        st.test_key("pexels", "bad")
    assert e.value.status == 401 and "拒绝了 API Key" in str(e.value)
