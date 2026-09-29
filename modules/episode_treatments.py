"""拆集「事件取舍」契约与机检(WORKFLOW.md Phase 5,2026-09-12)。

背景:旧机检要求「事件 100% 分配且不重复」+ 剧本「本集事件全覆盖」,Agent 为过机检把每个事件都演成一场戏,
成片节奏平铺。新契约把「分配」改成「归类」:每个事件仍必须落到唯一一集(不许静默丢),但每集须给每个事件
定 treatment——

    dramatize  演     正片成场,观众看见
    mention    带过   旁白或台词一句带过,不单独成场
    merge      并入   并进同集另一个 dramatize 事件的场里(merge_into 指向它)
    cut        删     不出现;须给理由,且不得在主线因果链上

机检(见 verify_plan / verify_screenplay):
  episode_plan.json
    events_classified_once     对账范围内事件 100% 归到唯一一集(与旧 events_assigned_once 同义,更名)
    treatments_complete        每集 events 里每个事件都有 treatment,取值在枚举内,cut/mention/merge 须有 reason
    dramatize_within_cap       每集 dramatize 数 ≤ ceil(duration_budget_s / sec_per_event),默认 90s/事件
    hook_points_dramatized     开场钩位/结尾卡点事件必须 dramatize
    cut_not_on_causal_chain    cut 事件不得 importance=major、不得是任何非 cut 事件的 caused_by、不得是卡点
    merge_target_valid         merge_into 指向同集 dramatize 事件
    duration_in_budget / ids_valid   沿用旧检
  screenplay.md(每集)
    dramatized_events_covered  本集每个 dramatize 事件至少出现在一场的 [事件] 行
    cut_events_absent          任何场次的 [事件] 不得引用 cut 事件
    scene_has_dramatized_event 每场 [事件] 至少含一个 dramatize 事件(纯 mention/merge 事件不得独立成场)
  旧格式(episode_plan 无 treatments)剧本侧回退为「全部视为 dramatize」并 WARN,不阻断存量项目。

兼容各家 episode_plan 写法:集号键 episode_id|ep|episode;事件键 events|event_ids|event_refs;
treatment 既收 `treatments: [{event, treatment, reason?, merge_into?}]`,也收 `event_treatment: {ev: "演"}` 字典形。
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

DEFAULT_SEC_PER_DRAMATIZED_EVENT = 90

TREATMENTS = ("dramatize", "mention", "merge", "cut")
TREATMENT_ZH = {"dramatize": "演", "mention": "带过", "merge": "并入", "cut": "删"}
_ALIASES = {
    "dramatize": "dramatize", "dramatise": "dramatize", "演": "dramatize", "正片展开": "dramatize", "正片": "dramatize",
    "展开": "dramatize", "play": "dramatize", "scene": "dramatize",
    "mention": "mention", "narrate": "mention", "带过": "mention", "旁白带过": "mention", "台词带过": "mention",
    "一句带过": "mention", "narration": "mention",
    "merge": "merge", "并入": "merge", "合并": "merge", "并": "merge",
    "cut": "cut", "drop": "cut", "删": "cut", "删除": "cut", "删去": "cut", "skip": "cut",
}

_EP_KEYS = ("episode_id", "ep", "episode", "id")
_EVENT_KEYS = ("events", "event_ids", "event_refs")


# ---------------------------------------------------------------- 读取/归一

def read_json(p: Path) -> Any:
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return None


def normalize_treatment(v: Any) -> str | None:
    if not isinstance(v, str):
        return None
    key = v.strip().lower()
    if key in _ALIASES:
        return _ALIASES[key]
    key = re.sub(r"[\s()（）:：/|].*$", "", key)     # "演(正片)" / "cut: 无关" 之类只取头
    return _ALIASES.get(key)


def episode_id(ep: dict) -> str | None:
    for k in _EP_KEYS:
        v = ep.get(k)
        if isinstance(v, str) and v:
            return v
        if isinstance(v, int) and k != "id":
            return f"ep{v:02d}"
    return None


def episode_events(ep: dict) -> list[str]:
    for k in _EVENT_KEYS:
        v = ep.get(k)
        if isinstance(v, list):
            return [x if isinstance(x, str) else (x.get("id") or x.get("event")) for x in v
                    if isinstance(x, (str, dict))]
    return []


def episode_budget(ep: dict, default: float | None = None) -> float | None:
    for k in ("duration_budget_s", "target_duration_s", "budget_s"):
        v = ep.get(k)
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    return default


def hook_events(ep: dict) -> dict[str, str | None]:
    hp = ep.get("hook_point") if isinstance(ep.get("hook_point"), dict) else {}
    out = {}
    for k in ("opening", "cliffhanger"):
        v = hp.get(k)
        if isinstance(v, dict):
            v = v.get("event") or v.get("event_id") or v.get("id")
        out[k] = v if isinstance(v, str) else None
    return out


def episode_treatments(ep: dict) -> tuple[dict[str, dict], list[str]]:
    """→ ({event_id: {treatment, reason, merge_into, raw}}, 格式错误清单)。两种写法都收。"""
    out: dict[str, dict] = {}
    errs: list[str] = []
    raw = ep.get("treatments")
    if isinstance(raw, list):
        for i, t in enumerate(raw):
            if not isinstance(t, dict):
                errs.append(f"treatments[{i}] 不是对象")
                continue
            ev = t.get("event") or t.get("event_id") or t.get("id")
            if not isinstance(ev, str) or not ev:
                errs.append(f"treatments[{i}] 缺 event")
                continue
            tr = normalize_treatment(t.get("treatment") or t.get("mode"))
            if tr is None:
                errs.append(f"{ev}: treatment 取值非法 {t.get('treatment')!r}(须 dramatize/mention/merge/cut)")
            if ev in out:
                errs.append(f"{ev}: treatments 重复登记")
            out[ev] = {"treatment": tr, "reason": (t.get("reason") or "").strip() if isinstance(t.get("reason"), str) else "",
                       "merge_into": t.get("merge_into") if isinstance(t.get("merge_into"), str) else None, "raw": t}
        return out, errs
    raw = ep.get("event_treatment") if isinstance(ep.get("event_treatment"), dict) else None
    if raw is None and isinstance(ep.get("treatments"), dict):
        raw = ep["treatments"]
    if isinstance(raw, dict):
        for ev, t in raw.items():
            if isinstance(t, dict):
                tr = normalize_treatment(t.get("treatment") or t.get("mode"))
                out[ev] = {"treatment": tr, "reason": (t.get("reason") or "") if isinstance(t.get("reason"), str) else "",
                           "merge_into": t.get("merge_into") if isinstance(t.get("merge_into"), str) else None, "raw": t}
            else:
                tr = normalize_treatment(t)
                out[ev] = {"treatment": tr, "reason": "", "merge_into": None, "raw": t}
            if tr is None:
                errs.append(f"{ev}: treatment 取值非法 {t!r}(须 dramatize/mention/merge/cut)")
    return out, errs


def has_treatments(ep: dict) -> bool:
    return bool(ep.get("treatments")) or isinstance(ep.get("event_treatment"), dict)


def dramatize_cap(budget_s: float | None, sec_per_event: float = DEFAULT_SEC_PER_DRAMATIZED_EVENT) -> int | None:
    if not budget_s or sec_per_event <= 0:
        return None
    return max(1, math.ceil(budget_s / sec_per_event))


def _chapter_num(ch: Any) -> int | None:
    if isinstance(ch, int):
        return ch
    if isinstance(ch, str):
        m = re.search(r"(\d+)", ch)
        if m:
            return int(m.group(1))
    return None


def events_in_scope(plan: dict, events: list[dict]) -> tuple[list[str], str]:
    """对账范围:总表带 roadmap(后续集只做路线图不逐事件分配)时,只对账精确规划集章节范围内的事件;否则全量。"""
    ids = [e.get("id") for e in events if isinstance(e, dict) and isinstance(e.get("id"), str)]
    eps = [e for e in plan.get("episodes") or [] if isinstance(e, dict)]
    if not plan.get("roadmap"):
        return ids, "全量"
    ends = []
    for ep in eps:
        cr = ep.get("chapter_range")
        if isinstance(cr, dict):
            n = _chapter_num(cr.get("end"))
            if n is not None:
                ends.append(n)
        elif isinstance(ep.get("chapters"), list) and ep["chapters"]:
            n = _chapter_num(ep["chapters"][-1])
            if n is not None:
                ends.append(n)
    if not ends:
        return ids, "全量(roadmap 无章节范围,退回全量)"
    last = max(ends)
    scoped = []
    for e in events:
        if not isinstance(e, dict) or not isinstance(e.get("id"), str):
            continue
        n = _chapter_num(e.get("chapter"))
        if n is None or n <= last:
            scoped.append(e["id"])
    return scoped, f"精确规划集章节 ≤ {last}(roadmap 之外不对账)"


# ---------------------------------------------------------------- episode_plan 机检

def verify_plan(plan: dict, events_doc: dict | list | None, graph: dict | None,
                sec_per_event: float = DEFAULT_SEC_PER_DRAMATIZED_EVENT) -> dict:
    """→ {checks: {name: bool}, errors: [], warns: [], episodes: [{ep, budget, n_events, n_dramatize, cap, ...}]}。"""
    errors: list[str] = []
    warns: list[str] = []
    checks: dict[str, bool] = {}
    events = events_doc.get("events") if isinstance(events_doc, dict) else (events_doc or [])
    events = [e for e in events if isinstance(e, dict)]
    ev_by_id = {e["id"]: e for e in events if isinstance(e.get("id"), str)}
    eps = [e for e in plan.get("episodes") or [] if isinstance(e, dict)]
    if not eps:
        errors.append("episodes 为空")
        return {"checks": {"schema": False}, "errors": errors, "warns": warns, "episodes": []}

    # 1. 归类唯一
    scope_ids, scope_desc = events_in_scope(plan, events)
    scope = set(scope_ids)
    assigned: list[str] = []
    for ep in eps:
        assigned.extend(episode_events(ep))
    seen: set[str] = set()
    dups = sorted({x for x in assigned if x in seen or seen.add(x)})
    missing = sorted(scope - set(assigned))
    extra = sorted(set(assigned) - set(ev_by_id)) if ev_by_id else []
    if dups:
        errors.append(f"事件重复归入多集: {dups[:20]}")
    if missing:
        errors.append(f"对账范围内事件未归类({len(missing)}): {missing[:20]}")
    if extra:
        errors.append(f"非法事件 ID: {extra[:20]}")
    checks["events_classified_once"] = not (dups or missing or extra)

    # 2. 每集 treatment
    default_budget = plan.get("default_duration_budget_s")
    default_budget = float(default_budget) if isinstance(default_budget, (int, float)) and default_budget > 0 else None
    treat_ok = cap_ok = hook_ok = cut_ok = merge_ok = dur_ok = ids_ok = True
    fs_ids = {f.get("id") for f in (graph or {}).get("foreshadowing") or [] if isinstance(f, dict)}
    all_treat: dict[str, str] = {}
    per_ep: list[dict] = []
    ep_treats: list[tuple[dict, dict[str, dict]]] = []
    for ep in eps:
        eid = episode_id(ep) or "?"
        evs = episode_events(ep)
        tr, ferrs = episode_treatments(ep)
        ep_treats.append((ep, tr))
        for e in ferrs:
            errors.append(f"{eid}: {e}")
        if ferrs:
            treat_ok = False
        if not has_treatments(ep):
            errors.append(f"{eid}: 缺 treatments(每个事件须定 dramatize/mention/merge/cut)")
            treat_ok = False
        missing_t = [e for e in evs if e not in tr or tr[e]["treatment"] is None]
        if missing_t and has_treatments(ep):
            errors.append(f"{eid}: 事件未定 treatment: {missing_t[:20]}")
            treat_ok = False
        stray = [e for e in tr if e not in evs]
        if stray:
            errors.append(f"{eid}: treatments 登记了不属于本集 events 的事件: {stray[:20]}")
            treat_ok = False
        for e, t in tr.items():
            if t["treatment"] in ("mention", "merge", "cut") and not t["reason"]:
                errors.append(f"{eid}: {e} treatment={t['treatment']} 须写 reason")
                treat_ok = False
            if t["treatment"]:
                all_treat[e] = t["treatment"]
        n_dram = sum(1 for e in evs if tr.get(e, {}).get("treatment") == "dramatize")
        budget = episode_budget(ep, default_budget)
        cap = dramatize_cap(budget, sec_per_event)
        if has_treatments(ep) and evs and n_dram == 0:
            errors.append(f"{eid}: 没有任何 dramatize 事件")
            cap_ok = False
        if cap is not None and n_dram > cap:
            errors.append(f"{eid}: dramatize 事件 {n_dram} 个 > 上限 {cap}(预算 {budget:.0f}s ÷ {sec_per_event:.0f}s/事件);"
                          f"多出的须改 mention/merge/cut")
            cap_ok = False
        hooks = hook_events(ep)
        for k, hv in hooks.items():
            if hv and ev_by_id and hv not in ev_by_id:
                errors.append(f"{eid}: hook_point.{k} 非法事件 ID {hv}")
                ids_ok = False
            elif hv and hv in evs and tr and tr.get(hv, {}).get("treatment") not in (None, "dramatize"):
                errors.append(f"{eid}: hook_point.{k}={hv} 是 {tr[hv]['treatment']},卡点事件必须 dramatize")
                hook_ok = False
        for f in ep.get("carry_over") or []:
            if fs_ids and isinstance(f, str) and f not in fs_ids:
                errors.append(f"{eid}: carry_over 非法伏笔 ID {f}")
                ids_ok = False
        # merge 目标
        for e, t in tr.items():
            if t["treatment"] == "merge":
                tgt = t["merge_into"]
                if not tgt:
                    errors.append(f"{eid}: {e} merge 缺 merge_into")
                    merge_ok = False
                elif tgt not in evs or tr.get(tgt, {}).get("treatment") != "dramatize":
                    errors.append(f"{eid}: {e} merge_into={tgt} 不是本集 dramatize 事件")
                    merge_ok = False
        # 时长
        if budget is None:
            warns.append(f"{eid}: 无时长预算,跳过 dramatize 上限")
        elif default_budget and abs(budget - default_budget) > 1e-6 and not plan.get("duration_policy"):
            warns.append(f"{eid}: 预算 {budget:.0f}s ≠ 总表默认 {default_budget:.0f}s")
        load = ep.get("content_load_estimate_s") or ep.get("estimated_duration_s") or ep.get("est_duration_s")
        if isinstance(load, (int, float)) and budget and load > budget * 1.1:
            errors.append(f"{eid}: 内容负荷估 {load:.0f}s 超预算 {budget:.0f}s 的 110%")
            dur_ok = False
        per_ep.append({"ep": eid, "budget_s": budget, "n_events": len(evs), "n_dramatize": n_dram, "cap": cap,
                       "n_mention": sum(1 for e in evs if tr.get(e, {}).get("treatment") == "mention"),
                       "n_merge": sum(1 for e in evs if tr.get(e, {}).get("treatment") == "merge"),
                       "n_cut": sum(1 for e in evs if tr.get(e, {}).get("treatment") == "cut"),
                       "hook": hooks})
    checks["treatments_complete"] = treat_ok
    checks["dramatize_within_cap"] = cap_ok
    checks["hook_points_dramatized"] = hook_ok
    checks["merge_target_valid"] = merge_ok
    checks["duration_in_budget"] = dur_ok
    checks["ids_valid"] = ids_ok

    # 3. cut 不在主线因果链上(跨集看:被任何非 cut 事件 caused_by 的不能删)
    kept = {e for e, t in all_treat.items() if t != "cut"}
    needed_by: dict[str, list[str]] = {}
    for e in kept:
        for c in (ev_by_id.get(e) or {}).get("caused_by") or []:
            if isinstance(c, str):
                needed_by.setdefault(c, []).append(e)
    for ep, tr in ep_treats:
        eid = episode_id(ep) or "?"
        hooks = set(v for v in hook_events(ep).values() if v)
        for e, t in tr.items():
            if t["treatment"] != "cut":
                continue
            imp = (ev_by_id.get(e) or {}).get("importance")
            if imp == "major":
                errors.append(f"{eid}: {e} 是 major 事件,不得 cut(可改 mention/merge)")
                cut_ok = False
            if e in needed_by:
                errors.append(f"{eid}: {e} 是 {needed_by[e][:5]} 的 caused_by,删掉因果断裂,至少 mention")
                cut_ok = False
            if e in hooks:
                errors.append(f"{eid}: {e} 是卡点事件,不得 cut")
                cut_ok = False
    checks["cut_not_on_causal_chain"] = cut_ok
    return {"checks": checks, "errors": errors, "warns": warns, "episodes": per_ep, "scope": scope_desc,
            "n_scope_events": len(scope), "n_assigned": len(assigned)}


# ---------------------------------------------------------------- screenplay 机检

def plan_episode(plan: dict | None, ep: str) -> dict | None:
    for e in (plan or {}).get("episodes") or []:
        if isinstance(e, dict) and episode_id(e) == ep:
            return e
    return None


def _norm_ev(x: str) -> str:
    return x.strip().lower()


# 场 = 同一空间 + 连续时间(2026-09-29 用户裁定):相邻两场同 SCN、同内外景、同时段即判拆碎,
# 除非后一场写 `(split_note: …)` 说明理由。此日期前生成(front matter generated_at 缺失或更早)的存量剧本只 WARN。
SPACETIME_RULE_SINCE = "2026-09-29"


def _generated_at(text: str) -> str | None:
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    if not m:
        return None
    d = re.search(r"^generated_at\s*:\s*['\"]?(\d{4}-\d{2}-\d{2})", m.group(1), re.M)
    return d.group(1) if d else None


def adjacent_same_spacetime(parsed_scenes: list[dict]) -> list[str]:
    """相邻两场同 SCN + 同内外景 + 同时段、且后一场无 split_note → ["S03→S04(SCN-0143 EXT 夜)", …]"""
    out = []
    for a, b in zip(parsed_scenes, parsed_scenes[1:]):
        sid = a.get("scene_id")
        if not sid or sid != b.get("scene_id") or b.get("split_note"):
            continue
        ie_a, ie_b = a.get("int_ext"), b.get("int_ext")
        tod_a, tod_b = (a.get("time_of_day") or "").strip(), (b.get("time_of_day") or "").strip()
        if ie_a == ie_b and tod_a == tod_b:
            out.append(f"{a.get('no')}→{b.get('no')}({sid} {ie_a or '?'} {tod_a or '?'})")
    return out


def verify_screenplay(screenplay_text: str, plan: dict | None, ep: str) -> dict:
    """剧本 vs 本集 treatment。→ {checks, errors, warns, scenes: [{scene, events}], treatments}"""
    from modules import script_breakdown as sb   # 复用剧本解析(场次/[事件] 行)

    errors: list[str] = []
    warns: list[str] = []
    checks: dict[str, bool] = {}
    ep_plan = plan_episode(plan, ep)
    parsed = sb.parse_screenplay(screenplay_text)
    scenes = [{"scene": s.get("no") or s.get("scene_id"), "events": [e for e in (s.get("events") or []) if e.lower() != "ev"]}
              for s in parsed.get("scenes") or []]
    if ep_plan is None:
        warns.append(f"episode_plan 里没有 {ep},无法核 treatment(仅解析场次)")
        return {"checks": {"dramatized_events_covered": True, "cut_events_absent": True, "scene_has_dramatized_event": True,
                           "scene_spacetime_continuous": True},
                "errors": errors, "warns": warns, "scenes": scenes, "treatments": {}, "legacy": True}
    evs = episode_events(ep_plan)
    tr, ferrs = episode_treatments(ep_plan)
    legacy = not has_treatments(ep_plan)
    if legacy:
        warns.append("episode_plan 本集无 treatments(2026-09-12 前旧格式):全部事件按 dramatize 核,建议重跑 p5-episode-plan")
        treat = {e: "dramatize" for e in evs}
    else:
        for e in ferrs:
            warns.append(f"episode_plan: {e}")
        treat = {e: (tr.get(e) or {}).get("treatment") or "dramatize" for e in evs}
    lower = {_norm_ev(e): e for e in treat}
    cited: dict[str, list[str]] = {}
    scene_no_dram: list[str] = []
    unknown: dict[str, list[str]] = {}
    for s in scenes:
        sid = s["scene"] or "?"
        kinds = []
        for e in s["events"]:
            canon = lower.get(_norm_ev(e))
            if canon is None:
                unknown.setdefault(e, []).append(sid)
                continue
            cited.setdefault(canon, []).append(sid)
            kinds.append(treat[canon])
        if s["events"] and "dramatize" not in kinds and all(k in ("mention", "merge", "cut") for k in kinds):
            scene_no_dram.append(f"{sid}({', '.join(s['events'])})")
    dram = [e for e in evs if treat[e] == "dramatize"]
    not_covered = [e for e in dram if e not in cited]
    if scenes and not any(s["events"] for s in scenes):
        errors.append("剧本没有任何场次标 [事件] 行(每场首行须为 `**[事件] evXXXX | [出场] CHAR-… | [时长] NNs**`),无法核对取舍")
    elif not_covered:
        errors.append(f"dramatize 事件未在任何场次 [事件] 出现: {not_covered}")
    checks["dramatized_events_covered"] = not not_covered and bool(scenes) and any(s["events"] for s in scenes)
    cut_cited = {e: v for e, v in cited.items() if treat[e] == "cut"}
    if cut_cited:
        errors.append("cut 事件被写成场次: " + "; ".join(f"{e}→{v}" for e, v in cut_cited.items()))
    checks["cut_events_absent"] = not cut_cited
    if scene_no_dram:
        errors.append("场次只挂 mention/merge 事件、没有 dramatize 事件(带过/并入的事件不得独立成场): " + "; ".join(scene_no_dram))
    checks["scene_has_dramatized_event"] = not scene_no_dram
    if unknown:
        warns.append("场次引用了不属于本集 episode_plan 的事件: " + "; ".join(f"{e}→{v}" for e, v in unknown.items()))
    no_event_scenes = [s["scene"] or "?" for s in scenes if not s["events"]]
    if no_event_scenes:
        warns.append(f"场次无 [事件] 行,无法核对: {no_event_scenes}")
    split = adjacent_same_spacetime(parsed.get("scenes") or [])
    gen = _generated_at(screenplay_text)
    legacy_split = not gen or gen < SPACETIME_RULE_SINCE
    msg = ("相邻场同一空间 + 连续时间被拆成多场(场 = 同一空间 + 连续时间;人物进出、动作回合写成场内【节拍】,"
           "确需分场在后一场写 `(split_note: 理由)`): " + "; ".join(split))
    if split and legacy_split:
        warns.append(msg + f"(存量剧本 generated_at={gen or '缺失'} 早于 {SPACETIME_RULE_SINCE},只 WARN)")
    elif split:
        errors.append(msg)
    checks["scene_spacetime_continuous"] = not split or legacy_split
    if dram and len(scenes) > len(dram) * 2:
        warns.append(f"场次 {len(scenes)} 场 > dramatize 事件 {len(dram)} 个的 2 倍,疑似平铺(每个演的事件平均 ≤2 场为宜)")
    return {"checks": checks, "errors": errors, "warns": warns, "scenes": scenes, "treatments": treat, "legacy": legacy,
            "n_scenes": len(scenes), "n_dramatize": len(dram)}


def format_report(res: dict, title: str) -> str:
    lines = [f"=== {title} ==="]
    for k, v in res.get("checks", {}).items():
        lines.append(f"[CHECK] {k:<28}: {'PASS' if v else 'FAIL'}")
    for e in res.get("errors", []):
        lines.append(f"FAIL  {e}")
    for w in res.get("warns", []):
        lines.append(f"WARN  {w}")
    return "\n".join(lines)
