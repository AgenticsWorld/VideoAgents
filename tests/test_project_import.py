"""Project import (zip → data/projects/<name>) regressions."""
import asyncio
import io
import zipfile

import pytest


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    from services.runtime import core

    monkeypatch.setattr(core, "PROJECTS_DIR", tmp_path / "projects")
    core.PROJECTS_DIR.mkdir()
    monkeypatch.setattr(core, "STATE", {})
    return core


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n, b in files.items():
            zf.writestr(n, b)
    return buf.getvalue()


async def _chunks(data: bytes, size=7):
    for i in range(0, len(data), size):
        yield data[i:i + size]


def _run(core, data, filename="pkg.zip", name=""):
    return asyncio.run(core.api_projects_import(_chunks(data), filename, name))


def test_wrapped_dir_takes_folder_name(runtime):
    r = _run(runtime, _zip({"myproj/settings.json": b"{}", "myproj/refs/style/a.txt": b"x",
                            "__MACOSX/myproj/._settings.json": b"junk", "myproj/.DS_Store": b"z"}))
    assert r["name"] == "myproj" and r["files"] == 2 and r["renamed_from"] is None
    assert (runtime.PROJECTS_DIR / "myproj" / "settings.json").read_text() == "{}"
    assert (runtime.PROJECTS_DIR / "myproj" / "refs" / "characters").is_dir()   # ensure_project 骨架
    assert not (runtime.PROJECTS_DIR / "myproj" / ".DS_Store").exists()
    assert not list(runtime.PROJECTS_DIR.glob(".import*"))                        # 暂存清理


def test_root_layout_takes_zip_stem_and_single_subdir_is_not_a_wrapper(runtime):
    r = _run(runtime, _zip({"refs/style/a.txt": b"x"}), filename="Cool Project (1).zip")
    assert r["name"] == "Cool-Project-1"
    assert (runtime.PROJECTS_DIR / "Cool-Project-1" / "refs" / "style" / "a.txt").exists()
    r = _run(runtime, _zip({"brief.md": b"# b", "settings.json": b"{}"}), filename="two.zip")
    assert r["name"] == "two"


def test_duplicate_auto_suffix_and_explicit_name_conflict(runtime):
    (runtime.PROJECTS_DIR / "p").mkdir()
    r = _run(runtime, _zip({"p/brief.md": b"x"}))
    assert r["name"] == "p-2" and r["renamed_from"] == "p"
    r = _run(runtime, _zip({"p/brief.md": b"x"}))
    assert r["name"] == "p-3"
    with pytest.raises(runtime.ServiceError) as e:
        _run(runtime, _zip({"p/brief.md": b"x"}), name="p")
    assert e.value.status_code == 409


def test_rejects_traversal_bad_zip_and_empty(runtime):
    with pytest.raises(runtime.ServiceError) as e:
        _run(runtime, _zip({"x/../../evil.txt": b"x", "x/ok.txt": b"y"}))
    assert e.value.status_code == 400 and not list(runtime.PROJECTS_DIR.iterdir())
    with pytest.raises(runtime.ServiceError) as e:
        _run(runtime, b"not a zip at all")
    assert e.value.status_code == 400
    with pytest.raises(runtime.ServiceError) as e:
        _run(runtime, b"")
    assert e.value.status_code == 400
    with pytest.raises(runtime.ServiceError):
        _run(runtime, _zip({"__MACOSX/a": b"1", "d/.DS_Store": b"2"}))
    assert not list(runtime.PROJECTS_DIR.iterdir())


def test_english_messages_follow_ui_language(runtime):
    runtime.STATE["ui_lang"] = "en"
    with pytest.raises(runtime.ServiceError) as e:
        _run(runtime, b"nope")
    assert e.value.detail == "Not a valid zip file"
