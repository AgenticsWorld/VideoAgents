# derivative-fiction — 衍生小说创作插件

基于项目既有的世界圣经(`bible/`)、角色、场景资产,创作衍生小说(前传/番外/支线/if 线)。
主流程是「小说→视频」的**解析方向**;本插件补上**生成方向**的文本工位,设定资产全部复用、正史只读。

## 团队(8 个 Agent)

| Agent | 粒度 | 职责 |
|---|---|---|
| `13-derivative-fiction/derivative-planner` 衍生企划 | 全书级 | 选题定位、时间线锚点、正史可用面与禁区、分歧点声明 |
| `13-derivative-fiction/novel-outline` 小说架构 | 全书级 | 卷章结构、主线冲突、人物弧光 |
| `13-derivative-fiction/prose-style` 文风圣经 | 全书级 | 叙事人称/视角、文风基调、章节体例(多章一致性锚点) |
| `13-derivative-fiction/chapter-planner` 章节规划 | 全书级 | 大纲拆章:每章节拍、出场角色、断章钩子 |
| `13-derivative-fiction/prose-writer` 章节写作 | 每章扇出 | 按章计划写正文,遵守文风圣经与正史设定 |
| `13-derivative-fiction/line-editor` 润色定稿 | 每章级 | 文字打磨、跨章衔接;不改剧情 |
| `11-qa/prose-qa` 文本质量审核 | 每章/全书 | 文笔、节奏、视角漂移、重复度、AI 腔 |
| `12-publishing/novel-release` 小说发布排版 | 全书级 | 合订、EPUB/TXT、平台包 |

复用内置:`11-qa/logic-qa`(剧情逻辑)、`11-qa/world-consistency-qa`(设定不越界)、
`11-qa/character-consistency-qa`(人设/语言风格)、`00-orchestration` 调度层全部、
`01-story/hook`(断章钩子顾问会签)。

## 流程(workflows/novel.yaml)

```
nv0 衍生立项(premise) → gn0
→ nv1 大纲 + 文风圣经(并行) → gn1【DH1 企划与大纲签字】
→ nv2 章节规划 → gn2
→ nv3 写作→润色(每章流水;试点章策略) → gn3【DH2 首章试读签字】
→ nv4 全书终审(logic/world/char/prose QA + bible-delta 审计) → gn4【DH3 发布签字】
→ nv5 发布排版
```

## 产物(命名空间 derivative/)

```
data/projects/<slug>/derivative/
├── premise.json        # 企划:题材/主角/时间线锚点/正史禁区/分歧点
├── outline.json        # 全书大纲
├── prose_style.md      # 文风圣经
├── chapters_plan.json  # 拆章总表
├── chapters/chNNN.md   # 章节正文(润色后新版本覆盖,version 管理)
├── bible-delta/        # 衍生新增设定(正史只读;升格走 memory-bible 仲裁+签字)
└── publish/            # 合订/EPUB/TXT/平台包
```

## 前置

主流程 `bible/` 已产出并过 H1(正史冻结):`world.json`、`dictionary.json`、`characters/index.json`。
无既有项目时也可先跑主流程 Phase 0–2 只建圣经,再用本插件创作。

## 与主流程视听分支的联动(main_dag_on_start.skip)

小说创作只依赖正史 bible/ 的文字设定。开工衍生小说时,orchestrator 会把主流程中**尚未开工**的
视听制作专用节点(`p3-voice` 声纹、`p3-lighting` 灯光、`p4`–`p11` 全部阶段及其闸门 g4–g10)
置为 `skipped` 挂起,避免排产误入与小说无关的分支;若其中任一节点已开工(视频制作进行中)则一个也不跳。
挂起可恢复:之后要做视频时,orchestrator 把这些节点恢复 `pending` 重新排产。
声明见 `workflows/novel.yaml` 顶层 `main_dag_on_start`,语义见 `agents/WORKFLOW.md` §10.3 第 6 条。
