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
先决:playwright + Chromium(render_captions.py doctor 自检;
caption_toolchain_verified 机检同口径)。
退出码:成功=0,任一失败=1。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import DATA_DIR, parse_args                          # noqa: E402

import captions as cap                                            # noqa: E402
import captions_html as chtml                                     # noqa: E402

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
    fonts = _load_manifest(FONTS_MANIFEST, "fonts")
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


def main():
    cmds = {"fonts-scan": None, "sfx-scan": None, "assets-sync": None,
            "render": cmd_render, "doctor": None,
            "sfx-track": cmd_sfx_track, "mux": cmd_mux}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print(__doc__)
        raise SystemExit(f"用法:render_captions.py {{{'|'.join(cmds)}}} ...")
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

    args, proj = parse_args(__doc__, configure=configure)
    if sub == "mux" and not args.video:
        raise SystemExit("[FAIL] mux 需要 --video(clips_caption 副本替换拼装后的无声视频)")
    cmds[sub](args, proj)


if __name__ == "__main__":
    main()
