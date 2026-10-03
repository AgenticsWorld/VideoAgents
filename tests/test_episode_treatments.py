"""§5A 事件取舍契约:episode_plan treatments 机检与剧本侧 [事件] 行核对。"""
import json
import subprocess
import sys
from pathlib import Path

from modules import episode_treatments as et

REPO = Path(__file__).resolve().parents[1]


def _events():
    return {"events": [
        {"id": "ev0001", "chapter": "ch001", "importance": "major", "caused_by": [], "leads_to": ["ev0002"]},
        {"id": "ev0002", "chapter": "ch001", "importance": "minor", "caused_by": ["ev0001"], "leads_to": ["ev0003"]},
        {"id": "ev0003", "chapter": "ch002", "importance": "background", "caused_by": [], "leads_to": []},
        {"id": "ev0004", "chapter": "ch002", "importance": "major", "caused_by": ["ev0002"], "leads_to": []},
        {"id": "ev0005", "chapter": "ch003", "importance": "minor", "caused_by": [], "leads_to": []},
        {"id": "ev0006", "chapter": "ch009", "importance": "minor", "caused_by": [], "leads_to": []},
    ]}


def _plan(treatments=None, budget=180, events=("ev0001", "ev0002", "ev0003", "ev0004", "ev0005"), roadmap=None):
    ep = {"episode_id": "ep01", "events": list(events), "duration_budget_s": budget,
          "chapter_range": {"start": "ch001", "end": "ch003"},
          "hook_point": {"opening": "ev0001", "cliffhanger": "ev0004"}, "carry_over": [], "recap_needed": []}
    if treatments is not None:
        ep["treatments"] = treatments
    plan = {"default_duration_budget_s": budget, "episodes": [ep]}
    if roadmap:
        plan["roadmap"] = roadmap
    return plan


GOOD = [
    {"event": "ev0001", "treatment": "dramatize"},
    {"event": "ev0002", "treatment": "mention", "reason": "过渡,旁白一句带过"},
    {"event": "ev0003", "treatment": "cut", "reason": "纯背景"},
    {"event": "ev0004", "treatment": "dramatize"},
    {"event": "ev0005", "treatment": "merge", "merge_into": "ev0004", "reason": "同场"},
]


def test_plan_good_passes():
    res = et.verify_plan(_plan(GOOD, roadmap=[{"episode_range": "ep02-ep03"}]), _events(), {"foreshadowing": []})
    assert all(res["checks"].values()), res["errors"]
    assert res["episodes"][0]["n_dramatize"] == 2 and res["episodes"][0]["cap"] == 2
    assert res["n_scope_events"] == 5          # roadmap:ch009 的 ev0006 不在对账范围


def test_plan_missing_treatments_fails_and_roadmapless_requires_all():
    res = et.verify_plan(_plan(None), _events(), None)
    assert res["checks"]["treatments_complete"] is False
    assert res["checks"]["events_classified_once"] is False   # 无 roadmap → ev0006 未归类


def test_plan_cap_and_hook_and_cut_rules():
    bad = [
        {"event": "ev0001", "treatment": "cut", "reason": "x"},                 # major + 卡点 + 是 ev0002 的 caused_by
        {"event": "ev0002", "treatment": "dramatize"},
        {"event": "ev0003", "treatment": "dramatize"},
        {"event": "ev0004", "treatment": "dramatize"},
        {"event": "ev0005", "treatment": "merge", "merge_into": "ev0001", "reason": "x"},   # 指向非 dramatize
    ]
    res = et.verify_plan(_plan(bad), _events(), None)
    c = res["checks"]
    assert c["dramatize_within_cap"] is False and c["hook_points_dramatized"] is False
    assert c["cut_not_on_causal_chain"] is False and c["merge_target_valid"] is False
    assert any("major" in e for e in res["errors"]) and any("caused_by" in e for e in res["errors"])


def test_plan_reason_required_and_dict_form_aliases():
    plan = _plan(None)
    plan["episodes"][0]["event_treatment"] = {"ev0001": "正片展开", "ev0002": "旁白带过", "ev0003": "删",
                                              "ev0004": "演", "ev0005": "并入"}
    res = et.verify_plan(plan, _events(), None)
    tr, errs = et.episode_treatments(plan["episodes"][0])
    assert not errs and tr["ev0001"]["treatment"] == "dramatize" and tr["ev0002"]["treatment"] == "mention"
    assert res["checks"]["treatments_complete"] is False       # 字典形无 reason
    assert any("reason" in e for e in res["errors"])


SP_OK = """# EP01
## S01 | EXT | SCN-0001 路上 | 黄昏
**[事件] ev0001 | [出场] CHAR-0001 | [时长] 60s**
### 【画面/动作】
老者拦路。
## S02 | INT | SCN-0002 机场 | 夜
**[事件] ev0004, ev0005(并入) | [出场] CHAR-0001、CHAR-0002 | [时长] 90s**
### 【旁白】
**王三合(V.O.)**:后来的事一句话就能说完。
"""


def test_screenplay_ok():
    res = et.verify_screenplay(SP_OK, _plan(GOOD), "ep01")
    assert all(res["checks"].values()), res["errors"]
    assert not res["legacy"]


def test_screenplay_violations():
    sp = SP_OK + """## S03 | INT | SCN-0003 家 | 夜
**[事件] ev0002 | [出场] CHAR-0001 | [时长] 20s**
动作:过渡场。
## S04 | INT | SCN-0003 家 | 夜
**[事件] ev0003 | [出场] CHAR-0001 | [时长] 20s**
动作:被删事件。
"""
    plan = _plan(GOOD)
    plan["episodes"][0]["treatments"][3] = {"event": "ev0004", "treatment": "dramatize"}
    sp = sp.replace("ev0004, ev0005(并入)", "ev0005(并入)")     # ev0004 不再成场
    res = et.verify_screenplay(sp, plan, "ep01")
    c = res["checks"]
    assert c["dramatized_events_covered"] is False and c["cut_events_absent"] is False
    assert c["scene_has_dramatized_event"] is False


def test_screenplay_legacy_plan_warns_not_fails():
    res = et.verify_screenplay(SP_OK, _plan(None, events=("ev0001", "ev0004", "ev0005")), "ep01")
    assert res["legacy"] and all(res["checks"].values())
    assert any("旧格式" in w for w in res["warns"])


def test_screenplay_without_event_lines_fails():
    sp = "# EP01\n## S01 | EXT | SCN-0001 路上 | 黄昏\n动作:没有事件行。\n"
    res = et.verify_screenplay(sp, _plan(GOOD), "ep01")
    assert res["checks"]["dramatized_events_covered"] is False
    assert any("[事件] 行" in e for e in res["errors"])



def test_screenplay_adjacent_same_spacetime():
    body = ("## S01 | EXT | SCN-0001 崖下 | 夜\n**[事件] ev0001 | [出场] CHAR-0001 | [时长] 10s**\n动作:甲。\n\n"
            "## S02 | EXT | SCN-0001 崖下 | 夜\n**[事件] ev0001 | [出场] CHAR-0002 | [时长] 10s**\n动作:乙。\n\n"
            "## S03 | EXT | SCN-0001 崖下 | 黎明\n**[事件] ev0001 | [出场] CHAR-0002 | [时长] 10s**\n动作:丙。\n")
    plan = _plan(GOOD)
    new = "---\ngenerated_at: 2026-09-29\n---\n" + body
    res = et.verify_screenplay(new, plan, "ep01")
    assert res["checks"]["scene_spacetime_continuous"] is False
    assert any("S01→S02" in e and "S02→S03" not in e for e in res["errors"])
    exempt = new.replace("## S02 | EXT | SCN-0001 崖下 | 夜\n", "## S02 | EXT | SCN-0001 崖下 | 夜\n(split_note: 切走再切回)\n")
    assert et.verify_screenplay(exempt, plan, "ep01")["checks"]["scene_spacetime_continuous"] is True
    legacy = et.verify_screenplay(body, plan, "ep01")          # 无 generated_at = 存量,只 WARN
    assert legacy["checks"]["scene_spacetime_continuous"] is True
    assert any("S01→S02" in w for w in legacy["warns"])

def test_cli_roundtrip(tmp_path):
    story = tmp_path / "story"
    (story / "episodes" / "ep01").mkdir(parents=True)
    (story / "episode_plan.json").write_text(json.dumps(_plan(GOOD, roadmap=[{"episode_range": "ep02-ep03"}]), ensure_ascii=False),
                                            encoding="utf-8")
    (story / "events.json").write_text(json.dumps(_events(), ensure_ascii=False), encoding="utf-8")
    (story / "episodes" / "ep01" / "screenplay.md").write_text(SP_OK, encoding="utf-8")
    r = subprocess.run([sys.executable, str(REPO / "code" / "verify_episode_plan.py"), "--out-root", str(tmp_path), "--json"],
                       capture_output=True, text=True, cwd=REPO)
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)["ok"] is True
    r = subprocess.run([sys.executable, str(REPO / "code" / "verify_episode_plan.py"), "--out-root", str(tmp_path),
                        "--sec-per-event", "180"], capture_output=True, text=True, cwd=REPO)
    assert r.returncode == 1 and "dramatize_within_cap        : FAIL" in r.stdout
    r = subprocess.run([sys.executable, str(REPO / "code" / "check_screenplay_events.py"), "--out-root", str(tmp_path),
                        "--ep", "ep01"], capture_output=True, text=True, cwd=REPO)
    assert r.returncode == 0, r.stdout + r.stderr
