#!/usr/bin/env bash
# 全自动模式启动脚本,等价 `make run-auto`(无需安装 make 亦可一键启动)。
# claude 引擎免审批执行命令(codex/deepagents 本就免审批);
# 调度层越界防护(orchestrator_guard hook + genmedia 守卫)不受权限模式影响,依然生效。
# 用法: ./run.sh [server 参数...]   可用 PYTHON=... 指定解释器。
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
    if command -v python >/dev/null 2>&1; then
        PYTHON=python
    elif command -v python3 >/dev/null 2>&1; then
        PYTHON=python3
    else
        echo "run.sh: 未找到 python/python3,请安装或用 PYTHON=/path/to/python 指定" >&2
        exit 1
    fi
fi

export VIDEOAGENTS_PERMISSION_MODE="${VIDEOAGENTS_PERMISSION_MODE:-bypassPermissions}"
exec "$PYTHON" webui/server.py "$@"
