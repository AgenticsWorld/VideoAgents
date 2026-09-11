# 剧本拆解表(script breakdown)· 2026-09-11

把剧情处理层(`agents/01-story/*`)对一集的全部产出——剧本、对白定稿、旁白、钩子、节奏、分集计划、事件卡、全书结构——拆成**一份结构化 JSON**,供控制台「📜 剧本预览」页(`/preview/script`)用表格 + 符号展示,让用户不读 Markdown 也能看懂剧情层做了什么、并对任一板块/任一行提修改意见直发负责工位。这页**只涉及剧情处理层**,不读 `directing/` 与任何生成产物;分镜预览页自此不再放剧本正文。

实现:`modules/script_breakdown.py`(schema、推导器、加载/过期判定、校验);机检 `code/check_script_breakdown.py`;接口 `GET /api/v1/projects/<p>/previews/script?ep=`(`services/runtime/core._preview_script`);页面 `apps/web/static/preview_script.html`。

## 两条来源,同一 schema

| 来源 | 文件 | 产生方式 | 页面标记 |
|---|---|---|---|
| 正式产物 | `story/episodes/<ep>/script_breakdown.json` | `01-story/timeline-story` 在 DAG 节点 `p5-breakdown`(p5-pacing 之后、g5/H3 之前)按 SOUL「剧本拆解表」规约产出;用户在页面点「🔁 重新分析」也直接派单到该工位(消息含 `script_breakdown`,页面据此识别在跑的 run 并轮询) | ✅ 正式拆解表 · 更新时间;任一输入文件比它新 → ⚠ 已过期 |
| 推导视图 | 无文件,服务端即时计算 | `script_breakdown.derive()` 解析 `screenplay.md`(兼容 `## S01 \| 外 \| SCN-0075 名 \| 黄昏`、`## S01 \| INT \| scene:SCN-0001 \| 未知`、`### S01 ｜ EXT ｜ SCN-001 名 · 日 ｜ 42s ｜ …`、`## 1-2 日 内 神庙大厅`、`### [S001 \| …]` 等已见形态;对白行 `- **名(CHAR-x)**(括注):台词 {emotion, est_duration_s, style_hits}` / `CHAR-x:「台词」` / `[LN-…] 名(CHAR-x)〔OV〕:台词`;`[事件]/[出场]/[时长]`、`〔出场:…〕`、`- 在场:` 元信息;`动作:`/`△`/子块段落;`转场:`;`旁白候选`)+ `pacing.json` + `hooks.json` + `narration.md|json` + `episode_plan.json` + `events.json` + `story_graph.json` + Bible 索引 | ⚠ 推导视图 + 「重新分析」按钮 |

正式产物存在时以它为准,**缺的顶层块由推导视图补齐**(如 agent 只写了 scenes/cast,plan/events/structure 仍显示)。正式产物不是合法 JSON 或缺 `scenes[]` 时退回推导视图并在页面报错。

## schema `script_breakdown/1.0`

```jsonc
{
  "schema_version": "script_breakdown/1.0", "ep": "ep01", "source": "agent",
  "title": "谶语初现", "logline": "一句话看点", "pov": "第一人称旁白 = 王三合(CHAR-0001)",
  "narration_enabled": true, "duration_budget_s": 600, "total_est_s": 621,
  "totals": {"scenes": 13, "lines": 109, "dialogue_s": 336.4, "narration_items": 13, "narration_s": 88.0, "cast": 9, "hooks": 12},
  "cast": [{"id": "CHAR-0001", "name": "王三合", "role": "主角", "scenes": ["S01","S02"], "lines": 32, "dialogue_s": 85.2, "arc": "一句话弧线"}],
  "scenes": [{
    "order": 1, "no": "S01", "scene_id": "SCN-0075", "scene_name": "城郊马路·下班回家路上",
    "int_ext": "EXT",                       // INT | EXT | INT/EXT | null
    "time_of_day": "黄昏", "segment": null, "events": ["ev00101"], "cast": ["CHAR-0001","CHAR-0002"],
    "summary": "一句话内容", "action": "画面/动作(≤700 字)", "beat": "开场钩", "purpose": "戏剧功能一句话",
    "alloc_s": 33, "start_s": 0, "end_s": 33, "alloc_source": "pacing",
    "dialogue_s": 16.4, "narration_s": 9.5, "silent_s": 7.1, "lines": 8,
    "emotion": {"type": "倦怠→被勾起的好奇", "intensity": 0.35},   // intensity ∈ [0,1]
    "tempo": "medium", "tempo_label": "中", "color": "ep01-seg1(暖砂灰黄昏)",
    "transition": "CUT TO", "hooks": ["oh-1"], "narration_ids": ["N-01"],
    "blocks": [                            // 场内小块,按剧本原文顺序(页面左列逐块显示,每块一个反馈按钮)
      {"type": "action", "text": "夕阳压在楼群的边线上。…"},
      {"type": "narration", "id": "N-01", "text": "…", "est_s": 9.5, "tone": "平实", "anchor": "S01开场·…", "final": true},   // final=false 为剧本内候选、未进 narration.md
      {"type": "dialogue", "lines": [{"id": "S01-D01", "speaker": "CHAR-0002", "speaker_name": "老道儿", "text": "施主,请留步!",
                                      "paren": null, "emotion": "洪亮/召唤", "est_s": 1.6, "style_hits": ["c1:施主"]}]},
      {"type": "sound", "text": "♪ 远处车流"},
      {"type": "transition", "text": "CUT TO"}
    ],
    "dialogue": [{"id": "S01-D01", "speaker": "CHAR-0002", "speaker_name": "老道儿", "text": "施主,请留步!",
                  "paren": null, "emotion": "洪亮/召唤", "est_s": 1.6, "style_hits": ["c1:施主"]}],   // 全场台词平铺(与 blocks 内一致)
    "narration_candidates": [{"speaker": "王三合", "text": "…"}],   // 仅推导视图/无 narration.md 时有意义
    "sound": [], "notes": ["adaptation_note …"], "incompressible": [{"item": "结尾空镜", "s": 1.3}],
    "pacing_only": false                    // pacing 有而剧本无的单元(框架开场/片尾钩子)为 true
  }],
  "narration": [{"id": "N-01", "scene": "S01", "anchor": "S01开场·踢石子", "text": "…", "est_s": 9.5, "tone": "平实"}],
  "hooks": {"opening": [{"id": "oh-1", "kind": "opening", "label": "…", "type": "cold_open", "copy": "…", "visual": "…",
                         "scene": "S01", "position": "…", "est_s": 4, "status": "active", "intent": "…", "risk": "…", "recommended": false}],
            "mid": [], "ending": [], "selected": {"opening": "oh-1", "ending": "ec-1"}, "recommendation": {"opening": "oh-1"}, "retired": 1, "note": "…"},
  "emotion_curve": [{"scene": "S01", "t_s": 16, "intensity": 0.35, "label": "基线"}], "curve_shape": "低起→峰1→…",
  "trim_suggestions": [{"scene": "S08", "save_s": 4.8, "how": "…", "risk": "low", "status": "…", "stale": null}],
  "hook_reserve": {"opening": 3, "ending": 15},
  "plan": {"title": "…", "chapter_range": {"start": "ch001", "end": "ch011"}, "events": ["ev00101"], "duration_budget_s": 600,
           "summary": "…", "beats": ["…"], "characters": ["CHAR-0001"], "hook_point": {"opening": "ev00101", "cliffhanger": "ev01101"},
           "carry_over": ["fs-002"], "recap_needed": []},
  "events": [{"id": "ev00101", "chapter": "ch001", "time_hint": "…", "location": "…", "characters": ["王三合"],
              "importance": "major", "cause": "…", "process": "…", "result": "…"}],
  "structure": {"current_act": "act1", "acts": [{"id": "act1", "title": "机缘拜师", "type": "narrative", "chapters": ["ch001","ch011"], "desc": "…"}]},
  "issues": [{"level": "warn", "scene": "S03A", "text": "S03A 没有时长分配"}]   // error | warn | info
}
```

必填:`schema_version`、`ep`、`scenes[].no`(唯一);`scenes[].summary` 缺则 WARN。`emotion.intensity`、`emotion_curve[].intensity` ∈ [0,1];`alloc_s/dialogue_s/narration_s/silent_s` 非负;`tempo` ∈ {slow, medium, fast};`dialogue[]` 每句 `speaker` + `text`;`narration[].scene` 须在场次表内;`scene_id`/`cast` 对照 Bible 索引(WARN)。

## 页面:一张表,左列读剧本、右列看关联信息

页面只有一张两列表格,**核心是通过读剧本串联其它信息**:

| 行 | 左列(剧本) | 右列(这一块对应的信息) | ✏️ 反馈发给 |
|---|---|---|---|
| 本集 | 看点 logline、叙述人称、分集概要与节拍 | ⏱ 预算/预计时长 · 📖 章节范围 · 🧩 覆盖事件 · 🪝 已选钩子(小 ✏️ → hook)· 🗂 分集计划(小 ✏️ → episode-planner)· ⚠ issues | `01-story/screenplay` |
| 场次头 | `S01` 场景名 · SCN id · 🏠内/🌳外 · ☀️🌇🌙 时段 · 出场人物色点 chip | ⏱ 时长三色条(对白黄/旁白紫/无声灰)+ 起始时刻 · 🎭 情绪脸谱 😌🙂😮😨😱 + 强度条 + 类型 + 🐢▶️⚡ 节奏(小 ✏️ → pacing)· 🎯 节拍/戏剧功能 · 🧩 事件卡摘要(小 ✏️ → event)· 🪝 落在本场的钩子(小 ✏️ → hook)· ✂️ 删减建议(小 ✏️ → pacing)· 🔒 不可压缩项 · 📝 改编注记 | `01-story/screenplay` |
| 动作段 | 画面/动作原文(连续段落并成一块) | — | `01-story/screenplay` |
| 对白块 | 连续台词(说话人色点 + 括注 + 台词) | 逐句对齐:情绪 · 估时 · ✓风格命中;多句时 Σ 合计 | `01-story/dialogue-rewrite` |
| 旁白 | 🎙 旁白正文(定稿文本;剧本候选未进定稿时标注) | N-id · 估时 · 语气 · 📍挂点 | `01-story/narration` |
| 转场 | ⤵ CUT TO 等 | — | `01-story/screenplay` |

页头:集号按钮(✅ 已有正式拆解表 / ⚠ 仅剧本 / – 无剧本)、标题 + ⏱ 预计/预算量表、来源状态行(正式 / 已过期 / 推导 + 「🔁 重新分析」)。反馈按钮把该块的定位与文本摘录预填进共用编辑浮窗(edit-popup.js),直接发给对应工位。

入口:控制台顶栏 📜 图标(分镜预览图标之前);各预览页右上下拉「📜 剧本预览」(分镜预览之前)。

## 重新分析

按钮把固定工单(输入清单、只读拆解、逐场 summary/beat/purpose、cast role/arc、跑机检 PASS 后交付、不派其它工位)`POST /api/v1/runs` 给 `01-story/timeline-story`(拆解表产出工位,2026-09-11 用户指定;引擎/模型随顶栏全局设置),页面每 4s 轮询该 run,结束后重新拉数据;刷新页面时服务端从在跑 run 里识别同集拆解单继续显示「分析中」。老项目一集一按;DAG 新项目由 `p5-breakdown` 自动产出。
