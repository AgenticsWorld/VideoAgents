"""剧本拆解表(script_breakdown.json)—— 剧情处理层(01-story)产物的统一结构化视图。

两条来源,同一 schema `script_breakdown/1.0`:
  ① 正式产物:`story/episodes/<ep>/script_breakdown.json`,由 `01-story/timeline-story` 在 p5-breakdown
     节点(或用户在「剧本预览」页点「重新分析」派单)按 docs/script_breakdown.md 规约产出(剧本内容的反馈仍发 screenplay 等各自工位);
  ② 推导视图:老项目没有正式产物时,本模块从 screenplay.md / dialogue.md / narration.md /
     hooks.json / pacing.json / episode_plan.json / events.json / story_graph.json 启发式拆解
     (各项目 agent 自由排版,解析器按已见过的多种形态兼容),标 source="derived"。

页面(apps/web/static/preview_script.html)与机检(code/check_script_breakdown.py)只认这一份 schema。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from modules import scene_links
from modules.entity_ids import CHAR_ID_PAT

SCHEMA_VERSION = "script_breakdown/1.0"
OWNER_AGENT = "01-story/timeline-story"    # 拆解表产出/「重新分析」承接工位(2026-09-11 用户指定)
BREAKDOWN_REL = "story/episodes/{ep}/script_breakdown.json"

# 页面各板块的负责 Agent(✏️ 修改直发对象)
BLOCK_AGENTS = {
    "overview": "01-story/screenplay",
    "scenes": "01-story/screenplay",
    "cast": "01-story/screenplay",
    "dialogue": "01-story/dialogue-rewrite",
    "narration": "01-story/narration",
    "hooks": "01-story/hook",
    "pacing": "01-story/pacing",
    "plan": "01-story/episode-planner",
    "events": "01-story/event",
    "structure": "01-story/story-structure",
}

# 拆解输入(相对项目根);正式产物比其中任一文件旧即视为过期
INPUT_FILES = ("story/episodes/{ep}/screenplay.md", "story/episodes/{ep}/dialogue.md",
               "story/episodes/{ep}/dialogue.json", "story/episodes/{ep}/narration.md",
               "story/episodes/{ep}/narration.json", "story/episodes/{ep}/hooks.json",
               "story/episodes/{ep}/pacing.json", "story/episode_plan.json")

# 剧本机器锚点契约(docs/screenplay_anchors.md,2026-09-23):方括号标签/行首关键词/场头字段位置固定不随输出语言变,
# 中文旧写法与英文规范写法等价(EVENTS/CAST/DURATION、ACTION/TRANSITION/TIME/SOUND/SFX/MUSIC/NARRATION、NO DIALOGUE…)
_SCENE_TOKEN = re.compile(r"^(S\d{1,3}[A-Za-z]?(?:[-–]\d+)?(?:[（(](?:续|cont'?d|continued)[)）])?|\d{1,2}-\d{1,2}|OH|EC|FRAME[-_]\w+)(?=$|[\s|｜·:：—\-])", re.I)
_CHAR_RE = re.compile(CHAR_ID_PAT)        # 数字编号与拼音 slug(CHAR-jie-rui-er)都认,口径见 modules/entity_ids(#117)
_SCN_RE = re.compile(r"SCN-\d+")
_EV_RE = re.compile(r"\bev[a-z]*[-_]?(?:ch\d+[-_])?\d+\b", re.I)
_DUR_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:s\b|秒)")
_TOD_WORDS = ("黄昏", "傍晚", "清晨", "凌晨", "黎明", "深夜", "午后", "正午", "白天", "夜晚", "傍午",
              "日", "夜", "晨", "暮", "午", "夕", "未知", "晚", "早",
              # 英文/其它输出语言的时段词(场头第四段按位置也认,见 _parse_heading)
              "dawn", "sunrise", "morning", "noon", "midday", "afternoon", "dusk", "sunset", "evening", "night",
              "midnight", "day", "later", "continuous", "same time", "unknown")
_INT_EXT = {"INT": "INT", "EXT": "EXT", "内": "INT", "外": "EXT", "内景": "INT", "外景": "EXT",
            "室内": "INT", "室外": "EXT", "INT/EXT": "INT/EXT", "EXT/INT": "INT/EXT",
            "INT.": "INT", "EXT.": "EXT", "INTERIOR": "INT", "EXTERIOR": "EXT", "INT./EXT.": "INT/EXT", "I/E": "INT/EXT"}
# 冒号行里不是台词说话人的前缀(动作/声音/元信息等)
_NOT_SPEAKER = {"动作", "转场", "时段", "声音", "旁白", "画面", "音效", "音乐", "镜头", "字幕", "字卡", "备注", "注",
                "场景", "时间", "地点", "人物", "出场", "事件", "时长", "目标时长", "活跃时间线", "分配事件",
                "主场景", "片头", "片尾", "下集预告", "片头文字", "讲述文本", "环境", "光线", "氛围", "道具",
                "adaptation_note", "split_note", "source", "视觉", "听觉", "情绪", "节奏", "提示", "说明", "logline",
                "集号", "章节范围", "覆盖事件", "开场钩子", "结尾悬念", "叙述人称", "主要角色", "本集看点",
                # 英文锚点/元信息前缀(大小写不敏感,比对时 upper)
                "ACTION", "VISUAL", "TRANSITION", "TIME", "SOUND", "SFX", "MUSIC", "NARRATION", "NARRATOR", "V.O.", "VO", "O.S.", "OS",
                "NOTE", "NOTES", "SCENE", "LOCATION", "SETTING", "CAST", "PRESENT", "EVENT", "EVENTS", "DURATION", "DUR", "TARGET DURATION",
                "PROPS", "LIGHTING", "MOOD", "ATMOSPHERE", "TITLE", "TITLE CARD", "CAPTION", "SUBTITLE", "CAMERA", "SHOT", "AUDIO",
                "SOURCE", "TEASER", "RECAP", "INTRO", "OUTRO", "NEXT EPISODE", "HOOK", "PACE", "TEMPO", "EMOTION", "POV", "LOGLINE",
                "EPISODE", "CHAPTERS", "MAIN CAST", "OPENING HOOK", "CLIFFHANGER", "ADAPTATION_NOTE", "SPLIT_NOTE"}


def _not_speaker(spk: str) -> bool:
    s = _strip_md(spk).strip()
    return s in _NOT_SPEAKER or s.upper().rstrip(".") in _NOT_SPEAKER or s.upper() in _NOT_SPEAKER
_DLG_META_RE = re.compile(r"\{[^{}]*\}\s*$")


# ---------------------------------------------------------------- 基础工具

def read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
    except Exception:
        return None


def read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8") if p.is_file() else ""
    except Exception:
        return ""


def _num(v) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.search(r"-?\d+(?:\.\d+)?", v)
        return float(m.group(0)) if m else None
    return None


def _s(v) -> str | None:
    return v.strip() if isinstance(v, str) and v.strip() else None


def _first(d: dict, *keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, "", [], {}):
            return v
    return None


def _clip(text: str, n: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def _strip_md(s: str) -> str:
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"`(.+?)`", r"\1", s)
    return s.strip()


def _tempo(v) -> str | None:
    if not isinstance(v, str) or not v.strip():
        return None
    t = re.split(r"[（(,，;；/·\s]", v.strip().lower(), 1)[0] or v.lower()
    if any(k in t for k in ("快", "fast", "急", "montage", "蒙太奇")):
        return "fast"
    if any(k in t for k in ("慢", "slow", "缓", "长镜", "留白", "hold")):
        return "slow"
    return "medium"


def _tod(field: str) -> str | None:
    f = field.strip()
    if not f or len(f) > 14:
        return None
    if any(w in f for w in _TOD_WORDS):
        return re.split(r"[—\-–]{2,}|——", f)[0].strip()
    return None


_PLACEMENT_VO_RE = re.compile(r"V\.\s?O\.?|\bVO\b|\bOV\b|画外音|内心|voice[- ]?over", re.I)
_PLACEMENT_OS_RE = re.compile(r"O\.\s?[SC]\.?|\bOS\b|画外|off[- ]?screen", re.I)


def _placement_mark(raw: str) -> str | None:
    """说话人段 / 括注 / 〔kind〕里的人物画外标记(2026-10-03 声画分离):(V.O.)/画外音/内心 → vo;(O.S.)/(O.C.)/画外 → os;
    没有标记 → None。「画外音」含「画外」,先判 vo。只对人物台词有意义,旁白者文本另走 narration_candidates。"""
    raw = raw or ""
    if _PLACEMENT_VO_RE.search(raw):
        return "vo"
    if _PLACEMENT_OS_RE.search(raw):
        return "os"
    return None


def _speaker(raw: str) -> tuple[str | None, str | None, bool]:
    """说话人段 → (CHAR id, 名字, 是否画外/旁白)。"""
    raw = _strip_md(raw or "")
    vo = bool(re.search(r"V\.?O\.?|O\.?S\.?|画外|旁白|叙述|narrat|voice[- ]?over|off[- ]?screen", raw, re.I))
    m = _CHAR_RE.search(raw)
    cid = m.group(0) if m else None
    name = _CHAR_RE.sub("", raw)
    name = re.sub(r"[（(][^()（）]*[)）]", "", name)      # (V.O.) / (CHAR-0001) 等括注
    name = re.sub(r"〔[^〕]*〕|【[^】]*】", "", name).strip(" :：·-—")
    return cid, (name or None), vo


def _dlg_meta(meta: str | None) -> dict:
    out: dict[str, Any] = {}
    if not meta:
        return out
    m = re.search(r"emotion\s*[:：]\s*([^,，}]+)", meta)
    if m:
        out["emotion"] = m.group(1).strip().strip("\"'")
    m = re.search(r"est_duration_s\s*[:：]\s*([\d.]+)", meta)
    if m:
        out["est_s"] = float(m.group(1))
    m = re.search(r"\bpace\s*[:：]\s*(fast|medium|slow)\b", meta, re.I)   # 语速档(2026-10-03 时间尺,dialogue-rewrite 随情绪写)
    if m:
        out["pace"] = m.group(1).lower()
    m = re.search(r"style_hits\s*[:：]\s*\[(.*?)\]", meta)
    if m:
        out["style_hits"] = [x.strip().strip("\"'") for x in m.group(1).split(",") if x.strip().strip("\"'")]
    return out


def _clean_line(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^[「“\"']+|[」”\"']+$", "", text).strip()
    return _strip_md(text)


# ---------------------------------------------------------------- screenplay.md 解析

def _parse_heading(text: str) -> dict | None:
    """场次标题 → 字段;不是场次标题返回 None。"""
    t = text.strip().strip("[]").strip()
    t = _strip_md(t)
    if not _SCENE_TOKEN.match(t):
        return None
    no = _SCENE_TOKEN.match(t).group(1).upper()
    rest = t[len(no):].lstrip(" |｜·:：")
    fields = [f.strip() for f in re.split(r"\s*[|｜]\s*", rest) if f.strip()]
    sc: dict[str, Any] = {"no": no, "scene_id": None, "scene_name": None, "int_ext": None,
                          "time_of_day": None, "events": [], "cast": [], "alloc_s": None,
                          "segment": None, "note": None, "split_note": None}
    if not fields and rest:
        fields = [rest]
    leftovers: list[str] = []
    for f in fields:
        f0 = f
        m = _SCN_RE.search(f) or re.search(r"(?:scene|场景)\s*[:：]\s*([\w\-]+)", f, re.I)
        if m and not sc["scene_id"]:
            sc["scene_id"] = m.group(0) if m.re is _SCN_RE else m.group(1)
            f = f.replace(m.group(0), "").strip(" :：·")
            f = re.sub(r"^(scene|场景)\s*[:：]?", "", f, flags=re.I).strip(" :：·")
            parts = [x for x in re.split(r"\s*[·•]\s*", f) if x]
            if len(parts) > 1 and _tod(parts[-1]) and not sc["time_of_day"]:
                sc["time_of_day"] = _tod(parts[-1])
                parts = parts[:-1]
            f = "·".join(parts)
            if f and not sc["scene_name"] and not _tod(f) and f.upper() not in _INT_EXT:
                sc["scene_name"] = f
            continue
        ev = _EV_RE.findall(f)
        if ev and not _CHAR_RE.search(f):
            sc["events"] = ev
            continue
        ch = _CHAR_RE.findall(f)
        if ch:
            sc["cast"] = ch
            continue
        m = _DUR_RE.fullmatch(f) or re.fullmatch(r"\[?(?:时长|DURATION|DUR)\]?\s*[:：]?\s*(\d+(?:\.\d+)?)\s*s?", f, re.I)
        if m:
            sc["alloc_s"] = float(m.group(1))
            continue
        m = re.match(r"^(段\s*\d+|SEG(?:MENT)?\s*\d+)", f, re.I)
        if m:
            sc["segment"] = m.group(1)
            continue
        words = re.split(r"\s+", f)
        matched = False
        for w in words:
            # 「INT(水下)」「EXT(水下)→EXT」:括注不影响内外景判定
            key = re.sub(r"[((][^))]*[))]", "", w.upper()).replace("→", "/").replace("->", "/")
            m2 = re.fullmatch(r"(INT|EXT)[/\-]+(INT|EXT)", key)
            if key in _INT_EXT and not sc["int_ext"]:
                sc["int_ext"] = _INT_EXT[key]
                matched = True
            elif m2 and not sc["int_ext"]:
                sc["int_ext"] = m2.group(1) if m2.group(1) == m2.group(2) else "INT/EXT"
                matched = True
            elif _tod(w) and not sc["time_of_day"]:
                sc["time_of_day"] = _tod(w)
                m2 = re.search(r"(?:——|—|--)\s*(.+)$", w)
                if m2:
                    sc["note"] = m2.group(1).strip()
                matched = True
            else:
                leftovers.append(w)
        if (not matched and f0 and not sc["time_of_day"] and sc["scene_id"] and f is fields[-1]
                and len(f0) <= 14 and (sc["scene_name"] or len(fields) >= 4)):
            sc["time_of_day"] = f0          # 场头末段按位置认作时段(输出语言任意,不靠时段词表)
            leftovers = [w for w in leftovers if w not in f0.split()]
            matched = True
        if not matched and f0 and not sc["scene_name"] and len(f0) <= 40:
            sc["scene_name"] = f0
            leftovers = [w for w in leftovers if w not in f0.split()]
    if not sc["scene_name"] and leftovers:
        sc["scene_name"] = " ".join(leftovers)[:40]
    return sc


_DLG_PATTERNS = [
    # - **老道儿(CHAR-0002)**(四下张望):叫我? {emotion: …}   /  - **CHAR-0002 纣王**:「…」 {…}
    re.compile(r"^(?:[-*]\s*)?\*\*(?P<spk>[^*]+?)\*\*\s*(?P<paren>[（(][^()（）]*[)）])?\s*[:：]\s*(?P<text>.+)$"),
    # [LN-ep01-01] 章墨(CHAR-0001)〔OV〕(擦汗)：台词(括注/〔〕次序不限,在 _speaker 里拆)
    re.compile(r"^(?:[-*]\s*)?\[(?P<id>[A-Za-z][\w-]*-[\w-]+)\]\s*(?P<spk>[^:：]+?)\s*[:：]\s*(?P<text>.+)$"),
    # CHAR-0002:「佳宁？」 {…}   /  CHAR-0011:「早安，九位。」
    re.compile(r"^(?:[-*]\s*)?(?P<spk>" + CHAR_ID_PAT + r")\s*(?P<paren>[（(][^()（）]*[)）])?\s*[:：]\s*(?P<text>.+)$"),
    # 老者：「……」(仅接受引号包裹的台词,避免把「动作:」「声音:」当说话人)
    re.compile(r"^(?:[-*]\s*)?(?P<spk>[^\s:：「「\[\]*|#>][^:：「「\[\]*|#>]{0,19}?)\s*(?P<paren>[（(][^()（）]*[)）])?\s*[:：]\s*(?P<text>[「“\"].+)$"),   # 英文名可含空格(Old Man)
]


def _match_dialogue(line: str, known: set[str] | None = None) -> dict | None:
    for i, pat in enumerate(_DLG_PATTERNS):
        m = pat.match(line)
        if not m:
            continue
        spk = m.group("spk").strip()
        if i == 3 and (_not_speaker(spk) or re.match(r"^(?:旁白候选|NARRATION)", spk, re.I)):
            return None
        if _not_speaker(spk) or re.search(r"[|｜]", spk):
            return None
        text = m.group("text").strip()
        meta = _DLG_META_RE.search(text)
        fields = _dlg_meta(meta.group(0) if meta else None)
        if meta:
            text = text[: meta.start()].strip()
        if i == 0 and not _CHAR_RE.search(spk) and "est_s" not in fields and not re.search(r"V\.?O\.?|O\.?S\.?|画外|旁白|narrat", spk, re.I):
            nm = _speaker(spk)[1] or ""
            # 粗体前缀 + 冒号的说明行(- **约束**:…)不是台词:无 ID 时只认已知角色名或短名+引号台词
            if not (nm in (known or ()) or (len(nm) <= 4 and re.match(r"^[「“\"]", text))):
                return None
        km = re.search(r"〔([^〕]*)〕", spk)
        kind = km.group(1).strip() if km else ""
        cid, name, vo = _speaker(spk)
        if kind and re.search(r"OV|VO|OS|画外|旁白|narrat", kind, re.I):
            vo = True
        paren = m.groupdict().get("paren")
        if paren is None:
            pm = [x for x in re.findall(r"[（(][^()（）]*[)）]", spk) if not _CHAR_RE.search(x)]
            paren = pm[-1] if pm else None
        out = {"id": m.groupdict().get("id"), "speaker": cid or name, "speaker_id": cid, "speaker_name": name,
               "vo": vo, "paren": paren.strip("（()）") if paren else None, "text": _clean_line(text), **fields}
        # 人物画外 / V.O.(2026-10-03 声画分离):带 CHAR 编号或已知人物名的说话人标了 (O.S.)/(V.O.) 仍是对白,记 placement;
        # 旁白者(旁白/叙述/narrator)不记 placement,照旧进旁白候选
        # 只认说话人括注 (V.O.)/(O.S.) 这类标记;〔OV〕/〔旁白〕kind 标签是旁白候选的写法(offer 系),照旧归旁白
        is_char = bool(cid) or (name is not None and name in (known or ()))
        if is_char and not re.search(r"旁白|叙述|narrat", f"{spk} {kind}", re.I) \
                and not (kind and re.search(r"OV|VO|OS|画外", kind, re.I)):
            pl = _placement_mark(f"{spk} {paren or ''}")
            if pl:
                out["placement"] = pl
        return out
    return None


def _push(cur: dict, kind: str, item) -> None:
    """按剧本原顺序记小块:连续的动作段并成一块、连续台词并成一块;旁白/转场各自成块。"""
    blocks = cur["blocks"]
    last = blocks[-1] if blocks else None
    if kind == "action":
        if last and last["type"] == "action":
            last["text"] += "\n" + item
        else:
            blocks.append({"type": "action", "text": item})
    elif kind == "sound":
        if last and last["type"] in ("action", "sound"):
            last["text"] += "\n♪ " + item
            last["type"] = last["type"]
        else:
            blocks.append({"type": "sound", "text": "♪ " + item})
    elif kind == "dialogue":
        if last and last["type"] == "dialogue":
            last["lines"].append(item)
        else:
            blocks.append({"type": "dialogue", "lines": [item]})
    elif kind == "narration":
        blocks.append({"type": "narration", **item})
    elif kind == "transition":
        blocks.append({"type": "transition", "text": item})


def parse_screenplay(text: str, known_names: set[str] | None = None) -> dict:
    """screenplay.md → {title, logline, pov, budget_s, scenes[]}。scenes 每项含 action / dialogue[] /
    narration_candidates[] / transition / notes,以及按原文顺序切好的 blocks[](action/sound/dialogue/narration/transition)。"""
    out: dict[str, Any] = {"title": None, "logline": None, "pov": None, "budget_s": None, "scenes": []}
    cur: dict | None = None
    block = None          # 当前 ### 子块类型:action / narration / dialogue / other
    for raw in text.splitlines():
        line = raw.rstrip()
        s = line.strip()
        if not s:
            continue
        hm = re.match(r"^(#{1,6})\s*(.*)$", s)
        if hm:
            level, htxt = len(hm.group(1)), hm.group(2).strip()
            if level == 1 and not out["title"]:
                out["title"] = _strip_md(htxt)
                continue
            head = _parse_heading(htxt) if level >= 2 else None
            if head:
                cur = {**head, "action": [], "dialogue": [], "narration_candidates": [], "transition": None,
                       "notes": [], "sound": [], "blocks": []}
                out["scenes"].append(cur)
                block = None
                continue
            if level == 2:
                cur = None          # 非场次的二级标题(元信息/核对表/交接说明)结束当前场
                block = None
                continue
            if cur is not None:     # 场内子块
                hl = htxt.lower()
                block = ("narration" if ("旁白" in htxt or "narrat" in hl or "v.o." in hl) else
                         "dialogue" if ("对白" in htxt or "dialog" in hl) else
                         "sound" if ("声" in htxt or "音" in htxt or "sound" in hl or "sfx" in hl or "music" in hl or "audio" in hl)
                         else "action")
            continue
        if cur is None:
            # 集级元信息
            m = re.search(r"(?:时长预算|目标时长|正片净时长|duration_budget_s|duration budget|target duration|runtime)\**\s*[:：]?\s*[—-]*\s*\**\s*(\d+(?:\.\d+)?)", s, re.I)
            if m and out["budget_s"] is None:
                out["budget_s"] = float(m.group(1))
            m = re.search(r"(?:本集看点|logline)\**\s*[:：—]+\s*(.+)$", s, re.I)
            if m and not out["logline"]:
                out["logline"] = _strip_md(m.group(1))
            m = re.search(r"(?:叙述人称|叙述视角|人称|pov)\**\s*[:：]\s*\**(.+?)\**\s*$", s, re.I)
            if m and not out["pov"] and len(m.group(1)) < 80:
                out["pov"] = _strip_md(m.group(1)).split("|")[0].strip()
            continue
        if s.startswith("|") or s.startswith(">"):
            continue
        # 场内键值元信息行(scene_id: … / 时长估算:~175s / bible_scene: …)不是画面
        if re.match(r"^(?:[-*]\s*)?[*`]*(scene_id|scene|bible_scene|scene_refs|event|events|event_refs|refs|时长估算|时长|事件|场景|地点|出场角色|人物|cast|characters|present|location|duration|est_duration)[*`]*\s*[:：]", s, re.I):
            ch = _CHAR_RE.findall(s)
            if ch and re.match(r"^(?:[-*]\s*)?\**(出场角色|人物|cast|characters|present)", s, re.I):
                cur["cast"] = list(dict.fromkeys(cur["cast"] + ch))
            m = re.search(r"(?:时长估算|时长|duration|est_duration)\**\s*[:：]\s*~?\s*(\d+(?:\.\d+)?)\s*s", s, re.I)
            if m and cur["alloc_s"] is None:
                cur["alloc_s"] = float(m.group(1))
            continue
        # 元信息行:[事件] / [出场] / [时长]
        if (re.search(r"[\[〔]\s*(事件|出场|出场角色|时长|在场|EVENTS?|CAST|PRESENT|DURATION|DUR)(?![A-Za-z])", s, re.I)
                or re.match(r"^(?:[-*]\s*)?(在场|出场|CAST|PRESENT)\s*[:：]", s, re.I)):
            ch = _CHAR_RE.findall(s)
            if ch:
                cur["cast"] = list(dict.fromkeys(cur["cast"] + ch))
            ev = [e for e in _EV_RE.findall(s) if e.lower() != "ev"]
            if ev and not cur["events"]:
                cur["events"] = ev
            m = re.search(r"\[\s*(?:时长|DURATION|DUR)\s*\]?\s*[:：]?\s*(\d+(?:\.\d+)?)\s*s?", s, re.I)
            if m and cur["alloc_s"] is None:
                cur["alloc_s"] = float(m.group(1))
            continue
        tr = scene_links.transition_line(s)   # 转场行(含场间衔接花括号,docs/scene_links.md)统一由 scene_links 识别
        if tr:
            cur["transition"] = tr
            _push(cur, "transition", tr)
            continue
        m = re.match(r"^\(?\s*adaptation_note\s*[:：]\s*(.+?)\)?\s*$", s, re.I)
        if m:
            cur["notes"].append(m.group(1).strip())
            continue
        m = re.match(r"^\(?\s*split_note\s*[:：]\s*(.+?)\)?\s*$", s, re.I)
        if m:   # 与上一场同时空仍分场的理由(scene_spacetime_continuous 豁免,2026-09-29)
            cur["split_note"] = m.group(1).strip()
            continue
        m = re.match(r"^[\[〔]?(?:旁白候选|NARRATION(?: CANDIDATE)?|V\.?O\.?)\s*(?:[（(]([^)）]*)[)）])?\s*[〕\]]?\s*[:：]?\s*(.*)$", s, re.I)
        if m:
            if m.group(2).strip():
                cur["narration_candidates"].append({"speaker": _strip_md(m.group(1) or "") or None, "text": _clean_line(m.group(2))})
                _push(cur, "narration", cur["narration_candidates"][-1])
            else:
                block = "narration"      # 〔旁白候选(…)〕标题行:其后的列表项都是旁白候选
            continue
        if block == "narration" and re.match(r"^[-*]\s+", s):
            cur["narration_candidates"].append({"speaker": None, "text": _clean_line(re.sub(r"^[-*]\s+", "", s).split("〔")[0])})
            _push(cur, "narration", cur["narration_candidates"][-1])
            continue
        if re.match(r"^[〔\[]\s*(?:本场无对白|NO DIALOGUE)", s, re.I):
            continue
        m = re.match(r"^(?:[-*]\s*)?(时段|声音|音效|音乐|TIME|SOUND|SFX|MUSIC|AUDIO)\s*[:：]\s*(.+)$", s, re.I)
        if m:
            if m.group(1).upper() in ("时段", "TIME") and not cur["time_of_day"]:
                cur["time_of_day"] = _tod(m.group(2).split("(")[0][:14]) or m.group(2)[:14]
            else:
                cur["sound"].append(_strip_md(m.group(2)))
                _push(cur, "sound", cur["sound"][-1])
            continue
        d = _match_dialogue(s, known_names)
        if d and d["text"]:
            in_narr_block = block == "narration"
            if block == "narration" and not d["speaker_id"]:
                block = None
            # 人物的 (O.S.)/(V.O.) 句(带 placement)是对白,不是旁白候选(2026-10-03 声画分离);### 旁白 块内的仍归旁白
            if (d["vo"] and not d.get("placement")) or in_narr_block:
                cur["narration_candidates"].append({"speaker": d["speaker_name"] or d["speaker"],
                                                    "speaker_id": d["speaker_id"], "text": d["text"],
                                                    "est_s": d.get("est_s")})
                _push(cur, "narration", cur["narration_candidates"][-1])
            else:
                d.pop("vo", None)
                cur["dialogue"].append(d)
                _push(cur, "dialogue", d)
            continue
        # 动作/画面:动作:… / △… / 【画面/动作】子块的自由段落 / 列表镜头描述
        m = re.match(r"^(?:[-*]\s*)?\**(?:动作|画面|画面/动作|ACTION|VISUAL)\**\s*[:：]\s*(.+)$", s, re.I)
        if m:
            block = None
            cur["action"].append(_strip_md(re.sub(r"〔[^〕]*〕", "", m.group(1))))
            _push(cur, "action", cur["action"][-1])
            continue
        if s.startswith("△"):
            cur["action"].append(_strip_md(s.lstrip("△ ")))
            _push(cur, "action", cur["action"][-1])
            continue
        if s.startswith("**") and s.endswith("**") and len(s) < 60:
            continue                # 粗体小标签行
        if s.startswith("```") or s.startswith("---"):
            continue
        s2 = re.sub(r"^[-*]\s*", "", s)
        s2 = re.sub(r"^\*\*[^*]+\*\*\s*", "", s2)      # - **镜 1**〔…〕…
        s2 = _strip_md(s2)
        if s2 and block != "dialogue":
            cur["action"].append(s2)
            _push(cur, "action", s2)
    return out


# ---------------------------------------------------------------- narration / hooks / pacing 归一

_NARR_RE = re.compile(r"^\[(?P<id>N-\d+[A-Za-z]?)\s*\|\s*(?P<meta>[^\]]*)\]\s*$")


def parse_narration_md(text: str) -> list[dict]:
    items: list[dict] = []
    cur: dict | None = None
    for raw in text.splitlines():
        s = raw.strip()
        if cur is not None:
            if s and not s.startswith("```") and not s.startswith("(") and not s.startswith("（") and not s.startswith("["):
                if cur["text"]:
                    cur["text"] += "\n" + s
                else:
                    cur["text"] = s
                continue
            if not s or s.startswith("```") or s.startswith("["):
                if cur["text"]:
                    items.append(cur)
                    cur = None
                if not s.startswith("["):
                    continue
        m = _NARR_RE.match(s)
        if not m:
            continue
        meta, last = {}, None
        for part in m.group("meta").split("|"):
            km = re.match(r"^\s*([A-Za-z_][\w]*)\s*[:：]\s*(.*)$", part)
            if km:
                last = km.group(1).lower()
                meta[last] = km.group(2).strip()
            elif last and part.strip():
                # 锚点四段「S01 | SCN-0018 | ev1000 | 场首「…」一镜」用 | 分隔却只有首段带键名:
                # 后续无键段拼回上一个键(2026-09-15 前只留首段,故事板挂镜要靠第四段引文)
                meta[last] += " | " + part.strip()
        cur = {"id": m.group("id"), "anchor": meta.get("anchor"), "est_s": _num(meta.get("est_duration_s")),
               "tone": meta.get("tone"), "source": meta.get("source"), "text": ""}
    if cur is not None and cur["text"]:
        items.append(cur)
    for it in items:
        it["scene"] = _anchor_scene(it.get("anchor"))
    return items


def _anchor_scene(anchor: str | None) -> str | None:
    if not anchor:
        return None
    m = re.match(r"^\s*(S\d{1,3}[A-Za-z]?(?:[-–]\d+)?|OH|EC|FRAME[-_]\w+)(?=$|[^A-Za-z0-9])", anchor, re.I)
    return m.group(1).upper() if m else None


def parse_narration_json(d: dict) -> list[dict]:
    out = []
    for e in d.get("entries") or d.get("items") or []:
        if not isinstance(e, dict):
            continue
        text = _s(e.get("text")) or _s(e.get("line"))
        if not text:
            continue
        anchor = _s(e.get("anchor")) or _s(e.get("scene")) or _s(e.get("unit"))
        out.append({"id": e.get("id") or e.get("num") or f"N-{len(out)+1:02d}", "anchor": anchor,
                    "scene": _anchor_scene(anchor), "est_s": _num(e.get("est_duration_s") or e.get("est_s")),
                    "tone": _s(e.get("tone")), "text": text})
    return out


def _hook_item(h: dict, kind: str) -> dict:
    pos = h.get("position_anchor") if isinstance(h.get("position_anchor"), dict) else {}
    scene = (_s(pos.get("scene")) or _s(h.get("source_scene")) or _s(h.get("scene")) or _s(h.get("unit"))
             or _s(h.get("source_scene_id")))
    return {"id": _s(h.get("id")) or "", "kind": kind, "label": _s(h.get("label")) or _s(h.get("route")),
            "type": _s(h.get("type")), "copy": _s(h.get("copy")), "visual": _clip(_s(h.get("visual")) or "", 200),
            "scene": _anchor_scene(scene) or scene, "position": _s(pos.get("position")) or _s(pos.get("placement")) or _s(h.get("position")),
            "est_s": _num(h.get("est_duration_s")), "status": _s(h.get("status")),
            "intent": _clip(_s(h.get("intent")) or "", 160), "risk": _clip(_s(h.get("risk")) or "", 120),
            "recommended": bool(h.get("recommended")), "teases": _s(h.get("teases_next_ep"))}


def normalize_hooks(d: dict | None) -> dict:
    d = d or {}
    out = {"opening": [], "mid": [], "ending": [], "selected": {}, "recommendation": {}, "retired": 0, "note": None}
    for k in ("opening_hooks", "opening"):
        out["opening"] += [_hook_item(h, "opening") for h in d.get(k) or [] if isinstance(h, dict)]
    for k in ("mid_hooks", "mid_suspense", "segment_hooks", "hooks"):
        out["mid"] += [_hook_item(h, "mid") for h in d.get(k) or [] if isinstance(h, dict)]
    for k in ("ending_cliffhangers", "ending"):
        out["ending"] += [_hook_item(h, "ending") for h in d.get(k) or [] if isinstance(h, dict)]
    sel = d.get("selected") if isinstance(d.get("selected"), dict) else {}
    out["selected"] = {k: v for k, v in sel.items() if isinstance(v, (str, list)) or v is None}
    rec = d.get("agent_recommendation") if isinstance(d.get("agent_recommendation"), dict) else {}
    out["recommendation"] = {k: v for k, v in rec.items() if isinstance(v, str) and k in ("opening", "ending", "mid")}
    out["retired"] = len(d.get("retired") or []) if isinstance(d.get("retired"), list) else 0
    meta = d.get("_meta") if isinstance(d.get("_meta"), dict) else {}
    ruling = meta.get("opening_hook_ruling") if isinstance(meta.get("opening_hook_ruling"), dict) else {}
    out["note"] = _clip(_s(ruling.get("decision")) or _s(sel.get("opening_note")) or "", 200) or None
    return out


def _pacing_scene(p: dict) -> dict:
    """pacing.json scenes[] 一项 → 统一字段。"""
    emo = p.get("emotion")
    e_type, e_int = None, None
    if isinstance(emo, dict):
        e_type = _s(emo.get("type")) or _s(emo.get("label")) or _s(emo.get("mood"))
        e_int = _num(emo.get("intensity"))
    elif isinstance(emo, str):
        e_type = emo
    if e_int is None:
        e_int = _num(p.get("intensity"))
    tr = p.get("time_range_s")
    start = _num(_first(p, "start_s", "t_start_s")) if not isinstance(tr, list) else _num(tr[0])
    end = _num(_first(p, "end_s", "t_end_s")) if not isinstance(tr, list) else (_num(tr[1]) if len(tr) > 1 else None)
    color = p.get("color_script_alignment") or p.get("color_align") or p.get("color") or {}
    color_s = (_s(color) if isinstance(color, str) else
               (_s(color.get("mood")) or _s(color.get("note")) or _s(color.get("act_name")) if isinstance(color, dict) else None))
    color_s = color_s or _s(p.get("color_segment")) or _s(p.get("color_mood"))
    no = _s(_first(p, "scene", "unit", "scene_no", "segment_id", "id")) or ""
    return {"no": no.upper() if re.match(r"^s\d", no, re.I) else no, "title": _s(_first(p, "title", "scene_ref", "function")),
            "alloc_s": _num(_first(p, "alloc_s", "duration_s", "seconds")), "start_s": start, "end_s": end,
            "dialogue_s": _num(_first(p, "dialogue_est_s", "dialogue_floor_s")), "narration_s": _num(p.get("narration_est_s")),
            "silent_s": _num(p.get("silent_s")), "lines": _num(p.get("dialogue_lines")),
            "emotion": {"type": e_type, "intensity": e_int} if (e_type or e_int is not None) else None,
            "tempo_label": _s(p.get("tempo")) or _s(p.get("curve_role")) or _s(p.get("pace")),
            "tempo": _tempo(_s(p.get("tempo")) or _s(p.get("pace"))),
            "beat": _s(_first(p, "peak_valley", "curve_role", "hook_beat")),
            "purpose": _clip(_s(_first(p, "pacing_intent", "function", "pacing_note", "execution_note", "notes")) or "", 220) or None,
            "color": _clip(color_s or "", 80) or None,
            "narration_ids": [x for x in (p.get("narration_ids") or p.get("narration_anchors") or []) if isinstance(x, str)],
            "events": [e for x in (p.get("events") or p.get("event_ids") or ([p["event"]] if isinstance(p.get("event"), str) else []))
                       if isinstance(x, str) for e in _EV_RE.findall(x)],
            "scene_id": (lambda m: m.group(0) if m else None)(_SCN_RE.search(str(_first(p, "scene_id", "scene_ref") or ""))),
            "incompressible": [{"item": _s(i.get("item")), "s": _num(i.get("s"))} for i in (p.get("incompressible") or []) if isinstance(i, dict)]}


def normalize_pacing(d: dict | None) -> dict:
    d = d or {}
    scenes = [_pacing_scene(p) for p in d.get("scenes") or [] if isinstance(p, dict)]
    curve: list[dict] = []
    ec = d.get("emotion_curve")
    pts = None
    if isinstance(ec, dict):
        pts = ec.get("points") or ec.get("sequence") or ec.get("curve")
    elif isinstance(ec, list):
        pts = ec
    for i, p in enumerate(pts or []):
        if not isinstance(p, dict):
            continue
        inten = _num(p.get("intensity"))
        if inten is None:
            continue
        unit = _s(_first(p, "unit", "scene", "beat")) or ""
        curve.append({"scene": _anchor_scene(unit) or unit, "t_s": _num(_first(p, "t_s", "at_s", "time_s")),
                      "intensity": inten, "label": _s(_first(p, "label", "mark", "beat", "note"))})
    if not curve:  # cui1 式:逐场 emotion_curve[]
        for p in d.get("scenes") or []:
            if not isinstance(p, dict) or not isinstance(p.get("emotion_curve"), list):
                continue
            base = _num(_first(p, "start_s", "t_start_s")) or (_num((p.get("time_range_s") or [None])[0]) if isinstance(p.get("time_range_s"), list) else None) or 0
            for q in p["emotion_curve"]:
                if isinstance(q, dict) and _num(q.get("intensity")) is not None:
                    curve.append({"scene": _s(p.get("scene")), "t_s": base + (_num(q.get("at_scene_s")) or 0),
                                  "intensity": _num(q.get("intensity")), "label": _s(q.get("beat"))})
    trims = []
    for tsug in d.get("trim_suggestions") or []:
        if isinstance(tsug, dict):
            trims.append({"scene": _s(tsug.get("scene")) or _s(tsug.get("unit")), "save_s": _num(tsug.get("save_s")),
                          "how": _clip(_s(tsug.get("how")) or "", 200), "risk": _s(tsug.get("risk")),
                          "status": _s(tsug.get("status")), "stale": _clip(_s(tsug.get("staleness")) or "", 160) or None})
    shape = ec.get("shape") if isinstance(ec, dict) else None
    return {"scenes": scenes, "curve": curve, "trims": trims, "budget_s": _num(d.get("duration_budget_s")),
            "total_s": _num(d.get("total_s")), "shape": _clip(_s(shape) or "", 300) or None,
            "hook_reserve": d.get("hook_reserve_s") if isinstance(d.get("hook_reserve_s"), dict) else None}


# ---------------------------------------------------------------- 推导

def _cast_index(base: Path) -> dict[str, dict]:
    idx = read_json(base / "bible" / "characters" / "index.json") or {}
    out = {}
    for c in idx.get("characters") or idx.get("entries") or []:
        if isinstance(c, dict) and c.get("id"):
            out[c["id"]] = {"name": _s(c.get("canonical_name")) or _s(c.get("name")) or c["id"],
                            "role": _s(c.get("tier_cn")) or _s(c.get("role")) or None}
    return out


def _scene_index(base: Path) -> dict[str, str]:
    idx = read_json(base / "bible" / "scenes" / "index.json") or {}
    return {s["id"]: (_s(s.get("name")) or _s(s.get("canonical_name")) or s["id"]) for s in idx.get("scenes") or idx.get("entries") or []
            if isinstance(s, dict) and s.get("id")}


def _plan_episode(plan: dict | None, ep: str) -> dict | None:
    for e in (plan or {}).get("episodes") or []:
        if isinstance(e, dict) and (e.get("episode_id") == ep or e.get("ep") == ep or e.get("id") == ep):
            return e
    return None


def _ep_events(events: dict | list | None, ids: list[str]) -> list[dict]:
    lst = events.get("events") if isinstance(events, dict) else events
    want = set(ids)
    out = []
    for e in lst or []:
        if not isinstance(e, dict) or e.get("id") not in want:
            continue
        chars = e.get("characters") or []
        out.append({"id": e["id"], "chapter": _s(e.get("chapter")), "time_hint": _s(e.get("time_hint")),
                    "location": _s(e.get("location")), "characters": [c for c in chars if isinstance(c, str)][:8],
                    "importance": _s(e.get("importance")), "cause": _clip(_s(e.get("cause")) or "", 160),
                    "process": _clip(_s(e.get("process")) or _s(e.get("summary")) or "", 260),
                    "result": _clip(_s(e.get("result")) or "", 160)})
    order = {i: n for n, i in enumerate(ids)}
    out.sort(key=lambda e: order.get(e["id"], 999))
    return out


def _structure(graph: dict | None, chapter_range: dict | None) -> dict | None:
    acts = [a for a in (graph or {}).get("acts") or [] if isinstance(a, dict)]
    if not acts:
        return None
    start = (chapter_range or {}).get("start")
    cur = None
    if isinstance(start, str):
        m = re.search(r"(\d+)", start)
        n = int(m.group(1)) if m else None
        for a in acts:
            ch = a.get("chapters") or []
            nums = [int(re.search(r"(\d+)", str(c)).group(1)) for c in ch if re.search(r"\d+", str(c))]
            if n is not None and len(nums) >= 2 and nums[0] <= n <= nums[-1]:
                cur = a.get("id")
                break
    return {"current_act": cur, "acts": [{"id": _s(a.get("id")), "title": _s(a.get("title")), "type": _s(a.get("type")),
                                           "chapters": [str(c) for c in (a.get("chapters") or [])][:2],
                                           "desc": _clip(_s(a.get("desc")) or "", 200)} for a in acts]}


def _scene_blocks(s: dict, narration: list[dict], cidx: dict) -> list[dict]:
    """场内小块(剧本原顺序):台词块带逐句 id/说话人名;剧本里的旁白候选与 narration.md 定稿条目按
    文本前缀对上则替换成定稿(带 N-id/估时/语气),对不上的定稿条目补在场尾。"""
    out: list[dict] = []
    n_items = [n for n in narration if n.get("scene") == s["no"]]
    used: set[str] = set()
    counter = 0

    import difflib
    n_cands = sum(1 for b in (s.get("blocks") or []) if b["type"] == "narration")

    def _match(text: str):
        norm = lambda t: re.sub(r"[\s,，。!！?？:：;；「」“”…—·]", "", t or "")
        key = norm(text)
        best, score = None, 0.0
        for n in n_items:
            if n["id"] in used:
                continue
            nk = norm(n.get("text"))
            r = difflib.SequenceMatcher(None, key[:40], nk[:40]).ratio() if key and nk else 0.0
            if key[:4] and key[:4] == nk[:4]:
                r = max(r, 0.6)
            if r > score:
                best, score = n, r
        # 一候选对一定稿时直接配对(旁白定稿常整句改写);否则要求相似度过半
        if best and (score >= 0.5 or (n_cands == 1 and len(n_items) == 1)):
            used.add(best["id"])
            return best
        return None

    for b in s.get("blocks") or []:
        if b["type"] == "dialogue":
            lines = []
            for d in b["lines"]:
                counter += 1
                lines.append({"id": d.get("id") or f"{s['no']}-D{counter:02d}",
                              "speaker": d["speaker_id"] or d["speaker_name"] or d["speaker"],
                              "speaker_name": d["speaker_name"] or (cidx.get(d["speaker_id"] or "", {}).get("name")),
                              "text": d["text"], "paren": d.get("paren"), "emotion": d.get("emotion"),
                              "est_s": d.get("est_s"), "style_hits": d.get("style_hits") or [],
                              **({"placement": d["placement"]} if d.get("placement") else {})})
            out.append({"type": "dialogue", "lines": lines})
        elif b["type"] == "narration":
            hit = _match(b.get("text"))
            if hit:
                out.append({"type": "narration", "id": hit["id"], "text": hit["text"], "est_s": hit.get("est_s"),
                            "tone": hit.get("tone"), "anchor": hit.get("anchor"), "speaker": b.get("speaker"), "final": True})
            else:
                out.append({"type": "narration", "id": None, "text": b.get("text"), "est_s": b.get("est_s"),
                            "speaker": b.get("speaker"), "final": False})
        else:
            out.append({"type": b["type"], "text": b.get("text")})
    tail = [{"type": "narration", "id": n["id"], "text": n["text"], "est_s": n.get("est_s"),
             "tone": n.get("tone"), "anchor": n.get("anchor"), "final": True} for n in n_items if n["id"] not in used]
    if tail and out and out[-1]["type"] == "transition":      # 未对上的定稿旁白补在场尾、转场之前
        out[-1:-1] = tail
    else:
        out += tail
    return out


def derive(base: Path, ep: str) -> dict:
    """从剧情层各文件推导拆解表(source=derived)。缺哪个文件就少哪一块,不报错。"""
    epdir = base / "story" / "episodes" / ep
    cidx, sidx = _cast_index(base), _scene_index(base)
    sp = parse_screenplay(read_text(epdir / "screenplay.md"), {c["name"] for c in cidx.values() if c.get("name")})
    pacing = normalize_pacing(read_json(epdir / "pacing.json"))
    hooks = normalize_hooks(read_json(epdir / "hooks.json"))
    narr_md = read_text(epdir / "narration.md")
    narration = parse_narration_md(narr_md) if narr_md else parse_narration_json(read_json(epdir / "narration.json") or {})
    narration_off = bool(narr_md and re.search(r"本集无旁白|无需旁白|全片零旁白|不设旁白|NO NARRATION|no narration (?:in|for) this episode", narr_md[:600], re.I))
    plan = _plan_episode(read_json(base / "story" / "episode_plan.json"), ep)
    issues: list[dict] = []

    p_by_no = {p["no"]: p for p in pacing["scenes"] if p["no"]}
    for n in narration:     # 锚点写场景 ID(SCN-xxxx)/场次名(如「框架开场」)时映射到对应场次
        if not n.get("scene") and n.get("anchor"):
            m = _SCN_RE.search(n["anchor"])
            if m:
                n["scene"] = next((x["no"] for x in sp["scenes"] + pacing["scenes"] if x.get("scene_id") == m.group(0)), None)
            if not n.get("scene"):
                head = re.split(r"[\s·:：(（,，、|]", n["anchor"].strip(), 1)[0]
                if len(head) >= 2:
                    n["scene"] = next((x["no"] for x in sp["scenes"] + pacing["scenes"]
                                       if head in str(x.get("scene_name") or x.get("title") or "")), None)
    used = set()
    scenes: list[dict] = []
    for i, s in enumerate(sp["scenes"]):
        p = p_by_no.get(s["no"]) or {}
        if p:
            used.add(s["no"])
        cast = list(dict.fromkeys(s["cast"] + [d["speaker_id"] for d in s["dialogue"] if d.get("speaker_id")]))
        dlg_s = p.get("dialogue_s")
        if dlg_s is None and s["dialogue"]:
            ests = [d.get("est_s") for d in s["dialogue"] if d.get("est_s") is not None]
            dlg_s = round(sum(ests), 1) if ests else None
        narr_ids = p.get("narration_ids") or [n["id"] for n in narration if n.get("scene") == s["no"]]
        narr_s = p.get("narration_s")
        if narr_s is None and narr_ids:
            ests = [n.get("est_s") for n in narration if n["id"] in narr_ids and n.get("est_s") is not None]
            narr_s = round(sum(ests), 1) if ests else None
        alloc = p.get("alloc_s") if p.get("alloc_s") is not None else s["alloc_s"]
        silent = p.get("silent_s")
        if silent is None and alloc is not None and (dlg_s is not None or narr_s is not None):
            silent = round(max(0.0, alloc - (dlg_s or 0) - (narr_s or 0)), 1)
        action = " ".join(s["action"])
        scene_id = s["scene_id"] or p.get("scene_id")
        scenes.append({
            "no": s["no"], "scene_id": scene_id, "scene_name": s["scene_name"] or (sidx.get(scene_id) if scene_id else None),
            "int_ext": s["int_ext"], "time_of_day": s["time_of_day"], "segment": s["segment"],
            "events": s["events"] or p.get("events") or [], "cast": cast,
            "summary": _clip(re.split(r"(?<=[。!?！？])", action, 1)[0], 80) if action else (p.get("title") or ""),
            "action": _clip(action, 700), "beat": p.get("beat"), "purpose": p.get("purpose") or (s["note"] or None),
            "alloc_s": alloc, "start_s": p.get("start_s"), "end_s": p.get("end_s"),
            "dialogue_s": dlg_s, "narration_s": narr_s, "silent_s": silent,
            "lines": len(s["dialogue"]), "emotion": p.get("emotion"),
            "tempo": p.get("tempo"), "tempo_label": p.get("tempo_label"), "color": p.get("color"),
            "transition": s["transition"], "hooks": [], "narration_ids": narr_ids,
            "dialogue": [{"id": d.get("id") or f"{s['no']}-D{n+1:02d}", "speaker": d["speaker_id"] or d["speaker_name"] or d["speaker"],
                          "speaker_name": d["speaker_name"] or (cidx.get(d["speaker_id"] or "", {}).get("name")),
                          "text": d["text"], "paren": d.get("paren"), "emotion": d.get("emotion"),
                          "est_s": d.get("est_s"), "style_hits": d.get("style_hits") or [],
                          # 人物画外 / V.O. 标记(2026-10-03 声画分离):只在剧本标了 (O.S.)/(V.O.) 时出现,画内句不带此键
                          **({"placement": d["placement"]} if d.get("placement") else {})}
                         for n, d in enumerate(s["dialogue"])],
            "blocks": _scene_blocks(s, narration, cidx),
            "narration_candidates": s["narration_candidates"], "sound": s["sound"][:6],
            "notes": s["notes"], "incompressible": p.get("incompressible") or [],
            "alloc_source": "pacing" if p.get("alloc_s") is not None else ("screenplay" if s["alloc_s"] is not None else None),
        })
        if not cast:
            issues.append({"level": "warn", "scene": s["no"], "text": f"{s['no']} 未标出场人物"})
    # pacing 里有而剧本里没有的单元(框架开场/片尾钩子等):补一行
    for p in pacing["scenes"]:
        if p["no"] and p["no"] not in used and p["no"] not in {x["no"] for x in scenes}:
            scenes.append({"no": p["no"], "scene_id": p.get("scene_id"), "scene_name": _clip(p.get("title") or "", 40) or None, "int_ext": None,
                           "time_of_day": None, "segment": None, "events": p.get("events") or [], "cast": [],
                           "summary": p.get("title") or "", "action": p.get("purpose") or "", "beat": p.get("beat"),
                           "purpose": p.get("purpose"), "alloc_s": p.get("alloc_s"), "start_s": p.get("start_s"),
                           "end_s": p.get("end_s"), "dialogue_s": p.get("dialogue_s"), "narration_s": p.get("narration_s"),
                           "silent_s": p.get("silent_s"), "lines": int(p.get("lines") or 0), "emotion": p.get("emotion"),
                           "tempo": p.get("tempo"), "tempo_label": p.get("tempo_label"), "color": p.get("color"),
                           "transition": None, "hooks": [], "narration_ids": p.get("narration_ids") or [], "dialogue": [],
                           "blocks": [{"type": "narration", "id": n["id"], "text": n["text"], "est_s": n.get("est_s"), "tone": n.get("tone"),
                                       "anchor": n.get("anchor"), "final": True} for n in narration if n.get("scene") == p["no"]],
                           "narration_candidates": [], "sound": [], "notes": [], "incompressible": p.get("incompressible") or [],
                           "alloc_source": "pacing", "pacing_only": True})
    # 顺序:有 pacing 起点的按起点排,其余插在前一个已知位置之后
    last = -1.0
    keys = []
    for sc in scenes:
        if sc.get("start_s") is not None:
            last = sc["start_s"]
            keys.append(sc["start_s"])
        else:
            last += 0.001
            keys.append(last)
    if any(sc.get("start_s") is not None for sc in scenes):
        scenes = [sc for _, sc in sorted(zip(keys, scenes), key=lambda kv: kv[0])]
    for n, sc in enumerate(scenes):
        sc["order"] = n + 1
    no_set = {sc["no"] for sc in scenes}
    for p in pacing["scenes"]:
        if p["no"] and p["no"] not in no_set:
            issues.append({"level": "warn", "scene": p["no"], "text": f"pacing 单元 {p['no']} 在剧本里找不到对应场次"})
    for sc in scenes:
        if sc["alloc_s"] is None:
            issues.append({"level": "info", "scene": sc["no"], "text": f"{sc['no']} 没有时长分配(pacing.json 缺失或未覆盖)"})
    # 钩子落场
    for kind in ("opening", "mid", "ending"):
        for h in hooks[kind]:
            for sc in scenes:
                if h.get("scene") and sc["no"] == h["scene"]:
                    sc["hooks"].append(h["id"] or kind)
    # 人物汇总
    cast_rows: dict[str, dict] = {}
    for sc in scenes:
        for cid in sc["cast"]:
            row = cast_rows.setdefault(cid, {"id": cid, "name": cidx.get(cid, {}).get("name") or cid,
                                             "role": cidx.get(cid, {}).get("role"), "scenes": [], "lines": 0, "dialogue_s": 0.0})
            if sc["no"] not in row["scenes"]:
                row["scenes"].append(sc["no"])
        for d in sc["dialogue"]:
            key = d["speaker"] or "?"
            row = cast_rows.setdefault(key, {"id": key, "name": d.get("speaker_name") or cidx.get(key, {}).get("name") or key,
                                             "role": cidx.get(key, {}).get("role"), "scenes": [], "lines": 0, "dialogue_s": 0.0})
            if sc["no"] not in row["scenes"]:
                row["scenes"].append(sc["no"])
            row["lines"] += 1
            row["dialogue_s"] = round(row["dialogue_s"] + (d.get("est_s") or 0), 1)
            if not row.get("name") or row["name"] == key:
                row["name"] = d.get("speaker_name") or row["name"]
    cast = sorted(cast_rows.values(), key=lambda r: (-r["lines"], r["id"]))
    for r in cast:
        if r["id"].startswith("CHAR-") and r["id"] not in cidx:
            issues.append({"level": "warn", "text": f"{r['id']} 不在 bible/characters/index.json"})
    for sc in scenes:
        if sc.get("scene_id") and sidx and sc["scene_id"] not in sidx:
            issues.append({"level": "warn", "scene": sc["no"], "text": f"{sc['no']} 场景 {sc['scene_id']} 不在 bible/scenes/index.json"})
    # 情绪曲线:pacing 曲线优先,否则由逐场情绪点构成
    curve = pacing["curve"] or [
        {"scene": sc["no"], "t_s": ((sc["start_s"] or 0) + (sc["alloc_s"] or 0) / 2) if sc["start_s"] is not None else None,
         "intensity": sc["emotion"]["intensity"], "label": sc["emotion"].get("type")}
        for sc in scenes if sc.get("emotion") and sc["emotion"].get("intensity") is not None]
    budget = pacing["budget_s"] or (_num((plan or {}).get("duration_budget_s")) if plan else None) or sp["budget_s"]
    allocs = [sc["alloc_s"] for sc in scenes if sc["alloc_s"] is not None]
    total = pacing["total_s"] if pacing["total_s"] is not None else (round(sum(allocs), 1) if allocs else None)
    if budget and total and abs(total - budget) > budget * 0.1:
        issues.append({"level": "warn", "text": f"估算总时长 {total:g}s 偏离预算 {budget:g}s 超过 10%"})
    if not scenes:
        issues.append({"level": "error", "text": "剧本没有解析出任何场次(screenplay.md 缺失或场次标题格式无法识别)"})
    dialogue_total = round(sum(d.get("est_s") or 0 for sc in scenes for d in sc["dialogue"]), 1)
    narr_total = round(sum(n.get("est_s") or 0 for n in narration), 1)
    for n in narration:
        if n.get("scene") and n["scene"] not in no_set:
            issues.append({"level": "warn", "text": f"旁白 {n['id']} 锚点场次 {n['scene']} 不在本集场次表"})
    ev_ids = list(dict.fromkeys(((plan or {}).get("events") or []) + [e for sc in scenes for e in sc["events"]]))
    return {
        "schema_version": SCHEMA_VERSION, "ep": ep, "source": "derived",
        "title": (plan or {}).get("title") or sp["title"], "logline": sp["logline"] or _clip((plan or {}).get("summary") or "", 200) or None,
        "pov": sp["pov"], "narration_enabled": not narration_off,
        "duration_budget_s": budget, "total_est_s": total,
        "totals": {"scenes": len(scenes), "lines": sum(sc["lines"] for sc in scenes), "dialogue_s": dialogue_total,
                   "narration_items": len(narration), "narration_s": narr_total, "cast": len(cast),
                   "hooks": len(hooks["opening"]) + len(hooks["mid"]) + len(hooks["ending"])},
        "cast": cast, "scenes": scenes, "narration": narration, "hooks": hooks,
        "emotion_curve": curve, "curve_shape": pacing["shape"], "trim_suggestions": pacing["trims"],
        "hook_reserve": pacing["hook_reserve"],
        "plan": ({"title": _s(plan.get("title")), "chapter_range": plan.get("chapter_range"), "events": plan.get("events") or [],
                  "duration_budget_s": _num(plan.get("duration_budget_s")), "summary": _s(plan.get("summary")),
                  "beats": [b for b in plan.get("beats") or [] if isinstance(b, str)],
                  "characters": [c for c in plan.get("characters") or [] if isinstance(c, str)],
                  "hook_point": plan.get("hook_point") if isinstance(plan.get("hook_point"), dict) else None,
                  "carry_over": plan.get("carry_over") or [], "recap_needed": plan.get("recap_needed") or []} if plan else None),
        "events": _ep_events(read_json(base / "story" / "events.json"), ev_ids) if ev_ids else [],
        "structure": _structure(read_json(base / "story" / "story_graph.json"), (plan or {}).get("chapter_range")),
        "issues": issues,
    }


# ---------------------------------------------------------------- 正式产物加载 / 校验

def breakdown_path(base: Path, ep: str) -> Path:
    return base / BREAKDOWN_REL.format(ep=ep)


def input_mtimes(base: Path, ep: str) -> dict[str, float]:
    out = {}
    for rel in INPUT_FILES:
        p = base / rel.format(ep=ep)
        if p.is_file():
            out[rel.format(ep=ep)] = p.stat().st_mtime
    return out


def load(base: Path, ep: str) -> dict:
    """返回 {"breakdown", "source": agent|derived|none, "stale", "file", "inputs", "errors"}。
    正式产物存在且可解析时以之为准,缺的顶层块用推导视图补齐;否则整份用推导视图。"""
    derived = derive(base, ep)
    inputs = input_mtimes(base, ep)
    p = breakdown_path(base, ep)
    has_inputs = any(k.endswith("screenplay.md") for k in inputs)
    # 场间衔接(2026-10-10,docs/scene_links.md):scenes[].link_out 一律由宿主按剧本场尾转场行现算,不靠拆解工位写
    sp_text = read_text(base / "story" / "episodes" / ep / "screenplay.md")
    if p.is_file():
        agent = read_json(p)
        if isinstance(agent, dict) and isinstance(agent.get("scenes"), list):
            errors, _ = validate(agent, base=None)
            merged = dict(derived)
            for k, v in agent.items():
                if v not in (None, [], {}, ""):
                    merged[k] = v
            merged["source"] = "agent"
            mt = p.stat().st_mtime
            stale = [k for k, v in inputs.items() if v > mt + 1]
            merged.setdefault("issues", [])
            if errors:
                merged["issues"] = [{"level": "error", "text": e} for e in errors] + list(merged["issues"])
            return {"breakdown": scene_links.annotate(merged, sp_text), "source": "agent", "stale": stale,
                    "file": str(p.relative_to(base)), "mtime": mt, "inputs": inputs, "errors": errors}
        return {"breakdown": scene_links.annotate(derived, sp_text), "source": "derived", "stale": [], "file": None, "mtime": None,
                "inputs": inputs, "errors": ["script_breakdown.json 不是合法拆解表(缺 scenes[] 或 JSON 无法解析)"]}
    return {"breakdown": scene_links.annotate(derived, sp_text) if has_inputs else None, "source": "derived" if has_inputs else "none",
            "stale": [], "file": None, "mtime": None, "inputs": inputs, "errors": []}


def validate(bd: dict, base: Path | None = None, ep: str | None = None) -> tuple[list[str], list[str]]:
    """机检 script_breakdown_ok:返回 (errors, warnings)。base 给定时对照 bible 索引核 ID。"""
    errors: list[str] = []
    warns: list[str] = []
    if not isinstance(bd, dict):
        return ["不是 JSON 对象"], []
    if bd.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version 须为 {SCHEMA_VERSION}(实际 {bd.get('schema_version')!r})")
    if ep and bd.get("ep") != ep:
        errors.append(f"ep 须为 {ep}(实际 {bd.get('ep')!r})")
    scenes = bd.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        errors.append("scenes[] 为空")
        scenes = []
    seen: set[str] = set()
    cidx = _cast_index(base) if base else {}
    sidx = _scene_index(base) if base else {}
    for i, sc in enumerate(scenes):
        if not isinstance(sc, dict):
            errors.append(f"scenes[{i}] 不是对象")
            continue
        no = sc.get("no")
        if not isinstance(no, str) or not no.strip():
            errors.append(f"scenes[{i}].no 缺失")
            continue
        if no in seen:
            errors.append(f"场次 {no} 重复")
        seen.add(no)
        if not _s(sc.get("summary")) and not sc.get("pacing_only"):
            warns.append(f"{no} 缺 summary(一句话内容)")
        emo = sc.get("emotion")
        if emo is not None:
            if not isinstance(emo, dict):
                errors.append(f"{no}.emotion 须为对象 {{type, intensity}}")
            else:
                it = emo.get("intensity")
                if it is not None and (not isinstance(it, (int, float)) or not 0 <= it <= 1):
                    errors.append(f"{no}.emotion.intensity 须在 0–1")
        for k in ("alloc_s", "dialogue_s", "narration_s", "silent_s"):
            v = sc.get(k)
            if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0):
                errors.append(f"{no}.{k} 须为非负数")
        if sc.get("tempo") not in (None, "slow", "medium", "fast"):
            errors.append(f"{no}.tempo 须为 slow|medium|fast")
        # NPC 参与构图判定(2026-10-09,docs/npc_staging.md):字段存在时查格式;缺判定由 check_script_breakdown 的 npc_judged 管
        from modules.npc_staging import judgment_errors
        errors.extend(judgment_errors(no, sc.get("npc")))
        for k, b in enumerate(sc.get("blocks") or []):
            if not isinstance(b, dict) or b.get("type") not in ("action", "sound", "dialogue", "narration", "transition"):
                errors.append(f"{no}.blocks[{k}].type 须为 action|sound|dialogue|narration|transition")
            elif b["type"] == "dialogue" and not isinstance(b.get("lines"), list):
                errors.append(f"{no}.blocks[{k}] 台词块缺 lines[]")
            elif b["type"] != "dialogue" and not _s(b.get("text")):
                errors.append(f"{no}.blocks[{k}] 缺 text")
        for j, d in enumerate(sc.get("dialogue") or []):
            if not isinstance(d, dict) or not _s(d.get("text")):
                errors.append(f"{no}.dialogue[{j}] 缺 text")
                continue
            if not _s(d.get("speaker")):
                errors.append(f"{no}.dialogue[{j}] 缺 speaker")
            es = d.get("est_s")
            if es is not None and (isinstance(es, bool) or not isinstance(es, (int, float))):
                errors.append(f"{no}.dialogue[{j}].est_s 须为数字")
        if sidx and sc.get("scene_id") and sc["scene_id"] not in sidx:
            warns.append(f"{no} 场景 {sc['scene_id']} 不在 bible/scenes/index.json")
        if cidx:
            for c in sc.get("cast") or []:
                if isinstance(c, str) and c.startswith("CHAR-") and c not in cidx:
                    warns.append(f"{no} 出场 {c} 不在 bible/characters/index.json")
    for j, n in enumerate(bd.get("narration") or []):
        if not isinstance(n, dict) or not _s(n.get("text")):
            errors.append(f"narration[{j}] 缺 text")
        elif n.get("scene") and seen and n["scene"] not in seen:
            warns.append(f"旁白 {n.get('id')} 锚点场次 {n['scene']} 不在场次表")
    for p in bd.get("emotion_curve") or []:
        if isinstance(p, dict):
            it = p.get("intensity")
            if not isinstance(it, (int, float)) or isinstance(it, bool) or not 0 <= it <= 1:
                errors.append("emotion_curve 每点 intensity 须在 0–1")
                break
    b, t = bd.get("duration_budget_s"), bd.get("total_est_s")
    if isinstance(b, (int, float)) and isinstance(t, (int, float)) and b > 0 and abs(t - b) > b * 0.1:
        warns.append(f"总估时 {t:g}s 偏离预算 {b:g}s 超过 10%")
    return errors, warns


def scene_nos(base: Path, ep: str) -> list[str]:
    """跨预览页跳转用(2026-09-12):剧本预览页会显示的场次号列表(正式拆解表优先,缺则推导视图);
    故事板/分镜预览只对存在的场次显示「📜 剧本」链接。无剧本时为空。"""
    p = breakdown_path(base, ep)
    if p.is_file():
        agent = read_json(p)
        if isinstance(agent, dict) and isinstance(agent.get("scenes"), list):
            return [str(sc.get("no")) for sc in agent["scenes"] if isinstance(sc, dict) and sc.get("no") is not None]
    if not (base / "story" / "episodes" / ep / "screenplay.md").is_file():
        return []
    d = derive(base, ep) or {}
    return [str(sc.get("no")) for sc in (d.get("scenes") or []) if isinstance(sc, dict) and sc.get("no") is not None]
