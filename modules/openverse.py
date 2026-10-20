"""Openverse 音效检索(2026-10-20):「生成模型」页「🔊 音效模型」的 Openverse 标签页(音效默认渠道),
以及 genmedia.py sfx 选 Openverse 时的取材。

不生成音频:按英文关键词搜 Openverse(https://api.openverse.org/v1/audio/)收录的 CC 授权 / 公有领域音频,
下载原文件交 genmedia 后处理。接口要点(2026-10-20 实测):
- 匿名可用:每分钟 20 次、每天 200 次(响应头 x-ratelimit-*);注册应用取 client_id / client_secret 后
  POST /v1/auth_tokens/token/(仅收 form 编码)换 Bearer 令牌,额度略高。令牌缓存在 data/.videoagents/openverse_token.json。
- 必须带 Accept: application/json,否则 Cloudflare 可能回 DRF 的 HTML 浏览页。
- q 是「所有词都要命中」:整句英文描述基本 0 条,只能用 2–3 个关键词;keywords / query_ladder 把描述压成关键词并逐级减词。
- Freesound 的条目 category 为空,category=sound_effect 会把它们全滤掉,所以不按 category 过滤;来源默认只取 freesound
  (wikimedia_audio 大多是读音录音,jamendo 是音乐)。
- length 档:shortest <30 s、short 30 s–2 min、medium 2–10 min、long >10 min;duration 字段单位毫秒。
- Freesound 的 url 是 128 kbps mp3 预览(cdn.freesound.org/previews/…-hq.mp3),无需登录即可下载。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("VIDEOAGENTS_DATA_DIR", ROOT / "data")).expanduser().resolve()
RUNTIME_DIR = Path(os.environ.get("VIDEOAGENTS_RUNTIME_DIR", DATA_DIR / ".videoagents")).expanduser().resolve()
TOKEN_PATH = RUNTIME_DIR / "openverse_token.json"
API_BASE = "https://api.openverse.org/v1"
USER_AGENT = "VideoAgents/1.0 (+https://github.com/AgenticsWorld/VideoAgents; sound effect search)"

# 许可范围:只收可商用、可改编、不传染的许可。BY-SA 会把整部片子绑进同一许可,NC / ND 不能商用 / 改编,一律不收
LICENSE_SETS = ("cc0,pdm", "cc0,pdm,by")
SOURCE_SETS = ("freesound", "freesound,wikimedia_audio")
DEFAULT_LICENSES = LICENSE_SETS[0]
DEFAULT_SOURCES = SOURCE_SETS[0]
LENGTHS = ("shortest", "short", "medium", "long")
PAGE_SIZE = 20                  # 匿名单页上限
MAX_KEYWORDS = 4
MAX_DOWNLOAD_BYTES = 60 * 1024 * 1024
TIMEOUT = 20
LICENSE_NAMES = {"cc0": "CC0", "pdm": "Public Domain Mark", "by": "CC BY", "by-sa": "CC BY-SA",
                 "by-nc": "CC BY-NC", "by-nd": "CC BY-ND", "by-nc-sa": "CC BY-NC-SA", "by-nc-nd": "CC BY-NC-ND",
                 "sampling+": "Sampling+", "nc-sampling+": "NC Sampling+"}
SOURCE_NAMES = {"freesound": "Freesound", "wikimedia_audio": "Wikimedia Commons", "jamendo": "Jamendo",
                "ccmixter": "ccMixter"}
# 关键词里去掉的词:虚词,以及几乎每条音效描述都会带、却很少出现在素材标题 / 标签里的泛称
STOPWORDS = frozenset("""
a an the of in on at to into onto over under through across from with without by for and or but as than then
is are was were be being been it its this that these those there here very really slightly quite some any
each every one two three few many much more most less little lot lots like near far behind inside outside
while during after before up down out off away around against between among along via per
sound sounds effect effects sfx audio recording recorded clip sample samples noise noises
""".split())


class OpenverseError(RuntimeError):
    """Openverse 请求失败;status 为 HTTP 状态码(连不上为 0)。"""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


def license_label(code: str, version: str = "") -> str:
    name = LICENSE_NAMES.get((code or "").lower(), (code or "").upper())
    return f"{name} {version}".strip() if version else name


def source_label(code: str) -> str:
    return SOURCE_NAMES.get(code or "", code or "")


# ---------------------------------------------------------------- 关键词
def keywords(text: str) -> list[str]:
    """英文描述 → 关键词(保序去重,去虚词与泛称),最多 MAX_KEYWORDS 个。"""
    out: list[str] = []
    for w in re.findall(r"[a-z][a-z']*", (text or "").lower()):
        w = w.strip("'")
        if len(w) < 2 or w in STOPWORDS or w in out:
            continue
        out.append(w)
    return out[:MAX_KEYWORDS]


def query_ladder(query: str = "", prompt: str = "") -> list[str]:
    """检索尝试序列:先用全部关键词,0 条再从尾部逐个减词(接口按「所有词都命中」检索,词越多越少)。
    query 显式给出时只拿它的词,不再看 prompt。"""
    words = keywords(query) if (query or "").strip() else keywords(prompt)
    return [" ".join(words[:n]) for n in range(len(words), 0, -1)]


def length_buckets(min_s: float = 0.0, max_s: float | None = None) -> str:
    """把时长区间换成 length 参数(逗号分隔);不限时返回空串。"""
    bounds = {"shortest": (0, 30), "short": (30, 120), "medium": (120, 600), "long": (600, float("inf"))}
    hi = float("inf") if max_s is None else max_s
    picked = [k for k in LENGTHS if bounds[k][1] > min_s and bounds[k][0] < hi]
    return "" if len(picked) == len(LENGTHS) else ",".join(picked)


# ---------------------------------------------------------------- HTTP / 令牌
def _open(req: urllib.request.Request, timeout: int = TIMEOUT):
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise OpenverseError(f"HTTP {e.code}:{detail}", e.code) from e
    except Exception as e:  # noqa: BLE001  DNS / TCP / TLS / 超时
        raise OpenverseError(f"连不上 Openverse({API_BASE}):{e}") from e


def _token_key(client_id: str, client_secret: str) -> str:
    return hashlib.sha1(f"{client_id}:{client_secret}".encode("utf-8")).hexdigest()[:16]


def access_token(client_id: str, client_secret: str, refresh: bool = False) -> str:
    """client_credentials 换 Bearer 令牌并缓存到过期前 60 s;两项都空返回空串(匿名)。"""
    client_id, client_secret = (client_id or "").strip(), (client_secret or "").strip()
    if not client_id and not client_secret:
        return ""
    if not client_id or not client_secret:
        raise OpenverseError("Openverse Client ID 与 Client Secret 须同时填写(都留空 = 匿名访问)", 400)
    key = _token_key(client_id, client_secret)
    if not refresh:
        try:
            rec = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
            if rec.get("key") == key and float(rec.get("expires_at") or 0) > time.time() + 60 and rec.get("token"):
                return rec["token"]
        except Exception:  # noqa: BLE001
            pass
    body = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": client_id,
                                   "client_secret": client_secret}).encode()
    req = urllib.request.Request(f"{API_BASE}/auth_tokens/token/", data=body, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded",
                                          "Accept": "application/json", "User-Agent": USER_AGENT})
    try:
        with _open(req) as r:
            data = json.loads(r.read().decode("utf-8"))
    except OpenverseError as e:
        if e.status in (400, 401, 403):
            raise OpenverseError(f"Openverse 令牌申请被拒(Client ID / Secret 不对,或注册邮件还没验证):{e}",
                                 e.status) from e
        raise
    token = str(data.get("access_token") or "")
    if not token:
        raise OpenverseError(f"Openverse 令牌接口没有返回 access_token:{json.dumps(data)[:200]}")
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(json.dumps({"key": key, "token": token,
                                      "expires_at": time.time() + float(data.get("expires_in") or 3600)}),
                          encoding="utf-8")
    return token


def _rate(headers) -> dict:
    """x-ratelimit-limit-<名> / x-ratelimit-available-<名> → {burst|sustained: {limit, available}}。"""
    out: dict = {}
    for name, value in headers.items():
        m = re.match(r"x-ratelimit-(limit|available)-(.+)", name.lower())
        if not m:
            continue
        kind = "burst" if m.group(2).endswith("burst") else "sustained" if m.group(2).endswith("sustained") else ""
        if kind:
            out.setdefault(kind, {})[m.group(1)] = value
    return out


def _normalize(r: dict) -> dict:
    dur = r.get("duration")
    tags = [t.get("name") for t in (r.get("tags") or []) if isinstance(t, dict) and t.get("name")]
    return {"id": r.get("id") or "", "title": r.get("title") or "", "url": r.get("url") or "",
            "duration_s": round(dur / 1000, 3) if isinstance(dur, (int, float)) else None,
            "license": (r.get("license") or "").lower(), "license_version": r.get("license_version") or "",
            "license_label": license_label(r.get("license") or "", r.get("license_version") or ""),
            "license_url": r.get("license_url") or "", "creator": r.get("creator") or "",
            "creator_url": r.get("creator_url") or "", "source": r.get("source") or "",
            "source_label": source_label(r.get("source") or ""), "filetype": (r.get("filetype") or "").lower(),
            "filesize": r.get("filesize"), "landing_url": r.get("foreign_landing_url") or "",
            "attribution": r.get("attribution") or "", "tags": tags[:12]}


def search(q: str, licenses: str = DEFAULT_LICENSES, sources: str = DEFAULT_SOURCES, length: str = "",
           client_id: str = "", client_secret: str = "", page_size: int = PAGE_SIZE) -> dict:
    """搜一页音频。返回 {query, result_count, results: [_normalize 后的条目], rate}。"""
    q = re.sub(r"\s+", " ", (q or "").strip())[:200]
    if not q:
        raise OpenverseError("Openverse 检索需要关键词", 400)
    if licenses not in LICENSE_SETS:
        raise OpenverseError(f"Openverse 许可范围无效:{licenses}(可选 {' / '.join(LICENSE_SETS)})", 400)
    if sources not in SOURCE_SETS:
        raise OpenverseError(f"Openverse 来源无效:{sources}(可选 {' / '.join(SOURCE_SETS)})", 400)
    params = {"q": q, "license": licenses, "source": sources, "page_size": max(1, min(PAGE_SIZE, int(page_size)))}
    if length:
        params["length"] = length
    url = f"{API_BASE}/audio/?" + urllib.parse.urlencode(params)
    token = access_token(client_id, client_secret)
    refreshed = retried = False
    while True:
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            with _open(urllib.request.Request(url, headers=headers)) as r:
                raw = r.read().decode("utf-8", "replace")
                rate = _rate(r.headers)
            break
        except OpenverseError as e:
            if e.status == 401 and token and not refreshed:     # 缓存的令牌失效:重新申请一次
                refreshed = True
                token = access_token(client_id, client_secret, refresh=True)
                continue
            if e.status == 0 and not retried:                   # 连不上 / 读超时:对方偶发慢(实测同一检索 0.8–30 s+),快速重试一次
                retried = True
                time.sleep(1.0)
                continue
            if e.status == 429:
                raise OpenverseError("Openverse 限流(匿名每分钟 20 次、每天 200 次):稍后再试,或在「生成模型」页"
                                     "音效 › Openverse 填写 Client ID / Client Secret 提高额度", 429) from e
            raise
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise OpenverseError(f"Openverse 没有返回 JSON:{raw[:200]}") from e
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise OpenverseError(f"Openverse 返回异常:{raw[:300]}")
    return {"query": q, "result_count": int(data.get("result_count") or 0),
            "results": [_normalize(r) for r in data["results"] if isinstance(r, dict) and r.get("url")],
            "rate": rate}


def download(url: str, dest: str | Path, max_bytes: int = MAX_DOWNLOAD_BYTES) -> int:
    """下载素材原文件到 dest,超过 max_bytes 即中止;返回字节数。"""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    total = 0
    dest = Path(dest)
    try:
        with _open(req, timeout=120) as r, dest.open("wb") as f:
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise OpenverseError(f"素材超过 {max_bytes // (1024 * 1024)} MB,已跳过")
                f.write(chunk)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    if not total:
        dest.unlink(missing_ok=True)
        raise OpenverseError("素材下载为空")
    return total
