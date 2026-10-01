# Local models

Runtime-downloaded model weights live here and are not committed to Git.

- `faster-whisper/`: created automatically by `modules/transcription.py` on the first ASR job.
  Every speech-recognition path (chat voice input, footage library, plugin flows, caption / mashup
  speech alignment) uses the model selected in Settings → Advanced → Voice input.

- `audio-separation/`: `UVR-MDX-NET-Inst_HQ_3.onnx` (about 64 MB), downloaded by `modules/audio_separation.py`
  the first time a "去人声 / 去环境声" adjustment is rendered on the Post-production page (sha256-checked). Set
  `VIDEOAGENTS_SEPARATION_MODEL_URL` to use a mirror, or place the file here by hand.

Set `VIDEOAGENTS_DATA_DIR` to move the whole data directory; the effective ASR cache is then
`$VIDEOAGENTS_DATA_DIR/models/faster-whisper/`.
