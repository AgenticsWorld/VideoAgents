# WORKFLOW.md — 小说 → 视频 多 Agent 流水线总纲

> 输入:一本小说(txt/epub,按章节存放)。
> 输出:可发布的成片(分集视频 + 字幕 + 封面 + 各平台包)。
> 团队:83 个 Agent,13 个类别,目录见 `agents/README.md`;可经插件扩展新工位与业务流程(§10,如衍生小说创作插件 `plugins/derivative-fiction/`)。
> 本文档是唯一的流程权威(single source of truth for process);各 Agent 的职责细节见其目录下的 `SOUL.md`。

---

## 1. 核心原则

1. **单一事实源**:所有世界观/角色/场景设定只存在于 Project Bible(`bible/`),由 `memory-bible` 唯一管理。任何 Agent 发现冲突只能上报,不得擅自改 Bible。
2. **产物皆文件、皆有版本**:每个 Agent 的输出是落盘文件(JSON/MD/媒体),由 `version` Agent 版本化,不可变(修改 = 新版本)。
3. **任务皆工单**:Orchestrator 用统一的 Work Order(见 §6)派活;Agent 只做工单里的事。
4. **质量三道闸**:机器校验(schema/指标)→ Evaluation 评分(rubric,阈值 80)→ 专项 QA Agent 审核。不过关自动带意见退回,最多重做 1 次(默认;用户可在设置「高级→Agent 高级设置→重跑次数」全局改,0=不自动重跑,运行提示词「用户重跑次数设定」注入的值覆盖本文档所有写死的 3 次/≤3 次/max_retries: 3),仍不过升级人工。
5. **上下文按需组装**:Agent 不读全库。Context Package 分两级(2026-08-01,教训:每单必调 context Agent 一次约 4 分钟,shixibook 310 张工单调了 395 次):**full 包**由 `context` Agent 裁剪(该任务需要的 Bible 片段 + 上游产物 + 缺陷历史),仅限三类工单——① `attempt > 1` 重做单(必须附上次失败原因与 evaluation 逐条意见);② 需**跨文件摘要裁剪**的工单——所需上下文**无法用明确文件路径清单表达**、必须摘要/裁剪进 token 预算时才算;「创作类/涉及设定」本身不是升 full 的理由,所需 Bible 与上游文件能以明确路径列进 `inputs` 的(执行 Agent 直读当前受控版),一律 inline(2026-08-02 教训:衍生小说链 52 单全按「创作类需裁剪」升 full,白名单形同虚设);③ 需聚合缺陷历史的工单(qa/defects 存在与该产物/该 Agent 相关的 open 缺陷)。其余工单走 **inline 轻量包**:orchestrator 派单时把输入文件路径清单 + 硬约束直接写进工单 `instruction`/`inputs`,`context_package: inline`,不调用 context Agent。**打包防重复**:同一工单已有未过期 `context.md` 时禁止重新打包,重打仅限 attempt 递增或 inputs 实质变更,且优先增量更新而非整包重建;章节/镜头/场景级扇出任务若确需 full,共享一份批次底包(如 `runs/<批次>-base/context.md`),逐实例只补该实例的增量,禁止逐实例复制同质全量包(2026-08-02 教训:nv3 二十三章草稿同刻各打一份近似全量包)。
6. **人工确认点(H1–H5 + H1A/H3A/H3B)不可跳过**:世界圣经、**角色与资产(H1A)**、美术风格、首集剧本、**每集分镜(H3A)**、**每集视觉生成(H3B)**、首集成片、发布,均需用户签字;其中分镜确认与视觉生成确认为每集一次——用户在控制台「分镜设定」预览页审看分镜/生成组划分并签字后,该集才允许进入 Phase 7 视频生成;本集全部生成组机检/抽检通过后,用户在「视频预览」页审看组 clip 并签字(H3B),该集才允许进入 Phase 9 剪辑合成。
7. **用户全局时长设定优先**:每集目标时长与单个分镜时长范围由用户在 Web 控制台「⏱ 时长设置」配置(默认每集 10 分钟、单镜 1–10 秒),运行时注入各 Agent 系统提示词;episode-planner 的每集预算、storyboard/shot-planning 的每镜时长必须以此为准,本文档各表中的具体秒数(如 180s/集、4.0s/镜)仅为示例。每集时长可设为「根据剧本自动」(settings.json `duration.episode_minutes: "auto"`):此时不设固定每集预算,episode-planner 按剧情结构自行决定集数与每集时长并在 episode_plan 中写明各集实际预算,pacing/edit 以 episode_plan 实际预算为基准。
8. **视频按生成组产出**:相邻同场景镜头打包为「生成组」(Σ时长 ≤项目「视频模型设置」的组时长上限,整数秒;默认 15s=Seedance 2.0 单次生成上限,仅当视频模型为 Seedance 2.5 且用户调高该设置时最高 30s——本文档余下部分出现的 15s 组上限示例值均指该设置的默认值,以系统提示词注入的项目实际设置为准),一组一次 Seedance 多镜头生成(见 §4 Phase 6/7 与 §9);组 clip 是一级产物,镜级时长是节奏意图而非硬约束。
9. **输出文件命名仅限英文与数字(2026-07-20)**:所有 Agent 落盘的文件名与目录名只准使用英文字母(a-z/A-Z)、数字(0-9)及分隔符 `-`/`_`/`.`,**禁止中文及其他任何非 ASCII 字符**——中文文件名在 ffmpeg/对象存储预签名 URL/跨平台路径处理中随时炸链。角色/场景等实体一律用 ID 或拼音/英文 slug 入文件名(如 `CHAR-0001_voiceprint.mp3`,不是 `林风_声纹.mp3`);机检项 ascii_filename,命中非 ASCII 文件名直接退回。本条只限**文件名**,文件内容(JSON 字段值、字幕、剧本)不受限。用户放入 `novel/`、`refs/` 的输入文件不强制,但引用时应先规范化改名。
10. **制作必需字段禁 UNKNOWN,缺失自行发挥补全(2026-08-04)**:输入文本对出场人物/场景/道具没有具体描述是常态(短篇尤甚;10days003 前科:世界观/场景/道具卡大量 `UNKNOWN` 占位,下游概念图与 prompt 无锚可依)。分层处理——**事实抽取层**(01-story:novel-parser/event)判不准照旧如实标 UNKNOWN,那是留给设定层的补全信号,不是终值;**设定/设计层**(02-worldbuilding、03-characters、04-creatures、05-scenes、06-art)凡产出制作必需字段(绘图/视频生成/配音要直接消费的外观、材质、光照、色彩、尺寸、声线等规格),遇原文与上游均无依据(含上游标 UNKNOWN)时,**先自行发挥设计出定值,再继续往后执行**:设计须与已有 Bible/style.json/题材惯例自洽,标 `inferred: true` + `reason`(写明设计依据),不得以信息缺失为由留白、停摆、上报或退单。**机检 `no_unknown_placeholder`**:设定层产物的制作必需字段值出现 UNKNOWN/未知/待定/TBD/N-A 或留空即退回。两条边界:① 原文或 Bible 已有定值的字段禁止发挥,以原文为准(原则 1);② 剧情悬念(原文**刻意不揭示**的信息,如幕后黑手身份、密室角色感知不到时间)不算信息缺失——如实记为剧情事实并写明「原文刻意未揭示」,但同一实体的制作必需字段仍须给出定值呈现方案(谜面也要能画出来)。自主设计随 H1/H1A/H2 签字转正,与原文抽取的设定同等受控;后文发现原文依据与之冲突,上报 memory-bible 走变更流程。

---

## 2. 项目数据布局

每个项目一个工作目录:

```
data/projects/<slug>/
├── novel/          # 输入:小说原文(按章节)
├── refs/           # 输入:用户放置的参考图(视觉风格/角色/场景/道具)、音乐与文本资料(见下方约定)
├── story/          # chapter_manifest.json(章节批清单), structured_story.json(总表),
│                   # structured_story/(chNNN.json 章节分片,供按章裁剪), story_graph.json,
│                   # events.json, story_timeline.json, episodes/ep01/screenplay.md ...
├── bible/          # 世界圣经:world.json, timeline.json, geography.json, religion.json,
│                   # culture.json, politics.json, economy.json, cultivation.json,
│                   # dictionary.json, characters/<id>/*.json, creatures/, scenes/,
│                   # style.json, props.json, costumes.json, color_script.json
├── directing/      # ep01/directing_plan.md, storyboard.json(含 groups_draft),
│                   # shot_list.json(含 generation_groups), continuity_plan.json,
│                   # shots/sh014/{camera,composition,blocking}.json
├── assets/         # prompts/(镜级锚点图 prompt shNNN.json + 组级视频 prompt grpNNN.json),
│                   # keyframes/(组锚点包:meta.json 清单+新生成锚图;复用锚只登记引用
│                   #   不复制副本,2026-08-24 引用化), clips/(组 clip grpNNN.mp4 + meta),
│                   # sketches/(用户手绘分镜 epNN/grpNNN/,经控制台「✏️ 手绘分镜」手机扫码绘制;
│                   #   2026-07-09 规则:原稿不直接进视频 refs——image-generation 先据手绘稿渲染
│                   #   风格化图像 anchor_sketch_*.png 入锚点包,组视频以该生成图为参考图;
│                   #   渲染图默认 [Image N] 软引用、严禁默认作整组 first_frame,硬锁须拆段+批准,见 §7A),
│                   # audio/{voice,narration,bgm,sfx,ambience}/
│                   #   voice/casting.json=项目级选角注册表(角色×形态→tts_model+tts_voice,全片唯一事实源),
│                   #   voice/refs/=项目级音色样本;voice/epNN/ 只放集级产物 lines/ 与 patches/
│                   #   (2026-07-12 改版:选角登记从 epNN/refs/manifest.json 上收到项目级,治跨集音色漂移)
│                   #   voice/epNN/dub/grpNNN/=后期配音逐句 TTS + dub_manifest.json(仅项目「对白配音=后期配音」,§8C;
│                   #   clips/epNN/grpNNN.native_audio.wav 为替换前的原生轨备份)
├── edit/           # ep01/{timeline.json, cut_v1.mp4, subtitles.srt, subtitles_final.srt,
│                   # captions.json, intro_outro/, thumbnail.png, final.mp4}
│                   #   subtitles.srt=正片(cut)0 秒基准;subtitles_final.srt=成片基准
│                   #   (接入片头后整体 +intro 实测时长,烧录/发布一律用 final 版)
├── qa/             # reports/, defects/(缺陷工单)
├── publish/        # <platform>/package/, seo.json, metadata.json, receipts/
├── code/           # 本项目的一次性制作脚本(Agent 为完成任务写的脚本,内嵌本项目创作数据)
└── runs/           # 工单、Context Package、评分记录、日志(runs/<task_id>/)
```

**项目制作脚本约定(code/)**:Agent 为某任务编写的一次性脚本(批量出图/合成、机检、媒体处理等**确有计算或外部调用**的脚本)
是项目产物,落 `data/projects/<slug>/code/` 并与其它产物一样用 `.version/vc.py register` 登记;
**不要**写到仓库根 `code/`(那里只放项目无关的通用工具,共享库在 `modules/`),也**不要**散落在 `runs/<task_id>/`
(那里只放运行记录四件套 + 可选 lesson.md,§6.1)。脚本内定位仓库根
用「向上找 modules/」标准头(见根 `code/README.md`),禁止硬编码绝对路径。
**宿主 `code/` 下的 CLI(`render_blocking_map.py`、`finalize_episode.py`、`render_captions.py`、各机检脚本等)只准按其用法调用:禁止复制到项目 `code/`、禁止改写成项目本地版本、禁止自写同功能替代脚本**(前科 2026-08-26 polan2:agent 重写了动线图渲染器,产物偏离规范);宿主脚本报错或功能不合需求 = 上报 orchestrator,由宿主侧修改(2026-08-27)。

**静态数据产物直接落盘,禁止「写脚本去写 JSON」(2026-08-16)**:JSON/MD/YAML 类设计产物(每镜 camera/composition/blocking、
shot_list、cue 表、prompt 组包等)的内容全部来自 Agent 自己的判断,没有任何需要程序计算的部分——一律用文件写入工具**逐份直接写出最终文件**;
严禁先把数据写成 Python dict/字面量脚本再执行脚本落盘(等于同一内容输出两遍,还多一轮读改跑),严禁为此分批写多个 `gen_*.py`。
只有产物确需计算(时长/坐标推导、扫描目录、调 genmedia/ffmpeg、跨文件机检)时才写脚本,且落 `code/`。
批处理工单(for_each 一次执行 N 份)同样逐份直写、一次做完,不按 2–3 份一批拆多轮。**同批产物间的共用说明(输入清单、坐标系定义、
画幅/安全区约定等)不得逐份复制进每个文件**——写在 SOUL/上游文件里的引用路径即可,或至多在回执 result.json 写一份;
单份产物只含 SOUL 输出表规定的字段与本实例特有的值(前科 2026-08-16 archigram p6-composition-ep01:13 镜先写 7 个 gen 脚本共 190KB
分 6 批跑,单份 20KB 中近半是逐份复制的共用说明,耗时 3712s = 同批 camera/blocking 单的 4 倍,产物合格但方法选贵了)。

### 用户参考目录 refs/(人工输入口)

用户把「希望成片长成什么样」的参考图、希望使用的音频与文本资料放进本目录(入口:Web 控制台顶栏「预览设定产物」菜单 →【参考文件】页,按分类上传并逐文件填写注释;需要引导用户补参考素材时,一律指引其进该页面,不要让用户手动开文件夹),视觉设定类 Agent 与配乐 Agent **必须先查看、优先参考/选用**:

```
refs/
├── style/        # 整体视觉风格:画风/渲染质感/色调/构图(截图、画集、他人作品均可)
├── characters/   # 角色形象参考;按角色建子目录(refs/characters/<角色名或id>/)则定向生效
├── scenes/       # 场景与世界观:建筑/地貌/氛围参考
├── props/        # 道具/服装/法宝参考(服装可建 costumes/ 子目录)
├── music/        # 用户希望使用的音频文件(背景音轨,BGM 候选,mp3/wav/flac 等)
├── video/        # 参考视频:动作/运镜/节奏/转场范例(mp4/mov/webm),视频生成 Agent 优先参考
├── thumbnail/    # 封面参考:他人爆款封面/构图/版式/文字风格范例(thumbnail Agent 优先参考)
├── text/         # 文本资料:设定/文案等文本文件(txt/md 等),相关 Agent 参考使用
└── NOTES.md      # 逐文件注释(哪个文件管什么、想用在哪);有则必读——用户在【参考文件】页逐文件填写,
                  # 自动写入本文件标记块(机器可读版 annotations.json);用户手写内容(标记块外)同样有效
```

**使用规则**:
1. **优先级**:用户参考图 > Agent 自行发挥。风格类决策(style.json、色彩、画风)与参考图冲突时,以参考图为准;与文字设定(Bible)冲突时上报用户裁决,不擅自取舍。
2. **落痕迹**:凡参考了 refs/ 的产物,须在其 meta/prompts.json 里记录所用参考图路径(`user_refs` 字段);art-director 在 style.json 中写明每张风格参考图影响了哪些决策。
3. **直接注入**:生成图像时把命中的参考图经 `genmedia --ref` 传入(见 §9);角色参考图同时作为 character-concept 三视图和 character-consistency 校正的形象锚点之一。
4. **目录为空不阻塞**:照常自行设计;但 art-director 应在 H2 确认时提醒用户「可从预览菜单进入【参考文件】页上传参考图后重跑风格」。
5. **匹配规则**:characters/ 下按子目录名对角色名/角色 id 做模糊匹配;散放在 refs/ 根目录的图一律视为整体风格参考。
6. **用户音乐**:`refs/music/` 有文件时,配乐 Agent(`09-audio/music`)必须先逐曲试听分析(曲风/情绪/节奏/时长),再对照本集情绪曲线自行判断每首曲子适合用在视频的哪些位置(哪些场次/情绪段),优先选用用户音乐,不足的段落才生成补齐;NOTES.md 指定了用途的按指定执行。选用情况(含未选用及原因)写入 cue sheet,`license.source` 记 `user_provided` 并如实标注来源文件路径,版权仍由 `11-qa/copyright` 终审。
7. **封面参考**:`refs/thumbnail/` 有图时,封面 Agent(`10-editing/thumbnail`)必须先逐图分析可借鉴点(构图/主体占比/文字位置与字重/色彩策略),作为 A/B 版设计的优先依据,并在送选清单 `user_refs` 字段落痕迹;NOTES.md 指定了用法的按指定执行。
8. **参考视频**:`refs/video/` 有文件时,视频生成类 Agent(`08-video-gen/*`)必须先逐段查看分析可借鉴点(动作/运镜/节奏/转场),按 NOTES.md 注释对位到相应镜头/生成组;所选视频模型支持参考视频时经 `genmedia.py video --ref-video` 注入(Seedance 2.x 等,受该模型的数量/时长上限约束),不支持时作为提示词描述的依据;所用路径记入产物 meta/prompts.json 的 `user_refs` 字段。

---

## 3. 总流程 DAG(阶段视图)

```
                          小说原文
                             │
              ┌──── Phase 0 摄入与立项 ────┐
              │  orchestrator / version /  │
              │  memory-bible / novel-parser│
              └─────────────┬──────────────┘
                        [G0 闸门]
                             │
                 Phase 1 剧情理解(并行)
            story-structure / event / timeline-story
                             │
                        [G1 闸门]
                             │
                 Phase 2 世界圣经(9 Agent 并行)
      world/timeline/geography/religion/culture/political/
              economy/magic-cultivation/dictionary
                → memory-bible 合并 → [G2 + H1 人工确认]
                             │
                 Phase 3 角色与资产
     character-manager →(appearance/growth/personality/
       relationship/voiceprint/dialogue-style 并行)
     creature/mount;scene →(environment/architecture/lighting)
                  [G3 + H1A 人工确认]
        (Phase 4 概念图:character-concept / costume-concept / environment-concept / prop / creature-concept)
                             │
          ┌──────────────────┴───────────────────┐
   Phase 4 美术风格                        Phase 5 剧本改编
   art-director →(character-concept/       episode-planner →(每集:
   environment-concept/prop/costume/        screenplay→dialogue-rewrite→
   color-script/aspect-ratio)               narration→hook→pacing)
   [G4 + H2 人工确认]                       [G5 + H3 人工确认(第1集)]
          └──────────────────┬───────────────────┘
                             │(以下按集推进,首集为试点)
                 Phase 6 导演分镜(每集)
        director → storyboard → shot-planning →
        (camera-movement/composition/cinematography/blocking 每镜并行)
        → continuity-planning   [G6 闸门 + H3A 分镜确认(每集签字)]
                             │
          ┌──────────────────┴───────────────────┐
   Phase 7 视觉生成(每组流水)              Phase 8 音频(每集)
   prompt → image-generation →              sfx-cue/ambience-cue/音色样本/
   character-consistency →                  旁白轨(先于 p7-video)→ music(后期)
   video-generation(组序串行,原生音频)      → audio-mixing(原生轨+BGM+旁白,
   → [dub 后期配音,仅设置开启 §8C]            依赖全组 p7-video)[G8 闸门]
   → lip-sync(兜底)/animation → upscale
   [G7 闸门 + H3B 视觉生成确认(每集签字),人工抽检 10%]
          └──────────────────┬───────────────────┘
                 Phase 9 剪辑合成(每集)
        edit → transition → subtitle/caption → title → thumbnail
                    [G9 闸门 + H4 首集人工确认]
                             │
                 Phase 10 终审(8 个 QA 并行)
     logic-qa / character-consistency-qa / timeline-qa /
     world-consistency-qa / visual-qa / audio-qa /
     content-safety / copyright        [G10 + H5 发布签字]
                             │
                 Phase 11 发布
     platform-adapter →(seo + metadata 并行)→ publisher
```

调度层 5 个 Agent(orchestrator / memory-bible / context / version / evaluation)贯穿全程,不属于任何单一 Phase。

### 3.1 DAG 按集动态展开(强制)

立项时生成的 dag.json 允许先用**阶段级模板节点**(p0–p11 各一套,不带集号)。但 `story/episode_plan.json` 通过 G5 后,orchestrator 必须**立即把「按集推进」的阶段展开为逐集节点**,此后模板节点不得再承接任何工单:

1. **展开范围**:Phase 5 的每集任务(screenplay/dialogue/narration/hook/pacing)与 Phase 6–10 全部节点,按 `pX-<task>-epNN` 逐集生成;每集自带闸门节点 `g6-epNN`(H3A 分镜签字,human)、`g7-epNN`(H3B 视觉生成签字,human)、`g8-epNN`、`g9-epNN`(H4 仅首集 human)、`g10-epNN`(H5 发布签字,human)。Phase 11 按发布单元展开。集间依赖遵循试点策略(首集全链路过 H4 后,后续集方可批量推进)。
2. **模板节点处置**:被展开覆盖的模板节点(如 `p6-plan`、`g6`)在展开时置为 `state: expanded` 并在 `note` 里指向逐集节点;**严禁拿单套模板节点跨集复用**——首集跑完把模板标 passed、后续集工单游离于 DAG 之外,会让空转看门狗失明。
3. **补录回填**:若展开时该集已有既往工单(历史修复场景),按 `runs/<task_id>/` 实际记录回填节点 `state` 与 `run_id`,changelog 标注 `backfill`。
4. **一致性机检**:episode_plan 中的每个 epNN 在 DAG 中必须至少有一个节点;未完结的 epNN 必须存在可跑或待签节点。任一不满足即视为调度缺陷(同 §6.1 三方不一致)。Web 控制台空转看门狗会对「plan 有集、DAG 无节点」自动告警并唤醒 orchestrator 补展开。

同理,`dynamic_expansion` 的其余维度(chapter_batch、shot/组、character、scene、platform)在对应索引产物冻结后按需展开(chapter_batch 在 `story/chapter_manifest.json` 通过 p0-scan 校验后展开为 `p0-parse-bNN`),粒度至少到能让「依赖已满足的待办节点」反映真实前沿为止。

### 3.2 dag.json 规范格式(强制)

`runs/dag.json` 由 orchestrator 生成与维护,**必须严格按以下结构落盘,不得增改键名**(历史项目曾出现 `nodes` 字典、`tasks`+`task_id` 等自创变体,导致空转看门狗解析失败、自动运行静默失明):

```json
{
  "project": "<slug>",
  "workflow_version": 1,
  "generated_by": "00-orchestration/workflow-orchestrator",
  "generated_at": "<ISO8601>",
  "nodes": [
    {
      "id": "p1-structure",
      "agent": "01-story/story-structure",
      "state": "pending",
      "depends_on": ["g0"],
      "human": false,
      "run_id": null,
      "outputs": ["story/story_graph.json"],
      "gate": null,
      "note": ""
    }
  ]
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `nodes` | ✓ | **列表**,顶层唯一的节点容器(不是 `tasks`,不是字典) |
| `id` | ✓ | 节点唯一标识,`depends_on` 引用的锚(不是 `task_id`) |
| `state` | ✓ | 枚举:`pending / dispatched / running / failed / blocked / done / passed / passed_human_override / expanded / template / skipped / cancelled / waived` |
| `depends_on` | ✓ | 依赖节点 id 列表(可为空 `[]`);**每个引用必须存在于 DAG 中且无环** |
| `agent` |  | 承接的执行 Agent(闸门节点可为 null) |
| `human` |  | `true` 表示人工签字节点;签字后可扩展为裁决记录对象(`decision`/`signed_by`/`signed_at`) |
| `run_id` / `outputs` / `gate` / `note` |  | 收尾钩子回填的运行 id、产物路径、闸门结果、备注 |

**机检(强制)**:立项生成 DAG 后、以及每次修改 dag.json 后,必须运行 `python3 services/runtime/dagcheck.py --project <slug> --strict` 并通过,才算完成(校验:结构合规、id 唯一、state 合法、依赖引用存在、无环)。机检责任在**写入方**(orchestrator);自动运行看门狗不做结构校验,仅在 dag.json 缺失或解析不出任何节点时唤醒 orchestrator 修复。存量旧格式项目只读兼容,但任何**重写/新建**的 dag.json 一律按本节格式。

---

## 4. 分阶段明细:发给谁、什么指令、怎么校验

每张表的「校验」列按三道闸展开:**机检**(自动)/ **评分**(evaluation Agent,rubric 阈值 80——实际以项目「审核设置·质量评委」为准,0=跳过评分不派 evaluation 单)/ **QA**(专项审核 Agent 或人工)。

### Phase 0 — 摄入与立项

| Agent | 工作指令(要点) | 输入 | 输出 | 校验 |
|---|---|---|---|---|
| workflow-orchestrator | 为小说 `<slug>` 立项:初始化目录、生成全流程 DAG、登记全部工单 | 小说原文、本文档、workflow.yaml | `<项目目录>/runs/dag.json`、工单队列 | 机检:DAG 无环、每个任务的依赖/产物路径合法 |
| version | 初始化项目版本库,登记基线 | 项目目录 | 版本库 + changelog | 机检:能记录/回滚任一产物 |
| memory-bible | 初始化空 Bible 骨架与写入规则 | 项目目录 | `bible/` 骨架 | 机检:骨架 schema 齐全 |
| novel-parser(p0-scan) | 扫描 `novel/` 章节文件并分批:整章归组,每批 1–1.5 万字,短章合并、超长章独立成批,**不拆章** | `novel/`(仅目录与字数,不读正文) | `story/chapter_manifest.json` | 机检:schema;章节文件覆盖率 100%;批字数在目标区间 |
| novel-parser(p0-parse,每章节批并行) | 解析本批章节:章节切分、场景切分、对白提取(带说话人)、实体标注(人/地/物/招式);跨批指代判不准标 UNKNOWN | 本批章节原文、chapter_manifest | `story/structured_story/chNNN.json`(每章一分片) | 机检:分片 schema;本批章节覆盖率 100%;说话人缺失率 <2%。评分:extraction_v1 ≥85(按批)。QA:抽样 3 章人工比对原文 |
| novel-parser(p0-merge) | 按 manifest 顺序把分片机械拼装为总表;只拼装校验不改写,分片缺漏退回对应批 | 全部章节分片、chapter_manifest | `story/structured_story.json`(下游契约不变;分片保留) | 机检:总表 schema;全书章节覆盖率 100%(章数+总字数与 `novel/` 对账);ID 全局唯一;全书 UNKNOWN <2% |

> 分章 map + 全局 merge(2026-07-23 改版):治整本单任务「原文+产物」上下文溢出;不设字数阈值分支,短篇即少数几批,统一走此路,粒度由 p0-scan 分批逻辑控制。

**G0 闸门**:structured_story 通过全部校验,后续任务才解锁。

### Phase 1 — 剧情理解(并行)

| Agent | 工作指令(要点) | 输入 | 输出 | 校验 |
|---|---|---|---|---|
| story-structure | 分析全书结构:幕/弧线、主线支线、伏笔与回收点 | structured_story | `story/story_graph.json` | 机检:每个节点引用合法章节。评分:analysis_v1。QA:logic-qa 预审(伏笔无回收须显式标注) |
| event | 提取事件卡:时间/地点/人物/起因/经过/结果/因果链 | structured_story | `story/events.json` | 机检:人物/地点字段非空、事件 ID 唯一。评分:extraction_v1。QA:因果链断裂清单人工抽查 |
| timeline-story | 建立叙事顺序 vs 故事时间双轴,标注闪回/插叙 | structured_story、events | `story/story_timeline.json` | 机检:时间轴无矛盾(先死后活类冲突=0)。QA:timeline-qa 预审 |

**G1 闸门**:三者互查通过(事件均能挂上时间轴与结构图)。

### Phase 2 — 世界圣经(9 Agent 并行 → 合并)

统一指令模板:「从 structured_story + events 中抽取你负责的领域设定;**每条设定必须注明原文出处(章节)**;原文没写但制作必需的,标 `inferred: true` 并给推断理由;**原文与上游均无依据的制作必需字段,先自行发挥设计定值(与已有设定自洽)再继续,禁止 UNKNOWN/未知/待定占位(§1 原则 10,机检 no_unknown_placeholder)**;术语以 dictionary 为准。」

| Agent | 领域 | 输出 | 校验 |
|---|---|---|---|
| world | 世界总览、国家、势力 | `bible/world.json` | 共用:机检 schema + 出处字段必填;评分 extraction_v1 ≥80;QA:world-consistency-qa 对 9 份文件交叉审(同一事实两处说法不一 = 缺陷单) |
| timeline | 世界史、纪年、大事件 | `bible/timeline.json` | 同上 + timeline-qa 审纪年自洽 |
| geography | 地形、山川、城市布局 | `bible/geography.json` | 同上 + 与 world.json 地名互查 |
| religion | 宗教、神明、信仰 | `bible/religion.json` | 同上 |
| culture | 风俗、语言、礼仪 | `bible/culture.json` | 同上 |
| political | 政体、阵营、外交关系 | `bible/politics.json` | 同上 + 与 world 势力表对齐 |
| economy | 货币、贸易、物价 | `bible/economy.json` | 同上 |
| magic-cultivation | 修炼/力量体系、等级、技能 | `bible/cultivation.json` | 同上 + 等级体系单调性检查(境界排序无环) |
| dictionary | 全库专有名词统一释义 | `bible/dictionary.json` | 机检:词条唯一;QA:其余 8 份文件的术语 100% 能在词典命中 |
| memory-bible | 将 9 份文件合并为 Bible v1,解决冲突或上报 | 上述 9 份 | `bible/` v1 + changelog | 机检:合并后交叉引用完整——`cross_refs_valid` 必须覆盖**全部 ID 命名空间**(事件 `ev*`、角色 `CHAR-*`、场景 `SCN-*`、地名/势力/术语),任何引用指向不存在的 ID 即 FAIL,禁止只查部分命名空间的空洞通过。QA:world-consistency-qa 终审 |

**G2 闸门 + H1 人工确认**:用户审阅 Bible 摘要并签字。签字后 Bible 进入受控状态(改动需走变更流程)。

### Phase 3 — 角色与资产

> 本阶段与 Phase 4 同受 **§1 原则 10** 约束:原文对出场角色/生物/场景/道具没有具体描述时(仅出场无刻画是常态),各设定 Agent **自行发挥设计定值**并标 `inferred: true` + 设计依据,然后继续往后执行;制作必需字段禁止 UNKNOWN/未知/待定占位(机检 no_unknown_placeholder,G3/G4 前退回补全)。

| Agent | 工作指令(要点) | 输入 | 输出 | 校验 |
|---|---|---|---|---|
| character-manager | 注册全部角色:唯一 ID、别名/曾用名合并、戏份分级(S/A/B/群演) | structured_story、events | `bible/characters/index.json` | 机检:ID 唯一、别名无二义。QA:character-consistency-qa 抽查重名合并正确性 |
| appearance | 每个 S/A/B 级角色的外观卡(性别、发色、瞳色、体型、标志物…可直接喂给绘图;gender 必填「男/女」,原文未写也须推断定值,易装角色另附 presented_gender) | index + 原文出处 | `bible/characters/<id>/appearance.json` | 机检:必填字段齐(含 gender);评分 extraction_v1;QA:与原文描写冲突 = 缺陷单 |
| character-growth | 有年龄跨度的角色的分龄形象版本 | appearance、story_timeline | `<id>/age_versions.json` | 机检:每版本挂在合法时间轴区间 |
| personality | 性格、动机、行为习惯、禁忌 | structured_story | `<id>/personality.json` | 评分 analysis_v1;QA:logic-qa 抽查「性格-行为」矛盾 |
| relationship | 全角色关系图(类型、强度、随剧情的变化) | events、index | `bible/characters/relationship.json` | 机检:边引用合法 ID;QA:关键关系与原文抽样比对 |
| voiceprint | 每个有台词角色的声音设定(音色、语速、口音、参考声线;跨龄角色按 age_versions 分版)——**设定卡完成即触发选角落地:orchestrator 随后派 09-audio/voice-generation 登记 casting.json 并合成该角色×各年龄形态的 voiceprint 样本(§8A 2026-07-20,人物 Voice 在人物设定阶段完成,不等到集级)** | personality、appearance | `<id>/voice.json`(下游链产 casting 条目 + `voice/refs/` 样本) | 机检:字段齐;QA:audio-qa 审「声音-人设」匹配度 |
| dialogue-style | 每个角色的说话风格卡(口头禅、句式、用词禁区) | structured_story 对白集 | `<id>/dialogue_style.json` | 评分 analysis_v1;QA:用原文台词回测风格卡命中率 ≥80% |
| creature | 妖兽/动物图鉴(形态、习性、战力);`bible/creatures/index.json` 是 CRE-* 权威登记处,登记条目即 Phase 4 `creature-concept` 的扇出实例(2026-08-26) | structured_story | `bible/creatures/creature.json` | 同 appearance 标准 |
| mount | 坐骑/飞禽设定(`anatomy`/`visual_identifiers`/`tack`/`endurance_state_log` 须可直接喂绘图,供 creature-concept 出图) | creature | `bible/creatures/mount.json` | 同上 |
| scene | 注册全部场景:唯一 ID、层级(地域>建筑>房间)、出场章节 | structured_story、geography | `bible/scenes/index.json` | 机检:ID 唯一、挂靠 geography 合法 |
| environment | 每场景的天气/季节/昼夜可变维度 | scene index、story_timeline | `bible/scenes/<id>/environment.json` | 机检:与时间轴一致(冬天的戏不能是盛夏场景) |
| architecture | 建筑风格卡(可喂给绘图) | scene、culture | `<id>/architecture.json` | QA:visual-qa 预审风格描述可执行性 |
| lighting | 每场景基准光照方案(日/夜/室内外) | scene、environment | `<id>/lighting.json` | QA:visual-qa 预审 |

**G3 闸门 + H1A 人工确认**:引用完整性全查——所有角色/场景子文件挂在合法 ID 上;character-consistency-qa 出报告;机检/QA 通过后,用户审看角色/生物/场景设定并签字,未签字不进 Phase 4/5。

### Phase 4 — 美术风格(依赖 G3)

| Agent | 工作指令(要点) | 输入 | 输出 | 校验 |
|---|---|---|---|---|
| art-director | 制定全片风格圣经:画风、渲染流派、参考片、负面清单(禁止元素);**先盘点 refs/,风格决策以用户参考图为准并在 style.json 记录对应关系** | Bible、用户偏好、**refs/(用户参考图,§2)** | `bible/style.json` | 评分 creative_v1;**H2 由用户签字锁定** |
| character-concept | 为 S/A 级角色出人设参考图;**整图单次生成、单张即锚(2026-08-04 二订)**:四格版式模板(`agents/06-art/character-concept/templates/character_sheet_template.png`,随平台分发;2026-08-14 版式改版:右侧两格特写并为一格通高大头像)作第一张 `--ref` 一次生成全身三视图+一格头肩大特写同框的整版 sheet,定稿 `<id>/sheet.png` 单张直接作下游视频参考,**不裁切子图**(front/side/back 多文件旧契约废止),替代逐视角多次生成;**`--n 1` 单张直出,禁多候选赛马(2026-08-04 三订,CHAR-0003 前科:多候选整批不满足风格)——自检不过或用户检查有意见时按缺陷/反馈定向重出一张,用户反馈是修改唯一驱动**;整图多视角同框的复制诱因由 p7 prompt 的 Identity lock 句+防重复长句硬约束兜住;**refs/characters/ 命中该角色的参考图必须经 --ref 注入(排在模板后)并记录** | appearance、style.json、refs/characters/ 与 refs/style/ | `assets/concepts/characters/<id>/` | 机检:与 appearance 字段逐项对照;sheet 四格版式齐全、2560x1440 起、主目录无单视角散图;QA:visual-qa + character-consistency-qa 打分 ≥80 |
| environment-concept | 关键场景**布局包(2026-08-19,替代单张主视角概念图;仅项目「输出设置 → 人物精确空间位置」开启时,默认开——关闭则沿用旧流程:主视角概念图 `main_*.png` + 昼夜变体,不出布局包、下列动线机检全部跳过)**:先写 `layout.json`(地图朝向、≥3 地标 `id/name_en/xy` 归一化坐标、九格机位语义 `views[1..9]`——每格 camera_from/looking_at/size/`angle∈{eye,low,high_oblique}`,**九格不出垂直俯视(俯视由 layout_top 专职)且须满足机位多样性规则:机位对两两不同、同一 looking_at ≤3、wide ≤4、close/detail ≥2、low ≥1、按地标 xy 算的方位角同一 ±30° 内 ≤3(2026-08-26,自核不入脚本;前科 polan 俯视格混入/七格同轴雷同)**),再出**俯视空间布局图** `layout_top.png`(top-down,无人无标注,≥2560x1440)与**9 宫格多角度场景图** `grid_9views.png`(俯视图 + `templates/scene_grid_template.png` 作 --ref 一次生成 3x3,逐格按 views 描述,九格与俯视图同一空间;昼夜变体 `grid_9views_<cond>.png`);**refs/scenes/ 命中的参考图经 --ref 注入并记录**。单方向场景图只覆盖一个朝向、分镜换机位场景就漂;俯视图给分镜师标人物站位/动线的底图,九格图给任何机位同一空间的参考 | scene、architecture、lighting、style、refs/scenes/ 与 refs/style/ | `assets/concepts/scenes/<id>/{layout_top.png,grid_9views.png,layout.json}` | 机检 `scene_layout_pack_ok`(`code/render_blocking_map.py --scene <id> --check-only`:三件齐全、两图 ≥2560x1440、地标 ≥3 且坐标合法、views tile 1..9 齐);QA:visual-qa 对照 style.json + 九格与俯视图空间一致 |
| prop | 武器/道具/法宝设定卡+参考图;优先参考 refs/props/;**剧情道具必填 `scale` 三字段(canonical_size 数值仲裁/relative_anchor 相对参照/prompt_token 全片唯一短语——内容语言随界面语言,2026-08-24)并出比例锚图 scale_ref_01.png(道具与无人尺度参照物同框)**——跨 clip 尺度一致性的源头锚;**道具图无人物红线(2026-08-18)**:道具全部参考图不得出现人物/身体局部/剪影,`--ref` 禁传角色/服装概念图——含人易触发渠道审核拒图,且人物抢占主体致道具信息稀释(cui3 前科:比例锚图全是角色手持小物),尺度改用桌面/门框/茶杯等参照物表达,身体相对尺寸只留在 relative_anchor/prompt_token 文字;**可手持的单面可读道具(手机/文书/照片/地图/典籍/符等)必标 `readable_face`(face_desc/back_desc/back_ref),其样式图 main_01.png 出成同一道具正/背双视图同框一张(整图单次生成,同角色 sheet 范式;侧面有信息的可三视图,不单出背面图,2026-08-25 二订)——正面平铺参考图偏置会把文字面/屏幕面喂向镜头,持读镜下游需背面视图可参照** | structured_story、style、refs/props/ | `bible/props.json` | 机检:关键道具(剧情道具)覆盖率 100%;剧情道具 scale 字段齐 + 比例锚图落盘(prop_scale_defined);道具图无人物(prop_image_no_person);readable_face 道具三子字段齐 + main 为双/三视图 sheet(prop_readable_face_defined,2026-08-25) |
| creature-concept | 为登记在册的生物/造物/坐骑(`bible/creatures/index.json` CRE-*)出形象参考图(2026-08-26):沿用角色四格版式整图单次生成,定稿 `<CRE-id>/sheet.png`(全身正/侧/背 + 头部大特写)单张即锚,**不裁切子图、`--n 1` 禁赛马**;**阶段变体必做**:详情卡多阶段(`creature.json` forms[] / `mount.json` endurance_state_log 与损伤进程)逐阶段各出 `sheet_<stage>.png` 并标注适用章节,差异不可见者写明免出理由;**坐骑图无骑手红线**(同道具无人物红线):不出现人物/骑手/身体局部,`--ref` 禁传角色概念图,鞍具按 `tack[]` 画在坐骑身上,人-兽尺度靠 `selection.json#scale.prompt_token` 文字锚、骑乘姿态由 p7 prompt 按 `riding_pose` 写入;目录键用 CRE-*(MNT-* 是 mount.json 内部编号,不跨文件引用);优先参考 refs/creatures/ | creatures/index.json、creature.json、mount.json、dictionary media_cues、style、refs/creatures/ | `assets/concepts/creatures/<CRE-id>/` | 机检:visual_identifiers/forms.appearance 逐项命中(creature_identifiers_match)、无人物(creature_image_no_person)、阶段变体齐或有免出理由(creature_stage_variants_ok)、sheet ≥2560×1440;QA:visual-qa ≥80;主要生物随 H2 交用户确认 |
| costume | 服装系统(按角色×场合×时期);优先参考 refs/props/costumes/ 与 refs/characters/;**2026-08-26 起 p4-char-concept 依赖本任务:定稿 sheet.png 着装 = 默认装 `visual_en` 逐字** | appearance、culture、story_timeline、refs/ | `bible/costumes.json` | QA:continuity 维度预审(换装点明确);机检 no_unknown_placeholder |
| costume-concept | **服装 sheet(2026-08-26)**:以该角色定稿 `sheet.png` 为形象锚(固定第二张 `--ref`),按 `bible/costumes.json` 该角色每套**非默认**服装各出一张整版四视图 `sheet_<COS-id>.png`(同四格版式、`--n 1` 禁赛马、≥2560×1440),prompt 逐字拼入该套 `visual_en`(图文同源,与 p7 costume_bound 注入串一致);默认装登记复用 `sheet.png`;复合躯体/共享外观的敞露态一并挂对方 sheet;所属时期无分龄基准 `sheet_<tag>.png` 的套装记 `blocked_on` 回派 character-concept;外观不可分辨/禁绘的套装记 `skip_reason`;台账 `costume_sheets.json` 是 p6 分镜挂图、§6A 审计、p7 refs、人物预览页「👕 服装」区取图的唯一入口 | costumes.json、`<id>/sheet.png`(+ 版本基准)、appearance/age_versions、style、refs/props/costumes/ | `assets/concepts/characters/<id>/sheet_<COS-id>.png` + `costume_sheets.json` | 机检:costume_sheet_coverage(每套有图或 skip/blocked 理由)、costume_visual_en_in_prompt、costume_sheet_identity_ref、四格/像素/ASCII 同角色 sheet;QA:visual-qa + character-consistency-qa ≥80(与 sheet.png 同一人);主角服装 sheet 随 H2 交用户确认 |
| color-script | 全片色彩曲线(每集/每幕的主色调与情绪);色调基准优先取自 refs/style/ | story_graph、episode_plan、refs/style/ | `bible/color_script.json` | 评分 creative_v1;QA:art-director 会签 |
| aspect-ratio | 决定画幅与分辨率矩阵(横/竖/多平台);**目标平台清单取自「📤 输出设置」发布平台多选(提示词注入,非口述猜测),母版画幅 = 输出设置主画幅(aspect_preset),平台矩阵须覆盖所选平台的全部画幅,与母版不同画幅的平台标裁切/缩放规则** | 「📤 输出设置」发布平台与主画幅 | `bible/aspect_ratio.json` | 机检:所选发布平台 100% 有条目、母版可派生各平台规格 |

> **概念图目录卫生(candidates 留档,2026-07-30)**:`assets/concepts/{characters,scenes,props,creatures}/<id>/` 主目录仅保留**最终采用的最新版本**图与 prompts.json/selection.json;落选候选、中间尝试、测试图(文件名含 candidate/attempt/test 或被新版替换的旧图)一律移入 `<id>/candidates/` 子目录留档,不删除以备追溯。下游按主目录整目录取图作形象锚(p7-image 锚点包、§6A 覆盖审计现货比对、§7E 修正取锚),弃用图混在主目录会被误取注入;`candidates/` 不计入 §6A 现货。五个概念 Agent(character-concept/costume-concept/environment-concept/prop/creature-concept,含 §6A 回派补图)出图挑选后即归位,重 roll 替换定稿时旧图先移入 `candidates/` 再落新图。

**G4 闸门 + H2 人工确认**:风格锁定。H2 时向用户展示「参考图 → 风格决策」对照(refs/ 为空则提醒用户可从预览菜单【参考文件】页上传参考图后重跑)。此后所有画面产物以 style.json 为准,改风格 = 走变更流程并评估重做成本。

### Phase 5 — 剧本改编(与 Phase 4 并行,依赖 G1/G2/G3)

| Agent | 工作指令(要点) | 输入 | 输出 | 校验 |
|---|---|---|---|---|
| episode-planner | 全书拆集:每集事件范围、目标时长、卡点位置;产出总表 | story_graph、events、pacing 约束、目标平台 | `story/episode_plan.json` | 机检:事件 100% 被分配且不重复;每集时长在预算内。评分 writing_v1 |
| screenplay(每集) | 把该集事件改写为剧本(场景标题/动作/对白/转场) | episode_plan、structured_story、Bible | `story/episodes/epNN/screenplay.md` | 机检:场景/角色引用合法 ID。评分 writing_v1 ≥80。QA:logic-qa 逐集审 |
| dialogue-rewrite(每集) | 优化对白:符合各角色 dialogue_style、口语化、时长可控 | screenplay、dialogue_style | 更新 screenplay 的对白层 | 机检:风格卡命中率 ≥80%;单句时长估算 ≤ 配音上限(`code/check_dialogue_fit.py --project --ep` 仅剧本层模式:line_le_cap = shot_max_s×0.7、line_est_consistent,2026-08-30);**分镜定稿后另有 p6-dialogue-fit 精简环节回到本岗(§7D ①′)** |
| narration(每集) | **仅「📤 输出设置」旁白开关(output.narration_enabled,新建项目默认关、存量项目缺省=开)开启时派发——关=全片无任何旁白,不派发、闸门不 HOLD(§7D)**;生成旁白稿:人称统一(默认第三人称)、补足画面外信息;**逐条挂场景锚点并标 `est_duration_s`(估时参数取 narrator 声线实测语速,不用通用字/秒经验值);接 §7D 无声组补写回派时新增条目并以新版本写回 narration.md(旁白唯一事实源)** | screenplay、structured_story | `epNN/narration.md` | 机检:人称一致性 100%;**每条有锚点(在本集 screenplay 内合法)与 est_duration_s**;QA:logic-qa 审「旁白-画面」冗余 |
| hook(每集) | 设计开头 3 秒钩子与结尾悬念;给出备选 3 条 | screenplay、下一集 episode_plan | `epNN/hooks.json` | 评分 creative_v1;QA:人工从备选中挑选或要求重写 |
| pacing(每集) | 节奏审定:逐场时长分配、情绪曲线、删减建议 | screenplay、color_script | `epNN/pacing.json` | 机检:总时长 = 预算 ±10%;QA:director 会签 |

**G5 闸门 + H3 人工确认**:第 1 集剧本用户签字后,后续集按同标准批量流转(用户可抽查)。

### Phase 6 — 导演分镜(每集,依赖 G4+G5)

| Agent | 工作指令(要点) | 输入 | 输出 | 校验 |
|---|---|---|---|---|
| director | 撰写本集导演阐述:视觉基调、重点场次处理、镜头语言倾向;**固定小节「转场清单」(2026-08-28,§9C):逐条列非硬切转场的位置/类型/时长/意图/理由,无则写「全片硬切」——是 shot_list `transition_in` 的唯一创意来源,Phase 9 只按字段实施不再读散文** | screenplay、pacing、style、color_script | `directing/epNN/directing_plan.md` | 评分 creative_v1;机检:转场清单章节存在、类型在枚举内;QA:art-director 会签 |
| storyboard | 分镜设计:逐场拆分镜头草案(画面内容、构图草描);**每组按导演转场清单登记入口转场 `transition_in` 与叙事块 `narrative_block`(闪回/梦境/蒙太奇/想象段成对:入口组与块尾下一组都有 transition_in,2026-08-28,§9C)**;**每场标结构化 `time_of_day`(受控枚举,依场景圣经 time_variants 定值,2026-07-20)**;按叙事节拍划分生成组草案 groups_draft(同场景、连续、Σ≤15s、对话轮不跨组);**每组登记出场角色服装 `costumes`(角色→`bible/costumes.json` 的 COS-id,按套装 scenes/chapters/episodes 与 change_points 定值;换装点只能落在组边界,落在节拍中间就在换装点切组,2026-08-26)**;**关联场景布局包并标注人物动线(2026-08-19,仅「人物精确空间位置」开启时)**:每组 `scene_refs`(俯视图/九格图/layout.json 路径)+ `blocking_map`(组内每个出场角色 start / path / end,位置引用 layout.json 地标 id,附动线句 `route_en` 供 prompt 逐字注入(语言随界面语言,2026-08-24 二订);无移动只写 start;同场景相邻组 start 接前组 end),每镜 `view_tile`(1–9,机位最近的九格格号);**每组全局站位表草案 `blocking_map.station_table[]`(2026-09-03:逐占字母条目 id/zone_en 所在区域/anchor{landmark,relation_en} 固定参照物/facing_en 身体朝向/neighbors[] 相邻人物/invariants[] 不能改变的位置关系,导演台视角、禁画幅侧与景深词)**;每角色 `label` = 短规范名(非代词、无括号说明、全集同角色同词,2026-08-27);独立态生物(牵引/拴着/独自入画)也写进 blocking_map 一条(id CRE-*,2026-08-27),骑乘态由骑手 mounted;跑宿主 CLI `code/render_blocking_map.py --source storyboard` 渲染草图自核(禁改写/自绘) | directing_plan、screenplay、scenes index(time_variants)、**场景布局包** `assets/concepts/scenes/<sid>/{layout_top.png,grid_9views.png,layout.json}` | `epNN/storyboard.json`(含 groups_draft、每场 time_of_day、每组 scene_refs/blocking_map、每镜 view_tile)+ `epNN/blocking_maps/draft/*.png` | 机检:剧本场景覆盖率 100%;每镜均入组;**storyboard_time_consistent:每场 time_of_day 必填且场内光照描写与之无昼夜矛盾**;**blocking_map_present(`render_blocking_map.py --source storyboard --strict`):有角色的组标注齐全、地标引用合法、route_en 非空(语言随界面语言,2026-08-24 二订)、相邻组动线衔接**。评分 visual_plan_v1 |
| shot-planning | 定稿镜头表:镜号、时长、景别、机位、出场角色/场景 ID、是否对白镜头;定稿生成组 generation_groups;**定稿组入口转场 `transition_in`(type/duration_s/intent/reason/source 受控枚举:可渲染 dissolve/fade_black/fade_white/dip_black/dip_white + 标注型 smash_cut/match_cut;Σ可渲染 ≤ 集预算 1%)与 `narrative_block`,拆并组后按新边界重挂;机检 transition_ok(2026-08-28,§9C)**;**每镜 `costumes{CHAR:COS}` 与每组 `costumes_by_char`(继承 storyboard 组 `costumes`,组内同角色同一套;下游 continuity `costume_states.outfit`、p7 `costume_by_char`/refs 服装 sheet、§6A 服装缺口、分镜预览页组卡服装图均以此为源,2026-08-26)**;**定稿旁白挂点 narration_anchors 与逐组音频形态 audio_plan(dialogue/narration_over/ambient_only),对无声组(ambient_only)逐组核查纯画面能否讲清叙事,讲不清回派 narration 补写旁白(新版本写回 narration.md)或上报剧本变更加对白(§7D ①);dialogue 组逐组核对台词估时能否装进组时长,超限优先回派 dialogue-rewrite 改短台词(§7D ①);**对话场景切组时组内说话人 ≤3(Seedance reference_audio 上限 3 段,§8A 设计层硬约束)——说话人多的群戏按说话回合拆组**;**组边界尽量令组尾镜与下组首镜人物阵容一致(solo 反应镜放组头不放组尾);阵容变化边界的首镜构图须与前组尾镜显著不同,交 continuity-planning 核查 boundary_cast_framing(2026-07-23,前科 grp018→grp019 汤米瞬现)**;**每组钉死 `time_of_day`(继承 storyboard 场块)与 `lighting_scheme_id`(该场景 lighting.json 中时段匹配的方案 ID,2026-07-20)——下游 prompt 取光照的唯一依据,无匹配方案回派 05-scenes/lighting 补,不得就近凑**;**全局站位表 `blocking_map.station_table` 定稿(station_table_ok,2026-09-03,`render_blocking_map.py --strict` 内置:每个占字母条目六项齐全——人物编号/所在区域/固定参照物(layout.json 地标)/身体朝向/相邻人物/不能改变的位置关系,导演台视角、禁画幅侧/景深/镜头词;下游 prompt 逐字拼入 `Blocking table:` 段)**;**继承 storyboard 的 scene_refs / blocking_map / view_tile 进 generation_groups / shots(2026-08-19,仅「人物精确空间位置」开启时),定稿分组与草案不同时按新组边界重切动线(拆组:前段 end=拆点=后段 start;并组:首 start+末 end),角色集合=characters_union,label 收口为短规范名(全集同角色同词,机检 label_ok,2026-08-27),然后**只准宿主 CLI** `code/render_blocking_map.py --project --ep` 渲染定稿动线俯视图 `epNN/blocking_maps/grpNNN.png`(图上只有字母与动线、无文字;prompt 组 refs 必挂;禁复制/改写到项目 code/ 或自绘);生物两态站位(2026-08-27):creatures_union 每个生物或作独立态条目(id CRE-*,占字母,◆/▲ 标记)或由骑手 mounted,两态皆无 = creature_blocking_ok 违规** | storyboard、pacing、narration.md、scenes lighting.json、场景 layout.json | `epNN/shot_list.json`(含 generation_groups、narration_anchors、逐组 audio_plan、dialogue 组 speakers、**逐组 time_of_day + lighting_scheme_id**) | 机检:Σ镜头时长 = 集时长 ±10%;角色/场景 ID 合法;镜号唯一;组:镜号全覆盖不重叠、同场景连续、Σ∈[4,15] 整数秒、组内出场角色 ≤4;**speakers_le_3:dialogue 组说话人数 ≤3(§8A,超限=FAIL 必须拆组)**;**station_table_ok(2026-09-03,同脚本):每组站位表六项/条目齐全、id 集合=占字母条目、anchor 地标合法、invariants ≥1、无画面视角词**;**blocking_map_present(`render_blocking_map.py --strict`):有角色的组 blocking_map 齐全且角色集合=characters_union、地标引用合法、相邻组动线衔接、定稿动线图落盘;每镜 view_tile ∈1–9 或 null(2026-08-19)**;**time_anchor_ok:每组 time_of_day/lighting_scheme_id 必填,scheme 在该场景 lighting.json 存在且 condition.time_of_day 与组一致,同场景同时段各组同 scheme(2026-07-20)**;**旁白:条目 100% 有挂点、镜/组引用合法、窗口 ≥ est_duration_s×1.15;组 audio_plan 100% 必填,ambient_only 组必附 silent_rationale(§7D);对白:dialogue 组 Σ台词估时 ≤ 组时长×0.7(§7D ①;2026-08-30 起 `code/check_dialogue_fit.py` 执行,超限不自查放行,交 p6-dialogue-fit 精简)** |
| dialogue-rewrite(p6-dialogue-fit,每集,shot-planning 定稿后) | **对白时长校验修正环节(§7D ①′,2026-08-30)**:定稿镜头表落盘后跑 `python3 code/check_dialogue_fit.py --project <slug> --ep epNN`——逐组 Σ台词估时 vs 组时长×0.7、逐镜 vs 镜长、单句 vs shot_max×0.7、记录估时 vs 文本+语速重算、shot_list 台词 vs 剧本对白层逐句核对;PASS 即关单(报告 `dialogue_fit.json` 落盘,不改任何稿);有超限组按报告 `trim_targets[]`(需削减秒数、逐句 target_chars,<6 字短句豁免)**逐句精简台词**:只动对白文本层——screenplay.md 对白层(事实源)、dialogue.md、shot_list.json 对应 `dialogue_lines[].text` 镜像同步改,镜/组结构、时长、说话人顺序一字不动;改完 `--write-est` 重算估时并复检至 PASS;精简须保语义与人设(风格命中率不降),删句/合句允许但不得删掉后续镜 blocking/表演触发所依赖的句;3 轮仍装不下上报 orchestrator 回派 shot-planning 调镜时长/拆组(分镜变更),**严禁指望模型压语速消化** | shot_list.json、screenplay.md、dialogue.md、各角色 voice.json(语速)、settings.json(shot_max_s) | `directing/epNN/dialogue_fit.json`;精简时 screenplay.md / dialogue.md / shot_list.json 新版本(仅对白文本层 diff) | 机检:`dialogue_fit_group`(Σ ≤ 组时长×0.7)、`dialogue_fit_shot`(Σ ≤ 镜长;>85% WARN)、`line_le_cap`、`line_est_consistent`、`lines_text_match_source`(脚本退出码 0);精简后 `style_hit_rate_gte_80`、非对白层 diff=0;QA:精简发生时 logic-qa 会签;**未 PASS 不派 p6-blocking、不得发起 H3A 签字** |
| camera-movement(每镜) | 运镜设计(推拉摇移/手持/稳定器,速度曲线) | shot_list、directing_plan | `shots/<id>/camera.json` | 机检:运镜类型在受支持的生成能力清单内 |
| composition(每镜) | 构图设计(九宫格位置、前中后景、视线方向) | storyboard、shot_list | `shots/<id>/composition.json` | QA:visual-qa 预审可执行性 |
| cinematography | 本集镜头语言规范(镜头焦段习惯、色温倾向、景深策略) | directing_plan、style | `epNN/cinematography.json` | QA:art-director 会签 |
| blocking(每镜) | 人物调度(站位、走位、动作节拍;**每入画角色附站位片段 `space_fragment_en`(内容语言随界面语言,2026-08-24;句内地标词逐字取 layout.json `name_en`,其语言亦随界面语言——2026-08-24 二订)——**2026-09-03 改为画面视角站位句**:按本镜机位(shot_list `view_tile` → layout.json views 视轴 + camera_position)写画幅侧/景深层/对镜朝向/同框相邻关系 + 一个地标词(逐字 name_en),禁罗盘方位(俯视图视角),≤40 英文词或 ≤60 字,另写枚举 `frame_position{side,depth,facing_camera}`;画面描述必须与导演台站位(组 blocking_map/station_table)一致;下游 prompt 逐字拼入不翻译,2026-07-23**;**本镜站位必须落在所属组 blocking_map 的动线上(组首镜=start、组尾镜=end、中间镜沿 path),地标词逐字取场景 layout.json `name_en`,2026-08-19,仅「人物精确空间位置」开启时**;**对白镜(`is_dialogue=true`)每说话角色附表演意图层节拍 `performance`(2026-08-26):goal / arc_from→arc_to / trigger{line_ref, word=冻结台词原文子串} / forbidden_early[] / end_state(≤40 字可见静止状态,禁 AU 编码与抽象情绪词,下游 prompt 逐字拼入);非对白镜仅情绪峰值场建议填**) | shot_list(含组 blocking_map)、scene、场景 layout.json、relationship | `shots/<id>/blocking.json` | 机检:人物在场合法性(该时间点该人物必须在该地点,查 story_timeline);**space_fragment_present:每角色必含非空 `space_fragment_en`(语言随界面语言,2026-08-24),含地标/屏侧词,无数值坐标与运镜词(2026-07-23)**;**camera_view_consistent(2026-09-03,`code/camera_view_check.py --strict`):每角色 `frame_position` 枚举合法、片段无罗盘方位词且含与 side 一致的画幅侧词、side 与两两纵深同 view_tile 视轴下的组 blocking_map 几何一致**;**blocking_on_map:与组级 blocking_map 位置一致、地标词在 layout.json 存在(2026-08-19)**;**performance_present:对白镜每说话角色 `performance` 六字段非空、trigger.word 为台词原文子串、end_state 合规(2026-08-26,仅对白镜)** |
| continuity-planning | 跨镜头连续性检查表:轴线、光线方向、服装/道具状态;**group_transitions 镜像 shot_list `transition_in`,可渲染转场边界 anchor 必须 none、match_cut 边界核首尾构图对位(transition_anchor_consistent,2026-08-28)**;组间衔接检查(组边界轴线/光线/服装、尾帧锚链完整;**边界两侧镜角色集合比对标 cast_change,阵容变化边界核首镜与前组尾镜构图显著不同——景别/机位/站位至少一项明显变化,2026-07-23**);**lighting_chain 逐条挂 time_of_day/lighting_scheme_id,核 key_light 与 scheme 昼夜相容、跨组时段跳变有 story_timeline 依据(2026-07-20)** | shot_list + 各镜头设计 + scenes lighting.json | `epNN/continuity_plan.json` | 机检:轴线跳变清单为空或有豁免说明;组衔接表覆盖全部相邻组;**boundary_cast_framing:cast_change 边界首镜与前组尾镜同景别同机位近似构图=FAIL(2026-07-23,前科 grp018→grp019 汤米瞬现)**;**时段链:lighting_chain 时段字段与 shot_list 一致、光照描述与 scheme 昼夜相容、无依据的昼夜跳变=FAIL**;QA:timeline-qa 会签 |
| concept-coverage-audit(art-director,每集) | 汇总 shot_list 本集出场实体(角色/场景/剧情道具/生物 CRE-*)× 所需视图,比对 `concepts/` 与 `props.json` 现货,列缺口清单并回派 character-concept/costume-concept/environment-concept/prop/creature-concept 补齐(§6A) | shot_list、`assets/concepts/`、`bible/props.json`、`bible/creatures/`、appearance/environment/props/creature 设定 | `directing/epNN/concept_coverage.json` | 机检 `concept_coverage_ok`:出场实体 × 所需视图 100% 有现货;补出概念图过 visual-qa + character-consistency-qa 常规打分入库 |

> **§6A 概念图覆盖审计(concept coverage audit,每集,G6 前强制,机检 `concept_coverage_ok`)**:Phase 4 只为 S/A 角色与关键场景出概念图,而本集**真正出场的实体以 `shot_list` 为准**——B 级角色、次要地点、本集新出场的剧情道具、生物/坐骑及其本集所处阶段的形象变体,其概念图缺口若漏到 p7-image,模型只能凭 appearance/props 文字脑补形象,跨组一致性从源头失守(出图经验见 char-concept / env-concept 笔记)。故 shot-planning 定稿后、H3A 签字前,art-director 执行一次覆盖审计:
> ① **枚举需求**:从 `shot_list` 汇总本集全部出场实体(角色 `CHAR-*`、场景 `SCN-*`、剧情道具、生物 `CRE-*`——取每镜 `creatures[]` / 组 `creatures_union`,2026-08-26)及每个实体所需视图——角色=整版 `sheet.png` + **本集各组 `costumes_by_char` 指到的每套非默认服装的 `sheet_<COS-id>.png`**(经 `assets/concepts/characters/<id>/costume_sheets.json` 台账判定:台账无该套、`file` 不存在或带 `blocked_on` 即缺口,带 `skip_reason` 的按理由所指替代图核对;2026-08-26)+ 剧情所需的关键表情版本;场景=**布局包三件套**(`layout_top.png` 俯视空间布局图 + `grid_9views.png` 9 宫格多角度图 + `layout.json`,2026-08-19 起;仅有旧版 `main_*.png` 单视角图算缺口)+ 按 `environment.json` 的昼夜/季节/光照变体(九格图变体);剧情道具=样式图 + 比例锚图 `scale_ref_01.png`(`readable_face` 道具的样式图须为正/背双视图 sheet,单面正面图算缺口,2026-08-25 二订)(§Phase 4 prop;两者均无人物入画);生物=整版三视图 `sheet.png` + **本集所在章节对应的阶段变体** `sheet_<stage>.png`(按 `creature.json` forms[] / `mount.json` endurance_state_log 判定该集应取哪一阶段;主目录只有 `sheet.png` 而该集需要另一阶段形象即算缺口;坐骑图无骑手);
> ② **比对现有**:逐一核对 `assets/concepts/{characters,scenes,props,creatures}/` 与 `bible/props.json` 现货,列出缺失清单(实体 × 缺失视图);各 `<id>/candidates/` 子目录为弃用候选留档(§Phase 4 目录卫生),**不计入现货**;
> ③ **补齐派发**:缺口回派对应概念 Agent——角色缺→character-concept、**服装 sheet 缺→costume-concept**(分龄基准缺则先回派 character-concept 出 `sheet_<tag>.png` 再补服装)、场景缺→environment-concept、道具缺→prop(道具须一并补齐 `scale` 三字段与比例锚图,同守道具图无人物红线)、生物缺/阶段变体缺→creature-concept(同守坐骑图无骑手红线);补出的概念图经 visual-qa / character-consistency-qa 常规打分入库,与 Phase 4 同标准;
> ④ **产出清单**:`directing/epNN/concept_coverage.json`(每实体:所需视图、现货路径、缺口状态 `covered | dispatched | filled`),进「分镜设定」预览页供 H3A 审看。
> **机检 `concept_coverage_ok`**:本集 shot_list 出场实体 × 所需视图 100% 有现货(状态全 `covered`/`filled`)方可发起 H3A 签字;**未过不得派发本集任何 p7-image / p7-video 工单**(与 §7D ① 估时级机检并列为 G6 前置硬闸)。新出场 S/A 主角若 Phase 4 遗漏,其新概念图须在 H3A 预览页**显著标注供用户确认**(等同 H2 风格锁定的每集延伸)。

**G6 闸门 + H3A 分镜确认(每集)**:shot_list(含 generation_groups)机检/QA 通过后,orchestrator 经 confirm 机制向用户发起**分镜签字**——用户在控制台「分镜设定」预览页审看本集分镜脚本、组划分、旁白挂点(含估时级适配结果)、对白组台词估时适配(p6-dialogue-fit 报告 `dialogue_fit.json`,§7D ①′)、逐组音频形态与无声组判定、**概念图覆盖审计结果(§6A;含本集新出场实体的补图与遗漏主角标注)**(可用「📝 注释」「✏️ 手绘分镜」预先注入导演意图)后签字;**§6A 概念图覆盖机检(`concept_coverage_ok`)与 §7D ① 估时级机检(旁白挂点/对白台词适配 + 组 audio_plan 齐备 + 无声组判定;对白侧 = p6-dialogue-fit 节点 PASS)未过不得发起签字,签字前不得派发本集任何 p7-* 工单**。签字后 shot_list 冻结,进入生成;签字后改分镜=走变更流程并评估重做成本。**签字弹窗同时确认本项目的「视频提示词技能」(§7F,2026-08-28)**:缺省按真正跑视频生成的模型自动解析(Seedance 2.5→sd25-pe / 2.0 系列→sd20-prompt-writing / MiniMax H3→h3-prompt-writing),用户可在弹窗、分镜预览页或「视频模型设置」改为手选一项或跳过;签字即把解析结果冻结为快照 `settings.json#prompt_skill.effective`,之后 p7-prompt 工单按快照执行并受 `prompt_skill_applied` 机检约束。

### Phase 7 — 视觉生成(每组流水线,依赖 G6+H3A)

> **§7A 生成组(generation group)范式**:一组同场景相邻镜头一次 Seedance 2.0 多镜头生成。
> 组内一致性由单次生成天然保证;组间续接按项目「时长设置 → 长镜头」开关(2026-09-01,默认关):
> **开启**时用「上一组尾帧(return_last_frame)作下一组参考锚」续接,
> 故 video-generation 按组序串行——**串行范围是续接链而非全集**:场景切换处若组首镜不引用前组
> 尾帧(refs 无尾帧、continuity_from 置 null),链在此断开,**断点两侧的组段可并行生成**
> (各段独立状态文件;判定与操作细则见 video-generation SOUL.md「实战经验」)。
> **关闭(默认)**时组间不挂尾帧参考图(continuity 所有组交界 `anchor: none`、refs 无 `*.last_frame.png`),
> 仅靠换构图文字承接开场句续接,续接链不存在、各组可并行生成;尾帧照常落盘(供预览/转场),
> 本段下述续接措辞/§7C 前向接缝规则仅在开启时适用。取舍依据:低分辨率草稿档下尾帧低清,
> 作参考图会拖累续接组画质与人脸一致性(前科:dzg6 ep01 grp028→grp029 人脸漂移)。**续接措辞规则**:组首镜与前组尾帧人物阵容一致时用
> "opening continues from [Image N]"(延续构图);组首镜含前组尾帧中**不存在的角色**时,
> 改用 "same location and lighting as [Image N], cut to a new <景别>" 并给新角色写入场/走位动作
> ——否则模型会延续上帧构图,新角色凭空出现(前科:ep01 grp007→grp008)。
> **改写句必须显式换构图(2026-07-23)**:新开场景别或机位须与尾帧明显不同(反打/过肩/侧向/景别升降一档),
> 禁止与尾帧同景别同机位的 "cut to a new shot" 弱指令——模型会照抄尾帧构图把新人原地插入,
> 接缝读作人物凭空出现(前科:tothemoon ep01 grp018→grp019 汤米);已在场但不在尾帧的角色换构图切镜即可,
> 真正新到场的角色另须入场动作或 establishing 开场。三种图生模式互斥(首帧/首尾帧/多参考图),组生成走多参考图
> 模式,keyframe 的角色是**参考锚点**(prompt 内 `[Image N]` 软引用),不再逐镜硬锁首尾帧。
> **`[Image N]` 编号铁律(2026-07-13)**:N = refs 数组下标 + 1(1-based,refs[0]=[Image 1],
> 场景概念图/尾帧一律计数)。按 0-based 下标编号是既成事故模式——ep05 整批 57 处错位,
> `角色@Image2` 全部错指前组续接尾帧,凡尾帧是对方角色的组**说话人互换**,十组返工
> (前科:ep01 grp017 同因)。机检 `imageref_bound`(prompt 产出后脚本全批核对 + video-generation
> 开跑前复核):每个 `<角色>@Image N` 的 refs[N-1] 必须含该角色 CHAR id、
> "continues from [Image N]" 的 refs[N-1] 必须是尾帧/锚帧;不符=退回重编号,禁直接开跑。
> **反向也要核(refs_all_referenced,2026-08-27)**:refs/audio_refs 里挂的每份素材正文都必须至少引用一次,
> 道具图以 `<道具名>@Image N` 绑定、尾帧必有开场声明句(tailframe_declared);imageref_bound 只核「写了的引用
> 指向对不对」,挂了不引的图它看不见——polan3 ep01 五张道具比例锚图全部只挂不引、grp014 尾帧无声明句即此漏网。
> 脚本 `code/refs_referenced_check.py` 全批执行;video-generation 开跑前单组复核。
> **产物文件名编号铁律(2026-07-22)**:`grp`/`sh` 编号一律三位零填充(`grp001.mp4`、`sh001.json`),
> 文件名与目录名同 shot_list 的 group_id/shot_id **逐字符一致**——写成两位(`grp01.mp4`)
> 预览与各机检全部对不上号。genmedia image/video 提交前硬校验 `grpsh_id_3digits`:
> 输出路径里 grp/sh 编号非三位直接拒单并给出正名;webui 预览读侧另按 前缀+编号数值
> 容错(grp01 ≙ grp001)兜底存量漂移文件,但容错只保预览可见,新产物一律写正名。
> 每镜精确时长不可控(模型按剧情定节奏):组总时长是硬约束(±1s),镜级时长是节奏意图;
> 组 clip 内实际镜头边界由切变检测写入 meta 供剪辑/QA 对位。原生音频(generate_audio)默认开,
> 对白在组 prompt 用 `{台词}`、对白组把**每个说话角色各自的 voiceprint 样本**(项目级
> `voice/refs/`,按年龄形态选 variant 版;≤3 段且**总时长 ≤15.2s**=方舟硬限,机检
> `audioref_total_le_15s`,样本规格 ≤5s/段见 §8A)挂 reference_audio,prompt 逐角色
> 显式绑定 `<角色>@Audio N`(样本仅提供该角色嗓音特点,**非台词朗读**;对白语音与口型由模型
> 原生合成——TTS 严禁用作对白配音,口型问题红线见 §8A),音效/环境声按 cue 文字注入;
> **BGM 与旁白一律后期**(prompt 禁 `（）` 与音乐描述);lip-sync 仅剩不换语音的对齐兜底。音频范式详见 §8A。
> **对白配音方式由项目「输出设置→对白配音」决定(角色提示词「用户输出设定」段注入,权威)**:视频原声
> (默认)=上述原生范式、全流程不做对白 TTS;后期配音=组 clip 交付后每个对白组必派 `p7-dub`,按画面开口
> 时段用角色声线 TTS 替换对白轨(§8C),画面/时长不变,upscale/edit/mix 一律取配音后 clip。
> 单镜首尾帧模式保留为兜底路径(组生成质量不达标时逐镜重做)。
>
> **手绘分镜渲染图的首帧红线(2026-07-09)**:用户手绘分镜经 image-generation 渲染出的
> `anchor_sketch_*.png` 是**动作参考锚**——手绘描绘的是导演要的决定性瞬间,通常在组中段而非开场,
> 默认只作多参考图模式的 `[Image N]` 软引用(prompt 写明该瞬间所在 Shot 段与拍点),
> **严禁默认升级为整组 `--first-frame`**(前科:ep01 grp021/022/028/029 渲染图被硬锁成整组首帧,
> 视频第 0 帧即手绘构图,起手铺垫全丢、与前组尾帧续接断裂)。软引用反复(≥3 次)不达标需硬锁时,
> 走**拆段兜底**(orchestrator 批准):组在手绘瞬间处拆成子段,渲染图作后段首帧或前段尾帧再拼接;
> 仅当手绘就是组首镜开场画面(sketch 标注/用户注释可证,回执 meta 附依据)才允许作整组首帧。

| Agent | 工作指令(要点) | 输入 | 输出 | 校验 |
|---|---|---|---|---|
| prompt(每组) | 组级多镜头视频 prompt(官方 Shot 1:/Shot 2: 结构,**开头内嵌风格锚点 `Overall visual style: ...`、结尾并入 `Global constraints:` 全局负面句**——anchors/negative 字段不会进入生成请求;**正文散文语言跟随用户界面语言、不必英文(2026-07-29);上游逐字拼入片段的内容语言亦由生产方按界面语言产出(2026-08-24:style 注入串 style_fragment_ui、space_fragment_en、lighting prompt_fragment_en、costume visual_en、prop prompt_token、sfx/ambience cue、route_en 与 layout.json name_en 地标词——2026-08-24 二订:原「恒为纯英文」例外取消;2026-08-27 四订:动线图上不带任何文字,route_en 只进 prompt,无字体限制;存量英文片段照旧逐字拼入不翻译,换语言须回派上游成套重出)——结构锚点(`Overall visual style:`/`Shot N:`/`Global constraints:`/`[Image N]`)、固定英文约束句、台词保持英文不受影响,细则见 prompt SOUL.md**;**凡 refs 含角色图的组(含单人组)正文必写 Identity lock 句(exactly N... must match one of the reference images; no extra or duplicate person),Global constraints 必含 no duplicate or twin characters(2026-07-31 起扩展为官方双胞胎约束完整版长句,见 prompt SOUL.md)——三视图参考天然带复制诱因,防重复句只留 negative=没写(2026-07-23,前科 tothemoon ep01 grp026 双伊娃)**;每镜按运镜/主体动作/空间位置/音频四要素,**一镜一运镜:运镜句只写 camera.json 的单一运镜术语,禁复合/矛盾运镜(2026-07-31 官方)**;**主体定义前置:`Shot 1:` 前逐角色写 `<角色>@Image N:<性别词+2–3 稳定特征>` 定义句,Shot 段内只用裸角色名、禁复述外观串;refs 建议 4–5 张重要性前置(角色图最前),角色图=该角色单张整版三视图 sheet(`<id>/sheet.png`,2026-08-04 二订平台约定,一角色一张;**组 `costumes_by_char` 指到非默认服装时改挂该套服装 sheet `<id>/sheet_<COS-id>.png`(经 `costume_sheets.json` 台账取图,2026-08-26,机检 costume_ref_listed;缺图退回走 §6A 回派 costume-concept,不得只靠 visual_en 文字硬顶)**;整图多视角同框带复制诱因,Identity lock 句+防重复长句因此为硬前提)**;`[Image N]`/`@` 绑定素材,`{}` 对白;音频要素逐字取自 sfx/ambience cue,**cue 用 `<>` 包裹(官方音效符号)**,禁 `（）` 与音乐描述;**剧情道具首现 Shot 段逐字拼入 props.json 的 `scale.prompt_token`,禁写数值尺寸,Global constraints 并入尺度恒定句**;**组含手持可读道具(readable_face)时该 Shot 段写明道具朝向:可读面朝剧中读者、背面朝观众,一句从简即可,且必须落正文——朝向约束若只写在 negative 字段等于没写(不进生成请求),模型就会把文字面/屏幕面转向观众造成穿帮(前科 offer ep01 grp009 合同条款正对镜头,2026-08-25)**;**按组 audio_plan 注入音频形态:narration_over/ambient_only 组禁 `{}` 台词、Global constraints 必含无对白约束句,narration_over 组开头声明该段配后期旁白、人物不开口,§7D ③**;**光照按组时段锚确定性注入(2026-07-20):逐字拼入组 lighting_scheme_id 所指 scheme 的 prompt_fragment_en,严禁自写昼夜光照散文;grpNNN.json 落盘带 time_of_day/lighting_scheme_id**;**空间布局按组确定性注入(layout_map_bound,2026-08-19,仅项目「输出设置 → 人物精确空间位置」开启时;关闭时场景锚挂场景概念图、不写声明句、机检跳过):refs 必挂本组人物动线俯视图 `directing/epNN/blocking_maps/grpNNN.png`(shot-planning 由 blocking_map 渲染:起点●/动线→/终点■)与场景 9 宫格图 `grid_9views.png`(角色图之后、道具图之前),正文 `Shot 1:` 前写固定 Spatial layout 声明句(`[Image N] is the top-down layout map ... do not render the map, its markers, arrows or labels. [Image M] is the 3x3 multi-angle sheet ... do not copy its tiling.` + `Map markers: A = <角色名> (<CHAR id>), B = …` 字母按 blocking_map 数组顺序;角色名逐字取 blocking_map `label`(短规范名),主体定义句 `<label>@Image N` 同词;俯视图上只有字母与动线、无文字,字母↔角色全靠此句——2026-08-27 四订),逐角色逐字拼入组 `blocking_map.characters[].route_en`,每镜可写 "framed like tile <view_tile> of [Image M]";脚本 `code/layout_map_bound_check.py` 全批核对——单方向场景图换机位就漂、人物跨组位置跳变,两者都靠这两张图 + 逐字动线句锚住**;**空间站位按镜确定性注入(2026-07-23):每镜空间位置句逐字拼入该镜 blocking.json 各入画角色的 space_fragment_en,严禁自写站位散文——门内外/屏侧只有逐字复用才跨镜稳定(前科 ep01 grp007→grp008 门外角色瞬移入画);blocking 缺片段回派补写,不得代写;**2026-09-03 起片段为画面视角站位句(blocking 按本镜机位换算),Shot 段禁俯视图视角方位词,每 Shot 段写 framed like tile <view_tile>;Spatial layout 句后必写 `Map usage: [Image N] is a spatial position reference only, never the picture — …` 俯视图用途限定句,Global constraints 并入 `no top-down or bird's-eye view, no map or floor-plan imagery`(map_reference_only,2026-09-03:俯视图只用于空间位置参考,不得直接用于画面);`Shot 1:` 前必写 `Blocking table:` 段逐字拼入组 `blocking_map.station_table` 五项(zone/anchor/facing/neighbors/keep,导演台不变量,格式见 prompt SOUL),缺表回派 shot-planning**;**表演证据层按组条件注入(2026-08-26,仅 `audio_plan == dialogue` 或情绪峰值场组;规范 `agents/08-video-gen/prompt/skills/performance-direction/SKILL.md`,引擎无关、与引擎 skill 并用):每个说话角色的 Shot 段把 blocking `performance` 意图层节拍翻译成可见证据——说话前状态 → 说到『触发词』时的面部五区(眉/眼/鼻翼/唇/下巴)+ 目光/身体/声音 → 呼吸停顿 → 结束状态;`trigger.word` 在 `{}` 之外再写一次作绑定短语、`end_state` 逐字拼入、`forbidden_early` 反应不得出现在触发词之前;AU 编码与强度只记 `grpNNN.json#performance[].au_calibration`,严禁进正文(会被渲染成画面文字);blocking 缺 `performance` 回派补写,不得代写导演意图**;**对白组 audio_refs=组内每个说话角色的 voiceprint 样本(≤3 段,按年龄形态选 variant 版),prompt 逐角色写 `<角色>@Audio N` 绑定句(音频锚点写 `[Audio N]`,绑定句含 voice.json 音色特征短语,2026-07-31)——样本仅锚嗓音特点、非台词朗读,§8A;发现说话人 >3 =上游切组违规,退回 shot-planning 拆组(speakers_le_3),不得自行取舍挂锚**);锚点图 image_prompt 仅按锚点缺口出(reuse-first:概念库能覆盖的锚不出 image_prompt,开场锚帧默认不出,2026-07-24) | 组内全部设计文件 + Bible 片段 + continuity 状态表 + audio_cues/ambience_cues + casting.json/voiceprint 样本库 | `assets/prompts/epNN/grpNNN.json`(组)+ `<shot>.json`(锚点图) | 机检:必含要素清单(风格锚点/角色锚点/画幅)全命中且 video_prompt 以 `Overall visual style:` 开头;长度不设固定词数上限(2026-08-16 废止「<1000 词」,官方 skill 均无数字上限;改为去冗余机检:无复述句/风格词约束句不重复;H3 渠道 `detailed_description` 段按 skill 350–500 英文词区间);素材引用与 refs 清单一致;禁写色号/元信息;道具尺度锚命中(prop_scale_token_ok);**对白组 audioref_bound:每个说话角色有 `@Audio N` 绑定且 audio_refs[N-1] 样本文件名含该角色 CHAR id(年龄形态与本组时间线一致)**;**audioref_total_le_15s:audio_refs 实测总时长 ≤15.2s(方舟硬限,超限任务创建即 400;genmedia 提交前同样硬校验)**;**refs_mandatory_le_cap(2026-08-30 改):refs ≤ 本组生效参考图上限(用户在分镜预览「🎛 模型」为该组单独覆盖了视频模型时按该模型硬限,如 Seedance 2.5 ≤30;否则项目「视频模型设置」)——refs 按实际需要挂齐(必挂项+确需的按需项),不为凑上限裁图、不自行拆组;超限照常落盘完整 refs 并标 `status: blocked_refs_cap`,orchestrator 转告用户在分镜预览给该组「🎛 模型」换更高上限模型(渠道不变、只影响该组)或手动删减参考图,拍板后重派本组;video-generation 对超限/blocked 组禁开跑,genmedia 按组级设定自动改用组模型并按实际模型硬校验**;**组内出场剧情道具参考图列入 refs 清单、首现组必含比例锚图 scale_ref_01.png(prop_ref_listed——只有文字 token 没有图,道具样式全靠模型脑补),且在首现 Shot 段以 `<道具名>@Image N:` 绑定句引用、后接逐字 prompt_token(prop_ref_bound,2026-08-27)**;**refs_all_referenced / audioref_all_referenced / tailframe_declared(2026-08-27):refs 每张图正文至少以 `[Image N]`/`<主体>@Image N` 引用一次、audio_refs 每段至少以 `[Audio N]`/`@Audio N` 引用一次,refs 含前组尾帧时必有 `opening continues from [Image N]` 或 `same location and lighting as [Image N]` 开场句——genmedia 把 refs 全部发给模型,正文不点名的图是无说明参考,会被忽略(白占名额)或误用(锚图背景漏进画面);脚本 `code/refs_referenced_check.py` 全批执行(前科 polan3 ep01 五张道具图只挂不引、grp014 尾帧无声明句)**;**`@Image N` 绑定命中(imageref_bound,脚本全批执行,§7A 编号铁律):每个 `<角色>@Image N` 的 refs[N-1] 含该角色 CHAR id、continues from 的 [Image N] 指向尾帧/锚帧——1-based,错位=说话人互换(前科 ep01 grp017、ep05 十组)**;**非对白组无 `{}` 且含无对白约束句(nonspeech_group_prompt_ok,§7D ③)**;**lighting_scheme_bound:time_of_day/lighting_scheme_id 与 shot_list 一致、scheme 的 prompt_fragment_en 逐字命中、无与时段昼夜相悖的光照词(2026-07-20;video-generation 开跑前复核,缺失/矛盾禁开跑)**;**blocking_bound:组内每镜每入画角色的 blocking space_fragment_en 在对应 Shot 段逐字命中(忽略大小写/连续空白),脚本 `code/blocking_bound_check.py` 全批执行,禁人眼抽查;video-generation 开跑前单组复核,未命中禁开跑(2026-07-23)**;**station_table_bound(2026-09-03,`code/layout_map_bound_check.py` ⑧):正文含 `Blocking table:` 且组 station_table 每条目 zone_en/anchor.relation_en/facing_en/neighbors[].relation_en/invariants[] 逐字命中;组缺表 WARN(--strict FAIL)**;**map_reference_only(2026-09-03,同脚本 ③):正文含同句带动线图 `[Image N]` 与 "spatial position reference only" 的用途限定句,Global constraints 含 "bird's-eye"——俯视图仅作空间位置参考,不得直接用于画面**;**performance_bound(2026-08-26,仅对白组):每对白镜每说话角色 trigger.word 在本 Shot 段 `{}` 内出现且 `{}` 外再出现一次、end_state 逐字命中、forbidden_early 不在绑定短语之前(WARN)、全文无 `AU\d`(au_not_in_prompt);脚本 `code/performance_bound_check.py` 全批执行;存量 blocking 缺 performance 按 WARN 回派**;**prompt_skill_applied(2026-08-28,§7F):组 json 回执 `skill_applied` 与项目「提示词技能」快照一致——套用时 `{id, sha256(当前 SKILL.md), checklist ≥3 条且无 false}`,不套用时 `{id: null, reason}`;脚本 `code/prompt_skill_check.py` 全批执行;运行时另核 `prompt_skill_read`(工具活动记录里没读 SKILL.md 的运行直接 error 退回,不看自述)** |
| image-generation(每组) | 组参考锚点包:**reuse-first(2026-07-24)——一律优先复用角色三视图/场景空间锚(本组动线俯视图 `blocking_maps/grpNNN.png` + 场景 9 宫格图 `grid_9views.png`,2026-08-19)/道具比例锚图,概念库全覆盖即零新生成(meta 记 `generation_channel: reuse-only`);**复用锚登记引用不落盘副本(2026-08-24 引用化)**:meta `anchors[]` 记 `file: null` + `source: reuse:<概念库原路径>` + `source_sha256`(溯源指纹),组 prompt refs 与视频请求直接用概念库原路径(实证 offer 全 36 组提交 refs 本就是原路径、包内副本零消费),仅新生成锚落盘包内文件;存量副本式锚点包不回迁,机检按 `file` 有无分支;组开场合成锚帧(anchor_opening)默认禁出**——组生成走多参考图模式且与首帧互斥,合成开场帧进不了视频请求(实证:xiaohongmao ep01 28 组落盘 86 张 anchor_opening,进入 Seedance 请求 0 张,纯沉没成本;tothemoon ep01 全程 reuse-only 质量不降),仅 orchestrator 批准的拆段/首帧兜底(§7A 首帧红线)例外;补生成仅限概念库缺口(特定服装状态/表情/道具细节特写、手绘分镜渲染),新生成锚 meta 必记 `gap_reason`(概念库缺什么、为何非生成不可);**道具锚优先取比例锚图 scale_ref_01.png(特写图无比例信息,防跨组尺度漂移)**;**组内有用户手绘分镜的,先据手绘稿渲染风格化图像(anchor_sketch_*.png)入锚点包——原稿严禁直接进视频 refs;渲染图 meta role 记 `action_ref`(参考锚),严禁标注首帧强锚(首帧红线见 §7A)**;**一切新生成锚帧必带所涉实体概念图 --ref 与 style.json 风格锚(§7E ①②)** | prompt、bible 概念图、style.json、用户参考图、手绘分镜(sketches/) | `assets/keyframes/epNN/<grp>/` | 机检:分辨率/画幅合规(**画幅 16:9/9:16 只是大致比例,近似即合规,禁按严格比例判等;引用式复用锚解引用 source 路径对源文件检查,2026-08-24**);锚点 ≤9 张(建议 4–5);**reuse_first_ok(2026-07-24):概念库已有可用图的锚不得新生成、新生成锚 meta 必记 gap_reason、anchor_opening 无兜底批准记录不得存在**;**imageref_order_bound(2026-08-24):meta anchors[] image_index 连续且与组 prompt refs 逐位路径一致(复用锚取 source 剥 reuse: 前缀)**;**组内出场剧情道具参考图已登记入锚点包(prop_ref_attached:meta 引用或包内文件,目标存在)**;**新生成锚过 repair_ref_anchored(§7E)**。QA:visual-qa ≥80 |
| character-consistency(每组) | 对锚点包做角色一致性校正;**复用锚(meta source=`reuse:`)与在库概念图同源,免校正——仅校新生成锚,reuse-only 组直接放行(2026-07-24)**;角色特写前置、单人照防「双胞胎」;**校正重生成必带在库三视图 --ref 与 style.json 风格锚(prompt 风格段 + 负面清单),严禁凭文字设定重画形象;所涉概念图缺失=停手上报,不自行补画(§7E)** | 锚点包、concepts 三视图、style.json | 校正后锚点包 | 机检:人脸相似度 ≥0.85(与人设参考图;仅新生成锚,复用锚免检);不达标自动重 roll ≤3 次;**修正重生成过 repair_ref_anchored(§7E)** |
| video-generation(每组,按组序串行) | 组 prompt+锚点包(+前组尾帧)一次生成多镜头组 clip;开 generate_audio 与 return_last_frame;**手绘渲染图只作 --ref 软引用,`first_frame` 指向 anchor_sketch_*/kf_action_* 而无开场依据/拆段说明=违规配置,开跑前退回(首帧红线见 §7A)**;**开跑前核对 refs 素材完备:组内出场剧情道具无对应参考图=违规配置退回(prop_ref_attached);`@Image N` 绑定复核(imageref_bound,§7A 编号铁律)不符=退回 prompt 重编号,严禁按错位 prompt 开跑;refs/audio_refs 每份素材正文有引用、尾帧有开场声明句(refs_all_referenced / tailframe_declared,`code/refs_referenced_check.py` 单组复核,2026-08-27)未过=退回 prompt 补引用,不得带着无说明的参考图开跑**;**局部穿帮缺陷单(repair_mode: v2v_edit)走 V2V 定向修改**(原 clip 作 --ref-video,§9),不整组重 roll;**整组重 roll 先做前向接缝评估(§7C):后组 refs 含本组尾帧的,追加后组首帧软引用 + "ending continues into"**;**对白组开跑前复核 audioref_bound(每个说话角色的 voiceprint 样本在 audio_refs 且 `@Audio N` 绑定正确,§8A)与 audioref_total_le_15s(实测总时长 ≤15.2s,超长样本先截短 ffmpeg -t 4.9 再跑并记 meta)不符=退回/修正后再开跑** | grpNNN.json、校正锚点包、前组尾帧、说话角色 voiceprint 样本(项目级 voice/refs/,casting.json 索引) | `assets/clips/epNN/grpNNN.mp4` + `grpNNN.meta.json`(含切变边界、尾帧路径、usage) | 机检:组总时长 ±1s、24fps/分辨率合规(**画幅 16:9/9:16 只是大致比例——引擎原生输出近似比例即合规,如 480p 档 864x496;不得因非严格 16:9/9:16 判失败或索要用户豁免,2026-08-03 Thedouble2 前科**)、有音轨、尾帧落盘。QA:visual-qa 组级打分(V2V 修复版按新组复检,对白组加**逐说话人**声学快检——参照各自 voiceprint 样本,错配=整组重 roll;**重 roll 组做双向接缝复检,§7C**) |
| voice-generation(p7-dub,**仅项目「对白配音=后期配音」**,每对白组,p7-video 后) | **后期配音(§8C)**:`python3 code/dub_group.py --project <slug> --ep epNN --group grpNNN` 一站式——从组 clip 原生轨实测每句台词开口起止(silencedetect,按 shot_list `dialogue_lines` 顺序对位;原生轨杂音重、自动检测不可靠时先 `--detect-only` 目检/听审再 `--segments` 手工给定),按 casting.json 该角色×形态条目(形态按组 audio_refs 样本名推断)TTS 逐句合成**冻结版台词一字不改**,语速 ±25% + atempo ±10% 贴合开口时长、起点对齐开口起点,原生轨在开口时段压低(-26dB,保留环境声/音效)叠上 TTS,画面流原样封装回 `grpNNN.mp4`(时长/fps/分辨率不变,原生轨备份 `.native_audio.wav`,重跑幂等);语速上限内仍装不下的句子记 overflow **上报回派 dialogue-rewrite 改短或整组重生成,严禁硬塞/剪画面**;视频原声模式脚本自动拒跑 | 组 clip+meta、shot_list dialogue_lines、casting.json、组 prompt audio_refs | `assets/audio/voice/epNN/dub/grpNNN/{lNN_<CHAR>.mp3,.fit.wav,dub_manifest.json}` + 组 clip 新版本(meta 追加 dialogue_voice 段) | 机检:dub_lines_text_match_frozen_script、dub_speaker_casting_bound(缺 casting 条目=FAIL)、dub_fit_ok(每句 fit_ratio ∈[0.9,1.1] 且无 overflow)、clip_duration_unchanged/video_stream_unchanged、av_offset_lt_80ms;QA:audio-qa 听审音色与 voice.json 相符、开口/闭口与语音起止贴合(明显对不上=开缺陷单,回派重测时段或整组重生成) |
| lip-sync(兜底) | 仅做不换语音的音画对齐校正;对白口型/语音缺陷默认走 video-generation 整组重生成(**严禁 TTS 干声换轨重驱口型**,§8A 红线);后期配音模式下对 p7-dub 交付的 clip 做同样的整体时移对齐兜底(仍不重驱口型画面,§8C) | 组 clip、缺陷单 | 更新组 clip | 机检:音画偏移 <80ms;QA:visual-qa 复检 |
| animation | 动作补间/局部重绘修复(按 QA 缺陷单触发;**重绘涉及人物/场景/道具形象的,素材与 prompt 受 §7E 形象红线约束**) | 组 clip、缺陷单 | 修复后组 clip | 复检原缺陷项通过;**涉形象重绘过 repair_ref_anchored(§7E)** |
| upscale | 超分至「输出设置」成片分辨率(像素尺寸按 aspect_ratio.json 画幅矩阵换算);**成片分辨率与草稿档不同时默认派发,无需用户确认(§7B)** | 组 clip | 终版组 clip | 机检:目标分辨率、无超分伪影抽检 |

**G7 闸门 + H3B 视觉生成确认(每集)**:全集生成组 QA 通过率 100%(允许 ≤5% 组人工豁免);**人工抽检每集 10% 组**;机检/抽检通过后,orchestrator 经 confirm 机制(`--sign`)向用户发起**视觉生成签字**——用户在「视频预览」页审看本集各组 clip 后签字,未签字不进 Phase 9 剪辑合成。签字确认的是组内容质量;成片方式(超分路径)仍按 §7B 自动执行,不另设确认。

> **§7B 分辨率与成片方式**:Phase 7 的一切视频生成(首次/重 roll/兜底重做)一律按「📤 输出设置」
> **草稿分辨率**执行(genmedia 层有硬闸门,越档自动压回)。G7 过闸后的终版组 clip 产出**不询问用户,自动按默认路径执行**:
> - 成片分辨率 = 草稿分辨率:草稿档组 clip 直接定为终版,不派 upscale;
> - 成片分辨率 ≠ 草稿分辨率:**默认且仅走 upscale(超分)**——草稿档组 clip 由 upscale 超分至成片分辨率,不花生成费,画面与过审版完全一致;**严禁按成片档重新生成**(生成费用高、耗时长,且 Seedance 2.0 无 seed,画面与过审版有随机差异);
>
> 唯一例外:QA 判定超分不达标的组,按缺陷兜底走成片档重出(video-generation 用同一 prompt+锚点包按成片分辨率重生成,**重出组须 visual-qa 复检**),不需询问用户。

> **§7C 重 roll 前向接缝规则(2026-07-10)**:尾帧续接链是**单向前向**的——整组重 roll grpN 时,
> grpN+1 已按 grpN 的**旧尾帧**生成,而新尾帧必然不同(无 seed)——grpN→grpN+1 的前向接缝是盲区。
> 开跑前必做**边界影响评估**:核对 grpN+1 的 refs 是否真含 grpN 尾帧(唯一可靠判据;仅
> continuity_from 有值而 refs 无尾帧属元数据冗余,不算续接边界)。不含(硬断点)→ 免处理;含 → 续接边界:
> ① 重 roll 的 refs **追加 grpN+1 首帧**(从现有 grpN+1.mp4 抽 t=0 帧落盘
> `assets/clips/epNN/<next_gid>.first_frame.png`),prompt 尾镜写 "ending continues into [Image N]"
> (与 opening continues from 对称)。目标是**状态对齐**(光线/服装/站位/道具),不是像素复刻——
> 组边界是硬切,像素级同帧反而似跳剪;② 交付后 visual-qa 对重 roll 组做**双向接缝复检**
> (与前组尾帧 + 与后组首帧);③ 复检仍明显跳变:优先剪辑级遮蔽(edit/transition 以既有硬切
> 结构或转场消化),仍不行才经 orchestrator 批准**级联重 roll grpN+1**(以新尾帧作锚,链式成本,最后手段)。
> **例外**:V2V 定向修改(v2v_edit)保留原画面运动,尾帧近似不变,免边界评估(复检顺带确认尾帧未漂移);
> 边界是**同一连续动作跨组**(非切镜)时,软引用不够,走拆段/首尾帧硬锁兜底
> (grpN+1 首帧作重 roll 组尾段的 `--last-frame`,orchestrator 批准)。

> **§7D 旁白/对白适配与组音频形态检查(narration & dialogue fit,2026-07-10;派发 p7-video 前强制)**:旁白是
> 后期轨,但**旁白挤不进画面、无声组无人交代叙事的问题必须在视频生成前发现**——组 clip 生成后
> 再扩镜/调组/补旁白 = 整组重 roll 的钱。**对白同理且更隐蔽:组总时长是生成硬约束(±1s),
> `{}` 台词由模型原生合成——台词超出组时长承载力时,模型会为念完台词强行提速,语速异常、
> 表演赶戏,只能整组重 roll。超长的解法优先级:改短台词(文本层,最便宜)> 调镜/拆组(分镜
> 变更)>> 压语速念完(禁止——那是把缺陷烧进成片)。**检查分两级,另有生成侧联动:
>
> **旁白开关(「📤 输出设置」→ 旁白,`settings.json` `output.narration_enabled`;新建项目默认关、存量项目缺省=开;2026-09-01 增)**:
> 关闭 = 用户约定**整个片子没有任何旁白**——p5-narration/p8-narrator 一律不派发、不建卡,闸门不因缺
> narration.md/旁白轨而 HOLD(两工位被派到也只说明开关已关闭并结单);shot-planning 不写 narration_anchors,
> `audio_plan` 禁用 narration_over——无对白组一律 ambient_only,`silent_rationale` 照常逐组核查,但纯画面
> 讲不清叙事时**不再回派补写旁白**,只能上报走剧本变更加对白或交用户裁决;prompt 不写旁白声明句(无对白
> 约束句照写);audio-mixing 只混原生轨 + BGM 两路;subtitle 只做对白字幕;narration 系列机检
> (narration_anchors_cover_all / narration_window_gte_est_x1.15 / narration_fit / narration_anchor_sync)
> 一律跳过(报 `skipped: narration off`)。本节以下条款与 §8B 均以开关开启为前提。
> ① **估时级(H3A 签字前,shot-planning 执行)**:
>   - 定稿 `narration_anchors`(每条旁白 → 具体镜/组区间 + 可用画面窗口秒数,窗口扣除其中
>     对白占时),机检窗口 ≥ `est_duration_s`×1.15;不满足在签字前解决(优先调镜时长,画面
>     装不下再回派 narration 精简);
>   - **逐组定稿音频形态 `audio_plan ∈ {dialogue, narration_over, ambient_only}`**(有对白镜
>     =dialogue;无对白但有旁白挂点=narration_over;两者皆无=ambient_only);
>   - **对白适配(估时级)**:dialogue 组逐组核对台词总估时(取 screenplay 对白层
>     `est_duration_s`,口径已按角色声线语速),机检 `Σ台词估时 ≤ 组总时长×0.7`(留动作/
>     反应/停顿空间);超限**优先回派 dialogue-rewrite 改短台词**,其次调镜时长/拆组,
>     严禁指望模型压语速消化;
>   - **①′ 对白时长校验修正环节(p6-dialogue-fit,2026-08-30)**:对白在 Phase 5 写完、镜头时长到 Phase 6 定稿
>     镜头表才确定,长度必然漂移,靠 shot-planning「自查」放行等于没有闸门(前科:dzg5 ep01 需临时开
>     `p5-dialogue-ep01-trim` 补删压)。改为 **shot_list 定稿后固定一个节点**:`python3 code/check_dialogue_fit.py
>     --project <slug> --ep epNN` 落成上述机检(`dialogue_fit_group` Σ≤组时长×0.7、`dialogue_fit_shot` Σ≤镜长、
>     `line_le_cap` 单句≤shot_max×0.7、`line_est_consistent` 记录估时=文本+语速重算、`lines_text_match_source` shot_list
>     台词与剧本对白层逐句一致),报告 `directing/epNN/dialogue_fit.json` 含每个超限组的 `trim_targets[]`(需削减秒数、
>     逐句 target_chars);PASS 即关单,超限由 dialogue-rewrite **按报告逐句精简**(只动对白文本层:screenplay.md 对白层
>     + dialogue.md + shot_list `dialogue_lines[].text` 镜像,镜/组结构不动),`--write-est` 同步估时后复检至 PASS;3 轮
>     仍装不下才走分镜变更(shot-planning 调镜时长/拆组)。p6-blocking(表演触发词取自台词)与 H3A 签字以该节点
>     PASS 为前置。估时口径统一:有效字符(汉字+英数字)÷(角色 voice.json `speed_cpm` 中点÷60),无语速设定的
>     群演信记录值并 WARN。
>   - **无声组核查**:每个 ambient_only 组必须逐组判定「纯画面 + 音效/环境声能否讲清该段叙事」
>     并附理由(`silent_rationale`,如纯动作/氛围/蒙太奇段);讲不清的,默认回派 narration
>     **补写旁白**(补写条目必须新版本写回 `narration.md` 并重过其机检与估时——narration.md
>     是旁白唯一事实源,严禁只登记在挂点表),确需加对白的上报 orchestrator 走剧本变更
>     (动 screenplay 冻结版,成本高,须批准);
>   - 挂点、audio_plan 与无声组判定一起进「分镜设定」预览页供用户 H3A 审看。
> ② **实测级(p7-video 派发前,narrator 执行)**:narrator 在 p7-video 之前合成正式旁白轨并
> 逐条实测时长(TTS 语速控制不可靠、换模型即漂移,估时不能代替实测),机检 `narration_fit`:
> 逐条实测时长 ≤ 挂点窗口×0.9(留呼吸空隙)且不与窗口内对白重叠;超窗默认回派 narration 精简
> 改稿→重合成复检(文本层修改,锚点不动,不改已冻结 shot_list、不重签 H3A);确需扩镜/调组的
> 走分镜变更流程并重估成本。**对白侧**:自 §8A 2026-07-20 改版起**不再合成组级干声、无干声实测
> 环节**——对白时长闸门就是 ① 的估时级机检(`Σ台词估时 ≤ 组总时长×0.7`,est_duration_s 口径已
> 按角色声线语速);超限回派 dialogue-rewrite 精简台词(文本层修改,不改冻结 shot_list),确需
> 扩镜/拆组的走分镜变更流程,**严禁指望模型压语速消化**。**narration_fit 或 ① 的对白估时机检
> 未通过,orchestrator 不得派发对应组的 p7-video 工单**。
> ③ **生成侧联动(prompt 执行,机检 `nonspeech_group_prompt_ok`)**:`audio_plan` 必须注入组
> prompt——`narration_over` 与 `ambient_only` 组**严禁出现 `{}` 台词**,Global constraints 必含
> 无对白约束句(`characters do not speak, no dialogue, no speech`);`narration_over` 组另在
> prompt 开头声明该段成片配后期旁白(如 `This segment is covered by post-production
> narration voice-over; characters act silently`),让模型知道叙事由旁白承担、画面纯动作表演
> ——不注明的话模型会自由发挥替人物配台词,出现怪异语言或无法理解的画面。此类组不传
> `--audio-ref`(voiceprint 音色锚仅对白组)。

> **§7E 修正阶段形象红线(repair identity red line,2026-07-12)**:一切**修正/校正/返修类**图像与视频
> 重生成——character-consistency 锚点校正、缺陷单返修、兜底重做、animation 局部重绘、V2V 定向修改
> 的样式参考——只准**复用既有冻结形象,严禁设计新形象**。前科:一致性校正阶段按文字设定裸 prompt
> 重生成人物图,无风格锚出图,新形象混入锚点包后整组设计风格跑偏。三条硬规则:
> ① **必带在库形象锚**:凡修正中调 genmedia 生成含人物/场景/道具的画面,`--ref` 必须包含所涉实体的
>   **在库概念图**(角色=`assets/concepts/characters/<id>/` 三视图、场景=`concepts/scenes/<id>/`、
>   道具=`concepts/props/<id>/` 比例锚图;一律取主目录定稿,严禁取 `candidates/` 弃用候选)及被修产物原图;
>   补生成/重出**道具图**时同守 §Phase 4 道具图无人物红线(画面无人物、`--ref` 不传角色/服装图);无 `--ref` 的裸 prompt 重生成 = 违规配置,
>   开跑前退回;
> ② **必带风格锚**:修正 prompt 必须命中 `bible/style.json` 风格锚——图像 prompt 含风格段且
>   `--negative` 带负面清单;视频 prompt 以 `Overall visual style:` 开头。**参考图不能替代风格锚**
>   (参考图只锚形象,不锚渲染质感/画风),二者缺一即退回;
> ③ **缺概念图 = 停手上报**:所涉实体在 `concepts/` 无现货(B 级角色/次要地点/新道具漏图)时,
>   修正 Agent **严禁凭 appearance/props 文字自行补画形象**——上报 orchestrator 按 §6A 缺口流程
>   改派 06-art 出概念图(过 visual-qa/character-consistency-qa 打分入库,新出场 S/A 主角走 H3A
>   显著标注确认),入库后再执行修正。
> 锚点**补生成**(image-generation 出概念库缺口的新画面,如服装状态/表情/道具特写锚;reuse-first 下仅限缺口,§7A)虽非修正,同受 ①② 约束(其缺概念图的
> 情形已由 §6A 覆盖审计在 H3A 前拦截)。
> **机检 `repair_ref_anchored`**(修正产物入库前强制):产物 meta 必须记录所用 refs 与风格锚命中
> 情况——refs 含所涉实体在库概念图路径、prompt 风格锚命中;不满足则产物不得入库、不得作下游锚,
> version 不予登记。

### 按需音频转写（09-audio/audio-transcription）

`audio-transcription` 是宿主内置的按需服务工位，不固定插入小说→视频主 DAG。任一流程只有音频、
没有可消费文字稿时，由 orchestrator 派单执行 `modules/transcription.py transcribe`，缺失的
faster-whisper 模型自动下载到 `data/models/faster-whisper/` 并复用；产出 UTF-8 时间轴 TXT 与
包含逐词时间、音频 SHA-256、模型信息的 JSON。数字人插件缺稿时必须自动展开
`dh0-transcribe`：单人物按 cast 名称直接生成连续覆盖母带的标签稿；多人优先使用显式说话人时间
边界，否则用本地 MFCC/音高聚类，并按用户指令映射“不同声纹第一次出现顺序”或“低音/高音”到
cast 人物。严禁逐行交替或从人物图片推断性别；`ready_for_digital_human=false` 时付费生成保持
阻塞。已有用户稿件则跳过转写且不覆盖。

> **§7F 视频提示词技能触发契约(prompt_skill,2026-08-28)**:prompt 工位(08-video-gen/prompt)写组级 video_prompt 时套用的官方提示词技能(`agents/08-video-gen/prompt/skills/` 下 sd25-pe / sd20-prompt-writing / h3-prompt-writing 及用户自装技能)**不再靠系统提示词软提醒,而是项目级显式设定 + 工单契约 + 双重机检**——前科:polan2(2026-08-27)运行时按全局渠道判定注入后,prompt 工位仍未读取 sd20-prompt-writing,需用户手动补指令,且没有任何环节能发现。
> ① **设定**:项目 `settings.json#prompt_skill {mode: auto|manual|off, skill_id}`。auto(默认)按**真正跑视频生成的模型**解析——video-generation 工位的「每 Agent 模型配置」视频渠道覆盖优先于全局渠道(旧实现只看全局,覆盖后判定失真);解析不到(ComfyUI 工作流无模型 id、新模型无对应技能)= 无技能并提醒手选。manual = 用户从该工位已安装技能里指定一项(与生效模型不匹配只告警)。off = 本项目不套用。入口:「视频模型设置」弹窗、H3A 签字弹窗、分镜预览页头部,三处同一设定。
> ② **快照**:运行时把解析结果写 `prompt_skill.effective {skill_id, mode, resolved_from, reason, decided_at}`——派 prompt 工单、保存设置、H3A 签字时刷新;机检以快照为基准,用户事后改技能/换模型,已写好的组 json 会因 id/sha256 不符而退回,退回单按新技能重做。
> ③ **工单契约**:runtime 在 prompt 工位系统提示词固定注入「提示词技能契约」段(生效技能与 SKILL.md 路径、必须动笔前 Read、回执字段格式、交付前必跑机检);orchestrator 派 p7-prompt 工单时 instruction 首段复述本项目生效技能(取自快照,无技能时写明原因),acceptance.auto 含 `prompt_skill_applied`。
> ④ **回执**:每个 `assets/prompts/epNN/grpNNN.json` 必带 `skill_applied`:套用时 `{id, sha256: <当前 SKILL.md 的 sha256>, checklist: [{item, pass}, …](≥3 条,对应技能核心要点)}`;不套用时 `{id: null, reason: user_skipped|no_match|disabled|missing}`。
> ⑤ **双重机检**:`prompt_skill_applied`(`code/prompt_skill_check.py --project <slug> --ep epNN [grp…]`,回执与快照/当前 SKILL.md 对账)+ `prompt_skill_read`(运行时在 prompt 工位运行结束时核对本次工具活动记录:没有读取 `skills/<技能目录>/` 的运行状态直接改 error 退回——派单的回给总制片按返工流程重派,用户手动对话的同会话自动追加一次补读工单;codex 引擎不回传文件读取事件,该项免检)。运行面板每个 prompt 运行带「🧰 技能」chip,漏读时红色标注。
> ⑥ **边界**:performance-direction 等按组条件引用的引擎无关技能不在本设定内,与提示词技能并用;已禁用的技能(state.json#skills_disabled)即使被 auto/manual 选中也按无技能处理(reason=disabled)。
> ⑦ **组级覆盖(2026-08-30)**:用户可在分镜预览每个生成组卡的「🎛 模型」按钮为该组单独指定视频模型(只能选**当前视频渠道**目录内的模型,渠道不可换;典型用途:必挂参考图超过 Seedance 2.0 的 9 张上限时只给这一组换 Seedance 2.5)与提示词技能(默认跟随全局;换了模型但技能仍「跟随全局」时按本组模型自动解析,避免 2.5 组套 2.0 技能)。存 `assets/group_settings/epNN/grpNNN.json {video_model, provider, prompt_skill{mode: global|auto|manual|off, skill_id}, effective}`,用户所有、不随 prompt 重出丢失;运行时同步项目级快照时一并刷新组 effective。消费链:prompt/video-generation 工位系统提示词固定注入「组级覆盖」段列出全部覆盖组;`prompt_skill_applied` 机检按组优先取组 effective;`prompt_skill_read` 对本单读到项目技能或任一覆盖组技能均算命中;genmedia `video` 按 `--output …/epNN/grpNNN.mp4` 推断组号(或显式 `--group`)读组设定改用组模型并按实际模型硬校验参考素材上限,`genmedia info --group` 可核对。分镜预览头部改为只读展示全局视频模型与技能(改动去「生成模型」页/「视频模型设置」),组卡 refs 超限按**组生效上限**判并提示用「🎛 模型」处理;手动加参考图不再按项目上限硬拦(极值 30 兜底)。

### Phase 8 — 音频(每集;cue 设计、音色样本与旁白轨先于 Phase 7 组生成,混音在其后)

> **§8A 音频范式(2026-07-20 改版:人物 Voice 样本范式——逐角色 voiceprint 挂锚 + prompt 显式绑定)**:
> 音效/环境声由 Seedance 2.0 原生随组视频生成——sound-effect 与 ambience 只产**文字 cue**。
> **红线(不变):TTS 严禁用于对白配音**——把 TTS 音轨当成片对白语音(生成期『原样使用参考
> 音频人声+口型同步』强绑,或后期换轨/贴片重驱口型)会导致**严重口型问题**(2026-07-09
> 实证);reference_audio 一律只作**嗓音特点参考**,对白语音与口型由模型原生合成。
> **红线的适用范围=项目「输出设置→对白配音=视频原声」(默认)**。用户把该项设为**后期配音**时,
> 即明确选择用 TTS 后期配对白并接受口型只能尽量贴合的取舍——此时红线由用户设置显式解除,
> 对白轨按 §8C 由 p7-dub 替换;但**仍禁止用 TTS 干声重驱/重绘口型画面**(只换音轨、以时段贴合)。
> 当前项目取哪种方式以角色提示词「用户输出设定→对白配音」注入为准,任何 Agent 不得自行切换。
> **人物 Voice 前置到人物设定阶段(Phase 3)**:每个有台词角色在人物设定时就完成
> ① `voice.json` 声纹设定(03-characters/voiceprint,含年龄形态分版)→ ② 选角登记
> `casting.json` → ③ **voiceprint 样本合成**(09-audio/voice-generation,每角色×年龄形态一段
> **3–5s** 平静中性内容纯人声干声,落项目级 `assets/audio/voice/refs/<CHAR>[_<variant>]_voiceprint.mp3`)。
> **样本时长红线 ≤5s/段(2026-07-20 实证)**:方舟 r2v 的 reference_audio **总时长硬限 15.2s**,
> 超限任务创建即 400 InvalidParameter(不计费)——早期 10–15s 规格两段配对必超
> (tothemoon 实测 12.3s+11.8s=24.1s 被拒);≤5s/段则 3 段满配 ≤15s 恒过,嗓音特点 5s 足够锚定。
> genmedia 提交前做 audioref_total_le_15s 硬校验(§7A),超限直接报错不发请求。
> **嗓音模板可由 Doubao-音频生成 1.0 描述定制(2026-08-31)**:「🎨 生成模型」页 TTS 火山渠道
> 模型选 `seed-audio-1.0`(列表首位,新配置默认)时,②③ 不再从「音色库」挑 speaker——
> **按 voice.json 声学字段(gender/presented_gender、pitch、timbre、accent;age_variants 逐
> 形态覆盖)拼文字描述直接生成定制嗓音**(`genmedia tts --character` 自动拼装,禁传 --voice,
> speaker 名会被忽略;reference_style 等剧情性文字不进描述)。casting.json 条目登记
> `tts_model: seed-audio-1.0` 与 genmedia 日志回显的 `voice_desc`,`tts_voice` 留空;
> voice_collision 按「同组说话人 timbre/pitch 描述雷同」判,雷同=调声纹卡后重出样本。
> **同一描述两次生成音色不同**:样本一次冻结全片复用,重出=受控变更;genmedia 在项目已有
> 冻结样本时自动把它挂为 seed-audio 的 @音频1 参考锚(形态样本重出锚定基础样本=同一副
> 嗓子按描述变龄;§8C 逐句 dub、旁白逐段合成同理不漂音色,旁白按旁白声线卡冻结样本
> `refs/NARRATOR_voiceprint.mp3` 作锚——2026-09-01 起卡与样本由 voice-generation 预先设计冻结)。
> **同一角色不同年龄阶段嗓音不同**:voice.json 的 age_variants 有分版的,逐 variant 出样本;
> 组生成挂锚时按该组时间线选对应形态的样本。
> **对白组挂逐角色 Voice 锚(2026-07-20,废止组级干声)**:~~每组一条台词干声轨
> `lines/grpNNN_dialogue.mp3`~~ 不再产出——单干声只含一个人的嗓音,多说话人组第二人音色
> 失控(手测实证:tothemoon ep01 grp012)。现行做法:组 audio_refs = **组内每个说话角色各自的
> voiceprint 样本**(≤3 段=Seedance 2.0 reference_audio 上限——**因此分镜组设计层就约束
> 组内说话人 ≤3**:shot-planning 硬机检 speakers_le_3,说话人多的群戏按说话回合拆组;
> 漏网到 prompt/video-generation 层的 >3 组=上游违规,退回 shot-planning 拆组,**不得自行
> 取舍挂锚**);prompt **逐角色显式绑定**
> `<角色>@Audio N`,指令句写明『[Audio N] 是<角色>的嗓音特点参考(<音色特征短语,取自 voice.json,2026-07-31>;仅音色,非本段台词的朗读);
> <角色>开口时用与 [Audio N] 一致的音色说台词』。台词本身仍以 `{}` 文本注入,由模型原生合成。
> **选角事实源=项目级 `assets/audio/voice/casting.json`**(2026-07-12 改版,继续有效):角色×形态
> (variant)→ tts_model+tts_voice 全片唯一登记;ComfyUI 的 tts_voice 是自动选择出的 TimbreModel 文件名,合成任何角色语音**前必查表**,表中无条目=先登记
> 再合成,多形态角色 variant 必填。音色复用豁免(collision_waivers)显式登记依据(同场互斥分析);
> **同组两说话人严禁共用同一 tts_voice 出样本**(同场互斥是 waiver 前提)。
> **换 TTS 模型=全员重选角受控变更**(音色名跨模型不可移植):整表更新+重出全部样本+评估已
> 成片漂移,严禁下一集悄悄沿用旧音色名。
> **已知风险与对策(历史实测,2026-07-09 thedoor)**:早期曾实测两条声纹样本齐传时模型只稳定
> 模仿第一段、第二说话人漂移(当时 prompt 未做逐角色 `@Audio N` 显式绑定)——因此本范式
> **强制**:①逐角色绑定句缺一即机检退回(audioref_bound);②交付后**逐说话人**声学快检
> (`code/voice_f0_check.py`,参照各角色自己的 voiceprint 样本),任一说话人音色错配=开缺陷单
> **整组重生成**(调整 audio_refs 顺序/绑定措辞后重 roll),不做 TTS 贴片/换轨精修(见红线);
> ③多说话人组反复(≥3 次)错配的,上报 orchestrator 回退「按说话回合拆组、一组一说话人」的
> 保守切法(shot-planning 仍保留该偏好)。方舟不逐字嵌入参考音频(波形互相关≈0,模型重演绎),
> 样本内容说什么无所谓,嗓音特点才是锚的对象。
> **无对白组严禁模型自编台词**:`audio_plan` 为 narration_over/ambient_only 的组(§7D),组
> prompt 不写 `{}`、不传 reference_audio,且必含无对白约束句;narration_over 组明示「该段配
> 后期旁白,人物不开口」——不明示时模型会替人物自由配音,出现怪异语言或与叙事无关的画面。
> **BGM 与旁白一律后期**:组 prompt 严禁 `（）` 音乐符号与音乐描述,负面词必含
> "no background music"。旁白不进组视频生成,但其 TTS 合成与逐条时长实测**提前到 p7-video
> 之前**完成(供 §7D 旁白适配检查;进成片仍由 audio-mixing 后期混入)。
> 音效 wav / 环境床音降为缺陷兜底贴片(patches/,仅音效/环境,不含对白)。

> **§8C 对白后期配音(dubbing,2026-08-15;项目「输出设置→对白配音=后期配音」时生效)**:
> **前置不变**:分镜/prompt/组生成与视频原声模式完全一致——对白组照写 `{}` 台词、照挂逐角色
> voiceprint 音色锚、人物开口表演由模型原生生成(原生对白语音就是**开口时段的时间依据**,也是
> 说话人对位的参照);§7D 对白估时闸门、speakers_le_3、audioref_bound 等全部照旧。
> **p7-dub(09-audio/voice-generation,每个 audio_plan=dialogue 的组,p7-video 交付后必派)**:
> ① 台词事实源=`shot_list.json` 组内各镜 `dialogue_lines`(speaker/text,冻结版一字不改;
>   `dialogue_tail_from` 反打镜不重复挂);② 开口时段=组 clip 原生轨语音区间(silencedetect,
>   按台词顺序对位;区间多则合并、少则按台词字数比例拆;自动检测不可靠时 `--detect-only` 目检/
>   听审后 `--segments` 手工给定);③ 逐句 TTS 按 `casting.json` 该角色×形态条目(形态按组
>   audio_refs 样本名 `<CHAR>_<variant>_voiceprint` 推断,可 `--variant` 覆盖;缺条目=先登记再
>   合成,dub_speaker_casting_bound),ComfyUI 渠道传 `--character/--variant` 自动选型、云渠道传
>   casting 的 voice;④ 口型贴合=语速 ±25% 重合成 + atempo ±10% 微调、起点对齐开口起点;
>   **装不下的句子记 overflow 上报**(回派 dialogue-rewrite 改短→重配,或整组重生成),严禁
>   硬塞、拉长画面或剪画面;⑤ 混轨=原生轨开口时段压低 -26dB(环境声/音效保留)叠 TTS(电平
>   对齐原生开口段),画面流 `-c:v copy` 封装回 `grpNNN.mp4`(时长/fps/分辨率不变),原生轨首次
>   替换前备份 `grpNNN.native_audio.wav`,重跑以备份为源(幂等)。统一走 `code/dub_group.py`,
>   产物 `assets/audio/voice/epNN/dub/grpNNN/`,meta.json 追加 `dialogue_voice` 段。
> **下游**:p7-lipsync(仅整体时移对齐兜底,不重驱口型)、p7-upscale、H3B 审看、p9-edit、
> p8-mix 一律取配音后 clip;p8-mix 不再另铺对白;audio-qa 听审音色与 voice.json 相符、
> 开口/闭口与语音起止贴合,明显对不上=开缺陷单回派 p7-dub 重测时段(手工 segments)或整组重生成。
> **视频原声模式(默认)不派 p7-dub、`dub_group.py` 自动拒跑**,§8A 红线全额生效。

| Agent | 工作指令(要点) | 输入 | 输出 | 校验 |
|---|---|---|---|---|
| sound-effect(先于 p7-prompt) | 音效 cue 设计:按事件点位写逐镜音频描述(打斗/门/脚步…;语言随界面语言,2026-08-24) | shot_list、blocking | `assets/audio/sfx/epNN/audio_cues.json` | 机检:关键动作 cue 覆盖率 ≥90%;无音乐字样 |
| ambience(先于 p7-prompt) | 环境声 cue:逐场景描述(语言随界面语言,2026-08-24),同场景跨组一字不差 | scene、environment、generation_groups | `assets/audio/ambience/epNN/ambience_cues.json` | 机检:每场景有 cue;跨组文本一致 |
| voice-generation(样本随 Phase 3 人物设定完成;集级只查漏补缺,先于 p7-video) | **每个有台词角色×年龄形态出 voiceprint 样本**(**3–5s** 平静中性内容纯人声干声——≤5s/段是方舟 audio_ref 总时长 15.2s 硬限的配额(3 段满配 ≤15s,§8A 2026-07-20 实证),按 voice.json 声纹选型,**人物设定阶段即完成**;新角色/新形态入库时随人物设定补出)——**仅作组生成 reference_audio 嗓音特点锚(逐角色挂锚,§8A 2026-07-20),严禁进成片对白**(§8A 红线);~~组级台词干声轨 lines/grpNNN_dialogue.mp3~~ **废止(2026-07-20)**;**合成前必查项目级选角注册表 `assets/audio/voice/casting.json`**(角色×形态→tts_model+tts_voice,全片唯一事实源;缺条目=先登记再合成,variant 必填,复用须 waiver 且**同组说话人不得共用音色**,§8A)并维护之;ComfyUI 渠道调用 `genmedia tts --character <CHAR-ID> [--variant ...]` 自动选型、禁止手填 `--voice`,casting 记录自动选中的 TimbreModel 文件;集级派单时核对本集说话角色样本覆盖 100%,缺则补;**旁白声线卡(2026-09-01,仅「📤 输出设置」旁白开关开启)**:项目级另出旁白声线卡 `assets/audio/voice/narrator.json` + 冻结样本 `refs/NARRATOR_voiceprint.mp3`——按项目基调(brief.md/题材)设计旁白声线描述,描述定制渠道(seed-audio-1.0)按描述直接合成,音色列表渠道按描述从渠道音色库自动选一个合适参考音色(性别硬过滤+气质就近,选型理由入卡,不得与任何角色 tts_voice 相同),genmedia 此后自动按卡固定旁白声线、**不随 TTS 设置变**(人物预览页「🎙 旁白」条目展示/试听;重定卡=受控变更) | **casting.json**、voice.json(含 age_variants)、generation_groups(集级查漏用)、brief.md(旁白声线设计) | 项目级 `assets/audio/voice/refs/<CHAR>[_<variant>]_voiceprint.mp3` + `refs/manifest.json`(角色×形态↔样本↔casting 条目)+ 更新 `voice/casting.json` + 旁白声线卡 `voice/narrator.json` 与 `refs/NARRATOR_voiceprint.mp3`(旁白开关开启时) | 机检:本集说话角色(×出场形态)样本覆盖 100%,样本时长 ∈[3,5]s、纯人声(**超 5s=FAIL:两段配对即撞方舟 15.2s 总时长硬限,§8A**);**casting_bound:每段样本的(角色,variant,tts_model,tts_voice)与 casting.json 条目一致,缺条目即 FAIL**;**voice_collision:同 tts_voice 分给两个有台词角色而无 collision_waiver 登记、或同组说话人共用音色即 FAIL**;QA:audio-qa 抽检样本与 voice.json 相符(音色/语速/年龄感) |
| narrator(先于 p7-video) | **仅「📤 输出设置」旁白开关开启时派发(新建项目默认关、存量项目缺省=开;关=全片无旁白,§7D)**;旁白配音(**声线唯一事实源=旁白声线卡 `assets/audio/voice/narrator.json` + 冻结样本 `refs/NARRATOR_voiceprint.mp3`,由 voice-generation 设计冻结、genmedia 自动按卡固定声线,不随「生成模型」页 TTS 设置变,合成一律不传 --voice(2026-09-01);渠道与卡不一致的告警=停手上报回派 voice-generation 重定卡;无卡(存量项目)提醒补卡后按旧口径:云渠道用渠道「默认音色」、seed-audio-1.0 用 --instructions 声线描述(首段验收后冻结样本自动挂锚)、ComfyUI 结合 instructions 从 TimbreModel narrator 候选自动选型**;后期轨,**在 p7-video 前合成并逐条实测时长,供 §7D 旁白适配检查**;超窗只上报回派 narration 改稿,不自行删句);**交付前跑 `code/check_narration_sync.py --stamp` 把当前 narration_anchors 指纹盖进 manifest(§8B)** | narration.md、shot_list.narration_anchors | `assets/audio/narration/epNN/`(逐条音频 + manifest:挂点/实测时长/**anchor_sync 指纹**) | 机检:语速在设定区间;**narration_fit:逐条实测时长 ≤ 挂点窗口×0.9、不与窗口内对白重叠(§7D ②)**;**narration_anchor_sync:逐段挂点组=shot_list 定稿、manifest 指纹与当前 narration_anchors 一致(§8B)**;QA:audio-qa |
| music | 配乐:按 color_script 情绪曲线**在需要烘托的位置**选/生成 BGM(开场定调/情绪转折/高潮/收束;对白密集与日常过渡段默认留白,**不从头铺到尾**),标注入出点(后期轨) | pacing、color_script | `assets/audio/bgm/epNN/` | 机检:BGM 覆盖率 30%–60%(越界须 notes 说明);QA:audio-qa 审情绪匹配与留白合理性;copyright 审版权 |
| audio-mixing(依赖全组 p7-video) | 三路混音:组 clip 原生轨(按组序拼接+接缝淡化)+ BGM + 旁白,响度对齐;缺陷贴片嵌入;**铺旁白前必跑 `code/check_narration_sync.py`,指纹失配=停手上报(§8B),旁白摆位逐条按当前 shot_list.narration_anchors,严禁按场景人工连续铺排** | 组 clips+meta、bgm、narration(**manifest 含 anchor_sync**)、patches | `assets/audio/final/epNN.wav` | 机检:**narration_anchor_sync(铺轨前置,§8B)**;响度 -14 LUFS ±1(平台标准)、真峰值 ≤-1dBTP、无削波。QA:audio-qa 终审 |

> **§8B 旁白挂点同步(narration anchor sync,2026-07-17;前科 DEF-ep05-audio-0005)**:旁白挂点的
> **唯一事实源是 `directing/epNN/shot_list.json` 的 `narration_anchors`**;narrator 的 manifest
> (`narration_track.json`)只是它的下游快照——两者一旦脱钩,混音会按旧挂点/旧组号铺轨,成片
> 音频总时长看似对齐、逐条旁白却全部错位(ep05 实际发生:shot_list 挂点改版后,混音仍读旧
> narration_track,把 N-03 起人工连续铺排、后段保留旧组号)。防线三条,机检统一为
> `narration_anchor_sync`(`code/check_narration_sync.py`):
> ① **产出侧盖章**:narrator 交付 manifest 前跑 `--stamp --task-id <id>`,把当前
>   `narration_anchors` 规范化 JSON 的 sha256 指纹写入 `anchor_sync` 块(逐段挂点组/编号/窗口
>   核对全过才盖得上),再 vc register 登记;**无指纹的旁白轨一律视为过期产物**。逐段 anchor
>   必须含可机读的 `grpNNN` 引用且与定稿 `anchor_group` 一致——只写场景名的自由文本挂点不可
>   用于铺轨,机检直接 FAIL。
> ② **消费侧重验**:audio-mixing 铺轨前、10-editing/edit 封装前**必跑只检模式**;指纹失配说明
>   shot_list 已改版,**停手上报 orchestrator 回派 narrator 重签(挂点未变仅需 --stamp 重盖+
>   窗口重验)或重合成,严禁按旧轨继续**。混音脚本的旁白摆位必须逐条取自「当前 shot_list 挂点
>   × manifest 实测时长」,不得内嵌手抄的挂点表。
> ③ **变更即失效**:shot_list 冻结后凡走变更流程产生新版本且触及 `narration_anchors` 或
>   `generation_groups`,orchestrator 必须将本集 p8-narrator(重签/重合成)、p8-mix、p9-edit
>   标脏重跑(§7 缺陷路由同款「只重跑受影响链路」);指纹机制保证即使漏标,下游开工自检也会
>   拦住。
> ※ 旁白开关(`output.narration_enabled`)关闭的项目本节不适用:无旁白轨可同步,
>   `check_narration_sync.py` 自动报 `skipped: narration off` 并 PASS(§7D)。

**G8 闸门**:final_audio 通过 audio-qa(含:**narration_anchor_sync 通过(§8B)**、跨组音色一致**且与 casting.json 选角一致(跨集维度)**、关键动作音效实际出声率 ≥90%、原生轨无 BGM 违禁)。

### Phase 9 — 剪辑合成(每集,依赖 G7+G8)

| Agent | 工作指令(要点) | 输入 | 输出 | 校验 |
|---|---|---|---|---|
| edit | 按 generation_groups 组序粗剪(组 clip 为剪辑单元,组内对位用 meta 切变边界)→ 按 pacing 精剪(裁切、变速);**剪辑期发现局部穿帮(服饰/道具/元素级)开 `repair_mode: v2v_edit` 缺陷单**(附时间窗+对照证据+修改指令草稿),由 video-generation 走 V2V 定向修改低成本修复(§9),不整组重 roll;**封装前重验 narration_anchor_sync(§8B ②)——final_audio 所据旁白轨与当前 shot_list 挂点失配即停手上报,不封装**;**终版封装(G9)接入片头后,正片 0 秒基准上的声轨与字幕都要整体后移一个片头实测时长——只准走宿主 CLI `code/finalize_episode.py assemble`(拼接时声轨随正片段一起进 concat,偏移天然产生)+ `shift`(产成片基准字幕 `subtitles_final.srt`)+ `check`(§9B),禁止手算 -itsoffset/adelay,烧录/发布一律用 final 版,严禁正片基准 SRT/声轨直接配 final.mp4(2026-07-18 前科:thedoor ep01–06 发布包字幕整体偏早一个片头时长)** | 组 clips+meta、final_audio、pacing | `edit/epNN/timeline.json` + `cut_v1.mp4` | 机检:**narration_anchor_sync(封装前置,§8B)**;成片时长 = 预算 ±5%;无黑帧/跳帧;fps 统一 24;**intro_offset_ok(终版封装时,`finalize_episode.py check`):成片时长 = Σ各段实测;字幕逐条 = 正片基准 + intro 实测(±200ms);成片声轨 vs final_audio 互相关滞后 = intro 实测(±80ms)**。评分 edit_v1 |
| transition | **只按 shot_list `transition_in` 实施组间转场,只准宿主 CLI `code/render_transitions.py` plan → render → check(§9C,2026-08-28)**:cut_v1 → cut_v2,pad 补偿(两侧各克隆半个转场时长的定格帧再 xfade,成片总长 ±1 帧不变,声轨流拷贝);硬切为默认;导演意图未进 shot_list 的转场不得自加,走变更流程回派 shot-planning | shot_list、cut_v1、timeline | `edit/epNN/cut_v2.mp4`、timeline `transitions[]`、`edit/epNN/transitions_render.json`(含黑帧白名单) | 机检 **transition_render_ok**(覆盖全部组边界、非硬切与 shot_list 一致、时长不变、声轨完整、抽帧核对);QA:visual-qa 抽检转场突兀度 |
| subtitle | 对白/旁白字幕(时轴对齐,**正片 0 秒基准,不含片头**;成片基准版由 edit 终版封装时用 `finalize_episode.py shift` 平移生成);**烧录样式权威:小字号贴底、最小化遮挡**(字高 ≤4% 画面高、底部居中、下边距 2%–4%、≤2 行、白字黑描边禁大面积底板) | final_audio、剧本文本 | `epNN/subtitles.srt` | 机检:时轴偏差 <200ms、错别字检查、每行字数 ≤ 平台上限、烧录样式合规(subtitle_style_ok) |
| caption | 花字设计(schema v2:headline 大标题/keyword 关键词/信息类,含字体、动画、配套音效;**仅花字开关开启时派发,见 §9A**)+ 逐组烧录(caption-render 工单,只准宿主 CLI) | shot_list、dictionary、timeline、`data/fonts\|sfx/manifest.json` | `epNN/captions.json` + `assets/clips_caption/epNN/` | 机检:术语与 dictionary 100% 一致 + `code/check_captions.py` design/render 段(schema v3/双写对账/入出点=语音逐字起止/资产命中/副本齐备且规格不变) |
| title | 片头/片尾(含下集预告位);**下集预告默认不配旁白**——钩子文案以字卡/花字呈现,声轨仅画面原声+BGM,需配音须工单显式指定(2026-07-10) | style、hooks、episode_plan | `epNN/intro_outro/` | 机检:预告无旁白轨(工单显式要求除外,teaser_no_narration);QA:art-director 会签 |
| thumbnail | 封面(每平台画幅各一,A/B 两版);**先盘点 refs/thumbnail/ 用户封面参考,优先借鉴其构图/版式/文字风格并落痕迹**(§2 规则 7) | 本集高光帧、style、seo 关键词、refs/thumbnail/ | `epNN/thumbnail_*.png` | 机检:画幅/安全区合规;QA:人工挑选 |

**G9 闸门 + H4**:第 1 集成片用户全片审看签字;后续集按抽检放行。**总装产物命名固定**:成片必须落盘为 `edit/epNN/final.mp4`(或含 `final` 的变体名,如 `epNN_final.mp4`)、成片基准字幕为 `edit/epNN/subtitles_final.srt`——Web 视频预览页与 platform-adapter 均按 `final` 名索引,orchestrator 开总装工单时 outputs 必须写此规范名,严禁 `master.mp4` 等别名(前科 2026-07-18:tothemoon p9-master-ep01 产出 master.mp4,视频预览页索引不到 ep01 成片)。

> **§9B 片头接入的时间轴平移(2026-08-26 增,归 `10-editing/edit`,总装工单必写)**
>
> - **问题**:片头(intro)接在正片之前后,正片 0 秒基准上的一切都要整体后移一个片头时长——外挂声轨 `assets/audio/final/epNN.wav` 与字幕 `subtitles.srt`/`.ass` 都是正片基准,直接配 `final.mp4` 就整体偏早。能力弱的模型常忘掉这一步、或只挪字幕不挪声轨(2026-07-18 thedoor 前科),所以**不再靠模型手算**。
> - **唯一工序**:宿主 CLI `code/finalize_episode.py`——`assemble`(intro + 正片画面 + final_audio + outro + teaser 一次 concat 成 `final.mp4`,声轨随正片段拼接、偏移天然正确;片头无音轨自动补静音,画面短于声轨用末帧补齐,**严禁 `-shortest`**,分辨率/fps 归一到正片口径)→ 自动 `shift`(字幕整体 +intro 实测时长,产 `subtitles_final.srt`/`.ass`;无片头 = 原样拷贝)→ 自动 `check`。成片由别的路径产出(花字版、外部返修)时至少 `shift` + `check` 必跑。**禁止 Agent 自写 concat 后再 `-itsoffset`/`adelay` 手算声轨偏移**;禁止用 `placement.json` 声明时长代替 `ffprobe` 实测(不一致以实测为准并上报 title)。
> - **机检 `intro_offset_ok`**(`finalize_episode.py check --project <slug> --ep epNN`,封装后、发布前必跑,FAIL 即不交付/不烧录/不发布):`final_duration_layout` 成片时长 = Σ各段实测(±0.25s);`subtitles_final_present` 有 subtitles.srt 就必须有 subtitles_final.srt;`subtitle_offset_all_cues` 逐条 cue = 正片基准 + intro 实测(±200ms,条数一致;`.ass` 同);`audio_offset_measured` 成片声轨 vs final_audio 互相关实测滞后 = intro 实测(±80ms,首/中/尾三窗一致 = 无累计漂移);`placement_declared_match` 声明 vs 实测(WARN)。台账 `edit/epNN/final_layout.json`(`cut_offset_s` = 片头偏移,各段起点/实测时长,check 结果)。
> - **段序与开关**:默认 intro → cut → outro → teaser;`settings.json#packaging` 禁用的段与缺失文件自动跳过,片头禁用时偏移 = 0,字幕原样拷贝。av(音频锁定)插件项目要求母带零重编码,与片头接入互斥(CLI 拒绝 assemble)。
> - **下游**:platform-adapter 打包前重跑 `check`(只读),字幕只取 `subtitles_final.*`;烧录字幕(`output.subtitle_burn_in`)只烧 `subtitles_final.*`,并抽帧核对首句出现时刻。

> **§9C 组间转场:Phase 6 设计、Phase 9 实施(2026-08-28 增)**
>
> - **问题**:transition 工位以前按 directing_plan 自由文本猜转场、自写 ffmpeg xfade,一项目一格式无法机检,且常忘记补偿 xfade 吃掉的重叠时长(成片缩短 → 外挂声轨/字幕/旁白挂点整体偏早);上游没有任何结构化转场字段,dzg5 等项目只能自造 `flashback_block`/`sub_block` 临时标记。
> - **设计链(Phase 6)**:director 阐述固定小节「转场清单」(位置/类型/时长/意图/理由,无则「全片硬切」)→ storyboard `groups_draft[].transition_in` + `narrative_block` → shot-planning 定稿 `generation_groups[].transition_in`(拆并组按新边界重挂)→ continuity-planning 镜像并核「可渲染转场边界 anchor=none」。**转场只挂在进入该段的组的入口一侧**(`transition_in`),缺省 = 硬切;首组只能 hard_cut / fade_black / fade_white(集首淡入)。闪回/梦境/蒙太奇/想象段以 `narrative_block {id, kind, role}` 成块,块入口组与块尾下一组都必须有 `transition_in`(有意硬切也显式写 `hard_cut` + reason)。机检 `transition_ok`(`code/check_generation_groups.py` 内置,默认开启):类型枚举、时长范围(dissolve 0.25–1.0s,fade/dip 0.3–1.5s)、非硬切必填 intent/reason、Σ可渲染 ≤ 集预算 1%、块成对连续。
> - **类型枚举**:可渲染 5 种——`dissolve`(叠化)、`dip_black` / `dip_white`(淡出再淡入的黑/白场)、`fade_black` / `fade_white`(前组尾淡出、本组硬入;集首 = 淡入);标注型 2 种——`smash_cut` / `match_cut`(不渲染 = 硬切,只供 continuity/QA 核构图对位)。风格化转场(划像/圈入等)暂不开放。
> - **生成侧(Phase 7)**:组间转场**不进 video_prompt**(fade in from black / white flash / dissolves in 之类一律禁写,机检 `no_intergroup_transition_in_prompt`);`transition_in` 非硬切的组不挂前组尾帧、不写 opening continues from。组内镜间的叠化/连续动作节奏不受此限。转场不占组时长,shot-planning 定稿时长不必留余量。
> - **实施(Phase 9,唯一工序)**:宿主 CLI `code/render_transitions.py`——`plan`(按 timeline `tracks.video` 推每个组边界时刻,写 `timeline.transitions[]`:每边界恰一条、硬切也登记、非硬切只能来自 shot_list)→ `render`(`cut_v1.mp4` → `cut_v2.mp4`,**pad 补偿**:叠化/dip 两侧各 tpad 克隆半个转场时长的尾帧/首帧再 xfade,fade 类以 enable 窗口限定;一切按帧量化;成片总长 = cut_v1 ±1 帧,边界之外一切时刻不动,声轨流拷贝)→ `check`(机检 `transition_render_ok`:覆盖全部边界、非硬切与 shot_list 一致、时长不变、声轨完整、逐处抽帧核对叠化中点/黑白帧、硬切位无漂移)。台账 `edit/epNN/transitions_render.json` 含 `black_frame_whitelist`,edit 的 no_black_frames 与 visual-qa 据此豁免有意黑场。**禁止 Agent 自写 xfade/tpad、禁止 trim 式真重叠**(会缩短成片、打破 final_audio 外挂基准);全片硬切时不产出 cut_v2,cut_v1 即定稿。`finalize_episode.py` 照常取版本号最高的 `cut_v*`。
> - **变更**:成片阶段想改转场 = 改 shot_list `transition_in`(新版本,走变更流程回派 shot-planning)后重跑 render;transition 工位不得就地改类型/时长。

> **§9A 花字与花字音效(2026-08-08 增,项目级开关,默认关)**
>
> - **开关**:「📤 输出设置」→ 花字(`settings.json` `output.caption_enabled`)。关闭 = caption 相关节点(主流程 p9-caption/p9-caption-render/p9-caption-final;av 插件 av2-caption/av4-caption-render/av4-caption-final)一律不派发、不建卡,闸门不因未派发而 HOLD(同 §7B p7-upscale/g7 先例);开启后 orchestrator 按 DAG condition 正常排产。
> - **设计**(`10-editing/caption`):产 `edit/epNN/captions.json` **schema v2**——全集 ≤4 个 style_presets(font_id 引用 `data/fonts/manifest.json`)、每条花字必填 group_id + 组内 local_start/local_end 与集级 start/end 双写对账、入出动画、`sfx_id` 引用 `data/sfx/manifest.json`(**音效只选库内素材,不逐次生成**)。**入出点 = 语音里这段花字文字被念出的起止(2026-08-18 用户裁定,±0.15s,机检 `caption_speech_aligned`)**:依据集级逐字语音时间轴 `edit/epNN/word_track.json`(`render_captions.py speech-align` 由 av beat_track / 主流程 subtitles.srt + 声轨生成;`speech-lookup` 查时间、`speech-snap` 一键吸附);语音里没念的画面标注型文字须标 `speech_free:true`(av 项目不允许)。密度:headline 每集 2–5 处、keyword ≤1 条/分钟、同屏最多 1 条、不入字幕安全区。av 项目无词典时文案必须逐片段命中母带原文(`caption_text_from_source`)。
> - **烧录**(caption-render 工单):**超分后的终版组 clip** 上逐组烧录**副本** `assets/clips_caption/epNN/grpNNN.mp4`(原 clip 永不改动;回执幂等,单组返工只重渲该组);**只准宿主 CLI `code/render_captions.py`,禁止 Agent 自写花字 ffmpeg 滤镜**。旧宿主无该 CLI 时由首项机检 `caption_toolchain_verified` 明确拦截。
> - **花字版成片**(caption-final 工单,归 `10-editing/edit`):`edit/epNN/final_caption.mp4` = clips_caption 副本替换对应组按同一 EDL 重拼;**音轨布局:a:0 = 声轨权威 + SFX 预混(AAC,播放器开箱即听;MP4 多音轨是互斥备选流,绝不能指望播放器叠加混播),a:1 = 声轨权威原样流拷贝(存档轨,av 项目零重编码机检 `final_caption_master_frames_intact` 对 a:1 逐帧校验)**。严禁 -shortest。干净版 `final.mp4` 照常产出,与花字版并列(文件名都含 `final`,视频预览页双双收录);干净版既有机检口径不变。
> - **资产(2026-08-11 改远端仓库制)**:字体/音效的**唯一事实源是独立 GitHub 素材仓库**(配置 `modules/caption_assets.json` 的 `repo`,结构约定顶层 `fonts/` + `sfx/`,用户动态维护),caption 工单第 0 步执行 `render_captions.py assets-sync` 按需拉取到本地缓存 `data/fonts|sfx/remote/`(gitignored,远端删除本地同步移除)并自动重扫 manifest;仓库未配置时降级用本地素材(兜底 `scripts/fetch_sfx.py --synth`);题材→风格包映射见 `agents/10-editing/caption/skills/caption-styling/SKILL.md`;license 由 11-qa/copyright 终审核对。
> - **机检**:`code/check_captions.py --require design|render|final` 三阶段;发布物料(platform-adapter)默认基于花字版转码(a:0 已含音效,直接转);零重编码承诺仍只对干净版 `final.mp4` 成立。

### Phase 10 — 终审(每集,8 个 QA 并行)

| Agent | 审什么 | 输出 | 通过标准 |
|---|---|---|---|
| logic-qa | 成片剧情逻辑、与原著关键情节偏差 | `qa/reports/epNN/logic.json` | 0 个 blocker 级缺陷 |
| character-consistency-qa | 全片人脸/服装/声音一致性 | `.../consistency.json` | 一致性分 ≥85,无认不出主角的镜头 |
| timeline-qa | 时间线/季节/年龄自洽 | `.../timeline.json` | 冲突数 = 0 |
| world-consistency-qa | 画面与 Bible 设定一致(建筑/服饰/体系) | `.../world.json` | blocker = 0 |
| visual-qa | 画质:畸变、闪烁、伪影、分辨率 | `.../visual.json` | 缺陷镜头 ≤2%,且均非关键镜头 |
| audio-qa | 音质:响度、爆音、口型偏移 | `.../audio.json` | 全部指标达标 |
| content-safety | 平台内容红线(暴力/血腥分级等) | `.../safety.json` | 无平台违禁项;分级标签明确 |
| copyright | 素材/音乐/字体版权链 | `.../copyright.json` | 每项素材有授权来源记录 |

任一 QA 出 blocker → 生成缺陷单(见 §7)→ orchestrator 回派责任 Agent 修复 → 仅重跑受影响链路 → 复审。

**G10 闸门 + H5**:8 份报告全绿,用户签发布字。

### Phase 11 — 发布

| Agent | 工作指令(要点) | 输入 | 输出 | 校验 |
|---|---|---|---|---|
| platform-adapter | 按平台矩阵转码(画幅/码率/时长切条);**发布目标平台取自「📤 输出设置」发布平台多选(提示词注入),仅面向所选平台产包;与主画幅不同画幅的平台从单母版裁/补适配,不重新生成;字幕一律取成片基准 `subtitles_final.srt`,严禁用正片基准 subtitles.srt 随 final.mp4 打包;打包前重跑 `code/finalize_episode.py check`(§9B,只读)** | final.mp4、subtitles_final.srt、final_layout.json、aspect_ratio.json、「📤 输出设置」发布平台 | `publish/<platform>/` | 机检:平台规格 lint 全过;产包平台集 = 输出设置所选平台;**intro_offset_ok 复检(`finalize_episode.py check` 全 PASS,字幕逐条与声轨互相关均对齐)** |
| seo | 标题(3 备选)/tag/简介,按平台调性 | 剧本、hooks、平台 | `publish/seo.json` | 机检:长度/敏感词合规;QA:人工挑标题 |
| metadata | 元数据(合集归属、集数、分级、封面绑定) | episode_plan、safety 报告 | `publish/metadata.json` | 机检:schema + 必填齐 |
| publisher | 定时/立即发布,回收平台回执 | 以上全部 | `publish/receipts/` | 机检:回执状态 = 成功;失败自动重试 2 次后报人工 |

---

## 5. 调度层的运行规则(贯穿全程)

| Agent | 何时被调用 | 职责要点 |
|---|---|---|
| workflow-orchestrator | 始终在线 | 按 DAG 解锁任务、派发工单、跟踪状态、失败重试、闸门判定(含缺陷清零机检与 waiver 记录,§7)、缺陷单路由;episode_plan 过 G5 后**按集展开 DAG**(§3.1,强制);**收尾钩子**:每个任务关单时校验运行记录四件套、触发实时版本登记、同步更新 `<项目目录>/runs/dag.json` 节点状态(§6.1) |
| context | 仅 full 包工单派发前(§1 原则 5 三类:重做单 / 需跨文件摘要裁剪(能列明确路径清单进 inputs 的不算,创作类不例外)/ 需聚合缺陷历史) | 组装 Context Package:该任务需要的 Bible 片段 + 上游产物 + 相关缺陷历史,控制在预算 token 内;扇出批次共享底包、逐实例增量,已有未过期 context.md 不重打;其余工单走 inline 轻量包,由 orchestrator 在工单本体内联,不调用本 Agent |
| memory-bible | 任何设定**写入**与冲突上报 | Bible 唯一写入口;冲突仲裁;变更走 changelog 并通知受影响下游。**读取受控版免仲裁**:任何 Agent 直读 `bible/` 当前受控版无需经过本 Agent |
| version | 每个产物落盘时 | **实时**版本化(落盘即登记,禁止依赖事后审计补录)、打标签(通过闸门的版本冻结)、支持回滚与 diff;changelog 保留真实产出 task_id |
| evaluation | 每个产物提交时 | 按 rubric 打分(0–100),<80 附具体修改意见退回(合格线以项目「审核设置·质量评委」为准,默认 60;设 0 则全程不派 evaluation 单、免验收评分);3 次不过升级人工 |

**子任务「手动停止」不是错误**:用户可在运行面板对排队/运行中的子任务点「⏹」手动停止。此类运行的 `status` 仍为 `error`,但 `dispatch.py --status/--runs/--wait/--wait-all` 输出会附「⏹已被用户手动停止(非错误,无需追查原因)」标记(API 字段 `stopped: "user"`,该 Agent 对话记录里也以「⏹ 已被用户手动停止…」开头)。调度层见到此标记**不得**当作程序错误去追查失败原因、翻日志或试探性重跑:只把节点记为 `failed`(note 写明「用户手动停止」),是否重派、跳过或改指令一律由用户决定——用户当轮没有明说时,以 `--confirm` 询问,不要自行重派。

**子任务「网络中断快速失败」不计 attempt**:claude CLI 遇 `API Error: Connection dropped (ECONNRESET)`/连接超时这类连接层错误(没拿到任何 HTTP 响应)时,宿主不再让它在「等待重试」里干耗(CLI 默认指数退避重试 10 次,多个并发 Agent 会一起卡死),而是**立刻终止进程并报错**:`status: error`,API 字段 `net_error: true`,`dispatch.py --status/--runs/--wait/--wait-all` 输出附「🔌网络中断快速失败」标记,该 Agent 对话记录以「🔌 网络中断…」开头(HTTP 状态类错误 429/529/5xx 仍由 CLI 自行重试,不在此列)。调度层见到此标记:不追查程序原因、不计入 `max_retries` 的 attempt;可用**原指令原引擎直接重派一次**(等片刻确认网络恢复后),重派仍是网络中断则停止自动重派,节点记 `failed`(note 写明「网络中断」),以 `--confirm` 升级用户修好代理/VPN 后再继续。总制片自身运行若因此中断,由用户重新发起。

## 6. 工单(Work Order)统一格式

Orchestrator 发给每个 Agent 的指令统一为:

```yaml
task_id: p7-ep01-grp005-videogen       # 阶段-集-组-工种
agent: 08-video-gen/video-generation
project: data/projects/<slug>
depends_on: [p7-ep01-grp005-imagegen, p7-ep01-grp004-videogen]  # 前组尾帧续接,按组序串行
attempt: 1            # 第几次尝试(重做时递增,并附上次失败原因)
context_package: runs/p7-ep01-grp005-videogen/context.md   # full 包:context Agent 已裁剪;inline 轻量包工单此处填 inline(§1 原则 5)
instruction: |
  为第 1 集生成组 grp005(sh006–sh008,Σ15s)生成多镜头组视频。
  组 prompt 见 grp005.json(Shot 1:/Shot 2:/Shot 3: 结构),锚点包用已校正版,
  前组尾帧 grp004.last_frame.png 作续接锚;开 generate_audio 与 return_last_frame;
  禁止出现 style.json 负面清单中的元素。
inputs:
  - assets/prompts/ep01/grp005.json
  - assets/keyframes/ep01/grp005/meta.json   # 锚点包清单;引用式复用锚的实体图按 grp005.json refs 原路径取,新生成锚图在包内
  - assets/clips/ep01/grp004.last_frame.png
expected_output:
  path: assets/clips/ep01/grp005.mp4
  spec: { duration: "15s ±1", resolution: "1920x1080", fps: 24, audio: true }
acceptance:
  auto: [duration_check, resolution_check, fps_check, audio_track_check, last_frame_saved]
  eval_rubric: visual_gen_v1     # 阈值 80
  qa: [visual-qa]
max_retries: 3
on_fail: escalate_human
```

Agent 完成后必须回执:`<项目目录>/runs/<task_id>/result.json`(产物路径、自检结果、遇到的设定冲突上报)。

**交付方式纪律(执行 Agent)**:工单要的是产物文件本身——JSON/MD 类设计产物直接逐份写出终稿,不写「生成脚本」再跑(§2「静态数据产物直接落盘」);
批处理工单一次做完不拆批;确需脚本(计算/媒体处理/机检)才写,落项目 `code/`,`runs/<task_id>/` 只放运行记录。
orchestrator 派 for_each 批处理单时在 `instruction` 末尾明写一句「直接逐份落 JSON,不要写生成脚本、不要分批」。

**inline 工单的上下文约定**:工单 `context_package: inline` 时,执行 Agent 以工单本体的 `instruction`/`inputs` 为完整上下文,不得因缺 `runs/<task_id>/context.md` 拒单,也不得自行读全库补料(缺料照旧走回执上报);各 Agent SOUL 输入表中的「Context Package / context.md」行在 inline 工单下即指工单本体,无需另有文件。

### 6.1 运行记录统一 schema(每个任务必备四件套)

`<项目目录>/runs/<task_id>/` 在任务置为 done/passed 前必须齐备以下四个文件,**缺一不得关单**(唯一豁免:inline 轻量包工单免 `context.md`,以工单本体的 `instruction`/`inputs` 充当上下文记录,前提是工单 `context_package: inline`;full 包工单缺 `context.md` 照旧不得关单):

| 文件 | 写入方 | 内容要求 |
|---|---|---|
| `context.md` | context | Context Package(含 token 预算声明);**仅 full 包工单必备**(§1 原则 5),inline 工单免 |
| `result.json` | 责任 Agent | 产物路径、自检结果、冲突上报;`status` 只允许 `completed / failed / escalated`,禁止 `completed_with_*` 之类带病状态——有残留问题必须开缺陷单并在 result 里引用缺陷 ID |
| `eval.json` | evaluation | **所有产出型任务必须有评分**(含 p3 及以后各阶段);逐维度得分 + verdict;无 eval 的产物不得登记进受控版本(项目「审核设置·质量评委」设 0 时全程免评分,本行不适用) |
| `meta.json` | orchestrator(收单钩子) | `run_id`(仅 12 位 hex,禁止自由文本)、`attempt`、`agent`、`model`(实际模型名)、`tokens`(实际输入/输出,非估算)、`started_at`/`finished_at`(ISO 8601,时区统一 `+08:00`)、`inputs[]`(路径 + sha256)、`outputs[]`(路径 + 登记版本 `@vN`) |

**任务收尾钩子(on_task_complete,orchestrator 执行)**:任务回执后必须依次 (a) 校验四件套齐备(inline 工单按上文豁免 `context.md`);(b) 调用 version 对全部产物**实时登记**(禁止依赖事后审计补录;补录仅限一次性历史修复,changelog 须标注 `backfill` 并保留真实产出 task_id);(c) 更新 `<项目目录>/runs/dag.json` 对应节点的 `state` 与 `run_id`。三步未完成,节点 state 不得变更为 done/passed;dag.json 与 gate 文件、runs/ 产物三者不一致视为调度缺陷。

**可选第五件:经验卡 `lesson.md`(2026-08-12,默认不写)**。仅当**同时满足**以下全部条件时,orchestrator 在收尾钩子随四件套补写 `runs/<task_id>/lesson.md`:① 该任务经历了缺陷单闭环(status: resolved)或 attempt ≥ 3 后才通过;② 教训是**机制性的**(工具用法/渠道参数/流程约束,换一个项目仍然成立),与本项目情节、人物、文本内容无关;③ 现有 SOUL.md/WORKFLOW.md 尚无同款条目。三条有一条不满足就**不写**——常规任务、内容性返工(写得不好重写)、已有规约覆盖的旧坑,一律不产出经验卡。格式:frontmatter(`title`/`category`(provider|workflow|tooling)/`severity`/`provider`/`model`/`agents`/`date`/`evidence`(run_id 或缺陷 ID))+ 正文两节「现象与根因」「怎么做才对」;**正文禁止引用项目原文、人物名与情节**,禁止出现绝对路径与 API Key。用途:设置菜单「高级→诊断数据」会扫描各项目 `runs/*/lesson.md` 供用户逐张预览勾选、打包进诊断导出 zip 手动提交给开发者(见 `modules/diagnostics.py`;永不自动上传),用于沉淀回 SOUL/WORKFLOW 规约。

**中英文与格式纪律**:记录字段名一律英文 snake_case;时间戳一律完整 ISO 8601(禁止只写日期);同一 gate/eval 不得复制粘贴时间戳。

## 7. 质量体系:评分、缺陷单、返工

**Rubric 家族**(evaluation Agent 维护,存 `agents/00-orchestration/evaluation/rubrics/`):

| rubric | 适用 | 维度示例(权重) |
|---|---|---|
| extraction_v1 | 设定/事件抽取类 | 忠实原文 40 / 出处可溯 20 / 完整性 25 / 格式 15 |
| analysis_v1 | 结构/性格分析类 | 证据充分 35 / 洞察深度 25 / 自洽 25 / 格式 15 |
| writing_v1 | 剧本/旁白类 | 忠实原著 30 / 戏剧性 25 / 对白自然 20 / 可拍性 15 / 格式 10 |
| creative_v1 | 风格/钩子/导演阐述 | 契合原著气质 30 / 独特性 25 / 可执行 30 / 格式 15 |
| visual_plan_v1 | 分镜/构图类 | 叙事清晰 30 / 视觉多样性 20 / 可生成性 30 / 规范 20 |
| visual_gen_v1 | 图像/视频产物 | 与设计稿匹配 35 / 技术质量 30 / 角色一致 25 / 无违禁 10 |
| edit_v1 | 剪辑产物 | 节奏 35 / 音画配合 30 / 技术规范 20 / 完成度 15 |

**缺陷单格式**(`qa/defects/<id>.json`):

命名强制 `DEF-<phase|epNN>-<domain>-<seq>.json`(如 `DEF-p2-worldqa-0001.json`、`DEF-ep01-visual-0042.json`),**禁止自由命名与 .md 缺陷单**;字段名一律英文,以下字段必填:

```json
{
  "defect_id": "DEF-ep01-visual-0042",
  "severity": "blocker | major | minor",
  "found_by": "11-qa/visual-qa",
  "task_id": "qa-ep01-visual",
  "date": "2026-07-04T18:07:00+08:00",
  "artifact": "assets/clips/ep01/sh014.mp4 @v2",
  "violated": "人脸相似度 0.71 < 0.85",
  "evidence": "qa/evidence/DEF-ep01-visual-0042.png",
  "assigned_to": "08-video-gen/character-consistency",
  "status": "open | fixing | verify | closed | waived",
  "resolution": null,
  "verified_by": null
}
```

生命周期规则:每张缺陷单必须有 `assigned_to`(orchestrator 路由时补齐);`closed` 必须填 `resolution` 与 `verified_by`(修复者不得自证关闭);`waived` 必须挂对应闸门 waiver 记录(见下)。QA 报告中的放行条件(如「某缺陷须在下一阶段前闭环」)由 orchestrator 转为该缺陷单的 `due_gate` 字段并在对应闸门机检强制。

**返工规则**:
1. 评分 <80 或 QA 缺陷 → 自动退回 + 意见,`attempt+1`,最多 1 次(默认;以用户「Agent 高级设置→重跑次数」为准);
2. 达到重跑次数上限仍不过 → 升级人工,附全部尝试与意见;
3. 缺陷根因在上游(如设定本身错)→ 不许下游打补丁,缺陷单改派上游,orchestrator 按 DAG 标脏并只重跑受影响链路;
4. **返修中的一切重生成受 §7E 形象红线约束**:只准复用在库概念图作形象锚、prompt 必带 style.json 风格锚,所涉概念图缺失时停手上报补齐——严禁修正环节新造人物/场景/道具形象(机检 repair_ref_anchored);
5. 通过闸门的版本由 version Agent 冻结,后续修改必须新开版本。

**闸门放行硬约束(G0–G10 通用,含 H1–H5)**:
1. **缺陷清零机检**:闸门判定前 orchestrator 必须机检本闸门范围内的缺陷单——`status=open|fixing|verify` 的 blocker/major = 0,且所有 `due_gate` 到期缺陷已闭环;否则闸门只能给出 `verdict: PASS_WITH_WAIVER` 或 `HOLD`,不存在「带 open major 直接 PASS」。
2. **waiver 显式化**:`PASS_WITH_WAIVER` 时 gate JSON 必须逐条列出 `waivers[]`:`{defect_id, reason, signed_by, follow_up}`(follow_up = 后续闭环安排或永久豁免声明);被豁免的缺陷单状态改为 `waived` 并回链该 gate 文件。
3. **QA 建议不可静默推翻**:任一会签 QA 报告 `recommendation` 为暂缓/hold 时,闸门不得直接 signed;人工坚持放行的,按 waiver 流程记录推翻理由与签字人。
4. **人工检查项不可自我豁免**:gate 定义中的人工 QA 项未执行时闸门不得 PASS;确需跳过的走 waiver 记录。
5. **升级裁决与闸门分离**:单任务的人工升级裁决(如三次不过后选择接受)只解锁该任务,不等同于闸门/H 点签字;其接受的残留问题必须转成缺陷单进入闸门机检范围。
6. **禁止事后补票**:阶段任务与评分必须在闸门判定前完成;`eval_mode: retroactive` 仅限一次性历史修复,常态流程出现即为调度缺陷。
7. **gate JSON 统一 schema**:`{phase, gate, checkpoint, decided_by, decided_at(完整 ISO 8601 +08:00,取实际决策时刻), verdict: PASS|PASS_WITH_WAIVER|HOLD, status, waivers[], qa_reports[], inputs, note}`。
8. **人工签字必须先建单**:`human:true` 节点依赖全部完成后,orchestrator 必须立即执行 `python3 services/runtime/dispatch.py --confirm "【<checkpoint>】<审阅要点与放行影响>" --sign --project <slug>`；未收到明确「签字」不得把节点写成 `passed`。每次总制片运行结束前必须检查 DAG 前沿，有已解锁人工节点却没有签字单时不得仅以文字汇报后关单。运行时会为漏单补建相同语义的永久签字单作为兜底，但只建单、不自动放行；用户签字后仍由 orchestrator 重做本节机检、落 gate JSON 并派 version 冻结。

## 8. 人工确认点汇总

| 点位 | 时机 | 用户确认什么 |
|---|---|---|
| H1 | G2 后 | 世界圣经摘要(设定理解对不对) |
| H1A | G3 后 | 角色与资产设定:角色形象/性格/关系/声音、生物、场景环境(进美术与剧本前锁定) |
| H2 | G4 后 | 美术风格 + 主角人设图(风格锁定) |
| H3 | G5 后 | 第 1 集剧本 |
| H3A(每集) | G6 后、Phase 7 前 | 本集分镜设定:分镜脚本/生成组划分/旁白挂点及估时适配/逐组音频形态与无声组判定/概念图覆盖审计结果(§6A,新出场实体补图与遗漏主角标注)(「分镜设定」预览页审看;签字前不生成视频,签字后另有旁白实测适配机检拦在 p7-video 前,§7D);**同时确认本项目「视频提示词技能」:自动(按生效视频模型)/手选/跳过,签字即冻结快照(§7F)** |
| H3B(每集) | G7 后、Phase 9 前 | 本集全部生成组终版 clip(「视频预览」页审看组画面/原生音频质量;签字前不进剪辑合成) |
| H4 | G9 后 | 第 1 集成片(试点集全片审看) |
| H5 | G10 后 | 发布签字 |

> 成片方式**不设人工确认点**:G7/H3B 后终版组 clip 自动走默认路径(成片分辨率与草稿档不同时仅 upscale 超分,严禁成片档重生成;见 §7B)——H3B 签的是组内容质量,不是成片方式。

**试点集策略**:第 1 集全流程走通并经 H4 签字后,后续集才批量并行,以免风格/质量问题被放大到全季。

## 9. 生成模型调用(genmedia 统一模块)

图像/视频生成的**渠道与模型由用户在 Web 客户端「生成服务」页配置**(落盘 `data/.videoagents/genconfig.json`),
生成类 Agent 一律通过统一模块调用,**不自行挑选模型、不直连各家 API**:

> **Agent 级模型配置**:每个 Agent 可在控制台(对话页「模型」按钮)单独配置执行引擎/文字模型
> 及图像/视频渠道,落盘 `data/.videoagents/agentmodels.json`,优先级高于全局设置;未单独配置时按分类默认
> (机械活→claude·sonnet;分析/评分→codex·gpt-5.5;创作核心→claude·opus)。
> 派单时该配置自动生效。**报错/返工禁止切换执行引擎**;总制片不得用 `--engine` 覆盖成员引擎
> (服务端会忽略换引擎请求)。同引擎内如需指定模型可用 `--model`。
> **赛马(改派多 Agent 并行重做同一任务、择优交付)仅限用户明确下令,严禁自行发起**,且赛马同样不换引擎。

```bash
# 查看当前生效渠道与模型(接工单后先跑一次,把结果记入产物 meta)
python3 modules/genmedia.py info

# 生成图像(关键帧/概念图/参考图)
python3 modules/genmedia.py image \
  --prompt "<英文正向 prompt>" --negative "<负面词>" \
  --output assets/keyframes/ep01/sh014/first_01.png \
  --aspect 16:9 \                       # 或 --size 2560x1440 精确尺寸
  --ref assets/concepts/characters/c003/sheet.png \  # 参考图可多张(角色整版三视图 sheet/组动线俯视图 blocking_maps/grpNNN.png/场景 9 宫格图 grid_9views.png)
  --n 4                                 # 候选张数,>1 时自动加 _01.._04 后缀

# 生成视频(组 clip;Seedance 2.0 多镜头多参考图模式,默认路径)
python3 modules/genmedia.py video \
  --prompt "<组级多镜头 prompt:Shot 1:.../Shot 2:...,素材按 [Image N] 引用,{对白}>" \
  --output assets/clips/ep01/grp005.mp4 \
  --ref assets/concepts/characters/CHAR-0001/sheet.png \      # 角色整版三视图(复用锚直接用概念库原路径,2026-08-24 引用化;新生成锚才用 keyframes/ 包内路径)
        directing/ep01/blocking_maps/grp005.png \   # 组人物动线俯视图(起点/动线/终点标注,shot-planning 渲染;2026-08-19)
        assets/concepts/scenes/SCN-0012/grid_9views.png \   # 场景 9 宫格多角度图
        assets/clips/ep01/grp004.last_frame.png \  # 前组尾帧续接锚(首组无此项)
  --audio-ref assets/audio/voice/refs/CHAR-0001_voiceprint.mp3 \
              assets/audio/voice/refs/CHAR-0002_voiceprint.mp3 \  # 对白组=每个说话角色各自的 voiceprint 样本(≤3 段且总时长 ≤15.2s=方舟硬限,audioref_total_le_15s,样本 ≤5s/段;按年龄形态选 variant 版;prompt 逐角色 @Audio N 绑定,仅锚嗓音特点,§8A:严禁写"原样使用人声/口型同步"类指令)
  --generate-audio on \
  --return-last-frame assets/clips/ep01/grp005.last_frame.png \
  --duration 15 --aspect 16:9 --resolution <按「输出设置」草稿/成片档>
# 注意:--ref(≤9 张,建议 4–5)与 --first-frame/--last-frame 互斥;
#      参考图/首尾帧无最小像素门槛(旧「≥3,686,400 像素火山硬限」2026-09-01 实测已不存在,废止;
#      新生成锚点图仍按平台统一出图规格 2560x1440 / 1440x2560 出图,复用图/尾帧原样可挂);
#      真人脸审核拒收(条件处置):部分视频渠道审核拒绝含真实人脸(photoreal face)的参考图——
#      仅当排查定位到被拒图为含人脸的角色 sheet/锚点图时,对图中所有人物的**脸部做彩铅化**
#      (colored pencil 风格重绘脸部,身体与背景保留原图质感)后替换重提;sheet 的彩铅化
#      改造回派 character-concept(细则见其 SOUL.md),正常流程不做此处理;
#      Seedance 2.0 时长须 [4,15] 整数秒或 -1(模型自定),不支持 --seed。
#      【仅当视频模型为 Seedance 2.5(doubao-seedance-2-5-260628 / dreamina-seedance-2-5-260628)时】:
#      时长放宽为 [4,30] 整数秒或 -1(单段 30s 直出);参考上限 30图/10视频/10音频,
#      参考音频与参考视频总时长各 ≤30s;支持纯音频参考(无需搭配图/视频);分辨率仅
#      480p/720p(1080p/4k genmedia 自动压 720p);首帧/首尾帧任务 ratio 仅 adaptive
#      (genmedia 自动改写,输出与首帧图同比)。组时长与参考数量的**取用上限仍以项目
#      「视频模型设置」为准**,不得因模型能力放宽而超出该设置。

# 生成视频(单镜首尾帧图生视频;兜底路径,组生成不达标时逐镜重做)
python3 modules/genmedia.py video \
  --prompt "<视频 prompt,含运镜描述>" \
  --output assets/clips/ep01/sh014.mp4 \
  --first-frame assets/keyframes/ep01/sh014/first_01.png \
  --last-frame  assets/keyframes/ep01/sh014/last_01.png \
  --duration 4 --aspect 16:9 --resolution <按「输出设置」草稿/成片档>

# V2V 定向修改(局部穿帮修复;Seedance 2.0 编辑能力,video-generation 按 repair_mode:
# v2v_edit 缺陷单执行——服饰/道具/元素级穿帮优先走此路,成本远低于整组重 roll)
python3 modules/genmedia.py video \
  --prompt "将视频1中<穿帮对象与改法>,其余画面、人物动作、运镜、节奏与声音保持完全不变" \
  --output assets/clips/ep01/grp012.mp4 \
  --ref-video assets/clips/ep01/archive/grp012_<时间戳>/grp012.mp4 \
  --ref <正确样式参考图,可选;涉人物/场景/道具形象时必须取 assets/concepts/ 在库概念图,严禁临时新生成样式图(§7E)> \
  --generate-audio on --duration <与原组一致> --aspect 16:9 --resolution <草稿档>
# 注意:--ref-video ≤3 个、单个 2-15s 且总时长 ≤15s、单文件 ≤45MB,与首尾帧互斥
#      (仅当视频模型为 Seedance 2.5 时放宽:≤10 个、单个 2-30s 且总时长 ≤30s;且 2.5 的
#      视频编辑任务 ratio 仅支持 adaptive、duration 仅支持 -1(自动与输入视频等长),
#      视频延长任务 ratio 仅支持 adaptive,违规将异步报错 InvalidParameter.TaskTypeConstraint);
#      方舟要求 reference_video 为公网 URL:参考视频自动上传对象存储换预签名链接,
#      需先在 Web 控制台「设置 → 文件托管」配好存储渠道(火山 TOS/阿里 OSS/腾讯 COS/
#      S3 兼容,生效=选中标签页;SDK 按需装 tos/oss2/cos-python-sdk-v5/boto3);
#      素材引用一律「视频n/图片n」序号;输入视频秒数计费(含视频输入档);
#      输出是整段重渲染——visual-qa 复检必做,对白组另跑声学快检(音色漂移即回退整组重 roll)。

# 生成音乐(BGM;music Agent 后期专用,严禁在组视频 prompt 里生成音乐)
python3 modules/genmedia.py music \
  --prompt "<英文音乐描述:曲风/情绪/乐器/节奏,Lyria 3 Pro 可含歌词>" \
  --output assets/audio/bgm/ep01/ep01_bgm_02.mp3 \
  [--duration <秒>]                     # elevenlabs(Eleven Music):3–600s,可按 cue 时长精确出段;comfyui(ACE-Step):1–240s;openrouter 忽略;省略=模型自定
# openrouter 时长由模型决定:Lyria 3 Pro 完整歌曲、Lyria 3 Clip 30s 片段/Loop;格式按扩展名
# (openrouter:mp3/wav/flac/opus;elevenlabs:仅 mp3/opus,force_instrumental 由「生成模型」页配置,默认纯音乐)。

# TTS 干声(narrator 旁白后期轨、voice-generation 角色 voiceprint 样本——样本仅作 reference_audio 嗓音特点锚,严禁进成片对白,§8A 红线;
#          角色音色先查项目级选角注册表 assets/audio/voice/casting.json,缺条目先登记,§8A;
#          项目「对白配音=后期配音」时的对白逐句 TTS 不直接调本命令,统一走 code/dub_group.py(内部调 generate_tts 并按开口时段贴合,§8C))
python3 modules/genmedia.py tts \
  --text "<旁白/台词文本>" \
  --output assets/audio/narration/ep01/ep01_narr_003.mp3 \
  [--character <CHAR-ID;ComfyUI 角色音色样本必传,旁白不传>] [--variant <年龄形态>] \
  [--voice <音色;云渠道角色配音按 casting 传(OpenRouter=音色名、火山=speaker 名、ElevenLabs=voice_id),旁白不传——自动用「生成模型」页生效渠道的「默认音色」;火山 seed-audio-1.0 描述定制:禁传,speaker 名会被忽略;ComfyUI 禁止手填>] \
  [--speed 1.0] [--instructions "<语气/情绪指令;OpenAI 系模型生效,火山注入情绪指令,ComfyUI 参与音色匹配>"]
# ComfyUI TTS 会读取项目角色设定并按内置音色目录 modules/timbre_catalog.json(索引远端 ComfyUI-Index-TTS/TimbreModel 音频库,首次使用自动下载缓存到 data/TimbreModel/)自动选取、上传参考音频,禁止手填 --voice；项目目录内没有 WAV/MP3 不构成阻塞。旧云渠道 casting 的 eve/ara 等音色名不能传给 ComfyUI,切换渠道后须自动重选并更新 casting。
# 火山渠道模型为 seed-audio-1.0(Doubao-音频生成 1.0,描述定制嗓音)时同 ComfyUI 纪律:角色传 --character、旁白靠 --instructions 描述声线,禁传 --voice;声线描述由 voice.json 声学字段自动拼装,项目已有冻结 voiceprint 样本时自动作 @音频1 参考锚(逐句/逐段合成不漂音色,§8A)。
```

Python 内调用(批量循环时省进程开销):`from modules.genmedia import generate_image, generate_video, generate_music, generate_tts`。

| 渠道(按配置自动路由) | 图像 | 视频 | 音乐 | TTS | 说明 |
|---|---|---|---|---|---|
| OpenRouter | ✓ | ✓ | ✓ | ✓ | 云端;视频异步轮询;音乐流式返回(Lyria 3 Pro 整曲 / Clip 30s);TTS 走 /audio/speech 字节流 |
| Ideogram | ✓ | — | — | — | 云端 |
| 火山引擎(方舟) | ✓ | ✓ | — | — | 云端;图像 Seedream 系列同步返回,视频异步任务自动轮询 |
| BytePlus(海外 ModelArk) | ✓ | ✓ | — | — | 云端;与方舟同构 API(ap-southeast-1),Seedream/Seedance 模型 ID 无 doubao- 前缀(Seedance 2.0/2.5 为 dreamina-seedance-2-*) |
| Fal | — | ✓ | — | — | 云端;queue.fal.run 异步队列,托管 Seedance 2.0/2.5、MiniMax H3、Kling 3.0 等端点;模型 ID 填家族前缀(bytedance/seedance-2.0、minimax/h3、fal-ai/kling-video/v3/pro),genmedia 按输入自动补 text-/image-/reference-to-video 任务段;Seedance/Kling 无 seed 入参 |
| ComfyUI | ✓ | ✓ | — | — | 本地;视频必须在设置页配好 API 格式工作流 JSON,占位符见模块头注释 |

**生成类 Agent 的纪律**:
1. 每次生成把「渠道/模型(`info` 输出)、seed、prompt、参考图」写入产物 `meta.json`,保证可复现;
2. 换 seed 重 roll 用 `--seed`;候选批量用 `--n`,不要自己写循环脚本拼文件名;
3. 生成失败(未配 Key、渠道超时、内容拦截)**如实写入回执并上报,严禁伪造或占位产物**;
4. 模型能力不满足工单要求(如运镜类型不支持)→ 上报 orchestrator,不擅自降级替换。

## 10. Agent 插件机制(团队扩展)

内置 83 个 Agent 覆盖「小说→视频」主流程;主流程之外的衍生业务(如基于世界圣经的衍生小说创作)
通过**声明式插件**扩展团队,不改内核。插件不含任何可执行代码——SOUL.md 即身份、YAML 即流程,
与内置 Agent 享受完全相同的派单/评审/闸门待遇。编写规范详见 `plugins/README.md`。

### 10.1 插件包格式

```
plugins/<plugin-name>/
├── plugin.json            # manifest(JSON,服务端零 yaml 依赖;字段见下)
├── README.md              # 插件说明(建议)
├── agents/
│   └── <NN-category>/<name>/SOUL.md   # 沿用 agents/_TEMPLATE.md 骨架,零新约定
└── workflows/<name>.yaml  # 可选:插件独立流程 DAG(与 agents/workflow.yaml 同构)
```

`plugin.json` 核心字段:`name`(须与目录名一致)、`version`、`description`、
`categories`(新类别注册:`{"13-derivative-fiction": "衍生创作"}`,编号沿用 13+ 段避让内置 00–12)、
`agents`(成员清单:`[{"id": "13-derivative-fiction/prose-writer", "stateless": false, "dispatcher": false}]`;
也可把成员放进内置类别如 `11-qa/`,前缀规则照常生效)、
`workflows`(流程 DAG 文件相对路径)、`outputs_ns`(产物命名空间,如 `derivative`——插件产物一律写
`data/projects/<slug>/<outputs_ns>/` 内,runs/、qa/defects/ 等通用记录不受限)、
`requires.artifacts`(前置产物声明,如需正史 `bible/` 冻结)。

### 10.2 发现、启停与生效

- **复制目录进 `plugins/` 即安装**(或 Web 控制台 ⚙️ 设置 →「插件」页上传 zip);**默认停用**,
  须在「插件」页手动启用。manifest 校验不过(id 撞名/缺 SOUL.md/JSON 损坏)的插件整体不注册,错误在「插件」页可见。
- 启用后:成员出现在控制台左侧列表(按类别编号归组),可对话、可被 orchestrator 派单;
  系统提示词自动注入其插件身份(所属插件、流程文件、产物命名空间);
  orchestrator 的系统提示词自动获得「已启用插件」清单与调度纪律。
- **agent id 全局唯一**:与内置团队或其他插件撞名的成员会被拒绝注册(错误可见),先到先得。

### 10.3 插件流程的调度纪律

1. 插件 `workflows/*.yaml` 与主 `workflow.yaml` **同等地位**:机器可读 DAG,约定(for_each 扇出、
   validation 三道闸、gate/human)完全一致;节点 id 用插件自己的前缀(如 `nv0-premise`),不与主流程冲突。
2. orchestrator 接到插件业务后,把插件 DAG 节点**并入项目 `runs/dag.json` 统一跟踪**
   (可与主流程共存),改完照常 `dagcheck.py --strict`。
3. 工单格式 §6、运行记录四件套 §6.1、评分与缺陷单 §7、文件名 ASCII 红线(§1 原则 9)对插件任务同等生效;
   人工签字点用 `--sign`,与 H1–H5 同规格。
4. `requires.artifacts` 未满足时先补主流程对应阶段,不得硬跑。
5. 插件若产生新设定,**不得写入正史 `bible/`**——写自己命名空间下的 `bible-delta/`,
   升格进正史必须走 memory-bible 仲裁 + 用户签字(见 memory-bible SOUL.md「衍生分支圣经」)。
6. **主流程分支联动跳过(`main_dag_on_start.skip`,可选声明)**:插件流程 YAML 可声明顶层
   `main_dag_on_start.skip`——与本插件业务无关的主流程节点清单(`p4-*` 为前缀通配,匹配含按集
   展开后的一切以 `p4-` 开头的节点;闸门 `g4` 等为精确 id)。orchestrator 在把插件 DAG 节点并入
   `runs/dag.json` 的**同一次改动**中执行,条件白名单:
   - **仅当清单内主流程节点全部处于未开工状态(pending/template/blocked)** 才执行:命中节点
     state 置 `skipped`,并写 `skip_reason`(注明插件名与「可恢复」);
   - 清单内**存在任一已开工节点**(dispatched/running/failed/done/passed/passed_human_override/expanded)
     即视为该分支业务进行中,**一个也不跳**,仅在汇报中说明;
   - 用户在同一指令中明确要求同时推进被跳分支(如「小说和视频都做」)时,不执行跳过。
   `skipped` 是挂起不是放弃:用户其后要求推进对应业务(如视频成片)时,orchestrator 把这些节点
   恢复 `pending` 重新排产。闸门判定时 skipped 依赖视为已满足(如 g3 不因 p3-voice/p3-lighting
   被跳过而阻塞签字)。每次改动照常 `dagcheck.py --strict`。首个使用方:derivative-fiction
   (小说只依赖正史 bible/,跳过 p3-voice/p3-lighting 与 p4–p11 全部视听制作节点);
   fusion-fiction 直改正史喂回视频主流程,**不适用**本机制。

### 10.4 安全红线

- SOUL.md 会**逐字注入模型系统提示词**,插件即提示词注入面——**安装第三方插件前必须人工审阅全部内容**。
- 插件是纯声明式的(plugin.json + SOUL.md + workflows YAML),**不允许包含可执行代码**;
  Agent 运行期为完成任务写的一次性脚本照常落项目 `code/`,与内置成员同规。
- 产物越出 `outputs_ns` 命名空间写入(尤其改写正史 bible/、其他插件命名空间)按调度缺陷处理。


### 白模参考视频扩展流程（2026-09-07）

用户要求白模参考视频时，orchestrator 在布局包完成后为每场景派 `05-scenes/scene-modeling`，产出带米制尺度与几何体的 `bible/scenes/<sid>/whitebox.json`；在分镜定稿、blocking、camera-movement、continuity-planning 完成后，逐集派 `07-directing/whitebox-staging`，写各组数值时间线并调用宿主 `code/render_whitebox.py --export` 输出双视角视频。两个工单在对应分镜确认前完成，已有非空间流程不强制补派。基础数据、比例、姿态、继承/切换和导出验收统一遵循 `docs/whitebox.md`，不得复制渲染器到项目。编译成功不代表视频导出成功，回执分别记录。

**防穿模工作流注释（dzg6/ep01 grp012、grp014 反馈）**：白模交付必须分别检查「摄像机可见性」和「主体完整动线的碰撞」，前者通过不代表后者通过。blocking / whitebox-staging 以人物、生物、坐骑和随身道具的实际包围体检查插值全过程、姿态转换及组间衔接，不能只验起终点或镜头首中尾。grp012 型迎面/绕行场面先预留独立通道、绕行点或错峰通行，禁止让运动人物直接穿过静止人物；grp014 型过门动作先穿过门洞并让全身离开墙厚范围，再转弯，禁止对门内点和转弯后点直接作穿墙的直线插值。普通非接触动作参考净距为人物间0.20m、主体与障碍物间0.15m；明确接触动作按局部接触另审，不得整体关闭碰撞检查。scene-modeling 提供真实墙厚、门洞和可行走区域，按包围半径及余量检查门洞净宽。发现冲突由责任工位修正路径、节拍或有依据的几何模型，保留原分镜时长、角色身份与调度意图；需更改已定主路线时同步回写其权威动线资料。**不得通过改机位、隐藏角色、删墙、透明墙、无依据扩门或缩小人物掩盖穿模**。交付回执记录冲突对象、时间、最小间距、修正方法及连续扫掠/逐帧复查结果；未消除的碰撞必须列为待修，不能标记白模验收通过。详细方法见 `docs/whitebox.md`「防穿模检查」。

**室内机位遮挡注释（dzg6/ep01 sh040 反馈）**：保留墙壁作为空间边界和碰撞依据；隔墙取景先将机位移入实际可拍空间，再核对该镜需显示的每位主体的脸部、上身和轮廓在整个镜头中的可见性，不能只对主角头中心发一条视线便判定通过。多人镜头须覆盖所有主要人物，走位、起坐、运镜前后都要查。若机位调整导致景别或视角变化，白模计划须记录原因，涉及正式镜头改动则交回导演处理。辅助空间观察可另设剖切视图，但剖切不能删除碰撞墙体，也不能冒充正常摄像机/导出视图通过遮挡验收；确需摄影棚可拆墙方案时必须明确标注。

**摄像机漏人注释（dzg6/ep01 grp050 反馈）**：角色列表是叙事登记，不等于完整入画名单。白模必须同时读取构图 layers、notes 和草描中的静止人物、前景与背景人物；例如 sh089 的老道儿虽不在 shot.characters 中，仍应在两位发笑人物中间出现。默认省略 visible_actor_ids，让实际机位与遮挡决定画面；特殊隐藏须在 basis 写明排除对象和理由，不得靠隐藏人物伪造单人取景或绕过碰撞检查。排查顺序为关键帧可见性、摄像机名单/渲染层、视锥与画幅、几何遮挡。验收按构图独立列出应入画人物，逐镜逐帧检查实际摄像机层；空间视图可见或只验主角投影均不足以通过。

**场次人物自动关联（grp048/sh085 后续反馈）**：以同集 scene_no + scene_id 汇总所有在场人物/生物为 scene_cast，无对白、静止及镜外人物也关联本场次参考图并进入白模；同地点不同场次不混用。shot.characters/characters_union 继续用于叙事和分组统计，不等于空间人员名单。shot-planning 定稿、whitebox-staging 开工、prompt 完成后执行 `python code/sync_scene_cast.py --project <slug> --ep <ep> --write [grp…]`；视频开跑前无 --write 复核并执行 refs_referenced_check.py。既有 Image 序号保留，新增图追加并绑定；禁止用“exactly N / no third character”全组人数锁删掉在场者。缺图、缺空间锚点和超渠道上限明确失败，不丢人物凑数。真正离场/远程声音须有明确状态；白模继承最后位置、姿态和退场关键帧，机位决定入画。精确补充轨迹写 scene_actors，特殊镜头过滤需 visibility_override_reason；每镜必现人物与遮挡独立复查。完整数据约定见 docs/whitebox.md。

**对话身体朝向（grp028–grp035 反馈）**：grp028–grp030 的“面向老道儿”被文字“朝南”覆盖，产生约85°偏差；grp031–grp034 则沿用旧稿“并肩朝东”，两人同向。今后先登记对话对象 ID，身体 yaw 由双方同一时刻世界坐标计算，不从画面左右或词语方位直接套角度；面对面两人分别算朝向，不复制同一角度。身体、头部/眼神与行走方向分开：不对视不等于背身；走向矮凳阶段可同向行走，落座交谈前完成转身。逐帧检查正面向量与对象向量的夹角，覆盖走位、起坐、切镜、组间首尾；直接面对面参考误差≤3°。朝向变更后复核正反打是否仍拍到正确面向。同步修订上游动线、逐镜调度、构图、草描、prompt 和白模中的旧朝向，避免重生成恢复错误；具体检查见 docs/whitebox.md。
