# -*- coding: utf-8 -*-
"""caption_catalog.py — 花字用途目录与花字策略(2026-09-24)。

唯一源 `modules/caption_catalog.json`:
- 用途类型(type):后期处理页「花字」弹窗按分类勾选;captions.json 每条 `type` 必在目录内;
- 字号档(tier):headline/keyword/label,captions.json 缺 `tier` 时按类型 default_tier 推导,
  em_pct 按档校验;
- 项目级策略(settings.json `output.caption_mode` + `output.caption_types`):
  auto(默认)= 花字 Agent 按题材自选任意类型;manual = 只准出用户勾选的类型,
  且勾选类型本集有对应对象即默认要出(2026-09-30),不出须在 captions.json 顶层
  `type_skips` 逐类写原因(机检 caption_types_covered;缺模版不算理由);
- 策略盖章:captions.json 顶层 `caption_policy`({mode, types}),机检 caption_policy_fresh
  与当前设置比对——改了勾选集合而设计未重跑即 FAIL;auto 模式下缺章按 auto 兼容(存量项目)。

本模块不依赖 modules/ 里其他文件,宿主 API / 机检 CLI / 派单注入共用。
"""
from __future__ import annotations

import json
from pathlib import Path

CATALOG_PATH = Path(__file__).resolve().parent / "caption_catalog.json"
CAPTION_MODES = ("auto", "manual")
DEFAULT_MODE = "auto"
POLICY_KEY = "caption_policy"
SKIP_KEY = "type_skips"      # manual 模式勾选却未出的类型 → 原因(2026-09-30)

_CACHE: dict | None = None


def load_catalog() -> dict:
    global _CACHE
    if _CACHE is None:
        _CACHE = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return _CACHE


def type_ids() -> tuple[str, ...]:
    return tuple(t["id"] for t in load_catalog()["types"])


def type_map() -> dict[str, dict]:
    return {t["id"]: t for t in load_catalog()["types"]}


def tier_of(caption: dict) -> str:
    """一条花字的字号档:显式 tier > 类型 default_tier > label。"""
    tiers = load_catalog()["tiers"]
    t = caption.get("tier")
    if t in tiers:
        return t
    return (type_map().get(caption.get("type")) or {}).get("default_tier", "label")


def tier_em_range(tier: str) -> tuple[float, float]:
    lo, hi = load_catalog()["tiers"].get(tier, {}).get("em_pct", [5, 26])
    return float(lo), float(hi)


# ---------------------------------------------------------------- 项目级策略

def normalize_policy(output: dict | None) -> dict:
    """settings.json output 段 → 规范化策略 {mode, types(排序去重,仅目录内 id)}。"""
    o = output or {}
    mode = o.get("caption_mode") if o.get("caption_mode") in CAPTION_MODES else DEFAULT_MODE
    known = set(type_ids())
    raw = o.get("caption_types") if isinstance(o.get("caption_types"), list) else []
    types = sorted({str(x) for x in raw if str(x) in known})
    return {"mode": mode, "types": types if mode == "manual" else []}


def validate_output(o: dict) -> list[str]:
    """给宿主 _validate_output 用:返回错误信息列表(空 = 合法)。"""
    errs = []
    if "caption_mode" in o and o["caption_mode"] not in CAPTION_MODES:
        errs.append(f"output.caption_mode must be one of {CAPTION_MODES}")
    if "caption_types" in o:
        ct = o["caption_types"]
        if not isinstance(ct, list) or not all(isinstance(x, str) for x in ct):
            errs.append("output.caption_types must be a list of type ids")
        else:
            unknown = sorted(set(ct) - set(type_ids()))
            if unknown:
                errs.append("output.caption_types unknown ids: " + ", ".join(unknown))
    if o.get("caption_mode") == "manual" and errs == []:
        if not [x for x in (o.get("caption_types") or []) if x in type_ids()]:
            errs.append("output.caption_mode=manual requires at least one caption type "
                        "(output.caption_types)")
    return errs


def allowed_types(policy: dict) -> tuple[str, ...]:
    """策略下允许出现的类型 id:auto = 目录全集;manual = 勾选集合。"""
    if policy.get("mode") == "manual":
        return tuple(policy.get("types") or [])
    return type_ids()


# ---------------------------------------------------------------- 项目可用性(弹窗灰显)

def is_av_project(proj_root: Path) -> bool:
    return (Path(proj_root) / "av" / "beat_track.json").is_file()


def availability(proj_root: Path) -> list[dict]:
    """逐类型给出本项目可选性:[{id, available, reason}]。

    - av 项目:av_ok=false 的类型不可选(文案须来自母带原文并按语音起止对齐);
    - requires 列出的数据文件/目录一个都不存在:不可选,提示先产出该数据。
    """
    root = Path(proj_root)
    av = is_av_project(root)
    out = []
    for t in load_catalog()["types"]:
        ok, why = True, ""
        if av and not t.get("av_ok", True):
            ok, why = False, "av 项目:文案须来自母带原文并按语音起止对齐,此类文字通常不是念出来的"
        elif t.get("requires"):
            hit = any((root / r).exists() for r in t["requires"])
            if not hit:
                ok, why = False, "缺项目数据:" + " / ".join(t["requires"])
        out.append({"id": t["id"], "available": ok, "reason": why})
    return out


# ---------------------------------------------------------------- captions.json 机检

def policy_issues(data: dict, policy: dict) -> tuple[list[str], list[str]]:
    """返回 (types_allowed 问题, policy_fresh 问题)。

    types_allowed:manual 模式下每条 type 必在勾选集合内(auto 跳过,仅目录枚举由 schema 检管)。
    policy_fresh:captions.json 顶层 caption_policy 与当前设置一致;manual 模式缺章即 FAIL;
                 auto 模式缺章按 auto 兼容(存量项目);章上写 manual 而设置为 auto 亦 FAIL。
    """
    allowed_bad: list[str] = []
    fresh_bad: list[str] = []
    mode = policy.get("mode", DEFAULT_MODE)
    if mode == "manual":
        allowed = set(policy.get("types") or [])
        for i, c in enumerate(data.get("captions", [])):
            tag = c.get("id") or f"captions[{i}]"
            if c.get("type") not in allowed:
                allowed_bad.append(f"{tag}: type {c.get('type')!r} 不在用户勾选的花字类型内")
    stamp = data.get(POLICY_KEY)
    if stamp is None:
        if mode == "manual":
            fresh_bad.append(f"captions.json 缺 {POLICY_KEY} 盖章"
                             "(render_captions.py policy --stamp 或设计时写入)")
    elif not isinstance(stamp, dict):
        fresh_bad.append(f"{POLICY_KEY} 须为对象")
    else:
        cur = {"mode": mode, "types": sorted(policy.get("types") or [])}
        got = {"mode": stamp.get("mode"), "types": sorted(stamp.get("types") or [])}
        if got != cur:
            fresh_bad.append(f"{POLICY_KEY} 过期:文件 {got} ≠ 当前设置 {cur}(改了花字设定须重跑设计)")
    return allowed_bad, fresh_bad


def coverage_issues(data: dict, policy: dict, proj_root: Path | None = None) -> list[str]:
    """caption_types_covered(2026-09-30):manual 模式下每个勾选类型要么至少出一条,
    要么在顶层 `type_skips` 写明不出的原因;本项目不可用的类型(availability)不计。
    缺模版不构成理由——没有合适模版就新建或复用相近模版。auto 模式返回空。"""
    if policy.get("mode", DEFAULT_MODE) != "manual":
        return []
    issues: list[str] = []
    skips = data.get(SKIP_KEY)
    if skips is None:
        skips = {}
    elif not isinstance(skips, dict):
        return [f"{SKIP_KEY} 须为对象 {{type: 原因}}"]
    unavailable = ({a["id"] for a in availability(proj_root) if not a["available"]}
                   if proj_root else set())
    used = {c.get("type") for c in data.get("captions", [])}
    for t in sorted(policy.get("types") or []):
        if t in used or t in unavailable:
            continue
        why = skips.get(t)
        if not isinstance(why, str) or len(why.strip()) < 4:
            issues.append(f"勾选了 {t} 却一条未出,也没在 {SKIP_KEY}.{t} 写原因"
                          "(本集有对应对象就要出;确实没有就写明原因)")
        elif any(k in why for k in ("模版", "模板")) and \
                any(k in why for k in ("无", "缺", "没有", "暂无")):
            issues.append(f"{SKIP_KEY}.{t}「{why.strip()[:40]}」:缺模版不构成理由,"
                          "新建模版或复用相近模版(如 intro_person / label_scene)")
    return issues


def stamp(data: dict, policy: dict) -> dict:
    data[POLICY_KEY] = {"mode": policy.get("mode", DEFAULT_MODE),
                        "types": sorted(policy.get("types") or [])}
    return data


def load_project_policy(proj_root: Path) -> dict:
    """直接从 <项目>/settings.json 读策略(机检 CLI 用,不经宿主)。"""
    p = Path(proj_root) / "settings.json"
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        cfg = {}
    return normalize_policy((cfg.get("output") or {}))


# ---------------------------------------------------------------- 派单注入文案

def prompt_block(policy: dict, proj_root: Path | None = None) -> str:
    """给花字设计工位的策略段落(中文,注入系统提示词)。"""
    cat = load_catalog()
    tm = type_map()
    cats = {c["id"]: c["label"] for c in cat["categories"]}
    avail = {a["id"]: a for a in availability(proj_root)} if proj_root else {}

    def line(tid: str) -> str:
        t = tm[tid]
        a = avail.get(tid)
        note = f"(本项目不可用:{a['reason']})" if a and not a["available"] else ""
        return f"  - `{tid}`【{cats.get(t['category'], t['category'])}】{t['label']}:{t['desc']};字号档 {t['default_tier']}{note}"

    if policy.get("mode") == "manual":
        ids = list(policy.get("types") or [])
        head = ("- **花字策略:手动(manual)** —— 用户只允许出下列类型,captions.json 每条 `type` "
                "必在此集合内(机检 `caption_types_allowed`);**勾选类型本集有对应对象即默认要出**"
                "(如 creature_card:本集首次出场的生物;prop_card:首次亮相的道具/法宝),"
                f"不出的类型须在 captions.json 顶层 `{SKIP_KEY}` 逐类写原因"
                "(如 {\"creature_card\": \"本集无生物出场\"};机检 `caption_types_covered`),"
                "缺模版不算理由——新建或复用相近模版:")
    else:
        ids = list(type_ids())
        head = ("- **花字策略:自动(auto)** —— 按题材与内容从目录自选类型(caption-styling skill 题材表),"
                "不必每类都出;可用类型:")
    body = "\n".join(line(t) for t in ids if t in tm)
    tiers = "、".join(f"{k}={v['label']} em_pct {v['em_pct'][0]}–{v['em_pct'][1]}"
                     for k, v in cat["tiers"].items())
    tail = (f"\n- 每条可写 `tier`(字号档:{tiers});缺省按类型 default_tier 推导,em_pct 按档校验"
            f"\n- captions.json 顶层必须盖章 `{POLICY_KEY}`:"
            f"{json.dumps({'mode': policy.get('mode', DEFAULT_MODE), 'types': sorted(policy.get('types') or [])}, ensure_ascii=False)}"
            "(`python3 code/render_captions.py policy --project <slug> --ep epNN --stamp` 可代写;"
            "机检 `caption_policy_fresh` 与设置不一致即 FAIL,用户改了勾选集合须重跑设计)")
    return head + "\n" + body + tail
