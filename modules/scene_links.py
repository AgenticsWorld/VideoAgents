"""场间衔接初稿(scene links,2026-10-10 一期;方案 docs/scene_links.md)。

匹配剪辑这类衔接要求「前一场最后一个画面 / 声音」与「后一场第一个画面 / 声音」成对设计,这两头写的是什么
由剧本决定——到分镜阶段场尾场头的内容已经定了,只能硬凑。所以由编剧在场尾「转场:」行起稿:

    转场:MATCH CUT TO S04 {衔接: 形状, 出: 符纹亮成一个圆, 入: 宝德门上同一道发亮的符}
    TRANSITION: MATCH CUT TO S04 {link: shape, out: the sigil flares into a ring, in: the same glowing sigil on the gate}

花括号三项是机器锚点(中文 `衔接/出/入` 与英文 `link/out/in` 等价,值里可以有逗号);`衔接` 只认 KINDS 白名单。
没有花括号的转场行照旧是普通转场(`CUT TO` / 黑场 / 叠化…),不受本模块约束。

分工:剧本定「接什么」(两头的内容对),导演定「拍不拍、怎么拍得对上」(directing_plan「## 转场清单」逐条处置),
时长 / 字卡 / 声桥仍归 Phase 6 过场设计与剪辑。
一期(2026-10-10):解析、剧本侧机检 scene_link_valid、导演处置机检 script_links_disposed、剧本预览页显示、过场设计诊断读取。
二期(2026-10-10):导演采纳的衔接由宿主登记进 shot_list `transition_in.link {kind, out, in, out_shot, in_shot, …}`
(modules/transition_design.propose 落、apply 写回;本模块出登记卡 link_record 与对账 verify_landed = 机检 script_links_landed),
两侧组提示词各写一句「【场间衔接】」(modules/scene_link_prompts.py,机检 scene_link_bound)。

本模块是剧本转场行的**唯一解析口**:modules/script_breakdown(剧本预览页)、modules/episode_treatments
(check_screenplay_events.py)、modules/transition_design(过场设计诊断)都从这里取,不各写一份正则。
"""
from __future__ import annotations

import re
from typing import Any

# 此日期起生成(front matter generated_at)的剧本按 FAIL 核;更早或缺失的存量剧本只 WARN、不回改
LINK_RULE_SINCE = "2026-10-10"
# 试验型衔接(形状 / 动作匹配)一集里超过这个数记 WARN:画面要逐帧重合,一期下游只当标注、不保证对得上
TRIAL_WARN_OVER = 2

# kind → 中英文名 / 档位 / 建议的转场词 / 导演采纳时落到的转场清单类型(check_generation_groups 受控枚举)
#   active 一期放开:靠现有白模同机位核查、镜头方向、声音 cue、首镜内容就能兑现
#   trial  试验:要两个画面的位置大小逐帧重合,一期只当标注(match_cut = 硬切),效果看一集再定
KINDS: dict[str, dict[str, str]] = {
    "shape":    {"zh": "形状匹配", "en": "Shape match",  "tier": "trial",  "cut": "MATCH CUT TO", "land": "match_cut",
                 "need_zh": "两个同形、同屏位的物件", "need_en": "two objects of the same shape at the same screen position"},
    "action":   {"zh": "动作匹配", "en": "Action match", "tier": "trial",  "cut": "MATCH CUT TO", "land": "match_cut",
                 "need_zh": "同一个动作,前场起、后场完成", "need_en": "one action that starts in this scene and finishes in the next"},
    "position": {"zh": "同机位",   "en": "Same framing", "tier": "active", "cut": "MATCH CUT TO", "land": "match_cut",
                 "need_zh": "同一空间同一构图,只变一样东西(时间流逝)", "need_en": "same place and framing, one thing changed (time passed)"},
    "motion":   {"zh": "运动接力", "en": "Motion relay", "tier": "active", "cut": "CUT TO", "land": "hard_cut",
                 "need_zh": "出画方向与入画方向一致", "need_en": "exit direction equals entry direction"},
    "sound":    {"zh": "声音接力", "en": "Sound relay",  "tier": "active", "cut": "CUT TO", "land": "hard_cut",
                 "need_zh": "前场的声音化成后场的声音", "need_en": "a sound in this scene becomes a sound in the next"},
    "line":     {"zh": "台词接力", "en": "Line relay",   "tier": "active", "cut": "CUT TO", "land": "hard_cut",
                 "need_zh": "前场末句的问或提到的人,后场首画面即答", "need_en": "the last line asks or names something the next scene's first image answers"},
    "contrast": {"zh": "反差硬切", "en": "Contrast cut", "tier": "active", "cut": "SMASH CUT TO", "land": "smash_cut",
                 "need_zh": "静接闹、明接暗这类反差", "need_en": "a hard contrast: quiet to loud, bright to dark"},
}
_KIND_ALIASES = {
    "shape": "shape", "graphic": "shape", "graphic match": "shape", "形状": "shape", "形状匹配": "shape", "图形": "shape", "图形匹配": "shape",
    "action": "action", "match on action": "action", "动作": "action", "动作匹配": "action",
    "position": "position", "framing": "position", "same framing": "position", "同机位": "position", "机位": "position",
    "同机位匹配": "position", "时间流逝": "position",
    "motion": "motion", "movement": "motion", "运动": "motion", "运动接力": "motion", "方向": "motion",
    "sound": "sound", "audio": "sound", "声音": "sound", "声音接力": "sound",
    "line": "line", "dialogue": "line", "台词": "line", "台词接力": "line",
    "contrast": "contrast", "smash": "contrast", "反差": "contrast", "反差硬切": "contrast",
}
_KEY_ALIASES = {"link": "link", "衔接": "link", "out": "out", "出": "out", "in": "in", "入": "in"}

_BRACE_RE = re.compile(r"\{([^{}]*)\}")
_KEY_RE = re.compile(r"(?:^|[,\uff0c;\uff1b\u3001]\s*)(link|out|in|衔接|出|入)\s*[:\uff1a]\s*", re.I)
_TARGET_RE = re.compile(r"(?:\bTO\b|切至|接至|→)\s*[:\uff1a]?\s*(S\d{1,3}[A-Za-z]?)\b", re.I)
# 转场词里点名了「设计型」剪辑却没写花括号:新剧本须补 {衔接, 出, 入}
_DESIGNED_WORD_RE = re.compile(r"MATCH\s+CUT|SMASH\s+CUT|匹配剪辑|匹配转场", re.I)
# 落黑 / 淡出类转场词:中间隔了黑,任何接力都断
_BLACK_WORD_RE = re.compile(r"BLACK|FADE|黑场|切黑|淡出|淡入", re.I)

# screenplay.md 的转场行(docs/screenplay_anchors.md):`转场:…` / `- 转场:…` / `**转场**:…` / `TRANSITION: …`,
# 或独立一行 `CUT TO:` `SMASH CUT TO` `FADE OUT.` `DISSOLVE TO:`(后面可跟花括号)
_LINE_RE = re.compile(r"^(?:[-*]\s*)?\**(?:转场|TRANSITION)\**\s*[:\uff1a]\s*\**(.+?)\**\s*$", re.I)
_BARE_RE = re.compile(r"^\**((?:SMASH |MATCH |JUMP )?CUT TO(?: BLACK)?|FADE (?:IN|OUT|TO BLACK)|DISSOLVE TO|WIPE TO|CROSSFADE|INTERCUT)"
                      r"(\s+S\d{1,3}[A-Za-z]?)?\s*[:.]?\**\s*(\{[^{}]*\})?\s*$", re.I)


def transition_line(line: str) -> str | None:
    """一行是不是转场行;是则返回转场文本(去掉行首关键词与 Markdown 粗体,花括号原样保留),否则 None。"""
    s = (line or "").strip()
    m = _LINE_RE.match(s)
    if m:
        return _strip_md(m.group(1))
    m = _BARE_RE.match(s)
    if m:
        return _strip_md(m.group(1) + (m.group(2) or "") + (" " + m.group(3) if m.group(3) else ""))
    return None


def _strip_md(s: str) -> str:
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    return re.sub(r"`(.+?)`", r"\1", s).strip()


def normalize_kind(v: Any) -> str | None:
    if not isinstance(v, str):
        return None
    key = re.sub(r"\s+", " ", v.strip().lower())
    return _KIND_ALIASES.get(key) or _KIND_ALIASES.get(re.sub(r"[\s((].*$", "", key))


def norm_no(no: Any) -> str:
    """场次号比对口径:大写、去掉 `(续)` / `(cont'd)` 之类后缀。"""
    s = str(no or "").strip().upper()
    m = re.match(r"S\d{1,3}[A-Z]?|\d{1,2}-\d{1,2}", s)
    return m.group(0) if m else s


def kind_label(kind: str | None, zh: bool = True) -> str:
    k = KINDS.get(kind or "")
    return (k["zh"] if zh else k["en"]) if k else (kind or "")


def _brace_fields(body: str) -> dict[str, str]:
    """花括号内按键位切:`衔接: 形状, 出: 甲,乙, 入: 丙` → {link, out, in}(值里的逗号保留)。"""
    ms = list(_KEY_RE.finditer(body))
    out: dict[str, str] = {}
    for i, m in enumerate(ms):
        key = _KEY_ALIASES[m.group(1).lower()]
        val = body[m.end(): ms[i + 1].start() if i + 1 < len(ms) else len(body)].strip(" \t,\uff0c;\uff1b\u3001")
        out.setdefault(key, val)
    return out


def parse_transition(text: str | None) -> dict:
    """转场文本 → {raw, cut, target, brace, link, designed_word}。
    cut = 去掉花括号后的转场词;target = 转场词里点名的下一场(`CUT TO S04`),没写为 None;
    link = None(没写花括号 / 花括号里一个键都认不出)或 {kind, kind_raw, out, in}(kind 认不出时为 None,留给机检报)。"""
    raw = (text or "").strip()
    m = _BRACE_RE.search(raw)
    cut = (raw[:m.start()] + raw[m.end():] if m else raw).strip().rstrip("。.").strip()
    link = None
    if m:
        f = _brace_fields(m.group(1))
        if f:
            link = {"kind": normalize_kind(f.get("link")), "kind_raw": (f.get("link") or "").strip(),
                    "out": (f.get("out") or "").strip(), "in": (f.get("in") or "").strip()}
    t = _TARGET_RE.search(cut)
    return {"raw": raw, "cut": cut, "target": t.group(1).upper() if t else None, "brace": bool(m), "link": link,
            "designed_word": bool(_DESIGNED_WORD_RE.search(cut))}


# ---------------------------------------------------------------- 逐场读取

def _block_text(b: dict) -> str:
    if b.get("type") == "dialogue":
        return " ".join(str(x.get("text") or "") for x in b.get("lines") or [])
    return str(b.get("text") or "")


def _raw_headers(screenplay_text: str, sb) -> list[str]:
    """场头原文行,口径与 script_breakdown.parse_screenplay 认场次的规则一致(二级及以下标题且 _parse_heading 认得)。"""
    out = []
    for raw in (screenplay_text or "").splitlines():
        hm = re.match(r"^(#{1,6})\s*(.*)$", raw.strip())
        if hm and len(hm.group(1)) >= 2 and sb._parse_heading(hm.group(2).strip()):
            out.append(raw.strip())
    return out


def scene_rows(screenplay_text: str) -> list[dict]:
    """逐场一行:{no, next_no, header(场头原文行), scene_id, scene_name, int_ext, time_of_day, transition(场内最后一条转场行文本),
    at_tail(这条转场行是不是本场最后一块), mid_links[](不在场尾却带花括号的转场行), tail_text, head_text, has_spoken,
    …parse_transition 各键}。按剧本原顺序。"""
    from modules import script_breakdown as sb     # 惰性导入:script_breakdown 也用本模块的 transition_line

    scenes = (sb.parse_screenplay(screenplay_text or "").get("scenes")) or []
    headers = _raw_headers(screenplay_text, sb)
    if len(headers) != len(scenes):
        headers = [None] * len(scenes)
    rows = []
    for i, s in enumerate(scenes):
        blocks = s.get("blocks") or []
        content = [b for b in blocks if b.get("type") != "transition"]
        trs = [(k, b) for k, b in enumerate(blocks) if b.get("type") == "transition"]
        last_k, last_b = trs[-1] if trs else (None, None)
        at_tail = last_k is not None and last_k == len(blocks) - 1
        mid = [b.get("text") for k, b in trs
               if not (at_tail and k == last_k) and (parse_transition(b.get("text")).get("link") is not None)]
        rows.append({
            "no": s.get("no"), "next_no": scenes[i + 1].get("no") if i + 1 < len(scenes) else None, "header": headers[i],
            "scene_id": s.get("scene_id"), "scene_name": s.get("scene_name"), "int_ext": s.get("int_ext"),
            "time_of_day": s.get("time_of_day"),
            "transition": last_b.get("text") if last_b else None, "at_tail": at_tail, "mid_links": mid,
            "tail_text": " ".join(_block_text(b) for b in content[-2:]),
            "head_text": " ".join(_block_text(b) for b in content[:2]),
            "has_spoken": bool(s.get("dialogue") or s.get("narration_candidates")),
            **{k: v for k, v in parse_transition(last_b.get("text") if last_b else None).items() if k != "raw"},
        })
    return rows


def links(screenplay_text: str, rows: list[dict] | None = None) -> list[dict]:
    """本集合法的场间衔接(kind 在白名单、出入俱全、写在场尾、有下一场),按场序:
    [{from, to, kind, tier, out, in, cut, label_zh, label_en, land}]。供导演处置对账 / 过场设计诊断 / 预览页。"""
    out = []
    for r in (rows if rows is not None else scene_rows(screenplay_text)):
        lk = r.get("link")
        if not lk or not r["at_tail"] or not r["next_no"] or lk["kind"] not in KINDS or not lk["out"] or not lk["in"]:
            continue
        if r.get("target") and r["target"] != norm_no(r["next_no"]):
            continue
        out.append(_public(r))
    return out


def _public(r: dict) -> dict:
    lk = r["link"]
    k = KINDS[lk["kind"]]
    return {"from": r["no"], "to": r["next_no"], "kind": lk["kind"], "tier": k["tier"], "out": lk["out"], "in": lk["in"],
            "cut": r.get("cut") or "", "label_zh": k["zh"], "label_en": k["en"], "land": k["land"]}


def links_by_scene(screenplay_text: str) -> dict[str, dict]:
    """{前一场场次号: 衔接}——挂到拆解表 scenes[].link_out、过场设计诊断 screenplay_link。"""
    return {str(x["from"]): x for x in links(screenplay_text)}


def annotate(breakdown: dict | None, screenplay_text: str) -> dict | None:
    """给拆解表逐场挂 link_out(宿主按剧本现算,不靠拆解工位写;剧本没写衔接的场不带此键)。原地改并返回。"""
    if not isinstance(breakdown, dict) or not isinstance(breakdown.get("scenes"), list):
        return breakdown
    by = links_by_scene(screenplay_text) if screenplay_text else {}
    for sc in breakdown["scenes"]:
        if not isinstance(sc, dict):
            continue
        sc.pop("link_out", None)
        hit = by.get(str(sc.get("no")))
        if hit:
            sc["link_out"] = hit
    return breakdown


# ---------------------------------------------------------------- 机检 scene_link_valid(剧本侧)

_EN_STOP = {"with", "that", "this", "from", "into", "onto", "over", "under", "then", "they", "them", "their", "there", "what",
            "when", "where", "which", "while", "same", "have", "been", "were", "will", "just", "still", "next", "scene", "shot"}


def _grams(s: str) -> set[str]:
    """粗粒度内容指纹:英文取 ≥4 字母的非停用词,中日韩文取相邻两字。只用来判「落点有没有写进正文」。"""
    s = (s or "").lower()
    words = {w for w in re.findall(r"[a-z0-9']{4,}", s) if w not in _EN_STOP}
    for seg in re.findall(r"[぀-ヿ㐀-鿿가-힯]+", s):
        words |= {seg[i:i + 2] for i in range(len(seg) - 1)}
    return words


def _generated_at(text: str) -> str | None:
    m = re.match(r"^---\s*\n(.*?)\n---", text or "", re.S)
    if not m:
        return None
    d = re.search(r"^generated_at\s*:\s*['\"]?(\d{4}-\d{2}-\d{2})", m.group(1), re.M)
    return d.group(1) if d else None


def verify(screenplay_text: str) -> dict:
    """scene_link_valid。→ {ok, errors, warns, links, legacy, n_boundaries}
    FAIL(存量剧本降为 WARN):衔接类型不在白名单 / 缺出或入 / 写在末场 / 点名的下一场不是相邻场 / 花括号不在场尾转场行 /
    台词接力但本场没有台词 / 转场词点名 MATCH CUT·SMASH CUT 却没写花括号。
    WARN:出 / 入 的内容在场尾 / 下一场场头正文里找不到 / 接力隔了黑场 / 试验型过多 / 衔接过密。"""
    rows = scene_rows(screenplay_text)
    gen = _generated_at(screenplay_text)
    legacy = not gen or gen < LINK_RULE_SINCE
    hard: list[str] = []
    warns: list[str] = []
    for i, r in enumerate(rows):
        no, nxt, lk = r["no"], r["next_no"], r.get("link")
        for t in r["mid_links"]:
            hard.append(f"{no}: 带 {{衔接…}} 的转场行不在场尾(其后还有正文):「{t}」——衔接只写在本场最后一行转场行")
        if r.get("brace") and lk is None and r["at_tail"]:
            warns.append(f"{no}: 转场行花括号里没有认出 衔接 / 出 / 入(link / out / in),按普通转场处理:「{r['transition']}」")
        if lk is None:
            if r.get("designed_word") and nxt:
                hard.append(f"{no}: 转场写了「{r['cut']}」却没写衔接内容——匹配 / 反差剪辑须补 "
                            f"`{{衔接: <类型>, 出: <本场最后一个画面或声音>, 入: <下一场第一个画面或声音>}}`,写不出成对内容就改回 CUT TO")
            continue
        if not r["at_tail"]:
            continue                                        # 已在 mid_links 报过
        where = f"{no}→{nxt or '?'}"
        if lk["kind"] not in KINDS:
            hard.append(f"{where}: 衔接类型「{lk['kind_raw'] or '(空)'}」不在白名单;只能写 "
                        + " / ".join(f"{v['zh']}({k})" for k, v in KINDS.items()))
            continue
        k = KINDS[lk["kind"]]
        if not lk["out"] or not lk["in"]:
            hard.append(f"{where}: {k['zh']}缺「{'出' if not lk['out'] else '入'}」——出 = 本场最后一个画面或声音,入 = 下一场第一个画面或声音,两项都要写")
            continue
        if not nxt:
            hard.append(f"{no}: 本集最后一场没有下一场可接,不能写衔接(集尾怎么收归「集尾收束」设置与导演转场清单)")
            continue
        if r.get("target") and r["target"] != norm_no(nxt):
            hard.append(f"{where}: 转场词点名 {r['target']},但下一场是 {nxt}——衔接只接相邻的下一场")
            continue
        if lk["kind"] == "line" and not r["has_spoken"]:
            hard.append(f"{where}: 台词接力要求本场有台词(前场末句的问或提到的人,后场首画面即答),本场没有任何台词")
        if not (_grams(lk["out"]) & _grams(r["tail_text"])):
            warns.append(f"{where}: 「出:{lk['out']}」在本场结尾的正文里找不到对应——落点要真的写进最后一条动作行 / 台词 / 声音,不能只贴标签")
        if not (_grams(lk["in"]) & _grams(rows[i + 1]["head_text"])):
            warns.append(f"{where}: 「入:{lk['in']}」在 {nxt} 开头的正文里找不到对应——起点要真的写进第一条动作行 / 台词 / 声音")
        if _BLACK_WORD_RE.search(r.get("cut") or ""):
            warns.append(f"{where}: {k['zh']}配了「{r['cut']}」——中间隔了黑场 / 淡出,接力会断;要接力就写 {k['cut']}")
    ok_links = links(screenplay_text, rows)
    trial = [x for x in ok_links if x["tier"] == "trial"]
    if len(trial) > TRIAL_WARN_OVER:
        warns.append(f"试验型衔接(形状 / 动作匹配){len(trial)} 处 > {TRIAL_WARN_OVER}:"
                     + "、".join(f"{x['from']}→{x['to']}" for x in trial)
                     + "——这两种要两个画面逐帧重合,一期下游只当标注、不保证对得上,留最要紧的")
    n_bound = max(0, len(rows) - 1)
    if len(ok_links) >= 3 and len(ok_links) * 2 > n_bound:
        warns.append(f"衔接 {len(ok_links)} 处 / 场界 {n_bound} 处,过半场界都在做设计型衔接,容易显得刻意;默认是 CUT TO")
    errors: list[str] = []
    if legacy and hard:
        warns = [f"{e}(存量剧本 generated_at={gen or '缺失'} 早于 {LINK_RULE_SINCE},只 WARN)" for e in hard] + warns
    else:
        errors = hard
    return {"ok": not errors, "errors": errors, "warns": warns, "links": ok_links, "legacy": legacy, "n_boundaries": n_bound}


# ---------------------------------------------------------------- 机检 script_links_disposed(导演侧)

DISPOSITIONS = {"adopt": "采纳", "modify": "修改", "drop": "弃用"}
LAND_TYPES = ("hard_cut", "match_cut", "smash_cut", "dissolve", "fade_black", "fade_white", "dip_black", "dip_white")
# 衔接成立的转场类型:两个画面 / 声音必须直接相接。落到黑场 / 白场 / 淡出淡入 = 中间隔了一段,衔接断了(要用那些就写弃用)
LINK_LAND_TYPES = ("hard_cut", "match_cut", "smash_cut", "dissolve")
_SCENE_NO = r"(S\d{1,3}[A-Za-z]?|\d{1,2}-\d{1,2})"
_DISP_LINE_RE = re.compile(r"(?:剧本衔接|script\s+link)\s*[:\uff1a]?\s*" + _SCENE_NO + r"\s*(?:→|⇒|->|—>|=>|至|到|\bto\b|[-–—])\s*"
                           + _SCENE_NO + r"(.*)$", re.I)
_DISP_WORD_RE = re.compile(r"(采纳|修改|弃用|\bADOPT(?:ED|S)?\b|\bMODIF(?:Y|IED|IES)\b|\bDROP(?:PED|S)?\b)", re.I)
_DISP_WORD = {"采纳": "adopt", "修改": "modify", "弃用": "drop"}


def parse_dispositions(plan_text: str) -> dict[tuple[str, str], dict]:
    """directing_plan.md 里的处置行 → {(前场, 后场): {disposition, reason, land, line}}。
    行锚点:`剧本衔接 S03→S04(…):采纳|修改|弃用 —— …`(英文 `Script link S03→S04 (…): ADOPT|MODIFY|DROP — …`)。
    同一场界多行取第一条带处置词的。"""
    out: dict[tuple[str, str], dict] = {}
    for raw in (plan_text or "").splitlines():
        m = _DISP_LINE_RE.search(raw)
        if not m:
            continue
        key = (norm_no(m.group(1)), norm_no(m.group(2)))
        w = _DISP_WORD_RE.search(m.group(3))
        if not w:
            out.setdefault(key, {"disposition": None, "reason": "", "land": None, "line": raw.strip()})
            continue
        word = w.group(1)
        disp = _DISP_WORD.get(word) or ("adopt" if word.upper().startswith("ADOPT") else
                                        "modify" if word.upper().startswith("MODIF") else "drop")
        reason = m.group(3)[w.end():].strip(" \t:\uff1a—–-·,\uff0c;\uff1b。.*`")
        land = next((t for t in LAND_TYPES if re.search(rf"\b{t}\b", reason)), None)
        if key not in out or out[key]["disposition"] is None:
            out[key] = {"disposition": disp, "reason": reason, "land": land, "line": raw.strip()}
    return out


def verify_dispositions(screenplay_text: str, plan_text: str) -> dict:
    """script_links_disposed:剧本每条合法衔接,导演阐述都要有一行处置。→ {ok, errors, warns, rows}
    FAIL:某条衔接没有处置行 / 处置行没写 采纳·修改·弃用 / 采纳·修改没写落到哪种转场类型 / 弃用·修改没写理由。
    WARN:处置了剧本里并不存在的衔接。剧本没有衔接 = 不适用,PASS。"""
    lks = links(screenplay_text)
    disp = parse_dispositions(plan_text)
    errors: list[str] = []
    warns: list[str] = []
    rows = []
    for x in lks:
        key = (norm_no(x["from"]), norm_no(x["to"]))
        where = f"{x['from']}→{x['to']}({x['label_zh']})"
        d = disp.get(key)
        rows.append({**x, "disposition": (d or {}).get("disposition"), "reason": (d or {}).get("reason"),
                     "landed": (d or {}).get("land")})
        if d is None:
            errors.append(f"{where}: 导演阐述没有处置这条剧本衔接——在「## 转场清单」加一行 "
                          f"`剧本衔接 {x['from']}→{x['to']}({x['label_zh']}):采纳 | 修改 | 弃用 —— …`")
        elif d["disposition"] is None:
            errors.append(f"{where}: 处置行没写 采纳 / 修改 / 弃用:「{d['line']}」")
        elif d["disposition"] in ("adopt", "modify") and not d["land"]:
            errors.append(f"{where}: {DISPOSITIONS[d['disposition']]}须写明落到哪种转场类型({' / '.join(LAND_TYPES)};"
                          f"本类衔接默认 {x['land']}),storyboard 才能落进 transition_in")
        elif d["disposition"] in ("adopt", "modify") and d["land"] not in LINK_LAND_TYPES:
            errors.append(f"{where}: {DISPOSITIONS[d['disposition']]}却落到 {d['land']}——中间隔了黑场 / 白场 / 淡出,两头接不上;"
                          f"衔接只能落 {' / '.join(LINK_LAND_TYPES)},确实要用 {d['land']} 就改写弃用")
        elif d["disposition"] in ("modify", "drop") and len(re.sub(r"\W", "", d["reason"])) < 4:
            errors.append(f"{where}: {DISPOSITIONS[d['disposition']]}须写理由(一句具体的话)")
    known = {(norm_no(x["from"]), norm_no(x["to"])) for x in lks}
    for key, d in disp.items():
        if key not in known:
            warns.append(f"{key[0]}→{key[1]}: 导演阐述处置了一条剧本里没有的衔接(剧本改过?):「{d['line']}」")
    return {"ok": not errors, "errors": errors, "warns": warns, "rows": rows}


# ---------------------------------------------------------------- 二期:登记卡 transition_in.link 与对账 script_links_landed

LINK_NOTE_MAX = 240
_LAND_PREFIX_RE = re.compile(r"^\s*(?:" + "|".join(LAND_TYPES) + r")\b\s*(?:[((][^))]{0,12}[))])?\s*[;;,,:\uff1a。.—–-]*\s*")


def disposition_note(reason: str | None) -> str:
    """导演处置行里「两侧镜头要对上的东西」:去掉行首的转场类型词(`match_cut(试验);`)后的正文,过长截断。只进登记卡给人和工位看,不进提示词。"""
    s = _LAND_PREFIX_RE.sub("", str(reason or "")).strip()
    return s if len(s) <= LINK_NOTE_MAX else s[: LINK_NOTE_MAX - 1] + "…"


def link_record(link: dict, *, out_shot: str | None, in_shot: str | None, note: str = "", device: str | None = None) -> dict:
    """shot_list `transition_in.link` 登记卡(写在后一场第一组的入口):衔接类型、两头内容、落在哪两镜、导演写的对位要求。
    device = 宿主随衔接带上的过场手段键名(motion_pair / sound_bridge),重出设计时据此撤换;没有就不带此键。"""
    rec = {"kind": link["kind"], "out": link["out"], "in": link["in"], "out_shot": out_shot, "in_shot": in_shot,
           "from_scene": link.get("from"), "to_scene": link.get("to"), "source": "screenplay"}
    if note:
        rec["note"] = note
    if KINDS[link["kind"]]["tier"] == "trial":
        rec["trial"] = True
    if device:
        rec["device"] = device
    return rec


def link_of(transition_in: Any) -> dict | None:
    """transition_in 里的登记卡(规范化:kind 在白名单且出入俱全才算);没有 / 不合法 = None。"""
    lk = transition_in.get("link") if isinstance(transition_in, dict) else None
    if not isinstance(lk, dict) or lk.get("kind") not in KINDS:
        return None
    out, inn = str(lk.get("out") or "").strip(), str(lk.get("in") or "").strip()
    return {**lk, "out": out, "in": inn} if out and inn else None


def _group_scene_no(g: dict, shots: dict) -> str:
    no = g.get("scene_no")
    if not no:
        first = shots.get((g.get("shots") or [None])[0]) or {}
        no = first.get("scene_no")
    return norm_no(no)


def boundary_of(shot_list: dict, from_no: Any, to_no: Any) -> tuple[dict, dict] | None:
    """衔接 from→to 落在哪条组边界:前一场最后一组 → 后一场第一组(相邻两组的场次号恰为 from / to)。找不到 = None。"""
    groups = [g for g in (shot_list.get("generation_groups") or []) if isinstance(g, dict) and g.get("group_id")]
    shots = {s.get("shot_id"): s for s in (shot_list.get("shots") or []) if isinstance(s, dict)}
    a_no, b_no = norm_no(from_no), norm_no(to_no)
    for a, b in zip(groups, groups[1:]):
        if _group_scene_no(a, shots) == a_no and _group_scene_no(b, shots) == b_no:
            return a, b
    return None


def verify_landed(screenplay_text: str, plan_text: str, shot_list: dict | None, design: dict | None = None) -> dict:
    """script_links_landed:导演采纳 / 修改的衔接是否真的落进了 shot_list。→ {ok, errors, warns, rows}
    FAIL:分镜表里找不到这条场界;后一场第一组没有显式 transition_in;类型与导演写的不一致;登记卡 link 与剧本现值不一致(过期);
          导演弃用 / 剧本已删的衔接,分镜表里还留着登记卡。
    WARN:登记卡还没写(宿主 `transition_design.py propose` 或 H3A 签字时自动写)。
    用户在过场卡上改过的边界(设计表 source=user)以用户为准,不报。剧本无衔接 / 没有 shot_list = 不适用,PASS。"""
    lks = links(screenplay_text)
    disp = parse_dispositions(plan_text)
    errors: list[str] = []
    warns: list[str] = []
    rows = []
    if not isinstance(shot_list, dict) or not shot_list.get("generation_groups"):
        return {"ok": True, "errors": errors, "warns": warns, "rows": rows, "skipped": "尚无 shot_list"}
    user_by_to = {b.get("to_group"): b for b in ((design or {}).get("boundaries") or [])
                  if isinstance(b, dict) and b.get("source") == "user" and b.get("status") in ("accepted", "rejected")}
    live: set[str] = set()
    for x in lks:
        d = disp.get((norm_no(x["from"]), norm_no(x["to"]))) or {}
        where = f"{x['from']}→{x['to']}({x['label_zh']})"
        pair = boundary_of(shot_list, x["from"], x["to"])
        row = {"from": x["from"], "to": x["to"], "kind": x["kind"], "disposition": d.get("disposition"),
               "to_group": pair[1]["group_id"] if pair else None, "state": ""}
        rows.append(row)
        if d.get("disposition") not in ("adopt", "modify"):
            row["state"] = "dropped" if d.get("disposition") == "drop" else "undisposed"
            continue
        if not pair:
            row["state"] = "no_boundary"
            errors.append(f"{where}: 分镜表里找不到 {x['from']} 最后一组 → {x['to']} 第一组这条边界(场次号对不上或该场没有分组)")
            continue
        a, b = pair
        live.add(b["group_id"])
        if b["group_id"] in user_by_to:
            row["state"] = "user_decided"
            continue
        t = b.get("transition_in") if isinstance(b.get("transition_in"), dict) else {}
        ty = str(t.get("type") or "hard_cut")
        if not t or (ty == "hard_cut" and not str(t.get("reason") or "").strip()):
            row["state"] = "missing"
            errors.append(f"{where}: 导演{DISPOSITIONS[d['disposition']]}了,但 {b['group_id']} 没有显式 transition_in——"
                          f"按转场清单该行落 type={d.get('land')} + intent + reason + source(运动 / 声音 / 台词接力也要显式写 hard_cut)")
            continue
        if d.get("land") and ty != d["land"]:
            row["state"] = "type_mismatch"
            errors.append(f"{where}: 导演写的是 {d['land']},{b['group_id']} 的 transition_in.type 却是 {ty}")
            continue
        lk = link_of(t)
        if lk is None:
            row["state"] = "base_only"
            warns.append(f"{where}: {b['group_id']} 已落 {ty},登记卡 transition_in.link 还没写"
                         f"(宿主 `python3 code/transition_design.py propose` 或 H3A 签字时自动写;写提示词前必须有)")
        elif (lk["kind"], lk["out"], lk["in"]) != (x["kind"], x["out"], x["in"]):
            row["state"] = "stale"
            errors.append(f"{where}: {b['group_id']} 的登记卡与剧本现值不一致(剧本衔接改过)——重跑 `python3 code/transition_design.py propose`")
        else:
            row["state"] = "landed"
    for g in shot_list.get("generation_groups") or []:
        if isinstance(g, dict) and g.get("group_id") not in live and link_of(g.get("transition_in")) and g.get("group_id") not in user_by_to:
            errors.append(f"{g.get('group_id')}: transition_in 留着登记卡 link,但剧本 / 导演处置里已没有这条采纳的衔接"
                          f"——重跑 `python3 code/transition_design.py propose` 撤掉")
    return {"ok": not errors, "errors": errors, "warns": warns, "rows": rows}
