"""#87:分镜分组时长口径与白模编译一致(差 ≤0.05s);白模待决项 issue_id 按已知 ep/gid 前缀精确校验。"""
import importlib.util
from pathlib import Path

import pytest

from modules.whitebox_issues import validate_issues

ROOT = Path(__file__).resolve().parents[1]


def _cgg():
    spec = importlib.util.spec_from_file_location("cgg", ROOT / "code" / "check_generation_groups.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _shot_list(durations, total):
    shots = [{"shot_id": f"sh{i}", "scene_id": "SCN-1", "duration_s": d, "characters": []} for i, d in enumerate(durations)]
    return {"shots": shots, "generation_groups": [{"group_id": "grp010", "scene_id": "SCN-1", "shots": [s["shot_id"] for s in shots],
                                                   "total_duration_s": total, "characters_union": [], "continuity_from": None}]}


@pytest.mark.parametrize("durations,total,ok", [
    ([6.4, 6.4, 6.0], 19, False),      # Σ18.8 声明 19:白模编译拒,这里也须拒
    ([6.0, 3.2], 9, False),            # Σ9.2
    ([6.0, 3.0], 9, True),
    ([1.8, 1.0, 2.4, 1.8, 1.0], 8, True),   # 浮点累加 7.999999999999999 仍通过
])
def test_duration_sum_matches_whitebox_tolerance(durations, total, ok):
    errors = [e for e in _cgg().check(_shot_list(durations, total)) if "duration_sum" in e]
    assert (not errors) == ok, errors


def _issue(iid):
    return {"issue_id": iid, "kind": "other", "severity": "advisory", "question": "q?", "provisional": "p",
            "options": [{"id": "A", "label": "a"}, {"id": "B", "label": "b"}]}


@pytest.mark.parametrize("ep,gid,iid,ok", [
    ("ep01", "grp010", "WBI-ep01-grp010-001", True),
    ("ep01", "grp010", "WBI-ep01-grp011-001", False),
    ("ep01", "grp010", "WBI-ep02-grp010-001", False),
    ("ep01", "grp010", "WBI-ep01-grp010-01", False),
    ("ep01", "grp010", "WBI-ep01-grp010-0010", False),
    # 含连字符的 ep/gid:旧 _ID_RE 贪婪解析成 ep=ep-01-grp、gid=x 等歧义组合,现按已知前缀精确比对
    ("ep-01", "grp-x", "WBI-ep-01-grp-x-002", True),
    ("ep-01", "grp-x", "WBI-ep-01-grp-x-y-002", False),
])
def test_issue_id_prefix_exact_match(ep, gid, iid, ok):
    call = lambda: validate_issues({"issues": [_issue(iid)]}, ep, gid, 4.0, ["sh1"], set())
    if ok:
        assert call()[0]["issue_id"] == iid
    else:
        with pytest.raises(ValueError, match="must look like"):
            call()
