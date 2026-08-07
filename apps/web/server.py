#!/usr/bin/env python3
"""Serve the original WebUI and proxy its versioned API to ``services.api``."""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask


ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(os.environ.get("VIDEOAGENTS_WEB_STATIC", Path(__file__).parent / "static")).resolve()
WEB_HOST = os.environ.get("VIDEOAGENTS_WEB_HOST", os.environ.get("VIDEOAGENTS_HOST", "127.0.0.1"))
WEB_PORT = int(os.environ.get("VIDEOAGENTS_WEB_PORT", os.environ.get("VIDEOAGENTS_PORT", "8630")))
# 手绘画布 LAN 侧车:控制台默认只绑 127.0.0.1 不暴露局域网,手机扫码画布单独起一个
# 0.0.0.0 监听,仅含 /draw/<token> 页、draw-sessions 两个 token 门控接口与静态资源。
# VIDEOAGENTS_DRAW_PORT=0 可禁用。
DRAW_HOST = os.environ.get("VIDEOAGENTS_DRAW_HOST", "0.0.0.0")
DRAW_PORT = int(os.environ.get("VIDEOAGENTS_DRAW_PORT", str(WEB_PORT + 1)))
API_PORT = int(os.environ.get("VIDEOAGENTS_API_PORT", "8640"))
API_ORIGIN = os.environ.get("VIDEOAGENTS_API_URL", f"http://127.0.0.1:{API_PORT}").rstrip("/")
START_API = os.environ.get("VIDEOAGENTS_START_API", "" if os.environ.get("VIDEOAGENTS_API_URL") else "1").lower() in {
    "1", "true", "yes", "on"
}
DATA_DIR = Path(os.environ.get("VIDEOAGENTS_DATA_DIR", ROOT / "data")).expanduser().resolve()
API_PROCESS: subprocess.Popen[bytes] | None = None
API_START_LOCK: asyncio.Lock | None = None
API_STOP_REQUESTED = False
DRAW_SERVER: uvicorn.Server | None = None
DRAW_THREAD: threading.Thread | None = None
UPSTREAMS: set[object] = set()
UPSTREAMS_LOCK = threading.Lock()

HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "content-length", "host",
}
PREVIEW_FILES = {
    "refs", "characters", "props", "scenes", "storyboard", "videos", "workflow", "worldview",
}

# 直连本机 API,绕过系统/环境代理(macOS 上 urllib 会自动读取系统代理设置)。
_DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _health() -> bool:
    try:
        with _DIRECT_OPENER.open(f"{API_ORIGIN}/api/v1/health", timeout=0.8) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def _api_env() -> dict[str, str]:
    return {
        **os.environ,
        "VIDEOAGENTS_HOST": "127.0.0.1",
        "VIDEOAGENTS_PORT": str(API_PORT),
        "VIDEOAGENTS_PUBLIC_PORT": str(DRAW_PORT or WEB_PORT),   # 手绘二维码 URL 用的端口
    }


def _api_lock() -> asyncio.Lock:
    global API_START_LOCK
    if API_START_LOCK is None:
        API_START_LOCK = asyncio.Lock()
    return API_START_LOCK


def _request_api_stop() -> None:
    """Ask the managed API child to shut down without blocking a signal handler."""
    global API_STOP_REQUESTED
    proc = API_PROCESS
    if proc and proc.poll() is None and not API_STOP_REQUESTED:
        API_STOP_REQUESTED = True
        proc.terminate()


async def _stop_api_process(timeout: float = 5) -> None:
    """Stop and reap the managed API process, with a bounded hard-stop fallback."""
    global API_PROCESS, API_STOP_REQUESTED
    proc = API_PROCESS
    if not proc:
        return
    if proc.poll() is None:
        _request_api_stop()
        try:
            await asyncio.to_thread(proc.wait, timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            await asyncio.to_thread(proc.wait)
    if API_PROCESS is proc:
        API_PROCESS = None
        API_STOP_REQUESTED = False


def _register_upstream(upstream: object) -> None:
    with UPSTREAMS_LOCK:
        UPSTREAMS.add(upstream)


def _close_upstream(upstream: object) -> None:
    with UPSTREAMS_LOCK:
        UPSTREAMS.discard(upstream)

    def _close() -> None:
        try:
            upstream.close()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - shutdown cleanup must be best-effort
            pass

    # close() 会阻塞在并发 read1() 后面,直到上游吐出下一个 chunk(SSE 空闲时是
    # 15s 心跳);而本函数会在事件循环线程上被调用(chunks() 的 finally),同步关闭
    # 等于把 8630 上所有请求冻结到心跳为止,故丢守护线程慢慢关。
    threading.Thread(target=_close, name="upstream-close", daemon=True).start()


def _begin_shutdown() -> None:
    """Stop producers so long-lived proxy streams reach EOF naturally."""
    if DRAW_SERVER is not None:
        DRAW_SERVER.should_exit = True
    # HTTPResponse.close() can block behind a concurrent read1().  Signal
    # handlers must never call it directly: stopping the managed API wakes its
    # SSE generators, which lets the proxy readers observe EOF and close safely.
    _request_api_stop()


class _WebServer(uvicorn.Server):
    """Notify managed services before Uvicorn begins draining connections."""

    def handle_exit(self, sig, frame) -> None:
        _begin_shutdown()
        super().handle_exit(sig, frame)


async def _ensure_api_ready() -> None:
    """Start or restart the local API service used by the Web proxy."""
    global API_PROCESS, API_STOP_REQUESTED
    if not START_API:
        return
    if API_PROCESS and API_PROCESS.poll() is None and await asyncio.to_thread(_health):
        return
    async with _api_lock():
        if API_PROCESS and API_PROCESS.poll() is None and await asyncio.to_thread(_health):
            return
        if API_PROCESS and API_PROCESS.poll() is None:
            await _stop_api_process()
        elif API_PROCESS and API_PROCESS.returncode is not None:
            print(f"VideoAgents API exited with code {API_PROCESS.returncode}; restarting", flush=True)
        popen_options = {"start_new_session": True} if os.name != "nt" else {}
        API_PROCESS = subprocess.Popen(
            [sys.executable, "-m", "services.api"], cwd=ROOT, env=_api_env(),
            stdout=None, stderr=None, **popen_options,
        )
        API_STOP_REQUESTED = False
        for _ in range(60):
            if await asyncio.to_thread(_health):
                return
            if API_PROCESS.poll() is not None:
                code = API_PROCESS.returncode
                API_PROCESS = None
                raise RuntimeError(f"API service exited with code {code}")
            await asyncio.sleep(0.5)
        API_PROCESS.terminate()
        raise RuntimeError(f"API service did not become ready at {API_ORIGIN}")


@asynccontextmanager
async def lifespan(_: FastAPI):
    await _ensure_api_ready()
    try:
        yield
    finally:
        _begin_shutdown()
        await _stop_api_process()


app = FastAPI(title="VideoAgents Web", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


@app.get("/api/v1/runtime")
async def runtime_info():
    return {
        "web": {"host": WEB_HOST, "port": WEB_PORT, "origin": f"http://{WEB_HOST}:{WEB_PORT}"},
        "api": {"port": API_PORT, "origin": API_ORIGIN},
    }


def _upstream_request(method: str, url: str, headers: dict[str, str], body: bytes):
    request = urllib.request.Request(url, data=body or None, headers=headers, method=method)
    try:
        return _DIRECT_OPENER.open(request, timeout=None)
    except urllib.error.HTTPError as error:
        return error


def _upstream_chunk(upstream, size: int = 64 * 1024) -> bytes:
    """Read one available HTTP chunk without waiting to fill ``size``.

    ``HTTPResponse.read(size)`` attempts to fill the requested buffer.  That
    stalls low-volume, long-lived SSE responses in the Web proxy until tens of
    kilobytes have accumulated.  ``read1`` performs at most one socket read,
    so each event/heartbeat can reach the browser immediately.
    """
    read1 = getattr(upstream, "read1", None)
    return read1(size) if read1 else upstream.read(size)


@app.api_route("/api/v1/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def proxy_api(path: str, request: Request):
    try:
        await _ensure_api_ready()
    except RuntimeError as error:
        return JSONResponse({"detail": f"API service unavailable: {error}"}, status_code=502)
    query = f"?{request.url.query}" if request.url.query else ""
    url = f"{API_ORIGIN}/api/v1/{path}{query}"
    body = await request.body()
    headers = {key: value for key, value in request.headers.items() if key.lower() not in HOP_HEADERS}
    headers["X-Forwarded-Host"] = request.headers.get("host", "")
    headers["X-Forwarded-Proto"] = request.url.scheme
    try:
        upstream = await asyncio.to_thread(_upstream_request, request.method, url, headers, body)
    except (OSError, urllib.error.URLError) as error:
        return JSONResponse({"detail": f"API service unavailable: {error}"}, status_code=502)
    _register_upstream(upstream)

    response_headers = {
        key: value for key, value in upstream.headers.items() if key.lower() not in HOP_HEADERS
    }

    async def chunks():
        try:
            while True:
                chunk = await asyncio.to_thread(_upstream_chunk, upstream)
                if not chunk:
                    break
                yield chunk
        finally:
            _close_upstream(upstream)

    return StreamingResponse(
        chunks(), status_code=upstream.status, headers=response_headers,
        background=BackgroundTask(_close_upstream, upstream),
    )


@app.post("/actions/open-project-folder", include_in_schema=False)
async def open_project_folder(request: Request):
    payload = await request.json()
    project = str(payload.get("project") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", project):
        raise HTTPException(400, "Invalid project name")
    folder = (DATA_DIR / "projects" / project).resolve()
    if folder.parent != (DATA_DIR / "projects").resolve() or not folder.is_dir():
        raise HTTPException(404, "Project directory not found")
    if sys.platform == "darwin":
        command = ["open", str(folder)]
    elif os.name == "nt":
        os.startfile(folder)  # type: ignore[attr-defined]
        return {"ok": True}
    else:
        command = ["xdg-open", str(folder)]
    subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"ok": True}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _page(name: str) -> FileResponse:
    path = STATIC_DIR / name
    if not path.is_file():
        raise HTTPException(404, "Page not found")
    return FileResponse(path, headers={"Cache-Control": "no-store"})


@app.get("/")
async def index():
    return _page("index.html")


@app.get("/models")
async def models():
    return _page("models.html")


@app.get("/storage")
async def storage():
    return _page("storage.html")


@app.get("/versions")
async def versions():
    return _page("versions.html")


@app.get("/clawbot")
async def clawbot():
    return _page("clawbot.html")


@app.get("/preview/{page}")
async def preview(page: str):
    if page not in PREVIEW_FILES:
        raise HTTPException(404, "Preview page not found")
    return _page(f"preview_{page}.html")


@app.get("/draw/{token}")
async def draw(token: str):
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        raise HTTPException(404, "Sketch session not found")
    return _page("draw.html")


draw_app = FastAPI(title="VideoAgents Draw", docs_url=None, redoc_url=None, openapi_url=None)


@draw_app.get("/draw/{token}")
async def draw_lan(token: str):
    return await draw(token)


@draw_app.api_route("/api/v1/draw-sessions/{token}", methods=["GET", "POST"])
async def draw_sessions_lan(token: str, request: Request):
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        raise HTTPException(404, "Sketch session not found")
    return await proxy_api(f"draw-sessions/{token}", request)


draw_app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def main() -> None:
    global DRAW_SERVER, DRAW_THREAD
    if not STATIC_DIR.is_dir():
        raise SystemExit(f"Static directory does not exist: {STATIC_DIR}")
    if DRAW_PORT:
        cfg = uvicorn.Config(
            draw_app, host=DRAW_HOST, port=DRAW_PORT, log_level="warning",
            timeout_graceful_shutdown=5,
        )
        DRAW_SERVER = uvicorn.Server(cfg)
        DRAW_THREAD = threading.Thread(
            target=DRAW_SERVER.run, daemon=True, name="draw-lan")
        DRAW_THREAD.start()
        print(f"VideoAgents Draw canvas (LAN) → http://{DRAW_HOST}:{DRAW_PORT}/draw/<token>")
    print(f"VideoAgents WebUI → http://{WEB_HOST}:{WEB_PORT} (API: {API_ORIGIN})")
    config = uvicorn.Config(
        app, host=WEB_HOST, port=WEB_PORT, log_level="info",
        timeout_graceful_shutdown=5,
    )
    try:
        _WebServer(config).run()
    except KeyboardInterrupt:
        # Uvicorn restores and re-raises the captured SIGINT after a graceful
        # drain; the command-line runner normally suppresses this traceback.
        pass
    finally:
        _begin_shutdown()
        if DRAW_THREAD and DRAW_THREAD.is_alive():
            DRAW_THREAD.join(timeout=5)


if __name__ == "__main__":
    main()
