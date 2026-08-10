# Wan2.1 T2V 1.3B ComfyUI Video Workflow

[English](#english) | [中文](#中文)

## English

[`video-wan2.1-t2v-1.3b-fp16-api.json`](video-wan2.1-t2v-1.3b-fp16-api.json) is
the API-format ComfyUI text-to-video workflow for
[Wan-AI/Wan2.1-T2V-1.3B](https://huggingface.co/Wan-AI/Wan2.1-T2V-1.3B).
At 1.3B parameters it is the lightweight tier of the Wan2.1 family — suitable
for low-VRAM hosts and backend smoke tests rather than final production
quality. Output is silent 832x480 MP4 (H.264) at 16 fps; 30 steps, `cfg 6.0`,
`uni_pc` sampler.

Placeholders: `PROMPT` `FRAMES` `SEED`.

### Install

Download the ComfyUI-format weights repackaged by Comfy-Org
(<https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged>,
<https://docs.comfy.org/tutorials/video/wan/wan-video>):

```text
ComfyUI/models/
├── diffusion_models/wan2.1_t2v_1.3B_fp16.safetensors
├── text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors
└── vae/wan_2.1_vae.safetensors
```

### Console Configuration

The video workflow JSON is empty by default. Only when using this model,
select the workflow in `🎨 生成模型 -> 视频 -> ComfyUI`:

```text
comfy/video-wan2.1-t2v-1.3b-fp16-api.json
```

### Runtime Parameters

`genmedia.py` fills `PROMPT` and `SEED` from the generation request, and
converts `--duration` to `FRAMES` at 16 fps rounded to Wan's required `4n+1`
frame count.

Fixed inside the JSON (fork the file to change them):

- resolution `832x480` — `--aspect` / `--resolution` are ignored;
- a general-purpose negative prompt (quality/artifact exclusions);
- sampling: 30 steps, `cfg 6.0`, `uni_pc` / `simple`.

This is a text-to-video workflow with no reference-image, first/last-frame, or
audio inputs; do not use `--ref`, `--first-frame`, `--last-frame`,
`--audio-ref`, or `--generate-audio on` with it.

---

## 中文

[`video-wan2.1-t2v-1.3b-fp16-api.json`](video-wan2.1-t2v-1.3b-fp16-api.json) 是
[Wan-AI/Wan2.1-T2V-1.3B](https://huggingface.co/Wan-AI/Wan2.1-T2V-1.3B)
的 API 格式 ComfyUI 文生视频工作流。1.3B 参数是 Wan2.1 家族的轻量档,适合低显存
主机与后端冒烟测试,不以最终成片质量为目标。输出为无声 832x480 MP4(H.264)、
16 fps;30 步、`cfg 6.0`、`uni_pc` 采样器。

占位符:`PROMPT` `FRAMES` `SEED`。

### 安装

下载 Comfy-Org 重打包的 ComfyUI 格式权重
(<https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged>、
<https://docs.comfy.org/tutorials/video/wan/wan-video>):

```text
ComfyUI/models/
├── diffusion_models/wan2.1_t2v_1.3B_fp16.safetensors
├── text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors
└── vae/wan_2.1_vae.safetensors
```

### 控制台配置

视频工作流 JSON 默认留空。只有在使用本模型时,才在
`🎨 生成模型 -> 视频 -> ComfyUI` 中选择本工作流:

```text
comfy/video-wan2.1-t2v-1.3b-fp16-api.json
```

### 运行时参数

`genmedia.py` 在执行时按生成请求注入 `PROMPT` 与 `SEED`,并把 `--duration` 按
16 fps 换算为 Wan 要求的 `4n+1` 帧数注入 `FRAMES`。

以下内容固定在 JSON 中(如需调整请 fork 一份修改):

- 分辨率 `832x480`——`--aspect` / `--resolution` 不生效;
- 通用负向提示词(画质/瑕疵排除项);
- 采样:30 步、`cfg 6.0`、`uni_pc` / `simple`。

这是纯文生视频工作流,没有参考图、首尾帧或音频输入;不要对它使用 `--ref`、
`--first-frame`、`--last-frame`、`--audio-ref` 或 `--generate-audio on`。
