"""演示视频机检(code/codevideo.py check --stage …)。每项返回 {id, ok, detail};整组全 ok 才退出 0。

- contract  页面契约:READY / DUR / render 存在;同一 t 渲两次截图一致、乱序渲同帧一致(确定性);渲染时没有外网请求;页面无报错
- timeline  timeline.json 合规(timeline.validate);window.DUR 与 dur 相差 ≤ 1 帧
- clips     每段 assets/clips/ep01/<grp>.mp4 存在、帧数 = 段帧数、无音轨、尺寸一致、指纹新鲜
- final     edit/ep01/final.mp4 存在、帧数 = 整片帧数、无黑帧段(blackdetect ≥ 0.5 s)、响度 −14 ± 1 LUFS、真峰值 ≤ −1 dBTP
- delivery  CREDITS 存在且列出 codevideo/fonts/ 下的字体与用到的采样乐器;build.sh 存在;项目产物里没有 LemoLab / Opus 署名
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import subprocess
from pathlib import Path

from . import EP, NS, CodeVideoError, ffmpeg, ffprobe
from . import timeline as TL

STAGES = ("contract", "timeline", "clips", "final", "delivery")
SIGNOFF_RE = re.compile(r"lemo ?lab|opus 5\.5", re.I)


def _item(cid: str, ok: bool, detail: str = "") -> dict:
    return {"id": cid, "ok": bool(ok), "detail": detail}


def contract(base: Path) -> list[dict]:
    from .render import Session, resolve_size
    out = []
    try:
        size = resolve_size(base, None)
    except CodeVideoError as e:
        return [_item("page_contract", False, str(e))]

    async def go():
        async with Session(base, size) as s:
            fp = await s.page()
            g = await fp.globals()
            dur = g["dur"]
            res = [_item("page_globals", isinstance(dur, (int, float)) and dur > 0 and g["render"],
                         f"DUR={dur} render={g['render']} TEXTS={g['texts']} EV={g['ev']} viewport={g['w']}x{g['h']}")]
            if not (isinstance(dur, (int, float)) and dur > 0):
                return res, fp
            ts = [dur * 0.37, dur * 0.81, dur * 0.12]
            shots = {}
            for t in ts + ts[::-1]:                     # 正序再倒序:第二遍各帧须与第一遍一致
                await fp.render(t)
                h = hashlib.sha256(await fp.png()).hexdigest()
                shots.setdefault(round(t, 4), []).append(h)
            bad = [t for t, hs in shots.items() if len(set(hs)) > 1]
            res.append(_item("deterministic", not bad,
                             "同一时刻两次渲染一致(正序 + 倒序)" if not bad else
                             f"这些时刻两次渲染结果不同(用了 Date.now / 无种子随机 / 依赖上一帧状态?):{bad}"))
            res.append(_item("no_external_requests", not fp.external,
                             "" if not fp.external else "渲染时请求了外网(字体与素材须放进项目):" + ", ".join(fp.external[:5])))
            res.append(_item("no_page_errors", not fp.errors, " | ".join(fp.errors[:3])))
            return res, fp

    try:
        res, _ = asyncio.run(go())
        out += res
    except CodeVideoError as e:
        out.append(_item("page_contract", False, str(e)))
    return out


def timeline_stage(base: Path) -> list[dict]:
    try:
        tl = TL.load(base)
    except CodeVideoError as e:
        return [_item("timeline_valid", False, str(e))]
    out = [_item("timeline_valid", True, f"{len(tl['sections'])} 段 · {sum(len(s['shots']) for s in tl['sections'])} 镜 · "
                                         f"{tl['dur']} s · {tl['size'][0]}x{tl['size'][1]}@{tl['fps']}")]
    sl = base / "directing" / EP / "shot_list.json"
    try:
        synced = json.loads(sl.read_text(encoding="utf-8")).get("total_duration_s")
        out.append(_item("directing_synced", abs(float(synced) - tl["frames"] / tl["fps"]) < 1e-3,
                         "" if synced else "先跑 codevideo.py sync"))
    except (OSError, ValueError, TypeError):
        out.append(_item("directing_synced", False, "directing/ep01/shot_list.json 不存在:先跑 codevideo.py sync"))
    if (base / NS / "index.html").is_file():
        from .render import Session

        async def go():
            async with Session(base, tuple(tl["size"])) as s:
                return (await (await s.page()).globals())["dur"]
        try:
            dur = asyncio.run(go())
            out.append(_item("page_dur_matches", isinstance(dur, (int, float)) and abs(dur - tl["dur"]) <= 1 / tl["fps"] + 1e-6,
                             f"window.DUR={dur} timeline dur={tl['dur']}"))
        except CodeVideoError as e:
            out.append(_item("page_dur_matches", False, str(e)))
    return out


def _probe_video(p: Path) -> dict:
    r = subprocess.run([ffprobe(), "-v", "error", "-count_frames", "-show_entries",
                        "stream=codec_type,width,height,nb_read_frames", "-of", "json", str(p)], capture_output=True, text=True)
    d = json.loads(r.stdout or "{}") if r.returncode == 0 else {}
    vs = [s for s in d.get("streams", []) if s.get("codec_type") == "video"]
    return {"frames": int(vs[0].get("nb_read_frames") or 0) if vs else 0, "w": vs[0].get("width") if vs else None,
            "h": vs[0].get("height") if vs else None,
            "audio": any(s.get("codec_type") == "audio" for s in d.get("streams", []))}


def clips(base: Path) -> list[dict]:
    from .render import clip_paths, status
    try:
        tl = TL.load(base)
        st = {r["section"]: r for r in status(base)}
    except CodeVideoError as e:
        return [_item("clips", False, str(e))]
    out = []
    for s in tl["sections"]:
        mp4, meta, _ = clip_paths(base, s["group_id"])
        if not mp4.is_file():
            out.append(_item(f"clip_{s['id']}", False, f"{mp4.relative_to(base)} 不存在"))
            continue
        pv = _probe_video(mp4)
        want = s["f1"] - s["f0"]
        probs = []
        if pv["frames"] != want:
            probs.append(f"帧数 {pv['frames']} ≠ {want}")
        if pv["audio"]:
            probs.append("带音轨")
        if [pv["w"], pv["h"]] != list(tl["size"]):
            probs.append(f"尺寸 {pv['w']}x{pv['h']} ≠ {tl['size'][0]}x{tl['size'][1]}")
        if st[s["id"]]["state"] != "fresh":
            probs.append("画面已过期(代码或时间轴改过,重跑 render)")
        out.append(_item(f"clip_{s['id']}", not probs, "; ".join(probs) or f"{want} 帧"))
    return out


def final(base: Path, lufs: float = -14) -> list[dict]:
    from .audio import loudness
    out = []
    p = base / "edit" / EP / "final.mp4"
    if not p.is_file():
        return [_item("final_exists", False, "edit/ep01/final.mp4 不存在:先跑 codevideo.py master")]
    try:
        tl = TL.load(base)
    except CodeVideoError as e:
        return [_item("final_exists", True), _item("timeline_valid", False, str(e))]
    pv = _probe_video(p)
    out.append(_item("final_frames", pv["frames"] == tl["frames"], f"{pv['frames']} 帧 / 应为 {tl['frames']}"))
    out.append(_item("final_has_audio", pv["audio"], ""))
    r = subprocess.run([ffmpeg(), "-hide_banner", "-nostats", "-i", str(p), "-vf", "blackdetect=d=0.5:pix_th=0.06",
                        "-an", "-f", "null", "-"], capture_output=True, text=True)
    blacks = re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", r.stderr)
    allowed = {round(float(x["t"]), 1) for x in tl.get("black_ok") or [] if isinstance(x, dict) and "t" in x}
    blacks = [b for b in blacks if round(float(b[0]), 1) not in allowed]
    out.append(_item("no_black_frames", not blacks,
                     "" if not blacks else "黑帧段:" + ", ".join(f"{float(a):.2f}–{float(b):.2f}s" for a, b in blacks[:6])
                     + "(刻意的黑场写进 timeline.json 的 black_ok:[{t}])"))
    m = loudness(p)
    i, tp = m.get("I"), m.get("TP")
    out.append(_item("loudness", i is not None and abs(i - lufs) <= 1, f"{i} LUFS(目标 {lufs} ± 1)"))
    out.append(_item("true_peak", tp is not None and tp <= -1.0, f"{tp} dBTP(≤ −1)"))
    return out


def delivery(base: Path) -> list[dict]:
    film = base / NS
    out = []
    credits = film / "CREDITS"
    text = credits.read_text(encoding="utf-8", errors="replace") if credits.is_file() else ""
    fonts = sorted({p.stem.split("-")[0] for p in (film / "fonts").glob("*") if p.suffix.lower() in (".woff2", ".woff", ".ttf", ".otf")}) \
        if (film / "fonts").is_dir() else []
    missing_fonts = [f for f in fonts if f.lower() not in text.lower()]
    insts = []
    for c in (film / "music").glob("*.credits.json") if (film / "music").is_dir() else []:
        try:
            insts += json.loads(c.read_text()).get("lines") or []
        except ValueError:
            pass
    missing_inst = [l for l in dict.fromkeys(insts) if l.split(":")[0][:30].lower() not in text.lower()]
    out.append(_item("credits_present", bool(text.strip()), "" if text.strip() else "codevideo/CREDITS 不存在或为空"))
    out.append(_item("credits_fonts", not missing_fonts, "" if not missing_fonts else "CREDITS 没写这些字体:" + ", ".join(missing_fonts)))
    out.append(_item("credits_instruments", not missing_inst,
                     "" if not missing_inst else "CREDITS 没写这些采样署名:" + " | ".join(missing_inst[:4])))
    out.append(_item("build_sh", (film / "build.sh").is_file(), ""))
    hits = []
    for p in film.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in (".html", ".js", ".json", ".md", ".css", ".srt", ".txt") \
                or p.name in ("CREDITS",) or "style" in p.relative_to(film).parts[:1]:
            continue
        try:
            if SIGNOFF_RE.search(p.read_text(encoding="utf-8", errors="ignore")):
                hits.append(p.relative_to(base).as_posix())
        except OSError:
            pass
    srt = base / "edit" / EP / "subtitles.srt"
    if srt.is_file() and SIGNOFF_RE.search(srt.read_text(encoding="utf-8", errors="ignore")):
        hits.append(srt.relative_to(base).as_posix())
    out.append(_item("no_library_signoff", not hits, "" if not hits else "这些文件里有 LemoLab / Opus 署名:" + ", ".join(hits[:5])))
    return out


def run(base: Path, stages: list[str]) -> dict:
    fns = {"contract": contract, "timeline": timeline_stage, "clips": clips, "final": final, "delivery": delivery}
    res = {}
    for st in stages:
        res[st] = fns[st](base)
    ok = all(i["ok"] for items in res.values() for i in items)
    return {"ok": ok, "stages": res}
