# -*- coding: utf-8 -*-
"""avsync.py — 音频锁定时间轴共享原语(audio-to-video 插件用)。

与主流程方向相反:主流程是「镜头先定、旁白 TTS 压缩去适配窗口」(WORKFLOW.md §8B),
本模块服务于「音频固定、画面去适配」——用户给定的 MP3 是唯一事实源,画面按它切分。

核心约束(来自 check_generation_groups.py:119-123):
  total_duration_s 必须是**整数**、必须 == int(round(Σ镜头时长))、必须 ∈[4,15]。
  故本模块一律按**整秒**切分,只有末组吸收小数余量(ceil 后由剪辑修剪)。

关键认识:音频是一条连续未剪的流,最后整段 mux。视频切点落在句中只影响观感,
不影响同步——同步 = ①每组画面内容对应它覆盖的那段文本 + ②累计位置零漂移。
整秒切分使累计位置恒为整数累加,漂移在数学上恒为 0。

纯函数库,无 CLI。机检入口见 code/check_av_sync.py。
"""
import hashlib
import json
import re
import shutil
import subprocess

MIN_GROUP_S = 4          # Seedance 2.0 硬下限
MAX_GROUP_S = 14         # 硬上限 15,留 1s 给模型 ±1s 交付公差
FPS = 24                 # 仓库画布 fps(edit 机检口径)
FRAME_S = 1.0 / FPS

# 句末标点(中英);连续标点合并,不切出空句
_SENT_END = re.compile(r"(?<=[。！？!?;；…])\s*|\n+")
_SILENCE_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SILENCE_END = re.compile(r"silence_end:\s*(-?[\d.]+)")
_LOUD_I = re.compile(r"^\s*I:\s*(-?[\d.]+)\s*LUFS", re.M)
_LOUD_PEAK = re.compile(r"^\s*Peak:\s*(-?[\d.]+)\s*dBFS", re.M)


# ---------------------------------------------------------------- 外部工具探测

def tool_available(name: str) -> bool:
    return shutil.which(name) is not None


def require_tools(*names: str) -> None:
    """缺工具即抛错。本模块不做静默降级——时间轴不能靠猜。

    注意:genmedia.py 对 ffprobe 缺失是静默返回 None(那里只是预检);
    本模块的实测时长是整条流水线的地基,缺工具必须显式失败。
    """
    missing = [n for n in names if not tool_available(n)]
    if missing:
        raise RuntimeError(
            f"缺少外部工具 {missing};音频锁定流程强依赖 ffprobe/ffmpeg,"
            "请先安装(macOS: brew install ffmpeg),不可跳过")


def _run(cmd: list[str], timeout: int = 300) -> str:
    """跑外部命令,返回 stdout+stderr(ffmpeg 的分析结果写在 stderr)。"""
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return (p.stdout or "") + (p.stderr or "")


# ---------------------------------------------------------------- 音频实测

def probe_duration(path: str) -> float:
    """ffprobe 实测时长(秒,float 全精度)。失败即抛错,不返回 None。"""
    require_tools("ffprobe")
    out = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "csv=p=0", str(path)], timeout=60).strip()
    try:
        return float(out.splitlines()[0])
    except (ValueError, IndexError) as e:
        raise RuntimeError(f"ffprobe 无法解析 {path} 的时长:{out!r}") from e


def probe_audio_info(path: str) -> dict:
    """音频流基本信息:codec / sample_rate / channels / bit_rate。"""
    require_tools("ffprobe")
    out = _run(["ffprobe", "-v", "error", "-select_streams", "a:0",
                "-show_entries", "stream=codec_name,sample_rate,channels,bit_rate",
                "-of", "json", str(path)], timeout=60)
    try:
        st = (json.loads(out).get("streams") or [{}])[0]
    except json.JSONDecodeError:
        st = {}
    br = st.get("bit_rate")
    return {
        "codec": st.get("codec_name"),
        "sample_rate": int(st["sample_rate"]) if st.get("sample_rate") else None,
        "channels": st.get("channels"),
        "bitrate_kbps": round(int(br) / 1000) if br else None,
    }


def silence_spans(path: str, noise_db: float = -30.0,
                  min_dur_s: float = 0.30) -> list[tuple[float, float]]:
    """ffmpeg silencedetect 找停顿,返回 [(start, end), …](升序,已配对)。

    讲述类音频的自然停顿即候选切点。noise_db 越高越灵敏;min_dur_s 太小会切碎。
    """
    require_tools("ffmpeg")
    out = _run(["ffmpeg", "-v", "info", "-i", str(path),
                "-af", f"silencedetect=noise={noise_db}dB:d={min_dur_s}",
                "-f", "null", "-"])
    starts = [float(m) for m in _SILENCE_START.findall(out)]
    ends = [float(m) for m in _SILENCE_END.findall(out)]
    spans = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else None      # 末段停顿可能无 silence_end
        if e is not None and e > s:
            spans.append((s, e))
    return sorted(spans)


def measure_loudness(path: str) -> dict:
    """ebur128 测响度(只报不改——本流程禁止重编码,响度不做归一化)。"""
    require_tools("ffmpeg")
    out = _run(["ffmpeg", "-v", "info", "-i", str(path),
                "-af", "ebur128=peak=true", "-f", "null", "-"])
    i_vals = _LOUD_I.findall(out)
    pk_vals = _LOUD_PEAK.findall(out)
    lufs = float(i_vals[-1]) if i_vals else None
    peak = float(pk_vals[-1]) if pk_vals else None
    return {
        "lufs": lufs,
        "true_peak_db": peak,
        "clipping": bool(peak is not None and peak > -0.1),
        "normalized": False,          # 恒 False:母带为用户提供,禁止重编码
    }


def audio_frame_md5s(path: str, stream: str = "0:a:0") -> list[str]:
    """逐音频帧 md5(framemd5,packet 级)——验证 mux 后音频帧是否原样拷贝。

    为何不用整流 md5:MP3 装进 MP4 时,容器会重写 gapless/编码器延迟元数据,
    整流 md5 必然变化(实测),但**每一帧的压缩载荷是逐字节拷贝的**。
    framemd5 直接比对帧载荷,才能区分「换容器」与「真重编码」:
      - 合法 -c:a copy    → 帧数相同,帧 1..N-1 全等(末帧因容器补齐可能被重写)
      - 重编码 MP3        → 大量帧不同(实测 1404/1533)
      - 转 AAC            → codec 与帧数均不同
      - 误用 -shortest    → 帧数少 1(音频被静默截断)
    """
    require_tools("ffmpeg")
    out = _run(["ffmpeg", "-v", "error", "-i", str(path),
                "-map", stream, "-c", "copy", "-f", "framemd5", "-"])
    md5s = []
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tail = line.rsplit(",", 1)[-1].strip()
        if re.fullmatch(r"[0-9a-fA-F]{32}", tail):
            md5s.append(tail.lower())
    if not md5s:
        raise RuntimeError(f"无法取 {path} 的逐帧 md5:{out.strip()[:200]}")
    return md5s


def compare_audio_frames(master: str, muxed: str, muxed_stream: str = "0:a:0") -> dict:
    """比对母带与成片的音频帧,返回判定明细。

    末帧允许不同(容器 gapless 补齐);其余帧必须逐帧全等,帧数必须相等。
    muxed_stream 缺省 a:0(干净版成片);花字版成片的母带存档轨在 a:1
    (a:0 是母带+SFX 预混轨),机检传 "0:a:1"。
    """
    a, b = audio_frame_md5s(master), audio_frame_md5s(muxed, muxed_stream)
    n = min(len(a), len(b))
    head_diff = [i for i in range(max(n - 1, 0)) if a[i] != b[i]]
    return {
        "master_frames": len(a),
        "muxed_frames": len(b),
        "frame_count_match": len(a) == len(b),
        "head_frames_identical": not head_diff,
        "diff_frame_count": len(head_diff),
        "first_diff_index": head_diff[0] if head_diff else None,
        "last_frame_identical": bool(n) and a[n - 1] == b[n - 1],
        "ok": len(a) == len(b) and not head_diff,
    }


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_sha256(obj) -> str:
    """规范化 JSON 指纹(与 check_narration_sync.canonical_fingerprint 同口径)。"""
    blob = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- 文本对齐

def midpoints(spans: list[tuple[float, float]]) -> list[float]:
    """取每段停顿的中点作候选切点——切在停顿正中,听感与观感最宽容。"""
    return [(s + e) / 2.0 for s, e in spans]


def split_sentences(text: str) -> list[str]:
    """按句末标点/换行切句,保留原文字符(不丢不改)。"""
    return [s.strip() for s in _SENT_END.split(text or "") if s and s.strip()]


def char_rate_boundaries(sentences: list[str], total_s: float) -> list[float]:
    """按字数比例把 total_s 分配给各句,返回累计边界(含末尾 total_s)。

    讲述语速在一段音频内大体恒定,字数比例是足够好的一阶估计;
    真正的精度由 snap() 吸附到实测停顿提供。
    """
    counts = [max(len(s), 1) for s in sentences]
    total_chars = sum(counts) or 1
    acc, out = 0, []
    for c in counts:
        acc += c
        out.append(total_s * acc / total_chars)
    if out:
        out[-1] = total_s                      # 末边界钉死在实测总长
    return out


def snap(boundaries: list[float], anchors: list[float],
         tol_s: float = 1.5) -> list[float]:
    """把预测边界吸附到最近的候选切点(超出 tol_s 则保留原值)。

    保持单调递增:吸附后若与前一个边界交叉,则放弃该次吸附。
    """
    if not anchors:
        return list(boundaries)
    out: list[float] = []
    for i, b in enumerate(boundaries):
        best = min(anchors, key=lambda a: abs(a - b))
        snapped = best if abs(best - b) <= tol_s else b
        low = out[-1] if out else 0.0
        high = boundaries[i + 1] if i + 1 < len(boundaries) else float("inf")
        out.append(snapped if low < snapped < high else b)
    return out


# ---------------------------------------------------------------- 整秒切分

def plan_integer_groups(total_s: float, cut_candidates: list[float],
                        min_s: int = MIN_GROUP_S,
                        max_s: int = MAX_GROUP_S) -> list[dict]:
    """把 [0, total_s] 切成生成组:每组 span 为整数秒且 ∈[min_s, max_s]。

    末组吸收小数余量:span 为 float,total_duration_s 取 ceil(span)
    (多生成的部分由 timeline.json 的 out 修剪掉)。

    切点在满足整秒与区间约束的前提下,优先靠近 cut_candidates(自然停顿),
    纯属观感优化——切点落在句中不影响同步。

    返回 [{index, audio_in_s, audio_out_s, span_s, total_duration_s,
            snapped, cut_offset_s}, …]
    """
    if total_s < min_s:
        raise ValueError(f"音频时长 {total_s:.3f}s 短于单组下限 {min_s}s,无法切分")

    def nearest_gap(x: float) -> float:
        return min((abs(c - x) for c in cut_candidates), default=float("inf"))

    bounds: list[float] = [0.0]
    pos = 0
    while total_s - pos > max_s:
        lo = pos + min_s
        # 上界另受约束:切完之后剩余必须还能构成至少一组(≥ min_s)
        hi = min(pos + max_s, int(total_s - min_s))
        if hi < lo:                                     # 退化:直接让末组接手
            break
        best = min(range(lo, hi + 1), key=lambda b: (nearest_gap(b), abs(b - (pos + max_s))))
        bounds.append(float(best))
        pos = best
    bounds.append(total_s)

    groups = []
    for i in range(len(bounds) - 1):
        a, b = bounds[i], bounds[i + 1]
        span = b - a
        is_last = (i == len(bounds) - 2)
        # 非末组 span 恒为整数;末组 ceil 后由剪辑修剪
        total_dur = int(round(span)) if not is_last else _ceil_int(span)
        gap = nearest_gap(b) if not is_last else 0.0
        groups.append({
            "index": i + 1,
            "audio_in_s": round(a, 3),
            "audio_out_s": round(b, 3),
            "span_s": round(span, 3),
            "total_duration_s": total_dur,
            "snapped": gap <= 0.5,
            "cut_offset_s": round(gap, 3) if gap != float("inf") else None,
        })
    return groups


def _ceil_int(x: float) -> int:
    i = int(x)
    return i if abs(x - i) < 1e-9 else i + 1


def split_shots(total_duration_s: int, shot_max_s: int = 8,
                min_shots: int = 1) -> list[float]:
    """把组时长拆成若干子镜头,各 ≤ shot_max_s,和恰好等于 total_duration_s。

    既满足内置「单个分镜时长范围」约束(core.py 注入的 shot_max_s),
    也避免一组一镜十几秒画面不动。返回的时长和为整数,故
    int(round(Σ)) == total_duration_s 天然成立(duration_sum 机检)。
    """
    n = max(min_shots, -(-total_duration_s // shot_max_s))    # ceil 除
    base = total_duration_s // n
    rem = total_duration_s - base * n
    return [float(base + (1 if k < rem else 0)) for k in range(n)]


def assign_text(groups: list[dict], segments: list[dict]) -> list[dict]:
    """给每组挂上它覆盖的文本片段 id 与拼接文本(按时间重叠判定)。

    这是「画面内容对应该时刻讲的内容」的机读依据,visual-scripter 据此写画面。
    """
    out = []
    for g in groups:
        a, b = g["audio_in_s"], g["audio_out_s"]
        hit = [s for s in segments
               if float(s["end"]) > a + 1e-9 and float(s["start"]) < b - 1e-9]
        out.append({**g,
                    "segment_ids": [s["id"] for s in hit],
                    "text": "".join(s.get("text", "") for s in hit)})
    return out
