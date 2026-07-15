# VideoAgents

**Video creation for everyone — a fully automated Agent Team that delivers finished video for about $3 per minute.**

[中文说明](README.zh-CN.md) · [💬 Join our Discord](https://discord.gg/faaZCrWxCq)

VideoAgents is an open-source, file-based multi-agent production system for
turning long-form fiction into episodic video. It defines 83 specialized agents
across story adaptation, worldbuilding, characters, art direction, directing,
media generation, audio, editing, quality assurance, and publishing.

The repository includes a local Web console, a machine-readable workflow DAG,
agent role specifications, media-provider adapters, and reusable validation
tools. Project inputs and generated media stay in a local `data/projects/`
workspace and are not committed by default.

## Highlights

- 83 focused agents organized into 13 production departments
- Human approval gates for story, art direction, storyboards, cuts, and release
- File-based artifacts with per-project versioning and auditable QA records
- Claude CLI, Codex CLI, and OpenAI-compatible DeepAgents execution engines
- Configurable image, video, music, TTS, and object-storage providers
- A multilingual local Web console with preview and storyboard tools

## Requirements

- Python 3.10 or newer
- At least one agent engine: Claude CLI, Codex CLI, or an OpenAI-compatible model
- FFmpeg for media inspection, audio processing, and editing workflows
- Provider credentials only for the generation services you choose to use

## Quick Start

```bash
git clone https://github.com/AgenticsWorld/VideoAgents.git
cd VideoAgents
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python webui/server.py
```

Open <http://127.0.0.1:8630>. Select or create a project in the top bar, then
configure an execution engine and any generation providers you need.

Optional integrations can be installed as extras:

```bash
python -m pip install -e ".[deepagents]"
python -m pip install -e ".[storage]"
python -m pip install -e ".[dev]"
```

FFmpeg must be installed separately with your operating system's package
manager.

## Repository Layout

```text
agents/          Agent role specifications, workflow documentation, and DAG
code/            Repository-level validation and utility scripts
modules/         Media, audio, versioning, and DeepAgents adapters
webui/           Local FastAPI console and browser interface
tests/           Repository integrity and security-default tests
data/projects/   Local project inputs and generated artifacts (gitignored)
```

`agents/WORKFLOW.md` is the human-readable process authority.
`agents/workflow.yaml` is the orchestrator's machine-readable input.

## Configuration

The Web console stores provider settings in `webui/genconfig.json`. That file
can contain API keys and is excluded from Git. Runtime logs, conversations,
session state, project sources, and generated media are also excluded.

Public-release security defaults are deliberately conservative:

- `VIDEOAGENTS_HOST=127.0.0.1`
- `VIDEOAGENTS_PERMISSION_MODE=acceptEdits`
- Claude OAuth usage probing is disabled

See [`.env.example`](.env.example) for opt-in overrides and read
[`SECURITY.md`](SECURITY.md) before enabling LAN access or unrestricted agent
permissions.

## Development

```bash
python -m pip install -e ".[dev]"
pytest
```

The CI suite verifies Python syntax, JSON/YAML integrity, workflow agent
references, security defaults, and the Web console's basic API surface.

## License and Attribution

Copyright 2026 AgenticsWorld.

Licensed under the [Apache License 2.0](LICENSE). You may use, modify, and
redistribute the project, including commercially. Redistributions must retain
the license and copyright notices, preserve the attribution in [`NOTICE`](NOTICE),
and identify modified files as required by the license.

Citation metadata is available in [`CITATION.cff`](CITATION.cff).

## Trademarks and Services

Third-party product and provider names are used only to describe compatible
integrations. Their respective owners retain all trademark and service rights.
VideoAgents does not grant access to or redistribute any third-party model or
service.
