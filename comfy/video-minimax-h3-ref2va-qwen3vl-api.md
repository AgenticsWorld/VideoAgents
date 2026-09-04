# MiniMax H3 Ref2VA (Qwen3-VL Heretic Text Encoder)

[English](#english) | [中文](#中文)

## English

[`video-minimax-h3-ref2va-qwen3vl-api.json`](video-minimax-h3-ref2va-qwen3vl-api.json)
is a variant of
[`video-minimax-h3-ref2va-api.json`](video-minimax-h3-ref2va-api.json) with a
single difference: the `CLIPLoader` text encoder is pinned to the literal file
`qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors` instead of the
`{{H3_TEXT_ENCODER}}` placeholder (which resolves to the built-in default
`qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors`).

Install the encoder file at:

```text
ComfyUI/models/text_encoders/qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors
```

`genmedia.py` detects literal (non-placeholder) model filenames in the selected
workflow and uses them for both the component preflight and execution, so this
template validates against the heretic encoder rather than the default one.

Everything else — UNet/VAE components, sampling settings, runtime parameters,
reference contract, and host preflight/failure-recovery guidance — is identical
to the base workflow; see
[`video-minimax-h3-ref2va-api.md`](video-minimax-h3-ref2va-api.md).

### Console Configuration

```text
comfy/video-minimax-h3-ref2va-qwen3vl-api.json
```

---

## 中文

[`video-minimax-h3-ref2va-qwen3vl-api.json`](video-minimax-h3-ref2va-qwen3vl-api.json)
是 [`video-minimax-h3-ref2va-api.json`](video-minimax-h3-ref2va-api.json) 的变体,
唯一差异:`CLIPLoader` 文本编码器写死为
`qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors`,不再使用
`{{H3_TEXT_ENCODER}}` 占位符(占位符会解析为内置默认的
`qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors`)。

编码器文件安装到:

```text
ComfyUI/models/text_encoders/qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors
```

`genmedia.py` 会识别所选工作流中写死(非占位符)的模型文件名,并在组件预检与执行时
一并采用,因此本模板按 heretic 编码器做预检,而不是默认编码器。

其余内容——UNet/VAE 组件、采样设置、运行时参数、参考输入约定、主机预检与故障
恢复——与基础工作流完全一致,见
[`video-minimax-h3-ref2va-api.md`](video-minimax-h3-ref2va-api.md)。

### 控制台配置

```text
comfy/video-minimax-h3-ref2va-qwen3vl-api.json
```
