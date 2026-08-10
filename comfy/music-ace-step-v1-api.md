# ACE-Step v1 ComfyUI Music Workflow

[English](#english) | [中文](#中文)

## English

[`music-ace-step-v1-api.json`](music-ace-step-v1-api.json) is the API-format
ComfyUI music workflow for [ACE-Step v1 3.5B](https://github.com/ace-step/ACE-Step),
used together with the custom node
[billwuhao/ComfyUI_ACE-Step](https://github.com/billwuhao/ComfyUI_ACE-Step).
It generates BGM / theme songs with a controllable duration.

Placeholders: `PROMPT` `LYRICS` `DURATION` `SEED`.

The workflow JSON is empty by default. Only when the music channel is switched to
**ComfyUI (local)** on the web console's `🎨 生成模型` page should this workflow
path be filled in.

### 1. Install The Custom Node

```bash
cd <ComfyUI>/custom_nodes
git clone https://github.com/billwuhao/ComfyUI_ACE-Step.git
cd ComfyUI_ACE-Step && pip install -r requirements.txt
```

After restarting ComfyUI, the node list should show `ACEModelLoader` /
`ACEStepGen` and friends.

### 2. Model Download

- Model page: [https://huggingface.co/ACE-Step/ACE-Step-v1-3.5B](https://huggingface.co/ACE-Step/ACE-Step-v1-3.5B)
- Optional Chinese-rap LoRA: [https://huggingface.co/ACE-Step/ACE-Step-v1-chinese-rap-LoRA](https://huggingface.co/ACE-Step/ACE-Step-v1-chinese-rap-LoRA)

Target layout (`ComfyUI/models/TTS/ACE-Step-v1-3.5B/`):

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

One-shot pull example:

```bash
# Requires huggingface-cli; in mainland China, export HF_ENDPOINT=https://hf-mirror.com first
huggingface-cli download ACE-Step/ACE-Step-v1-3.5B \
  --local-dir <ComfyUI>/models/TTS/ACE-Step-v1-3.5B
```

#### Windows Comfy Desktop Shared-Directory Caveat

The current `ComfyUI_ACE-Step` node looks up
`folder_paths.models_dir\TTS\ACE-Step-v1-3.5B`. It does not follow the
HuggingFace cache automatically, and it may not read custom keys in
`shared_model_paths.yaml`. If the Comfy Desktop install directory is separate
from the shared model directory, you must map the shared directory onto **the
install directory shown in the error message**. The confirmed error path in this
case was:

```text
E:\Comfy-Desktop\ComfyUI-Installs\Test\ComfyUI\models\TTS\ACE-Step-v1-3.5B
```

For example, if the model actually lives at
`E:\Comfy-Desktop\ComfyUI-Shared\models\TTS\ACE-Step-v1-3.5B`, run this in an
administrator PowerShell (create the parent directory first if `models\TTS`
does not exist):

```powershell
$install = 'E:\Comfy-Desktop\ComfyUI-Installs\Test\ComfyUI'
$shared = 'E:\Comfy-Desktop\ComfyUI-Shared\models\TTS'
New-Item -ItemType Directory -Force "$install\models" | Out-Null
New-Item -ItemType Junction -Path "$install\models\TTS" -Target $shared
```

Then confirm the four subdirectories sit directly under
`models\TTS\ACE-Step-v1-3.5B\` (do not nest an extra
`ACE-Step-v1-3.5B\ACE-Step-v1-3.5B` layer), restart ComfyUI, and test the
connection on the `🎨 生成模型` page. The test should show `ACEModelLoader` and
`ACEStepGen` as visible; if the nodes are missing, reinstall/update the
`ComfyUI_ACE-Step` custom node first.

### 3. Lyrics vs. Instrumental

The default BGM lyrics placeholder is `[Instrumental]` (pure instrumental). For
vocal songs, change the `LYRICS` default inside the workflow, or fork your own
API JSON with fixed lyrics.

### 4. Console Configuration Example

```json
{
  "music": {
    "provider": "comfyui",
    "comfyui": {
      "url": "http://127.0.0.1:8188",
      "workflow": "comfy/music-ace-step-v1-api.json",
      "checkpoint": ""
    }
  }
}
```

### 5. VRAM And Alternatives

| Option | Rough VRAM | Notes |
|---|---|---|
| ACE-Step 3.5B | ~8GB+ (cpu_offload available) | BGM / theme songs, controllable duration |
| Cloud ElevenLabs / Volcengine / OpenRouter | 0 local VRAM | Stable quality, zero ops; paid and network-dependent |

---

## 中文

[`music-ace-step-v1-api.json`](music-ace-step-v1-api.json) 是
[ACE-Step v1 3.5B](https://github.com/ace-step/ACE-Step) 的 API 格式 ComfyUI 音乐工作流,
配合自定义节点 [billwuhao/ComfyUI_ACE-Step](https://github.com/billwuhao/ComfyUI_ACE-Step)
使用,用于生成 BGM / 主题曲,可指定时长。

占位符:`PROMPT` `LYRICS` `DURATION` `SEED`。

工作流 JSON 默认留空。只有在 Web 控制台「🎨 生成模型」页将音乐渠道切换为
**ComfyUI(本地)** 时,才按需填写本工作流路径。

### 1. 安装自定义节点

```bash
cd <ComfyUI>/custom_nodes
git clone https://github.com/billwuhao/ComfyUI_ACE-Step.git
cd ComfyUI_ACE-Step && pip install -r requirements.txt
```

重启 ComfyUI 后,节点列表应出现 `ACEModelLoader` / `ACEStepGen` 等。

### 2. 模型下载

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

#### Windows Comfy Desktop 共享目录注意事项

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

### 3. 歌词与纯音乐

BGM 默认歌词占位为 `[Instrumental]`(纯音乐)。若要人声歌曲,可在工作流里改 `LYRICS` 默认,或自行 fork 一份带固定歌词的 API JSON。

### 4. 控制台配置示例

```json
{
  "music": {
    "provider": "comfyui",
    "comfyui": {
      "url": "http://127.0.0.1:8188",
      "workflow": "comfy/music-ace-step-v1-api.json",
      "checkpoint": ""
    }
  }
}
```

### 5. 显存与替代方案

| 方案 | 显存粗估 | 说明 |
|---|---|---|
| ACE-Step 3.5B | ~8GB+(可 cpu_offload) | BGM / 主题曲,可指定时长 |
| 云端 ElevenLabs / 火山 / OpenRouter | 0 本地显存 | 质量稳、零运维;付费与网络依赖 |
