"""小说文本页(2026-11-07):预览设定产物菜单「参考文件」下的「📖 小说文本」,只读列出项目 novel/ 下所有 .md 文件。"""
import asyncio
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "apps/web/static"


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    from services.runtime import core

    monkeypatch.setattr(core, "PROJECTS_DIR", tmp_path / "projects")
    core.PROJECTS_DIR.mkdir()
    core.ensure_project("p")
    return core


def put(base: Path, rel: str, text: str = "x") -> None:
    f = base / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")


def test_lists_md_recursively_in_natural_order(runtime):
    novel = runtime.PROJECTS_DIR / "p" / "novel"
    for rel in ["ch10.md", "ch2.md", "chapters/ch11.md", "chapters/ch01.md", "第一卷 #1.md", "Upper.MD",
                "original.txt", "chapters/index.json", ".draft.md", ".cache/x.md"]:
        put(novel, rel)
    r = asyncio.run(runtime.api_novel_list("p"))
    assert r["exists"] and not r["truncated"]
    # 根目录的排前,数字按数值排(ch2 在 ch10 前);.MD 也算;隐藏文件 / 目录不列
    assert [f["path"] for f in r["files"]] == ["ch2.md", "ch10.md", "Upper.MD", "第一卷 #1.md",
                                               "chapters/ch01.md", "chapters/ch11.md"]
    assert r["others"] == 2                          # original.txt + chapters/index.json
    url = r["files"][3]["url"]                       # 文件名里的空格 / # / 中文要转义,否则 # 之后会被浏览器当锚点
    assert url.startswith("/projects/p/novel/") and " " not in url and "#" not in url


def test_no_novel_dir_and_missing_project(runtime):
    assert asyncio.run(runtime.api_novel_list("p")) == {"project": "p", "exists": False, "files": [], "others": 0, "truncated": False}
    with pytest.raises(runtime.ServiceError):
        asyncio.run(runtime.api_novel_list("nope"))


def test_list_caps_file_count(runtime, monkeypatch):
    monkeypatch.setattr(runtime, "NOVEL_LIST_MAX", 3)
    for i in range(5):
        put(runtime.PROJECTS_DIR / "p" / "novel", f"ch{i}.md")
    r = asyncio.run(runtime.api_novel_list("p"))
    assert [f["path"] for f in r["files"]] == ["ch0.md", "ch1.md", "ch2.md"] and r["truncated"]


def test_api_route_urls_fetch_the_file(runtime):
    from services.api import app as api_app

    text = "# 第1章 断桥\n\n清明清晨。\n"
    put(runtime.PROJECTS_DIR / "p" / "novel", "chapters/第一卷 #1.md", text)
    client = TestClient(api_app.app)
    body = client.get("/api/v1/projects/p/novel").json()
    url = body["files"][0]["url"]
    assert url.startswith("/api/v1/projects/p/artifacts/novel/chapters/")
    resp = client.get(url)
    assert resp.status_code == 200 and resp.content.decode("utf-8") == text


def test_menu_entry_and_page_switchers():
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    menu = re.search(r'<div id="preview-menu">.*?</div></div>', index, re.S).group(0)
    hrefs = re.findall(r'href="(/preview/[a-z]+)"', menu)
    assert hrefs[:2] == ["/preview/refs", "/preview/novel"]          # 紧跟在「参考文件」下面
    for page in sorted(STATIC.glob("preview_*.html")):
        src = page.read_text(encoding="utf-8")
        if "const PV_PAGES=" in src:                                # 各预览页右上角切换下拉同样排在参考文件后
            assert "['/preview/refs','\\u{1F5BC} 参考文件'],['/preview/novel','\\u{1F4D6} 小说文本']," in src, page.name


def test_page_strings_in_every_dictionary():
    keys = ["小说文本", "📖 小说文本", "小说文本 · Agentics：VideoAgents", "章节目录", "第 {p} / {n} 页", "约 {n} 字",
            "更新于 {t}", "novel/ · {n} 个 md 文件", "项目目录 novel/ 下还没有 md 文件", "另有 {n} 个非 md 文件(如 .txt)未列出"]
    page = (STATIC / "preview_novel.html").read_text(encoding="utf-8")
    for k in keys[3:]:
        assert f"'{k}'" in page, k
    for d in sorted((STATIC / "i18n").glob("*.js")):
        if d.name == "i18n.js":
            continue
        src = d.read_text(encoding="utf-8")
        for k in keys:
            assert f'\n"{k}": ' in src, (d.name, k)
