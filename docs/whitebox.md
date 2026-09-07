# Three.js 白模空间与参考视频

场景预览支持旋转和缩放；分镜预览每组的「3D 白模」同时播放空间视角和镜头视角，可切换正俯视并拖动时间。统一使用本地 Three.js 0.180.0，不依赖 CDN；预览与视频导出共用同一个渲染器。参考视频分为 top.mp4（俯视空间、人物、摄像机与视轴）和 camera.mp4（最终机位白模）。

## 系统与项目职责

系统拥有 `modules/whitebox.py` 编译和校验、`whitebox-renderer.js` 渲染、`modules/whitebox_export.py` 导出，以及 API/预览界面。项目 Agent 只拥有场景资产和调度计划，不复制宿主工具。

运行链：environment-concept 布局包 → **05-scenes/scene-modeling** 场景白模 → shot-planning + blocking + camera-movement + continuity-planning → **07-directing/whitebox-staging** 数值时间线和参考视频 → 分镜确认/视频生成。

新项目由 orchestrator 在有布局包且要求白模参考视频时添加上述工单，场景工单以 SCN-ID 为粒度，调度工单以 ep 为粒度，逐组按依赖先后执行。已有项目可直接编译，不修改其分镜、动线或视频。

## 坐标、比例与场景资产

`bible/scenes/<sid>/whitebox.json`：

```json
{
  "schema_version": "whitebox_scene.v1",
  "scene_id": "SCN-0001",
  "dimensions_m": [6, 3, 5],
  "inferred": true,
  "scale_basis": "按俯视图以0.9m门宽校准，非实测",
  "objects": [
    {"id":"table", "shape":"box", "size_m":[1.2,0.75,0.65], "xy":[0.5,0.5]},
    {"id":"column", "shape":"cylinder", "size_m":[0.3,3,0.3], "position":[-2,1.5,-1]}
  ]
}
```

1单位=1米，Y向上，X向地图右方，Z向地图下方。`dimensions_m=[宽,高,深]`。地图归一化坐标 `(u,v)` 映射到 `[(u-.5)*宽,高度,(v-.5)*深]`。`position` 是几何体中心；`xy` 默认中心高度为 size_m[1]/2，可用 elevation_m 覆盖。`shape` 默认 box，可用 sphere/cylinder。`size_m` 是三个轴的包围盒尺寸，yaw 为绕Y轴弧度。人物轨迹 position 是脚下/支撑平面的锚点，不能误用物体中心。

没有标尺的图无法恢复绝对尺度，必须保留 inferred 和比例锚说明。legacy 缺场景资产时按地标生成简模并明确告警；其 20×12m 默认图幅不等于校准值。一个场景白模对应一套布局，暂不自动选择 layout 的替代形态。

## 分镜组调度

原始输入：`directing/<ep>/shot_list.json` 的 generation_groups、blocking_map 和 shots，逐镜 camera.json/blocking.json，场景 layout.json。字母/颜色与 `code/render_blocking_map.py` 相同，按组内数组顺序；不是跨所有组的永久颜色。骑乘生物与骑手同色、不另占字母。

精确计划：`directing/<ep>/whitebox_plans/<gid>.json`。actors 和 cameras 可分别省略；若提供 actors，须覆盖 blocking_map 全体及其坐骑，ID必须一致，颜色与字母由系统锁定。

```json
{
  "actors": [{
    "id":"CHAR-0001", "size_m":[0.48,1.7,0.38],
    "keyframes":[
      {"t":0,"position":[-1,0,0],"yaw":1.57,"pose":"stand"},
      {"t":2,"position":[-1,0,0],"yaw":1.57,"pose":"stand"},
      {"t":5,"position":[1,0,0],"yaw":1.57,"pose":"sit"},
      {"t":8,"position":[1,0,0],"yaw":1.57,"pose":"sit"}
    ]
  }],
  "cameras":[{
    "shot_id":"sh001", "start":0, "duration_s":8, "movement":"static",
    "keyframes":[
      {"t":0,"position":[1,1.6,4],"target":[0,1.2,0],"fov":45},
      {"t":8,"position":[1,1.6,4],"target":[0,1.2,0],"fov":45}
    ]
  }],
  "continuity":{"actors":"validate","camera":"cut"}
}
```

人物关键帧 t 是组内秒，摄像机关键帧 t 是镜内秒；camera.start 是组内秒。关键帧严格递增，覆盖0到对应时长；摄像机分段与镜头顺序和时长一致。位置、target、fov、yaw 线性插值，yaw 走最短角；pose 离散切换。关键帧可带 hold=true（到下一帧前保持）或 easing="smooth"。定点停留建议写重复位置。镜头切换时直接切换机位。

旧数据编译优先消费数值 xy_start/xy_end、path/beat 的 t+xy/landmark/pose、camera.whitebox_keyframes。没有数值运动节拍时按组时长均匀分配动线节点，文字姿态只作粗略推断；身高默认1.7m，生物默认1.4m。机位先取 layout view_tile 视轴，再按景别和镜首人物位置估计距离；缺 view_tile 暂取第一机位，所有推断均出警告。只描述运动方式但没有幅度时，推拉按视距20%、横移按1m估计，必须由 whitebox-staging 校准。暂不支持的运镜按固定机位显示并警告。

continuity 从源组 continuity_from 取前组。actors 默认 validate，camera 默认 cut。inherit 使用前组末位置/姿态或机位，不改后续目标；跨场景/场次或无前组时拒绝继承。validate 仅报告冲突，不擅自移动演员。同场次的时间跳跃也应显式 cut。

## 编译、输出与验证

```sh
python code/render_whitebox.py --project dzg6 --ep ep01 --check-only
python code/render_whitebox.py --project dzg6 --ep ep01
python code/render_whitebox.py --project dzg6 --ep ep01 grp002 --export
python code/render_whitebox.py --project dzg6 --ep ep01 --export --width 960 --height 540 --fps 24
```

不指定组时处理全体。编译结果为 `directing/<ep>/whitebox/episode.json` 和场景目录 `whitebox.scene.json`；计划与编译输出分开，重编译不覆写 Agent 设计。报错包含 group_id；任一选中组错误则 CLI 非零退出。

导出需要项目 Python 依赖 Playwright、已安装 Chromium（`python -m playwright install chromium`）以及 PATH 中 FFmpeg；可用 VIDEOAGENTS_CHROMIUM 指向独立 Chromium 可执行文件。通过 file URL 加载本地渲染器，逐帧按 i/fps 渲染，不依赖实时录屏速度。输出 `assets/whitebox/<ep>/<grp>/{top.mp4,camera.mp4,manifest.json}`，H.264/yuv420p，无音频，默认960×540/24fps。时长精度为一帧；manifest 记录源数据 SHA-256、帧数和规格。仅一项导出任务同时运行；浏览器关闭后服务端任务继续。

API（前缀 `/api/v1/projects/<project>/whitebox`）：GET `/scenes/<sid>`、GET `/<ep>`、POST `/<ep>/exports/<gid>` 开始导出、GET 同一路径查询状态。路径标识严格限定，禁止目录穿越。预览 GET 不写项目。旧服务尚未重启时，前端可读取已编译产物预览；导出新 API 需重启服务生效。

`dzg6/ep01` 的8个场景已按其俯视图写入带比例依据的白模。它们位于用户项目 data 目录（按仓库约定不提交）。数值轨迹尚有遗留推断，不能把粗模视为最终镜头调度。最终视频模型画面仍在原分镜组卡中，白模输出不会自动上传到生成渠道。

参考实现 API：[Three.js OrbitControls](https://threejs.org/docs/pages/OrbitControls.html)、[WebGLRenderer](https://threejs.org/docs/pages/WebGLRenderer.html)。
