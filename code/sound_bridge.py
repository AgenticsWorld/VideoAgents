#!/usr/bin/env python3
"""声桥(J-cut / L-cut)底床构建宿主 CLI(2026-10-03,过场设计四期改版;docs/transition_design.md「四期」,WORKFLOW.md §9C)。

shot_list transition_in.sound_bridge {kind j|l, s, carry bed|line}(存量 audio_lead_s 自动归一为 j/bed)。carry=bed 的边界由本 CLI 按
`mix_basis.py sources` 的取源文件(当前采纳版本)切出去人声底床片段:J = 下组开头 s 秒镜像预滚(放切点前,渐强),L = 本组结尾 s 秒镜像延续
(放切点后,渐弱);产物 edit/<ep>/sound_bridges/<B-id>.j.wav|.l.wav + manifest.json。audio-mixing 在 sources 之后、铺轨之前必跑 build;
carry=line 不出文件(桥声由切点旁的画外句承担,offscreen_lines)。本组原生轨不提前、不错位;BGM / 旁白 / wav 总长都不变。

子命令:
  list   列出本集需要声桥的边界(kind / s / carry / 取源 / 状态),不构建
  build  构建 / 刷新底床文件并写台账(幂等:取源与参数不变不重出;--force 全部重出)
  check  机检 sound_bridge_built(carry=bed 的边界都有当前 key 的文件;去人声模型回落 = WARN)

用法:python3 code/sound_bridge.py build --project <slug> --ep ep01 [--force] [--json]
退出码:list/build 0 正常(build 有 failed = 1),2 无 shot_list;check PASS/WARN = 0,FAIL = 1。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args  # noqa: E402  副作用:modules/ 入 sys.path

import sound_bridge as sbm  # noqa: E402


def _log(tag: str, msg: str) -> None:
    print(f"[{tag}] {msg}", flush=True)


def main() -> int:
    a, proj = parse_args("声桥(J/L)底床构建 / 机检",
                         configure=lambda p: (p.add_argument("cmd", choices=["list", "build", "check"]),
                                              p.add_argument("--force", action="store_true"),
                                              p.add_argument("--json", action="store_true")))
    ep = a.ep
    if not (proj / "directing" / ep / "shot_list.json").is_file():
        _log("FAIL", f"无 directing/{ep}/shot_list.json")
        return 2
    if a.cmd == "list":
        exp = sbm.expected(proj, ep)
        man = sbm.load_manifest(proj, ep) or {}
        by = {e.get("id"): e for e in man.get("bridges") or []}
        if a.json:
            print(json.dumps({"expected": exp, "manifest": man}, ensure_ascii=False, indent=2))
            return 0
        if not exp:
            _log("INFO", "本集没有声桥边界")
        for e in exp:
            m = by.get(e["id"]) or {}
            st = m.get("status") if m.get("key") == e["key"] else ("stale" if m else "missing")
            _log("BRIDGE", f"{e['id']} {e['kind'].upper()} {e['s']:g}s carry={e['carry']} src={e['src_to'] if e['kind'] == 'j' else e['src_from']} "
                           f"@{e.get('cum_start_s')} 状态={st if e['carry'] == 'bed' else 'line(不出文件)'}")
        return 0
    if a.cmd == "build":
        man = sbm.build(proj, ep, force=a.force, log=lambda m: print(m, flush=True))
        if a.json:
            print(json.dumps(man, ensure_ascii=False, indent=2))
        s = man["summary"]
        _log("DONE", f"声桥 {s['total']}:built {s['built']} / fallback {s['fallback']} / line {s['line']} / failed {s['failed']}"
                     f"(本次渲染 {s['rendered_now']})→ {sbm.bridge_dir(proj, ep) / sbm.MANIFEST}")
        return 1 if s["failed"] else 0
    res = sbm.check(proj, ep)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        for n, v in res["checks"].items():
            _log(v if v in ("PASS", "WARN", "FAIL") else "SKIP", f"{n}: {v}")
        for m in res["errors"]:
            _log("FAIL", m)
        for m in res["warnings"]:
            _log("WARN", m)
    return 1 if res["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
