"""编号制(场次/分镜组/分镜):新建项目预留插入位制,存量项目逐一制不变。"""
import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules import genmedia, id_scheme  # noqa: E402


def _project(tmp_path, name, settings):
    base = tmp_path / "projects" / name
    base.mkdir(parents=True)
    (base / "settings.json").write_text(json.dumps(settings))
    return base


def test_format_and_normalize():
    assert [id_scheme.format_id(p, 1, 10) for p in ("S", "grp", "sh")] == ["S010", "grp0010", "sh0010"]
    assert id_scheme.format_id("grp", 12, 10) == "grp0120"
    assert [id_scheme.format_id(p, 2, 1) for p in ("S", "grp", "sh")] == ["S02", "grp002", "sh002"]
    for bad in (None, {}, {"step": 7}, {"step": "x"}, "10"):
        assert id_scheme.normalize(bad) == {"step": 1}
    assert id_scheme.normalize({"step": "10"}) == {"step": 10}
    assert id_scheme.prompt_section(1) == ""
    assert "grp0011" in id_scheme.prompt_section(10)


def test_project_step_defaults_to_legacy(tmp_path):
    assert id_scheme.project_step(_project(tmp_path, "new", {"numbering": {"step": 10}})) == 10
    assert id_scheme.project_step(_project(tmp_path, "old", {"output": {}})) == 1
    assert id_scheme.project_step(tmp_path / "missing") == 1


def test_genmedia_digit_check_follows_project(tmp_path, monkeypatch):
    monkeypatch.delenv("VIDEOAGENTS_PROJECT", raising=False)
    monkeypatch.delenv("WEBUI_PROJECT", raising=False)
    new = _project(tmp_path, "new", {"numbering": {"step": 10}})
    old = _project(tmp_path, "old", {})
    genmedia._check_id_digits(new / "assets/clips/ep01/grp0010.mp4",
                              new / "assets/keyframes/ep01/sh0011/first_01.png")
    genmedia._check_id_digits(old / "assets/clips/ep01/grp001.mp4")
    for bad in (new / "assets/clips/ep01/grp001.mp4", new / "assets/keyframes/ep01/sh010/a.png",
                old / "assets/clips/ep01/grp01.mp4"):
        with pytest.raises(RuntimeError, match="grpsh_id_3digits"):
            genmedia._check_id_digits(bad)


def test_genmedia_digit_check_insert_group_hint(tmp_path, monkeypatch):
    """#86:逐一制项目遇四位插入号不给 grp052 式误导正名,改提示相邻 +1 / 取末尾空号;短号仍给补零正名。"""
    monkeypatch.delenv("VIDEOAGENTS_PROJECT", raising=False)
    monkeypatch.delenv("WEBUI_PROJECT", raising=False)
    old = _project(tmp_path, "old", {})
    new = _project(tmp_path, "new", {"numbering": {"step": 10}})
    with pytest.raises(RuntimeError) as e:
        genmedia._check_id_digits(old / "assets/clips/ep01/grp0052.mp4")
    msg = str(e.value)
    assert "grpsh_id_3digits" in msg and "'grp052'" not in msg
    assert "相邻组号 +1" in msg and "末尾空号" in msg
    with pytest.raises(RuntimeError, match="'grp001'"):
        genmedia._check_id_digits(old / "assets/clips/ep01/grp01.mp4")
    with pytest.raises(RuntimeError, match="grp0011"):
        genmedia._check_id_digits(new / "assets/clips/ep01/grp005.mp4")

def test_propose_groups_numbering():
    spec = importlib.util.spec_from_file_location("cgg", ROOT / "code" / "check_generation_groups.py")
    cgg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cgg)
    shots = [{"shot_id": f"sh{i}", "scene_id": f"SCN-{i}", "duration_s": 5, "characters": []}
             for i in range(3)]
    assert [g["group_id"] for g in cgg.propose_groups(shots, 10)] == ["grp0010", "grp0020", "grp0030"]
    assert [g["group_id"] for g in cgg.propose_groups(shots)] == ["grp001", "grp002", "grp003"]


def test_narration_anchor_tokens():
    spec = importlib.util.spec_from_file_location("cns", ROOT / "code" / "check_narration_sync.py")
    cns = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "code"))
    spec.loader.exec_module(cns)
    assert cns.anchor_tokens("grp0010 起 sh0011,旧 grp001/sh002") == (
        {"grp0010", "grp001"}, {"sh0011", "sh002"})


def test_new_project_is_stamped_and_locked(tmp_path, monkeypatch):
    from services.runtime import core

    monkeypatch.setattr(core, "PROJECTS_DIR", tmp_path / "projects")

    async def no_op(*args, **kwargs):
        return {"ok": True}
    monkeypatch.setattr(core, "api_chat", no_op)
    monkeypatch.setattr(core, "_notify_settings_change", no_op)
    (core.PROJECTS_DIR / "old").mkdir(parents=True)
    (core.PROJECTS_DIR / "old" / "settings.json").write_text("{}")

    asyncio.run(core.api_projects_create({"name": "fresh", "settings": {"numbering": {"step": 1}}}))
    assert core.load_project_settings("fresh")["numbering"] == {"step": 10}
    assert core.load_project_settings("old")["numbering"] == {"step": 1}

    asyncio.run(core.api_projconfig_set({"project": "fresh", "numbering": {"step": 1}}))
    asyncio.run(core.api_projconfig_set({"project": "old", "numbering": {"step": 10}}))
    assert core.load_project_settings("fresh")["numbering"] == {"step": 10}
    assert core.load_project_settings("old")["numbering"] == {"step": 1}

    agent = "07-directing/shot-planning"
    head = "\n## 编号制:预留插入位(本项目新建时已锁定"     # 注入节的节头(SOUL 正文只引用节名)
    assert head in core.build_role_prompt(agent, "fresh")
    assert head not in core.build_role_prompt(agent, "old")
