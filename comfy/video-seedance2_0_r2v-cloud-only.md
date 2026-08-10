# Seedance 2.0 Reference-to-Video (Comfy Cloud only)

[English](#english) | [中文](#中文)

## English

[`video-seedance2_0_r2v-cloud-only.json`](video-seedance2_0_r2v-cloud-only.json)
is the API-format ComfyUI workflow for ByteDance **Seedance 2.0**
reference-to-video, built on the `ByteDance2ReferenceNode` API node. The node
calls the hosted Seedance service, so this workflow runs **only on Comfy Cloud**
(billed per generation on the Comfy Cloud account); it cannot run on a local
ComfyUI without the paid API-node backend. Because generation happens on the
provider side, there is nothing to install and no model-file placeholder: the
`model` input is the fixed literal `Seedance 2.0`.

### Console Configuration

Select in `🎨 生成模型 -> 视频 -> ComfyUI` with 运行方式 = Comfy Cloud and a
configured Comfy Cloud API Key:

```text
comfy/video-seedance2_0_r2v-cloud-only.json
```

### Template Placeholders

Fixed inputs: `model` (`Seedance 2.0`), `model.auto_downscale` (true),
`model.auto_upscale` (false), `watermark` (false). Everything per-request is a
`{{TOKEN}}` placeholder:

| Workflow token | Value |
|---|---|
| `PROMPT` | generation prompt (official Shot 1:/Shot 2: multi-shot structure supported) |
| `RESOLUTION` | `480p` / `720p` / `1080p` (Ark Seedance 2.0 tiering) |
| `RATIO` | output ratio, e.g. `16:9` / `1:1` / `adaptive` (follow references) |
| `DURATION` | integer seconds in [4,15], or `-1` for model-decided length |
| `GENERATE_AUDIO` | boolean, native synchronized audio on/off |
| `SEED` | integer seed (top-level `seed` input) |

Reference images are intentionally **not** static `LoadImage` nodes in the
template. Like the MiniMax-H3 template, `genmedia.py` attaches them at
execution time: each `--ref` is uploaded to ComfyUI, gets its own `LoadImage`
node, and is wired to `model.reference_images.image_N` (1-based, up to 9 for
Seedance 2.0).

### Integration Status

Wired into `genmedia.py` via a dedicated `ByteDance2ReferenceNode` branch
(detected by `class_type`, modeled on the MiniMax-H3 one); **not yet tested
against Comfy Cloud**. The branch maps `--resolution/--aspect/--duration/
--generate-audio/--seed` onto the node inputs and validates before submitting:
duration must be an integer in [4,15] or `-1`; out-of-tier resolutions are
clamped (`360p`→`480p`, `4k`→`1080p`) with a warning; unknown ratios fall back
to `adaptive`; `--generate-audio` defaults to on. `--first-frame`/
`--last-frame`/`--ref-video`/`--audio-ref` are rejected (r2v template; the
cloud node exposes no audio-reference socket), as is running with a local
ComfyUI (运行方式 must be Comfy Cloud; RunningHub-hosted copies of the workflow
are passed through). `--return-last-frame` extracts the final frame from the
downloaded MP4, same as H3.

The raw Comfy Cloud export this template was distilled from had misaligned
widget values (`model.auto_downscale: 1077699876`,
`model.auto_upscale: "randomize"`, `seed: false`); they were normalized to
their semantically correct types (`true` / `false` / `{{SEED}}`).

This template is reference-to-video only. `model.video_editing` /
`model.output_format` are Seedance 2.5 inputs and are absent here; V2V
editing/extension and first/last-frame tasks need their own workflow.

---

## 中文

[`video-seedance2_0_r2v-cloud-only.json`](video-seedance2_0_r2v-cloud-only.json)
是 ByteDance **Seedance 2.0** 参考图生视频(r2v)的 API 格式 ComfyUI 工作流,
基于 `ByteDance2ReferenceNode` API 节点。该节点调用官方托管的 Seedance 服务,
因此**仅能在 Comfy Cloud 运行**(按 Comfy Cloud 账号计费),本地 ComfyUI 没有
付费 API 节点后端,无法执行。生成发生在服务端,无需安装任何模型文件,也没有
模型占位符:`model` 输入固定为字面量 `Seedance 2.0`。

### 控制台配置

在 `🎨 生成模型 -> 视频 -> ComfyUI` 中选择,运行方式 = Comfy Cloud,并配置好
Comfy Cloud API Key:

```text
comfy/video-seedance2_0_r2v-cloud-only.json
```

### 模板占位符

固定输入:`model`(`Seedance 2.0`)、`model.auto_downscale`(true)、
`model.auto_upscale`(false)、`watermark`(false)。逐次请求的参数均为
`{{TOKEN}}` 占位符:

| 工作流占位符 | 取值 |
|---|---|
| `PROMPT` | 生成提示词(支持官方 Shot 1:/Shot 2: 分镜结构) |
| `RESOLUTION` | `480p` / `720p` / `1080p`(与方舟 Seedance 2.0 档位同口径) |
| `RATIO` | 输出比例,如 `16:9` / `1:1` / `adaptive`(跟随参考图) |
| `DURATION` | 整数秒 [4,15],或 `-1`(模型自定时长) |
| `GENERATE_AUDIO` | 布尔,原生音画同步开关 |
| `SEED` | 整数种子(顶层 `seed` 输入) |

模板刻意**不含**静态 `LoadImage` 节点:与 MiniMax-H3 模板同款约定,
`genmedia.py` 在执行时动态挂载——把每张 `--ref` 上传到 ComfyUI,逐张新建
`LoadImage` 节点并接到 `model.reference_images.image_N`(从 1 起编号,
Seedance 2.0 最多 9 张)。

### 接入状态

已通过 `ByteDance2ReferenceNode` 专用分支接入 `genmedia.py`(按 `class_type`
判定,参照 MiniMax-H3 分支实现);**尚未在 Comfy Cloud 实测**。分支把
`--resolution/--aspect/--duration/--generate-audio/--seed` 映射到节点输入,并在
提交前校验:时长须为 [4,15] 整数或 `-1`;越档分辨率带警告压档(`360p`→`480p`、
`4k`→`1080p`);未知比例回落 `adaptive`;`--generate-audio` 缺省为开。
`--first-frame`/`--last-frame`/`--ref-video`/`--audio-ref` 一律拒绝(r2v 模板;
云节点未暴露参考音频接口),本地 ComfyUI 运行也拒绝(运行方式须为 Comfy Cloud;
RunningHub 托管的同款工作流放行)。`--return-last-frame` 与 H3 一样从下载的
MP4 抽取尾帧。

本模板蒸馏自 Comfy Cloud 原始导出,导出件的控件值存在错位
(`model.auto_downscale: 1077699876`、`model.auto_upscale: "randomize"`、
`seed: false`),已按语义修正为正确类型(`true` / `false` / `{{SEED}}`)。

本模板仅用于参考图生视频。`model.video_editing` / `model.output_format` 是
Seedance 2.5 的输入,此处不存在;V2V 编辑/延长与首尾帧任务请另建对应工作流。
