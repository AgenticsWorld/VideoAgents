"""顶栏项目选择弹窗的两个接口:/projects/overview(备份折叠、类型、自动运行、运行数、最近更新)
与 /projects/disk(磁盘占用,带缓存)。"""
import asyncio
import json
import os
import time

import pytest


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    from services.runtime import core

    monkeypatch.setattr(core, "PROJECTS_DIR", tmp_path / "projects")
    core.PROJECTS_DIR.mkdir()
    monkeypatch.setattr(core, "STATE", {})
    monkeypatch.setattr(core, "RUNS", {})
    monkeypatch.setattr(core, "PROJECT_DISK_CACHE", {})
    return core


def _mk(core, name, files=None):
    d = core.PROJECTS_DIR / name
    d.mkdir(parents=True)
    for rel, data in (files or {}).items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_bytes(data if isinstance(data, bytes) else data.encode())
    return d


def _overview(core):
    return {p["name"]: p for p in asyncio.run(core.api_projects_overview())["projects"]}


def test_backup_base_rule():
    from services.runtime.core import _backup_base

    names = {"fengshen3", "dzg6", "alices"}
    assert _backup_base("fengshen3-20260918", names) == "fengshen3"
    assert _backup_base("dzg6-20260908-sd25", names) == "dzg6"        # 手工改过的后缀
    assert _backup_base("alices-20260925-1", names) == "alices"       # 同日重名 -1
    assert _backup_base("alices-pano", names) is None                 # 不是日期:另一个项目
    assert _backup_base("orphan-20260101", names) is None             # 源项目已不在:照常单列
    assert _backup_base("alices-20261301", names) is None             # 不是合法日期
    assert _backup_base("alices-2026092", names) is None
    assert _backup_base("fengshen3", names) is None


def test_overview_fields(runtime):
    core = runtime
    _mk(core, "fengshen3", {"settings.json": "{}"})
    _mk(core, "fengshen3-20260918")
    _mk(core, "fengshen3-20261003-unsensor")
    _mk(core, "leijun", {"settings.json": json.dumps({"project": {"type": "mashup"}})})
    _mk(core, "history", {"av/audio_map.json": "{}"})            # 存量项目按产物推断类型
    _mk(core, "orphan-20260101")
    (core.PROJECTS_DIR / ".copy-tmp-x").mkdir()                    # 复制中的临时目录不列
    core.STATE["watchdog"] = {"fengshen3": True, "leijun": False}
    future = time.time() + 3600
    core.RUNS.update({
        "a": {"project": "fengshen3", "status": "running", "created": 1, "started": 2},
        "b": {"project": "fengshen3", "status": "queued", "created": 3},
        "c": {"project": "fengshen3-20260918", "status": "running", "created": 4},
        "d": {"project": "leijun", "status": "done", "created": 5, "ended": future},
    })

    ov = _overview(core)
    assert sorted(ov) == ["fengshen3", "fengshen3-20260918", "fengshen3-20261003-unsensor",
                          "history", "leijun", "orphan-20260101"]
    assert ov["fengshen3"]["backup_of"] is None
    assert ov["fengshen3-20260918"]["backup_of"] == "fengshen3"
    assert ov["fengshen3-20261003-unsensor"]["backup_of"] == "fengshen3"
    assert ov["orphan-20260101"]["backup_of"] is None
    assert (ov["fengshen3"]["type"], ov["leijun"]["type"], ov["history"]["type"]) == (
        "film", "mashup", "audio-to-video")
    assert ov["fengshen3"]["watchdog"] is True and ov["leijun"]["watchdog"] is False
    assert (ov["fengshen3"]["running"], ov["fengshen3"]["queued"]) == (1, 1)
    assert ov["fengshen3-20260918"]["running"] == 1 and ov["leijun"]["running"] == 0
    assert ov["leijun"]["updated"] == future                       # 运行记录比目录新时取运行时间


def test_updated_sees_second_level_but_not_run_dirs(runtime):
    core = runtime
    d = _mk(core, "p", {"assets/canvas/a.png": b"x", "runs/abc/log.txt": b"x"})
    old = time.time() - 86400 * 10
    for f in [d / "assets/canvas/a.png", d / "runs/abc/log.txt", d / "runs/abc",
              d / "assets/canvas", d / "assets", d / "runs", d]:
        os.utime(f, (old, old))
    assert abs(_overview(core)["p"]["updated"] - old) < 1
    newer = old + 3600
    os.utime(d / "assets/canvas", (newer, newer))                  # 二级条目算
    assert abs(_overview(core)["p"]["updated"] - newer) < 1
    os.utime(d / "runs/abc", (newer + 60, newer + 60))             # runs/ 下逐个运行目录不看
    assert abs(_overview(core)["p"]["updated"] - newer) < 1


def test_disk_sizes_and_cache(runtime, tmp_path):
    core = runtime
    _mk(core, "a", {"x.bin": b"1" * 1000, "sub/y.bin": b"2" * 24})
    _mk(core, "b", {"z.bin": b"3" * 7})
    outside = tmp_path / "big.bin"
    outside.write_bytes(b"0" * 100000)
    os.symlink(outside, core.PROJECTS_DIR / "a" / "link.bin")      # 符号链接不跟进
    r = asyncio.run(core.api_projects_disk("a, b,nope,../a,a"))["sizes"]
    assert set(r) == {"a", "b"} and r["b"] == 7
    assert 1024 <= r["a"] < 2000
    (core.PROJECTS_DIR / "b" / "more.bin").write_bytes(b"4" * 50)
    assert asyncio.run(core.api_projects_disk("b"))["sizes"]["b"] == 7       # 10 分钟内回缓存
    core.PROJECT_DISK_CACHE["b"] = (time.time() - core.PROJECT_DISK_TTL - 1, 7)
    assert asyncio.run(core.api_projects_disk("b"))["sizes"]["b"] == 57      # 过期重扫
    assert asyncio.run(core.api_projects_disk(""))["sizes"] == {}
