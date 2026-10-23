"""参考文件页「🔎 图库」(2026-10-23):按关键词搜免费图库,用户点选后由服务端下载进 refs/<板块>/。

图源(接口要点 2026-10-23 实测 / 核对条款):
- openverse:免 Key。GET https://api.openverse.org/v1/images/,聚合 Flickr / Wikimedia Commons / Europeana /
  大都会 / 史密森尼 / 克利夫兰等。匿名每分钟 20 次、每天 200 次(响应头 x-ratelimit-*),缩略图 /thumb/ 另计
  anon_thumbnail 额度;匿名每页最多 20 条、最多翻到第 12 页(240 条),再往后 401。中文词只命中中文标题的条目,
  所以页面有「译成英文」按钮(translate_query,走 Agentics 引擎当前的文本模型)。必须带 Accept: application/json。
  「生成模型」页音效 › Openverse 填了 Client ID / Secret 时沿用其令牌(额度更高),没填即匿名。
- pexels / pixabay:各要一个免费 API Key。本期不做 Key 配置界面,只读环境变量 PEXELS_API_KEY / PIXABAY_API_KEY
  或素材库的 <数据目录>/.videoagents/footageconfig.json 的 pexels_api_key / pixabay_api_key(与 modules/footage.py 同口径);
  没有 Key 时该图源列为不可用。Pexels 200 次/时、2 万次/月,locale=zh-CN 支持中文;Pixabay 100 次/60 秒,lang=zh 原生中文,
  条款要求检索结果缓存 24 小时、不得长期热链(用前先下载到本地),API 给的最大图 largeImageURL 长边 1280。

许可:Openverse 只收 CC0 / 公有领域 / CC BY(BY-SA 会传染整片、NC / ND 不能商用或改编,一律不收,同音效口径);
Pexels / Pixabay 为各自的免署名可商用许可。两家条款都禁止自动化批量采集(尤其用于机器学习)——这里只在用户点选时下载单张。
下载:长边超过 2560 等比缩到 2560 重新编码,不超过的原样保存(Pixabay 最大 1280,不会缩)。
"""
from __future__ import annotations

import hashlib
import io
import ipaddress
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from modules import openverse

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("VIDEOAGENTS_DATA_DIR", ROOT / "data")).expanduser().resolve()
RUNTIME_DIR = Path(os.environ.get("VIDEOAGENTS_RUNTIME_DIR", DATA_DIR / ".videoagents")).expanduser().resolve()
CACHE_DIR = RUNTIME_DIR / "stock_images"
FOOTAGE_CONFIG = DATA_DIR / ".videoagents" / "footageconfig.json"
USER_AGENT = "VideoAgents/1.0 (+https://github.com/AgenticsWorld/VideoAgents; reference image search)"

PROVIDERS = ("openverse", "pexels", "pixabay")
PROVIDER_LABELS = {"openverse": "Openverse", "pexels": "Pexels", "pixabay": "Pixabay"}
PROVIDER_HOME = {"openverse": "https://openverse.org", "pexels": "https://www.pexels.com",
                 "pixabay": "https://pixabay.com"}
KEY_ENV = {"pexels": "PEXELS_API_KEY", "pixabay": "PIXABAY_API_KEY"}
OPENVERSE_LICENSES = "cc0,pdm,by"
OPENVERSE_MAX_PAGE = 12                 # 匿名翻到第 13 页 401(只给前 240 条)
PAGE_SIZE = {"openverse": 20, "pexels": 30, "pixabay": 30}
ORIENTATIONS = ("", "horizontal", "vertical", "square")
KINDS = ("", "photo", "illustration")
CACHE_TTL = 24 * 3600                   # Pixabay 条款:检索结果缓存 24 小时
MAX_SIDE = 2560
MAX_DOWNLOAD_BYTES = 60 * 1024 * 1024
MAX_PIXELS = 160_000_000                # Wikimedia 偶有上亿像素的扫描件,解码前按 Openverse 给的宽高拦
TIMEOUT = 25
_ITEMS_MAX = 5000
_ITEMS: dict[tuple[str, str], dict] = {}     # (provider, id) → 含下载地址的完整条目;导入时按它取原图,不信前端给的地址

# Openverse source 代号 → 显示名(只列常见的,其余把下划线换空格)
SITE_NAMES = {"flickr": "Flickr", "wikimedia": "Wikimedia Commons", "europeana": "Europeana", "met": "The Met",
              "clevelandmuseum": "Cleveland Museum of Art", "rijksmuseum": "Rijksmuseum", "rawpixel": "rawpixel",
              "stocksnap": "StockSnap", "nasa": "NASA", "brooklynmuseum": "Brooklyn Museum",
              "wordpress": "WordPress Photo Directory", "geographorguk": "Geograph", "smk": "SMK",
              "sciencemuseum": "Science Museum", "nypl": "NYPL", "inaturalist": "iNaturalist",
              "digitaltmuseum": "DigitaltMuseum", "museumsvictoria": "Museums Victoria",
              "wellcome_collection": "Wellcome Collection", "bio_diversity": "Biodiversity Heritage Library"}

LANG = "zh"          # 服务端文案语言(core 每次调用前按界面语言设:zh 出中文,其余出英文)


def set_lang(code: str) -> None:
    global LANG
    LANG = "zh" if (code or "zh") == "zh" else "en"


def _t(zh: str, en: str) -> str:
    return zh if LANG == "zh" else en


class StockError(RuntimeError):
    """图库请求失败;status 为 HTTP 状态码(连不上为 0)。"""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


def has_cjk(text: str) -> bool:
    return bool(re.search(r"[぀-ヿ㐀-鿿가-힯]", text or ""))


# ---------------------------------------------------------------- Key / 图源
def api_key(provider: str) -> str:
    if provider not in KEY_ENV:
        return ""
    key = os.environ.get(KEY_ENV[provider]) or ""
    if not key:
        try:
            key = str(json.loads(FOOTAGE_CONFIG.read_text(encoding="utf-8")).get(f"{provider}_api_key") or "")
        except Exception:  # noqa: BLE001
            key = ""
    return key.strip()


def providers() -> list[dict]:
    """弹窗图源下拉:[{id, label, ready, needs_key, home}]。"""
    return [{"id": p, "label": PROVIDER_LABELS[p], "home": PROVIDER_HOME[p],
             "needs_key": p in KEY_ENV, "ready": p not in KEY_ENV or bool(api_key(p))} for p in PROVIDERS]


# ---------------------------------------------------------------- HTTP
def _open(req: urllib.request.Request, timeout: int = TIMEOUT):
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise StockError(f"HTTP {e.code}: {detail}", e.code) from e
    except Exception as e:  # noqa: BLE001  DNS / TCP / TLS / 超时
        host = urllib.parse.urlsplit(req.full_url).hostname or req.full_url
        raise StockError(_t(f"连不上 {host}:{e}", f"Cannot reach {host}: {e}")) from e


def _get_json(provider: str, url: str, headers: dict | None = None) -> tuple[dict, dict]:
    """GET → (JSON, 响应头);连不上快速重试一次,429 换成可读的限流说明。"""
    retried = False
    while True:
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT,
                                                   **(headers or {})})
        try:
            with _open(req) as r:
                raw = r.read().decode("utf-8", "replace")
                hdrs = dict(r.headers.items())
            break
        except StockError as e:
            if e.status == 0 and not retried:
                retried = True
                time.sleep(1.0)
                continue
            if e.status == 429:
                limits = {"openverse": _t("匿名每分钟 20 次、每天 200 次", "20 per minute / 200 per day anonymously"),
                          "pexels": _t("每小时 200 次", "200 per hour"),
                          "pixabay": _t("每分钟 100 次", "100 per minute")}[provider]
                raise StockError(_t(f"{PROVIDER_LABELS[provider]} 限流({limits}),稍后再试或换个图源",
                                    f"{PROVIDER_LABELS[provider]} rate limit reached ({limits}); "
                                    "try again later or switch source"), 429) from e
            if e.status in (401, 403) and provider in KEY_ENV:
                raise StockError(_t(f"{PROVIDER_LABELS[provider]} 拒绝了 API Key(检查 {KEY_ENV[provider]})",
                                    f"{PROVIDER_LABELS[provider]} rejected the API key (check {KEY_ENV[provider]})"),
                                 e.status) from e
            raise
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise StockError(_t(f"{PROVIDER_LABELS[provider]} 没有返回 JSON:{raw[:200]}",
                            f"{PROVIDER_LABELS[provider]} did not return JSON: {raw[:200]}")) from e
    if not isinstance(data, dict):
        raise StockError(_t(f"{PROVIDER_LABELS[provider]} 返回异常:{raw[:200]}",
                            f"Unexpected response from {PROVIDER_LABELS[provider]}: {raw[:200]}"))
    return data, hdrs


# ---------------------------------------------------------------- 条目归一
def _site_label(code: str) -> str:
    code = code or ""
    if code.startswith("smithsonian"):
        return "Smithsonian"
    return SITE_NAMES.get(code, code.replace("_", " ").title())


_WIKIMEDIA = re.compile(r"(https://upload\.wikimedia\.org/wikipedia/[^/]+)/([0-9a-f])/([0-9a-f]{2})/([^/?#]+)$")


def _wikimedia_thumb(url: str, width: int, size: int) -> str:
    """Wikimedia 原图地址 → 它自己的缩略图(size 取官方标准档 500 / 1280);原图不比 size 宽、或不是 Wikimedia 地址返回空串。
    Openverse 的 /thumb/ 对上千万像素的 Wikimedia 原图常回 424「Thumbnail unavailable from provider」(2026-10-23 实测)。"""
    m = _WIKIMEDIA.match(url or "")
    if not m or not width or width <= size:
        return ""
    return f"{m[1]}/thumb/{m[2]}/{m[3]}/{m[4]}/{size}px-{m[4]}"


def _item(provider: str, iid, **kw) -> dict:
    """统一结构;thumb_alt 是缩略图加载失败时页面改用的地址;download 只留在服务端(_ITEMS),返回给页面前去掉。"""
    it = {"provider": provider, "id": str(iid), "title": "", "thumb": "", "thumb_alt": "", "preview": "",
          "width": 0, "height": 0,
          "author": "", "author_url": "", "page_url": "", "site": PROVIDER_LABELS[provider], "license": "",
          "license_label": "", "license_url": "", "attribution": "", "attribution_required": False, "download": ""}
    it.update({k: v for k, v in kw.items() if v is not None})
    return it


def _from_openverse(r: dict) -> dict | None:
    lic = (r.get("license") or "").lower()
    if lic not in OPENVERSE_LICENSES.split(",") or not r.get("url") or not r.get("id"):
        return None
    w = int(r.get("width") or 0)
    wm = _wikimedia_thumb(r["url"], w, 500)
    return _item("openverse", r["id"], title=r.get("title") or "", thumb=wm or r.get("thumbnail") or r["url"],
                 thumb_alt=r["url"], preview=_wikimedia_thumb(r["url"], w, 1280) or r["url"],
                 width=w, height=int(r.get("height") or 0),
                 author=r.get("creator") or "", author_url=r.get("creator_url") or "",
                 page_url=r.get("foreign_landing_url") or "", site=_site_label(r.get("source") or ""),
                 license=lic, license_label=openverse.license_label(lic, r.get("license_version") or ""),
                 license_url=r.get("license_url") or "", attribution=r.get("attribution") or "",
                 attribution_required=lic == "by", download=r["url"])


def _from_pexels(p: dict) -> dict | None:
    src = p.get("src") or {}
    if not p.get("id") or not src.get("original"):
        return None
    who = p.get("photographer") or ""
    return _item("pexels", p["id"], title=p.get("alt") or "", thumb=src.get("medium") or src.get("small") or "",
                 thumb_alt=src.get("large") or src["original"], preview=src.get("large2x") or src.get("large") or src["original"],
                 width=int(p.get("width") or 0), height=int(p.get("height") or 0), author=who,
                 author_url=p.get("photographer_url") or "", page_url=p.get("url") or "",
                 license="pexels", license_label="Pexels License", license_url="https://www.pexels.com/license/",
                 attribution=f"Photo by {who} on Pexels" if who else "Photo from Pexels", download=src["original"])


def _from_pixabay(h: dict) -> dict | None:
    big = h.get("largeImageURL") or h.get("webformatURL")
    if not h.get("id") or not big:
        return None
    who = h.get("user") or ""
    w, hh = int(h.get("imageWidth") or 0), int(h.get("imageHeight") or 0)
    if w and hh and max(w, hh) > 1280:            # API 给的 largeImageURL 长边 1280,宽高报的是原图
        s = 1280 / max(w, hh)
        w, hh = round(w * s), round(hh * s)
    return _item("pixabay", h["id"], title=h.get("tags") or "", thumb=h.get("webformatURL") or big, thumb_alt=big,
                 preview=big,
                 width=w, height=hh, author=who,
                 author_url=f"https://pixabay.com/users/{who}-{h.get('user_id')}/" if who and h.get("user_id") else "",
                 page_url=h.get("pageURL") or "", license="pixabay", license_label="Pixabay Content License",
                 license_url="https://pixabay.com/service/license-summary/",
                 attribution=f"Image by {who} from Pixabay" if who else "Image from Pixabay", download=big)


def _remember(items: list[dict]) -> None:
    for it in items:
        _ITEMS.pop((it["provider"], it["id"]), None)
        _ITEMS[(it["provider"], it["id"])] = it
    while len(_ITEMS) > _ITEMS_MAX:
        _ITEMS.pop(next(iter(_ITEMS)))


def public(it: dict) -> dict:
    return {k: v for k, v in it.items() if k != "download"}


# ---------------------------------------------------------------- 检索缓存(24 h)
def _cache_path(key: str) -> Path:
    return CACHE_DIR / (hashlib.sha1(key.encode("utf-8")).hexdigest()[:24] + ".json")


def _cache_get(key: str) -> dict | None:
    p = _cache_path(key)
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if rec.get("key") != key or time.time() - float(rec.get("ts") or 0) > CACHE_TTL:
        return None
    return rec.get("data")


def _cache_put(key: str, data: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for old in CACHE_DIR.glob("*.json"):              # 顺带清过期的
        try:
            if now - old.stat().st_mtime > CACHE_TTL:
                old.unlink()
        except OSError:
            pass
    tmp = _cache_path(key).with_suffix(".tmp")
    tmp.write_text(json.dumps({"key": key, "ts": now, "data": data}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, _cache_path(key))


# ---------------------------------------------------------------- 检索
def search(provider: str, q: str, page: int = 1, orientation: str = "", kind: str = "",
           client_id: str = "", client_secret: str = "") -> dict:
    """搜一页 → {provider, query, page, total, has_more, results: [条目], rate, cached}。
    client_id / client_secret 只对 Openverse 有用(音效 › Openverse 存的凭证,空 = 匿名)。"""
    if provider not in PROVIDERS:
        raise StockError(_t(f"未知图源:{provider}", f"Unknown image source: {provider}"), 400)
    q = re.sub(r"\s+", " ", (q or "").strip())[:100]
    if not q:
        raise StockError(_t("请输入搜索关键词", "Enter a search term"), 400)
    orientation = orientation if orientation in ORIENTATIONS else ""
    kind = kind if kind in KINDS else ""
    page = max(1, int(page or 1))
    if provider == "openverse" and page > OPENVERSE_MAX_PAGE:
        raise StockError(_t("Openverse 匿名最多翻到第 12 页", "Openverse allows at most 12 pages anonymously"), 400)
    if provider in KEY_ENV and not api_key(provider):
        raise StockError(_t(f"{PROVIDER_LABELS[provider]} 需要 API Key(环境变量 {KEY_ENV[provider]}),本期未配置",
                            f"{PROVIDER_LABELS[provider]} needs an API key ({KEY_ENV[provider]}), not configured"), 400)
    key = json.dumps([provider, q, page, orientation, kind], ensure_ascii=False)
    hit = _cache_get(key)
    if hit is not None:
        items = [it for it in hit.get("items") or [] if isinstance(it, dict) and it.get("download")]
        _remember(items)
        return {**{k: v for k, v in hit.items() if k != "items"}, "results": [public(it) for it in items],
                "cached": True}
    data = {"openverse": _search_openverse, "pexels": _search_pexels, "pixabay": _search_pixabay}[provider](
        q, page, orientation, kind, client_id=client_id, client_secret=client_secret)
    data.update(provider=provider, query=q, page=page)
    _cache_put(key, data)
    _remember(data["items"])
    out = {k: v for k, v in data.items() if k != "items"}
    return {**out, "results": [public(it) for it in data["items"]], "cached": False}


def _search_openverse(q, page, orientation, kind, *, client_id="", client_secret=""):
    params = {"q": q, "license": OPENVERSE_LICENSES, "extension": "jpg,png,webp", "page": page,
              "page_size": PAGE_SIZE["openverse"]}
    if orientation:
        params["aspect_ratio"] = {"horizontal": "wide", "vertical": "tall", "square": "square"}[orientation]
    if kind:
        params["category"] = "photograph" if kind == "photo" else "illustration,digitized_artwork"
    headers = {}
    try:
        token = openverse.access_token(client_id, client_secret)
    except openverse.OpenverseError:
        token = ""                                          # 凭证不对就退回匿名,不挡检索
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data, hdrs = _get_json("openverse", f"{openverse.API_BASE}/images/?" + urllib.parse.urlencode(params), headers)
    items = [x for x in (_from_openverse(r) for r in data.get("results") or [] if isinstance(r, dict)) if x]
    pages = int(data.get("page_count") or 0)
    return {"total": int(data.get("result_count") or 0), "items": items,
            "has_more": page < min(pages, OPENVERSE_MAX_PAGE), "rate": openverse._rate(hdrs)}


def _search_pexels(q, page, orientation, kind, **_):
    params = {"query": q, "page": page, "per_page": PAGE_SIZE["pexels"]}
    if orientation:
        params["orientation"] = {"horizontal": "landscape", "vertical": "portrait", "square": "square"}[orientation]
    if has_cjk(q):
        params["locale"] = "zh-CN"
    data, hdrs = _get_json("pexels", "https://api.pexels.com/v1/search?" + urllib.parse.urlencode(params),
                           {"Authorization": api_key("pexels")})
    items = [x for x in (_from_pexels(p) for p in data.get("photos") or [] if isinstance(p, dict)) if x]
    low = {k.lower(): v for k, v in hdrs.items()}
    rate = {"monthly": {"limit": low.get("x-ratelimit-limit"), "available": low.get("x-ratelimit-remaining")}} \
        if low.get("x-ratelimit-remaining") else {}
    return {"total": int(data.get("total_results") or 0), "items": items, "has_more": bool(data.get("next_page")),
            "rate": rate}


def _search_pixabay(q, page, orientation, kind, **_):
    params = {"key": api_key("pixabay"), "q": q, "page": page, "per_page": PAGE_SIZE["pixabay"],
              "safesearch": "true", "image_type": kind or "all"}
    if orientation in ("horizontal", "vertical"):              # Pixabay 没有方图档
        params["orientation"] = orientation
    if has_cjk(q):
        params["lang"] = "zh"
    data, hdrs = _get_json("pixabay", "https://pixabay.com/api/?" + urllib.parse.urlencode(params))
    items = [x for x in (_from_pixabay(h) for h in data.get("hits") or [] if isinstance(h, dict)) if x]
    hits = int(data.get("totalHits") or 0)
    low = {k.lower(): v for k, v in hdrs.items()}
    rate = {"burst": {"limit": low.get("x-ratelimit-limit"), "available": low.get("x-ratelimit-remaining")}} \
        if low.get("x-ratelimit-remaining") else {}
    return {"total": int(data.get("total") or hits), "items": items,
            "has_more": page * PAGE_SIZE["pixabay"] < hits, "rate": rate}


# ---------------------------------------------------------------- 取单条 / 下载
def lookup(provider: str, iid: str, client_id: str = "", client_secret: str = "") -> dict:
    """(provider, id) → 含下载地址的条目:先查本进程检索过的,查不到(服务重启过)再按 id 回源取一次。"""
    iid = str(iid or "").strip()
    if provider not in PROVIDERS or not re.fullmatch(r"[A-Za-z0-9-]{1,64}", iid):
        raise StockError(_t("图片编号无效", "Invalid image id"), 400)
    it = _ITEMS.get((provider, iid))
    if it:
        return it
    if provider == "openverse":
        headers = {}
        try:
            token = openverse.access_token(client_id, client_secret)
        except openverse.OpenverseError:
            token = ""
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data, _ = _get_json("openverse", f"{openverse.API_BASE}/images/{iid}/", headers)
        it = _from_openverse(data)
    elif provider == "pexels":
        if not api_key("pexels"):
            raise StockError(_t("Pexels 需要 API Key", "Pexels needs an API key"), 400)
        data, _ = _get_json("pexels", f"https://api.pexels.com/v1/photos/{iid}", {"Authorization": api_key("pexels")})
        it = _from_pexels(data)
    else:
        if not api_key("pixabay"):
            raise StockError(_t("Pixabay 需要 API Key", "Pixabay needs an API key"), 400)
        data, _ = _get_json("pixabay", "https://pixabay.com/api/?" + urllib.parse.urlencode(
            {"key": api_key("pixabay"), "id": iid}))
        hits = data.get("hits") or []
        it = _from_pixabay(hits[0]) if hits and isinstance(hits[0], dict) else None
    if not it:
        raise StockError(_t("图库里找不到这张图(或许可不在可收范围)", "Image not found (or its license is not accepted)"),
                         404)
    _remember([it])
    return it


def _check_url(url: str) -> None:
    """下载地址来自图库接口返回;仍拒绝非 http(s) 与本机 / 内网地址。"""
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host or host == "localhost" or host.endswith(".local"):
        raise StockError(_t(f"下载地址不可用:{url[:120]}", f"Unusable download URL: {url[:120]}"), 400)
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
        raise StockError(_t(f"下载地址不可用:{url[:120]}", f"Unusable download URL: {url[:120]}"), 400)


def fetch(it: dict, max_side: int = MAX_SIDE) -> tuple[bytes, str]:
    """下载原图 → (字节, 扩展名);长边超过 max_side 等比缩小并重新编码(有透明通道存 PNG,否则 JPEG)。"""
    from PIL import Image, ImageOps

    url = it.get("download") or ""
    _check_url(url)
    if it.get("width") and it.get("height") and int(it["width"]) * int(it["height"]) > MAX_PIXELS:
        raise StockError(_t("这张图像素太多(超过 1.6 亿),请换一张", "This image is too large (over 160 MP); pick another"),
                         400)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "image/*,*/*;q=0.8"})
    buf = bytearray()
    retried = False
    while True:
        try:
            with _open(req, timeout=120) as r:
                while True:
                    chunk = r.read(1 << 16)
                    if not chunk:
                        break
                    buf += chunk
                    if len(buf) > MAX_DOWNLOAD_BYTES:
                        raise StockError(_t("原图超过 60 MB,已跳过", "The original is over 60 MB; skipped"), 400)
            break
        except StockError as e:
            if e.status == 0 and not retried and not buf:
                retried = True
                time.sleep(1.0)
                continue
            raise
    data = bytes(buf)
    if not data:
        raise StockError(_t("下载到的图片为空", "The downloaded image is empty"))
    try:
        img = Image.open(io.BytesIO(data))
        fmt = (img.format or "").upper()
        w, h = img.size
        scale = min(1.0, max_side / max(w, h))
        if fmt == "JPEG" and scale < 1:                       # 大 JPEG 解码时先按 1/2、1/4… 降采样,省内存
            img.draft("RGB", (max(1, round(w * scale)), max(1, round(h * scale))))
        img.load()
    except Exception as e:  # noqa: BLE001
        raise StockError(_t(f"下载的不是可识别的图片:{e}", f"The download is not a readable image: {e}")) from e
    ext = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}.get(fmt)
    if ext and scale == 1:
        return data, ext                                     # 不超限:原字节原格式
    img = ImageOps.exif_transpose(img)
    if max(img.size) > max_side:
        s = max_side / max(img.size)
        img = img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))), Image.LANCZOS)
    out = io.BytesIO()
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        img.convert("RGBA").save(out, "PNG", optimize=True)
        return out.getvalue(), ".png"
    img.convert("RGB").save(out, "JPEG", quality=92, optimize=True)
    return out.getvalue(), ".jpg"


# ---------------------------------------------------------------- 中文检索词 → 英文
TRANSLATE_SYSTEM = ("You turn an image-search query into English search keywords for a stock photo library. "
                    "Reply with 1 to 3 plain English keywords separated by spaces (nouns first, no articles), "
                    "nothing else: no quotes, no explanation, no punctuation.")
_LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def translate_query(text: str, conn: dict, timeout: int = 90) -> str:
    """调用 OpenAI 兼容 chat/completions(conn = {base_url, api_key, model}),把检索词译成 1–3 个英文关键词。"""
    text = re.sub(r"\s+", " ", (text or "").strip())[:200]
    if not text:
        raise StockError(_t("没有要翻译的内容", "Nothing to translate"), 400)
    base = str(conn.get("base_url") or "").rstrip("/")
    model = str(conn.get("model") or "").strip()
    if not base or not model:
        raise StockError(_t("Agentics 引擎没有选文本模型(「🎨 生成模型」页或控制台顶栏)",
                            "No text model is selected for the Agentics engine (Generation Models page or top bar)"),
                         400)
    body = {"model": model, "temperature": 0, "max_tokens": 1024,
            "messages": [{"role": "system", "content": TRANSLATE_SYSTEM}, {"role": "user", "content": text}]}
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    if conn.get("api_key"):
        headers["Authorization"] = f"Bearer {conn['api_key']}"
    req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode("utf-8"), headers=headers)
    host = (urllib.parse.urlsplit(base).hostname or "").lower()
    # macOS 系统代理会劫持发往本机的请求(LM Studio 等本地模型),本机地址直连
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})).open if host in _LOOPBACK else urllib.request.urlopen
    try:
        with opener(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        raise StockError(_t(f"文本模型 {model} 调用失败:HTTP {e.code} {detail}",
                            f"Text model {model} failed: HTTP {e.code} {detail}"), 502) from e
    except Exception as e:  # noqa: BLE001
        raise StockError(_t(f"连不上文本模型({base}):{e}", f"Cannot reach the text model ({base}): {e}"), 502) from e
    try:
        msg = data["choices"][0]["message"]
        reply = msg.get("content") or ""
    except (KeyError, IndexError, TypeError, AttributeError):
        raise StockError(_t(f"文本模型返回异常:{json.dumps(data)[:200]}",
                            f"Unexpected text model reply: {json.dumps(data)[:200]}"), 502) from None
    words = clean_keywords(reply)
    if not words:
        raise StockError(_t("文本模型没有给出英文关键词", "The text model returned no English keywords"), 502)
    return words


def clean_keywords(reply: str) -> str:
    """去掉推理模型的 <think> 段与引号 / 标点,取第一行有英文词的内容(「Keywords: …」只取冒号后),最多 4 个词。"""
    reply = re.sub(r"<think>.*?</think>", " ", reply or "", flags=re.S | re.I)
    reply = re.sub(r"^.*</think>", " ", reply, flags=re.S | re.I)           # 只有收尾标签的(开头被模板吃掉)
    for line in reply.splitlines():
        words = re.findall(r"[A-Za-z][A-Za-z'-]*", line.split(":")[-1])
        if words:
            return " ".join(words[:4]).lower()
    return ""
