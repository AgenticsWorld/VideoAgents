"""「生成模型」页代码音效标签页的「试听」:一串程序合成的拟音(点击、嗖、闷响、叮、爆破音、心跳),每个落在固定时刻。"""
import os
import sys

import numpy as np
import soundfile as sf

sys.path.insert(0, os.environ["LIB"])
from core.audio import sfx  # noqa: E402

buf = np.zeros((int(sfx.SR * 6.0), 2), np.float32)
for at, snd, gain, pan in ((0.2, sfx.click(), 0.8, -0.4), (0.8, sfx.whoosh(0.5), 0.7, 0.0), (1.6, sfx.thump(), 0.9, 0.0),
                           (2.4, sfx.ding(), 0.6, 0.3), (3.2, sfx.pop(), 0.7, -0.2), (3.9, sfx.step(), 0.8, 0.2),
                           (4.6, sfx.heartbeat(), 0.8, 0.0)):
    sfx.add(buf, snd, at, gain, pan)
peak = float(np.abs(buf).max()) or 1.0
sf.write(os.environ["CODE_AUDIO_OUT"], (buf / peak * 0.7).astype(np.float32), sfx.SR)
