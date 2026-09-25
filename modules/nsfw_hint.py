# -*- coding: utf-8 -*-
"""nsfw_hint —— NSFW 模式的关键字提示机检(WARN-only,2026-09-25)。

只提示不路由:按双语词表扫分镜表各生成组的文字(shots 动作/姿态/描述、组字段)与组 prompt(video_prompt),
命中且该组**未**标记 NSFW(group_settings 手动/兜底、shot_list 组 nsfw 均为否)时给 WARN,
提示用户到分镜预览组卡点 🔞 标记。词表判不准是已知前提(渠道审核按自家模型判定),
所以本机检永不 FAIL、不改任何文件、不参与模型路由——真正的路由靠显式标记与审核拒收兜底(modules/genmedia.py)。

数据源:directing/<ep>/shot_list.json(generation_groups[]、shots[])、assets/prompts/<ep>/<grp>.json、
assets/group_settings/<ep>/<grp>.json。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

CHECK = "nsfw_hint"

# 类别 → 词表(中英;英文按整词匹配,中文按子串)。故意只收明确指向性强的词,减少误报。
LEXICON: dict[str, tuple[str, ...]] = {
    "nudity": ("裸体", "全裸", "半裸", "裸露", "赤裸", "脱光", "露点", "乳房", "臀部特写",
               "nude", "naked", "nudity", "topless", "bare breasts", "undressed", "strip off"),
    "sex": ("性爱", "做爱", "性交", "交欢", "云雨", "床戏", "情欲", "性暗示", "呻吟", "淫",
            "sex scene", "sexual", "intercourse", "erotic", "orgasm", "moaning", "foreplay", "seduce"),
    "gore": ("血腥", "血浆", "内脏", "开膛", "断肢", "断头", "斩首", "碎尸", "剖腹", "肢解", "血肉模糊", "脑浆",
             "gore", "gory", "dismember", "decapitat", "entrails", "disembowel", "mutilat", "severed"),
    "extreme_violence": ("虐杀", "凌虐", "折磨", "酷刑", "屠杀", "活剥", "施暴", "强暴", "强奸",
                         "torture", "massacre", "slaughter", "rape", "brutal beating", "execution"),
    "drugs": ("吸毒", "注射毒品", "海洛因", "冰毒", "可卡因", "大麻", "毒瘾",
              "heroin", "cocaine", "meth", "injecting drugs", "drug use", "overdose"),
    "self_harm": ("自杀", "自残", "割腕", "上吊", "服毒", "跳楼",
                  "suicide", "self-harm", "slit wrist", "hang himself", "hang herself", "overdose on"),
}

_EN_WORD = {w for ws in LEXICON.values() for w in ws if re.fullmatch(r"[a-z][a-z .\-]*", w)}


def _texts_of(obj) -> list[str]:
    """递归收集 dict/list 内全部字符串。"""
    out: list[str] = []
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            out.extend(_texts_of(v))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_texts_of(v))
    return out


# 负面清单/禁止句不算命中:prompt 常写「Negative: … gore, severed limbs」「禁止出现血腥」
_NEG_RE = re.compile(r"(negative|avoid|exclude|do not|don't|must not|no\b|without|禁止|不得|不要|不出现|负面|避免|勿)", re.IGNORECASE)


def strip_negatives(text: str) -> str:
    """按句/行切分,丢掉含否定/负面标记的句子,只留正向描述。"""
    keep = []
    for sent in re.split(r"(?<=[.。;;!!?？\n])", text):
        if sent.strip() and not _NEG_RE.search(sent):
            keep.append(sent)
    return "".join(keep)


def scan_text(text: str) -> dict[str, list[str]]:
    """返回 {类别: [命中词]}(去重,按词表顺序);否定句/负面清单先剔除。"""
    text = strip_negatives(text)
    low = text.lower()
    hits: dict[str, list[str]] = {}
    for cat, words in LEXICON.items():
        found = []
        for w in words:
            if w in _EN_WORD:
                if re.search(r"(?<![a-z])" + re.escape(w) + r"(?:[a-z]*)?(?![a-z])", low):
                    found.append(w)
            elif w in text:
                found.append(w)
        if found:
            hits[cat] = found
    return hits


def _read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def group_flag(root: Path, ep: str, grp: str, shot_group: dict | None) -> str:
    """组当前 NSFW 标记来源:manual / failover / shot_list / ''(与 core.group_nsfw_state 同口径)。"""
    gs = _read_json(root / "assets" / "group_settings" / ep / f"{grp}.json")
    if isinstance(gs, dict) and isinstance(gs.get("nsfw"), bool):
        if not gs["nsfw"]:
            return "manual_off"
        return "failover" if str(gs.get("nsfw_reason") or "") == "failover" else "manual"
    if isinstance(shot_group, dict) and shot_group.get("nsfw") is True:
        return "shot_list"
    return ""


def scan_episode(root: Path, ep: str) -> dict:
    """扫一集:返回 {groups: [{grp, flag, hits{cat:[words]}, sources[]}], warns: [str], missing: bool}。"""
    sl = _read_json(root / "directing" / ep / "shot_list.json")
    if not isinstance(sl, dict):
        return {"groups": [], "warns": [], "missing": True}
    shots = {str(s.get("shot_id") or ""): s for s in (sl.get("shots") or []) if isinstance(s, dict)}
    rows, warns = [], []
    for g in sl.get("generation_groups") or []:
        if not isinstance(g, dict):
            continue
        grp = str(g.get("group_id") or "")
        if not grp:
            continue
        texts, sources = [], []
        gtxt = " ".join(_texts_of({k: v for k, v in g.items() if k not in ("shots", "group_id", "scene_id")}))
        if gtxt.strip():
            texts.append(gtxt); sources.append("shot_list:group")
        stxt = " ".join(_texts_of([shots[s] for s in (g.get("shots") or []) if s in shots]))
        if stxt.strip():
            texts.append(stxt); sources.append("shot_list:shots")
        pd = _read_json(root / "assets" / "prompts" / ep / f"{grp}.json")
        if isinstance(pd, dict):
            ptxt = " ".join(_texts_of({k: pd.get(k) for k in ("video_prompt", "shots", "scene_block") if k in pd}))
            if ptxt.strip():
                texts.append(ptxt); sources.append("prompt")
        hits = scan_text("\n".join(texts))
        flag = group_flag(root, ep, grp, g)
        rows.append({"grp": grp, "flag": flag, "hits": hits, "sources": sources})
        if hits and flag in ("", "manual_off"):
            cats = "、".join(f"{c}({'/'.join(ws[:3])})" for c, ws in hits.items())
            warns.append(f"{ep}/{grp} 文字命中敏感词 {cats},但本组未标记 NSFW"
                         + ("(已手动标为否,如确需可忽略)" if flag == "manual_off" else
                            ":如需走备用模型请在分镜预览组卡点 🔞,或由 shot-planning 补 nsfw: true"))
    return {"groups": rows, "warns": warns, "missing": False}


def hint_for_group(root: Path, ep: str, grp: str, shot_group: dict | None, prompt: dict | None) -> dict[str, list[str]]:
    """预览页用:单组命中词(未标记时才返回非空;已标记 NSFW 的组不提示)。"""
    if group_flag(root, ep, grp, shot_group) in ("manual", "failover", "shot_list"):
        return {}
    texts = _texts_of({k: v for k, v in (shot_group or {}).items() if k not in ("shots",)})
    if isinstance(prompt, dict):
        texts.extend(_texts_of({k: prompt.get(k) for k in ("video_prompt", "shots", "scene_block") if k in prompt}))
    return scan_text("\n".join(texts))
