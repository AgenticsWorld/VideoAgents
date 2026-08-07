# MiniMax H3 ComfyUI Video Workflow

[English](#english) | [中文](#中文)

## English

[`video-minimax-h3-ref2va-api.json`](video-minimax-h3-ref2va-api.json) is the API-format
ComfyUI workflow for [MiniMaxAI/MiniMax-H3](https://huggingface.co/MiniMaxAI/MiniMax-H3).
It uses `MiniMaxH3ReferenceToVideo` (Ref2VA), so it preserves the VideoAgents
group-video contract: up to 9 reference images, up to 3 voice-reference clips,
native synchronized audio, 24 fps, and a locally extracted `--return-last-frame`.

### Install

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

### Console Configuration

The video workflow JSON is empty by default. Only when using MiniMax H3, select
this workflow in `🎨 生成模型 -> 视频 -> ComfyUI`:

```text
comfy/video-minimax-h3-ref2va-api.json
```

H3 model names and sampling settings are intentionally not exposed in the
console. VideoAgents uses its compatible defaults for this workflow.

### Runtime Parameters

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

### Host Preflight And Failure Recovery

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

---

## 中文

[`video-minimax-h3-ref2va-api.json`](video-minimax-h3-ref2va-api.json) 是
[MiniMaxAI/MiniMax-H3](https://huggingface.co/MiniMaxAI/MiniMax-H3) 的 API 格式
ComfyUI 视频工作流。它使用 `MiniMaxH3ReferenceToVideo`(Ref2VA)节点,因此保留了
VideoAgents 组视频约定:最多 9 张参考图、最多 3 段声音参考、原生音画同步、24 fps,
以及本地提取的 `--return-last-frame`。

### 安装

先将 ComfyUI 更新到包含 `MiniMaxH3ReferenceToVideo` 节点的版本,再下载官方
ComfyUI 模板引用的 ComfyUI 格式 H3 权重:

```text
ComfyUI/models/
├── diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors
├── text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors
└── vae/
    ├── minimax_h3_video_vae_fp16.safetensors
    └── minimax_h3_audio_vae_fp32.safetensors
```

Ref2VA 工作流必须使用 `ref2va` 扩散权重。`fl2va_*` 文件(包括
`minimax_h3_fl2va_pruned_int8_convrot.safetensors`)用于首尾帧生成,不能在此替代。
VideoAgents 的 H3 集成内置了兼容的 Ref2VA 组件名与采样设置。

上游 MiniMax 仓库提供原始 Diffusers 权重;ComfyUI 转换版与官方节点/模板见:

- <https://huggingface.co/MiniMaxAI/MiniMax-H3>
- <https://docs.comfy.org/tutorials/video/minimax/minimax-h3>
- <https://huggingface.co/Comfy-Org/MiniMax-H3>

### 控制台配置

视频工作流 JSON 默认留空。只有在使用 MiniMax H3 时,才在
`🎨 生成模型 -> 视频 -> ComfyUI` 中选择本工作流:

```text
comfy/video-minimax-h3-ref2va-api.json
```

H3 模型名与采样设置有意不在控制台暴露;VideoAgents 对本工作流使用内置的兼容默认值。

### 运行时参数

工作流本身不含固定生产参数,由 `genmedia.py` 在执行时注入:

| 工作流占位符 | 来源 |
|---|---|
| `WIDTH`、`HEIGHT` | 请求的 `--aspect` 与 `--resolution`,受项目输出设置约束,并对齐 H3 的 32 像素 latent 网格 |
| `H3_FRAMES`、`FPS` | 请求的 `--duration` 与 H3 内置帧率 |
| `SEED`、`PROMPT` | 生成请求 |
| `H3_*` 模型/采样器占位符 | 内置 H3 默认值 |
| 参考输入端口 | 所有传入的 `--ref` / `--audio-ref`,按顺序上传到 ComfyUI |

H3 输出原生音画视频。支持 `--generate-audio on`;`off` 会被拒绝而不是静默剥离原生音频。
`--return-last-frame` 从生成完的 MP4 中提取最后一帧解码画面,作为下一生成组的连续性锚点。
本地 Base 工作流接受配置的 360p/480p/720p/1080p 草稿分辨率。`360p` 是低显存冒烟测试档
(16:9 在 H3 的 32 像素网格上映射为 640x352),仅用于后端验证,验证后应回到项目认可的
输出档位。若最终设置为 `4k`,请按项目草稿档生成后走已有的放大阶段:H3 的 2K 重生成模块
未开源。

本工作流是 Ref2VA。不要对它使用 `--first-frame`、`--last-frame` 或 `--ref-video`;
需要这些模式时请选用对应的 H3 FL2VA/视频参考工作流。

### 主机预检与故障恢复

H3 的 int8 文本编码器仍是 32B 模型。"int8" 只减小权重存储,不代表模型加载便宜。
在将 H3 选为生产视频工作流之前,先确认 `GET /system_stats` 健康,且主机内存与 GPU
显存都有足够余量、没有其他 ComfyUI 任务在跑。先跑一次 5 秒、单参考图的冒烟测试,
测试产出 MP4 后再使用配置的生产步数。

出现以下任一情况时不要反复提交:

- `SamplerCustomAdvanced` 处报 `HostBuffer.read_file_slice failed`;
- prompt 进入 `running` 后 ComfyUI 端口失联。

两者都说明是 ComfyUI 模型加载/offload 故障,不是视频渲染耗时长。不要对同一 prompt
反复重试。先核对 `minimax_h3_ref2va_pruned_int8_convrot.safetensors` 的 SHA256 与
ComfyUI 实际模型路径,然后同步更新 ComfyUI 与 `comfy-aimdo`。在 64GB 主机上,带
`--disable-pinned-memory --disable-async-offload` 重启是合适的首个诊断手段:它绕开
ComfyUI 的 pinned-memory 与异步预取路径。它可能更慢,且当同样的 host-buffer 错误复现时
并不保证解决;若进程在模型加载期间被终止,请增加可用系统内存或降低主机其他负载。

`genmedia.py` 现在会对账 `/queue` 与 `/history`:一个从两者中同时消失的 prompt 会被
报告为 ComfyUI 任务被取消/重启,而不是等满通用的 30 分钟超时。在冒烟测试能保持在线
并成功完成之前,不要把 H3 写进全局生产配置。
