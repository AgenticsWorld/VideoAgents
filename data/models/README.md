# Local models

Runtime-downloaded model weights live here and are not committed to Git.

- `faster-whisper/`: created automatically by `modules/transcription.py` on the first ASR job.

Set `VIDEOAGENTS_DATA_DIR` to move the whole data directory; the effective ASR cache is then
`$VIDEOAGENTS_DATA_DIR/models/faster-whisper/`.
