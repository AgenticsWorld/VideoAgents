#!/usr/bin/env python3
"""数字人对白流水线机检：标签时间轴、片段覆盖、母带零重编码。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from modules.avsync import (compare_audio_frames, file_sha256, probe_audio_info,
                            probe_duration)  # noqa: E402


def check(track_path: str, clips_dir: str | None, final: str | None,
          require: str) -> dict:
    errors, warnings = [], []
    path = Path(track_path)
    if not path.is_file():
        return {"ok": False, "errors": [f"dialogue_track 不存在：{path}"], "warnings": []}
    track = json.loads(path.read_text(encoding="utf-8"))
    master = Path(track.get("master_audio") or "")
    utterances = track.get("utterances") or []
    if track.get("schema_version") != 1:
        errors.append("schema_version 必须为 1")
    if not master.is_file():
        errors.append(f"母带不存在：{master}")
    elif file_sha256(str(master)) != track.get("master_sha256"):
        errors.append("母带 SHA256 与规划基线不一致")
    if len(track.get("cast") or {}) < 1:
        errors.append("至少需要一个数字人")
    if track.get("alignment_mode") != "explicit_timestamps":
        errors.append("数字人时间轴必须来自逐段显式时间戳，禁止使用字数估时")
    if not utterances:
        errors.append("utterances 为空")
    cursor = 0.0
    for item in utterances:
        start, end = float(item.get("start", -1)), float(item.get("end", -1))
        if abs(start - cursor) > 0.051:
            errors.append(f"{item.get('id')} 与上一段不连续：{cursor:.3f} → {start:.3f}")
        if end <= start or not item.get("speaker") or not item.get("text"):
            errors.append(f"{item.get('id')} 段数据非法")
        if item.get("speaker") not in (track.get("cast") or {}):
            errors.append(f"{item.get('id')} 的说话人未登记")
        cursor = end
    duration = float(track.get("duration_s") or 0)
    if utterances and abs(cursor - duration) > 0.051:
        errors.append(f"时间轴未覆盖母带末尾：{cursor:.3f} / {duration:.3f}")
    if master.is_file() and abs(probe_duration(str(master)) - duration) > 0.051:
        errors.append("母带实测时长与时间轴基线不一致")

    if require in {"clips", "final"}:
        base = Path(clips_dir or "")
        jobs = base / "jobs"
        for item in utterances:
            clip = base / f"{item['id']}.mp4"
            job_path = jobs / f"{item['id']}.json"
            if not job_path.is_file():
                errors.append(f"缺少可恢复任务台账：{job_path}")
            else:
                try:
                    job = json.loads(job_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    errors.append(f"任务台账损坏：{job_path}")
                    job = {}
                if job.get("utterance_id") != item.get("id"):
                    errors.append(f"{item.get('id')} 任务台账与片段不匹配")
                if job.get("status") != "completed":
                    errors.append(
                        f"{item.get('id')} 渠道任务尚未完成：{job.get('status') or 'pending'}")
                provider = job.get("provider")
                if provider not in {"static", "heygen", "klingai", "comfyui"}:
                    errors.append(f"{item.get('id')} 任务台账渠道非法：{provider or 'missing'}")
                if item.get("render_mode") == "avatar" and provider == "static":
                    errors.append(f"{item.get('id')} 口型片段不能用静帧台账冒充")
                if provider != "static" and not job.get("task_id"):
                    errors.append(f"{item.get('id')} 渠道任务缺少可恢复 task_id")
            if not clip.is_file():
                errors.append(f"缺少片段：{clip}")
                continue
            actual = probe_duration(str(clip))
            if actual + 0.08 < float(item["duration_s"]):
                errors.append(f"{item['id']} 片段过短：{actual:.3f}s < {item['duration_s']:.3f}s")

    audio_check = None
    if require == "final":
        target = Path(final or "")
        if not target.is_file():
            errors.append(f"成片不存在：{target}")
        elif master.is_file():
            final_duration = probe_duration(str(target))
            if abs(final_duration - duration) > 0.12:
                errors.append(f"成片时长偏差：{final_duration:.3f}s / {duration:.3f}s")
            if probe_audio_info(str(target)) != probe_audio_info(str(master)):
                errors.append("成片音频流参数与母带不一致")
            audio_check = compare_audio_frames(str(master), str(target))
            if not audio_check["ok"]:
                errors.append("成片音频帧与母带不一致：禁止重编码或截断母带")
    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "utterance_count": len(utterances), "duration_s": duration,
            "audio_frame_check": audio_check}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--track", required=True)
    ap.add_argument("--clips-dir")
    ap.add_argument("--final")
    ap.add_argument("--require", choices=("plan", "clips", "final"), default="final")
    args = ap.parse_args(argv)
    if args.require in {"clips", "final"} and not args.clips_dir:
        ap.error("--require clips/final 时必须提供 --clips-dir")
    if args.require == "final" and not args.final:
        ap.error("--require final 时必须提供 --final")
    result = check(args.track, args.clips_dir, args.final, args.require)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
