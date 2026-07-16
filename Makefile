.PHONY: install install-dev run run-auto test

install:
	python -m pip install -e .

install-dev:
	python -m pip install -e ".[dev]"

run:
	python webui/server.py

# 全自动模式:claude 引擎免审批执行命令(codex/deepagents 本就免审批)。
# 调度层越界防护(orchestrator_guard hook + genmedia 守卫)不受权限模式影响,依然生效。
run-auto:
	VIDEOAGENTS_PERMISSION_MODE=bypassPermissions python webui/server.py

test:
	pytest
