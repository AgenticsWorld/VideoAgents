"""时间尺(modules/time_cost.py)+ 四级机检 CLI(code/check_time_budget.py)+ 对白适配新口径。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
from modules import time_cost as tc  # noqa: E402

LINE = "哪吒,再把你师父的宝贝多用几件来,看我道术如何!"


def test_line_est_adds_onset_pause_and_pace():
    assert tc.eff_chars(LINE) == 21 and tc.inner_pauses(LINE) == 2
    base = tc.line_est(LINE)                                   # medium,4.2 字/秒
    assert base == round(0.7 + 21 / 4.2 + 0.8, 2)
    assert tc.line_est(LINE, "slow", 253.5) > base > tc.line_est(LINE, "fast", 253.5)
    assert tc.line_est(LINE, "medium", 252.0) == tc.line_est(LINE)   # 252 cpm = 4.2 字/秒
    assert tc.line_est_legacy(LINE, 253.5) == 5.0                 # 旧口径:字数 ÷ 语速,一位小数
    assert tc.line_est("嗯。") >= tc.MIN_LINE_S and tc.line_est("a") == round(0.7 + 1 / 4.2, 2)
    assert tc.line_est("") == 0.0


def test_pace_guess_and_dialogue_need():
    assert tc.guess_pace("哭求") == "slow" and tc.guess_pace("急报") == "fast" and tc.guess_pace("平叙") == ""
    need = tc.dialogue_need([{"text": LINE}, {"text": "原来是你!", "pace": "fast"}])
    assert need == round(tc.line_est(LINE) + tc.line_est("原来是你!", "fast") + 2 * (tc.PRE_SPEECH_S + tc.POST_SPEECH_S), 2)
    assert tc.dialogue_need([{"text": LINE, "est": 5.0}]) == 5.7   # 有记录估时按记录值 + 余量


def test_beat_costs_prose_and_phrase():
    assert tc.beat_cost("整个人撞在石壁上") == (1.2, "fall")
    assert tc.beat_cost("话落") == (0.3, "hold")
    assert tc.beat_cost("把它放回案上") == (0.7, "default")
    total, beats = tc.action_cost("顺壁滑倒,蜷在地上挣命")                   # 短语:每句一拍
    assert len(beats) == 2 and total == 2.4
    total, beats = tc.action_cost("手一抖,乾坤圈掷出,一道短促的金色光弧,夹颈一圈", prose=True)
    kinds = [b["kind"] for b in beats]
    assert kinds == ["throw", "fx", "default"] and beats[-1]["cost_s"] == tc.DESC_CLAUSE_S   # 同类并拍,描写短句按 0.25
    assert tc.is_meta_action_line("拍:④ 更大冲突 | 时长:28s") and tc.is_meta_action_line("动作段 ab07-1 起手 → 反制")
    assert not tc.is_meta_action_line("【起手】洞口人影一闪")


def test_sequence_connectors_and_blocking_beats():
    assert tc.count_sequence_connectors("他先收肘,随后甩臂,再抖腕;接着转身。then he runs. 再次 先生") == 5   # 再次/先生 不算
    bj = {"characters": [{"id": "A", "beats": [{"t": 0.2, "action": "撞在石壁上"}, {"t": 0.5, "action": "滑倒"},
                                               {"t": 0.6, "action": "幡杆歪了"}, {"t": 1.0, "action": "点名", "sync": "台词『哪吒,』起"}]}]}
    beats = tc.blocking_beats(bj)
    assert [b["cost_s"] for b in beats] == [1.2, 1.2, 0.7, 0.0] and beats[-1]["kind"] == "synced"
    issues = tc.beat_spacing_issues(bj)
    assert len(issues) == 1 and "t=0.5" in issues[0]
    need = tc.shot_need([], beat_costs=[b["cost_s"] for b in beats])
    assert need["action_s"] == 3.1 and need["need_s"] == 3.5          # 动作镜首尾各 0.2


def test_group_density_and_budget_plan():
    assert tc.group_density_issue([1.2, 0.8, 0.6] * 7, 28) is not None
    assert tc.group_density_issue([1.2, 0.8, 0.6] * 5, 13) is None     # 密集但 ≤15s
    assert tc.group_density_issue([5, 6, 7, 8], 26) is None             # 不密集不管
    assert tc.budget_plan(300, 280, 50)["status"] == "ok"
    p = tc.budget_plan(300, 400, 50)
    assert (p["status"], p["recommended_budget_s"], p["extension_pct"]) == ("extend", 400.0, 33.3)
    p = tc.budget_plan(300, 480, 50)
    assert (p["status"], p["cap_s"], p["need_cut_s"]) == ("over_cap", 450.0, 30.0)
    assert tc.budget_plan(300, 320, 0)["status"] == "over_cap"         # 0 = 不许加长
    assert tc.extend_pct({"duration": {"episode_extend_pct": 20}}) == 20.0 and tc.extend_pct({}) == 50.0


def test_legacy_detection():
    assert tc.is_legacy("2026-09-26") and tc.is_legacy(None) and not tc.is_legacy("2026-10-03T08:00:00")
    assert tc.doc_date({"_meta": {"written_at": "2026-09-27"}}) == "2026-09-27"
    assert tc.frontmatter_date("---\nep: ep07\ngenerated_at: 2026-10-05\n---") == "2026-10-05"


# ---------------------------------------------------------------- CLI 端到端(临时项目)
SCREENPLAY = """---
ep: ep01
generated_at: 2026-10-05
duration_budget_s: 20
---
## S01 | EXT | SCN-0001 平台 | 夜
**[事件] ev0001 | [出场] CHAR-0001, CHAR-0002 | [时长] 6s**

动作:【起手】洞口人影一闪,彩云提着灯出来。

- **CHAR-0002 彩云**:「师父唤你进——」 {emotion: 传话, pace: fast, est_duration_s: 1.1}

动作:哪吒手一抖,乾坤圈掷出,灯碎了,人撞在石壁上,顺着壁滑倒。

- **CHAR-0001 哪吒**:「原来是你!」 {emotion: 冷怒, pace: slow, est_duration_s: 0.9}

## S02 | INT | SCN-0002 洞内 | 夜
**[事件] ev0002 | [出场] CHAR-0001 | [时长] 12s**

动作:哪吒站在洞口不动。
"""


def make_project(tmp: Path) -> Path:
    base = tmp / "proj"
    (base / "story" / "episodes" / "ep01").mkdir(parents=True)
    (base / "directing" / "ep01" / "shots" / "sh001").mkdir(parents=True)
    (base / "directing" / "ep01" / "shots" / "sh002").mkdir(parents=True)
    (base / "assets" / "prompts" / "ep01").mkdir(parents=True)
    (base / "bible" / "characters" / "CHAR-0001").mkdir(parents=True)
    (base / "bible" / "characters" / "CHAR-0001" / "voice.json").write_text(json.dumps({"speed_cpm": [240, 264]}))
    (base / "settings.json").write_text(json.dumps({"duration": {"episode_minutes": 1, "episode_extend_pct": 50}}))
    (base / "story" / "episodes" / "ep01" / "screenplay.md").write_text(SCREENPLAY)
    shots = [
        {"shot_id": "sh001", "scene_id": "SCN-0001", "duration_s": 0.8, "characters": ["CHAR-0001"], "is_dialogue": False,
         "poses": {"CHAR-0001": {"pose": "stand", "action": "掷圈,撞壁滑倒"}}},
        {"shot_id": "sh002", "scene_id": "SCN-0001", "duration_s": 1.0, "characters": ["CHAR-0001"], "is_dialogue": True,
         "dialogue_lines": [{"speaker": "CHAR-0001", "text": "原来是你!", "emotion": "冷怒", "pace": "slow", "est_duration_s": 0.9}],
         "poses": {"CHAR-0001": {"pose": "stand", "action": ""}}},
    ] + [{"shot_id": f"sh{i:03d}", "scene_id": "SCN-0001", "duration_s": 0.7, "characters": [], "is_dialogue": False, "poses": {}}
         for i in range(3, 24)]
    sl = {"_meta": {"written_at": "2026-10-05"}, "budget_s": 60, "shots": shots,
          "generation_groups": [{"group_id": "grp001", "scene_id": "SCN-0001", "shots": [s["shot_id"] for s in shots],
                                 "total_duration_s": 16, "has_dialogue": True, "audio_plan": "dialogue"}]}
    (base / "directing" / "ep01" / "shot_list.json").write_text(json.dumps(sl, ensure_ascii=False))
    (base / "directing" / "ep01" / "shots" / "sh001" / "blocking.json").write_text(json.dumps({
        "shot_id": "sh001", "characters": [{"id": "CHAR-0001", "beats": [{"t": 0.1, "action": "掷圈"}, {"t": 0.2, "action": "撞在石壁上"},
                                                                       {"t": 0.5, "action": "滑倒"}]}]}, ensure_ascii=False))
    (base / "directing" / "ep01" / "shots" / "sh002" / "blocking.json").write_text(json.dumps({
        "shot_id": "sh002", "characters": [{"id": "CHAR-0001", "beats": [{"t": 0.2, "action": "开口", "sync": "台词『原来是你』"}]}]}, ensure_ascii=False))
    (base / "assets" / "prompts" / "ep01" / "grp001.json").write_text(json.dumps({
        "video_prompt": "Overall visual style: x.\n\nShot 1: 使用：哪吒。不采用：无。他先收肘,随后把右臂甩出去,再抖腕,接着撞上石壁,然后顺壁滑倒,最后蜷成一团。\n\n"
                        "Shot 2: 使用：哪吒。不采用：无。他开口:{原来是你!}\n\n【未采用素材】无。\n\n【保持一致】x。\n\nGlobal constraints: none."},
        ensure_ascii=False))
    return base


def test_check_time_budget_all_scopes(tmp_path, capsys):
    import check_time_budget as ctb
    base = make_project(tmp_path)
    rc = ctb.main(["--project", "p", "--ep", "ep01", "--out-root", str(base)])
    out = capsys.readouterr().out
    assert rc == 1
    rep = json.loads((base / "directing" / "ep01" / "time_budget.json").read_text())
    c = rep["checks"]
    # script:S01 分 6s 装不下(两句台词 + 五拍动作);剧本集预算 20s 够,整集 ok
    assert c["scene_time_budget_ok"] == "FAIL" and c["episode_time_budget_ok"] == "PASS"
    s1 = next(s for s in rep["script"]["scenes"] if s["scene"] == "S01")
    assert s1["status"] == "over" and s1["lines"] == 2 and s1["dialogue_s"] > 0 and s1["action_s"] > 0
    assert s1["lines_detail"][1]["pace"] == "slow" and s1["lines_detail"][1]["pace_source"] == "line"
    assert s1["lines_detail"][1]["cpm"] == 252.0                       # 角色语速中点
    # shots:sh001 0.8s 装不下两拍;密集组 16s > 15s
    assert c["shot_time_budget_ok"] == "FAIL" and c["group_density_ok"] == "FAIL"
    assert any("grp001/sh001" in e and "shot_time_budget_ok" in e for e in rep["errors"])
    assert any("group_density_ok: grp001" in e for e in rep["errors"])
    # blocking:三拍 3.1s > 0.8s;间隔 0.1s < 0.3s;sh002 挂台词的拍不占时间但台词 + 余量 > 1.0s
    assert c["beat_budget_ok"] == "FAIL" and c["beat_spacing_ok"] == "FAIL"
    # prompt:Shot 1 六个顺序连接词 > 3 拍 + 1
    assert c["prompt_beats_bound"] == "FAIL"
    assert "prompt_beats_bound: grp001/sh001" in out
    # 存量集只 WARN
    sl = json.loads((base / "directing" / "ep01" / "shot_list.json").read_text())
    sl["_meta"]["written_at"] = "2026-09-01"
    (base / "directing" / "ep01" / "shot_list.json").write_text(json.dumps(sl, ensure_ascii=False))
    rc = ctb.main(["--project", "p", "--ep", "ep01", "--out-root", str(base), "--scope", "shots", "--no-report"])
    assert rc == 0


def test_budget_extension_and_over_cap(tmp_path, capsys):
    import check_time_budget as ctb
    base = make_project(tmp_path)
    (base / "settings.json").write_text(json.dumps({"duration": {"episode_minutes": 1, "episode_extend_pct": 50}}))
    # episode_plan 预算 6s(优先于剧本 20s)→ Σ需求 ≈ 8.5 > 6 但 ≤ 9:extend;pacing 仍按 6s → FAIL pacing_budget_adopted
    (base / "story" / "episodes" / "ep01" / "pacing.json").write_text(json.dumps({
        "duration_budget_s": 6, "total_s": 6, "scenes": [{"scene": "S01", "alloc_s": 6}, {"scene": "S02", "alloc_s": 0.5}]}))
    (base / "story" / "episode_plan.json").write_text(json.dumps({"episodes": [{"ep": "ep01", "duration_budget_s": 6}]}))
    rc = ctb.main(["--project", "p", "--ep", "ep01", "--out-root", str(base), "--scope", "script"])
    rep = json.loads((base / "story" / "episodes" / "ep01" / "time_budget.json").read_text())
    plan = rep["script"]["plan"]
    assert rc == 1 and plan["status"] == "extend" and 6 < plan["recommended_budget_s"] <= 9
    assert rep["checks"]["pacing_budget_adopted"] == "FAIL" and rep["checks"]["pacing_extension_within_cap"] == "PASS"
    # pacing 采纳加长并写 duration_policy → PASS
    s1_need = next(s for s in rep["script"]["scenes"] if s["scene"] == "S01")["need_s"]
    (base / "story" / "episodes" / "ep01" / "pacing.json").write_text(json.dumps({
        "duration_budget_s": plan["recommended_budget_s"], "total_s": plan["recommended_budget_s"],
        "duration_policy": {"kind": "time_budget_extension", "base_budget_s": 6, "extend_pct": plan["extension_pct"]},
        "scenes": [{"scene": "S01", "alloc_s": s1_need + 0.2}, {"scene": "S02", "alloc_s": plan["recommended_budget_s"] - s1_need - 0.2}]}))
    rc = ctb.main(["--project", "p", "--ep", "ep01", "--out-root", str(base), "--scope", "script", "--no-report"])
    assert rc == 0
    # 不许加长(0%)→ over_cap,给出 trim_targets 与 merge_candidates
    (base / "settings.json").write_text(json.dumps({"duration": {"episode_minutes": 1, "episode_extend_pct": 0}}))
    rc = ctb.main(["--project", "p", "--ep", "ep01", "--out-root", str(base), "--scope", "script"])
    rep = json.loads((base / "story" / "episodes" / "ep01" / "time_budget.json").read_text())
    assert rc == 1 and rep["script"]["plan"]["status"] == "over_cap"
    assert rep["script"]["trim_targets"] and rep["script"]["trim_targets"][0]["lines"]
    assert rep["script"]["merge_candidates"] and rep["script"]["merge_candidates"][0]["beats"] >= 3
    capsys.readouterr()


def test_dialogue_fit_new_vs_legacy(tmp_path, capsys):
    import check_dialogue_fit as cdf
    base = make_project(tmp_path)
    rep = cdf.run(base, "ep01", write_est=False)
    assert rep["legacy"] is False and rep["checks"]["dialogue_fit_shot"] == "FAIL"     # 0.9 + 0.7 余量 > 1.0s
    assert any("开口前后余量" in e for e in rep["errors"])
    rep = cdf.run(base, "ep01", legacy_override=True)
    assert rep["legacy"] is True and rep["checks"]["dialogue_fit_shot"] != "FAIL"
    capsys.readouterr()


def test_action_body_ignores_frozen_lines_quotes_and_native_lead():
    """#109 #110:顺序连接词只数动作散文——冻结台词、『触发词』引用、宿主【原生先入】句里的「再/先/接着」不计。"""
    import check_time_budget as ctb
    body = ("场景激活：使用 Image 2。不采用：Image 3。"
            "她抬头:{你再不许来,先去。}说到『再不许』时抬手,随后转身。\n"
            "【原生先入】本镜开场时哪吒正说到一半,上一镜画外已说出的「先去」不再重说,接着说完。\n")
    assert tc.count_sequence_connectors(ctb._action_body(body)) == 1          # 只剩「随后」
    lead = "他站定。\n【原生先入】本镜起即由李靖在画外开口:{你先去。再不许回来}——说话人不在画内,话未说完即切到下一镜。"
    assert tc.count_sequence_connectors(ctb._action_body(lead)) == 0
    assert tc.count_sequence_connectors(ctb._action_body("Not used: Image 3. He nods, then turns. Native lead: X is mid-line, then finishes.")) == 1
