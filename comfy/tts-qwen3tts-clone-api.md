# Qwen3-TTS Voice Clone ComfyUI Workflow

[English](#english) | [中文](#中文)

## English

[`tts-qwen3tts-clone-api.json`](tts-qwen3tts-clone-api.json) is the
**Voice Clone** workflow for
[Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) (1.7B Base, Apache 2.0): a
reference voice sample plus the dialogue text produce the spoken line. Its
companion for designing the voice sample from a text description is
[`tts-qwen3tts-voicedesign-api.md`](tts-qwen3tts-voicedesign-api.md).

Placeholders: `TEXT` `REF_AUDIO` `SEED`.

### Install

Install the custom node pack
[flybirdxx/ComfyUI-Qwen-TTS](https://github.com/flybirdxx/ComfyUI-Qwen-TTS)
(node `FB_Qwen3TTSVoiceClone`); the model
(`Qwen/Qwen3-TTS-12Hz-1.7B-Base`) is downloaded on first run. On Comfy Cloud
the node pack must be available in your workspace.

### Console Configuration

`🎨 生成模型` › **TTS › ComfyUI**, run mode **local** or **Comfy Cloud**:

```json
{
  "tts": {
    "provider": "comfyui",
    "comfyui": {
      "workflow": "comfy/tts-qwen3tts-clone-api.json",
      "design_workflow": "comfy/tts-qwen3tts-voicedesign-api.json"
    }
  }
}
```

`workflow` is the **Voice Clone** slot, `design_workflow` the **Voice Design**
slot. With both set, `genmedia.py tts` chooses by purpose: an output named
`*_voiceprint.*` (a character / narrator voice sample) uses Voice Design;
every other line uses this workflow, with the project's frozen sample
`assets/audio/voice/refs/<CHAR>[_<variant>]_voiceprint.mp3` uploaded as
`REF_AUDIO` (falling back to the TimbreModel auto-pick when the project has no
sample yet).

### Runtime Parameters

- `x_vector_only` is **on**: only the speaker embedding is extracted, so no
  transcript of the reference audio is needed (VideoAgents has no channel for
  one). For closer timbre / prosody, fork the JSON, put the sample's exact
  transcript in `ref_text` and switch `x_vector_only` off.
- Sampling uses the node defaults (`top_p 1.0`, `top_k 50`,
  `temperature 0.9`, `repetition_penalty 1.05`); `language` is `Auto`.
- There is no speed / duration input: `--speed` is not applied. Newer versions
  of the node pack add an optional `instruct` input (tone / emotion) that this
  template leaves at its default.

---

## 中文

[`tts-qwen3tts-clone-api.json`](tts-qwen3tts-clone-api.json) 是
[Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS)(1.7B Base,Apache 2.0)的 **Voice Clone** 工作流:
嗓音样本 + 对白文字 → 对白语音。按嗓音文字描述设计样本的配套工作流见
[`tts-qwen3tts-voicedesign-api.md`](tts-qwen3tts-voicedesign-api.md)。

占位符:`TEXT` `REF_AUDIO` `SEED`。

### 安装

安装自定义节点包 [flybirdxx/ComfyUI-Qwen-TTS](https://github.com/flybirdxx/ComfyUI-Qwen-TTS)
(节点 `FB_Qwen3TTSVoiceClone`);模型(`Qwen/Qwen3-TTS-12Hz-1.7B-Base`)首次运行自动下载。
Comfy Cloud 需工作区里有该节点包。

### 控制台配置

`🎨 生成模型` › **TTS › ComfyUI**,运行方式**本地**或 **Comfy Cloud**:

```json
{
  "tts": {
    "provider": "comfyui",
    "comfyui": {
      "workflow": "comfy/tts-qwen3tts-clone-api.json",
      "design_workflow": "comfy/tts-qwen3tts-voicedesign-api.json"
    }
  }
}
```

`workflow` 是 **Voice Clone** 位,`design_workflow` 是 **Voice Design** 位。两个都配时
`genmedia.py tts` 按用途自动选:输出文件名是 `*_voiceprint.*`(出角色/旁白嗓音样本)走 Voice Design;
其余台词走本工作流,参考音频自动取项目冻结样本
`assets/audio/voice/refs/<CHAR>[_<variant>]_voiceprint.mp3` 上传为 `REF_AUDIO`
(项目还没有样本时回退 TimbreModel 自动选型)。

### 运行时参数

- `x_vector_only` **默认开**:只提取说话人嵌入,不需要参考音频的逐字稿(VideoAgents 没有传逐字稿的通道)。
  想要更贴的音色/韵律:fork 本 JSON,在 `ref_text` 填样本的逐字稿并关掉 `x_vector_only`。
- 采样取节点默认值(`top_p 1.0`、`top_k 50`、`temperature 0.9`、`repetition_penalty 1.05`),`language` 为 `Auto`。
- 没有语速/时长输入位,`--speed` 不生效。节点包新版多了可选的 `instruct`(语气/情绪)输入,本模板留默认。
