#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""render_captions.py — 花字渲染 CLI(10-editing/caption、10-editing/edit 的唯一执行界面)。

花字纪律(WORKFLOW.md「花字与花字音效」节):Agent 禁止自写花字 ffmpeg 滤镜,
逐组烧录/SFX 轨/花字版封装一律走本 CLI——幂等回执与机检口径建立在统一编码参数上。

花字时间轴(2026-09-25,modules/caption_timeline.py;前科 fengshen3 ep06 DEF-ep06-caption-0001):
  本 CLI 一切时间换算都走花字时间轴——烧录取源 = 各组**当前采纳的后期版本**(未采纳 = 母本),
  captions.json 的 local_start/local_end 是母本 v0 坐标,由宿主按版本 time_ops 换到版本坐标(落在被删段内的
  花字丢弃并 WARN);集级 start/end、word_track、speech-lookup 一律是 **cut 基准**(正片 = 后期版本 + 组边界层
  定格/黑场/字卡,不含片头);花字版成片按干净版 final.mp4 的时间轴(cut 基准 + 片头)落位。
  Agent 不需要也不允许手算这些偏移。

子命令:
  assets-sync                        从远端素材仓库同步字体/音效到本地缓存并重扫 manifest
                                     (仓库配置:modules/caption_assets.json 或
                                     $VIDEOAGENTS_CAPTION_ASSETS_REPO=owner/name[@branch];
                                     未配置时跳过,继续用本地素材)
  fonts-scan                         扫系统字体 + data/fonts/(外置,gitignored)→ data/fonts/manifest.json
  fonts-list [--project S] [--json]  列可用字体(项目 refs/fonts/ 用户字体排前,id 前缀 proj:,现扫不需重跑 fonts-scan)
  sfx-scan                           扫 data/sfx/ → data/sfx/manifest.json
  doctor                             HTML 引擎环境自检(playwright/Chromium/fonttools)
  timeline   --project X --ep epNN [--json]
                                     打印花字时间轴:正片 / 片头 / 组边界层 / 各组取源版本与起点(排障与设计参考)
  policy     --project X --ep epNN [--stamp]
                                     打印生效的花字策略(output.caption_mode auto|manual、
                                     允许类型、本项目不可用类型);--stamp 把 caption_policy
                                     盖章写进 captions.json(机检 caption_policy_fresh)
  render     --project X --ep epNN [--grp grpNNN ...] [--force]
                                     逐组烧录:各组当前采纳版本 + captions.json
                                     → assets/clips_caption/{ep}/{grp}.mp4(原 clip / 版本文件不动;
                                     回执一致即 SKIP,--force 强制重渲)。
                                     HTML 引擎(captions_html.py;schema v3,模版在
                                     <项目>/edit/caption_templates/;libass 已退役)
  final      --project X --ep epNN [--force]
                                     花字版成片一步到位(caption-final 工单的唯一工序):在干净版
                                     edit/{ep}/final.mp4 上按成片时间轴叠全部花字 → 建 SFX 轨 → 封装
                                     edit/{ep}/final_caption.mp4(a:0=声轨权威+SFX 预混,a:1=声轨权威流拷贝);
                                     回执 edit/{ep}/final_caption.json,一致即 SKIP
  sfx-track  --project X --ep epNN [--out 相对路径]
                                     captions.json → edit/{ep}/caption_sfx.m4a(成片时间轴,时长=干净版成片)
  mux        --project X --ep epNN --video <已烧录成片画面>
                                     旧式封装(画面由别的路径产出时):a:0=声轨权威+SFX 预混,a:1=声轨权威流拷贝
                                     → edit/{ep}/final_caption.mp4;常规请用 final
  speech-align --project X --ep epNN [--backend auto|interp|whisper|import]
                                     [--words-json <外部ASR逐词JSON>] [--whisper-model <id>]
                                     建集级逐字语音时间轴 edit/{ep}/word_track.json(cut 基准)
                                     (台本:av beat_track / 主流程 subtitles.srt;声轨:母带/final)。
                                     auto = 装了 faster-whisper 用 whisper,否则 interp。
  speech-lookup --project X --ep epNN --text "花字文案" [--near 秒]
                                     查这段文字被念出的起止时间(cut 基准;设计时逐条填 start/end 用)
  speech-snap  --project X --ep epNN [--dry-run]
                                     把 captions.json 每条花字的入出点吸附到语音起止
                                     (speech_free:true 的条目跳过),local 按时间轴回写 v0 坐标,原地改写并打印变更
先决:playwright + Chromium(render_captions.py doctor 自检;
caption_toolchain_verified 机检同口径)。
退出码:成功=0,任一失败=1。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import DATA_DIR, parse_args, reexec_with_host_python                          # noqa: E402

import captions as cap                                            # noqa: E402
import captions_html as chtml                                     # noqa: E402
import caption_timeline as ct                                     # noqa: E402
import speechalign as sa                                          # noqa: E402
import timemap                                                    # noqa: E402
from avsync import file_sha256, probe_duration                    # noqa: E402

FONTS_DIR = DATA_DIR / "fonts"
SFX_DIR = DATA_DIR / "sfx"
FONTS_MANIFEST = FONTS_DIR / "manifest.json"
SFX_MANIFEST = SFX_DIR / "manifest.json"


def _load_manifest(path: Path, kind: str) -> dict:
    if not path.is_file():
        raise SystemExit(f"[FAIL] 缺 {path};先跑 render_captions.py {kind}-scan")
    return json.loads(path.read_text(encoding="utf-8"))


def _load_ep_inputs(proj: Path, ep: str):
    cj = proj / "edit" / ep / "captions.json"
    sl = proj / "directing" / ep / "shot_list.json"
    if not cj.is_file():
        raise SystemExit(f"[FAIL] 缺 {cj}(先派 caption 设计工单)")
    if not sl.is_file():
        raise SystemExit(f"[FAIL] 缺 {sl}")
    data = cap.load_captions(cj)
    shot_list = json.loads(sl.read_text(encoding="utf-8"))
    return data, shot_list


def _groups_with_captions(data: dict) -> dict:
    by_grp: dict[str, list] = {}
    for c in data.get("captions", []):
        by_grp.setdefault(c.get("group_id") or "?", []).append(c)
    return by_grp


def _timeline(proj: Path, ep: str, audio_used: bool = True) -> ct.CaptionTimeline:
    """花字时间轴(混音基准过期等硬错误由 timemap_layers 直接 SystemExit 上报)。"""
    tl = ct.CaptionTimeline.resolve(proj, ep, audio_used=audio_used)
    print(f"[AXIS ] {tl.describe()}")
    for w in tl.warnings:
        print(f"[WARN ] {w}")
    return tl


def _rel(proj: Path, p: Path | None) -> str:
    if p is None:
        return "(无)"
    try:
        return str(Path(p).relative_to(proj))
    except ValueError:
        return str(p)


def cmd_timeline(args, proj):
    tl = _timeline(proj, args.ep)
    if args.json:
        print(json.dumps(tl.as_dict(), ensure_ascii=False, indent=1))
        return
    for n in tl.notes:
        print(f"[NOTE ] {n}")
    print(f"[INFO ] 干净版成片:{_rel(proj, tl.final)};片头偏移 {tl.cut_offset_s:.3f}s;声轨权威:{tl.audio_authority()[1]};指纹 {tl.fingerprint()}")
    for gid in tl.order:
        g = tl.groups[gid]
        w = tl.group_window(gid)
        ops = f"  [{timemap.describe(g.local_ops)}]" if g.local_ops else ""
        print(f"[GRP  ] {gid} v{g.v} {g.src_rel or '(无文件)'} {g.duration_s:.3f}s cut {w[0]:.3f}–{w[1]:.3f}s({g.authority}){ops}")


def cmd_render(args, proj):
    try:                      # 全局 manifest + 项目 refs/fonts/ 用户字体(proj:*)
        fonts = cap.load_fonts_manifest(FONTS_MANIFEST, proj, require=True)
    except RuntimeError as e:
        raise SystemExit(f"[FAIL] {e}")
    data, shot_list = _load_ep_inputs(proj, args.ep)
    ok, msg = chtml.chromium_ready()
    if not ok:
        raise SystemExit(f"[FAIL] caption_toolchain_verified: {msg}")
    issues = cap.validate_captions(data, shot_list, fonts_manifest=fonts,
                                   proj_root=proj)
    if issues:
        print("[FAIL] captions.json 未过 design 校验:")
        for i in issues:
            print("   -", i)
        raise SystemExit(1)
    tl = _timeline(proj, args.ep, audio_used=False)
    by_grp = _groups_with_captions(data)
    targets = args.grp or sorted(by_grp)
    n_r = n_s = n_d = 0
    cache = proj / "assets" / "caption_cache"
    with chtml.StickerRenderer(cache) as renderer:
        for grp in targets:
            caps = by_grp.get(grp) or []
            if not caps:
                print(f"[SKIP ] {grp}: 本组无花字,不产副本")
                continue
            g = tl.groups.get(grp)
            if g is None or g.src is None:
                raise SystemExit(f"[FAIL] {grp}: 找不到组源(当前采纳版本 / 母本 assets/clips/{args.ep}/{grp}.mp4)")
            burn, dropped = tl.burn_captions(grp, caps)
            for d in dropped:
                print(f"[WARN ] {grp}: 花字 {d} 落在后期删段内,本版本不烧(设计侧可改挂 / 删除)")
            if not burn:
                n_d += 1
                print(f"[SKIP ] {grp}: 本组花字全部落在删段内,不产副本")
                continue
            out = proj / "assets" / "clips_caption" / args.ep / f"{grp}.mp4"
            extra = {"src_version": g.v, "src_rel": g.src_rel, "local_ops": g.local_ops,
                     "timeline_fingerprint": tl.fingerprint(), "dropped": dropped}
            r = chtml.render_group_html(g.src, out, burn, fonts, proj,
                                        renderer, force=args.force, receipt_extra=extra)
            n_r += r["status"] == "rendered"
            n_s += r["status"] == "skipped"
            ver = f"v{g.v}" + ("(后期版本)" if g.v > 0 else "(母本)")
            print(f"[{'RENDER' if r['status'] == 'rendered' else 'SKIP  '}] {grp} {ver} {g.src_rel} → {r['out']}")
    print(f"[DONE] 渲染 {n_r} 组,幂等跳过 {n_s} 组,无花字 {len(targets) - n_r - n_s - n_d} 组,全被删段 {n_d} 组")


def cmd_fonts_list(args, proj):
    """列出本项目可用字体:refs/fonts/ 项目字体排前,其后是 data/fonts/ 与系统字体。"""
    m = cap.load_fonts_manifest(FONTS_MANIFEST, proj)
    if args.json:
        print(json.dumps(m, ensure_ascii=False, indent=1))
        return
    n_proj = sum(1 for f in m["fonts"] if f.get("source") == "project")
    if not FONTS_MANIFEST.is_file():
        print(f"[WARN] 缺 {FONTS_MANIFEST}(先跑 fonts-scan 才能看到系统/外置字体)")
    print(f"[INFO] 项目字体 {n_proj} 个(refs/fonts/,Web「参考文件」页「字体」板块上传),"
          f"其余 {len(m['fonts']) - n_proj} 个来自 data/fonts/ 与系统")
    for f in m["fonts"]:
        cjk = "CJK" if f.get("cjk") else ("?" if f.get("cjk") is None else "-")
        print(f"  {f['id']:<48} {cjk:<4} {f['family']} {f.get('subfamily') or ''}".rstrip())


def cmd_doctor(args, proj=None):
    ok, msg = chtml.chromium_ready()
    print(f"[{'OK  ' if ok else 'FAIL'}] HTML 引擎:{msg}")
    try:
        import fontTools                                          # noqa: F401
        print("[OK  ] fonttools(字形覆盖预检)")
    except ImportError:
        ok = False
        print("[FAIL] 缺 fonttools:pip install fonttools")
    if not ok:
        raise SystemExit(1)


# ---------------------------------------------------------------- 花字版成片

def _final_items(tl: ct.CaptionTimeline, data: dict) -> list[dict]:
    items, dropped = ct.plan_final_items(tl, data)
    for d in dropped:
        print(f"[WARN ] 花字 {d} 不进花字版成片")
    return items


def _sfx_items(items: list[dict]) -> list[dict]:
    """build_sfx_track 读条目 start(集级):这里换成成片秒。"""
    return [{**it["cap"], "start": it["t0"], "end": it["t1"]} for it in items]


def _build_sfx(proj: Path, ep: str, items: list[dict], duration_s: float, out: Path) -> Path | None:
    if not ct.sfx_uses(items):
        print("[INFO ] 全集花字无 sfx 引用,不建 SFX 轨(a:0 = 声轨权威单独转码)")
        return None
    sfx_m = _load_manifest(SFX_MANIFEST, "sfx")
    r = cap.build_sfx_track({"captions": _sfx_items(items), "cards": []}, sfx_m, SFX_DIR, duration_s, out)
    print(f"[DONE ] SFX 轨 {r['out']}({r['sfx_count']} 条音效,{r['duration_s']}s,成片时间轴)")
    return Path(r["out"])


def _audio_authority(tl: ct.CaptionTimeline) -> tuple[Path, str]:
    audio, kind = tl.audio_authority()
    if audio is None:
        raise SystemExit("[FAIL] 找不到声轨权威(干净版 edit/{ep}/final.mp4 或母带 assets/audio/master/);先出干净版成片")
    return audio, kind


def cmd_sfx_track(args, proj):
    data, _ = _load_ep_inputs(proj, args.ep)
    tl = _timeline(proj, args.ep)
    items = _final_items(tl, data)
    ref = tl.final or tl.cut
    if ref is None:
        raise SystemExit("[FAIL] 缺成片 / 正片文件,无法定 SFX 轨时长")
    dur = probe_duration(str(ref))
    out = proj / (args.out or f"edit/{args.ep}/caption_sfx.m4a")
    if _build_sfx(proj, args.ep, items, dur, out) is None:
        raise SystemExit(1)


def _mux(proj: Path, ep: str, video: Path, audio: Path, kind: str, sfx: Path | None) -> dict:
    out = proj / "edit" / ep / "final_caption.mp4"
    r = cap.mux_caption_final(video, audio, sfx, out)
    print(f"[DONE ] 花字版成片 {r['out']}({r['duration_s']}s;a:0=预混 a:1=声轨权威存档,权威={kind}:{_rel(proj, audio)})")
    return r


def cmd_mux(args, proj):
    video = Path(args.video) if Path(args.video).is_absolute() else proj / args.video
    if not video.is_file():
        raise SystemExit(f"[FAIL] 拼装视频不存在:{video}")
    tl = _timeline(proj, args.ep)
    audio, kind = _audio_authority(tl)
    sfx_track = proj / "edit" / args.ep / "caption_sfx.m4a"
    data, _ = _load_ep_inputs(proj, args.ep)
    if ct.sfx_uses(_final_items(tl, data)) and not sfx_track.is_file():
        raise SystemExit(f"[FAIL] 缺 {sfx_track}(先跑 sfx-track)")
    _mux(proj, args.ep, video, audio, kind, sfx_track if sfx_track.is_file() else None)


def cmd_final(args, proj):
    ep = args.ep
    try:
        fonts = cap.load_fonts_manifest(FONTS_MANIFEST, proj, require=True)
    except RuntimeError as e:
        raise SystemExit(f"[FAIL] {e}")
    data, shot_list = _load_ep_inputs(proj, ep)
    ok, msg = chtml.chromium_ready()
    if not ok:
        raise SystemExit(f"[FAIL] caption_toolchain_verified: {msg}")
    issues = cap.validate_captions(data, shot_list, fonts_manifest=fonts, proj_root=proj)
    if issues:
        print("[FAIL] captions.json 未过 design 校验:")
        for i in issues:
            print("   -", i)
        raise SystemExit(1)
    tl = _timeline(proj, ep)
    if tl.final is None:
        raise SystemExit(f"[FAIL] 缺干净版 edit/{ep}/final.mp4(先 post_apply.py finalize / finalize_episode.py assemble 出干净版)")
    if tl.timeline_stale:
        raise SystemExit("[FAIL] 干净版成片的 timeline 与当前采纳版本不一致,花字会错位:先 post_apply.py finalize 重出干净版,再出花字版")
    items = _final_items(tl, data)
    if not items:
        raise SystemExit("[FAIL] 没有任何花字能落到成片时间轴上")
    audio, kind = _audio_authority(tl)
    ed = proj / "edit" / ep
    out = ed / "final_caption.mp4"
    receipt_p = ct.final_receipt_path(proj, ep)
    expect = {"src_sha256": file_sha256(str(tl.final)), "captions_hash": chtml.episode_items_hash(items, proj, fonts),
              "renderer": chtml.HTML_RENDERER_VERSION, "audio_sha256": file_sha256(str(audio)),
              "sfx_plan": ct.sfx_plan_fingerprint(items), "timeline_fingerprint": tl.fingerprint()}
    if not args.force and out.is_file() and receipt_p.is_file():
        try:
            old = json.loads(receipt_p.read_text(encoding="utf-8"))
            if all(old.get(k) == v for k, v in expect.items()) and old.get("out_sha256") == file_sha256(str(out)):
                print(f"[SKIP ] {_rel(proj, out)} 回执一致(源成片 / 花字 / 声轨 / 时间轴都没变);--force 强制重出")
                return
        except (json.JSONDecodeError, OSError):
            pass
    for it in items:
        c = it["cap"]
        print(f"[PLAN ] {c.get('id')} {it['group_id']}「{c.get('text')}」 local {c.get('local_start')}-{c.get('local_end')} → 成片 {it['t0']:.3f}-{it['t1']:.3f}s")
    burned = ed / "final_caption.video.mp4"
    cache = proj / "assets" / "caption_cache"
    with chtml.StickerRenderer(cache) as renderer:
        r = chtml.render_episode_html(tl.final, burned, items, fonts, proj, renderer, receipt_p, force=True,
                                      receipt_extra={"timeline_fingerprint": tl.fingerprint(), "cut": _rel(proj, tl.cut),
                                                     "cut_offset_s": tl.cut_offset_s})
    print(f"[BURN ] {len(items)} 条花字叠到 {_rel(proj, tl.final)} → {burned.name}")
    dur = probe_duration(str(tl.final))
    sfx = _build_sfx(proj, ep, items, dur, ed / "caption_sfx.m4a")
    try:
        m = _mux(proj, ep, burned, audio, kind, sfx)
    finally:
        burned.unlink(missing_ok=True)
    receipt = {**r["receipt"], **expect, "audio_authority": {"kind": kind, "path": _rel(proj, audio)},
               "sfx_track": _rel(proj, sfx) if sfx else None, "out": _rel(proj, out),
               "out_sha256": file_sha256(str(out)), "duration_s": m["duration_s"], "items": [
                   {"id": it["cap"].get("id"), "group_id": it["group_id"], "t0": it["t0"], "t1": it["t1"]} for it in items]}
    receipt_p.write_text(json.dumps(receipt, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[DONE ] 回执 {_rel(proj, receipt_p)};交付前跑 python3 code/check_captions.py --project <slug> --ep {ep} --require final")


# ---------------------------------------------------------------- 逐字语音时间轴

def cmd_speech_align(args, proj):
    ep = args.ep
    found = sa.find_transcript(proj, ep)
    if found is None:
        raise SystemExit(f"[FAIL] 缺台本时间码:av/{ep}/beat_track.json | av/beat_track.json | "
                         f"edit/{ep}/subtitles.srt 都不存在")
    kind, tpath = found
    segs = sa.load_segments(kind, tpath)
    if not segs:
        raise SystemExit(f"[FAIL] {tpath} 没有可用的逐句时间码")
    audio = sa.find_audio(proj, ep)
    if audio is None:
        print(f"[WARN ] 找不到 {ep} 声轨(assets/audio/master|final/),静音检测/ASR 不可用")
    tl = _timeline(proj, ep, audio_used=audio is not None)
    # 台本(字幕基准)→ cut 基准 → 声轨基准:对齐/静音检测全程在声轨自身时间轴上做,产出再折回 cut 基准
    def seg_to_audio(t: float) -> float:
        t_cut = timemap.map_time(tl.ops, t) if tl.ops else float(t)
        return timemap.inverse_time(tl.audio_ops, t_cut) if (audio is not None and tl.audio_ops) else t_cut
    def word_to_cut(t: float) -> float:
        if audio is not None:
            return timemap.map_time(tl.audio_ops, t) if tl.audio_ops else float(t)
        return float(t)          # 无声轨:词时刻已在 cut 基准(台本经 ops 折算)
    segs_a = [{**s, "start": round(seg_to_audio(s["start"]), 3), "end": round(seg_to_audio(s["end"]), 3)} for s in segs]
    segs_a = [s for s in segs_a if s["end"] > s["start"]]
    if tl.ops or tl.audio_ops:
        print(f"[AXIS ] 台本→声轨基准折算:{timemap.describe(tl.ops) if tl.ops else '无'};声轨→cut:{timemap.describe(tl.audio_ops) if tl.audio_ops else '无'}")
    backend = args.backend
    if backend == "auto":
        try:
            import faster_whisper  # noqa: F401
            backend = "whisper" if audio is not None else "interp"
        except ImportError:
            backend = "interp"
    if backend == "interp":
        words, stats = sa.build_interp(segs_a, audio)
        source = "char_rate_interp"
    elif backend == "whisper":
        if audio is None:
            raise SystemExit("[FAIL] whisper 后端需要声轨文件")
        prompt = "".join(s["text"] for s in segs)[:200]
        asr_model = sa.asr_model(args.whisper_model)
        asr = sa.run_whisper(audio, model_size=asr_model,
                             language=args.language or None, initial_prompt=prompt)
        words, stats = sa.align_asr_to_transcript(segs_a, asr, audio)
        source = "asr_align"
        stats["asr"] = f"faster-whisper:{asr_model}"
    elif backend == "import":
        if not args.words_json:
            raise SystemExit("[FAIL] --backend import 需要 --words-json <外部ASR逐词JSON>")
        wp = Path(args.words_json)
        wp = wp if wp.is_absolute() else proj / wp
        raw = json.loads(wp.read_text(encoding="utf-8"))
        items = raw if isinstance(raw, list) else raw.get("words") or raw.get("items") or []
        asr = sa.normalize_external_words(items)
        if not asr:
            raise SystemExit(f"[FAIL] {wp} 里没有 [{{text,start,end}}] 逐词条目")
        words, stats = sa.align_asr_to_transcript(segs_a, asr, audio)
        source = "external_align"
        stats["asr"] = str(wp.name)
    else:
        raise SystemExit(f"[FAIL] 未知后端 {backend!r}")
    for w in words:
        w["start"], w["end"] = round(word_to_cut(w["start"]), 3), round(word_to_cut(w["end"]), 3)
    time_axis = {"basis": "cut", "cut": _rel(proj, tl.cut) if tl.cut else None, "cut_offset_s": tl.cut_offset_s,
                 "fingerprint": tl.fingerprint(), "audio_ops": tl.audio_ops, "transcript_ops": tl.ops,
                 "note": "词时刻 = cut 基准(后期版本 + 组边界层,不含片头),与 captions.json start/end、speech-lookup 同尺"}
    track = sa.assemble(ep, proj, audio, kind, tpath, segs, words, source, stats, time_axis=time_axis)
    out = sa.word_track_path(proj, ep)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(track, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[DONE] {out}:{len(words)} 个发声单元,台本={kind}:{tpath.name},"
          f"后端={source},confidence={track['confidence']},基准=cut({tl.fingerprint()}),stats={stats}")
    if track["confidence"] == "low":
        print("       ⚠ 精度偏低(无静音检测/ASR 命中率低);建议装 faster-whisper 或导入外部 ASR 逐词结果")


def _load_track(proj, ep, tl=None):
    p = sa.word_track_path(proj, ep)
    if not p.is_file():
        raise SystemExit(f"[FAIL] 缺 {p}(先跑 render_captions.py speech-align)")
    track = sa.load_word_track(p)
    stale = sa.staleness(track, proj, ep, tl)
    if stale:
        print("[WARN ] word_track 可能过期:" + "; ".join(stale))
    return track


def cmd_speech_lookup(args, proj):
    if not args.text:
        raise SystemExit("[FAIL] speech-lookup 需要 --text")
    tl = _timeline(proj, args.ep)
    track = _load_track(proj, args.ep, tl)
    sp = sa.locate(track, args.text, near_s=args.near)
    if sp is None:
        raise SystemExit(f"[MISS] 「{args.text}」在语音逐字轨中找不到(核对是否与台本一字不差)")
    words = track["words"][sp["first"]:sp["last"] + 1]
    gid = tl.group_at(sp["start"])
    loc = ""
    if gid:
        ls, le = tl.cut_to_local(gid, sp["start"]), tl.cut_to_local(gid, sp["end"])
        loc = f",组 {gid} local {ls:.3f}-{le:.3f}"
    print(f"[HIT ] 「{args.text}」 {sp['start']:.3f} → {sp['end']:.3f}s "
          f"(时长 {sp['end'] - sp['start']:.2f}s,匹配={sp['match']},候选 {sp['candidates']} 处;cut 基准{loc})")
    print("       " + " ".join(f"{w['text']}@{w['start']:.2f}" for w in words))


def cmd_speech_snap(args, proj):
    data, shot_list = _load_ep_inputs(proj, args.ep)
    tl = _timeline(proj, args.ep)
    track = _load_track(proj, args.ep, tl)
    rep = sa.snap_captions(data, track, shot_list, tl=tl)
    for r in rep["snapped"]:
        o, n = r["old"], r["new"]
        mv = f",{r['moved']}→改挂" if r.get("moved") else ""
        print(f"[SNAP ] {r['id']}「{r['text']}」 {o[0]}-{o[1]} → {n[0]}-{n[1]} ({r['match']}{mv})")
    for x in rep["skipped"]:
        print(f"[SKIP ] {x}")
    for x in rep["not_found"]:
        print(f"[MISS ] {x} 语音里找不到——核对文案或标 speech_free:true")
    # 没被吸附的条目(speech_free / 找不到)也把集级 start/end 按时间轴从 local 回写:集级永远 = local 的 cut 基准换算
    snapped_ids = {r["id"] for r in rep["snapped"]}
    n_sync = 0
    for c in data.get("captions", []):
        if (c.get("id") in snapped_ids) or c.get("group_id") not in tl.groups:
            continue
        try:
            ls, le = float(c.get("local_start")), float(c.get("local_end"))
        except (TypeError, ValueError):
            continue
        s0, s1 = tl.local_to_cut(c["group_id"], ls), tl.local_to_cut(c["group_id"], le, end=True)
        if s0 is None or s1 is None:
            print(f"[WARN ] {c.get('id')}「{c.get('text')}」local {ls}-{le} 落在 {c['group_id']} 的后期删段内,集级不可换算(改挂或删除)")
            continue
        s0, s1 = round(s0, 3), round(s1, 3)
        if (c.get("start"), c.get("end")) != (s0, s1):
            print(f"[SYNC ] {c.get('id')}「{c.get('text')}」集级 {c.get('start')}-{c.get('end')} → {s0}-{s1}(按时间轴从 local 回写)")
            c["start"], c["end"] = s0, s1
            n_sync += 1
    if args.dry_run:
        print(f"[DRY ] 未写回;可吸附 {len(rep['snapped'])} 条,集级回写 {n_sync} 条,找不到 {len(rep['not_found'])} 条")
        return
    cj = proj / "edit" / args.ep / "captions.json"
    data["time_axis"] = f"cut 基准(正片 {tl.cut.name if tl.cut else '-'}:后期版本 + 组边界层,不含片头);local 为母本 v0 组内坐标;宿主 render_captions.py speech-snap 写"
    cj.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[DONE] {cj}:吸附 {len(rep['snapped'])} 条,集级回写 {n_sync} 条,跳过 {len(rep['skipped'])} 条,"
          f"找不到 {len(rep['not_found'])} 条")
    if rep["not_found"]:
        raise SystemExit(1)


def cmd_policy(args, proj):
    """打印本项目生效的花字策略(模式/允许类型/可用性);--stamp 把盖章写进 captions.json。"""
    import caption_catalog as ccat
    policy = ccat.load_project_policy(proj)
    avail = {a["id"]: a for a in ccat.availability(proj)}
    print(json.dumps({"mode": policy["mode"], "types": policy["types"],
                      "allowed": list(ccat.allowed_types(policy)),
                      "unavailable": {k: v["reason"] for k, v in avail.items() if not v["available"]}},
                     ensure_ascii=False, indent=1))
    if args.stamp:
        cj = proj / "edit" / args.ep / "captions.json"
        data = cap.load_captions(cj)
        ccat.stamp(data, policy)
        cj.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[DONE] {cj}:caption_policy 盖章 {data[ccat.POLICY_KEY]}")


def main():
    cmds = {"fonts-scan": None, "fonts-list": cmd_fonts_list,
            "policy": cmd_policy, "timeline": cmd_timeline,
            "sfx-scan": None, "assets-sync": None,
            "render": cmd_render, "final": cmd_final, "doctor": None,
            "sfx-track": cmd_sfx_track, "mux": cmd_mux,
            "speech-align": cmd_speech_align, "speech-lookup": cmd_speech_lookup,
            "speech-snap": cmd_speech_snap}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print(__doc__)
        raise SystemExit(f"用法:render_captions.py {{{'|'.join(cmds)}}} ...")
    reexec_with_host_python()   # 缺 Playwright 时换宿主解释器重跑(_common;须在 pop 子命令之前,argv 原样传递)
    sub = sys.argv.pop(1)
    if sub == "doctor":
        cmd_doctor(None)
        return
    if sub == "assets-sync":
        # 从远端素材仓库(modules/caption_assets.json 或
        # $VIDEOAGENTS_CAPTION_ASSETS_REPO)同步字体/音效,并重扫两份 manifest
        r = cap.sync_assets(FONTS_DIR, SFX_DIR)
        if r.get("skipped"):
            print(f"[SKIP ] 远端素材库未配置({r['reason']}),继续使用本地素材")
            return
        print(f"[SYNC ] {r['repo']}@{r['branch']}:下载 {r['downloaded']},"
              f"移除 {r['removed']},已有 {r['kept']}")
        m1 = cap.scan_fonts(FONTS_DIR, FONTS_MANIFEST)
        m2 = cap.scan_sfx(SFX_DIR, SFX_MANIFEST)
        print(f"[DONE] manifest 刷新:{len(m1['fonts'])} 个字体面,{len(m2['sfx'])} 条音效")
        return
    if sub == "fonts-scan":
        m = cap.scan_fonts(FONTS_DIR, FONTS_MANIFEST)
        cjk = sum(1 for f in m["fonts"] if f.get("cjk"))
        print(f"[DONE] {FONTS_MANIFEST}:{len(m['fonts'])} 个字体面(CJK {cjk} 个)")
        return
    if sub == "sfx-scan":
        m = cap.scan_sfx(SFX_DIR, SFX_MANIFEST)
        print(f"[DONE] {SFX_MANIFEST}:{len(m['sfx'])} 条音效")
        if not m["sfx"]:
            print("       data/sfx/ 为空:跑 scripts/fetch_sfx.py --synth 生成入门包,"
                  "或自放 CC0 素材后重扫")
        return

    def configure(ap):
        ap.add_argument("--grp", nargs="*", default=None, help="只处理指定组(可多个)")
        ap.add_argument("--force", action="store_true", help="忽略幂等回执强制重渲 / 重出")
        ap.add_argument("--stamp", action="store_true", help="policy:把当前花字策略盖章写进 captions.json")
        ap.add_argument("--video", default=None, help="mux:已烧录的成片画面路径(旧式封装)")
        ap.add_argument("--out", default=None, help="输出路径(相对项目根)")
        ap.add_argument("--backend", default="auto", choices=("auto", "interp", "whisper", "import"),
                        help="speech-align 后端(auto=有 faster-whisper 则 whisper,否则 interp)")
        ap.add_argument("--words-json", default=None, help="speech-align import:外部 ASR 逐词 JSON")
        ap.add_argument("--whisper-model", default=None,
                        help="speech-align whisper 模型(缺省=设置→高级→语音输入选中的识别模型)")
        ap.add_argument("--language", default=None, help="speech-align whisper 语言代码(缺省自动)")
        ap.add_argument("--text", default=None, help="speech-lookup:要定位的花字文案")
        ap.add_argument("--near", type=float, default=None, help="speech-lookup:参考时刻(秒),多次出现时取最近")
        ap.add_argument("--dry-run", action="store_true", help="speech-snap:只打印不写回")
        ap.add_argument("--json", action="store_true", help="fonts-list / timeline:输出 JSON")

    args, proj = parse_args(__doc__, configure=configure)
    if sub == "mux" and not args.video:
        raise SystemExit("[FAIL] mux 需要 --video(已烧录的成片画面);一步到位请用 render_captions.py final")
    cmds[sub](args, proj)


if __name__ == "__main__":
    main()
