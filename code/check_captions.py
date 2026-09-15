#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_captions.py — 花字与花字音效机检(caption,主流程 p9-caption* 与 av 插件共用)。

事实源:`edit/epNN/captions.json`(schema v3)、`directing/epNN/shot_list.json`、
        `data/fonts|sfx/manifest.json`、终版组 clip 与 clips_caption 副本、花字版成片。
被检方:caption Agent 的设计产物、render_captions.py 的烧录副本、edit 的花字版封装。

花字纪律(WORKFLOW.md「花字与花字音效」节):原组 clip 永不改动;渲染只走宿主 CLI;
花字版成片 a:0=母带+SFX 预混(开箱即听)、a:1=母带流拷贝(存档轨,逐帧校验)。

分阶段(输入不存在的阶段记 SKIP,不算失败;--require 强制要求某阶段就位):

  [design] 设计阶段(caption 设计工单交付前必跑)
    1. caption_schema_v2          schema_version=3、字段/枚举合法(检查名保持历史沿用)
    2. caption_groups_valid       group_id 命中 shot_list,local 时间落在组时长内
    3. caption_time_consistent    集级 start == 组起点 + local_start(±0.1s,双写对账)
    4. caption_assets_resolved    font_id/sfx_id 全部在 manifest 命中
    5. caption_text_from_source   花字文本片段能在母带原文找到(仅 av 项目,防造词;
                                  有 bible/dictionary.json 的主流程项目仍走 dictionary_match_100)
    6. ascii_filename             花字产物文件名仅 ASCII(WORKFLOW.md §1 原则 9)
   6a. caption_speech_aligned     花字入出点 == 语音里这段文字被念出的起止(±0.15s;
                                  依据 edit/epNN/word_track.json 逐字轨,由
                                  render_captions.py speech-align 生成;speech_free:true
                                  的画面标注型花字豁免,av 项目不得豁免;有台本时间码
                                  却缺/过期 word_track 即 FAIL)

  [render] 烧录阶段(av4/p9 caption-render 交付前必跑)
    7. caption_toolchain_verified playwright + Chromium 可启动(HTML 引擎)
   7a. caption_glyph_coverage     模版 font:// 可解析 + 花字文本字形全覆盖
                                  (浏览器缺字形静默回退,libass 时代同款陷阱)
    8. captions_rendered_all      每个含花字的组都有副本+回执,且回执指纹新鲜
                                  (v3:源 clip sha / 花字+模版+字体 hash / 引擎版本)
    9. caption_render_spec_ok     副本 宽/高/fps 与源一致,时长差 ≤1 帧
   10. caption_clip_audio_intact  av:副本保持无声;主流程:副本音轨参数与源一致

  [final] 成片阶段(av4/p9 caption-final 交付前必跑)
   11. caption_final_duration_match  花字版时长 == 声轨权威时长 ±0.10s
   12. caption_premix_present       a:0 为 AAC 预混轨且另有 a:1 存档轨(≥2 条音轨)
   13. final_caption_master_frames_intact  a:1 与母带逐帧 md5 一致(仅 av;
                                  a:0 是预混轨,零重编码的存档口径移到 a:1)
   14. no_black_frames             花字版无成段黑帧(blackdetect ≥0.4s 即 FAIL)

分桶实现说明:1–5 复用 modules/captions.py 的 validate_captions(单一事实源),
按其消息文案归桶——改那边的文案时请同步 _BUCKET_PATTERNS。

用法:
  python3 code/check_captions.py --project <slug> --ep epNN                    # 自动判阶段
  python3 code/check_captions.py --project <slug> --ep epNN --require design   # 设计交付
  python3 code/check_captions.py --project <slug> --ep epNN --require final    # 成片终审
退出码:全 PASS=0,任一 FAIL=1。
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import DATA_DIR, parse_args                          # noqa: E402

import captions as cap                                            # noqa: E402
import captions_html as chtml                                     # noqa: E402
import avsync                                                     # noqa: E402
import speechalign as sa                                          # noqa: E402

STAGES = ("design", "render", "final")
DUR_TOL_S = 0.10
FONTS_DIR = DATA_DIR / "fonts"
SFX_DIR = DATA_DIR / "sfx"

# validate_captions 消息 → 机检名(顺序敏感:先专后泛)
_BUCKET_PATTERNS = (
    ("caption_text_from_source", ("母带原文",)),
    ("caption_assets_resolved", ("manifest",)),
    ("caption_time_consistent", ("与 组起点",)),
    ("caption_groups_valid", ("group_id", "local_start", "local_end", "超出组时长")),
)


def _bucket(msg: str) -> str:
    for name, keys in _BUCKET_PATTERNS:
        if any(k in msg for k in keys):
            return name
    return "caption_schema_v2"


def main():
    def configure(ap):
        ap.add_argument("--require", choices=STAGES, default=None,
                        help="强制要求该阶段(含其前置阶段)的输入就位,缺失即 FAIL")
        ap.add_argument("--captions", default=None, help="显式指定 captions.json 路径")
        ap.add_argument("--shot-list", default=None, help="显式指定 shot_list.json 路径")

    args, proj = parse_args(__doc__, configure=configure)
    ep = args.ep
    checks: list[tuple[str, bool]] = []

    def check(name, ok, detail=""):
        checks.append((name, bool(ok)))
        print(f"[CHECK] {name}: {'PASS' if ok else 'FAIL'}" + (f"  {detail}" if detail else ""))

    def skip(name, why):
        print(f"[SKIP ] {name}: {why}")

    def need(stage):
        return bool(args.require) and STAGES.index(stage) <= STAGES.index(args.require)

    # ---------------------------------------------------------------- design
    cj = Path(args.captions) if args.captions else proj / "edit" / ep / "captions.json"
    sl_path = Path(args.shot_list) if args.shot_list else proj / "directing" / ep / "shot_list.json"
    data = shot_list = None
    if not cj.is_file():
        if need("design"):
            check("caption_schema_v2", False, f"缺 {cj}")
        else:
            skip("design", f"{cj} 不存在(花字未设计或开关未开)")
    elif not sl_path.is_file():
        check("caption_groups_valid", False, f"缺 {sl_path}")
    else:
        data = cap.load_captions(cj)
        shot_list = json.loads(sl_path.read_text(encoding="utf-8"))
        fonts_m = cap.load_fonts_manifest(FONTS_DIR / "manifest.json", proj) \
            if (FONTS_DIR / "manifest.json").is_file() else None
        sfx_m = json.loads((SFX_DIR / "manifest.json").read_text(encoding="utf-8")) \
            if (SFX_DIR / "manifest.json").is_file() else None
        if fonts_m is None or sfx_m is None:
            check("caption_assets_resolved", False,
                  "缺 fonts/sfx manifest(render_captions.py fonts-scan / sfx-scan)")
        # av 项目的防造词口径:无 dictionary 时以母带原文为唯一事实源
        beat_path = proj / "av" / "beat_track.json"
        has_dict = (proj / "bible" / "dictionary.json").is_file()
        beat_text = None
        if beat_path.is_file() and not has_dict:
            bt = json.loads(beat_path.read_text(encoding="utf-8"))
            beat_text = "".join(s.get("text", "") for s in bt.get("segments", []))
        issues = cap.validate_captions(data, shot_list, fonts_manifest=fonts_m,
                                       sfx_manifest=sfx_m, beat_text=beat_text,
                                       proj_root=proj)
        buckets = {n: [] for n, _ in _BUCKET_PATTERNS}
        buckets["caption_schema_v2"] = []
        for msg in issues:
            buckets[_bucket(msg)].append(msg)
        for name in ("caption_schema_v2", "caption_groups_valid",
                     "caption_time_consistent", "caption_assets_resolved"):
            check(name, not buckets[name], "; ".join(buckets[name][:3]))
        if beat_text is not None:
            check("caption_text_from_source", not buckets["caption_text_from_source"],
                  "; ".join(buckets["caption_text_from_source"][:3]))
        else:
            skip("caption_text_from_source",
                 "存在 bible/dictionary.json(走 dictionary_match_100)" if has_dict
                 else "无 av/beat_track.json(主流程项目)")
        # 花字入出点与语音逐字起止对齐(2026-08-18 用户裁定:准确匹配这段文字被念出的时段)
        wt_path = sa.word_track_path(proj, ep)
        if sa.find_transcript(proj, ep) is None:
            skip("caption_speech_aligned", f"无台本时间码(av/{ep}/beat_track.json | edit/{ep}/subtitles.srt)")
        elif not wt_path.is_file():
            check("caption_speech_aligned", False,
                  f"缺 {wt_path.relative_to(proj)}(先跑 render_captions.py speech-align)")
        else:
            try:
                track = sa.load_word_track(wt_path)
                stale = sa.staleness(track, proj, ep)
                sp_issues = (["word_track 过期:" + "; ".join(stale)] if stale else []) \
                    + sa.check_speech_alignment(data, track, shot_list,
                                                allow_speech_free=beat_text is None)
            except (ValueError, OSError) as e:
                sp_issues = [f"word_track 无法读取:{e}"]
            check("caption_speech_aligned", not sp_issues,
                  (f"({len(sp_issues)} 条)" + "; ".join(sp_issues[:3])) if sp_issues else "")
        bad = [str(p) for p in (cj, proj / "assets" / "clips_caption" / ep)
               if p.exists() and p.name != p.name.encode("ascii", "ignore").decode()]
        bad += avsync_non_ascii(proj / "assets" / "clips_caption" / ep)
        check("ascii_filename", not bad, "; ".join(bad[:3]))

    # ---------------------------------------------------------------- render
    cap_dir = proj / "assets" / "clips_caption" / ep
    if data is None or (not cap_dir.is_dir() and not need("render")):
        if data is not None:
            skip("render", f"{cap_dir} 不存在(未到烧录阶段)")
    else:
        ok_tc, tc_msg = chtml.chromium_ready()
        check("caption_toolchain_verified", ok_tc, "" if ok_tc else tc_msg)
        by_grp = {}
        for c in data.get("captions", []):
            by_grp.setdefault(c.get("group_id"), []).append(c)
        fonts_m = cap.load_fonts_manifest(FONTS_DIR / "manifest.json", proj)
        missing, stale, spec_bad, audio_bad = [], [], [], []
        for grp, caps in sorted(by_grp.items()):
            src = proj / "assets" / "clips" / ep / f"{grp}.mp4"
            out = cap_dir / f"{grp}.mp4"
            receipt = cap_dir / f"{grp}.render.json"
            if not (out.is_file() and receipt.is_file()):
                missing.append(grp)
                continue
            if not src.is_file():
                stale.append(f"{grp}(源 clip 已不存在)")
                continue
            try:
                old = json.loads(receipt.read_text(encoding="utf-8"))
                fresh = (old.get("renderer") == chtml.HTML_RENDERER_VERSION
                         and old.get("src_sha256") == avsync.file_sha256(str(src))
                         and old.get("captions_hash")
                         == chtml._captions_hash_v3(caps, proj, fonts_m))
            except (json.JSONDecodeError, OSError, RuntimeError):
                fresh = False
            if not fresh:
                stale.append(grp)
                continue
            si, oi = cap.probe_video_info(str(src)), cap.probe_video_info(str(out))
            frame_tol = 1.0 / max(1, si["fps"]) + 0.001
            if (si["width"], si["height"]) != (oi["width"], oi["height"]) \
                    or abs(si["fps"] - oi["fps"]) > 0.01 \
                    or abs(si["duration_s"] - oi["duration_s"]) > frame_tol:
                spec_bad.append(f"{grp}({si['width']}x{si['height']}@{si['fps']}"
                                f"/{si['duration_s']:.3f}s → {oi['width']}x{oi['height']}"
                                f"@{oi['fps']}/{oi['duration_s']:.3f}s)")
            if si["has_audio"] != oi["has_audio"]:
                audio_bad.append(f"{grp}(源 has_audio={si['has_audio']} 副本={oi['has_audio']})")
            elif si["has_audio"]:
                a, b = avsync.probe_audio_info(str(src)), avsync.probe_audio_info(str(out))
                if (a["codec"], a["sample_rate"], a["channels"]) != \
                        (b["codec"], b["sample_rate"], b["channels"]):
                    audio_bad.append(f"{grp}(音轨参数变化 {a} → {b})")
        # HTML 引擎的字体实效口径:模版 font:// 可解析 + 字形全覆盖
        # (浏览器缺字形同样静默回退,渲前逐条预检;引擎渲染时也会再拦一次)
        glyph_bad = []
        for caps in by_grp.values():
            for c in caps:
                try:
                    files = chtml.template_font_files(
                        proj, c.get("template_ref") or "", fonts_m)
                    miss = chtml.glyph_missing(c.get("text") or "", files)
                    if miss:
                        glyph_bad.append(f"{c.get('id')}:{miss}")
                except RuntimeError as e:
                    glyph_bad.append(f"{c.get('id')}:{e}")
        check("caption_glyph_coverage", not glyph_bad, "; ".join(glyph_bad[:3]))
        check("captions_rendered_all", not missing and not stale,
              (f"缺副本:{missing[:5]} " if missing else "")
              + (f"回执过期(需重渲):{stale[:5]}" if stale else ""))
        check("caption_render_spec_ok", not spec_bad, "; ".join(spec_bad[:3]))
        check("caption_clip_audio_intact", not audio_bad, "; ".join(audio_bad[:3]))

    # ---------------------------------------------------------------- final
    final_cap = proj / "edit" / ep / "final_caption.mp4"
    if not final_cap.is_file():
        if need("final"):
            check("caption_final_duration_match", False, f"缺 {final_cap}")
        elif data is not None:
            skip("final", f"{final_cap} 不存在(未到封装阶段)")
    else:
        master = None
        for cand in (*(proj / "assets" / "audio" / "master" / f"{ep}.{ext}"
                       for ext in ("mp3", "m4a", "wav")),
                     proj / "assets" / "audio" / "final" / f"{ep}.wav"):
            if cand.is_file():
                master = cand
                break
        ref_dur = avsync.probe_duration(str(master)) if master else \
            (avsync.probe_duration(str(proj / "edit" / ep / "final.mp4"))
             if (proj / "edit" / ep / "final.mp4").is_file() else None)
        got = avsync.probe_duration(str(final_cap))
        if ref_dur is None:
            check("caption_final_duration_match", False, "找不到声轨母带或干净版成片作时长权威")
        else:
            check("caption_final_duration_match", abs(got - ref_dur) <= DUR_TOL_S,
                  f"got={got:.3f}s ref={ref_dur:.3f}s tol={DUR_TOL_S}")
        streams = _audio_streams(final_cap)
        premix_ok = (len(streams) >= 2 and streams[0].get("codec_name") == "aac")
        check("caption_premix_present", premix_ok,
              f"音轨数={len(streams)} a0={streams[0].get('codec_name') if streams else '无'}"
              "(要求 a:0=AAC 预混 + a:1=母带存档)")
        is_av = bool(master) and master.parent.name == "master"
        if is_av and len(streams) >= 2:
            cmp = avsync.compare_audio_frames(str(master), str(final_cap), "0:a:1")
            check("final_caption_master_frames_intact", cmp["ok"],
                  "" if cmp["ok"] else
                  f"frames {cmp['master_frames']}/{cmp['muxed_frames']} "
                  f"diff={cmp['diff_frame_count']} first={cmp['first_diff_index']}")
        else:
            skip("final_caption_master_frames_intact",
                 "主流程项目(混音轨非零重编码口径)" if not is_av else "花字版缺 a:1")
        spans = _black_spans(final_cap)
        check("no_black_frames", not spans, f"黑帧段:{spans[:3]}")

    print()
    n_fail = sum(1 for _, ok in checks if not ok)
    print(f"[RESULT] {len(checks) - n_fail}/{len(checks)} PASS" + (f",{n_fail} FAIL" if n_fail else ",全部通过"))
    sys.exit(1 if n_fail else 0)


def avsync_non_ascii(root: Path) -> list[str]:
    bad = []
    if root.is_dir():
        for p in root.rglob("*"):
            if p.name != p.name.encode("ascii", "ignore").decode():
                bad.append(str(p))
    return bad


def _audio_streams(path: Path) -> list[dict]:
    out = avsync._run(["ffprobe", "-v", "error", "-select_streams", "a",
                       "-show_entries", "stream=index,codec_name,sample_rate,channels",
                       "-of", "json", str(path)], timeout=60)
    try:
        return json.loads(out).get("streams") or []
    except json.JSONDecodeError:
        return []


def _black_spans(path: Path, min_d: float = 0.4) -> list[str]:
    out = avsync._run(["ffmpeg", "-v", "info", "-i", str(path),
                       "-vf", f"blackdetect=d={min_d}:pix_th=0.10",
                       "-an", "-f", "null", "-"], timeout=3600)
    return [f"{s}-{e}s" for s, e in
            re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", out)]


if __name__ == "__main__":
    main()
