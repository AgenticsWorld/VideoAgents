#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""music_library.py — 项目音乐库宿主 CLI + 机检 music_library_synced(WORKFLOW.md §8 music,2026-09-27)。

每集新增的 BGM 入库(`assets/audio/library/music/`:index.json + MUS-NNNN.<ext>)并标记数据;之后的剧集配乐
**先查库、优先复用,没有合适的再新生成**。「谁在用」由各集 cue sheet 推导,sync 整集重算、重跑幂等。

用法(p8-music 的标准顺序:list/search → use 或 genmedia music → 写 cue sheet → sync --write → check):
  python3 code/music_library.py list   --project <slug> [--all] [--json]            # 库内曲目 + 标记数据 + 使用集
  python3 code/music_library.py search --project <slug> [--query "弦乐 悬疑"] [--mood 紧张]
                                       [--min-duration 20] [--max-duration 90] [--json]   # 关键词打分排序(不设 = 全列)
  python3 code/music_library.py use    --project <slug> --ep epNN --track MUS-0003 [--output epNN_bgm_02.mp3]
                                                                                     # 复制库内曲目到本集 bgm 目录,打印 cue 的 license 段
  python3 code/music_library.py sync   --project <slug> [--ep epNN] [--write] [--backfill] [--json]
                                                                                     # 按 cue sheet 对账:新曲入库、used_in 重算(缺省只预演)
  python3 code/music_library.py annotate --project <slug> --track MUS-0003 [--title ..] [--mood ..] [--genre ..]
                                       [--tempo-bpm 96] [--instrumentation ..] [--tags a,b] [--description ..]
  python3 code/music_library.py add    --project <slug> --file <项目相对或绝对路径> [--ep epNN] [标记参数同 annotate]
                                                                                     # 手工入库一首(不经 cue sheet)
  python3 code/music_library.py check  --project <slug> --ep epNN [--json]           # 机检 music_library_synced

sync 不带 --ep = 全部有 cue sheet 的集;--backfill = 存量回补(音乐库上线前已交付的集:首次对账标 backfilled,
  机检对缺 music_library_report 只 WARN,cue sheet 之后被重写过即恢复 FAIL;p8-music 交付时不要带 --backfill)。
退出码:list/search/use/sync/annotate/add 正常=0,出错=1(sync 有 cue 文件缺失 / track_id 查不到也=1);check PASS/WARN=0,FAIL=1。
"""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args  # noqa: E402  副作用:modules/ 与仓库根入 sys.path

from modules import music_library as ml  # noqa: E402  本脚本与模块同名,须走 modules. 前缀

CMDS = ("list", "search", "use", "sync", "annotate", "add", "check")


def _log(tag: str, msg: str) -> None:
    print(f"[{tag}] {msg}", flush=True)


def _configure(ap):
    ap.add_argument("cmd", choices=CMDS)
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--all", action="store_true", help="list/search:连被替换掉的旧曲(superseded)一起列")
    ap.add_argument("--query", default="", help="search:关键词(空格 / 逗号分隔)")
    ap.add_argument("--min-duration", type=float, default=None)
    ap.add_argument("--max-duration", type=float, default=None)
    ap.add_argument("--track", default="", help="use/annotate:曲目 ID(MUS-NNNN)")
    ap.add_argument("--output", default="", help="use:本集 bgm 目录下的文件名,缺省沿用库内文件名")
    ap.add_argument("--file", default="", help="add:要入库的音频文件")
    ap.add_argument("--write", action="store_true", help="sync:落盘(缺省只预演)")
    ap.add_argument("--backfill", action="store_true", help="sync:存量回补")
    for k in ("title", "mood", "genre", "instrumentation", "description", "prompt"):
        ap.add_argument(f"--{k}", default=None)
    ap.add_argument("--tempo-bpm", type=float, default=None)
    ap.add_argument("--intensity", type=float, default=None)
    ap.add_argument("--tags", default=None, help="逗号分隔")


def _meta(args) -> dict:
    meta = {k: getattr(args, k) for k in ("title", "mood", "genre", "instrumentation", "description", "prompt")}
    meta["tempo_bpm"], meta["intensity"] = args.tempo_bpm, args.intensity
    meta["tags"] = [x.strip() for x in args.tags.split(",") if x.strip()] if args.tags is not None else None
    return {k: v for k, v in meta.items() if v is not None}


def _row(t: dict) -> str:
    eps = sorted({u.get("ep") for u in t.get("used_in") or [] if u.get("ep")})
    bits = [t["track_id"], f"{t.get('duration_s') or '?'}s", t.get("mood") or "-", t.get("genre") or "-",
            f"{t['tempo_bpm']:g}bpm" if t.get("tempo_bpm") else "-", t.get("instrumentation") or "-",
            "来源 " + str((t.get("origin") or {}).get("ep") or t.get("source") or "-"),
            "用于 " + (",".join(eps) if eps else "无")]
    if t.get("status") == "superseded":
        bits.append("superseded")
    return " | ".join(bits)


def do_list(proj: Path, args, searching: bool) -> int:
    idx = ml.load_index(proj)
    if searching:
        rows = ml.search(idx, args.query, args.mood or "", args.min_duration, args.max_duration, include_superseded=args.all)
    else:
        rows = ml.search(idx, include_superseded=args.all)
    if args.json:
        print(json.dumps({"library": ml.LIB_REL, "total": len(idx["tracks"]), "tracks": rows}, ensure_ascii=False, indent=1))
        return 0
    if not idx["tracks"]:
        _log("INFO", "音乐库为空(本项目还没有入库的曲目)")
        return 0
    for t in rows:
        _log("TRK ", _row(t) + (f" | score {t['score']}" if searching and (args.query or args.mood) else ""))
        if t.get("prompt"):
            print("       prompt: " + str(t["prompt"])[:200])
    _log("INFO", f"{len(rows)}/{len(idx['tracks'])} 首;文件在 {ml.LIB_REL}/<file>,可直接试听 / ffprobe")
    return 0


def do_use(proj: Path, ep: str, args) -> int:
    idx = ml.load_index(proj)
    track = ml.find_track(idx, args.track)
    if track is None:
        _log("FAIL", f"音乐库里没有 {args.track or '(未给 --track)'}")
        return 1
    src = ml.track_file(proj, track)
    if not src.is_file():
        _log("FAIL", f"库内文件缺失:{ml.LIB_REL}/{track.get('file')}")
        return 1
    name = Path(args.output).name if args.output else track["file"]
    if Path(name).suffix.lower() != src.suffix.lower():
        name = Path(name).stem + src.suffix.lower()      # 只复制不转码,扩展名跟库内文件
    dst = proj / ml.BGM_REL / ep / name
    if dst.is_file() and ml.sha256_file(dst) != track.get("sha256"):
        _log("FAIL", f"{ml.BGM_REL}/{ep}/{name} 已存在且内容不同,换一个 --output 文件名")
        return 1
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.is_file():
        shutil.copy2(src, dst)
    out = {"track_id": track["track_id"], "file": name, "path": f"{ml.BGM_REL}/{ep}/{name}",
           "duration_s": track.get("duration_s"), "license": ml.license_block(track)}
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        _log("OK  ", f"{track['track_id']} → {out['path']}({track.get('duration_s') or '?'}s)")
        print("cue 的 file 写 " + name + ",license 段照抄:")
        print(json.dumps(out["license"], ensure_ascii=False, indent=1))
    return 0


def do_sync(proj: Path, args, ep_given: bool) -> int:
    idx = ml.load_index(proj)
    eps = [args.ep] if ep_given else None
    reps = ml.sync_all(proj, idx, eps, write=args.write, backfill=args.backfill)
    bad = any(r.get("error") or r["missing_files"] or r["unknown_tracks"] for r in reps)
    if args.write and reps:
        ml.save_index(proj, idx)
    if args.json:
        print(json.dumps({"written": bool(args.write), "episodes": reps, "total_tracks": len(idx["tracks"])}, ensure_ascii=False, indent=1))
        return 1 if bad else 0
    if not reps:
        _log("INFO", "没有带 cue sheet 的集")
    for r in reps:
        if r.get("error"):
            _log("FAIL", f"{r['ep']}: 没有 cue sheet({ml.BGM_REL}/{r['ep']}/cue_sheet.json)")
            continue
        _log("SYNC", f"{r['ep']}: 新入库 {len(r['added'])} 首,复用库内 {len(r['reused'])} 处,登记使用 {r['usage']} 条"
             + (f",标 superseded {len(r['superseded'])} 首" if r["superseded"] else ""))
        for a in r["added"]:
            _log("ADD ", f"{a['track_id']} ← {a['file']}")
        for f in r["missing_files"]:
            _log("FAIL", f"cue 引用的文件不存在:{f}")
        for u in r["unknown_tracks"]:
            _log("FAIL", f"{u['cue_id']} 的 license.track_id={u['track_id']} 在库里查不到")
        for d in r["derived"]:
            _log("WARN", f"{d['cue_id']} 的文件与库内 {d['track_id']} 不是同一份(派生文件)")
    if not args.write:
        _log("INFO", "预演,未落盘;加 --write 写入 " + ml.LIB_REL + "/index.json")
    return 1 if bad else 0


def do_annotate(proj: Path, args) -> int:
    idx = ml.load_index(proj)
    track = ml.find_track(idx, args.track)
    if track is None:
        _log("FAIL", f"音乐库里没有 {args.track or '(未给 --track)'}")
        return 1
    changed = ml.annotate(track, _meta(args))
    if changed:
        ml.save_index(proj, idx)
    _log("OK  ", f"{track['track_id']} 改了 {', '.join(changed) if changed else '0 项'}")
    return 0


def do_add(proj: Path, args, ep_given: bool) -> int:
    if not args.file:
        _log("FAIL", "须给 --file")
        return 1
    src = Path(args.file).expanduser()
    if not src.is_absolute():
        src = proj / src
    if not src.is_file():
        _log("FAIL", f"文件不存在:{src}")
        return 1
    idx = ml.load_index(proj)
    origin = {"ep": args.ep} if ep_given else {}
    try:
        origin["file"] = str(src.resolve().relative_to(proj.resolve()))
    except ValueError:
        origin["file"] = src.name
    is_user = origin["file"].startswith("refs/")
    track, created = ml.add_track(proj, idx, src, meta=_meta(args), origin=origin,
                                  source="user_provided" if is_user else "generated",
                                  license_={"source": "user_provided", "origin": origin["file"]} if is_user else {})
    if created:
        ml.save_index(proj, idx)
    _log("OK  ", f"{track['track_id']} {'入库' if created else '已在库(内容相同)'}:{ml.LIB_REL}/{track['file']}")
    return 0


def do_check(proj: Path, ep: str, as_json: bool) -> int:
    res = ml.check_episode(proj, ep)
    if as_json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        for i in res["items"]:
            _log(i["status"].ljust(4), i["name"] + (":" + i["detail"] if i["detail"] else ""))
        _log(res["status"].ljust(4), f"{ml.CHECK_NAME} {ep}")
    return 1 if res["status"] == "FAIL" else 0


def main() -> int:
    args, proj = parse_args("项目音乐库:入库 / 选曲 / 对账 / 机检", configure=_configure)
    ep_given = any(a == "--ep" or a.startswith("--ep=") for a in sys.argv[1:])
    if args.cmd in ("use", "check") and not ep_given:
        _log("FAIL", f"{args.cmd} 须给 --ep")
        return 1
    if args.cmd == "list":
        return do_list(proj, args, False)
    if args.cmd == "search":
        return do_list(proj, args, True)
    if args.cmd == "use":
        return do_use(proj, args.ep, args)
    if args.cmd == "sync":
        return do_sync(proj, args, ep_given)
    if args.cmd == "annotate":
        return do_annotate(proj, args)
    if args.cmd == "add":
        return do_add(proj, args, ep_given)
    return do_check(proj, args.ep, args.json)


if __name__ == "__main__":
    sys.exit(main())
