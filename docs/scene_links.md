# 场间衔接(scene links)· 剧本阶段起稿匹配剪辑与接力,一路登记到提示词(2026-10-10 一期 + 二期)

适用:所有项目,无开关。编剧没写衔接的剧本一切照旧;`generated_at` 早于 2026-10-10 的存量剧本只 WARN、不回改。

## 为什么

匹配剪辑要求「前一场最后一个画面」和「后一场第一个画面」成对设计,这两个画面是什么由剧本决定——到分镜阶段场尾场头的内容已经定了,只能硬凑。此前的状况(2026-10-10 对存量项目的统计,去重后 97 份剧本 / 1149 场):

- **剧本在写,但是自由文本**:748 场有「转场:」行,571 场是裸 `CUT TO`;其余约 177 场带设计内容,编剧工位已在自发写匹配(「CUT TO(水面的震颤接水下殿柱的震颤)」)。
- **导演没有处置义务**:导演阐述「转场清单」被定为下游唯一创意来源,剧本转场句采不采纳无人对账。能对上分镜边界的 8 处剧本匹配剪辑,3 处保留为 `match_cut`、3 处成了硬切、2 处成了白场。
- **过场设计读不到**:`transition_design._screenplay()` 只认行首是「转场」的行,`- 转场:` / `**转场**:` / 英文 `TRANSITION:` 都读不到,场头也只认一种排版(全部项目副本合计:能读到转场句的场次 241 → 修复后 2536)。
- **`match_cut` 只是标注**:「拿什么对什么」只在 reason 散文里,白模只核「两镜同机位」一种,两侧组提示词里没有匹配句。

## 分工

| 谁 | 定什么 | 不定什么 |
|---|---|---|
| 编剧(`01-story/screenplay`) | **接什么**:两头的内容对(本场最后一个画面 / 声音、下一场第一个画面 / 声音)与衔接类型 | 时长、景别、焦段、叠化 / 黑场、字卡、声桥 |
| 导演(`07-directing/director`) | **拍不拍、怎么拍得对上**:逐条采纳 / 修改 / 弃用,落到哪种转场类型,两侧镜头的景别 / 屏位 / 方向 | 不新增剧本没写的两头内容 |
| 宿主(过场设计 `propose` / 提示词同步) | **登记与传达**:把采纳的衔接登记进 shot_list、写进两侧组提示词;按过场模式带上成对运镜 / 声桥 | 不改两头内容、不替导演做采纳 |
| 过场设计工位 / 剪辑 | **怎么接**:时长、字卡、定场、声桥 | 不改导演采纳的衔接(有异议走 feedback) |

## 契约:场尾转场行的花括号

```markdown
转场:MATCH CUT TO S04 {衔接: 形状, 出: 符纹亮成一个圆, 入: 宝德门上同一道发亮的符}
TRANSITION: MATCH CUT TO S04 {link: shape, out: the sigil flares into a ring, in: the same glowing sigil on the gate}
```

- 三个键是机器锚点:中文 `衔接 / 出 / 入` 与英文 `link / out / in` 等价,键之间用逗号或分号隔开,值里可以有逗号。
- `出` = 本场最后一个画面或声音,`入` = 下一场第一个画面或声音;两头都要在正文里**真的写了**(本场最后一条动作行 / 末句台词 / 声音行,下一场第一条动作行 / 首句台词 / 声音行)。
- 只写在本场**最后一行**转场行;只接**相邻的下一场**(转场词后点名场次号时必须就是下一场);本集最后一场不写。
- 默认不写(`CUT TO`)。写的前提见 screenplay SOUL「场间衔接初稿」三条。

### 类型白名单(`modules/scene_links.KINDS`)

| 键 | 中文 | 档位 | 两头要写成什么 | 建议转场词 | 导演采纳默认落到 |
|---|---|---|---|---|---|
| `shape` | 形状匹配 | 试验 | 两个同形、同屏位的物件 | `MATCH CUT TO` | `match_cut` |
| `action` | 动作匹配 | 试验 | 同一个动作前场起、后场完成 | `MATCH CUT TO` | `match_cut` |
| `position` | 同机位 | 放开 | 同一空间同一构图,只变一样东西(时间流逝) | `MATCH CUT TO` | `match_cut`(+ `whitebox_contract.match`) |
| `motion` | 运动接力 | 放开 | 出画方向与入画方向一致 | `CUT TO` | 显式 `hard_cut` |
| `sound` | 声音接力 | 放开 | 前场的声音化成后场的声音 | `CUT TO` | 显式 `hard_cut` |
| `line` | 台词接力 | 放开 | 前场末句的问或提到的人,后场首画面即答 | `CUT TO` | 显式 `hard_cut` |
| `contrast` | 反差硬切 | 放开 | 静接闹、明接暗 | `SMASH CUT TO` | `smash_cut` |

**试验型**(形状 / 动作):要两个画面的位置大小逐帧重合,视频模型只靠文字命中率不高;下游当标注处理(`match_cut` = 硬切;二期起两侧提示词会写同一个固定摆位,但仍不保证重合),没对上不记缺陷,一集超过 2 处记 WARN。**放开**的五种靠现有手段就能兑现:白模同机位核查、镜头运动方向、声音 cue、后场首镜内容。

## 机检

| 机检 | 在哪 | FAIL | WARN |
|---|---|---|---|
| `scene_link_valid` | `code/check_screenplay_events.py`(`modules/scene_links.verify`),p5-screenplay 验收 | 类型不在白名单;缺 `出` 或 `入`;写在本集最后一场;点名的下一场不是相邻场;花括号不在场尾转场行;台词接力但本场没有台词;转场词点名 `MATCH CUT` / `SMASH CUT` 却没写花括号 | `出` / `入` 的内容在场尾 / 下一场场头正文里找不到(按内容指纹粗判,防只贴标签);接力配了黑场 / 淡出;试验型 > 2 处;衔接占过半场界 |
| `script_links_disposed` | `code/check_scene_links.py`(`modules/scene_links.verify_dispositions`),p6-plan 验收 | 某条衔接没有处置行;处置行没写 采纳 / 修改 / 弃用;采纳 / 修改没写落到的转场类型,或落到黑场 / 白场 / 淡出类(只能 hard_cut / match_cut / smash_cut / dissolve);弃用 / 修改没写理由 | 处置了剧本里没有的衔接 |
| `script_links_landed`(二期) | `code/check_scene_links.py`(`verify_landed`,有 shot_list 时才核),p6-shots 验收;`sync_scene_links.py` 在 p7-prompt 再核一次 | 分镜表里找不到这条场界;后一场第一组没有显式 `transition_in`;类型与导演写的不一致;登记卡与剧本现值不一致;弃用 / 已删的衔接还留着登记卡 | 登记卡还没写(p7-prompt 时升为违规);用户在过场卡改过的边界不报 |
| `transition_link_valid`(二期) | `code/check_generation_groups.py`,并入 `transition_ok` | 登记卡 kind 不在白名单、out / in 为空、type 不是 hard_cut / match_cut / smash_cut / dissolve、与 inserts / hold_s 并用、首组、缺 reason、`out_shot` / `in_shot` 不是前组末镜 / 本组首镜(拆并组后过期) | — |
| `scene_link_bound`(二期) | `code/sync_scene_links.py`(`modules/scene_link_prompts.sync_episode`),p7-prompt 验收 | 带登记卡的边界某一侧组 prompt 缺衔接句 / 句子不在正确的 Shot 段 / 有内容已改的残留句;登记卡已撤却还留着句子 | 组 prompt 尚未写 = skipped |

`scene_link_valid` 对存量剧本(front matter `generated_at` 早于 `LINK_RULE_SINCE` = 2026-10-10 或缺失)全部降为 WARN。`script_links_disposed` 在剧本没有合法衔接时不适用,直接 PASS。

### 导演处置行(directing_plan.md「## 转场清单」)

```markdown
- 剧本衔接 S03→S04(形状匹配):采纳 —— match_cut(试验);S03 末镜符纹特写居中、占画面约三分之一,S04 首镜门上的符同位同大。
- 剧本衔接 S05→S06(台词接力):修改 —— hard_cut;接点从「谁在门外」改到上一句「你听」,S06 首镜直接给门外的人。
- 剧本衔接 S08→S09(反差硬切):弃用 —— S09 开场要先用字卡交代「三日后」,反差被字卡隔断。
```

行锚点 `剧本衔接 Sxx→Syy`(英文 `Script link Sxx→Syy`,箭头认 `→ -> — 至 to`)+ 处置词 `采纳 | 修改 | 弃用`(`ADOPT | MODIFY | DROP`)。采纳 / 修改的行就是该边界的转场清单条目:storyboard 落到后一场第一组的 `transition_in`(运动 / 声音 / 台词接力落显式 `hard_cut` + `reason`),并让前场末镜 / 后场首镜的画面内容落在该行写的落点 / 起点上。写进 shot_list 的边界,过场设计 `propose` 记 `accepted, source=shot_list`,模式建议(字卡 / 定场空镜)只进候选。

## 流水线位置

```
Phase 5  p5-screenplay   场尾转场行起稿 {衔接, 出, 入}        → scene_link_valid
         p5-dialogue     花括号不动;被引的台词保留接点词
         p5-breakdown    不必写——拆解表 scenes[].link_out 由宿主按剧本现算
         g5 H3           剧本预览页转场小块右列显示衔接(类型 / 本场结尾 / 下一场开头,试验型打标)
Phase 6  p6-plan         check_scene_links.py --list → 转场清单逐条处置  → script_links_disposed
         p6-storyboard   采纳 / 修改的行落 transition_in(type / intent / reason / source)+ 两侧镜头内容
         p6-shots        继承并按新分组重挂                                → script_links_landed
         p6-transition-design(模式 ≠ 极简)propose:登记 transition_in.link 并写回 shot_list   → transition_link_valid
         g6 H3A          签字钩子兜底登记(极简模式没有过场设计节点);过场卡可改选候选
Phase 7  p7-prompt       sync_scene_links.py --write:两侧组提示词各一句【场间衔接】          → scene_link_bound
```

## 二期:登记卡与提示词(2026-10-10)

一期只做到「编剧写了、导演表了态、页面看得见」。二期把它传到出视频的那一步,全部由宿主做,不靠工位记得。

### 登记卡 `transition_in.link`

写在**后一场第一组**的 `transition_in` 里(与 `motion_pair` / `sound_bridge` 并列):

```json
"transition_in": {
  "type": "match_cut", "intent": "scene_change", "reason": "转场清单 #1:符纹接门上的符", "source": "directing_plan#转场清单/1",
  "link": {"kind": "shape", "out": "符纹亮成一个圆", "in": "宝德门上同一道发亮的符",
           "out_shot": "sh012", "in_shot": "sh013", "from_scene": "S03", "to_scene": "S04", "source": "screenplay",
           "note": "S03 末镜符纹特写居中、约占画面三分之一,S04 首镜门上的符同位同大", "trial": true}
}
```

- `kind / out / in` 取自剧本花括号;`out_shot` = 前组最后一镜、`in_shot` = 本组第一镜;`note` = 导演处置行里写的对位要求(去掉行首类型词);`trial` 只在形状 / 动作匹配时有;`device` 记宿主随衔接带上的手段键名(`motion_pair` / `sound_bridge`),重出时据此撤换。
- 方案阶段这个字段叫 `match{}`;落地时改名 `link`,因为它装的是全部七种衔接,不只是匹配。个别项目自造的 `match_pair` 不迁移、不读。
- **谁写**:只有 `modules/transition_design.propose`(`_link_design`)——边界诊断带 `screenplay_link` 且导演处置为采纳 / 修改时,把登记卡加到该边界的设计上,条目记 `status=accepted, source=script_link`,并当场 `apply` 写回 shot_list(导演定的,不等用户裁决)。分镜工位已落的 `type / intent / reason / source` 原样保留;没落的由宿主按导演写的类型补建(`source: script_link`)。模式建议(字卡 / 定场空镜)只进候选。
- **撤销**:剧本删了衔接或导演改成弃用 → 下次 `propose` 撤掉登记卡和随它带的手段(宿主补建的整条撤掉,分镜工位落的底子留下)。
- **用户优先**:用户在过场卡上选了候选 / 保持硬切(设计表 `source=user`)的边界不再登记,对账也不报。
- **极简模式**:不派 `p6-transition-design`,由 H3A 签字钩子调 `transition_design.land_script_links` 兜底(有待登记 / 待撤销的边界才跑 `propose`;没有衔接的集不动、不新建设计表)。

### 随衔接带的手段(守过场模式口径)

| 衔接 | 手段 | 电影感 | 经典 / 自定义 | 极简 |
|---|---|---|---|---|
| 运动接力 | `motion_pair`(方向按「出」里的「向右 / 向左 / 向上 / 向下」推,读不出默认向右) | 自动带;**白模开启时不自动带**(摄影机由白模视频定),只放候选 | 候选「衔接 + 成对运镜」 | 不出 |
| 声音接力 / 台词接力 | `sound_bridge {kind: j, s: 项目默认, carry}`(承载口径同过场设计:默认 line、声画分离开着且切点旁有画外句才 line,否则 bed) | 自动带 | 候选「衔接 + 声先入」 | 不出 |
| 形状 / 动作 / 同机位 / 反差 | 无 | — | — | — |

手段只在底子是 `hard_cut` / `dissolve` 且分镜工位没自己写同名字段时才加。

### 提示词两侧的衔接句

`python3 code/sync_scene_links.py --project <slug> --ep epNN [grp…] --write`(prompt 工位写完必跑,做法同 `sync_motion_pairs.py`):

- 前组**最后一个** Shot 段:`【场间衔接】本镜最后一个画面落在「符纹亮成一个圆」上:收尾前让它位于画面正中、占画面高度约三分之一并停住约半秒,镜头不再运动、不切走。`
- 本组**第一个** Shot 段:`【场间衔接】本镜第一帧就是「宝德门上同一道发亮的符」:位于画面正中、占画面高度约三分之一,开头停住约半秒;不先交代环境、不叠化、不从黑起。`
- 七种衔接各一对固定句式(`modules/scene_link_prompts._ZH / _EN`),非中文界面写 `Scene link:` 英文句。两条原则:**每句只写本镜自己的内容**(两段视频各自生成、互相看不见,写了对面的东西模型会把它画进本镜);形状匹配两侧写**同一个固定摆位**,文字能约定的对位只有这么多。
- 只用登记卡的 `out` / `in`(各截 80 字);`note` 带场次号和行内说明,给写提示词的工位看,不进提示词。
- 幂等:先剔旧句再写;登记卡撤了只剔除;切换界面语言重跑即整句替换。总是插在同段【运镜对接】【原生先入】句之前(那两种的剔除口径是「从标记到行尾」),三个 sync 先后顺序随意。

## 页面

剧本预览页(`/preview/script`)不加板块:场内最后一条转场小块,左列只显示转场词(花括号收起),右列显示「🔗 类型」「本场结尾 …」「下一场开头 …」,形状 / 动作匹配带「试验」标。数据来自 `script_breakdown.load()` 挂的 `scenes[].link_out`(正式拆解表与推导视图都由宿主按 `screenplay.md` 现算,拆解工位不用写)。该小块的「✏️ 反馈」仍发 screenplay。

## 实现位置

- `modules/scene_links.py`:**剧本转场行的唯一解析口**。`transition_line` / `parse_transition`(转场行与花括号)、`scene_rows` / `links` / `links_by_scene` / `annotate`、`verify`(scene_link_valid)、`parse_dispositions` / `verify_dispositions`(script_links_disposed)、`KINDS` 白名单;二期 `link_record` / `link_of` / `boundary_of` / `verify_landed`(script_links_landed)。
- `modules/scene_link_prompts.py` + `code/sync_scene_links.py`(二期):衔接句的句式、写入、机检 scene_link_bound。
- `code/check_generation_groups.py`(二期):`_check_link` = transition_link_valid。
- `services/runtime/core.py`(二期):`_transition_design_sign_accept` 先调 `land_script_links`。
- `apps/web/static/preview_storyboard.html`(二期):过场卡接缝标签「🔗 类型」、诊断行「🔗 剧本衔接 · 类型 · 导演采纳 / 修改 / 弃用 / 未处置 · 出 → 入」、`source=script_link` 的「📜 剧本衔接」标。
- `modules/script_breakdown.py`:`parse_screenplay` 的转场行识别改走 `scene_links.transition_line`(存量 2592 行转场行对拍零差异);`load()` 挂 `link_out`。
- `modules/episode_treatments.py`:`verify_screenplay` 的 `checks.scene_link_valid` 与回执 `links[]`。
- `modules/transition_design.py`:`_screenplay()` 改用 `scene_links.scene_rows`(场头对得上旧正则的字段仍取原文,存量口径不变),`diagnose` 出 `diagnosis.screenplay_link` 与 `script_link_disposition`;二期 `_link_design` / `propose` 衔接分支 / `land_script_links` / `_rewrite_transitions`。
- `code/check_scene_links.py`:`--list` 衔接清单 / 缺省核导演处置。
- `apps/web/static/preview_script.html`:`linkHtml`;11 份界面词典。
- 规约:screenplay / dialogue-rewrite / director / storyboard / transition-design SOUL,WORKFLOW.md Phase 5 · Phase 6 表与 §9C,workflow.yaml `p5-screenplay` / `p6-plan` 的 `validation.auto`,`docs/screenplay_anchors.md`。
- 测试:`tests/test_scene_links.py`(一期)、`tests/test_scene_links_stage2.py`(二期)。

## 未做 / 后续

- **三期(看效果再定)**:白模按屏幕位置核对两镜主体;后组首帧拿前组尾帧作构图参考;剪辑时在切点前后几帧里找最对得上的一对。形状 / 动作匹配的精度要到这一步才稳。
- 成对运镜仍只写 prompt、不回写 `camera.json`(过场设计既有缺口);所以白模开启时不自动带。
- 声桥底床(去人声模型)在真项目成片里还没验证过;电影感模式下声音 / 台词接力会自动带上它。
- 过场卡候选下拉里的候选名(「衔接 + 声先入」等)是服务端中文,与既有候选名一样不随界面语言翻译。
- 一期、二期都没有真项目跑过:编剧 / 导演 / 提示词工位按新规约的真派单未跑,没有真出片对比过衔接句的效果。
