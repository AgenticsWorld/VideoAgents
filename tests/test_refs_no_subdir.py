"""参考文件页不再建子目录(2026-10-26):上传 / 图库导入一律落 refs/<分类>/,图对应哪个角色 / 生物 / 道具由注释说明;
存量项目里已有的子目录照常列出、可删。"""
import asyncio
import inspect
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "apps/web/static"
NEW_DESC = ["角色形象参考;在注释里写明是哪个角色(角色名或 id)", "生物/坐骑/异兽参考;在注释里写明是哪个生物(生物名或 id)",
            "道具/法宝/服装参考;在注释里写明是哪件道具或服装"]
DROPPED = ["子目录(可选)", "填写后上传到该子目录,如角色名(仅英文数字)",
           "只对目标板块生效;角色 / 生物 / 道具可填角色名、生物名或 costumes(仅英文数字)",
           "角色形象参考;填子目录(角色名或 id)可定向到该角色", "生物/坐骑/异兽参考;填子目录(生物名或 id)可定向到该生物",
           "道具/法宝参考;服装可填 costumes 子目录"]


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    from services.runtime import core

    monkeypatch.setattr(core, "PROJECTS_DIR", tmp_path / "projects")
    core.PROJECTS_DIR.mkdir()
    core.ensure_project("p")
    return core


def test_upload_has_no_subdir_and_lands_in_category(runtime):
    from services.api import app as api_app

    assert "subdir" not in inspect.signature(runtime.api_refs_upload).parameters
    assert "subdir" not in inspect.signature(api_app.upload_reference).parameters   # 旧页面带来的 ?subdir= 被忽略
    r = asyncio.run(runtime.api_refs_upload(b"\x89PNG", "p", "characters", "nezha front.png"))
    assert r["path"] == "characters/nezha_front.png"
    assert "子目录" not in (runtime.PROJECTS_DIR / "p" / "refs" / "README.md").read_text(encoding="utf-8")


def test_legacy_subdir_files_still_listed_and_deletable(runtime):
    refs = runtime.PROJECTS_DIR / "p" / "refs"
    (refs / "characters" / "nezha").mkdir()
    (refs / "characters" / "nezha" / "a.png").write_bytes(b"\x89PNG")
    files = [f["path"] for c in asyncio.run(runtime.api_refs_list("p"))["categories"]
             if c["key"] == "characters" for f in c["files"]]
    assert files == ["characters/nezha/a.png"]
    asyncio.run(runtime.api_refs_delete({"project": "p", "path": "characters/nezha/a.png"}))
    assert not (refs / "characters" / "nezha").exists() and (refs / "characters").is_dir()   # 删空的存量子目录顺带清掉


def test_page_and_gallery_have_no_subdir_ui():
    page = (STATIC / "preview_refs.html").read_text(encoding="utf-8")
    gallery = (STATIC / "stock-gallery.js").read_text(encoding="utf-8")
    for text in (page, gallery):
        assert "subdir" not in text.lower() and "子目录" not in text and "sg-sub" not in text
    meta = dict(re.findall(r"^\s+(characters|creatures|props):\['[^']+','[^']+','([^']+)'\]", page, re.M))
    assert list(meta.values()) == NEW_DESC
    for path in sorted((STATIC / "i18n").glob("??.js")):
        text = path.read_text(encoding="utf-8")
        for key in NEW_DESC:
            assert f"\n{json.dumps(key, ensure_ascii=False)}: " in text, (path.name, key)
        for key in DROPPED:
            assert f"\n{json.dumps(key, ensure_ascii=False)}: " not in text, (path.name, key)


def test_workflow_matches_by_notes_with_legacy_subdirs():
    wf = (ROOT / "agents/WORKFLOW.md").read_text(encoding="utf-8")
    rule = re.search(r"^5\. \*\*匹配规则\*\*.*$", wf, re.M).group(0)
    assert "NOTES.md" in rule and "存量" in rule and "按子目录名" not in rule
    assert "按角色建子目录" not in wf and "按生物建子目录" not in wf
