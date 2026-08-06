# MiniMax H3 ComfyUI Video Workflow

[`minimax-h3-ref2va-api.json`](minimax-h3-ref2va-api.json) is the API-format
ComfyUI workflow for [MiniMaxAI/MiniMax-H3](https://huggingface.co/MiniMaxAI/MiniMax-H3).
It uses `MiniMaxH3ReferenceToVideo` (Ref2VA), so it preserves the VideoAgents
group-video contract: up to 9 reference images, up to 3 voice-reference clips,
native synchronized audio, 24 fps, and a locally extracted `--return-last-frame`.

## Install

Update ComfyUI to a version exposing `MiniMaxH3ReferenceToVideo`, then download
the ComfyUI-format H3 weights referenced by the official ComfyUI template:

```text
ComfyUI/models/
├── diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors
├── text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors
└── vae/
    ├── minimax_h3_video_vae_fp16.safetensors
    └── minimax_h3_audio_vae_fp32.safetensors
```

The Ref2VA workflow requires a `ref2va` diffusion weight. A
`fl2va_*` file, including `minimax_h3_fl2va_pruned_int8_convrot.safetensors`,
is for first/last-frame generation and cannot be substituted here. VideoAgents
uses the compatible Ref2VA component names and sampling settings built into its
H3 integration.

The upstream MiniMax repository provides the original Diffusers weights. The
ComfyUI conversion and official node/template are documented at:

- <https://huggingface.co/MiniMaxAI/MiniMax-H3>
- <https://docs.comfy.org/tutorials/video/minimax/minimax-h3>
- <https://huggingface.co/Comfy-Org/MiniMax-H3>

## Console Configuration

The video workflow JSON is empty by default. Only when using MiniMax H3, select
this workflow in `🎨 生成模型 -> 视频 -> ComfyUI`:

```text
comfy/minimax-h3-ref2va-api.json
```

H3 model names and sampling settings are intentionally not exposed in the
console. VideoAgents uses its compatible defaults for this workflow.

## Runtime Parameters

The workflow has no fixed production settings. `genmedia.py` supplies them at
execution time:

| Workflow token | Source |
|---|---|
| `WIDTH`, `HEIGHT` | requested `--aspect` and `--resolution`, constrained by project Output Settings and aligned to H3's 32-pixel latent grid |
| `H3_FRAMES`, `FPS` | requested `--duration` and the built-in H3 frame rate |
| `SEED`, `PROMPT` | the generation request |
| `H3_*` model/sampler tokens | built-in H3 defaults |
| reference sockets | every supplied `--ref` / `--audio-ref`, uploaded to ComfyUI in order |

H3 emits native audio-video. `--generate-audio on` is supported; `off` is
rejected rather than silently stripping the native audio. `--return-last-frame`
extracts the final decoded frame from the finished MP4 for the next generation
group's continuity anchor. The local Base workflow accepts the configured
360p/480p/720p/1080p draft dimensions. `360p` is a low-memory smoke-test tier
(16:9 maps to 640x352 on H3's 32-pixel grid); use it only for backend
validation, then return to the project's accepted output tier. For a `4k` final setting, generate at the
project draft tier and use the existing upscale stage: H3's 2K regeneration
module is not open-sourced.

This workflow is Ref2VA. Do not use `--first-frame`, `--last-frame`, or
`--ref-video` with it; choose the corresponding H3 FL2VA/video-reference
workflow when those modes are required.

## Host Preflight And Failure Recovery

H3's int8 text encoder is still a 32B model. "int8" reduces weight storage;
it does not make model loading cheap. Before selecting H3 as the production
video workflow, confirm that `GET /system_stats` is healthy and that both host
RAM and GPU VRAM have sufficient headroom with no other ComfyUI job running.
Run a five-second, one-reference smoke test first, then use the configured
production steps only after that test creates an MP4.

Do not repeatedly submit when either of the following occurs:

- `HostBuffer.read_file_slice failed` at `SamplerCustomAdvanced`;
- the ComfyUI port becomes unreachable after the prompt enters `running`.

Both indicate a ComfyUI model-loading/offload failure, not a long video
render. Do not keep retrying the same prompt. First verify the SHA256 and
actual ComfyUI model path of `minimax_h3_ref2va_pruned_int8_convrot.safetensors`, then
update both ComfyUI and `comfy-aimdo` together. A restart with
`--disable-pinned-memory --disable-async-offload` is the appropriate first
diagnostic on a 64GB host: it avoids ComfyUI's pinned-memory and async-prefetch
paths. It can be slower and does not guarantee a fix when the same host-buffer
error reproduces, so increase available system memory or lower other host
workloads when the process is terminated during model load.

`genmedia.py` now reconciles `/queue` with `/history`: a prompt that disappears
from both is reported as a cancelled/restarted ComfyUI task instead of waiting
for the generic 30-minute timeout. Keep H3 out of the global production config
until the smoke test remains online and finishes successfully.
