---
name: agentics-media-generation
description: Use when the video-generation agent submits or recovers video through AgenticsLLM; explains fixed profile fields, indexed attachments, long-running task recovery, and safe error handling without exposing backend workflow nodes.
metadata:
  skill_version: 0.3.0
  owner: video-generation
  tags:
    - agentics
    - video-generation
    - media-generation
---

# AgenticsLLM 视频生成

AgenticsLLM 使用登录桌面端账号调用后端媒体生成 profile。Profile 详情明确声明其支持的固定参数、默认值、枚举转换及附件数量；详情缓存、约束校验、上传、幂等键、轮询与取消都由 `modules/genmedia.py` 负责。

## Profile 契约

- 视频参数只可能来自固定字段：`prompt`、`negative_prompt`、`duration`、`resolution`、`aspect_ratio`、`fps`、`seed`、`steps`、`guidance_scale`、`generate_audio`。实际可用字段和取值以当前 profile 详情为准，未映射字段不得硬塞。
- 视频附件只可能是 `reference_images`、`reference_videos`、`reference_audios`、`first_frame`、`last_frame`。客户端按 profile 的 `min_items/max_items` 检查数量，并用从 0 开始的 `index` 提交。
- `value_map` 的可选值是严格枚举；例如 H3 的分辨率应使用 profile 接受的 `480P/480p/768P/768p`，不要发送工作流内部的 `0.4/0.9`。
- `fill: repeat_last` 由服务端填充未提供的工作流槽位。H3 的 `reference_images` 为 1–3 张时，一张参考图已经满足要求；不要把同一文件手工重复三次。

## 调用边界

- 只通过 `python3 modules/genmedia.py video ...` 提交；先运行 `info` 或工单要求的 `info --group epNN/grpNNN` 核对实际 profile。
- 视频任务可能长时间排队或生成。提交命令应允许至少 2 小时运行，并根据每分钟心跳继续等待；单次 TLS、DNS、连接或服务端可重试错误不是生成失败，也不占用“重新生成”次数。
- 按 `grpNNN.json` 原顺序传 `--ref`、`--audio-ref`、`--ref-video`；首尾帧只用 `--first-frame/--last-frame`，不同附件语义不要互换。
- 不手工请求 Agentics REST 接口，不读取或猜测 RunningHub 节点名，不拼 `parameters`、`files[].token` 或 `files[].index`。
- 不因生成失败修改 `modules/genmedia.py`、后端 profile 或工作流。工具契约问题原样上报维护者。

## 失败处理

- HTTP 422：记录错误中的 `profile_code/location/field/reason/expected`；`required` 补必填输入，`constraint_violation/enum_mismatch` 按 `expected` 修正，`unsupported_field` 交给客户端刷新详情一次。仍失败则原样上报，不猜字段、不盲重试。
- HTTP 401/403：停止并报告桌面端登录失效；不索取或打印 JWT。
- HTTP 402：停止并报告余额不足。
- HTTP 503、TLS/DNS/连接中断：让当前 `genmedia.py` 进程继续轮询同一 task ID；不要另开一条相同任务。
- 已创建任务必须记录 task ID。如果外层工具或 Agent 进程在创建后中断，先运行 `python3 modules/genmedia.py reclaim --task-id <UUID> --output <原输出路径>` 恢复等待和下载；只有服务端明确返回失败/取消后才允许按工单策略重新生成。
- AgenticsLLM 视频等待达到客户端上限时不会自动取消服务端任务；继续使用 `reclaim`，不得把“尚未确认终态”写成“生成失败”。用户明确中止时客户端才请求取消。

MiniMax H3 等模型的提示词写法仍由 prompt 工位按实际 profile code 选择对应模型 skill；本 skill 不改变 prompt，也不复制模型写作规则。
