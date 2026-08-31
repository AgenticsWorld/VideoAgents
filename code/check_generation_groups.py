#!/usr/bin/env python3
"""check_generation_groups.py — 生成组(generation_groups)机检 + 草案分组工具。

机检规则(WORKFLOW.md §7A / shot-planning SOUL):
  1. groups_cover_all_shots      组覆盖全部镜号,不重不漏
  2. group_shots_contiguous_same_scene  组内镜号连续且同 scene_id
  3. group_duration_4_15_int     total_duration_s ∈ [4,15] 整数,且 = Σ组内 duration_s
  4. group_characters_lte_4      characters_union ≤4
  5. continuity_from_chain       首组 null,其余指向前一组 group_id

§7D ① 机检(workflow.yaml p6-shots validation,默认开启,--skip-7d 跳过):
  6. narration_anchors_cover_all       narration.md 条目 100% 有挂点;挂点镜/组引用合法
  7. narration_window_gte_est_x1.15    可用画面窗口 window_s ≥ est_duration_s×1.15,
                                       且不超挂点镜区间物理时长
  8. audio_plan_complete               每组 audio_plan ∈ {dialogue,narration_over,ambient_only}
                                       且与 has_dialogue/挂点事实一致;ambient_only 必附
                                       silent_rationale
  (dialogue_est_fits_group_x0.7 需 screenplay 对白层估时与角色语速,由 code/check_dialogue_fit.py 执行——
   2026-08-30 起为 p6-dialogue-fit 节点的宿主 CLI,不再由 shot-planning 自查)

组间转场机检 transition_ok(2026-08-28,WORKFLOW.md §9C;默认开启,--skip-transition 跳过):
  9. transition_type_valid            组 transition_in.type ∈ 受控枚举(缺省 = hard_cut);可渲染类型
                                       (dissolve/fade_black/fade_white/dip_black/dip_white)duration_s 落在各自范围;
                                       首组只能 hard_cut / fade_black / fade_white(淡入),没有前组可叠
  10. transition_reason_required      非 hard_cut(含标注型 smash_cut/match_cut)必填 reason 与 intent(可溯 directing_plan 转场清单)
  11. transition_budget_le_1pct       Σ可渲染转场 duration_s ≤ 集预算 budget_s × 1%
  12. narrative_block_paired          narrative_block 同 id 的组必须连续,role 序列 start[/middle…]/end(单组 single);
                                       块首组必有 transition_in(可为显式 hard_cut + reason),块尾组的下一组同样必有

CLI:
  python3 code/check_generation_groups.py <shot_list.json>            # 机检已有分组
  python3 code/check_generation_groups.py <shot_list.json> --propose  # 按贪心规则生成草案分组并打印
  python3 code/check_generation_groups.py <shot_list.json> --propose --write  # 草案写回 shot_list(新增字段)
  python3 code/check_generation_groups.py <shot_list.json> --narration <narration.md>  # 显式指定旁白稿

narration.md 缺省按数据布局自动推导(directing/epNN/shot_list.json →
story/episodes/epNN/narration.md),推导不到且未 --skip-7d 视为机检失败。
草案分组只是兜底基线:正式流程中分组由 storyboard(节拍)起草、shot-planning 定稿,
本脚本的贪心结果不含节拍判断,仅供机检自测与试点手工分组的参照。
"""
import argparse
import json
import re
import sys
from pathlib import Path

MAX_GROUP_S = 15              # 默认=Seedance 2.0 单次生成上限;实际以项目「视频模型设置」
                              # settings.json 的 shot_group.max_group_s 为准(main 里覆盖,4-30)
MIN_GROUP_S = 4
MAX_CHARS = 4
WINDOW_FACTOR = 1.15          # §7D ①:窗口 ≥ est_duration_s×1.15
AUDIO_PLANS = ("dialogue", "narration_over", "ambient_only")
# —— 组间转场契约(shot_list.generation_groups[].transition_in,2026-08-28;render_transitions.py 同源)——
# 可渲染 5 种由宿主 CLI code/render_transitions.py 在 Phase 9 实施(pad 补偿,总时长不变);
# 标注型 2 种不渲染(= 硬切),只供 continuity/QA 核构图对位;缺省 = hard_cut
TRANSITION_RENDERABLE = {
    "dissolve": (0.25, 1.0),      # 叠化(xfade=fade)
    "fade_black": (0.3, 1.5),     # 前组尾淡出到黑,本组硬入;首组 = 从黑淡入
    "fade_white": (0.3, 1.5),     # 同上,白
    "dip_black": (0.3, 1.5),      # 前组淡出到黑 + 本组从黑淡入(xfade=fadeblack)
    "dip_white": (0.3, 1.5),      # 同上,白(xfade=fadewhite)
}
TRANSITION_ANNOTATION = ("smash_cut", "match_cut")
TRANSITION_TYPES = ("hard_cut",) + tuple(TRANSITION_RENDERABLE) + TRANSITION_ANNOTATION
TRANSITION_INTENTS = ("flashback_in", "flashback_out", "time_skip", "scene_change", "montage",
                      "dream_in", "dream_out", "chapter", "episode_open", "other")
TRANSITION_BUDGET_RATIO = 0.01     # Σ可渲染转场时长 ≤ 集预算 1%
BLOCK_KINDS = ("flashback", "dream", "montage", "imagination")
BLOCK_ROLES = ("start", "middle", "end", "single")
# narration.md 条目头:[N-xx | anchor: 场景锚 | est_duration_s: 秒 | source: 章#段]
NARR_ITEM_RE = re.compile(
    r"^\[(N-\d+)\s*\|\s*anchor:\s*[^|\]]+\|\s*est_duration_s:\s*([\d.]+)", re.M)


def project_max_group_s(shot_list_path: Path) -> int:
    """项目「视频模型设置」的生成组时长上限:directing/epNN/shot_list.json →
    项目根 settings.json 的 shot_group.max_group_s;读不到回落 15(Seedance 2.0 口径)。"""
    try:
        st = json.loads((shot_list_path.resolve().parents[2] / "settings.json").read_text())
        v = int(round(float((st.get("shot_group") or {})["max_group_s"])))
        return v if 4 <= v <= 30 else 15
    except Exception:
        return 15


def derive_narration_path(shot_list_path: Path) -> Path | None:
    """directing/epNN/shot_list.json → story/episodes/epNN/narration.md"""
    ep = shot_list_path.parent.name
    p = shot_list_path.parent.parent.parent / "story" / "episodes" / ep / "narration.md"
    return p if p.is_file() else None


def propose_groups(shots: list[dict]) -> list[dict]:
    """贪心草案:同场景、连续、Σ≤15 整数、组内角色 ≤4。"""
    groups, cur = [], []

    def flush():
        if not cur:
            return
        groups.append({
            "group_id": f"grp{len(groups) + 1:03d}",
            "scene_id": cur[0].get("scene_id"),
            "shots": [s["shot_id"] for s in cur],
            "total_duration_s": int(round(sum(s["duration_s"] for s in cur))),
            "characters_union": sorted({c for s in cur for c in (s.get("characters") or [])}),
            "has_dialogue": any(s.get("is_dialogue") for s in cur),
            "continuity_from": groups[-1]["group_id"] if groups else None,
        })
        cur.clear()

    for s in shots:
        chars = set(s.get("characters") or [])
        if cur:
            cur_dur = sum(x["duration_s"] for x in cur)
            cur_chars = {c for x in cur for c in (x.get("characters") or [])}
            if (s.get("scene_id") != cur[0].get("scene_id")
                    or cur_dur + s["duration_s"] > MAX_GROUP_S
                    or len(cur_chars | chars) > MAX_CHARS):
                flush()
        cur.append(s)
    flush()
    return groups


def check(shot_list: dict) -> list[str]:
    errors = []
    shots = shot_list.get("shots") or []
    groups = shot_list.get("generation_groups") or []
    if not groups:
        return ["shot_list 无 generation_groups 字段(未分组)"]
    by_id = {s["shot_id"]: s for s in shots}
    order = {s["shot_id"]: i for i, s in enumerate(shots)}

    # 1. 覆盖不重不漏
    seen = []
    for g in groups:
        seen.extend(g.get("shots") or [])
    if sorted(seen) != sorted(by_id):
        missing = set(by_id) - set(seen)
        dup_or_extra = [x for x in seen if seen.count(x) > 1] + list(set(seen) - set(by_id))
        errors.append(f"groups_cover_all_shots: 缺 {sorted(missing)} / 重或多 {sorted(set(dup_or_extra))}")

    prev_gid = None
    for g in groups:
        gid = g.get("group_id", "?")
        gshots = g.get("shots") or []
        # 2. 连续且同场景
        idxs = [order.get(x) for x in gshots]
        if None in idxs or idxs != list(range(idxs[0], idxs[0] + len(idxs))):
            errors.append(f"{gid} group_shots_contiguous: 镜号不连续 {gshots}")
        scenes = {by_id[x].get("scene_id") for x in gshots if x in by_id}
        if len(scenes) > 1:
            errors.append(f"{gid} same_scene: 跨场景 {sorted(scenes)}")
        # 3. 时长
        td = g.get("total_duration_s")
        real = sum(by_id[x]["duration_s"] for x in gshots if x in by_id)
        if not isinstance(td, int):
            errors.append(f"{gid} duration_int: total_duration_s={td!r} 非整数")
        elif td != int(round(real)):
            errors.append(f"{gid} duration_sum: total_duration_s={td} ≠ Σ镜时长 {real:g}")
        if not (isinstance(td, (int, float)) and MIN_GROUP_S <= td <= MAX_GROUP_S):
            errors.append(f"{gid} duration_range: {td} ∉ [{MIN_GROUP_S},{MAX_GROUP_S}]")
        # 4. 角色数
        chars = g.get("characters_union")
        real_chars = sorted({c for x in gshots if x in by_id for c in (by_id[x].get("characters") or [])})
        if chars is None:
            errors.append(f"{gid} characters_union 缺失")
        elif sorted(chars) != real_chars:
            errors.append(f"{gid} characters_union 与镜表不符: {chars} ≠ {real_chars}")
        if len(real_chars) > MAX_CHARS:
            errors.append(f"{gid} characters_lte_4: 组内角色 {len(real_chars)} > {MAX_CHARS}")
        # 5. 组序链
        if g.get("continuity_from") != prev_gid:
            errors.append(f"{gid} continuity_from: {g.get('continuity_from')!r} ≠ 前组 {prev_gid!r}")
        prev_gid = gid
    return errors


def check_7d(shot_list: dict, narration_md: str | None) -> list[str]:
    """§7D ① 机检:旁白挂点(narration_anchors)+ 逐组音频形态(audio_plan)。"""
    errors = []
    shots = shot_list.get("shots") or []
    groups = shot_list.get("generation_groups") or []
    by_id = {s["shot_id"]: s for s in shots}
    by_gid = {g.get("group_id"): g for g in groups}

    if narration_md is None:
        errors.append("narration_anchors_cover_all: narration.md 未找到"
                      "(--narration 指定或 --skip-7d 显式跳过)")
        narr = {}
    else:
        narr = {m.group(1): float(m.group(2))
                for m in NARR_ITEM_RE.finditer(narration_md)}
        if not narr:
            errors.append("narration_anchors_cover_all: narration.md 解析不到任何条目"
                          "(条目头格式应为 [N-xx | anchor: … | est_duration_s: …])")

    anchors = shot_list.get("narration_anchors")
    if anchors is None:
        errors.append("narration_anchors_cover_all: shot_list 无 narration_anchors 字段"
                      "(§7D ① 挂点未定稿)")
        anchors = []
    anchored_groups = set()
    seen_ids = set()
    for a in anchors:
        nid = a.get("narration_id", "?")
        seen_ids.add(nid)
        if narr and nid not in narr:
            errors.append(f"{nid} anchor_ref: narration.md 无此条目")
        a_shots = a.get("anchor_shots") or []
        gid = a.get("anchor_group")
        g = by_gid.get(gid)
        if not a_shots:
            errors.append(f"{nid} anchor_ref: anchor_shots 为空")
        for sid in a_shots:
            if sid not in by_id:
                errors.append(f"{nid} anchor_ref: 镜 {sid} 不存在")
        if g is None:
            errors.append(f"{nid} anchor_ref: 组 {gid!r} 不存在")
        else:
            anchored_groups.add(gid)
            outside = [s for s in a_shots if s not in (g.get("shots") or [])]
            if outside:
                errors.append(f"{nid} anchor_ref: 镜 {outside} 不属于组 {gid}")
        est = a.get("est_duration_s")
        if narr.get(nid) is not None and est is not None and abs(est - narr[nid]) > 0.11:
            errors.append(f"{nid} est_mismatch: 挂点 est={est} ≠ narration.md {narr[nid]}")
        win = a.get("window_s")
        est_ref = narr.get(nid, est)
        if not isinstance(win, (int, float)):
            errors.append(f"{nid} window: window_s 缺失")
        else:
            span = sum(by_id[s].get("duration_s") or 0 for s in a_shots if s in by_id)
            if span and win > span + 0.01:
                errors.append(f"{nid} window: window_s={win} 超挂点镜区间物理时长 {span:g}"
                              "(窗口应=区间时长−区间内对白占时)")
            if est_ref is not None and win < est_ref * WINDOW_FACTOR - 1e-9:
                errors.append(f"{nid} narration_window_gte_est_x1.15: window_s={win} <"
                              f" est {est_ref}×{WINDOW_FACTOR}={est_ref * WINDOW_FACTOR:.2f}"
                              "(优先调镜时长消化,装不下上报回派 narration 精简)")
    missing = sorted(set(narr) - seen_ids, key=lambda x: int(x.split("-")[1]))
    if missing:
        errors.append(f"narration_anchors_cover_all: 条目缺挂点 {missing}")

    for g in groups:
        gid = g.get("group_id", "?")
        plan = g.get("audio_plan")
        has_dlg = bool(g.get("has_dialogue"))
        has_narr = gid in anchored_groups
        if plan not in AUDIO_PLANS:
            errors.append(f"{gid} audio_plan_complete: audio_plan={plan!r} 非法或缺失")
            continue
        expect = ("dialogue" if has_dlg
                  else "narration_over" if has_narr else "ambient_only")
        if plan != expect:
            errors.append(f"{gid} audio_plan_consistent: audio_plan={plan},但按"
                          f" has_dialogue={has_dlg}/挂点={'有' if has_narr else '无'}"
                          f" 应为 {expect}")
        if plan == "ambient_only" and not (g.get("silent_rationale") or "").strip():
            errors.append(f"{gid} silent_rationale: ambient_only 组未说明纯画面"
                          "能讲清叙事的理由(§7D ① 无声组核查)")
    return errors


def transition_of(group: dict) -> dict:
    """组入口转场,规范化:缺省 / None / 空对象 = hard_cut。"""
    t = group.get("transition_in")
    if not isinstance(t, dict) or not t:
        return {"type": "hard_cut"}
    t = dict(t)
    t["type"] = str(t.get("type") or "hard_cut")
    return t


def check_transitions(shot_list: dict) -> list[str]:
    """transition_ok(2026-08-28):组入口转场 transition_in + 叙事块 narrative_block 机检。"""
    errors = []
    groups = shot_list.get("generation_groups") or []
    budget = shot_list.get("budget_s") or shot_list.get("total_duration_s") \
        or sum(g.get("total_duration_s") or 0 for g in groups)
    render_total = 0.0
    has_tr = {}
    for i, g in enumerate(groups):
        gid = g.get("group_id", "?")
        raw = g.get("transition_in")
        if raw is not None and not isinstance(raw, dict):
            errors.append(f"{gid} transition_type_valid: transition_in 须为对象,得到 {type(raw).__name__}")
            continue
        t = transition_of(g)
        ty = t["type"]
        has_tr[gid] = bool(raw)
        if ty not in TRANSITION_TYPES:
            errors.append(f"{gid} transition_type_valid: type={ty!r} 不在枚举 {list(TRANSITION_TYPES)}")
            continue
        dur = t.get("duration_s")
        if ty in TRANSITION_RENDERABLE:
            lo, hi = TRANSITION_RENDERABLE[ty]
            if not isinstance(dur, (int, float)) or isinstance(dur, bool):
                errors.append(f"{gid} transition_type_valid: {ty} 缺 duration_s(范围 {lo}–{hi}s)")
            elif not (lo - 1e-9 <= dur <= hi + 1e-9):
                errors.append(f"{gid} transition_type_valid: {ty} duration_s={dur} ∉ [{lo},{hi}]")
            else:
                render_total += float(dur)
            if i == 0 and ty in ("dissolve", "dip_black", "dip_white"):
                errors.append(f"{gid} transition_type_valid: 首组无前组可叠,{ty} 非法"
                              "(首组只能 hard_cut / fade_black / fade_white 淡入)")
        elif dur not in (None, 0, 0.0):
            errors.append(f"{gid} transition_type_valid: {ty} 不渲染,duration_s 应省略或为 0(得到 {dur})")
        if ty != "hard_cut":
            if not str(t.get("reason") or "").strip():
                errors.append(f"{gid} transition_reason_required: {ty} 缺 reason(须可溯 directing_plan 转场清单)")
            if t.get("intent") not in TRANSITION_INTENTS:
                errors.append(f"{gid} transition_reason_required: {ty} intent={t.get('intent')!r}"
                              f" 不在枚举 {list(TRANSITION_INTENTS)}")
    if budget and render_total > float(budget) * TRANSITION_BUDGET_RATIO + 1e-9:
        errors.append(f"transition_budget_le_1pct: Σ可渲染转场 {render_total:g}s >"
                      f" 集预算 {budget}s × {TRANSITION_BUDGET_RATIO:g} = {float(budget) * TRANSITION_BUDGET_RATIO:.2f}s")

    # narrative_block:同 id 连续、role 序列合法、块首与块尾下一组都有 transition_in
    blocks: dict[str, list[tuple[int, str, str]]] = {}
    for i, g in enumerate(groups):
        nb = g.get("narrative_block")
        if nb is None:
            continue
        gid = g.get("group_id", "?")
        if not isinstance(nb, dict) or not nb.get("id"):
            errors.append(f"{gid} narrative_block_paired: narrative_block 须为含 id 的对象")
            continue
        if nb.get("kind") not in BLOCK_KINDS:
            errors.append(f"{gid} narrative_block_paired: kind={nb.get('kind')!r} 不在枚举 {list(BLOCK_KINDS)}")
        if nb.get("role") not in BLOCK_ROLES:
            errors.append(f"{gid} narrative_block_paired: role={nb.get('role')!r} 不在枚举 {list(BLOCK_ROLES)}")
        blocks.setdefault(str(nb["id"]), []).append((i, gid, str(nb.get("role"))))
    for bid, rows in blocks.items():
        idxs = [r[0] for r in rows]
        if idxs != list(range(idxs[0], idxs[0] + len(idxs))):
            errors.append(f"narrative_block_paired: 块 {bid} 的组不连续 {[r[1] for r in rows]}")
        roles = [r[2] for r in rows]
        expect = ["single"] if len(rows) == 1 else ["start"] + ["middle"] * (len(rows) - 2) + ["end"]
        if roles != expect:
            errors.append(f"narrative_block_paired: 块 {bid} role 序列 {roles} 应为 {expect}")
        first_gid = rows[0][1]
        if not has_tr.get(first_gid):
            errors.append(f"{first_gid} narrative_block_paired: 块 {bid} 入口组缺 transition_in"
                          "(可为显式 hard_cut + reason,表示有意硬切)")
        last_i = rows[-1][0]
        if last_i + 1 < len(groups):
            nxt = groups[last_i + 1].get("group_id", "?")
            if not has_tr.get(nxt):
                errors.append(f"{nxt} narrative_block_paired: 块 {bid} 出口(块尾 {rows[-1][1]} 的下一组)缺 transition_in")
    return errors


def main():
    ap = argparse.ArgumentParser(description="generation_groups 机检 / 草案分组")
    ap.add_argument("shot_list", help="shot_list.json 路径")
    ap.add_argument("--propose", action="store_true", help="生成贪心草案分组")
    ap.add_argument("--write", action="store_true", help="配合 --propose:把草案写回 shot_list.json")
    ap.add_argument("--narration", help="narration.md 路径(缺省按数据布局自动推导)")
    ap.add_argument("--skip-7d", action="store_true",
                    help="跳过 §7D ① 旁白挂点/audio_plan 机检(仅查生成组)")
    ap.add_argument("--skip-transition", action="store_true",
                    help="跳过组间转场机检 transition_ok(transition_in / narrative_block)")
    args = ap.parse_args()

    path = Path(args.shot_list)
    data = json.loads(path.read_text())
    global MAX_GROUP_S
    MAX_GROUP_S = project_max_group_s(path)

    if args.propose:
        groups = propose_groups(data.get("shots") or [])
        data["generation_groups"] = groups
        durs = [g["total_duration_s"] for g in groups]
        sizes = [len(g["shots"]) for g in groups]
        print(f"草案分组: {len(data.get('shots') or [])} 镜 → {len(groups)} 组;"
              f" 组镜数分布 {sorted(set(sizes))},组时长 {min(durs)}–{max(durs)}s")
        if args.write:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
            print(f"已写回 {path}")

    errors = check(data)
    if not args.skip_7d:
        narr_path = Path(args.narration) if args.narration else derive_narration_path(path)
        narr_text = narr_path.read_text() if narr_path and narr_path.is_file() else None
        errors += check_7d(data, narr_text)
    if not args.skip_transition:
        errors += check_transitions(data)
    if errors:
        print(f"机检未通过({len(errors)} 项):")
        for e in errors:
            print(f"  ✗ {e}")
        sys.exit(1)
    groups = data["generation_groups"]
    n_anchor = len(data.get("narration_anchors") or [])
    n_tr = sum(1 for g in groups if transition_of(g)["type"] != "hard_cut")
    print(f"机检通过: {len(groups)} 组全部合规"
          + ("" if args.skip_7d else f";§7D ① 挂点 {n_anchor} 条/audio_plan 齐备")
          + ("" if args.skip_transition else f";transition_ok 非硬切转场 {n_tr} 处"))


if __name__ == "__main__":
    main()
