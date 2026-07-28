.PHONY: install install-dev install-deepagents run api run-auto web-dev web-build \
	desktop desktop-install desktop-run desktop-dev desktop-build desktop-runtime desktop-package test

PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
RUNTIME_VERSION ?= $(shell git rev-parse --short HEAD)

install:
	$(PYTHON) -m pip install -e .

install-dev:
	$(PYTHON) -m pip install -e ".[dev]"

# deepagents 引擎专用 venv(deepagents 需 Python ≥3.11,与主环境的 3.10 底线隔离)。
# 依赖清单与 pyproject [deepagents] extra 保持一致;Python 版本不足时 pip 会明确报错。
DEEPAGENTS_PYTHON ?= $(shell command -v python3.12 2>/dev/null || command -v python3.11 2>/dev/null || echo python3)
install-deepagents:
	$(DEEPAGENTS_PYTHON) -m venv .venv-deepagents
	.venv-deepagents/bin/python -m pip install --upgrade pip
	.venv-deepagents/bin/python -m pip install "deepagents>=0.2" "langchain-openai>=0.3"

run:
	$(PYTHON) apps/web/server.py

api:
	$(PYTHON) -m services.api

# 全自动模式:claude 引擎免审批执行命令(codex/deepagents 本就免审批)。
# 调度层越界防护(orchestrator_guard hook + genmedia 守卫)不受权限模式影响,依然生效。
run-auto:
	VIDEOAGENTS_PERMISSION_MODE=bypassPermissions $(PYTHON) apps/web/server.py

web-dev:
	npm run dev:web

web-build:
	npm run build:web

# Electron 桌面端。make desktop 生成安装包；开发运行使用 make desktop-dev。
desktop: desktop-package

desktop-install:
	npm ci
	$(PYTHON) -m pip install "uv==0.11.30"

desktop-run: desktop-dev

desktop-dev:
	npm run dev:desktop

desktop-build:
	npm run build:desktop

desktop-runtime:
	$(PYTHON) apps/desktop/scripts/build_runtime.py --version $(RUNTIME_VERSION)
	$(PYTHON) apps/desktop/scripts/package_runtime.py --version $(RUNTIME_VERSION)

desktop-package: desktop-build
	npm run package --workspace @videoagents/desktop

test:
	$(PYTHON) -m pytest
