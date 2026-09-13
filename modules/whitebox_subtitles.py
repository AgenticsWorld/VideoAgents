"""白模样片字幕(2026-09-11):给整集白模样片(assets/whitebox/<ep>/<ep>-camera.mp4)烧入对白/旁白字幕。

本机 ffmpeg 不带 libass/freetype(无 subtitles/drawtext 滤镜),所以字幕用 PIL 画成透明 PNG 字幕带,
再作为第二路输入(concat demuxer 按时长排片)用 overlay 叠到样片上;与动态样片 code/animatic.py 同一套
字体查找/折行与配色(台词黄、旁白紫)。

字幕来源(只读,不写回任何源文件):
- 对白:directing/<ep>/shot_list.json shots[].dialogue_lines[{speaker,text,est_duration_s}],
  在该镜时段内按各句估时按比例分配显示区间;说话人名取 bible/characters|creatures/index.json。
- 旁白:文本取 story/episodes/<ep>/narration.md([N-xx | anchor … | est_duration_s: …] 条目),
  挂点取 shot_list narration_anchors(首个挂点镜起点起、est_duration_s 内,不超出挂点镜窗口),
  没有挂点表时按 shots[].narration_ref(如 "N-01(前段)")的镜段兜底。
- 逐镜时间:directing/<ep>/whitebox/episode.json groups[].cameras[](shot_id/start/duration_s),
  没有编译结果时按 shot_list 组内镜序累加 duration_s;组在样片里的起点按各组 camera.mp4 时长累加。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf", "/System/Library/Fonts/STHeiti Light.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
]
ROOT = Path(__file__).resolve().parents[1]
DIALOGUE_COLOR = (251, 191, 36)
NARRATION_COLOR = (192, 132, 252)
MIN_CUE_S = 0.6
NARRATION_RE = re.compile(
    r"^\[(N-\d+)\s*\|\s*anchor:\s*([^|\]]+)\|\s*est_duration_s:\s*([\d.]+)(?:\s*\|.*)?\]\s*\n(.+)$", re.M)


def _read(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
    except (ValueError, OSError):
        return None


def find_font(size: int):
    """字体:data/fonts/ 自带 > 系统中文字体 > PIL 默认。"""
    from PIL import ImageFont
    cands = [str(p) for p in sorted((ROOT / "data" / "fonts").glob("*")) if p.suffix.lower() in (".ttf", ".otf", ".ttc")]
    for f in cands + FONT_CANDIDATES:
        if Path(f).is_file():
            try:
                return ImageFont.truetype(f, size)
            except Exception:
                continue
    return ImageFont.load_default()


def wrap_text(draw, text: str, font, max_w: int) -> list[str]:
    """按像素宽逐字折行(中文无空格,英文按词)。"""
    lines, cur = [], ""
    for tok in re.findall(r"\s+|[A-Za-z0-9'’.,;:!?()\-]+|.", text):
        trial = cur + tok
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur.rstrip())
            cur = tok.lstrip()
    if cur.strip():
        lines.append(cur.rstrip())
    return lines


def character_names(base: Path) -> dict[str, str]:
    """CHAR-/CRE- id → 规范名(字幕说话人前缀)。"""
    names: dict[str, str] = {}
    cidx = _read(base / "bible" / "characters" / "index.json") or {}
    for c in cidx.get("characters") or []:
        if isinstance(c, dict) and c.get("id"):
            names[c["id"]] = c.get("canonical_name") or c.get("name") or c["id"]
    cr = _read(base / "bible" / "creatures" / "index.json") or {}
    for c in cr.get("creatures") or []:
        if isinstance(c, dict) and c.get("id"):
            names[c["id"]] = c.get("name") or c["id"]
    return names


def narration_texts(base: Path, ep: str) -> dict[str, dict]:
    """narration.md → {N-xx: {text, est_s}}(与分镜预览 narration_items 同一正则)。"""
    p = base / "story" / "episodes" / ep / "narration.md"
    try:
        md = p.read_text(encoding="utf-8") if p.is_file() else ""
    except OSError:
        md = ""
    out = {}
    for m in NARRATION_RE.finditer(md):
        try:
            est = float(m.group(3))
        except ValueError:
            est = 0.0
        out[m.group(1)] = {"text": m.group(4).strip(), "est_s": est}
    return out


def _narration_id(ref) -> str | None:
    m = re.match(r"\s*(N-\d+)", str(ref or ""))
    return m.group(1) if m else None


def shot_timeline(base: Path, ep: str, group_ids: list[str], group_durations: dict[str, float],
                  shot_list: dict | None = None) -> dict[str, dict]:
    """样片时间轴上每个定稿镜的起止:{shot_id: {start, end, group_id}}。

    组起点按 group_ids 顺序用各组视频时长累加;组内镜段优先取 whitebox/episode.json 编译结果
    (cameras[] start/duration_s),没有时按 shot_list 组内镜序累加 duration_s。
    """
    sl = shot_list if shot_list is not None else (_read(base / "directing" / ep / "shot_list.json") or {})
    durations = {s.get("shot_id"): float(s.get("duration_s") or 0) for s in sl.get("shots") or [] if isinstance(s, dict)}
    group_shots = {g.get("group_id"): [s for s in (g.get("shots") or []) if isinstance(s, str)]
                   for g in sl.get("generation_groups") or [] if isinstance(g, dict)}
    compiled = {}
    ep_json = _read(base / "directing" / ep / "whitebox" / "episode.json") or {}
    groups = ep_json.get("groups") or []
    for g in (groups if isinstance(groups, list) else groups.values()):
        if isinstance(g, dict) and g.get("group_id"):
            compiled[g["group_id"]] = g
    timeline: dict[str, dict] = {}
    offset = 0.0
    for gid in group_ids:
        total = float(group_durations.get(gid) or 0)
        cams = [c for c in (compiled.get(gid) or {}).get("cameras") or [] if isinstance(c, dict) and c.get("shot_id")]
        if cams:
            for c in cams:
                start = float(c.get("start") or 0)
                dur = float(c.get("duration_s") or 0)
                timeline[c["shot_id"]] = {"start": round(offset + start, 3), "end": round(offset + start + dur, 3), "group_id": gid}
        else:
            t = 0.0
            for sid in group_shots.get(gid) or []:
                dur = durations.get(sid) or 0
                timeline[sid] = {"start": round(offset + t, 3), "end": round(offset + t + dur, 3), "group_id": gid}
                t += dur
        offset += total if total > 0 else sum(durations.get(s) or 0 for s in group_shots.get(gid) or [])
    return timeline


def episode_dialogue_placements(base: Path, ep: str, group_ids: list[str], group_durations: dict[str, float],
                                audio: dict | None) -> tuple[list[dict], list[dict]]:
    """对白语音库逐句音频在样片时间轴上的摆位(modules/dialogue_track.place_lines);audio 空则 ([], [])。"""
    if not audio:
        return [], []
    from modules.dialogue_track import place_lines
    timeline = shot_timeline(base, ep, group_ids, group_durations)
    return place_lines(audio, timeline)


def episode_subtitle_cues(base: Path, ep: str, group_ids: list[str], group_durations: dict[str, float],
                          placements: list[dict] | None = None) -> list[dict]:
    """样片字幕条:[{start,end,kind:dialogue|narration,text,shot_id,group_id}],按 start 排序。
    placements(对白语音库排轨结果,2026-09-13)给出的句子按实际音频起止显示,其余句子按估时比例分配。"""
    sl = _read(base / "directing" / ep / "shot_list.json") or {}
    timeline = shot_timeline(base, ep, group_ids, group_durations, sl)
    names = character_names(base)
    cues: list[dict] = []
    placed = {(p["shot_id"], p["idx"]): p for p in placements or []}
    shots = [s for s in sl.get("shots") or [] if isinstance(s, dict) and s.get("shot_id") in timeline]
    # 对白:有库音频的句子按实际起止;其余镜内按各句估时比例分配
    for s in shots:
        # 规约键 text;兼容写成 line 的出稿(与 dialogue_tts.collect_lines / dub_group 同口径,序号 idx 才能对上)
        lines = [ln for ln in (s.get("dialogue_lines") or []) if isinstance(ln, dict) and str(ln.get("text") or ln.get("line") or "").strip()]
        if not lines:
            continue
        seg = timeline[s["shot_id"]]
        span = max(seg["end"] - seg["start"], MIN_CUE_S)
        weights = [max(float(ln.get("est_duration_s") or 0), 0.0) for ln in lines]
        if sum(weights) <= 0:
            weights = [1.0] * len(lines)
        total_w = sum(weights)
        t = seg["start"]
        for idx, (ln, w) in enumerate(zip(lines, weights)):
            dur = span * w / total_w
            sp = str(ln.get("speaker") or ln.get("character_id") or "").strip()
            name = names.get(sp, sp)
            text = str(ln.get("text") or ln.get("line")).strip()
            p = placed.get((s["shot_id"], idx))
            start, end = (p["start"], max(p["end"], p["start"] + MIN_CUE_S)) if p else (t, t + dur)
            cues.append({"start": round(start, 3), "end": round(end, 3), "kind": "dialogue",
                         "text": f"{name}:{text}" if name and name != "NARRATOR" else text,
                         "shot_id": s["shot_id"], "group_id": seg["group_id"]})
            t += dur
    # 旁白:挂点表优先,缺失按 shots[].narration_ref 镜段兜底
    texts = narration_texts(base, ep)
    placed: set[str] = set()
    for a in sl.get("narration_anchors") or []:
        if not isinstance(a, dict) or not a.get("narration_id"):
            continue
        nid = a["narration_id"]
        anchors = [x for x in (a.get("anchor_shots") or []) if x in timeline]
        if not anchors or nid in placed:
            continue
        start = min(timeline[x]["start"] for x in anchors)
        window_end = max(timeline[x]["end"] for x in anchors)
        est = float(a.get("est_duration_s") or (texts.get(nid) or {}).get("est_s") or 0)
        end = min(window_end, start + est) if est > 0 else window_end
        end = max(end, start + MIN_CUE_S)
        cues.append({"start": round(start, 3), "end": round(end, 3), "kind": "narration",
                     "text": (texts.get(nid) or {}).get("text") or f"旁白 {nid}",
                     "shot_id": anchors[0], "group_id": timeline[anchors[0]]["group_id"]})
        placed.add(nid)
    ref_spans: dict[str, list[dict]] = {}
    for s in shots:
        for ref in (s.get("narration_ref") if isinstance(s.get("narration_ref"), list) else [s.get("narration_ref")]):
            nid = _narration_id(ref)
            if nid and nid not in placed:
                ref_spans.setdefault(nid, []).append(timeline[s["shot_id"]])
    for nid, segs in ref_spans.items():
        start = min(x["start"] for x in segs)
        window_end = max(x["end"] for x in segs)
        est = float((texts.get(nid) or {}).get("est_s") or 0)
        end = min(window_end, start + est) if est > 0 else window_end
        end = max(end, start + MIN_CUE_S)
        first = min(segs, key=lambda x: x["start"])
        cues.append({"start": round(start, 3), "end": round(end, 3), "kind": "narration",
                     "text": (texts.get(nid) or {}).get("text") or f"旁白 {nid}",
                     "shot_id": next(k for k, v in timeline.items() if v is first), "group_id": first["group_id"]})
    cues.sort(key=lambda c: (c["start"], 0 if c["kind"] == "dialogue" else 1, c["end"]))
    return cues


def cues_fingerprint(cues: list[dict]) -> str:
    """字幕内容指纹(进样片清单,源文本/时间变了样片判过期)。"""
    payload = [(round(c["start"], 2), round(c["end"], 2), c["kind"], c["text"]) for c in cues]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode("utf-8")).hexdigest()


def _segments(cues: list[dict], total_s: float) -> list[tuple[float, float, tuple]]:
    """把可能重叠的字幕条切成互不重叠的时段,每段带当时在屏的 (kind,text) 元组(空元组 = 无字幕)。"""
    bounds = {0.0, float(total_s)}
    for c in cues:
        bounds.add(min(max(c["start"], 0.0), total_s))
        bounds.add(min(max(c["end"], 0.0), total_s))
    pts = sorted(bounds)
    out = []
    for a, b in zip(pts, pts[1:]):
        if b - a <= 1e-3:
            continue
        mid = (a + b) / 2
        active = tuple((c["kind"], c["text"]) for c in cues if c["start"] <= mid < c["end"])
        if out and out[-1][2] == active:
            out[-1] = (out[-1][0], b, active)
        else:
            out.append((a, b, active))
    return out


def render_band(path: Path, width: int, height: int, active: tuple) -> None:
    """一段在屏字幕 → 整幅透明 PNG(底部黑带 + 居中文字;台词黄、旁白紫)。"""
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    if active:
        d = ImageDraw.Draw(im)
        fsub = find_font(max(14, width // 42))
        lh = int(fsub.size * 1.4)
        rows = []
        for kind, text in active:
            for ln in wrap_text(d, text, fsub, int(width * 0.9)):
                rows.append((kind, ln))
        rows = rows[-6:]
        band_h = min(height, lh * len(rows) + 24)
        d.rectangle((0, height - band_h, width, height), fill=(0, 0, 0, 200))
        y = height - band_h + 12
        for kind, ln in rows:
            color = DIALOGUE_COLOR if kind == "dialogue" else NARRATION_COLOR
            d.text(((width - d.textlength(ln, font=fsub)) // 2, y), ln, font=fsub, fill=color + (255,))
            y += lh
    im.save(path, "PNG")


def write_subtitle_track(cues: list[dict], width: int, height: int, total_s: float, staging: Path) -> dict:
    """在 staging 下生成字幕带 PNG 序列 + concat 清单(subs.txt),供 ffmpeg 作第二路输入 overlay。

    返回 {list, frames, segments};相同在屏内容复用同一张 PNG。
    """
    staging.mkdir(parents=True, exist_ok=True)
    segs = _segments(cues, total_s)
    files: dict[tuple, Path] = {}
    lines = []
    for i, (a, b, active) in enumerate(segs):
        if active not in files:
            p = staging / f"sub{len(files):04d}.png"
            render_band(p, width, height, active)
            files[active] = p
        lines.append(f"file '{files[active].as_posix()}'\nduration {b - a:.3f}")
    if not segs:
        p = staging / "sub0000.png"
        render_band(p, width, height, ())
        files[()] = p
        lines.append(f"file '{p.as_posix()}'\nduration {max(total_s, 0.1):.3f}")
    last = files[segs[-1][2]] if segs else files[()]
    lines.append(f"file '{last.as_posix()}'")   # concat demuxer 末条须重复才按时长收尾
    listing = staging / "subs.txt"
    listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"list": listing, "frames": len(files), "segments": len(segs)}
