from argparse import Namespace

import pygit2

from modules import vc


def test_business_artifact_versions_use_pygit2(tmp_path):
    artifact = tmp_path / "artifact.txt"
    artifact.write_text("v1")
    vc._init_paths(str(tmp_path / ".version"))
    vc.cmd_init(Namespace())

    def register(task: str):
        vc.cmd_register(Namespace(
            artifacts=["artifact.txt"], task_id=task, attempt=1,
            reason=task, tag=None,
        ))

    register("first")
    artifact.write_text("v2")
    register("second")

    repo = pygit2.Repository(str(tmp_path / ".version" / "repo.git"))
    assert len(list(repo.walk(repo.head.target))) == 2
    assert vc.load_manifest()["artifacts"]["artifact.txt"]["current"] == 2

    vc.cmd_rollback(Namespace(
        artifact="artifact.txt", version="v1", task_id="rollback",
        attempt=1, reason="restore",
    ))
    assert artifact.read_text() == "v1"
    assert vc.load_manifest()["artifacts"]["artifact.txt"]["current"] == 3
