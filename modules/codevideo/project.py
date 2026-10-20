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

from . import (EP, INSTRUMENTS_DIR, LIB_DIR, MIRROR, NS, STYLES_DIR, VENDOR_THREE, CodeVideoError, ffmpeg, is_light,
               light_styles, project_block, project_settings, render_size, style_ids, style_row, style_slugs, upstream)

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
    """把风格文件复制进 codevideo/style/ 并记 sha256;已锁定且未指定新风格时只核对。
    轻量风格(OPUSCAR 98,o98-NN)没有现成的 STYLE.md:按那条风格提示生成一份(见 light_style_md)。"""
    blk = project_block(base)
    slug = slug or blk.get("code_style")
    if not slug or slug not in style_ids():
        raise CodeVideoError(f"风格未选或不在风格库:{slug!r}(settings.json#project.code_style;完整风格 {len(style_slugs())} 个见 "
                             "agents/workflows/codevideo/styles/README.md,轻量风格 o98-01…o98-98 见 styles/opuscar98/styles.json)")
    dst = base / NS / "style"
    dst.mkdir(parents=True, exist_ok=True)
    lock_p = dst / "lock.json"
    light = is_light(slug)
    if light:
        entry = next(x for x in light_styles()["styles"] if x["id"] == slug)
        files = {"STYLE.md": light_style_md(entry).encode("utf-8"),
                 "style.json": (json.dumps({"slug": slug, "light": True, "source": "opuscar98", **entry}, ensure_ascii=False,
                                           indent=1) + "\n").encode("utf-8")}
    else:
        files = {name: (STYLES_DIR / slug / name).read_bytes() for name in LOCK_COPY}
    h = hashlib.sha256()
    for name in sorted(files):
        h.update(files[name])
    if not light:
        h.update((STYLES_DIR / slug / "DEMO.md").read_bytes())
    digest = h.hexdigest()
    if lock_p.is_file():
        old = json.loads(lock_p.read_text())
        if old.get("slug") == slug and all((dst / n).is_file() for n in files):
            return {**old, "changed": False, "upstream_changed": old.get("sha256") != digest}
    for name, data in files.items():
        (dst / name).write_bytes(data)
    (dst / "DEMO.md").unlink(missing_ok=True)       # 换风格:旧风格的样片说明不留
    row = style_row(slug) or {}
    lock = {"slug": slug, "en": row.get("en"), "cn": row.get("cn"), "light": light, "sha256": digest,
            "lemo_rev": upstream().get("rev"), "locked_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if light:
        lock["related"] = row.get("related") or []
    lock_p.write_text(json.dumps(lock, ensure_ascii=False, indent=1), encoding="utf-8")
    return {**lock, "changed": True}


def light_style_md(x: dict) -> str:
    """轻量风格的 STYLE.md:风格提示原文 + 构成 + 参考(只学语法)+ 补全须知。分镜与导演说明不带(那是 OPUSCAR 98 的主角 Clawd)。"""
    rel = ", ".join(f"`{s}`" for s in x.get("related") or [])
    refs = "\n".join(f"- {r}" for r in x.get("refs") or []) or "- (无)"
    return f"""# {x['style_zh']} / {x['style_en']} — 轻量风格(OPUSCAR 98 · No.{x['no']:02d})

> 来源:lemo-opuscar《OPUSCAR 98》第 {x['no']} 部({x['year']}《{x['zh']}》/ *{x['title']}*)用的风格。轻量风格只有下面这段**风格提示**,
> 没有完整风格那样的十一节规范(材质、色彩、字体、运动、镜头语法、声音调色板、原生手法、陷阱),也没有样片源码。

## 1. 风格提示(原文,固定不改)

**中文**

{x['prompt_zh']}

**English**

{x['prompt_en']}

## 2. 构成

- 渲染:{x['render']}(色调 {x['dim']})
- 混合手法:{'、'.join(x.get('blend_zh') or [])}({'; '.join(x.get('blend_en') or [])})

## 3. 参考(只学语法,不得照搬)

{refs}

## 4. 这段提示原来怎么用的(只作说明)

{x['desc_zh']}

## 5. 轻量风格须知

- **提示是不变量**:上面提示写到的画面、色彩、镜头、转场与「避免」条款全片照做。
- **其余由导演补全**:提示没写到的材质、字体与字幕设计、运动质感、镜头语法、声音调色板与媒介原生手法,film-director 在
  TREATMENT.md「风格补全」一节逐项定下并写明依据(从提示与参考作品推出来,不是从别的片子抄),之后全片按补全口径执行。
- **技法参考**:相近的完整风格 {rel or '(无)'}——方案写成后 `python3 code/codevideo.py demo --project <项目> --slug <其中之一>`
  拉它的样片源码学渲染技法(引擎、笔刷、着色器、后期),只学技法,不拿它的故事、镜头与配乐;完整风格的 STYLE.md 在
  agents/workflows/codevideo/styles/<slug>/STYLE.md,可读它的材质 / 运动 / 镜头语法作补全参考。
- **版权红线**:提示与参考里点名的电影、导演、作品只作语法参考,片中不得出现它们的角色、情节、镜头复刻、片名、logo 或配乐;
  不得使用 OPUSCAR 98 的角色(包括 Clawd);片中不带 LemoLab / OPUSCAR 署名。
"""


def demo(base: Path, progress=None, slug: str = "") -> dict:
    """按需下载样片源码与海报。须先有 codevideo/TREATMENT.md(先写方案、后看样片)。
    完整风格默认拉本风格的;轻量风格没有样片,须用 slug 指定一个完整风格(锁定时记的相近风格优先)。"""
    from .fetch import fetch_files
    if not (base / NS / "TREATMENT.md").is_file():
        raise DemoBeforeTreatment("codevideo/TREATMENT.md 还没写:先写导演方案(三个候选结构与选择),再看样片——"
                                  "样片只用来学技法,不能拿它的故事、结构、镜头与配乐")
    lock = lock_style(base)
    target = slug or ("" if lock.get("light") else lock["slug"])
    if not target:
        raise CodeVideoError("轻量风格没有自己的样片:用 --slug 指定一个完整风格拉它的样片学技法,相近的有 "
                             + ("、".join(lock.get("related") or []) or "(无)"))
    if target not in style_slugs():
        raise CodeVideoError(f"--slug 须是完整风格(43 个之一),收到 {target!r}")
    if target == lock["slug"]:
        shutil.copyfile(STYLES_DIR / target / "DEMO.md", base / NS / "style" / "DEMO.md")
    res = fetch_files(f"demo:{target}", progress)
    root = MIRROR / "styles" / target
    return {**res, "slug": target, "demo_dir": str(root / "demo"), "poster": str(root / "poster.jpg"),
            "demo_md": str(base / NS / "style" / "DEMO.md") if target == lock["slug"] else
            str(STYLES_DIR / target / "DEMO.md")}


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
