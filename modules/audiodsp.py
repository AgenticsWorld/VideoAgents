"""程序化音频 DSP 共享原语(48kHz,seed 固定可复现)。

从 code/build_ep01_{ambience,sfx,bgm}.py 中抽取的公共函数,三个脚本共用。
约定:white/brown_noise 收绝对 seed(调用方自带各自的 seed 基数,保证既有产物
比特级可复现);slow_lfo 保留原 9000+offset 语义。
"""
import numpy as np
from scipy.signal import butter, sosfilt

SR = 48000


def white(n, seed):
    r = np.random.default_rng(seed)
    return r.standard_normal(n)


def lowpass(x, cutoff, order=4, sr=SR):
    sos = butter(order, cutoff, btype="low", fs=sr, output="sos")
    return sosfilt(sos, x)


def highpass(x, cutoff, order=2, sr=SR):
    sos = butter(order, cutoff, btype="high", fs=sr, output="sos")
    return sosfilt(sos, x)


def bandpass(x, low, high, order=3, sr=SR):
    high = min(high, sr / 2 - 100)
    sos = butter(order, [low, high], btype="band", fs=sr, output="sos")
    return sosfilt(sos, x)


def brown_noise(n, seed, sr=SR):
    w = white(n, seed)
    b = np.cumsum(w)
    b = highpass(b, 0.8, order=1, sr=sr)  # 去直流漂移
    return b / (np.max(np.abs(b)) + 1e-9)


def colored_noise(n, exponent, seed, sr=SR):
    rng = np.random.default_rng(seed)
    w = rng.normal(0, 1, n)
    spectrum = np.fft.rfft(w)
    freqs = np.fft.rfftfreq(n, d=1.0 / sr)
    freqs[0] = freqs[1] if n > 1 else 1.0
    spectrum = spectrum / (freqs ** (exponent / 2.0))
    colored = np.fft.irfft(spectrum, n)
    return colored / (np.max(np.abs(colored)) + 1e-9)


def slow_lfo(n, cycles_amps, seed_offset=0, sr=SR):
    """cycles_amps: [(整数周期数覆盖整段时长, 幅度), ...]
    频率取 k/duration 的整数倍,保证时长首尾相位完全重合、循环处包络零跳变。"""
    t = np.arange(n) / sr
    duration = n / sr
    r = np.random.default_rng(9000 + seed_offset)
    out = np.zeros(n)
    for k, a in cycles_amps:
        f = k / duration
        phase = r.uniform(0, 2 * np.pi)
        out += a * np.sin(2 * np.pi * f * t + phase)
    return out


def env_exp(n, decay):
    t = np.linspace(0, 1, n)
    return np.exp(-decay * t)


def env_rise_fall(n):
    t = np.linspace(0, 1, n)
    return np.sin(np.pi * t)


def env_swell_cut(n, cut_at=0.62):
    t = np.linspace(0, 1, n)
    env = np.sin(np.pi * np.minimum(t, cut_at) / cut_at / 2) ** 0.7
    cut_idx = int(cut_at * n)
    fade = np.ones(n)
    tail = n - cut_idx
    if tail > 0:
        fade[cut_idx:] = np.linspace(1, 0, tail) ** 3
    return env * fade


def loop_crossfade(sig, fade_len):
    """noise 类信号首尾等能量(equal-power)交叉淡化,消除循环接缝爆音/音量凹陷。"""
    sig = sig.copy()
    fade = np.linspace(0, 1, fade_len)
    fade_out = np.cos(fade * np.pi / 2)
    fade_in = np.sin(fade * np.pi / 2)
    tail = sig[-fade_len:].copy()
    head = sig[:fade_len].copy()
    sig[-fade_len:] = tail * fade_out + head * fade_in
    return sig


def normalize_peak(sig, peak_dbfs=-3.0):
    target = 10 ** (peak_dbfs / 20)
    cur = np.max(np.abs(sig)) + 1e-9
    return sig * (target / cur)


def stereoize(l, r, width=0.35):
    mid = (l + r) / 2
    side = (l - r) / 2 * width
    left = mid + side
    right = mid - side
    return np.stack([left, right], axis=1)


def mix(*layers):
    n = max(len(x) for x in layers)
    out = np.zeros(n)
    for x in layers:
        out[: len(x)] += x
    return out
