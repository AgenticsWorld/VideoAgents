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
