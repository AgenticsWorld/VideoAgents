"""Durable, project-scoped skill execution metadata (no artifact contents or diffs)."""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from pathlib import Path

STATUSES = {"pending", "running", "completed", "skipped", "failed", "unverified"}
TERMINAL = STATUSES - {"pending", "running"}


def scope_from_message(message: str) -> tuple[str, str]:
    # Infer only an unambiguous scope; never cross-product episodes and groups.
    eps = set(re.findall(r"\bep\d+\b", message, re.I))
    groups = set(re.findall(r"\bgrp\d+\b", message, re.I))
    ep = next(iter(eps)).lower() if len(eps) == 1 else ""
    group = next(iter(groups)).lower() if ep and len(groups) == 1 else ""
    return ep, group


def seed(run: dict, skills: list[dict], root: Path) -> list[dict]:
    ep, group = scope_from_message(run.get("message") or "")
    records = []
    for skill in skills:
        if skill["agent_id"] != run["agent"] or not skill["selected"]:
            continue
        try:
            version = hashlib.sha256((root / skill["path"]).read_bytes()).hexdigest()
        except OSError:
            version = ""
        records.append({
            "id": uuid.uuid4().hex, "run_id": run["id"], "project": run["project"],
            "agent_id": run["agent"], "agent_name": run["agent_name"],
            "skill_id": skill["id"], "skill_name": skill["name"], "version": version,
            "created": run["created"], "started": None, "ended": None,
            "episode": ep, "group": group, "status": "pending",
            "reason": "项目已勾选，等待 Agent 判断任务是否适用", "seeded": True,
        })
    return records


def report(records: list[dict], body: dict, now: float | None = None) -> list[dict]:
    """Validate explicit lifecycle reports. Completion requires a matching start."""
    now = time.time() if now is None else now
    sid, status, reason = body.get("skill_id"), body.get("status"), body.get("reason")
    if not isinstance(sid, str) or not isinstance(status, str) or status not in STATUSES - {"pending"}:
        raise ValueError("invalid skill_id or status")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
        raise ValueError("reason must contain 1-1000 characters")
    matches = [r for r in records if r["skill_id"] == sid]
    if not matches:
        raise ValueError("skill is not selected for this agent/run")
    ep = body.get("episode", matches[0]["episode"])
    group = body.get("group", matches[0]["group"])
    if not isinstance(ep, str) or (ep and not re.fullmatch(r"ep\d+", ep)):
        raise ValueError("episode must be ep followed by digits, or empty")
    if not isinstance(group, str) or (group and (not ep or not re.fullmatch(r"grp\d+", group))):
        raise ValueError("group requires an episode and must be grp followed by digits")
    target = next((r for r in matches if r["episode"] == ep and r["group"] == group), None)
    if status == "completed" and (not target or target["status"] not in {"running", "completed"}):
        raise ValueError("completion requires a matching running record")
    if target and target["status"] in TERMINAL:
        if target["status"] == status and target["reason"] == reason.strip():
            return records  # Idempotent retry after a lost HTTP response.
        raise ValueError("terminal skill records cannot be overwritten; use a new run for retries")
    out = [dict(r) for r in records]
    if target:
        item = next(r for r in out if r["id"] == target["id"])
    else:
        item = dict(matches[0], id=uuid.uuid4().hex, episode=ep, group=group,
                    started=None, ended=None, status="pending", seeded=False)
        item.pop("trigger", None)
        out.append(item)
    # Replace the candidate placeholder when the agent reports concrete scopes.
    out = [r for r in out if r["id"] == item["id"] or
           not (r["skill_id"] == sid and r.get("seeded") and r["status"] == "pending")]
    item["seeded"] = False
    item["status"] = status
    if status == "running" and item["started"] is None:
        item["started"] = now
    if status in TERMINAL:
        item["ended"] = now
    # Preserve the trigger from the start event; terminal messages describe outcome.
    if status == "running" or not item.get("trigger"):
        item["trigger"] = reason.strip()
    item["reason"] = reason.strip()
    return out


def finish(records: list[dict], run: dict) -> list[dict]:
    out = [dict(r) for r in records]
    for r in out:
        if r["status"] in TERMINAL:
            continue
        if run.get("stopped") and not run.get("started"):
            r.update(status="skipped", reason="运行在启动前取消，技能未执行")
        elif r["status"] == "running" and run.get("status") == "error":
            r.update(status="failed", reason="技能执行期间运行失败或中断，未收到完成登记")
        else:
            r.update(status="unverified", reason="运行已结束，未收到完整的技能执行登记")
        r["ended"] = run.get("ended") or time.time()
    return out


def write(project_root: Path, run: dict, records: list[dict]):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run["id"]):
        raise ValueError("invalid run id")
    directory = project_root / "runs" / "skill_records"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{run['id']}.json"
    temporary = directory / f".{run['id']}.{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text(json.dumps({"run_id": run["id"], "project": run["project"],
                                        "records": records}, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def history(project_root: Path, project: str, active_runs: dict, *, offset=0, limit=50) -> dict:
    records = []
    for path in (project_root / "runs" / "skill_records").glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("project") != project:
                continue
            run_id = data["run_id"]
            for stored in data["records"]:
                if not isinstance(stored, dict):
                    continue
                required = ("id", "run_id", "project", "agent_id", "agent_name", "skill_id",
                            "skill_name", "version", "episode", "group", "status", "reason")
                if not all(isinstance(stored.get(k), str) for k in required):
                    continue
                if stored["status"] not in STATUSES or not isinstance(stored.get("created"), (int, float)):
                    continue
                if any(stored.get(k) is not None and not isinstance(stored[k], (int, float))
                       for k in ("started", "ended")):
                    continue
                if stored.get("project") != project or stored.get("run_id") != run_id:
                    continue
                r = dict(stored)
                run = active_runs.get(run_id)
                if r["status"] not in TERMINAL and (not run or run.get("status") not in {"queued", "running"}):
                    # A crash may precede final persistence. Never leave historical rows running.
                    r.update(status="unverified", reason="服务重启或运行已结束，未收到完整的技能执行登记")
                r.pop("seeded", None)
                records.append(r)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    records.sort(key=lambda r: (r.get("created", 0), r.get("started") or 0, r["id"]), reverse=True)
    return {"records": records[offset:offset + limit], "total": len(records), "offset": offset}
