"""分镜预览「添加参考图 → 🖊 草图」(2026-09-15):本集分镜 + 每镜草图的选图器数据,以及组 refs 放行 assets/storyboard/。"""
import asyncio
import json
from pathlib import Path

import pytest

from services.runtime import core

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "PROJECTS_DIR", tmp_path)
    base = tmp_path / "demo"
    (base / "directing" / "ep01").mkdir(parents=True)
    (base / "directing" / "ep01" / "storyboard.json").write_text(json.dumps({"scenes": [
        {"scene_no": "S01", "shots_draft": [{"order": 1, "content": "开场"}, {"order": 2, "content": "第二镜"}]},
        {"scene_no": "S02", "shots_draft": [{"order": 1, "content": "换场"}]},
    ]}, ensure_ascii=False))
    (base / "directing" / "ep01" / "shot_list.json").write_text(json.dumps({"shots": [
        {"shot_id": "sh001", "storyboard_ref": "S01/order:1"},
        {"shot_id": "sh002", "storyboard_ref": "S01/order:2"},
    ]}))
    sk = base / "assets" / "storyboard" / "ep01"
    (sk / "_grids").mkdir(parents=True)
    for name in ("S01-01.png", "S01-01_v2.png", "S01-01.new.png", "S01-02.png", "_grids/x.png", "S01-010.png"):
        (sk / name).write_bytes(PNG)
    (sk / "index.json").write_text(json.dumps({"shots": {"S01-01": {"status": "done", "file": "assets/storyboard/ep01/S01-01.png"}}}))
    pf = base / "assets" / "prompts" / "ep01" / "G01.json"
    pf.parent.mkdir(parents=True)
    pf.write_text(json.dumps({"refs": [], "notes": []}))
    return base


def test_sketch_pick_lists_shots_and_all_sketches(project):
    d = core._board_sketch_pick("demo", "ep01")
    assert d["has_storyboard"] and [s["key"] for s in d["shots"]] == ["S01-01", "S01-02", "S02-01"]
    s1 = d["shots"][0]
    assert s1["shot_ids"] == ["sh001"] and s1["status"] == "done" and s1["content"] == "开场"
    # 主图在前、后缀变体在后;.new.png 临时件、_grids/ 与 S01-010(别的镜)不混进来
    assert [i["name"] for i in s1["images"]] == ["S01-01.png", "S01-01_v2.png"]
    assert s1["images"][0]["rel"] == "assets/storyboard/ep01/S01-01.png"
    assert s1["images"][0]["url"].startswith("/projects/demo/assets/storyboard/ep01/S01-01.png?v=")
    assert [i["name"] for i in d["shots"][1]["images"]] == ["S01-02.png"]
    assert d["shots"][2]["images"] == [] and d["shots"][2]["shot_ids"] == []


def test_sketch_pick_without_storyboard(project):
    (project / "directing" / "ep01" / "storyboard.json").unlink()
    d = core._board_sketch_pick("demo", "ep01")
    assert d["has_storyboard"] is False and d["shots"] == []


def test_grpref_add_accepts_storyboard_sketch(project):
    r = asyncio.run(core.api_grpref_add({"project": "demo", "ep": "ep01", "grp": "G01",
                                         "ref": "assets/storyboard/ep01/S01-01.png"}))
    assert r == {"ref": "assets/storyboard/ep01/S01-01.png", "refs": 1}
    d = json.loads((project / "assets" / "prompts" / "ep01" / "G01.json").read_text())
    assert d["refs"] == ["assets/storyboard/ep01/S01-01.png"]
    assert core._grpref_user_added(d, "assets/storyboard/ep01/S01-01.png")   # 用户手动加的,组卡可删
    assert "从分镜草图" in d["notes"][0]
    with pytest.raises(core.ServiceError):   # 仍只放行资产/草图目录
        asyncio.run(core.api_grpref_add({"project": "demo", "ep": "ep01", "grp": "G01",
                                         "ref": "assets/storyboard/ep01/../../../x.png"}))
    with pytest.raises(core.ServiceError):
        asyncio.run(core.api_grpref_add({"project": "demo", "ep": "ep01", "grp": "G01", "ref": "directing/ep01/storyboard.json"}))
