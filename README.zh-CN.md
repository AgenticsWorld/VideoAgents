# VideoAgents

**人人都能创作视频 —— 全自动智能体团队，成片成本仅约每分钟 3 美元。**

[English](README.md) · [💬 加入 Discord 社群](https://discord.gg/faaZCrWxCq)

VideoAgents 是一套开源、文件驱动的多 Agent 视频生产系统，用于把长篇小说改编为分集视频。项目包含 83 个专业 Agent，覆盖剧情改编、世界观、角色、美术、导演、音视频生成、剪辑、质量审核和发布。

仓库提供原生 HTML/CSS WebUI、Electron 桌面外壳、开放 FastAPI、机器可读的工作流 DAG、Agent 职责说明、媒体服务适配器和通用校验工具。小说原文、项目配置和生成媒体默认保存在本地 `data/projects/`，不会提交到 Git。

## 主要能力

- 87 个 Agent，分属 13 个制作部门
- 声明式 Agent 插件机制：扩展新工位与业务流程；内置衍生小说、双书融合两个官方插件，上传插件保存在可写的运行时数据目录
- 剧情、美术、分镜、成片和发布环节的人工确认闸门
- 文件化产物、项目内版本管理和可审计 QA 记录
- 支持 Claude CLI、Codex CLI 和 OpenAI 兼容的 DeepAgents 引擎
- 可配置图像、视频、音乐、TTS 与对象存储服务
- 多语言本地 Web 控制台、资产预览和手机手绘分镜
- 浏览器与 Electron 共用原 WebUI 的同一套静态页面
- 版本化 `/api/v1`、OpenAPI、持久运行状态与实时 SSE 通知

## 环境要求

- Python 3.10 或更高版本
- Node.js 22.12 或更高版本（仅开发或打包 Electron 客户端）
- 至少一种 Agent 执行引擎：Claude CLI、Codex CLI 或 OpenAI 兼容模型
- FFmpeg，用于媒体检查、音频处理和剪辑流程
- 仅需配置实际使用的生成服务凭据

## 快速开始

```bash
git clone https://github.com/AgenticsWorld/VideoAgents.git
cd VideoAgents
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python apps/web/check.py
python apps/web/server.py
```

完成可编辑安装后，也可以直接运行 `videoagents-web`。如只需要开放 API 服务，则运行 `videoagents-api`。

浏览器打开 <http://127.0.0.1:8630>，在顶部选择或创建项目，再配置执行引擎与所需生成服务。API 文档位于 <http://127.0.0.1:8630/api/v1/docs>。

WebUI 使用 `npm run dev:web`（实际启动 Python Web 网关）；桌面客户端开发使用 `npm ci && npm run dev:desktop`。桌面客户端默认启动同一个 Web 网关，也可通过 `VIDEOAGENTS_API_URL=https://host` 反向代理远程 API。发布安装包不包含 Python；首次启动且本机没有可用环境时，客户端从 `https://s3.agentics.world/packages/video-agents/metadata.json` 读取当前平台版本并下载安装。Claude、Codex、Kimi、Pi、OpenCode、FFmpeg、模型和 GPU 环境仍按需独立安装。Windows x64 桌面客户端在本机没有可用 FFmpeg 时，可从同一发布源自动下载安装（不需要包管理器和管理员权限），版本钉在 `apps/desktop/ffmpeg-artifact.json`。

桌面开发前请确认 `node --version` 不低于 `.nvmrc` 指定的版本；使用 nvm 时先执行 `nvm use`。切换 Node 大版本后必须重新执行 `npm ci`，避免保留由旧 Node 生成的不完整 Electron 安装。

也可以完全通过 Makefile 操作桌面端：首次安装依赖使用 `make desktop-install`，开发启动使用 `make desktop-dev`（或 `make desktop-run`），只编译使用 `make desktop-build`；`make desktop-runtime` 使用当前 Git 短 hash 构建独立 Python ZIP，产物写入 `apps/desktop/.runtime-packages/`。`make desktop` 只生成不含 Python 的客户端安装包，产物写入 `apps/desktop/release/`。

Python 运行时与 Electron 应用完全分开版本化。运行时写入用户数据目录的 `python-runtimes/versions/<version>/`，通过 `active.json` 激活。已有可用环境时启动过程不会访问版本索引；仅首次缺少环境，或用户从桌面菜单选择“检查并更新 Python 环境”时才检查最新版本。下载过程校验大小、SHA-256、平台、架构、版本和可执行路径。开发/诊断时仍可用 `VIDEOAGENTS_PYTHON` 显式覆盖。

仅当 `v*` tag 指向 `main` 中的提交时，工作流才构建桌面端与 Python 环境；两类包统一使用 tag 版本号并发布到 `packages/video-agents/`。Python 环境位于 `python/`，macOS 与 Windows 的带版本桌面 ZIP 分别位于 `mac/` 和 `win/`，共享更新索引为 `metadata.json`；两个桌面目录还会用 `VideoAgents.zip` 覆盖发布最新版本。Release 客户端启动时先读取该 metadata：高于当前版本但仍受支持时提示可选更新；低于其中的 `desktop.minimumVersion` 时，在创建主窗口和启动本地后端之前进入不可跳过的强制更新流程。发布任务从 GitHub Actions 的 `MINIMUM_DESKTOP_VERSION` environment/repository variable 读取该值，未配置时默认为 `1.0.0`；格式错误或最低版本高于本次 release 版本时，发布任务会失败。

macOS 桌面端从 Finder 启动时会读取用户登录 Shell 的 `PATH`，并补充 Homebrew、`~/.local/bin`、Kimi、OpenCode、Grok、Volta、pnpm 等常见 CLI 目录。因此终端中已安装的 `claude`、`codex`、`kimi`、`pi`、`opencode`、`grok` 会被本地 Python 服务及其 Agent 子进程继承，无需把第三方 CLI 打入客户端安装包。切换到 `pi` 引擎后，语言模型下拉会通过 `pi --list-models` 动态读取当前 Pi 登录凭证实际可用的渠道与模型。`opencode` 引擎同样通过 `opencode models` 动态读取；`grok` 引擎（Grok Build CLI，安装见 https://grok.com/build，`grok login` 登录）通过 `grok models` 动态读取。各引擎（deepagents 除外）的语言模型下拉均默认「智能分配」：按 Agent 任务复杂度自动选模型（如 opencode 引擎把创作核心 Agent 派给 DeepSeek V4 Pro、其余派给 DeepSeek V4 Flash），切换引擎或语言模型会自动同步全部 Agent 的模型设置。

顶栏切到某个 CLI 引擎（或开屏）时若检测到命令未安装或未登录，会弹出引导窗口：「一键安装」在后台运行该 CLI 的官方安装命令（`curl -fsSL <官方脚本> | bash`，Windows 为 `irm <官方脚本> | iex`；OpenCode 在 Windows 上走 `npm install -g opencode-ai`），日志实时显示，失败可「在终端中安装」以便回答脚本的交互提问；装好后「登录」会弹出终端窗口运行该 CLI 自己的登录命令（如 `claude auth login`、`codex login`、`kimi login`），用户在浏览器中用自己的账号授权，程序轮询到已登录即关窗，全程不经手令牌。安装子进程不带本程序与各渠道的密钥环境变量；一键安装与登录只对本机访问开放。

本地 `make desktop` 默认设置 `CSC_IDENTITY_AUTO_DISCOVERY=false`，不会读取 macOS Keychain 中的 Apple 开发者证书，也不会签名或公证。正式发布签名只由 GitHub Actions 在显式提供 `CSC_LINK`、Apple ID 等 secrets 时启用。

如需无人值守的全自动流水线，用全自动模式启动，`claude` 引擎的 Agent 执行
命令时不再弹审批（`codex`、`pi` 与 `deepagents` 引擎本就免审批）：

```bash
make run-auto
```

该模式下 Agent 可执行任意命令，请仅在可信机器上使用。调度层越界防护
（`orchestrator_guard` hook 与 `modules/genmedia.py` 运行时守卫）与权限模式
无关，依然生效。

可选集成：

```bash
python -m pip install -e ".[storage]"
python -m pip install -e ".[dev]"
```

deepagents 引擎需要 Python ≥3.11。若主环境是 3.10，请改用
`make install-deepagents` 创建专用的 `.venv-deepagents`（运行时自动发现）；
也可设置 `DEEPAGENTS_PY` 指向任意已安装 deepagents 的解释器。

如需为该独立环境指定 Python 解释器：

```bash
make install-deepagents DEEPAGENTS_PYTHON=python3.11
```

如果通过 asdf 管理 Python，可显式选择已安装的 3.11 版本：

```bash
ASDF_PYTHON_VERSION=3.11.12 make install-deepagents DEEPAGENTS_PYTHON=python
```

DeepAgents 默认按 128000 token 上下文运行，单次输出上限 8192 token，图递归上限为 250
（调度类 Agent 为 500）。runner 还会为系统提示词和工具 schema 预留 16384 token，首轮输入
超限直接拒绝；约 48000 个估算 token 时压缩历史，只保留约 8000 个估算 token；每次 shell 工具输出最多返回 24000
字节。为避免无界扫描和递归放大上下文，`glob` 工具与自动泛用子 Agent 默认关闭。
只有当本地推理服务实际配置了相同或更大的上下文窗口时，才覆盖下面的值：

```bash
DEEPAGENTS_CONTEXT_WINDOW=131072 DEEPAGENTS_MAX_OUTPUT_TOKENS=8192 ./run.sh
```

还可调整 `DEEPAGENTS_RECURSION_LIMIT` 与 `DEEPAGENTS_MAX_TOOL_OUTPUT_BYTES`；提高任一值都会
增加运行时间或上下文压力。撞到 recursion_limit 时 runner 会从最后检查点自动续跑，
最多 `DEEPAGENTS_MAX_CONTINUATIONS`（默认 3）轮，仍未收敛才报错。

FFmpeg 需要通过操作系统包管理器单独安装。

## 目录结构

```text
agents/          Agent 职责、工作流文档和 DAG
code/            仓库级校验与工具脚本
modules/         媒体、音频、版本管理和 DeepAgents 适配器
services/api/    可独立运行的 Python API 服务、Schema 和运行状态
services/runtime/ Agent 调度、项目业务、Provider 与内部运行工具
apps/web/        原 WebUI 静态页面、Python 静态服务与同源 API 反向代理
apps/desktop/    Electron 外壳、Python Web 服务生命周期、打包与更新
tests/           回归测试(pytest):模块、流水线 CLI 与仓库完整性
data/projects/   视频剪辑项目资源、输入与生成产物，默认不入 Git
```

`agents/WORKFLOW.md` 是供人阅读的流程权威，`agents/workflow.yaml` 是 orchestrator 的机器执行输入。

完整的组件关系、目录决策、`packages/` 的适用时机、内嵌调度与独立 Worker 的区别见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

## 配置与安全

运行配置和 SQLite 控制状态保存在 `data/.videoagents/`，项目素材始终保存在 `data/projects/`。这些目录可能包含 API Key、运行日志、小说原文和生成媒体，均不会入库。

公开版默认：

- 仅监听 `127.0.0.1`
- Agent 权限模式为 `acceptEdits`
- 不读取 Claude OAuth 凭据进行用量探测

需要局域网手绘分镜或更高自动化权限时，参考 [`.env.example`](.env.example) 显式开启。启用前请阅读 [`SECURITY.md`](SECURITY.md)，不要把控制台直接暴露到公网。

## 开发

```bash
python -m pip install -e ".[dev]"
pytest
npm run test:web   # 浏览器端白模 / 导演台代码的 Node 检查
```

CI 跑 `tests/` 全量:模块与流水线 CLI 回归、仓库完整性(JSON/YAML、工作流 Agent 引用、安全默认值)、HTTP 层(路由表、离线 GET 冒烟、Web 控制台页面)。需要 `ffmpeg` 的用例在未安装时自动跳过。

## 许可证与引用

Copyright 2026 AgenticsWorld.

项目采用 [GNU 通用公共许可证第 3 版](LICENSE)（`GPL-3.0-only`）。允许使用、修改、分发和商业使用；分发本项目或其修改版时，必须以同一许可证发布并提供对应的源代码，保留许可证与版权声明（含 [`NOTICE`](NOTICE)），并标明所做修改及修改日期。本软件不附带任何担保。

v1.0.38 及更早的发布版本以 Apache License 2.0 发布，这些版本仍可按该许可证使用。

标准引用信息见 [`CITATION.cff`](CITATION.cff)。
