#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""场景内外景一致性机检 scene_int_ext_match(WORKFLOW.md Phase 3 scene / Phase 5 p5-screenplay,2026-09-17)。

  不带 --ep   核 `bible/scenes/index.json`:`int_ext` ∈ INT / EXT / INT/EXT;非法值 FAIL,未登记(存量)WARN。
  带 --ep     另核 `story/episodes/<ep>/screenplay.md` 各场头 INT/EXT vs 所挂 SCN 的 int_ext:
              登记值不相容 → FAIL(给出可改挂的现成 ID,没有则提示上报 scene_gaps 新立 ID);
              存量未登记按名称/层级推断,不相容只 WARN;场头 INT/EXT 跨内外而 SCN 单一 → WARN。

用法:python3 code/check_scene_int_ext.py --project <slug> [--ep ep06] [--strict] [--json]
退出码:0 PASS(--strict 时 WARN 也算失败)、1 FAIL、2 文件缺失。只 print,不改任何文件。
宿主 CLI,Agent 只准调用,禁止复制/改写到项目 code/。
"""
import json
import sys

from _common import parse_args

from modules import scene_int_ext as sie


def main() -> int:
    args, root = parse_args(__doc__, ep=False, configure=lambda ap: (
        ap.add_argument("--ep", default=None, help="同时核该集剧本场头"),
        ap.add_argument("--strict", action="store_true", help="WARN 也算失败"),
        ap.add_argument("--json", action="store_true", help="机器可读输出")))
    if not (root / "bible" / "scenes" / "index.json").is_file():
        print(f"MISSING {root / 'bible' / 'scenes' / 'index.json'}")
        return 2
    res = {"index": sie.verify_index(root)}
    if args.ep:
        if not (root / "story" / "episodes" / args.ep / "screenplay.md").is_file():
            print(f"MISSING story/episodes/{args.ep}/screenplay.md")
            return 2
        res["episode"] = sie.verify_episode(root, args.ep)
    errors = [e for r in res.values() for e in r["errors"]]
    warns = [w for r in res.values() for w in r["warns"]]
    ok = not errors and not (args.strict and warns)
    if args.json:
        print(json.dumps({"check": "scene_int_ext_match", "pass": ok, "errors": errors, "warnings": warns, **res},
                         ensure_ascii=False, indent=2))
        return 0 if ok else 1
    for e in errors:
        print("FAIL", e)
    for w in warns:
        print("WARN", w)
    scope = f"{args.project}/{args.ep}" if args.ep else args.project
    print(f"{'PASS' if ok else 'FAIL'} scene_int_ext_match {scope}: {len(errors)} 错误 · {len(warns)} 警告")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
