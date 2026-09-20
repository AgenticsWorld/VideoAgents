#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""render_captions.py — 花字渲染 CLI(10-editing/caption、10-editing/edit 的唯一执行界面)。

花字纪律(WORKFLOW.md「花字与花字音效」节):Agent 禁止自写花字 ffmpeg 滤镜,
逐组烧录/SFX 轨/花字版封装一律走本 CLI——幂等回执与机检口径建立在统一编码参数上。

子命令:
  assets-sync                        从远端素材仓库同步字体/音效到本地缓存并重扫 manifest
                                     (仓库配置:modules/caption_assets.json 或
                                     $VIDEOAGENTS_CAPTION_ASSETS_REPO=owner/name[@branch];
                                     未配置时跳过,继续用本地素材)
  fonts-scan                         扫系统字体 + data/fonts/(外置,gitignored)→ data/fonts/manifest.json
  fonts-list [--project S] [--json]  列可用字体(项目 refs/fonts/ 用户字体排前,id 前缀 proj:,现扫不需重跑 fonts-scan)
  sfx-scan                           扫 data/sfx/ → data/sfx/manifest.json
  doctor                             HTML 引擎环境自检(playwright/Chromium/fonttools)
  render     --project X --ep epNN [--grp grpNNN ...] [--force]
                                     逐组烧录:assets/clips/{ep}/{grp}.mp4 + captions.json
                                     → assets/clips_caption/{ep}/{grp}.mp4(原 clip 不动;
                                     回执一致即 SKIP,--force 强制重渲)。
                                     HTML 引擎(captions_html.py;schema v3,模版在
                                     <项目>/edit/caption_templates/;libass 已退役)
  sfx-track  --project X --ep epNN [--out 相对路径]
                                     captions.json → edit/{ep}/caption_sfx.m4a(集级,时长=母带)
  mux        --project X --ep epNN --video <拼装视频>
                                     花字版封装:a:0=母带+SFX 预混,a:1=母带流拷贝
                                     → edit/{ep}/final_caption.mp4
  speech-align --project X --ep epNN [--backend auto|interp|whisper|import]
                                     [--words-json <外部ASR逐词JSON>] [--whisper-model small]
                                     建集级逐字语音时间轴 edit/{ep}/word_track.json
                                     (台本:av beat_track / 主流程 subtitles.srt;声轨:母带/final)。
                                     auto = 装了 faster-whisper 用 whisper,否则 interp。
  speech-lookup --project X --ep epNN --text "花字文案" [--near 秒]
                                     查这段文字被念出的起止时间(设计时逐条填 start/end 用)
  speech-snap  --project X --ep epNN [--dry-run]
                                     把 captions.json 每条花字的入出点吸附到语音起止
                                     (speech_free:true 的条目跳过),原地改写并打印变更
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
import speechalign as sa                                          # noqa: E402

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
    by_grp = _groups_with_captions(data)
    targets = args.grp or sorted(by_grp)
    n_r = n_s = 0
    cache = proj / "assets" / "caption_cache"
    with chtml.StickerRenderer(cache) as renderer:
        for grp in targets:
            caps = by_grp.get(grp) or []
            if not caps:
                print(f"[SKIP ] {grp}: 本组无花字,不产副本")
                continue
            clip = proj / "assets" / "clips" / args.ep / f"{grp}.mp4"
            if not clip.is_file():
                raise SystemExit(f"[FAIL] 缺终版组 clip {clip}")
            out = proj / "assets" / "clips_caption" / args.ep / f"{grp}.mp4"
            r = chtml.render_group_html(clip, out, caps, fonts, proj,
                                        renderer, force=args.force)
            n_r += r["status"] == "rendered"
            n_s += r["status"] == "skipped"
            print(f"[{'RENDER' if r['status'] == 'rendered' else 'SKIP  '}] {grp} → {r['out']}")
    print(f"[DONE] 渲染 {n_r} 组,幂等跳过 {n_s} 组,无花字 {len(targets) - n_r - n_s} 组")


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


def cmd_sfx_track(args, proj):
    sfx_m = _load_manifest(SFX_MANIFEST, "sfx")
    data, _ = _load_ep_inputs(proj, args.ep)
    master = _find_master(proj, args.ep)
    dur = cap.probe_duration(str(master))
    out = proj / (args.out or f"edit/{args.ep}/caption_sfx.m4a")
    r = cap.build_sfx_track(data, sfx_m, SFX_DIR, dur, out)
    print(f"[DONE] SFX 轨 {r['out']}({r['sfx_count']} 条音效,{r['duration_s']}s)")


def _find_master(proj: Path, ep: str) -> Path:
    """成片声轨权威:av 流程=用户母带(mp3/m4a/wav);主流程=audio-mixing 的 final wav。"""
    for ext in ("mp3", "m4a", "wav"):
        cand = proj / "assets" / "audio" / "master" / f"{ep}.{ext}"
        if cand.is_file():
            return cand
    cand = proj / "assets" / "audio" / "final" / f"{ep}.wav"
    if cand.is_file():
        return cand
    raise SystemExit(f"[FAIL] 找不到 {ep} 的声轨母带(assets/audio/master|final/)")


def cmd_mux(args, proj):
    video = Path(args.video) if Path(args.video).is_absolute() else proj / args.video
    if not video.is_file():
        raise SystemExit(f"[FAIL] 拼装视频不存在:{video}")
    master = _find_master(proj, args.ep)
    sfx_track = proj / "edit" / args.ep / "caption_sfx.m4a"
    if not sfx_track.is_file():
        raise SystemExit(f"[FAIL] 缺 {sfx_track}(先跑 sfx-track)")
    out = proj / "edit" / args.ep / "final_caption.mp4"
    r = cap.mux_caption_final(video, master, sfx_track, out)
    print(f"[DONE] 花字版成片 {r['out']}({r['duration_s']}s;"
          "a:0=预混 a:1=母带存档)")


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
    backend = args.backend
    if backend == "auto":
        try:
            import faster_whisper  # noqa: F401
            backend = "whisper" if audio is not None else "interp"
        except ImportError:
            backend = "interp"
    if backend == "interp":
        words, stats = sa.build_interp(segs, audio)
        source = "char_rate_interp"
    elif backend == "whisper":
        if audio is None:
            raise SystemExit("[FAIL] whisper 后端需要声轨文件")
        prompt = "".join(s["text"] for s in segs)[:200]
        asr = sa.run_whisper(audio, model_size=args.whisper_model,
                             language=args.language or None, initial_prompt=prompt)
        words, stats = sa.align_asr_to_transcript(segs, asr, audio)
        source = "asr_align"
        stats["asr"] = f"faster-whisper:{args.whisper_model}"
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
        words, stats = sa.align_asr_to_transcript(segs, asr, audio)
        source = "external_align"
        stats["asr"] = str(wp.name)
    else:
        raise SystemExit(f"[FAIL] 未知后端 {backend!r}")
    track = sa.assemble(ep, proj, audio, kind, tpath, segs, words, source, stats)
    out = sa.word_track_path(proj, ep)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(track, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[DONE] {out}:{len(words)} 个发声单元,台本={kind}:{tpath.name},"
          f"后端={source},confidence={track['confidence']},stats={stats}")
    if track["confidence"] == "low":
        print("       ⚠ 精度偏低(无静音检测/ASR 命中率低);建议装 faster-whisper 或导入外部 ASR 逐词结果")


def _load_track(proj, ep):
    p = sa.word_track_path(proj, ep)
    if not p.is_file():
        raise SystemExit(f"[FAIL] 缺 {p}(先跑 render_captions.py speech-align)")
    track = sa.load_word_track(p)
    stale = sa.staleness(track, proj, ep)
    if stale:
        print("[WARN ] word_track 可能过期:" + "; ".join(stale))
    return track


def cmd_speech_lookup(args, proj):
    if not args.text:
        raise SystemExit("[FAIL] speech-lookup 需要 --text")
    track = _load_track(proj, args.ep)
    sp = sa.locate(track, args.text, near_s=args.near)
    if sp is None:
        raise SystemExit(f"[MISS] 「{args.text}」在语音逐字轨中找不到(核对是否与台本一字不差)")
    words = track["words"][sp["first"]:sp["last"] + 1]
    print(f"[HIT ] 「{args.text}」 {sp['start']:.3f} → {sp['end']:.3f}s "
          f"(时长 {sp['end'] - sp['start']:.2f}s,匹配={sp['match']},候选 {sp['candidates']} 处)")
    print("       " + " ".join(f"{w['text']}@{w['start']:.2f}" for w in words))


def cmd_speech_snap(args, proj):
    data, shot_list = _load_ep_inputs(proj, args.ep)
    track = _load_track(proj, args.ep)
    rep = sa.snap_captions(data, track, shot_list)
    for r in rep["snapped"]:
        o, n = r["old"], r["new"]
        mv = f",{r['moved']}→改挂" if r.get("moved") else ""
        print(f"[SNAP ] {r['id']}「{r['text']}」 {o[0]}-{o[1]} → {n[0]}-{n[1]} ({r['match']}{mv})")
    for x in rep["skipped"]:
        print(f"[SKIP ] {x}")
    for x in rep["not_found"]:
        print(f"[MISS ] {x} 语音里找不到——核对文案或标 speech_free:true")
    if args.dry_run:
        print(f"[DRY ] 未写回;可吸附 {len(rep['snapped'])} 条,找不到 {len(rep['not_found'])} 条")
        return
    cj = proj / "edit" / args.ep / "captions.json"
    cj.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[DONE] {cj}:吸附 {len(rep['snapped'])} 条,跳过 {len(rep['skipped'])} 条,"
          f"找不到 {len(rep['not_found'])} 条")
    if rep["not_found"]:
        raise SystemExit(1)


def main():
    cmds = {"fonts-scan": None, "fonts-list": cmd_fonts_list,
            "sfx-scan": None, "assets-sync": None,
            "render": cmd_render, "doctor": None,
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
        ap.add_argument("--force", action="store_true", help="忽略幂等回执强制重渲")
        ap.add_argument("--video", default=None, help="mux:花字版拼装视频路径")
        ap.add_argument("--out", default=None, help="输出路径(相对项目根)")
        ap.add_argument("--backend", default="auto", choices=("auto", "interp", "whisper", "import"),
                        help="speech-align 后端(auto=有 faster-whisper 则 whisper,否则 interp)")
        ap.add_argument("--words-json", default=None, help="speech-align import:外部 ASR 逐词 JSON")
        ap.add_argument("--whisper-model", default="small", help="speech-align whisper 模型尺寸")
        ap.add_argument("--language", default=None, help="speech-align whisper 语言代码(缺省自动)")
        ap.add_argument("--text", default=None, help="speech-lookup:要定位的花字文案")
        ap.add_argument("--near", type=float, default=None, help="speech-lookup:参考时刻(秒),多次出现时取最近")
        ap.add_argument("--dry-run", action="store_true", help="speech-snap:只打印不写回")
        ap.add_argument("--json", action="store_true", help="fonts-list:输出合并后的 manifest JSON")

    args, proj = parse_args(__doc__, configure=configure)
    if sub == "mux" and not args.video:
        raise SystemExit("[FAIL] mux 需要 --video(clips_caption 副本替换拼装后的无声视频)")
    cmds[sub](args, proj)


if __name__ == "__main__":
    main()
