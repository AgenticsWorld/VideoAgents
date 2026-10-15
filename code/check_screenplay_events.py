#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""剧本事件取舍机检 dramatized_events_covered(WORKFLOW.md Phase 5 p5-screenplay,2026-09-12)。

核 `story/episodes/<ep>/screenplay.md` 各场次 `[事件]` 行 vs `story/episode_plan.json` 本集 treatments:
  dramatized_events_covered   本集每个 dramatize 事件至少出现在一场的 [事件] 行
  cut_events_absent           任何场次不得引用 cut 事件
  scene_has_dramatized_event  每场 [事件] 至少含一个 dramatize 事件(mention/merge 事件不得独立成场)
  scene_spacetime_continuous  相邻两场同 SCN + 同内外景 + 同时段且后一场无 (split_note: …) → FAIL
                              (场 = 同一空间 + 连续时间,2026-09-29;generated_at 早于该日或缺失的存量剧本只 WARN)
  scene_link_valid            场尾转场行的场间衔接 `{衔接: 类型, 出: …, 入: …}`(docs/scene_links.md,2026-10-10):类型在白名单
                              (形状 / 动作 / 同机位 / 运动 / 声音 / 台词 / 反差)、出入俱全、只写在场尾且接相邻下一场、台词接力须本场有台词、
                              转场词点名 MATCH CUT / SMASH CUT 须带花括号 → 否则 FAIL(generated_at 早于 2026-10-10 或缺失的存量剧本只 WARN);
                              出 / 入 的内容在场尾 / 下一场场头正文找不到 → WARN
WARN:场次引用本集之外的事件;场次无 [事件] 行;场次数 > dramatize 事件数 ×2(平铺信号);
      episode_plan 本集无 treatments(旧格式)→ 全部事件按 dramatize 核,不阻断存量项目。
衍生(原创)模式(episode_plan 顶层 derivative_mode: true,2026-10-09,WORKFLOW §5A):事件三项报 SKIPPED,
  场次元信息行写 `[EVENTS] none` / `[事件] 无` 为合法占位(挂了 ev… 反而 WARN);scene_spacetime_continuous / scene_link_valid 照常。

用法:python3 code/check_screenplay_events.py --project <slug> --ep ep01 [--strict] [--json]
退出码:0 PASS(--strict 时 WARN 也算失败)、1 FAIL、2 文件缺失。只 print,不改任何文件。
宿主 CLI,Agent 只准调用,禁止复制/改写到项目 code/。
"""
import json
import sys

from _common import parse_args

from modules import episode_treatments as et


def main() -> int:
    args, root = parse_args(__doc__, configure=lambda ap: (
        ap.add_argument("--strict", action="store_true", help="WARN 也算失败"),
        ap.add_argument("--json", action="store_true", help="机器可读输出")))
    sp = root / "story" / "episodes" / args.ep / "screenplay.md"
    if not sp.is_file():
        print(f"MISSING {sp}")
        return 2
    plan = et.read_json(root / "story" / "episode_plan.json")
    if plan is None:
        print(f"MISSING {root / 'story' / 'episode_plan.json'}")
        return 2
    res = et.verify_screenplay(sp.read_text(encoding="utf-8"), plan, args.ep)
    ok = all(res["checks"].values()) and not (args.strict and res["warns"])
    if args.json:
        print(json.dumps({"ok": ok, **{k: v for k, v in res.items() if k != "scenes"},
                          "scenes": res["scenes"]}, ensure_ascii=False, indent=2))
        return 0 if ok else 1
    print(et.format_report(res, f"{args.ep} 剧本事件取舍机检"))
    tr = res.get("treatments") or {}
    if tr:
        by = {}
        for e, t in tr.items():
            by.setdefault(t, []).append(e)
        for t in et.TREATMENTS:
            if by.get(t):
                print(f"  {et.TREATMENT_ZH[t]:<3}({t:<9}) {len(by[t]):>3}: {' '.join(by[t])}")
    print(f"  场次 {res.get('n_scenes')} 场 / dramatize 事件 {res.get('n_dramatize')} 个")
    for x in res.get("links") or []:
        print(f"  衔接 {x['from']}→{x['to']} {x['label_zh']}{'(试验)' if x['tier'] == 'trial' else ''}: 出「{x['out']}」→ 入「{x['in']}」")
    print(f"\n结果: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
