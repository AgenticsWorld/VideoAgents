#!/usr/bin/env python3
"""NPC 参与构图(2026-10-09,docs/npc_staging.md):逐场次查看生效值、给剧本判定参考建议、跑下游机检、刷新组 prompt 固定段。

用法:
  python3 code/npc_staging.py --project <slug> --ep ep01                 # 逐场次生效表(总开关 / 来源 / 密度 / 理由)
  python3 code/npc_staging.py --project <slug> --ep ep01 --suggest       # 关键词建议(timeline-story 判定时的参考,不是结论)
  python3 code/npc_staging.py --project <slug> --ep ep01 --check storyboard|shots|composition|prompt|all
        storyboard  = npc_staging_applied(故事板 npc[] / npc_applied 与生效值一致)
        shots       = npc_shots_consistent(镜头表:关闭的场次不得带 npc[])
        composition = npc_layers_bound(带 npc[] 的镜,composition.json 对应层写明 NPC)
        prompt      = npc_prompt_bound(组 prompt 的 NPC 固定段与身份锁收窄)
  python3 code/npc_staging.py --project <slug> --ep ep01 --write [grp…]  # 刷新组 prompt 的 NPC 固定段(幂等)
退出码:0 PASS、1 FAIL。宿主 CLI,Agent 只准调用,禁止复制/改写到项目 code/。
"""
import json
import sys

from _common import parse_args

from modules import npc_staging as npc

CHECKS = ("storyboard", "shots", "composition", "prompt", "all")


def main() -> int:
    args, root = parse_args("NPC 参与构图", configure=lambda ap: (
        ap.add_argument("groups", nargs="*", help="--write / --check prompt 时限定组"),
        ap.add_argument("--suggest", action="store_true", help="打印关键词建议(判定参考)"),
        ap.add_argument("--check", choices=CHECKS, help="下游机检"),
        ap.add_argument("--write", action="store_true", help="刷新组 prompt 的 NPC 固定段"),
        ap.add_argument("--json", action="store_true", help="机器可读输出")))
    ep = args.ep
    res = npc.resolve(root, ep)
    if args.suggest:
        from modules import script_breakdown as sb
        bd = sb.load(root, ep).get("breakdown") or {}
        rows = {str(sc["no"]): npc.suggest(sc) for sc in bd.get("scenes") or [] if isinstance(sc, dict) and sc.get("no")}
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            for no, s in rows.items():
                print(f"{no}\t{'开·' + npc.DENSITY_ZH[s['density']] if s['on'] else '关'}\t{s['reason']}")
            print("(关键词建议只作参考:正式判定按剧本原文写进 script_breakdown.json scenes[].npc)")
        return 0
    if args.write or args.check:
        errors, warns, extra = [], [], {}
        scopes = ("storyboard", "shots", "composition", "prompt") if args.check == "all" else ((args.check,) if args.check else ())
        if args.write or "prompt" in scopes:
            r = npc.sync_prompts(root, ep, args.groups, write=args.write)
            errors += r["errors"]
            extra["prompt"] = {"updated": r["updated"], "groups": r["groups"]}
        if "storyboard" in scopes:
            e, w, states = npc.check_storyboard(root, ep, res)
            errors += e
            warns += w
            extra["storyboard"] = states
        if "shots" in scopes:
            e, w = npc.check_shots(root, ep, res)
            errors += e
            warns += w
        if "composition" in scopes:
            e, w = npc.check_composition(root, ep, res)
            errors += e
            warns += w
        ok = not errors
        if args.json:
            print(json.dumps({"check": args.check or "write", "pass": ok, "master": res["master"], "errors": errors,
                              "warnings": warns, **extra}, ensure_ascii=False, indent=2))
        else:
            for e in errors:
                print("FAIL", e)
            for w in warns:
                print("WARN", w)
            if args.write:
                print("updated:", ", ".join(extra["prompt"]["updated"]) or "(无)")
            print(f"{'PASS' if ok else 'FAIL'} npc_staging {args.check or 'write'} {args.project}/{ep}: "
                  f"总开关 {res['master']} · {len(errors)} 错误 · {len(warns)} 警告")
        return 0 if ok else 1
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    print(f"总开关 output.npc_staging = {res['master']}  ·  用户覆盖 {res['overrides_rel']}")
    for no, r in res["scenes"].items():
        state = f"开·{npc.DENSITY_ZH.get(r['density'], r['density'])}" if r["on"] else "关"
        src = {"master_off": "总开关关", "user": "用户", "auto": "自动判定", "none": "未判定"}[r["source"]]
        print(f"{no}\t{state}\t[{src}]\t{r['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
