#!/usr/bin/env python3
"""check_dialogue_audible.py — 出片后对白镜「有声段」核对 dialogue_audible(2026-10-03,docs/time_cost.md 第四落点的兜底)。

不转写、不下载模型(规约禁止自发 ASR):只从组 clip 音轨按自相关找「像人声的有声段」(80–400 Hz 周期性 + 能量,modules/voice_activity.py),
对每个对白镜,在它的计划时段(shot_list 累计时长;有 meta boundary_map 用 boundary_map)前后各放宽 --pad 秒,
取窗内最长连续有声段 / 该镜台词估时:
  < --fail-ratio(默认 0.3)  FAIL  台词疑似整句缺失(前科 fengshen3 ep07 grp011 sh084:5s 台词窗内只有 0.9s 人声)
  < --warn-ratio(默认 0.5)  WARN  疑似被截短 / 赶词
这是安全网不是裁判:同窗相邻镜的台词会互相顶替通过,只能抓「整句没了」这种大漏。

用法:python3 code/check_dialogue_audible.py --project <slug> --ep ep07 grp011 [grp012 …]   # 缺省全集已出片的对白组
退出码:0 通过(可含 WARN);1 有 FAIL。报告打印到 stdout,同时写 runs/_checks/dialogue_audible_<ep>.json(--no-report 不写)。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args  # noqa: E402

# 有声段检测(自相关基频 + 能量)在 modules/voice_activity.py,与 dub_group.py 的开口时段检测共用一套口径
from voice_activity import voiced_mask, runs_from_mask, longest_in_window, extract_wav  # noqa: E402,F401


def main(argv=None):
    def cfg(ap):
        ap.add_argument("groups", nargs="*")
        ap.add_argument("--pad", type=float, default=1.5, help="计划时段前后放宽秒数(默认 1.5)")
        ap.add_argument("--fail-ratio", type=float, default=0.3)
        ap.add_argument("--warn-ratio", type=float, default=0.5)
        ap.add_argument("--no-report", action="store_true")
    args, base = parse_args("出片后对白镜有声段核对 dialogue_audible", configure=cfg, argv=argv)
    sl = json.loads((base / "directing" / args.ep / "shot_list.json").read_text(encoding="utf-8"))
    shots = {s["shot_id"]: s for s in sl.get("shots") or []}
    errs, warns, rows = [], [], []
    for g in sl.get("generation_groups") or []:
        gid = g.get("group_id")
        if args.groups and gid not in set(args.groups):
            continue
        ids = g.get("shots") or []
        if not any(shots.get(x, {}).get("is_dialogue") and shots[x].get("dialogue_lines") for x in ids):
            continue
        clip = base / "assets" / "clips" / args.ep / f"{gid}.mp4"
        if not clip.is_file():
            continue
        meta_p = base / "assets" / "clips" / args.ep / f"{gid}.meta.json"
        bmap = {}
        try:
            meta = json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.is_file() else {}
            for b in meta.get("boundary_map") or []:
                bmap[b.get("shot_id")] = (float(b.get("start_s")), float(b.get("end_s")))
        except (ValueError, OSError, TypeError):
            bmap = {}
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "a.wav"
            try:
                extract_wav(clip, wav)
                runs = runs_from_mask(voiced_mask(wav))
            except Exception as e:  # noqa: BLE001
                warns.append(f"{gid} dialogue_audible: 音轨分析失败 {e}")
                continue
        t = 0.0
        for sid in ids:
            s = shots.get(sid) or {}
            dur = float(s.get("duration_s") or 0)
            a, b = bmap.get(sid, (t, t + dur))
            t += dur
            if not (s.get("is_dialogue") and s.get("dialogue_lines")):
                continue
            est = sum(float(l.get("est_duration_s") or 0) for l in s["dialogue_lines"] if isinstance(l, dict))
            if est <= 0:
                continue
            longest = longest_in_window(runs, a - args.pad, b + args.pad)
            ratio = round(longest / est, 2)
            row = {"group_id": gid, "shot_id": sid, "window_s": [round(a, 2), round(b, 2)], "est_s": round(est, 2),
                   "longest_voiced_s": longest, "ratio": ratio, "status": "ok"}
            if ratio < args.fail_ratio:
                row["status"] = "missing"
                errs.append(f"{gid}/{sid} dialogue_audible: 计划 {a:.1f}–{b:.1f}s(±{args.pad:g}s)窗内最长人声段 {longest}s,"
                            f"台词估时 {est:.1f}s(比 {ratio}),疑似整句缺失")
            elif ratio < args.warn_ratio:
                row["status"] = "short"
                warns.append(f"{gid}/{sid} dialogue_audible: 窗内最长人声段 {longest}s / 估时 {est:.1f}s(比 {ratio}),疑似截短或赶词")
            rows.append(row)
    for w in warns:
        print("WARN", w)
    for e in errs:
        print("FAIL", e)
    if not args.no_report:
        out = base / "runs" / "_checks" / f"dialogue_audible_{args.ep}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"episode": args.ep, "rows": rows, "errors": errs, "warnings": warns}, ensure_ascii=False, indent=2) + "\n")
        print(f"报告 → {out}")
    print(f"dialogue_audible: 对白镜 {len(rows)} 个,疑似缺失 {sum(1 for r in rows if r['status'] == 'missing')},"
          f"疑似截短 {sum(1 for r in rows if r['status'] == 'short')};{len(errs)} FAIL / {len(warns)} WARN")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
