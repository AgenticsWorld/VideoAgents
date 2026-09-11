#!/usr/bin/env python3
"""动态样片(animatic,故事板预览页,2026-09-11):把一集的分镜草图按时长串成视频,烧入台词/旁白字幕,
有旁白/对白音轨时按挂点混入。零生成费用(ffmpeg + PIL),用来在花视频生成费用之前看节奏与镜头密度。

输入:directing/<ep>/storyboard.json(草案镜与时长建议)、shot_list.json(有则用定稿时长 duration_s 与旁白挂点)、
     assets/storyboard/<ep>/index.json + <S01-01>.png(草图;缺图用占位卡:灰底 + 镜号 + 画面内容文字)、
     assets/audio/narration/<ep>/manifest.json|narration_track.json(旁白段,可选)、
     assets/audio/voice/<ep>/... 对白干声(可选,按镜 shNNN 命名的 mp3/wav)。
输出:assets/storyboard/<ep>/animatic.mp4 + animatic.json(shots[] 起止秒/是否有草图、duration_source=final|draft、
     缺图数、inputs_mtime 供页面判「样片已过期」)。

用法:python3 code/animatic.py --project <slug> --ep ep01 [--fps 24] [--no-audio] [--dry-run]
退出码:0 成功、1 ffmpeg 失败、2 storyboard.json 缺失。宿主 CLI,由故事板页按钮经宿主后台调用。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from _common import parse_args

from modules import storyboard_board as sbb

FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf", "/System/Library/Fonts/STHeiti Light.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
]
DEFAULT_SHOT_S = 4.0
AUDIO_EXTS = (".mp3", ".wav", ".m4a", ".flac", ".ogg")


def _font(size: int):
    from PIL import ImageFont
    root = sbb.ROOT
    cands = [str(p) for p in sorted((root / "data" / "fonts").glob("*")) if p.suffix.lower() in (".ttf", ".otf", ".ttc")]
    for f in cands + FONT_CANDIDATES:
        if Path(f).is_file():
            try:
                return ImageFont.truetype(f, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _wrap(draw, text: str, font, max_w: int) -> list[str]:
    """按像素宽逐字折行(中文无空格,英文按词)。"""
    lines, cur = [], ""
    for tok in re.findall(r"\s+|[A-Za-z0-9'’.,;:!?()\-]+|.", text):
        trial = cur + tok
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur.rstrip())
            cur = tok.lstrip()
    if cur.strip():
        lines.append(cur.rstrip())
    return lines


def _canvas_size(aspect: str) -> tuple[int, int]:
    try:
        rw, rh = (int(x) for x in aspect.split(":"))
    except Exception:
        rw, rh = 16, 9
    return (1280, 720) if rw >= rh else (720, 1280)


def render_frame(out: Path, sketch: Path | None, size: tuple[int, int], label: str, content: str,
                 subs: list[tuple[str, str]]) -> None:
    """一镜一帧:草图等比铺满(留黑边)/ 占位卡;左上角镜号标签;底部字幕带(台词黄、旁白紫)。"""
    from PIL import Image, ImageDraw
    W, H = size
    im = Image.new("RGB", (W, H), (18, 18, 22))
    d = ImageDraw.Draw(im)
    if sketch and sketch.is_file():
        try:
            sk = Image.open(sketch).convert("RGB")
            sk.thumbnail((W, H))
            im.paste(sk, ((W - sk.width) // 2, (H - sk.height) // 2))
        except Exception:
            sketch = None
    if not sketch or not sketch.is_file():
        f = _font(max(22, W // 40))
        lines = _wrap(d, content or "(无画面描述)", f, int(W * 0.8))[:8]
        lh = int(f.size * 1.5)
        y = (H - lh * len(lines)) // 2
        for ln in lines:
            d.text(((W - d.textlength(ln, font=f)) // 2, y), ln, font=f, fill=(150, 156, 170))
            y += lh
        fs = _font(max(18, W // 60))
        d.text((W // 2 - d.textlength("(草图未出)", font=fs) // 2, y + 8), "(草图未出)", font=fs, fill=(110, 116, 130))
    # 标签
    fl = _font(max(18, W // 58))
    pad = 8
    tw = d.textlength(label, font=fl)
    d.rectangle((12, 12, 12 + tw + pad * 2, 12 + fl.size + pad * 2), fill=(0, 0, 0))
    d.text((12 + pad, 12 + pad), label, font=fl, fill=(230, 234, 240))
    # 字幕带
    if subs:
        fsub = _font(max(20, W // 42))
        lh = int(fsub.size * 1.4)
        rows = []
        for kind, text in subs:
            for k, ln in enumerate(_wrap(d, text, fsub, int(W * 0.9))):
                rows.append((kind, ln))
        rows = rows[-6:]
        band_h = lh * len(rows) + 24
        d.rectangle((0, H - band_h, W, H), fill=(0, 0, 0))
        y = H - band_h + 12
        for kind, ln in rows:
            color = (251, 191, 36) if kind == "dialogue" else (192, 132, 252)
            d.text(((W - d.textlength(ln, font=fsub)) // 2, y), ln, font=fsub, fill=color)
            y += lh
    im.save(out, "PNG")


def _narration_audio(base: Path, ep: str, sl: dict, shot_start: dict[str, float]) -> list[tuple[Path, float]]:
    """旁白段 → (文件, 起始秒):按 shot_list narration_anchors 的首个挂点镜起始时间。"""
    ndir = base / "assets" / "audio" / "narration" / ep
    man = sbb._read_json(ndir / "manifest.json") or sbb._read_json(ndir / "narration_track.json") or {}
    files = {s.get("num"): s.get("file") for s in man.get("segments") or [] if isinstance(s, dict) and s.get("num")}
    out = []
    for a in sl.get("narration_anchors") or []:
        if not isinstance(a, dict):
            continue
        nid = a.get("narration_id")
        rel = files.get(nid) or f"{ep}_nar_{str(nid or '').split('-')[-1]}.mp3"
        f = ndir / rel
        shots = [s for s in (a.get("anchor_shots") or []) if s in shot_start]
        if f.is_file() and shots:
            out.append((f, shot_start[shots[0]]))
    return out


def _dialogue_audio(base: Path, ep: str, shot_start: dict[str, float]) -> list[tuple[Path, float]]:
    """对白干声(可选):assets/audio/voice/<ep>/ 下以定稿镜号 shNNN 开头的音频,挂到该镜起点。"""
    vdir = base / "assets" / "audio" / "voice" / ep
    out = []
    if not vdir.is_dir():
        return out
    for f in sorted(vdir.rglob("*")):
        if f.suffix.lower() not in AUDIO_EXTS:
            continue
        m = re.match(r"^(sh\d+[a-z]?)", f.stem)
        if m and m.group(1) in shot_start:
            out.append((f, shot_start[m.group(1)]))
    return out


def main() -> int:
    def configure(ap):
        ap.add_argument("--fps", type=int, default=24)
        ap.add_argument("--no-audio", action="store_true", help="不混旁白/对白音轨(纯字幕样片)")
        ap.add_argument("--dry-run", action="store_true")
    args, root = parse_args("动态样片(草图串片)", configure=configure)
    ep = args.ep
    if not (root / "directing" / ep / "storyboard.json").is_file():
        print(f"MISSING directing/{ep}/storyboard.json")
        return 2
    board = sbb.load_board(root, ep)
    if not board["scenes"]:
        print("FAIL storyboard.json 没有场次")
        return 2
    sl = sbb._read_json(root / "directing" / ep / "shot_list.json") or {}
    idx = sbb.load_index(root, ep)
    aspect = sbb.resolve_aspect(root)
    size = _canvas_size(aspect)
    has_final = bool(sl.get("shots"))
    plan, t, missing = [], 0.0, 0
    shot_start: dict[str, float] = {}      # 定稿镜号 → 起始秒(音轨挂点用)
    inputs_mtime = max((root / "directing" / ep / f).stat().st_mtime for f in ("storyboard.json",)
                       if (root / "directing" / ep / f).is_file())
    if (root / "directing" / ep / "shot_list.json").is_file():
        inputs_mtime = max(inputs_mtime, (root / "directing" / ep / "shot_list.json").stat().st_mtime)
    for sc in board["scenes"]:
        for sh in sc["shots"]:
            finals = [f for f in sh["final"] if f.get("duration_s") is not None]
            if has_final and finals:
                dur = sum(float(f["duration_s"]) for f in finals)
                src = "final"
            else:
                dur = float(sh.get("duration_hint_s") or DEFAULT_SHOT_S)
                src = "draft"
            dur = max(0.5, dur)
            rec = idx["shots"].get(sh["key"]) or {}
            sk = root / rec["file"] if rec.get("status") == "done" and rec.get("file") else None
            if sk and sk.is_file():
                inputs_mtime = max(inputs_mtime, sk.stat().st_mtime)
            else:
                sk = None
                missing += 1
            off = 0.0
            for f in finals:
                shot_start[f["shot_id"]] = t + off
                off += float(f["duration_s"])
            subs = [("dialogue", f"{l.get('name') or l.get('speaker') or ''}:{l['text']}" if l.get('name') or l.get('speaker') else l['text'])
                    for l in sh.get("dialogue") or [] if l.get("text")]
            subs += [("narration", f"旁白 {n}") for n in (sh.get("narration_ref") or [])]
            plan.append({"key": sh["key"], "scene_no": sc["scene_no"], "order": sh["order"], "start_s": round(t, 2),
                         "duration_s": round(dur, 2), "has_sketch": bool(sk), "sketch": sk, "src": src,
                         "label": f"{sc['scene_no']} #{sh['order']}  {sh.get('size_hint') or ''}  {dur:g}s"
                                  + (f"  {finals[0]['shot_id']}" if finals else ""),
                         "content": sh.get("content") or "", "subs": subs})
            t += dur
    src_all = "final" if has_final and all(p["src"] == "final" for p in plan) else ("mixed" if has_final else "draft")
    out_dir = sbb.sketch_dir(root, ep)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "animatic.mp4"
    audio = [] if args.no_audio else _narration_audio(root, ep, sl, shot_start) + _dialogue_audio(root, ep, shot_start)
    if args.dry_run:
        print(f"[dry-run] {len(plan)} 镜 Σ{t:.1f}s 缺草图 {missing} 时长口径 {src_all} 画布 {size} 音轨 {len(audio)} → {out.relative_to(root)}")
        return 0
    if not shutil.which("ffmpeg"):
        print("FAIL 未找到 ffmpeg")
        return 1
    with tempfile.TemporaryDirectory(prefix="animatic_") as td:
        tdp = Path(td)
        lines = []
        for i, p in enumerate(plan):
            fp = tdp / f"f{i:04d}.png"
            render_frame(fp, p["sketch"], size, p["label"], p["content"], p["subs"])
            lines.append(f"file '{fp.as_posix()}'\nduration {p['duration_s']:.3f}")
        lines.append(f"file '{(tdp / f'f{len(plan) - 1:04d}.png').as_posix()}'")   # concat demuxer 末帧须重复
        (tdp / "list.txt").write_text("\n".join(lines) + "\n")
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(tdp / "list.txt")]
        for f, _ in audio:
            cmd += ["-i", str(f)]
        vf = f"fps={args.fps},scale={size[0]}:{size[1]}:flags=lanczos,format=yuv420p"
        if audio:
            parts = [f"[{k + 1}:a]adelay={int(st * 1000)}|{int(st * 1000)},apad[a{k}]" for k, (_, st) in enumerate(audio)]
            fc = ";".join(parts) + ";" + "".join(f"[a{k}]" for k in range(len(audio))) + f"amix=inputs={len(audio)}:normalize=0,atrim=0:{t:.3f}[aout]"
            cmd += ["-filter_complex", fc, "-map", "0:v", "-map", "[aout]", "-c:a", "aac", "-b:a", "128k"]
        cmd += ["-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-movflags", "+faststart",
                "-t", f"{t:.3f}", str(out)]   # concat 末帧重复条目会把末镜时长再加一次,按 Σ 截齐
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if r.returncode != 0 or not out.is_file():
            print(f"FAIL ffmpeg: {(r.stderr or '').strip()[-600:]}")
            return 1
    meta = {"schema": "animatic/1.0", "ep": ep, "file": out.relative_to(root).as_posix(), "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "duration_s": round(t, 2), "shots": len(plan), "missing_sketches": missing, "duration_source": src_all,
            "audio_tracks": len(audio), "fps": args.fps, "size": list(size), "inputs_mtime": int(inputs_mtime),
            "timeline": [{k: v for k, v in p.items() if k in ("key", "scene_no", "order", "start_s", "duration_s", "has_sketch", "src")} for p in plan]}
    (out_dir / "animatic.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    print(f"OK {out.relative_to(root)} {len(plan)} 镜 Σ{t:.1f}s 缺草图 {missing} 时长口径 {src_all} 音轨 {len(audio)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
