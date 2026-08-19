# LTX-2.5 Distilled T2V ComfyUI Video Workflow

[English](#english) | [中文](#中文)

## English

[`video-ltx2.5-distilled-bf16-t2v-api.json`](video-ltx2.5-distilled-bf16-t2v-api.json) is the API-format ComfyUI text-to-video workflow for [Lightricks/LTX-2.5](https://huggingface.co/Lightricks/LTX-2.5). It uses the official 22B distilled transformer with the Gemma4 LTX-2.5 text encoder and jointly generates synchronized audio and video. The default is the convolutional video VAE, which is faster and uses less memory than the diffusion decoder.

The JSON is an API template. All model names, dimensions, frame rate, sampler,
sigma schedule, decode tiling, and output prefix are supplied at runtime by
`modules/genmedia.py`; no input filename or output size is embedded in the graph.

### Install

Update ComfyUI to a build with the LTX-2.5 core nodes (`LTXVConditioning`, `EmptyLTXVLatentVideo`, `LTXVConcatAVLatent`, `LTXVEmptyLatentAudio`, `LTXVAudioVAEDecode`, etc.). The official model card recommends the built-in LTXVideo nodes; install/update [ComfyUI-LTXVideo](https://github.com/Lightricks/ComfyUI-LTXVideo) through ComfyUI Manager when the nodes are not present.

Place the files downloaded from the [LTX-2.5 Hugging Face repository](https://huggingface.co/Lightricks/LTX-2.5) here:

```text
ComfyUI/models/
├── diffusion_models/ltx-2.5-22b-distilled-transformer-bf16.safetensors
├── text_encoders/gemma4-12b-with-proj-ltx-2.5-bf16.safetensors
├── vae/ltx-2.5-audio-vae-bf16.safetensors
└── vae/ltx-2.5-video-vae-conv-bf16.safetensors
```

For maximum decode quality, replace the video VAE with `ltx-2.5-video-vae-bf16.safetensors` in node `3`. The diffusion decoder needs substantially more VRAM and time; the convolutional decoder is the practical default for local VideoAgents hosts.

### Console Configuration

Select this workflow under `🎨 生成模型 -> 视频 -> ComfyUI`:

```text
comfy/video-ltx2.5-distilled-bf16-t2v-api.json
```

### Runtime Parameters

`genmedia.py` fills the prompt/seed, maps the requested aspect and resolution to a
32-pixel grid, and converts `--duration` to `LTX_FRAMES` at the configured fps.
LTX-2.5 requires the frame count to be `8n+1`, so the actual duration can differ by
one or more frames.

The runtime token set includes:

- `LTX_UNET`, `LTX_TEXT_ENCODER`, `LTX_VIDEO_VAE`, `LTX_AUDIO_VAE`;
- `WIDTH`, `HEIGHT`, `FPS`, `LTX_FRAMES`, `SEED`;
- `LTX_CFG`, `LTX_SAMPLER`, `LTX_SIGMAS`;
- `LTX_TILE_SIZE`, `LTX_TILE_OVERLAP`, `LTX_TEMPORAL_SIZE`, `LTX_TEMPORAL_OVERLAP`.

### Image and Audio Inputs

`--ref` and `--first-frame`/`--last-frame` are mutually exclusive. `--first-frame` is uploaded at task time and attached to
`LTXVImgToVideoInplace`. `--last-frame` and each `--ref` image are uploaded and
chained through the Comfy core `LTXVAddGuide` node (`last` uses frame index `-1`,
references use frame index `0`). When no image is supplied, no extra loader or
conditioning node is added. The workflow always samples and muxes LTX-2.5's native audio;
`--generate-audio off` is rejected because this graph has no silent-audio branch.
The current core LTX nodes do not expose an external-audio-reference encoder, so
`--audio-ref` is rejected instead of being silently ignored.

---

## 中文

[`video-ltx2.5-distilled-bf16-t2v-api.json`](video-ltx2.5-distilled-bf16-t2v-api.json) 是 [Lightricks/LTX-2.5](https://huggingface.co/Lightricks/LTX-2.5) 的 API 格式 ComfyUI 文生视频工作流。它使用官方 22B 蒸馏 transformer、LTX-2.5 专用 Gemma4 文本编码器，并联合生成音视频 latent，输出带原生音画同步音频。默认使用卷积视频 VAE，相比扩散视频解码器速度更快、显存占用更低。

该 JSON 是 API 模板。模型文件名、分辨率、帧率、采样器、sigma 调度、解码
分块参数以及输出前缀都由 `modules/genmedia.py` 在运行时注入，工作流中不写死
输入文件名或输出尺寸。

### 安装

请将 ComfyUI 更新到包含 LTX-2.5 核心节点（`LTXVConditioning`、`EmptyLTXVLatentVideo`、`LTXVConcatAVLatent`、`LTXVEmptyLatentAudio`、`LTXVAudioVAEDecode` 等）的版本。缺少节点时，按官方模型卡建议通过 ComfyUI Manager 安装或更新 [ComfyUI-LTXVideo](https://github.com/Lightricks/ComfyUI-LTXVideo)。

从 [LTX-2.5 Hugging Face 仓库](https://huggingface.co/Lightricks/LTX-2.5) 下载并放置：

```text
ComfyUI/models/
├── diffusion_models/ltx-2.5-22b-distilled-transformer-bf16.safetensors
├── text_encoders/gemma4-12b-with-proj-ltx-2.5-bf16.safetensors
├── vae/ltx-2.5-audio-vae-bf16.safetensors
└── vae/ltx-2.5-video-vae-conv-bf16.safetensors
```

如需最高解码质量，可把节点 `3` 的视频 VAE 改为 `ltx-2.5-video-vae-bf16.safetensors`。扩散解码器需要更多显存和时间；当前默认卷积解码器更适合本地 VideoAgents 主机。

### 控制台配置

在 `🎨 生成模型 -> 视频 -> ComfyUI` 中选择：

```text
comfy/video-ltx2.5-distilled-bf16-t2v-api.json
```

### 运行时参数

`genmedia.py` 会注入提示词和 seed，根据请求的比例、分辨率映射到 32 像素网格，
并按运行时 fps 将 `--duration` 换算为 `LTX_FRAMES`。LTX-2.5 要求帧数为 `8n+1`，
实际时长可能按帧数略微四舍五入。

运行时占位符包括：

- `LTX_UNET`、`LTX_TEXT_ENCODER`、`LTX_VIDEO_VAE`、`LTX_AUDIO_VAE`；
- `WIDTH`、`HEIGHT`、`FPS`、`LTX_FRAMES`、`SEED`；
- `LTX_CFG`、`LTX_SAMPLER`、`LTX_SIGMAS`；
- `LTX_TILE_SIZE`、`LTX_TILE_OVERLAP`、`LTX_TEMPORAL_SIZE`、`LTX_TEMPORAL_OVERLAP`。

### 图像与音频输入

`--ref` 与 `--first-frame`/`--last-frame` 互斥。`--first-frame` 会在任务提交时上传，并接入 `LTXVImgToVideoInplace`；`--last-frame`
以及每张 `--ref` 会上传后通过 Comfy 核心的 `LTXVAddGuide` 链式接入（尾帧
使用 `-1`，参考图使用第 `0` 帧）。未提供图像时不会添加额外 loader 或条件节点。
工作流始终采样并封装 LTX-2.5 原生音频，因此 `--generate-audio off` 会被拒绝；
当前核心 LTX 节点没有外部音频参考编码器，`--audio-ref` 也会明确报错而不会被静默忽略。
