#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""burn_subtitles.py — 字幕烧录宿主 CLI:把成片基准字幕 subtitles_final.* 烧进 final.mp4 的副本 + 机检 subtitle_style_ok。

背景(2026-10-09,issue #133):项目设置「内嵌字幕」(output.subtitle_burn_in)开启时,成片终稿要内嵌字幕。以前规约让 edit
自己用 ffmpeg subtitles / ass 滤镜烧录,但宿主环境不保证 ffmpeg 带 libass(macOS Homebrew 默认 bottle 没有 ass / subtitles /
drawtext 三个滤镜),edit 只能在项目 code/ 下自写替代脚本,一项目一格式、字号边距无从机检。本 CLI 与白模样片同一路线:
PIL 把每段在屏字幕画成整幅透明 PNG(字体查找 / 折行复用 modules/whitebox_subtitles.py),concat 按时长排片作第二路输入
overlay 到成片上——不依赖 libass,各机器出图一致。

  burn   读 edit/epNN/subtitles_final[.<lang>].srt(或同名 .ass;只认成片基准版,正片基准 subtitles.srt 一律拒绝)
         → 烧进 edit/epNN/final.mp4 的副本 final_sub[.<lang>].mp4(干净版 final.mp4 永不改动;画面重编码 crf 18,
         **全部音轨流拷贝**);烧完自动 check
  check  机检 subtitle_style_ok(FAIL 即不交付):
           subs_source_ok        字幕是成片基准版;末条不超出成片(+0.5s);片头偏移 >0 而字幕与正片基准版逐条相同 = 未平移(FAIL);
                                 字幕文件早于成片 → WARN(可能是旧版,请重跑 finalize_episode.py shift)
           style_lines_le_2      每段在屏字幕 ≤2 行(超了先自动缩字号到 80%,仍超 = FAIL,请 subtitle 重新切分)
           style_char_height     单行字符高(参考字形「国Hgjy」墨迹高)≤ 画面高 4%
           style_bottom_margin   末行下边距 2%–4% 画面高
           style_band_height     字幕区总高(含描边)≤ 画面高 12%
           duration_match        烧录版时长 = 源成片 ±1 帧
           audio_stream_copy     音轨条数 / 编码与源成片一致(流拷贝)
           first_cue_frame       首句中点抽帧:字幕区以外与源成片一致(MAD ≤3)、字幕区有字(MAD ≥1.5);
                                 字幕间隙(≥0.5s)抽一帧整幅与源一致(字幕没有拖尾)
         台账 edit/epNN/subtitles_burn.json(按输出文件名记源 / 字幕 sha / 字号版式 / check 结果)

样式(subtitle SOUL 职责 5 是唯一权威):底部居中;白字 + 黑描边(描边 ≈ 字号 8%),无底板;字符高 = min(画面高 4%, 画面宽 5%)
(竖屏按宽收紧,免得一行只放得下十来个字;--char-pct 只能往下调);下边距默认 3%(--margin-pct 2–4);两行时行距自动压到
字幕区 ≤12%。字体:--font > data/fonts/ > 系统中文字体(与白模样片同一查找顺序);非中日英文字请用 --font 指定覆盖该文字的字体。
上下黑边开启时在加完黑边的成片画布上烧(字幕落在画布底部)。

用法:
  python3 code/burn_subtitles.py burn  --project <slug> --ep epNN [--lang en] [--src final.mp4] [--out final_sub.mp4]
                                       [--char-pct 4] [--margin-pct 3] [--font <ttf/otf/ttc>] [--crf 18] [--preset medium]
  python3 code/burn_subtitles.py check --project <slug> --ep epNN [--lang en] [--src final.mp4] [--out final_sub.mp4]
退出码:全 PASS=0,任一 FAIL=1(WARN 不影响退出码)。
"""
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from _common import parse_args  # 副作用:modules/ 入 sys.path
from avsync import probe_duration, require_tools
from whitebox_subtitles import _segments, find_font, wrap_text

LEDGER = "subtitles_burn.json"
DEFAULT_SRC = "final.mp4"
REF_GLYPHS = "国Hgjy"       # 字符高参考:CJK 全高字 + 拉丁升部 / 降部
MAX_LINES = 2
CHAR_PCT_MAX = 4.0          # subtitle SOUL 职责 5:单行字符高 ≤ 画面高 4%
CHAR_W_PCT = 5.0            # 竖屏按宽收紧:字符高 ≤ 画面宽 5%
BAND_PCT_MAX = 12.0         # 两行时字幕区总高 ≤ 画面高 12%
MARGIN_PCT = (2.0, 4.0)     # 下边距 2%–4%
LINE_PITCH = 1.25           # 行距 = 字号 × 1.25(超 12% 时压到 1.12)
WIDTH_FRAC = 0.9            # 每行最宽 = 画面宽 90%
SHRINK = (0.9, 0.8)         # 一段折行超 2 行时依次缩到 90% / 80% 字号再试
_LANG_RE = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$")


# ---------------------------------------------------------------- 基础

def _run(cmd, timeout=3600, binary=False):
    p = subprocess.run(cmd, capture_output=True, text=not binary, timeout=timeout)
    if p.returncode != 0:
        err = p.stderr if isinstance(p.stderr, str) else (p.stderr or b"").decode("utf-8", "replace")
        raise RuntimeError(f"命令失败({p.returncode}):{' '.join(map(str, cmd))}\n{err[-2000:]}")
    return p.stdout


def _streams(path):
    out = _run(["ffprobe", "-v", "error", "-show_entries", "stream=index,codec_type,codec_name,width,height,r_frame_rate",
                "-of", "json", str(path)], timeout=60)
    return json.loads(out).get("streams") or []


def _fps(v):
    try:
        n, d = (v.get("r_frame_rate") or "24/1").split("/")
        return float(n) / float(d)
    except (ValueError, ZeroDivisionError):
        return 24.0


def _video_packets(path):
    """视频流帧数(按包计数,不解码)。"""
    out = _run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries", "stream=nb_read_packets",
                "-of", "csv=p=0", str(path)], timeout=300).strip().split(",")[0]
    return int(out) if out.isdigit() else None


def _sha(path, limit=1 << 24):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read(limit))
    return h.hexdigest()[:16]


def _s(t):
    h, m, rest = t.replace(",", ".").split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


_SRT_TIME = re.compile(r"^\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})")
_TAGS = re.compile(r"</?[ibus]>|<font[^>]*>|</font>|\{[^}]*\}")


def parse_subs(path):
    """SRT / ASS → [{start, end, text}](text 内换行保留;去 <i>/{\\an8} 等标签)。"""
    text = Path(path).read_text(encoding="utf-8-sig")
    cues = []
    if Path(path).suffix.lower() == ".ass":
        for line in text.splitlines():
            if not line.startswith("Dialogue:"):
                continue
            parts = line.split(":", 1)[1].split(",", 9)
            if len(parts) < 10:
                continue
            body = _TAGS.sub("", parts[9].replace("\\N", "\n").replace("\\n", "\n")).strip()
            if body:
                cues.append({"start": _s(parts[1].strip()), "end": _s(parts[2].strip()), "text": body})
        return sorted(cues, key=lambda c: c["start"])
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        lines = [ln for ln in block.split("\n") if ln.strip()]
        for i, ln in enumerate(lines):
            m = _SRT_TIME.match(ln)
            if m:
                body = _TAGS.sub("", "\n".join(lines[i + 1:])).strip()
                if body:
                    cues.append({"start": _s(m.group(1)), "end": _s(m.group(2)), "text": body})
                break
    return cues


def _subs_file(ed, lang):
    """成片基准字幕:subtitles_final[.<lang>|_<lang>].srt 优先,其次同名 .ass;返回 (路径或 None, 正片基准对应文件或 None)。"""
    stems = ["subtitles_final"] if not lang else [f"subtitles_final.{lang}", f"subtitles_final_{lang}"]
    for stem in stems:
        for ext in (".srt", ".ass"):
            p = ed / f"{stem}{ext}"
            if p.is_file():
                base = ed / (stem.replace("subtitles_final", "subtitles", 1) + ext)
                return p, (base if base.is_file() else None)
    return None, None


def _out_name(lang, out):
    if out:
        return out
    return f"final_sub.{lang}.mp4" if lang else "final_sub.mp4"


def _cut_offset(ed):
    try:
        return float(json.loads((ed / "final_layout.json").read_text(encoding="utf-8")).get("cut_offset_s") or 0.0)
    except Exception:  # noqa: BLE001
        return 0.0


# ---------------------------------------------------------------- 版式

def _font(font_path, size):
    if font_path:
        from PIL import ImageFont
        return ImageFont.truetype(str(font_path), size)
    return find_font(size)


def _ref_box(font):
    return font.getbbox(REF_GLYPHS)          # (l, t, r, b),相对绘制原点


class Layout:
    """一集字幕的统一版式:字号 / 行距 / 下边距按画面尺寸一次定好,逐段只折行(超 2 行时该段缩字号)。"""

    def __init__(self, width, height, char_pct=CHAR_PCT_MAX, margin_pct=3.0, font_path=None):
        self.W, self.H, self.font_path = int(width), int(height), font_path
        self.margin = round(self.H * margin_pct / 100.0)
        self.char_cap = min(self.H * min(char_pct, CHAR_PCT_MAX) / 100.0, self.W * CHAR_W_PCT / 100.0)
        size = max(8, int(self.char_cap * 1.3))
        while size > 8 and self._char_h(size) > self.char_cap:
            size -= 1
        self.pitch_k = LINE_PITCH
        # 两行时字幕区(下边距 + 一行行距 + 字符高 + 描边)≤ 12%:先压行距,再降字号
        while size > 8 and self._band2(size) > self.H * BAND_PCT_MAX / 100.0:
            if self.pitch_k > 1.12:
                self.pitch_k = 1.12
            else:
                size -= 1
        self.size = size
        self.font = _font(font_path, size)
        self.font_desc = getattr(self.font, "path", None) or "PIL 默认字体"

    def _char_h(self, size):
        l, t, r, b = _ref_box(_font(self.font_path, size))
        return b - t

    def _stroke(self, size):
        return max(2, round(size * 0.08))

    def _band2(self, size):
        return self.margin + round(size * self.pitch_k) + self._char_h(size) + 2 * self._stroke(size)

    def rows(self, draw, texts, font):
        out = []
        for text in texts:
            for src_line in str(text).split("\n"):
                if src_line.strip():
                    out += wrap_text(draw, src_line.strip(), font, int(self.W * WIDTH_FRAC))
        return out

    def render(self, path, texts):
        """一段在屏字幕(texts:同时在屏的各条)→ 整幅透明 PNG;返回版式实测。"""
        from PIL import Image, ImageDraw
        im = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        meta = {"lines": 0, "size": self.size}
        if texts:
            d = ImageDraw.Draw(im)
            font, size = self.font, self.size
            rows = self.rows(d, texts, font)
            for k in SHRINK:
                if len(rows) <= MAX_LINES:
                    break
                size = max(8, int(self.size * k))
                font = _font(self.font_path, size)
                rows = self.rows(d, texts, font)
            l, t, r, b = _ref_box(font)
            stroke, pitch = self._stroke(size), round(size * self.pitch_k)
            for i, ln in enumerate(reversed(rows)):
                ink_bottom = self.H - self.margin - i * pitch          # 参考字形墨迹底(降部)落在这条线上
                x = (self.W - d.textlength(ln, font=font)) / 2
                d.text((x, ink_bottom - b), ln, font=font, fill=(255, 255, 255, 255), stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
            bbox = im.getbbox()
            meta = {"lines": len(rows), "size": size, "char_h_px": b - t, "stroke_px": stroke, "pitch_px": pitch,
                    "margin_px": self.margin, "band_top_px": bbox[1] if bbox else self.H, "band_bottom_px": bbox[3] if bbox else self.H}
        im.save(path, "PNG")
        return meta


def build_track(cues, layout, total_s, staging):
    """切成互不重叠的在屏时段,逐段出 PNG + concat 清单;返回 (清单路径, 各段版式 [(a, b, texts, meta)])。"""
    staging.mkdir(parents=True, exist_ok=True)
    segs = _segments([{"start": c["start"], "end": c["end"], "kind": "sub", "text": c["text"]} for c in cues], total_s)
    if not segs:
        segs = [(0.0, max(total_s, 0.1), ())]
    files, metas, lines = {}, [], []
    for a, b, active in segs:
        texts = tuple(t for _, t in active)
        if texts not in files:
            p = staging / f"sub{len(files):04d}.png"
            files[texts] = (p, layout.render(p, texts))
        p, meta = files[texts]
        if texts:
            metas.append((a, b, texts, meta))
        lines.append(f"file '{p.as_posix()}'\nduration {b - a:.6f}")
    lines.append(f"file '{files[tuple(t for _, t in segs[-1][2])][0].as_posix()}'")   # concat demuxer 末条须重复才按时长收尾
    listing = staging / "subs.txt"
    listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return listing, metas


# ---------------------------------------------------------------- burn / check

def _gray(path, t, w, h):
    return _run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.6f}", "-i", str(path), "-frames:v", "1",
                 "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "gray", "-"], timeout=120, binary=True)


def _mad(a, b, rows=None, w=None):
    """平均绝对差;rows=(r0, r1) 只比这些行(w = 每行像素数)。"""
    if rows:
        a, b = a[rows[0] * w:rows[1] * w], b[rows[0] * w:rows[1] * w]
    n = min(len(a), len(b))
    return sum(abs(a[i] - b[i]) for i in range(n)) / n if n else 999.0


def source_problems(ed, src, subs, base_subs):
    """字幕源核对 → (problems, warns, cues, 源成片时长)。burn 前先跑,有 problem 不烧。"""
    cues = parse_subs(subs)
    sdur = probe_duration(src)
    problems, warns = [], []
    if not cues:
        problems.append(f"{subs.name} 无 cue")
    elif max(c["end"] for c in cues) > sdur + 0.5:
        problems.append(f"末条止于 {max(c['end'] for c in cues):.3f}s 超出成片 {sdur:.3f}s(字幕基准不对,回 edit 重跑 shift)")
    off = _cut_offset(ed)
    if base_subs is not None and off > 0.05 and cues:
        base = parse_subs(base_subs)
        if len(base) == len(cues) and all(abs(x["start"] - y["start"]) < 1e-3 for x, y in zip(base, cues)):
            problems.append(f"片头偏移 {off:.3f}s 而 {subs.name} 与正片基准 {base_subs.name} 逐条相同 = 未平移(先跑 finalize_episode.py shift)")
    if subs.stat().st_mtime < src.stat().st_mtime - 2:
        warns.append(f"{subs.name} 早于 {src.name}(成片重封装后字幕没重跑 shift?)")
    return problems, warns, cues, sdur


def do_check(proj, ep, src, out, subs, base_subs, layout_args, write=True):
    ed = proj / "edit" / ep
    results = []

    def rec(name, ok, detail="", warn=False):
        tag = "PASS" if ok else ("WARN" if warn else "FAIL")
        results.append({"check": name, "result": tag, "detail": detail})
        print(f"[{tag:<5}] {name}: {detail}")
        return ok

    if not src.is_file():
        rec("subs_source_ok", False, f"缺源成片 {src.name}(先跑 finalize_episode.py assemble)")
        return False, results
    if subs is None:
        rec("subs_source_ok", False, "缺成片基准字幕 subtitles_final*.srt/.ass(先跑 finalize_episode.py shift;正片基准 subtitles.srt 不得直接烧)")
        return False, results
    problems, warns, cues, sdur = source_problems(ed, src, subs, base_subs)
    vs = next((s for s in _streams(src) if s.get("codec_type") == "video"), {})
    W, H, fps = int(vs.get("width") or 0), int(vs.get("height") or 0), _fps(vs)
    rec("subs_source_ok", not problems, f"{subs.name} {len(cues)} 条 / {src.name} {sdur:.3f}s" + (";" + ";".join(problems) if problems else ""))
    if warns:
        rec("subs_source_fresh", False, ";".join(warns), warn=True)
    if not cues:
        return False, results

    # 版式:重渲一遍字幕带(与 burn 同一函数,确定性),按实测判
    lay = Layout(W, H, **layout_args)
    tmp = Path(tempfile.mkdtemp(prefix="va-burnchk-"))
    try:
        _, metas = build_track(cues, lay, sdur, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    over = [f"{a:.2f}s({m['lines']} 行)" for a, b, t, m in metas if m["lines"] > MAX_LINES]
    rec("style_lines_le_2", not over, f"{len(metas)} 段在屏字幕" + (f";超 2 行 {over[:6]}(请 subtitle 重新切分)" if over else ""))
    ch = max(m["char_h_px"] for *_, m in metas)
    rec("style_char_height", ch <= H * CHAR_PCT_MAX / 100.0 + 0.5,
        f"字号 {lay.size}px,字符高 {ch}px = {ch / H * 100:.2f}% 画面高(上限 {CHAR_PCT_MAX:g}%;字体 {Path(str(lay.font_desc)).name})")
    mp = lay.margin / H * 100
    rec("style_bottom_margin", MARGIN_PCT[0] - 1e-6 <= mp <= MARGIN_PCT[1] + 1e-6, f"下边距 {lay.margin}px = {mp:.2f}%(须 {MARGIN_PCT[0]:g}%–{MARGIN_PCT[1]:g}%)")
    bmax = max((H - m["band_top_px"]) for *_, m in metas)
    rec("style_band_height", bmax <= H * BAND_PCT_MAX / 100.0 + 0.5, f"字幕区最高 {bmax}px = {bmax / H * 100:.2f}%(上限 {BAND_PCT_MAX:g}%,含描边)")

    if not out.is_file():
        rec("duration_match", False, f"缺烧录版 {out.name}(先跑 burn)")
        return False, results
    odur = probe_duration(out)
    sn, on = _video_packets(src), _video_packets(out)
    rec("duration_match", abs(odur - sdur) <= 1.0 / fps + 0.005 and (sn is None or on == sn),
        f"{out.name} {odur:.3f}s / {on} 帧;{src.name} {sdur:.3f}s / {sn} 帧")
    sa = [s.get("codec_name") for s in _streams(src) if s.get("codec_type") == "audio"]
    oa = [s.get("codec_name") for s in _streams(out) if s.get("codec_type") == "audio"]
    rec("audio_stream_copy", sa == oa, f"音轨 源 {sa} / 烧录版 {oa}")
    w, h = max(16, W // 4), max(16, H // 4)
    a, b, texts, m = metas[0]
    t = (a + b) / 2
    x, y = _gray(out, t, w, h), _gray(src, t, w, h)
    top_rows = int(h * (m["band_top_px"] - 2 * m["stroke_px"]) / H)
    outside, inside = _mad(x, y, (0, max(1, top_rows)), w), _mad(x, y, (top_rows, h), w)
    ok = outside <= 3.0 and inside >= 1.5
    detail = f"首句 @{t:.2f}s:字幕区外差 {outside:.2f}(≤3)、字幕区差 {inside:.2f}(≥1.5)"
    gap = next(((p_b + n_a) / 2 for (_, p_b, *_), (n_a, *_) in zip(metas, metas[1:]) if n_a - p_b >= 0.5), None)
    if gap is not None:
        g = _mad(_gray(out, gap, w, h), _gray(src, gap, w, h))
        ok = ok and g <= 3.0
        detail += f";间隙 @{gap:.2f}s 整幅差 {g:.2f}(≤3)"
    rec("first_cue_frame", ok, detail)

    passed = all(r["result"] != "FAIL" for r in results)
    print(f"[{'PASS' if passed else 'FAIL'} ] subtitle_style_ok:{len(results)} 项")
    if write:
        p = ed / LEDGER
        try:
            led = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
        except ValueError:
            led = {}
        led.setdefault("outputs", {})[out.name] = {
            "src": f"edit/{ep}/{src.name}", "subs": f"edit/{ep}/{subs.name}", "subs_sha": _sha(subs),
            "src_sha": _sha(src), "font": str(lay.font_desc), "font_px": lay.size, "margin_px": lay.margin,
            "size": [W, H], "cues": len(cues),
            "check": {"name": "subtitle_style_ok", "result": "PASS" if passed else "FAIL", "items": results,
                      "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}}
        led["file"] = f"edit/{ep}/{LEDGER}"
        led["cli"] = "code/burn_subtitles.py"
        p.write_text(json.dumps(led, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return passed, results


def do_burn(proj, ep, src, out, subs, layout_args, crf=18, preset="medium"):
    cues = parse_subs(subs)
    if not cues:
        raise SystemExit(f"[FAIL] {subs.name} 无 cue")
    sdur = probe_duration(src)
    vs = next((s for s in _streams(src) if s.get("codec_type") == "video"), {})
    lay = Layout(int(vs["width"]), int(vs["height"]), **layout_args)
    staging = Path(tempfile.mkdtemp(prefix="va-burn-"))
    tmp_out = out.with_name(out.stem + ".tmp" + out.suffix)
    try:
        listing, metas = build_track(cues, lay, sdur, staging / "subs")
        print(f"[INFO ] {len(cues)} 条 cue → {len(metas)} 段在屏字幕;字号 {lay.size}px、下边距 {lay.margin}px、字体 {lay.font_desc}")
        n = _video_packets(src)     # 字幕带清单末条重复一帧,overlay 会多吐一帧:按源帧数截断,时长与源逐帧一致
        _run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-f", "concat", "-safe", "0", "-i", str(listing),
              "-filter_complex", "[0:v][1:v]overlay=0:0:format=auto:eof_action=repeat,format=yuv420p[v]",
              "-map", "[v]", "-map", "0:a?"] + (["-frames:v", str(n)] if n else []) + ["-c:v", "libx264", "-crf", str(crf), "-preset", preset,
              "-c:a", "copy", "-map_metadata", "0", "-movflags", "+faststart", str(tmp_out)])
        tmp_out.replace(out)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        if tmp_out.exists():
            tmp_out.unlink()
    print(f"[DONE ] {out.relative_to(proj)}(源 {src.name} 不动)")


def main(argv=None):
    def configure(ap):
        ap.add_argument("cmd", choices=("burn", "check"))
        ap.add_argument("--lang", default=None, help="附加语言版本:读 subtitles_final.<lang>.srt(或 subtitles_final_<lang>.srt),默认产 final_sub.<lang>.mp4")
        ap.add_argument("--src", default=DEFAULT_SRC, help=f"源成片(干净版),默认 {DEFAULT_SRC};不会被改动")
        ap.add_argument("--out", default=None, help="烧录版文件名(须含 final、不得与源同名),默认 final_sub[.<lang>].mp4")
        ap.add_argument("--char-pct", type=float, default=CHAR_PCT_MAX, help=f"字符高占画面高 %%,只能 ≤{CHAR_PCT_MAX:g}(竖屏另受画面宽 {CHAR_W_PCT:g}%% 约束)")
        ap.add_argument("--margin-pct", type=float, default=3.0, help="下边距占画面高 %%,2–4,默认 3")
        ap.add_argument("--font", default=None, help="字体文件(默认 data/fonts/ > 系统中文字体)")
        ap.add_argument("--crf", type=int, default=18)
        ap.add_argument("--preset", default="medium")

    args, proj = parse_args(__doc__.splitlines()[0], configure=configure, argv=argv)
    require_tools("ffmpeg", "ffprobe")
    if args.lang and not _LANG_RE.match(args.lang):
        raise SystemExit(f"[FAIL] --lang 须是语言代码(如 en / zh / pt-BR):{args.lang!r}")
    if not (0 < args.char_pct <= CHAR_PCT_MAX):
        raise SystemExit(f"[FAIL] --char-pct 须在 (0, {CHAR_PCT_MAX:g}](subtitle SOUL 职责 5 上限)")
    if not (MARGIN_PCT[0] <= args.margin_pct <= MARGIN_PCT[1]):
        raise SystemExit(f"[FAIL] --margin-pct 须在 {MARGIN_PCT[0]:g}–{MARGIN_PCT[1]:g}")
    if args.font and not Path(args.font).is_file():
        raise SystemExit(f"[FAIL] 字体文件不存在:{args.font}")
    ed = proj / "edit" / args.ep
    src = ed / args.src
    out = ed / _out_name(args.lang, args.out)
    if "final" not in out.name or out.name == src.name or out.name == DEFAULT_SRC:
        raise SystemExit(f"[FAIL] 烧录版文件名须含 final 且不得覆盖源成片 / final.mp4:{out.name}")
    subs, base_subs = _subs_file(ed, args.lang)
    layout_args = {"char_pct": args.char_pct, "margin_pct": args.margin_pct, "font_path": args.font}
    if args.cmd == "burn":
        if not src.is_file():
            raise SystemExit(f"[FAIL] 缺源成片 {src.relative_to(proj)}(先跑 finalize_episode.py assemble)")
        if subs is None:
            raise SystemExit(f"[FAIL] 缺成片基准字幕 edit/{args.ep}/subtitles_final{'.' + args.lang if args.lang else ''}.srt"
                             "(先跑 finalize_episode.py shift;正片基准 subtitles.srt 不得直接烧)")
        problems, warns, _, _ = source_problems(ed, src, subs, base_subs)
        if problems:
            raise SystemExit("[FAIL] subs_source_ok:" + ";".join(problems) + "(未烧录)")
        for w in warns:
            print(f"[WARN ] {w}")
        do_burn(proj, args.ep, src, out, subs, layout_args, crf=args.crf, preset=args.preset)
    ok, _ = do_check(proj, args.ep, src, out, subs, base_subs, layout_args)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
