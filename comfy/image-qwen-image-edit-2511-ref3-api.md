# Qwen-Image-Edit 2511 ComfyUI Multi-Reference Workflow

[English](#english) | [中文](#中文)

## English

[`image-qwen-image-edit-2511-ref3-api.json`](image-qwen-image-edit-2511-ref3-api.json) is an image-to-image
workflow for [Qwen-Image-Edit-2511](https://huggingface.co/Qwen/Qwen-Image-Edit-2511) (20B, Apache 2.0) with
**1 to 3 reference images** as *conditions* (identity, outfit, layout) and an empty latent — composition follows
the prompt. It runs the Lightning 4-step LoRA: `KSampler` 4 steps, `cfg 1.0`, `euler` / `simple`,
`ModelSamplingAuraFlow shift 3`, `CFGNorm`.

Placeholders: `PROMPT` `FIRST_FRAME` `WIDTH` `HEIGHT` `GEN_WIDTH` `GEN_HEIGHT` `SEED`.

It has no text-to-image counterpart of its own: pair it with any text-to-image template
(e.g. [`image-flux2-dev-fp8-api.md`](image-flux2-dev-fp8-api.md), [`image-ideogram4-int8-api.md`](image-ideogram4-int8-api.md),
[`image-z-image-turbo-bf16-api.md`](image-z-image-turbo-bf16-api.md)) and put this file in `ref_workflow`.

### Install

Native ComfyUI nodes. Weights (<https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI>,
<https://huggingface.co/lightx2v/Qwen-Image-Edit-2511-Lightning>):

```text
ComfyUI/models/
├── diffusion_models/qwen_image_edit_2511_bf16.safetensors
├── loras/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors
├── text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors
└── vae/qwen_image_vae.safetensors
```

### Console Configuration

```json
{
  "image": {
    "provider": "comfyui",
    "comfyui": {
      "workflow": "comfy/image-flux2-dev-fp8-api.json",
      "ref_workflow": "comfy/image-qwen-image-edit-2511-ref3-api.json",
      "negative_mode": "append_exclusions",
      "checkpoint": ""
    }
  }
}
```

### Runtime Parameters

All three `LoadImage` nodes carry `FIRST_FRAME` only so the template parses; `genmedia.py` binds the N-th `--ref`
to the N-th `LoadImage` and removes unused `image2` / `image3` slots on both encode nodes. More than 3 references
are rejected before submission. Refer to the references by order in the prompt.

`GEN_WIDTH` / `GEN_HEIGHT` are filled by `genmedia.py` with the requested aspect ratio scaled down to at most
1.6 MP (multiples of 16) — the model generates at that native size and the final `ImageScale` (lanczos)
brings the image to the requested `WIDTH`x`HEIGHT` (2560x1440 platform spec). Requests at or below
1.6 MP are generated 1:1.

There is no `NEGATIVE` placeholder: the negative side is a `ConditioningZeroOut` (`cfg 1.0` / unconditional
model). Set the image channel's `negative_mode` to `append_exclusions` so exclusions are appended to the
positive prompt as `Exclude from the image: …` instead of being dropped.

For maximum fidelity at roughly ten times the cost, fork the JSON: drop the Lightning LoRA and use about 40 steps
with `cfg` around 4 (the negative encode node then takes effect).

---

## 中文

[`image-qwen-image-edit-2511-ref3-api.json`](image-qwen-image-edit-2511-ref3-api.json) 是
[Qwen-Image-Edit-2511](https://huggingface.co/Qwen/Qwen-Image-Edit-2511)(20B,Apache 2.0)的图生图工作流:
**1–3 张参考图**作*条件*(身份、服装、版式),空 latent 生成——构图听提示词的。挂 Lightning 4 步 LoRA:
`KSampler` 4 步、`cfg 1.0`、`euler` / `simple`、`ModelSamplingAuraFlow shift 3`、`CFGNorm`。

占位符:`PROMPT` `FIRST_FRAME` `WIDTH` `HEIGHT` `GEN_WIDTH` `GEN_HEIGHT` `SEED`。

它没有自己的文生图配套:与任意文生图模板搭配(如 [`image-flux2-dev-fp8-api.md`](image-flux2-dev-fp8-api.md)、
[`image-ideogram4-int8-api.md`](image-ideogram4-int8-api.md)、[`image-z-image-turbo-bf16-api.md`](image-z-image-turbo-bf16-api.md)),
本文件填 `ref_workflow`。

### 安装

全部 ComfyUI 原生节点。权重(<https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI>、
<https://huggingface.co/lightx2v/Qwen-Image-Edit-2511-Lightning>):

```text
ComfyUI/models/
├── diffusion_models/qwen_image_edit_2511_bf16.safetensors
├── loras/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors
├── text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors
└── vae/qwen_image_vae.safetensors
```

### 控制台配置

```json
{
  "image": {
    "provider": "comfyui",
    "comfyui": {
      "workflow": "comfy/image-flux2-dev-fp8-api.json",
      "ref_workflow": "comfy/image-qwen-image-edit-2511-ref3-api.json",
      "negative_mode": "append_exclusions",
      "checkpoint": ""
    }
  }
}
```

### 运行时参数

三个 `LoadImage` 都写 `FIRST_FRAME` 只是为了让模板可解析;`genmedia.py` 把第 N 张 `--ref` 绑到第 N 个 `LoadImage`,
两个编码节点上没用到的 `image2` / `image3` 槽位自动摘掉;超过 3 张在提交前报错。提示词里按顺序指代参考图。

`GEN_WIDTH` / `GEN_HEIGHT` 由 `genmedia.py` 按本次宽高比压到 1.6 MP 以内(16 的倍数)填入——模型按这个原生尺寸生成,
末端 `ImageScale`(lanczos)再放大到请求的 `WIDTH`x`HEIGHT`(平台出图规格 2560x1440);请求尺寸不超过 1.6 MP 时 1:1 生成。

没有 `NEGATIVE` 占位符:负面侧是 `ConditioningZeroOut`(`cfg 1.0` / 无条件模型)。把图像渠道的 `negative_mode` 设为
`append_exclusions`,排除项才会以 `Exclude from the image: …` 并入正面提示词,否则被静默丢弃。

要最高保真(成本约高一个数量级):fork 本 JSON,去掉 Lightning LoRA,改约 40 步、`cfg` 约 4(此时负面编码节点才生效)。
