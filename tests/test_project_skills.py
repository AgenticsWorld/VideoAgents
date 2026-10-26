"""Project skill selection, activation gates, and performance opt-in regressions."""
import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROMPT = "08-video-gen/prompt"
PERFORMANCE = f"{PROMPT}/performance-direction"
CUSTOM = "01-story/dialogue-rewrite/dialogue-polish"
PLUGIN = "plugin-demo/writer/polish"


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    from services.runtime import core

    monkeypatch.setattr(core, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(core, "STATE", {})
    for name in ("one", "two"):
        (core.PROJECTS_DIR / name).mkdir(parents=True)
    monkeypatch.setattr(core, "ensure_project", lambda project: (core.PROJECTS_DIR / project).mkdir(exist_ok=True))
    async def no_notify(*args):
        pass
    monkeypatch.setattr(core, "_notify_settings_change", no_notify)
    model = {"id": core.PROMPT_SKILL_SD25}
    monkeypatch.setattr(core, "load_genconfig", lambda: {"test": True})
    monkeypatch.setattr(core, "auto_prompt_skill", lambda cfg=None: (model["id"], "test-model"))
    catalog = []
    for sid, kind in [(core.PROMPT_SKILL_SD25, "conditional"),
                      (core.PROMPT_SKILL_SD20, "conditional"),
                      (core.PROMPT_SKILL_H3, "conditional"),
                      (core.PROMPT_SKILL_WAN30, "conditional"),
                      (core.PROMPT_SKILL_KLING4, "conditional"),
                      (PERFORMANCE, "soul"), (CUSTOM, "generic"), (PLUGIN, "generic"),
                      ("09-audio/audio-transcription/audio-transcription", "always"),
                      ("08-video-gen/video-generation/agentics-media-generation", "conditional")]:
        agent, directory = sid.rsplit("/", 1)
        catalog.append({"id": sid, "agent_id": agent, "agent_name": "对白优化" if sid == CUSTOM else agent,
                        "category_name": "test", "plugin": "demo" if sid == PLUGIN else None,
                        "dir": directory, "name": directory, "description": "适用任务时执行",
                        "path": f"agents/{agent}/skills/{directory}/SKILL.md", "kind": kind,
                        "condition": "模型条件" if kind == "conditional" else ""})
    monkeypatch.setattr(core, "scan_agent_skills", lambda refresh=False: catalog)
    monkeypatch.setattr(core, "sync_group_settings_effective", lambda project: [])
    return core, model


def save(core, project, overrides):
    return asyncio.run(core.api_project_skills_set(project, {"overrides": overrides}))


def selected(core, project):
    return {s["id"] for s in core.list_project_skills(project) if s["selected"]}


@pytest.mark.parametrize("directory", ["sd25-pe", "sd20-pe", "h3-pe", "wan3-pe", "kling4-pe"])
def test_only_model_prompt_skill_defaults_on(runtime, directory):
    core, model = runtime
    model["id"] = f"{PROMPT}/{directory}"
    assert selected(core, "one") == {model["id"]}
    assert not core.project_skill_enabled(PERFORMANCE, "one")
    # Opening the dialog is read-only and includes every installed skill, including plugins.
    response = asyncio.run(core.api_project_skills_get("one"))
    assert len(response["skills"]) == 10
    assert any(s["id"] == PLUGIN for s in response["skills"])
    assert not core.project_settings_path("one").exists()


def test_project_selection_is_persistent_and_isolated(runtime):
    core, _ = runtime
    save(core, "one", {PERFORMANCE: True, CUSTOM: True, PLUGIN: True})
    assert {PERFORMANCE, CUSTOM, PLUGIN} <= selected(core, "one")
    assert selected(core, "two") == {core.PROMPT_SKILL_SD25}
    # Updating another setting preserves existing skill choices.
    asyncio.run(core.api_projconfig_set({"project": "one", "output": {"caption_enabled": True}}))
    saved = json.loads(core.project_settings_path("one").read_text())
    assert saved["project_skills"]["overrides"][PERFORMANCE] is True
    save(core, "one", {PERFORMANCE: False})
    assert CUSTOM in selected(core, "one")
    assert PERFORMANCE not in selected(core, "one")
    assert core.STATE == {}


def test_model_follows_defaults_but_explicit_opt_out_survives(runtime):
    core, model = runtime
    save(core, "one", {CUSTOM: True})
    model["id"] = core.PROMPT_SKILL_H3
    assert selected(core, "one") == {CUSTOM, core.PROMPT_SKILL_H3}
    save(core, "one", {core.PROMPT_SKILL_H3: False})
    assert core.resolve_prompt_skill("one")["reason"] == "disabled"
    assert selected(core, "one") == {CUSTOM}
    effective = core.load_project_settings("one")["prompt_skill"]["effective"]
    assert effective["skill_id"] == ""
    save(core, "one", {core.PROMPT_SKILL_H3: True})
    assert core.resolve_prompt_skill("one")["skill_id"] == core.PROMPT_SKILL_H3


def test_no_matching_or_missing_model_skill_is_safe(runtime):
    core, model = runtime
    model["id"] = ""
    assert selected(core, "one") == set()
    assert core.resolve_prompt_skill("one")["reason"] == "no_match"
    model["id"] = f"{PROMPT}/uninstalled"
    assert selected(core, "one") == set()
    assert core.resolve_prompt_skill("one")["reason"] == "missing"


def test_global_disable_wins_over_project_selection(runtime):
    core, _ = runtime
    save(core, "one", {PERFORMANCE: True})
    core.STATE["skills_disabled"] = [PERFORMANCE, core.PROMPT_SKILL_SD25]
    assert selected(core, "one") == set()
    assert not core.project_skill_enabled(PERFORMANCE, "one")
    assert core.resolve_prompt_skill("one")["reason"] == "disabled"


@pytest.mark.parametrize("body", [None, [], {"overrides": []}, {"overrides": {CUSTOM: "false"}},
                                  {"overrides": {CUSTOM: 1}}, {"overrides": {}, "unknown": True},
                                  {"overrides": {"missing/skill": True}}])
def test_rejects_invalid_selection_without_writing(runtime, body):
    core, _ = runtime
    with pytest.raises(core.ServiceError) as exc:
        asyncio.run(core.api_projconfig_set({"project": "one", "project_skills": body}))
    assert exc.value.status_code == 400
    assert not core.project_settings_path("one").exists()


def test_skill_contract_and_resume_reflect_latest_project_selection(runtime):
    core, _ = runtime
    contract = core.agent_skill_prompt(PROMPT, "one")
    assert "表演控制:项目未勾选" in contract
    assert "本工位未启用自动激活" in contract
    save(core, "one", {PERFORMANCE: True, CUSTOM: True})
    contract = core.agent_skill_prompt(PROMPT, "one")
    assert "表演控制:项目已勾选" in contract
    assert f"agents/{PROMPT}/skills/performance-direction/SKILL.md" in contract
    assert "相关工作之前完整读取" in contract and "已完成/已跳过/失败" in contract
    assert CUSTOM not in contract
    custom_contract = core.agent_skill_prompt(CUSTOM.rsplit("/", 1)[0], "one")
    assert CUSTOM in custom_contract
    assert core.skill_resume_message("继续", contract, True).endswith("继续")
    save(core, "one", {PERFORMANCE: False})
    updated = core.skill_resume_message("继续", core.agent_skill_prompt(PROMPT, "one"), True)
    assert "表演控制:项目未勾选" in updated
    assert "表演控制:项目已勾选" not in updated


def test_skill_routes_use_path_project_and_validate(runtime):
    core, _ = runtime
    from services.api.app import app
    import httpx
    async def probe():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/projects/one/skills", json={"overrides": {CUSTOM: True}})
            assert response.status_code == 200, response.text
            assert response.json()["project"] == "one"
            response = await client.get("/api/v1/projects/two/skills")
            assert response.status_code == 200
            assert not next(s["selected"] for s in response.json()["skills"] if s["id"] == CUSTOM)
            response = await client.post("/api/v1/projects/one/skills", json={"overrides": {CUSTOM: "false"}})
            assert response.status_code == 400
    asyncio.run(probe())


def test_performance_checker_skips_by_default_but_checks_when_selected(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VIDEOAGENTS_RUNTIME_DIR", str(tmp_path / "runtime"))
    spec = importlib.util.spec_from_file_location("performance_check_test", ROOT / "code/performance_bound_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(sys, "argv", ["performance_bound_check.py", "--out-root", str(tmp_path)])
    assert mod.main() == 0
    assert "skipped" in capsys.readouterr().out
    pf = tmp_path / "grp001.json"
    pf.write_text(json.dumps({"audio_plan": "dialogue", "video_prompt": "AU12 {你好}", "shots": []}))
    assert mod.check_group(pf, tmp_path, "ep01", {}, True) == ([], [], True)
    (tmp_path / "settings.json").write_text(json.dumps({"project_skills": {"overrides": {PERFORMANCE: True}}}))
    errs, _, skipped = mod.check_group(pf, tmp_path, "ep01", {}, True)
    assert not skipped and any("au_not_in_prompt" in e for e in errs)
    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime/state.json").write_text(json.dumps({"skills_disabled": [PERFORMANCE]}))
    assert mod.check_group(pf, tmp_path, "ep01", {}, True) == ([], [], True)


def test_group_override_cannot_auto_activate_an_unselected_skill(runtime, monkeypatch):
    core, _ = runtime
    monkeypatch.setattr(core, "group_video_candidates", lambda *args: {
        "provider": "ark", "global_model": "seedance-2.5", "overridable": True, "global_label": "Seedance 2.5"})
    monkeypatch.setattr(core, "video_model_caps", lambda model: None)
    monkeypatch.setattr(core, "video_model_label", lambda model, provider: model)
    monkeypatch.setattr(core, "auto_prompt_skill_for_model", lambda model: core.PROMPT_SKILL_SD20)
    gs = {"video_model": "seedance-2.0", "provider": "ark"}
    result = core.resolve_group_settings("one", "ep01", "grp001", gs=gs)
    assert result["skill_id"] == "" and result["reason"] == "disabled"
    save(core, "one", {core.PROMPT_SKILL_SD20: True})
    assert core.resolve_group_settings("one", "ep01", "grp001", gs=gs)["skill_id"] == core.PROMPT_SKILL_SD20
    save(core, "one", {core.PROMPT_SKILL_SD20: False})
    gs["prompt_skill"] = {"mode": "manual", "skill_id": core.PROMPT_SKILL_SD20}
    assert core.resolve_group_settings("one", "ep01", "grp001", gs=gs)["reason"] == "disabled"


# #137:项目设置里勾着但技能已不在安装清单(已移除 / 桌面端升级丢失)
GONE = f"{PROMPT}/high-density-fight"
ORPHAN = "plugin-gone/writer/polish"


def stale_settings(core, project, overrides):
    path = core.project_settings_path(project)
    path.write_text(json.dumps({"project_skills": {"overrides": overrides}}), encoding="utf-8")
    return path


@pytest.fixture
def stale(runtime, monkeypatch):
    core, _ = runtime
    monkeypatch.setattr(core, "list_agents", lambda refresh=False: [
        {"id": PROMPT, "name": "提示词工程师", "category_name": "视频生成"}])
    stale_settings(core, "one", {GONE: True, ORPHAN: True, f"{PROMPT}/old-off": False, CUSTOM: True})
    return core


def test_stale_selection_is_listed_as_not_installed(stale):
    core = stale
    skills = asyncio.run(core.api_project_skills_get("one"))["skills"]
    by_id = {s["id"]: s for s in skills}
    assert by_id[GONE]["installed"] is False
    assert (by_id[GONE]["selected"], by_id[GONE]["path"], by_id[GONE]["name"]) == (True, "", "high-density-fight")
    assert by_id[GONE]["agent_name"] == "提示词工程师"
    assert by_id[ORPHAN]["agent_name"] == "plugin-gone/writer" and not by_id[ORPHAN]["installed"]
    assert f"{PROMPT}/old-off" not in by_id          # 勾掉的失效项没有影响,不列出
    assert all(s["installed"] for s in skills if s["id"] not in (GONE, ORPHAN))
    # 挂在所属 Agent 的已安装技能之后(界面按 agent_id 连续分组)
    ids = [s["id"] for s in skills]
    last_prompt = max(i for i, s in enumerate(skills) if s["agent_id"] == PROMPT and s["installed"])
    assert ids.index(GONE) == last_prompt + 1 and ids[-1] == ORPHAN
    assert len(skills) == 12


def test_stale_selection_can_be_unchecked_but_not_enabled(stale):
    core = stale
    path = core.project_settings_path("one")
    # 未安装的 id 写 true 照旧拒绝,文件不动
    before = path.read_text(encoding="utf-8")
    for body in ({GONE: True}, {"missing/skill": True}, {GONE: False, "missing/skill": True}):
        with pytest.raises(core.ServiceError) as exc:
            save(core, "one", body)
        assert exc.value.status_code == 400
        assert path.read_text(encoding="utf-8") == before
    # 写 false = 从项目设置删掉该键;其它勾选与本次的已安装改动照常保存
    response = save(core, "one", {GONE: False, PERFORMANCE: True})
    overrides = json.loads(path.read_text(encoding="utf-8"))["project_skills"]["overrides"]
    assert GONE not in overrides and overrides[ORPHAN] is True
    assert overrides[CUSTOM] is True and overrides[PERFORMANCE] is True
    assert GONE not in {s["id"] for s in response["skills"]}
    save(core, "one", {ORPHAN: False, f"{PROMPT}/old-off": False})
    overrides = json.loads(path.read_text(encoding="utf-8"))["project_skills"]["overrides"]
    assert set(overrides) == {CUSTOM, PERFORMANCE}
    assert all(s["installed"] for s in core.list_project_skills("one"))


def test_skill_report_explains_not_installed(stale, monkeypatch):
    core = stale
    monkeypatch.setattr(core, "RUNS", {})
    monkeypatch.setattr(core, "publish_run", lambda run: None)
    stale_settings(core, "one", {GONE: True, PERFORMANCE: True})
    run = {"id": "run137", "project": "one", "agent": PROMPT, "agent_name": "提示词工程师",
           "status": "queued", "created": 1000, "message": "ep01/grp001"}
    core.init_skill_records(run)
    assert PERFORMANCE in run["_skill_bound_ids"] and GONE not in run["_skill_bound_ids"]
    assert run["_skill_missing_ids"] == [GONE]
    assert GONE not in {r["skill_id"] for r in run["_skill_records"]}
    run["status"] = "running"
    core.RUNS[run["id"]] = run

    def report(skill_id):
        with pytest.raises(core.ServiceError) as exc:
            asyncio.run(core.api_skill_report(run["id"], {"skill_id": skill_id, "status": "failed",
                                                          "reason": "selected but skill file unavailable"}))
        assert exc.value.status_code == 400
        return str(exc.value)

    assert "未安装" in report(GONE) and GONE in report(GONE)
    core.STATE["ui_lang"] = "en"
    assert "not installed" in report(GONE)
    assert report(CUSTOM) == "skill is not selected for this agent/run"


def test_not_installed_prompt_line_is_conditional(stale, monkeypatch):
    core = stale
    line = "不调用 skill_report 登记"
    stale_settings(core, "one", {CUSTOM: True})
    assert line not in core.agent_skill_prompt(PROMPT, "one")
    stale_settings(core, "one", {GONE: True, CUSTOM: True, PERFORMANCE: True})
    contract = core.agent_skill_prompt(PROMPT, "one")
    assert f"项目启用的技能 `{GONE}` 未安装" in contract and line in contract
    assert f"- `{GONE}`" not in contract                       # 不进「本工位已勾选」清单
    assert "表演控制:项目已勾选" in contract
    other = core.agent_skill_prompt(CUSTOM.rsplit("/", 1)[0], "one")
    assert line not in other and GONE not in other
    dispatcher = core.agent_skill_prompt(next(iter(core.DISPATCHERS)), "one")
    assert GONE not in dispatcher and CUSTOM in dispatcher
    # 表演控制本身不在安装清单时,勾着也不按「已勾选」注入
    catalog = [s for s in core.scan_agent_skills() if s["id"] != PERFORMANCE]
    monkeypatch.setattr(core, "scan_agent_skills", lambda refresh=False: catalog)
    contract = core.agent_skill_prompt(PROMPT, "one")
    assert "表演控制:项目未勾选" in contract and f"`{PERFORMANCE}`、`{GONE}`" not in contract
    assert f"`{GONE}`、`{PERFORMANCE}` 未安装" in contract
