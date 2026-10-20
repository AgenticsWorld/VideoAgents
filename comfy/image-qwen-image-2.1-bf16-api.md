# Qwen Image 2.1 bf16 ComfyUI Text-to-Image Workflow

[English](#english) | [中文](#中文)

## English

[`image-qwen-image-2.1-bf16-api.json`](image-qwen-image-2.1-bf16-api.json) is the API-format text-to-image
workflow for [Qwen/Qwen-Image-2.1](https://huggingface.co/Qwen/Qwen-Image-2.1) using the bf16 safetensors
weights from [Comfy-Org/Qwen-Image-2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1). It is the same graph as
[`image-qwen-image-2.1-q8-gguf-api.json`](image-qwen-image-2.1-q8-gguf-api.md) with node `1` replaced by the core
`UNETLoader`, so it needs **no custom node**: `TextEncodeQwenImage21`, `KSampler` 25 steps, `cfg 1.0`,
`euler` / `simple`.

Placeholders: `PROMPT` `WIDTH` `HEIGHT` `GEN_WIDTH` `GEN_HEIGHT` `SEED`.

Tested 2026-10-20 on a local ComfyUI 0.42.1 with an RTX 4080 SUPER (32 GB): a 1024x1024 image took about
105 s, including the first model load.

> **Comfy Cloud**: as of 2026-10-20 (Cloud v0.42.1) the node graph is supported and `qwen3vl_8b_bf16` is in
> Cloud's shared model catalog, but `qwen_image_2.1_bf16.safetensors` and `qwen_image_2.1_vae_bf16.safetensors`
> are not, so a Cloud submission fails validation unless your Cloud account has both files.
>
> **License**: Qwen Research License — **non-commercial use only**. Confirm the terms before any commercial
> use.

### Install

1. **ComfyUI 0.37.0 or newer** — `TextEncodeQwenImage21` is a core node added with Qwen Image 2.1 support.
2. Weights from <https://huggingface.co/Comfy-Org/Qwen-Image-2.1> (`split_files/`):

```text
ComfyUI/models/
├── diffusion_models/qwen_image_2.1_bf16.safetensors   (14.2 GB)
├── text_encoders/qwen3vl_8b_bf16.safetensors          (17.5 GB)
└── vae/qwen_image_2.1_vae_bf16.safetensors            (0.7 GB)
```

On less VRAM, the same repository has smaller files that fit the graph with a one-line fork of the JSON —
`qwen_image_2.1_int8_convrot.safetensors` (7.3 GB) on node `1`, `qwen3vl_8b_int8_convrot.safetensors` (9.4 GB)
on node `2`. These were not tested with this template.

### Console Configuration

Run mode **local**:

```json
{
  "image": {
    "provider": "comfyui",
    "comfyui": {
      "workflow": "comfy/image-qwen-image-2.1-bf16-api.json",
      "ref_workflow": "comfy/image-qwen-image-2.1-bf16-ref10-api.json",
      "negative_mode": "append_exclusions",
      "checkpoint": ""
    }
  }
}
```

Reference-image companion (1 to 10 references, same model files):
[`image-qwen-image-2.1-bf16-ref10-api.md`](image-qwen-image-2.1-bf16-ref10-api.md) — read its test note first;
on a 32 GB card it was far slower than text-to-image.

### Runtime Parameters

`GEN_WIDTH` / `GEN_HEIGHT` are filled by `genmedia.py` with the requested aspect ratio scaled down to at
most 1.6 MP (multiples of 16) — the image is generated at that size from an `EmptyLatentImage` and the final
`ImageScale` (lanczos) brings it to the requested `WIDTH`x`HEIGHT` (2560x1440 platform spec). Requests at or
below 1.6 MP are generated 1:1. The model is native up to 2048 px per side; to generate larger without the
upscale step, fork the JSON and put `WIDTH` / `HEIGHT` on node `5` (time grows with the pixel count).

There is no `NEGATIVE` placeholder: at `cfg 1.0` the negative conditioning is not evaluated, so the node's
`negative_prompt` is left empty. Set the image channel's `negative_mode` to `append_exclusions` so exclusions
are appended to the positive prompt as `Exclude from the image: …` instead of being dropped. To make a real
negative prompt effective at roughly twice the time, fork the JSON: raise `cfg` on node `6` (about 3) and put
`{{NEGATIVE}}` into `negative_prompt` on node `4`, then use `negative_mode: "conditioning"`.

Prompting: Qwen Image 2.1 reads prose — write full sentences like a caption of the finished image.

---

## 中文

[`image-qwen-image-2.1-bf16-api.json`](image-qwen-image-2.1-bf16-api.json) 是
[Qwen/Qwen-Image-2.1](https://huggingface.co/Qwen/Qwen-Image-2.1) bf16 safetensors 版(权重来自
[Comfy-Org/Qwen-Image-2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1))的 API 格式文生图工作流。与
[`image-qwen-image-2.1-q8-gguf-api.json`](image-qwen-image-2.1-q8-gguf-api.md) 是同一张图,只把节点 `1` 换成原生
`UNETLoader`,因此**不需要任何自定义节点**:`TextEncodeQwenImage21`、`KSampler` 25 步、`cfg 1.0`、
`euler` / `simple`。

占位符:`PROMPT` `WIDTH` `HEIGHT` `GEN_WIDTH` `GEN_HEIGHT` `SEED`。

2026-10-20 实测:本地 ComfyUI 0.42.1、RTX 4080 SUPER(32 GB),出一张 1024x1024 约 105 秒(含首次加载模型)。

> **Comfy Cloud**:截至 2026-10-20(云端 v0.42.1),节点都已支持,`qwen3vl_8b_bf16` 也在云端公共模型目录里,
> 但 `qwen_image_2.1_bf16.safetensors` 与 `qwen_image_2.1_vae_bf16.safetensors` 不在,除非云端账号里已有这两个文件,
> 否则提交会在校验阶段被拒。
>
> **许可**:Qwen Research License——**仅限非商业用途**,商用前请先确认条款。

### 安装

1. **ComfyUI 0.37.0 及以上**——`TextEncodeQwenImage21` 是随 Qwen Image 2.1 支持加入的原生节点。
2. 权重来自 <https://huggingface.co/Comfy-Org/Qwen-Image-2.1>(`split_files/` 下):

```text
ComfyUI/models/
├── diffusion_models/qwen_image_2.1_bf16.safetensors   (14.2 GB)
├── text_encoders/qwen3vl_8b_bf16.safetensors          (17.5 GB)
└── vae/qwen_image_2.1_vae_bf16.safetensors            (0.7 GB)
```

显存不够时,同一仓库有更小的文件,fork 本 JSON 改一行即可:节点 `1` 换 `qwen_image_2.1_int8_convrot.safetensors`
(7.3 GB),节点 `2` 换 `qwen3vl_8b_int8_convrot.safetensors`(9.4 GB)。这两种组合没有用本模板实测过。

### 控制台配置

运行方式选 **本地**:

```json
{
  "image": {
    "provider": "comfyui",
    "comfyui": {
      "workflow": "comfy/image-qwen-image-2.1-bf16-api.json",
      "ref_workflow": "comfy/image-qwen-image-2.1-bf16-ref10-api.json",
      "negative_mode": "append_exclusions",
      "checkpoint": ""
    }
  }
}
```

参考图配套工作流(1–10 张参考图,模型文件相同):
[`image-qwen-image-2.1-bf16-ref10-api.md`](image-qwen-image-2.1-bf16-ref10-api.md)——先看其中的实测说明,32 GB 显卡上
它比文生图慢得多。

### 运行时参数

`GEN_WIDTH` / `GEN_HEIGHT` 由 `genmedia.py` 按本次宽高比压到 1.6 MP 以内(16 的倍数)填入——以这个尺寸从
`EmptyLatentImage` 生成,末端 `ImageScale`(lanczos)再放大到请求的 `WIDTH`x`HEIGHT`(平台出图规格 2560x1440);
请求尺寸不超过 1.6 MP 时 1:1 生成。模型原生支持到单边 2048 px;想不经放大直接出大图,fork 本 JSON,把节点 `5`
改填 `WIDTH` / `HEIGHT`(耗时随像素数增长)。

没有 `NEGATIVE` 占位符:`cfg 1.0` 下负面条件不参与计算,节点的 `negative_prompt` 留空。把图片渠道的
`negative_mode` 设为 `append_exclusions`,排除项才会以 `Exclude from the image: …` 并入正面提示词,否则被丢弃。
要让真正的负面提示词生效(耗时约翻倍):fork 本 JSON,把节点 `6` 的 `cfg` 调高(约 3),节点 `4` 的
`negative_prompt` 填 `{{NEGATIVE}}`,再把 `negative_mode` 设为 `conditioning`。

提示词写法:Qwen Image 2.1 读的是散文——用完整句子,像给成片写图注。
