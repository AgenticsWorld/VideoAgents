#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""music_library.py — 项目音乐库宿主 CLI + 机检 music_library_synced(WORKFLOW.md §8 music,2026-09-27)。

每集新增的 BGM 入库(`assets/audio/library/music/`:index.json + MUS-NNNN.<ext>)并标记数据;之后的剧集配乐
**先查库,主题曲按配额原样复用、其余走主题变奏或新生成**(2026-10-07)。「谁在用」由各集 cue sheet 推导,sync 整集重算、重跑幂等。

两层库:`theme_id` 非空的曲目 = 季级主题曲(登记在主题表 `themes[]`,可跨集原样复用);空 = 一次性 cue(只出本集)。
每个配乐点三选一:原样复用(use)/ 主题变奏(variant 看基曲 → genmedia 按需时长新出,cue 写 variant_of)/ 全新生成。
复用配额(check 硬拦):原样复用 ≤ 40% 配乐点(至少放行 1 条)、新生成 + 变奏 ≥ 30%、同曲同集 ≤ 1 次、一曲 ≤ 3 集、
只复用主题曲、复用 cue 时长 ≥ 曲目 60%、情绪强度相差 ≤ 0.25、复用 / 变奏 / 新生成都要写 reason。

用法(p8-music 的标准顺序:list/search → theme → use / variant + genmedia music → 写 cue sheet → sync --write → check):
  python3 code/music_library.py list   --project <slug> [--all] [--json]            # 库内曲目 + 标记数据 + 主题 + 使用集
  python3 code/music_library.py search --project <slug> [--query "弦乐 悬疑"] [--mood 紧张] [--theme T-hero]
                                       [--min-duration 20] [--max-duration 90] [--json]   # 关键词打分排序(不设 = 全列)
  python3 code/music_library.py theme  --project <slug> [--json]                     # 主题表 + 各主题的曲目 / 变奏 / 复用集
  python3 code/music_library.py theme  --project <slug> --add T-hero --name "哪吒主题" --for "主角 / 成长"
                                       --motif "<英文动机描述:乐器 / 乐句 / 调性>" [--mood ..] [--ep epNN]   # 登记 / 更新主题
  python3 code/music_library.py use    --project <slug> --ep epNN --track MUS-0003 [--output epNN_bgm_02.mp3]
                                       [--need-duration 12]                        # 复制库内曲目到本集 bgm 目录,打印 cue 的 license 段;
                                                                                     # 非主题曲 / 已复用满 3 集 / 配乐点不足曲目 60% 直接拒绝(退出码 2)
  python3 code/music_library.py variant --project <slug> --track MUS-0003 [--json]  # 看基曲的动机 / 提示词 / 已有变奏,
                                                                                     # 打印变奏 cue 要写的字段(variant_of / theme_id)
  python3 code/music_library.py sync   --project <slug> [--ep epNN] [--write] [--backfill] [--json]
                                                                                     # 按 cue sheet 对账:新曲入库、used_in 重算(缺省只预演)
  python3 code/music_library.py annotate --project <slug> --track MUS-0003 [--title ..] [--mood ..] [--genre ..]
                                       [--tempo-bpm 96] [--instrumentation ..] [--tags a,b] [--description ..]
                                       [--theme T-hero | --theme none] [--variant-of MUS-0001]   # 挂主题 / 记变奏来源
  python3 code/music_library.py add    --project <slug> --file <项目相对或绝对路径> [--ep epNN] [标记参数同 annotate]
                                                                                     # 手工入库一首(不经 cue sheet)
  python3 code/music_library.py check  --project <slug> --ep epNN [--json]           # 机检 music_library_synced

sync 不带 --ep = 全部有 cue sheet 的集;--backfill = 存量回补(音乐库上线前已交付的集:首次对账标 backfilled,
  机检对缺 music_library_report 只 WARN,cue sheet 之后被重写过即恢复 FAIL;p8-music 交付时不要带 --backfill)。
退出码:list/search/theme/variant/use/sync/annotate/add 正常=0,出错=1(sync 有 cue 文件缺失 / track_id 查不到也=1;
  use 被复用配额拒绝=2);check PASS/WARN=0,FAIL=1。
"""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args  # noqa: E402  副作用:modules/ 与仓库根入 sys.path

from modules import music_library as ml  # noqa: E402  本脚本与模块同名,须走 modules. 前缀

CMDS = ("list", "search", "theme", "variant", "use", "sync", "annotate", "add", "check")


def _log(tag: str, msg: str) -> None:
    print(f"[{tag}] {msg}", flush=True)


def _configure(ap):
    ap.add_argument("cmd", choices=CMDS)
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--all", action="store_true", help="list/search:连被替换掉的旧曲(superseded)一起列")
    ap.add_argument("--query", default="", help="search:关键词(空格 / 逗号分隔)")
    ap.add_argument("--min-duration", type=float, default=None)
    ap.add_argument("--max-duration", type=float, default=None)
    ap.add_argument("--track", default="", help="use/variant/annotate:曲目 ID(MUS-NNNN)")
    ap.add_argument("--output", default="", help="use:本集 bgm 目录下的文件名,缺省沿用库内文件名")
    ap.add_argument("--need-duration", type=float, default=None, help="use:配乐点要铺的秒数,不足曲目 60% 即拒绝(该走变奏)")
    ap.add_argument("--theme", default=None, help="search:只列该主题的曲目;annotate:挂主题(none = 摘掉)")
    ap.add_argument("--variant-of", default=None, help="annotate:记本曲是哪首的变奏")
    ap.add_argument("--add", default="", help="theme:登记 / 更新主题 ID(形如 T-hero)")
    ap.add_argument("--name", default=None, help="theme:主题名")
    ap.add_argument("--for", dest="for_", default=None, help="theme:代表谁 / 什么(角色 / 场所 / 情绪)")
    ap.add_argument("--motif", default=None, help="theme:核心动机的英文描述(乐器 / 乐句 / 调性),变奏提示词从这里起")
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
    if args.theme is not None:
        meta["theme_id"] = "" if str(args.theme).strip().lower() in ("", "none", "-") else str(args.theme).strip().upper()
    if args.variant_of is not None:
        meta["variant_of"] = "" if str(args.variant_of).strip().lower() in ("", "none", "-") else str(args.variant_of).strip().upper()
    return {k: v for k, v in meta.items() if v is not None}


def _row(t: dict) -> str:
    eps = sorted({u.get("ep") for u in t.get("used_in") or [] if u.get("ep")})
    re_eps = ml.reuse_episodes(t)
    bits = [t["track_id"], f"{t.get('duration_s') or '?'}s", t.get("mood") or "-", t.get("genre") or "-",
            f"{t['tempo_bpm']:g}bpm" if t.get("tempo_bpm") else "-", t.get("instrumentation") or "-",
            ("主题 " + t["theme_id"]) if t.get("theme_id") else "一次性",
            "来源 " + str((t.get("origin") or {}).get("ep") or t.get("source") or "-"),
            "用于 " + (",".join(eps) if eps else "无"),
            f"原样复用 {len(re_eps)}/{ml.TRACK_REUSE_EPS_MAX} 集"]
    if t.get("variant_of"):
        bits.append("变奏自 " + t["variant_of"])
    if t.get("status") == "superseded":
        bits.append("superseded")
    return " | ".join(bits)


def _theme_rows(idx: dict) -> list[dict]:
    rows = []
    for th in idx.get("themes") or []:
        trks = ml.theme_tracks(idx, th["theme_id"])
        rows.append({**th, "tracks": [t["track_id"] for t in trks if not t.get("variant_of")],
                     "variants": [t["track_id"] for t in trks if t.get("variant_of")],
                     "reused_in": sorted({e for t in trks for e in ml.reuse_episodes(t)})})
    return rows


def do_theme(proj: Path, args, ep_given: bool) -> int:
    idx = ml.load_index(proj)
    if args.add:
        meta = {"name": args.name, "for": args.for_, "motif": args.motif, "mood": args.mood}
        try:
            th, created = ml.add_theme(idx, args.add, meta, created_ep=args.ep if ep_given else "")
        except ValueError as e:
            _log("FAIL", str(e))
            return 1
        ml.save_index(proj, idx)
        _log("OK  ", f"{th['theme_id']} {'登记' if created else '更新'}:{th.get('name') or '-'} / {th.get('for') or '-'}"
             + (f"\n       motif: {th['motif']}" if th.get("motif") else ""))
        if not th.get("motif"):
            _log("WARN", "主题没写 --motif(核心动机的英文描述),后面出变奏没有起点;请补上")
        _log("INFO", f"把曲目挂到主题:python3 code/music_library.py annotate --track MUS-NNNN --theme {th['theme_id']}")
        return 0
    rows = _theme_rows(idx)
    if args.json:
        print(json.dumps({"themes": rows, "one_off": [t["track_id"] for t in idx["tracks"]
                                                       if not t.get("theme_id") and t.get("status") != "superseded"]},
                         ensure_ascii=False, indent=1))
        return 0
    if not rows:
        _log("INFO", "主题表为空:还没登记任何主题动机。库里的曲目全是一次性 cue,不可跨集原样复用;"
             "确是贯穿全季的动机就用 theme --add 登记,再 annotate --theme 挂上曲目")
    for r in rows:
        _log("THM ", f"{r['theme_id']} | {r.get('name') or '-'} | {r.get('for') or '-'} | 曲目 {','.join(r['tracks']) or '无'}"
             f" | 变奏 {','.join(r['variants']) or '无'} | 原样复用于 {','.join(r['reused_in']) or '无'}")
        if r.get("motif"):
            print("       motif: " + str(r["motif"])[:240])
    one_off = [t["track_id"] for t in idx["tracks"] if not t.get("theme_id") and t.get("status") != "superseded"]
    _log("INFO", f"{len(rows)} 个主题;一次性曲目 {len(one_off)} 首" + (":" + ",".join(one_off) if one_off else ""))
    return 0


def do_variant(proj: Path, args) -> int:
    idx = ml.load_index(proj)
    track = ml.find_track(idx, args.track)
    if track is None:
        _log("FAIL", f"音乐库里没有 {args.track or '(未给 --track)'}")
        return 1
    th = ml.find_theme(idx, track.get("theme_id")) if track.get("theme_id") else None
    sibs = [t["track_id"] for t in idx["tracks"] if t.get("variant_of") == track["track_id"] and t.get("status") != "superseded"]
    out = {"track_id": track["track_id"], "theme_id": track.get("theme_id") or "", "theme": th,
           "base_prompt": track.get("prompt") or "", "mood": track.get("mood") or "", "genre": track.get("genre") or "",
           "tempo_bpm": track.get("tempo_bpm"), "instrumentation": track.get("instrumentation") or "",
           "duration_s": track.get("duration_s"), "existing_variants": sibs,
           "cue_fields": {"variant_of": track["track_id"], "theme_id": track.get("theme_id") or ""}}
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    _log("VAR ", f"基曲 {track['track_id']}({track.get('duration_s') or '?'}s)"
         + (f" 主题 {track['theme_id']}" + (f" {th.get('name')}" if th and th.get("name") else "") if track.get("theme_id") else " 一次性 cue(变奏后仍不算主题)"))
    if th and th.get("motif"):
        print("       motif(必须保留的动机): " + th["motif"])
    if track.get("prompt"):
        print("       base prompt: " + str(track["prompt"])[:400])
    print(f"       mood {track.get('mood') or '-'} | {track.get('genre') or '-'} | "
          f"{(str(track['tempo_bpm']) + 'bpm') if track.get('tempo_bpm') else '-'} | {track.get('instrumentation') or '-'}")
    if sibs:
        print("       已有变奏: " + ", ".join(sibs) + "(别再出一样的)")
    print("写变奏提示词:保留动机句(同样的主奏乐器 / 乐句 / 调性),改节奏 / 配器 / 强度 / 段落结构,时长按配乐点 --duration 出;")
    print("cue 顶层写 " + json.dumps(out["cue_fields"], ensure_ascii=False) + ",license 照新生成写;")
    print("music_library_report.variations 写 {cue_id, variant_of, reason}(保留了什么、改了什么)。")
    return 0


def do_list(proj: Path, args, searching: bool) -> int:
    idx = ml.load_index(proj)
    if searching:
        rows = ml.search(idx, args.query, args.mood or "", args.min_duration, args.max_duration, include_superseded=args.all)
        if args.theme:
            rows = [r for r in rows if r.get("theme_id") == str(args.theme).strip().upper()]
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
    n_theme = sum(1 for t in idx["tracks"] if t.get("theme_id") and t.get("status") != "superseded")
    _log("INFO", f"{len(rows)}/{len(idx['tracks'])} 首(主题曲 {n_theme} 首,其余一次性);文件在 {ml.LIB_REL}/<file>,可直接试听 / ffprobe")
    _log("INFO", f"复用配额:原样复用 ≤ {ml.REUSE_MAX_RATIO:.0%} 配乐点、新生成 + 变奏 ≥ {ml.FRESH_MIN_RATIO:.0%}、"
         f"同曲同集 ≤ {ml.SAME_TRACK_PER_EP_MAX} 次、一曲 ≤ {ml.TRACK_REUSE_EPS_MAX} 集、只复用主题曲;主题表看 theme 子命令")
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
    # 复用配额前置拦截(check 也会再拦;这里早拦省得复制了再返工)
    if not ml.is_theme_track(track):
        _log("DENY", f"{track['track_id']} 是一次性 cue(没挂主题),不跨集原样复用;确是贯穿全季的动机就先"
             f" theme --add + annotate --track {track['track_id']} --theme T-xxx,否则走 variant 出变奏或新生成")
        return 2
    eps = ml.reuse_episodes(track)
    if ep not in eps and len(eps) >= ml.TRACK_REUSE_EPS_MAX:
        _log("DENY", f"{track['track_id']} 已在 {', '.join(eps)} 原样复用满 {ml.TRACK_REUSE_EPS_MAX} 集,本集改走 variant 出变奏")
        return 2
    d_trk = track.get("duration_s")
    if args.need_duration is not None and d_trk and args.need_duration < ml.REUSE_MIN_COVER * float(d_trk):
        _log("DENY", f"配乐点只要 {args.need_duration:g}s,不足 {track['track_id']} 全长 {float(d_trk):.1f}s 的 "
             f"{ml.REUSE_MIN_COVER:.0%};掐一小段用 = 改走 variant 按需时长出变奏")
        return 2
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
        _log("OK  ", f"{track['track_id']} → {out['path']}({track.get('duration_s') or '?'}s)"
             f",主题 {track['theme_id']},本集之前已原样复用于 {', '.join(eps) if eps else '无'}")
        print("cue 的 file 写 " + name + ",license 段照抄:")
        print(json.dumps(out["license"], ensure_ascii=False, indent=1))
        print("music_library_report.reused 写 {cue_id, track_id, reason}(为什么此处要让这个主题再现);同集同曲只准 1 次。")
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
    meta = _meta(args)
    if meta.get("theme_id") and ml.find_theme(idx, meta["theme_id"]) is None:
        _log("FAIL", f"主题 {meta['theme_id']} 不在主题表里,先 theme --add {meta['theme_id']} --name .. --motif ..")
        return 1
    if meta.get("variant_of"):
        base = ml.find_track(idx, meta["variant_of"])
        if base is None or base["track_id"] == track["track_id"]:
            _log("FAIL", f"--variant-of {meta['variant_of']} 不是库里另一首曲目")
            return 1
        if not meta.get("theme_id") and base.get("theme_id") and not track.get("theme_id"):
            meta["theme_id"] = base["theme_id"]            # 变奏继承基曲的主题
    changed = ml.annotate(track, meta)
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
    if args.cmd == "theme":
        return do_theme(proj, args, ep_given)
    if args.cmd == "variant":
        return do_variant(proj, args)
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
