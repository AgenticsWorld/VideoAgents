#!/usr/bin/env python3
"""dispatch.py — Director 派单工具(也可人工使用)。

用法:
  python3 services/runtime/dispatch.py "<agent_id>" "<工作指令>" [--project demo] [--wait]
  python3 services/runtime/dispatch.py --list                # 列出全部 agent_id
  python3 services/runtime/dispatch.py --runs                # 查看运行状态
  python3 services/runtime/dispatch.py --status <run_id>     # 查看单个运行(含结果)
  python3 services/runtime/dispatch.py --wait-all <run_id...> # 等待多个运行全部结束(带进度心跳)
  python3 services/runtime/dispatch.py --confirm "<问题>" [--timeout 60] [--options 重跑,跳过] [--default 重跑]
                                                  # 重跑类确认:阻塞至答复或超时(弹窗至多 60s 自动落默认),stdout 输出所选项
  python3 services/runtime/dispatch.py --confirm "<H 门说明>" --sign [--timeout 14400]
                                                  # 签字类确认(H1-H5/H3A 人工签字点专用):弹窗不倒计时、
                                                  # 永不自动确认,保留到用户点「签字」;本命令等待至答复或
                                                  # --timeout(默认 4h),超时 stdout 输出「未签字」——超时不是
                                                  # 通过,应把任务记为等待人工后正常结束,弹窗仍保留

零依赖(仅标准库)。通过公开 API 派单,所以所有客户端都能实时看到。
"""
import argparse
import json
import os
import sys
import time
import re
import urllib.request

PORT = os.environ.get("VIDEOAGENTS_PORT", "8630")
BASE = os.environ.get("VIDEOAGENTS_API_URL", f"http://127.0.0.1:{PORT}").rstrip("/")
API = BASE + "/api/v1"
API_TOKEN = os.environ.get("VIDEOAGENTS_API_TOKEN", "")
PARENT = os.environ.get("VIDEOAGENTS_RUN_ID")          # 由 runtime 注入:标记父运行
DEFAULT_PROJECT = os.environ.get("VIDEOAGENTS_PROJECT", "demo")
WAIT_TIMEOUT_DEFAULT = 7200          # --wait/--wait-all 默认等待上限(秒),与运行超时缺省 2h 对齐
DEFAULT_ENGINE = os.environ.get("VIDEOAGENTS_ENGINE", "claude")   # 继承派单方的引擎



def project_slug(value: str) -> str:
    """Argparse validator: --project accepts a slug, never a path."""
    if not value or len(value) > 80 or not re.fullmatch(r"[\w-]+", value):
        raise argparse.ArgumentTypeError("--project must be a project name, not a directory path")
    return value


# macOS 系统代理(如 wsm)会连 127.0.0.1 一起劫持导致 503;
# 本工具缺省访问本机 API,用空 ProxyHandler 强制直连,无需调用方 export no_proxy。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(path: str, payload: dict | None = None):
    req = urllib.request.Request(API + path)
    if API_TOKEN:
        req.add_header("Authorization", f"Bearer {API_TOKEN}")
    if payload is not None:
        req.data = json.dumps(payload).encode()
        req.add_header("Content-Type", "application/json")
    with _OPENER.open(req, timeout=30) as r:
        return json.loads(r.read().decode())


def fmt_run(r: dict) -> str:
    dur = ""
    if r.get("started"):
        dur = f" {int((r.get('ended') or time.time()) - r['started'])}s"
    par = f" ←{r['parent']}" if r.get("parent") else ""
    # 用户在运行面板手动停止的任务:status 仍是 error,但标签直接标出,免得被当成程序错误去追查
    tag = ""
    if r.get("stopped") == "user":
        tag = " ⏹已被用户手动停止(非错误,无需追查原因)"
    elif r.get("stopped"):
        tag = " ⏹服务关闭/重启时被中断(非错误,无需追查原因)"
    return (f"[{r['status']:>7}] {r['id']} {r['agent']}{par}{dur} "
            f"| {r.get('message', '')[:60]}{tag}")


def heartbeat(note: str):
    """把等待进度上报到父运行,UI 上实时可见。无父运行或旧版 server 时静默。"""
    if not PARENT:
        return
    try:
        api(f"/runs/{PARENT}/progress", {"note": note})
    except Exception:
        pass


def confirm(question: str, timeout: int, options: list[str], default: str,
            sign: bool = False, project: str = DEFAULT_PROJECT):
    """发起用户确认;阻塞至答复或超时。stdout 只输出最终选项(供调用方脚本读取)。
    sign=True 为签字类:弹窗永不自动确认;本函数超时输出「未签字」,不得视为通过。"""
    resp = api("/approvals", {"question": question, "timeout": timeout,
                                "options": options, "default": default,
                                "kind": "sign" if sign else "confirm",
                                "parent": PARENT, "project": project})
    cid = resp["confirm_id"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(2)
        try:
            c = api(f"/approvals/{cid}")
        except Exception:
            continue
        if c.get("answer"):
            heartbeat(f"✅ 用户选择「{c['answer']}」:{question[:100]}")
            print(c["answer"])
            return
        heartbeat(("✍️ 等待用户签字" if sign else "❓ 等待用户确认")
                  + f"(剩 {int(deadline - time.time())}s):{question[:120]}")
    if sign:
        heartbeat(f"⏱ 等待签字超时,弹窗保留,任务转入等待人工:{question[:100]}")
        print("未签字")
        return
    heartbeat(f"⏱ 确认超时,采用默认「{default}」:{question[:100]}")
    print(default)


def wait_all(ids: list[str], timeout: int, interval: int = 5):
    """轮询等待多个 run 全部结束;每轮向父运行发心跳。"""
    if PARENT and PARENT in ids:
        msg = (
            f"拒绝自等待:当前运行 {PARENT} 不能作为自己的子任务。"
            "--wait-all 只接受派单命令返回的子任务 run_id;"
            "如果尚未派出子任务,不要调用 --wait-all。"
        )
        print(msg, file=sys.stderr)
        raise SystemExit(64)

    deadline = time.time() + timeout
    while True:
        runs = {}
        for rid in ids:
            try:
                runs[rid] = api(f"/runs/{rid}")
            except Exception as e:  # noqa: BLE001
                runs[rid] = {"id": rid, "agent": "?", "status": "unknown",
                             "error": str(e)[:120]}
        pending = [r for r in runs.values()
                   if r.get("status") not in ("done", "error")]
        done_n = len(ids) - len(pending)
        if not pending:
            heartbeat(f"✅ {done_n}/{len(ids)} 子任务全部完成,正在验收")
            failed = False
            for rid in ids:
                r = runs[rid]
                print(fmt_run(r))
                if r.get("files"):
                    print("  产物:", *r["files"], sep="\n    ")
                if r.get("result"):
                    print("  --- 结果 ---")
                    print("  " + (r["result"][:2000]).replace("\n", "\n  "))
                if r.get("status") != "done":
                    failed = True
                    if r.get("stopped"):
                        print("  ⏹ 该任务" + ("被用户在运行面板手动停止" if r["stopped"] == "user"
                                            else "因服务关闭/重启被中断")
                              + ",非程序错误,无需追查失败原因;是否重派由用户决定")
                    elif r.get("error"):
                        print("  错误:", r["error"])
            sys.exit(1 if failed else 0)

        def brief(r):
            name = (r.get("agent") or "?").split("/")[-1]
            dur = f" {int(time.time() - r['started'])}s" if r.get("started") else ""
            return f"{name}({r.get('status')}{dur})"

        note = (f"⏳ 等待子任务 {done_n}/{len(ids)}:"
                + ", ".join(brief(r) for r in pending))
        heartbeat(note[:280])
        if time.time() > deadline:
            print(f"等待超时({timeout}s),仍未完成:",
                  ", ".join(r["id"] for r in pending))
            sys.exit(2)
        time.sleep(interval)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("agent", nargs="?")
    ap.add_argument("instruction", nargs="?")
    ap.add_argument("--project", default=DEFAULT_PROJECT, type=project_slug)
    ap.add_argument("--model", default=None)
    # 默认 None:未显式指定时走「Agent 级模型配置 > 继承派单方引擎」;
    # 显式传 --engine/--model 则强制覆盖该成员的 Agent 级配置(force)
    ap.add_argument("--engine", default=None,
                    choices=["claude", "codex", "kimi", "pi", "opencode", "grok", "deepagents"])
    ap.add_argument("--wait", action="store_true")
    # 等待类默认 7200s(与运行超时缺省 2h 对齐);--confirm 未显式指定时按类别取默认(见下)
    ap.add_argument("--timeout", type=int, default=None)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--runs", action="store_true")
    ap.add_argument("--status", metavar="RUN_ID")
    ap.add_argument("--wait-all", nargs="+", metavar="RUN_ID", dest="wait_all")
    ap.add_argument("--confirm", metavar="QUESTION")
    ap.add_argument("--sign", action="store_true",
                    help="签字类确认:弹窗不自动确认,等用户点「签字」")
    ap.add_argument("--options", default=None)
    ap.add_argument("--default", default=None, dest="default_opt")
    args = ap.parse_args()

    if args.list:
        for a in api("/agents"):
            mark = " [调度]" if a.get("dispatcher") else ""
            print(f"{a['id']:<45} {a['name']}{mark}")
        return

    if args.runs:
        for r in api("/runs"):
            print(fmt_run(r))
        return

    if args.confirm:
        raw = args.options or ("签字,暂缓" if args.sign else "重跑,跳过")
        opts = [o.strip() for o in raw.split(",") if o.strip()]
        # 默认等待:重跑类 60s(弹窗同步倒计时);签字类 4h(弹窗不倒计时,超时弹窗仍保留)
        timeout = args.timeout if args.timeout is not None else (14400 if args.sign else 60)
        confirm(args.confirm, timeout, opts, args.default_opt or opts[0],
                sign=args.sign, project=args.project)
        return

    if args.wait_all:
        wait_all(args.wait_all, args.timeout or WAIT_TIMEOUT_DEFAULT)
        return

    if args.status:
        r = api(f"/runs/{args.status}")
        print(fmt_run(r))
        if r.get("files"):
            print("产物:", *r["files"], sep="\n  ")
        if r.get("result"):
            print("--- 结果 ---")
            print(r["result"][:4000])
        if r.get("status") == "error" and r.get("error"):
            print("错误:", r["error"])
        return

    if not args.agent or not args.instruction:
        ap.error("需要 <agent_id> 和 <工作指令>,或使用 --list/--runs/--status")

    resp = api("/runs", {
        "agent": args.agent, "message": args.instruction,
        "project": args.project, "model": args.model,
        "engine": args.engine or DEFAULT_ENGINE,
        "force": bool(args.engine or args.model),
        "source": "director" if PARENT else "cli", "parent": PARENT,
    })
    run_id = resp["run_id"]
    print(f"已派单 run_id={run_id} → {args.agent}")

    if args.wait:
        wait_timeout = args.timeout or WAIT_TIMEOUT_DEFAULT
        deadline = time.time() + wait_timeout
        while time.time() < deadline:
            time.sleep(5)
            r = api(f"/runs/{run_id}")
            dur = f" {int(time.time() - r['started'])}s" if r.get("started") else ""
            heartbeat(f"⏳ 等待 {args.agent}({run_id} {r['status']}{dur})")
            if r["status"] in ("done", "error"):
                print(fmt_run(r))
                if r.get("files"):
                    print("产物:", *r["files"], sep="\n  ")
                print("--- 结果 ---")
                print((r.get("result") or r.get("error") or "")[:4000])
                if r.get("stopped"):
                    print("⏹ 该任务" + ("被用户在运行面板手动停止" if r["stopped"] == "user"
                                      else "因服务关闭/重启被中断")
                          + ",非程序错误,无需追查失败原因;是否重派由用户决定")
                elif r["status"] == "error" and r.get("error") and r.get("result"):
                    print("错误:", r["error"])
                sys.exit(0 if r["status"] == "done" else 1)
        print(f"等待超时({wait_timeout}s),任务仍在后台运行,稍后用 --status {run_id} 查询")
        sys.exit(2)


if __name__ == "__main__":
    main()
