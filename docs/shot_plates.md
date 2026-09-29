# 分镜背景图（shot plates）· 2026-09-09

每个分镜一张「镜首机位看出去的空场景实拍感图」（运动镜头按分档另出镜尾一张），作该分镜组视频生成的参考图。现有 refs 各管一段：白模 camera.mp4 给几何与人物位置、角色 sheet 给形象；背景图补上「这个镜头本身的画面」，让视频模型开镜时的构图、透视、机高和方位直接有图可依。场景俯视图自此只供分镜预览查看、九宫格退役，两者都不再进视频参考图。

实现：`modules/shot_plates.py`；CLI `code/render_shot_plates.py`（出图）、`code/sync_shot_plates.py`（接线与机检）。

## 流程位置（workflow.yaml）

`p6-whitebox`（白模调度，`render_whitebox.py --compile-only` 只编译不导出）→ `g6w` 人工闸门「H3W-白模确认」（用户在分镜预览页审看 3D 白模后签字）→ `p6-whitebox-export`（同一 `07-directing/whitebox-staging` 收导出工单导出 camera.mp4，2026-09-10 起原 whitebox-export 工位已并入，机检 `whitebox_videos_exported` = `render_whitebox.py --verify-export`）→ `p6-shot-plates`（`08-video-gen/shot-plates` 出背景图，机检 `shot_plate_bound`）→ `g6`「H3A-分镜确认」→ `p7-prompt`。`render_shot_plates.py` 会拒绝为未导出白模视频的组出图，保证背景图只在用户确认白模之后生成（出图有费用）。签字后修改白模：重新 `--compile-only` → 重签 g6w → 重导出受影响组 → `render_shot_plates.py --force <shot…>` 重出受影响镜。

## 每镜出几张（运镜分档）

按 `camera.json#movement` 命名 + 白模镜首/镜尾机位几何判定：

| 分档 | 判定 | 张数 |
| --- | --- | --- |
| 静态 | static / fixed / locked / handheld… 或起止机位、视轴、fov 都不变 | 镜首 1 张 |
| 推拉、变焦 | push / pull / dolly / zoom | 镜首 1 张（同一视轴，起止共用） |
| 摇、俯仰 | pan / tilt，或机位不动只转头 | 镜首 1 张 |
| 横移、跟拍 | track / truck / follow / lateral… | 位移 < 机位到主体距离 10%：镜首 1 张；否则镜首 + 镜尾 2 张 |
| 复杂轨迹 | crane / orbit / rise… 或未知命名且有位移 | 镜首 + 镜尾 2 张 |

镜尾图以镜尾白模帧为第一参考图、镜首成图为第二参考图、俯视图第三，seed 与镜首相同，材质与光照跟着镜首走。

## 运行纪律与验收（2026-09-09，前科 dzg6 p6-shot-plates-ep01-s01s02）

Agent 把出图脚本丢到后台就结单，进程随任务结束被杀，9 组 16 镜一张没出。系统侧对策：

- 脚本每出一张打印 `saved:`，并按镜落盘集索引与场景库，中途被杀不丢已出图，重跑按索引/库自动续。
- `--max-new N`：本次最多新出 N 张即返回，退出码 3 表示还有待出；Agent 在前台循环运行直到退出码 0，避免单次命令过长而被诱导丢后台。
- `--status`：机检 `shot_plates_complete`，逐镜给出 ok / partial（缺镜尾）/ missing / stale / file_missing，有问题退出码 1。workflow.yaml 把它列为 p6-shot-plates 的验收机检，orchestrator 只认它，不采信回执自述。
- SOUL 硬纪律：禁止 nohup / & / “后台继续”；结单前必跑 `--status` PASS。
- 2026-09-14 补：工具单次超时**自动**转后台同样算丢后台（成员一结单进程即退出、子进程被杀）。预防：出图命令显式 `timeout=600000` + `--max-new 3` 分批；被转后台就 TaskOutput 阻塞等到退出码。宿主侧：claude 引擎 Bash 默认超时提到 1 小时（`BASH_DEFAULT_TIMEOUT_MS`）；CLI 退出后进程组仍有子进程时宿主代等其排空再收尾，`dispatch.py --status` 附「⚠️成员提前结单」标记（`orphaned_children`），总制片只认机检。

## 母图制（2026-09-14 起：按机位出广角母图，分镜图从母图派生）

**为什么改**：liaozhai3 SCN-0005 反例——40 mm 镜位的全景重投影只剩一面白墙加一张糊掉的案，二次生成把整间屋（屋梁、坐榻、门帘、窗）凭空重造；85 mm 镜位的重投影是一块橘色墙面，出图变成烛台特写。全景 2880 宽对应 360°，56° 水平视角只分到约 450 像素源，焦距越窄参考图越像一块纹理，模型权重接近零，只听场景描述；而「wide framing」这种人物景别词又在推它画广角整屋。28 mm 级（b188 f46）的重投影有窗、门、墙角，模型守住了结构——参考图有结构才有约束力。

- **母图**：每个机位范围只出一张广角图，垂直视场 ≥ 55°（≈23 mm 等效，16:9 下水平 ≈85°；分镜本身更宽时 = 分镜视场 + 4°），朝向/俯仰取范围内待出各镜的平均方向（装不下的同伴逐个剔除），位置取这些镜机位的质心。**机位范围**（2026-09-14 二订，用户按 liaozhai3 SCN-0005 实跑裁定——0.6 m 时 20 镜出 13 张母图，朝向一致相距 1 m 内的三个机位各出一张是浪费）：同场景、同光照方案，水平距 ≤ 2 m 且 ≤ 本镜主体距离的 80%，机高差 ≤ 0.5 m（不再要求同机高档；贴地 <0.5 m 只与贴地合）。派生是纯旋转不补视差，背景板只作视频参考，中等视差可接受。
- **母图尺寸**：长边 2880，面积封顶 4,600,000 px——方舟 Seedream 5.0 pro 单图硬上限 4,624,220 px（2026-09-14 实跑 2880×1620 全部被拒 `image area must be at most 4624220 pixels`），16:9 下落到 2858×1608。库 `assets/concepts/scenes/<sid>/plates/index.json` + `<key>.png` + `<key>.json` + `<key>.pano.jpg` + `<key>.whitebox.jpg`，条目 `master: true`，`key = <lighting_scheme_id>_b<朝向°>_h<机高档>_x<机位x>_z<机位z>_w<母图fov°>`，跨组、跨集共享。
- **分镜直接引用母图整图**（2026-09-14 三订，用户裁定）：不按本镜焦距裁窄——裁窄后画面信息太少，视频模型在生成时会自行发挥不存在的元素；宁可给视频模型一张比镜头更宽的图，由 `Shot plates:` 段说明「这是该机位的广角母图，镜头画面是其中更紧的一块，不得凭空添加图中没有的陈设」。哪些镜能用同一张母图仍按几何判：本镜视锥四角整个落在母图画幅内（`view_fits`），不满足另出母图。集索引每镜记 `view{master_fov, fov, fraction, bearing_delta_deg, pitch_delta_deg, distance_m}`（本镜在母图里的位置，只记录不裁切）。曾实现过的纯旋转单应派生（`view_maps` + `cv2.remap`，`<母图key>.view_b…_p…_f….png`）已删除。
- 母图不要浅景深：背景板本就该全幅清晰，景深由视频模型加；母图提示词与风格串因此剔除浅景深/虚化子句，负面词加 bokeh / shallow depth of field / vignette。
- 同一次运行按 fov 从宽到窄决策，最宽的镜先定母图；本次决定新出的母图也参与后续镜位的派生判断，不重复出图。
- 集索引 `directing/<ep>/shot_plates.json`：每镜 `plates[]{role: start|end, key（母图）, file（母图）, reuse: new|library, view{…}, camera（本镜）}`；机位与当前 `episode.json` 不一致视为过期（机检 WARN）。统计口径：`new` = 新出母图张数，`library` = 引用已有或本次母图的镜数。
- 旧口径（已废止）：按机位指纹容差复用（朝向 ±20°、机位 6 m、fov ±15°）+ 同轴更宽库图中心裁切 `<key>.crop_f<fov>.png`；2026-09-14 前逐镜直出的库条目（有 `pano_ref` 无 `master`）与更早的白模帧直出条目同为 legacy：已在集索引里的镜照旧引用，新决策不再复用，`--status` 列 WARN，`--repano` 整体重出（有费用，用户决定）。

## 出图（2026-09-10 起全景制，见 `docs/scene_panos.md`）

- 先保证场景全景齐备（`modules/scene_panos.py`：按本集机位规划少数锚点 → 白模深度全景 → 图像模型出 2:1 全景，每个光照方案一张；同方案第二个锚点起链式补洞，同锚点第二个方案起保结构重打光）。当前图像模型不支持 2:1 全景 → 退出码 2 `[pano_unsupported]`，一张背景图也不出，Agent 上报用户换模型。
- 只对母图出图（2026-09-14）：refs `[Image 1]` = 场景全景按**母图机位**（广角）用白模几何重投影的透视图 `<key>.pano.jpg`（内容与位置权威，画质与空洞不作数，提示词要求重绘清晰、视场以它为准不外扩、不添画外天花/家具、全幅深焦）；镜尾母图再加 `[Image 2]` 镜首母图。机位事实里的镜头口径按水平视场写（wide-angle / moderately wide / normal-lens / long-lens），不再用人物景别词；画内清单剔除只擦到画幅边缘一线的几何。**白模干净帧与俯视图不再进 refs**（多张背景图各自基于白模帧出图互不一致，是改全景制的直接原因）；白模帧仍渲作 `<key>.whitebox.jpg` 供预览核对。库条目 `pano_ref{anchor_id, scheme, hole_fraction, distance_from_anchor_m}`；无 `pano_ref` 的旧条目为 legacy，不再被新决策复用，`--status` 列 WARN，`--repano` 整体重出（有费用，用户决定）。
- 旧口径（2026-09-09，已废止）：`[Image 1]` 白模干净帧 → `[Image 2]` 场景俯视图；镜尾图在两者之间插镜首成图。
- 提示词：空场景声明 + 组 `time_of_day` + 光照方案 `prompt_fragment_en` + 机位事实（景别、等效焦距、机高档、俯仰、机位落在哪个几何上、罗盘朝向、画左/画右/身后各是什么——由 `layout.json#orientation` 把白模坐标映射到东南西北）+ 白模帧用法 + 俯视图用法 + 画内自左向右清单（白模几何盒采样投影，按基名/地标归并）+ **画外不可见清单**（在画幅外/身后的地标，明令不画——实测没有这句时场景描述会把身后的大门院墙带进画面）+ 场景描述（architecture.json 的 form / arch_style / era_region / scale / materials / details，声明只作材质与年代参考）+ 禁人/禁网格/禁俯视 + `style_fragment_en`。negative = `negative_prompt_en` + architecture.negative + 人物/网格/俯视词。**空场景图去人物用语(2026-09-20)**:`style_fragment_en` 过 `plate_style()` 剔除描述人物的分句(subject / hair / skin / 服装面料),`negative_prompt_en` 过 `plate_negative()` 剔除人脸/人物/服装类条目及「须有前景/比例参照物」类条目——方舟等渠道没有独立负面通道,negative 以「避免出现:…」并进正文,参考图几乎全空(仰拍天空/大片水雾)时这些词会被当内容画出来(实证 fengshen3 SCN-0110 母图画出白须老人);场景视角图 `scene_plates.py` / `scene_pair_plates.py` 同用这两个函数。可选 `--sun <罗盘>` 写太阳相对机位方向。
- 分辨率：母图长边 2880、派生分镜图长边 1920，按项目画幅；渠道 = 场景预览页顶栏「🎨 图像模型」的选择，空则控制台默认图像模型（`modules.genmedia.generate_image` 按输出目录自动套用，不写死）；场景全景另按同页「🌐 全景模型」，两者独立（2026-09-12）。Seedream 5.0 pro 口径：1920×1080 落 0.3 元档 + 参考图首张免费、之后 0.02 元/张。

## 背景图模式（2026-09-22：全景图 | 世界模型；2026-09-25 加九宫格）

分镜背景图的参考来源分三种模式，**全局设置**在输出设置（新建向导与项目设置「输出设置」均有，仅白模开启时显示）`output.plate_mode`，**场景级覆盖**在场景预览页「🎦 分镜背景图」板块标题后的「按场景选择」（跟随全局 / 全景图 / 世界模型 / 九宫格，存库 `assets/concepts/scenes/<sid>/plates/index.json#mode`，`POST /projects/<p>/scenes/<sid>/plates/plate-mode`）。生效值 = 场景级非 inherit 时取场景级，否则取项目级；`modules/shot_plates.py#effective_plate_mode`。

- **全景图（pano；2026-09-26 起界面隐藏、后端仍支持）**：即上节全景制——按白模机位自动规划锚点出 2:1 全景，每张母图把全景按母图机位重投影成 `<key>.pano.jpg` 作 `[Image 1]` 二次生成。
- **世界模型（world；2026-09-26 起界面隐藏、后端仍支持）**：用户在场景预览页「🌐 全景图」板块**自选锚点**创建全景图 → 在「🌍 世界模型」板块基于该全景生成世界模型（World Labs Marble，1500 credits/次，`docs/worldlabs_world.md`）→ `render_shot_plates.py` 在 world 里按母图机位（position/target/fov，白模坐标）用无头 Chromium + Spark 截图 `<key>.world.jpg`（`modules/worldlabs.py#render_world_views`，渲染页 `apps/web/static/world-view-export.html`；先试 GPU 无头，不可用退回 swiftshader，`VIDEOAGENTS_WORLD_GPU=0` 强制 swiftshader）作 `[Image 1]` 二次生成。提示词口径改为「world 内渲染截图，可能糊/噪，内容与位置权威」；其余（母图制、复用、接线、机检）与全景制完全一致，两种模式的母图同库同键。**场景没有世界模型时整条链停下**：`WorldMissing` → CLI 退出码 4 并打印 `[world_missing]`（列出场景），一张都不出；由用户到场景预览页生成世界模型（计费）或把该场景/项目改回全景图；Agent 不得自行跑 `worldlabs_world.py`。`--dry-run` 不要求已有 world，只列决策并标注模式。
- 世界模型模式**不跑** `ensure_scene_panos`（锚点规划/自动出全景）：全景由用户手选锚点在预览页出；光照方案只影响提示词，world 只有一个（按生成时选的全景方案）。
- 库条目 `pano_ref.kind = pano | world`（world 条目另记 `world_id` / 来源锚点 / 方案 / splats 精度），`plate_mode` 记出图时的模式；`is_legacy` 判定不分模式（有 `pano_ref` 且 `master` 即非 legacy）。场景预览页背景图 caption 显示「世界模型(全景 A1)」；分镜预览页不区分。
- **九宫格（grid，2026-09-25，用户方案；2026-09-26 起为默认且界面唯一可选项——输出设置两处下拉与场景预览页「按场景选择」只露「九宫格」，pano/world 为隐藏项，存量项目/场景的旧值仍能显示与生效）**：不出全景、不用世界模型、不出母图。① 每场景每光照方案按 `layout.json#views`（tile 1..9：`camera_from`/`looking_at`/`size`/`angle`/`desc_en`）以**俯视图** `layout_top.png` 为 `[Image 1]`、宿主生成的纯版式模板 `<方案>_grid9.template.jpg` 为 `[Image 2]` 出一张 3×3 宫格 `<方案>_grid9.png`（`ensure_grid9`）；② 按版式拆成 9 张背景图 `<方案>_grid9_tN.png`（四边内缩 1.5% 去白线，缩放到分镜图规格 `plate_size`）入库（文件名 .png、内容 JPEG q92，与模型直出库图同一约定；2026-09-26 前拆格误存 PNG 单格 1.8 MB，存量已就地转），条目 `grid9: true`、`master: false`、`pano_ref = {kind: 'grid9', sheet, template, tile, cols, rows, scheme, view{camera_from, looking_at, size, angle, desc}, box}`，`camera` 为该格的**合成机位事实**（地标 xy → 白模坐标同 `inventory()` 映射；机高 low 0.4 / eye 1.6 / high_oblique ≥5 m 并按俯角 30° 抬高；视场 wide 55° / medium 40° / close 28° / detail 20°）；③ 每镜按白模机位事实从九格里自动选一格（`pick_grid9_tile`：score = 朝向差/30° + 机位水平距/5 m + 机高档差×0.5 + 俯仰差/20° + 格视场比本镜窄 5° 以上罚 0.5，取最小），集索引 `plates[].view.grid9` 记各分量与所选格号，`reuse = grid9`，`whitebox_frame` 为本镜白模帧 `directing/<ep>/whitebox/plate_frames/<shot>_<role>.whitebox.jpg`（预览核对用，不进 refs）。
  - 九格条目不走 `find_master` / 单应派生；`is_legacy` 对 `grid9` 条目为否；同方案的宫格已出且 9 张齐全时直接复用（重出整张宫格须 `--no-grid-fallback --force`；补图默认开着时 `--force` 只重出目标镜的补图，不会覆盖同方案 9 格）。`layout.json` 缺 views（tile 1..9）或 `camera_from`/`looking_at` 不是地标 id → `Grid9LayoutError`，CLI 退出码 1，先补场景布局包。
  - 提示词 `build_grid9_prompt`：整图声明（3×3、白线、与 [Image 2] 版式一致）+ 俯视图用法与地标在图上的位置词 + 逐格「景别 from 地标 looking toward 地标 + 机位高度词 + 罗盘朝向 + desc_en」+ 禁人/禁字/禁并格/禁俯视 + 风格串；负面词 `NEGATIVE_GRID`。宫格整图 + 同名 `.json` 台账只作记录，不进视频 refs；文件名不用退役的 `grid_9views*` 前缀（三处机检黑名单）。
  - 评审时提过的替代方案（格位取本集母图机位、白模联系表作参考、拆格后仍按母图制派生）在 fengshen3 SCN-0121 实测：三张宫格之间不一致、白模帧空的格子雷同，用户拍板回到本方案。代价：每格原生约 910×510 放大到 1920 宽；九格是语义机位，与分镜机位只是"最近"而非同一机位，透视差靠视频模型吸收。
  - **九宫格自动补图（2026-09-26，九宫格模式默认流程；`--no-grid-fallback` 关闭）**：「先从九格里选，没有合适的再单独出图」。最近格的选格分量任一超限（`GRID9_FIT`：朝向差 > 30° / 俯仰差 > 20° / 机位水平距 > 6 m / 机高档差 ≥ 2）即视为不合适，改以**俯视图 `[Image 1]` + 整张九宫格 `[Image 2]`**（镜尾另加镜首成图 `[Image 3]`、同 seed）为参考，按本镜白模机位事实单独出一张分镜图规格的背景图（`build_grid9_fallback_prompt`：机位句同母图口径 + 俯视图只作布局参考 + 九宫格是同一地点材质/陈设/光线的权威参考且逐格列出机位、点名最近的一格「从它出发再把相机挪到本机位」+ 画内清单/画外清单 + 单幅禁宫格）。库条目 `grid9_fallback: true`、`master: false`、`pano_ref = {kind: 'grid9_fallback', sheet, scheme, nearest_tile, reasons}`，key = `plate_key + _g9fb`；相近机位（`GRID9_FB_TOLERANCE`：朝向 ±15° / 水平距 ≤1 m / 机高 ±0.3 m / 俯仰 ±10° / 视场 ±10°）复用同一张补图（`find_grid9_fallback`，含本次待出的 pending）。集索引 `reuse = grid9_fallback`、`view.grid9` 仍记被拒的最近格、`view.fallback = {reasons, key, source: new|library}`；开启时索引里仍用不合适格子的镜会重新决策（不动已出的宫格，不需要 `--force`；补图开着时 `--force` 只重出目标镜的补图，要重出宫格本身须 `--no-grid-fallback --force`）；`--max-new` 同样限补图张数。结果比较页 `code/grid9_compare.py --project <slug> --ep <ep> --scene <sid>` → `qa/grid9_compare/<sid>.html`（白模帧 / 所选格 + 打分 / 补图三列并排，控制台文件路由 `/api/v1/projects/<slug>/artifacts/qa/grid9_compare/<sid>.html` 可直接打开）。alices2 SCN-long-hall 实测（67 条需求 22 合适 / 45 补图，31 张新出）见该页，用户据此拍板作为默认流程；已知局限：极端俯仰（仰 80° / 俯 −88°）模型只做到明显低/高角度。
- 预览数据：`/previews/scenes` 顶层 `plate_mode`（项目级），`scenes[].plate_mode{mode, effective, has_world}`；世界模型模式且尚无 world 的场景板块内有黄字提示 + 「前往世界模型」。世界模型板块自 2026-09-22 起挂在「分镜背景图」板块之上（全景图 → 世界模型 → 分镜背景图，与流程顺序一致）。

## 手工截取背景图(2026-09-26:全景图 / 世界模型视窗「💾 背景图」)

全景图与世界模型自 2026-09-26 起不再作为自动出图模式(界面隐藏),改为**手工补背景图**的来源:

- 场景预览页「🌐 全景图」板块点正式全景进 360° 视窗(`apps/web/static/pano-viewer.js`,`openPano360(src, meta)`,meta 带 project/sid/anchor_id/scheme/锚点 position/yaw_deg/画幅),右上角「💾 背景图」把当前画面按项目画幅(长边 1920)离屏渲一帧,连同换算出的白模机位 `camera{position, target, fov_v_deg}` `POST /projects/<p>/scenes/<sid>/plates/manual`。方向换算:全景图中心列 = 锚点 yaw(与 `modules/scene_panos.py` 投影约定一致,`fwd = (−sin yaw, 0, −cos yaw)`),视窗 `lon` 与贴图 u 的关系 `u = lon/360`(LON0 = 180 正对中心列)→ 世界 yaw = yaw0 − (lon − 180);`lat` 为仰角。虚线框 = 将保存的画幅范围(屏幕比 ≥ 画幅比时同垂直视场左右裁,否则以屏幕水平视场为准上下裁)。
- 「🌍 世界模型」板块的 Spark 视窗(`world-viewer.js`)默认**不显示白模线框、不画全景机位红球**,工具栏「💾 背景图」同样按画幅离屏渲一帧,相机方向直接取 three 相机(已在白模坐标系),`view` 记 yaw(罗盘,北 0 · 东 90)/pitch/fov/精度。
- 服务端 `_scene_plate_manual`(services/runtime/core.py):校验宽高比与项目画幅一致(不一致 400)、按 `camera_facts` 算机位事实(朝向/机高/俯仰/视场/罗盘/standing),存 `plates/<plate_key>_hand<时间戳>.png`(JPEG 内容)+ 同名 `.json`,库条目 `master: true`、`manual: true`、`pano_ref = {kind: pano_manual | world_manual, anchor_id, scheme, view, source_file}`、`plate_mode: manual`。`is_legacy` 为否;分镜预览页「换图」候选列表自动包含,母图制 `find_master` 也能复用。场景预览页 caption 显示「手工截取(全景 A3)」/「手工截取(世界模型)」。
- 保存后页面原位重载(`reloadKeep`)以刷新「分镜背景图」板块;世界模型视窗重挂后状态行保留保存提示 60 s。隔离实例(8730/8740)无头实测:全景 A1/A3、世界模型各保存成功,候选列表可见。

## 按修改意见重出一张(2026-09-26:分镜预览「✏️ 修改」→ 修改师)

分镜预览页每张背景图的「✏️ 修改」发给修改师(`kind=shot_plate`,代行 `08-video-gen/shot-plates`;修改单头 `files:` 带当前图路径,定位文本带镜号/起点或终点/库 key 与修订命令)。修改师**不跑** `render_shot_plates.py --force`(那是按机位重新决策、从全景/九宫格/世界模型再出一张,用户的意见进不去),而是跑宿主 CLI:

```sh
python code/revise_shot_plate.py --project <slug> --ep <ep> --shot <shNNN> [--role start|end] --change "<英文修改要求>" [--note "<用户原话>"] [--dry-run] [--seed N]
```

- **原图不动**:不管这张图来自九宫格拆格(`grid9`)、九宫格补图(`grid9_fallback`)、全景截图 / 世界模型截图(`pano_manual` / `world_manual`)、母图(`pano` / `world`)还是旧法图,库里原条目与文件原样保留,引用同一张原图的其它镜不受影响;「🔁 换图」随时能换回。
- **以当前图为参考新出**:`[Image 1]` = 本镜当前这张背景图(`modules/shot_plates.py#build_revision_prompt`:机位/构图/地平线/布局/陈设/材质/天气/光向/调色的权威参考,只改「Requested change」点名的内容,不移机位、不扩/缩视场、不增删未提及的东西),再附本镜机位句(集索引 `camera`)、光照方案片段、组 `time_of_day`、禁人/禁字/禁宫格、风格串;`--note` 的用户原话逐字附在末尾。尺寸随原图(面积超母图上限时落到母图规格),渠道同母图/补图(场景预览页「🎨 图像模型」,空则全局)。
- **入库**:`plates/<原 key 去掉已有 _revN>_rev<N>.png`(+ 同名 `.json`),条目 `revised: true`、`master: false`、`pano_ref = {kind: 'revision', source_key, source_file, source_kind, change, note}`、`plate_mode: revision`;链式修改 `X_rev1 → X_rev2`(N 取库里同根最大修订号 + 1)。`is_legacy` 对 revised 条目为否(`--status` 不报 legacy WARN);不参与 `find_master` 派生(它只服务这一镜)。
- **只替换本镜该角色**:集索引 `plates[]` 该条目改为 `key/file` 指向新图、`reuse: revised`、`view/crop: null`、`revised_from = {key, file, reuse}`、`revised_at`、`revision = {change, note}`;机位指纹 `camera` 不动,非 `--force` 的 `render_shot_plates` 按「记录仍新鲜」保留(同换图)。随后 `sync_group(write=True)` 把新图接进本组 prompt refs / `Shot plates:` 段与逐镜激活句。
- 预览:分镜页显示「起点 · 按修改意见重出」;场景页与换图弹窗 caption「按修改意见重出(基于 <原 key>)」。`--dry-run` 只打印提示词与参考图。一条修改意见出一张,不赛马;用户不满意再发一条修改单(头里带上次修改记录)再出一张。

## 提示词的几条防偏规则（2026-09-09，dzg6 grp003 反例）

grp003 的 sh005 背景图（机位在路上、朝南南西横看绿化带，54 mm）被画成了朝西沿马路望向夕阳的纵深街景，视频模型因此把 sh006 的图当成整组唯一背景，两镜同景。原因是提示词里「画左/画右」引用了布局图 orientation 的四边文字（「马路向路口延伸、夕阳压在这一端」），加上路端方向地标出现在画幅边缘时仍写成「马路向远处延伸」。现在：

- 画左/画右只写罗盘方位，不再附四边说明；四边说明只保留「看向」的那一面与「身后」。
- 细长地面物（马路/人行道/绿化带/围墙，长边 ≥8 m 且长宽比 ≥3）按长轴与视轴夹角写明「横贯画面、不向远处延伸」（>60°）/「向画面深处延伸」（<30°）/「斜穿画面」。
- 路端方向地标只在落在画幅中央区（|ndc_x| ≤ 0.6）时才写「马路向 X 延伸」。
- 「画外不可见」清单只列点状地标（门/建筑/灯杆/道具），绿化带、围墙这类面状地标不列，避免与画内清单自相矛盾。
- 同组第二张起的镜首图以本组最先落定的背景图为第二参考图（「另一机位拍的同一处地方」），同组各镜读作一个地方；`Shot plates:` 段加了「每镜只用自己的图，不把某镜的图带进另一镜」。

## 接线（shot_plate_bound）

`code/sync_shot_plates.py --project <slug> --ep <ep> [grp…] --write`：剔除组 prompt refs 里的场景俯视图 / 九宫格 / 退役动线图与残留 `Spatial layout:` / `Map usage:` / `framed like tile` 句，把本组各镜背景图按镜序插在角色/生物 sheet 之后（两张图的镜 start、end 都挂，**不走首尾帧模式**——多镜组里首尾帧与参考图互斥），重排 `[Image N]`/`@Image N`（引用已移除素材的 token 删除并 WARN），在 `Shot 1:` 前写固定段：

`Shot plates: [Image N] is the empty background plate of Shot k, photographed from that shot's exact camera with nobody in it — match its framing, perspective, camera height, set dressing and lighting, then add the characters; [Image M] is the end plate of Shot k (where the camera move ends); the shot travels from the start plate's framing to this framing. Background plates are set references only: never freeze the shot on them, keep the characters and motion described in each Shot.`

**机器句语言（2026-09-23）**：2.5 口径的 `【场景】` 段、逐镜「场景激活：」句及其后的画内/画外/背景/构图层次句随界面语言——中文界面出中文，其余界面一律英文（`【Scene】Scene A (camera position of Shot 1; empty background plate) reference [Image 2], …` / `Scene activation: use Scene A ([Image 2]); do not use Scene B ([Image 3]). In frame left to right: … Composition layers: …`）。语言按 `state.json` 的 `ui_lang`（回落 genconfig `ui_language`，缺省中文）读取，`modules/shot_plates.py#ui_lang_is_zh`，测试可用环境变量 `VIDEOAGENTS_UI_LANG` 覆盖；`_ACT_RE` 中英两套机器句都剔除，切换界面语言后 `--write` 可干净重写；机检 `shot_plate_bound` 两套都认。「Shot N｜标题」段头的标题收尾同时认中文句号与英文句点。2.0 与 H3 口径本就是英文，不受影响。

**Seedance 2.5 变体（2026-09-09）**：组生效模型为 2.5（组级覆盖、项目提示词技能快照或 genmedia 当前模型任一判定）时，`--write` 改写为 2.5 官方结构：Shot plates 段变为 `【场景】场景A（Shot 1 的机位，空场景 background plate）参考 [Image N]，只采用空间布局、建筑、材质和光线…`，并在每个 Shot 段头（`Shot k:` 或 `Shot k｜标题。`）插入机器持有的「场景激活：使用场景A（[Image N]）；不采用场景B（[Image M]）。」句；Agent 自己写的「使用：/不采用：」清单不动。机检同样分支。实测 dzg6 grp010：2.0 式绑定段三次都被模型合成一个环境，按此结构重写后两镜各用各图。配套机检 `sd25_prompt_structure`（`code/prompt_skill_check.py`）要求正文其余部分也按 2.5 结构写。

**逐镜画内/画外/背景/构图层次机器句（2026-09-14，liaozhai3 S04-09 反例）**：视频提示词里此前没有一句约束背景——composition.json 的 `layers.bg`「北后檐粉墙落进近黑」没注入，plate 阶段算好的「画外不可见」清单也没进视频 prompt，模型把画外的东窗搬到案后的北粉墙上。plate 阶段实测「画外不可见」清单能拦住身后的东西被画进来，视频阶段同理。现 `--write` 按本镜真实机位视锥对白模几何算（`shot_view_extras`，复用 `inventory`）：画内自左向右（墙段归并成布局四边说明里的墙名，如「北后檐粉墙」）、画外不入画（只列点状实体地标：门/窗/家具/道具/光源；没有白模实体的 feature 类如「室中空地」和整个在画幅上方的屋梁不列，避免与构图文字「上缘是暗梁区」矛盾）、本镜背景（视轴正对的墙，附四边说明全文）、`composition.json#layers` fg/mg/bg 逐字（句号换分号）。2.5 口径接在「场景激活：」句之后：`本镜画内自左向右：…。画外不入画（不得画进本镜）：…。本镜背景：…。构图层次：前景…；中景…；背景…。`；2.0 口径写进 `Shot plates:` 段各图说明句（`In frame left to right / Not in frame / Backdrop`），H3 接在 `Plate anchor:` 句后。【场景】槽位句改为写明本镜在母图里的位置（`view.fraction` 与带符号朝向/俯仰偏差：「约占母图宽高的 6 成，中心居中，以案头、北后檐粉墙为中心」；≥ 85% 写「取景与它基本一致」）并加「尤其不得添加图中没有的窗户、门洞与家具」；夜间方案（`scene_panos.openings_for`：方案 time_of_day 含夜/night 且主光不是窗/日光/月光来光，洞口按白模墙段 `-lintel/-sill` 自动列名）在【场景】段末附「所有窗户与门口都是不透光的暗面」句。全部机器持有：`_ACT_RE` 按前缀整段剔除后重写，幂等；机检 `shot_plate_bound` 对有画内清单的镜要求 Shot 段含「本镜画内」句（缺 = 违规，跑 `--write`），缺「构图层次：」只 WARN。集索引 `view.bearing_delta_deg` 自此带符号（正 = 本镜视轴在母图右侧）。同日曾一并试过全景/母图两处改动（全景洞口暗面规则与透光机检；母图视场自适应、防退后提示词与保真机检），实测效果不佳均已回退，母图制仍按上节口径。

**MiniMax H3 变体（2026-09-09）**：组生效模型为 H3（模型 id 或工作流名同时含 minimax 与 h3，或技能为 h3-pe）时，Shot plates 段按 Ref2VA 官方 §2.2 写成图片构图锚：`<Picture N> ([Image N]) is the empty background plate and composition anchor of [Shot k] … reference generation for architecture, set dressing, lighting and camera space only`，并在每个 Shot 段头插入 `Plate anchor: this shot's set and framing correspond to <Picture N> ([Image N]). <Picture M> ([Image M]) not used in this shot.`；`[Image N]` 并列保留供项目机检。配套机检 `h3_prompt_structure`。H3 变体尚未在真实项目上出片验证。

不带 `--write` 为机检：refs 不含俯视图/九宫格（违规）、每张背景图在 refs 且有说明句（违规）、背景图机位过期（WARN）、尚无背景图的镜（WARN，`--strict` 违规）。`render_shot_plates.py` 出图后自动 `--write`；prompt 工位产出 prompt 后再跑一次。原 prompt 首次备份到 `directing/<ep>/whitebox/prompt_backups/`。

## 预览

- 分镜预览页（`/preview/storyboard`）每个 shNNN 模块底部「🖼 分镜背景图」：关联的全部背景图缩略（宽 100 px，起点/终点 · 新出/复用库图/裁自宽景），点击放大。数据来自 `api_preview_storyboard` 的 `shots[].plates`。
- 「🔁 换图」（2026-09-23，每张背景图「✏️ 修改」按钮之前）：弹窗列出本镜所在场景背景图库的全部图（母图/旧法图，含朝向/机高/焦距/时段/来源全景与被哪些镜引用，当前这张标 ✓），点选一张确认后替换本镜该角色（起点/终点）的背景图：`GET /projects/{p}/storyboard/{ep}/plates/{shot}/candidates` 列图，`POST …/plates/{shot}/swap {role, key}` 改写 `directing/<ep>/shot_plates.json` 该条目（`reuse: manual`，记 `swapped_from` / `swapped_at`，机位指纹 `camera` 不动、`view` 按新母图重算）并立即 `sync_group(write=True)` 把新图接进本组 prompt 的 refs / `Shot plates:` 段与逐镜激活句。非 `--force` 重出按「记录仍新鲜」保留手选，`--force` 才按机位重新决策；分镜预览显示为「起点 · 手动换图」。
- 场景预览页（`/preview/scenes`）「🖼 分镜背景图」板块：按库列出，标注朝向/机高/焦距/时段与被哪些集/镜引用；`plates/` 子目录不再混进概念图库。

## 实测记录（dzg6 ep01 grp002，doubao-seedream-5-0-pro，1920×1080）

- sh004（跟拍 8 m）：镜首、镜尾两张，镜尾以镜首成图为第二参考图，同一地点同一材质，机位前移；方向、画左画右与白模一致。
- sh003（贴地朝南 CU）：首版被场景描述带偏（画出了身后的大门院墙），加入「画外不可见」清单后重出（见库 `<key>.json` 提示词）。贴地机位模型仍偏高（约 1 m）是已知局限。
- 2026-09-08 原型对照（2560×1440，已归档在 `assets/shot_plates/`）：白模干净帧是决定性输入；整张九宫格会被整格复制盖过白模，这是九宫格退役的直接依据。
