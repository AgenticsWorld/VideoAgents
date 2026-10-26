"""运行中插话(services/runtime/run_steer.py + core.api_steer / execute_run 接线)。

claude / pi 用假 CLI 走真实的 execute_run:假 claude 按 2.1.308 `-p --input-format stream-json
--replay-user-messages` 实测口径(stdin 一行一条 user 消息、取用时回放 isReplay、工具边界取用
排队的插话、出 result 后等下一条输入);假 pi 按 0.99.2 `--mode rpc` 文档口径(命令回执 response、
prompt+streamingBehavior=steer、agent_settled、读到 EOF 退出)。opencode 的 inbox 调用用假 CLI。"""
import asyncio
import json
import os
import stat
import sys
import time
import types
from pathlib import Path

import pytest

from services.runtime import core, opencode_cli as oc, run_steer

posix_only = pytest.mark.skipif(os.name == "nt", reason="posix select / shebang")
AG = "02-members/steer-tester"

_READER = r'''
import json, os, select, sys, time
BUF = [b""]
def read_line(timeout=None):
    while b"\n" not in BUF[0]:
        if timeout is not None:
            r, _, _ = select.select([0], [], [], timeout)
            if not r:
                return None
        chunk = os.read(0, 65536)
        if not chunk:
            rest, BUF[0] = BUF[0], b""
            return rest.decode() if rest else ""
        BUF[0] += chunk
    line, _, BUF[0] = BUF[0].partition(b"\n")
    return line.decode()
def out(o):
    sys.stdout.write(json.dumps(o, ensure_ascii=False) + "\n")
    sys.stdout.flush()
LOG = open(os.environ["FAKE_LOG"], "a", encoding="utf-8")
def log(kind, text):
    LOG.write(json.dumps({"kind": kind, "text": text}, ensure_ascii=False) + "\n")
    LOG.flush()
log("argv", " ".join(sys.argv[1:]))
'''

FAKE_CLAUDE = _READER + r'''
mode = os.environ.get("FAKE_MODE", "")
def replay(line):
    m = json.loads(line)["message"]
    log("user", m["content"])
    out({"type": "user", "message": m, "isReplay": True})
    return m["content"]
out({"type": "system", "subtype": "init", "session_id": "sess-fake"})
replay(read_line())
out({"type": "assistant", "message": {"id": "m1", "content": [
    {"type": "tool_use", "name": "Bash", "input": {"command": "sleep 1"}}]}})
time.sleep(float(os.environ.get("FAKE_TOOL_S", "1.5")))
out({"type": "user", "message": {"content": [{"type": "tool_result", "content": "STEP1"}]}})
got = []
while True:                       # 工具边界:取用已到的插话,插进本轮
    line = read_line(0)
    if not line:
        break
    got.append(replay(line))
time.sleep(float(os.environ.get("FAKE_FINAL_S", "0")))   # 本轮最后一次模型输出
out({"type": "result", "result": "DONE1" + "".join("|" + g for g in got), "session_id": "sess-fake",
     "total_cost_usd": 0.001, "num_turns": 2})
n = 1
while True:                       # 出 result 后等下一条输入,来一条跑一轮;EOF 退出
    line = read_line()
    if not line:
        break
    if mode == "ignore":
        continue
    n += 1
    out({"type": "result", "result": f"DONE{n}|" + replay(line), "session_id": "sess-fake",
         "total_cost_usd": 0.002, "num_turns": 3})
log("exit", "eof")
'''

FAKE_PI = _READER + r'''
steering = []
def handle(cmd, running):
    t, rid = cmd.get("type"), cmd.get("id")
    if t == "get_state":
        out({"id": rid, "type": "response", "command": t, "success": True, "data": {"sessionId": "pi-sess-1"}})
    elif t == "set_steering_mode":
        out({"id": rid, "type": "response", "command": t, "success": True})
    elif t == "extension_ui_response":
        log("ui", json.dumps(cmd))
    elif t == "prompt":
        if running and cmd.get("streamingBehavior") == "steer":
            steering.append(cmd["message"])
            out({"id": rid, "type": "response", "command": t, "success": True, "data": {"disposition": "queued"}})
        else:
            out({"id": rid, "type": "response", "command": t, "success": True, "data": {"disposition": "started"}})
            return cmd["message"]
    return None
def agent(msg):
    out({"type": "agent_start"})
    log("user", msg)
    out({"type": "message_end", "message": {"role": "user", "content": [{"type": "text", "text": msg}]}})
    if os.environ.get("FAKE_NET_ERR"):   # 首个请求连接中断,pi 自动重试后恢复
        out({"type": "message_end", "message": {"role": "assistant", "content": [], "stopReason": "error",
                                                "errorMessage": "Connection error."}})
        out({"type": "agent_end", "willRetry": True})
        out({"type": "auto_retry_start", "attempt": 1, "maxAttempts": 3, "delayMs": 2000,
             "errorMessage": "Connection error."})
        out({"type": "agent_start"})
    out({"type": "tool_execution_start", "toolName": "bash", "args": {"command": "sleep 1"}})
    if os.environ.get("FAKE_UI"):
        out({"type": "extension_ui_request", "id": "ui-1", "method": "confirm", "title": "ok?"})
    end = time.time() + float(os.environ.get("FAKE_TOOL_S", "1.5"))
    while time.time() < end:
        line = read_line(max(0.01, end - time.time()))
        if line:
            handle(json.loads(line), True)
    out({"type": "tool_execution_end", "result": {"content": [{"type": "text", "text": "STEP1"}]}, "isError": False})
    got = list(steering)
    steering.clear()
    for m in got:
        log("user", m)
        out({"type": "message_end", "message": {"role": "user", "content": [{"type": "text", "text": m}]}})
    text = "PIDONE" + "".join("|" + g for g in got)
    stop = os.environ.get("FAKE_STOP", "stop")   # length = 末条回复被单次输出上限截断
    out({"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                                            "usage": {"input": 10, "output": 2, "reasoning": 1}, "stopReason": stop}})
    out({"type": "agent_end"})
    out({"type": "agent_settled"})
while True:
    line = read_line()
    if not line:
        break
    msg = handle(json.loads(line), False)
    if msg:
        agent(msg)
log("exit", "eof")
'''


def _script(tmp_path: Path, name: str, body: str) -> str:
    p = tmp_path / name
    p.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return str(p)


def _loop_run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def rt(monkeypatch, tmp_path):
    """execute_run / api_steer 的最小运行环境:目录进 tmp,提示词 / 技能 / 收尾钩子打桩,api_chat 记录调用。"""
    for name in ("RUNS_DIR", "CHATS_DIR", "PROJECTS_DIR"):
        d = tmp_path / name.lower()
        d.mkdir()
        monkeypatch.setattr(core, name, d)
    # 起引擎前的项目快照 / rm 垫片都落在 tmp,不写进用户目录与真实运行时目录
    monkeypatch.setenv("VIDEOAGENTS_SNAPSHOT_DIR", str(tmp_path / "snapshots"))
    monkeypatch.setattr(core, "DELETE_GUARD_BIN_DIR", tmp_path / "delete-guard-bin")
    monkeypatch.setattr(core, "STATE", {"sessions": {}})
    monkeypatch.setattr(core, "save_state", lambda state: None)
    for name in ("RUNS", "RUN_TASKS", "RUN_PROCS", "RUN_STEER"):
        monkeypatch.setattr(core, name, {})
    events = []
    monkeypatch.setattr(core, "HUB", types.SimpleNamespace(publish=events.append))
    monkeypatch.setattr(core, "build_role_prompt", lambda *a, **k: "ROLE")
    monkeypatch.setattr(core, "agent_skill_prompt", lambda *a, **k: "")
    monkeypatch.setattr(core, "init_skill_records", lambda run: None)
    monkeypatch.setattr(core, "agent_dir", lambda agent: tmp_path)
    monkeypatch.setattr(core, "is_dispatcher_agent", lambda agent: False)
    monkeypatch.setattr(core, "is_stateless_agent", lambda agent: False)
    monkeypatch.setattr(core, "agent_run_limit", lambda *a, **k: 1)
    monkeypatch.setattr(core, "agent_sem", lambda agent, limit: asyncio.Semaphore(1))
    monkeypatch.setattr(core, "global_sem", lambda: asyncio.Semaphore(8))
    monkeypatch.setattr(core, "agent_memory_enabled", lambda: True)
    monkeypatch.setattr(core, "run_timeout_setting", lambda: 60)
    monkeypatch.setattr(core, "idle_timeout_setting", lambda: 30)
    monkeypatch.setattr(core, "thinking_effort_setting", lambda: "")
    monkeypatch.setattr(core, "max_turns_setting", lambda: 50)
    monkeypatch.setattr(core, "ui_lang_code", lambda *a: "zh")
    monkeypatch.setattr(core, "_prompt_skill_postcheck", lambda run: None)
    monkeypatch.setattr(core, "_keypoints_postcheck", lambda run: None)
    monkeypatch.setattr(core, "_canvas_motion_after_run", lambda run: None)
    monkeypatch.setattr(core, "issue_feedback_enabled", lambda: False)

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(core, "_nsfw_refusal_postcheck", _noop)
    monkeypatch.setattr(core, "ensure_human_gate_approvals", _noop)
    chats = []

    async def _chat(body):
        chats.append(body)
        return {"run_id": f"new{len(chats)}"}
    monkeypatch.setattr(core, "api_chat", _chat)
    log = tmp_path / "fake.log"
    monkeypatch.setenv("FAKE_LOG", str(log))
    ns = types.SimpleNamespace(core=core, events=events, chats=chats, tmp=tmp_path)
    ns.log = lambda: [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
    ns.chat = lambda: [json.loads(x) for x in core.chat_path(AG, "demo").read_text(encoding="utf-8").splitlines()]
    return ns


def _run(engine: str, **kw) -> dict:
    run = {"id": f"r{time.time_ns() % 10**9}", "agent": AG, "agent_name": "tester", "source": "user",
           "parent": None, "project": "demo", "status": "queued", "created": time.time(),
           "message": "TASK", "engine": engine, "model": "", **kw}
    core.RUNS[run["id"]] = run
    return run


async def _until(cond, timeout=10.0):
    t0 = time.time()
    while not cond():
        if time.time() - t0 > timeout:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.02)


def _steer(text):
    return core.api_steer({"agent": AG, "project": "demo", "message": text})


# ---------------------------------------------------------------- claude
@posix_only
def test_claude_steer_lands_mid_turn(rt, monkeypatch):
    monkeypatch.setattr(core, "resolve_cli_executable", lambda e: _script(rt.tmp, "claude", FAKE_CLAUDE))
    monkeypatch.setenv("FAKE_TOOL_S", "1.5")
    run = _run("claude")

    async def go():
        task = asyncio.create_task(core.execute_run(run, "TASK", None))
        await _until(lambda: run.get("activity"))          # 工具在跑
        res = await _steer("ALSO-BANANA")
        assert res == {"ok": True, "mode": "steer", "run_id": run["id"]}
        await task
    _loop_run(go())
    assert run["status"] == "done"
    assert run["result"].startswith("DONE1|【用户插话】") and "ALSO-BANANA" in run["result"]
    assert run["steers"][0]["delivered"] and run["steers"][0]["via"] == "run"
    argv = rt.log()[0]["text"]
    assert "--input-format stream-json" in argv and "--replay-user-messages" in argv and "TASK" not in argv
    assert [e["text"] for e in rt.log() if e["kind"] == "user"][0] == "TASK"   # 提示词走 stdin
    assert rt.log()[-1] == {"kind": "exit", "text": "eof"}                       # 收尾关 stdin,CLI 正常退出
    assert rt.chats == []                                                      # 没有补派
    steer_entries = [c for c in rt.chat() if c.get("steer")]
    assert steer_entries == [{**steer_entries[0], "role": "user", "text": "ALSO-BANANA",
                              "run_id": run["id"], "steer": "run"}]
    assert core.RUN_STEER == {}


@posix_only
def test_claude_steer_after_last_tool_runs_as_next_turn(rt, monkeypatch):
    """插话撞上本轮最后一次模型输出(没有工具边界):CLI 出 result 后把它当下一轮跑,两轮回复都留下。"""
    monkeypatch.setattr(core, "resolve_cli_executable", lambda e: _script(rt.tmp, "claude", FAKE_CLAUDE))
    monkeypatch.setenv("FAKE_TOOL_S", "0.2")
    monkeypatch.setenv("FAKE_FINAL_S", "2.0")
    run = _run("claude")

    async def go():
        task = asyncio.create_task(core.execute_run(run, "TASK", None))
        await _until(lambda: run.get("activity"))
        await asyncio.sleep(0.8)                        # 工具已结束,模型在出最后一段
        assert (await _steer("LATE"))["mode"] == "steer"
        await task
    _loop_run(go())
    assert run["status"] == "done" and run["result"].startswith("DONE1\n\nDONE2|【用户插话】")
    assert run["steers"][0]["delivered"] and rt.chats == []


@posix_only
def test_claude_unconsumed_steer_is_requeued(rt, monkeypatch):
    """CLI 出 result 后迟迟不取用插话:宽限期到即关 stdin 收尾,未送达的补派一单。"""
    monkeypatch.setattr(core, "resolve_cli_executable", lambda e: _script(rt.tmp, "claude", FAKE_CLAUDE))
    monkeypatch.setattr(run_steer, "CLAUDE_CLOSE_GRACE_S", 1)
    monkeypatch.setenv("FAKE_MODE", "ignore")
    monkeypatch.setenv("FAKE_TOOL_S", "0.2")
    monkeypatch.setenv("FAKE_FINAL_S", "1.5")
    run = _run("claude")

    async def go():
        task = asyncio.create_task(core.execute_run(run, "TASK", None))
        await _until(lambda: run.get("activity"))
        await asyncio.sleep(0.6)
        assert (await _steer("NEVER-READ"))["mode"] == "steer"
        await task
    t0 = time.time()
    _loop_run(go())
    assert time.time() - t0 < 10
    assert run["status"] == "done" and run["result"] == "DONE1"
    assert run["steers"][0]["via"] == "requeued" and not run["steers"][0]["delivered"]
    assert len(rt.chats) == 1
    body = rt.chats[0]
    assert body["steer"] == "requeued" and body["engine"] == "claude" and body["agent"] == AG
    assert body["message"] == run_steer.requeue_message(["NEVER-READ"])


@posix_only
def test_pending_steers_fold_into_prompt_at_spawn(rt, monkeypatch):
    """排队期间 / 进程起来之前收下的插话:起进程时并进提示词。"""
    monkeypatch.setattr(core, "resolve_cli_executable", lambda e: _script(rt.tmp, "claude", FAKE_CLAUDE))
    monkeypatch.setenv("FAKE_TOOL_S", "0.1")
    run = _run("claude", steers=[{"text": "EXTRA-1", "ts": 1, "delivered": False},
                                 {"text": "EXTRA-2", "ts": 2, "delivered": False}])
    _loop_run(core.execute_run(run, "TASK", None))
    first = [e["text"] for e in rt.log() if e["kind"] == "user"][0]
    assert first == run_steer.fold_steers("TASK", ["EXTRA-1", "EXTRA-2"])
    assert all(i["delivered"] and i["via"] == "prompt" for i in run["steers"])
    assert run["status"] == "done" and rt.chats == []


def test_spawn_file_not_found_appends_system_error(rt, monkeypatch):
    """解析已过、起进程仍 FileNotFoundError(杀毒软件扫描等瞬态故障):报未安装并附系统原始错误。"""
    monkeypatch.setattr(core, "resolve_cli_executable", lambda e: str(rt.tmp / "gone" / "claude"))
    run = _run("claude")
    _loop_run(core.execute_run(run, "TASK", None))
    assert run["status"] == "error"
    assert run["error"].startswith(core.cli_not_found_error("claude")) and "（系统返回：" in run["error"]


# ---------------------------------------------------------------- pi(rpc)
@posix_only
def test_pi_rpc_steer_and_session_from_get_state(rt, monkeypatch):
    monkeypatch.setattr(core, "resolve_cli_executable", lambda e: _script(rt.tmp, "pi", FAKE_PI))
    monkeypatch.setenv("FAKE_TOOL_S", "1.5")
    monkeypatch.setenv("FAKE_UI", "1")
    run = _run("pi")

    async def go():
        task = asyncio.create_task(core.execute_run(run, "TASK", None))
        await _until(lambda: run.get("activity"))
        assert (await _steer("PI-EXTRA"))["mode"] == "steer"
        await task
    _loop_run(go())
    assert run["status"] == "done", run.get("error")
    assert run["result"].startswith("PIDONE|【用户插话】") and "PI-EXTRA" in run["result"]
    assert run["session_id"] == "pi-sess-1" and core.STATE["sessions"][f"pi::{AG}::demo"] == "pi-sess-1"
    assert run["steers"][0]["delivered"] and run["steers"][0]["via"] == "run"
    log = rt.log()
    assert "--mode rpc" in log[0]["text"] and "TASK" not in log[0]["text"]
    assert {"kind": "ui", "text": json.dumps({"type": "extension_ui_response", "id": "ui-1",
                                              "cancelled": True})} in log
    assert log[-1] == {"kind": "exit", "text": "eof"} and rt.chats == []


@posix_only
def test_pi_truncated_last_reply_fails_run(rt, monkeypatch):
    """末条回复 stopReason=length:pi 退出码 0 也判失败,错误写明截断与用量。"""
    monkeypatch.setattr(core, "resolve_cli_executable", lambda e: _script(rt.tmp, "pi", FAKE_PI))
    monkeypatch.setenv("FAKE_TOOL_S", "0.1")
    monkeypatch.setenv("FAKE_STOP", "length")
    run = _run("pi")
    _loop_run(core.execute_run(run, "TASK", None))
    assert run["status"] == "error"
    assert run["error"].startswith("输出被截断") and "stopReason=length" in run["error"]
    assert "输出 2 token,其中思考 1" in run["error"] and "pi 自身的模型配置" in run["error"]
    assert "_pi_truncated" not in run


def test_pi_truncation_cleared_by_later_reply():
    """截断后模型又回了一条(截断的工具调用被 pi 回错误结果后重来):不再算截断。"""
    run = {"id": "r1", "agent": AG}
    end = lambda stop: {"type": "message_end", "message": {   # noqa: E731
        "role": "assistant", "content": [], "usage": {"output": 5}, "stopReason": stop}}
    core.handle_pi_event(run, end("length"))
    assert run["_pi_truncated"] == {"output": 5}
    core.handle_pi_event(run, end("toolUse"))
    assert "_pi_truncated" not in run
    core.handle_pi_event(run, end("length"))
    core.handle_pi_event(run, {"type": "message_end", "message": {"role": "user", "content": []}})
    assert "_pi_truncated" in run                       # 用户消息不清
    assert "error" not in run                           # 截断本身不在事件处理时记错,收尾再判


@posix_only
def test_pi_truncation_overrides_recovered_request_error(rt, monkeypatch):
    """中途一次请求出错(pi 自动重试已恢复)、末条又被截断:错误以截断为准。"""
    monkeypatch.setattr(core, "resolve_cli_executable", lambda e: _script(rt.tmp, "pi", FAKE_PI))
    monkeypatch.setenv("FAKE_TOOL_S", "0.1")
    monkeypatch.setenv("FAKE_STOP", "length")
    run = _run("deepagents", error="Connection error.")
    monkeypatch.setattr(core, "agentics_preflight", lambda model="": {
        "da": {"provider": "agentics"}, "model": "m", "reason": "", "error": ""})
    monkeypatch.setattr(core, "agentics_pi_command", lambda: [_script(rt.tmp, "pi", FAKE_PI)])
    monkeypatch.setattr(core, "agentics_vision", lambda da, model: False)
    monkeypatch.setattr(core, "agentics_pi_launch", lambda *a, **k: (["--mode", "json"], {}))
    monkeypatch.setattr(core, "AGENTICS_PI_HOME", rt.tmp / "agentics-pi")
    _loop_run(core.execute_run(run, "TASK", None))
    assert run["status"] == "error" and run["error"].startswith("输出被截断")
    assert "AGENTICS_MAX_OUTPUT_TOKENS" in run["error"]


@posix_only
def test_agentics_run_declares_vision_from_channel(rt, monkeypatch):
    """Agentics 派单按 agentics_vision 判定把 vision 传给 agentics_pi_launch(2026-10-26 前派单一律纯文本,
    pi 读图只回「Current model does not support images」)。"""
    monkeypatch.setattr(core, "resolve_cli_executable", lambda e: _script(rt.tmp, "pi", FAKE_PI))
    monkeypatch.setenv("FAKE_TOOL_S", "0.1")
    da = {"provider": "agentics", "base_url": "https://x/api/v1", "api_key": "jwt", "model": "deepseek/v"}
    monkeypatch.setattr(core, "agentics_preflight", lambda model="": {
        "da": da, "model": "deepseek/v", "reason": "", "error": ""})
    monkeypatch.setattr(core, "agentics_pi_command", lambda: [_script(rt.tmp, "pi", FAKE_PI)])
    asked, launched = [], []
    monkeypatch.setattr(core, "agentics_vision", lambda d, model: asked.append((d, model)) or True)
    monkeypatch.setattr(core, "agentics_pi_launch",
                        lambda *a, **k: launched.append(k) or (["--mode", "json"], {}))
    monkeypatch.setattr(core, "AGENTICS_PI_HOME", rt.tmp / "agentics-pi")
    run = _run("deepagents")
    _loop_run(core.execute_run(run, "TASK", None))
    assert run["status"] == "done"
    assert asked == [(da, "deepseek/v")]
    assert launched and launched[0]["vision"] is True


@posix_only
def test_pi_request_error_recovered_by_retry_run_done(rt, monkeypatch):
    """中途一次请求 Connection error.、pi 自动重试后跑完:以 done 结单,不留请求错误。"""
    monkeypatch.setattr(core, "resolve_cli_executable", lambda e: _script(rt.tmp, "pi", FAKE_PI))
    monkeypatch.setenv("FAKE_TOOL_S", "0.1")
    monkeypatch.setenv("FAKE_NET_ERR", "1")
    run = _run("pi")
    _loop_run(core.execute_run(run, "TASK", None))
    assert run["status"] == "done", run.get("error")
    assert run["result"] == "PIDONE" and "error" not in run and "_pi_request_error" not in run
    assert {"type": "progress", "run_id": run["id"], "agent": AG,
            "note": "🔁 请求失败,自动重试 1/3 · Connection error."} in rt.events


def test_pi_request_error_cleared_only_after_success():
    """请求错误:之后有回复正常返回才清;重试用尽(末条仍 error)保留;别处记的错误不清。"""
    run = {"id": "r1", "agent": AG}
    end = lambda stop, err=None: {"type": "message_end", "message": {   # noqa: E731
        "role": "assistant", "content": [], "stopReason": stop, "errorMessage": err}}
    core.handle_pi_event(run, end("error", "Connection error."))
    core.handle_pi_event(run, {"type": "message_end", "message": {"role": "user", "content": []}})
    assert run["error"] == "Connection error."           # 用户消息不算恢复
    core.handle_pi_event(run, end("toolUse"))
    assert "error" not in run and "_pi_request_error" not in run
    for _ in range(4):                                   # 首次 + 3 次重试全失败
        core.handle_pi_event(run, end("error", "500 Internal Server Error"))
    core.handle_pi_event(run, {"type": "auto_retry_end", "success": False, "attempt": 3,
                               "finalError": "500 Internal Server Error"})
    assert run["error"] == "500 Internal Server Error"
    core.handle_pi_event(run, end("aborted"))
    assert run["error"] == "pi request aborted"
    run = {"id": "r2", "agent": AG}
    core.handle_pi_event(run, {"id": run_steer.PI_PROMPT_ID, "type": "response", "command": "prompt",
                               "success": False, "error": "model unavailable"})
    core.handle_pi_event(run, end("stop"))
    assert run["error"] == "model unavailable"           # 提示词被拒不是请求错误,不清


class _Stdin:
    def __init__(self):
        self.lines, self.closed = [], False

    def is_closing(self):
        return self.closed

    def write(self, data):
        self.lines.append(json.loads(data))

    async def drain(self):
        pass

    def close(self):
        self.closed = True


def _pi_channel():
    return run_steer.PiSteer(types.SimpleNamespace(stdin=_Stdin()))


def test_pi_channel_waits_for_steer_reply_across_settle():
    """插话命令写出时 pi 恰好收尾(agent_settled 先到):等回执;started 则跑完下一轮再关 stdin。"""
    ch = _pi_channel()
    item = {"text": "X", "delivered": False}
    assert _loop_run(ch.steer(item))
    assert ch.proc.stdin.lines[-1] == {"id": "va-steer-1", "type": "prompt",
                                       "message": run_steer.steer_message("X"), "streamingBehavior": "steer"}
    ch.on_event({"type": "agent_settled"})
    assert ch.open and not ch.proc.stdin.closed
    ch.on_event({"id": "va-steer-1", "type": "response", "command": "prompt", "success": True,
                 "data": {"disposition": "started"}})
    assert ch.open
    ch.on_event({"type": "agent_start"})
    assert ch.on_event({"type": "message_end", "message": {"role": "user", "content": [
        {"type": "text", "text": run_steer.steer_message("X")}]}}) == []   # 第一条 user = 本单提示词
    ch.on_event({"type": "agent_settled"})
    assert ch.proc.stdin.closed and not ch.open
    assert not _loop_run(ch.steer({"text": "Y", "delivered": False}))     # 已收尾:不再收


def test_pi_channel_prompt_rejected_and_steer_rejected():
    ch = _pi_channel()
    _loop_run(ch.start("TASK"))
    assert [c["type"] for c in ch.proc.stdin.lines] == ["set_steering_mode", "get_state", "prompt"]
    item = {"text": "X", "delivered": False}
    _loop_run(ch.steer(item))
    ch.on_event({"id": "va-steer-1", "type": "response", "command": "prompt", "success": False, "error": "nope"})
    assert item.get("failed") and not item["delivered"]
    ch.on_event({"id": run_steer.PI_PROMPT_ID, "type": "response", "command": "prompt", "success": False,
                 "error": "Model not found"})
    assert ch.proc.stdin.closed
    run = {"id": "r", "agent": AG}
    core.handle_pi_event(run, {"id": run_steer.PI_PROMPT_ID, "type": "response", "command": "prompt",
                               "success": False, "error": "Model not found"})
    core.handle_pi_event(run, {"id": run_steer.PI_STATE_ID, "type": "response", "command": "get_state",
                               "success": True, "data": {"sessionId": "s-9"}})
    assert run == {"id": "r", "agent": AG, "error": "Model not found", "session_id": "s-9"}


def test_claude_results_accumulate():
    run = {"id": "r", "agent": AG}
    core.handle_claude_event(run, {"type": "result", "result": "A", "session_id": "s"})
    core.handle_claude_event(run, {"type": "result", "result": "B", "session_id": "s"})
    assert run["result"] == "A\n\nB"


def test_pi_rpc_args():
    assert core._pi_rpc_args(["node", "x", "--mode", "json", "--approve"]) == ["node", "x", "--mode", "rpc",
                                                                             "--approve"]


# ---------------------------------------------------------------- api_steer 去向
def test_api_steer_routes(rt):
    def go(text):
        return _loop_run(_steer(text))

    assert go("hello")["mode"] == "new" and rt.chats[-1]["steer"] is None          # 没有在跑的
    assert go("/stop all")["mode"] == "new" and rt.chats[-1]["message"] == "/stop all"
    q1 = _run("codex", created=1.0)
    q2 = _run("codex", created=2.0)
    res = go("MERGE-ME")
    assert res == {"ok": True, "mode": "merged", "run_id": q2["id"]}
    assert q2["steers"][0]["text"] == "MERGE-ME" and "steers" not in q1
    assert rt.chat()[-1]["steer"] == "merged"
    r = _run("codex", status="running")
    res = go("CODEX")                                    # 进程还没起来:挂起,起进程时并进提示词
    assert res["mode"] == "steer" and r["steers"][-1]["text"] == "CODEX"
    r.update(_steer_spawned=True, _steerable=False)
    n = len(rt.chats)
    res = go("CODEX2")                                   # 不支持的引擎:并进排队中的单
    assert res["mode"] == "merged" and len(rt.chats) == n
    q1["status"] = q2["status"] = "done"
    res = go("CODEX3")                                   # 也没有排队的:照常新建并注明原因
    assert res["mode"] == "queued" and res["reason"] == "unsupported"
    assert rt.chats[-1]["steer"] == "queued" and rt.chats[-1]["steer_reason"] == "unsupported"
    r2 = _run("claude", status="running", _steer_spawned=True, _steerable=True)
    assert go("TWO")["reason"] == "multiple"
    r2["status"] = "done"
    ch = run_steer.SteerChannel()
    ch.open = False
    core.RUN_STEER[r["id"]] = ch
    assert go("FIN")["reason"] == "finishing"


def test_requeue_on_stop_only_notes(rt):
    run = _run("claude", status="error", stopped="user")
    _loop_run(core._requeue_steers(run, [{"text": "LOST", "delivered": False}]))
    assert rt.chats == []
    note = rt.chat()[-1]
    assert note["role"] == "assistant" and "没有送达" in note["text"] and "LOST" in note["text"]


# ---------------------------------------------------------------- opencode 2.x(inbox)
FAKE_OC = r'''#!/bin/sh
echo "$*" >> "{d}/oc.log"
case "$4" in
  */prompt) echo '{{"data":{{"id":"msg_steer1","type":"user","delivery":"steer"}}}}' ;;
  */inbox) cat "{d}/inbox.json" ;;
  */inbox/*) echo '{{}}' ;;
  *) [ "$1" = "session" ] && cat "{d}/export.json" ;;
esac
'''


@pytest.fixture
def fake_oc(tmp_path):
    exe = tmp_path / "opencode"
    exe.write_text(FAKE_OC.format(d=tmp_path))
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    (tmp_path / "inbox.json").write_text('{"data":[]}')
    return str(exe)


@posix_only
def test_opencode_cli_steer_helpers(fake_oc, tmp_path):
    assert oc.steer_prompt(fake_oc, "ses_1", "插话", None, tmp_path) == "msg_steer1"
    assert oc.inbox_ids(fake_oc, "ses_1", None, tmp_path) == set()
    (tmp_path / "inbox.json").write_text('{"data":[{"id":"msg_steer1","type":"user"}]}')
    assert oc.inbox_ids(fake_oc, "ses_1", None, tmp_path) == {"msg_steer1"}
    assert oc.inbox_cancel(fake_oc, "ses_1", "msg_steer1", None, tmp_path)
    calls = (tmp_path / "oc.log").read_text().splitlines()
    assert calls[0].startswith("api --standalone POST /api/session/ses_1/prompt -d ")
    body = json.loads(calls[0].split(" -d ", 1)[1])
    assert body == {"text": "插话", "delivery": "steer", "resume": False}
    assert calls[-1] == "api --standalone DELETE /api/session/ses_1/inbox/msg_steer1"


@posix_only
def test_opencode_missing_steps_skip_steer_user_messages(fake_oc, tmp_path):
    (tmp_path / "export.json").write_text(json.dumps({"messages": [
        {"id": "u1", "type": "user"},
        {"id": "a1", "type": "assistant", "tokens": {"input": 1, "output": 1}},
        {"id": "msg_steer1", "type": "user"},
        {"id": "a2", "type": "assistant", "tokens": {"input": 2, "output": 2}},
    ]}))
    got = oc.missing_step_finishes(fake_oc, "ses_1", ["a2"], None, tmp_path, ["msg_steer1"])
    assert [e["part"]["messageID"] for e in got] == ["a1"]       # 起点是本轮提示词,不是插话
    assert oc.missing_step_finishes(fake_oc, "ses_1", ["a2"], None, tmp_path) == []


def test_opencode_channel_buffers_until_session_and_cancels_undelivered(monkeypatch):
    posted, cancelled = [], []

    def steer_prompt(exe, sid, text, env, cwd):
        posted.append((sid, text))
        return f"msg_{len(posted)}"
    monkeypatch.setattr(oc, "steer_prompt", steer_prompt)
    monkeypatch.setattr(oc, "inbox_ids", lambda exe, sid, env, cwd: {"msg_2"})
    monkeypatch.setattr(oc, "inbox_cancel", lambda exe, sid, iid, env, cwd: cancelled.append(iid) or True)
    ch = run_steer.OpencodeSteer("opencode", {}, "/w")
    a, b = {"text": "A", "delivered": False}, {"text": "B", "delivered": False}

    async def go():
        assert await ch.steer(a)                       # 会话 id 还没到:先挂着
        assert posted == []
        ch.on_event({"type": "step_start", "sessionID": "ses_x"})
        assert await ch.steer(b)
        await ch.finalize()
    _loop_run(go())
    assert posted == [("ses_x", run_steer.steer_message("A")), ("ses_x", run_steer.steer_message("B"))]
    assert a["delivered"] and a["ref"] == "msg_1"
    assert b.get("failed") and not b["delivered"] and cancelled == ["msg_2"]   # 会话空闲后才写入:撤回
    assert not ch.open
