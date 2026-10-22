"""各工位 SOUL 示例里的实体 ID 写法与 WORKFLOW.md「实体 ID 写法」表一致(#152)。

宿主只认部分写法:场景 `SCN-\\d+`、人物 `CHAR-…`(区分大小写)、生物 `CRE-…`、事件 `ev` + 数字;其余世界观命名空间各给一种写法。
SOUL 示例写成别的形态,Agent 照抄后宿主正则静默漏抓、跨文件引用对不上。这里扫 agents/**/SOUL.md 里已知的不一致写法。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PATTERNS = {
    "人物写成 c003": re.compile(r"(?<![A-Za-z0-9#\-])c0\d{2,3}(?!\d)"),
    "人物写成小写 char- / char_ / chr_": re.compile(r"(?<![A-Za-z0-9_.\-])(?:char|chr)[-_](?!concept\b|rate(?:_|\b)|id\b)[a-z0-9]"),
    "地理节点写成 GEO-": re.compile(r"\bGEO-"),
    "事件写成 evt_": re.compile(r"\bevt[_-]"),
    "事件写成 ev_数字": re.compile(r"\bev_\d"),
    "事件写成 EV-数字": re.compile(r"\bEV-\d"),
    "场景写成 sc-": re.compile(r"(?<![A-Za-z0-9_\-])sc-[a-z0-9]"),
    "场景写成 s012": re.compile(r"(?<![A-Za-z0-9#\-])s0\d{2,3}(?!\d)"),
    "生物写成 cre_": re.compile(r"\bcre_[a-z0-9]"),
    "坐骑卡写成 MNT-": re.compile(r"\bMNT-"),
    "分档货币写成 cur_x_low": re.compile(r"\bcur_[a-z0-9_]+_(?:low|mid|high)\b"),
    "道具写成 prop_017": re.compile(r"\bprop_(?:\d|…)"),
}

# 确有理由的例外:(SOUL 相对路径, 规则名, 命中片段) → 理由。没有例外就保持为空。
ALLOW: dict[tuple[str, str, str], str] = {}


def scan(agents_dir: Path) -> list[tuple[str, int, str, str]]:
    hits = []
    for p in sorted(agents_dir.rglob("SOUL.md")):
        rel = p.relative_to(agents_dir.parent).as_posix()
        for no, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            for name, rx in PATTERNS.items():
                for m in rx.finditer(line):
                    if (rel, name, m.group(0)) not in ALLOW:
                        hits.append((rel, no, name, line.strip()[:160]))
    return hits


def test_patterns_catch_known_bad_forms_only():
    bad = ['"characters": ["c003"]', "[出场] char-linxiao", '"owner_ref": "char_lin_feng"', '"char_id": "chr_lin_feng"',
           '"by_character": { "char-001": 1 }', '"geo_ref": "GEO-qingyun-zong"', '"cause_event": "evt_…"', '"after_event": "ev_0042"',
           '"event_id": "EV-0102"', "## S03 | INT | scene:sc-cangjinge | 夜", '"scene_id": "s012"', "assets/concepts/scenes/s012/",
           '"id": "cre_bingjiao"', "坐骑卡的 `MNT-*`", '"from": "cur_lingshi_low"', '"id": "prop_017"', '"prop_ref": "prop_…"']
    for s in bad:
        assert any(rx.search(s) for rx in PATTERNS.values()), s
    good = ['"characters": ["CHAR-0003"]', "CHAR-jie-rui-er", '"scene_id": "SCN-0012"', "sh014", "S03", "S010", '"event_id": "ev0102"',
            '"geo_ref": "geo_mt_qingyun"', '"id": "CRE-0012"', "mnt_bingjiao_01", '"from": "cur_lingshi#low"', "p4-char-concept",
            "task_id: p4-char-CHAR-0003-concept", "`char_rate_snap`", "avsync.char_rate_boundaries()", '"char_id": "CHAR-0001"', "<char_id>", "#c0c0c0", "DEF-ep01-0042",
            "LGT-0012-01", "prompt_token", "disc-1", '"id": "PROP-0017"', "prop_scale_check", "prop_ref_attached", "prop_states"]
    for s in good:
        assert not any(rx.search(s) for rx in PATTERNS.values()), s


def test_soul_examples_use_workflow_id_forms():
    hits = scan(ROOT / "agents")
    assert not hits, "SOUL 示例实体 ID 与 WORKFLOW.md「实体 ID 写法」表不一致:\n" + "\n".join(
        f"  {rel}:{no} [{name}] {line}" for rel, no, name, line in hits)


def test_workflow_has_id_table_with_conditional_scope():
    text = (ROOT / "agents/WORKFLOW.md").read_text(encoding="utf-8")
    i = text.index("**实体 ID 写法")
    block = text[i:text.index("**G2 闸门", i)]
    assert "新项目适用" in block and "存量项目以 `bible/*/index.json` 已发 ID" in block and "不改已发 ID、不做迁移" in block
    for prefix in ("`SCN-`", "`CHAR-`", "`CRE-`", "`PROP-`", "`ev`", "`geo_`", "`camp_`", "`cur_`", "`mnt_`"):
        assert prefix in block, prefix
