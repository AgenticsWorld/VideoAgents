#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""transition_design.py — 过场设计宿主 CLI(2026-09-24,docs/transition_design.md;WORKFLOW.md §9C 二期)。

把「组边界」当一等对象:每集一张过场设计表 directing/epNN/transition_design.json(逐边界:变化诊断 + 设计 + 候选 + 状态 + 反馈);
shot_list.generation_groups[].transition_in 仍是唯一定稿字段,只由本 CLI apply 从设计表投影写回(json 读写保真,Agent 不得 JS 整写)。

  diagnose  逐边界诊断(不写文件):换场景 / 跳时段 / 阵容 / 光线 / 叙事块进出、剧本「转场:」句、导演包装声明、continuity 判定、
            字卡文字候选(时间词来源 screenplay / continuity / derived=推定)、定场素材可用性(全景锚点 / 母图)
  propose   按本集生效「过场模式」(settings.json#transitions,集级 episode.json#transitions_mode 覆盖)重出全集建议 → 设计表;
            已 accepted / rejected 的边界保留裁决;shot_list 里导演清单 / 后期页写的现有设计记 accepted(source=shot_list/post_plan)
  accept    --boundary B-grpA-grpB [--alt N | --design '<json>']:接受主设计 / 第 N 个候选 / 用户给的 transition_in,并 apply
  reject    --boundary …:裁定保持硬切(原有导演设计则恢复),并 apply
  card      --boundary … --lines "次日清晨" "天庭 · 南天门":改字卡 / 叠字幕文字(source=user),已接受则同步 apply
  apply     设计表 → shot_list.transition_in(accepted / rejected 的边界)
  check     transition_design_ok:设计表存在、契约合法(复用 transition_ok 接缝/插入规则)、有变化边界已裁决(proposed=WARN)、已接受与 shot_list 一致
  mode      --set minimal|classic|cinematic|custom|project:写 / 清集级模式覆盖(不自动重出建议;--propose 一并重出)

用法:
  python3 code/transition_design.py diagnose --project <slug> --ep epNN
  python3 code/transition_design.py propose  --project <slug> --ep epNN [--force]
  python3 code/transition_design.py accept   --project <slug> --ep epNN --boundary B-grp020-grp009 [--alt 1]
  python3 code/transition_design.py reject   --project <slug> --ep epNN --boundary B-grp020-grp009 [--note 说明]
  python3 code/transition_design.py card     --project <slug> --ep epNN --boundary B-grp020-grp009 --lines 次日清晨 "天庭 · 南天门"
  python3 code/transition_design.py apply    --project <slug> --ep epNN
  python3 code/transition_design.py check    --project <slug> --ep epNN
  python3 code/transition_design.py mode     --project <slug> --ep epNN --set classic [--propose]
退出码:check 任一 FAIL=1;其余异常=2。
渲染与成片机检见 code/render_transitions.py(plan → build → preview/render → check)。
"""
import json
import sys
from pathlib import Path

from _common import parse_args  # noqa: F401  副作用:modules/ 与仓库根入 sys.path

from modules import transition_design as td  # noqa: E402


def _fmt_design(t):
    if not isinstance(t, dict):
        return "—"
    bits = [t.get("type") or "hard_cut"]
    if t.get("duration_s"):
        bits[0] += f" {t['duration_s']:g}s"
    if t.get("join"):
        bits.append(f"style={t['join'].get('style')}")
    if t.get("freeze_s"):
        bits.append(f"定格 {t['freeze_s']:g}s")
    if t.get("hold_s"):
        bits.append(f"黑场 {t['hold_s']:g}s")
    for x in t.get("inserts") or []:
        extra = ""
        if x.get("kind") == "title_card":
            extra = " " + " / ".join((x.get("card") or {}).get("lines") or [])
        elif x.get("kind") == "establishing":
            s = x.get("source") or {}
            extra = f" {s.get('mode')} {s.get('scene_id')}/{s.get('anchor_id') or s.get('file', '')}"
        bits.append(f"+{x.get('kind')} {float(x.get('duration_s') or 0):g}s{extra}")
    if t.get("overlay_card"):
        bits.append("叠字 " + " / ".join(t["overlay_card"].get("lines") or []))
    return " · ".join(bits)


def main(argv=None):
    def configure(ap):
        ap.add_argument("cmd", choices=("diagnose", "propose", "accept", "reject", "card", "apply", "check", "mode"))
        ap.add_argument("--boundary", help="边界 id,如 B-grp020-grp009")
        ap.add_argument("--alt", type=int, help="accept:候选序号(0 起)")
        ap.add_argument("--design", help="accept:直接给 transition_in JSON")
        ap.add_argument("--lines", nargs="*", help="card:字卡 / 叠字幕文字(1–3 行)")
        ap.add_argument("--note", default="", help="reject:说明")
        ap.add_argument("--force", action="store_true", help="propose:忽略已有裁决重出(feedback 保留)")
        ap.add_argument("--set", dest="set_mode", help="mode:minimal|classic|cinematic|custom|project(=清集级覆盖)")
        ap.add_argument("--propose", action="store_true", help="mode:切换后立即重出建议")
        ap.add_argument("--json", action="store_true", help="以 JSON 输出")

    args, proj = parse_args(__doc__.splitlines()[0], configure=configure, argv=argv)
    ep = args.ep
    try:
        if args.cmd == "diagnose":
            rows = td.diagnose(proj, ep)
            if args.json:
                print(json.dumps(rows, ensure_ascii=False, indent=2))
            else:
                eff = td.effective(proj, ep)
                print(f"[INFO ] 过场模式 {eff['mode']}({eff['mode_source']});字卡 {'允许' if eff['allow_cards'] else '禁止'};插入预算 {eff['insert_budget_pct']:g}%")
                for r in rows:
                    d = r["diagnosis"]
                    flags = [k for k in ("scene_change", "time_jump", "cast_change", "light_jump") if d.get(k)]
                    if d.get("narrative_block_edge") in ("enter", "exit"):
                        flags.append("block_" + d["narrative_block_edge"])
                    print(f"  {r['id']:<22} {d['class']:<12} {'/'.join(flags) or '—':<40} 字卡 {r['card_lines']}"
                          f"{' (' + (d.get('time_word_source') or '') + ')' if d.get('time_word') else ''}"
                          f"  定场 {'√' if r.get('establishing') else '×'}  现状 {_fmt_design(r.get('current'))}")
            return 0
        if args.cmd == "propose":
            data = td.propose(proj, ep, force=args.force)
            n = {k: sum(1 for b in data["boundaries"] if b.get("status") == k) for k in ("proposed", "accepted", "rejected", "none")}
            print(f"[DONE ] 设计表 {td.design_path(proj, ep).relative_to(proj)}:模式 {data['mode']}({data['mode_source']});"
                  f"建议 {n['proposed']} / 已接受 {n['accepted']} / 已裁硬切 {n['rejected']} / 无需 {n['none']}")
            for b in data["boundaries"]:
                if b.get("status") in ("proposed", "accepted"):
                    print(f"  {b['id']:<22} {b['status']:<9} {_fmt_design(b.get('design'))}"
                          + (f"   候选 {[a['label'] for a in b.get('alternatives') or []]}" if b.get("alternatives") else ""))
            return 0
        if args.cmd in ("accept", "reject", "card"):
            if not args.boundary:
                raise SystemExit("[FAIL] 须给 --boundary")
            if args.cmd == "accept":
                design = json.loads(args.design) if args.design else None
                b = td.accept(proj, ep, args.boundary, alt=args.alt, transition_in=design, by="cli")
            elif args.cmd == "reject":
                b = td.reject(proj, ep, args.boundary, by="cli", note=args.note)
            else:
                if not args.lines:
                    raise SystemExit("[FAIL] card 须给 --lines")
                b = td.set_card_lines(proj, ep, args.boundary, args.lines)
            print(f"[DONE ] {b['id']} {b['status']}:{_fmt_design(b.get('design'))}(已 apply 到 shot_list)")
            return 0
        if args.cmd == "apply":
            r = td.apply(proj, ep)
            print(f"[DONE ] apply:写回 {len(r['written'])} 组 {r['written']};shot_list {'已更新' if r.get('changed') else '无变化'}")
            return 0
        if args.cmd == "mode":
            if not args.set_mode:
                eff = td.effective(proj, ep)
                print(json.dumps(eff, ensure_ascii=False, indent=2))
                return 0
            eff = td.set_episode_mode(proj, ep, None if args.set_mode == "project" else args.set_mode)
            print(f"[DONE ] 本集过场模式 {eff['mode']}({eff['mode_source']})")
            if args.propose:
                td.propose(proj, ep)
                print(f"[DONE ] 已按 {eff['mode']} 重出建议")
            return 0
        ok, items = td.check(proj, ep)
        for it in items:
            print(f"[{it['result']:<5}] {it['check']}: {it['detail']}")
        print(f"[{'PASS' if ok else 'FAIL'} ] transition_design_ok:{len(items)} 项")
        return 0 if ok else 1
    except (KeyError, ValueError, FileNotFoundError) as ex:
        print(f"[FAIL] {ex}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
