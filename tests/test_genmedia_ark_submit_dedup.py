"""方舟视频提交读超时:_request 包装的 _TransportError 须走查重分支,不得直接失败(issue #91)。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import modules.genmedia as g  # noqa: E402


def _patch(monkeypatch, found):
    def boom(url, body, headers, **kw):
        raise g._TransportError(url, TimeoutError("The read operation timed out"))
    calls = []
    monkeypatch.setattr(g, "_ark_video_body", lambda *a, **k: {})
    monkeypatch.setattr(g, "_draft_mode_active", lambda *a: False)
    monkeypatch.setattr(g, "_post_json", boom)
    monkeypatch.setattr(g, "_find_recent_ark_task", lambda *a, **k: calls.append(1) or found)
    monkeypatch.setattr(g, "_ark_wait_and_download", lambda cfg, tid, *a, **k: f"ok:{tid}")
    return calls


def test_transport_error_hits_dedup_and_reclaims(monkeypatch):
    calls = _patch(monkeypatch, "cgt-123")
    out = g._video_ark({"api_key": "k", "model": "m"}, "p", None, None, 5, "720p", "16:9", None, "o.mp4")
    assert calls and out == "ok:cgt-123"


def test_transport_error_without_task_fails_after_dedup(monkeypatch):
    calls = _patch(monkeypatch, None)
    with pytest.raises(RuntimeError, match="任务列表未见新任务"):
        g._video_ark({"api_key": "k", "model": "m"}, "p", None, None, 5, "720p", "16:9", None, "o.mp4")
    assert calls
