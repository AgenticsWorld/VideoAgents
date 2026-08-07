# Z-Image Turbo ComfyUI Image-to-Image Workflow

[English](#english) | [中文](#中文)

## English

[`image-z-image-turbo-bf16-img2img-api.json`](image-z-image-turbo-bf16-img2img-api.json)
is the image-to-image companion of
[`image-z-image-turbo-bf16-api.md`](image-z-image-turbo-bf16-api.md), for
[Tongyi-MAI/Z-Image-Turbo](https://huggingface.co/Tongyi-MAI/Z-Image-Turbo).
The reference image is loaded via `LoadImage`, Lanczos-scaled with center crop
to the requested `WIDTH`x`HEIGHT`, VAE-encoded, then re-sampled at
`denoise 0.65` (8 steps, `cfg 1.0` — same turbo settings as the text-to-image
workflow).

Placeholders: `PROMPT` `FIRST_FRAME` `WIDTH` `HEIGHT` `SEED`.

### Install

Same models as the text-to-image workflow; see the install section of
[`image-z-image-turbo-bf16-api.md`](image-z-image-turbo-bf16-api.md). No extra
downloads are needed.

### Console Configuration

This workflow is not selected as the main image workflow. It goes into the
image channel's **`ref_workflow`** field, next to the main workflow:

```json
{
  "image": {
    "provider": "comfyui",
    "comfyui": {
      "url": "http://127.0.0.1:8188",
      "workflow": "comfy/image-z-image-turbo-bf16-api.json",
      "ref_workflow": "comfy/image-z-image-turbo-bf16-img2img-api.json",
      "checkpoint": ""
    }
  }
}
```

`genmedia.py` switches to `ref_workflow` automatically whenever a `--ref`
image is supplied, uploads the reference to ComfyUI, and injects it as
`FIRST_FRAME`. Exactly one `--ref` is supported; more than one is rejected,
and a `--ref` request without a configured `ref_workflow` fails explicitly.

### Runtime Parameters

`PROMPT`, `WIDTH`, `HEIGHT`, `SEED`, and `FIRST_FRAME` come from the
generation request. `denoise 0.65` balances reference fidelity against prompt
adherence: lower keeps more of the reference, higher gives the prompt more
freedom. It is fixed in the JSON; fork the file to change it. The negative
prompt behaves as in the text-to-image workflow (no `NEGATIVE` placeholder;
use `negative_mode: "append_exclusions"` when exclusions matter).

---

## 中文

[`image-z-image-turbo-bf16-img2img-api.json`](image-z-image-turbo-bf16-img2img-api.json)
是 [`image-z-image-turbo-bf16-api.md`](image-z-image-turbo-bf16-api.md) 的图生图配套工作流,
基于 [Tongyi-MAI/Z-Image-Turbo](https://huggingface.co/Tongyi-MAI/Z-Image-Turbo)。
参考图经 `LoadImage` 载入,用 Lanczos 缩放并居中裁剪到请求的 `WIDTH`x`HEIGHT`,
VAE 编码后以 `denoise 0.65` 重新采样(8 步、`cfg 1.0`,与文生图工作流相同的
turbo 设置)。

占位符:`PROMPT` `FIRST_FRAME` `WIDTH` `HEIGHT` `SEED`。

### 安装

与文生图工作流使用相同模型,见
[`image-z-image-turbo-bf16-api.md`](image-z-image-turbo-bf16-api.md) 的安装章节,
无需额外下载。

### 控制台配置

本工作流不作为主图片工作流选择,而是填入图片渠道的 **`ref_workflow`** 字段,
与主工作流并列:

```json
{
  "image": {
    "provider": "comfyui",
    "comfyui": {
      "url": "http://127.0.0.1:8188",
      "workflow": "comfy/image-z-image-turbo-bf16-api.json",
      "ref_workflow": "comfy/image-z-image-turbo-bf16-img2img-api.json",
      "checkpoint": ""
    }
  }
}
```

只要生成请求带 `--ref` 参考图,`genmedia.py` 就会自动切换到 `ref_workflow`,
把参考图上传到 ComfyUI 并注入为 `FIRST_FRAME`。仅支持一张 `--ref`,多张会被拒绝;
带 `--ref` 但未配置 `ref_workflow` 会显式报错。

### 运行时参数

`PROMPT`、`WIDTH`、`HEIGHT`、`SEED`、`FIRST_FRAME` 来自生成请求。`denoise 0.65`
平衡参考图保真与提示词自由度:调低更贴近参考图,调高提示词发挥空间更大。
该值固定在 JSON 中,如需调整请 fork 一份修改。负向提示词行为与文生图工作流一致
(没有 `NEGATIVE` 占位符;需要排除项时用 `negative_mode: "append_exclusions"`)。
