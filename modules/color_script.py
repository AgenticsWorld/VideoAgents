# -*- coding: utf-8 -*-
"""color_script.py — 色彩脚本(bible/color_script.json)的宿主读取与机检(2026-09-17)。

背景:06-art/color-script 的输出契约长期只锁到「每集每幕有 palette/mood/rationale」,字段名没锁死,
各项目产出了十来种结构——集键 ep(1 或 "ep01")/ episode / episode_id,段落键 acts / segments / beats,
场景挂点 scenes / scene / scene_ref(s) / scn_id / bible_scenes / scene_ids 或干脆没有,
色板 palette(色值表)/ dominant_hex+accent_hex / {名字: 色值}。宿主(后期页场次色块、scene_palette
处方「留空自动读脚本」)只认其中一种(episodes[].episode + segments[].scenes + key_palette),
其余项目一律读空。

本模块是宿主读色彩脚本的唯一入口:
  episode_entry(cs, ep)      取某集条目(集键各写法归一到 "epNN")
  segments(entry)            段落归一:[{id, scenes[], scene_ids[], events[], palette[], mood, variant, note}]
  scene_palettes(cs, ep)     {场次号 | SCN-id → 色板, "_episode": 整集色板}(后期页 / post_apply 用)
  variants(cs)               调色变体(闪回 / 梦境…):{kind: {note, palette[], grade{}}}(grade-planner 用)
  validate(cs, plan_eps)     机检 color_script_ok(新契约严格、旧写法 WARN)

新契约(SOUL.md 已锁死,validate 据此):
  episodes[] = {episode_id:"epNN", key_palette[], acts[]}
  acts[]     = {act, event_refs[], scene_ids[], palette[], mood, saturation, brightness, rationale, [variant, variant_note, variant_grade{}]}
  variants   = {<kind>: {note, palette[], grade:{contrast, temperature_shift_k, saturation, soften, grain, luma_step_pct}}}
  peaks[]    = {episode_id, act, type}
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REL = "bible/color_script.json"
HEX = re.compile(r"#[0-9a-fA-F]{6}\b")
VARIANT_KINDS = ("flashback", "dream", "montage", "imagination")     # 与 shot_list narrative_block.kind 同一枚举
GRADE_KEYS = ("contrast", "temperature_shift_k", "saturation", "soften", "grain", "luma_step_pct")
_SEG_KEYS = ("acts", "segments", "beats")
_SCENE_NO_KEYS = ("scenes", "scene", "screenplay_scene", "story_scene", "scene_ref", "scene_refs")
_SCENE_ID_KEYS = ("scene_ids", "bible_scenes", "scn_id", "scene_id", "scene_refs", "scene_ref", "scenes", "scene")
_EVENT_KEYS = ("event_refs", "events", "event_ids", "event_range")
_PALETTE_KEYS = ("palette", "palette_detail", "palette_roles", "sub_palettes", "dominant_hex", "accent_hex",
                 "dominant", "secondary", "support", "accent")


def load(base: Path) -> dict:
    try:
        d = json.loads((Path(base) / REL).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def norm_ep(v) -> str:
    """集键归一:1 / "1" / "ep1" / "EP01" / "ep01" → "ep01";认不出返回 ""。"""
    if isinstance(v, bool):
        return ""
    if isinstance(v, int):
        return f"ep{v:02d}"
    m = re.fullmatch(r"\s*(?:ep|EP|Ep|第)?\s*0*(\d{1,3})\s*(?:集)?\s*", str(v or ""))
    return f"ep{int(m.group(1)):02d}" if m else ""


def entry_ep(e: dict) -> str:
    for k in ("episode_id", "episode", "ep", "id", "episode_no"):
        n = norm_ep(e.get(k)) if isinstance(e, dict) else ""
        if n:
            return n
    return ""


def episode_entries(cs: dict) -> list[dict]:
    eps = cs.get("episodes") if isinstance(cs, dict) else None
    if isinstance(eps, dict):                       # {"ep01": {...}} 写法
        return [dict(v, episode_id=v.get("episode_id") or k) for k, v in eps.items() if isinstance(v, dict)]
    return [e for e in (eps or []) if isinstance(e, dict)]


def episode_entry(cs: dict, ep: str) -> dict | None:
    want = norm_ep(ep)
    return next((e for e in episode_entries(cs) if want and entry_ep(e) == want), None)


def _hexes(v) -> list[str]:
    """从任意形态里抠色值:色值表 / {名字: 色值} / [{hex: …}] / 夹在说明文字里的 #RRGGBB;去重保序。"""
    out: list[str] = []

    def walk(x):
        if isinstance(x, str):
            out.extend(m.upper() for m in HEX.findall(x))
        elif isinstance(x, dict):
            for y in x.values():
                walk(y)
        elif isinstance(x, (list, tuple)):
            for y in x:
                walk(y)
    walk(v)
    seen: set[str] = set()
    return [c for c in out if not (c in seen or seen.add(c))]


def legend(cs: dict) -> dict[str, str]:
    """色名 → 色值:段落 palette 写色名(「夜航靛蓝」)而色值集中登记在顶层图例(palette_legend / palette_library…)的写法。"""
    out: dict[str, str] = {}

    def walk(x, depth=0):
        if depth > 5 or not isinstance(x, dict):
            return
        for k, v in x.items():
            if isinstance(v, str) and HEX.fullmatch(v.strip()):
                out.setdefault(str(k).strip(), v.strip().upper())
            elif isinstance(v, dict):
                walk(v, depth + 1)
    walk({k: v for k, v in (cs or {}).items() if k not in ("episodes", "peaks")})
    return out


def _resolve(v, names: dict[str, str] | None) -> list[str]:
    got = _hexes(v)
    if got or not names:
        return got
    out = [names[x] for x in _strs(v) if x in names]
    return list(dict.fromkeys(out))


def _strs(v) -> list[str]:
    if isinstance(v, str):
        return [s for s in re.split(r"[\s,、/;;]+", v.strip()) if s]
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if isinstance(x, (str, int)) and str(x).strip()]
    return []


def segments(entry: dict | None, names: dict[str, str] | None = None) -> list[dict]:
    """一集的色彩段落,各写法归一。scenes = 剧本场次号(S01…),scene_ids = 场景圣经 id(SCN-…)。"""
    if not isinstance(entry, dict):
        return []
    raw = next((entry[k] for k in _SEG_KEYS if isinstance(entry.get(k), list) and entry[k]
                and any(_hexes([s.get(p) for p in _PALETTE_KEYS]) for s in entry[k] if isinstance(s, dict))), None)
    if raw is None:
        raw = next((entry[k] for k in _SEG_KEYS if isinstance(entry.get(k), list) and entry[k]), [])
    out = []
    for i, s in enumerate(raw):
        if not isinstance(s, dict):
            continue
        palette = _resolve(s.get("palette"), names) or _hexes([s.get(p) for p in _PALETTE_KEYS[1:]])
        nos, ids = [], []
        for k in _SCENE_NO_KEYS:
            nos += [x for x in _strs(s.get(k)) if re.fullmatch(r"S\d{1,3}[a-zA-Z]?", x)]
        for k in _SCENE_ID_KEYS:
            ids += [x for x in _strs(s.get(k)) if re.fullmatch(r"SCN-[\w\-]+", x)]
        events = []
        for k in _EVENT_KEYS:
            events += [x for x in _strs(s.get(k)) if re.fullmatch(r"ev[\w\-]+", x)]
        variant = str(s.get("variant") or "").strip().lower()
        note = str(s.get("variant_note") or s.get("flashback_note") or "").strip()
        if not variant and (note or "闪回变体" in str(s.get("palette_family") or "")):
            variant = "flashback"                  # 旧写法:只有 flashback_note / palette_family「(闪回变体)」
        out.append({"id": str(s.get("id") or s.get("act_id") or s.get("segment_id") or s.get("beat_id") or s.get("act") or s.get("beat") or i + 1),
                    "scenes": list(dict.fromkeys(nos)), "scene_ids": list(dict.fromkeys(ids)), "events": list(dict.fromkeys(events)),
                    "palette": palette, "palette_direct": bool(_hexes(s.get("palette"))), "mood": str(s.get("mood") or s.get("emotion") or s.get("tone") or "").strip(),
                    "rationale": str(s.get("rationale") or "").strip(), "variant": variant, "note": note,
                    # 本段对变体总则的数值覆盖(例:仙家闪回暖偏只 +80K)
                    "variant_grade": {k: v for k, v in (s.get("variant_grade") or {}).items() if k in GRADE_KEYS}
                    if isinstance(s.get("variant_grade"), dict) else {}})
    return out


def episode_palette(entry: dict | None, limit: int = 6, names: dict[str, str] | None = None) -> list[str]:
    """整集色板:key_palette 优先,否则各段落色板按出现顺序去重取前几个。"""
    if not isinstance(entry, dict):
        return []
    key = _resolve(entry.get("key_palette"), names)
    if key:
        return key[:limit]
    seen: list[str] = []
    for s in segments(entry, names):
        for c in s["palette"]:
            if c not in seen:
                seen.append(c)
    return seen[:limit]


def scene_palettes(cs: dict, ep: str) -> dict:
    """{场次号 → 色板, SCN-id → 色板, "_episode": 整集色板}。同一场景被多个段落引用时取第一个非变体段落
    (同一 SCN 常被现实段与闪回段共用,场次色块应显示现实段的色板),全是变体才取变体的。"""
    entry = episode_entry(cs, ep)
    out: dict[str, list[str]] = {}
    names = legend(cs)
    segs = segments(entry, names)
    for prefer_plain in (True, False):
        for s in segs:
            if not s["palette"] or (prefer_plain and s["variant"]):
                continue
            for key in s["scenes"] + s["scene_ids"]:
                out.setdefault(key, s["palette"])
    out["_episode"] = episode_palette(entry, names=names)
    return out


def palette_for(cs: dict, ep: str, scene_no: str = "", scene_id: str = "") -> list[str]:
    """scene_palette 处方「留空自动读脚本」:场次号 → SCN-id → 整集色板。"""
    table = scene_palettes(cs, ep)
    return table.get(scene_no or "\0") or table.get(scene_id or "\0") or table.get("_episode") or []


def variants(cs: dict) -> dict:
    """调色变体 {kind: {note, palette[], grade{}}}:新契约顶层 variants;旧写法在任意层级的 <kind>_variant 里。"""
    out: dict[str, dict] = {}
    v = cs.get("variants") if isinstance(cs, dict) else None
    if isinstance(v, dict):
        for kind, d in v.items():
            if isinstance(d, dict):
                out[str(kind)] = {"note": str(d.get("note") or "").strip(), "palette": _hexes(d.get("palette") or d.get("colors")),
                                  "grade": {k: d["grade"][k] for k in GRADE_KEYS if isinstance(d.get("grade"), dict) and k in d["grade"]}}

    def walk(x, depth=0):
        if depth > 4 or not isinstance(x, dict):
            return
        for k, d in x.items():
            m = re.fullmatch(r"(\w+)_variant", str(k))
            if m and isinstance(d, dict) and m.group(1) not in out:
                out[m.group(1)] = {"note": str(d.get("note") or "").strip(), "palette": _hexes(d.get("palette") or d.get("colors") or d),
                                   "grade": {}, "legacy": True}
            elif isinstance(d, dict):
                walk(d, depth + 1)
    walk({k: v for k, v in (cs or {}).items() if k != "episodes"})
    return out


# ---------------------------------------------------------------- 机检 color_script_ok
def validate(cs: dict, plan_eps: list[str] | None = None, strict: bool = False) -> tuple[list[str], list[str]]:
    """返回 (errors, warnings)。新契约字段缺失:strict=FAIL,否则 WARN(存量项目);内容性缺陷(某集无条目、
    段落无合法色值 / 无 mood / 无 rationale、peaks 指向不存在的集)一律 FAIL。"""
    errs: list[str] = []
    warns: list[str] = []
    soft = errs if strict else warns
    entries = episode_entries(cs)
    if not entries:
        return ["episodes 为空或不是数组"], warns
    have: dict[str, dict] = {}
    names = legend(cs)
    for i, e in enumerate(entries):
        n = entry_ep(e)
        if not n:
            errs.append(f"episodes[{i}]: 认不出集号(须有 episode_id: \"epNN\")")
            continue
        if n in have:
            errs.append(f"{n}: 条目重复")
        have[n] = e
        if e.get("episode_id") != n:
            soft.append(f"{n}: 缺 episode_id: \"{n}\"(现为 {({k: e.get(k) for k in ('ep', 'episode', 'episode_id') if k in e})})")
        if not isinstance(e.get("acts"), list) or not e.get("acts"):
            soft.append(f"{n}: 段落须写在 acts[](现为 {[k for k in _SEG_KEYS if isinstance(e.get(k), list)] or '无'})")
        if not _hexes(e.get("key_palette")):
            soft.append(f"{n}: 缺 key_palette(整集 3–6 个主色)")
        segs = segments(e, names)
        if not segs:
            errs.append(f"{n}: 没有任何色彩段落")
        for s in segs:
            tag = f"{n}/act {s['id']}"
            if not s["palette"]:
                errs.append(f"{tag}: palette 没有合法色值(#RRGGBB)")
            elif not s["palette_direct"]:
                soft.append(f"{tag}: palette 须直接写色值 #RRGGBB(现为色名 / 其它字段,宿主靠图例或兜底字段才读到)")
            if not s["mood"]:
                errs.append(f"{tag}: mood 为空")
            if not s["rationale"]:
                errs.append(f"{tag}: rationale 为空")
            if not s["events"]:
                soft.append(f"{tag}: 缺 event_refs(本段对应 episode_plan 的哪些事件)")
            if not s["scene_ids"] and not s["scenes"]:
                soft.append(f"{tag}: 缺 scene_ids(本段发生在哪些 SCN-场景;下游按场景取色板)")
            if s["variant"] and s["variant"] not in VARIANT_KINDS:
                errs.append(f"{tag}: variant={s['variant']!r} 不在枚举 {'/'.join(VARIANT_KINDS)} 内")
    for ep in plan_eps or []:
        if norm_ep(ep) and norm_ep(ep) not in have:
            errs.append(f"{norm_ep(ep)}: episode_plan 里有这一集,色彩脚本没有条目")
    for i, p in enumerate(cs.get("peaks") or []):
        n = entry_ep(p) if isinstance(p, dict) else ""
        if not n:
            errs.append(f"peaks[{i}]: 认不出集号")
        elif n not in have:
            errs.append(f"peaks[{i}]: 指向不存在的 {n}")
    # 变体:段落标了 variant,就必须有对应的变体定义,且定义可执行(note + 色板 + grade 数值)
    used = {s["variant"] for e in have.values() for s in segments(e, names) if s["variant"]}
    defs = variants(cs)
    for kind in sorted(used):
        d = defs.get(kind)
        if not d:
            errs.append(f"variants.{kind}: 有段落标了 variant={kind},却没有变体定义")
            continue
        if d.get("legacy"):
            soft.append(f"variants.{kind}: 变体定义须写在顶层 variants.{kind}(现为旧写法 {kind}_variant)")
        if not d["palette"]:
            errs.append(f"variants.{kind}: 没有合法色值")
        if not d["grade"]:
            soft.append(f"variants.{kind}: 缺 grade 数值(contrast / temperature_shift_k / soften / grain / luma_step_pct),下游调色只能从文字里猜")
    for kind, d in defs.items():
        for k, val in (d.get("grade") or {}).items():
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                errs.append(f"variants.{kind}.grade.{k}: 须为数值")
    return errs, warns
