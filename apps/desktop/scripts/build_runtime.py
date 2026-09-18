#!/usr/bin/env python3
"""Build a relocatable CPython runtime for the current desktop platform."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import platform
import shutil
import subprocess
import sys
from pathlib import Path


DESKTOP = Path(__file__).resolve().parents[1]
ROOT = DESKTOP.parents[1]
LOCK = DESKTOP / "runtime-requirements.lock"
DEFAULT_OUTPUT = DESKTOP / ".runtime"
PYTHON_REQUEST = "3.12.13"
SCHEMA = 1
BROWSER_DIR = "playwright-browsers"
BROWSER_READY = f"{BROWSER_DIR}/.chromium-ready"


def uv_executable() -> str:
    configured = os.environ.get("UV")
    candidates = [
        configured,
        shutil.which("uv"),
        ROOT / ".venv" / ("Scripts/uv.exe" if os.name == "nt" else "bin/uv"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    raise SystemExit("uv is required. Run `make desktop-install` first.")


def run(*command: str, env: dict[str, str] | None = None) -> str:
    print("+", " ".join(command), flush=True)
    result = subprocess.run(command, check=True, text=True, env=env, stdout=subprocess.PIPE)
    return result.stdout.strip()


def platform_id() -> str:
    return {"Darwin": "darwin", "Windows": "win32", "Linux": "linux"}[platform.system()]


def arch_id() -> str:
    machine = platform.machine().lower()
    return {"aarch64": "arm64", "arm64": "arm64", "amd64": "x64", "x86_64": "x64"}.get(machine, machine)


def find_python(directory: Path) -> Path:
    names = ["python.exe"] if os.name == "nt" else ["python3", "python"]
    matches: list[Path] = []
    for name in names:
        matches.extend(directory.glob(f"cpython-*/**/{name}"))
    matches = [path for path in matches if path.is_file() and (os.name == "nt" or os.access(path, os.X_OK))]
    if not matches:
        raise SystemExit(f"Managed Python executable not found under {directory}")
    return min(matches, key=lambda path: len(path.parts))


def is_junction(path: Path) -> bool:
    """Return whether *path* is a Windows directory junction.

    pathlib only exposes is_junction on Python 3.12+ and Windows.  uv uses a
    junction for its major/minor CPython alias there, while Unix uses a symlink.
    """
    check = getattr(path, "is_junction", None)
    if check and check():
        return True
    try:
        return bool(path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except (AttributeError, OSError):
        return False


def remove_python_aliases(python_store: Path) -> None:
    """Remove uv's absolute top-level aliases before relocating the runtime."""
    for candidate in python_store.iterdir():
        if is_junction(candidate):
            candidate.rmdir()
        elif candidate.is_symlink():
            candidate.unlink()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--version", help="Artifact version, normally the short Git commit hash")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    lock_hash = hashlib.sha256(LOCK.read_bytes()).hexdigest()
    existing = output / "runtime-manifest.json"
    if existing.is_file() and not args.force:
        manifest = json.loads(existing.read_text(encoding="utf-8"))
        executable = output / manifest.get("executable", "")
        if (
            manifest.get("requirementsSha256") == lock_hash
            and executable.is_file()
            and Path(manifest.get("executable", "")).parts[:1] == ("py",)
            and (output / BROWSER_READY).is_file()
        ):
            if args.version and manifest.get("version") != args.version:
                manifest["version"] = args.version
                existing.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"Python runtime is current: {manifest['version']}")
            return

    uv = uv_executable()
    staging = output.with_name(output.name + ".staging")
    if staging.exists():
        shutil.rmtree(staging)
    python_store = staging / "python"
    python_store.mkdir(parents=True)
    env = {
        **os.environ,
        "UV_CACHE_DIR": str(DESKTOP / ".uv-cache"),
        "UV_PYTHON_INSTALL_DIR": str(python_store),
    }
    run(uv, "python", "install", PYTHON_REQUEST, "--install-dir", str(python_store), "--no-bin", env=env)
    # uv also creates a convenient major/minor alias whose target is absolute.
    # Resolve it while staging, then remove top-level aliases before packaging.
    python = find_python(python_store).resolve()
    # This is our private standalone distribution, not an operating-system Python.
    # Installing into its own site-packages keeps interpreter + stdlib + dependencies
    # inside one movable directory tree and avoids non-relocatable venv symlinks.
    run(
        uv, "pip", "install", "--python", str(python), "--system",
        "--break-system-packages", "--requirements", str(LOCK), env=env,
    )
    version = run(str(python), "-c", "import platform; print(platform.python_version())", env=env)
    remove_python_aliases(python_store)
    distribution = python_store / python.relative_to(python_store).parts[0]
    executable_in_distribution = python.relative_to(distribution)
    # uv's long distribution name can push lark-oapi beyond Windows MAX_PATH.
    compact_distribution = staging / "py"
    distribution.rename(compact_distribution)
    python = compact_distribution / executable_in_distribution
    browser_dir = staging / BROWSER_DIR
    browser_env = {
        **env,
        "PLAYWRIGHT_BROWSERS_PATH": str(browser_dir),
        "PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT": os.environ.get(
            "PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT", "120000"
        ),
    }
    # The Python package is part of the managed runtime; Chromium is installed
    # beside it so the whole renderer remains relocatable and travels with the
    # runtime ZIP to end users.
    run(str(python), "-m", "playwright", "install", "chromium", env=browser_env)
    # Validate after the final internal relocation.
    run(str(python), "-c",
        "import cv2,fastapi,fontTools,faster_whisper,lark_oapi,numpy,PIL,playwright,qrcode,scenedetect,scipy,tos,uvicorn,yaml,yt_dlp",
        env=env)
    # deepagents 引擎(桌面版默认引擎):运行时自带,core.deepagents_python() 回落到当前解释器
    run(str(python), "-c",
        "import deepagents,langchain_openai\n"
        "from deepagents.backends import LocalShellBackend\n"
        "from deepagents.middleware.summarization import SummarizationMiddleware\n"
        "from langgraph.checkpoint.sqlite import SqliteSaver",
        env=env)
    run(str(python), "-c",
        "import os\n"
        "from playwright.sync_api import sync_playwright\n"
        "with sync_playwright() as p:\n"
        "    assert os.path.isfile(p.chromium.executable_path)",
        env=browser_env)
    (browser_dir / ".chromium-ready").write_text("1\n", encoding="utf-8")
    manifest = {
        "schema": SCHEMA,
        "version": args.version or f"cpython-{version}-{lock_hash[:12]}",
        "pythonVersion": version,
        "platform": platform_id(),
        "arch": arch_id(),
        "executable": python.relative_to(staging).as_posix(),
        "requirementsSha256": lock_hash,
    }
    (staging / "runtime-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if output.exists():
        shutil.rmtree(output)
    staging.replace(output)
    print(f"Built Python runtime: {manifest['version']} ({output})")


if __name__ == "__main__":
    main()
