#!/usr/bin/env python3
"""Normalize a release desktop artifact and emit S3 metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import zipfile
from pathlib import Path


DESKTOP = Path(__file__).resolve().parents[1]
RELEASE = DESKTOP / "release"
OUTPUT = DESKTOP / ".desktop-packages"
PUBLIC_BASE = "https://s3.agentics.world/packages/video-agents/"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def newest(pattern: str) -> Path:
    matches = [path for path in RELEASE.glob(pattern) if path.is_file()]
    if not matches:
        raise SystemExit(f"No desktop artifact matching {pattern}")
    return max(matches, key=lambda path: path.stat().st_mtime_ns)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", choices=["mac", "win"], required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--build-hash", required=True)
    parser.add_argument("--public-base", default=PUBLIC_BASE)
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    filename = f"VideoAgents-{args.version}.zip"
    package = OUTPUT / filename
    if args.platform == "mac":
        source = newest("*.zip")
        shutil.copyfile(source, package)
    else:
        installer = newest("*.exe")
        with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.write(installer, "VideoAgents-Setup.exe")
    metadata = {
        "schema": 1,
        "platform": args.platform,
        "version": args.version,
        "buildHash": args.build_hash,
        "filename": filename,
        "url": f"{args.public_base.rstrip('/')}/{args.platform}/{filename}",
        "sha256": sha256(package),
        "size": package.stat().st_size,
    }
    metadata_path = OUTPUT / f"{filename}.metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
