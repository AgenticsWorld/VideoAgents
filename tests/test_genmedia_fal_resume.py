"""Fal 在途任务台账:任务建成即落盘、同一请求重跑续接不重提、reclaim 只查询下载。"""
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import modules.genmedia as g  # noqa: E402

CFG = {"api_key": "fal-key"}
ENDPOINT = "bytedance/seedance-2.0/text-to-video"
BODY = {"prompt": "a cat", "duration": "5"}


class FakeFal:
    """假的 Fal 队列:statuses 逐次弹出(字符串=状态,异常实例=抛出);记录提交次数。"""

    def __init__(self, monkeypatch, tmp_path, statuses=("IN_PROGRESS", "COMPLETED")):
        self.statuses = list(statuses)
        self.submits = []
        self.status_calls = 0
        self.result = {"video": {"url": "data:video/mp4;base64,QUJD"}}
        self.result_error = None
        monkeypatch.setattr(g, "PENDING_TASK_DIR", tmp_path / "pending")
        monkeypatch.setattr(g, "_post_json", self.post)
        monkeypatch.setattr(g, "_get_json", self.get)
        monkeypatch.setattr(g.time, "sleep", lambda s: None)
        monkeypatch.delenv("VIDEOAGENTS_AGENT", raising=False)

    def post(self, url, body, headers=None, **kw):
        rid = f"req-{len(self.submits) + 1}"
        self.submits.append((url, body))
        return {"request_id": rid, "status_url": f"https://q/{rid}/status", "response_url": f"https://q/{rid}"}

    def get(self, url, headers=None, **kw):
        assert headers == {"Authorization": "Key fal-key"}
        if url.endswith("/status"):
            self.status_calls += 1
            item = self.statuses.pop(0) if self.statuses else "COMPLETED"
            if isinstance(item, BaseException):
                raise item
            return item if isinstance(item, dict) else {"status": item}
        if self.result_error:
            raise self.result_error
        return dict(self.result, _from=url)


def _interrupt(fal, out):
    """模拟进程在轮询中途被掐:第一次查状态就抛 KeyboardInterrupt。"""
    fal.statuses = [KeyboardInterrupt()]
    with pytest.raises(KeyboardInterrupt):
        g._fal_submit_and_wait(CFG, ENDPOINT, dict(BODY), str(out))
    assert len(fal.submits) == 1 and g._pending_task_read(str(out))["request_id"] == "req-1"


def test_fingerprint_ignores_seed_and_url_signatures():
    base = {"prompt": "p", "seed": 1, "video_url": "https://b/k.mp4?X-Sig=a&t=1", "image_url": "data:image/png;base64,AAA"}
    same = {"prompt": "p", "seed": 999, "video_url": "https://b/k.mp4?X-Sig=b&t=2", "image_url": "data:image/png;base64,AAA"}
    fp = g._request_fingerprint("e", base)
    assert fp == g._request_fingerprint("e", same)
    assert fp != g._request_fingerprint("e", dict(base, prompt="q"))
    assert fp != g._request_fingerprint("e", dict(base, image_url="data:image/png;base64,BBB"))
    assert fp != g._request_fingerprint("e", dict(base, video_url="https://b/other.mp4?X-Sig=a"))
    assert fp != g._request_fingerprint("other", base)


def test_record_is_written_before_polling_and_cleared_after_save(monkeypatch, tmp_path):
    fal = FakeFal(monkeypatch, tmp_path)
    out = tmp_path / "grp001.mp4"
    seen = []
    real_get = fal.get

    def spy(url, headers=None, **kw):
        if url.endswith("/status"):
            seen.append(g._pending_task_read(str(out)))
        return real_get(url, headers, **kw)
    monkeypatch.setattr(g, "_get_json", spy)
    assert g._fal_submit_and_wait(CFG, ENDPOINT, dict(BODY), str(out)) == str(out)
    assert out.read_bytes() == b"ABC"
    assert seen[0]["request_id"] == "req-1" and seen[0]["kind"] == "视频" and seen[0]["endpoint"] == ENDPOINT
    assert g._pending_task_read(str(out)) is None
    assert fal.submits[0][0] == f"{g.FAL_QUEUE_BASE}/{ENDPOINT}"


def test_rerun_after_interrupt_resumes_without_resubmitting(monkeypatch, tmp_path):
    fal = FakeFal(monkeypatch, tmp_path)
    out = tmp_path / "grp001.mp4"
    _interrupt(fal, out)
    fal.statuses = ["COMPLETED"]
    saved = g._fal_submit_and_wait(CFG, ENDPOINT, dict(BODY, seed=42), str(out))
    assert saved == str(out) and len(fal.submits) == 1          # 没有第二次提交
    assert g._pending_task_read(str(out)) is None


def test_timeout_keeps_record_for_next_run(monkeypatch, tmp_path):
    fal = FakeFal(monkeypatch, tmp_path)
    monkeypatch.setattr(g, "VIDEO_TIMEOUT", 0)          # 一次都轮询不到就超时
    out = tmp_path / "grp001.mp4"
    with pytest.raises(RuntimeError, match="原命令原样重跑会续接"):
        g._fal_submit_and_wait(CFG, ENDPOINT, dict(BODY), str(out))
    assert len(fal.submits) == 1 and g._pending_task_read(str(out))["request_id"] == "req-1"


def test_changed_request_submits_fresh(monkeypatch, tmp_path):
    fal = FakeFal(monkeypatch, tmp_path)
    out = tmp_path / "grp001.mp4"
    _interrupt(fal, out)
    fal.statuses = ["COMPLETED"]
    g._fal_submit_and_wait(CFG, ENDPOINT, dict(BODY, prompt="a dog"), str(out))
    assert len(fal.submits) == 2 and fal.submits[1][1]["prompt"] == "a dog"


def test_gone_task_falls_back_to_fresh_submit(monkeypatch, tmp_path):
    fal = FakeFal(monkeypatch, tmp_path)
    out = tmp_path / "grp001.mp4"
    _interrupt(fal, out)
    fal.statuses = [g._HTTPStatusError(404, "https://q/req-1/status", "not found"), "COMPLETED"]
    assert g._fal_submit_and_wait(CFG, ENDPOINT, dict(BODY), str(out)) == str(out)
    assert len(fal.submits) == 2
    assert g._pending_task_read(str(out)) is None


def test_stale_unfinished_task_is_abandoned_but_finished_one_is_used(monkeypatch, tmp_path):
    fal = FakeFal(monkeypatch, tmp_path)
    out = tmp_path / "grp001.mp4"
    _interrupt(fal, out)
    rec = g._pending_task_read(str(out))
    rec["submitted_ts"] = int(time.time()) - g.PENDING_TASK_STALE_S - 60
    g._pending_task_write(str(out), rec)
    fal.statuses = ["COMPLETED"]                      # 旧任务其实早就完成:直接取回,不重提
    g._fal_submit_and_wait(CFG, ENDPOINT, dict(BODY), str(out))
    assert len(fal.submits) == 1

    out2 = tmp_path / "grp002.mp4"
    fal2 = FakeFal(monkeypatch, tmp_path / "b")
    _interrupt(fal2, out2)
    rec = g._pending_task_read(str(out2))
    rec["submitted_ts"] = int(time.time()) - g.PENDING_TASK_STALE_S - 60
    g._pending_task_write(str(out2), rec)
    fal2.statuses = ["IN_PROGRESS", "COMPLETED"]      # 过久仍未完成:放弃旧任务,重新提交
    g._fal_submit_and_wait(CFG, ENDPOINT, dict(BODY), str(out2))
    assert len(fal2.submits) == 2


def test_failed_task_clears_record(monkeypatch, tmp_path):
    fal = FakeFal(monkeypatch, tmp_path, statuses=[{"status": "COMPLETED", "error": "boom", "error_type": "x"}])
    out = tmp_path / "grp001.mp4"
    with pytest.raises(RuntimeError, match="任务失败"):
        g._fal_submit_and_wait(CFG, ENDPOINT, dict(BODY), str(out))
    assert g._pending_task_read(str(out)) is None


def test_transient_poll_errors_keep_waiting(monkeypatch, tmp_path):
    fal = FakeFal(monkeypatch, tmp_path, statuses=[g._TransportError("u", TimeoutError("t")),
                                                   g._HTTPStatusError(404, "u", "flaky"), "COMPLETED"])
    out = tmp_path / "grp001.mp4"
    assert g._fal_submit_and_wait(CFG, ENDPOINT, dict(BODY), str(out)) == str(out)   # 新任务上的 404 不当作失效
    assert len(fal.submits) == 1 and fal.status_calls == 3


def test_image_and_no_output_calls(monkeypatch, tmp_path):
    fal = FakeFal(monkeypatch, tmp_path)
    fal.result = {"images": [{"url": "data:image/png;base64,QUJD"}]}
    monkeypatch.setattr(g, "_fal_image_body", lambda *a, **k: ("fal-ai/x", {"prompt": "p"}))
    out = tmp_path / "a.png"
    assert g._image_fal(CFG, "p", "", [], 1024, 1024, 1, str(out)) == b"ABC"
    assert g._pending_task_read(str(out)) is None
    g._fal_queue_run(CFG, "fal-ai/x", {"prompt": "p"}, 10, "label")     # 不给 output:不落台账
    assert not (tmp_path / "pending").exists() or not list((tmp_path / "pending").iterdir())


def test_reclaim_reads_record_and_never_submits(monkeypatch, tmp_path):
    fal = FakeFal(monkeypatch, tmp_path)
    cfgp = tmp_path / "genconfig.json"
    cfgp.write_text(json.dumps({"image": {"fal": {"api_key": "fal-key"}}}), encoding="utf-8")
    monkeypatch.setattr(g, "CONFIG_PATH", cfgp)
    monkeypatch.setattr(g, "_post_json", lambda *a, **k: pytest.fail("reclaim 不得提交"))
    out = tmp_path / "grp001.mp4"
    with pytest.raises(RuntimeError, match="没有未完成的 Fal 任务"):
        g.reclaim_video("", str(out))
    rec = {"provider": "fal", "kind": "视频", "request_id": "req-9", "status_url": "https://q/req-9/status",
           "response_url": "https://q/req-9", "submitted_ts": int(time.time())}
    g._pending_task_write(str(out), rec)
    assert g.reclaim_video("", str(out)) == str(out) and out.read_bytes() == b"ABC"
    assert g._pending_task_read(str(out)) is None

    g._pending_task_write(str(out), rec)
    fal.statuses = [g._HTTPStatusError(404, "u", "gone")]
    with pytest.raises(RuntimeError, match="已无法恢复"):
        g.reclaim_video("req-9", str(out))

    g._pending_task_write(str(out), dict(rec, kind="图像"))
    with pytest.raises(RuntimeError, match="reclaim 只取视频"):
        g.reclaim_video("", str(out))


def test_reclaim_with_other_task_id_keeps_existing_routes(monkeypatch, tmp_path):
    FakeFal(monkeypatch, tmp_path)
    out = tmp_path / "grp001.mp4"
    g._pending_task_write(str(out), {"provider": "fal", "kind": "视频", "request_id": "req-9",
                                     "status_url": "s", "response_url": "r"})
    monkeypatch.setattr(g, "_ark_reclaim_config", lambda: {"provider": "volcengine"})
    monkeypatch.setattr(g, "_ark_wait_and_download", lambda cfg, tid, output, **k: f"ark:{tid}")
    assert g.reclaim_video("cgt-123", str(out)) == "ark:cgt-123"
