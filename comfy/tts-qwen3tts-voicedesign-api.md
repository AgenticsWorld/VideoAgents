# Qwen3-TTS Voice Design ComfyUI Workflow

[English](#english) | [中文](#中文)

## English

[`tts-qwen3tts-voicedesign-api.json`](tts-qwen3tts-voicedesign-api.json) is
the **Voice Design** workflow for
[Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) (1.7B VoiceDesign,
Apache 2.0): a *text description of the voice* plus the text to speak produce
a voice sample — no reference audio. It is the companion of
[`tts-qwen3tts-clone-api.md`](tts-qwen3tts-clone-api.md).

Placeholders: `TEXT` `VOICE_DESC` `SEED`.

### Install

Custom node pack
[flybirdxx/ComfyUI-Qwen-TTS](https://github.com/flybirdxx/ComfyUI-Qwen-TTS)
(node `FB_Qwen3TTSVoiceDesign`); the model
(`Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign`) is downloaded on first run.

### Console Configuration

This workflow goes into the TTS channel's **Voice Design** slot
(`design_workflow`), next to the Voice Clone workflow — see the JSON snippet
in [`tts-qwen3tts-clone-api.md`](tts-qwen3tts-clone-api.md). It is never
selected as the main (`workflow`) slot.

### Runtime Parameters

`genmedia.py tts` uses this workflow only when the output file is a voice
sample (`*_voiceprint.*`). `VOICE_DESC` is assembled automatically: for a
character, from the acoustic fields of `bible/characters/<CHAR>/voice.json`
(gender, pitch, timbre, accent; an age variant overrides per field); for the
narrator, from the narrator voice card, else `--instructions`, else a built-in
description. Passing a description as `--voice` overrides it.

The same description yields a slightly different voice on every run. Freeze
the sample once and reuse it: all later lines are cloned from it by the Voice
Clone workflow. Regenerating a sample is a controlled change.

---

## 中文

[`tts-qwen3tts-voicedesign-api.json`](tts-qwen3tts-voicedesign-api.json) 是
[Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS)(1.7B VoiceDesign,Apache 2.0)的 **Voice Design** 工作流:
*嗓音的文字描述* + 要说的文字 → 嗓音样本,不需要参考音频。与
[`tts-qwen3tts-clone-api.md`](tts-qwen3tts-clone-api.md) 配套使用。

占位符:`TEXT` `VOICE_DESC` `SEED`。

### 安装

自定义节点包 [flybirdxx/ComfyUI-Qwen-TTS](https://github.com/flybirdxx/ComfyUI-Qwen-TTS)
(节点 `FB_Qwen3TTSVoiceDesign`);模型(`Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign`)首次运行自动下载。

### 控制台配置

本工作流填在 TTS 渠道的 **Voice Design** 位(`design_workflow`),与 Voice Clone 工作流并列——配置片段见
[`tts-qwen3tts-clone-api.md`](tts-qwen3tts-clone-api.md)。不要选到主工作流(`workflow`)位。

### 运行时参数

`genmedia.py tts` 只在输出是嗓音样本(`*_voiceprint.*`)时用本工作流。`VOICE_DESC` 自动拼装:
角色取 `bible/characters/<CHAR>/voice.json` 的声学字段(性别、音高、音色、口音;年龄形态逐字段覆盖);
旁白取旁白声线卡,其次 `--instructions`,再次内置描述。`--voice` 传描述文本可覆盖。

同一描述每次生成的音色都会略有不同。样本一次冻结、此后复用:后续台词一律由 Voice Clone 工作流按该样本克隆;
重出样本属受控变更。
