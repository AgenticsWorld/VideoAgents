#!/usr/bin/env python3
"""Fetch the pinned FFmpeg package for the distribution mirrors that lack it.

The desktop client installs FFmpeg on Windows from the same mirror as the
Python runtime (S3 for the agentics.world build, OSS for the shumati.cn build).
The package is an upstream build pinned in ffmpeg-artifact.json; this script
checks each mirror's public URL and downloads the package only when a mirror
does not serve it yet, so a release does not depend on the upstream host once
the mirrors are filled.

stdout carries `upload_<mirror>=true|false` lines for $GITHUB_OUTPUT; progress
goes to stderr.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import re
import sys
import urllib.request
from pathlib import Path


DESKTOP = Path(__file__).resolve().parents[1]
DEFAULT_PIN = DESKTOP / "ffmpeg-artifact.json"
SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
MAX_SIZE = 2 * 1024 * 1024 * 1024
OPENER = urllib.request.build_opener()


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def load_pin(path: Path) -> list[dict]:
    """Return the pinned packages as flat entries with platform and arch."""
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema") != 1:
        raise SystemExit(f"Unsupported FFmpeg pin schema: {path}")
    entries: list[dict] = []
    for platform in ("mac", "win"):
        for arch, artifact in sorted((document.get(platform) or {}).items()):
            where = f"{path.name} {platform}/{arch}"
            if not SAFE_NAME.fullmatch(str(artifact.get("version", ""))):
                raise SystemExit(f"Invalid FFmpeg version in {where}")
            filename = str(artifact.get("filename", ""))
            if not SAFE_NAME.fullmatch(filename) or not filename.endswith(".zip"):
                raise SystemExit(f"Invalid FFmpeg filename in {where}")
            if not SHA256.fullmatch(str(artifact.get("sha256", ""))):
                raise SystemExit(f"Invalid FFmpeg sha256 in {where}")
            size = artifact.get("size")
            if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_SIZE:
                raise SystemExit(f"Invalid FFmpeg size in {where}")
            sources = artifact.get("sources")
            if not isinstance(sources, list) or not sources or not all(
                isinstance(source, str) and source.startswith("https://") for source in sources
            ):
                raise SystemExit(f"FFmpeg sources must be https URLs in {where}")
            entries.append({**artifact, "platform": platform, "arch": arch})
    if not entries:
        raise SystemExit(f"No FFmpeg packages pinned in {path}")
    return entries


def mirror_url(public_base: str, filename: str) -> str:
    return f"{public_base.rstrip('/')}/ffmpeg/{filename}"


def mirror_has(public_base: str, entry: dict) -> bool:
    """True when the mirror already serves the package at its pinned size."""
    url = mirror_url(public_base, entry["filename"])
    request = urllib.request.Request(url, method="HEAD")
    try:
        with OPENER.open(request, timeout=60) as response:
            length = response.headers.get("Content-Length")
    except (OSError, http.client.HTTPException) as error:
        # A missing object answers 403 on S3 and 404 on OSS.
        log(f"  {url}: not available ({error})")
        return False
    if length != str(entry["size"]):
        log(f"  {url}: size {length} differs from pinned {entry['size']}")
        return False
    log(f"  {url}: present")
    return True


def fetch(entry: dict, output: Path) -> Path:
    """Download the package from the first source that yields the pinned bytes."""
    target = output / entry["filename"]
    if target.is_file() and target.stat().st_size == entry["size"] and sha256(target) == entry["sha256"]:
        return target
    output.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f"{target.name}.part")
    problems: list[str] = []
    for source in entry["sources"]:
        log(f"Downloading {source}")
        try:
            digest = hashlib.sha256()
            received = 0
            with OPENER.open(source, timeout=120) as response, partial.open("wb") as stream:
                while chunk := response.read(1024 * 1024):
                    received += len(chunk)
                    if received > entry["size"]:
                        raise ValueError("larger than the pinned size")
                    digest.update(chunk)
                    stream.write(chunk)
            if received != entry["size"]:
                raise ValueError(f"size {received} differs from pinned {entry['size']}")
            if digest.hexdigest() != entry["sha256"]:
                raise ValueError("SHA-256 differs from the pinned value")
        except (OSError, http.client.HTTPException, ValueError) as error:
            partial.unlink(missing_ok=True)
            problems.append(f"{source}: {error}")
            log(f"  failed: {error}")
            continue
        partial.replace(target)
        return target
    raise SystemExit("Could not fetch the pinned FFmpeg package:\n" + "\n".join(problems))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def parse_mirror(value: str) -> tuple[str, str]:
    name, separator, public_base = value.partition("=")
    if not separator or not re.fullmatch(r"[a-z0-9_]+", name) or not public_base.startswith(("https://", "http://")):
        raise argparse.ArgumentTypeError(f"expected NAME=PUBLIC_BASE_URL, got {value!r}")
    return name, public_base


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pin", type=Path, default=DEFAULT_PIN)
    parser.add_argument("--mirror", type=parse_mirror, action="append", required=True,
                        metavar="NAME=PUBLIC_BASE_URL", help="Distribution mirror to check; repeatable")
    parser.add_argument("--output", type=Path, required=True, help="Directory for packages that need uploading")
    args = parser.parse_args()
    entries = load_pin(args.pin)
    uploads: dict[str, bool] = {}
    for name, public_base in args.mirror:
        log(f"Checking mirror {name}")
        missing = [entry for entry in entries if not mirror_has(public_base, entry)]
        uploads[name] = bool(missing)
        for entry in missing:
            fetch(entry, args.output)
    for name, upload in uploads.items():
        print(f"upload_{name}={'true' if upload else 'false'}")


if __name__ == "__main__":
    main()
