"""NPC 参与构图(2026-10-09,docs/npc_staging.md):总开关 + 场次三态/密度的生效值、用户覆盖文件、关键词建议、
拆解表判定机检、故事板/镜头表/构图机检、组 prompt 固定段与身份锁收窄、草图提示词、白模图例、设置校验与接口。"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules import npc_staging as npc  # noqa: E402
from modules import storyboard_board as sbb  # noqa: E402


def _w(p: Path, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False) if not isinstance(data, str) else data, encoding="utf-8")


def _project(tmp_path, master=None, scenes=None):
    base = tmp_path / "p"
    _w(base / "settings.json", {"output": {"npc_staging": master}} if master else {})
    bd = {"schema_version": "script_breakdown/1.0", "ep": "ep01", "scenes": scenes if scenes is not None else [
        {"no": "S01", "scene_name": "城南集市", "summary": "午市人声鼎沸", "npc": {"on": True, "density": "dense", "reason": "原文午市人声鼎沸"}},
        {"no": "S02", "scene_name": "卧房", "summary": "两人密谈", "npc": {"on": False, "density": None, "reason": "密谈"}},
        {"no": "S03", "scene_name": "走廊", "summary": "他走过"},
    ]}
    _w(base / "story/episodes/ep01/script_breakdown.json", bd)
    _w(base / "story/episodes/ep01/screenplay.md", "## S01 | 外 | 城南集市 | 昼\n")
    return base


def _storyboard(base, s01_npc=True, applied=("dense",), s02_npc=False):
    shots1 = [{"order": 1, "content": "全景集市", "size_hint": "全景"},
              {"order": 2, "content": "近景主角", "size_hint": "近景"}]
    if s01_npc:
        shots1[0]["npc"] = [{"layer": "bg", "what": "挑担路人从画右走过"}, {"layer": "fg", "what": "虚焦灯笼穗"}]
        shots1[1]["npc"] = [{"layer": "bg", "what": "背景虚化人影"}]
    sc1 = {"scene_no": "S01", "shots_draft": shots1}
    if applied:
        sc1["npc_applied"] = {"on": True, "density": applied[0]}
    shots2 = [{"order": 1, "content": "两人对坐", "size_hint": "中景"}]
    if s02_npc:
        shots2[0]["npc"] = [{"layer": "bg", "what": "丫鬟走过"}]
    _w(base / "directing/ep01/storyboard.json", {"scenes": [sc1, {"scene_no": "S02", "shots_draft": shots2}]})


# ---------------------------------------------------------------- 生效值 / 覆盖文件

def test_resolve_precedence(tmp_path):
    base = _project(tmp_path)
    r = npc.resolve(base, "ep01")
    assert r["master"] == "auto"
    assert (r["scenes"]["S01"]["on"], r["scenes"]["S01"]["density"], r["scenes"]["S01"]["source"]) == (True, "dense", "auto")
    assert (r["scenes"]["S02"]["on"], r["scenes"]["S02"]["source"]) == (False, "auto")
    assert (r["scenes"]["S03"]["on"], r["scenes"]["S03"]["source"]) == (False, "none")        # 未判定 = 关
    # 用户:S03 开(密度回落到建议的 sparse),S01 自动但改密度,S02 强开
    npc.set_override(base, "ep01", "S03", "on")
    npc.set_override(base, "ep01", "S01", "auto", "sparse")
    npc.set_override(base, "ep01", "S02", "on", "medium")
    r = npc.resolve(base, "ep01")
    assert (r["scenes"]["S03"]["on"], r["scenes"]["S03"]["density"], r["scenes"]["S03"]["source"]) == (True, "sparse", "user")
    assert (r["scenes"]["S01"]["density"], r["scenes"]["S01"]["source"]) == ("sparse", "auto")
    assert (r["scenes"]["S02"]["on"], r["scenes"]["S02"]["density"]) == (True, "medium")
    # 总开关 off 一律关,但保留自动/用户信息供界面显示
    _w(base / "settings.json", {"output": {"npc_staging": "off"}})
    r = npc.resolve(base, "ep01")
    assert all(not v["on"] and v["source"] == "master_off" for v in r["scenes"].values())
    assert r["scenes"]["S02"]["user"]["mode"] == "on"


def test_set_override_roundtrip_and_validation(tmp_path):
    base = _project(tmp_path)
    p = npc.override_path(base, "ep01")
    npc.set_override(base, "ep01", "S03(续)", "off", "dense")       # 关闭不记密度;场次号允许括号
    d = json.loads(p.read_text())
    assert d["scenes"]["S03(续)"]["mode"] == "off" and d["scenes"]["S03(续)"]["density"] is None
    npc.set_override(base, "ep01", "S03(续)", "auto")                # 回到自动 = 删除;全空删文件
    assert not p.exists()
    for bad in (("S01", "maybe", None), ("S01", "on", "huge"), ("../x", "on", None), ("", "on", None)):
        with pytest.raises(ValueError):
            npc.set_override(base, "ep01", *bad)


def test_suggest_keywords():
    assert npc.suggest({"scene_name": "城南集市", "action": "午市人声鼎沸"})["density"] == "dense"
    assert npc.suggest({"scene_name": "火车站候车大厅"}) ["on"] is True
    assert npc.suggest({"scene_name": "书院回廊"})["density"] == "sparse"
    assert npc.suggest({"scene_name": "城南集市", "action": "集市空荡荡的,四下无人"})["on"] is False
    assert npc.suggest({"scene_name": "卧房"})["rule"] == "off_private"
    assert npc.suggest({"scene_name": "大街", "time_of_day": "深夜"})["rule"] == "night"
    assert npc.suggest({"scene_name": "夜市", "time_of_day": "深夜"})["on"] is True
    assert npc.suggest({"scene_name": "Main street", "action": "a bustling crowd"}, zh=False)["density"] == "dense"
    assert npc.suggest({"scene_name": "山顶"})["on"] is False


# ---------------------------------------------------------------- 拆解表判定 npc_judged

def test_check_judgments(tmp_path):
    base = _project(tmp_path)
    e, w = npc.check_judgments(base, "ep01")
    assert any("S03 缺" in x for x in e) and not w                  # 新拆解表缺判定 = FAIL
    p = base / "story/episodes/ep01/script_breakdown.json"
    old = time.mktime(time.strptime("2026-09-01", "%Y-%m-%d"))
    os.utime(p, (old, old))
    e, w = npc.check_judgments(base, "ep01")
    assert not e and any("S03 缺" in x for x in w)                  # 存量只 WARN
    _w(base / "settings.json", {"output": {"npc_staging": "off"}})
    assert npc.check_judgments(base, "ep01") == ([], [])            # 总开关 off 跳过
    assert npc.judgment_errors("S09", {"on": True}) and npc.judgment_errors("S09", {"on": False, "density": "dense", "reason": "x"})
    assert npc.judgment_errors("S09", {"on": True, "density": "dense", "reason": "x"}) == []


def test_breakdown_validate_reports_bad_npc():
    from modules import script_breakdown as sb
    errs, _ = sb.validate({"schema_version": sb.SCHEMA_VERSION, "ep": "ep01",
                           "scenes": [{"no": "S01", "summary": "x", "npc": {"on": "yes"}}]})
    assert any("S01.npc.on" in e for e in errs)


# ---------------------------------------------------------------- 故事板 / 镜头表 / 构图

def test_check_storyboard(tmp_path):
    base = _project(tmp_path)
    _storyboard(base)
    e, w, st = npc.check_storyboard(base, "ep01")
    assert e == [] and st == {"S01": "ok", "S02": "ok"}
    # 用户把 S01 密度改成稀疏 → 回执过期;S02 强开 → 没写 npc
    npc.set_override(base, "ep01", "S01", "auto", "sparse")
    npc.set_override(base, "ep01", "S02", "on")
    e, w, st = npc.check_storyboard(base, "ep01")
    assert st == {"S01": "stale", "S02": "stale"}
    assert any(x.startswith("S01:") and "npc_applied" in x for x in e) and any("S02: NPC 参与构图已开启" in x for x in e)
    # 关闭场次写了 npc = FAIL;坏条目 = FAIL
    npc.set_override(base, "ep01", "S02", "auto")
    npc.set_override(base, "ep01", "S01", "auto")
    _storyboard(base, s02_npc=True)
    e, _, _ = npc.check_storyboard(base, "ep01")
    assert any("S02" in x and "写了 npc[]" in x for x in e)
    sb = json.loads((base / "directing/ep01/storyboard.json").read_text())
    sb["scenes"][1]["shots_draft"][0]["npc"] = [{"layer": "sky", "what": ""}]
    _w(base / "directing/ep01/storyboard.json", sb)
    e, _, _ = npc.check_storyboard(base, "ep01")
    assert any(".layer 须为" in x for x in e)


def test_final_shots_composition_and_shot_list(tmp_path):
    base = _project(tmp_path)
    _storyboard(base)
    _w(base / "directing/ep01/shot_list.json", {"shots": [
        {"shot_id": "sh001", "scene_no": "S01", "storyboard_ref": "S01/order:1"},                       # 回查草案
        {"shot_id": "sh002", "scene_no": "S01", "storyboard_ref": "S01/order:2", "npc": []},           # 定稿镜显式不要
        {"shot_id": "sh003", "scene_no": "S02", "storyboard_ref": "S02/order:1", "npc": [{"layer": "bg", "what": "x"}]},
    ], "generation_groups": [{"group_id": "grp001", "shots": ["sh001", "sh002"]}, {"group_id": "grp002", "shots": ["sh003"]}]})
    fin = npc.final_shot_npc(base, "ep01")
    assert list(fin) == ["sh001"] and len(fin["sh001"]["npc"]) == 2              # S02 关:不出现
    e, _ = npc.check_shots(base, "ep01")
    assert any(x.startswith("sh003") for x in e)
    _w(base / "directing/ep01/shots/sh001/composition.json", {"layers": {"fg": "灯笼穗虚焦", "mg": "主角", "bg": "无"}})
    e, _ = npc.check_composition(base, "ep01")
    assert e == ["sh001: npc[] 有背景(bg)NPC,composition.json layers.bg 却为空"]


# ---------------------------------------------------------------- 组 prompt 固定段

PROMPT = ("Overall visual style: x.\n\nIdentity lock: exactly 2 characters on screen; every person on screen must match one "
          "of the reference images; no extra or duplicate person.\n\nShot 1: 集市全景。\n\nShot 2: 近景。\n\n"
          "Global constraints: no watermark.")


def test_prompt_segment_idempotent_and_lock(tmp_path):
    rows = [{"n": 1, "shot_id": "sh001", "scene_no": "S01", "npc": [{"layer": "bg", "what": "挑担路人从画右走过。"}]}]
    a = npc.apply_prompt({"video_prompt": PROMPT}, rows, "dense")
    t = a["video_prompt"]
    assert t.index(npc.SEG_BEGIN) < t.index("Global constraints:") and npc.SEG_END in t
    assert "Shot 1 — background: 挑担路人从画右走过." in t and "a lively crowd" in t
    assert "every named character on screen must match their own reference image" in t
    assert "exactly 2 named character(s) on screen" in t and "no extra or duplicate person" not in t
    assert npc.check_prompt(a, rows, "dense") == []
    assert npc.apply_prompt(a, rows, "dense") == a                                    # 幂等
    assert npc.check_prompt(a, rows, "sparse")                                        # 密度变了 = 过期
    assert npc.check_prompt({"video_prompt": PROMPT}, rows, "dense")                  # 缺段 + 身份锁未收窄
    off = npc.apply_prompt(a, [], None)
    assert npc.SEG_BEGIN not in off["video_prompt"] and "npc_staging" not in off
    assert npc.check_prompt(a, [], None)                                              # 无 NPC 残留段 = FAIL
    from modules.prompt_layout import paragraphize
    assert paragraphize(t) == t


def test_sync_prompts_writes_group_file(tmp_path):
    base = _project(tmp_path)
    _storyboard(base)
    _w(base / "directing/ep01/shot_list.json", {"shots": [
        {"shot_id": "sh001", "scene_no": "S01", "storyboard_ref": "S01/order:1"},
        {"shot_id": "sh002", "scene_no": "S01", "storyboard_ref": "S01/order:2"}],
        "generation_groups": [{"group_id": "grp001", "shots": ["sh001", "sh002"]}]})
    _w(base / "assets/prompts/ep01/grp001.json", {"video_prompt": PROMPT})
    r = npc.sync_prompts(base, "ep01")
    assert r["errors"] and not r["updated"]                                           # 只检不写
    r = npc.sync_prompts(base, "ep01", write=True)
    assert r["updated"] == ["grp001"] and r["errors"] == []
    t = json.loads((base / "assets/prompts/ep01/grp001.json").read_text())["video_prompt"]
    assert "Shot 1 — foreground: 虚焦灯笼穗; background: 挑担路人从画右走过." in t and "Shot 2 — background: 背景虚化人影." in t
    npc.set_override(base, "ep01", "S01", "off")                                     # 关掉 → 重写删段
    r = npc.sync_prompts(base, "ep01", write=True)
    assert r["errors"] == [] and npc.SEG_BEGIN not in json.loads((base / "assets/prompts/ep01/grp001.json").read_text())["video_prompt"]


# ---------------------------------------------------------------- 草图 / 白模图例 / 看板

def test_sketch_prompt_and_board(tmp_path):
    base = _project(tmp_path)
    _storyboard(base)
    board = sbb.load_board(base, "ep01")
    sc = board["scenes"][0]
    assert sc["npc_applied"] == {"on": True, "density": "dense"} and sc["shots"][0]["npc"][0]["layer"] == "bg"
    prompt, _ = sbb.build_prompt(sc, sc["shots"][0], {})
    assert "Anonymous NPC figures and objects for depth" in prompt and "挑担路人" in prompt
    gp, _ = sbb.build_grid_prompt([(sc, sc["shots"][0])], {}, 2, 2)
    assert "NPC: background:" in gp


def test_whitebox_legend_marks_npc():
    from modules.whitebox_refs import legend_rows
    rows = legend_rows({"actors": [], "extras": [{"id": "EXTRA-NPC-01", "label": "路人", "color": "#888888"},
                                                  {"id": "EXTRA-01", "label": "巡逻兵", "color": "#888888"}]})
    assert "anonymous NPC passer-by, not a named character" in rows[0] and "background extra" in rows[1]


# ---------------------------------------------------------------- 设置 / 接口 / 页面

def test_settings_validation_and_api(tmp_path, monkeypatch):
    from services.runtime import core
    assert core.DEFAULT_GENCONFIG["output"]["npc_staging"] == "auto"
    out = dict(core.DEFAULT_GENCONFIG["output"])
    core._validate_output({**out, "npc_staging": "off"})
    with pytest.raises(core.ServiceError):
        core._validate_output({**out, "npc_staging": "on"})
    monkeypatch.setattr(core, "PROJECTS_DIR", tmp_path)
    base = _project(tmp_path)
    _storyboard(base)
    r = asyncio.run(core.api_npc_set("p", "ep01", {"scene": "S02", "mode": "on", "density": "sparse"}))
    s2 = r["npc"]["scenes"]["S02"]
    assert (s2["on"], s2["density"], s2["source"], s2["applied"]) == (True, "sparse", "user", "stale")
    with pytest.raises(core.ServiceError):
        asyncio.run(core.api_npc_set("p", "ep01", {"scene": "S02", "mode": "bogus"}))
    with pytest.raises(core.ServiceError):
        asyncio.run(core.api_npc_set("p", "ep99", {"scene": "S02", "mode": "on"}))


def test_role_prompt_npc_line(tmp_path, monkeypatch):
    from services.runtime import core
    monkeypatch.setattr(core, "PROJECTS_DIR", tmp_path)
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "settings.json").write_text("{}")
    line = next(ln for ln in core.build_role_prompt("07-directing/storyboard", "p").splitlines() if ln.startswith("- NPC 参与构图:"))
    assert "按场次判定(auto" in line and "npc_applied" in line
    (tmp_path / "p" / "settings.json").write_text(json.dumps({"output": {"npc_staging": "off"}}))
    line = next(ln for ln in core.build_role_prompt("07-directing/storyboard", "p").splitlines() if ln.startswith("- NPC 参与构图:"))
    assert "全部关闭(off)" in line


def test_webui_wiring():
    idx = (ROOT / "apps/web/static/index.html").read_text(encoding="utf-8")
    assert 'id="out-npc"' in idx and 'id="npo-npc"' in idx and "npc_staging:$('#out-npc').value" in idx
    for page in ("preview_script.html", "preview_board.html"):
        html = (ROOT / "apps/web/static" / page).read_text(encoding="utf-8")
        assert "/static/npc-switch.js" in html and "NpcSwitch.chip(" in html
    js = (ROOT / "apps/web/static/npc-switch.js").read_text(encoding="utf-8")
    assert "/episodes/${encodeURIComponent(CFG.ep())}/npc" in js
