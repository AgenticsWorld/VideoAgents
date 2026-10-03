"""修改师(00-orchestration/reviser)纯函数与提示词装配的离线测试。"""
import json
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="va-reviser-")
os.environ.setdefault("VIDEOAGENTS_DATA_DIR", _TMP)

from services.runtime import core  # noqa: E402

PROJ = "revtest"


def test_reviser_registered_stateless_not_dispatcher():
    assert core.is_stateless_agent(core.REVISER_ID)
    assert not core.is_dispatcher_agent(core.REVISER_ID)
    ag = {a["id"]: a for a in core.list_agents(refresh=True)}
    assert core.REVISER_ID in ag
    assert ag[core.REVISER_ID]["category"] == "00-orchestration"
    assert core.default_agent_model(core.REVISER_ID, "smart_claude") == {"engine": "claude", "model": "opus"}


def test_sanitize_target_whitelists_and_limits():
    assert core.sanitize_revision_target(None) is None
    assert core.sanitize_revision_target("x") is None
    t = core.sanitize_revision_target({
        "kind": "chara/cter!", "id": "C" * 500, "ep": "ep01", "label": 42,
        "files": ["a.md", "", 7, "b.png"], "agents": ["06-art/prop", "../evil", "x"],
        "rerun_downstream": "yes", "extra": 1})
    assert t["kind"] == "character" and len(t["id"]) == 120 and t["ep"] == "ep01"
    assert t["files"] == ["a.md", "b.png"] and t["agents"] == ["06-art/prop"]
    assert t["rerun_downstream"] is True and t["label"] == "42" and "extra" not in t


def test_agents_for_kind_and_explicit_override():
    assert core.revision_agents_for({"kind": "character"}) == [
        "03-characters/appearance", "06-art/character-concept"]
    assert core.revision_agents_for({"kind": "prop", "agents": ["09-audio/music", "no/such"]}) == [
        "09-audio/music", "06-art/prop"]
    assert core.revision_agents_for({"kind": "unknown"}) == []


def test_parse_revision_block():
    txt = ("改好了。\n\n## 变更记录\nchanged_files: a.md, b.png; c.json\n"
           "dirty_nodes: none\nsignature_expired: H3S\nchecks: x PASS; y PASS\nnotes: 备注文字\n")
    rec = core._parse_revision_block(txt)
    assert rec["changed_files"] == ["a.md", "b.png", "c.json"]
    assert rec["dirty_nodes"] == [] and rec["signature_expired"] == "H3S"
    assert rec["checks"] == ["x PASS", "y PASS"] and rec["notes"] == "备注文字"
    assert core._parse_revision_block("没有段落") == {}


def test_header_history_and_pending_note():
    d = core.revisions_dir(PROJ)
    d.mkdir(parents=True, exist_ok=True)
    rec = {"id": "abc123abc123", "project": PROJ, "created": 1.0, "status": "done",
           "target": {"kind": "character", "id": "CHAR-001", "ep": ""},
           "message": "披风改深红", "files": ["bible/characters/CHAR-001/appearance.md"],
           "record": {"changed_files": ["bible/characters/CHAR-001/appearance.md"],
                      "dirty_nodes": ["p7-ep01-grp003-prompt"], "signature_expired": ""},
           "rerun_downstream": False, "consumed": None}
    (d / "abc123abc123.json").write_text(json.dumps(rec, ensure_ascii=False))
    head = core._revision_header(PROJ, {"kind": "character", "id": "CHAR-001", "ep": "",
                                        "files": ["x.md"], "agents": [], "rerun_downstream": True},
                                 "修改 人物\n修改意见: 再深一点")
    assert head.startswith("[修改单] kind=character id=CHAR-001 ep=- rerun_downstream=是")
    assert "files: x.md" in head and "03-characters/appearance" in head
    assert "runs/revisions/abc123abc123.json" in head and head.endswith("修改意见: 再深一点")
    note = core.pending_revisions_note(PROJ)
    assert "[修改记录]" in note and "p7-ep01-grp003-prompt" in note and "披风改深红" in note
    assert core.mark_revisions_consumed(PROJ, "test") == 1
    assert core.pending_revisions_note(PROJ) == ""
    assert json.loads((d / "abc123abc123.json").read_text())["consumed"]["by"] == "test"


def test_role_prompt_attaches_delegate_souls():
    core.ensure_project(PROJ)
    p = core.build_role_prompt(core.REVISER_ID, PROJ, {"kind": "scene", "id": "SCN-0001"})
    assert "你是修改师" in p and "不要代劳" not in p
    assert "### 代行:05-scenes/scene" in p and "### 代行:06-art/environment-concept" in p
    q = core.build_role_prompt("06-art/prop", PROJ)
    assert "不要代劳" in q and "代行工位" not in q
