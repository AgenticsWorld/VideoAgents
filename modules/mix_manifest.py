# -*- coding: utf-8 -*-
"""mix_manifest — 混音基准台账(WORKFLOW.md §8B ④ / §9B,2026-09-23)。

背景:p8-mix 以前只读 v0 母本 `assets/clips/epNN/grpNNN.mp4`,后期页采纳的版本(删段 / 慢动作 / 插黑定格)
只能在出成片时把混好的整条轨按 timemap 硬切——BGM 在剪点跳拍、旁白被腰斩、慢动作把音乐一起拉慢。
现改为:**混音读各组当前采纳版本**,交付时把「按哪一版混的」盖进清单 `assets/audio/final/epNN.mix.json`;
出成片时宿主比对清单与台账:一致 → 声轨 / 字幕不再套 post_versions 层重映射;不一致 → 旧口径兜底(v0 基准混音)
或停手上报(后期基准混音已过期,须重跑 p8-mix)。

基准指纹只看**组级本地 time_ops**(组内秒,与 timeline 是否存在、组起点怎么累计无关),盖章与核对两端口径一致;
版本号指纹另算(只影响声音内容不影响时轴,失配只 WARN)。
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

try:
    import post_plan as pp
    import timemap
except ImportError:  # 服务端以 modules.* 包路径导入时
    from modules import post_plan as pp
    from modules import timemap

SCHEMA = "mix_basis/1.0"
MANIFEST_SUFFIX = ".mix.json"
CHECK_NAME = "mix_basis_current"
STATUS_NONE, STATUS_CURRENT, STATUS_VERSIONS_CHANGED, STATUS_STALE = "none", "current", "versions_changed", "stale"
DUR_TOL_S = 1.0     # 混音 wav 时长 vs Σ组时长(尾部余量容忍)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def manifest_path(proj: Path, ep: str) -> Path:
    return Path(proj) / "assets" / "audio" / "final" / f"{ep}{MANIFEST_SUFFIX}"


def audio_path(proj: Path, ep: str) -> Path | None:
    d = Path(proj) / "assets" / "audio" / "final"
    for ext in (".wav", ".m4a", ".mp3"):
        p = d / f"{ep}{ext}"
        if p.is_file():
            return p
    return None


def probe_duration(path: Path) -> float:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                             capture_output=True, text=True, timeout=60).stdout.strip()
        return round(float(out), 6) if out else 0.0
    except Exception:  # noqa: BLE001
        return 0.0


def group_order(proj: Path, ep: str) -> list[str]:
    """组序:timeline.json 视频轨优先(剪辑定稿顺序),回退 shot_list.generation_groups 顺序。"""
    proj = Path(proj)
    tl = pp.read_json(proj / "edit" / ep / "timeline.json") or {}
    ids = [t.get("group_id") for t in (((tl.get("tracks") or {}).get("video") or []) if isinstance(tl, dict) else [])
           if isinstance(t, dict) and t.get("group_id")]
    if ids:
        return ids
    sl = pp.read_json(proj / "directing" / ep / "shot_list.json") or {}
    return [g.get("group_id") for g in (sl.get("generation_groups") or []) if isinstance(g, dict) and g.get("group_id")]


def current_basis(proj: Path, ep: str, plan: dict | None = None, groups: list[str] | None = None) -> list[dict]:
    """各组当前指针的基准行:[{group_id, v, src(项目相对路径,可能 None), time_ops(组内秒,母本基准)}]。"""
    proj = Path(proj)
    plan = plan if plan is not None else pp.load_plan(proj, ep)
    rows = []
    for gid in (groups if groups is not None else group_order(proj, ep)):
        f = pp.current_file(proj, ep, gid, plan)
        rows.append({"group_id": gid, "v": pp.current_version(plan, gid),
                     "src": str(f.relative_to(proj)) if f else None,
                     "time_ops": timemap.normalize_ops(pp.effective_time_ops(plan, gid))})
    return rows


def ops_fingerprint(rows: list[dict]) -> str:
    slim = [[r["group_id"], timemap.normalize_ops(r.get("time_ops") or [])] for r in rows]
    return hashlib.sha256(json.dumps(slim, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def versions_fingerprint(rows: list[dict]) -> str:
    slim = [[r["group_id"], int(r.get("v") or 0)] for r in rows]
    return hashlib.sha256(json.dumps(slim, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def basis_delta(rows: list[dict]) -> float:
    return round(sum(timemap.total_delta(r.get("time_ops") or []) for r in rows), 6)


def has_ops(rows: list[dict]) -> bool:
    return any(timemap.normalize_ops(r.get("time_ops") or []) for r in rows)


def with_timing(proj: Path, ep: str, rows: list[dict]) -> list[dict]:
    """补实测时长与两套累计起点:cum_start_s(混音 / 后期基准 = 当前版本实测时长累计)、cum_start_v0_s(母本基准)。"""
    proj = Path(proj)
    cum, cum0 = 0.0, 0.0
    out = []
    for r in rows:
        row = dict(r)
        f = proj / r["src"] if r.get("src") else None
        d = probe_duration(f) if f and f.is_file() else 0.0
        v0 = proj / "assets" / "clips" / ep / f"{r['group_id']}.mp4"
        if int(r.get("v") or 0) > 0:
            d0 = probe_duration(v0) if v0.is_file() else d - timemap.total_delta(r.get("time_ops") or [])
        else:
            d0 = d
        row.update({"duration_s": round(d, 6), "cum_start_s": round(cum, 6),
                    "v0_duration_s": round(d0, 6), "cum_start_v0_s": round(cum0, 6)})
        cum += d
        cum0 += d0
        out.append(row)
    return out


def load_manifest(proj: Path, ep: str) -> dict | None:
    d = pp.read_json(manifest_path(proj, ep))
    return d if isinstance(d, dict) and d.get("schema", "").startswith("mix_basis/") else None


def write_manifest(proj: Path, ep: str, task_id: str, cli: str = "code/mix_basis.py stamp",
                   audio: Path | None = None, plan: dict | None = None) -> tuple[dict, list[str]]:
    """交付盖章:把当前采纳版本基准写进 assets/audio/final/epNN.mix.json。返回 (manifest, warnings)。"""
    proj = Path(proj)
    plan = plan if plan is not None else pp.load_plan(proj, ep)
    rows = with_timing(proj, ep, current_basis(proj, ep, plan))
    audio = Path(audio) if audio else audio_path(proj, ep)
    if audio is None or not audio.is_file():
        raise FileNotFoundError(f"混音产物不存在:assets/audio/final/{ep}.wav")
    warns = []
    missing = [r["group_id"] for r in rows if not r.get("src")]
    if missing:
        warns.append(f"{len(missing)} 组无视频文件(未出片或 skipped):{', '.join(missing[:6])}")
    a_dur = probe_duration(audio)
    total = round(sum(r["duration_s"] for r in rows), 6)
    if abs(a_dur - total) > DUR_TOL_S:
        warns.append(f"混音时长 {a_dur:.3f}s 与 Σ组时长 {total:.3f}s 相差 {abs(a_dur - total):.3f}s(>{DUR_TOL_S:g}s);"
                     "请核对原生轨是否按当前版本逐组拼接")
    n_post = sum(1 for r in rows if int(r.get("v") or 0) > 0)
    man = {"schema": SCHEMA, "episode": ep, "stamped_at": _now(), "task_id": task_id, "cli": cli,
           "basis": "post_versions", "all_v0": n_post == 0,
           "plan_fingerprint": pp.plan_fingerprint(plan), "ops_fingerprint": ops_fingerprint(rows),
           "versions_fingerprint": versions_fingerprint(rows), "delta_s": basis_delta(rows),
           "groups": rows, "groups_on_post_version": n_post,
           "audio": {"file": str(audio.relative_to(proj)) if audio.is_relative_to(proj) else str(audio),
                     "duration_s": a_dur, "fingerprint": pp.file_fingerprint(audio)}}
    pp.write_json(manifest_path(proj, ep), man)
    return man, warns


def compare(proj: Path, ep: str, plan: dict | None = None) -> dict:
    """清单 vs 当前台账。status:none(无清单,旧口径)/ current / versions_changed(时轴同、版本号变)/ stale(时轴变)。
    stale 且 mix_has_ops=False(混音按纯 v0 基准盖章)→ 出成片仍可按 timemap 重映射兜底;mix_has_ops=True → 须重混。"""
    proj = Path(proj)
    plan = plan if plan is not None else pp.load_plan(proj, ep)
    cur = current_basis(proj, ep, plan)
    res = {"status": STATUS_NONE, "cur_ops_fp": ops_fingerprint(cur), "cur_delta_s": basis_delta(cur), "cur_has_ops": has_ops(cur),
           "cur_groups_on_post_version": sum(1 for r in cur if int(r.get("v") or 0) > 0),
           "audio_present": audio_path(proj, ep) is not None, "changed_groups": [], "detail": ""}
    man = load_manifest(proj, ep)
    if not man:
        res["detail"] = ("本集尚无混音产物" if not res["audio_present"] else
                         "混音未盖基准章(旧口径:按 v0 母本混,出成片时按 timemap 重映射)")
        return res
    mrows = man.get("groups") or []
    m_by = {r.get("group_id"): r for r in mrows if isinstance(r, dict)}
    res.update({"stamped_at": man.get("stamped_at"), "task_id": man.get("task_id"), "mix_ops_fp": man.get("ops_fingerprint"),
                "mix_delta_s": float(man.get("delta_s") or 0.0), "mix_has_ops": has_ops(mrows), "mix_all_v0": bool(man.get("all_v0")),
                "audio_fingerprint_ok": None})
    ap = audio_path(proj, ep)
    if ap is not None and (man.get("audio") or {}).get("fingerprint"):
        res["audio_fingerprint_ok"] = pp.file_fingerprint(ap) == man["audio"]["fingerprint"]
    changed = []
    for r in cur:
        m = m_by.get(r["group_id"])
        if m is None:
            changed.append(f"{r['group_id']}(清单缺此组)")
        elif timemap.normalize_ops(m.get("time_ops") or []) != r["time_ops"]:
            changed.append(f"{r['group_id']} v{int(m.get('v') or 0)}→v{r['v']}")
        elif int(m.get("v") or 0) != int(r.get("v") or 0):
            changed.append(f"{r['group_id']} v{int(m.get('v') or 0)}→v{r['v']}(仅版本号)")
    res["changed_groups"] = changed
    if res["cur_ops_fp"] == man.get("ops_fingerprint"):
        if versions_fingerprint(cur) == man.get("versions_fingerprint"):
            res["status"] = STATUS_CURRENT
            res["detail"] = (f"混音基准与当前采纳版本一致({res['cur_groups_on_post_version']} 组在后期版本,Δ{res['cur_delta_s']:+.3f}s,"
                             f"盖章 {man.get('stamped_at')} / {man.get('task_id')})")
        else:
            res["status"] = STATUS_VERSIONS_CHANGED
            res["detail"] = ("采纳版本号变了但时轴未变(声音内容可能不同,时轴无需重映射):" + "; ".join(changed[:6]))
    else:
        res["status"] = STATUS_STALE
        res["detail"] = ("混音基准过期:" + "; ".join(changed[:6]) + ("…" if len(changed) > 6 else "")
                         + (";混音按后期版本基准盖章,须重跑 p8-mix 或回滚采纳" if res["mix_has_ops"]
                            else ";混音按 v0 母本基准盖章,出成片时按 timemap 重映射兜底(旧口径)"))
    if res.get("audio_fingerprint_ok") is False:
        res["detail"] += ";⚠ 混音文件在盖章后被改动(指纹不符)"
    return res


def check_row(res: dict) -> tuple[str, str]:
    """机检口径:(PASS|WARN|FAIL, detail)。stale 且混音带后期时轴 = FAIL;其余失配只 WARN。"""
    st = res.get("status")
    if st == STATUS_CURRENT:
        return "PASS", res.get("detail", "")
    if st == STATUS_STALE and res.get("mix_has_ops"):
        return "FAIL", res.get("detail", "")
    return "WARN", res.get("detail", "")
