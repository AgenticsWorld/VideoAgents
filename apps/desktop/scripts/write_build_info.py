#!/usr/bin/env python3
"""Write desktop channel/version metadata before packaging."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


TARGET = Path(__file__).resolve().parents[1] / "build-info.json"
LOCK = Path(__file__).resolve().parents[1] / "runtime-requirements.lock"


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
        # 与 build_runtime.py 的 requirementsSha256 同源:app 启动时比对已装
        # 运行时的依赖清单哈希,不一致自动更新运行时(app 升级带动依赖升级)
        "requirementsSha256": hashlib.sha256(LOCK.read_bytes()).hexdigest(),
    }, indent=2) + "\n", encoding="utf-8")
    print(TARGET.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
