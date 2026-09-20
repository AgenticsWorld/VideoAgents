# FLUX.2 [dev] ComfyUI Text-to-Image Workflow

[English](#english) | [中文](#中文)

## English

[`image-flux2-dev-fp8-api.json`](image-flux2-dev-fp8-api.json) is the
API-format ComfyUI text-to-image workflow for
[black-forest-labs/FLUX.2-dev](https://huggingface.co/black-forest-labs/FLUX.2-dev)
(32B). Sampling follows the official ComfyUI template: `BasicGuider` +
`FluxGuidance 4.0`, `Flux2Scheduler` 20 steps, `euler` sampler,
`EmptyFlux2LatentImage` at the requested size (FLUX.2 generates natively up to
~4 MP, so 2560x1440 needs no upscale pass).

Placeholders: `PROMPT` `WIDTH` `HEIGHT` `SEED`.

For image-to-image with up to 10 reference images, use the companion workflow
[`image-flux2-dev-fp8-ref10-api.md`](image-flux2-dev-fp8-ref10-api.md).

> **License**: FLUX.2 [dev] weights are under the FLUX non-commercial license.
> Confirm the terms before commercial use.

### Install

Update ComfyUI to a version supporting FLUX.2, then download the
ComfyUI-format weights (<https://huggingface.co/Comfy-Org/flux2-dev>,
`split_files/`):

```text
ComfyUI/models/
├── diffusion_models/flux2_dev_fp8mixed.safetensors      (35.5 GB)
├── text_encoders/mistral_3_small_flux2_fp8.safetensors  (18 GB)
└── vae/flux2-vae.safetensors
```

On Comfy Cloud the models are preinstalled; if a file name differs, fork the
JSON and adjust the loader nodes.

### Console Configuration

On the web console's `🎨 生成模型` page, switch the image channel to
**ComfyUI** with run mode **local** or **Comfy Cloud**, and fill in the
workflow path:

```text
comfy/image-flux2-dev-fp8-api.json
```

The `checkpoint` field stays empty: models are loaded by fixed file names via
`UNETLoader`/`CLIPLoader`/`VAELoader`.

### Runtime Parameters

`genmedia.py` fills `PROMPT`, `WIDTH`, `HEIGHT`, and `SEED` from the generation
request. Sampling settings are fixed in the JSON; fork the file to change them.
To speed up, insert a `LoraLoaderModelOnly`
(`Flux2TurboComfyv2.safetensors`, strength 1.0) after `UNETLoader` and set
`steps` to 8.

The workflow has no `NEGATIVE` placeholder — `BasicGuider` takes positive
conditioning only. A negative prompt passed with the default
`negative_mode: "conditioning"` is silently dropped. To make exclusions
effective, set the image channel's `negative_mode` to `append_exclusions`,
which appends `Exclude from the image: …` to the positive prompt instead.

---

## 中文

[`image-flux2-dev-fp8-api.json`](image-flux2-dev-fp8-api.json) 是
[black-forest-labs/FLUX.2-dev](https://huggingface.co/black-forest-labs/FLUX.2-dev)(32B)
的 API 格式 ComfyUI 文生图工作流。采样参数照 ComfyUI 官方模板:`BasicGuider` +
`FluxGuidance 4.0`、`Flux2Scheduler` 20 步、`euler` 采样器,`EmptyFlux2LatentImage`
按请求尺寸原生生成(FLUX.2 原生支持约 4 MP,2560x1440 无需放大环节)。

占位符:`PROMPT` `WIDTH` `HEIGHT` `SEED`。

带参考图(至多 10 张)的图生图请使用配套工作流
[`image-flux2-dev-fp8-ref10-api.md`](image-flux2-dev-fp8-ref10-api.md)。

> **授权**:FLUX.2 [dev] 权重为 FLUX 非商用许可,商用前须确认条款。

### 安装

把 ComfyUI 升级到支持 FLUX.2 的版本,再下载 ComfyUI 格式权重
(<https://huggingface.co/Comfy-Org/flux2-dev> 的 `split_files/`):

```text
ComfyUI/models/
├── diffusion_models/flux2_dev_fp8mixed.safetensors      (35.5 GB)
├── text_encoders/mistral_3_small_flux2_fp8.safetensors  (18 GB)
└── vae/flux2-vae.safetensors
```

Comfy Cloud 已预装模型;文件名不一致时 fork 本 JSON 改加载节点即可。

### 控制台配置

在网页控制台 `🎨 生成模型` 页把图像渠道切到 **ComfyUI**,运行方式选**本地**或
**Comfy Cloud**,工作流路径填:

```text
comfy/image-flux2-dev-fp8-api.json
```

`checkpoint` 字段留空:本工作流经 `UNETLoader`/`CLIPLoader`/`VAELoader` 按固定文件名加载模型。

### 运行时参数

`genmedia.py` 在执行时按生成请求填入 `PROMPT`、`WIDTH`、`HEIGHT`、`SEED`。采样参数固定在
JSON 里,要改请 fork 文件。想提速:在 `UNETLoader` 后插入 `LoraLoaderModelOnly`
(`Flux2TurboComfyv2.safetensors`,强度 1.0),`steps` 改 8。

本工作流没有 `NEGATIVE` 占位符——`BasicGuider` 只收正面 conditioning。默认
`negative_mode: "conditioning"` 下传入的负面提示词会被静默丢弃;要让排除项生效,把图像渠道的
`negative_mode` 设为 `append_exclusions`,负面词会以 `Exclude from the image: …` 并入正面提示词。
