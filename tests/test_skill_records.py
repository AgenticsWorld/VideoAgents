"""Skill lifecycle records: real reports, project isolation, persistence, and recovery."""
import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from modules import skill_records as history

SKILL = "08-video-gen/prompt/performance-direction"


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    from services.runtime import core
    monkeypatch.setattr(core, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(core, "ROOT", tmp_path)
    monkeypatch.setattr(core, "RUNS", {})
    f = tmp_path / "skill/SKILL.md"
    f.parent.mkdir()
    f.write_text("# Performance direction version one", encoding="utf-8")
    skills = [{"id": SKILL, "name": "表演控制", "path": "skill/SKILL.md", "selected": True,
               "agent_id": "08-video-gen/prompt"}]
    monkeypatch.setattr(core, "list_project_skills", lambda project: skills if project == "one" else [])
    return core, skills, f


def make_run(core, rid="run1", project="one"):
    run = {"id": rid, "project": project, "agent": "08-video-gen/prompt", "agent_name": "提示词工程师",
           "status": "queued", "created": 1000, "message": "请处理 ep01/grp002"}
    core.init_skill_records(run)
    core.RUNS[rid] = run
    core.publish_run(run)
    return run


def report(core, run, status, **extra):
    return asyncio.run(core.api_skill_report(run["id"], {"skill_id": SKILL, "status": status,
        "reason": "项目已勾选，当前组为对白组" if status == "running" else "完成全部技能步骤", **extra}))


def rows(core, project="one", **kwargs):
    return asyncio.run(core.api_project_skill_records(project, **kwargs))["records"]


def test_pending_start_complete_and_immutable_version(runtime):
    core, _, file = runtime
    run = make_run(core)
    assert rows(core)[0]["status"] == "pending"
    old_version = hashlib.sha256(file.read_bytes()).hexdigest()
    run.update(status="running", started=1001)
    core.init_skill_records(run)
    core.publish_run(run)
    assert rows(core)[0]["status"] == "pending"  # Agent started != skill started.
    report(core, run, "running")
    assert rows(core)[0]["status"] == "running"
    report(core, run, "completed")
    report(core, run, "completed")  # HTTP retry is idempotent.
    run.update(status="done", ended=1010)
    core.publish_run(run)
    file.write_text("new skill version")
    record = rows(core)[0]
    assert record["version"] == old_version
    assert record["status"] == "completed" and record["started"] and record["ended"]
    assert (record["episode"], record["group"]) == ("ep01", "grp002")
    assert record["trigger"] == "项目已勾选，当前组为对白组"
    # Terminal reports survive restart independently of in-memory runs.
    core.RUNS.clear()
    assert rows(core)[0]["status"] == "completed"
    assert rows(core, "two") == []


@pytest.mark.parametrize("state,started,expected", [("done", False, "unverified"),
    ("done", True, "unverified"), ("error", True, "failed"), ("error", False, "unverified")])
def test_missing_reports_never_count_as_completed(runtime, state, started, expected):
    core, _, _ = runtime
    run = make_run(core)
    run.update(status="running", started=1001)
    core.publish_run(run)
    if started:
        report(core, run, "running")
    run.update(status=state, ended=1002)
    core.publish_run(run)
    assert rows(core)[0]["status"] == expected


def test_cancel_before_start_and_restart_recovery(runtime):
    core, _, _ = runtime
    run = make_run(core)
    run.update(status="error", stopped="user", ended=1001)
    core.publish_run(run)
    assert rows(core)[0]["status"] == "skipped"
    other = make_run(core, "run2")
    other.update(status="running", started=1002)
    core.publish_run(other)
    report(core, other, "running")
    core.RUNS.clear()  # Simulate a crash before final persistence.
    statuses = {r["run_id"]: r["status"] for r in rows(core)}
    assert statuses == {"run1": "skipped", "run2": "unverified"}


def test_multiple_groups_use_distinct_records_and_preserve_terminal_results(runtime):
    core, _, _ = runtime
    run = make_run(core)
    run["status"] = "running"
    report(core, run, "running", episode="ep02", group="grp003")
    report(core, run, "completed", episode="ep02", group="grp003")
    report(core, run, "skipped", episode="ep02", group="grp004", reason="纯环境组，不适用表演控制")
    assert len(rows(core)) == 2
    assert next(r for r in rows(core) if r["group"] == "grp004")["trigger"] == "纯环境组，不适用表演控制"
    assert {(r["group"], r["status"]) for r in rows(core)} == {("grp003", "completed"), ("grp004", "skipped")}
    run.update(status="error", ended=1010)
    core.publish_run(run)
    assert {r["status"] for r in rows(core)} == {"completed", "skipped"}


@pytest.mark.parametrize("body", [
    {"status": "completed"}, {"status": "unknown"}, {"status": []},
    {"reason": ""}, {"reason": "x" * 1001}, {"episode": "../../other"},
    {"episode": "", "group": "grp001"}, {"group": "bad"}, {"skill_id": "other/skill"},
])
def test_invalid_reports_do_not_mutate_records(runtime, body):
    core, _, _ = runtime
    run = make_run(core)
    run["status"] = "running"
    before = json.dumps(run["_skill_records"])
    with pytest.raises(core.ServiceError):
        asyncio.run(core.api_skill_report(run["id"], {"skill_id": SKILL, "status": "running", "reason": "适用", **body}))
    assert json.dumps(run["_skill_records"]) == before


def test_unselected_other_agent_and_inactive_run_cannot_report(runtime):
    core, skills, _ = runtime
    run = make_run(core)
    with pytest.raises(core.ServiceError) as exc:
        report(core, run, "running")
    assert exc.value.status_code == 409
    skills[0]["selected"] = False
    core.init_skill_records(run)
    run["status"] = "running"
    core.publish_run(run)
    assert rows(core)[0]["status"] == "skipped"
    with pytest.raises(core.ServiceError):
        report(core, run, "running")


def test_api_routes_scope_pagination_and_error_handling(runtime):
    core, _, _ = runtime
    from services.api.app import app
    import httpx
    run = make_run(core)
    run["status"] = "running"
    async def probe():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/v1/runs/run1/skills", json={"skill_id": SKILL, "status": "running", "reason": "对白组"})
            assert r.status_code == 200, r.text
            for group in ("grp002", "grp003"):
                r = await client.post("/api/v1/runs/run1/skills", json={"skill_id": SKILL, "status": "skipped",
                                     "reason": "不适用", "episode": "ep01", "group": group})
                assert r.status_code == 200, r.text
            r = await client.get("/api/v1/projects/one/skills/records?limit=1&offset=1")
            assert r.status_code == 200 and len(r.json()["records"]) == 1 and r.json()["total"] == 2
            r = await client.get("/api/v1/projects/two/skills/records")
            assert r.json()["records"] == []
            r = await client.get("/api/v1/projects/one/skills/records?limit=999")
            assert r.status_code == 400
    asyncio.run(probe())


def test_ambiguous_scope_and_corrupt_history(runtime):
    core, _, _ = runtime
    assert history.scope_from_message("ep01/grp001 and ep02/grp002") == ("", "")
    run = make_run(core)
    directory = core.PROJECTS_DIR / "one/runs/skill_records"
    (directory / "corrupt.json").write_text("not json")
    (directory / "foreign.json").write_text(json.dumps({"project": "two", "records": []}))
    assert len(rows(core)) == 1


def test_seed_does_not_record_unselected_or_other_agents(runtime):
    core, skills, _ = runtime
    skills.append(dict(skills[0], id="other/skill", agent_id="other/agent"))
    skills.append(dict(skills[0], id="unselected/skill", selected=False))
    make_run(core)
    assert [r["skill_id"] for r in rows(core)] == [SKILL]


def test_queued_skill_version_refreshes_at_actual_launch(runtime):
    core, _, file = runtime
    run = make_run(core)
    file.write_text("version changed while queued")
    core.init_skill_records(run)
    run.update(status="running", started=1002)
    core.publish_run(run)
    assert rows(core)[0]["version"] == hashlib.sha256(file.read_bytes()).hexdigest()


def test_failed_persistence_does_not_acknowledge_a_report(runtime, monkeypatch):
    core, _, _ = runtime
    run = make_run(core)
    run["status"] = "running"
    before = json.dumps(run["_skill_records"])
    def fail(*args):
        raise OSError("disk full")
    monkeypatch.setattr(history, "write", fail)
    with pytest.raises(OSError):
        report(core, run, "running")
    assert json.dumps(run["_skill_records"]) == before


def test_cli_binds_current_run_and_reports_structured_metadata(monkeypatch):
    from services.runtime import skill_report
    captured = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return b'{"ok":true}'
    class Opener:
        def open(self, req, timeout):
            captured.append(req)
            return Response()
    monkeypatch.setenv("VIDEOAGENTS_RUN_ID", "run1")
    monkeypatch.setenv("VIDEOAGENTS_API_URL", "http://localhost:8640")
    monkeypatch.setattr(skill_report.urllib.request, "build_opener", lambda *args: Opener())
    assert skill_report.main([SKILL, "running", "--reason", "当前组为对白组", "--ep", "ep03", "--group", "grp004"]) == 0
    assert captured[0].full_url == "http://localhost:8640/api/v1/runs/run1/skills"
    assert json.loads(captured[0].data)["episode"] == "ep03"
    monkeypatch.delenv("VIDEOAGENTS_RUN_ID")
    with pytest.raises(SystemExit) as error:
        skill_report.main([SKILL, "running", "--reason", "对白"])
    assert error.value.code == 2
