# agents/ — 小说→视频 多 Agent 制作团队

基于 `data/Novel_to_Video_MultiAgent_System.xlsx` 的总体规划落地:83 个 Agent、13 个类别。
每个 Agent 一个目录,目录下的 `SOUL.md` 定义它的职责、输入输出、工作指令格式与质量标准。

- **流程权威**:`WORKFLOW.md`(人读)/ `workflow.yaml`(orchestrator 执行输入)
- **项目数据**:`data/projects/<slug>/`(布局见 WORKFLOW.md §2)

## 目录结构(编号 = 大致流水线顺序;实际依赖以 DAG 为准)

| 目录 | 类别 | Agent 数 | 成员 |
|---|---|---|---|
| `00-orchestration/` | 调度层(贯穿全程) | 5 | workflow-orchestrator, memory-bible, context, version, evaluation |
| `01-story/` | 剧情 | 10 | novel-parser, story-structure, event, timeline-story, screenplay, narration, dialogue-rewrite, episode-planner, hook, pacing |
| `02-worldbuilding/` | 世界设定 | 9 | world, timeline, geography, religion, culture, political, economy, magic-cultivation, dictionary |
| `03-characters/` | 角色 | 7 | character-manager, appearance, character-growth, personality, relationship, voiceprint, dialogue-style |
| `04-creatures/` | 生物资产 | 2 | creature, mount |
| `05-scenes/` | 场景资产 | 4 | scene, environment, architecture, lighting |
| `06-art/` | 美术资产 | 7 | art-director, character-concept, environment-concept, prop, costume, color-script, aspect-ratio |
| `07-directing/` | 导演 | 8 | director, storyboard, shot-planning, camera-movement, composition, cinematography, blocking, continuity-planning |
| `08-video-gen/` | 视频生成 | 7 | prompt, image-generation, character-consistency, video-generation, lip-sync, animation, upscale |
| `09-audio/` | 音频 | 6 | voice-generation, narrator, music, sound-effect, ambience, audio-mixing |
| `10-editing/` | 剪辑 | 6 | edit, transition, subtitle, caption, title, thumbnail |
| `11-qa/` | 审核 | 8 | logic-qa, character-consistency-qa, timeline-qa, world-consistency-qa, visual-qa, audio-qa, content-safety, copyright |
| `12-publishing/` | 发布 | 4 | platform-adapter, seo, metadata, publisher |

## SOUL.md 统一结构

每份 SOUL.md 按同一骨架撰写,保证 orchestrator / context Agent 可以机械地定位信息:

1. **我是谁** — 类别、流水线阶段、一句话使命
2. **职责** — 具体做什么
3. **不做什么(边界)** — 防止与相邻 Agent 抢活
4. **输入 / 输出** — 来源、产物路径、格式要点
5. **接受的工作指令** — 工单字段 + 示例
6. **质量标准(DoD)** — 机检项 + 评分 rubric 维度
7. **校验与返工** — 谁验收、不过怎么办
8. **上下游协作** — 上游、下游、需对齐的伙伴

## 演进约定

- 新增 Agent:建目录 + SOUL.md,并在 `workflow.yaml` 挂上任务节点,更新本表。
- 修改职责边界:同时改双方 SOUL.md 的「不做什么」,避免真空或重叠。
- rubric 演进:存放于 `00-orchestration/evaluation/rubrics/`,版本号后缀(`_v2`)。
