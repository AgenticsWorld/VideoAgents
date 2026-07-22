#!/usr/bin/env python3
"""Create a versioned, checksummed desktop Python runtime ZIP."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import zipfile
from pathlib import Path


DESKTOP = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME = DESKTOP / ".runtime"
DEFAULT_OUTPUT = DESKTOP / ".runtime-packages"
PUBLIC_BASE = "https://s3.agentics.world/packages/python/"
PLATFORM_NAMES = {"darwin": "macos", "win32": "windows"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_junction(path: Path) -> bool:
    check = getattr(path, "is_junction", None)
    return bool(check and check())


def runtime_paths(root: Path):
    """Yield runtime entries without descending into symlinks or junctions."""
    for source in sorted(root.iterdir()):
        yield source
        if source.is_dir() and not source.is_symlink() and not is_junction(source):
            yield from runtime_paths(source)


def add_path(archive: zipfile.ZipFile, source: Path, relative: Path) -> None:
    name = relative.as_posix()
    if is_junction(source):
        # Windows uv aliases target the absolute staging directory and must not
        # be distributed. The real versioned CPython directory is also present.
        return
    if source.is_symlink():
        info = zipfile.ZipInfo(name)
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, os.readlink(source))
    elif source.is_dir():
        info = zipfile.ZipInfo(f"{name}/")
        info.external_attr = (stat.S_IFDIR | 0o755) << 16
        archive.writestr(info, b"")
    else:
        archive.write(source, name)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    runtime = args.runtime.resolve()
    manifest = json.loads((runtime / "runtime-manifest.json").read_text(encoding="utf-8"))
    if manifest.get("version") != args.version:
        raise SystemExit("Runtime manifest version does not match --version")
    platform_name = PLATFORM_NAMES.get(manifest.get("platform"))
    if not platform_name:
        raise SystemExit(f"Unsupported runtime platform: {manifest.get('platform')}")
    arch = manifest.get("arch")
    filename = f"{platform_name}-python-{args.version}-{arch}.zip"
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    package = output_dir / filename
    with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for source in runtime_paths(runtime):
            add_path(archive, source, source.relative_to(runtime))
    metadata = {
        "schema": 1,
        "platform": "mac" if manifest["platform"] == "darwin" else "win",
        "arch": arch,
        "version": args.version,
        "pythonVersion": manifest["pythonVersion"],
        "filename": filename,
        "url": f"{PUBLIC_BASE}{filename}",
        "sha256": sha256(package),
        "size": package.stat().st_size,
    }
    metadata_path = output_dir / f"{filename}.metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
