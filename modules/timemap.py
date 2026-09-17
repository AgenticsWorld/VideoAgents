# -*- coding: utf-8 -*-
"""timemap.py — 正片时间轴的「时长编辑」映射(2026-09-17,WORKFLOW.md §9C/§9D 节奏垫片)。

背景:成片正片的外挂声轨(assets/audio/final/epNN.wav)、字幕(subtitles.srt/.ass)、旁白挂点都以剪辑交付的
粗剪 cut_v1 为 0 秒基准。后期页的「插黑 / 定格」(组间黑场停留、前组尾帧定格、组内某时刻插黑)和「删段」
都会**改变正片时长**,基准随之失效。本模块把这类编辑记成一张确定性的时间映射表(ops),由宿主 CLI 在
出成片时按同一张表重映射声轨与字幕,Agent 不再手算偏移。

一条 op = 源基准上的一段区间 [src_t0, src_t1) 被替换成 out_len 秒的输出:
  插入(黑场/定格) src_t0 == src_t1, out_len > 0 ,可带 freeze_s / hold_s / audio(sustain|fade|mute)
  删除(删段)     src_t0 <  src_t1, out_len == 0
映射规则(map_time):源时刻 t 的输出时刻 = t + Σ(out_len − 区间长) 对所有 src_t1 <= t 的 op;
落在被删区间内的 t 折到该区间输出起点;恰在插入点上的 t(下一组首帧)排在插入块**之后**。

层的复合(compose):后期版本层(组内删段/插黑,基准 = 原粗剪)之上再叠组间垫片层(基准 = 后期拼出的
cut_post)。compose 把第二层的时刻经第一层逆映射折回原基准,得到一张总表,声轨/字幕只重映射一次。

音频重映射(remap_audio):按 ops 切开源声轨,插入块按策略生成——
  sustain  取插入点前 ≤1s 的源声轨循环铺满(两端 40ms 淡化);若 op 带 bed_file(场景床音)则循环床音;
  fade     插入块为静音,前段末 150ms 淡出、后段首 150ms 淡入;
  mute     插入块为数字静音(不淡化)。
「延续」策略是否合适(插入点前有无对白/旁白)由创建 op 的一方判定后写入 audio 字段;本模块只执行。
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

AUDIO_POLICIES = ("sustain", "fade", "mute")
SUSTAIN_WINDOW_S = 1.0        # 延续策略取插入点前最多 1 s 源声轨循环
SUSTAIN_MIN_S = 0.25
FADE_S = 0.15                 # fade 策略两侧淡化时长
EDGE_S = 0.04                 # 延续块两端淡化
EPS = 1e-6


# ---------------------------------------------------------------- op 规范化

def normalize_ops(ops) -> list[dict]:
    """收敛成 [{src_t0, src_t1, out_len, ...}],按 src_t0 排序;非法 / 零效果的 op 丢弃。"""
    out = []
    for o in ops or []:
        if not isinstance(o, dict):
            continue
        try:
            t0 = float(o.get("src_t0", o.get("src_t", 0.0)) or 0.0)
            t1 = float(o.get("src_t1", t0) if o.get("src_t1") is not None else t0)
            ln = float(o.get("out_len", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        if t1 < t0:
            t0, t1 = t1, t0
        if ln < 0:
            ln = 0.0
        if abs(ln - (t1 - t0)) <= EPS:
            continue                      # 无时长变化(如换段)不入表
        row = {k: v for k, v in o.items() if k not in ("src_t",)}
        row.update({"src_t0": round(t0, 6), "src_t1": round(t1, 6), "out_len": round(ln, 6)})
        au = str(row.get("audio") or "").lower()
        if ln > 0:
            row["audio"] = au if au in AUDIO_POLICIES else "sustain"
        out.append(row)
    out.sort(key=lambda r: (r["src_t0"], r["src_t1"]))
    return out


def delta_of(op: dict) -> float:
    return float(op["out_len"]) - (float(op["src_t1"]) - float(op["src_t0"]))


def total_delta(ops) -> float:
    return round(sum(delta_of(o) for o in normalize_ops(ops)), 6)


def ops_fingerprint(ops) -> str:
    return hashlib.sha256(json.dumps(normalize_ops(ops), sort_keys=True).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- 映射

def map_time(ops, t: float) -> float:
    """源基准时刻 → 输出时刻。"""
    t = float(t)
    out = t
    for o in normalize_ops(ops):
        t0, t1 = o["src_t0"], o["src_t1"]
        if t1 <= t + EPS:
            out += delta_of(o)
        elif t0 < t < t1:                      # 落在被删区间内:折到区间输出起点
            out += (t0 - t)
            # 区间内的输出长度(删段 = 0;非零 out_len 的替换区间按比例)
            if t1 - t0 > EPS and o["out_len"] > 0:
                out += (t - t0) / (t1 - t0) * o["out_len"]
            break
        else:
            break
    return round(out, 6)


def inverse_time(ops, t_out: float) -> float:
    """输出时刻 → 源基准时刻(落在插入块内的时刻折回插入点)。"""
    t_out = float(t_out)
    src = t_out
    acc = 0.0
    for o in normalize_ops(ops):
        t0, t1 = o["src_t0"], o["src_t1"]
        start_out = t0 + acc                 # 该 op 在输出上的起点
        end_out = start_out + o["out_len"]
        if t_out < start_out - EPS:
            break
        if t_out <= end_out + EPS:           # 落在 op 输出块内
            if o["out_len"] > EPS and t1 - t0 > EPS:
                return round(t0 + (t_out - start_out) / o["out_len"] * (t1 - t0), 6)
            return round(t1 if o["out_len"] <= EPS else t0, 6)
        acc += delta_of(o)
        src = t_out - acc
    return round(src, 6)


def compose(first, second) -> list[dict]:
    """first:源基准 → 中间基准;second:中间基准 → 输出。返回源基准 → 输出的一张总表。"""
    first = normalize_ops(first)
    out = list(first)
    for o in normalize_ops(second):
        row = dict(o)
        row["src_t0"] = inverse_time(first, o["src_t0"])
        row["src_t1"] = inverse_time(first, o["src_t1"]) if o["src_t1"] > o["src_t0"] else row["src_t0"]
        row.setdefault("layer", "second")
        out.append(row)
    return normalize_ops(out)


def map_fn(ops, extra_shift: float = 0.0):
    ops = normalize_ops(ops)
    return lambda t: map_time(ops, t) + float(extra_shift)


# ---------------------------------------------------------------- 字幕

_SRT_LINE = re.compile(r"^\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})(.*)$")
_ASS_EVENT = re.compile(r"^(Dialogue|Comment):\s*(\d+),([^,]+),([^,]+),(.*)$")


def srt_to_s(t: str) -> float:
    h, mi, rest = t.strip().split(":")
    s, ms = re.split(r"[,.]", rest)
    return int(h) * 3600 + int(mi) * 60 + int(s) + int(ms.ljust(3, "0")[:3]) / 1000.0


def s_to_srt(x: float) -> str:
    ms = int(round(max(0.0, x) * 1000))
    h, ms = divmod(ms, 3600000)
    mi, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{mi:02d}:{s:02d},{ms:03d}"


def ass_to_s(t: str) -> float:
    h, mi, rest = t.strip().split(":")
    return int(h) * 3600 + int(mi) * 60 + float(rest)


def s_to_ass(x: float) -> str:
    cs = int(round(max(0.0, x) * 100))
    h, cs = divmod(cs, 360000)
    mi, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{mi:02d}:{s:02d}.{cs:02d}"


def remap_srt_text(text: str, fn) -> str:
    out = []
    for line in text.splitlines():
        m = _SRT_LINE.match(line)
        if m:
            a, b = fn(srt_to_s(m.group(1))), fn(srt_to_s(m.group(2)))
            if b <= a:
                b = a + 0.001
            line = f"{s_to_srt(a)} --> {s_to_srt(b)}{m.group(3)}"
        out.append(line)
    return "\n".join(out) + "\n"


def remap_ass_text(text: str, fn) -> str:
    out = []
    for line in text.splitlines():
        m = _ASS_EVENT.match(line)
        if m:
            a, b = fn(ass_to_s(m.group(3))), fn(ass_to_s(m.group(4)))
            if b <= a:
                b = a + 0.01
            line = f"{m.group(1)}: {m.group(2)},{s_to_ass(a)},{s_to_ass(b)},{m.group(5)}"
        out.append(line)
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- 音频重映射

def _run(cmd: list[str], timeout: int = 3600) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"命令失败({p.returncode}):{' '.join(map(str, cmd))}\n{(p.stderr or '')[-2000:]}")
    return p.stdout or ""


def probe_audio_duration(path: Path) -> float:
    out = _run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=duration:format=duration",
                "-of", "json", str(path)], timeout=60)
    j = json.loads(out or "{}")
    for s in j.get("streams") or []:
        try:
            return float(s.get("duration"))
        except (TypeError, ValueError):
            pass
    return float((j.get("format") or {}).get("duration") or 0.0)


def _fmt(x: float) -> str:
    return f"{float(x):.6f}"


def build_audio_plan(ops, src_dur: float) -> list[dict]:
    """把 ops 展开为输出声轨的分段表:[{kind:'src', t0, t1, fade_out?, fade_in?} | {kind:'pad', op, len}]。"""
    ops = normalize_ops(ops)
    segs, pos = [], 0.0
    for o in ops:
        t0, t1 = o["src_t0"], o["src_t1"]
        if t0 > pos + EPS:
            segs.append({"kind": "src", "t0": pos, "t1": min(t0, src_dur)})
        if o["out_len"] > EPS:
            segs.append({"kind": "pad", "op": o, "len": o["out_len"]})
        pos = max(pos, t1)
    if src_dur > pos + EPS:
        segs.append({"kind": "src", "t0": pos, "t1": src_dur})
    # fade 策略:相邻源段的尾/首淡化
    for i, s in enumerate(segs):
        if s["kind"] != "pad" or s["op"].get("audio") != "fade":
            continue
        if i > 0 and segs[i - 1]["kind"] == "src":
            segs[i - 1]["fade_out"] = min(FADE_S, segs[i - 1]["t1"] - segs[i - 1]["t0"])
        if i + 1 < len(segs) and segs[i + 1]["kind"] == "src":
            segs[i + 1]["fade_in"] = min(FADE_S, segs[i + 1]["t1"] - segs[i + 1]["t0"])
    return [s for s in segs if s["kind"] == "pad" or s["t1"] - s["t0"] > EPS]


def remap_audio(src: Path, dst: Path, ops, sample_rate: int = 48000, src_dur: float | None = None) -> dict:
    """按 ops 重映射声轨 → dst(wav pcm_s16le,立体声)。src_dur 给定时源按该长度截取/补静音(源 mp4 的 AAC 流
    常比画面长几十毫秒的 priming 样本,按容器时长对齐才与画面 + 垫片相等)。返回 {segments, src_duration, out_duration, delta}。"""
    src, dst = Path(src), Path(dst)
    real = probe_audio_duration(src)
    if real <= 0:
        raise RuntimeError(f"源声轨无法解析:{src}")
    src_dur = float(src_dur) if src_dur else real
    plan = build_audio_plan(ops, src_dur)
    inputs = ["-i", str(src)]
    bed_idx: dict[str, int] = {}
    parts, labels = [], []
    fmt = f"aresample={sample_rate},aformat=sample_fmts=fltp:channel_layouts=stereo"
    for i, s in enumerate(plan):
        lab = f"[a{i}]"
        if s["kind"] == "src":
            # 先按时间戳对齐样本(async):-c copy 拼出的 AAC 每段带 priming 样本,时间戳重叠,不对齐会多出样本
            f = (f"[0:a]aresample={sample_rate}:async=1:first_pts=0,atrim=start={_fmt(s['t0'])}:end={_fmt(s['t1'])},asetpts=PTS-STARTPTS,{fmt},"
                 f"apad=whole_dur={_fmt(s['t1'] - s['t0'])},atrim=end={_fmt(s['t1'] - s['t0'])},asetpts=PTS-STARTPTS")
            if s.get("fade_out"):
                f += f",afade=t=out:st={_fmt(s['t1'] - s['t0'] - s['fade_out'])}:d={_fmt(s['fade_out'])}"
            if s.get("fade_in"):
                f += f",afade=t=in:st=0:d={_fmt(s['fade_in'])}"
            parts.append(f + lab)
        else:
            o, ln = s["op"], float(s["len"])
            policy = o.get("audio") or "sustain"
            bed = o.get("bed_file")
            if policy == "sustain" and bed and Path(bed).is_file():
                if bed not in bed_idx:
                    bed_idx[bed] = len(inputs) // 2
                    inputs += ["-i", str(bed)]
                k = bed_idx[bed]
                parts.append(f"[{k}:a]{fmt},aloop=loop=-1:size={int(60 * sample_rate)},atrim=end={_fmt(ln)},asetpts=PTS-STARTPTS,"
                             f"afade=t=in:st=0:d={_fmt(min(EDGE_S, ln / 2))},afade=t=out:st={_fmt(max(0.0, ln - EDGE_S))}:d={_fmt(min(EDGE_S, ln / 2))}{lab}")
            elif policy == "sustain":
                w = min(SUSTAIN_WINDOW_S, max(SUSTAIN_MIN_S, ln), max(0.0, o["src_t0"]))
                if w < SUSTAIN_MIN_S / 2:
                    parts.append(f"anullsrc=r={sample_rate}:cl=stereo,atrim=end={_fmt(ln)},asetpts=PTS-STARTPTS{lab}")
                else:
                    loops = int(ln // w) + 1
                    parts.append(f"[0:a]aresample={sample_rate}:async=1:first_pts=0,atrim=start={_fmt(o['src_t0'] - w)}:end={_fmt(o['src_t0'])},asetpts=PTS-STARTPTS,{fmt},"
                                 f"aloop=loop={loops}:size={int(round(w * sample_rate))},atrim=end={_fmt(ln)},asetpts=PTS-STARTPTS,"
                                 f"afade=t=in:st=0:d={_fmt(min(EDGE_S, ln / 2))},afade=t=out:st={_fmt(max(0.0, ln - EDGE_S))}:d={_fmt(min(EDGE_S, ln / 2))}{lab}")
            else:
                parts.append(f"anullsrc=r={sample_rate}:cl=stereo,atrim=end={_fmt(ln)},asetpts=PTS-STARTPTS{lab}")
        labels.append(lab)
    if not labels:
        raise RuntimeError("时间映射后没有任何声轨段")
    if len(labels) == 1:
        parts.append(f"{labels[0]}anull[aout]")
    else:
        parts.append("".join(labels) + f"concat=n={len(labels)}:v=0:a=1[aout]")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.stem + ".remapping.wav")
    cmd = ["ffmpeg", "-y", "-v", "error", "-nostdin", *inputs, "-filter_complex", ";".join(parts),
           "-map", "[aout]", "-c:a", "pcm_s16le", "-ar", str(sample_rate), "-ac", "2", str(tmp)]
    try:
        _run(cmd)
        if dst.exists():
            dst.unlink()
        tmp.rename(dst)
    finally:
        tmp.unlink(missing_ok=True)
    out_dur = probe_audio_duration(dst)
    return {"segments": len(plan), "src_duration": round(src_dur, 6), "out_duration": round(out_dur, 6),
            "delta": total_delta(ops), "pads": sum(1 for s in plan if s["kind"] == "pad")}


def remap_audio_cached(src: Path, dst: Path, ops, sample_rate: int = 48000, src_dur: float | None = None) -> tuple[Path, dict]:
    """同源同表不重算:dst 旁 .json 记源指纹与 ops 指纹。"""
    src, dst = Path(src), Path(dst)
    st = src.stat()
    key = {"src": str(src), "size": st.st_size, "mtime": int(st.st_mtime), "ops": ops_fingerprint(ops), "sr": sample_rate,
           "src_dur": round(src_dur, 3) if src_dur else None}
    meta_p = dst.with_suffix(dst.suffix + ".json")
    if dst.is_file() and meta_p.is_file():
        try:
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
            if meta.get("key") == key:
                return dst, meta.get("result") or {}
        except Exception:  # noqa: BLE001
            pass
    res = remap_audio(src, dst, ops, sample_rate, src_dur)
    meta_p.write_text(json.dumps({"key": key, "result": res, "ops": normalize_ops(ops)}, ensure_ascii=False, indent=2), encoding="utf-8")
    return dst, res


# ---------------------------------------------------------------- 台账

TIMEMAP_FILE = "timemap.json"


def load_layer(path: Path) -> list[dict]:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    if isinstance(d, dict):
        return normalize_ops(d.get("ops") or [])
    if isinstance(d, list):
        return normalize_ops(d)
    return []


def describe(ops) -> str:
    ops = normalize_ops(ops)
    if not ops:
        return "无时长编辑"
    ins = [o for o in ops if o["out_len"] > EPS]
    dele = [o for o in ops if o["out_len"] <= EPS]
    return (f"{len(ins)} 处插入 +{sum(o['out_len'] for o in ins):.2f}s"
            + (f",{len(dele)} 处删除 −{sum(o['src_t1'] - o['src_t0'] for o in dele):.2f}s" if dele else "")
            + f",合计 {total_delta(ops):+.2f}s")
