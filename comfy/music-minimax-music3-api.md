# MiniMax Music 3 ComfyUI Music Workflow

[English](#english) | [中文](#中文)

## English

[`music-minimax-music3-api.json`](music-minimax-music3-api.json) is the
API-format ComfyUI music workflow for
[MiniMaxAI/MiniMax-Music3](https://huggingface.co/MiniMaxAI/MiniMax-Music3)
(open weights, Apache 2.0), using ComfyUI's native nodes
(`MiniMaxMusic3TextEncode` → `EmptyMiniMaxMusic3LatentAudio` → `KSampler`
30 steps, `cfg 1.7`, `euler` / `simple` → `VAEDecodeAudio` → `SaveAudioMP3`).
No custom nodes are required.

Placeholders: `PROMPT` `LYRICS` `DURATION` `SEED`.

### Install

Update ComfyUI to a version with the MiniMax Music 3 nodes, then download the
ComfyUI-format weights (<https://huggingface.co/Comfy-Org/MiniMax-Music-3>):

```text
ComfyUI/models/
├── diffusion_models/minimax_music3_dit_fp16.safetensors                      (4.9 GB)
├── text_encoders/minimax_music3_text_encoder_pruned_int8_convrot.safetensors (9.2 GB)
└── vae/minimax_music3_dav.safetensors
```

Smaller / larger variants exist (`…_dit_int8_convrot`, `…_dit_fp32`,
`…_text_encoder_pruned_bf16`, `…_text_encoder_bf16`); fork the JSON to switch.

### Console Configuration

On the `🎨 生成模型` page choose **Music › ComfyUI**, run mode **local** or
**Comfy Cloud**, and select:

```text
comfy/music-minimax-music3-api.json
```

### Runtime Parameters

`genmedia.py music` fills `PROMPT` (the caption), `LYRICS` (`[Instrumental]`
unless the channel's `lyrics` is set), `DURATION` (`--duration`, default 30 s,
clamped to 1–240) and `SEED`.

- **`DURATION` is an upper bound, not a fixed length.** The encoder node's
  `max_duration` caps the piece and the model decides where to end; in a real
  run a 31 s request returned 22 s. Check the returned length and let the mix
  stage loop / extend when a cue needs the full time.
- The model follows structured captions well — *Global Metadata* (genre, BPM,
  key, mood, production), *Vocal Details*, *Arrangement* (section by section).
  Plain prose prompts also work.
- Output loudness is on the quiet side (about −16 LUFS in testing) and needs
  normalising in the mix.
- For pieces much longer than a minute, swap `VAEDecodeAudio` for
  `VAEDecodeAudioTiled` (`tile_size 1536`, `overlap 64`) to cut decode memory.

---

## 中文

[`music-minimax-music3-api.json`](music-minimax-music3-api.json) 是
[MiniMaxAI/MiniMax-Music3](https://huggingface.co/MiniMaxAI/MiniMax-Music3)(开放权重,Apache 2.0)
的 API 格式 ComfyUI 音乐工作流,全部用 ComfyUI 原生节点(`MiniMaxMusic3TextEncode` →
`EmptyMiniMaxMusic3LatentAudio` → `KSampler` 30 步、`cfg 1.7`、`euler` / `simple` →
`VAEDecodeAudio` → `SaveAudioMP3`),不需要自定义节点。

占位符:`PROMPT` `LYRICS` `DURATION` `SEED`。

### 安装

把 ComfyUI 升级到带 MiniMax Music 3 节点的版本,再下载 ComfyUI 格式权重
(<https://huggingface.co/Comfy-Org/MiniMax-Music-3>):

```text
ComfyUI/models/
├── diffusion_models/minimax_music3_dit_fp16.safetensors                      (4.9 GB)
├── text_encoders/minimax_music3_text_encoder_pruned_int8_convrot.safetensors (9.2 GB)
└── vae/minimax_music3_dav.safetensors
```

另有更小/更大的版本(`…_dit_int8_convrot`、`…_dit_fp32`、`…_text_encoder_pruned_bf16`、
`…_text_encoder_bf16`),要换请 fork 本 JSON。

### 控制台配置

在 `🎨 生成模型` 页选 **音乐 › ComfyUI**,运行方式**本地**或 **Comfy Cloud**,工作流选:

```text
comfy/music-minimax-music3-api.json
```

### 运行时参数

`genmedia.py music` 填入 `PROMPT`(音乐描述)、`LYRICS`(渠道 `lyrics` 未设时为 `[Instrumental]`)、
`DURATION`(`--duration`,缺省 30 秒,限 1–240)与 `SEED`。

- **`DURATION` 是上限不是定长。** 编码节点的 `max_duration` 只封顶,模型自己决定在哪收尾;实测请求 31 秒
  回了 22 秒。拿到成品要核对实际时长,cue 需要满时长时交混音环节循环/补段。
- 模型对结构化描述跟随得好——*Global Metadata*(曲风、BPM、调性、情绪、制作质感)、*Vocal Details*、
  *Arrangement*(逐段编排);普通散文提示词也能用。
- 成品响度偏低(实测约 −16 LUFS),混音时需要归一化。
- 远超一分钟的长曲,把 `VAEDecodeAudio` 换成 `VAEDecodeAudioTiled`(`tile_size 1536`、`overlap 64`)省解码显存。
