# 剧本机器锚点契约(screenplay anchors,2026-09-23)

**问题**:剧本拆解(`modules/script_breakdown.py`)、对白适配(`code/check_dialogue_fit.py`)、内外景登记(`modules/scene_int_ext.py`)、事件取舍机检(`code/check_screenplay_events.py`)、故事板旁白挂点(`modules/storyboard_board.py`)都靠剧本/对白/旁白文件里的标签解析,而这些标签此前只有中文写法。输出语言(`output.language`)不是中文时,写作工位按输出语言写整份文件,标签一并被翻译,英文剧本解析成空场、拆解表空白、机检误判。

**契约**:下面这套**机器锚点**固定不随输出语言变。**中文旧写法与英文规范写法等价**,解析器两套都认;正文散文(动作描写、台词、旁白文本、场景名、时段词)按输出语言写。不得自造第三种写法(其它语言也用英文锚点)。

## screenplay.md

| 要素 | 中文写法(存量) | 英文规范写法(非中文输出语言) | 解析要点 |
|---|---|---|---|
| 场头 | `## S03 \| INT \| SCN-0012 藏经阁 \| 夜` | `## S03 \| INT \| SCN-0012 Sutra hall \| night` | 场次号 `S03`(`(续)`/`(cont'd)`);内外景 `INT/EXT/INT/EXT`(也认 `内/外/INT./EXT./INTERIOR/EXTERIOR`);`SCN-xxxx` 后接场景名;**末段按位置认作时段**,任何语言都行 |
| 元信息行 | `**[事件] ev0021 \| [出场] CHAR-0001 \| [时长] 40s**` | `**[EVENTS] ev0021 \| [CAST] CHAR-0001 \| [DURATION] 40s**` | 方括号标签是锚点;事件/人物只认 `ev…`/`CHAR-…` ID。**衍生模式**(episode_plan 顶层 `derivative_mode: true`,2026-10-09)事件位写占位 `none`(中文 `无`):`**[EVENTS] none \| [CAST] CHAR-0001 \| [DURATION] 40s**`,`check_screenplay_events.py` 认作合法;非衍生模式不得写 |
| 动作 | `动作:…` / `△…` | `ACTION: …` / `△ …` | 无前缀的普通段落也算动作 |
| 台词 | `CHAR-0001:「…」` / `- **名(CHAR-0001)**(括注):… {emotion, est_duration_s}` | `CHAR-0001: "…"` / `- **Name (CHAR-0001)**(paren): "…" {emotion: …, est_duration_s: …}` | 说话人必须带 `CHAR-` ID;只有名字时台词须加引号(`「」“”""`) |
| 旁白候选 | `〔旁白候选(旁白)〕:…` | `[NARRATION (narrator)]: …` / `[V.O.]: …`(无 CHAR 说话人) | 旁白者文本归旁白候选;**带 CHAR 说话人的 `(O.S.)` / `(V.O.)` 括注(2026-10-03 声画分离,docs/sound_split.md)仍是对白行,拆解表 / 对白适配保留并带 `placement` os / vo** |
| 转场 | `转场:CUT TO` | `TRANSITION: CUT TO` / 独立一行 `CUT TO:` `FADE OUT.` `DISSOLVE TO:` | |
| 时段/声音 | `时段:…` `声音:…` `音效:…` `音乐:…` | `TIME: …` `SOUND: …` `SFX: …` `MUSIC: …` | |
| 本场无对白 | `〔本场无对白〕` | `[NO DIALOGUE]` | |
| 场内子标题 | `### 旁白` / `### 对白` / `### 声音` | `### Narration` / `### Dialogue` / `### Sound` | 决定其后段落归类 |
| 集级元信息 | `时长预算:180s` `本集看点:…` `叙述人称:…` | `Duration budget: 180s` `Logline: …` `POV: …` | 出现在第一个场头之前 |
| 改编注 | `(adaptation_note: …)` | 同左 | 键名固定英文 |
| 场内节拍 | `动作:【反制】…` | `ACTION: [BEAT: Counter] …` | 标在该节拍第一行动作行首;解析层当动作文本保留,pacing `beats[].beat` 按同名对齐(2026-09-29) |
| 同时空分场理由 | `(split_note: …)` | 同左 | 键名固定英文;写在后一场场头下,豁免 `scene_spacetime_continuous` |

## narration.md

| 要素 | 中文写法 | 英文规范写法 |
|---|---|---|
| 条目头 | `[N-03 \| anchor: S03 场首 \| est_duration_s: 4.5 \| source: …]` | `[N-03 \| anchor: S03 scene start \| est_duration_s: 4.5 \| source: …]`(键名固定英文) |
| 锚点位置词 | `场首/开场` `场末/结尾` | `scene start` / `end of scene`(故事板挂点 `modules/storyboard_board.py` 两套都认) |
| 本集无旁白 | 稿首 `本集无旁白` | 稿首 `[NO NARRATION]` |

## dialogue.md(对白层)

| 要素 | 中文写法 | 英文规范写法 |
|---|---|---|
| 条目块 | `### [LN-ep01-01] 名(CHAR-0001)` + `- **定稿**:台词` | `### [LN-ep01-01] Name (CHAR-0001)` + `- **final**: line` |
| 表格列 | 列名含 `台词/对白/定稿`、`说话人/角色`;排除 `原句/字数/编号/id` | 列名含 `line/text`、`speaker/char`;排除 `original/source/chars/count/length/index/no.` |
| 旁白段标题 | `### 旁白…` | `### Narration…`(其下条目不进对白适配) |

## 实现位置

- `modules/script_breakdown.py`:`_SCENE_TOKEN`/`_INT_EXT`/`_TOD_WORDS`/`_NOT_SPEAKER`(+`_not_speaker`)/`_parse_heading`(末段按位置认时段)/`parse_screenplay` 各锚点正则/`derive` 的无旁白声明。
- `modules/scene_int_ext.py`:`_ALIASES` 英文别名;存量推断词英文整词命中。
- `code/check_dialogue_fit.py`、`services/runtime/core.py` 对白解析:`**final**`、英文列名与排除列、`Narration` 段。
- `modules/storyboard_board.py`:旁白锚点位置词。
- 测试:`tests/test_script_breakdown.py::test_parse_english_anchor_screenplay`。

存量中文项目不受影响;换输出语言后写作工位按本契约写,拆解/机检不用改。
