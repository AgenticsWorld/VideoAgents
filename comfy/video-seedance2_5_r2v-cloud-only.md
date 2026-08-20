# Seedance 2.5 Reference-to-Video (Comfy Cloud only)

[English](#english) | [中文](#中文)

## English

[`video-seedance2_5_r2v-cloud-only.json`](video-seedance2_5_r2v-cloud-only.json)
is the API-format ComfyUI workflow for ByteDance **Seedance 2.5**
reference-to-video, built on the same `ByteDance2ReferenceNode` API node as the
[Seedance 2.0 template](video-seedance2_0_r2v-cloud-only.md) (the `model` input
selects the version). The node calls the hosted Seedance service, so this
workflow runs **only on Comfy Cloud** (billed per generation on the Comfy Cloud
account). Nothing to install, no model-file placeholder: `model` is the fixed
literal `Seedance 2.5`.

### Console Configuration

Select in `🎨 生成模型 -> 视频 -> ComfyUI` with 运行方式 = Comfy Cloud and a
configured Comfy Cloud API Key:

```text
comfy/video-seedance2_5_r2v-cloud-only.json
```

### Template Placeholders

Fixed inputs: `model` (`Seedance 2.5`), `model.video_editing` (false — this
template is reference-to-video; editing/extension tasks need their own
workflow), `model.output_format` (`mp4`), `model.auto_downscale` (true),
`model.auto_upscale` (false), `watermark` (false). Everything per-request is a
`{{TOKEN}}` placeholder:

| Workflow token | Value |
|---|---|
| `PROMPT` | generation prompt (use the sd25-pe prompt conventions for 2.5) |
| `RESOLUTION` | `480p` / `720p` only (Seedance 2.5 hard limit) |
| `RATIO` | output ratio, e.g. `16:9` / `adaptive` (follow references) |
| `DURATION` | integer seconds in [4,30], or `-1` for model-decided length |
| `GENERATE_AUDIO` | boolean, native synchronized audio on/off |
| `SEED` | integer seed (top-level `seed` input) |

Reference images are intentionally **not** static `LoadImage` nodes in the
template. Like the MiniMax-H3 template, `genmedia.py` attaches them at
execution time: each `--ref` is uploaded to ComfyUI, gets its own `LoadImage`
node, and is wired to `model.reference_images.image_N` (1-based, up to 30 for
Seedance 2.5).

### Integration Status

Wired into `genmedia.py` via the dedicated `ByteDance2ReferenceNode` branch —
same behavior as the [Seedance 2.0 template](video-seedance2_0_r2v-cloud-only.md),
with 2.5 tiering: duration integer in [4,30] or `-1`, resolution clamped to
`480p`/`720p`, up to 30 reference images. **Not yet tested against Comfy
Cloud.** `--first-frame`/`--last-frame`/`--ref-video`/`--audio-ref` are
rejected (r2v template; no audio-reference socket on the cloud node), and 运行
方式 must be Comfy Cloud.

---

## 中文

[`video-seedance2_5_r2v-cloud-only.json`](video-seedance2_5_r2v-cloud-only.json)
是 ByteDance **Seedance 2.5** 参考图生视频(r2v)的 API 格式 ComfyUI 工作流,
与 [Seedance 2.0 模板](video-seedance2_0_r2v-cloud-only.md) 同用
`ByteDance2ReferenceNode` API 节点(`model` 输入选版本)。该节点调用官方托管的
Seedance 服务,因此**仅能在 Comfy Cloud 运行**(按 Comfy Cloud 账号计费)。
无需安装任何模型文件,也没有模型占位符:`model` 固定为字面量 `Seedance 2.5`。

### 控制台配置

在 `🎨 生成模型 -> 视频 -> ComfyUI` 中选择,运行方式 = Comfy Cloud,并配置好
Comfy Cloud API Key:

```text
comfy/video-seedance2_5_r2v-cloud-only.json
```

### 模板占位符

固定输入:`model`(`Seedance 2.5`)、`model.video_editing`(false——本模板仅
参考图生视频,编辑/延长任务请另建工作流)、`model.output_format`(`mp4`)、
`model.auto_downscale`(true)、`model.auto_upscale`(false)、`watermark`
(false)。逐次请求的参数均为 `{{TOKEN}}` 占位符:

| 工作流占位符 | 取值 |
|---|---|
| `PROMPT` | 生成提示词(2.5 建议按 sd25-pe 提示词规范撰写) |
| `RESOLUTION` | 仅 `480p` / `720p`(Seedance 2.5 硬限) |
| `RATIO` | 输出比例,如 `16:9` / `adaptive`(跟随参考图) |
| `DURATION` | 整数秒 [4,30],或 `-1`(模型自定时长) |
| `GENERATE_AUDIO` | 布尔,原生音画同步开关 |
| `SEED` | 整数种子(顶层 `seed` 输入) |

模板刻意**不含**静态 `LoadImage` 节点:与 MiniMax-H3 模板同款约定,
`genmedia.py` 在执行时动态挂载——把每张 `--ref` 上传到 ComfyUI,逐张新建
`LoadImage` 节点并接到 `model.reference_images.image_N`(从 1 起编号,
Seedance 2.5 最多 30 张)。

### 接入状态

已通过 `ByteDance2ReferenceNode` 专用分支接入 `genmedia.py`,行为与
[Seedance 2.0 模板](video-seedance2_0_r2v-cloud-only.md)一致,按 2.5 口径放宽/
收紧:时长 [4,30] 整数或 `-1`、分辨率仅 `480p`/`720p`(越档压 `720p`)、参考图
最多 30 张。**尚未在 Comfy Cloud 实测。**`--first-frame`/`--last-frame`/
`--ref-video`/`--audio-ref` 一律拒绝(r2v 模板;云节点未暴露参考音频接口),
运行方式须为 Comfy Cloud。
