#!/usr/bin/env python3
"""check_footage.py — mashup 混剪流程机检(footage_sync)。

音频侧与 audio-to-video 同一套物理事实(母带即成片声轨,零重编码),原语直接
复用 modules/avsync.py;视频侧是 mashup 专有规则:现成素材可切任意时长,
故**无整秒约束**、切片**零公差**(帧数恒 == round(duration*fps),不是 ±1s)。

时间结构两层(v2,2026-08-06 节奏改版):
  拍(beat)= transcript-aligner 按句切的语义时间事实,mashup/beat_track.json;
  镜(shot)= shot-designer 在拍内细分的画面单元,directing/{ep}/shot_list.json
            逐镜带**绝对** t_in/t_out,恰好铺满所属拍——直给拍(literal)1 镜,
            渲染拍(render)多镜快切,节奏来源于此。
  镜长无上限(拍长即上限)、硬下限 0.5s;1 镜 = 1 组不变。零漂移不依赖等长:
  帧数按累计取整 round(t_out×fps)−round(t_in×fps),镜铺满 [0,total] 则全片
  帧数恒 == round(total×fps),数学上无漂移。

检查项(按阶段;输入不存在的阶段记 SKIP 不算失败,--require 强制):
  [timeline] 1 audio_map_valid        mashup/audio_map.json 结构与母带存在
             2 master_untouched       assets/audio/master/{ep}.mp3 sha256 与登记一致
             3 beats_monotonic        拍单调、无缝隙(t_in[i+1]==t_out[i])
             4 beats_cover_master     首拍从 0 起,末拍止于母带实测时长(±1 帧)
             5 beat_span_in_range     每拍时长 ∈[--min-span,--max-span](缺省 1.0–20.0s;
                                      拍=句,节奏由镜承担,禁止为凑节奏改拍)
             6 shotlist_match         shot_list 与 beat_track 对齐:每镜绝对 t_in/t_out、
                                      全片无缝覆盖、嵌套于单一拍内、镜长 ≥0.5s、
                                      duration_s 一致、1 镜=1 组、
                                      每拍有 beat_design 条目(mode∈literal|render,focus 非空)
             7 ascii_filename         mashup/ 与 assets/clips/{ep}/ 文件名 ASCII
             8 beat_fingerprint       盖章校验(audio_map 改版即过期,须重对齐)
  [picks]    9 picks_complete         每组有选片:rough 区间 ≥ 镜长+0.5s(推荐 +2s)
            10 sources_registered     每个选片源在 mashup/sources.json 有完整登记
                                      (provider/id/url/title/uploader/license;
                                      license 拿不到写 "unknown"——版权责任由用户
                                      MH1 签字自担,本检查只验登记完整,不验许可类型)
  [clips]   11 clip_frames_exact      每组 assets/clips/{ep}/{grp}.mp4 帧数
                                      == round(t_out×fps)−round(t_in×fps),零公差
            12 clip_silent            组片段不得含音轨(声轨唯一来源是母带)
            13 clip_spec_normalized   宽/高/fps 与 beat_track 头部一致,SAR=1
  [final]   14 final_duration_match   edit/{ep}/final.mp4 视频时长 == 母带(±1 帧)
            15 final_audio_no_transcode  母带逐帧 md5 原样(允许容器重写末帧;
                                      codec 变/帧数变/大量帧载荷不同即 FAIL)

picks/clips 两段自 v2 起按 shot_list 的镜(=组)遍历,不再按拍——渲染拍一拍多组,
shot_list 未就位时这两段无从核对(--require 时 FAIL,否则 SKIP)。

用法:
  python3 code/check_footage.py --project <slug> --ep ep01            # 自动判阶段
  python3 code/check_footage.py --project <slug> --ep ep01 --require final
  python3 code/check_footage.py --project <slug> --ep ep01 --stamp --task-id <id>

--stamp 在 timeline 段 1–5、7 全过后,把 audio_map 指纹写入 beat_track 的
footage_sync 块(拒绝在内容检查未过时盖章;第 6 项 shot_list 属下游产物,
盖章时未就位记 SKIP 不拦);--require {timeline,picks,clips,final}
强制该阶段及其前置输入就位,缺失即 FAIL。
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from _common import parse_args  # noqa: F401  (副作用:modules/ 入 sys.path)
import avsync
import footage

STAGES = ["timeline", "picks", "clips", "final"]
DEFAULT_MIN_SPAN, DEFAULT_MAX_SPAN = 1.0, 20.0   # 拍(语义句)时长界
MIN_SHOT_S = 0.5                                  # 镜硬下限(12 帧@24fps,再短即闪帧)
BEAT_MODES = ("literal", "render")


def _configure(ap):
    ap.add_argument("--require", choices=STAGES, default=None,
                    help="强制要求该阶段及前置阶段输入就位,缺失即 FAIL")
    ap.add_argument("--stamp", action="store_true",
                    help="timeline 内容检查全过后把 audio_map 指纹写入 beat_track")
    ap.add_argument("--task-id", default=None, help="--stamp 必填,盖章留痕")
    ap.add_argument("--min-span", type=float, default=DEFAULT_MIN_SPAN)
    ap.add_argument("--max-span", type=float, default=DEFAULT_MAX_SPAN)
    ap.add_argument("--audio-map", default=None)
    ap.add_argument("--beat-track", default=None)
    ap.add_argument("--shot-list", default=None)


def audio_map_fingerprint(am: dict) -> str:
    """只取决定时间轴的字段(与 check_av_sync 同口径),追加盖章块不影响指纹。"""
    return avsync.canonical_sha256({
        "master_sha256": am.get("master_sha256"),
        "master_duration_s": am.get("master_duration_s"),
        "silences": am.get("silences"),
    })


def _bid(b: dict) -> str:
    """拍 ID;兼容 v1 beat_track(键名 shot_id)。"""
    return b.get("beat_id") or b.get("shot_id") or "?"


def main() -> None:
    args, proj = parse_args(__doc__.splitlines()[0], configure=_configure)
    ep = args.ep
    am_path = Path(args.audio_map) if args.audio_map else proj / "mashup" / "audio_map.json"
    bt_path = Path(args.beat_track) if args.beat_track else proj / "mashup" / "beat_track.json"
    sl_path = Path(args.shot_list) if args.shot_list else proj / "directing" / ep / "shot_list.json"
    picks_path = proj / "mashup" / "picks.json"
    sources_path = proj / "mashup" / "sources.json"
    clips_dir = proj / "assets" / "clips" / ep
    final_path = proj / "edit" / ep / "final.mp4"

    checks: list[tuple[str, bool]] = []

    def check(name: str, ok: bool, detail: str = "") -> bool:
        checks.append((name, bool(ok)))
        print(f"[CHECK] {name}: {'PASS' if ok else 'FAIL'}  {detail}".rstrip())
        return bool(ok)

    def skip(name: str, why: str) -> None:
        print(f"[SKIP ] {name}: {why}")

    def need(stage: str) -> bool:
        return bool(args.require) and STAGES.index(stage) <= STAGES.index(args.require)

    def fail_exit(msg: str) -> None:
        print(f"[RESULT] footage_sync: FAIL({msg})")
        sys.exit(1)

    # ---------- timeline ----------
    if not am_path.exists() or not bt_path.exists():
        if need("timeline") or args.stamp:
            fail_exit(f"timeline 输入不存在:{am_path if not am_path.exists() else bt_path}")
        skip("timeline", f"{am_path.name}/{bt_path.name} 未就位")
        print("[RESULT] footage_sync: PASS(无可检输入)")
        return

    am = json.loads(am_path.read_text())
    bt = json.loads(bt_path.read_text())
    beats = bt.get("beats") or bt.get("shots") or []   # v1 兼容读旧键名
    fps = int(bt.get("fps") or footage.DEFAULT_FPS)
    width, height = int(bt.get("width") or 0), int(bt.get("height") or 0)
    frame_s = 1.0 / fps

    check("audio_map_valid",
          bool(am.get("master") and am.get("master_sha256")
               and isinstance(am.get("master_duration_s"), (int, float))
               and isinstance(am.get("silences"), list)),
          "须含 master/master_sha256/master_duration_s/silences")
    total = float(am.get("master_duration_s") or 0)

    master = proj / "assets" / "audio" / "master" / f"{ep}{Path(str(am.get('master') or '')).suffix or '.mp3'}"
    if not master.exists():
        cand = sorted((proj / "assets" / "audio" / "master").glob(f"{ep}.*")) \
            if (proj / "assets" / "audio" / "master").is_dir() else []
        master = cand[0] if cand else master
    if master.exists():
        check("master_untouched", avsync.file_sha256(str(master)) == am.get("master_sha256"),
              f"{master.name} sha256 与 audio_map 登记比对")
    else:
        check("master_untouched", False, f"母带不存在:assets/audio/master/{ep}.*")

    mono = bool(beats) and all(
        b.get("t_out", 0) > b.get("t_in", -1) for b in beats) and all(
        abs(beats[i + 1]["t_in"] - beats[i]["t_out"]) < 1e-6 for i in range(len(beats) - 1))
    check("beats_monotonic", mono, f"{len(beats)} 拍,单调无缝隙")
    if beats:
        check("beats_cover_master",
              abs(beats[0]["t_in"]) < 1e-6 and abs(beats[-1]["t_out"] - total) <= frame_s,
              f"[0, {total}] vs [{beats[0]['t_in']}, {beats[-1]['t_out']}](公差 1 帧)")
        bad = [_bid(b) for b in beats
               if not (args.min_span - 1e-6 <= b["t_out"] - b["t_in"] <= args.max_span + 1e-6)]
        check("beat_span_in_range", not bad,
              f"拍长 ∈[{args.min_span},{args.max_span}]s" + (f";超界:{bad[:5]}" if bad else ""))
    else:
        check("beats_cover_master", False, "beat_track 拍列表为空")
        check("beat_span_in_range", False, "beat_track 拍列表为空")

    # 镜行(sid/gid/t_in/t_out):shotlist_match 产出,picks/clips 段沿用
    shot_rows: list[dict] | None = None
    if sl_path.exists():
        sl = json.loads(sl_path.read_text())
        sl_shots = sl.get("shots") or []
        groups = {g.get("group_id"): g for g in sl.get("generation_groups") or []}
        design = {d.get("beat_id"): d for d in sl.get("beat_design") or []}
        gid_of = {g["shots"][0]: gid for gid, g in groups.items()
                  if isinstance(g.get("shots"), list) and len(g.get("shots")) == 1}
        problems = []

        rows = []
        for s in sl_shots:
            sid = s.get("shot_id")
            if not isinstance(s.get("t_in"), (int, float)) or not isinstance(s.get("t_out"), (int, float)):
                problems.append(f"{sid} 缺绝对 t_in/t_out(v2 必填)")
                continue
            rows.append(s)
        rows.sort(key=lambda x: x["t_in"])

        for i, s in enumerate(rows):
            sid = s.get("shot_id")
            span = s["t_out"] - s["t_in"]
            if span < MIN_SHOT_S - 1e-6:
                problems.append(f"{sid} 镜长 {span:.2f}s < {MIN_SHOT_S}s 硬下限")
            if abs(float(s.get("duration_s") or 0) - span) > 0.005:
                problems.append(f"{sid} duration_s 与 t_out-t_in 不符")
            if i and abs(s["t_in"] - rows[i - 1]["t_out"]) > 1e-6:
                problems.append(f"{sid} 与前镜有缝隙/重叠")
            host = [b for b in beats
                    if b["t_in"] - 1e-6 <= s["t_in"] and s["t_out"] <= b["t_out"] + 1e-6]
            if not host:
                problems.append(f"{sid} 跨拍(镜必须嵌套在单一拍内)")
            elif s.get("beat_id") and s["beat_id"] != _bid(host[0]):
                problems.append(f"{sid} beat_id 标注与实际所属拍不符")
            if sid not in gid_of:
                problems.append(f"{sid} 无 1:1 对应组(1 镜=1 组)")
        if rows and (abs(rows[0]["t_in"]) > 1e-6 or abs(rows[-1]["t_out"] - total) > frame_s):
            problems.append(f"镜未精确覆盖 [0, {total}](公差 1 帧)")
        if len(gid_of) != len(groups):
            problems.append("存在多镜组或空组(1 镜=1 组)")
        for b in beats:
            d = design.get(_bid(b))
            if not d:
                problems.append(f"{_bid(b)} 缺 beat_design 条目")
            elif d.get("mode") not in BEAT_MODES or not str(d.get("focus") or "").strip():
                problems.append(f"{_bid(b)} beat_design mode/focus 非法")
        check("shotlist_match", not problems and bool(rows),
              f"{len(rows)} 镜/{len(beats)} 拍,镜嵌套铺满"
              + (f";{problems[:5]}" if problems else ""))
        if not problems and rows:
            shot_rows = [{"sid": s["shot_id"], "gid": gid_of[s["shot_id"]],
                          "t_in": s["t_in"], "t_out": s["t_out"]} for s in rows]
    elif need("timeline") and not args.stamp:
        check("shotlist_match", False, f"shot_list 不存在:{sl_path}")
    else:
        skip("shotlist_match", "shot_list.json 未就位(盖章阶段属正常)")

    bad_names = [p.name for base in (proj / "mashup", clips_dir) if base.is_dir()
                 for p in base.rglob("*") if not p.name.isascii()]
    check("ascii_filename", not bad_names, f"非 ASCII:{bad_names[:5]}" if bad_names else "")

    content_ok = all(ok for _, ok in checks)

    if args.stamp:
        if not args.task_id:
            sys.exit("错误:--stamp 必须带 --task-id(盖章留痕)")
        if not content_ok:
            fail_exit("时间轴内容检查未过,拒绝盖章")
        bt["footage_sync"] = {
            "audio_map": os.path.relpath(am_path, proj),
            "audio_map_sha256": audio_map_fingerprint(am),
            "master_sha256": am.get("master_sha256"),
            "master_duration_s": total,
            "beat_count": len(beats),
            "fps": fps, "width": width, "height": height,
            "stamped_by_task": args.task_id,
            "stamped_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        }
        tmp = bt_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(bt, ensure_ascii=False, indent=1) + "\n")
        os.replace(tmp, bt_path)
        print(f"[STAMP] footage_sync 已写入 {bt_path}(记得 vc register 登记新版本)")

    recorded = (bt.get("footage_sync") or {}).get("audio_map_sha256")
    if recorded is None and not args.stamp:
        check("beat_fingerprint", False,
              "未盖章:先跑 --stamp --task-id <id>;旧产物一律视为过期")
    else:
        fp = audio_map_fingerprint(am)
        check("beat_fingerprint", (recorded or fp) == fp,
              "audio_map 已改版,须回派 transcript-aligner 重对齐" if recorded not in (None, fp) else "")

    # ---------- picks ----------
    if picks_path.exists() and shot_rows is None:
        # 选片按镜(=组)核对;shot_list 未过/未就位则无从展开
        if need("picks"):
            check("picks_complete", False, "shot_list 未就位或未过第 6 项,无从按镜核对")
            check("sources_registered", False, "无从核对")
        else:
            skip("picks", "shot_list 未就位,picks 按镜核对挂起")
    elif picks_path.exists():
        picks = json.loads(picks_path.read_text()).get("picks") or {}
        sources = {(s.get("provider"), str(s.get("id"))): s
                   for s in (json.loads(sources_path.read_text()).get("sources") or [])} \
            if sources_path.exists() else {}
        missing, thin, unreg = [], [], []
        for r in shot_rows:
            gid = r["gid"]
            p = picks.get(gid)
            span = r["t_out"] - r["t_in"]
            if not p or not all(k in p for k in ("provider", "id", "rough_in", "rough_out")):
                missing.append(gid)
                continue
            if float(p["rough_out"]) - float(p["rough_in"]) < span + 0.5 - 1e-6:
                thin.append(gid)
            src = sources.get((p["provider"], str(p["id"])))
            if not src or not all(src.get(k) for k in ("url", "title", "license")) \
                    or gid not in (src.get("used_by") or []):
                unreg.append(gid)
        check("picks_complete", not missing and not thin,
              (f"缺选片:{missing[:5]};" if missing else "")
              + (f"rough 区间不足镜长+0.5s:{thin[:5]}" if thin else "全部就位"))
        check("sources_registered", not unreg,
              f"台账缺失/不全:{unreg[:5]}" if unreg else f"{len(sources)} 个源已登记")
    elif need("picks"):
        check("picks_complete", False, f"picks.json 不存在:{picks_path}")
        check("sources_registered", False, "无从核对")
    else:
        skip("picks", "mashup/picks.json 未就位")

    # ---------- clips ----------
    clips = sorted(clips_dir.glob("grp*.mp4")) if clips_dir.is_dir() else []
    if (clips or need("clips")) and shot_rows is None:
        if need("clips"):
            check("clip_frames_exact", False, "shot_list 未就位或未过第 6 项,无从按镜核对")
            check("clip_silent", False, "无从核对")
            check("clip_spec_normalized", False, "无从核对")
        else:
            skip("clips", "shot_list 未就位,clips 按镜核对挂起")
    elif clips or need("clips"):
        by_gid = {p.stem: p for p in clips}
        bad_frames, with_audio, bad_spec, absent = [], [], [], []
        for r in shot_rows:
            gid = r["gid"]
            p = by_gid.get(gid)
            if not p:
                absent.append(gid)
                continue
            # 累计取整口径(footage.frames_for_beat):逐镜 round(dur*fps) 会累积舍入,
            # 累计取整使全片帧数恒 == round(total*fps);镜长不齐不影响该数学
            want = footage.frames_for_beat(r["t_in"], r["t_out"], fps)
            info = footage.probe_video(str(p))
            if footage.count_frames(str(p)) != want:
                bad_frames.append(gid)
            if info["has_audio"]:
                with_audio.append(gid)
            if (width and info["width"] != width) or (height and info["height"] != height) \
                    or (info["fps"] and abs(info["fps"] - fps) > 0.01) \
                    or (info["sar"] not in (None, "1:1", "0:1", "N/A")):
                bad_spec.append(gid)
        check("clip_frames_exact", not absent and not bad_frames,
              (f"缺片:{absent[:5]};" if absent else "")
              + (f"帧数不符:{bad_frames[:5]}" if bad_frames
                 else f"{len(shot_rows) - len(absent)} 组零公差"))
        check("clip_silent", not with_audio,
              f"含音轨:{with_audio[:5]}" if with_audio else "")
        check("clip_spec_normalized", not bad_spec,
              f"规格不符:{bad_spec[:5]}" if bad_spec else f"{width}x{height}@{fps} SAR=1")
    else:
        skip("clips", f"assets/clips/{ep}/ 无 grp*.mp4")

    # ---------- final ----------
    if final_path.exists():
        vd = footage.probe_video(str(final_path))["duration_s"]
        check("final_duration_match", abs(vd - total) <= frame_s,
              f"final {vd:.3f}s vs 母带 {total:.3f}s(公差 1 帧)")
        if master.exists():
            cmp = avsync.compare_audio_frames(str(master), str(final_path))
            check("final_audio_no_transcode", bool(cmp.get("ok")),
                  "母带逐帧 md5 原样(允许容器重写末帧)" if cmp.get("ok")
                  else f"帧数 {cmp.get('master_frames')}→{cmp.get('muxed_frames')},"
                       f"载荷不同 {cmp.get('diff_frame_count')} 帧;"
                       f"疑似重编码或 -shortest,回退 edit 重封装(-c:a copy,禁 -shortest)")
        else:
            check("final_audio_no_transcode", False, "母带不存在,无从比对")
    elif need("final"):
        check("final_duration_match", False, f"final 不存在:{final_path}")
        check("final_audio_no_transcode", False, "无从比对")
    else:
        skip("final", f"edit/{ep}/final.mp4 未就位")

    all_ok = all(ok for _, ok in checks)
    print(f"[RESULT] footage_sync: {'PASS' if all_ok else 'FAIL'}")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
