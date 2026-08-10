# Z-Image Turbo ComfyUI Text-to-Image Workflow

[English](#english) | [中文](#中文)

## English

[`image-z-image-turbo-bf16-api.json`](image-z-image-turbo-bf16-api.json) is the
API-format ComfyUI text-to-image workflow for
[Tongyi-MAI/Z-Image-Turbo](https://huggingface.co/Tongyi-MAI/Z-Image-Turbo),
a distilled turbo model that produces an image in 8 steps at `cfg 1.0`
(`res_multistep` sampler, `simple` scheduler, AuraFlow sampling with
`shift 3.0`).

Placeholders: `PROMPT` `WIDTH` `HEIGHT` `SEED`.

For image-to-image with a reference image, use the companion workflow
[`image-z-image-turbo-bf16-img2img-api.md`](image-z-image-turbo-bf16-img2img-api.md).

### Install

Update ComfyUI to a version supporting Z-Image, then download the
ComfyUI-format weights referenced by the official ComfyUI template
(<https://huggingface.co/Comfy-Org/z_image_turbo>,
<https://docs.comfy.org/tutorials/image/z-image>):

```text
ComfyUI/models/
├── diffusion_models/z_image_turbo_bf16.safetensors
├── text_encoders/qwen_3_4b.safetensors
└── vae/ae.safetensors
```

### Console Configuration

The image workflow JSON is empty by default. Only when the image channel is
switched to **ComfyUI (local)** on the web console's `🎨 生成模型` page should
this workflow path be filled in:

```text
comfy/image-z-image-turbo-bf16-api.json
```

The `checkpoint` field stays empty: this workflow loads its models by fixed
file names via `UNETLoader`/`CLIPLoader`/`VAELoader` and has no `CHECKPOINT`
placeholder.

### Runtime Parameters

`genmedia.py` fills `PROMPT`, `WIDTH`, `HEIGHT`, and `SEED` from the generation
request at execution time. Sampling settings (8 steps, `cfg 1.0`) are fixed in
the JSON; fork the file to change them.

The workflow has no `NEGATIVE` placeholder — the negative conditioning is a
`ConditioningZeroOut` of the positive, as required at `cfg 1.0`. A negative
prompt passed with the default `negative_mode: "conditioning"` is silently
dropped. To make exclusions effective, set the image channel's
`negative_mode` to `append_exclusions`, which appends
`Exclude from the image: …` to the positive prompt instead.

---

## 中文

[`image-z-image-turbo-bf16-api.json`](image-z-image-turbo-bf16-api.json) 是
[Tongyi-MAI/Z-Image-Turbo](https://huggingface.co/Tongyi-MAI/Z-Image-Turbo)
的 API 格式 ComfyUI 文生图工作流。Z-Image Turbo 是蒸馏加速模型,8 步、`cfg 1.0`
即可出图(`res_multistep` 采样器、`simple` 调度器、AuraFlow 采样 `shift 3.0`)。

占位符:`PROMPT` `WIDTH` `HEIGHT` `SEED`。

带参考图的图生图请使用配套工作流
[`image-z-image-turbo-bf16-img2img-api.md`](image-z-image-turbo-bf16-img2img-api.md)。

### 安装

先将 ComfyUI 更新到支持 Z-Image 的版本,再下载官方 ComfyUI 模板引用的
ComfyUI 格式权重(<https://huggingface.co/Comfy-Org/z_image_turbo>、
<https://docs.comfy.org/tutorials/image/z-image>):

```text
ComfyUI/models/
├── diffusion_models/z_image_turbo_bf16.safetensors
├── text_encoders/qwen_3_4b.safetensors
└── vae/ae.safetensors
```

### 控制台配置

图片工作流 JSON 默认留空。只有在 Web 控制台「🎨 生成模型」页将图片渠道切换为
**ComfyUI(本地)** 时,才按需填写本工作流路径:

```text
comfy/image-z-image-turbo-bf16-api.json
```

`checkpoint` 字段保持留空:本工作流通过 `UNETLoader`/`CLIPLoader`/`VAELoader`
按固定文件名加载模型,没有 `CHECKPOINT` 占位符。

### 运行时参数

`genmedia.py` 在执行时按生成请求注入 `PROMPT`、`WIDTH`、`HEIGHT`、`SEED`。
采样设置(8 步、`cfg 1.0`)固定在 JSON 中,如需调整请 fork 一份修改。

本工作流没有 `NEGATIVE` 占位符——负向条件是对正向条件做 `ConditioningZeroOut`,
这是 `cfg 1.0` 的要求。默认 `negative_mode: "conditioning"` 下传入的负向提示词会被
静默丢弃。若需要排除项生效,请将图片渠道的 `negative_mode` 设为
`append_exclusions`,它会改为在正向提示词后追加 `Exclude from the image: …`。
