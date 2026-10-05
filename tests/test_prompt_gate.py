# -*- coding: utf-8 -*-
"""视频提示词签字(H3V,workflow.yaml g7p,2026-10-05):本集全部组 p7-prompt 后、锚点包 / 组视频生成前的人工闸门。
modules/prompt_gate.py 口径 + genmedia video 硬校验 video_prompt_signed + core 签字前置 / 签字记录 / 页面状态。"""
import asyncio
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules import genmedia, prompt_gate as pg  # noqa: E402
from services.runtime import core  # noqa: E402


def _write(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False))


def _project(tmp_path: Path, gate_state: str | None = "pending", prompts=("grp001", "grp002")) -> Path:
    base = tmp_path / "projects" / "demo"
    _write(base / "settings.json", {})
    _write(base / "directing" / "ep01" / "shot_list.json",
           {"generation_groups": [{"group_id": "grp001"}, {"group_id": "grp002"}]})
    for gid in prompts:
        _write(base / "assets" / "prompts" / "ep01" / f"{gid}.json",
               {"video_prompt": f"Shot 1: {gid}", "refs": ["a.png"], "audio_refs": [], "video_refs": []})
    nodes = [{"id": "p7-prompt-ep01-grp001", "state": "done"}, {"id": "p7-prompt-ep01-grp002", "state": "done"},
             {"id": "g7-ep01", "human": True, "checkpoint": "H3B-视觉生成确认", "state": "pending",
              "depends_on": ["p7-video-ep01-grp001"]}]
    if gate_state is not None:
        nodes.append({"id": "g7p-ep01", "human": True, "gate": True, "checkpoint": pg.CHECKPOINT, "state": gate_state,
                      "depends_on": ["p7-prompt-ep01-grp001", "p7-prompt-ep01-grp002"]})
    _write(base / "runs" / "dag.json", {"nodes": nodes})
    return base


def test_workflow_yaml_has_gate_between_prompt_and_video():
    doc = yaml.safe_load((ROOT / "agents" / "workflow.yaml").read_text())
    tasks = {t["id"]: t for p in doc["phases"] for t in p["tasks"]}
    gate = tasks["g7p"]
    assert gate["human"] is True and gate["gate"] is True and gate["checkpoint"] == pg.CHECKPOINT
    assert gate["depends_on"] == ["p7-prompt"] and gate.get("scope") == "episode"
    assert "g7p" in tasks["p7-video"]["depends_on"] and "g7p" in tasks["p7-image"]["depends_on"]


def test_gate_node_and_video_blocked(tmp_path):
    base = _project(tmp_path, "pending")
    assert pg.gate_node(base, "ep01")["id"] == "g7p-ep01"
    assert pg.gate_node(base, "ep02") is None                    # 别的集的闸门不算
    assert pg.video_blocked(base, "ep01").startswith("g7p-ep01")
    assert pg.video_blocked(base, "ep02") == ""
    for state in ("passed", "done", "passed_human_override", "skipped", "waived"):
        assert pg.video_blocked(_project(tmp_path, state), "ep01") == "", state
    assert pg.video_blocked(_project(tmp_path, None), "ep01") == ""   # 存量集:DAG 里没有该闸门 = 不拦


def test_h3b_gate_is_not_mistaken_for_prompt_gate(tmp_path):
    base = _project(tmp_path, None)          # 只有 g7-ep01(H3B)
    assert pg.gate_node(base, "ep01") is None


def test_missing_prompts_and_fingerprints(tmp_path):
    base = _project(tmp_path, prompts=("grp001",))
    assert pg.missing_prompts(base, "ep01") == ["grp002"]
    _write(base / "assets" / "prompts" / "ep01" / "grp002.json", {"video_prompt": "  "})
    assert pg.missing_prompts(base, "ep01") == ["grp002"]         # 空正文也算没写
    _write(base / "assets" / "prompts" / "ep01" / "grp002.json", {"video_prompt": "Shot 1: x", "refs": []})
    assert pg.missing_prompts(base, "ep01") == []
    rec = {"groups": pg.fingerprints(base, "ep01")}
    assert pg.changed_groups(base, "ep01", rec) == []
    d = json.loads((base / "assets" / "prompts" / "ep01" / "grp001.json").read_text())
    _write(base / "assets" / "prompts" / "ep01" / "grp001.json", {**d, "skill_applied": {"id": "x"}, "user_note": "n"})
    assert pg.changed_groups(base, "ep01", rec) == []             # 回执 / 注释等字段不算改动
    _write(base / "assets" / "prompts" / "ep01" / "grp001.json", {**d, "refs": ["a.png", "b.png"]})
    assert pg.changed_groups(base, "ep01", rec) == ["grp001"]
    assert pg.changed_groups(base, "ep01", {}) == []


def test_clip_target(tmp_path):
    base = _project(tmp_path)
    assert pg.clip_target(base / "assets" / "clips" / "ep01" / "grp001.mp4") == (base.resolve(), "ep01", "grp001")
    assert pg.clip_target(base / "assets" / "clips" / "ep01" / "grp001_02.mp4")[1] == "ep01"   # 多候选同样算组视频
    assert pg.clip_target(base / "assets" / "transitions" / "ep01" / "B-001.establishing.mp4") is None
    assert pg.clip_target(base / "assets" / "clips" / "ep01" / "archive" / "grp001.mp4") is None
    assert pg.clip_target(tmp_path / "out.mp4") is None


def test_genmedia_video_refuses_before_signoff(tmp_path):
    base = _project(tmp_path, "pending")
    out = base / "assets" / "clips" / "ep01" / "grp001.mp4"
    with pytest.raises(RuntimeError, match="video_prompt_signed"):
        genmedia._check_video_prompt_signed(str(out))
    genmedia._check_video_prompt_signed(str(base / "assets" / "transitions" / "ep01" / "B-001.bridge.mp4"))
    genmedia._check_video_prompt_signed(str(_project(tmp_path, "passed") / "assets" / "clips" / "ep01" / "grp001.mp4"))
    genmedia._check_video_prompt_signed(str(_project(tmp_path, None) / "assets" / "clips" / "ep01" / "grp001.mp4"))


def test_gate_code_and_display():
    node = {"id": "g7p-ep03", "human": True}
    assert core._gate_code(node) == "H3V"
    assert core._gate_code({"id": "g7-ep03"}) == "H3B"            # g7p 不得吞掉 g7
    assert core._gate_display(node, "zh") == "H3V-视频提示词确认(ep03)"
    assert core._gate_display(node, "en") == "H3V Video prompt sign-off (ep03)"
    assert core._gate_display({"id": "g7p-ep03", "checkpoint": pg.CHECKPOINT}, "zh") == pg.CHECKPOINT


def _run(coro):
    return asyncio.run(coro)     # 不依赖线程里残留的事件循环(全量跑时前面的测试可能已把它关掉)


@pytest.fixture
def host(tmp_path, monkeypatch):
    base = _project(tmp_path, "pending", prompts=("grp001",))
    monkeypatch.setattr(core, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(core, "notify_user", lambda *a, **k: None)

    async def _noop(c):
        return None
    monkeypatch.setattr(core, "_continue_signed_gate", _noop)
    core.CONFIRMS.clear()
    yield base
    core.CONFIRMS.clear()


def test_sign_guard_record_and_page_state(host):
    base = host
    r = _run(core.api_confirm_create({"question": "【H3V-视频提示词确认(ep01)】本集 2 组视频提示词已写完", "kind": "sign",
                                      "project": "demo"}))
    c = core.CONFIRMS[r["confirm_id"]]
    assert c["gate_id"] == "g7p-ep01" and c["checkpoint"] == pg.CHECKPOINT
    gate = core._prompt_gate(base, "ep01")
    assert gate["in_dag"] and not gate["signed"] and gate["pending"][0]["id"] == c["id"]
    assert gate["missing_prompts"] == ["grp002"]
    # 还有组没写提示词 → 拒签,签字卡保留
    with pytest.raises(core.ServiceError) as ei:
        _run(core.api_prompt_signoff("demo", "ep01", {"confirm_id": c["id"], "answer": "签字"}))
    assert ei.value.status_code == 409 and c["answer"] is None
    assert not (base / pg.SIGNOFF_REL.format(ep="ep01")).exists()
    # 提示词补齐 → 英文按钮答复归一后签字,落逐组指纹
    _write(base / "assets" / "prompts" / "ep01" / "grp002.json", {"video_prompt": "Shot 1: grp002", "refs": []})
    res = _run(core.api_prompt_signoff("demo", "ep01", {"confirm_id": c["id"], "answer": "Sign off"}))
    assert res["confirm"]["answer"] == "签字" and res["gate"]["signed"] and not res["gate"]["pending"]
    rec = pg.load_record(base, "ep01")
    assert rec["answer"] == "签字" and rec["signed_from"] == "preview_storyboard" and set(rec["groups"]) == {"grp001", "grp002"}
    # 签字后改某组提示词:页面只提示改动,不算过期
    _write(base / "assets" / "prompts" / "ep01" / "grp002.json", {"video_prompt": "Shot 1: rewritten", "refs": []})
    gate = core._prompt_gate(base, "ep01")
    assert gate["signed"] and gate["changed_groups"] == ["grp002"]


def test_hold_is_recorded_but_not_signed(host):
    base = host
    r = _run(core.api_confirm_create({"question": "[H3V Video prompt sign-off (ep01)]", "kind": "sign", "project": "demo"}))
    _run(core.api_confirm_answer(r["confirm_id"], {"answer": "Hold"}))      # 控制台签字卡答复同样落记录
    rec = pg.load_record(base, "ep01")
    assert rec["answer"] == "暂缓" and rec["signed_from"] == "console" and rec["groups"] == {}
    assert core._prompt_gate(base, "ep01")["signed"] is False
    assert pg.video_blocked(base, "ep01")                                    # 暂缓 = 仍不得出视频
