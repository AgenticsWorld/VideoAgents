# MiniMax H3 FL2VA 360° Orbit Workflow

[English](#english) | [中文](#中文)

## English

[`video-minimax-h3-fl2va-360orbit-api.json`](video-minimax-h3-fl2va-360orbit-api.json)
is an API-format ComfyUI workflow that turns one image into a frozen-time 360°
camera orbit with
[pablodawson/MiniMax-H3-360-Orbit-LoRA](https://huggingface.co/pablodawson/MiniMax-H3-360-Orbit-LoRA).
It uses `MiniMaxH3ImageToVideo` (FL2VA) and feeds the same image to both
`first_frame` and `last_frame`. The clip therefore ends on the frame it started
from, and orbits can be chained without a seam. The output is a video-only H.264
MP4 at 24 fps. The audio stream H3 generates is not decoded.

### Install

The LoRA was trained on the FL2VA checkpoint and does not apply to Ref2VA
(`video-minimax-h3-ref2va-api.json`). Required files:

```text
ComfyUI/models/
├── diffusion_models/minimax_h3_fl2va_int8_convrot.safetensors
├── loras/minimax_h3_flf2v_lora_v1.safetensors
├── text_encoders/qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors
└── vae/minimax_h3_video_vae_fp16.safetensors
```

- The LoRA is published at
  <https://huggingface.co/pablodawson/MiniMax-H3-360-Orbit-LoRA/resolve/main/minimax_h3_flf2v_lora_v1.safetensors>.
- The LoRA was trained on `minimax_h3_fl2va_pruned_int8_convrot.safetensors`.
  The template uses the full `minimax_h3_fl2va_int8_convrot.safetensors`
  instead, because the LoRA's 208 attention/MLP layers have the same names and
  shapes in both files and sampled weights are byte-identical. The two files
  differ only in the timestep/adaLN path, which the LoRA excludes. To run on
  the exact training base, change `unet_name` in node `1` to the pruned file.
- The text encoder is pinned to the heretic variant. For the official encoder,
  change `clip_name` in node `3` to `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors`.
- The FL2VA and Ref2VA weights differ. Do not substitute a `ref2va_*` file.

### Console Configuration

Select this workflow in `🎨 生成模型 -> 视频 -> ComfyUI` only when generating
360° orbit clips:

```text
comfy/video-minimax-h3-fl2va-360orbit-api.json
```

The workflow has no `MiniMaxH3ReferenceToVideo` node, so `genmedia.py` runs it
through the generic ComfyUI video path, not the H3 Ref2VA integration.

The canvas page's 动图 tab uses this template directly in its `360° 环绕` (360°
orbit) mode with the local ComfyUI run mode. It does not change the workflow
selected on the Generation Models page.

### Runtime Parameters

| Workflow input | Source |
|---|---|
| `{{FIRST_FRAME}}` | `--first-frame`, required. The same image feeds `first_frame` and `last_frame`. |
| `{{SEED}}` | the generation request |
| `{{LTX_FRAMES}}` | `--duration` at 24 fps (8n+1). The H3 node rounds it up to its 17n+5 grid. `--duration 3` gives exactly 73 frames, the LoRA's training length. |

Fixed values: the LoRA's training caption as the prompt, used verbatim as the
model card requires; 768×768; LoRA strength 1.0; 28 steps; `res_multistep`
sampler with the `simple` scheduler; 24 fps.

These request fields are ignored:

- `--prompt`, because the caption is fixed. Edit node `6` to change it.
- `--aspect` and `--resolution`, because the size is fixed at 768×768.
- `--last-frame`, because the first frame is reused.

The generic ComfyUI path rejects `--ref`, `--audio-ref`, `--ref-video`,
`--return-last-frame`, and `--generate-audio`.

### Input Image

Use a square image. The FL2VA node stretches `first_frame` to the canvas and
center-crops `last_frame`. A non-square input therefore gives two different
end frames, and the loop does not close.

### Limits

The LoRA was trained on 28 human-centric, square, 73-frame orbit clips. Other
subjects, aspect ratios, and lengths are untested, and subtle motion such as
blinks can still appear. Durations other than 3 s change the orbit speed and
round up to the H3 frame grid; for example, 5 s gives 124 frames and 8 s gives
209.

The host-memory preflight and failure-recovery guidance in
[`video-minimax-h3-ref2va-api.md`](video-minimax-h3-ref2va-api.md) also
applies here, because the 32B text encoder is the same size.

---

## 中文

[`video-minimax-h3-fl2va-360orbit-api.json`](video-minimax-h3-fl2va-360orbit-api.json)
是 API 格式的 ComfyUI 工作流,用
[pablodawson/MiniMax-H3-360-Orbit-LoRA](https://huggingface.co/pablodawson/MiniMax-H3-360-Orbit-LoRA)
把一张图生成时间定格、镜头环绕 360° 的视频。它使用 `MiniMaxH3ImageToVideo`(FL2VA)
节点,同一张图同时接 `first_frame` 与 `last_frame`,所以片尾会回到起始画面,多段环绕
可以无缝衔接。输出是只有画面的 H.264 MP4(24 fps),H3 生成的音频流不解码。

### 安装

这个 LoRA 是在 FL2VA 权重上训练的,不能用于 Ref2VA(`video-minimax-h3-ref2va-api.json`)。
需要的文件:

```text
ComfyUI/models/
├── diffusion_models/minimax_h3_fl2va_int8_convrot.safetensors
├── loras/minimax_h3_flf2v_lora_v1.safetensors
├── text_encoders/qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors
└── vae/minimax_h3_video_vae_fp16.safetensors
```

- LoRA 下载地址:
  <https://huggingface.co/pablodawson/MiniMax-H3-360-Orbit-LoRA/resolve/main/minimax_h3_flf2v_lora_v1.safetensors>。
- LoRA 的训练底模是 `minimax_h3_fl2va_pruned_int8_convrot.safetensors`,本模板用的是完整版
  `minimax_h3_fl2va_int8_convrot.safetensors`。原因:LoRA 作用的 208 个注意力 / MLP 层在两个
  文件里名称和形状完全一致,抽样权重逐字节相同;两者只在时间步 / adaLN 路径上不同,而 LoRA
  不碰这部分。想用训练底模,把节点 `1` 的 `unet_name` 改成 pruned 文件即可。
- 文本编码器写死为 heretic 版。要用官方版,把节点 `3` 的 `clip_name` 改成
  `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors`。
- FL2VA 与 Ref2VA 的权重不同,不要换成 `ref2va_*` 文件。

### 控制台配置

只在生成 360° 环绕视频时,才在 `🎨 生成模型 -> 视频 -> ComfyUI` 中选择本工作流:

```text
comfy/video-minimax-h3-fl2va-360orbit-api.json
```

工作流里没有 `MiniMaxH3ReferenceToVideo` 节点,所以 `genmedia.py` 按通用 ComfyUI 视频
路径执行,不走 H3 Ref2VA 的专门处理。

画板「动图」标签的「360° 环绕」方式选本地模型时直接用本模板(ComfyUI 本地运行方式),
不改「生成模型」页选的工作流。

### 运行时参数

| 工作流输入 | 来源 |
|---|---|
| `{{FIRST_FRAME}}` | `--first-frame`,必填。同一张图同时接 `first_frame` 与 `last_frame` |
| `{{SEED}}` | 生成请求 |
| `{{LTX_FRAMES}}` | `--duration` 按 24 fps 换算的 8n+1 帧数,H3 节点再向上取到 17n+5 网格。`--duration 3` 正好 73 帧,即 LoRA 的训练长度 |

固定值:提示词是 LoRA 训练原句(模型卡要求原样使用);画面 768×768;LoRA 强度 1.0;
28 步;`res_multistep` 采样器配 `simple` 调度;24 fps。

以下请求参数会被忽略:

- `--prompt`:提示词已固定。要改,编辑节点 `6`。
- `--aspect` 与 `--resolution`:尺寸固定为 768×768。
- `--last-frame`:直接复用首帧。

`--ref`、`--audio-ref`、`--ref-video`、`--return-last-frame`、`--generate-audio`
会被通用 ComfyUI 路径直接拒绝。

### 输入图

请用正方形图片。FL2VA 节点会把 `first_frame` 直接拉伸到画布尺寸,`last_frame` 则居中裁切;
图片不是正方形时,首尾两帧不一样,环绕首尾接不上。

### 限制

LoRA 只用 28 段以人物为主体、正方形、73 帧的环绕视频训练。其他题材、画幅和时长都没有测过,
眨眼之类的细微动作仍可能出现。时长不是 3 秒时,环绕速度会变,帧数也会向上取到 H3 网格:
例如 5 秒是 124 帧,8 秒是 209 帧。

主机内存预检与故障恢复的说明同样适用(文本编码器同样是 32B),见
[`video-minimax-h3-ref2va-api.md`](video-minimax-h3-ref2va-api.md)。
