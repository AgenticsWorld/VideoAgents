# VideoAgents

**Video creation for everyone — a fully automated Agent Team that delivers finished video for about $3 per minute.**

[中文说明](README.zh-CN.md) · [💬 Join our Discord](https://discord.gg/faaZCrWxCq)

VideoAgents is an open-source, file-based multi-agent production system for
turning long-form fiction into episodic video. It defines 83 specialized agents
across story adaptation, worldbuilding, characters, art direction, directing,
media generation, audio, editing, quality assurance, and publishing.

The repository includes the original HTML/CSS WebUI, an Electron shell, an open FastAPI service, a machine-readable workflow DAG,
agent role specifications, media-provider adapters, and reusable validation
tools. Project inputs and generated media stay in a local `data/projects/`
workspace and are not committed by default.

## Highlights

- 83 focused agents organized into 13 production departments
- Declarative agent plugins add new roles and workflows; official derivative-fiction and fusion-fiction teams are bundled, while uploaded plugins live in the writable runtime data directory
- Human approval gates for story, art direction, storyboards, cuts, and release
- File-based artifacts with per-project versioning and auditable QA records
- Claude CLI, Codex CLI, and OpenAI-compatible DeepAgents execution engines
- Configurable image, video, music, TTS, and object-storage providers
- A multilingual local Web console with preview and storyboard tools
- The same original static WebUI shared by browsers and Electron
- Versioned `/api/v1`, OpenAPI, durable run state, and resumable SSE events

## Requirements

- Python 3.10 or newer
- Node.js 22.12 or newer only for Electron development and packaging
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
python apps/web/check.py
python apps/web/server.py
```

After installation, the equivalent `videoagents-web` command is also available. Use `videoagents-api` when only the open API service is needed.

Open <http://127.0.0.1:8630>. API documentation is at
<http://127.0.0.1:8630/api/v1/docs>. Select or create a project in the top bar, then
configure an execution engine and any generation providers you need.

Use `npm run dev:web` to start the Python Web gateway and `npm ci && npm run dev:desktop` for the
desktop shell. Electron starts the same gateway by default or proxies the remote API
specified by `VIDEOAGENTS_API_URL`. Release packages do not contain Python. On the first launch
without an installed runtime, the client reads `https://s3.agentics.world/packages/video-agents.json`
and downloads the matching package. Claude, Codex, FFmpeg, models, and GPU environments remain
optional external installations.

Before desktop development, make sure `node --version` meets `.nvmrc`; with nvm, run `nvm use` first. Run `npm ci` again after switching Node major versions so an incomplete Electron installation from the old runtime is not reused.

The equivalent Makefile targets are `make desktop-install`, `make desktop-dev` (or `make desktop-run`) for development, and `make desktop-build` for compilation. `make desktop-runtime` creates a separately distributable Python ZIP using the current short Git hash under `apps/desktop/.runtime-packages/`. `make desktop` creates the client-only artifacts under `apps/desktop/release/`.

The Python runtime and Electron application are versioned independently. Runtimes are installed under the user data directory at `python-runtimes/versions/<version>/` and selected through `active.json`. Startup never checks for updates when a valid runtime exists; the index is read only on first installation or when the user chooses the manual Python update menu item. Package size, SHA-256, platform, architecture, version, and executable containment are validated before activation. `VIDEOAGENTS_PYTHON` remains an explicit development and diagnostics override.

Desktop packages produced from `dev` retain product version `1.0.2`; the short Git hash is stored separately as `buildHash`. They are published at the stable URLs `packages/video-agents-mac.zip` (Apple Silicon arm64) and `packages/video-agents-win.zip` (Windows x64, containing the NSIS installer). A Dev client reads the same index on every launch and prompts only when `desktop.mac/win.buildHash` differs from its embedded build hash. On approval it verifies and downloads the ZIP, then replaces the macOS app or silently runs the Windows upgrade after the current process exits. The Release channel remains separate from this Dev S3 update channel.

When launched from Finder, the macOS desktop client imports the login shell `PATH` and supplements common Homebrew, `~/.local/bin`, Kimi, Volta, and pnpm locations. Existing `claude`, `codex`, and `kimi` installations are therefore inherited by the local Python service and its Agent child processes instead of being bundled into the client.

Local `make desktop` packaging defaults to `CSC_IDENTITY_AUTO_DISCOVERY=false`, so it never reads an Apple developer certificate from the macOS Keychain and performs no signing or notarization. Release signing is enabled only in GitHub Actions when `CSC_LINK` and the Apple secrets are explicitly supplied.

For unattended pipelines, start the console in full-auto mode so agents on the
`claude` engine can run shell commands without approval prompts (the `codex`
and `deepagents` engines already run unattended):

```bash
make run-auto
```

This lets agents execute any command, so use it only on a trusted machine. The
orchestration-layer guardrails (the `orchestrator_guard` hook and the runtime
check in `modules/genmedia.py`) are independent of the permission mode and stay
active.

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
services/api/    Independently runnable Python API service, schemas, and runtime state
services/runtime/ Agent scheduling, project services, providers, and runtime tools
apps/web/        Original static WebUI plus Python static server/API reverse proxy
apps/desktop/    Electron shell, Python Web lifecycle, packaging, and updates
tests/           Repository integrity and security-default tests
data/projects/   Video project resources, inputs, and generated artifacts
```

`agents/WORKFLOW.md` is the human-readable process authority.
`agents/workflow.yaml` is the orchestrator's machine-readable input.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for component relationships,
directory decisions, the future role of `packages/`, and inline scheduler versus worker trade-offs.

## Configuration

Runtime configuration and SQLite control state live in `data/.videoagents/`;
project media remains in `data/projects/`. Both can contain private data and are excluded from Git.

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
