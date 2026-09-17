#!/usr/bin/env python3
"""Print the CHANGELOG.md section for one release version.

Usage: python scripts/release_notes.py 1.0.31 [--changelog CHANGELOG.md]

Exits 1 (printing nothing) when the version has no section, so the caller can
fall back to auto-generated notes.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HEADING = re.compile(r"^## \[(?P<version>[^\]]+)\](?: - (?P<date>\S+))?\s*$")


def extract(changelog: str, version: str) -> str | None:
    lines = changelog.splitlines()
    start = None
    for i, line in enumerate(lines):
        m = HEADING.match(line)
        if m and m.group("version") == version:
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if HEADING.match(lines[j]):
            end = j
            break
    body = "\n".join(lines[start + 1 : end]).strip("\n")
    return body + "\n" if body else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version", help="release version without the leading v, e.g. 1.0.31")
    parser.add_argument("--changelog", default="CHANGELOG.md", type=Path)
    args = parser.parse_args()
    version = args.version.lstrip("v")
    body = extract(args.changelog.read_text(encoding="utf-8"), version)
    if body is None:
        print(f"No CHANGELOG.md section for {version}", file=sys.stderr)
        return 1
    sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
