"""网络检测:回答「VPN/代理在命令行环境里生效了吗」。

很多 VPN 只接管系统代理(浏览器走),terminal 里的 curl/git/pip 仍然直连。
本模块在后端进程里探测:默认完全跟随进程网络环境(等同在 terminal 直接执行
curl,不加任何代理参数);API 调用方也可显式指定本地代理端口测试。探测优先
用 curl 子进程——它就是 terminal 的真实工具,自带分段计时,且支持 socks5h
代理(urllib 原生不支持 SOCKS);无 curl 时回退 urllib。纯标准库,无新依赖。

对外三个入口(均为阻塞函数,调用方用 asyncio.to_thread 包装):
  api_proxy()  代理检测:系统代理 + 进程 env + 常见端口扫描,附终端 export 命令
  api_probe()  单站点单通道连通性探测(站点写死,不接受任意 URL,避免 SSRF)
  api_ip()     出口 IP + 归属地(ip-api.com,失败回退 ipinfo.io)
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

PROBE_TIMEOUT = 8

# 探测站点写死(前端只传 key):baidu 是国内基线,基线通而其余直连不通 = 被墙非断网。
# strict=True 的目标必须拿到 2xx 才算通(默认口径 2xx-4xx 都算连通):claude 实测的是
# Claude Code 安装命令 `curl -fsSL https://claude.ai/install.sh | bash` 会不会被拒——
# 用 HEAD 请求(head=True)只取响应头不下载脚本正文,Cloudflare 403 盾页/地区拒绝都判不通。
TARGETS: dict[str, dict[str, Any]] = {
    "baidu": {"label": "baidu.com", "url": "https://www.baidu.com/", "baseline": True},
    "google": {"label": "google.com", "url": "https://www.google.com/generate_204"},
    "youtube": {"label": "www.youtube.com", "url": "https://www.youtube.com/generate_204"},
    "openai": {"label": "openai.com", "url": "https://openai.com/"},
    "claude": {"label": "claude.ai/install.sh", "url": "https://claude.ai/install.sh",
               "strict": True, "head": True},
}

# 常见本地代理端口(Clash/ClashX 7890/7897、V2Ray 1087/1086/10808/10809、
# Surge 6152/6153、Privoxy 8118、Astrill 3128、通用 1080/8888、sing-box 20170/20171)
_COMMON_HTTP_PORTS = [7890, 7897, 1087, 6152, 8118, 3128, 8888, 20171, 10809]
_COMMON_SOCKS_PORTS = [7891, 1080, 1086, 6153, 20170, 10808]
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
_ENV_VARS = ("https_proxy", "http_proxy", "all_proxy", "no_proxy")

_CURL_ERRORS = {
    5: "无法解析代理地址",
    6: "DNS 解析失败",
    7: "连接被拒绝",
    28: "超时",
    35: "TLS 握手失败",
    52: "服务器空响应",
    56: "连接被重置",
    60: "证书校验失败",
}


def _curl_bin() -> str | None:
    return shutil.which("curl")


def _proxy_ok(proxy: str) -> bool:
    """代理参数来自请求体,仅放行本机地址,防止把后端当跳板。"""
    try:
        parts = urllib.parse.urlsplit(proxy)
    except ValueError:
        return False
    return (parts.scheme in {"http", "https", "socks5", "socks5h"}
            and (parts.hostname or "") in _LOOPBACK_HOSTS
            and parts.port is not None and 0 < parts.port < 65536)


# ---------- 代理检测 ----------

def _system_proxies() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        if sys.platform == "darwin":
            r = subprocess.run(["scutil", "--proxy"], capture_output=True, text=True, timeout=5)
            kv = dict(re.findall(r"(\w+)\s*:\s*(\S+)", r.stdout))
            for prefix, scheme in (("HTTPS", "http"), ("HTTP", "http"), ("SOCKS", "socks5")):
                if (kv.get(f"{prefix}Enable") == "1" and kv.get(f"{prefix}Proxy")
                        and (kv.get(f"{prefix}Port") or "").isdigit()):
                    out.append({"scheme": scheme, "host": kv[f"{prefix}Proxy"],
                                "port": int(kv[f"{prefix}Port"]), "source": "system"})
        elif os.name == "nt":
            r = subprocess.run(
                ["reg", "query",
                 r"HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings"],
                capture_output=True, text=True, timeout=5)
            if re.search(r"ProxyEnable\s+REG_DWORD\s+0x1", r.stdout):
                m = re.search(r"ProxyServer\s+REG_SZ\s+(\S+)", r.stdout)
                if m:
                    # 值可能是 "host:port" 或 "http=h:p;https=h:p;socks=h:p"
                    for item in m.group(1).split(";"):
                        scheme, _, addr = item.rpartition("=")
                        host, _, port = addr.rpartition(":")
                        if host and port.isdigit():
                            out.append({"scheme": "socks5" if scheme == "socks" else "http",
                                        "host": host, "port": int(port), "source": "system"})
    except Exception:  # noqa: BLE001  # 系统命令不可用就跳过,靠其他来源
        pass
    return out


def _env_proxies() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for var in ("https_proxy", "http_proxy", "all_proxy"):
        value = os.environ.get(var) or os.environ.get(var.upper()) or ""
        try:
            parts = urllib.parse.urlsplit(value)
        except ValueError:
            continue
        if parts.hostname and parts.port:
            out.append({"scheme": "socks5" if parts.scheme.startswith("socks") else "http",
                        "host": parts.hostname, "port": parts.port, "source": "env"})
    return out


def _scan_proxies() -> list[dict[str, Any]]:
    ports = [(p, "http") for p in _COMMON_HTTP_PORTS] + [(p, "socks5") for p in _COMMON_SOCKS_PORTS]

    def check(item: tuple[int, str]) -> dict[str, Any] | None:
        port, scheme = item
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return {"scheme": scheme, "host": "127.0.0.1", "port": port, "source": "scan"}
        except OSError:
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(ports)) as pool:
        return [c for c in pool.map(check, ports) if c]


def detect_proxies() -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for cand in _system_proxies() + _env_proxies() + _scan_proxies():
        key = (cand["host"], cand["port"])
        if key not in seen:
            seen.add(key)
            candidates.append(cand)

    def url_of(scheme: str) -> str | None:
        for cand in candidates:
            if cand["scheme"] == scheme:
                return f"{scheme}://{cand['host']}:{cand['port']}"
        return None

    http_url, socks_url = url_of("http"), url_of("socks5")
    env = {var: value for var in _ENV_VARS
           for name in (var, var.upper()) if (value := os.environ.get(name))}
    return {
        "platform": sys.platform,
        "curl": bool(_curl_bin()),
        "candidates": candidates,
        "recommended": http_url or socks_url,
        "env": env,
        "command": _shell_command(http_url, socks_url),
    }


def _shell_command(http_url: str | None, socks_url: str | None) -> str | None:
    proxy_url = http_url or socks_url
    if not proxy_url:
        return None
    if os.name == "nt":
        cmd = f'$env:HTTPS_PROXY="{proxy_url}"; $env:HTTP_PROXY="{proxy_url}"'
        if socks_url:
            cmd += f'; $env:ALL_PROXY="{socks_url}"'
        return cmd
    cmd = f"export https_proxy={proxy_url} http_proxy={proxy_url}"
    if socks_url:
        cmd += f" all_proxy={socks_url}"
    return cmd


# ---------- 连通性探测 ----------

def _curl_proxy_arg(proxy: str) -> str:
    # socks5→socks5h:让代理端做远程 DNS,避免本地 DNS 污染得出假阴性
    return re.sub(r"^socks5://", "socks5h://", proxy)


def _probe_curl(url: str, proxy: str | None, timeout: int, strict: bool, head: bool) -> dict[str, Any]:
    cmd = [_curl_bin() or "curl", "-sS", "-o", os.devnull, "-L", "--max-redirs", "5",
           "--max-time", str(timeout),
           "-w", r"%{http_code} %{time_connect} %{time_appconnect} %{time_total}"]
    if head:
        cmd.append("-I")
    if proxy:  # 不传 proxy 则完全跟随进程环境,等同 terminal 直接执行 curl
        cmd += ["-x", _curl_proxy_arg(proxy)]
    cmd.append(url)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "超时"}
    if r.returncode != 0:
        detail = _CURL_ERRORS.get(r.returncode) or (r.stderr.strip().splitlines() or [f"curl 退出码 {r.returncode}"])[0]
        return {"ok": False, "error": detail}
    try:
        status_s, connect_s, tls_s, total_s = r.stdout.strip().split()[-4:]
        status = int(status_s)
    except ValueError:
        return {"ok": False, "error": f"curl 输出异常:{r.stdout.strip()[:80]}"}
    result = {"ok": 200 <= status < (300 if strict else 500), "status": status,
              "connect_ms": round(float(connect_s) * 1000),
              "tls_ms": round(float(tls_s) * 1000),
              "total_ms": round(float(total_s) * 1000)}
    if not result["ok"]:
        result["error"] = f"HTTP {status}"
    return result


def _probe_urllib(url: str, proxy: str | None, timeout: int, strict: bool, head: bool) -> dict[str, Any]:
    if proxy and proxy.startswith("socks"):
        return {"ok": False, "error": "未安装 curl,无法测试 SOCKS 代理"}
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy else []
    opener = urllib.request.build_opener(*handlers)   # 不传 proxy 则跟随环境默认代理行为
    req = urllib.request.Request(url, headers={"User-Agent": "VideoAgents-netcheck"},
                                 method="HEAD" if head else "GET")
    start = time.perf_counter()
    try:
        with opener.open(req, timeout=timeout) as resp:
            status = resp.status
    except urllib.error.HTTPError as error:
        status = error.code
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "error": str(error)[:200] or type(error).__name__}
    total_ms = round((time.perf_counter() - start) * 1000)
    result = {"ok": 200 <= status < (300 if strict else 500), "status": status, "total_ms": total_ms}
    if not result["ok"]:
        result["error"] = f"HTTP {status}"
    return result


def probe_url(url: str, proxy: str | None = None, timeout: int = PROBE_TIMEOUT,
              strict: bool = False, head: bool = False) -> dict[str, Any]:
    if _curl_bin():
        return _probe_curl(url, proxy, timeout, strict, head)
    return _probe_urllib(url, proxy, timeout, strict, head)


# ---------- 出口 IP + 归属地 ----------

# ip-api.com 免费版仅 http 且自带中文归属地;ipinfo.io 作 https 备胎
_IP_APIS = [
    "http://ip-api.com/json/?fields=status,query,country,regionName,city,isp&lang=zh-CN",
    "https://ipinfo.io/json",
]


def _fetch_text(url: str, proxy: str | None, timeout: int) -> str:
    if _curl_bin():
        cmd = ["curl", "-sS", "--max-time", str(timeout)]
        if proxy:
            cmd += ["-x", _curl_proxy_arg(proxy)]
        cmd.append(url)
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
        if r.returncode != 0:
            raise RuntimeError(_CURL_ERRORS.get(r.returncode)
                               or (r.stderr.strip().splitlines() or [f"curl 退出码 {r.returncode}"])[0])
        return r.stdout
    if proxy and proxy.startswith("socks"):
        raise RuntimeError("未安装 curl,无法测试 SOCKS 代理")
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy else []
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers={"User-Agent": "VideoAgents-netcheck"})
    with opener.open(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def query_ip(proxy: str | None = None, timeout: int = PROBE_TIMEOUT) -> dict[str, Any]:
    errors: list[str] = []
    for api_url in _IP_APIS:
        try:
            data = json.loads(_fetch_text(api_url, proxy, timeout))
        except Exception as error:  # noqa: BLE001
            errors.append(str(error)[:120])
            continue
        if "ip-api.com" in api_url:
            if data.get("status") != "success":
                errors.append(f"ip-api: {data.get('message', 'failed')}")
                continue
            ip, isp = data.get("query"), data.get("isp")
            location = " ".join(filter(None, (data.get("country"), data.get("regionName"), data.get("city"))))
        else:
            ip, isp = data.get("ip"), data.get("org")
            location = " ".join(filter(None, (data.get("country"), data.get("region"), data.get("city"))))
        if ip:
            return {"ok": True, "ip": ip, "location": location, "isp": isp or ""}
    return {"ok": False, "error": "; ".join(errors) or "查询失败"}


# ---------- API 入口(app.py 经 asyncio.to_thread 调用) ----------

def _resolve_proxy(body: dict[str, Any]) -> tuple[str | None, str | None]:
    """返回 (proxy, error):默认(不传 channel)不指定代理、跟随进程环境。"""
    if (body.get("channel") or "direct") != "proxy":
        return None, None
    proxy = str(body.get("proxy") or "") or (detect_proxies().get("recommended") or "")
    if not proxy:
        return None, "未检测到代理"
    if not _proxy_ok(proxy):
        return None, "代理地址仅允许本机(127.0.0.1/localhost)"
    return proxy, None


def api_proxy() -> dict[str, Any]:
    return {"ok": True, **detect_proxies()}


def api_probe(body: dict[str, Any]) -> dict[str, Any]:
    target = TARGETS.get(str(body.get("target") or ""))
    if not target:
        return {"ok": False, "error": f"未知目标:{body.get('target')}"}
    proxy, error = _resolve_proxy(body)
    if error:
        return {"ok": False, "error": error}
    return probe_url(target["url"], proxy,
                     strict=bool(target.get("strict")), head=bool(target.get("head")))


def api_ip(body: dict[str, Any]) -> dict[str, Any]:
    proxy, error = _resolve_proxy(body)
    if error:
        return {"ok": False, "error": error}
    return query_ip(proxy)
