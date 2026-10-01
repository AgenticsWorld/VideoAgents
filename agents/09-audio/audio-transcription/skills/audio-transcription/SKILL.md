---
name: audio-transcription
description: Transcribe local audio or video speech with faster-whisper into reusable UTF-8 text and JSON timelines, including lightweight multi-speaker acoustic clustering and deterministic cast mapping. Use when a workflow has audio but no transcript, when subtitles or dialogue timing are needed, or when digital-human input needs timestamped speaker labels.
---

# Audio Transcription

Use the host command below. Do not install packages inside a project and do not copy model files into
project assets. The host downloads a missing faster-whisper checkpoint once into
`data/models/faster-whisper/` and reuses it.

The recognition model is the one the user selected in Settings → Advanced → Voice input (the host
reads it automatically; the same model serves the chat voice input and the footage library). Do not
pass `--model` unless the work order says the user explicitly asked for a different model, and never
switch to a larger model on your own to retry.

## 1. Check the backend

```bash
python3 modules/transcription.py doctor
```

Do not use `doctor --download` before every job. A normal `transcribe` call loads an existing model or
downloads it automatically when absent. Use `doctor --download` only for an explicit preflight request.

## 2. Produce a general timeline

```bash
python3 modules/transcription.py transcribe \
  --audio "$VIDEOAGENTS_PROJECT_ROOT/refs/audio/source.mp3" \
  --output "$VIDEOAGENTS_PROJECT_ROOT/transcription/source.txt" \
  --json-output "$VIDEOAGENTS_PROJECT_ROOT/transcription/source.json" \
  --language zh
```

Omit `--language` to auto-detect. Add `--initial-prompt` with confirmed names or technical terms to improve
recognition; it is a vocabulary hint, not permission to invent absent words. The TXT format is one
`[HH:MM:SS.mmm-HH:MM:SS.mmm] text` row per ASR segment. JSON preserves model metadata, audio SHA-256,
detected language, segments, and word timestamps for downstream components.

## 3. Produce a digital-human transcript

For one mapped person, provide the exact cast name:

```bash
python3 modules/transcription.py transcribe \
  --audio "$VIDEOAGENTS_PROJECT_ROOT/assets/audio/master/ep01.mp3" \
  --output "$VIDEOAGENTS_PROJECT_ROOT/digital-human/transcript_generated.txt" \
  --json-output "$VIDEOAGENTS_PROJECT_ROOT/digital-human/transcription.json" \
  --format digital-human --speaker "主持人" --language zh
```

This format expands silence gaps into a continuous timeline covering exactly `0..ffprobe duration`, so each
row is directly consumable by `modules/dialogue_video.py plan`:

```text
[00:00:00.000-00:00:04.200] 主持人：欢迎来到今天的节目。
```

For multiple people, select the first applicable mapping mode. Never alternate names line by line.

1. If exact speaker turns exist, use them as the highest-priority source of truth:

```json
{"turns": [
  {"start": 0.0, "end": 4.2, "speaker": "主持人"},
  {"start": 4.2, "end": 8.6, "speaker": "嘉宾"}
]}
```

Pass that file with `--speaker-turns <path> --format digital-human`.

2. If the user states identities by first distinct voice appearance, run local acoustic clustering:

```bash
python3 modules/transcription.py transcribe \
  --audio "$VIDEOAGENTS_PROJECT_ROOT/assets/audio/master/ep01.mp3" \
  --output "$VIDEOAGENTS_PROJECT_ROOT/digital-human/transcript_generated.txt" \
  --json-output "$VIDEOAGENTS_PROJECT_ROOT/digital-human/transcription.json" \
  --format digital-human --diarize --num-speakers 2 \
  --speaker-order "主持人" --speaker-order "嘉宾" --language zh
```

`speaker-order` means the first appearance of each distinct acoustic cluster. It does not mean turns alternate.

3. If the user explicitly maps low/high voice (including male/female wording as a production heuristic), use:

```bash
  --format digital-human --diarize --num-speakers 2 \
  --pitch-map "low=主持人" --pitch-map "high=嘉宾"
```

Do not infer gender from portrait images. Translate explicit “男声/女声” wording to low/high pitch mapping;
record `mapping_mode: pitch` and do not claim biological gender recognition. With no identity mapping, diarization
may output `说话人1/说话人2`, but `ready_for_digital_human` remains false.

The lightweight backend uses the already-installed PyAV, NumPy, and SciPy stack (MFCC, pitch, deterministic
k-means) and downloads no diarization model. If confidence is below the configured threshold, preserve the TXT/JSON
for review but do not invoke `dialogue_video.py plan` or any paid avatar provider.

## 4. Completion checks

- TXT is UTF-8, non-empty, chronological, and has complete start/end timestamps.
- JSON `audio.sha256` matches the input and `segments` match the TXT.
- Digital-human single-person output uses the cast's exact name.
- Digital-human multi-person names match cast names and use explicit turns, first-appearance mapping, or an explicit
  low/high mapping; never portrait inference or mechanical alternation.
- JSON `ready_for_digital_human` is true before dispatching paid generation; otherwise report the confidence blocker.
- Keep the audio untouched. Transcription reads it; it never rewrites, normalizes, trims, or resamples it.
