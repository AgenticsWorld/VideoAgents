"""NPC 参与构图(2026-10-09,docs/npc_staging.md):在画面里加入路人、前景物体等 NPC 元素补空间、做出前中后三层。

开关分两级:
  项目级总开关  settings.json#output.npc_staging ∈ auto(默认,按场次判定)| off(全部关闭)
  场次级三态    自动 / 开 / 关 + 密度档 稀疏 sparse / 适中 medium / 热闹 dense
    自动判定  = 剧本拆解表 story/episodes/<ep>/script_breakdown.json 的 scenes[].npc {on, density, reason}
                (01-story/timeline-story 在 p5-breakdown 写;「重新分析」会刷新)
    用户覆盖  = story/episodes/<ep>/scene_npc.json(宿主自有,剧本预览页 / 故事板页的 👥 开关写;重新分析不覆盖)
  生效值 = 总开关 off → 关;用户选了开/关 → 用户值;否则自动判定;都没有(存量项目 / 推导视图)→ 关(未判定)。
  密度:用户改过的密度优先,其次自动判定的密度,再次 suggest() 的建议,最后 medium。

下游(只执行、不判断):
  故事板  storyboard.json 镜草案 npc[] = [{layer: fg|mg|bg, what}],场块 npc_applied = {on, density} 回执
          (机检 npc_staging_applied:check_storyboard)
  镜头表  shot_list shots[].npc 照抄草案(缺省时宿主按 storyboard_ref 回查草案,final_shot_npc)
  构图    composition.json layers 对应层写进路人/前景物体(机检 npc_layers_bound:check_composition)
  白模    extras 用 EXTRA-NPC-xx 摆位(图例标 anonymous NPC passer-by)
  提示词  宿主固定段「Background figures (NPC): … End background figures.」(sync_prompt / check_prompt,
          机检 npc_prompt_bound),有路人的组把身份锁句收窄到具名角色
剧本里点名的群演(storyboard extras)不受开关影响,与 npc[] 是两回事。
"""
from __future__ import annotations

import fcntl
import json
import math
import os
import re
import time
from pathlib import Path

MASTER_MODES = ("auto", "off")
DENSITIES = ("sparse", "medium", "dense")
LAYERS = ("fg", "mg", "bg")
OVERRIDE_MODES = ("auto", "on", "off")
OVERRIDE_REL = "story/episodes/{ep}/scene_npc.json"
OVERRIDE_SCHEMA = "scene_npc/1.0"
FEATURE_DATE = "2026-10-09"      # 此前产出的拆解表缺 npc 判定只 WARN(存量不追溯)
DENSITY_ZH = {"sparse": "稀疏", "medium": "适中", "dense": "热闹"}
LAYER_ZH = {"fg": "前景", "mg": "中景", "bg": "背景"}
LAYER_EN = {"fg": "foreground", "mg": "midground", "bg": "background"}
# 开启场次里「可放 NPC 的镜」(非插入、非特写)至少多少比例带 npc[](不足只 WARN)
DENSITY_MIN_RATIO = {"sparse": 0.25, "medium": 0.4, "dense": 0.6}
DENSITY_PROMPT = {"sparse": "only one or two at a time, far from the action",
                  "medium": "a few at a time, never a crowd",
                  "dense": "a lively crowd that fills the depth of the space"}

SEG_BEGIN = "Background figures (NPC):"
SEG_END = "End background figures."
SEG_RE = re.compile(r"\n?" + re.escape(SEG_BEGIN) + r".*?" + re.escape(SEG_END) + r"\n?", re.S)
# 有路人的组:身份锁句「每个画面里的人都要对上参考图 / 不许多余的人」收窄到具名角色(scene_cast 同口径改写的补充)
_LOCK_EVERY_RE = re.compile(r"every person on screen(?:,? including anyone seen from behind,?)? must match one of (?:these|the) reference images", re.I)
_LOCK_EXTRA_RE = re.compile(r"no extra or duplicate person", re.I)
_LOCK_EVERY_NEW = "every named character on screen must match their own reference image"
_LOCK_COUNT_RE = re.compile(r"exactly\s+(\d+)\s+(?!named\s)characters?(?:\(s\))?\s+on screen", re.I)


# ---------------------------------------------------------------- 基础读写

def _read_json(p: Path):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return None


def master_mode(base: Path) -> str:
    """项目级总开关(settings.json output.npc_staging);缺键 = auto(没有判定的场次照样按关处理,存量行为不变)。"""
    cfg = _read_json(Path(base) / "settings.json") or {}
    v = str((cfg.get("output") or {}).get("npc_staging") or "auto").strip().lower()
    return v if v in MASTER_MODES else "auto"


def override_path(base: Path, ep: str) -> Path:
    return Path(base) / OVERRIDE_REL.format(ep=ep)


def load_overrides(base: Path, ep: str) -> dict:
    """{schema, ep, scenes: {S01: {mode, density, updated_at}}};文件缺失/坏 → 空。"""
    d = _read_json(override_path(base, ep)) or {}
    scenes = d.get("scenes") if isinstance(d.get("scenes"), dict) else {}
    clean = {}
    for no, rec in scenes.items():
        if not isinstance(rec, dict):
            continue
        mode = rec.get("mode") if rec.get("mode") in OVERRIDE_MODES else "auto"
        dens = rec.get("density") if rec.get("density") in DENSITIES else None
        if mode == "auto" and not dens:
            continue
        clean[str(no)] = {"mode": mode, "density": dens, "updated_at": rec.get("updated_at")}
    return {"schema": OVERRIDE_SCHEMA, "ep": ep, "scenes": clean}


def set_override(base: Path, ep: str, scene_no: str, mode: str, density: str | None = None) -> dict:
    """写一场的用户覆盖(flock 串行)。mode=auto 且无密度 = 删除该条(回到自动判定);全空则删文件。返回改后的整份内容。"""
    scene_no = str(scene_no or "").strip()
    if not re.match(r"^[^\s/\\<>\"']{1,24}$", scene_no):     # 场次号有 S03A-1 / S03(续) / EC 等形态
        raise ValueError(f"bad scene number: {scene_no!r}")
    if mode not in OVERRIDE_MODES:
        raise ValueError(f"mode must be one of {OVERRIDE_MODES}")
    if density not in (None, "", *DENSITIES):
        raise ValueError(f"density must be one of {DENSITIES} or empty")
    density = density or None
    p = override_path(base, ep)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p.with_suffix(".lock"), "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        d = load_overrides(base, ep)
        if mode == "auto" and not density:
            d["scenes"].pop(scene_no, None)
        else:
            d["scenes"][scene_no] = {"mode": mode, "density": density if mode != "off" else None,
                                     "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        if d["scenes"]:
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, p)
        elif p.exists():
            p.unlink()
    return d


# ---------------------------------------------------------------- 自动判定:关键词建议(只作参考与界面提示,正式判定由 timeline-story 写)

_OFF_SPACE = ("空无一人", "四下无人", "杳无人迹", "空无人烟", "人迹罕至", "空荡荡", "空空荡荡", "空荡", "寂静无人", "夜深人静",
              "清场", "屏退左右", "屏退", "密谈", "密议", "潜入", "deserted", "empty street", "nobody around", "no one around")
_OFF_PRIVATE = ("卧房", "卧室", "寝室", "寝殿", "闺房", "内室", "密室", "浴室", "浴房", "车厢内", "车内", "轿中", "轿内", "帐中",
                "梦境", "梦中", "幻境", "识海", "心魔", "意识空间", "bedroom", "dream", "inside the car")
_DENSE = ("人山人海", "人群", "人潮", "熙攘", "熙熙攘攘", "人声鼎沸", "围观", "拥挤", "摩肩接踵", "水泄不通", "喧闹", "热闹",
          "庙会", "夜市", "集市", "市集", "闹市", "灯会", "crowd", "bustling", "packed", "busy market", "night market", "fair")
_PUBLIC = ("街市", "街道", "大街", "街头", "街上", "广场", "码头", "渡口", "车站", "站台", "机场", "酒楼", "酒肆", "茶馆", "茶楼",
           "客栈大堂", "饭馆", "餐厅", "食堂", "商场", "宴", "朝堂", "朝会", "大殿", "军营", "校场", "操场", "校园", "教室",
           "医院大厅", "候车", "市场", "店铺", "street", "square", "plaza", "station", "harbor", "harbour", "dock", "tavern",
           "restaurant", "banquet", "camp", "market", "mall", "classroom", "court")
_SPARSE = ("庭院", "院子", "院中", "走廊", "回廊", "长廊", "书院", "学堂", "铺子", "府邸", "府中", "前厅", "村口", "村道",
           "小巷", "巷子", "山道", "官道", "田间", "courtyard", "corridor", "hallway", "lobby", "village", "alley", "lane")
_NIGHT = ("深夜", "凌晨", "子时", "丑时", "三更", "半夜", "late night", "midnight")
_NIGHT_OK = ("夜市", "灯会", "庙会", "宴", "night market", "banquet", "festival")


def _hit(text: str, words) -> str | None:
    low = text.lower()
    for w in words:
        if re.fullmatch(r"[a-z ]+", w):
            if re.search(r"\b" + re.escape(w) + r"\b", low):
                return w
        elif w in text:
            return w
    return None


def _scene_text(sc: dict) -> str:
    parts = [sc.get(k) for k in ("scene_name", "summary", "action", "purpose", "beat")]
    for b in sc.get("blocks") or []:
        if isinstance(b, dict) and b.get("type") in ("action", "sound"):
            parts.append(b.get("text"))
    return " ".join(str(x) for x in parts if x)


def suggest(sc: dict, zh: bool = True) -> dict:
    """按剧本文字给一个建议 {on, density, reason, rule, word}。只作参考:正式判定由 timeline-story 写进拆解表。"""
    text = _scene_text(sc or {})
    tod = str((sc or {}).get("time_of_day") or "")

    def out(on, dens, rule, word):
        if zh:
            why = {"off_space": "剧本写明无人/私下", "off_private": "私密/内心空间", "night": "深夜时段",
                   "dense": "人多热闹的场合", "public": "公共空间", "sparse": "半公共空间",
                   "none": "未命中公共空间用语"}[rule]
            reason = f"{why}" + (f"(「{word}」)" if word else "")
        else:
            why = {"off_space": "script says the place is empty / private", "off_private": "private or inner space",
                   "night": "late night", "dense": "busy, crowded occasion", "public": "public space",
                   "sparse": "semi-public space", "none": "no public-space wording found"}[rule]
            reason = why + (f' ("{word}")' if word else "")
        return {"on": on, "density": dens if on else None, "reason": reason, "rule": rule, "word": word}

    w = _hit(text, _OFF_SPACE)
    if w:
        return out(False, None, "off_space", w)
    w = _hit(text, _OFF_PRIVATE)
    if w:
        return out(False, None, "off_private", w)
    if _hit(tod + " " + text[:200], _NIGHT) and not _hit(text, _NIGHT_OK):
        return out(False, None, "night", _hit(tod + " " + text[:200], _NIGHT))
    w = _hit(text, _DENSE)
    if w:
        return out(True, "dense", "dense", w)
    w = _hit(text, _PUBLIC)
    if w:
        return out(True, "medium", "public", w)
    w = _hit(text, _SPARSE)
    if w:
        return out(True, "sparse", "sparse", w)
    return out(False, None, "none", None)


# ---------------------------------------------------------------- 判定格式 / 生效值

def normalize_judgment(v) -> dict | None:
    """拆解表 scenes[].npc → {on, density, reason};不合格式返回 None(按未判定处理)。"""
    if not isinstance(v, dict) or not isinstance(v.get("on"), bool):
        return None
    dens = v.get("density") if v.get("density") in DENSITIES else None
    if v["on"] and not dens:
        dens = "medium"
    return {"on": v["on"], "density": dens if v["on"] else None, "reason": str(v.get("reason") or "").strip()}


def judgment_errors(no: str, v) -> list[str]:
    """拆解表单场 npc 字段的格式错误(字段存在时才查)。"""
    if v is None:
        return []
    if not isinstance(v, dict):
        return [f"{no}.npc 须为对象 {{on, density, reason}}"]
    errs = []
    if not isinstance(v.get("on"), bool):
        errs.append(f"{no}.npc.on 须为 true/false")
    if v.get("on") is True and v.get("density") not in DENSITIES:
        errs.append(f"{no}.npc.density 须为 {'|'.join(DENSITIES)}(开启时必填)")
    if v.get("on") is False and v.get("density") not in (None, ""):
        errs.append(f"{no}.npc.density 关闭时不写(null)")
    if not str(v.get("reason") or "").strip():
        errs.append(f"{no}.npc.reason 必填(引用剧本原文说明为什么开/关)")
    return errs


def _breakdown(base: Path, ep: str) -> dict:
    from modules import script_breakdown as sb
    return sb.load(base, ep).get("breakdown") or {}


def resolve(base: Path, ep: str, breakdown: dict | None = None, master: str | None = None, zh: bool = True,
            extra_scene_nos=()) -> dict:
    """{master, overrides_rel, scenes: {S01: {on, density, source, reason, auto, user, suggest}}}。
    source ∈ master_off | user | auto | none。breakdown 缺省时读拆解表(正式产物优先,缺则推导视图——推导视图没有判定)。"""
    base = Path(base)
    master = master if master in MASTER_MODES else master_mode(base)
    bd = breakdown if isinstance(breakdown, dict) else _breakdown(base, ep)
    ov = load_overrides(base, ep)["scenes"]
    rows: dict[str, dict] = {}
    scenes = [sc for sc in (bd.get("scenes") or []) if isinstance(sc, dict) and sc.get("no")]
    seen = {str(sc["no"]) for sc in scenes}
    for no in list(extra_scene_nos) + list(ov):
        if str(no) not in seen:
            scenes.append({"no": str(no)})
            seen.add(str(no))
    for sc in scenes:
        no = str(sc["no"])
        auto = normalize_judgment(sc.get("npc"))
        user = ov.get(no)
        sug = suggest(sc, zh=zh) if sc.get("scene_name") or sc.get("action") or sc.get("blocks") else None
        if master == "off":
            on, dens, source = False, None, "master_off"
        elif user and user["mode"] in ("on", "off"):
            on, source = user["mode"] == "on", "user"
            dens = user.get("density") or (auto or {}).get("density") or (sug or {}).get("density") or "medium"
        elif auto:
            on, source = auto["on"], "auto"
            dens = (user or {}).get("density") or auto.get("density") or "medium"
        else:
            on, dens, source = False, None, "none"
        rows[no] = {"on": on, "density": dens if on else None, "source": source,
                    "reason": (auto or {}).get("reason") or "", "auto": auto, "user": user, "suggest": sug}
    return {"master": master, "overrides_rel": OVERRIDE_REL.format(ep=ep), "scenes": rows}


def effective(res: dict, scene_no: str) -> dict:
    return (res.get("scenes") or {}).get(str(scene_no)) or {"on": False, "density": None, "source": "none"}


# ---------------------------------------------------------------- 故事板

def shot_npc(d: dict) -> list[dict]:
    """镜草案 / 镜头表镜的 npc[] 归一:只留 layer 合法、what 非空的条目。"""
    out = []
    items = d.get("npc") if isinstance(d, dict) and isinstance(d.get("npc"), list) else []
    for x in items:
        if isinstance(x, dict) and x.get("layer") in LAYERS and str(x.get("what") or "").strip():
            out.append({"layer": x["layer"], "what": str(x["what"]).strip()})
    return out


def _npc_entry_errors(where: str, v) -> list[str]:
    if v is None:
        return []
    if not isinstance(v, list):
        return [f"{where}.npc 须为数组 [{{layer, what}}]"]
    errs = []
    for k, x in enumerate(v):
        if not isinstance(x, dict) or x.get("layer") not in LAYERS:
            errs.append(f"{where}.npc[{k}].layer 须为 {'|'.join(LAYERS)}")
        elif not str(x.get("what") or "").strip():
            errs.append(f"{where}.npc[{k}].what 必填(画面里是什么、在做什么)")
    return errs


def normalize_applied(v) -> dict | None:
    if not isinstance(v, dict) or not isinstance(v.get("on"), bool):
        return None
    return {"on": v["on"], "density": v.get("density") if v["on"] and v.get("density") in DENSITIES else None}


def _size_kind(size: str) -> str:
    s = str(size or "")
    if re.search(r"插入|insert", s, re.I):
        return "insert"
    if re.search(r"特写|\bE?CU\b|close[- ]?up", s, re.I):
        return "close"
    return "other"


def applied_state(raw_scene: dict, eff: dict) -> str:
    """故事板一场是否按当前生效设定写了 NPC:ok | stale。"""
    from modules.storyboard_board import _scene_drafts
    drafts = [d for d in _scene_drafts(raw_scene or {}) if isinstance(d, dict)]
    n_with = sum(1 for d in drafts if shot_npc(d))
    applied = normalize_applied((raw_scene or {}).get("npc_applied"))
    if eff.get("on"):
        ok = applied == {"on": True, "density": eff.get("density")} and n_with > 0
    else:
        ok = n_with == 0 and not (applied and applied["on"])
    return "ok" if ok else "stale"


def check_storyboard(base: Path, ep: str, res: dict | None = None) -> tuple[list[str], list[str], dict]:
    """机检 npc_staging_applied:返回 (errors, warnings, {场次: state})。"""
    from modules.storyboard_board import board_scene_no, _scene_drafts, _draft_order
    sbj = _read_json(Path(base) / "directing" / ep / "storyboard.json") or {}
    raw = sbj.get("scenes") if isinstance(sbj.get("scenes"), list) else []
    nos = [board_scene_no(sc, i) for i, sc in enumerate(raw) if isinstance(sc, dict)]
    res = res or resolve(base, ep, extra_scene_nos=nos)
    errors, warns, states = [], [], {}
    for i, sc in enumerate(raw):
        if not isinstance(sc, dict):
            continue
        no = board_scene_no(sc, i)
        eff = effective(res, no)
        drafts = [d for d in _scene_drafts(sc) if isinstance(d, dict)]
        for j, d in enumerate(drafts):
            errors += _npc_entry_errors(f"{no}#{_draft_order(d, j)}", d.get("npc"))
        with_npc = [d for d in drafts if shot_npc(d)]
        applied = normalize_applied(sc.get("npc_applied"))
        state = applied_state(sc, eff)
        states[no] = state
        if eff.get("on"):
            want = {"on": True, "density": eff.get("density")}
            if applied != want:
                errors.append(f"{no}: NPC 参与构图生效为「开·{DENSITY_ZH.get(eff.get('density'), eff.get('density'))}」"
                              f"({eff.get('source')}),场块 npc_applied 须为 {json.dumps(want)}(现为 {json.dumps(applied)})")
            if not with_npc:
                errors.append(f"{no}: NPC 参与构图已开启,但没有一镜写 npc[]")
            else:
                elig = [d for d in drafts if _size_kind(d.get("size_hint") or d.get("size")) != "insert"]
                need = DENSITY_MIN_RATIO.get(eff.get("density") or "medium", 0.4)
                got = sum(1 for d in elig if shot_npc(d))
                if elig and got < math.ceil(need * len(elig) - 1e-9):
                    warns.append(f"{no}: 密度「{DENSITY_ZH.get(eff.get('density'))}」建议至少 {math.ceil(need * len(elig))}/{len(elig)} "
                                 f"个非插入镜带 NPC,现 {got}")
            for j, d in enumerate(drafts):
                if shot_npc(d) and _size_kind(d.get("size_hint") or d.get("size")) == "insert":
                    warns.append(f"{no}#{_draft_order(d, j)}: 插入镜不放 NPC")
        else:
            if with_npc:
                errors.append(f"{no}: NPC 参与构图为关({eff.get('source')}),但 "
                              f"{len(with_npc)} 镜写了 npc[](剧本点名的群演写 extras,不写 npc)")
            if applied and applied["on"]:
                errors.append(f"{no}: NPC 参与构图为关,场块 npc_applied 不得为开")
    return errors, warns, states


# ---------------------------------------------------------------- 镜头表 / 构图

_REF_RE = re.compile(r"^(.+?)(?:/shots_draft)?/order:(\d+)(?:/split:[^/]+)?$")


def final_shot_npc(base: Path, ep: str, sl: dict | None = None, res: dict | None = None) -> dict[str, dict]:
    """定稿镜 → {scene_no, npc[]}:镜头表镜自带 npc 优先,否则按 storyboard_ref 回查故事板草案;只留生效为开的场次。"""
    from modules.storyboard_board import board_scene_no, _scene_drafts, _draft_order
    base = Path(base)
    sl = sl if isinstance(sl, dict) else (_read_json(base / "directing" / ep / "shot_list.json") or {})
    sbj = _read_json(base / "directing" / ep / "storyboard.json") or {}
    drafts = {}
    for i, sc in enumerate(sbj.get("scenes") or []):
        if isinstance(sc, dict):
            no = board_scene_no(sc, i)
            for j, d in enumerate(_scene_drafts(sc)):
                if isinstance(d, dict):
                    drafts[(no, _draft_order(d, j))] = d
    res = res or resolve(base, ep)
    out = {}
    for s in sl.get("shots") or []:
        if not isinstance(s, dict) or not s.get("shot_id"):
            continue
        no = str(s.get("scene_no") or "")
        ref = _REF_RE.match(str(s.get("storyboard_ref") or ""))
        if not no and ref:
            no = ref.group(1)
        if not effective(res, no).get("on"):
            continue
        npc = shot_npc(s) if isinstance(s.get("npc"), list) else (shot_npc(drafts.get((ref.group(1), int(ref.group(2))), {})) if ref else [])
        if npc:
            out[str(s["shot_id"])] = {"scene_no": no, "npc": npc}
    return out


def check_shots(base: Path, ep: str, res: dict | None = None) -> tuple[list[str], list[str]]:
    """机检 npc_shots_consistent:镜头表 npc[] 格式;生效为关的场次不得带 npc[]。"""
    sl = _read_json(Path(base) / "directing" / ep / "shot_list.json") or {}
    res = res or resolve(base, ep)
    errors, warns = [], []
    for s in sl.get("shots") or []:
        if not isinstance(s, dict):
            continue
        sid = s.get("shot_id") or "?"
        errors += _npc_entry_errors(sid, s.get("npc"))
        if shot_npc(s) and not effective(res, s.get("scene_no") or "").get("on"):
            errors.append(f"{sid}: 场次 {s.get('scene_no')} NPC 参与构图为关,镜头表不得带 npc[]")
    return errors, warns


def check_composition(base: Path, ep: str, res: dict | None = None) -> tuple[list[str], list[str]]:
    """机检 npc_layers_bound:带 npc[] 的定稿镜,composition.json 对应层(fg/mg/bg)须写明 NPC。"""
    base = Path(base)
    errors, warns = [], []
    for sid, rec in final_shot_npc(base, ep, res=res).items():
        p = base / "directing" / ep / "shots" / sid / "composition.json"
        comp = _read_json(p)
        if comp is None:
            warns.append(f"{sid}: 尚无 composition.json")
            continue
        layers = comp.get("layers") if isinstance(comp.get("layers"), dict) else {}
        for layer in sorted({x["layer"] for x in rec["npc"]}):
            v = layers.get(layer)
            if not str(v or "").strip() or str(v).strip() in ("无", "none", "None", "-"):
                errors.append(f"{sid}: npc[] 有{LAYER_ZH[layer]}({layer})NPC,composition.json layers.{layer} 却为空")
    return errors, warns


# ---------------------------------------------------------------- 视频提示词固定段

def group_rows(base: Path, ep: str, group: dict, shots_npc: dict[str, dict]) -> list[dict]:
    """本组带 NPC 的镜:[{n: 组内序号(Shot n), shot_id, scene_no, npc[]}]。"""
    rows = []
    for n, sid in enumerate(group.get("shots") or [], 1):
        rec = shots_npc.get(str(sid))
        if rec:
            rows.append({"n": n, "shot_id": str(sid), "scene_no": rec["scene_no"], "npc": rec["npc"]})
    return rows


def segment(rows: list[dict], density: str | None) -> str:
    """组 prompt 固定段(英文固定句 + 分镜里写的 NPC 描述逐字)。rows 空 → 空串。"""
    if not rows:
        return ""
    per = []
    for r in rows:
        bits = [f"{LAYER_EN[x['layer']]}: {x['what'].rstrip('。.;；')}" for x in sorted(r["npc"], key=lambda x: LAYERS.index(x["layer"]))]
        per.append(f"Shot {r['n']} — " + "; ".join(bits) + ".")
    dens = DENSITY_PROMPT.get(density or "medium", DENSITY_PROMPT["medium"])
    return (f"{SEG_BEGIN} " + " ".join(per) +
            f" These are anonymous background figures and foreground objects that only add depth and life to the space "
            f"({dens}). They are not registered characters: they do not match or resemble any reference image, never speak "
            f"or react to the dialogue, never look into the camera, stay softer in focus than the named characters, and never "
            f"block a named character's face or eyeline. The identity lock applies to the named characters only. {SEG_END}")


def apply_prompt(prompt: dict, rows: list[dict], density: str | None) -> dict:
    """幂等刷新组 prompt 的 NPC 固定段:有 rows 写在 Global constraints 之前(无则文末),并把身份锁句收窄到具名角色;
    无 rows 删除旧段。返回新 prompt(不改入参)。"""
    from modules.prompt_layout import paragraphize
    out = dict(prompt or {})
    text = SEG_RE.sub("\n\n", out.get("video_prompt") or "")
    seg = segment(rows, density)
    if seg:
        text = _LOCK_EVERY_RE.sub(_LOCK_EVERY_NEW, text)
        text = _LOCK_EXTRA_RE.sub("no duplicate person", text)
        text = _LOCK_COUNT_RE.sub(r"exactly \1 named character(s) on screen", text)
        k = text.rfind("Global constraints:")
        text = (text[:k].rstrip() + "\n\n" + seg + "\n\n" + text[k:]) if k >= 0 else (text.rstrip() + "\n\n" + seg)
        out["npc_staging"] = {"shots": [r["shot_id"] for r in rows], "density": density or "medium"}
    else:
        out.pop("npc_staging", None)
    out["video_prompt"] = paragraphize(text)
    return out


def check_prompt(prompt: dict, rows: list[dict], density: str | None) -> list[str]:
    text = (prompt or {}).get("video_prompt") or ""
    m = SEG_RE.search(text)
    seg = segment(rows, density)
    errs = []
    if seg:
        if not m or re.sub(r"\s+", " ", m.group(0)).strip() != re.sub(r"\s+", " ", seg).strip():
            errs.append("NPC 固定段缺失或过期(跑 code/npc_staging.py --write)")
        if _LOCK_EVERY_RE.search(text) or _LOCK_EXTRA_RE.search(text) or _LOCK_COUNT_RE.search(text):
            errs.append("有路人的组身份锁句仍写「every person on screen must match / no extra person」(跑 --write 收窄到具名角色)")
    elif m:
        errs.append("本组已无 NPC(场次关闭或分镜未写),须删除 NPC 固定段(跑 --write)")
    return errs


def sync_prompts(base: Path, ep: str, groups=None, write: bool = False) -> dict:
    """逐组核对 / 刷新 NPC 固定段。返回 {groups: [...], updated: [...], errors: [...]}。"""
    base = Path(base)
    sl = _read_json(base / "directing" / ep / "shot_list.json") or {}
    res = resolve(base, ep)
    shots_npc = final_shot_npc(base, ep, sl=sl, res=res)
    want = set(groups or [])
    report, updated, errors = [], [], []
    for g in sl.get("generation_groups") or []:
        if not isinstance(g, dict) or not g.get("group_id"):
            continue
        gid = g["group_id"]
        if want and gid not in want:
            continue
        rows = group_rows(base, ep, g, shots_npc)
        dens = effective(res, rows[0]["scene_no"]).get("density") if rows else None
        pp = base / "assets" / "prompts" / ep / f"{gid}.json"
        prompt = _read_json(pp)
        if prompt is None:
            report.append({"group_id": gid, "npc_shots": [r["shot_id"] for r in rows], "has_prompt": False})
            continue
        new = apply_prompt(prompt, rows, dens) if write else prompt
        if write and new != prompt:
            tmp = pp.with_suffix(".tmp")
            tmp.write_text(json.dumps(new, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, pp)
            updated.append(gid)
        errors += [f"{gid}: {e}" for e in check_prompt(new, rows, dens)]
        report.append({"group_id": gid, "npc_shots": [r["shot_id"] for r in rows], "density": dens, "has_prompt": True})
    return {"groups": report, "updated": updated, "errors": errors}


# ---------------------------------------------------------------- 拆解表判定机检(npc_judged)

def check_judgments(base: Path, ep: str, bd: dict | None = None, mtime: float | None = None) -> tuple[list[str], list[str]]:
    """拆解表每场都要有 npc 判定(总开关 off 时跳过);FEATURE_DATE 之前的存量拆解表缺判定只 WARN。"""
    base = Path(base)
    if master_mode(base) == "off":
        return [], []
    if bd is None:
        from modules import script_breakdown as sb
        p = sb.breakdown_path(base, ep)
        bd = _read_json(p) or {}
        mtime = p.stat().st_mtime if p.is_file() else None
    legacy = mtime is not None and time.strftime("%Y-%m-%d", time.localtime(mtime)) < FEATURE_DATE
    errors, warns = [], []
    for sc in bd.get("scenes") or []:
        if not isinstance(sc, dict) or not sc.get("no") or sc.get("pacing_only"):
            continue
        no = str(sc["no"])
        if sc.get("npc") is None:
            (warns if legacy else errors).append(f"{no} 缺 NPC 参与构图判定 npc {{on, density, reason}}(npc_judged)")
        else:
            errors += judgment_errors(no, sc.get("npc"))
    return errors, warns
