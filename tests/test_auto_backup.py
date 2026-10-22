"""版本管理页「自动备份」:签字后把项目目录打成 zip 放进用户指定目录(modules/project_backup.py + core 钩子)。"""
import asyncio
import threading
import time
import zipfile

import pytest

from modules import project_backup


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    from services.runtime import core

    monkeypatch.setattr(core, "PROJECTS_DIR", tmp_path / "projects")
    core.PROJECTS_DIR.mkdir()
    monkeypatch.setattr(core, "STATE", {})
    monkeypatch.setattr(core, "save_state", lambda state: None)
    monkeypatch.setattr(core, "ui_lang_code", lambda: "zh")
    monkeypatch.setattr(core, "CONFIRMS", {})
    monkeypatch.setattr(core, "AUTO_BACKUP_JOBS", {})
    notes = []
    monkeypatch.setattr(core, "notify_user", notes.append)
    core._notes = notes
    return core


def _run(coro):
    """独立事件循环跑协程,不动主线程的当前循环(asyncio.run 会把它清成 None,后续用 get_event_loop 的测试会挂)。"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _project(core, name="demo"):
    base = core.PROJECTS_DIR / name
    (base / "refs" / "text").mkdir(parents=True)
    (base / "assets" / "clips").mkdir(parents=True)
    (base / "settings.json").write_text('{"a": 1}')
    (base / "refs" / "text" / "story.txt").write_text("故事" * 500)
    (base / "assets" / "clips" / "grp010.mp4").write_bytes(b"\x00" * 4096)
    return base


def _wait_idle(core, project, timeout=10.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        job = core.AUTO_BACKUP_JOBS.get(project)
        if job and not job["running"]:
            return job
        time.sleep(0.02)
    raise AssertionError("auto backup did not finish")


def _sign(core, project, answer="签字", **extra):
    cid = f"c{len(core.CONFIRMS)}"
    core.CONFIRMS[cid] = {"id": cid, "question": "签字?", "options": ["签字", "暂缓"], "default": "签字",
                          "timeout": None, "kind": "sign", "parent": None, "created": time.time(),
                          "answer": None, "project": project, **extra}

    async def go():
        res = await core.api_confirm_answer(cid, {"answer": answer})
        await asyncio.sleep(0)          # 备份排在事件循环下一轮
        return res
    return _run(go())


def test_zip_project_layout_compression_and_name(tmp_path):
    src = tmp_path / "projects" / "demo"
    (src / "assets").mkdir(parents=True)
    (src / "brief.md").write_text("# b" * 200)
    (src / "assets" / "a.MP4").write_bytes(b"\x01" * 1000)
    out = tmp_path / "bk"
    r = project_backup.zip_project(src, out, "demo", now=time.mktime((2026, 10, 22, 15, 30, 0, 0, 0, -1)))
    assert r["file"] == "demo-20261022-153000.zip" and r["files"] == 2 and r["skipped"] == 0
    with zipfile.ZipFile(out / r["file"]) as zf:
        info = {i.filename: i for i in zf.infolist()}
        assert set(info) == {"demo/brief.md", "demo/assets/a.MP4"}
        assert info["demo/assets/a.MP4"].compress_type == zipfile.ZIP_STORED
        assert info["demo/brief.md"].compress_type == zipfile.ZIP_DEFLATED
        assert zf.read("demo/brief.md") == b"# b" * 200
    assert not list(out.glob(".*.part"))
    # 同一秒再打一份:追加 -2,不覆盖
    r2 = project_backup.zip_project(src, out, "demo", now=time.mktime((2026, 10, 22, 15, 30, 0, 0, 0, -1)))
    assert r2["file"] == "demo-20261022-153000-2.zip"


def test_zip_project_skips_backup_dir_inside_project_and_lists_by_exact_name(tmp_path):
    src = tmp_path / "demo"
    src.mkdir()
    (src / "x.txt").write_text("x")
    inner = src / "backups"
    r1 = project_backup.zip_project(src, inner, "demo")
    r2 = project_backup.zip_project(src, inner, "demo")     # 第二份不能把第一份打进去
    with zipfile.ZipFile(inner / r2["file"]) as zf:
        assert zf.namelist() == ["demo/x.txt"]
    (inner / "demo-2-20261022-101010.zip").write_bytes(b"")  # 别的项目(demo-2)的备份
    (inner / "demo-notes.zip").write_bytes(b"")
    names = {row["file"] for row in project_backup.list_backups(inner, "demo")}
    assert names == {r1["file"], r2["file"]}
    assert project_backup.list_backups(tmp_path / "missing", "demo") == []


def test_settings_validation(runtime, tmp_path):
    with pytest.raises(runtime.ServiceError) as e:
        _run(runtime.api_auto_backup_set({"enabled": True, "dir": ""}))
    assert e.value.status_code == 400 and "备份目录" in e.value.detail
    with pytest.raises(runtime.ServiceError) as e:
        _run(runtime.api_auto_backup_set({"enabled": True, "dir": "relative/bk"}))
    assert "绝对路径" in e.value.detail
    with pytest.raises(runtime.ServiceError) as e:
        _run(runtime.api_auto_backup_set({"enabled": True, "dir": str(runtime.PROJECTS_DIR / "bk")}))
    assert "项目数据目录" in e.value.detail
    assert runtime.auto_backup_settings() == {"enabled": False, "dir": ""}      # 失败不落盘
    runtime.ui_lang_code = lambda: "en"                                         # 非中文界面出英文
    with pytest.raises(runtime.ServiceError) as e:
        _run(runtime.api_auto_backup_set({"enabled": True}))
    assert e.value.detail == "Enter a backup folder before turning on auto backup"
    runtime.ui_lang_code = lambda: "zh"
    target = tmp_path / "deep" / "bk"
    r = _run(runtime.api_auto_backup_set({"enabled": True, "dir": str(target)}))
    assert r["enabled"] is True and r["dir"] == str(target.resolve()) and target.is_dir()
    assert not list(target.iterdir())                                            # 试写文件已删
    assert runtime.STATE["auto_backup"] == {"enabled": True, "dir": str(target.resolve())}
    r = _run(runtime.api_auto_backup_set({"enabled": False}))
    assert r == {"enabled": False, "dir": str(target.resolve())}                 # 关掉保留目录


def test_sign_off_creates_backup_and_hold_does_not(runtime, tmp_path):
    _project(runtime)
    bk = tmp_path / "bk"
    _run(runtime.api_auto_backup_set({"enabled": True, "dir": str(bk)}))
    _sign(runtime, "demo", answer="暂缓")
    assert "demo" not in runtime.AUTO_BACKUP_JOBS and not list(bk.iterdir())
    _sign(runtime, "demo", answer="Sign off")                    # 英文按钮文字同样归一成「签字」
    job = _wait_idle(runtime, "demo")
    assert job["last"]["ok"] is True and job["last"]["files"] == 3
    zips = list(bk.glob("demo-*.zip"))
    assert len(zips) == 1 and zips[0].name == job["last"]["file"]
    with zipfile.ZipFile(zips[0]) as zf:
        assert sorted(zf.namelist()) == ["demo/assets/clips/grp010.mp4", "demo/refs/text/story.txt",
                                         "demo/settings.json"]
    st = _run(runtime.api_auto_backup_get("demo"))
    assert st["count"] == 1 and st["recent"][0]["file"] == zips[0].name and st["running"] is False


def test_backup_round_trips_through_project_import(runtime, tmp_path):
    base = _project(runtime)
    r = project_backup.zip_project(base, tmp_path / "bk", "demo")
    data = (tmp_path / "bk" / r["file"]).read_bytes()

    async def chunks():
        yield data
    res = _run(runtime.api_projects_import(chunks(), r["file"], ""))
    assert res["name"] == "demo-2" and res["renamed_from"] == "demo"
    restored = runtime.PROJECTS_DIR / "demo-2"
    assert (restored / "refs" / "text" / "story.txt").read_text() == (base / "refs" / "text" / "story.txt").read_text()
    assert (restored / "assets" / "clips" / "grp010.mp4").read_bytes() == b"\x00" * 4096


def test_off_or_non_sign_does_nothing(runtime, tmp_path):
    _project(runtime)
    _sign(runtime, "demo")                                       # 默认关
    assert runtime.AUTO_BACKUP_JOBS == {}
    _run(runtime.api_auto_backup_set({"enabled": True, "dir": str(tmp_path / "bk")}))
    cid = "rerun1"
    runtime.CONFIRMS[cid] = {"id": cid, "question": "重跑?", "options": ["重跑", "跳过"], "default": "重跑",
                             "timeout": 60, "kind": "confirm", "parent": None, "created": time.time(),
                             "answer": None, "project": "demo"}
    _run(runtime.api_confirm_answer(cid, {"answer": "重跑"}))
    assert runtime.AUTO_BACKUP_JOBS == {}


def test_gate_sign_off_backs_up_after_continuation(runtime, tmp_path, monkeypatch):
    _project(runtime)
    _run(runtime.api_auto_backup_set({"enabled": True, "dir": str(tmp_path / "bk")}))
    calls = []

    async def fake_continue(c):
        calls.append(c["id"])
        return "run-1"
    monkeypatch.setattr(runtime, "_continue_signed_gate", fake_continue)
    monkeypatch.setattr(runtime, "_gate_display_by_id", lambda proj, gid, lang=None: "H3S 故事板确认 · ep01")
    _sign(runtime, "demo", gate_id="g6s-ep01", checkpoint="H3S")
    job = _wait_idle(runtime, "demo")
    assert calls and job["last"]["ok"] and job["last"]["trigger"] == "H3S 故事板确认 · ep01"


def test_sign_during_backup_queues_one_more_and_failure_notifies(runtime, tmp_path, monkeypatch):
    _project(runtime)
    _run(runtime.api_auto_backup_set({"enabled": True, "dir": str(tmp_path / "bk")}))
    gate, n = threading.Event(), []
    real = project_backup.zip_project

    def slow(src, dest, project, now=None):
        n.append(1)
        if len(n) == 1:
            assert gate.wait(5)
        return real(src, dest, project, now)
    monkeypatch.setattr(project_backup, "zip_project", slow)
    assert runtime.auto_backup_trigger("demo", "a")
    assert runtime.auto_backup_trigger("demo", "b")        # 打包中:记一笔待补
    assert runtime.auto_backup_trigger("demo", "c")        # 多次合并成一份
    with pytest.raises(runtime.ServiceError) as e:         # 打包中不能删项目
        _run(runtime.api_projects_delete({"project": "demo", "confirm": "demo"}))
    assert e.value.status_code == 409
    gate.set()
    job = _wait_idle(runtime, "demo")
    assert len(n) == 2 and job["last"]["ok"] and job["last"]["trigger"] == "c"

    def boom(src, dest, project, now=None):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(project_backup, "zip_project", boom)
    runtime.auto_backup_trigger("demo", "d")
    job = _wait_idle(runtime, "demo")
    assert job["last"]["ok"] is False and "No space" in job["last"]["error"]
    assert runtime._notes and "demo" in runtime._notes[-1] and "自动备份失败" in runtime._notes[-1]


def test_pick_dir_api_returns_path_or_cancelled(runtime, tmp_path, monkeypatch):
    seen = []

    def fake(start, title):
        seen.append((start, title))
        return str(tmp_path / "picked") if len(seen) == 1 else None
    monkeypatch.setattr(runtime, "_native_pick_dir", fake)
    assert _run(runtime.api_auto_backup_pick_dir({"dir": str(tmp_path)})) == {"path": str(tmp_path / "picked")}
    assert seen[0] == (tmp_path, "选择备份目录")
    assert _run(runtime.api_auto_backup_pick_dir({})) == {"cancelled": True}
    assert seen[1][0] == runtime.Path.home()                  # 没填目录从主目录开始
    assert runtime.auto_backup_settings() == {"enabled": False, "dir": ""}   # 只选不存


def test_native_pick_dir_macos_and_unavailable(runtime, tmp_path, monkeypatch):
    import subprocess
    calls = []

    class R:
        def __init__(self, rc, out="", err=""):
            self.returncode, self.stdout, self.stderr = rc, out, err
    replies = [R(0, str(tmp_path) + "/\n"), R(1, "", "execution error: User canceled. (-128)")]

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return replies.pop(0)
    monkeypatch.setattr(runtime.sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert runtime._native_pick_dir(tmp_path, "选择备份目录") == str(tmp_path)   # 去掉结尾 /
    assert calls[0][0] == "osascript" and "choose folder" in calls[0][2] and str(tmp_path) in calls[0][2]
    assert runtime._native_pick_dir(tmp_path / "missing", "t") is None           # 取消
    assert str(runtime.Path.home()) in calls[1][2]                               # 起始目录不存在退回主目录
    monkeypatch.setattr(runtime.sys, "platform", "linux")
    monkeypatch.setattr(runtime.shutil, "which", lambda name: None)
    if runtime.os.name != "nt":
        with pytest.raises(runtime.ServiceError) as e:
            runtime._native_pick_dir(tmp_path, "t")
        assert e.value.status_code == 501
