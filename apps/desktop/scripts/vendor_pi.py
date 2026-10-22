#!/usr/bin/env python3
"""Install the pinned Pi Coding Agent used by the built-in Agentics engine.

vendor/pi/package.json + package-lock.json pin the version.  This script runs
`npm ci` there (production dependencies only, no lifecycle scripts), optionally
prunes files the packaged desktop app never loads, and checks that the launcher
starts.  The desktop build ships vendor/pi as extraResources (backend/vendor/pi)
and runs it with Electron's own Node (ELECTRON_RUN_AS_NODE=1), so no separate
Node.js is bundled.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
PI_DIR = ROOT / "vendor" / "pi"
NODE_MODULES = PI_DIR / "node_modules"
STAMP = NODE_MODULES / ".videoagents-install.json"
PACKAGE = "@earendil-works/pi-coding-agent"
CLI = NODE_MODULES / PACKAGE / "dist" / "bundle" / "cli.js"
LAUNCHER = PI_DIR / "launch.mjs"
TUI_NATIVE = NODE_MODULES / "@earendil-works" / "pi-tui" / "native"


def run(*command: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    print("+", " ".join(command), flush=True)
    result = subprocess.run(command, cwd=cwd, env=env, check=True, text=True,
                            stdout=subprocess.PIPE, encoding="utf-8")
    return result.stdout.strip()


def pinned_version() -> str:
    deps = json.loads((PI_DIR / "package.json").read_text(encoding="utf-8"))["dependencies"]
    return deps[PACKAGE]


def lock_hash() -> str:
    return hashlib.sha256((PI_DIR / "package-lock.json").read_bytes()).hexdigest()


def npm_executable() -> str:
    found = shutil.which("npm.cmd" if os.name == "nt" else "npm") or shutil.which("npm")
    if not found:
        raise SystemExit("npm is required to install the bundled pi (Node.js 22.19+).")
    return found


def platform_id() -> str:
    return {"Darwin": "darwin", "Windows": "win32", "Linux": "linux"}[platform.system()]


def arch_id() -> str:
    machine = platform.machine().lower()
    return {"aarch64": "arm64", "arm64": "arm64", "amd64": "x64", "x86_64": "x64"}.get(machine, machine)


def prune(target: str) -> None:
    """Drop what the engine never loads: source maps, esbuild (only used to compile
    pi extensions; the engine runs with --no-extensions) and other platforms' TUI
    helpers (native files must not reach the signed app for the wrong platform)."""
    removed = 0
    for path in list(NODE_MODULES.rglob("*.map")):
        if path.is_file():
            removed += path.stat().st_size
            path.unlink()
    for path in (NODE_MODULES / "esbuild", NODE_MODULES / "@esbuild"):
        if path.exists():
            shutil.rmtree(path)
    system, _, arch = target.partition("-")
    if TUI_NATIVE.is_dir():
        for os_dir in TUI_NATIVE.iterdir():
            if os_dir.is_dir() and os_dir.name != system:
                shutil.rmtree(os_dir)
        prebuilds = TUI_NATIVE / system / "prebuilds"
        if prebuilds.is_dir():
            for build in prebuilds.iterdir():
                if build.is_dir() and build.name != f"{system}-{arch}":
                    shutil.rmtree(build)
    print(f"Pruned bundled pi for {target} ({removed // (1024 * 1024)} MB of source maps)")


def electron_node() -> list[str] | None:
    """Electron executable from the desktop workspace (the Node the app really uses)."""
    package = ROOT / "node_modules" / "electron"
    path_file = package / "path.txt"
    if not path_file.is_file():
        return None
    executable = package / "dist" / path_file.read_text(encoding="utf-8").strip()
    return [str(executable)] if executable.is_file() else None


def check(node: list[str], expected: str) -> None:
    env = {**os.environ, "ELECTRON_RUN_AS_NODE": "1", "PI_OFFLINE": "1",
           "PI_SKIP_VERSION_CHECK": "1", "PI_TELEMETRY": "0", "NO_COLOR": "1"}
    version = run(*node, str(LAUNCHER), "--version", env=env).splitlines()[-1].strip()
    if version != expected:
        raise SystemExit(f"Bundled pi reports {version!r}, expected {expected!r}")
    print(f"Bundled pi {version} starts with {node[0]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--prune", action="store_true",
                        help="drop source maps, esbuild and other platforms' native helpers (desktop packaging)")
    parser.add_argument("--target", default=f"{platform_id()}-{arch_id()}",
                        help="platform-arch kept by --prune, e.g. darwin-arm64 or win32-x64")
    parser.add_argument("--force", action="store_true", help="reinstall even if the lockfile is unchanged")
    parser.add_argument("--node", help="Node executable for the start-up check (default: Electron, else node)")
    args = parser.parse_args()

    expected = pinned_version()
    state = {"lock": lock_hash(), "pruned": args.target if args.prune else ""}
    current = {}
    if STAMP.is_file() and CLI.is_file():
        try:
            current = json.loads(STAMP.read_text(encoding="utf-8"))
        except ValueError:
            current = {}
    # 已按另一个平台精简过的目录补不回来,只能重装
    repruned = args.prune and current.get("pruned") not in ("", None, args.target)
    if args.force or repruned or current.get("lock") != state["lock"]:
        if NODE_MODULES.exists():
            shutil.rmtree(NODE_MODULES)
        run(npm_executable(), "ci", "--omit=dev", "--ignore-scripts", "--no-audit", "--no-fund", cwd=PI_DIR)
        current = {"lock": state["lock"], "pruned": ""}
    else:
        print(f"Bundled pi {expected} is installed (lockfile unchanged)")
    if args.prune and current.get("pruned") != args.target:
        prune(args.target)
        current["pruned"] = args.target
    STAMP.write_text(json.dumps(current) + "\n", encoding="utf-8")

    node = [args.node] if args.node else (electron_node() or [shutil.which("node") or "node"])
    check(node, expected)


if __name__ == "__main__":
    sys.exit(main())
