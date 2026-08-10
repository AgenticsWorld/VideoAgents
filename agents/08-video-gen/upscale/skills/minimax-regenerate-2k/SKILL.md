---
name: minimax-regenerate-2k
description: Use when the upscale agent needs to super-resolve a MiniMax-H3 768P clip to 2K via the MiniMax video regeneration API (POST /v2/video_regeneration), only if a MiniMax API key is configured and the source clip meets the H3 768P output spec.
metadata:
  skill_version: 0.1.0
  owner: upscale
  tags:
    - minimax
    - regenerate-2k
    - video-upscale
  api_doc: https://platform.minimax.io/docs/api-reference/video-generation-v2-regeneration
---

# MiniMax Regenerate-2K 视频超分

## 目的

用 MiniMax 云端超分模型 Regenerate-2K(`POST /v2/video_regeneration`,模型固定 `MiniMax-H3`,分辨率固定 `2K`)把 768P 草稿 clip 重生成为 2K 终版。它不是通用像素放大器:模型按源视频重新推理细节,画质优于插值放大,但**仅接受 MiniMax-H3 768P 直出规格的源视频**。

本 skill 只定义判定与调用流程;能否使用由下述适用条件决定,不满足时回退 SOUL.md 常规超分手段,严禁硬套。

## 适用条件(逐条判定,全部满足才走本 skill)

1. **MiniMax 已配置**:系统提示词出现「MiniMax Regenerate-2K 超分 Skill」注入段即满足(Web 控制台「🎨 生成模型」视频 MiniMax 标签页已填 API Key,或环境变量 `MINIMAX_API_KEY`);与当前生效视频渠道是不是 minimax 无关。
2. **源 clip 满足 API 输入规格**(= MiniMax-H3 768P 直出成片口径,通常仅当本组草稿由 H3 768P 生成时天然满足):
   - 含音轨(必须);
   - 24 fps;
   - 宽高均能被 32 整除;
   - 面积 ≤ 768×1344 = 1,032,192 px;
   - 107–362 帧(约 4–15s @24fps)。

   判定方法:直接跑 `--dry-run`,`genmedia.py upscale` 会用 ffprobe 自动预检并逐项报错;不要手工目测。
3. 备选通道:若本组 768P 草稿正是 7 天内经 MiniMax API 生成且任务 `succeeded`,可用 `--source-task-id <任务id>` 免传源视频(任务 id 见生成日志「任务已创建 <id>」)。

不满足任一条(常见:草稿由 Seedance/ComfyUI 生成、fps≠24、无音轨)→ 本 skill 不适用,按 SOUL.md 常规手段超分,并在回执 `result.json` 里说明判定结果。

## 调用

```bash
# 先只跑预检(不计费,不发任务)
python3 modules/genmedia.py upscale --input assets/clips/ep01/grp014.mp4 \
    --output assets/clips/ep01/grp014_2k.mp4 --prompt "<原始 video_prompt>" --dry-run

# 正式提交(异步任务,内部轮询到完成并落盘)
python3 modules/genmedia.py upscale --input assets/clips/ep01/grp014.mp4 \
    --output assets/clips/ep01/grp014_2k.mp4 --prompt "<原始 video_prompt>"

# 或按源任务 id 重生成(免传源视频,与 --input 互斥)
python3 modules/genmedia.py upscale --source-task-id <任务id> --output <路径.mp4>
```

- `--prompt` 必须传**该组生成时的原始 video_prompt**(取本组 `meta/prompts.json` 记录),不要新写描述——Regenerate-2K 按原 prompt + 源视频重生成,换 prompt 会引入内容漂移。
- 源视频 ≤45MB 时 base64 内联提交;更大时自动走「设置 → 文件托管」对象存储预签名 URL(未配置会报错提示)。
- 输出文件名遵守 ASCII 红线与 grp/sh 三位零填充(工具提交前自动机检)。

## 输出后处理与自检

1. 输出为 2K 档(按源画幅等比,16:9 约 2560×1440)。与「📤 输出设置」成片档在 `bible/aspect_ratio.json` 的目标像素尺寸不一致时,用 ffmpeg 缩放到目标尺寸(下采样用默认 bicubic 即可;严禁改 fps/时长/画幅):
   `ffmpeg -y -i in_2k.mp4 -vf scale=W:H -c:a copy out_final.mp4`
2. 机检(SOUL.md 口径):ffprobe 核对 `target_resolution_ok`,fps/时长与输入完全一致,音轨原样保留(音画同步不得漂移)。
3. 抽帧比对源 clip 自检超分伪影(过锐/涂抹/光晕/闪烁);回执记录 `model: MiniMax-H3 Regenerate-2K`、任务 id、参数,保证可复现。

## 失败与计费纪律

- 任务 `failed/cancelled`、429(限流)、402(余额不足):如实上报,**严禁对同一 clip 反复盲重试**——按 `output_seconds`(输出秒数)计费,重试即重复扣费;限流可等待后单次重试一次,余额不足直接上报用户。
- 预检不合规、渠道不可用、结果 QA 不达标:回退 SOUL.md 常规超分手段,不阻塞流水线。
- 冲突时以 upscale SOUL.md 为准(本 skill 不改变职责边界:不修内容缺陷、不动音轨、不做平台转码)。
