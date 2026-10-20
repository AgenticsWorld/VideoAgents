"""演示视频(code-video,2026-10-20)的服务端接口实现;路由在 services/api/app.py。

- 「生成模型」页代码作曲 / 代码音效标签页:采样库与 HDRI 包状态 / 后台下载 / 删除,内置示范脚本试听
- 「🖥️ 代码预览」页(/preview/code):片子状态(时间轴、各段渲染新鲜度、混音 / 成片)、后台按段渲染任务、注释与提交修改师

页面本身由 apps/web/server.py 的侧车静态服务(WEB_PORT+2,modules/codevideo/serve.py 预览模式)提供,与控制台不同源。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from modules import codevideo as CV
from services.runtime import core

PACK_JOBS: dict[str, dict] = {}            # 包名 → {status, done, size, error, started_at}
RENDER_JOBS: dict[str, dict] = {}          # 项目 → {status, sections, log[], started_at, finished_at, error, proc}
AUDIO_DEMO_KINDS = ("music", "sfx")
NOTE_MAX = 2000


def _err(code: int, msg: str):
    raise core.ServiceError(code, msg)


# ---------------------------------------------------------------- 采样库 / HDRI 包

def packs_status() -> dict:
    from modules.codevideo import fetch as F
    rows = F.pack_status()
    for r in rows:
        job = PACK_JOBS.get(r["name"]) or {}
        r["job"] = {k: job.get(k) for k in ("status", "done", "size", "error")} if job else None
    return {"packs": rows, "dir": str(CV.MIRROR), "rev": CV.upstream().get("rev"), "repo": CV.upstream().get("repo")}


def _pack_worker(name: str):
    from modules.codevideo import fetch as F
    job = PACK_JOBS[name]

    def prog(done, size):
        job["done"], job["size"] = done, size

    try:
        F.fetch_pack(name, prog)
        job["status"] = "done"
    except Exception as e:  # noqa: BLE001
        job["status"], job["error"] = "error", str(e)[:400]
    job["finished_at"] = time.time()
    core.HUB.publish({"type": "codevideo_pack", "name": name, "status": job["status"], "error": job.get("error", "")})


def packs_fetch(name: str) -> dict:
    from modules.codevideo import fetch as F
    if name not in F.packs():
        _err(404, f"Unknown pack: {name}")
    if (PACK_JOBS.get(name) or {}).get("status") == "running":
        _err(409, f"{name} is already downloading")
    PACK_JOBS[name] = {"status": "running", "done": 0, "size": F.packs()[name]["size"], "error": "", "started_at": time.time()}
    threading.Thread(target=_pack_worker, args=(name,), daemon=True, name=f"codevideo-pack-{name}").start()
    return {"ok": True, "name": name}


def packs_remove(name: str) -> dict:
    from modules.codevideo import fetch as F
    if (PACK_JOBS.get(name) or {}).get("status") == "running":
        _err(409, f"{name} is downloading")
    try:
        return F.remove_pack(name)
    except CV.CodeVideoError as e:
        _err(404, str(e))


# ---------------------------------------------------------------- 试听

def _demo_path(kind: str) -> Path:
    return CV.CACHE_DIR / f"demo_{kind}.wav"


def audio_demo(kind: str) -> dict:
    """跑内置示范脚本(modules/codevideo/demo/<kind>_demo.py),与 Agent 写的脚本走同一条运行链路。"""
    from modules.codevideo import audio as A
    if kind not in AUDIO_DEMO_KINDS:
        _err(404, f"Unknown demo: {kind}")
    out = _demo_path(kind)
    log: list[str] = []
    try:
        st = A.run_script(CV.PKG / "demo" / f"{kind}_demo.py", out, extra_env={"CODEVIDEO_AUTO_FETCH": "0"},
                          log=log.append)
    except CV.CodeVideoError as e:
        _err(500, str(e))
    inst = next((l.split(":", 1)[1].strip() for l in "\n".join(log).splitlines() if l.startswith("demo instrument:")), "")
    return {"ok": True, "url": f"/api/v1/codevideo/audio-demo/{kind}.wav?v={int(out.stat().st_mtime)}",
            "duration_s": st["duration_s"], "peak_db": st["peak_db"], "seconds": st.get("seconds"), "instrument": inst}


def audio_demo_file(kind: str) -> Path:
    p = _demo_path(kind)
    if kind not in AUDIO_DEMO_KINDS or not p.is_file():
        _err(404, "demo not rendered yet")
    return p


# ---------------------------------------------------------------- 代码预览页

def _base(project: str) -> Path:
    base = core._proj_base(project)
    blk = CV.project_block(base)
    if blk.get("type") != "code-video":
        _err(400, "Not a code-video (演示视频) project")
    return base


def preview_info(project: str) -> dict:
    from modules.codevideo import render as R
    from modules.codevideo import timeline as TL
    base = _base(project)
    film = base / CV.NS
    blk = CV.project_block(base)
    out = {"project": base.name, "style": CV.style_row(blk.get("code_style") or "") or {"slug": blk.get("code_style")},
           "settings": {k: blk.get(k) for k in ("storyboard_review", "narration", "subtitles", "length_s", "fps", "music_source")},
           "size": list(CV.render_size(CV.project_settings(base))), "has_page": (film / "index.html").is_file(),
           "page": f"/@film/{base.name}/index.html", "notes": load_notes(base)["notes"]}
    try:
        tl = TL.load(base)
        out["timeline"] = {
            "title": tl.get("title") or "", "dur": tl["dur"], "fps": tl["fps"], "size": tl["size"], "frames": tl["frames"],
            "sections": [{"id": s["id"], "title": s.get("title") or "", "t0": s["t0"], "t1": s["t1"], "group_id": s["group_id"],
                          "scene_no": s["scene_no"],
                          "shots": [{"id": sh["id"], "shot_id": sh["shot_id"], "t0": sh["t0"], "t1": sh["t1"],
                                     "size": sh.get("size") or "", "move": sh.get("move") or "",
                                     "content": sh.get("content") or "", "key": bool(sh.get("key"))} for sh in s["shots"]]}
                         for s in tl["sections"]],
            "hits": [{"t": h["t"], "id": h.get("id") or "", "what": h.get("what") or ""} for h in tl.get("hits") or []],
        }
        out["size"] = tl["size"]
        if out["has_page"]:
            try:
                out["render"] = R.status(base)
            except CV.CodeVideoError as e:
                out["render_error"] = str(e)
    except CV.CodeVideoError as e:
        out["timeline_error"] = str(e)
    from modules.codevideo import beats as BT
    grid = BT.load_beats(base)
    if grid:                                    # 外来配乐的节拍网格(三期):时间轴下方画拍点 / 强拍
        out["beats"] = {"bpm": grid.get("bpm"), "meter": grid.get("meter"), "beats": (grid.get("beats") or [])[:4000],
                        "downbeats": (grid.get("downbeats") or [])[:1000], "confidence": grid.get("confidence")}
    mix = film / "out" / "mix.wav"
    out["mix"] = f"/@film/{base.name}/out/mix.wav?v={int(mix.stat().st_mtime)}" if mix.is_file() else None
    fin = base / "edit" / CV.EP / "final.mp4"
    out["final"] = (f"/api/v1/projects/{base.name}/artifacts/edit/{CV.EP}/final.mp4?v={int(fin.stat().st_mtime)}"
                    if fin.is_file() else None)
    job = RENDER_JOBS.get(base.name) or {}
    out["job"] = {k: job.get(k) for k in ("id", "status", "sections", "started_at", "finished_at", "error")} if job else None
    if job:
        out["job"]["log"] = (job.get("log") or [])[-40:]
    return out


def _render_worker(project: str, cmd: list[str]):
    job = RENDER_JOBS[project]
    try:
        proc = subprocess.Popen(cmd, cwd=str(core.ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                env={**__import__("os").environ, "VIDEOAGENTS_PROJECT": project, "PYTHONUNBUFFERED": "1"})
        job["proc"] = proc
        for line in proc.stdout:
            job["log"].append(line.rstrip()[:400])
            del job["log"][:-400]
        code = proc.wait()
        job["status"] = "done" if code == 0 else ("cancelled" if job.get("cancel") else "error")
        if code:
            job["error"] = next((l for l in reversed(job["log"]) if l.startswith("✗")), f"exit {code}")
    except Exception as e:  # noqa: BLE001
        job["status"], job["error"] = "error", str(e)[:400]
    job["finished_at"] = time.time()
    job.pop("proc", None)
    core.HUB.publish({"type": "codevideo_render", "project": project, "status": job["status"], "error": job.get("error", "")})


def render_start(project: str, body: dict) -> dict:
    base = _base(project)
    job = RENDER_JOBS.get(base.name) or {}
    if job.get("status") == "running":
        _err(409, "A render is already running for this project")
    sections = [str(x) for x in (body or {}).get("sections") or [] if re.fullmatch(r"[A-Za-z0-9_\-]{1,48}", str(x))]
    cmd = [sys.executable, str(CV.CLI), "render", "--project", base.name]
    if sections:
        cmd += ["--section", *sections]
    if (body or {}).get("force"):
        cmd.append("--force")
    jid = uuid.uuid4().hex[:10]
    RENDER_JOBS[base.name] = {"id": jid, "status": "running", "sections": sections, "log": [], "started_at": time.time(),
                              "finished_at": None, "error": ""}
    threading.Thread(target=_render_worker, args=(base.name, cmd), daemon=True, name=f"codevideo-render-{base.name}").start()
    core.HUB.publish({"type": "codevideo_render", "project": base.name, "status": "running"})
    return {"ok": True, "id": jid}


def render_cancel(project: str) -> dict:
    job = RENDER_JOBS.get(core.safe_slug(project)) or {}
    proc = job.get("proc")
    if job.get("status") != "running" or not proc:
        _err(409, "No render is running")
    job["cancel"] = True
    proc.terminate()
    return {"ok": True}


# ---- 注释(代码预览页 🗒):codevideo/notes.json {notes:[{id, t, section, shot, text, created_at, updated_at}]}

def _notes_path(base: Path) -> Path:
    return base / CV.NS / "notes.json"


def load_notes(base: Path) -> dict:
    try:
        d = json.loads(_notes_path(base).read_text(encoding="utf-8"))
        return d if isinstance(d.get("notes"), list) else {"notes": []}
    except (OSError, ValueError):
        return {"notes": []}


def _save_notes(base: Path, d: dict):
    p = _notes_path(base)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def note_set(project: str, body: dict) -> dict:
    """{id?, t, text}:没有 id = 新增;text 为空 = 删除。时间点所在段 / 镜由宿主按 timeline.json 补。"""
    from modules.codevideo import timeline as TL
    base = _base(project)
    body = body or {}
    text = str(body.get("text") or "").strip()[:NOTE_MAX]
    nid = str(body.get("id") or "")
    d = load_notes(base)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    if nid:
        cur = next((n for n in d["notes"] if n.get("id") == nid), None)
        if cur is None:
            _err(404, "note not found")
        if not text:
            d["notes"] = [n for n in d["notes"] if n.get("id") != nid]
        else:
            cur.update(text=text, updated_at=now)
    else:
        if not text:
            _err(400, "empty note")
        try:
            t = max(0.0, float(body.get("t")))
        except (TypeError, ValueError):
            _err(400, "t (seconds) is required")
        sec = shot = ""
        try:
            s, sh = TL.at(TL.load(base), t)
            sec, shot = s["id"], sh["id"]
        except CV.CodeVideoError:
            pass
        d["notes"].append({"id": uuid.uuid4().hex[:8], "t": round(t, 3), "section": sec, "shot": shot, "text": text,
                           "created_at": now, "updated_at": now})
    d["notes"].sort(key=lambda n: n.get("t") or 0)
    _save_notes(base, d)
    return d


async def notes_submit(project: str, body: dict) -> dict:
    """「📨 提交注释」:全部注释一张修改单发修改师(kind=codevideo,代行工位 scene-coder / film-director),派单成功后清空。"""
    from modules.codevideo import timeline as TL
    base = _base(project)
    notes = load_notes(base)["notes"]
    zh = core._notes_submit_zh()
    if not notes:
        _err(400, "没有注释可提交" if zh else "There are no notes to submit")
    try:
        tl = TL.load(base)
        titles = {s["id"]: (s.get("title") or s["id"], s["group_id"], s["t0"], s["t1"]) for s in tl["sections"]}
        shots = {sh["id"]: sh for s in tl["sections"] for sh in s["shots"]}
    except CV.CodeVideoError:
        titles, shots = {}, {}
    lines = []
    for i, n in enumerate(notes, 1):
        sec = titles.get(n.get("section") or "")
        sh = shots.get(n.get("shot") or "")
        where = (f"{n['t']:.2f}s · 段 {n.get('section')}" + (f"「{sec[0]}」({sec[1]},{sec[2]}–{sec[3]}s)" if sec else "")
                 + (f" · 镜 {sh['id']}({sh['shot_id']}:{(sh.get('content') or '')[:60]})" if sh else "")) if zh else \
                (f"{n['t']:.2f}s · section {n.get('section')}" + (f" \"{sec[0]}\" ({sec[1]}, {sec[2]}–{sec[3]}s)" if sec else "")
                 + (f" · shot {sh['id']} ({sh['shot_id']}: {(sh.get('content') or '')[:60]})" if sh else ""))
        lines.append(f"{i}. [{where}]\n   " + ("注释:" if zh else "Note: ") + n["text"].replace("\n", "\n   "))
    rel = f"data/projects/{base.name}/{CV.NS}"
    if zh:
        head = (f"以下是用户在「🖥️ 代码预览」页对演示视频 {base.name} 写的全部注释(共 {len(notes)} 条),一次性交给你改。"
                f"画面是 {rel}/ 下的网页代码(index.html / main.js / js/ / sections/<段 id>.js,页面契约见 agents/workflows/codevideo.md),"
                f"时间轴是 {rel}/timeline.json。逐条改代码(只动需要改的段,段专属改动放 sections/<段 id>.js,这样只重渲这一段);"
                f"改了时长 / 段落要同步 timeline.json 并跑 `python3 code/codevideo.py sync --project {base.name}`。"
                f"改完跑 `python3 code/codevideo.py check --project {base.name} --stage contract,timeline`,再跑 "
                f"`python3 code/codevideo.py render --project {base.name}`(只重渲指纹变了的段);动了声音或时长再跑 mix 与 master。"
                f"落实不了的逐条说明原因。提交时这些注释已从 {rel}/notes.json 清空,以本单为准。")
    else:
        head = (f"Below are all {len(notes)} notes the user wrote on the Code Preview page for the code-video project {base.name}. "
                f"The picture is the web page code under {rel}/ (index.html / main.js / js/ / sections/<section id>.js; page contract "
                f"in agents/workflows/codevideo.md) and the timeline is {rel}/timeline.json. Apply each note in code (keep section-only "
                f"changes in sections/<section id>.js so only that section re-renders); if timings or sections change, update "
                f"timeline.json and run `python3 code/codevideo.py sync --project {base.name}`. Then run "
                f"`python3 code/codevideo.py check --project {base.name} --stage contract,timeline` and "
                f"`python3 code/codevideo.py render --project {base.name}` (re-renders only sections whose fingerprint changed); "
                f"re-run mix and master if sound or timing changed. Explain any note you cannot apply. The notes were cleared from "
                f"{rel}/notes.json on submission; this order is the source of truth.")
    target = {"kind": "codevideo", "id": "", "ep": CV.EP,
              "files": [f"{CV.NS}/timeline.json", f"{CV.NS}/index.html"],
              "label": (f"代码预览 全部注释({len(notes)} 条)" if zh else f"Code preview: all notes ({len(notes)})"),
              "rerun_downstream": bool((body or {}).get("rerun_downstream"))}
    res = await core.api_chat({"agent": core.REVISER_ID, "message": head + "\n\n" + "\n".join(lines), "project": base.name,
                               "target": target, "source": "user"})
    _save_notes(base, {"notes": []})
    return {"ok": True, "run_id": res.get("run_id"), "count": len(notes)}


async def board_notes_submit(base: Path, ep: str, body: dict) -> dict:
    """故事板页「📨 提交注释」在演示视频项目里的走法:storyboard.json 是 codevideo.py sync 从 timeline.json 生成的,
    改它没用;把故事板注释按键(整集 * / 场次 S010 / 镜 S010-02)换算成时刻并进代码预览注释,一起发修改师改代码。"""
    from modules import storyboard_board as sbb
    from modules.codevideo import timeline as TL
    notes = sbb.load_notes(base, ep)["notes"]
    if not notes:
        _err(400, "没有注释可提交" if core._notes_submit_zh() else "There are no notes to submit")
    try:
        tl = TL.load(base)
    except CV.CodeVideoError as e:
        _err(400, str(e))
    secs = {s["scene_no"]: s for s in tl["sections"]}
    d = load_notes(base)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    for key, n in notes.items():
        text = str((n or {}).get("text") or "").strip()
        if not text:
            continue
        t, sec, shot = 0.0, "", ""
        scene_no, _, order = key.partition("-")
        if scene_no in secs:
            s = secs[scene_no]
            sh = next((x for x in s["shots"] if order and str(x["order"]).zfill(2) == order.zfill(2)), None)
            t = (sh["t0"] + sh["t1"]) / 2 if sh else s["t0"]
            sec, shot = s["id"], (sh["id"] if sh else "")
        d["notes"].append({"id": uuid.uuid4().hex[:8], "t": round(t, 3), "section": sec, "shot": shot,
                           "text": (f"[故事板 {key}] " if key != "*" else "[故事板 整集] ") + text,
                           "created_at": now, "updated_at": now})
    d["notes"].sort(key=lambda x: x.get("t") or 0)
    _save_notes(base, d)
    res = await notes_submit(base.name, body)
    sbb.clear_notes(base, ep)
    return {**res, "keys": list(notes), "notes": {}}


async def grp_notes_submit(base: Path, ep: str, body: dict) -> dict:
    """分镜预览页「📨 提交注释」在演示视频项目里的走法:组 = 段,组注释(assets/notes/<ep>/<grp>.json)换算成段起点时刻
    并进代码预览注释,发修改师改代码;提交后删掉组注释文件。"""
    from modules.codevideo import timeline as TL
    ndir = base / "assets" / "notes" / ep
    files = [f for f in sorted(ndir.glob("*.json"))] if ndir.is_dir() else []
    try:
        tl = TL.load(base)
    except CV.CodeVideoError as e:
        _err(400, str(e))
    by_grp = {s["group_id"]: s for s in tl["sections"]}
    d = load_notes(base)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    used = []
    for f in files:
        try:
            text = str((json.loads(f.read_text(encoding="utf-8")) or {}).get("text") or "").strip()
        except (OSError, ValueError):
            continue
        if not text:
            continue
        s = by_grp.get(f.stem)
        d["notes"].append({"id": uuid.uuid4().hex[:8], "t": round(s["t0"], 3) if s else 0.0, "section": s["id"] if s else "",
                           "shot": "", "text": f"[分镜预览 {f.stem}] {text}", "created_at": now, "updated_at": now})
        used.append(f)
    if not used:
        _err(400, "本集没有组注释可提交" if core._notes_submit_zh() else "This episode has no group notes to submit")
    d["notes"].sort(key=lambda x: x.get("t") or 0)
    _save_notes(base, d)
    res = await notes_submit(base.name, body)
    for f in used:
        f.unlink(missing_ok=True)
    return {**res, "groups": [f.stem for f in used]}
