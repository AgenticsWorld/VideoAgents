"""有状态 Agent 的扇出工单(project_types.FANOUT_TASK_PREFIXES,2026-10-22):scene-coder 的 cv3-sec 每段一单并行。

起因:handpan 总制片一次派出 5 段 cv3-sec,scene-coder 是有状态 Agent(同 Agent 额度恒为 1),只有一段在跑、其余排队。
扇出工单按无状态派(全新会话、不回存会话、同类单之间按「单 Agent 并发额度」并行),该 Agent 其余运行照旧串行续会话;
并行的 `codevideo.py render --section` 同写 codevideo/render.json,台账读-改-写加锁。
"""
import asyncio
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from modules.codevideo import render as R  # noqa: E402
from services.runtime import core  # noqa: E402
from test_run_steer import FAKE_CLAUDE, _loop_run, _script, posix_only, rt  # noqa: E402,F401  复用 execute_run 的最小运行环境

SC = "18-code-video/scene-coder"
REAL_AGENT_SEM, REAL_RUN_LIMIT = core.agent_sem, core.agent_run_limit


def test_run_task_id_from_dispatch_message():
    tid = core.run_task_id
    assert tid("执行工单 cv3-sec-grp0010(段 wake 画面,只改 codevideo/sections/wake.js):完整工单见 …") == "cv3-sec-grp0010"
    assert tid("执行工单cv3-sec-grp0020。") == "cv3-sec-grp0020"
    assert tid("```yaml\ntask_id: cv3-sec-grp0030\nagent: x\n```") == "cv3-sec-grp0030"
    assert tid("请按 data/projects/hp/runs/cv3-sec-grp0040/workorder.md 执行") == "cv3-sec-grp0040"
    # 首行第一个工单号为准:正文里顺带提到的段工单不算
    assert tid("执行工单 cv3-engine(参照 cv3-sec-grp0010 的写法)") == "cv3-engine"
    assert tid("缺陷 DEF-ep01-visual-0001 返工说明") == ""
    assert tid("看看引擎\n执行工单 cv3-sec-grp0010") == ""
    assert tid("") == ""


def test_is_fanout_run_only_for_listed_agent_and_prefix():
    msg = "执行工单 cv3-sec-grp0010(段 wake)"
    assert core.is_fanout_run(SC, msg)
    assert core.is_fanout_run(SC, "执行工单 cv3-sec-grp0050 第 2 次尝试(缺陷返工)")
    assert not core.is_fanout_run(SC, "执行工单 cv3-render(全片渲染)")
    assert not core.is_fanout_run(SC, "执行工单 cv3-sec(总单)")
    assert not core.is_fanout_run("18-code-video/film-director", msg)
    assert not core.is_stateless_agent(SC)


@posix_only
def test_fanout_runs_parallel_with_fresh_sessions(rt, monkeypatch):
    """3 段 cv3-sec 同时跑、都不 resume、不回存会话;同 Agent 的有状态运行彼此串行、各自续会话,也不被扇出单挡住。"""
    monkeypatch.setattr(core, "resolve_cli_executable", lambda e: _script(rt.tmp, "claude", FAKE_CLAUDE))
    monkeypatch.setattr(core, "agent_sem", REAL_AGENT_SEM)
    monkeypatch.setattr(core, "agent_run_limit", REAL_RUN_LIMIT)
    monkeypatch.setattr(core, "AGENT_SEMS", {})
    monkeypatch.setenv("FAKE_TOOL_S", "0.8")
    key = f"claude::{SC}::demo"
    core.STATE["sessions"][key] = "old-sess"

    def mk(i, msg):
        run = {"id": f"fan{i}", "agent": SC, "agent_name": "scene-coder", "source": "agent", "parent": None,
               "project": "demo", "status": "queued", "created": time.time(), "message": msg,
               "engine": "claude", "model": ""}
        core.RUNS[run["id"]] = run
        return run

    fan = [mk(i, f"执行工单 cv3-sec-grp00{i}0(段 s{i})") for i in (1, 2, 3)]
    sf = [mk(8, "执行工单 cv3-render(全片渲染)"), mk(9, "看看引擎")]

    async def go():
        await asyncio.gather(*(core.execute_run(r, r["message"], None) for r in fan + sf))
    _loop_run(go())

    assert all(r["status"] == "done" for r in fan + sf), [(r["id"], r["status"], r.get("error")) for r in fan + sf]
    assert all(r.get("fanout") for r in fan) and not any(r.get("fanout") for r in sf)
    assert max(r["started"] for r in fan) < min(r["ended"] for r in fan)            # 扇出单同时在跑
    a, b = sorted(sf, key=lambda r: r["started"])
    assert b["started"] >= a["ended"]                                                # 有状态运行仍串行
    assert a["started"] < min(r["ended"] for r in fan)                               # 不等扇出单
    argv = [x["text"] for x in rt.log() if x["kind"] == "argv"]
    assert len(argv) == 5 and sum("--resume" in x for x in argv) == 2                # 只有有状态运行续会话
    assert core.STATE["sessions"][key] == "sess-fake"


def test_render_ledger_concurrent_sections_keep_both(tmp_path, monkeypatch):
    """两个进程(这里用线程)同时 `render --section` 各记各的段,台账不互相覆盖。"""
    (tmp_path / R.NS).mkdir()
    secs = {"wake": "grp0010", "shape": "grp0020"}
    tl = {"size": [1920, 1080], "fps": 30, "sections": [{"id": s, "group_id": g} for s, g in secs.items()]}
    monkeypatch.setattr(R.TL, "load", lambda base: tl)
    monkeypatch.setattr(R.TL, "section_of", lambda t, k: next(s for s in t["sections"] if s["id"] == k))
    monkeypatch.setattr(R, "fingerprints", lambda base, t=None: {s: f"fp-{s}" for s in secs})
    monkeypatch.setattr(R, "status", lambda base: [{"section": s, "state": "stale"} for s in secs])

    async def fake_render(base, t, todo, w, log):
        return [{"section": s["id"], "group_id": s["group_id"], "frames": 30, "generated_at": "now", "seconds": 1.0}
                for s in todo]
    monkeypatch.setattr(R, "_render_sections", fake_render)
    real_load = R.load_ledger

    def slow_load(base):                 # 读到旧台账后停一下,让另一方在此期间也读 —— 不加锁就会互相覆盖
        d = real_load(base)
        time.sleep(0.3)
        return d
    monkeypatch.setattr(R, "load_ledger", slow_load)

    ts = [threading.Thread(target=R.render, args=(tmp_path, [s]), kwargs={"log": lambda *a: None}) for s in secs]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    led = real_load(tmp_path)
    assert set(led["sections"]) == set(secs)
    assert led["sections"]["shape"]["fingerprint"] == "fp-shape"
    assert not list((tmp_path / R.NS).glob(".render.json.*.tmp"))
