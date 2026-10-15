"""参考文件页「其他」分类(refs/other/):任意格式可传,排在「文本」之后。"""
import asyncio

import pytest


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    from services.runtime import core

    monkeypatch.setattr(core, "PROJECTS_DIR", tmp_path / "projects")
    core.PROJECTS_DIR.mkdir()
    core.ensure_project("p")
    return core


def _upload(core, name, category="other", data=b"x", subdir=""):
    return asyncio.run(core.api_refs_upload(data, "p", category, subdir, name))


def test_other_follows_text_and_is_in_project_skeleton(runtime):
    assert (runtime.PROJECTS_DIR / "p" / "refs" / "other").is_dir()
    keys = [c["key"] for c in asyncio.run(runtime.api_refs_list("p"))["categories"]]
    assert keys[keys.index("text") + 1] == "other"
    assert "`other/`" in (runtime.PROJECTS_DIR / "p" / "refs" / "README.md").read_text()


def test_other_accepts_any_file_type(runtime):
    paths = [_upload(runtime, n)["path"] for n in
             ("设定集 v2.pdf", "budget.xlsx", "pack.tar.gz", "scene.blend", "LICENSE", "shot.PNG", "demo.mp4")]
    assert paths == ["other/v2.pdf", "other/budget.xlsx", "other/pack.tar.gz", "other/scene.blend",
                     "other/LICENSE", "other/shot.png", "other/demo.mp4"]
    files = {f["name"]: f for c in asyncio.run(runtime.api_refs_list("p"))["categories"]
             if c["key"] == "other" for f in c["files"]}
    assert set(files) == {p.split("/", 1)[1] for p in paths}
    assert files["shot.png"]["is_image"] and files["demo.mp4"]["is_video"]          # 媒体仍按扩展名预览
    assert not any(files["v2.pdf"][k] for k in ("is_image", "is_audio", "is_video", "is_font"))


def test_other_existing_project_without_dir_and_note_roundtrip(runtime):
    refs = runtime.PROJECTS_DIR / "p" / "refs"
    (refs / "other").rmdir()                                  # 存量项目没有 other/ 目录
    assert [c for c in asyncio.run(runtime.api_refs_list("p"))["categories"] if c["key"] == "other"][0]["files"] == []
    rel = _upload(runtime, "relations.csv")["path"]
    asyncio.run(runtime.api_refs_note({"project": "p", "path": rel, "note": "角色关系表,编剧参考"}))
    assert "- `other/relations.csv`:角色关系表,编剧参考" in (refs / "NOTES.md").read_text()
    asyncio.run(runtime.api_refs_delete({"project": "p", "path": rel}))
    assert not (refs / rel).exists() and "relations.csv" not in (refs / "NOTES.md").read_text()
    assert (refs / "other").is_dir()                          # 分类目录本身保留


def test_other_does_not_loosen_other_categories(runtime):
    with pytest.raises(runtime.ServiceError):
        _upload(runtime, "notes.pdf", category="fonts")      # 字体分类仍只收字体
    with pytest.raises(runtime.ServiceError):
        _upload(runtime, "a.pdf", category="misc")           # 未登记的分类仍拒收
