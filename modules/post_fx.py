# -*- coding: utf-8 -*-
"""post_fx.py — 后期处方的 ffmpeg 滤镜链构建与执行(供 code/post_apply.py 调用)。

约束(与 render_transitions.py 一致):本机 ffmpeg 无 drawtext/subtitles(无 libass/freetype),
文字类一律 PIL→PNG→overlay;视频编码统一 libx264 crf18 yuv420p +faststart;声轨流拷贝。
每种 ffmpeg 类处方对应一个 stage 生成器:输入标签 {in}、输出标签 {out},可带额外 -i 输入。
组内时间段作用域用 timeline enable='between(t,t0,t1)' 表达(不支持 timeline 的滤镜包在 split/blend 里,
enable 挂在 blend 上,关闭时 blend 直通第一路 = 原画面)。
"""
from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from pathlib import Path

ENC_VIDEO = ["-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
             "-color_range", "tv", "-movflags", "+faststart"]
ENC_PREVIEW = ["-c:v", "libx264", "-crf", "26", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
PREVIEW_SECONDS = 4.0
PREVIEW_HEIGHT = 480

LUT_CHAINS = {
    "teal_orange": "colorbalance=rs=-0.08:gs=-0.02:bs=0.12:rm=0.05:gm=0:bm=-0.03:rh=0.10:gh=0.03:bh=-0.10,eq=saturation=1.1:contrast=1.06",
    "warm_film": "colortemperature=temperature=5200,eq=contrast=1.04:saturation=0.95:gamma=1.03,colorbalance=rh=0.05:bh=-0.05",
    "cool_night": "colortemperature=temperature=8000,eq=contrast=1.08:saturation=0.85:brightness=-0.03,colorbalance=bs=0.10:rs=-0.05",
    "bleach": "eq=saturation=0.45:contrast=1.3:gamma=0.95",
    "vintage": "eq=saturation=0.7:contrast=0.9:brightness=0.03,colorbalance=rs=0.06:bs=-0.06:rh=0.04:gh=0.02,vignette=angle=PI/5",
}


class FxError(RuntimeError):
    pass


# ---------------------------------------------------------------- 工具
def require_tools(*names: str) -> None:
    missing = [n for n in names if shutil.which(n) is None]
    if missing:
        raise FxError(f"缺少外部工具 {missing};后期处方强依赖 ffmpeg/ffprobe,请先安装(macOS: brew install ffmpeg)")


def run(cmd: list[str], timeout: int = 1800, binary: bool = False):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if p.returncode != 0:
        err = (p.stderr or b"").decode("utf-8", "replace")
        raise FxError(f"命令失败({p.returncode}):{' '.join(map(str, cmd))}\n{err[-2000:]}")
    return p.stdout if binary else p.stdout.decode("utf-8", "replace")


def probe(path: Path) -> dict:
    """{width,height,fps,duration,has_audio,nb_frames}"""
    require_tools("ffprobe")
    out = run(["ffprobe", "-v", "error", "-show_entries",
               "stream=codec_type,width,height,r_frame_rate,duration,nb_frames:format=duration",
               "-of", "json", str(path)], timeout=60)
    j = json.loads(out)
    info = {"width": 0, "height": 0, "fps": 24.0, "duration": 0.0, "has_audio": False, "nb_frames": 0}
    for s in j.get("streams", []):
        if s.get("codec_type") == "video" and not info["width"]:
            info["width"], info["height"] = int(s.get("width") or 0), int(s.get("height") or 0)
            try:
                n, d = str(s.get("r_frame_rate") or "24/1").split("/")
                info["fps"] = float(n) / float(d)
            except Exception:
                info["fps"] = 24.0
            try:
                info["nb_frames"] = int(s.get("nb_frames") or 0)
            except ValueError:
                pass
        elif s.get("codec_type") == "audio":
            info["has_audio"] = True
    try:
        info["duration"] = float((j.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        info["duration"] = 0.0
    return info


def frame_stats(path: Path, t: float = 0.0, is_image: bool = False) -> dict:
    """t 秒处一帧的均值 RGB(0..1)与亮度标准差;图片直接读首帧。"""
    require_tools("ffmpeg")
    cmd = ["ffmpeg", "-v", "error"]
    if not is_image:
        cmd += ["-ss", f"{max(0.0, t):.3f}"]
    cmd += ["-i", str(path), "-frames:v", "1", "-vf", "scale=64:36", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    raw = run(cmd, timeout=120, binary=True)
    n = len(raw) // 3
    if n == 0:
        raise FxError(f"无法取帧:{path}")
    sr = sg = sb = 0
    lum = []
    for i in range(n):
        r, g, b = raw[3 * i], raw[3 * i + 1], raw[3 * i + 2]
        sr += r
        sg += g
        sb += b
        lum.append(0.299 * r + 0.587 * g + 0.114 * b)
    mean_l = sum(lum) / n
    std_l = math.sqrt(sum((x - mean_l) ** 2 for x in lum) / n)
    return {"r": sr / n / 255, "g": sg / n / 255, "b": sb / n / 255, "luma": mean_l / 255, "std": std_l / 255}


def palette_mean(colors: list[str]) -> dict | None:
    vals = [c.lstrip("#") for c in colors if re.fullmatch(r"#?[0-9a-fA-F]{6}", str(c))]
    if not vals:
        return None
    rs = [int(v[0:2], 16) for v in vals]
    gs = [int(v[2:4], 16) for v in vals]
    bs = [int(v[4:6], 16) for v in vals]
    n = len(vals)
    return {"r": sum(rs) / n / 255, "g": sum(gs) / n / 255, "b": sum(bs) / n / 255}


def extract_frame(src: Path, t: float, dst: Path) -> Path:
    require_tools("ffmpeg")
    dst.parent.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(src), "-frames:v", "1", str(dst)], timeout=120)
    return dst


def _esc_path(p: str) -> str:
    return str(p).replace("\\", "/").replace("'", r"\'").replace(":", r"\:")


def _en(scope: dict) -> str:
    if scope.get("level") == "range":
        return f":enable='between(t\\,{float(scope['t0']):.3f}\\,{float(scope['t1']):.3f})'"
    return ""


def _gain(src: float, dst: float, strength: float) -> float:
    if src <= 1e-4:
        return 1.0
    return max(0.6, min(1.5, 1.0 + strength * (dst / src - 1.0)))


def _rect(mask: dict | None, w: int, h: int) -> tuple[int, int, int, int] | None:
    """蒙版归一化框 {x,y,w,h}(0..1) → 像素矩形(偶数对齐,留 2px 边)。"""
    if not mask or not all(k in mask for k in ("x", "y", "w", "h")):
        return None
    x = max(2, int(float(mask["x"]) * w))
    y = max(2, int(float(mask["y"]) * h))
    rw = max(4, int(float(mask["w"]) * w))
    rh = max(4, int(float(mask["h"]) * h))
    rw = min(rw, w - x - 2)
    rh = min(rh, h - y - 2)
    if rw < 4 or rh < 4:
        return None
    return x // 2 * 2, y // 2 * 2, rw // 2 * 2, rh // 2 * 2


# ---------------------------------------------------------------- stage 生成器
class Ctx:
    """一次 apply 的上下文:源视频信息、参考解析、额外输入登记。"""

    def __init__(self, base: Path, src: Path, info: dict, lut_files: dict[str, str] | None = None):
        self.base = base
        self.src = src
        self.w, self.h, self.fps = int(info["width"]), int(info["height"]), float(info["fps"])
        self.duration = float(info.get("duration") or 0)
        self.inputs: list[str] = []          # 额外 -i(索引从 1 起)
        self.lut_files = lut_files or {}
        self.n = 0
        self.notes: list[str] = []

    def add_input(self, path: Path) -> int:
        self.inputs.append(str(path))
        return len(self.inputs)

    def label(self, tag: str) -> str:
        self.n += 1
        return f"{tag}{self.n}"

    def resolve(self, rel: str | None) -> Path | None:
        if not rel:
            return None
        p = Path(rel)
        p = p if p.is_absolute() else self.base / rel
        return p if p.is_file() else None


def stage_basic(r: dict, ctx: Ctx) -> str:
    p, en = r["params"], _en(r["scope"])
    parts = [f"eq=brightness={p['brightness']:.3f}:contrast={p['contrast']:.3f}:saturation={p['saturation']:.3f}:gamma={p['gamma']:.3f}{en}"]
    if abs(float(p.get("temperature", 6500)) - 6500) >= 50:
        parts.append(f"colortemperature=temperature={int(p['temperature'])}{en}")
    return "{in}" + ",".join(parts) + "{out}"


def _blend_wrap(chain: str, opacity: float, ctx: Ctx, en: str, mode: str = "normal") -> str:
    a, b, c = ctx.label("a"), ctx.label("b"), ctx.label("c")
    return f"{{in}}split[{a}][{b}];[{b}]{chain}[{c}];[{a}][{c}]blend=all_mode={mode}:all_opacity={opacity:.3f}{en}{{out}}"


def stage_lut(r: dict, ctx: Ctx) -> str:
    p, en = r["params"], _en(r["scope"])
    preset = str(p.get("preset") or "teal_orange")
    if preset in LUT_CHAINS:
        chain = LUT_CHAINS[preset]
    elif preset in ctx.lut_files:
        chain = f"lut3d=file='{_esc_path(ctx.lut_files[preset])}':interp=tetrahedral"
    else:
        raise FxError(f"未知 LUT 预设 {preset}")
    return _blend_wrap(chain, float(p.get("strength", 0.6)), ctx, en)


def stage_scene_palette(r: dict, ctx: Ctx) -> str:
    p, en = r["params"], _en(r["scope"])
    target = palette_mean(p.get("palette") or [])
    if not target:
        raise FxError("场次色板为空:请在处方里填色板或先让色彩脚本落盘")
    t_mid = ctx.duration / 2 if r["scope"].get("level") != "range" else (float(r["scope"]["t0"]) + float(r["scope"]["t1"])) / 2
    s = frame_stats(ctx.src, t_mid)
    k = float(p.get("strength", 0.5))
    rr, gg, bb = _gain(s["r"], target["r"], k), _gain(s["g"], target["g"], k), _gain(s["b"], target["b"], k)
    ctx.notes.append(f"palette gains r={rr:.3f} g={gg:.3f} b={bb:.3f}")
    parts = [f"colorchannelmixer=rr={rr:.4f}:gg={gg:.4f}:bb={bb:.4f}{en}"]
    if abs(float(p.get("saturation", 1)) - 1) > 1e-3:
        parts.append(f"eq=saturation={float(p['saturation']):.3f}{en}")
    return "{in}" + ",".join(parts) + "{out}"


def stage_match_ref(r: dict, ctx: Ctx) -> str:
    p, en = r["params"], _en(r["scope"])
    ref = ctx.resolve((r.get("refs") or {}).get("frame"))
    if not ref:
        raise FxError("参考帧匹配需要 refs.frame(先在监视器上「取参考帧」)")
    t_mid = ctx.duration / 2 if r["scope"].get("level") != "range" else (float(r["scope"]["t0"]) + float(r["scope"]["t1"])) / 2
    s, t = frame_stats(ctx.src, t_mid), frame_stats(ref, 0, is_image=True)
    k = float(p.get("strength", 0.8))
    rr, gg, bb = _gain(s["r"], t["r"], k), _gain(s["g"], t["g"], k), _gain(s["b"], t["b"], k)
    parts = [f"colorchannelmixer=rr={rr:.4f}:gg={gg:.4f}:bb={bb:.4f}{en}"]
    if p.get("match_contrast") and s["std"] > 1e-4:
        c = max(0.6, min(1.6, 1.0 + k * (t["std"] / s["std"] - 1.0)))
        parts.append(f"eq=contrast={c:.3f}{en}")
        ctx.notes.append(f"match contrast={c:.3f}")
    ctx.notes.append(f"match gains r={rr:.3f} g={gg:.3f} b={bb:.3f}")
    return "{in}" + ",".join(parts) + "{out}"


def stage_atmos(r: dict, ctx: Ctx) -> str:
    p, en = r["params"], _en(r["scope"])
    out = "{in}"
    glow = float(p.get("glow", 0))
    if glow > 0:
        a, b, c = ctx.label("a"), ctx.label("b"), ctx.label("c")
        sigma = max(8, int(ctx.h / 40))
        out += f"split[{a}][{b}];[{b}]gblur=sigma={sigma},eq=brightness=-0.15:contrast=1.4[{c}];[{a}][{c}]blend=all_mode=screen:all_opacity={glow * 0.8:.3f}{en},"
    chain = []
    tint = str(p.get("tint") or "none")
    temp = {"warm": 5200, "cool": 8000, "candle": 4300}.get(tint)
    if temp:
        chain.append(f"colortemperature=temperature={temp}{en}")
        if tint == "candle":
            chain.append(f"eq=saturation=1.05:gamma=0.97{en}")
    fl = float(p.get("flicker", 0))
    if fl > 0:
        chain.append(f"eq=brightness='{0.05 * fl:.4f}*sin(2*PI*t*9)+{0.025 * fl:.4f}*sin(2*PI*t*23)':eval=frame{en}")
    vg = float(p.get("vignette", 0))
    if vg > 0:
        chain.append(f"vignette=angle=PI/4*{0.45 + 0.55 * vg:.3f}{en}")
    gr = float(p.get("grain", 0))
    if gr > 0:
        chain.append(f"noise=alls={int(6 + 34 * gr)}:allf=t+u{en}")
    if not chain and glow <= 0:
        chain.append("null")
    out += ",".join(chain) if chain else "null"
    return out + "{out}"


def stage_deflicker(r: dict, ctx: Ctx) -> str:
    p, en = r["params"], _en(r["scope"])
    size = int(p.get("size", 5))
    return _blend_wrap(f"deflicker=size={size}:mode=am", float(p.get("strength", 0.8)), ctx, en)


def stage_delogo(r: dict, ctx: Ctx) -> str:
    rect = _rect((r.get("refs") or {}).get("mask"), ctx.w, ctx.h)
    if not rect:
        raise FxError("遮标去除需要矩形蒙版 refs.mask(在监视器上画框)")
    x, y, w, h = rect
    return "{in}" + f"delogo=x={x}:y={y}:w={w}:h={h}{_en(r['scope'])}" + "{out}"


def stage_overlay_asset(r: dict, ctx: Ctx) -> str:
    p, sc = r["params"], r["scope"]
    asset = ctx.resolve((r.get("refs") or {}).get("asset"))
    if not asset:
        raise FxError("叠加素材需要 refs.asset(项目内素材路径,如 assets/post/fx/sparks.mp4)")
    idx = ctx.add_input(asset)
    t0 = float(sc.get("t0") or 0) if sc.get("level") == "range" else 0.0
    op = float(p.get("opacity", 0.8))
    mode = str(p.get("blend") or "screen")
    rect = _rect((r.get("refs") or {}).get("mask"), ctx.w, ctx.h) if p.get("fit") == "mask" else None
    fx = ctx.label("fx")
    en = _en(sc)
    if mode == "normal":
        geo = f"scale={rect[2]}:{rect[3]}" if rect else f"scale={ctx.w}:{ctx.h}"
        pos = f"x={rect[0]}:y={rect[1]}" if rect else "x=0:y=0"
        return (f"[{idx}:v]{geo},format=rgba,colorchannelmixer=aa={op:.3f},setpts=PTS+{t0:.3f}/TB[{fx}];"
                f"{{in}}[{fx}]overlay={pos}:eof_action=pass:format=auto{en}{{out}}")
    if rect:
        geo = f"scale={rect[2]}:{rect[3]},pad={ctx.w}:{ctx.h}:{rect[0]}:{rect[1]}:black"
    else:
        geo = f"scale={ctx.w}:{ctx.h}"
    return (f"[{idx}:v]{geo},format=yuv420p,fps={ctx.fps:g},"
            f"tpad=start_duration={t0:.3f}:start_mode=add:start_color=black:stop=-1:stop_mode=add:stop_color=black[{fx}];"
            f"{{in}}[{fx}]blend=all_mode={mode}:all_opacity={op:.3f}:shortest=1{en}{{out}}")


def stage_watermark(r: dict, ctx: Ctx) -> str:
    p = r["params"]
    asset = ctx.resolve((r.get("refs") or {}).get("asset"))
    if not asset:
        raise FxError("水印需要 refs.asset(PNG 角标)")
    idx = ctx.add_input(asset)
    wpx = max(16, int(ctx.w * float(p.get("scale", 10)) / 100)) // 2 * 2
    m = int(p.get("margin", 24))
    pos = {"top_right": f"x=W-w-{m}:y={m}", "top_left": f"x={m}:y={m}",
           "bottom_right": f"x=W-w-{m}:y=H-h-{m}", "bottom_left": f"x={m}:y=H-h-{m}"}[str(p.get("position") or "top_right")]
    wm = ctx.label("wm")
    return (f"[{idx}:v]scale={wpx}:-2,format=rgba,colorchannelmixer=aa={float(p.get('opacity', 0.7)):.3f}[{wm}];"
            f"{{in}}[{wm}]overlay={pos}:format=auto{{out}}")


STAGES = {"basic": stage_basic, "lut": stage_lut, "scene_palette": stage_scene_palette, "match_ref": stage_match_ref,
          "atmos": stage_atmos, "deflicker": stage_deflicker, "delogo": stage_delogo,
          "overlay_asset": stage_overlay_asset, "watermark": stage_watermark}
FFMPEG_KINDS = set(STAGES)


def build_graph(recipes: list[dict], ctx: Ctx, tail: str = "") -> str:
    """把若干 ffmpeg 类处方串成一条 filter_complex;返回图文本,输出标签固定 [vout]。"""
    parts = ["[0:v]format=yuv420p[v0]"]
    cur = "v0"
    for i, r in enumerate(recipes):
        gen = STAGES.get(r["kind"])
        if not gen:
            raise FxError(f"{r['kind']} 不是 ffmpeg 类处方")
        nxt = f"v{i + 1}"
        snippet = gen(r, ctx).replace("{in}", f"[{cur}]").replace("{out}", f"[{nxt}]")
        parts.append(snippet)
        cur = nxt
    parts.append(f"[{cur}]{tail or 'null'}[vout]")
    return ";".join(parts)


def apply_graph(src: Path, dst: Path, graph: str, ctx: Ctx, preview: bool = False,
                preview_from: float = 0.0, keep_audio: bool = True) -> None:
    """执行滤镜链;preview=True 出前 4 秒 480p 低清(range 作用域从 t0 起)。"""
    require_tools("ffmpeg")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.stem + ".rendering" + dst.suffix)
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src)]
    for extra in ctx.inputs:
        cmd += ["-i", extra]
    cmd += ["-filter_complex", graph, "-map", "[vout]"]
    if keep_audio:
        cmd += ["-map", "0:a?", "-c:a", "copy"]
    else:
        cmd += ["-an"]
    if preview:
        cmd += ["-ss", f"{max(0.0, preview_from):.3f}", "-t", f"{PREVIEW_SECONDS:.1f}"] + ENC_PREVIEW
    else:
        cmd += ENC_VIDEO
    cmd += [str(tmp)]
    try:
        run(cmd, timeout=3600)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    if dst.exists():
        dst.unlink()
    tmp.rename(dst)


def preview_scale_tail() -> str:
    return f"scale=-2:{PREVIEW_HEIGHT}"


# ---------------------------------------------------------------- 整集拼装(build-cut)
def normalize_segment(src: Path, dst: Path, w: int, h: int, fps: float, t_in: float, t_out: float) -> int:
    """把一段素材归一到 w×h/fps/yuv420p,精确 N 帧(短则末帧克隆补齐),纯视频(正片只承担画面,
    声轨由 assets/audio/final/epNN.wav 在终版封装时外挂;带 AAC 拼接会让视频流起始偏移一个 priming 延迟)。返回帧数。"""
    require_tools("ffmpeg")
    n = max(1, int(round((t_out - t_in) * fps)))
    vf = (f"trim=start={t_in:.6f}:end={t_out:.6f},setpts=PTS-STARTPTS,"
          f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,"
          f"setsar=1,fps={fps:g},format=yuv420p,tpad=stop=-1:stop_mode=clone")
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", vf, "-frames:v", str(n), "-an",
           "-fps_mode", "passthrough"] + ENC_VIDEO + [str(dst)]
    run(cmd, timeout=3600)
    return n


def concat_segments(files: list[Path], dst: Path) -> None:
    """concat demuxer 流拷贝(各段已归一)。"""
    require_tools("ffmpeg")
    lst = dst.with_name("." + dst.stem + ".concat.txt")
    lst.write_text("".join(f"file '{str(f).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'\n" for f in files), encoding="utf-8")
    tmp = dst.with_name(dst.stem + ".rendering" + dst.suffix)
    try:
        run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
             "-movflags", "+faststart", str(tmp)], timeout=3600)
        if dst.exists():
            dst.unlink()
        tmp.rename(dst)
    finally:
        lst.unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)
