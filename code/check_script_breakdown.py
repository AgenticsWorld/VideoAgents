#!/usr/bin/env python3
"""剧本拆解表机检 script_breakdown_ok(WORKFLOW.md Phase 5 p5-breakdown 节点,2026-09-11)。

核 `story/episodes/<ep>/script_breakdown.json`:schema 版本、ep、场次表非空且场次号唯一、
emotion.intensity ∈ [0,1]、时长字段非负、tempo 受控枚举、每句台词有 speaker/text、旁白锚点
在场次表内、场景/人物 ID 对照 bible 索引(WARN)、总估时与预算偏差 >10%(WARN)。
另与推导视图(modules/script_breakdown.derive)对拍:拆解表场次数明显少于剧本解析出的场次数时 WARN。

用法:python3 code/check_script_breakdown.py --project <slug> --ep ep01 [--strict] [--json]
退出码:0 PASS(--strict 时 WARN 也算失败)、1 FAIL、2 文件缺失。
宿主 CLI,Agent 只准调用,禁止复制/改写到项目 code/。
"""
import json
import sys

from _common import parse_args

from modules import script_breakdown as sb


def main() -> int:
    args, root = parse_args("剧本拆解表机检 script_breakdown_ok",
                            configure=lambda ap: (ap.add_argument("--strict", action="store_true", help="WARN 也算失败"),
                                                  ap.add_argument("--json", action="store_true", help="机器可读输出")))
    path = sb.breakdown_path(root, args.ep)
    if not path.is_file():
        print(f"MISSING {path.relative_to(root)}(尚未产出拆解表)")
        return 2
    data = sb.read_json(path)
    if data is None:
        print(f"FAIL {path.relative_to(root)}: JSON 无法解析")
        return 1
    errors, warns = sb.validate(data, base=root, ep=args.ep)
    derived = sb.derive(root, args.ep)
    n_agent, n_derived = len(data.get("scenes") or []), len(derived.get("scenes") or [])
    if n_derived and n_agent < n_derived * 0.6:
        warns.append(f"拆解表 {n_agent} 场,剧本解析出 {n_derived} 场,疑似漏场")
    stale = [k for k, v in sb.input_mtimes(root, args.ep).items() if v > path.stat().st_mtime + 1]
    if stale:
        warns.append("拆解表比这些输入旧(须重跑拆解):" + ", ".join(stale))
    ok = not errors and not (args.strict and warns)
    if args.json:
        print(json.dumps({"check": "script_breakdown_ok", "pass": ok, "errors": errors, "warnings": warns,
                          "scenes": n_agent, "derived_scenes": n_derived, "stale_inputs": stale}, ensure_ascii=False, indent=2))
    else:
        for e in errors:
            print("FAIL", e)
        for w in warns:
            print("WARN", w)
        print(f"{'PASS' if ok else 'FAIL'} script_breakdown_ok {args.project}/{args.ep}: "
              f"{n_agent} 场 · {len(errors)} 错误 · {len(warns)} 警告")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
