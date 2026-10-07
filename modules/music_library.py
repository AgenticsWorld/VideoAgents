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

主题表与变奏(2026-10-07):台账顶层 `themes[]` = 季级主题动机表 [{theme_id: "T-hero", name, for, motif, mood, created_ep}],
曲目的 `theme_id` 非空 = 主题曲(可跨集原样复用),空 = 一次性 cue(只出本集,不跨集复用);`variant_of` = 本曲是库内某首的
主题变奏(同一动机、换节奏 / 配器 / 强度 / 时长重新生成),入库时继承基曲的 theme_id。

cue sheet 侧契约(09-audio/music SOUL):
  原样复用:"license": {"source": "library", "track_id": "MUS-0003", "origin": "assets/audio/library/music/MUS-0003.mp3"}
  主题变奏:新生成的 cue 顶层写 "variant_of": "MUS-0003"(可选 "theme_id");license 照新生成写
  库非空时顶层写 `music_library_report`:{"consulted": true,
    "reused": [{cue_id, track_id, reason}], "variations": [{cue_id, variant_of, reason}], "generated": [{cue_id, reason}]}
  三种取法的 reason 都必填。

机检 `music_library_synced`(`code/music_library.py check`):本集 cue 的曲目都已入库、`used_in` 与 cue sheet 一致、
library 引用的 track_id 有效、库里先于本集已有别集曲目时 cue sheet 带 `music_library_report` 且每条 cue 给了取法原因;
复用配额(2026-10-07,只对库里先于本集已有别集曲目的集生效):原样复用 ≤ 40% 配乐点(至少放行 1 条)、新生成 + 变奏 ≥ 30%、
同一曲目同一集原样复用 ≤ 1 次、一首曲目原样复用 ≤ 3 集、只准复用主题曲(theme_id 非空)、复用 cue 时长 ≥ 曲目时长 60%、
情绪强度(mood 里的 0–1 数字 / intensity)与曲目相差 ≤ 0.25;存量回补且 cue sheet 未重写的集只 WARN。
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
META_FIELDS = ("title", "mood", "genre", "tempo_bpm", "instrumentation", "intensity", "tags", "description", "prompt",
               "theme_id", "variant_of")
THEME_FIELDS = ("name", "for", "motif", "mood")
# 复用配额(2026-10-07):同一首曲子跨集反复原样铺,听感就是「每集同一首」;配额逼着每集至少有新动静,主题靠变奏延续
REUSE_MAX_RATIO = 0.40          # 原样复用 cue 数 / 本集铺了的 cue 数 ≤ 40%(至少放行 1 条,免得 2 条 cue 的集一条也不能复用)
FRESH_MIN_RATIO = 0.30          # 新生成 + 变奏 ≥ 30%(向上取整)
SAME_TRACK_PER_EP_MAX = 1       # 同一曲目同一集原样复用 ≤ 1 次
TRACK_REUSE_EPS_MAX = 3         # 一首曲目原样复用的集数 ≤ 3(不含来源集)
REUSE_MIN_COVER = 0.60          # 复用 cue 时长 ≥ 曲目时长 60%(28s 的曲掐 6s 用 = 该走变奏按需时长出)
INTENSITY_MAX_DIFF = 0.25       # cue 情绪强度区间与曲目强度区间的间距 ≤ 0.25
_THEME_RE = re.compile(r"^T-[A-Za-z0-9_-]+$")
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
    idx["themes"] = [th for th in (idx.get("themes") or []) if isinstance(th, dict) and th.get("theme_id")]
    for t in idx["tracks"]:
        t.setdefault("status", "active")
        if not isinstance(t.get("used_in"), list):
            t["used_in"] = []
        if not isinstance(t.get("tags"), list):
            t["tags"] = []
        t["theme_id"] = _text(t.get("theme_id")).upper()
        t["variant_of"] = _text(t.get("variant_of")).upper()
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


def find_theme(idx: dict, theme_id: str) -> dict | None:
    tid = _text(theme_id).upper()
    return next((th for th in idx.get("themes") or [] if _text(th.get("theme_id")).upper() == tid), None)


def add_theme(idx: dict, theme_id: str, meta: dict | None = None, created_ep: str = "") -> tuple[dict, bool]:
    """登记 / 更新一个主题动机(theme_id 形如 T-hero)。返回 (theme, created)。"""
    tid = _text(theme_id).upper()
    if not _THEME_RE.match(tid):
        raise ValueError(f"theme_id 须形如 T-hero(字母 / 数字 / - / _):{theme_id}")
    th = find_theme(idx, tid)
    created = th is None
    if created:
        th = {"theme_id": tid, "name": "", "for": "", "motif": "", "mood": "", "created_ep": created_ep or "",
              "created_at": now_iso()}
        idx.setdefault("themes", []).append(th)
    for k in THEME_FIELDS:
        if (meta or {}).get(k) is not None:
            th[k] = _text((meta or {}).get(k))
    return th, created


def theme_tracks(idx: dict, theme_id: str, include_superseded: bool = False) -> list[dict]:
    tid = _text(theme_id).upper()
    return [t for t in idx["tracks"] if t.get("theme_id") == tid and (include_superseded or t.get("status") != "superseded")]


def is_theme_track(track: dict) -> bool:
    return bool(_text(track.get("theme_id")))


def intensity_range(track_or_cue: dict) -> tuple[float, float] | None:
    """情绪强度区间:intensity 数值优先,否则取 mood 文本里 0–1 的数字(「敬畏→好奇,强度 0.20→0.42」→ (0.20, 0.42));没有 = None。"""
    v = _num(track_or_cue.get("intensity"))
    if v is not None and 0 <= v <= 1:
        return (v, v)
    nums = [float(x) for x in re.findall(r"(?<![\d.])(?:0?\.\d+|[01](?:\.\d+)?)(?![\d.])", _text(track_or_cue.get("mood")))]
    nums = [x for x in nums if 0 <= x <= 1]
    return (min(nums), max(nums)) if nums else None


def intensity_gap(a: tuple[float, float] | None, b: tuple[float, float] | None) -> float | None:
    if a is None or b is None:
        return None
    return max(0.0, a[0] - b[1], b[0] - a[1])


def reuse_quota(n_active: int) -> tuple[int, int]:
    """(本集最多可原样复用的 cue 数, 至少要新生成 / 变奏的 cue 数)。"""
    if n_active <= 0:
        return 0, 0
    max_reuse = max(1, int(REUSE_MAX_RATIO * n_active + 1e-9))
    min_fresh = int(-(-FRESH_MIN_RATIO * n_active // 1))      # ceil
    return max_reuse, min_fresh


def _ep_no(ep: str) -> int:
    m = re.search(r"(\d+)", str(ep or ""))
    return int(m.group(1)) if m else 0


def reuse_episodes(track: dict) -> list[str]:
    """原样复用过这首曲目的集(不含来源集),按 used_in 推导。"""
    return sorted({u.get("ep") for u in track.get("used_in") or [] if u.get("reused") and u.get("ep")})


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
        "theme_id": (first("theme_id") or first("theme_id", src=lic)).upper(),
        "variant_of": (first("variant_of") or first("variant_of", src=lic)).upper(),
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


def cue_variant_of(cue: dict) -> str:
    return cue_meta(cue)["variant_of"]


def cue_kind(cue: dict, reused_cues: set | None = None) -> str:
    """配乐点取法:reuse(原样复用库内曲目)/ variation(主题变奏新生成)/ fresh(全新生成)/ user(用户音乐)。
    reused_cues = sync 按内容指纹认出的复用(license 没写 library 也算)。"""
    src = cue_source(cue)
    if src == "library" or (reused_cues and str(cue.get("cue_id")) in reused_cues):
        return "reuse"
    if src == "user_provided":
        return "user"
    return "variation" if cue_variant_of(cue) else "fresh"


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
    rep = {"ep": ep, "added": [], "reused": [], "variations": [], "usage": 0, "missing_files": [], "unknown_tracks": [],
           "unknown_variants": [], "unknown_themes": [], "derived": [], "superseded": [], "changed": False}
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
            meta = cue_meta(cue)
            base = find_track(idx, meta["variant_of"]) if meta["variant_of"] else None
            if meta["variant_of"] and base is None:
                rep["unknown_variants"].append({"cue_id": cue.get("cue_id"), "variant_of": meta["variant_of"]})
            if base is not None and not meta["theme_id"]:
                meta["theme_id"] = _text(base.get("theme_id")).upper()     # 变奏继承基曲的主题
            if meta["theme_id"] and find_theme(idx, meta["theme_id"]) is None:
                rep["unknown_themes"].append({"cue_id": cue.get("cue_id"), "theme_id": meta["theme_id"]})
            track, created = add_track(
                proj, idx, path, meta=meta, write=write, license_=lic,
                origin={"ep": ep, "cue_id": cue.get("cue_id"), "file": rel},
                source="user_provided" if src_kind == "user_provided" else "generated")
            if created:
                rep["added"].append({"track_id": track["track_id"], "cue_id": cue.get("cue_id"), "file": rel})
            else:
                for k, v in meta.items():               # 已在库:只补空字段,不覆盖已有标记
                    if track.get(k) in (None, "", []) and v not in (None, "", []):
                        track[k] = v
            if base is not None and (track.get("origin") or {}).get("ep") == ep:
                rep["variations"].append({"cue_id": cue.get("cue_id"), "track_id": track["track_id"], "variant_of": base["track_id"]})
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
    if rep["unknown_variants"]:
        add("library_variant_valid", "FAIL", "variant_of 在音乐库里查不到:"
            + ", ".join(f"{x['cue_id']}→{x['variant_of']}" for x in rep["unknown_variants"]))
    if rep["unknown_themes"]:
        add("library_theme_valid", "FAIL", "theme_id 不在主题表里(先 music_library.py theme --add):"
            + ", ".join(f"{x['cue_id']}→{x['theme_id']}" for x in rep["unknown_themes"]))
    # 先于本集已在库的别集曲目(按本集首次同步时库里已有到第几首算;本集还没同步过 = 当前全部)
    since = (rec or {}).get("first_track_no")
    if not isinstance(since, int):
        since = last_track_no(idx)
    earlier = [t for t in idx["tracks"] if t.get("status") != "superseded"
               and (t.get("origin") or {}).get("ep") != ep and track_no(t) <= since]
    if not earlier:
        add("library_report", "PASS", "本集之前库里没有别集曲目,无需 music_library_report")
        return _verdict(ep, items)
    # 存量回补的集且 cue sheet 自回补后没被重写过 = 音乐库上线前的产物,只提醒
    soft = bool((rec or {}).get("backfilled")) and \
        (rec or {}).get("backfill_sheet_sha") == sha256_file(proj / BGM_REL / ep / "cue_sheet.json")
    bad = "WARN" if soft else "FAIL"
    report = sheet.get("music_library_report") if isinstance(sheet.get("music_library_report"), dict) else None
    reused_cues = {str(u["cue_id"]) for u in rep["reused"]}
    active = [c for c in cues if is_active(c)]
    kinds = {str(c.get("cue_id")): cue_kind(c, reused_cues) for c in active}
    if report is None or report.get("consulted") is not True:
        add("library_report", bad, f"库里先于本集已有 {len(earlier)} 首别集曲目,cue sheet 须写 music_library_report(consulted: true)"
            + ("(存量回补的集,只提醒)" if soft else ""))
    else:
        def reasons_of(key):
            return {str(g.get("cue_id")): _text(g.get("reason")) for g in (report.get(key) or []) if isinstance(g, dict)}
        gen_r, re_r, var_r = reasons_of("generated"), reasons_of("reused"), reasons_of("variations")
        lacking = [cid for cid, k in kinds.items() if k == "fresh" and not gen_r.get(cid)]
        if lacking:
            add("library_report", bad, "新生成的 cue 未在 music_library_report.generated 写明库内无合适曲目的原因:" + ", ".join(lacking))
        else:
            add("library_report", "PASS")
        lacking = [cid for cid, k in kinds.items() if k == "reuse" and not re_r.get(cid)]
        if lacking:
            add("library_reuse_reason", bad, "原样复用的 cue 未在 music_library_report.reused 写 reason(为什么此处要让这个主题再现):" + ", ".join(lacking))
        else:
            add("library_reuse_reason", "PASS")
        lacking = [cid for cid, k in kinds.items() if k == "variation" and not var_r.get(cid)]
        if lacking:
            add("library_variation_reason", bad, "主题变奏的 cue 未在 music_library_report.variations 写 reason(保留了什么动机、改了什么):" + ", ".join(lacking))
        elif any(k == "variation" for k in kinds.values()):
            add("library_variation_reason", "PASS")
    # 复用配额(2026-10-07)
    n = len(active)
    n_reuse = sum(1 for k in kinds.values() if k == "reuse")
    n_fresh = sum(1 for k in kinds.values() if k in ("fresh", "variation"))
    max_reuse, min_fresh = reuse_quota(n)
    if n_reuse > max_reuse:
        add("library_reuse_quota", bad, f"原样复用 {n_reuse}/{n} 条,超过 {REUSE_MAX_RATIO:.0%} 配额(本集最多 {max_reuse} 条);多出的改走主题变奏或新生成")
    else:
        add("library_reuse_quota", "PASS", f"原样复用 {n_reuse}/{n} 条(上限 {max_reuse})")
    if n and n_fresh < min_fresh:
        add("library_fresh_quota", bad, f"新生成 + 变奏只有 {n_fresh}/{n} 条,少于 {FRESH_MIN_RATIO:.0%}(本集至少 {min_fresh} 条)")
    else:
        add("library_fresh_quota", "PASS", f"新生成 + 变奏 {n_fresh}/{n} 条(下限 {min_fresh})")
    per_track: dict[str, list[str]] = {}
    for c in active:
        if kinds[str(c.get("cue_id"))] != "reuse":
            continue
        tid = cue_track_id(c)
        if not tid:        # license 没写 library、按指纹认出来的:从 trial 里反查
            hit = next((u for u in rep["reused"] if u["cue_id"] == c.get("cue_id")), None)
            tid = hit["track_id"] if hit else ""
        if tid:
            per_track.setdefault(tid, []).append(str(c.get("cue_id")))
    dup = {tid: cids for tid, cids in per_track.items() if len(cids) > SAME_TRACK_PER_EP_MAX}
    if dup:
        add("library_track_once_per_episode", bad, "同一曲目同一集原样复用超过 1 次:"
            + "; ".join(f"{tid}←{', '.join(cids)}" for tid, cids in dup.items()))
    else:
        add("library_track_once_per_episode", "PASS")
    span, no_theme, cover, inten = [], [], [], []
    for tid, cids in per_track.items():
        trk = find_track(trial, tid) or find_track(idx, tid)
        if trk is None:
            continue
        eps = [e for e in reuse_episodes(trk) if _ep_no(e) <= _ep_no(ep)]     # 只数到本集为止,后面的集不追溯本集
        if ep not in eps:
            eps.append(ep)
        if len(eps) > TRACK_REUSE_EPS_MAX:
            span.append(f"{tid} 已在 {', '.join(sorted(eps))} 原样复用")
        if not is_theme_track(trk):
            no_theme.append(f"{tid}←{', '.join(cids)}")
        d_trk = _num(trk.get("duration_s"))
        for c in active:
            if str(c.get("cue_id")) not in cids:
                continue
            d_cue = (_num(c.get("out_s")) or 0) - (_num(c.get("in_s")) or 0)
            if d_trk and d_cue < REUSE_MIN_COVER * d_trk:
                cover.append(f"{c.get('cue_id')} 只用 {d_cue:.1f}s / {tid} 全长 {d_trk:.1f}s")
            gap = intensity_gap(intensity_range(c), intensity_range(trk))
            if gap is not None and gap > INTENSITY_MAX_DIFF + 1e-9:
                cover_r, trk_r = intensity_range(c), intensity_range(trk)
                inten.append(f"{c.get('cue_id')} 强度 {cover_r[0]:.2f}–{cover_r[1]:.2f} vs {tid} {trk_r[0]:.2f}–{trk_r[1]:.2f}")
    add("library_track_reuse_span", bad if span else "PASS",
        ("一首曲目原样复用超过 3 集,后面的集改走变奏:" + "; ".join(span)) if span else "")
    add("library_reuse_theme_only", bad if no_theme else "PASS",
        ("只准原样复用主题曲(theme_id 非空),一次性 cue 不跨集:" + "; ".join(no_theme)
         + " → 确是主题动机就先 music_library.py theme --add + annotate --theme,否则改走变奏 / 新生成") if no_theme else "")
    add("library_reuse_cover", bad if cover else "PASS",
        (f"复用 cue 时长不足曲目的 {REUSE_MIN_COVER:.0%},掐一小段用 = 该按需时长出变奏:" + "; ".join(cover)) if cover else "")
    add("library_reuse_intensity", bad if inten else "PASS",
        (f"复用 cue 的情绪强度与曲目相差超过 {INTENSITY_MAX_DIFF},硬凑:" + "; ".join(inten)) if inten else "")
    return _verdict(ep, items)


def _verdict(ep: str, items: list[dict]) -> dict:
    sts = {i["status"] for i in items}
    return {"check": CHECK_NAME, "episode": ep, "status": "FAIL" if "FAIL" in sts else "WARN" if "WARN" in sts else "PASS",
            "items": items}
