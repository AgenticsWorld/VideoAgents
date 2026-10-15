"""Agent 高级设置「多集并行」:默认关 = 按集顺序推进(当前集全部节点结束才派下一集,发布环节 p11-* 不计);
看门狗前沿、闸门补建签字单、dagcheck --frontier 同口径;开关经运行提示词只注入调度型 Agent。"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("VIDEOAGENTS_DATA_DIR", tempfile.mkdtemp(prefix="va-eppar-"))

from services.runtime import core, dagcheck  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PROJ = "epparalleltest"


def _nodes():
    return [
        {"id": "g5", "state": "passed", "human": True},
        {"id": "p8-voiceprints", "state": "pending", "depends_on": ["g5"]},            # 项目级
        {"id": "p5-screenplay-ep01", "state": "passed", "depends_on": ["g5"]},
        {"id": "p6-shots-ep01", "state": "passed", "depends_on": ["p5-screenplay-ep01"]},
        {"id": "p7-video-ep01-grp001", "state": "pending", "depends_on": ["p6-shots-ep01"]},
        {"id": "p7-video-ep01-grp002", "state": "pending", "depends_on": ["p6-shots-ep01"]},
        {"id": "p11-publish-xhs-ep01", "state": "pending", "depends_on": ["p6-shots-ep01"]},
        {"id": "p5-screenplay-ep02", "state": "pending", "depends_on": ["g5"]},
        {"id": "g6-ep02", "state": "pending", "human": True, "depends_on": ["g5"]},
        {"id": "p5-screenplay-ep10", "state": "pending", "depends_on": ["g5"]},
        {"id": "p6-plan", "state": "expanded", "depends_on": ["g5"]},
    ]


def _write_dag(tmp_path, nodes):
    runs = tmp_path / PROJ / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    (runs / "dag.json").write_text(json.dumps({"nodes": nodes}))
    return runs / "dag.json"


def test_node_episode():
    assert dagcheck.node_episode({"id": "p7-video-ep03-grp012"}) == 3
    assert dagcheck.node_episode({"id": "g10-ep12"}) == 12
    assert dagcheck.node_episode({"id": "x", "for_each": {"episode": "ep05"}}) == 5
    assert dagcheck.node_episode({"id": "p8-voiceprints"}) is None
    assert dagcheck.node_episode({"id": "p11-bundle-ep01-ep03"}) is None    # 跨多集不归属单集
    assert dagcheck.node_episode({"id": "deep02-check"}) is None            # 词中 ep 不算集号


def test_episode_inherited_from_dependencies():
    nodes = _nodes() + [
        {"id": "p6-scene-model-SCN-0007", "state": "pending", "depends_on": ["p5-screenplay-ep02"]},
        {"id": "p6-scene-model-SCN-0008", "state": "pending", "depends_on": ["p6-scene-model-SCN-0007"]},
        {"id": "p10-season-qa", "state": "pending", "depends_on": ["p5-screenplay-ep01", "p5-screenplay-ep02"]},
        # 项目级闸门 / 依赖里混有项目级节点的,即使另一个依赖是 ep01 也不归 ep01(前科 fengshen3 g5)
        {"id": "g5b", "state": "blocked", "human": True, "depends_on": ["p5-screenplay-ep01"]},
        {"id": "p5-merge", "state": "pending", "depends_on": ["p5-screenplay-ep01", "p8-voiceprints"]},
    ]
    eps = dagcheck.episode_map(nodes)
    assert eps["p6-scene-model-SCN-0007"] == 2 and eps["p6-scene-model-SCN-0008"] == 2
    assert eps["p10-season-qa"] is None and eps["p8-voiceprints"] is None
    assert eps["g5b"] is None and eps["p5-merge"] is None
    assert {"p6-scene-model-SCN-0007", "p6-scene-model-SCN-0008"} <= dagcheck.episode_serial_hold(nodes)[1]


def test_serial_hold_current_episode_and_publish_excluded():
    nodes = _nodes()
    cur, held = dagcheck.episode_serial_hold(nodes)
    assert cur == 1
    assert held == {"p5-screenplay-ep02", "g6-ep02", "p5-screenplay-ep10"}
    # ep01 只剩发布环节未结束 → 视为做完,当前集推到 ep02;ep01 的发布节点照常可派
    for n in nodes:
        if n["id"].startswith("p7-video-ep01"):
            n["state"] = "passed"
    cur, held = dagcheck.episode_serial_hold(nodes)
    assert cur == 2 and held == {"p5-screenplay-ep10"}
    f = dagcheck.frontier(nodes, parallel=False)
    assert "p11-publish-xhs-ep01" in f["runnable"] and "p5-screenplay-ep02" in f["runnable"]
    assert f["human_waiting"] == ["g6-ep02"] and f["held_ready"] == ["p5-screenplay-ep10"]


def test_serial_hold_blocked_or_reopened_keeps_episode_open():
    nodes = _nodes()
    for n in nodes:
        if n["id"].startswith("p7-video-ep01"):
            n["state"] = "blocked"          # 用户暂缓 = 该集未做完,后续集不开始
    assert dagcheck.episode_serial_hold(nodes)[0] == 1
    for n in nodes:
        if n["id"] in ("p7-video-ep01-grp001", "p7-video-ep01-grp002", "p11-publish-xhs-ep01",
                       "p5-screenplay-ep02", "g6-ep02"):
            n["state"] = "passed"
    nodes[2]["state"] = "pending"         # 已完成的 ep01 被标脏 → 重新成为当前集
    cur, held = dagcheck.episode_serial_hold(nodes)
    assert cur == 1 and held == {"p5-screenplay-ep10"}


def test_parallel_mode_has_no_hold():
    f = dagcheck.frontier(_nodes(), parallel=True)
    assert f["held"] == [] and f["current_episode"] is None
    assert {"p5-screenplay-ep02", "p5-screenplay-ep10"} <= set(f["runnable"])


def test_dag_runnable_follows_setting(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "PROJECTS_DIR", tmp_path)
    _write_dag(tmp_path, _nodes())
    monkeypatch.delitem(core.STATE, "episode_parallel", raising=False)
    runnable, human = core._dag_runnable(PROJ)
    assert sorted(runnable) == ["p11-publish-xhs-ep01", "p7-video-ep01-grp001",
                                "p7-video-ep01-grp002", "p8-voiceprints"]
    assert human == []
    monkeypatch.setitem(core.STATE, "episode_parallel", True)
    runnable, human = core._dag_runnable(PROJ)
    assert "p5-screenplay-ep02" in runnable and "p5-screenplay-ep10" in runnable
    assert human == ["g6-ep02"]


def test_gate_guard_skips_held_gate(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "PROJECTS_DIR", tmp_path)
    _write_dag(tmp_path, _nodes())
    monkeypatch.setattr(core, "CONFIRMS", {})
    calls = []

    async def fake_create(body):
        calls.append(body["gate_id"])
        return {"confirm_id": f"c{len(calls)}"}

    monkeypatch.setattr(core, "api_confirm_create", fake_create)
    monkeypatch.delitem(core.STATE, "episode_parallel", raising=False)
    assert asyncio.run(core.ensure_human_gate_approvals(PROJ)) == [] and calls == []
    monkeypatch.setitem(core.STATE, "episode_parallel", True)
    assert asyncio.run(core.ensure_human_gate_approvals(PROJ)) == ["c1"] and calls == ["g6-ep02"]


def test_default_off_and_api_roundtrip(monkeypatch):
    monkeypatch.setattr(core, "save_state", lambda state: None)
    monkeypatch.delitem(core.STATE, "episode_parallel", raising=False)
    assert core.episode_parallel_setting() is False
    assert asyncio.run(core.api_agent_advanced_get())["episode_parallel"] is False
    out = asyncio.run(core.api_agent_advanced_set({"episode_parallel": True}))
    assert out["episode_parallel"] is True and core.episode_parallel_setting() is True
    monkeypatch.delitem(core.STATE, "episode_parallel", raising=False)


def test_prompt_injected_for_dispatcher_only(monkeypatch):
    core.ensure_project(PROJ)
    monkeypatch.delitem(core.STATE, "episode_parallel", raising=False)
    off = core.build_role_prompt(core.ORCHESTRATOR_AGENT, PROJ)
    assert "多集并行:**关闭**" in off and "--frontier" in off and "多集并行:**开启**" not in off
    monkeypatch.setitem(core.STATE, "episode_parallel", True)
    on = core.build_role_prompt(core.ORCHESTRATOR_AGENT, PROJ)
    assert "多集并行:**开启**" in on and "多集并行:**关闭**" not in on
    assert "多集推进设定" not in core.build_role_prompt("06-art/prop", PROJ)


def test_cli_frontier(tmp_path):
    dag = tmp_path / "dag.json"
    dag.write_text(json.dumps({"nodes": _nodes()}))
    cmd = [sys.executable, str(ROOT / "services/runtime/dagcheck.py"), str(dag), "--frontier"]
    serial = subprocess.run(cmd + ["--episode-serial"], capture_output=True, text=True, cwd=ROOT)
    assert serial.returncode == 0, serial.stdout + serial.stderr
    assert "当前集:ep01" in serial.stdout and "p5-screenplay-ep10" in serial.stdout
    ready_line = next(x for x in serial.stdout.splitlines() if x.startswith("可派节点"))
    assert "p5-screenplay-ep02" not in ready_line and "p8-voiceprints" in ready_line
    par = subprocess.run(cmd + ["--episode-parallel"], capture_output=True, text=True, cwd=ROOT)
    assert "多集并行:开启" in par.stdout and "按集顺序暂缓" not in par.stdout
    # 不带模式参数时读用户设置(state.json episode_parallel)
    rt = tmp_path / "rt"
    rt.mkdir()
    (rt / "state.json").write_text(json.dumps({"episode_parallel": True}))
    env = {**os.environ, "VIDEOAGENTS_RUNTIME_DIR": str(rt)}
    live = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT, env=env)
    assert "多集并行:开启" in live.stdout
