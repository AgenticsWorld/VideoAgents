#!/usr/bin/env python3
"""原生先入(声画分离三期,2026-10-03)宿主 CLI:docs/sound_split.md「三期」,WORKFLOW.md §8D。

画内句 dialogue_lines[].native_lead {shot, s, reason, source} = 让说话人在同组前一镜(说话人不在画内)就在画外开口,切过来后接着说完
(组内 J-cut,Seedance 2.5 原生;fengshen3 ep07 grp008 实测 J 1/1 过、L 0/2 不开放)。

子命令:
  plan     列出已标的句(含问题)+ 自动建议(sound_split=auto:满足 P3–P8 且命中 N1 插入镜 / N2 听者先行 / N3 定场起声的画内首句)
  set      给一句标 native_lead:--shot --idx [--s 秒] [--reason "N1|依据"] [--source directing|user];前一镜由时间线推导
  clear    去掉一句的 native_lead:--shot --idx
  apply    把 plan 的自动建议全部写进 shot_list(shot-planning 工位用;--source directing)
  check    机检 native_lead_valid(P1–P8)+ native_lead_bound(prompt 标记句;组 prompt 未写 = skipped)
  sync     把 native_lead 写进组 prompt(--write;不带只查):前镜段末【原生先入】{先入}、本镜 {全句}→{余句} + 接续句;无 native_lead 的组清理残留
  audible  出片后核验:组 clip 人声起点落在前一镜窗口(先入 ≥0.3s)= PASS,否则 WARN(不阻断)

用法:python3 code/sync_native_leads.py plan  --project <slug> --ep epNN [--json]
     python3 code/sync_native_leads.py set   --project <slug> --ep epNN --shot sh041 --idx 0 --reason "N1|前镜 sh040 箭主观镜无人物"
     python3 code/sync_native_leads.py sync  --project <slug> --ep epNN [--groups grp008 …] --write
     python3 code/sync_native_leads.py check --project <slug> --ep epNN
退出码:0 通过 / 无事可做;1 有 FAIL;2 无 shot_list。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args  # noqa: E402  副作用:modules/ 与仓库根入 sys.path

from modules import native_lead as nl  # noqa: E402


def _log(tag: str, msg: str) -> None:
    print(f"[{tag}] {msg}", flush=True)


def main() -> int:
    a, proj = parse_args("原生先入(组内 J-cut)标注 / prompt 同步 / 机检",
                         configure=lambda p: (p.add_argument("cmd", choices=["plan", "set", "clear", "apply", "check", "sync", "audible"]),
                                              p.add_argument("--groups", nargs="*", default=[]),
                                              p.add_argument("--shot"), p.add_argument("--idx", type=int),
                                              p.add_argument("--s", type=float), p.add_argument("--reason", help="trigger|evidence"),
                                              p.add_argument("--source", default="user", choices=["directing", "user"]),
                                              p.add_argument("--write", action="store_true"), p.add_argument("--json", action="store_true")))
    ep = a.ep
    if not (proj / "directing" / ep / "shot_list.json").is_file():
        _log("FAIL", f"无 directing/{ep}/shot_list.json")
        return 2
    if a.cmd == "plan":
        recs, sugg = nl.collect(proj, ep), nl.suggest(proj, ep)
        ok, why = nl.mode_ok(proj)
        if a.json:
            print(json.dumps({"mode_ok": ok, "why": why, "leads": recs, "suggestions": sugg}, ensure_ascii=False, indent=2))
            return 0
        _log("MODE", "原生先入可用" if ok else f"原生先入不可用:{why}")
        for r in recs:
            _log("LEAD", f"{r['group_id']} {r['shot_id']}/l{r['idx']:02d} {r['speaker']} 前镜 {r['prev']} s={r['s']:g} "
                         f"「{r['lead']}」|「{r['rest']}」 {r['reason'].get('trigger')} {r['source']}" + (f"  ✗ {';'.join(r['issues'])}" if r["issues"] else ""))
        for s in sugg:
            _log("SUGG", f"{s['group_id']} {s['shot_id']}/l00 {s['speaker']} ← {s['prev']} {s['trigger']} s={s['s']:g} 「{s['lead']}」|「{s['rest']}」"
                         + ("(已标)" if s["already"] else "") + f"  {s['evidence']}")
        if not recs and not sugg:
            _log("INFO", "本集没有已标或可建议的原生先入")
        return 0
    if a.cmd in ("set", "clear"):
        if not a.shot or a.idx is None:
            _log("FAIL", f"{a.cmd} 需要 --shot 与 --idx")
            return 2
        reason = None
        if a.reason:
            t, _, ev = a.reason.partition("|")
            reason = {"trigger": t.strip(), "evidence": ev.strip()}
        try:
            ln = nl.set_line(proj, ep, a.shot, a.idx, on=(a.cmd == "set"), s=a.s, reason=reason, source=a.source)
        except ValueError as e:
            _log("FAIL", str(e))
            return 1
        _log("OK", f"{a.shot}/l{a.idx:02d} native_lead={json.dumps(ln.get('native_lead'), ensure_ascii=False)}")
        return 0
    if a.cmd == "apply":
        n = 0
        for s in nl.suggest(proj, ep):
            if s["already"]:
                continue
            try:
                nl.set_line(proj, ep, s["shot_id"], 0, on=True, s=s["s"], reason={"trigger": s["trigger"], "evidence": s["evidence"]}, source="directing")
                n += 1
                _log("SET", f"{s['group_id']} {s['shot_id']}/l00 ← {s['prev']} {s['trigger']}")
            except ValueError as e:
                _log("WARN", f"{s['shot_id']}: {e}")
        _log("DONE", f"写入 {n} 句")
        return 0
    if a.cmd == "audible":
        res = nl.audible(proj, ep, a.groups or None)
        if a.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            for r in res["rows"]:
                _log(r["status"] if r["status"] in ("PASS", "WARN") else "SKIP", json.dumps(r, ensure_ascii=False))
            for w in res["warnings"]:
                _log("WARN", w)
            _log("DONE", f"native_lead_audible: {res['checks'].get('native_lead_audible')}")
        return 0
    # check / sync
    res = nl.sync_episode(proj, ep, a.groups or None, write=(a.cmd == "sync" and a.write))
    verrs = nl.validate(proj, ep)
    if a.json:
        print(json.dumps({**res, "valid_errors": verrs}, ensure_ascii=False, indent=2))
    else:
        for e in verrs:
            _log("FAIL", e)
        for g in res["groups"]:
            if g["updated"]:
                _log("WRITE", f"{g['group_id']} prompt 已写入原生先入句")
            for e in g["errors"]:
                _log("FAIL", e)
            if g["skipped"]:
                _log("SKIP", f"{g['group_id']} {g['skipped']}")
        for w in res["warnings"]:
            _log("WARN", w)
        n = len(res["leads"])
        _log("DONE", f"原生先入 {n} 句;native_lead_valid {'FAIL' if verrs else 'PASS'};native_lead_bound {'FAIL' if res['errors'] else ('PASS' if n else 'skipped')}")
    return 1 if (verrs or res["errors"]) else 0


if __name__ == "__main__":
    sys.exit(main())
