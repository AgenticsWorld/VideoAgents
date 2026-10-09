# 世界模型(World Labs Marble / Atlas,2026-09-12;Atlas 2026-10-09)

按场景白模/全景生成可漫游的 3D world(高斯泼溅),在场景预览页里用 Spark 渲染、按白模坐标对齐、WASD 漫游。支持「生成 + 场景预览 + 导演台对象运动预览」;
在 world 里按分镜机位截图、全景重投影作背景图参考等试验流程(`dev-eric-worldlabs` 分支)**不并入**。

## 配置

控制台「🎨 生成模型」→「🌍 世界模型」板块(`genconfig.json#world`):两个标签页 **Marble**(默认)/ **Atlas**,选中的标签页即生效渠道
(`world.provider` = `marble` | `atlas`,保存后生效;场景预览页「➕ 生成世界模型」按钮旁显示当前渠道)。

**Marble**:`marble.api_key`(「验证 Key」查余额),
`marble.model`:`marble-1.1`(默认)/ `marble-1.1-plus`(室外/大空间自动出更大世界,耗更多 credits)/ `marble-1.0` / `marble-1.0-draft`,可自定义 ID。
Key 在 https://platform.worldlabs.ai/api-keys 创建,也可用环境变量 `WORLDLABS_API_KEY`;API 文档 https://docs.worldlabs.ai/api(鉴权头 `WLT-Api-Key`)。

**Atlas**(World Labs Marble 2 beta):只有 `atlas.api_key`(无模型可选,固定任务 `atlasChisel`);「验证 Key」读一条本项目操作记录
(Atlas 没有余额接口)。Key 在 https://atlas-beta.worldlabs.ai/api-keys 创建,须勾选 `tasks.create` / `operations.read` / `assets.create` / `assets.read`;
也可用环境变量 `ATLAS_API_KEY`;API 根地址默认 `https://api.atlas-beta.worldlabs.ai/api/v2`(beta,`ATLAS_API_BASE_URL` 可覆盖)。见下文「Atlas 渠道」。

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
  「💾 背景图」把虚线框里的画面按 16:9(不随项目画幅)存为本场景一张新背景图(`POST …/plates/manual`,台账 `pano_ref.world_key` 记来自哪个世界模型);
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

## Atlas 渠道(2026-10-09,`modules/worldlabs_atlas.py`)

Atlas 没有「一次出整个 3D 世界」的任务,它按机位出图。为了让场景预览视窗、背景图模式「世界模型」的截图、导演台世界背景都照旧可用,
本渠道自己把多机位视图融合成与 Marble 同格式的高斯泼溅,产物目录、`world.json` 字段、默认 / 对齐 / 截图逻辑与 Marble 完全共用
(`world.json#provider = atlas`,旧记录没有该字段 = marble)。流程(`code/worldlabs_world.py --provider atlas`,页面按钮自动带上):

1. **视图规划**(`plan_views`,至多 28 个目标机位 = 单次上限;`--views N` 可减):全景锚点处 6 向平视环 + 3 向俯视环(-40°);
   本场景各集白模分镜机位(镜首/镜尾,按母图视场 55°,同位同向去重后按位姿最远点取样,至多占剩余预算一半);余下在白模空地上按最远点取站位,每站 4 向。
2. **输入**:每个目标机位的白模 z 深度(`scene_panos.raycast` 纯 numpy 求交,1280×720,约 3 s/张)编码为反向 log 8bit PNG(白 = 近)作
   `contextFrames`;所选全景图在锚点处切 6 张平视 + 1 张俯视透视图作 `sourceFrames`(机位精确已知,让生成视图沿用全景的材质与光照);
   提示词 = 场景设定卡描述(不写「360 全景」和以全景中心为准的方位)。请求体约 2 MB,内联 base64。
3. **提交** `POST /tasks:atlasChisel`(带 Idempotency-Key;429/5xx 按 Retry-After 重试)→ 轮询 `GET /operations/{id}` → 下载各视图
   (读链接过期时按 assetId `:createReadUrl` 重签)。
4. **融合**:每张视图按本机位白模深度反投影(隔 2 像素取一点,深度断层处剔除;无几何处放到 200 m 外并稀疏取样),分级体素去重
   (同格留离机位最近的点),写 SPZ v2:`splats_full_res.spz`(≤ 300 万点)与 `splats_500k.spz`(≤ 50 万点,视窗默认)。
   坐标约定同 Marble(以全景相机为原点、OpenCV 轴),`alignment` 填 `metric_scale_factor = 1`、`ground_plane_offset = 0`、`scale_fix = 1`——
   深度来自白模,本身就是米制、与白模对齐,不需要尺度标定。正面预览图取锚点正前方那张视图。

本目录下另有 `atlas/`:`request.json`(机位清单 / 提示词 / 幂等键)、`ctx_NN_depth.png`、`src_NN.jpg`、`view_NN.png`(生成视图)。
中断后 `--resume <operation_id> --world <key>` 续接:按 `request.json` 重算白模深度后下载融合。Atlas 只支持 `--source scene_pano`。

画质:融合出的是按视图贴出来的点云,机位附近清楚、远离所有机位处有空洞;规划已含本场景分镜机位附近的视图,背景图截图主要落在这些位置。
费率按 Atlas 平台当前价目(1000 credits = 1 美元,文档未公开每视图价格),一次生成 = 一次 atlasChisel(至多 28 个目标视图)。
实测(2026-10-09):假 API 全链(alices SCN-long-hall,26 机位)出图对齐无误、视窗 / 无头截图可用;真实接口只验证到鉴权(假 Key 401),**真实生成未跑**。

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
