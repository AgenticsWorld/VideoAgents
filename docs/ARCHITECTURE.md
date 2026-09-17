# VideoAgents 产品架构

## 目标与边界

本版本是一次性产品化迁移，不保留旧 Web API 的兼容层。系统面向单租户、多客户端使用：同一套 Python 服务可以同时被浏览器、桌面客户端和其他 API 客户端访问，不引入用户、租户或权限模型。

迁移遵循两个边界：

1. 视频创作领域保持稳定。`agents/`、`modules/`、`code/` 及 `data/` 不因产品外壳重构而改名或搬迁。
2. 产品接口使用 `/api/v1` 暴露，旧 `/api/*` 路径直接返回 404；除 URL 外，WebUI 沿用原服务的请求方法、请求体和响应行为。

## 运行架构

```mermaid
flowchart LR
    B["浏览器 / 移动浏览器"] -->|"同源 URL"| W["apps/web Python 网关"]
    E["Electron 外壳"] -->|"启动并访问同一服务"| W
    W -->|"原 WebUI 静态 HTML/CSS/JS"| U["apps/web/static"]
    W -->|"反向代理 /api/v1 与 SSE"| P["services/api FastAPI"]
    P --> R["现有调度与视频创作逻辑"]
    P --> S["SQLite 运行状态"]
    R --> A["agents / modules / code"]
    R --> D["data/projects 项目资源"]
```

浏览器模式下，`apps/web/server.py` 原样提供原 WebUI 静态资源，并把 `/api/v1` 反向代理到独立的 `services/api`，因此请求天然同源，不需要 CORS。Electron 不实现第二套静态服务或代理，只负责启动同一个 Python Web 网关并打开其 URL；也可让该网关代理 `VIDEOAGENTS_API_URL` 指定的远程 API。

`apps/web/static` 是从旧 `webui/static` 完整迁入的唯一界面源码。HTML 结构、CSS、图片与多语言资源不再经过 Vue/Vite 重建，仅修改页面 JavaScript 使用的 API URL；请求方法、请求体、响应处理、轮询和 SSE 行为保持原样。浏览器和 Electron 消费同一 URL 服务，体验不会因客户端分叉。

## 目录职责

```text
apps/
  web/             原 WebUI 静态页面、Python 静态服务与 API 反向代理
  desktop/         Electron 生命周期、原生能力、Web 服务进程、更新和安装包
services/
  api/             FastAPI 产品接口、Schema、SQLite 状态和启动入口
  runtime/         Agent 调度、项目业务、预览、Provider、版本与内部工具
agents/            83 个创作 Agent、工作流文档及 DAG
modules/           媒体、音频、执行引擎、项目版本等领域适配器
code/              仓库/成片校验和生产工具，不是前端源代码目录
data/projects/     运行时视频剪辑项目、输入资源和生成产物
data/.videoagents/ 可重建/可迁移的运行状态，例如 SQLite
tests/             Python 产品接口、领域完整性和版本管理测试
```

### `apps/web` 与 `apps/desktop` 的关系

`apps/web` 是完整的可运行 Web 客户端：`static/` 是实际界面，`server.py` 提供页面、同源 `/api/v1` 反向代理及少量本机操作。它既不依赖 Electron API，也不知道自己运行在桌面客户端还是普通浏览器中。`apps/desktop` 不复制页面、服务器实现或业务状态，只负责：

- 建立安全的 BrowserWindow 和最小化 preload 接口；
- 使用首次启动时独立下载的 Python 运行时启动 `apps/web/server.py`；该服务再启动本地 API，或代理 `VIDEOAGENTS_API_URL` 指定的远程 API；
- 安装包、应用生命周期和自动更新。

因此 Web 可以独立演进和通过 URL 使用，Desktop 只是它的一种承载方式。

### 为什么目前没有 `packages/`

在 JavaScript monorepo 中，`packages/` 通常放多个应用共享并独立版本化的库，例如：

- 从 OpenAPI 生成的 API 客户端和类型；
- Web 与 Electron preload 共用的 IPC 协议；
- 多个前端共用的设计系统组件；
- 统一日志、配置和测试工具。

目前只有一个 UI，Electron 只提供很小的原生桥接。过早拆包只会增加构建和版本关系，所以本次没有创建空壳 `packages/`。当出现第二个独立前端，或共享协议增长到需要单独测试/发布时，再将 API client、contracts、ui-kit 分别提升到 `packages/`，不会影响当前运行架构。

### 为什么使用 `services/api/`

仓库根目录已经代表 VideoAgents 产品，再用同名 `videoagents/` 表示其中一个后端部署单元，会混淆产品命名空间和服务职责。现在按部署形态划分：`apps/` 放置由用户直接运行的 Web/Electron 客户端，`services/` 放置可独立启动的长期运行服务，`services/api/` 明确表示公开 Python API 服务。

产品 API 与执行业务均已迁入 `services/`：`api/` 只负责公开协议与传输，`runtime/` 负责调度和业务实现。`agents/`、`modules/`、`code/` 和 `data/` 仍是稳定的领域与资源根目录，因此没有为形式统一而引入 `services/api/src/` 或改写这些领域路径。

`code/` 没有消失；它仍是工作流使用的校验和生产工具目录。`agents/` 与 `modules/` 也没有转移，因为它们是现有视频创作领域的一部分，而非产品 API 层。

## API 与状态

- 唯一公开前缀：`/api/v1`。
- OpenAPI：`/api/v1/openapi.json`，交互文档：`/api/v1/docs`。
- WebUI 通信契约沿用原 `webui/server.py`：读取使用 GET，状态变更使用 POST（线稿删除仍使用原 DELETE），请求体与响应结构保持不变，只有 URL 迁移到 `/api/v1`。
- 运行、人工确认和事件记录持久化到 SQLite；SSE 与原 WebUI 一样只推送当前连接后的实时事件，持久化字段不会注入前端事件载荷。
- `apps/web/server.py` 使用非缓冲读取转发 SSE。不能使用等待填满固定缓冲区的 `read(size)`，否则运行、聊天和线稿事件会滞留到刷新页面后才通过普通 GET 显示。
- `data/projects/` 始终是视频项目的资源目录，不会迁入数据库。SQLite 只存控制面状态，不存小说、图片、音视频或剪辑工程资产。

## 调度器与独立 Worker 的取舍

当前版本采用“同进程、清晰边界”：FastAPI 进程内运行现有调度逻辑，控制状态写入 SQLite。它的优点是对核心创作代码改动最少、部署和调试简单、Agent 的文件工作区语义不变，适合单租户单机执行、多个客户端共同控制的首个产品版本。

独立 Worker 模式会把 API 只保留为控制面，由一个或多个 Worker 从队列领取任务。它适合这些场景：

- API 更新或重启时，长任务不能被中断；
- 需要多台生成机器、GPU 调度或横向扩容；
- 需要任务租约、重试、优先级、节点失联恢复及资源配额；
- API 和执行环境需要不同的安全边界。

代价是必须引入可靠队列或数据库抢占协议、任务幂等、心跳/租约、取消传播、日志与产物汇聚。当前版本已把 API Schema、SQLite 状态和执行桥接分开，后续可以将桥接层替换成 Worker 协议，而无需再次改变 Web API；但现在不承担这套分布式复杂度。

## Git 的业务用途

项目不再维护业务文件版本库(原 `.version/` 嵌入式 pygit2 仓库与「从某版本克隆项目」功能已移除);运行时不依赖 `pygit2`,也不调用系统 `git`。Codex 参数中的 `--skip-git-repo-check` 只是第三方 CLI 的运行选项,与业务无关。

## 构建、更新和发布

- Web：没有 Node 编译步骤；`python apps/web/check.py`（或 `npm run build:web`）校验静态资源完整性及 API 路径，运行入口为 `videoagents-web`。
- Desktop：`npm run build:desktop` 编译 Electron 主进程；`electron-builder` 只将 Electron、Web 网关和业务后端生成 macOS DMG/ZIP、Windows NSIS/portable，不包含 Python。
- Python runtime：`make desktop-runtime` 以当前 Git 短 hash 为版本，生成当前平台的可迁移 CPython ZIP、SHA-256 和元数据。正式发布时 macOS arm64 与 Windows x64 环境使用 tag 版本，并上传到 `s3://agentics-prod/packages/video-agents/python/`。
- Agent 插件：官方声明式插件随服务端发布在 `backend/plugins/`；用户上传插件写入 `VIDEOAGENTS_DATA_DIR/plugins/`，不会修改只读的 Desktop App。插件通过 `/api/v1/plugins` 安装、启停和删除，动态注册的 Agent 与工作流继续使用同一运行状态、审批和事件 API。
- Desktop Release：`v*` tag 指向 `main` 中的提交时，同一工作流构建 macOS arm64 ZIP 和 Windows x64 NSIS，并规范化为 `mac/VideoAgents-<version>.zip` 与 `win/VideoAgents-<version>.zip`；两个目录中的 `VideoAgents.zip` 始终覆盖为最新版。
- GitHub Actions：Pull Request 由 CI 验证；`desktop.yaml` 只由 `main` 提交上的 `v*` tag 触发，构建 macOS/Windows 桌面端及对应 Python 环境，并向 GitHub Release 上传 4 个可区分平台的 ZIP 资产；Release 说明取自 `CHANGELOG.md` 中对应版本的段落（`scripts/release_notes.py`），缺少该段落时回退为 GitHub 自动生成的说明。
- 发布工作流支持可选签名密钥：macOS 使用 `MAC_CSC_LINK`/`MAC_CSC_KEY_PASSWORD` 及 Apple notarization secrets，Windows 使用 `WIN_CSC_LINK`/`WIN_CSC_KEY_PASSWORD`。未配置时仍可产出无签名测试包；面向普通用户发布及 macOS 自动更新时应配置签名。
- 客户端安装包不包含 Python。首次启动若没有可用环境，客户端读取 `https://s3.agentics.world/packages/video-agents/metadata.json`，选择 `python.mac.<arch>` 或 `python.win.<arch>`，下载 `packages/video-agents/python/macos-python-<version>-<arch>.zip` 或 Windows 对应包。
- 独立运行时安装到用户数据目录 `python-runtimes/versions/<version>/`，由 `active.json` 选择。已有可用环境时启动不访问远程索引；只有首次安装或桌面菜单手动更新才检查。下载后必须通过大小、SHA-256、平台、架构、版本和目录越界校验。
- GitHub Actions 不再由 `dev` push 触发打包。tag 发布使用 production environment 的 AWS 凭据和 `us-west-2`，先上传不可变的 Python/桌面版本包与两个桌面 latest 包，最后切换 `agentics-prod/packages/video-agents/metadata.json`。
- Release 客户端每次启动检查 `metadata.json` 的 `desktop` 版本。发现更高版本时先询问用户；确认后校验下载包，退出当前程序，再由独立 helper 更新 macOS `.app` 或启动 Windows NSIS，避免覆盖运行中程序。
- Python 运行时可以不更新 Electron 而单独切换版本；后端业务源码仍随应用发布，避免运行时依赖与业务协议漂移。API 大版本通过 URL 前缀演进。

本阶段不提供 Docker 或 `deploy/` 目录。远程访问时直接运行 Python 服务，并由可信网络或外部网关负责 TLS；由于本版本不实现账户权限系统，不应把服务裸露到公网。
