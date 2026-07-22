#!/usr/bin/env python3
"""Merge runtime package metadata into packages/video-agents.json."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--desktop-artifacts", type=Path)
    parser.add_argument("--public-base", help="Override package URL prefix, useful for staging tests")
    args = parser.parse_args()
    if args.index.is_file():
        try:
            document = json.loads(args.index.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            document = {}
    else:
        document = {}
    if not isinstance(document, dict):
        raise SystemExit("Existing runtime index must be a JSON object")
    document["schema"] = 1
    python = document.setdefault("python", {})
    if not isinstance(python, dict):
        raise SystemExit("Existing runtime index python field must be an object")
    metadata_files = sorted(args.artifacts.rglob("*.metadata.json"))
    if not metadata_files:
        raise SystemExit("No runtime metadata files found")
    for metadata_file in metadata_files:
        metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
        metadata.pop("schema", None)
        platform = metadata.pop("platform")
        arch = metadata.pop("arch")
        filename = metadata.pop("filename", None)
        if args.public_base and filename:
            metadata["url"] = f"{args.public_base.rstrip('/')}/{filename}"
        python.setdefault(platform, {})[arch] = metadata
    if args.desktop_artifacts:
        desktop = document.setdefault("desktop", {})
        if not isinstance(desktop, dict):
            raise SystemExit("Existing runtime index desktop field must be an object")
        desktop_files = sorted(args.desktop_artifacts.rglob("*.metadata.json"))
        if not desktop_files:
            raise SystemExit("No desktop metadata files found")
        for metadata_file in desktop_files:
            metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
            metadata.pop("schema", None)
            platform = metadata.pop("platform")
            desktop[platform] = metadata
    document["updatedAt"] = datetime.now(timezone.utc).isoformat()
    args.index.parent.mkdir(parents=True, exist_ok=True)
    args.index.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(args.index.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
