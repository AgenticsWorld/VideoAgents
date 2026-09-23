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


def stage_soften(r: dict, ctx: Ctx) -> str:
    """柔化:高斯模糊与原片按强度混合(压高频不糊轮廓);半径以 1080p 为基准随画面高度缩放。"""
    p, en = r["params"], _en(r["scope"])
    k = float(p.get("strength", 0.5))
    if k <= 0:
        return "{in}null{out}"
    sigma = max(0.3, float(p.get("radius", 2)) * ctx.h / 1080.0)
    ctx.notes.append(f"soften sigma={sigma:.2f} mix={k:.2f}")
    return _blend_wrap(f"gblur=sigma={sigma:.3f}", k, ctx, en)


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
          "atmos": stage_atmos, "soften": stage_soften, "deflicker": stage_deflicker, "delogo": stage_delogo,
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


# ---------------------------------------------------------------- 分镜剪辑(换段:两个版本按时间段拼接)
def merge_ranges(cuts: list[dict], duration: float, fps: float) -> list[tuple[float, float]]:
    """时间段规范化:裁到 [0,duration]、按帧对齐、排序、合并重叠/相邻;过短(<1 帧)的丢弃。"""
    step = 1.0 / max(1.0, fps)
    out: list[tuple[float, float]] = []
    rows = []
    for c in cuts or []:
        try:
            t0, t1 = float(c.get("t0") or 0), float(c.get("t1") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        t0 = max(0.0, min(duration, round(t0 / step) * step))
        t1 = max(0.0, min(duration, round(t1 / step) * step))
        if t1 - t0 >= step - 1e-6:
            rows.append((t0, t1))
    for t0, t1 in sorted(rows):
        if out and t0 <= out[-1][1] + 1e-6:
            out[-1] = (out[-1][0], max(out[-1][1], t1))
        else:
            out.append((t0, t1))
    return out


def splice_plan(cuts: list[tuple[float, float]], duration: float) -> list[dict]:
    """把「切掉的时间段」展开成整条时间线的分段表:[{src:'base'|'alt', t0, t1}],覆盖 [0,duration]。"""
    segs, pos = [], 0.0
    for t0, t1 in cuts:
        if t0 > pos + 1e-6:
            segs.append({"src": "base", "t0": pos, "t1": t0})
        segs.append({"src": "alt", "t0": t0, "t1": t1})
        pos = t1
    if duration > pos + 1e-6:
        segs.append({"src": "base", "t0": pos, "t1": duration})
    return segs


def splice(base_src: Path, alt_src: Path, dst: Path, cuts: list[dict]) -> dict:
    """分镜剪辑:以 base 版本为时间线,cuts 里的时间段换成 alt 版本同一时间段的画面(与声音),
    其余保留 base;各段归一到 base 的 w×h/fps 后 concat 一次编码出 dst。
    alt 比 base 短、时间段超出 alt 尾部时,末帧克隆补齐(notes 里说明)。返回 {segments, duration, notes}。"""
    require_tools("ffmpeg", "ffprobe")
    bi, ai = probe(base_src), probe(alt_src)
    w, h, fps = bi["width"], bi["height"], bi["fps"] or 24.0
    if not w or not h or not bi["duration"]:
        raise FxError(f"基准版本无法解析:{base_src.name}")
    if not ai["width"] or not ai["duration"]:
        raise FxError(f"替换版本无法解析:{alt_src.name}")
    ranges = merge_ranges(cuts, bi["duration"], fps)
    if not ranges:
        raise FxError("没有有效的剪切时间段")
    segs = splice_plan(ranges, bi["duration"])
    notes: list[str] = []
    audio = bool(bi["has_audio"])
    if audio and not ai["has_audio"]:
        notes.append("替换版本无声轨,替换段用静音")
    if ai["duration"] + 0.05 < max(r[1] for r in ranges):
        notes.append(f"替换版本时长 {ai['duration']:.2f}s 短于剪切段末尾,超出部分用替换版本末帧补齐")
    if (ai["width"], ai["height"]) != (w, h):
        notes.append(f"替换版本分辨率 {ai['width']}×{ai['height']} 已归一到 {w}×{h}")
    parts, vlabels, alabels = [], [], []
    for i, s in enumerate(segs):
        idx = 0 if s["src"] == "base" else 1
        info = bi if idx == 0 else ai
        length = s["t1"] - s["t0"]
        n = max(1, int(round(length * fps)))
        t_end = min(s["t1"], info["duration"]) if idx == 1 else s["t1"]
        parts.append(f"[{idx}:v]trim=start={s['t0']:.6f}:end={max(t_end, s['t0'] + 1e-3):.6f},setpts=PTS-STARTPTS,"
                     f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,"
                     f"setsar=1,fps={fps:g},format=yuv420p,tpad=stop=-1:stop_mode=clone,trim=end_frame={n},setpts=PTS-STARTPTS[v{i}]")
        vlabels.append(f"[v{i}]")
        if audio:
            if info["has_audio"]:
                parts.append(f"[{idx}:a]atrim=start={s['t0']:.6f}:end={max(t_end, s['t0'] + 1e-3):.6f},asetpts=PTS-STARTPTS,"
                             f"aformat=sample_rates=48000:channel_layouts=stereo,apad=whole_dur={length:.6f},atrim=end={length:.6f},asetpts=PTS-STARTPTS[a{i}]")
            else:
                parts.append(f"anullsrc=r=48000:cl=stereo:d={length:.6f}[a{i}]")
            alabels.append(f"[a{i}]")
    if audio:
        parts.append("".join(v + a for v, a in zip(vlabels, alabels)) + f"concat=n={len(segs)}:v=1:a=1[vo][ao]")
    else:
        parts.append("".join(vlabels) + f"concat=n={len(segs)}:v=1:a=0[vo]")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.stem + ".rendering" + dst.suffix)
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(base_src), "-i", str(alt_src),
           "-filter_complex", ";".join(parts), "-map", "[vo]"]
    if audio:
        cmd += ["-map", "[ao]", "-c:a", "aac", "-b:a", "192k"]
    cmd += ENC_VIDEO + [str(tmp)]
    try:
        run(cmd, timeout=3600)
        if dst.exists():
            dst.unlink()
        tmp.rename(dst)
    finally:
        tmp.unlink(missing_ok=True)
    return {"segments": segs, "cuts": [{"t0": round(a, 3), "t1": round(b, 3)} for a, b in ranges],
            "duration": round(bi["duration"], 3), "notes": notes}


# ---------------------------------------------------------------- 分镜剪辑(删段:切掉的时间段直接删除,其余按序拼接)
def cut_out(src: Path, dst: Path, cuts: list[dict]) -> dict:
    """删段:以 src 为时间线,cuts 里的时间段直接删除,剩下的段按原顺序拼接一次编码出 dst(时长变短)。
    画面与声轨同步裁剪;返回 {segments(保留段), cuts(规范化后删除段), duration(原时长), new_duration, removed, notes}。"""
    require_tools("ffmpeg", "ffprobe")
    info = probe(src)
    w, h, fps = info["width"], info["height"], info["fps"] or 24.0
    if not w or not h or not info["duration"]:
        raise FxError(f"基准版本无法解析:{src.name}")
    ranges = merge_ranges(cuts, info["duration"], fps)
    if not ranges:
        raise FxError("没有有效的删除时间段")
    keep = [s for s in splice_plan(ranges, info["duration"]) if s["src"] == "base"]
    if not keep:
        raise FxError("选中的时间段覆盖了整段视频,没有剩余内容可拼")
    removed = sum(b - a for a, b in ranges)
    audio = bool(info["has_audio"])
    parts, vlabels, alabels = [], [], []
    for i, s in enumerate(keep):
        length = s["t1"] - s["t0"]
        n = max(1, int(round(length * fps)))
        parts.append(f"[0:v]trim=start={s['t0']:.6f}:end={max(s['t1'], s['t0'] + 1e-3):.6f},setpts=PTS-STARTPTS,"
                     f"fps={fps:g},format=yuv420p,trim=end_frame={n},setpts=PTS-STARTPTS[v{i}]")
        vlabels.append(f"[v{i}]")
        if audio:
            parts.append(f"[0:a]atrim=start={s['t0']:.6f}:end={max(s['t1'], s['t0'] + 1e-3):.6f},asetpts=PTS-STARTPTS,"
                         f"aformat=sample_rates=48000:channel_layouts=stereo,apad=whole_dur={length:.6f},atrim=end={length:.6f},asetpts=PTS-STARTPTS[a{i}]")
            alabels.append(f"[a{i}]")
    if audio:
        parts.append("".join(v + a for v, a in zip(vlabels, alabels)) + f"concat=n={len(keep)}:v=1:a=1[vo][ao]")
    else:
        parts.append("".join(vlabels) + f"concat=n={len(keep)}:v=1:a=0[vo]")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.stem + ".rendering" + dst.suffix)
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-filter_complex", ";".join(parts), "-map", "[vo]"]
    if audio:
        cmd += ["-map", "[ao]", "-c:a", "aac", "-b:a", "192k"]
    cmd += ENC_VIDEO + [str(tmp)]
    try:
        run(cmd, timeout=3600)
        if dst.exists():
            dst.unlink()
        tmp.rename(dst)
    finally:
        tmp.unlink(missing_ok=True)
    new_dur = sum(s["t1"] - s["t0"] for s in keep)
    notes = [f"时长 {info['duration']:.2f}s → {new_dur:.2f}s,与镜头表/时间线的组时长不再一致,出成片前需同步 timeline"]
    return {"segments": keep, "cuts": [{"t0": round(a, 3), "t1": round(b, 3)} for a, b in ranges],
            "duration": round(info["duration"], 3), "new_duration": round(new_dur, 3), "removed": round(removed, 3), "notes": notes}


# ---------------------------------------------------------------- 分镜剪辑(插黑 / 定格:组内某时刻插入定格帧 + 黑场,时长变长)
def insert_pad(src: Path, dst: Path, t: float, freeze_s: float = 0.0, hold_s: float = 0.0, audio: str = "sustain") -> dict:
    """在 src 的 t 秒处插入 [定格 freeze_s(t 前一帧克隆)] + [黑场 hold_s],其余原样,一次编码出 dst(时长变长)。
    画面按帧量化;声轨按 modules/timemap 同一策略重映射(sustain 延续前段 / fade 淡出 / mute 静音)。
    返回 {t, freeze_s, hold_s, audio, duration, new_duration, time_ops(组内秒,基准 = src)}。"""
    try:
        import timemap
    except ImportError:  # 服务端以 modules.post_* 包路径导入时
        from modules import timemap
    require_tools("ffmpeg", "ffprobe")
    info = probe(src)
    w, h, fps = info["width"], info["height"], info["fps"] or 24.0
    if not w or not h or not info["duration"]:
        raise FxError(f"基准版本无法解析:{src.name}")
    step = 1.0 / fps
    tf = int(round(float(t) * fps))
    total_f = int(round(info["duration"] * fps))
    tf = max(0, min(total_f, tf))
    fz_f, hd_f = int(round(float(freeze_s or 0) * fps)), int(round(float(hold_s or 0) * fps))
    if fz_f <= 0 and hd_f <= 0:
        raise FxError("定格与黑场时长至少一项 > 0")
    if fz_f > 0 and tf == 0:
        raise FxError("开头 0 秒处没有前一帧可定格,请改用黑场或把时刻后移")
    audio = audio if audio in timemap.AUDIO_POLICIES else "sustain"
    t_q = tf * step
    parts, labels = [], []
    base = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps={fps:g},format=yuv420p,settb=AVTB"
    if tf > 0:
        f = f"[0:v]trim=end_frame={tf},setpts=PTS-STARTPTS,{base}"
        if fz_f:
            f += f",tpad=stop_mode=clone:stop_duration={fz_f / fps:.6f}"
        parts.append(f + f",setpts=N/({fps:g}*TB)[va]")
        labels.append("[va]")
    if hd_f:
        parts.append(f"color=c=black:s={w}x{h}:r={fps:g}:d={hd_f / fps:.6f},format=yuv420p,setsar=1,settb=AVTB,"
                     f"trim=end_frame={hd_f},setpts=N/({fps:g}*TB)[vk]")
        labels.append("[vk]")
    if tf < total_f:
        parts.append(f"[0:v]trim=start_frame={tf},setpts=PTS-STARTPTS,{base},setpts=N/({fps:g}*TB)[vb]")
        labels.append("[vb]")
    parts.append("".join(labels) + f"concat=n={len(labels)}:v=1:a=0,setpts=N/({fps:g}*TB)[vo]")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp_v = dst.with_name(dst.stem + ".rendering.video" + dst.suffix)
    tmp = dst.with_name(dst.stem + ".rendering" + dst.suffix)
    a_tmp = dst.with_name(dst.stem + ".rendering.wav")
    ops = [{"src_t0": round(t_q, 6), "src_t1": round(t_q, 6), "out_len": round((fz_f + hd_f) / fps, 6),
            "freeze_s": round(fz_f / fps, 6), "hold_s": round(hd_f / fps, 6), "audio": audio, "kind": "insert_pad"}]
    try:
        run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-filter_complex", ";".join(parts), "-map", "[vo]", "-an",
             "-frames:v", str(total_f + fz_f + hd_f)] + ENC_VIDEO + [str(tmp_v)], timeout=3600)
        if info["has_audio"]:
            timemap.remap_audio(src, a_tmp, ops, src_dur=info["duration"])
            run(["ffmpeg", "-y", "-v", "error", "-i", str(tmp_v), "-i", str(a_tmp), "-map", "0:v:0", "-map", "1:a:0",
                 "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(tmp)], timeout=3600)
        else:
            tmp_v.rename(tmp)
        if dst.exists():
            dst.unlink()
        tmp.rename(dst)
    finally:
        for x in (tmp_v, tmp, a_tmp):
            x.unlink(missing_ok=True)
    new_dur = probe(dst)["duration"]
    return {"t": round(t_q, 3), "freeze_s": round(fz_f / fps, 3), "hold_s": round(hd_f / fps, 3), "audio": audio,
            "duration": round(info["duration"], 3), "new_duration": round(new_dur, 3), "time_ops": ops,
            "notes": [f"时长 {info['duration']:.2f}s → {new_dur:.2f}s;出成片时外挂声轨/字幕按 timemap 自动平移"]}


# ---------------------------------------------------------------- 慢动作(画面修补 slow_motion,2026-09-23:时间段按倍率拉长,时长变长)
SLOWMO_RATES = (1.5, 2.0, 3.0, 4.0)


def slow_motion(src: Path, dst: Path, t0: float | None, t1: float | None, rate: float, interp: Path | None = None,
                interp_whole: bool = False, audio: str = "stretch") -> dict:
    """把 src 的时间段 [t0, t1)(None = 整段)按 rate 倍拉长:段前 / 段后原样,段内用高帧率补帧素材 setpts 回放
    (慢动作),一次编码出 dst;声轨按 modules/timemap 同一策略重映射(stretch 保音高拉伸 / sustain / fade / mute)。
    interp = agent 用 RIFE 类工作流对该时间段(interp_whole=True 时为整段源)补出的高帧率片段;不给则用 ffmpeg
    minterpolate 光流补帧兜底(画质次之)。返回 {t0, t1, rate, method, audio, duration, new_duration, time_ops(组内秒,基准 = src)}。"""
    try:
        import timemap
    except ImportError:  # 服务端以 modules.post_* 包路径导入时
        from modules import timemap
    require_tools("ffmpeg", "ffprobe")
    info = probe(src)
    w, h, fps = info["width"], info["height"], info["fps"] or 24.0
    if not w or not h or not info["duration"]:
        raise FxError(f"基准版本无法解析:{src.name}")
    rate = float(rate)
    if not (1.0 < rate <= 8.0):
        raise FxError(f"倍率须在 (1, 8] 之间:{rate}")
    total_f = int(round(info["duration"] * fps))
    f0 = 0 if t0 is None else max(0, min(total_f, int(round(float(t0) * fps))))
    f1 = total_f if t1 is None else max(0, min(total_f, int(round(float(t1) * fps))))
    if f1 - f0 < 2:
        raise FxError("时间段至少 2 帧")
    span_f = f1 - f0
    out_f = int(round(span_f * rate))          # 慢放段输出帧数
    audio = audio if audio in timemap.AUDIO_POLICIES else "stretch"
    base = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,format=yuv420p,settb=AVTB"
    inputs = ["-i", str(src)]
    parts, labels = [], []
    if f0 > 0:
        parts.append(f"[0:v]trim=end_frame={f0},setpts=PTS-STARTPTS,{base},fps={fps:g},setpts=N/({fps:g}*TB)[va]")
        labels.append("[va]")
    if interp is not None:
        inputs += ["-i", str(interp)]
        ii = probe(interp)
        if not ii.get("width") or not ii.get("duration"):
            raise FxError(f"补帧片段无法解析:{Path(interp).name}")
        method = "interp"
        seg = "[1:v]"
        if interp_whole:
            # 整段源的补帧版本:先按源时间取出 [t0, t1)
            seg += f"trim=start={f0 / fps:.6f}:end={f1 / fps:.6f},setpts=PTS-STARTPTS,"
        else:
            seg += "setpts=PTS-STARTPTS,"
        # 高帧率片段按倍率放慢时间戳,再重采样到源帧率:补出的中间帧成为慢放里的真实帧
        parts.append(f"{seg}setpts={rate:.6f}*PTS,{base},fps={fps:g},trim=end_frame={out_f},"
                     f"tpad=stop_mode=clone:stop=-1,trim=end_frame={out_f},setpts=N/({fps:g}*TB)[vm]")
    else:
        method = "minterpolate"
        mi_fps = fps * rate
        parts.append(f"[0:v]trim=start_frame={f0}:end_frame={f1},setpts=PTS-STARTPTS,{base},"
                     f"minterpolate=fps={mi_fps:g}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1,"
                     f"setpts={rate:.6f}*PTS,fps={fps:g},trim=end_frame={out_f},tpad=stop_mode=clone:stop=-1,trim=end_frame={out_f},"
                     f"setpts=N/({fps:g}*TB)[vm]")
    labels.append("[vm]")
    if f1 < total_f:
        parts.append(f"[0:v]trim=start_frame={f1},setpts=PTS-STARTPTS,{base},fps={fps:g},setpts=N/({fps:g}*TB)[vb]")
        labels.append("[vb]")
    if len(labels) == 1:
        parts.append(f"{labels[0]}null[vo]")
    else:
        parts.append("".join(labels) + f"concat=n={len(labels)}:v=1:a=0,setpts=N/({fps:g}*TB)[vo]")
    n_total = f0 + out_f + (total_f - f1)
    ops = [{"src_t0": round(f0 / fps, 6), "src_t1": round(f1 / fps, 6), "out_len": round(out_f / fps, 6),
            "rate": rate, "audio": audio, "kind": "slow_motion"}]
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp_v = dst.with_name(dst.stem + ".rendering.video" + dst.suffix)
    tmp = dst.with_name(dst.stem + ".rendering" + dst.suffix)
    a_tmp = dst.with_name(dst.stem + ".rendering.wav")
    try:
        run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", ";".join(parts), "-map", "[vo]", "-an",
             "-frames:v", str(n_total)] + ENC_VIDEO + [str(tmp_v)], timeout=7200)
        if info["has_audio"]:
            timemap.remap_audio(src, a_tmp, ops, src_dur=info["duration"])
            run(["ffmpeg", "-y", "-v", "error", "-i", str(tmp_v), "-i", str(a_tmp), "-map", "0:v:0", "-map", "1:a:0",
                 "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(tmp)], timeout=3600)
        else:
            tmp_v.rename(tmp)
        if dst.exists():
            dst.unlink()
        tmp.rename(dst)
    finally:
        for x in (tmp_v, tmp, a_tmp):
            x.unlink(missing_ok=True)
    new_dur = probe(dst)["duration"]
    return {"t0": round(f0 / fps, 3), "t1": round(f1 / fps, 3), "rate": rate, "method": method, "audio": audio,
            "duration": round(info["duration"], 3), "new_duration": round(new_dur, 3), "time_ops": ops,
            "notes": [f"时长 {info['duration']:.2f}s → {new_dur:.2f}s(第 {f0 / fps:.2f}–{f1 / fps:.2f} 秒 ×{rate:g},"
                      f"{'RIFE 补帧片段' if method == 'interp' else 'ffmpeg minterpolate 光流补帧兜底'});出成片时外挂声轨/字幕按 timemap 自动平移"]}
