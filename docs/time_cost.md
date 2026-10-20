# 时间尺:台词 + 动作统一估时(2026-10-03)

## 问题

fengshen3 ep07 grp011(28 s / 21 镜)出片后丢了一句 5 秒台词,sh080 之后分镜与画面对不上。逐帧对照白模参考视频:
前 6 秒对位,sh076 起每镜超时,延迟累积到约 2 秒;到 sh084 该开口时模型还在补前面的动作,这句台词没有连续时间可放。
不是提示词太长(24k 字里动作只占 18%,反例多),也不是时间标签写错——是上游三层同时超额分配:

| 层 | 现状 | 证据 |
|---|---|---|
| 台词估时 | 字数 ÷ 角色语速,无起止余量、无停顿、无情绪 | 对白语音库 44 句自然时长 / 估时中位 1.29;拟合 `0.68 + 字数/4.8 + 0.41×停顿`,MAE 0.41 s(旧 0.9 s) |
| 对白镜镜长 | = 估时(零余量);`dialogue_fit_shot` 只在 >100% FAIL | 44 对白镜 34 镜占比 95–100%,37 条「紧」WARN 全放行 |
| 动作节拍 | 从没人估过秒数;pacing 自上而下分时长 | S09 四回合 16 个动作事件 + 5 句台词分 28 s;blocking 1.2 s 镜排三拍(t=0.2/0.5/0.6);prompt 再展开成五个动作 |

动作估时和「节拍预算卡在 blocking」不是一回事:前者自下而上算出需要多少秒去争取时长,后者自上而下在镜长冻结后只能删拍。两者共用一张单价表。

## 口径(`modules/time_cost.py`,唯一来源)

**台词** `line_est(text, pace, cpm, wpm)` = `ONSET_S 0.7` + 有效字数 ÷ 语速 + `PAUSE_S 0.4` × 句中停顿数(≥ 0.6 s)
- 语速 = 角色 `voice.json#speed_cpm` 中点 ÷ 60 × 档位倍率 `fast 1.19 / medium 1 / slow 0.81`;无角色语速按档位 `5.0 / 4.2 / 3.4` 字/秒。
- **英文等按词计的句子**(2026-10-09,#124):句中没有汉字 / 假名 / 谚文、且有字母(拉丁、西里尔、带重音字母…)时,
  有效字数换成**词数**(按空白切,`can't` / `well-known` 各一词;字母数 ÷ 6 兜底,单个长词不至于估成一词),
  语速换成**词速**:角色 `voice.json#speed_wpm` 中点 > `speed_cpm` 换算(× 160/252,保持角色相对快慢;> 400 的 `speed_cpm`
  是按字母 / 分钟写的,÷ 3.8)> 默认 `DEFAULT_WPM 160`(2026-10-20 实测校准,原 130,见下「英文校准」),再乘档位倍率;
  `Dr.` / `Mr.` 等缩写的点与小数点不算句中停顿。
  旧口径把字母逐个当字、再除以中文字速,英文高估约 2 倍(alices 真实 seed-audio 样本 1.72 词/秒)。
  含汉字 / 假名 / 谚文的句子、只有数字标点的句子仍按字计,与 2026-10-03 口径逐字相同(fengshen3 190 句复算 0 变化)。
  拉丁文字项目的嗓音卡写 `speed_wpm`(词 / 分钟);精简目标对这类句子给 `target_words`(<3 词短句豁免)。
- 档位 `pace` 由 **dialogue-rewrite** 随 `emotion` 写进对白行元信息 `{emotion: 冷怒, pace: slow, est_duration_s: …}`,
  `shot_list.dialogue_lines[]` 照抄;没写按情绪词猜(急/喝/惊/怒 → fast,哭/恳求/低语/冷/沉吟 → slow),猜不出 medium。
- 对白镜余量:每句开口前 `PRE_SPEECH_S 0.4` + 说完后 `POST_SPEECH_S 0.3`。
- 旧公式(字数 ÷ 语速,一位小数)只给存量文件做 `line_est_consistent` 比对。

**动作** `beat_cost(action)`:关键词命中取最大;缺省 0.7 s

| 类型 | 秒 | 关键词(节选) |
|---|---|---|
| hold | 0.3 | 话落 / 镜首 / 停住 / 不动 / 站定 / 保持 |
| glance / face | 0.5 / 0.4 | 转头 / 看向 / 目光 / 低头 / 皱眉 / 嘴角 / 咬牙 |
| hand | 0.5 | 抬手 / 伸手 / 捏诀 / 握 / 拂 |
| turn / step / run | 0.7 / 0.8 / 1.0 | 转身 / 走 / 退 / 蹭退 / 跑 / 冲 / 追 |
| throw / catch | 0.7 / 0.5 | 掷 / 甩 / 挥 / 刺 / 递 / 抖 / 接住 / 抓 |
| fall / rise / sit | 1.2 / 1.5 / 1.0 | 撞 / 摔 / 滑倒 / 蜷 / 起身 / 蹲下 / 跪下 |
| draw / fx / drop / leap | 0.8 / 1.0 / 0.8 / 1.0 | 拔剑 / 收进袖 / 亮起 / 熄灭 / 腾起 / 碎 / 坠 / 纵身 / 遁 |

- 散文(剧本动作行、分镜 content)用 `action_cost(text, prose=True)`:按句读切拍,没有动作词的短句按描写 0.25 s,相邻同类动作并成一拍;
  `poses[].action` 这类动作短语 `prose=False`,每句一拍。
- blocking `beats[]`:`sync` 引台词原文的拍与说话同时发生,不另占时间;同人相邻拍 `t` 间隔 ≥ `MIN_BEAT_GAP_S 0.3`。

**组** `group_density_issue`:平均镜长 < `DENSE_ASL_S 1.5` 的密集组总时长 ≤ `DENSE_GROUP_MAX_S 15`(用户 2026-10-02 拍板;ep06/ep07 统计:≤12 s 组切点对位 90%,≥24 s 只 60%)。

**预算** `budget_plan(base, need, pct)`:`ok` / `extend`(推荐预算 = ⌈需求⌉,≤ base × (1 + pct%))/ `over_cap`(need_cut_s = 需求 − 上限)。
`pct` = 项目设置「视频节奏 → 可加长单集」`settings.json#duration.episode_extend_pct`(默认 50,0 = 不许加长)。

**存量** `is_legacy(date)`:剧本 front matter `generated_at` / shot_list·storyboard `_meta` 日期早于 `2026-10-03` 或缺失 → 只 WARN。

## 四个落点(`code/check_time_budget.py --project <slug> --ep epNN --scope …`)

| scope | 阶段 | 规则 | 机检 | 报告 |
|---|---|---|---|---|
| `script` | p5-pacing 分时长前 | 每场 `alloc_s` ≥ Σ台词估时(含余量)+ Σ动作节拍;Σ需求 vs 集预算按三步:**加长单集 > 精简台词 > 合并动作短镜** | scene_time_budget_ok / episode_time_budget_ok / pacing_budget_adopted / pacing_extension_within_cap / pacing_total_matches_budget | `story/episodes/epNN/time_budget.json`(`plan` / `trim_targets` / `merge_candidates`) |
| `shots` | p6-storyboard(`--source storyboard`)/ p6-shots | 镜长 ≥ 节拍单价 + 台词估时 + 余量;密集组 ≤ 15 s | shot_time_budget_ok / group_density_ok(后者也内置于 `check_generation_groups.py`) | `directing/epNN/time_budget.json` |
| `blocking` | p6-blocking | Σ `beats[]` 单价 ≤ 镜长 − 台词估时;同人相邻拍 ≥ 0.3 s | beat_budget_ok / beat_spacing_ok | 同上 |
| `prompt` | p7-prompt | Shot 段「不采用：」句之后的顺序连接词(先/随后/接着/然后/再…/then/next…)数 ≤ blocking 占时间节拍数 + 1 | prompt_beats_bound | 同上 |

pacing 加长单集时写:
```json
"duration_budget_s": 400,
"duration_policy": { "kind": "time_budget_extension", "base_budget_s": 300, "extend_pct": 33.3, "need_s": 398, "reason": "…" }
```
shot-planning 的 `budget_s` / edit 以调整后的 `duration_budget_s` 为基准;episode_plan 的集预算不改。

对白适配 `code/check_dialogue_fit.py` 同步改口径:估时走时间尺(`--legacy` / `--new` 可强制),`dialogue_fit_shot` 新集 = Σ估时 + 0.7 s 余量 ≤ 镜长。
台词演法 `modules/dialogue_direction.py` 的 `paced_target` 直接取时间尺,工位没给档位时沿用对白层 `pace`。

## 英文校准(2026-10-20,`code/calibrate_time_cost.py`)

起因:用户反馈中文对白估时较好、英文不太准。语料 63 句(alices ep01 34 句 + jidi ep01 7 句 + 自写 22 句,覆盖数字 / 时刻 / 首字母词 /
缩写 / 多音节长词 / 碎句 / 破折号 / 全大写喊叫;`docs/time_cost_calibration/en_corpus.json`),火山 seed-audio-1.0 用各人物冻结声线、
自然语速(不带演法 / 目标时长)真合成,按对白语音库同口径修剪首尾静音后量时长(`en_samples.json`;音频在 `data/calibration/`,不入库)。
拟合报告 `en_fit_report.json`:

| 口径 | 偏差(实测 − 估时) | 平均误差 | 比值中位 | 误差 > 20% |
|---|---|---|---|---|
| 无人物卡语速,默认 130 wpm(校准前) | −0.97 s | 1.42 s | 0.86 | 26 / 63 |
| 无人物卡语速,默认 160 wpm(现行) | +0.03 s | 1.09 s | 0.99 | 29 / 63 |
| 有人物卡 `speed_wpm`(alices / jidi 现状,未改) | −0.11 s | 1.01 s | 0.98 | 25 / 63 |
| 参照:中文 fengshen3 83 句 | −0.29 s | 0.51 s(均长 3.95 s,13%) | 0.94 | 20 / 83 |

- 只改了默认词速 130 → 160(最小二乘 157、中位无偏 163)。它管没写 `speed_wpm` 的说话人,以及只写了中文口径 `speed_cpm`
  的人物卡(按 160/252 换算,jidi 50 人里 45 人是这种,原换算出 88–134 wpm,现 108–165 wpm)。
- **方案里的「按音节计」与「停顿分句间 / 句内」被数据否掉,没有上线**:同一批数据自由拟合,按词 1.00 s(留一 1.07 s)、按音节 1.06 s
  (留一 1.13 s),词 + 音节一起拟合时音节系数只有词的 ¼——长的多音节词念得快(「Extraordinary, isn't it? Absolutely,
  unmistakably, irrevocably extraordinary.」27 音节只念了 4.95 s)。句间、句内停顿拟合出来分别约 0.8 s / 0.9 s,不比统一 0.4 s 结构更准。
  音节计数(《爱丽丝》原著按词频与 CMU 发音词典一致 99.7%)与停顿分类留在校准工具里做诊断,换渠道或拿到原生视频实测时可重比。
- 人物卡语速保持绝对语义(用户 2026-10-20 拍板不改成相对倍率)。实测 / 估时 逐人物差异明显(Alice 0.81、帽匠 1.39),
  seed-audio 的语速与停顿主要跟声线描述走,卡上的 wpm 并不参与合成。
- 噪声底:seed-audio 英文句中停顿 0.6–1.7 s 不等(慢声线在逗号处停一两秒),任何按文本的公式平均误差都在 1 s 上下。
- 原生视频只读参考(`calibrate_time_cost.py native`,自相关人声跨度、不转写):alices sh003 3.8 s(合成 3.88 s)、jidi sh001 3.3 s
  (合成 4.42 s)、sh005 3.0 s(合成 4.81 s)——Seedance 原生英文比合成快、停顿短,以合成校准的估时相对视频偏保守,与中文同一做法。
- 重跑:`corpus` → `synth`(付费,已合成的按 key 跳过)→ `fit --report`;参数改动由人确认后改 `DEFAULT_WPM`。

## 旁白(2026-10-20,`code/narration_est.py`)

旁白估时原由 narration 工位手算。16 个中文项目 484 段旁白实测(narrator manifest)对照:手写估时平均偏长 1.72 s、平均误差 2.13 s;
时间尺 `line_est`(medium)比值中位 1.00、平均误差 1.67 s——更准,但对下游太紧:挂点窗口 ≥ 估时 × 1.15、narrator 实测 ≤ 窗口 × 0.9,
实测超过估时 × 1.035 就可能超窗回派,无偏估时下 44% 的段会超(手写偏长,21%)。所以:

- 估时 = `line_est(text, "medium")` × 项目系数,一位小数;英文旁白同样按词。
- 系数 = 本项目**其它集** narrator 实测(未变速的段)实测 / line_est 的 80 分位,≥ 5 段才用,限 0.8–1.5;按集留出回测超阈 10%(377 段)。
  没有历史用 `NARRATION_DEFAULT_FACTOR 1.15`(全量 25%,除去慢声线 yddl2 后 1%;慢声线出完第一集后自动学到,yddl2 学到 1.3)。
- narration 工位不手算:写完 / 改稿 / 补写后跑 `--write` 只改条目头数字;只检模式 = 机检 `narration_est_consistent`(容差 max(0.25 s, 10%))。
- 旁白配音的语速区间(speech_rate_in_range)对英文按词 / 分钟量,不套中文字 / 秒。

## 出片后兜底(`code/check_dialogue_audible.py`)

不转写、不下载模型:自相关找人声段(80–400 Hz),间隔 ≤ 0.8 s 的人声段并成一段(`--merge-gap`,2026-10-20;原沿用开口检测的 0.5 s),
每个对白镜在计划时段 ±1.5 s 窗内取最长连续人声段 / 台词估时,< 0.3 FAIL(疑似整句缺失)、< 0.5 WARN。ep07 grp011 sh084 比值 0.25,
正是丢掉的那句。相邻镜台词会互相顶替通过,只抓「整句没了」。英文台词多是几个短句,句间停顿 0.5–0.8 s,0.5 s 合并时 jidi ep01
7 个对白镜误报 4 FAIL,0.8 s 下 0 FAIL / 3 WARN,sh084 仍是 0.25。

## 推演:ep07 S09 按新口径

需求约 55 s(台词 21 + 动作 34;分配 28)。全集 Σ需求 489 s,基准 300 s,上限 450 s → `over_cap`,差 39 s 由精简台词 / 合并短镜消化。
S09 拿到约 55 s 后按四回合拆成 4 组(≤ 15 s),sh084 那句台词的镜约 7 s。

## 待校准

- 动作单价表默认值是经验值,出几组片后按实际用时修。
- Seedance 原生开口语速未测(现用语音库数据代理;被挤时模型能说到 5–6 字/秒)。英文 3 句只读参考显示原生比合成快,见「英文校准」。
- 密集组阈值 1.5 s、镜级余量 0.4 / 0.3 s。

## 实现位置

`modules/time_cost.py`、`code/check_time_budget.py`、`code/check_dialogue_audible.py`、`code/check_dialogue_fit.py`(估时/余量/存量)、
`code/check_generation_groups.py`(group_density_ok)、`code/calibrate_time_cost.py`(英文校准)、`code/narration_est.py`(旁白估时)、`modules/dialogue_direction.py`、`modules/script_breakdown.py`(解析 `pace`)、
`services/runtime/core.py`(设置默认/校验/「用户时长设定」注入)、`apps/web/static/index.html` + `i18n/*.js`(可加长单集输入)、
SOUL:pacing / dialogue-rewrite / storyboard / shot-planning / blocking / prompt(+ fight-choreography)/ video-generation;`agents/workflow.yaml`、`agents/WORKFLOW.md` §7D ①″。
测试 `tests/test_time_cost.py`、`tests/test_time_cost_en.py`(旁白估时 / 校准工具 / 出片核查合并间隔 / 直播打包)。
