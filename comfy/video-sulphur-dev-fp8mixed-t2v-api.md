# LTX "Sulphur" Dev T2V ComfyUI Video Workflow

[English](#english) | [中文](#中文)

## English

[`video-sulphur-dev-fp8mixed-t2v-api.json`](video-sulphur-dev-fp8mixed-t2v-api.json)
is an API-format ComfyUI text-to-video workflow built on the Lightricks LTX
"sulphur" dev fp8-mixed audio-video checkpoint, accelerated with the
`ltx-2.3-22b-distilled-lora-1.1_fro90_ceil72_condsafe` LoRA at strength 0.5.
Video and audio latents are generated jointly (`LTXVConcatAVLatent` →
`LTXVSeparateAVLatent`), so the output MP4 (H.264) carries **native
synchronized audio** at 24 fps. Sampling is a 6-sigma distilled schedule
(`ManualSigmas` + `lcm` sampler, `cfg 1.0`), decoded with `VAEDecodeTiled` to
bound VRAM.

Placeholders: `PROMPT` `NEGATIVE` `LTX_FRAMES` `SEED`.

### Install

The workflow needs a ComfyUI build providing the LTX audio-video nodes
(`LTXAVTextEncoderLoader`, `LTXVAudioVAELoader`, `LTXVConcatAVLatent`, …) —
update ComfyUI and the Lightricks LTX-Video custom nodes
(<https://github.com/Lightricks/ComfyUI-LTXVideo>) to current versions, then
place the weights:

```text
ComfyUI/models/
├── checkpoints/sulphur_dev_fp8mixed.safetensors
├── loras/ltx-2.3-22b-distilled-lora-1.1_fro90_ceil72_condsafe.safetensors
└── text_encoders/gemma_3_12B_it_fp4_mixed.safetensors
```

The checkpoint bundles the video VAE and audio VAE; the text encoder and audio
VAE loaders reference it by the same `sulphur_dev_fp8mixed.safetensors` name,
so keep the filename unchanged.

### Console Configuration

The video workflow JSON is empty by default. Only when using this model,
select the workflow in `🎨 生成模型 -> 视频 -> ComfyUI`:

```text
comfy/video-sulphur-dev-fp8mixed-t2v-api.json
```

### Runtime Parameters

`genmedia.py` fills `PROMPT`, `NEGATIVE`, and `SEED` from the generation
request, and converts `--duration` to `LTX_FRAMES` at 24 fps rounded to LTX's
required `8n+1` frame count. The same frame count drives both the video latent
and the empty audio latent, keeping audio and video aligned.

Fixed inside the JSON (fork the file to change them):

- resolution `768x512` — `--aspect` / `--resolution` are ignored;
- frame rate 24 fps, LoRA strength 0.5, distilled sigma schedule
  `0.85, 0.7933, 0.68, 0.51, 0.2833, 0.0`;
- tiled VAE decode (`tile_size 768`, `temporal_size 64`).

This is a text-to-video workflow with no reference-image or first/last-frame
inputs; do not use `--ref`, `--first-frame`, or `--last-frame` with it. Audio
is native: `--generate-audio on` is the expected mode.

---

## 中文

[`video-sulphur-dev-fp8mixed-t2v-api.json`](video-sulphur-dev-fp8mixed-t2v-api.json)
是基于 Lightricks LTX "sulphur" dev fp8-mixed 音视频 checkpoint 的 API 格式
ComfyUI 文生视频工作流,叠加 `ltx-2.3-22b-distilled-lora-1.1_fro90_ceil72_condsafe`
蒸馏 LoRA(强度 0.5)加速。视频与音频 latent 联合生成(`LTXVConcatAVLatent` →
`LTXVSeparateAVLatent`),输出 MP4(H.264)自带 **原生音画同步音频**,24 fps。
采样为 6 段 sigma 蒸馏调度(`ManualSigmas` + `lcm` 采样器、`cfg 1.0`),
用 `VAEDecodeTiled` 分块解码以控制显存。

占位符:`PROMPT` `NEGATIVE` `LTX_FRAMES` `SEED`。

### 安装

本工作流需要提供 LTX 音视频节点(`LTXAVTextEncoderLoader`、`LTXVAudioVAELoader`、
`LTXVConcatAVLatent` 等)的 ComfyUI 环境——请把 ComfyUI 与 Lightricks LTX-Video
自定义节点(<https://github.com/Lightricks/ComfyUI-LTXVideo>)更新到当前版本,
然后放置权重:

```text
ComfyUI/models/
├── checkpoints/sulphur_dev_fp8mixed.safetensors
├── loras/ltx-2.3-22b-distilled-lora-1.1_fro90_ceil72_condsafe.safetensors
└── text_encoders/gemma_3_12B_it_fp4_mixed.safetensors
```

checkpoint 内置视频 VAE 与音频 VAE;文本编码器与音频 VAE 加载器都按同一个
`sulphur_dev_fp8mixed.safetensors` 文件名引用它,请勿改名。

### 控制台配置

视频工作流 JSON 默认留空。只有在使用本模型时,才在
`🎨 生成模型 -> 视频 -> ComfyUI` 中选择本工作流:

```text
comfy/video-sulphur-dev-fp8mixed-t2v-api.json
```

### 运行时参数

`genmedia.py` 在执行时按生成请求注入 `PROMPT`、`NEGATIVE`、`SEED`,并把
`--duration` 按 24 fps 换算为 LTX 要求的 `8n+1` 帧数注入 `LTX_FRAMES`。视频 latent
与空音频 latent 使用同一帧数,保证音画对齐。

以下内容固定在 JSON 中(如需调整请 fork 一份修改):

- 分辨率 `768x512`——`--aspect` / `--resolution` 不生效;
- 帧率 24 fps、LoRA 强度 0.5、蒸馏 sigma 调度
  `0.85, 0.7933, 0.68, 0.51, 0.2833, 0.0`;
- 分块 VAE 解码(`tile_size 768`、`temporal_size 64`)。

这是纯文生视频工作流,没有参考图或首尾帧输入;不要对它使用 `--ref`、
`--first-frame` 或 `--last-frame`。音频为原生输出:`--generate-audio on`
是预期用法。
