# fusion-fiction — 双书融合创作插件

把两本书融合成一部新剧本:**甲本**出故事/人物灵魂(可含美术风格),**乙本**覆盖世界观
(地理/政治/经济/宗教/文化…按维度分配矩阵与用户逐项确认)与人物形象壳。
乙本若是世界名著/公版作品,零文本输入,凭模型知识考据(名著模式)。
典型案例:古惑仔(故事/人物灵魂/画风)× 西游记(人物形象/地理/政治/经济/宗教)
→ 发生在西游世界的黑帮故事,陈浩南成为孙悟空,妖怪化作别家帮派。

## 直改正史(本插件与 §10.3.5 命名空间默认纪律的差异,特此声明)

融合产物**直接写回正史路径**(`bible/*.json`、`bible/characters/`、`story/episodes/*/screenplay.md`),
好处是控制台既有预览页(世界观/人物/场景/分镜/视频)**零改动直接呈现融合结果**。

- **分支与版本由用户自行管理**:插件不做克隆/版本登记/回滚;建议开工前自行留存项目备份
  (如 git 分支),FH1 签字项含「确认直改本项目正史」。
- **魂对账基线靠快照**:剧本覆写前 script-transposer 先把甲本原剧本完整快照存
  `fusion/episodes/{ep}/source_screenplay.md` 并登记进 transpose_log.baseline,
  QA 魂对账与用户对照全走该快照。

`fusion/` 命名空间只存过程件:维度矩阵、乙本档案、人物映射表、转换字典、移植台账、基线快照、冲突登记。

## 三个核心产物(方案的命脉)

| 产物 | 作用 |
|---|---|
| `fusion_plan.json` 维度分配矩阵 | 每个维度(story/soul/appearance/世界观六件套/art_style/era_anchor)标注取自 A\|B\|blend,与用户讨论敲定 |
| `character_map.json` 人物映射表 | 用户锁定对 + 系统推荐(带置信度/理由/top-3 备选)+ unmapped 兜底策略,整表签字 |
| `dictionary.json` 概念转换字典 | 甲世界物件/制度/黑话 → 乙世界等价物 + 现代词黑名单(时代错位机检词表);术语增量并入 bible/dictionary.json |

## 团队(6 个新工位 + 大量复用)

| Agent | 粒度 | 职责 |
|---|---|---|
| `14-fusion/fusion-planner` 融合企划 | 全书级 | 与用户敲定维度矩阵/时代锚点/乙本模式/映射种子/红线;明示直改正史并取得确认 |
| `14-fusion/classic-scholar` 乙本考据 | 全书级 | 名著模式:凭模型知识建乙本圣经与人物档案(版本口径+来源标注) |
| `14-fusion/character-mapper` 人物映射 | 全书级 | 四路证据(原型/关系图同构/性格/势力对位)推荐对应,诚实标 unmapped |
| `14-fusion/world-merger` 世界融合 | 全书级 | 逐维度把乙侧设定与合成人设卡(甲魂乙壳)写回正史 bible/,产出转换字典 |
| `14-fusion/script-transposer` 剧本移植 | 每集扇出 | 先存基线快照再覆写 story/episodes/:保节拍换壳,人名 100% 走映射表,置换 100% 查字典 |
| `11-qa/fusion-fidelity-qa` 双源保真审核 | 每集/全书 | 甲魂丢没丢(对基线快照)+乙壳破没破+跨集一致;SOUL_LOSS/SHELL_BREAK/MAP_VIOLATION/DICT_DRIFT 四类缺陷 |

复用内置:`01-story/novel-parser` + `02-worldbuilding/*` + `03-characters/character-manager`
(乙本全文模式解析,产物重定向 `fusion/source_b/`)、`01-story/dialogue-rewrite`(台词精修)、
`11-qa/logic-qa`、`world-consistency-qa`、`character-consistency-qa`、`copyright`
(甲本多为在版权作品,融合产物风险必审)、`00-orchestration` 全部贯穿服务。

## 流程(workflows/fusion.yaml)

```
fs0 融合立项(维度矩阵) → gf0【FH1 融合蓝图签字】
→ fs1 乙本摄入(classic 考据 | fulltext 解析,二选一分支) → gf1
→ fs2 人物映射 → gf2【FH2 人物对应表签字:可锁定/换选/增删】
→ fs3 世界融合(★直改 bible/;圣经+字典+合成人设卡) → gf3【FH3 签字;世界观/人物预览页即时可看】
→ fs4 剧本移植→台词精修(★直改 story/;每集流水,试点集策略) → gf4【FH4 首集试读签字】
→ fs5 全剧终审(logic/world/char/fidelity/copyright) → gf5【FH5 定稿签字】
→ fs6 下游刷新(主流程 p3-scene/p4/p6 置 stale 重跑→场景/分镜/视频预览刷新)+ 小说化(均可选)
```

## 产物布局

```
data/projects/<slug>/
├── bible/                # ★ 融合圣经(矩阵 B|blend 维度覆写,标 A 维度不动)
│   └── characters/       # ★ 合成人设卡(甲魂乙壳)+ index.json 人名同步
├── story/episodes/{ep}/screenplay.md   # ★ 移植剧本(覆写;基线快照在 fusion/episodes/)
├── qa/reports/fusion/    # 终审报告(logic/world/character/fidelity/copyright)
└── fusion/               # 过程件命名空间
    ├── source_b/         # 乙本:novel/(fulltext 模式原文,classic 留空)、bible/、characters.json
    ├── fusion_plan.json  # 维度矩阵+锚点+种子对+红线
    ├── character_map.json
    ├── dictionary.json   # 概念转换字典+现代词黑名单
    ├── conflicts.json
    ├── episodes/{ep}/source_screenplay.md   # 甲本原剧本基线快照(覆写前留存)
    ├── episodes/{ep}/transpose_log.json     # baseline+逐场对账台账
    └── novelize_brief.json                  # 可选:fs6 小说化交接单
```

## 前置

1. 甲本项目已跑完主流程 p0–p3 与 p5:`bible/`(含 characters/relationship)冻结、
   `story/episode_plan.json` 与各集 screenplay 就绪;未跑到的先补主流程,不硬跑。
2. **建议自行留存项目备份**(如 git 分支):流程会直接覆写 `bible/` 与 `story/`,
   插件不提供回滚,版本管理由用户自理。
3. fs6 小说化需 `derivative-fiction` 插件已启用(仅该项,主链不依赖);正史即融合圣经,
   nv 流程照常读 `bible/` 与 `story/episodes/`,无需特殊输入源。
