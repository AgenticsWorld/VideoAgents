#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mix_basis.py — 混音基准宿主 CLI + 机检 mix_basis_current(WORKFLOW.md §8B ④ / §9B,2026-09-23)。

p8-mix 的原生轨**按各组当前采纳版本**抽取(后期页「采纳」的删段 / 慢动作 / 插黑定格版本;未采纳的组取 v0 母本),
BGM cue 与旁白挂点按剪后时间线(cum_start_s)摆位;交付时盖章记录「按哪一版混的」。出成片时宿主比对清单与台账:
一致 → 外挂声轨 / 字幕不再套 post_versions 层 timemap 重映射;不一致 → 旧口径兜底或停手上报。

用法:
  python3 code/mix_basis.py sources --project <slug> --ep epNN [--json]   # 开混前:各组取源(当前版本文件 / 时长 / 起点 / time_ops)
  python3 code/mix_basis.py stamp   --project <slug> --ep epNN --task-id <task_id> [--audio assets/audio/final/epNN.wav]
                                                                          # 交付前:盖章写 assets/audio/final/epNN.mix.json
  python3 code/mix_basis.py check   --project <slug> --ep epNN [--json]   # 消费侧(post check / finalize):清单 vs 当前台账

sources 输出的每组字段:
  src            本组混音取源(项目相对路径;v>0 = 后期采纳版本,v0 = 母本)
  sound_v        原生声轨内容所在版本(后期页去人声 / 去环境声改过才 > 0,2026-10-01);盖章后它变了 = check FAIL,须重混
  dub_fp         后期配音指纹(§8C,2026-10-03;p7-dub 的 dub_manifest:配音时刻 + 逐句时段 + 去人声状态;未配音 = null);
                 盖章后它变了(重配音 / 改时段)= check FAIL,须重混;dub_predates_version=true = 当前采纳版本建于配音之前、
                 文件里没有配音 = FAIL(先回滚到母本或重做该版本)。配音后 clip 的对白轨已是 TTS,混音不另铺对白
  duration_s     该文件实测时长;cum_start_s = 混音时间线上的组起点(按 src 实测累计 **+ 本组前的组边界层**,BGM / 旁白摆位用这个)
  boundary_before_s 本组前组边界占时(定格 + 黑场停留 + 插入段,过场设计 2026-09-24):原生轨在此留白(按 boundaries[].audio:
                 mute 静音 / sustain 延续前段房间声),BGM / 旁白照常跨过去铺——跨越边界的 cue 不断;cum_start_groups_s = 不含边界层的组累计
顶层 boundaries[]:每个有占时或有声桥的边界 {from_group, to_group, freeze_s, hold_s, insert_s, total_s, audio, type, inserts[], sound_bridge};
  boundary_delta_s = Σ占时;混音 wav 总长应 = Σ组时长 + boundary_delta_s(stamp 核对)
  sound_bridge(四期 2026-10-03 改版,modules/sound_bridge.py;原 audio_lead_s「整轨提前」语义作废):{kind j|l, s, carry bed|line, file, status[, duck_db]}
                 J 声先入:carry=bed 时 file(下组底床镜像预滚)从 cum_start_s − s 起铺、渐强;**本组与下组原生轨都不提前、不错位**;
                 carry=line 无文件,下组首句画外句经 offscreen_lines[] 的 t0(< cum_start_s)先到。
                 L 声延续:carry=bed 时 file(本组底床镜像延续)从 cum_start_s 起铺 s 秒、渐弱,下组原生轨前 s 秒从 duck_db 渐强到 0;
                 carry=line 时前组末句画外句 t0+duration 越过切点。status missing/stale = 先跑 code/sound_bridge.py build。
                 不占时、不进 timemap、不改 wav 总长;参数进边界指纹、文件进 sound_bridge 指纹,改了 = 须重混
  cum_start_v0_s 母本基准起点(只用于换算旧口径的 meta boundary_map:组内时刻经 time_ops 映射 = timemap.map_time)
  time_ops       该版本相对母本的组内时长编辑(删段 out_len=0 / 变速 / 插入);boundary_map、对白开口时段都是母本
                 基准,落在删除区间内的事件在本版本里已不存在,不得再往上铺声音

退出码:sources/stamp 全部正常=0,缺组文件或产物=1;check PASS/WARN=0,FAIL=1。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args  # noqa: E402  副作用:modules/ 入 sys.path

import mix_manifest as mb  # noqa: E402
import post_plan as pp  # noqa: E402
import timemap  # noqa: E402


def _log(tag: str, msg: str) -> None:
    print(f"[{tag}] {msg}", flush=True)


def do_sources(proj: Path, ep: str, as_json: bool) -> int:
    plan = pp.load_plan(proj, ep)
    basis = mb.current_basis(proj, ep, plan)
    bounds = mb.boundary_layer(proj, ep, basis)
    rows = mb.with_timing(proj, ep, basis, bounds)
    if not rows:
        _log("FAIL", "没有分镜组(timeline / shot_list 都为空)")
        return 1
    bounds = mb.sound_bridge_rows(proj, ep, bounds)     # 声桥(2026-10-03):并进底床文件状态
    pending = {}
    for r in rows:
        latest = max([int(x.get("v") or 0) for x in pp.group_versions(plan, r["group_id"])] or [0])
        if latest > int(r["v"]):
            pending[r["group_id"]] = latest
    total = round(sum(r["duration_s"] for r in rows), 6)
    # 画外对白轨(声画分离 2026-10-03):os/vo 句按 heard_in 窗口合成的集级独立声轨,逐句绝对摆位 + 台账指纹
    off_man = mb.offscreen_manifest(proj, ep)
    off_all = mb.offscreen_rows(proj, ep, rows, include_dropped=True)
    off_rows = [o for o in off_all if not o.get("dropped") and o.get("t0") is not None]
    off_dropped = [o for o in off_all if o.get("dropped") or o.get("t0") is None]
    off_fp = mb.offscreen_fingerprint(proj, ep, off_man)
    off_stale = mb.offscreen_stale(proj, ep, off_man)
    out = {"episode": ep, "groups": rows, "total_duration_s": round(total + mb.boundary_delta(bounds), 6),
           "groups_total_s": total, "delta_vs_v0_s": mb.basis_delta(rows),
           "groups_on_post_version": sum(1 for r in rows if int(r["v"]) > 0),
           "ops_fingerprint": mb.ops_fingerprint(rows), "plan_fingerprint": pp.plan_fingerprint(plan),
           "boundaries": bounds, "boundary_delta_s": mb.boundary_delta(bounds), "boundary_fingerprint": mb.boundary_fingerprint(bounds),
           "sound_bridge_fingerprint": mb.sound_bridge_fingerprint(proj, ep),
           "offscreen_lines": off_rows, "offscreen_fingerprint": off_fp,
           "unadopted_newer_versions": pending}
    missing = [r["group_id"] for r in rows if not r.get("src")]
    if as_json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        _log("PLAN", f"{ep} 混音取源:{len(rows)} 组,{out['groups_on_post_version']} 组在后期采纳版本,"
                     f"组时长 {total:.3f}s + 组边界层 {out['boundary_delta_s']:.3f}s = 混音总长 {out['total_duration_s']:.3f}s"
                     f"(相对母本 Δ{out['delta_vs_v0_s']:+.3f}s),基准指纹 {out['ops_fingerprint']},边界指纹 {out['boundary_fingerprint']}")
        b_by = {b["to_group"]: b for b in bounds}
        for r in rows:
            ops = timemap.describe(r["time_ops"]) if r["time_ops"] else ""
            b = b_by.get(r["group_id"])
            if b and b["total_s"] > 0:
                parts = ([f"定格 {b['freeze_s']:g}s"] if b["freeze_s"] else []) + ([f"黑场 {b['hold_s']:g}s"] if b["hold_s"] else []) \
                    + [f"{x['kind']} {x['duration_s']:g}s" for x in b["inserts"]]
                _log("BND ", f"{b['from_group']}→{b['to_group']} 组边界 +{b['total_s']:.3f}s({' + '.join(parts)};原生轨 {b['audio']},BGM/旁白连续铺过)")
            sb = (b or {}).get("sound_bridge") if b else None
            if sb:
                if sb["kind"] == "j":
                    how = (f"底床预滚 {sb.get('file') or '(未构建)'} 从 {r['cum_start_s'] - sb['s']:.3f}s 起铺、{sb['s']:g}s 渐强" if sb["carry"] == "bed"
                           else "下组首句画外句经 offscreen_lines[] 先到")
                    _log("BRIDGE", f"{b['from_group']}→{b['to_group']} 声先入 J {sb['s']:g}s:{how};本组原生轨仍从 {r['cum_start_s']:.3f}s 同步起,不提前")
                else:
                    how = (f"底床延续 {sb.get('file') or '(未构建)'} 从 {r['cum_start_s']:.3f}s 起铺 {sb['s']:g}s 渐弱" if sb["carry"] == "bed"
                           else "前组末句画外句越过切点")
                    _log("BRIDGE", f"{b['from_group']}→{b['to_group']} 声延续 L {sb['s']:g}s:{how};本组原生轨前 {sb['s']:g}s 从 {sb.get('duck_db', -12):g} dB 渐强")
                if sb["carry"] == "bed" and sb.get("status") in ("missing", "stale", "failed", None):
                    _log("WARN", f"{b['from_group']}→{b['to_group']} 声桥底床未构建 / 已过期({sb.get('status')}):先 python3 code/sound_bridge.py build 再开混")
            _log("SRC ", f"{r['group_id']} v{r['v']} {r.get('src') or '(无文件)'} {r['duration_s']:.3f}s @{r['cum_start_s']:.3f}s"
                         + (f"  [{ops}]" if ops else "") + (f"  dub={r['dub_fp']}" if r.get("dub_fp") else ""))
            if r.get("dub_predates_version"):
                _log("FAIL", f"{r['group_id']} 当前采纳版本 v{r['v']} 建于后期配音之前,文件里没有配音:先在后期页回滚到母本或重做该版本")
        for gid, v in pending.items():
            _log("WARN", f"{gid} 有未采纳的更新版本 v{v},混音按当前指针取源;要进成片须先在后期页采纳再开混")
        for o in off_rows:
            _log("OFFSCREEN", f"{o.get('group_id')} {o.get('shot_id')}/l{int(o.get('idx') or 0):02d} {o.get('speaker')} {o.get('placement')} "
                              f"t0={float(o.get('t0') or 0):.3f}s dur={float(o.get('duration_s') or 0):.3f}s fx={o.get('source_fx') or 'plain'}"
                              + (f" gain={o['gain_db']:+.1f}dB" if o.get("gain_db") not in (None, 0, 0.0) else ""))
        for o in off_dropped:
            _log("SKIP", f"{o.get('group_id')} {o.get('shot_id')}/l{int(o.get('idx') or 0):02d} 画外句落在该组采纳版本的删段内,本版本里已不存在,不铺")
        if off_rows:
            _log("PLAN", f"画外对白轨 {len(off_rows)} 句(独立声轨,按 t0 铺、不占时、不改 wav 总长),指纹 {off_fp}")
        if off_stale:
            _log("WARN", ("shot_list 有画外 / V.O. 句但尚无画外对白台账" if not off_man else
                          "画外对白台账落后于当前 shot_list 的画外句(新增 / 删句 / 改位 / 改效果)")
                 + ":先 python3 code/offscreen_lines.py synth 重出再开混")
    if missing:
        _log("FAIL", f"{len(missing)} 组无视频文件:{', '.join(missing[:8])}")
        return 1
    return 0


def do_stamp(proj: Path, ep: str, task_id: str, audio: str | None) -> int:
    try:
        man, warns = mb.write_manifest(proj, ep, task_id, audio=(proj / audio) if audio else None)
    except FileNotFoundError as e:
        _log("FAIL", str(e))
        return 1
    for w in warns:
        _log("WARN", w)
    _log("DONE", f"盖章 {mb.manifest_path(proj, ep).relative_to(proj)}:{man['groups_on_post_version']} 组在后期版本,"
                 f"Δ{man['delta_s']:+.3f}s,基准指纹 {man['ops_fingerprint']},组边界层 {len(man['boundaries']['ops'])} 处 "
                 f"+{man['boundaries']['delta_s']:.3f}s(指纹 {man['boundaries']['fingerprint']}),任务 {task_id}")
    return 0


def do_check(proj: Path, ep: str, as_json: bool) -> int:
    res = mb.compare(proj, ep)
    tag, detail = mb.check_row(res)
    if as_json:
        print(json.dumps({"name": mb.CHECK_NAME, "status": tag, "detail": detail, **res}, ensure_ascii=False, indent=2))
    else:
        _log("CHECK", f"{mb.CHECK_NAME}: {tag}  {detail}")
    return 1 if tag == "FAIL" else 0


def main(argv=None):
    def configure(ap):
        ap.add_argument("cmd", choices=("sources", "stamp", "check"))
        ap.add_argument("--json", action="store_true", help="sources/check:机器可读输出")
        ap.add_argument("--task-id", default=None, help="stamp 必填:盖章任务 ID(留痕)")
        ap.add_argument("--audio", default=None, help="stamp:混音产物(项目相对路径,默认 assets/audio/final/epNN.wav)")

    args, proj = parse_args(__doc__.splitlines()[0], configure=configure, argv=argv)
    if args.cmd == "sources":
        return do_sources(proj, args.ep, args.json)
    if args.cmd == "stamp":
        if not args.task_id:
            _log("FAIL", "--stamp 必须带 --task-id(盖章留痕)")
            return 1
        return do_stamp(proj, args.ep, args.task_id, args.audio)
    return do_check(proj, args.ep, args.json)


if __name__ == "__main__":
    sys.exit(main())
