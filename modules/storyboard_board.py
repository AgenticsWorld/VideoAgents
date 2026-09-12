"""故事板预览(2026-09-11):分镜层(07-directing/storyboard)产物 storyboard.json 的归一化视图 + 分镜草图台账。

页面 /preview/board 与草图 CLI code/storyboard_sketch.py 共用本模块:
- load_board(base, ep):把各历史版本 storyboard.json(字段名多次演化)归一成同一结构,
  并按 shot_list.json 的 storyboard_ref 把定稿镜号/时长/台词对回草案镜;
- 草图台账 assets/storyboard/<ep>/index.json(schema storyboard_sketches/1.0):
  shots[<S01-01>] = {file, status(queued|running|done|failed), error, prompt, note, provider, model, refs, updated_at}
  图片 assets/storyboard/<ep>/<S01-01>.png;台账写入经 flock 串行,宿主后台任务与 Agent 的 CLI 进程可并发;
- build_prompt / collect_refs:草图提示词(铅笔手绘分镜风格,英文风格句 + 原文画面内容)与参考图
  (只带出场人物 sheet,缩到 512px 长边;不带场景图——俯视布局图会误导图像模型,场景只靠文字描述;
  也不用风格参考图,铅笔风格全靠提示词——2026-09-11 用户拍板);
  **人物优先、背景留白**(2026-09-12 用户拍板:没有合适的场景参考图,草图弱化场景展现,主要按分镜表现
  镜头机位、人物比例、神态、动作):风格句要求人物线稿清晰、按景别画对人物比例、表情与肢体可读,背景只
  两三笔示意或留白;地点只留一句短提示放在最后,不再拼场景卡描述;从 content/sketch/action 文字里自动推导
  「Camera:」(角度/高度/镜头/朝向)与「Expressions:」(神态/视线)两句英文关键词加进提示词(camera_hint / expression_hint);
- 宫格批量(2026-09-11 用户拍板,2026-09-12 由 3×3 降为 2×2):单集标题行「出草图」一次出一张 2×2 宫格图
  (≤4 镜,按集内顺序分批),split_grid 切成小图落到各镜的 <S01-01>.png(台账记 mode=grid + grid.file/cell);
  宫格原图存 _grids/。单镜「出图/重出」仍是单张出图。宫格切出的小图(约 1230×690)与单张(1280 长边)接近,
  动态样片按画布等比缩放统一。降到 2×2 的原因:3×3 每格约 820×460,画不出可读的表情,且逐格文字预算太小。
只读 storyboard.json / shot_list.json / bible 与概念图,不改任何分镜文件。
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SCHEMA_VERSION = "storyboard_sketches/1.0"
STORYBOARD_AGENT = "07-directing/storyboard"
DIRECTOR_AGENT = "07-directing/director"
SKETCH_AGENT = "07-directing/storyboard-sketch"
SKETCH_DIR_REL = "assets/storyboard/{ep}"
REF_MAX_EDGE = 512           # 参考图缩放长边(草图只需形象/空间提示,不需要高清)
MAX_CAST_REFS = 3            # 人物参考图上限(多了反而稀释风格参考)
STATUSES = ("queued", "running", "done", "failed")

# 2026-09-12 用户拍板:人物优先、背景留白——草图只为看机位、人物比例、神态、动作,场景不展开(没有合适的场景参考图)。
SKETCH_STYLE_PROMPT = (
    "Film storyboard panel, rough pencil sketch on white paper: hand-drawn monochrome line art with loose "
    "hatching and soft grey marker shading, quick gestural strokes, unfinished sketchbook look. "
    "FIGURES FIRST: draw the characters with clear confident lines, correct body proportions and figure size "
    "for the stated shot size, readable facial expressions, eye lines and body gestures; faces must be clear "
    "enough to read the emotion. "
    "BACKGROUND MINIMAL: only two or three loose lines or a little light hatching to hint at the space, most of "
    "the paper left blank; no architectural detail, no furniture detail, no props unless mentioned. "
    "The framing must show the camera angle, camera height and lens exactly as described (eye level, high "
    "angle, low angle, over-the-shoulder, profile, from behind). "
    "Strictly black-and-white, no color. A single frame, no panel borders, no text, no captions, no speech "
    "bubbles, no watermark. The attached images are the project's official character designs — keep each "
    "character's likeness, hairstyle and outfit, but redraw everything as a pencil sketch; the location is "
    "described in text only and stays a faint hint."
)
SKETCH_NEGATIVE = ("color, colorful, photo, photorealistic, 3d render, cgi, painting, ink wash, anime cel, "
                   "text, letters, caption, watermark, logo, speech bubble, comic panel grid, multiple panels, "
                   "border, frame lines, detailed background, cluttered environment, architectural rendering, "
                   "interior design, landscape painting, scenery without people")

GRID_MAX_PANELS = 4          # 一张宫格图最多 4 镜(2×2;2026-09-12 由 3×3/9 镜降下来:每格更大才画得出表情,逐格文字预算也更宽)
GRID_MAX_CAST_REFS = 4       # 宫格模式人物参考图上限(4 镜的出场并集,按出场次数取前几位)
GRID_CELL_TRIM = 0.02        # 切分时每格四边各裁掉 2%,去掉模型画的格线/留白
GRID_DIR_REL = "assets/storyboard/{ep}/_grids"
GRID_STYLE_PROMPT = (
    "A film storyboard contact sheet: a strict {cols}x{rows} grid of {n} equal-size panels on one white page, "
    "{cols} columns and {rows} rows, separated only by thin straight black gutter lines, panels read left to right, "
    "top to bottom, every panel filling its cell edge to edge with the same {aspect} framing. Each panel is a rough "
    "pencil sketch: hand-drawn monochrome line art with loose hatching and soft grey marker shading, quick gestural "
    "strokes, unfinished sketchbook look. FIGURES FIRST in every panel: clear confident lines for the characters, "
    "correct body proportions and figure size for that panel's shot size, readable facial expressions, eye lines "
    "and body gestures. BACKGROUND MINIMAL in every panel: only two or three loose lines to hint at the space, most "
    "of the paper left blank; no architectural or furniture detail, no props unless mentioned. Each panel must show "
    "its camera angle, height and lens exactly as described. Strictly black-and-white, no color. No text, no "
    "numbers, no captions, no speech bubbles, no watermark inside the panels. The attached images are the project's "
    "official character designs — keep each character's likeness, hairstyle and outfit in every panel, but redraw "
    "everything as a pencil sketch; locations are described in text only and stay a faint hint.{blank}"
)
GRID_NEGATIVE = ("color, colorful, photo, photorealistic, 3d render, cgi, painting, ink wash, anime cel, "
                 "text, letters, numbers, caption, watermark, logo, speech bubble, uneven panels, overlapping panels, "
                 "panels of different sizes, decorative border, detailed background, cluttered environment, "
                 "architectural rendering, interior design, scenery without people")

_REF_RE = re.compile(r"^(.+?)(?:/shots_draft)?/order:(\d+)(?:/split:[^/]+)?$")
_DLG_RE = re.compile(r"^\s*(?:S\d+[A-Za-z]?\s*[/·:\-]\s*)?(CHAR-\d+|NARRATOR|[^:：/·「」]{1,12})\s*[:：]\s*(.+?)\s*$")


# ---------------- 通用 ----------------

def _read_json(p: Path):
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def shot_key(scene_no: str, order) -> str:
    """草图台账键 / 文件名:S01-01(scene_no 去空白,序号两位)。"""
    sn = re.sub(r"[^\w\-]", "", str(scene_no or "S0"))
    try:
        return f"{sn}-{int(order):02d}"
    except Exception:
        tail = re.sub(r"[^\w]", "", str(order))
        return f"{sn}-{tail}"


def sketch_dir(base: Path, ep: str) -> Path:
    return base / SKETCH_DIR_REL.format(ep=ep)


def index_path(base: Path, ep: str) -> Path:
    return sketch_dir(base, ep) / "index.json"


def load_index(base: Path, ep: str) -> dict:
    d = _read_json(index_path(base, ep)) or {}
    if not isinstance(d.get("shots"), dict):
        d = {"schema": SCHEMA_VERSION, "ep": ep, "shots": {}}
    d.setdefault("schema", SCHEMA_VERSION)
    d.setdefault("ep", ep)
    return d


def update_index(base: Path, ep: str, key: str, patch: dict | None, remove: bool = False) -> dict:
    """读-改-写台账一条(flock 串行;宿主线程与 Agent 的 CLI 进程可并发)。返回改后的整份台账。"""
    p = index_path(base, ep)
    p.parent.mkdir(parents=True, exist_ok=True)
    lock = p.with_suffix(".lock")
    with open(lock, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            d = load_index(base, ep)
            if remove:
                d["shots"].pop(key, None)
            else:
                rec = d["shots"].get(key) or {}
                rec.update(patch or {})
                rec["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                d["shots"][key] = rec
            d["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1))
            os.replace(tmp, p)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)
    return d


# ---------------- 资产索引(人物/场景/生物/道具 名字 + 缩略图) ----------------

def _first_image(d: Path, prefer: tuple[str, ...] = ()) -> Path | None:
    if not d.is_dir():
        return None
    for name in prefer:
        f = d / name
        if f.is_file():
            return f
    for f in sorted(d.iterdir()):
        if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") \
                and not f.name.startswith(("_", ".")):
            return f
    return None


def scene_text(base: Path, sid: str) -> str:
    """草图提示词用的场景文字(替代场景图):取场景卡 architecture.json 的建筑语言/空间类型/材质/细节,
    拼成一段 ≤360 字;没有建筑卡返回空(调用方回落 index 的 description)。"""
    a = _read_json(base / "bible" / "scenes" / sid / "architecture.json") or {}
    if not a:
        return ""
    parts = []
    for v in (a.get("arch_style"), (a.get("space_type") or {}).get("detail") if isinstance(a.get("space_type"), dict) else a.get("space_type"),
              a.get("form"), a.get("scale")):
        if isinstance(v, str) and v.strip():
            parts.append(v.strip().rstrip("。;;"))
    mats = [m for m in (a.get("materials") or []) if isinstance(m, str)][:5]
    if mats:
        parts.append("材质: " + "、".join(mats))
    dets = [d for d in (a.get("details") or []) if isinstance(d, str)]
    if dets:
        parts.append(dets[0])
    return "; ".join(parts)[:360]


def asset_catalog(base: Path) -> dict:
    """{characters:{id:{name,file}}, scenes:{...}, creatures:{...}, props:{...}};file 为项目相对路径(缺图为 None)。"""
    out: dict[str, dict] = {"characters": {}, "scenes": {}, "creatures": {}, "props": {}}
    cidx = _read_json(base / "bible" / "characters" / "index.json") or {}
    for c in cidx.get("characters", []) or []:
        if isinstance(c, dict) and c.get("id"):
            out["characters"][c["id"]] = {"name": c.get("canonical_name") or c.get("name") or c["id"]}
    sidx = _read_json(base / "bible" / "scenes" / "index.json") or {}
    for s in sidx.get("scenes", []) or []:
        if isinstance(s, dict) and s.get("id"):
            out["scenes"][s["id"]] = {"name": s.get("name") or s["id"], "type": s.get("type") or "",
                                     "description": scene_text(base, s["id"]) or str(s.get("description") or "")[:200]}
    cr = _read_json(base / "bible" / "creatures" / "index.json") or {}
    for c in cr.get("creatures", []) or []:
        if isinstance(c, dict) and c.get("id"):
            out["creatures"][c["id"]] = {"name": c.get("name") or c["id"]}
    pr = _read_json(base / "bible" / "props.json") or {}
    plist = pr.get("props") if isinstance(pr, dict) else pr
    for p in plist or []:
        if isinstance(p, dict) and p.get("id"):
            out["props"][p["id"]] = {"name": p.get("name") or p["id"]}
    conc = base / "assets" / "concepts"
    for kind, sub, prefer in (("characters", "characters", ("sheet.png",)),
                              ("creatures", "creatures", ("sheet.png",)),
                              ("props", "props", ("main_01.png", "main.png")),
                              ("scenes", "scenes", ("layout_top.png", "grid_9views.png"))):
        d = conc / sub
        if not d.is_dir():
            continue
        for sd in sorted(d.iterdir()):
            if not sd.is_dir() or sd.name.startswith("."):
                continue
            f = None
            if kind == "scenes":
                # 场景缩略:优先该场景库的分镜背景图(实拍视角)→ 俯视布局图 → 九宫格
                pidx = _read_json(sd / "plates" / "index.json") or {}
                for p in pidx.get("plates") or []:
                    if isinstance(p, dict) and p.get("file") and (base / p["file"]).is_file():
                        f = base / p["file"]
                        break
            f = f or _first_image(sd, prefer)
            rec = out[kind].setdefault(sd.name, {"name": sd.name})
            rec["file"] = f.relative_to(base).as_posix() if f else None
    for kind in out:
        for rec in out[kind].values():
            rec.setdefault("file", None)
    return out


# ---------------- storyboard.json 归一化 ----------------

def _first(d: dict, *keys, default=None):
    for k in keys:
        v = d.get(k)
        if v not in (None, "", [], {}):
            return v
    return default


def _as_list(v) -> list:
    if v is None:
        return []
    if isinstance(v, list):
        return [x for x in v if x not in (None, "")]
    return [v]


def parse_dialogue_ref(v, names: dict) -> list[dict]:
    """草案镜 dialogue_ref 的几种历史写法 → [{speaker, name, text}] / [{ref}]:
    "S01/CHAR-0002:施主,请留步!" · "S01 · CHAR-0010:「食馎饦否?」(est 1.5s)" · "S04-D01" / "ep01-s01-d001"(仅编号)"""
    rows = []
    for item in _as_list(v):
        if isinstance(item, dict):
            sp = item.get("speaker") or item.get("char") or ""
            txt = item.get("text") or item.get("line") or ""
            if txt:
                rows.append({"speaker": sp, "name": names.get(sp, sp), "text": txt})
            elif item.get("id") or item.get("ref"):
                rows.append({"ref": item.get("id") or item.get("ref")})
            continue
        s = str(item).strip()
        m = _DLG_RE.match(s)
        if m and (m.group(1).startswith("CHAR-") or m.group(1) == "NARRATOR" or m.group(1) in names.values()):
            sp = m.group(1)
            txt = re.sub(r"\s*[((]est\s*[\d.]+s[))]\s*$", "", m.group(2)).strip()
            txt = re.sub(r"^[「『\"]|[」』\"]$", "", txt)
            sid = sp if sp.startswith("CHAR-") or sp == "NARRATOR" else next((k for k, n in names.items() if n == sp), sp)
            rows.append({"speaker": sid, "name": names.get(sid, sp), "text": txt})
        elif re.match(r"^[\w\-]+$", s):
            rows.append({"ref": s})
        else:
            rows.append({"speaker": "", "name": "", "text": s})
    return rows


def _shot_list_map(sl: dict) -> dict[tuple[str, int], list[dict]]:
    """shot_list shots[] 按 storyboard_ref "S01/order:1"(含 /split:a 与 /shots_draft/ 变体)→ 草案镜 (scene_no, order)。"""
    m: dict[tuple[str, int], list[dict]] = {}
    for s in sl.get("shots") or []:
        if not isinstance(s, dict):
            continue
        r = _REF_RE.match(str(s.get("storyboard_ref") or ""))
        if not r:
            continue
        m.setdefault((r.group(1), int(r.group(2))), []).append(s)
    return m


def load_board(base: Path, ep: str, catalog: dict | None = None) -> dict:
    """归一化的故事板:{title, scenes[], totals, has_storyboard, shot_list}。scenes[].shots[] 是页面表格的行。"""
    sb = _read_json(base / "directing" / ep / "storyboard.json") or {}
    sl = _read_json(base / "directing" / ep / "shot_list.json") or {}
    cat = catalog or asset_catalog(base)
    names = {k: v["name"] for k, v in cat["characters"].items()}
    names.update({k: v["name"] for k, v in cat["creatures"].items()})
    slmap = _shot_list_map(sl)
    scenes = []
    raw_scenes = sb.get("scenes") if isinstance(sb.get("scenes"), list) else []
    for i, sc in enumerate(raw_scenes):
        if not isinstance(sc, dict):
            continue
        scene_no = str(_first(sc, "scene_no", "screenplay_ref", "no", default=f"S{i + 1:02d}"))
        sid = sc.get("scene_id") or ""
        drafts = sc.get("shots_draft") if isinstance(sc.get("shots_draft"), list) else \
            (sc.get("shots") if isinstance(sc.get("shots"), list) else [])
        shots, cast_union, groups = [], [], sc.get("groups_draft") if isinstance(sc.get("groups_draft"), list) else []
        creatures, props = [], []
        # 组草案的出场角色 → 按 shot_orders/shot_ids 落到镜(有的项目如 liaozhai2 只在组上写 characters,镜上没有 cast)
        grp_cast_by_order: dict[int, list] = {}
        grp_cast_by_id: dict[str, list] = {}
        for g in groups:
            if isinstance(g, dict):
                for c in _as_list(g.get("creatures")):
                    if isinstance(c, str) and c not in creatures:
                        creatures.append(c)
                gcast = [c for c in _as_list(_first(g, "characters", "cast", "cast_ids", default=[])) if isinstance(c, str)]
                for o in _as_list(g.get("shot_orders")):
                    try:
                        grp_cast_by_order.setdefault(int(o), []).extend(gcast)
                    except Exception:
                        pass
                for sid_ in _as_list(_first(g, "shot_ids", "shots", default=[])):
                    if isinstance(sid_, str):
                        grp_cast_by_id.setdefault(sid_, []).extend(gcast)
        for j, d in enumerate(drafts):
            if not isinstance(d, dict):
                continue
            order = d.get("order") if d.get("order") is not None else j + 1
            try:
                order = int(order)
            except Exception:
                order = j + 1
            cast = [c for c in _as_list(_first(d, "cast", "characters", "cast_ids", default=[])) if isinstance(c, str)]
            finals = slmap.get((scene_no, order)) or []
            if not cast:   # 镜上没写 → 镜头表定稿镜的 characters → 所属组草案的 characters
                for f in finals:
                    for c in _as_list(_first(f, "characters", "cast", default=[])):
                        if isinstance(c, str) and c not in cast:
                            cast.append(c)
            if not cast:
                for c in grp_cast_by_order.get(order, []) + grp_cast_by_id.get(str(d.get("shot_id") or ""), []):
                    if c not in cast:
                        cast.append(c)
            for c in cast:
                if c not in cast_union:
                    cast_union.append(c)
            for a in _as_list(_first(d, "asset_refs", "props", default=[])):
                pid = a.get("id") if isinstance(a, dict) else a
                if isinstance(pid, str) and pid.startswith("PROP-") and pid not in props:
                    props.append(pid)
            dialogue = []
            for f in finals:
                for ln in f.get("dialogue_lines") or []:
                    if isinstance(ln, dict) and ln.get("text"):
                        sp = ln.get("speaker") or ""
                        dialogue.append({"speaker": sp, "name": names.get(sp, sp), "text": ln["text"],
                                         "est_s": ln.get("est_duration_s")})
            if not dialogue:
                dialogue = parse_dialogue_ref(_first(d, "dialogue_ref", "dialogue", "lines", default=None), names)
            key = shot_key(scene_no, order)
            shots.append({
                "key": key, "order": order,
                "shot_id": d.get("shot_id") or d.get("shot_id_ref") or "",
                "size_hint": _first(d, "size_hint", "size", "size_code", default=""),
                "content": str(_first(d, "content", "subject_action", "description", default="")),
                "action": str(_first(d, "action", default="")),
                "sketch": str(_first(d, "sketch", "composition_sketch", "camera_intent", "camera_movement_intent", default="")),
                "duration_hint_s": _first(d, "duration_hint_s", "duration_s", default=None),
                "cast": cast,
                "extras": str(_first(d, "extras", default="")),
                "dialogue": dialogue,
                "narration_ref": [str(x) for x in _as_list(_first(d, "narration_ref", "narration_refs", "narrator_ref", default=[]))],
                "beat": str(_first(d, "beat", default="")),
                "notes": str(_first(d, "notes", "note", default="")),
                "view_tile": d.get("view_tile"),
                "final": [{"shot_id": f.get("shot_id"), "duration_s": f.get("duration_s"),
                           "size": f.get("size") or f.get("size_code"), "group": None}
                          for f in finals],
            })
        # 定稿组号:generation_groups shots[] 反查
        grp_of = {}
        for g in sl.get("generation_groups") or []:
            if isinstance(g, dict):
                for s_id in g.get("shots") or []:
                    grp_of[s_id] = g.get("group_id")
        for s in shots:
            for f in s["final"]:
                f["group"] = grp_of.get(f["shot_id"])
        alloc = _first(sc, "unit_alloc_s", "alloc_s", "allocated_duration_s", "scene_duration_anchor_s", "duration_budget_s")
        draft_sum = _first(sc, "draft_sum_s", "shots_sum_s")
        if draft_sum is None:
            try:
                draft_sum = round(sum(float(s["duration_hint_s"] or 0) for s in shots), 1)
            except Exception:
                draft_sum = None
        final_sum = None
        try:
            fs = [float(f["duration_s"]) for s in shots for f in s["final"] if f.get("duration_s") is not None]
            final_sum = round(sum(fs), 1) if fs else None
        except Exception:
            pass
        scenes.append({
            "scene_no": scene_no, "scene_id": sid,
            "scene_name": (cat["scenes"].get(sid) or {}).get("name") or "",
            "scene_description": (cat["scenes"].get(sid) or {}).get("description") or "",
            "location": str(_first(sc, "location", "scene_name", "name", "display_name", default="")),
            "time_of_day": str(_first(sc, "time_of_day", default="")),
            "alloc_s": alloc, "draft_sum_s": draft_sum, "final_sum_s": final_sum,
            "note": str(_first(sc, "unit_note", "scene_note", "scene_intent", "beat", default="")),
            "color": str(_first(sc, "color_segment", "color_ref", default="")),
            "cast": _scene_cast(sc, cast_union, shots),
            "creatures": creatures, "props": props,
            "group_count": len(groups),
            "shots": shots,
        })
    return {
        "has_storyboard": bool(raw_scenes),
        "title": (sb.get("_meta") or {}).get("episode_title") or sb.get("title") or "",
        "storyboard_meta": {k: (sb.get("_meta") or {}).get(k) for k in ("task_id", "written_at", "status", "agent")},
        "scenes": scenes,
        "totals": {"scenes": len(scenes), "shots": sum(len(s["shots"]) for s in scenes),
                   "groups": sum(s["group_count"] for s in scenes),
                   "draft_sum_s": round(sum(float(s["draft_sum_s"] or 0) for s in scenes), 1) if scenes else None,
                   "final_total_s": sl.get("total_duration_s"), "budget_s": sl.get("budget_s"),
                   "final_shots": sl.get("shot_count"), "final_groups": sl.get("group_count")},
    }


def _scene_cast(sc: dict, cast_union: list, shots: list) -> list:
    """场次出场人物:场上 cast_ids/cast(分镜师显式写的)优先,否则各镜/各组并集;再补台词说话人。"""
    out = [c for c in _as_list(_first(sc, "cast_ids", "cast", "characters", default=[])) if isinstance(c, str)] or list(cast_union)
    for sh in shots:
        for ln in sh.get("dialogue") or []:
            sp = ln.get("speaker")
            if isinstance(sp, str) and sp.startswith("CHAR-") and sp not in out:
                out.append(sp)
    return out


def find_shot(board: dict, scene_no: str, order=None) -> tuple[dict | None, list[dict]]:
    """按场次(+序号)取归一化场与镜;order 为空时返回该场全部镜。"""
    for sc in board["scenes"]:
        if sc["scene_no"] == scene_no:
            if order is None:
                return sc, list(sc["shots"])
            return sc, [s for s in sc["shots"] if s["order"] == int(order)]
    return None, []


def find_shots_by_keys(board: dict, keys: list[str]) -> list[tuple[dict, dict]]:
    """按草图键(S01-03)在归一化故事板里找 (scene, shot),保持传入顺序;找不到的键跳过。"""
    idx = {sh["key"]: (sc, sh) for sc in board["scenes"] for sh in sc["shots"]}
    return [idx[k] for k in keys if k in idx]


def episode_shots(board: dict, scene_no: str | None = None) -> list[tuple[dict, dict]]:
    """整集(或某场)的 (scene, shot) 按集内顺序。"""
    return [(sc, sh) for sc in board["scenes"] if scene_no is None or sc["scene_no"] == scene_no for sh in sc["shots"]]


# ---------------- 草图提示词 + 参考图 ----------------

# 机位/神态关键词表(中文分镜文字 → 英文提示词短语;按出现顺序拼进 Camera: / Expressions: 句,2026-09-12)。
# 只做关键词命中,不做语义理解:分镜原文本身也整段进提示词,这里是把最影响构图与表演的信息再用英文点一次。
_CAMERA_TERMS = (
    ("俯拍", "high angle"), ("俯视", "high angle"), ("高机位", "high angle"), ("顶拍", "top-down"), ("顶视", "top-down"),
    ("鸟瞰", "bird's-eye view"), ("仰拍", "low angle"), ("仰视", "low angle"), ("低机位", "low angle"), ("低角度", "low angle"),
    ("平视", "eye level"), ("过肩", "over-the-shoulder"), ("主观", "POV"), ("POV", "POV"), ("正面", "frontal"),
    ("正对", "frontal"), ("侧面", "profile"), ("侧拍", "profile"), ("侧身", "three-quarter view"), ("背影", "from behind"),
    ("背对", "from behind"), ("背后", "from behind"), ("正反打", "shot/reverse shot"), ("反打", "reverse angle"),
    ("广角", "wide lens"), ("长焦", "long lens"), ("鱼眼", "fisheye lens"), ("微距", "macro"),
    ("倾斜", "dutch angle"), ("斜角", "dutch angle"), ("对称", "symmetrical composition"), ("剪影", "silhouette"),
    ("前景", "foreground element framing"), ("门框", "framed by a doorway"), ("窗框", "framed by a window"),
    ("推进", "push-in (draw the start frame)"), ("推近", "push-in (draw the start frame)"), ("拉远", "pull-out (draw the start frame)"),
    ("拉开", "pull-out (draw the start frame)"), ("横移", "lateral tracking"), ("跟拍", "following shot"), ("跟随", "following shot"),
    ("环绕", "orbit"), ("摇镜", "pan"), ("横摇", "pan"), ("摇摄", "pan"), ("升降", "crane"), ("手持", "handheld"),
)
_EXPRESSION_TERMS = (
    ("微笑", "smiling"), ("带笑", "smiling"), ("大笑", "laughing"), ("狂笑", "laughing wildly"), ("冷笑", "sneering"),
    ("苦笑", "wry smile"), ("皱眉", "frowning"), ("蹙眉", "frowning"), ("惊恐", "terrified"), ("惊讶", "surprised"),
    ("震惊", "shocked"), ("吃惊", "surprised"), ("惊", "startled"), ("哭", "crying"), ("泪", "tears"), ("愤怒", "angry"),
    ("怒", "angry"), ("冷漠", "cold and indifferent"), ("冷淡", "cold"), ("紧张", "tense"), ("恐惧", "fearful"),
    ("害怕", "afraid"), ("疑惑", "puzzled"), ("困惑", "confused"), ("不解", "puzzled"), ("警惕", "wary"),
    ("沉默", "silent, lips pressed"), ("悲伤", "sad"), ("难过", "sad"), ("疲惫", "exhausted"), ("得意", "smug"),
    ("严肃", "stern"), ("面无表情", "blank face"), ("木然", "blank face"), ("呆滞", "dazed"), ("茫然", "blank, lost"),
    ("瞪", "glaring"), ("凝视", "staring"), ("盯", "staring"), ("对视", "locking eyes"), ("低头", "head lowered"),
    ("抬头", "looking up"), ("回头", "looking back over the shoulder"), ("侧目", "glancing sideways"),
    ("视线", "clear eye line"), ("目光", "clear eye line"), ("看向", "looking toward"), ("闭眼", "eyes closed"),
    ("咬牙", "jaw clenched"), ("颤抖", "trembling"), ("喘", "panting"), ("屏息", "holding breath"),
    ("犹豫", "hesitant"), ("决绝", "resolute"), ("绝望", "despairing"), ("释然", "relieved"), ("温柔", "gentle"),
    ("挣扎", "struggling"), ("蜷缩", "curled up"), ("瘫", "slumped"), ("僵住", "frozen stiff"), ("僵在", "frozen stiff"),
)


def _term_hits(text: str, table) -> list[str]:
    """按关键词在文字里的出现位置排序,去重返回英文短语。"""
    found = []
    for zh, en in table:
        i = text.find(zh)
        if i >= 0 and en not in (e for _, e in found):
            found.append((i, en))
    return [en for _, en in sorted(found)]


def camera_hint(shot: dict) -> str:
    """从 sketch/content/action 推导机位英文短语(角度/高度/镜头/朝向/运镜),无命中返回空。"""
    text = " ".join(str(shot.get(k) or "") for k in ("sketch", "content", "action"))
    return ", ".join(_term_hits(text, _CAMERA_TERMS)[:6])


def expression_hint(shot: dict) -> str:
    """从 content/action/sketch 推导神态/视线英文短语,无命中返回空。"""
    text = " ".join(str(shot.get(k) or "") for k in ("content", "action", "sketch"))
    return ", ".join(_term_hits(text, _EXPRESSION_TERMS)[:8])


def _space_hint(scene: dict, max_chars: int = 60) -> str:
    """地点只留一句短提示(地点/场名 + 时段,截到 max_chars),不带场景卡描述——背景只是示意。"""
    loc = " ".join(x for x in (scene.get("location") or scene.get("scene_name") or "", scene.get("time_of_day") or "")
                   if x and x not in ("未知", "unknown"))
    return _short(loc, max_chars)


def build_prompt(scene: dict, shot: dict, names: dict, note: str = "") -> tuple[str, str]:
    """单镜提示词(2026-09-12 人物优先):风格句 → 景别 → 机位 → 出场 → 画面/动作 → 神态 → 构图 → 群众 → 地点短提示(最后,只作示意) → 修改意见。"""
    parts = [SKETCH_STYLE_PROMPT]
    if shot.get("size_hint"):
        parts.append(f"Shot size: {shot['size_hint']}.")
    cam = camera_hint(shot)
    if cam:
        parts.append(f"Camera: {cam}.")
    cast = [names.get(c, c) for c in shot.get("cast") or []]
    if cast:
        parts.append("Characters in frame: " + ", ".join(cast) + ".")
    if shot.get("content"):
        parts.append(f"What we see: {shot['content']}")
    if shot.get("action") and shot["action"] not in (shot.get("content") or ""):
        parts.append(f"Action: {shot['action']}")
    ex = expression_hint(shot)
    if ex:
        parts.append(f"Expressions and gestures to make readable: {ex}.")
    if shot.get("sketch"):
        parts.append(f"Composition: {shot['sketch']}")
    if shot.get("extras"):
        parts.append(f"Background extras (loose figures only): {shot['extras']}")
    # 场景只靠文字(2026-09-11 用户拍板);2026-09-12 起只留一句短地点提示,放最后,不再拼场景卡描述
    space = _space_hint(scene)
    if space:
        parts.append(f"Space hint (background stays a few faint lines): {space}.")
    if note and note.strip():
        parts.append(f"Revision instruction (takes priority): {note.strip()}")
    return " ".join(parts), SKETCH_NEGATIVE


def _shrink(src: Path, cache: Path) -> Path:
    """参考图缩到 REF_MAX_EDGE 长边的 JPEG(按源路径+mtime 哈希缓存);PIL 不可用或失败则原图。"""
    try:
        from PIL import Image
    except Exception:
        return src
    cache.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha1(f"{src}|{int(src.stat().st_mtime)}|{REF_MAX_EDGE}".encode()).hexdigest()[:16]
    out = cache / f"{h}.jpg"
    if out.is_file():
        return out
    try:
        im = Image.open(src)
        im.thumbnail((REF_MAX_EDGE, REF_MAX_EDGE))
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        im.save(out, "JPEG", quality=85)
        return out
    except Exception:
        return src


def collect_refs(base: Path, ep: str, scene: dict, shot: dict, catalog: dict) -> list[Path]:
    """只带出场人物 sheet(≤MAX_CAST_REFS),缩小后返回绝对路径。不带场景图(俯视图会误导模型,场景靠文字)、不带风格参考图。"""
    cache = sketch_dir(base, ep) / "_refcache"
    refs: list[Path] = []
    n = 0
    for cid in shot.get("cast") or []:
        rec = catalog["characters"].get(cid) or catalog["creatures"].get(cid) or {}
        if rec.get("file") and (base / rec["file"]).is_file() and n < MAX_CAST_REFS:
            refs.append(_shrink(base / rec["file"], cache))
            n += 1
    return refs


def grid_layout(n: int) -> tuple[int, int]:
    """按镜数选宫格:1 镜单张(调用方走单镜路径)、2–4 镜 2×2(GRID_MAX_PANELS=4,2026-09-12 起不再出 3×3);
    超过 4 镜只作兜底返回 3×3,正常调用方已按 GRID_MAX_PANELS 分批。返回 (cols, rows)。"""
    if n <= 1:
        return 1, 1
    if n <= 4:
        return 2, 2
    return 3, 3


def _short(s, n: int) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


GRID_PROMPT_MAX = 2400       # 宫格提示词字符上限(4 格合一,按三档收紧逐格文字直到不超)
# 2026-09-12 收紧顺序改为先砍地点(desc 第一档就不进),再压画面内容;机位/神态短语与构图最后才压——草图要的是机位、比例、神态、动作。
_GRID_CLIPS = ({"desc": 0, "content": 260, "action": 160, "sketch": 200, "note": 160},
               {"desc": 0, "content": 180, "action": 100, "sketch": 140, "note": 120},
               {"desc": 0, "content": 120, "action": 60, "sketch": 90, "note": 90})


def build_grid_prompt(panels: list[tuple[dict, dict]], names: dict, cols: int, rows: int, aspect: str = "16:9",
                      max_chars: int = GRID_PROMPT_MAX) -> tuple[str, str]:
    """宫格提示词:风格总句 + 各场地点短提示一次(只作示意) + 逐格「Panel k (row r, col c)」景别/机位/地点/出场/画面/动作/神态/构图(用台账 note)。
    panels = [(scene, shot)],≤ cols*rows;格数不满时说明剩余格留白(切分时只取前 n 格)。
    总长超 max_chars 时按 _GRID_CLIPS 三档收紧逐格文字(2026-09-12:地点描述不进,先压画面内容,机位/神态/构图最后压)。"""
    n = len(panels)
    cells = cols * rows
    blank = f" The last {cells - n} cell(s) of the grid stay blank white." if n < cells else ""
    head = GRID_STYLE_PROMPT.format(cols=cols, rows=rows, n=cells, aspect=aspect or "16:9", blank=blank)
    prompt = ""
    for clip in _GRID_CLIPS:
        parts = [head]
        seen = []
        for sc, _ in panels:
            key = sc.get("scene_no")
            if key in seen:
                continue
            seen.append(key)
            loc = _space_hint(sc)
            desc = _short(sc.get("scene_description"), clip["desc"]) if clip["desc"] else ""
            if loc or desc:
                parts.append(f"Location {key} (background stays a few faint lines): {loc}{'. ' + desc if desc else ''}.")
        for k, (sc, shot) in enumerate(panels, 1):
            r, c = (k - 1) // cols + 1, (k - 1) % cols + 1
            seg = [f"Panel {k} (row {r}, column {c}):"]
            if shot.get("size_hint"):
                seg.append(f"{shot['size_hint']} shot.")
            cam = camera_hint(shot)
            if cam:
                seg.append(f"Camera: {cam}.")
            if len(seen) > 1:
                seg.append(f"Location {sc.get('scene_no')}.")
            cast = [names.get(x, x) for x in shot.get("cast") or []]
            if cast:
                seg.append("Characters: " + ", ".join(cast) + ".")
            if shot.get("content"):
                seg.append(_short(shot["content"], clip["content"]))
            if clip["action"] and shot.get("action") and shot["action"] not in (shot.get("content") or ""):
                seg.append("Action: " + _short(shot["action"], clip["action"]))
            ex = expression_hint(shot)
            if ex:
                seg.append(f"Expressions: {ex}.")
            if clip["sketch"] and shot.get("sketch"):
                seg.append("Composition: " + _short(shot["sketch"], clip["sketch"]))
            note = str(shot.get("_note") or "").strip()
            if note:
                seg.append("Revision instruction: " + _short(note, clip["note"]))
            parts.append(" ".join(seg))
        prompt = " ".join(parts)
        if len(prompt) <= max_chars:
            break
    return prompt, GRID_NEGATIVE


def collect_grid_refs(base: Path, ep: str, panels: list[tuple[dict, dict]], catalog: dict) -> list[Path]:
    """宫格模式参考图:各格(≤4)出场人物并集,按出场格数降序取前 GRID_MAX_CAST_REFS 张 sheet(缩小后)。"""
    cache = sketch_dir(base, ep) / "_refcache"
    freq: dict[str, int] = {}
    for _, shot in panels:
        for cid in shot.get("cast") or []:
            freq[cid] = freq.get(cid, 0) + 1
    refs: list[Path] = []
    for cid, _ in sorted(freq.items(), key=lambda kv: (-kv[1], kv[0])):
        rec = catalog["characters"].get(cid) or catalog["creatures"].get(cid) or {}
        if rec.get("file") and (base / rec["file"]).is_file():
            refs.append(_shrink(base / rec["file"], cache))
        if len(refs) >= GRID_MAX_CAST_REFS:
            break
    return refs


def split_grid(src: Path, cols: int, rows: int, n: int, outs: list[Path], trim: float = GRID_CELL_TRIM) -> list[Path]:
    """把宫格图等分成 cols×rows 格,按行优先取前 n 格,四边各裁 trim 去格线,存到 outs[k](PNG)。返回写出的路径。"""
    from PIL import Image
    im = Image.open(src).convert("RGB")
    W, H = im.size
    cw, ch = W / cols, H / rows
    tx, ty = cw * trim, ch * trim
    written = []
    for k in range(min(n, cols * rows, len(outs))):
        r, c = k // cols, k % cols
        box = (int(c * cw + tx), int(r * ch + ty), int((c + 1) * cw - tx), int((r + 1) * ch - ty))
        tile = im.crop(box)
        outs[k].parent.mkdir(parents=True, exist_ok=True)
        tile.save(outs[k], "PNG", optimize=True)
        written.append(outs[k])
    return written


def grid_size(provider: str, aspect: str) -> str:
    """宫格图出图尺寸:所有渠道都按 3,686,400 像素当量(2560×1440)出,切 2×2 后每格约 1230×690(裁边后);不再压缩。"""
    try:
        rw, rh = (int(x) for x in str(aspect or "16:9").split(":"))
    except Exception:
        rw, rh = 16, 9
    pixels = 3_686_400
    w = -(-int((pixels * rw / rh) ** 0.5) // 8) * 8
    h = -(-(w * rh) // (rw * 8)) * 8
    return f"{w}x{h}"


def resolve_aspect(base: Path) -> str:
    """项目视频画幅(settings.json output.aspect_preset/aspect_custom);缺省 16:9。"""
    try:
        from modules.output_format import resolve_output
        cfg = _read_json(base / "settings.json") or {}
        aspect, _, _ = resolve_output(cfg)
        return aspect or "16:9"
    except Exception:
        return "16:9"


def sketch_size(provider: str, aspect: str) -> str:
    """出图尺寸:火山/BytePlus 的 Seedream 5.x 硬限「≥3,686,400 像素」(2560x1440 当量,与宿主手绘生图同口径),
    否则按 3686400 当量出、成图再压到 1280 长边;其它渠道走 1280x720 当量(genmedia ASPECT_SIZES,多数接口的最小档)。返回 "WxH"。"""
    try:
        rw, rh = (int(x) for x in str(aspect or "16:9").split(":"))
    except Exception:
        rw, rh = 16, 9
    pixels = 3_686_400 if provider in ("volcengine", "byteplus") else 921_600
    w = -(-int((pixels * rw / rh) ** 0.5) // 8) * 8
    h = -(-(w * rh) // (rw * 8)) * 8
    return f"{w}x{h}"


def animatic_status(base: Path, ep: str) -> dict:
    """动态样片现状(code/animatic.py 产物 assets/storyboard/<ep>/animatic.mp4 + animatic.json):
    存在与否、元数据、是否过期(storyboard.json / shot_list.json / 任一草图比样片新)、当前缺草图数。"""
    d = sketch_dir(base, ep)
    mp4, meta = d / "animatic.mp4", _read_json(d / "animatic.json") or {}
    idx = load_index(base, ep)
    done = [r for r in idx["shots"].values() if isinstance(r, dict) and r.get("status") == "done"
            and r.get("file") and (base / r["file"]).is_file()]
    latest = 0.0
    for f in (base / "directing" / ep / "storyboard.json", base / "directing" / ep / "shot_list.json"):
        if f.is_file():
            latest = max(latest, f.stat().st_mtime)
    for r in done:
        latest = max(latest, (base / r["file"]).stat().st_mtime)
    out = {"exists": mp4.is_file(), "path": mp4.relative_to(base).as_posix(), "sketches_done": len(done)}
    if mp4.is_file():
        st = mp4.stat()
        out.update({"name": mp4.name, "size_mb": round(st.st_size / 1048576, 1), "mtime": int(st.st_mtime),
                    "url": f"/projects/{base.name}/{out['path']}?v={int(st.st_mtime)}",
                    "duration_s": meta.get("duration_s"), "shots": meta.get("shots"),
                    "missing_sketches": meta.get("missing_sketches"), "duration_source": meta.get("duration_source"),
                    "audio_tracks": meta.get("audio_tracks"), "created_at": meta.get("created_at"),
                    "stale": latest > float(meta.get("inputs_mtime") or st.st_mtime) + 1})
    return out


def sketch_url(base: Path, rec: dict) -> str | None:
    f = base / str(rec.get("file") or "")
    if rec.get("file") and f.is_file():
        return f"/projects/{base.name}/{rec['file']}?v={int(f.stat().st_mtime)}"
    return None
