"""layout_map_bound 机检(code/layout_map_bound_check.py):白模隐藏人物豁免主体定义句(#100)。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
import layout_map_bound_check as lmb  # noqa: E402


def _case(tmp_path, video_prompt):
    pf = tmp_path / "grp001.json"
    pf.write_text(json.dumps({"group_id": "grp001", "refs": ["assets/concepts/characters/CHAR-0001/sheet.png"],
                              "video_prompt": video_prompt}, ensure_ascii=False))
    groups = {"grp001": {"group_id": "grp001", "blocking_map": {"characters": [
        {"id": "CHAR-0001", "label": "哪吒", "route_en": "Nezha walks from the gate to the altar."},
        {"id": "CRE-0002", "label": "青牛", "route_en": "The ox waits outside the gate."}]}}}
    return pf, groups


def test_hidden_cast_is_exempt_from_subject_sentence(tmp_path, monkeypatch):
    vp = "哪吒@Image 1:少年。Shot 1: Nezha walks from the gate to the altar. The ox waits outside the gate."
    pf, groups = _case(tmp_path, vp)
    monkeypatch.setattr(lmb, "cast_filter", lambda *a: None)                      # 白模未开:照旧要求 @Image
    errs, _ = lmb.check_group(pf, groups, tmp_path, "ep01", False)
    assert len(errs) == 1 and "CRE-0002" in errs[0] and "主体定义句" in errs[0]
    monkeypatch.setattr(lmb, "cast_filter", lambda *a: {"visible": ["CHAR-0001"], "hidden": {"CRE-0002": "整组不在画幅内"}})
    assert lmb.check_group(pf, groups, tmp_path, "ep01", False)[0] == []


def test_hidden_cast_route_is_still_checked(tmp_path, monkeypatch):
    pf, groups = _case(tmp_path, "哪吒@Image 1:少年。Shot 1: Nezha walks from the gate to the altar.")
    monkeypatch.setattr(lmb, "cast_filter", lambda *a: {"visible": ["CHAR-0001"], "hidden": {"CRE-0002": "x"}})
    errs, _ = lmb.check_group(pf, groups, tmp_path, "ep01", False)
    assert len(errs) == 1 and "动线句未逐字命中" in errs[0]
