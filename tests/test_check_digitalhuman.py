"""Completed Agentics clips must pass the same ledger checks as other channels."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
import check_digitalhuman as checker


@pytest.fixture
def clip_project(tmp_path, monkeypatch):
    master = tmp_path / "master.mp3"
    master.write_bytes(b"master")
    (tmp_path / "utt0001.mp4").write_bytes(b"clip")
    track = tmp_path / "dialogue_track.json"
    track.write_text(json.dumps({
        "schema_version": 1, "master_audio": str(master),
        "master_sha256": checker.file_sha256(str(master)),
        "duration_s": 2.0, "alignment_mode": "explicit_timestamps",
        "cast": {"host": {}},
        "utterances": [{"id": "utt0001", "speaker": "host", "text": "Hello",
                        "start": 0, "end": 2, "duration_s": 2,
                        "render_mode": "avatar"}],
    }), encoding="utf-8")
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    monkeypatch.setattr(checker, "probe_duration", lambda path: 2.0)

    def check(**overrides):
        job = {"utterance_id": "utt0001", "provider": "agentics",
               "status": "completed", "task_id": "existing-task", **overrides}
        (jobs / "utt0001.json").write_text(json.dumps(job), encoding="utf-8")
        return checker.check(str(track), str(tmp_path), None, "clips")

    return check


@pytest.mark.parametrize("provider", ["agentics", "heygen", "klingai", "runninghub", "comfyui"])
def test_completed_avatar_channels_pass(clip_project, provider):
    result = clip_project(provider=provider)
    assert result["ok"], result["errors"]


@pytest.mark.parametrize("overrides,error", [
    ({"provider": "unknown"}, "渠道非法"),
    ({"provider": None}, "渠道非法"),
    ({"provider": "static"}, "口型片段不能用静帧"),
    ({"status": "submitted"}, "渠道任务尚未完成"),
    ({"task_id": ""}, "缺少可恢复 task_id"),
    ({"utterance_id": "utt0002"}, "任务台账与片段不匹配"),
])
def test_invalid_ledgers_still_block(clip_project, overrides, error):
    result = clip_project(**overrides)
    assert not result["ok"]
    assert any(error in message for message in result["errors"])


def test_agentics_short_clip_still_blocks(clip_project, monkeypatch):
    monkeypatch.setattr(checker, "probe_duration",
                        lambda path: 1.0 if path.endswith(".mp4") else 2.0)
    result = clip_project()
    assert not result["ok"]
    assert any("片段过短" in message for message in result["errors"])
