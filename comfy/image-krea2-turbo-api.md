# Krea 2 Turbo ComfyUI Text-to-Image Workflow

[English](#english) | [中文](#中文)

## English

[`image-krea2-turbo-api.json`](image-krea2-turbo-api.json) is the API-format text-to-image workflow for
[Krea 2 Turbo](https://huggingface.co/Comfy-Org/Krea-2) (12B, Krea 2 Community License), native ComfyUI nodes
only: `KSampler` 10 steps, `cfg 1.0`, `euler` / `simple`.

Placeholders: `PROMPT` `WIDTH` `HEIGHT` `GEN_WIDTH` `GEN_HEIGHT` `SEED`.

Reference-image companion: [`image-krea2-turbo-styleref-api.md`](image-krea2-turbo-styleref-api.md).

### Install

Weights from <https://huggingface.co/Comfy-Org/Krea-2>:

```text
ComfyUI/models/
├── diffusion_models/krea2_turbo_fp8_scaled.safetensors   (13.1 GB)
├── text_encoders/qwen3vl_4b_fp8_scaled.safetensors       (5.2 GB)
└── vae/qwen_image_vae.safetensors
```

Other precisions (`…_int8_convrot`, `…_bf16`, `…_nvfp4`) work too; fork the JSON to switch.

### Console Configuration

`🎨 生成模型` › **Image › ComfyUI**, run mode **local** or **Comfy Cloud**:

```json
{
  "image": {
    "provider": "comfyui",
    "comfyui": {
      "workflow": "comfy/image-krea2-turbo-api.json",
      "ref_workflow": "comfy/image-krea2-turbo-styleref-api.json",
      "negative_mode": "append_exclusions",
      "checkpoint": ""
    }
  }
}
```

### Runtime Parameters

`GEN_WIDTH` / `GEN_HEIGHT` are filled by `genmedia.py` with the requested aspect ratio scaled down to at most
1.6 MP (multiples of 16) — the model generates at that native size and the final `ImageScale` (lanczos)
brings the image to the requested `WIDTH`x`HEIGHT` (2560x1440 platform spec). Requests at or below
1.6 MP are generated 1:1.

There is no `NEGATIVE` placeholder: the negative side is a `ConditioningZeroOut` (`cfg 1.0` / unconditional
model). Set the image channel's `negative_mode` to `append_exclusions` so exclusions are appended to the
positive prompt as `Exclude from the image: …` instead of being dropped.

Style LoRAs from the same repository (`loras/krea2_*.safetensors`) or community LoRAs can be added with a
`LoraLoaderModelOnly` after `UNETLoader`; none is included so the template runs with the base files only.

---

## 中文

[`image-krea2-turbo-api.json`](image-krea2-turbo-api.json) 是 [Krea 2 Turbo](https://huggingface.co/Comfy-Org/Krea-2)
(12B,Krea 2 Community License)的 API 格式文生图工作流,全部 ComfyUI 原生节点:`KSampler` 10 步、`cfg 1.0`、`euler` / `simple`。

占位符:`PROMPT` `WIDTH` `HEIGHT` `GEN_WIDTH` `GEN_HEIGHT` `SEED`。

参考图配套工作流:[`image-krea2-turbo-styleref-api.md`](image-krea2-turbo-styleref-api.md)。

### 安装

权重见 <https://huggingface.co/Comfy-Org/Krea-2>:

```text
ComfyUI/models/
├── diffusion_models/krea2_turbo_fp8_scaled.safetensors   (13.1 GB)
├── text_encoders/qwen3vl_4b_fp8_scaled.safetensors       (5.2 GB)
└── vae/qwen_image_vae.safetensors
```

其它精度(`…_int8_convrot`、`…_bf16`、`…_nvfp4`)同样可用,要换请 fork 本 JSON。

### 控制台配置

`🎨 生成模型` › **图像 › ComfyUI**,运行方式**本地**或 **Comfy Cloud**:

```json
{
  "image": {
    "provider": "comfyui",
    "comfyui": {
      "workflow": "comfy/image-krea2-turbo-api.json",
      "ref_workflow": "comfy/image-krea2-turbo-styleref-api.json",
      "negative_mode": "append_exclusions",
      "checkpoint": ""
    }
  }
}
```

### 运行时参数

`GEN_WIDTH` / `GEN_HEIGHT` 由 `genmedia.py` 按本次宽高比压到 1.6 MP 以内(16 的倍数)填入——模型按这个原生尺寸生成,
末端 `ImageScale`(lanczos)再放大到请求的 `WIDTH`x`HEIGHT`(平台出图规格 2560x1440);请求尺寸不超过 1.6 MP 时 1:1 生成。

没有 `NEGATIVE` 占位符:负面侧是 `ConditioningZeroOut`(`cfg 1.0` / 无条件模型)。把图像渠道的 `negative_mode` 设为
`append_exclusions`,排除项才会以 `Exclude from the image: …` 并入正面提示词,否则被静默丢弃。

同仓库的风格 LoRA(`loras/krea2_*.safetensors`)或社区 LoRA 可在 `UNETLoader` 后加 `LoraLoaderModelOnly` 挂载;
模板本身不带 LoRA,只靠基础文件即可运行。
