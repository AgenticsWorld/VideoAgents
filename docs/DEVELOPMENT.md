# Development guide

[中文](DEVELOPMENT.zh-CN.md)

This guide collects the operational detail for running VideoAgents from source, developing and packaging the desktop client, configuring agent engines, and cutting releases. For a first run, start with the [Quickstart](../README.md#quickstart) in the README.

## Requirements

- Python 3.10 or newer
- Node.js 22.12 or newer only for Electron development and packaging
- At least one agent engine: Claude CLI, Codex CLI, or an OpenAI-compatible model
- FFmpeg for media inspection, audio processing, and editing workflows
- Provider credentials only for the generation services you choose to use

## Run from source

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

## Desktop client

### Development

Use `npm run dev:web` to start the Python Web gateway and `npm ci && npm run dev:desktop` for the
desktop shell. Electron starts the same gateway by default or proxies the remote API
specified by `VIDEOAGENTS_API_URL`. Release packages do not contain Python. On the first launch
without an installed runtime, the client reads `https://s3.agentics.world/packages/video-agents/metadata.json`
and downloads the matching package. Claude, Codex, Kimi, Pi, OpenCode, Grok, FFmpeg, models, and GPU environments remain
optional external installations. On Windows x64 the desktop client offers to download FFmpeg from the same
package source when none is found (no package manager or administrator rights needed); the build is pinned in
`apps/desktop/ffmpeg-artifact.json`.

Before desktop development, make sure `node --version` meets `.nvmrc`; with nvm, run `nvm use` first. Run `npm ci` again after switching Node major versions so an incomplete Electron installation from the old runtime is not reused.

The equivalent Makefile targets are `make desktop-install`, `make desktop-dev` (or `make desktop-run`) for development, and `make desktop-build` for compilation. `make desktop-runtime` creates a separately distributable Python ZIP using the current short Git hash under `apps/desktop/.runtime-packages/`. `make desktop` creates the client-only artifacts under `apps/desktop/release/`.

### Python runtime

The Python runtime and Electron application are versioned independently. Runtimes are installed under the user data directory at `python-runtimes/versions/<version>/` and selected through `active.json`. Startup never checks for updates when a valid runtime exists; the index is read only on first installation or when the user chooses the manual Python update menu item. Package size, SHA-256, platform, architecture, version, and executable containment are validated before activation. `VIDEOAGENTS_PYTHON` remains an explicit development and diagnostics override.

### Releases

Desktop and Python packages are produced only when a `v*` tag points to a commit on `main`. The tag version is embedded in both package types and published under `packages/video-agents/`: Python runtimes in `python/`, versioned desktop ZIPs in `mac/` and `win/`, and `metadata.json` as the shared update index. Each desktop directory also exposes `VideoAgents.zip` as the latest package. Release clients read this metadata before creating the main window: a newer supported version remains optional, while clients older than `desktop.minimumVersion` enter a non-skippable update flow before the local backend starts. The publish job reads that value from the GitHub Actions environment/repository variable `MINIMUM_DESKTOP_VERSION` and defaults to `1.0.0` when it is unset; publishing fails when the value is invalid or newer than the release version.

### Signing

Local `make desktop` packaging defaults to `CSC_IDENTITY_AUTO_DISCOVERY=false`, so it never reads an Apple developer certificate from the macOS Keychain and performs no signing or notarization. Release signing is enabled only in GitHub Actions when `CSC_LINK` and the Apple secrets are explicitly supplied.

## Agent engines

### CLI discovery and model menus

When launched from Finder, the macOS desktop client imports the login shell `PATH` and supplements common Homebrew, `~/.local/bin`, Kimi, OpenCode, Grok, Volta, and pnpm locations. Existing `claude`, `codex`, `kimi`, `pi`, `opencode`, `grok`, and `agy` (Antigravity CLI) installations are therefore inherited by the local Python service and its Agent child processes instead of being bundled into the client. When the `pi` engine is selected, its language-model menus are populated from `pi --list-models`, so they reflect the providers and models available to the current Pi login. The `opencode` engine likewise populates its menus from `opencode models`, and the `grok` engine (Grok Build CLI, install from https://grok.com/build, sign in with `grok login`) from `grok models`, and the `antigravity` engine (Antigravity CLI, binary `agy`, install from https://antigravity.google/docs/getting-started?tab=cli, sign in by running `agy` once in a terminal) from `agy models`. For every engine except agentics the language-model menu defaults to Smart Assignment, which picks a model per agent by task complexity (e.g. on the opencode engine creative-core agents go to DeepSeek V4 Pro and the rest to DeepSeek V4 Flash); switching the engine or language model automatically re-syncs every agent's model settings.

### Setup dialog

When the top-bar engine (or the one restored at startup) is a CLI that is missing or not signed in, a setup dialog opens. **One-click install** runs that CLI's official install command in the background (`curl -fsSL <official script> | bash`, or `irm <official script> | iex` on Windows; OpenCode installs OpenCode 2 from `https://opencode.ai/v2/install`, and on Windows uses `npm install -g @opencode/cli`) and streams the log; if it fails, **Install in a terminal** reruns it in a terminal window so the script's questions can be answered. Once installed, **Sign in** opens a terminal window running the CLI's own login command (such as `claude auth login`, `codex login`, `kimi login`); the user authorizes with their own account in the browser and the dialog closes when the login is detected. The app never handles the tokens. The install process gets no app or provider secrets in its environment, and install and sign-in are only offered to requests from the local machine.

### Adding to a running task (Ctrl+Enter)

In the console, Enter sends a new task (queued if the same agent's previous task is still running). Ctrl+Enter (or ⌘+Enter on macOS) instead adds the message to the task that agent is running in the current project (`POST /api/v1/runs/steer`). The message reaches the model after the current tool call finishes and before its next model call, so a long command has to finish first. How each engine takes it (`services/runtime/run_steer.py`):

- `claude`: runs use `-p --input-format stream-json --replay-user-messages`; the prompt and any added messages are written to stdin as user messages, and the service closes stdin after the `result`. A message that arrives during the turn's final model output is run by the CLI as one more turn.
- `pi` and `agentics`: runs use `--mode rpc`; the prompt is a `prompt` command, added messages are `prompt` with `streamingBehavior: "steer"`, and stdin is closed after `agent_settled`. The session id comes from the `get_state` reply.
- `opencode` 2.x: a separate `opencode api --standalone` call writes the message to the session's inbox with `delivery: "steer"` and `resume: false`; the running `--standalone` process picks it up at its next step. Anything still in the inbox when the run ends is withdrawn.
- `codex`, `kimi`, `grok`, `antigravity` and opencode 1.x cannot take input once a run has started: the message is merged into that agent's queued task, or sent as a new task if none is queued, and the chat says why.

Messages that arrive while the task is still queued or before its process has started are added to its prompt. Messages not delivered before the task ends are sent as a new task (for a task stopped by hand, the chat only notes them).

### Full-auto mode

For unattended pipelines, start the console in full-auto mode so agents on the
`claude` engine can run shell commands without approval prompts (the `codex`,
`pi`, and `agentics` engines already run unattended):

```bash
make run-auto
```

This lets agents execute any command, so use it only on a trusted machine. The
orchestration-layer guardrails (the `orchestrator_guard` hook and the runtime
check in `modules/genmedia.py`) are independent of the permission mode and stay
active.

## Optional integrations

Optional integrations can be installed as extras:

```bash
python -m pip install -e ".[storage]"
python -m pip install -e ".[dev]"
```

## Agentics engine (bundled pi)

The `agentics` engine (engine id `deepagents` in settings, APIs and `dispatch.py`) is
[Pi Coding Agent](https://github.com/earendil-works/pi) shipped with the app. The version is
pinned in `vendor/pi/package.json` and `vendor/pi/package-lock.json`; it is separate from a
`pi` you install and sign in to yourself, which stays available as the `pi` engine.

- **Desktop**: packaging runs `apps/desktop/scripts/vendor_pi.py --prune` (`npm ci`, then drops
  source maps and other platforms' native helpers) and ships `vendor/pi` as
  `backend/vendor/pi`. The app has no separate Node.js: the service starts pi with the desktop
  executable (`VIDEOAGENTS_NODE`) and `ELECTRON_RUN_AS_NODE=1`.
- **From source**: run `make install-pi` once (needs Node.js 22.19 or newer on `PATH`;
  `VIDEOAGENTS_NODE` can point at another Node). `make desktop-dev` does this automatically.

The language model comes from 🎨 Generation Models → Language Model/Agentics: Agentics (the
signed-in desktop account), a local OpenAI-compatible endpoint, a cloud endpoint (any
OpenAI-compatible provider; the Base URL, API key and model have no defaults and must be filled
in by the user), and — hidden unless already active — OpenRouter. Each run gets its own pi configuration directory under
`<data>/.videoagents/agentics-pi/runs/` with a `models.json` for the active channel (the API key
is referenced as `$VIDEOAGENTS_LLM_API_KEY` and passed only through the environment) and is
removed when the run ends; sessions live in `agentics-pi/sessions/` and are resumed with
`--session`. pi is started with `--no-extensions --no-skills --no-prompt-templates
--no-context-files`, so a user's own `~/.pi/agent` setup never applies.

Models are declared with a 128,000-token context window and a 65,536-token output cap
(8,192 for the local channel); pi compacts the session before the window fills. Thinking and
the reply share the output cap, and pi lowers it further when the context is nearly full. A run
whose last reply is cut off by the cap (`stopReason=length`) is marked as failed. Override the
values only when the model server allows the larger ones:

```bash
AGENTICS_CONTEXT_WINDOW=262144 AGENTICS_MAX_OUTPUT_TOKENS=131072 ./run.sh
```

A model is declared with image input (`"input": ["text", "image"]`) when it can take images: for
the Agentics and OpenRouter channels this follows `architecture.input_modalities` in OpenRouter's model
catalog, and for the local and cloud channels the 图片输入 checkbox on that page (ticked by default).
Without it, pi drops every image an agent reads.

## FFmpeg

FFmpeg must be installed separately with your operating system's package
manager.

## Tests

python -m pip install -e ".[dev]"
pytest
npm run test:web   # Node checks for the browser-side whitebox / director code
```

CI runs the whole `tests/` suite: module and pipeline-CLI regressions, repository
integrity (JSON/YAML, workflow agent references, security defaults), and the HTTP
layer (route table, offline GET smoke test, Web console pages). Cases that need
`ffmpeg` skip when it is not installed.
