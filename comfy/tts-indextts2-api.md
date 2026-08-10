# IndexTTS-2 ComfyUI TTS Workflow

[English](#english) | [中文](#中文)

## English

[`tts-indextts2-api.json`](tts-indextts2-api.json) is the API-format ComfyUI TTS
workflow for [IndexTTS-2](https://github.com/index-tts/index-tts), used together
with the custom node
[chenpipi0807/ComfyUI-Index-TTS](https://github.com/chenpipi0807/ComfyUI-Index-TTS).
It is used for narration / voiceprint samples, with high-quality Chinese and
English voice cloning.

Placeholders: `TEXT` `REF_AUDIO` `SEED`.

The workflow JSON is empty by default. Only when the TTS channel is switched to
**ComfyUI (local)** on the web console's `🎨 生成模型` page should this workflow
path be filled in.

### 1. Install The Custom Node

```bash
cd <ComfyUI>/custom_nodes
git clone https://github.com/chenpipi0807/ComfyUI-Index-TTS.git
cd ComfyUI-Index-TTS && pip install -r requirements.txt
```

After restarting ComfyUI, the node list should show `IndexTTS2BaseNode` and
friends.

### 2. Model Download

- Main model: [https://huggingface.co/IndexTeam/IndexTTS-2](https://huggingface.co/IndexTeam/IndexTTS-2)
- Semantic codec: [https://huggingface.co/amphion/MaskGCT/tree/main/semantic_codec](https://huggingface.co/amphion/MaskGCT/tree/main/semantic_codec)
- Speaker embedding: [https://huggingface.co/funasr/campplus](https://huggingface.co/funasr/campplus) (`campplus_cn_common.bin`)
- Wav2Vec2Bert: [https://huggingface.co/facebook/w2v-bert-2.0](https://huggingface.co/facebook/w2v-bert-2.0)

The node's bundled one-shot download script is recommended:

```bash
# Optional mirror for mainland China
export HF_ENDPOINT=https://hf-mirror.com
python <ComfyUI>/custom_nodes/ComfyUI-Index-TTS/TTS2_download.py
```

Target directory: `ComfyUI/models/IndexTTS-2/` (containing `gpt.pth`
`s2mel.pth` `bpe.model` `qwen0.6bemo4-merge/` `semantic_codec/`
`w2v-bert-2.0/` etc.; see the node README for details).

### 3. Voice / Reference Audio

IndexTTS-2 is a **reference-audio cloning** model. Unlike cloud TTS, voices are
not selected by voice name:

- Reference audio is no longer entered manually on the config page. ComfyUI TTS
  reads the project character's `voice.json`, `personality.json`, and
  `appearance.json`, hard-filters by gender against the bundled catalog
  `modules/timbre_catalog.json` (which indexes the remote audio library
  [ComfyUI-Index-TTS/TimbreModel](https://github.com/chenpipi0807/ComfyUI-Index-TTS/tree/main/TimbreModel)),
  automatically picks reference audio by age, pitch, and voice-quality tags,
  downloads the selected file on first use into the local cache
  `data/TimbreModel/`, then uploads it as `REF_AUDIO`
- Character voiceprints: call `genmedia tts --character CHAR-0001
  [--variant child]`; if the output filename already contains `CHAR-0001`, the
  character ID can be inferred automatically
- Narration: omit `--character`/`--voice` and describe the style with
  `--instructions`; the module auto-matches among catalog candidates tagged as
  narrator. Selection is deterministic: the same character profile and
  instructions always yield the same voice
- `--voice` is kept only for compatibility, and the ComfyUI channel accepts only
  real local audio files; do not fill it in manually in normal workflows

### 4. Dependency Troubleshooting

If `IndexTTS2BaseNode` reports
`No module named 'transformers.generation.beam_constraints'`, the reference
audio already passed `LoadAudio`; the failure is an incompatible IndexTTS2
dependency in the ComfyUI Python environment. IndexTTS2's official dependencies
are pinned to `transformers==4.52.1` and `tokenizers==0.21.0`. Install the
matching versions with **the same Python interpreter that launches that
ComfyUI** and restart ComfyUI; do not configure reference audio in the
VideoAgents console. After installing, verify in that interpreter first:

```bash
python -c "from transformers.generation.beam_constraints import DisjunctiveConstraint; print('ok')"
```

The official dependency versions are at
<https://github.com/index-tts/index-tts/blob/main/pyproject.toml>. If the same
ComfyUI also runs other model nodes, back up the environment before
upgrading/downgrading shared dependencies, or use a dedicated ComfyUI instance
for IndexTTS2.

### 5. Console Configuration Example

```json
{
  "tts": {
    "provider": "comfyui",
    "comfyui": {
      "url": "http://127.0.0.1:8188",
      "workflow": "comfy/tts-indextts2-api.json",
      "checkpoint": "",
      "voice": "/path/to/narrator_ref.wav"
    }
  }
}
```

### 6. VRAM And Alternatives

| Option | Rough VRAM | Notes |
|---|---|---|
| IndexTTS-2 | ~6–12GB | High-quality Chinese/English cloning, good for narration and voiceprint samples |
| Cloud ElevenLabs / Volcengine / OpenRouter | 0 local VRAM | Stable quality, zero ops; paid and network-dependent |

If you only need lightweight English TTS, you can swap in an F5-TTS / Kokoro
workflow, as long as it ends with a `SaveAudio*` node and its placeholders stay
compatible with the table above.

---

## 中文

[`tts-indextts2-api.json`](tts-indextts2-api.json) 是
[IndexTTS-2](https://github.com/index-tts/index-tts) 的 API 格式 ComfyUI TTS 工作流,
配合自定义节点 [chenpipi0807/ComfyUI-Index-TTS](https://github.com/chenpipi0807/ComfyUI-Index-TTS)
使用,用于旁白 / 声纹样本,中英克隆质量高。

占位符:`TEXT` `REF_AUDIO` `SEED`。

工作流 JSON 默认留空。只有在 Web 控制台「🎨 生成模型」页将 TTS 渠道切换为
**ComfyUI(本地)** 时,才按需填写本工作流路径。

### 1. 安装自定义节点

```bash
cd <ComfyUI>/custom_nodes
git clone https://github.com/chenpipi0807/ComfyUI-Index-TTS.git
cd ComfyUI-Index-TTS && pip install -r requirements.txt
```

重启 ComfyUI 后,节点列表应出现 `IndexTTS2BaseNode` 等。

### 2. 模型下载

- 主模型:[https://huggingface.co/IndexTeam/IndexTTS-2](https://huggingface.co/IndexTeam/IndexTTS-2)
- 语义编码器:[https://huggingface.co/amphion/MaskGCT/tree/main/semantic_codec](https://huggingface.co/amphion/MaskGCT/tree/main/semantic_codec)
- 说话人嵌入:[https://huggingface.co/funasr/campplus](https://huggingface.co/funasr/campplus) (`campplus_cn_common.bin`)
- Wav2Vec2Bert:[https://huggingface.co/facebook/w2v-bert-2.0](https://huggingface.co/facebook/w2v-bert-2.0)

推荐用节点自带脚本一键下载:

```bash
# 国内镜像可选
export HF_ENDPOINT=https://hf-mirror.com
python <ComfyUI>/custom_nodes/ComfyUI-Index-TTS/TTS2_download.py
```

目标目录:`ComfyUI/models/IndexTTS-2/`(含 `gpt.pth` `s2mel.pth` `bpe.model` `qwen0.6bemo4-merge/` `semantic_codec/` `w2v-bert-2.0/` 等,详见节点 README)。

### 3. 音色 / 参考音频

IndexTTS-2 是**参考音频克隆**模型,不像云端 TTS 用音色名选声线:

- 不再在配置页手填参考音频。ComfyUI TTS 会读取项目角色的 `voice.json`、
  `personality.json`、`appearance.json`,按内置音色目录 `modules/timbre_catalog.json`
  (索引远端 [ComfyUI-Index-TTS/TimbreModel](https://github.com/chenpipi0807/ComfyUI-Index-TTS/tree/main/TimbreModel)
  音频库)性别硬过滤并按年龄、音高、声线标签自动选取参考音频,首次使用自动下载缓存到
  `data/TimbreModel/`,然后上传为 `REF_AUDIO`
- 角色 voiceprint:调用 `genmedia tts --character CHAR-0001 [--variant child]`;若输出文件名
  已含 `CHAR-0001`,可自动推断角色 ID
- 旁白:不传 `--character`/`--voice`,用 `--instructions` 描述风格;模块从 catalog 中标为
  narrator 的候选自动匹配。选择是确定性的,相同角色设定与指令会得到同一音色
- `--voice` 仅保留兼容用途,且 ComfyUI 渠道只接受真实存在的本地音频文件;正常工作流禁止手填

### 4. 依赖故障

若 `IndexTTS2BaseNode` 报 `No module named 'transformers.generation.beam_constraints'`,说明参考音频已经通过
`LoadAudio`,失败点是 ComfyUI Python 环境的 IndexTTS2 依赖不兼容。IndexTTS2 官方依赖固定为
`transformers==4.52.1` 和 `tokenizers==0.21.0`;应使用**启动该 ComfyUI 的同一个 Python 解释器**安装对应版本并重启
ComfyUI,不要在 VideoAgents 控制台配置参考音频。安装后先在该解释器验证:

```bash
python -c "from transformers.generation.beam_constraints import DisjunctiveConstraint; print('ok')"
```

官方依赖版本见 <https://github.com/index-tts/index-tts/blob/main/pyproject.toml>。ComfyUI 还运行其他模型节点时,
升级/降级共享环境依赖前应先备份环境或为 IndexTTS2 使用独立 ComfyUI 实例。

### 5. 控制台配置示例

```json
{
  "tts": {
    "provider": "comfyui",
    "comfyui": {
      "url": "http://127.0.0.1:8188",
      "workflow": "comfy/tts-indextts2-api.json",
      "checkpoint": "",
      "voice": "/path/to/narrator_ref.wav"
    }
  }
}
```

### 6. 显存与替代方案

| 方案 | 显存粗估 | 说明 |
|---|---|---|
| IndexTTS-2 | ~6–12GB | 中英克隆质量高,适合旁白与声纹样本 |
| 云端 ElevenLabs / 火山 / OpenRouter | 0 本地显存 | 质量稳、零运维;付费与网络依赖 |

若只要轻量英文 TTS,也可自行换 F5-TTS / Kokoro 等工作流,只要最终有 `SaveAudio*` 节点且占位符与上表兼容即可。
