#!/usr/bin/env python3
"""check_time_budget.py — 时间预算机检(时间尺 modules/time_cost.py 的四个落点,2026-10-03,docs/time_cost.md)。

  --scope script    场级(p5-pacing):剧本每场 Σ台词估时(含开口前后余量)+ Σ动作节拍 ≤ 分配时长;
                    Σ需求 > 集预算时按用户拍板三步给出处理:加长单集(≤ 设置「可加长单集」%)> 精简台词 > 合并动作短镜
                    机检 scene_time_budget_ok / episode_time_budget_ok / pacing_budget_adopted / pacing_extension_within_cap
                    报告 story/episodes/epNN/time_budget.json
  --scope shots     镜级(p6-storyboard / p6-shots):每镜镜长 ≥ 节拍单价 + 台词估时 + 余量;密集组(平均镜长 <1.5s)≤ 15s
                    机检 shot_time_budget_ok / group_density_ok;--source storyboard|shot_list(缺省有 shot_list 用 shot_list)
  --scope blocking  守门(p6-blocking):blocking beats[] 单价之和 ≤ 镜长 − 台词估时;同人相邻节拍 ≥ 0.3s
                    机检 beat_budget_ok / beat_spacing_ok
  --scope prompt    守门(p7-prompt):每个 Shot 段顺序连接词数 ≤ blocking 节拍数 + 1(prompt 只能把节拍写细,不能加新的顺序动作)
                    机检 prompt_beats_bound
  缺省 --scope all:按现有产物逐级跑。shots/blocking/prompt 报告写 directing/epNN/time_budget.json。

存量规则:产物日期(剧本 front matter generated_at / shot_list·storyboard _meta)早于 2026-10-03 的集一律 WARN 不 FAIL(--strict 例外)。

用法:python3 code/check_time_budget.py --project <slug> --ep ep01 [--scope script|shots|blocking|prompt|all]
     python3 code/check_time_budget.py --project <slug> --ep ep01 --scope shots --source storyboard
     python3 code/check_time_budget.py --project <slug> --ep ep01 --scope prompt grp011
退出码:0 通过(可含 WARN);1 有 FAIL。
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules import time_cost as tc  # noqa: E402
from modules import shot_timing  # noqa: E402
from modules.dialogue_tts import name_index, resolve_speaker  # noqa: E402
from check_dialogue_fit import load_speeds, speaker_id, line_placement  # noqa: E402  line_placement:声画分离(2026-10-03)

TOL_S = 0.1              # 镜级容差
SCENE_TOL_S = 0.5        # 场级容差
PACING_TOL = 0.10        # pacing 总时长 vs 预算 ±10%


def _read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
    except (ValueError, OSError):
        return None


class Report:
    def __init__(self, legacy: bool):
        self.legacy = legacy
        self.errs, self.warns, self.checks = [], [], {}

    def fail(self, name: str, msg: str):
        """新集 FAIL;存量降 WARN。"""
        (self.warns if self.legacy else self.errs).append(f"{name}: {msg}")
        self.checks[name] = "WARN" if self.legacy else "FAIL"

    def warn(self, name: str, msg: str):
        self.warns.append(f"{name}: {msg}")
        self.checks.setdefault(name, "WARN")

    def ok(self, name: str):
        self.checks.setdefault(name, "PASS")


# ---------------------------------------------------------------- 台词行 → 估时输入
def _line_input(text: str, spk: str, speeds: dict, pace: str = "", emotion: str = "") -> dict:
    cpm = speeds.get(spk)
    p = tc.norm_pace(pace) or tc.guess_pace(emotion)
    return {"text": text, "speaker": spk, "pace": p or "medium", "pace_source": "line" if tc.norm_pace(pace) else ("emotion" if p else "default"),
            "cpm": cpm, "est": tc.line_est(text, p, cpm)}


# ================================================================ script
def run_script(base: Path, ep: str, rep_all: dict, strict: bool) -> Report:
    from modules import script_breakdown as sb
    sdir = base / "story" / "episodes" / ep
    sp_path = sdir / "screenplay.md"
    if not sp_path.is_file():
        r = Report(True)
        r.warn("script_scope_skipped", f"无 {sp_path.relative_to(base)}")
        return r
    md = sp_path.read_text(encoding="utf-8")
    legacy = tc.is_legacy(tc.frontmatter_date(md))
    r = Report(legacy and not strict)
    parsed = sb.parse_screenplay(md)
    settings = _read_json(base / "settings.json") or {}
    pct = tc.extend_pct(settings)
    speeds = load_speeds(base)
    names = name_index(base)
    pacing = _read_json(sdir / "pacing.json") or {}
    policy = pacing.get("duration_policy") if isinstance(pacing.get("duration_policy"), dict) else None
    pacing_scenes = {}
    for ps in pacing.get("scenes") or []:
        if isinstance(ps, dict):
            no = str(ps.get("scene") or ps.get("scene_no") or ps.get("unit") or "").upper()
            if no:
                pacing_scenes[no] = ps

    # 基准预算:duration_policy.base_budget_s > episode_plan > 剧本 > 设置
    base_budget = None
    if policy and policy.get("base_budget_s"):
        base_budget = float(policy["base_budget_s"])
    if base_budget is None:
        plan = _read_json(base / "story" / "episode_plan.json") or {}
        for e in plan.get("episodes") or []:
            if isinstance(e, dict) and str(e.get("ep") or e.get("id") or e.get("episode") or "").lower() in (ep, ep.replace("ep", "")):
                for k in ("duration_budget_s", "target_duration_s", "budget_s"):
                    if e.get(k):
                        base_budget = float(e[k])
                        break
                break
    if base_budget is None and parsed.get("budget_s"):
        base_budget = float(parsed["budget_s"])
    if base_budget is None:
        mins = (settings.get("duration") or {}).get("episode_minutes")
        if isinstance(mins, (int, float)) and mins > 0:
            base_budget = float(mins) * 60

    scenes_out, need_total, alloc_total = [], 0.0, 0.0
    for sc in parsed.get("scenes") or []:
        no = str(sc.get("no") or "").upper()
        lines_in = []
        for d in sc.get("dialogue") or []:
            text = str(d.get("text") or "").strip()
            if not text:
                continue
            spk = d.get("speaker_id") or speaker_id(str(d.get("speaker") or ""), names) or ""
            lines_in.append(_line_input(text, spk, speeds, d.get("pace") or "", d.get("emotion") or ""))
        actions = [a for a in sc.get("action") or [] if isinstance(a, str) and a.strip() and not tc.is_meta_action_line(a)]
        act_total, beats = 0.0, []
        for a in actions:
            s, bs = tc.action_cost(a, prose=True)
            act_total += s
            beats.append({"text": a[:80], "beats": len(bs), "cost_s": s})
        dlg = tc.dialogue_need(lines_in)
        need = round(dlg + act_total, 2)
        ps = pacing_scenes.get(no) or {}
        alloc = ps.get("alloc_s") if ps.get("alloc_s") is not None else sc.get("alloc_s")
        alloc = float(alloc) if alloc is not None else None
        rec = {"scene": no, "scene_id": sc.get("scene_id"), "alloc_s": alloc, "dialogue_s": dlg, "action_s": round(act_total, 2),
               "need_s": need, "lines": len(lines_in), "action_lines": beats, "status": "ok"}
        need_total += need
        if alloc is not None:
            alloc_total += alloc
            if need > alloc + SCENE_TOL_S:
                rec["status"] = "over"
                rec["over_s"] = round(need - alloc, 1)
                r.fail("scene_time_budget_ok", f"{no} 内容需求 {need:g}s(台词 {dlg:g} + 动作 {act_total:.1f})> 分配 {alloc:g}s,超 {need - alloc:.1f}s")
        rec["lines_detail"] = lines_in
        scenes_out.append(rec)
    r.ok("scene_time_budget_ok")

    plan = tc.budget_plan(base_budget or 0, need_total, pct)
    out = {"base_budget_s": base_budget, "extend_pct": pct, "need_total_s": round(need_total, 1), "alloc_total_s": round(alloc_total, 1),
           "plan": plan, "scenes": scenes_out, "trim_targets": [], "merge_candidates": []}
    if plan["status"] == "no_budget":
        r.warn("episode_time_budget_ok", "找不到集预算(episode_plan / 剧本 / 设置),跳过")
    elif plan["status"] == "ok":
        r.ok("episode_time_budget_ok")
    elif plan["status"] == "extend":
        r.ok("episode_time_budget_ok")
        r.warn("episode_time_budget_ok", f"Σ需求 {need_total:.0f}s > 预算 {base_budget:.0f}s:第一步加长单集到 {plan['recommended_budget_s']:.0f}s"
                                         f"(+{plan['extension_pct']}%,上限 +{pct:g}% = {plan['cap_s']:.0f}s),pacing 写 duration_budget_s 与 duration_policy")
    else:
        need_cut = plan["need_cut_s"]
        r.fail("episode_time_budget_ok", f"Σ需求 {need_total:.0f}s > 预算上限 {plan['cap_s']:.0f}s(基准 {base_budget:.0f}s +{pct:g}%),"
                                         f"加长到顶仍差 {need_cut:g}s:第二步精简台词(trim_targets),第三步合并动作短镜(merge_candidates)")
        out["trim_targets"] = _trim_targets(scenes_out, need_cut)
        out["merge_candidates"] = _merge_candidates(scenes_out)
    # pacing 已落盘时核对它采纳了加长、且没超上限、总时长对账
    if pacing:
        pb = pacing.get("duration_budget_s")
        try:
            pb = float(pb) if pb is not None else None
        except (TypeError, ValueError):
            pb = None
        if pb is not None and base_budget:
            if pb > plan["cap_s"] + 1e-9:
                r.fail("pacing_extension_within_cap", f"pacing duration_budget_s {pb:g}s > 上限 {plan['cap_s']:.0f}s(基准 {base_budget:.0f}s +{pct:g}%)")
            else:
                r.ok("pacing_extension_within_cap")
            if plan["status"] == "extend" and pb + 1e-9 < plan["recommended_budget_s"]:
                r.fail("pacing_budget_adopted", f"Σ需求 {need_total:.0f}s 要加长到 {plan['recommended_budget_s']:.0f}s,pacing 仍按 {pb:g}s 分配(未采纳加长)")
            else:
                r.ok("pacing_budget_adopted")
            if pb != base_budget and not policy:
                r.warn("pacing_budget_adopted", f"pacing duration_budget_s {pb:g}s ≠ 基准 {base_budget:g}s 但无 duration_policy 说明")
            tot = pacing.get("total_s")
            try:
                tot = float(tot) if tot is not None else None
            except (TypeError, ValueError):
                tot = None
            if tot is not None:
                if abs(tot - pb) > pb * PACING_TOL + 1e-9:
                    r.fail("pacing_total_matches_budget", f"pacing total_s {tot:g}s 与 duration_budget_s {pb:g}s 相差超过 ±{PACING_TOL:.0%}")
                else:
                    r.ok("pacing_total_matches_budget")
    rep_all["script"] = out
    return r


def _trim_targets(scenes: list[dict], need_cut: float) -> list[dict]:
    """第二步精简台词:需削减秒数按各场台词估时占比分摊,再摊到各句 → target_chars(<6 字短句豁免)。"""
    pool = sum(s["dialogue_s"] for s in scenes) or 1.0
    out = []
    for s in scenes:
        if not s["lines_detail"]:
            continue
        cut_s = need_cut * s["dialogue_s"] / pool
        cands = [ln for ln in s["lines_detail"] if tc.eff_chars(ln["text"]) >= 6] or list(s["lines_detail"])
        lpool = sum(ln["est"] for ln in cands) or 1.0
        lines = []
        for ln in s["lines_detail"]:
            c = cut_s * ln["est"] / lpool if ln in cands else 0.0
            rate = tc.speech_rate(ln["pace"], ln["cpm"])
            keep_s = max(0.0, ln["est"] - c - tc.ONSET_S - tc.PAUSE_S * tc.inner_pauses(ln["text"]))
            tgt = max(0, min(tc.eff_chars(ln["text"]), int(keep_s * rate)))
            lines.append({"speaker": ln["speaker"], "text": ln["text"], "chars": tc.eff_chars(ln["text"]), "est_s": ln["est"],
                          "target_chars": tgt, "cut_chars": tc.eff_chars(ln["text"]) - tgt})
        lines.sort(key=lambda x: -x["cut_chars"])
        out.append({"scene": s["scene"], "need_cut_s": round(cut_s, 1), "lines": lines,
                    "hint": "按 target_chars 逐句精简(文本层;<6 字短句已豁免);合并/删句亦可,Σ估时降到位即通过"})
    return out


def _merge_candidates(scenes: list[dict]) -> list[dict]:
    """第三步合并动作短镜:节拍最多的动作行(≥3 拍)列给 storyboard,受力反馈拍并入主动作。"""
    out = []
    for s in scenes:
        for a in s["action_lines"]:
            if a["beats"] >= 3:
                out.append({"scene": s["scene"], "text": a["text"], "beats": a["beats"], "cost_s": a["cost_s"],
                            "hint": "把受力反馈/结果拍并入主动作同一镜,或删次要拍"})
    out.sort(key=lambda x: -x["cost_s"])
    return out[:40]


# ================================================================ shots
def _shot_lines(shot: dict, speeds: dict, names: dict, onscreen_only: bool = True) -> list[dict]:
    """本镜台词 → 估时输入。声画分离(2026-10-03):placement=os|vo 的句由后期合成、不占本镜说话人嘴时间,
    默认不计(其窗口由 offscreen_lines 的 offscreen_fit 另查);onscreen_only=False 时全部返回并带 placement。"""
    out = []
    raw = shot.get("dialogue_lines")
    if raw is None:
        raw = shot.get("dialogue_ref") if isinstance(shot.get("dialogue_ref"), list) else []
    for ln in raw or []:
        if not isinstance(ln, dict):
            continue
        text = str(ln.get("text") or ln.get("line") or "").strip()
        if not text:
            continue
        pl = line_placement(ln)
        if onscreen_only and pl != "on":
            continue
        sid, _ = resolve_speaker(ln, names)
        item = _line_input(text, sid or "", speeds, ln.get("pace") or "", ln.get("emotion") or "")
        item["placement"] = pl
        out.append(item)
    return out


def _shot_actions(shot: dict) -> tuple[list[str], bool]:
    """→ (动作文本, 是否散文):poses.action 是动作短语;没有 poses 时退回 content 散文。"""
    acts = []
    poses = shot.get("poses")
    if isinstance(poses, dict):
        for v in poses.values():
            if isinstance(v, dict) and str(v.get("action") or "").strip():
                a = str(v["action"]).strip()
                if a.startswith("画外") or a.lower().startswith("off"):
                    continue
                acts.append(a)
    if acts:
        return acts, False
    c = str(shot.get("content_brief") or shot.get("content") or "").strip()
    return ([c] if c else []), True


def run_shots(base: Path, ep: str, rep_all: dict, strict: bool, source: str | None, groups_filter) -> Report:
    ddir = base / "directing" / ep
    sl = _read_json(ddir / "shot_list.json")
    sb = _read_json(ddir / "storyboard.json")
    if source == "storyboard" or (source is None and not sl):
        src_name, doc = "storyboard", sb
    else:
        src_name, doc = "shot_list", sl
    if not doc:
        r = Report(True)
        r.warn("shots_scope_skipped", f"无 directing/{ep}/{src_name}.json")
        return r
    legacy = tc.is_legacy(tc.doc_date(doc))
    r = Report(legacy and not strict)
    speeds, names = load_speeds(base), name_index(base)
    shots_out, groups_out = [], []
    if src_name == "shot_list":
        shots = {s.get("shot_id"): s for s in doc.get("shots") or [] if isinstance(s, dict)}
        groups = [(g.get("group_id"), [shots.get(x) for x in g.get("shots") or []], g.get("total_duration_s"))
                  for g in doc.get("generation_groups") or [] if isinstance(g, dict)]
        dur_key = "duration_s"
    else:
        groups = []
        for sc in doc.get("scenes") or []:
            by_order = {str(s.get("order")): s for s in sc.get("shots_draft") or [] if isinstance(s, dict)}
            for g in sc.get("groups_draft") or []:
                ids = [str(x) for x in (g.get("shots") or g.get("orders") or g.get("shot_orders") or [])]
                members = [by_order.get(x) or by_order.get(x.split("-")[-1]) for x in ids]
                groups.append((f"{sc.get('scene_no')}/g{g.get('group_order') or g.get('order') or ''}",
                               [m for m in members if m], g.get("duration_hint_sum_s")))
        dur_key = "duration_hint_s"
    if groups_filter:
        groups = [g for g in groups if g[0] in set(groups_filter)]
    seen = set()
    for gid, members, total in groups:
        durs = []
        for s in members:
            if not s:
                continue
            sid = s.get("shot_id") or f"{gid}#{s.get('order')}"
            try:
                dur = float(s.get(dur_key) or 0)
            except (TypeError, ValueError):
                dur = 0.0
            durs.append(dur)
            if sid in seen:
                continue
            seen.add(sid)
            lines = _shot_lines(s, speeds, names)
            acts, prose = _shot_actions(s)
            need = tc.shot_need(lines, acts, prose=prose)
            rec = {"shot_id": sid, "group_id": gid, "duration_s": dur, **{k: need[k] for k in ("dialogue_s", "action_s", "margin_s", "need_s")},
                   "beats": need["beats"], "status": "ok"}
            if dur and need["need_s"] > dur + TOL_S:
                rec["status"] = "over"
                rec["over_s"] = round(need["need_s"] - dur, 1)
                r.fail("shot_time_budget_ok", f"{gid}/{sid} 镜长 {dur:g}s < 需求 {need['need_s']:g}s(台词 {need['dialogue_s']:g} + 节拍 {need['action_s']:g} + 余量 {need['margin_s']:g});"
                                             f"{'合并相邻镜或加镜长' if not lines else '对白镜留开口前后余量'}")
            shots_out.append(rec)
        issue = tc.group_density_issue(durs, total)
        grec = {"group_id": gid, "total_s": total, "shots": len(durs), "asl_s": round(sum(durs) / len(durs), 2) if durs else None, "status": "ok"}
        if issue:
            grec["status"] = "dense_over"
            r.fail("group_density_ok", f"{gid} {issue}")
        groups_out.append(grec)
    r.ok("shot_time_budget_ok")
    r.ok("group_density_ok")
    rep_all["shots"] = {"source": src_name, "shots": shots_out, "groups": groups_out,
                        "summary": {"shots": len(shots_out), "shots_over": sum(1 for s in shots_out if s["status"] == "over"),
                                    "groups": len(groups_out), "groups_dense_over": sum(1 for g in groups_out if g["status"] != "ok")}}
    return r


# ================================================================ blocking
def run_blocking(base: Path, ep: str, rep_all: dict, strict: bool, groups_filter) -> Report:
    ddir = base / "directing" / ep
    sl = _read_json(ddir / "shot_list.json")
    if not sl:
        r = Report(True)
        r.warn("blocking_scope_skipped", f"无 directing/{ep}/shot_list.json")
        return r
    legacy = tc.is_legacy(tc.doc_date(sl))
    r = Report(legacy and not strict)
    speeds, names = load_speeds(base), name_index(base)
    shots = {s.get("shot_id"): s for s in sl.get("shots") or [] if isinstance(s, dict)}
    group_of = {sid: g.get("group_id") for g in sl.get("generation_groups") or [] for sid in g.get("shots") or []}
    out = []
    for sid, s in shots.items():
        if groups_filter and group_of.get(sid) not in set(groups_filter):
            continue
        bj = _read_json(ddir / "shots" / sid / "blocking.json")
        if not bj:
            continue
        try:
            dur = float(s.get("duration_s") or 0)
        except (TypeError, ValueError):
            dur = 0.0
        lines = _shot_lines(s, speeds, names)
        beats = tc.blocking_beats(bj)
        need = tc.shot_need(lines, beat_costs=[b["cost_s"] for b in beats])
        rec = {"shot_id": sid, "group_id": group_of.get(sid), "duration_s": dur, "dialogue_s": need["dialogue_s"],
               "beats": beats, "beats_s": need["action_s"], "need_s": need["need_s"], "status": "ok"}
        if dur and need["need_s"] > dur + TOL_S:
            rec["status"] = "over"
            r.fail("beat_budget_ok", f"{group_of.get(sid)}/{sid} 镜长 {dur:g}s 装不下 {len([b for b in beats if b['cost_s']])} 拍({need['action_s']:g}s)"
                                     f"{' + 台词 ' + str(need['dialogue_s']) + 's' if lines else ''} = {need['need_s']:g}s;删拍/并入相邻镜,或回派 shot-planning 加镜长")
        for msg in tc.beat_spacing_issues(bj):
            rec["status"] = "over"
            r.fail("beat_spacing_ok", f"{group_of.get(sid)}/{sid} {msg}")
        out.append(rec)
    r.ok("beat_budget_ok")
    r.ok("beat_spacing_ok")
    rep_all["blocking"] = {"shots": out, "summary": {"shots": len(out), "shots_over": sum(1 for x in out if x["status"] == "over")}}
    return r


# ================================================================ prompt
_USE_RE = re.compile(r"(不采用|Not used)\s*[:：][^。.\n]*[。.]")


def _action_body(body: str) -> str:
    """Shot 段正文去掉机器句(场景激活/画内/画外/构图层次/使用/不采用),只留动作描写。"""
    m = _USE_RE.search(body)
    return body[m.end():] if m else body


def run_prompt(base: Path, ep: str, rep_all: dict, strict: bool, groups_filter) -> Report:
    ddir = base / "directing" / ep
    sl = _read_json(ddir / "shot_list.json")
    pdir = base / "assets" / "prompts" / ep
    if not sl or not pdir.is_dir():
        r = Report(True)
        r.warn("prompt_scope_skipped", f"无 shot_list 或 assets/prompts/{ep}/")
        return r
    legacy = tc.is_legacy(tc.doc_date(sl))
    r = Report(legacy and not strict)
    shots = {s.get("shot_id"): s for s in sl.get("shots") or [] if isinstance(s, dict)}
    out = []
    for g in sl.get("generation_groups") or []:
        gid = g.get("group_id")
        if groups_filter and gid not in set(groups_filter):
            continue
        pj = _read_json(pdir / f"{gid}.json")
        vp = (pj or {}).get("video_prompt") or ""
        if not vp:
            continue
        kind = shot_timing.group_kind(base, ep, gid)
        heads = shot_timing.shot_paragraphs(vp, kind == "h3")
        ids = g.get("shots") or []
        for h in heads:
            k = h["no"] - 1
            if k >= len(ids):
                break
            sid = ids[k]
            body = _action_body(vp[h["head_end"]:h["body_end"]])
            n_conn = tc.count_sequence_connectors(body)
            bj = _read_json(ddir / "shots" / sid / "blocking.json")
            if bj:
                allowed = len([b for b in tc.blocking_beats(bj) if b["cost_s"] > 0]) + 1
                src = "blocking"
            else:
                acts, prose = _shot_actions(shots.get(sid) or {})
                allowed = len(tc.shot_need([], acts, prose=prose)["beats"]) + 1
                src = "poses"
            rec = {"group_id": gid, "shot_id": sid, "connectors": n_conn, "allowed": allowed, "source": src, "status": "ok"}
            if n_conn > allowed:
                rec["status"] = "over"
                r.fail("prompt_beats_bound", f"{gid}/{sid} Shot {h['no']} 顺序连接词 {n_conn} 个 > 节拍预算 {allowed}({src});只把已有节拍写细,不加新的顺序动作")
            out.append(rec)
    r.ok("prompt_beats_bound")
    rep_all["prompt"] = {"shots": out, "summary": {"shots": len(out), "shots_over": sum(1 for x in out if x["status"] == "over")}}
    return r


# ================================================================ main
def main(argv=None):
    def cfg(ap):
        ap.add_argument("groups", nargs="*", help="只查指定组(shots/blocking/prompt 范围)")
        ap.add_argument("--scope", default="all", choices=["all", "script", "shots", "blocking", "prompt"])
        ap.add_argument("--source", default=None, choices=["storyboard", "shot_list"], help="shots 范围取哪份(缺省有 shot_list 用 shot_list)")
        ap.add_argument("--strict", action="store_true", help="存量集也按 FAIL 判")
        ap.add_argument("--no-report", action="store_true")
    args, base = parse_args("时间预算机检(台词 + 动作节拍,docs/time_cost.md)", configure=cfg, argv=argv)
    scopes = ["script", "shots", "blocking", "prompt"] if args.scope == "all" else [args.scope]
    rep_all = {"generated_by": "code/check_time_budget.py", "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "episode": args.ep, "scopes": scopes, "checks": {}, "errors": [], "warnings": []}
    for sc in scopes:
        if sc == "script":
            r = run_script(base, args.ep, rep_all, args.strict)
        elif sc == "shots":
            r = run_shots(base, args.ep, rep_all, args.strict, args.source, args.groups or None)
        elif sc == "blocking":
            r = run_blocking(base, args.ep, rep_all, args.strict, args.groups or None)
        else:
            r = run_prompt(base, args.ep, rep_all, args.strict, args.groups or None)
        rep_all["checks"].update(r.checks)
        rep_all["errors"] += r.errs
        rep_all["warnings"] += r.warns
        if r.legacy and sc in rep_all:
            rep_all[sc]["legacy"] = True
    for w in rep_all["warnings"]:
        print("WARN", w)
    for e in rep_all["errors"]:
        print("FAIL", e)
    rep_all["pass"] = not rep_all["errors"]
    if not args.no_report:
        if "script" in rep_all and len(scopes) == 1:
            out = base / "story" / "episodes" / args.ep / "time_budget.json"
        else:
            out = base / "directing" / args.ep / "time_budget.json"
            if "script" in rep_all:
                sout = base / "story" / "episodes" / args.ep / "time_budget.json"
                sout.parent.mkdir(parents=True, exist_ok=True)
                sout.write_text(json.dumps({k: rep_all[k] for k in ("generated_by", "generated_at", "episode", "script")}, ensure_ascii=False, indent=2) + "\n")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rep_all, ensure_ascii=False, indent=2) + "\n")
        print(f"报告 → {out}")
    if "script" in rep_all:
        s = rep_all["script"]
        print(f"script: Σ需求 {s['need_total_s']}s / 预算 {s['base_budget_s']} (+{s['extend_pct']:g}% 上限 {s['plan'].get('cap_s')}) → {s['plan']['status']}")
    for sc in ("shots", "blocking", "prompt"):
        if sc in rep_all and "summary" in rep_all[sc]:
            print(f"{sc}: {rep_all[sc]['summary']}")
    print(f"{len(rep_all['errors'])} FAIL / {len(rep_all['warnings'])} WARN")
    return 0 if rep_all["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
