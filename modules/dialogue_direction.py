"""台词演法(2026-10-02):每句对白的「导演式」表演指示与目标时长,存进分镜表,对白语音库按它合成。

背景:对白语音以前只带一个情绪标签(甚至没带),逐句孤立合成,语气平、时长靠事后变速去贴镜长。fengshen3 ep07 实测
把提示词改成「音色一句话 + 状态与演法 + 场景与对象 + 目标时长」后,情绪明显到位,时长基本落在目标上(Doubao-音频生成 1.0)。

契约:directing/<ep>/shot_list.json shots[].dialogue_lines[] 每句可带
  emotion   剧本情绪标注(写演法时缺了由本模块按台词原文从 story/episodes/<ep>/script_breakdown.json 补回)
  delivery  {direction, scene, target_s, pace, text_sha, by, at}
            direction 演法:状态 + 怎么演 + 语速 / 音量 / 停顿(写给配音演员看的一两句,不复述台词)
            scene     场景与对象:在哪、对谁说、刚发生了什么(一句;不用引号引台词,免得被念出来)
            target_s  目标时长(秒):由 pace(fast|medium|slow)或工位直接给的秒数算出,宿主按字数与镜长收口
            text_sha  写入时台词原文的指纹:台词后来改了,这条演法即作废(机检 FAIL,对白语音库当它不存在)
分镜规划定稿、对白精简之后由 07-directing/dialogue-direction 工位(workflow.yaml p6-dialogue-direction)逐句写,
只走宿主 CLI code/dialogue_direction.py(plan 取上下文 → apply 批量写入 → check);用户也可在分镜预览页
「对白语音」面板里逐句改。本模块是唯一写入口:只动 dialogue_lines 的 emotion / delivery 两个键,别的一概不碰。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

PACES = {"fast": 5.0, "medium": 4.2, "slow": 3.4}        # 字/秒(有效字符,不含标点)
PAUSE_S = 0.2                                            # 句中每个停顿标点另加的时长
RATE_MIN, RATE_MAX = 2.5, 6.5                            # 可接受的语速范围(字/秒)
TARGET_MIN = 0.6                                         # 再短模型出不稳
LEAD_S, GAP_S = 0.10, 0.15                               # 与 modules/dialogue_track 排轨口径一致:镜首留白、句间间隔
DIRECTION_MAX, SCENE_MAX = 200, 120
DEFAULT_BY = "07-directing/dialogue-direction"
_EFFECTIVE = re.compile(r"[一-鿿A-Za-z0-9]")
_INNER_PAUSE = re.compile(r"[,,、;;:。.!!??—…]+")
_QUOTED = re.compile(r"[“\"「『]([^”\"」』]{2,})[”\"」』]")


def _read(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
    except (ValueError, OSError):
        return None


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def text_sha(text: str) -> str:
    return hashlib.sha256(_norm(text).encode("utf-8")).hexdigest()[:12]


def effective_chars(text: str) -> int:
    return len(_EFFECTIVE.findall(str(text or "")))


def inner_pauses(text: str) -> int:
    """句中停顿数:标点串的个数,不算句末那一处。"""
    marks = _INNER_PAUSE.findall(str(text or "").strip())
    return max(0, len(marks) - (1 if re.search(r"[。.!!??—…]+\s*$", str(text or "").strip()) else 0))


def paced_target(text: str, pace: str) -> float:
    """按语速档算的自然时长:有效字数 ÷ 档位语速 + 句中停顿。"""
    rate = PACES.get(pace or "medium", PACES["medium"])
    return round(max(TARGET_MIN, effective_chars(text) / rate + PAUSE_S * inner_pauses(text)), 2)


def bounds(text: str) -> tuple[float, float]:
    """这句台词可接受的时长范围(由语速范围换算)。"""
    n = max(1, effective_chars(text))
    return round(max(TARGET_MIN, n / RATE_MAX), 2), round(max(TARGET_MIN, n / RATE_MIN + PAUSE_S * inner_pauses(text)), 2)


def line_text(ln: dict) -> str:
    return str(ln.get("text") or ln.get("line") or "").strip()


def line_delivery(ln: dict) -> dict | None:
    """对白行上仍然有效的演法(台词原文没变)→ {direction, scene, target_s};没有或已作废返回 None。"""
    d = ln.get("delivery") if isinstance(ln, dict) else None
    if not isinstance(d, dict) or not str(d.get("direction") or "").strip():
        return None
    if d.get("text_sha") and d["text_sha"] != text_sha(line_text(ln)):
        return None
    try:
        target = float(d.get("target_s")) if d.get("target_s") not in (None, "") else None
    except (TypeError, ValueError):
        target = None
    return {"direction": str(d["direction"]).strip(), "scene": str(d.get("scene") or "").strip(),
            "target_s": target if target and target > 0 else None}


# ---------------------------------------------------------------- 上下文

def script_emotions(base: Path, ep: str) -> dict[str, dict]:
    """剧本拆解表里每句对白的情绪标注与括注:{去空白的台词原文: {emotion, paren}}。"""
    out: dict[str, dict] = {}

    def walk(o):
        if isinstance(o, dict):
            if o.get("text") and o.get("speaker") and ("emotion" in o or "paren" in o):
                out.setdefault(_norm(o["text"]), {"emotion": str(o.get("emotion") or "").strip(),
                                                  "paren": str(o.get("paren") or "").strip()})
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(_read(Path(base) / "story" / "episodes" / ep / "script_breakdown.json") or {})
    return out


def _names(base: Path) -> dict[str, str]:
    out = {}
    for rel, key in (("bible/characters/index.json", "characters"), ("bible/creatures/index.json", "creatures")):
        for c in (_read(Path(base) / rel) or {}).get(key) or []:
            if isinstance(c, dict) and c.get("id"):
                out[c["id"]] = str(c.get("canonical_name") or c.get("name") or c["id"])
    return out


def _scene_names(base: Path) -> dict[str, str]:
    idx = _read(Path(base) / "bible" / "scenes" / "index.json") or {}
    return {s["id"]: str(s.get("name") or s["id"]) for s in idx.get("scenes") or [] if isinstance(s, dict) and s.get("id")}


def _speaker_id(ln: dict) -> str:
    m = re.search(r"((?:CHAR|CRE)-\d+)", " ".join(str(ln.get(k) or "") for k in ("speaker", "char", "character_id", "speaker_char")))
    return m.group(1) if m else ""


def _line_limit(shot: dict, lines: list[dict], i: int) -> float | None:
    """这一句在本镜里最多能占多长:镜长 − 镜首留白 − 句间间隔 − 同镜其它句子已定(或估)的时长。"""
    try:
        dur = float(shot.get("duration_s"))
    except (TypeError, ValueError):
        return None
    others = 0.0
    for j, other in enumerate(lines):
        if j == i:
            continue
        d = line_delivery(other)
        try:
            others += float((d or {}).get("target_s") or other.get("est_duration_s") or 0)
        except (TypeError, ValueError):
            pass
    return round(dur - LEAD_S - GAP_S * (len(lines) - 1) - others, 2)


def context(base: Path, ep: str, shot_list: dict | None = None) -> list[dict]:
    """逐句上下文(工位写演法要看的东西都在这):人物、台词、剧本情绪与括注、场景、本镜画面与该人物动作、上一句、
    镜长、原估时、这句的时长上限、按三档语速各是多少秒、现有演法与状态(ok | missing | stale)。"""
    base = Path(base)
    sl = shot_list if shot_list is not None else (_read(base / "directing" / ep / "shot_list.json") or {})
    emo, names, scenes = script_emotions(base, ep), _names(base), _scene_names(base)
    group_of = {sid: g.get("group_id") for g in sl.get("generation_groups") or [] if isinstance(g, dict)
                for sid in g.get("shots") or []}
    out, prev = [], None
    for shot in sl.get("shots") or []:
        if not isinstance(shot, dict) or not shot.get("shot_id"):
            continue
        lines = [ln for ln in shot.get("dialogue_lines") or [] if isinstance(ln, dict) and line_text(ln)]
        for i, ln in enumerate(lines):
            text, spk = line_text(ln), _speaker_id(ln)
            script = emo.get(_norm(text)) or {}
            cur = ln.get("delivery") if isinstance(ln.get("delivery"), dict) else None
            status = "ok" if line_delivery(ln) else ("stale" if cur and str(cur.get("direction") or "").strip() else "missing")
            lo, hi = bounds(text)
            item = {
                "shot_id": shot["shot_id"], "idx": i, "group_id": group_of.get(shot["shot_id"], ""),
                "speaker": spk, "speaker_name": names.get(spk, str(ln.get("speaker") or "")),
                "text": text, "emotion": str(ln.get("emotion") or ln.get("tone") or "").strip() or script.get("emotion", ""),
                "paren": script.get("paren", ""),
                "scene_id": shot.get("scene_id") or "", "scene_name": scenes.get(shot.get("scene_id") or "", ""),
                "shot_brief": str(shot.get("content_brief") or ""),
                "action": str(((shot.get("poses") or {}).get(spk) or {}).get("action") or "") if spk else "",
                "others_in_shot": [names.get(c, c) for c in shot.get("characters") or [] if c != spk],
                "prev_line": prev,
                "shot_duration_s": shot.get("duration_s"), "est_duration_s": ln.get("est_duration_s"),
                "chars": effective_chars(text), "limit_s": _line_limit(shot, lines, i), "min_s": lo, "max_s": hi,
                "pace_seconds": {p: paced_target(text, p) for p in PACES},
                "delivery": cur, "status": status,
            }
            out.append(item)
            prev = {"speaker_name": item["speaker_name"], "text": text, "same_scene": True}
    # 上一句是否同场景:跨场景的上一句只作弱参考
    last_scene = None
    for item in out:
        if item["prev_line"] is not None:
            item["prev_line"] = dict(item["prev_line"], same_scene=last_scene == item["scene_id"])
        last_scene = item["scene_id"]
    return out


# ---------------------------------------------------------------- 写入

def resolve_target(text: str, pace: str = "", target_s=None, limit_s: float | None = None) -> tuple[float, list[str]]:
    """目标时长:工位给了秒数用秒数,否则按语速档算;再按可接受语速范围与本镜上限收口。→ (秒, 提示)"""
    notes = []
    lo, hi = bounds(text)
    try:
        t = float(target_s) if target_s not in (None, "") else None
    except (TypeError, ValueError):
        t = None
    if t is None or t <= 0:
        t = paced_target(text, pace)
    if t < lo:
        notes.append(f"目标 {t:g}s 太短(这句最快也要 {lo:g}s),已放宽到 {lo:g}s")
        t = lo
    if t > hi:
        notes.append(f"目标 {t:g}s 太长(语速不到 {RATE_MIN:g} 字/秒),已收到 {hi:g}s")
        t = hi
    if limit_s is not None and t > limit_s:
        if limit_s >= lo:
            notes.append(f"目标 {t:g}s 超出本镜可用 {limit_s:g}s,已收到 {limit_s:g}s")
            t = limit_s
        else:
            notes.append(f"本镜只剩 {limit_s:g}s,这句最快也要 {lo:g}s,装不下(按 {lo:g}s 出声,会拖进下一镜;回派对白精简或加镜长)")
            t = lo
    return round(t, 2), notes


def _validate(text: str, direction: str, scene: str) -> list[str]:
    errs = []
    if not direction:
        errs.append("direction 为空")
    if len(direction) > DIRECTION_MAX:
        errs.append(f"direction 超过 {DIRECTION_MAX} 字")
    if len(scene) > SCENE_MAX:
        errs.append(f"scene 超过 {SCENE_MAX} 字")
    body = _norm(text)
    for field, value in (("direction", direction), ("scene", scene)):
        # 引号里的整句话会被模型当台词念出来:只允许引台词里的个别字词(用来指重音),不许整句或别人的台词
        for q in _QUOTED.findall(value):
            if len(_norm(q)) > 8 or _norm(q) not in body:
                errs.append(f"{field} 里用引号引了「{q[:12]}」:只能引本句台词里的个别字词,别的话改成转述")
    return errs


def apply(base: Path, ep: str, items: list[dict], by: str = "") -> dict:
    """批量写入演法。items:[{shot_id, idx?, direction, scene?, pace?, target_s?}];同时把缺的 emotion 按剧本补回。
    任何一条不合法整批不写(返回 errors);成功返回 {written, notes, backfilled_emotion}。"""
    base = Path(base)
    path = base / "directing" / ep / "shot_list.json"
    sl = _read(path)
    if not isinstance(sl, dict):
        return {"written": 0, "errors": [f"读不到 directing/{ep}/shot_list.json"]}
    shots = {s.get("shot_id"): s for s in sl.get("shots") or [] if isinstance(s, dict)}
    errors, notes, staged = [], [], []
    for it in items or []:
        sid = str((it or {}).get("shot_id") or "")
        try:
            idx = int(it.get("idx") or 0)
        except (TypeError, ValueError, AttributeError):
            idx = 0
        shot = shots.get(sid)
        lines = [ln for ln in (shot or {}).get("dialogue_lines") or [] if isinstance(ln, dict) and line_text(ln)]
        if shot is None or not 0 <= idx < len(lines):
            errors.append(f"{sid}/l{idx:02d}:分镜表里没有这句台词")
            continue
        ln = lines[idx]
        direction, scene = str(it.get("direction") or "").strip(), str(it.get("scene") or "").strip()
        pace = str(it.get("pace") or "").strip().lower()
        if pace and pace not in PACES:
            errors.append(f"{sid}/l{idx:02d}:pace 只能是 {'/'.join(PACES)}")
            continue
        bad = _validate(line_text(ln), direction, scene)
        if bad:
            errors += [f"{sid}/l{idx:02d}:{b}" for b in bad]
            continue
        staged.append((shot, lines, idx, ln, direction, scene, pace, it.get("target_s")))
    if errors:
        return {"written": 0, "errors": errors}
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    for shot, lines, idx, ln, direction, scene, pace, target_s in staged:
        ln.pop("delivery", None)                              # 先摘掉旧值,算上限时不把自己算进「其它句子」
        target, ns = resolve_target(line_text(ln), pace, target_s, _line_limit(shot, lines, idx))
        notes += [f"{shot['shot_id']}/l{idx:02d}:{n}" for n in ns]
        ln["delivery"] = {"direction": direction, "scene": scene, "target_s": target,
                          **({"pace": pace} if pace else {}),
                          "text_sha": text_sha(line_text(ln)), "by": by or DEFAULT_BY, "at": stamp}
    backfilled = backfill_emotions(base, ep, sl)
    if staged or backfilled:
        path.write_text(json.dumps(sl, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"written": len(staged), "notes": notes, "backfilled_emotion": backfilled, "errors": []}


def backfill_emotions(base: Path, ep: str, shot_list: dict) -> int:
    """写了演法的对白行缺 emotion 的,按台词原文从剧本拆解表补回(只改内存里的 shot_list,由调用方落盘)。
    没写演法的句子不补:emotion 进对白语音库的 key,补了会让这些句子无端过期、按旧写法重出一遍。"""
    emo = script_emotions(base, ep)
    n = 0
    for shot in shot_list.get("shots") or []:
        for ln in (shot or {}).get("dialogue_lines") or []:
            if (isinstance(ln, dict) and line_text(ln) and isinstance(ln.get("delivery"), dict)
                    and not str(ln.get("emotion") or ln.get("tone") or "").strip()):
                hit = (emo.get(_norm(line_text(ln))) or {}).get("emotion")
                if hit:
                    ln["emotion"] = hit
                    n += 1
    return n


def clear(base: Path, ep: str, shot_id: str, idx: int = 0) -> bool:
    """摘掉一句的演法(回到没写的状态)。"""
    path = Path(base) / "directing" / ep / "shot_list.json"
    sl = _read(path) or {}
    for shot in sl.get("shots") or []:
        if isinstance(shot, dict) and shot.get("shot_id") == shot_id:
            lines = [ln for ln in shot.get("dialogue_lines") or [] if isinstance(ln, dict) and line_text(ln)]
            if 0 <= idx < len(lines) and "delivery" in lines[idx]:
                del lines[idx]["delivery"]
                path.write_text(json.dumps(sl, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                return True
    return False


# ---------------------------------------------------------------- 机检

def check(base: Path, ep: str) -> dict:
    """dialogue_direction_bound:本集每句有说话人编号的对白都有有效演法与目标时长。
    FAIL:缺演法、台词改过(演法作废);WARN:情绪标注缺、目标时长超出本镜可用、语速超出范围。"""
    rows = context(base, ep)
    fails, warns = [], []
    for r in rows:
        tag = f"{r['shot_id']}/l{r['idx']:02d} {r['speaker_name']}"
        if not r["speaker"]:
            continue                                         # 群杂 / 未解析说话人:对白语音库本就跳过
        if r["status"] == "missing":
            fails.append(f"{tag}:没有演法")
            continue
        if r["status"] == "stale":
            fails.append(f"{tag}:台词改过,演法已作废,要重写")
            continue
        d = line_delivery({"text": r["text"], "delivery": r["delivery"]}) or {}
        if not r["emotion"]:
            warns.append(f"{tag}:没有情绪标注(剧本里也没对上)")
        t = d.get("target_s")
        if not t:
            warns.append(f"{tag}:没有目标时长")
        else:
            if r["limit_s"] is not None and t > r["limit_s"] + 0.05:
                warns.append(f"{tag}:目标 {t:g}s 超出本镜可用 {r['limit_s']:g}s")
            if not r["min_s"] - 0.01 <= t <= r["max_s"] + 0.01:
                warns.append(f"{tag}:目标 {t:g}s 超出这句可接受的 {r['min_s']:g}–{r['max_s']:g}s")
    total = sum(1 for r in rows if r["speaker"])
    return {"check": "dialogue_direction_bound", "ok": not fails, "total": total,
            "bound": sum(1 for r in rows if r["speaker"] and r["status"] == "ok"), "fails": fails, "warns": warns}
