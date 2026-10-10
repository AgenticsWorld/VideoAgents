"""场间衔接初稿(docs/scene_links.md,2026-10-10 一期):剧本场尾转场行的 {衔接, 出, 入} 解析、剧本侧机检 scene_link_valid、
导演处置机检 script_links_disposed、剧本拆解表 link_out、过场设计诊断读取。合成小项目,不依赖真实数据。"""
import json
import subprocess
import sys
from pathlib import Path

from modules import episode_treatments as et
from modules import scene_links as sl
from modules import script_breakdown as sb

REPO = Path(__file__).resolve().parents[1]
NEW = "---\ngenerated_at: 2026-10-10\n---\n"


def _sp(tail1="转场:CUT TO", tail2="转场:CUT TO", s1_extra="", s2_first="动作:宝德门上同一道发亮的符,门缓缓打开。", fm=NEW):
    return (fm + "# EP01\n\n"
            "## S01 | INT | SCN-0001 书房 | 夜\n**[事件] ev0001 | [出场] CHAR-0001 | [时长] 30s**\n"
            "动作:林昭伏案画符。\nCHAR-0001:「成了。」\n动作:符纹亮成一个圆。\n" + s1_extra + tail1 + "\n\n"
            "## S02 | EXT | SCN-0002 宝德门 | 日\n**[事件] ev0001 | [出场] CHAR-0002 | [时长] 30s**\n"
            + s2_first + "\n" + tail2 + "\n\n"
            "## S03 | EXT | SCN-0003 集市 | 日\n**[事件] ev0001 | [出场] CHAR-0002 | [时长] 20s**\n"
            "动作:集市的喧闹扑面而来。\n")


SHAPE = "转场:MATCH CUT TO S02 {衔接: 形状, 出: 符纹亮成一个圆, 入: 宝德门上同一道发亮的符}"


# ---------------------------------------------------------------- 解析

def test_transition_line_forms():
    for line in ("转场:CUT TO", "转场：CUT TO", "- 转场:CUT TO", "**转场**:CUT TO", "- **转场**:**CUT TO**", "TRANSITION: CUT TO"):
        assert sl.transition_line(line) == "CUT TO", line
    assert sl.transition_line("CUT TO:") == "CUT TO"
    assert sl.transition_line("FADE OUT.") == "FADE OUT"
    assert sl.transition_line("SMASH CUT TO") == "SMASH CUT TO"
    assert sl.transition_line("MATCH CUT TO S04: {link: shape, out: a ring, in: a moon}") == "MATCH CUT TO S04 {link: shape, out: a ring, in: a moon}"
    for line in ("动作:他走了。", "CHAR-0001:「CUT TO」", "CUT TO the chase and run", ""):
        assert sl.transition_line(line) is None, line


def test_parse_transition_braces_zh_en():
    p = sl.parse_transition("MATCH CUT TO S04 {衔接: 形状, 出: 符纹亮成一个圆,越来越亮, 入: 宝德门上同一道发亮的符}")
    assert p["cut"] == "MATCH CUT TO S04" and p["target"] == "S04" and p["designed_word"]
    assert p["link"] == {"kind": "shape", "kind_raw": "形状", "out": "符纹亮成一个圆,越来越亮", "in": "宝德门上同一道发亮的符"}
    p = sl.parse_transition("SMASH CUT TO {link: contrast; out: silence, one candle; in: a market roar}")
    assert p["target"] is None and p["link"]["kind"] == "contrast"
    assert p["link"]["out"] == "silence, one candle" and p["link"]["in"] == "a market roar"
    # 全角冒号 / 逗号同样认;中文类型别名归一
    p = sl.parse_transition("CUT TO {衔接：台词接力，出：谁在门外，入：门外站着的人}")
    assert p["link"] == {"kind": "line", "kind_raw": "台词接力", "out": "谁在门外", "in": "门外站着的人"}
    # 没有花括号 = 普通转场;括注里的场次号不算点名
    p = sl.parse_transition("CUT TO(自圣像眼睑推入,接 S04 同一圣像的殿内)")
    assert p["link"] is None and p["target"] is None and not p["brace"]
    assert sl.parse_transition("MATCH CUT TO")["designed_word"] and sl.parse_transition("MATCH CUT TO")["link"] is None
    # 花括号里一个键都认不出 = 不是衔接
    assert sl.parse_transition("CUT TO {备注: 接下一场}")["link"] is None
    # 类型认不出时 kind=None,留给机检报
    assert sl.parse_transition("CUT TO {衔接: 魔术, 出: 甲, 入: 乙}")["link"]["kind"] is None
    assert set(sl.KINDS) == {"shape", "action", "position", "motion", "sound", "line", "contrast"}
    assert [k for k, v in sl.KINDS.items() if v["tier"] == "trial"] == ["shape", "action"]


def test_links_and_rows():
    text = _sp(tail1=SHAPE)
    rows = sl.scene_rows(text)
    assert [r["no"] for r in rows] == ["S01", "S02", "S03"] and rows[0]["next_no"] == "S02" and rows[2]["next_no"] is None
    assert rows[0]["header"].startswith("## S01 | INT | SCN-0001") and rows[0]["at_tail"] and rows[0]["has_spoken"]
    assert rows[2]["transition"] is None
    lk = sl.links(text)
    assert lk == [{"from": "S01", "to": "S02", "kind": "shape", "tier": "trial", "out": "符纹亮成一个圆", "in": "宝德门上同一道发亮的符",
                   "cut": "MATCH CUT TO S02", "label_zh": "形状匹配", "label_en": "Shape match", "land": "match_cut"}]
    assert sl.links(_sp()) == []


# ---------------------------------------------------------------- scene_link_valid

def test_verify_valid_and_plain_scripts_pass():
    res = sl.verify(_sp(tail1=SHAPE))
    assert res["ok"] and not res["errors"] and not res["warns"] and len(res["links"]) == 1 and not res["legacy"]
    plain = sl.verify(_sp())
    assert plain["ok"] and not plain["errors"] and not plain["warns"] and plain["links"] == []


def test_verify_failures():
    def errs(**kw):
        res = sl.verify(_sp(**kw))
        assert not res["ok"]
        return " | ".join(res["errors"])

    assert "不在白名单" in errs(tail1="转场:CUT TO {衔接: 魔术, 出: 符纹, 入: 门上的符}")
    assert "缺「入」" in errs(tail1="转场:MATCH CUT TO {衔接: 形状, 出: 符纹亮成一个圆}")
    assert "下一场是 S02" in errs(tail1="转场:MATCH CUT TO S03 {衔接: 形状, 出: 符纹亮成一个圆, 入: 宝德门上同一道发亮的符}")
    assert "却没写衔接内容" in errs(tail1="转场:MATCH CUT TO")
    assert "却没写衔接内容" in errs(tail1="转场:SMASH CUT TO(以撞车声切入)")
    # 带花括号的转场行后面还有正文 = 不在场尾
    assert "不在场尾" in errs(tail1=SHAPE + "\n动作:他又坐下。")
    # 台词接力但本场没有任何台词(S02 无对白)
    assert "台词接力要求本场有台词" in errs(tail2="转场:CUT TO {衔接: 台词, 出: 门缓缓打开, 入: 集市的喧闹}")
    # 本集最后一场不能写衔接
    last = _sp() + "转场:CUT TO {衔接: 声音, 出: 集市的喧闹, 入: 钟声}\n"
    res = sl.verify(last)
    assert not res["ok"] and any("最后一场" in e for e in res["errors"])


def test_verify_warns_and_legacy():
    # 两头内容没写进正文 = 只贴标签 → WARN,不 FAIL
    res = sl.verify(_sp(tail1="转场:MATCH CUT TO {衔接: 形状, 出: 一轮满月, 入: 一只铜锣}"))
    assert res["ok"] and sum("找不到对应" in w for w in res["warns"]) == 2
    # 接力配了黑场
    res = sl.verify(_sp(tail1="转场:CUT TO BLACK {衔接: 声音, 出: 符纹亮成一个圆, 入: 宝德门上同一道发亮的符}"))
    assert res["ok"] and any("隔了黑场" in w for w in res["warns"])
    # 存量剧本(无 generated_at):FAIL 全部降为 WARN
    legacy = sl.verify(_sp(tail1="转场:MATCH CUT TO", fm=""))
    assert legacy["ok"] and legacy["legacy"] and not legacy["errors"] and any("只 WARN" in w for w in legacy["warns"])
    old = sl.verify(_sp(tail1="转场:CUT TO {衔接: 魔术, 出: 甲, 入: 乙}", fm="---\ngenerated_at: 2026-10-09\n---\n"))
    assert old["ok"] and any("不在白名单" in w for w in old["warns"])


def test_verify_trial_cap_warn():
    scenes = "".join(
        f"## S{i:02d} | INT | SCN-{i:04d} 房{i} | 夜\n**[事件] ev0001 | [出场] CHAR-0001 | [时长] 10s**\n动作:圆形物件{i}出现在画面中心。\n"
        f"转场:MATCH CUT TO {{衔接: 形状, 出: 圆形物件{i}, 入: 圆形物件{i + 1}}}\n\n" for i in range(1, 4))
    text = NEW + scenes + "## S04 | INT | SCN-0004 房4 | 夜\n**[事件] ev0001 | [出场] CHAR-0001 | [时长] 10s**\n动作:圆形物件4出现在画面中心。\n"
    res = sl.verify(text)
    assert res["ok"] and len(res["links"]) == 3 and any("试验型衔接" in w and "3 处" in w for w in res["warns"])


def test_verify_screenplay_reports_scene_link_valid():
    plan = {"episodes": [{"episode_id": "ep01", "events": ["ev0001"], "duration_budget_s": 180,
                          "treatments": [{"event": "ev0001", "treatment": "dramatize"}]}]}
    ok = et.verify_screenplay(_sp(tail1=SHAPE), plan, "ep01")
    assert ok["checks"]["scene_link_valid"] is True and ok["links"][0]["kind"] == "shape"
    bad = et.verify_screenplay(_sp(tail1="转场:MATCH CUT TO"), plan, "ep01")
    assert bad["checks"]["scene_link_valid"] is False and any(e.startswith("场间衔接 S01") for e in bad["errors"])
    # episode_plan 里没有本集 / 衍生模式两条早退路径同样核
    assert et.verify_screenplay(_sp(tail1="转场:MATCH CUT TO"), {"episodes": []}, "ep01")["checks"]["scene_link_valid"] is False
    der = {"derivative_mode": True, "derivative_note": "衍生", "episodes": [{"ep": "ep01", "events": [], "treatments": []}]}
    assert et.verify_screenplay(_sp(tail1="转场:MATCH CUT TO"), der, "ep01")["checks"]["scene_link_valid"] is False


# ---------------------------------------------------------------- script_links_disposed

TWO = dict(tail1=SHAPE, tail2="转场:SMASH CUT TO {衔接: 反差, 出: 门缓缓打开, 入: 集市的喧闹}")


def test_dispositions_pass_and_fail():
    text = _sp(**TWO)
    plan = ("## 转场清单\n"
            "- 剧本衔接 S01→S02(形状匹配):采纳 —— match_cut(试验);S01 末镜符纹特写居中,S02 首镜门上的符同位同大。\n"
            "- 剧本衔接 S02→S03(反差硬切):弃用 —— S03 开场要先用字卡交代三日后,反差被隔断。\n")
    res = sl.verify_dispositions(text, plan)
    assert res["ok"] and not res["warns"]
    assert [(r["from"], r["disposition"], r["landed"]) for r in res["rows"]] == [("S01", "adopt", "match_cut"), ("S02", "drop", None)]
    # 英文写法 + ASCII 箭头
    en = "- Script link S01->S02 (shape): ADOPT — match_cut; ring centered.\n- Script link S02 to S03 (contrast): MODIFIED — hard_cut; land on the door instead.\n"
    res = sl.verify_dispositions(text, en)
    assert res["ok"] and [r["disposition"] for r in res["rows"]] == ["adopt", "modify"]

    def errs(plan_text):
        res = sl.verify_dispositions(text, plan_text)
        assert not res["ok"]
        return " | ".join(res["errors"])

    assert "没有处置这条剧本衔接" in errs("## 转场清单\n全片硬切\n")
    assert "须写明落到哪种转场类型" in errs(plan.replace("match_cut(试验);", ""))
    assert "须写理由" in errs(plan.replace("弃用 —— S03 开场要先用字卡交代三日后,反差被隔断。", "弃用"))
    assert "没写 采纳 / 修改 / 弃用" in errs(plan.replace("采纳 —— match_cut(试验)", "照做"))


def test_dispositions_not_applicable_and_stray():
    assert sl.verify_dispositions(_sp(), "## 转场清单\n全片硬切\n")["ok"]
    assert sl.verify_dispositions(_sp(), "")["ok"]
    res = sl.verify_dispositions(_sp(), "- 剧本衔接 S01→S02:采纳 —— match_cut\n")
    assert res["ok"] and any("剧本里没有的衔接" in w for w in res["warns"])


def test_cli_list_and_check(tmp_path):
    (tmp_path / "story" / "episodes" / "ep01").mkdir(parents=True)
    (tmp_path / "story" / "episodes" / "ep01" / "screenplay.md").write_text(_sp(**TWO), encoding="utf-8")
    cli = [sys.executable, str(REPO / "code" / "check_scene_links.py"), "--out-root", str(tmp_path), "--ep", "ep01"]
    r = subprocess.run(cli + ["--list", "--json"], capture_output=True, text=True, cwd=REPO)
    assert r.returncode == 0, r.stdout + r.stderr
    assert [x["kind"] for x in json.loads(r.stdout)["links"]] == ["shape", "contrast"]
    r = subprocess.run(cli, capture_output=True, text=True, cwd=REPO)        # 有衔接但还没有导演阐述
    assert r.returncode == 2 and "MISSING" in r.stdout
    (tmp_path / "directing" / "ep01").mkdir(parents=True)
    plan = tmp_path / "directing" / "ep01" / "directing_plan.md"
    plan.write_text("## 转场清单\n- 剧本衔接 S01→S02(形状匹配):采纳 —— match_cut;符纹居中。\n", encoding="utf-8")
    r = subprocess.run(cli, capture_output=True, text=True, cwd=REPO)
    assert r.returncode == 1 and "script_links_disposed       : FAIL" in r.stdout and "S02→S03" in r.stdout
    plan.write_text(plan.read_text(encoding="utf-8") + "- 剧本衔接 S02→S03(反差硬切):采纳 —— smash_cut;门开即切。\n", encoding="utf-8")
    r = subprocess.run(cli + ["--json"], capture_output=True, text=True, cwd=REPO)
    assert r.returncode == 0 and json.loads(r.stdout)["checks"] == {"script_links_disposed": True}
    # 剧本事件机检 CLI 回执带衔接清单
    (tmp_path / "story" / "episode_plan.json").write_text(json.dumps(
        {"episodes": [{"episode_id": "ep01", "events": ["ev0001"], "duration_budget_s": 180,
                       "treatments": [{"event": "ev0001", "treatment": "dramatize"}]}]}), encoding="utf-8")
    r = subprocess.run([sys.executable, str(REPO / "code" / "check_screenplay_events.py"), "--out-root", str(tmp_path), "--ep", "ep01"],
                       capture_output=True, text=True, cwd=REPO)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "scene_link_valid" in r.stdout and "衔接 S01→S02 形状匹配(试验)" in r.stdout


# ---------------------------------------------------------------- 拆解表 link_out / 过场设计诊断

def test_breakdown_load_attaches_link_out(tmp_path):
    d = tmp_path / "story" / "episodes" / "ep01"
    d.mkdir(parents=True)
    (d / "screenplay.md").write_text(_sp(tail1=SHAPE), encoding="utf-8")
    bd = sb.load(tmp_path, "ep01")["breakdown"]            # 推导视图
    s1, s2 = bd["scenes"][0], bd["scenes"][1]
    assert s1["link_out"]["kind"] == "shape" and s1["link_out"]["to"] == "S02" and "link_out" not in s2
    assert s1["blocks"][-1] == {"type": "transition", "text": SHAPE.split(":", 1)[1]}       # 转场块文本保留花括号原文
    # 正式拆解表:工位不写 link_out,宿主照剧本挂;工位自己写的会被剧本现算值覆盖
    agent = {"schema_version": sb.SCHEMA_VERSION, "ep": "ep01", "scenes": [
        {"no": "S01", "summary": "画符", "link_out": {"kind": "bogus"}, "blocks": [{"type": "transition", "text": "MATCH CUT TO S02"}]},
        {"no": "S02", "summary": "门开", "link_out": {"kind": "bogus"}, "blocks": []}]}
    (d / "script_breakdown.json").write_text(json.dumps(agent, ensure_ascii=False), encoding="utf-8")
    res = sb.load(tmp_path, "ep01")
    assert res["source"] == "agent"
    assert res["breakdown"]["scenes"][0]["link_out"]["label_zh"] == "形状匹配" and "link_out" not in res["breakdown"]["scenes"][1]


def test_transition_design_reads_all_transition_forms(tmp_path):
    from modules import transition_design as td

    d = tmp_path / "story" / "episodes" / "ep01"
    d.mkdir(parents=True)
    text = (NEW + "# EP01\n\n"
            "## S01 | INT | SCN-0001 书房 | 夜\n动作:符纹亮成一个圆。\n- **转场**:MATCH CUT TO S02 {衔接: 形状, 出: 符纹亮成一个圆, 入: 宝德门上同一道发亮的符}\n\n"
            "### S02 ｜ EXT ｜ SCN-0002 宝德门 · 三日后\n动作:宝德门上同一道发亮的符。\nTRANSITION: CUT TO\n\n"
            "## S03 | EXT | SCN-0003 集市 | 日\n动作:喧闹。\n转场:黑场\n")
    (d / "screenplay.md").write_text(text, encoding="utf-8")
    sp = td._screenplay(tmp_path, "ep01")
    assert list(sp) == ["S01", "S02", "S03"]
    # 场头对得上旧正则的字段取原文
    assert sp["S01"]["header"] == "## S01 | INT | SCN-0001 书房 | 夜" and sp["S01"]["tod"] == "夜" and sp["S01"]["name"] == "书房"
    assert sp["S01"]["transition_out"].startswith("转场:MATCH CUT TO S02 {") and sp["S01"]["link_out"]["kind"] == "shape"
    # 旧正则读不到的排版(三级标题 / 全角竖线 / 英文锚点)现在读得到
    assert sp["S02"]["scene_id"] == "SCN-0002" and sp["S02"]["int_ext"] == "EXT" and sp["S02"]["transition_out"] == "转场:CUT TO"
    assert sp["S02"]["transition_in"] == sp["S01"]["transition_out"] and sp["S02"]["link_out"] is None
    assert sp["S03"]["transition_out"] == "转场:黑场"

    (tmp_path / "directing" / "ep01").mkdir(parents=True)
    shots = [{"shot_id": f"sh00{i}", "scene_no": n, "size": "中景", "duration_s": 2.0}
             for i, n in enumerate(["S01", "S01", "S02", "S03"], 1)]
    groups = [
        {"group_id": "grp001", "scene_id": "SCN-0001", "scene_no": "S01", "shots": ["sh001"], "total_duration_s": 2, "time_of_day": "夜"},
        {"group_id": "grp002", "scene_id": "SCN-0001", "scene_no": "S01", "shots": ["sh002"], "total_duration_s": 2, "time_of_day": "夜"},
        {"group_id": "grp003", "scene_id": "SCN-0002", "scene_no": "S02", "shots": ["sh003"], "total_duration_s": 2, "time_of_day": "日"},
        {"group_id": "grp004", "scene_id": "SCN-0003", "scene_no": "S03", "shots": ["sh004"], "total_duration_s": 2, "time_of_day": "日"},
    ]
    (tmp_path / "directing" / "ep01" / "shot_list.json").write_text(json.dumps(
        {"episode": "ep01", "budget_s": 60, "shots": shots, "generation_groups": groups}, ensure_ascii=False), encoding="utf-8")
    diag = {b["id"]: b["diagnosis"] for b in td.diagnose(tmp_path, "ep01")}
    assert diag["B-grp001-grp002"]["screenplay_link"] is None            # 同一场内的组边界不挂
    lk = diag["B-grp002-grp003"]["screenplay_link"]
    assert lk["kind"] == "shape" and lk["from"] == "S01" and lk["to"] == "S02"
    assert diag["B-grp002-grp003"]["screenplay_hint"].startswith("转场:MATCH CUT TO S02")
    assert diag["B-grp003-grp004"]["screenplay_link"] is None and diag["B-grp003-grp004"]["screenplay_hint"] == "转场:CUT TO"
    assert diag["B-grp002-grp003"]["time_word"] == "三日后"               # 场头时间词照旧取到(新读到的排版)
