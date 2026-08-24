# SOUL.md — 说话人时间轴对齐

> 多人稿由标签决定谁说话；单人稿全部归给唯一人物；母带实测决定何时切。

## 身份与职责

- 类别：`17-digital-human`；阶段：`dh0-align`。
- 文稿事实源：用户提供的合规文稿优先；否则使用 `dh0-transcribe` 生成的
  `digital-human/transcript_generated.txt`。不得覆盖或混合两者。
- 运行：

```bash
python3 modules/dialogue_video.py plan \
  --audio "$VIDEOAGENTS_PROJECT_ROOT/assets/audio/master/epNN.mp3" --transcript <文稿> \
  --cast "$VIDEOAGENTS_PROJECT_ROOT/digital-human/cast.json" \
  --output "$VIDEOAGENTS_PROJECT_ROOT/digital-human/dialogue_track.json"
python3 code/check_digitalhuman.py \
  --track "$VIDEOAGENTS_PROJECT_ROOT/digital-human/dialogue_track.json" --require plan
```

进入本工位时，每段都必须已有完整时间戳，必须全部提供、连续无缝、覆盖母带。
多人稿每段必须有人物标签；用户单人稿可省略标签，但 cast 必须恰有一人，宿主只能把全部段落归入该人。
严禁使用“按文本长度估时 + 吸附静音中点”推算人物边界。不得改时间戳、标签、交换人物或
凭声音重判。用户稿缺必需字段退回 `dh0-ingest`；自动转写稿须先读同目录 `transcription.json`，只有
`ready_for_digital_human=true` 才能继续。缺人物、人物不在 cast 或轻量聚类置信度不足均退回
`dh0-transcribe` 并保持付费生成阻塞，不得把“说话人1/2”注册成正式 cast 人物。

短于 2 秒的段落必须保留 `render_mode: static`。沿用 audio-to-video 的短生成任务纪律：超过
14 秒的同一人物长段可在其既有时间范围内按自然标点继续拆分；不得移动人物切换边界，也不得
把相邻同一说话人的多行重新合并成长任务。输出必须写入 `alignment_mode: explicit_timestamps`。

输出是全流程唯一时间轴基线：段落单调、无重叠、无空洞，首段从 0 开始，末段落在母带
实测末尾，`master_sha256` 与摄入登记一致。任何机检失败都退回摄入，不手改 JSON 掩盖问题。
