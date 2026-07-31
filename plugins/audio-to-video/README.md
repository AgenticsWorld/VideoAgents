# audio-to-video — 音频配画团队

**给一段讲述音频配上视频画面。** 用户提供 MP3(如一个人讲历史故事)与对应文本,
流程产出一条画面精确贴合这段音频的成片,**声轨就是用户原来那个 MP3**。

与主流程方向**正好相反**:

| | 主流程(小说→视频) | 本插件(音频→视频) |
|---|---|---|
| 谁固定 | 画面(镜头先定) | **音频**(母带不可动) |
| 谁适配 | 旁白(TTS 压缩塞进窗口) | **画面**(按实测时长切分) |
| 声轨来源 | TTS 旁白 + 模型原生对白 + 后期混音 | **用户母带,零重编码** |
| 时长事实源 | `settings.json` 的集预算 | **ffprobe 实测的母带时长** |

## 依赖两个仓库级文件(重要)

插件是纯声明式的(§10.4 不允许含可执行代码),但时间轴精度不能交给 LLM 每个项目
现写一遍脚本。故本插件**依赖两个随仓库发布的文件**:

| 文件 | 作用 |
|---|---|
| `modules/avsync.py` | 时间轴原语:实测时长、停顿检出、字数估时、边界吸附、整秒切分、逐帧校验 |
| `code/check_av_sync.py` | 机检 `av_sync`:分 timeline/clips/final 三阶段共 15 项;`--stamp` 盖章 |

⚠️ 把 `plugins/audio-to-video/` 单独拷到没有这两个文件的环境,插件会注册成功但
**所有时间检查退化为 LLM 目测**。请确认目标环境的 VideoAgents 版本包含它们。

外部依赖:**ffmpeg / ffprobe 必须可用**(`brew install ffmpeg`)。摄入阶段会显式探测,
缺失即升级人工——不做静默降级。

## 流程

```
av0 音频立项    audio-ingest(实测+停顿+sha256) → transcript-aligner(文本对齐)
                └─ ga0 [AVH1-时间轴基线签字] ★全流程最关键
av1 设定与美术  era-researcher(轻量时代考据) ∥ 06-art/*(风格/概念/服装/道具)
                05-scenes/*(场景/环境/建筑/光照)
                └─ ga1 [AVH2-风格与时代考据签字]
av2 画面规划    visual-scripter(画什么) → timeline-planner(多长,盖章)
                → 07-directing/continuity-planning
                └─ ga2 [AVH3-画面规划与分镜确认]
av3 视觉生成    for_each group: 08-video-gen/prompt → image-generation
                → video-generation(--generate-audio off) → character-consistency
                └─ ga3 [AVH4-视觉生成确认]
av4 剪辑封装    10-editing/edit(母带 -c:a copy,严禁 -shortest) → subtitle/title/thumbnail
av5 终审        11-qa/av-sync-qa ∥ visual-qa ∥ world-consistency-qa ∥ content-safety
                ∥ copyright ∥ audio-qa(只测量上报)
                └─ ga5 [AVH5-成片签字]
```

## 核心机制:整秒切分

**关键认识**:音频是一条连续未剪的流,最后整段 mux。所以视频切点落在句中只影响**观感**,
不影响同步。同步 = ①每组画面内容对应它覆盖的那段文本 + ②累计位置零漂移。

因此不需要 ASR 强制对齐,也不需要切点精确落在停顿里:

1. `ffprobe` 实测总长(如 612.384s,全精度不取整)
2. `silencedetect` 出停顿,取中点作候选切点
3. 文本按字数比例估时 + 吸附到停顿 → `av/beat_track.json`
4. **整秒切分**:每组 span 为整数秒且 ∈`[4,14]`,切点优先取段落边界;末组吸收小数余量
5. 生成 `--duration <整数>` `--generate-audio off`(无声画面,彻底规避口型问题)
6. `timeline.json` 整秒累加 → **累计漂移在数学上恒为 0**;仅末组按 `out` 修剪
7. 封装 `-c:v copy -c:a copy`

上限取 **14 而非 15**:留 1s 给模型 ±1s 交付公差。
`total_duration_s` 必须是整数、∈[4,15]、且 == `int(round(Σ子镜头时长))`——整秒切分让三条天然同时满足。

## 「零重编码」的准确口径

实测结论,**不要写成「整条流比特级一致」**:

MP3 装进 MP4 时,容器必然重写 gapless/编码器延迟元数据,**整流 md5 一定变**;
末帧也可能被容器补齐重写。但每一帧的压缩载荷是逐字节拷贝的。故机检比对**逐帧 md5**,
允许末帧不同——这样既放过合法的 `-c:a copy`,又能检出:

| 情形 | 检出方式 |
|---|---|
| 转 AAC | codec 变 + 帧数变(1533→1724) |
| 重编码 MP3(codec/参数完全相同,最隐蔽) | 逐帧 md5:1404/1533 帧载荷不同 |
| 误用 `-shortest` | 帧数少 1(**成片总长检查仍会 PASS,差 0ms,只有逐帧能发现**) |

**封装铁律:严禁 `-shortest`。** 音频是权威时长,视频须 ≥ 音频后由 timeline 修剪。

保证止于 `edit/epNN/final.mp4`。发布阶段 `12-publishing/platform-adapter` 必然按平台规格转码,
那之后不再有零重编码保证(预期行为);转码副本文件名**不得含 `final`/`master`**,
否则被视频预览页当成第二个成片。

## 三项特殊授权(均由 AVH1 用户签字兜底)

1. **直改正史**(覆盖 §10.3.5,同 `fusion-fiction` 先例):考据/形象/场景/分镜/剧本直写
   `bible/`、`story/`、`directing/`,让现有预览页零改动呈现;`av/` 只存过程件。
   分支与版本用户自理,插件不做克隆/回滚——**开工前建议自行留备份(如 git 分支)**。
2. **母带不归一化**:`audio-qa` 的 `-14 LUFS ±1`、真峰值、音效出声率 ≥90% 与本流程对立,
   改为只测量上报,超标开 minor 缺陷并**永久豁免**(§7 `PASS_WITH_WAIVER`)。
3. **关闭片头/片尾/预告**:`settings.json` 的 `packaging` 三项须置 `false`。片头会重编码
   并把讲述起点推后,破坏零漂移证明。

## 五处主流程机检不适用(逐项替换)

| 主流程机检 | 为何不适用 | 替换 |
|---|---|---|
| `narration_anchors` 窗口检查 | 要求 `window ≥ est×1.15` 且 `dur ≤ window×0.9`,音频锁定下 `window == dur`,**数学上不可满足** | `narration_anchors: []` + `check_generation_groups.py --skip-7d` |
| `narration_anchors_cover_all` | 本流程无 `narration.md` | 同上 `--skip-7d` |
| `audio_track_present` | 一律 `--generate-audio off` 出无声画面 | 反向断言 `clip_silent` |
| `narration_anchor_sync`(edit 封装前) | 本流程无 TTS 旁白轨 | `av_sync`(**工单须显式声明**,否则 edit 按其 SOUL 会停手上报) |
| `lufs_-14_pm1` 等音频指标 | 与零重编码对立 | `loudness_measured_reported`(只报不阻断) |

## 团队

**新增 6 个**(类别 `15-audio-video`「音频配画」):

| Agent | 职责 | 主要产物 |
|---|---|---|
| `audio-ingest` | 实测时长 + 停顿检出 + 母带指纹(只测不改) | `av/audio_map.json`、`assets/audio/master/epNN.mp3` |
| `transcript-aligner` | 文本切句 + 估时 + 吸附停顿 | `av/beat_track.json` |
| `era-researcher` | 轻量时代考据(服饰/建筑/器物/礼制)+ 人物 + 场景 | `bible/era.json` 等 |
| `visual-scripter` | 逐段设计**画什么**(不碰时长) | `story/episodes/epNN/screenplay.md` |
| `timeline-planner` | 整秒切分,组装正史 shot_list,**盖时间轴章** | `directing/epNN/shot_list.json` |
| `11-qa/av-sync-qa` | 成片终审:零漂移 + 母带原样 + 逐段语义对应 | `qa/reports/epNN/av_sync.json` |

> `timeline-planner` 与 `visual-scripter` **刻意分开**:前者纯算术(时长锁定),后者纯创作
> (画面内容)。时序先锁死,创作不得改动时序。

**复用内置 20+ 个,零改动**:`06-art/*`(风格/概念/服装/道具)、`05-scenes/*`(场景/环境/建筑/光照)、
`07-directing/continuity-planning`、`08-video-gen/*`(prompt/图/视频/一致性)、
`10-editing/*`(剪辑/字幕/标题/封面)、`11-qa/*`(视觉/世界观/安全/版权/音频)。

字幕是**白捡的**:`beat_track.json` 自带精确时间码,逐段直接转 SRT,天然完美同步。

⚠️ **美术与场景底座省不掉**:`08-video-gen/prompt` 硬检 `lighting_scheme_bound`、
`costume_bound`、`prop_scale_token_ok`,要求逐字引用 `bible/` 里的英文片段;
`video-generation` 会再检一遍并被明确指示「缺字段或矛盾退回 prompt,严禁开跑」。
所以「轻量」只针对**世界观**(不跑主流程 Phase 2 的九个 agent),美术/场景该建的还得建。

## 用法

1. **启用**:⚙️ 设置 →「插件」页 → `audio-to-video` → 启用(装后默认停用)
2. **放素材**:MP3 放 `refs/audio/`(或「参考图」预览页上传);文本放 `refs/`
3. **选风格**:菜单「设计构想」→「设计风格」→ 🎨 风格库(94 预设)。**不用新建 UI**
4. **点单**给总制片:

```
启动 audio-to-video 流程:音频 refs/audio/qin_history.mp3,文本 refs/qin_history.txt,
设计风格已在设计构想里选好。先跑 av0 摄入与对齐,出时间轴基线给我签 AVH1。
```

或 CLI:

```bash
python3 services/runtime/dispatch.py "00-orchestration/workflow-orchestrator" \
  "启动 audio-to-video 流程:音频 refs/audio/qin_history.mp3,文本 refs/qin_history.txt" \
  --project my-history
```

## 看产出物

产物直写正史路径,现有预览页零改动呈现:

| 看什么 | 在哪 |
|---|---|
| 时代考据/世界观 | 👁 预览 →「世界观预览」(`bible/era.json` 会自动出现) |
| 出场人物 / 场景 | 「人物预览」/「场景预览」 |
| 画面剧本与分镜 | 顶栏 🎬「分镜预览」(每组带 📄 Prompt、🖼 图片按钮) |
| 成片与组片段 | 顶栏 🎞「视频预览」 |
| DAG 进度与成本 | 「工作流预览」 |

插件过程件(`av/*.json`)无专属预览页,直接取:
`/api/v1/projects/<slug>/artifacts/av/beat_track.json`

⚠️ 工作流预览页的阶段名硬编码为 `p0`–`p11`(`_wf_phase_of()` 只认 `^[pg]\d+`),
`av*` 节点会落进「加派任务」泳道。**处置**:orchestrator 并入 `runs/dag.json` 时给每个节点
显式写 `phase` 字段(该函数优先取 `n["phase"]`)——已写入本插件的调度纪律。

## 自检命令

```bash
python3 code/check_av_sync.py --project <slug> --ep ep01                  # 自动判阶段
python3 code/check_av_sync.py --project <slug> --ep ep01 --require final  # 成片终审
python3 code/check_generation_groups.py --project <slug> --ep ep01 --skip-7d
python3 services/runtime/dagcheck.py --project <slug> --strict
```

## v1 不做

语音转文本(ASR;`beat_track.json` 已按句子级 schema 设计,接 ASR 只换 `source` 填充来源,
下游零改动)、句子级同步、多集(`episode_minutes` 是项目级设置,多集不同长度无法同时满足
`duration_pm_5pct`,v1 限定一个 MP3 = 一个项目 = ep01)、BGM/环境音叠加(会破坏零重编码)、
片头片尾、专属预览页。

## 建议

第一次跑用 **60–90 秒**的短音频(约 6–8 组)摸清实际开销与卡点,再上正片。
最可能卡住的两处:美术底座的英文片段是否齐备、`--generate-audio off` 在本仓库**零先例**
(flag 本身在 `genmedia.py` 是通的,但 `audio_track_present` 机检需按本插件的替换执行)。
