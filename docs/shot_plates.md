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
- 提示词：空场景声明 + 组 `time_of_day` + 光照方案 `prompt_fragment_en` + 机位事实（景别、等效焦距、机高档、俯仰、机位落在哪个几何上、罗盘朝向、画左/画右/身后各是什么——由 `layout.json#orientation` 把白模坐标映射到东南西北）+ 白模帧用法 + 俯视图用法 + 画内自左向右清单（白模几何盒采样投影，按基名/地标归并）+ **画外不可见清单**（在画幅外/身后的地标，明令不画——实测没有这句时场景描述会把身后的大门院墙带进画面）+ 场景描述（architecture.json 的 form / arch_style / era_region / scale / materials / details，声明只作材质与年代参考）+ 禁人/禁网格/禁俯视 + `style_fragment_en`。negative = `negative_prompt_en` + architecture.negative + 人物/网格/俯视词。可选 `--sun <罗盘>` 写太阳相对机位方向。
- 分辨率：母图长边 2880、派生分镜图长边 1920，按项目画幅；渠道 = 场景预览页顶栏「🎨 图像模型」的选择，空则控制台默认图像模型（`modules.genmedia.generate_image` 按输出目录自动套用，不写死）；场景全景另按同页「🌐 全景模型」，两者独立（2026-09-12）。Seedream 5.0 pro 口径：1920×1080 落 0.3 元档 + 参考图首张免费、之后 0.02 元/张。

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

**Seedance 2.5 变体（2026-09-09）**：组生效模型为 2.5（组级覆盖、项目提示词技能快照或 genmedia 当前模型任一判定）时，`--write` 改写为 2.5 官方结构：Shot plates 段变为 `【场景】场景A（Shot 1 的机位，空场景 background plate）参考 [Image N]，只采用空间布局、建筑、材质和光线…`，并在每个 Shot 段头（`Shot k:` 或 `Shot k｜标题。`）插入机器持有的「场景激活：使用场景A（[Image N]）；不采用场景B（[Image M]）。」句；Agent 自己写的「使用：/不采用：」清单不动。机检同样分支。实测 dzg6 grp010：2.0 式绑定段三次都被模型合成一个环境，按此结构重写后两镜各用各图。配套机检 `sd25_prompt_structure`（`code/prompt_skill_check.py`）要求正文其余部分也按 2.5 结构写。

**MiniMax H3 变体（2026-09-09）**：组生效模型为 H3（模型 id 或工作流名同时含 minimax 与 h3，或技能为 h3-prompt-writing）时，Shot plates 段按 Ref2VA 官方 §2.2 写成图片构图锚：`<Picture N> ([Image N]) is the empty background plate and composition anchor of [Shot k] … reference generation for architecture, set dressing, lighting and camera space only`，并在每个 Shot 段头插入 `Plate anchor: this shot's set and framing correspond to <Picture N> ([Image N]). <Picture M> ([Image M]) not used in this shot.`；`[Image N]` 并列保留供项目机检。配套机检 `h3_prompt_structure`。H3 变体尚未在真实项目上出片验证。

不带 `--write` 为机检：refs 不含俯视图/九宫格（违规）、每张背景图在 refs 且有说明句（违规）、背景图机位过期（WARN）、尚无背景图的镜（WARN，`--strict` 违规）。`render_shot_plates.py` 出图后自动 `--write`；prompt 工位产出 prompt 后再跑一次。原 prompt 首次备份到 `directing/<ep>/whitebox/prompt_backups/`。

## 预览

- 分镜预览页（`/preview/storyboard`）每个 shNNN 模块底部「🖼 分镜背景图」：关联的全部背景图缩略（宽 100 px，起点/终点 · 新出/复用库图/裁自宽景），点击放大。数据来自 `api_preview_storyboard` 的 `shots[].plates`。
- 场景预览页（`/preview/scenes`）「🖼 分镜背景图」板块：按库列出，标注朝向/机高/焦距/时段与被哪些集/镜引用；`plates/` 子目录不再混进概念图库。

## 实测记录（dzg6 ep01 grp002，doubao-seedream-5-0-pro，1920×1080）

- sh004（跟拍 8 m）：镜首、镜尾两张，镜尾以镜首成图为第二参考图，同一地点同一材质，机位前移；方向、画左画右与白模一致。
- sh003（贴地朝南 CU）：首版被场景描述带偏（画出了身后的大门院墙），加入「画外不可见」清单后重出（见库 `<key>.json` 提示词）。贴地机位模型仍偏高（约 1 m）是已知局限。
- 2026-09-08 原型对照（2560×1440，已归档在 `assets/shot_plates/`）：白模干净帧是决定性输入；整张九宫格会被整格复制盖过白模，这是九宫格退役的直接依据。
