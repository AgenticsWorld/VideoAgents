# 故事板(/preview/board)与分镜草图

2026-09-11 起,分镜层(`agents/07-directing/storyboard`)的产物有一页专门的表格视图,让用户不读 JSON 也能看懂每场拆了哪些镜、并逐块提意见:控制台顶栏 📋 图标(分镜预览图标之前)/ 各预览页右上下拉「📋 故事板」(分镜预览之前)。

## 页面结构

1. **导演计划** `directing/<ep>/directing_plan.md`:从分镜预览页移到这里,Markdown 渲染(`/static/mini-md.js`,无第三方库),默认折叠;标题栏「✏️ 修改」发总制片。
2. **每场一张卡**:场次头 = 场号 / 地点 / 场景 id / 时段 / ⏱ 分配·草案·定稿秒数 / 镜数·组数 / 🖊 已出草图数 + 资产条(场景缩略、出场人物 sheet 缩略、生物、道具)+ 场次备注(`unit_note` 等)与色彩段;右侧「✏️ 修改」(总制片)。批量出草图的按钮在**单集标题行最后**(「🖊 出草图(N)」,见下)。
3. **逐镜表**:编号(序号、景别、时长建议、草案镜号、镜头表定稿 shNNN·时长·景别·组号,绿色)| 内容(画面内容、🎬 动作、🧍 姿态(分镜层 `poses`:每角色 体位·动作,2026-09-14)、💬 台词(优先取 shot_list 定稿 `dialogue_lines`,缺则解析草案 `dialogue_ref`)、🎙 旁白(N-id + 正文 + 估时;挂镜见下节「旁白显示」)、📐 构图草描、👥 群演、👤 出场、📝 备注)| 草图(按项目画幅的小图 + 状态角标 + 「出图/重出」「✏️ 改草图」)| ✏️ 反馈(总制片)。

数据接口 `GET /api/v1/projects/<p>/previews/board?ep=epNN`(`services/runtime/core.py` `_preview_board`);归一化逻辑在 `modules/storyboard_board.py` `load_board`——storyboard.json 的字段名历经多版(`shots_draft/shots`、`content/subject_action`、`cast/characters`、`unit_alloc_s/alloc_s/…`),统一成一套供页面与 CLI 共用,并按 `shot_list.json` 的 `storyboard_ref`(`S01/order:1`,兼容 `/split:a`、`/shots_draft/`)把定稿镜对回草案镜。

## 分镜草图

- **出图(两种方式)**:
  - **整集宫格批量**(2026-09-11 用户拍板;2026-09-12 由 3×3 降为 2×2):单集标题行最后「🖊 出草图(N)」→ `POST …/board/<ep>/sketch {all:true, provider?, model?, force?}`。宿主把本集**尚未出图**的镜按集内顺序每 4 镜一批(已出的单张/宫格草图一律跳过,单镜/改草图工单在飞的镜也不抢;Shift+点击 = `force` 全部重出),每批调用一次 `python3 code/storyboard_sketch.py --project --ep --grid --keys S01-01,…`:一批出**一张 2×2 宫格图**(1 镜退回单张),`split_grid` 等分切成小图(每格四边各裁 2% 去格线)落到各镜 `<S01-01>.png`,宫格原图存 `assets/storyboard/<ep>/_grids/<时间>_<首键>_<尾键>.png`;台账每镜记 `mode: grid` + `grid: {file, cols, rows, keys, cell}`。任务键 `<project>/<ep>/*`(`jobs["*"]`,带 `total/done/failed/current`),每批结束发 SSE `board_sketch`(`scene:"*"`, `keys:[…]`)。宫格提示词 `build_grid_prompt`(人物优先风格总句 + 各场地点短提示一次 + 逐格 Panel k 景别/机位/出场/画面/动作/神态/构图,总长超 2400 字符按三档收紧逐格文字:地点描述不进,先压画面内容,机位/神态/构图最后压);参考图 = 4 镜出场人物并集按出场次数取前 4 张 sheet;出图尺寸所有渠道一律 3,686,400 像素当量(2560×1440),切 2×2 后每格约 1230×690,不再压缩。**为何降到 2×2**:3×3 每格约 820×460,中景以上的脸只剩几十像素画不出可读表情;9 格并集人物超过 4 张参考图就会换脸;逐格文字预算约 260 字符,加机位/神态后必掉进收紧档。
  - **单镜单张**(不变):每镜「出图/重出」→ `POST … {scene, order, provider?, model?}` → `storyboard_sketch.py --scene S01 --order N`,每镜一张(`mode: single`)。CLI 的 `--scene`(不带 `--order`)整场逐镜仍可用,页面不再挂场次级按钮。
  - 每镜结束发 SSE `board_sketch` 事件,页面另有 4s 轮询 `GET …/board/<ep>/sketches` 兜底。
- **风格**:铅笔手绘分镜,**人物优先、背景留白**(2026-09-12 用户拍板:没有合适的场景参考图,草图弱化场景展现,主要按分镜表现镜头机位、人物比例、神态、动作;提示词见 `modules/storyboard_board.py` `SKETCH_STYLE_PROMPT`:人物线稿清晰、按景别画对人物比例、表情/视线/肢体可读,背景只两三笔示意或留白,画面须体现所述机位角度/高度/镜头)。单镜提示词顺序:风格句 → 景别 → `Camera:` → 出场 → `Body poses and actions:`(2026-09-14,见下)→ 画面/动作 → `Expressions:` → 构图 → 群众 → `Space hint:` 一句短地点(放最后,只作示意,不再拼场景卡描述 300 字)→ 修改意见。`Camera:`/`Expressions:` 由 `camera_hint` / `expression_hint` 从分镜 sketch/content/action 文字按关键词表(俯拍/仰拍/平视/过肩/背影/推进…;微笑/皱眉/惊恐/凝视/低头…)自动推导成英文短语,无命中则不写;分镜原文仍整段进提示词。负面词补了 detailed background / cluttered environment / architectural rendering / interior design。参考图 = 仅出场人物 sheet(≤3),缩到 512px 长边(缓存 `assets/storyboard/<ep>/_refcache/`);**不带场景图**(俯视布局图会误导图像模型,2026-09-11 用户拍板);**不用风格参考图**(2026-09-11 实测带枪械的分镜截图被火山内容审核拒收,用户拍板改为纯文字描述风格)。出图尺寸:火山/BytePlus 按 Seedream 5.x 硬限 ≥3,686,400 像素出(2560×1440 当量),其它渠道 1280×720 当量;成图统一压到 1280 长边。
- **人物姿态/动作(2026-09-14 用户拍板)**:每镜提示词多一句 `Body poses and actions (draw exactly as stated): …`,宫格模式逐格 `Poses: …` 且**不进三档裁剪**。来源优先分镜层结构化字段 `shots_draft[].poses`(`{CHAR-id: {pose, action}}`,`pose` 受控枚举 stand/sit/lie/kneel/crouch/prone 由宿主映射成英文,`action` 中文直通不翻译,拼成「<角色名> kneeling, 挥剑」),存量项目没有该字段时退回从 content/action/sketch 文字按关键词表推导(`pose_hint`,长词优先、`_POSE_EXCLUDE` 剔除车站/走廊/俯拍这类非肢体词)。为什么要加:草图原本靠中文长散文带姿态,站/坐/躺/奔跑/挥剑/闪躲淹没在句中、宫格还会被截掉,模型基本不听;同一字段下游 shot_list → blocking `pose`/站位片段开头 → 白模六态关键帧 → 视频 prompt 主体动作逐层继承。机检 `code/storyboard_pose_check.py`(pose_present)。
- **渠道/模型**:页面顶部「🖊 草图模型」单独选(空 = 跟随全局图像渠道),存 `state.json` `image_model_prefs.sketch`(`GET/POST /api/v1/config/image-model/sketch`,旧路径 `/config/sketch-model` 仍可用)。下拉组件 `apps/web/static/image-model-picker.js` 与场景/人物/生物/道具预览页顶部的图像模型下拉共用(kind = scenes/panos/characters/creatures/props,同样存 `image_model_prefs.<kind>`;场景页 2026-09-12 起分「🎨 图像模型」scenes 与「🌐 全景模型」panos 两块,各自独立、默认都跟随全局);这几类由 genmedia 按输出目录 `assets/concepts/<kind>/` 自动套用(`modules/genmedia.py` `image_pref_env`,显式 `--provider/--model` 优先;`scenes/<sid>/panos/` 下判为 panos,全景的模型支持检查与出图按 panos 的选择,分镜背景图按 scenes),概念图工位无须改动;Key 仍取该渠道在「生成模型」页保存的配置。实现:`genmedia image --provider/--model` = 环境变量 `VIDEOAGENTS_IMAGE_PROVIDER/MODEL`,只作用于当前进程,不改全局设置。模型清单 `apps/web/static/image-models.js` 与「生成模型」页共用。
- **台账** `assets/storyboard/<ep>/index.json`(schema `storyboard_sketches/1.0`):`shots[<S01-01>] = {file, status: queued|running|done|failed, error, prompt, note, provider, model, aspect, refs, started_at, finished_at, updated_at}`;写入经 flock 串行。图片 `assets/storyboard/<ep>/<S01-01>.png`。草图只供看构图/调度,**不进视频参考图**。
- **修改草图**:单镜「✏️ 改草图」→ 浮窗直发 `07-directing/storyboard-sketch`(`agents/07-directing/storyboard-sketch/SOUL.md`),工单首行带 `[草图 epNN/S01-03]` 标记;宿主按在跑/排队工单文本匹配该标记,页面把该格标成「🎨 重绘中」并轮询,工单结束(SSE `run` 事件)自动刷新图片。Agent 把意见翻成一句画面指令作 `--note` 重跑同一 CLI(note 存台账,下次重出仍生效),不改任何分镜文件。
- **其它修改**:故事板整体 / 场次 / 单镜「✏️」一律发总制片(牵涉分镜、镜头表等上下游)。

## 兼容

- 老项目没有 `storyboard.json` 时只显示导演计划与提示;场次缺 `scene_no` 按 `screenplay_ref`/序号兜底。
- 分镜预览页不再下发/显示导演计划(`api_preview_storyboard` 去掉 `directing_plan` 字段)。

## 旁白显示(2026-09-15)

旁白稿 `story/episodes/<ep>/narration.md` 在 Phase 1 定稿,每条只锚到「场次 | 场景 ID | 事件 ID | 剧本动作行引文」,不到镜。页面每镜 🎙 行显示落在该镜的旁白 **N-id + 正文 + 估时**,挂镜由 `modules/storyboard_board.attach_narration` 统一定,优先级:

1. **草案镜 `narration_ref`**(分镜师明确标的起播镜,2026-09-15 起有旁白稿时必填,机检 `code/storyboard_narration_check.py --strict`)——不打标;
2. **shot_list `narration_anchors`**(H3S 之后 shot-planning 定稿)——`anchor_shots` 首镜反查草案镜,打「镜头表挂点」灰标;
3. **锚点引文推定**(存量项目 / 分镜师漏写):锚点「…」引的剧本动作行(CHAR-id 换成人名)与各镜 content/action/sketch 做字符二元组覆盖率,≥0.25 取最高的镜;不够时「场首/场末」关键词兜底——打「按锚点推定」灰标,悬停显示锚点原文;
4. 锚到本场但推不到镜的,列在场头「锚在本场,未定到镜」;连场次都对不上的进 `board.narration.unplaced`(标题行「N 条推定挂点」计数含之)。

标题行统计「🎙 N 条旁白 Σs」+ 推定条数;动态样片的旁白字幕(紫)同样烧正文。锚点解析在 `modules/script_breakdown.parse_narration_md`(2026-09-15 起保留全部四段,此前只留场次号)。

## 故事板签字(H3S,2026-09-11)

- `agents/workflow.yaml` 新增人工闸门 **g6s「H3S-故事板确认」**:`depends_on: [p6-storyboard]`,`p6-shots` 改为依赖 g6s——分镜师交付后,总制片按规约建签字卡(`dispatch.py --confirm "【H3S-故事板确认｜项目 <slug>】…" --sign`;漏建时运行时兜底 `ensure_human_gate_approvals` 补建),未签字不派 shot-planning。草图**不是**签字前置,签字卡只报「已出草图 M/N」。
- 页面:标题下方签字条(`_board_gate`):有待答复 H3S 卡 → 「✅ 签字确认 / ⏸ 暂缓」按钮(`POST /api/v1/projects/<p>/board/<ep>/signoff {confirm_id, answer}` → 与控制台同一条签字卡 `api_confirm_answer`,并落 `directing/<ep>/storyboard_signoff.json` 记 storyboard.json 的 mtime);已签 → 「✅ 故事板已签字」;签字后 storyboard.json 又改动 → 「⚠ 签字已过期」(等总制片重新建单)。卡的归属按 project + checkpoint/question 含 `H3S` + gate_id/question 含集号匹配。
- 存量项目的 `runs/dag.json` 没有 g6s 节点,总制片下次展开/对账 DAG 时按 workflow.yaml 补;在此之前页面只显示「故事板签字点 · 等总制片建签字单」。

## 动态样片(animatic,2026-09-11)

- 宿主 CLI `code/animatic.py --project --ep [--no-audio]`:把草图按时长串成 `assets/storyboard/<ep>/animatic.mp4`(+ `animatic.json`),PIL 逐镜合成帧(草图**按画布等比缩放**——单张 1280 长边与宫格切出的约 820×460 小图混用时尺寸不一,`fit_canvas` 既缩小也放大到刚好装进画布再居中,余边留黑;顶部半透明黑带压镜号标签 + 画面内容文字(≤3 行)便于对照;缺草图的镜用占位卡:灰底 + 镜号 + 画面内容文字居中;底部字幕带:台词黄、旁白紫),ffmpeg concat 出 1280×720 / 720×1280(按项目画幅)24fps;有 `assets/audio/narration/<ep>/` 旁白段按 shot_list `narration_anchors` 挂点混入、`assets/audio/voice/<ep>/` 以 shNNN 开头的对白干声挂到该镜起点。零生成费用。
- 时长口径:有 shot_list 定稿用 `duration_s`(草案镜被拆成多镜时求和),否则草案 `duration_hint_s`;`duration_source = final|draft|mixed` 页面角标注明。
- 入口:故事板页「🎞 出动态样片」(标题下折叠块,内嵌播放;缺 N 张草图只提醒不拦,Shift 无关)(成片发布页的「🎞 动态样片」板块 2026-09-13 已删,只保留故事板页入口)。`POST /api/v1/projects/<p>/board/<ep>/animatic` 起宿主后台线程,SSE `board_animatic` 通知,页面 4s 轮询兜底;`animatic_status()` 按 storyboard.json / shot_list.json / 任一草图比样片新判「已过期」。


## 预览页互跳(2026-09-12)

剧本预览、故事板、分镜预览三页在场次头行右侧、故事板镜行右侧和分镜镜卡首行右侧放图标链接(📜 剧本 / 📋 故事板 / 🎦 分镜,只显示图标,tooltip 带编号),点击跳到目标页并定位、高亮 2 秒。链接只对目标真实存在的场次/镜显示,存在性由端点给出:剧本端点 `board_scenes`/`shot_scenes`,故事板端点 `script_scenes`/`shot_scenes` + `shots[].final[].shot_id`,分镜端点 `script_scenes`/`board_scene_nos` + `shots[].board_key`(经 storyboard_ref 反查的故事板镜行键 S01-03)。URL 契约 `?project=&ep=#scene=S01 | #shot=sh020 | #shot=S01-03 | #group=grpNNN`,由 `static/jump-anchor.js` 在页面加载前读走 hash、渲染并恢复滚动位置后定位;找不到目标时页顶提示。场次以 `scene_no`(S01 式)对齐,三边写法不一致的场次(如 `S03(续)` vs `S03-cont`)或老 shot_list 没写 `scene_no` 的项目不出链接。
