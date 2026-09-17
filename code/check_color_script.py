#!/usr/bin/env python3
"""check_color_script.py — 色彩脚本机检 color_script_ok(2026-09-17)。

背景:bible/color_script.json 的字段名长期没锁死,各项目产出了十来种结构(集键 ep/episode/episode_id、
段落 acts/segments/beats、场景挂点各写各的、色板写色名),宿主后期页场次色块与 scene_palette 处方读不到,
调色规划工位(10-editing/grade-planner)只能从说明文字里猜闪回变体的数值。契约现已在
agents/06-art/color-script/SOUL.md 锁死,本脚本据此机检;宿主读取侧(modules/color_script.py)仍兼容旧写法。

规则:
  FAIL(任何项目):episodes 为空;某集认不出集号 / 条目重复;episode_plan 里有的集没有条目;某集没有段落;
       段落没有合法色值 / mood 为空 / rationale 为空;peaks 指向不存在的集;variant 不在枚举
       flashback/dream/montage/imagination 内;标了 variant 却没有变体定义;变体定义没有色值;grade 不是数值。
  契约字段(缺省 WARN,--strict 按 FAIL——新项目交付前用):episode_id:"epNN"、段落写在 acts[]、key_palette、
       event_refs、scene_ids、palette 直接写色值、变体定义写在顶层 variants.<kind> 且带 grade 数值。

用法:
  python3 code/check_color_script.py --project <slug>            # 存量项目:契约字段只 WARN
  python3 code/check_color_script.py --project <slug> --strict   # color-script 交付前必跑
退出码:0 通过(可含 WARN)、1 有违规、2 源文件缺失。宿主机检脚本,Agent 只准调用、不得复制/改写到项目 code/。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args  # noqa: E402  副作用:modules/ 入 sys.path

import color_script  # noqa: E402


def main() -> int:
    def configure(ap):
        ap.add_argument("--strict", action="store_true", help="契约字段缺失也按 FAIL(新项目交付前)")
        ap.add_argument("--max", type=int, default=40, help="最多打印多少条 WARN/FAIL(缺省 40)")
    args, root = parse_args("色彩脚本机检 color_script_ok", ep=False, configure=configure)
    if not (root / color_script.REL).is_file():
        print(f"MISSING {color_script.REL}")
        return 2
    cs = color_script.load(root)
    plan = {}
    try:
        plan = json.loads((root / "story" / "episode_plan.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    plan_eps = [color_script.entry_ep(e) for e in (plan.get("episodes") or []) if isinstance(e, dict)]
    errs, warns = color_script.validate(cs, [e for e in plan_eps if e], strict=args.strict)
    for tag, rows in (("WARN", warns), ("FAIL", errs)):
        for r in rows[:args.max]:
            print(f"{tag} {r}")
        if len(rows) > args.max:
            print(f"{tag} …另有 {len(rows) - args.max} 条")
    n = len(color_script.episode_entries(cs))
    print(f"{'FAIL' if errs else 'OK'} color_script_ok:{n} 集,{len(errs)} 违规,{len(warns)} 提醒")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
