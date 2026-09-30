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
    2. caption_groups_valid       group_id 命中 shot_list,local 时间落在组时长内(母本 v0 坐标)
    3. caption_time_consistent    集级 start == 组起点 + local_start(±0.1s,双写对账);主流程按**花字时间轴**
                                  (modules/caption_timeline.py:cut 基准 = 后期采纳版本 + 组边界层,不含片头)核对,
                                  落在后期删段内的花字亦 FAIL;av 项目仍按 shot_list audio_in_s;
                                  混音基准过期等时间轴无法解析的硬错误在此 FAIL
    4. caption_assets_resolved    font_id/sfx_id 全部在 manifest 命中
    5. caption_text_from_source   花字文本片段能在母带原文找到(仅 av 项目,防造词;
                                  有 bible/dictionary.json 的主流程项目仍走 dictionary_match_100)
    6. ascii_filename             花字产物文件名仅 ASCII(WORKFLOW.md §1 原则 9)
   6a. caption_speech_aligned     花字入出点 == 语音里这段文字被念出的起止(±0.15s;
                                  依据 edit/epNN/word_track.json 逐字轨,由
                                  render_captions.py speech-align 生成;speech_free:true
                                  的画面标注型花字豁免,av 项目不得豁免;有台本时间码
                                  却缺/过期 word_track 即 FAIL)
   6b. caption_types_allowed     花字策略=手动(settings.json output.caption_mode=manual)时,
                                  每条 type 必在用户勾选集合 output.caption_types 内
                                  (目录 modules/caption_catalog.json;auto 模式 SKIP)
   6d. caption_types_covered     手动策略下每个勾选类型要么至少出一条,要么在 captions.json 顶层
                                  type_skips.<type> 写明不出的原因(本项目不可用类型不计;
                                  「缺模版」不算理由;auto 模式 SKIP;2026-09-30)
   6c. caption_policy_fresh      captions.json 顶层 caption_policy 盖章 == 当前设置
                                  (manual 缺章即 FAIL;auto 缺章按 auto 兼容存量;
                                  改了勾选集合而设计未重跑即 FAIL)

  [render] 烧录阶段(av4/p9 caption-render 交付前必跑)
    7. caption_toolchain_verified playwright + Chromium 可启动(HTML 引擎)
   7a. caption_glyph_coverage     模版 font:// 可解析 + 花字文本字形全覆盖
                                  (浏览器缺字形静默回退,libass 时代同款陷阱)
    8. captions_rendered_all      每个含花字的组都有副本+回执,且回执指纹新鲜
                                  (v3:源 sha / 花字+模版+字体 hash / 引擎版本;源 = 该组**当前采纳的后期版本**,
                                  未采纳 = 母本;花字 local 时间按版本 time_ops 换算后参与 hash,2026-09-25)
    9. caption_render_spec_ok     副本 宽/高/fps 与源一致,时长差 ≤1 帧
   10. caption_clip_audio_intact  av:副本保持无声;主流程:副本音轨参数与源一致

  [final] 成片阶段(av4/p9 caption-final 交付前必跑)
   11. caption_final_duration_match  花字版时长 == 声轨权威时长 ±0.10s(权威 = caption_timeline.audio_authority:
                                  干净版 final.mp4 自带声轨,或 av 项目母带)
  11a. caption_final_fresh         花字版回执 edit/epNN/final_caption.json 与当前干净版成片 / 花字 / 声轨 / 时间轴一致
                                  (render_captions.py final 写;无回执的旧产物 FAIL——须用 final 重出)
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
from _common import DATA_DIR, parse_args, reexec_with_host_python                          # noqa: E402

import captions as cap                                            # noqa: E402
import caption_catalog as ccat                                    # noqa: E402
import captions_html as chtml                                     # noqa: E402
import caption_timeline as ct                                     # noqa: E402
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
    reexec_with_host_python()   # 缺 Playwright 时换宿主解释器重跑(_common)
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
        # 花字时间轴(2026-09-25):主流程集级 start/end 必须是 cut 基准 = local 经后期版本 time_ops + 组边界层换算
        tl, tl_err = None, None
        try:
            tl = ct.CaptionTimeline.resolve(proj, ep, audio_used=True)
        except SystemExit as e:
            tl_err = str(e)
        if tl_err:
            buckets["caption_time_consistent"].append(f"花字时间轴无法解析:{tl_err}")
        else:
            for w in tl.warnings:
                print(f"[WARN ] {w}")
            print(f"[AXIS ] {tl.describe()}(指纹 {tl.fingerprint()})")
            for i, c in enumerate(data.get("captions", [])):
                tag = c.get("id") or f"captions[{i}]"
                g = tl.groups.get(c.get("group_id"))
                if g is None or g.authority == "audio_in_s":
                    continue                      # 组不存在(groups_valid 已报)/ av 项目沿用 audio_in_s 口径
                try:
                    ls, le = float(c.get("local_start")), float(c.get("local_end"))
                except (TypeError, ValueError):
                    continue
                exp0 = tl.local_to_cut(c["group_id"], ls)
                exp1 = tl.local_to_cut(c["group_id"], le, end=True)
                if exp0 is None or exp1 is None or exp1 - exp0 < ct.MIN_BURN_S:
                    buckets["caption_time_consistent"].append(
                        f"{tag}: local {ls}-{le} 落在 {c['group_id']} 的后期删段 / 修剪之外(该版本里不存在这段画面,改挂或删除)")
                    continue
                st = c.get("start")
                if isinstance(st, (int, float)) and abs(float(st) - exp0) > 0.1:
                    buckets["caption_time_consistent"].append(
                        f"{tag}: start {st} 与 组起点+local_start 的 cut 基准 {exp0:.3f} 不一致(speech-snap 可回写)")
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
                stale = sa.staleness(track, proj, ep, tl)
                sp_issues = (["word_track 过期:" + "; ".join(stale)] if stale else []) \
                    + sa.check_speech_alignment(data, track, shot_list,
                                                allow_speech_free=beat_text is None, tl=tl)
            except (ValueError, OSError) as e:
                sp_issues = [f"word_track 无法读取:{e}"]
            check("caption_speech_aligned", not sp_issues,
                  (f"({len(sp_issues)} 条)" + "; ".join(sp_issues[:3])) if sp_issues else "")
        # 花字策略(2026-09-24):手动模式只准出用户勾选的类型 + 盖章与设置一致
        policy = ccat.load_project_policy(proj)
        allowed_bad, fresh_bad = ccat.policy_issues(data, policy)
        if policy["mode"] == "manual":
            check("caption_types_allowed", not allowed_bad,
                  (f"允许 {policy['types']};" if allowed_bad else "") + "; ".join(allowed_bad[:3]))
            cov_bad = ccat.coverage_issues(data, policy, proj)
            check("caption_types_covered", not cov_bad, "; ".join(cov_bad[:3]))
        else:
            skip("caption_types_allowed", "花字策略=自动(output.caption_mode=auto)")
            skip("caption_types_covered", "花字策略=自动(output.caption_mode=auto)")
        check("caption_policy_fresh", not fresh_bad, "; ".join(fresh_bad[:2]))
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
            g = tl.groups.get(grp) if tl is not None else None
            src = g.src if (g is not None and g.src is not None) else proj / "assets" / "clips" / ep / f"{grp}.mp4"
            burn = caps
            if g is not None:
                burn, _dropped = tl.burn_captions(grp, caps)
                if not burn:
                    continue                      # 本组花字全部落在删段内:不应有副本
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
                         == chtml._captions_hash_v3(burn, proj, fonts_m))
                if fresh and g is not None and int(old.get("src_version", 0) or 0) != g.v:
                    fresh = False                 # 回执写的版本号与当前采纳指针不符(旧回执无此字段 = v0 母本)
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
              + (f"回执过期(需重渲,源 = 当前采纳版本):{stale[:5]}" if stale else ""))
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
        for cand in (proj / "assets" / "audio" / "master" / f"{ep}.{ext}" for ext in ("mp3", "m4a", "wav")):
            if cand.is_file():
                master = cand
                break
        tl_f, tl_f_err = None, None
        try:
            tl_f = ct.CaptionTimeline.resolve(proj, ep, audio_used=True)
        except SystemExit as e:
            tl_f_err = str(e)
        authority, auth_kind = (tl_f.audio_authority() if tl_f is not None else (master, "master" if master else "none"))
        ref_dur = avsync.probe_duration(str(authority)) if authority else None
        got = avsync.probe_duration(str(final_cap))
        if ref_dur is None:
            check("caption_final_duration_match", False, tl_f_err or "找不到声轨权威(干净版 final.mp4 / 母带)作时长权威")
        else:
            check("caption_final_duration_match", abs(got - ref_dur) <= DUR_TOL_S,
                  f"got={got:.3f}s ref={ref_dur:.3f}s({auth_kind}) tol={DUR_TOL_S}")
        # 花字版回执(render_captions.py final):源成片 / 花字+入出点 / 声轨 / 时间轴任一变了都要重出
        rp = ct.final_receipt_path(proj, ep)
        if tl_f is None or data is None:
            check("caption_final_fresh", False, tl_f_err or "缺 captions.json")
        elif not rp.is_file():
            check("caption_final_fresh", False, f"缺 {rp.relative_to(proj)}(旧路径产物;用 render_captions.py final 重出)")
        else:
            items, _dropped = ct.plan_final_items(tl_f, data)
            fonts_f = cap.load_fonts_manifest(FONTS_DIR / "manifest.json", proj) \
                if (FONTS_DIR / "manifest.json").is_file() else {"fonts": []}
            try:
                old = json.loads(rp.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                old = {}
            expect = {"src_sha256": avsync.file_sha256(str(tl_f.final)) if tl_f.final else None,
                      "captions_hash": chtml.episode_items_hash(items, proj, fonts_f) if items else None,
                      "renderer": chtml.HTML_RENDERER_VERSION,
                      "audio_sha256": avsync.file_sha256(str(authority)) if authority else None,
                      "sfx_plan": ct.sfx_plan_fingerprint(items), "timeline_fingerprint": tl_f.fingerprint(),
                      "out_sha256": avsync.file_sha256(str(final_cap))}
            diff = [k for k, v in expect.items() if old.get(k) != v]
            check("caption_final_fresh", not diff, ("过期字段:" + ",".join(diff) + "(重跑 render_captions.py final)") if diff else "")
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
        # 设计内的黑场(字卡 / fade_black / 黑场垫片)按转场台账白名单豁免(与干净版 transition_render_ok 同口径,窗口 +片头偏移)
        wl = _black_whitelist(proj, ep, tl_f)
        spans, exempt = _drop_whitelisted(spans, wl)
        check("no_black_frames", not spans, f"黑帧段:{spans[:3]}" if spans else (f"设计内黑场 {exempt} 段已豁免" if exempt else ""))

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


def _black_whitelist(proj: Path, ep: str, tl) -> list[tuple[float, float]]:
    """transitions_render.json#black_frame_whitelist(out_cut 基准)→ 成片基准窗口;台账 out_cut 不是当前正片时不豁免。"""
    tr = proj / "edit" / ep / "transitions_render.json"
    if tl is None or tl.cut is None or not tr.is_file():
        return []
    try:
        d = json.loads(tr.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if Path(d.get("out_cut") or "").name != tl.cut.name:
        return []
    off = float(tl.cut_offset_s or 0.0)
    out = []
    for w in d.get("black_frame_whitelist") or []:
        try:
            out.append((float(w["start_s"]) + off, float(w["end_s"]) + off))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _drop_whitelisted(spans: list[str], wl: list[tuple[float, float]], tol: float = 0.15) -> tuple[list[str], int]:
    keep, n = [], 0
    for sp in spans:
        a, b = (float(x) for x in sp.rstrip("s").split("-"))
        if any(a >= s0 - tol and b <= s1 + tol for s0, s1 in wl):
            n += 1
        else:
            keep.append(sp)
    return keep, n


def _black_spans(path: Path, min_d: float = 0.4) -> list[str]:
    out = avsync._run(["ffmpeg", "-v", "info", "-i", str(path),
                       "-vf", f"blackdetect=d={min_d}:pix_th=0.10",
                       "-an", "-f", "null", "-"], timeout=3600)
    return [f"{s}-{e}s" for s, e in
            re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", out)]


if __name__ == "__main__":
    main()
