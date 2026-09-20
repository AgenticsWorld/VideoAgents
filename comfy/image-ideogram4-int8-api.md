# Ideogram 4 ComfyUI Text-to-Image Workflow

[English](#english) | [中文](#中文)

## English

[`image-ideogram4-int8-api.json`](image-ideogram4-int8-api.json) is the API-format text-to-image workflow for
[Ideogram 4.0](https://huggingface.co/Comfy-Org/Ideogram-4) open weights (9.3B), native ComfyUI nodes only:
`DualModelGuider` (conditional + separate unconditional model, `cfg 7`) with `CFGOverride` (`cfg 3` over the last
30 % of steps), `Ideogram4Scheduler` and `euler`. The scheduler is fixed to the official **Quality** preset
(48 steps, `mu 0.0`, `std 1.5`); *Default* is 20 / 0.0 / 1.75 and *Turbo* 12 / 0.5 / 1.75 — fork the JSON to switch.

Placeholders: `PROMPT` `WIDTH` `HEIGHT` `GEN_WIDTH` `GEN_HEIGHT` `SEED`.

Ideogram 4 has no image-conditioning input in ComfyUI (no reference latents), so there is no image-to-image
companion; pair it with a `ref_workflow` of another model.

> **License**: Ideogram Non-Commercial Model Agreement — commercial deployment needs a separate licence.

> **Safety filter**: for some prompts the model itself renders a grey card reading *"Image blocked by safety
> filter"* and the job still reports success. An ordinary landscape prompt triggered it three times in testing
> (with and without the negative list, with and without an artist name); the trigger was not identified. Inspect
> the output before using it.

### Install

```text
ComfyUI/models/
├── diffusion_models/ideogram4_int8_convrot.safetensors
├── diffusion_models/ideogram4_unconditional_int8_convrot.safetensors
├── text_encoders/qwen3vl_8b_fp8_scaled.safetensors
└── vae/flux2-vae.safetensors
```

(`…_fp8_scaled` variants of the two diffusion models work as well.)

### Console Configuration

```json
{
  "image": {
    "provider": "comfyui",
    "comfyui": {
      "workflow": "comfy/image-ideogram4-int8-api.json",
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

The model natively prefers structured JSON captions (colour palette, layout boxes, text placement); plain prose
prompts work too.

---

## 中文

[`image-ideogram4-int8-api.json`](image-ideogram4-int8-api.json) 是
[Ideogram 4.0](https://huggingface.co/Comfy-Org/Ideogram-4) 开放权重(9.3B)的 API 格式文生图工作流,全部 ComfyUI 原生节点:
`DualModelGuider`(条件模型 + 独立无条件模型,`cfg 7`)配 `CFGOverride`(后 30 % 步数 `cfg 3`)、`Ideogram4Scheduler`、`euler`。
调度器固定为官方 **Quality** 预设(48 步、`mu 0.0`、`std 1.5`);*Default* 为 20 / 0.0 / 1.75,*Turbo* 为 12 / 0.5 / 1.75,要换请 fork 本 JSON。

占位符:`PROMPT` `WIDTH` `HEIGHT` `GEN_WIDTH` `GEN_HEIGHT` `SEED`。

Ideogram 4 在 ComfyUI 里没有图像条件入口(不读参考 latent),所以没有图生图配套;`ref_workflow` 请配其它模型的模板。

> **授权**:Ideogram Non-Commercial Model Agreement,商用部署需另行授权。

> **安全过滤**:对某些提示词,模型会自己画出一张灰底的 *"Image blocked by safety filter"* 占位图,而任务仍报成功。
> 实测一条普通风景提示词连续三次触发(带/不带负面清单、带/不带艺术家名都一样),诱因未定位。成品务必先看图再用。

### 安装

```text
ComfyUI/models/
├── diffusion_models/ideogram4_int8_convrot.safetensors
├── diffusion_models/ideogram4_unconditional_int8_convrot.safetensors
├── text_encoders/qwen3vl_8b_fp8_scaled.safetensors
└── vae/flux2-vae.safetensors
```

(两个扩散模型的 `…_fp8_scaled` 版本同样可用。)

### 控制台配置

```json
{
  "image": {
    "provider": "comfyui",
    "comfyui": {
      "workflow": "comfy/image-ideogram4-int8-api.json",
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

模型原生更吃结构化 JSON 描述(配色、版面框、文字位置);普通散文提示词也能用。
