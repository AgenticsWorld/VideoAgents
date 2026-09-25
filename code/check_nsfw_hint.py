#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NSFW 关键字提示机检 nsfw_hint(NSFW 模式,2026-09-25;WARN-only,永不 FAIL)。

  按双语词表扫 directing/<ep>/shot_list.json 各生成组文字与 assets/prompts/<ep>/<grp>.json 的 video_prompt,
  命中敏感词但该组未标记 NSFW(group_settings 手动/兜底、shot_list 组 nsfw 均为否)→ WARN,
  提示到分镜预览组卡点 🔞。只提示不路由、不改文件;真正的路由靠显式标记 + 审核拒收兜底(modules/genmedia.py)。

用法:python3 code/check_nsfw_hint.py --project <slug> --ep epNN [--strict] [--json]
退出码:0 PASS(--strict 时有 WARN 也算失败返回 1)、2 文件缺失。
宿主 CLI,Agent 只准调用,禁止复制/改写到项目 code/。
"""
import json
import sys

from _common import parse_args

from modules import nsfw_hint


def main() -> int:
    args, root = parse_args(__doc__, configure=lambda ap: (
        ap.add_argument("--strict", action="store_true", help="WARN 也算失败"),
        ap.add_argument("--json", action="store_true", help="机器可读输出")))
    res = nsfw_hint.scan_episode(root, args.ep)
    if res["missing"]:
        print(f"MISSING directing/{args.ep}/shot_list.json")
        return 2
    ok = not (args.strict and res["warns"])
    if args.json:
        print(json.dumps({"check": nsfw_hint.CHECK, "pass": ok, "warnings": res["warns"], "groups": res["groups"]},
                         ensure_ascii=False, indent=2))
        return 0 if ok else 1
    for w in res["warns"]:
        print("WARN", w)
    flagged = sum(1 for r in res["groups"] if r["flag"] in ("manual", "failover", "shot_list"))
    print(f"[{nsfw_hint.CHECK}] {args.project}/{args.ep}: {len(res['groups'])} 组扫描, 已标记 NSFW {flagged} 组, "
          f"WARN {len(res['warns'])} 条 -> {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
