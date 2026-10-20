# 世界模型(HunyuanWorld 本地算力,2026-10-10)

与 [World Labs Marble](worldlabs_world.md) 并列的第二条世界模型渠道:**场景全景 → 自有 GPU 主机
(HunyuanWorld-1.0 分层 3D 重建 + 多视图渲染 + 3DGS 训练)→ 与 Marble 完全相同的产物契约**,
视窗漫游、对齐微调、导演台世界视角、背景图模式「世界模型」截图链全部零改动复用。不耗云端 credits。

架构边界:GPU 计算全部在独立仓库 **[AgenticsWorld/VideoAgents-hunyuan-rpc](https://github.com/AgenticsWorld/VideoAgents-hunyuan-rpc)**
(自包含 FastAPI worker,部署文档即其 README,默认 Windows + 3090);本仓其余部分只有 HTTP 调用逻辑
(`modules/hunyuan_world.py` 薄客户端 + `code/hunyuan_world.py` CLI + 宿主派发分支)。

## 配置

控制台「🎨 生成模型」→「🌍 世界模型」:渠道 = 当前选中标签页(保存生效),`genconfig.json#world.provider` ∈
`marble`(默认)/ `hunyuan`。HunyuanWorld(远程)标签页:`hunyuan.base_url`(worker 地址)、
`hunyuan.token`(X-Worker-Token)、高级参数 `hunyuan.{views, gs_steps, fp8}`(留空走 worker 缺省);
「验证」打 `GET /health` 回 GPU/env/磁盘自检。环境变量 `HUNYUAN_WORKER_URL` / `HUNYUAN_WORKER_TOKEN` 可覆盖。

## 链路(四阶段,全在 worker 侧)

| 阶段 | 内容 |
|---|---|
| A 重建 | `demo_scenegen.py`(bf16 + 逐层顺序卸载,不打 FP8;Windows 补丁见部署文档)对场景锚点全景出分层 textured mesh(`mesh_layer*.ply`) |
| B 渲染 | Open3D 离屏渲染有界轨迹多视图(中心 1 + 环上内外交替,默认 24 张 1024×576,半径 0.35 m 按标定换算) |
| C 训练 | gsplat(MCMC,上限 1M 高斯)RGB+深度监督,默认 8000 步 → `splats_{full_res,500k,150k,100k}.spz` |
| D 打包 | 合并减面出 `collider.glb`(≤65536 三角形)、`thumbnail.jpg`、`world_pano.jpg`、产物清单 |

坐标系与 Marble 同惯例(x 右、y 下、+z = 全景中心列,原点 = 全景相机):1.0 世界系(z 上、中心列 = −x)
在 Stage B 经旋转 `v'=(y,−z,−x)` 换入契约系,viewer 对齐变换原样适用。尺度:MoGe 深度是相对尺度,
标定口径与 Marble 相同——`scale_fix = 全景相机真实离地高(白模精确值)/ worker 拟合的
ground_plane_offset(原始单位)`;残余误差在视窗 ⚙ 设置里微调保存(同一条已打通链路)。

## 入口

与 Marble 完全共用:场景预览页「🌍 世界模型」板块(仅项目「白模」开启时)「➕ 生成世界模型」,
`POST /api/v1/projects/<p>/scenes/<sid>/world` 按 provider 派发到 `code/hunyuan_world.py`,
SSE `scene_world` 事件推日志;多世界模型卡片、★ 默认、全屏视窗、💾 背景图、对齐微调语义不变
(Hunyuan 世界没有「在 Marble 中打开」链接,卡片带 provider 区分)。

CLI(`code/hunyuan_world.py`)与 Marble 版同形:

```bash
python code/hunyuan_world.py --project <p> --scene <sid>                      # 首个锚点全景 → world
python code/hunyuan_world.py --project <p> --scene <sid> --new --anchor A2    # 再生成一个
python code/hunyuan_world.py --project <p> --scene <sid> --resume <job_id> --world W2   # 中断续接下载
python code/hunyuan_world.py --project <p> --scene <sid> --list --set-default W2
python code/hunyuan_world.py --check                                          # worker 自检
python code/hunyuan_world.py --project <p> --scene <sid> --smoke              # 袖珍自检(4 视图 200 步)
```

## 产物

同 Marble 契约(`assets/concepts/scenes/<sid>/world/worlds/<key>/`):`input.json`、`pano.png`、
`generate.json`(记 worker job_id/参数)、`world.json`(`provider: "hunyuan"`、`world_id: hy-<job_id>`、
`model: hunyuanworld-1.0+gsplat`、`semantics_metadata/alignment` 同构)、`splats_<res>.spz`、
`collider.glb`、`world_pano.jpg`、`thumbnail.jpg`。`world/index.json#default` 语义不变。

worker 侧任务工作区 `work_root/jobs/<job_id>/`(中间产物:scenegen/ renders/ gs/),
阶段标记 `<n>_<stage>.done` 支持 `POST /worlds/<id>/resume` 断点续跑;完成/失败 7 天自动清理。

## 已知限制

- **清晰度上限 = 训练视图分辨率**:`render_width/render_height`(缺省 1600×900,worker config defaults 可调;
  再高 24G 显存吃紧)远低于全景源,想更锐先保证全景源本身清晰、再调渲染分辨率与 views。
- **针状杂刺**:深度突变边缘的拉长高斯由训练里的尺度正则抑制(权重 0.01,宽长轴比限 5);
  仍明显就加大视图数或降 radius_m。
- **世界范围限于全景包络**:链路没有 2.0 的 WorldStereo 世界扩展,漫游轨迹按 0.35 m 环设计,
  离全景机位越远越退化(与 Marble 单视点 world 同类限制);遮挡背面是 1.0 分层补全的近似。
- **尺度是拟合值**:地面平面拟合的 ground_plane_offset 误差进 scale_fix,必要时手动微调。
- **单卡单任务**:worker 忙时提交回 409,稍后重试;跨主机多 worker 暂不支持。
- 首次运行要下载 ~60 GB 权重;SPZ 为 DC-only(shDegree 0),视角相关效果(高光)弱于 Marble。
- HunyuanWorld-1.0 社区许可证:EU/UK/韩国不适用;产品月活 >100 万须向腾讯申请商用许可
  (详见 [hunyuan-rpc README](https://github.com/AgenticsWorld/VideoAgents-hunyuan-rpc) 末尾 License 节)。
