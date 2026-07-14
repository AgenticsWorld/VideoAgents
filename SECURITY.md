# Security Policy

## Supported Versions

Security fixes are applied to the latest release on the `main` branch.

## Reporting a Vulnerability

Do not open a public issue for an unpatched vulnerability or exposed secret.
Use GitHub's private vulnerability reporting feature for this repository. If it
is unavailable, contact the AgenticsWorld organization owners through GitHub.

Include affected versions, reproduction steps, impact, and any suggested fix.
Do not include real API keys, private source material, or generated project data.

## Deployment Boundary

VideoAgents is a local production tool, not an Internet-facing multi-user
service. Agents may read and write project files and may execute local commands.

- Keep `VIDEOAGENTS_HOST=127.0.0.1` unless LAN access is explicitly required.
- Never expose the Web console directly to the public Internet.
- Keep `VIDEOAGENTS_PERMISSION_MODE=acceptEdits` for untrusted inputs.
- Treat novels, reference files, prompts, and model output as untrusted content.
- Store provider keys only in `webui/genconfig.json` or a secret manager.
- Do not commit `data/`, runtime logs, chat histories, or configuration files.
- Run the application under a dedicated OS account or container when processing
  untrusted projects.

Claude OAuth usage probing is disabled by default. Enabling
`VIDEOAGENTS_ENABLE_CLAUDE_USAGE_PROBE` allows the process to read the local
Claude credential solely to query usage data; enable it only on a trusted host.
