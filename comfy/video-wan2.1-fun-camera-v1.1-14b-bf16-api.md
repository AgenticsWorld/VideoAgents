# Wan2.1 Fun Camera v1.1 14B ComfyUI Video Workflow

[English](#english) | [中文](#中文)

## English

[`video-wan2.1-fun-camera-v1.1-14b-bf16-api.json`](video-wan2.1-fun-camera-v1.1-14b-bf16-api.json)
is the API-format ComfyUI image-to-video workflow for
[alibaba-pai/Wan2.1-Fun-V1.1-14B-Control-Camera](https://huggingface.co/alibaba-pai/Wan2.1-Fun-V1.1-14B-Control-Camera).
It animates a start image with a controlled camera move via
`WanCameraEmbedding` + `WanCameraImageToVideo`. Output is silent 832x480 MP4
(H.264) at 16 fps; 20 steps, `cfg 6.0`, `uni_pc` sampler, `shift 8.0`.

Placeholders: `PROMPT` `FIRST_FRAME` `FRAMES` `SEED`.

### Install

Download the ComfyUI-format weights referenced by the official ComfyUI Fun
Camera template (<https://docs.comfy.org/tutorials/video/wan/fun-camera>;
shared Wan components from
<https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged>):

```text
ComfyUI/models/
├── diffusion_models/wan2.1_fun_camera_v1.1_14B_bf16.safetensors
├── text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors
├── clip_vision/clip_vision_h.safetensors
└── vae/wan_2.1_vae.safetensors
```

The 14B bf16 diffusion weight is ~32GB; plan VRAM/offload headroom
accordingly, or use a quantized variant with a forked workflow.

### Console Configuration

The video workflow JSON is empty by default. Only when using this model,
select the workflow in `🎨 生成模型 -> 视频 -> ComfyUI`:

```text
comfy/video-wan2.1-fun-camera-v1.1-14b-bf16-api.json
```

### Runtime Parameters

`genmedia.py` fills `PROMPT` and `SEED` from the generation request, converts
`--duration` to `FRAMES` at 16 fps rounded to Wan's required `4n+1` frame
count, and uploads `--first-frame` as `FIRST_FRAME`. **`--first-frame` is
required** — without it the `FIRST_FRAME` placeholder stays unfilled and the
workflow fails at `LoadImage`.

Fixed inside the JSON (fork the file to change them):

- camera move: `Pan Right` at `speed 0.35` — fork per-shot copies for other
  poses (`Pan Left`, `Zoom In`, `Tilt Up`, …);
- resolution `832x480` — `--aspect` / `--resolution` are ignored;
- a general-purpose negative prompt;
- sampling: 20 steps, `cfg 6.0`, `uni_pc` / `simple`.

No `--last-frame`, `--ref`, audio inputs, or `--generate-audio on`; the model
emits silent video.

---

## 中文

[`video-wan2.1-fun-camera-v1.1-14b-bf16-api.json`](video-wan2.1-fun-camera-v1.1-14b-bf16-api.json)
是 [alibaba-pai/Wan2.1-Fun-V1.1-14B-Control-Camera](https://huggingface.co/alibaba-pai/Wan2.1-Fun-V1.1-14B-Control-Camera)
的 API 格式 ComfyUI 图生视频工作流。它通过 `WanCameraEmbedding` +
`WanCameraImageToVideo` 以受控运镜让起始图动起来。输出为无声 832x480 MP4
(H.264)、16 fps;20 步、`cfg 6.0`、`uni_pc` 采样器、`shift 8.0`。

占位符:`PROMPT` `FIRST_FRAME` `FRAMES` `SEED`。

### 安装

下载官方 ComfyUI Fun Camera 模板引用的 ComfyUI 格式权重
(<https://docs.comfy.org/tutorials/video/wan/fun-camera>;Wan 公共组件见
<https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged>):

```text
ComfyUI/models/
├── diffusion_models/wan2.1_fun_camera_v1.1_14B_bf16.safetensors
├── text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors
├── clip_vision/clip_vision_h.safetensors
└── vae/wan_2.1_vae.safetensors
```

14B bf16 扩散权重约 32GB,请预留相应显存/offload 余量,或 fork 工作流改用量化版权重。

### 控制台配置

视频工作流 JSON 默认留空。只有在使用本模型时,才在
`🎨 生成模型 -> 视频 -> ComfyUI` 中选择本工作流:

```text
comfy/video-wan2.1-fun-camera-v1.1-14b-bf16-api.json
```

### 运行时参数

`genmedia.py` 在执行时按生成请求注入 `PROMPT` 与 `SEED`,把 `--duration` 按 16 fps
换算为 Wan 要求的 `4n+1` 帧数注入 `FRAMES`,并把 `--first-frame` 上传后注入
`FIRST_FRAME`。**`--first-frame` 是必填项**——缺少它时 `FIRST_FRAME` 占位符不会被
填充,工作流会在 `LoadImage` 处失败。

以下内容固定在 JSON 中(如需调整请 fork 一份修改):

- 运镜:`Pan Right`、`speed 0.35`——需要其他运镜(`Pan Left`、`Zoom In`、
  `Tilt Up` 等)时按镜头 fork 副本;
- 分辨率 `832x480`——`--aspect` / `--resolution` 不生效;
- 通用负向提示词;
- 采样:20 步、`cfg 6.0`、`uni_pc` / `simple`。

不支持 `--last-frame`、`--ref`、音频输入或 `--generate-audio on`;模型输出无声视频。
