# 开发指南

[English](DEVELOPMENT.md)

本文汇总从源码运行 VideoAgents、开发与打包桌面客户端、配置执行引擎和发版的操作细节。首次使用请先看 README 的[快速开始](../README.zh-CN.md#quickstart)。

## 环境要求

- Python 3.10 或更高版本
- Node.js 22.12 或更高版本（仅开发或打包 Electron 客户端）
- 至少一种 Agent 执行引擎：Claude CLI、Codex CLI 或 OpenAI 兼容模型
- FFmpeg，用于媒体检查、音频处理和剪辑流程
- 仅需配置实际使用的生成服务凭据

## 从源码运行

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

## 桌面客户端

### 开发

WebUI 使用 `npm run dev:web`（实际启动 Python Web 网关）；桌面客户端开发使用 `npm ci && npm run dev:desktop`。桌面客户端默认启动同一个 Web 网关，也可通过 `VIDEOAGENTS_API_URL=https://host` 反向代理远程 API。发布安装包不包含 Python；首次启动且本机没有可用环境时，客户端从 `https://s3.agentics.world/packages/video-agents/metadata.json` 读取当前平台版本并下载安装。Claude、Codex、Kimi、Pi、OpenCode、FFmpeg、模型和 GPU 环境仍按需独立安装。Windows x64 桌面客户端在本机没有可用 FFmpeg 时，可从同一发布源自动下载安装（不需要包管理器和管理员权限），版本钉在 `apps/desktop/ffmpeg-artifact.json`。

桌面开发前请确认 `node --version` 不低于 `.nvmrc` 指定的版本；使用 nvm 时先执行 `nvm use`。切换 Node 大版本后必须重新执行 `npm ci`，避免保留由旧 Node 生成的不完整 Electron 安装。

也可以完全通过 Makefile 操作桌面端：首次安装依赖使用 `make desktop-install`，开发启动使用 `make desktop-dev`（或 `make desktop-run`），只编译使用 `make desktop-build`；`make desktop-runtime` 使用当前 Git 短 hash 构建独立 Python ZIP，产物写入 `apps/desktop/.runtime-packages/`。`make desktop` 只生成不含 Python 的客户端安装包，产物写入 `apps/desktop/release/`。

### Python 运行时

Python 运行时与 Electron 应用完全分开版本化。运行时写入用户数据目录的 `python-runtimes/versions/<version>/`，通过 `active.json` 激活。已有可用环境时启动过程不会访问版本索引；仅首次缺少环境，或用户从桌面菜单选择“检查并更新 Python 环境”时才检查最新版本。下载过程校验大小、SHA-256、平台、架构、版本和可执行路径。开发/诊断时仍可用 `VIDEOAGENTS_PYTHON` 显式覆盖。

### 发版

仅当 `v*` tag 指向 `main` 中的提交时，工作流才构建桌面端与 Python 环境；两类包统一使用 tag 版本号并发布到 `packages/video-agents/`。Python 环境位于 `python/`，macOS 与 Windows 的带版本桌面 ZIP 分别位于 `mac/` 和 `win/`，共享更新索引为 `metadata.json`；两个桌面目录还会用 `VideoAgents.zip` 覆盖发布最新版本。Release 客户端启动时先读取该 metadata：高于当前版本但仍受支持时提示可选更新；低于其中的 `desktop.minimumVersion` 时，在创建主窗口和启动本地后端之前进入不可跳过的强制更新流程。发布任务从 GitHub Actions 的 `MINIMUM_DESKTOP_VERSION` environment/repository variable 读取该值，未配置时默认为 `1.0.0`；格式错误或最低版本高于本次 release 版本时，发布任务会失败。

### 签名

本地 `make desktop` 默认设置 `CSC_IDENTITY_AUTO_DISCOVERY=false`，不会读取 macOS Keychain 中的 Apple 开发者证书，也不会签名或公证。正式发布签名只由 GitHub Actions 在显式提供 `CSC_LINK`、Apple ID 等 secrets 时启用。

## 执行引擎

### CLI 发现与模型下拉

macOS 桌面端从 Finder 启动时会读取用户登录 Shell 的 `PATH`，并补充 Homebrew、`~/.local/bin`、Kimi、OpenCode、Grok、Volta、pnpm 等常见 CLI 目录。因此终端中已安装的 `claude`、`codex`、`kimi`、`pi`、`opencode`、`grok`、`agy`（Antigravity CLI）会被本地 Python 服务及其 Agent 子进程继承，无需把第三方 CLI 打入客户端安装包。切换到 `pi` 引擎后，语言模型下拉会通过 `pi --list-models` 动态读取当前 Pi 登录凭证实际可用的渠道与模型。`opencode` 引擎同样通过 `opencode models` 动态读取；`grok` 引擎（Grok Build CLI，安装见 https://grok.com/build，`grok login` 登录）通过 `grok models` 动态读取；`antigravity` 引擎（Antigravity CLI，命令 `agy`，安装见 https://antigravity.google/docs/getting-started?tab=cli，在终端运行一次 `agy` 完成登录）通过 `agy models` 动态读取。各引擎（agentics 除外）的语言模型下拉均默认「智能分配」：按 Agent 任务复杂度自动选模型（如 opencode 引擎把创作核心 Agent 派给 DeepSeek V4 Pro、其余派给 DeepSeek V4 Flash），切换引擎或语言模型会自动同步全部 Agent 的模型设置。

### 安装与登录引导

顶栏切到某个 CLI 引擎（或开屏）时若检测到命令未安装或未登录，会弹出引导窗口：「一键安装」在后台运行该 CLI 的官方安装命令（`curl -fsSL <官方脚本> | bash`，Windows 为 `irm <官方脚本> | iex`；OpenCode 装 OpenCode 2：`https://opencode.ai/v2/install`，Windows 上走 `npm install -g @opencode/cli`），日志实时显示，失败可「在终端中安装」以便回答脚本的交互提问；装好后「登录」会弹出终端窗口运行该 CLI 自己的登录命令（如 `claude auth login`、`codex login`、`kimi login`），用户在浏览器中用自己的账号授权，程序轮询到已登录即关窗，全程不经手令牌。安装子进程不带本程序与各渠道的密钥环境变量；一键安装与登录只对本机访问开放。

### 运行中插话(Ctrl+Enter)

控制台输入框按 Enter 是新建一单(同一 Agent 上一单没跑完就排队);按 Ctrl+Enter(macOS 也可 ⌘+Enter)
是「插话」:`POST /api/v1/runs/steer` 把消息送进该 Agent 在当前项目里正在执行的那一单,
在当前工具调用结束后、下一次调用模型前送达(长命令要等它跑完)。各引擎的通道(`services/runtime/run_steer.py`):

- `claude`:派单改用 `-p --input-format stream-json --replay-user-messages`,提示词与插话都作为 stdin 的
  user 消息写入,出 `result` 后由宿主关 stdin;插话撞上本轮最后一次模型输出时 CLI 会接着再跑一轮。
- `pi` 与 `agentics`:派单改用 `--mode rpc`,提示词走 `prompt` 命令,插话走 `prompt` + `streamingBehavior: "steer"`,
  `agent_settled` 后关 stdin;会话 id 取 `get_state` 的回执。
- `opencode` 2.x:另起 `opencode api --standalone` 往会话 inbox 写 `delivery: "steer"`、`resume: false` 的输入,
  运行中的 `--standalone` 进程在下一步边界取走;收尾时仍在 inbox 里的撤回。
- `codex`、`kimi`、`grok`、`antigravity` 与 opencode 1.x 没有运行中输入通道:插话并进该 Agent 排队中的那一单,
  没有排队的就照常新建一单,对话里标明原因。

排队中或进程还没起来时收到的插话并进提示词;任务结束前没送达的补派一单(手动停止的只在对话里记一笔)。

### 全自动模式

如需无人值守的全自动流水线，用全自动模式启动，`claude` 引擎的 Agent 执行
命令时不再弹审批（`codex`、`pi` 与 `agentics` 引擎本就免审批）：

```bash
make run-auto
```

该模式下 Agent 可执行任意命令，请仅在可信机器上使用。调度层越界防护
（`orchestrator_guard` hook 与 `modules/genmedia.py` 运行时守卫）与权限模式
无关，依然生效。

## 可选集成

可选集成：

```bash
python -m pip install -e ".[storage]"
python -m pip install -e ".[dev]"
```

## Agentics 引擎（内置 pi）

`agentics` 引擎（设置、接口与 `dispatch.py` 里的引擎 id 仍是 `deepagents`）就是随程序打包的
[Pi Coding Agent](https://github.com/earendil-works/pi)，版本钉在 `vendor/pi/package.json` 与
`vendor/pi/package-lock.json`；它与用户自己安装、自己登录的 `pi` 互不相干，后者仍作为 `pi` 引擎可选。

- **桌面端**：打包时运行 `apps/desktop/scripts/vendor_pi.py --prune`（`npm ci` 后去掉 source map
  与其他平台的原生组件），把 `vendor/pi` 作为 `backend/vendor/pi` 打进安装包。客户端不另带 Node.js：
  服务端用桌面程序本身（`VIDEOAGENTS_NODE`）加 `ELECTRON_RUN_AS_NODE=1` 启动 pi。
- **源码运行**：先执行一次 `make install-pi`（需要 PATH 上有 Node.js 22.19 或更新版本；也可用
  `VIDEOAGENTS_NODE` 指定 Node）。`make desktop-dev` 会自动执行这一步。

语言模型取自 🎨 生成模型 →「语言模型/Agentics」：Agentics（已登录的桌面端账号）、本地 OpenAI 兼容端点，
以及默认隐藏（已是生效渠道时才显示）的云端模型与 OpenRouter。每次运行在
`<数据目录>/.videoagents/agentics-pi/runs/` 下建一个独立的 pi 配置目录，按生效渠道写 `models.json`
（API Key 只写成 `$VIDEOAGENTS_LLM_API_KEY`，真值经环境变量传入），运行结束即删除；会话存在
`agentics-pi/sessions/`，用 `--session` 续接。pi 带 `--no-extensions --no-skills --no-prompt-templates
--no-context-files` 启动，用户自己的 `~/.pi/agent` 配置不会生效。

模型按 128000 token 上下文、单次输出上限 65536 token（本地模型渠道 8192）声明，上下文将满前由 pi
自动压缩会话。单次输出上限由思考和正文共用，pi 还会按上下文余量收窄。末条回复被这个上限截断
（`stopReason=length`）时，运行判为失败。只有当模型服务确实支持更大的值时才覆盖：

```bash
AGENTICS_CONTEXT_WINDOW=262144 AGENTICS_MAX_OUTPUT_TOKENS=131072 ./run.sh
```

模型能看图时按图片输入声明（`"input": ["text", "image"]`）：Agentics 与 OpenRouter 渠道按 OpenRouter 模型目录的
`architecture.input_modalities` 判断，本地与云端渠道按该页的「图片输入」勾选（默认勾）。不这样声明，成员读到的图片会被
pi 整张丢掉。

## FFmpeg

FFmpeg 需要通过操作系统包管理器单独安装。

## 测试

```bash
python -m pip install -e ".[dev]"
pytest
npm run test:web   # 浏览器端白模 / 导演台代码的 Node 检查
```

CI 跑 `tests/` 全量:模块与流水线 CLI 回归、仓库完整性(JSON/YAML、工作流 Agent 引用、安全默认值)、HTTP 层(路由表、离线 GET 冒烟、Web 控制台页面)。需要 `ffmpeg` 的用例在未安装时自动跳过。
