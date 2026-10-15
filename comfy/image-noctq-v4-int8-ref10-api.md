# Noct Q V4 (Qwen Image 2.1) ComfyUI Multi-Reference Image-to-Image Workflow

[English](#english) | [中文](#中文)

## English

[`image-noctq-v4-int8-ref10-api.json`](image-noctq-v4-int8-ref10-api.json) is the reference-image companion of
[`image-noctq-v4-int8-api.md`](image-noctq-v4-int8-api.md), for
[Noct Q V4](https://huggingface.co/Noctaluna/Noct-Q-Uncensored-Qwen-Image-2.1) — image editing runs on the
same files as text-to-image. It accepts **1 to 10 reference images**, wired to `TextEncodeQwenImage21`'s
`images.image_1` … `images.image_10` with the VAE connected: each reference is seen by the Qwen3-VL text
encoder and spliced into the sequence as VAE latents. The image is generated from an empty latent at the
requested aspect ratio, so references are *conditions* (identity, outfit, layout, style) and composition
follows the prompt. Sampling is the same as text-to-image: 25 steps, `cfg 3.0`, `euler` / `simple`.

Placeholders: `PROMPT` `NEGATIVE` `FIRST_FRAME` `WIDTH` `HEIGHT` `GEN_WIDTH` `GEN_HEIGHT` `SEED`.

> **License / content**: non-commercial only (Qwen Research License); the model renders adult content.
> See [`image-noctq-v4-int8-api.md`](image-noctq-v4-int8-api.md).

### Install

Same ComfyUI version (0.37.0 or newer) and same three model files as the text-to-image workflow; see the
install section of [`image-noctq-v4-int8-api.md`](image-noctq-v4-int8-api.md), including the Comfy Cloud
note. No extra downloads are needed.

### Console Configuration

This workflow goes into the image channel's **`ref_workflow`** field, next to the main workflow (run mode
**local** or **Comfy Cloud**):

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

`genmedia.py` switches to `ref_workflow` automatically whenever `--ref` images are supplied and uploads every
reference to ComfyUI.

### How References Are Bound

All ten `LoadImage` nodes carry the `FIRST_FRAME` placeholder only so the template parses; the actual
binding is positional. `genmedia.py` binds the N-th `--ref` to the N-th `LoadImage` (node ids 101…110) and
removes every unused `images.image_N` slot together with its `LoadImage`. More than 10 `--ref` images are
rejected before anything is submitted. In the prompt, refer to the references by order; the official Qwen
Image 2.1 edit template writes them as `<image1>`, `<image2>`, ….

The node resizes each reference to about 1024x1024 px of area (multiples of 32, aspect ratio kept) before
encoding — `resolution` on node `4`. Every reference lengthens the sequence, so time and VRAM grow with the
number of references; on small cards fork the JSON and lower `resolution`.

### Runtime Parameters

`GEN_WIDTH` / `GEN_HEIGHT`, the final `ImageScale` to `WIDTH`x`HEIGHT`, and `NEGATIVE` behave as in the
text-to-image workflow.

The template samples from an `EmptyLatentImage` at the requested aspect ratio rather than from the node's own
`latent` output (an empty latent at the first reference's resized size). That is what lets the output follow
the requested size, but the node documents that any other size shifts the edit. For a faithful in-place edit
of a single image, fork the JSON and connect `KSampler.latent_image` to node `4` output `2`: the result then
has the first reference's aspect ratio (the final `ImageScale` center-crops it to `WIDTH`x`HEIGHT`), and
`genmedia.py` counts the fork as a redraw template rather than a multi-reference one.

---

## 中文

[`image-noctq-v4-int8-ref10-api.json`](image-noctq-v4-int8-ref10-api.json) 是
[`image-noctq-v4-int8-api.md`](image-noctq-v4-int8-api.md) 的参考图配套工作流,基于
[Noct Q V4](https://huggingface.co/Noctaluna/Noct-Q-Uncensored-Qwen-Image-2.1)——图像编辑与文生图用同一套文件。
接受 **1–10 张参考图**,接在 `TextEncodeQwenImage21` 的 `images.image_1` … `images.image_10` 上并连上 VAE:每张
参考图既给 Qwen3-VL 文本编码器看,也以 VAE latent 拼进序列。成图从请求宽高比的空 latent 生成,所以参考图是
*条件*(身份、服装、版式、画风),构图听提示词的。采样与文生图相同:25 步、`cfg 3.0`、`euler` / `simple`。

占位符:`PROMPT` `NEGATIVE` `FIRST_FRAME` `WIDTH` `HEIGHT` `GEN_WIDTH` `GEN_HEIGHT` `SEED`。

> **许可 / 内容**:仅限非商业用途(Qwen Research License);模型可生成成人内容。
> 详见 [`image-noctq-v4-int8-api.md`](image-noctq-v4-int8-api.md)。

### 安装

ComfyUI 版本(0.37.0 及以上)与三个模型文件都和文生图工作流相同,见
[`image-noctq-v4-int8-api.md`](image-noctq-v4-int8-api.md) 的安装章节(含 Comfy Cloud 说明),无需额外下载。

### 控制台配置

本工作流填入图片渠道的 **`ref_workflow`** 字段,与主工作流并列(运行方式 **本地** 或 **Comfy Cloud** 均可):

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

只要生成请求带 `--ref` 参考图,`genmedia.py` 就会自动切换到 `ref_workflow`,并把每张参考图上传到 ComfyUI。

### 参考图怎么绑

十个 `LoadImage` 都写 `FIRST_FRAME` 只是为了让模板可解析,实际按位置绑定:`genmedia.py` 把第 N 张 `--ref` 绑到
第 N 个 `LoadImage`(节点 101…110),没用到的 `images.image_N` 槽位连同对应的 `LoadImage` 一并摘掉;超过 10 张在
提交前报错。提示词里按顺序指代参考图;Qwen Image 2.1 官方编辑模板的写法是 `<image1>`、`<image2>`……

节点在编码前把每张参考图缩放到约 1024x1024 px 的面积(32 的倍数、保持宽高比)——即节点 `4` 的 `resolution`。
每多一张参考图序列就更长,耗时和显存随张数增长;小显存显卡可 fork 本 JSON 调低 `resolution`。

### 运行时参数

`GEN_WIDTH` / `GEN_HEIGHT`、末端 `ImageScale` 到 `WIDTH`x`HEIGHT`、`NEGATIVE` 的行为都与文生图工作流一致。

本模板从请求宽高比的 `EmptyLatentImage` 采样,没有用节点自带的 `latent` 输出(按第一张参考图缩放后尺寸给的
空 latent)。这样成图尺寸才能跟随请求,但节点说明写明:换成别的尺寸会让编辑结果产生偏移。要对单张图做贴合的
原地编辑,fork 本 JSON,把 `KSampler.latent_image` 改接节点 `4` 的第 `2` 路输出:此时成图是第一张参考图的宽高比
(末端 `ImageScale` 居中裁切到 `WIDTH`x`HEIGHT`),且 `genmedia.py` 会把这份 fork 视为原图重绘模板,不再按多参考图处理。
