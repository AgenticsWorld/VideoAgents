# 过场设计(transition design)· 组边界的字卡 / 定场空镜 / 叠字幕 / 时光流转(2026-09-24 一期,2026-09-26 二期)

适用:所有项目。项目「过场模式」(🎵 视频节奏弹窗 / 新建向导第二步,`settings.json#transitions.mode`)决定做多少:极简 = 现状(硬切为主,只按导演转场清单做叠化 / 黑白场),经典 = 字卡 / 全景扫动定场 / 叠字幕 / 定格 / 接缝风格,电影感 = 定场 + 叠字、**生成式**定场空镜与桥接、成对运镜,自定义 = 六类边界逐项选。存量项目无该段 = 极简;新项目默认经典。

## 为什么

组视频是一组一组生成的,组之间原本只有硬切或导演清单里点名的叠化 / 黑白场。换场景、跳时段、进出闪回时观众没有任何「被带过去」的手段:没有地点字幕、没有定场空镜、没有时间字卡,fengshen3 ep06 一集里换了七次场景全靠硬切。一期把「组边界」升为一等对象,给了设计表 + 页面裁决 + 成片渲染的整条链;二期补上**谁来设计**(工位)和**定场空镜怎么真的出片**(视频生成的位置)。

## 数据

| 文件 | 谁写 | 内容 |
|---|---|---|
| `directing/epNN/transition_design.json` | 宿主 CLI `code/transition_design.py`(propose / design / card / accept / reject / apply) | 每集一张设计表 `schema transition_design.v1`:逐边界 `id B-grpA-grpB` / 诊断(换场景 / 跳时段 / 阵容 / 光线 / 叙事块进出、剧本转场句、导演声明、continuity 判定、字卡文字候选、定场素材可用性)/ `design` / `alternatives[]` / `status`(proposed / accepted / rejected / none)/ `source`(mode / agent / user / shot_list / post_plan)/ `feedback[]` |
| `directing/epNN/shot_list.json#generation_groups[].transition_in` | 只由 `apply` 投影写回 | **唯一定稿字段**。一期扩展:`join.style`(dissolve 的 xfade 风格族)、`inserts[]`(`title_card` / `establishing` / `timelapse` / `bridge`,单段 0.5–4 s、单边界 Σ ≤ 6 s、首组不得)、`overlay_card`(叠字幕,不变长)、`sound_bridge {kind, s, carry}`(四期声桥,2026-10-03 改版;存量 `audio_lead_s` 读入时自动归一为 `{j, s, bed}`)、`link {kind, out, in, out_shot, in_shot, note, …}`(剧本场间衔接登记卡,2026-10-10,docs/scene_links.md;导演采纳的衔接由 `propose` 登记并当场写回) |
| `assets/transitions/epNN/<B-id>.establishing.mp4` + `.still.jpg` | video-generation(clip)/ 宿主 `clips --prepare`(首帧静帧) | 二期:生成式定场空镜 clip 与其首帧 |
| `assets/transitions/epNN/<B-id>.bridge.mp4` | video-generation | 生成式桥接 clip(首尾帧贴前组尾 / 本组首) |
| `edit/epNN/transitions/<B-id>/` | `render_transitions.py build` | 渲好的插入段 `ins{k}.mp4`、字卡 / 叠字 PNG、预览小片、`meta.json`(指纹) |
| `edit/epNN/transitions_render.json` | `render_transitions.py check` | 成片台账:`inserts[]` 构建状态、`timemap`(插入段占时)、黑场白名单 |

`transition_in` 契约与机检 `transition_ok` 在 `code/check_generation_groups.py`(INSERT_KINDS / ESTABLISHING_MODES = pano_sweep | plate_kenburns | i2v / 预算);设计表机检 `transition_design_ok` 在 `modules/transition_design.check`;成片机检 `transition_render_ok`(含 `insert_budget_ok` / `inserts_built` / `insert_frames_verified`)在 `code/render_transitions.py`。

## 流程(按流水线位置)

```
Phase 6  director 转场清单 → storyboard/shot-planning 定稿 transition_in(非硬切转场)
         → p6-continuity → [p6-shot-plates | p6-scene-plates](定场素材)
         → p6-transition-design(07-directing/transition-design,模式 ≠ 极简)        ← 二期
             transition_design.py propose → 逐边界复核 design / card → clips --prepare(i2v 首帧)→ check
         → 用户在分镜预览页过场卡裁决(✔ 接受 / 候选 / ⏭ 保持硬切 / ✎ 改字 / ✏️ 反馈)
         → g6 H3A 签字:签字卡带待裁决数,**签字即接受剩余 proposed 并 apply 进 shot_list**    ← 二期
Phase 7  p7-transition-clips(video-generation,每集;仅定稿含 i2v 定场 / 桥接时)                ← 二期
             transition_design.py clips --prepare → genmedia video --first-frame <still> … → clips --check
         (与组视频并行,进 g7 H3B 依赖)
Phase 9  p9-transition:render_transitions.py plan → build(消费 clip;缺 = missing/FAIL,不顶替)→ render → check
         p8-mix 混音边界层按 mix_basis sources 的 boundaries[] 留空档;finalize 按 timemap 平移
```

### 一期(2026-09-24)

- 诊断 `diagnose`:相邻组比对 scene_id / time_of_day / characters_union / lighting_scheme_id / narrative_block,读剧本场次头与场尾「转场:」句、导演阐述「无字幕板」声明(仅提示)、continuity 衔接表;字卡文字候选 = 关系时间词(剧本 > continuity > 推定时段词)+ 场景库地名;定场素材 = 服务本组首镜的全景锚点(pano_sweep)> 首镜起点母图(plate_kenburns)。
- **剧本读取(2026-10-10 修,docs/scene_links.md)**:场次头与转场句改由 `modules/scene_links` 读——此前只认行首是「转场」的行,`- 转场:` / `**转场**:` / 英文 `TRANSITION:` / 独立一行 `CUT TO:` 都读不到,场头也只认 `## Sxx | INT | SCN-… | …` 一种排版(全部项目副本合计:能读到转场句的场次 241 → 2536;原先读得到的场次字段与转场句不变)。场尾转场行带 `{衔接, 出, 入}` 时诊断另出 `screenplay_link {from, to, kind, tier, out, in, …}`,只挂在「本场最后一组 → 下一场第一组」这条边界上,并带导演处置 `script_link_disposition {disposition, land, note}`。导演采纳 / 修改的衔接由 `propose` 登记成 `transition_in.link`(`status=accepted, source=script_link`,当场 apply;模式建议只进候选;电影感下运动接力带成对运镜、声音 / 台词接力带声先入,经典 / 自定义放候选,极简不出),详见 docs/scene_links.md「二期」。
- 建议 `propose`:按模式表出主设计 + 候选;导演清单 / 后期页已写的设计记 accepted 不动;用户裁决保留。
- 页面:分镜预览页每两组之间一张过场卡,顶部汇总条(集级模式下拉 / 插入预算条 / 出建议 / ⏹ 集尾);`▶ 出预览` 走 `render_transitions.py preview`。
- 成片:`build` 把插入段渲成段文件,`render` 按段链插到组边界(前组尾 → 定格 → 黑场 → 插入段 → 本组首),成片按 Σ 变长、timemap 同表 `kind: boundary_insert`;字卡 / 叠字字体按 §2 规则 9 自动取项目字体。

### 二期(2026-09-26):工位 + 生成式定场空镜

**工位 `07-directing/transition-design`**(节点 `p6-transition-design`,条件 `transition_design_enabled` = 生效模式 ≠ minimal)

- 只跑宿主 CLI。`propose` 之后逐边界复核:归类是否与剧本 / 导演清单一致、字卡文字(推定时间词)是否对、按首镜景别 / 素材可用性 / 预算在候选间取舍。
- 新 CLI 动词 `design --boundary … --alt N | --design '<json>' --note 理由`:把候选或自拟设计升为主设计,**状态仍 proposed**,原主设计退入候选「原建议」;`source=agent`,`designed_by` / `agent_note` 落表,页面打「🤖 工位建议」。已裁决(accepted / rejected / source=shot_list|post_plan)的边界拒改,只能 `feedback`。
- `propose`(非 `--force`)不再覆盖工位定过的主设计,模式默认建议只进候选。
- 工位不接受、不 apply。**H3A 签字即接受**:服务端签字时 `accept_all_proposed(by=sign:g6)` 把仍 proposed 的边界接受并 apply(同 H3W 待决项「签字即接受默认」先例);签字卡末尾自动追加「过场设计待裁决:epNN N 处(含 M 段需视频生成的定场/桥接)」。想保留硬切的边界在签字前点「⏭ 保持硬切」。CLI `accept-all` 供补跑。
- 机检 `transition_design_ok` 新增 `transition_design_generative_allowed`(i2v / bridge 只在模式 `allow_generative` 时可出)与 `transition_clips_ready`(WARN:定稿的生成式 clip 是否齐,Phase 7 的事)。
- 修改师 `REVISION_KIND["transition"]` 首位换成本工位 SOUL(过场卡 ✏️ 反馈由修改师按本工位规约代行 design / card)。

**生成式定场空镜(i2v)——「换场景:定场空镜 + 叠地点字幕」**

- 何时:生效模式 `allow_generative` 为真(电影感;自定义勾选「生成式过场」)。`propose` 直接把 `establishing.source` 出成
  ```json
  {"scene_id": "SCN-0002", "mode": "i2v",
   "file": "assets/transitions/ep01/B-grp002-grp003.establishing.mp4",
   "still": "assets/transitions/ep01/B-grp002-grp003.establishing.still.jpg",
   "base": {"mode": "plate_kenburns", "file": "assets/concepts/scenes/SCN-0002/plates/p1.png", "zoom": 1.08},
   "prompt": "定场空镜:南天门(外景);时段:日间;画面严格延续首帧的场景与光线,镜头极缓慢推进…;全程无人物…;无字幕…;不切镜…",
   "camera": "slow_push_in"}
  ```
  `base` 保留一期的全景锚点 / 母图信息供渲首帧;契约要求 i2v 必给 `source.file`(assets/transitions/ 下 .mp4)。经典 / 极简 / 自定义未勾 → 仍是 `pano_sweep` / `plate_kenburns`(ffmpeg 合成,零视频生成费)。
- 首帧静帧:`transition_design.py clips --prepare`(`prepare_clip_stills`)按项目画幅从全景锚点视窗(`pano_still`)或母图(居中裁切缩放)渲出 `.still.jpg`;过场卡在 clip 未出时显示它 + 「待生成」。
- **clip 在 Phase 7 出**(为什么放这里:它是一次视频模型调用,归 08-video-gen,须在 H3A 定稿之后、H3B 审看之前,与组视频并行且不进组序续接链):节点 `p7-transition-clips`(video-generation,每集,条件 `transition_clips_requested` = `clips` 清单非空,依赖 g6、进 g7 依赖)。工位按清单逐段 `genmedia video --first-frame <still> --prompt <提示词> --duration <请求时长 = max(4, ceil(插入段时长))> --generate-audio off --output <file>`,不挂人物参考图、不传参考视频;`clips --check` = 机检 `transition_clips_ready`(文件在、时长 ≥ 插入段时长、首帧在)。
- Phase 9:`render_transitions.py build` 对 `i2v` 只消费文件——按 fps 重采样、裁到插入段帧数(不足尾帧克隆)、叠地点字幕照一期路径合成到定场段上;**缺文件 = `meta.inserts[k].missing`,`inserts_built` FAIL**,transition 工位上报补派 `p7-transition-clips`,不得退回全景横摇顶替(设计已过 H3A)。指纹含 clip 文件(size / mtime),clip 出片后自动重建。
- 页面:过场卡 `render_state` 新值 `awaiting_clip`(待生成);i2v 段显示首帧静帧与 已生成 / 待生成 chip。

## 配置

| 层级 | 位置 | 取值 | 说明 |
|---|---|---|---|
| 项目 | `settings.json#transitions.mode`(🎵 视频节奏弹窗 / 新建向导) | `minimal` / `classic` / `cinematic` / `custom` | 预设附属项固定:极简 禁字卡·0%·生成式关·首镜定场关;经典 允许·8%·关·关;电影感 允许·10%·**开**·开;custom 四项 + 六类边界 `custom_map` 可选 |
| 集 | `assets/group_settings/epNN/episode.json#transitions_mode`(分镜预览页「⟿ 过场」下拉) | 同上 / project | 改模式即重出建议,不自动接受 |
| 字体 | `settings.json#transitions.card_font` | 项目内路径 / `proj:<family>` | 缺省 `refs/fonts/` 首个 > 全局 > 系统 |
| 集尾 | `settings.json#transitions.episode_close`;shot_list 顶层 `episode_close` | fade_black / fade_white / cut_black / cut_white / hard_cut | 见 WORKFLOW §9C「集尾收束」 |

## CLI 速查

```
python3 code/transition_design.py diagnose|propose|design|feedback|accept|accept-all|reject|card|apply|check|clips|mode --project <slug> --ep epNN …
python3 code/render_transitions.py plan|build|preview|render|check --project <slug> --ep epNN
```

## 机检一览

| 机检 | 在哪 | 何时 FAIL |
|---|---|---|
| `transition_ok`(transition_type_valid / transition_reason_required / transition_pad_valid / transition_join_valid / transition_insert_valid / transition_insert_budget / transition_close_valid / narrative_block_paired) | check_generation_groups.py | shot_list `transition_in` 契约不合法;i2v 缺 `source.file` |
| `transition_design_ok`(present / contract / coverage / applied / generative_allowed / transition_ok;clips_ready WARN) | transition_design.py check | 设计表缺(非极简)、契约不合法、有变化边界缺条目、已裁决未同步、模式不允许却出了 i2v / bridge |
| `transition_clips_ready`(+ `transition_clip_stills` WARN) | transition_design.py clips --check | 定稿的 i2v / bridge clip 缺文件或时长不足 |
| `motion_pair_valid` / `transition_sound_bridge_valid`(入 transition_ok) | check_generation_groups.py | 配对不合法 / 与 inserts 并用 / 首组 / 缺 reason;声桥 s ∉ (0,1.5] / 配了插入段或黑场 / type 非 hard_cut·dissolve / `carry: line` 但切点旁无画外句或声画分离关 |
| `sound_bridge_built`(p8-mix 验收) | code/sound_bridge.py check / mix_basis check | 有声桥的边界缺底床片段或构建指纹落后于取源版本 / 设计 |
| `motion_pair_bound` | sync_motion_pairs.py | 两侧组 prompt 缺运镜对接句、句子不在正确 Shot 段、残留过期句 |
| `transition_link_valid`(入 transition_ok)/ `script_links_landed` / `scene_link_bound` | check_generation_groups.py / check_scene_links.py / sync_scene_links.py | 剧本场间衔接登记卡契约不合法 / 导演采纳的衔接没落进 shot_list 或登记卡过期 / 两侧组 prompt 缺衔接句(docs/scene_links.md) |
| `transition_render_ok`(… inserts_built / insert_frames_verified / insert_budget_ok) | render_transitions.py check | 生成式 clip 缺失、段文件缺、指纹过期、预算超 |

### 三期(2026-09-26):生成式桥接 + 成对运镜

**桥接 `bridge`**(允许生成式过场的模式)

- 何时:进闪回 / 梦境 / 想象块的边界 propose 主设计 = `{"type":"hard_cut","inserts":[{"kind":"bridge","duration_s":2.0,"join_out":"hard_cut","audio":"sustain","file":"assets/transitions/epNN/<B-id>.bridge.mp4","first_frame":"…/<B-id>.bridge.first.jpg","last_frame":"…/<B-id>.bridge.last.jpg","prompt":"过渡桥接:画面从首帧(书房)连续形变、流动到尾帧(南天门),记忆浮现…;中段不出现任何新的人物…"}]}`(可带叠字);白场 + 定格退作候选;出块边界给「桥接回到当下」候选;自定义 `custom_map` 选 `bridge` 同。经典 / 极简 / 自定义未勾生成式 → 不出(机检 `transition_design_generative_allowed`)。
- 首尾帧:首帧 = 前组尾帧(`assets/clips/epNN/<from>.last_frame.png`,无则从前组 clip 抽末帧),尾帧 = 本组 clip 首帧;`clips --prepare` 抽出到 `assets/transitions/epNN/`。两侧组视频未出时 `--prepare` 报「组视频尚未生成」——所以 **`p7-transition-clips` 依赖全组 `p7-video`**。
- clip:video-generation 首尾帧模式 `genmedia video --first-frame … --last-frame … --prompt <桥接句> --duration 4`;`clips --check` = `transition_clips_ready`(文件、时长)+ `transition_clip_stills`(首尾帧在)。build 消费文件,`insert_frames_verified` 核桥接首末帧贴合前组尾 / 本组首。契约:bridge 须给 `file`,与 `motion_pair` 互斥。

**成对运镜 `motion_pair`**

- 字段:`transition_in.motion_pair {"out": <前组尾镜运镜>, "in": <本组首镜运镜>, "speed": slow|medium|fast}`;合法配对 `MOTION_PAIRS`(check_generation_groups.py):`pan_left/right`、`tilt_up/down`、`whip_pan_left/right`、`dolly_left/right` 同向延续,`push_in ↔ pull_out` 互补。机检 `motion_pair_valid`:配对合法、type ∈ hard_cut/dissolve、与 inserts 互斥、首组不得、须写 reason。
- 出处:电影感模式下换场景 / 跳时间边界的候选「成对运镜 + 声先入」(默认 pan_right→pan_right medium;工位按两侧构图 `design --design` 改方向);自定义 `custom_map` 选 `motion_pair` 为主设计。
- 落地:不是插入段,而是**两侧组 prompt 各一句**。宿主 `code/sync_motion_pairs.py --project <slug> --ep epNN [grp…] --write`(`modules/motion_pairs.py`):前组**最后一个** `Shot N:` 段末追加「【运镜对接】本镜结尾以中速向右横摇带出画面,收尾时主体已移出画外,运镜不停、不减速;下一组从同一向右横摇接入。」,本组 **`Shot 1:`** 段末追加「【运镜对接】本镜开头延续上一组的中速向右横摇接入,前 0.5 秒画面仍在向右横摇中,随后稳定到本镜构图;开头不切镜、不叠化。」(非中文界面 `Motion pair:` 英文句);幂等(先剔旧标记句),设计撤了只剔除,原 prompt 首次备份到 `directing/epNN/whitebox/prompt_backups/`。机检 `motion_pair_bound`(p7-prompt 验收项):两侧句子在、落在正确 Shot 段、无残留过期句;组 prompt 未写 = skipped;无 motion_pair 的集 PASS。
- 页面:过场卡接缝标签显示「成对运镜 out→in」。

### 四期(2026-09-26 立,2026-10-03 改版):声桥 `sound_bridge`(J-cut / L-cut)

- **旧语义作废**:四期原字段 `audio_lead_s` 的落法是「本组原生轨从 cum_start_s − lead 起铺」= 整条原生轨提前 lead 秒,对白组整组口型错位,不是 J-cut。改版后字段消失:宿主 `transition_of()` 把存量 `audio_lead_s` 自动归一为 `sound_bridge {kind: j, s, carry: bed}`,边界指纹随之变 → `mix_basis_current` 报 stale,重混一次即可;不加兼容开关。
- 字段:`transition_in.sound_bridge = {kind: j|l, s ∈ (0, 1.5], carry: bed|line}`。`j` 声先入 = 下组声音压在本组尾画面上;`l` 声延续 = 本组声音拖过切点压在下组首画面上;`carry: bed` = 去人声的底床预滚 / 延续,`carry: line` = 一句画外台词跨切点(**仅项目「声画分离」≠ 关且切点旁有画外句**:J 要下组首镜 `heard_in` 含首镜的 os/vo 句,L 要前组末镜 `heard_in` 含末镜的 os/vo 句;否则退 bed;docs/sound_split.md)。机检 `transition_sound_bridge_valid`:s 区间、只配无 inserts / 无 hold_s 的 hard_cut / dissolve、首组不得、须写 reason、carry line 的画外句存在性。
- 设置:`settings.json#transitions.sound_bridge_s`(0.1–1.5,默认 0.5)、`transitions.sound_bridge_carry`(bed 默认 / line),所有模式都存(视频节奏设置「声桥默认」两项)。生效:极简 / 经典不出声桥候选;电影感换场景 / 跳时段边界 propose 自动给 J(carry bed)主设计,前组尾镜无画内对白且下组首镜定场 / 远景或无对白时另给 L 候选;自定义 `custom_map` 选 `j_cut`(声先入)/ `l_cut`(声延续)为主设计。
- 落地在混音,不在画面:`render_transitions` 不处理它(cut_v2 自带声轨不是成片声轨),timemap 不变。① 宿主 `python3 code/sound_bridge.py build|check --project <slug> --ep epNN [--force]`(audio-mixing 在 `mix_basis.py sources` 之后、铺轨之前必跑):按 `sources` 各组取源文件(当前采纳版本)出底床片段 `edit/epNN/sound_bridges/<B-id>.j.wav|.l.wav` + `manifest.json`——J bed = 下组取源音频开头 s 秒去人声、s 秒渐强;L bed = 前组取源音频结尾 ≤1 s 去人声循环延续到 s 秒、两端 40 ms 淡化、s 秒渐弱;去人声走 `modules/audio_separation`,模型不可用回落低通 4 kHz + −6 dB 并 WARN;`carry: line` 不出文件;幂等,构建指纹 = 取源文件 + kind/s/carry。② 摆位:`mix_basis.py sources` 的 `boundaries[]` 也列出 `total_s=0` 但带 `sound_bridge {kind, s, carry, file, status}` 的边界(`BRIDGE` 行打印),边界指纹追加声桥项;audio-mixing 四种摆位——**J bed**:file 从 `cum_start_s(B) − s` 起铺(渐强),前组原生轨不变、**本组原生轨仍从 cum_start_s 同步起**;**J line**:无文件,下组首句画外句由 `offscreen_lines[]` 的 t0(可 < cum_start_s)承担;**L bed**:file 从 `cum_start_s(B)` 起铺 s 秒(渐弱),本组原生轨前 s 秒从 −12 dB 渐强到 0;**L line**:前组末句画外句 t0 + duration 越过切点,本组原生轨同样前 s 秒渐强。BGM / 旁白 / wav 总长不变。stamp 盖边界指纹(含声桥)+ sound_bridge 构建指纹;改了声桥或未 build = `mix_basis_current` FAIL / `sound_bridge_built` FAIL,重跑 p8-mix。
- 与声画分离联动:`carry: line` 时 offscreen_lines 窗口放宽——J:下组首镜 heard_in 的画外句 `offset_s` 可为负(≥ −s,缺省 −s);L:前组末镜 heard_in 的画外句窗口 end + s。
- 页面:过场卡接缝标签显示「声先入 0.5s」/「声延续 0.5s」(carry line 加「·画外台词」);新「声桥」行(无 / J / L + 秒数 + 承载)经 `decide {action: design}` 写回设计。

## 未做 / 后续

- 机检 `establishing_on_scene_change`(电影感 / 自定义「新场景首镜必须定场」时新场景首镜景别 ≥ 全景且无近景人物)未落地。
- 声桥只在成片混音里体现,过场卡「▶ 出预览」小片仍是静音,听不到 J/L-cut;底床片段真跑(去人声模型)未在真项目验证。
- 成对运镜只写 prompt,不回写 `shots/<id>/camera.json`;运镜工位 / 白模不知道这一对。
- 真项目未跑二至四期节点;服务须重启后 H3A 签字接受 / 提示词「过场模式」段才生效。
