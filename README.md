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
- File-based artifacts with auditable QA records
- Claude CLI, Codex CLI, and OpenAI-compatible DeepAgents execution engines
- Configurable image, video, music, TTS, and object-storage providers
- A multilingual local Web console with preview and storyboard tools
- The same original static WebUI shared by browsers and Electron
- Versioned `/api/v1`, OpenAPI, durable run state, and real-time SSE events

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
without an installed runtime, the client reads `https://s3.agentics.world/packages/video-agents/metadata.json`
and downloads the matching package. Claude, Codex, Kimi, Pi, OpenCode, Grok, FFmpeg, models, and GPU environments remain
optional external installations.

Before desktop development, make sure `node --version` meets `.nvmrc`; with nvm, run `nvm use` first. Run `npm ci` again after switching Node major versions so an incomplete Electron installation from the old runtime is not reused.

The equivalent Makefile targets are `make desktop-install`, `make desktop-dev` (or `make desktop-run`) for development, and `make desktop-build` for compilation. `make desktop-runtime` creates a separately distributable Python ZIP using the current short Git hash under `apps/desktop/.runtime-packages/`. `make desktop` creates the client-only artifacts under `apps/desktop/release/`.

The Python runtime and Electron application are versioned independently. Runtimes are installed under the user data directory at `python-runtimes/versions/<version>/` and selected through `active.json`. Startup never checks for updates when a valid runtime exists; the index is read only on first installation or when the user chooses the manual Python update menu item. Package size, SHA-256, platform, architecture, version, and executable containment are validated before activation. `VIDEOAGENTS_PYTHON` remains an explicit development and diagnostics override.

Desktop and Python packages are produced only when a `v*` tag points to a commit on `main`. The tag version is embedded in both package types and published under `packages/video-agents/`: Python runtimes in `python/`, versioned desktop ZIPs in `mac/` and `win/`, and `metadata.json` as the shared update index. Each desktop directory also exposes `VideoAgents.zip` as the latest package. Release clients read this metadata before creating the main window: a newer supported version remains optional, while clients older than `desktop.minimumVersion` enter a non-skippable update flow before the local backend starts. The publish job reads that value from the GitHub Actions environment/repository variable `MINIMUM_DESKTOP_VERSION` and defaults to `1.0.0` when it is unset; publishing fails when the value is invalid or newer than the release version.

When launched from Finder, the macOS desktop client imports the login shell `PATH` and supplements common Homebrew, `~/.local/bin`, Kimi, OpenCode, Grok, Volta, and pnpm locations. Existing `claude`, `codex`, `kimi`, `pi`, `opencode`, and `grok` installations are therefore inherited by the local Python service and its Agent child processes instead of being bundled into the client. When the `pi` engine is selected, its language-model menus are populated from `pi --list-models`, so they reflect the providers and models available to the current Pi login. The `opencode` engine likewise populates its menus from `opencode models`, and the `grok` engine (Grok Build CLI, install from https://grok.com/build, sign in with `grok login`) from `grok models`. For every engine except deepagents the language-model menu defaults to Smart Assignment, which picks a model per agent by task complexity (e.g. on the opencode engine creative-core agents go to DeepSeek V4 Pro and the rest to DeepSeek V4 Flash); switching the engine or language model automatically re-syncs every agent's model settings.

Local `make desktop` packaging defaults to `CSC_IDENTITY_AUTO_DISCOVERY=false`, so it never reads an Apple developer certificate from the macOS Keychain and performs no signing or notarization. Release signing is enabled only in GitHub Actions when `CSC_LINK` and the Apple secrets are explicitly supplied.

For unattended pipelines, start the console in full-auto mode so agents on the
`claude` engine can run shell commands without approval prompts (the `codex`,
`pi`, and `deepagents` engines already run unattended):

```bash
make run-auto
```

This lets agents execute any command, so use it only on a trusted machine. The
orchestration-layer guardrails (the `orchestrator_guard` hook and the runtime
check in `modules/genmedia.py`) are independent of the permission mode and stay
active.

Optional integrations can be installed as extras:

```bash
python -m pip install -e ".[storage]"
python -m pip install -e ".[dev]"
```

The deepagents engine requires Python 3.11 or newer. If your main environment
runs Python 3.10, use `make install-deepagents` instead to create a dedicated
`.venv-deepagents` that the runtime discovers automatically; alternatively set
`DEEPAGENTS_PY` to any interpreter that has deepagents installed.

To choose a specific interpreter for that dedicated environment:

```bash
make install-deepagents DEEPAGENTS_PYTHON=python3.11
```

When using asdf, select an installed Python 3.11 version explicitly:

```bash
ASDF_PYTHON_VERSION=3.11.12 make install-deepagents DEEPAGENTS_PYTHON=python
```

DeepAgents defaults to a 128,000-token context window, an 8,192-token output cap,
and a graph recursion limit of 250 (500 for dispatcher agents). The runner
reserves 16,384 tokens for prompt and tool-schema overhead, rejects an oversized
first request, summarizes at 48,000 estimated tokens, keeps 8,000 tokens, and caps each shell
tool result at 24,000 bytes. The `glob` tool and automatic general-purpose
subagent are disabled to prevent unbounded scans and recursive context growth.
Override the model window only when the model server is configured with the
same or larger value:

```bash
DEEPAGENTS_CONTEXT_WINDOW=131072 DEEPAGENTS_MAX_OUTPUT_TOKENS=8192 ./run.sh
```

`DEEPAGENTS_RECURSION_LIMIT` and `DEEPAGENTS_MAX_TOOL_OUTPUT_BYTES` can also be
adjusted; raising either increases runtime or context pressure. When the recursion
limit is hit, the runner resumes from the last checkpoint up to
`DEEPAGENTS_MAX_CONTINUATIONS` (default 3) times before failing.

FFmpeg must be installed separately with your operating system's package
manager.

## Repository Layout

```text
agents/          Agent role specifications, workflow documentation, and DAG
code/            Repository-level validation and utility scripts
modules/         Media, audio, and DeepAgents adapters
services/api/    Independently runnable Python API service, schemas, and runtime state
services/runtime/ Agent scheduling, project services, providers, and runtime tools
apps/web/        Original static WebUI plus Python static server/API reverse proxy
apps/desktop/    Electron shell, Python Web lifecycle, packaging, and updates
tests/           Regression suite (pytest): modules, pipeline CLIs, and repository integrity
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
