# -*- coding: utf-8 -*-
"""timemap_layers.py — 正片相对「原粗剪 / final_audio / subtitles 基准」的时长编辑层(2026-09-25 自 finalize_episode.py 抽出)。

finalize_episode.py(终版封装)与 render_captions.py(花字版:speech-align 逐字轨、花字/SFX 落位)都要回答同一个问题:
当前正片 cut 上的时刻与外挂声轨 / 字幕 / 原粗剪时刻怎么换算。答案由两层表与混音基准清单共同决定
(WORKFLOW.md §9B / §8B ④ / §9C):
  层 1 post_versions   edit/epNN/timemap.json(后期采纳版本的组内删段 / 插黑 / 变速,原粗剪基准),仅正片为后期拼片时生效;
  层 2 boundary_pads   edit/epNN/transitions_render.json#timemap(组边界定格 / 黑场 / 插入段,其 src_cut 基准),仅正片为该台账 out_cut 时生效;
  混音基准             assets/audio/final/epNN.mix.json 盖章与当前采纳指针 / 边界层一致时,对应层对声轨不再套用(字幕仍按全表平移)。
本模块只做决定与换算,不碰文件;两个 CLI 调同一份代码,口径不会分叉。

  load_timemap(proj, ep, cut, notes, audio_used)  → (ops, info):ops = 字幕(原基准)→ cut 的总表;info["audio_ops"] = 外挂声轨 → cut 的表
  resolve_cut(proj, ep)                            → 干净版成片所封装的正片文件(final_layout.json 记录的 cut 优先,其次 cut_post_v2 / cut_post / 最新 cut_v*)
  cut_offset_s(proj, ep)                           → 片头偏移(final_layout.json#cut_offset_s;无台账 = 0)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

try:
    import mix_manifest
    import timemap
except ImportError:  # 服务端以 modules.* 包路径导入时
    from modules import mix_manifest, timemap


def resolve_cut(proj: Path, ep: str) -> Path | None:
    """干净版成片所用的正片:final_layout.json 的 cut 段(finalize 写)优先;否则按 post_apply.finalize 的选择顺序。"""
    proj = Path(proj)
    ed = proj / "edit" / ep
    lay = ed / "final_layout.json"
    if lay.is_file():
        try:
            d = json.loads(lay.read_text(encoding="utf-8"))
            for s in d.get("segments") or []:
                if s.get("name") == "cut" and s.get("file") and (proj / s["file"]).is_file():
                    return proj / s["file"]
            c = (d.get("timemap") or {}).get("cut")
            if c and (proj / c).is_file():
                return proj / c
        except Exception:  # noqa: BLE001
            pass
    for name in ("cut_post_v2.mp4", "cut_post.mp4"):
        if (ed / name).is_file():
            return ed / name
    cands = sorted(ed.glob("cut_v*.mp4"), key=lambda p: int(re.search(r"cut_v(\d+)", p.name).group(1)))
    return cands[-1] if cands else None


def cut_offset_s(proj: Path, ep: str) -> float:
    lay = Path(proj) / "edit" / ep / "final_layout.json"
    if not lay.is_file():
        return 0.0
    try:
        return float(json.loads(lay.read_text(encoding="utf-8")).get("cut_offset_s") or 0.0)
    except Exception:  # noqa: BLE001
        return 0.0


def load_timemap(proj, ep, cut, notes=None, audio_used=True):
    """正片 cut 相对「原粗剪 / final_audio / subtitles 基准」的时长编辑总表(ops)。
    层 1 post_versions(edit/epNN/timemap.json,基准 = 原粗剪):仅当 cut 是后期拼片(cut_post*)或转场产物的源是后期拼片时生效;
    层 2 boundary_pads(transitions_render.json#timemap,基准 = 其 src_cut):仅当 cut 就是该台账的 out_cut 时生效。
    混音基准(2026-09-23,§8B ④):p8-mix 已按采纳版本混音并盖章(assets/audio/final/epNN.mix.json)且与当前台账一致时,
    final_audio / subtitles 本身就是后期基准,层 1 不再套用;清单过期且混音带后期时轴 = FAIL(须重跑 p8-mix);
    无清单或混音按纯 v0 盖章 = 旧口径(层 1 照旧重映射)。
    返回 (ops, info)。"""
    notes = notes if notes is not None else []
    ed = proj / "edit" / ep
    cut_rel = str(Path(cut).resolve().relative_to(proj.resolve())) if Path(cut).resolve().is_relative_to(proj.resolve()) else Path(cut).name
    info = {"cut": cut_rel, "layers": []}
    post_ops, pad_ops, pad_src = [], [], None
    tr = ed / "transitions_render.json"
    if tr.is_file():
        try:
            d = json.loads(tr.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            d = {}
        tm = d.get("timemap") or {}
        if d.get("out_cut") and Path(d["out_cut"]).name == Path(cut).name and tm.get("ops"):
            pad_ops = timemap.normalize_ops(tm["ops"])
            pad_src = d.get("src_cut")
            info["layers"].append({"layer": "boundary_pads", "file": str(tr.relative_to(proj)), "basis": tm.get("basis"),
                                   "ops": len(pad_ops), "delta_s": timemap.total_delta(pad_ops)})
    tmp = ed / timemap.TIMEMAP_FILE
    post_basis_cut = Path(cut).name.startswith("cut_post") or (pad_src and Path(pad_src).name.startswith("cut_post"))
    if tmp.is_file() and post_basis_cut:
        post_ops = timemap.load_layer(tmp)
        if post_ops:
            info["layers"].append({"layer": "post_versions", "file": str(tmp.relative_to(proj)),
                                   "ops": len(post_ops), "delta_s": timemap.total_delta(post_ops)})
    post_ops, mres = _apply_mix_basis(proj, ep, cut, post_ops, post_basis_cut, info, notes, audio_used)
    ops = timemap.compose(post_ops, pad_ops) if post_ops else pad_ops
    info["ops"] = ops
    info["delta_s"] = timemap.total_delta(ops)
    # 组边界层与混音(2026-09-24 过场设计):混音盖章的边界层 = 本 cut 的边界层(指纹同口径:from/to/占时 ms)→ 声轨已铺在含过场的
    # 时间线上,不再套边界层重映射(否则字卡/定场处被填静音、BGM 断);字幕仍按全表平移。混音含边界层而正片没经 render_transitions
    # (pad_ops 空)= 音画长度不等,FAIL。
    audio_ops = ops
    mix_b_fp, mix_b_delta = mres.get("mix_boundary_fp"), float(mres.get("mix_boundary_delta_s") or 0.0)
    if pad_ops and mix_b_fp and mres.get("status") in (mix_manifest.STATUS_CURRENT, mix_manifest.STATUS_VERSIONS_CHANGED):
        cut_b_fp = mix_manifest.boundary_fingerprint(pad_ops)
        if cut_b_fp == mix_b_fp:
            audio_ops = post_ops if post_ops else []
            info["layers"] = [l for l in info["layers"] if l.get("layer") != "boundary_pads"]
            info["layers"].append({"layer": "boundary_pads", "file": str(tr.relative_to(proj)), "ops": len(pad_ops),
                                   "delta_s": timemap.total_delta(pad_ops), "audio_skipped": True, "reason": "mix_boundary_current"})
            notes.append(f"混音已含组边界层(盖章 {mres.get('stamped_at')},{len(pad_ops)} 处 +{timemap.total_delta(pad_ops):.3f}s),"
                         "外挂声轨不再套边界层重映射(BGM 跨过场连续);字幕仍按表平移")
        else:
            msg = (f"混音盖章的组边界层(Δ{mix_b_delta:+.3f}s)与正片 {Path(cut).name} 的边界层(Δ{timemap.total_delta(pad_ops):+.3f}s)不一致:"
                   "请重跑 p8-mix(或重跑 render_transitions 使两边一致)")
            if audio_used:
                raise SystemExit("[FAIL] " + msg)
            notes.append("⚠ " + msg)
    elif not pad_ops and mix_b_delta > 0 and audio_used and mres.get("status") in (mix_manifest.STATUS_CURRENT, mix_manifest.STATUS_VERSIONS_CHANGED):
        raise SystemExit(f"[FAIL] 混音已按含组边界层(+{mix_b_delta:.3f}s)的时间线铺轨,而正片 {Path(cut).name} 没有经 render_transitions 插入过场"
                         "(不是转场台账的 out_cut):请先跑 render_transitions.py render 再封装其产物")
    elif pad_ops and audio_used and mres.get("cur_boundary_has_inserts") and not mix_b_fp:
        notes.append("⚠ 混音清单不含组边界层而正片有字卡/定场插入:声轨按 timemap 切开、BGM 会在过场处断——请重跑 p8-mix(sources 已给 boundaries)")
    info["audio_ops"] = audio_ops
    info["audio_delta_s"] = timemap.total_delta(audio_ops)
    if ops:
        notes.append(f"正片带时长编辑表 timemap:{timemap.describe(ops)}(" + " + ".join(l["layer"] for l in info["layers"])
                     + "),字幕按表平移" + (";外挂声轨按表重映射" if audio_ops else ";外挂声轨不重映射"))
    return ops, info


def _apply_mix_basis(proj, ep, cut, post_ops, post_basis_cut, info, notes, audio_used=True):
    """按混音基准清单决定层 1(post_versions)是否套用;返回生效的 post_ops(可能被清空)。"""
    res = mix_manifest.compare(proj, ep)
    info["mix_basis"] = {k: res.get(k) for k in ("status", "stamped_at", "task_id", "mix_ops_fp", "cur_ops_fp",
                                                   "mix_delta_s", "cur_delta_s", "changed_groups",
                                                   "boundary_status", "mix_boundary_fp", "cur_boundary_fp", "mix_boundary_delta_s", "cur_boundary_delta_s")}
    st = res.get("status")
    if st == mix_manifest.STATUS_NONE:
        return post_ops, res
    if st in (mix_manifest.STATUS_CURRENT, mix_manifest.STATUS_VERSIONS_CHANGED):
        if st == mix_manifest.STATUS_VERSIONS_CHANGED:
            notes.append("⚠ 混音后采纳版本号有变但时轴未变(声音内容可能与画面版本不同):" + "; ".join(res.get("changed_groups") or [])[:300])
        if res.get("cur_has_ops"):
            if not post_basis_cut:
                msg = (f"混音按后期采纳版本基准(Δ{res.get('cur_delta_s', 0):+.3f}s,盖章 {res.get('stamped_at')})而正片 {Path(cut).name} "
                       f"是原粗剪基准;请用 code/post_apply.py finalize 出后期拼片再封装,或重跑 p8-mix")
                if audio_used:
                    raise SystemExit("[FAIL] " + msg)
                notes.append("⚠ " + msg)
                return post_ops, res
            if post_ops:
                info["layers"] = [l for l in info["layers"] if l.get("layer") != "post_versions"]
                info["layers"].append({"layer": "post_versions", "skipped": True, "reason": "mix_basis_current",
                                       "ops": len(post_ops), "delta_s": timemap.total_delta(post_ops)})
                notes.append(f"混音已按采纳版本基准(盖章 {res.get('stamped_at')} / {res.get('task_id')},"
                             f"{timemap.describe(post_ops)}),final_audio / 字幕不再套 post_versions 层重映射")
            return [], res
        return post_ops, res
    # stale
    if res.get("mix_has_ops") or res.get("boundary_status") == mix_manifest.BND_STALE:
        msg = "混音基准过期:" + res.get("detail", "") + ";请重跑 p8-mix(或在后期页回滚到混音时的采纳版本)"
        if audio_used:
            raise SystemExit("[FAIL] " + msg)
        notes.append("⚠ " + msg)
        return post_ops, res
    notes.append("⚠ 混音按 v0 母本基准盖章而采纳版本已变:" + "; ".join(res.get("changed_groups") or [])[:300]
                 + ";按 timemap 重映射兜底(旧口径:BGM 剪点无交叉淡化、跨剪点旁白会被切断,建议重跑 p8-mix)")
    return post_ops, res
