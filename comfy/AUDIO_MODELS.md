# ComfyUI 音乐 / TTS 模型与工作流

图像/视频之外,音乐与 TTS 也可走本地 ComfyUI。推荐组合:

- **音乐 BGM**:[ACE-Step v1 3.5B](https://github.com/ace-step/ACE-Step) + 自定义节点 [billwuhao/ComfyUI_ACE-Step](https://github.com/billwuhao/ComfyUI_ACE-Step)
- **TTS 旁白 / 声纹样本**:[IndexTTS-2](https://github.com/index-tts/index-tts) + 自定义节点 [chenpipi0807/ComfyUI-Index-TTS](https://github.com/chenpipi0807/ComfyUI-Index-TTS)

对应 API 工作流:

| 用途 | 工作流 | 占位符 |
|---|---|---|
| 音乐 | [`ace-step-v1-music-api.json`](ace-step-v1-music-api.json) | `PROMPT` `LYRICS` `DURATION` `SEED` |
| TTS | [`indextts2-tts-api.json`](indextts2-tts-api.json) | `TEXT` `REF_AUDIO` `SEED` |

工作流 JSON 默认留空。只有在 Web 控制台「🎨 生成模型」页将音乐/TTS 渠道切换为
**ComfyUI(本地)** 时，才按需填写上表中的工作流路径。

## 1. 安装自定义节点

```bash
cd <ComfyUI>/custom_nodes

# 音乐
git clone https://github.com/billwuhao/ComfyUI_ACE-Step.git
cd ComfyUI_ACE-Step && pip install -r requirements.txt && cd ..

# TTS
git clone https://github.com/chenpipi0807/ComfyUI-Index-TTS.git
cd ComfyUI-Index-TTS && pip install -r requirements.txt && cd ..
```

重启 ComfyUI 后,节点列表应出现 `ACEModelLoader` / `ACEStepGen` / `IndexTTS2BaseNode` 等。

## 2. 音乐模型下载(ACE-Step)

- 模型页:[https://huggingface.co/ACE-Step/ACE-Step-v1-3.5B](https://huggingface.co/ACE-Step/ACE-Step-v1-3.5B)
- 可选中文说唱 LoRA:[https://huggingface.co/ACE-Step/ACE-Step-v1-chinese-rap-LoRA](https://huggingface.co/ACE-Step/ACE-Step-v1-chinese-rap-LoRA)

放置目录(`ComfyUI/models/TTS/ACE-Step-v1-3.5B/`):

```text
ACE-Step-v1-3.5B/
├─ ace_step_transformer/
│    config.json
│    diffusion_pytorch_model.safetensors
├─ music_dcae_f8c8/
│    config.json
│    diffusion_pytorch_model.safetensors
├─ music_vocoder/
│    config.json
│    diffusion_pytorch_model.safetensors
└─ umt5-base/
     config.json
     model.safetensors
     special_tokens_map.json
     tokenizer.json
     tokenizer_config.json
```

一键拉取示例:

```bash
# 需要 huggingface-cli;国内可先 export HF_ENDPOINT=https://hf-mirror.com
huggingface-cli download ACE-Step/ACE-Step-v1-3.5B \
  --local-dir <ComfyUI>/models/TTS/ACE-Step-v1-3.5B
```

### Windows Comfy Desktop 共享目录注意事项

`ComfyUI_ACE-Step` 当前节点按 `folder_paths.models_dir\TTS\ACE-Step-v1-3.5B` 查找，
不会自动跟随 HuggingFace cache，也不一定会读取 `shared_model_paths.yaml` 中的自定义键。
如果 Comfy Desktop 的安装目录与共享模型目录分离，必须把共享目录映射到**报错中显示的安装目录**。
本次已确认的报错路径是：

```text
E:\Comfy-Desktop\ComfyUI-Installs\Test\ComfyUI\models\TTS\ACE-Step-v1-3.5B
```

例如模型实际位于 `E:\Comfy-Desktop\ComfyUI-Shared\models\TTS\ACE-Step-v1-3.5B`，
请在管理员 PowerShell 中执行（目标 `models\TTS` 不存在时先创建父目录）：

```powershell
$install = 'E:\Comfy-Desktop\ComfyUI-Installs\Test\ComfyUI'
$shared = 'E:\Comfy-Desktop\ComfyUI-Shared\models\TTS'
New-Item -ItemType Directory -Force "$install\models" | Out-Null
New-Item -ItemType Junction -Path "$install\models\TTS" -Target $shared
```

然后确认以下四个子目录直接位于 `models\TTS\ACE-Step-v1-3.5B\` 下（不要多套一层
`ACE-Step-v1-3.5B\ACE-Step-v1-3.5B`），重启 ComfyUI，再在「🎨 生成模型」页测试连接。
测试结果应显示 `ACEModelLoader` 与 `ACEStepGen` 可见；若节点不可见，先重装/更新
`ComfyUI_ACE-Step` 自定义节点。

BGM 默认歌词占位为 `[Instrumental]`(纯音乐)。若要人声歌曲,可在工作流里改 `LYRICS` 默认,或自行 fork 一份带固定歌词的 API JSON。

## 3. TTS 模型下载(IndexTTS-2)

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

### 音色 / 参考音频

IndexTTS-2 是**参考音频克隆**模型,不像云端 TTS 用音色名选声线:

- 不再在配置页手填参考音频。ComfyUI TTS 会读取项目角色的 `voice.json`、
  `personality.json`、`appearance.json`,从 `data/TimbreModel/catalog.json` 性别硬过滤并按
  年龄、音高、声线标签自动选取参考音频,然后上传为 `REF_AUDIO`
- 角色 voiceprint:调用 `genmedia tts --character CHAR-0001 [--variant child]`;若输出文件名
  已含 `CHAR-0001`,可自动推断角色 ID
- 旁白:不传 `--character`/`--voice`,用 `--instructions` 描述风格;模块从 catalog 中标为
  narrator 的候选自动匹配。选择是确定性的,相同角色设定与指令会得到同一音色
- `--voice` 仅保留兼容用途,且 ComfyUI 渠道只接受真实存在的本地音频文件;正常工作流禁止手填

### IndexTTS2 依赖故障

若 `IndexTTS2BaseNode` 报 `No module named 'transformers.generation.beam_constraints'`,说明参考音频已经通过
`LoadAudio`,失败点是 ComfyUI Python 环境的 IndexTTS2 依赖不兼容。IndexTTS2 官方依赖固定为
`transformers==4.52.1` 和 `tokenizers==0.21.0`;应使用**启动该 ComfyUI 的同一个 Python 解释器**安装对应版本并重启
ComfyUI,不要在 VideoAgents 控制台配置参考音频。安装后先在该解释器验证:

```bash
python -c "from transformers.generation.beam_constraints import DisjunctiveConstraint; print('ok')"
```

官方依赖版本见 <https://github.com/index-tts/index-tts/blob/main/pyproject.toml>。ComfyUI 还运行其他模型节点时,
升级/降级共享环境依赖前应先备份环境或为 IndexTTS2 使用独立 ComfyUI 实例。

## 4. 控制台配置示例

```json
{
  "music": {
    "provider": "comfyui",
    "comfyui": {
      "url": "http://127.0.0.1:8188",
      "workflow": "comfy/ace-step-v1-music-api.json",
      "checkpoint": ""
    }
  },
  "tts": {
    "provider": "comfyui",
    "comfyui": {
      "url": "http://127.0.0.1:8188",
      "workflow": "comfy/indextts2-tts-api.json",
      "checkpoint": "",
      "voice": "/path/to/narrator_ref.wav"
    }
  }
}
```

## 5. 显存与替代方案

| 方案 | 显存粗估 | 说明 |
|---|---|---|
| ACE-Step 3.5B | ~8GB+(可 cpu_offload) | BGM / 主题曲,可指定时长 |
| IndexTTS-2 | ~6–12GB | 中英克隆质量高,适合旁白与声纹样本 |
| 云端 ElevenLabs / 火山 / OpenRouter | 0 本地显存 | 质量稳、零运维;付费与网络依赖 |

若只要轻量英文 TTS,也可自行换 F5-TTS / Kokoro 等工作流,只要最终有 `SaveAudio*` 节点且占位符与上表兼容即可。
