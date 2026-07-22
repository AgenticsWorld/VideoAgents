from __future__ import annotations

import time
from typing import Any

from services.runtime import core

from .state import RuntimeStore


def install_runtime_store(store: RuntimeStore) -> None:
    """Attach durable state to the existing, battle-tested execution runtime."""

    for run in store.load_runs():
        if run.get("status") in {"queued", "running"}:
            run["status"] = "error"
            run["error"] = "服务重启导致运行中断"
            run["ended"] = time.time()
            store.upsert_run(run)
        core.RUNS[run["id"]] = run

    for approval in store.load_approvals():
        core.CONFIRMS[approval["id"]] = approval

    original_publish = core.HUB.publish

    def publish(event: dict[str, Any]) -> None:
        if event.get("type") == "run" and isinstance(event.get("run"), dict):
            store.upsert_run(core.RUNS.get(event["run"].get("id"), event["run"]))
        elif event.get("type") == "confirm":
            approval = core.CONFIRMS.get(str(event.get("id")))
            if approval:
                # The public event intentionally omits internal timestamps used
                # when rebuilding the approval queue after a service restart.
                store.upsert_approval(approval)
        elif event.get("type") == "confirm_done":
            approval = core.CONFIRMS.get(str(event.get("id")))
            if approval:
                store.upsert_approval(approval)
        store.append_event(event)
        # Persistence is an API-service implementation detail.  Live WebUI
        # events keep the original server.py payload exactly unchanged.
        original_publish(event)

    core.HUB.publish = publish
