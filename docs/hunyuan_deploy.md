# HunyuanWorld GPU worker 部署(默认 Windows + 3090 24G,2026-10-10)

把「场景全景 → HunyuanWorld-1.0 分层重建 → 多视图渲染 → 3DGS 训练 → SPZ/GLB 产物」跑在你自己的
GPU 主机上,VideoAgents(开发机,Mac/Linux 皆可)经 HTTP 调用。功能说明见
[docs/hunyuan_world.md](hunyuan_world.md);License 注意事项见文末。

默认目标机:**Windows 10/11 x64 + RTX 3090 24G**。Linux 主机同样适用(命令按 POSIX 习惯替换即可),
文中以 Windows 为准。

## 0. 前置要求

| 项 | 要求 |
|---|---|
| GPU | NVIDIA,≥24 GB 显存(3090/4090 验证口径;更小显存需自行下调 `max_gaussians` 与视图分辨率) |
| 驱动 | 最新 Game Ready / Studio 驱动 |
| 磁盘 | ≥100 GB 空闲(权重 ~60 GB + 任务工作区,单任务中间产物 5–20 GB) |
| 软件 | Git、[Miniconda](https://docs.conda.io/en/latest/miniconda.html)、可用内网固定 IP |
| 网络 | 能访问 huggingface.co(不能则配 `HF_ENDPOINT` 镜像,见第 5 步) |

## 1. 拷贝代码

worker 自包含在仓库 `gpu_worker/` 目录,只需它(不必整仓):

```bat
git clone --depth 1 <VideoAgents 仓库地址> videoagents
xcopy /E /I videoagents\gpu_worker D:\gpu_worker
cd /d D:\gpu_worker
```

## 2. 创建 hyworld conda 环境

HunyuanWorld-1.0 官方环境是 Linux 导向(`docker/HunyuanWorld.yaml`),Windows 按下面的适配清单手装。
打开 **Anaconda Prompt**:

```bat
conda create -n hyworld1 python=3.10 -y
conda activate hyworld1

:: PyTorch 2.5 + cu124(Windows 官方 wheel;与 HunyuanWorld-1.0 测试版本一致)
pip install torch==2.5.0 torchvision==0.20.0 --index-url https://download.pytorch.org/whl/cu124

:: HunyuanWorld-1.0 运行依赖(demo_scenegen 链路;版本与官方 docker/HunyuanWorld.yaml 对齐。
:: 注意:transformers 不要装新版——新版对 torch 2.5 禁用 PyTorch 支持,
:: 会让 diffusers 导入链断裂,报 "Failed to import diffusers.loaders.unet ... name 'nn' is not defined")
pip install diffusers==0.34.0 transformers==4.51.0 accelerate==1.6.0 peft==0.15.0 safetensors==0.5.3
pip install open-clip-torch==2.30.0 pytorch-lightning==2.4.0 torchmetrics==1.7.1
pip install opencv-python imageio imageio-ffmpeg tqdm sentencepiece
pip install scikit-image matplotlib easydict                :: 分层重建 / ZIM 依赖
pip install "utils3d_moge @ git+https://github.com/EasternJournalist/utils3d-moge.git"
:: 注意:MoGe 深度用的是 GitHub 的 utils3d_moge(含 numpy 子模块),不是 PyPI 的 utils3d 包;
:: 装错会在深度预测阶段报 AttributeError: module 'utils3d' has no attribute 'numpy'
pip install basicsr-fixed facexlib gfpgan realesrgan     :: 全景超分(sr=True 必需)
:: basicsr/realesrgan 与 torchvision 0.18+ 的兼容 shim(否则 import 报 functional_tensor 缺失)
python -c "import pathlib, torchvision; p = pathlib.Path(torchvision.__file__).parent/'transforms'/'functional_tensor.py'; p.write_text('from ._functional_tensor import *\n', encoding='utf-8')"
pip install onnxruntime-gpu                               :: ZIM 分割(ViT-L onnx)
pip install open3d trimesh                                :: Stage B 渲染 / Stage D 打包
pip install moge                                          :: 全景深度(MoGe, Ruicheng/moge-vitl)

:: 3DGS 训练(Stage C)。Windows + torch 2.5 无预编译 wheel(官方只编到 pt24),pip 装好后
:: 还需一次性 JIT 编译:装 VS Build Tools(C++ 桌面开发)+ CUDA Toolkit 12.4,
:: 然后在「x64 Native Tools Command Prompt」里 python -c "import gsplat" 编译一次入缓存(详见故障表)
pip install gsplat

:: worker 服务自身
pip install -r requirements.txt
```

说明:
- `--fp8_gemm` 走的是「权重 FP8 存储 + bf16 计算」路径(AngelSlim),3090(算力 8.6)可用,
  不依赖 FP8 硬件单元;`--fp8_attention` 需要 SageAttention(无可靠 Windows wheel),**默认关**。
- draco(.drc 导出)本链路不消费,**不需要安装**。
- GroundingDINO 只在给 `labels_fg1/fg2` 前景分层时才需要,当前默认不分层,**不需要安装**。

## 3. 拉 HunyuanWorld-1.0 仓库与权重

```bat
git clone https://github.com/Tencent-Hunyuan/HunyuanWorld-1.0.git D:\models\HunyuanWorld-1.0
```

**Windows 必打补丁(2026-10-19 起由 worker 自动管理):** `stage_a_scenegen.py` 在每次运行前
**幂等自动应用**以下补丁(`--no-patch` 可跳过),无需手工操作;此处仅作原理记录。

```bat
:: 补丁 1(显存策略):模型级 CPU 卸载 → 逐层顺序卸载。
:: 原版 enable_model_cpu_offload 会把整个 FLUX transformer(bf16 ~24GB)整体搬上 GPU,24G 卡放不下,
:: 官方因此要求 FP8;但 AngelSlim FP8 量化在 Windows torch 2.5 上必现原生崩溃(access violation,
:: 位置随机漂移,CPU/GPU 路径均复现,探针排除算子与张量生命周期,判断为该平台构建的稳定性问题)。
:: 逐层顺序卸载按 block 流经 GPU,峰值显存仅几 GB,bf16 直接跑,不需要 FP8;代价是推理略慢。
python -c "p=r'D:\models\HunyuanWorld-1.0\hy3dworld\utils\inpaint_utils.py'; s=open(p,encoding='utf-8').read(); a='pipe.enable_model_cpu_offload()'; b='pipe.enable_sequential_cpu_offload()'; assert s.count(a)==1; open(p,'w',encoding='utf-8').write(s.replace(a,b)); print('patched')"

:: 补丁 2(超分分块):RealESRGAN 开 512 tile。
:: sr_inference 会吞掉 RuntimeError(如 CUDA OOM)只打印不保存文件,流水线静默继续到天空阶段才报
:: FileNotFoundError: full_image_sr.png;tile=512 后 24G 卡不再 OOM。
python -c "p=r'D:\models\HunyuanWorld-1.0\hy3dworld\models\layer_decomposer.py'; s=open(p,encoding='utf-8').read(); a='sr_utils.build_sr_model(scale=self.scale, gpu_id=0)'; b='sr_utils.build_sr_model(scale=self.scale, gpu_id=0, tile=512)'; assert s.count(a)==1; open(p,'w',encoding='utf-8').write(s.replace(a,b)); print('patched')"

:: 补丁 3(小图免分块):tile_process 在 Windows 小图(宽 ≤1536)上原生 stack overflow,
:: 小图不需要分块,直接 upsampler.tile = 0(worker 自动注入到 sr_utils.sr_inference)
:: 补丁 4(清理容错):逐层顺序卸载后参数在 meta 设备,_clear_models 的 pipeline.to('cpu')
:: 必抛 Cannot copy out of meta tensor;worker 自动改为 try/except 跳过
:: 补丁 5(utils3d 别名+image_uv shim):正确实现来自 GitHub utils3d_moge(PyPI 的 utils3d 是另一个包,
:: 没有 numpy 子模块);新版又删了 UniK3D 时代的 image_uv API。worker 自动把 hy3dworld 的 import
:: 改为 try/except 别名,并在 image_uv 缺失时注入归一化像素网格 shim
:: 补丁 6(faces int32):open3d Vector3iVector(int32)在 Windows 上对 int64 的千万级三角形索引
:: 直接 access violation;worker 自动补 astype(np.int32)(另需 open3d>=0.18,实测 0.20 修复)
:: 补丁 7(重建前释放模型):两个 FLUX 流水线 + SR + ZIM/GDINO 几十 GB 在分层阶段结束后仍驻留,
:: mesh 组合阶段 np.vstack 直接 ArrayMemoryError;worker 自动在 generate_world 前置 None + gc
```

> FP8 说明:任务参数/界面里的 FP8 开关**默认关**(Windows 不可用);`gemm_quantization_processor.py`
> 的两处量化补丁(fp32 / GPU 路径)经实测均不能绕开该崩溃,**不再需要打**——若已打过无副作用,无需回退。
> `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`:worker 会注入,但 **Windows torch 2.5 不支持**
> (启动有 not supported 警告,无害;Linux 有效)。

**ZIM 分割:仓库必须克隆在 HunyuanWorld-1.0 目录内(代码用相对路径 ./ZIM/zim_vit_l_2092 找权重)**
cd /d D:\models\HunyuanWorld-1.0
git clone https://github.com/naver-ai/ZIM.git ZIM
cd ZIM
pip install -e .
mkdir zim_vit_l_2092
cd zim_vit_l_2092
curl -L -O https://huggingface.co/naver-iv/zim-anything-vitl/resolve/main/zim_vit_l_2092/encoder.onnx
curl -L -O https://huggingface.co/naver-iv/zim-anything-vitl/resolve/main/zim_vit_l_2092/decoder.onnx
```

其余权重(HunyuanWorld-PanoDiT-Image / PanoInpaint-Scene / PanoInpaint-Sky、MoGe、GroundingDINO、FLUX 底座)
在首次运行时由 Hugging Face 自动下载;如需提前拉取,`huggingface-cli login` 后执行一次冒烟任务即可。

**FLUX 是 gated 仓库(必做)**:先用 HF 账号在浏览器里接受
[FLUX.1-Fill-dev](https://huggingface.co/black-forest-labs/FLUX.1-Fill-dev) 与
[FLUX.1-dev](https://huggingface.co/black-forest-labs/FLUX.1-dev) 的许可,再
`huggingface-cli login --token <HF_TOKEN>`(read 权限即可),否则首次运行报
`GatedRepoError: 401 ... restricted`。也可把 token 写进 `config.json#hf_token`(worker 注入阶段子进程)。

## 4. 配置 worker

```bat
cd /d D:\gpu_worker
copy config.example.json config.json
```

编辑 `config.json`:

```json
{
  "hyworld_repo": "D:/models/HunyuanWorld-1.0",
  "python": "C:/Users/<你>/miniconda3/envs/hyworld1/python.exe",
  "work_root": "D:/hyworld_jobs",
  "port": 8310,
  "token": "<换成长随机串>",
  "job_ttl_days": 7,
  "hf_home": "D:/hf_cache",
  "hf_endpoint": "",
  "defaults": { "fp8": true, "fp8_attention": false, "views": 24, "gs_steps": 8000, "max_gaussians": 1000000 }
}
```

- `python` 指向 hyworld1 env 的解释器(worker 本身也建议在该 env 里跑,Stage 子进程统一用它)。
- `token` 即 VideoAgents 侧要填的 X-Worker-Token。
- `hf_home`:HF 缓存目录(默认在 C 盘 `%USERPROFILE%\.cache\huggingface`,FLUX 等权重几十 GB,
  C 盘空间紧张时务必指到 D 盘;改已下载的缓存可整目录搬过去)。
- 访问不了 huggingface.co 时 `hf_endpoint` 填 `https://hf-mirror.com`。

## 5. 启动与自检

```bat
conda activate hyworld1
python D:\gpu_worker\hunyuan_worker.py
```

另开一个窗口:

```bat
curl -H "X-Worker-Token: <token>" http://localhost:8310/health
```

`ok: true` 即就绪(python / hyworld_repo / gpu.cuda 逐项打勾,含 GPU 名与显存)。

**冒烟任务**(袖珍:4 视图 + 200 步训练,端到端验证四阶段,约 10 分钟):
在 VideoAgents 侧任意场景跑
`python code/hunyuan_world.py --project <p> --scene <sid> --smoke`,
产物 `splats_*.spz` 能在场景预览页视窗打开即全链路通。

## 6. 常驻与网络

- **开机自启**:任务计划程序(Task Scheduler)新建基本任务 → 触发器「登录时/启动时」→
  操作 `C:\Users\<你>\miniconda3\envs\hyworld1\pythonw.exe D:\gpu_worker\hunyuan_worker.py`,
  起始于 `D:\gpu_worker`。
- **防火墙**:高级安全 Windows Defender 防火墙 → 入站规则 → 新建规则 → 端口 TCP 8310 → 允许
  (作用域建议限定内网子网)。**worker 只绑内网,不要暴露公网**;鉴权只有共享 token。
- 任务工作区 `work_root/jobs/<id>/` 完成/失败 7 天自动清理(`job_ttl_days`)。

## 7. VideoAgents 侧接线

控制台「🎨 生成模型」→「🌍 世界模型」→ **HunyuanWorld(远程)** 标签页:
填 worker 地址(`http://<GPU主机内网IP>:8310`)与 token → 「验证」显示 GPU/磁盘即通;
高级参数(视图数/训练步数/FP8)可留空走 worker 缺省。保存后,场景预览页「🌍 世界模型」板块的
「生成世界模型」即走本地算力(板块提示语会变为「不耗 credits」)。

## 8. 故障表

| 症状 | 处理 |
|---|---|
| `Failed to import diffusers.loaders.unet ... name 'nn' is not defined` | transformers 版本太新(新版要求 torch ≥2.6,对 2.5 禁用 PyTorch 支持):`pip install transformers==4.51.0 diffusers==0.34.0` |
| `import basicsr` 报 `No module named 'torchvision.transforms.functional_tensor'` | torchvision 0.18+ 把该模块改名 `_functional_tensor`,basicsr/realesrgan 还在按旧名 import。在 env 里补一个兼容 shim(不动第三方源码):`python -c "import pathlib, torchvision; p = pathlib.Path(torchvision.__file__).parent/'transforms'/'functional_tensor.py'; p.write_text('from ._functional_tensor import *\n', encoding='utf-8')"` |
| `import zim_anything` 报 `No module named 'easydict'` | `pip install easydict` |
| `UnicodeEncodeError: 'gbk' codec can't encode character`(打印 emoji 处) | Windows 控制台默认 GBK:worker 已对阶段子进程注入 `PYTHONUTF8=1`(stage_a 与 worker 双保险) |
| 深度预测阶段 `AttributeError: module 'utils3d' has no attribute 'numpy'` | 装了 PyPI 的 `utils3d`(没有 numpy 子模块):`pip uninstall -y utils3d` 后装 MoGe 官方依赖 `pip install "utils3d_moge @ git+https://github.com/EasternJournalist/utils3d-moge.git"` |
| gsplat 报 `where cl` 失败 / import 失败 | 没有预编译 wheel 匹配(torch 2.5 + Windows 官方只编到 pt24),须 JIT 编译:① 装 **VS Build Tools**(勾「使用 C++ 的桌面开发」,MSVC/CUDA 装非 C 盘无碍:用 vswhere 找 vcvars64.bat 路径)+ **CUDA Toolkit 12.4**;② 同一 Anaconda Prompt 里 `call <路径>\vcvars64.bat` → `where cl` 确认;③ `python -c` 调一次 rasterization 触发编译入缓存;之后普通环境/worker 直接用缓存 |
| gsplat 编译报 `cl: error D8021 :无效的数值参数"/Wno-attributes"` | gsplat 自带的告警抑制 flag 与 MSVC 不兼容:删掉 `gsplat\cuda\_backend.py` 里的 `-Wno-attributes`(worker 的 stage_c 已幂等自动处理,重装 gsplat 后重跑即可) |
| `PytorchStreamReader failed reading zip archive` | 模型权重缓存损坏(下载被截断或返回 HTML 错误页):删掉重新下。超分默认 scale=2 用 **RealESRGAN_x2plus.pth**:`del hy3dworld\utils\weights\RealESRGAN_x2plus.pth` 后 `curl -L -o <同路径> https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth`,并用 `python -c "import torch; torch.load(r'<同路径>', map_location='cpu')"` 验证可读 |
| FP8 量化阶段无声崩溃(exit -1,`Windows fatal exception: access violation`,位置在 42–67% 随机漂移) | AngelSlim FP8 在 Windows torch 2.5 的原生稳定性问题(探针已排除算子/尺寸/float8 生命周期单因素)。**不要走 FP8**:打「逐层顺序卸载」补丁(第 3 节补丁 1)后 bf16 直接跑,FP8 开关保持关 |
| 天空阶段 `FileNotFoundError: full_image_sr.png`(此前控制台有 `Error ... out of memory`) | RealESRGAN 超分 OOM 被 `sr_inference` 吞掉:打「SR tile=512」补丁(第 3 节补丁 2) |
| inpaint/VAE 阶段 `torch.OutOfMemoryError`(报十几 GB 空闲却分配不出 0.5–1 GB) | 显存碎片化(ZIM/GDINO/MoGe 常驻 + 顺序卸载搬运)。worker 已默认注入 `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`;4K 宽全景在 24G 上仍会撞墙,**输入全景建议 ≤2048×1024**(SR 后 ≤4096×2048 已是上限,冒烟用 1024×512) |
| `OSError: ... os error 112`(磁盘空间不足) | HF 缓存默认在 C 盘:`config.json` 加 `"hf_home": "D:/hf_cache"` 并重启 worker(已下载的缓存可整目录搬过去);D 盘也要留 ≥100 GB |
| `demo_scenegen.py` 下权重超时 | 配 `hf_endpoint=https://hf-mirror.com`,或先 `huggingface-cli download` 手动拉 |
| 全景重建 OOM(24G) | 确认任务参数 `fp8: true`;仍 OOM 把 `views` 降到 16、`max_gaussians` 降到 500000 |
| Windows 路径空格/中文导致子进程异常 | hyworld_repo / work_root 用纯英文无空格路径(如示例 D:\models\…) |
| worker 忙(409) | 单卡单任务;等当前任务完,或 `DELETE /worlds/<id>` 取消 |
| SPZ 在视窗里方向/比例不对 | 视窗 ⚙ 设置里调 yaw/尺度微调并「保存对齐微调」(与 Marble 同链路);若所有场景普遍偏 180°,反馈检查 Stage B 轴向约定 |
| 生成成功但视窗里世界糊成一片/颜色形状全错 | SPZ 段落顺序必须与官方 serializePackedGaussians 一致:**positions → alphas → colors → scales → rotations**(与 checkSizes 的检查顺序不同)。gpu_worker/spz_writer.py 2026-10-20 前版本按错顺序写,症状 = 位置正常但其余属性全错位;更新 spz_writer.py 并重跑 gs 阶段即可 |

## 9. License 注意(先读再用)

HunyuanWorld-1.0 权重与代码适用 **TENCENT HUNYUANWORLD-1.0 COMMUNITY LICENSE AGREEMENT**:
- **地域限制:不适用欧盟、英国、韩国**;
- 商用门槛:面向公众的产品/服务**上月活 >100 万**须向腾讯单独申请许可(hunyuan3d@tencent.com);
- 分发衍生作品须附带该协议文本与 Notice;服务需显著披露实际提供方、不得暗示腾讯背书。
VideoAgents 不分发模型权重——worker 由用户在自己主机自行部署,与「自带 API Key 调云端服务」同性质。
