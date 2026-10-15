# NPC 参与构图 · 2026-10-09

在画面里加入路人、前景物体等**无名** NPC 元素，补空间、丰富层次，让场景有前中后三层，空间立起来。开关挂在**场次**上：剧本拆解时自动判定，故事板第一个按它出画面，构图 / 白模 / 提示词只执行、不判断。

实现：`modules/npc_staging.py`（生效值、建议、机检、提示词固定段）；宿主 CLI `code/npc_staging.py`；接口 `POST /api/v1/projects/<p>/episodes/<ep>/npc`；页面组件 `apps/web/static/npc-switch.js`（剧本预览页、故事板页共用）。

## 开关

| 层级 | 位置 | 取值 |
|---|---|---|
| 项目总开关 | `settings.json#output.npc_staging`（输出设置 / 新建向导「NPC 参与构图」） | `auto` 按场次判定（默认）/ `off` 全部关闭 |
| 场次自动判定 | `story/episodes/<ep>/script_breakdown.json` 的 `scenes[].npc`，由 `01-story/timeline-story` 在 `p5-breakdown` 写；「重新分析」会刷新 | `{on: bool, density: sparse\|medium\|dense\|null, reason}` |
| 场次用户覆盖 | `story/episodes/<ep>/scene_npc.json`（宿主自有，剧本预览页 / 故事板页场次头 🚶 开关写；重新分析不覆盖，Agent 不改） | `{scenes: {S01: {mode: auto\|on\|off, density, updated_at}}}` |

**生效值**（`resolve()`，`python3 code/npc_staging.py --project <slug> --ep epNN` 打印）：总开关 off → 关；用户选了开 / 关 → 用户值；否则自动判定；都没有（存量项目、推导视图）→ 关（未判定）。密度：用户改过的 > 自动判定 > 关键词建议 > medium。

密度档：**稀疏 sparse**（一两个、离主体远）/ **适中 medium**（三五个、不成群）/ **热闹 dense**（人流填满纵深，主体前方与视线方向留空）。

## 自动判定口径（timeline-story）

- 开：公共或热闹的场合（街市、集市、广场、码头、车站、机场、酒楼茶馆、宴席、朝会大殿、军营校场、教室…），且时段与情境下这里本该有人；热闹 = 剧本写了人群 / 围观 / 熙攘 / 人声鼎沸或庙会夜市一类场合，适中 = 一般公共空间，稀疏 = 半公共空间（庭院、走廊、书院、店铺、村口小巷）。
- 关：剧本写明空无一人 / 清场 / 屏退左右；密谈、潜入、对峙等需要「只有他们」的场；私密空间（卧房、密室、车厢、轿内）、梦境与内心空间；深夜（夜市、灯会、宴席除外）；已有大批剧本点名群演的场（那些人走 `extras`）。
- `reason` 引剧本原文；人物「独自」不等于场所无人。
- `--suggest` 的关键词建议（`suggest()`）只作参考，也用于界面提示。

机检 **npc_judged**（并入 `code/check_script_breakdown.py`）：总开关不是 off 时每场须有判定，格式错或缺判定 = FAIL；2026-10-09 之前产出的存量拆解表缺判定只 WARN。

## 下游契约（只在生效为开的场次）

| 环节 | 写什么 | 机检 |
|---|---|---|
| 故事板 `storyboard.json` | 镜草案 `npc: [{layer: fg\|mg\|bg, what}]`；场块回执 `npc_applied: {on, density}`。按景别放：远/全景放中景、背景层人流；中景放背景路人 + 可选虚化前景物体；近景/特写只放虚化前景或背景虚化人影；插入镜不放 | **npc_staging_applied**（`--check storyboard`）：开 → 回执与生效值一致且至少一镜有 `npc[]`（比例低于 稀疏 25% / 适中 40% / 热闹 60% 只 WARN）；关 → 无 `npc[]` |
| 镜头表 `shot_list.json` | 定稿镜照抄 `npc[]`（缺省时宿主按 `storyboard_ref` 回查草案）；不进 `characters`、不计人数 | **npc_shots_consistent**（`--check shots`） |
| 构图 `composition.json` | `layers` 对应层写明 NPC（无名描述，不占主体位置、不挡视线、比主体虚）；白模项目里 layers 会原样进视频提示词「构图层次」句 | **npc_layers_bound**（`--check composition`） |
| 白模 | 计划 `extras` 用 `EXTRA-NPC-01…`，同组共用一个中性色；图例写 `anonymous NPC passer-by, not a named character`（`modules/whitebox_refs.legend_rows`）；前景物体用 `props` 几何体 | — |
| 视频提示词 | 宿主固定段 `Background figures (NPC): Shot k — background: …; foreground: …. … End background figures.`，写在 `Global constraints:` 前；有 NPC 的组把身份锁收窄到具名角色（`every named character … / no duplicate person / exactly N named character(s)`）。prompt 写完跑 `python3 code/npc_staging.py --project <slug> --ep epNN --write [grp…]` | **npc_prompt_bound**（`--check prompt`）：段齐全且与镜头表一致、身份锁已收窄；无 NPC 的组无残留 |
| 环境声 | 开启场次加与密度相符的人声底（walla，听不清字句） | — |
| 故事板草图 | `modules/storyboard_board._shot_parts` 自动把 `npc[]` 写进草图提示词（无名松散人形） | — |

背景图（九宫格 / 母图 / 场景图）**不变**，继续不出人；前景物体也不放进背景图（背景图是多镜共用的）。

剧本里点名的群演（巡逻兵、乘客、有戏的围观者）照旧写 `extras`，不受开关影响，也不与 `npc[]` 重复。

## 改开关之后

改动只写 `scene_npc.json`，**不自动重做任何下游**：故事板页对 `npc_applied` 与生效值不符的场次标「⚠ 分镜未按此设定」，由用户用该场「✏️ 修改」让分镜师只补写 / 删除本场 `npc[]` 与回执（不重拆镜）；之后镜头表、构图、白模、提示词按各自机检提示补齐（白模签字后再改要重新签字）。

## 风险与约束

- **串脸**：视频模型可能把参考图主角的脸套到路人身上——路人要小、虚、背身，固定段写明不像任何参考图、身份锁只约束具名角色。
- **抢戏**：靠构图约束与按景别放置。
- **组内路人跳变**：白模开启时由 EXTRA-NPC 关键帧保证；不开白模只能靠提示词写清密度与方向。
- **时长**：NPC 动作不计入时间尺，不占主角动作节拍。
