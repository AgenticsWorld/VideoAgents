# MiniMax H3 Image ComfyUI Text-to-Image Workflow

[English](#english) | [中文](#中文)

## English

[`image-minimax-h3-image-t2i-api.json`](image-minimax-h3-image-t2i-api.json) is the API-format text-to-image
workflow for [MiniMaxAI/MiniMax-H3-Image](https://huggingface.co/MiniMaxAI/MiniMax-H3-Image), using the ComfyUI
weights from [Comfy-Org/MiniMax-H3-Image](https://huggingface.co/Comfy-Org/MiniMax-H3-Image). It needs **no
custom node**: the core `MiniMaxH3ImageConditioning` node feeds a `KSampler` with **6 steps, `cfg 1.0`, `euler` /
`simple`**. These are the settings of the official ComfyUI template
[`image_minimax_h3_image_t2i.json`](https://github.com/Comfy-Org/workflow_templates/blob/main/templates/image_minimax_h3_image_t2i.json).
The checkpoint is distilled to 6 steps and has no classifier-free guidance.

MiniMax-H3-Image uses one checkpoint for text-to-image and editing. The reference-image companion
[`image-minimax-h3-image-i2i-api.md`](image-minimax-h3-image-i2i-api.md) uses the same three model files.

Placeholders: `PROMPT` `WIDTH` `HEIGHT` `SEED`.

> **Not tested on a GPU yet (2026-11-12).** The graph was checked against the node definitions in ComfyUI
> source (commit `c5751c81`, 2026-11-11) and loaded through `genmedia.py`'s placeholder filling, but no image
> has been generated with it, locally or on Comfy Cloud. Generation time and VRAM use are unmeasured.
>
> **License**: MiniMax-H3-Image Community License. Read the full
> [LICENSE](https://huggingface.co/MiniMaxAI/MiniMax-H3-Image/blob/main/LICENSE) before you use it. As of
> 2026-11-12 it covers use only in its "Applicable Territory", which excludes the EU, the UK, South Korea and the
> United States. Commercial use needs separate written authorization from MiniMax above USD 1M annualized
> revenue or for a hosted service to third parties, and a commercial product must display the name
> "MiniMax-H3-Image".

### Install

1. **ComfyUI 0.45.0 or newer.** `MiniMaxH3ImageConditioning` and the `minimax` type of `CLIPLoader` are core
   nodes added with MiniMax-H3-Image support.
2. Model files, as in the official template:

```text
ComfyUI/models/
├── diffusion_models/minimax_h3_image_pruned_int8_convrot.safetensors   (21.0 GB)
├── text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors       (27.1 GB)
└── vae/minimax_h3_image_vae_fp16.safetensors                           (1.2 GB)
```

The diffusion model and the VAE come from <https://huggingface.co/Comfy-Org/MiniMax-H3-Image>. The text encoder
is in that repository and also in <https://huggingface.co/Comfy-Org/MiniMax-H3>. The model card says the text
encoder is MiniMax-H3's Qwen3-VL encoder, unchanged.

The same repository has alternative files. To use one, fork the JSON and change one line. None of these were
tested with this template:

| Node | File | Size |
|---|---|---|
| `1` diffusion model | `minimax_h3_image_pruned_w4a8.safetensors` | 12.6 GB |
| `1` diffusion model | `minimax_h3_image_pruned_bf16.safetensors` | 40.2 GB |
| `2` text encoder | `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors`, the file [`video-minimax-h3-ref2va-api.json`](video-minimax-h3-ref2va-api.md) uses | 15.7 GB |

The DiT has 33.1B parameters and the text encoder is a 32B model, so loading is heavy even with int8 weights.
Before you make this the production image workflow, run one 16:9 image with no other ComfyUI job running. The
host checks and the "do not resubmit" signs in the
[MiniMax H3 video workflow notes](video-minimax-h3-ref2va-api.md#host-preflight-and-failure-recovery) apply to
the same text encoder here.

### Console Configuration

Run mode **local**:

```json
{
  "image": {
    "provider": "comfyui",
    "comfyui": {
      "workflow": "comfy/image-minimax-h3-image-t2i-api.json",
      "ref_workflow": "comfy/image-minimax-h3-image-i2i-api.json",
      "negative_mode": "append_exclusions",
      "checkpoint": ""
    }
  }
}
```

### Runtime Parameters

**Generation size.** MiniMax-H3-Image does poorly at low resolution. It works from about 4 MP to 20 MP and
gives its best results around 16 MP. The platform image spec (2560x1440, 3.7 MP) and the smaller defaults
(1280x720) are below that range, so this workflow does not sample at `WIDTH`x`HEIGHT` directly:

1. Node `10` (`EmptyImage`) is a blank `WIDTH`x`HEIGHT` image. It only carries the requested aspect ratio.
2. Node `11` (`ImageScaleToTotalPixels`, `megapixels 4.0`, `resolution_steps 32`) scales it to 4 MP
   (4,194,304 px, the official template's default and the model card's default `min_generation_area` of
   2048x2048). Both sides land on H3's 32-pixel grid.
3. Node `12` (`GetImageSize`) passes that size to node `4`, which creates the latent.
4. The final `ImageScale` (lanczos, center crop) brings the image to `WIDTH`x`HEIGHT`.

| Request | Generated at |
|---|---|
| 2560x1440 / 1280x720 (16:9) | 2720x1536 |
| 1440x2560 (9:16) | 1536x2720 |
| 1024x1024 (1:1) | 2048x2048 |
| 1152x864 (4:3) | 2368x1760 |
| 1680x720 (21:9) | 3136x1344 |

Requests above 4 MP, such as 3840x2160, are also generated at 4 MP and then enlarged by `ImageScale`. For more
detail, fork the JSON and raise `megapixels` on node `11` (16 is the node's maximum and the model's best
range). Time and memory grow with the pixel count. `GEN_WIDTH` / `GEN_HEIGHT` are not used: they are capped
at 1.6 MP, which is too small for this model.

**No negative prompt.** The model has no classifier-free guidance. The model card says `guidance_scale` must be
1 and `negative_prompt` is not supported. Node `5` (`ConditioningZeroOut`) fills the sampler's negative input,
and there is no `NEGATIVE` placeholder. Set the image channel's `negative_mode` to `append_exclusions` so
exclusions are added to the positive prompt as `Exclude from the image: …` instead of being dropped. Do not raise
`cfg`.

**Prompting.** The model expects a detailed **English** description of the whole image. MiniMax provides a
prompt expander ([MiniMax-H3-Image-PE](https://huggingface.co/MiniMaxAI/MiniMax-H3-Image-PE)), and the official
template has an optional expander branch (`TextGenerateV2` with `qwen3.5_9b_w4a8_gptq`, 7.3 GB), off by default.
This workflow leaves that branch out because VideoAgents prompts are already full descriptions. To use the
expander, start from the official template.

### Differences From the Official Template

- The prompt expander branch is removed, and `PROMPT` goes straight to `MiniMaxH3ImageConditioning`.
- The `ResolutionSelector` (aspect-ratio preset, default 1:1 at 4 MP) is replaced by nodes `10`–`12`, which
  follow the requested aspect ratio at the same 4 MP. A final `ImageScale` to `WIDTH`x`HEIGHT` is added.
- The seed comes from `SEED`, and the output prefix is `videoagents/minimax-h3-image`.

---

## 中文

[`image-minimax-h3-image-t2i-api.json`](image-minimax-h3-image-t2i-api.json) 是
[MiniMaxAI/MiniMax-H3-Image](https://huggingface.co/MiniMaxAI/MiniMax-H3-Image) 的 API 格式文生图工作流,权重用
[Comfy-Org/MiniMax-H3-Image](https://huggingface.co/Comfy-Org/MiniMax-H3-Image) 的 ComfyUI 版。**不需要任何自定义节点**:
原生 `MiniMaxH3ImageConditioning` 接 `KSampler`,**6 步、`cfg 1.0`、`euler` / `simple`**,与官方 ComfyUI 模板
[`image_minimax_h3_image_t2i.json`](https://github.com/Comfy-Org/workflow_templates/blob/main/templates/image_minimax_h3_image_t2i.json)
一致。该权重蒸馏到 6 步,不做无分类器引导(CFG)。

MiniMax-H3-Image 文生图与编辑共用一个权重。参考图配套工作流
[`image-minimax-h3-image-i2i-api.md`](image-minimax-h3-image-i2i-api.md) 用同样三个模型文件。

占位符:`PROMPT` `WIDTH` `HEIGHT` `SEED`。

> **尚未上 GPU 实测(2026-11-12)。** 节点图已对照 ComfyUI 源码(commit `c5751c81`,2026-11-11)里的节点定义核对过,
> 也用 `genmedia.py` 的占位符替换加载过,但本地和 Comfy Cloud 都还没用它出过图。耗时和显存占用都没测。
>
> **许可**:MiniMax-H3-Image Community License,使用前请通读
> [LICENSE](https://huggingface.co/MiniMaxAI/MiniMax-H3-Image/blob/main/LICENSE)。截至 2026-11-12,许可只覆盖其「适用地区」,
> 欧盟、英国、韩国、美国不在其内。年化营收超过 100 万美元,或向第三方提供托管服务时,商用须另获 MiniMax 书面授权;
> 商用产品界面须显著标明「MiniMax-H3-Image」。

### 安装

1. **ComfyUI 0.45.0 及以上**:`MiniMaxH3ImageConditioning` 与 `CLIPLoader` 的 `minimax` 类型是随 MiniMax-H3-Image 支持加入的原生节点。
2. 模型文件(与官方模板相同):

```text
ComfyUI/models/
├── diffusion_models/minimax_h3_image_pruned_int8_convrot.safetensors   (21.0 GB)
├── text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors       (27.1 GB)
└── vae/minimax_h3_image_vae_fp16.safetensors                           (1.2 GB)
```

扩散模型与 VAE 来自 <https://huggingface.co/Comfy-Org/MiniMax-H3-Image>。文本编码器在该仓库和
<https://huggingface.co/Comfy-Org/MiniMax-H3> 里都有。模型卡说明,它就是 MiniMax-H3 原样的 Qwen3-VL 文本编码器。

同一仓库还有其它文件可替换,fork 本 JSON 改一行即可。以下组合都没有用本模板实测过:

| 节点 | 文件 | 大小 |
|---|---|---|
| `1` 扩散模型 | `minimax_h3_image_pruned_w4a8.safetensors` | 12.6 GB |
| `1` 扩散模型 | `minimax_h3_image_pruned_bf16.safetensors` | 40.2 GB |
| `2` 文本编码器 | `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors`,即 [`video-minimax-h3-ref2va-api.json`](video-minimax-h3-ref2va-api.md) 用的那个文件 | 15.7 GB |

DiT 有 33.1B 参数,文本编码器是 32B 模型,即使是 int8 权重,加载也很重。把它设为生产图像工作流之前,先在没有其它
ComfyUI 任务时出一张 16:9 图。[MiniMax H3 视频工作流说明](video-minimax-h3-ref2va-api.md#主机预检与故障恢复)里的
主机预检和「不要反复提交」的情形,对这里同一个文本编码器同样适用。

### 控制台配置

运行方式选 **本地**:

```json
{
  "image": {
    "provider": "comfyui",
    "comfyui": {
      "workflow": "comfy/image-minimax-h3-image-t2i-api.json",
      "ref_workflow": "comfy/image-minimax-h3-image-i2i-api.json",
      "negative_mode": "append_exclusions",
      "checkpoint": ""
    }
  }
}
```

### 运行时参数

**生成尺寸。** MiniMax-H3-Image 低分辨率效果差,有效范围约 4–20 MP,16 MP 左右最好。平台出图规格(2560x1440,3.7 MP)和更小的默认尺寸
(1280x720)都在这个范围以下,所以本工作流不直接按 `WIDTH`x`HEIGHT` 采样:

1. 节点 `10`(`EmptyImage`)生成一张 `WIDTH`x`HEIGHT` 的空白图,只用来携带本次宽高比。
2. 节点 `11`(`ImageScaleToTotalPixels`,`megapixels 4.0`、`resolution_steps 32`)把它缩放到 4 MP
   (4,194,304 像素,即官方模板默认值,也是模型卡 `min_generation_area` 的默认值 2048x2048),宽高都落在 H3 的 32 像素网格上。
3. 节点 `12`(`GetImageSize`)把这个尺寸交给节点 `4`,由它创建 latent。
4. 末端 `ImageScale`(lanczos、居中裁切)再把图缩放到 `WIDTH`x`HEIGHT`。

| 请求尺寸 | 实际生成尺寸 |
|---|---|
| 2560x1440 / 1280x720(16:9) | 2720x1536 |
| 1440x2560(9:16) | 1536x2720 |
| 1024x1024(1:1) | 2048x2048 |
| 1152x864(4:3) | 2368x1760 |
| 1680x720(21:9) | 3136x1344 |

超过 4 MP 的请求(如 3840x2160)同样按 4 MP 生成,再由 `ImageScale` 放大。想要更多细节,fork 本 JSON,调高节点 `11` 的
`megapixels`(节点上限 16,也是模型的最佳区间)。耗时和内存随像素数增长。没有用 `GEN_WIDTH` / `GEN_HEIGHT`:它们上限 1.6 MP,
对这个模型太小。

**没有负面提示词。** 模型不做无分类器引导,模型卡说明 `guidance_scale` 只能为 1,也不支持 `negative_prompt`。采样器的负面输入由节点 `5`
(`ConditioningZeroOut`)填充,没有 `NEGATIVE` 占位符。把图片渠道的 `negative_mode` 设为 `append_exclusions`,排除项会以
`Exclude from the image: …` 并入正面提示词,否则会被丢弃。不要调高 `cfg`。

**提示词写法。** 模型期望一段完整描述整张画面的**英文**详细描述。MiniMax 提供提示词扩写模型
([MiniMax-H3-Image-PE](https://huggingface.co/MiniMaxAI/MiniMax-H3-Image-PE)),官方模板里也有一条可选扩写分支
(`TextGenerateV2` + `qwen3.5_9b_w4a8_gptq`,7.3 GB,默认关闭)。VideoAgents 写出的提示词本身已是完整描述,所以本工作流去掉了这条分支。
需要扩写时请从官方模板起步。

### 与官方模板的差异

- 去掉提示词扩写分支,`PROMPT` 直接进 `MiniMaxH3ImageConditioning`。
- `ResolutionSelector`(宽高比预设,默认 1:1、4 MP)换成节点 `10`–`12`,按本次请求的宽高比生成,像素数同为 4 MP;末端加了缩放到
  `WIDTH`x`HEIGHT` 的 `ImageScale`。
- 种子取 `SEED`,输出前缀为 `videoagents/minimax-h3-image`。
