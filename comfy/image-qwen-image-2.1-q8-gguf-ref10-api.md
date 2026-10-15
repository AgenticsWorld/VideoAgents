# Qwen Image 2.1 GGUF (Q8_0) ComfyUI Multi-Reference Image-to-Image Workflow

[English](#english) | [中文](#中文)

## English

[`image-qwen-image-2.1-q8-gguf-ref10-api.json`](image-qwen-image-2.1-q8-gguf-ref10-api.json) is the
reference-image companion of
[`image-qwen-image-2.1-q8-gguf-api.md`](image-qwen-image-2.1-q8-gguf-api.md), converted from
`image_qwen_image_2_1_i2i_gguf.json` (the official Comfy-Org Qwen Image 2.1 image-edit template with the
diffusion loader swapped for `UnetLoaderGGUF`). It accepts **1 to 10 reference images**, wired to
`TextEncodeQwenImage21`'s `images.image_1` … `images.image_10` with the VAE connected: each reference is seen
by the Qwen3-VL text encoder and spliced into the sequence as VAE latents. Sampling is the same as
text-to-image: 25 steps, `cfg 1.0`, `euler` / `simple`.

Placeholders: `PROMPT` `FIRST_FRAME` `WIDTH` `HEIGHT` `GEN_WIDTH` `GEN_HEIGHT` `SEED`.

> **Local only** (needs the ComfyUI-GGUF custom node, which Comfy Cloud does not have) and
> **non-commercial only** (Qwen Research License). See
> [`image-qwen-image-2.1-q8-gguf-api.md`](image-qwen-image-2.1-q8-gguf-api.md).

### Install

Same ComfyUI version (0.37.0 or newer), same ComfyUI-GGUF node pack and same three model files as the
text-to-image workflow; see the install section of
[`image-qwen-image-2.1-q8-gguf-api.md`](image-qwen-image-2.1-q8-gguf-api.md). No extra downloads are
needed.

### Console Configuration

This workflow goes into the image channel's **`ref_workflow`** field, next to the main workflow (run mode
**local**):

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

`genmedia.py` switches to `ref_workflow` automatically whenever `--ref` images are supplied and uploads every
reference to ComfyUI.

### How References Are Bound

All ten `LoadImage` nodes carry the `FIRST_FRAME` placeholder only so the template parses; the actual
binding is positional. `genmedia.py` binds the N-th `--ref` to the N-th `LoadImage` (node ids 101…110) and
removes every unused `images.image_N` slot together with its `LoadImage`. More than 10 `--ref` images are
rejected before anything is submitted. In the prompt, refer to the references by order; the source workflow
writes them as `<image1>`, `<image2>`, ….

### What Differs From the Source Workflow

The source is laid out for editing one picture in place; this template is laid out for generating a new
image at the requested size with the references as *conditions* (identity, outfit, layout, style).

- **Canvas.** The source samples from the node's own `latent` output — an empty latent at the first
  reference's size — and only uses the `ResolutionSelector` canvas when `custom_size` is switched on. This
  template always samples from an `EmptyLatentImage` at `GEN_WIDTH`x`GEN_HEIGHT`, so the output follows the
  request and composition follows the prompt. The node documents that a canvas other than the first
  reference's size shifts the edit. For a faithful in-place edit of a single image, fork the JSON and connect
  `KSampler.latent_image` to node `4` output `2`: the result then has the first reference's aspect ratio (the
  final `ImageScale` center-crops it to `WIDTH`x`HEIGHT`), and `genmedia.py` counts the fork as a redraw
  template rather than a multi-reference one.
- **Reference size.** The source sets `resolution` to `0`, which encodes every reference at its own pixel
  size. This template uses the node default `1024` (each reference resized to about 1024x1024 px of area,
  multiples of 32, aspect ratio kept), because platform reference images are large and every reference
  lengthens the sequence — time and VRAM grow with their number and size. Fork the JSON to change
  `resolution` on node `4`.
- **Cache node.** The source routes the model through `QwenImage21Cache` set to `auto` / `default`, which is
  what the model does without the node, so it is left out. On memory-starved cards, fork the JSON and insert
  it between nodes `1` and `6` with `dtype: "int8"`.
- `SaveImageAdvanced` is replaced by `SaveImage`; the `ImageCompare` preview is dropped.

### Runtime Parameters

`GEN_WIDTH` / `GEN_HEIGHT`, the final `ImageScale` to `WIDTH`x`HEIGHT`, and the absence of a `NEGATIVE`
placeholder (`cfg 1.0`; use `negative_mode: "append_exclusions"`) behave as in the text-to-image workflow.

---

## 中文

[`image-qwen-image-2.1-q8-gguf-ref10-api.json`](image-qwen-image-2.1-q8-gguf-ref10-api.json) 是
[`image-qwen-image-2.1-q8-gguf-api.md`](image-qwen-image-2.1-q8-gguf-api.md) 的参考图配套工作流,由
`image_qwen_image_2_1_i2i_gguf.json` 转换而来(把底模加载器换成 `UnetLoaderGGUF` 的 Comfy-Org 官方 Qwen Image
2.1 图像编辑模板)。接受 **1–10 张参考图**,接在 `TextEncodeQwenImage21` 的 `images.image_1` …
`images.image_10` 上并连上 VAE:每张参考图既给 Qwen3-VL 文本编码器看,也以 VAE latent 拼进序列。采样与文生图
相同:25 步、`cfg 1.0`、`euler` / `simple`。

占位符:`PROMPT` `FIRST_FRAME` `WIDTH` `HEIGHT` `GEN_WIDTH` `GEN_HEIGHT` `SEED`。

> **只能在本地跑**(需要 ComfyUI-GGUF 自定义节点,Comfy Cloud 没有),且**仅限非商业用途**
> (Qwen Research License)。详见
> [`image-qwen-image-2.1-q8-gguf-api.md`](image-qwen-image-2.1-q8-gguf-api.md)。

### 安装

ComfyUI 版本(0.37.0 及以上)、ComfyUI-GGUF 节点包和三个模型文件都与文生图工作流相同,见
[`image-qwen-image-2.1-q8-gguf-api.md`](image-qwen-image-2.1-q8-gguf-api.md) 的安装章节,无需额外下载。

### 控制台配置

本工作流填入图片渠道的 **`ref_workflow`** 字段,与主工作流并列(运行方式选 **本地**):

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

只要生成请求带 `--ref` 参考图,`genmedia.py` 就会自动切换到 `ref_workflow`,并把每张参考图上传到 ComfyUI。

### 参考图怎么绑

十个 `LoadImage` 都写 `FIRST_FRAME` 只是为了让模板可解析,实际按位置绑定:`genmedia.py` 把第 N 张 `--ref` 绑到
第 N 个 `LoadImage`(节点 101…110),没用到的 `images.image_N` 槽位连同对应的 `LoadImage` 一并摘掉;超过 10 张在
提交前报错。提示词里按顺序指代参考图;原工作流的写法是 `<image1>`、`<image2>`……

### 与原工作流的差别

原工作流是为「原地改一张图」摆的;本模板是为「按请求尺寸出一张新图、参考图当*条件*(身份、服装、版式、
画风)」摆的。

- **画布。** 原工作流从节点自带的 `latent` 输出采样——按第一张参考图尺寸给的空 latent——只有打开 `custom_size`
  才用 `ResolutionSelector` 的画布。本模板一律从 `GEN_WIDTH`x`GEN_HEIGHT` 的 `EmptyLatentImage` 采样,成图尺寸
  跟随请求、构图听提示词的。节点说明写明:画布不是第一张参考图的尺寸时编辑结果会偏移。要对单张图做贴合的
  原地编辑,fork 本 JSON,把 `KSampler.latent_image` 改接节点 `4` 的第 `2` 路输出:此时成图是第一张参考图的
  宽高比(末端 `ImageScale` 居中裁切到 `WIDTH`x`HEIGHT`),且 `genmedia.py` 会把这份 fork 视为原图重绘模板,
  不再按多参考图处理。
- **参考图尺寸。** 原工作流把 `resolution` 设成 `0`,即每张参考图按自身像素尺寸编码。本模板用节点默认值
  `1024`(每张缩放到约 1024x1024 px 的面积、32 的倍数、保持宽高比),因为平台的参考图都很大,而每张参考图都会
  拉长序列——耗时和显存随张数与尺寸增长。要改就 fork 本 JSON 调节点 `4` 的 `resolution`。
- **缓存节点。** 原工作流让模型过一道 `QwenImage21Cache`(`auto` / `default`),这与不接该节点时模型的默认行为
  相同,所以没有保留。显存吃紧时可 fork 本 JSON,把它插在节点 `1` 与 `6` 之间并设 `dtype: "int8"`。
- `SaveImageAdvanced` 换成了 `SaveImage`;`ImageCompare` 预览去掉了。

### 运行时参数

`GEN_WIDTH` / `GEN_HEIGHT`、末端 `ImageScale` 到 `WIDTH`x`HEIGHT`、没有 `NEGATIVE` 占位符(`cfg 1.0`;用
`negative_mode: "append_exclusions"`)这几点都与文生图工作流一致。
