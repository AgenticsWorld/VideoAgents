# 世界模型(World Labs Marble,2026-09-12)

按场景白模/全景生成可漫游的 3D world(高斯泼溅),在场景预览页里用 Spark 渲染、按白模坐标对齐、WASD 漫游。只做「生成 + 预览」;
在 world 里按分镜机位截图、全景重投影作背景图参考等试验流程(`dev-eric-worldlabs` 分支)**不并入**。

## 配置

控制台「🎨 生成模型」→「🌍 世界模型」板块(`genconfig.json#world`):渠道固定 Marble(World Labs);`marble.api_key`(「验证 Key」查余额),
`marble.model`:`marble-1.1`(默认)/ `marble-1.1-plus`(室外/大空间自动出更大世界,耗更多 credits)/ `marble-1.0` / `marble-1.0-draft`,可自定义 ID。
Key 在 https://platform.worldlabs.ai/api-keys 创建,也可用环境变量 `WORLDLABS_API_KEY`;API 文档 https://docs.worldlabs.ai/api(鉴权头 `WLT-Api-Key`)。

## 入口

场景预览页(`/preview/scenes`)每个已建白模的场景,在「3D 白模」「全景图」板块之下有「🌍 世界模型」板块——**仅项目「白模」选项
(`settings.json#output.spatial_blocking`)开启时显示**(预览 API 以 `whitebox_enabled` 下发;关闭时 `POST …/world` 返回 409)。

- 「全景来源」下拉:
  - `scene_pano` **场景全景图**(首选):`modules/scene_panos.py` 出的锚点全景 `panos/<anchor>/<scheme>.png`(内容按设定卡,机位/yaw 已知),按「锚点 / 光照方案」列出;
  - `depth2rgb` **Marble 深度转全景**:用锚点的白模径向深度全景(`panos/<anchor>/depth_pano.npy`,缺则现渲)log 编码送 `pano:depth_to_rgb` 出全景(计费);
    场景还没规划锚点时自动在场景最空旷处取机位(`W0`,离地 1.6 m),深度全景渲进 `world/`,不进 `panos/index.json`。
- 「生成世界模型」:`POST /api/v1/projects/<p>/scenes/<sid>/world {source, anchor?, scheme?, force?}` → 宿主后台线程跑
  `python code/worldlabs_world.py --project <p> --scene <sid> --source … [--anchor A1 --scheme LGT-…] [--force]`,输出逐行经 SSE `scene_world` 事件推到页面;
  已有 world 时按钮为「重新生成」,二次点击确认后带 `force`,旧版归档到 `world/variants/<时间>/`。
  `GET …/scenes/<sid>/world` 回任务状态(日志尾 80 行)+ 当前 world 摘要。
- 视窗(`apps/web/static/world-viewer.js`,Spark `vendor/spark/spark.module.js`,MIT):精度(500k / full_res …)、白模线框开关、回到全景机位、
  yaw / 尺度微调(只作现场比对,数值需回写 `world.json#alignment.yaw_fix_deg / scale_fix`);拖动转头,W/S/A/D/Q/E 漫游,滚轮前进;
  「在 Marble 打开」跳 `world_marble_url`。

## 产物(`assets/concepts/scenes/<sid>/world/`)

`input.json`(来源/锚点/方案/相机位/提示词;depth2rgb 另记 operation/cost)、`pano.png`(送 `worlds:generate` 的 2:1 全景)、
`depth_pano.png` / `whitebox_pano.jpg`(depth2rgb)、`generate.json`、`world.json`(world 响应 + `semantics_metadata` + `alignment` + `input`)、
`splats_<res>.spz`、`collider.glb`、`world_pano.jpg`、`thumbnail.jpg`、`variants/<label>/`(归档)。`world/` 子目录不进概念图库;预览 API 以 `scenes[].world` 带出,
`scenes[].world_sources` 列可用全景来源。

## 坐标对齐

world 原点 = 全景相机位,OpenCV 系(y 向下、z 向前 = 全景中心列)。`metric_scale_factor` 乘坐标与尺寸、`ground_plane_offset` 减 y 得米制;绕 X 轴转 180° 进 three.js
(y 向上);再绕 Y 转全景相机 yaw、平移到全景相机位即为白模坐标(viewer 外层 Group = yaw + 平移,内层 SplatMesh = scale + y offset + 绕 X 180°)。
`ground_plane_offset` 应 ≈ 全景相机离地高度,`scale_fix = 相机高 / ground_plane_offset` 作米制修正(实测室外场景模型高估 1.7 倍)。

## 已知限制

- 贴图对深度几何「loosely adhere」,world 生成又会重估几何,墙线与白模有偏差,靠线框比对。
- 单视点 world 离全景机位越远越退化;大场景一场需多个 world(每次 ≈ 1500 credits),多 world 拼接只有 Studio 有。
- 限速约 3 次/分钟、60 次/小时;单个 world 约 5 分钟。`--resume <operation_id>` 可续接中断的下载。
