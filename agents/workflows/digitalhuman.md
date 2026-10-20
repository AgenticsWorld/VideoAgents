# 🧑‍💼 数字人(项目类型 `digital-human`)

> 2026-10-20 起本流程并入 VideoAgents 主程序:新建项目时第一步选「数字人」即走本流程,
> 不再需要安装/启用 digital-human 插件包(「插件」页会把旧包标为已并入)。流程 DAG 在
> `agents/workflows/digitalhuman.yaml`,团队在 `agents/17-digital-human/` 与 `agents/11-qa/digital-human-sync-qa/`,
> 项目类型机制见 `agents/WORKFLOW.md` §11。

输入一条音频母带、可选文稿及一个或更多人物图片。用户没有提供文稿时，总制片会自动派宿主
`09-audio/audio-transcription` 生成带时间轴文字稿；已有文稿仍按原格式直接使用。画面按最终
有效文稿的边界切换，只出现当前说话人。
片段通过宿主“生成模型设置 → 数字人”中当前选中的 HeyGen、
Kling AI（中国北京）、RunningHub 云端工作流或本地 ComfyUI InfiniteTalk 生成。

最终成片的声音始终是用户原始母带：数字人渠道返回的音轨会被丢弃，封装使用
`-c:a copy`，禁止 `-shortest`，并由逐音频帧 MD5 机检确认没有重编码或静默截断。

## 宿主要求

以下工具链随主程序发布：

- `modules/digitalhuman.py`：四渠道单人数字人片段生成
- `modules/dialogue_video.py`：标签解析、时间轴、片段渲染、母带封装
- `modules/transcription.py`、`faster-whisper`：缺稿时本地 ASR；模型自动缓存到 `data/models/`
- `code/check_digitalhuman.py`：时间轴、片段和最终母带机检
- `modules/avsync.py`、`ffmpeg`、`ffprobe`

第一个任务 `dh0-ingest` 会实际执行 `python3 code/check_digitalhuman.py --help` 和
`python3 modules/transcription.py --help`；缺失即停止并提示。

### RunningHub 与 ComfyUI 的区别

- **RunningHub** 是独立云端渠道：选择国内站或国际站、填写对应站点 API Key、验证并选择
  RunningHub 工作区的数字人工作流 ID，并可选择 Standard/Plus/Ultra 机器规格。国内站与国际站
  账号、Key 和工作流不互通。输入图片及当前对白片段会上传到所选站点，远端 `task_id` 持久化到
  片段任务台账，中断后恢复轮询，不会重复创建付费任务。
  宿主使用当前 V2 `openapi/v2/media/upload/binary` 媒体接口；旧
  `task/openapi/upload` 不得作为降级通道。
  数字人片段在宿主内按 1 个 RunningHub 工单串行放行；如果同一 Key 还被网页端、其它项目或
  外部客户端占用，平台返回并发/机器资源/限流背压时，当前片段进入 `waiting_capacity`，按
  10–120 秒退避并持续重试，最长等待 2 小时。该状态不是失败，不触发剩余片段批量失败或重复派单。
- **ComfyUI** 是本地渠道：只使用本地 ComfyUI 服务地址和宿主 `comfy/` 目录中的
  InfiniteTalk API JSON，不读取 RunningHub 的 Key、工作流 ID 或机器规格。

RunningHub 云端数字人工作流须先在网页工作区跑通。普通网页导出件可以保留测试时的示例
文件名：只要实际执行图中恰好有一个 `LoadImage` 和一个 `LoadAudio`，宿主就会上传当前段的
人物图与口型驱动音频并自动覆盖这两个节点，同时尽力覆盖正向提示词、Seed 和直接帧数输入。
未连接的演示孤岛节点不会造成歧义。若执行图中存在多个图片或音频加载节点，则必须精简到
各一个，或者在目标输入位显式使用 `{{IMAGE}}`/`{{FIRST_FRAME}}` 与
`{{AUDIO}}`/`{{REF_AUDIO}}` 占位符。另支持 `{{PROMPT}}`、`{{SEED}}`、
`{{DURATION}}`、`{{FRAMES}}`。

## 克隆声音（没有原始录音时）

新建项目时「声音来源」选 **克隆声音**：不需要原始录音，给一份台词稿和每个说话人的一段声音样本，系统逐句用样本的
音色合成对白，拼成母带和逐段带时间戳的人物稿，之后的数字人流程与「已有录音 + 文稿」完全一样。

- **声音样本**：参考文件「克隆声音」分类（`refs/voices/`），每个说话人一段 5–30 秒、只有这一个人说话的干净录音，
  格式 mp3 / wav / flac / m4a / aac / ogg。只上传本人或已获授权的声音。
- **台词稿**：每行一句「说话人：台词」（中英文冒号都行），只有一个说话人时可以不写人名；空行和 `#` 开头的行忽略：

```text
主持人：欢迎收听本期节目。
嘉宾：谢谢邀请，今天我们聊聊人工智能电影。
主持人：先从数字人的制作说起。
```

- **对应关系**：在「人物对应说明」里写清每个说话人用哪张照片、哪段声音，例如「主持人用 host.png 的照片和 host.mp3 的声音」；
  只有一个说话人且只有一段样本，或样本文件名与说话人同名时会自动对上。
- **TTS 渠道**：按任意样本克隆只有三种渠道能做——「生成模型」页 TTS 选 Agentics（Voice Clone 模型须收参考音频，如
  qwen3tts-clone）、ComfyUI（Voice Clone 工作流，如 IndexTTS2 / Qwen3-TTS Clone）或火山引擎的 Doubao-音频生成 1.0。
  其余渠道会在第一步直接报错，不会拿音色库里的近似音色冒充。
- **流程**：`dh0-voice`（09-audio/voice-generation）只跑宿主 CLI：

```bash
python3 code/dialogue_clone.py plan  --project <slug> --ep ep01 [--voice 主持人=refs/voices/host.mp3 ...]
python3 code/dialogue_clone.py synth --project <slug> --ep ep01 [--gap 0.4] [--force]
python3 code/dialogue_clone.py check --project <slug> --ep ep01
```

  产物：逐句音频 `assets/audio/voice/clone/ep01/`、母带 `assets/audio/master/ep01.mp3`、人物稿
  `digital-human/transcript_cloned.txt`、台账 `digital-human/voice_clone.json`（渠道、样本指纹、逐句时间）。
  只有新句或改过的句子会重新合成；台词稿或样本改了，`check` 会判过期，重跑 `synth` 后下游重新出片。

## 文稿输入（可选）

启动流程时可以完全不提供文稿。`dh0-ingest` 固化母带和人物映射后，总制片自动派
`dh0-transcribe`；首次使用若本地没有模型，会下载到宿主
`data/models/faster-whisper/`，之后复用（识别模型 = 宿主「设置 → 高级 → 语音输入」选中的那个）。生成结果默认是
`digital-human/transcript_generated.txt` 与 `digital-human/transcription.json`。

单人物录音可全自动把所有转写段落归到唯一人物。多人录音优先使用用户明确的说话人时间边界；
没有边界时，宿主在 faster-whisper 词时间轴上使用现有 PyAV/NumPy/SciPy 做低成本 MFCC、音高和
能量特征聚类，不下载额外说话人模型。用户可说明“第一个出现的不同说话人是主持人，第二个是
嘉宾”，或明确“低音/高音（兼容男声/女声表述）”对应哪张人物图。这里的第一/第二是声纹簇首次
出现顺序，不是把台词机械轮流分配。流程不会从人物图片推断性别；聚类置信度不足时会保留稿件
供审看，但在付费生成前阻塞。

用户主动提供文稿时，沿用以下格式，且不会被 ASR 覆盖。

### 单人物稿

只有一个人物映射时必须逐段提供时间戳，但人物名称可以省略；系统会自动归入唯一人物：

```text
[00:00.000-00:04.200] 欢迎来到今天的节目。
[00:04.200-00:08.600] 今天我们聊聊人工智能电影。
```

仍可在每行写“主持人：”，但同一份单人稿不得混用有名和无名格式。

### 多人物稿

两个或更多人物时，每个非空段落必须同时包含完整起止时间、说话人名称和台词：

```text
[00:00.000-00:03.200] 主持人：欢迎来到今天的节目。
[00:03.200-00:07.850] 嘉宾：谢谢邀请，今天我们聊聊人工智能电影。
[00:07.850-00:11.400] 主持人：先从数字人的制作说起。
```

时间戳必须全部提供，首段从 `00:00.000` 开始，相邻段首尾一致、无重叠、无空洞，末段结束
时间必须等于 ffprobe 实测母带时长（允许 0.05 秒封装误差）。多人无标签稿，或任何只写
“主持人：台词”但没有时间戳的稿件会在 `dh0-ingest/dh0-align` 直接停止，绝不会进入付费生成。

缺稿时会做本地 ASR 和轻量声学聚类，但不把它包装成专业声纹鉴定，也不会用全文字数估算多人
换人边界。沿用
audio-to-video 的短任务策略，超过 14 秒的同一人物长段可在用户给定区间内部按自然标点继续
拆分；人物切换边界保持原样。短于 2 秒的极短接话使用该人物静帧，避免触发渠道最短时长限制。

### 人物映射

用户不必提供 `cast.json`。新建项目时在「人物对应说明」里（写入 `brief.md`），或开工时在聊天里说明人物与图片的对应关系即可，例如：

```text
主持人使用 refs/characters/host.png；嘉宾使用 refs/characters/guest.png。
```

多人无稿时在同一段自然语言里补充声音映射即可，无需 JSON：

```text
第一个出现的不同说话人是主持人，第二个是嘉宾；
主持人使用 refs/characters/host.png，嘉宾使用 refs/characters/guest.png。
```

或：

```text
低音说话人是主持人，使用 refs/characters/host.png；
高音说话人是嘉宾，使用 refs/characters/guest.png。
```

流程会默认把自然语言映射归一化成内部 `digital-human/cast.json`，不需要用户额外要求
“生成 JSON”。如果用户已有 `cast.json`，也仍然兼容：

```json
{
  "speakers": {
    "主持人": {"image": "refs/characters/host.png"},
    "嘉宾": {"image": "refs/characters/guest.png"}
  }
}
```

## 用法

1. **新建项目**:顶栏项目下拉「＋ 新建项目…」→ 第一步选 🧑‍💼 **数字人**
2. **基础信息**:选音频母带(上传到 `refs/audio/`)、每个说话人一张照片(上传到 `refs/characters/`)、
   在「人物对应说明」写清谁用哪张照片(多人无稿时再写声音映射,见上节);文稿可选——留空就自动转写,
   有现成逐段带起止时间的文稿就粘贴(写入 `refs/text/transcript.txt`)
3. **输出设置**:画幅、语言、分辨率;旁白、片头片尾等影视剧专用开关已按类型关闭
4. **开工**:总制片核对素材并汇报缺项,素材齐了回复开工,它按本流程立项,从 dh0 摄入开始。
   开工时也可在聊天里补充说明,例如:

```text
开工。母带 refs/audio/podcast.mp3。第一个出现的不同说话人是主持人,使用 refs/characters/host.png;
第二个出现的不同说话人是嘉宾,使用 refs/characters/guest.png。每次屏幕只显示当前说话人,最终保留原始母带。
```

## 宿主命令

以下示例从工作区根目录执行；`PROJECT_ROOT` 必须指向当前项目，所有产物都写在项目内：

```bash
PROJECT_ROOT=data/projects/<slug>

# 只有用户没提供文稿时才由 dh0-transcribe 执行；单人物示例：
python3 modules/transcription.py transcribe \
  --audio "$PROJECT_ROOT/assets/audio/master/ep01.mp3" \
  --output "$PROJECT_ROOT/digital-human/transcript_generated.txt" \
  --json-output "$PROJECT_ROOT/digital-human/transcription.json" \
  --format digital-human --speaker "主持人" --language zh

# 两人物首次出现顺序示例（不是逐行交替）：
python3 modules/transcription.py transcribe \
  --audio "$PROJECT_ROOT/assets/audio/master/ep01.mp3" \
  --output "$PROJECT_ROOT/digital-human/transcript_generated.txt" \
  --json-output "$PROJECT_ROOT/digital-human/transcription.json" \
  --format digital-human --diarize --num-speakers 2 \
  --speaker-order "主持人" --speaker-order "嘉宾" --language zh

python3 modules/dialogue_video.py plan \
  --audio "$PROJECT_ROOT/assets/audio/master/ep01.mp3" \
  --transcript "$PROJECT_ROOT/digital-human/transcript_generated.txt" \
  --speaker-image "主持人=$PROJECT_ROOT/refs/characters/host.png" \
  --output "$PROJECT_ROOT/digital-human/dialogue_track.json"

python3 modules/dialogue_video.py render \
  --track "$PROJECT_ROOT/digital-human/dialogue_track.json" \
  --output-dir "$PROJECT_ROOT/assets/digital-human/ep01" --only utt0001

python3 modules/dialogue_video.py status \
  --track "$PROJECT_ROOT/digital-human/dialogue_track.json" \
  --output-dir "$PROJECT_ROOT/assets/digital-human/ep01" \
  --output "$PROJECT_ROOT/digital-human/render_status.json"

python3 modules/dialogue_video.py compose \
  --track "$PROJECT_ROOT/digital-human/dialogue_track.json" \
  --clips-dir "$PROJECT_ROOT/assets/digital-human/ep01" \
  --output "$PROJECT_ROOT/edit/ep01/final.mp4"

python3 code/check_digitalhuman.py \
  --track "$PROJECT_ROOT/digital-human/dialogue_track.json" \
  --clips-dir "$PROJECT_ROOT/assets/digital-human/ep01" \
  --final "$PROJECT_ROOT/edit/ep01/final.mp4" --require final
```

实际工作流不会用一条命令跑完整批次，而是把每个 utterance 作为独立 `dh2-avatar` 工单派发。
每段渠道任务写入 `assets/digital-human/ep01/jobs/uttNNNN.json`；其中保存 provider、task_id、
状态与错误，但不保存 API Key。宿主或 Agent 中断后重新运行同一个 `--only` 命令，会继续查询
原 task_id 并下载结果，不会再次提交。远端明确失败时才可使用 `--retry-failed`。

严禁用 `&`/`nohup` 留下无人跟踪的后台进程。Agent 只有在命令真实退出、片段与台账均完成后
才能结束工单；全片 `status` 返回 `complete=true` 且 `--require clips` 通过后才能开始合成。

## 上手测试

1. 在“生成模型设置 → 数字人”选择渠道并点测试；Kling AI 固定北京接口。RunningHub 的该测试只确认账户 Key/余额，还需选择
   `.cn/.ai` 站点，并用“验证并添加”登记已在对应工作区跑通的数字人工作流。
2. 新建一个「数字人」类型项目，确认控制台左侧出现「数字人」分组（5 个成员）。
3. 先做无文稿单人测试：只给 2–5 秒母带和一张人物图，确认出现
   `transcript_generated.txt`、`transcription.json`，且模型只写入宿主 `data/models/`。再做一份
   用户带时间戳稿测试，确认流程跳过 `dh0-transcribe` 且不覆盖原稿。
4. 做两人无稿测试：分别使用“第一/第二出现顺序”和“低音/高音”映射，检查 TXT 每行带真实人物名，
   JSON 含 `diarization.mapping_mode/confidence/cluster_names` 且
   `ready_for_digital_human=true`。人为调高置信度阈值后必须在 `dialogue_video.py plan` 阶段阻塞。
5. 等 `dh2-avatar` 完成后运行上面的 `status`，应显示 `complete: true`，并确认
   `jobs/utt0001.json` 有 task_id（静帧段 provider 为 `static`，没有云端 task_id）。
   RunningHub 并发被占满时可看到 `waiting_capacity` 与 `capacity_waits`；释放名额后应自动变为
   `submitted/running/completed`，无需手工 `--retry-failed`。
6. 最终必须以 `code/check_digitalhuman.py --require final` 退出码 0 为通过标准。

## 首版边界

- 缺稿时做本地 ASR 与轻量声学聚类并自动生成带人物时间轴；首版建议最多两人，重叠说话、极短
  接话、重背景音乐或低置信度会阻塞付费生成。它不是专业声纹鉴定。
- 每镜只显示一个人，不做双人同框、反应镜头或自动运镜。
- 不添加 BGM、降噪、归一化、片头片尾；这些都会改变或移动母带。
- 不设人工闸门；机检失败即返工，连续失败按宿主默认策略升级给用户。
- 不承诺离开宿主任务后继续“后台渲染”；中断恢复依靠持久化 task_id 和工作流重派。
