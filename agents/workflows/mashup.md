# ✂️ 混剪(项目类型 `mashup`)

**给讲述音频配现成素材画面,不生成一帧视频。** 用户提供对应文稿与**素材库**(讲述
音频**可选**——只给文稿时由 narrator 工位按文稿 TTS 合成母带,声线可在点单时指定),
流程把文稿切成语义拍、逐拍设计镜头节奏,从本地素材库的现成分镜切片配画,
拼接封装,**声轨就是用户原来那个 MP3**(零重编码)。
**v3 起素材库是唯一素材来源——不再联网找素材**,生成成本 → 零(纯本地转码)。
**v4 声画词级对齐(解说体)**:拍边界由 faster-whisper 逐词时间戳实测(不再字数估时),
镜头切换卡在词被念出的时刻,节奏三项机检硬卡——对标「旁白说什么画面就是什么、
切换卡旁白节奏」的解说混剪。

与近亲「音频配画」(`audio-to-video`)的差异:

| | 音频配画 audio-to-video | 混剪 mashup |
|---|---|---|
| 画面来源 | 图/视频生成(成本大头) | **本地素材库现成分镜**(data/footage/<name>,v3 唯一通道) |
| 美术底座 | 必建(prompt 机检硬要求) | 不建;**只保留 p0 剧情理解 + p1 story_graph**(v4.1,节奏映射输入;p2 世界圣经整段跳过) |
| 切分约束 | 整数秒 ∈[4,14](生成模型 --duration 逼的) | **拍/镜两层**:拍=句 ∈[1,20]s;镜长无上限、硬下限 0.5s |
| 交付公差 | ±1s(模型交付公差) | **零公差**(帧数按累计取整,现成素材想切多准切多准) |
| 新增风险轴 | — | **素材权属**(自备素材用户自证,台账强制登记)与**复用冲突**(同 clip 同区间双组即缺陷) |

母带纪律两者相同:母带即成片声轨、ffprobe 实测为唯一时长事实源、封装 `-c:a copy`、
严禁 `-shortest`、累计漂移恒 0。

---

## v3 素材库唯一通道(2026-08-28,用户决策)

v2 的网络检索(YouTube/CC0 素材站)在实测中被 403/API Key/限流反复卡死
(leijun 项目 325 组只配出 34 组)。v3 把素材来源整个换成**本地素材库**:

- **素材库**是宿主功能(`modules/footage_library.py` + 素材库页):上传/下载视频
  → 自动切分镜 + ASR 字幕 + AI 画面解读(info:画面/主旨/风格/标签四行),
  落盘 `data/footage/<name>/`(clips.json + 逐分镜 mp4/缩略图/联系图 + 源片)。
- **工单必须指明素材库**(一个或多个),总制片解析后落 `mashup/footage_source.json`
  (通道唯一事实源);scout/curator/cutter 第一动作读它。未指明=不开工,总制片向用户要。
- **全局 clip 寻址** `"<库名>:<clip_id>"`(如 `fabuhui:clip_006`),picks/sources/台账
  全链路统一,跨库不撞号;rough 坐标 = 所属库源片绝对秒。
- **零流量三档成本阶梯**:catalog/search 元数据 → 库内 sheet/缩略图现成 jpg 逐图目视
  → cut 直切库内源片。无预览下载、无正片拉流。
- **复用是默认策略**(库比组少):复用冲突检查取代查重——同 clip 多组精剪窗口互不
  重叠、时间轴相邻两组不视觉近似;**精剪窗口不许跨 clip 边界**(=区间内硬切,一票否决)。
- **库内兜底三档**(禁网络):① 语义放宽 → ② 邻镜素材延长顶替(限同库)→
  ③ 静帧+Ken Burns(`footage.py still`,零公差可直接拼接)。

## v4 声画词级对齐(2026-09-02,leijun 摸底改版)

leijun 摸底暴露三个病根,全部指向「没有词级时间戳」:拍边界字数估时 42% 误差
>0.5s;「切点压焦点词」无数据支撑,73% 多镜拍实测纯均分;成片 50% 静帧兜底。
v4 的解法:

1. **拍边界 ASR 实测**:`code/mashup_align.py build` 一条命令——faster-whisper
   逐词时间戳 → difflib 对齐文稿(台本是唯一文本事实源)→ 句边界取实测词界/停顿
   中点,同时产出逐字时间轴 `edit/{ep}/word_track.json`。降级路径保留(无
   faster-whisper 或 match_ratio<0.6 时退回 v3 字数估时,呈报 MH1)。
2. **切镜吸词**:shot-designer 的切点用 speech-lookup 查词 onset,**t_in = 词
   start**,非拍首镜登记 `cut_word` 词锚(机检 shot_cut_on_word ±0.3s 硬卡);
   literal 镜长 = 所指实体的语音区间,不平分整拍。
3. **入点公式**:cutter 按 `in_point = action_anchor_s − (词 onset − t_in)` 让
   素材动作恰在词被念出的时刻出现(curator 逐组登记 anchor_word)。
4. **节奏三项机检**(check_footage 6c–6e,阈值按 leijun 数据校准,旧版三项全
   FAIL):render/literal 镜长中位数比 ≤0.7、3–5s 均拍占比 ≤50%、render 快切
   (≤2.5s)占比 ≥70%。SOUL 写得再细没机检就不执行——leijun 已证明。
5. **still 降为最后手段**:curator 逐组理由 + 批内 >20% 必须上报;QA 报告
   still_ratio 目标 ≤15%(leijun 是 50%)。
6. **有限转场**:仅相邻组同 clip 复用与连续静帧组之间 0.3s 叠化(merge 单限定
   写权,两情形之外即 FAIL);渲染走宿主 render_transitions(pad 补偿总时长
   ±1 帧不变、声轨不碰),mx3 新增 mx3-transition 节点统一封装。
7. **流程瘦身(v4.1)**:p2 世界圣经九工位+merge 整段跳过(检索语言/词形消费者已随
   素材库通道消亡,纪实题材全是「现实世界/无」占位),p1-timeline/g1/g2 一并跳;
   主题基调由 shot-designer 从文稿自提炼——每项目省 10+ 单 agent 会话,
   核心链路收敛为:对齐 → 分镜 → 选片 → 切片 → 成片。

## v2 节奏与语义方法论(2026-08-06,保留)

摸底测试的两个病根:3–5s 均拍导致**无节奏感**;按字面名词配画(「事情」→刷油漆)
导致**无共鸣**。解法固化为 shot-designer 的四步纪律:

1. **找信息焦点**:「就是自己喜欢的事情」重点在**「喜欢」**不在「事情」——焦点是
   情绪重心,不是语法宾语。
2. **定对应模式**(文画对应两逻辑):
   - **literal(一致)**:文字与画面讲同一个事——说曹操,画面就是曹操。1 镜,可长镜。
   - **render(相辅相成)**:文字与画面各承担不同信息——「喜欢的事」无画面可直接
     对应,用**人的状态**渲染(做事时的笑脸/投入);一个画面渲染力度不够就
     **群像多镜快切**(2–6 镜,0.8–2.5s/镜),**节奏感来源于此**。
   - 逐拍在 `shot_list.json` 的 `beat_design` 声明 mode+focus,机检硬卡。
3. **选意象,主题前瞻**:先通读全稿与世界观参考,意象服务**全片主题**,拒绝无共鸣的
   字面匹配;v3 的意象池即素材库的 info/subtitle,匹配语言照库的实际语言。
4. **切镜定节奏**:镜带绝对 t_in/t_out 铺满所属拍,直给拍慢、渲染拍快,长短交替
   跟信息密度走,严禁全片镜长趋同。

**时间结构**:拍(beat,aligner 按句切,时长事实)⊃ 镜(shot,designer 拍内细分)。
1 镜 = 1 组不变,零漂移数学不变(帧数按绝对时刻累计取整,与镜长不齐无关)。

> 2026-10-20 起本流程并入 VideoAgents 主程序:新建项目时第一步选「混剪」即走本流程,
> 不再需要安装/启用 mashup 插件包(「插件」页会把旧包标为已并入)。流程 DAG 在
> `agents/workflows/mashup.yaml`,团队在 `agents/16-mashup/` 与 `agents/11-qa/mashup-qa/`,
> 项目类型机制见 `agents/WORKFLOW.md` §11。

## 运行前提

| 要求 | 说明 |
|---|---|
| **宿主工具链** `modules/footage.py` + `modules/footage_library.py` + `code/check_footage.py` + `code/mashup_align.py` + `code/render_transitions.py` | 随主程序发布;第一个节点 mx0-ingest 先自检,不通即停 |
| **faster-whisper(强烈建议)** | 声画对齐的核心(`pip install faster-whisper`,模型自动下载到 data/models/);缺失自动降级字数估时并呈报 MH1——能跑但对齐精度回到 v3 |
| **素材库已就位** | `data/footage/<name>/clips.json` 存在、分镜切分完成、**建议 info(AI 画面解读)覆盖率 100%**(在素材库页跑「AI 分析」)——info 是 scout 语义匹配的主检索面,缺了只能靠字幕 |
| **ffmpeg / ffprobe 可用** | `brew install ffmpeg`(v3 起 yt-dlp/node 不再是前置) |
| **至少一个 agent 引擎** | `footage-curator` 必须派给**带视觉能力**的引擎(要看图选片) |

自检:`python3 modules/footage.py doctor`(检查外部命令并列出素材库;
doctor 的 yt-dlp/youtube 连通性项失败**不影响**混剪)。

## 流程

```
p0–p1 剧情理解(文稿即文本输入;story_graph 供节奏映射;p2 世界圣经已跳)─┐
mx0 音频立项与分镜  audio-ingest(工具链自检+素材库核验+实测+停顿)   │
                    → transcript-aligner(mashup_align build:ASR 词级实测
                      拍边界 + word_track 两件套,盖章)              │
                    → shot-designer(catalog 通读→焦点→模式→切镜吸词)◄┘
                    └─ gm0 [MH1 时间轴与分镜签字] ★基线,含节奏设计、
                       版权责任自担与素材库确认(第 5 签字项)
mx1 检索与选片      for_each 主题簇批: footage-scout(8–10 簇/单,catalog 通读
                      全库语义匹配,零网络零新增视频字节;逐簇独立产出)
                    → footage-curator(4–6 簇/批,直读库内缩略图/联系图逐图核验;
                      literal 看所指、render 看人的状态讲 focus;按源片绝对秒定
                      rough 区间,精剪窗口不跨 clip 边界,**只写本批分片**
                      picks/bNN.json + sources/bNN.json)
                    → mx1-curate-merge(合并全部分片为全局 picks/sources 单文件,
                      去重+used_by 并集+复用冲突复检,统一盖章)
                    └─ gm1 [MH2 选片确认] — 分镜预览页看选片帧,组注释调检索意图
mx2 切片            for_each 组: clip-cutter(从库源片按绝对秒直切→零公差精剪归一;
                      镜时长取 shot_list 绝对 t_in/t_out;still 兜底组走静帧推拉)∥全并发
mx3 剪辑封装        10-editing/edit(concat 出 cut_v1,timeline 带 group_id/in/out)
                    → 10-editing/transition(render_transitions 渲染有限叠化,
                      pad 补偿时长不变;footage.py mux 母带 -c:a copy 出 final)
                    → subtitle(逐拍直转 SRT,cue end 收到末词;字幕跟句不跟镜)
                    → thumbnail
mx4 终审            mashup-qa(21 项机检+语义抽检+卡点抽检+still_ratio+台账终核)
                    ∥ visual-qa ∥ content-safety ∥ copyright(report_only)
                    └─ gm3 [MH3 成片签字] — 阅看自备素材权属提示
```

## 核心机制

**零流量成本阶梯**(v3 全程本地):catalog/search 元数据 → 库内 sheet/缩略图
现成 jpg 逐图目视 → cut 直切库内源片(fps 归一由 cut 内建,25fps 源直切 24fps
零公差)。切片从**源片**切而非库 clip mp4——库 clip 是二次编码,再切成三代画质。

**零公差时间轴**:文稿经 ASR 词级实测切成拍(v4;无缝隙覆盖
`[0, master_duration_s]`,降级路径为字数估时+吸附),镜在拍内铺满、帧数按**累计取整**
`round(t_out×fps)−round(t_in×fps)`,整条时间轴数学上不可能漂——快切短镜再多
也不影响该数学;`check_footage.py` 四段 21 项机检兜底,
`--stamp` 盖章机制与音频配画同宗(audio_map 改版即时间轴过期)。

**批派单与会话纪律(2026-08-12 提速改版实测,v3 批宽再放大)**:mx1 按主题簇**批**
扇出——大头是会话固定开销与长汇报,打包摊薄;v3 scout 零网络零等待,8–10 簇/单,
curate 免下载但逐图看图仍吃上下文,4–6 簇/批。多批并发写全局台账必互相覆盖,
故 curator 分片写 `picks/bNN.json`+`sources/bNN.json`,由 `mx1-curate-merge`
收口合并、去重(`used_by` 取并集)并统一盖章。派单纪律:底本与 catalog 只读一遍、
自检一轮即止、回执短字段、总汇报 ≤3 行;首批派出后批次划分不得再改。

**复用与冲突检查(v3)**:一库供全片,同 clip 多组复用是设计预期;守住三条防
画面重复——同 clip 多组精剪窗口互不重叠、时间轴相邻两组不共用视觉近似区间、
同 clip 同区间双组开缺陷单。精剪窗口跨 clip 边界 = 区间内硬切,一票否决。

**权属口径(v3)**:素材库为用户自备素材,license 恒 `user-provided`,权属由用户
自证(MH1 第 3+5 签字项兜底);流程强制**如实登记**——`mashup/sources.json` 逐 clip 记
provider/id/url/title/uploader/license/used_by,机检 `sources_registered` 兜底,
库源片本身的权属提示(如源片是他人发布会的录制)在 MH3 签字时呈给用户。
`11-qa/copyright` 转 report_only。

## 在分镜预览页编辑与调整(设计目标)

`shot_list.json` 按标准 schema 落正史(1 镜 = 1 组;v2 增量键 t_in/t_out/beat_id/
beat_design,页面忽略不认识的键),分镜预览页零改动可用:

| 页面元素 | 混剪语义 | 调整方式 |
|---|---|---|
| 场次分块 | 主题簇(语义相邻聚簇;渲染拍群像天然同簇) | ✏️ 修改 → 总制片回派 shot-designer |
| 每组 📄 Prompt | 焦点/模式 + 画面需求 + 检索意图 | 只读查看 |
| 每组 📝 组注释 | **调整检索意图的入口**:注释注入 video_prompt,scout 重跑必重读 | 页面直接编辑 |
| 组锚点图(keyframes) | curator 的选片帧,MH2 签字凭据 | 看图打回 |
| 组卡片段(clips) | 切好的素材片段 | 播放核验 |
| ✏️ 修改按钮 | 换画面/换区间/改节奏等自由指令 | 发给总制片按语义回派 |

改**拍边界**的指令会回派 transcript-aligner 重切并重盖章(拍时间轴是一体的);
改**拍内节奏**(镜数/快慢)回派 shot-designer 重切镜,拍不动、不需重盖章;
换画面/换素材只动对应簇/组,时间轴不动,不需重签 MH1。

## 团队

**新增 6 个**(类别 `16-mashup`「混剪配画」)+ 1 个 QA:

| Agent | 职责 | 主要产物 |
|---|---|---|
| `audio-ingest` | 工具链自检+素材库核验+母带入册+实测(只测不改) | `mashup/audio_map.json` |
| `transcript-aligner` | 一句一拍切语义时间轴,吸附停顿,盖章 | `mashup/beat_track.json` |
| `shot-designer` | 焦点→模式→意象→拍内切镜定节奏,聚簇 | `directing/{ep}/shot_list.json` 等 |
| `footage-scout` ∥ | catalog 通读素材库,逐组语义匹配候选(零网络) | `mashup/candidates/*.json` |
| `footage-curator` 👁 | 逐图核验选片(按 mode 分口径),源片绝对秒定 rough,分片登记台账;merge 收口单合并盖章 | `mashup/picks/bNN.json`、`sources/bNN.json`;merge 产全局 `picks.json`、`sources.json` |
| `clip-cutter` ∥ | 库源片直切+零公差精剪归一;still 兜底执行 | `assets/clips/{ep}/grpNNN.mp4` |
| `11-qa/mashup-qa` ∥ | 终审:机检全量+按 mode 语义抽检+复用抽检+台账终核 | `qa/reports/{ep}/mashup.json` |

(∥=stateless 可并发;👁=须带视觉能力引擎)
**复用内置零改动**:`10-editing/edit`/`subtitle`/`thumbnail`、
`11-qa/visual-qa`/`content-safety`/`copyright`/`logic-qa`,
以及主流程 p0 与 p1 structure/events(01-story,产 story_graph 节奏参考;02-worldbuilding 自 v4.1 不再使用)。
刻意不复用 `08-video-gen/*` 与 `06-art/*`(prompt 机检链对检索流程不适用)。

## 用法

1. **备素材库**:设置 → 高级 → 素材库,上传/下载源视频,等切分+字幕+AI 分析全部完成
   (建议 info 覆盖率 100%)
2. **新建项目**:顶栏项目下拉「＋ 新建项目…」→ 第一步选 ✂️ **混剪**
3. **基础信息**:粘贴文稿(写入 `refs/text/transcript.txt`,同一份写入 `novel/original.txt` 作 p0 的文本输入)、
   勾选素材库(可多选,记在 `settings.json#project.footage_libraries`)、讲述音频可选(上传到 `refs/audio/`;
   不给音频就按文稿 TTS 合成母带,声线/语气可写在「内容定位」里,需「生成模型」页已配 TTS 渠道)
4. **开工**:总制片核对素材并汇报缺项,素材齐了回复开工,它按本流程立项,并行跑 p0–p1 剧情理解与 mx0 摄入对齐,
   再逐拍分镜设计,出时间轴与节奏基线请你签 MH1

## 自检命令

```bash
python3 modules/footage.py doctor                                        # 先跑(列素材库)
python3 modules/footage.py catalog --library <name> --brief              # 看库内分镜概貌
python3 code/check_footage.py --project <slug> --ep ep01                 # 自动判阶段
python3 code/check_footage.py --project <slug> --ep ep01 --require final # 成片终审
python3 services/runtime/dagcheck.py --project <slug> --strict
```

## v4 不做

**联网找素材**(素材只出自本地库)、多集(一个 MP3 = 一个项目 = ep01)、
BGM/音效叠加(破坏零重编码)、片头片尾、画质增强/调色统一(素材异源色调不一是
混剪美学预期)、专属预览页(分镜预览页够用)、变速、字幕逐字精度(跟拍,只收
cue end)、转场类型扩展(仅 dissolve 0.3s 两情形——相邻组同 clip 复用、连续静帧
组之间;其余边界一律硬切,快切节奏仍靠硬切)。
(v3 的「不做 ASR 强对齐」条款已废止——v4 主路径就是它。)

## 建议

素材库的 **info 覆盖率直接决定选片质量**——开工前在素材库页把「AI 分析」跑完
(scout 语义匹配的主检索面就是 info 四行);库的题材要与文稿对得上
(讲发布会的稿配发布会库),325 组配 213 clips 这种「组多库少」的项目,
复用与兜底档是常态,预期管理好。最可能卡住的两处:某段文稿的意象在全部库里
都没有(curator 走兜底或上报补库,不许硬凑)、库源片被移动/删除
(cutter ffprobe 失败即上报,重新导库)。
