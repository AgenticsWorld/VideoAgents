# -*- coding: utf-8 -*-
"""全自动模式(工作流预览页勾选,按项目,2026-10-22):开启后签字卡建卡即代签、确认卡按默认项代答,
签字前置机检照常拦(被拦卡保留、原因交等待方 / 总制片),Agent 提示词只在开启时注入「全自动模式」段。"""
import asyncio
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules import prompt_gate as pg  # noqa: E402
from services.runtime import core, dispatch  # noqa: E402

PROJ = "demo"


def _write(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _run(coro):
    loop = asyncio.new_event_loop()   # 不用 asyncio.run:它会把主线程当前循环清成 None,影响后面用 get_event_loop() 的测试
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def host(tmp_path, monkeypatch):
    """项目 demo:ep01 两组只写了 grp001 的提示词,H3V 闸门 g7p-ep01 已解锁(依赖全完成)。"""
    base = tmp_path / "projects" / PROJ
    _write(base / "settings.json", {})
    _write(base / "directing" / "ep01" / "shot_list.json",
           {"generation_groups": [{"group_id": "grp001"}, {"group_id": "grp002"}]})
    _write(base / "assets" / "prompts" / "ep01" / "grp001.json", {"video_prompt": "Shot 1: grp001", "refs": []})
    _write(base / "runs" / "dag.json", {"nodes": [
        {"id": "p7-prompt-ep01-grp001", "state": "done"}, {"id": "p7-prompt-ep01-grp002", "state": "done"},
        {"id": "g7p-ep01", "human": True, "gate": True, "checkpoint": pg.CHECKPOINT, "state": "pending",
         "depends_on": ["p7-prompt-ep01-grp001", "p7-prompt-ep01-grp002"]},
        {"id": "p7-video-ep01-grp001", "state": "pending", "depends_on": ["g7p-ep01"]}]})
    monkeypatch.setattr(core, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(core, "notify_user", lambda *a, **k: None)
    monkeypatch.setattr(core, "save_state", lambda *a, **k: None)
    monkeypatch.setitem(core.STATE, "full_auto", {})
    events, chats, continued = [], [], []
    monkeypatch.setattr(core.HUB, "publish", lambda ev: events.append(ev))

    async def _continue(c):
        continued.append(dict(c))
        return None

    async def _chat(body):
        chats.append(body)
        return {"run_id": f"r{len(chats)}"}
    monkeypatch.setattr(core, "_continue_signed_gate", _continue)
    monkeypatch.setattr(core, "api_chat", _chat)
    monkeypatch.setattr(core, "agent_effective_model", lambda a: {"engine": "claude", "model": ""})
    core.CONFIRMS.clear()
    yield {"base": base, "events": events, "chats": chats, "continued": continued}
    core.CONFIRMS.clear()


def _fix_missing_prompt(base: Path):
    _write(base / "assets" / "prompts" / "ep01" / "grp002.json", {"video_prompt": "Shot 1: grp002", "refs": []})


def test_prompt_section_only_when_enabled(host, monkeypatch):
    assert core.full_auto_prompt_section(PROJ) == "" and core.full_auto_prompt_section(PROJ, True) == ""
    off = core.build_role_prompt(core.ORCHESTRATOR_AGENT, PROJ)
    assert "全自动模式" not in off
    monkeypatch.setitem(core.STATE, "full_auto", {PROJ: True})
    orch = core.build_role_prompt(core.ORCHESTRATOR_AGENT, PROJ)
    assert orch.count("## 全自动模式:开启") == 1
    assert "自动签字未通过" in orch and "--sign --project demo" in orch and "内容安全 / 版权类 blocker 不得代为豁免" in orch
    member = core.build_role_prompt("06-art/prop", PROJ)
    assert member.count("## 全自动模式:开启") == 1 and "保证出片" in member and "自动签字未通过" not in member
    assert core.full_auto_prompt_section("other") == ""          # 按项目:别的项目不受影响


def test_off_keeps_manual_sign_off(host):
    r = _run(core.api_confirm_create({"question": "【H3V-视频提示词确认(ep01)】", "kind": "sign", "project": PROJ}))
    c = core.CONFIRMS[r["confirm_id"]]
    assert c["answer"] is None and "auto" not in r and "auto_blocked" not in r
    assert any(e.get("type") == "confirm" for e in host["events"])


def test_sign_card_signed_on_create(host, monkeypatch):
    monkeypatch.setitem(core.STATE, "full_auto", {PROJ: True})
    _fix_missing_prompt(host["base"])
    r = _run(core.api_confirm_create({"question": "【H3V-视频提示词确认(ep01)】已写完", "kind": "sign", "project": PROJ}))
    c = core.CONFIRMS[r["confirm_id"]]
    assert r["answer"] == "签字" and r["auto"] is True and c["answer"] == "签字" and c["gate_id"] == "g7p-ep01"
    assert not any(e.get("type") == "confirm" for e in host["events"])            # 不弹窗
    assert len(host["continued"]) == 1 and host["continued"][0]["auto"] is True     # 唤醒总制片走全自动回执
    rec = pg.load_record(host["base"], "ep01")
    assert rec["answer"] == "签字" and rec["signed_from"] == "auto" and set(rec["groups"]) == {"grp001", "grp002"}
    # 同一闸门换个措辞再发(漏单守卫 / 总制片各写一份):复用已代签的卡,不再签第二次
    r2 = _run(core.api_confirm_create({"question": "[H3V Video prompt sign-off (ep01)]", "kind": "sign",
                                       "project": PROJ, "origin": "runtime_gate_guard"}))
    assert r2["confirm_id"] == c["id"] and len(host["continued"]) == 1


def test_confirm_card_gets_default(host, monkeypatch):
    monkeypatch.setitem(core.STATE, "full_auto", {PROJ: True})
    r = _run(core.api_confirm_create({"question": "任务X验收未过,是否重跑?", "project": PROJ, "default": "跳过"}))
    assert r["answer"] == "跳过" and core.CONFIRMS[r["confirm_id"]]["answer"] == "跳过"
    r2 = _run(core.api_confirm_create({"question": "用 A 还是 B?", "project": PROJ, "options": ["方案A", "方案B"]}))
    assert r2["answer"] == "方案A"
    assert not host["continued"] and not host["chats"]


def test_blocked_sign_stays_open_and_waiter_gets_reason(host, monkeypatch):
    monkeypatch.setitem(core.STATE, "full_auto", {PROJ: True})
    monkeypatch.setitem(core.RUNS, "run-orch", {"id": "run-orch", "status": "running", "project": PROJ,
                                                "agent": core.ORCHESTRATOR_AGENT})
    q = "【H3V-视频提示词确认(ep01)】本集提示词已写完"
    r = _run(core.api_confirm_create({"question": q, "kind": "sign", "project": PROJ, "parent": "run-orch"}))
    c = core.CONFIRMS[r["confirm_id"]]
    assert "grp002" in r["auto_blocked"] and c["answer"] is None and "auto" not in c
    assert core.confirm_public(c)["auto_blocked"] == r["auto_blocked"]             # 卡上带原因(控制台弹窗显示)
    assert any(e.get("type") == "confirm" for e in host["events"])
    assert not host["chats"]                       # 有 dispatch.py 在等:原因由它输出,不另行知会总制片
    # 修好后重新发起(措辞不同、同一闸门):复用这张卡并代签
    _fix_missing_prompt(host["base"])
    r2 = _run(core.api_confirm_create({"question": "【H3V-视频提示词确认(ep01)】已补齐 grp002",
                                       "kind": "sign", "project": PROJ, "parent": "run-orch"}))
    assert r2["confirm_id"] == c["id"] and r2["answer"] == "签字" and "auto_blocked" not in c


def test_gate_guard_block_notifies_producer_once(host, monkeypatch):
    monkeypatch.setitem(core.STATE, "full_auto", {PROJ: True})
    created = _run(core.ensure_human_gate_approvals(PROJ))            # 漏单守卫补建:没有等待方
    c = core.CONFIRMS[created[0]]
    assert c["answer"] is None and c["auto_blocked"]
    assert len(host["chats"]) == 1 and "代签未通过" in host["chats"][0]["message"]
    assert host["chats"][0]["agent"] == core.ORCHESTRATOR_AGENT
    _run(core.ensure_human_gate_approvals(PROJ))                       # 每次核对重试代签;原因没变不重复知会
    assert len(host["chats"]) == 1
    _fix_missing_prompt(host["base"])
    _run(core.ensure_human_gate_approvals(PROJ))
    assert c["answer"] == "签字" and len(host["continued"]) == 1


def test_toggle_sweeps_pending_cards_and_wakes_producer(host, monkeypatch):
    monkeypatch.setattr(core, "max_retries_setting", lambda: 2)   # 重跑次数 0 时重跑类默认被改成「跳过」,这里固定额度
    r = _run(core.api_confirm_create({"question": "【H3V-视频提示词确认(ep01)】", "kind": "sign", "project": PROJ}))
    rc = _run(core.api_confirm_create({"question": "rerun?", "project": PROJ, "default": "重跑"}))
    assert core.CONFIRMS[r["confirm_id"]]["answer"] is None
    _fix_missing_prompt(host["base"])
    out = _run(core.api_full_auto_set(PROJ, {"enabled": True}))
    assert out["enabled"] is True and core.STATE["full_auto"] == {PROJ: True}
    assert set(out["answered"]) == {r["confirm_id"], rc["confirm_id"]} and not out["blocked"]
    assert core.CONFIRMS[r["confirm_id"]]["answer"] == "签字" and core.CONFIRMS[rc["confirm_id"]]["answer"] == "重跑"
    assert any(e == {"type": "full_auto", "project": PROJ, "enabled": True} for e in host["events"])
    assert out["notified"] and "全自动模式已开启" in host["chats"][-1]["message"]   # 总制片没在跑、DAG 未走完
    assert _run(core.api_full_auto_get(PROJ)) == {"project": PROJ, "enabled": True}
    assert core._preview_workflow(PROJ)["full_auto"] is True
    n = len(host["chats"])
    assert _run(core.api_full_auto_set(PROJ, {"enabled": True})) == {"project": PROJ, "enabled": True}   # 没变化不重扫
    assert len(host["chats"]) == n
    off = _run(core.api_full_auto_set(PROJ, {"enabled": False}))
    assert off["enabled"] is False and core.STATE["full_auto"] == {}
    r3 = _run(core.api_confirm_create({"question": "【H1 世界圣经】", "kind": "sign", "project": PROJ}))
    assert core.CONFIRMS[r3["confirm_id"]]["answer"] is None                      # 关闭后恢复人工签字
    with pytest.raises(core.ServiceError):
        _run(core.api_full_auto_set("no-such-project", {"enabled": True}))


def test_dispatch_prints_auto_answer_and_block_reason(monkeypatch):
    monkeypatch.setattr(dispatch, "heartbeat", lambda note: None)
    monkeypatch.setattr(dispatch.time, "sleep", lambda s: None)
    replies = {"/approvals": {"confirm_id": "c1", "answer": "签字", "auto": True}}
    monkeypatch.setattr(dispatch, "api", lambda path, payload=None: replies[path])
    buf = io.StringIO()
    with redirect_stdout(buf):
        dispatch.confirm("【H1】", 60, ["签字", "暂缓"], "签字", sign=True)
    assert buf.getvalue().strip() == "签字"
    # 建卡时被拦 → 直接输出原因;轮询中才被拦(勾选时扫到的存量卡)同样退出
    replies["/approvals"] = {"confirm_id": "c2", "auto_blocked": "H3V 未能签字:grp002 缺提示词"}
    buf = io.StringIO()
    with redirect_stdout(buf):
        dispatch.confirm("【H3V】", 60, ["签字", "暂缓"], "签字", sign=True)
    assert buf.getvalue().strip() == "自动签字未通过:H3V 未能签字:grp002 缺提示词"
    replies.update({"/approvals": {"confirm_id": "c3"}, "/approvals/c3": {"id": "c3", "auto_blocked": "x"}})
    buf = io.StringIO()
    with redirect_stdout(buf):
        dispatch.confirm("【H3V】", 60, ["签字", "暂缓"], "签字", sign=True)
    assert buf.getvalue().strip() == "自动签字未通过:x"


def test_api_routes_registered():
    from services.api.app import app
    paths = app.openapi()["paths"]
    assert {"get", "post"} <= set(paths["/api/v1/projects/{project}/full-auto"])
