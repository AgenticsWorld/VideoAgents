"""TTS 语音模式与音色库(2026-10-02)。

「生成模型」页 TTS 每个渠道先选**模式**:
  - 音色设计(design):Voice Design 模型按声纹卡文字描述出嗓音样本,Voice Clone 模型拿样本当参考出对白/旁白。
    支持渠道:agentics(两个 profile)、comfyui(两套工作流)、volcengine(两格都是 Doubao-音频生成 1.0)。
  - 音色库(library):从音色库里给每个人物选一个音色(登记 casting.json),再用模型合成。
    agentics / comfyui 的音色库是**本地**音色库(data/TimbreModel 的参考音频,配能收参考音频的 Voice Clone 模型);
    volcengine / openrouter / elevenlabs / minimax 是渠道自带的音色 ID 库。
配置键:tts.<provider>.voice_mode(design|library)。comfyui 段的 mode 是运行方式(本地/云端/RunningHub),与此无关。

音色库不在界面上设置:本模块按需自动拉取(本机缓存 24 小时,拉取失败用旧缓存)、按人物声纹卡自动打分选型。
配音工位用 `python3 modules/genmedia.py voices --character <CHAR-ID>` 取候选,选定后登记 casting.json 再合成;
合成时宿主不替工位偷偷选音色(casting.json 仍是唯一事实源),只有不带人物也没给音色的调用(无声线卡的旁白、
直播对口型)才自动取一个旁白型音色。

各渠道数据源:
  volcengine  OpenAPI ListSpeakers(账号 Access Key / Secret Key 签名;填在 TTS › 火山引擎 › 音色库模式)
  elevenlabs  GET /v1/voices(账号内音色;公共 Voice Library 只搜索推荐,加入账号须工位显式 --add)
  minimax     POST /v1/get_voice(系统音色 + 账号内克隆/生成音色)
  openrouter  模型目录 supported_voices(只有名字,性别/语言靠命名规律与内置标签表,拿不准的标「须听辨」)
  local       modules/timbre_selector 的目录索引(data/TimbreModel)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("VIDEOAGENTS_DATA_DIR") or ROOT / "data").expanduser().resolve()
RUNTIME_DIR = Path(os.environ.get("VIDEOAGENTS_RUNTIME_DIR", DATA_DIR / ".videoagents")).expanduser().resolve()
CONFIG_PATH = Path(os.environ.get("VIDEOAGENTS_CONFIG_PATH", RUNTIME_DIR / "genconfig.json")).expanduser().resolve()
CACHE_DIR = RUNTIME_DIR / "voice_library"
CACHE_TTL = 24 * 3600
HTTP_TIMEOUT = 30

MODES = ("design", "library")
DESIGN_PROVIDERS = ("agentics", "comfyui", "volcengine")    # 支持音色设计模式的渠道
LOCAL_LIBRARY_PROVIDERS = ("agentics", "comfyui")           # 音色库模式用本地音色库的渠道
SEEDAUDIO_MODEL = "seed-audio-1.0"
VOLC_LIBRARY_MODELS = ("seed-tts-2.0", "seed-tts-1.0")
MINIMAX_BASES = ("https://api.minimax.io", "https://api.minimaxi.com")


# ---------------------------------------------------------------- 模式

def _comfy_design_configured(pc: dict) -> bool:
    key = "rh_design_workflow_id" if str(pc.get("mode") or "").startswith("rh_") else "design_workflow"
    return bool(str(pc.get(key) or "").strip())


def voice_mode(provider: str, pc: dict | None) -> str:
    """渠道生效的语音模式。只支持音色库的渠道恒为 library;没存过 voice_mode 的存量配置按旧行为推断
    (火山选的是音频生成 1.0、ComfyUI 配了 Voice Design 工作流 → design)。"""
    pc = pc or {}
    if provider not in DESIGN_PROVIDERS:
        return "library"
    saved = str(pc.get("voice_mode") or "").strip()
    if saved in MODES:
        return saved
    if provider == "volcengine":
        return "design" if (pc.get("custom_model") or pc.get("model") or "") == SEEDAUDIO_MODEL else "library"
    if provider == "comfyui":
        return "design" if _comfy_design_configured(pc) else "library"
    return "design"


def voice_library_kind(provider: str, pc: dict | None) -> str:
    """音色库模式下用哪种音色库:local(本地参考音频库)| provider(渠道音色 ID 库);音色设计模式返回 ''。"""
    if voice_mode(provider, pc) != "library":
        return ""
    return "local" if provider in LOCAL_LIBRARY_PROVIDERS else "provider"


def effective_model(provider: str, pc: dict | None) -> str:
    """按模式生效的模型:火山音色设计=音频生成 1.0,火山音色库=Seed-TTS 档(存量的音频生成/声音复刻值回落 2.0);
    agentics 与 comfyui 按 profile / 工作流运行,无单一模型,返回 ''。"""
    pc = pc or {}
    if provider in ("agentics", "comfyui"):
        return ""
    model = str(pc.get("custom_model") or pc.get("model") or "")
    if provider == "volcengine":
        if voice_mode(provider, pc) == "design":
            return SEEDAUDIO_MODEL
        return VOLC_LIBRARY_MODELS[0] if model in ("", SEEDAUDIO_MODEL, "seed-icl-2.0") else model
    return model


def load_tts_config() -> dict:
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8")).get("tts") or {}
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}


def tts_settings(cfg: dict | None = None) -> dict:
    """生效 TTS 设置摘要:{provider, voice_mode, voice_library, model, pc}。"""
    cfg = load_tts_config() if cfg is None else cfg
    provider = str(cfg.get("provider") or "")
    pc = cfg.get(provider) if isinstance(cfg.get(provider), dict) else {}
    return {"provider": provider, "voice_mode": voice_mode(provider, pc),
            "voice_library": voice_library_kind(provider, pc),
            "model": effective_model(provider, pc), "pc": pc}


# ---------------------------------------------------------------- 归一

_FEMALE = ("女", "female", "woman", "women", "girl", "lady", "queen", "princess", "sister", "aunt",
           "mother", "wife", "maid", "御姐", "萝莉")
_MALE = ("男", "male", "man", "men", "boy", "gentleman", "guy", "king", "prince", "brother", "uncle",
         "father", "husband", "大叔")
_AGE_WORDS = (("child", ("儿童", "童声", "孩童", "幼年", "小孩", "child", "kid")),
              ("elderly", ("老年", "年迈", "老人", "爷爷", "奶奶", "elderly", "senior", "old")),
              ("middle", ("中年", "middle")),
              ("young", ("少年", "少女", "青年", "young", "teen", "youth")))
_LANG_WORDS = (("zh", ("zh", "chinese", "mandarin", "cantonese", "中文", "普通话", "粤语")), ("en", ("en", "english")),
               ("ja", ("ja", "japanese")), ("ko", ("ko", "korean")), ("es", ("es", "spanish")),
               ("fr", ("fr", "french")), ("de", ("de", "german")), ("pt", ("pt", "portuguese")),
               ("ru", ("ru", "russian")), ("it", ("it", "italian")), ("id", ("id", "indonesian")),
               ("vi", ("vi", "vietnamese")), ("ar", ("ar", "arabic")), ("hi", ("hi", "hindi")))
# 声线关键词表:条目描述与声纹卡里同时出现即加分(中文没有分词,按词表逐个找)
_TIMBRE_WORDS = ("沉稳", "磁性", "温柔", "甜美", "活泼", "清亮", "清朗", "清澈", "清冷", "低沉", "浑厚", "厚重", "沙哑",
                 "干哑", "粗", "御姐", "霸气", "知性", "阳光", "开朗", "成熟", "稳重", "醇厚", "可爱", "明亮", "柔和",
                 "治愈", "冷", "温和", "爽朗", "利落", "干净", "威严", "苍老", "稚嫩", "少年", "青年", "中年", "老年",
                 "播音", "解说", "旁白", "纪录片", "新闻", "有声", "说书", "calm", "deep", "warm", "bright", "husky",
                 "soft", "narrat", "raspy", "gentle", "energetic", "serious", "mature")
_NARRATOR_WORDS = ("旁白", "解说", "播音", "纪录片", "有声", "说书", "新闻", "阅读", "narrat", "audiobook", "documentary",
                   "news", "storytell")


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.casefold()))


def guess_gender(*texts) -> str:
    """从名称/描述里认性别:中文按包含,英文按整词(woman 里的 man 不算)。认不出返回 ''。"""
    text = " ".join(str(t or "") for t in texts)
    low, words = text.casefold(), _words(text)
    female = any(t in low for t in _FEMALE if not t.isascii()) or any(t in words for t in _FEMALE if t.isascii())
    male = any(t in low for t in _MALE if not t.isascii()) or any(t in words for t in _MALE if t.isascii())
    if female != male:
        return "female" if female else "male"
    return ""


def guess_age(*texts) -> str:
    """认年龄档;「不作童声」这类否定写法里的年龄词不算。"""
    text = " ".join(str(t or "") for t in texts)
    low, words = text.casefold(), _words(text)

    def affirmed(token: str) -> bool:
        return any(not re.search(r"[不非无勿][^,。;、,.;]{0,3}$", low[max(0, m.start() - 4):m.start()])
                   for m in re.finditer(re.escape(token), low))
    for age, tokens in _AGE_WORDS:
        if any(affirmed(t) if not t.isascii() else (t in words) for t in tokens):
            return age
    return ""


def guess_langs(*texts) -> list[str]:
    text = " ".join(str(t or "") for t in texts)
    low, words = text.casefold(), _words(text)
    return [code for code, tokens in _LANG_WORDS
            if any((t in low) if not t.isascii() else (t in words) for t in tokens)]


def _entry(vid, name="", gender="", age="", languages=(), description="", tags=(), preview_url="",
           source="", priority=0, **extra) -> dict:
    gender = {"女": "female", "男": "male"}.get(str(gender).strip(), str(gender).strip().casefold())
    if gender not in ("male", "female"):
        gender = guess_gender(gender) or ""
    langs = []
    for raw in languages or ():
        code = str(raw or "").casefold().replace("_", "-").split("-")[0]
        code = next((c for c, tokens in _LANG_WORDS if code in tokens), code if len(code) == 2 else "")
        if code and code not in langs:
            langs.append(code)
    return {"id": str(vid), "name": str(name or vid), "gender": gender,
            "age": guess_age(age) or "", "languages": langs, "description": str(description or ""),
            "tags": [str(t) for t in tags or () if t], "preview_url": str(preview_url or ""),
            "source": source, "priority": priority, **extra}


# ---------------------------------------------------------------- 拉取

def _http_json(url: str, headers: dict | None = None, payload: dict | None = None) -> dict:
    data = None
    headers = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode()
        headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}") from e
    except (OSError, ValueError) as e:
        raise RuntimeError(str(e)) from e


def _volc_openapi(ak: str, sk: str, action: str, version: str, body: dict,
                  service: str = "speech_saas_prod", region: str = "cn-beijing",
                  host: str = "open.volcengineapi.com") -> dict:
    """火山引擎 OpenAPI POST 调用(HMAC-SHA256 签名,同官方 SDK Signer)。"""
    payload = json.dumps(body).encode()
    query = urllib.parse.urlencode(sorted({"Action": action, "Version": version}.items()))
    xdate = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    payload_hash = hashlib.sha256(payload).hexdigest()
    headers = {"host": host, "x-date": xdate, "x-content-sha256": payload_hash,
               "content-type": "application/json; charset=utf-8"}
    signed = ";".join(sorted(headers))
    canon = "\n".join(["POST", "/", query, "".join(f"{k}:{headers[k]}\n" for k in sorted(headers)),
                       signed, payload_hash])
    scope = f"{xdate[:8]}/{region}/{service}/request"
    sts = "\n".join(["HMAC-SHA256", xdate, scope, hashlib.sha256(canon.encode()).hexdigest()])

    def h(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()
    k_sign = h(h(h(h(sk.encode(), xdate[:8]), region), service), "request")
    sig = hmac.new(k_sign, sts.encode(), hashlib.sha256).hexdigest()
    req = urllib.request.Request(
        f"https://{host}/?{query}", data=payload, method="POST",
        headers={"Authorization": f"HMAC-SHA256 Credential={ak}/{scope}, SignedHeaders={signed}, Signature={sig}",
                 "X-Date": xdate, "X-Content-Sha256": payload_hash,
                 "Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}") from e
    except (OSError, ValueError) as e:
        raise RuntimeError(str(e)) from e


def volc_credentials(pc: dict | None) -> tuple[str, str]:
    """火山账号 Access Key / Secret Key:TTS › 火山引擎(音色库模式)里填的 → 环境变量
    VOLC_ACCESSKEY / VOLC_SECRETKEY → 存量配置兜底(2026-10-02 之前这对密钥借用「文件托管 › 火山引擎 TOS」
    的配置,还没在 TTS 页重新保存过的安装沿用它)。"""
    pc = pc or {}
    ak = str(pc.get("access_key") or os.environ.get("VOLC_ACCESSKEY") or "").strip()
    sk = str(pc.get("secret_key") or os.environ.get("VOLC_SECRETKEY") or "").strip()
    if not (ak and sk):
        try:
            tos = (json.loads(CONFIG_PATH.read_text(encoding="utf-8")).get("storage") or {}).get("tos") or {}
        except (OSError, ValueError):
            tos = {}
        ak, sk = str(tos.get("access_key") or "").strip(), str(tos.get("secret_key") or "").strip()
    return ak, sk


def _fetch_volcengine(pc: dict, model: str) -> list[dict]:
    ak, sk = volc_credentials(pc)
    if not (ak and sk):
        raise RuntimeError("火山音色库需要账号的 Access Key / Secret Key:「🎨 生成模型」页 TTS › 火山引擎"
                           "(音色库模式)填入(在火山引擎控制台「API 访问密钥」创建)")
    out, page = [], 1
    while True:
        r = _volc_openapi(ak, sk, "ListSpeakers", "2025-05-20",
                          {"ResourceIDs": [model], "Page": page, "Limit": 100})
        err = (r.get("ResponseMetadata") or {}).get("Error") or {}
        if err:
            raise RuntimeError(f"ListSpeakers {err.get('Code')}: {err.get('Message')}")
        res = r.get("Result") or {}
        batch = res.get("Speakers") or []
        for v in batch:
            cats = [c for group in v.get("Categories") or [] for c in (group.get("Categories") or [])]
            out.append(_entry(
                v.get("VoiceType") or "", v.get("Name"), v.get("Gender"), v.get("Age"),
                [x.get("Language") for x in v.get("Languages") or [] if isinstance(x, dict)],
                v.get("Description"), cats + (v.get("NormalLabels") or []) + (v.get("SpecialLabels") or []),
                v.get("TrialURL") or v.get("ShortTrialURL"), "system", int(v.get("Heat") or 0) // 20))
        if not batch or len(out) >= int(res.get("Total") or 0) or page >= 20:
            return [e for e in out if e["id"]]
        page += 1


def _elevenlabs_entry(v: dict, source: str) -> dict:
    labels = v.get("labels") or {}
    langs = [x.get("language") for x in v.get("verified_languages") or [] if isinstance(x, dict)]
    langs += [v.get("language") or labels.get("language")]
    desc = " ".join(str(x) for x in (v.get("description"), v.get("descriptive") or labels.get("descriptive"),
                                      labels.get("description"), v.get("accent") or labels.get("accent"),
                                      v.get("use_case") or labels.get("use_case")) if x)
    return _entry(v.get("voice_id") or "", v.get("name"), v.get("gender") or labels.get("gender"),
                  v.get("age") or labels.get("age"), langs, desc,
                  [x for x in (v.get("category"), v.get("use_case") or labels.get("use_case")) if x],
                  v.get("preview_url"), source, public_owner_id=str(v.get("public_owner_id") or ""))


def _fetch_elevenlabs(pc: dict, model: str) -> list[dict]:
    key = str(pc.get("api_key") or os.environ.get("ELEVENLABS_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("ElevenLabs 未配置 API Key")
    d = _http_json("https://api.elevenlabs.io/v1/voices", {"xi-api-key": key})
    return [e for e in (_elevenlabs_entry(v, "account") for v in d.get("voices") or []) if e["id"]]


def elevenlabs_search_library(pc: dict, search: str = "", limit: int = 30) -> list[dict]:
    """搜 ElevenLabs 公共 Voice Library(只供推荐;合成前须用 elevenlabs_add 加入账号)。"""
    key = str(pc.get("api_key") or os.environ.get("ELEVENLABS_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("ElevenLabs 未配置 API Key")
    q = {"page_size": str(max(1, min(100, limit)))}
    if search:
        q["search"] = search
    d = _http_json("https://api.elevenlabs.io/v1/shared-voices?" + urllib.parse.urlencode(q), {"xi-api-key": key})
    return [e for e in (_elevenlabs_entry(v, "library") for v in d.get("voices") or []) if e["id"]]


def elevenlabs_add(pc: dict, voice_id: str, public_owner_id: str, name: str = "") -> dict:
    """把公共 Voice Library 音色加入账号(占账号音色位;只在工位显式要求时调用),并清掉账号音色缓存。"""
    key = str(pc.get("api_key") or os.environ.get("ELEVENLABS_API_KEY") or "").strip()
    if not (key and voice_id and public_owner_id):
        raise RuntimeError("加入 ElevenLabs 音色需要 API Key、voice_id 与 public_owner_id")
    d = _http_json(f"https://api.elevenlabs.io/v1/voices/add/{urllib.parse.quote(public_owner_id)}"
                   f"/{urllib.parse.quote(voice_id)}", {"xi-api-key": key}, {"new_name": name or voice_id})
    _cache_path("elevenlabs", "").unlink(missing_ok=True)
    return {"voice_id": d.get("voice_id") or voice_id, "name": name or voice_id}


def _minimax_key(pc: dict) -> str:
    field = "api_key_cn" if "minimaxi.com" in str(pc.get("api_base") or "") else "api_key_io"
    return str(pc.get(field) or pc.get("api_key") or os.environ.get("MINIMAX_API_KEY") or "").strip()


def _fetch_minimax(pc: dict, model: str) -> list[dict]:
    key = _minimax_key(pc)
    if not key:
        raise RuntimeError("MiniMax 未配置 API Key")
    base = str(pc.get("api_base") or MINIMAX_BASES[0]).rstrip("/")
    d = _http_json(base + "/v1/get_voice", {"Authorization": f"Bearer {key}"}, {"voice_type": "all"})
    err = d.get("base_resp") or {}
    if err.get("status_code"):
        raise RuntimeError(f"MiniMax get_voice 失败(code={err['status_code']}):{err.get('status_msg') or ''}")
    out = []
    for group, source in (("system_voice", "system"), ("voice_cloning", "account"), ("voice_generation", "account")):
        for v in d.get(group) or []:
            desc = v.get("description")
            desc = " / ".join(str(x) for x in desc if x) if isinstance(desc, list) else str(desc or "")
            vid, name = v.get("voice_id") or "", v.get("voice_name") or ""
            langs = guess_langs(vid.split("_")[0]) or (["zh"] if re.search(r"[\u4e00-\u9fff]", name) else [])
            out.append(_entry(vid, name or vid, guess_gender(vid, name, desc), guess_age(vid, name, desc),
                              langs, desc, (), "", source))
    return [e for e in out if e["id"]]


# OpenRouter 目录只给音色名:已知命名的补性别/语言,其余留空(选型时标「须听辨」)
_OR_NAMED = {
    "x-ai/": {"eve": "female", "ara": "female", "rex": "male", "leo": "male"},
    "canopylabs/orpheus": {"tara": "female", "leah": "female", "jess": "female", "mia": "female", "zoe": "female",
                           "leo": "male", "dan": "male", "zac": "male"},
    "google/gemini": {n: "female" for n in (
        "zephyr", "kore", "leda", "aoede", "callirrhoe", "autonoe", "despina", "erinome", "laomedeia", "achernar",
        "gacrux", "pulcherrima", "vindemiatrix", "sulafat")} | {n: "male" for n in (
        "puck", "charon", "fenrir", "orus", "enceladus", "iapetus", "umbriel", "algieba", "algenib", "rasalgethi",
        "alnilam", "schedar", "achird", "zubenelgenubi", "sadachbia", "sadaltager")},
}
_KOKORO_LANG = {"a": "en", "b": "en", "j": "ja", "z": "zh", "e": "es", "f": "fr", "h": "hi", "i": "it", "p": "pt"}


def _openrouter_entry(model: str, voice: str) -> dict:
    low = voice.casefold()
    gender, langs = "", []
    named = next((table for prefix, table in _OR_NAMED.items() if model.startswith(prefix)), None)
    if named:
        gender = named.get(low, "")
    if model.startswith("hexgrad/kokoro") and re.match(r"^[a-z][fm]_", low):
        gender = "female" if low[1] == "f" else "male"
        langs = [_KOKORO_LANG.get(low[0], "")]
    elif model.startswith("minimax/"):
        gender, langs = guess_gender(voice), guess_langs(voice.split("_")[0])
    elif re.match(r"^[a-z]{2}-[A-Z]{2}-", voice):                    # MAI:en-US-Harper:MAI-Voice-2
        langs = [low[:2]]
    elif re.match(r"^[a-z]{2}_", low):                               # Voxtral:en_paul_neutral
        langs = [low[:2]]
    elif re.search(r"-([a-z]{2})$", low):                            # Deepgram:aura-2-thalia-en
        langs = [low.rsplit("-", 1)[1]]
    return _entry(voice, voice, gender, guess_age(voice), [x for x in langs if x], "", (), "", "system")


def _fetch_openrouter(pc: dict, model: str) -> list[dict]:
    d = _http_json("https://openrouter.ai/api/v1/models?output_modalities=speech")
    hit = next((m for m in d.get("data") or [] if m.get("id") == model), None)
    if hit is None:
        raise RuntimeError(f"OpenRouter 语音模型目录里没有 {model}")
    voices = [str(v) for v in hit.get("supported_voices") or [] if v]
    if not voices:
        raise RuntimeError(f"OpenRouter 模型 {model} 没有公开音色清单(音色库模式不能用,换一个模型)")
    return [_openrouter_entry(model, v) for v in voices]


def _fetch_local(pc: dict, model: str) -> list[dict]:
    try:
        from modules.timbre_selector import load_catalog
    except ImportError:                               # 脚本直跑时无包前缀
        from timbre_selector import load_catalog
    _, entries = load_catalog(pc.get("timbre_dir") or "", pc.get("timbre_catalog") or "")
    return [_entry(e["file"], Path(str(e["file"])).stem, e.get("gender"), "", ["zh"], "",
                   list(e.get("tags") or []) + list(e.get("roles") or []), "", "local",
                   int(e.get("priority") or 0), ages=list(e.get("ages") or []), pitch=str(e.get("pitch") or ""))
            for e in entries]


_FETCHERS = {"volcengine": _fetch_volcengine, "elevenlabs": _fetch_elevenlabs, "minimax": _fetch_minimax,
             "openrouter": _fetch_openrouter, "local": _fetch_local}


def _cache_path(library: str, model: str) -> Path:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", model or "all").strip("-") or "all"
    return CACHE_DIR / f"{library}--{slug}.json"


def load_library(library: str, pc: dict | None = None, model: str = "", refresh: bool = False) -> dict:
    """取音色库条目(本机缓存 CACHE_TTL;refresh=True 强制重拉;拉取失败时用旧缓存并在 stale 里写原因)。
    library:volcengine | elevenlabs | minimax | openrouter | local。→ {library, model, fetched_at, entries, stale}"""
    if library not in _FETCHERS:
        raise RuntimeError(f"没有 {library} 这个音色库")
    pc = pc or {}
    path = _cache_path(library, model if library in ("volcengine", "openrouter") else "")
    cached = None
    if library != "local":                            # 本地目录读文件即可,不缓存
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cached = None
        if cached and not refresh and time.time() - float(cached.get("fetched_at") or 0) < CACHE_TTL:
            return {**cached, "stale": ""}
    try:
        entries = _FETCHERS[library](pc, model)
    except RuntimeError as err:
        if cached and cached.get("entries"):
            return {**cached, "stale": str(err)}
        raise
    doc = {"library": library, "model": model, "fetched_at": int(time.time()), "entries": entries}
    if library != "local":
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass
    return {**doc, "stale": ""}


# ---------------------------------------------------------------- 选型

def search(entries: list[dict], query: str) -> list[dict]:
    """关键词过滤(空格分隔,全部命中):名称 / ID / 性别 / 年龄 / 语言 / 标签 / 描述。"""
    words = [w for w in str(query or "").casefold().split() if w]
    if not words:
        return list(entries)
    out = []
    for e in entries:
        hay = " ".join([e["id"], e["name"], e["gender"], {"female": "女", "male": "男"}.get(e["gender"], ""),
                        e["age"], " ".join(e["languages"]), " ".join(e["tags"]), e["description"]]).casefold()
        if all(w in hay for w in words):
            out.append(e)
    return out


def rank(entries: list[dict], profile: dict, *, narrator: bool = False, lang: str = "",
         taken: dict | None = None) -> list[dict]:
    """按人物画像(modules/timbre_selector._profile:gender/age/pitch/voice_text/text)给渠道音色打分排序。
    性别对不上的直接滤掉;已分给别的人物的音色(taken={音色: 人物})排到最后并注明。"""
    taken = taken or {}
    voice_text, text = profile.get("voice_text") or "", profile.get("text") or ""
    out = []
    for e in entries:
        if profile.get("gender") and e["gender"] and e["gender"] != profile["gender"]:
            continue
        score, reasons = int(e.get("priority") or 0), []
        if profile.get("gender"):
            if e["gender"]:
                score += 30
                reasons.append(f"gender={e['gender']}")
            else:
                score -= 15
                reasons.append("性别未知,须听辨")
        if e["age"] and e["age"] == profile.get("age"):
            score += 8
            reasons.append(f"age={e['age']}")
        elif e["age"] and profile.get("age") and e["age"] != profile["age"]:
            score -= 12 if {e["age"], profile["age"]} & {"child", "elderly"} else 2
        if lang and e["languages"]:
            if lang in e["languages"]:
                score += 12
                reasons.append(f"lang={lang}")
            else:
                score -= 25
        hay = " ".join([e["name"], e["description"], " ".join(e["tags"])]).casefold()
        if lang == "zh" and ("粤语" in hay or "cantonese" in (e["id"] + hay).casefold()) and "粤" not in voice_text:
            score -= 25                               # 普通话人物不取粤语音色
        matched = [w for w in _TIMBRE_WORDS if w in hay and w in voice_text]
        loose = [w for w in _TIMBRE_WORDS if w in hay and w not in voice_text and w in text]
        score += 8 * len(matched) + 2 * len(loose)
        if matched or loose:
            reasons.append("声线=" + "/".join((matched + loose)[:5]))
        if narrator and any(w in hay for w in _NARRATOR_WORDS):
            score += 16
            reasons.append("narrator")
        owner = taken.get(e["id"])
        if owner:
            score -= 1000
            reasons.append(f"已分给 {owner}")
        out.append({**e, "score": score, "reason": ", ".join(reasons) or "默认排序"})
    return sorted(out, key=lambda e: (-e["score"], e["id"].casefold()))


def voice_card_age(project_root: Path | None, character: str, variant: str = "") -> str:
    """人物嗓音年龄档(child/young/middle/elderly),只看声纹卡的年龄字段(形态分版优先):先认「25-30」这类岁数,
    再认年龄词。timbre_selector 的画像是在全部人物文字里找年龄词,父母辈人物常被「奶奶 / 年迈」之类的剧情用词带偏。"""
    if not (project_root and character):
        return ""
    try:
        card = json.loads((Path(project_root) / "bible" / "characters" / character / "voice.json")
                          .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    docs = [card]
    if variant:
        docs = [item for item in card.get("age_variants") or [] if isinstance(item, dict) and variant.casefold() in
                " ".join(str(item.get(k) or "") for k in ("variant", "version_id", "id", "name", "label")).casefold()] + docs
    for doc in docs:
        for key in ("age_band", "voice_age_impression", "timbre"):
            raw = str(doc.get(key) or "")
            m = re.search(r"(\d{1,3})\s*[-–~至到]\s*(\d{1,3})", raw) or re.search(r"(\d{1,3})()\s*岁", raw)
            if m:
                years = (int(m.group(1)) + int(m.group(2) or m.group(1))) / 2
                return "child" if years < 13 else "young" if years < 35 else "middle" if years < 55 else "elderly"
            age = guess_age(raw)
            if age:
                return age
    return ""


def taken_voices(project_root: Path | None, character: str = "", variant: str = "") -> dict[str, str]:
    """选角表里已分出去的音色:{tts_voice: 人物};本人物(任何形态)自己的不算。"""
    if not project_root:
        return {}
    try:
        d = json.loads((Path(project_root) / "assets" / "audio" / "voice" / "casting.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for c in d.get("castings") or d.get("entries") or []:
        if not isinstance(c, dict):
            continue
        ch, voice = c.get("character_id") or c.get("char_id") or "", str(c.get("tts_voice") or "").strip()
        if voice and ch != character:
            out[voice] = ch
    return out


def text_lang(*texts) -> str:
    """合成文本的语言(只分中文/其它):有汉字算 zh,有拉丁字母算 en,否则不设限。"""
    text = " ".join(str(t or "") for t in texts)
    if re.search(r"[一-鿿]", text):
        return "zh"
    return "en" if re.search(r"[A-Za-z]{3}", text) else ""


def recommend(character: str = "", variant: str = "", project: str = "", output: str = "", text: str = "",
              query: str = "", top: int = 8, refresh: bool = False, cfg: dict | None = None,
              lang: str = "") -> dict:
    """当前 TTS 设置下给人物(不传 character=旁白)推荐音色。→ {provider, voice_mode, voice_library, model,
    total, stale, candidates:[{id,name,gender,age,languages,description,preview_url,score,reason}], note}"""
    try:
        from modules import timbre_selector as ts
    except ImportError:
        import timbre_selector as ts
    st = tts_settings(cfg)
    base = {k: st[k] for k in ("provider", "voice_mode", "voice_library", "model")}
    if st["voice_mode"] == "design":
        return {**base, "total": 0, "stale": "", "candidates": [],
                "note": "当前渠道是音色设计模式:不选音色,按声纹卡描述出嗓音样本(genmedia tts --character … "
                        "输出 <CHAR>_voiceprint.mp3),对白拿样本当参考"}
    character = ts._normalize_character(character)
    root = ts._resolve_project(project, output)
    if character and not (root and (root / "bible" / "characters" / character).is_dir()):
        raise RuntimeError(f"读不到 {character} 的人物设定(项目:{root or '未识别'})")
    taken = taken_voices(root, character, variant)
    if st["voice_library"] == "local":
        if query:
            lib = load_library("local", st["pc"])
            profile = ts._profile(root, character, variant, text, "")
            ranked = rank(search(lib["entries"], query), profile, narrator=not character, taken=taken)
        else:
            profile, rows = ts.rank_timbres(text, output or "x.mp3", character, variant, project or str(root or ""),
                                            "", st["pc"].get("timbre_dir") or "", st["pc"].get("timbre_catalog") or "")
            ranked = [{"id": e["file"], "name": Path(str(e["file"])).stem, "gender": str(e.get("gender") or ""),
                       "age": "/".join(e.get("ages") or []), "languages": ["zh"], "description": "",
                       "tags": list(e.get("tags") or []), "preview_url": "", "source": "local",
                       "score": score - (1000 if taken.get(e["file"]) else 0),
                       "reason": ", ".join(reasons + ([f"已分给 {taken[e['file']]}"] if taken.get(e["file"]) else []))
                                 or "catalog priority"}
                      for score, e, reasons in rows]
            ranked.sort(key=lambda e: (-e["score"], e["id"].casefold()))
        return {**base, "total": len(ranked), "stale": "", "candidates": ranked[:top],
                "note": "本地音色库(data/TimbreModel):音色是参考音频文件,casting.json 的 tts_voice 登记文件名"}
    lib = load_library(st["provider"], st["pc"], st["model"], refresh)
    profile = ts._profile(root, character, variant, text, "")
    profile["age"] = voice_card_age(root, character, variant) or ("adult" if not character else profile["age"])
    ranked = rank(search(lib["entries"], query), profile, narrator=not character,
                  lang=lang or text_lang(text, profile.get("voice_text")), taken=taken)
    note = ""
    if st["provider"] == "elevenlabs":
        note = ("只列账号内已有音色;不够用时 voices --scope library --search <关键词> 搜公共音色库,"
                "选中的用 voices --add <voice_id> --owner <public_owner_id> 加入账号(占账号音色位)后再登记")
    elif any(not e["gender"] for e in ranked[:top]):
        note = "标「性别未知,须听辨」的音色登记前先试合成一句听辨,casting 条目备注 gender_verified: true"
    return {**base, "total": len(ranked), "stale": lib.get("stale") or "", "candidates": ranked[:top], "note": note}


def default_voice(provider: str, pc: dict, model: str, text: str = "") -> str:
    """不带人物也没给音色的调用(无声线卡的旁白、直播对口型)用的音色:存量配置里的默认音色 →
    音色库里自动取一个旁白型音色(按文本语言)。取不到返回 ''。"""
    legacy = str((pc or {}).get("voice") or "").strip()
    if legacy and provider != "openrouter":
        return legacy
    try:
        lib = load_library(provider, pc, model)
    except RuntimeError:
        return legacy                                 # 音色库拉不到:有旧值就用旧值
    if legacy and any(e["id"] == legacy for e in lib["entries"]):
        return legacy                                 # OpenRouter 各模型音色互不通用:旧值属于当前模型才用
    ranked = rank(lib["entries"], {"gender": "", "age": "adult", "voice_text": "", "text": ""},
                  narrator=True, lang=text_lang(text))
    return ranked[0]["id"] if ranked else ""
