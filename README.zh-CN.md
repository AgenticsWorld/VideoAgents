# VideoAgents

**人人都能创作视频 —— 全自动智能体团队，成片成本仅约每分钟 3 美元。**

[English](README.md) · [💬 加入 Discord 社群](https://discord.gg/faaZCrWxCq)

VideoAgents 是一套开源、文件驱动的多 Agent 视频生产系统，用于把长篇小说改编为分集视频。项目包含 83 个专业 Agent，覆盖剧情改编、世界观、角色、美术、导演、音视频生成、剪辑、质量审核和发布。

仓库提供本地 Web 控制台、机器可读的工作流 DAG、Agent 职责说明、媒体服务适配器和通用校验工具。小说原文、项目配置和生成媒体默认保存在本地 `data/projects/`，不会提交到 Git。

## 主要能力

- 83 个 Agent，分属 13 个制作部门
- 剧情、美术、分镜、成片和发布环节的人工确认闸门
- 文件化产物、项目内版本管理和可审计 QA 记录
- 支持 Claude CLI、Codex CLI 和 OpenAI 兼容的 DeepAgents 引擎
- 可配置图像、视频、音乐、TTS 与对象存储服务
- 多语言本地 Web 控制台、资产预览和手机手绘分镜

## 环境要求

- Python 3.10 或更高版本
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
python webui/server.py
```

浏览器打开 <http://127.0.0.1:8630>，在顶部选择或创建项目，再配置执行引擎与所需生成服务。

如需无人值守的全自动流水线，用全自动模式启动，`claude` 引擎的 Agent 执行
命令时不再弹审批（`codex` 与 `deepagents` 引擎本就免审批）：

```bash
make run-auto
```

该模式下 Agent 可执行任意命令，请仅在可信机器上使用。调度层越界防护
（`orchestrator_guard` hook 与 `modules/genmedia.py` 运行时守卫）与权限模式
无关，依然生效。

可选集成：

```bash
python -m pip install -e ".[deepagents]"
python -m pip install -e ".[storage]"
python -m pip install -e ".[dev]"
```

FFmpeg 需要通过操作系统包管理器单独安装。

## 目录结构

```text
agents/          Agent 职责、工作流文档和 DAG
code/            仓库级校验与工具脚本
modules/         媒体、音频、版本管理和 DeepAgents 适配器
webui/           FastAPI 本地控制台与浏览器界面
tests/           仓库完整性和安全默认值测试
data/projects/   本地项目输入与生成产物，默认不入 Git
```

`agents/WORKFLOW.md` 是供人阅读的流程权威，`agents/workflow.yaml` 是 orchestrator 的机器执行输入。

## 配置与安全

Web 控制台把生成服务配置保存在 `webui/genconfig.json`。该文件可能包含 API Key，已被 Git 忽略。运行日志、对话、会话状态、小说原文和生成媒体同样不会入库。

公开版默认：

- 仅监听 `127.0.0.1`
- Agent 权限模式为 `acceptEdits`
- 不读取 Claude OAuth 凭据进行用量探测

需要局域网手绘分镜或更高自动化权限时，参考 [`.env.example`](.env.example) 显式开启。启用前请阅读 [`SECURITY.md`](SECURITY.md)，不要把控制台直接暴露到公网。

## 开发

```bash
python -m pip install -e ".[dev]"
pytest
```

CI 会检查 Python 语法、JSON/YAML 完整性、工作流 Agent 引用、安全默认值和 Web 控制台基础 API。

## 许可证与引用

Copyright 2026 AgenticsWorld.

项目采用 [Apache License 2.0](LICENSE)。允许使用、修改、分发和商业使用；再分发时需要保留许可证与版权声明、保留 [`NOTICE`](NOTICE) 中的来源署名，并按许可证要求标明修改。

标准引用信息见 [`CITATION.cff`](CITATION.cff)。
