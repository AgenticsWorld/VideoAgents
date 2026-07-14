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

At least one execution engine is required to run agents:

- `claude`: Claude CLI using `claude -p`
- `codex`: Codex CLI using `codex exec --json`
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
