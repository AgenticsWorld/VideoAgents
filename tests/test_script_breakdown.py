"""剧本拆解表:推导解析器(多种剧本排版)、正式产物加载/过期判定、机检与预览接口。"""
import json
from pathlib import Path

import pytest

from modules import script_breakdown as sb


DZG_STYLE = """# EP01《谶语初现》

> **本集看点**:一句谶语把年轻教师推到了机场。
>
> - **集号**:ep01 | **时长预算**:600s
> - **叙述人称**:第一人称旁白 = 王三合(CHAR-0001)

## S01 | 外 | SCN-0075 城郊马路 | 黄昏

**[事件] ev00101(前段) | [出场] CHAR-0001、CHAR-0002 | [时长] 33s**

### 【画面/动作】

夕阳压在楼群的边线上。王三合踢着石子往前走。

### 【旁白】

**王三合(V.O.)**:我刚出生时,有位道士给我批过命。

### 【对白】

- **老道儿(CHAR-0002)**:施主,请留步! {emotion: 洪亮/召唤, est_duration_s: 1.6, style_hits: ["c1:施主"]}
- **王三合(CHAR-0001)**(四下张望):叫我? {emotion: 疑惑, est_duration_s: 0.5, style_hits: []}
- **约束**:这一行不是台词。

**转场**:CUT TO

## S02 | INT | scene:SCN-0001 | 夜
[出场:CHAR-0001, CHAR-0004]
动作:王三合推门回家。
CHAR-0004:「回来啦?」 {emotion: 平静, est_duration_s: 1.0}
转场:CUT TO
"""

PIPE_STYLE = """# ep01 剧本
### S01 ｜ EXT ｜ SCN-001 山路(荒山) · 日 ｜ 42s ｜ beat 1 ｜ ev002
〔出场:CHAR-0001、CHAR-0003(尸体,无行动)〕
〔本场无对白〕
动作:「我」拎着断腿走到拖车旁。〔证据镜①〕
〔旁白候选(CH-A 男声)〕
- 干草下面是极其重要的货物。〔立点①〕
## 1-2 日 内 神庙大厅
- 在场:CHAR-0001 章墨、CHAR-0002 章小满
△ 章墨从弯道后探上来。
[LN-ep01-01] 章墨(CHAR-0001)〔OV〕：我叫章墨,一个音乐生。
[LN-ep01-02] 章墨(CHAR-0001)〔对白〕(擦汗)：小满,我们到了。
"""

NARR_MD = """# EP01 旁白稿
[N-01 | anchor: S01开场·踢石子 | est_duration_s: 9.5 | source: ch001#p009 | tone: 平实]
我刚出生,道士就说我仙缘深厚。

[N-02 | anchor: SCN-0001 | est_duration_s: 4.2]
他连钱都没提。
"""

PACING = {"duration_budget_s": 600, "total_s": 621,
          "scenes": [{"scene": "S01", "alloc_s": 33, "start_s": 0, "end_s": 33, "dialogue_est_s": 16.4,
                      "narration_est_s": 9.5, "silent_s": 7.1, "emotion": {"type": "倦怠", "intensity": 0.35},
                      "tempo": "中(对白密度最高)", "narration_ids": ["N-01"]},
                     {"scene": "S02", "alloc_s": 70, "start_s": 33, "end_s": 103, "emotion": {"type": "交锋", "intensity": 0.7}, "tempo": "快"},
                     {"scene": "EC", "title": "片尾钩子", "alloc_s": 15, "start_s": 606, "end_s": 621, "emotion": {"type": "收束", "intensity": 0.7}}],
          "emotion_curve": {"shape": "低起→峰", "points": [{"unit": "S01", "t_s": 16, "intensity": 0.35}, {"unit": "S02", "t_s": 60, "intensity": 0.7}]},
          "trim_suggestions": [{"scene": "S02", "save_s": 4.8, "how": "删重复句", "risk": "low"}]}
HOOKS = {"opening_hooks": [{"id": "oh-1", "type": "cold_open", "copy": "施主,请留步!", "position_anchor": {"scene": "S01"}, "est_duration_s": 4}],
         "mid_hooks": [{"id": "mh-1", "type": "foreshadow", "unit": "S02"}],
         "ending_cliffhangers": [{"id": "ec-1", "type": "text_card", "scene": "EC"}],
         "selected": {"opening": None, "ending": "ec-1"}, "agent_recommendation": {"opening": "oh-1"}}


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def project(tmp_path):
    base = tmp_path / "demo"
    write(base / "story/episodes/ep01/screenplay.md", DZG_STYLE)
    write(base / "story/episodes/ep01/narration.md", NARR_MD)
    write(base / "story/episodes/ep01/pacing.json", PACING)
    write(base / "story/episodes/ep01/hooks.json", HOOKS)
    write(base / "story/episode_plan.json", {"episodes": [{"episode_id": "ep01", "title": "谶语初现", "duration_budget_s": 600,
                                                            "chapter_range": {"start": "ch001", "end": "ch011"},
                                                            "events": ["ev00101"], "beats": ["老者拦路"], "characters": ["CHAR-0001"]}]})
    write(base / "story/events.json", {"events": [{"id": "ev00101", "chapter": "ch001", "location": "路上", "characters": ["王三合"],
                                                     "process": "老者拦路", "importance": "major"}]})
    write(base / "story/story_graph.json", {"acts": [{"id": "act1", "title": "机缘", "chapters": ["ch001", "ch011"], "desc": "拜师"},
                                                      {"id": "act2", "title": "历练", "chapters": ["ch012", "ch290"]}]})
    write(base / "bible/characters/index.json", {"characters": [{"id": "CHAR-0001", "name": "王三合", "tier_cn": "主角"},
                                                                 {"id": "CHAR-0002", "name": "老道儿"}, {"id": "CHAR-0004", "name": "我妈"}]})
    write(base / "bible/scenes/index.json", {"scenes": [{"id": "SCN-0075", "name": "城郊马路"}, {"id": "SCN-0001", "name": "家中"}]})
    return base


def test_parse_dzg_style_screenplay():
    sp = sb.parse_screenplay(DZG_STYLE)
    assert sp["budget_s"] == 600 and sp["pov"].startswith("第一人称") and "谶语" in sp["logline"]
    assert [s["no"] for s in sp["scenes"]] == ["S01", "S02"]
    s1, s2 = sp["scenes"]
    assert s1["scene_id"] == "SCN-0075" and s1["scene_name"] == "城郊马路" and s1["int_ext"] == "EXT" and s1["time_of_day"] == "黄昏"
    assert s1["events"] == ["ev00101"] and s1["cast"] == ["CHAR-0001", "CHAR-0002"] and s1["alloc_s"] == 33
    assert [d["speaker_id"] for d in s1["dialogue"]] == ["CHAR-0002", "CHAR-0001"]     # 「约束」说明行不是台词
    assert s1["dialogue"][0]["est_s"] == 1.6 and s1["dialogue"][0]["emotion"] == "洪亮/召唤" and s1["dialogue"][0]["style_hits"] == ["c1:施主"]
    assert s1["dialogue"][1]["paren"] == "四下张望" and s1["dialogue"][1]["text"] == "叫我?"
    assert s1["narration_candidates"][0]["text"].startswith("我刚出生") and s1["transition"] == "CUT TO"
    assert "夕阳" in " ".join(s1["action"])
    assert s2["scene_id"] == "SCN-0001" and s2["int_ext"] == "INT" and s2["time_of_day"] == "夜" and s2["cast"] == ["CHAR-0001", "CHAR-0004"]
    assert s2["dialogue"][0]["speaker_id"] == "CHAR-0004" and s2["dialogue"][0]["text"] == "回来啦?"


def test_parse_pipe_and_offer_styles():
    sp = sb.parse_screenplay(PIPE_STYLE)
    assert [s["no"] for s in sp["scenes"]] == ["S01", "1-2"]
    s1, s2 = sp["scenes"]
    assert s1["scene_id"] == "SCN-001" and s1["scene_name"] == "山路(荒山)" and s1["time_of_day"] == "日" and s1["alloc_s"] == 42
    assert s1["events"] == ["ev002"] and s1["cast"] == ["CHAR-0001", "CHAR-0003"] and not s1["dialogue"]
    assert s1["narration_candidates"][0]["text"] == "干草下面是极其重要的货物。"
    assert "证据镜" not in " ".join(s1["action"])
    assert s2["int_ext"] == "INT" and s2["time_of_day"] == "日" and s2["scene_name"] == "神庙大厅"
    assert s2["cast"] == ["CHAR-0001", "CHAR-0002"] and "章墨从弯道" in s2["action"][0]
    assert len(s2["dialogue"]) == 1 and s2["dialogue"][0]["id"] == "LN-ep01-02" and s2["dialogue"][0]["paren"] == "擦汗"
    assert s2["narration_candidates"][0]["speaker_id"] == "CHAR-0001"      # 〔OV〕归旁白候选


def test_derive_merges_pacing_hooks_narration_plan(project):
    bd = sb.derive(project, "ep01")
    assert bd["source"] == "derived" and bd["title"] == "谶语初现" and bd["duration_budget_s"] == 600 and bd["total_est_s"] == 621
    nos = [s["no"] for s in bd["scenes"]]
    assert nos == ["S01", "S02", "EC"]                      # pacing 独有单元按 start_s 排在末尾
    s1 = bd["scenes"][0]
    assert s1["alloc_s"] == 33 and s1["alloc_source"] == "pacing" and s1["dialogue_s"] == 16.4 and s1["silent_s"] == 7.1
    assert s1["emotion"] == {"type": "倦怠", "intensity": 0.35} and s1["tempo"] == "medium" and s1["hooks"] == ["oh-1"]
    assert s1["narration_ids"] == ["N-01"] and s1["dialogue"][0]["speaker_name"] == "老道儿"
    s2 = bd["scenes"][1]
    assert s2["tempo"] == "fast" and s2["dialogue_s"] == 1.0 and s2["silent_s"] == 69.0 - 4.2 or s2["narration_s"] == 4.2
    assert bd["scenes"][2]["pacing_only"] and bd["scenes"][2]["hooks"] == ["ec-1"]
    narr = {n["id"]: n for n in bd["narration"]}
    assert narr["N-01"]["scene"] == "S01" and narr["N-01"]["tone"] == "平实"
    assert narr["N-02"]["scene"] == "S02"                    # SCN-0001 锚点映射到场次
    cast = {c["id"]: c for c in bd["cast"]}
    assert cast["CHAR-0002"]["lines"] == 1 and cast["CHAR-0002"]["dialogue_s"] == 1.6 and cast["CHAR-0001"]["role"] == "主角"
    assert cast["CHAR-0004"]["scenes"] == ["S02"]
    assert bd["hooks"]["selected"]["ending"] == "ec-1" and bd["hooks"]["recommendation"]["opening"] == "oh-1"
    assert bd["emotion_curve"][0]["scene"] == "S01" and bd["curve_shape"] == "低起→峰"
    assert bd["trim_suggestions"][0]["save_s"] == 4.8
    assert bd["plan"]["chapter_range"]["start"] == "ch001" and bd["events"][0]["id"] == "ev00101"
    assert bd["structure"]["current_act"] == "act1"
    assert bd["totals"]["lines"] == 3 and bd["totals"]["narration_items"] == 2


def test_load_prefers_agent_file_and_flags_stale(project):
    res = sb.load(project, "ep01")
    assert res["source"] == "derived" and res["file"] is None
    agent = {"schema_version": sb.SCHEMA_VERSION, "ep": "ep01", "title": "正式版",
             "scenes": [{"no": "S01", "summary": "老者拦路", "beat": "开场钩", "emotion": {"type": "倦怠", "intensity": 0.35},
                         "dialogue": [{"speaker": "CHAR-0002", "text": "施主,请留步!", "est_s": 1.6}]}]}
    path = sb.breakdown_path(project, "ep01")
    write(path, agent)
    import os, time
    future = time.time() + 5
    os.utime(path, (future, future))
    res = sb.load(project, "ep01")
    assert res["source"] == "agent" and res["stale"] == [] and res["errors"] == []
    bd = res["breakdown"]
    assert bd["title"] == "正式版" and [s["no"] for s in bd["scenes"]] == ["S01"]
    assert bd["plan"]["title"] == "谶语初现"                 # 正式表缺的块由推导视图补齐
    old = time.time() - 100
    os.utime(path, (old, old))
    res = sb.load(project, "ep01")
    assert "story/episodes/ep01/screenplay.md" in res["stale"]
    write(path, "{not json")
    res = sb.load(project, "ep01")
    assert res["source"] == "derived" and res["errors"]


def test_validate_reports_schema_errors(project):
    errors, warns = sb.validate({"schema_version": "x", "ep": "ep02", "scenes": [
        {"no": "S01", "emotion": {"intensity": 1.5}, "tempo": "快", "alloc_s": -1, "dialogue": [{"text": "hi"}]},
        {"no": "S01"}, {"no": "S03", "scene_id": "SCN-9999", "cast": ["CHAR-9999"], "summary": "x"}],
        "narration": [{"id": "N-01", "scene": "S09", "text": "t"}], "emotion_curve": [{"intensity": 2}],
        "duration_budget_s": 600, "total_est_s": 800}, base=project, ep="ep01")
    joined = "\n".join(errors)
    for frag in ("schema_version", "ep 须为 ep01", "intensity 须在 0–1", "tempo 须为", "alloc_s 须为非负数", "缺 speaker", "场次 S01 重复", "emotion_curve"):
        assert frag in joined, frag
    wj = "\n".join(warns)
    assert "SCN-9999" in wj and "CHAR-9999" in wj and "S09" in wj and "偏离预算" in wj
    ok_errors, _ = sb.validate(sb.derive(project, "ep01"), base=project, ep="ep01")
    assert ok_errors == []


def test_check_cli_and_preview_api(project, monkeypatch):
    import subprocess, sys
    root = Path(__file__).resolve().parents[1]
    cli = [sys.executable, str(root / "code/check_script_breakdown.py"), "--project", "demo", "--ep", "ep01", "--out-root", str(project)]
    assert subprocess.run(cli, capture_output=True, text=True).returncode == 2       # 缺文件
    bd = sb.derive(project, "ep01")
    bd["source"] = "agent"
    write(sb.breakdown_path(project, "ep01"), bd)
    r = subprocess.run(cli + ["--json"], capture_output=True, text=True)
    out = json.loads(r.stdout)      # 2026-10-09 起新拆解表每场须有 NPC 参与构图判定(npc_judged)
    assert r.returncode == 1 and any("npc_judged" in e for e in out["errors"]), r.stdout + r.stderr
    for sc in bd["scenes"]:
        if not sc.get("pacing_only"):
            sc["npc"] = {"on": False, "density": None, "reason": "测试"}
    write(sb.breakdown_path(project, "ep01"), bd)
    r = subprocess.run(cli + ["--json"], capture_output=True, text=True)
    out = json.loads(r.stdout)
    assert r.returncode == 0 and out["pass"] and out["scenes"] == 3, r.stdout + r.stderr
    from services.runtime import core
    monkeypatch.setattr(core, "PROJECTS_DIR", project.parent)
    d = core._preview_script("demo", "")
    assert d["ep"] == "ep01" and d["source"] == "agent" and d["episodes"][0]["has_breakdown"] is True
    assert d["agents"]["dialogue"] == "01-story/dialogue-rewrite" and d["breakdown"]["scenes"][0]["no"] == "S01"
    assert d["reanalyze_run"] is None
    sbd = core._preview_storyboard("demo", "ep01")
    assert "screenplay" not in sbd and "narration" not in sbd and "narration_items" in sbd


def test_blocks_follow_script_order_and_merge_final_narration(project):
    sp = sb.parse_screenplay(DZG_STYLE)
    assert [b["type"] for b in sp["scenes"][0]["blocks"]] == ["action", "narration", "dialogue", "transition"]
    assert len(sp["scenes"][0]["blocks"][2]["lines"]) == 2
    bd = sb.derive(project, "ep01")
    b = bd["scenes"][0]["blocks"]
    assert [x["type"] for x in b] == ["action", "narration", "dialogue", "transition"]
    assert b[1]["id"] == "N-01" and b[1]["final"] is True and b[1]["text"].startswith("我刚出生,道士")   # 候选被定稿替换
    assert b[2]["lines"][0]["id"] == "S01-D01" and b[2]["lines"][1]["speaker_name"] == "王三合"
    s2 = bd["scenes"][1]["blocks"]
    assert s2[-2]["type"] == "narration" and s2[-2]["id"] == "N-02"      # 场尾补上未对上的定稿条目
    assert bd["scenes"][2]["blocks"] == []                                   # 仅节奏表单元无旁白时空块
    errors, _ = sb.validate({"schema_version": sb.SCHEMA_VERSION, "ep": "ep01",
                             "scenes": [{"no": "S01", "summary": "x", "blocks": [{"type": "x"}, {"type": "dialogue"}, {"type": "action"}]}]})
    assert sum("blocks[" in e for e in errors) == 3


EN_ANCHORS = """# Episode 1 — The Well

- Duration budget: 180s
- Logline: A boy finds a talking well.
- POV: third person

## S01 | EXT. | SCN-0075 Village well | dusk
**[EVENTS] ev0001, ev0002 (merged) | [CAST] CHAR-0001, CHAR-0002 | [DURATION] 40s**
ACTION: Wang crosses the square. The well hums.
CHAR-0001: "Who's there?"
- **Old Man (CHAR-0002)**(coughing): "Nobody you'd know." {emotion: wary, est_duration_s: 2.4}
[NARRATION (narrator)]: Three years had passed since the drought.
SFX: wind through dry reeds
TRANSITION: CUT TO
(adaptation_note: merged ch01 two beats; source: ch01#p04)

## S02 | INTERIOR | SCN-0012 | night
**[EVENTS] ev0003 | [CAST] CHAR-0001 | [DURATION] 25s**
[NO DIALOGUE]
△ Wang lights a candle.
Old Man: "Sleep."
FADE OUT.
"""


def test_parse_english_anchor_screenplay():
    """剧本机器锚点契约(docs/screenplay_anchors.md):英文规范写法与中文旧写法等价,英文剧本不再解析成空。"""
    sp = sb.parse_screenplay(EN_ANCHORS, {"Old Man"})
    assert sp["title"] == "Episode 1 — The Well" and sp["budget_s"] == 180.0
    assert sp["logline"] == "A boy finds a talking well." and sp["pov"] == "third person"
    assert [s["no"] for s in sp["scenes"]] == ["S01", "S02"]
    a, b = sp["scenes"]
    assert a["int_ext"] == "EXT" and a["scene_id"] == "SCN-0075" and a["scene_name"] == "Village well" and a["time_of_day"] == "dusk"
    assert a["events"] == ["ev0001", "ev0002"] and a["cast"] == ["CHAR-0001", "CHAR-0002"] and a["alloc_s"] == 40.0
    assert a["action"] == ["Wang crosses the square. The well hums."]
    assert [(d["speaker_id"], d["text"]) for d in a["dialogue"]] == [("CHAR-0001", "Who's there?"), ("CHAR-0002", "Nobody you'd know.")]
    assert a["dialogue"][1]["paren"] == "coughing" and a["dialogue"][1]["emotion"] == "wary" and a["dialogue"][1]["est_s"] == 2.4
    assert a["narration_candidates"] == [{"speaker": "narrator", "text": "Three years had passed since the drought."}]
    assert a["sound"] == ["wind through dry reeds"] and a["transition"] == "CUT TO"
    assert a["notes"] == ["merged ch01 two beats; source: ch01#p04"]
    assert [x["type"] for x in a["blocks"]] == ["action", "dialogue", "narration", "sound", "transition"]
    assert b["int_ext"] == "INT" and b["time_of_day"] == "night" and b["events"] == ["ev0003"] and b["alloc_s"] == 25.0
    assert b["action"] == ["Wang lights a candle."] and b["transition"] == "FADE OUT"
    assert [(d["speaker_name"], d["text"]) for d in b["dialogue"]] == [("Old Man", "Sleep.")]


def test_english_int_ext_aliases_and_inference():
    from modules import scene_int_ext as ie
    assert ie.norm("Interior") == "INT" and ie.norm("EXT.") == "EXT" and ie.norm("int./ext.") == "INT/EXT"
    assert ie.infer({"name": "Village square"}) == "EXT" and ie.infer({"name": "Throne hall"}) == "INT"
    assert ie.infer({"name": "Mushroom"}) is None          # room 是子串,不按整词命中
