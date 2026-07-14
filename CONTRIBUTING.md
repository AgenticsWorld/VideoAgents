# Contributing

## Development Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
pytest
```

## Change Guidelines

- Keep `agents/WORKFLOW.md` and `agents/workflow.yaml` synchronized.
- Update both neighboring agent roles when changing a responsibility boundary.
- Keep provider credentials, source material, generated media, and run records
  out of commits.
- Add focused tests for shared behavior and user-facing workflows.
- Preserve backward compatibility for existing project artifacts when practical.
- Identify modified files and retain `LICENSE` and `NOTICE` when redistributing.

## Pull Requests

Describe the behavior change, its motivation, verification performed, and any
migration impact. Keep unrelated refactors out of the same pull request. By
submitting a contribution, you agree that it is licensed under Apache-2.0.
