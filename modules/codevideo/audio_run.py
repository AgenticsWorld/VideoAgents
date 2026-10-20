"""作曲 / 音效 / 混音脚本运行器:python -m modules.codevideo.audio_run <script.py> <out.wav>

由 audio.run_script 以子进程调用(环境变量见 audio.py 头注释)。把脚本当 __main__ 跑完后,
若脚本用过上游采样器(core.audio.sampler),把用到的乐器署名写到 <out.wav>.credits.json,供 CREDITS 汇总。
"""
from __future__ import annotations

import json
import os
import runpy
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: python -m modules.codevideo.audio_run <script.py> <out.wav>", file=sys.stderr)
        return 2
    script, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    lib = os.environ.get("LIB")
    if lib and lib not in sys.path:
        sys.path.insert(0, lib)
    sys.argv = [str(script)]
    runpy.run_path(str(script), run_name="__main__")
    sampler = sys.modules.get("core.audio.sampler") or sys.modules.get("sampler")
    names = sorted(getattr(sampler, "_INST", {}) or {}) if sampler else []
    if names:
        try:
            lines = sampler.credits(names)
            lines = lines if isinstance(lines, list) else [str(lines)]
        except Exception as e:  # noqa: BLE001
            lines = [f"(credits() failed: {e})"]
        Path(str(out) + ".credits.json").write_text(json.dumps({"instruments": names, "lines": lines}, ensure_ascii=False,
                                                                indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
