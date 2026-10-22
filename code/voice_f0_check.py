#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""voice_f0_check.py — 对白组逐说话人声学快检 voice_f0(#148,2026-10-22;WORKFLOW.md §8A)。

不转写、不下载模型(规约禁止自发 ASR):只比基频。对白组出片后,把组 clip 里每个说话人开口时段的基频(F0)中位数
和该说话人自己的 voiceprint 样本(组 prompt 的 audio_refs)的基频中位数比,偏离太大 = 模型没按音色锚出声
(前科:DEF-p7-video-voice-binding 第二说话人漂移;dzg2 ep01 grp004 / grp010 两说话人音色交叉 / 坍缩到同一人)。

适用条件(不满足时整体 SKIP、退出码 0;只有三条同时成立才跑):
  ① 本组 audio_plan == dialogue(shot_list generation_groups[].audio_plan,缺省时看组 prompt;都没写按有无画内句);
  ② 项目「输出设置 → 对白配音」= 视频原声(output.dialogue_voice 缺省 / native;后期配音时组 clip 对白轨由 p7-dub 换掉,不检);
  ③ 本组 prompt(assets/prompts/epNN/grpNNN.json)挂了 audio_refs。

分段:组内各镜时窗取 clip meta 的 boundary_map(出片实测切点),缺的镜按 shot_list duration_s 累计(与
check_dialogue_audible / dub_group 同口径);每镜只看画内句(placement=on,os/vo 画外句不在 clip 原生人声里);
一镜只有一个说话人 = 整镜时窗归他;一镜多人 = 按台词估时(est_duration_s,缺省按字数)比例切(split=true,近似);
首句标了 native_lead 的镜,时窗向前并入前一镜。参照样本:audio_refs 里文件名含该说话人编号的 voiceprint
(<CHAR>[_<形态>]_voiceprint.*,modules/voice_variants.parse_voiceprint)。

F0 估计:16 kHz 单声道,40 ms 窗 / 10 ms 跳,YIN 累积均值归一化差分函数(CMNDF)取第一个低于门限的谷 + 抛物线插值,
只统计过能量门限且 CMNDF 谷 < YIN_THRESHOLD 的浊音帧;片段、样本走同一实现(数值可直接比)。

判定(每个说话人,把他所有片段的浊音帧并起来取中位数;偏离 = max(片段, 样本) / min(片段, 样本) − 1,升降对称):
  偏离 ≤ WARN_DEV(20%)               PASS
  WARN_DEV < 偏离 ≤ FAIL_DEV(50%)     WARN  只记回执(情绪抬高 / 压低、样本与模型演绎的固定落差多在这一档)
  偏离 > FAIL_DEV                    FAIL  进重 roll 流程(受「重跑次数」设定限制)
  多说话人组里两人互相更接近对方的样本(两样本本身相差 > WARN_DEV、至少一人偏离 > WARN_DEV)→ 两人都升 FAIL
  (疑似 [Audio N] 交叉套用);只有一方更接近别人的样本只在明细记 nearest_ref,仍按偏离定档
  片段或样本浊音帧 < MIN_VOICED_FRAMES、或 audio_refs 里没有该说话人的样本  SKIP(不判)
阈值依据见 WARN_DEV / FAIL_DEV 注释。

用法:
  python3 code/voice_f0_check.py --project <slug> --ep ep01 --group grp012 [--json]
  python3 code/voice_f0_check.py --project <slug> assets/clips/ep01/grp012.mp4 --seg 0.3:5.4:CHAR-0002 [--seg …] \
      [--ref CHAR-0002=assets/audio/voice/refs/CHAR-0002_voiceprint.mp3] [--json]
      # 单片段形态:手工给段;--ref 缺省时按 clip 路径找同名组 prompt 的 audio_refs,再退回 refs/<CHAR>_voiceprint.mp3
退出码:0 = PASS / WARN / SKIP;1 = 有说话人 FAIL;2 = 参数或数据错误(项目名非法、路径越出项目、缺 shot_list / clip、ffmpeg 失败)。
只读:不写任何文件(要留档用 --json 重定向)。
"""
from __future__ import annotations

import json
import math
import re
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args, project_output_setting  # noqa: E402  (副作用:modules/ 入 sys.path)

CHECK = "voice_f0"

SR = 16000
WIN_S, HOP_S = 0.040, 0.010        # 40 ms 窗容纳 60 Hz 下限的 2 个周期;10 ms 跳
F0_MIN, F0_MAX = 60.0, 500.0       # 低沉老年男声 ~60 Hz 至童声 / 激动女声 ~500 Hz
YIN_THRESHOLD = 0.25               # CMNDF 谷低于此值才算浊音(YIN 原文 0.10–0.15 针对干净录音;成片带环境声 / 音效要放宽:
                                   # 真实片段上 0.20 / 0.25 / 0.30 与 Praat 片段中位数 p90 差 11% / 9% / 7%,但 40–300 Hz 低频轰鸣
                                   # 误判浊音 0.6% / 3% / 5%,取 0.25)
ENERGY_REL = 0.05                  # 帧 RMS 低于全文件帧 RMS 95 分位 × 此值(≈ −26 dB)视为静音 / 底噪
ENERGY_FLOOR = 0.002               # 绝对能量下限(≈ −54 dBFS)
MIN_VOICED_FRAMES = 30             # 浊音帧 < 0.3 s 的片段 / 样本中位数不稳,记 SKIP 不判

# 阈值依据(2026-10-22 本机 8 个项目 263 个对白组 / 405 个说话人只读校准,明细见提交说明):
# · WARN_DEV 0.20:Agent 自写的 5 版 voice_f0_check 与历史缺陷单都按 ±20% 判错配;同一角色跨组的片段中位数围绕
#   它自己的中位数离散 p50 12% / p75 24%,情绪句(喊、哭、兴奋)可到 20–40%(fengshen3 ep06 grp019 哪吒偏高一档交用户试听),
#   单凭基频分不清「演得激动」和「换了嗓音」,这一档只记 WARN。
# · FAIL_DEV 0.50(原拟 0.35):按 35% 判,27% 的说话人、32% 的组 FAIL(Praat 复测 25% / 31%)——35–50% 这档主要是
#   样本与模型演绎之间的固定落差(同一角色 ≥3 组时,片段中位数相对样本的固定偏移 p75 27%、p90 49%,如 fengshen3
#   CHAR-0042 10 组一致偏低 26%)和情绪峰值,整组重 roll 修不掉,只会空耗付费重跑额度;50%(≈ 7 个半音)以上多是
#   成年男声 ↔ 女声 / 童声这类明显换了嗓音的情形(如成年男声样本 136 Hz 出片 272 Hz,老妇样本 209 Hz 出片 412 Hz)。
WARN_DEV = 0.20
FAIL_DEV = 0.50

_SAFE_NAME = re.compile(r"[\w-]{1,80}")
_SEG = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*[:-]\s*(\d+(?:\.\d+)?)\s*:\s*(\S+)\s*$")
_STATUS_RANK = {"SKIP": 0, "PASS": 1, "WARN": 2, "FAIL": 3}


class CheckError(Exception):
    """参数 / 数据错误(退出码 2)。"""


# ---------------------------------------------------------------- 路径与项目数据

def _safe_name(value, what: str) -> str:
    v = str(value or "")
    if not _SAFE_NAME.fullmatch(v):
        raise CheckError(f"{what} 只能是名字(字母数字 _ -),不能是路径:{value!r}")
    return v


def inside(base: Path, rel) -> Path:
    """把项目内相对路径(或项目内绝对路径)解析成绝对路径;resolve 后越出项目根 = 拒绝。"""
    p = Path(str(rel))
    p = (p if p.is_absolute() else base / p).resolve()
    if not p.is_relative_to(base):
        raise CheckError(f"路径越出项目目录:{rel}")
    return p


def _rel(base: Path, p: Path) -> str:
    try:
        return str(p.relative_to(base))
    except ValueError:
        return str(p)


def _read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
    except (ValueError, OSError):
        return None


def _placement(ln: dict) -> str:
    try:
        from modules import offscreen_lines as osl
    except ImportError:   # pragma: no cover
        try:
            import offscreen_lines as osl
        except ImportError:
            return "on"
    return osl.placement(ln)


def _parse_voiceprint(name):
    try:
        from modules.voice_variants import parse_voiceprint
    except ImportError:   # pragma: no cover
        from voice_variants import parse_voiceprint
    return parse_voiceprint(name)


def _text_weight(t: str) -> float:
    cjk = len(re.findall(r"[㐀-鿿぀-ヿ가-힯]", t))
    words = len(re.findall(r"[A-Za-z0-9À-ɏЀ-ӿ']+", t))
    return float(max(1, cjk + words))


def on_lines(shot: dict) -> list[dict]:
    """本镜画内台词(有文本、placement=on);os / vo 画外句由后期合成,不在 clip 原生人声里。"""
    out = []
    for ln in (shot or {}).get("dialogue_lines") or []:
        if not isinstance(ln, dict):
            continue
        text = str(ln.get("text") or ln.get("line") or "").strip()
        spk = ln.get("speaker") or ln.get("character_id")
        if text and spk and _placement(ln) == "on":
            out.append({"speaker": str(spk), "text": text, "est": float(ln.get("est_duration_s") or 0) or None,
                        "native_lead": ln.get("native_lead") if isinstance(ln.get("native_lead"), dict) else None})
    return out


def shot_windows(group: dict, shots: dict, meta: dict) -> dict:
    """组内各镜在 clip 里的时窗:meta boundary_map 优先,缺的镜按 shot_list duration_s 累计。"""
    bmap = {}
    for b in (meta or {}).get("boundary_map") or []:
        try:
            bmap[b.get("shot_id")] = (float(b.get("start_s")), float(b.get("end_s")))
        except (TypeError, ValueError):
            continue
    out, t = {}, 0.0
    for sid in group.get("shots") or []:
        dur = float((shots.get(sid) or {}).get("duration_s") or 0)
        out[sid] = bmap.get(sid, (t, t + dur))
        t += dur
    return out


def plan_segments(group: dict, shots: dict, windows: dict) -> list[dict]:
    """→ [{speaker, shot_id, start_s, end_s, split}]:每个有画内句的镜按说话人切时段。"""
    segs = []
    for sid in group.get("shots") or []:
        lines = on_lines(shots.get(sid) or {})
        if not lines:
            continue
        a, b = windows.get(sid, (0.0, 0.0))
        nl = lines[0].get("native_lead")
        if nl and nl.get("shot") in windows:          # 原生先入:人声从前一镜起
            a = min(a, windows[nl["shot"]][0])
        if b <= a:
            continue
        speakers = list(dict.fromkeys(ln["speaker"] for ln in lines))
        if len(speakers) == 1:
            segs.append({"speaker": speakers[0], "shot_id": sid, "start_s": round(a, 3), "end_s": round(b, 3), "split": False})
            continue
        weights = [ln["est"] or _text_weight(ln["text"]) for ln in lines]
        total, t = sum(weights), a
        for ln, w in zip(lines, weights):
            e = t + (b - a) * w / total
            if segs and segs[-1]["shot_id"] == sid and segs[-1]["speaker"] == ln["speaker"]:
                segs[-1]["end_s"] = round(e, 3)
            else:
                segs.append({"speaker": ln["speaker"], "shot_id": sid, "start_s": round(t, 3), "end_s": round(e, 3), "split": True})
            t = e
    return segs


# ---------------------------------------------------------------- 音频与 F0

def load_audio(src: Path):
    """任意音频 / 视频 → 16 kHz 单声道 float64 numpy 数组(ffmpeg 列表传参,不经 shell)。"""
    import numpy as np
    from voice_activity import extract_wav
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "a.wav"
        try:
            extract_wav(src, wav)
        except FileNotFoundError as e:
            raise CheckError("缺少 ffmpeg(macOS: brew install ffmpeg)") from e
        except Exception as e:  # noqa: BLE001  (CalledProcessError:无音轨 / 文件坏)
            raise CheckError(f"抽音轨失败:{src.name}({e})") from e
        with wave.open(str(wav)) as w:
            x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float64) / 32768.0
    return x


def f0_track(x, sr: int = SR):
    """YIN(简化):→ (times_s, f0_hz) 两个等长数组,只含浊音帧。"""
    import numpy as np
    win, hop = int(round(sr * WIN_S)), int(round(sr * HOP_S))
    tau_min, tau_max = max(2, int(sr / F0_MAX)), int(sr / F0_MIN)
    if x is None or len(x) < win + hop:
        return np.zeros(0), np.zeros(0)
    frames = np.lib.stride_tricks.sliding_window_view(x, win)[::hop]
    frames = frames - frames.mean(axis=1, keepdims=True)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    gate = max(ENERGY_FLOOR, ENERGY_REL * float(np.percentile(rms, 95)))
    keep = np.nonzero(rms >= gate)[0]
    if keep.size == 0:
        return np.zeros(0), np.zeros(0)
    fr = frames[keep]
    nfft = 1 << int(math.ceil(math.log2(2 * win)))
    spec = np.fft.rfft(fr, nfft, axis=1)
    acf = np.fft.irfft(spec * np.conj(spec), nfft, axis=1)[:, :tau_max + 2]
    csum = np.concatenate([np.zeros((fr.shape[0], 1)), np.cumsum(fr ** 2, axis=1)], axis=1)
    taus = np.arange(tau_max + 2)
    e0 = csum[:, win - taus]                       # Σ_{j<W-τ} x_j²
    e1 = csum[:, [win]] - csum[:, taus]            # Σ_{j≥τ} x_j²
    d = np.maximum(e0 + e1 - 2 * acf, 0.0)
    cm = np.ones_like(d)
    run = np.cumsum(d[:, 1:], axis=1)
    cm[:, 1:] = d[:, 1:] * taus[1:] / np.maximum(run, 1e-12)
    times, f0s = [], []
    for row, idx in zip(cm, keep):
        below = np.nonzero(row[tau_min:tau_max + 1] < YIN_THRESHOLD)[0]
        if below.size == 0:
            continue
        t = int(below[0]) + tau_min
        while t + 1 <= tau_max and row[t + 1] < row[t]:   # 顺坡走到谷底
            t += 1
        a, b, c = row[t - 1], row[t], row[t + 1]
        den = a - 2 * b + c
        shift = 0.5 * (a - c) / den if abs(den) > 1e-12 else 0.0
        tau = t + max(-1.0, min(1.0, shift))
        f = sr / tau
        if F0_MIN <= f <= F0_MAX:
            times.append((idx * hop + win / 2) / sr)
            f0s.append(f)
    return np.asarray(times), np.asarray(f0s)


def _stats(vals) -> dict:
    import numpy as np
    v = np.asarray(vals, dtype=float)
    if v.size == 0:
        return {"voiced_frames": 0, "f0_median_hz": None}
    return {"voiced_frames": int(v.size), "f0_median_hz": round(float(np.median(v)), 1),
            "f0_p25_hz": round(float(np.percentile(v, 25)), 1), "f0_p75_hz": round(float(np.percentile(v, 75)), 1)}


def deviation(clip_hz: float, ref_hz: float) -> float:
    """相对偏离,升降对称:max/min − 1(120→150 与 150→120 都是 0.25)。"""
    hi, lo = max(clip_hz, ref_hz), min(clip_hz, ref_hz)
    return hi / lo - 1.0


def classify(dev: float) -> str:
    return "FAIL" if dev > FAIL_DEV else "WARN" if dev > WARN_DEV else "PASS"


# ---------------------------------------------------------------- 判定

def judge(clip_track, segments: list[dict], refs: dict, ref_tracks: dict) -> list[dict]:
    """segments=[{speaker,start_s,end_s,split,shot_id?}];refs={speaker: 相对路径};ref_tracks={speaker: f0 数组}。
    → 逐说话人明细(含逐片段)。"""
    import numpy as np
    times, f0s = clip_track
    ref_stats = {spk: _stats(arr) for spk, arr in ref_tracks.items()}
    rows = []
    for spk in dict.fromkeys(s["speaker"] for s in segments):
        mine = [s for s in segments if s["speaker"] == spk]
        seg_rows, clean, mixed = [], [], []
        for s in mine:
            m = (times >= s["start_s"]) & (times < s["end_s"])
            vals = f0s[m]
            (mixed if s.get("split") else clean).append(vals)
            seg_rows.append(dict(s, **_stats(vals)))
        clean_v = np.concatenate(clean) if clean else np.zeros(0)
        use_split = clean_v.size < MIN_VOICED_FRAMES and mixed
        vals = np.concatenate([clean_v] + mixed) if use_split else clean_v
        row = {"speaker": spk, "ref": refs.get(spk), "segments": seg_rows,
               "basis": "incl_split" if use_split else "clean", **_stats(vals)}
        rs = ref_stats.get(spk)
        if rs:
            row["ref_f0_median_hz"], row["ref_voiced_frames"] = rs["f0_median_hz"], rs["voiced_frames"]
        if not refs.get(spk):
            row.update(status="SKIP", reason="本组 audio_refs 里没有该说话人的 voiceprint 样本(audioref_bound 另检)")
        elif not rs or rs["voiced_frames"] < MIN_VOICED_FRAMES:
            row.update(status="SKIP", reason=f"参照样本浊音帧不足 {MIN_VOICED_FRAMES}(样本太短 / 无人声)")
        elif row["voiced_frames"] < MIN_VOICED_FRAMES:
            row.update(status="SKIP", reason=f"片段浊音帧 {row['voiced_frames']} < {MIN_VOICED_FRAMES}(台词太短或被音效盖住),不判")
        else:
            dev = deviation(row["f0_median_hz"], rs["f0_median_hz"])
            row.update(deviation=round(dev, 3), direction="higher" if row["f0_median_hz"] >= rs["f0_median_hz"] else "lower",
                       status=classify(dev))
        rows.append(row)
    # 串音色复判:片段离同组另一说话人的样本更近(两样本本身可分)记 nearest_ref;两人互相更接近对方的样本 = 疑似对调,
    # 两人都升 FAIL。只有一方更近不升档——样本与模型演绎常有固定落差(dzg2 王三合各组一致比样本低 ~25%,就总是「更接近」
    # 老道儿的样本),单向判据会把这类组每组都打 FAIL。
    judged = {r["speaker"]: r for r in rows if r["status"] in ("PASS", "WARN", "FAIL")}
    for r in judged.values():
        own = r["ref_f0_median_hz"]
        others = {k: o["ref_f0_median_hz"] for k, o in judged.items() if k != r["speaker"] and deviation(own, o["ref_f0_median_hz"]) > WARN_DEV}
        if not others:
            continue
        near = min(others, key=lambda k: abs(math.log(r["f0_median_hz"] / others[k])))
        if abs(math.log(r["f0_median_hz"] / others[near])) < abs(math.log(r["f0_median_hz"] / own)):
            r["nearest_ref"] = near
    for r in judged.values():
        o = judged.get(r.get("nearest_ref"))
        if o and o.get("nearest_ref") == r["speaker"] and max(r["deviation"], o["deviation"]) > WARN_DEV:
            r["swap_with"] = o["speaker"]
            r["status"] = "FAIL"
            r["reason"] = f"与 {o['speaker']} 互相更接近对方的样本,疑似两人音色对调([Audio N] 交叉套用)"
    return rows


def _overall(rows: list[dict]) -> str:
    return max((r["status"] for r in rows), key=_STATUS_RANK.get, default="SKIP")


def _report(base: Path, **kw) -> dict:
    rep = {"check": CHECK, "project": base.name,
           "thresholds": {"warn_dev": WARN_DEV, "fail_dev": FAIL_DEV, "min_voiced_frames": MIN_VOICED_FRAMES,
                          "f0_range_hz": [F0_MIN, F0_MAX]}}
    rep.update(kw)
    rep["pass"] = rep.get("status") != "FAIL"
    return rep


def _ref_tracks(base: Path, refs: dict) -> dict:
    cache, out = {}, {}
    for spk, rel in refs.items():
        if rel not in cache:
            p = inside(base, rel)
            if not p.is_file():
                raise CheckError(f"参照样本不存在:{rel}")
            cache[rel] = f0_track(load_audio(p))[1]
        out[spk] = cache[rel]
    return out


def refs_from_prompt(prompt: dict | None) -> dict:
    """audio_refs → {人物编号: 相对路径}(同一人挂多条取第一条)。"""
    out = {}
    for ref in (prompt or {}).get("audio_refs") or []:
        hit = _parse_voiceprint(ref)
        if hit and hit[0] not in out:
            out[hit[0]] = str(ref)
    return out


def check_group(base: Path, ep: str, gid: str) -> dict:
    sl = _read_json(inside(base, f"directing/{ep}/shot_list.json"))
    if not isinstance(sl, dict):
        raise CheckError(f"缺 directing/{ep}/shot_list.json 或无法解析")
    group = next((g for g in sl.get("generation_groups") or [] if g.get("group_id") == gid), None)
    if group is None:
        raise CheckError(f"shot_list 无组 {gid}")
    shots = {s.get("shot_id"): s for s in sl.get("shots") or [] if isinstance(s, dict)}
    prompt = _read_json(inside(base, f"assets/prompts/{ep}/{gid}.json")) or {}
    audio_plan = group.get("audio_plan") or prompt.get("audio_plan")
    has_lines = any(on_lines(shots.get(sid) or {}) for sid in group.get("shots") or [])
    mode = project_output_setting(base, "dialogue_voice", "native") or "native"
    ctx = {"episode": ep, "group": gid, "audio_plan": audio_plan, "dialogue_voice": mode}
    skip = None
    if mode != "native":
        skip = f"对白配音={mode}(后期配音):组 clip 对白轨由 p7-dub 替换,不跑本检"
    elif audio_plan and audio_plan != "dialogue":
        skip = f"audio_plan={audio_plan}(非对白组)"
    elif not has_lines:
        skip = "本组没有画内台词"
    elif not prompt.get("audio_refs"):
        skip = "组 prompt 未挂 audio_refs(没有音色锚可比)"
    if skip:
        return _report(base, status="SKIP", reason=skip, speakers=[], **ctx)
    clip = inside(base, f"assets/clips/{ep}/{gid}.mp4")
    if not clip.is_file():
        raise CheckError(f"缺组 clip:assets/clips/{ep}/{gid}.mp4(未出片)")
    meta = _read_json(inside(base, f"assets/clips/{ep}/{gid}.meta.json")) or {}
    segments = plan_segments(group, shots, shot_windows(group, shots, meta))
    refs = refs_from_prompt(prompt)
    rows = judge(f0_track(load_audio(clip)), segments, refs,
                 _ref_tracks(base, {s: r for s, r in refs.items() if s in {x["speaker"] for x in segments}}))
    notes = []
    if not (meta.get("boundary_map")):
        notes.append("meta 无 boundary_map,时窗按 shot_list duration_s 累计(出片实际切点可能偏移)")
    return _report(base, status=_overall(rows), clip=_rel(base, clip), speakers=rows, notes=notes, **ctx)


def check_clip(base: Path, clip_arg: str, seg_specs: list[str], ref_specs: list[str]) -> dict:
    clip = inside(base, clip_arg)
    if not clip.is_file():
        raise CheckError(f"clip 不存在:{clip_arg}")
    if not seg_specs:
        raise CheckError("单片段形态须给 --seg 起:止:期望CHAR(可多次)")
    segments = []
    for spec in seg_specs:
        m = _SEG.match(spec)
        if not m or float(m.group(2)) <= float(m.group(1)):
            raise CheckError(f"--seg 格式应为 起:止:CHAR(秒,止 > 起):{spec!r}")
        segments.append({"speaker": _safe_name(m.group(3), "--seg 的人物编号"), "start_s": float(m.group(1)),
                         "end_s": float(m.group(2)), "split": False})
    refs = {}
    for spec in ref_specs:
        if "=" not in spec:
            raise CheckError(f"--ref 格式应为 CHAR=<项目内样本路径>:{spec!r}")
        spk, path = spec.split("=", 1)
        refs[_safe_name(spk.strip(), "--ref 的人物编号")] = path.strip()
    rel = _rel(base, clip)
    m = re.fullmatch(r"assets/clips/([\w-]+)/([\w-]+)\.mp4", rel)
    if m:
        for spk, r in refs_from_prompt(_read_json(inside(base, f"assets/prompts/{m.group(1)}/{m.group(2)}.json"))).items():
            refs.setdefault(spk, r)
    for spk in {s["speaker"] for s in segments}:
        if spk not in refs:
            hit = sorted((base / "assets" / "audio" / "voice" / "refs").glob(f"{spk}_voiceprint.*"))
            if hit:
                refs[spk] = _rel(base, hit[0])
    for spk in list(refs):
        inside(base, refs[spk])
    wanted = {s["speaker"] for s in segments}
    rows = judge(f0_track(load_audio(clip)), segments, refs, _ref_tracks(base, {s: r for s, r in refs.items() if s in wanted}))
    return _report(base, status=_overall(rows), clip=rel, speakers=rows, notes=[])


# ---------------------------------------------------------------- 输出

def _fmt_row(r: dict) -> str:
    if r["status"] == "SKIP":
        return f"[SKIP] {r['speaker']}: {r.get('reason', '')}"
    dev = r.get("deviation")
    line = (f"[{r['status']}] {r['speaker']}: 片段 F0 中位 {r['f0_median_hz']} Hz(浊音帧 {r['voiced_frames']}) / "
            f"样本 {r['ref_f0_median_hz']} Hz,偏离 {dev * 100:.1f}%({'偏高' if r.get('direction') == 'higher' else '偏低'})")
    if r.get("reason"):
        line += f";{r['reason']}"
    elif r.get("nearest_ref"):
        line += f";片段更接近 {r['nearest_ref']} 的样本(单向,不升档,交 audio-qa / 用户试听)"
    return line


def emit(rep: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return
    where = "/".join(x for x in (rep.get("episode"), rep.get("group")) if x) or rep.get("clip", "")
    print(f"=== voice_f0 声学快检 {rep.get('project')} {where} ===")
    if rep.get("reason"):
        print(f"[SKIP] {rep['reason']}")
    for r in rep.get("speakers") or []:
        print(_fmt_row(r))
        for s in r.get("segments") or []:
            tag = "(多人同镜按估时切)" if s.get("split") else ""
            print(f"       {s.get('shot_id') or '-'} {s['start_s']:.2f}–{s['end_s']:.2f}s 浊音帧 {s['voiced_frames']}"
                  f" F0 中位 {s.get('f0_median_hz')} Hz{tag}")
    for n in rep.get("notes") or []:
        print(f"[NOTE] {n}")
    print(f"[RESULT] voice_f0: {rep['status']}"
          f"(PASS ≤{WARN_DEV:.0%} < WARN ≤{FAIL_DEV:.0%} < FAIL;WARN 只记回执,FAIL 才进重 roll)")


def main(argv=None) -> int:
    def cfg(ap):
        ap.add_argument("clip", nargs="?", help="单片段形态:项目内 clip 路径(配 --seg)")
        ap.add_argument("--group", help="组号,如 grp012(按组自动分段)")
        ap.add_argument("--seg", action="append", default=[], help="单片段形态:起:止:期望CHAR(秒,可多次)")
        ap.add_argument("--ref", action="append", default=[], help="单片段形态:CHAR=<项目内样本路径>(可多次)")
        ap.add_argument("--json", action="store_true", help="机器可读输出")
    args, base = parse_args("对白组逐说话人声学快检 voice_f0(基频比对 voiceprint 样本)", configure=cfg, argv=argv)
    try:
        _safe_name(args.project, "--project")
        _safe_name(args.ep, "--ep")
        if args.group:
            _safe_name(args.group, "--group")
        base = Path(base).resolve()
        if not base.is_dir():
            raise CheckError(f"项目不存在:{args.project}")
        if args.clip:
            rep = check_clip(base, args.clip, args.seg, args.ref)
        elif args.group:
            rep = check_group(base, args.ep, args.group)
        else:
            raise CheckError("缺 --group(按组)或 <clip> --seg …(单片段)")
    except CheckError as e:
        if args.json:
            print(json.dumps({"check": CHECK, "status": "ERROR", "pass": False, "error": str(e)}, ensure_ascii=False, indent=2))
        else:
            print(f"[ERROR] voice_f0: {e}", file=sys.stderr)
        return 2
    emit(rep, args.json)
    return 1 if rep["status"] == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
