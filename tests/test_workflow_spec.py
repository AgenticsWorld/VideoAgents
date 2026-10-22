"""机器可读规约(agents/workflow.yaml 及各项目类型 / 插件 DAG)与 WORKFLOW.md / SOUL 的一致性。"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _dag_files():
    files = [ROOT / "agents" / "workflow.yaml"]
    files += sorted((ROOT / "agents" / "workflows").glob("*.yaml"))
    files += sorted((ROOT / "plugins").glob("*/workflows/*.yaml"))
    return files


def _tasks(dag):
    for ph in dag.get("phases") or []:
        for task in ph.get("tasks") or []:
            yield task


def _task(dag, tid):
    for task in _tasks(dag):
        if task.get("id") == tid:
            return task
    raise AssertionError(f"workflow.yaml 缺节点 {tid}")


# #144:2026-07-20 起组级台词干声轨 voice/<ep>/lines/ 已废止(§8A),任何 DAG 都不得再要求它
_LINES_OUT = re.compile(r"audio/voice/[^\"'\s]*/lines(/|$)")


def test_no_dag_requires_episode_lines_dry_voice():
    yaml = pytest.importorskip("yaml")
    for f in _dag_files():
        text = f.read_text(encoding="utf-8")
        dag = yaml.safe_load(text)
        for task in _tasks(dag):
            for out in task.get("outputs") or []:
                assert not _LINES_OUT.search(str(out)), (f.name, task.get("id"), out)
            auto = (task.get("validation") or {}).get("auto") or []
            for name in auto:
                assert "group_lines" not in str(name), (f.name, task.get("id"), name)


def test_p8_voiceprints_matches_workflow_md_and_soul():
    yaml = pytest.importorskip("yaml")
    dag = yaml.safe_load((ROOT / "agents" / "workflow.yaml").read_text(encoding="utf-8"))
    task = _task(dag, "p8-voiceprints")
    assert task["agent"] == "09-audio/voice-generation"
    assert task["outputs"] == ["assets/audio/voice/refs/manifest.json", "assets/audio/voice/casting.json"]
    auto = task["validation"]["auto"]
    assert auto == ["voiceprint_coverage_main_100", "sample_duration_3_5s", "casting_bound", "voice_collision"]

    md = (ROOT / "agents" / "WORKFLOW.md").read_text(encoding="utf-8")
    rows = [ln for ln in md.splitlines() if ln.startswith("| voice-generation(") and "p8-voiceprints" in ln]
    assert len(rows) == 1, rows
    for name in auto:
        assert name in rows[0], name
    assert "只放集级产物 lines/" not in md

    soul = (ROOT / "agents" / "09-audio" / "voice-generation" / "SOUL.md").read_text(encoding="utf-8")
    accept = [ln for ln in soul.splitlines() if ln.startswith("- 验收方:机检")]
    assert len(accept) == 1, accept
    for name in auto:
        assert name in accept[0], name
