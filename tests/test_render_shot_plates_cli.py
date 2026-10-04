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


def test_manual_plate_needed_exit_code_5_and_grid_layout_passthrough(tmp_path, monkeypatch, capsys):
    """九宫格手动补图(2026-10-04):有镜待用户手工截取 → 退出码 5 并打印 [manual_plate_needed];--grid-layout 透传,缺省 center。"""
    seen = {}
    monkeypatch.setattr(rsp, "reexec_with_host_python", lambda: None)
    monkeypatch.setattr(rsp, "spatial_blocking_enabled", lambda base: True)
    monkeypatch.setattr(rsp, "read", lambda p, d=None: {"groups": [{"group_id": "grp001", "cameras": [{"shot_id": "sh001"}]}]})

    def fake_run(base, ep, only, **kw):
        seen.update(kw)
        return {"errors": [], "new": 0, "manual_needed": ["sh001:start"]}
    monkeypatch.setattr(rsp, "run_episode", fake_run)
    monkeypatch.setattr(rsp, "sync_episode", lambda *a, **k: {"updated_prompts": [], "errors": [], "warnings": []})
    monkeypatch.setattr(sys, "argv", ["render_shot_plates.py", "--out-root", str(tmp_path), "--ep", "ep01", "sh001", "--allow-unexported"])
    assert rsp.main() == 5
    assert "[manual_plate_needed]" in capsys.readouterr().out and seen["grid_layout"] == "center"
    monkeypatch.setattr(sys, "argv", ["render_shot_plates.py", "--out-root", str(tmp_path), "--ep", "ep01", "sh001", "--allow-unexported",
                                      "--dry-run", "--grid-layout", "views"])
    assert rsp.main() == 0 and seen["grid_layout"] == "views"      # dry-run 不报待补

