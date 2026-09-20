# FLUX.2 [dev] ComfyUI Multi-Reference Image-to-Image Workflow

[English](#english) | [中文](#中文)

## English

[`image-flux2-dev-fp8-ref10-api.json`](image-flux2-dev-fp8-ref10-api.json)
is the reference-image companion of
[`image-flux2-dev-fp8-api.md`](image-flux2-dev-fp8-api.md), for
[black-forest-labs/FLUX.2-dev](https://huggingface.co/black-forest-labs/FLUX.2-dev).
It accepts **1 to 10 reference images**. Each reference goes through
`LoadImage` → `ImageScaleToTotalPixels` (1 MP) → `VAEEncode` →
`ReferenceLatent`; the `ReferenceLatent` nodes are chained onto the prompt
conditioning (reference 1 first), and the image is generated from an empty
latent at the requested size — references are *conditions* (identity, outfit,
layout, style), not the initial canvas, so composition follows the prompt.

Placeholders: `PROMPT` `FIRST_FRAME` `WIDTH` `HEIGHT` `SEED`.

> **License**: FLUX.2 [dev] weights are under the FLUX non-commercial license.
> Confirm the terms before commercial use.

### Install

Same models as the text-to-image workflow; see the install section of
[`image-flux2-dev-fp8-api.md`](image-flux2-dev-fp8-api.md). No extra downloads
are needed.

### Console Configuration

This workflow goes into the image channel's **`ref_workflow`** field, next to
the main workflow (run mode **local** or **Comfy Cloud**):

```json
{
  "image": {
    "provider": "comfyui",
    "comfyui": {
      "workflow": "comfy/image-flux2-dev-fp8-api.json",
      "ref_workflow": "comfy/image-flux2-dev-fp8-ref10-api.json",
      "negative_mode": "append_exclusions",
      "checkpoint": ""
    }
  }
}
```

`genmedia.py` switches to `ref_workflow` automatically whenever `--ref` images
are supplied and uploads every reference to ComfyUI.

### How References Are Bound

All ten `LoadImage` nodes carry the `FIRST_FRAME` placeholder only so the
template parses; the actual binding is positional. Because the template has
more than one `LoadImage`, `genmedia.py` binds the N-th `--ref` to the N-th
`LoadImage` (node ids 101…110) and **removes every unused slot as a whole
chain** (`LoadImage` → scale → `VAEEncode` → `ReferenceLatent`, with the
downstream conditioning re-wired — the same as bypassing the row in the UI).
More than 10 `--ref` images are rejected before anything is submitted.

Refer to references by order in the prompt ("the first reference image is the
layout template; the other reference images show the character…").

### Runtime Parameters

`PROMPT`, `WIDTH`, `HEIGHT`, `SEED` and the references come from the generation
request. `FluxGuidance 4.0`, 20 steps and `megapixels 1.0` per reference are
fixed in the JSON; fork the file to change them. Every reference adds ~1 MP of
tokens to a 32B model — time and VRAM grow with the count, so pass only the
anchors the shot needs (lower `megapixels` to 0.5 when using many). The
negative prompt behaves as in the text-to-image workflow (no `NEGATIVE`
placeholder; use `negative_mode: "append_exclusions"`).

Storyboard sketches: with this template configured, the image-to-image
capacity reported to the sketch pipeline is 10 (character sheets are passed as
references); single-image img2img templates still count as 0.

---

## 中文

[`image-flux2-dev-fp8-ref10-api.json`](image-flux2-dev-fp8-ref10-api.json) 是
[`image-flux2-dev-fp8-api.md`](image-flux2-dev-fp8-api.md) 的参考图配套工作流,模型为
[black-forest-labs/FLUX.2-dev](https://huggingface.co/black-forest-labs/FLUX.2-dev)。
支持 **1–10 张参考图**:每张走 `LoadImage` → `ImageScaleToTotalPixels`(1 MP)→
`VAEEncode` → `ReferenceLatent`,各 `ReferenceLatent` 串联挂到提示词 conditioning 上
(第 1 张在链首),再从请求尺寸的空 latent 生成——参考图是**条件**(身份、服装、版式、风格),
不是初始画面,构图听提示词的。

占位符:`PROMPT` `FIRST_FRAME` `WIDTH` `HEIGHT` `SEED`。

> **授权**:FLUX.2 [dev] 权重为 FLUX 非商用许可,商用前须确认条款。

### 安装

模型与文生图工作流相同,见
[`image-flux2-dev-fp8-api.md`](image-flux2-dev-fp8-api.md) 的安装一节,无需额外下载。

### 控制台配置

本工作流填在图像渠道的 **`ref_workflow`**(参考图工作流)字段,与主工作流并列
(运行方式**本地**或 **Comfy Cloud**):

```json
{
  "image": {
    "provider": "comfyui",
    "comfyui": {
      "workflow": "comfy/image-flux2-dev-fp8-api.json",
      "ref_workflow": "comfy/image-flux2-dev-fp8-ref10-api.json",
      "negative_mode": "append_exclusions",
      "checkpoint": ""
    }
  }
}
```

只要带了 `--ref`,`genmedia.py` 就自动切到 `ref_workflow`,并把每张参考图上传到 ComfyUI。

### 参考图怎么绑

十个 `LoadImage` 都写着 `FIRST_FRAME` 占位符只是为了让模板可解析,真正的绑定按位置来:
模板里 `LoadImage` 多于一个时,`genmedia.py` 把第 N 张 `--ref` 绑到第 N 个 `LoadImage`
(节点 id 101…110),并把**没用到的槽位整条摘除**(`LoadImage` → 缩放 → `VAEEncode` →
`ReferenceLatent`,下游 conditioning 改接上游,等同界面里把该行 Bypass)。超过 10 张
`--ref` 在提交前报错。

提示词里按顺序指代参考图(「第一张参考图是版式模板,其余参考图是角色……」)。

### 运行时参数

`PROMPT`、`WIDTH`、`HEIGHT`、`SEED` 与参考图来自生成请求。`FluxGuidance 4.0`、20 步、
每张参考图 `megapixels 1.0` 固定在 JSON 里,要改请 fork 文件。每张参考图给 32B 模型增加约
1 MP 的 token,耗时与显存随张数上涨,只传镜头真正需要的锚图(张数多时把 `megapixels` 降到 0.5)。
负面提示词行为同文生图工作流(无 `NEGATIVE` 占位符,用 `negative_mode: "append_exclusions"`)。

故事板草图:配置本模板后,报给草图链的图生图容量为 10(人物 sheet 作参考图传入);
单图 img2img 模板仍按 0 计。
