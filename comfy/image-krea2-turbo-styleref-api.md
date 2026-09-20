# Krea 2 Turbo ComfyUI Style-Reference Workflow

[English](#english) | [中文](#中文)

## English

[`image-krea2-turbo-styleref-api.json`](image-krea2-turbo-styleref-api.json) is the reference-image companion of
[`image-krea2-turbo-api.md`](image-krea2-turbo-api.md). It follows the official Krea 2 *style reference* template:
`TextEncodeQwenImageEditPlus` (`image1`…`image3`) → `FluxKontextMultiReferenceLatentMethod`
(`index_timestep_zero`) → `CFGGuider cfg 1.0`, `ModelSamplingFlux 1.15 / 0.5`, `BasicScheduler simple` 8 steps,
with the official **`krea2_style_reference.safetensors`** LoRA.

Placeholders: `PROMPT` `FIRST_FRAME` `WIDTH` `HEIGHT` `GEN_WIDTH` `GEN_HEIGHT` `SEED`.

> **What it is for**: the references steer *style, mood and visual direction*. They do **not** preserve a
> character's or prop's identity, and a layout template passed as a reference leaks into the picture as style
> (verified on a four-panel character sheet: mannequin drawn into the panel, identity lost). Use
> [`image-qwen-image-edit-2511-ref3-api.md`](image-qwen-image-edit-2511-ref3-api.md) or
> [`image-flux2-dev-fp8-ref10-api.md`](image-flux2-dev-fp8-ref10-api.md) for identity / layout work.

### Install

The text-to-image files plus the LoRA (<https://huggingface.co/Comfy-Org/Krea-2>):

```text
ComfyUI/models/loras/krea2_style_reference.safetensors   (0.5 GB, required)
```

### Console Configuration

Fill it into the image channel's **`ref_workflow`** field — see the snippet in
[`image-krea2-turbo-api.md`](image-krea2-turbo-api.md).

### Runtime Parameters

1 to 3 `--ref` images. All three `LoadImage` nodes carry `FIRST_FRAME` only so the template parses; `genmedia.py`
binds the N-th `--ref` to the N-th `LoadImage` and removes unused `image2` / `image3` slots. More than 3 references
are rejected before submission.

`GEN_WIDTH` / `GEN_HEIGHT` are filled by `genmedia.py` with the requested aspect ratio scaled down to at most
1.6 MP (multiples of 16) — the model generates at that native size and the final `ImageScale` (lanczos)
brings the image to the requested `WIDTH`x`HEIGHT` (2560x1440 platform spec). Requests at or below
1.6 MP are generated 1:1.

There is no `NEGATIVE` placeholder: the negative side is a `ConditioningZeroOut` (`cfg 1.0` / unconditional
model). Set the image channel's `negative_mode` to `append_exclusions` so exclusions are appended to the
positive prompt as `Exclude from the image: …` instead of being dropped.

---

## 中文

[`image-krea2-turbo-styleref-api.json`](image-krea2-turbo-styleref-api.json) 是
[`image-krea2-turbo-api.md`](image-krea2-turbo-api.md) 的参考图配套工作流,结构照 Krea 2 官方*风格参考*模板:
`TextEncodeQwenImageEditPlus`(`image1`…`image3`)→ `FluxKontextMultiReferenceLatentMethod`(`index_timestep_zero`)→
`CFGGuider cfg 1.0`、`ModelSamplingFlux 1.15 / 0.5`、`BasicScheduler simple` 8 步,并挂官方
**`krea2_style_reference.safetensors`** LoRA。

占位符:`PROMPT` `FIRST_FRAME` `WIDTH` `HEIGHT` `GEN_WIDTH` `GEN_HEIGHT` `SEED`。

> **适用范围**:参考图传的是*画风、氛围、视觉方向*,**不保**人物/道具身份;把版式模板当参考图传进去会被当成「风格」画进画面
> (四格人物 sheet 实测:人偶被画进格子、身份丢失)。保身份/按版式出图请用
> [`image-qwen-image-edit-2511-ref3-api.md`](image-qwen-image-edit-2511-ref3-api.md) 或
> [`image-flux2-dev-fp8-ref10-api.md`](image-flux2-dev-fp8-ref10-api.md)。

### 安装

文生图的全部文件,外加 LoRA(<https://huggingface.co/Comfy-Org/Krea-2>):

```text
ComfyUI/models/loras/krea2_style_reference.safetensors   (0.5 GB,必需)
```

### 控制台配置

填在图像渠道的 **`ref_workflow`**(图生图)位,配置片段见 [`image-krea2-turbo-api.md`](image-krea2-turbo-api.md)。

### 运行时参数

支持 1–3 张 `--ref`。三个 `LoadImage` 都写 `FIRST_FRAME` 只是为了让模板可解析;`genmedia.py` 把第 N 张 `--ref` 绑到第 N 个
`LoadImage`,没用到的 `image2` / `image3` 槽位自动摘掉;超过 3 张在提交前报错。

`GEN_WIDTH` / `GEN_HEIGHT` 由 `genmedia.py` 按本次宽高比压到 1.6 MP 以内(16 的倍数)填入——模型按这个原生尺寸生成,
末端 `ImageScale`(lanczos)再放大到请求的 `WIDTH`x`HEIGHT`(平台出图规格 2560x1440);请求尺寸不超过 1.6 MP 时 1:1 生成。

没有 `NEGATIVE` 占位符:负面侧是 `ConditioningZeroOut`(`cfg 1.0` / 无条件模型)。把图像渠道的 `negative_mode` 设为
`append_exclusions`,排除项才会以 `Exclude from the image: …` 并入正面提示词,否则被静默丢弃。
