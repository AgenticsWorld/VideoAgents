"""用 Playwright(异步)打开演示视频页面:等 window.READY,页面报错 / 必需文件 404 即停(免得渲出坏片)。

浏览器参数沿用上游:macOS 显式用 Metal 做 WebGL(swiftshader 软渲染对 3D/着色器页面慢数倍),
CODEVIDEO_ANGLE=metal|gl|vulkan|swiftshader|default 可改;VIDEOAGENTS_CHROMIUM 指定可执行文件(与白模导出同口径)。
"""
from __future__ import annotations

import os
import sys

from . import CodeVideoError

READY_TIMEOUT_MS = 180_000
FATAL_TYPES = {"script", "document"}


def browser_args() -> list[str]:
    angle = os.environ.get("CODEVIDEO_ANGLE") or ("metal" if sys.platform == "darwin" else "default")
    return ([] if angle == "default" else [f"--use-angle={angle}"]) + [
        "--enable-gpu", "--ignore-gpu-blocklist", "--enable-webgl", "--font-render-hinting=none",
        "--force-color-profile=srgb", "--autoplay-policy=no-user-gesture-required"]


async def launch(pw):
    kw = {"headless": True, "args": browser_args()}
    exe = os.environ.get("VIDEOAGENTS_CHROMIUM")
    if exe:
        kw["executable_path"] = exe
    try:
        return await pw.chromium.launch(**kw)
    except Exception as e:  # noqa: BLE001
        raise CodeVideoError(f"无头浏览器启动失败:{str(e).splitlines()[0]}(开发环境先跑 python3 -m playwright install chromium)") from e


class FilmPage:
    """一个打开好的页面。errors 收集页面报错;check() 有错就抛。"""

    def __init__(self, page):
        self.page = page
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.external: list[str] = []

    def check(self):
        if self.errors:
            raise CodeVideoError("页面报错:" + " | ".join(self.errors[:3]))

    async def render(self, t: float):
        try:
            await self.page.evaluate("t => window.render(t)", float(t))
        except Exception as e:  # noqa: BLE001
            raise CodeVideoError(f"render({t:.3f}) 抛错:{str(e).splitlines()[0][:300]}") from e
        self.check()

    async def jpeg(self, quality: int = 95) -> bytes:
        return await self.page.screenshot(type="jpeg", quality=quality)

    async def png(self) -> bytes:
        return await self.page.screenshot(type="png")

    async def globals(self) -> dict:
        return await self.page.evaluate("""() => ({dur: window.DUR, ready: window.READY === true,
            render: typeof window.render === 'function', texts: typeof window.TEXTS === 'function',
            ev: Array.isArray(window.EV) ? window.EV.length : null, w: innerWidth, h: innerHeight})""")


async def open_film(browser, url: str, size: tuple[int, int], origin: str) -> FilmPage:
    w, h = size
    page = await browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=1)
    fp = FilmPage(page)

    def on_console(m):
        if m.type == "error" and not m.text.startswith("Failed to load resource"):
            fp.warnings.append(m.text[:300])

    def on_response(r):
        if r.status < 400 or r.url.endswith("/favicon.ico"):
            return
        if r.request.resource_type in FATAL_TYPES:
            fp.errors.append(f"{r.status} {r.url}(页面必需的 {r.request.resource_type};库文件请用 /core/… 等绝对路径)")
        else:
            fp.warnings.append(f"optional file missing: {r.url} ({r.status})")

    def on_request(req):
        if not req.url.startswith((origin, "data:", "blob:", "about:")):
            fp.external.append(req.url)

    page.on("console", on_console)
    page.on("pageerror", lambda e: fp.errors.append(str(e)[:400]))
    page.on("response", on_response)
    page.on("request", on_request)
    # 外网请求一律拦下(页面必须自带字体与素材);记进 external 供机检报告
    await page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(origin)
                     else route.abort())
    await page.goto(url)
    try:
        await page.wait_for_function("window.READY === true", timeout=READY_TIMEOUT_MS)
    except Exception as e:  # noqa: BLE001
        fp.check()
        raise CodeVideoError(f"window.READY 在 {READY_TIMEOUT_MS // 1000} s 内没有变成 true"
                             + (f";控制台:{' | '.join(fp.warnings[-3:])}" if fp.warnings else "")) from e
    fp.check()
    g = await fp.globals()
    if not g["render"]:
        raise CodeVideoError("页面没有 window.render(t)")
    return fp


async def webgl_renderer(browser) -> str | None:
    page = await browser.new_page()
    try:
        return await page.evaluate("""() => { const c = document.createElement('canvas');
            const g = c.getContext('webgl2') || c.getContext('webgl'); if (!g) return null;
            const e = g.getExtension('WEBGL_debug_renderer_info');
            return String(e ? g.getParameter(e.UNMASKED_RENDERER_WEBGL) : g.getParameter(g.RENDERER)); }""")
    finally:
        await page.close()
