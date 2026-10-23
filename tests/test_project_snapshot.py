"""项目快照(services/runtime/project_snapshot.py):派单起引擎前把项目克隆到数据目录之外,误删可找回。"""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from services.runtime import project_snapshot as ps

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def proj(tmp_path):
    p = tmp_path / "data" / "projects" / "jiangwu"
    (p / "bible").mkdir(parents=True)
    (p / "bible" / "style.json").write_text('{"a": 1}', encoding="utf-8")
    (p / "brief.md").write_text("brief", encoding="utf-8")
    return p


def _stamp(ts: float) -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(ts))


def test_take_survives_project_deletion(proj, tmp_path):
    store = tmp_path / "snap" / "jiangwu"
    res = ps.take(proj, store, interval=1800)
    assert res["status"] == "taken"
    assert res["method"] == ("clone" if sys.platform == "darwin" else "hardlink")
    snap = Path(res["path"])
    subprocess.run(["rm", "-rf", str(proj)], check=True)
    assert (snap / "bible" / "style.json").read_text(encoding="utf-8") == '{"a": 1}'
    assert not list(store.glob(".*.part"))


def test_fresh_within_interval_and_skip_missing(proj, tmp_path):
    store = tmp_path / "snap" / "jiangwu"
    now = time.time()
    first = ps.take(proj, store, interval=1800, now=now)
    assert ps.take(proj, store, interval=1800, now=now + 60) == {"status": "fresh", "path": first["path"]}
    assert ps.take(proj, store, interval=1800, now=now + 1900)["status"] == "taken"
    assert ps.take(tmp_path / "nope", store)["status"] == "skipped"


def test_prune_keeps_recent_and_daily(tmp_path):
    store = tmp_path / "s"
    now = time.mktime((2026, 10, 23, 12, 0, 0, 0, 0, -1))
    ages = [0, 600, 1200, 1800, 26 * 3600, 27 * 3600, 3 * 86400, 8 * 86400]   # 秒
    for a in ages:
        (store / _stamp(now - a)).mkdir(parents=True)
    removed = {p.name for p in ps.prune(store, keep=3, days=7, now=now)}
    left = {p.name for p in store.iterdir()}
    # 最近 3 份 + 前一天最新一份 + 3 天前那份;同日更早的、7 天以前的删掉
    assert left == {_stamp(now - a) for a in (0, 600, 1200, 26 * 3600, 3 * 86400)}
    assert removed == {_stamp(now - a) for a in (1800, 27 * 3600, 8 * 86400)}


def test_take_prunes_sibling_projects(proj, tmp_path):
    root = tmp_path / "snap"
    old = root / "deleted-project" / _stamp(time.time() - 9 * 86400)
    old.mkdir(parents=True)
    ps.take(proj, root / "jiangwu")
    assert not old.exists()


def test_store_dir_separates_data_dirs(tmp_path):
    a = ps.store_dir(tmp_path / "x" / "data", tmp_path / "root")
    b = ps.store_dir(tmp_path / "y" / "data", tmp_path / "root")
    assert a != b and a.parent == b.parent == tmp_path / "root"


def test_env_switches(monkeypatch, tmp_path):
    monkeypatch.setenv("VIDEOAGENTS_SNAPSHOT_DIR", str(tmp_path / "custom"))
    assert ps.snapshot_root() == tmp_path / "custom"
    monkeypatch.setenv("VIDEOAGENTS_SNAPSHOT", "0")
    assert not ps.enabled()
    monkeypatch.delenv("VIDEOAGENTS_SNAPSHOT")
    assert ps.enabled()


def test_restore_cli_never_overwrites(proj, tmp_path):
    snap = Path(ps.take(proj, tmp_path / "snap" / "jiangwu")["path"])
    cli = [sys.executable, "-m", "services.runtime.project_snapshot"]
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    r = subprocess.run([*cli, "restore", str(snap), str(proj)], cwd=ROOT, env=env, capture_output=True, text=True)
    assert r.returncode == 1 and "不覆盖" in r.stderr
    dest = tmp_path / "data" / "projects" / "jiangwu-restored"
    r = subprocess.run([*cli, "restore", str(snap), str(dest)], cwd=ROOT, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (dest / "brief.md").read_text(encoding="utf-8") == "brief"
    r = subprocess.run([*cli, "list", str(tmp_path / "snap")], cwd=ROOT, env=env, capture_output=True, text=True)
    assert r.returncode == 0
