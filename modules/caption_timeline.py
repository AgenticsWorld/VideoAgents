# -*- coding: utf-8 -*-
"""caption_timeline.py — 花字时间轴(2026-09-25,WORKFLOW.md §9A;前科 fengshen3 ep06 DEF-ep06-caption-0001)。

背景:花字设计(captions.json)以组为单位写组内时间 local_start/local_end(**母本 v0 clip 坐标**,与 shot_list 同基准),
烧录与花字版成片却要落在**干净版成片实际的时间线**上。以前 render_captions.py 只认 v0 母本与原粗剪时间轴:
后期页采纳的版本(删段 / 慢动作 / 调色)被丢掉,组边界的过场字卡 / 定格 / 黑场与片头偏移都没算进去,花字与音效整体错位。

本模块把「一条花字最终出现在成片第几秒、烧在哪个文件上」收敛成一处确定性换算,render / check / speech-* 全部走它:

  组源(burn source)   各组**当前采纳版本**文件(post_plan 指针;未采纳 = 母本),与 build-cut / 混音取源同口径
  组内映射 local_ops    该版本相对母本的组内时长编辑表(post_plan.effective_time_ops),local(v0) → 版本坐标;
                        落在被删段内的花字整条丢弃(WARN),被删掉一部分的裁到剩余部分
  组起点 start_src_s    干净版成片所封装的正片(timemap_layers.resolve_cut)的**源 cut 基准**上的组起点:
                        edit/epNN/timeline.json 视频轨 timeline_in(sync-timeline 后 = 后期拼片基准;未同步 = 原粗剪基准);
                        av 项目按 shot_list audio_in_s(母带权威);都没有时按当前版本实测时长累计
  组边界层 pad_ops      transitions_render.json#timemap(仅当正片就是该台账的 out_cut):定格 / 黑场 / 字卡插入,组起点排在插入块之后、
                        组尾排在插入块之前(花字不得盖到字卡上)
  片头偏移 cut_offset_s final_layout.json(finalize 写);花字版成片 = 干净版 final.mp4 时间轴,所以成片时刻 = cut 时刻 + 片头
  外挂声轨 / 字幕层     timemap_layers.load_timemap 同 finalize 口径:ops = 字幕基准 → cut,audio_ops = 混音 wav 基准 → cut
                        (speech-align 用:ASR 逐字在 wav 基准,台本在字幕基准,产出统一折到 cut 基准)

**captions.json 的集级 start/end 定义为 cut 基准**(正片 = 后期版本 + 组边界层,不含片头):与 word_track、speech-lookup、
组窗口同一把尺;设计侧不需要知道后期 / 过场存在,只要 speech-snap 或 `start = local_to_cut(group, local_start)`。
机检 caption_time_consistent(主流程)按此核对。

用法(宿主 CLI 内):
    tl = CaptionTimeline.resolve(proj, ep)          # 缺 cut / 台账时降级为原粗剪口径,不抛;混音基准过期等硬错误抛 SystemExit
    tl.groups["grp010"].src                        # 烧录取源
    tl.local_to_cut("grp010", 1.2)                 # → cut 基准秒;None = 被删段
    tl.final_time("grp010", 1.2)                   # → 成片秒(+片头)
    tl.burn_captions("grp010", caps)               # → (烧录用副本列表:local 已换到版本坐标, 丢弃的 id 列表)
    tl.fingerprint()                               # 时间轴指纹:进回执 / word_track,变了就过期
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

try:
    import mix_manifest
    import post_plan as pp
    import timemap
    import timemap_layers
except ImportError:  # 服务端以 modules.* 包路径导入时
    from modules import mix_manifest, timemap, timemap_layers
    from modules import post_plan as pp

MIN_BURN_S = 0.2      # 渲染器下限:映射后短于此的花字不烧
EPS = 1e-6


def _probe_duration(path: Path) -> float:
    return mix_manifest.probe_duration(path)


def _in_deleted(ops: list[dict], t: float) -> bool:
    return any(o["out_len"] <= EPS and o["src_t0"] + EPS < t < o["src_t1"] - EPS for o in ops)


def _map_before_inserts(ops: list[dict], t: float) -> float:
    """map_time 会把恰在插入点上的时刻排到插入块之后;组尾 / 花字出点要排在插入块**之前**。"""
    out = timemap.map_time(ops, t)
    for o in ops:
        if o["src_t1"] - o["src_t0"] <= EPS and abs(o["src_t0"] - t) <= 1e-3:
            out -= o["out_len"]
    return round(out, 6)


class GroupRow:
    __slots__ = ("gid", "src", "src_rel", "v", "local_ops", "in_s", "start_src_s", "duration_s", "v0_duration_s", "authority")

    def __init__(self, gid: str, src: Path | None, src_rel: str | None, v: int, local_ops: list[dict], in_s: float,
                 start_src_s: float, duration_s: float, v0_duration_s: float, authority: str):
        self.gid, self.src, self.src_rel, self.v = gid, src, src_rel, int(v)
        self.local_ops, self.in_s = local_ops, float(in_s)
        self.start_src_s, self.duration_s, self.v0_duration_s = float(start_src_s), float(duration_s), float(v0_duration_s)
        self.authority = authority

    def as_dict(self) -> dict:
        return {"group_id": self.gid, "src": self.src_rel, "v": self.v, "local_ops": self.local_ops, "in_s": self.in_s,
                "start_src_s": self.start_src_s, "duration_s": self.duration_s, "v0_duration_s": self.v0_duration_s,
                "authority": self.authority}


class CaptionTimeline:
    def __init__(self, proj: Path, ep: str):
        self.proj, self.ep = Path(proj), ep
        self.cut: Path | None = None
        self.final: Path | None = None
        self.cut_offset_s = 0.0
        self.pad_ops: list[dict] = []
        self.ops: list[dict] = []          # 字幕基准 → cut
        self.audio_ops: list[dict] = []    # 外挂声轨(混音 wav)基准 → cut
        self.layers_info: dict = {}
        self.groups: dict[str, GroupRow] = {}
        self.order: list[str] = []
        self.notes: list[str] = []
        self.warnings: list[str] = []
        self.timeline_stale: list[str] = []   # timeline 记录的后期版本 ≠ 当前采纳指针的组

    # ------------------------------------------------------------ 构建
    @classmethod
    def resolve(cls, proj: Path, ep: str, audio_used: bool = True) -> "CaptionTimeline":
        tl = cls(proj, ep)
        proj = tl.proj
        ed = proj / "edit" / ep
        tl.cut = timemap_layers.resolve_cut(proj, ep)
        fin = ed / "final.mp4"
        tl.final = fin if fin.is_file() else None
        tl.cut_offset_s = timemap_layers.cut_offset_s(proj, ep)
        if tl.cut is not None:
            ops, info = timemap_layers.load_timemap(proj, ep, tl.cut, tl.notes, audio_used=audio_used)
            tl.ops = timemap.normalize_ops(ops)
            tl.audio_ops = timemap.normalize_ops(info.get("audio_ops") or [])
            tl.layers_info = {k: info.get(k) for k in ("cut", "layers", "mix_basis", "delta_s", "audio_delta_s")}
            # 组边界层:只在正片就是转场台账 out_cut 时生效(与 load_timemap 判定同源)
            tr = ed / "transitions_render.json"
            if tr.is_file():
                try:
                    d = json.loads(tr.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    d = {}
                tm = d.get("timemap") or {}
                if d.get("out_cut") and Path(d["out_cut"]).name == tl.cut.name and tm.get("ops"):
                    tl.pad_ops = timemap.normalize_ops(tm["ops"])
        tl._build_groups()
        return tl

    def _build_groups(self) -> None:
        proj, ep = self.proj, self.ep
        plan = pp.load_plan(proj, ep)
        sl = pp.read_json(proj / "directing" / ep / "shot_list.json") or {}
        sl_groups = {g.get("group_id"): g for g in (sl.get("generation_groups") or []) if isinstance(g, dict) and g.get("group_id")}
        tl_json = pp.read_json(proj / "edit" / ep / "timeline.json") or {}
        entries = ((tl_json.get("tracks") or {}).get("video")) or tl_json.get("video") or []
        entries = [e for e in entries if isinstance(e, dict) and e.get("group_id")]
        is_av = (proj / "av" / "audio_map.json").is_file() or any(g.get("audio_in_s") is not None for g in sl_groups.values())
        basis = mix_manifest.current_basis(proj, ep, plan)
        by_basis = {r["group_id"]: r for r in basis}
        synced = ((tl_json.get("post") or {}).get("versions") or {}) if isinstance(tl_json.get("post"), dict) else {}
        order = [e["group_id"] for e in entries] if entries else [r["group_id"] for r in basis]
        seen = set()
        order = [g for g in order if not (g in seen or seen.add(g))]
        for gid in by_basis:
            if gid not in seen:
                order.append(gid)
                seen.add(gid)
        cum = 0.0
        ent_by = {e["group_id"]: e for e in entries}
        for gid in order:
            r = by_basis.get(gid) or {"group_id": gid, "v": 0, "src": None, "time_ops": []}
            src = proj / r["src"] if r.get("src") else None
            local_ops = timemap.normalize_ops(r.get("time_ops") or [])
            e = ent_by.get(gid)
            in_s = float((e or {}).get("in") or 0.0)
            dur = None
            authority = "timeline"
            start = None
            if e is not None:
                for k in ("timeline_in_s", "timeline_in", "cum_start_s"):
                    if isinstance(e.get(k), (int, float)):
                        start = float(e[k])
                        break
                for a, b in (("timeline_out_s", "timeline_in_s"), ("timeline_out", "timeline_in"), ("cum_end_s", "cum_start_s")):
                    if isinstance(e.get(a), (int, float)) and isinstance(e.get(b), (int, float)):
                        dur = float(e[a]) - float(e[b])
                        break
                if dur is None and isinstance(e.get("duration_s"), (int, float)):
                    dur = float(e["duration_s"])
                if dur is None and isinstance(e.get("out"), (int, float)):
                    dur = (float(e["out"]) - in_s) / float(e.get("speed") or 1.0)
                pv = (synced.get(gid) or {}).get("v") if isinstance(synced.get(gid), dict) else None
                if synced and pv is not None and int(pv) != int(r.get("v") or 0):
                    self.timeline_stale.append(f"{gid}(timeline v{int(pv)} ≠ 采纳 v{int(r.get('v') or 0)})")
            g = sl_groups.get(gid) or {}
            if is_av and g.get("audio_in_s") is not None:
                start, authority = float(g["audio_in_s"]), "audio_in_s"
                if dur is None:
                    dur = float(g.get("av_span_s") or g.get("total_duration_s") or 0) or None
            if dur is None:
                dur = _probe_duration(src) if src and src.is_file() else float(g.get("total_duration_s") or 0.0)
                authority = "probe" if authority == "timeline" else authority
            if start is None:
                start, authority = cum, ("cumulative" if authority == "timeline" else authority)
            v0 = proj / "assets" / "clips" / ep / f"{gid}.mp4"
            if int(r.get("v") or 0) > 0 and v0.is_file():
                v0_dur = _probe_duration(v0)
            else:
                v0_dur = float(g.get("total_duration_s") or 0.0) or (dur - timemap.total_delta(local_ops) if dur else 0.0)
            self.groups[gid] = GroupRow(gid, src if src and src.is_file() else None, r.get("src"), int(r.get("v") or 0), local_ops,
                                        in_s, start, dur or 0.0, v0_dur, authority)
            cum = start + (dur or 0.0)
        self.order = order
        if self.timeline_stale:
            self.warnings.append("timeline.json 记录的后期版本与当前采纳指针不一致(先 post_apply.py finalize 重出干净版):"
                                 + ", ".join(self.timeline_stale[:6]))

    # ------------------------------------------------------------ 换算
    def _map_cut(self, t_src: float, end: bool = False) -> float:
        if not self.pad_ops:
            return round(float(t_src), 6)
        return _map_before_inserts(self.pad_ops, t_src) if end else timemap.map_time(self.pad_ops, t_src)

    def local_to_version(self, gid: str, t_local: float) -> float | None:
        """组内 v0 时刻 → 当前版本坐标;落在被删段内返回 None。"""
        g = self.groups.get(gid)
        if g is None:
            return None
        if _in_deleted(g.local_ops, float(t_local)):
            return None
        return round(timemap.map_time(g.local_ops, float(t_local)), 6)

    def version_to_cut(self, gid: str, t_ver: float, end: bool = False) -> float | None:
        g = self.groups.get(gid)
        if g is None:
            return None
        rel = float(t_ver) - g.in_s
        if rel < -1e-3 or (g.duration_s and rel > g.duration_s + 1e-3):
            return None                       # 被 timeline in/out 修剪掉
        return self._map_cut(g.start_src_s + max(0.0, rel), end=end)

    def local_to_cut(self, gid: str, t_local: float, end: bool = False) -> float | None:
        """组内 v0 时刻 → cut 基准秒(后期版本 + 组边界层,不含片头);被删段 / 被修剪返回 None。"""
        tv = self.local_to_version(gid, t_local)
        return None if tv is None else self.version_to_cut(gid, tv, end=end)

    def final_time(self, gid: str, t_local: float, end: bool = False) -> float | None:
        t = self.local_to_cut(gid, t_local, end=end)
        return None if t is None else round(t + self.cut_offset_s, 6)

    def cut_to_local(self, gid: str, t_cut: float) -> float | None:
        """cut 基准秒 → 组内 v0 时刻(speech-snap 回写 local 用)。"""
        g = self.groups.get(gid)
        if g is None:
            return None
        t_src = timemap.inverse_time(self.pad_ops, float(t_cut)) if self.pad_ops else float(t_cut)
        t_ver = t_src - g.start_src_s + g.in_s
        return round(timemap.inverse_time(g.local_ops, t_ver) if g.local_ops else t_ver, 3)

    def group_window(self, gid: str) -> tuple[float, float] | None:
        g = self.groups.get(gid)
        if g is None:
            return None
        return (self._map_cut(g.start_src_s), self._map_cut(g.start_src_s + g.duration_s, end=True))

    def group_at(self, t_cut: float) -> str | None:
        for gid in self.order:
            w = self.group_window(gid)
            if w and w[0] <= t_cut < w[1]:
                return gid
        return None

    def burn_captions(self, gid: str, caps: list[dict]) -> tuple[list[dict], list[str]]:
        """烧录用副本:local_start/local_end 换到当前版本坐标(被删段裁掉,整条落在删段内 / 短于 0.2s 的丢弃)。"""
        g = self.groups.get(gid)
        kept, dropped = [], []
        for c in caps:
            tag = str(c.get("id") or c.get("text"))
            try:
                ls, le = float(c.get("local_start")), float(c.get("local_end"))
            except (TypeError, ValueError):
                kept.append(dict(c))
                continue
            ops = g.local_ops if g else []
            if not ops:
                kept.append(dict(c))
                continue
            a = timemap.map_time(ops, ls)
            b = _map_before_inserts(ops, le)
            if b - a < MIN_BURN_S:
                dropped.append(tag)
                continue
            row = dict(c)
            row["local_start"], row["local_end"] = round(a, 3), round(b, 3)
            row["_v0_local"] = [ls, le]
            kept.append(row)
        return kept, dropped

    # ------------------------------------------------------------ 成片 / 声轨权威
    def audio_authority(self) -> tuple[Path | None, str]:
        """花字版 a:1 存档轨 / a:0 预混底轨的来源:
        av 项目且干净版成片就是母带时间线(无片头 / 无时长编辑)= 母带文件(零重编码机检口径);
        否则 = 干净版 final.mp4 自带声轨(finalize 已按 timemap / 片头拼好,与画面同一时间线)。"""
        proj, ep = self.proj, self.ep
        master = None
        for ext in ("mp3", "m4a", "wav"):
            cand = proj / "assets" / "audio" / "master" / f"{ep}.{ext}"
            if cand.is_file():
                master = cand
                break
        trivial = abs(self.cut_offset_s) < 1e-3 and not self.ops and not self.pad_ops
        if master is not None and (trivial or self.final is None):
            return master, "master"
        if self.final is not None:
            return self.final, "final"
        if master is not None:
            return master, "master"
        wav = proj / "assets" / "audio" / "final" / f"{ep}.wav"
        return (wav, "final_audio") if wav.is_file() and trivial else (None, "none")

    def fingerprint(self) -> str:
        slim = {"cut": self.cut.name if self.cut else None, "offset": round(self.cut_offset_s, 3),
                "pads": self.pad_ops, "ops": self.ops, "audio_ops": self.audio_ops,
                "groups": [[g.gid, g.src_rel, g.v, g.local_ops, round(g.in_s, 3), round(g.start_src_s, 3), round(g.duration_s, 3)]
                           for g in (self.groups[k] for k in self.order)]}
        return hashlib.sha256(json.dumps(slim, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]

    def describe(self) -> str:
        n_post = sum(1 for g in self.groups.values() if g.v > 0)
        bits = [f"正片 {self.cut.name if self.cut else '(无)'}", f"{len(self.order)} 组({n_post} 组在后期版本)"]
        if self.pad_ops:
            bits.append(f"组边界层 {len(self.pad_ops)} 处 {timemap.total_delta(self.pad_ops):+.3f}s")
        if abs(self.cut_offset_s) > 1e-3:
            bits.append(f"片头 +{self.cut_offset_s:.3f}s")
        if self.audio_ops:
            bits.append(f"声轨→cut {timemap.describe(self.audio_ops)}")
        if self.ops:
            bits.append(f"字幕→cut {timemap.describe(self.ops)}")
        return ",".join(bits)

    def as_dict(self) -> dict:
        return {"cut": str(self.cut.relative_to(self.proj)) if self.cut else None,
                "final": str(self.final.relative_to(self.proj)) if self.final else None,
                "cut_offset_s": self.cut_offset_s, "pad_ops": self.pad_ops, "ops": self.ops, "audio_ops": self.audio_ops,
                "fingerprint": self.fingerprint(), "groups": [self.groups[g].as_dict() for g in self.order],
                "warnings": self.warnings, "notes": self.notes}


# ---------------------------------------------------------------- 花字版成片:全集条目落到成片时间轴

def plan_final_items(tl: CaptionTimeline, data: dict) -> tuple[list[dict], list[str]]:
    """captions.json → 成片时间轴条目 [{cap, t0, t1, group_id}](t 为干净版 final.mp4 秒),与被丢弃的 id(被删段 / 被修剪 / 过短)。
    只按 group_id + local_start/local_end 推导,**不信任**条目自带的集级 start/end(那是设计侧的对账字段)。"""
    items, dropped = [], []
    for c in data.get("captions", []):
        gid = c.get("group_id")
        tag = str(c.get("id") or c.get("text"))
        try:
            ls, le = float(c.get("local_start")), float(c.get("local_end"))
        except (TypeError, ValueError):
            dropped.append(f"{tag}(local 时间非法)")
            continue
        t0 = tl.final_time(gid, ls)
        t1 = tl.final_time(gid, le, end=True)
        if t0 is None and t1 is not None and gid in tl.groups:
            # 起点落在被删段而终点还在:从删段之后开始(map_time 把删段内时刻折到删段输出起点)
            tc = tl.version_to_cut(gid, timemap.map_time(tl.groups[gid].local_ops, ls))
            t0 = None if tc is None else round(tc + tl.cut_offset_s, 6)
        if t0 is None or t1 is None:
            dropped.append(f"{tag}(落在被删段 / 修剪之外)")
            continue
        if t1 - t0 < MIN_BURN_S:
            dropped.append(f"{tag}(映射后 {t1 - t0:.2f}s 过短)")
            continue
        items.append({"cap": c, "t0": round(t0, 3), "t1": round(t1, 3), "group_id": gid})
    return items, dropped


def sfx_uses(items: list[dict]) -> list[dict]:
    """成片时间轴上的音效落点:[{sfx_id, at_s, gain_db?, pitch?, caption}](与 captions.build_sfx_track 同口径:start + offset_s)。"""
    out = []
    for it in items:
        c = it["cap"]
        sfx = c.get("sfx")
        if not sfx:
            continue
        for one in (sfx if isinstance(sfx, list) else [sfx]):
            if not isinstance(one, dict) or not one.get("sfx_id"):
                continue
            out.append({"sfx_id": one["sfx_id"], "at_s": round(float(it["t0"]) + float(one.get("offset_s") or 0.0), 3),
                        "gain_db": one.get("gain_db"), "pitch": one.get("pitch"), "caption": c.get("id")})
    return out


def sfx_plan_fingerprint(items: list[dict]) -> str | None:
    uses = sfx_uses(items)
    if not uses:
        return None
    return hashlib.sha256(json.dumps(uses, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def final_receipt_path(proj: Path, ep: str) -> Path:
    return Path(proj) / "edit" / ep / "final_caption.json"
