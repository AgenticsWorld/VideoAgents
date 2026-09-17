#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""post_apply.py — 后期处方宿主 CLI:按台账出片/登记版本/机检/整集拼装(WORKFLOW.md §9D,2026-09-11)。

背景:「后期预览」页(/preview/post)把每项后期调整记成一条处方(edit/epNN/post_plan.json),分镜组母本
assets/clips/epNN/grpNNN.mp4 永不覆盖,产物按版本另存 assets/post/epNN/grpNNN/v{n}.mp4。本 CLI 是唯一
的执行工序:ffmpeg 类处方在本机直接出片;agent 类处方由 10-editing/post-finishing 出片后用 register 登记;
出成片时按当前指针重拼正片(cut_post)→ 转场 → 终版封装,全部走宿主 CLI,Agent 禁止自写 ffmpeg。

  blocks       [--json] [--stats] [--verify --by <工位id>]  列出本集叙事块(shot_list narrative_block:闪回/梦境/蒙太奇/想象段)及其组、场景、当前版本;
               --stats 另测每块「最新版本」与块前后现实组的平均明度,给出明度台阶 %(调色规划工位的自检依据);
               --verify 机检 grade_plan_proposed:该工位开过处方的块,处方全部已出片/已采纳、块内有母本的组都出过带其处方的版本
  propose      --kind <做法> (--block <块id> | --scene <SCN> | --group grpNNN [--t0 s --t1 s] | --episode)
               --params '<JSON>' --note "<说明>" --by <工位id>
               工位开处方的唯一入口(10-editing/grade-planner 等):参数按目录收敛,同一 (by, 做法, 作用域) 幂等——
               未裁决的就地更新并退回草稿,不堆重复;只开处方,不出片、不采纳(出片用 apply,采纳归用户 H3P)
  apply        --recipe <id>[ --recipe <id2>...] [--group grpNNN] [--preview]
               对处方作用域内的每个组,在当前版本之上施加 ffmpeg 滤镜链出新版本(多条处方=同一版本一次链上);
               --preview 只出前 4 秒 480p 低清到 assets/post/epNN/<grp>/refs/preview_<id>.mp4,不进版本链
  register     --recipe <id> --file <路径> [--group grpNNN]   agent 类处方把外部产物登记为新版本(状态→已出片)
  register     --group grpNNN --file <路径> [--note 工单号]   分镜剪辑「派单剪辑师」产物登记为本组新版本(无处方)
  adopt        --recipe <id>       采纳:版本指针指向该产物;转场处方回写 shot_list.transition_in
  discard      --recipe <id>       弃用:指针退回;文件不删
  rollback     --group grpNNN --to <v>   把某组指针挪到任一版本(0 = 母本)
  check        机检 post_ok(出成片前必跑),台账 edit/epNN/post_check.json:
                 post_plan_applied     已采纳处方的产物文件存在且指纹与台账一致;当前指针文件存在
                 post_no_pending       没有「已派单」处方(FAIL);有草稿只 WARN
                 color_consistency     同场次相邻组均值色差(近似 ΔE)不超阈值(WARN)
                 sfx_cues_resolved     音效点位表每条已选来源或显式略过(WARN)
                 transitions_synced    已采纳转场处方与 shot_list.transition_in 一致(FAIL)
                 flashback_graded      闪回块的每组都落在带「调色与光感」处方的已采纳版本上(WARN;无闪回块 = PASS)
  sync-timeline 把各组当前指针写进 edit/epNN/timeline.json:tracks.video[].src 指向 build-cut 归一化的后期段文件
               (assets/post/epNN/_cut/<grp>.mp4,与 render_transitions 分段流拷贝口径一致),原值存 src_orig/in_orig/out_orig,
               首次改写备份 timeline.pre_post.json;同时把各组当前版本的时长编辑(插黑/定格/删段,版本条目 time_ops)
               平移到原粗剪基准写成 edit/epNN/timemap.json(post_versions 层),finalize_episode 据此平移外挂声轨/字幕(§9B)
  build-cut    按 timeline 组序把各组当前版本归一化后拼成 edit/epNN/cut_post.mp4(整集级 ffmpeg 处方如水印在此施加)
  finalize     build-cut → sync-timeline → render_transitions.py render(--src cut_post.mp4 --out cut_post_v2.mp4)
               → finalize_episode.py assemble --cut <cut_post_v2|cut_post> → check;原 final.mp4 首次备份为 final.pre_post.mp4
  cleanup      版本保留规则:母本 + 最近两个已采纳版本(+ 当前指针),其余删文件留记录(H3P 签字时自动跑)
  status       [--json] 打印台账摘要

用法:
  python3 code/post_apply.py apply    --project <slug> --ep epNN --recipe rcp-xxxx [--preview]
  python3 code/post_apply.py check    --project <slug> --ep epNN
  python3 code/post_apply.py finalize --project <slug> --ep epNN
退出码:0 成功/全 PASS;1 处方执行失败或任一 FAIL(WARN 不影响);2 参数/文件缺失。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args, REPO_ROOT, DATA_DIR  # noqa: E402  副作用:modules/ 入 sys.path

import post_fx as fx  # noqa: E402
import post_plan as pp  # noqa: E402
import timemap  # noqa: E402

CHECK_NAME = "post_ok"
DELTA_E_WARN = 14.0


def _log(tag: str, msg: str) -> None:
    print(f"[{tag:<5}] {msg}", flush=True)


# ---------------------------------------------------------------- 组上下文
def _block_of(g: dict) -> dict:
    nb = g.get("narrative_block") if isinstance(g.get("narrative_block"), dict) else {}
    return {"block_id": str(nb.get("id") or ""), "block_kind": str(nb.get("kind") or "")}


def load_groups(proj: Path, ep: str) -> list[dict]:
    """timeline 组序优先(含 cum_start),回退 shot_list 顺序。返回 [{group_id, scene_id, scene_no, block_id, block_kind, order, in, out, cum_start_s}]"""
    sl = pp.read_json(proj / "directing" / ep / "shot_list.json") or {}
    gmeta = {g.get("group_id"): g for g in (sl.get("generation_groups") or []) if isinstance(g, dict) and g.get("group_id")}
    tl = pp.read_json(proj / "edit" / ep / "timeline.json") or {}
    rows = []
    tracks = ((tl.get("tracks") or {}).get("video") or []) if isinstance(tl, dict) else []
    if tracks:
        for i, t in enumerate(tracks):
            gid = t.get("group_id")
            if not gid:
                continue
            g = gmeta.get(gid) or {}
            t_in = t.get("in_orig") if t.get("in_orig") is not None else t.get("in")
            t_out = t.get("out_orig") if t.get("out_orig") is not None else t.get("out")
            rows.append({"group_id": gid, "scene_id": g.get("scene_id") or t.get("scene_id") or "", "scene_no": g.get("scene_no") or "",
                         **_block_of(g), "order": i, "in": float(t_in or 0), "out": float(t_out or 0),
                         "cum_start_s": float(t.get("cum_start_s") or 0), "src": t.get("src_orig") or t.get("src") or f"assets/clips/{ep}/{gid}.mp4"})
    else:
        cum = 0.0
        for i, (gid, g) in enumerate(gmeta.items()):
            d = float(g.get("total_duration_s") or 0)
            rows.append({"group_id": gid, "scene_id": g.get("scene_id") or "", "scene_no": g.get("scene_no") or "",
                         **_block_of(g), "order": i, "in": 0.0, "out": d, "cum_start_s": cum, "src": f"assets/clips/{ep}/{gid}.mp4"})
            cum += d
    return rows


def groups_in_scope(scope: dict, groups: list[dict]) -> list[dict]:
    return [g for g in groups if pp.scope_matches_group(scope, g)]


def lut_files(proj: Path) -> dict[str, str]:
    return {p["id"]: p["file"] for p in pp.lut_presets(proj, DATA_DIR) if p.get("file")}


def scene_palette_from_script(proj: Path, ep: str, scene_no: str) -> list[str]:
    """color_script.json:episodes[ep].segments[].scenes 含 scene_no → palette。"""
    cs = pp.read_json(proj / "bible" / "color_script.json") or {}
    for e in cs.get("episodes", []) or []:
        if not isinstance(e, dict) or (e.get("episode") or e.get("episode_id")) != ep:
            continue
        for seg in e.get("segments", []) or []:
            if isinstance(seg, dict) and scene_no in (seg.get("scenes") or []):
                return [c for c in (seg.get("palette") or []) if isinstance(c, str)]
        return [c for c in (e.get("key_palette") or []) if isinstance(c, str)]
    return []


# ---------------------------------------------------------------- apply / register
def do_apply(proj: Path, ep: str, recipe_ids: list[str], only_group: str | None, preview: bool) -> int:
    fx.require_tools("ffmpeg", "ffprobe")
    plan = pp.load_plan(proj, ep)
    recipes = []
    for rid in recipe_ids:
        r = pp.find_recipe(plan, rid)
        if not r:
            _log("FAIL", f"处方不存在:{rid}")
            return 2
        if r.get("exec") != "ffmpeg":
            _log("FAIL", f"{rid}({r.get('kind')})不是 ffmpeg 类处方,agent 类请出片后用 register 登记")
            return 2
        recipes.append(r)
    if not recipes:
        return 2
    groups = load_groups(proj, ep)
    targets = groups_in_scope(recipes[0]["scope"], groups)
    for r in recipes[1:]:
        ids = {g["group_id"] for g in groups_in_scope(r["scope"], groups)}
        targets = [g for g in targets if g["group_id"] in ids]
    if only_group:
        targets = [g for g in targets if g["group_id"] == only_group]
    if not targets:
        _log("FAIL", "作用域内没有分镜组(或 --group 不在作用域内)")
        return 2
    if preview:
        targets = targets[:1]
    luts = lut_files(proj)
    ok_all = True
    outputs: dict[str, dict] = {}
    for g in targets:
        gid = g["group_id"]
        src = pp.current_file(proj, ep, gid, plan)
        if not src:
            _log("SKIP", f"{gid} 没有母本 clip,跳过")
            continue
        info = fx.probe(src)
        ctx = fx.Ctx(proj, src, info, luts)
        # 场次色板留空 → 自动读色彩脚本
        for r in recipes:
            if r["kind"] == "scene_palette" and not r["params"].get("palette"):
                r["params"]["palette"] = scene_palette_from_script(proj, ep, g.get("scene_no") or "")
        try:
            tail = fx.preview_scale_tail() if preview else ""
            graph = fx.build_graph(recipes, ctx, tail)
            base_v = pp.current_version(plan, gid)
            if preview:
                dst = pp.post_dir(proj, ep) / gid / "refs" / f"preview_{recipes[0]['id']}.mp4"
                t0 = float(recipes[0]["scope"].get("t0") or 0) if recipes[0]["scope"].get("level") == "range" else 0.0
                fx.apply_graph(src, dst, graph, ctx, preview=True, preview_from=t0)
                _log("DONE", f"{gid} 预览 {dst.relative_to(proj)}(基于 v{base_v}){' · ' + '; '.join(ctx.notes) if ctx.notes else ''}")
                print(json.dumps({"preview": str(dst.relative_to(proj)), "group_id": gid, "base_v": base_v, "notes": ctx.notes}, ensure_ascii=False))
                continue
            v = pp.next_version_no(plan, gid)
            dst = pp.post_dir(proj, ep) / gid / f"v{v}.mp4"
            t_start = time.time()
            fx.apply_graph(src, dst, graph, ctx, preview=False)
            ver = pp.register_version(plan, proj, ep, gid, str(dst.relative_to(proj)), [r["id"] for r in recipes], base_v)
            outputs[gid] = {"v": ver["v"], "file": ver["file"]}
            _log("DONE", f"{gid} v{ver['v']} ← v{base_v} {dst.relative_to(proj)} {time.time() - t_start:.0f}s{' · ' + '; '.join(ctx.notes) if ctx.notes else ''}")
        except Exception as e:  # noqa: BLE001
            ok_all = False
            _log("FAIL", f"{gid}:{str(e)[-600:]}")
            for r in recipes:
                pp.set_status(r, "failed", error=str(e)[-500:])
            pp.save_plan(proj, ep, plan)
            return 1
    if preview:
        return 0
    for r in recipes:
        prev = (r.get("output") or {}).get("versions") or {}
        merged = {**prev, **outputs}
        pp.set_status(r, "applied", output={"versions": merged}, error="", applied_at=pp._now())
        r["cost"]["actual"] = f"ffmpeg 本机 · {len(outputs)} 组"
    pp.save_plan(proj, ep, plan)
    return 0 if ok_all else 1


def do_propose(proj: Path, ep: str, kind: str, scope: dict, params_json: str, note: str, by: str) -> int:
    try:
        params = json.loads(params_json) if params_json else {}
        if not isinstance(params, dict):
            raise ValueError("--params 须为 JSON 对象")
    except ValueError as e:
        _log("FAIL", f"--params 解析失败:{e}")
        return 2
    groups = load_groups(proj, ep)
    lv = scope.get("level")
    if lv == "block" and not any(g["block_id"] == scope.get("block_id") for g in groups):
        known = ", ".join(b["block_id"] for b in pp.narrative_blocks(groups)) or "无"
        _log("FAIL", f"叙事块不存在:{scope.get('block_id')}(本集 shot_list narrative_block:{known})")
        return 2
    if lv == "scene" and not any(g["scene_id"] == scope.get("scene_id") for g in groups):
        _log("FAIL", f"场次不在本集:{scope.get('scene_id')}")
        return 2
    if lv in ("group", "range"):
        g = next((x for x in groups if x["group_id"] == scope.get("group_id")), None)
        if not g:
            _log("FAIL", f"分镜组不在本集:{scope.get('group_id')}")
            return 2
        scope["scene_id"] = g["scene_id"]
    plan = pp.load_plan(proj, ep)
    try:
        r, how = pp.propose_recipe(plan, kind, scope, params, {}, note, by)
    except ValueError as e:
        _log("FAIL", str(e))
        return 2
    if how != "unchanged":
        pp.save_plan(proj, ep, plan)
    n = len(groups_in_scope(r["scope"], groups))
    _log("DONE", f"{r['id']} {how}:{pp.KIND_BY_ID[kind]['label']} · {r['scope']} · 覆盖 {n} 组 · 状态 {pp.STATUS_LABEL[r['status']]}")
    print(json.dumps({"recipe": r["id"], "result": how, "kind": kind, "scope": r["scope"], "params": r["params"],
                      "groups": n, "status": r["status"]}, ensure_ascii=False))
    return 0


def _latest_file(proj: Path, ep: str, gid: str, plan: dict) -> tuple[Path | None, int]:
    """该组最新一个文件仍在的版本(已出片未采纳的也算),没有就回母本。"""
    for ver in sorted(pp.group_versions(plan, gid), key=lambda x: -int(x.get("v") or 0)):
        f = proj / str(ver.get("file") or "")
        if not ver.get("cleaned") and f.is_file():
            return f, int(ver.get("v") or 0)
    return pp.version_file(proj, ep, gid, 0, plan), 0


def do_blocks(proj: Path, ep: str, as_json: bool, stats: bool, verify_by: str = "") -> int:
    plan = pp.load_plan(proj, ep)
    groups = load_groups(proj, ep)
    blocks = pp.narrative_blocks(groups)
    by_id = {g["group_id"]: g for g in groups}
    if stats:
        fx.require_tools("ffmpeg", "ffprobe")

    def luma(gid: str, latest: bool) -> float | None:
        g = by_id[gid]
        f = _latest_file(proj, ep, gid, plan)[0] if latest else pp.current_file(proj, ep, gid, plan)
        if not f:
            return None
        dur = max(0.0, g["out"] - g["in"]) or fx.probe(f)["duration"]
        return fx.frame_stats(f, g["in"] + dur / 2)["luma"]

    for b in blocks:
        idx = [by_id[x]["order"] for x in b["groups"]]
        prev = next((g for g in reversed(groups) if g["order"] < min(idx) and g["block_id"] != b["block_id"]), None)
        nxt = next((g for g in groups if g["order"] > max(idx) and g["block_id"] != b["block_id"]), None)
        b["neighbors"] = [g["group_id"] for g in (prev, nxt) if g]
        b["versions"] = {gid: {"current": pp.current_version(plan, gid), "latest": _latest_file(proj, ep, gid, plan)[1]} for gid in b["groups"]}
        b["recipes"] = [{"id": r["id"], "kind": r["kind"], "status": r["status"], "by": r.get("created_by")}
                        for r in plan.get("recipes", []) if r.get("status") != "discarded"
                        and any(pp.scope_matches_group(r.get("scope", {}), by_id[gid]) for gid in b["groups"])
                        and r.get("section") == "grade"]
        if stats:
            try:
                inner = [v for v in (luma(gid, True) for gid in b["groups"]) if v is not None]
                outer = [v for v in (luma(gid, False) for gid in b["neighbors"]) if v is not None]
                if inner and outer:
                    li, lo = sum(inner) / len(inner), sum(outer) / len(outer)
                    b["luma"] = {"block": round(li, 4), "neighbors": round(lo, 4),
                                 "step_pct": round(100 * abs(li - lo) / max(lo, 1e-4), 1)}
            except Exception as e:  # noqa: BLE001
                b["luma_error"] = str(e)[:200]
    rc = 0
    if verify_by:
        bad, mine_total = [], 0
        for b in blocks:
            mine = [r for r in plan.get("recipes", []) if r.get("created_by") == verify_by and r.get("status") != "discarded"
                    and r.get("section") == "grade" and any(pp.scope_matches_group(r.get("scope", {}), by_id[gid]) for gid in b["groups"])]
            if not mine:
                continue
            mine_total += len(mine)
            bad += [f"{b['block_id']}:{r['id']} {pp.STATUS_LABEL.get(r['status'], r['status'])}" for r in mine if r["status"] not in ("applied", "adopted")]
            ids = {r["id"] for r in mine}
            for gid in b["groups"]:
                if pp.version_file(proj, ep, gid, 0, plan) and not any(ids & set(v.get("recipes") or []) for v in pp.group_versions(plan, gid)):
                    bad.append(f"{b['block_id']}:{gid} 未出片")
        rc = 1 if bad else 0
        detail = "; ".join(bad[:10]) if bad else (f"{mine_total} 条处方全部已出片" if mine_total else f"{verify_by} 未开任何处方(无叙事块或无调色意图)")
        print(f"[CHECK] grade_plan_proposed: {'FAIL' if bad else 'PASS'}  {detail}", flush=True)
    if as_json:
        print(json.dumps({"ep": ep, "blocks": blocks}, ensure_ascii=False))
        return rc
    if not blocks:
        _log("INFO", "本集 shot_list 没有 narrative_block(无闪回/梦境/蒙太奇/想象段)")
    for b in blocks:
        lm = b.get("luma")
        _log("INFO", f"{b['block_id']}({b['kind']}){len(b['groups'])} 组 {b['groups'][0]}…{b['groups'][-1]} · 场景 {','.join(b['scene_ids'])}"
                     f" · 调色处方 {len(b['recipes'])} 条" + (f" · 明度 块 {lm['block']:.3f} / 邻 {lm['neighbors']:.3f} / 台阶 {lm['step_pct']}%" if lm else ""))
    return rc


def do_register(proj: Path, ep: str, rid: str | None, file: str, group: str | None, note: str = "") -> int:
    plan = pp.load_plan(proj, ep)
    r = pp.find_recipe(plan, rid) if rid else None
    if rid and not r:
        _log("FAIL", f"处方不存在:{rid}")
        return 2
    f = Path(file)
    f = f if f.is_absolute() else proj / file
    if not f.is_file():
        _log("FAIL", f"文件不存在:{file}")
        return 2
    gid = group or (r["scope"].get("group_id") if r else None)
    if not gid:
        _log("FAIL", "整集/场次作用域的处方登记时须 --group 指明是哪一组的产物")
        return 2
    try:
        f.relative_to(proj)
    except ValueError:
        _log("FAIL", "产物必须在项目目录内(建议 assets/post/epNN/<grp>/)")
        return 2
    base_v = pp.current_version(plan, gid)
    if not r:
        # 分镜剪辑「派单剪辑师」工单的产物:无处方,直接登记为本组新版本(不动指针),note 记工单号
        ver = pp.register_version(plan, proj, ep, gid, str(f.relative_to(proj)), [], base_v, by="edit")
        ver["note"] = note or f"剪辑师产物 {f.name}"
        for o in plan.get("edit_orders") or []:
            if note and o.get("id") == note:
                o["v"] = ver["v"]
                o["registered_at"] = pp._now()
        pp.save_plan(proj, ep, plan)
        _log("PASS", f"{gid} 登记 v{ver['v']} ← {ver['file']}(剪辑师产物,{ver['note']})")
        return 0
    ver = pp.register_version(plan, proj, ep, gid, str(f.relative_to(proj)), [rid], base_v, by="register")
    out = (r.get("output") or {}).get("versions") or {}
    out[gid] = {"v": ver["v"], "file": ver["file"]}
    pp.set_status(r, "applied", output={"versions": out}, error="", applied_at=pp._now())
    pp.save_plan(proj, ep, plan)
    _log("DONE", f"{gid} v{ver['v']} 登记 {ver['file']}(处方 {rid} → 已出片)")
    return 0


def do_adopt(proj: Path, ep: str, rid: str, discard: bool = False) -> int:
    plan = pp.load_plan(proj, ep)
    r = pp.find_recipe(plan, rid)
    if not r:
        _log("FAIL", f"处方不存在:{rid}")
        return 2
    try:
        (pp.discard_recipe if discard else pp.adopt_recipe)(proj, ep, plan, r)
    except ValueError as e:
        _log("FAIL", str(e))
        return 1
    pp.save_plan(proj, ep, plan)
    _log("DONE", f"{rid} → {pp.STATUS_LABEL[r['status']]}")
    return 0


def do_rollback(proj: Path, ep: str, gid: str, to: int) -> int:
    plan = pp.load_plan(proj, ep)
    try:
        pp.adopt_version(plan, gid, to)
    except ValueError as e:
        _log("FAIL", str(e))
        return 1
    pp.save_plan(proj, ep, plan)
    _log("DONE", f"{gid} 指针 → v{to}")
    return 0


# ---------------------------------------------------------------- check
def do_check(proj: Path, ep: str, write: bool = True) -> tuple[bool, list[dict]]:
    plan = pp.load_plan(proj, ep)
    groups = load_groups(proj, ep)
    checks: list[dict] = []

    def rec(name, ok, detail="", warn=False):
        tag = "WARN" if warn else ("PASS" if ok else "FAIL")
        checks.append({"name": name, "status": tag, "detail": detail})
        print(f"[CHECK] {name}: {tag}" + (f"  {detail}" if detail else ""), flush=True)

    # 1 post_plan_applied
    bad = []
    for r in plan.get("recipes", []):
        if r.get("status") != "adopted" or r.get("exec") not in ("ffmpeg", "agent"):
            continue
        for gid, info in ((r.get("output") or {}).get("versions") or {}).items():
            ver = next((x for x in pp.group_versions(plan, gid) if int(x.get("v") or 0) == int(info.get("v") or 0)), None)
            if not ver:
                bad.append(f"{r['id']}:{gid} 版本记录缺失")
                continue
            if ver.get("cleaned"):
                continue
            f = proj / str(ver.get("file") or "")
            if not f.is_file():
                bad.append(f"{gid} v{ver['v']} 文件缺失")
            elif pp.file_fingerprint(f) != ver.get("fingerprint"):
                bad.append(f"{gid} v{ver['v']} 指纹不符(文件被改动)")
    for gid, v in (plan.get("current") or {}).items():
        if int(v or 0) > 0 and not pp.version_file(proj, ep, gid, int(v), plan):
            bad.append(f"{gid} 当前指针 v{v} 文件缺失")
    rec("post_plan_applied", not bad, "; ".join(bad[:6]) + ("…" if len(bad) > 6 else "") if bad else f"已采纳处方产物齐全,指针 {sum(1 for v in (plan.get('current') or {}).values() if int(v or 0) > 0)} 组在后期版本")

    # 2 post_no_pending
    s = pp.summary(plan)
    if s.get("dispatched"):
        rec("post_no_pending", False, f"派单中 {s['dispatched']} 条,等回来采纳或弃用后再出成片")
    elif s.get("draft") or s.get("applied"):
        rec("post_no_pending", True, f"草稿 {s.get('draft', 0)} 条、已出片未裁决 {s.get('applied', 0)} 条(不进成片)", warn=True)
    else:
        rec("post_no_pending", True, "无待办处方")

    # 3 color_consistency(同场次相邻组,当前版本中间帧均值)
    warn_pairs = []
    measured = 0
    try:
        stats = {}
        for g in groups:
            f = pp.current_file(proj, ep, g["group_id"], plan)
            if not f:
                continue
            dur = max(0.0, g["out"] - g["in"])
            stats[g["group_id"]] = fx.frame_stats(f, g["in"] + dur / 2)
            measured += 1
        for a, b in zip(groups, groups[1:]):
            if a["scene_id"] and a["scene_id"] == b["scene_id"] and a["group_id"] in stats and b["group_id"] in stats:
                sa, sb = stats[a["group_id"]], stats[b["group_id"]]
                de = 100 * ((sa["r"] - sb["r"]) ** 2 + (sa["g"] - sb["g"]) ** 2 + (sa["b"] - sb["b"]) ** 2) ** 0.5
                if de > DELTA_E_WARN:
                    warn_pairs.append(f"{a['group_id']}→{b['group_id']} {de:.0f}")
        rec("color_consistency", not warn_pairs, ("同场相邻组色差超 %g:" % DELTA_E_WARN) + ", ".join(warn_pairs[:8]) if warn_pairs else f"{measured} 组实测,同场相邻组色差均在阈值内", warn=bool(warn_pairs))
    except Exception as e:  # noqa: BLE001
        rec("color_consistency", True, f"未能实测:{str(e)[:120]}", warn=True)

    # 4 sfx_cues_resolved
    sfx = pp.load_sfx(proj, ep)
    rows = sfx.get("rows") or []
    if not rows:
        rec("sfx_cues_resolved", True, "无音效点位表(未从分镜 cue 生成)")
    else:
        unresolved = [r for r in rows if not r.get("source") or (r.get("source") in ("library", "generate") and not r.get("file"))]
        rec("sfx_cues_resolved", not unresolved, f"{len(unresolved)}/{len(rows)} 条点位未落地" if unresolved else f"{len(rows)} 条点位全部落地或显式略过", warn=bool(unresolved))

    # 5 transitions_synced
    sl = pp.read_json(proj / "directing" / ep / "shot_list.json") or {}
    tin = {g.get("group_id"): g.get("transition_in") for g in (sl.get("generation_groups") or []) if isinstance(g, dict)}
    mism = []
    for r in plan.get("recipes", []):
        if r.get("kind") == "transition" and r.get("status") == "adopted":
            gid = r["scope"].get("group_id")
            t = tin.get(gid) or {}
            pr = r["params"]
            if str(t.get("type")) != str(pr.get("type")) \
                    or (str(pr.get("type")) != "hard_cut" and abs(float(t.get("duration_s") or 0) - float(pr.get("duration_s") or 0)) > 1e-3) \
                    or abs(float(t.get("hold_s") or 0) - float(pr.get("hold_s") or 0)) > 1e-3 \
                    or abs(float(t.get("freeze_s") or 0) - float(pr.get("freeze_s") or 0)) > 1e-3 \
                    or (float(pr.get("hold_s") or 0) > 0 and str(t.get("hold_audio") or "sustain") != str(pr.get("hold_audio") or "sustain")):
                mism.append(gid)
    rec("transitions_synced", not mism, "shot_list 与已采纳转场处方不一致:" + ", ".join(mism) if mism else "转场处方与 shot_list 一致")

    # 6 flashback_graded(2026-09-17):闪回块每组的当前版本应带「调色与光感」处方;只 WARN(存量项目、用户有意不调)
    fb = [b for b in pp.narrative_blocks(groups) if b["kind"] == "flashback"]
    if not fb:
        rec("flashback_graded", True, "本集无闪回块")
    else:
        grade_ids = {r["id"] for r in plan.get("recipes", []) if r.get("section") == "grade" and r.get("status") == "adopted"}
        miss = []
        for b in fb:
            for gid in b["groups"]:
                if not pp.version_file(proj, ep, gid, 0, plan):
                    continue
                cur = pp.current_version(plan, gid)
                ver = next((x for x in pp.group_versions(plan, gid) if int(x.get("v") or 0) == cur), None) if cur else None
                if not ver or not (set(ver.get("recipes") or []) & grade_ids):
                    miss.append(f"{b['block_id']}:{gid}")
        rec("flashback_graded", not miss, ("闪回组未落在已采纳的调色版本上:" + ", ".join(miss[:8]) + ("…" if len(miss) > 8 else "")) if miss
            else f"{len(fb)} 个闪回块全部组已带调色处方", warn=bool(miss))

    ok = all(c["status"] != "FAIL" for c in checks)
    print(f"[RESULT] {CHECK_NAME}: {'PASS' if ok else 'FAIL'}", flush=True)
    if write:
        pp.write_json(proj / pp.CHECK_REL.format(ep=ep), {
            "schema": "post_check/1.0", "ep": ep, "checked_at": pp._now(), "generated_by": "code/post_apply.py",
            "plan_fingerprint": pp.plan_fingerprint(plan),
            "check": {"name": CHECK_NAME, "result": "PASS" if ok else "FAIL", "items": checks}})
    return ok, checks


# ---------------------------------------------------------------- sync-timeline / build-cut / finalize
def _segment_dir(proj: Path, ep: str) -> Path:
    return pp.post_dir(proj, ep) / "_cut"


def do_sync_timeline(proj: Path, ep: str) -> int:
    """把各组当前指针写进 timeline.json:tracks.video[].src 指向归一化后的后期段文件(build-cut 产出,
    与 render_transitions 分段渲染的流拷贝口径一致),原值保留在 src_orig/in_orig/out_orig;段文件不存在时退回版本文件。
    首次改写前备份 timeline.pre_post.json。"""
    plan = pp.load_plan(proj, ep)
    tlp = proj / "edit" / ep / "timeline.json"
    tl = pp.read_json(tlp)
    if not isinstance(tl, dict):
        _log("FAIL", f"timeline.json 不存在:{tlp.relative_to(proj)}(先由 10-editing/edit 出粗剪)")
        return 2
    bak = tlp.with_name("timeline.pre_post.json")
    if not bak.exists():
        bak.write_text(tlp.read_text(encoding="utf-8"), encoding="utf-8")
        _log("NOTE", f"timeline.json 首次改写,备份为 {bak.name}")
    segdir = _segment_dir(proj, ep)
    versions = {}
    n_seg = 0
    for t in ((tl.get("tracks") or {}).get("video") or []):
        gid = t.get("group_id")
        if not gid:
            continue
        t.setdefault("src_orig", t.get("src"))
        t.setdefault("in_orig", t.get("in"))
        t.setdefault("out_orig", t.get("out"))
        v = pp.current_version(plan, gid)
        seg = segdir / f"{gid}.mp4"
        meta = pp.read_json(segdir / f"{gid}.json") or {}
        f = pp.current_file(proj, ep, gid, plan)
        if seg.is_file() and int(meta.get("v", -1)) == v and meta.get("frames"):
            t["src"] = str(seg.relative_to(proj))
            t["in"] = 0.0
            t["out"] = round(float(meta["frames"]) / float(meta.get("fps") or 24), 6)
            n_seg += 1
        elif f:
            t["src"] = str(f.relative_to(proj))
            t["in"], t["out"] = t.get("in_orig") or 0.0, t.get("out_orig") or t.get("out")
        t["post_v"] = v
        versions[gid] = {"v": v, "src": t["src"]}
    # 时长编辑表(2026-09-17):各组当前版本的 time_ops(组内秒)按**原始**组序累计起点平移到原粗剪基准;
    # 起点按 in_orig/out_orig 顺序累加(与 final_audio / subtitles 同基准),不信任 agent 写的 cum_start_s
    orig, acc = [], 0.0
    for t in ((tl.get("tracks") or {}).get("video") or []):
        if not isinstance(t, dict):
            continue
        t_in = float(t.get("in_orig") if t.get("in_orig") is not None else (t.get("in") or 0.0))
        t_out = t.get("out_orig") if t.get("out_orig") is not None else t.get("out")
        dur = max(0.0, float(t_out or 0.0) - t_in) / float(t.get("speed") or 1.0)
        if t.get("group_id"):
            orig.append({"group_id": t["group_id"], "cum_start_s": acc})
        acc += dur
    ops = pp.episode_time_ops(plan, orig)
    tm_p = proj / "edit" / ep / timemap.TIMEMAP_FILE
    pp.write_json(tm_p, {"schema": "timemap/1.0", "episode": ep, "written_at": pp._now(), "cli": "code/post_apply.py sync-timeline",
                         "basis": "原粗剪 cut_v1 / final_audio / subtitles 的正片 0 秒基准",
                         "layer": "post_versions", "ops": ops, "delta_s": timemap.total_delta(ops),
                         "plan_fingerprint": pp.plan_fingerprint(plan)})
    tl["post"] = {"schema": "post_sync/1.0", "synced_at": pp._now(), "cut": f"edit/{ep}/{pp.CUT_POST}", "versions": versions,
                  "segments": n_seg, "plan_fingerprint": pp.plan_fingerprint(plan),
                  "timemap": f"edit/{ep}/{timemap.TIMEMAP_FILE}", "timemap_delta_s": timemap.total_delta(ops)}
    pp.write_json(tlp, tl)
    n = sum(1 for v in versions.values() if v["v"] > 0)
    _log("DONE", f"timeline#post 同步 {len(versions)} 组(段文件 {n_seg}),其中 {n} 组指向后期版本;"
                 f"timemap {timemap.describe(ops)} → {tm_p.relative_to(proj)}")
    return 0


def do_build_cut(proj: Path, ep: str) -> int:
    fx.require_tools("ffmpeg", "ffprobe")
    plan = pp.load_plan(proj, ep)
    groups = load_groups(proj, ep)
    if not groups:
        _log("FAIL", "没有分镜组(timeline / shot_list 都为空)")
        return 2
    ed = proj / "edit" / ep
    ed.mkdir(parents=True, exist_ok=True)
    ref = ed / "cut_v1.mp4"
    if ref.is_file():
        info = fx.probe(ref)
    else:
        first = next((pp.current_file(proj, ep, g["group_id"], plan) for g in groups if pp.current_file(proj, ep, g["group_id"], plan)), None)
        if not first:
            _log("FAIL", "找不到任何组的视频文件")
            return 2
        info = fx.probe(first)
    w, h, fps = int(info["width"]), int(info["height"]), float(info["fps"])
    _log("PLAN", f"目标规格 {w}x{h}@{fps:g},{len(groups)} 组")
    cache = _segment_dir(proj, ep)
    cache.mkdir(parents=True, exist_ok=True)
    segs = []
    for g in groups:
        gid = g["group_id"]
        src = pp.current_file(proj, ep, gid, plan)
        if not src:
            _log("SKIP", f"{gid} 无文件(timeline skipped_groups 或未出片)")
            continue
        key = f"{pp.file_fingerprint(src)}_{g['in']:.3f}_{g['out']:.3f}_{w}x{h}_{fps:g}"
        seg = cache / f"{gid}.mp4"
        meta = cache / f"{gid}.json"
        cached = pp.read_json(meta) or {}
        if seg.is_file() and cached.get("key") == key:
            segs.append(seg)
            continue
        t_out = g["out"] if g["out"] > g["in"] else fx.probe(src)["duration"]
        n = fx.normalize_segment(src, seg, w, h, fps, g["in"], t_out)
        pp.write_json(meta, {"key": key, "frames": n, "fps": fps, "src": str(src.relative_to(proj)), "v": pp.current_version(plan, gid)})
        _log("RUN", f"{gid} 归一 {n} 帧 ← {src.relative_to(proj)}")
        segs.append(seg)
    if not segs:
        _log("FAIL", "没有可拼的段")
        return 1
    out = ed / pp.CUT_POST
    fx.concat_segments(segs, out)
    # 整集级 ffmpeg 处方(水印等)施加在拼好的正片上
    ep_recipes = [r for r in plan.get("recipes", []) if r.get("status") == "adopted" and r.get("exec") == "ffmpeg"
                  and r["scope"].get("level") == "episode" and r["kind"] in ("watermark",)]
    if ep_recipes:
        ctx = fx.Ctx(proj, out, fx.probe(out), lut_files(proj))
        graph = fx.build_graph(ep_recipes, ctx)
        tmp = ed / ("." + pp.CUT_POST + ".wm.mp4")
        fx.apply_graph(out, tmp, graph, ctx)
        out.unlink()
        tmp.rename(out)
        _log("DONE", f"整集级处方 {len(ep_recipes)} 条已施加(" + ", ".join(r["kind"] for r in ep_recipes) + ")")
    d = fx.probe(out)["duration"]
    _log("DONE", f"{out.relative_to(proj)} {d:.2f}s,{len(segs)} 段")
    return 0


def _run_cli(script: str, args: list[str]) -> int:
    cmd = [sys.executable, str(REPO_ROOT / "code" / script)] + args
    _log("RUN", " ".join(cmd[1:]))
    p = subprocess.run(cmd, cwd=str(REPO_ROOT))
    return p.returncode


def do_finalize(proj: Path, ep: str, project: str) -> int:
    rc = do_build_cut(proj, ep)
    if rc:
        return rc
    rc = do_sync_timeline(proj, ep)
    if rc:
        return rc
    ed = proj / "edit" / ep
    v2 = ed / pp.CUT_POST_V2
    if v2.exists():
        v2.unlink()
    rc = _run_cli("render_transitions.py", ["render", "--project", project, "--ep", ep, "--src", pp.CUT_POST, "--out", pp.CUT_POST_V2])
    if rc:
        _log("FAIL", f"render_transitions 退出码 {rc}")
        return 1
    cut = pp.CUT_POST_V2 if v2.is_file() else pp.CUT_POST
    final = ed / "final.mp4"
    bak = ed / "final.pre_post.mp4"
    if final.is_file() and not bak.exists():
        final.rename(bak)
        _log("NOTE", f"原 final.mp4 备份为 {bak.name}")
    rc = _run_cli("finalize_episode.py", ["assemble", "--project", project, "--ep", ep, "--cut", cut])
    if rc:
        _log("FAIL", f"finalize_episode assemble 退出码 {rc}")
        return 1
    ok, _ = do_check(proj, ep)
    _log("DONE" if ok else "FAIL", f"成片 edit/{ep}/final.mp4(正片 {cut})")
    return 0 if ok else 1


def do_cleanup(proj: Path, ep: str) -> int:
    plan = pp.load_plan(proj, ep)
    cleaned = pp.cleanup_versions(plan, proj, ep)
    pp.save_plan(proj, ep, plan)
    _log("DONE", f"清理 {len(cleaned)} 个版本文件" + (":" + ", ".join(cleaned[:10]) if cleaned else ""))
    return 0


def do_status(proj: Path, ep: str, as_json: bool) -> int:
    plan = pp.load_plan(proj, ep)
    s = pp.summary(plan)
    cur = {g: v for g, v in (plan.get("current") or {}).items() if int(v or 0) > 0}
    if as_json:
        print(json.dumps({"summary": s, "current": cur, "fingerprint": pp.plan_fingerprint(plan)}, ensure_ascii=False))
    else:
        _log("INFO", f"处方 {s['total']}:" + " ".join(f"{pp.STATUS_LABEL[k]} {s.get(k, 0)}" for k in pp.STATUSES if s.get(k)))
        _log("INFO", f"{len(cur)} 组在后期版本:" + ", ".join(f"{g}=v{v}" for g, v in list(cur.items())[:12]))
    return 0


def main(argv=None):
    def configure(ap):
        ap.add_argument("cmd", choices=("blocks", "propose", "apply", "register", "adopt", "discard", "rollback", "check", "sync-timeline",
                                        "build-cut", "finalize", "cleanup", "status"))
        ap.add_argument("--recipe", action="append", default=[], help="处方 id,可重复")
        ap.add_argument("--group", default=None, help="限定分镜组 grpNNN")
        ap.add_argument("--file", default=None, help="register:产物路径(项目内)")
        ap.add_argument("--note", default="", help="register(无 --recipe):版本备注/分镜剪辑工单号")
        ap.add_argument("--to", type=int, default=0, help="rollback:目标版本号(0=母本)")
        ap.add_argument("--preview", action="store_true", help="apply:只出 4 秒 480p 低清预览")
        ap.add_argument("--json", action="store_true", help="status / blocks:JSON 输出")
        ap.add_argument("--stats", action="store_true", help="blocks:实测每块与前后现实组的明度台阶")
        ap.add_argument("--verify", action="store_true", help="blocks:机检 grade_plan_proposed(配 --by)")
        ap.add_argument("--kind", default="", help="propose:做法 id(basic/lut/scene_palette/atmos/soften…)")
        ap.add_argument("--block", default=None, help="propose:叙事块 id(shot_list narrative_block.id)")
        ap.add_argument("--scene", default=None, help="propose:场次 SCN-id")
        ap.add_argument("--episode", action="store_true", help="propose:整集作用域")
        ap.add_argument("--t0", type=float, default=None, help="propose:组内时间段起点秒(配 --group)")
        ap.add_argument("--t1", type=float, default=None, help="propose:组内时间段终点秒(配 --group)")
        ap.add_argument("--params", default="", help="propose:参数 JSON 对象")
        ap.add_argument("--by", default="", help="propose / blocks --verify:工位 id(如 10-editing/grade-planner)")

    args, proj = parse_args(__doc__.splitlines()[0], configure=configure, argv=argv)
    ep = re.sub(r"[^\w\-]", "", args.ep)
    if not proj.is_dir():
        _log("FAIL", f"项目目录不存在:{proj}")
        return 2
    if args.cmd == "blocks":
        if args.verify and not args.by:
            _log("FAIL", "blocks --verify 需要 --by <工位id>")
            return 2
        return do_blocks(proj, ep, args.json, args.stats, args.by if args.verify else "")
    if args.cmd == "propose":
        picked = [x for x in (args.block, args.scene, args.group, args.episode or None) if x]
        if not args.kind or len(picked) != 1 or not args.by:
            _log("FAIL", "propose 需要 --kind、--by,以及 --block / --scene / --group / --episode 恰好一个")
            return 2
        if (args.t0 is None) != (args.t1 is None) or (args.t0 is not None and not args.group):
            _log("FAIL", "--t0/--t1 须成对并配 --group")
            return 2
        scope = ({"level": "block", "block_id": args.block} if args.block else {"level": "scene", "scene_id": args.scene} if args.scene
                 else {"level": "episode"} if args.episode
                 else {"level": "range", "group_id": args.group, "t0": args.t0, "t1": args.t1} if args.t0 is not None
                 else {"level": "group", "group_id": args.group})
        return do_propose(proj, ep, args.kind, scope, args.params, args.note, args.by)
    if args.cmd == "apply":
        if not args.recipe:
            _log("FAIL", "apply 需要 --recipe")
            return 2
        return do_apply(proj, ep, args.recipe, args.group, args.preview)
    if args.cmd == "register":
        if not args.file or not (args.recipe or args.group):
            _log("FAIL", "register 需要 --file,以及 --recipe(处方产物)或 --group(分镜剪辑工单产物)")
            return 2
        return do_register(proj, ep, args.recipe[0] if args.recipe else None, args.file, args.group, args.note)
    if args.cmd in ("adopt", "discard"):
        if not args.recipe:
            _log("FAIL", f"{args.cmd} 需要 --recipe")
            return 2
        return do_adopt(proj, ep, args.recipe[0], discard=args.cmd == "discard")
    if args.cmd == "rollback":
        if not args.group:
            _log("FAIL", "rollback 需要 --group")
            return 2
        return do_rollback(proj, ep, args.group, args.to)
    if args.cmd == "check":
        ok, _ = do_check(proj, ep)
        return 0 if ok else 1
    if args.cmd == "sync-timeline":
        return do_sync_timeline(proj, ep)
    if args.cmd == "build-cut":
        return do_build_cut(proj, ep)
    if args.cmd == "finalize":
        return do_finalize(proj, ep, args.project)
    if args.cmd == "cleanup":
        return do_cleanup(proj, ep)
    return do_status(proj, ep, args.json)


if __name__ == "__main__":
    sys.exit(main())
