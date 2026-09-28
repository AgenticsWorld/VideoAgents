"""对白语音排轨(2026-09-13):把对白语音库(modules/dialogue_tts.py)的逐句音频按镜时间线摆到样片时间轴上,
并用 ffmpeg 混成一条对白轨。动态样片(code/animatic.py)与白模样片(modules/whitebox_export.py)共用。

- place_lines(audio, timeline):同镜多句从镜起点按实测时长顺序排开(句间留 GAP_S),不截断;超出镜时长的部分
  记 overflow_s 回给调用方写进各自 manifest(这是分镜规划的反馈信号,不在这里硬塞)。
  上一句拖进本镜时本句起点顺延到它说完(2026-09-28,两句不叠着说;顺延不超过本镜时长的 MAX_DELAY_RATIO,记 delay_s)。
  样片传进来的是对白语音库的节奏贴合版(dialogue_tts.line_audio(paced=True)),时长已按估时压过。
- build_track(placements, total_s, out):anullsrc 底 + 每句 adelay → amix(normalize=0) → alimiter,
  输出 48k 立体声 wav,长度精确到 total_s。
- cues_from_placements(...):按实际音频起止生成字幕条(替代按估时均摊),供白模样片字幕带用。
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

GAP_S = 0.15      # 相邻两句之间的停顿
LEAD_S = 0.10     # 镜起点到第一句开口的提前量
MAX_DELAY_RATIO = 0.5   # 为避让上一句,本镜第一句最多顺延到本镜时长的这个比例处


def place_lines(audio: dict[tuple[str, int], dict], timeline: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """audio:{(shot_id, idx): {path, duration_s, speaker, text, ...}};timeline:{shot_id: {start, end}}。
    返回 (placements 按 start 排序, overflow 列表)。"""
    by_shot: dict[str, list[tuple[int, dict]]] = {}
    for (sid, idx), e in audio.items():
        if sid in timeline:
            by_shot.setdefault(sid, []).append((idx, e))
    placements, overflow = [], []
    busy = None       # 上一句说完的时刻 + GAP_S
    for sid, items in sorted(by_shot.items(), key=lambda kv: float(timeline[kv[0]]["start"])):
        seg = timeline[sid]
        start, end = float(seg["start"]), float(seg["end"])
        t = start + LEAD_S
        if busy is not None and busy > t:
            t = max(t, min(busy, start + max(LEAD_S, (end - start) * MAX_DELAY_RATIO)))
        delay = t - (start + LEAD_S)
        for idx, e in sorted(items, key=lambda x: x[0]):
            dur = float(e.get("duration_s") or 0)
            pl = {"shot_id": sid, "idx": idx, "start": round(t, 3), "end": round(t + dur, 3), "duration_s": round(dur, 3),
                  "path": Path(e["path"]), "speaker": e.get("speaker", ""), "text": e.get("text", ""),
                  "group_id": seg.get("group_id", ""), "overflow_s": round(max(0.0, t + dur - end), 3)}
            if delay > 0.001:
                pl["delay_s"] = round(delay, 3)
            placements.append(pl)
            if pl["overflow_s"] > 0.05:
                overflow.append({k: pl[k] for k in ("shot_id", "idx", "speaker", "start", "end", "overflow_s")})
            t += dur + GAP_S
        busy = t
    placements.sort(key=lambda p: (p["start"], p["shot_id"], p["idx"]))
    return placements, overflow


def cues_from_placements(placements: list[dict], names: dict[str, str] | None = None, min_cue_s: float = 0.6) -> list[dict]:
    """字幕条(与 whitebox_subtitles.episode_subtitle_cues 同结构),时间取实际音频起止。"""
    names = names or {}
    out = []
    for p in placements:
        sp = p.get("speaker") or ""
        name = names.get(sp, sp)
        out.append({"start": p["start"], "end": max(p["end"], p["start"] + min_cue_s), "kind": "dialogue",
                    "text": f"{name}:{p['text']}" if name and name != "NARRATOR" else p["text"],
                    "shot_id": p["shot_id"], "group_id": p.get("group_id", "")})
    return out


def build_track(placements: list[dict], total_s: float, out: Path, *, extra: list[tuple[Path, float]] | None = None,
                sample_rate: int = 48000, timeout: int = 900) -> Path:
    """把逐句音频(以及 extra 里的 (文件, 起始秒),如旁白段)混成一条 total_s 长的立体声 wav。"""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("未找到 ffmpeg")
    items = [(p["path"], p["start"]) for p in placements] + list(extra or [])
    total_s = max(0.1, float(total_s))
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
           "-f", "lavfi", "-t", f"{total_s:.3f}", "-i", f"anullsrc=r={sample_rate}:cl=stereo"]
    parts, labels = [], ["[0:a]"]
    for k, (f, st) in enumerate(items, start=1):
        cmd += ["-i", str(f)]
        ms = int(round(float(st) * 1000))
        parts.append(f"[{k}:a]aresample={sample_rate},aformat=channel_layouts=stereo,adelay={ms}|{ms}[a{k}]")
        labels.append(f"[a{k}]")
    fc = ";".join(parts + [f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:normalize=0,alimiter=limit=0.95[out]"])
    cmd += ["-filter_complex", fc, "-map", "[out]", "-ac", "2", "-ar", str(sample_rate), "-t", f"{total_s:.3f}", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0 or not Path(out).is_file():
        raise RuntimeError(f"对白轨混音失败:{(r.stderr or '').strip()[-800:]}")
    return Path(out)
