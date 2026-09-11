# 故事板预览(/preview/board)与分镜草图

2026-09-11 起,分镜层(`agents/07-directing/storyboard`)的产物有一页专门的表格视图,让用户不读 JSON 也能看懂每场拆了哪些镜、并逐块提意见:控制台顶栏 📋 图标(分镜预览图标之前)/ 各预览页右上下拉「📋 故事板预览」(分镜预览之前)。

## 页面结构

1. **导演计划** `directing/<ep>/directing_plan.md`:从分镜预览页移到这里,Markdown 渲染(`/static/mini-md.js`,无第三方库),默认折叠;标题栏「✏️ 修改」发总制片。
2. **每场一张卡**:场次头 = 场号 / 地点 / 场景 id / 时段 / ⏱ 分配·草案·定稿秒数 / 镜数·组数 / 🖊 已出草图数 + 资产条(场景缩略、出场人物 sheet 缩略、生物、道具)+ 场次备注(`unit_note` 等)与色彩段;右侧「✏️ 修改」(总制片)。批量出草图的按钮在**单集标题行最后**(「🖊 出草图(N)」,见下)。
3. **逐镜表**:编号(序号、景别、时长建议、草案镜号、镜头表定稿 shNNN·时长·景别·组号,绿色)| 内容(画面内容、🎬 动作、💬 台词(优先取 shot_list 定稿 `dialogue_lines`,缺则解析草案 `dialogue_ref`)、🎙 旁白挂点、📐 构图草描、👥 群演、👤 出场、📝 备注)| 草图(按项目画幅的小图 + 状态角标 + 「出图/重出」「✏️ 改草图」)| ✏️ 反馈(总制片)。

数据接口 `GET /api/v1/projects/<p>/previews/board?ep=epNN`(`services/runtime/core.py` `_preview_board`);归一化逻辑在 `modules/storyboard_board.py` `load_board`——storyboard.json 的字段名历经多版(`shots_draft/shots`、`content/subject_action`、`cast/characters`、`unit_alloc_s/alloc_s/…`),统一成一套供页面与 CLI 共用,并按 `shot_list.json` 的 `storyboard_ref`(`S01/order:1`,兼容 `/split:a`、`/shots_draft/`)把定稿镜对回草案镜。

## 分镜草图

- **出图(两种方式)**:
  - **整集九宫格批量**(2026-09-11 用户拍板):单集标题行最后「🖊 出草图(N)」→ `POST …/board/<ep>/sketch {all:true, provider?, model?, force?}`。宿主把本集**尚未出图**的镜按集内顺序每 9 镜一批(已出的单张/宫格草图一律跳过,单镜/改草图工单在飞的镜也不抢;Shift+点击 = `force` 全部重出),每批调用一次 `python3 code/storyboard_sketch.py --project --ep --grid --keys S01-01,…`:一批出**一张 3×3 宫格图**(不足 9 镜:2–4 镜 2×2,1 镜退回单张),`split_grid` 等分切成小图(每格四边各裁 2% 去格线)落到各镜 `<S01-01>.png`,宫格原图存 `assets/storyboard/<ep>/_grids/<时间>_<首键>_<尾键>.png`;台账每镜记 `mode: grid` + `grid: {file, cols, rows, keys, cell}`。任务键 `<project>/<ep>/*`(`jobs["*"]`,带 `total/done/failed/current`),每批结束发 SSE `board_sketch`(`scene:"*"`, `keys:[…]`)。宫格提示词 `build_grid_prompt`(风格总句 + 各场地点一次 + 逐格 Panel k 景别/出场/画面/构图,总长超 2400 字符按三档收紧逐格文字);参考图 = 9 镜出场人物并集按出场次数取前 4 张 sheet;出图尺寸所有渠道一律 3,686,400 像素当量(2560×1440),切 3×3 后每格约 820×460,不再压缩。
  - **单镜单张**(不变):每镜「出图/重出」→ `POST … {scene, order, provider?, model?}` → `storyboard_sketch.py --scene S01 --order N`,每镜一张(`mode: single`)。CLI 的 `--scene`(不带 `--order`)整场逐镜仍可用,页面不再挂场次级按钮。
  - 每镜结束发 SSE `board_sketch` 事件,页面另有 4s 轮询 `GET …/board/<ep>/sketches` 兜底。
- **风格**:铅笔手绘分镜(提示词见 `modules/storyboard_board.py` `SKETCH_STYLE_PROMPT`);参考图 = 仅出场人物 sheet(≤3),缩到 512px 长边(缓存 `assets/storyboard/<ep>/_refcache/`);**不带场景图**(俯视布局图会误导图像模型,场景靠文字:地点 + 时段 + 场景卡 description,2026-09-11 用户拍板);**不用风格参考图**(2026-09-11 实测带枪械的分镜截图被火山内容审核拒收,用户拍板改为纯文字描述风格)。出图尺寸:火山/BytePlus 按 Seedream 5.x 硬限 ≥3,686,400 像素出(2560×1440 当量),其它渠道 1280×720 当量;成图统一压到 1280 长边。
- **渠道/模型**:页面顶部「🖊 草图模型」单独选(空 = 跟随全局图像渠道),存 `state.json` `image_model_prefs.sketch`(`GET/POST /api/v1/config/image-model/sketch`,旧路径 `/config/sketch-model` 仍可用)。下拉组件 `apps/web/static/image-model-picker.js` 与场景/人物/生物/道具预览页顶部「🎨 图像模型」共用(kind = scenes/characters/creatures/props,同样存 `image_model_prefs.<kind>`);这四类由 genmedia 按输出目录 `assets/concepts/<kind>/` 自动套用(`modules/genmedia.py` `image_pref_env`,显式 `--provider/--model` 优先;全景/分镜背景图的模型支持检查同样按 scenes 的选择),概念图工位无须改动;Key 仍取该渠道在「生成模型」页保存的配置。实现:`genmedia image --provider/--model` = 环境变量 `VIDEOAGENTS_IMAGE_PROVIDER/MODEL`,只作用于当前进程,不改全局设置。模型清单 `apps/web/static/image-models.js` 与「生成模型」页共用。
- **台账** `assets/storyboard/<ep>/index.json`(schema `storyboard_sketches/1.0`):`shots[<S01-01>] = {file, status: queued|running|done|failed, error, prompt, note, provider, model, aspect, refs, started_at, finished_at, updated_at}`;写入经 flock 串行。图片 `assets/storyboard/<ep>/<S01-01>.png`。草图只供看构图/调度,**不进视频参考图**。
- **修改草图**:单镜「✏️ 改草图」→ 浮窗直发 `07-directing/storyboard-sketch`(`agents/07-directing/storyboard-sketch/SOUL.md`),工单首行带 `[草图 epNN/S01-03]` 标记;宿主按在跑/排队工单文本匹配该标记,页面把该格标成「🎨 重绘中」并轮询,工单结束(SSE `run` 事件)自动刷新图片。Agent 把意见翻成一句画面指令作 `--note` 重跑同一 CLI(note 存台账,下次重出仍生效),不改任何分镜文件。
- **其它修改**:故事板整体 / 场次 / 单镜「✏️」一律发总制片(牵涉分镜、镜头表等上下游)。

## 兼容

- 老项目没有 `storyboard.json` 时只显示导演计划与提示;场次缺 `scene_no` 按 `screenplay_ref`/序号兜底。
- 分镜预览页不再下发/显示导演计划(`api_preview_storyboard` 去掉 `directing_plan` 字段)。

## 故事板签字(H3S,2026-09-11)

- `agents/workflow.yaml` 新增人工闸门 **g6s「H3S-故事板确认」**:`depends_on: [p6-storyboard]`,`p6-shots` 改为依赖 g6s——分镜师交付后,总制片按规约建签字卡(`dispatch.py --confirm "【H3S-故事板确认｜项目 <slug>】…" --sign`;漏建时运行时兜底 `ensure_human_gate_approvals` 补建),未签字不派 shot-planning。草图**不是**签字前置,签字卡只报「已出草图 M/N」。
- 页面:标题下方签字条(`_board_gate`):有待答复 H3S 卡 → 「✅ 签字确认 / ⏸ 暂缓」按钮(`POST /api/v1/projects/<p>/board/<ep>/signoff {confirm_id, answer}` → 与控制台同一条签字卡 `api_confirm_answer`,并落 `directing/<ep>/storyboard_signoff.json` 记 storyboard.json 的 mtime);已签 → 「✅ 故事板已签字」;签字后 storyboard.json 又改动 → 「⚠ 签字已过期」(等总制片重新建单)。卡的归属按 project + checkpoint/question 含 `H3S` + gate_id/question 含集号匹配。
- 存量项目的 `runs/dag.json` 没有 g6s 节点,总制片下次展开/对账 DAG 时按 workflow.yaml 补;在此之前页面只显示「故事板签字点 · 等总制片建签字单」。

## 动态样片(animatic,2026-09-11)

- 宿主 CLI `code/animatic.py --project --ep [--no-audio]`:把草图按时长串成 `assets/storyboard/<ep>/animatic.mp4`(+ `animatic.json`),PIL 逐镜合成帧(草图**按画布等比缩放**——单张 1280 长边与宫格切出的约 820×460 小图混用时尺寸不一,`fit_canvas` 既缩小也放大到刚好装进画布再居中,余边留黑;顶部半透明黑带压镜号标签 + 画面内容文字(≤3 行)便于对照;缺草图的镜用占位卡:灰底 + 镜号 + 画面内容文字居中;底部字幕带:台词黄、旁白紫),ffmpeg concat 出 1280×720 / 720×1280(按项目画幅)24fps;有 `assets/audio/narration/<ep>/` 旁白段按 shot_list `narration_anchors` 挂点混入、`assets/audio/voice/<ep>/` 以 shNNN 开头的对白干声挂到该镜起点。零生成费用。
- 时长口径:有 shot_list 定稿用 `duration_s`(草案镜被拆成多镜时求和),否则草案 `duration_hint_s`;`duration_source = final|draft|mixed` 页面角标注明。
- 入口:故事板页「🎞 出动态样片」(标题下折叠块,内嵌播放;缺 N 张草图只提醒不拦,Shift 无关)与视频预览页「🎞 动态样片」板块(白模样片之上,同一按钮)。`POST /api/v1/projects/<p>/board/<ep>/animatic` 起宿主后台线程,SSE `board_animatic` 通知,页面 4s 轮询兜底;`animatic_status()` 按 storyboard.json / shot_list.json / 任一草图比样片新判「已过期」。

