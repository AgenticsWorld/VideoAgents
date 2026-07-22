#!/usr/bin/env python3
"""dagcheck.py — dag.json 结构机检(规范格式见 WORKFLOW.md §3.2)。

用法:
  python3 services/runtime/dagcheck.py --project <slug> # 校验 data/projects/<slug>/runs/dag.json
  python3 services/runtime/dagcheck.py <path/to/dag.json>          # 校验指定文件
  python3 services/runtime/dagcheck.py --project <slug> --strict   # 旧格式警告也视为失败(立项/改 DAG 后自检)

exit code:0=通过;1=有错误(--strict 下警告也算)。

零依赖(仅标准库)。runtime 的空转看门狗 import 本模块的 dag_errors(),
DAG 结构损坏时唤醒总制片修复,而不是解析失败后静默失明(前科:tothemoon
顶层键写成 tasks,看门狗空转数轮无人发觉)。
"""
import argparse
import json
import sys
from pathlib import Path

# 节点 state 合法枚举(与 WORKFLOW.md §3.2 一致;runtime _DONE_STATES 是其子集)
STATES = {"pending", "dispatched", "running", "failed", "blocked", "done",
          "passed", "passed_human_override", "expanded", "template",
          "skipped", "cancelled", "waived"}


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
    args = ap.parse_args()
    if args.project:
        path = Path(__file__).resolve().parents[2] / "data" / "projects" / args.project / "runs" / "dag.json"
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
    n = len(_normalize(doc)[0])
    print(f"通过:{n} 个节点" + (f",{len(warnings)} 个旧格式警告(建议迁移到规范格式)" if warnings else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
