# -*- coding: utf-8 -*-
"""项目音乐库(music_library,2026-09-27):每集新增的 BGM 入库并标记数据,之后的剧集先在库里选曲,没有合适的再新生成。

目录:`assets/audio/library/music/`——`index.json` 台账 + 曲目文件 `MUS-NNNN.<ext>`(入库即复制,某集 bgm 目录重跑 / 删除不影响库)。
事实源分工:**曲目与标记数据**以本库 index.json 为准;**谁在用**(`used_in[]`)由各集 `assets/audio/bgm/epNN/cue_sheet.json`
推导,`sync_episode` 每次整集重算,重跑幂等。

台账:
  {"schema_version": 1, "updated_at": "...",
   "episodes": {"ep02": {"first_synced_at": "...", "first_track_no": 4, "synced_at": "...", "backfilled": false,
                         "backfill_sheet_sha": "仅回补的集"}},      # first_track_no = 本集首次对账前库里已有到第几首
   "tracks": [{
     "track_id": "MUS-0003", "file": "MUS-0003.mp3", "sha256": "...", "status": "active" | "superseded",
     "title": "", "mood": "", "genre": "", "tempo_bpm": null, "instrumentation": "", "intensity": null,
     "tags": [], "description": "", "prompt": "", "duration_s": 31.0, "format": {"codec", "sample_rate", "channels"},
     "source": "generated" | "user_provided", "license": {...原始授权链...},
     "origin": {"ep": "ep01", "cue_id": "ep01_bgm_02", "file": "assets/audio/bgm/ep01/ep01_bgm_02.mp3"},
     "added_at": "...",
     "used_in": [{"ep", "cue_id", "in_s", "out_s", "duration_s", "scene", "mood", "file", "reused", "derived"}]}]}

cue sheet 侧契约(09-audio/music SOUL):复用库内曲目的 cue 写
  "license": {"source": "library", "track_id": "MUS-0003", "origin": "assets/audio/library/music/MUS-0003.mp3"}
库非空时 cue sheet 顶层写 `music_library_report`:{"consulted": true, "reused": [{cue_id, track_id}], "generated": [{cue_id, reason}]}。

机检 `music_library_synced`(`code/music_library.py check`):本集 cue 的曲目都已入库、`used_in` 与 cue sheet 一致、
library 引用的 track_id 有效、库里先于本集已有别集曲目时 cue sheet 带 `music_library_report` 且每条新生成的 cue 给了原因。
只依赖标准库 + ffprobe(缺 ffprobe 时时长留空,不报错)。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

LIB_REL = "assets/audio/library/music"
INDEX_NAME = "index.json"
BGM_REL = "assets/audio/bgm"
CHECK_NAME = "music_library_synced"
AUDIO_EXTS = (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus")
META_FIELDS = ("title", "mood", "genre", "tempo_bpm", "instrumentation", "intensity", "tags", "description", "prompt")
_RETIRED = {"retired", "removed", "dropped", "deleted", "cancelled"}
_EP_RE = re.compile(r"^ep\d+$")

_HASH_CACHE: dict[str, tuple[int, int, str]] = {}   # path → (size, mtime_ns, sha256);预览页每次打开都要对账,免重复读盘


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def lib_dir(proj: Path) -> Path:
    return Path(proj) / LIB_REL


def index_path(proj: Path) -> Path:
    return lib_dir(proj) / INDEX_NAME


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def load_index(proj: Path) -> dict:
    idx = _read_json(index_path(proj))
    if not isinstance(idx, dict):
        idx = {}
    idx.setdefault("schema_version", 1)
    idx["tracks"] = [t for t in (idx.get("tracks") or []) if isinstance(t, dict) and t.get("track_id")]
    if not isinstance(idx.get("episodes"), dict):
        idx["episodes"] = {}
    for t in idx["tracks"]:
        t.setdefault("status", "active")
        if not isinstance(t.get("used_in"), list):
            t["used_in"] = []
        if not isinstance(t.get("tags"), list):
            t["tags"] = []
    return idx


def save_index(proj: Path, idx: dict) -> Path:
    path = index_path(proj)
    path.parent.mkdir(parents=True, exist_ok=True)
    idx["updated_at"] = now_iso()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def sha256_file(path: Path) -> str:
    path = Path(path)
    st = path.stat()
    hit = _HASH_CACHE.get(str(path))
    if hit and hit[0] == st.st_size and hit[1] == st.st_mtime_ns:
        return hit[2]
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    _HASH_CACHE[str(path)] = (st.st_size, st.st_mtime_ns, h.hexdigest())
    return h.hexdigest()


def probe(path: Path) -> dict:
    """ffprobe 读时长与规格;没装 ffprobe 或读不出 = 空 dict。"""
    exe = shutil.which("ffprobe")
    if not exe:
        return {}
    try:
        out = subprocess.run([exe, "-v", "error", "-select_streams", "a:0", "-show_entries",
                              "format=duration:stream=codec_name,sample_rate,channels", "-of", "json", str(path)],
                             capture_output=True, text=True, timeout=30)
        j = json.loads(out.stdout or "{}")
    except Exception:  # noqa: BLE001
        return {}
    res: dict = {}
    try:
        res["duration_s"] = round(float((j.get("format") or {}).get("duration")), 3)
    except (TypeError, ValueError):
        pass
    st = (j.get("streams") or [{}])[0]
    fmt = {}
    if st.get("codec_name"):
        fmt["codec"] = st["codec_name"]
    try:
        fmt["sample_rate"] = int(st.get("sample_rate"))
    except (TypeError, ValueError):
        pass
    if isinstance(st.get("channels"), int):
        fmt["channels"] = st["channels"]
    if fmt:
        res["format"] = fmt
    return res


def find_track(idx: dict, track_id: str) -> dict | None:
    tid = str(track_id or "").strip().upper()
    return next((t for t in idx["tracks"] if str(t.get("track_id")).upper() == tid), None)


def find_by_hash(idx: dict, sha: str) -> dict | None:
    return next((t for t in idx["tracks"] if t.get("sha256") == sha), None)


def track_no(track: dict) -> int:
    m = re.match(r"^MUS-(\d+)$", str(track.get("track_id")))
    return int(m.group(1)) if m else 0


def last_track_no(idx: dict) -> int:
    """库里最大的曲目序号(曲目 ID 顺序发放,序号即入库先后;时间戳只到秒,同秒入库分不出先后)。"""
    return max([track_no(t) for t in idx["tracks"]] or [0])


def next_track_id(idx: dict) -> str:
    return f"MUS-{last_track_no(idx) + 1:04d}"


def track_file(proj: Path, track: dict) -> Path:
    return lib_dir(proj) / str(track.get("file") or "")


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, (list, tuple)):
        return " / ".join(_text(x) for x in v if _text(x))
    if isinstance(v, (int, float)):
        return str(v)
    return ""


def cue_meta(cue: dict) -> dict:
    """从 cue 抽标记数据(各项目 cue sheet 字段名不完全一致,逐个别名取第一个非空)。"""
    lic = cue.get("license") if isinstance(cue.get("license"), dict) else {}

    def first(*keys, src=cue):
        for k in keys:
            if _text(src.get(k)):
                return _text(src.get(k))
        return ""

    bpm = None
    for k in ("tempo_bpm", "bpm", "tempo"):
        v = cue.get(k)
        if _num(v):
            bpm = _num(v)
            break
        if isinstance(v, str) and (m := re.search(r"\d+(?:\.\d+)?", v)):
            bpm = float(m.group(0))
            break
    tags = cue.get("tags") if isinstance(cue.get("tags"), list) else []
    return {
        "title": first("title", "name"),
        "mood": first("mood", "emotion"),
        "genre": first("genre", "style"),
        "tempo_bpm": bpm,
        "instrumentation": first("instrumentation", "instruments"),
        "intensity": _num(cue.get("intensity")),
        "tags": [_text(x) for x in tags if _text(x)],
        "description": first("description", "role", "rationale", "notes"),
        "prompt": first("prompt_en", "prompt") or first("prompt", "prompt_en", src=lic),
    }


def is_active(cue: dict) -> bool:
    """在时间线上实际铺了的 cue(退役 / 无入出点的只算素材,不算使用)。"""
    if str(cue.get("status") or "").strip().lower() in _RETIRED:
        return False
    a, b = _num(cue.get("in_s")), _num(cue.get("out_s"))
    return a is not None and b is not None and b > a


def cue_source(cue: dict) -> str:
    lic = cue.get("license") if isinstance(cue.get("license"), dict) else {}
    return str(lic.get("source") or "").strip().lower()


def cue_track_id(cue: dict) -> str:
    lic = cue.get("license") if isinstance(cue.get("license"), dict) else {}
    return str(lic.get("track_id") or cue.get("track_id") or "").strip().upper()


def list_episodes(proj: Path) -> list[str]:
    root = Path(proj) / BGM_REL
    if not root.is_dir():
        return []
    return sorted(d.name for d in root.iterdir() if d.is_dir() and _EP_RE.match(d.name) and (d / "cue_sheet.json").is_file())


def load_cue_sheet(proj: Path, ep: str) -> tuple[dict | None, list[dict]]:
    sheet = _read_json(Path(proj) / BGM_REL / ep / "cue_sheet.json")
    if not isinstance(sheet, dict):
        return None, []
    return sheet, [c for c in (sheet.get("cues") or []) if isinstance(c, dict)]


def add_track(proj: Path, idx: dict, src: Path, meta: dict | None = None, origin: dict | None = None,
              source: str = "generated", license_: dict | None = None, write: bool = True) -> tuple[dict, bool]:
    """入库一首曲目(按内容 sha256 去重)。返回 (track, created)。write=False 只算不落盘(对账预演)。"""
    src = Path(src)
    sha = sha256_file(src)
    hit = find_by_hash(idx, sha)
    if hit:
        return hit, False
    tid = next_track_id(idx)
    ext = src.suffix.lower() if src.suffix.lower() in AUDIO_EXTS else ".mp3"
    track = {"track_id": tid, "file": f"{tid}{ext}", "sha256": sha, "status": "active"}
    meta = meta or {}
    for k in META_FIELDS:
        track[k] = meta.get(k) if meta.get(k) not in (None, "") else ([] if k == "tags" else (None if k in ("tempo_bpm", "intensity") else ""))
    track.update(probe(src))
    track["source"] = source or "generated"
    track["license"] = license_ or {}
    track["origin"] = origin or {}
    track["added_at"] = now_iso()
    track["used_in"] = []
    if write:
        lib_dir(proj).mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, lib_dir(proj) / track["file"])
    idx["tracks"].append(track)
    return track, True


def annotate(track: dict, meta: dict) -> list[str]:
    """改标记数据(显式指定的字段直接覆盖)。返回改动的字段名。"""
    changed = []
    for k in META_FIELDS:
        if k in meta and meta[k] is not None and track.get(k) != meta[k]:
            track[k] = meta[k]
            changed.append(k)
    return changed


def sync_episode(proj: Path, idx: dict, ep: str, write: bool = True, backfill: bool = False) -> dict:
    """按本集 cue sheet 对账:没入库的曲目入库、整集 used_in 重算、本集被替换掉的旧曲标 superseded。

    write=False 只预演(不复制文件;调用方也不要 save_index)。backfill=True = 存量回补:首次对账的集标 backfilled 并记下
    当时 cue sheet 的指纹,机检对其 music_library_report 缺失只 WARN;cue sheet 之后被重写过(指纹变了)即恢复按 FAIL。
    """
    proj = Path(proj)
    rep = {"ep": ep, "added": [], "reused": [], "usage": 0, "missing_files": [], "unknown_tracks": [],
           "derived": [], "superseded": [], "changed": False}
    sheet, cues = load_cue_sheet(proj, ep)
    if sheet is None:
        rep["error"] = "no_cue_sheet"
        return rep
    before = json.dumps([(t["track_id"], t.get("status"), t.get("used_in")) for t in idx["tracks"]], sort_keys=True, ensure_ascii=False)
    had = last_track_no(idx)        # 本集对账前库里已有到第几首
    usage: dict[str, list[dict]] = {}
    referenced: set[str] = set()
    for cue in cues:
        fname = _text(cue.get("file"))
        if not fname:
            continue
        rel = f"{BGM_REL}/{ep}/{fname}"
        path = proj / rel
        src_kind = cue_source(cue)
        track, derived = None, False
        if src_kind == "library" and cue_track_id(cue):
            track = find_track(idx, cue_track_id(cue))
            if track is None:
                rep["unknown_tracks"].append({"cue_id": cue.get("cue_id"), "track_id": cue_track_id(cue)})
                continue
            if path.is_file() and sha256_file(path) != track.get("sha256"):
                derived = True      # 裁剪 / 循环过的派生文件:算这首曲目的使用,不另入库
                rep["derived"].append({"cue_id": cue.get("cue_id"), "track_id": track["track_id"], "file": rel})
        else:
            if not path.is_file():
                if rel not in rep["missing_files"]:
                    rep["missing_files"].append(rel)
                continue
            lic = cue.get("license") if isinstance(cue.get("license"), dict) else {}
            track, created = add_track(
                proj, idx, path, meta=cue_meta(cue), write=write, license_=lic,
                origin={"ep": ep, "cue_id": cue.get("cue_id"), "file": rel},
                source="user_provided" if src_kind == "user_provided" else "generated")
            if created:
                rep["added"].append({"track_id": track["track_id"], "cue_id": cue.get("cue_id"), "file": rel})
            else:
                for k, v in cue_meta(cue).items():       # 已在库:只补空字段,不覆盖已有标记
                    if track.get(k) in (None, "", []) and v not in (None, "", []):
                        track[k] = v
        referenced.add(track["track_id"])
        if track.get("status") == "superseded":
            track["status"] = "active"
        reused = (track.get("origin") or {}).get("ep") != ep
        if reused and not any(r["track_id"] == track["track_id"] and r["cue_id"] == cue.get("cue_id") for r in rep["reused"]):
            rep["reused"].append({"track_id": track["track_id"], "cue_id": cue.get("cue_id")})
        if is_active(cue):
            a, b = _num(cue.get("in_s")), _num(cue.get("out_s"))
            row = {"ep": ep, "cue_id": cue.get("cue_id"), "in_s": a, "out_s": b, "duration_s": round(b - a, 3),
                   "scene": _text(cue.get("scene_id")) or _text(cue.get("scene")), "mood": _text(cue.get("mood")),
                   "file": rel, "reused": reused}
            if derived:
                row["derived"] = True
            usage.setdefault(track["track_id"], []).append(row)
    for t in idx["tracks"]:
        t["used_in"] = [u for u in t["used_in"] if u.get("ep") != ep] + usage.get(t["track_id"], [])
        t["used_in"].sort(key=lambda u: (str(u.get("ep")), u.get("in_s") or 0))
        # 本集重做后被替换掉的旧曲:来源是本集、cue sheet 已不再引用、别集也没用 → 标 superseded(文件保留,选曲默认不列)
        if ((t.get("origin") or {}).get("ep") == ep and t["track_id"] not in referenced
                and not t["used_in"] and t.get("status") != "superseded"):
            t["status"] = "superseded"
            rep["superseded"].append(t["track_id"])
    rep["usage"] = sum(len(v) for v in usage.values())
    after = json.dumps([(t["track_id"], t.get("status"), t.get("used_in")) for t in idx["tracks"]], sort_keys=True, ensure_ascii=False)
    rep["changed"] = before != after
    rec = idx["episodes"].get(ep) if isinstance(idx["episodes"].get(ep), dict) else None
    if rec is None:
        rec = {"first_synced_at": now_iso(), "first_track_no": had, "backfilled": bool(backfill)}
        if backfill:
            rec["backfill_sheet_sha"] = sha256_file(proj / BGM_REL / ep / "cue_sheet.json")
        idx["episodes"][ep] = rec
    rec["synced_at"] = now_iso()
    return rep


def sync_all(proj: Path, idx: dict, eps: list[str] | None = None, write: bool = True, backfill: bool = False) -> list[dict]:
    return [sync_episode(proj, idx, ep, write=write, backfill=backfill) for ep in (eps or list_episodes(proj))]


def pending(proj: Path) -> dict:
    """预演对账:还有多少集 / 曲目没同步进库(预览页提示用,不落盘)。"""
    idx = load_index(proj)
    reps = sync_all(proj, json.loads(json.dumps(idx)), write=False, backfill=True)
    eps = [r["ep"] for r in reps if not r.get("error") and (r["changed"] or r["added"])]
    return {"episodes": eps, "tracks": sum(len(r["added"]) for r in reps)}


def _tokens(text: str) -> list[str]:
    return [w for w in re.split(r"[\s,,、;;/|·→\-—()()\[\]【】::]+", str(text or "").lower()) if w]


def search(idx: dict, query: str = "", mood: str = "", min_duration: float | None = None,
           max_duration: float | None = None, include_superseded: bool = False) -> list[dict]:
    """按关键词给库内曲目打分排序(命中词数;mood 命中加权)。不设关键词 = 全部列出。选不选由配乐 Agent 自己听 / 判断。"""
    q, m = _tokens(query), _tokens(mood)
    rows = []
    for t in idx["tracks"]:
        if t.get("status") == "superseded" and not include_superseded:
            continue
        d = _num(t.get("duration_s"))
        if min_duration is not None and d is not None and d < min_duration:
            continue
        if max_duration is not None and d is not None and d > max_duration:
            continue
        hay = " ".join(_text(t.get(k)) for k in ("title", "mood", "genre", "instrumentation", "description", "prompt", "tags")).lower()
        mood_hay = _text(t.get("mood")).lower()
        score = sum(1 for w in q if w in hay) + sum(2 for w in m if w in mood_hay)
        if (q or m) and not score:
            continue
        rows.append({**t, "score": score})
    rows.sort(key=lambda r: (-r["score"], r["track_id"]))
    return rows


def license_block(track: dict) -> dict:
    """复用库内曲目时写进 cue 的 license 段(原始授权链整段带上,copyright 审核不用再回库里翻)。"""
    return {"source": "library", "track_id": track["track_id"], "origin": f"{LIB_REL}/{track.get('file')}",
            "original": track.get("license") or {}}


def check_episode(proj: Path, ep: str) -> dict:
    """机检 music_library_synced。"""
    proj = Path(proj)
    items: list[dict] = []

    def add(name, status, detail=""):
        items.append({"name": name, "status": status, "detail": detail})

    sheet, cues = load_cue_sheet(proj, ep)
    if sheet is None:
        add("cue_sheet_exists", "FAIL", f"{BGM_REL}/{ep}/cue_sheet.json 不存在或不是合法 JSON")
        return _verdict(ep, items)
    idx = load_index(proj)
    rec = idx["episodes"].get(ep) if isinstance(idx["episodes"].get(ep), dict) else None
    trial = json.loads(json.dumps(idx))
    rep = sync_episode(proj, trial, ep, write=False)
    if rep["missing_files"]:
        add("cue_files_exist", "FAIL", "cue 引用的音频文件不存在:" + ", ".join(rep["missing_files"]))
    else:
        add("cue_files_exist", "PASS")
    if rep["unknown_tracks"]:
        add("library_track_id_valid", "FAIL", "license.track_id 在音乐库里查不到:"
            + ", ".join(f"{x['cue_id']}→{x['track_id']}" for x in rep["unknown_tracks"]))
    else:
        add("library_track_id_valid", "PASS")
    if rec is None or rep["added"] or rep["changed"]:
        why = []
        if rec is None:
            why.append("本集还没同步过")
        if rep["added"]:
            why.append("未入库:" + ", ".join(x["file"] for x in rep["added"]))
        if rep["changed"] and not rep["added"]:
            why.append("used_in 与 cue sheet 不一致")
        add("library_in_sync", "FAIL", ";".join(why) + f" → python3 code/music_library.py sync --ep {ep} --write")
    else:
        add("library_in_sync", "PASS")
    for d in rep["derived"]:
        add("library_file_identical", "WARN", f"{d['cue_id']} 的文件与库内 {d['track_id']} 不是同一份(裁剪 / 循环过的派生文件,须在 cue notes 说明)")
    # 先于本集已在库的别集曲目(按本集首次同步时库里已有到第几首算;本集还没同步过 = 当前全部)
    since = (rec or {}).get("first_track_no")
    if not isinstance(since, int):
        since = last_track_no(idx)
    earlier = [t for t in idx["tracks"] if t.get("status") != "superseded"
               and (t.get("origin") or {}).get("ep") != ep and track_no(t) <= since]
    if not earlier:
        add("library_report", "PASS", "本集之前库里没有别集曲目,无需 music_library_report")
    else:
        # 存量回补的集且 cue sheet 自回补后没被重写过 = 音乐库上线前的产物,只提醒
        soft = bool((rec or {}).get("backfilled")) and \
            (rec or {}).get("backfill_sheet_sha") == sha256_file(proj / BGM_REL / ep / "cue_sheet.json")
        report = sheet.get("music_library_report") if isinstance(sheet.get("music_library_report"), dict) else None
        if report is None or report.get("consulted") is not True:
            add("library_report", "WARN" if soft else "FAIL",
                f"库里先于本集已有 {len(earlier)} 首别集曲目,cue sheet 须写 music_library_report(consulted: true)"
                + ("(存量回补的集,只提醒)" if soft else ""))
        else:
            reasons = {str(g.get("cue_id")): _text(g.get("reason")) for g in (report.get("generated") or []) if isinstance(g, dict)}
            reused_cues = {str(u["cue_id"]) for u in rep["reused"]}
            lacking = [str(c.get("cue_id")) for c in cues
                       if is_active(c) and cue_source(c) not in ("library", "user_provided")
                       and str(c.get("cue_id")) not in reused_cues and not reasons.get(str(c.get("cue_id")))]
            if lacking:
                add("library_report", "WARN" if soft else "FAIL",
                    "新生成的 cue 未在 music_library_report.generated 写明库内无合适曲目的原因:" + ", ".join(lacking))
            else:
                add("library_report", "PASS")
    return _verdict(ep, items)


def _verdict(ep: str, items: list[dict]) -> dict:
    sts = {i["status"] for i in items}
    return {"check": CHECK_NAME, "episode": ep, "status": "FAIL" if "FAIL" in sts else "WARN" if "WARN" in sts else "PASS",
            "items": items}
