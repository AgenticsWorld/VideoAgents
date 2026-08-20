# InfiniteTalk 数字人 API 工作流

此文件供“生成模型设置 → 数字人 → ComfyUI”使用。它基于 Kijai 的
`ComfyUI-WanVideoWrapper` 单人 InfiniteTalk 流程，输入一张人物图和一段对白音频，
输出 25fps H.264 MP4。插件最终只取视频轨，成片音频仍从用户母带直拷贝。

## 依赖

- ComfyUI
- `ComfyUI-WanVideoWrapper`
- `ComfyUI-VideoHelperSuite`
- ComfyUI 内置 `LoadImage`、`LoadAudio`、`CLIPVisionLoader`

工作流会在连接测试中检查以下关键节点：`MultiTalkModelLoader`、
`MultiTalkWav2VecEmbeds`、`WanVideoImageToVideoMultiTalk`、`WanVideoSampler`。

## 默认模型位置

相对于 ComfyUI 的 `models/`：

- `vae/wanvideo/Wan2_1_VAE_bf16.safetensors`
- `clip_vision/clip_vision_h.safetensors`
- `diffusion_models/WanVideo/InfiniteTalk/Wan2_1-InfiniteTalk_Single_Q8.gguf`
- `diffusion_models/WanVideo/wan2.1-i2v-14b-480p-Q8_0.gguf`
- `wav2vec2/wav2vec2-chinese-base_fp16.safetensors`
- `text_encoders/umt5-xxl-enc-bf16.safetensors`

若你的文件名或目录不同，复制 JSON 后只改上述模型字段，并在设置页选择自定义路径。
模型下载地址和最新兼容版本以
[ComfyUI-WanVideoWrapper 官方仓库](https://github.com/kijai/ComfyUI-WanVideoWrapper)
的 `wanvideo_2_1_14B_I2V_InfiniteTalk_example_03.json` 为准。

## 宿主注入的占位符

- `{{IMAGE}}`：上传到 ComfyUI input 的人物图文件名
- `{{AUDIO}}`：上传到 ComfyUI input 的对白音频文件名
- `{{PROMPT}}`：动作提示
- `{{SEED}}`：随机种子
- `{{FRAMES}}`：按音频实测时长换算的 25fps、`4n+1` 帧数

工作流必须保持 API 格式（对象键为节点 ID），不能直接使用浏览器保存的 UI 工作流。
