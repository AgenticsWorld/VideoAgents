# -*- coding: utf-8 -*-
"""post_plan.py — 后期处方台账(edit/epNN/post_plan.json)与处方目录。

后期预览页(/preview/post,WORKFLOW.md §9D,2026-09-11)的原子记录是「处方」(recipe):
一条处方说清楚作用在哪(scope)、做什么(section/kind)、参数与参考(params/refs)、说明(note)、
到哪一步了(status)、产物(output)。五个分区(细节填充/调色与光感/特效/包装/音效与声音)共用同一套
字段与状态机,差别只在 kind 的参数表。

三种执行方式(exec):
  ffmpeg  宿主 CLI code/post_apply.py 直接出片(分镜组母本永不覆盖,产物按版本另存 assets/post/)
  agent   派单 10-editing/post-finishing,agent 出片后用 post_apply.py register 登记版本
  record  只记台账,出成片时由拼装/混音工序按台账生效(包装层、声音层)

版本链:versions[grp] = [{v, file, recipes[], base_v, fingerprint, created_at, adopted_at, cleaned}];
current[grp] = 当前指针(0 = 母本 assets/clips/epNN/grpNNN.mp4)。回滚只挪指针不删文件;
H3P 签字时 cleanup 只保留母本 + 最近两个已采纳版本(文件删、记录留,标 cleaned)。

作用域四级继承:episode → scene → group → range(组内时间段);下级覆盖上级,
effective_recipes() 返回某组实际生效的处方(继承来的带 inherited=True)。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path

SCHEMA = "post_plan/1.0"
LEDGER_REL = "edit/{ep}/post_plan.json"
CHECK_REL = "edit/{ep}/post_check.json"
SIGNOFF_REL = "edit/{ep}/post_signoff.json"
SFX_REL = "assets/post/{ep}/sfx_cues.json"
POST_DIR_REL = "assets/post/{ep}"
CUT_POST = "cut_post.mp4"
CUT_POST_V2 = "cut_post_v2.mp4"
AGENT_ID = "10-editing/post-finishing"
GATE_ID = "g9p"
CHECKPOINT = "H3P-后期确认"
KEEP_ADOPTED = 2          # 版本保留:母本 + 最近两个已采纳版本

STATUSES = ("draft", "dispatched", "applied", "adopted", "discarded", "failed")
STATUS_LABEL = {"draft": "草稿", "dispatched": "已派单", "applied": "已出片",
                "adopted": "已采纳", "discarded": "已弃用", "failed": "失败"}
LAYERS = {"picture": "画面", "pack": "包装", "sound": "声音"}
SECTIONS = [
    {"id": "fill", "label": "细节填充", "layer": "picture", "order": 1},
    {"id": "grade", "label": "调色与光感", "layer": "picture", "order": 2},
    {"id": "vfx", "label": "特效", "layer": "picture", "order": 3},
    {"id": "pack", "label": "包装", "layer": "pack", "order": 4},
    {"id": "sound", "label": "音效与声音", "layer": "sound", "order": 5},
]
LUT_PRESETS = [
    {"id": "teal_orange", "label": "青橙(电影感)"},
    {"id": "warm_film", "label": "暖调胶片"},
    {"id": "cool_night", "label": "冷调夜景"},
    {"id": "bleach", "label": "漂白(低饱和高反差)"},
    {"id": "vintage", "label": "复古褪色"},
]

# 处方目录:params 的 type ∈ number|range|select|text|bool|palette|asset;
# refs ∈ frame(参考帧)|mask(蒙版)|asset(素材);preview=True 表示可快速预览(仅 ffmpeg 类)
KINDS: list[dict] = [
    # ---- 细节填充 ----
    {"id": "deflicker", "section": "fill", "label": "去闪烁", "exec": "ffmpeg", "scopes": ["group", "range", "scene"],
     "params": [{"key": "size", "label": "窗口(帧)", "type": "range", "min": 3, "max": 15, "step": 2, "default": 5},
                {"key": "strength", "label": "强度", "type": "range", "min": 0, "max": 1, "step": 0.1, "default": 0.8}],
     "hint": "时域亮度平滑,治 AI 视频常见的逐帧曝光跳动;强度=与原片混合比"},
    {"id": "delogo", "section": "fill", "label": "遮标去除(矩形)", "exec": "ffmpeg", "scopes": ["group", "range"],
     "refs": ["mask"], "params": [], "hint": "在监视器上画一个矩形蒙版,用周围像素填补(适合小水印/小杂物)"},
    {"id": "upscale", "section": "fill", "label": "超分", "exec": "agent", "scopes": ["group", "scene", "episode"],
     "params": [{"key": "resolution", "label": "目标分辨率", "type": "select", "options": ["1080p", "2k", "4k"], "default": "1080p"}],
     "hint": "走 genmedia.py upscale(SeedVR2 / MiniMax),逐组决定哪些值得超分"},
    {"id": "local_repaint", "section": "fill", "label": "局部重绘", "exec": "agent", "scopes": ["group", "range"],
     "refs": ["mask", "frame"],
     "params": [{"key": "strength", "label": "改动幅度", "type": "range", "min": 0.1, "max": 1, "step": 0.1, "default": 0.5}],
     "hint": "蒙版内按说明重绘(V2V 编辑);说明写清「改什么、改成什么」"},
    {"id": "interpolate", "section": "fill", "label": "插帧", "exec": "agent", "scopes": ["group", "scene", "episode"],
     "params": [{"key": "fps", "label": "目标帧率", "type": "select", "options": ["48", "60"], "default": "48"}],
     "hint": "RIFE 类工作流(可绑 RunningHub);成片帧率须全集一致,建议整集作用域"},
    # ---- 调色与光感 ----
    {"id": "basic", "section": "grade", "label": "基础校正", "exec": "ffmpeg", "scopes": ["group", "range", "scene", "episode"],
     "params": [{"key": "brightness", "label": "亮度", "type": "range", "min": -0.3, "max": 0.3, "step": 0.01, "default": 0},
                {"key": "contrast", "label": "对比", "type": "range", "min": 0.5, "max": 1.8, "step": 0.02, "default": 1},
                {"key": "saturation", "label": "饱和", "type": "range", "min": 0, "max": 2, "step": 0.05, "default": 1},
                {"key": "gamma", "label": "伽马", "type": "range", "min": 0.5, "max": 2, "step": 0.02, "default": 1},
                {"key": "temperature", "label": "色温(K)", "type": "range", "min": 3000, "max": 9000, "step": 100, "default": 6500}],
     "hint": "曝光/对比/白平衡/饱和四件套;色温 6500 = 不动"},
    {"id": "lut", "section": "grade", "label": "LUT 预设", "exec": "ffmpeg", "scopes": ["group", "range", "scene", "episode"],
     "params": [{"key": "preset", "label": "预设", "type": "select", "options": [p["id"] for p in LUT_PRESETS], "default": "teal_orange"},
                {"key": "strength", "label": "强度", "type": "range", "min": 0, "max": 1, "step": 0.05, "default": 0.6}],
     "hint": "内置五档参数化预设;项目 assets/post/luts/*.cube 与 data/luts/*.cube 会自动列进预设"},
    {"id": "scene_palette", "section": "grade", "label": "场次色板", "exec": "ffmpeg", "scopes": ["scene", "group", "range"],
     "params": [{"key": "palette", "label": "目标色板", "type": "palette", "default": []},
                {"key": "strength", "label": "强度", "type": "range", "min": 0, "max": 1, "step": 0.05, "default": 0.5},
                {"key": "saturation", "label": "饱和", "type": "range", "min": 0.5, "max": 1.5, "step": 0.05, "default": 1}],
     "hint": "把画面整体均值推向色彩脚本(bible/color_script.json)里该场次的色板;色板留空 = 自动读脚本"},
    {"id": "match_ref", "section": "grade", "label": "参考帧匹配", "exec": "ffmpeg", "scopes": ["group", "range"],
     "refs": ["frame"],
     "params": [{"key": "strength", "label": "强度", "type": "range", "min": 0, "max": 1, "step": 0.05, "default": 0.8},
                {"key": "match_contrast", "label": "同时匹配反差", "type": "bool", "default": True}],
     "hint": "把本组的色彩统计匹配到一帧参考(通常取同场次已定稿的组),解决跨组偏色"},
    {"id": "atmos", "section": "grade", "label": "氛围 · 光感", "exec": "ffmpeg", "scopes": ["group", "range", "scene", "episode"],
     "params": [{"key": "glow", "label": "光晕", "type": "range", "min": 0, "max": 1, "step": 0.05, "default": 0},
                {"key": "vignette", "label": "暗角", "type": "range", "min": 0, "max": 1, "step": 0.05, "default": 0},
                {"key": "grain", "label": "颗粒", "type": "range", "min": 0, "max": 1, "step": 0.05, "default": 0},
                {"key": "tint", "label": "色调偏向", "type": "select", "options": ["none", "warm", "cool", "candle"], "default": "none"},
                {"key": "flicker", "label": "烛光闪烁", "type": "range", "min": 0, "max": 1, "step": 0.05, "default": 0}],
     "hint": "光感低配版:高光晕染、暗角、胶片颗粒、冷暖偏向、烛光闪烁;重打光待模型选定后拆成独立分区"},
    {"id": "lighting_scheme", "section": "grade", "label": "跟随光照方案", "exec": "agent", "scopes": ["group", "scene"],
     "params": [{"key": "strength", "label": "强度", "type": "range", "min": 0.1, "max": 1, "step": 0.1, "default": 0.5}],
     "hint": "按组提示词里的 lighting_scheme_id 光位描述重打光(V2V);说明只写「哪里不对」"},
    {"id": "relight_ref", "section": "grade", "label": "参考帧重打光", "exec": "agent", "scopes": ["group", "range"],
     "refs": ["frame"],
     "params": [{"key": "strength", "label": "强度", "type": "range", "min": 0.1, "max": 1, "step": 0.1, "default": 0.5}],
     "hint": "把光源方向/明暗分布匹配到参考帧(V2V 重打光模型)"},
    # ---- 特效 ----
    {"id": "overlay_asset", "section": "vfx", "label": "叠加素材", "exec": "ffmpeg", "scopes": ["group", "range"],
     "refs": ["asset", "mask"],
     "params": [{"key": "blend", "label": "混合", "type": "select", "options": ["screen", "overlay", "lighten", "addition", "normal"], "default": "screen"},
                {"key": "opacity", "label": "不透明度", "type": "range", "min": 0, "max": 1, "step": 0.05, "default": 0.8},
                {"key": "fit", "label": "范围", "type": "select", "options": ["full", "mask"], "default": "full"}],
     "hint": "把一段素材(粒子/光效/雨雪,黑底或带 alpha)叠到画面上;范围=蒙版时只叠在蒙版框内"},
    {"id": "vfx_generate", "section": "vfx", "label": "生成特效", "exec": "agent", "scopes": ["group", "range"],
     "refs": ["mask", "frame"],
     "params": [{"key": "type", "label": "类型", "type": "select", "options": ["粒子", "光效", "天气", "法术", "其他"], "default": "光效"},
                {"key": "strength", "label": "强度", "type": "range", "min": 0.1, "max": 1, "step": 0.1, "default": 0.6}],
     "hint": "说明必写四问:发生什么、从哪来、到哪去、多强"},
    # ---- 包装 ----
    {"id": "subtitle_style", "section": "pack", "label": "字幕样式", "exec": "record", "scopes": ["episode"],
     "params": [{"key": "font_size_pct", "label": "字高(% 画面高)", "type": "range", "min": 2.5, "max": 4, "step": 0.1, "default": 3.5},
                {"key": "margin_pct", "label": "下边距 %", "type": "range", "min": 2, "max": 4, "step": 0.5, "default": 3},
                {"key": "outline", "label": "黑描边", "type": "bool", "default": True},
                {"key": "max_lines", "label": "最多行数", "type": "select", "options": ["1", "2"], "default": "2"}],
     "hint": "烧录样式权威(WORKFLOW Phase 9 subtitle 行);监视器底部按此实时预览"},
    {"id": "watermark", "section": "pack", "label": "水印 / 角标", "exec": "ffmpeg", "scopes": ["episode"],
     "refs": ["asset"],
     "params": [{"key": "position", "label": "位置", "type": "select", "options": ["top_right", "top_left", "bottom_right", "bottom_left"], "default": "top_right"},
                {"key": "scale", "label": "宽度(% 画面宽)", "type": "range", "min": 4, "max": 25, "step": 1, "default": 10},
                {"key": "opacity", "label": "不透明度", "type": "range", "min": 0.1, "max": 1, "step": 0.05, "default": 0.7},
                {"key": "margin", "label": "边距 px", "type": "range", "min": 0, "max": 80, "step": 4, "default": 24}],
     "hint": "PNG 角标叠到整集正片上(出成片 build-cut 阶段施加,不进分镜组版本)"},
    {"id": "caption", "section": "pack", "label": "花字", "exec": "record", "scopes": ["group", "range"],
     "params": [{"key": "text", "label": "文字", "type": "text", "default": ""},
                {"key": "position", "label": "位置", "type": "select", "options": ["top_left", "top_right", "bottom_left", "bottom_right", "center"], "default": "top_left"},
                {"key": "type", "label": "类型", "type": "select", "options": ["headline", "keyword", "location"], "default": "keyword"}],
     "hint": "记入台账,出成片前由花字工位按 captions.json 契约落地(须开启 output.caption_enabled)"},
    {"id": "transition", "section": "pack", "label": "组间转场(入)", "exec": "record", "scopes": ["group"],
     "params": [{"key": "type", "label": "类型", "type": "select", "options": ["hard_cut", "dissolve", "dip_black", "dip_white", "fade_black"], "default": "dissolve"},
                {"key": "duration_s", "label": "时长 s", "type": "range", "min": 0.2, "max": 2, "step": 0.1, "default": 0.6}],
     "hint": "采纳即写回 shot_list.generation_groups[].transition_in,出成片时 render_transitions 重渲"},
    # ---- 音效与声音 ----
    {"id": "ambience", "section": "sound", "label": "环境声", "exec": "record", "scopes": ["scene", "episode"],
     "params": [{"key": "desc", "label": "描述", "type": "text", "default": ""},
                {"key": "level_db", "label": "相对对白 dB", "type": "range", "min": -30, "max": -6, "step": 1, "default": -18}],
     "hint": "一段循环底噪 + 电平;出成片前派混音工位落地"},
    {"id": "bgm_segment", "section": "sound", "label": "BGM 段落", "exec": "record", "scopes": ["episode", "range"],
     "params": [{"key": "mood", "label": "情绪", "type": "text", "default": ""},
                {"key": "intensity", "label": "强度", "type": "range", "min": 0.1, "max": 1, "step": 0.1, "default": 0.5},
                {"key": "action", "label": "动作", "type": "select", "options": ["replace", "add", "remove"], "default": "replace"}],
     "hint": "在整集时间轴上划段落、标情绪;派音乐工位重出该段"},
    {"id": "level", "section": "sound", "label": "对白 / 旁白电平", "exec": "record", "scopes": ["group", "range"],
     "params": [{"key": "dialogue_db", "label": "对白 dB", "type": "range", "min": -12, "max": 12, "step": 0.5, "default": 0},
                {"key": "narration_db", "label": "旁白 dB", "type": "range", "min": -12, "max": 12, "step": 0.5, "default": 0},
                {"key": "bgm_db", "label": "BGM dB", "type": "range", "min": -24, "max": 6, "step": 0.5, "default": 0}],
     "hint": "「这句被 BGM 盖住了」这类问题;对白重配退回视频页(p7-dub)"},
    {"id": "mix_target", "section": "sound", "label": "混音目标", "exec": "record", "scopes": ["episode"],
     "params": [{"key": "lufs", "label": "响度 LUFS", "type": "range", "min": -18, "max": -12, "step": 0.5, "default": -14},
                {"key": "tp", "label": "真峰 dBTP", "type": "range", "min": -2, "max": -0.5, "step": 0.1, "default": -1}],
     "hint": "整集响度目标,派混音工位重跑"},
]
KIND_BY_ID = {k["id"]: k for k in KINDS}
SECTION_BY_ID = {s["id"]: s for s in SECTIONS}
SCOPE_ORDER = {"episode": 0, "scene": 1, "group": 2, "range": 3}


# ---------------------------------------------------------------- 文件读写
def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def ledger_path(base: Path, ep: str) -> Path:
    return base / LEDGER_REL.format(ep=ep)


def post_dir(base: Path, ep: str) -> Path:
    return base / POST_DIR_REL.format(ep=ep)


def read_json(p: Path, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(p: Path, obj) -> None:
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def empty_plan(ep: str) -> dict:
    return {"schema": SCHEMA, "ep": ep, "updated_at": _now(), "recipes": [], "versions": {}, "current": {}}


def load_plan(base: Path, ep: str) -> dict:
    plan = read_json(ledger_path(base, ep))
    if not isinstance(plan, dict) or plan.get("schema") != SCHEMA:
        return empty_plan(ep)
    plan.setdefault("recipes", [])
    plan.setdefault("versions", {})
    plan.setdefault("current", {})
    return plan


def save_plan(base: Path, ep: str, plan: dict) -> None:
    plan["schema"] = SCHEMA
    plan["ep"] = ep
    plan["updated_at"] = _now()
    write_json(ledger_path(base, ep), plan)


def plan_fingerprint(plan: dict) -> str:
    """签字指纹:处方的状态/参数/产物 + 当前指针;台账一变签字即过期。"""
    slim = {"recipes": [{k: r.get(k) for k in ("id", "scope", "kind", "params", "refs", "note", "status", "output")}
                        for r in plan.get("recipes", [])],
            "current": plan.get("current", {})}
    return hashlib.sha256(json.dumps(slim, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def file_fingerprint(p: Path) -> str:
    """产物指纹:大小 + 首尾各 1MB 的 sha256 前 16 位(整段 sha 对 GB 级视频太慢)。"""
    p = Path(p)
    if not p.is_file():
        return ""
    h = hashlib.sha256()
    size = p.stat().st_size
    h.update(str(size).encode())
    with p.open("rb") as f:
        h.update(f.read(1 << 20))
        if size > (2 << 20):
            f.seek(-(1 << 20), os.SEEK_END)
            h.update(f.read(1 << 20))
    return h.hexdigest()[:16]


# ---------------------------------------------------------------- 处方
def new_id() -> str:
    return "rcp-" + uuid.uuid4().hex[:8]


def normalize_scope(scope: dict | None) -> dict:
    scope = dict(scope or {})
    level = scope.get("level") or ("range" if scope.get("t1") is not None else "group" if scope.get("group_id") else
                                   "scene" if scope.get("scene_id") else "episode")
    if level not in SCOPE_ORDER:
        raise ValueError(f"unknown scope level: {level}")
    out = {"level": level}
    if level in ("scene", "group", "range"):
        if level == "scene":
            if not scope.get("scene_id"):
                raise ValueError("scene scope needs scene_id")
            out["scene_id"] = str(scope["scene_id"])
        else:
            if not scope.get("group_id"):
                raise ValueError(f"{level} scope needs group_id")
            out["group_id"] = str(scope["group_id"])
            if scope.get("scene_id"):
                out["scene_id"] = str(scope["scene_id"])
    if level == "range":
        t0 = float(scope.get("t0") or 0)
        t1 = float(scope.get("t1") or 0)
        if t1 <= t0:
            raise ValueError("range scope needs t1 > t0")
        out["t0"], out["t1"] = round(t0, 3), round(t1, 3)
    return out


def coerce_params(kind: dict, params: dict | None) -> dict:
    """按目录把参数收敛到合法范围,缺省补默认值。"""
    params = dict(params or {})
    out = {}
    for spec in kind.get("params", []):
        key = spec["key"]
        v = params.get(key, spec.get("default"))
        t = spec.get("type")
        if t in ("range", "number"):
            try:
                v = float(v)
            except (TypeError, ValueError):
                v = float(spec.get("default") or 0)
            lo, hi = spec.get("min"), spec.get("max")
            if lo is not None:
                v = max(float(lo), v)
            if hi is not None:
                v = min(float(hi), v)
            v = round(v, 4)
        elif t == "select":
            opts = [str(o) for o in spec.get("options", [])]
            v = str(v) if v is not None else ""
            if opts and v not in opts and not (key == "preset"):     # LUT 预设允许 .cube 文件名
                v = str(spec.get("default"))
        elif t == "bool":
            v = bool(v) if not isinstance(v, str) else v.lower() in ("1", "true", "yes", "on")
        elif t == "palette":
            v = [str(c) for c in (v or []) if re.fullmatch(r"#?[0-9a-fA-F]{6}", str(c))]
            v = [c if c.startswith("#") else "#" + c for c in v]
        else:
            v = "" if v is None else str(v)[:2000]
        out[key] = v
    return out


def make_recipe(kind_id: str, scope: dict, params: dict | None = None, refs: dict | None = None,
                note: str = "", by: str = "user") -> dict:
    kind = KIND_BY_ID.get(kind_id)
    if not kind:
        raise ValueError(f"unknown recipe kind: {kind_id}")
    scope = normalize_scope(scope)
    if scope["level"] not in kind.get("scopes", []):
        raise ValueError(f"{kind['label']} 不支持作用域 {scope['level']}")
    if not str(note or "").strip():
        raise ValueError("处方说明(note)必填:执行侧能力不足时退化为给 agent 的自然语言指令")
    sec = SECTION_BY_ID[kind["section"]]
    return {"id": new_id(), "scope": scope, "layer": sec["layer"], "section": kind["section"], "kind": kind_id,
            "exec": kind["exec"], "params": coerce_params(kind, params), "refs": dict(refs or {}),
            "note": str(note).strip()[:2000], "status": "draft", "created_at": _now(), "updated_at": _now(),
            "created_by": by, "run_id": None, "output": None, "cost": {"estimate": "", "actual": ""}, "error": ""}


def find_recipe(plan: dict, rid: str) -> dict | None:
    return next((r for r in plan.get("recipes", []) if r.get("id") == rid), None)


def set_status(recipe: dict, status: str, **extra) -> None:
    if status not in STATUSES:
        raise ValueError(f"bad status {status}")
    recipe["status"] = status
    recipe["updated_at"] = _now()
    for k, v in extra.items():
        recipe[k] = v


def scope_matches_group(scope: dict, group: dict) -> bool:
    """group = {group_id, scene_id};判断处方作用域是否覆盖该组。"""
    lv = scope.get("level")
    if lv == "episode":
        return True
    if lv == "scene":
        return scope.get("scene_id") == group.get("scene_id")
    return scope.get("group_id") == group.get("group_id")


def effective_recipes(plan: dict, group: dict, include_status: tuple = ("draft", "dispatched", "applied", "adopted", "failed")) -> list[dict]:
    """某组实际生效的处方:自身(group/range)+ 继承(episode/scene),按分区/作用域排序,继承的标 inherited。
    同 kind 的下级覆盖上级(上级仍返回但标 overridden=True,前端灰显)。"""
    rows = []
    for r in plan.get("recipes", []):
        if r.get("status") not in include_status:
            continue
        if scope_matches_group(r.get("scope", {}), group):
            row = dict(r)
            row["inherited"] = r["scope"]["level"] in ("episode", "scene")
            rows.append(row)
    # 覆盖判定:同 kind 更具体的作用域存在 → 上级 overridden
    by_kind: dict[str, list[dict]] = {}
    for row in rows:
        by_kind.setdefault(row["kind"], []).append(row)
    for kind, lst in by_kind.items():
        deepest = max(SCOPE_ORDER[x["scope"]["level"]] for x in lst)
        for x in lst:
            x["overridden"] = SCOPE_ORDER[x["scope"]["level"]] < deepest and x["scope"]["level"] in ("episode", "scene")
    rows.sort(key=lambda x: (SECTION_BY_ID[x["section"]]["order"], SCOPE_ORDER[x["scope"]["level"]], x.get("created_at") or ""))
    return rows


# ---------------------------------------------------------------- 版本链
def group_versions(plan: dict, gid: str) -> list[dict]:
    return plan.setdefault("versions", {}).setdefault(gid, [])


def current_version(plan: dict, gid: str) -> int:
    try:
        return int(plan.get("current", {}).get(gid) or 0)
    except (TypeError, ValueError):
        return 0


def version_file(base: Path, ep: str, gid: str, v: int, plan: dict) -> Path | None:
    """版本 v 的文件(v=0 母本);文件不存在返回 None。"""
    if v <= 0:
        p = base / "assets" / "clips" / ep / f"{gid}.mp4"
        return p if p.is_file() else None
    for ver in group_versions(plan, gid):
        if int(ver.get("v") or 0) == v and ver.get("file"):
            p = base / ver["file"]
            return p if p.is_file() else None
    return None


def current_file(base: Path, ep: str, gid: str, plan: dict) -> Path | None:
    """当前指针指向的文件;指针文件缺失时回退母本。"""
    v = current_version(plan, gid)
    p = version_file(base, ep, gid, v, plan)
    return p or version_file(base, ep, gid, 0, plan)


def next_version_no(plan: dict, gid: str) -> int:
    vs = [int(v.get("v") or 0) for v in group_versions(plan, gid)]
    return (max(vs) + 1) if vs else 1


def register_version(plan: dict, base: Path, ep: str, gid: str, rel_file: str, recipe_ids: list[str],
                     base_v: int, by: str = "post_apply") -> dict:
    v = next_version_no(plan, gid)
    ver = {"v": v, "file": rel_file, "recipes": list(recipe_ids), "base_v": int(base_v),
           "fingerprint": file_fingerprint(base / rel_file), "created_at": _now(), "created_by": by,
           "adopted_at": None, "cleaned": False}
    group_versions(plan, gid).append(ver)
    return ver


def adopt_version(plan: dict, gid: str, v: int) -> dict:
    vs = group_versions(plan, gid)
    ver = next((x for x in vs if int(x.get("v") or 0) == int(v)), None)
    if v != 0 and not ver:
        raise ValueError(f"{gid} 没有版本 v{v}")
    if ver and ver.get("cleaned"):
        raise ValueError(f"{gid} v{v} 文件已按保留规则清理,不能再采纳")
    plan.setdefault("current", {})[gid] = int(v)
    if ver:
        ver["adopted_at"] = _now()
    return ver or {"v": 0}


def adopt_recipe(base: Path, ep: str, plan: dict, recipe: dict) -> dict:
    """采纳:ffmpeg/agent 类把产物版本设为当前指针;record 类直接记台账;转场类回写 shot_list.transition_in。"""
    if recipe.get("exec") in ("ffmpeg", "agent"):
        if recipe.get("status") != "applied" or not (recipe.get("output") or {}).get("versions"):
            raise ValueError("只有「已出片」的处方才能采纳")
        for gid, info in recipe["output"]["versions"].items():
            adopt_version(plan, gid, int(info.get("v") or 0))
    elif recipe.get("kind") == "transition":
        write_transition_in(base, ep, recipe)
    set_status(recipe, "adopted", adopted_at=_now())
    return recipe


def discard_recipe(base: Path, ep: str, plan: dict, recipe: dict) -> dict:
    """弃用:已采纳的 ffmpeg/agent 类把指针退回该版本的 base_v;文件不删(cleanup 时按保留规则处理)。"""
    if recipe.get("status") == "adopted" and recipe.get("exec") in ("ffmpeg", "agent"):
        for gid, info in ((recipe.get("output") or {}).get("versions") or {}).items():
            v = int(info.get("v") or 0)
            if current_version(plan, gid) == v:
                ver = next((x for x in group_versions(plan, gid) if int(x.get("v") or 0) == v), None)
                plan.setdefault("current", {})[gid] = int((ver or {}).get("base_v") or 0)
    elif recipe.get("status") == "adopted" and recipe.get("kind") == "transition":
        write_transition_in(base, ep, recipe, remove=True)
    set_status(recipe, "discarded")
    return recipe


def write_transition_in(base: Path, ep: str, recipe: dict, remove: bool = False) -> None:
    sl_path = base / "directing" / ep / "shot_list.json"
    sl = read_json(sl_path)
    if not isinstance(sl, dict):
        raise ValueError("shot_list.json 不存在,转场处方无处回写")
    gid = recipe["scope"].get("group_id")
    hit = False
    for g in sl.get("generation_groups", []) or []:
        if isinstance(g, dict) and g.get("group_id") == gid:
            hit = True
            if remove:
                prev = (g.get("transition_in") or {}).get("_post_prev")
                if prev is not None:
                    g["transition_in"] = prev or None
                    if not prev:
                        g.pop("transition_in", None)
            else:
                p = recipe.get("params") or {}
                prev = g.get("transition_in")
                if isinstance(prev, dict) and prev.get("source") == "post_plan":
                    prev = prev.get("_post_prev")
                g["transition_in"] = {"type": str(p.get("type") or "dissolve"), "duration_s": float(p.get("duration_s") or 0.6),
                                      "intent": "post", "reason": recipe.get("note", ""), "source": "post_plan",
                                      "recipe_id": recipe["id"], "_post_prev": prev if isinstance(prev, dict) else {}}
    if not hit:
        raise ValueError(f"shot_list 里没有组 {gid}")
    write_json(sl_path, sl)


def cleanup_versions(plan: dict, base: Path, ep: str, keep_adopted: int = KEEP_ADOPTED) -> list[str]:
    """保留母本 + 最近 keep_adopted 个已采纳版本(以及当前指针);其余版本文件删除、记录标 cleaned。
    返回被清理的 'grp/vN' 列表。"""
    cleaned = []
    # 未裁决(已出片 / 派单中)处方引用的版本也保留:签字不替用户做 A|B 决定
    undecided = set()
    for r in plan.get("recipes", []):
        if r.get("status") in ("applied", "dispatched"):
            for gid, info in ((r.get("output") or {}).get("versions") or {}).items():
                undecided.add((gid, int(info.get("v") or 0)))
    for gid, vs in plan.get("versions", {}).items():
        cur = current_version(plan, gid)
        adopted = sorted([x for x in vs if x.get("adopted_at")], key=lambda x: (x.get("adopted_at") or "", int(x.get("v") or 0)), reverse=True)
        keep = {int(x["v"]) for x in adopted[:keep_adopted]} | {cur} | {v for g, v in undecided if g == gid}
        for x in vs:
            v = int(x.get("v") or 0)
            if v in keep or x.get("cleaned"):
                continue
            f = base / str(x.get("file") or "")
            if x.get("file") and f.is_file():
                try:
                    f.unlink()
                except OSError:
                    continue
            x["cleaned"] = True
            x["cleaned_at"] = _now()
            cleaned.append(f"{gid}/v{v}")
    return cleaned


def summary(plan: dict) -> dict:
    cnt = {s: 0 for s in STATUSES}
    for r in plan.get("recipes", []):
        cnt[r.get("status", "draft")] = cnt.get(r.get("status", "draft"), 0) + 1
    return {"total": len(plan.get("recipes", [])), **cnt}


# ---------------------------------------------------------------- 音效点位表
def sfx_path(base: Path, ep: str) -> Path:
    return base / SFX_REL.format(ep=ep)


def load_sfx(base: Path, ep: str) -> dict:
    d = read_json(sfx_path(base, ep))
    if not isinstance(d, dict):
        return {"schema": "post_sfx_cues/1.0", "ep": ep, "rows": []}
    d.setdefault("rows", [])
    return d


def save_sfx(base: Path, ep: str, d: dict) -> None:
    d["schema"] = "post_sfx_cues/1.0"
    d["ep"] = ep
    d["updated_at"] = _now()
    write_json(sfx_path(base, ep), d)


def lut_presets(base: Path, data_dir: Path | None = None) -> list[dict]:
    """内置参数化预设 + 项目/全局 .cube 文件。"""
    out = [dict(p) for p in LUT_PRESETS]
    dirs = [base / "assets" / "post" / "luts"]
    if data_dir:
        dirs.append(Path(data_dir) / "luts")
    for d in dirs:
        if d.is_dir():
            for f in sorted(d.glob("*.cube")):
                out.append({"id": f.name, "label": f.stem + " (.cube)", "file": str(f)})
    return out
