# -*- coding: utf-8 -*-
"""speechalign.py — 逐字语音时间轴(word_track)共享原语:花字入出点与语音逐字对齐。

需求(2026-08-18 用户裁定):花字**出现/消失的时间必须准确匹配语音中这段花字文字内容
的开始与结束**——不是"落在这句话里",而是"这几个字被念出来的那一段"。

为此引入集级逐字时间轴 `edit/epNN/word_track.json`(schema wordtrack.v1):
  { "schema_version": 1, "episode": "ep01",
    "audio": {"path": "assets/audio/master/ep01.m4a", "sha256": "...", "duration_s": 592.1},
    "transcript": {"kind": "beat_track"|"srt", "path": "...", "sha256": "..."},
    "source": "char_rate_interp" | "asr_align" | "external_align",
    "confidence": "low"|"medium"|"high", "stats": {...},
    "words": [ {"i": 0, "text": "大", "start": 0.12, "end": 0.31, "seg": "S001", "src": "interp"}, … ] }
  words = 全部**发声单元**(CJK 逐字;拉丁/数字按连续串成词),按时间单调排列,
  文本拼接(去标点空白)== 台本原文;标点只参与停顿估时,不入 words。

三种填充后端(同 schema,下游只认 words):
  - interp   逐句时间码(av: beat_track;主流程: subtitles.srt)+ ffmpeg 静音检测 → 句内按
             字重(CJK 1/数字 1/拉丁词≈音节数/标点停顿)在**非静音区间**内插值。零依赖,
             永远可跑;精度取决于句边界与静音检测(confidence=low/medium)。
  - whisper  faster-whisper 逐词时间戳(word_timestamps)→ 与台本逐字对齐(difflib),
             未命中的字在相邻命中点之间插值(confidence 按命中率;src=asr/interp 逐字标注)。
             需 pip install faster-whisper(可选依赖,缺失时明确报错)。
  - import   外部 ASR 逐词结果(如火山录音文件识别 utterances[].words[])JSON
             [{"text","start","end"}, …](秒或毫秒自动识别)→ 同 whisper 的对齐流程。

花字对账口径(check_captions `caption_speech_aligned`):在 word_track 中定位花字文本
(去标点)最靠近其现有时间/所在组窗口的一次出现,要求
  |start − 首字 start| ≤ SPEECH_TOL_S 且 |end − 末字 end| ≤ SPEECH_TOL_S。
`speech_free: true` 的花字(主流程画面标注型、语音里没念的文字)豁免;av 项目文本
必须来自母带原文,故不允许豁免。

纯函数库,无 CLI。CLI 入口 code/render_captions.py speech-align|speech-lookup|speech-snap。
"""
import difflib
import json
import re
from pathlib import Path

import avsync

SCHEMA_VERSION = 1
SPEECH_TOL_S = 0.15          # 花字入出 vs 语音起止允许偏差(≈3.6 帧 @24fps)
PARTIAL_WINDOW_S = 6.0       # 部分匹配(首尾锚)允许的最大跨度
SIL_NOISE_DB = -32.0
SIL_MIN_S = 0.15             # 句内小停顿也要抓到(audio_map 用 0.3,这里更细)

_CJK = re.compile(r"[㐀-䶿一-鿿豈-﫿぀-ヿ가-힯]")
_ALNUM = re.compile(r"[A-Za-z0-9À-ɏЀ-ӿ']+")
_DIGIT = re.compile(r"^[0-9]+$")
_PAUSE_MAJOR = "。！？!?;；…"
_PAUSE_MINOR = "，,、：:—–-"


# ---------------------------------------------------------------- 分词(发声单元)

def tokenize(text: str) -> list[dict]:
    """把文本切成单元序列:{text, kind: cjk|word|pause|other, weight}。

    weight 是句内估时的相对时长权重;pause 单元不发声(不入 words)但吃时间。
    """
    units = []
    i, n = 0, len(text or "")
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if _CJK.match(ch):
            units.append({"text": ch, "kind": "cjk", "weight": 1.0})
            i += 1
            continue
        m = _ALNUM.match(text, i)
        if m:
            tok = m.group(0)
            if _DIGIT.match(tok):
                w = 1.0 * len(tok)                    # 数字逐位念(221 → 二百二十一)
            else:
                w = max(1.0, round(len(tok) / 3.0))   # 拉丁词≈音节数
            units.append({"text": tok, "kind": "word", "weight": w})
            i = m.end()
            continue
        if ch in _PAUSE_MAJOR:
            units.append({"text": ch, "kind": "pause", "weight": 0.35})
        elif ch in _PAUSE_MINOR:
            units.append({"text": ch, "kind": "pause", "weight": 0.2})
        # 引号/括号等其它符号:不发声、不吃时间
        i += 1
    return units


def voiced_units(text: str) -> list[dict]:
    return [u for u in tokenize(text) if u["kind"] in ("cjk", "word")]


def voiced_text(text: str) -> str:
    """去标点空白后的发声文本(拉丁词间用单空格分隔,统一小写),用于定位比对。"""
    return " ".join(u["text"].lower() for u in voiced_units(text))


# ---------------------------------------------------------------- 台本与音频定位

def find_transcript(proj: Path, ep: str) -> tuple[str, Path] | None:
    """逐句时间码来源:av 项目 beat_track(集级优先)→ mashup 插件 beat_track → 主流程 subtitles.srt。

    mashup 分支必须排在 srt 之前:mashup 的 subtitles.srt 是 beat_track 的下游转写,
    退化去读它会形成自证循环(错误边界喂回对齐)。
    """
    for p in (proj / "av" / ep / "beat_track.json", proj / "av" / "beat_track.json"):
        if p.is_file():
            return "beat_track", p
    p = proj / "mashup" / "beat_track.json"
    if p.is_file():
        return "mashup_beat_track", p
    p = proj / "edit" / ep / "subtitles.srt"
    if p.is_file():
        return "srt", p
    return None


def find_audio(proj: Path, ep: str) -> Path | None:
    """声轨权威:av/mashup=母带(audio_map.master 优先),主流程=audio-mixing final wav。"""
    for am in (proj / "av" / ep / "audio_map.json", proj / "mashup" / "audio_map.json"):
        if not am.is_file():
            continue
        try:
            rel = json.loads(am.read_text(encoding="utf-8")).get("master")
            if rel and (proj / rel).is_file():
                return proj / rel
        except (ValueError, OSError):
            pass
    for ext in ("mp3", "m4a", "wav"):
        cand = proj / "assets" / "audio" / "master" / f"{ep}.{ext}"
        if cand.is_file():
            return cand
    cand = proj / "assets" / "audio" / "final" / f"{ep}.wav"
    return cand if cand.is_file() else None


_SRT_TIME = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")


def _srt_ts(h, m, s, ms) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")[:3]) / 1000.0


def load_segments(kind: str, path: Path) -> list[dict]:
    """统一成 [{id, start, end, text}] 逐句列表(单调)。"""
    segs = []
    if kind == "beat_track":
        bt = json.loads(path.read_text(encoding="utf-8"))
        for s in bt.get("segments", []):
            segs.append({"id": s.get("id"), "start": float(s["start"]),
                         "end": float(s["end"]), "text": s.get("text", "")})
    elif kind == "mashup_beat_track":
        bt = json.loads(path.read_text(encoding="utf-8"))
        for b in bt.get("beats", []):
            segs.append({"id": b.get("beat_id"), "start": float(b["t_in"]),
                         "end": float(b["t_out"]), "text": b.get("text", "")})
    elif kind == "srt":
        cur = None
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            m = _SRT_TIME.search(line)
            if m:
                cur = {"id": f"C{len(segs) + 1:04d}",
                       "start": _srt_ts(*m.groups()[:4]), "end": _srt_ts(*m.groups()[4:]),
                       "text": ""}
                segs.append(cur)
            elif cur is not None and line.strip() and not line.strip().isdigit():
                cur["text"] += line.strip()
            elif not line.strip():
                cur = None
    else:
        raise ValueError(f"未知台本类型 {kind!r}")
    return [s for s in segs if s["end"] > s["start"] and voiced_units(s["text"])]


def _sha_segments(segs: list[dict]) -> str:
    return avsync.canonical_sha256([[s["start"], s["end"], s["text"]] for s in segs])


# ---------------------------------------------------------------- 后端 1:句内插值(零依赖)

def _voiced_intervals(start: float, end: float, silences: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """[start,end] 去掉静音区间后的发声区间;全静音则退回整段。"""
    out, cur = [], start
    for s, e in silences:
        if e <= start or s >= end:
            continue
        s, e = max(s, start), min(e, end)
        if s > cur:
            out.append((cur, s))
        cur = max(cur, e)
    if cur < end:
        out.append((cur, end))
    return out or [(start, end)]


def _interp_segment(seg: dict, silences: list[tuple[float, float]]) -> list[dict]:
    units = tokenize(seg["text"])
    total_w = sum(u["weight"] for u in units) or 1.0
    ivs = _voiced_intervals(seg["start"], seg["end"], silences)
    total_t = sum(e - s for s, e in ivs)

    def at(frac: float) -> float:            # 累计权重比例 → 发声时间轴上的时刻
        t = frac * total_t
        for s, e in ivs:
            if t <= (e - s) + 1e-9:
                return s + t
            t -= (e - s)
        return ivs[-1][1]

    words, acc = [], 0.0
    for u in units:
        a, b = acc / total_w, (acc + u["weight"]) / total_w
        acc += u["weight"]
        if u["kind"] in ("cjk", "word"):
            words.append({"text": u["text"], "start": round(at(a), 3),
                          "end": round(at(b), 3), "seg": seg["id"], "src": "interp"})
    return words


def _voiced_seconds(seg: dict, silences) -> float:
    ivs = _voiced_intervals(seg["start"], seg["end"], silences)
    if ivs == [(seg["start"], seg["end"])] and any(
            s <= seg["start"] and e >= seg["end"] for s, e in silences):
        return 0.0                                   # 整句落在静音里(退回整段的兜底不算)
    return sum(e - s for s, e in ivs)


def _merge_silent_segments(segs: list[dict], silences) -> tuple[list[dict], int]:
    """逐句时间码偶有整句压在静音上的坏边界(上游按字数估时+吸附的副作用);
    这类句并入下一句(末句并入上一句)一起分配,避免把几个字挤进 0.2s。"""
    if not silences:
        return segs, 0
    out, merged, carry = [], 0, None
    for seg in segs:
        cur = dict(seg)
        if carry is not None:
            cur = {"id": carry["id"], "start": carry["start"], "end": cur["end"],
                   "text": carry["text"] + cur["text"]}
            carry = None
        need = sum(u["weight"] for u in tokenize(cur["text"]) if u["kind"] != "pause")
        if _voiced_seconds(cur, silences) < min(0.15, 0.06 * need):
            carry, merged = cur, merged + 1
            continue
        out.append(cur)
    if carry is not None:                            # 末句:并入前一句
        if out:
            prev = out.pop()
            out.append({"id": prev["id"], "start": prev["start"], "end": carry["end"],
                        "text": prev["text"] + carry["text"]})
        else:
            out.append(carry)
    return out, merged


def build_interp(segs: list[dict], audio: Path | None) -> tuple[list[dict], dict]:
    silences: list[tuple[float, float]] = []
    if audio is not None and avsync.tool_available("ffmpeg"):
        silences = avsync.silence_spans(str(audio), noise_db=SIL_NOISE_DB, min_dur_s=SIL_MIN_S)
    segs2, merged = _merge_silent_segments(segs, silences)
    words = []
    for seg in segs2:
        words.extend(_interp_segment(seg, silences))
    stats = {"segments": len(segs), "silences_used": len(silences), "merged_silent_segments": merged}
    return words, stats


# ---------------------------------------------------------------- 后端 2/3:ASR 逐词 → 台本对齐

def _asr_chars(asr_words: list[dict]) -> list[dict]:
    """外部逐词结果拆成发声单元(CJK 逐字均分该词时长),字段 text/start/end。"""
    out = []
    for w in asr_words:
        units = voiced_units(str(w.get("text") or w.get("word") or ""))
        if not units:
            continue
        s, e = float(w["start"]), float(w["end"])
        step = (e - s) / len(units)
        for k, u in enumerate(units):
            out.append({"text": u["text"].lower(), "start": s + k * step, "end": s + (k + 1) * step})
    return out


def normalize_external_words(items: list[dict]) -> list[dict]:
    """外部 JSON 容错:键名 text/word,start/end 或 start_time/end_time;毫秒自动换算秒。"""
    out = []
    for it in items:
        s = it.get("start", it.get("start_time"))
        e = it.get("end", it.get("end_time"))
        if s is None or e is None:
            continue
        out.append({"text": it.get("text", it.get("word", "")), "start": float(s), "end": float(e)})
    if out and max(x["end"] for x in out) > 36000:      # >10h 不合理 → 视为毫秒
        for x in out:
            x["start"] /= 1000.0
            x["end"] /= 1000.0
    return out


def align_asr_to_transcript(segs: list[dict], asr_words: list[dict],
                            audio: Path | None = None) -> tuple[list[dict], dict]:
    """ASR 逐词 → 台本逐字:difflib 对齐,命中取 ASR 时间,未命中在相邻命中间插值。

    先按 interp 得到全量兜底时间(保证每个字都有值且单调),再用命中覆盖并对未命中段
    做局部线性重分布——台本是唯一文本事实源,ASR 只提供时间。
    """
    base, _ = build_interp(segs, audio)
    tgt = [w["text"].lower() for w in base]
    asr = _asr_chars(asr_words)
    src = [w["text"] for w in asr]
    sm = difflib.SequenceMatcher(a=tgt, b=src, autojunk=False)
    hit = {}
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                hit[i1 + k] = asr[j1 + k]
    for i, w in enumerate(base):
        if i in hit:
            w["start"], w["end"], w["src"] = round(hit[i]["start"], 3), round(hit[i]["end"], 3), "asr"
    # 未命中段:在左右最近命中点之间按字数线性重分布(段首/段尾无锚则保留 interp 值)
    i, n = 0, len(base)
    while i < n:
        if base[i]["src"] == "asr":
            i += 1
            continue
        j = i
        while j < n and base[j]["src"] != "asr":
            j += 1
        left = base[i - 1]["end"] if i > 0 and base[i - 1]["src"] == "asr" else None
        right = base[j]["start"] if j < n else None
        if left is not None and right is not None and right > left:
            step = (right - left) / (j - i)
            for k in range(i, j):
                base[k]["start"] = round(left + (k - i) * step, 3)
                base[k]["end"] = round(left + (k - i + 1) * step, 3)
        i = j
    # 单调修复(ASR 偶发倒挂)
    for k in range(1, n):
        if base[k]["start"] < base[k - 1]["end"]:
            base[k]["start"] = base[k - 1]["end"]
        if base[k]["end"] < base[k]["start"]:
            base[k]["end"] = base[k]["start"]
    ratio = len(hit) / max(1, n)
    stats = {"segments": len(segs), "asr_units": len(asr), "matched": len(hit),
             "match_ratio": round(ratio, 3)}
    return base, stats


def run_whisper(audio: Path, model_size: str = "small", language: str | None = None,
                initial_prompt: str | None = None) -> list[dict]:
    """faster-whisper 逐词时间戳。模型统一缓存到 ``data/models/``。"""
    try:
        from modules.transcription import load_model
    except ModuleNotFoundError:  # python code/render_captions.py ...
        from transcription import load_model
    model = load_model(model_size, device="auto", compute_type="default")
    segments, _info = model.transcribe(str(audio), language=language, word_timestamps=True,
                                       initial_prompt=initial_prompt,
                                       vad_filter=True, beam_size=5)
    out = []
    for seg in segments:
        for w in (seg.words or []):
            out.append({"text": w.word, "start": float(w.start), "end": float(w.end)})
    return out


# ---------------------------------------------------------------- 句边界:ASR 实测(mashup 拍对齐)

def beats_from_asr(sentences: list[str], asr_words: list[dict], total_s: float,
                   silences: list[tuple[float, float]] | None = None,
                   ) -> tuple[list[float], list[str], dict]:
    """整篇文稿逐句 → ASR 实测句边界(mashup 拍边界的 v4 后端)。

    替代 avsync.char_rate_boundaries+snap 的「字数估时+盲吸」:difflib 把台本发声单元
    与 ASR 单元对齐,句边界取「上句末单元实测 end 与下句首单元实测 start」的中点;
    该间隙与静音停顿相交时改取停顿中点(听感最宽容)。未锚定边界在左右锚定边界之间
    按 tokenize **加权**字重插值(数字按位、拉丁按音节——修字数估时的数字毒药)。

    返回 (boundaries, srcs, stats):
      boundaries  len = len(sentences)+1,含 0.0 与 total_s,单调;
      srcs        len = len(sentences)-1(内部边界),取值 asr_word|asr_silence_mid|interp;
      stats       {anchored, interp, match_ratio}。
    调用方按 match_ratio 决定采信(建议 ≥0.6,否则整体降级 char_rate+snap)。
    """
    silences = silences or []
    sent_units: list[list[dict]] = [voiced_units(s) for s in sentences]
    tgt, sent_first, sent_last = [], [], []
    for units in sent_units:
        sent_first.append(len(tgt))
        tgt.extend(u["text"].lower() for u in units)
        sent_last.append(len(tgt) - 1)
    asr = _asr_chars(asr_words)
    sm = difflib.SequenceMatcher(a=tgt, b=[w["text"] for w in asr], autojunk=False)
    hit: dict[int, dict] = {}
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                hit[i1 + k] = asr[j1 + k]

    def _find_hit(idx: int, step: int, limit: int = 2) -> dict | None:
        for d in range(limit + 1):
            w = hit.get(idx + d * step)
            if w is not None:
                return w
        return None

    n = len(sentences)
    bounds: list[float | None] = [0.0] + [None] * (n - 1) + [float(total_s)]
    srcs = ["interp"] * (n - 1)
    for i in range(n - 1):
        last = _find_hit(sent_last[i], -1) if sent_units[i] else None
        nxt = _find_hit(sent_first[i + 1], +1) if sent_units[i + 1] else None
        if last is None or nxt is None or nxt["start"] <= last["end"] - 0.5:
            continue                      # 单侧缺锚或 ASR 倒挂过甚 → 留给插值
        lo, hi = last["end"], max(nxt["start"], last["end"])
        mid = (lo + hi) / 2
        b, src = mid, "asr_word"
        cands = [(s + e) / 2 for s, e in silences if e > lo - 0.05 and s < hi + 0.05]
        if cands:
            b, src = min(cands, key=lambda c: abs(c - mid)), "asr_silence_mid"
        bounds[i + 1], srcs[i] = round(b, 3), src
    # 未锚定边界:左右最近锚定边界之间按句加权字重插值
    weights = [max(sum(u["weight"] for u in units), 0.001) for units in sent_units]
    i = 1
    while i <= n - 1:
        if bounds[i] is not None:
            i += 1
            continue
        j = i
        while j <= n - 1 and bounds[j] is None:
            j += 1
        left, right = bounds[i - 1], bounds[j]      # 两端必非 None(0 与 total_s 恒有值)
        span_w = sum(weights[i - 1:j])
        acc = 0.0
        for k in range(i, j):
            acc += weights[k - 1]
            bounds[k] = round(left + (right - left) * acc / span_w, 3)
        i = j
    # 单调 clamp(锚定点也可能被 ASR 偶发倒挂波及)
    out = [float(b) for b in bounds]      # type: ignore[arg-type]
    for k in range(1, len(out)):
        out[k] = max(out[k], out[k - 1])
    out[-1] = float(total_s)
    anchored = sum(1 for s in srcs if s != "interp")
    stats = {"anchored": anchored, "interp": len(srcs) - anchored,
             "match_ratio": round(len(hit) / max(1, len(tgt)), 3)}
    return out, srcs, stats


# ---------------------------------------------------------------- word_track 装配/读写

def _confidence(source: str, stats: dict) -> str:
    if source == "char_rate_interp":
        return "medium" if stats.get("silences_used") else "low"
    r = stats.get("match_ratio", 0)
    return "high" if r >= 0.85 else ("medium" if r >= 0.6 else "low")


def assemble(ep: str, proj: Path, audio: Path | None, kind: str, tpath: Path,
             segs: list[dict], words: list[dict], source: str, stats: dict) -> dict:
    for i, w in enumerate(words):
        w["i"] = i
    audio_meta = None
    if audio is not None:
        audio_meta = {"path": str(audio.relative_to(proj)) if audio.is_relative_to(proj) else str(audio),
                      "sha256": avsync.file_sha256(str(audio)),
                      "duration_s": round(avsync.probe_duration(str(audio)), 3)
                      if avsync.tool_available("ffprobe") else None}
    return {"schema_version": SCHEMA_VERSION, "episode": ep, "audio": audio_meta,
            "transcript": {"kind": kind,
                           "path": str(tpath.relative_to(proj)) if tpath.is_relative_to(proj) else str(tpath),
                           "sha256": _sha_segments(segs)},
            "source": source, "confidence": _confidence(source, stats),
            "stats": stats, "words": words}


def word_track_path(proj: Path, ep: str) -> Path:
    return proj / "edit" / ep / "word_track.json"


def load_word_track(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != SCHEMA_VERSION or not isinstance(data.get("words"), list):
        raise ValueError(f"{path} 不是 wordtrack.v{SCHEMA_VERSION}")
    return data


def staleness(track: dict, proj: Path, ep: str) -> list[str]:
    """word_track 是否过期:台本(逐句时间码)或音频变了都要重建。"""
    why = []
    found = find_transcript(proj, ep)
    if found is None:
        why.append("台本来源(beat_track/subtitles.srt)已不存在")
    else:
        kind, p = found
        try:
            sha = _sha_segments(load_segments(kind, p))
            if sha != (track.get("transcript") or {}).get("sha256"):
                why.append(f"台本 {p.name} 已变更(重跑 speech-align)")
        except (ValueError, OSError) as e:
            why.append(f"台本无法读取:{e}")
    audio = find_audio(proj, ep)
    am = track.get("audio") or {}
    if audio is not None and am.get("sha256") and avsync.file_sha256(str(audio)) != am["sha256"]:
        why.append(f"声轨 {audio.name} 已变更(重跑 speech-align)")
    return why


# ---------------------------------------------------------------- 定位:花字文本 → 语音区间

def _index(track: dict):
    words = track["words"]
    keys = [str(w["text"]).lower() for w in words]
    return words, keys


def locate(track: dict, text: str, near_s: float | None = None,
           window: tuple[float, float] | None = None) -> dict | None:
    """在 word_track 中找花字文本对应的语音区间。

    返回 {start, end, first, last, match: exact|partial, candidates} 或 None。
    多次出现时取最靠近 near_s(缺省取 window 中点)的一次;window 给定时优先窗口内候选。
    exact 找不到时退化为首尾锚(前 2 单元 + 后 2 单元,跨度 ≤ PARTIAL_WINDOW_S)。
    """
    words, keys = _index(track)
    q = [u["text"].lower() for u in voiced_units(text)]
    if not q or not words:
        return None
    ref = near_s if near_s is not None else (sum(window) / 2 if window else None)

    def pick(cands):
        if not cands:
            return None
        if window:
            inside = [c for c in cands if c["start"] < window[1] and c["end"] > window[0]]
            cands = inside or cands
        if ref is not None:
            cands.sort(key=lambda c: abs(c["start"] - ref))
        return cands[0]

    n, m = len(keys), len(q)
    exact = []
    for i in range(0, n - m + 1):
        if keys[i:i + m] == q:
            exact.append({"start": words[i]["start"], "end": words[i + m - 1]["end"],
                          "first": i, "last": i + m - 1, "match": "exact"})
    best = pick(exact)
    if best:
        best["candidates"] = len(exact)
        return best
    if m < 4:
        return None
    # 部分匹配:首锚+尾锚
    head, tail = q[:2], q[-2:]
    heads = [i for i in range(n - 1) if keys[i:i + 2] == head]
    tails = [i for i in range(n - 1) if keys[i:i + 2] == tail]
    partial = []
    for h in heads:
        for t in tails:
            if t + 1 > h + 1 and words[t + 1]["end"] - words[h]["start"] <= PARTIAL_WINDOW_S:
                partial.append({"start": words[h]["start"], "end": words[t + 1]["end"],
                                "first": h, "last": t + 1, "match": "partial"})
    best = pick(partial)
    if best:
        best["candidates"] = len(partial)
    return best


def _group_window(cap: dict, groups: dict) -> tuple[float, float] | None:
    g = groups.get(cap.get("group_id")) or {}
    base = g.get("audio_in_s")
    if base is None:
        # 主流程组无 audio_in_s:用花字自身 start-local_start 反推组起点
        if isinstance(cap.get("start"), (int, float)) and isinstance(cap.get("local_start"), (int, float)):
            base = float(cap["start"]) - float(cap["local_start"])
        else:
            return None
    span = float(g.get("av_span_s") or g.get("total_duration_s") or 0) or 15.0
    return (float(base), float(base) + span)


def speech_span(track: dict, cap: dict, groups: dict) -> dict | None:
    """一条花字对应的语音区间(以现有 start 为参考,组窗口为范围)。"""
    win = _group_window(cap, groups)
    near = cap.get("start") if isinstance(cap.get("start"), (int, float)) else None
    return locate(track, cap.get("text") or "", near_s=near, window=win)


MIN_CAPTION_S = 0.25          # 渲染器下限 0.2s + 余量:语音区间更短时从起点起算此时长


def expected_span(sp: dict, win: tuple[float, float] | None,
                  min_dur_s: float = MIN_CAPTION_S) -> tuple[float, float] | None:
    """语音区间 → 花字应有的 [start,end](裁进组窗口;短于 min_dur 从起点补足)。
    语音落在组窗口之外(可容纳 <0.2s)返回 None。"""
    s, e = sp["start"], max(sp["end"], sp["start"] + min_dur_s)
    if win:
        s, e = max(s, win[0]), min(e, win[1])
        if e - s < 0.2:
            return None
    return round(s, 3), round(e, 3)


def group_at(groups: dict, t: float) -> str | None:
    """av 项目:时刻 t 落在哪个组(audio_in_s ≤ t < audio_in_s+span);主流程组无 audio_in_s 返回 None。"""
    for gid, g in groups.items():
        base = g.get("audio_in_s")
        if base is None:
            continue
        span = float(g.get("av_span_s") or g.get("total_duration_s") or 0)
        if float(base) <= t < float(base) + span:
            return gid
    return None


# ---------------------------------------------------------------- 对账与吸附

def check_speech_alignment(data: dict, track: dict, shot_list: dict,
                           tol_s: float = SPEECH_TOL_S, allow_speech_free: bool = True) -> list[str]:
    """机检 caption_speech_aligned 的问题列表(空=通过)。"""
    groups = {g["group_id"]: g for g in shot_list.get("generation_groups", [])}
    issues = []
    for i, c in enumerate(data.get("captions", [])):
        tag = c.get("id") or f"captions[{i}]"
        if c.get("speech_free"):
            if not allow_speech_free:
                issues.append(f"{tag}: av 项目文本必来自母带,不允许 speech_free 豁免")
            continue
        sp = speech_span(track, c, groups)
        if sp is None:
            issues.append(f"{tag}:「{c.get('text')}」在语音逐字轨中找不到(非语音文字请标 "
                          "speech_free:true;否则核对文案是否与台本一字不差)")
            continue
        s, e = c.get("start"), c.get("end")
        if not (isinstance(s, (int, float)) and isinstance(e, (int, float))):
            issues.append(f"{tag}: start/end 缺失")
            continue
        win = _group_window(c, groups)
        exp = expected_span(sp, win)
        if exp is None:
            g_hint = group_at(groups, sp["start"])
            issues.append(f"{tag}:「{c.get('text')}」的语音 {sp['start']}-{sp['end']} 不在本组 "
                          f"{c.get('group_id')} 窗口内"
                          + (f"(应挂到 {g_hint})" if g_hint else "") + ";speech-snap 可自动改挂")
            continue
        ds, de = float(s) - exp[0], float(e) - exp[1]
        if abs(ds) > tol_s or abs(de) > tol_s:
            issues.append(f"{tag}: 入出 {s}-{e} 与语音「{c.get('text')}」{exp[0]}-{exp[1]} "
                          f"偏差 {ds:+.2f}/{de:+.2f}s(>±{tol_s}s;render_captions.py speech-snap 可自动吸附)")
    return issues


def snap_captions(data: dict, track: dict, shot_list: dict,
                  min_dur_s: float = MIN_CAPTION_S) -> dict:
    """把每条花字的 start/end/local_start/local_end 吸附到语音区间(原地修改)。

    - 语音起点落在别的组(av 项目按 audio_in_s 判定)→ 改挂 group_id 到该组(花字不跨组);
    - 语音区间跨组尾 → end 裁到组尾;短于 min_dur_s(渲染器下限 0.2s)→ 从起点补足;
    - 组内 local 时间随集级同步重算(av 组起点=audio_in_s;主流程沿用原 start-local_start 差)。
    返回 {snapped:[…], skipped:[…], not_found:[…]}。
    """
    groups = {g["group_id"]: g for g in shot_list.get("generation_groups", [])}
    rep = {"snapped": [], "skipped": [], "not_found": []}
    for i, c in enumerate(data.get("captions", [])):
        tag = c.get("id") or f"captions[{i}]"
        if c.get("speech_free"):
            rep["skipped"].append(f"{tag}(speech_free)")
            continue
        sp = speech_span(track, c, groups)
        if sp is None:
            rep["not_found"].append(f"{tag}「{c.get('text')}」")
            continue
        moved = None
        gid = group_at(groups, sp["start"])
        if gid and gid != c.get("group_id"):
            moved, c["group_id"] = c.get("group_id"), gid
        exp = expected_span(sp, _group_window(c, groups), min_dur_s)
        if exp is None:
            rep["skipped"].append(f"{tag}(语音区间 {sp['start']}-{sp['end']} 无法落进组窗口)")
            if moved:
                c["group_id"] = moved
            continue
        g = groups.get(c.get("group_id")) or {}
        base = g.get("audio_in_s")
        if base is None:
            base = float(c.get("start", 0)) - float(c.get("local_start", 0))
        base = float(base)
        old = (c.get("start"), c.get("end"))
        c["start"], c["end"] = exp
        c["local_start"], c["local_end"] = round(exp[0] - base, 3), round(exp[1] - base, 3)
        c["speech_match"] = sp["match"]
        rep["snapped"].append({"id": tag, "text": c.get("text"), "old": old, "new": exp,
                               "match": sp["match"], "moved": moved})
    return rep
