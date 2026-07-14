#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读校验 episode_plan.json:事件 100% 覆盖且不重复、时长在预算内、ID 合法。仅 print 统计,不改任何文件。

用法:python3 code/verify_episode_plan.py [--project <slug>]
"""
import json, sys

from _common import parse_args

args, _proj = parse_args(__doc__, ep=False)
BASE = _proj / "story"
plan = json.load(open(BASE / "episode_plan.json", encoding="utf-8"))
events = json.load(open(BASE / "events.json", encoding="utf-8"))
graph = json.load(open(BASE / "story_graph.json", encoding="utf-8"))

all_ev = [e["id"] for e in events["events"]]
all_ev_set = set(all_ev)
all_fs = {f["id"] for f in graph["foreshadowing"]}

assigned = []
for ep in plan["episodes"]:
    assigned.extend(ep["events"])

assigned_set = set(assigned)
dups = [x for x in assigned_set if assigned.count(x) > 1]
missing = sorted(all_ev_set - assigned_set)
extra = sorted(assigned_set - all_ev_set)

print("=== episode_plan 机检 ===")
print(f"events.json 事件总数        : {len(all_ev)}")
print(f"episode_plan 分配事件条目数  : {len(assigned)}")
print(f"去重后覆盖事件数            : {len(assigned_set)}")
print(f"重复分配的事件             : {dups if dups else '无'}")
print(f"漏分配的事件               : {missing if missing else '无'}")
print(f"非法/多余事件ID            : {extra if extra else '无'}")
cov_ok = (len(assigned) == len(all_ev)) and not dups and not missing and not extra
print(f"[CHECK] events_assigned_once: {'PASS' if cov_ok else 'FAIL'}")

# 时长预算
budget = plan["default_duration_budget_s"]
print(f"\n=== 时长预算(基准 {budget}s / 集) ===")
dur_ok = True
for ep in plan["episodes"]:
    b = ep["duration_budget_s"]
    load = ep.get("content_load_estimate_s", "-")
    inrange = (b == budget)
    load_ok = (isinstance(load, int) and load <= budget)
    dur_ok = dur_ok and inrange and load_ok
    print(f"  {ep['ep']} 预算={b}s 内容负荷估={load}s 事件数={len(ep['events'])} "
          f"卡点={ep['hook_point']['opening']}->{ep['hook_point']['cliffhanger']}")
print(f"[CHECK] duration_in_budget : {'PASS' if dur_ok else 'FAIL'}")

# ID 合法性(事件卡点 + 伏笔)
bad_hook = []
for ep in plan["episodes"]:
    for k in ("opening", "cliffhanger"):
        v = ep["hook_point"][k]
        if v not in all_ev_set:
            bad_hook.append((ep["ep"], k, v))
bad_fs = []
for ep in plan["episodes"]:
    for f in ep.get("carry_over", []):
        if f not in all_fs:
            bad_fs.append((ep["ep"], f))
print(f"\n=== ID 合法性 ===")
print(f"非法卡点事件ID : {bad_hook if bad_hook else '无'}")
print(f"非法伏笔ID     : {bad_fs if bad_fs else '无'}")
id_ok = not bad_hook and not bad_fs
print(f"[CHECK] ids_valid          : {'PASS' if id_ok else 'FAIL'}")

# 叙事顺序单调性(骨架应 ev0001->ev0034 顺次不倒序)
mono = all(assigned[i] <= assigned[i+1] for i in range(len(assigned)-1))
print(f"\n[CHECK] narrative_order_monotonic (ev 编号顺次): {'PASS' if mono else 'FAIL'}")

print(f"\n总集数: {plan['total_episodes']}  | 全部机检: "
      f"{'ALL PASS' if cov_ok and dur_ok and id_ok else 'HAS FAIL'}")
sys.exit(0 if (cov_ok and dur_ok and id_ok) else 1)
