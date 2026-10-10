#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""剧本场间衔接清单 + 导演处置机检 script_links_disposed / 落表对账 script_links_landed(WORKFLOW.md Phase 6 p6-plan · p6-shots,2026-10-10;docs/scene_links.md)。

编剧在场尾「转场:」行起稿的场间衔接 `{衔接: 类型, 出: …, 入: …}`(匹配剪辑、运动 / 声音 / 台词接力、反差硬切)以前到了
导演这里没有处置义务,采不采纳无人对账。本 CLI:
  --list   只列本集剧本的衔接清单(导演写阐述前先看;每条带类型、出 / 入、档位、采纳时默认落到的转场类型)
  缺省     列清单 + 核 `directing/<ep>/directing_plan.md`:
           script_links_disposed  剧本每条合法衔接,阐述「## 转场清单」里都有一行处置
                                    `剧本衔接 S03→S04(形状匹配):采纳 | 修改 | 弃用 —— …`
                                    (英文 `Script link S03→S04 (shape): ADOPT | MODIFY | DROP — …`);
                                  采纳 / 修改须写明落到哪种转场类型(hard_cut / match_cut / smash_cut / dissolve / …),
                                  弃用 / 修改须写理由 → 否则 FAIL。处置了剧本里没有的衔接 → WARN。
           script_links_landed    (二期;有 directing/<ep>/shot_list.json 时才核)导演采纳 / 修改的衔接真的落进了分镜表:
                                  后一场第一组有显式 transition_in 且类型与导演写的一致;登记卡 transition_in.link 与剧本现值一致;
                                  弃用 / 已删的衔接没有残留登记卡 → 否则 FAIL。登记卡还没写(宿主 propose / H3A 签字时自动写)→ WARN;
                                  用户在过场卡上改过的边界以用户为准,不报
           剧本没有任何衔接 = 不适用,PASS。

用法:python3 code/check_scene_links.py --project <slug> --ep ep01 [--list] [--strict] [--json]
退出码:0 PASS(--strict 时 WARN 也算失败)、1 FAIL、2 文件缺失(缺剧本;非 --list 时缺导演阐述且剧本有衔接)。只 print,不改任何文件。
宿主 CLI,Agent 只准调用,禁止复制/改写到项目 code/。
"""
import json
import sys

from _common import parse_args

from modules import scene_links as sl


LAND_STATE = {"landed": "已登记", "base_only": "已落类型,登记卡未写", "missing": "未落", "type_mismatch": "类型不符",
              "stale": "登记卡过期", "no_boundary": "找不到边界", "user_decided": "用户已在过场卡改写"}


def _read_json(p):
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
    except (OSError, ValueError):
        return None


def _print_links(links: list, rows: list | None = None) -> None:
    by = {(r["from"], r["to"]): r for r in rows or []}
    for x in links:
        k = sl.KINDS[x["kind"]]
        tier = "试验:下游只当标注,不保证画面重合" if x["tier"] == "trial" else "放开"
        print(f"  {x['from']}→{x['to']} {x['label_zh']}({x['kind']};{tier};采纳默认落 {x['land']})")
        print(f"      出「{x['out']}」→ 入「{x['in']}」   要对上的:{k['need_zh']}")
        d = by.get((x["from"], x["to"]))
        if d is not None:
            disp = sl.DISPOSITIONS.get(d.get("disposition") or "", "未处置")
            print(f"      导演:{disp}{' → ' + d['landed'] if d.get('landed') else ''}{' —— ' + d['reason'] if d.get('reason') else ''}")


def main() -> int:
    args, root = parse_args(__doc__, configure=lambda ap: (
        ap.add_argument("--list", action="store_true", help="只列剧本衔接清单,不核导演处置"),
        ap.add_argument("--strict", action="store_true", help="WARN 也算失败"),
        ap.add_argument("--json", action="store_true", help="机器可读输出")))
    sp = root / "story" / "episodes" / args.ep / "screenplay.md"
    if not sp.is_file():
        print(f"MISSING {sp}")
        return 2
    text = sp.read_text(encoding="utf-8")
    links = sl.links(text)
    if args.list:
        if args.json:
            print(json.dumps({"links": links}, ensure_ascii=False, indent=2))
        else:
            print(f"=== {args.ep} 剧本场间衔接 {len(links)} 处 ===")
            _print_links(links)
            if not links:
                print("  (本集剧本没有写场间衔接,导演阐述无须处置)")
        return 0
    plan = root / "directing" / args.ep / "directing_plan.md"
    if links and not plan.is_file():
        print(f"MISSING {plan}")
        return 2
    plan_text = plan.read_text(encoding="utf-8") if plan.is_file() else ""
    res = sl.verify_dispositions(text, plan_text)
    shot_list = _read_json(root / "directing" / args.ep / "shot_list.json")
    land = sl.verify_landed(text, plan_text, shot_list, _read_json(root / "directing" / args.ep / "transition_design.json")) \
        if shot_list else None
    checks = {"script_links_disposed": res["ok"], **({"script_links_landed": land["ok"]} if land else {})}
    warns = res["warns"] + (land["warns"] if land else [])
    ok = all(checks.values()) and not (args.strict and warns)
    if args.json:
        print(json.dumps({"ok": ok, "checks": checks, **res, "warns": warns,
                          "errors": res["errors"] + (land["errors"] if land else []),
                          "landed": land["rows"] if land else None}, ensure_ascii=False, indent=2))
        return 0 if ok else 1
    print(f"=== {args.ep} 剧本场间衔接处置机检 ===")
    print(f"[CHECK] {'script_links_disposed':<28}: {'PASS' if res['ok'] else 'FAIL'}" + ("" if links else "(剧本无衔接,不适用)"))
    if land:
        print(f"[CHECK] {'script_links_landed':<28}: {'PASS' if land['ok'] else 'FAIL'}")
    else:
        print(f"[CHECK] {'script_links_landed':<28}: SKIPPED(尚无 shot_list)")
    _print_links(links, res["rows"])
    for r in (land["rows"] if land else []):
        if r["state"] in LAND_STATE:
            print(f"  {r['from']}→{r['to']} 落表:{LAND_STATE[r['state']]}" + (f"({r['to_group']})" if r.get("to_group") else ""))
    for e in res["errors"] + (land["errors"] if land else []):
        print(f"FAIL  {e}")
    for w in warns:
        print(f"WARN  {w}")
    print(f"\n结果: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
