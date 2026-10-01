#!/usr/bin/env python3
"""Local audio transcription with reusable, timestamped outputs.

The ASR model is loaded through faster-whisper.  Named models are downloaded on
first use below ``data/models/faster-whisper/`` (or
``VIDEOAGENTS_DATA_DIR/models/faster-whisper``) and reused by later jobs.

Which model: the one selected in 设置 → 高级 → 语音输入 (``state.json#voice_input.model``,
read through ``voice_input.selected_model``) — the single switch shared by the chat
voice input, the footage library, plugin flows and speech alignment.  ``--model`` /
``model_name`` only override it for an explicit user request.

This module deliberately does not alternate cast names.  A single speaker can
be supplied with ``--speaker``; multi-speaker input can use explicit turns or a
lightweight local acoustic clustering pass mapped by first appearance or pitch.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
from pathlib import Path
from typing import Iterable

import numpy as np
from scipy.fft import dct

try:
    from modules.avsync import file_sha256, probe_duration
    from modules.voice_input import selected_model
except ModuleNotFoundError:  # python modules/transcription.py ...
    from avsync import file_sha256, probe_duration
    from voice_input import selected_model


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("VIDEOAGENTS_DATA_DIR", ROOT / "data")).expanduser().resolve()
MODEL_ROOT = DATA_DIR / "models" / "faster-whisper"
DEFAULT_DIARIZATION_CONFIDENCE = 0.35
_TURN_BREAK = re.compile(r"[。！？!?；;…]\s*$")


def format_timestamp(seconds: float) -> str:
    """Format non-negative seconds as ``HH:MM:SS.mmm``."""
    millis = max(0, round(float(seconds) * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def resolve_model(model_name: str | None = None) -> str:
    """Explicit name/path wins; otherwise the model selected in the voice-input settings."""
    return model_name or selected_model()


def load_model(model_name: str | None = None, device: str = "auto",
               compute_type: str = "default"):
    """Load a model, downloading named checkpoints into ``MODEL_ROOT`` once."""
    model_name = resolve_model(model_name)
    # 与设置页下载同口径:hf_xet 经本机系统代理会在最后一块永久挂起,首次自动下载一律走纯 HTTP
    # (须在 import huggingface_hub 之前设置)。
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "缺少 faster-whisper；请重新安装/升级 VideoAgents 宿主后再运行音频转文字"
        ) from exc
    MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    source = Path(model_name).expanduser()
    model_ref = str(source.resolve()) if source.is_dir() else model_name
    return WhisperModel(model_ref, device=device, compute_type=compute_type,
                        download_root=str(MODEL_ROOT))


def _word_dict(word) -> dict:
    return {
        # faster-whisper uses a leading space to delimit Latin words; preserve
        # it so regrouping words at speaker boundaries does not join English.
        "text": str(getattr(word, "word", "")),
        "start": round(float(getattr(word, "start", 0.0)), 3),
        "end": round(float(getattr(word, "end", 0.0)), 3),
        "probability": round(float(getattr(word, "probability", 0.0)), 4),
    }


def run_asr(audio: Path, model_name: str | None = None, language: str | None = None,
            initial_prompt: str | None = None, device: str = "auto",
            compute_type: str = "default") -> tuple[list[dict], dict]:
    """Return faster-whisper segments plus detected-language metadata."""
    model = load_model(model_name, device=device, compute_type=compute_type)
    segments, info = model.transcribe(
        str(audio), language=language, initial_prompt=initial_prompt,
        word_timestamps=True, vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500}, beam_size=5,
    )
    rows = []
    for seg in segments:  # iteration is what actually runs faster-whisper
        text = str(getattr(seg, "text", "")).strip()
        if not text:
            continue
        words = [_word_dict(word) for word in (getattr(seg, "words", None) or [])]
        words = [word for word in words if word["text"].strip()
                 and word["end"] > word["start"]]
        rows.append({
            "text": text,
            "start": round(float(seg.start), 3),
            "end": round(float(seg.end), 3),
            "words": words,
            "avg_logprob": round(float(getattr(seg, "avg_logprob", 0.0)), 4),
        })
    meta = {
        "language": getattr(info, "language", language),
        "language_probability": round(float(
            getattr(info, "language_probability", 0.0) or 0.0), 4),
        "duration_after_vad": round(float(
            getattr(info, "duration_after_vad", 0.0) or 0.0), 3),
    }
    return rows, meta


def _decode_audio(path: Path, sampling_rate: int = 16000) -> np.ndarray:
    """Decode to mono float32 through faster-whisper's bundled PyAV backend."""
    try:
        from faster_whisper.audio import decode_audio  # type: ignore
    except ImportError as exc:
        raise RuntimeError("轻量说话人聚类需要 faster-whisper/PyAV 音频解码后端") from exc
    return np.asarray(decode_audio(str(path), sampling_rate=sampling_rate), dtype=np.float32)


def _asr_units(segments: list[dict], pause_s: float = 0.38,
               max_unit_s: float = 3.5) -> list[dict]:
    """Group word timestamps into short, likely single-speaker acoustic units."""
    atoms = []
    for row in segments:
        words = row.get("words") or []
        if words:
            atoms.extend({"text": str(word["text"]), "start": float(word["start"]),
                          "end": float(word["end"]), "word": dict(word)} for word in words)
        else:
            atoms.append({"text": str(row["text"]), "start": float(row["start"]),
                          "end": float(row["end"]), "word": None})
    atoms.sort(key=lambda row: (row["start"], row["end"]))
    if not atoms:
        raise ValueError("ASR 没有可用于说话人聚类的时间单元")
    units: list[dict] = []
    for atom in atoms:
        if units:
            current = units[-1]
            gap = atom["start"] - current["end"]
            punctuation_break = bool(_TURN_BREAK.search(current["text"])) and gap >= 0.10
            if gap > pause_s or punctuation_break or atom["end"] - current["start"] > max_unit_s:
                current = None
            else:
                current["text"] += atom["text"]
                current["end"] = max(current["end"], atom["end"])
                if atom["word"]:
                    current["words"].append(atom["word"])
                continue
        units.append({"text": atom["text"], "start": atom["start"], "end": atom["end"],
                      "words": [atom["word"]] if atom["word"] else []})
    return units


def _frame_audio(samples: np.ndarray, frame: int = 400, hop: int = 160) -> np.ndarray:
    if samples.size < frame:
        samples = np.pad(samples, (0, frame - samples.size))
    count = 1 + max(0, (samples.size - frame) // hop)
    indexes = np.arange(frame)[None, :] + hop * np.arange(count)[:, None]
    return samples[indexes]


def _mel_filters(sample_rate: int = 16000, n_fft: int = 512,
                 count: int = 26) -> np.ndarray:
    def hz_to_mel(value):
        return 2595.0 * np.log10(1.0 + value / 700.0)

    def mel_to_hz(value):
        return 700.0 * (10 ** (value / 2595.0) - 1.0)

    points = mel_to_hz(np.linspace(hz_to_mel(70.0), hz_to_mel(sample_rate / 2), count + 2))
    bins = np.floor((n_fft + 1) * points / sample_rate).astype(int)
    filters = np.zeros((count, n_fft // 2 + 1), dtype=np.float64)
    for index in range(1, count + 1):
        left, center, right = bins[index - 1:index + 2]
        center = max(center, left + 1)
        right = max(right, center + 1)
        for pos in range(left, min(center, filters.shape[1])):
            filters[index - 1, pos] = (pos - left) / max(1, center - left)
        for pos in range(center, min(right, filters.shape[1])):
            filters[index - 1, pos] = (right - pos) / max(1, right - center)
    return filters


_MEL_FILTERS = _mel_filters()


def _median_pitch(frames: np.ndarray, sample_rate: int = 16000) -> tuple[float | None, float]:
    pitches, qualities = [], []
    min_lag, max_lag = int(sample_rate / 350), int(sample_rate / 70)
    n_fft = 1 << math.ceil(math.log2(frames.shape[1] * 2 - 1))
    for frame in frames[::2]:
        centered = frame - np.mean(frame)
        energy = float(np.dot(centered, centered))
        if energy < 1e-5:
            continue
        spectrum = np.fft.rfft(centered, n=n_fft)
        corr = np.fft.irfft(spectrum * np.conj(spectrum), n=n_fft)[:frames.shape[1]]
        window = corr[min_lag:min(max_lag + 1, corr.size)]
        if not window.size or corr[0] <= 0:
            continue
        lag = min_lag + int(np.argmax(window))
        quality = float(corr[lag] / corr[0])
        if quality >= 0.22:
            pitches.append(sample_rate / lag)
            qualities.append(quality)
    if not pitches:
        return None, 0.0
    return round(float(np.median(pitches)), 2), round(float(np.median(qualities)), 3)


def _unit_features(samples: np.ndarray, sample_rate: int = 16000) -> tuple[np.ndarray, dict]:
    """Return a compact MFCC/pitch feature vector and diagnostics for one unit."""
    if not samples.size:
        raise ValueError("说话人聚类遇到空音频区间")
    peak = float(np.max(np.abs(samples)))
    if peak > 0:
        samples = samples / peak
    frames = _frame_audio(samples)
    windowed = (frames - frames.mean(axis=1, keepdims=True)) * np.hanning(frames.shape[1])
    rms = np.sqrt(np.mean(windowed ** 2, axis=1) + 1e-12)
    voiced = windowed[rms >= max(1e-4, float(np.percentile(rms, 35)))]
    if not voiced.size:
        voiced = windowed
    power = np.abs(np.fft.rfft(voiced, n=512, axis=1)) ** 2
    log_mel = np.log(np.maximum(power @ _MEL_FILTERS.T, 1e-10))
    mfcc = dct(log_mel, type=2, axis=1, norm="ortho")[:, 1:14]
    frequencies = np.linspace(0, sample_rate / 2, power.shape[1])
    centroid = np.sum(power * frequencies, axis=1) / np.maximum(np.sum(power, axis=1), 1e-9)
    zcr = np.mean(np.abs(np.diff(np.signbit(voiced), axis=1)), axis=1)
    pitch_hz, pitch_quality = _median_pitch(voiced, sample_rate)
    vector = np.concatenate([
        np.mean(mfcc, axis=0), np.std(mfcc, axis=0),
        [float(np.mean(centroid) / (sample_rate / 2)), float(np.mean(zcr)),
         math.log10(float(np.mean(rms)) + 1e-8), (pitch_hz or 0.0) / 250.0,
         pitch_quality],
    ]).astype(np.float64)
    return vector, {"pitch_hz": pitch_hz, "pitch_quality": pitch_quality,
                    "voiced_frames": int(voiced.shape[0])}


def _deterministic_kmeans(features: np.ndarray, weights: np.ndarray,
                          clusters: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Small deterministic weighted k-means, avoiding another ML dependency."""
    if features.shape[0] < clusters:
        raise ValueError(f"可用语音段只有 {features.shape[0]} 个，无法区分 {clusters} 位说话人")
    spread = np.std(features, axis=0)
    normalized = (features - np.mean(features, axis=0)) / np.where(spread > 1e-8, spread, 1.0)
    chosen = [int(np.argmax(weights))]
    while len(chosen) < clusters:
        distances = np.min(np.stack([
            np.sum((normalized - normalized[index]) ** 2, axis=1) for index in chosen
        ]), axis=0)
        distances[chosen] = -1
        chosen.append(int(np.argmax(distances)))
    centroids = normalized[chosen].copy()
    labels = np.zeros(normalized.shape[0], dtype=int)
    for _ in range(50):
        distances = np.sum((normalized[:, None, :] - centroids[None, :, :]) ** 2, axis=2)
        next_labels = np.argmin(distances, axis=1)
        reserved: set[int] = set()
        for cluster in range(clusters):
            if not np.any(next_labels == cluster):
                candidates = np.argsort(np.min(distances, axis=1))[::-1]
                farthest = next((int(index) for index in candidates
                                 if int(index) not in reserved), int(candidates[0]))
                next_labels[farthest] = cluster
                reserved.add(farthest)
        next_centroids = np.stack([
            np.average(normalized[next_labels == cluster], axis=0,
                       weights=weights[next_labels == cluster])
            for cluster in range(clusters)
        ])
        if np.array_equal(labels, next_labels) and np.allclose(centroids, next_centroids):
            labels, centroids = next_labels, next_centroids
            break
        labels, centroids = next_labels, next_centroids
    distances = np.sqrt(np.sum((normalized[:, None, :] - centroids[None, :, :]) ** 2, axis=2))
    return labels, centroids, distances


def _parse_pitch_map(values: list[str] | None) -> dict[str, str]:
    mapping = {}
    aliases = {"low": "low", "high": "high", "male": "low", "female": "high",
               "低音": "low", "高音": "high", "男声": "low", "女声": "high"}
    for value in values or []:
        if "=" not in value:
            raise ValueError("--pitch-map 格式必须是 low=人物 或 high=人物")
        key, name = (part.strip() for part in value.split("=", 1))
        key = aliases.get(key.lower(), aliases.get(key, ""))
        if not key or not name:
            raise ValueError("--pitch-map 仅支持 low/high（兼容男声/女声别名）")
        mapping[key] = name
    if mapping and set(mapping) != {"low", "high"}:
        raise ValueError("--pitch-map 必须同时提供 low 和 high")
    return mapping


def diarize_speakers(segments: list[dict], audio: Path, num_speakers: int,
                     speaker_order: list[str] | None = None,
                     pitch_map: dict[str, str] | None = None,
                     min_confidence: float = DEFAULT_DIARIZATION_CONFIDENCE) -> tuple[list[dict], dict]:
    """Lightweight local speaker clustering and deterministic cast mapping."""
    if not 2 <= num_speakers <= 4:
        raise ValueError("轻量说话人聚类仅支持 2–4 人；当前数字人首版建议最多 2 人")
    speaker_order = [name.strip() for name in (speaker_order or []) if name.strip()]
    pitch_map = pitch_map or {}
    if not 0.0 <= min_confidence <= 1.0:
        raise ValueError("说话人聚类最低置信度必须在 0–1 之间")
    if speaker_order and pitch_map:
        raise ValueError("说话人首次出现顺序与低音/高音映射只能选择一种")
    if speaker_order and len(speaker_order) != num_speakers:
        raise ValueError("--speaker-order 人物数量必须等于 --num-speakers")
    if len(set(speaker_order)) != len(speaker_order):
        raise ValueError("--speaker-order 中的人物名称不能重复")
    if pitch_map and num_speakers != 2:
        raise ValueError("低音/高音映射只支持两人录音")
    if pitch_map and (set(pitch_map) != {"low", "high"}
                      or len(set(pitch_map.values())) != 2):
        raise ValueError("低音/高音必须分别映射到两个不同人物")

    samples, sample_rate = _decode_audio(audio), 16000
    units = _asr_units(segments)
    vectors, details, weights = [], [], []
    audio_duration = samples.size / sample_rate
    for index, unit in enumerate(units):
        left_limit = (0.0 if index == 0 else
                      (float(units[index - 1]["end"]) + float(unit["start"])) / 2)
        right_limit = (audio_duration if index == len(units) - 1 else
                       (float(unit["end"]) + float(units[index + 1]["start"])) / 2)
        start = max(left_limit, float(unit["start"]) - 0.06)
        end = min(right_limit, float(unit["end"]) + 0.06)
        # Very short replies need enough samples for stable MFCC/F0.  Expansion
        # remains inside the local neighborhood and is recorded as lower evidence.
        if end - start < 0.65:
            middle = (start + end) / 2
            start, end = max(left_limit, middle - 0.325), min(right_limit, middle + 0.325)
        vector, detail = _unit_features(samples[int(start * sample_rate):int(end * sample_rate)])
        vectors.append(vector)
        details.append(detail)
        weights.append(max(0.2, float(unit["end"]) - float(unit["start"])))
    labels, _centroids, distances = _deterministic_kmeans(
        np.stack(vectors), np.asarray(weights, dtype=np.float64), num_speakers)
    ordered_clusters = sorted(range(num_speakers), key=lambda cluster: min(
        units[index]["start"] for index, label in enumerate(labels) if label == cluster))
    cluster_names: dict[int, str] = {}
    mapping_mode = "unmapped"
    pitch_gap = None
    if speaker_order:
        cluster_names = dict(zip(ordered_clusters, speaker_order))
        mapping_mode = "first_appearance"
    elif pitch_map:
        cluster_pitch = {}
        for cluster in range(num_speakers):
            values = [details[index]["pitch_hz"] for index, label in enumerate(labels)
                      if label == cluster and details[index]["pitch_hz"]]
            cluster_pitch[cluster] = float(np.median(values)) if values else None
        if any(value is None for value in cluster_pitch.values()):
            raise ValueError("低音/高音映射失败：至少一个声纹簇无法测得稳定音高")
        low, high = sorted(cluster_pitch, key=lambda cluster: cluster_pitch[cluster])
        pitch_gap = round(cluster_pitch[high] - cluster_pitch[low], 2)
        cluster_names = {low: pitch_map["low"], high: pitch_map["high"]}
        mapping_mode = "pitch"
    else:
        cluster_names = {cluster: f"说话人{index + 1}"
                         for index, cluster in enumerate(ordered_clusters)}

    margins = []
    for index, label in enumerate(labels):
        own = distances[index, label]
        other = np.min(np.delete(distances[index], label))
        margins.append(max(0.0, float((other - own) / max(other, 1e-8))))
    confidence = float(np.average(margins, weights=np.asarray(weights)))
    cluster_duration = {cluster: round(sum(
        weights[index] for index, label in enumerate(labels) if label == cluster), 3)
        for cluster in range(num_speakers)}
    if min(cluster_duration.values()) < 0.5:
        confidence *= 0.6
    if mapping_mode == "pitch":
        confidence *= min(1.0, max(0.0, (pitch_gap or 0.0) / 55.0))
    confidence = round(confidence, 3)

    rows = []
    for index, unit in enumerate(units):
        speaker = cluster_names[int(labels[index])]
        row = {**unit, "speaker": speaker, "cluster": f"SPEAKER_{int(labels[index]):02d}",
               "cluster_margin": round(margins[index], 3),
               "pitch_hz": details[index]["pitch_hz"]}
        if rows and rows[-1]["speaker"] == speaker:
            rows[-1]["text"] += row["text"]
            rows[-1]["end"] = row["end"]
            rows[-1]["words"].extend(row["words"])
            rows[-1]["cluster_margin"] = round(min(
                rows[-1]["cluster_margin"], row["cluster_margin"]), 3)
        else:
            rows.append(row)
    ready = mapping_mode != "unmapped" and confidence >= min_confidence
    meta = {
        "backend": "lightweight-mfcc-pitch-kmeans",
        "num_speakers": num_speakers,
        "mapping_mode": mapping_mode,
        "cluster_names": {f"SPEAKER_{cluster:02d}": name
                          for cluster, name in cluster_names.items()},
        "confidence": confidence,
        "minimum_confidence": min_confidence,
        "pitch_gap_hz": pitch_gap,
        "cluster_duration_s": {f"SPEAKER_{cluster:02d}": value
                               for cluster, value in cluster_duration.items()},
        "ready_for_digital_human": ready,
        "warning": (None if ready else
                    "说话人聚类/人物映射置信度不足，文字稿可审阅但禁止进入付费数字人生成"),
    }
    return rows, meta


def load_speaker_turns(path: Path) -> list[dict]:
    """Read ``[{speaker,start,end}]`` or ``{"turns": [...]}`` speaker boundaries."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("turns") if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or not rows:
        raise ValueError("speaker-turns 必须是非空数组或包含 turns 数组的 JSON")
    turns = []
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError(f"speaker-turns 第 {index} 项必须是对象")
        speaker = str(row.get("speaker") or "").strip()
        try:
            start, end = float(row["start"]), float(row["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"speaker-turns 第 {index} 项缺少合法 start/end") from exc
        if not speaker or start < 0 or end <= start:
            raise ValueError(f"speaker-turns 第 {index} 项人物为空或时间范围非法")
        turns.append({"speaker": speaker, "start": start, "end": end})
    turns.sort(key=lambda row: (row["start"], row["end"]))
    for previous, current in zip(turns, turns[1:]):
        if current["start"] < previous["end"] - 0.001:
            raise ValueError("speaker-turns 不能重叠")
    return turns


def _turn_at(start: float, end: float, turns: list[dict]) -> int | None:
    midpoint = (start + end) / 2
    containing = [index for index, row in enumerate(turns)
                  if row["start"] <= midpoint < row["end"]]
    if containing:
        return containing[0]
    overlaps = [(max(0.0, min(end, row["end"]) - max(start, row["start"])), index)
                for index, row in enumerate(turns)]
    overlap, index = max(overlaps, key=lambda item: item[0], default=(0.0, None))
    return index if index is not None and overlap > 0 else None


def assign_speakers(segments: list[dict], speaker: str | None = None,
                    turns: list[dict] | None = None) -> list[dict]:
    """Attach speakers; word timestamps split ASR segments at explicit turn boundaries."""
    if speaker and turns:
        raise ValueError("--speaker 与 --speaker-turns 不能同时使用")
    if speaker:
        return [{**row, "speaker": speaker} for row in segments]
    if not turns:
        return [dict(row) for row in segments]

    atoms = []
    for row in segments:
        words = row.get("words") or []
        if words:
            for word in words:
                atoms.append({**word, "turn_index": _turn_at(
                    word["start"], word["end"], turns)})
        else:
            atoms.append({"text": row["text"], "start": row["start"], "end": row["end"],
                          "turn_index": _turn_at(row["start"], row["end"], turns)})
    missing = [atom for atom in atoms if atom["turn_index"] is None]
    if missing:
        first = missing[0]
        raise ValueError(
            "speaker-turns 未覆盖全部语音；首个未覆盖区间为 "
            f"{format_timestamp(first['start'])}-{format_timestamp(first['end'])}"
        )
    grouped = [{"speaker": turn["speaker"], "text": "", "start": turn["start"],
                "end": turn["end"], "words": []} for turn in turns]
    for atom in atoms:
        index = atom["turn_index"]
        assert index is not None
        grouped[index]["text"] += atom["text"]
        grouped[index]["words"].append({
            key: value for key, value in atom.items() if key != "turn_index"})
    empty = [index + 1 for index, row in enumerate(grouped) if not row["text"].strip()]
    if empty:
        raise ValueError(f"speaker-turns 第 {', '.join(map(str, empty))} 段没有识别到台词")
    return grouped


def validate_speaker_turn_coverage(turns: list[dict], duration: float,
                                   tolerance: float = 0.05) -> list[dict]:
    """Require explicit multi-speaker boundaries to cover the digital-human master."""
    if abs(turns[0]["start"]) > tolerance:
        raise ValueError("数字人 speaker-turns 首段必须从 0 开始")
    if abs(turns[-1]["end"] - duration) > tolerance:
        raise ValueError("数字人 speaker-turns 末段必须等于 ffprobe 母带时长")
    normalized = [dict(row) for row in turns]
    normalized[0]["start"] = 0.0
    normalized[-1]["end"] = duration
    for index, (previous, current) in enumerate(zip(normalized, normalized[1:]), 2):
        if abs(previous["end"] - current["start"]) > tolerance:
            raise ValueError(f"数字人 speaker-turns 第 {index - 1}/{index} 段之间有空洞")
        boundary = round((previous["end"] + current["start"]) / 2, 3)
        previous["end"] = current["start"] = boundary
    return normalized


def cover_audio_timeline(segments: list[dict], duration: float) -> list[dict]:
    """Expand speech-only ASR rows into a contiguous 0..duration timeline.

    Silence between adjacent rows is split at its midpoint.  This is suitable
    for digital-human video cuts because the original audio remains untouched.
    """
    if not segments:
        raise ValueError("ASR 没有识别到有效语音")
    if duration <= 0:
        raise ValueError("音频时长必须大于 0")
    rows = [dict(row) for row in segments]
    boundaries = [0.0]
    for previous, current in zip(rows, rows[1:]):
        left, right = float(previous["end"]), float(current["start"])
        boundary = (left + right) / 2 if right >= left else right
        boundary = min(duration, max(boundaries[-1], boundary))
        boundaries.append(boundary)
    boundaries.append(duration)
    for index, row in enumerate(rows):
        row["start"] = round(boundaries[index], 3)
        row["end"] = round(boundaries[index + 1], 3)
        if row["end"] <= row["start"]:
            raise ValueError(f"ASR 第 {index + 1} 段时间范围为空，无法生成连续时间轴")
    return rows


def render_transcript(segments: Iterable[dict], include_speaker: bool) -> str:
    lines = []
    for row in segments:
        prefix = f"[{format_timestamp(row['start'])}-{format_timestamp(row['end'])}] "
        if include_speaker:
            speaker = str(row.get("speaker") or "").strip()
            if not speaker:
                raise ValueError(
                    "数字人格式必须明确每段说话人：单人使用 --speaker；多人使用 --speaker-turns，"
                    "或使用 --diarize 配合明确映射；不能按交替顺序猜人物"
                )
            prefix += f"{speaker}："
        lines.append(prefix + str(row["text"]).strip())
    return "\n".join(lines) + "\n"


def transcribe(audio: str, output: str, json_output: str | None = None,
               model_name: str | None = None, language: str | None = None,
               initial_prompt: str | None = None, speaker: str | None = None,
               speaker_turns: str | None = None, digital_human: bool = False,
               device: str = "auto", compute_type: str = "default",
               diarize: bool = False, num_speakers: int | None = None,
               speaker_order: list[str] | None = None,
               pitch_map: dict[str, str] | None = None,
               min_diarization_confidence: float = DEFAULT_DIARIZATION_CONFIDENCE) -> dict:
    audio_path, output_path = Path(audio).resolve(), Path(output).resolve()
    if not audio_path.is_file():
        raise FileNotFoundError(f"音频不存在：{audio_path}")
    model_name = resolve_model(model_name)
    turns = load_speaker_turns(Path(speaker_turns).resolve()) if speaker_turns else None
    duration = probe_duration(str(audio_path))
    if speaker and turns:
        raise ValueError("--speaker 与 --speaker-turns 不能同时使用")
    if digital_human and turns:
        turns = validate_speaker_turn_coverage(turns, duration)
    if diarize and (speaker or turns):
        raise ValueError("--diarize 不能与 --speaker/--speaker-turns 同时使用")
    if not diarize and (num_speakers or speaker_order or pitch_map):
        raise ValueError("--num-speakers/--speaker-order/--pitch-map 必须与 --diarize 一起使用")
    raw, asr_meta = run_asr(audio_path, model_name=model_name, language=language,
                            initial_prompt=initial_prompt, device=device,
                            compute_type=compute_type)
    diarization = None
    if diarize:
        inferred_count = (num_speakers or len(speaker_order or [])
                          or len(pitch_map or {}))
        if inferred_count < 2:
            raise ValueError("--diarize 需要 --num-speakers，或提供完整人物顺序/低高音映射")
        assigned, diarization = diarize_speakers(
            raw, audio_path, inferred_count, speaker_order=speaker_order,
            pitch_map=pitch_map, min_confidence=min_diarization_confidence)
    else:
        assigned = assign_speakers(raw, speaker=speaker, turns=turns)
    # Explicit multi-speaker turns are the hard source of truth and must not be
    # moved to ASR word edges or silence midpoints.  Single-speaker ASR rows can
    # safely absorb adjacent silence to cover the master.
    final = (assigned if digital_human and turns else
             cover_audio_timeline(assigned, duration) if digital_human else assigned)
    transcript = render_transcript(final, include_speaker=digital_human)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(transcript, encoding="utf-8")

    payload = {
        "schema": "videoagents.transcription.v1",
        "audio": {"path": str(audio_path), "sha256": file_sha256(audio_path),
                  "duration_s": round(duration, 3)},
        "model": {"backend": "faster-whisper", "name": model_name,
                  "cache_root": str(MODEL_ROOT), "device": device,
                  "compute_type": compute_type},
        "asr": asr_meta,
        "format": "digital-human" if digital_human else "timeline",
        "speaker_attribution": ("single" if speaker else
                                "explicit_turns" if turns else
                                f"acoustic_{diarization['mapping_mode']}" if diarization else
                                "none"),
        "diarization": diarization,
        "ready_for_digital_human": (
            True if speaker or turns else
            bool(diarization and diarization["ready_for_digital_human"]) if digital_human else None),
        "transcript": str(output_path),
        "transcript_sha256": file_sha256(str(output_path)),
        "segments": final,
    }
    json_path = Path(json_output).resolve() if json_output else output_path.with_suffix(".json")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    payload["json_output"] = str(json_path)
    return payload


def doctor(model_name: str | None = None, download: bool = False,
           device: str = "auto", compute_type: str = "default") -> dict:
    model_name = resolve_model(model_name)
    try:
        import faster_whisper  # type: ignore
    except ImportError:
        return {"ok": False, "dependency": "faster-whisper", "model": model_name,
                "cache_root": str(MODEL_ROOT),
                "error": "缺少 faster-whisper，请重新安装/升级 VideoAgents 宿主"}
    MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    result = {"ok": True, "dependency": getattr(faster_whisper, "__version__", "unknown"),
              "model": model_name, "cache_root": str(MODEL_ROOT),
              "lightweight_diarization": "mfcc-pitch-kmeans",
              "cached_entries": sorted(path.name for path in MODEL_ROOT.iterdir())}
    if download:
        load_model(model_name, device=device, compute_type=compute_type)
        result["downloaded_or_loaded"] = True
        result["cached_entries"] = sorted(path.name for path in MODEL_ROOT.iterdir())
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="本地音频转文字（模型缓存到 data/models/）")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("doctor")
    check.add_argument("--model", default=None,
                       help="缺省=设置→高级→语音输入选中的识别模型；仅用户明确要求时才传")
    check.add_argument("--download", action="store_true", help="缺模型时立即下载并试加载")
    check.add_argument("--device", default="auto")
    check.add_argument("--compute-type", default="default")
    run = sub.add_parser("transcribe")
    run.add_argument("--audio", required=True)
    run.add_argument("--output", required=True)
    run.add_argument("--json-output")
    run.add_argument("--model", default=None,
                     help="缺省=设置→高级→语音输入选中的识别模型；仅用户明确要求时才传")
    run.add_argument("--language", help="语言代码，如 zh/en；缺省自动检测")
    run.add_argument("--initial-prompt")
    run.add_argument("--speaker", help="单说话人名称")
    run.add_argument("--speaker-turns", help="多人显式时间边界 JSON")
    run.add_argument("--diarize", action="store_true",
                     help="用本地 MFCC/音高聚类区分多人，不下载额外模型")
    run.add_argument("--num-speakers", type=int, help="已知说话人数（轻量模式建议 2）")
    run.add_argument("--speaker-order", action="append", default=[], metavar="NAME",
                     help="按不同声纹第一次出现顺序映射人物，可重复")
    run.add_argument("--pitch-map", action="append", default=[], metavar="LOW_OR_HIGH=NAME",
                     help="两人低音/高音映射，可重复；兼容 male/female、男声/女声别名")
    run.add_argument("--min-diarization-confidence", type=float,
                     default=DEFAULT_DIARIZATION_CONFIDENCE)
    run.add_argument("--format", choices=("timeline", "digital-human"), default="timeline")
    run.add_argument("--device", default="auto")
    run.add_argument("--compute-type", default="default")
    args = parser.parse_args(argv)
    if args.command == "doctor":
        result = doctor(args.model, args.download, args.device, args.compute_type)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1
    order = []
    for value in args.speaker_order:
        order.extend(name.strip() for name in value.split(",") if name.strip())
    pitch_map = _parse_pitch_map(args.pitch_map)
    result = transcribe(
        args.audio, args.output, args.json_output, args.model, args.language,
        args.initial_prompt, args.speaker, args.speaker_turns,
        args.format == "digital-human", args.device, args.compute_type,
        args.diarize, args.num_speakers, order, pitch_map,
        args.min_diarization_confidence,
    )
    print(json.dumps({"transcript": result["transcript"],
                      "json_output": result["json_output"],
                      "segments": len(result["segments"]),
                      "language": result["asr"]["language"],
                      "speaker_attribution": result["speaker_attribution"],
                      "ready_for_digital_human": result["ready_for_digital_human"]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
