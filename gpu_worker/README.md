# HunyuanWorld GPU worker

跑在 GPU 主机(默认 Windows + 3090 24G)上的自包含服务:场景全景 → HunyuanWorld-1.0 分层重建 → 多视图渲染 → 3DGS → SPZ/GLB 产物。

**部署步骤见主仓文档 [`docs/hunyuan_deploy.md`](../docs/hunyuan_deploy.md)。** 本目录不 import VideoAgents 其他模块,VideoAgents 侧也只通过 HTTP 调用本服务(`modules/hunyuan_world.py`)。

快速开始(环境已按部署文档搭好后):

```bat
copy config.example.json config.json   :: 改 hyworld_repo / python / work_root / token
python hunyuan_worker.py               :: 监听 :8310
curl -H "X-Worker-Token: <token>" http://localhost:8310/health
```
