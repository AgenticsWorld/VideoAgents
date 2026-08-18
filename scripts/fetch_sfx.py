#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch_sfx.py — 花字音效库初始化(data/sfx/,gitignored,素材不进仓库)。

两种来源(可并用),完成后自动重扫 manifest:
  --synth              用 ffmpeg 合成一套入门音效(whoosh/impact/pop/ding/riser)。
                       纯本仓库合成、无第三方素材,LICENSE 记 CC0-1.0,离线可用,
                       11-qa/copyright 终审零风险。效果基础,正式项目建议换真实素材。
  --import-dir <dir>   把用户自备目录里的音频(wav/mp3/m4a/ogg/flac)拷入
                       data/sfx/imported/;请只放可商用素材(CC0 优先),并在
                       data/sfx/manifest.json 里人工补 license 字段(重扫会保留)。

推荐的开源(CC0)音效来源(手动下载后 --import-dir 导入):
  - Kenney(kenney.nl):UI Audio / Impact Sounds 包,CC0
  - freesound.org:按 license 过滤 Creative Commons 0
  - Sonniss GDC Game Audio Bundle(免版税,条款见其 EULA)

用法:
  python3 scripts/fetch_sfx.py --synth
  python3 scripts/fetch_sfx.py --import-dir ~/Downloads/kenney_impact_sounds
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "code"))
from _common import DATA_DIR                                      # noqa: E402

import captions as cap                                            # noqa: E402

SFX_DIR = DATA_DIR / "sfx"
AUDIO_EXTS = (".wav", ".mp3", ".m4a", ".ogg", ".flac", ".aac")

# 合成配方:文件名即 tags(whoosh_impact_01 → whoosh+impact);全部 48k 立体声 wav。
# lavfi 没法做真实拟音,这里用噪声/正弦 + 包络凑出可用的基础动效音。
_SYNTH = {
    # 快速掠过声:棕噪声 + 带通 + 快进快出包络
    "whoosh_soft_01.wav": (
        "anoisesrc=d=0.6:color=brown:seed=7,highpass=f=180,lowpass=f=2800,"
        "afade=t=in:d=0.22:curve=qsin,afade=t=out:st=0.28:d=0.32:curve=qsin,volume=4dB"),
    # 掠过+落点:噪声 whoosh 与 60Hz 低频冲击叠加
    "whoosh_impact_01.wav": (
        "anoisesrc=d=0.55:color=brown:seed=11,highpass=f=200,lowpass=f=3200,"
        "afade=t=in:d=0.18:curve=qsin,afade=t=out:st=0.2:d=0.14,volume=3dB[w];"
        "sine=f=58:d=0.5,adelay=300|300,afade=t=out:st=0.32:d=0.45:curve=exp,volume=8dB[b];"
        "[w][b]amix=inputs=2:normalize=0,alimiter=limit=0.891"),
    # 重落点:低频正弦衰减 + 短噪声爆点
    "impact_boom_01.wav": (
        "sine=f=52:d=0.7,afade=t=out:st=0.04:d=0.66:curve=exp,volume=9dB[b];"
        "anoisesrc=d=0.09:color=white:seed=3,lowpass=f=5000,"
        "afade=t=out:st=0.01:d=0.08,volume=-2dB[n];"
        "[b][n]amix=inputs=2:normalize=0,alimiter=limit=0.891"),
    # 轻快点缀:短正弦 pop
    "pop_light_01.wav": (
        "sine=f=880:d=0.12,afade=t=in:d=0.005,afade=t=out:st=0.03:d=0.09:curve=exp,"
        "volume=-2dB"),
    # 提示叮声:高频正弦长衰减
    "ding_soft_01.wav": (
        "sine=f=1318:d=0.9,afade=t=in:d=0.004,afade=t=out:st=0.05:d=0.85:curve=exp,"
        "volume=-4dB"),
    # 上扬铺垫:粉噪声渐强急停
    "riser_noise_01.wav": (
        "anoisesrc=d=1.1:color=pink:seed=23,highpass=f=300,lowpass=f=6000,"
        "afade=t=in:d=0.95:curve=qsin,afade=t=out:st=0.95:d=0.15,volume=2dB"),
}

_SYNTH_LICENSE = ("CC0-1.0\n本目录音效由 scripts/fetch_sfx.py 以 ffmpeg 滤镜合成,"
                  "不含任何第三方素材,可自由商用。\n")


import re as _re

_MAXVOL = _re.compile(r"max_volume:\s*(-?[\d.]+) dB")


def _peak_normalize(path: Path, headroom_db: float = 1.0):
    """把合成产物峰值归一到 -headroom dBFS。

    合成配方的原始电平偏低(实测 whoosh 峰值 -6dB、均值 -22dB),直接入混
    会被 -14 LUFS 的旁白完全盖住(2026-08-08 前科:成片里音效听不见)。
    归一后由 captions.json 的 gain_db 从 0dB 基准往下调,响度语义才直观。
    """
    p = subprocess.run([cap.ffmpeg_bin(), "-i", str(path), "-af", "volumedetect",
                        "-f", "null", "-"], capture_output=True, text=True, timeout=120)
    m = _MAXVOL.search(p.stderr or "")
    if not m:
        return
    boost = -float(m.group(1)) - headroom_db
    if abs(boost) < 0.2:
        return
    tmp = path.with_suffix(".norm.wav")
    subprocess.run([cap.ffmpeg_bin(), "-v", "error", "-y", "-i", str(path),
                    "-af", f"volume={boost:.2f}dB", str(tmp)],
                   capture_output=True, text=True, timeout=120, check=True)
    tmp.replace(path)


def synth():
    out_dir = SFX_DIR / "synth"
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, graph in _SYNTH.items():
        out = out_dir / name
        # 配方统一以无标号滤镜链结尾,这里补上 [out] 作为唯一输出
        cmd = [cap.ffmpeg_bin(), "-v", "error", "-y", "-filter_complex", graph + "[out]",
               "-map", "[out]", "-ar", "48000", "-ac", "2", str(out)]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if p.returncode != 0:
            raise SystemExit(f"[FAIL] 合成 {name} 失败:{(p.stderr or '')[-300:]}")
        _peak_normalize(out)
        print(f"[SYNTH] {out}")
    (out_dir / "LICENSE.txt").write_text(_SYNTH_LICENSE, encoding="utf-8")


def import_dir(src: Path):
    if not src.is_dir():
        raise SystemExit(f"[FAIL] 目录不存在:{src}")
    out_dir = SFX_DIR / "imported"
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for p in sorted(src.rglob("*")):
        if p.suffix.lower() in AUDIO_EXTS and p.is_file():
            safe = p.name.encode("ascii", "ignore").decode() or f"sfx_{n}.wav"
            shutil.copy2(p, out_dir / safe)      # ASCII 文件名红线(§1 原则 9)
            n += 1
    print(f"[IMPORT] {n} 个音频 → {out_dir}(请核对素材许可,manifest 里补 license 字段)")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--synth", action="store_true", help="合成入门音效包(CC0,离线)")
    ap.add_argument("--import-dir", default=None, help="导入用户自备音效目录")
    args = ap.parse_args()
    if not args.synth and not args.import_dir:
        ap.print_help()
        raise SystemExit(1)
    if args.synth:
        synth()
    if args.import_dir:
        import_dir(Path(args.import_dir).expanduser())
    m = cap.scan_sfx(SFX_DIR, SFX_DIR / "manifest.json")
    print(f"[DONE] manifest 刷新:{len(m['sfx'])} 条音效({SFX_DIR / 'manifest.json'})")


if __name__ == "__main__":
    main()
