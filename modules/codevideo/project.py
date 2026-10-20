"""演示视频项目级操作:工具链自检、锁定风格、按需拉样片、状态总览、一键重建。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import (EP, INSTRUMENTS_DIR, LIB_DIR, MIRROR, NS, STYLES_DIR, VENDOR_THREE, CodeVideoError, ffmpeg,
               project_block, project_settings, render_size, style_row, style_slugs, upstream)

STYLE_FILES = ("STYLE.md", "DEMO.md", "style.json")
LOCK_COPY = ("STYLE.md", "style.json")      # 锁定时只放这两份;DEMO.md 由 demo 在 TREATMENT.md 写成后放出(先方案后样片)


def doctor(gl: bool = True) -> dict:
    """宿主工具链自检(cv0-ingest 的 host_toolchain_verified)。返回 {ok, items:[{id, ok, detail, fatal}]}。"""
    items = []

    def add(cid, ok, detail="", fatal=True):
        items.append({"id": cid, "ok": bool(ok), "detail": detail, "fatal": fatal})

    for mod in ("numpy", "scipy", "soundfile", "PIL"):
        try:
            __import__(mod)
            add(f"python_{mod}", True)
        except Exception as e:  # noqa: BLE001
            add(f"python_{mod}", False, f"{sys.executable} 缺少 {mod}:{e}")
    try:
        import soxr  # noqa: F401
        add("python_soxr", True, fatal=False)
    except Exception:  # noqa: BLE001
        add("python_soxr", False, "没装 soxr:采样器退回 scipy 重采样(音质略差,可用)", fatal=False)
    ff = ffmpeg()
    try:
        r = subprocess.run([ff, "-hide_banner", "-version"], capture_output=True, text=True, timeout=30)
        enc = subprocess.run([ff, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=30).stdout
        fl = subprocess.run([ff, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=30).stdout
        miss = [x for x in ("libx264", "aac") if x not in enc] + [x for x in ("loudnorm", "ebur128", "blackdetect") if x not in fl]
        add("ffmpeg", r.returncode == 0 and not miss, (r.stdout.splitlines() or [""])[0][:80]
            + (f";缺 {', '.join(miss)}" if miss else ""))
    except (OSError, subprocess.TimeoutExpired) as e:
        add("ffmpeg", False, f"ffmpeg 不可用({e}):桌面端在设置里安装 ffmpeg,开发环境 brew install ffmpeg")
    lib_ok = all(p.is_file() for p in (LIB_DIR / "core" / "lib.js", LIB_DIR / "upstream.json",
                                       VENDOR_THREE / "three.module.js", LIB_DIR / "core" / "audio" / "sampler.py"))
    add("host_library", lib_ok, f"modules/codevideo/lib(lemo-opuscar {upstream().get('rev', '')[:12]})" if lib_ok
        else "宿主库不完整:重装 / 更新 VideoAgents")
    styles_ok = all((STYLES_DIR / s / "STYLE.md").is_file() for s in style_slugs())
    add("style_library", styles_ok, f"{len(style_slugs())} 个风格" if styles_ok else "风格库不完整")
    try:
        import playwright  # noqa: F401
        add("playwright", True)
    except Exception as e:  # noqa: BLE001
        add("playwright", False, f"缺少 Playwright({e})")
        gl = False
    if gl:
        from .page import launch, webgl_renderer

        async def go():
            from playwright.async_api import async_playwright
            async with async_playwright() as pw:
                b = await launch(pw)
                try:
                    return await webgl_renderer(b)
                finally:
                    await b.close()
        try:
            name = asyncio.run(go())
            soft = bool(name and any(k in name.lower() for k in ("swiftshader", "llvmpipe", "software")))
            add("chromium", True, "无头浏览器可启动")
            add("webgl", bool(name) and not soft, (name or "没有 WebGL") + ("(软件渲染:3D / 着色器风格会很慢)" if soft else ""),
                fatal=False)
        except CodeVideoError as e:
            add("chromium", False, str(e))
    from .fetch import installed_libs
    libs = installed_libs()
    add("instrument_packs", True, ("已下载采样库:" + "、".join(libs)) if libs else
        "还没下载采样库(代码作曲用到真实乐器时按需下载,或在「生成模型」页代码作曲标签页下载)", fatal=False)
    ok = all(i["ok"] for i in items if i["fatal"])
    return {"ok": ok, "items": items}


def lock_style(base: Path, slug: str | None = None) -> dict:
    """把风格文件复制进 codevideo/style/ 并记 sha256;已锁定且未指定新风格时只核对。"""
    blk = project_block(base)
    slug = slug or blk.get("code_style")
    if not slug or slug not in style_slugs():
        raise CodeVideoError(f"风格未选或不在风格库:{slug!r}(settings.json#project.code_style;可选 {len(style_slugs())} 个,见 "
                             "agents/workflows/codevideo/styles/README.md)")
    dst = base / NS / "style"
    dst.mkdir(parents=True, exist_ok=True)
    lock_p = dst / "lock.json"
    h = hashlib.sha256()
    for name in STYLE_FILES:
        src = STYLES_DIR / slug / name
        h.update(src.read_bytes())
    digest = h.hexdigest()
    if lock_p.is_file():
        old = json.loads(lock_p.read_text())
        if old.get("slug") == slug and all((dst / n).is_file() for n in LOCK_COPY):
            return {**old, "changed": False, "upstream_changed": old.get("sha256") != digest}
    for name in LOCK_COPY:
        shutil.copyfile(STYLES_DIR / slug / name, dst / name)
    (dst / "DEMO.md").unlink(missing_ok=True)       # 换风格:旧风格的样片说明不留
    row = style_row(slug) or {}
    lock = {"slug": slug, "en": row.get("en"), "cn": row.get("cn"), "sha256": digest,
            "lemo_rev": upstream().get("rev"), "locked_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    lock_p.write_text(json.dumps(lock, ensure_ascii=False, indent=1), encoding="utf-8")
    return {**lock, "changed": True}


def demo(base: Path, progress=None) -> dict:
    """按需下载本项目风格的样片源码与海报。须先有 codevideo/TREATMENT.md(先写方案、后看样片)。"""
    from .fetch import fetch_files
    if not (base / NS / "TREATMENT.md").is_file():
        raise DemoBeforeTreatment("codevideo/TREATMENT.md 还没写:先写导演方案(三个候选结构与选择),再看样片——"
                                  "样片只用来学技法,不能拿它的故事、结构、镜头与配乐")
    lock = lock_style(base)
    shutil.copyfile(STYLES_DIR / lock["slug"] / "DEMO.md", base / NS / "style" / "DEMO.md")
    res = fetch_files(f"demo:{lock['slug']}", progress)
    root = MIRROR / "styles" / lock["slug"]
    return {**res, "slug": lock["slug"], "demo_dir": str(root / "demo"), "poster": str(root / "poster.jpg"),
            "demo_md": str(base / NS / "style" / "DEMO.md")}


class DemoBeforeTreatment(CodeVideoError):
    pass


def info(base: Path) -> dict:
    from . import render as R
    from . import timeline as TL
    film = base / NS
    blk = project_block(base)
    out = {"project": base.name, "type": blk.get("type"), "style": blk.get("code_style"),
           "settings": {k: blk.get(k) for k in ("storyboard_review", "narration", "subtitles", "length_s", "fps", "music_source")},
           "size": list(render_size(project_settings(base))), "film_dir": f"{NS}/",
           "files": {n: (film / n).is_file() for n in ("brief.json", "TREATMENT.md", "timeline.json", "lines.json", "index.html",
                                                       "events.json", "mix.py", "CREDITS")},
           "style_locked": (film / "style" / "lock.json").is_file(),
           "instruments_dir": str(INSTRUMENTS_DIR)}
    try:
        tl = TL.load(base)
        out["timeline"] = {"dur": tl["dur"], "fps": tl["fps"], "size": tl["size"], "sections": len(tl["sections"]),
                           "shots": sum(len(s["shots"]) for s in tl["sections"])}
        if out["files"]["index.html"]:
            out["sections"] = R.status(base)
    except CodeVideoError as e:
        out["timeline_error"] = str(e)
    out["final"] = (base / "edit" / EP / "final.mp4").is_file()
    return out


def build(base: Path, workers: int | None = None, log=print) -> dict:
    """一键重建:sync → events → voice(有 lines.json)→ score(有 music/score.py)→ render → mix → master。"""
    from . import audio as A
    from . import render as R
    from . import timeline as TL
    steps = {}
    steps["sync"] = TL.sync(base)
    steps["events"] = R.events(base)
    film = base / NS
    if (film / "lines.json").is_file() and project_block(base).get("narration", True):
        steps["voice"] = A.voice(base, log=log)
    score = film / "music" / "score.py"
    if score.is_file():
        steps["score"] = A.run_script(score, film / "music" / "score.wav", duration=TL.load(base)["dur"], cwd=film, log=log)
    steps["render"] = R.render(base, workers=workers, log=log)
    steps["mix"] = A.mix(base, log=log)
    steps["master"] = A.master(base, log=log)
    return steps
