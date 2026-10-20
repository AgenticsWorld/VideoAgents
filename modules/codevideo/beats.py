"""演示视频的节拍对齐(三期,2026-10-20):配乐来自项目音乐库 / 用户音乐 / 音乐模型时,先测出音乐的节拍网格,再把画面时间轴对到拍上。

代码作曲的片子是「先网格后画面」:作曲脚本从 timeline.json 算拍点,天然对齐。外来音乐反过来——音乐固定,画面去贴它:
1. fit():从一首曲子里选一段(从强拍起,开头静音之后),长度 = 片长;不够长就在两个强拍之间无缝循环;尾部淡出 → codevideo/music/score.wav
2. analyze():测节拍 → codevideo/music/beats.json {bpm, beats[], downbeats[], meter, confidence, audio_sha256}
3. snap():把 timeline.json 的段边界吸到强拍(其次拍点)、镜头切点吸到拍点、击点吸到拍点;段边界的移动用分段线性时间映射
   带动段内所有时刻(镜头、台词、击点),片长不变;改前备份,改后照常 sync

测拍只用 numpy / scipy(不引 librosa):对数频带谱通量作起音包络 → 自相关估速度(以 120 BPM 为中心的对数高斯先验)→
Ellis 2007 动态规划跟拍 → 低频带起音在每 meter 拍上的强弱定强拍相位。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np

from . import NS, CodeVideoError, ffmpeg

SR_ANALYSIS = 22050
HOP = 256
N_FFT = 2048
SR_OUT = 48000
SCHEMA = "codevideo_beats/1"


# ---------------------------------------------------------------- 读音频

def _decode(path: Path, sr: int, channels: int) -> np.ndarray:
    r = subprocess.run([ffmpeg(), "-v", "error", "-i", str(path), "-f", "f32le", "-acodec", "pcm_f32le", "-ac", str(channels),
                        "-ar", str(sr), "-"], capture_output=True)
    if r.returncode or not r.stdout:
        raise CodeVideoError(f"读不了音频 {path.name}:{r.stderr.decode(errors='replace')[-300:]}")
    x = np.frombuffer(r.stdout, dtype=np.float32)
    return x.reshape(-1, channels) if channels > 1 else x


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- 起音包络 / 速度 / 跟拍

def onset_envelope(y: np.ndarray, sr: int = SR_ANALYSIS) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(全频起音强度, 低频起音强度, 帧时刻 s)。"""
    if len(y) < N_FFT:
        y = np.pad(y, (0, N_FFT - len(y)))
    n = 1 + (len(y) - N_FFT) // HOP
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(n)[:, None]
    S = np.abs(np.fft.rfft(y[idx] * np.hanning(N_FFT)[None, :], axis=1))
    freqs = np.fft.rfftfreq(N_FFT, 1 / sr)
    edges = np.geomspace(30, sr / 2 * 0.95, 49)
    bands, low_n = [], 0
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (freqs >= lo) & (freqs < hi)
        if m.any():
            bands.append(S[:, m].mean(axis=1))
            if hi <= 200:
                low_n += 1
    B = np.log1p(100.0 * np.stack(bands, axis=1))
    D = np.maximum(0.0, np.diff(B, axis=0, prepend=B[:1]))
    full = D.mean(axis=1)
    low = D[:, :max(1, low_n)].mean(axis=1)

    def norm(e):
        k = max(1, int(round(0.4 * sr / HOP)))
        base = np.convolve(e, np.ones(k) / k, mode="same")
        e = np.maximum(0.0, e - base)
        s = e.std()
        return e / s if s > 0 else e

    # 帧时刻按窗尾计:对数压缩后,起音刚进窗口尾部谱通量就跳起来(按窗中心计会系统性早半个窗,约 46 ms)
    times = (np.arange(n) * HOP + N_FFT - 2 * HOP) / sr      # 按合成鼓点 / 拨弦音实测校准,系统偏差 < 5 ms
    return norm(full), norm(low), times


def estimate_tempo(env: np.ndarray, sr: int = SR_ANALYSIS, lo_bpm: float = 50, hi_bpm: float = 220) -> tuple[float, float]:
    """(bpm, 置信度 0–1)。自相关 × 对数高斯先验(中心 120 BPM,一个八度的标准差)。"""
    fr = sr / HOP
    e = env - env.mean()
    ac = np.correlate(e, e, mode="full")[len(e) - 1:]
    if ac[0] <= 0:
        return 120.0, 0.0
    ac = ac / ac[0]
    lags = np.arange(len(ac))
    with np.errstate(divide="ignore"):
        bpm = 60.0 * fr / np.maximum(lags, 1e-9)
    ok = (bpm >= lo_bpm) & (bpm <= hi_bpm)
    prior = np.exp(-0.5 * (np.log2(np.maximum(bpm, 1e-9) / 120.0)) ** 2)
    score = np.where(ok, np.maximum(ac, 0) * prior, -1)
    k = int(np.argmax(score))
    if 1 <= k < len(ac) - 1:                   # 抛物线插值细化峰位
        a, b, c = ac[k - 1], ac[k], ac[k + 1]
        den = a - 2 * b + c
        off = 0.5 * (a - c) / den if den != 0 else 0.0
        k_f = k + float(np.clip(off, -0.5, 0.5))
    else:
        k_f = float(k)
    conf = float(np.clip(ac[k] / (np.abs(ac[ok]).mean() * 4 + 1e-9), 0, 1)) if ok.any() else 0.0
    return 60.0 * fr / k_f, conf


def track_beats(env: np.ndarray, bpm: float, tightness: float = 100.0) -> np.ndarray:
    """Ellis 2007 动态规划:返回拍点帧号。"""
    period = 60.0 / bpm * SR_ANALYSIS / HOP
    n = len(env)
    score = env.astype(float).copy()
    back = np.full(n, -1)
    lo_off, hi_off = int(round(2 * period)), max(1, int(round(period / 2)))
    for i in range(n):
        lo, hi = i - lo_off, i - hi_off
        if hi < 0:
            continue
        lo = max(0, lo)
        prev = np.arange(lo, hi + 1)
        cand = score[prev] - tightness * np.log((i - prev) / period) ** 2
        j = int(np.argmax(cand))
        score[i] = env[i] + cand[j]
        back[i] = prev[j]
    tail = max(1, int(round(period)))
    i = n - tail + int(np.argmax(score[n - tail:]))
    beats = []
    while i >= 0:
        beats.append(i)
        i = back[i]
    beats = np.array(beats[::-1])
    # 去掉开头 / 结尾落在静音里的拍(起音包络长时间为零)
    strong = env > 0.05 * (env.max() or 1)
    if strong.any():
        first, last = np.argmax(strong), n - 1 - np.argmax(strong[::-1])
        beats = beats[(beats >= first - period / 2) & (beats <= last + period / 2)]
    return beats


def analyze(path: Path, meter: int = 4) -> dict:
    y = _decode(path, SR_ANALYSIS, 1)
    dur = len(y) / SR_ANALYSIS
    full, low, times = onset_envelope(y)
    bpm, conf = estimate_tempo(full)
    bf = track_beats(full, bpm)
    beats = times[bf] if len(bf) else np.array([])
    # 亚帧细化:拍点帧附近起音包络做抛物线插值
    step = HOP / SR_ANALYSIS
    for k, f in enumerate(bf):
        if 0 < f < len(full) - 1:
            a_, b_, c_ = full[f - 1], full[f], full[f + 1]
            den = a_ - 2 * b_ + c_
            if den < 0:
                beats[k] += float(np.clip(0.5 * (a_ - c_) / den, -0.5, 0.5)) * step
    if len(beats) >= 4:                         # 速度用拍点对序号的最小二乘斜率(比相邻拍距中位数少受帧量化影响)
        idx = np.arange(len(beats))
        bpm = 60.0 / float(np.polyfit(idx, beats, 1)[0])
    phase, downbeats = 0, beats[::meter]
    if len(bf) >= meter * 2:
        strength = [low[bf[p::meter]].mean() + 0.5 * full[bf[p::meter]].mean() for p in range(meter)]
        phase = int(np.argmax(strength))
        downbeats = beats[phase::meter]
    return {"schema": SCHEMA, "duration_s": round(dur, 3), "bpm": round(float(bpm), 2), "meter": meter,
            "confidence": round(conf, 3), "beats": [round(float(t), 4) for t in beats],
            "downbeats": [round(float(t), 4) for t in downbeats], "downbeat_phase": phase}


# ---------------------------------------------------------------- 选段适配

def _lead_in(y: np.ndarray, sr: int, floor_db: float = -40.0) -> float:
    win = max(1, int(0.05 * sr))
    n = len(y) // win
    if n == 0:
        return 0.0
    rms = np.sqrt((y[:n * win].reshape(n, win) ** 2).mean(axis=1) + 1e-12)
    peak = rms.max()
    on = np.nonzero(20 * np.log10(rms / peak + 1e-12) > floor_db)[0]
    return float(on[0] * win / sr) if len(on) else 0.0


def fit(src: Path, out_wav: Path, dur: float, start: float | str = "auto", fade_out: float = 1.5, meter: int = 4) -> dict:
    """从 src 选一段长 dur 的音乐写到 out_wav(48 kHz 立体声),返回节拍网格(片子时间)与选段信息。"""
    src = Path(src)
    a = analyze(src, meter)
    stereo = _decode(src, SR_OUT, 2)
    src_dur = len(stereo) / SR_OUT
    mono = stereo.mean(axis=1)
    lead = _lead_in(mono, SR_OUT)
    downs = [d for d in a["downbeats"] if d >= lead - 0.05] or [lead]
    if start == "auto":
        s0 = next((d for d in downs if src_dur - d >= dur), downs[0])
    else:
        s0 = float(start)
        if not 0 <= s0 < src_dur:
            raise CodeVideoError(f"--start {s0} 超出曲长 {src_dur:.1f} s")
    need = int(round(dur * SR_OUT))
    i0 = int(round(s0 * SR_OUT))
    seg = stereo[i0:]
    looped = False
    if len(seg) < need:
        # 不够长:取 s0 之后的最后一个强拍作循环终点(至少两小节),强拍到强拍首尾 30 ms 交叉淡化接起来
        later = [d for d in a["downbeats"] if d > s0 + 1.0]
        if not later:
            raise CodeVideoError(f"{src.name} 只有 {src_dur:.1f} s,从 {s0:.2f} s 起不够 {dur:.1f} s,又找不到可循环的小节")
        loop = stereo[i0:int(round(later[-1] * SR_OUT))]
        xf = min(int(0.03 * SR_OUT), len(loop) // 4)
        out = loop.copy()
        ramp = np.linspace(0, 1, xf, dtype=np.float32)[:, None]
        while len(out) < need:
            nxt = loop.copy()
            out[-xf:] = out[-xf:] * (1 - ramp) + nxt[:xf] * ramp
            out = np.concatenate([out, nxt[xf:]])
        seg, looped = out, True
    seg = seg[:need].astype(np.float32).copy()
    fi = min(int(0.01 * SR_OUT), len(seg))
    seg[:fi] *= np.linspace(0, 1, fi, dtype=np.float32)[:, None]
    fo = min(int(fade_out * SR_OUT), len(seg))
    if fo > 0:
        seg[-fo:] *= (0.5 + 0.5 * np.cos(np.linspace(0, np.pi, fo, dtype=np.float32)))[:, None]
    import soundfile as sf
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out_wav), seg, SR_OUT, subtype="PCM_24")
    if looped:
        grid = analyze(out_wav, meter)
    else:
        grid = dict(a, beats=[round(b - s0, 4) for b in a["beats"] if -1e-3 <= b - s0 <= dur + 1e-3],
                    downbeats=[round(b - s0, 4) for b in a["downbeats"] if -1e-3 <= b - s0 <= dur + 1e-3],
                    duration_s=round(dur, 3))
    grid["source"] = {"file": str(src), "sha256": sha256(src), "duration_s": round(src_dur, 3), "start_s": round(s0, 4),
                      "looped": looped, "lead_in_s": round(lead, 3)}
    grid["audio_sha256"] = sha256(out_wav)
    grid["fitted_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    return grid


def beats_path(base: Path) -> Path:
    return base / NS / "music" / "beats.json"


def load_beats(base: Path) -> dict | None:
    try:
        return json.loads(beats_path(base).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_beats(base: Path, grid: dict) -> Path:
    p = beats_path(base)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(grid, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


# ---------------------------------------------------------------- 时间轴对齐

def _nearest(grid: list[float], t: float) -> float | None:
    if not grid:
        return None
    a = np.asarray(grid)
    return float(a[int(np.argmin(np.abs(a - t)))])


def _warp(pairs: list[tuple[float, float]]):
    xs = np.array([p[0] for p in pairs])
    ys = np.array([p[1] for p in pairs])
    return lambda t: float(np.interp(t, xs, ys))


def snap(raw: dict, grid: dict, sections: str = "downbeat", shots: str = "beat", hits: str = "beat",
         min_section_s: float = 0.5, min_shot_s: float = 0.25) -> tuple[dict, dict]:
    """返回 (新 timeline 原始 dict, 报告)。raw 是 timeline.json 原文(未经 validate 派生字段)。"""
    beats = [float(b) for b in grid.get("beats") or []]
    downs = [float(b) for b in grid.get("downbeats") or []]
    if len(beats) < 4:
        raise CodeVideoError("节拍网格里拍点太少(< 4),无法对齐;换一首节奏清楚的音乐,或 --sections off 只对击点")
    period = 60.0 / float(grid.get("bpm") or 120)
    tl = json.loads(json.dumps(raw))
    dur = float(tl["dur"])
    secs = tl["sections"]
    rep = {"sections": [], "shots": [], "hits": [], "bpm": grid.get("bpm"), "max_shift_s": 0.0,
           "kept": {"sections": [], "hits": []}}
    # 1) 段边界 → 强拍(容差半小节)/ 拍点(容差半拍),分段线性映射带动段内一切时刻
    pairs = [(0.0, 0.0)]
    meter = int(grid.get("meter") or 4)
    for s in secs[1:]:
        old = float(s["t0"])
        new = None
        if sections != "off":
            d = _nearest(downs, old) if sections == "downbeat" else None
            if d is not None and abs(d - old) <= period * meter / 2:
                new = d
            else:
                b = _nearest(beats, old)
                if b is not None and abs(b - old) <= period / 2:
                    new = b
        if new is None or new <= pairs[-1][1] + min_section_s or new >= dur - min_section_s:
            if sections != "off":
                rep["kept"]["sections"].append(s["id"])     # 附近没有拍 / 会短于最短段长:原地保留,机检豁免
            new = old if old > pairs[-1][1] + min_section_s else None
        if new is None:
            continue
        pairs.append((old, new))
        if new != old:
            rep["sections"].append({"id": s["id"], "from": round(old, 3), "to": round(new, 3)})
    pairs.append((dur, dur))
    w = _warp(pairs)
    for s in secs:
        s["t0"], s["t1"] = round(w(s["t0"]), 4), round(w(s["t1"]), 4)
    for sh in tl.get("shots") or []:
        sh["t0"], sh["t1"] = round(w(sh["t0"]), 4), round(w(sh["t1"]), 4)
    for k in ("lines", "hits", "black_ok"):
        for x in tl.get(k) or []:
            if isinstance(x, dict) and isinstance(x.get("t"), (int, float)):
                x["t"] = round(w(x["t"]), 4)
    if isinstance(tl.get("poster_t"), (int, float)):
        tl["poster_t"] = round(w(tl["poster_t"]), 4)
    # 2) 段内镜头切点 → 拍点(容差半拍),保持首尾相接与最短镜长
    if shots != "off":
        by_sec: dict[str, list[dict]] = {}
        for sh in tl.get("shots") or []:
            by_sec.setdefault(sh["section"], []).append(sh)
        sec_by_id = {s["id"]: s for s in secs}
        for sid, rows in by_sec.items():
            rows.sort(key=lambda r: r["t0"])
            for prev, cur in zip(rows, rows[1:]):
                old = cur["t0"]
                b = _nearest(beats, old)
                if b is None or abs(b - old) > period / 2:
                    continue
                if b - prev["t0"] < min_shot_s or cur["t1"] - b < min_shot_s:
                    continue
                if not sec_by_id[sid]["t0"] < b < sec_by_id[sid]["t1"]:
                    continue
                prev["t1"] = cur["t0"] = round(b, 4)
                rep["shots"].append({"id": cur["id"], "from": round(old, 3), "to": round(b, 3)})
    # 3) 击点 → 拍点(容差半拍)
    if hits != "off":
        for h in tl.get("hits") or []:
            if not isinstance(h, dict) or not isinstance(h.get("t"), (int, float)):
                continue
            b = _nearest(beats, h["t"])
            if b is not None and abs(b - h["t"]) <= period / 2:
                rep["hits"].append({"id": h.get("id") or "", "from": round(h["t"], 3), "to": round(b, 3)})
                h["t"] = round(b, 4)
            else:
                rep["kept"]["hits"].append(h.get("id") or f"@{h['t']:.2f}")
    rep["max_shift_s"] = round(max([abs(r["to"] - r["from"]) for k in ("sections", "shots", "hits") for r in rep[k]] or [0]), 3)
    tl["beat_sync"] = {"bpm": grid.get("bpm"), "meter": meter, "audio_sha256": grid.get("audio_sha256"),
                       "sections": sections, "shots": shots, "hits": hits, "kept": rep["kept"],
                       "snapped_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    return tl, rep


def snap_project(base: Path, sections: str = "downbeat", shots: str = "beat", hits: str = "beat", dry_run: bool = False) -> dict:
    from . import timeline as TL
    grid = load_beats(base)
    if not grid:
        raise CodeVideoError("缺少 codevideo/music/beats.json:先跑 codevideo.py music-fit(或 beats)")
    p = TL.path(base)
    raw = json.loads(p.read_text(encoding="utf-8"))
    new, rep = snap(raw, grid, sections, shots, hits)
    TL.validate(new)                                # 对齐后的时间轴必须仍合规
    rep["dry_run"] = dry_run
    if not dry_run:
        bak = p.with_name(f"timeline.before-snap-{time.strftime('%Y%m%d-%H%M%S')}.json")
        shutil.copyfile(p, bak)
        p.write_text(json.dumps(new, ensure_ascii=False, indent=1), encoding="utf-8")
        rep["backup"] = bak.relative_to(base).as_posix()
        rep["sync"] = TL.sync(base)
    return rep


def check(base: Path) -> list[dict]:
    """机检(外来音乐才有 beats.json):网格与当前 score.wav 一致;击点与段边界落拍(≤ 1 帧)。
    timeline.json#beat_sync 里设成 off 的类别、snap 时附近没有拍而原地保留的段 / 击点(kept)豁免,在说明里列出。"""
    from . import timeline as TL
    grid = load_beats(base)
    if not grid:
        return []
    out = []
    score = base / NS / "music" / "score.wav"
    fresh = score.is_file() and grid.get("audio_sha256") == sha256(score)
    out.append({"id": "beats_match_score", "ok": fresh,
                "detail": "" if fresh else "beats.json 不是按当前 music/score.wav 测的:重跑 codevideo.py music-fit 或 beats"})
    try:
        tl = TL.load(base)
    except CodeVideoError as e:
        return out + [{"id": "hits_on_beat", "ok": False, "detail": str(e)}]
    bs = tl.get("beat_sync") if isinstance(tl.get("beat_sync"), dict) else {}
    kept = bs.get("kept") if isinstance(bs.get("kept"), dict) else {}
    tol = 1.0 / tl["fps"] + 1e-6
    beats = grid.get("beats") or []

    def verdict(cid, kind, items, total):
        if bs.get(kind) == "off":
            return {"id": cid, "ok": True, "detail": f"snap 时 --{kind} off(按方案刻意不对拍),豁免"}
        waived = [x for x in items if x[0] in (kept.get(kind) or [])]
        bad = [x for x in items if x not in waived]
        note = (";附近没有拍、原地保留:" + ", ".join(w[1] for w in waived[:6])) if waived else ""
        return {"id": cid, "ok": not bad,
                "detail": (f"{total} 个" + ("击点" if kind == "hits" else "段边界") + note) if not bad
                else "不在拍上:" + ", ".join(b[1] for b in bad[:8]) + note}

    hits = [h for h in tl.get("hits") or [] if isinstance(h, dict) and isinstance(h.get("t"), (int, float))]
    off_h = [(h.get("id") or f"@{h['t']:.2f}", f"{h.get('id') or ''}@{h['t']:.2f}s") for h in hits
             if (b := _nearest(beats, h["t"])) is not None and abs(b - h["t"]) > tol]
    out.append(verdict("hits_on_beat", "hits", off_h, len(hits)))
    off_s = [(s["id"], f"{s['id']}@{s['t0']:.2f}s") for s in tl["sections"][1:]
             if (b := _nearest(beats, s["t0"])) is not None and abs(b - s["t0"]) > tol]
    out.append(verdict("sections_on_beat", "sections", off_s, len(tl["sections"]) - 1))
    return out


# ---------------------------------------------------------------- 项目级:选曲适配 + 登记使用记录

def resolve_source(base: Path, track: str = "", file: str = "") -> tuple[Path, dict]:
    """(音频文件, cue sheet 的 license 段)。track = 项目音乐库曲目号 MUS-NNNN;file = 项目内路径(refs/music/… 或
    codevideo/music/… 下音乐模型生成的原始文件)或绝对路径。"""
    if bool(track) == bool(file):
        raise CodeVideoError("music-fit 需要 --track <MUS-NNNN>(项目音乐库)或 --file <音频路径>(二选一)")
    if track:
        from modules import music_library as ML
        idx = ML.load_index(base)
        t = ML.find_track(idx, track.upper())
        if t is None:
            raise CodeVideoError(f"项目音乐库里没有 {track}(python3 code/music_library.py search --project {base.name} 查)")
        p = ML.track_file(base, t)
        if not p.is_file():
            raise CodeVideoError(f"曲目文件不在了:{p.relative_to(base)}")
        return p, {**ML.license_block(t), "title": t.get("title") or "", "mood": t.get("mood") or ""}
    p = Path(file)
    p = p if p.is_absolute() else (base / p)
    if not p.is_file():
        raise CodeVideoError(f"音频文件不存在:{file}")
    try:
        rel = p.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        rel = str(p)
    meta = {}
    mp = p.with_suffix(".meta.json")
    if mp.is_file():
        try:
            meta = (json.loads(mp.read_text(encoding="utf-8")) or {}).get("music") or {}
        except ValueError:
            meta = {}
    if rel.startswith("refs/"):
        return p, {"source": "user_provided", "file": rel}
    return p, {"source": "generated", "file": rel, "provider": meta.get("provider") or "", "model": meta.get("model") or "",
               "prompt": meta.get("prompt") or ""}


def fit_project(base: Path, track: str = "", file: str = "", start: float | str = "auto", fade_out: float = 1.5,
                mood: str = "", log=print) -> dict:
    """选曲适配到片长 → codevideo/music/score.wav + beats.json;写 assets/audio/bgm/ep01/cue_sheet.json 一条 cue 并同步项目音乐库
    (曲目使用记录、模型生成的新曲入库,与影视剧 BGM 同一套台账)。"""
    from . import EP
    from . import timeline as TL
    tl = TL.load(base)
    src, lic = resolve_source(base, track, file)
    out = base / NS / "music" / "score.wav"
    grid = fit(src, out, float(tl["dur"]), start, fade_out, int(tl.get("meter") or 4))
    grid["license"] = lic
    write_beats(base, grid)
    res = {"score": out.relative_to(base).as_posix(), "bpm": grid["bpm"], "beats": len(grid["beats"]),
           "downbeats": len(grid["downbeats"]), "start_s": grid["source"]["start_s"], "looped": grid["source"]["looped"],
           "confidence": grid["confidence"]}
    # 使用记录:cue sheet 一条 cue(文件 = 适配后的那段),music_library.sync_episode 对账
    bgm = base / "assets" / "audio" / "bgm" / EP
    bgm.mkdir(parents=True, exist_ok=True)
    cue_file = f"{EP}_bgm_01.wav"
    shutil.copyfile(out, bgm / cue_file)
    cue = {"cue_id": f"{EP}_bgm_01", "file": cue_file, "in_s": 0.0, "out_s": float(tl["dur"]),
           "duration_s": float(tl["dur"]), "tempo_bpm": grid["bpm"], "mood": mood or lic.get("mood") or "",
           "title": lic.get("title") or "", "status": "active", "license": {k: v for k, v in lic.items() if k not in ("title", "mood")},
           "fit": {"source_start_s": grid["source"]["start_s"], "looped": grid["source"]["looped"], "fade_out_s": fade_out}}
    sheet = {"episode": EP, "generator": "code/codevideo.py music-fit", "written_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
             "cues": [cue]}
    (bgm / "cue_sheet.json").write_text(json.dumps(sheet, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        from modules import music_library as ML
        idx = ML.load_index(base)
        rep = ML.sync_episode(base, idx, EP)
        ML.save_index(base, idx)
        res["library"] = {k: rep.get(k) for k in ("added", "reused", "derived", "missing_files", "unknown_tracks")}
    except Exception as e:  # noqa: BLE001 — 台账同步失败不影响配乐本身
        res["library_error"] = str(e)[:300]
        log(f"! 项目音乐库同步失败(配乐已写好):{e}")
    return res
