"""#59:默认装(或只有一套)缺台账时回落 sheet.png;非默认装缺图仍报缺。"""
import json

from modules import scene_cast


def _setup(tmp_path, costumes_doc):
    (tmp_path / "bible").mkdir()
    (tmp_path / "bible" / "costumes.json").write_text(json.dumps(costumes_doc))
    for cid in ("CHAR-0001", "CHAR-0002", "CHAR-0003"):
        d = tmp_path / "assets/concepts/characters" / cid
        d.mkdir(parents=True)
        (d / "sheet.png").write_bytes(b"x")
    return {
        "shots": [{"shot_id": "sh001", "scene_no": "S01", "characters": ["CHAR-0001", "CHAR-0002", "CHAR-0003"]}],
        "generation_groups": [{"group_id": "grp001", "scene_id": "SCN-1", "shots": ["sh001"],
                               "costumes_by_char": {"CHAR-0001": "COS-001", "CHAR-0002": "COS-011",
                                                    "CHAR-0003": "CHAR-0003_daily_01"}}],
    }


def _rows(tmp_path, source):
    return {r["id"]: r for r in scene_cast.scene_reference_rows(tmp_path, "ep01", source)["grp001"]}


def test_default_costume_falls_back_to_sheet(tmp_path, monkeypatch):
    monkeypatch.setattr(scene_cast, "whitebox_cast", lambda *a: None)
    doc = {
        "costumes": [
            {"id": "COS-001", "character_ref": "CHAR-0001", "default": True},
            {"id": "COS-002", "character_ref": "CHAR-0001", "default": False},
            {"id": "COS-010", "character_id": "CHAR-0002", "default": True},
            {"id": "COS-011", "character_id": "CHAR-0002", "default": False},
        ],
        "characters": [{"character_id": "CHAR-0003",
                        "outfits": [{"id": "CHAR-0003_daily_01", "occasion": "日常"}]}],
    }
    rows = _rows(tmp_path, _setup(tmp_path, doc))
    # 标了 default:true → 回落 sheet.png
    assert rows["CHAR-0001"]["ref"].endswith("CHAR-0001/sheet.png") and not rows["CHAR-0001"]["missing"]
    # 非默认装缺台账 → 仍报缺,不拿默认装 sheet 顶替
    assert rows["CHAR-0002"]["ref"] is None and rows["CHAR-0002"]["missing"]
    # 只登记了一套(套装 id 以角色编号打头)→ 视为默认装
    assert rows["CHAR-0003"]["ref"].endswith("CHAR-0003/sheet.png") and not rows["CHAR-0003"]["missing"]


def test_ledger_hit_still_preferred(tmp_path, monkeypatch):
    monkeypatch.setattr(scene_cast, "whitebox_cast", lambda *a: None)
    src = _setup(tmp_path, {"costumes": [{"id": "COS-001", "character_ref": "CHAR-0001", "default": True}]})
    d = tmp_path / "assets/concepts/characters/CHAR-0001"
    (d / "sheet_COS-001.png").write_bytes(b"x")
    (d / "costume_sheets.json").write_text(json.dumps({"sheets": [{"costume_ref": "COS-001", "file": "sheet_COS-001.png"}]}))
    assert _rows(tmp_path, src)["CHAR-0001"]["ref"].endswith("sheet_COS-001.png")


def test_wardrobe_shapes():
    w = {"CHAR-0001": {"costumes": {"a", "b"}, "defaults": {"a"}}}
    assert scene_cast.is_default_costume(w, "CHAR-0001", "a")
    assert not scene_cast.is_default_costume(w, "CHAR-0001", "b")
    assert not scene_cast.is_default_costume({}, "CHAR-0001", "a")


def test_wardrobe_pointer_and_nested(tmp_path):
    (tmp_path / "bible").mkdir()
    (tmp_path / "bible" / "costumes.json").write_text(json.dumps({"characters": [
        {"character_id": "CHAR-0001", "default_outfit_id": "CHAR-0001_b",
         "outfits": [{"id": "CHAR-0001_a"}, {"id": "CHAR-0001_b"}]}]}))
    w = scene_cast.costume_wardrobe(tmp_path)
    assert w["CHAR-0001"] == {"costumes": {"CHAR-0001_a", "CHAR-0001_b"}, "defaults": {"CHAR-0001_b"}}
    assert scene_cast.costume_wardrobe(tmp_path / "missing") == {}
