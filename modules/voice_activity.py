# -*- coding: utf-8 -*-
"""voice_activity.py — 本机「像人声的有声段」检测(不转写、不下载模型;规约禁止自发 ASR)。

按短时自相关找 80–400 Hz 的周期性 + 能量,逐 50 ms 帧打标,再合并成有声段。两处共用:
  code/check_dialogue_audible.py  出片后对白镜有声段核对(2026-10-03)
  code/dub_group.py               后期配音的开口时段检测(2026-10-03 起在人声 stem 上跑,见 §8C)
"""
from __future__ import annotations

import subprocess
import wave
from pathlib import Path

HOP_S, WIN_S = 0.05, 0.064
F0_LO, F0_HI = 80, 400
MERGE_GAP_S = 0.5
MIN_RUN_S = 0.25
ENERGY_FLOOR = 0.005
PERIODICITY = 0.45


def extract_wav(src: Path, out: Path, mono_16k: bool = True) -> None:
    """抽成 16 kHz 单声道 PCM(自相关只看基频,够用且快)。"""
    cmd = ["ffmpeg", "-v", "error", "-y", "-nostdin", "-i", str(src), "-vn"]
    if mono_16k:
        cmd += ["-ac", "1", "-ar", "16000"]
    cmd += ["-c:a", "pcm_s16le", str(out)]
    subprocess.run(cmd, check=True)


def voiced_mask(wav: Path, energy_floor: float = ENERGY_FLOOR, periodicity: float = PERIODICITY) -> list[bool]:
    import numpy as np
    w = wave.open(str(wav))
    sr, nch = w.getframerate(), w.getnchannels()
    x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(float) / 32768
    if nch > 1:
        x = x.reshape(-1, nch).mean(axis=1)
    hop, win = int(sr * HOP_S), int(sr * WIN_S)
    lo, hi = sr // F0_HI, sr // F0_LO
    out = []
    hann = np.hanning(win)
    for i in range(0, max(0, len(x) - win), hop):
        f = x[i:i + win] * hann
        e = float(np.sqrt((f ** 2).mean()))
        if e < energy_floor:
            out.append(False)
            continue
        ac = np.correlate(f, f, "full")[win - 1:]
        ac = ac / (ac[0] + 1e-12)
        out.append(bool(ac[lo:hi].max() > periodicity))
    return out


def runs_from_mask(mask, hop=HOP_S, merge_gap=MERGE_GAP_S, min_run=MIN_RUN_S) -> list[list[float]]:
    runs, start = [], None
    for i, v in enumerate(list(mask) + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            runs.append([start * hop, i * hop])
            start = None
    merged = []
    for r in runs:
        if merged and r[0] - merged[-1][1] <= merge_gap:
            merged[-1][1] = r[1]
        else:
            merged.append(list(r))
    return [[round(a, 3), round(b, 3)] for a, b in merged if b - a >= min_run]


def longest_in_window(runs, a, b) -> float:
    best = 0.0
    for s, e in runs:
        ov = min(e, b) - max(s, a)
        if ov > best:
            best = ov
    return round(best, 2)


def voiced_runs(audio: Path, merge_gap: float = MERGE_GAP_S, min_run: float = MIN_RUN_S,
                energy_floor: float = ENERGY_FLOOR) -> list[list[float]]:
    """任意音频 / 视频文件 → 有声段 [[start, end], ...](秒)。"""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "va.wav"
        extract_wav(audio, wav)
        return runs_from_mask(voiced_mask(wav, energy_floor=energy_floor), merge_gap=merge_gap, min_run=min_run)
