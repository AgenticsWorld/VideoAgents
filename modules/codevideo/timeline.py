"""codevideo/timeline.json:演示视频的唯一事实源(段 / 镜 / 击点 / 台词时点),schema codevideo_timeline/1。

段 = 现有页面里的「场次」与「生成组」:第 i 段 → 场次号 S{i*10:03d}、组号 grp{i*10:04d};全片第 j 镜 → sh{j*10:04d}
(与新建项目的插入位编号制一致)。帧边界累计取整:frame(t) = round(t*fps),段帧数 = frame(t1) − frame(t0),
各段相加恰好等于整片帧数,拼接不丢帧不多帧。

sync() 由它生成 directing/ep01/shot_list.json 与 storyboard.json,分镜预览 / 故事板 / 后期页零改动显示。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import EP, FPS_CHOICES, ID_RE, MAX_DUR_S, NS, CodeVideoError

SCHEMA = "codevideo_timeline/1"
SIZES = ("wide", "full", "medium", "close", "insert", "extreme_close", "extreme_wide")
EPS = 1e-6


def path(base: Path) -> Path:
    return base / NS / "timeline.json"


def frame(t: float, fps: int) -> int:
    return int(round(float(t) * fps + 1e-9))


def load(base: Path) -> dict:
    p = path(base)
    if not p.is_file():
        raise CodeVideoError(f"缺少 {p.relative_to(base)}(cv1-treatment 产出)")
    try:
        tl = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        raise CodeVideoError(f"timeline.json 不是合法 JSON:{e}") from e
    return validate(tl)


def validate(tl: dict) -> dict:
    """校验并补派生字段(frames / group_id / scene_no / shot_id / order)。不合规抛 CodeVideoError(列出全部问题)。"""
    errs: list[str] = []
    if not isinstance(tl, dict):
        raise CodeVideoError("timeline.json 顶层必须是对象")
    if tl.get("schema") not in (None, SCHEMA):
        errs.append(f"schema 应为 {SCHEMA}")
    fps = tl.get("fps", 24)
    if fps not in FPS_CHOICES:
        errs.append(f"fps 只能是 {FPS_CHOICES}")
        fps = 24
    size = tl.get("size")
    if not (isinstance(size, list) and len(size) == 2 and all(isinstance(v, int) and 16 <= v <= 8192 and v % 2 == 0 for v in size)):
        errs.append("size 须为 [宽, 高](16–8192 的偶数)")
    dur = tl.get("dur")
    if not isinstance(dur, (int, float)) or not 0 < dur <= MAX_DUR_S:
        errs.append(f"dur 须为 0–{MAX_DUR_S} 秒")
        dur = 0.0
    secs = tl.get("sections") if isinstance(tl.get("sections"), list) else []
    if not secs:
        errs.append("sections 不能为空")
    seen: set[str] = set()
    prev = 0.0
    for i, s in enumerate(secs):
        if not isinstance(s, dict):
            errs.append(f"sections[{i}] 不是对象")
            continue
        sid = str(s.get("id") or "")
        if not ID_RE.fullmatch(sid) or sid in seen:
            errs.append(f"sections[{i}].id 缺失、重复或含非法字符:{sid!r}")
        seen.add(sid)
        t0, t1 = s.get("t0"), s.get("t1")
        if not all(isinstance(v, (int, float)) for v in (t0, t1)) or t1 <= t0:
            errs.append(f"段 {sid}:t0/t1 须为数字且 t1 > t0")
            continue
        if abs(t0 - prev) > EPS:
            errs.append(f"段 {sid}:t0={t0} 与上一段结尾 {prev} 不相接" if i else f"首段 {sid} 须从 0 开始")
        prev = t1
        if frame(t1, fps) - frame(t0, fps) < 1:
            errs.append(f"段 {sid} 不足 1 帧")
    if secs and dur and abs(prev - dur) > EPS:
        errs.append(f"末段结尾 {prev} ≠ dur {dur}")
    by_sec = {s.get("id"): s for s in secs if isinstance(s, dict)}
    shots = tl.get("shots") if isinstance(tl.get("shots"), list) else []
    shot_ids: set[str] = set()
    per_sec: dict[str, list[dict]] = {}
    for j, sh in enumerate(shots):
        if not isinstance(sh, dict):
            errs.append(f"shots[{j}] 不是对象")
            continue
        hid = str(sh.get("id") or "")
        if not ID_RE.fullmatch(hid) or hid in shot_ids:
            errs.append(f"shots[{j}].id 缺失、重复或含非法字符:{hid!r}")
        shot_ids.add(hid)
        sec = by_sec.get(sh.get("section"))
        if sec is None:
            errs.append(f"镜 {hid}:section {sh.get('section')!r} 不存在")
            continue
        t0, t1 = sh.get("t0"), sh.get("t1")
        if not all(isinstance(v, (int, float)) for v in (t0, t1)) or t1 <= t0:
            errs.append(f"镜 {hid}:t0/t1 须为数字且 t1 > t0")
            continue
        if t0 < sec["t0"] - EPS or t1 > sec["t1"] + EPS:
            errs.append(f"镜 {hid} [{t0}, {t1}] 超出所属段 {sec['id']} [{sec['t0']}, {sec['t1']}]")
        per_sec.setdefault(sec["id"], []).append(sh)
    for sid, rows in per_sec.items():
        rows.sort(key=lambda r: r["t0"])
        sec = by_sec[sid]
        cur = sec["t0"]
        for r in rows:
            if abs(r["t0"] - cur) > EPS:
                errs.append(f"段 {sid} 的镜头没有首尾相接覆盖:{r['id']} 从 {r['t0']} 开始,应为 {cur}")
            cur = r["t1"]
        if abs(cur - sec["t1"]) > EPS:
            errs.append(f"段 {sid} 的镜头没有覆盖到段尾 {sec['t1']}(止于 {cur})")
    for sid in by_sec:
        if shots and sid not in per_sec:
            errs.append(f"段 {sid} 没有镜头")
    for k in ("hits", "lines"):
        for x in tl.get(k) or []:
            if not isinstance(x, dict) or not isinstance(x.get("t"), (int, float)) or not 0 <= x["t"] <= (dur or MAX_DUR_S):
                errs.append(f"{k} 里有条目缺 t 或越界:{json.dumps(x, ensure_ascii=False)[:80]}")
    if errs:
        raise CodeVideoError("timeline.json 不合规:\n  - " + "\n  - ".join(errs[:30]))

    out = dict(tl, fps=fps, schema=SCHEMA)
    out["frames"] = frame(dur, fps)
    order = 0
    norm_secs = []
    for i, s in enumerate(secs, 1):
        f0, f1 = frame(s["t0"], fps), frame(s["t1"], fps)
        rows = []
        for k, sh in enumerate(per_sec.get(s["id"]) or [], 1):
            order += 1
            rows.append(dict(sh, shot_id=f"sh{order * 10:04d}", order=k,
                             f0=frame(sh["t0"], fps), f1=frame(sh["t1"], fps)))
        if not rows:   # 没写镜头表时整段算一镜
            order += 1
            rows.append({"id": f"{s['id']}-1", "section": s["id"], "t0": s["t0"], "t1": s["t1"],
                         "shot_id": f"sh{order * 10:04d}", "order": 1, "f0": f0, "f1": f1})
        norm_secs.append(dict(s, index=i, scene_no=f"S{i * 10:03d}", group_id=f"grp{i * 10:04d}",
                              f0=f0, f1=f1, shots=rows))
    out["sections"] = norm_secs
    return out


def section_of(tl: dict, key: str) -> dict:
    for s in tl["sections"]:
        if key in (s["id"], s["group_id"], s["scene_no"]):
            return s
    raise CodeVideoError(f"没有这一段:{key}(可用段 id / 组号 / 场次号)")


def at(tl: dict, t: float) -> tuple[dict, dict]:
    """t 秒所在的 (段, 镜)。"""
    for s in tl["sections"]:
        if s["t0"] - EPS <= t < s["t1"] - EPS or s is tl["sections"][-1]:
            for sh in s["shots"]:
                if sh["t0"] - EPS <= t < sh["t1"] - EPS:
                    return s, sh
            return s, s["shots"][-1]
    raise CodeVideoError(f"t={t} 不在时间轴内")


def sync(base: Path) -> dict:
    """timeline.json → directing/ep01/shot_list.json + storyboard.json(只覆盖本模块写的版本,带 _meta.source=codevideo)。"""
    tl = load(base)
    fps = tl["fps"]
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    shots, groups, scenes = [], [], []
    for s in tl["sections"]:
        sec_dur = round((s["f1"] - s["f0"]) / fps, 6)
        for sh in s["shots"]:
            shots.append({
                "shot_id": sh["shot_id"], "scene_no": s["scene_no"], "scene_id": "",
                "storyboard_ref": f"{s['scene_no']}/order:{sh['order']}",
                "t_in": round(sh["f0"] / fps, 6), "t_out": round(sh["f1"] / fps, 6),
                "duration_s": round((sh["f1"] - sh["f0"]) / fps, 6),
                "size": sh.get("size") or "", "camera_move": sh.get("move") or "", "angle": sh.get("angle") or "",
                "content": sh.get("content") or "", "why": sh.get("why") or "", "codevideo_id": sh["id"],
            })
        groups.append({"group_id": s["group_id"], "scene_no": s["scene_no"], "scene_id": "",
                       "shots": [sh["shot_id"] for sh in s["shots"]], "total_duration_s": sec_dur,
                       "codevideo_section": s["id"], "title": s.get("title") or "",
                       "frames": s["f1"] - s["f0"], "frame_start": s["f0"],
                       "transition_in": {"type": "cut"}})
        scenes.append({"scene_no": s["scene_no"], "scene_id": "", "location": s.get("title") or s["id"],
                       "scene_note": s.get("note") or "", "alloc_s": sec_dur,
                       "shots_draft": [{"order": sh["order"], "shot_id": sh["shot_id"],
                                        "size_hint": sh.get("size") or "",
                                        "content": sh.get("content") or "",
                                        "sketch": " · ".join(x for x in (sh.get("angle"), sh.get("move")) if x),
                                        "duration_hint_s": round((sh["f1"] - sh["f0"]) / fps, 3),
                                        "notes": sh.get("why") or "", "key": bool(sh.get("key"))}
                                       for sh in s["shots"]],
                       "groups_draft": [{"group_id": s["group_id"], "shot_orders": [sh["order"] for sh in s["shots"]]}]})
    total = round(tl["frames"] / fps, 6)
    meta = {"source": "codevideo", "generated_by": "code/codevideo.py sync", "written_at": now}
    shot_list = {"schema_version": "codevideo/1", "episode": EP, "fps": fps, "size": tl["size"],
                 "total_duration_s": total, "shot_count": len(shots), "group_count": len(groups),
                 "_meta": meta, "shots": shots, "generation_groups": groups}
    storyboard = {"_meta": dict(meta, episode_title=tl.get("title") or "", status="synced"),
                  "title": tl.get("title") or "", "scenes": scenes}
    d = base / "directing" / EP
    d.mkdir(parents=True, exist_ok=True)
    for name, obj in (("shot_list.json", shot_list), ("storyboard.json", storyboard)):
        p = d / name
        if p.is_file():
            try:
                old = json.loads(p.read_text(encoding="utf-8"))
            except ValueError:
                old = {}
            if old and (old.get("_meta") or {}).get("source") != "codevideo":
                raise CodeVideoError(f"{p.relative_to(base)} 不是 codevideo.py sync 写的,不覆盖(先挪走再同步)")
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)
    return {"sections": len(groups), "shots": len(shots), "frames": tl["frames"], "dur": total, "fps": fps,
            "size": tl["size"]}
