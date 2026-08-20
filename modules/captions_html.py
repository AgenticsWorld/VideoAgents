# -*- coding: utf-8 -*-
"""captions_html.py — 花字 HTML 渲染引擎(2026-08-14 起替代 libass 路径)。

架构:HTML 模版(项目内,agent 按项目视觉定制)→ headless Chromium 逐帧透明截图
→ ProRes 4444 alpha 贴片(项目缓存)→ ffmpeg overlay 合成逐组副本。
设计(captions.json v3)仍由 10-editing/caption Agent 产出;本模块只做确定性执行。
Agent 一律经 code/render_captions.py CLI 调用,禁止自写渲染脚本。

## 模版协议 v1(captpl.v1)

- 模版是自包含 HTML,位于 <项目>/edit/caption_templates/<name>.html,
  captions.json 以 template_ref: "template:<name>" 引用;
- 画布(stage)1800×700 CSS px,基准字号 150 CSS px;引擎用 device_scale_factor
  = 目标字号/150 渲染,合成端不再缩放(渲多大贴多大,零像素浪费);
- 模版必须暴露 window.seek(t):幂等地把所有样式设置到 t 秒的状态(确定性重渲
  的根基,禁止依赖真实时钟/requestAnimationFrame/CSS animation 自走);
- 模版必须设 window.__anchorEl = "<文字块元素 id>":锚点定位用 bbox,不含装饰;
- 字体一律写 font://<font_id>(如 font://user:MaShanZheng),引擎按 fonts
  manifest 解析成 file:// 并做字形覆盖预检——直接写系统字体名会静默回退,禁止;
- 参数经 URL query 注入:text(文字)、dur(总时长秒)、params(JSON:分段色/
  盒色/辉光等模版自定义参数);
- 入场/出场时长由模版内定,idle 段必须设计为无缝循环(任意 dur 都成立)。

时长语义:贴片时长 = local_end - local_start,叠加在组 clip 的 local_start 处。
缓存:<项目>/assets/caption_cache/<hash>.mov(+.json bbox 元数据),键 =
(协议版本, 模版内容, 文字, 参数, dsf, fps, 时长);PNG 中间帧用完即焚。
"""
import json
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote

from avsync import canonical_sha256, file_sha256, require_tools
from captions import (POSITIONS, _POSITION_ALIASES, X264_ARGS, ffmpeg_bin,
                      probe_video_info, resolve_font)

PROTOCOL_VERSION = "captpl.v1"
HTML_RENDERER_VERSION = "h1"      # 引擎逻辑变更即递增 → 旧回执/缓存自动过期
STAGE_W, STAGE_H = 1800, 700      # 模版画布(CSS px)
BASE_FONT_PX = 150                # 模版基准字号(CSS px)
EM_PCT_RANGE = (7.0, 26.0)        # em_pct 合法区间(字号 em 占画面高百分比)
DSF_RANGE = (0.2, 4.0)            # device_scale_factor 允许区间
MAX_TEXT_CSS_W = 1700             # 文字估宽超此值即报错(花字文案纪律:短句)
_FONT_URL = re.compile(r"font://([A-Za-z0-9_:.\-]+)")
_FULLWIDTH = re.compile(r"[ᄀ-鿿　-ヿ가-힯＀-￯]")

# ---------------------------------------------------------------- 模版解析


def templates_dir(proj_root: Path) -> Path:
    return Path(proj_root) / "edit" / "caption_templates"


def template_path(proj_root: Path, ref: str) -> Path:
    """template_ref "template:<name>" → 项目内模版文件路径。"""
    if not ref.startswith("template:"):
        raise RuntimeError(f"template_ref 须为 template:<name>,得到 {ref!r}")
    name = ref[len("template:"):]
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", name):
        raise RuntimeError(f"模版名非法:{name!r}")
    p = templates_dir(proj_root) / f"{name}.html"
    if not p.is_file():
        raise RuntimeError(f"模版不存在:{p}(花字设计工单应先产出项目风格系统)")
    return p


def resolve_template(proj_root: Path, ref: str,
                     fonts_manifest: dict) -> tuple[str, list[str]]:
    """读模版并解析 font:// 占位。返回 (可渲染 html, 用到的 font_id 列表)。"""
    raw = template_path(proj_root, ref).read_text(encoding="utf-8")
    font_ids = sorted(set(_FONT_URL.findall(raw)))
    html = raw
    for fid in font_ids:
        ent = resolve_font(fid, fonts_manifest)      # 找不到即抛错,不静默
        html = html.replace(f"font://{fid}", Path(ent["path"]).as_uri())
    return html, font_ids


def template_font_files(proj_root: Path, ref: str,
                        fonts_manifest: dict) -> list[Path]:
    raw = template_path(proj_root, ref).read_text(encoding="utf-8")
    return [Path(resolve_font(fid, fonts_manifest)["path"])
            for fid in sorted(set(_FONT_URL.findall(raw)))]


def protocol_issues(html_text: str) -> list[str]:
    """模版协议静态检查(不渲染)。硬闸门的第一层。"""
    issues = []
    if "window.seek" not in html_text:
        issues.append("缺 window.seek(t)(协议 captpl.v1 必须)")
    if "__anchorEl" not in html_text:
        issues.append("缺 window.__anchorEl 锚点声明")
    if re.search(r"@keyframes|animation\s*:", html_text):
        issues.append("含 CSS animation/@keyframes:动画必须全部由 seek(t) 驱动,"
                      "自走动画会破坏确定性")
    if re.search(r"requestAnimationFrame|setInterval|setTimeout\s*\(", html_text):
        issues.append("含自走时钟(rAF/定时器):禁止,动画只能由 seek(t) 驱动")
    if re.search(r"https?://", html_text):
        issues.append("含外部网络引用:模版必须自包含(离线可渲)")
    return issues


def glyph_missing(text: str, font_files: list[Path]) -> dict[str, str]:
    """字形覆盖预检:{font_path: 缺的字}。浏览器缺字形会静默回退系统字体,
    必须渲前拦截(libass 时代同款教训,font_effective 的 HTML 版)。"""
    try:
        from fontTools.ttLib import TTFont
    except ImportError:
        raise RuntimeError("缺 fonttools(pip install fonttools)——字形覆盖预检必需")
    chars = {c for c in text if not c.isspace() and not c.isascii()} | \
            {c for c in text if c.isascii() and c.isalnum()}
    out = {}
    for fp in font_files:
        cmap = TTFont(str(fp), lazy=True).getBestCmap()
        miss = "".join(sorted(c for c in chars if ord(c) not in cmap))
        if miss:
            out[str(fp)] = miss
    return out


def estimate_css_width(text: str, spacing_px: float = 26.0) -> float:
    """渲前估宽(CSS px):CJK ≈ 1.0 em,拉丁/数字 ≈ 0.62 em,再加字距。"""
    w = 0.0
    for ch in text:
        w += BASE_FONT_PX * (1.0 if _FULLWIDTH.match(ch) else 0.62) + spacing_px
    return w

# ---------------------------------------------------------------- 贴片渲染


def _sticker_key(html: str, text: str, params: dict, dur: float,
                 fps: float, dsf: float) -> str:
    return canonical_sha256({
        "protocol": PROTOCOL_VERSION, "renderer": HTML_RENDERER_VERSION,
        "template_sha": canonical_sha256(html), "text": text, "params": params,
        "dur": round(dur, 3), "fps": round(fps, 4), "dsf": round(dsf, 4)})[:16]


class StickerRenderer:
    """批渲染器:单 Chromium 实例复用,贴片按内容 hash 缓存。

    用法:
        with StickerRenderer(cache_dir) as sr:
            mov, meta = sr.render(html, text, params, dur, fps, dsf)
    """

    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._pw = None
        self._browser = None
        self._contexts: dict[float, object] = {}   # dsf → BrowserContext

    def __enter__(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RuntimeError("缺 playwright(pip install playwright && "
                               "python3 -m playwright install chromium)")
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch()
        return self

    def __exit__(self, *exc):
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()
        return False

    def _page(self, dsf: float):
        # device_scale_factor 是 context 级属性,按 dsf 建 context 池复用
        key = round(dsf, 4)
        if key not in self._contexts:
            ctx = self._browser.new_context(
                viewport={"width": STAGE_W, "height": STAGE_H},
                device_scale_factor=dsf)
            self._contexts[key] = {"ctx": ctx, "page": ctx.new_page()}
        return self._contexts[key]["page"]

    def render(self, html: str, text: str, params: dict, dur: float,
               fps: float, dsf: float, tag: str = "") -> tuple[Path, dict]:
        key = _sticker_key(html, text, params, dur, fps, dsf)
        mov = self.cache_dir / f"{key}.mov"
        meta_p = self.cache_dir / f"{key}.json"
        if mov.is_file() and meta_p.is_file():
            return mov, json.loads(meta_p.read_text(encoding="utf-8"))

        sissues = protocol_issues(html)
        if sissues:
            raise RuntimeError(f"{tag}: 模版协议不合规:{'; '.join(sissues)}")

        page = self._page(dsf)
        with tempfile.TemporaryDirectory(prefix="capsticker_") as td:
            tdir = Path(td)
            html_p = tdir / "t.html"
            html_p.write_text(html, encoding="utf-8")
            url = (f"{html_p.as_uri()}?text={quote(text)}&dur={dur:.3f}"
                   f"&params={quote(json.dumps(params, ensure_ascii=False))}")
            page.goto(url)
            page.wait_for_function("document.fonts.status === 'loaded'")
            ok = page.evaluate(
                "() => typeof window.seek === 'function' && !!window.__anchorEl"
                " && !!document.getElementById(window.__anchorEl)")
            if not ok:
                raise RuntimeError(f"{tag}: 模版运行时协议不合规"
                                   "(seek/__anchorEl/锚点元素三者须齐备)")

            # 幂等抽查:t_mid 渲一帧 → 拨走 → 拨回,两帧必须逐字节一致
            t_mid = min(dur / 2, 1.2)
            page.evaluate(f"window.seek({t_mid:.4f})")
            a = page.screenshot(omit_background=True)
            page.evaluate(f"window.seek({max(0.0, dur - 0.05):.4f})")
            page.evaluate(f"window.seek({t_mid:.4f})")
            if page.screenshot(omit_background=True) != a:
                raise RuntimeError(f"{tag}: seek(t) 不幂等(同一 t 两次渲染不一致),"
                                   "模版含随机/时钟依赖,违反协议")
            if not _png_has_alpha_content(a):
                raise RuntimeError(f"{tag}: idle 中段画面全透明(贴片空渲)")

            bbox = page.evaluate(
                "() => { const r = document.getElementById(window.__anchorEl)"
                ".getBoundingClientRect();"
                " return {x: r.x, y: r.y, w: r.width, h: r.height}; }")
            if bbox["w"] <= 1 or bbox["h"] <= 1:
                raise RuntimeError(f"{tag}: 锚点 bbox 为空")
            if bbox["x"] < 0 or bbox["y"] < 0 \
                    or bbox["x"] + bbox["w"] > STAGE_W \
                    or bbox["y"] + bbox["h"] > STAGE_H:
                raise RuntimeError(f"{tag}: 文字块越出画布({bbox}),缩字号或缩短文案")
            bbox = {k: v * dsf for k, v in bbox.items()}   # CSS px → 截图像素

            n = max(2, int(round(dur * fps)))
            frames = tdir / "frames"
            frames.mkdir()
            for k in range(n):
                page.evaluate(f"window.seek({k / fps:.5f})")
                page.screenshot(path=str(frames / f"{k:05d}.png"),
                                omit_background=True)
            subprocess.run(
                [ffmpeg_bin(), "-y", "-v", "error",
                 "-framerate", f"{fps:.6f}", "-i", str(frames / "%05d.png"),
                 "-c:v", "prores_ks", "-profile:v", "4444",
                 "-pix_fmt", "yuva444p10le", str(mov)],
                check=True, capture_output=True, timeout=1800)
            # TemporaryDirectory 退出即焚 PNG(滚动清理,峰值=单贴片)
        meta = {"bbox": bbox, "dsf": dsf, "fps": fps, "dur": dur,
                "frames": n, "protocol": PROTOCOL_VERSION}
        meta_p.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        return mov, meta


def _png_has_alpha_content(png_bytes: bytes, thresh: int = 8) -> bool:
    """PNG 是否含非透明像素(ffmpeg 提取 alpha 均值判断,无 PIL 依赖)。"""
    # metadata=print 走 info 级日志,必须 -v info 才有输出(-v error 会静默,
    # 2026-08-14 空渲误报前科)
    p = subprocess.run(
        [ffmpeg_bin(), "-v", "info", "-i", "pipe:0", "-vf",
         "alphaextract,signalstats,metadata=print:key=lavfi.signalstats.YMAX",
         "-f", "null", "-"],
        input=png_bytes, capture_output=True, timeout=120)
    m = re.search(rb"YMAX=(\d+)", p.stderr or b"")
    if not m:
        raise RuntimeError("alpha 检查失败:signalstats 无输出(ffmpeg 异常)")
    return int(m.group(1)) >= thresh

# ---------------------------------------------------------------- 逐组合成


def _style_v3(cap: dict) -> tuple[str, float, dict]:
    ref = cap.get("template_ref") or ""
    em_pct = float(cap.get("em_pct") or 0)
    params = cap.get("params") or {}
    if not isinstance(params, dict):
        raise RuntimeError(f"{cap.get('id')}: params 须为对象")
    if not (EM_PCT_RANGE[0] <= em_pct <= EM_PCT_RANGE[1]):
        raise RuntimeError(f"{cap.get('id')}: em_pct {em_pct} 不在 {EM_PCT_RANGE}")
    return ref, em_pct, params


def _anchor_xy(position: str, bbox: dict, W: int, H: int) -> tuple[float, float]:
    """POSITIONS 锚点语义(与 libass \\an 一致)→ 贴片左上角坐标,并做安全区收拢。"""
    pos = _POSITION_ALIASES.get(position, position)
    xr, yr, an = POSITIONS.get(pos, POSITIONS["center"])
    ax, ay = xr * W, yr * H
    if an in (7, 4, 1):
        x = ax - bbox["x"]
    elif an in (9, 6, 3):
        x = ax - (bbox["x"] + bbox["w"])
    else:
        x = ax - (bbox["x"] + bbox["w"] / 2)
    if an in (7, 8, 9):
        y = ay - bbox["y"]
    elif an in (4, 5, 6):
        y = ay - (bbox["y"] + bbox["h"] / 2)
    else:
        y = ay - (bbox["y"] + bbox["h"])
    # 收拢:文字块不出左右 2% 边距、不进底部字幕安全区(y+h ≤ 0.82H)
    x += max(0.0, 0.02 * W - (x + bbox["x"])) \
        - max(0.0, (x + bbox["x"] + bbox["w"]) - 0.98 * W)
    y -= max(0.0, (y + bbox["y"] + bbox["h"]) - 0.82 * H)
    y += max(0.0, 0.02 * H - (y + bbox["y"]))
    return x, y


def _captions_hash_v3(captions: list[dict], proj_root: Path,
                      fonts_manifest: dict) -> str:
    tpl = {}
    for c in captions:
        ref = c.get("template_ref") or ""
        if ref not in tpl:
            tpl[ref] = file_sha256(str(template_path(proj_root, ref)))
    fonts = []
    for c in captions:
        for fp in template_font_files(proj_root, c["template_ref"], fonts_manifest):
            st = fp.stat()
            fonts.append((str(fp), st.st_size, int(st.st_mtime)))
    return canonical_sha256({"captions": captions, "templates": tpl,
                             "fonts": sorted(set(fonts))})


def render_group_html(clip_path: Path, out_path: Path, captions: list[dict],
                      fonts_manifest: dict, proj_root: Path,
                      renderer: StickerRenderer, force: bool = False) -> dict:
    """单组烧录(HTML 引擎):clip + 该组花字 → clips_caption 副本。

    幂等回执语义与 libass 版一致:(源 clip sha, 花字+模版+字体 hash, 引擎版本)
    一致即 SKIP。原组 clip 永不改动。cards 图卡 v3 暂不支持(默认关,启用需求
    出现时再移植)。
    """
    require_tools("ffmpeg", "ffprobe")
    clip_path, out_path = Path(clip_path), Path(out_path)
    receipt_path = out_path.with_suffix(".render.json")
    src_sha = file_sha256(str(clip_path))
    cap_hash = _captions_hash_v3(captions, proj_root, fonts_manifest)
    if not force and receipt_path.is_file() and out_path.is_file():
        try:
            old = json.loads(receipt_path.read_text(encoding="utf-8"))
            if (old.get("src_sha256"), old.get("captions_hash"),
                    old.get("renderer")) == \
                    (src_sha, cap_hash, HTML_RENDERER_VERSION):
                return {"status": "skipped", "out": str(out_path)}
        except (json.JSONDecodeError, OSError):
            pass

    info = probe_video_info(str(clip_path))
    W, H, fps = info["width"], info["height"], float(info["fps"])
    stickers = []
    for c in captions:
        tag = c.get("id") or c.get("text")
        ref, em_pct, params = _style_v3(c)
        html, _ = resolve_template(proj_root, ref, fonts_manifest)
        miss = glyph_missing(c["text"], template_font_files(proj_root, ref,
                                                            fonts_manifest))
        if miss:
            raise RuntimeError(f"{tag}: 字形缺失 {miss}(浏览器会静默回退,禁渲)")
        est_w = estimate_css_width(c["text"])
        if est_w > MAX_TEXT_CSS_W:
            raise RuntimeError(f"{tag}: 文案过长(估宽 {est_w:.0f}px > "
                               f"{MAX_TEXT_CSS_W}),花字须短句,请拆分")
        em = em_pct / 100.0 * H
        dsf = em / BASE_FONT_PX
        # 溢出预收缩:按估宽把整贴片等比缩进可用宽(0.9W)
        if est_w * dsf > 0.9 * W:
            dsf *= (0.9 * W) / (est_w * dsf)
        dsf = min(max(dsf, DSF_RANGE[0]), DSF_RANGE[1])
        ls, le = float(c["local_start"]), float(c["local_end"])
        le = min(le, info["duration_s"])
        dur = round(le - ls, 3)
        if dur <= 0.2:
            raise RuntimeError(f"{tag}: 花字时长 {dur}s 过短(<0.2s)")
        mov, meta = renderer.render(html, c["text"], params, dur, fps, dsf,
                                    tag=str(tag))
        x, y = _anchor_xy(c.get("position") or "center", meta["bbox"], W, H)
        stickers.append({"mov": mov, "x": x, "y": y, "ls": ls, "le": le})

    out_path.parent.mkdir(parents=True, exist_ok=True)
    inputs = [ffmpeg_bin(), "-v", "error", "-y", "-i", str(clip_path)]
    chains, prev = [], "0:v"
    for i, s in enumerate(stickers):
        inputs += ["-i", str(s["mov"])]
        chains.append(f"[{i + 1}:v]setpts=PTS+{s['ls']:.3f}/TB[stk{i}]")
        chains.append(
            f"[{prev}][stk{i}]overlay={s['x']:.0f}:{s['y']:.0f}:"
            f"enable='between(t,{s['ls']:.3f},{s['le']:.3f})'[v{i}]")
        prev = f"v{i}"
    audio = ["-c:a", "copy"] if info["has_audio"] else ["-an"]
    cmd = inputs + ["-filter_complex", ";".join(chains), "-map", f"[{prev}]",
                    *([] if not info["has_audio"] else ["-map", "0:a"]),
                    *X264_ARGS,
                    "-colorspace", "bt709", "-color_primaries", "bt709",
                    "-color_trc", "bt709", *audio, str(out_path)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if p.returncode != 0 or not out_path.is_file():
        raise RuntimeError(f"合成失败 {clip_path.name}: {(p.stderr or '')[-400:]}")

    out_info = probe_video_info(str(out_path))
    if (out_info["width"], out_info["height"]) != (W, H):
        raise RuntimeError(f"合成后分辨率变化:{W}x{H} → "
                           f"{out_info['width']}x{out_info['height']}")
    if abs(out_info["duration_s"] - info["duration_s"]) > 1.0 / max(1, fps) + 0.001:
        raise RuntimeError(f"合成后时长漂移:{info['duration_s']} → "
                           f"{out_info['duration_s']}")
    receipt = {"schema": "caption.render.v2", "renderer": HTML_RENDERER_VERSION,
               "engine": "html", "protocol": PROTOCOL_VERSION,
               "src": str(clip_path), "src_sha256": src_sha,
               "captions_hash": cap_hash, "captions_count": len(captions),
               "out_sha256": file_sha256(str(out_path)),
               "encoder": " ".join(X264_ARGS)}
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=1),
                            encoding="utf-8")
    return {"status": "rendered", "out": str(out_path)}

# ---------------------------------------------------------------- 环境自检


def chromium_ready() -> tuple[bool, str]:
    """caption_toolchain_verified 的 HTML 版:playwright + Chromium 可启动。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False, ("缺 playwright:pip install playwright && "
                       "python3 -m playwright install chromium")
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            ver = b.version
            b.close()
        return True, f"Chromium {ver}"
    except Exception as e:                                    # noqa: BLE001
        return False, f"Chromium 启动失败:{e}(python3 -m playwright install chromium)"
