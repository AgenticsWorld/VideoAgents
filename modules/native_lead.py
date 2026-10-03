"""原生先入(native lead,声画分离三期,2026-10-03;docs/sound_split.md「三期」,WORKFLOW.md §8D)。

组内 J-cut 交给多镜头模型原生做:画内句 `dialogue_lines[].native_lead {shot, s, reason, source}`——这句仍是画内句(照写 `{}`、
照挂 audio_ref、照数 speakers_le_3),只是让说话人在**前一镜**(同组、说话人不在画内的插入镜 / 主观镜 / 听者镜 / 定场镜)就在画外
开口,切过来后在自己的镜里接着说完;声音与画面同一次生成,嗓音、气口、环境声天然连续。
fengshen3 ep07 grp008 实测(Seedance 2.5,2026-10-03):J 型 1/1 过(先入 1.075 s、不重说、不画出说话人);L 型(尾句拖到下一镜)0/2——
模型倾向把台词**前置**并在说话人镜内收尾,故三期只开放 J,L 继续走一期(整句转画外后期合成)/ 二期(底床延续)。

硬前提(native_lead_valid,全部满足):
  P1 项目 output.sound_split = auto;P2 对白配音 = 视频原声(dialogue_voice=native);P3 本组生效视频模型是 Seedance 2.5(shot_timing.group_kind);
  P4 前一镜与本句所在镜同一生成组(本镜不是组首镜);P5 前一镜说话人不在画内(characters 不含他)且前一镜无画内台词、无画外句、无旁白挂点;
  P6 本句是本镜第一句、说话人是本镜画内说话人;P7 前一镜 ≥ MIN_PREV_SHOT_S,s = min(前镜时长, MAX_LEAD_S, 估时×LEAD_FRACTION);
  P8 先入文本不含 blocking performance 的触发词(performance_bound 要在本镜 `{}` 里找它)。
触发(sound_split=auto 时 shot-planning 按白名单自动标,须写 reason):N1 插入镜 / 主观镜(前镜无人物)、N2 听者先行(前镜只有听者)、
N3 定场起声(前镜是组首定场 / 远景)、U 用户手标。
prompt 落地由宿主 `sync_native_leads.py --write`(本模块 sync_episode)写固定标记句,prompt 工位不手写:
  前一镜段末:【原生先入】本镜起即由<说话人>在画外开口:{<先入文本>}——说话人不在画内,画面不出现任何多出的人物,话未说完即切到下一镜。
  本镜段:原 {<全句>} 改为 {<余句>},段末:【原生先入】本镜开场时<说话人>正说到一半,上一镜画外已说出的部分不再重说,接着说完。
机检 native_lead_bound(prompt 两句都在、余句在、全句 / 先入文本不再出现在本镜);出片后 audible:人声起点落在前一镜窗口(先入 ≥ AUDIBLE_MIN_S)
= PASS,否则 WARN「先入未生效」(画面仍成立,不阻断)。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from modules.shot_timing import KIND_H3, KIND_SD25, group_kind, shot_paragraphs, ui_lang_is_zh
from modules.whitebox import component, read

MAX_LEAD_S = 1.0
MIN_PREV_SHOT_S = 0.6
LEAD_FRACTION = 0.4
AUDIBLE_MIN_S = 0.3
WIDE_SIZES = ("大远景", "远景", "全景", "大全景", "EWS", "WS", "LS", "ELS", "extreme wide", "wide", "establishing")
TRIGGERS = {"N1": "插入镜 / 主观镜起声(前镜无人物)", "N2": "听者先行(前镜只有听者)", "N3": "定场起声(前镜是组首定场 / 远景)", "U": "用户手标"}
MARK_ZH, MARK_EN = "【原生先入】", "Native lead:"
MARK_RE = re.compile(r"[ \t]*(?:【原生先入】|Native lead:)[^\n]*?[。.](?=[ \t]*(?:\n|$))")
_SPLIT_RE = re.compile(r"[,，、;；]")
_ID_RE = re.compile(r"((?:CHAR|CRE)-\d+)")


# ---------------------------------------------------------------- 读取

def _f(v, default=None):
    try:
        return float(v) if v is not None and v != "" and not isinstance(v, bool) else default
    except (TypeError, ValueError):
        return default


def _settings(base: Path) -> dict:
    return read(Path(base) / "settings.json", {}) or {}


def mode_ok(base: Path) -> tuple[bool, str]:
    """P1 + P2:声画分离 = auto 且视频原声。"""
    out = (_settings(base).get("output") or {})
    ss = out.get("sound_split") if out.get("sound_split") in ("off", "script_only", "auto") else "auto"
    if ss != "auto":
        return False, f"声画分离={ss}(原生先入只在 auto 下)"
    if (out.get("dialogue_voice") or "native") != "native":
        return False, "对白配音=后期配音(原生声会被 TTS 替换,原生先入无意义)"
    return True, ""


def native_lead(ln) -> dict | None:
    """对白行的原生先入(规范化):{shot, s, reason, source};缺 / 非法 = None。"""
    nl = ln.get("native_lead") if isinstance(ln, dict) else None
    if not isinstance(nl, dict) or not nl.get("shot"):
        return None
    s = _f(nl.get("s"), 0.0) or 0.0
    reason = nl.get("reason") if isinstance(nl.get("reason"), dict) else {"trigger": str(nl.get("trigger") or ""), "evidence": str(nl.get("evidence") or "")}
    return {"shot": str(nl["shot"]), "s": round(s, 3), "reason": {"trigger": str(reason.get("trigger") or ""), "evidence": str(reason.get("evidence") or "")},
            "source": str(nl.get("source") or "")}


def line_text(ln) -> str:
    return str(ln.get("text") or ln.get("line") or "").strip() if isinstance(ln, dict) else ""


def _placement(ln) -> str:
    v = str(ln.get("placement") or "").strip().lower() if isinstance(ln, dict) else ""
    return v if v in ("on", "os", "vo") else "on"


def _speaker(ln) -> str:
    for k in ("speaker", "char", "character_id", "speaker_char"):
        m = _ID_RE.search(str((ln or {}).get(k) or ""))
        if m:
            return m.group(1)
    return str((ln or {}).get("speaker") or "").strip()


def dialogue_lines(shot: dict) -> list[dict]:
    return [ln for ln in (shot.get("dialogue_lines") or []) if isinstance(ln, dict) and line_text(ln)]


def _is_wide(shot: dict) -> bool:
    s = str(shot.get("size") or "").lower()
    return any(w.lower() in s for w in WIDE_SIZES)


def lead_split(text: str, s: float, est: float | None) -> tuple[str, str]:
    """先入文本 / 余句:优先在第一个句读(,，、;;)处切且首分句 ≤60% 字数;否则按 s/est 比例切字(≥2 字,留 ≥2 字)。"""
    text = text.strip()
    m = _SPLIT_RE.search(text)
    if m and 0 < m.end() <= len(text) * 0.6 and len(text) - m.end() >= 2:
        return text[:m.end()], text[m.end():].strip()
    frac = (s / est) if est and est > 0 else LEAD_FRACTION
    n = max(2, min(len(text) - 2, int(round(len(text) * min(0.6, max(0.15, frac))))))
    return text[:n], text[n:].strip()


def lead_seconds(prev_dur: float, est: float | None) -> float:
    return round(max(0.0, min(prev_dur, MAX_LEAD_S, (est or 0.0) * LEAD_FRACTION if est else MAX_LEAD_S)), 3)


def _timeline(sl: dict):
    shots = {s["shot_id"]: s for s in sl.get("shots") or [] if isinstance(s, dict) and s.get("shot_id")}
    info: dict[str, dict] = {}
    for g in sl.get("generation_groups") or []:
        if not isinstance(g, dict) or not g.get("group_id"):
            continue
        ids = [x for x in g.get("shots") or [] if isinstance(x, str)]
        for k, sid in enumerate(ids):
            info[sid] = {"group_id": g["group_id"], "order": k, "prev": ids[k - 1] if k > 0 else None, "first": k == 0}
    return shots, info


def _anchored_shots(sl: dict) -> set[str]:
    out = set()
    for a in sl.get("narration_anchors") or []:
        if isinstance(a, dict):
            out.update(x for x in a.get("anchor_shots") or [] if isinstance(x, str))
    return out


def _perf_words(base: Path, ep: str, sid: str, cid: str) -> list[str]:
    b = read(Path(base) / "directing" / ep / "shots" / sid / "blocking.json", {}) or {}
    out = []
    for c in b.get("characters") or []:
        if isinstance(c, dict) and (c.get("id") or c.get("character_id")) == cid:
            w = ((c.get("performance") or {}).get("trigger") or {}).get("word")
            if w:
                out.append(str(w))
    return out


# ---------------------------------------------------------------- 条件判定 / 自动建议

def _prereq(base: Path, ep: str, sl: dict, shots: dict, info: dict, sid: str, idx: int, ln: dict, kind_cache: dict) -> tuple[list[str], dict]:
    """P3–P8 逐条判;返回 (问题列表, 事实 {prev, prev_dur, s, lead, rest, trigger_candidates})。"""
    errs, facts = [], {}
    meta = info.get(sid) or {}
    gid = meta.get("group_id")
    prev = meta.get("prev")
    spk = _speaker(ln)
    if _placement(ln) != "on":
        errs.append("原生先入只配画内句(placement=on)")
    if idx != 0:
        errs.append("只配本镜第一句(P6)")
    if not prev:
        errs.append("本镜是组首镜,没有同组前一镜(P4;组边界先入走二期声桥)")
        return errs, facts
    ps = shots.get(prev) or {}
    if spk and spk in (ps.get("characters") or []):
        errs.append(f"前一镜 {prev} 画内有说话人 {spk}(P5)")
    if dialogue_lines(ps):
        errs.append(f"前一镜 {prev} 自己有台词(P5)")
    if prev in _anchored_shots(sl):
        errs.append(f"前一镜 {prev} 挂了旁白(P5)")
    if spk and spk not in (shots.get(sid) or {}).get("characters", []):
        errs.append(f"说话人 {spk} 不在本镜 {sid} 画内(P6)")
    pd = _f(ps.get("duration_s"), 0.0) or 0.0
    if pd < MIN_PREV_SHOT_S:
        errs.append(f"前一镜 {prev} 只有 {pd:g}s(< {MIN_PREV_SHOT_S}s,P7)")
    if gid:
        k = kind_cache.get(gid)
        if k is None:
            try:
                k = group_kind(base, ep, gid)
            except Exception:
                k = ""
            kind_cache[gid] = k
        if k != KIND_SD25:
            errs.append(f"本组生效视频模型不是 Seedance 2.5({k or '未知'},P3)")
    est = _f(ln.get("est_duration_s"))
    s = lead_seconds(pd, est)
    lead, rest = lead_split(line_text(ln), s, est)
    for w in _perf_words(base, ep, sid, spk) if spk else []:
        if w and w in lead:
            errs.append(f"先入文本「{lead}」含表演触发词『{w}』(P8:触发词须留在本镜 `{{}}` 里)")
    cands = []
    if not (ps.get("characters") or []):
        cands.append("N1")
    elif dialogue_lines(ps) == [] and spk not in (ps.get("characters") or []):
        cands.append("N2")
    if (info.get(prev) or {}).get("first") and _is_wide(ps):
        cands.append("N3")
    facts.update(prev=prev, prev_dur=pd, s=s, lead=lead, rest=rest, triggers=cands, group_id=gid, speaker=spk, est=est)
    return errs, facts


def collect(base: Path, ep: str, shot_list: dict | None = None) -> list[dict]:
    """已标 native_lead 的句子(镜序):{shot_id, idx, group_id, speaker, text, lead, rest, s, prev, reason, source, issues[]}。"""
    base = Path(base)
    sl = shot_list if shot_list is not None else (read(base / "directing" / ep / "shot_list.json", {}) or {})
    shots, info = _timeline(sl)
    cache: dict = {}
    out = []
    for s in sl.get("shots") or []:
        if not isinstance(s, dict) or not s.get("shot_id"):
            continue
        for idx, ln in enumerate(dialogue_lines(s)):
            nl = native_lead(ln)
            if not nl:
                continue
            errs, facts = _prereq(base, ep, sl, shots, info, s["shot_id"], idx, ln, cache)
            if facts.get("prev") and nl["shot"] != facts["prev"]:
                errs.append(f"native_lead.shot={nl['shot']} 不是本镜的同组前一镜 {facts['prev']}")
            s_eff = nl["s"] if nl["s"] > 0 else facts.get("s", 0.0)
            if facts.get("prev_dur") is not None and s_eff > min(facts["prev_dur"], MAX_LEAD_S) + 1e-6:
                errs.append(f"s={s_eff:g} 超过前镜时长 / 上限 {min(facts['prev_dur'], MAX_LEAD_S):g}s(P7)")
            trig = nl["reason"]["trigger"]
            if trig not in TRIGGERS:
                errs.append(f"reason.trigger={trig or '缺'} 不在 {list(TRIGGERS)}")
            elif trig != "U" and facts.get("triggers") is not None and trig not in facts["triggers"]:
                errs.append(f"reason.trigger={trig} 与前镜事实不符(可用:{facts['triggers'] or '无'})")
            lead, rest = lead_split(line_text(ln), s_eff, facts.get("est"))
            out.append({"shot_id": s["shot_id"], "idx": idx, "group_id": facts.get("group_id") or (info.get(s["shot_id"]) or {}).get("group_id"),
                        "speaker": facts.get("speaker") or _speaker(ln), "text": line_text(ln), "lead": lead, "rest": rest,
                        "s": round(s_eff, 3), "prev": nl["shot"], "reason": nl["reason"], "source": nl["source"], "issues": errs})
    return out


def validate(base: Path, ep: str, shot_list: dict | None = None) -> list[str]:
    """native_lead_valid:P1–P8 + 字段合法;消息 "<gid>/<sid>/lNN native_lead_valid: …"。"""
    base = Path(base)
    recs = collect(base, ep, shot_list)
    if not recs:
        return []
    ok, why = mode_ok(base)
    out = []
    for r in recs:
        tag = f"{r['group_id'] or '?'}/{r['shot_id']}/l{r['idx']:02d}"
        if not ok:
            out.append(f"{tag} native_lead_valid: {why}")
        for i in r["issues"]:
            out.append(f"{tag} native_lead_valid: {i}")
    return out


def suggest(base: Path, ep: str, shot_list: dict | None = None) -> list[dict]:
    """自动建议(sound_split=auto):满足 P3–P8 且命中 N1–N3 的画内首句 → [{shot_id, idx, speaker, prev, s, lead, rest, trigger, evidence, already}]。"""
    base = Path(base)
    sl = shot_list if shot_list is not None else (read(base / "directing" / ep / "shot_list.json", {}) or {})
    ok, _ = mode_ok(base)
    if not ok:
        return []
    shots, info = _timeline(sl)
    cache: dict = {}
    out = []
    for s in sl.get("shots") or []:
        if not isinstance(s, dict) or not s.get("shot_id"):
            continue
        lines = dialogue_lines(s)
        if not lines or _placement(lines[0]) != "on":
            continue
        ln = lines[0]
        errs, facts = _prereq(base, ep, sl, shots, info, s["shot_id"], 0, ln, cache)
        if errs or not facts.get("triggers"):
            continue
        if not _SPLIT_RE.search(line_text(ln)):
            continue       # 自动建议只给有句读可切的句子(按字数硬切会切在词中间);用户手标(U)不受此限
        trig = facts["triggers"][0]
        prev = facts["prev"]
        ps = shots.get(prev) or {}
        ev = {"N1": f"前镜 {prev} 无人物({ps.get('size') or ''}),说话人 {facts['speaker']} 在本镜开口",
              "N2": f"前镜 {prev} 只有听者 {ps.get('characters')},说话人 {facts['speaker']} 不在其中",
              "N3": f"前镜 {prev} 是组首{ps.get('size') or '远景'},说话人不在其中"}[trig]
        out.append({"shot_id": s["shot_id"], "idx": 0, "group_id": facts["group_id"], "speaker": facts["speaker"], "prev": prev,
                    "s": facts["s"], "lead": facts["lead"], "rest": facts["rest"], "trigger": trig, "evidence": ev,
                    "already": native_lead(ln) is not None})
    return out


# ---------------------------------------------------------------- 写回

def _write_sl(path: Path, sl: dict) -> None:
    path.write_text(json.dumps(sl, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def set_line(base: Path, ep: str, shot_id: str, idx: int, *, on: bool = True, s: float | None = None, reason: dict | str | None = None,
             source: str = "user") -> dict:
    """给一句画内句加 / 去 native_lead(前一镜由时间线推导,s 缺省按 P7 算)。非法抛 ValueError。"""
    base = Path(base)
    path = base / "directing" / ep / "shot_list.json"
    sl = read(path, None)
    if not isinstance(sl, dict):
        raise ValueError(f"读不到 {path}")
    shots, info = _timeline(sl)
    shot = shots.get(shot_id)
    if not shot:
        raise ValueError(f"镜 {shot_id} 不存在")
    lines = dialogue_lines(shot)
    if not isinstance(idx, int) or idx < 0 or idx >= len(lines):
        raise ValueError(f"{shot_id} 没有第 {idx} 句台词")
    ln = lines[idx]
    if not on:
        ln.pop("native_lead", None)
        _write_sl(path, sl)
        return ln
    errs, facts = _prereq(base, ep, sl, shots, info, shot_id, idx, ln, {})
    hard = [e for e in errs if "P3" not in e]      # 模型族只告警(集级 / 组级可能稍后切换),其余前提不满足即拒
    if hard:
        raise ValueError(";".join(hard))
    s_eff = _f(s)
    if s_eff is None or s_eff <= 0:
        s_eff = facts["s"]
    if s_eff > min(facts["prev_dur"], MAX_LEAD_S) + 1e-6:
        raise ValueError(f"s 不得超过 {min(facts['prev_dur'], MAX_LEAD_S):g}s(前镜时长 / 上限)")
    if isinstance(reason, str):
        reason = {"trigger": "U" if source == "user" else (facts["triggers"][0] if facts["triggers"] else "U"), "evidence": reason}
    if not isinstance(reason, dict):
        reason = {"trigger": "U" if source == "user" else (facts["triggers"][0] if facts["triggers"] else "U"), "evidence": ""}
    if str(reason.get("trigger") or "") not in TRIGGERS:
        raise ValueError(f"reason.trigger 须为 {list(TRIGGERS)}")
    ln["native_lead"] = {"shot": facts["prev"], "s": round(float(s_eff), 3),
                         "reason": {"trigger": str(reason["trigger"]), "evidence": str(reason.get("evidence") or "")},
                         "source": source if source in ("directing", "user") else "user"}
    _write_sl(path, sl)
    return ln


# ---------------------------------------------------------------- prompt 同步(固定标记句)

def sentences(speaker_label: str, lead: str, zh: bool) -> tuple[str, str]:
    if zh:
        return (f"{MARK_ZH}本镜起即由{speaker_label}在画外开口:{{{lead}}}——说话人{speaker_label}不在画内,画面不出现任何多出的人物,话未说完即切到下一镜。",
                f"{MARK_ZH}本镜开场时{speaker_label}正说到一半,上一镜画外已说出的「{lead}」不再重说,接着说完。")
    return (f"{MARK_EN} from the first frame of this shot {speaker_label} speaks off-screen: {{{lead}}} — {speaker_label} is not in frame, no extra person appears, the cut comes mid-sentence.",
            f"{MARK_EN} {speaker_label} is already mid-sentence when this shot opens; the part already spoken off-screen in the previous shot (\"{lead}\") is not repeated, the line simply continues to its end.")


def strip_marks(vp: str) -> str:
    return re.sub(r"[ \t]{2,}", " ", MARK_RE.sub("", vp))


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _append(vp: str, head: dict, sentence: str) -> str:
    body_end = head["body_end"]
    body = vp[head["head_end"]:body_end]
    stripped = body.rstrip()
    trail = body[len(stripped):]
    sep = "" if (not stripped or stripped.endswith(("。", ";", "；", "!", "！", "?", "？"))) else " "
    return vp[:head["head_end"]] + stripped + sep + sentence + trail + vp[body_end:]


def _replace_brace(vp: str, head: dict, old: str, new: str) -> tuple[str, bool]:
    """在该 Shot 段体内把 {old}(忽略空白差异)换成 {new};没找到返回 (vp, False)。"""
    body = vp[head["head_end"]:head["body_end"]]
    for m in re.finditer(r"\{([^{}]*)\}", body):
        if _norm(m.group(1)) == _norm(old):
            rep = ("{" + new + "}") if new else ""
            nb = body[:m.start()] + rep + body[m.end():]
            _replace_brace.last_after = body[m.end():m.end() + 24]      # 摘掉 {先入} 时记住后文,还原用
            return vp[:head["head_end"]] + nb + vp[head["body_end"]:], True
    return vp, False


_replace_brace.last_after = ""


def _insert_before_brace(vp: str, head: dict, anchor: str, new: str, after: str = "") -> tuple[str, bool]:
    """在该 Shot 段体内插回 {new}(还原「已拆句」写法):优先插在记录的后文 after 之前(原位),找不到再插在 {anchor} 之前。"""
    body = vp[head["head_end"]:head["body_end"]]
    if after:
        k = body.find(after)
        if k >= 0:
            nb = body[:k] + "{" + new + "}" + body[k:]
            return vp[:head["head_end"]] + nb + vp[head["body_end"]:], True
    for m in re.finditer(r"\{([^{}]*)\}", body):
        if _norm(m.group(1)) == _norm(anchor):
            nb = body[:m.start()] + "{" + new + "}" + body[m.start():]
            return vp[:head["head_end"]] + nb + vp[head["body_end"]:], True
    return vp, False


def _label(base: Path, cid: str, group: dict) -> str:
    for row in ((group.get("blocking_map") or {}).get("characters") or []):
        if isinstance(row, dict) and row.get("id") == cid and row.get("label"):
            return str(row["label"])
    idx = read(Path(base) / "bible" / "characters" / "index.json", {}) or {}
    for c in idx.get("characters") or []:
        if isinstance(c, dict) and c.get("id") == cid:
            return str(c.get("canonical_name") or c.get("name") or cid)
    return cid


def _restore(vp: str, h3: bool, records: list[dict]) -> str:
    """按上次写入记录把本镜段还原(余句→全句,或插回被摘掉的 {先入}),使重写幂等。"""
    for rec in records:
        heads = {h["no"]: h for h in shot_paragraphs(vp, h3)}
        h = heads.get(int(rec.get("shot_no") or 0))
        if not h:
            continue
        if rec.get("mode") == "split":
            vp, _ = _insert_before_brace(vp, h, rec.get("rest") or "", rec.get("lead") or "", rec.get("after") or "")
        else:
            vp, _ = _replace_brace(vp, h, rec.get("rest") or "", rec.get("full") or "")
    return vp


def apply_prompt(vp: str, *, prev_no: int, shot_no: int, full: str, lead: str, rest: str, label: str, h3: bool, zh: bool,
                 prev_records: list[dict] | None = None) -> tuple[str, list[str], str]:
    """一句的 prompt 落地(幂等):先还原旧记录(余句→全句)并剔标记句,再把 {全句}→{余句}、前镜加先入句、本镜加接续句。"""
    vp = strip_marks(vp)
    heads = shot_paragraphs(vp, h3)
    by_no = {h["no"]: h for h in heads}
    errs = []
    vp = _restore(vp, h3, prev_records or [])
    heads = shot_paragraphs(vp, h3)
    by_no = {h["no"]: h for h in heads}
    hp, hs = by_no.get(prev_no), by_no.get(shot_no)
    if not hp or not hs:
        return vp, [f"prompt 缺 Shot {prev_no} / Shot {shot_no} 段"]
    vp, ok = _replace_brace(vp, hs, full, rest)
    mode = "whole"
    if not ok:
        # prompt 工位已把这句按句读拆成 {先入}{余句}(sd25 表演节拍写法):只摘掉 {先入}
        body = vp[hs["head_end"]:hs["body_end"]]
        braces = [_norm(m) for m in re.findall(r"\{([^{}]*)\}", body)]
        if _norm(lead) in braces and _norm(rest) in braces:
            vp, ok = _replace_brace(vp, hs, lead, "")
            mode = "split"
            apply_prompt.last_after = _replace_brace.last_after
    if not ok:
        return vp, [f"Shot {shot_no} 段里找不到 {{{full}}} 或 {{{lead}}}+{{{rest}}}(prompt 台词与 shot_list 不一致)"]
    heads = shot_paragraphs(vp, h3)
    by_no = {h["no"]: h for h in heads}
    s_prev, s_cont = sentences(label, lead, zh)
    vp = _append(vp, by_no[shot_no], s_cont)
    heads = shot_paragraphs(vp, h3)
    by_no = {h["no"]: h for h in heads}
    vp = _append(vp, by_no[prev_no], s_prev)
    return vp, errs, mode


def check_prompt(vp: str, *, prev_no: int, shot_no: int, full: str, lead: str, rest: str, h3: bool) -> list[str]:
    """native_lead_bound:前镜段含标记句与 {先入};本镜段含 {余句}、不含 {全句} 也不含先入文本的 {};本镜段有接续标记句。"""
    heads = {h["no"]: h for h in shot_paragraphs(vp, h3)}
    hp, hs = heads.get(prev_no), heads.get(shot_no)
    errs = []
    if not hp or not hs:
        return [f"prompt 缺 Shot {prev_no} / Shot {shot_no} 段"]
    pb, sb = vp[hp["head_end"]:hp["body_end"]], vp[hs["head_end"]:hs["body_end"]]
    if not MARK_RE.search(pb) or not any(_norm(m) == _norm(lead) for m in re.findall(r"\{([^{}]*)\}", pb)):
        errs.append(f"Shot {prev_no} 段缺【原生先入】句 / {{{lead}}}")
    braces_s = [_norm(m) for m in re.findall(r"\{([^{}]*)\}", sb)]
    if _norm(rest) not in braces_s:
        errs.append(f"Shot {shot_no} 段缺 {{{rest}}}")
    if _norm(full) in braces_s or any(b.startswith(_norm(lead)) and b != _norm(rest) for b in braces_s):
        errs.append(f"Shot {shot_no} 段仍含全句 / 先入文本的 {{}}(会重说)")
    if not MARK_RE.search(sb):
        errs.append(f"Shot {shot_no} 段缺接续标记句")
    return errs


def _write_prompt(base: Path, ep: str, pp: Path, prompt: dict, new_vp: str, records: list[dict]) -> None:
    from modules.prompt_layout import paragraphize
    updated = dict(prompt)
    updated["video_prompt"] = paragraphize(new_vp)
    updated["native_leads"] = records
    backup = base / "directing" / ep / "whitebox" / "prompt_backups" / pp.name
    backup.parent.mkdir(parents=True, exist_ok=True)
    if not backup.exists():
        backup.write_bytes(pp.read_bytes())
    tmp = pp.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, pp)


def sync_episode(base: Path, ep: str, groups=None, write: bool = False, zh=None) -> dict:
    """逐组把 native_lead 写进组 prompt(--write)并机检 native_lead_bound。无 native_lead 的组只剔除残留标记句 / 回填全句。
    返回 {leads, groups: [{group_id, updated, skipped, errors}], updated_prompts, errors, warnings}。"""
    ep = component(ep)
    base = Path(base)
    zh = ui_lang_is_zh() if zh is None else zh
    sl = read(base / "directing" / ep / "shot_list.json", {}) or {}
    recs = collect(base, ep, sl)
    by_group: dict[str, list[dict]] = {}
    for r in recs:
        by_group.setdefault(r["group_id"], []).append(r)
    gl = {g.get("group_id"): g for g in sl.get("generation_groups") or [] if isinstance(g, dict) and g.get("group_id")}
    want = set(groups or [])
    rows, errors, warnings, updated = [], [], [], []
    for gid, g in gl.items():
        if want and gid not in want:
            continue
        pp = base / "assets" / "prompts" / ep / f"{gid}.json"
        need = by_group.get(gid) or []
        row = {"group_id": gid, "updated": False, "skipped": "", "errors": []}
        prompt = read(pp, None)
        if not isinstance(prompt, dict) or not str(prompt.get("video_prompt") or "").strip():
            row["skipped"] = "组 prompt 尚未写" if need else ""
            if need:
                warnings.append(f"{gid}: native_lead_bound skipped(组 prompt 尚未写,prompt 工位写完必跑 sync_native_leads --write)")
            rows.append(row)
            continue
        vp = str(prompt["video_prompt"])
        h3 = group_kind(base, ep, gid) == KIND_H3
        ids = [x for x in g.get("shots") or [] if isinstance(x, str)]
        prev_records = [r for r in (prompt.get("native_leads") or []) if isinstance(r, dict)]
        new_vp, records, errs = vp, [], []
        if need:
            for r in need:
                if r["issues"]:
                    errs += [f"{gid}/{r['shot_id']}: native_lead_valid: {i}" for i in r["issues"]]
                    continue
                sno, pno = ids.index(r["shot_id"]) + 1, ids.index(r["prev"]) + 1
                label = _label(base, r["speaker"], g)
                new_vp, e, mode = apply_prompt(new_vp, prev_no=pno, shot_no=sno, full=r["text"], lead=r["lead"], rest=r["rest"], label=label,
                                               h3=h3, zh=zh, prev_records=prev_records)
                prev_records = []      # 还原只做一次
                errs += [f"{gid}/{r['shot_id']}: native_lead_bound: {x}" for x in e]
                if not e:
                    records.append({"shot_id": r["shot_id"], "shot_no": sno, "prev_no": pno, "full": r["text"], "lead": r["lead"], "rest": r["rest"],
                                    "s": r["s"], "mode": mode, "after": getattr(apply_prompt, "last_after", "") if mode == "split" else ""})
        else:
            # 无 native_lead:回填上次改写(全句 / 插回先入)并剔标记句
            new_vp = _restore(strip_marks(vp), h3, prev_records)
        if write:
            if new_vp != vp or (prompt.get("native_leads") or []) != records:
                _write_prompt(base, ep, pp, prompt, new_vp, records)
                row["updated"] = True
                updated.append(gid)
            vp_check = new_vp
        else:
            vp_check = vp
        # 机检按(写后 / 现状)的 prompt
        for r in need:
            if r["issues"]:
                continue
            sno, pno = ids.index(r["shot_id"]) + 1, ids.index(r["prev"]) + 1
            ce = check_prompt(vp_check, prev_no=pno, shot_no=sno, full=r["text"], lead=r["lead"], rest=r["rest"], h3=h3)
            errs += [f"{gid}/{r['shot_id']}: native_lead_bound: {x}" for x in ce]
        if not need and MARK_RE.search(vp_check):
            errs.append(f"{gid}: native_lead_bound: 残留【原生先入】句但 shot_list 已无 native_lead(跑 --write 清理)")
        row["errors"] = errs
        errors += errs
        rows.append(row)
    return {"leads": recs, "groups": rows, "updated_prompts": updated, "errors": errors, "warnings": warnings}


# ---------------------------------------------------------------- 出片后核验

def audible(base: Path, ep: str, groups=None, log=print) -> dict:
    """native_lead_audible:组 clip 原生轨人声起点是否落在前一镜窗口(先入 ≥ AUDIBLE_MIN_S)。PASS / WARN(未生效)/ skipped(无 clip / 无 meta)。
    用 audio_separation 出人声 stem(模型不可用退回原轨)+ voice_activity 有声段;窗口取 meta boundary_map。"""
    base = Path(base)
    sl = read(base / "directing" / ep / "shot_list.json", {}) or {}
    recs = [r for r in collect(base, ep, sl) if not r["issues"]]
    want = set(groups or [])
    out = {"checks": {}, "rows": [], "warnings": []}
    by_group: dict[str, list[dict]] = {}
    for r in recs:
        if not want or r["group_id"] in want:
            by_group.setdefault(r["group_id"], []).append(r)
    if not by_group:
        out["checks"]["native_lead_audible"] = "skipped: no native leads"
        return out
    try:
        from modules import voice_activity as va
    except ImportError:
        import voice_activity as va
    import tempfile
    status = "PASS"
    for gid, rs in by_group.items():
        clip = base / "assets" / "clips" / ep / f"{gid}.mp4"
        meta = read(base / "assets" / "clips" / ep / f"{gid}.meta.json", {}) or {}
        bm = {b.get("shot_id"): (float(b.get("start_s") or 0), float(b.get("end_s") or 0)) for b in meta.get("boundary_map") or [] if isinstance(b, dict)}
        if not clip.is_file() or not bm:
            out["rows"].append({"group_id": gid, "status": "skipped", "note": "无 clip / meta.boundary_map"})
            continue
        with tempfile.TemporaryDirectory() as td:
            src = clip
            try:
                try:
                    from modules import audio_separation as asep
                except ImportError:
                    import audio_separation as asep
                from _common import DATA_DIR
                sess = asep.load_session(asep.ensure_model(DATA_DIR, log))
                mix = asep.decode(clip)
                _bed, voice = asep.separate(sess, mix)
                src = Path(td) / "voice.wav"
                asep.write_wav(src, voice)
            except Exception as e:  # noqa: BLE001
                out["warnings"].append(f"{gid}: 人声分离不可用({str(e)[-80:]}),按原轨检测")
            runs = va.voiced_runs(src)
        for r in rs:
            pw, sw = bm.get(r["prev"]), bm.get(r["shot_id"])
            if not pw or not sw:
                out["rows"].append({"group_id": gid, "shot_id": r["shot_id"], "status": "skipped", "note": "boundary_map 缺镜"})
                continue
            cand = [x for x in runs if x[1] > pw[0] and x[0] < sw[1]]
            onset = cand[0][0] if cand else None
            lead_s = round(sw[0] - onset, 3) if onset is not None else None
            ok = onset is not None and onset >= pw[0] - 0.1 and lead_s >= AUDIBLE_MIN_S
            row = {"group_id": gid, "shot_id": r["shot_id"], "prev": r["prev"], "onset_s": onset, "lead_s": lead_s, "want_s": r["s"],
                   "status": "PASS" if ok else "WARN"}
            if not ok:
                status = "WARN"
                row["note"] = "先入未生效(人声起点不在前一镜内)" if onset is not None else "窗口内无人声"
            out["rows"].append(row)
    out["checks"]["native_lead_audible"] = status
    return out
