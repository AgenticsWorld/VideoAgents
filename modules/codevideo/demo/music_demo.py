"""「生成模型」页代码作曲标签页的「试听」:8 秒示范配乐(脚本契约与 Agent 写的作曲脚本相同)。
装了采样库就用采样乐器(竖琴 / 钢琴 / 弦乐拨奏,取第一个可用的),否则用合成器 —— 两种都能证明工具链可用。"""
import os
import sys

import numpy as np
import soundfile as sf

sys.path.insert(0, os.environ["LIB"])
from core.audio import sampler as S  # noqa: E402
from core.audio import sfx  # noqa: E402

SR, BEAT, DUR = sfx.SR, 0.5, 8.0
MELODY = ["D4", "F4", "A4", "D5", "C5", "A4", "G4", "E4", "F4", "A4", "C5", "F5", "E5", "C5", "D5", "A4"]
BASS = ["D2", "D2", "C2", "C2", "Bb1", "Bb1", "A1", "A1"]
inst = None
os.environ["CODEVIDEO_AUTO_FETCH"] = "0"          # 试听不触发下载
for cand in ("harp", "piano", "violins_pizz", "celesta", "harpsichord"):
    try:
        S.load(cand)
        inst = cand
        break
    except Exception:  # noqa: BLE001 — 没下载这个库
        continue
if inst:
    ev = [(i * BEAT / 2, inst, S.midi(p), BEAT, 0.7, 0.2) for i, p in enumerate(MELODY)]
    ev += [(i * BEAT * 2, inst, S.midi(p) + 12, BEAT * 2, 0.55, -0.3) for i, p in enumerate(BASS)]
    buf = S.render(ev, dur=DUR)
else:
    t = np.arange(int(SR * DUR)) / SR
    buf = np.zeros((len(t), 2), np.float32)

    def tone(f, at, d, amp, pan):
        n0, n = int(at * SR), int(d * SR)
        tt = np.arange(n) / SR
        x = (np.sin(2 * np.pi * f * tt) + 0.3 * np.sin(4 * np.pi * f * tt)) * np.exp(-tt * 3.0) * np.clip(tt * 80, 0, 1)
        n = min(n, len(buf) - n0)
        buf[n0:n0 + n, 0] += amp * (1 - pan) * x[:n]
        buf[n0:n0 + n, 1] += amp * (1 + pan) * x[:n]
    for i, p in enumerate(MELODY):
        tone(S.hz(p), i * BEAT / 2, BEAT * 1.5, 0.18, 0.2)
    for i, p in enumerate(BASS):
        tone(S.hz(p) * 2, i * BEAT * 2, BEAT * 2.5, 0.22, -0.3)
    buf = (buf / max(1e-6, np.abs(buf).max()) * 0.7).astype(np.float32)
print(f"demo instrument: {inst or 'synth'}")
sf.write(os.environ["CODE_AUDIO_OUT"], buf, SR)
