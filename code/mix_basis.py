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
  duration_s     该文件实测时长;cum_start_s = 混音时间线上的组起点(按 src 实测累计,BGM / 旁白摆位用这个)
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
    rows = mb.with_timing(proj, ep, mb.current_basis(proj, ep, plan))
    if not rows:
        _log("FAIL", "没有分镜组(timeline / shot_list 都为空)")
        return 1
    pending = {}
    for r in rows:
        latest = max([int(x.get("v") or 0) for x in pp.group_versions(plan, r["group_id"])] or [0])
        if latest > int(r["v"]):
            pending[r["group_id"]] = latest
    total = round(sum(r["duration_s"] for r in rows), 6)
    out = {"episode": ep, "groups": rows, "total_duration_s": total, "delta_vs_v0_s": mb.basis_delta(rows),
           "groups_on_post_version": sum(1 for r in rows if int(r["v"]) > 0),
           "ops_fingerprint": mb.ops_fingerprint(rows), "plan_fingerprint": pp.plan_fingerprint(plan),
           "unadopted_newer_versions": pending}
    missing = [r["group_id"] for r in rows if not r.get("src")]
    if as_json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        _log("PLAN", f"{ep} 混音取源:{len(rows)} 组,{out['groups_on_post_version']} 组在后期采纳版本,"
                     f"总时长 {total:.3f}s(相对母本 Δ{out['delta_vs_v0_s']:+.3f}s),基准指纹 {out['ops_fingerprint']}")
        for r in rows:
            ops = timemap.describe(r["time_ops"]) if r["time_ops"] else ""
            _log("SRC ", f"{r['group_id']} v{r['v']} {r.get('src') or '(无文件)'} {r['duration_s']:.3f}s @{r['cum_start_s']:.3f}s"
                         + (f"  [{ops}]" if ops else ""))
        for gid, v in pending.items():
            _log("WARN", f"{gid} 有未采纳的更新版本 v{v},混音按当前指针取源;要进成片须先在后期页采纳再开混")
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
                 f"Δ{man['delta_s']:+.3f}s,基准指纹 {man['ops_fingerprint']},任务 {task_id}")
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
