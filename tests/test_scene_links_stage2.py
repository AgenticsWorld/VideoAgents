"""场间衔接二期(docs/scene_links.md,2026-10-10):导演采纳的剧本衔接登记进 shot_list transition_in.link(过场设计 propose / H3A 兜底)、
随衔接带的手段守模式口径、撤销与用户优先、契约机检 transition_link_valid、对账 script_links_landed、两侧组提示词的衔接句 scene_link_bound。
合成小项目,不依赖真实数据、不读本机生成模型配置。"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "modules"))
sys.path.insert(0, str(ROOT / "code"))
sys.path.insert(0, str(ROOT))

import check_generation_groups as cgg  # noqa: E402
from modules import motion_pairs as mpx  # noqa: E402
from modules import scene_link_prompts as slp  # noqa: E402
from modules import scene_links as sl  # noqa: E402
from modules import transition_design as td  # noqa: E402

EP = "ep01"
SCREENPLAY = """---
generated_at: 2026-10-10
---
# EP01

## S01 | INT | SCN-0001 书房 | 夜
**[事件] ev0001 | [出场] CHAR-0001 | [时长] 30s**
动作:林昭伏案画符。
动作:符纹亮成一个圆。
转场:MATCH CUT TO S02 {衔接: 形状, 出: 符纹亮成一个圆, 入: 宝德门上同一道发亮的符}

## S02 | EXT | SCN-0002 宝德门 | 日
**[事件] ev0001 | [出场] CHAR-0002 | [时长] 30s**
动作:宝德门上同一道发亮的符,门缓缓打开。
- **守门人(CHAR-0002)**:「门外是谁?」 {emotion: 警觉}
转场:CUT TO {衔接: 台词, 出: 守门人问门外是谁, 入: 门外站着的林昭}

## S03 | EXT | SCN-0003 门外 | 日
**[事件] ev0001 | [出场] CHAR-0001 | [时长] 20s**
动作:门外站着的林昭转身,向左奔出画面。
转场:CUT TO {衔接: 运动, 出: 林昭向左奔出画面, 入: 林昭自右奔入长街}

## S04 | EXT | SCN-0004 长街 | 日
**[事件] ev0001 | [出场] CHAR-0001 | [时长] 20s**
动作:林昭自右奔入长街,四下无声。
转场:SMASH CUT TO {衔接: 反差, 出: 长街四下无声, 入: 集市的喧闹}

## S05 | EXT | SCN-0005 集市 | 日
**[事件] ev0001 | [出场] CHAR-0001 | [时长] 20s**
动作:集市的喧闹扑面而来。
"""
PLAN = """## 转场清单
- 剧本衔接 S01→S02(形状匹配):采纳 —— match_cut(试验);末镜符纹特写居中,首镜门上的符同位同大。
- 剧本衔接 S02→S03(台词接力):采纳 —— hard_cut;首镜直接给门外的人。
- 剧本衔接 S03→S04(运动接力):修改 —— hard_cut;出画改成贴着画左边缘。
- 剧本衔接 S04→S05(反差硬切):弃用 —— 开场要先用字卡交代三日后。
"""
BASE_02 = {"type": "match_cut", "intent": "scene_change", "reason": "转场清单 #1:符纹接门上的符", "source": "directing_plan#转场清单/1"}


def _project(tmp_path: Path, mode: str = "classic", *, plan: str = PLAN, screenplay: str = SCREENPLAY, spatial: bool = False,
             landed_base: bool = True) -> Path:
    base = tmp_path / "proj"
    (base / "story" / "episodes" / EP).mkdir(parents=True)
    (base / "directing" / EP).mkdir(parents=True)
    (base / "bible" / "scenes").mkdir(parents=True)
    (base / "story" / "episodes" / EP / "screenplay.md").write_text(screenplay, encoding="utf-8")
    (base / "directing" / EP / "directing_plan.md").write_text(plan, encoding="utf-8")
    # prompt_skill 钉成 Seedance 2.0 写法,免得 group_kind 回落去读本机生成模型配置
    (base / "settings.json").write_text(json.dumps({
        "output": {"aspect": "16:9", **({"spatial_blocking": True} if spatial else {})},
        "transitions": {"mode": mode}, "prompt_skill": {"effective": {"skill_id": "prompt/sd20-pe"}}}), encoding="utf-8")
    (base / "bible" / "scenes" / "index.json").write_text(json.dumps({"scenes": [
        {"id": f"SCN-000{i}", "name": n} for i, n in enumerate(["书房", "宝德门", "门外", "长街", "集市"], 1)]}, ensure_ascii=False), encoding="utf-8")
    scenes = ["S01", "S01", "S02", "S03", "S04", "S05"]
    shots = [{"shot_id": f"sh{i:03d}", "scene_no": n, "size": "中景", "duration_s": 3.0} for i, n in enumerate(scenes, 1)]
    groups = [
        {"group_id": "grp001", "scene_id": "SCN-0001", "scene_no": "S01", "shots": ["sh001", "sh002"], "total_duration_s": 6, "time_of_day": "夜"},
        {"group_id": "grp002", "scene_id": "SCN-0002", "scene_no": "S02", "shots": ["sh003"], "total_duration_s": 3, "time_of_day": "日",
         **({"transition_in": dict(BASE_02)} if landed_base else {})},
        {"group_id": "grp003", "scene_id": "SCN-0003", "scene_no": "S03", "shots": ["sh004"], "total_duration_s": 3, "time_of_day": "日"},
        {"group_id": "grp004", "scene_id": "SCN-0004", "scene_no": "S04", "shots": ["sh005"], "total_duration_s": 3, "time_of_day": "日"},
        {"group_id": "grp005", "scene_id": "SCN-0005", "scene_no": "S05", "shots": ["sh006"], "total_duration_s": 3, "time_of_day": "日"},
    ]
    (base / "directing" / EP / "shot_list.json").write_text(json.dumps(
        {"episode": EP, "budget_s": 120, "shots": shots, "generation_groups": groups}, ensure_ascii=False, indent=2), encoding="utf-8")
    return base


def _tr(base: Path) -> dict:
    sl_ = json.loads((base / "directing" / EP / "shot_list.json").read_text(encoding="utf-8"))
    return {g["group_id"]: g.get("transition_in") for g in sl_["generation_groups"]}


def _row(base: Path, bid: str) -> dict:
    return next(b for b in td.load_design(base, EP)["boundaries"] if b["id"] == bid)


def _set_plan(base: Path, old: str, new: str) -> None:
    p = base / "directing" / EP / "directing_plan.md"
    text = p.read_text(encoding="utf-8")
    assert old in text
    p.write_text(text.replace(old, new), encoding="utf-8")


def _set_mode(base: Path, mode: str) -> None:
    p = base / "settings.json"
    st = json.loads(p.read_text(encoding="utf-8"))
    st["transitions"]["mode"] = mode
    p.write_text(json.dumps(st), encoding="utf-8")


def _landed(base: Path) -> dict:
    d = base / "directing" / EP
    return sl.verify_landed((base / "story" / "episodes" / EP / "screenplay.md").read_text(encoding="utf-8"),
                            (d / "directing_plan.md").read_text(encoding="utf-8"),
                            json.loads((d / "shot_list.json").read_text(encoding="utf-8")), td.load_design(base, EP))


# ---------------------------------------------------------------- 登记

def test_propose_registers_adopted_links_classic(tmp_path):
    base = _project(tmp_path, "classic")
    before = _landed(base)        # 分镜工位只落了 S01→S02 的类型;S02→S03 / S03→S04 没落
    assert not before["ok"] and sum("没有显式 transition_in" in e for e in before["errors"]) == 2
    assert any("登记卡 transition_in.link 还没写" in w for w in before["warns"])
    td.propose(base, EP)
    tr = _tr(base)
    # 分镜工位落的底子原样保留,只在上面加登记卡;形状匹配打 trial;note = 导演写的对位要求(去掉行首类型词)
    assert {k: v for k, v in tr["grp002"].items() if k != "link"} == BASE_02
    assert tr["grp002"]["link"] == {"kind": "shape", "out": "符纹亮成一个圆", "in": "宝德门上同一道发亮的符", "out_shot": "sh002", "in_shot": "sh003",
                                    "from_scene": "S01", "to_scene": "S02", "source": "screenplay",
                                    "note": "末镜符纹特写居中,首镜门上的符同位同大", "trial": True}
    # 分镜工位没落的由宿主按导演写的类型补建;经典模式不自动带声桥 / 成对运镜,只放候选
    assert tr["grp003"]["type"] == "hard_cut" and tr["grp003"]["source"] == "script_link" and tr["grp003"]["link"]["kind"] == "line"
    assert "sound_bridge" not in tr["grp003"] and "device" not in tr["grp003"]["link"]
    assert tr["grp004"]["link"]["kind"] == "motion" and "motion_pair" not in tr["grp004"]
    assert "导演修改" in tr["grp004"]["reason"] and "贴着画左边缘" in tr["grp004"]["link"]["note"]
    assert tr["grp005"] is None                                    # 导演弃用:不登记,按普通边界出建议
    r3, r4, r5 = _row(base, "B-grp002-grp003"), _row(base, "B-grp003-grp004"), _row(base, "B-grp004-grp005")
    assert (r3["status"], r3["source"]) == ("accepted", "script_link") and (r4["status"], r4["source"]) == ("accepted", "script_link")
    assert r3["alternatives"][0]["label"] == "衔接 + 声先入" and r3["alternatives"][0]["transition_in"]["sound_bridge"]["kind"] == "j"
    alt4 = r4["alternatives"][0]
    assert alt4["label"] == "衔接 + 成对运镜" and alt4["transition_in"]["motion_pair"] == {"out": "pan_left", "in": "pan_left", "speed": "medium"}
    assert any(a["label"].startswith("过场模式建议") for a in r3["alternatives"])      # 模式建议(字卡 / 定场)退到候选
    assert r5["status"] == "proposed" and "link" not in (r5["design"] or {})
    assert r5["diagnosis"]["script_link_disposition"]["disposition"] == "drop"
    # 契约、设计表机检、对账全过;重出幂等
    assert not [e for e in cgg.check_transitions(json.loads((base / "directing" / EP / "shot_list.json").read_text(encoding="utf-8")), 8.0)]
    ok, items = td.check(base, EP)
    assert ok, [i for i in items if i["result"] == "FAIL"]
    after = _landed(base)
    assert after["ok"] and not after["warns"] and [r["state"] for r in after["rows"]] == ["landed", "landed", "landed", "dropped"]
    snap = (base / "directing" / EP / "shot_list.json").read_bytes()
    td.propose(base, EP)
    assert (base / "directing" / EP / "shot_list.json").read_bytes() == snap


def test_cinematic_adds_devices_and_whitebox_guard(tmp_path):
    base = _project(tmp_path, "cinematic")
    td.propose(base, EP)
    tr = _tr(base)
    assert tr["grp003"]["sound_bridge"] == {"kind": "j", "s": 0.5, "carry": "bed"} and tr["grp003"]["link"]["device"] == "sound_bridge"
    assert tr["grp004"]["motion_pair"] == {"out": "pan_left", "in": "pan_left", "speed": "medium"} and tr["grp004"]["link"]["device"] == "motion_pair"
    assert "motion_pair" not in tr["grp002"] and "sound_bridge" not in tr["grp002"]       # 匹配类不带手段
    assert _row(base, "B-grp002-grp003")["alternatives"][0]["label"] == "衔接(不带声先入)"
    assert not cgg.check_transitions(json.loads((base / "directing" / EP / "shot_list.json").read_text(encoding="utf-8")), 10.0)
    # 换回经典:随衔接带的手段撤掉(靠 link.device 认),登记卡留下
    _set_mode(base, "classic")
    td.propose(base, EP)
    tr = _tr(base)
    assert "sound_bridge" not in tr["grp003"] and "motion_pair" not in tr["grp004"] and tr["grp004"]["link"]["kind"] == "motion"
    # 白模开启:摄影机由白模视频定,电影感也不自动带成对运镜(只在候选);声桥照带
    wb = _project(tmp_path / "wb", "cinematic", spatial=True)
    td.propose(wb, EP)
    tr = _tr(wb)
    assert "motion_pair" not in tr["grp004"] and tr["grp003"]["sound_bridge"]["kind"] == "j"
    assert _row(wb, "B-grp003-grp004")["alternatives"][0]["label"] == "衔接 + 成对运镜"


def test_minimal_mode_lands_via_sign_fallback(tmp_path):
    base = _project(tmp_path, "minimal")
    assert not td.enabled(base, EP)
    pending = td.land_script_links(base, EP)
    assert pending == ["B-grp001-grp002", "B-grp002-grp003", "B-grp003-grp004"]
    tr = _tr(base)
    assert tr["grp002"]["link"]["kind"] == "shape" and tr["grp003"]["link"]["kind"] == "line" and tr["grp004"]["link"]["kind"] == "motion"
    assert all("sound_bridge" not in tr[g] and "motion_pair" not in tr[g] for g in ("grp003", "grp004"))
    assert _row(base, "B-grp002-grp003")["alternatives"] == []          # 极简不出带手段的候选
    assert td.land_script_links(base, EP) == []                          # 都登记了:不再动
    # 没写衔接的集:不动、不新建设计表
    plain = _project(tmp_path / "plain", "minimal", screenplay=SCREENPLAY.replace(" {衔接: 形状, 出: 符纹亮成一个圆, 入: 宝德门上同一道发亮的符}", "")
                     .replace(" {衔接: 台词, 出: 守门人问门外是谁, 入: 门外站着的林昭}", "")
                     .replace(" {衔接: 运动, 出: 林昭向左奔出画面, 入: 林昭自右奔入长街}", "")
                     .replace("SMASH CUT TO {衔接: 反差, 出: 长街四下无声, 入: 集市的喧闹}", "CUT TO"), plan="## 转场清单\n全片硬切\n", landed_base=False)
    assert td.land_script_links(plain, EP) == [] and not td.design_path(plain, EP).is_file()


def test_dropped_or_removed_links_are_stripped(tmp_path):
    base = _project(tmp_path, "cinematic")
    td.propose(base, EP)
    # 导演把两条改成弃用:宿主补建的整条撤掉,分镜工位落的底子留下、只撤登记卡
    _set_plan(base, "- 剧本衔接 S02→S03(台词接力):采纳 —— hard_cut;首镜直接给门外的人。", "- 剧本衔接 S02→S03(台词接力):弃用 —— 这里要停一拍,不抢答。")
    _set_plan(base, "- 剧本衔接 S01→S02(形状匹配):采纳 —— match_cut(试验);末镜符纹特写居中,首镜门上的符同位同大。",
              "- 剧本衔接 S01→S02(形状匹配):弃用 —— 两个圆的大小对不上。")
    stale = _landed(base)
    assert not stale["ok"] and sum("留着登记卡" in e for e in stale["errors"]) == 2
    assert td.land_script_links(base, EP) == ["B-grp001-grp002", "B-grp002-grp003"]
    tr = _tr(base)
    assert tr["grp002"] == BASE_02 and tr["grp003"] is None and tr["grp004"]["link"]["kind"] == "motion"
    r3 = _row(base, "B-grp002-grp003")
    assert r3["source"] != "script_link" and "link" not in (r3["design"] or {})
    assert _row(base, "B-grp001-grp002")["source"] == "shot_list"
    ok, items = td.check(base, EP)
    assert ok, [i for i in items if i["result"] == "FAIL"]
    assert _landed(base)["ok"]
    # 剧本把运动接力的花括号删了:登记卡跟着撤
    p = base / "story" / "episodes" / EP / "screenplay.md"
    p.write_text(p.read_text(encoding="utf-8").replace(" {衔接: 运动, 出: 林昭向左奔出画面, 入: 林昭自右奔入长街}", ""), encoding="utf-8")
    td.propose(base, EP)
    assert _tr(base)["grp004"] is None


def test_user_decision_on_card_wins(tmp_path):
    base = _project(tmp_path, "classic")
    td.propose(base, EP)
    td.reject(base, EP, "B-grp002-grp003")                      # 用户在过场卡点「保持硬切」
    td.accept(base, EP, "B-grp003-grp004", alt=0)               # 用户选候选「衔接 + 成对运镜」
    td.propose(base, EP)
    tr = _tr(base)
    assert tr["grp003"]["type"] == "hard_cut" and "link" not in tr["grp003"]
    assert tr["grp004"]["motion_pair"]["out"] == "pan_left" and tr["grp004"]["link"]["kind"] == "motion"
    assert _row(base, "B-grp002-grp003")["source"] == "user" and _row(base, "B-grp003-grp004")["source"] == "user"
    assert td.land_script_links(base, EP) == []                 # 不把用户裁掉的衔接登记回去
    labels = [a["label"] for a in _row(base, "B-grp002-grp003")["alternatives"]]
    assert labels[0] == "剧本衔接(导演采纳)" and "衔接 + 声先入" in labels          # 留着回头路:候选里能选回衔接
    td.accept(base, EP, "B-grp002-grp003", alt=0)
    assert _tr(base)["grp003"]["link"]["kind"] == "line"
    res = _landed(base)
    assert res["ok"] and [r["state"] for r in res["rows"]] == ["landed", "user_decided", "user_decided", "dropped"]


# ---------------------------------------------------------------- 契约 / 对账

def test_transition_link_valid_contract():
    def errs(link=None, **t):
        g2 = {"group_id": "g2", "shots": ["s3"], "total_duration_s": 4,
              "transition_in": {"type": "hard_cut", "intent": "scene_change", "reason": "r",
                                "link": {"kind": "line", "out": "甲", "in": "乙", "out_shot": "s2", "in_shot": "s3", **(link or {})}, **t}}
        sl_ = {"budget_s": 100, "generation_groups": [{"group_id": "g1", "shots": ["s1", "s2"], "total_duration_s": 4}, g2]}
        return " | ".join(e for e in cgg.check_transitions(sl_, 8.0) if "transition_link_valid" in e)

    assert errs() == ""
    assert "不在白名单" in errs({"kind": "magic"})
    assert "link.out 为空" in errs({"out": " "})
    assert "只能" in errs(type="dip_black", duration_s=0.8)
    assert "不得与 inserts / hold_s 并用" in errs(hold_s=1.0)
    assert "不是前组 g1 的最后一镜 s2" in errs({"out_shot": "s1"})
    assert "不是本组第一镜 s3" in errs({"in_shot": "s9"})
    assert "须写 reason" in errs(reason="")
    first = {"budget_s": 100, "generation_groups": [{"group_id": "g1", "shots": ["s1"], "total_duration_s": 4, "transition_in": {
        "type": "hard_cut", "reason": "r", "link": {"kind": "line", "out": "甲", "in": "乙"}}}]}
    assert any("首组无前组,不得 link" in e for e in cgg.check_transitions(first, 8.0))


def test_dispositions_must_land_on_a_direct_join():
    res = sl.verify_dispositions(SCREENPLAY, PLAN.replace("采纳 —— hard_cut;首镜直接给门外的人。", "采纳 —— dip_black;黑一下再给门外的人。"))
    assert not res["ok"] and any("落到 dip_black" in e and "弃用" in e for e in res["errors"])
    assert sl.verify_dispositions(SCREENPLAY, PLAN)["ok"]


def test_verify_landed_states(tmp_path):
    base = _project(tmp_path, "classic")
    d = base / "directing" / EP
    shot_list = json.loads((d / "shot_list.json").read_text(encoding="utf-8"))
    assert sl.verify_landed(SCREENPLAY, PLAN, None)["ok"]                      # 尚无 shot_list:不适用
    by = {g["group_id"]: g for g in shot_list["generation_groups"]}
    by["grp002"]["transition_in"]["type"] = "smash_cut"                         # 与导演写的 match_cut 不一致
    by["grp003"]["transition_in"] = {"type": "hard_cut", "intent": "scene_change", "reason": "转场清单 #2", "source": "directing_plan#转场清单/2",
                                     "link": {"kind": "line", "out": "旧的问话", "in": "门外站着的林昭"}}   # 登记卡过期
    del shot_list["generation_groups"][3]                                       # S04 整场没有分组
    res = sl.verify_landed(SCREENPLAY, PLAN, shot_list)
    states = {r["from"]: r["state"] for r in res["rows"]}
    assert states == {"S01": "type_mismatch", "S02": "stale", "S03": "no_boundary", "S04": "dropped"}
    assert not res["ok"] and len(res["errors"]) == 3


# ---------------------------------------------------------------- 提示词衔接句

def _prompts(base: Path, bodies: dict) -> None:
    d = base / "assets" / "prompts" / EP
    d.mkdir(parents=True, exist_ok=True)
    for gid, vp in bodies.items():
        (d / f"{gid}.json").write_text(json.dumps({"group_id": gid, "video_prompt": vp}, ensure_ascii=False, indent=2), encoding="utf-8")


def _vp(base: Path, gid: str) -> str:
    return json.loads((base / "assets" / "prompts" / EP / f"{gid}.json").read_text(encoding="utf-8"))["video_prompt"]


BODIES = {"grp001": "Shot 1: 林昭伏案画符。\n\nShot 2: 符纹逐渐亮起。\n\nGlobal constraints: no background music.",
          "grp002": "Shot 1: 门上的符发亮,守门人问话。", "grp003": "Shot 1: 门外的林昭转身奔出。",
          "grp004": "Shot 1: 林昭奔入长街。", "grp005": "Shot 1: 集市喧闹。"}


def test_sentences_cover_every_kind_and_only_name_own_side():
    for kind in sl.KINDS:
        for zh in (True, False):
            o, i = slp.sentences({"kind": kind, "out": "甲方的内容。", "in": "乙方的内容"}, zh)
            for s, own, other in ((o, "甲方的内容", "乙方的内容"), (i, "乙方的内容", "甲方的内容")):
                assert s.startswith(slp.MARK_ZH if zh else slp.MARK_EN) and s.endswith("。" if zh else ".")
                assert own in s and other not in s and "甲方的内容。" not in s        # 每句只写本镜自己的内容;内容句末标点去掉
                assert [m.strip() for m in slp.MARK_RE.findall("Shot 1: 正文。" + s)] == [s]
    long = slp.sentences({"kind": "sound", "out": "钟" * 200, "in": "铃"}, True)[0]
    assert "钟" * slp.TEXT_MAX not in long and "…" in long


def test_sync_writes_sentences_idempotently(tmp_path):
    base = _project(tmp_path, "classic")
    td.propose(base, EP)
    _prompts(base, BODIES)
    res = slp.sync_episode(base, EP, write=False, zh=True)
    assert len(res["pairs"]) == 3 and len(res["errors"]) == 6 and all("scene_link_bound" in e for e in res["errors"])
    res = slp.sync_episode(base, EP, write=True, zh=True)
    assert not res["errors"] and res["updated_prompts"] == ["grp001", "grp002", "grp003", "grp004"]
    g1, g2 = _vp(base, "grp001"), _vp(base, "grp002")
    out1, in2 = slp.sentences(_tr(base)["grp002"]["link"], True)
    # 前组:句子在最后一个 Shot 段里、Global constraints 之前;本组:首镜起点句在前、尾镜落点句在后(单镜组两句同段)
    assert g1.index("Shot 2:") < g1.index(out1) < g1.index("Global constraints:") and "【场间衔接】" not in g1[:g1.index("Shot 2:")]
    out2 = slp.sentences(_tr(base)["grp003"]["link"], True)[0]
    assert g2.index(in2) < g2.index(out2) and "宝德门上同一道发亮的符" in in2 and "符纹亮成一个圆" not in in2
    assert _vp(base, "grp005") == BODIES["grp005"]                    # 弃用的那条边界两侧不写
    snap = {g: _vp(base, g) for g in BODIES}
    again = slp.sync_episode(base, EP, write=True, zh=True)
    assert not again["errors"] and again["updated_prompts"] == [] and snap == {g: _vp(base, g) for g in BODIES}
    # 界面语言换成英文:整句替换,不留中文残句
    en = slp.sync_episode(base, EP, write=True, zh=False)
    assert not en["errors"] and "Scene link:" in _vp(base, "grp002") and "【场间衔接】" not in _vp(base, "grp002")
    slp.sync_episode(base, EP, write=True, zh=True)
    # 登记卡撤了(导演改弃用 → propose):不写只核会报残留,--write 剔干净
    _set_plan(base, "- 剧本衔接 S03→S04(运动接力):修改 —— hard_cut;出画改成贴着画左边缘。", "- 剧本衔接 S03→S04(运动接力):弃用 —— 这场不追。")
    td.propose(base, EP)
    res = slp.sync_episode(base, EP, write=False, zh=True)
    assert any("grp004" in e and "衔接已撤" in e for e in res["errors"]) and any("grp003" in e and "残留过期" in e for e in res["errors"])
    res = slp.sync_episode(base, EP, write=True, zh=True)
    assert not res["errors"] and _vp(base, "grp004") == BODIES["grp004"]


def test_sync_coexists_with_motion_pair_sentences(tmp_path):
    base = _project(tmp_path, "cinematic")
    td.propose(base, EP)                                  # S03→S04 运动接力:电影感自动带成对运镜
    _prompts(base, BODIES)
    # 先写运镜句再写衔接句 / 反过来,结果都是「衔接句在前、运镜句在后」,两边各自剔除互不误伤
    assert not mpx.sync_episode(base, EP, write=True, zh=True)["errors"]
    assert not slp.sync_episode(base, EP, write=True, zh=True)["errors"]
    g3 = _vp(base, "grp003")
    assert g3.index("【场间衔接】") < g3.index("【运镜对接】")
    assert "【场间衔接】" in mpx.strip_marks(g3) and "【运镜对接】" in slp.strip_marks(g3)
    assert not mpx.sync_episode(base, EP, write=True, zh=True)["errors"] and not slp.sync_episode(base, EP, write=False, zh=True)["errors"]
    assert not mpx.sync_episode(base, EP, write=False, zh=True)["errors"]
    _prompts(base, BODIES)
    assert not slp.sync_episode(base, EP, write=True, zh=True)["errors"]
    assert not mpx.sync_episode(base, EP, write=True, zh=True)["errors"]
    assert _vp(base, "grp003").index("【场间衔接】") < _vp(base, "grp003").index("【运镜对接】")
    assert not slp.sync_episode(base, EP, write=False, zh=True)["errors"]


def test_cli_sync_and_landed_reconciliation(tmp_path):
    base = _project(tmp_path, "classic")
    _prompts(base, BODIES)
    cli = [sys.executable, str(ROOT / "code" / "sync_scene_links.py"), "--out-root", str(base), "--ep", EP]
    env = {**__import__("os").environ, "VIDEOAGENTS_UI_LANG": "zh"}
    r = subprocess.run(cli, capture_output=True, text=True, cwd=ROOT, env=env)     # 导演采纳了但还没登记:对账违规
    assert r.returncode == 1 and "VIOLATION script_links_landed" in r.stdout and "登记卡" in r.stdout
    td.propose(base, EP)
    r = subprocess.run(cli, capture_output=True, text=True, cwd=ROOT, env=env)
    assert r.returncode == 1 and "缺尾镜落点句" in r.stdout and "script_links_landed" not in r.stdout
    r = subprocess.run(cli + ["--write"], capture_output=True, text=True, cwd=ROOT, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "LINK grp001 → grp002: 形状匹配" in r.stdout and "-> PASS" in r.stdout
    r = subprocess.run([sys.executable, str(ROOT / "code" / "check_scene_links.py"), "--out-root", str(base), "--ep", EP, "--json"],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0 and json.loads(r.stdout)["checks"] == {"script_links_disposed": True, "script_links_landed": True}
