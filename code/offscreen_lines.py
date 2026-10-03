#!/usr/bin/env python3
"""声画分离——人物画外对白(O.S. / V.O.)宿主 CLI(2026-10-03,docs/sound_split.md,WORKFLOW.md §8D)。

shot_list dialogue_lines[].placement ∈ on | os | vo:os/vo 句不进组视频(prompt 无 `{}`、不挂该人 audio_ref),由本 CLI 按人物
选角 × 形态 TTS 后期合成(同 §8C 口径,带台词演法;对白语音库开着时直接复用库文件),按 source_fx 做声处理,按 heard_in 镜的
可用窗口收口,落 assets/audio/voice/<ep>/offscreen/ + offscreen_manifest.json;混音经 `mix_basis.py sources` 的 offscreen_lines[]
作第四路「画外对白」轨摆位。超窗(overflow)= 回派 dialogue-rewrite 精简或 shot-planning 改 heard_in,本 CLI 不拉长画面、不硬塞。

子命令:
  plan   纯读:逐句窗口 / 估时 / 合成前置(选角 / 形态 / 样本)与机检问题,不合成
  synth  合成 / 刷新画外句音频并写台账(只补 key 变了或缺文件的句子;--force 全部重出;--groups 只处理指定组)
  check  机检:placement_valid / placement_speaker_is_cast / placement_reason_valid / offscreen_fit / post_voice_no_overlap /
         offscreen_not_in_prompt / offscreen_synced / offscreen_all_bound(声画分离关闭且无画外句 = 全部 skipped)
  set    改一句的声源字段(--shot --idx --placement [--heard-in sh… ] [--fx] [--offset] [--reason "D1|依据"]),写回 shot_list

用法:python3 code/offscreen_lines.py plan  --project <slug> --ep ep01 [--json]
     python3 code/offscreen_lines.py synth --project <slug> --ep ep01 [--groups grp003 grp007] [--force]
     python3 code/offscreen_lines.py check --project <slug> --ep ep01 [--json]
     python3 code/offscreen_lines.py set   --project <slug> --ep ep01 --shot sh015 --idx 0 --placement os --heard-in sh016 --fx door --reason "D1|听者反应镜承接"
退出码:plan/synth 0=正常(synth 有 overflow/unbound/failed 时 =1),2=无 shot_list,3=声画分离已关闭且无画外句;check PASS/WARN=0,FAIL=1。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args  # noqa: E402  副作用:modules/ 入 sys.path

import offscreen_lines as ol  # noqa: E402


def _log(tag: str, msg: str) -> None:
    print(f"[{tag}] {msg}", flush=True)


def _shot_list(proj: Path, ep: str) -> dict | None:
    p = proj / "directing" / ep / "shot_list.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
    except (ValueError, OSError):
        return None


def do_plan(proj: Path, ep: str, as_json: bool) -> int:
    sl = _shot_list(proj, ep)
    if sl is None:
        _log("FAIL", f"无 directing/{ep}/shot_list.json")
        return 2
    p = ol.plan(proj, ep, sl)
    if as_json:
        print(json.dumps(p, ensure_ascii=False, indent=2))
        return 0
    _log("MODE", f"声画分离={p['mode']};画外句 {len(p['lines'])} 句")
    if not p["lines"]:
        _log("INFO", "本集 shot_list 没有 os/vo 句" + ("(声画分离已关闭)" if p["mode"] == "off" else ""))
        return 3 if p["mode"] == "off" else 0
    for r in p["lines"]:
        w = r["window"]
        tts = r.get("tts") or {}
        _log("LINE", f"{r['group_id'] or '?'} {r['shot_id']}/l{r['idx']:02d} {r['speaker'] or r['speaker_raw'] or '?'} {r['placement']}"
                     f" fx={r['source_fx']} heard_in={','.join(r['heard_in'])} t={r['t_in_group_s']:.2f}s est={r['est_s']:.2f}s"
                     f" 窗口={w['span_s']:.1f}s(−画内 {w['on_s']:.1f} −旁白 {w['narration_s']:.1f})可用={w['available_s']:.2f}s"
                     f" tts={tts.get('status')}{(' ' + tts['reason']) if tts.get('reason') else ''} 「{r['text'][:24]}」")
    for m in p["issues"]:
        _log("WARN" if "(WARN" in m else "FAIL", m)
    return 0


def do_synth(proj: Path, ep: str, groups, force: bool, as_json: bool) -> int:
    sl = _shot_list(proj, ep)
    if sl is None:
        _log("FAIL", f"无 directing/{ep}/shot_list.json")
        return 2
    if not ol.collect(proj, ep, sl):
        _log("INFO", "本集没有 os/vo 句,不合成" + ("(声画分离已关闭)" if ol.mode(proj) == "off" else ""))
        return 3 if ol.mode(proj) == "off" else 0
    man = ol.synth(proj, ep, force=force, only_groups=groups or None, log=lambda m: print(m, flush=True), shot_list=sl)
    if as_json:
        print(json.dumps(man, ensure_ascii=False, indent=2))
    s = man["summary"]
    _log("DONE", f"画外句 {s['total']}:ok {s['ok']} / overflow {s['overflow']} / unbound {s['unbound']} / failed {s['failed']}"
                 f"(本次合成 {s['synthesized']} 句,{man['sync_seconds']}s)→ {ol.lib_dir(proj, ep) / ol.MANIFEST}")
    return 1 if (s["overflow"] or s["unbound"] or s["failed"]) else 0


def do_check(proj: Path, ep: str, as_json: bool) -> int:
    sl = _shot_list(proj, ep)
    if sl is None:
        _log("FAIL", f"无 directing/{ep}/shot_list.json")
        return 2
    res = ol.check(proj, ep, sl)
    if as_json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        for n, v in res["checks"].items():
            _log(v if v in ("PASS", "WARN", "FAIL") else "SKIP", f"{n}: {v}")
        for m in res["errors"]:
            _log("FAIL", m)
        for m in res["warnings"]:
            _log("WARN", m)
    return 1 if res["errors"] else 0


def do_set(proj: Path, ep: str, a) -> int:
    if not a.shot or a.idx is None:
        _log("FAIL", "set 需要 --shot 与 --idx")
        return 2
    reason = None
    if a.reason:
        trig, _, ev = a.reason.partition("|")
        reason = {"trigger": trig.strip(), "evidence": ev.strip()}
    try:
        ln = ol.set_line(proj, ep, a.shot, a.idx, placement=a.placement, heard_in=a.heard_in or None, source_fx=a.fx,
                         offset_s=a.offset, reason=reason, source=a.source)
    except ValueError as exc:
        _log("FAIL", str(exc))
        return 1
    _log("OK", f"{a.shot}/l{a.idx:02d} ← {json.dumps({k: ln.get(k) for k in ('placement', 'heard_in', 'source_fx', 'offset_s', 'placement_reason', 'placement_source') if k in ln}, ensure_ascii=False)}")
    for m in ol.validate(proj, ep):
        if f"{a.shot}/l{a.idx:02d}" in m:
            _log("WARN" if "(WARN" in m else "FAIL", m)
    return 0


def main() -> int:
    a, proj = parse_args(
        "声画分离:人物画外对白(O.S./V.O.)后期合成 / 机检 / 改字段",
        configure=lambda p: (p.add_argument("cmd", choices=["plan", "synth", "check", "set"]),
                   p.add_argument("--groups", nargs="*", default=[], help="synth 只处理这些组"),
                   p.add_argument("--force", action="store_true", help="synth 全部重出"),
                   p.add_argument("--json", action="store_true"),
                   p.add_argument("--shot"), p.add_argument("--idx", type=int),
                   p.add_argument("--placement", choices=list(ol.PLACEMENTS)),
                   p.add_argument("--heard-in", nargs="*", default=[]),
                   p.add_argument("--fx", choices=list(ol.SOURCE_FX)),
                   p.add_argument("--offset", type=float),
                   p.add_argument("--reason", help="trigger|evidence,如 D1|听者反应镜承接"),
                   p.add_argument("--source", default="directing", choices=["script", "directing", "user"])))
    ep = a.ep
    if a.cmd == "plan":
        return do_plan(proj, ep, a.json)
    if a.cmd == "synth":
        return do_synth(proj, ep, a.groups, a.force, a.json)
    if a.cmd == "check":
        return do_check(proj, ep, a.json)
    return do_set(proj, ep, a)


if __name__ == "__main__":
    sys.exit(main())
