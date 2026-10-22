"""#72:看门狗可派待办统计不得把 template/expanded 扇出骨架算进去。"""
import json

from services.runtime import core


def test_dag_runnable_skips_template_and_expanded(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "PROJECTS_DIR", tmp_path)
    runs = tmp_path / "p" / "runs"
    runs.mkdir(parents=True)
    nodes = [
        {"id": "a", "state": "passed"},
        {"id": "tpl", "state": "template", "human": False, "depends_on": ["a"]},
        {"id": "exp", "state": "expanded", "human": False, "depends_on": ["a"]},
        {"id": "go", "state": "pending", "human": False, "depends_on": ["a"]},
        {"id": "h-tpl", "state": "template", "human": True, "depends_on": ["a"]},
        {"id": "h", "state": "pending", "human": True, "depends_on": ["a"]},
    ]
    (runs / "dag.json").write_text(json.dumps({"nodes": nodes}))
    assert core._dag_runnable("p") == (["go"], ["h"])


# ---- #149:skipped / waived 依赖视同满足(WORKFLOW.md §10.3 第 6 条),人工闸门另加保护 ----
import asyncio  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

from services.runtime import dagcheck  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _dag(tmp_path, monkeypatch, nodes, proj="p"):
    monkeypatch.setattr(core, "PROJECTS_DIR", tmp_path)
    runs = tmp_path / proj / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    (runs / "dag.json").write_text(json.dumps({"nodes": nodes}))
    return runs / "dag.json"


def test_skipped_and_waived_deps_count_as_satisfied_cancelled_does_not():
    nodes = [
        {"id": "a", "state": "passed"}, {"id": "s", "state": "skipped"},
        {"id": "w", "state": "waived"}, {"id": "c", "state": "cancelled"},
        {"id": "x-skip", "state": "pending", "depends_on": ["a", "s"]},
        {"id": "x-waive", "state": "pending", "depends_on": ["a", "w"]},
        {"id": "x-only-skip", "state": "pending", "depends_on": ["s", "w"]},   # 非人工节点不受闸门保护
        {"id": "x-cancel", "state": "pending", "depends_on": ["a", "c"]},
        {"id": "x-missing", "state": "pending", "depends_on": ["a", "nope"]},
    ]
    runnable, human = dagcheck.ready_nodes(nodes)
    assert runnable == ["x-skip", "x-waive", "x-only-skip"] and human == []
    assert dagcheck.SATISFIED_STATES == dagcheck.DONE_STATES | {"skipped", "waived"}
    assert "cancelled" not in dagcheck.SATISFIED_STATES


def test_issue_repro_codevideo_engine_after_beatsync_skipped(tmp_path, monkeypatch):
    """代码作曲:cv2-beatsync 置 skipped,cv3-engine 其余依赖 passed → 进可派前沿(看门狗与 --frontier 同口径)。"""
    path = _dag(tmp_path, monkeypatch, [
        {"id": "gc1", "state": "passed", "human": True, "depends_on": []},
        {"id": "cv1-look", "state": "passed"},
        {"id": "cv2-beatsync", "state": "skipped", "skip_reason": "music_source=code"},
        {"id": "cv3-engine", "state": "pending", "depends_on": ["gc1", "cv1-look", "cv2-beatsync"]},
    ])
    monkeypatch.setattr(core, "episode_parallel_setting", lambda: True)
    assert core._dag_runnable("p") == (["cv3-engine"], [])
    out = subprocess.run([sys.executable, str(ROOT / "services/runtime/dagcheck.py"), str(path),
                          "--frontier", "--episode-parallel"], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "可派节点(依赖已满足):cv3-engine" in out.stdout


def _gate_nodes():
    return [
        {"id": "p6-storyboard-ep01", "state": "skipped"},
        {"id": "p9-title-ep01", "state": "skipped"}, {"id": "p9-subtitle-ep01", "state": "passed"},
        {"id": "p10-logic-ep01", "state": "waived"},
        # 依赖全 skipped / waived:skip 清单漏列的闸门,不算解锁
        {"id": "g6s-ep01", "state": "pending", "human": True, "gate": True,
         "checkpoint": "H3S-故事板确认", "depends_on": ["p6-storyboard-ep01"]},
        {"id": "g10-ep01", "state": "pending", "human": True, "gate": True,
         "checkpoint": "H5-发布签字", "depends_on": ["p9-title-ep01", "p10-logic-ep01"]},
        # 有一个真正完成的依赖,其余 skipped:照常解锁
        {"id": "g9-ep01", "state": "pending", "human": True, "gate": True,
         "checkpoint": "H4-首集成片确认", "depends_on": ["p9-subtitle-ep01", "p9-title-ep01"]},
        {"id": "g-free", "state": "pending", "human": True, "depends_on": []},   # 无依赖:沿用原口径
    ]


def test_human_gate_guard_all_skipped_deps_not_ready(tmp_path, monkeypatch):
    nodes = _gate_nodes()
    assert dagcheck.ready_nodes(nodes) == ([], ["g9-ep01", "g-free"])
    assert [n["id"] for n in dagcheck.ready_human_gates(nodes)] == ["g9-ep01", "g-free"]
    _dag(tmp_path, monkeypatch, nodes)
    assert [n["id"] for n in core._ready_human_gates("p")] == ["g9-ep01", "g-free"]
    # 签字绑定同口径:点名全 skipped 的闸门绑不上,点名真解锁的闸门照常绑定
    assert core._sign_gate_binding("p", "【H3S-故事板确认｜项目 p】", "g6s-ep01") is None
    assert core._sign_gate_binding("p", "【H4-首集成片确认｜项目 p】", "g9-ep01") == ("g9-ep01", "H4-首集成片确认")


def test_gate_guard_runtime_creates_cards_only_for_really_unlocked(tmp_path, monkeypatch):
    """漏单守卫 ensure_human_gate_approvals(启动 / 运行结束 / 全自动勾选都走它)只为真正解锁的闸门建签字单。"""
    _dag(tmp_path, monkeypatch, _gate_nodes())
    monkeypatch.setattr(core, "episode_parallel_setting", lambda: True)
    monkeypatch.setattr(core, "notify_user", lambda *a, **k: None)
    monkeypatch.setattr(core.HUB, "publish", lambda ev: None)
    monkeypatch.setitem(core.STATE, "full_auto", {})
    core.CONFIRMS.clear()
    loop = asyncio.new_event_loop()
    try:
        created = loop.run_until_complete(core.ensure_human_gate_approvals("p"))
        gates = sorted(core.CONFIRMS[c]["gate_id"] for c in created)
        assert gates == ["g-free", "g9-ep01"]
    finally:
        loop.close()
        core.CONFIRMS.clear()


def test_workflow_preview_marks_sign_ready_by_dagcheck(tmp_path, monkeypatch):
    """工作流预览页「等待签字」以服务端 deps_ready 为准,与补建签字单同口径。"""
    _dag(tmp_path, monkeypatch, _gate_nodes())
    rows = {r["id"]: r for r in core._preview_workflow("p")["nodes"]}
    assert rows["g9-ep01"]["deps_ready"] is True and rows["g-free"]["deps_ready"] is True
    assert rows["g6s-ep01"]["deps_ready"] is False and rows["g10-ep01"]["deps_ready"] is False
    html = (ROOT / "apps/web/static/preview_workflow.html").read_text(encoding="utf-8")
    assert "n.deps_ready??" in html
