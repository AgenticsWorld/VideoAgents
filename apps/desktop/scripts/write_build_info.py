#!/usr/bin/env python3
"""Write desktop channel/version metadata before packaging."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


TARGET = Path(__file__).resolve().parents[1] / "build-info.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", choices=["local", "dev", "release"], required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--build-hash", required=True)
    args = parser.parse_args()
    TARGET.write_text(json.dumps({
        "schema": 1,
        "channel": args.channel,
        "version": args.version,
        "buildHash": args.build_hash,
    }, indent=2) + "\n", encoding="utf-8")
    print(TARGET.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
