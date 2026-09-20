# InfiniteTalk (fp8 + lightx2v 4-step) ComfyUI Digital-Human Workflow

[English](#english) | [中文](#中文)

## English

[`digitalhuman-infinitetalk-fp8-lightx2v-api.json`](digitalhuman-infinitetalk-fp8-lightx2v-api.json) is a faster
single-speaker alternative to [`digitalhuman-infinitetalk-api.md`](digitalhuman-infinitetalk-api.md): one portrait
image plus one dialogue audio produce a talking clip with
[InfiniteTalk](https://huggingface.co/Kijai/WanVideo_comfy) on Wan 2.1 I2V 14B 480p. Differences from the base
template: fp8-scaled safetensors instead of Q8 GGUF, the **lightx2v 4-step distill LoRA** (`flowmatch_distill`,
4 steps, `cfg 1.0`, `shift 11`) instead of 6 `dpm++_sde` steps, 30-block swap for 24 GB cards, `mkl` colour
matching, `motion_frame 25`, and the portrait resized to fit 832x832 keeping its aspect ratio
(`ImageResizeKJv2`, multiples of 16) instead of a fixed 832x480.

Placeholders: `IMAGE` `AUDIO` `PROMPT` `SEED` `FRAMES`.

### Install

Custom node packs: **ComfyUI-WanVideoWrapper**, **ComfyUI-KJNodes**, **ComfyUI-VideoHelperSuite**.

```text
ComfyUI/models/
├── diffusion_models/Wan2_1-I2V-14B-480p_fp8_e4m3fn_scaled_KJ.safetensors
├── diffusion_models/InfiniteTalk/Wan2_1-InfiniTetalk-Single_fp16.safetensors
├── loras/lightx2v_I2V_14B_480p_cfg_step_distill_rank128_bf16.safetensors
├── text_encoders/umt5-xxl-enc-bf16.safetensors
├── clip_vision/clip_vision_h.safetensors
└── vae/Wan2_1_VAE_bf16.safetensors
```

`TencentGameMate/chinese-wav2vec2-base` is downloaded by `DownloadAndLoadWav2VecModel` on first run. Adjust the
file names / sub-folders in the JSON to match your installation.

### Console Configuration

`🎨 生成模型` › **Digital human › ComfyUI**, run mode **local** or **Comfy Cloud**, workflow:

```text
comfy/digitalhuman-infinitetalk-fp8-lightx2v-api.json
```

### Runtime Parameters

`FRAMES` is computed from the audio length at 25 fps (`4n+1`); `PROMPT` defaults to a neutral speaking
description. Compared with the RunningHub original this template drops the platform-specific helper nodes (audio
duration math, vocal separation — dialogue audio from TTS is already a clean voice) and uses `sdpa` attention with
no `torch.compile`, so it runs on a stock install; switch `attention_mode` to `sageattn` if you have it for extra
speed.

---

## 中文

[`digitalhuman-infinitetalk-fp8-lightx2v-api.json`](digitalhuman-infinitetalk-fp8-lightx2v-api.json) 是
[`digitalhuman-infinitetalk-api.md`](digitalhuman-infinitetalk-api.md) 的更快的单人说话替代方案:一张人物图 + 一段对白音频,
用 Wan 2.1 I2V 14B 480p 上的 [InfiniteTalk](https://huggingface.co/Kijai/WanVideo_comfy) 生成说话片段。与基础模板的差别:
fp8 scaled safetensors(而非 Q8 GGUF)、**lightx2v 4 步蒸馏 LoRA**(`flowmatch_distill`、4 步、`cfg 1.0`、`shift 11`,而非
6 步 `dpm++_sde`)、30 块 block swap 适配 24 GB 显卡、`mkl` 色彩匹配、`motion_frame 25`,人物图按比例缩进 832x832
(`ImageResizeKJv2`,16 的倍数)而不是固定 832x480。

占位符:`IMAGE` `AUDIO` `PROMPT` `SEED` `FRAMES`。

### 安装

自定义节点包:**ComfyUI-WanVideoWrapper**、**ComfyUI-KJNodes**、**ComfyUI-VideoHelperSuite**。

```text
ComfyUI/models/
├── diffusion_models/Wan2_1-I2V-14B-480p_fp8_e4m3fn_scaled_KJ.safetensors
├── diffusion_models/InfiniteTalk/Wan2_1-InfiniTetalk-Single_fp16.safetensors
├── loras/lightx2v_I2V_14B_480p_cfg_step_distill_rank128_bf16.safetensors
├── text_encoders/umt5-xxl-enc-bf16.safetensors
├── clip_vision/clip_vision_h.safetensors
└── vae/Wan2_1_VAE_bf16.safetensors
```

`TencentGameMate/chinese-wav2vec2-base` 由 `DownloadAndLoadWav2VecModel` 首次运行时自动下载。JSON 里的文件名/子目录请按你的安装调整。

### 控制台配置

`🎨 生成模型` › **数字人 › ComfyUI**,运行方式**本地**或 **Comfy Cloud**,工作流选:

```text
comfy/digitalhuman-infinitetalk-fp8-lightx2v-api.json
```

### 运行时参数

`FRAMES` 按音频时长以 25 fps 换算(`4n+1`);`PROMPT` 缺省为中性的说话描述。相比 RunningHub 原版,本模板去掉了平台专有的辅助节点
(音频时长换算、人声分离——TTS 出的对白本就是干净人声),注意力用 `sdpa`、不开 `torch.compile`,标准安装即可运行;
装了 sageattention 的可把 `attention_mode` 改成 `sageattn` 提速。
