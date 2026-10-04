# 世界模型(World Labs Marble,2026-09-12)

按场景白模/全景生成可漫游的 3D world(高斯泼溅),在场景预览页里用 Spark 渲染、按白模坐标对齐、WASD 漫游。支持「生成 + 场景预览 + 导演台对象运动预览」;
在 world 里按分镜机位截图、全景重投影作背景图参考等试验流程(`dev-eric-worldlabs` 分支)**不并入**。

## 配置

控制台「🎨 生成模型」→「🌍 世界模型」板块(`genconfig.json#world`):渠道固定 Marble(World Labs);`marble.api_key`(「验证 Key」查余额),
`marble.model`:`marble-1.1`(默认)/ `marble-1.1-plus`(室外/大空间自动出更大世界,耗更多 credits)/ `marble-1.0` / `marble-1.0-draft`,可自定义 ID。
Key 在 https://platform.worldlabs.ai/api-keys 创建,也可用环境变量 `WORLDLABS_API_KEY`;API 文档 https://docs.worldlabs.ai/api(鉴权头 `WLT-Api-Key`)。

## 入口

场景预览页(`/preview/scenes`)每个已建白模的场景,在「3D 白模」「全景图」板块之下有「🌍 世界模型」板块——**仅项目「白模」选项
(`settings.json#output.spatial_blocking`)开启时显示**(预览 API 以 `whitebox_enabled` 下发;关闭时 `POST …/world` 返回 409)。

- 「全景来源」下拉(**世界模型必须基于全景图**,2026-09-13):
  - `scene_pano` **场景全景图**:`modules/scene_panos.py` 出的锚点全景 `panos/<anchor>/<scheme>.png`(内容按设定卡,机位/yaw 已知),按「锚点 / 光照方案」列出;
    场景还没有全景图时按钮禁用,提示「请先创建全景图」并给「前往创建全景图」跳到下方「🌐 全景图」板块;`POST …/world` 此时返回 409。
  - `depth2rgb` **Marble 深度转全景**(页面已去掉,仅 CLI `--source depth2rgb` 保留;`POST …/world` 传它返回 400):用锚点的白模径向深度全景
    (`panos/<anchor>/depth_pano.npy`,缺则现渲)log 编码送 `pano:depth_to_rgb` 出全景(计费);
    场景还没规划锚点时自动在场景最空旷处取机位(`W0`,离地 1.6 m),深度全景渲进 `world/`,不进 `panos/index.json`。
- **一个场景可以有多个世界模型**(2026-10-04):每次「➕ 生成世界模型」都新增一个(已有的不动),编号 `W1`、`W2`…。
  `POST /api/v1/projects/<p>/scenes/<sid>/world {source, anchor?, scheme?}` → 宿主后台线程跑
  `python code/worldlabs_world.py --project <p> --scene <sid> --source … --new [--anchor A1 --scheme LGT-…]`,输出逐行经 SSE `scene_world` 事件推到页面;
  `GET …/scenes/<sid>/world` 回任务状态(日志尾 80 行)+ `worlds`(全部摘要)+ `world`(默认世界模型)。
- 板块里每个世界模型一张卡片:**正面预览图**(Marble 给的 `thumbnail.jpg`,没有就用它的全景)+ 编号 + **基于哪张全景图**(「来源 全景图 A3」)+ 模型 / 时间;
  多于一个时默认的那个带「★ 默认」角标。
- 点卡片打开**全屏视窗**(`apps/web/static/world-viewer.js` 的 `openWorld`,Spark `vendor/spark/spark.module.js`,MIT;Esc / ✕ 关闭):
  拖动转头,W/S/A/D/Q/E 漫游,滚轮前进;「⟲」回到全景机位;
  「💾 背景图」把虚线框里的画面按项目画幅存为本场景一张新背景图(`POST …/plates/manual`,台账 `pano_ref.world_key` 记来自哪个世界模型);
  「⚙ 设置」:精度(500k / full_res …)、白模线框、yaw / 尺度微调及「保存对齐微调」(写该世界模型 `world.json#alignment.yaw_fix_deg / scale_fix`)、
  「设为默认世界模型」、「在 Marble 中打开」(`world_marble_url`)。设置写回走 `POST …/scenes/<sid>/worlds/<key> {default?: true, yaw_fix_deg?, scale_fix?}`。
- **默认世界模型** = 背景图模式 `world` 的截图(`render_world_views`)与导演台世界背景所用的那个:`world/index.json#default`,没记时取第一个。

## 产物(`assets/concepts/scenes/<sid>/world/worlds/<key>/`)

每个世界模型一个目录 `world/worlds/W<n>/`;此前的单世界模型产物直接放在 `world/` 根下,原地当作 `W1` 读取、不搬动。`world/index.json` 记默认世界模型。
生成半途失败:还没提交 `worlds:generate` 的目录自动清掉;已提交的留着,可 `--resume <operation_id> --world <key>` 续接。

`input.json`(来源/锚点/方案/相机位/提示词;depth2rgb 另记 operation/cost)、`pano.png`(送 `worlds:generate` 的 2:1 全景)、
`depth_pano.png` / `whitebox_pano.jpg`(depth2rgb)、`generate.json`、`world.json`(world 响应 + `semantics_metadata` + `alignment` + `input`)、
`splats_<res>.spz`、`collider.glb`、`world_pano.jpg`、`thumbnail.jpg`(正面预览图)。旧版 `--force` 留下的 `world/variants/<label>/` 归档不再列出。
`world/` 子目录不进概念图库;预览 API 以 `scenes[].worlds`(全部)与 `scenes[].world`(默认)带出,`scenes[].world_sources` 列可用全景来源。

## 坐标对齐

world 原点 = 全景相机位,OpenCV 系(y 向下、z 向前 = 全景中心列)。`metric_scale_factor` 乘坐标与尺寸、`ground_plane_offset` 减 y 得米制;绕 X 轴转 180° 进 three.js
(y 向上);再绕 Y 转全景相机 yaw、平移到全景相机位即为白模坐标(viewer 外层 Group = yaw + 平移,内层 SplatMesh = scale + y offset + 绕 X 180°)。
`ground_plane_offset` 应 ≈ 全景相机离地高度,`scale_fix = 相机高 / ground_plane_offset` 作米制修正(实测室外场景模型高估 1.7 倍)。

## 作为分镜背景图的参考来源（背景图模式「世界模型」，2026-09-22）

输出设置「背景图模式」（白模开启时显示；场景预览页「分镜背景图」板块可按场景覆盖）选「世界模型」后，`code/render_shot_plates.py` 不再自动规划锚点出全景，而是要求该场景已有 world：在 world 里按母图机位截图（`modules/worldlabs.py#render_world_views`，渲染页 `apps/web/static/world-view-export.html`，与 `world-viewer.js` 同一套对齐变换；`world_missing()` 判定 world.json + splats 文件是否齐）作 `[Image 1]` 二次生成。没有 world → 退出码 4 `[world_missing]`，由用户在本板块生成后重跑；Agent 不得自行生成。截图精度取 `full_res` 优先（`VIEW_RES_ORDER`），JPEG 落库 `<key>.world.jpg`。细则见 `docs/shot_plates.md`「背景图模式」。

## 已知限制

- 贴图对深度几何「loosely adhere」,world 生成又会重估几何,墙线与白模有偏差,靠线框比对。
- 单视点 world 离全景机位越远越退化;大场景一场需多个 world(每次 ≈ 1500 credits),多 world 拼接只有 Studio 有。
- 限速约 3 次/分钟、60 次/小时;单个 world 约 5 分钟。`--resume <operation_id>` 可续接中断的下载。


## 导演台世界视角（2026-09-13）

导演台 `/preview/director` 顶部的「世界模型视角」在当前场景存在可用 world 时启用。
静态白模被场景当前世界模型替换，保留角色、群演、组级道具、轨迹和当前摄影机；对象继续使用所选版本的时间轴。
当前镜头之外的角色和道具也显示，但对象关键帧中的显式 `visible:false` 仍生效。
默认开启「对象透视」，把对象绘制在世界背景之上，方便观察被墙遮挡的运动；关闭后使用正常混合深度显示。
世界本身是静态场景，不会移除已经烘焙在世界中的家具，也不随历史白模版本回滚。

拖动转头，W/S 前后、A/D 左右、Q/E 升降、Shift 加速、滚轮前进；键盘漫游先点视窗获取焦点。
「回到全景机位」恢复生成时的相机位置。世界视角用于预览；解锁编辑时自动切回旋转白模视角。

性能策略：只在进入世界视角后加载 Spark 和 SPZ，默认 500k，可手动选 150k、100k 或 full_res；
复用导演台现有单个 WebGLRenderer、项目预览分辨率和 pixelRatio=1。
同场景切组/版本时保留世界资源和漫游位置，换场景或销毁监视器时释放世界资源。
暂停且无漫游输入时不持续刷新，只响应交互、加载和 Spark 排序完成的刷新通知。
加载失败可降档或重新加载；场景无世界模型时自动回到旋转视角。

实现：`apps/web/static/director-world.js`，不修改共享白模导出渲染器。
回归检查：`node tests/director_world_check.mjs`（替换 GPU 的逻辑测试），
`node tests/whitebox_performance_check.mjs`。实际设备 GPU 帧率仍需浏览器验收。
