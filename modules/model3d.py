"""3D 模型渠道(2026-11-25):生成模型页「🧊 3D 模型」板块(世界模型下方)的配置辅助。

配置段 genconfig.json#model3d,生效渠道 provider:
- tripo     = Tripo3D 官方 API(https://developers.tripo3d.ai/zh/docs/introduction)。国际站 openapi.tripo3d.ai、
              国内站 openapi.tripo3d.com(同一套 /v3 接口),两站 Key 分存 api_key_ai / api_key_cn;
              model = 请求体的 model 字段(H 系列 v3.1/v3.0/v2.5,P 系列低面数 P2/P1),文生 / 图生 / 多视图共用。
- fal       = Fal 托管的 3D 端点;model 存模型前缀(同图像 / 视频 Fal 的约定),按输入补 text-to-3d / image-to-3d 任务段,
              「自定义…」可填完整端点 ID。
- rhapi     = RunningHub 标准模型 API 的 3D 端点(目录见 modules/rh_models.py,kind=model3d);t23d = 文生 3D、
              i23d = 图生 3D(含多视图)两个端点,两站 Key 分存。
- hunyuan3d = 局域网 Hunyuan3D-2 服务(仓库自带 api_server.py:POST /generate 同步回模型文件、POST /send 回 uid、
              GET /status/{uid});模型权重由服务端启动参数决定(api_server.py 默认 Hunyuan3D-2mini Turbo),
              这里的 model 只记录服务端加载的是哪一版(Turbo 版推理 5 步即可,其余版本要 30 步以上);
              texture = 是否请求纹理(服务端须带 --enable_tex 启动)。

设置页的连接验证(tripo_balance / hunyuan3d_check)之外,2026-11-25 接入「图生 3D」:参考文件页「🧊 3D 模型」板块的
「✨ 生成」窗口(core.api_refgen3d_*)经 `genmedia.py model3d` 调 generate(),出 GLB。

多参考图 = 多视图(2026-11-25 调研各渠道接口):3D 生成不收「任意多张参考图」,收的是同一物体的固定视角——
正面(必填)+ 左 / 后 / 右(选填)。各渠道 / 模型支持程度不同,按模型自动处理,收不下的视角如实写进 notes:
- Tripo 官方 v3:全部模型(H / P 系列)都有 multiview-to-model,inputs 用视角键,正面必填且至少两张。
- Fal:Hunyuan 3D 3.1 Pro(input_image_url + back/left/right_image_url 具名槽位)、Tripo H3.1(multiview-to-3d,
  image_urls 按 [正, 左, 后, 右] 位置认图,缺左侧时后 / 右用不上)、Meshy 7.1(multi-image-to-3d,1–4 张不分视角)、
  Rodin 2.5(image_urls 不分视角)、Hi3D 3.0(multi-view-to-3d 具名槽位);Hunyuan 3.1 Rapid、Tripo P2、TRELLIS 2、Idol Max / Max Turbo 只收一张。
- RH:hunyuan3d-v3.1/image-to-3d 自带 left/back/right 槽位;tripo-* 与 hitem3d-* 有同名 multiview / multi-image 端点
  (按端点名自动换);miora-3d 收 multiViewImages(不分视角);meshy6 只收一张。
- 本地 Hunyuan3D-2 api_server.py:只收一张(params.image),多视图版 2mv 的权重 api_server 也调不了多视图。
"""
from __future__ import annotations

import base64
import ipaddress
import json
import mimetypes
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

TRIPO_SITES = {
    "ai": {"base": "https://openapi.tripo3d.ai/v3", "label": "tripo3d.ai",
           "keys_url": "https://developers.tripo3d.ai/zh/keys"},
    "cn": {"base": "https://openapi.tripo3d.com/v3", "label": "tripo3d.com",
           "keys_url": "https://developers.tripo3d.com/zh/keys"},
}
# Tripo 请求体 model 取值(docs: /docs/models-and-versions、/docs/generation-text-to-model/p);首项 = 默认
TRIPO_MODELS = ("v3.1-20260211", "v3.0-20250812", "v2.5-20250123", "P2-20260801", "P1-20260311")
# 局域网 Hunyuan3D-2 服务端加载的 DiT 版本(Hugging Face tencent/Hunyuan3D-2* 的 subfolder);首项 = api_server.py 默认
HUNYUAN3D_MODELS = ("hunyuan3d-dit-v2-mini-turbo", "hunyuan3d-dit-v2-mini", "hunyuan3d-dit-v2-0-turbo",
                    "hunyuan3d-dit-v2-0-fast", "hunyuan3d-dit-v2-0", "hunyuan3d-dit-v2-mv-turbo", "hunyuan3d-dit-v2-mv")
HUNYUAN3D_PATHS = ("/generate", "/send", "/status/{uid}")


class Model3DError(RuntimeError):
    pass


def tripo_site(value) -> str:
    v = str(value or "").strip().lower()
    return "cn" if v == "cn" or "tripo3d.com" in v else "ai"


def _is_private(host: str) -> bool:
    if host in ("localhost",) or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


def _open(req: urllib.request.Request, timeout: float):
    """局域网 / 本机地址绕过系统代理直连(macOS 上 urllib 会读系统代理,把内网请求发给代理);公网照常。"""
    host = urllib.parse.urlsplit(req.full_url).hostname or ""
    if _is_private(host):
        return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)


def tripo_balance(site: str, api_key: str, timeout: float = 20, zh: bool = True) -> dict:
    """GET /v3/account/balance → {balance, frozen}(单位 credits)。Key 无效 / 网络失败抛 Model3DError;
    zh=False 时报错文案用英文(随界面语言)。"""
    site = tripo_site(site)
    label = TRIPO_SITES[site]["label"]
    key = str(api_key or "").strip()
    if not key:
        raise Model3DError(f"Tripo API Key 未填写({label})" if zh else f"Tripo API key is empty ({label})")
    req = urllib.request.Request(TRIPO_SITES[site]["base"] + "/account/balance",
                                 headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
    try:
        with _open(req, timeout) as r:
            body = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read() or b"{}")
        except ValueError:
            err = {}
        msg = str(err.get("message") or e.reason or "").strip()
        tail = f" · {msg}" if msg else ""
        if e.code == 401 or err.get("code") in (2, 1002):
            raise Model3DError(f"Key 无效或未授权(HTTP {e.code}{tail})" if zh
                               else f"Invalid or unauthorized key (HTTP {e.code}{tail})") from None
        raise Model3DError(f"HTTP {e.code}{tail}") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        why = getattr(e, "reason", e)
        raise Model3DError(f"连接 {label} 失败:{why}" if zh else f"Could not reach {label}: {why}") from None
    if body.get("code") not in (0, None):
        raise Model3DError(f"code={body.get('code')} {body.get('message') or ''}".strip())
    data = body.get("data") or {}
    return {"balance": data.get("balance"), "frozen": data.get("frozen")}


def hunyuan3d_check(base_url: str, timeout: float = 10, zh: bool = True) -> dict:
    """局域网 Hunyuan3D-2 服务连通性:读 /openapi.json,核对仓库自带 api_server 的三个接口都在。
    不发生成请求(服务端没有健康检查接口,也不暴露加载的模型)。zh=False 时报错文案用英文。"""
    base = _h3d_base(base_url, zh)
    req = urllib.request.Request(base + "/openapi.json", headers={"Accept": "application/json"})
    try:
        with _open(req, timeout) as r:
            spec = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise Model3DError(f"{base}/openapi.json {'返回' if zh else 'returned'} HTTP {e.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        why = getattr(e, "reason", e)
        raise Model3DError(f"连不上 {base}:{why}" if zh else f"Could not reach {base}: {why}") from None
    except ValueError:
        raise Model3DError(f"{base}/openapi.json 不是 JSON,地址可能不是 Hunyuan3D 服务" if zh
                           else f"{base}/openapi.json is not JSON; this may not be a Hunyuan3D server") from None
    paths = set((spec.get("paths") or {}) if isinstance(spec, dict) else {})
    missing = [p for p in HUNYUAN3D_PATHS if p not in paths]
    if missing:
        miss = ", ".join(missing)
        raise Model3DError(f"{base} 不是 Hunyuan3D-2 api_server(缺接口 {miss})" if zh
                           else f"{base} is not a Hunyuan3D-2 api_server (missing {miss})")
    return {"base_url": base, "paths": list(HUNYUAN3D_PATHS)}


# ======================================================================
# 图生 3D(2026-11-25):参考文件页「🧊 3D 模型」板块「✨ 生成」→ genmedia.py model3d → generate()
# ======================================================================
PROVIDERS = ("tripo", "fal", "rhapi", "hunyuan3d")
PROVIDER_LABELS = {"tripo": "Tripo", "fal": "Fal", "rhapi": "RH", "hunyuan3d": "本地 Hunyuan3D"}
VIEWS = ("front", "left", "back", "right")          # Tripo 规范顺序;正面必填
VIEW_NAMES_ZH = {"front": "正面", "left": "左侧", "back": "背面", "right": "右侧"}
H3D_DEFAULT_URL = "http://127.0.0.1:8080"            # 与 core DEFAULT_GENCONFIG、models.html H3D_DEFAULT_URL 一致
GLB_MAGIC = b"glTF"
IMAGE_MAX_SIDE = 2048                                # 发出去的参考图长边上限(各家只要 ≥256px,大图徒增上传与请求体)
IMAGE_MAX_BYTES = 6 * 1024 * 1024
GEN_TIMEOUT = 30 * 60
POLL_S = 4
META_PREFIX = "MODEL3D_META "                        # genmedia model3d 成功后 stdout 末行:本前缀 + JSON(core 解析)
TRIPO_LABELS = {"v3.1-20260211": "Tripo H3.1", "v3.0-20250812": "Tripo H3.0", "v2.5-20250123": "Tripo H2.5",
                "P2-20260801": "Tripo P2", "P1-20260311": "Tripo P1"}
# Fal 内置 3D 模型(模型前缀;与 models.html FAL_3D_MODELS 同序,首项 = 默认);第三项 = 是否收多视图(按 2026-11-25 端点参数表)
FAL_MODELS = (
    ("fal-ai/hunyuan-3d/v3.1/pro", "Hunyuan 3D 3.1 Pro", True),
    ("fal-ai/hunyuan-3d/v3.1/rapid", "Hunyuan 3D 3.1 Rapid", False),
    ("tripo3d/h3.1", "Tripo H3.1", True),
    ("tripo3d/p2", "Tripo P2", False),
    ("meshy/v7.1", "Meshy 7.1", True),
    ("fal-ai/hyper3d/rodin/v2.5", "Hyper3D Rodin 2.5", True),
    ("fal-ai/trellis-2", "TRELLIS 2", False),
    ("hitem3d/hi3d/v3.0", "Hi3D 3.0", True),
    # Sperid Labs Idol(2026-11-25 用户要求加入):只收一张 image_url;出 game_glb(3 万面 + UV + 2048 贴图)与
    # model_glb(全分辨率顶点色网格),取 game_glb;没有关纹理的开关;enable_pbr 另加 $0.10,不开
    ("speridlabs/idol/max", "Idol Max", False),
    ("speridlabs/idol/max/turbo", "Idol Max Turbo", False),
)
H3D_LABELS = {"hunyuan3d-dit-v2-mini-turbo": "Hunyuan3D-2mini Turbo", "hunyuan3d-dit-v2-mini": "Hunyuan3D-2mini",
              "hunyuan3d-dit-v2-0-turbo": "Hunyuan3D-2 Turbo", "hunyuan3d-dit-v2-0-fast": "Hunyuan3D-2 Fast",
              "hunyuan3d-dit-v2-0": "Hunyuan3D-2", "hunyuan3d-dit-v2-mv-turbo": "Hunyuan3D-2mv Turbo",
              "hunyuan3d-dit-v2-mv": "Hunyuan3D-2mv"}
# RH 端点参数表里的视角槽位
_RH_VIEW_KEYS = {"left": "leftImageUrl", "back": "backImageUrl", "right": "rightImageUrl"}
_RH_EXTRA_LIST = "multiViewImages"                   # miora-3d:正面之外的视角,不分方位


def model_label(provider: str, model: str) -> str:
    """模型展示名(产品名,不随界面语言变):Tripo / Fal 内置 / 本地按表,其余(RH 端点、自定义)原样。"""
    if provider == "tripo":
        return TRIPO_LABELS.get(model, model)
    if provider == "fal":
        return next((lab for m, lab, _ in FAL_MODELS if m == model), model)
    if provider == "hunyuan3d":
        return H3D_LABELS.get(model, model)
    return model


def _env_zh() -> bool:
    return (os.environ.get("VIDEOAGENTS_UI_LANG") or "zh").lower().startswith("zh")


def _t(zh_text: str, en_text: str, zh: bool | None = None) -> str:
    return zh_text if (_env_zh() if zh is None else zh) else en_text


def _log(msg: str) -> None:
    print(f"[genmedia] {msg}", file=sys.stderr, flush=True)


def _view_names(views, zh: bool | None = None) -> str:
    use_zh = _env_zh() if zh is None else zh
    return ("、" if use_zh else ", ").join(VIEW_NAMES_ZH[v] if use_zh else v for v in views)


def _h3d_base(base_url, zh: bool = True) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        raise Model3DError("服务地址未填写" if zh else "Server address is empty")
    if "://" not in base:       # 「192.168.x.x:8080」这类不带协议的地址(urlsplit 会把 IP 当成 scheme)
        base = "http://" + base
    if urllib.parse.urlsplit(base).scheme not in ("http", "https"):
        raise Model3DError("服务地址须以 http:// 或 https:// 开头" if zh
                           else "Server address must start with http:// or https://")
    return base


def _first_key(allcfg: dict, section: str, getter) -> str:
    """图像 / 视频段任一段填过的同渠道 Key(Fal / RH 账号一个 Key 通用,同 genmedia.get_config 的口径)。"""
    for kind in ("image", "video"):
        v = getter((allcfg.get(kind) or {}).get(section) or {})
        if v:
            return v
    return ""


def _model_of(pc: dict, default: str) -> str:
    # 生成模型页存法:选内置项 model = 该项、custom_model 空;选「自定义…」两者都是自填值
    return str(pc.get("model") or pc.get("custom_model") or default).strip()


def resolve(allcfg: dict, provider: str = "", model: str = "", zh: bool | None = None) -> dict:
    """genconfig 全文 → 本次 3D 生成用的渠道配置 {provider, model, api_key / site / base_url …}。
    provider / model 为空 = 「生成模型」页 3D 模型段的生效渠道与所选模型;Key 未配置抛 Model3DError。"""
    m3 = allcfg.get("model3d") if isinstance(allcfg.get("model3d"), dict) else {}
    prov = str(provider or m3.get("provider") or "tripo").strip()
    if prov not in PROVIDERS:
        raise Model3DError(_t(f"未知的 3D 渠道 {prov}(可选 {', '.join(PROVIDERS)})",
                              f"Unknown 3D provider {prov} (one of {', '.join(PROVIDERS)})", zh))
    pc = m3.get(prov) if isinstance(m3.get(prov), dict) else {}
    model = str(model or "").strip()
    where = _t("「🎨 生成模型」页 → 🧊 3D 模型", "Generation Models page → 🧊 3D Models", zh)
    if prov == "tripo":
        site = tripo_site(pc.get("site"))
        key = str(pc.get(f"api_key_{site}") or "").strip() or os.environ.get("TRIPO_API_KEY", "").strip()
        if not key:
            label = TRIPO_SITES[site]["label"]
            raise Model3DError(_t(f"Tripo 未填写 {label} 的 API Key({where} → Tripo)",
                                  f"No Tripo API key for {label} ({where} → Tripo)", zh))
        return {"provider": prov, "site": site, "api_key": key, "model": model or _model_of(pc, TRIPO_MODELS[0])}
    if prov == "fal":
        key = (str(pc.get("api_key") or "").strip()
               or _first_key(allcfg, "fal", lambda c: str(c.get("api_key") or "").strip())
               or os.environ.get("FAL_KEY", "").strip())
        if not key:
            raise Model3DError(_t(f"Fal 未填写 API Key({where} → Fal,或图像 / 视频的 Fal 标签页)",
                                  f"No Fal API key ({where} → Fal, or the Fal tab of Image / Video)", zh))
        return {"provider": prov, "api_key": key, "model": (model or _model_of(pc, FAL_MODELS[0][0])).strip("/")}
    if prov == "rhapi":
        from modules import rh_models
        site = rh_models.site_of(pc.get("site"))
        key = (rh_models.api_key(pc, site)
               or _first_key(allcfg, "rhapi", lambda c: rh_models.api_key(c, site))
               or os.environ.get("RUNNINGHUB_API_KEY", "").strip())
        label = rh_models.SITES[site]["label"]
        if not key:
            raise Model3DError(_t(f"RH 未填写 {label} 的 API Key({where} → RH)",
                                  f"No RH API key for {label} ({where} → RH)", zh))
        mid = model or str(pc.get("i23d") or "").strip()
        if not mid:
            raise Model3DError(_t(f"RH 没选图生 3D 模型({where} → RH)", f"No RH image-to-3D model selected ({where} → RH)", zh))
        return {"provider": prov, "site": site, "api_key": key, "model": mid}
    base = _h3d_base(pc.get("base_url") or H3D_DEFAULT_URL, _env_zh() if zh is None else zh)
    return {"provider": prov, "base_url": base, "model": model or _model_of(pc, HUNYUAN3D_MODELS[0]),
            "texture": pc.get("texture") is True}


# ---------------------------------------------------------------- 生成窗口的模型清单
def _rh_cached(site: str) -> dict:
    """RH 目录本地缓存里的 3D 端点 {id: entry}(只读缓存、不联网;任务类型按当前规则重算)。"""
    from modules import rh_models
    try:
        models = json.loads((rh_models.CACHE_DIR / f"{rh_models.site_of(site)}.json").read_text(encoding="utf-8")).get("models") or []
    except (OSError, ValueError, AttributeError):
        return {}
    out = {}
    for m in models:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        task = rh_models._task_of(m.get("category") or "", m["id"]) or str(m.get("task") or "")
        if task in rh_models.MODEL3D_TASKS:
            out[m["id"]] = {**m, "task": task}
    return out


def _rh_props(entry: dict) -> dict:
    return ((entry.get("schema") or {}).get("properties") or {}) if isinstance(entry, dict) else {}


def _rh_sibling(cat: dict, mid: str, want: str) -> dict | None:
    """同系列的另一任务端点:tripo-h/image-to-3d-v3.1 ↔ tripo-h/multiview-to-3d-v3.1,hitem3d-v3/image-to-3d ↔ multi-image-to-3d。"""
    head, _, last = mid.rpartition("/")
    if want == "multiview-to-3d":
        alts = [last.replace("image-to-3d", t, 1) for t in ("multiview-to-3d", "multi-image-to-3d")] if "image-to-3d" in last else []
    else:
        alts = [last.replace(t, "image-to-3d", 1) for t in ("multiview-to-3d", "multi-image-to-3d") if t in last]
    for alt in alts:
        e = cat.get(f"{head}/{alt}" if head else alt)
        if e and e.get("task") == want:
            return e
    return None


def _rh_multiview(cat: dict, entry: dict) -> bool:
    props = _rh_props(entry)
    return (entry.get("task") == "multiview-to-3d" or any(k in props for k in (*_RH_VIEW_KEYS.values(), _RH_EXTRA_LIST))
            or _rh_sibling(cat, entry["id"], "multiview-to-3d") is not None)


def options(allcfg: dict, zh: bool = True) -> dict:
    """生成 3D 模型窗口的「模型」下拉:按渠道分组,各带是否已配置(没 Key 置灰)与每个模型是否收多视图。
    {default: {provider, model}, providers: [{provider, label, configured, hint, models: [{id, label, multiview}]}]}
    multiview: True / False / None(自定义端点,生成时按参数表判断)。"""
    m3 = allcfg.get("model3d") if isinstance(allcfg.get("model3d"), dict) else {}
    rows = []

    def configured(prov: str) -> tuple[bool, str]:
        try:
            resolve(allcfg, prov, "x", zh)
            return True, ""
        except Model3DError as e:
            return False, str(e)

    # Tripo:全部模型都有多视图端点
    tp = m3.get("tripo") if isinstance(m3.get("tripo"), dict) else {}
    ok, hint = configured("tripo")
    models = [{"id": m, "label": TRIPO_LABELS.get(m, m), "multiview": True} for m in TRIPO_MODELS]
    cur = _model_of(tp, TRIPO_MODELS[0])
    if cur not in TRIPO_MODELS:
        models.append({"id": cur, "label": cur, "multiview": True})
    rows.append({"provider": "tripo", "label": "Tripo", "configured": ok, "hint": hint, "models": models})
    # Fal:内置 8 个按参数表定;生成模型页填的自定义端点放最后,能否多视图生成时再判
    fp = m3.get("fal") if isinstance(m3.get("fal"), dict) else {}
    ok, hint = configured("fal")
    models = [{"id": m, "label": lab, "multiview": mv} for m, lab, mv in FAL_MODELS]
    cur = _model_of(fp, FAL_MODELS[0][0]).strip("/")
    if cur not in {m for m, _, _ in FAL_MODELS}:
        models.append({"id": cur, "label": cur, "multiview": None})
    rows.append({"provider": "fal", "label": "Fal", "configured": ok, "hint": hint, "models": models})
    # RH:目录缓存里的图生 3D 端点(多视图端点由同系列图生端点按视角数自动换,不单列)
    rp = m3.get("rhapi") if isinstance(m3.get("rhapi"), dict) else {}
    ok, hint = configured("rhapi")          # 只看 Key;没选 i23d 不妨碍在窗口里挑一个
    from modules import rh_models
    site = rh_models.site_of(rp.get("site"))
    cat = _rh_cached(site)
    models = [{"id": mid, "label": str(e.get("name") or mid), "multiview": _rh_multiview(cat, e)}
              for mid, e in sorted(cat.items()) if e["task"] == "image-to-3d"]
    cur = str(rp.get("i23d") or "").strip()
    if cur and cur not in {m["id"] for m in models}:
        e = cat.get(cur)
        models.insert(0, {"id": cur, "label": str((e or {}).get("name") or cur),
                          "multiview": _rh_multiview(cat, e) if e else None})
    if ok and not models:
        ok, hint = False, _t("RH 的 3D 模型目录还没拉取:到「🎨 生成模型」页 → 🧊 3D 模型 → RH 标签页刷新模型列表",
                             "The RH 3D model catalog has not been fetched yet: refresh it on Generation Models page → 🧊 3D Models → RH", zh)
    rows.append({"provider": "rhapi", "label": "RH", "configured": ok, "hint": hint, "site": site, "models": models})
    # 本地:服务端加载哪个模型由启动参数定,只有一项;api_server.py 只收一张图
    hp = m3.get("hunyuan3d") if isinstance(m3.get("hunyuan3d"), dict) else {}
    ok, hint = configured("hunyuan3d")
    cur = _model_of(hp, HUNYUAN3D_MODELS[0])
    rows.append({"provider": "hunyuan3d", "label": PROVIDER_LABELS["hunyuan3d"], "configured": ok, "hint": hint,
                 "base_url": str(hp.get("base_url") or H3D_DEFAULT_URL), "texture": hp.get("texture") is True,
                 "models": [{"id": cur, "label": H3D_LABELS.get(cur, cur), "multiview": False}]})
    prov = str(m3.get("provider") or "tripo")
    prov = prov if prov in PROVIDERS else "tripo"
    dmodel = {"tripo": _model_of(tp, TRIPO_MODELS[0]), "fal": _model_of(fp, FAL_MODELS[0][0]).strip("/"),
              "rhapi": str(rp.get("i23d") or "").strip(), "hunyuan3d": cur}[prov]
    return {"default": {"provider": prov, "model": dmodel}, "providers": rows, "views": list(VIEWS)}


# ---------------------------------------------------------------- 公共:参考图整理、下载、GLB 校验
def _prep_image(path, workdir: Path, name: str) -> Path:
    """参考图整理成各家都收的样子:PNG / JPEG、长边 ≤2048、≤6 MB,按 EXIF 方向转正;已合规的原样用(不改磁盘上的原图)。
    有透明通道的存 PNG(抠好图的主体对 3D 生成最友好),否则存 JPEG。"""
    p = Path(path)
    if not p.is_file():
        raise Model3DError(_t(f"参考图不存在:{path}", f"Reference image not found: {path}"))
    from PIL import Image, ImageOps
    try:
        im = Image.open(p)
        im.load()
    except Exception as e:  # noqa: BLE001
        raise Model3DError(_t(f"读不了参考图 {p.name}:{e}", f"Cannot read reference image {p.name}: {e}")) from None
    fmt = (im.format or "").upper()
    rotate = (im.getexif() or {}).get(0x0112, 1) not in (1, None)
    big = max(im.size) > IMAGE_MAX_SIDE
    if fmt in ("PNG", "JPEG") and not big and not rotate and p.stat().st_size <= IMAGE_MAX_BYTES:
        return p
    if rotate:
        im = ImageOps.exif_transpose(im)
    if max(im.size) > IMAGE_MAX_SIDE:
        im.thumbnail((IMAGE_MAX_SIDE, IMAGE_MAX_SIDE), Image.LANCZOS)
    alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
    out = workdir / f"{name}.{'png' if alpha else 'jpg'}"
    if alpha:
        im.convert("RGBA").save(out, "PNG", optimize=True)
    else:
        im.convert("RGB").save(out, "JPEG", quality=92)
    return out


def _mime(path: Path) -> str:
    head = path.read_bytes()[:8]
    return "image/png" if head.startswith(b"\x89PNG") else "image/jpeg" if head.startswith(b"\xff\xd8") else (
        mimetypes.guess_type(path.name)[0] or "application/octet-stream")


def _download(url: str, label: str, attempts: int = 4) -> bytes:
    last: Exception | None = None
    for i in range(attempts):
        try:
            with _open(urllib.request.Request(url, headers={"User-Agent": "VideoAgents"}), 600) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code < 500 and e.code != 429:
                raise Model3DError(_t(f"{label} 模型文件下载失败:HTTP {e.code}", f"{label} model download failed: HTTP {e.code}")) from None
            last = e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
        _log(f"{label} 模型文件下载中断,第 {i + 1}/{attempts} 次重试…")
        time.sleep(3)
    raise Model3DError(_t(f"{label} 模型文件下载失败:{last}", f"{label} model download failed: {last}"))


def check_glb(data: bytes, label: str = "") -> bytes:
    """GLB(glTF 2.0 二进制)文件头校验:前 4 字节 glTF、版本 2。不是就报错(渠道回了 OBJ / FBX / 压缩包 / 错误页)。"""
    if len(data) >= 12 and data[:4] == GLB_MAGIC and int.from_bytes(data[4:8], "little") == 2:
        return data
    head = data[:160].decode("utf-8", "replace").strip() if data else ""
    tail = f":{head}" if head.startswith(("{", "<")) else ""
    raise Model3DError(_t(f"{label} 返回的不是 GLB 模型文件({len(data)} 字节){tail}",
                          f"{label} did not return a GLB model file ({len(data)} bytes){tail}"))


# ---------------------------------------------------------------- Tripo 官方 v3
def _tripo_err(status: int, err: dict) -> str:
    code = err.get("code")
    msg = str(err.get("message") or "").strip()
    sug = str(err.get("suggestion") or "").strip()
    tail = (f" · {msg}" if msg else "") + (f"({sug})" if sug else "")
    if status == 401 or code in (2, 1002):
        return _t(f"Tripo Key 无效或未授权(HTTP {status}{tail})", f"Invalid or unauthorized Tripo key (HTTP {status}{tail})")
    if code == 2010:
        return _t(f"Tripo 余额不足(code 2010),请到 Tripo 开放平台充值{tail}", f"Not enough Tripo credits (code 2010){tail}")
    if code == 2008:
        return _t(f"Tripo 内容审核未通过(code 2008){tail}", f"Rejected by Tripo content policy (code 2008){tail}")
    if code in (1007, 2000):
        return _t(f"Tripo 限流 / 并发已满(code {code}),稍后再试{tail}", f"Tripo rate / concurrency limit (code {code}); try again later{tail}")
    return f"Tripo HTTP {status} code={code}{tail}"


def _tripo_call(cfg: dict, method: str, path: str, body: dict | None = None,
                file: tuple[str, bytes, str] | None = None, timeout: float = 60) -> dict:
    url = TRIPO_SITES[cfg["site"]]["base"] + path
    headers = {"Authorization": f"Bearer {cfg['api_key']}", "Accept": "application/json"}
    data = None
    if file:
        boundary = uuid.uuid4().hex
        name, content, mime = file
        data = b"".join([(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
                          f"Content-Type: {mime}\r\n\r\n").encode(), content, f"\r\n--{boundary}--\r\n".encode()])
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    label = TRIPO_SITES[cfg["site"]]["label"]
    for attempt in range(5):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with _open(req, timeout) as r:
                resp = json.loads(r.read() or b"{}")
            break
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read() or b"{}")
            except ValueError:
                err = {}
            # 429 = 请求被拒、没建任务,可重试;5xx 时建任务的请求可能已成功(只重试查询,免得重复计费)
            if attempt < 4 and (e.code == 429 or (e.code >= 500 and method == "GET")):
                time.sleep(min(32, 2 ** (attempt + 1)))
                continue
            raise Model3DError(_tripo_err(e.code, err)) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            if attempt < 4 and method == "GET":
                time.sleep(min(32, 2 ** (attempt + 1)))
                continue
            why = getattr(e, "reason", e)
            raise Model3DError(_t(f"连接 {label} 失败:{why}", f"Could not reach {label}: {why}")) from None
    if resp.get("code") not in (0, None):
        raise Model3DError(_tripo_err(200, resp))
    return resp.get("data") or {}


def _tripo_model_url(output: dict) -> str:
    for k in ("model_url", "pbr_model", "model", "base_model"):
        v = output.get(k)
        if isinstance(v, dict):
            v = v.get("url")
        if isinstance(v, str) and v.startswith("http"):
            return v
    return ""


def _tripo_generate(cfg: dict, views: dict, texture: bool, notes: list) -> tuple[bytes, dict]:
    tokens = {}
    for v, p in views.items():
        d = _tripo_call(cfg, "POST", "/files", file=(p.name, p.read_bytes(), _mime(p)), timeout=180)
        if not d.get("file_token"):
            raise Model3DError(_t(f"Tripo 上传参考图失败:{json.dumps(d, ensure_ascii=False)[:200]}",
                                  f"Tripo image upload failed: {json.dumps(d)[:200]}"))
        tokens[v] = d["file_token"]
    opts = {"model": cfg["model"], "texture": bool(texture), "pbr": bool(texture)}   # pbr=true 会强开纹理,关纹理时两个都关
    if len(tokens) > 1:
        path, body = "/generation/multiview-to-model", {"inputs": [{v: tokens[v]} for v in VIEWS if v in tokens], **opts}
    else:
        path, body = "/generation/image-to-model", {"input": tokens["front"], **opts}
    tid = str(_tripo_call(cfg, "POST", path, body).get("task_id") or "")
    if not tid:
        raise Model3DError(_t("Tripo 建任务失败(没返回 task_id)", "Tripo did not return a task_id"))
    _log(f"Tripo 任务已创建 {tid}({path.rsplit('/', 1)[-1]} · {cfg['model']})")
    t0, last = time.time(), ""
    while True:
        time.sleep(POLL_S)
        task = _tripo_call(cfg, "GET", f"/tasks/{tid}")
        st = str(task.get("status") or "")
        if st != last:
            _log(f"Tripo {tid} {st or '?'} {task.get('progress') if task.get('progress') is not None else ''}"
                 f"(已等待 {int(time.time() - t0)}s)")
            last = st
        if st == "success":
            url = _tripo_model_url(task.get("output") or {})
            if not url:
                raise Model3DError(_t(f"Tripo 任务成功但没有模型地址:{json.dumps(task.get('output'), ensure_ascii=False)[:300]}",
                                      f"Tripo task succeeded without a model URL: {json.dumps(task.get('output'))[:300]}"))
            return _download(url, "Tripo"), {"endpoint": path.rsplit("/", 1)[-1], "task_id": tid,
                                              "credits": task.get("credits_consumed"), "views": list(tokens)}
        if st in ("failed", "cancelled", "banned", "expired"):
            why = task.get("error_message") or task.get("error_code") or ""
            raise Model3DError(_t(f"Tripo 任务 {tid} 失败({st}){':' + str(why) if why else ''}",
                                  f"Tripo task {tid} {st}{': ' + str(why) if why else ''}"))
        if time.time() - t0 > GEN_TIMEOUT:
            raise Model3DError(_t(f"Tripo 任务 {tid} 等待超时({GEN_TIMEOUT // 60} 分钟),可到 Tripo 控制台凭任务号取回",
                                  f"Tripo task {tid} timed out after {GEN_TIMEOUT // 60} minutes"))


# ---------------------------------------------------------------- Fal
_FAL_MAIN_KEYS = ("front_image_url", "input_image_url", "image_url")
_FAL_LIST_KEYS = ("image_urls", "input_image_urls")


def _fal_image_keys(props: dict) -> tuple[str, str]:
    main = next((k for k in _FAL_MAIN_KEYS if k in props), "")
    arr = next((k for k in _FAL_LIST_KEYS if k in props and "array" in (props[k].get("types") or [])), "")
    return main, arr


def _fal_candidates(model: str, n_views: int) -> list[str]:
    from modules import fal_models
    head, task = fal_models.split_endpoint(model, "model3d")
    if task:
        return [model]
    if n_views > 1:
        return [f"{head}/multiview-to-3d", f"{head}/multi-view-to-3d", f"{head}/multi-image-to-3d", f"{head}/image-to-3d", head]
    return [f"{head}/image-to-3d", head]


def fal_body(entry: dict, views: dict, texture: bool, to_url, notes: list) -> tuple[dict, list]:
    """按 Fal 端点参数表组请求体,返回 (body, 实际用上的视角)。
    具名槽位(input/front_image_url + back/left/right_image_url)按视角放;只有 image_urls 数组的按 [正, 左, 后, 右] 顺序放;
    纹理开关映射到该端点有的字段;输出格式字段一律要 glb。"""
    props = entry["input"]["props"]
    main, arr = _fal_image_keys(props)
    body: dict = {}
    named = {v: f"{v}_image_url" for v in ("left", "back", "right")}
    if main and (any(k in props for k in named.values()) or not arr):
        body[main] = to_url(views["front"])
        used = ["front"] + [v for v in ("left", "back", "right") if v in views and named[v] in props]
        for v in used[1:]:
            body[named[v]] = to_url(views[v])
    elif arr:
        used = [v for v in VIEWS if v in views]
        body[arr] = [to_url(views[v]) for v in used]
    else:
        raise Model3DError(_t(f"Fal 端点 {entry['endpoint_id']} 不收参考图", f"Fal endpoint {entry['endpoint_id']} takes no image"))

    def is_bool(k):
        return k in props and "boolean" in (props[k].get("types") or [])

    def enum(k):
        return [str(x) for x in (props.get(k, {}).get("enum") or [])]
    if texture:
        for k in ("textured_mesh", "enable_texture"):        # 这两个默认关
            if is_bool(k):
                body[k] = True
    else:
        for k in ("texture", "pbr", "enable_pbr", "should_texture", "enable_texture", "textured_mesh"):
            if is_bool(k):
                body[k] = False
        if "Geometry" in enum("generate_type"):
            body["generate_type"] = "Geometry"
        if "no" in enum("texture"):
            body["texture"] = "no"
        if "None" in enum("material"):
            body["material"] = "None"
        # enable_pbr 只管粗糙度 / 金属度贴图,关它不等于不出纹理;端点没有真正的纹理开关时如实说明
        if not any(k in body for k in ("texture", "pbr", "should_texture", "enable_texture", "textured_mesh",
                                       "generate_type", "material")):
            notes.append(_t(f"Fal 端点 {entry['endpoint_id']} 没有关纹理的选项,出的模型仍带贴图",
                            f"Fal endpoint {entry['endpoint_id']} has no option to turn textures off; the model still comes textured"))
    for k in ("geometry_file_format", "export_format"):
        if "glb" in enum(k):
            body[k] = "glb"
    return body, used


def _find_glb_url(res) -> str:
    """Fal 结果里找 GLB 文件:先认常见键(game_glb / model_glb / model_mesh / model_file),再全量找 content_type / 扩展名是 glb 的。"""
    if isinstance(res, dict):
        # game_glb 排最前:Idol 同时回 game_glb(带 UV 贴图的游戏级网格)与 model_glb(全分辨率顶点色网格),要前者
        for k in ("game_glb", "model_glb", "model_mesh", "model_file", "glb"):
            v = res.get(k)
            if isinstance(v, dict) and str(v.get("url") or "").split("?", 1)[0].lower().endswith(".glb"):
                return v["url"]
    stack = [res]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            url = str(cur.get("url") or "")
            if url and (cur.get("content_type") == "model/gltf-binary" or url.split("?", 1)[0].lower().endswith(".glb")):
                return url
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return ""


def _fal_generate(cfg: dict, views: dict, texture: bool, notes: list, output: str) -> tuple[bytes, dict]:
    from modules import fal_models, genmedia
    model = cfg["model"]
    views = dict(views)
    if model.startswith("tripo3d/") and len(views) > 1:
        # Fal 托管的 Tripo 多视图按 image_urls 位置认 [正, 左, 后, 右]:中间缺一个,后面的就对不上位置
        keep = {}
        for v in VIEWS:
            if v not in views:
                break
            keep[v] = views[v]
        if len(keep) < len(views):
            notes.append(_t(f"Fal 托管的 Tripo 多视图按「正、左、后、右」位置认图,缺{_view_names([v for v in VIEWS if v not in views][:1])}时"
                            f"{_view_names([v for v in views if v not in keep])}用不上,本次没传",
                            f"Fal-hosted Tripo multiview reads images by position [front, left, back, right]; with "
                            f"{_view_names([v for v in VIEWS if v not in views][:1])} missing, {_view_names([v for v in views if v not in keep])} could not be sent"))
            views = keep
    entry = None
    cands = _fal_candidates(model, len(views))
    try:
        for ep in cands:
            rec = fal_models.lookup(ep, api_key=cfg["api_key"])
            if rec and any(_fal_image_keys(rec["input"]["props"])):
                entry = rec
                break
    except fal_models.CatalogUnavailable as e:
        raise Model3DError(str(e)) from None
    if not entry:
        raise Model3DError(_t(f"Fal 上找不到 {model} 的图生 3D 端点(试过 {', '.join(cands)})",
                              f"No image-to-3D endpoint for {model} on Fal (tried {', '.join(cands)})"))
    to_url = lambda p: genmedia._file_to_data_url(str(p), IMAGE_MAX_BYTES)   # noqa: E731
    body, used = fal_body(entry, views, texture, to_url, notes)
    fal_models._check_required(body, entry)
    ep = entry["endpoint_id"]
    res = genmedia._fal_queue_run({"api_key": cfg["api_key"]}, ep, body, GEN_TIMEOUT, Path(output).name,
                                  kind="3D 模型", poll=POLL_S, output=output)
    url = _find_glb_url(res)
    if not url:
        raise Model3DError(_t(f"Fal 任务成功但结果里没有 GLB:{json.dumps(res, ensure_ascii=False)[:300]}",
                              f"Fal task succeeded but returned no GLB: {json.dumps(res)[:300]}"))
    data = genmedia._decode_data_url(url)
    genmedia._pending_task_clear(output)
    return data, {"endpoint": ep, "views": used}


# ---------------------------------------------------------------- RH 标准模型 API
def rh_body(entry: dict, urls: dict, texture: bool) -> dict:
    """按 RH 端点参数表组请求体。urls = {视角: 已上传的地址},正面必有。
    必填参数取文档默认值(演示素材地址除外);选填参数不传(用服务端默认,演示图默认值不会混进来)。"""
    from modules import rh_models
    schema = entry.get("schema") or {}
    props = schema.get("properties") or {}
    required = list(schema.get("required") or [])
    image_keys = {"imageUrl", "frontImageUrl", _RH_EXTRA_LIST, *_RH_VIEW_KEYS.values()}
    body: dict = {}
    for k in required:
        spec = props.get(k) or {}
        if k in image_keys:
            continue
        if "default" in spec and not rh_models._looks_like_url_default(spec["default"]):
            body[k] = spec["default"]
        elif spec.get("enum"):
            body[k] = spec["enum"][0]
    main = "frontImageUrl" if "frontImageUrl" in props else "imageUrl"
    body[main] = urls["front"]
    for v, k in _RH_VIEW_KEYS.items():
        if v in urls and k in props:
            body[k] = urls[v]
    if _RH_EXTRA_LIST in props:
        extra = [urls[v] for v in ("left", "back", "right") if v in urls]
        if extra:
            body[_RH_EXTRA_LIST] = extra
    if "inputMode" in props:
        body["inputMode"] = "views"

    def enum(k):
        return [str(x) for x in (props.get(k, {}).get("enum") or [])]
    for k in ("texture", "pbr", "enablePbr", "shouldTexture", "enableTexture"):
        if k in props and props[k].get("type") == "boolean" and not texture:
            body[k] = False
    if {"mesh", "both"} <= set(enum("requestType")):              # hitem3d:默认 mesh = 只出白模
        body["requestType"] = "both" if texture else "mesh"
    if not texture:
        g = next((x for x in enum("generateType") if x.lower() == "geometry"), "")
        if g:
            body["generateType"] = g
    missing = [k for k in required if k not in body]
    if missing:
        raise Model3DError(_t(f"RH 端点 {entry['id']} 的必填参数 {', '.join(missing)} 本次没有对应输入",
                              f"RH endpoint {entry['id']} needs {', '.join(missing)}, which this request cannot fill"))
    return body


def rh_views_for(entry: dict, views: dict) -> list:
    props = _rh_props(entry)
    used = ["front"] + [v for v in ("left", "back", "right") if v in views and _RH_VIEW_KEYS[v] in props]
    if _RH_EXTRA_LIST in props:
        used = ["front"] + [v for v in ("left", "back", "right") if v in views]
    return used


def _rh_pick(site: str, entry: dict, n_views: int) -> dict:
    """视角数与所选端点不符时换同系列端点:多视图但端点只收一张且没有视角槽位 → 换 multiview 端点;
    只有一张但端点是多视图 → 换图生端点(没有就报错)。"""
    from modules import rh_models
    props = _rh_props(entry)
    if n_views > 1 and entry.get("task") == "image-to-3d" and not any(k in props for k in (*_RH_VIEW_KEYS.values(), _RH_EXTRA_LIST)):
        cat = {m["id"]: m for m in rh_models.catalog(site)}
        return _rh_sibling(cat, entry["id"], "multiview-to-3d") or entry
    if n_views == 1 and entry.get("task") == "multiview-to-3d":
        cat = {m["id"]: m for m in rh_models.catalog(site)}
        alt = _rh_sibling(cat, entry["id"], "image-to-3d")
        if not alt:
            raise Model3DError(_t(f"RH 端点 {entry['id']} 是多视图生 3D,至少要正面加一个其它视角",
                                  f"RH endpoint {entry['id']} is multiview-to-3D and needs the front view plus at least one more"))
        return alt
    return entry


def _rh_glb_url(resp: dict) -> str:
    items = [r for r in (resp.get("results") or []) if isinstance(r, dict) and r.get("url")]
    for r in items:
        name = str(r["url"]).split("?", 1)[0].lower()
        if name.endswith(".glb") or str(r.get("outputType") or "").lower().lstrip(".") == "glb":
            return r["url"]
    kinds = ", ".join(sorted({str(r["url"]).split("?", 1)[0].rsplit(".", 1)[-1].lower() for r in items})) or "-"
    raise Model3DError(_t(f"RH 任务成功但没有 GLB 产物(产物类型:{kinds})", f"RH task succeeded without a GLB result (got: {kinds})"))


def _rh_generate(cfg: dict, views: dict, texture: bool, notes: list) -> tuple[bytes, dict]:
    from modules import rh_models
    site, key = cfg["site"], cfg["api_key"]
    try:
        entry = rh_models.find_entry(site, cfg["model"])
    except RuntimeError as e:
        raise Model3DError(str(e)) from None
    if entry.get("kind") != "model3d" and entry.get("task") not in rh_models.MODEL3D_TASKS:
        raise Model3DError(_t(f"RH 端点 {entry['id']} 不是 3D 生成端点", f"RH endpoint {entry['id']} is not a 3D generation endpoint"))
    if entry.get("task") == "text-to-3d":
        raise Model3DError(_t(f"RH 端点 {entry['id']} 是文生 3D,不收参考图;请选图生 3D 模型",
                              f"RH endpoint {entry['id']} is text-to-3D; pick an image-to-3D model"))
    entry = _rh_pick(site, entry, len(views))
    used = rh_views_for(entry, views)
    urls = {v: rh_models.upload(site, key, str(views[v])) for v in used}
    body = rh_body(entry, urls, texture)
    tid = rh_models.submit(site, key, entry["id"], body)
    _log(f"RH 3D 任务已创建 {tid}({entry['id']})")
    resp = rh_models.wait(site, key, tid, GEN_TIMEOUT, "3D")
    usage = rh_models.usage_note(resp)
    if usage:
        _log(f"RH 用量 {usage}")
    return rh_models.download(_rh_glb_url(resp)), {"endpoint": entry["id"], "task_id": tid, "views": used}


# ---------------------------------------------------------------- 本地 Hunyuan3D-2 api_server
def _h3d_generate(cfg: dict, views: dict, texture: bool, notes: list) -> tuple[bytes, dict]:
    steps = 5 if "turbo" in cfg["model"].lower() else 30      # Turbo 是步数蒸馏版;服务端默认 5 步,其余版本 5 步出不了形
    body = {"image": base64.b64encode(views["front"].read_bytes()).decode(), "texture": bool(texture),
            "type": "glb", "num_inference_steps": steps}
    req = urllib.request.Request(cfg["base_url"] + "/generate", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    _log(f"本地 Hunyuan3D {cfg['base_url']} 生成中({cfg['model']} · {steps} 步 · 纹理{'开' if texture else '关'})")
    try:
        with _open(req, GEN_TIMEOUT) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        raw = e.read() or b""
        if e.code == 404 and b"error_code" in raw:
            # api_server.py 出错一律回 404 + 固定文案,真正原因只在服务端日志里
            raise Model3DError(_t("本地 Hunyuan3D 服务生成失败(服务端没给原因,看服务端日志)"
                                  + (";勾选了「生成纹理」时服务端须带 --enable_tex 启动,否则必然失败" if texture else ""),
                                  "The local Hunyuan3D server failed (no reason given; check the server log)"
                                  + ("; with “Generate texture” on, the server must be started with --enable_tex" if texture else ""))) from None
        raise Model3DError(f"Hunyuan3D HTTP {e.code}:{raw[:200].decode('utf-8', 'replace')}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        why = getattr(e, "reason", e)
        raise Model3DError(_t(f"连不上本地 Hunyuan3D 服务 {cfg['base_url']}:{why}",
                              f"Could not reach the local Hunyuan3D server {cfg['base_url']}: {why}")) from None
    return data, {"endpoint": "/generate", "steps": steps, "views": ["front"]}


# ---------------------------------------------------------------- 入口
def generate(allcfg: dict, views: dict, output: str, *, provider: str = "", model: str = "",
             texture: bool | None = None) -> dict:
    """参考图(views = {front: 路径, left/back/right: 可选})→ GLB 写到 output,返回实际用的渠道 / 模型 / 端点 / 视角等。
    texture=None:本地 Hunyuan3D 按设置页「生成纹理」,云端渠道默认出带纹理的模型。"""
    unknown = [v for v in views if v not in VIEWS]
    if unknown:
        raise Model3DError(f"unknown view: {', '.join(unknown)}")
    if not views.get("front"):
        raise Model3DError(_t("缺正面参考图(正面必填)", "The front view image is required"))
    cfg = resolve(allcfg, provider, model)
    prov = cfg["provider"]
    texture = (cfg.get("texture", False) if prov == "hunyuan3d" else True) if texture is None else bool(texture)
    label = PROVIDER_LABELS[prov] if _env_zh() else ("Local Hunyuan3D" if prov == "hunyuan3d" else PROVIDER_LABELS[prov])
    notes: list[str] = []
    t0 = time.time()
    with tempfile.TemporaryDirectory(prefix="model3d-") as td:
        prepped = {v: _prep_image(views[v], Path(td), v) for v in VIEWS if views.get(v)}
        _log(f"3D 模型 · {PROVIDER_LABELS[prov]} · {cfg['model']} · 视角 {_view_names(prepped, True)} · 纹理{'开' if texture else '关'}")
        if prov == "tripo":
            data, meta = _tripo_generate(cfg, prepped, texture, notes)
        elif prov == "fal":
            data, meta = _fal_generate(cfg, prepped, texture, notes, output)
        elif prov == "rhapi":
            data, meta = _rh_generate(cfg, prepped, texture, notes)
        else:
            data, meta = _h3d_generate(cfg, prepped, texture, notes)
    check_glb(data, label)
    dropped = [v for v in prepped if v not in meta.get("views", [])]
    if dropped and not any(_view_names(dropped) in n for n in notes):
        notes.append(_t(f"所选模型只收{_view_names(meta.get('views') or ['front'])},{_view_names(dropped)}参考图没用上",
                        f"The selected model only takes {_view_names(meta.get('views') or ['front'])}; "
                        f"{_view_names(dropped)} not used"))
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(out.name + ".part")
    part.write_bytes(data)
    part.replace(out)
    for n in notes:
        _log(n)
    return {"provider": prov, "model": cfg["model"], "texture": texture, "notes": notes, "bytes": len(data),
            "seconds": round(time.time() - t0, 1), **meta}
