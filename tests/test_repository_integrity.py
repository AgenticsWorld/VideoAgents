import ast
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "agents"


def test_python_sources_parse():
    files = [
        path
        for path in ROOT.rglob("*.py")
        if ".git" not in path.parts and ".venv" not in path.parts
    ]
    assert files
    for path in files:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_json_and_yaml_files_parse():
    for path in ROOT.rglob("*.json"):
        if ".git" not in path.parts:
            json.loads(path.read_text(encoding="utf-8"))
    for path in [AGENTS / "workflow.yaml", ROOT / "CITATION.cff"]:
        assert yaml.safe_load(path.read_text(encoding="utf-8"))


def test_workflow_references_existing_agents():
    workflow = yaml.safe_load((AGENTS / "workflow.yaml").read_text(encoding="utf-8"))
    known = {
        str(path.parent.relative_to(AGENTS))
        for path in AGENTS.glob("*/*/SOUL.md")
    }
    referenced = set()

    def collect(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "agent" and isinstance(child, str):
                    referenced.add(child)
                if key == "qa" and isinstance(child, list):
                    referenced.update(
                        item for item in child if isinstance(item, str) and "/" in item
                    )
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(workflow)
    assert len(known) == 83
    assert referenced == known


def test_plugins_are_well_formed():
    """插件完整性:manifest 合法、SOUL.md 齐备、id 不与内置/其他插件冲突、
    workflow 引用的 agent 都存在(内置 ∪ 本插件)。约定见 agents/WORKFLOW.md §10。"""
    known_core = {
        str(path.parent.relative_to(AGENTS))
        for path in AGENTS.glob("*/*/SOUL.md")
    }
    seen_plugin_ids: set[str] = set()
    manifests = sorted((ROOT / "plugins").glob("*/plugin.json"))
    assert manifests, "至少应有 derivative-fiction 官方插件"
    for manifest_path in manifests:
        pdir = manifest_path.parent
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert m["name"] == pdir.name
        assert m["description"]
        ids = [a["id"] if isinstance(a, dict) else a for a in m["agents"]]
        assert ids
        for aid in ids:
            assert (pdir / "agents" / aid / "SOUL.md").is_file(), f"{pdir.name}: 缺 {aid}/SOUL.md"
            assert aid not in known_core, f"{pdir.name}: {aid} 与内置团队冲突"
            assert aid not in seen_plugin_ids, f"{pdir.name}: {aid} 与其他插件冲突"
            seen_plugin_ids.add(aid)
        for wf in m.get("workflows", []):
            doc = yaml.safe_load((pdir / wf).read_text(encoding="utf-8"))
            assert doc
            referenced = set()

            def collect(value):
                if isinstance(value, dict):
                    for key, child in value.items():
                        if key == "agent" and isinstance(child, str):
                            referenced.add(child)
                        if key == "qa" and isinstance(child, list):
                            referenced.update(
                                item for item in child
                                if isinstance(item, str) and "/" in item
                            )
                        collect(child)
                elif isinstance(value, list):
                    for child in value:
                        collect(child)

            collect(doc)
            unknown = referenced - known_core - set(ids)
            assert not unknown, f"{pdir.name}/{wf}: 引用了不存在的 agent {sorted(unknown)}"


def test_release_tree_has_no_private_project_artifacts():
    assert not (ROOT / "runs").exists()
    assert not (ROOT / "qa" / "evidence").exists()
    forbidden = (
        "/Users/" + "ericfish",
        "ericfish" + "@gmail.com",
        "data/projects/" + "thedoor",
    )
    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        assert not any(value in text for value in forbidden), path
