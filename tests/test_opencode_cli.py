"""opencode 2.x 兼容(services/runtime/opencode_cli.py + core / engine_setup 接线)。

假 CLI 按 2.1.2 实测输出造:`--version`、`api --standalone model.list`、`session export`、
`auth list / export`;事件样本取自 2.1.2 真实 `run --format json`(路径截短)。"""
import json
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from services.runtime import core, engine_setup as es, opencode_cli as oc

posix_only = pytest.mark.skipif(os.name == "nt", reason="posix shell")

MODEL_LIST = {"location": {"directory": "/w"}, "data": [
    {"id": "deepseek-v4-pro", "providerID": "opencode-go", "variants": [{"id": "high"}, {"id": "max"}]},
    {"id": "kimi-k2.7-code", "providerID": "opencode-go", "variants": []},
]}
EXPORT = {"info": {"id": "ses_ok"}, "messages": [
    {"id": "msg_u1", "type": "user", "text": "上一轮"},
    {"id": "msg_a0", "type": "assistant", "tokens": {"input": 9, "output": 1, "reasoning": 0,
                                                      "cache": {"read": 0, "write": 0}}, "cost": 0},
    {"id": "msg_i1", "type": "idle"},
    {"id": "msg_u2", "type": "user", "text": "本轮"},
    {"id": "msg_a1", "type": "assistant", "tokens": {"input": 5389, "output": 111, "reasoning": 0,
                                                      "cache": {"read": 0, "write": 0}}, "cost": 0},
    {"id": "msg_a2", "type": "assistant", "tokens": {"input": 3780, "output": 3, "reasoning": 0,
                                                      "cache": {"read": 1792, "write": 0}}, "cost": 0},
    {"id": "msg_i2", "type": "idle"},
]}


@pytest.fixture(autouse=True)
def _clear_caches():
    oc._VERSIONS.clear()
    oc._VARIANTS.clear()
    yield
    oc._VERSIONS.clear()
    oc._VARIANTS.clear()


def _fake(tmp_path: Path, version: str = "opencode v2.1.2", models: dict | None = MODEL_LIST) -> str:
    """假 opencode:按子命令回放 2.x 输出;每次调用的 argv 与 PWD 记进 calls.log。"""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "models.json").write_text(json.dumps(models) if models is not None else "")
    (tmp_path / "export.json").write_text(json.dumps(EXPORT))
    exe = tmp_path / "opencode"
    exe.write_text(f"""#!/bin/sh
echo "$PWD|$*" >> "{tmp_path}/calls.log"
case "$1" in
  --version) echo "{version}" ;;
  api) [ -s "{tmp_path}/models.json" ] || exit 1; cat "{tmp_path}/models.json" ;;
  session) if [ "$3" = "ses_ok" ]; then cat "{tmp_path}/export.json"; else echo "Session not found: $3" >&2; exit 1; fi ;;
  auth) if [ "$2" = "list" ]; then echo '[{{"id":"opencode-go","name":"OpenCode Go","connections":[{{"type":"credential","id":"cred_1"}}]}}]'
        else echo '[{{"integrationID":"github","active":true,"value":{{"type":"key","key":"gh"}}}},{{"integrationID":"opencode-go","active":true,"value":{{"type":"key","key":"sk-go"}}}}]'; fi ;;
esac
""")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return str(exe)


def _calls(tmp_path: Path) -> list[str]:
    p = tmp_path / "calls.log"
    return p.read_text().splitlines() if p.exists() else []


# ---------------------------------------------------------------- 版本探测
@posix_only
@pytest.mark.parametrize("out,major", [("1.18.23", 1), ("opencode v2.1.2", 2),
                                       ("opencode v0.0.0-beta-19507", 2), ("garbage", None)])
def test_major_version(tmp_path, out, major):
    exe = _fake(tmp_path, version=out)
    assert oc.major_version(exe) == major
    assert oc.is_v2(exe) is (major == 2)


@posix_only
def test_major_version_cached_until_binary_changes(tmp_path):
    exe = _fake(tmp_path, version="1.18.23")
    assert oc.major_version(exe) == 1 and oc.major_version(exe) == 1
    assert sum(c.endswith("|--version") for c in _calls(tmp_path)) == 1
    _fake(tmp_path, version="opencode v2.1.2 ")          # 升级覆盖了二进制:大小 / mtime 变了就重测
    assert oc.major_version(exe) == 2
    assert oc.major_version(str(tmp_path / "missing")) is None and oc.is_v2(None) is False


# ---------------------------------------------------------------- 推理档位
@posix_only
def test_model_ref_only_supported_variants(tmp_path):
    exe = _fake(tmp_path)
    assert oc.model_ref(exe, "opencode-go/deepseek-v4-pro", "high") == "opencode-go/deepseek-v4-pro#high"
    assert oc.model_ref(exe, "opencode-go/deepseek-v4-pro", "medium") == "opencode-go/deepseek-v4-pro"
    assert oc.model_ref(exe, "opencode-go/kimi-k2.7-code", "max") == "opencode-go/kimi-k2.7-code"
    assert oc.model_ref(exe, "opencode/unknown", "high") == "opencode/unknown"
    assert oc.model_ref(exe, "opencode-go/deepseek-v4-pro", "") == "opencode-go/deepseek-v4-pro"
    assert oc.model_ref(exe, "", "high") == ""                       # 没指定模型:无处挂档位
    assert oc.model_ref(exe, "opencode-go/deepseek-v4-pro#max", "high") == "opencode-go/deepseek-v4-pro#max"
    assert sum("|api --standalone model.list" in c for c in _calls(tmp_path)) == 1   # 档位表有缓存


@posix_only
def test_model_ref_without_table_never_adds_variant(tmp_path):
    exe = _fake(tmp_path, models=None)                               # api 调用失败
    assert oc.model_ref(exe, "opencode-go/deepseek-v4-pro", "high") == "opencode-go/deepseek-v4-pro"


# ---------------------------------------------------------------- 会话 / 用量 / 凭证
@posix_only
def test_session_exists(tmp_path):
    exe = _fake(tmp_path)
    assert oc.session_exists(exe, "ses_ok", cwd=tmp_path) is True
    assert oc.session_exists(exe, "ses_gone", cwd=tmp_path) is False
    assert oc.session_exists(str(tmp_path / "missing"), "ses_ok") is None
    assert all(c.startswith(f"{tmp_path}|") for c in _calls(tmp_path))   # PWD 对齐到 cwd


@posix_only
def test_missing_step_finishes_only_this_turn_unreported(tmp_path):
    exe = _fake(tmp_path)
    evs = oc.missing_step_finishes(exe, "ses_ok", ["msg_a1"])
    assert [e["part"]["messageID"] for e in evs] == ["msg_a2"]       # 上一轮的 msg_a0 不算
    assert evs[0]["synthetic"] and evs[0]["part"]["tokens"]["cache"]["read"] == 1792
    assert oc.missing_step_finishes(exe, "ses_gone", []) == []


@posix_only
def test_auth_list_and_go_key(tmp_path):
    exe = _fake(tmp_path)
    assert oc.auth_list(exe)[0]["id"] == "opencode-go"
    assert oc.go_key(exe) == "sk-go"


def test_child_env():
    env = oc.child_env({"PATH": "/bin"}, cwd="/w")
    assert env == {"PATH": "/bin", "NO_COLOR": "1", "OPENCODE_DISABLE_AUTOUPDATE": "1", "PWD": "/w"}


# ---------------------------------------------------------------- engine_setup 接线
@posix_only
def test_v2_login_status_and_login_line(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".local/share/opencode").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    exe = _fake(tmp_path)
    assert es.login_status("opencode", exe) == {"logged_in": True, "method": "opencode-go"}
    assert es.login_command_line("opencode", exe, windows=False) == f"{exe} auth login --standalone"
    # 2.x 不看 auth.json(升级导入后不再更新):凭证库为空即未登录,哪怕旧文件还在
    (home / ".local/share/opencode/auth.json").write_text(json.dumps({"opencode-go": {"type": "api"}}))
    empty = tmp_path / "e"
    empty.mkdir()
    exe2 = _fake(empty)
    (empty / "opencode").write_text((empty / "opencode").read_text().replace(
        '"connections":[{"type":"credential","id":"cred_1"}]', '"connections":[]'))
    assert es.login_status("opencode", exe2) == {"logged_in": False, "method": ""}
    v1 = _fake(tmp_path / "v1", version="1.18.23")
    assert es.login_status("opencode", v1) == {"logged_in": True, "method": "opencode-go"}   # 1.x 仍读 auth.json
    assert es.login_command_line("opencode", v1, windows=False) == f"{v1} auth login"


# ---------------------------------------------------------------- 事件流(2.1.2 实测样本)
SID = "ses_e9d8add75ffeVIMxEWW3Ti34xX"
V2_RUN = [
    {"type": "step_start", "sessionID": SID, "part": {"messageID": "msg_a1", "type": "step-start"}},
    {"type": "tool_use", "sessionID": SID, "part": {"messageID": "msg_a1", "type": "tool", "tool": "write",
     "state": {"status": "completed", "input": {"path": "/w/hello.txt", "content": "hi\n"},
               "output": "Created file successfully: hello.txt"}}},
    {"type": "step_finish", "sessionID": SID, "part": {"messageID": "msg_a1", "type": "step-finish",
     "reason": "tool-calls", "cost": 0, "tokens": {"input": 5389, "output": 111, "reasoning": 0,
                                                    "cache": {"read": 0, "write": 0}}}},
    {"type": "step_start", "sessionID": SID, "part": {"messageID": "msg_a2", "type": "step-start"}},
    {"type": "tool_use", "sessionID": SID, "part": {"messageID": "msg_a2", "type": "tool", "tool": "shell",
     "state": {"status": "completed", "input": {"command": "cat hello.txt"}, "output": "hi\n"}}},
    {"type": "step_finish", "sessionID": SID, "part": {"messageID": "msg_a2", "type": "step-finish",
     "reason": "tool-calls", "cost": 0, "tokens": {"input": 142, "output": 39, "reasoning": 0,
                                                    "cache": {"read": 5376, "write": 0}}}},
    # 末步只有 text、没有 step_finish(2.x 常态),宿主事后补一条 synthetic
    {"type": "text", "sessionID": SID, "part": {"messageID": "msg_a3", "type": "text", "text": "DONE"}},
]
SYNTH = {"type": "step_finish", "sessionID": SID, "synthetic": True, "part": {
    "messageID": "msg_a3", "type": "step-finish", "cost": 0,
    "tokens": {"input": 3780, "output": 3, "reasoning": 0, "cache": {"read": 1792, "write": 0}}}}


@pytest.fixture
def bus(monkeypatch):
    events = []
    monkeypatch.setattr(core.HUB, "publish", lambda ev: events.append(ev))
    monkeypatch.setattr(core, "publish_run", lambda run: events.append({"type": "run"}))
    return events


def test_v2_events(bus):
    run = {"id": "r1", "agent": "a", "project": "p"}
    for obj in V2_RUN + [SYNTH]:
        core.handle_opencode_event(run, obj)
    assert run["session_id"] == SID and run["result"] == "DONE"
    assert run["activity"][1] == "⚙ Bash: cat hello.txt"              # shell 归一为 Bash
    assert run["activity"][0].startswith("✎ write: ") and run["files"][0].endswith("hello.txt")
    assert run["_oc_steps"] == ["msg_a1", "msg_a2", "msg_a3"]
    assert run["tokens"] == 5500 + 5557 + 5575                         # 无 total 时分项求和
    err = {**run}
    core.handle_opencode_event(err, {"type": "error", "sessionID": SID, "error": {
        "type": "provider.no-route", "message": "Variant unavailable for opencode/x: max"}})
    assert err["error"] == "Variant unavailable for opencode/x: max"


def test_v2_usage_counts_synthetic_step(tmp_path):
    log = tmp_path / "r.jsonl"
    log.write_text("\n".join(json.dumps(o) for o in V2_RUN + [SYNTH]) + "\n")
    assert core._parse_run_usage(log) == {"in": 5389 + 142 + 5376 + 3780 + 1792, "out": 111 + 39 + 3}


# ---------------------------------------------------------------- 停止:先 SIGINT 再兜底杀组
@posix_only
def test_kill_proc_tree_sigint_first(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SIGINT_GRACE_S", 0.5)
    mark = tmp_path / "sigint"
    child = (f"import signal,time,pathlib\n"
             f"signal.signal(signal.SIGINT, lambda *a: pathlib.Path({str(mark)!r}).write_text('1'))\n"
             f"print('ready', flush=True)\n"
             f"time.sleep(30)\n")
    proc = subprocess.Popen([sys.executable, "-c", child], start_new_session=True, stdout=subprocess.PIPE)
    try:
        assert proc.stdout.readline().strip() == b"ready"
        proc._sigint_first = True
        core._kill_proc_tree(proc)
        time.sleep(0.2)
        assert mark.read_text() == "1" and proc.poll() is None        # 只收到 SIGINT,还活着
        assert proc.wait(timeout=5) == -signal.SIGKILL                 # 宽限期后整组被杀
        assert proc._sigint_first is False
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
