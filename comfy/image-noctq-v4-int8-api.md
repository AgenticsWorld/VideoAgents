# Noct Q V4 (Qwen Image 2.1) ComfyUI Text-to-Image Workflow

[English](#english) | [中文](#中文)

## English

[`image-noctq-v4-int8-api.json`](image-noctq-v4-int8-api.json) is the API-format text-to-image workflow for
[Noct Q V4](https://huggingface.co/Noctaluna/Noct-Q-Uncensored-Qwen-Image-2.1), a realism merge of
[Qwen/Qwen-Image-2.1](https://huggingface.co/Qwen/Qwen-Image-2.1). It is converted from the author's
`NoctQ_V4_workflow.json` and keeps its settings: `TextEncodeQwenImage21` (positive + negative),
`KSampler` 25 steps, `cfg 3.0`, `euler` / `simple`. Core nodes only.

Placeholders: `PROMPT` `NEGATIVE` `WIDTH` `HEIGHT` `GEN_WIDTH` `GEN_HEIGHT` `SEED`.

Reference-image companion: [`image-noctq-v4-int8-ref10-api.md`](image-noctq-v4-int8-ref10-api.md).

> **License**: Noct Q is a derivative of Qwen-Image-2.1 under the Qwen Research License —
> **non-commercial use only**. Confirm the terms before any commercial use.
>
> **Content**: the model card is tagged *not-for-all-audiences*; the merge renders nudity and explicit
> scenes without a LoRA. What you generate with it is your responsibility.

### Install

**ComfyUI 0.37.0 or newer.** `TextEncodeQwenImage21` is a core node added with Qwen Image 2.1 support; on
older versions the submission is rejected with `missing_node_type`.

Weights (<https://huggingface.co/Noctaluna/Noct-Q-Uncensored-Qwen-Image-2.1>,
<https://huggingface.co/Comfy-Org/Qwen-Image-2.1>):

```text
ComfyUI/models/
├── diffusion_models/NoctQ_V4_int8_convrot.safetensors
├── text_encoders/qwen3vl_8b_int8_convrot.safetensors
└── vae/qwen_image_2.1_vae_bf16.safetensors
```

These are the three files the author's workflow loads; the int8 text encoder is about half the size of the
bf16 one and is the pairing recommended for 8–12 GB cards. If you already have `qwen3vl_8b_bf16.safetensors`
(the file used by the official Qwen Image 2.1 template), fork the JSON and change `clip_name` on node `2`
instead of downloading the int8 file.

**Comfy Cloud** (checked 2026-10-10, Cloud v0.39.2): the node is available, but **none of the three files**
is in the shared model catalog. Import them into your Cloud account first (Cloud's model import accepts
Hugging Face / Civitai URLs); until then the submission fails validation on nodes `1`, `2` and `3`. The
catalog does have `qwen3vl_8b_bf16.safetensors`, so forking node `2` to it leaves only the diffusion model
and the VAE to import.

### Console Configuration

Run mode **local** or **Comfy Cloud**:

```json
{
  "image": {
    "provider": "comfyui",
    "comfyui": {
      "workflow": "comfy/image-noctq-v4-int8-api.json",
      "ref_workflow": "comfy/image-noctq-v4-int8-ref10-api.json",
      "negative_mode": "conditioning",
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
upscale step, fork the JSON and put `WIDTH` / `HEIGHT` on node `5` (time grows with the pixel count).

`NEGATIVE` goes to the node's `negative_prompt` and takes effect at `cfg 3.0`, so keep `negative_mode` at
`conditioning`. The author's recommended negative prompt is **not** baked in, because it works against
non-photographic styles (sketches, illustration); pass it per request when you want photographic output:

```text
artifacts, gpt-image, washed-out colors, low quality, low resolution, AI slop, deviantart, sloppy lines, rough sketch, blurry, indistinct, missing fingers, badly drawn hands, wrong number of fingers
```

For roughly twice the speed, fork the JSON and set `cfg` to `1.0`; the negative prompt is then ignored
(switch `negative_mode` to `append_exclusions` so exclusions are appended to the positive prompt).

Prompting: write full sentences like a caption of the finished image — the kind of shot, the subject, the
action, the place, the light, the framing. Qwen Image 2.1 reads prose, not tag lists; there is no trigger word.

---

## 中文

[`image-noctq-v4-int8-api.json`](image-noctq-v4-int8-api.json) 是
[Noct Q V4](https://huggingface.co/Noctaluna/Noct-Q-Uncensored-Qwen-Image-2.1) 的 API 格式文生图工作流。
Noct Q 是 [Qwen/Qwen-Image-2.1](https://huggingface.co/Qwen/Qwen-Image-2.1) 的写实向融合模型。本模板由作者的
`NoctQ_V4_workflow.json` 转换而来,采样设置原样保留:`TextEncodeQwenImage21`(同时出正面 / 负面条件)、
`KSampler` 25 步、`cfg 3.0`、`euler` / `simple`。全部 ComfyUI 原生节点。

占位符:`PROMPT` `NEGATIVE` `WIDTH` `HEIGHT` `GEN_WIDTH` `GEN_HEIGHT` `SEED`。

参考图配套工作流:[`image-noctq-v4-int8-ref10-api.md`](image-noctq-v4-int8-ref10-api.md)。

> **许可**:Noct Q 是 Qwen-Image-2.1 的衍生模型,适用 Qwen Research License——**仅限非商业用途**,
> 商用前请先确认条款。
>
> **内容**:模型卡标注 *not-for-all-audiences*,不挂 LoRA 即可生成裸体与成人场景。用它生成什么由使用者自行负责。

### 安装

**ComfyUI 0.37.0 及以上。** `TextEncodeQwenImage21` 是随 Qwen Image 2.1 支持加入的原生节点,旧版本提交时会被
`missing_node_type` 拒收。

权重(<https://huggingface.co/Noctaluna/Noct-Q-Uncensored-Qwen-Image-2.1>、
<https://huggingface.co/Comfy-Org/Qwen-Image-2.1>):

```text
ComfyUI/models/
├── diffusion_models/NoctQ_V4_int8_convrot.safetensors
├── text_encoders/qwen3vl_8b_int8_convrot.safetensors
└── vae/qwen_image_2.1_vae_bf16.safetensors
```

这三个文件就是作者工作流加载的那三个;int8 版文本编码器体积约为 bf16 版的一半,是作者给 8–12 GB 显卡推荐的
搭配。手头已有 `qwen3vl_8b_bf16.safetensors`(Qwen Image 2.1 官方模板用的那个)时,可以不下载 int8 版,fork
本 JSON 把节点 `2` 的 `clip_name` 改掉即可。

**Comfy Cloud**(2026-10-10 核对,云端 v0.39.2):节点有,但这**三个文件公共模型目录里都没有**。须先把它们导入
自己的云端账号(Cloud 的模型导入接受 Hugging Face / Civitai 链接);导入前提交会在节点 `1`、`2`、`3` 上校验失败。
目录里有 `qwen3vl_8b_bf16.safetensors`,把节点 `2` fork 成它,就只剩底模和 VAE 需要导入。

### 控制台配置

运行方式 **本地** 或 **Comfy Cloud** 均可:

```json
{
  "image": {
    "provider": "comfyui",
    "comfyui": {
      "workflow": "comfy/image-noctq-v4-int8-api.json",
      "ref_workflow": "comfy/image-noctq-v4-int8-ref10-api.json",
      "negative_mode": "conditioning",
      "checkpoint": ""
    }
  }
}
```

### 运行时参数

`GEN_WIDTH` / `GEN_HEIGHT` 由 `genmedia.py` 按本次宽高比压到 1.6 MP 以内(16 的倍数)填入——以这个尺寸从
`EmptyLatentImage` 生成,末端 `ImageScale`(lanczos)再放大到请求的 `WIDTH`x`HEIGHT`(平台出图规格 2560x1440);
请求尺寸不超过 1.6 MP 时 1:1 生成。模型原生支持到单边 2048 px;想不经放大直接出大图,fork 本 JSON,把节点 `5`
改填 `WIDTH` / `HEIGHT`(耗时随像素数增长)。

`NEGATIVE` 写进节点的 `negative_prompt`,在 `cfg 3.0` 下生效,所以 `negative_mode` 保持 `conditioning`。作者推荐的
负面提示词**没有**写死在模板里,因为它会压制非照片风格(草图、插画);要照片质感时按次传入:

```text
artifacts, gpt-image, washed-out colors, low quality, low resolution, AI slop, deviantart, sloppy lines, rough sketch, blurry, indistinct, missing fingers, badly drawn hands, wrong number of fingers
```

要快一倍左右:fork 本 JSON 把 `cfg` 改为 `1.0`,此时负面提示词不再生效(把 `negative_mode` 换成
`append_exclusions`,排除项会并入正面提示词)。

提示词写法:用完整句子,像给成片写图注——镜头类型、主体、动作、地点、光线、构图。Qwen Image 2.1 读的是
散文,不是标签串;没有触发词。
