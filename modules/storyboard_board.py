"""故事板(2026-09-11):分镜层(07-directing/storyboard)产物 storyboard.json 的归一化视图 + 分镜草图台账。

页面 /preview/board 与草图 CLI code/storyboard_sketch.py 共用本模块:
- load_board(base, ep):把各历史版本 storyboard.json(字段名多次演化)归一成同一结构,
  并按 shot_list.json 的 storyboard_ref 把定稿镜号/时长/台词对回草案镜;
- 草图台账 assets/storyboard/<ep>/index.json(schema storyboard_sketches/1.0):
  shots[<S01-01>] = {file, status(queued|running|done|failed), error, prompt, note, provider, model, refs, updated_at}
  (mode=hand / provider=hand_drawn 表示故事板页「✍️ 手绘」手机画布直接落盘的草图,2026-09-16,不经 AI;重出时渠道回落偏好)
  图片 assets/storyboard/<ep>/<S01-01>.png;台账写入经 flock 串行,宿主后台任务与 Agent 的 CLI 进程可并发;
- build_prompt / collect_refs:草图提示词(铅笔手绘分镜风格,英文风格句 + 原文画面内容)与参考图
  (只带出场人物 sheet,缩到 512px 长边;不带场景图——俯视布局图会误导图像模型,场景只靠文字描述;
  也不用风格参考图,铅笔风格全靠提示词——2026-09-11 用户拍板);
  **人物优先、背景留白**(2026-09-12 用户拍板:没有合适的场景参考图,草图弱化场景展现,主要按分镜表现
  镜头机位、人物比例、神态、动作):风格句要求人物线稿清晰、按景别画对人物比例、表情与肢体可读,背景只
  两三笔示意或留白;地点只留一句短提示放在最后,不再拼场景卡描述;从 content/sketch/action 文字里自动推导
  「Camera:」(角度/高度/镜头/朝向)与「Expressions:」(神态/视线)两句英文关键词加进提示词(camera_hint / expression_hint);
  **人物姿态/动作(2026-09-14 用户拍板)**:每镜再加一句「Body poses and actions:」——优先用分镜层结构化字段
  `shots_draft[].poses`(`{CHAR-id: {pose: stand|sit|lie|kneel|crouch|prone, action: "挥剑"}}`,pose 受控枚举由宿主映射成
  英文,action 中文直通不翻译),存量项目没有该字段时退回从 content/action/sketch 文字按关键词表推导(pose_hint);
  之前草图里人物站/坐/躺、奔跑/挥剑/闪躲全靠中文长散文,姿态词淹没在句中、宫格模式还会被截掉,模型基本不听;
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
HAND_DRAWN_PROVIDER = "hand_drawn"           # 台账 provider 记号:故事板页「✍️ 手绘」直接落盘的草图(mode=hand),不是图像模型出的
REF_MAX_EDGE = 512           # 参考图缩放长边(草图只需形象/空间提示,不需要高清)
MAX_CAST_REFS = 3            # 人物参考图上限(多了反而稀释风格参考)
STATUSES = ("queued", "running", "done", "failed")

# 2026-09-12 用户拍板:人物优先、背景留白——草图只为看机位、人物比例、神态、动作,场景不展开(没有合适的场景参考图)。
SKETCH_STYLE_PROMPT = (
    "Film storyboard panel, rough pencil sketch on white paper: hand-drawn monochrome line art with loose "
    "hatching and soft grey marker shading, quick gestural strokes, unfinished sketchbook look. "
    "FIGURES FIRST: draw the characters with clear confident lines, correct body proportions and figure size "
    "for the stated shot size, readable facial expressions, eye lines and body gestures; faces must be clear "
    "enough to read the emotion. Pose every figure exactly as stated (standing, sitting, kneeling, crouching, "
    "lying down, running, swinging a weapon, dodging…); the body state is as important as the face. "
    "BACKGROUND MINIMAL: only two or three loose lines or a little light hatching to hint at the space, most of "
    "the paper left blank; no architectural detail, no furniture detail, no props unless mentioned. "
    "The framing must show the camera angle, camera height and lens exactly as described (eye level, high "
    "angle, low angle, over-the-shoulder, profile, from behind). "
    "Strictly black-and-white, no color. A single frame, no panel borders, no text, no captions, no speech "
    "bubbles, no watermark. "
)
# 风格句结尾按有无参考图二选一(2026-09-17):有参考图 → 附图是人物设定;无参考图(comfyui 纯文生图)→ 人物按文字画
SKETCH_REFS_SENTENCE = (
    "The attached images are the project's official character designs — keep each character's likeness, "
    "hairstyle and outfit, but redraw everything as a pencil sketch; the location is described in text only "
    "and stays a faint hint."
)
# 纯文生图整句风格提示(2026-09-17):不用 SKETCH_STYLE_PROMPT——Z-Image 这类 cfg=1 的本地模型不吃 negative,
# 会把风格句里列举的姿态(standing, sitting, kneeling…)和「no panel borders」之类否定句照字面画成多格姿势表;
# 这里只写正向描述、不列举姿态、不出现「panel/sheet/grid」字样。
SKETCH_STYLE_PROMPT_TEXT_ONLY = (
    "One rough pencil sketch on white paper filling the whole page: hand-drawn monochrome line art with loose "
    "hatching and soft grey marker shading, quick gestural strokes, unfinished sketchbook look. It is a single "
    "moment from a film, seen through the camera once, drawn as one picture. FIGURES FIRST: clear confident "
    "lines for the characters, correct body proportions and figure size for the stated shot size, readable "
    "facial expressions, eye lines and body gestures; each figure holds exactly the body position the shot "
    "describes. BACKGROUND MINIMAL: two or three loose lines or a little light hatching to hint at the space, "
    "most of the paper left blank. The framing shows the stated camera angle, camera height and lens. "
    "Strictly black-and-white. Characters are drawn from the text description alone (age, build, hairstyle, "
    "outfit as stated); the location is described in text only and stays a faint hint."
)
# 纯文生图渠道(2026-09-17 用户拍板):comfyui(本地 / Comfy Cloud / RunningHub)的「参考图」是 img2img 初始画面,
# 传人物 sheet 会把构图锁死成设定稿且压不住写实底图,多张还直接报错;草图一律不传参考图,按提示词画铅笔草图。
TEXT_ONLY_SKETCH_PROVIDERS = ("comfyui",)
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
    "and body gestures; pose every figure exactly as that panel states (standing, sitting, kneeling, crouching, "
    "lying down, running, swinging, dodging…). BACKGROUND MINIMAL in every panel: only two or three loose lines to hint at the space, most "
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


# ---------------- 用户注释(2026-09-15):故事板页「🗒 注释」,分镜设计的参考 ----------------
NOTES_REL = "directing/{ep}/storyboard_notes.json"
NOTES_SCHEMA = "storyboard_notes/1.0"
NOTE_MAX_CHARS = 2000
_NOTE_KEY_RE = re.compile(r"^(\*|[A-Za-z0-9_\-]{1,40})$")     # "*" 整集 / "S01" 场次 / "S01-03" 镜


def notes_path(base: Path, ep: str) -> Path:
    return base / NOTES_REL.format(ep=ep)


def note_key_ok(key: str) -> bool:
    return bool(_NOTE_KEY_RE.match(key or ""))


def note_level(key: str) -> str:
    """注释键的层级:episode(*)/ scene(S01)/ shot(S01-03)。"""
    if key == "*":
        return "episode"
    return "shot" if "-" in key else "scene"


def load_notes(base: Path, ep: str) -> dict:
    """{schema, ep, notes: {key: {text, level, updated_at, scene_no?, order?, shot_id?, content?}}}。文件缺失/坏 → 空。"""
    d = _read_json(notes_path(base, ep)) or {}
    if not isinstance(d.get("notes"), dict):
        d = {"schema": NOTES_SCHEMA, "ep": ep, "notes": {}}
    d.setdefault("schema", NOTES_SCHEMA)
    d.setdefault("ep", ep)
    d["notes"] = {k: v for k, v in d["notes"].items() if isinstance(v, dict) and (v.get("text") or "").strip()}
    return d


def update_note(base: Path, ep: str, key: str, text: str, meta: dict | None = None) -> dict:
    """写/改/删一条注释(flock 串行,与台账同一套路);text 空 = 删除。返回改后的整份文件内容。
    meta(scene_no/order/shot_id/content 等)随条目存盘:分镜重做后镜序可能变,Agent 靠这些字段对回原镜。"""
    if not note_key_ok(key):
        raise ValueError(f"bad note key: {key!r}")
    text = (text or "").strip()
    if len(text) > NOTE_MAX_CHARS:
        raise ValueError(f"note too long (>{NOTE_MAX_CHARS} chars)")
    p = notes_path(base, ep)
    p.parent.mkdir(parents=True, exist_ok=True)
    lock = p.with_suffix(".lock")
    with open(lock, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            d = load_notes(base, ep)
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            if not text:
                d["notes"].pop(key, None)
            else:
                rec = d["notes"].get(key) or {"created_at": now}
                rec.update({k: v for k, v in (meta or {}).items() if v not in (None, "")})
                rec.update({"text": text, "level": note_level(key), "updated_at": now})
                d["notes"][key] = rec
            d["updated_at"] = now
            d["_readme"] = ("用户在「📋 故事板」页写的注释,按键分三级:* = 整集,S01 = 场次,S01-03 = 场次-草案镜序。"
                            "分镜师(storyboard)重做本集分镜、镜头表工位(shot-planning)定稿镜头表、修改师改分镜时,"
                            "必须先读本文件,把每条注释当作用户对分镜设计的意见/约束:能落实的落实,不能落实的在汇报里说明原因。"
                            "镜级条目带 scene_no/order/shot_id/content(写注释时那一镜的内容摘要),重拆镜后镜序变了就按 content 对回原镜。")
            if not d["notes"]:
                p.unlink(missing_ok=True)
                d["notes"] = {}
            else:
                tmp = p.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1))
                os.replace(tmp, p)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)
    return d


def note_meta(board: dict, key: str) -> dict:
    """按注释键从归一化故事板取定位元数据(镜级:scene_no/order/shot_id/content 摘要;场级:scene_no/location)。"""
    if key == "*":
        return {"title": board.get("title") or ""}
    for sc in board.get("scenes") or []:
        if key == sc.get("scene_no"):
            return {"scene_no": sc["scene_no"], "scene_id": sc.get("scene_id") or "", "location": sc.get("location") or ""}
        for sh in sc.get("shots") or []:
            if sh.get("key") == key:
                return {"scene_no": sc["scene_no"], "order": sh.get("order"), "shot_id": sh.get("shot_id") or "",
                        "content": (sh.get("content") or "")[:80]}
    return {}


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
    for c in cr.get("creatures") or cr.get("entries") or []:      # 有的项目(liaozhai3)生物库顶层键是 entries
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


def board_scene_no(sc: dict, i: int) -> str:
    """storyboard.json 场块的场次号(S01 式):scene_no 缺则回落 screenplay_ref / no / 序号。"""
    return str(_first(sc, "scene_no", "screenplay_ref", "no", default=f"S{i + 1:02d}"))


def _scene_drafts(sc: dict) -> list:
    return sc.get("shots_draft") if isinstance(sc.get("shots_draft"), list) else \
        (sc.get("shots") if isinstance(sc.get("shots"), list) else [])


def _draft_order(d: dict, j: int) -> int:
    order = d.get("order") if d.get("order") is not None else j + 1
    try:
        return int(order)
    except Exception:
        return j + 1


def board_targets(sb: dict) -> tuple[list[str], set[str]]:
    """跨预览页跳转用(2026-09-12):storyboard.json 里实际存在的 (场次号列表, 草案镜键集合 S01-03);
    剧本/分镜预览只对存在的目标显示「📋 故事板」链接。"""
    raw = sb.get("scenes") if isinstance(sb.get("scenes"), list) else []
    nos: list[str] = []
    keys: set[str] = set()
    for i, sc in enumerate(raw):
        if not isinstance(sc, dict):
            continue
        no = board_scene_no(sc, i)
        nos.append(no)
        for j, d in enumerate(_scene_drafts(sc)):
            if isinstance(d, dict):
                keys.add(shot_key(no, _draft_order(d, j)))
    return nos, keys


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
        scene_no = board_scene_no(sc, i)
        sid = sc.get("scene_id") or ""
        drafts = _scene_drafts(sc)
        shots, cast_union, groups = [], [], sc.get("groups_draft") if isinstance(sc.get("groups_draft"), list) else []
        creatures, props = [], []
        # 组草案的出场角色 → 按 shot_orders/shot_ids 落到镜(有的项目如 liaozhai2 只在组上写 characters,镜上没有 cast)
        grp_cast_by_order: dict[int, list] = {}
        grp_cast_by_id: dict[str, list] = {}
        for g in groups:
            if isinstance(g, dict):
                for c in _as_list(g.get("creatures")) + _as_list(g.get("creatures_union")):
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
            order = _draft_order(d, j)
            cast = [c for c in _as_list(_first(d, "cast", "characters", "cast_ids", default=[])) if isinstance(c, str)]
            finals = slmap.get((scene_no, order)) or []
            # 本场出场生物(2026-09-17):组草案之外,镜草案 / 镜头表定稿镜上登记的也算(有的项目只在镜上写 creatures)
            for c in _as_list(d.get("creatures")) + [x for f in finals for x in _as_list(f.get("creatures"))]:
                if isinstance(c, str) and c not in creatures:
                    creatures.append(c)
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
                "poses": normalize_poses(_first(d, "poses", "figure_states", default=None)),
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
    narration = load_narration(base, ep)
    narr_summary = attach_narration(scenes, narration, sl, names)
    return {
        "has_storyboard": bool(raw_scenes),
        "title": (sb.get("_meta") or {}).get("episode_title") or sb.get("title") or "",
        "storyboard_meta": {k: (sb.get("_meta") or {}).get(k) for k in ("task_id", "written_at", "status", "agent")},
        "scenes": scenes,
        "narration": narr_summary,
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


# ---------------- 旁白挂镜(2026-09-15) ----------------
# 旁白稿 story/episodes/<ep>/narration.md 在 Phase 1 就定稿,每条只锚到「场次 + 剧本动作行」;精确到镜的挂点有两个来源:
#   ① 分镜师在草案镜写 narration_ref: ["N-01"](2026-09-15 起为必填,机检 narration_ref_ok);
#   ② shot-planning 定稿的 shot_list narration_anchors(H3S 之后才有)。
# 两者都没有(存量项目 / H3S 前)时按锚点文字推定:锚点里「…」引的剧本动作行与各镜 content/action 做字符二元组相似度,
# 「场首/场末」关键词兜底;推不出的挂在场块顶部「未定位」。页面与动态样片都用这里的结果,不各自再猜。

NARRATION_SOURCES = ("storyboard", "shot_list", "anchor_text", "scene", "unplaced")
_NARR_SIM_MIN = 0.25         # 锚点引文二元组被镜文字覆盖的最低比例(liaozhai3 ep01 实测命中 0.4–0.9、误配 <0.15)
_QUOTE_RE = re.compile(r"[「『“\"]([^」』”\"]{2,})[」』”\"]")


def load_narration(base: Path, ep: str) -> list[dict]:
    """本集旁白稿 → [{id, text, est_s, anchor, scene, tone}](复用 script_breakdown 的解析器;md 优先、json 兜底)。"""
    from modules import script_breakdown as sbd
    epdir = base / "story" / "episodes" / ep
    md = sbd.read_text(epdir / "narration.md")
    if md:
        items = sbd.parse_narration_md(md)
    else:
        items = sbd.parse_narration_json(sbd.read_json(epdir / "narration.json") or {})
    return [it for it in items if it.get("id") and it.get("text")]


def _bigrams(s: str) -> set:
    s = re.sub(r"[\s,，。.;；:：、!！?？「」『』“”\"'()（）\[\]…—-]", "", s or "")
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else set()


def _anchor_quote(anchor: str, names: dict) -> str:
    """锚点第四段里「…」引的剧本动作行(多段引文拼一起);CHAR-id 换成人名,便于与镜文字比。"""
    qs = _QUOTE_RE.findall(anchor or "")
    q = "".join(qs) if qs else ""
    for cid, nm in (names or {}).items():
        q = q.replace(cid, nm)
    return q


def _guess_shot(anchor: str, shots: list[dict], names: dict) -> tuple[dict | None, float]:
    """按锚点文字在场内定镜:引文相似度优先,其次 场首/场末 关键词;返回 (镜, 相似度)。"""
    if not shots:
        return None, 0.0
    quote = _anchor_quote(anchor, names)
    best, best_sc = None, 0.0
    if quote:
        qb = _bigrams(quote)
        if qb:
            for sh in shots:
                body = " ".join([sh.get("content") or "", sh.get("action") or "", sh.get("sketch") or ""])
                sc = len(qb & _bigrams(body)) / len(qb)
                if sc > best_sc:
                    best, best_sc = sh, sc
    if best is not None and best_sc >= _NARR_SIM_MIN:
        return best, round(best_sc, 2)
    a = anchor or ""
    if re.search(r"场首|开场|场头|片首|集首|开头", a):
        return shots[0], 0.0
    if re.search(r"场末|场尾|收尾|结尾|片尾|集末|末尾", a):
        return shots[-1], 0.0
    return None, round(best_sc, 2)


def attach_narration(scenes: list[dict], narration: list[dict], sl: dict, names: dict | None = None) -> dict:
    """把旁白条目挂到归一化场/镜上(就地改):每镜 narration[] = [{id, text, est_s, anchor, source, sim}],
    每场 narration_unplaced[](锚到本场但定不到镜),返回集级汇总 {items, placed, by_source, unplaced[]}。
    优先级:草案镜 narration_ref → shot_list narration_anchors → 锚点文字推定 → 场级未定位 → 集级未定位。"""
    names = names or {}
    by_id = {n["id"]: n for n in narration}
    by_scene_no = {sc["scene_no"]: sc for sc in scenes}
    by_scene_id = {sc["scene_id"]: sc for sc in scenes if sc.get("scene_id")}
    for sc in scenes:
        sc["narration_unplaced"] = []
        for sh in sc["shots"]:
            sh["narration"] = []
    placed: dict[str, list] = {}

    def _put(sh: dict, n: dict, source: str, sim: float = 0.0):
        sh["narration"].append({"id": n["id"], "text": n.get("text") or "", "est_s": n.get("est_s"),
                                "anchor": n.get("anchor") or "", "source": source, "sim": sim})
        placed.setdefault(n["id"], []).append(source)

    # ① 草案镜 narration_ref(分镜师明确挂点);引了旁白稿里没有的 id 也照显示(text 空),机检会报
    for sc in scenes:
        for sh in sc["shots"]:
            for nid in sh.get("narration_ref") or []:
                _put(sh, by_id.get(nid) or {"id": nid, "text": "", "est_s": None, "anchor": ""}, "storyboard")
    # ② shot_list narration_anchors:首个挂点镜(定稿 shNNN)反查草案镜
    draft_of_final = {f["shot_id"]: sh for sc in scenes for sh in sc["shots"] for f in sh.get("final") or [] if f.get("shot_id")}
    for a in (sl or {}).get("narration_anchors") or []:
        if not isinstance(a, dict):
            continue
        nid = a.get("narration_id")
        n = by_id.get(nid)
        if not n or nid in placed:
            continue
        for fid in _as_list(a.get("anchor_shots")):
            sh = draft_of_final.get(fid)
            if sh is not None:
                _put(sh, n, "shot_list")
                break
    # ③ 锚点文字推定 / ④ 场级未定位 / ⑤ 集级未定位
    unplaced_ep: list[dict] = []
    for n in narration:
        if n["id"] in placed:
            continue
        sc = by_scene_no.get(str(n.get("scene") or "").upper())
        if sc is None and n.get("anchor"):
            m = re.search(r"SCN-\d+", n["anchor"])
            if m:
                sc = by_scene_id.get(m.group(0))
        rec = {"id": n["id"], "text": n.get("text") or "", "est_s": n.get("est_s"), "anchor": n.get("anchor") or ""}
        if sc is None:
            unplaced_ep.append({**rec, "source": "unplaced"})
            continue
        sh, sim = _guess_shot(n.get("anchor") or "", sc["shots"], names)
        if sh is not None:
            _put(sh, n, "anchor_text", sim)
        else:
            sc["narration_unplaced"].append({**rec, "source": "scene", "sim": sim})
            placed.setdefault(n["id"], []).append("scene")
    by_source: dict[str, int] = {}
    for srcs in placed.values():
        by_source[srcs[0]] = by_source.get(srcs[0], 0) + 1
    return {"items": len(narration), "placed": sum(1 for n in narration if n["id"] in placed),
            "by_source": by_source, "unplaced": unplaced_ep,
            "total_est_s": round(sum(float(n.get("est_s") or 0) for n in narration), 1)}


def check_narration(board: dict, narration: list[dict], strict: bool = False) -> tuple[list[str], list[str]]:
    """旁白挂点机检 narration_ref_ok(2026-09-15):
    - 草案镜 narration_ref 引的 id 必须在旁白稿里(FAIL);同一条挂到多镜 WARN(旁白只在一处起播);
    - 旁白稿每条必须被某镜 narration_ref 引用(缺省 WARN——存量项目靠锚点推定;--strict FAIL,新项目交付前);
    - 引用它的镜所在场与锚点场次不一致(FAIL:挂错场);
    - 该镜(或所在场)时长建议明显装不下 est_duration_s(WARN,定稿窗口由 shot-planning 复核)。"""
    errs, warns = [], []
    by_id = {n["id"]: n for n in narration}
    ref_at: dict[str, list[tuple[dict, dict]]] = {}
    for sc in board["scenes"]:
        for sh in sc["shots"]:
            for nid in sh.get("narration_ref") or []:
                ref_at.setdefault(nid, []).append((sc, sh))
                if nid not in by_id:
                    errs.append(f"{sh['key']}: narration_ref {nid} 不在旁白稿里")
    for nid, locs in ref_at.items():
        if len(locs) > 1:
            warns.append(f"{nid}: 挂到了 {len(locs)} 镜({', '.join(sh['key'] for _, sh in locs)}),旁白只在首镜起播、其余镜应留空")
        n = by_id.get(nid)
        if not n:
            continue
        want = str(n.get("scene") or "").upper()
        for sc, sh in locs:
            if want and sc["scene_no"].upper() != want:
                errs.append(f"{sh['key']}: {nid} 锚在 {want},却挂在 {sc['scene_no']}")
        try:
            est = float(n.get("est_s") or 0)
            win = sum(float(x["duration_hint_s"] or 0) for _, x in locs)
            if est and win and win * 1.15 < est * 0.5:
                warns.append(f"{locs[0][1]['key']}: {nid} 估时 {est:g}s,所挂镜时长建议只有 {win:g}s(定稿窗口由 shot-planning 复核)")
        except Exception:
            pass
    for n in narration:
        if n["id"] not in ref_at:
            msg = f"{n['id']}: 旁白稿条目没有任何镜引用(anchor: {n.get('anchor') or '—'})"
            (errs if strict else warns).append(msg)
    return errs, warns


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

# ---------------- 人物姿态/动作(2026-09-14 用户拍板) ----------------
# 受控枚举:分镜层 shots_draft[].poses[<CHAR-id>].pose 取值;白模关键帧 pose 同一套(modules/whitebox.py POSES),
# 站/坐/躺沿用白模原三态,跪/蹲/趴为本次新增(白模渲染器与包围盒同步支持)。
POSE_ENUM = ("stand", "sit", "lie", "kneel", "crouch", "prone")
POSE_EN = {"stand": "standing", "sit": "sitting", "lie": "lying down", "kneel": "kneeling",
           "crouch": "crouching", "prone": "lying face down"}
POSE_ZH = {"stand": "站", "sit": "坐", "lie": "躺", "kneel": "跪", "crouch": "蹲", "prone": "趴"}
# 各体位在站位片段/动线句里的可辨认写法(机检 pose 与 space_fragment_en 是否相符时用,中英都认)
POSE_WORDS = {"stand": ("站", "立", "stand"), "sit": ("坐", "sit", "seat"), "lie": ("躺", "卧", "lying", "lie", "reclin"),
              "kneel": ("跪", "kneel"), "crouch": ("蹲", "crouch", "squat"), "prone": ("趴", "俯卧", "匍匐", "prone", "face down")}

# 关键词表:中文分镜文字 → 英文短语(与 _CAMERA_TERMS 同款,只做子串命中不做语义理解;
# 单字项只收高精度的,易误命中的一律用双字;action 中文直通的项目也可能在 content 里写英文,英文项按整词匹配 _POSE_TERMS_EN)。
_POSE_EXCLUDE = ("车站", "站台", "站牌", "驿站", "站位", "坐标", "坐落", "卧室", "卧房", "卧榻", "卧铺", "走廊", "走道", "走线",
                 "跑道", "倒影", "倒像", "伏笔", "起伏", "埋伏", "闪光", "闪烁", "闪回", "闪电", "闪现", "闪过", "推进", "推近",
                 "拉远", "拉开", "跟拍", "跟随", "扑面", "扑克", "拍摄", "俯拍", "侧拍", "仰拍", "顶拍", "跳切", "跳接", "跳动")
_POSE_TERMS = (
    # 静态体位
    ("站立", "standing"), ("站着", "standing"), ("站在", "standing"), ("站定", "standing still"), ("站起", "standing up"),
    ("起身", "rising to their feet"), ("直立", "standing upright"), ("伫立", "standing still"), ("肃立", "standing at attention"),
    ("站", "standing"),
    ("坐着", "sitting"), ("坐在", "sitting"), ("坐下", "sitting down"), ("落座", "sitting down"), ("端坐", "sitting upright"),
    ("盘坐", "sitting cross-legged"), ("盘腿", "sitting cross-legged"), ("坐起", "sitting up"), ("坐", "sitting"),
    ("平躺", "lying on the back"), ("仰卧", "lying on the back"), ("侧卧", "lying on one side"), ("侧躺", "lying on one side"),
    ("躺", "lying down"), ("卧", "lying down"),
    ("跪拜", "kowtowing"), ("跪地", "kneeling on the ground"), ("下跪", "kneeling"), ("跪", "kneeling"), ("屈膝", "kneeling"),
    ("半蹲", "half crouch"), ("蹲", "crouching"),
    ("俯卧", "lying face down"), ("趴", "lying face down"), ("匍匐", "crawling on the ground"), ("伏案", "hunched over the desk"),
    ("靠着", "leaning against"), ("靠在", "leaning against"), ("倚着", "leaning against"), ("倚在", "leaning against"),
    ("斜倚", "reclining"), ("弯腰", "bending over"), ("俯身", "bending forward"), ("躬身", "bowing"), ("鞠躬", "bowing"),
    ("仰头", "head tilted back"), ("叉腰", "hands on hips"), ("抱臂", "arms crossed"), ("背手", "hands behind the back"),
    ("握拳", "fists clenched"), ("攥紧", "gripping tightly"),
    # 移动
    ("行走", "walking"), ("踱步", "pacing"), ("走", "walking"), ("狂奔", "sprinting"), ("跑", "running"), ("奔", "running"),
    ("追赶", "chasing"), ("追上", "catching up"), ("冲向", "charging at"), ("冲出", "rushing out"), ("冲进", "rushing in"),
    ("退后", "stepping back"), ("后退", "stepping back"), ("倒退", "backing away"), ("跳起", "jumping up"), ("跳下", "jumping down"),
    ("跃起", "leaping up"), ("一跃", "leaping"), ("飞跃", "leaping over"), ("攀爬", "climbing"), ("爬起", "getting up"),
    ("爬上", "climbing onto"), ("蹒跚", "staggering"), ("踉跄", "staggering"), ("倒下", "collapsing"), ("倒地", "falling to the ground"),
    ("跌", "falling"), ("摔", "falling"), ("转身", "turning around"), ("回身", "turning around"), ("迈步", "striding"),
    ("骑马", "riding a horse"), ("骑着", "riding"), ("上马", "mounting"), ("下马", "dismounting"), ("拖着", "dragging"),
    ("牵着", "leading by hand"), ("扛着", "carrying on the shoulder"), ("背着", "carrying on the back"), ("抬着", "carrying"),
    ("提着", "carrying"), ("拎着", "carrying"), ("撑着", "propping up"),
    # 动作
    ("挥剑", "swinging a sword"), ("挥刀", "swinging a blade"), ("挥手", "waving a hand"), ("挥", "swinging"),
    ("劈", "slashing down"), ("砍", "hacking"), ("刺向", "thrusting at"), ("刺出", "thrusting"), ("拔剑", "drawing a sword"),
    ("拔刀", "drawing a blade"), ("持剑", "holding a sword"), ("握剑", "gripping a sword"), ("举剑", "raising a sword"),
    ("抬手", "raising a hand"), ("举手", "hand raised"), ("伸手", "reaching out"), ("推门", "pushing the door"),
    ("推开", "pushing open"), ("推搡", "shoving"), ("拉住", "grabbing hold"), ("搂住", "holding close"), ("抱", "embracing"),
    ("扶", "supporting"), ("搀", "supporting"), ("指着", "pointing at"), ("指向", "pointing toward"), ("抓住", "grabbing"),
    ("拽", "yanking"), ("掀", "lifting"), ("递", "handing over"), ("捧", "holding in both hands"), ("端起", "lifting up"),
    ("拍打", "patting"), ("敲", "knocking"), ("捶", "pounding"), ("踢", "kicking"), ("踹", "kicking"), ("掷", "throwing"),
    ("扔", "throwing"), ("擦", "wiping"), ("梳", "combing"), ("拨", "stirring"), ("抚摸", "stroking"), ("摸着", "touching"),
    ("掩面", "covering the face"), ("捂住", "covering"), ("拱手", "cupping hands in salute"), ("作揖", "bowing with clasped hands"),
    ("磕头", "kowtowing"), ("叩首", "kowtowing"),
    # 反应
    ("闪躲", "dodging"), ("闪身", "dodging aside"), ("闪开", "dodging away"), ("躲避", "dodging"), ("躲", "dodging"), ("闪", "dodging"),
    ("退缩", "shrinking back"), ("缩身", "shrinking back"), ("扑向", "lunging at"), ("扑倒", "lunging down"), ("扑", "lunging"),
    ("格挡", "parrying"), ("挡住", "blocking"), ("抱头", "covering the head"), ("捂脸", "covering the face"),
    ("定住", "freezing"), ("愣", "freezing"),
)
_POSE_TERMS_EN = (
    ("standing", "standing"), ("stands", "standing"), ("stand", "standing"), ("sitting", "sitting"), ("seated", "sitting"),
    ("sits", "sitting"), ("sit", "sitting"), ("lying", "lying down"), ("lies", "lying down"), ("reclining", "reclining"),
    ("kneeling", "kneeling"), ("kneels", "kneeling"), ("kneel", "kneeling"), ("crouching", "crouching"), ("crouch", "crouching"),
    ("squatting", "squatting"), ("prone", "lying face down"), ("face down", "lying face down"), ("leaning", "leaning"),
    ("running", "running"), ("runs", "running"), ("sprint", "sprinting"), ("walking", "walking"), ("walks", "walking"),
    ("jumping", "jumping"), ("jumps", "jumping"), ("leaping", "leaping"), ("climbing", "climbing"), ("falling", "falling"),
    ("falls", "falling"), ("collapses", "collapsing"), ("dodging", "dodging"), ("dodges", "dodging"), ("swinging", "swinging"),
    ("swings", "swinging"), ("draws a sword", "drawing a sword"), ("thrusts", "thrusting"), ("lunges", "lunging"),
    ("bowing", "bowing"), ("bows", "bowing"), ("kowtow", "kowtowing"), ("reaching", "reaching out"), ("pushing", "pushing"),
    ("pulling", "pulling"), ("embracing", "embracing"), ("turning around", "turning around"), ("riding", "riding"),
)


_NEGATED_RE = re.compile(r"(不|没|未|别|勿|莫|无)(再|曾|有|要|敢|肯|能|会|去|想)?$")


def _pose_term_hits(text: str) -> list[str]:
    """从分镜文字命中姿态/动作英文短语(按出现位置排序、按短语去重);先剔除 _POSE_EXCLUDE 里的非肢体词。"""
    t = str(text or "")
    for bad in _POSE_EXCLUDE:
        t = t.replace(bad, "﹍" * len(bad))    # 等长占位,保住位置
    # 长词优先、占位去重:「侧卧」命中后同一处的「卧」不再单独命中(否则一句里 lying on one side / lying down 双报)
    covered: list[tuple[int, int]] = []
    hits: list[tuple[int, str]] = []
    for zh, en in sorted(_POSE_TERMS, key=lambda kv: -len(kv[0])):
        start = 0
        while True:
            i = t.find(zh, start)
            if i < 0:
                break
            j = i + len(zh)
            if not any(i < b and j > a for a, b in covered):
                covered.append((i, j))
                # 否定词紧邻在前(「不坐起来」「没回身」「不再站」)= 该动作没发生,占住位置但不命中
                if not _NEGATED_RE.search(t[max(0, i - 3):i]) and en not in (e for _, e in hits):
                    hits.append((i, en))
            start = j
    found = [en for _, en in sorted(hits)]
    for m in re.finditer(r"[A-Za-z][A-Za-z ]+", t):
        seg = m.group(0).lower()
        for en_kw, en in _POSE_TERMS_EN:
            if re.search(rf"\b{re.escape(en_kw)}\b", seg) and en not in found:
                found.append(en)
    return found


def normalize_poses(v) -> dict:
    """把 storyboard.json 里各种写法归一成 {id: {pose, action}}:
    dict {id: {pose, action}} / {id: "sit"} / list [{id|character, pose, action}];非法/空 → {}。pose 小写去空格,不在枚举内原样保留(交机检报)。"""
    out: dict = {}
    items = []
    if isinstance(v, dict):
        items = [(k, rec) for k, rec in v.items()]
    elif isinstance(v, list):
        for rec in v:
            if isinstance(rec, dict):
                cid = rec.get("id") or rec.get("character") or rec.get("cast")
                if isinstance(cid, str):
                    items.append((cid, rec))
    for cid, rec in items:
        if not isinstance(cid, str) or not cid.strip():
            continue
        if isinstance(rec, str):
            rec = {"pose": rec}
        if not isinstance(rec, dict):
            continue
        pose = str(rec.get("pose") or rec.get("state") or "").strip().lower()
        action = str(rec.get("action") or rec.get("action_zh") or "").strip()
        if not pose and not action:
            continue
        out[cid.strip()] = {"pose": pose, "action": action}
    return out


def pose_hint(shot: dict, names: dict | None = None) -> str:
    """每镜「Body poses and actions:」句:有结构化 `poses` 时逐角色 "<名> <体位英文>, <action 原文>"(action 中文直通,2026-09-14 拍板),
    否则退回从 content/action/sketch 文字按关键词表推导(最多 10 个短语);都没有返回空。"""
    poses = shot.get("poses") or {}
    segs = []
    for cid, rec in poses.items():
        if not isinstance(rec, dict):
            continue
        name = (names or {}).get(cid, cid)
        body = ", ".join(x for x in (POSE_EN.get(rec.get("pose") or "", ""), str(rec.get("action") or "").strip()) if x)
        if body:
            segs.append(f"{name} {body}")
    if segs:
        return "; ".join(segs)
    text = " ".join(str(shot.get(k) or "") for k in ("content", "action", "sketch"))
    return ", ".join(_pose_term_hits(text)[:10])


def check_poses(board: dict, strict: bool = False) -> tuple[list[str], list[str]]:
    """机检 pose_present(2026-09-14):每镜每个出场角色(CHAR-*)在 `poses` 有条目且 pose 在枚举内。
    整镜没写 `poses` 的存量项目按 WARN(strict=True 按 FAIL);写了 `poses` 但漏角色/枚举外一律 FAIL;
    生物(CRE-*)缺条目只 WARN;poses 里出现不在本镜/本场出场的 id 只 WARN。返回 (errors, warnings)。"""
    errs, warns = [], []
    for sc in board.get("scenes") or []:
        scene_cast = set(sc.get("cast") or []) | set(sc.get("creatures") or [])
        for sh in sc.get("shots") or []:
            key, cast, poses = sh.get("key"), [c for c in (sh.get("cast") or []) if isinstance(c, str)], sh.get("poses") or {}
            if not poses:
                if cast:
                    (errs if strict else warns).append(f"{key}: 缺 poses(出场 {', '.join(cast)} 的体位/动作未登记)")
                continue
            for cid, rec in poses.items():
                pose = (rec or {}).get("pose") or ""
                if pose and pose not in POSE_ENUM:
                    errs.append(f"{key}/{cid}: pose {pose!r} 不在枚举 {'/'.join(POSE_ENUM)} 内")
                elif not pose:
                    errs.append(f"{key}/{cid}: 缺 pose(只写了 action)")
                if cid not in cast and cid not in scene_cast:
                    warns.append(f"{key}/{cid}: poses 里的 id 不在本镜/本场出场名单")
            for cid in cast:
                if cid not in poses:
                    (warns if cid.startswith("CRE-") else errs).append(f"{key}/{cid}: 出场但 poses 无条目")
    return errs, warns


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


def sketch_text_only(provider: str) -> bool:
    """该图像渠道出草图是否走纯文生图(不传人物参考图,宫格批量也退化为逐镜单张):comfyui 各运行方式皆是,见 TEXT_ONLY_SKETCH_PROVIDERS。"""
    return str(provider or "").strip().lower() in TEXT_ONLY_SKETCH_PROVIDERS


def build_prompt(scene: dict, shot: dict, names: dict, note: str = "", with_refs: bool = True) -> tuple[str, str]:
    """单镜提示词(2026-09-12 人物优先):风格句 → 景别 → 机位 → 出场 → **姿态/动作(2026-09-14)** → 画面/动作 → 神态 → 构图 → 群众 → 地点短提示(最后,只作示意) → 修改意见。
    with_refs=False(comfyui 纯文生图)时整句风格提示换成 SKETCH_STYLE_PROMPT_TEXT_ONLY(不列举姿态、无否定句、人物按文字画)。"""
    parts = [SKETCH_STYLE_PROMPT + SKETCH_REFS_SENTENCE if with_refs else SKETCH_STYLE_PROMPT_TEXT_ONLY]
    if shot.get("size_hint"):
        parts.append(f"Shot size: {shot['size_hint']}.")
    cam = camera_hint(shot)
    if cam:
        parts.append(f"Camera: {cam}.")
    cast = [names.get(c, c) for c in shot.get("cast") or []]
    if cast:
        parts.append("Characters in frame: " + ", ".join(cast) + ".")
    ph = pose_hint(shot, names)
    if ph:
        parts.append(f"Body poses and actions (draw exactly as stated): {ph}.")
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
    """宫格提示词:风格总句 + 各场地点短提示一次(只作示意) + 逐格「Panel k (row r, col c)」景别/机位/地点/出场/姿态动作(不裁)/画面/动作/神态/构图(用台账 note)。
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
            ph = pose_hint(shot, names)
            if ph:      # 姿态/动作句不进三档裁剪(2026-09-14):它正是以前被长散文淹没、被截掉的信息
                seg.append(f"Poses: {ph}.")
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
        stale = latest > float(meta.get("inputs_mtime") or st.st_mtime) + 1
        stale_reason = "inputs" if stale else ""
        # 对白语音库(2026-09-13):开关开着且库指纹与样片记录的不一致(台词/音色变了、或样片出时还没开库)也算过期
        try:
            from modules import dialogue_tts as dt
            if dt.enabled(base):
                cur = dt.library_fingerprint(dt.load_manifest(base, ep))
                rec = (meta.get("dialogue_tts") or {}).get("fingerprint", "")
                if not stale and (cur != rec or not (meta.get("dialogue_tts") or {}).get("enabled")):
                    stale, stale_reason = True, "dialogue_tts"
        except Exception:  # noqa: BLE001
            pass
        out.update({"name": mp4.name, "size_mb": round(st.st_size / 1048576, 1), "mtime": int(st.st_mtime),
                    "url": f"/projects/{base.name}/{out['path']}?v={int(st.st_mtime)}",
                    "duration_s": meta.get("duration_s"), "shots": meta.get("shots"),
                    "missing_sketches": meta.get("missing_sketches"), "duration_source": meta.get("duration_source"),
                    "audio_tracks": meta.get("audio_tracks"), "created_at": meta.get("created_at"),
                    "dialogue_tts": meta.get("dialogue_tts") or {},
                    "stale": stale, "stale_reason": stale_reason})
    return out


def sketch_url(base: Path, rec: dict) -> str | None:
    f = base / str(rec.get("file") or "")
    if rec.get("file") and f.is_file():
        return f"/projects/{base.name}/{rec['file']}?v={int(f.stat().st_mtime)}"
    return None
