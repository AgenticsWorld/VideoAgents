# 声画分离:人物画外对白 O.S. / V.O.(2026-10-03,一期)

**问题**:全链默认「声源 = 画内开口的人」——一句台词必然是说话人在本镜画内开口(组 prompt `{台词}`),`dialogue_lines` 没有声源位置
字段,`is_dialogue` 二值,`audio_plan` 三值全以人物开口为前提;剧本解析器虽认 `V.O./O.S.`(`modules/script_breakdown.py`),却把这类句
全部归到旁白候选,`check_dialogue_fit.py` 直接把 V.O. 说话人剔出对白层。结果:分镜里出不来画外对白(电话、隔门、反打只拍听者),人物
V.O.(内心独白、读信、回忆声)没有通道——旁白链只限旁白者声线。

**原则**:
1. 一个字段解耦声与画:`dialogue_lines[].placement ∈ on | os | vo`(缺省 on,存量零影响)+ `heard_in`(在哪几镜听见)。
2. **os/vo 句一律后期合成,不进 `{}`**:§8A「TTS 严禁配对白」红线的根因是画内口型,画外无嘴可对,视频原声模式同样成立;模型原生出
   画外音会把说话人画进来(白模 hidden 前科)且声线无保障;后期合成复用 dub_group / 对白语音库机制,时长可实测、可机检。
3. 组内(同场景)的 J 型声画分离交给多镜头模型原生做(**三期,只开放 J**,见下);组边界(换场)声桥属**二期**。
4. 与旁白开关**正交**:旁白开关只管旁白者声线;人物 V.O. 归本项。

## 设置

`settings.json#output.sound_split`(「📤 输出设置 → 声画分离」,新建向导同列;系统提示词「用户输出设定」段注入,权威):

| 值 | 含义 |
|---|---|
| `off`(默认,2026-10-03 用户拍板;存量项目缺键同样视为关) | 全部台词画内开口(存量口径);shot_list 出现 os/vo 即 `placement_valid` FAIL;offscreen / native_lead 系列机检 `skipped: sound_split off`;声桥 carry=line 不可选 |
| `script_only` | 只认剧本层说话人括注 `(O.S.)` / `(V.O.)`,分镜层不得自行转画外 |
| `auto` | 剧本层标记 + 分镜层按白名单自动转画外(每条须写 `placement_reason`)+ 三期原生先入自动建议 |

## 契约

### shot_list `shots[].dialogue_lines[]`

| 字段 | 取值 | 说明 |
|---|---|---|
| `placement` | `on` / `os` / `vo` | 缺省 on。os=说话人在本场但不入画(电话另一端、隔门、出画仍说、反打只拍听者);vo=非本时空声源(内心独白、读信、回忆中的话、幻听传音) |
| `heard_in` | `[shot_id…]` | os/vo 必填(缺省=本镜);**必须与本句所在镜同一生成组**(画外句只能压在同组画面上) |
| `source_fx` | `plain` / `phone` / `door` / `distance` / `inner` / `memory` | 声处理预设(`modules/offscreen_lines.SOURCE_FX`,ffmpeg 滤镜链);缺省 os→plain、vo→inner |
| `offset_s` | ≥0 | 相对 heard_in 首镜起点,缺省 0.4;同一窗口多句按顺序顺延(间隔 0.3 s)。**例外(二期声桥 `carry: line`,2026-10-03)**:本组 `transition_in.sound_bridge` 为 J 时,heard_in 含首镜的画外句 offset_s 可为负(≥ −s,缺省 −s,先于切点出声);下一组为 L 时,heard_in 含末镜的画外句窗口 end + s(越过切点) |
| `placement_reason` | `{trigger, evidence}` | 触发 id 见下;分镜层 D1–D5 须写 evidence |
| `placement_source` | `script` / `directing` / `user` | 谁定的 |

镜内序号 idx 只数有台词的句子(与 dialogue_tts / dialogue_direction / dub_group 同口径),os/vo 句也占 idx 位。

### 剧本写法

`- **林昭(CHAR-0003)**(O.S.):「…」 {emotion: …, pace: …}` / `(V.O.)`;英文 `- **Name (CHAR-0001)** (O.S.): "…"`。剧本只在**声源客观不在画内**时写
(不做剪辑风格决策);剧本写了 = 下游不得改回画内(`placement_matches_source`)。`script_breakdown.json` 把这类句保留为对白条目并带
`placement`(不再归旁白候选);旁白候选只收旁白者文本(`〔旁白候选〕` / `[NARRATION …]` / 无 CHAR 说话人的 `[V.O.]:`)。

### 触发白名单(`modules/offscreen_lines.TRIGGERS`)

| 层 | id | 条件 |
|---|---|---|
| 剧本(screenplay / dialogue-rewrite) | `S-phone` `S-door` `S-exit` `S-remote` | 电话 / 传音另一端;隔门隔墙 / 楼上楼下;人已出画仍在说;远处看不见的人喊话 → O.S. |
| | `S-inner` `S-letter` `S-memory` | 心理描写改台词;读信 / 书信 / 卷轴;回忆里响起的话 / 幻听 / 托梦 → V.O. |
| 分镜(storyboard / shot-planning,仅 auto) | `D1` | 反应镜承接:句长 ≥12 字且戏剧重点在听者(听者有 emotion 标注 / performance trigger 挂在听者) → 前半 on 后半 os 压听者反应镜 |
| | `D2` | 时间尺超限:dialogue_fit / time_budget 报超限且组内有反应镜 / 空镜可承载 → 超出部分转 os,**不改台词文本**(位次:改短台词 > 转画外 > 调镜 / 拆组) |
| | `D3` | 群戏第四人起的插话且该人非本镜视觉焦点 → os,免拆组 |
| | `D4` | 边走边说出画:blocking route 出画 / presence remote → 出画后的句子 os |
| | `D5` | 场首定场镜且首句说话人不在镜内 → 首句 os 压定场镜 |
| 用户 | `U` | 分镜预览页「对白语音」面板手改 |

**硬保护(不触发)**:说话人本人承担本镜表演证据层(performance 节拍挂在他脸上)、情绪峰值句;已冻结 shot_list 的存量集不动。

### 组 `audio_plan`(四值)

`dialogue`(有画内句)| `voice_over`(无画内句,但有旁白挂点或 os/vo 句;`narration_over` 为旧名,两者互认)| `ambient_only`。
`expected_audio_plan()` 是唯一口径(`check_generation_groups.py` / `check_dialogue_fit.py` 共用)。prompt 对 voice_over 组:无 `{}`、
无 audio_refs,开头声明句 `This segment is covered by post-production voice (narration or off-screen dialogue); characters on screen act
silently, no one speaks.`,Global constraints 无对白约束句照写。旁白关闭时 voice_over 仍可由 os/vo 句触发。

### prompt 规则

os/vo 句不写 `{}`、不挂该人 audio_ref、正文不提其名(白模 hidden 规则不变);Shot 段写听者行为(listens to a voice from off-screen /
from the phone, does not speak)。`speakers_le_3`(已落成代码机检)、audioref_bound、nonspeech_group_prompt_ok、refs_all_referenced
**只数画内说话人**;performance_bound:无画内句的人物免除 `{}` 规则,触发词须在 Shot 段正文出现一次(听者反应绑定)。

## 窗口与机检

可用窗口 = heard_in 镜总长 − 画内句估时 − 同窗旁白估时 − 起点偏移 − 同窗前面画外句占用。估时级 `est × 1.15 ≤ 可用`(plan,同旁白
`narration_window_gte_est_x1.15`),实测级 `duration ≤ 可用 × 0.9`(synth / check,同 narration_fit)。超窗 = 回派 dialogue-rewrite 精简或
shot-planning 改 heard_in,**不拉长画面、不硬塞**;允许变速时(output.dialogue_tts_max_tempo > 1)先节奏贴合再判。

| 机检 | 在哪 | 口径 |
|---|---|---|
| `placement_valid` | offscreen_lines.validate(check_generation_groups / check_dialogue_fit 调用) | 取值合法;heard_in 同组且存在;mode off 有 os/vo = FAIL;script_only 有 directing 来源 = FAIL |
| `placement_speaker_is_cast` | 同上 | speaker 是人物 / 生物编号且在 bible index 登记;不在本集任何镜 / 组人物集合 = WARN。旁白不得借人物 V.O. 伪装 |
| `placement_reason_valid` | 同上 | 剧本层 trigger ∈ S-*;分镜层 trigger ∈ D1–D5 / U 且 D* 须写 evidence |
| `placement_matches_source` | check_dialogue_fit | 剧本标画外、分镜改画内 = FAIL;剧本画内、分镜转画外须 placement_source=directing/user + reason |
| `offscreen_fit` | validate(估时)/ synth+check(实测) | 见上 |
| `post_voice_no_overlap` | validate | 同一窗口旁白 + 画外句装不下 = FAIL(挪旁白挂点或改 heard_in) |
| `offscreen_not_in_prompt` | check_prompts | 组 prompt `{}` 内出现画外句文本;audio_refs 含只有画外句的说话人 |
| `offscreen_synced` | check | 台账 `source_fingerprint` ≠ 当前 shot_list 画外句集合(新增 / 删句 / 改位置 / 改效果 / 改台词) |
| `offscreen_all_bound` | synth/check | 有 unbound(选角 / 形态 / 样本缺)或 failed |
| `speakers_le_3` | check_generation_groups | 画内说话人 ≤3(os/vo 不计) |
| `dialogue_fit_shot` / `dialogue_fit_group` | check_dialogue_fit | 只累计画内句 |
| `dialogue_audible` / `shot_time_budget_ok` | check_dialogue_audible / check_time_budget | 只看画内句(画外声不在 clip 里、不占嘴时间) |

## 宿主 CLI 与产物

```
python3 code/offscreen_lines.py plan  --project <slug> --ep epNN [--json]       # 逐句窗口 / 估时 / 合成前置 / 问题,不合成
python3 code/offscreen_lines.py synth --project <slug> --ep epNN [--groups grp…] [--force]
python3 code/offscreen_lines.py check --project <slug> --ep epNN [--json]
python3 code/offscreen_lines.py set   --project <slug> --ep epNN --shot shNNN --idx 0 --placement os --heard-in sh… --fx door --reason "D1|依据"
```

synth:`dialogue_tts.plan` 给每句 key / 选角 / 形态(与对白语音库同口径,带台词演法)→ 库开着且库文件新鲜就直接复制,否则合成到 `_raw/`
再修剪 → 按 source_fx 出 `.fx.wav`(48k 立体声)→ 实测时长 → 可用窗口 × 0.9 收口 → 写台账。产物 `assets/audio/voice/epNN/offscreen/`:
`<shot>_lNN_<CHAR>.mp3`(干声)+ 同名 `.fx.wav` + `offscreen_manifest.json`(逐句 `t_in_group_s` / `duration_s` / `window` / `status`
ok|overflow|unbound|failed;`source_fingerprint`(来源)与 `fingerprint`(已摆位音频,进 mix.json))。幂等:key 与效果不变不重合成。

## 混音 / 后期 / 字幕

- `code/mix_basis.py sources` 顶层新增 `offscreen_lines[]`(`offscreen_lines.mix_rows`:绝对时刻 t0 已含组 `cum_start_s` 与该组
  `time_ops` 换算;起点落在删除区间内的句子标 `dropped` 不混)+ `offscreen_fingerprint`;audio-mixing 把它作第四路「画外对白」轨按 t0
  铺(电平对齐相邻原生对白,BGM 对其 ducking 同对白)。stamp 盖进 `mix.json#offscreen`;改了画外句未重混 = `mix_basis_current` FAIL。
- 后期配音模式:`dub_group.py` 跳过 os/vo 句(manifest `offscreen_lines_skipped`),只有画外句的组不派 p7-dub;画外声仍由本轨承担。
- 样片(动态样片 / 白模样片):`dialogue_track.place_lines` / `whitebox_subtitles` 按 heard_in 首镜 + offset 摆位,字幕前缀 `(画外)` / `(V.O.)`。
- 后期页「配音」轨显示画外条目(label 带 👻,`source: offscreen`);分镜预览「对白语音」面板每句可改声源 / 效果 / 偏移
  (`POST …/dialogue-direction` 带 placement 字段 → `offscreen_lines.set_line`,placement_source=user);
  `GET …/offscreen-lines` 状态、`POST …/offscreen-lines/sync` 后台跑 synth。

## 流水线

新节点 `p8-offscreen`(09-audio/voice-generation,每集,`condition: offscreen_lines_present` = sound_split ≠ off 且本集 shot_list 有
os/vo 句;depends_on p6-shots / p6-dialogue-fit / p6-dialogue-direction(若实例化)/ p8-voiceprints;p8-mix 依赖它)。工位只跑宿主 CLI
plan → synth → check,不手摆音频;overflow 回执上报,orchestrator 回派 dialogue-rewrite 精简或 shot-planning 改 heard_in。

无声组核查三级(§7D ①):① 补旁白(旁白开)→ ② 补人物 V.O.(声画分离开,有原著心理描写 / 信件依据,回派 dialogue-rewrite 加 `(V.O.)`
句 = 剧本变更对白层,不动画面、不吃 0.7 承载、不碰口型)→ ③ 加画内对白 / 交用户裁决。旁白窗口 `window_s` 改为扣除画内句 + 同窗 os/vo 句。

## 与旁白开关的关系

| 内容 | 归属 | 声线 | 开关 |
|---|---|---|---|
| 全知叙述:时间地点、省略交代、总结、人物未说出口的客观评价 | 旁白(narration.md → narration_anchors) | 旁白声线卡 | 旁白 |
| 人物自己的话 / 心声 / 读的信 / 回忆里别人说过的话 | 人物 V.O./O.S.(dialogue_lines) | 该角色 casting | 声画分离 |

vo 的 speaker 必须是本集 cast 的人物编号(`placement_speaker_is_cast`),文本须是人物口吻(叙述体伪装旁白由 logic-qa 审)。audio-mixing 不再按
旁白开关硬编码路数,按 `sources` 列出的轨道混:原生 + BGM + 旁白(开关开才列)+ 画外对白(有就列)。字幕:os/vo 归对白字幕,不看旁白开关。

## 二期(2026-10-03):组边界声桥 `sound_bridge` 已落地

规则细节见 `docs/transition_design.md` 四期改版、WORKFLOW §9C。摘要:
- 旧 `transition_in.audio_lead_s`(「本组原生轨从 cum_start_s − lead 起铺」= 整轨提前、口型错位)**作废**,宿主自动归一为 `sound_bridge {kind: j, s, carry: bed}`;边界指纹变 → 重混一次。不加兼容开关。
- 新契约 `sound_bridge = {kind: j|l, s ∈ (0,1.5], carry: bed|line}`;设置 `transitions.sound_bridge_s`(默认 0.5)/ `sound_bridge_carry`(默认 bed);极简 / 经典不出,电影感换场景 / 跳时段自动给 J(bed),满足条件另给 L 候选;自定义 `custom_map` 加 `l_cut`。机检 `transition_sound_bridge_valid`、`sound_bridge_built`。
- `carry: bed` 由宿主 `code/sound_bridge.py build` 出去人声底床片段(`edit/epNN/sound_bridges/`),audio-mixing 按 `mix_basis sources.boundaries[].sound_bridge` 摆位,**本组原生轨仍从 cum_start_s 同步起**。
- `carry: line` 与本文档联动:只在声画分离 ≠ 关且切点旁有画外句时可写(J:下组首镜 heard_in 含首镜的 os/vo 句;L:前组末镜 heard_in 含末镜的 os/vo 句),由 `offscreen_lines[]` 的 t0 跨切点承担(窗口放宽见契约表 `offset_s` 例外);否则退 bed。

## 三期(2026-10-03):原生先入 `native_lead` 已落地(只开放 J)

**实验(fengshen3 ep07 grp008「白骨洞问罪」,Seedance 2.5 480p 25 s,2026-10-03;基线 = 正式 clip,全部画内)**:

| 变体 | 设计 | 石矶那句的人声段 | 相对切点 | 不重说 | 说话人不入画 | 判定 |
|---|---|---|---|---|---|---|
| 基线 | 全句在 sh041(说话人近景) | 5.50–7.90 s 单段 | 起点在 sh041 起点后 0.125 s | — | — | 对照 |
| B1 J-cut | 「你射死我门人,」写在 sh040(箭主观镜,无人物)画外,「还推不知?」在 sh041 接着说 | 4.30–6.05 + 6.85–7.70(中间是 prompt 要求的吸气停顿),合计 2.60 s(基线 2.40) | **先入 1.075 s**(从 sh040 第一帧起声) | ✅ | ✅(sh040 六帧只有箭) | **通过** |
| B2 L-cut r1 | 「你射死我门人,」在 sh041,「还推不知?」写在 sh042(李靖捡箭特写)画外 | 4.90–7.50 单段 | 比 sh042 起点**早 0.29 s 说完**,sh042 无人声;反而在 sh040 提前 0.48 s 起声 | ✅ | ✅ | ❌ |
| B2 L-cut r2 | 同上,Shot 4 彻底不提后半句、Shot 5 写「0 秒即在画外接着说」 | 4.80–7.40 单段 | 早 0.43 s 说完;sh040 提前 0.62 s 起声 | ✅ | ✅ | ❌ |

结论:模型守「不把说话人画进来」,但倾向把台词**前置**并在说话人镜内收尾,不肯拖过切点。J 顺着这个倾向成功;L 逆着它两次失败。
**三期只开放 J**;L 型需求继续走一期(整句转画外后期合成,D1)/ 二期(底床延续)。提示词只写「从本镜起即进声」,不指望按秒卡点(B1 把整个前镜都压上了声音)。嗓音:两段中值基频 232 / 254 Hz,与石矶其它句 246–262 Hz 一致,李靖 130 Hz。

**契约**:画内句 `dialogue_lines[].native_lead {shot: 同组前一镜, s ∈ (0,1.0], reason {trigger N1|N2|N3|U, evidence}, source directing|user}`——这句**仍是画内句**(照写 `{}`、照挂 audio_ref、照数 speakers_le_3、估时照旧)。先入文本 = 第一个句读前的分句(lead),余句 rest;自动建议只给有句读的句子(按字数硬切会切在词中间)。

**硬前提(机检 `native_lead_valid`,`modules/native_lead.validate`,已接进 `check_generation_groups.py`)**:

| # | 条件 |
|---|---|
| P1 | `output.sound_split = auto` |
| P2 | 对白配音 = 视频原声(后期配音模式下原生声会被 TTS 替换) |
| P3 | 本组生效视频模型 = Seedance 2.5(`shot_timing.group_kind`;只验证过它) |
| P4 | 前一镜与本镜同一生成组(本镜非组首镜;组边界先入走二期声桥) |
| P5 | 前一镜说话人不在画内(`characters` 不含他)且前一镜无任何台词 / 画外句 / 旁白挂点 |
| P6 | 本句是本镜第一句、说话人在本镜画内 |
| P7 | 前镜 ≥0.6 s,`s = min(前镜时长, 1.0, 估时×40%)` |
| P8 | 先入文本不含该人 blocking performance 触发词(performance_bound 要在本镜 `{}` 里找它) |

**触发白名单(仅 auto;shot-planning 自动标,写 reason;不命中一律不加)**:`N1` 插入镜 / 主观镜起声(前镜无人物)、`N2` 听者先行(前镜只有听者)、`N3` 定场起声(前镜是组首定场 / 远景);`U` 用户在「对白语音」面板手标。

**宿主 CLI**(Agent 只准调用):
```
python3 code/sync_native_leads.py plan    --project <slug> --ep epNN            # 已标 + 自动建议(满足 P3–P8 且命中 N1–N3)
python3 code/sync_native_leads.py apply   --project <slug> --ep epNN            # 建议全部写进 shot_list(source=directing)
python3 code/sync_native_leads.py set     --project <slug> --ep epNN --shot sh041 --idx 0 --reason "N1|依据"
python3 code/sync_native_leads.py sync    --project <slug> --ep epNN [--groups grp…] --write   # 写进组 prompt
python3 code/sync_native_leads.py check   --project <slug> --ep epNN            # native_lead_valid + native_lead_bound
python3 code/sync_native_leads.py audible --project <slug> --ep epNN [--groups grp…]   # 出片后核验
```
`sync --write` 往组 prompt 写两句**固定标记句**(幂等、可清理;组 json 记 `native_leads[]` 还原信息,原 prompt 首次备份 `prompt_backups/`):
- 前一镜段末:「【原生先入】本镜起即由<说话人>在画外开口:{<先入>}——说话人不在画内,画面不出现任何多出的人物,话未说完即切到下一镜。」
- 本镜:`{全句}` → `{余句}`(prompt 工位已按句读拆成 `{先入}{余句}` 的只摘掉 `{先入}`),段末「【原生先入】本镜开场时<说话人>正说到一半,上一镜画外已说出的「…」不再重说,接着说完。」

非中文界面写 `Native lead:` 英文句。prompt 工位**不手写**这两句、不自行拆句、不得把全句写进前镜。

**机检**:`native_lead_bound`(p7-prompt 验收):两句都在、余句在、全句 / 先入文本不再出现在本镜 `{}`。出片后 `audible`:组 clip 人声起点(人声分离 + 有声段)落在前一镜窗口、先入 ≥0.3 s = PASS,否则 WARN「先入未生效」——画面仍成立,不阻断、不开缺陷单,QA 记录。`check_dialogue_audible` 对带 native_lead 的镜把窗口向前并入前一镜。白模 hidden 机检:标记句含「不在画内」属否定句,豁免。

**分工**:同组内切点 J → 三期原生;L 或说话人全程不入画 → 一期画外句后期合成(D1 后半句压听者仍是一期);组边界 → 二期声桥。

**界面**:分镜预览「对白语音」面板每句「原生先入」勾选(U);分镜页 🎧 先入 角标;H3A 预览可见。

## 实现位置

`modules/offscreen_lines.py`(契约 / 窗口 / 机检 / 合成 / 混音行 / 写回)、`code/offscreen_lines.py`(CLI)、
`code/check_dialogue_fit.py`、`code/check_generation_groups.py`、`code/check_dialogue_audible.py`、`code/check_time_budget.py`、
`code/performance_bound_check.py`、`modules/dialogue_direction.py`、`modules/script_breakdown.py`、`modules/dialogue_tts.py`、
`code/dub_group.py`、`code/mix_basis.py` + `modules/mix_manifest.py`、`modules/dialogue_track.py`、`modules/whitebox_subtitles.py`、
`services/runtime/core.py`(设置 / 提示注入 / API / 后期轨)、`apps/web/static/{index.html,dialogue-tts.js,preview_storyboard.html,preview_post.html}`
+ 11 份词典;三期 `modules/native_lead.py` + `code/sync_native_leads.py`;规约 WORKFLOW.md §8D 与各工位 SOUL。测试 `tests/test_offscreen_lines.py` 等。
