#!/usr/bin/env python3
"""Build the production desktop and Python package metadata document."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path


VERSION = re.compile(r"^\d+\.\d+\.\d+$")


def version_tuple(value: str) -> tuple[int, int, int]:
    if not VERSION.fullmatch(value):
        raise SystemExit(f"Invalid desktop version: {value}")
    return tuple(map(int, value.split(".")))  # type: ignore[return-value]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--desktop-artifacts", type=Path, action="append")
    parser.add_argument("--minimum-desktop-version", default="1.0.0")
    parser.add_argument("--version", required=True)
    parser.add_argument("--public-base", help="Package URL prefix for this distribution")
    args = parser.parse_args()
    document = {"schema": 1, "python": {}}
    python = document["python"]
    metadata_files = sorted(args.artifacts.rglob("*.metadata.json"))
    if not metadata_files:
        raise SystemExit("No runtime metadata files found")
    for metadata_file in metadata_files:
        metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
        if metadata.get("version") != args.version:
            raise SystemExit(f"Runtime version does not match release tag: {metadata_file}")
        metadata.pop("schema", None)
        platform = metadata.pop("platform")
        arch = metadata.pop("arch")
        filename = metadata.pop("filename", None)
        if args.public_base and filename:
            metadata["url"] = f"{args.public_base.rstrip('/')}/python/{filename}"
        python.setdefault(platform, {})[arch] = metadata
    if args.desktop_artifacts:
        desktop = document.setdefault("desktop", {})
        minimum_version = args.minimum_desktop_version
        if version_tuple(minimum_version) > version_tuple(args.version):
            raise SystemExit("Minimum desktop version cannot be newer than the release version")
        desktop["minimumVersion"] = minimum_version
        desktop_files = sorted(
            metadata_file
            for artifact_dir in args.desktop_artifacts
            for metadata_file in artifact_dir.rglob("*.metadata.json")
        )
        if not desktop_files:
            raise SystemExit("No desktop metadata files found")
        for metadata_file in desktop_files:
            metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
            if metadata.get("version") != args.version:
                raise SystemExit(f"Desktop version does not match release tag: {metadata_file}")
            metadata.pop("schema", None)
            platform = metadata.pop("platform")
            if platform in desktop:
                raise SystemExit(f"Duplicate desktop metadata for {platform}: {metadata_file}")
            filename = metadata.pop("filename", None)
            if args.public_base and filename:
                metadata["url"] = f"{args.public_base.rstrip('/')}/{platform}/{filename}"
            desktop[platform] = metadata
    document["updatedAt"] = datetime.now(timezone.utc).isoformat()
    args.index.parent.mkdir(parents=True, exist_ok=True)
    args.index.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(args.index.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
