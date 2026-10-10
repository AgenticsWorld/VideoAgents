# Qwen Image 2.1 GGUF (Q8_0) ComfyUI Text-to-Image Workflow

[English](#english) | [中文](#中文)

## English

[`image-qwen-image-2.1-q8-gguf-api.json`](image-qwen-image-2.1-q8-gguf-api.json) is the API-format
text-to-image workflow for [Qwen/Qwen-Image-2.1](https://huggingface.co/Qwen/Qwen-Image-2.1) in the Q8_0 GGUF
quantization (7.6 GB) from
[abenzerps/Qwen-Image-2.1-Uncensored-GGUF](https://huggingface.co/abenzerps/Qwen-Image-2.1-Uncensored-GGUF).
It is converted from `image_qwen_image_2_1_t2i_gguf.json` — the official Comfy-Org Qwen Image 2.1 template
with the diffusion loader swapped for `UnetLoaderGGUF` — and keeps its settings: `TextEncodeQwenImage21`,
`KSampler` 25 steps, `cfg 1.0`, `euler` / `simple`. The source loads the `Q6_K` file; this template names `Q8_0`.

Placeholders: `PROMPT` `WIDTH` `HEIGHT` `GEN_WIDTH` `GEN_HEIGHT` `SEED`.

Reference-image companion:
[`image-qwen-image-2.1-q8-gguf-ref10-api.md`](image-qwen-image-2.1-q8-gguf-ref10-api.md).

> **Local only.** `UnetLoaderGGUF` comes from the ComfyUI-GGUF custom node pack. Comfy Cloud's node catalog
> (checked 2026-10-10, Cloud v0.39.2) does not include it, so on Cloud the submission is rejected with
> `missing_node_type`. See [Without the GGUF node](#without-the-gguf-node) for a variant that needs no
> custom node.
>
> **License**: Qwen Research License — **non-commercial use only**. Confirm the terms before any commercial
> use.

### Install

1. **ComfyUI 0.37.0 or newer** — `TextEncodeQwenImage21` is a core node added with Qwen Image 2.1 support.
2. **ComfyUI-GGUF** with Qwen Image 2.1 support. The model repository recommends the
   [leejet/ComfyUI-GGUF](https://github.com/leejet/ComfyUI-GGUF) fork; the original `city96/ComfyUI-GGUF`
   does not list the `qwen_image21` architecture and cannot load the file.
3. Weights. The plain `qwen-image-2.1-*.gguf` quantizations are on the repository's
   [`base` branch](https://huggingface.co/abenzerps/Qwen-Image-2.1-Uncensored-GGUF/tree/base); the text
   encoder and VAE are from <https://huggingface.co/Comfy-Org/Qwen-Image-2.1>:

```text
ComfyUI/models/
├── diffusion_models/qwen-image-2.1-Q8_0.gguf
├── text_encoders/qwen3vl_8b_bf16.safetensors
└── vae/qwen_image_2.1_vae_bf16.safetensors
```

Other files work with a one-line fork of the JSON: a smaller quantization (`Q6_K`, `Q5_K_M`, `Q4_K_M`,
`Q4_0`) on node `1`, or `qwen3vl_8b_int8_convrot.safetensors` (about half the size) on node `2`. The same
repository's `main` branch holds `qwen-image-2.1-UC-*.gguf`, an uncensored variant that renders adult
content; it loads with the same graph by changing `unet_name`, and what you generate with it is your
responsibility.

### Console Configuration

Run mode **local**:

```json
{
  "image": {
    "provider": "comfyui",
    "comfyui": {
      "workflow": "comfy/image-qwen-image-2.1-q8-gguf-api.json",
      "ref_workflow": "comfy/image-qwen-image-2.1-q8-gguf-ref10-api.json",
      "negative_mode": "append_exclusions",
      "checkpoint": ""
    }
  }
}
```

### Runtime Parameters

`GEN_WIDTH` / `GEN_HEIGHT` are filled by `genmedia.py` with the requested aspect ratio scaled down to at
most 1.6 MP (multiples of 16) — the image is generated at that size from an `EmptyLatentImage` and the final
`ImageScale` (lanczos) brings it to the requested `WIDTH`x`HEIGHT` (2560x1440 platform spec). Requests at or
below 1.6 MP are generated 1:1. The model is native up to 2048 px per side; to generate larger without the
upscale step, fork the JSON and put `WIDTH` / `HEIGHT` on node `5` (time grows with the pixel count). The
source workflow's `ResolutionSelector` is gone: the size comes from the request.

There is no `NEGATIVE` placeholder: at `cfg 1.0` the negative conditioning is not evaluated, so the node's
`negative_prompt` is left empty. Set the image channel's `negative_mode` to `append_exclusions` so exclusions
are appended to the positive prompt as `Exclude from the image: …` instead of being dropped. To make a real
negative prompt effective at roughly twice the time, fork the JSON: raise `cfg` on node `6` (about 3) and put
`{{NEGATIVE}}` into `negative_prompt` on node `4`, then use `negative_mode: "conditioning"`.

Prompting: Qwen Image 2.1 reads prose — write full sentences like a caption of the finished image.

### Without the GGUF node

If you have a safetensors build of the same model (for example Comfy-Org's `qwen_image_2.1_bf16.safetensors`
in `models/diffusion_models`), fork the JSON and replace node `1` with the core loader; nothing else changes
and no custom node is needed:

```json
"1": {"class_type": "UNETLoader", "inputs": {"unet_name": "qwen_image_2.1_bf16.safetensors", "weight_dtype": "default"}}
```

That fork is also the form Comfy Cloud can run once the Qwen Image 2.1 diffusion model and VAE exist in your
Cloud account — neither is in Cloud's shared model catalog as of 2026-10-10.

---

## 中文

[`image-qwen-image-2.1-q8-gguf-api.json`](image-qwen-image-2.1-q8-gguf-api.json) 是
[Qwen/Qwen-Image-2.1](https://huggingface.co/Qwen/Qwen-Image-2.1) Q8_0 GGUF 量化版(7.6 GB,来自
[abenzerps/Qwen-Image-2.1-Uncensored-GGUF](https://huggingface.co/abenzerps/Qwen-Image-2.1-Uncensored-GGUF))的
API 格式文生图工作流。由 `image_qwen_image_2_1_t2i_gguf.json` 转换而来——那是把底模加载器换成 `UnetLoaderGGUF`
的 Comfy-Org 官方 Qwen Image 2.1 模板——采样设置原样保留:`TextEncodeQwenImage21`、`KSampler` 25 步、
`cfg 1.0`、`euler` / `simple`。原工作流加载的是 `Q6_K`,本模板改写为 `Q8_0`。

占位符:`PROMPT` `WIDTH` `HEIGHT` `GEN_WIDTH` `GEN_HEIGHT` `SEED`。

参考图配套工作流:
[`image-qwen-image-2.1-q8-gguf-ref10-api.md`](image-qwen-image-2.1-q8-gguf-ref10-api.md)。

> **只能在本地跑。** `UnetLoaderGGUF` 来自 ComfyUI-GGUF 自定义节点包。Comfy Cloud 的节点目录(2026-10-10 核对,
> 云端 v0.39.2)里没有它,所以选云端提交会被 `missing_node_type` 拒收。不需要自定义节点的变体见
> [不用 GGUF 节点](#不用-gguf-节点)。
>
> **许可**:Qwen Research License——**仅限非商业用途**,商用前请先确认条款。

### 安装

1. **ComfyUI 0.37.0 及以上**——`TextEncodeQwenImage21` 是随 Qwen Image 2.1 支持加入的原生节点。
2. 支持 Qwen Image 2.1 的 **ComfyUI-GGUF**。模型仓库推荐
   [leejet/ComfyUI-GGUF](https://github.com/leejet/ComfyUI-GGUF) 这个分支;原版 `city96/ComfyUI-GGUF` 的架构清单里
   没有 `qwen_image21`,加载不了这个文件。
3. 权重。不带 UC 的 `qwen-image-2.1-*.gguf` 量化件在该仓库的
   [`base` 分支](https://huggingface.co/abenzerps/Qwen-Image-2.1-Uncensored-GGUF/tree/base);文本编码器与 VAE 来自
   <https://huggingface.co/Comfy-Org/Qwen-Image-2.1>:

```text
ComfyUI/models/
├── diffusion_models/qwen-image-2.1-Q8_0.gguf
├── text_encoders/qwen3vl_8b_bf16.safetensors
└── vae/qwen_image_2.1_vae_bf16.safetensors
```

换别的文件只需 fork 本 JSON 改一行:节点 `1` 换更小的量化档(`Q6_K`、`Q5_K_M`、`Q4_K_M`、`Q4_0`),节点 `2`
换 `qwen3vl_8b_int8_convrot.safetensors`(体积约一半)。同一仓库 `main` 分支上的 `qwen-image-2.1-UC-*.gguf` 是
可生成成人内容的无审查变体,改 `unet_name` 即可用同一张图加载;用它生成什么由使用者自行负责。

### 控制台配置

运行方式选 **本地**:

```json
{
  "image": {
    "provider": "comfyui",
    "comfyui": {
      "workflow": "comfy/image-qwen-image-2.1-q8-gguf-api.json",
      "ref_workflow": "comfy/image-qwen-image-2.1-q8-gguf-ref10-api.json",
      "negative_mode": "append_exclusions",
      "checkpoint": ""
    }
  }
}
```

### 运行时参数

`GEN_WIDTH` / `GEN_HEIGHT` 由 `genmedia.py` 按本次宽高比压到 1.6 MP 以内(16 的倍数)填入——以这个尺寸从
`EmptyLatentImage` 生成,末端 `ImageScale`(lanczos)再放大到请求的 `WIDTH`x`HEIGHT`(平台出图规格 2560x1440);
请求尺寸不超过 1.6 MP 时 1:1 生成。模型原生支持到单边 2048 px;想不经放大直接出大图,fork 本 JSON,把节点 `5`
改填 `WIDTH` / `HEIGHT`(耗时随像素数增长)。原工作流里的 `ResolutionSelector` 已去掉,尺寸由请求决定。

没有 `NEGATIVE` 占位符:`cfg 1.0` 下负面条件不参与计算,节点的 `negative_prompt` 留空。把图片渠道的
`negative_mode` 设为 `append_exclusions`,排除项才会以 `Exclude from the image: …` 并入正面提示词,否则被丢弃。
要让真正的负面提示词生效(耗时约翻倍):fork 本 JSON,把节点 `6` 的 `cfg` 调高(约 3),节点 `4` 的
`negative_prompt` 填 `{{NEGATIVE}}`,再把 `negative_mode` 设为 `conditioning`。

提示词写法:Qwen Image 2.1 读的是散文——用完整句子,像给成片写图注。

### 不用 GGUF 节点

手头有同一模型的 safetensors 版(例如 Comfy-Org 的 `qwen_image_2.1_bf16.safetensors`,放
`models/diffusion_models`)时,fork 本 JSON,把节点 `1` 换成原生加载器即可,其余不变,也不需要自定义节点:

```json
"1": {"class_type": "UNETLoader", "inputs": {"unet_name": "qwen_image_2.1_bf16.safetensors", "weight_dtype": "default"}}
```

Comfy Cloud 能跑的也是这个写法,前提是云端账号里已有 Qwen Image 2.1 的底模与 VAE——截至 2026-10-10,
云端公共模型目录里两者都没有。
