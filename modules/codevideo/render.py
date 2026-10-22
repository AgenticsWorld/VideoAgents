"""演示视频的出图出片:静帧、联系表、按段渲染(带指纹增量)、关键镜故事板。

逐帧:page.evaluate(render(t)) → JPEG 截图 → 管道交给 ffmpeg(libx264 crf14,中间母本);多个 worker 各开一个浏览器,
一段的帧区间切块并行,块按顺序无损拼接。页面契约要求渲染确定、不依赖上一帧,所以任何帧可由任何 worker 单独渲。

按段增量(代码预览页「渲染改动段」、cv3-render):段指纹 = 共用输入(页面代码、timeline.json、voices/dur.json、字体与图片…)
+ 本段专属文件(sections/<段 id>.* 及其 import 的其它段文件)+ 帧区间 + 尺寸 + 宿主库版本。只改 sections/<段 id>.js
时只重渲这一段;改了引擎(其余文件)全部段都过期。台账 codevideo/render.json。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from modules import file_lock

from . import EP, NS, CodeVideoError, ffmpeg, upstream
from . import timeline as TL
from .page import FilmPage, launch, open_film
from .serve import FilmServer

RENDERER_VERSION = "codevideo-render/1"
# 不进画面指纹的目录 / 文件(产物、审片图、音频与文档)
SKIP_DIRS = {"out", "stills", "sheets", "style", "music", ".cache", "node_modules"}
SKIP_FILES = {"events.json", "render.json", "notes.json", "brief.json", "TREATMENT.md", "CREDITS", "build.sh", "mix.py"}
SKIP_EXT = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac", ".mp4", ".mov", ".py", ".md", ".pyc", ".lock", ".tmp", ".srt"}
SECTION_RE = re.compile(r"^(?:js/)?sections/([A-Za-z0-9][A-Za-z0-9_\-]*)\.[A-Za-z0-9]+$")
IMPORT_RE = re.compile(r"""(?:\bfrom\s*|\bimport\s*\(?\s*)['"]([^'"]+)['"]""")


def workers_default() -> int:
    return max(1, min(3, (os.cpu_count() or 2) // 2))


# ---------------------------------------------------------------- 会话

class Session:
    """一个片子的渲染会话:临时静态服务 + N 个浏览器页面(按需打开,复用到会话结束)。"""

    def __init__(self, base: Path, size: tuple[int, int] | None = None):
        self.base = base
        self.film = base / NS
        if not (self.film / "index.html").is_file():
            raise CodeVideoError(f"{NS}/index.html 不存在:页面还没写(cv3-engine)")
        self.size = size
        self.srv: FilmServer | None = None
        self._pw = self._ctx = None
        self.browsers: list = []
        self.pages: list[FilmPage] = []

    async def __aenter__(self):
        from playwright.async_api import async_playwright
        self.srv = FilmServer(film=self.film).start()
        self._ctx = async_playwright()
        self._pw = await self._ctx.__aenter__()
        return self

    async def __aexit__(self, *a):
        for b in self.browsers:
            try:
                await b.close()
            except Exception:  # noqa: BLE001
                pass
        if self._ctx:
            await self._ctx.__aexit__(*a)
        if self.srv:
            self.srv.stop()

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.srv.port}"

    async def page(self, i: int = 0) -> FilmPage:
        while len(self.pages) <= i:
            b = await launch(self._pw)
            self.browsers.append(b)
            size = self.size or (1920, 1080)
            self.pages.append(await open_film(b, self.srv.url(), size, self.origin))
        return self.pages[i]


def resolve_size(base: Path, size: tuple[int, int] | None) -> tuple[int, int]:
    if size:
        return size
    try:
        return tuple(TL.load(base)["size"])  # type: ignore[return-value]
    except CodeVideoError:
        from . import project_settings, render_size
        return render_size(project_settings(base))


# ---------------------------------------------------------------- 静帧 / 联系表

async def _stills(base: Path, times: list[float], out_dir: Path, size, prefix: str) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    async with Session(base, size) as s:
        fp = await s.page()
        for t in times:
            await fp.render(t)
            p = out_dir / f"{prefix}{t:.2f}.jpg"
            p.write_bytes(await fp.jpeg(92))
            paths.append(p)
    return paths


def stills(base: Path, times: list[float], out_dir: Path | None = None, size=None, prefix: str = "t_") -> list[Path]:
    size = resolve_size(base, size)
    return asyncio.run(_stills(base, times, out_dir or base / NS / "stills", size, prefix))


def sheet(out: Path, images: list[Path], cols: int = 4, width: int = 480, labels: list[str] | None = None) -> Path:
    from PIL import Image, ImageDraw
    if not images:
        raise CodeVideoError("联系表没有图")
    im0 = Image.open(images[0])
    h = width * im0.height // im0.width
    rows = (len(images) + cols - 1) // cols
    canvas = Image.new("RGB", (cols * width, rows * (h + 22)), (20, 20, 24))
    d = ImageDraw.Draw(canvas)
    for k, f in enumerate(images):
        im = Image.open(f).convert("RGB").resize((width, h))
        x, y = (k % cols) * width, (k // cols) * (h + 22)
        canvas.paste(im, (x, y + 22))
        d.text((x + 6, y + 5), (labels[k] if labels else f.stem)[:70], fill=(230, 230, 230))
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, quality=88)
    return out


# ---------------------------------------------------------------- 指纹

def _page_inputs(film: Path) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """(共用输入 {相对路径: sha256}, 段专属 {段 id: {相对路径: sha256}})"""
    shared: dict[str, str] = {}
    per: dict[str, dict[str, str]] = {}
    for p in sorted(film.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(film).as_posix()
        parts = rel.split("/")
        if parts[0] in SKIP_DIRS or parts[0].startswith(("_v", ".")) or rel in SKIP_FILES or p.suffix.lower() in SKIP_EXT:
            continue
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        m = SECTION_RE.match(rel)
        if m:
            per.setdefault(m.group(1), {})[rel] = h
        else:
            shared[rel] = h
    return shared, per


def _section_deps(film: Path, per: dict[str, dict[str, str]], sid: str) -> set[str]:
    """段 sid 的专属文件加上它(传递地)import 的其它段文件。"""
    todo, seen = [sid], set()
    while todo:
        cur = todo.pop()
        if cur in seen:
            continue
        seen.add(cur)
        for rel in per.get(cur, {}):
            try:
                text = (film / rel).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for spec in IMPORT_RE.findall(text):
                if not spec.startswith("."):
                    continue
                tgt = (film / rel).parent.joinpath(spec).resolve()
                try:
                    trel = tgt.relative_to(film.resolve()).as_posix()
                except ValueError:
                    continue
                m = SECTION_RE.match(trel)
                if m and m.group(1) not in seen:
                    todo.append(m.group(1))
    return seen


def fingerprints(base: Path, tl: dict | None = None) -> dict[str, str]:
    tl = tl or TL.load(base)
    film = base / NS
    shared, per = _page_inputs(film)
    common = json.dumps({"v": RENDERER_VERSION, "lib": upstream().get("rev"), "size": tl["size"], "fps": tl["fps"],
                         "shared": shared}, sort_keys=True)
    out = {}
    for s in tl["sections"]:
        deps = _section_deps(film, per, s["id"])
        own = {rel: h for d in sorted(deps) for rel, h in per.get(d, {}).items()}
        blob = common + json.dumps({"own": own, "f": [s["f0"], s["f1"]]}, sort_keys=True)
        out[s["id"]] = hashlib.sha256(blob.encode()).hexdigest()[:20]
    return out


def ledger_path(base: Path) -> Path:
    return base / NS / "render.json"


def load_ledger(base: Path) -> dict:
    try:
        d = json.loads(ledger_path(base).read_text(encoding="utf-8"))
        return d if isinstance(d.get("sections"), dict) else {"sections": {}}
    except (OSError, ValueError):
        return {"sections": {}}


def clip_paths(base: Path, group_id: str) -> tuple[Path, Path, Path]:
    d = base / "assets" / "clips" / EP
    return d / f"{group_id}.mp4", d / f"{group_id}.meta.json", d / f"{group_id}.last_frame.png"


def status(base: Path) -> list[dict]:
    """每段渲染状态:fresh(已渲且指纹一致)/ stale(代码或时间改了)/ missing。"""
    tl = TL.load(base)
    fps_now = fingerprints(base, tl)
    led = load_ledger(base)["sections"]
    rows = []
    for s in tl["sections"]:
        mp4, _, _ = clip_paths(base, s["group_id"])
        rec = led.get(s["id"]) or {}
        st = "missing" if not mp4.is_file() else ("fresh" if rec.get("fingerprint") == fps_now[s["id"]] else "stale")
        rows.append({"section": s["id"], "group_id": s["group_id"], "scene_no": s["scene_no"], "title": s.get("title") or "",
                     "t0": s["t0"], "t1": s["t1"], "frames": s["f1"] - s["f0"], "state": st,
                     "rendered_at": rec.get("rendered_at"), "seconds": rec.get("seconds"),
                     "clip": mp4.relative_to(base).as_posix() if mp4.is_file() else None})
    return rows


# ---------------------------------------------------------------- 编码

async def _encode(fp: FilmPage, frames: range, fps: int, out: Path, progress=None):
    cmd = [ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(fps),
           "-c:v", "mjpeg", "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "14",
           "-pix_fmt", "yuv420p", "-r", str(fps), "-an", str(out)]
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=err)
        try:
            for f in frames:
                await fp.render(f / fps)
                buf = await fp.jpeg(95)
                try:
                    await asyncio.to_thread(proc.stdin.write, buf)
                except BrokenPipeError:
                    break
                if progress:
                    progress(1)
            proc.stdin.close()
            code = await asyncio.to_thread(proc.wait)
        except BaseException:
            proc.kill()
            raise
        if code != 0:
            err.seek(0)
            raise CodeVideoError(f"ffmpeg 编码失败:{err.read().decode(errors='replace')[-800:]}")


def _concat(parts: list[Path], out: Path):
    if len(parts) == 1:
        parts[0].replace(out)
        return
    lst = out.with_suffix(".concat.txt")
    lst.write_text("".join(f"file '{p.name}'\n" for p in parts))
    try:
        r = subprocess.run([ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
                            "-i", lst.name, "-c", "copy", out.name], cwd=out.parent, capture_output=True, text=True)
        if r.returncode:
            raise CodeVideoError(f"ffmpeg 拼接失败:{r.stderr[-600:]}")
    finally:
        lst.unlink(missing_ok=True)
        for p in parts:
            p.unlink(missing_ok=True)


async def _render_sections(base: Path, tl: dict, todo: list[dict], workers: int, log) -> list[dict]:
    fps = tl["fps"]
    done = []
    async with Session(base, tuple(tl["size"])) as s:
        pages = [await s.page(i) for i in range(workers)]
        g = await pages[0].globals()
        if not isinstance(g["dur"], (int, float)) or abs(g["dur"] - tl["dur"]) > 1.0 / fps + 1e-6:
            raise CodeVideoError(f"window.DUR={g['dur']} 与 timeline.json dur={tl['dur']} 不一致(差超过 1 帧)")
        for sec in todo:
            t_start = time.time()
            mp4, meta, last = clip_paths(base, sec["group_id"])
            mp4.parent.mkdir(parents=True, exist_ok=True)
            n = sec["f1"] - sec["f0"]
            k = max(1, min(workers, n // max(1, fps)))       # 不足 1 秒一块的段不拆
            step = -(-n // k)
            stage = Path(tempfile.mkdtemp(prefix=f".{sec['group_id']}-", dir=mp4.parent))
            try:
                parts = [stage / f"part{i}.mp4" for i in range(k)]
                count = [0]

                def tick(_):
                    count[0] += 1
                    if count[0] % (fps * 2) == 0 or count[0] == n:
                        log(f"  {sec['id']} {count[0]}/{n} 帧")

                await asyncio.gather(*[_encode(pages[i], range(sec["f0"] + i * step, min(sec["f1"], sec["f0"] + (i + 1) * step)),
                                               fps, parts[i], tick) for i in range(k)])
                out_tmp = stage / "section.mp4"
                _concat(parts, out_tmp)
                await pages[0].render((sec["f1"] - 1) / fps)
                (stage / "last.png").write_bytes(await pages[0].png())
                out_tmp.replace(mp4)
                (stage / "last.png").replace(last)
            finally:
                for p in stage.glob("*"):
                    p.unlink(missing_ok=True)
                stage.rmdir()
            rec = {"generator": "codevideo", "renderer": RENDERER_VERSION, "section": sec["id"], "group_id": sec["group_id"],
                   "fps": fps, "size": tl["size"], "frames": n, "frame_start": sec["f0"], "frame_end": sec["f1"],
                   "duration_s": round(n / fps, 6), "audio": False,
                   "boundary_map": [{"shot_id": sh["shot_id"], "start_s": round((sh["f0"] - sec["f0"]) / fps, 6),
                                     "end_s": round((sh["f1"] - sec["f0"]) / fps, 6)} for sh in sec["shots"]],
                   "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "seconds": round(time.time() - t_start, 1)}
            meta.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
            done.append(rec)
            log(f"✓ {sec['id']} → {mp4.relative_to(base)}({n} 帧,{rec['seconds']} s)")
        warn = [w for p in pages for w in p.warnings]
        if warn:
            log("页面警告:" + " | ".join(list(dict.fromkeys(warn))[:5]))
    return done


def render(base: Path, sections: list[str] | None = None, force: bool = False, workers: int | None = None,
           log=print) -> dict:
    tl = TL.load(base)
    fps_now = fingerprints(base, tl)
    st = {r["section"]: r for r in status(base)}
    if sections:
        todo = [TL.section_of(tl, k) for k in sections]
        if not force:
            todo = [s for s in todo if st[s["id"]]["state"] != "fresh"]
    else:
        todo = [s for s in tl["sections"] if force or st[s["id"]]["state"] != "fresh"]
    skipped = [s["id"] for s in tl["sections"] if s not in todo]
    if not todo:
        log("所有段都是最新的,没有要渲染的(--force 强制重渲)")
        return {"rendered": [], "skipped": skipped}
    w = max(1, min(6, workers or workers_default()))
    log(f"渲染 {len(todo)} 段({', '.join(s['id'] for s in todo)}),{w} 个 worker,{tl['size'][0]}x{tl['size'][1]}@{tl['fps']}")
    done = asyncio.run(_render_sections(base, tl, todo, w, log))
    # 台账读-改-写加锁、原子落盘:cv3-sec 分段工单并行 `render --section` 时各自只记自己的段,不互相覆盖
    path = ledger_path(base)
    with file_lock.locked(path.with_name(".render.json.lock")):
        led = load_ledger(base)
        for rec in done:
            led["sections"][rec["section"]] = {"group_id": rec["group_id"], "fingerprint": fps_now[rec["section"]],
                                               "frames": rec["frames"], "rendered_at": rec["generated_at"],
                                               "seconds": rec["seconds"]}
        led["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        tmp = path.with_name(f".render.json.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(led, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    return {"rendered": [r["section"] for r in done], "skipped": skipped}


# ---------------------------------------------------------------- 故事板(真代码关键帧)

def board_shots(tl: dict, keys: list[str] | None = None) -> list[tuple[dict, dict]]:
    allshots = [(s, sh) for s in tl["sections"] for sh in s["shots"]]
    if keys:
        pick = [(s, sh) for s, sh in allshots if sh["id"] in keys or sh["shot_id"] in keys]
        if not pick:
            raise CodeVideoError("--keys 没有命中任何镜头(用 timeline 镜 id 或 shNNNN)")
        return pick
    marked = [(s, sh) for s, sh in allshots if sh.get("key")]
    if marked:
        return marked
    n = len(allshots)
    want = n if n <= 9 else 9
    return [allshots[round(i * (n - 1) / max(1, want - 1))] for i in range(want)] if want > 1 else allshots


def board(base: Path, keys: list[str] | None = None, log=print) -> dict:
    from modules import storyboard_board as sbb
    tl = TL.load(base)
    TL.sync(base)
    pick = board_shots(tl, keys)
    d = sbb.sketch_dir(base, EP)
    d.mkdir(parents=True, exist_ok=True)
    style = (base / NS / "style" / "lock.json")
    slug = json.loads(style.read_text()).get("slug") if style.is_file() else ""

    async def go():
        out = []
        async with Session(base, tuple(tl["size"])) as s:
            fp = await s.page()
            for sec, sh in pick:
                t = (sh["f0"] + sh["f1"]) / 2 / tl["fps"]
                await fp.render(t)
                key = sbb.shot_key(sec["scene_no"], sh["order"])
                img = d / f"{key}.jpg"
                img.write_bytes(await fp.jpeg(90))
                out.append((key, img, t, sec, sh))
        return out

    rows = asyncio.run(go())
    for key, img, t, sec, sh in rows:
        sbb.update_index(base, EP, key, {"status": "done", "file": img.relative_to(base).as_posix(), "provider": "code",
                                         "model": slug or "codevideo", "mode": "code", "t": round(t, 3),
                                         "note": f"{sec['id']}/{sh['id']} @ {t:.2f}s"})
    labels = [f"{sec['scene_no']} {sh['shot_id']} · {sh.get('size') or ''} · {(sh['f1'] - sh['f0']) / tl['fps']:.1f}s · "
              f"{(sh.get('content') or '')[:40]}" for _, _, _, sec, sh in rows]
    sh_path = sheet(d / "board_sheet.jpg", [r[1] for r in rows], cols=3, width=560, labels=labels)
    log(f"✓ 故事板 {len(rows)} 镜 → {d.relative_to(base)}/(联系表 {sh_path.relative_to(base)})")
    return {"shots": [r[0] for r in rows], "sheet": sh_path.relative_to(base).as_posix()}


# ---------------------------------------------------------------- 事件 / 阅读时长

def events(base: Path) -> dict:
    size = resolve_size(base, None)

    async def go():
        async with Session(base, size) as s:
            fp = await s.page()
            return await fp.page.evaluate("() => ({dur: window.DUR, ev: window.EV || []})")

    ev = asyncio.run(go())
    p = base / NS / "events.json"
    p.write_text(json.dumps(ev, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return {"dur": ev["dur"], "events": len(ev["ev"]), "path": p.relative_to(base).as_posix()}


_IS_CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯豈-﫿]")


def readcheck(base: Path, step: float = 0.04, latin_cps: float = 15, cjk_cps: float = 4.5, pad: float = 1.5,
              minimum: float = 1.5) -> dict:
    """同上游 readcheck.mjs:每段文字从第一次完整进画起连续在画的时长 ≥ 汉字÷4.5 + 其它÷15 + 1.5 s(且 ≥ 1.5 s)。"""
    size = resolve_size(base, None)

    async def go():
        async with Session(base, size) as s:
            fp = await s.page()
            return await fp.page.evaluate("""(STEP) => {
              if (typeof window.TEXTS !== 'function') return {noTexts: true};
              const W = innerWidth, H = innerHeight, seen = {}, partial = {};
              for (let t = 0; t <= window.DUR; t += STEP) {
                window.render(t); const vis = new Set();
                for (const b of window.TEXTS(t) || []) {
                  b.id = String(b.id); const key = b.id + '\\u0000' + b.text;
                  if (!(b.x0 >= 0 && b.y0 >= 0 && b.x1 <= W && b.y1 <= H)) { partial[key] ??= {id: b.id, text: b.text, t0: t}; continue; }
                  vis.add(key); const s = seen[key] ??= {id: b.id, text: b.text, t0: t, run: 0, done: false};
                  if (!s.done) s.run = Math.min(t + STEP, window.DUR) - s.t0;
                }
                for (const k in seen) if (!vis.has(k)) seen[k].done = true;
              }
              for (const k in partial) if (seen[k]) delete partial[k];
              return {seen: Object.values(seen), partial: Object.values(partial)};
            }""", step)

    res = asyncio.run(go())
    if res.get("noTexts") or not (res.get("seen") or res.get("partial")):
        return {"checked": False, "reason": "页面没有 window.TEXTS(t)" if res.get("noTexts") else "TEXTS 从未返回文字",
                "bad": [], "ok": []}
    bad, ok = [], []
    for s in res["seen"]:
        chars = [c for c in s["text"] if not c.isspace()]
        cjk = sum(1 for c in chars if _IS_CJK.match(c))
        need = max(minimum, cjk / cjk_cps + (len(chars) - cjk) / latin_cps + pad)
        row = {"id": s["id"], "t0": round(s["t0"], 2), "need": round(need, 2), "got": round(s["run"], 2), "text": s["text"][:40]}
        (ok if s["run"] >= need - 1e-6 else bad).append(row)
    for s in res["partial"]:
        bad.append({"id": s["id"], "t0": round(s["t0"], 2), "need": None, "got": None, "text": s["text"][:40],
                    "reason": "never fully visible"})
    return {"checked": True, "bad": bad, "ok": ok}

