#!/usr/bin/env python3
"""对白文稿 → 单人切换数字人视频。

每段都必须有完整起止时间。多人稿使用
``[00:01.200-00:04.800] 说话人: 台词``；只有一个人物映射时可省略人名。
不再允许按字数估算人物边界。
最终封装始终 ``-c:a copy``，渠道临时音轨一律丢弃。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from modules.avsync import (file_sha256, probe_duration, require_tools,
                                split_sentences)
    from modules.digitalhuman import generate_avatar
except ModuleNotFoundError:  # python modules/dialogue_video.py ...
    from avsync import (file_sha256, probe_duration, require_tools,
                        split_sentences)
    from digitalhuman import generate_avatar

_LINE = re.compile(
    r"^\s*(?:\[(?P<start>[^\]-]+)\s*-\s*(?P<end>[^\]]+)\]\s*)?"
    r"(?P<speaker>[^:：\n]+?)\s*[:：]\s*(?P<text>.+?)\s*$")
_PLAIN_TIMED = re.compile(
    r"^\s*\[(?P<start>[^\]-]+)\s*-\s*(?P<end>[^\]]+)\]\s*(?P<text>.+?)\s*$")
MAX_AVATAR_SEGMENT_S = 14.0


def _seconds(value: str) -> float:
    parts = value.strip().split(":")
    if len(parts) == 1:
        return float(parts[0])
    if len(parts) == 2:
        return float(parts[0]) * 60 + float(parts[1])
    if len(parts) == 3:
        return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    raise ValueError(f"无法解析时间：{value}")


def parse_labeled_transcript(text: str, default_speaker: str | None = None) -> list[dict]:
    """解析多人物标签稿，或把单人物无标签稿自动归给 ``default_speaker``。"""
    lines = [(lineno, raw.strip()) for lineno, raw in enumerate(text.splitlines(), 1)
             if raw.strip() and not raw.lstrip().startswith("#")]
    if not lines:
        raise ValueError("文稿为空")
    matches = [_LINE.match(raw) for _, raw in lines]
    labeled = all(matches) and (default_speaker is None or all(
        m.group("speaker").strip() == default_speaker for m in matches if m))
    rows = []
    if labeled:
        for match in matches:
            assert match is not None
            start, end = match.group("start"), match.group("end")
            rows.append({
                "speaker": match.group("speaker").strip(),
                "text": match.group("text").strip(),
                "start": _seconds(start) if start is not None else None,
                "end": _seconds(end) if end is not None else None,
            })
    elif default_speaker:
        # 一部分带当前人物标签、一部分不带属于歧义输入；显式拒绝，避免把标签念出来。
        if any(m and m.group("speaker").strip() == default_speaker for m in matches):
            raise ValueError("单人文稿不能混用带人物名称和不带人物名称的行")
        timed = [_PLAIN_TIMED.match(raw) for _, raw in lines]
        if all(timed):
            for match in timed:
                assert match is not None
                rows.append({"speaker": default_speaker, "text": match.group("text").strip(),
                             "start": _seconds(match.group("start")),
                             "end": _seconds(match.group("end"))})
        elif any(raw.startswith("[") for _, raw in lines):
            raise ValueError("单人时间戳必须全部提供或全部省略，不能混用")
        else:
            # 按句而不是按整篇生成，避免长稿变成一个超长渠道任务。
            for sentence in split_sentences("\n".join(raw for _, raw in lines)):
                rows.append({"speaker": default_speaker, "text": sentence,
                             "start": None, "end": None})
    else:
        lineno, raw = next(((n, r) for (n, r), m in zip(lines, matches) if not m), lines[0])
        raise ValueError(f"多人物文稿第 {lineno} 行缺少说话人标签：{raw}")
    timed = [r["start"] is not None for r in rows]
    if any(timed) and not all(timed):
        raise ValueError("时间戳必须全部提供或全部省略，不能混用")
    return rows


def _require_timed_transcript(text: str, default_speaker: str | None = None) -> None:
    """逐段硬检；在任何时间轴处理或渠道调用之前给出准确行号。"""
    missing_labels, missing_times = [], []
    for lineno, raw in enumerate(text.splitlines(), 1):
        value = raw.strip()
        if not value or value.startswith("#"):
            continue
        if default_speaker and _PLAIN_TIMED.match(value):
            # Check this before _LINE: the colon inside ``00:00`` can otherwise
            # be interpreted as a speaker separator when the label is omitted.
            continue
        match = _LINE.match(value)
        if not match:
            missing_labels.append(lineno)
        elif match.group("start") is None or match.group("end") is None:
            missing_times.append(lineno)
    if missing_labels:
        shown = ", ".join(map(str, missing_labels[:20]))
        suffix = "；单人稿可用 [起始-结束] 台词" if default_speaker else ""
        raise ValueError(f"数字人文稿第 {shown} 行缺少人物名称或合法台词格式{suffix}")
    if missing_times:
        shown = ", ".join(map(str, missing_times[:20]))
        raise ValueError(
            f"数字人文稿第 {shown} 行缺少完整起止时间；每段必须使用："
            "[00:00.000-00:03.200] 主持人：台词")


def _merge_consecutive(rows: list[dict]) -> list[dict]:
    out: list[dict] = []
    for row in rows:
        if out and out[-1]["speaker"] == row["speaker"] and (
                row["start"] is None or abs(out[-1]["end"] - row["start"]) <= 0.05):
            out[-1]["text"] += " " + row["text"]
            if row["end"] is not None:
                out[-1]["end"] = row["end"]
        else:
            out.append(dict(row))
    return out


def _text_weight(text: str) -> int:
    return max(1, len(re.sub(r"\s+", "", text)))


def _split_text(text: str, target_chars: int) -> list[str]:
    """按自然标点拆长句，必要时按字符硬切；拼接后与原文本一致。"""
    pieces = re.findall(r".+?[，,；;。！？!?](?:[”’\"']*)|.+$", text)
    atoms: list[str] = []
    for piece in pieces:
        while _text_weight(piece) > target_chars:
            cut = min(len(piece), target_chars)
            atoms.append(piece[:cut])
            piece = piece[cut:]
        if piece:
            atoms.append(piece)
    groups: list[str] = []
    current = ""
    for atom in atoms:
        if current and _text_weight(current + atom) > target_chars:
            groups.append(current)
            current = atom
        else:
            current += atom
    if current:
        groups.append(current)
    return groups or [text]


def _split_long_rows(rows: list[dict], total_duration_s: float,
                     max_segment_s: float = MAX_AVATAR_SEGMENT_S) -> list[dict]:
    """沿用 audio-to-video 的短任务纪律，把估时过长的说话轮次拆成可恢复小段。"""
    total_weight = sum(_text_weight(row["text"]) for row in rows)
    out: list[dict] = []
    for row in rows:
        if row["start"] is not None:
            estimated_s = float(row["end"]) - float(row["start"])
        else:
            estimated_s = total_duration_s * _text_weight(row["text"]) / total_weight
        if estimated_s <= max_segment_s + 0.001:
            out.append(dict(row))
            continue
        target_chars = max(1, int(_text_weight(row["text"]) * max_segment_s / estimated_s))
        parts = _split_text(row["text"], target_chars)
        if len(parts) == 1:
            out.append(dict(row))
            continue
        weights = [_text_weight(part) for part in parts]
        if row["start"] is None:
            for part in parts:
                out.append({**row, "text": part})
            continue
        start, span = float(row["start"]), estimated_s
        cursor = start
        for index, (part, weight) in enumerate(zip(parts, weights)):
            end = (float(row["end"]) if index == len(parts) - 1
                   else cursor + (float(row["end"]) - cursor) * weight
                   / sum(weights[index:]))
            out.append({**row, "text": part, "start": cursor, "end": end})
            cursor = end
    return out


def _load_speakers(cast: str | None, speaker_images: dict | None) -> dict:
    speakers = {}
    if cast:
        cast_path = Path(cast)
        if not cast_path.is_file():
            raise RuntimeError(f"人物映射文件不存在：{cast_path}")
        cast_data = json.loads(cast_path.read_text(encoding="utf-8"))
        speakers.update(cast_data.get("speakers") or cast_data)
    speakers.update(speaker_images or {})
    if not speakers:
        raise ValueError("至少需要一个人物映射（自然语言流程会自动整理；CLI 用 --speaker-image）")
    for name, item in speakers.items():
        if not isinstance(item, dict) or not item.get("image"):
            raise ValueError(f"人物映射中的 {name} 缺少 image")
        if not Path(item["image"]).is_file():
            raise ValueError(f"{name} 的数字人图片不存在：{item['image']}")
    return speakers


def _require_generated_transcript_ready(transcript_path: Path, audio_path: Path) -> None:
    """Honor ASR/diarization readiness before any paid avatar plan is built."""
    metadata_path = transcript_path.with_name("transcription.json")
    if not metadata_path.is_file():
        return
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        recorded = Path(str(metadata.get("transcript") or "")).resolve()
    except (OSError, ValueError, TypeError):
        return
    if recorded != transcript_path.resolve():
        return
    audio_meta = metadata.get("audio") or {}
    if audio_meta.get("sha256") != file_sha256(str(audio_path)):
        raise ValueError("自动转写稿对应的音频 SHA256 与当前母带不一致，必须重新转写")
    transcript_sha = metadata.get("transcript_sha256")
    if transcript_sha and transcript_sha != file_sha256(str(transcript_path)):
        raise ValueError("自动转写稿内容已改变，说话人置信度结论已失效；请重新转写或将修订稿作为用户文稿导入")
    if metadata.get("ready_for_digital_human") is not True:
        diarization = metadata.get("diarization") or {}
        raise ValueError(
            "自动转写稿的说话人聚类未达到付费生成条件："
            f"confidence={diarization.get('confidence')}, "
            f"required={diarization.get('minimum_confidence')}；请确认人物映射或提供文稿")


def build_dialogue_track(audio: str, transcript: str, cast: str | None = None,
                         speaker_images: dict | None = None) -> dict:
    audio_path, transcript_path = map(Path, (audio, transcript))
    for path in (audio_path, transcript_path):
        if not path.is_file():
            raise RuntimeError(f"输入文件不存在：{path}")
    duration = probe_duration(str(audio_path))
    speakers = _load_speakers(cast, speaker_images)
    _require_generated_transcript_ready(transcript_path, audio_path)
    transcript_text = transcript_path.read_text(encoding="utf-8")
    default_speaker = next(iter(speakers)) if len(speakers) == 1 else None
    _require_timed_transcript(transcript_text, default_speaker=default_speaker)
    rows = parse_labeled_transcript(transcript_text, default_speaker=default_speaker)
    has_explicit_timestamps = rows[0]["start"] is not None
    if not has_explicit_timestamps:
        raise ValueError(
            "数字人文稿每段必须提供完整起止时间；多人稿还必须有人物名称。"
            "格式：[00:00.000-00:03.200] 主持人：台词；禁止按字数估算换人边界")
    # 与 audio-to-video 相同，把云端生成任务控制为短段；人物不变时也允许无缝续接。
    # 不再合并相邻同一说话人，避免一行续稿形成几十秒的单个渠道任务。
    rows = _split_long_rows(rows, duration)
    unknown = sorted({r["speaker"] for r in rows} - set(speakers))
    if unknown:
        raise ValueError(f"文稿说话人未出现在人物映射：{', '.join(unknown)}")

    if abs(rows[0]["start"]) > 0.05 or abs(rows[-1]["end"] - duration) > 0.05:
        raise ValueError(f"显式时间轴必须覆盖整条母带 0–{duration:.3f}s")
    for previous, current in zip(rows, rows[1:]):
        if abs(previous["end"] - current["start"]) > 0.05:
            raise ValueError("显式时间轴必须连续、无重叠、无空洞")

    utterances = []
    for index, row in enumerate(rows, 1):
        start, end = float(row["start"]), float(row["end"])
        if end <= start:
            raise ValueError(f"第 {index} 段时间非法：{start}–{end}")
        utterances.append({
            "id": f"utt{index:04d}", "speaker": row["speaker"], "text": row["text"],
            "start": round(start, 3), "end": round(end, 3),
            "duration_s": round(end - start, 3),
            "image": speakers[row["speaker"]]["image"],
            # Kling Avatar 当前最短 2s；极短接话用静帧，避免伪造口型或污染母带。
            "render_mode": "avatar" if end - start >= 2.0 else "static",
        })
    return {
        "schema_version": 1,
        "master_audio": str(audio_path),
        "master_sha256": file_sha256(str(audio_path)),
        "duration_s": round(duration, 3),
        "max_avatar_segment_s": MAX_AVATAR_SEGMENT_S,
        "alignment_mode": "explicit_timestamps",
        "transcript": str(transcript_path),
        "cast": speakers,
        "utterances": utterances,
    }


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode:
        raise RuntimeError(f"命令失败：{' '.join(cmd[:8])}\n{proc.stderr[-1500:]}")


def _static_clip(image: str, duration: float, output: str) -> None:
    target = Path(output)
    partial = target.with_name(target.stem + ".part" + target.suffix)
    _run(["ffmpeg", "-y", "-loop", "1", "-i", image, "-t", f"{duration:.3f}",
          "-vf", "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720",
          "-an", "-r", "24", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(partial)])
    partial.replace(target)


def _write_static_job(path: Path, utt: dict, clip: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {
        "schema_version": 1, "utterance_id": utt["id"], "provider": "static",
        "status": "completed", "output": str(clip), "duration_s": utt["duration_s"],
        "completed_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _init_avatar_job(path: Path, utt: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    value = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    value.update({"schema_version": 1, "utterance_id": utt["id"],
                  "speaker": utt["speaker"], "duration_s": utt["duration_s"]})
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def render_status(track_path: str, output_dir: str) -> dict:
    track = json.loads(Path(track_path).read_text(encoding="utf-8"))
    out_dir = Path(output_dir)
    jobs_dir = out_dir / "jobs"
    rows, counts = [], {}
    for utt in track["utterances"]:
        clip, job_path = out_dir / f"{utt['id']}.mp4", jobs_dir / f"{utt['id']}.json"
        try:
            job = json.loads(job_path.read_text(encoding="utf-8")) if job_path.is_file() else {}
        except (OSError, json.JSONDecodeError):
            job = {"status": "invalid_job", "error": f"任务台账损坏：{job_path}"}
        clip_valid = False
        if clip.is_file() and clip.stat().st_size:
            try:
                clip_valid = probe_duration(str(clip)) + 0.08 >= float(utt["duration_s"])
            except (RuntimeError, OSError, json.JSONDecodeError):
                pass
        status = ("completed" if clip_valid and job.get("status") == "completed" else
                  "invalid_clip" if clip.is_file() and not clip_valid else
                  str(job.get("status") or "pending"))
        counts[status] = counts.get(status, 0) + 1
        rows.append({"id": utt["id"], "speaker": utt["speaker"],
                     "render_mode": utt["render_mode"], "status": status,
                     "provider": job.get("provider"), "task_id": job.get("task_id"),
                     "error": job.get("error")})
    completed = counts.get("completed", 0)
    return {"schema_version": 1, "total": len(rows), "completed": completed,
            "pending": len(rows) - completed, "complete": completed == len(rows),
            "counts": counts, "utterances": rows}


def render_track(track_path: str, output_dir: str, only: set[str] | None = None,
                 dry_run: bool = False, retry_failed: bool = False) -> list[str]:
    require_tools("ffmpeg", "ffprobe")
    resolved_track = Path(track_path).resolve()
    track = json.loads(resolved_track.read_text(encoding="utf-8"))
    # dialogue_track.json belongs in <project>/digital-human/.  Resolve its
    # project-relative media references from that project rather than from the
    # caller's current working directory, which is normally the workspace root.
    project_root = resolved_track.parent.parent

    def track_media_path(value: str) -> str:
        path = Path(value)
        return str(path if path.is_absolute() else project_root / path)

    master = track_media_path(track["master_audio"])
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    jobs_dir = out_dir / "jobs"
    selected = [utt for utt in track["utterances"] if not only or utt["id"] in only]
    if only:
        unknown = sorted(only - {utt["id"] for utt in track["utterances"]})
        if unknown:
            raise ValueError(f"未知 utterance id：{', '.join(unknown)}")
    outputs = []
    for utt in selected:
        clip = out_dir / f"{utt['id']}.mp4"
        audio = out_dir / f"{utt['id']}.mp3"
        job_path = jobs_dir / f"{utt['id']}.json"
        outputs.append(str(clip))
        if dry_run:
            continue
        if clip.is_file() and clip.stat().st_size:
            try:
                if probe_duration(str(clip)) + 0.08 >= float(utt["duration_s"]):
                    if utt["render_mode"] == "static":
                        _write_static_job(job_path, utt, clip)
                        continue
                    if job_path.is_file():
                        job = json.loads(job_path.read_text(encoding="utf-8"))
                        if job.get("task_id"):
                            job.update({"status": "completed", "provider_status": "completed",
                                        "output": str(clip), "utterance_id": utt["id"]})
                            tmp = job_path.with_name(job_path.name + ".tmp")
                            tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2) + "\n",
                                           encoding="utf-8")
                            tmp.replace(job_path)
                            continue
            except (RuntimeError, OSError, json.JSONDecodeError):
                pass
        if utt["render_mode"] == "static":
            _static_clip(track_media_path(utt["image"]), utt["duration_s"], str(clip))
            _write_static_job(job_path, utt, clip)
        else:
            # 片段音频只是口型驱动输入，可转成 128k MP3 控制 Kling 的 5MB 上限；
            # 最终成片从未使用这里的音频，仍原样直拷贝 master_audio。
            audio_valid = False
            if audio.is_file() and audio.stat().st_size:
                try:
                    audio_valid = abs(probe_duration(str(audio)) - float(utt["duration_s"])) <= 0.12
                except RuntimeError:
                    pass
            if not audio_valid:
                audio_partial = audio.with_name(audio.stem + ".part" + audio.suffix)
                _run(["ffmpeg", "-y", "-ss", str(utt["start"]), "-to", str(utt["end"]),
                      "-i", master, "-vn", "-c:a", "libmp3lame", "-b:a", "128k",
                      str(audio_partial)])
                audio_partial.replace(audio)
            _init_avatar_job(job_path, utt)
            generate_avatar(track_media_path(utt["image"]), str(audio), str(clip),
                            prompt="single person speaking naturally, steady camera",
                            job_path=str(job_path), retry_failed=retry_failed)
        if not clip.is_file() or probe_duration(str(clip)) + 0.08 < float(utt["duration_s"]):
            raise RuntimeError(f"{utt['id']} 渠道片段未覆盖计划时长")
    return outputs


def compose(track_path: str, clips_dir: str, output: str,
            width: int = 1280, height: int = 720, fps: int = 24) -> str:
    require_tools("ffmpeg", "ffprobe")
    track = json.loads(Path(track_path).read_text(encoding="utf-8"))
    clips = Path(clips_dir)
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="digitalhuman-compose-",
                                     dir=str(output_path.parent)) as tmp_name:
        tmp = Path(tmp_name)
        normalized = []
        for i, utt in enumerate(track["utterances"]):
            source = clips / f"{utt['id']}.mp4"
            if not source.is_file():
                raise RuntimeError(f"缺少数字人片段：{source}")
            target = tmp / f"{i:04d}.mp4"
            duration = float(utt["duration_s"])
            vf = (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                  f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,fps={fps},"
                  f"tpad=stop_mode=clone:stop_duration={duration:.3f},"
                  f"trim=duration={duration:.3f},setpts=PTS-STARTPTS")
            _run(["ffmpeg", "-y", "-i", str(source), "-vf", vf, "-an",
                  "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps),
                  "-video_track_timescale", str(fps * 1000), str(target)])
            normalized.append(target)
        manifest = tmp / "concat.txt"
        manifest.write_text("".join(f"file '{p.as_posix()}'\n" for p in normalized),
                            encoding="utf-8")
        video_only = tmp / "video.mp4"
        _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(manifest),
              "-an", "-c:v", "copy", str(video_only)])
        # 关键纪律：不使用 -shortest，不解码、不重编码母带音轨。
        _run(["ffmpeg", "-y", "-i", str(video_only), "-i", track["master_audio"],
              "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "copy",
              "-movflags", "+faststart", str(output_path)])
    return str(output_path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="逐段带时间戳和人物标签的数字人视频流水线")
    sub = ap.add_subparsers(dest="cmd", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--audio", required=True)
    plan.add_argument("--transcript", required=True)
    plan.add_argument("--cast", help="可选人物映射 JSON；项目插件会从自然语言自动生成")
    plan.add_argument("--speaker-image", action="append", default=[], metavar="NAME=PATH",
                      help="不使用 cast.json 时直接指定人物，可重复；单人稿只传一次")
    plan.add_argument("--output", required=True)
    render = sub.add_parser("render")
    render.add_argument("--track", required=True)
    render.add_argument("--output-dir", required=True)
    render.add_argument("--only", action="append", default=[])
    render.add_argument("--dry-run", action="store_true")
    render.add_argument("--retry-failed", action="store_true",
                        help="仅对渠道已明确失败的片段重新提交；中断任务默认直接恢复")
    status = sub.add_parser("status")
    status.add_argument("--track", required=True)
    status.add_argument("--output-dir", required=True)
    status.add_argument("--output", help="可选：把状态快照原子写入 JSON 文件")
    mux = sub.add_parser("compose")
    mux.add_argument("--track", required=True)
    mux.add_argument("--clips-dir", required=True)
    mux.add_argument("--output", required=True)
    mux.add_argument("--width", type=int, default=1280)
    mux.add_argument("--height", type=int, default=720)
    mux.add_argument("--fps", type=int, default=24)
    args = ap.parse_args(argv)
    if args.cmd == "plan":
        speaker_images = {}
        for value in args.speaker_image:
            if "=" not in value:
                plan.error("--speaker-image 格式必须是 NAME=PATH")
            name, image = value.split("=", 1)
            if not name.strip() or not image.strip():
                plan.error("--speaker-image 的人物名称和图片路径不能为空")
            speaker_images[name.strip()] = {"image": image.strip()}
        result = build_dialogue_track(args.audio, args.transcript, args.cast,
                                      speaker_images=speaker_images)
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                     encoding="utf-8")
        print(args.output)
    elif args.cmd == "render":
        print("\n".join(render_track(args.track, args.output_dir, set(args.only), args.dry_run,
                                     args.retry_failed)))
    elif args.cmd == "status":
        result = render_status(args.track, args.output_dir)
        rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            target = Path(args.output)
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".tmp")
            tmp.write_text(rendered, encoding="utf-8")
            tmp.replace(target)
        print(rendered, end="")
    else:
        print(compose(args.track, args.clips_dir, args.output,
                      args.width, args.height, args.fps))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
