"""场景内外景一致性 scene_int_ext_match(WORKFLOW.md Phase 3 scene / Phase 5 p5-screenplay,2026-09-17)。

「一 SCN 一空间」的内外景一环:同一处地点(总兵府)书里既有院子里的戏、又有厅内的戏时,院落与厅内是两个空间,
要各有 ID、各有布局包。前科 fengshen3 ep06:S08/S12 场头 `INT | SCN-0108`,而 SCN-0108 是院落级室外布局
(前厅只是外观地标),白模厅内无物,人工发现后才补立 SCN-0140。

口径:`bible/scenes/index.json` 每条登记 `int_ext` ∈ INT / EXT / INT/EXT(半开放:亭、廊、洞口、敞篷车,
一张俯视图同时容纳内外);剧本场头的 INT/EXT 必须与所挂 SCN 的 `int_ext` 相容。
存量 index 没有该字段 → 按名称/层级推断,只 WARN 不阻断。
"""
from __future__ import annotations

from pathlib import Path

from modules import script_breakdown as sb

VALUES = ("INT", "EXT", "INT/EXT")
_ALIASES = {"INT": "INT", "EXT": "EXT", "INT/EXT": "INT/EXT", "EXT/INT": "INT/EXT", "内": "INT", "外": "EXT",
            "内景": "INT", "外景": "EXT", "室内": "INT", "室外": "EXT", "半开放": "INT/EXT", "半室外": "INT/EXT"}
# 存量推断用词:只在一边命中时才下结论,两边都命中或都不命中 → 不推断
_INT_WORDS = ("内景", "室内", "厅", "堂", "室", "房", "舱", "帐内", "洞", "殿内", "宫内", "阁内", "楼内", "车内", "牢", "窖", "密道")
_EXT_WORDS = ("外景", "室外", "庭院", "院落", "前院", "后院", "空场", "广场", "校场", "演武场", "街", "巷", "路", "道旁", "野", "山",
              "岭", "峰", "崖", "谷", "河", "江", "湖", "海", "岸", "滩", "渡口", "桥", "林", "城门", "城外", "城楼", "城墙", "关外",
              "战场", "阵前", "营外", "辕门", "云端", "天空", "码头", "田", "村口")


def norm(v) -> str | None:
    if not isinstance(v, str):
        return None
    return _ALIASES.get(v.strip().upper()) or _ALIASES.get(v.strip())


def infer(entry: dict) -> str | None:
    """存量条目无 int_ext 时的推断;拿不准返回 None。"""
    name = str(entry.get("name") or entry.get("canonical_name") or "")
    hit_int = any(w in name for w in _INT_WORDS)
    hit_ext = any(w in name for w in _EXT_WORDS)
    if hit_int != hit_ext:
        return "INT" if hit_int else "EXT"
    if not hit_int and str(entry.get("level") or "").lower() in ("room", "房间"):
        return "INT"
    return None


def load_index(base: Path) -> dict[str, dict]:
    idx = sb.read_json(base / "bible" / "scenes" / "index.json") or {}
    rows = idx if isinstance(idx, list) else (idx.get("scenes") or idx.get("entries") or [])
    return {s["id"]: s for s in rows if isinstance(s, dict) and s.get("id")}


def scene_int_ext(entry: dict) -> tuple[str | None, bool]:
    """返回 (内外景, 是否登记值)。登记值非法按未登记处理。"""
    v = norm(entry.get("int_ext"))
    return (v, True) if v else (infer(entry), False)


def compatible(header: str | None, scene: str | None) -> bool:
    return not header or not scene or scene == "INT/EXT" or header == scene


def _relatives(sid: str, want: str, index: dict[str, dict]) -> list[str]:
    """同一处地点里内外景对得上的现成 ID:子级、父级、同父兄弟。"""
    me = index.get(sid) or {}
    out = []
    for oid, e in index.items():
        if oid == sid:
            continue
        related = e.get("parent") == sid or me.get("parent") == oid or (me.get("parent") and e.get("parent") == me.get("parent")) \
            or e.get("split_from") == sid
        if related and scene_int_ext(e)[0] in (want, "INT/EXT"):
            out.append(oid)
    return out


def verify_index(base: Path) -> dict:
    """全书:index 的 int_ext 登记情况。非法值 FAIL,未登记 WARN。"""
    index = load_index(base)
    errors, warns, missing = [], [], []
    for sid, e in index.items():
        raw = e.get("int_ext")
        if raw is None:
            missing.append(sid)
        elif not norm(raw):
            errors.append(f"{sid} int_ext={raw!r} 非法,须为 {' / '.join(VALUES)}")
    if missing:
        head = ", ".join(missing[:12]) + (f" …共 {len(missing)} 条" if len(missing) > 12 else "")
        warns.append(f"未登记 int_ext(存量,按名称推断兜底):{head}")
    return {"n_scenes": len(index), "missing": missing, "errors": errors, "warns": warns}


def verify_episode(base: Path, ep: str) -> dict:
    """单集:剧本场头 INT/EXT vs 所挂 SCN 的 int_ext。"""
    index = load_index(base)
    sp = base / "story" / "episodes" / ep / "screenplay.md"
    parsed = sb.parse_screenplay(sb.read_text(sp)) if sp.is_file() else {"scenes": []}
    errors, warns, rows = [], [], []
    for sc in parsed.get("scenes") or []:
        sid, head = sc.get("scene_id"), sc.get("int_ext")
        entry = index.get(sid or "")
        if not entry:
            continue  # ID 不在 index 由 script_breakdown_ok / 剧本机检管
        val, declared = scene_int_ext(entry)
        row = {"scene": sc.get("no"), "scene_id": sid, "header": head, "scene_int_ext": val, "declared": declared, "ok": True}
        rows.append(row)
        if not head:
            warns.append(f"{sc.get('no')} 场头缺 INT/EXT,无法核对 {sid}")
            continue
        if head == "INT/EXT" and val in ("INT", "EXT"):
            warns.append(f"{sc.get('no')} 场头 INT/EXT 跨内外,而 {sid} 登记为 {val}:一场跨两个空间须按空间拆场次")
            continue
        if compatible(head, val):
            continue
        row["ok"] = False
        rel = _relatives(sid, head, index)
        fix = (f"改挂 {' / '.join(rel)}" if rel else
               f"index 里没有这处地点的 {head} 空间 → 回执 scene_gaps[] 上报,由 scene 新立 ID(parent/split_from={sid})并补布局包,剧本再改挂")
        msg = f"{sc.get('no')} 场头 {head},所挂 {sid}「{entry.get('name')}」是 {val}{'' if declared else '(按名称推断)'};{fix}"
        (errors if declared else warns).append(msg)
    return {"ep": ep, "rows": rows, "errors": errors, "warns": warns, "n_scenes": len(rows)}

