# -*- coding: utf-8 -*-
"""#73:render_shot_plates CLI 补图默认开(与 run_episode / docs 一致),--no-grid-fallback 关闭。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "code"))

import render_shot_plates as rsp  # noqa: E402


@pytest.mark.parametrize("extra,expected", [([], True), (["--no-grid-fallback"], False), (["--grid-fallback"], True)])
def test_grid_fallback_default_on(tmp_path, monkeypatch, extra, expected):
    seen = {}
    monkeypatch.setattr(rsp, "reexec_with_host_python", lambda: None)
    monkeypatch.setattr(rsp, "spatial_blocking_enabled", lambda base: True)
    monkeypatch.setattr(rsp, "read", lambda p, d=None: {"groups": [{"group_id": "grp001", "cameras": [{"shot_id": "sh001"}]}]})

    def fake_run(base, ep, only, **kw):
        seen.update(kw)
        return {"errors": [], "new": 0}
    monkeypatch.setattr(rsp, "run_episode", fake_run)
    monkeypatch.setattr(sys, "argv", ["render_shot_plates.py", "--out-root", str(tmp_path), "--ep", "ep01", "sh001",
                                      "--force", "--dry-run", "--allow-unexported", *extra])
    assert rsp.main() == 0
    assert seen["grid_fallback"] is expected and seen["force"] is True
