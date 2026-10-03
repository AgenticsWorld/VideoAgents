# -*- coding: utf-8 -*-
"""生成组硬约束机检(code/check_generation_groups.py 规则 1–5,WORKFLOW §7A)。

一组 = 一次视频生成:镜号要全覆盖且连续、同一场次、时长在模型单次上限内、角色不超参考图槽位。
这几条放过去,下游要么生成失败要么成片缺镜,所以逐条锁「合规的过、每种违规各报各的」。
转场 / 旁白挂点 / 声画分离等其余规则各有专门的测试文件。
"""
import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "code" / "check_generation_groups.py"


@pytest.fixture()
def cgg():
    # 每个用例独立加载:main() 会按项目设置改写模块级 MAX_GROUP_S,不与别的测试文件共用同一份模块状态
    spec = importlib.util.spec_from_file_location("cgg_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _shot(i, scene, scene_no, dur, chars=()):
    return {"shot_id": f"sh{i:03d}", "scene_id": scene, "scene_no": scene_no, "duration_s": dur, "characters": list(chars)}


def _group(gid, shots, total, chars, prev, scene="SCN-0001", scene_no="S01"):
    return {"group_id": gid, "scene_id": scene, "scene_no": scene_no, "shots": list(shots), "total_duration_s": total,
            "characters_union": list(chars), "continuity_from": prev}


def _base():
    """6 镜 3 组:S01 四镜分两组,S02 两镜一组。"""
    shots = [_shot(1, "SCN-0001", "S01", 2, ["CHAR-A"]), _shot(2, "SCN-0001", "S01", 3, ["CHAR-A", "CHAR-B"]),
             _shot(3, "SCN-0001", "S01", 4, ["CHAR-B"]), _shot(4, "SCN-0001", "S01", 4, []),
             _shot(5, "SCN-0002", "S02", 3, ["CHAR-C"]), _shot(6, "SCN-0002", "S02", 3, ["CHAR-C"])]
    groups = [_group("grp001", ["sh001", "sh002"], 5, ["CHAR-A", "CHAR-B"], None),
              _group("grp002", ["sh003", "sh004"], 8, ["CHAR-B"], "grp001"),
              _group("grp003", ["sh005", "sh006"], 6, ["CHAR-C"], "grp002", "SCN-0002", "S02")]
    return {"episode": "ep01", "shots": shots, "generation_groups": groups}


def _only(errors, key):
    return [e for e in errors if key in e]


# ---------------------------------------------------------------- 基线

def test_valid_grouping_passes(cgg):
    assert cgg.check(_base()) == []


def test_ungrouped_shot_list_is_reported(cgg):
    sl = _base()
    del sl["generation_groups"]
    assert cgg.check(sl) == ["shot_list 无 generation_groups 字段(未分组)"]
    sl["generation_groups"] = []
    assert len(cgg.check(sl)) == 1


# ---------------------------------------------------------------- 1. 覆盖不重不漏

def test_missing_shot(cgg):
    sl = _base()
    sl["generation_groups"][1].update(shots=["sh003"], total_duration_s=4)
    cover = _only(cgg.check(sl), "groups_cover_all_shots")
    assert len(cover) == 1 and "缺 ['sh004']" in cover[0]


def test_shot_in_two_groups(cgg):
    sl = _base()
    sl["generation_groups"][1].update(shots=["sh002", "sh003", "sh004"], total_duration_s=11, characters_union=["CHAR-A", "CHAR-B"])
    cover = _only(cgg.check(sl), "groups_cover_all_shots")
    assert len(cover) == 1 and "重或多 ['sh002']" in cover[0] and "缺 []" in cover[0]


def test_group_references_unknown_shot(cgg):
    sl = _base()
    sl["generation_groups"][2]["shots"] = ["sh005", "sh006", "sh999"]
    errors = cgg.check(sl)
    assert "重或多 ['sh999']" in _only(errors, "groups_cover_all_shots")[0]
    assert _only(errors, "grp003 group_shots_contiguous")           # 镜表里没有的镜号也算不连续


def test_trailing_shots_left_out(cgg):
    sl = _base()
    sl["generation_groups"].pop()
    assert "缺 ['sh005', 'sh006']" in _only(cgg.check(sl), "groups_cover_all_shots")[0]


# ---------------------------------------------------------------- 2. 连续且同场次

def test_interleaved_groups_are_not_contiguous(cgg):
    sl = _base()
    sl["generation_groups"][0].update(shots=["sh001", "sh003"], total_duration_s=6, characters_union=["CHAR-A", "CHAR-B"])
    sl["generation_groups"][1].update(shots=["sh002", "sh004"], total_duration_s=7, characters_union=["CHAR-A", "CHAR-B"])
    errors = cgg.check(sl)
    assert len(_only(errors, "group_shots_contiguous")) == 2
    assert not _only(errors, "groups_cover_all_shots")              # 覆盖本身没问题,只是穿插


def test_reversed_order_inside_group(cgg):
    sl = _base()
    sl["generation_groups"][0]["shots"] = ["sh002", "sh001"]
    assert _only(cgg.check(sl), "grp001 group_shots_contiguous")


def test_group_spanning_two_scenes(cgg):
    sl = _base()
    sl["generation_groups"][1].update(shots=["sh003"], total_duration_s=4)
    sl["generation_groups"][2].update(shots=["sh004", "sh005", "sh006"], total_duration_s=10)
    errors = cgg.check(sl)
    assert "['SCN-0001', 'SCN-0002']" in _only(errors, "grp003 same_scene:")[0]
    assert "['S01', 'S02']" in _only(errors, "grp003 same_scene_instance")[0]


def test_same_place_different_scene_instance(cgg):
    # 同一 SCN 被两个场次用到(回到同一地点):地点相同也不能并成一组
    sl = _base()
    for s in sl["shots"][4:]:
        s["scene_id"] = "SCN-0001"
    sl["generation_groups"][1].update(shots=["sh003"], total_duration_s=4)
    sl["generation_groups"][2].update(shots=["sh004", "sh005", "sh006"], total_duration_s=10, scene_id="SCN-0001")
    errors = cgg.check(sl)
    assert not _only(errors, "same_scene:") and _only(errors, "grp003 same_scene_instance")


@pytest.mark.parametrize("empty", [{"shots": []}, {"shots": None}, {}])
def test_empty_group_is_reported_not_crashed(cgg, empty):
    # 曾在这里抛 IndexError:机检直接崩掉,其余组的问题也报不出来
    sl = _base()
    extra = _group("grp004", [], 5, [], "grp003", "SCN-0002", "S02")
    del extra["shots"]
    extra.update(empty)
    sl["generation_groups"].append(extra)
    sl["generation_groups"][0]["total_duration_s"] = 6
    errors = cgg.check(sl)
    assert _only(errors, "grp004 group_shots_contiguous: 组内没有镜头")
    assert _only(errors, "grp001 duration_sum")                      # 别的组照常被检


# ---------------------------------------------------------------- 3. 时长

def _single(durations, total):
    shots = [_shot(i + 1, "SCN-0001", "S01", d) for i, d in enumerate(durations)]
    return {"shots": shots, "generation_groups": [_group("grp001", [s["shot_id"] for s in shots], total, [], None)]}


@pytest.mark.parametrize("durations,total", [([2, 2], 4), ([5, 5, 5], 15), ([1.5, 2.5], 4), ([3.5, 3.5, 4, 4], 15)])
def test_duration_bounds_inclusive(cgg, durations, total):
    assert cgg.check(_single(durations, total)) == []


@pytest.mark.parametrize("durations,total", [([1, 2], 3), ([8, 8], 16), ([3], 3)])
def test_duration_out_of_range(cgg, durations, total):
    errors = cgg.check(_single(durations, total))
    assert _only(errors, "grp001 duration_range") and not _only(errors, "duration_sum") and not _only(errors, "duration_int")


@pytest.mark.parametrize("total", [5.0, "5", None])
def test_duration_must_be_int(cgg, total):
    assert _only(cgg.check(_single([2, 3], total)), "grp001 duration_int")


def test_duration_must_equal_sum_of_shots(cgg):
    errors = cgg.check(_single([2, 3], 6))
    assert "total_duration_s=6 ≠ Σ镜时长 5" in _only(errors, "grp001 duration_sum")[0]
    assert not _only(errors, "duration_range")


def test_project_max_group_s_is_read_from_settings(cgg, tmp_path):
    path = tmp_path / "directing" / "ep01" / "shot_list.json"
    path.parent.mkdir(parents=True)
    path.write_text("{}")
    assert cgg.project_max_group_s(path) == 15                       # 没有 settings.json
    for value, expected in ((10, 10), (30, 30), (4, 4), (3, 15), (31, 15), ("x", 15)):
        (tmp_path / "settings.json").write_text(json.dumps({"shot_group": {"max_group_s": value}}))
        assert cgg.project_max_group_s(path) == expected, value


def test_range_follows_max_group_s(cgg):
    sl = _single([6, 6], 12)
    assert cgg.check(sl) == []
    cgg.MAX_GROUP_S = 10
    assert _only(cgg.check(sl), "grp001 duration_range")
    cgg.MAX_GROUP_S = 20
    assert cgg.check(_single([9, 9], 18)) == []


# ---------------------------------------------------------------- 4. 角色数

def _cast(chars_per_shot, union):
    shots = [_shot(i + 1, "SCN-0001", "S01", 3, c) for i, c in enumerate(chars_per_shot)]
    return {"shots": shots, "generation_groups": [_group("grp001", [s["shot_id"] for s in shots], 3 * len(shots), union, None)]}


def test_four_characters_allowed(cgg):
    assert cgg.check(_cast([["A", "B"], ["C", "D"]], ["D", "C", "B", "A"])) == []     # 顺序无关


def test_five_characters_rejected(cgg):
    errors = cgg.check(_cast([["A", "B", "C"], ["D", "E"]], ["A", "B", "C", "D", "E"]))
    assert _only(errors, "grp001 characters_lte_4") and not _only(errors, "characters_union")


def test_understated_union_does_not_hide_fifth_character(cgg):
    # 组上少报一个人:人数仍按镜表实数算,少报另报「与镜表不符」
    errors = cgg.check(_cast([["A", "B", "C"], ["D", "E"]], ["A", "B", "C", "D"]))
    assert _only(errors, "grp001 characters_lte_4") and _only(errors, "characters_union 与镜表不符")


def test_characters_union_missing_or_wrong(cgg):
    sl = _cast([["A"], ["B"]], ["A", "B"])
    del sl["generation_groups"][0]["characters_union"]
    assert _only(cgg.check(sl), "grp001 characters_union 缺失")
    sl["generation_groups"][0]["characters_union"] = ["A", "B", "C"]
    assert _only(cgg.check(sl), "grp001 characters_union 与镜表不符")
    sl["generation_groups"][0]["characters_union"] = []
    assert _only(cgg.check(sl), "grp001 characters_union 与镜表不符")


# ---------------------------------------------------------------- 5. 组序链

def test_first_group_must_not_point_back(cgg):
    sl = _base()
    sl["generation_groups"][0]["continuity_from"] = "grp000"
    assert _only(cgg.check(sl), "grp001 continuity_from")


@pytest.mark.parametrize("value", [None, "grp001", "grp003"])
def test_later_group_must_point_to_previous(cgg, value):
    sl = _base()
    sl["generation_groups"][2]["continuity_from"] = value
    errors = _only(cgg.check(sl), "continuity_from")
    assert len(errors) == 1 and errors[0].startswith("grp003 ")


def test_each_violation_reported_independently(cgg):
    sl = _base()
    sl["generation_groups"][0]["total_duration_s"] = 6                # 时长不符
    sl["generation_groups"][1]["characters_union"] = []               # 角色并集不符
    sl["generation_groups"][2]["continuity_from"] = None              # 组序链断
    errors = cgg.check(sl)
    assert [e.split(":")[0] for e in errors] == ["grp001 duration_sum", "grp002 characters_union 与镜表不符", "grp003 continuity_from"]


# ---------------------------------------------------------------- 草案分组

def test_proposed_groups_pass_the_check(cgg):
    sl = _base()
    sl["generation_groups"] = cgg.propose_groups(sl["shots"])
    assert cgg.check(sl) == []
    assert [g["shots"] for g in sl["generation_groups"]] == [["sh001", "sh002", "sh003", "sh004"], ["sh005", "sh006"]]
    assert [g["continuity_from"] for g in sl["generation_groups"]] == [None, "grp001"]


def test_propose_splits_on_duration_cap(cgg):
    shots = [_shot(i + 1, "SCN-0001", "S01", 6) for i in range(5)]
    groups = cgg.propose_groups(shots)
    assert [g["total_duration_s"] for g in groups] == [12, 12, 6]
    assert cgg.check({"shots": shots, "generation_groups": groups}) == []


def test_propose_splits_on_fifth_character(cgg):
    shots = [_shot(1, "SCN-0001", "S01", 3, ["A", "B"]), _shot(2, "SCN-0001", "S01", 3, ["C", "D"]),
             _shot(3, "SCN-0001", "S01", 3, ["E"]), _shot(4, "SCN-0001", "S01", 3, ["A"])]
    groups = cgg.propose_groups(shots)
    assert [g["shots"] for g in groups] == [["sh001", "sh002"], ["sh003", "sh004"]]
    assert groups[1]["characters_union"] == ["A", "E"]
    assert cgg.check({"shots": shots, "generation_groups": groups}) == []


def test_propose_splits_on_scene_instance(cgg):
    shots = [_shot(1, "SCN-0001", "S01", 4), _shot(2, "SCN-0001", "S03", 4)]      # 同地点、不同场次
    assert [g["shots"] for g in cgg.propose_groups(shots)] == [["sh001"], ["sh002"]]


# ---------------------------------------------------------------- CLI

def _run(path, *args):
    proc = subprocess.run([sys.executable, str(SCRIPT), str(path), "--skip-7d", "--skip-transition", *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    return proc.returncode, proc.stdout + proc.stderr


def _project(tmp_path, shot_list, settings=None):
    path = tmp_path / "demo" / "directing" / "ep01" / "shot_list.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(shot_list, ensure_ascii=False), encoding="utf-8")
    if settings is not None:
        (tmp_path / "demo" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    return path


def test_cli_exit_codes(tmp_path):
    path = _project(tmp_path, _base())
    code, out = _run(path)
    assert code == 0 and "机检通过: 3 组全部合规" in out, out
    bad = copy.deepcopy(_base())
    bad["generation_groups"][1].update(shots=["sh003"], total_duration_s=4)
    path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    code, out = _run(path)
    assert code == 1 and "groups_cover_all_shots" in out and "sh004" in out, out


def test_cli_uses_project_group_cap(tmp_path):
    sl = _single([9, 9], 18)
    path = _project(tmp_path, sl)
    code, out = _run(path)
    assert code == 1 and "duration_range: 18 ∉ [4,15]" in out, out
    (tmp_path / "demo" / "settings.json").write_text(json.dumps({"shot_group": {"max_group_s": 20}}), encoding="utf-8")
    code, out = _run(path)
    assert code == 0, out


def test_cli_propose_write_round_trip(tmp_path):
    sl = _base()
    del sl["generation_groups"]
    path = _project(tmp_path, sl)
    assert _run(path)[0] == 1                                        # 未分组
    code, out = _run(path, "--propose", "--write")
    assert code == 0 and "6 镜 → 2 组" in out, out
    written = json.loads(path.read_text(encoding="utf-8"))
    assert [g["group_id"] for g in written["generation_groups"]] == ["grp001", "grp002"]
    assert _run(path)[0] == 0                                        # 写回的草案自身过检
