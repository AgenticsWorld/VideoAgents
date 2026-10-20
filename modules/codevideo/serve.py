"""演示视频页面的本机静态服务(ES module 不能走 file://)。

两种用法:
- 渲染(render.py / checks.py):`FilmServer(film=<项目>/codevideo)`,临时端口,只服务这一部片,/@film/ = 片子目录;
- 代码预览页侧车(apps/web/server.py 起在 WEB_PORT+2):`FilmServer(projects=<data>/projects, preview=True)`,
  /@film/<项目>/ = 该项目的 codevideo/。预览模式给 HTML 注入 /@bridge.js(接父页面 postMessage 拖动/播放),
  并带 CSP:页面只能连自己这个源(connect-src 'self'),不能提交表单;与控制台不同源(端口不同),读不到控制台接口。

共用映射:/core/assets/polyhaven/… 先找宿主库再找按需下载的镜像;/core/… → modules/codevideo/lib/core/;
/node_modules/three/build/three.module.js、/node_modules/three/examples/jsm/… → lib/vendor/three/(页面写法与上游一致)。
只认 Host 为 localhost 或 IP 字面量(挡 DNS rebinding),只收 GET / HEAD,路径不能出各自的根目录。
"""
from __future__ import annotations

import mimetypes
import re
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import LIB_DIR, MIRROR, NS, VENDOR_THREE

TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
         ".json": "application/json; charset=utf-8", ".css": "text/css; charset=utf-8", ".txt": "text/plain; charset=utf-8",
         ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
         ".gif": "image/gif", ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf", ".otf": "font/otf",
         ".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".ogg": "audio/ogg", ".mp4": "video/mp4",
         ".hdr": "application/octet-stream", ".bin": "application/octet-stream", ".gltf": "model/gltf+json",
         ".glb": "model/gltf-binary", ".wasm": "application/wasm", ".map": "application/json"}
FILM = "/@film/"
BRIDGE = "/@bridge.js"
PREVIEW_CSP = ("default-src 'self' blob: data:; script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:; "
               "style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; media-src 'self' blob: data:; "
               "font-src 'self' blob: data:; connect-src 'self' blob: data:; worker-src 'self' blob:; "
               "form-action 'none'; base-uri 'self'")
_PROJECT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]*$")

# 预览桥:只回应父页面的 {__cv:1,type:'seek',t,seq,texts?};把 READY / 报错 / 每次渲染耗时报回父页面
BRIDGE_JS = r"""(()=>{if(window.parent===window)return;
const send=m=>window.parent.postMessage(Object.assign({__cv:1},m),'*');
addEventListener('error',e=>send({type:'error',message:String(e.message||e.error||e)}));
addEventListener('unhandledrejection',e=>send({type:'error',message:String((e.reason&&e.reason.message)||e.reason)}));
let ready=false;const t0=performance.now();
const poll=setInterval(()=>{if(window.READY===true&&typeof window.render==='function'){clearInterval(poll);ready=true;
send({type:'ready',dur:window.DUR,w:innerWidth,h:innerHeight,texts:typeof window.TEXTS==='function',ev:Array.isArray(window.EV)?window.EV.length:0,ms:Math.round(performance.now()-t0)})}
else if(performance.now()-t0>180000){clearInterval(poll);send({type:'error',message:'window.READY never became true (180 s)'})}},100);
addEventListener('message',e=>{const d=e.data;if(!d||!d.__cv||!ready||d.type!=='seek')return;const s=performance.now();
try{window.render(+d.t)}catch(err){send({type:'error',message:String((err&&err.stack)||err)});return}
let texts;if(d.texts&&typeof window.TEXTS==='function'){try{texts=window.TEXTS(+d.t)}catch(err){}}
send({type:'rendered',t:+d.t,seq:d.seq,ms:Math.round(performance.now()-s),texts})});})();
"""


_FETCH_LOCK = threading.Lock()


def _fetch_asset(rel: str) -> bool:
    """页面要的 /core/assets/… 不在本机:属于 HDRI 包或 polyhaven 模型清单就当场下载(免费、十几 MB)。返回是否下载过。"""
    from . import CodeVideoError
    from . import fetch as F
    with _FETCH_LOCK:
        try:
            if rel.lower().endswith(".hdr"):
                return not F.fetch_pack("hdri").get("skipped")
            if any(r["path"] == rel for r in F.file_set("assets:polyhaven")):
                return F.fetch_files("assets:polyhaven")["downloaded"] > 0
        except CodeVideoError as e:
            import sys
            print(f"[codevideo] 自动下载 {rel} 失败:{e}", file=sys.stderr)
    return False


def _inside(base: Path, rel: str) -> Path | None:
    p = (base / rel.lstrip("/")).resolve()
    b = base.resolve()
    return p if p == b or b in p.parents else None


class FilmServer:
    def __init__(self, film: Path | None = None, projects: Path | None = None, preview: bool = False,
                 host: str = "127.0.0.1", port: int = 0, extra_hosts: tuple[str, ...] = ()):
        if (film is None) == (projects is None):
            raise ValueError("FilmServer needs exactly one of film= / projects=")
        self.film, self.projects, self.preview = film, projects, preview
        self.hosts = {"127.0.0.1", "localhost", *extra_hosts}
        handler = self._handler()
        self.httpd = ThreadingHTTPServer((host, port), handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread: threading.Thread | None = None

    # ---- 路径映射 ----
    def resolve(self, path: str) -> Path | None:
        if path.startswith("/core/assets/"):
            rel = path[len("/core/"):]
            for attempt in (0, 1):
                for base in (LIB_DIR / "core", MIRROR / "core"):
                    p = _inside(base, rel)
                    if p and p.is_file():
                        return p
                if attempt == 0 and not _fetch_asset("core/" + rel):
                    break
            return _inside(MIRROR / "core", rel)      # 仍没有 → 404
        if path.startswith("/core/"):
            return _inside(LIB_DIR / "core", path[len("/core/"):])
        if path == "/node_modules/three/build/three.module.js":
            return VENDOR_THREE / "three.module.js"
        if path.startswith("/node_modules/three/examples/jsm/"):
            return _inside(VENDOR_THREE / "jsm", path[len("/node_modules/three/examples/jsm/"):])
        if path.startswith("/styles/"):   # 按需下载的样片(只读参考,预览服务不开放)
            return None if self.preview else _inside(MIRROR / "styles", path[len("/styles/"):])
        if path.startswith(FILM):
            rest = path[len(FILM):]
            if self.film is not None:
                return _inside(self.film, rest)
            proj, _, sub = rest.partition("/")
            if not _PROJECT_RE.fullmatch(proj):
                return None
            return _inside(self.projects / proj / NS, sub)
        return None

    def url(self, rel: str = "index.html", project: str | None = None) -> str:
        mid = f"{urllib.parse.quote(project)}/" if project else ""
        return f"http://127.0.0.1:{self.port}{FILM}{mid}{rel}"

    def _handler(self):
        server = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # 静默
                pass

            def _deny(self, code: int, msg: str):
                body = msg.encode()
                self.send_response(code)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def do_HEAD(self):
                self.do_GET()

            def do_GET(self):
                if not _host_ok(self.headers.get("Host") or "", server.hosts):
                    return self._deny(403, "bad host")
                try:
                    path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
                except Exception:  # noqa: BLE001
                    return self._deny(400, "bad request")
                if "\0" in path:
                    return self._deny(400, "bad request")
                if server.preview and path == BRIDGE:
                    return self._send(BRIDGE_JS.encode(), TYPES[".js"])
                p = server.resolve(path)
                if p is None:
                    return self._deny(403 if path.startswith((FILM, "/core/", "/node_modules/")) else 404, "outside root")
                if not p.is_file():
                    return self._deny(404, "not found")
                data = p.read_bytes()
                ctype = TYPES.get(p.suffix.lower()) or mimetypes.guess_type(p.name)[0] or "application/octet-stream"
                if server.preview and p.suffix.lower() == ".html":
                    data = _inject_bridge(data)
                self._send(data, ctype)

            def _send(self, data: bytes, ctype: str):
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                if server.preview:
                    self.send_header("Content-Security-Policy", PREVIEW_CSP)
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Cross-Origin-Resource-Policy", "cross-origin")   # 控制台页面播放 out/mix.wav
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(data)

            def do_POST(self):
                self._deny(405, "read only")

            do_PUT = do_DELETE = do_PATCH = do_POST

        return H

    def start(self) -> "FilmServer":
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True, name="codevideo-serve")
        self.thread.start()
        return self

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *a):
        self.stop()


def _host_ok(header: str, names: set[str]) -> bool:
    """只认 localhost 与 IP 字面量(挡 DNS rebinding:重绑定靠域名)。"""
    import ipaddress
    h = header.strip()
    host = h[1:h.index("]")] if h.startswith("[") and "]" in h else h.rsplit(":", 1)[0] if h.count(":") == 1 else h
    if host in names:
        return True
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _inject_bridge(html: bytes) -> bytes:
    tag = b'<script src="' + BRIDGE.encode() + b'"></script>'
    m = re.search(rb"<head[^>]*>", html, re.I)
    if m:
        return html[:m.end()] + tag + html[m.end():]
    return tag + html
