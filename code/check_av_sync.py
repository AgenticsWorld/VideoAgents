#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_av_sync.py — 音画锁定同步机检(av_sync,audio-to-video 插件)。

事实源:用户提供的母带 MP3(`assets/audio/master/epNN.mp3`)与 `av/audio_map.json` 的实测时长。
被检方:`av/beat_track.json`、`directing/epNN/shot_list.json`、`edit/epNN/timeline.json`、
        `assets/clips/epNN/grpNNN.mp4`、`edit/epNN/final.mp4`。

与 §8B 的 narration_anchor_sync 方向相反:那里音频(TTS)可压缩、画面窗口固定;
这里**画面可调、音频不可动**。故本机检不检查「旁白是否塞得进窗口」,而检查
「画面是否精确铺满音频、累计有无漂移、母带有没有被动过」。

分阶段(输入不存在的阶段记 SKIP,不算失败;用 --require 强制要求某阶段就位):

  [timeline] 时间轴阶段(AVH1/AVH3 前必跑)
    1. master_untouched          母带 sha256 == audio_map 记录值
    2. audio_map_valid           audio_map 必备字段完整、实测时长 > 0
    3. beats_monotonic           beat 段单调、无缝隙、精确覆盖 [0, 总长]
    4. spans_cover_master        Σ组 span == 母带总长 ±1 帧(1/24s)
    5. span_in_range             每组 span ∈[4,14];非末组必须整数秒
    6. duration_int_consistent   total_duration_s 为整数、∈[4,15]、== int(round(Σ子镜头时长))
    7. ascii_filename            全部落盘文件名仅 ASCII(WORKFLOW.md §1 原则 9)
    8. beat_fingerprint          beat_track.audio_map_sha256 == 当前 audio_map 指纹
                                 (1–7 为盖章前置;本项在 --stamp 之后校验)

  [clips] 片段阶段(剪辑开工前必跑)
    9. clip_ge_span              每组实测片长 ≥ 该组 span(否则 out 修剪会超出片长)
   10. clip_silent               组片段不得含音轨(--generate-audio off 的反向断言)

  [final] 成片阶段(AVH5 前必跑)
   11. timeline_out_exact        timeline 每条 out-in == 对应 span
   12. no_cumulative_drift       累计视频位置 == 累计音频位置,逐组核对
   13. final_duration_match      final.mp4 时长 == 母带总长 ±0.10s
   14. final_audio_no_transcode  成片音轨 codec/采样率/声道 == 母带(任一变化即重编码)
   15. final_audio_frames_intact 逐音频帧 md5 比对:帧数相等且帧 1..N-1 逐字节相同

  ⚠️ 关于「零重编码」的准确口径(实测结论,勿写成整流比特级一致):
     MP3 装进 MP4 时容器必然重写 gapless/编码器延迟元数据,**整流 md5 一定变**,
     但每帧压缩载荷是逐字节拷贝的;末帧因容器补齐可能被重写。故本机检比对逐帧 md5,
     并允许末帧不同——这样既放过合法的 -c:a copy,又能检出:
       重编码 MP3(实测 1404/1533 帧不同)、转 AAC(codec 与帧数均变)、
       误用 -shortest(帧数少 1,音频被静默截断)。
     封装铁律:**严禁 -shortest**;音频是权威时长,视频须 ≥ 音频后由 timeline 修剪。

用法:
  python3 code/check_av_sync.py --project <slug> --ep epNN                  # 自动判阶段
  python3 code/check_av_sync.py --project <slug> --ep epNN --require final  # 成片终审
  python3 code/check_av_sync.py --project <slug> --ep epNN \
      --stamp --task-id <task_id>    # timeline 段全过后给 beat_track 盖章(timeline-planner 交付时)

调用时机(缺一即视为流程违规):
  - timeline-planner 交付前:--stamp 盖指纹,随后 vc register 登记新版本;
  - 08-video-gen 开跑前、10-editing/edit 封装前:只检模式,FAIL 即停手上报 orchestrator
    (指纹失配 = audio_map 已改版,须回派 transcript-aligner 重对齐,严禁按旧 beat 继续)。
退出码:全 PASS=0,任一 FAIL=1。
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args                                   # noqa: E402

import avsync                                                    # noqa: E402
from avsync import (FRAME_S, MIN_GROUP_S, canonical_sha256,
                    file_sha256)

STAGES = ("timeline", "clips", "final")
DUR_TOL_S = 0.10             # 成片总长容差
HARD_MAX_GROUP_S = 15        # 默认=Seedance 2.0 绝对上限(check_generation_groups 同口径);
                             # 实际以项目「分镜组设置」shot_group.max_group_s 为准(main 里读取)


def project_max_group_s(proj: Path) -> int:
    """项目「分镜组设置」的生成组时长上限(settings.json shot_group.max_group_s,
    4-30;读不到回落 HARD_MAX_GROUP_S=15 的 Seedance 2.0 口径)。"""
    try:
        st = json.loads((proj / "settings.json").read_text())
        v = int(round(float((st.get("shot_group") or {})["max_group_s"])))
        return v if 4 <= v <= 30 else HARD_MAX_GROUP_S
    except Exception:
        return HARD_MAX_GROUP_S


def audio_map_fingerprint(am: dict) -> str:
    """audio_map 的稳定指纹:只取决定时间轴的字段,不受后续追加的盖章块影响。"""
    return canonical_sha256({
        "master_sha256": am.get("master_sha256"),
        "master_duration_s": am.get("master_duration_s"),
        "silences": am.get("silences") or [],
    })


def non_ascii_names(root: Path) -> list[str]:
    bad = []
    if not root.is_dir():
        return bad
    for p in root.rglob("*"):
        if p.name != p.name.encode("ascii", "ignore").decode():
            bad.append(str(p.relative_to(root)))
    return bad


def main():
    def configure(ap):
        ap.add_argument("--stamp", action="store_true",
                        help="timeline 段全过后,把 audio_map 指纹写入 beat_track 的 av_sync 块")
        ap.add_argument("--task-id", default=None, help="--stamp 必填:盖章任务 ID(留痕)")
        ap.add_argument("--require", choices=STAGES, default=None,
                        help="强制要求该阶段(含其前置阶段)的输入就位,缺失即 FAIL")
        ap.add_argument("--audio-map", default=None, help="显式指定 av/audio_map.json 路径")
        ap.add_argument("--beat-track", default=None, help="显式指定 av/beat_track.json 路径")
        ap.add_argument("--shot-list", default=None, help="显式指定 shot_list.json 路径")

    args, proj = parse_args(__doc__, configure=configure)
    ep = args.ep
    hard_max = project_max_group_s(proj)   # 项目「分镜组设置」组时长上限(回落 15)
    span_max = hard_max - 1                # 留 1s 交付公差(与 avsync.MAX_GROUP_S=14 同理)
    checks: list[tuple[str, bool]] = []

    def check(name: str, ok: bool, detail: str = ""):
        checks.append((name, ok))
        print(f"[CHECK] {name}: {'PASS' if ok else 'FAIL'}" + (f"  {detail}" if detail else ""))

    def skip(name: str, why: str):
        print(f"[SKIP ] {name}: {why}")

    def need(stage: str) -> bool:
        """--require final 时,final 及其前置阶段都算「必须就位」。"""
        return bool(args.require) and STAGES.index(stage) <= STAGES.index(args.require)

    am_path = Path(args.audio_map) if args.audio_map else proj / "av" / "audio_map.json"
    bt_path = Path(args.beat_track) if args.beat_track else proj / "av" / "beat_track.json"
    sl_path = Path(args.shot_list) if args.shot_list else proj / "directing" / ep / "shot_list.json"

    print(f"=== av_sync 机检 ({args.project}/{ep}) ===")
    for label, p in (("audio_map", am_path), ("beat_track", bt_path), ("shot_list", sl_path)):
        if not p.is_file():
            print(f"[RESULT] av_sync: FAIL({label} 不存在:{p})")
            sys.exit(1)
        print(f"{label:11}: {p}")

    audio_map = json.loads(am_path.read_text(encoding="utf-8"))
    beat_track = json.loads(bt_path.read_text(encoding="utf-8"))
    shot_list = json.loads(sl_path.read_text(encoding="utf-8"))
    groups = shot_list.get("generation_groups") or []
    shots_by_id = {s["shot_id"]: s for s in shot_list.get("shots") or []}
    total = audio_map.get("master_duration_s")
    fp = audio_map_fingerprint(audio_map)
    print(f"母带总长     : {total}s;audio_map 指纹 {fp[:16]}…;生成组 {len(groups)} 组\n")

    # ---------------------------------------------------------------- 1/2 母带与 audio_map
    master_rel = audio_map.get("master") or f"assets/audio/master/{ep}.mp3"
    master = proj / master_rel
    if not master.is_file():
        check("master_untouched", False, f"母带不存在:{master}")
    else:
        actual = file_sha256(str(master))
        recorded = audio_map.get("master_sha256")
        check("master_untouched", actual == recorded,
              "母带与 audio_map 记录一致" if actual == recorded else
              f"母带已被改动(实测 {actual[:16]}…,记录 {str(recorded)[:16]}…)——"
              "本流程禁止对母带做任何重编码/归一化,须回退母带或重跑 audio-ingest")

    missing = [k for k in ("master", "master_sha256", "master_duration_s", "silences")
               if audio_map.get(k) is None]
    check("audio_map_valid", not missing and isinstance(total, (int, float)) and total > 0,
          f"缺字段 {missing}" if missing else f"字段完整,实测时长 {total}s")

    # ---------------------------------------------------------------- 3 beat 连续性
    segs = beat_track.get("segments") or []
    bad = []
    prev_end = 0.0
    for i, s in enumerate(segs):
        st, en = s.get("start"), s.get("end")
        if st is None or en is None:
            bad.append(f"{s.get('id', i)}(缺 start/end)")
            continue
        if abs(float(st) - prev_end) > 1e-6:
            bad.append(f"{s.get('id', i)}(start {st} ≠ 上段 end {prev_end:g})")
        if float(en) <= float(st):
            bad.append(f"{s.get('id', i)}(end ≤ start)")
        prev_end = float(en)
    if segs and total is not None and abs(prev_end - float(total)) > FRAME_S:
        bad.append(f"末段 end {prev_end:g} ≠ 母带总长 {total}")
    check("beats_monotonic", bool(segs) and not bad,
          "; ".join(bad) if bad else f"{len(segs)} 段单调连续,精确覆盖 [0, {total}]")

    # ---------------------------------------------------------------- 5/6/7 组时长
    spans, bad_range, bad_int = [], [], []
    for gi, g in enumerate(groups):
        gid = g.get("group_id", f"#{gi}")
        a_in, a_out = g.get("audio_in_s"), g.get("audio_out_s")
        span = g.get("av_span_s")
        if a_in is None or a_out is None or span is None:
            bad_range.append(f"{gid}(缺 audio_in_s/audio_out_s/av_span_s)")
            continue
        span = float(span)
        spans.append(span)
        if abs((float(a_out) - float(a_in)) - span) > 1e-6:
            bad_range.append(f"{gid}(av_span_s {span} ≠ audio_out−audio_in {float(a_out) - float(a_in):g})")
        if not (MIN_GROUP_S - 1e-9 <= span <= span_max + 1e-9):
            bad_range.append(f"{gid}(span {span} ∉ [{MIN_GROUP_S},{span_max}])")
        is_last = (gi == len(groups) - 1)
        if not is_last and abs(span - round(span)) > 1e-6:
            bad_range.append(f"{gid}(非末组 span {span} 非整数秒)")

        td = g.get("total_duration_s")
        if not isinstance(td, int) or isinstance(td, bool):
            bad_int.append(f"{gid}(total_duration_s={td!r} 非整数)")
        elif not (MIN_GROUP_S <= td <= hard_max):
            bad_int.append(f"{gid}(total_duration_s {td} ∉ [{MIN_GROUP_S},{hard_max}])")
        else:
            real = sum(float(shots_by_id[sid]["duration_s"])
                       for sid in (g.get("shots") or []) if sid in shots_by_id)
            if int(round(real)) != td:
                bad_int.append(f"{gid}(total_duration_s {td} ≠ int(round(Σ子镜头 {real:g})))")
            if td + 1e-9 < span:
                bad_int.append(f"{gid}(total_duration_s {td} < span {span},生成时长不足以覆盖音频段)")

    cover = sum(spans)
    ok_cover = total is not None and abs(cover - float(total)) <= FRAME_S
    check("spans_cover_master", ok_cover,
          f"Σspan {cover:.3f}s vs 母带 {total}s(容差 1 帧 {FRAME_S:.4f}s)"
          + ("" if ok_cover else " —— 画面未精确铺满音频,必然出现漂移或黑屏"))
    check("span_in_range", not bad_range, "; ".join(bad_range) or
          f"全部 {len(spans)} 组 span ∈[{MIN_GROUP_S},{span_max}],非末组均整数秒")
    check("duration_int_consistent", not bad_int, "; ".join(bad_int) or
          "全部组 total_duration_s 为整数且与 Σ子镜头一致")

    # ---------------------------------------------------------------- 8 ASCII 文件名
    nonascii = non_ascii_names(proj)
    check("ascii_filename", not nonascii,
          f"命中非 ASCII 文件名 {nonascii[:5]}" if nonascii else "全部落盘文件名仅 ASCII")

    # 内容检查(不含指纹——指纹正是 --stamp 要写的东西,故在盖章后才校验)
    content_ok = all(ok for _, ok in checks)

    # ---------------------------------------------------------------- --stamp(须在指纹校验前)
    if args.stamp:
        if not args.task_id:
            sys.exit("错误:--stamp 必须带 --task-id(盖章留痕)")
        if not content_ok:
            print("[RESULT] av_sync: FAIL(时间轴内容检查未过,拒绝盖章)")
            sys.exit(1)
        beat_track["av_sync"] = {
            "audio_map": str(am_path.relative_to(proj)) if am_path.is_relative_to(proj) else str(am_path),
            "audio_map_sha256": fp,
            "master_sha256": audio_map.get("master_sha256"),
            "master_duration_s": total,
            "group_count": len(groups),
            "stamped_by_task": args.task_id,
            "stamped_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        }
        tmp = str(bt_path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(beat_track, f, ensure_ascii=False, indent=1)
            f.write("\n")
        os.replace(tmp, bt_path)
        print(f"[STAMP] av_sync 已写入 {bt_path}(记得 vc register 登记新版本)")

    # ---------------------------------------------------------------- 8 指纹
    recorded_fp = (beat_track.get("av_sync") or {}).get("audio_map_sha256") \
        or beat_track.get("audio_map_sha256")
    if recorded_fp is None:
        check("beat_fingerprint", False,
              "beat_track 无 audio_map 指纹——timeline-planner 未盖章(用 --stamp),旧产物一律视为过期")
    else:
        check("beat_fingerprint", recorded_fp == fp,
              "指纹一致" if recorded_fp == fp else
              f"指纹失配(beat_track 记 {recorded_fp[:16]}…,当前 {fp[:16]}…)——"
              "audio_map 已改版,须回派 transcript-aligner 重对齐,严禁按旧 beat 继续")

    # ---------------------------------------------------------------- 9/10 片段阶段
    clips_dir = proj / "assets" / "clips" / ep
    clip_map = {g.get("group_id"): clips_dir / f"{g.get('group_id')}.mp4" for g in groups}
    present = {gid: p for gid, p in clip_map.items() if p and p.is_file()}
    if not present:
        if need("clips"):
            check("clip_ge_span", False, f"{clips_dir}/ 下无任何组片段(--require clips)")
        else:
            skip("clip_ge_span / clip_silent", f"{clips_dir}/ 尚无组片段,视觉生成未开始")
    else:
        short, noaudio_bad = [], []
        for g in groups:
            gid = g.get("group_id")
            p = present.get(gid)
            if not p:
                if need("clips"):
                    short.append(f"{gid}(片段缺失)")
                continue
            span = float(g.get("av_span_s") or 0)
            try:
                measured = avsync.probe_duration(str(p))
            except RuntimeError as e:
                short.append(f"{gid}({e})")
                continue
            if measured + 1e-6 < span:
                short.append(f"{gid}(实测 {measured:.3f}s < span {span}s,缺 {span - measured:.3f}s)")
            if avsync.probe_audio_info(str(p)).get("codec"):
                noaudio_bad.append(gid)
        check("clip_ge_span", not short, "; ".join(short) or
              f"{len(present)} 组片段实测时长均 ≥ 对应 span"
              + ("" if not short else ""))
        if short:
            print("        └ 处置阶梯:① timeline 用 speed=measured/span 微调(≤3% 不可感知)"
                  " ② 重 roll 该组 ③ 末帧定格补足")
        check("clip_silent", not noaudio_bad,
              f"以下组含音轨(应为 --generate-audio off 的无声画面):{noaudio_bad[:6]}"
              if noaudio_bad else f"{len(present)} 组片段均无音轨")

    # ---------------------------------------------------------------- 11-14 成片阶段
    tl_path = proj / "edit" / ep / "timeline.json"
    final = proj / "edit" / ep / "final.mp4"

    if not tl_path.is_file():
        if need("final"):
            check("timeline_out_exact", False, f"timeline 不存在:{tl_path}(--require final)")
        else:
            skip("timeline_out_exact / no_cumulative_drift", f"{tl_path} 尚不存在,剪辑未开始")
    else:
        timeline = json.loads(tl_path.read_text(encoding="utf-8"))
        entries = ((timeline.get("tracks") or {}).get("video")) or []
        span_by_gid = {g.get("group_id"): float(g.get("av_span_s") or 0) for g in groups}
        bad_out, cum_bad = [], []
        cum = 0.0
        for e in entries:
            gid = e.get("group_id")
            span = span_by_gid.get(gid)
            if span is None:
                bad_out.append(f"{gid}(不在 shot_list.generation_groups 中)")
                continue
            i_s, o_s = e.get("in"), e.get("out")
            if i_s is None or o_s is None:
                bad_out.append(f"{gid}(缺 in/out)")
                continue
            used = float(o_s) - float(i_s)
            if abs(used - span) > FRAME_S:
                bad_out.append(f"{gid}(out−in {used:.3f}s ≠ span {span}s)")
            g = next((x for x in groups if x.get("group_id") == gid), {})
            if g.get("audio_in_s") is not None and abs(cum - float(g["audio_in_s"])) > FRAME_S:
                cum_bad.append(f"{gid}(累计画面位置 {cum:.3f}s ≠ 音频起点 {g['audio_in_s']}s)")
            cum += used
        missing_gids = [g.get("group_id") for g in groups
                        if g.get("group_id") not in {e.get("group_id") for e in entries}]
        if missing_gids:
            bad_out.append(f"timeline 漏组 {missing_gids[:6]}")
        check("timeline_out_exact", not bad_out, "; ".join(bad_out) or
              f"{len(entries)} 条 timeline 条目 out−in 与 span 逐条一致")
        drift = abs(cum - float(total or 0))
        check("no_cumulative_drift", not cum_bad and drift <= FRAME_S,
              "; ".join(cum_bad) or f"累计画面总长 {cum:.3f}s vs 母带 {total}s,漂移 {drift * 1000:.1f}ms")

    if not final.is_file():
        if need("final"):
            check("final_duration_match", False, f"成片不存在:{final}(--require final)")
        else:
            skip("final_duration_match / final_audio_bitexact", f"{final} 尚不存在,未出成片")
    else:
        try:
            fd = avsync.probe_duration(str(final))
            ok_d = abs(fd - float(total)) <= DUR_TOL_S
            check("final_duration_match", ok_d,
                  f"成片 {fd:.3f}s vs 母带 {total}s,差 {abs(fd - float(total)) * 1000:.0f}ms(容差 {DUR_TOL_S * 1000:.0f}ms)")
        except RuntimeError as e:
            check("final_duration_match", False, str(e))
        if master.is_file():
            # 13. 参数同一性:codec/采样率/声道 任一变化即证明发生了重编码
            mi, fi = avsync.probe_audio_info(str(master)), avsync.probe_audio_info(str(final))
            same_params = all(mi.get(k) == fi.get(k)
                              for k in ("codec", "sample_rate", "channels"))
            check("final_audio_no_transcode", same_params,
                  f"母带 {mi.get('codec')}/{mi.get('sample_rate')}Hz/{mi.get('channels')}ch"
                  + (" 与成片一致" if same_params else
                     f" ≠ 成片 {fi.get('codec')}/{fi.get('sample_rate')}Hz/{fi.get('channels')}ch"
                     " —— 音轨被重编码,封装必须用 -c:a copy"))
            # 14. 逐帧原样:末帧允许被容器 gapless 补齐重写,其余帧必须逐字节相同
            try:
                r = avsync.compare_audio_frames(str(master), str(final))
                detail = (f"{r['muxed_frames']}/{r['master_frames']} 帧,"
                          f"帧 1..N-1 逐帧一致"
                          f"(末帧{'亦一致' if r['last_frame_identical'] else '因容器补齐被重写,允许'})")
                if not r["frame_count_match"]:
                    detail = f"帧数不等(母带 {r['master_frames']},成片 {r['muxed_frames']})"
                    if r["muxed_frames"] < r["master_frames"]:
                        detail += ("——成片音频短于母带:或封装误用了 -shortest(音频是权威时长,"
                                   "视频须 ≥ 音频后由 timeline 修剪),或母带在封装后被换过"
                                   "(先看 master_untouched 结论)")
                elif not r["head_frames_identical"]:
                    detail = (f"{r['diff_frame_count']} 帧载荷不同(首个差异帧 #{r['first_diff_index']})"
                              "—— 音轨被重编码,封装必须用 -c:a copy")
                check("final_audio_frames_intact", r["ok"], detail)
            except RuntimeError as e:
                check("final_audio_frames_intact", False, str(e))
        else:
            check("final_audio_no_transcode", False, f"母带不存在,无法比对:{master}")
            check("final_audio_frames_intact", False, f"母带不存在,无法比对:{master}")

    all_ok = all(ok for _, ok in checks)
    print(f"[RESULT] av_sync: {'PASS' if all_ok else 'FAIL'}")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
