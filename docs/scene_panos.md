# 场景全景锚点（scene panos）· 2026-09-10

分镜背景图自此一律**由场景全景二次生成**：先在白模场景内少数「锚点」出 360° 等距柱状全景（每个光照方案一张），再把全景按每个**机位的广角母图**（2026-09-14 母图制，`docs/shot_plates.md`；此前按每个分镜机位）用白模几何重投影成透视图，交图像模型重绘成母图，各分镜图从母图按朝向/焦距单应派生——窄焦距直接重投影时参考图只剩一块纹理，模型会把整间屋重造。之前每镜各自按白模帧 + 俯视图直出，多张背景图之间同一处地方长得不一样；全景是同一场景所有背景图的共同来源，一致性由此而来。

实现：`modules/scene_panos.py`；CLI `code/render_scene_panos.py`（预跑/改锚点/状态）；`code/render_shot_plates.py` 出背景图前自动保证全景齐备（`docs/shot_plates.md`）。白模全景渲染页 `apps/web/static/whitebox-pano-export.html`（无头 Chromium）。

## 数据（场景级资产，跨组跨集复用）

`assets/concepts/scenes/<sid>/panos/`

| 文件 | 内容 |
| --- | --- |
| `index.json` | `scene_panos.v1`：`anchors[]{anchor_id, position[x,y,z], yaw_deg, source auto|auto-self|manual, locked, serves[](ep/镜:角色), panos{<scheme>: {file, mode fresh|chain|relight, parent, channel, seed, size}}}`、`indoor`、`planned_at`、`blocked`（图像模型不支持全景时写入） |
| `<A>/whitebox_pano.jpg` | 白模彩色全景（出全景的第一参考图） |
| `<A>/depth_pano.npy` / `.json` | 径向深度全景（米）+ 锚点相机记录（重投影用） |
| `<A>/<scheme>.png` / `.json` | 该光照方案的真实全景（2880×1440，严格 2:1）+ 提示词/参考图/渠道 |
| `<A>/<scheme>.chain_<P>.jpg` | 父锚点全景重投影到本锚点球面的参考图（链式补洞用） |

预览：场景预览页「🌐 全景图」板块（3D 白模之下）——俯视图上标出每个锚点中心，逐锚点列各方案全景与白模全景，标注中心坐标（白模米制 x/z）、高度、服务的机位、出图模式；图像模型不支持全景时红条提示换模型。✏️ 修改按钮直发 `08-video-gen/shot-plates`。

## 三个设计问题

**1. 不为每个分镜位置出全景（成本）**：全景数量由**机位覆盖**决定。锚点 a 能服务机位 c 须同时满足：水平距离 ≤ max(3 m, 50% × 机位到主体距离) 且 ≤ 10 m；锚点→机位、锚点→主体两条线段不穿过白模实体。候选点 = 场景 0.5 m 网格上不在实体内、离高实体 ≥ 1.2 m 的点 + 各机位自身位置；贪心集合覆盖，每轮取服务最多未覆盖机位的候选，直到全覆盖。锚点高度 = max(1.6 m, 机高中位数)（高于座椅背/柜台，遮挡少；重投影按机位射线求交，锚点高度不必等于机高）。增量：新机位先看已有锚点能否服务，不能才补锚点。机位落在白模地面之外时锚点夹回地面边缘内 0.5 m（白模外半球没有几何，全景只能瞎补；根治是把场景白模范围建到覆盖全部机位）。dzg6 SCN-0002（机场候机厅）ep01 54 个机位 → 5 个锚点。

**2. 多张全景互相一致**：同一方案第二个锚点起走**链式补洞**——把已成全景用白模几何重投影到新锚点球面（`reproject_to_anchor`），作第二参考图，提示词声明「同一地点换位置拍的，所示物体全部保持、只补空白区」；先出服务机位最多的锚点（母全景）。提示词还按白模几何**逐物写清**方位（罗盘 + 画面横向百分比）、距离、长轴走向与朝向（有 `<id>_back` 靠背块的座椅自动推出「靠背在 X 侧、面朝 Y」）——白模只是方块，不写这些模型会在每个锚点各自猜（dzg6 SCN-0002 首版 A1/A2 座椅方向相反，加清单后一致）。**不要**再把母全景原图挂作参考：实测（A4/A5）模型会整张照抄原图、无视本锚点几何（`CHAIN_INCLUDE_SOURCE=False`）。每张链式全景记 `parent.consistency`（与重投影参考图的像素相似度，仅 WARN；抓不住座椅朝向这类语义差异，最终靠预览页人眼核对，`--redo <A>` 重出）。**时段变化**（白天/夜晚 = 不同光照方案）：同一锚点已有其它方案的全景时走**保结构重打光**——以已成全景为第一参考图、白模全景第二，提示词要求像素对齐只改光照；不重画内容。模式记在 `panos[scheme].mode`。

**3. 全景中心自动还是手动**：默认自动（上述规划），预览页显示每个锚点在俯视图中的坐标。手动：`code/render_scene_panos.py --scene <sid> --anchor x,z[,yaw] --force`（锚点锁定，规划时保留，只为未覆盖机位补锚点）或直接改 `index.json` 后 `--replan --force`。

## 重投影（backward warp）

目标视图（分镜机位透视图 / 新锚点球面）每个像素：对白模几何（objects 盒 + 地板外扩 20 m + 室内天花板）射线求交得 3D 点 → 回到源全景按方向取色 → 用源深度全景做遮挡判定（源在该方向的深度明显小于点距 = 被遮挡 → 空洞）；目标射线无几何时按纯方向取色，但源在该方向有几何即视为遮挡。分镜图空洞 inpaint 后作 `[Image 1]`；空洞 > 50% 换下一锚点，都不行则在该机位加锚点出全景。实测 dzg6 SCN-0002：机位离锚点 0.1–2 m 空洞 0.3–4%，锚点间 5.4 m 链式重投影空洞 8%。

## 投影保障（2026-09-20）

开阔外景的白模全景只有几个小盒子贴着地平线，看上去像一张普通广角构图，图像模型会交出「2:1 的广角风景照」（fengshen3 SCN-0110 A1–A12：左右缘接不上、天顶/天底不成色带、底部是清晰前景）。三道保障：

1. **外景白模投影引导线**：`render_whitebox_pano` 对室外场景调 `draw_projection_guides`，在无几何的天空（虚拟平面高 25 m）与地面像素上画世界直角网格（镜头居格心，无线穿过天顶/天底）+ 地平线四向刻度（无文字）；直线弯成向两极汇聚的曲线是等距柱状最强的视觉签名。只改 `whitebox_pano.jpg`，不动深度全景。`depth_pano.json#guides` 记版本；存量外景白模在**出全景前**本机重渲（不作废已有全景）。室内不画。
2. **提示词**：外景加 `EXTERIOR_PROJECTION_RULES`（无单一视向、地平线贯穿整幅、身后在两缘且接得上、天底拉伸不得是清晰前景、日/月只占一个方位）与 `GUIDES_REF_RULE`（引导线只示意曲率、不得画出）；所有全景末尾加 `PANO_PROJECTION_TAIL` 复述投影；风格段经 `pano_style` 只留材质/色调/颗粒分句，剔除 subject / layers of depth / backlight / god rays 等单镜头构图用语。
3. **投影机检** `projection_check`：主判据天底横向细节比（底部 5% 行 ÷ 中段，合格 0.14–0.55、广角 0.85–2.6，> 0.7 FAIL）；辅判据极区行方差 + 左右缘接缝比同时超限 FAIL；仅接缝比 > 3 为 WARN（合格全景也常见）。FAIL → 成图改名 `<scheme>.rejected-projection-<时间>.png`、不入索引、抛 `PanoProjectionError`，**本批立即停下**（链式补洞会把错误投影传给后续锚点），CLI 打印 `[pano_projection_fail]` 退出码 3（`render_shot_plates.py` 同）。宿主不自动重出；Agent 用 `--only <锚点>` 重出，次数计入用户设定的重跑次数，用尽原文上报用户。结果记入成图 sidecar `projection_check`。

## 锚点可见性（2026-09-20）

一个 SCN 同时含室内外时（fengshen3 SCN-0046：云台 + 洞内主室 + 侧室），按整场景写的内容会把墙外的东西全喂给模型——洞内锚点 A1 被画成「洞府外观定场图」。`anchor_view(scene, anchor, indoor)` 在锚点处对白模几何逐射线求最近命中（720×360；贴地薄块按地板命中点落在占地内归还），得每物体可见像素 / 无遮挡应占像素、前右后左四向「看得出去」占比；提示词只写看得见的：

1. **物体清单**：可见像素 < 10 的物体不写；可见比例 < 35% 的注明「mostly hidden… never bring it into full view」。
2. **四向文字**：俯视图四边说明只在该向看得出去时成立。该向被墙挡死（地平线带看得出去 < 10%）**且**墙外还有一大截地图（墙距 < 到图边距离的 60%）→ 改写为 `sector_sentence`：该向看得清的具名地标 + 「近处的实墙」，只从开口瞥见的另说。单间房（墙即图边）照旧。
3. **封闭室内锚点**（室内且四向全挡死，`view['enclosed']`）：不下发 `note_en` 全域说明；材质 / 光照段经 `indoor_clauses` 剔除讲墙外的分句（看不见的地标用词 + 室外词命中数 > 看得清的地标用词命中数；光照段连逗号也切）；**不挂整场景俯视图**；追加 `ENCLOSED_RULE`（镜头在室内、四周与头顶是实体，不得画建筑外观 / 天空 / 云海，开口处除外）。
4. **白模一致性机检** `conformity_check`：白模深度全景的轮廓线在成图 3 px 内找得到边缘的比例 s0，对横向错位基线 null 的 z 分。s0 < 0.30 且 z < 1 → FAIL（改名 `.rejected-conformity-*`、整批停、退出码 3，同投影机检）；z < 2 → WARN；成图边缘过密（null ≥ 0.75，如机场大厅）不判。标定：liaozhai3 SCN-0005 0.88/0.31、SCN-0140 A1 0.86/0.53 PASS；SCN-0046 A1 0.13/0.24 FAIL。

`is_indoor` 补判：子场景只有 whitebox.json、没有 lighting / architecture 时再看 `bible/scenes/index.json` 的 `int_ext` 与场景名「内景 / 室内」（fengshen3 SCN-0140「…前厅正堂(内景)」原被判成室外 → 不补顶、厅堂上方画成天空）。室内外判定变了的锚点在出全景前本机重渲白模，不作废已有全景。

## 图像模型能力

全景要求任意宽高（2880×1440）。`pano_support(cfg)`：火山/BytePlus Seedream、ComfyUI、Agentics、Fal 的 Seedream/FLUX.2/Qwen 家族可出；Fal 的 Nano Banana/GPT Image/Kontext（固定比例枚举）、MiniMax、OpenRouter 不可。不可、或返回图宽高比偏离 2:1 超过 3% 时：`index.json#blocked` 写入原因，CLI 打印 `[pano_unsupported]` 退出码 2，**一张背景图也不出**；预览页红条提示。Agent 须原文上报请用户换图像模型，不得自行换模型或绕过。

全景用哪个图像模型（2026-09-12）：场景预览页顶栏图像模型分两块——「🎨 图像模型」（kind `scenes`，概念图 / 分镜背景图 / 布局图 / 四方向图）与「🌐 全景模型」（kind `panos`，本模块的 2:1 全景），各自可选不同渠道与该渠道的模型，存 `state.json` `image_model_prefs.scenes / .panos`，默认都跟随全局「生成模型」；全景空时按全局，**不回退到「图像模型」**。实现：`modules/genmedia.py` `image_kind_of_output` 把 `assets/concepts/scenes/<sid>/panos/` 下的输出判为 `panos`，`scene_panos.py` 的支持检查与出图都按 `image_pref_env('panos')`。用户切换：全景改「🌐 全景模型」，否则改控制台「🎨 生成模型」。

## 命令

```sh
python code/render_scene_panos.py --project <slug> --scene SCN-0002 --dry-run        # 规划锚点 + 渲白模全景,不调图像模型
python code/render_scene_panos.py --project <slug> --scene SCN-0002                  # 出缺的全景(各集机位所用方案)
python code/render_scene_panos.py --project <slug> --scene SCN-0002 --anchor -8,5 --force
python code/render_scene_panos.py --project <slug> --scene SCN-0002 --anchor -8,5,30 --only-new --scheme L1   # 只加锁定锚点并只出它这一张(预览页「创建全景图」;不规划、其它锚点不动;没有机位也可)
python code/render_scene_panos.py --project <slug> --scene SCN-0002 --status         # 机检 scene_panos_ready
python code/render_shot_plates.py --project <slug> --ep ep01 [grp…]                   # 自动保证全景 → 重投影 → 出背景图
python code/render_shot_plates.py --project <slug> --ep ep01 [grp…] --repano          # 把 legacy(非全景制)背景图整体重出(用户确认后)
```

全景与背景图一样在派发任务内前台跑完，禁止丢后台。库里 2026-09-10 前的旧背景图（无 `pano_ref`）视为 legacy：不再被新决策复用，`--status` 列为 WARN；整体重出有费用，由用户决定。

## 预览页「创建全景图」（2026-09-13）

场景预览页「🌐 全景图」板块始终显示（没有索引时锚点 0）。点「➕ 创建全景图」→ 在俯视图上点一个位置（或直接填 x/z，白模米制、原点在场景中心，`x = (u-0.5)×W`、`z = (v-0.5)×D`）→ 选光照方案（机位在用的方案在前，其后是 bible lighting.json 里其余方案）→ 「生成全景图」。后端 `POST /api/v1/projects/<p>/scenes/<sid>/panos {x, z, yaw?, scheme?}` 起后台任务跑上面的 `--only-new` 命令：`add_manual_anchor` 加一个锁定的 manual 锚点（坐标夹回地面内 0.5 m；serves 只接管尚无锚点服务的机位），`ensure_scene_panos(only=[新锚点], schemes={方案})` 只出这一张（链式/重打光规则照旧），其它锚点与背景图不动。进度经 SSE `scene_panos` 事件逐行显示；退出码 2（全景模型不支持 2:1）在板块显示红条。要求项目「白模」选项开启且场景有 layout.json。
