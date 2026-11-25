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

本模块只做设置页的连接验证;按配置出 3D 模型的生成命令尚未接入。
"""
from __future__ import annotations

import ipaddress
import json
import urllib.error
import urllib.parse
import urllib.request

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
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        raise Model3DError("服务地址未填写" if zh else "Server address is empty")
    if "://" not in base:       # 「192.168.x.x:8080」这类不带协议的地址(urlsplit 会把 IP 当成 scheme)
        base = "http://" + base
    if urllib.parse.urlsplit(base).scheme not in ("http", "https"):
        raise Model3DError("服务地址须以 http:// 或 https:// 开头" if zh
                           else "Server address must start with http:// or https://")
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
