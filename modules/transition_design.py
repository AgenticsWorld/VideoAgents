"""过场设计(transition design,2026-09-24;方案 docs/transition_design.md,WORKFLOW.md §9C 二期)。

把「组边界」当一等对象:每集一张过场设计表 directing/<ep>/transition_design.json(逐边界:变化诊断 + 设计 + 候选 + 状态 + 反馈),
shot_list.generation_groups[].transition_in 仍是唯一定稿字段,只由本模块 apply() 从设计表投影写回(json 读写保真,不经 agent JS 整写)。

设置:settings.json#transitions {mode: minimal|classic|cinematic|custom, …};非 custom 模式的四个附属项(说明性字卡 / 插入预算 / 生成式 /
新场景首镜定场)由 MODES 表派生、页面不显示、文件里不写;集级覆盖 assets/group_settings/<ep>/episode.json#transitions_mode。
存量项目 settings.json 无 transitions 段 = minimal(现状不变);新建向导默认 classic。

契约常量与机检在 code/check_generation_groups.py(transition_ok);渲染与成片机检在 code/render_transitions.py(plan → build → render → check)。
集尾收束(2026-09-25):settings.json#transitions.episode_close {type: fade_black|fade_white|cut_black|cut_white|hard_cut, duration_s, hold_s, hold_audio}(所有模式都有,
默认淡出到黑 1.0s + 黑场 0.5s);shot_list 顶层 episode_close 写了就按它(hard_cut = 显式不处理);effective_episode_close() 给出本集生效值,
render_transitions.py 在成片末尾施加画面淡出 + 停留,finalize_episode.py 同刻淡出外挂声轨。
本模块提供:effective() 模式展开、diagnose() 边界诊断、propose() 按模式出建议、accept/reject/apply、check()、payload()(分镜预览页过场卡数据)、
字卡 PNG 与全景视窗渲染(build 与页面预览共用)。
二期工位(2026-09-26,07-directing/transition-design,workflow.yaml p6-transition-design,条件 transition_design_enabled = 模式 ≠ minimal):
propose 之后由工位逐边界复核 / 用 set_design() 换主设计(仍 proposed,不接受)、card 改字;用户在分镜预览页过场卡裁决,
**H3A 签字即接受剩余 proposed**(accept_all_proposed,同 H3W 待决项先例)。生效模式允许生成式过场(allow_generative)时定场空镜按 i2v 出设计:
首帧静帧由 prepare_clip_stills() 渲(全景锚点视窗 / 母图),clip 由 Phase 7 p7-transition-clips(08-video-gen/video-generation)图生视频到
assets/transitions/epNN/<B-id>.establishing.mp4(clips_needed / check_clips = 机检 transition_clips_ready),render_transitions build 只消费文件。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "code") not in sys.path:
    sys.path.insert(0, str(ROOT / "code"))

SCHEMA = "transition_design.v1"
DESIGN_FILE = "transition_design.json"
MODES = ("minimal", "classic", "cinematic", "custom")
# 三个预设模式的固定附属值(§3.3 已拍板):说明性字卡 / 插入预算 % / 生成式过场 / 新场景首镜定场
MODE_TABLE = {
    "minimal":   {"allow_cards": False, "insert_budget_pct": 0.0,  "allow_generative": False, "establishing_first_shot": False},
    "classic":   {"allow_cards": True,  "insert_budget_pct": 8.0,  "allow_generative": False, "establishing_first_shot": False},
    "cinematic": {"allow_cards": True,  "insert_budget_pct": 10.0, "allow_generative": True,  "establishing_first_shot": True},
}
CUSTOM_DEFAULTS = {"allow_cards": True, "insert_budget_pct": 8.0, "allow_generative": False, "establishing_first_shot": False}
# 自定义模式六类边界 → 处理方式(custom_map 的键与可选值)
BOUNDARY_CLASSES = ("scene_change", "time_jump", "block_enter", "block_exit", "same_scene", "episode_open")
CUSTOM_OPTIONS = ("director", "hard_cut", "dissolve", "dip_black", "dip_white", "title_card", "overlay_card",
                  "establishing", "establishing_overlay", "timelapse", "bridge",
                  "motion_pair", "j_cut", "l_cut")     # 三期 成对运镜 / 四期 声桥 j_cut 声先入 · l_cut 声延续(2026-10-03 改版)
BRIDGE_S = 2.0            # 生成式桥接默认时长(前组尾帧 → 本组首帧的形变过渡)
# 四期声桥(2026-10-03 改版,modules/sound_bridge.py):transition_in.sound_bridge {kind j|l, s, carry bed|line};原 audio_lead_s(整轨提前)作废
SOUND_BRIDGE_S = 0.5       # 声桥默认时长(settings.transitions.sound_bridge_s 覆盖)
SOUND_BRIDGE_RANGE = (0.1, 1.5)
SOUND_BRIDGE_CARRIES = ("bed", "line")
AUDIO_LEAD_S = SOUND_BRIDGE_S   # 旧名(2026-09-26 四期),保留给外部引用
MOTION_DEFAULT = ("pan_right", "pan_right", "medium")
CUSTOM_MAP_DEFAULT = {"scene_change": "establishing", "time_jump": "title_card", "block_enter": "dip_white",
                      "block_exit": "director", "same_scene": "hard_cut", "episode_open": "director"}
DEFAULT_SETTINGS = {"mode": "minimal", "card_style": "caption_default", "card_language": "script"}
EPISODE_CLOSE_KEYS = ("type", "duration_s", "hold_s", "hold_audio")   # 设置项只存这四键(reason / source 归 shot_list)
CARD_S, ESTAB_S, CARD_JOIN_S = 2.5, 2.5, 0.4
WIDE_SIZES = ("大远景", "远景", "全景", "大全景", "EWS", "WS", "LS", "ELS", "extreme wide", "wide", "establishing")
# 相对时间词(只认「跨日/跨期」关系词;单纯时段词 清晨/午后/黄昏 不算——那些由 time_of_day 派生并打「推定」)
TIME_WORD_RE = re.compile(r"(次日清晨|次日晨|翌日清晨|次日|翌日|当晚|当夜|同日|同一天|数日[前后]|数月[前后]|数年[前后]|多年[前后]|"
                          r"[一二三四五六七八九十两\d]+\s*(?:个)?(?:天|日|月|年|载)[前后])")
TOD_WORD = {"昼": "日间", "日": "日间", "白天": "日间", "清晨": "清晨", "晨": "清晨", "黎明": "黎明", "午后": "午后", "黄昏": "黄昏", "傍晚": "傍晚",
            "夜": "夜", "夜晚": "夜", "深夜": "深夜", "阴天": "阴天", "雨": "雨天"}
DIRECTOR_NOCARD_RE = re.compile(r"(无|不加|没有|禁)[^。;,\n]{0,12}?(字幕板|字卡|说明性字幕)")
SCREENPLAY_HEAD_RE = re.compile(r"^##\s*(S\d+)\s*\|\s*(INT|EXT|内|外)[^|]*\|\s*(SCN-\d+)\s*([^|]*)\|\s*(.+?)\s*$", re.M)


# ---------------------------------------------------------------- settings / mode

def _read(p: Path, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8")) if Path(p).is_file() else default
    except (ValueError, OSError):
        return default


def _write(p: Path, data) -> None:
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)


def normalize_settings(raw: dict | None) -> dict:
    """settings.json#transitions 规范化(服务端校验与保存共用)。非 custom 只保留 mode + 样式项;custom 保留四附属项与 custom_map。"""
    raw = raw if isinstance(raw, dict) else {}
    mode = str(raw.get("mode") or DEFAULT_SETTINGS["mode"])
    if mode not in MODES:
        raise ValueError(f"transitions.mode must be one of {list(MODES)}")
    out = {"mode": mode, "card_style": str(raw.get("card_style") or DEFAULT_SETTINGS["card_style"]),
           "card_language": str(raw.get("card_language") or DEFAULT_SETTINGS["card_language"])}
    # 字卡 / 叠字字体(2026-09-24):项目内相对路径(refs/fonts/xx.ttf)或花字引擎 id(proj:<family>);空 = 自动取 refs/fonts/ 首个
    cf = str(raw.get("card_font") or "").strip()
    if cf:
        if cf.startswith("/") or ".." in Path(cf).parts or (":" in cf and not cf.startswith("proj:")):
            raise ValueError("transitions.card_font must be a path inside the project (e.g. refs/fonts/x.ttf) or a proj:<family> id")
        out["card_font"] = cf
    # 集尾收束(2026-09-25):所有模式都有;缺 = 默认淡出到黑 1.0s + 黑场 0.5s;hard_cut = 不处理(停在末帧,即 2026-09-25 前的行为)
    out["episode_close"] = normalize_close_setting(raw.get("episode_close"))
    # 声桥默认(2026-10-03 四期改版):所有模式都存;极简 / 经典不出声桥候选,电影感 / 自定义按它出 J/L 的时长与承载
    if raw.get("sound_bridge_s") not in (None, ""):
        try:
            sbs = float(raw.get("sound_bridge_s"))
        except (TypeError, ValueError):
            raise ValueError("transitions.sound_bridge_s must be a number") from None
        if not (SOUND_BRIDGE_RANGE[0] - 1e-9 <= sbs <= SOUND_BRIDGE_RANGE[1] + 1e-9):
            raise ValueError(f"transitions.sound_bridge_s must be {SOUND_BRIDGE_RANGE[0]}-{SOUND_BRIDGE_RANGE[1]}")
        out["sound_bridge_s"] = round(sbs, 3)
    else:
        out["sound_bridge_s"] = SOUND_BRIDGE_S
    carry = str(raw.get("sound_bridge_carry") or "bed")
    if carry not in SOUND_BRIDGE_CARRIES:
        raise ValueError(f"transitions.sound_bridge_carry must be one of {list(SOUND_BRIDGE_CARRIES)}")
    out["sound_bridge_carry"] = carry
    if mode == "custom":
        out["allow_cards"] = bool(raw.get("allow_cards", CUSTOM_DEFAULTS["allow_cards"]))
        try:
            pct = float(raw.get("insert_budget_pct", CUSTOM_DEFAULTS["insert_budget_pct"]))
        except (TypeError, ValueError):
            raise ValueError("transitions.insert_budget_pct must be a number") from None
        if not (0 <= pct <= 30):
            raise ValueError("transitions.insert_budget_pct must be 0-30")
        out["insert_budget_pct"] = pct
        out["allow_generative"] = bool(raw.get("allow_generative", CUSTOM_DEFAULTS["allow_generative"]))
        out["establishing_first_shot"] = bool(raw.get("establishing_first_shot", CUSTOM_DEFAULTS["establishing_first_shot"]))
        cm = raw.get("custom_map") if isinstance(raw.get("custom_map"), dict) else {}
        m = dict(CUSTOM_MAP_DEFAULT)
        for k, v in cm.items():
            if k in BOUNDARY_CLASSES:
                if v not in CUSTOM_OPTIONS:
                    raise ValueError(f"transitions.custom_map.{k}={v!r} not in {list(CUSTOM_OPTIONS)}")
                m[k] = v
        out["custom_map"] = m
    return out


def expand(settings: dict | None) -> dict:
    """按模式展开四个附属项(非 custom 由表派生)。"""
    st = normalize_settings(settings)
    mode = st["mode"]
    if mode == "custom":
        return dict(st)
    return {**st, **MODE_TABLE[mode], "custom_map": None}


def normalize_close_setting(raw) -> dict:
    """settings.json#transitions.episode_close 规范化:缺 / 空 = 默认;非法抛 ValueError(服务端 400)。只留 type/duration_s/hold_s/hold_audio。"""
    from check_generation_groups import EPISODE_CLOSE_DEFAULT, check_episode_close, normalize_episode_close  # noqa: E402
    if not isinstance(raw, dict) or not raw:
        return dict(EPISODE_CLOSE_DEFAULT)
    t = normalize_episode_close(raw, source="settings")
    errs = check_episode_close(t, "transitions.episode_close")
    if errs:
        raise ValueError(errs[0].split(": ", 1)[-1])
    if t["type"] == "hard_cut":
        return {"type": "hard_cut"}
    return {k: t[k] for k in EPISODE_CLOSE_KEYS}


def effective_episode_close(base: Path, ep: str | None = None, shot_list: dict | None = None) -> dict | None:
    """本集生效的集尾收束:shot_list 顶层 episode_close 写了就按它(hard_cut → None = 不处理),否则项目设置。
    返回 None 或 {type, duration_s, hold_s, hold_audio, reason?, source}(source = shot_list.episode_close | settings.transitions.episode_close)。"""
    from check_generation_groups import check_episode_close, episode_close_of  # noqa: E402
    base = Path(base)
    if shot_list is None and ep:
        shot_list = _shot_list(base, ep)
    t = episode_close_of(shot_list or {})
    if t is not None:
        if check_episode_close(t):
            return None                     # 契约不合法:transition_ok 会 FAIL,这里不猜
        return None if t["type"] == "hard_cut" else t
    st = effective(base, ep).get("episode_close") or {}
    if not st or st.get("type") == "hard_cut":
        return None
    return {**st, "source": "settings.transitions.episode_close"}


def set_episode_close(base: Path, ep: str, value: dict | None) -> dict | None:
    """写 / 清 shot_list 顶层 episode_close(json 读写保真):None = 删键(跟随项目设置);{type: hard_cut} = 显式不处理;
    fade_* 缺 duration_s/hold_s/hold_audio 时按项目设置补。返回生效值。"""
    from check_generation_groups import check_episode_close, normalize_episode_close  # noqa: E402
    base = Path(base)
    slp = base / "directing" / ep / "shot_list.json"
    sl = _read(slp, None)
    if not isinstance(sl, dict):
        raise FileNotFoundError(f"缺 {slp}")
    if value is None:
        changed = sl.pop("episode_close", None) is not None
    else:
        if not isinstance(value, dict) or not value.get("type"):
            raise ValueError("episode_close must be an object with type")
        from check_generation_groups import EPISODE_CLOSE_CUT_DEFAULT, EPISODE_CLOSE_DEFAULT, EPISODE_CLOSE_FADES  # noqa: E402
        st = effective(base, ep).get("episode_close") or {}
        # 只在同一族(淡出类 ↔ 淡出类 / 切黑类 ↔ 切黑类)内继承项目设置的时长/停留/声音;跨族按该族默认(切黑 1.0s/mute,淡出 1.0s/0.5s/fade)
        is_fade = value.get("type") in EPISODE_CLOSE_FADES
        same_family = (st.get("type") in EPISODE_CLOSE_FADES) == is_fade and st.get("type") not in (None, "hard_cut")
        family_default = EPISODE_CLOSE_DEFAULT if is_fade else EPISODE_CLOSE_CUT_DEFAULT
        merged = {**({k: v for k, v in st.items() if k in EPISODE_CLOSE_KEYS} if same_family else family_default), **value}
        t = normalize_episode_close(merged, source="user")
        errs = check_episode_close(t)
        if errs:
            raise ValueError(errs[0].split(": ", 1)[-1])
        new = {k: v for k, v in t.items() if k in EPISODE_CLOSE_KEYS or k in ("reason", "source")}
        changed = sl.get("episode_close") != new
        sl["episode_close"] = new
    if changed:
        sl.setdefault("_meta", {})["episode_close_set_at"] = dt.datetime.now().isoformat(timespec="seconds")
        _write(slp, sl)
    return effective_episode_close(base, ep, sl)


def project_settings(base: Path) -> dict:
    st = _read(Path(base) / "settings.json", {}) or {}
    return st.get("transitions") if isinstance(st.get("transitions"), dict) else {}


def episode_mode_override(base: Path, ep: str) -> str | None:
    d = _read(Path(base) / "assets" / "group_settings" / ep / "episode.json", {}) or {}
    m = d.get("transitions_mode")
    return m if m in MODES else None


def effective(base: Path, ep: str | None = None) -> dict:
    """本集生效的过场设置:项目 settings.json#transitions(无 = minimal)+ 集级 transitions_mode 覆盖(只换模式,附属项随模式派生;
    集级切到 custom 时沿用项目 custom 附属项)。返回含 mode / mode_source / project_mode。"""
    base = Path(base)
    proj = project_settings(base)
    try:
        proj_eff = expand(proj)
    except ValueError:
        proj_eff = expand(None)
    out = dict(proj_eff)
    out["project_mode"] = proj_eff["mode"]
    out["mode_source"] = "project"
    if ep:
        ov = episode_mode_override(base, ep)
        if ov and ov != proj_eff["mode"]:
            if ov == "custom":
                out = {**expand({**proj, "mode": "custom"}), "project_mode": proj_eff["mode"]}
            else:
                out = {**proj_eff, "mode": ov, **MODE_TABLE[ov], "custom_map": None, "project_mode": proj_eff["mode"]}
            out["mode_source"] = "episode"
    # 声画分离模式(2026-10-03):声桥 carry=line 的前提;设置页读 settings.json#output.sound_split(缺省 off)
    st = _read(base / "settings.json", {}) or {}
    ss = (st.get("output") or {}).get("sound_split") if isinstance(st.get("output"), dict) else None
    out["sound_split"] = ss if ss in ("off", "script_only", "auto") else "off"
    return out


def set_episode_mode(base: Path, ep: str, mode: str | None) -> dict:
    """写 / 清集级模式覆盖(assets/group_settings/<ep>/episode.json#transitions_mode;None 或与项目同 = 删键)。"""
    p = Path(base) / "assets" / "group_settings" / ep / "episode.json"
    d = _read(p, {}) or {}
    if mode is not None and mode not in MODES:
        raise ValueError(f"mode must be one of {list(MODES)}")
    proj_mode = effective(base)["mode"]
    if mode is None or mode == proj_mode:
        d.pop("transitions_mode", None)
    else:
        d["transitions_mode"] = mode
    if d:
        _write(p, d)
    elif p.is_file():
        p.unlink()
    return effective(base, ep)


# ---------------------------------------------------------------- data access

def design_path(base: Path, ep: str) -> Path:
    return Path(base) / "directing" / ep / DESIGN_FILE


def load_design(base: Path, ep: str) -> dict | None:
    d = _read(design_path(base, ep))
    return d if isinstance(d, dict) and isinstance(d.get("boundaries"), list) else None


def _shot_list(base: Path, ep: str) -> dict:
    return _read(Path(base) / "directing" / ep / "shot_list.json", {}) or {}


def _scenes_index(base: Path) -> dict:
    d = _read(Path(base) / "bible" / "scenes" / "index.json", {}) or {}
    rows = d.get("scenes") if isinstance(d, dict) else d
    out = {}
    for r in rows or []:
        if isinstance(r, dict) and r.get("id"):
            out[r["id"]] = r
    return out


def scene_display_name(base: Path, sid: str, scenes: dict | None = None) -> str:
    scenes = scenes if scenes is not None else _scenes_index(base)
    r = scenes.get(sid) or {}
    name = str(r.get("name") or sid)
    return name


def _screenplay(base: Path, ep: str) -> dict:
    """场次头与场尾「转场:」行:{scene_no: {header, int_ext, scene_id, name, tod, transition_out(本场末的转场句), transition_in(上一场末的转场句)}}"""
    p = Path(base) / "story" / "episodes" / ep / "screenplay.md"
    try:
        text = p.read_text(encoding="utf-8") if p.is_file() else ""
    except OSError:
        text = ""
    heads = list(SCREENPLAY_HEAD_RE.finditer(text))
    out, prev_out = {}, None
    for i, m in enumerate(heads):
        seg = text[m.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        tr = None
        for line in seg.splitlines():
            if line.strip().startswith("转场"):
                tr = line.strip()
        out[m.group(1)] = {"header": m.group(0).strip(), "int_ext": m.group(2), "scene_id": m.group(3),
                           "name": m.group(4).strip(), "tod": m.group(5).strip(), "transition_out": tr,
                           "transition_in": prev_out}
        prev_out = tr
    return out


def _director_notes(base: Path, ep: str) -> list[str]:
    p = Path(base) / "directing" / ep / "directing_plan.md"
    try:
        text = p.read_text(encoding="utf-8") if p.is_file() else ""
    except OSError:
        return []
    notes = []
    for line in text.splitlines():
        if DIRECTOR_NOCARD_RE.search(line) and len(line) < 400:
            notes.append(line.strip().lstrip("> ").strip())
    # 去重保序,最多 3 条
    seen, out = set(), []
    for n in notes:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out[:3]


def _continuity(base: Path, ep: str) -> dict:
    cp = _read(Path(base) / "directing" / ep / "continuity_plan.json", {}) or {}
    return {(x.get("from_group"), x.get("to_group")): x for x in (cp.get("group_transitions") or []) if isinstance(x, dict)}


def _shot_plates(base: Path, ep: str) -> dict:
    d = _read(Path(base) / "directing" / ep / "shot_plates.json", {}) or {}
    return d.get("shots") if isinstance(d.get("shots"), dict) else {}


def _panos_index(base: Path, sid: str) -> dict:
    return _read(Path(base) / "assets" / "concepts" / "scenes" / sid / "panos" / "index.json", {}) or {}


# ---------------------------------------------------------------- diagnosis

def _block(g: dict) -> dict:
    nb = g.get("narrative_block")
    return nb if isinstance(nb, dict) and nb.get("id") else {}


def _last_shot(g: dict, shots: dict) -> dict:
    ids = [x for x in (g.get("shots") or []) if isinstance(x, str)]
    return shots.get(ids[-1]) if ids else {}


def _line_placement(ln) -> str:
    try:
        from check_generation_groups import line_placement  # noqa: E402
        return line_placement(ln)
    except Exception:
        v = str((ln or {}).get("placement") or "").strip().lower() if isinstance(ln, dict) else ""
        return v if v in ("on", "os", "vo") else "on"


def _has_onscreen_dialogue(shot: dict | None) -> bool:
    return any(isinstance(ln, dict) and str(ln.get("text") or ln.get("line") or "").strip() and _line_placement(ln) == "on"
               for ln in ((shot or {}).get("dialogue_lines") or []))


def _offscreen_line_heard(g: dict, shots: dict, target: str | None) -> bool:
    """组内是否有 heard_in 含 target 镜的画外句(声画分离一期)——声桥 carry=line 的前提。"""
    if not target:
        return False
    for sid in g.get("shots") or []:
        for ln in ((shots.get(sid) or {}).get("dialogue_lines") or []):
            if isinstance(ln, dict) and str(ln.get("text") or ln.get("line") or "").strip() and _line_placement(ln) != "on":
                heard = ln.get("heard_in") if isinstance(ln.get("heard_in"), list) and ln.get("heard_in") else [sid]
                if target in heard:
                    return True
    return False


def _first_shot(g: dict, shots: dict) -> dict:
    sid = (g.get("shots") or [None])[0]
    return shots.get(sid) or {}


def _is_wide(shot: dict) -> bool:
    size = str(shot.get("size") or shot.get("shot_size") or "")
    return any(w.lower() in size.lower() for w in WIDE_SIZES)


def establishing_source(base: Path, ep: str, g: dict, shots: dict, plates: dict | None = None) -> dict | None:
    """本组场景可用的定场素材:① 全景(优先服务本组首镜的锚点 + 本组光照方案)→ pano_sweep;② 首镜起点母图 → plate_kenburns;③ None。"""
    sid, scheme = g.get("scene_id"), g.get("lighting_scheme_id")
    if not sid:
        return None
    first = (g.get("shots") or [None])[0]
    idx = _panos_index(base, sid)
    anchors = [a for a in (idx.get("anchors") or []) if isinstance(a, dict) and a.get("anchor_id")]
    pdir = Path(base) / "assets" / "concepts" / "scenes" / sid / "panos"

    def has(a, sch):
        p = (a.get("panos") or {}).get(sch) or {}
        return bool(p.get("file")) and (pdir / a["anchor_id"] / p["file"]).is_file()

    pick, pick_scheme = None, None
    for want_serve in (True, False):
        for a in anchors:
            if want_serve and not any(str(s).startswith(f"{ep}/{first}:") for s in (a.get("serves") or [])):
                continue
            if scheme and has(a, scheme):
                pick, pick_scheme = a, scheme
                break
        if pick:
            break
    if not pick:
        for a in anchors:
            for sch in (a.get("panos") or {}):
                if has(a, sch):
                    pick, pick_scheme = a, sch
                    break
            if pick:
                break
    if pick:
        cam = None
        plates = plates if plates is not None else _shot_plates(base, ep)
        sp = plates.get(first) or {}
        for pl in (sp.get("plates") or []):
            if isinstance(pl, dict) and isinstance(pl.get("camera"), dict):
                cam = pl["camera"]
                break
        src = {"scene_id": sid, "mode": "pano_sweep", "anchor_id": pick["anchor_id"], "scheme": pick_scheme,
               "scheme_match": pick_scheme == scheme, "sweep_deg": 16.0, "fov_v_deg": 45.0}
        if cam and isinstance(cam.get("position"), list) and isinstance(cam.get("target"), list):
            src["look_dir"] = [round(float(cam["target"][i]) - float(cam["position"][i]), 4) for i in range(3)]
        return src
    plates = plates if plates is not None else _shot_plates(base, ep)
    sp = plates.get(first) or {}
    for pl in (sp.get("plates") or []):
        if isinstance(pl, dict) and pl.get("file") and (Path(base) / pl["file"]).is_file():
            return {"scene_id": sid, "mode": "plate_kenburns", "file": pl["file"], "scheme": sp.get("lighting_scheme_id") or scheme,
                    "scheme_match": True, "zoom": 1.08}
    return None


def timelapse_source(base: Path, g: dict) -> dict | None:
    """同锚点两个光照方案(时光流转)。"""
    sid = g.get("scene_id")
    if not sid:
        return None
    idx = _panos_index(base, sid)
    pdir = Path(base) / "assets" / "concepts" / "scenes" / sid / "panos"
    for a in idx.get("anchors") or []:
        schemes = [s for s, p in (a.get("panos") or {}).items() if p.get("file") and (pdir / a["anchor_id"] / p["file"]).is_file()]
        if len(schemes) >= 2:
            want = g.get("lighting_scheme_id")
            to = want if want in schemes else schemes[-1]
            frm = next(s for s in schemes if s != to)
            return {"scene_id": sid, "anchor_id": a["anchor_id"], "scheme_from": frm, "scheme_to": to, "fov_v_deg": 45.0}
    return None


def boundary_id(a: str, b: str) -> str:
    return f"B-{a}-{b}"


def diagnose(base: Path, ep: str) -> list[dict]:
    """逐边界诊断(不写文件):变化标签 + 剧本转场句 + 导演声明 + continuity 判定 + 字卡文字候选 + 定场素材可用性。"""
    base = Path(base)
    sl = _shot_list(base, ep)
    groups = [g for g in (sl.get("generation_groups") or []) if isinstance(g, dict) and g.get("group_id")]
    shots = {s.get("shot_id"): s for s in (sl.get("shots") or []) if isinstance(s, dict)}
    scenes = _scenes_index(base)
    sp = _screenplay(base, ep)
    dnotes = _director_notes(base, ep)
    cont = _continuity(base, ep)
    plates = _shot_plates(base, ep)
    from check_generation_groups import transition_of  # noqa: E402  (code/ 已入 sys.path)
    out = []
    for i in range(1, len(groups)):
        a, b = groups[i - 1], groups[i]
        fa, fb = _first_shot(a, shots), _first_shot(b, shots)
        ba, bb = _block(a), _block(b)
        sc = a.get("scene_id") != b.get("scene_id")
        tj = (a.get("time_of_day") or "") != (b.get("time_of_day") or "")
        cast = set(a.get("characters_union") or []) != set(b.get("characters_union") or [])
        light = (a.get("lighting_scheme_id") or "") != (b.get("lighting_scheme_id") or "")
        edge = None
        if bb and not ba:
            edge = "enter"
        elif ba and not bb:
            edge = "exit"
        elif ba and bb and ba.get("id") != bb.get("id"):
            edge = "enter"
        elif ba and bb:
            edge = "inside"
        spb = sp.get(str(b.get("scene_no") or fb.get("scene_no") or "")) or {}
        spa = sp.get(str(a.get("scene_no") or fa.get("scene_no") or "")) or {}
        hint = spa.get("transition_out") if (sc or (spa.get("transition_out") and a.get("scene_no") != b.get("scene_no"))) else None
        ct = cont.get((a["group_id"], b["group_id"])) or {}
        # 相对时间词:剧本场次头 > continuity 说明 > 无(派生只给时段词)
        time_word, time_src = None, None
        # 关系词只从「进入本组」的边界文本找:剧本场次头 > 上一场末转场句 > continuity 该边界说明;只在真跳时段 / 换场景时采纳
        if tj or sc:
            for text, srcname in ((spb.get("tod") or "", "screenplay"), (spb.get("header") or "", "screenplay"),
                                  (hint or "", "screenplay"),
                                  (" ".join(str(ct.get(k) or "") for k in ("anchor_basis", "notes")), "continuity")):
                m = TIME_WORD_RE.search(text)
                if m:
                    time_word, time_src = m.group(1), srcname
                    break
        if not time_word and tj:
            tod = str(b.get("time_of_day") or "").strip()
            time_word, time_src = (TOD_WORD.get(tod, tod) or None), "derived"
        loc = scene_display_name(base, b.get("scene_id") or "", scenes) if b.get("scene_id") else None
        lines, srcs = [], []
        if time_word:
            lines.append(time_word)
            srcs.append(time_src)
        if loc:
            lines.append(loc.replace("·", " · "))
            srcs.append("scenes_index")
        cls = ("block_enter" if edge == "enter" else "block_exit" if edge == "exit" else
               "time_jump" if tj else "scene_change" if sc else "same_scene")
        estab = establishing_source(base, ep, b, shots, plates)
        tl = timelapse_source(base, b) if (tj and not sc) else None
        out.append({
            "id": boundary_id(a["group_id"], b["group_id"]), "from_group": a["group_id"], "to_group": b["group_id"],
            "index": i, "boundary_shots": [(a.get("shots") or [None])[-1], (b.get("shots") or [None])[0]],
            "diagnosis": {
                "class": cls,
                "scene_change": [a.get("scene_id"), b.get("scene_id")] if sc else None,
                "scene_names": [scene_display_name(base, a.get("scene_id") or "", scenes), loc] if sc else None,
                "time_jump": [a.get("time_of_day"), b.get("time_of_day")] if tj else None,
                "time_word": time_word, "time_word_source": time_src,
                "cast_change": cast, "light_jump": light,
                "narrative_block_edge": edge, "narrative_block": (bb or ba) or None,
                "screenplay_hint": hint, "screenplay_header": spb.get("header"),
                "director_notes": dnotes,
                "continuity": {k: ct.get(k) for k in ("id", "anchor", "boundary_type", "cast_change", "framing_change_ok")} if ct else None,
                "first_shot_wide": _is_wide(fb), "first_shot_size": fb.get("size"),
                "has_change": bool(sc or tj or cast or light or edge in ("enter", "exit")),
                # 四期声桥(2026-10-03):切点两侧的台词事实——L 候选条件(前组末镜无画内对白 + 下组首镜定场 / 无对白)与 carry=line 前提
                "last_shot_has_dialogue": _has_onscreen_dialogue(_last_shot(a, shots)),
                "first_shot_has_dialogue": _has_onscreen_dialogue(fb),
                "last_shot_offscreen_line": _offscreen_line_heard(a, shots, (a.get("shots") or [None])[-1]),
                "first_shot_offscreen_line": _offscreen_line_heard(b, shots, (b.get("shots") or [None])[0]),
            },
            "card_lines": lines, "card_sources": srcs,
            "establishing": estab, "timelapse": tl,
            # 二期(2026-09-26):生成式定场空镜(i2v)的素材口径——场景名 / 时段 / 内外景,供 i2v_source 组提示词与首帧静帧
            "ep": ep,
            "scene": {"id": b.get("scene_id"), "name": loc, "time_of_day": b.get("time_of_day"),
                      "int_ext": spb.get("int_ext"), "lighting_scheme_id": b.get("lighting_scheme_id")},
            # 三期(2026-09-26):桥接提示词要知道「从哪来」
            "scene_from": {"id": a.get("scene_id"), "name": scene_display_name(base, a.get("scene_id") or "", scenes) if a.get("scene_id") else None,
                           "time_of_day": a.get("time_of_day"), "int_ext": spa.get("int_ext")},
            "current": transition_of(b) if b.get("transition_in") else {"type": "hard_cut"},
            "current_raw": b.get("transition_in") if isinstance(b.get("transition_in"), dict) else None,
            "to_duration_s": b.get("total_duration_s"), "from_duration_s": a.get("total_duration_s"),
        })
    return out


# ---------------------------------------------------------------- proposals

def _card_insert(lines, srcs, bg="black", dur=CARD_S, audio="mute", join_out="dip_black"):
    return {"kind": "title_card", "duration_s": dur, "card": {"lines": list(lines), "sources": list(srcs), "bg": bg, "fade_s": 0.4},
            "join_out": join_out, "join_out_s": CARD_JOIN_S, "audio": audio}


def _estab_insert(src, dur=ESTAB_S, overlay=None, join_out="hard_cut"):
    x = {"kind": "establishing", "duration_s": dur, "source": dict(src), "join_out": join_out, "audio": "mute"}
    if join_out != "hard_cut":
        x["join_out_s"] = CARD_JOIN_S
    if overlay:
        x["overlay_card"] = overlay
    return x


def _overlay(lines, srcs, dur=CARD_S, position="bottom_left"):
    return {"lines": list(lines), "sources": list(srcs), "duration_s": dur, "position": position}


# ---------------------------------------------------------------- 生成式定场空镜(i2v,二期 2026-09-26)
#
# 「换场景 → 定场空镜 + 叠地点字幕」在生效模式允许生成式过场(allow_generative:电影感 / 自定义勾选)时,定场空镜不再只是全景横摇 /
# 母图推进的 ffmpeg 合成,而是一段**由视频模型图生视频**的空镜 clip:首帧 = 宿主从该场景全景锚点(或首镜母图)渲出的静帧,提示词 = 场景名 /
# 时段 / 内外景 + 无人物 + 缓慢运镜。设计仍在 Phase 6 定(本模块 propose / 工位 07-directing/transition-design),clip 的**生成放在 Phase 7**
# (workflow.yaml p7-transition-clips → 08-video-gen/video-generation,H3A 签字后、H3B 前),Phase 9 render_transitions build 只消费文件:
#   assets/transitions/epNN/<B-id>.establishing.mp4     生成的空镜 clip(build 按插入段时长裁到帧数,缺失 = build 记 missing、check FAIL)
#   assets/transitions/epNN/<B-id>.establishing.still.jpg 首帧静帧(宿主 clips --prepare 渲出;图生视频的 --first-frame)
CLIP_DIR = "assets/transitions"
I2V_MIN_DURATION_S = 4.0     # 图生视频请求时长下限(多数模型最短 4s;build 只取插入段所需帧数,多余裁掉)


def clip_paths(ep: str, bid: str, kind: str = "establishing") -> dict:
    """生成式插入段的约定路径(项目根相对):establishing → <B-id>.establishing.{mp4,still.jpg};
    bridge → <B-id>.bridge.mp4 + 首尾帧 <B-id>.bridge.first.jpg(前组尾帧)/ <B-id>.bridge.last.jpg(本组首帧)。"""
    stem = f"{CLIP_DIR}/{ep}/{bid}.{kind}"
    out = {"file": f"{stem}.mp4"}
    if kind == "establishing":
        out["still"] = f"{stem}.still.jpg"
    elif kind == "bridge":
        out["first"], out["last"] = f"{stem}.first.jpg", f"{stem}.last.jpg"
    return out


def bridge_prompt(scene_from: dict, scene_to: dict, block: dict | None) -> str:
    """生成式桥接 i2v(首尾帧)提示词:从前组尾帧连续形变 / 流动到本组首帧;按叙事块种类给记忆 / 梦境 / 想象的质感;无新增人物、无文字。"""
    kind = str((block or {}).get("kind") or "")
    a = str((scene_from or {}).get("name") or (scene_from or {}).get("id") or "")
    b = str((scene_to or {}).get("name") or (scene_to or {}).get("id") or "")
    zh = _has_cjk(a) or _has_cjk(b)
    feel_zh = {"flashback": "记忆浮现:轻微光晕与褪色,像被回忆卷入", "dream": "梦境:朦胧、失焦、缓慢漂浮",
               "imagination": "想象:画面如水面般泛起再重组", "montage": "时间流逝:光影快速掠过"}.get(kind, "同一空间在光影中连续转换")
    feel_en = {"flashback": "memory surfacing: a soft halo and fading colour, as if pulled into recollection", "dream": "dream: hazy, defocused, slowly drifting",
               "imagination": "imagination: the image ripples like water and reassembles", "montage": "passing time: light and shadow sweep quickly"}.get(kind, "one space transforming continuously through light")
    if zh:
        return (f"过渡桥接:画面从首帧({a})连续形变、流动到尾帧({b}),{feel_zh};中段不出现任何新的人物、面孔或文字,"
                "只让首帧已有的形体与光线渐变为尾帧的形体与光线;不切镜、不闪白、不黑场;结尾稳定停在尾帧构图。")
    return (f"Transition bridge: the image morphs and flows continuously from the first frame ({a}) into the last frame ({b}); {feel_en}; "
            "no new people, faces or text appear mid-way — only the shapes and light already present in the first frame gradually become those of the last frame; "
            "no cuts, no white flash, no black; ends settled on the last frame's composition.")


def bridge_insert(ep: str, b: dict, dur: float = BRIDGE_S, audio: str = "sustain") -> dict:
    """生成式桥接插入段:文件 / 首尾帧按约定路径,提示词由宿主生成。"""
    paths = clip_paths(ep, b["id"], "bridge")
    return {"kind": "bridge", "duration_s": dur, "join_out": "hard_cut", "audio": audio,
            "file": paths["file"], "first_frame": paths["first"], "last_frame": paths["last"],
            "prompt": bridge_prompt(b.get("scene_from") or {}, b.get("scene") or {}, (b.get("diagnosis") or {}).get("narrative_block"))}


def i2v_source(ep: str, b: dict, estab: dict) -> dict:
    """把 establishing_source 给出的全景 / 母图素材包成 i2v 源:保留原素材(base)供渲首帧,加 file / still 约定路径与提示词。"""
    if not estab or estab.get("mode") == "i2v":
        return estab
    sc = b.get("scene") or {}
    paths = clip_paths(ep, b["id"], "establishing")
    src = {"scene_id": estab.get("scene_id") or sc.get("id"), "mode": "i2v", "file": paths["file"], "still": paths["still"],
           "base": {k: v for k, v in estab.items() if k not in ("scene_id",)},
           "scheme": estab.get("scheme"), "scheme_match": estab.get("scheme_match", True),
           "prompt": clip_prompt(sc, estab), "camera": "slow_push_in"}
    return src


def _has_cjk(s: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in str(s or ""))


def clip_prompt(scene: dict, estab: dict | None = None) -> str:
    """定场空镜 i2v 提示词(语言跟场景名:中文场景名出中文,其余英文;工位可按场景圣经描述改写,但须保留「无人物」「无文字」「不转场」三句)。"""
    name = str((scene or {}).get("name") or (scene or {}).get("id") or "")
    tod = str((scene or {}).get("time_of_day") or "").strip()
    ie = str((scene or {}).get("int_ext") or "").upper()
    ie_zh = {"INT": "内景", "EXT": "外景", "内": "内景", "外": "外景"}.get(ie, "")
    ie_en = {"INT": "interior", "EXT": "exterior", "内": "interior", "外": "exterior"}.get(ie, "")
    if _has_cjk(name) or _has_cjk(tod):
        bits = [f"定场空镜:{name}" + (f"({ie_zh})" if ie_zh else ""), f"时段:{tod}" if tod else "",
                "画面严格延续首帧的场景与光线,镜头极缓慢推进,环境细节轻微动态(光影、尘埃、风)",
                "全程无人物、无动物、无生物入画;无字幕、无文字、无水印;不切镜、不做任何转场;结尾保持静止环境"]
        return ";".join(x for x in bits if x) + "。"
    bits = [f"Establishing shot, empty {ie_en or 'location'}: {name}", f"time of day: {tod}" if tod else "",
            "the scene and lighting strictly continue from the first frame; the camera pushes in very slowly with subtle ambient motion (light, dust, wind)",
            "no people, no animals, no creatures in frame at any time; no captions, no text, no watermark; no cuts, no transitions; ends on a still environment"]
    return "; ".join(x for x in bits if x) + "."


def _reason(d: dict, what: str) -> str:
    bits = []
    if d.get("scene_names"):
        bits.append(f"{d['scene_names'][0]} → {d['scene_names'][1]}")
    if d.get("time_jump"):
        bits.append(f"{d['time_jump'][0]} → {d['time_jump'][1]}")
    if d.get("narrative_block_edge") in ("enter", "exit"):
        nb = d.get("narrative_block") or {}
        bits.append(("进入" if d["narrative_block_edge"] == "enter" else "离开") + f"叙事块 {nb.get('id') or ''}({nb.get('kind') or ''})")
    if d.get("cast_change"):
        bits.append("阵容变化")
    if d.get("light_jump"):
        bits.append("光线跳变")
    return f"过场设计:{what};诊断 " + ("、".join(bits) or "无显著变化") + (f";剧本 {d['screenplay_hint']}" if d.get("screenplay_hint") else "")


def _intent(d: dict) -> str:
    e = d.get("narrative_block_edge")
    kind = ((d.get("narrative_block") or {}).get("kind") or "")
    if e == "enter":
        return "dream_in" if kind == "dream" else "montage" if kind == "montage" else "flashback_in"
    if e == "exit":
        return "dream_out" if kind == "dream" else "flashback_out"
    if d.get("time_jump"):
        return "time_skip"
    if d.get("scene_change"):
        return "scene_change"
    return "other"


def _designs_for(b: dict, eff: dict) -> tuple[dict | None, list[dict]]:
    """按生效模式给边界出主设计 + 候选;返回 (design_transition_in | None, alternatives[])。None = 不建议改(保持现状)。
    生效模式允许生成式过场(allow_generative)时,定场空镜按 i2v(视频模型图生视频,Phase 7 p7-transition-clips 出 clip)出设计;
    否则全景横摇 / 母图推进(ffmpeg 合成,不花视频生成费)。"""
    d = b["diagnosis"]
    mode = eff["mode"]
    allow_cards = bool(eff.get("allow_cards"))
    lines, srcs = b.get("card_lines") or [], b.get("card_sources") or []
    have_lines = bool(lines)
    estab, tl = b.get("establishing"), b.get("timelapse")
    if estab and eff.get("allow_generative") and b.get("ep"):
        estab = i2v_source(b["ep"], b, estab)
    cur = b.get("current") or {"type": "hard_cut"}
    intent = _intent(d)
    alts: list[dict] = []

    def keep_hard():
        return {"type": "hard_cut", "intent": intent if intent != "other" else "other", "reason": _reason(d, "保持硬切"), "source": "transition_design"}

    def card_only():
        return {"type": "fade_black", "duration_s": 0.6, "intent": intent, "reason": _reason(d, "字卡"), "source": "transition_design",
                "inserts": [_card_insert(lines, srcs)]}

    def overlay_only():
        return {"type": cur.get("type", "hard_cut") if cur.get("type") in ("hard_cut", "dissolve", "dip_white", "dip_black") else "hard_cut",
                **({"duration_s": cur["duration_s"]} if cur.get("type") != "hard_cut" and cur.get("duration_s") else {}),
                "intent": intent, "reason": _reason(d, "叠地点/时间字幕"), "source": "transition_design",
                "overlay_card": _overlay(lines, srcs)}

    def estab_only(overlay=False):
        return {"type": "hard_cut", "intent": intent, "reason": _reason(d, "定场空镜" + ("+叠字" if overlay else "")), "source": "transition_design",
                "inserts": [_estab_insert(estab, overlay=_overlay(lines, srcs) if (overlay and have_lines and allow_cards) else None)]}

    def card_plus_estab():
        return {"type": "fade_black", "duration_s": 0.6, "intent": intent, "reason": _reason(d, "字卡+定场"), "source": "transition_design",
                "inserts": [_card_insert(lines, srcs, join_out="dissolve"), _estab_insert(estab)]}

    def timelapse_only():
        return {"type": "dissolve", "duration_s": 0.6, "intent": intent, "reason": _reason(d, "时光流转"), "source": "transition_design",
                "inserts": [{"kind": "timelapse", "duration_s": 3.0, "source": dict(tl), "join_out": "dissolve", "join_out_s": 0.6, "audio": "mute"}]}

    def block_in(with_overlay=True):
        kind = ((d.get("narrative_block") or {}).get("kind") or "flashback")
        t = {"type": "dip_white" if kind != "dream" else "dissolve", "duration_s": 1.0 if kind != "dream" else 0.8, "freeze_s": 0.3,
             "intent": intent, "reason": _reason(d, "进叙事块:白场 + 定格" + ("+叠字" if with_overlay else "")), "source": "transition_design"}
        if kind == "dream":
            t["join"] = {"style": "blur_through"}
        if with_overlay and have_lines and allow_cards:
            t["overlay_card"] = _overlay(lines, srcs, dur=2.5)
        return t

    def block_out():
        if cur.get("type") not in (None, "hard_cut") and cur.get("source") != "transition_design":
            return None   # 导演已设计出口(match_cut 等)
        return {"type": "hard_cut", "intent": intent, "reason": _reason(d, "出叙事块有意硬切"), "source": "transition_design"}

    # —— 三期 / 四期(2026-09-26) ——
    generative = bool(eff.get("allow_generative")) and bool(b.get("ep"))

    def bridge_design(with_overlay=True):
        """生成式桥接:前组尾帧 → 本组首帧的形变过渡(clip 由 Phase 7 p7-transition-clips 出);进块可叠字。"""
        t = {"type": "hard_cut", "intent": intent, "reason": _reason(d, "生成式桥接" + ("+叠字" if with_overlay else "")), "source": "transition_design",
             "inserts": [bridge_insert(b["ep"], b)]}
        if with_overlay and have_lines and allow_cards:
            t["overlay_card"] = _overlay(lines, srcs, dur=2.5)
        return t

    sb_s = float(eff.get("sound_bridge_s") or SOUND_BRIDGE_S)
    sb_carry_pref = str(eff.get("sound_bridge_carry") or "bed")
    sound_split_on = str(eff.get("sound_split") or "off") != "off"

    def _carry(kind: str) -> str:
        """承载:项目默认 line 且声画分离开着且切点旁有画外句(J 看下组首镜,L 看前组末镜)才 line,否则 bed。"""
        if sb_carry_pref == "line" and sound_split_on:
            if (kind == "j" and d.get("first_shot_offscreen_line")) or (kind == "l" and d.get("last_shot_offscreen_line")):
                return "line"
        return "bed"

    def sound_bridge(kind: str, base_design=None):
        """声桥:在无插入段 / 无黑场的 hard_cut / dissolve 设计上加 sound_bridge {kind, s, carry}(2026-10-03 改版,替代 audio_lead_s)。"""
        t = dict(base_design or keep_hard())
        if t.get("type") not in ("hard_cut", "dissolve") or t.get("inserts") or t.get("hold_s"):
            return None
        t.pop("audio_lead_s", None)
        carry = _carry(kind)
        t["sound_bridge"] = {"kind": kind, "s": sb_s, "carry": carry}
        what = "声先入(J-cut:下组声音先到,压在本组尾画面上)" if kind == "j" else "声延续(L-cut:本组声音拖过切点,压在下组首画面上)"
        t["reason"] = str(t.get("reason") or _reason(d, what)).rstrip("。") + f";声桥 {kind.upper()} {sb_s:g}s " + \
            ("画外台词跨切点" if carry == "line" else "底床" + ("预滚" if kind == "j" else "延续")) + ("(J-cut)" if kind == "j" else "(L-cut)")
        return t

    def j_cut(base_design=None):
        return sound_bridge("j", base_design)

    def l_cut(base_design=None):
        return sound_bridge("l", base_design)

    def l_cut_fits() -> bool:
        """L 候选条件:前组末镜无画内对白(有可延续的环境 / 音乐感收尾)且下组首镜是定场 / 远景或无对白。"""
        return (not d.get("last_shot_has_dialogue")) and (bool(d.get("first_shot_wide")) or not d.get("first_shot_has_dialogue"))

    def motion_pair_design(with_overlay=True, lead=True):
        """成对运镜:前组尾镜 out 运镜带出、本组首镜 in 运镜接入(写进两侧 prompt,sync_motion_pairs);默认再配声先入。"""
        out, inn, speed = MOTION_DEFAULT
        t = {"type": "hard_cut", "intent": intent, "reason": _reason(d, f"成对运镜 {out}→{inn}"), "source": "transition_design",
             "motion_pair": {"out": out, "in": inn, "speed": speed}}
        if with_overlay and have_lines and allow_cards:
            t["overlay_card"] = _overlay(lines, srcs)
        if lead:
            t["sound_bridge"] = {"kind": "j", "s": sb_s, "carry": _carry("j")}
            t["reason"] += f";声桥 J {sb_s:g}s"
        return t

    cls = d["class"]
    if mode == "minimal":
        return None, []
    if mode == "custom":
        opt = (eff.get("custom_map") or CUSTOM_MAP_DEFAULT).get(cls, "director")
        table = {
            "director": lambda: None, "hard_cut": keep_hard,
            "dissolve": lambda: {"type": "dissolve", "duration_s": 0.6, "intent": intent, "reason": _reason(d, "叠化"), "source": "transition_design"},
            "dip_black": lambda: {"type": "dip_black", "duration_s": 0.8, "intent": intent, "reason": _reason(d, "黑场"), "source": "transition_design"},
            "dip_white": lambda: {"type": "dip_white", "duration_s": 0.8, "intent": intent, "reason": _reason(d, "白场"), "source": "transition_design"},
            "title_card": lambda: card_only() if (have_lines and allow_cards) else keep_hard(),
            "overlay_card": lambda: overlay_only() if (have_lines and allow_cards) else keep_hard(),
            "establishing": lambda: estab_only() if estab else (overlay_only() if (have_lines and allow_cards) else keep_hard()),
            "establishing_overlay": lambda: estab_only(True) if estab else (overlay_only() if (have_lines and allow_cards) else keep_hard()),
            "timelapse": lambda: timelapse_only() if tl else (card_only() if (have_lines and allow_cards) else keep_hard()),
            # 三期:生成式桥接只在勾选「生成式过场」时可出;否则退硬切(候选里仍不给 bridge,机检 generative_allowed 会拦)
            "bridge": lambda: bridge_design(True) if generative else keep_hard(),
            "motion_pair": lambda: motion_pair_design(True, lead=True),
            "j_cut": lambda: j_cut(),   # 四期:硬切 + 声先入(J)
            "l_cut": lambda: l_cut(),   # 四期(2026-10-03):硬切 + 声延续(L)
        }
        design = table.get(opt, lambda: None)()
        if design is not None:
            alts.append({"label": "保持硬切", "transition_in": keep_hard()})
        return design, alts
    # classic / cinematic
    design = None
    if cls == "block_enter":
        if generative:
            # 电影感(允许生成式):闪回 / 梦境进块用生成式桥接,白场 + 定格退作候选
            design = bridge_design(True)
            alts.append({"label": "白场 + 定格(不生成)", "transition_in": block_in(True)})
        else:
            design = block_in(True)
        alts.append({"label": "只白场不叠字", "transition_in": block_in(False)})
        if have_lines and allow_cards:
            alts.append({"label": "白底字卡", "transition_in": {**card_only(), "type": "fade_white", "inserts": [_card_insert(lines, srcs, bg="white", join_out="dip_white")]}})
    elif cls == "block_exit":
        design = block_out()
        if design is None:
            return None, []
        if have_lines and allow_cards:
            alts.append({"label": "叠「当下」地点字幕", "transition_in": overlay_only()})
        if generative:
            alts.append({"label": "生成式桥接回到当下", "transition_in": bridge_design(False)})
        jc = j_cut(design) if mode == "cinematic" else None
        if jc:
            alts.append({"label": "硬切 + 声先入(J-cut)", "transition_in": jc})
    elif cls == "time_jump":
        if have_lines and allow_cards:
            design = card_plus_estab() if (mode == "cinematic" and estab and not d.get("first_shot_wide")) else card_only()
            alts.append({"label": "只叠字幕(不变长)", "transition_in": overlay_only()})
            if tl:
                alts.append({"label": "时光流转", "transition_in": timelapse_only()})
            if estab and design.get("inserts") and len(design["inserts"]) == 1:
                alts.append({"label": "字卡+定场", "transition_in": card_plus_estab()})
        elif tl:
            design = timelapse_only()
        elif estab:
            design = estab_only(False)
        else:
            design = {"type": "dip_black", "duration_s": 0.8, "intent": intent, "reason": _reason(d, "黑场(无字卡/定场素材)"), "source": "transition_design"}
        if mode == "cinematic":
            alts.append({"label": "成对运镜 + 声先入", "transition_in": motion_pair_design(have_lines and allow_cards, lead=True)})
            if l_cut_fits():
                alts.append({"label": "硬切 + 声延续(L-cut)", "transition_in": l_cut()})
    elif cls == "scene_change":
        overlay_ok = have_lines and allow_cards
        if estab and not d.get("first_shot_wide"):
            design = estab_only(mode == "cinematic" and overlay_ok)
            if overlay_ok:
                # 四期:不变长的叠字方案在电影感下默认带音先入(J-cut)
                ov = overlay_only()
                alts.append({"label": "只叠地点字幕(不变长)" + (" + 声先入" if mode == "cinematic" else ""), "transition_in": (j_cut(ov) if mode == "cinematic" else None) or ov})
                alts.append({"label": "定场+叠字" if mode != "cinematic" else "只定场", "transition_in": estab_only(mode != "cinematic")})
        elif overlay_ok:
            design = overlay_only()
            if mode == "cinematic":
                design = j_cut(design) or design
            if estab:
                alts.append({"label": "定场空镜(首镜已是远景,通常不必)", "transition_in": estab_only(False)})
        elif estab:
            design = estab_only(False)
        else:
            design = {"type": "dissolve", "duration_s": 0.5, "intent": intent, "reason": _reason(d, "叠化(无字卡/定场素材)"), "source": "transition_design"}
            if mode == "cinematic":
                design = j_cut(design) or design
        if mode == "cinematic":
            # 三期:成对运镜(前组尾镜横摇带出、本组首镜同向横摇接入)作候选;方向由工位按两侧构图改(design --design)
            alts.append({"label": "成对运镜 + 声先入", "transition_in": motion_pair_design(overlay_ok, lead=True)})
            # 四期(2026-10-03):前组尾无画内对白 + 下组首镜定场 / 无对白 → 声延续候选
            if l_cut_fits():
                alts.append({"label": "硬切 + 声延续(L-cut)", "transition_in": l_cut()})
    else:   # same_scene
        if not d.get("has_change"):
            return None, []
        if cur.get("type") not in (None, "hard_cut"):
            return None, []
        design = None   # 同场景默认不动;给候选
        alts.append({"label": "定格 0.2s 再硬切", "transition_in": {"type": "hard_cut", "freeze_s": 0.2, "intent": "other", "reason": _reason(d, "定格软化硬切"), "source": "transition_design"}})
        alts.append({"label": "叠化 0.5s", "transition_in": {"type": "dissolve", "duration_s": 0.5, "intent": "other", "reason": _reason(d, "叠化"), "source": "transition_design"}})
        return None, alts
    if design is not None:
        alts.append({"label": "保持硬切", "transition_in": keep_hard()})
    return design, alts


def propose(base: Path, ep: str, *, force: bool = False) -> dict:
    """按生效模式重出全集建议,写 transition_design.json。已 accepted / rejected 的边界保留裁决(force=True 时也重出但保留 feedback);
    shot_list 里非本模块来源的非硬切设计(导演清单)记为 status=accepted, source=shot_list。"""
    base = Path(base)
    eff = effective(base, ep)
    old = load_design(base, ep) or {}
    old_by = {b.get("id"): b for b in (old.get("boundaries") or []) if isinstance(b, dict)}
    rows = []
    for b in diagnose(base, ep):
        prev = old_by.get(b["id"]) or {}
        design, alts = _designs_for(b, eff)
        cur = b.get("current") or {"type": "hard_cut"}
        cur_raw = b.get("current_raw") or {}
        entry = {**b, "alternatives": alts, "feedback": prev.get("feedback") or [],
                 "prev_transition_in": prev.get("prev_transition_in", cur_raw if cur_raw.get("source") != "transition_design" else prev.get("prev_transition_in"))}
        if cur_raw and cur_raw.get("source") not in ("transition_design",) and (cur.get("type") != "hard_cut" or cur_raw.get("reason")):
            # 导演清单 / 后期页写入的现有设计:视为已定稿,建议只进候选
            entry["design"] = dict(cur_raw)
            entry["status"] = "accepted"
            entry["source"] = "post_plan" if cur_raw.get("source") == "post_plan" else "shot_list"
            if design is not None:
                entry["alternatives"] = [{"label": f"过场模式建议({eff['mode']})", "transition_in": design}] + alts
        elif prev.get("status") in ("accepted", "rejected") and not force:
            entry["design"] = prev.get("design")
            entry["status"] = prev["status"]
            entry["source"] = prev.get("source") or "user"
            if design is not None and not any(a.get("transition_in") == design for a in entry["alternatives"]):
                entry["alternatives"] = [{"label": f"过场模式建议({eff['mode']})", "transition_in": design}] + entry["alternatives"]
        elif prev.get("status") == "proposed" and prev.get("source") == "agent" and isinstance(prev.get("design"), dict) and not force:
            # 二期工位(07-directing/transition-design)定过的建议:重出建议不覆盖,模式默认建议只进候选
            entry["design"] = prev["design"]
            entry["status"] = "proposed"
            entry["source"] = "agent"
            for k in ("agent_note", "designed_by", "designed_at"):
                if prev.get(k):
                    entry[k] = prev[k]
            if design is not None and design != prev["design"] and not any(a.get("transition_in") == design for a in entry["alternatives"]):
                entry["alternatives"] = [{"label": f"过场模式建议({eff['mode']})", "transition_in": design}] + entry["alternatives"]
        elif design is None:
            entry["design"] = None
            entry["status"] = "none"
            entry["source"] = "mode"
        else:
            entry["design"] = design
            entry["status"] = "proposed"
            entry["source"] = "mode"
        rows.append(entry)
    data = {"schema": SCHEMA, "episode": ep, "mode": eff["mode"], "mode_source": eff["mode_source"],
            "settings": {k: eff.get(k) for k in ("allow_cards", "insert_budget_pct", "allow_generative", "establishing_first_shot",
                                                 "sound_bridge_s", "sound_bridge_carry")},
            "written_at": dt.datetime.now().isoformat(timespec="seconds"), "boundaries": rows}
    _write(design_path(base, ep), data)
    return data


def _find(data: dict, bid: str) -> dict:
    for b in data.get("boundaries") or []:
        if b.get("id") == bid:
            return b
    raise KeyError(f"边界 {bid} 不在设计表")


def accept(base: Path, ep: str, bid: str, *, alt: int | None = None, transition_in: dict | None = None, by: str = "user") -> dict:
    """接受主设计 / 第 alt 个候选 / 用户给的 transition_in,写回 shot_list。"""
    data = load_design(base, ep)
    if not data:
        data = propose(base, ep)
    b = _find(data, bid)
    if transition_in is not None:
        design = dict(transition_in)
        design.setdefault("source", "transition_design")
        b["source"] = "user"
    elif alt is not None:
        alts = b.get("alternatives") or []
        if not (0 <= alt < len(alts)):
            raise ValueError(f"候选序号 {alt} 越界(共 {len(alts)})")
        design = dict(alts[alt]["transition_in"])
        b["source"] = "user"
    else:
        if not b.get("design"):
            raise ValueError("该边界没有主设计可接受")
        design = dict(b["design"])
        b["source"] = b.get("source") if b.get("source") in ("shot_list", "post_plan") else "user"
    design.setdefault("source", "transition_design")
    b["design"] = design
    b["status"] = "accepted"
    b["decided_at"] = dt.datetime.now().isoformat(timespec="seconds")
    b["decided_by"] = by
    _write(design_path(base, ep), data)
    apply(base, ep)
    return _find(load_design(base, ep), bid)


def reject(base: Path, ep: str, bid: str, *, by: str = "user", note: str = "") -> dict:
    """裁定保持硬切(写显式 hard_cut + reason;若原 shot_list 有导演设计则恢复原设计)。"""
    data = load_design(base, ep) or propose(base, ep)
    b = _find(data, bid)
    prev = b.get("prev_transition_in")
    if isinstance(prev, dict) and prev and prev.get("source") != "transition_design":
        b["design"] = dict(prev)
    else:
        b["design"] = {"type": "hard_cut", "intent": "other", "reason": ("用户裁定保持硬切" + (":" + note if note else "")), "source": "transition_design"}
    b["status"] = "rejected"
    b["source"] = "user"
    b["decided_at"] = dt.datetime.now().isoformat(timespec="seconds")
    b["decided_by"] = by
    _write(design_path(base, ep), data)
    apply(base, ep)
    return _find(load_design(base, ep), bid)


def add_feedback(base: Path, ep: str, bid: str, text: str, by: str = "user") -> dict:
    data = load_design(base, ep) or propose(base, ep)
    b = _find(data, bid)
    b.setdefault("feedback", []).append({"at": dt.datetime.now().isoformat(timespec="seconds"), "by": by, "text": text, "resolved": False})
    _write(design_path(base, ep), data)
    return b


def set_design(base: Path, ep: str, bid: str, *, alt: int | None = None, transition_in: dict | None = None,
               by: str = "07-directing/transition-design", note: str = "") -> dict:
    """二期工位(07-directing/transition-design)改主设计:第 alt 个候选 / 给定 transition_in 升为主设计,状态仍是 proposed
    (**不接受**——接受归用户在分镜预览页过场卡裁决,或 H3A 签字即接受);原主设计退入候选首位。
    已由用户 / 导演清单裁决(accepted / rejected / source=shot_list|post_plan)的边界拒改(ValueError),工位只能提反馈。"""
    data = load_design(base, ep) or propose(base, ep)
    b = _find(data, bid)
    if b.get("status") in ("accepted", "rejected") or b.get("source") in ("shot_list", "post_plan"):
        raise ValueError(f"{bid} 已裁决({b.get('status')} / {b.get('source')}),工位不得改主设计;有异议用 feedback")
    alts = b.get("alternatives") or []
    if transition_in is not None:
        if not isinstance(transition_in, dict) or not transition_in.get("type"):
            raise ValueError("transition_in must be an object with type")
        design = dict(transition_in)
    elif alt is not None:
        if not (0 <= alt < len(alts)):
            raise ValueError(f"候选序号 {alt} 越界(共 {len(alts)})")
        design = dict(alts[alt]["transition_in"])
        alts = [a for i, a in enumerate(alts) if i != alt]
    else:
        raise ValueError("须给 --alt 或 --design")
    design.setdefault("source", "transition_design")
    if note:
        design["reason"] = (str(design.get("reason") or "过场设计").rstrip("。;") + f";工位:{note}")
    old = b.get("design")
    if isinstance(old, dict) and old != design and not any(a.get("transition_in") == old for a in alts):
        alts = [{"label": "原建议", "transition_in": old}] + alts
    b["design"], b["alternatives"] = design, alts
    b["status"], b["source"] = "proposed", "agent"
    b["designed_by"], b["designed_at"] = by, dt.datetime.now().isoformat(timespec="seconds")
    if note:
        b["agent_note"] = note
    _write(design_path(base, ep), data)
    return b


def accept_all_proposed(base: Path, ep: str, *, by: str = "sign:g6") -> list[str]:
    """把仍是 proposed 的边界全部接受并 apply(H3A 签字即接受默认设计,同 H3W 待决项先例)。返回接受的边界 id。"""
    data = load_design(base, ep)
    if not data:
        return []
    done = []
    now = dt.datetime.now().isoformat(timespec="seconds")
    for b in data["boundaries"]:
        if b.get("status") == "proposed" and isinstance(b.get("design"), dict):
            b["design"].setdefault("source", "transition_design")
            b["status"], b["decided_at"], b["decided_by"] = "accepted", now, by
            b["source"] = b.get("source") if b.get("source") == "agent" else "mode"
            done.append(b["id"])
    if done:
        _write(design_path(base, ep), data)
        apply(base, ep)
    return done


def enabled(base: Path, ep: str | None = None) -> bool:
    """本集是否启用过场设计工位(workflow.yaml 条件 transition_design_enabled):生效过场模式 ≠ minimal。"""
    return effective(base, ep)["mode"] != "minimal"


# ---------------------------------------------------------------- 生成式插入段 clip(Phase 7 p7-transition-clips)

def _frame_size(base: Path) -> tuple[int, int]:
    """首帧静帧尺寸:按项目输出画幅(settings.json#output.aspect_preset/aspect_custom,缺省 16:9)。"""
    from modules.output_format import resolve_output  # noqa: PLC0415
    asp = resolve_output(_read(Path(base) / "settings.json", {}) or {})[0]
    return {"9:16": (1080, 1920), "1:1": (1440, 1440), "4:3": (1600, 1200), "3:4": (1200, 1600), "21:9": (2520, 1080)}.get(asp, (1920, 1080))


def _clip_rows_from(base: Path, ep: str, t: dict, bid: str, to_group: str, status: str) -> list[dict]:
    """一个 transition_in 里需要外部 clip 的插入段(establishing i2v / bridge)→ 行。"""
    rows = []
    for k, x in enumerate(t.get("inserts") or []):
        kind = x.get("kind")
        src = x.get("source") if isinstance(x.get("source"), dict) else {}
        if kind == "establishing" and src.get("mode") == "i2v":
            paths = clip_paths(ep, bid, "establishing")
            rows.append({"id": bid, "to_group": to_group, "insert_index": k, "kind": "establishing", "status": status,
                         "file": src.get("file") or paths["file"], "still": src.get("still") or paths["still"],
                         "prompt": src.get("prompt") or "", "scene_id": src.get("scene_id"), "base": src.get("base") or {},
                         "duration_s": float(x.get("duration_s") or 0.0), "request_duration_s": max(I2V_MIN_DURATION_S, math.ceil(float(x.get("duration_s") or 0.0)))})
        elif kind == "bridge":
            paths = clip_paths(ep, bid, "bridge")
            rows.append({"id": bid, "to_group": to_group, "insert_index": k, "kind": "bridge", "status": status,
                         "file": x.get("file") or paths["file"], "still": x.get("first_frame") or paths["first"],
                         "first_frame": x.get("first_frame") or paths["first"], "last_frame": x.get("last_frame") or paths["last"],
                         "prompt": x.get("prompt") or "",
                         "duration_s": float(x.get("duration_s") or 0.0), "request_duration_s": max(I2V_MIN_DURATION_S, math.ceil(float(x.get("duration_s") or 0.0)))})
    return rows


def clips_needed(base: Path, ep: str, *, include_proposed: bool = False) -> list[dict]:
    """本集需要视频生成的过场素材:shot_list 已定稿(accepted)设计里的 establishing(i2v)/ bridge 插入段;
    include_proposed=True 时另含设计表 proposed 的(供工位提前渲首帧)。每行带 exists / still_exists / duration_ok(有 ffprobe 时)。"""
    from check_generation_groups import transition_of  # noqa: E402
    base = Path(base)
    sl = _shot_list(base, ep)
    rows, seen = [], set()
    for g in sl.get("generation_groups") or []:
        if not isinstance(g, dict) or not isinstance(g.get("transition_in"), dict):
            continue
        t = transition_of(g)
        gid = g.get("group_id")
        # 边界 id 由设计表反查(缺表时按组序推前组)
        prev_gid = None
        groups = [x.get("group_id") for x in sl.get("generation_groups") or []]
        if gid in groups and groups.index(gid) > 0:
            prev_gid = groups[groups.index(gid) - 1]
        if not prev_gid:
            continue
        bid = boundary_id(prev_gid, gid)
        for r in _clip_rows_from(base, ep, t, bid, gid, "accepted"):
            rows.append(r)
            seen.add((r["id"], r["kind"]))
    if include_proposed:
        data = load_design(base, ep)
        for b in (data or {}).get("boundaries") or []:
            if b.get("status") == "proposed" and isinstance(b.get("design"), dict):
                for r in _clip_rows_from(base, ep, transition_of({"transition_in": b["design"]}), b["id"], b["to_group"], "proposed"):
                    if (r["id"], r["kind"]) not in seen:
                        rows.append(r)
                        seen.add((r["id"], r["kind"]))
    for r in rows:
        f = base / r["file"]
        r["exists"] = f.is_file()
        r["still_exists"] = bool(r.get("still")) and (base / r["still"]).is_file()
        if r["kind"] == "bridge":
            r["last_frame_exists"] = bool(r.get("last_frame")) and (base / r["last_frame"]).is_file()
            r["still_exists"] = r["still_exists"] and r["last_frame_exists"]
        r["clip_duration_s"] = _probe_duration(f) if r["exists"] else None
        r["duration_ok"] = (r["clip_duration_s"] is None) or (r["clip_duration_s"] + 1e-3 >= r["duration_s"])
    return rows


def _extract_frame(src: Path, out: Path, *, last: bool = False) -> bool:
    """用 ffmpeg 抽首帧 / 末帧为 jpg;失败返回 False。"""
    import shutil
    import subprocess
    if not shutil.which("ffmpeg") or not Path(src).is_file():
        return False
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-v", "error"] + (["-sseof", "-0.08"] if last else []) + ["-i", str(src), "-frames:v", "1", "-q:v", "2", str(out)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
    except Exception:
        return False
    if last and not out.is_file():       # 极短 clip 时 -sseof 可能落空,退回按时长定位
        d = _probe_duration(Path(src)) or 0.0
        try:
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{max(0.0, d - 0.05):.3f}", "-i", str(src), "-frames:v", "1", "-q:v", "2", str(out)],
                           check=True, capture_output=True, timeout=120)
        except Exception:
            return False
    return out.is_file()


def _probe_duration(p: Path) -> float | None:
    import shutil
    import subprocess
    if not shutil.which("ffprobe"):
        return None
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "format=duration",
                              "-of", "default=nw=1:nk=1", str(p)], capture_output=True, text=True, timeout=60, check=True).stdout.strip()
        return float(out) if out else None
    except Exception:
        return None


def prepare_clip_stills(base: Path, ep: str, rows: list[dict] | None = None, *, force: bool = False) -> list[dict]:
    """为 establishing(i2v)行渲首帧静帧(全景锚点视窗 pano_view / 首镜母图缩放),写到 still 路径;返回处理过的行。"""
    base = Path(base)
    rows = rows if rows is not None else clips_needed(base, ep, include_proposed=True)
    w, h = _frame_size(base)
    done = []
    for r in rows:
        if r.get("kind") == "bridge":
            # 三期:桥接首尾帧 = 前组尾帧(assets/clips/<from>.last_frame.png 或从前组 clip 抽末帧)+ 本组 clip 首帧;两侧组视频未出即无法准备
            from_gid = r["id"].split("-")[1] if r["id"].count("-") >= 2 else None
            to_gid = r.get("to_group")
            first_out, last_out = base / r["first_frame"], base / r["last_frame"]
            ok_first = first_out.is_file() and not force
            if not ok_first:
                lf = base / "assets" / "clips" / ep / f"{from_gid}.last_frame.png"
                if lf.is_file():
                    from PIL import Image
                    first_out.parent.mkdir(parents=True, exist_ok=True)
                    Image.open(lf).convert("RGB").save(first_out, quality=93)
                    ok_first = True
                else:
                    ok_first = _extract_frame(base / "assets" / "clips" / ep / f"{from_gid}.mp4", first_out, last=True)
            ok_last = (last_out.is_file() and not force) or _extract_frame(base / "assets" / "clips" / ep / f"{to_gid}.mp4", last_out, last=False)
            r["still_exists"] = r["last_frame_exists"] = ok_first and ok_last
            if not (ok_first and ok_last):
                r["still_error"] = f"桥接首尾帧缺:前组 {from_gid} / 本组 {to_gid} 的组视频尚未生成(p7-video 后再 --prepare)"
                continue
            done.append(r)
            continue
        if r.get("kind") != "establishing" or not r.get("still"):
            continue
        out = base / r["still"]
        if out.is_file() and not force:
            r["still_exists"] = True
            continue
        b = r.get("base") or {}
        out.parent.mkdir(parents=True, exist_ok=True)
        if b.get("mode") == "pano_sweep" and b.get("anchor_id"):
            pano_still(base, {"scene_id": r.get("scene_id"), "anchor_id": b["anchor_id"], "scheme": b.get("scheme"),
                              "look_dir": b.get("look_dir"), "fov_v_deg": b.get("fov_v_deg") or 45.0}, w, h, out)
        elif b.get("file") and (base / b["file"]).is_file():
            from PIL import Image
            im = Image.open(base / b["file"]).convert("RGB")
            # 按目标画幅居中裁切再缩放(母图 55° 广角比例通常已接近输出画幅)
            sw, sh = im.size
            tr = w / h
            if sw / sh > tr:
                nw = int(round(sh * tr))
                im = im.crop(((sw - nw) // 2, 0, (sw - nw) // 2 + nw, sh))
            else:
                nh = int(round(sw / tr))
                im = im.crop((0, (sh - nh) // 2, sw, (sh - nh) // 2 + nh))
            im.resize((w, h), Image.LANCZOS).save(out, quality=93)
        else:
            r["still_error"] = "定场素材缺失(无全景锚点 / 母图),无法渲首帧"
            continue
        r["still_exists"] = out.is_file()
        done.append(r)
    return done


def check_clips(base: Path, ep: str) -> tuple[bool, list[dict]]:
    """transition_clips_ready(p7-transition-clips 验收):本集 shot_list 已定稿的 establishing(i2v)/ bridge 插入段,
    clip 文件在、时长 ≥ 插入段时长、首帧静帧在;一条都不需要 = PASS 并注明。"""
    items, fails = [], 0

    def rec(name, ok, detail="", warn=False):
        nonlocal fails
        tag = "PASS" if ok else ("WARN" if warn else "FAIL")
        if tag == "FAIL":
            fails += 1
        items.append({"check": name, "result": tag, "detail": detail})

    rows = clips_needed(base, ep)
    if not rows:
        rec("transition_clips_ready", True, "本集定稿设计无生成式插入段(无需 clip)")
        return True, items
    missing = [f"{r['id']}.{r['kind']}" for r in rows if not r["exists"]]
    short = [f"{r['id']}.{r['kind']}({r['clip_duration_s']:.2f}s<{r['duration_s']:g}s)" for r in rows if r["exists"] and not r["duration_ok"]]
    no_still = [f"{r['id']}.{r['kind']}" for r in rows if not r["still_exists"]]
    rec("transition_clips_ready", not missing and not short,
        f"需 {len(rows)} 段;缺文件 {missing[:6]}" if missing else (f"需 {len(rows)} 段;时长不足 {short[:4]}" if short else f"{len(rows)} 段 clip 齐全"))
    rec("transition_clip_stills", not no_still, ("缺首帧 / 桥接首尾帧静帧 " + ", ".join(no_still[:6]) + "(跑 clips --prepare;桥接须两侧组视频已出)") if no_still else "首帧 / 首尾帧静帧齐全", warn=True)
    return fails == 0, items


def set_card_lines(base: Path, ep: str, bid: str, lines: list[str]) -> dict:
    """用户直接改字卡 / 叠字幕文字(source 改 user),已接受的同步写回 shot_list。"""
    data = load_design(base, ep) or propose(base, ep)
    b = _find(data, bid)
    lines = [str(x).strip() for x in lines if str(x).strip()][:3]
    b["card_lines"], b["card_sources"] = lines, ["user"] * len(lines)

    def patch(t):
        if not isinstance(t, dict):
            return
        if isinstance(t.get("overlay_card"), dict):
            t["overlay_card"]["lines"], t["overlay_card"]["sources"] = list(lines), ["user"] * len(lines)
        for x in t.get("inserts") or []:
            if x.get("kind") == "title_card":
                x.setdefault("card", {})["lines"] = list(lines)
                x["card"]["sources"] = ["user"] * len(lines)
            if isinstance(x.get("overlay_card"), dict):
                x["overlay_card"]["lines"], x["overlay_card"]["sources"] = list(lines), ["user"] * len(lines)
    patch(b.get("design"))
    for a in b.get("alternatives") or []:
        patch(a.get("transition_in"))
    _write(design_path(base, ep), data)
    if b.get("status") == "accepted":
        apply(base, ep)
    return b


def apply(base: Path, ep: str) -> dict:
    """设计表 → shot_list.transition_in 投影:accepted / rejected 的边界写 design;其余不动。json 读写保真(浮点不塌缩)。
    返回 {written: [gid...], unchanged: n}。"""
    base = Path(base)
    data = load_design(base, ep)
    if not data:
        return {"written": [], "unchanged": 0}
    slp = base / "directing" / ep / "shot_list.json"
    sl = _read(slp, None)
    if not isinstance(sl, dict):
        raise FileNotFoundError(f"缺 {slp}")
    by_to = {b["to_group"]: b for b in data["boundaries"] if b.get("status") in ("accepted", "rejected") and isinstance(b.get("design"), dict)}
    written, changed = [], False
    for g in sl.get("generation_groups") or []:
        b = by_to.get(g.get("group_id"))
        if not b:
            continue
        new = _clean_design(b["design"])
        if g.get("transition_in") != new:
            g["transition_in"] = new
            changed = True
        written.append(g["group_id"])
    if changed:
        sl.setdefault("_meta", {})["transition_design_applied_at"] = dt.datetime.now().isoformat(timespec="seconds")
        _write(slp, sl)
    return {"written": written, "unchanged": len(data["boundaries"]) - len(written), "changed": changed}


def _clean_design(t: dict) -> dict:
    """写回 shot_list 的 transition_in:去掉设计表私有键(sources 保留在 card 内供页面显示「推定」)。"""
    out = {k: v for k, v in t.items() if not str(k).startswith("_") or k == "_post_prev"}
    if out.get("type") == "hard_cut":
        out.pop("duration_s", None)
    return out


# ---------------------------------------------------------------- check

def check(base: Path, ep: str) -> tuple[bool, list[dict]]:
    """transition_design_ok:设计表存在且契约合法(复用 transition_ok 的接缝/插入规则)、有变化的边界都已裁决(proposed=WARN,缺=FAIL,
    minimal 模式降 WARN)、已接受的设计与 shot_list 一致(apply 幂等)。"""
    from check_generation_groups import _check_inserts, _check_join, check_transitions, transition_of  # noqa: E402
    base = Path(base)
    eff = effective(base, ep)
    items, fails = [], 0

    def rec(name, ok, detail="", warn=False):
        nonlocal fails
        tag = "PASS" if ok else ("WARN" if warn else "FAIL")
        if tag == "FAIL":
            fails += 1
        items.append({"check": name, "result": tag, "detail": detail})

    data = load_design(base, ep)
    if not data:
        rec("transition_design_present", eff["mode"] == "minimal", f"缺 {DESIGN_FILE}(过场模式 {eff['mode']};minimal 不要求)", warn=eff["mode"] == "minimal")
        return fails == 0, items
    rec("transition_design_present", True, f"{len(data['boundaries'])} 个边界;模式 {data.get('mode')}({data.get('mode_source')})")
    errs = []
    for b in data["boundaries"]:
        for label, t in ([("design", b.get("design"))] + [(f"alt{i}", a.get("transition_in")) for i, a in enumerate(b.get("alternatives") or [])]):
            if not isinstance(t, dict):
                continue
            tn = transition_of({"transition_in": t})
            errs += [f"{b['id']}/{label}: {e}" for e in _check_join(b["to_group"], tn)]
            e2, _ = _check_inserts(b["to_group"], tn, False)
            errs += [f"{b['id']}/{label}: {e}" for e in e2]
    rec("transition_design_contract", not errs, f"设计/候选契约异常 {len(errs)}" + (f":{errs[:4]}" if errs else ""))
    pend = [b["id"] for b in data["boundaries"] if b.get("status") == "proposed"]
    need = [b["id"] for b in data["boundaries"] if (b.get("diagnosis") or {}).get("class") in ("scene_change", "time_jump", "block_enter", "block_exit")]
    missing = [i for i in need if not any(b["id"] == i and b.get("status") in ("accepted", "rejected", "proposed", "none") for b in data["boundaries"])]
    rec("transition_design_coverage", not pend and not missing,
        f"换场景/跳时间/叙事块边界 {len(need)};待裁决 {len(pend)}" + (f" {pend[:6]}" if pend else "") + (f";缺条目 {missing}" if missing else ""),
        warn=bool(pend) and not missing or eff["mode"] == "minimal")
    sl = _shot_list(base, ep)
    by = {g.get("group_id"): g for g in (sl.get("generation_groups") or []) if isinstance(g, dict)}
    mism = [b["id"] for b in data["boundaries"] if b.get("status") in ("accepted", "rejected") and isinstance(b.get("design"), dict)
            and (by.get(b["to_group"]) or {}).get("transition_in") != _clean_design(b["design"])]
    rec("transition_design_applied", not mism, f"已裁决 {sum(1 for b in data['boundaries'] if b.get('status') in ('accepted', 'rejected'))} 处" + (f";shot_list 未同步 {mism[:4]}(跑 apply)" if mism else ""))
    # 二期(2026-09-26):生成式插入段(定场 i2v / 桥接)只在生效模式允许生成式过场时可出;clip 由 Phase 7 p7-transition-clips 生成,这里只 WARN
    gen = []
    for b in data["boundaries"]:
        if b.get("status") in ("accepted", "proposed") and isinstance(b.get("design"), dict):
            for x in transition_of({"transition_in": b["design"]}).get("inserts") or []:
                if x.get("kind") == "bridge" or (x.get("kind") == "establishing" and (x.get("source") or {}).get("mode") == "i2v"):
                    gen.append(f"{b['id']}.{x.get('kind')}")
    rec("transition_design_generative_allowed", not gen or bool(eff.get("allow_generative")),
        (f"生成式插入段 {len(gen)} 处 {gen[:4]};过场模式 {eff['mode']} " + ("允许" if eff.get("allow_generative") else "**不允许**生成式过场(改模式或换定场方式)")) if gen else "无生成式插入段")
    if gen:
        rows = clips_needed(base, ep)
        miss = [f"{r['id']}.{r['kind']}" for r in rows if not r["exists"]]
        rec("transition_clips_ready", not miss, (f"已定稿 {len(rows)} 段生成式 clip;缺 {miss[:4]}(Phase 7 p7-transition-clips 出片)" if miss else f"{len(rows)} 段 clip 齐全") if rows else "定稿设计尚无生成式插入段", warn=True)
    slerr = check_transitions(sl, eff["insert_budget_pct"]) if sl else []
    slerr = [e for e in slerr if "transition_" in e]
    rec("transition_ok", not slerr, f"shot_list transition_ok:{len(slerr)} 条" + (f" {slerr[:3]}" if slerr else ""))
    return fails == 0, items


# ---------------------------------------------------------------- rendering helpers(build 与页面预览共用)

FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/Hiragino Sans GB.ttc", "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc", "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", "C:/Windows/Fonts/simsun.ttc", "C:/Windows/Fonts/msyh.ttc",
]


FONT_EXTS = (".ttf", ".otf", ".ttc")
PROJECT_FONTS_SUBDIR = "refs/fonts"     # 与 modules/captions.py 同一目录(「参考文件」页「字体」板块上传)


def project_fonts(base: Path) -> list[Path]:
    """项目字体 refs/fonts/(WORKFLOW §2 规则 9:有则全片统一优先),按文件名排序。"""
    d = Path(base) / PROJECT_FONTS_SUBDIR
    return [p for p in sorted(d.glob("*")) if p.is_file() and p.suffix.lower() in FONT_EXTS] if d.is_dir() else []


def resolve_card_font(base: Path) -> Path | None:
    """字卡 / 叠字字体:settings.json#transitions.card_font(项目内相对路径 或 proj:<family> 花字 id)→ 项目 refs/fonts/ 首个 → None(走全局 data/fonts/ 与系统字体)。
    找不到指定字体时回落到 refs/fonts/ 首个而不是静默用系统字体,并由 build 日志提示。"""
    base = Path(base)
    spec = str(project_settings(base).get("card_font") or "").strip()
    if spec.startswith("proj:"):
        try:
            try:
                from modules.captions import scan_project_fonts  # noqa: PLC0415
            except ImportError:
                from captions import scan_project_fonts  # noqa: PLC0415
            for rec in scan_project_fonts(base):
                if rec.get("id") == spec and Path(rec.get("path") or "").is_file():
                    return Path(rec["path"])
        except Exception:
            pass
    elif spec:
        p = base / spec
        if p.is_file() and p.suffix.lower() in FONT_EXTS:
            return p
    pf = project_fonts(base)
    return pf[0] if pf else None


def font_path(prefer_serif: bool = True, font: Path | str | None = None) -> str | None:
    """实际用于渲染的字体文件:指定字体(resolve_card_font)→ 全局 data/fonts/ → 系统候选;None = PIL 默认位图字体。"""
    from PIL import ImageFont
    cands = [str(p) for p in sorted((ROOT / "data" / "fonts").glob("*")) if p.suffix.lower() in FONT_EXTS]
    order = FONT_CANDIDATES if prefer_serif else [f for f in FONT_CANDIDATES if "Songti" not in f and "Serif" not in f]
    for f in ([str(font)] if font else []) + cands + order:
        if Path(f).is_file():
            try:
                ImageFont.truetype(f, 12)
                return f
            except Exception:
                continue
    return None


def find_font(size: int, prefer_serif: bool = True, font: Path | str | None = None):
    from PIL import ImageFont
    f = font_path(prefer_serif, font)
    if f:
        try:
            return ImageFont.truetype(f, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _text_w(draw, text, font):
    l, t, r, b = draw.textbbox((0, 0), text, font=font)
    return r - l, b - t


def render_card_png(lines: list[str], width: int, height: int, out: Path, *, bg: str = "black", color: str | None = None,
                    bg_image: Path | None = None, font: Path | str | None = None) -> dict:
    """字幕卡:黑/白/纯色/前组尾帧模糊底 + 居中 1–3 行(首行大字、余行小字、字间距),返回 {file, text_bbox, contrast, font}。
    font = resolve_card_font(base)(项目字体优先);None 时走全局/系统字体。"""
    from PIL import Image, ImageDraw, ImageFilter
    lines = [str(x) for x in lines if str(x).strip()][:3]
    if bg == "blur_prev" and bg_image and Path(bg_image).is_file():
        im = Image.open(bg_image).convert("RGB").resize((width, height)).filter(ImageFilter.GaussianBlur(max(8, width // 60)))
        dark = Image.new("RGB", (width, height), (0, 0, 0))
        im = Image.blend(im, dark, 0.55)
        fg = (245, 245, 245)
    elif bg == "white":
        im = Image.new("RGB", (width, height), (245, 243, 238))
        fg = (28, 28, 28)
    elif bg == "color":
        im = Image.new("RGB", (width, height), _hex(color, (20, 24, 34)))
        fg = (235, 235, 235)
    else:
        im = Image.new("RGB", (width, height), (0, 0, 0))
        fg = (235, 235, 235)
    draw = ImageDraw.Draw(im)
    sizes = [max(18, height // 12)] + [max(14, height // 20)] * 2
    fonts = [find_font(s, font=font) for s in sizes[:len(lines)]]
    gaps = [int(height * 0.045)] * len(lines)
    heights = []
    for ln, f in zip(lines, fonts):
        heights.append(_text_w(draw, ln, f)[1])
    total = sum(heights) + sum(gaps[:-1]) if lines else 0
    y = (height - total) // 2
    bbox = None
    for i, (ln, f) in enumerate(zip(lines, fonts)):
        spaced = " ".join(ln) if (i == 0 and len(ln) <= 8 and re.search(r"[\u4e00-\u9fff]", ln)) else ln
        w, h = _text_w(draw, spaced, f)
        x = (width - w) // 2
        draw.text((x, y), spaced, font=f, fill=fg)
        bb = (x, y, x + w, y + h)
        bbox = bb if bbox is None else (min(bbox[0], bb[0]), min(bbox[1], bb[1]), max(bbox[2], bb[2]), max(bbox[3], bb[3]))
        y += h + gaps[i]
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out, "PNG")
    lum_bg = _lum(im.getpixel((8, 8)))
    lum_fg = _lum(fg)
    contrast = (max(lum_bg, lum_fg) + 0.05) / (min(lum_bg, lum_fg) + 0.05)
    safe = bbox is not None and bbox[0] >= width * 0.05 and bbox[2] <= width * 0.95 and bbox[1] >= height * 0.05 and bbox[3] <= height * 0.95
    return {"file": str(out), "text_bbox": bbox, "contrast": round(contrast, 2), "in_safe_area": bool(safe), "lines": lines,
            "font": font_path(True, font)}


def render_overlay_png(lines: list[str], width: int, height: int, out: Path, *, position: str = "bottom_left",
                       font: Path | str | None = None) -> dict:
    """叠字幕(透明 RGBA):白字 + 深描边 + 软阴影,位置 bottom_left / bottom_right / center / top_*;font 同 render_card_png。"""
    from PIL import Image, ImageDraw, ImageFilter
    lines = [str(x) for x in lines if str(x).strip()][:3]
    im = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    sizes = [max(16, height // 20)] + [max(13, height // 28)] * 2
    fonts = [find_font(s, prefer_serif=False, font=font) for s in sizes[:len(lines)]]
    draw = ImageDraw.Draw(im)
    dims = [_text_w(draw, ln, f) for ln, f in zip(lines, fonts)]
    gap = int(height * 0.018)
    block_w = max((d[0] for d in dims), default=0)
    block_h = sum(d[1] for d in dims) + gap * max(0, len(lines) - 1)
    mx, my = int(width * 0.06), int(height * 0.08)
    if position == "center":
        x0, y0 = (width - block_w) // 2, (height - block_h) // 2
    else:
        x0 = mx if position.endswith("left") else width - mx - block_w
        y0 = my if position.startswith("top") else height - my - block_h
    shadow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    def _x(w):
        return x0 if position.endswith("left") else (x0 + block_w - w if position.endswith("right") else x0 + (block_w - w) // 2)
    y = y0
    for ln, f, (w, h) in zip(lines, fonts, dims):
        sd.text((_x(w) + 3, y + 3), ln, font=f, fill=(0, 0, 0, 170))
        y += h + gap
    shadow = shadow.filter(ImageFilter.GaussianBlur(4))
    im = Image.alpha_composite(im, shadow)
    draw = ImageDraw.Draw(im)
    y = y0
    for ln, f, (w, h) in zip(lines, fonts, dims):
        draw.text((_x(w), y), ln, font=f, fill=(250, 250, 250, 255), stroke_width=max(1, height // 360), stroke_fill=(10, 10, 10, 220))
        y += h + gap
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out, "PNG")
    return {"file": str(out), "text_bbox": (x0, y0, x0 + block_w, y0 + block_h), "lines": lines, "font": font_path(False, font)}


def _hex(c, default):
    try:
        c = str(c or "").lstrip("#")
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4)) if len(c) == 6 else default
    except ValueError:
        return default


def _lum(rgb):
    def ch(v):
        v = v / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = rgb[:3]
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _pano_load(base: Path, sid: str, anchor_id: str, scheme: str):
    """全景图(RGB ndarray)+ 锚点位置 + 全景 yaw0(弧度;与 scene_panos._sample_pano 同约定)。"""
    import cv2
    import numpy as np
    pdir = Path(base) / "assets" / "concepts" / "scenes" / sid / "panos" / anchor_id
    idx = _panos_index(base, sid)
    anchor = next((a for a in idx.get("anchors") or [] if a.get("anchor_id") == anchor_id), None)
    if not anchor:
        raise FileNotFoundError(f"{sid}/{anchor_id}: 锚点不在 panos/index.json")
    rec = (anchor.get("panos") or {}).get(scheme) or {}
    f = pdir / (rec.get("file") or f"{scheme}.png")
    if not f.is_file():
        raise FileNotFoundError(f"{sid}/{anchor_id}: 缺全景 {f.name}")
    pano = cv2.cvtColor(cv2.imread(str(f), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
    drec = _read(pdir / "depth_pano.json", {}) or {}
    cam = (drec.get("camera") or {})
    yaw0 = math.radians(float(cam.get("yaw_deg", anchor.get("yaw_deg") or 0) or 0))
    pos = np.array(cam.get("position") or anchor.get("position") or [0, 1.6, 0], dtype=np.float64)
    return pano, pos, yaw0, f


def pano_view(base: Path, sid: str, anchor_id: str, scheme: str, look_dir, width: int, height: int, *, fov_v_deg: float = 45.0,
              yaw_offset_deg: float = 0.0, pitch_deg: float = 0.0, _cache: dict | None = None):
    """从锚点位置看 look_dir(世界方向,可绕 y 转 yaw_offset)的透视视窗(RGB ndarray)。纯球面采样(机位 = 锚点,无视差,不需白模深度)。"""
    import cv2
    import numpy as np
    try:
        from modules.scene_panos import _view_rays  # noqa: E402
    except ImportError:
        from scene_panos import _view_rays  # noqa: E402  code/ CLI:_common 已把 modules/ 入 sys.path
    key = (sid, anchor_id, scheme)
    if _cache is not None and key in _cache:
        pano, pos, yaw0, _ = _cache[key]
    else:
        pano, pos, yaw0, _ = _pano_load(base, sid, anchor_id, scheme)
        if _cache is not None:
            _cache[key] = (pano, pos, yaw0, None)
    d = np.array(look_dir if look_dir is not None else [math.sin(yaw0), 0.0, -math.cos(yaw0)], dtype=np.float64)
    d[1] = 0.0
    if np.linalg.norm(d) < 1e-6:
        d = np.array([0.0, 0.0, -1.0])
    d /= np.linalg.norm(d)
    a = math.radians(yaw_offset_deg)
    d = np.array([d[0] * math.cos(a) + d[2] * math.sin(a), 0.0, -d[0] * math.sin(a) + d[2] * math.cos(a)])
    d[1] = math.tan(math.radians(pitch_deg))
    d /= np.linalg.norm(d)
    cam = {"position": pos.tolist(), "target": (pos + d).tolist(), "fov_v_deg": fov_v_deg}
    _, dirs = _view_rays(cam, width, height)
    H, W = pano.shape[:2]
    cy, sy = math.cos(yaw0), math.sin(yaw0)
    lx = cy * dirs[:, 0] - sy * dirs[:, 2]
    lz = sy * dirs[:, 0] + cy * dirs[:, 2]
    ly = dirs[:, 1]
    theta = np.arctan2(lx, -lz)
    phi = np.arccos(np.clip(ly, -1, 1))
    u = ((theta + np.pi) / (2 * np.pi) * W).astype(np.float32).reshape(height, width)
    v = (phi / np.pi * H).astype(np.float32).reshape(height, width)
    img = cv2.remap(pano, u, v, cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)
    return img


def pano_sweep_frames(base: Path, src: dict, width: int, height: int, n: int, out_dir: Path) -> list[Path]:
    """定场空镜帧序列:sweep_deg 度横摇(居中于 look_dir),轻微推进(fov 收 6%)。写 out_dir/f%04d.jpg。"""
    import cv2
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for p in out_dir.glob("f*.jpg"):
        p.unlink()
    sweep = float(src.get("sweep_deg") or 16.0)
    fov = float(src.get("fov_v_deg") or 45.0)
    cache: dict = {}
    files = []
    for i in range(max(1, n)):
        t = i / max(1, n - 1)
        e = t * t * (3 - 2 * t)          # smoothstep
        img = pano_view(base, src["scene_id"], src["anchor_id"], src["scheme"], src.get("look_dir"), width, height,
                        fov_v_deg=fov * (1 - 0.06 * e), yaw_offset_deg=-sweep / 2 + sweep * e, pitch_deg=float(src.get("pitch_deg") or 2.0), _cache=cache)
        f = out_dir / f"f{i:04d}.jpg"
        cv2.imwrite(str(f), cv2.cvtColor(img, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 94])
        files.append(f)
    return files


def pano_still(base: Path, src: dict, width: int, height: int, out: Path, scheme: str | None = None) -> Path:
    import cv2
    img = pano_view(base, src["scene_id"], src["anchor_id"], scheme or src["scheme"], src.get("look_dir"), width, height,
                    fov_v_deg=float(src.get("fov_v_deg") or 45.0), pitch_deg=float(src.get("pitch_deg") or 2.0))
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), cv2.cvtColor(img, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 92])
    return out


def _has_text_render(t: dict) -> bool:
    """该边界是否要渲染文字(字卡 / 叠字):决定字体是否进指纹。"""
    if isinstance(t.get("overlay_card"), dict):
        return True
    return any(isinstance(x, dict) and (x.get("kind") == "title_card" or isinstance(x.get("overlay_card"), dict))
               for x in (t.get("inserts") or []))


def source_fingerprint(base: Path, t: dict) -> str:
    """设计 + 素材指纹(素材文件 size/mtime)+ 字卡/叠字所用项目字体(文件名/size/mtime):任一变化 = 已渲染段过期。
    无项目字体(走全局/系统字体)时字体不进指纹,存量指纹不变。"""
    h = hashlib.sha256(json.dumps(t, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    if _has_text_render(t):
        f = resolve_card_font(base)
        if f is not None:
            try:
                st = f.stat()
                h.update(f"font:{f.name}:{st.st_size}:{int(st.st_mtime)}".encode())
            except OSError:
                h.update(f"font:{f.name}:missing".encode())
    for x in (t.get("inserts") or []):
        s = x.get("source") or {}
        paths = []
        if x.get("kind") == "establishing" and s.get("mode") == "pano_sweep":
            paths.append(Path(base) / "assets" / "concepts" / "scenes" / str(s.get("scene_id")) / "panos" / str(s.get("anchor_id")) / f"{s.get('scheme')}.png")
        elif x.get("kind") == "establishing" and s.get("file"):
            paths.append(Path(base) / s["file"])
        elif x.get("kind") == "timelapse":
            for sch in (s.get("scheme_from"), s.get("scheme_to")):
                paths.append(Path(base) / "assets" / "concepts" / "scenes" / str(s.get("scene_id")) / "panos" / str(s.get("anchor_id")) / f"{sch}.png")
        elif x.get("kind") == "bridge" and x.get("file"):
            paths.append(Path(base) / x["file"])
        for p in paths:
            try:
                st = p.stat()
                h.update(f"{p.name}:{st.st_size}:{int(st.st_mtime)}".encode())
            except OSError:
                h.update(f"{p.name}:missing".encode())
    return h.hexdigest()[:16]


def close_fingerprint(t: dict | None) -> str:
    """集尾收束指纹:只看 type / duration_s / hold_s / hold_audio(来源与 reason 不影响渲染)。"""
    if not t:
        return ""
    return source_fingerprint(Path("."), {k: t.get(k) for k in EPISODE_CLOSE_KEYS})


# ---------------------------------------------------------------- page payload

def payload(base: Path, ep: str) -> dict:
    """分镜预览页「过场卡」数据:设计表(无则即时诊断、status=undecided)+ 当前 shot_list 值 + 渲染台账状态 + 预览/缩略图 URL + 预算汇总。"""
    base = Path(base)
    eff = effective(base, ep)
    data = load_design(base, ep)
    sl = _shot_list(base, ep)
    by = {g.get("group_id"): g for g in (sl.get("generation_groups") or []) if isinstance(g, dict)}
    budget_s = float(sl.get("budget_s") or sl.get("total_duration_s") or 0)
    ledger = _read(base / "edit" / ep / "transitions_render.json", {}) or {}
    led_by = {(e.get("from_group"), e.get("to_group")): e for e in (ledger.get("transitions") or []) if isinstance(e, dict)}
    builds = {b.get("id"): b for b in (ledger.get("inserts") or []) if isinstance(b, dict)}
    rows = data["boundaries"] if data else diagnose(base, ep)
    from check_generation_groups import insert_total_s, transition_of  # noqa: E402
    out_rows, ins_total = [], 0.0
    tdir = base / "edit" / ep / "transitions"

    def url(rel: Path | str | None):
        if not rel:
            return None
        p = base / rel if not Path(str(rel)).is_absolute() else Path(rel)
        if not p.is_file():
            return None
        return f"/projects/{base.name}/{p.relative_to(base).as_posix()}?v={int(p.stat().st_mtime)}"

    for b in rows:
        g = by.get(b["to_group"]) or {}
        cur = transition_of(g) if g.get("transition_in") else {"type": "hard_cut"}
        ins_total += insert_total_s(cur)
        bid = b["id"]
        led = led_by.get((b["from_group"], b["to_group"])) or {}
        bd = tdir / bid
        meta = _read(bd / "meta.json", {}) or {}
        fp_now = source_fingerprint(base, cur)
        built = bool(meta.get("fingerprint")) and meta.get("fingerprint") == fp_now
        rendered = bool(led) and led.get("fingerprint") == fp_now if led.get("fingerprint") else bool(led) and not (cur.get("inserts") or cur.get("overlay_card"))
        status = b.get("status") or "undecided"
        # 二期(2026-09-26):生成式插入段(定场 i2v / 桥接)的 clip 状态——缺 clip 时先等 Phase 7 p7-transition-clips 出片
        clips = []
        for x in (cur.get("inserts") or []):
            src = x.get("source") if isinstance(x.get("source"), dict) else {}
            if x.get("kind") == "bridge" or (x.get("kind") == "establishing" and src.get("mode") == "i2v"):
                paths = clip_paths(ep, bid, "establishing" if x.get("kind") == "establishing" else "bridge")
                f = (src.get("file") if x.get("kind") == "establishing" else x.get("file")) or paths["file"]
                if x.get("kind") == "establishing":
                    still, last = src.get("still") or paths.get("still"), None
                else:   # 三期桥接:首帧 = 前组尾帧、尾帧 = 本组首帧
                    still, last = x.get("first_frame") or paths.get("first"), x.get("last_frame") or paths.get("last")
                clips.append({"kind": x.get("kind"), "file": f, "exists": (base / f).is_file(),
                              "still": still, "still_url": url(still) if still else None,
                              "last": last, "last_url": url(last) if last else None})
        if status == "accepted" and (cur.get("inserts") or cur.get("overlay_card") or cur.get("type") != "hard_cut"):
            render_state = "rendered" if (led and rendered) else ("built" if built else "pending")
            if any(not c["exists"] for c in clips) and render_state in ("pending", "built"):
                render_state = "awaiting_clip"     # 缺生成式 clip:build 只记 missing,先等 p7-transition-clips 出片
        else:
            render_state = None
        stale = bool(meta.get("fingerprint")) and meta.get("fingerprint") != fp_now
        thumbs = {
            "prev_tail": url(f"assets/clips/{ep}/{b['from_group']}.last_frame.png"),
            "next_head": url(f"edit/{ep}/transitions/thumbs/{b['to_group']}.first.jpg"),
            "card": url(bd / "card.png") if (bd / "card.png").is_file() else None,
            "overlay": url(bd / "overlay.png") if (bd / "overlay.png").is_file() else None,
            "establishing": url(bd / "establishing.jpg") if (bd / "establishing.jpg").is_file() else None,
            "preview": url(bd / "preview.mp4") if (bd / "preview.mp4").is_file() else None,
        }
        out_rows.append({
            "id": bid, "from_group": b["from_group"], "to_group": b["to_group"], "boundary_shots": b.get("boundary_shots"),
            "diagnosis": b.get("diagnosis"), "card_lines": b.get("card_lines"), "card_sources": b.get("card_sources"),
            "establishing_available": bool(b.get("establishing")), "timelapse_available": bool(b.get("timelapse")),
            "design": b.get("design"), "alternatives": b.get("alternatives") or [], "status": status, "source": b.get("source"),
            "feedback": b.get("feedback") or [], "current": cur, "insert_total_s": round(insert_total_s(cur), 3),
            "render_state": render_state, "stale": stale, "ledger": {k: led.get(k) for k in ("type", "duration_s", "cut_time_s", "renders_as")} if led else None,
            "thumbs": thumbs, "preview_meta": meta.get("preview") or None,
            "clips": clips, "designed_by": b.get("designed_by"), "agent_note": b.get("agent_note"),
        })
    # 集尾收束(2026-09-25):生效值 + 来源 + 渲染台账状态 + 末组尾帧 / 预览小片
    close_eff = effective_episode_close(base, ep, sl)
    close_raw = sl.get("episode_close") if isinstance(sl.get("episode_close"), dict) else None
    last_gid = (sl.get("generation_groups") or [{}])[-1].get("group_id") if sl.get("generation_groups") else None
    led_close = ledger.get("episode_close") if isinstance(ledger.get("episode_close"), dict) else None
    cdir = tdir / "episode_close"
    cmeta = _read(cdir / "meta.json", {}) or {}
    close_fp = close_fingerprint(close_eff)
    episode_close = {
        "effective": close_eff, "shot_list": close_raw, "settings": eff.get("episode_close"),
        "source": ("shot_list" if close_raw else "settings"), "last_group": last_gid,
        "render_state": ("rendered" if (led_close and led_close.get("fingerprint") == close_fp) else ("pending" if close_eff else None)),
        "ledger": led_close,
        "thumbs": {"prev_tail": url(f"assets/clips/{ep}/{last_gid}.last_frame.png") if last_gid else None,
                   "preview": url(cdir / "preview.mp4") if (cdir / "preview.mp4").is_file() and cmeta.get("preview_fingerprint") == close_fp else None},
    }
    n_change = sum(1 for r in out_rows if (r.get("diagnosis") or {}).get("has_change"))
    n_non_hard = sum(1 for r in out_rows if (r.get("current") or {}).get("type") != "hard_cut" or (r.get("current") or {}).get("inserts") or (r.get("current") or {}).get("overlay_card"))
    return {
        "ep": ep, "settings": eff, "designed": bool(data), "design_mode": data.get("mode") if data else None,
        "design_written_at": data.get("written_at") if data else None,
        "summary": {"boundaries": len(out_rows), "with_change": n_change, "non_hard_cut": n_non_hard,
                    "pending": sum(1 for r in out_rows if r["status"] == "proposed"),
                    "insert_total_s": round(ins_total, 2), "budget_s": round(budget_s * eff["insert_budget_pct"] / 100.0, 2),
                    "episode_budget_s": budget_s},
        "boundaries": out_rows,
        "episode_close": episode_close,
    }


def ensure_head_thumbs(base: Path, ep: str, groups: list[str] | None = None) -> int:
    """组视频首帧缩略(edit/<ep>/transitions/thumbs/<gid>.first.jpg,按 clip mtime 复用),供过场卡设计链显示本组首帧。"""
    import subprocess
    base = Path(base)
    cdir = base / "assets" / "clips" / ep
    tdir = base / "edit" / ep / "transitions" / "thumbs"
    n = 0
    gids = groups if groups is not None else sorted(p.stem for p in cdir.glob("grp*.mp4")) if cdir.is_dir() else []
    for gid in gids:
        clip = cdir / f"{gid}.mp4"
        out = tdir / f"{gid}.first.jpg"
        if not clip.is_file():
            continue
        if out.is_file() and out.stat().st_mtime >= clip.stat().st_mtime:
            continue
        tdir.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(clip), "-frames:v", "1", "-vf", "scale=480:-2", str(out)],
                           check=True, capture_output=True, timeout=60)
            n += 1
        except Exception:
            continue
    return n
