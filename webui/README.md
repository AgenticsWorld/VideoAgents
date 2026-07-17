# VideoAgents Web Console

The FastAPI console provides direct conversations with all 83 agents,
orchestrated team execution, live run status, project configuration, artifact
previews, and mobile storyboard sketches.

## Start

From the repository root:

```bash
python -m pip install -e .
python webui/server.py
```

Open <http://127.0.0.1:8630>.

For unattended pipelines, start in full-auto mode (`make run-auto` from the
repository root):

```bash
VIDEOAGENTS_PERMISSION_MODE=bypassPermissions python webui/server.py
```

With the default `acceptEdits` mode, headless `claude -p` agents silently deny
any Bash command that is not allowlisted in `.claude/settings.json` and fall
back to asking the user for approval. `bypassPermissions` removes those
prompts; agents can then run arbitrary commands, so use it only on a trusted
machine. The orchestration-layer guardrails (`orchestrator_guard` hook and the
`modules/genmedia.py` runtime check) do not depend on the permission mode and
remain active. The `codex`, `kimi`, and `deepagents` engines are unaffected —
they never prompt for approval.

At least one execution engine is required to run agents:

- `claude`: Claude CLI using `claude -p`
- `codex`: Codex CLI using `codex exec --json`
- `kimi`: Kimi Code CLI using `kimi -p --output-format stream-json`
- `deepagents`: an OpenAI-compatible endpoint through the optional DeepAgents
  environment

The interface can configure supported image, video, audio, TTS, and object
storage providers. Provider configuration is written to
`webui/genconfig.json`, which may contain plaintext API keys and is ignored by
Git.

## Runtime Data

- `webui/chats/`: conversation records
- `webui/runs/`: complete execution event logs
- `webui/state.json`: resumable session and watchdog state
- `webui/agentmodels.json`: per-agent model overrides saved from the UI
- `data/projects/<slug>/`: project source files and generated artifacts

All of these paths are ignored by Git.

## Environment

| Variable | Default | Purpose |
|---|---|---|
| `VIDEOAGENTS_HOST` | `127.0.0.1` | Listen address |
| `VIDEOAGENTS_PORT` | `8630` | Listen port |
| `VIDEOAGENTS_PERMISSION_MODE` | `acceptEdits` | Claude CLI permission mode |
| `VIDEOAGENTS_ENABLE_CLAUDE_USAGE_PROBE` | disabled | Allow reading the local Claude OAuth credential for the usage display |
| `CLAUDE_BIN` | `claude` | Claude CLI executable |
| `CODEX_BIN` | `codex` | Codex CLI executable |
| `KIMI_BIN` | `kimi` | Kimi Code CLI executable |
| `DEEPAGENTS_PY` | `webui/.venv-deepagents/bin/python` | DeepAgents Python executable |

Setting `VIDEOAGENTS_HOST=0.0.0.0` enables LAN access for the tokenized mobile
storyboard page. The console is not an Internet-facing service and must not be
exposed directly to the public Internet. Read the root `SECURITY.md` before
enabling LAN access or `bypassPermissions`.

## Dispatch CLI

```bash
python webui/dispatch.py --list
python webui/dispatch.py "<agent-id>" "<instruction>" --wait
python webui/dispatch.py --runs
python webui/dispatch.py --status <run-id>
python webui/dispatch.py --wait-all <run-id>...
```
