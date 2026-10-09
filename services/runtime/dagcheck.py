#!/usr/bin/env python3
"""dagcheck.py — dag.json 结构机检(规范格式见 WORKFLOW.md §3.2)。

用法:
  python3 services/runtime/dagcheck.py --project <slug> # 校验 data/projects/<slug>/runs/dag.json
  python3 services/runtime/dagcheck.py <path/to/dag.json>          # 校验指定文件
  python3 services/runtime/dagcheck.py --project <slug> --strict   # 旧格式警告也视为失败(立项/改 DAG 后自检)
  python3 services/runtime/dagcheck.py --project <slug> --frontier # 列依赖已满足的待办前沿(按「多集并行」设置
                                                                  # 标出按集顺序暂缓的后续集节点)

exit code:0=通过;1=有错误(--strict 下警告也算)。

零依赖(仅标准库)。runtime 的空转看门狗 import 本模块的 dag_errors(),
DAG 结构损坏时唤醒总制片修复,而不是解析失败后静默失明(前科:tothemoon
顶层键写成 tasks,看门狗空转数轮无人发觉)。
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("VIDEOAGENTS_DATA_DIR", ROOT / "data")).expanduser().resolve()
STATE_PATH = Path(os.environ.get("VIDEOAGENTS_RUNTIME_DIR", DATA_DIR / ".videoagents")
                  ).expanduser().resolve() / "state.json"

# 节点 state 合法枚举(与 WORKFLOW.md §3.2 一致;runtime _DONE_STATES 是其子集)
STATES = {"pending", "dispatched", "running", "failed", "blocked", "done",
          "passed", "passed_human_override", "expanded", "template",
          "skipped", "cancelled", "waived"}


DONE_STATES = {"done", "passed", "passed_human_override"}
FINAL_STATES = DONE_STATES | {"skipped", "cancelled", "waived"}   # 不再执行的终态
# 发布环节(Phase 11,按平台展开、常由用户暂缓或跳过)不算「一集是否做完」
PUBLISH_PREFIX = "p11-"
_EP_RE = re.compile(r"(?<![A-Za-z0-9])ep(\d+)(?!\d)", re.I)


def _normalize(doc) -> tuple[list[dict], list[str], list[str]]:
    """归一化节点容器,返回 (nodes, errors, warnings)。与 server._dag_load_nodes 同口径。"""
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(doc, dict):
        return [], ["顶层必须是 JSON 对象"], warnings
    if "nodes" in doc:
        container = doc["nodes"]
    elif "tasks" in doc:
        container = doc["tasks"]
        warnings.append("顶层键为 tasks(旧格式),规范应为 nodes")
    else:
        return [], ["缺少顶层 nodes 键(也没有旧格式的 tasks 键)"], warnings
    if isinstance(container, dict):
        warnings.append("nodes 为字典(旧格式),规范应为列表 [{id, ...}]")
        return ([{**v, "id": k} for k, v in container.items()],
                errors, warnings)
    if not isinstance(container, list):
        return [], ["nodes/tasks 必须是列表或字典"], warnings
    nodes = []
    legacy_task_id = False
    for i, n in enumerate(container):
        if not isinstance(n, dict):
            errors.append(f"第 {i} 个节点不是 JSON 对象")
            continue
        if n.get("task_id") and not n.get("id"):
            legacy_task_id = True
        nodes.append({**n, "id": n.get("id") or n.get("task_id")})
    if legacy_task_id:
        warnings.append("节点用 task_id 作标识(旧格式),规范字段为 id")
    return nodes, errors, warnings


def validate(doc) -> tuple[list[str], list[str]]:
    """校验 dag.json 文档,返回 (errors, warnings)。errors 非空即结构损坏。"""
    nodes, errors, warnings = _normalize(doc)
    if errors and not nodes:
        return errors, warnings
    ids: set[str] = set()
    for n in nodes:
        nid = n.get("id")
        if not nid or not isinstance(nid, str):
            errors.append(f"存在缺少 id/task_id 的节点:{json.dumps(n, ensure_ascii=False)[:80]}")
            continue
        if nid in ids:
            errors.append(f"节点 id 重复:{nid}")
        ids.add(nid)
        state = n.get("state")
        if state not in STATES:
            errors.append(f"节点 {nid}:state={state!r} 不在合法枚举 {sorted(STATES)}")
        deps = n.get("depends_on") or n.get("deps") or []
        if "deps" in n and "depends_on" not in n:
            warnings.append(f"节点 {nid}:字段名 deps(旧格式),规范字段为 depends_on")
        if not isinstance(deps, list):
            errors.append(f"节点 {nid}:depends_on 必须是列表")
        # human 按真值语义使用:true=人工签字节点,签字后可扩展为裁决记录对象,不限类型
    # 依赖引用与环检测(Kahn 拓扑排序)
    dep_map = {}
    for n in nodes:
        nid = n.get("id")
        if not nid:
            continue
        deps = n.get("depends_on") or n.get("deps") or []
        deps = [d for d in deps if isinstance(d, str)] if isinstance(deps, list) else []
        for d in deps:
            if d not in ids:
                errors.append(f"节点 {nid}:依赖 {d} 在 DAG 中不存在")
        dep_map[nid] = [d for d in deps if d in ids]
    remaining = dict(dep_map)
    while remaining:
        free = [k for k, v in remaining.items() if not v]
        if not free:
            errors.append(f"存在循环依赖,涉及节点:{', '.join(sorted(remaining)[:8])}")
            break
        for k in free:
            remaining.pop(k)
        for v in remaining.values():
            v[:] = [d for d in v if d in remaining]
    return errors, warnings


def node_episode(node: dict) -> int | None:
    """节点所属集号:for_each.episode 优先,否则取 id 里唯一的 epNN;不带集号或跨多集返回 None。"""
    fe = node.get("for_each")
    if isinstance(fe, dict) and fe.get("episode") is not None:
        m = re.search(r"\d+", str(fe["episode"]))
        if m:
            return int(m.group())
    found = {int(x) for x in _EP_RE.findall(str(node.get("id") or ""))}
    return found.pop() if len(found) == 1 else None


def episode_map(nodes: list[dict]) -> dict[str, int | None]:
    """节点 id → 所属集号。自身带集号的按 node_episode;不带的非人工节点沿 depends_on 继承——依赖
    全部归同一集时归该集(如 id 只写场景号的 p6-scene-model-SCN-* 只依赖 p6-env-concept-ep03 → ep03);
    依赖里有项目级节点、跨多集或没有依赖的仍是项目级(None)。不带集号的人工闸门(g5 等)恒为项目级。"""
    by_id = {n.get("id"): n for n in nodes if n.get("id")}
    memo: dict[str, int | None] = {}

    def ep_of(nid: str, seen: frozenset) -> int | None:
        if nid in memo:
            return memo[nid]
        n = by_id.get(nid)
        if n is None or nid in seen:     # 悬空依赖 / 环:不归属
            return None
        ep = node_episode(n)
        deps = [d for d in (n.get("depends_on") or []) if isinstance(d, str)]
        if ep is None and deps and not n.get("human"):
            found = {ep_of(d, seen | {nid}) for d in deps}
            ep = found.pop() if len(found) == 1 else None   # {None} 也落到 None
        memo[nid] = ep
        return ep

    return {nid: ep_of(nid, frozenset()) for nid in by_id}


def ready_nodes(nodes: list[dict]) -> tuple[list[str], list[str]]:
    """依赖已满足的待办前沿:(非人工节点, pending 人工签字节点)。runtime 看门狗同口径。"""
    done = {n["id"] for n in nodes if n.get("state") in DONE_STATES}
    runnable, human_waiting = [], []
    for n in nodes:
        state = n.get("state")
        # 终态不再执行;blocked 是用户明确暂缓;template/expanded 是 for_each 扇出骨架(#72)
        if state in FINAL_STATES or state in ("blocked", "template", "expanded"):
            continue
        if not all(d in done for d in (n.get("depends_on") or [])):
            continue
        if n.get("human"):
            if state == "pending":   # 只有 pending 人工节点能发起新的签字
                human_waiting.append(n["id"])
        else:
            runnable.append(n["id"])
    return runnable, human_waiting


def episode_serial_hold(nodes: list[dict]) -> tuple[int | None, set[str]]:
    """「多集并行」关闭时的集序闸:返回 (当前集号, 按集顺序暂缓的节点 id)。
    当前集 = 仍有节点未到终态的最小集号(发布环节 p11-* 不计);集号更大的未结束分集节点一律暂缓。
    节点归属按 episode_map(含沿依赖继承);项目级节点不受影响。没有未完成的集时返回 (None, 空集)。"""
    eps = episode_map(nodes)
    scoped, open_eps = [], set()
    for n in nodes:
        if n.get("state") in ("template", "expanded"):
            continue
        ep = eps.get(n.get("id"))
        if ep is None:
            continue
        scoped.append((n, ep))
        if n.get("state") not in FINAL_STATES and not str(n.get("id") or "").startswith(PUBLISH_PREFIX):
            open_eps.add(ep)
    if not open_eps:
        return None, set()
    cur = min(open_eps)
    return cur, {n["id"] for n, ep in scoped if ep > cur and n.get("state") not in FINAL_STATES}


def episode_parallel_enabled() -> bool:
    """读用户「Agent 高级设置→多集并行」(state.json episode_parallel,默认关)。"""
    try:
        return bool(json.loads(STATE_PATH.read_text()).get("episode_parallel", False))
    except Exception:  # noqa: BLE001
        return False


def frontier(nodes: list[dict], parallel: bool) -> dict:
    """待办前沿 + 集序闸;parallel=False 时把后续集的节点从可派清单里剔出。"""
    runnable, human_waiting = ready_nodes(nodes)
    cur, held = (None, set()) if parallel else episode_serial_hold(nodes)
    return {"parallel": parallel, "current_episode": cur,
            "runnable": [x for x in runnable if x not in held],
            "human_waiting": [x for x in human_waiting if x not in held],
            "held_ready": [x for x in runnable + human_waiting if x in held],
            "held": sorted(held)}


def _print_frontier(nodes: list[dict], parallel: bool) -> None:
    f = frontier(nodes, parallel)

    def show(ids: list[str]) -> str:
        if not ids:
            return "无"
        return ", ".join(ids[:40]) + (f" … 共 {len(ids)} 个" if len(ids) > 40 else "")

    if parallel:
        print("多集并行:开启(试点集过 H4 后各集可同时推进)")
    else:
        print("多集并行:关闭(按集顺序推进:当前集全部节点结束后才派下一集;集内各组/镜/场景照常并行扇出)")
        cur = f["current_episode"]
        print(f"当前集:ep{cur:02d}" if cur is not None else "当前集:无(没有未完成的分集,发布环节 p11-* 不计)")
    print(f"可派节点(依赖已满足):{show(f['runnable'])}")
    print(f"待签人工闸门:{show(f['human_waiting'])}")
    if not parallel and f["held"]:
        print(f"按集顺序暂缓(当前集之后的集,共 {len(f['held'])} 个未结束节点;其中依赖已满足、"
              f"当前集结束后才派的):{show(f['held_ready'])}")


def dag_errors(path) -> list[str]:
    """server 看门狗用:只返回硬错误(旧格式警告由容错解析兜住,不算错误)。"""
    try:
        doc = json.loads(Path(path).read_text())
    except Exception as e:  # noqa: BLE001
        return [f"JSON 解析失败:{e}"]
    return validate(doc)[0]


def main() -> int:
    ap = argparse.ArgumentParser(description="dag.json 结构机检")
    ap.add_argument("path", nargs="?", help="dag.json 路径(与 --project 二选一)")
    ap.add_argument("--project", help="项目 slug,校验 data/projects/<slug>/runs/dag.json")
    ap.add_argument("--strict", action="store_true", help="警告也视为失败(新写入的 DAG 必须全绿)")
    ap.add_argument("--frontier", action="store_true",
                    help="校验后列出依赖已满足的待办前沿;多集并行关闭时标出按集顺序暂缓的后续集节点")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--episode-parallel", dest="parallel", action="store_true", default=None,
                      help="--frontier 按多集并行口径(缺省读用户设置)")
    mode.add_argument("--episode-serial", dest="parallel", action="store_false",
                      help="--frontier 按集顺序口径(缺省读用户设置)")
    args = ap.parse_args()
    if args.project:
        path = DATA_DIR / "projects" / args.project / "runs" / "dag.json"
    elif args.path:
        path = Path(args.path)
    else:
        ap.error("需要 path 或 --project")
    if not path.is_file():
        print(f"[错误] 文件不存在:{path}")
        return 1
    try:
        doc = json.loads(path.read_text())
    except Exception as e:  # noqa: BLE001
        print(f"[错误] JSON 解析失败:{e}")
        return 1
    errors, warnings = validate(doc)
    for w in warnings:
        print(f"[警告] {w}")
    for e in errors:
        print(f"[错误] {e}")
    if errors or (args.strict and warnings):
        print(f"未通过:{len(errors)} 个错误,{len(warnings)} 个警告"
              +("(--strict 下警告计为失败)" if args.strict and warnings else ""))
        return 1
    nodes = _normalize(doc)[0]
    print(f"通过:{len(nodes)} 个节点" + (f",{len(warnings)} 个旧格式警告(建议迁移到规范格式)" if warnings else ""))
    if args.frontier:
        _print_frontier(nodes, episode_parallel_enabled() if args.parallel is None else args.parallel)
    return 0


if __name__ == "__main__":
    sys.exit(main())
