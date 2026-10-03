"""引擎事件 → 前端实时反馈:claude 增量 stream_event 去重、思考心跳、工具回执。"""
import json

import pytest

from services.runtime import core


@pytest.fixture
def bus(monkeypatch):
    events = []
    monkeypatch.setattr(core.HUB, "publish", lambda ev: events.append(ev))
    monkeypatch.setattr(core, "publish_run", lambda run: events.append({"type": "run"}))
    return events


def _run():
    return {"id": "r1", "agent": "a", "project": "p"}


def _ev(event):
    return {"type": "stream_event", "event": event, "session_id": "s"}


def test_claude_partial_text_streams_once(bus):
    run = _run()
    core.handle_claude_event(run, _ev({"type": "content_block_start", "index": 0,
                                       "content_block": {"type": "text", "text": ""}}))
    for piece in ("你好", ",", "世界"):
        core.handle_claude_event(run, _ev({"type": "content_block_delta", "index": 0,
                                           "delta": {"type": "text_delta", "text": piece}}))
    # 整块 assistant 事件照旧到达:不得重复推送已流出的文字
    core.handle_claude_event(run, {"type": "assistant", "message": {
        "content": [{"type": "text", "text": "你好,世界"}]}})
    texts = [e["text"] for e in bus if e["type"] == "text"]
    assert texts == ["你好", ",", "世界"]
    assert run["text"] == "你好,世界"
    assert "progress" not in run          # 文字块收尾后清掉进度行


def test_claude_without_partial_publishes_whole_block(bus):
    run = _run()
    core.handle_claude_event(run, {"type": "assistant", "message": {
        "content": [{"type": "text", "text": "整段"}]}})
    assert [e["text"] for e in bus if e["type"] == "text"] == ["整段"]


def test_claude_thinking_heartbeat_and_tool_receipt(bus):
    run = _run()
    core.handle_claude_event(run, {"type": "system", "subtype": "thinking_tokens",
                                   "estimated_tokens": 12350})
    assert run["progress"] == "💭 思考中 ≈12.3k tokens"
    core.handle_claude_event(run, _ev({"type": "content_block_start", "index": 1,
                                       "content_block": {"type": "tool_use", "name": "Bash", "input": {}}}))
    core.handle_claude_event(run, _ev({"type": "content_block_delta", "index": 1,
                                       "delta": {"type": "input_json_delta", "partial_json": "x" * 2100}}))
    assert run["progress"].startswith("⚙ 生成工具调用参数… Bash 2.1 KB")
    core.handle_claude_event(run, {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]}})
    assert run["progress"] == "⚙ 执行工具中… Bash"
    core.handle_claude_event(run, {"type": "user", "message": {"content": [
        {"type": "tool_result", "content": [{"type": "text", "text": "a.txt\nb.txt"}]}]}})
    receipts = [e for e in bus if e["type"] == "tool" and e.get("kind") == "result"]
    assert receipts and receipts[-1]["desc"] == "↩ 11 B · a.txt"
    assert run["progress"] == "⏳ 等待模型下一步…"
    # 回执不得混进 activity(prompt_skill_read 机检按 activity 做路径匹配)
    assert all(not a.startswith("↩") for a in run.get("activity", []))


def test_tool_receipt_error_flag(bus):
    run = _run()
    core.tool_receipt(run, "boom: no such file", is_error=True, label="exit 1")
    assert bus[0]["desc"] == "✗ 报错 · exit 1 · boom: no such file"


def test_other_engines_receipts(bus):
    run = _run()
    core.handle_codex_event(run, {"type": "item.completed", "item": {
        "type": "command_execution", "exit_code": 0, "aggregated_output": "ok\n"}})
    core.handle_pi_event(run, {"type": "tool_execution_end", "toolName": "bash",
                               "result": {"content": [{"type": "text", "text": "done"}]}, "isError": False})
    core.handle_kimi_event(run, {"role": "tool", "tool_call_id": "t", "content": "1\tline"})
    core.handle_opencode_event(run, {"type": "tool_use", "sessionID": "s", "part": {
        "tool": "read", "state": {"status": "completed", "input": {}, "output": "text"}}})
    core.handle_deepagents_event(run, {"type": "tool_result", "name": "execute", "output": "x", "is_error": False})
    descs = [e["desc"] for e in bus if e["type"] == "tool" and e.get("kind") == "result"]
    assert descs == ["↩ 3 B · ok", "↩ 4 B · done", "↩ 6 B · 1\tline", "↩ 4 B · text", "↩ execute 1 B · x"]
