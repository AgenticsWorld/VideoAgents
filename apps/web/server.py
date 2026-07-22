#!/usr/bin/env python3
"""Serve the original WebUI and proxy its versioned API to ``services.api``."""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
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
API_PORT = int(os.environ.get("VIDEOAGENTS_API_PORT", "8640"))
API_ORIGIN = os.environ.get("VIDEOAGENTS_API_URL", f"http://127.0.0.1:{API_PORT}").rstrip("/")
START_API = os.environ.get("VIDEOAGENTS_START_API", "" if os.environ.get("VIDEOAGENTS_API_URL") else "1").lower() in {
    "1", "true", "yes", "on"
}
DATA_DIR = Path(os.environ.get("VIDEOAGENTS_DATA_DIR", ROOT / "data")).expanduser().resolve()
API_PROCESS: subprocess.Popen[bytes] | None = None
API_START_LOCK: asyncio.Lock | None = None

HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "content-length", "host",
}
PREVIEW_FILES = {
    "refs", "characters", "props", "scenes", "storyboard", "videos", "workflow", "worldview",
}


def _health() -> bool:
    try:
        with urllib.request.urlopen(f"{API_ORIGIN}/api/v1/health", timeout=0.8) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def _api_env() -> dict[str, str]:
    return {
        **os.environ,
        "VIDEOAGENTS_HOST": "127.0.0.1",
        "VIDEOAGENTS_PORT": str(API_PORT),
        "VIDEOAGENTS_PUBLIC_PORT": str(WEB_PORT),
    }


def _api_lock() -> asyncio.Lock:
    global API_START_LOCK
    if API_START_LOCK is None:
        API_START_LOCK = asyncio.Lock()
    return API_START_LOCK


async def _ensure_api_ready() -> None:
    """Start or restart the local API service used by the Web proxy."""
    global API_PROCESS
    if not START_API:
        return
    if API_PROCESS and API_PROCESS.poll() is None and await asyncio.to_thread(_health):
        return
    async with _api_lock():
        if API_PROCESS and API_PROCESS.poll() is None and await asyncio.to_thread(_health):
            return
        if API_PROCESS and API_PROCESS.poll() is None:
            API_PROCESS.terminate()
            try:
                await asyncio.to_thread(API_PROCESS.wait, 5)
            except subprocess.TimeoutExpired:
                API_PROCESS.kill()
        elif API_PROCESS and API_PROCESS.returncode is not None:
            print(f"VideoAgents API exited with code {API_PROCESS.returncode}; restarting", flush=True)
        API_PROCESS = subprocess.Popen(
            [sys.executable, "-m", "services.api"], cwd=ROOT, env=_api_env(),
            stdout=None, stderr=None,
        )
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
        global API_PROCESS
        if API_PROCESS and API_PROCESS.poll() is None:
            API_PROCESS.terminate()
            try:
                await asyncio.to_thread(API_PROCESS.wait, 5)
            except subprocess.TimeoutExpired:
                API_PROCESS.kill()


app = FastAPI(title="VideoAgents Web", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


def _upstream_request(method: str, url: str, headers: dict[str, str], body: bytes):
    request = urllib.request.Request(url, data=body or None, headers=headers, method=method)
    try:
        return urllib.request.urlopen(request, timeout=None)
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

    response_headers = {
        key: value for key, value in upstream.headers.items() if key.lower() not in HOP_HEADERS
    }

    async def chunks():
        while True:
            chunk = await asyncio.to_thread(_upstream_chunk, upstream)
            if not chunk:
                break
            yield chunk

    return StreamingResponse(
        chunks(), status_code=upstream.status, headers=response_headers,
        background=BackgroundTask(upstream.close),
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


def main() -> None:
    if not STATIC_DIR.is_dir():
        raise SystemExit(f"Static directory does not exist: {STATIC_DIR}")
    print(f"VideoAgents WebUI → http://{WEB_HOST}:{WEB_PORT} (API: {API_ORIGIN})")
    uvicorn.run(app, host=WEB_HOST, port=WEB_PORT, log_level="info")


if __name__ == "__main__":
    main()
