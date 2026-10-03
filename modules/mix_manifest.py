# -*- coding: utf-8 -*-
"""mix_manifest — 混音基准台账(WORKFLOW.md §8B ④ / §9B,2026-09-23)。

背景:p8-mix 以前只读 v0 母本 `assets/clips/epNN/grpNNN.mp4`,后期页采纳的版本(删段 / 慢动作 / 插黑定格)
只能在出成片时把混好的整条轨按 timemap 硬切——BGM 在剪点跳拍、旁白被腰斩、慢动作把音乐一起拉慢。
现改为:**混音读各组当前采纳版本**,交付时把「按哪一版混的」盖进清单 `assets/audio/final/epNN.mix.json`;
出成片时宿主比对清单与台账:一致 → 声轨 / 字幕不再套 post_versions 层重映射;不一致 → 旧口径兜底(v0 基准混音)
或停手上报(后期基准混音已过期,须重跑 p8-mix)。

基准指纹只看**组级本地 time_ops**(组内秒,与 timeline 是否存在、组起点怎么累计无关),盖章与核对两端口径一致;
版本号指纹另算(只影响声音内容不影响时轴,失配只 WARN)。

组边界层(过场设计,2026-09-24;前科 fengshen3 ep06:字卡处 BGM 断 2.5 s):shot_list `transition_in` 的定格 / 黑场停留 /
插入段(字卡、定场空镜、时光流转、桥接)会在组边界**插入**时长,以前混音不知道这层,finalize 按 timemap 把混好的整条轨切开填静音,
BGM 一起断。现在 `sources` 直接给出边界层(帧量化,与 render_transitions.pad_ops 同口径)且 `cum_start_s` 已含它:混音把 BGM / 旁白
铺在**带过场的最终时间线**上(跨越插入段的 cue 连续播),原生轨在边界处按策略留白;盖章记边界指纹,finalize 一致时**不再**对声轨套
边界层重映射(字幕仍按表平移);边界层改了 = 须重跑 p8-mix。

后期配音(§8C,2026-10-03):p7-dub 原地改写 v0 母本的对白轨并写 `assets/audio/voice/epNN/dub/grpNNN/dub_manifest.json`。
`sources` 每组另给 `dub_fp`(配音时刻 + 逐句时段指纹,没配音 = null)盖进清单,重配音后未重混 = FAIL(sound_changed 同级);
`dub_predates_version`:当前采纳版本链的根版本建于配音之前(从旧母本派生,采纳文件里没有配音)= FAIL,须回滚或重做该版本再重混。
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
BOUNDARY_FPS_DEFAULT = 24.0
# 边界层状态:current(盖章边界层 = 当前 shot_list)/ stale(变了)/ absent(旧清单无边界层但当前有边界插入)/ none(两边都没有边界插入)
BND_CURRENT, BND_STALE, BND_ABSENT, BND_NONE = "current", "stale", "absent", "none"


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


def dub_manifest(proj: Path, ep: str, gid: str) -> dict | None:
    d = pp.read_json(Path(proj) / "assets" / "audio" / "voice" / ep / "dub" / gid / "dub_manifest.json")
    return d if isinstance(d, dict) and d.get("dubbed_at") else None


def dub_fingerprint(man: dict | None) -> str | None:
    """后期配音产物指纹:配音时刻 + 逐句 (起点, 贴合时长) + 去人声状态;重配音 / 改时段 / 改去人声都会变。"""
    if not man:
        return None
    slim = [man.get("dubbed_at"), (man.get("vocal_removal") or {}).get("status"),
            [[e.get("line"), (e.get("segment") or {}).get("start"), e.get("fit_duration_s")] for e in (man.get("lines") or []) if isinstance(e, dict)]]
    return hashlib.sha256(json.dumps(slim, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def dub_predates_version(plan: dict, gid: str, man: dict | None) -> bool:
    """当前采纳版本(v>0)的版本链根(base_v=0 的那一版)是否建于配音之前:是 = 它从没配音的旧母本派生,文件里没有配音。"""
    if not man:
        return False
    v = pp.current_version(plan, gid)
    dubbed_at = str(man.get("dubbed_at") or "").replace("T", " ")
    seen = set()
    while v > 0 and v not in seen:
        seen.add(v)
        ver = next((x for x in pp.group_versions(plan, gid) if int(x.get("v") or 0) == v), None)
        if not ver:
            return False
        if int(ver.get("base_v") or 0) <= 0:
            created = str(ver.get("created_at") or "").replace("T", " ")
            return bool(created) and bool(dubbed_at) and created < dubbed_at
        v = int(ver.get("base_v") or 0)
    return False


def current_basis(proj: Path, ep: str, plan: dict | None = None, groups: list[str] | None = None) -> list[dict]:
    """各组当前指针的基准行:[{group_id, v, src(项目相对路径,可能 None), time_ops(组内秒,母本基准)}]。"""
    proj = Path(proj)
    plan = plan if plan is not None else pp.load_plan(proj, ep)
    rows = []
    for gid in (groups if groups is not None else group_order(proj, ep)):
        f = pp.current_file(proj, ep, gid, plan)
        dm = dub_manifest(proj, ep, gid)
        rows.append({"group_id": gid, "v": pp.current_version(plan, gid),
                     "sound_v": pp.sound_version(plan, gid),      # 原生声轨内容所在版本(去人声 / 去环境声改过才 > 0)
                     "dub_fp": dub_fingerprint(dm),               # 后期配音指纹(§8C;未配音 = None)
                     "dub_predates_version": dub_predates_version(plan, gid, dm),
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


def _probe_fps(path: Path) -> float:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate",
                              "-of", "csv=p=0", str(path)], capture_output=True, text=True, timeout=60).stdout.strip()
        n, d = out.split("/")
        f = float(n) / float(d)
        return f if f > 0 else BOUNDARY_FPS_DEFAULT
    except Exception:  # noqa: BLE001
        return BOUNDARY_FPS_DEFAULT


def _q(x, fps: float) -> float:
    """秒 → 帧量化秒(与 render_transitions._frames 同口径:round(x×fps)/fps)。"""
    try:
        return round(int(round(float(x or 0.0) * fps)) / fps, 6)
    except (TypeError, ValueError):
        return 0.0


def boundary_layer(proj: Path, ep: str, rows: list[dict], fps: float | None = None) -> list[dict]:
    """组边界层:按 shot_list transition_in(hold_s / freeze_s / inserts[])算每个边界在混音时间线上占的秒数(帧量化)。
    返回按组序的 [{from_group, to_group, freeze_s, hold_s, insert_s, total_s, audio, type, inserts:[{kind,duration_s}]}],
    只含 total_s > 0 的边界;顺序:前组尾 → 定格 → 黑场 → 插入段 → 本组首。audio = 原生轨在该边界的留白策略(hold_audio / 插入段 audio)。"""
    proj = Path(proj)
    sl = pp.read_json(proj / "directing" / ep / "shot_list.json") or {}
    by = {g.get("group_id"): g for g in (sl.get("generation_groups") or []) if isinstance(g, dict)}
    if fps is None:
        first = next((proj / r["src"] for r in rows if r.get("src") and (proj / r["src"]).is_file()), None)
        fps = _probe_fps(first) if first else BOUNDARY_FPS_DEFAULT
    out = []
    for a, b in zip(rows, rows[1:]):
        g = by.get(b["group_id"]) or {}
        t = g.get("transition_in") if isinstance(g.get("transition_in"), dict) else {}
        fz, hd = _q(t.get("freeze_s"), fps), _q(t.get("hold_s"), fps)
        ins = [x for x in (t.get("inserts") or []) if isinstance(x, dict) and x.get("kind")]
        ins_rows = [{"kind": x.get("kind"), "duration_s": max(2.0 / fps, _q(x.get("duration_s"), fps)) if float(x.get("duration_s") or 0) > 0 else 0.0} for x in ins]
        ins_s = round(sum(x["duration_s"] for x in ins_rows), 6)
        total = round(fz + hd + ins_s, 6)
        # 四期(2026-09-26):音先入 audio_lead_s(J-cut)——本组原生轨比画面早 lead 秒进入,压在前组尾画面上;不占时、不进 timemap,
        # 只影响混音摆位,故边界层也列出 total_s=0 但 audio_lead_s>0 的边界,并进指纹(改了 lead = 须重混)
        lead = min(1.0, _q(t.get("audio_lead_s"), fps)) if float(t.get("audio_lead_s") or 0) > 0 and not ins_rows and not hd else 0.0
        if total <= 0 and lead <= 0:
            continue
        if hd:
            audio = str(t.get("hold_audio") or "sustain")
        elif ins_rows:
            a0 = str(ins[0].get("audio") or "mute")
            audio = "sustain" if a0 == "sustain" else "mute"
        else:
            audio = str(t.get("hold_audio") or "sustain")
        out.append({"from_group": a["group_id"], "to_group": b["group_id"], "freeze_s": fz, "hold_s": hd, "insert_s": ins_s,
                    "total_s": total, "audio": audio, "type": str(t.get("type") or "hard_cut"), "inserts": ins_rows,
                    "audio_lead_s": lead})
    return out


def boundary_fingerprint(bounds: list[dict]) -> str:
    """边界层指纹:看 (from, to, 占时 ms[, 音先入 ms]),占时部分与 transitions_render.json#timemap.ops 的 (from_group, to_group, out_len) 同口径;
    音先入(四期)只在 >0 时追加第 4 项,无音先入的项目指纹与旧口径完全一致。"""
    slim = []
    for b in bounds:
        total = float(b.get("total_s", b.get("out_len")) or 0.0)
        lead = float(b.get("audio_lead_s") or 0.0)
        if total <= 0 and lead <= 0:
            continue
        row = [b.get("from_group"), b.get("to_group"), int(round(total * 1000))]
        if lead > 0:
            row.append(int(round(lead * 1000)))
        slim.append(row)
    return hashlib.sha256(json.dumps(slim, sort_keys=False).encode("utf-8")).hexdigest()[:16]


def boundary_has_leads(bounds: list[dict]) -> bool:
    return any(float(b.get("audio_lead_s") or 0.0) > 0 for b in bounds)


def boundary_delta(bounds: list[dict]) -> float:
    return round(sum(float(b.get("total_s", b.get("out_len")) or 0.0) for b in bounds), 6)


def boundary_has_inserts(bounds: list[dict]) -> bool:
    return any(float(b.get("insert_s") or 0.0) > 0 for b in bounds)


def with_timing(proj: Path, ep: str, rows: list[dict], boundaries: list[dict] | None = None) -> list[dict]:
    """补实测时长与两套累计起点:cum_start_s(混音 / 后期基准 = 当前版本实测时长累计 **+ 组边界层**,2026-09-24)、
    cum_start_v0_s(母本基准,不含边界层);另给 boundary_before_s(本组前边界占时)与 cum_start_groups_s(不含边界层的组累计)。"""
    proj = Path(proj)
    bounds = boundaries if boundaries is not None else boundary_layer(proj, ep, rows)
    b_by = {b["to_group"]: b for b in bounds}
    cum, cum0, cumg = 0.0, 0.0, 0.0
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
        bb = float((b_by.get(r["group_id"]) or {}).get("total_s") or 0.0)
        cum += bb
        row.update({"duration_s": round(d, 6), "cum_start_s": round(cum, 6), "boundary_before_s": round(bb, 6),
                    "cum_start_groups_s": round(cumg, 6),
                    "v0_duration_s": round(d0, 6), "cum_start_v0_s": round(cum0, 6)})
        cum += d
        cumg += d
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
    basis = current_basis(proj, ep, plan)
    bounds = boundary_layer(proj, ep, basis)
    rows = with_timing(proj, ep, basis, bounds)
    audio = Path(audio) if audio else audio_path(proj, ep)
    if audio is None or not audio.is_file():
        raise FileNotFoundError(f"混音产物不存在:assets/audio/final/{ep}.wav")
    warns = []
    missing = [r["group_id"] for r in rows if not r.get("src")]
    if missing:
        warns.append(f"{len(missing)} 组无视频文件(未出片或 skipped):{', '.join(missing[:6])}")
    a_dur = probe_duration(audio)
    total = round(sum(r["duration_s"] for r in rows) + boundary_delta(bounds), 6)
    if abs(a_dur - total) > DUR_TOL_S:
        warns.append(f"混音时长 {a_dur:.3f}s 与 Σ组时长 + Σ边界层 {total:.3f}s 相差 {abs(a_dur - total):.3f}s(>{DUR_TOL_S:g}s);"
                     "请核对原生轨是否按当前版本逐组拼接、组边界(定格/黑场/字卡等插入段)是否按 sources 的 boundaries 留出")
    n_post = sum(1 for r in rows if int(r.get("v") or 0) > 0)
    man = {"schema": SCHEMA, "episode": ep, "stamped_at": _now(), "task_id": task_id, "cli": cli,
           "basis": "post_versions", "all_v0": n_post == 0,
           "plan_fingerprint": pp.plan_fingerprint(plan), "ops_fingerprint": ops_fingerprint(rows),
           "versions_fingerprint": versions_fingerprint(rows), "delta_s": basis_delta(rows),
           "groups": rows, "groups_on_post_version": n_post,
           # 组边界层(2026-09-24 过场设计):混音已把 BGM / 旁白铺在含边界插入的时间线上;finalize 一致时不再对声轨套边界层重映射
           "boundaries": {"source": "shot_list.transition_in", "ops": bounds, "fingerprint": boundary_fingerprint(bounds),
                          "delta_s": boundary_delta(bounds), "has_inserts": boundary_has_inserts(bounds)},
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
    cur_b = boundary_layer(proj, ep, cur)
    res = {"status": STATUS_NONE, "cur_ops_fp": ops_fingerprint(cur), "cur_delta_s": basis_delta(cur), "cur_has_ops": has_ops(cur),
           "cur_groups_on_post_version": sum(1 for r in cur if int(r.get("v") or 0) > 0),
           "cur_boundary_fp": boundary_fingerprint(cur_b), "cur_boundary_delta_s": boundary_delta(cur_b),
           "cur_boundary_has_inserts": boundary_has_inserts(cur_b), "mix_boundary_fp": None, "mix_boundary_delta_s": 0.0,
           "boundary_status": BND_NONE if not cur_b else BND_ABSENT,
           "audio_present": audio_path(proj, ep) is not None, "changed_groups": [], "sound_changed": [],
           "dub_changed": [], "dub_stale_versions": [r["group_id"] for r in cur if r.get("dub_predates_version")], "detail": ""}
    man = load_manifest(proj, ep)
    stale_dub_note = ((";{} 组当前采纳的后期版本建于配音之前(从旧母本派生,文件里没有配音):{}{},须在后期页回滚到母本或重做该版本,再重混"
                       ).format(len(res["dub_stale_versions"]), ", ".join(res["dub_stale_versions"][:6]), "…" if len(res["dub_stale_versions"]) > 6 else "")
                      if res["dub_stale_versions"] else "")
    if not man:
        res["detail"] = ("本集尚无混音产物" if not res["audio_present"] else
                         "混音未盖基准章(旧口径:按 v0 母本混,出成片时按 timemap 重映射)") + stale_dub_note
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
    # 原生声轨内容变了(后期页去人声 / 去环境声,或其弃用回退):时轴没变也必须重混,否则成片里还是旧声音
    res["sound_changed"] = [r["group_id"] for r in cur
                            if r["group_id"] in m_by and int(m_by[r["group_id"]].get("sound_v") or 0) != int(r.get("sound_v") or 0)]
    # 后期配音(§8C):配音时刻 / 逐句时段 / 去人声状态变了(含混音后才配音)= 对白内容变了,同样必须重混
    res["dub_changed"] = [r["group_id"] for r in cur
                          if r["group_id"] in m_by and (m_by[r["group_id"]].get("dub_fp") or None) != (r.get("dub_fp") or None)]
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
    # 组边界层(2026-09-24):盖章边界指纹 vs 当前 shot_list;有插入段而混音不含/不一致 = 出成片时 BGM 会在过场处断,须重混
    mb_ = man.get("boundaries") if isinstance(man.get("boundaries"), dict) else None
    if mb_ is not None:
        res["mix_boundary_fp"], res["mix_boundary_delta_s"] = mb_.get("fingerprint"), float(mb_.get("delta_s") or 0.0)
        if not cur_b and not (mb_.get("ops") or []):
            res["boundary_status"] = BND_NONE
        elif res["mix_boundary_fp"] == res["cur_boundary_fp"]:
            res["boundary_status"] = BND_CURRENT
        else:
            res["boundary_status"] = BND_STALE
    if res["boundary_status"] == BND_STALE:
        res["status"] = STATUS_STALE
        if abs(res["mix_boundary_delta_s"] - res["cur_boundary_delta_s"]) < 1e-6:
            res["detail"] += ";组边界层已变(占时未变:过场的音先入 audio_lead_s 或边界归属与混音时不同),须重跑 p8-mix 让声轨按新的先入摆位"
        else:
            res["detail"] += (";组边界层已变(混音 Δ{:+.3f}s → 当前 Δ{:+.3f}s:过场的定格/黑场/字卡等插入段与混音时不同),"
                              "须重跑 p8-mix,否则过场处声轨按 timemap 切开、BGM 会断").format(res["mix_boundary_delta_s"], res["cur_boundary_delta_s"])
    elif res["boundary_status"] == BND_ABSENT:
        res["detail"] += (";混音清单不含组边界层(旧口径)而当前 shot_list 有边界插入 Δ{:+.3f}s:出成片时声轨按 timemap 切开"
                          "{}").format(res["cur_boundary_delta_s"], ",字卡/定场处 BGM 会断,须重跑 p8-mix" if res["cur_boundary_has_inserts"] else "(仅定格/黑场,可接受)")
    if res["sound_changed"]:
        res["detail"] += (";{} 组的原生声轨在混音后改过(去人声 / 去环境声):{}{},须重跑 p8-mix,否则成片仍是旧声音"
                          ).format(len(res["sound_changed"]), ", ".join(res["sound_changed"][:6]), "…" if len(res["sound_changed"]) > 6 else "")
    if res["dub_changed"]:
        res["detail"] += (";{} 组的后期配音在混音后变了(重配音 / 改时段 / 改去人声):{}{},须重跑 p8-mix,否则成片里的对白还是旧配音"
                          ).format(len(res["dub_changed"]), ", ".join(res["dub_changed"][:6]), "…" if len(res["dub_changed"]) > 6 else "")
    res["detail"] += stale_dub_note
    if res.get("audio_fingerprint_ok") is False:
        res["detail"] += ";⚠ 混音文件在盖章后被改动(指纹不符)"
    return res


def check_row(res: dict) -> tuple[str, str]:
    """机检口径:(PASS|WARN|FAIL, detail)。stale 且混音带后期时轴 = FAIL;原生声轨内容改过 / 后期配音改过 / 采纳版本早于配音 = FAIL;其余失配只 WARN。"""
    st = res.get("status")
    bst = res.get("boundary_status")
    if res.get("sound_changed") or res.get("dub_changed") or res.get("dub_stale_versions"):
        return "FAIL", res.get("detail", "")
    if bst == BND_STALE and (res.get("cur_boundary_has_inserts") or float(res.get("mix_boundary_delta_s") or 0) > 0):
        return "FAIL", res.get("detail", "")
    if bst == BND_ABSENT and res.get("cur_boundary_has_inserts"):
        return "FAIL", res.get("detail", "")
    if st == STATUS_CURRENT:
        return "PASS", res.get("detail", "")
    if st == STATUS_STALE and res.get("mix_has_ops"):
        return "FAIL", res.get("detail", "")
    return "WARN", res.get("detail", "")
