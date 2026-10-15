"""桌面端 Windows FFmpeg 安装包:钉版本文件、发版时代传到 S3 / OSS、索引按发行版给各自地址。"""
import hashlib
import importlib.util
import json
import subprocess
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "apps" / "desktop" / "scripts"
PIN = ROOT / "apps" / "desktop" / "ffmpeg-artifact.json"
spec = importlib.util.spec_from_file_location("mirror_ffmpeg", SCRIPTS / "mirror_ffmpeg.py")
mirror_ffmpeg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mirror_ffmpeg)

PACKAGE = b"pinned ffmpeg package bytes"


@pytest.fixture
def served(monkeypatch):
    """本机 HTTP 服务:routes 里有的路径 200,其余 404(S3 对缺失对象回 403,同样算没有)。"""
    routes: dict[str, bytes] = {}
    requests: list[tuple[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def _answer(self, with_body: bool) -> None:
            requests.append((self.command, self.path))
            body = routes.get(self.path)
            if body is None:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if with_body:
                self.wfile.write(body)

        def do_HEAD(self):  # noqa: N802
            self._answer(False)

        def do_GET(self):  # noqa: N802
            self._answer(True)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    # 本机可能开着系统代理,urllib 默认会把 127.0.0.1 的请求也发过去。
    monkeypatch.setattr(mirror_ffmpeg, "OPENER", urllib.request.build_opener(urllib.request.ProxyHandler({})))
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", routes, requests
    finally:
        server.shutdown()
        server.server_close()


def entry(origin: str, **overrides) -> dict:
    return {
        "platform": "win", "arch": "x64", "version": "9.9-essentials", "filename": "ffmpeg-9.9.zip",
        "sha256": hashlib.sha256(PACKAGE).hexdigest(), "size": len(PACKAGE),
        "sources": [f"{origin}/upstream/ffmpeg-9.9.zip"], **overrides,
    }


def test_pin_names_one_windows_package_with_https_sources():
    entries = mirror_ffmpeg.load_pin(PIN)
    assert [(e["platform"], e["arch"]) for e in entries] == [("win", "x64")]
    pinned = entries[0]
    assert all(source.startswith("https://") and source.endswith("/" + pinned["filename"])
               for source in pinned["sources"])


@pytest.mark.parametrize("change", [
    {"sha256": "abc"}, {"size": 0}, {"size": "114768076"}, {"filename": "../ffmpeg.zip"},
    {"filename": "ffmpeg.exe"}, {"version": "9 0"}, {"sources": []}, {"sources": ["http://example.com/f.zip"]},
])
def test_pin_with_a_malformed_field_is_rejected(tmp_path, change):
    document = json.loads(PIN.read_text(encoding="utf-8"))
    document["win"]["x64"].update(change)
    pin = tmp_path / "ffmpeg-artifact.json"
    pin.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(SystemExit):
        mirror_ffmpeg.load_pin(pin)


def test_index_points_each_distribution_at_its_own_mirror(tmp_path):
    """AGT(S3)与 SMT(OSS)两份索引:同一个包、同一个校验值,下载地址各走各的。"""
    artifacts = tmp_path / "runtime-artifacts" / "python-runtime-windows-x64"
    artifacts.mkdir(parents=True)
    (artifacts / "windows-python-1.2.3-x64.zip.metadata.json").write_text(json.dumps({
        "schema": 1, "version": "1.2.3", "pythonVersion": "3.12.13", "platform": "win", "arch": "x64",
        "filename": "windows-python-1.2.3-x64.zip", "sha256": "0" * 64, "size": 10,
    }), encoding="utf-8")
    pinned = mirror_ffmpeg.load_pin(PIN)[0]

    def build(name: str, *extra: str) -> dict:
        index = tmp_path / name / "metadata.json"
        subprocess.run(
            [sys.executable, str(SCRIPTS / "update_runtime_index.py"), "--index", str(index),
             "--artifacts", str(artifacts.parent), "--version", "1.2.3", *extra],
            check=True, capture_output=True, text=True,
        )
        return json.loads(index.read_text(encoding="utf-8"))

    bases = {
        "s3": "https://s3.agentics.world/packages/video-agents",
        "oss": "https://cdn.shumati.cn/packages/video-agents/",
    }
    for name, base in bases.items():
        document = build(name, "--public-base", base, "--ffmpeg-artifact", str(PIN))
        assert document["ffmpeg"] == {"win": {"x64": {
            "version": pinned["version"],
            "url": f"{base.rstrip('/')}/ffmpeg/{pinned['filename']}",
            "sha256": pinned["sha256"],
            "size": pinned["size"],
        }}}
        assert document["python"]["win"]["x64"]["url"] == f"{base.rstrip('/')}/python/windows-python-1.2.3-x64.zip"
    assert "ffmpeg" not in build("plain", "--public-base", bases["s3"])


def test_mirror_is_filled_only_when_it_does_not_serve_the_pinned_package(served):
    origin, routes, _ = served
    pinned = entry(origin)
    assert mirror_ffmpeg.mirror_has(f"{origin}/s3", pinned) is False
    routes["/s3/ffmpeg/ffmpeg-9.9.zip"] = PACKAGE
    assert mirror_ffmpeg.mirror_has(f"{origin}/s3/", pinned) is True
    # 同名但大小不对(传坏了、被别的文件占了)按没有处理,重新传。
    routes["/s3/ffmpeg/ffmpeg-9.9.zip"] = PACKAGE + b"!"
    assert mirror_ffmpeg.mirror_has(f"{origin}/s3", pinned) is False


def test_fetch_falls_back_to_the_next_source_and_verifies_the_bytes(served, tmp_path):
    origin, routes, _ = served
    routes["/tampered/ffmpeg-9.9.zip"] = PACKAGE[::-1]
    routes["/upstream/ffmpeg-9.9.zip"] = PACKAGE
    pinned = entry(origin, sources=[
        f"{origin}/gone/ffmpeg-9.9.zip", f"{origin}/tampered/ffmpeg-9.9.zip", f"{origin}/upstream/ffmpeg-9.9.zip",
    ])
    target = mirror_ffmpeg.fetch(pinned, tmp_path / "out")
    assert target.read_bytes() == PACKAGE
    assert [path.name for path in (tmp_path / "out").iterdir()] == ["ffmpeg-9.9.zip"]

    with pytest.raises(SystemExit, match="Could not fetch"):
        mirror_ffmpeg.fetch(entry(origin, sources=[f"{origin}/tampered/ffmpeg-9.9.zip"]), tmp_path / "bad")
    assert list((tmp_path / "bad").iterdir()) == []


def test_release_run_downloads_once_and_flags_only_the_empty_mirror(served, tmp_path, monkeypatch, capsys):
    origin, routes, requests = served
    routes["/s3/ffmpeg/ffmpeg-9.9.zip"] = PACKAGE
    routes["/upstream/ffmpeg-9.9.zip"] = PACKAGE
    monkeypatch.setattr(mirror_ffmpeg, "load_pin", lambda path: [entry(origin)])
    output = tmp_path / "ffmpeg-mirror"

    def run() -> str:
        monkeypatch.setattr(sys, "argv", [
            "mirror_ffmpeg.py", "--mirror", f"s3={origin}/s3", "--mirror", f"oss={origin}/oss",
            "--output", str(output),
        ])
        mirror_ffmpeg.main()
        return capsys.readouterr().out

    assert run() == "upload_s3=false\nupload_oss=true\n"
    assert (output / "ffmpeg-9.9.zip").read_bytes() == PACKAGE
    assert ("GET", "/upstream/ffmpeg-9.9.zip") in requests

    # 两边都有之后,发版不再碰上游。
    routes["/oss/ffmpeg/ffmpeg-9.9.zip"] = PACKAGE
    del routes["/upstream/ffmpeg-9.9.zip"]
    requests.clear()
    assert run() == "upload_s3=false\nupload_oss=false\n"
    assert all(method == "HEAD" for method, _ in requests)


def test_release_workflow_uploads_ffmpeg_before_the_index_that_points_at_it():
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "desktop.yaml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["publish"]["steps"]
    names = [step.get("name", "") for step in steps]
    by_name = {step.get("name", ""): step for step in steps}

    index = by_name["Build distribution metadata"]["run"]
    assert index.count("--ffmpeg-artifact apps/desktop/ffmpeg-artifact.json") == 2

    fetch = next(step for step in steps if step.get("id") == "ffmpeg")
    assert "--pin apps/desktop/ffmpeg-artifact.json" in fetch["run"]
    assert '--mirror s3="$S3_PUBLIC_BASE"' in fetch["run"] and '--mirror oss="$OSS_PUBLIC_BASE"' in fetch["run"]
    assert fetch["run"].rstrip().endswith('>> "$GITHUB_OUTPUT"')
    # 上游拉不到时,要在任何东西发布出去之前失败。
    assert names.index(fetch["name"]) < names.index("Publish GitHub release assets")

    for name, output, destination in (
        ("Publish production packages to S3", "upload_s3", "s3://agentics-prod/packages/video-agents/ffmpeg/"),
        ("Publish production packages to OSS", "upload_oss",
         "oss://${OSS_PACKAGES_BUCKET}/packages/video-agents/ffmpeg/"),
    ):
        step = by_name[name]
        assert step["env"]["UPLOAD_FFMPEG"] == "${{ steps.ffmpeg.outputs.%s }}" % output
        run = step["run"]
        assert run.index(destination) < run.index("metadata.json")
