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

## 复用（按机位指纹建库，不按分镜建图）

- 库：`assets/concepts/scenes/<sid>/plates/index.json` + `<key>.png` + `<key>.json`（提示词/refs/机位事实/渠道）+ `<key>.whitebox.jpg`（干净白模帧）。`key = <lighting_scheme_id>_b<朝向°>_h<机高档>_x<机位x>_z<机位z>_f<fov°>`，由首个生成它的机位命名，跨组、跨集共享。
- 复用容差：同场景、同光照方案、同机高档（0 贴地 <0.5 m / 1 低机位 <1.2 m / 2 人眼 <2.0 m / 3 高机位）、朝向 ±20°、机位 6 m 内、fov ±15° 直接复用；同一机位同一朝向而库图更宽（fov 差 > 15°）时按 `tan(fov/2)` 比例从库图中心裁切复用（`<key>.crop_f<fov>.png`，裁切不低于原图 1/3）。反打（朝向差 180°）、换时段、跨机高档一律不复用。
- 同一次运行按 fov 从宽到窄决策，窄景别可裁切同轴宽图；本次决定新出的机位也参与后续镜位的复用判断，不重复出图。
- 集索引 `directing/<ep>/shot_plates.json`：每镜 `plates[]{role: start|end, key, file, reuse: new|library|crop, crop, camera}`，分镜只记指纹与文件；机位与当前 `episode.json` 不一致视为过期（机检 WARN）。

dzg6 ep01 全集 112 镜 dry-run：需 114 张背景图，新出 46、库复用 60、裁切 8。

## 出图

- refs：`[Image 1]` 白模干净帧（隐藏人物/群演，1920×1080）→ `[Image 2]` 场景俯视图（`scene_refs.layout_top`）；镜尾图在两者之间插镜首成图。
- 提示词：空场景声明 + 组 `time_of_day` + 光照方案 `prompt_fragment_en` + 机位事实（景别、等效焦距、机高档、俯仰、机位落在哪个几何上、罗盘朝向、画左/画右/身后各是什么——由 `layout.json#orientation` 把白模坐标映射到东南西北）+ 白模帧用法 + 俯视图用法 + 画内自左向右清单（白模几何盒采样投影，按基名/地标归并）+ **画外不可见清单**（在画幅外/身后的地标，明令不画——实测没有这句时场景描述会把身后的大门院墙带进画面）+ 场景描述（architecture.json 的 form / arch_style / era_region / scale / materials / details，声明只作材质与年代参考）+ 禁人/禁网格/禁俯视 + `style_fragment_en`。negative = `negative_prompt_en` + architecture.negative + 人物/网格/俯视词。可选 `--sun <罗盘>` 写太阳相对机位方向。
- 分辨率长边 1920 按项目画幅；渠道 = 控制台默认图像模型（`modules.genmedia.generate_image`，不写死）。Seedream 5.0 pro 口径：1920×1080 落 0.3 元档 + 参考图首张免费、之后 0.02 元/张。

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
