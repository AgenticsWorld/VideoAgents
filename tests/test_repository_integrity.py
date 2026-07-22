import ast
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "agents"
GENERATED_PARTS = {
    ".git", ".venv", ".runtime", ".runtime.staging", ".runtime-packages", ".uv-cache",
    "node_modules", "dist", "release",
}


def is_generated(path: Path) -> bool:
    return bool(GENERATED_PARTS.intersection(path.parts))


def test_python_sources_parse():
    files = [
        path
        for path in ROOT.rglob("*.py")
        if not is_generated(path)
    ]
    assert files
    for path in files:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_json_and_yaml_files_parse():
    for path in ROOT.rglob("*.json"):
        if not is_generated(path):
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
    known_core = {
        str(path.parent.relative_to(AGENTS))
        for path in AGENTS.glob("*/*/SOUL.md")
    }
    seen_plugin_ids: set[str] = set()
    manifests = sorted((ROOT / "plugins").glob("*/plugin.json"))
    assert {path.parent.name for path in manifests} >= {
        "derivative-fiction", "fusion-fiction",
    }
    for manifest_path in manifests:
        plugin_dir = manifest_path.parent
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["name"] == plugin_dir.name
        ids = [item["id"] if isinstance(item, dict) else item for item in manifest["agents"]]
        assert ids
        for agent_id in ids:
            assert (plugin_dir / "agents" / agent_id / "SOUL.md").is_file()
            assert agent_id not in known_core
            assert agent_id not in seen_plugin_ids
            seen_plugin_ids.add(agent_id)
        for workflow_path in manifest.get("workflows", []):
            workflow = yaml.safe_load((plugin_dir / workflow_path).read_text(encoding="utf-8"))
            referenced: set[str] = set()

            def collect(value):
                if isinstance(value, dict):
                    for key, child in value.items():
                        if key == "agent" and isinstance(child, str):
                            referenced.add(child)
                        if key == "qa" and isinstance(child, list):
                            referenced.update(item for item in child if isinstance(item, str) and "/" in item)
                        collect(child)
                elif isinstance(value, list):
                    for child in value:
                        collect(child)

            collect(workflow)
            assert not referenced - known_core - set(ids)


def test_release_tree_has_no_private_project_artifacts():
    assert not (ROOT / "runs").exists()
    assert not (ROOT / "qa" / "evidence").exists()
    forbidden = (
        "/Users/" + "ericfish",
        "ericfish" + "@gmail.com",
        "data/projects/" + "thedoor",
    )
    for path in ROOT.rglob("*"):
        if not path.is_file() or is_generated(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        assert not any(value in text for value in forbidden), path
