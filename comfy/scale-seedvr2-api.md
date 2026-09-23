# SeedVR2 ComfyUI Video Upscale Workflow

[English](#english) | [中文](#中文)

## English

[`scale-seedvr2-api.json`](scale-seedvr2-api.json) is the API-format ComfyUI
workflow for [ByteDance SeedVR2](https://iceclear.github.io/projects/seedvr2/)
video upscaling. It is a **standalone upscale channel**, not a video-generation
template: configure it under `🎨 生成模型 -> 超分 -> ComfyUI`, never under the
video tab (MiniMax-H3 / Seedance live there).

Input is one source clip; output is a **target pixel size** (`WIDTH` × `HEIGHT`)
from `--aspect` × `--resolution`, not a scale multiplier. fps, duration, and
the source audio track are preserved.

SeedVR2 is a one-step diffusion restoration model (arXiv:2506.05301), natively
supported in ComfyUI (PR [#14424](https://github.com/Comfy-Org/ComfyUI/pull/14424)).
This template uses the 3B INT8 checkpoint from the official video-upscale
example; fork the JSON to switch to 7B or another quantization.

### Install

Update ComfyUI to a build that exposes `SeedVR2Preprocess` /
`SeedVR2Conditioning` / `SeedVR2PostProcessing` / `ResizeImageMaskNode`, then
download the ComfyUI-format weights from
[Comfy-Org/SeedVR2](https://huggingface.co/Comfy-Org/SeedVR2):

```text
ComfyUI/models/
├── diffusion_models/seedvr2_3b_int8_convrot.safetensors
└── vae/seedvr2_ema_vae_fp16.safetensors
```

Optional higher-quality / other-quantization files live in the same Hugging Face
repo (`seedvr2_7b_*`, `seedvr2_3b_fp16`, …). The VAE is shared across variants.

Official tutorial: <https://docs.comfy.org/tutorials/utility/seedvr2>

### Console Configuration

Select in `🎨 生成模型 -> 超分 -> ComfyUI` (local ComfyUI or a host that has
the SeedVR2 core nodes and weights). The video ComfyUI dropdown does not list
this file (`upscale-` prefix):

```text
comfy/scale-seedvr2-api.json
```

The ComfyUI URL / Cloud Key / RunningHub settings on this tab are independent
of `🎬 视频模型`. Switching the video workflow to MiniMax-H3 does not change
the upscale backend.

**RunningHub**: when the ComfyUI run mode is RunningHub (`rh_cn` / `rh_ai`),
this local JSON is not used — `genmedia.py upscale` submits the cloud
workflow selected on the RunningHub tab (`rh_workflow_id`, e.g. a workspace
copy of a SeedVR2 upscale workflow). Workspace exports usually carry no
`{{TOKEN}}` placeholders (the author's demo file name / default long side are
literals), so the tool binds nodes directly, idempotently on top of any
placeholders that do exist:

| Binding | Node located | Value written |
|---|---|---|
| source video | the single `*LoadVideo*` node (`VHS_LoadVideo.video`, `LoadVideo.file`, …) | uploaded `--input` (RunningHub upload cap 30 MB) |
| target size | the resize node upstream of `SeedVR2Preprocess.resized_images` | `ImageScaleByAspectRatio V2`: `scale_to_length` (or its upstream `Int` primitive) = longest/shortest/width/height side of `WIDTH`×`HEIGHT` per `scale_to_side` — the output keeps the **source** aspect ratio; `ResizeImageMaskNode` / `width`+`height` nodes: both dimensions |
| seed | the primary KSampler-family sampler | `--seed` (warning only if absent) |

VHS meta-batching (`VHS_BatchManager` + the `meta_batch` links on
`VHS_LoadVideo` / `VHS_VideoCombine`) is removed on submission: VHS implements
it by re-queuing the same prompt server-side under new prompt ids, and
RunningHub only tracks the first one — the task reports SUCCESS after the first
batch while the file is only finalized by the last requeue, so the output list
stays empty. The whole clip is processed in one pass instead. Preview-only
`VHS_VideoCombine` nodes (`save_output: false`) are pruned when a saving one
exists, so the cloud does not encode twice and the download picks the real
output. `--dry-run` validates the binding against the cached cloud
workflow without uploading or creating a task.

### Runtime Parameters

`genmedia.py upscale` fills the placeholders. Call:

```text
python3 modules/genmedia.py upscale --input in.mp4 --output out.mp4 \
  --aspect 16:9 --resolution 1080p
```

`--prompt` / `--source-task-id` are MiniMax Regenerate-2K only; SeedVR2 ignores
them. Output length follows the source.

| Workflow token | Source |
|---|---|
| `VIDEO` | uploaded `--input` |
| `WIDTH`, `HEIGHT` | `--aspect` × `--resolution` (defaults to the project's final-cut tier; 4k allowed), aligned to an 8-pixel grid |
| `SEED` | `--seed`, or a random seed |

Fixed inside the JSON (fork the file to change them):

- UNet `seedvr2_3b_int8_convrot.safetensors`, VAE `seedvr2_ema_vae_fp16.safetensors`;
- resize mode `scale dimensions` + `crop: center` + `lanczos`;
- one-step `euler` / `simple` / `cfg 1` sampling;
- tiled VAE encode/decode (`tile_size 512`, `temporal_size 64`);
- post-process color correction `none`;
- output MP4 H.264, audio copied from the source.

For a `4k` target, confirm GPU VRAM headroom first; raise tiled VAE sizes only
after a 1080p smoke test succeeds.

---

## 中文

[`scale-seedvr2-api.json`](scale-seedvr2-api.json) 是
[ByteDance SeedVR2](https://iceclear.github.io/projects/seedvr2/) 视频超分的
API 格式 ComfyUI 工作流。它是**独立超分渠道**,不是视频生成模板:在
`🎨 生成模型 -> 超分 -> ComfyUI` 中配置,不要放到视频标签页(那里是 MiniMax-H3 /
Seedance)。

输入为一段源 clip,输出为 `--aspect` × `--resolution` 换算的**指定像素尺寸**
(`WIDTH` × `HEIGHT`),不是按倍率放大。fps、时长与源音轨保持不变。

SeedVR2 是一步扩散修复模型(arXiv:2506.05301),已原生接入 ComfyUI
(PR [#14424](https://github.com/Comfy-Org/ComfyUI/pull/14424))。本模板采用官方
视频超分示例的 3B INT8 权重;若要换 7B 或其他量化档,请复制 JSON 改 UNet 文件名。

### 安装

先将 ComfyUI 更新到包含 `SeedVR2Preprocess` / `SeedVR2Conditioning` /
`SeedVR2PostProcessing` / `ResizeImageMaskNode` 的版本,再从
[Comfy-Org/SeedVR2](https://huggingface.co/Comfy-Org/SeedVR2) 下载 ComfyUI 格式权重:

```text
ComfyUI/models/
├── diffusion_models/seedvr2_3b_int8_convrot.safetensors
└── vae/seedvr2_ema_vae_fp16.safetensors
```

同仓库还有 7B 与 FP16 等变体,VAE 各档共用。官方教程:
<https://docs.comfy.org/tutorials/utility/seedvr2>

### 控制台配置

在 `🎨 生成模型 -> 超分 -> ComfyUI` 中选择。视频 ComfyUI 下拉不会列出本文件
(`upscale-` 前缀):

```text
comfy/scale-seedvr2-api.json
```

本标签页的 ComfyUI 地址 / Cloud Key / RunningHub 与 `🎬 视频模型` 完全独立;
把视频工作流换成 MiniMax-H3 不会改超分后端。

**RunningHub**:ComfyUI 运行方式为 RunningHub(`rh_cn` / `rh_ai`)时不用本地 JSON,
`genmedia.py upscale` 提交 RunningHub 标签页选中的云端工作流(`rh_workflow_id`,
如工作区保存的 SeedVR2 超分工作流)。工作区导出件通常**没有 `{{TOKEN}}` 占位符**
(源视频是作者演示文件名、长边是模板默认值等字面值),工具改为顺节点直绑(有占位符
时为幂等覆写):

| 绑定项 | 定位节点 | 写入值 |
|---|---|---|
| 源视频 | 唯一的 `*LoadVideo*` 节点(`VHS_LoadVideo.video` / `LoadVideo.file` …) | 上传后的 `--input`(RunningHub 单文件上限 30MB) |
| 目标尺寸 | `SeedVR2Preprocess.resized_images` 上游的缩放节点 | `ImageScaleByAspectRatio V2`:按 `scale_to_side` 把 `WIDTH`×`HEIGHT` 的最长/最短/宽/高边写入 `scale_to_length`(或其上游 `Int` primitive)——输出**画幅跟随源视频**;`ResizeImageMaskNode` / `width`+`height` 类节点:写入宽高 |
| 种子 | 主采样器(KSampler 系) | `--seed`(定位不到只提醒) |

提交时会拆掉 VHS 元批处理(`VHS_BatchManager` 及 `VHS_LoadVideo` / `VHS_VideoCombine`
上的 `meta_batch` 连线):VHS 靠服务端把同一提示词以新 prompt_id 反复重排队分批,
RunningHub 只跟踪首个 prompt——首批跑完就报 SUCCESS 而文件要到末批才落盘,产物清单为空;
改为整段一次处理。有正式落盘节点时,作者并挂的 `save_output: false` 预览
`VHS_VideoCombine` 会被剪除,避免云端重复编码、下载错拿预览件。`--dry-run` 只对缓存的云端工作流做直绑校验,
不上传、不建任务。

### 运行时参数

由 `genmedia.py upscale` 注入占位符:

```text
python3 modules/genmedia.py upscale --input in.mp4 --output out.mp4 \
  --aspect 16:9 --resolution 1080p
```

`--prompt` / `--source-task-id` 仅 MiniMax Regenerate-2K 使用,SeedVR2 忽略。
输出时长跟随源视频。

| 工作流占位符 | 来源 |
|---|---|
| `VIDEO` | 上传的 `--input` |
| `WIDTH`、`HEIGHT` | `--aspect` × `--resolution`(缺省取项目成片档;允许 4k),对齐 8 像素网格 |
| `SEED` | `--seed`,或随机种子 |

JSON 内固定(要改请复制文件):

- UNet `seedvr2_3b_int8_convrot.safetensors`、VAE `seedvr2_ema_vae_fp16.safetensors`;
- 缩放模式 `scale dimensions` + `crop: center` + `lanczos`;
- 一步 `euler` / `simple` / `cfg 1` 采样;
- 分块 VAE 编解码(`tile_size 512`,`temporal_size 64`);
- 后处理校色 `none`;
- 输出 MP4 H.264,音轨从源视频拷贝。

目标为 `4k` 时先确认 GPU 显存余量;请先用 1080p 冒烟测试通过后再加大分块。
