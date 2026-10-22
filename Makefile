.PHONY: install install-dev install-pi run api run-auto web-dev web-build \
	desktop desktop-install desktop-run desktop-dev desktop-build desktop-runtime desktop-package \
	plugin-package test

PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
RUNTIME_VERSION ?= $(shell git rev-parse --short HEAD)
DESKTOP_DEV_SERVICE_ENV ?= dev

install:
	$(PYTHON) -m pip install -e .

# Agentics 引擎(内置 pi):按 vendor/pi/package-lock.json 装到 vendor/pi/node_modules。
# 源码版运行需要 PATH 上有 Node.js 22.19+;桌面端打包时由 desktop-package 带上 --prune 精简后打进安装包。
install-pi:
	$(PYTHON) apps/desktop/scripts/vendor_pi.py

install-dev:
	$(PYTHON) -m pip install -e ".[dev]"

run:
	$(PYTHON) apps/web/server.py

api:
	$(PYTHON) -m services.api

# 全自动模式:claude 引擎免审批执行命令(codex/pi/agentics 本就免审批)。
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

desktop-dev: install-pi
	VIDEOAGENTS_SERVICE_ENV=$(DESKTOP_DEV_SERVICE_ENV) npm run dev:desktop

desktop-build:
	npm run build:desktop

desktop-runtime:
	$(PYTHON) apps/desktop/scripts/build_runtime.py --version $(RUNTIME_VERSION)
	$(PYTHON) apps/desktop/scripts/package_runtime.py --version $(RUNTIME_VERSION)

desktop-package: desktop-build
	$(PYTHON) apps/desktop/scripts/vendor_pi.py --prune
	npm run package --workspace @videoagents/desktop

# 声明式 Agent 插件打包(产出 dist/<name>-<version>.zip,可在控制台「插件」页上传安装)。
# 打包脚本会拒绝任何 .py/.sh 进包——插件是纯声明式的(WORKFLOW.md §10.4),
# 机检脚本随本体发布在 modules/ 与 code/。
PLUGIN ?= plugin-src/audio-to-video
plugin-package:
	$(PYTHON) scripts/package_plugin.py $(PLUGIN)

test:
	$(PYTHON) -m pytest
