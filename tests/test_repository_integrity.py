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
