#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""episode_plan.json 只读机检(WORKFLOW.md Phase 5 p5-episode-plan;2026-09-12 改「分配」为「归类+取舍」)。

机检项(逻辑在 modules/episode_treatments.py):
  events_classified_once   对账范围内事件 100% 归到唯一一集、无重复、ID 合法(总表带 roadmap 时只对账精确规划集章节范围)
  treatments_complete      每集 events 逐个定 treatment ∈ dramatize/mention/merge/cut;mention/merge/cut 须写 reason
  dramatize_within_cap     每集 dramatize 事件数 ≤ ceil(duration_budget_s / --sec-per-event),默认 90s/事件
  hook_points_dramatized   开场钩位/结尾卡点事件必须 dramatize
  cut_not_on_causal_chain  cut 事件不得 importance=major、不得是任何保留事件的 caused_by、不得是卡点
  merge_target_valid       merge_into 指向同集 dramatize 事件
  duration_in_budget / ids_valid  沿用旧检

用法:python3 code/verify_episode_plan.py [--project <slug>] [--sec-per-event 90] [--json]
退出码:0 全 PASS、1 有 FAIL、2 文件缺失。只 print,不改任何文件。
宿主 CLI,Agent 只准调用,禁止复制/改写到项目 code/。
"""
import json
import sys

from _common import parse_args

from modules import episode_treatments as et


def main() -> int:
    args, root = parse_args(__doc__, ep=False, configure=lambda ap: (
        ap.add_argument("--sec-per-event", type=float, default=et.DEFAULT_SEC_PER_DRAMATIZED_EVENT,
                        help="每个 dramatize 事件平均预算秒数,决定每集 dramatize 上限(默认 90)"),
        ap.add_argument("--json", action="store_true", help="机器可读输出")))
    base = root / "story"
    plan = et.read_json(base / "episode_plan.json")
    if plan is None:
        print(f"MISSING {base / 'episode_plan.json'}(不存在或 JSON 无法解析)")
        return 2
    events = et.read_json(base / "events.json")
    graph = et.read_json(base / "story_graph.json")
    if events is None:
        print(f"MISSING {base / 'events.json'}")
        return 2
    res = et.verify_plan(plan, events, graph, sec_per_event=args.sec_per_event)
    ok = all(res["checks"].values())
    if args.json:
        print(json.dumps({"ok": ok, **res}, ensure_ascii=False, indent=2))
        return 0 if ok else 1
    print(et.format_report(res, "episode_plan 机检"))
    print(f"\n对账范围: {res.get('scope')}  范围内事件 {res.get('n_scope_events')}  归类条目 {res.get('n_assigned')}")
    print("\n集     预算   事件  演  带过  并入  删  上限  卡点")
    for e in res["episodes"]:
        b = f"{e['budget_s']:.0f}s" if e["budget_s"] else "-"
        hk = e["hook"]
        print(f"{e['ep']:<6} {b:>6} {e['n_events']:>4} {e['n_dramatize']:>4} {e['n_mention']:>5} {e['n_merge']:>5} "
              f"{e['n_cut']:>4} {str(e['cap'] or '-'):>4}  {hk.get('opening')}->{hk.get('cliffhanger')}")
    print(f"\n全部机检: {'ALL PASS' if ok else 'HAS FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
