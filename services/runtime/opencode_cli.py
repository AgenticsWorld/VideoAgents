"""opencode CLI 2.x 兼容层(2026-10-22,实测 1.18.23 / 2.1.2)。

OpenCode 2(2026-09 起;官方安装脚本 opencode.ai/v2/install、npm 包 @opencode/cli,旧脚本
拒装 2.x)与 1.x 对接口径的差异。按 `opencode --version` 的主版本分流,1.x 行为不变:

- 默认把命令交给常驻后台服务(`opencode serve --service`,首次调用自动拉起、宿主退出后仍在),
  杀掉客户端时服务里的会话照跑不误;派单 / 列模型 / 查会话一律加 --standalone(私有子服务器,
  stdin 断开即退出,与客户端同一进程组)。
- 托管后台服务启动时会续跑「执行中途被杀」的会话(插一句 "The server restarted while you were
  working" 后接着调工具,每轮最多 10 次):SIGKILL 不释放执行占用,用户下次开 TUI 或跑任何
  不带 --standalone 的命令(含 auth login)就复活。停止时先只给客户端发 SIGINT,由它在私有
  服务器上正式中断会话(实测 0.1s 退出、会话记 interrupted、不再续跑),再兜底杀组
  (core._kill_proc_tree);事后再调 session.interrupt 无效(新起的服务器里没有在跑的执行)。
- run 不认 --variant:推理档位写进 -m provider/model#variant,模型没有该档位直接报
  「Variant unavailable」(1.x 静默忽略)→ 先查档位表,只拼模型支持的档位。
- models 不认 --refresh,也不再写 ~/.cache/opencode/models.json 快照(每次现拉目录)。
- --session 指向不存在的会话时静默新建同 id 的空会话(1.x 报 Session not found,宿主据此
  带完整角色重跑)→ 续接前用 session export 预检。
- stdin 不是终端时 run 先读完 stdin 再发提示词:继承来的管道不关就永远卡住 → stdin 接 DEVNULL。
- 工作目录取环境变量 PWD 而非进程 cwd(宿主启动目录 ≠ cwd 时整单跑错目录)→ PWD 置为 cwd。
- 凭证存 opencode.db 的 credential 表(1.x 的 auth.json 升级时导入一次,文件保留但不再更新)
  → 登录状态读 auth list,Go 订阅 Key 读 auth export。
- 内置工具改名 bash→shell、task→subagent,文件参数 filePath→path。
- JSON 事件流每轮最后一步常缺 step_finish(客户端 session.wait 返回后不再消费事件)
  → 结束后按 session export 里本轮 assistant 消息的 tokens 补记。
- 1.x 升级上来时 opencode.db 原地迁移,会话 id 保留,已记录的会话照常续接。
- 运行中插话(run_steer):另起 `api --standalone` 往会话 inbox 写 delivery=steer、resume=false
  的输入,正在跑的 --standalone 进程在下一步边界取走;会话已空闲才写进去的用 inbox.cancel 撤回。
  (另一条路是自起 `opencode serve` + `run --server`,但服务端须设 OPENCODE_PASSWORD 否则
  客户端报 Authentication required;没采用,沿用 --standalone。)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

VERSION_TIMEOUT_S = 15
CALL_TIMEOUT_S = 30
VARIANTS_TTL_S = 600          # 档位表随模型目录变化,十分钟足够新
VARIANTS_RETRY_S = 60         # 读失败时一分钟后再试,期间按无档位处理

# 2.x 内置工具名 → claude 同义工具名(core.tool_summary 按 claude 命名归类活动)
TOOL_ALIASES = {"shell": "Bash", "subagent": "Task"}

_LOCK = threading.Lock()
_VERSIONS: dict[tuple, int | None] = {}
_VARIANTS: dict[str, tuple[float, dict[str, list[str]]]] = {}


def child_env(base: dict | None = None, cwd: str | Path | None = None) -> dict:
    """opencode 子进程环境:禁颜色 / 自动更新;给了 cwd 就把 PWD 对齐(2.x 按 PWD 定工作目录)。"""
    env = dict(os.environ if base is None else base)
    env["NO_COLOR"] = "1"
    env["OPENCODE_DISABLE_AUTOUPDATE"] = "1"
    if cwd:
        env["PWD"] = str(cwd)
    return env


def _call(exe: str, args: list[str], env: dict | None = None, cwd: str | Path | None = None,
          timeout: float = CALL_TIMEOUT_S, keep_stdout: bool = True
          ) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            [exe, *args], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if keep_stdout else subprocess.DEVNULL,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, env=child_env(env, cwd), cwd=str(cwd) if cwd else None)
    except (OSError, subprocess.SubprocessError):
        return None


def major_version(exe: str | None) -> int | None:
    """`opencode --version` 的主版本:1.x 输出 "1.18.23",2.x 输出 "opencode v2.1.2";
    2.x 的 beta / dev 构建版本号是 0.0.0-beta-N,按 2 计。读不出返回 None(调用方按 1.x 老口径)。
    按路径 + mtime + 大小缓存:升级覆盖二进制(或 npm 重写 .cmd 垫片)后自动重测。"""
    if not exe:
        return None
    try:
        st = Path(exe).stat()
    except OSError:
        return None
    key = (exe, st.st_mtime_ns, st.st_size)
    with _LOCK:
        if key in _VERSIONS:
            return _VERSIONS[key]
    r = _call(exe, ["--version"], timeout=VERSION_TIMEOUT_S)
    if r is None:                 # 起不来 / 超时:不缓存,下次再测
        return None
    m = re.search(r"(\d+)\.\d+\.\d+", r.stdout or "") if r.returncode == 0 else None
    major = None if not m else (2 if int(m.group(1)) == 0 else int(m.group(1)))
    with _LOCK:
        _VERSIONS[key] = major
    return major


def is_v2(exe: str | None) -> bool:
    return (major_version(exe) or 1) >= 2


def model_variants(exe: str, env: dict | None = None, cwd: str | Path | None = None
                   ) -> dict[str, list[str]]:
    """{"provider/model": [档位 id…]},读 `api --standalone model.list`(与 `models` 同一份目录)。
    读不到返回 {}——调用方据此不拼档位,宁可用模型默认档也不冒报错的风险。"""
    now = time.time()
    with _LOCK:
        hit = _VARIANTS.get(exe)
        if hit and now - hit[0] < VARIANTS_TTL_S:
            return hit[1]
    table: dict[str, list[str]] = {}
    r = _call(exe, ["api", "--standalone", "model.list"], env, cwd)
    if r and r.returncode == 0:
        try:
            data = json.loads(r.stdout).get("data") or []
        except (ValueError, AttributeError):
            data = []
        for m in data:
            if isinstance(m, dict) and m.get("providerID") and m.get("id"):
                table[f"{m['providerID']}/{m['id']}"] = [
                    str(v.get("id") if isinstance(v, dict) else v) for v in (m.get("variants") or [])]
    with _LOCK:
        _VARIANTS[exe] = (now if table else now - VARIANTS_TTL_S + VARIANTS_RETRY_S, table)
    return table


def model_ref(exe: str, model: str | None, level: str, env: dict | None = None,
              cwd: str | Path | None = None) -> str:
    """2.x 的 -m 取值:模型支持该推理档位时拼成 model#level,否则原样(= 1.x 对未知 variant
    静默忽略的口径)。未指定模型时无处挂档位,也原样返回。"""
    model = model or ""
    if not model or not level or "#" in model:
        return model
    return f"{model}#{level}" if level in model_variants(exe, env, cwd).get(model, ()) else model


def session_exists(exe: str, sid: str, env: dict | None = None,
                   cwd: str | Path | None = None) -> bool | None:
    """True / False(明确报 Session not found)/ None(判断不了,按存在处理)。实测约 0.2s。"""
    r = _call(exe, ["session", "export", sid, "--standalone"], env, cwd, keep_stdout=False)
    if r is None:
        return None
    if r.returncode == 0:
        return True
    return False if re.search(r"session not found", r.stderr or "", re.I) else None


def missing_step_finishes(exe: str, sid: str, reported, env: dict | None = None,
                          cwd: str | Path | None = None, steer_ids=()) -> list[dict]:
    """补本轮事件流缺的 step_finish:session export 里最后一条 user 消息(即本轮提示词)之后的
    assistant 消息各带 tokens / cost,事件流已报过的按 messageID 跳过。本轮的运行中插话
    (steer_ids,即 steer_prompt 返回的消息 id)也是 user 消息,不算本轮起点。读不到返回 []。"""
    r = _call(exe, ["session", "export", sid, "--standalone"], env, cwd)
    if not r or r.returncode != 0:
        return []
    try:
        msgs = json.loads(r.stdout).get("messages") or []
    except (ValueError, AttributeError):
        return []
    skip = set(steer_ids or ())
    start = max((i for i, m in enumerate(msgs) if isinstance(m, dict) and m.get("type") == "user"
                 and m.get("id") not in skip), default=-1)
    seen = set(reported or ())
    out = []
    for m in msgs[start + 1:]:
        if not isinstance(m, dict) or m.get("type") != "assistant" or m.get("id") in seen:
            continue
        if isinstance(m.get("tokens"), dict):
            out.append({"type": "step_finish", "sessionID": sid, "synthetic": True,
                        "part": {"messageID": m["id"], "type": "step-finish",
                                 "tokens": m["tokens"], "cost": m.get("cost") or 0}})
    return out


def steer_prompt(exe: str, sid: str, text: str, env: dict | None = None,
                 cwd: str | Path | None = None) -> str | None:
    """运行中插话:把一条 user 输入写进会话的持久化 inbox(delivery=steer),返回其消息 id。
    resume=false:不在这个短命的私有服务器里起执行——正在跑该会话的进程在下一步边界
    从库里取走(实测 2.1.2 跨 --standalone 进程送达)。失败返回 None。"""
    body = json.dumps({"text": text, "delivery": "steer", "resume": False}, ensure_ascii=False)
    r = _call(exe, ["api", "--standalone", "POST", f"/api/session/{sid}/prompt", "-d", body], env, cwd)
    if not r or r.returncode != 0:
        return None
    try:
        data = json.loads(r.stdout).get("data") or {}
    except (ValueError, AttributeError):
        return None
    return data.get("id") if isinstance(data, dict) else None


def inbox_ids(exe: str, sid: str, env: dict | None = None,
              cwd: str | Path | None = None) -> set[str] | None:
    """会话 inbox 里尚未送达的条目 id(session.inbox.list);读不到返回 None。"""
    r = _call(exe, ["api", "--standalone", "GET", f"/api/session/{sid}/inbox"], env, cwd)
    if not r or r.returncode != 0:
        return None
    try:
        items = json.loads(r.stdout).get("data")
    except (ValueError, AttributeError):
        return None
    if not isinstance(items, list):
        return None
    return {i["id"] for i in items if isinstance(i, dict) and i.get("id")}


def inbox_cancel(exe: str, sid: str, iid: str, env: dict | None = None,
                 cwd: str | Path | None = None) -> bool:
    """撤回一条未送达的 inbox 条目(session.inbox.cancel;已送达的为空操作)。"""
    r = _call(exe, ["api", "--standalone", "DELETE", f"/api/session/{sid}/inbox/{iid}"], env, cwd)
    return bool(r and r.returncode == 0)


def auth_list(exe: str, env: dict | None = None) -> list[dict] | None:
    """`auth list --standalone --format json`:[{id, name, connections: [...]}](不含密钥)。"""
    r = _call(exe, ["auth", "list", "--standalone", "--format", "json"], env)
    if not r or r.returncode != 0:
        return None
    try:
        data = json.loads(r.stdout)
    except ValueError:
        return None
    return data if isinstance(data, list) else None


def go_key(exe: str, env: dict | None = None) -> str | None:
    """`auth export --standalone` 里 OpenCode Go(integrationID 含 opencode)的 API Key,
    多条时启用中的优先。输出含密钥,只在内存里取用。"""
    r = _call(exe, ["auth", "export", "--standalone"], env)
    if not r or r.returncode != 0:
        return None
    try:
        data = json.loads(r.stdout)
    except ValueError:
        return None
    if not isinstance(data, list):
        return None
    ents = [e for e in data if isinstance(e, dict)
            and "opencode" in str(e.get("integrationID") or "").lower()]
    for ent in sorted(ents, key=lambda e: not e.get("active")):
        val = ent.get("value")
        key = val.get("key") if isinstance(val, dict) else None
        if isinstance(key, str) and key.strip():
            return key.strip()
    return None
