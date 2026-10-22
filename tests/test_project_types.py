"""项目类型(2026-10-20):影视剧 / 音频配画 / 数字人 / 混剪,原三个插件并入主程序。"""
import asyncio
import io
import json
import zipfile
from pathlib import Path

import pytest

from services.runtime import core, project_types

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "PROJECTS_DIR", tmp_path / "projects")
    core.PROJECTS_DIR.mkdir()
    sent = []

    async def fake_chat(body):
        sent.append(body)
        return {"ok": True}

    async def no_op(*args, **kwargs):
        return {"ok": True}
    monkeypatch.setattr(core, "api_chat", fake_chat)
    monkeypatch.setattr(core, "_notify_settings_change", no_op)
    footage = tmp_path / "footage"
    (footage / "fabuhui").mkdir(parents=True)
    (footage / "fabuhui" / "project.json").write_text("{}")
    monkeypatch.setattr(core._footage_lib(), "FOOTAGE_DIR", footage)
    return sent


def _create(body):
    return asyncio.run(core.api_projects_create(body))


def test_registry_files_and_agents_exist():
    for tid, t in project_types.TYPES.items():
        assert (ROOT / t["workflow"]).is_file(), tid
        if tid == project_types.DEFAULT:
            continue
        assert (ROOT / t["doc"]).is_file(), tid
        for cat in t["categories"]:
            assert cat in core.CATEGORY_NAMES
            assert any((ROOT / "agents" / cat).glob("*/SOUL.md"))
        for aid in t["agents"]:
            assert (ROOT / "agents" / aid / "SOUL.md").is_file()
    for aid in project_types.STATELESS_AGENTS:
        assert (ROOT / "agents" / aid / "SOUL.md").is_file()
        assert core.is_stateless_agent(aid)
    assert project_types.of_agent("16-mashup/clip-cutter") == "mashup"
    assert project_types.of_agent("11-qa/av-sync-qa") == "audio-to-video"
    assert project_types.of_agent("11-qa/visual-qa") is None
    assert project_types.of_agent("06-art/art-director") is None


def test_workflow_dags_reference_existing_agents():
    yaml = pytest.importorskip("yaml")
    for tid, t in project_types.TYPES.items():
        dag = yaml.safe_load((ROOT / t["workflow"]).read_text(encoding="utf-8"))
        if tid != project_types.DEFAULT:
            assert dag.get("project_type") == tid
            assert "plugin" not in dag
        for ph in dag["phases"]:
            for task in ph.get("tasks", []):
                if task.get("agent"):
                    assert (ROOT / "agents" / task["agent"] / "SOUL.md").is_file(), (tid, task["id"])


def test_agent_list_tags_project_type():
    by_id = {a["id"]: a for a in core.list_agents(refresh=True)}
    assert by_id["17-digital-human/avatar-generator"]["project_type"] == "digital-human"
    assert by_id["17-digital-human/avatar-generator"]["plugin"] is None
    assert by_id["16-mashup/shot-designer"]["category_name"] == "混剪"
    assert by_id["07-directing/storyboard"]["project_type"] is None


def test_project_type_members_default_to_high_tier():
    # 项目类型专属成员智能分配一律高档;11-qa/ 下全部低档,含各类型的终审(2026-10-21 用户要求)
    agents = core.list_agents(refresh=True)
    members = [a["id"] for a in agents if a["project_type"] and not a["id"].startswith("11-qa/")]
    assert len(members) >= 16
    for aid in members:
        assert core.default_agent_model(aid, "smart_claude") == core.AM_MODE_MODELS["smart_claude"]["high"], aid
    qa = [a["id"] for a in agents if a["id"].startswith("11-qa/")]
    assert "11-qa/mashup-qa" in qa
    for aid in qa:
        assert core.default_agent_model(aid, "smart_claude") == core.AM_MODE_MODELS["smart_claude"]["low"], aid


def test_film_project_unchanged(env):
    _create({"name": "novel", "novel": "第一章", "settings": {"output": {"narration_enabled": True}}})
    cfg = core.load_project_settings("novel")
    assert cfg["project"] == {"type": "film"}
    assert cfg["output"]["narration_enabled"] is True
    pdir = core.PROJECTS_DIR / "novel"
    assert (pdir / "novel" / "original.txt").read_text(encoding="utf-8") == "第一章"
    assert not (pdir / "refs" / "text" / "transcript.txt").exists()
    msg = env[0]["message"]
    assert "小说原文已导入 novel/original.txt(约 3 字)" in msg and "项目类型" not in msg


def test_mashup_project_forces_settings_and_writes_inputs(env):
    _create({"name": "mx", "project_type": "mashup", "novel": "说曹操曹操到", "brief": "纪实",
             "footage_libraries": ["fabuhui", "fabuhui", "../x"],
             "rhythm": {"episode": "classic"},
             "settings": {"output": {"narration_enabled": True, "spatial_blocking": True},
                          "transitions": {"mode": "cinematic"}}})
    cfg = core.load_project_settings("mx")
    assert cfg["project"] == {"type": "mashup", "footage_libraries": ["fabuhui"]}
    assert cfg["output"]["narration_enabled"] is False and cfg["output"]["spatial_blocking"] is False
    assert cfg["transitions"]["mode"] == "minimal"
    assert cfg["transitions"]["episode_close"]["type"] == "hard_cut"
    assert cfg["duration"]["episode_minutes"] == "auto"
    assert not any(cfg["packaging"][k] for k in ("intro_enabled", "outro_enabled", "teaser_enabled"))
    pdir = core.PROJECTS_DIR / "mx"
    assert (pdir / "refs" / "text" / "transcript.txt").read_text(encoding="utf-8").strip() == "说曹操曹操到"
    assert (pdir / "novel" / "original.txt").read_text(encoding="utf-8") == "说曹操曹操到"
    assert "叙事节奏" not in (pdir / "brief.md").read_text(encoding="utf-8")
    msg = env[0]["message"]
    assert "agents/workflows/mashup.yaml" in msg and "data/footage/fabuhui" in msg
    assert "讲述音频:未上传(可选" in msg and "现在不要派单" in msg


def test_unknown_type_and_missing_library_rejected(env):
    with pytest.raises(core.ServiceError) as e:
        _create({"name": "bad", "project_type": "anime"})
    assert e.value.status_code == 400
    with pytest.raises(core.ServiceError) as e:
        _create({"name": "bad", "project_type": "mashup", "footage_libraries": ["nope"]})
    assert "nope" in e.value.detail
    assert not (core.PROJECTS_DIR / "bad").exists()


def test_deferred_init_dispatches_once_after_uploads(env):
    r = _create({"name": "dh", "project_type": "digital-human", "brief": "第一个说话人是主持人",
                 "defer_init": True})
    assert r["init"] == "pending" and env == []
    asyncio.run(core.api_refs_upload(b"ID3fake", "dh", "audio", "", "podcast.mp3"))
    asyncio.run(core.api_refs_upload(b"\x89PNG", "dh", "characters", "", "host.png"))
    with pytest.raises(core.ServiceError):          # 讲述音频分类只收音频
        asyncio.run(core.api_refs_upload(b"x", "dh", "audio", "", "notes.txt"))
    asyncio.run(core.api_projects_init("dh"))
    assert len(env) == 1
    msg = env[0]["message"]
    assert "refs/audio/podcast.mp3" in msg and "refs/characters/host.png" in msg
    assert "文稿:未提供(可选" in msg and "人物与声音的对应说明" in msg
    with pytest.raises(core.ServiceError) as e:
        asyncio.run(core.api_projects_init("dh"))
    assert e.value.status_code == 409 and len(env) == 1


def test_digital_human_voice_source(env):
    _create({"name": "rec", "project_type": "digital-human"})
    assert core.load_project_settings("rec")["project"] == {"type": "digital-human", "voice_source": "recording"}
    assert "dh0-voice`(克隆配音)不适用" in core.build_role_prompt("00-orchestration/workflow-orchestrator", "rec")
    with pytest.raises(core.ServiceError):
        _create({"name": "bad", "project_type": "digital-human", "voice_source": "tts"})
    _create({"name": "cl", "project_type": "digital-human", "voice_source": "clone",
             "novel": "主持人:你好\n嘉宾:你好", "defer_init": True})
    asyncio.run(core.api_refs_upload(b"ID3fake", "cl", "voices", "", "host.mp3"))
    with pytest.raises(core.ServiceError):
        asyncio.run(core.api_refs_upload(b"x", "cl", "voices", "", "host.png"))
    asyncio.run(core.api_projects_init("cl"))
    msg = env[-1]["message"]
    assert "声音来源:克隆声音" in msg and "refs/voices/host.mp3" in msg and "台词稿:refs/text/transcript.txt" in msg
    assert "音频母带" not in msg
    orch = core.build_role_prompt("00-orchestration/workflow-orchestrator", "cl")
    assert "声音来源 = 克隆声音" in orch and "code/dialogue_clone.py" in orch and "refs/voices/" in orch
    keys = [c["key"] for c in asyncio.run(core.api_refs_list("cl"))["categories"]]
    assert keys[:2] == ["audio", "voices"]
    _create({"name": "novel"})
    assert "voices" not in [c["key"] for c in asyncio.run(core.api_refs_list("novel"))["categories"]]


def test_type_is_locked_and_legacy_projects_inferred(env):
    _create({"name": "av", "project_type": "audio-to-video"})
    # 设置接口里 project 是项目名;project 段锁定,保存其它段时原样保留
    asyncio.run(core.api_projconfig_set({"project": "av", "review": {"evaluation": 50}}))
    assert core.project_type_of("av") == "audio-to-video"
    assert core.load_project_settings("av")["review"]["evaluation"] == 50

    legacy = core.PROJECTS_DIR / "old"
    (legacy / "mashup").mkdir(parents=True)
    (legacy / "settings.json").write_text("{}")
    assert core.project_type_of("old") == "film"
    (legacy / "mashup" / "beat_track.json").write_text("{}")
    assert core.project_type_of("old") == "mashup"
    asyncio.run(core.api_projconfig_set({"project": "old", "review": {"evaluation": 0}}))
    saved = json.loads((legacy / "settings.json").read_text(encoding="utf-8"))
    assert saved["project"]["type"] == "mashup"


def test_prompts_carry_project_type(env):
    _create({"name": "mx", "project_type": "mashup", "footage_libraries": ["fabuhui"]})
    _create({"name": "novel"})
    orch = core.build_role_prompt("00-orchestration/workflow-orchestrator", "mx")
    assert "## 项目类型:混剪(mashup" in orch and "fabuhui" in orch and "16-mashup/clip-cutter" in orch
    member = core.build_role_prompt("16-mashup/footage-scout", "mx")
    assert "你属于「混剪」项目类型团队" in member and "/mashup/" in member
    reused = core.build_role_prompt("10-editing/edit", "mx")
    assert "## 本项目类型:混剪" in reused
    for agent in ("00-orchestration/workflow-orchestrator", "10-editing/edit"):
        film = core.build_role_prompt(agent, "novel")
        assert "\n## 项目类型:" not in film and "\n## 本项目类型:" not in film


def test_refs_audio_category_by_type(env):
    _create({"name": "novel"})
    _create({"name": "av", "project_type": "audio-to-video"})
    film = [c["key"] for c in asyncio.run(core.api_refs_list("novel"))["categories"]]
    assert "audio" not in film
    assert [c["key"] for c in asyncio.run(core.api_refs_list("av"))["categories"]][0] == "audio"


def _plugin_zip(name):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{name}/plugin.json", json.dumps({"name": name, "agents": ["16-mashup/audio-ingest"]}))
        zf.writestr(f"{name}/agents/16-mashup/audio-ingest/SOUL.md", "# SOUL.md — x\n")
    return buf.getvalue()


def test_integrated_plugin_packages(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "BUNDLED_PLUGINS_DIR", tmp_path / "bundled")
    monkeypatch.setattr(core, "PLUGINS_DIR", tmp_path / "user")
    old = tmp_path / "user" / "mashup"
    (old / "agents" / "16-mashup" / "audio-ingest").mkdir(parents=True)
    (old / "plugin.json").write_text(json.dumps({"name": "mashup", "agents": ["16-mashup/audio-ingest"]}))
    (old / "agents" / "16-mashup" / "audio-ingest" / "SOUL.md").write_text("# SOUL.md — x\n")
    monkeypatch.setitem(core.STATE, "plugins_enabled", ["mashup"])
    try:
        info = next(p for p in core.list_plugins(refresh=True) if p["name"] == "mashup")
        assert info["integrated"] == "mashup" and not info["active"] and info["agents"] == []
        assert info["errors"] and "混剪" in info["errors"][0]
        assert core.plugin_of_agent("16-mashup/audio-ingest") is None
        with pytest.raises(core.ServiceError) as e:
            asyncio.run(core.api_plugins_upload(_plugin_zip("digital-human")))
        assert e.value.status_code == 400
    finally:
        core._expire_agent_caches()


@pytest.mark.parametrize("nid,phase", [
    ("av0-ingest", "av0"), ("ga3-ep01", "av3"), ("mx1-scout-b01", "mx1"), ("mx1m-merge", "mx1m"),
    ("gm1", "mx1m"), ("gm2", "mx3"), ("dh2-avatar-utt0001", "dh2"), ("p6-shots-ep01", "p6"), ("ep06-x", "extra")])
def test_workflow_preview_phase_of(nid, phase):
    assert core._wf_phase_of({"id": nid}) == phase


# ---- #149:main_dag_on_start.skip 须覆盖本类型不适用的全部主流程节点 ----
# 宿主待办前沿把 skipped 依赖视同满足(WORKFLOW.md §10.3 第 6 条),漏列的主流程节点在上游被跳过后会出现在前沿上
# (p0-bible-init 只依赖已 done 的 p0-init,看门狗反复唤醒总制片);依赖全 skipped 的人工闸门永远停在 pending。
def _wf_nodes(rel: str) -> list[dict]:
    """DAG 模板节点,phase 级 depends_on 并入各节点;@prev_group 之类组间自依赖去掉。"""
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))
    out = []
    for ph in doc["phases"]:
        for t in ph.get("tasks", []):
            deps = [d for d in list(t.get("depends_on") or []) + list(ph.get("depends_on") or []) if "@" not in d]
            out.append({"id": t["id"], "phase": ph["id"], "for_each": t.get("for_each") or ph.get("for_each"),
                        "human": bool(t.get("human") or t.get("human_on")), "depends_on": deps})
    return out


def _skip_hit(entry: str, nid: str) -> bool:
    return nid.startswith(entry[:-1]) if entry.endswith("*") else nid == entry


_MAIN = "agents/workflow.yaml"
_NOVEL = "plugins/derivative-fiction/workflows/novel.yaml"


def _skip_keep() -> dict[str, set[str]]:
    """各类型 / 插件 DAG 刻意保留(不跳)的主流程节点。"""
    main = _wf_nodes(_MAIN)
    keep = {project_types.TYPES[t]["workflow"]: {"p0-init"}
            for t in ("audio-to-video", "code-video", "digital-human")}
    # 混剪:文稿走 p0 解析、p1 structure/events 出 story_graph / events(mx0-design 的输入),g0 照常
    keep[project_types.TYPES["mashup"]["workflow"]] = {
        "p0-init", "p0-bible-init", "p0-scan", "p0-parse", "p0-merge", "g0", "p1-structure", "p1-events"}
    # 衍生小说:只依赖正史 bible/(p0–p3 文字设定与 H1 / H1A),声纹 / 灯光与 p4 起的视听节点全跳
    keep[_NOVEL] = {n["id"] for n in main if n["phase"] in ("p0", "p1", "p2", "p3")} - {"p3-voice", "p3-lighting"}
    return keep


def test_main_dag_skip_lists_cover_all_inapplicable_main_nodes():
    yaml = pytest.importorskip("yaml")
    main = _wf_nodes(_MAIN)
    ids = {n["id"] for n in main}
    fanout = {n["id"] for n in main if n["for_each"]}
    keep = _skip_keep()
    assert set(keep) == {t["workflow"] for tid, t in project_types.TYPES.items()
                         if tid != project_types.DEFAULT} | {_NOVEL}
    for rel, kept in keep.items():
        skip = yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))["main_dag_on_start"]["skip"]
        for e in skip:   # 防笔误:精确 id 须存在;通配须命中模板节点,或是按 for_each 展开的 <模板 id>-*
            assert any(_skip_hit(e, i) for i in ids) or (e.endswith("-*") and e[:-2] in fanout), (rel, e)
        uncovered = {i for i in ids if not any(_skip_hit(e, i) for e in skip)}
        assert uncovered == kept, (rel, sorted(uncovered - kept), sorted(kept - uncovered))


def test_main_dag_skip_lists_leave_no_stray_main_frontier():
    """立项时(p0-init done、清单节点 skipped、其余 pending)的待办前沿里,主流程节点只能是该类型刻意保留的,
    也不会冒出主流程人工闸门的签字单。"""
    yaml = pytest.importorskip("yaml")
    from services.runtime import dagcheck
    main = _wf_nodes(_MAIN)
    for rel, kept in _skip_keep().items():
        skip = yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))["main_dag_on_start"]["skip"]
        nodes = [{**n, "state": "done" if n["id"] == "p0-init" else
                  "skipped" if any(_skip_hit(e, n["id"]) for e in skip) else "pending"} for n in main]
        nodes += [{**n, "state": "pending"} for n in _wf_nodes(rel)]
        main_ids = {n["id"] for n in main}
        runnable, human = dagcheck.ready_nodes(nodes)
        assert {i for i in runnable if i in main_ids} <= kept, (rel, runnable)
        assert not [i for i in human if i in main_ids], (rel, human)
        assert [i for i in runnable if i not in main_ids], rel     # 类型 / 插件 DAG 的首节点照常可派
