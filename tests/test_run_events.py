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


# ---------------------------------------------------------------- antigravity(agy stream-json,实测 1.4.3)
AGY_CID = "99f20100-0660-4c45-bb7f-524230bbffb6"
AGY_RUN = [   # 真实样本(init.tools 截短):跑一条 shell 命令后回复 done
    {"event": "init", "conversation_id": AGY_CID, "init": {
        "model": "gemini-3.8-flash-low", "cwd": "/w", "tools": ["run_command", "write_to_file"],
        "permission_mode": "always-proceed"}},
    {"event": "step_update", "step_update": {"conversation_id": AGY_CID, "step_index": 0,
                                             "state": "DONE", "step_type": "user_input"}},
    {"event": "step_update", "step_update": {
        "conversation_id": AGY_CID, "step_index": 1, "state": "DONE", "step_type": "agent_response",
        "duration_seconds": 4.38622, "usage": {"input_tokens": 6735, "output_tokens": 49,
                                               "thinking_tokens": 0, "cache_read_tokens": 0,
                                               "total_tokens": 6784}}},
    {"event": "step_update", "step_update": {
        "conversation_id": AGY_CID, "step_index": 2, "state": "ACTIVE", "step_type": "tool",
        "tool_name": "run_command", "tool_info": {"name": "run_command", "parameters": {
            "CommandLine": "echo hello-agy > out.txt && cat out.txt"}}}},
    {"event": "step_update", "step_update": {
        "conversation_id": AGY_CID, "step_index": 2, "state": "DONE", "step_type": "tool",
        "tool_name": "run_command", "duration_seconds": 0.256088, "tool_info": {
            "name": "run_command", "parameters": {"CommandLine": "echo hello-agy > out.txt && cat out.txt"},
            "output": "Command exited with code 0.\n\nCommand output:\nhello-agy\r\n"}}},
    {"event": "step_update", "step_update": {"conversation_id": AGY_CID, "step_index": 3,
                                             "state": "ACTIVE", "step_type": "agent_response",
                                             "text_delta": "done"}},
    {"event": "step_update", "step_update": {
        "conversation_id": AGY_CID, "step_index": 3, "state": "DONE", "step_type": "agent_response",
        "text_delta": "\n", "duration_seconds": 3.276829, "usage": {
            "input_tokens": 6873, "output_tokens": 1, "thinking_tokens": 0, "cache_read_tokens": 0,
            "total_tokens": 6874}}},
    {"event": "result", "result": {
        "conversation_id": AGY_CID, "status": "SUCCESS", "response": "done\n",
        "duration_seconds": 7.713853, "num_turns": 1, "usage": {
            "input_tokens": 13608, "output_tokens": 50, "thinking_tokens": 0,
            "cache_read_tokens": 0, "total_tokens": 13658}}},
]


def test_antigravity_stream(bus):
    run = _run()
    for obj in AGY_RUN:
        core.handle_antigravity_event(run, obj)
    assert run["session_id"] == AGY_CID
    assert [e["text"] for e in bus if e["type"] == "text"] == ["done", "\n"]   # DONE 带的是末段增量
    assert run["text"] == run["result"] == "done\n"
    assert run["activity"] == ["⚙ Bash: echo hello-agy > out.txt && cat out.txt"]   # ACTIVE/DONE 只登记一次
    receipts = [e["desc"] for e in bus if e["type"] == "tool" and e.get("kind") == "result"]
    assert receipts == ["↩ 56 B · Command exited with code 0."]
    assert run["tokens"] == 13658 and run["turns"] == 1
    assert "error" not in run and not run.get("_session_lost")


def test_antigravity_file_tools_and_errors(bus):
    run = _run()
    for i, (name, params) in enumerate([
            ("write_to_file", {"TargetFile": str(core.ROOT / "data/projects/p/a.md"), "CodeContent": "x"}),
            ("replace_file_content", {"TargetFile": "b.json"}),
            ("view_file", {"AbsolutePath": "agents/x/skills/s1/SKILL.md"}),
            ("grep_search", {"SearchPath": "/w", "Query": "TODO"})]):
        core.handle_antigravity_event(run, {"event": "step_update", "step_update": {
            "step_index": i, "state": "DONE", "step_type": "tool", "tool_name": name,
            "tool_info": {"name": name, "parameters": params, "output": "ok"}}})
    assert run["files"] == ["data/projects/p/a.md", "b.json"]   # 只有写/改算产物,绝对路径转相对 ROOT
    assert run["activity"][2] == "📖 Read: agents/x/skills/s1/SKILL.md"   # prompt_skill_read 按此匹配
    assert run["activity"][3] == "🔍 Grep: TODO"
    core.handle_antigravity_event(run, {"event": "step_update", "step_update": {
        "step_index": 9, "state": "DONE", "step_type": "tool", "tool_name": "run_command",
        "tool_info": {"name": "run_command", "parameters": {"CommandLine": "false"},
                      "error": {"type": "EXEC", "message": "exit status 1"}}}})
    assert bus[-2]["desc"] == "✗ 工具报错 · exit status 1"
    core.handle_antigravity_event(run, {"event": "result", "result": {
        "conversation_id": "c", "status": "ERROR", "error": "authentication required", "response": ""}})
    assert "result" not in run and run["error"] == "authentication required"


def test_antigravity_resume_silently_replaced(bus):
    """--conversation 指向已不存在的会话:agy 只打 warning、静默新开会话,init 里 id 不同。"""
    run = {**_run(), "_resume_sid": "old-id"}
    core.handle_antigravity_event(run, {"event": "init", "conversation_id": "new-id", "init": {}})
    assert run["_session_lost"] is True and "session_id" not in run
    ok = {**_run(), "_resume_sid": AGY_CID}
    core.handle_antigravity_event(ok, AGY_RUN[0])
    assert ok["session_id"] == AGY_CID and not ok.get("_session_lost")


def test_antigravity_usage_parse(tmp_path):
    full = tmp_path / "full.jsonl"
    full.write_text("\n".join(json.dumps(o) for o in AGY_RUN) + "\n")
    assert core._parse_run_usage(full) == {"in": 13608, "out": 50}
    cut = tmp_path / "cut.jsonl"          # 被停止/超时:无 result,逐步累加
    cut.write_text("\n".join(json.dumps(o) for o in AGY_RUN[:-1]) + "\n")
    assert core._parse_run_usage(cut) == {"in": 13608, "out": 50}
    # cache_read 若不含在 input_tokens 里,按 total_tokens 反推,不漏算
    assert core._agy_usage_io({"input_tokens": 100, "cache_read_tokens": 40, "output_tokens": 5,
                               "thinking_tokens": 7, "total_tokens": 152}) == (140, 12)


def test_antigravity_models_and_effort():
    out = ("Fetching available models...\n"
           "gemini-4-argon-high\tGemini 4 Argon (High)\n"
           "claude-opus-5-5-low\tClaude Opus 5.5 (Low)\n")
    models = core._parse_agy_models(out)
    assert [m["id"] for m in models] == ["gemini-4-argon-high", "claude-opus-5-5-low"]
    assert models[0]["name"] == "Gemini 4 Argon (High)"
    assert core.engine_effort_args("antigravity", "max") == ["--effort", "max"]
    assert core.engine_effort_args("antigravity", "") == []
    assert core.SMART_MODE_BY_ENGINE["antigravity"] == "smart_antigravity"
    assert core.AM_MODE_MODELS["smart_antigravity"]["high"]["model"] == "gemini-4-argon-high"
    assert core.CLI_ENV_VARS["antigravity"] == "AGY_BIN" and "antigravity" in core.ENGINES
