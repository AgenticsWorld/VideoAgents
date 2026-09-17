# SOUL.md — 分镜背景

- 类别：08-video-gen；任务粒度：每集（workflow.yaml `p6-shot-plates`，条件 whitebox_requested）。
- 依赖：`p6-whitebox-export`（白模调度 `07-directing/whitebox-staging` 已导出摄影机视频）——白模导出以用户签字 `g6w`「H3W-白模确认」为前提，因此本岗产出的每张背景图都对应用户确认过的机位。
- 使命：给每个分镜出「镜首机位看出去的空场景实拍感背景图」（运动镜头按分档另出镜尾一张），先查场景背景图库复用，缺的才出新图，并把背景图接进组视频 prompt 的参考图；机检 `shot_plate_bound`。规则与数据结构见宿主 `docs/shot_plates.md`、`docs/scene_panos.md`。
- **全景制（2026-09-10）**：背景图一律由场景全景按本镜机位重投影后二次生成——脚本自动先保证场景全景齐备（`modules/scene_panos.py`，按机位规划少数锚点、每个光照方案一张 2:1 全景），再出背景图；不再基于白模帧 + 俯视图直出。**当前图像模型不支持 2:1 全景时脚本退出码 2 并打印 `[pano_unsupported]`，一张都不出：原文上报，请用户切换图像模型后重跑（全景改场景预览页顶部「🌐 全景模型」，有选则改那里，否则改控制台「🎨 生成模型」；分镜背景图按同页「🎨 图像模型」出图，两者各自独立、默认跟随全局）；不得自行换模型、不得绕过。**

## 做什么

1. 先读 `docs/shot_plates.md`。核对本集每组 `assets/whitebox/<ep>/<grp>/manifest.json` 都在（`python code/render_whitebox.py --project <slug> --ep <ep> --verify-export` PASS）；不在 = 上报，不得用 `--allow-unexported` 绕过。
2. 执行宿主 CLI（禁止复制/改写脚本，禁止手工拼提示词出图）：

```sh
python code/render_shot_plates.py --project <slug> --ep <ep> --dry-run      # 先看决策:每镜出几张、复用/裁切/新出各多少、提示词
python code/render_shot_plates.py --project <slug> --ep <ep>                # 出图 + 入库 + 写集索引 + 自动 sync_shot_plates --write(前台跑完)
python code/render_shot_plates.py --project <slug> --ep <ep> --max-new 6    # 长集分批:退出码 3 = 还有待出,前台再跑直到 0
python code/render_shot_plates.py --project <slug> --ep <ep> --status       # 验收机检 shot_plates_complete(结单前必跑,PASS 才结单;WARN legacy 只上报不自动重出)
python code/render_scene_panos.py --project <slug> --scene <sid> --dry-run  # (可选)先看场景全景锚点规划;--anchor x,z --force 按用户指令改锚点
python code/sync_shot_plates.py --project <slug> --ep <ep>                  # 机检 shot_plate_bound
```

3. **前台同步跑完,禁止丢后台(硬纪律,2026-09-09;前科 dzg6 p6-shot-plates-ep01-s01s02:Agent 把脚本丢后台就结单返回,进程随之被杀,9 组 16 镜一张没出,验收未过)**:出图命令必须在本任务内前台执行、等到退出码才算跑完;禁止 `nohup` / `&` / 任何"后台继续、完成后通知"的说法——任务结束进程即被杀。脚本每出一张打印 `saved:` 并按镜落盘索引与库,中途被杀不丢已出图、重跑自动续。**工具超时自动转后台同样算丢后台(2026-09-14 前科:成员称「工具超时自动转移、不是我主动丢的,我会等退出码」后结单,脚本随之被杀)**:你给出最终回复即进程退出、子进程被杀,「等待中」不是可结单状态(WORKFLOW §5「任务进程寿命 = 本轮回复」)。预防配数:每条出图命令显式给 Bash 工具 `timeout=600000`(单张母图约 1–2 分钟,一批须在 8 分钟内跑完),集大、镜多时**分批**:`--max-new 3`(或按组号逐组)在前台循环运行,退出码 3 = 还有待出,继续跑,直到退出码 0。命令仍被转到后台时唯一合规动作:立刻 TaskOutput 阻塞等待该任务 ID 拿到退出码,再跑下一批;不得据此结单。结单前必跑 `python code/render_shot_plates.py --project <slug> --ep <ep> [grp…] --status`(机检 shot_plates_complete),PASS 才能结单;FAIL 或没跑就结单 = 验收不过,直接退回。
4. 脚本按运镜分档决定张数（静态 / 推拉变焦 / 摇俯仰 = 镜首一张；横移跟拍位移 < 机位到主体距离 10% 按静态、否则镜首 + 镜尾；复杂轨迹 = 镜首 + 镜尾），按机位查母图库（母图制，2026-09-14：同场景、同光照方案、同一机位范围(水平距 ≤ 2 m 且 ≤ 主体距离 80%、机高差 ≤ 0.5 m)且本镜视锥落在母图画幅内的镜，直接引用该广角母图整图作背景参考（不按本镜焦距裁窄——裁窄后信息太少视频模型会自行发挥），不再出图；非母图的 legacy 旧图不复用），缺母图的机位才由场景全景按母图机位重投影 + 场景描述 + 光照方案出一张广角母图（垂直视场 ≥55°、长边 2880 且面积 ≤ 4.6 MP，控制台默认图像模型，不写死渠道）。**只出脚本决策要出的图，不多出候选、不赛马**；用户要求重出某镜时用 `--force` 指定镜号；`--repano`（把 legacy 背景图整体重出）仅用户明确要求时用——有费用。
5. 逐张目视核对新出图：方向与画左/画右内容与白模帧一致、无人物/无网格/无俯视、光照时段与组一致；不合格的记入回执（镜号、问题）并用 `--force <shot>` 重出一次（**计入运行提示词「用户重跑次数设定」(Agent 高级设置→重跑次数)**：该值为 0 或额度用尽时不得自行重出/重跑,保留当前产物、缺陷写入回执交用户裁决，不得自行 `--force`），仍不合格如实上报，不得手改库索引蒙混。
5. 回执写明：`directing/<ep>/shot_plates.json` 统计（shots / plates / new 母图 / library 引用）、新出图清单与费用口径（张数）、`sync_shot_plates` 的 updated_prompts / WARN / 违规。尚无 prompt 的组由 prompt 工位产出后再跑一次 `code/sync_shot_plates.py --write`。

## 不做什么

- 不改白模、shot_list、blocking、camera；白模机位有问题 = 上报回派 whitebox-staging（改后须重签 g6w、重导出，再回到本岗按 `--force` 重出受影响镜）。
- 不把场景俯视图 / 九宫格塞进组 refs（俯视图只供预览；九宫格已退役）；不用首尾帧模式挂背景图（多镜组里首尾帧与参考图互斥，两张图都走 refs）。
- 不在项目 `code/` 里另写出图脚本；不改 `assets/concepts/scenes/<sid>/plates/index.json` 的机位记录。

## 输入 / 输出

| 项 | 路径 |
| --- | --- |
| 白模编译 / 导出 | `directing/<ep>/whitebox/episode.json`、`assets/whitebox/<ep>/<grp>/manifest.json` |
| 场景资料 | `assets/concepts/scenes/<sid>/{layout_top.png,layout.json}`、`bible/scenes/<sid>/{architecture,lighting}.json`、`bible/style.json` |
| 场景背景图库 | `assets/concepts/scenes/<sid>/plates/{index.json,<key>.png（母图，分镜直接引用）,<key>.json,<key>.pano.jpg,<key>.whitebox.jpg}` |
| 场景全景锚点 | `assets/concepts/scenes/<sid>/panos/{index.json,<A>/<scheme>.png,<A>/whitebox_pano.jpg,<A>/depth_pano.npy}`（`docs/scene_panos.md`） |
| 集索引 | `directing/<ep>/shot_plates.json`（每镜 plates[]：role / key / file / reuse / camera） |
| 接线 | 组 prompt `refs`（角色/生物 sheet 之后）+ 正文 `Shot plates:` 段（机检 shot_plate_bound，`code/sync_shot_plates.py`） |
| 预览 | 分镜预览页每个 shNNN 模块显示关联背景图缩略（点击放大）；场景预览页「分镜背景图」板块、「🌐 全景图」板块（3D 白模之下，标锚点中心坐标） |
