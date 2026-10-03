"""声画分离——人物画外对白 O.S. / V.O.(2026-10-03,输出设置「声画分离」output.sound_split,默认 auto;docs/sound_split.md)。

此前全链默认「声源 = 画内开口的人」:一句台词 = 说话人必然在本镜画内开口(prompt `{}`),分镜里出不来画外对白、
人物 V.O.(内心独白 / 读信 / 回忆声)也没有通道(旁白链只限旁白者声线)。本模块给每句台词一个声源位置字段,并承担
画外句的后期合成、窗口收口、机检与混音摆位。

- 事实源:directing/<ep>/shot_list.json shots[].dialogue_lines[] 新增字段
    placement ∈ on | os | vo(缺省 on;os=说话人在本场但不入画,vo=非本时空声源:内心独白 / 读信 / 回忆中的话 / 幻听传音)
    heard_in  [shot_id…] 听见这句的镜(os/vo 必填,缺省=本镜;必须与本句所在镜同一生成组)
    source_fx plain | phone | door | distance | inner | memory(声处理预设;缺省 os→plain,vo→inner)
    offset_s  ≥0,相对 heard_in 首镜起点(缺省 0.4;同一窗口多句按顺序顺延)
    placement_reason {trigger, evidence}(剧本层 S-*、分镜层 D1–D5、用户 U)与 placement_source script|directing|user
  镜内序号 idx 只数有台词的句子,与 dialogue_tts / dialogue_direction / dub_group 同口径(os/vo 句也占 idx 位)。
- os/vo 句一律后期合成,不进组 prompt `{}`、不挂该人 audio_ref、正文不提其名——§8A「TTS 严禁配对白」红线的根因是画内口型,
  画外无嘴可对,视频原声模式同样成立。合成口径同对白语音库(casting × 形态 + voiceprint 样本 + 台词演法),库开着时直接复用库文件。
- 产物:assets/audio/voice/<ep>/offscreen/<shot>_l<idx>_<CHAR>.mp3(干声,修剪版)+ 同名 .fx.wav(按 source_fx 处理,48k 立体声,
  混音直接用)+ offscreen_manifest.json(逐句 t_in_group_s / duration_s / 窗口 / 状态 / 指纹)。
- 窗口:heard_in 镜总长 − 画内句估时 − 同窗旁白估时 − offset;估时级 est×1.15 ≤ 可用(plan),实测级 duration ≤ 可用×0.9(synth/check);
  超窗 = 回派 dialogue-rewrite 精简或 shot-planning 改 heard_in,本模块不拉长画面、不硬塞。
- 混音:mix_basis.py sources 顶层 offscreen_lines[](mix_rows:绝对时刻已含组 cum_start_s 与 time_ops 换算)作第四路「画外对白」轨;
  fingerprint(manifest) 进 mix.json,改了画外句须重混(mix_basis_current)。
- 与旁白开关正交:旁白开关只管旁白者声线;人物 V.O. 归本项(speaker 必须是人物 / 生物编号,placement_speaker_is_cast)。
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

SCHEMA = "offscreen_lines/v1"
MANIFEST = "offscreen_manifest.json"
LIB_REL = "assets/audio/voice/{ep}/offscreen"
SOUND_SPLIT_MODES = ("off", "script_only", "auto")
PLACEMENTS = ("on", "os", "vo")
DEFAULT_OFFSET_S = 0.4      # 画外句相对 heard_in 首镜起点的缺省偏移(听者先入画再出声)
GAP_S = 0.3                 # 同一窗口多句画外之间的间隔
PLAN_FACTOR = 1.15          # 估时级:est × 1.15 ≤ 可用窗口(同旁白 narration_window_gte_est_x1.15)
FIT_FACTOR = 0.9            # 实测级:实测 ≤ 可用窗口 × 0.9(同 narration_fit 留呼吸空隙)
MIN_WINDOW_S = 0.5
_ID_RE = re.compile(r"((?:CHAR|CRE)-\d+)")

# 声处理预设:ffmpeg -af 滤镜链(干声 → .fx.wav)。plain 只做重采样;电话=窄带 + 压缩;隔门=低通 + 衰减 + 短回声;
# 远处=低通 + 衰减 + 较长回声;内心=贴耳(轻压缩)+ 淡混响;回忆=低通 + 长混响(与 inner 区分:更远、更虚)
SOURCE_FX: dict[str, dict] = {
    "plain": {"label": "原声", "af": []},
    "phone": {"label": "电话", "af": ["highpass=f=300", "lowpass=f=3400", "acompressor=threshold=-18dB:ratio=4:attack=5:release=80",
                                   "volume=-2dB"]},
    "door": {"label": "隔门", "af": ["lowpass=f=1800", "volume=-5dB", "aecho=0.8:0.5:40:0.25"]},
    "distance": {"label": "远处", "af": ["lowpass=f=4000", "volume=-7dB", "aecho=0.7:0.5:70:0.3"]},
    "inner": {"label": "内心", "af": ["acompressor=threshold=-20dB:ratio=2.5:attack=10:release=120", "aecho=0.75:0.6:28:0.18", "volume=+1dB"]},
    "memory": {"label": "回忆", "af": ["lowpass=f=5000", "aecho=0.8:0.8:110:0.38", "volume=-2dB"]},
}
DEFAULT_FX = {"os": "plain", "vo": "inner"}

# 触发白名单:剧本层(声源客观不在画内,由 screenplay / dialogue-rewrite 写括注)、分镜层(storyboard / shot-planning 导演性决策)、用户
TRIGGERS: dict[str, str] = {
    "S-phone": "电话 / 传音另一端", "S-door": "隔门隔墙 / 楼上楼下喊话", "S-exit": "人已出画仍在说",
    "S-remote": "远处看不见的人喊话",
    "S-inner": "心理描写改台词(内心独白)", "S-letter": "读信 / 书信 / 卷轴内容", "S-memory": "回忆里响起的话 / 幻听 / 托梦",
    "D1": "反应镜承接(句长 ≥12 字且戏剧重点在听者)", "D2": "时间尺超限,转画外压反应镜 / 空镜(不改台词文本)",
    "D3": "群戏第四人起的插话(非本镜视觉焦点)", "D4": "边走边说,出画后的句子", "D5": "场首定场镜,首句说话人不在镜内",
    "U": "用户在预览页手动指定",
}
SCRIPT_TRIGGERS = tuple(k for k in TRIGGERS if k.startswith("S-"))
DIRECTING_TRIGGERS = ("D1", "D2", "D3", "D4", "D5")


# ---------------------------------------------------------------- 基础读取

def _read(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
    except (ValueError, OSError):
        return None


def _f(v, default=None):
    try:
        return float(v) if v is not None and v != "" and not isinstance(v, bool) else default
    except (TypeError, ValueError):
        return default


def mode(base: Path) -> str:
    """项目输出设置 output.sound_split(off / script_only / auto;缺省 auto)。"""
    st = _read(Path(base) / "settings.json") or {}
    v = (st.get("output") or {}).get("sound_split")
    return v if v in SOUND_SPLIT_MODES else "auto"


def enabled(base: Path) -> bool:
    return mode(base) != "off"


def placement(ln) -> str:
    """对白行的声源位置(归一化):非法 / 缺省一律 on。"""
    if not isinstance(ln, dict):
        return "on"
    v = str(ln.get("placement") or "").strip().lower()
    return v if v in PLACEMENTS else "on"


def is_onscreen(ln) -> bool:
    return placement(ln) == "on"


def heard_in(ln, shot_id: str) -> list[str]:
    """听见这句的镜(去重保序);画内句恒为本镜;画外句缺省也是本镜。"""
    if placement(ln) == "on":
        return [shot_id]
    raw = ln.get("heard_in") if isinstance(ln, dict) else None
    if isinstance(raw, str):
        raw = [raw]
    out: list[str] = []
    for x in raw or []:
        if isinstance(x, str) and x.strip() and x.strip() not in out:
            out.append(x.strip())
    return out or [shot_id]


def source_fx(ln) -> str:
    p = placement(ln)
    if p == "on":
        return ""
    v = str(ln.get("source_fx") or "").strip().lower() if isinstance(ln, dict) else ""
    return v if v in SOURCE_FX else DEFAULT_FX[p]


def offset_s(ln) -> float:
    v = _f(ln.get("offset_s")) if isinstance(ln, dict) else None
    return round(v, 3) if v is not None and v >= 0 else DEFAULT_OFFSET_S


def line_text(ln) -> str:
    return str(ln.get("text") or ln.get("line") or "").strip() if isinstance(ln, dict) else ""


def dialogue_lines(shot: dict) -> list[dict]:
    """镜内有台词的对白行(dict 且文本非空),顺序即 idx。"""
    return [ln for ln in (shot.get("dialogue_lines") or []) if isinstance(ln, dict) and line_text(ln)]


def onscreen_lines(shot: dict) -> list[dict]:
    return [ln for ln in dialogue_lines(shot) if is_onscreen(ln)]


def offscreen_lines_of(shot: dict) -> list[dict]:
    return [ln for ln in dialogue_lines(shot) if not is_onscreen(ln)]


def expected_audio_plan(group: dict, shots_by_id: dict, narration_anchors=None) -> str:
    """组音频形态的期望值(2026-10-03 四值):有画内句 → dialogue;无画内句但有旁白挂点或 os/vo 句 → voice_over;否则 ambient_only。"""
    ids = [s for s in group.get("shots") or [] if isinstance(s, str)]
    has_on = has_off = False
    for sid in ids:
        s = shots_by_id.get(sid) or {}
        for ln in dialogue_lines(s):
            if is_onscreen(ln):
                has_on = True
            else:
                has_off = True
    if has_on:
        return "dialogue"
    gid = group.get("group_id")
    for a in narration_anchors or []:
        if not isinstance(a, dict):
            continue
        if a.get("anchor_group") == gid or any(x in ids for x in a.get("anchor_shots") or []):
            has_off = True
            break
    return "voice_over" if has_off else "ambient_only"


def audio_plan_ok(actual: str, expected: str) -> bool:
    """narration_over 是 voice_over 的旧名,两者互认。"""
    a, e = (actual or "").strip(), expected
    if e == "voice_over":
        return a in ("voice_over", "narration_over")
    return a == e


# ---------------------------------------------------------------- 时间线与窗口

def _speaker_id(ln: dict) -> str:
    for k in ("speaker", "char", "character_id", "speaker_char"):
        m = _ID_RE.search(str(ln.get(k) or ""))
        if m:
            return m.group(1)
    return ""


def _name_index(base: Path) -> dict[str, str]:
    try:
        try:
            from modules import dialogue_tts as dt
        except ImportError:
            import dialogue_tts as dt
        return dt.name_index(base)
    except Exception:
        return {}


def resolve_speaker(ln: dict, names: dict[str, str]) -> str:
    cid = _speaker_id(ln)
    if cid:
        return cid
    raw = str(ln.get("speaker") or ln.get("char") or "").strip()
    return names.get(raw) or names.get(re.sub(r"[〔【(\[（].*$", "", raw).strip()) or ""


def group_timeline(sl: dict) -> tuple[dict, dict]:
    """→ (shots_by_id, info):info[shot_id] = {group_id, order, start_s(组内), end_s, duration_s}。"""
    shots_by_id = {s["shot_id"]: s for s in sl.get("shots") or [] if isinstance(s, dict) and s.get("shot_id")}
    info: dict[str, dict] = {}
    for g in sl.get("generation_groups") or []:
        if not isinstance(g, dict) or not g.get("group_id"):
            continue
        t = 0.0
        for k, sid in enumerate([x for x in g.get("shots") or [] if isinstance(x, str)]):
            d = _f((shots_by_id.get(sid) or {}).get("duration_s"), 0.0) or 0.0
            info[sid] = {"group_id": g["group_id"], "order": k, "start_s": round(t, 3), "end_s": round(t + d, 3), "duration_s": d}
            t += d
    return shots_by_id, info


def _line_est(base: Path, ln: dict, speaker: str, cpms: dict) -> float:
    e = _f(ln.get("est_duration_s"))
    if e and e > 0:
        return round(e, 2)
    try:
        try:
            from modules import time_cost as tc
        except ImportError:
            import time_cost as tc
        return tc.line_est(line_text(ln), str(ln.get("pace") or "") or tc.guess_pace(str(ln.get("emotion") or "")), cpms.get(speaker))
    except Exception:
        return round(max(0.7, len(line_text(ln)) / 4.2 + 0.7), 2)


def collect(base: Path, ep: str, shot_list: dict | None = None, durations: dict | None = None) -> list[dict]:
    """全部 os/vo 句(镜序 × 句序),带窗口与组内时刻。durations:{(shot_id, idx): 实测秒}(synth 后用实测顺延,否则按估时)。
    记录:{shot_id, idx, group_id, speaker, speaker_raw, text, placement, heard_in, source_fx, offset_s, reason, source, est_s,
          window{start_s, end_s, span_s, on_s, narration_s, available_s}, t_in_group_s, issues[]}。
    issues 只记结构性问题(heard_in 跨组 / 不存在的镜等),机检文案由 validate 组织。"""
    base = Path(base)
    sl = shot_list if shot_list is not None else (_read(base / "directing" / ep / "shot_list.json") or {})
    shots_by_id, info = group_timeline(sl)
    names = _name_index(base)
    try:
        try:
            from modules import time_cost as tc
        except ImportError:
            import time_cost as tc
        cpms = tc.character_cpm(base)
    except Exception:
        cpms = {}
    anchors = [a for a in sl.get("narration_anchors") or [] if isinstance(a, dict)]
    # 画内句占时按镜累计(扣窗口用)
    on_by_shot: dict[str, float] = {}
    for sid, s in shots_by_id.items():
        on_by_shot[sid] = round(sum(_line_est(base, ln, resolve_speaker(ln, names), cpms) for ln in onscreen_lines(s)), 2)
    out: list[dict] = []
    for s in sl.get("shots") or []:
        if not isinstance(s, dict) or not s.get("shot_id"):
            continue
        sid = s["shot_id"]
        for idx, ln in enumerate(dialogue_lines(s)):
            if is_onscreen(ln):
                continue
            spk = resolve_speaker(ln, names)
            hi = heard_in(ln, sid)
            gid = (info.get(sid) or {}).get("group_id", "")
            issues: list[str] = []
            good = []
            for h in hi:
                if h not in shots_by_id:
                    issues.append(f"heard_in 引用不存在的镜 {h}")
                elif (info.get(h) or {}).get("group_id", "") != gid:
                    issues.append(f"heard_in {h} 不在本句所在组 {gid or '?'} 内(画外句只能压在同组画面上)")
                else:
                    good.append(h)
            if not good:
                good = [sid] if sid in info else []
            ws = min((info[h]["start_s"] for h in good), default=0.0)
            we = max((info[h]["end_s"] for h in good), default=0.0)
            on_s = round(sum(on_by_shot.get(h, 0.0) for h in good), 2)
            narr = 0.0
            for a in anchors:
                ash = [x for x in a.get("anchor_shots") or [] if isinstance(x, str)]
                if (ash and set(ash) & set(good)) or (not ash and a.get("anchor_group") == gid and gid):
                    narr += _f(a.get("est_duration_s"), 0.0) or 0.0
            off = offset_s(ln)
            est = _line_est(base, ln, spk, cpms)
            reason = ln.get("placement_reason") if isinstance(ln.get("placement_reason"), dict) else None
            out.append({
                "shot_id": sid, "idx": idx, "group_id": gid, "speaker": spk,
                "speaker_raw": str(ln.get("speaker") or ln.get("char") or ""), "text": line_text(ln),
                "placement": placement(ln), "heard_in": good or hi, "source_fx": source_fx(ln), "offset_s": off,
                "reason": reason, "source": str(ln.get("placement_source") or "").strip().lower(),
                "emotion": str(ln.get("emotion") or ln.get("tone") or ""), "est_s": est,
                "window": {"start_s": round(ws, 3), "end_s": round(we, 3), "span_s": round(we - ws, 3),
                           "on_s": on_s, "narration_s": round(narr, 2), "available_s": 0.0},
                "t_in_group_s": 0.0, "issues": issues,
            })
    # 同一组内按窗口起点 / 镜序 / 句序顺延摆位;可用窗口 = 跨度 − 画内 − 旁白 − 偏移 − 同窗前面句的占用
    out.sort(key=lambda r: (r["group_id"], r["window"]["start_s"], (info.get(r["shot_id"]) or {}).get("order", 0), r["idx"]))
    cursor: dict[tuple, float] = {}       # (group_id, window key) → 已占到的组内时刻
    for r in out:
        key = (r["group_id"], tuple(r["heard_in"]))
        w = r["window"]
        t0 = max(w["start_s"] + r["offset_s"], cursor.get(key, -1.0))
        dur = (durations or {}).get((r["shot_id"], r["idx"]))
        used = round(dur if dur else r["est_s"], 3)
        r["t_in_group_s"] = round(t0, 3)
        w["available_s"] = round(max(0.0, w["end_s"] - t0 - w["on_s"] - w["narration_s"]), 2)
        cursor[key] = round(t0 + used + GAP_S, 3)
    return out


def current_fingerprint(base: Path, ep: str, shot_list: dict | None = None) -> str | None:
    """当前 shot_list 画外句集合的指纹(台词 / 位置 / 效果 / 偏移 / 听见的镜);None = 没有画外句。"""
    recs = collect(base, ep, shot_list)
    if not recs:
        return None
    slim = [[r["shot_id"], r["idx"], r["speaker"], r["placement"], r["group_id"], r["heard_in"], r["source_fx"],
             r["offset_s"], r["text"]] for r in recs]
    return hashlib.sha256(json.dumps(slim, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- 机检(纯读)

def _registered_ids(base: Path) -> set[str]:
    ids: set[str] = set()
    for rel, key in (("bible/characters/index.json", "characters"), ("bible/creatures/index.json", "creatures")):
        d = _read(Path(base) / rel) or {}
        for c in d.get(key) or []:
            if isinstance(c, dict) and c.get("id"):
                ids.add(str(c["id"]))
    return ids


def validate(base: Path, ep: str, shot_list: dict | None = None) -> list[str]:
    """结构 / 归属 / 窗口机检 → 消息列表 "<gid>/<sid> <check>: …"(空 = 全过)。
    placement_valid / placement_speaker_is_cast / placement_reason_valid / offscreen_fit / post_voice_no_overlap。
    声画分离关闭时任何 os/vo 句都是 placement_valid 违规;仅剧本标记模式下分镜层(directing)来源的画外句违规。"""
    base = Path(base)
    sl = shot_list if shot_list is not None else (_read(base / "directing" / ep / "shot_list.json") or {})
    m = mode(base)
    errs: list[str] = []
    recs = collect(base, ep, sl)
    if not recs:
        return errs
    shots_by_id, _info = group_timeline(sl)
    reg = _registered_ids(base)
    ep_cast: set[str] = set()
    for s in shots_by_id.values():
        ep_cast.update(x for x in (s.get("characters") or []) + (s.get("creatures") or []) if isinstance(x, str))
    for g in sl.get("generation_groups") or []:
        if isinstance(g, dict):
            ep_cast.update(x for x in (g.get("characters_union") or []) + (g.get("creatures_union") or []) if isinstance(x, str))
            sc = g.get("scene_cast")
            if isinstance(sc, list):
                ep_cast.update(x if isinstance(x, str) else str((x or {}).get("id") or "") for x in sc)
    for r in recs:
        tag = f"{r['group_id'] or '?'}/{r['shot_id']}/l{r['idx']:02d}"
        if m == "off":
            errs.append(f"{tag} placement_valid: 声画分离已关闭(output.sound_split=off),该句标了 {r['placement']}——改回画内或开启设置")
            continue
        src = r["source"]
        trig = str((r["reason"] or {}).get("trigger") or "").strip()
        if m == "script_only" and (src == "directing" or (not src and trig in DIRECTING_TRIGGERS)):
            errs.append(f"{tag} placement_valid: 仅剧本标记模式下分镜层不得自行转画外(placement_source=directing / {trig or '-'})")
        for i in r["issues"]:
            errs.append(f"{tag} placement_valid: {i}")
        if not r["speaker"]:
            errs.append(f"{tag} placement_speaker_is_cast: 画外句说话人不是人物 / 生物编号:{r['speaker_raw'] or '(空)'}(旁白走 narration.md,不借人物 V.O.)")
        elif reg and r["speaker"] not in reg:
            errs.append(f"{tag} placement_speaker_is_cast: {r['speaker']} 未在 bible index 登记")
        elif ep_cast and r["speaker"] not in ep_cast:
            errs.append(f"{tag} placement_speaker_is_cast: {r['speaker']} 不在本集任何镜 / 组的人物集合里(WARN:确认是本集 cast 的画外声)")
        # 来源与理由
        if src == "script" or (not src and trig in SCRIPT_TRIGGERS):
            if trig and trig not in SCRIPT_TRIGGERS:
                errs.append(f"{tag} placement_reason_valid: 剧本层画外句的 trigger 应为 S-*(现为 {trig})")
        elif src in ("directing", "user") or not src:
            if trig not in DIRECTING_TRIGGERS and trig != "U":
                errs.append(f"{tag} placement_reason_valid: 分镜层转画外须写 placement_reason.trigger ∈ {list(DIRECTING_TRIGGERS) + ['U']}(现为 {trig or '缺'})")
            elif trig in DIRECTING_TRIGGERS and not str((r["reason"] or {}).get("evidence") or "").strip():
                errs.append(f"{tag} placement_reason_valid: {trig} 须写 evidence(超限报告 / 听者情绪标注 / 出画动线等依据)")
        else:
            errs.append(f"{tag} placement_reason_valid: placement_source 非法:{src}")
        # 窗口
        w = r["window"]
        if w["span_s"] <= 0:
            errs.append(f"{tag} offscreen_fit: heard_in 窗口为空(镜时长缺失)")
            continue
        room_wo_narr = w["end_s"] - r["t_in_group_s"] - w["on_s"]
        if w["narration_s"] > 0 and room_wo_narr - w["narration_s"] < r["est_s"] * PLAN_FACTOR <= room_wo_narr:
            errs.append(f"{tag} post_voice_no_overlap: 同一窗口 {r['heard_in']} 旁白 {w['narration_s']:.1f}s 与画外句 {r['est_s']:.1f}s 装不下"
                        f"(窗口 {room_wo_narr:.1f}s)——挪旁白挂点或改 heard_in")
        elif r["est_s"] * PLAN_FACTOR > w["available_s"]:
            errs.append(f"{tag} offscreen_fit: 估时 {r['est_s']:.1f}s×{PLAN_FACTOR} > 可用窗口 {w['available_s']:.1f}s"
                        f"(heard_in {r['heard_in']} 跨度 {w['span_s']:.1f}s − 画内 {w['on_s']:.1f}s − 旁白 {w['narration_s']:.1f}s − 起点偏移)——精简台词或扩 heard_in")
    return errs


_BRACE_RE = re.compile(r"\{([^{}]*)\}")


def _norm(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", str(text or ""), flags=re.UNICODE).lower()


def check_prompts(base: Path, ep: str, shot_list: dict | None = None) -> list[str]:
    """offscreen_not_in_prompt:组 prompt 的 `{}` 内不得出现画外句文本;audio_refs 不得含只有画外句的说话人。"""
    base = Path(base)
    sl = shot_list if shot_list is not None else (_read(base / "directing" / ep / "shot_list.json") or {})
    recs = collect(base, ep, sl)
    if not recs:
        return []
    shots_by_id, _ = group_timeline(sl)
    names = _name_index(base)
    errs: list[str] = []
    by_group: dict[str, list[dict]] = {}
    for r in recs:
        by_group.setdefault(r["group_id"], []).append(r)
    groups = {g["group_id"]: g for g in sl.get("generation_groups") or [] if isinstance(g, dict) and g.get("group_id")}
    for gid, rs in by_group.items():
        pj = _read(base / "assets" / "prompts" / ep / f"{gid}.json")
        if not pj:
            continue
        vp = str(pj.get("video_prompt") or "")
        braces = [_norm(x) for x in _BRACE_RE.findall(vp)]
        on_speakers = set()
        for sid in (groups.get(gid) or {}).get("shots") or []:
            for ln in onscreen_lines(shots_by_id.get(sid) or {}):
                on_speakers.add(resolve_speaker(ln, names))
        for r in rs:
            key = _norm(r["text"])
            if key and any(key and (key in b or b in key) and len(b) >= min(len(key), 4) for b in braces):
                errs.append(f"{gid}/{r['shot_id']}/l{r['idx']:02d} offscreen_not_in_prompt: 画外句进了 prompt `{{}}`(模型会让人物画内开口):「{r['text'][:20]}」")
            if r["speaker"] and r["speaker"] not in on_speakers:
                for i, a in enumerate(pj.get("audio_refs") or [], 1):
                    if r["speaker"] in str(a):
                        errs.append(f"{gid}/{r['shot_id']}/l{r['idx']:02d} offscreen_not_in_prompt: {r['speaker']} 本组只有画外句却挂了 audio_refs[{i}](画外声后期合成,不挂音色锚)")
                        break
    return errs


# ---------------------------------------------------------------- 台账 / 合成

def lib_dir(base: Path, ep: str) -> Path:
    return Path(base) / LIB_REL.format(ep=ep)


def load_manifest(base: Path, ep: str) -> dict | None:
    return _read(lib_dir(base, ep) / MANIFEST)


def fingerprint(manifest: dict | None) -> str | None:
    """已摆位画外轨的指纹(进 mix.json):逐句 位置 / 时长 / 文件 key;无台账或无可混句 = None。"""
    if not isinstance(manifest, dict):
        return None
    rows = [[e.get("group_id"), e.get("shot_id"), e.get("idx"), e.get("speaker"), e.get("placement"), e.get("t_in_group_s"),
             e.get("duration_s"), e.get("key"), e.get("source_fx"), e.get("gain_db", 0)]
            for e in manifest.get("lines") or [] if isinstance(e, dict) and e.get("status") in ("ok", "overflow") and e.get("fx_file")]
    if not rows:
        return None
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def is_stale(base: Path, ep: str, manifest: dict | None = None, shot_list: dict | None = None) -> bool:
    """台账是否落后于当前 shot_list 的画外句集合(新增 / 删句 / 改位置 / 改效果 / 改台词)。"""
    man = manifest if manifest is not None else load_manifest(base, ep)
    cur = current_fingerprint(base, ep, shot_list)
    if man is None:
        return cur is not None
    return (man.get("source_fingerprint") or None) != cur


def _ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def apply_fx(src: Path, dst: Path, fx: str) -> list[str]:
    """干声 → 按预设处理的 48k 立体声 wav(混音直接用);返回用到的滤镜链。无 ffmpeg 时抛错。"""
    ff = _ffmpeg()
    if not ff:
        raise RuntimeError("缺 ffmpeg,无法做声处理")
    chain = list((SOURCE_FX.get(fx) or SOURCE_FX["plain"])["af"]) + ["aresample=48000", "aformat=channel_layouts=stereo"]
    tmp = dst.with_name(dst.stem + ".tmp.wav")
    r = subprocess.run([ff, "-hide_banner", "-nostats", "-loglevel", "error", "-y", "-i", str(src), "-af", ",".join(chain),
                        "-c:a", "pcm_s16le", str(tmp)], capture_output=True, text=True, timeout=300)
    if r.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg 声处理失败:{(r.stderr or '').strip()[-200:]}")
    tmp.replace(dst)
    return chain


def plan(base: Path, ep: str, shot_list: dict | None = None) -> dict:
    """纯读:逐句窗口 / 估时 / 合成前置(选角 / 形态 / 样本)与机检问题;不合成。"""
    base = Path(base)
    sl = shot_list if shot_list is not None else (_read(base / "directing" / ep / "shot_list.json") or {})
    recs = collect(base, ep, sl)
    by_key = {}
    if recs:
        try:
            try:
                from modules import dialogue_tts as dt
            except ImportError:
                import dialogue_tts as dt
            by_key = {(e["shot_id"], e["idx"]): e for e in dt.plan(base, ep, sl)["lines"]}
        except Exception as exc:          # 选角表 / 渠道配置读不出:plan 仍给窗口,合成前置标 unknown
            by_key = {"_error": str(exc)}
    for r in recs:
        e = by_key.get((r["shot_id"], r["idx"])) if isinstance(by_key, dict) else None
        if isinstance(e, dict):
            r["tts"] = {"status": e.get("status"), "reason": e.get("reason") or "", "variant": e.get("variant"),
                        "tts_voice": e.get("tts_voice"), "key": e.get("key"), "anchored": e.get("anchored")}
        else:
            r["tts"] = {"status": "unknown", "reason": by_key.get("_error", "") if isinstance(by_key, dict) else ""}
    return {"mode": mode(base), "episode": ep, "lines": recs, "issues": validate(base, ep, sl),
            "source_fingerprint": current_fingerprint(base, ep, sl)}


def synth(base: Path, ep: str, *, force: bool = False, tts=None, probe=None, log=print, only_groups=None,
          shot_list: dict | None = None) -> dict:
    """合成 / 刷新画外句音频并写台账。tts(entry, out_path) / probe(path)->秒 可注入(测试);任一句失败记 failed 不中断。
    流程:dialogue_tts.plan 给每句 key / 选角 / 形态(与对白语音库同口径)→ 库开着且库文件新鲜就直接复制,否则合成到 _raw/ 再修剪
    → 按 source_fx 出 .fx.wav → 实测时长 → 可用窗口 × FIT_FACTOR 收口(超出且允许变速时先节奏贴合,仍超 = overflow)。"""
    base = Path(base)
    try:
        from modules import dialogue_tts as dt
    except ImportError:
        import dialogue_tts as dt
    sl = shot_list if shot_list is not None else (_read(base / "directing" / ep / "shot_list.json") or {})
    ldir = lib_dir(base, ep)
    rdir = ldir / "_raw"
    old = load_manifest(base, ep) or {}
    old_by = {(e.get("shot_id"), e.get("idx")): e for e in old.get("lines") or [] if isinstance(e, dict)}
    recs = collect(base, ep, sl)
    only = set(only_groups or [])
    p = dt.plan(base, ep, sl)
    by_key = {(e["shot_id"], e["idx"]): e for e in p["lines"]}
    by_char = dt.voice_mode(p["provider"], p["model"])[0] == "design"
    tts = tts or dt._default_tts(base)
    probe = probe or dt.probe_duration
    lib_ok = dt.enabled(base)
    lib_audio = dt.line_audio(base, ep, dt.load_manifest(base, ep)) if lib_ok else {}
    max_tempo = dt.default_max_tempo(base)
    lines: list[dict] = []
    durations: dict[tuple, float] = {}
    t_start = time.time()
    n_synth = n_fail = 0
    with dt._Lock(ldir / ".lock"):
        ldir.mkdir(parents=True, exist_ok=True)
        for r in recs:
            key = (r["shot_id"], r["idx"])
            prev = old_by.get(key) or {}
            e = by_key.get(key)
            entry = {k: r[k] for k in ("shot_id", "idx", "group_id", "speaker", "text", "placement", "heard_in", "source_fx",
                                       "offset_s", "est_s", "emotion")}
            entry.update(reason=r["reason"], placement_source=r["source"])
            if only and r["group_id"] not in only and prev.get("status") in ("ok", "overflow"):
                lines.append(prev)       # 本次不处理的组沿用旧记录
                if prev.get("duration_s"):
                    durations[key] = float(prev["duration_s"])
                continue
            if not e or e.get("status") == "unbound":
                entry.update(status="unbound", reason_tts=(e or {}).get("reason") or "shot_list 里找不到该句", file="", fx_file="")
                lines.append(entry)
                log(f"[WARN] {r['group_id']}/{r['shot_id']}/l{r['idx']:02d} 画外句不能合成:{entry['reason_tts']}")
                continue
            entry.update(variant=e.get("variant"), variant_source=e.get("variant_source"), tts_voice=e.get("tts_voice"),
                         tts_model=e.get("tts_model"), key=e.get("key"), file=e["file"], fx_file=e["file"][:-4] + ".fx.wav")
            dry = ldir / entry["file"]
            fxp = ldir / entry["fx_file"]
            fresh = (not force and prev.get("key") == entry["key"] and prev.get("status") in ("ok", "overflow")
                     and dry.is_file() and fxp.is_file() and prev.get("source_fx") == r["source_fx"])
            try:
                if not fresh:
                    lib = lib_audio.get(key)
                    if lib and lib.get("key") == entry["key"] and Path(lib["path"]).is_file():
                        shutil.copyfile(lib["path"], dry)
                        entry["source"] = f"dialogue_tts:{lib.get('file')}"
                    else:
                        rdir.mkdir(parents=True, exist_ok=True)
                        raw = rdir / entry["file"]
                        ent = dict(e, _voice_by_character=by_char)
                        tts(ent, raw)
                        entry["trim"] = dt.trim_file(raw, dry, max_pause=dt.default_max_pause(base), probe=probe)
                        entry["source"] = "tts"
                        n_synth += 1
                    entry["fx_chain"] = apply_fx(dry, fxp, r["source_fx"])
                    entry["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                else:
                    entry.update(source=prev.get("source"), trim=prev.get("trim"), fx_chain=prev.get("fx_chain"),
                                 generated_at=prev.get("generated_at"), pace=prev.get("pace"))
                dur = probe(fxp)
                if dur is None:
                    raise RuntimeError("读不到声处理后音频时长")
                entry["duration_s"] = round(float(dur), 3)
                entry["gain_db"] = 0.0
                entry["status"] = "ok"
            except Exception as exc:
                n_fail += 1
                entry.update(status="failed", error=str(exc)[-300:])
                log(f"[WARN] {r['group_id']}/{r['shot_id']}/l{r['idx']:02d} 画外句合成失败:{exc}")
            if entry.get("duration_s"):
                durations[key] = entry["duration_s"]
            lines.append(entry)
        # 用实测时长重新顺延摆位,再做窗口收口
        placed = {(x["shot_id"], x["idx"]): x for x in collect(base, ep, sl, durations)}
        for entry in lines:
            r2 = placed.get((entry["shot_id"], entry["idx"]))
            if not r2:
                continue
            entry["t_in_group_s"] = r2["t_in_group_s"]
            entry["window"] = r2["window"]
            if entry.get("status") != "ok":
                continue
            room = r2["window"]["available_s"] * FIT_FACTOR
            if entry["duration_s"] > room + 1e-6:
                fxp = ldir / entry["fx_file"]
                dry = ldir / entry["file"]
                if max_tempo > 1.0 and room >= MIN_WINDOW_S:
                    try:
                        paced = ldir / "_paced" / entry["file"]
                        pc = dt.pace_file(dry, paced, room, max_tempo=max_tempo, probe=probe)
                        entry["fx_chain"] = apply_fx(paced, fxp, entry["source_fx"])
                        d2 = probe(fxp)
                        entry["pace"] = dict(pc, file=f"_paced/{entry['file']}")
                        entry["duration_s"] = round(float(d2), 3) if d2 is not None else pc["duration_s"]
                    except Exception as exc:
                        log(f"[WARN] {entry['group_id']}/{entry['shot_id']}/l{entry['idx']:02d} 节奏贴合失败:{exc}")
                if entry["duration_s"] > room + 1e-6:
                    entry["status"] = "overflow"
                    entry["overflow_s"] = round(entry["duration_s"] - room, 2)
                    log(f"[FAIL] {entry['group_id']}/{entry['shot_id']}/l{entry['idx']:02d} offscreen_fit: 实测 {entry['duration_s']:.2f}s > 可用 "
                        f"{r2['window']['available_s']:.2f}s×{FIT_FACTOR}(heard_in {entry['heard_in']})——精简台词或改 heard_in,不拉长画面")
        wanted = {e.get("file") for e in lines if e.get("file")} | {e.get("fx_file") for e in lines if e.get("fx_file")}
        for f in ldir.iterdir() if ldir.is_dir() else []:
            if f.is_file() and f.suffix in (".mp3", ".wav") and f.name not in wanted and not f.name.startswith("."):
                (ldir / "_prev").mkdir(exist_ok=True)
                f.replace(ldir / "_prev" / f.name)
        summary = {"total": len(lines), "ok": sum(1 for e in lines if e.get("status") == "ok"),
                   "overflow": sum(1 for e in lines if e.get("status") == "overflow"),
                   "unbound": sum(1 for e in lines if e.get("status") == "unbound"),
                   "failed": sum(1 for e in lines if e.get("status") == "failed"), "synthesized": n_synth}
        man = {"schema": SCHEMA, "ep": ep, "mode": mode(base), "generated_by": "modules/offscreen_lines.py",
               "note": "人物画外对白(O.S./V.O.)后期合成轨;t_in_group_s 为组内时刻,混音经 mix_basis sources 的 offscreen_lines 换算绝对时刻",
               "synced_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "sync_seconds": round(time.time() - t_start, 1),
               "tts_provider": p["provider"], "tts_model": p["model"], "fit_factor": FIT_FACTOR, "max_tempo": max_tempo,
               "source_fingerprint": current_fingerprint(base, ep, sl), "lines": lines, "summary": summary,
               "checks": {"offscreen_fit": "FAIL" if summary["overflow"] else "PASS",
                          "offscreen_all_bound": "FAIL" if summary["unbound"] or summary["failed"] else "PASS",
                          "unbound_lines": [f"{e['shot_id']}/l{e['idx']:02d}" for e in lines if e.get("status") == "unbound"],
                          "failed_lines": [f"{e['shot_id']}/l{e['idx']:02d}" for e in lines if e.get("status") == "failed"],
                          "overflow_lines": [f"{e['shot_id']}/l{e['idx']:02d}" for e in lines if e.get("status") == "overflow"]}}
        man["fingerprint"] = fingerprint(man)
        (ldir / MANIFEST).write_text(json.dumps(man, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return man


def check(base: Path, ep: str, shot_list: dict | None = None) -> dict:
    """机检汇总:{mode, checks{name: PASS|WARN|FAIL|skipped}, errors[], warnings[]}。
    声画分离关闭且无画外句 → 全部 skipped;有画外句照常报 placement_valid。"""
    base = Path(base)
    sl = shot_list if shot_list is not None else (_read(base / "directing" / ep / "shot_list.json") or {})
    names = ["placement_valid", "placement_speaker_is_cast", "placement_reason_valid", "offscreen_fit", "post_voice_no_overlap",
             "offscreen_not_in_prompt", "offscreen_synced", "offscreen_all_bound"]
    m = mode(base)
    recs = collect(base, ep, sl)
    if not recs:
        tag = "skipped: sound_split off" if m == "off" else "skipped: no offscreen lines"
        return {"mode": m, "checks": {n: tag for n in names}, "errors": [], "warnings": [], "count": 0}
    msgs = validate(base, ep, sl) + check_prompts(base, ep, sl)
    errors = [x for x in msgs if "(WARN" not in x]
    warnings = [x for x in msgs if "(WARN" in x]
    man = load_manifest(base, ep)
    if man is None:
        errors.append(f"{ep} offscreen_synced: 尚无画外对白台账(先跑 offscreen_lines.py synth)")
    elif is_stale(base, ep, man, sl):
        errors.append(f"{ep} offscreen_synced: 台账落后于当前 shot_list 画外句(新增 / 删句 / 改位置 / 改效果 / 改台词),重跑 synth")
    else:
        for e in man.get("lines") or []:
            tag = f"{e.get('group_id')}/{e.get('shot_id')}/l{int(e.get('idx') or 0):02d}"
            if e.get("status") == "overflow":
                errors.append(f"{tag} offscreen_fit: 实测 {e.get('duration_s')}s 超可用窗口(overflow {e.get('overflow_s')}s)")
            elif e.get("status") in ("unbound", "failed"):
                errors.append(f"{tag} offscreen_all_bound: {e.get('status')}:{e.get('reason_tts') or e.get('error') or ''}")
    checks = {}
    for n in names:
        if any(f" {n}:" in x for x in errors):
            checks[n] = "FAIL"
        elif any(f" {n}:" in x for x in warnings):
            checks[n] = "WARN"
        else:
            checks[n] = "PASS"
    return {"mode": m, "checks": checks, "errors": errors, "warnings": warnings, "count": len(recs)}


# ---------------------------------------------------------------- 混音 / 预览

def mix_rows(base: Path, ep: str, rows: list[dict]) -> list[dict]:
    """给 mix_basis sources:台账里可混的画外句 → 绝对时刻。rows = mix_manifest.with_timing 的组行(group_id / cum_start_s / time_ops)。
    组内时刻经该组 time_ops 换算(删段内的句子在该版本里已不存在 → 跳过并标 dropped)。"""
    base = Path(base)
    man = load_manifest(base, ep)
    if not man:
        return []
    try:
        try:
            from modules import timemap
        except ImportError:
            import timemap
    except Exception:
        timemap = None
    by_group = {r.get("group_id"): r for r in rows or [] if isinstance(r, dict)}
    ldir = lib_dir(base, ep)
    out: list[dict] = []
    for e in man.get("lines") or []:
        if not isinstance(e, dict) or e.get("status") not in ("ok", "overflow") or not e.get("fx_file"):
            continue
        row = by_group.get(e.get("group_id"))
        if not row:
            continue
        t = float(e.get("t_in_group_s") or 0.0)
        ops = row.get("time_ops") or []
        if ops and timemap is not None:
            # 起点落在删除区间内的句子在该版本里已不存在(与旁白 / 对白开口同口径),不得再往上铺
            if any(float(o["out_len"]) <= 0 and o["src_t0"] <= t < o["src_t1"] for o in timemap.normalize_ops(ops)):
                out.append({"group_id": e["group_id"], "shot_id": e["shot_id"], "idx": e["idx"], "speaker": e.get("speaker"),
                            "placement": e.get("placement"), "dropped": "time_ops 删除区间", "t0": None})
                continue
            t = timemap.map_time(ops, t)
        out.append({"group_id": e["group_id"], "shot_id": e["shot_id"], "idx": e["idx"], "speaker": e.get("speaker"),
                    "placement": e.get("placement"), "source_fx": e.get("source_fx"), "text": e.get("text"),
                    "t0": round(float(row.get("cum_start_s") or 0.0) + t, 3), "duration_s": e.get("duration_s"),
                    "file": str(ldir / e["fx_file"]), "gain_db": float(e.get("gain_db") or 0.0), "status": e.get("status")})
    return out


def status(base: Path, ep: str) -> dict:
    """预览页汇总:模式、逐句状态、台账是否落后。"""
    base = Path(base)
    sl = _read(base / "directing" / ep / "shot_list.json") or {}
    recs = collect(base, ep, sl)
    man = load_manifest(base, ep) or {}
    by = {(e.get("shot_id"), e.get("idx")): e for e in man.get("lines") or [] if isinstance(e, dict)}
    lines = []
    for r in recs:
        e = by.get((r["shot_id"], r["idx"])) or {}
        lines.append({"shot_id": r["shot_id"], "idx": r["idx"], "group_id": r["group_id"], "speaker": r["speaker"], "text": r["text"],
                      "placement": r["placement"], "heard_in": r["heard_in"], "source_fx": r["source_fx"], "offset_s": r["offset_s"],
                      "reason": r["reason"], "placement_source": r["source"], "est_s": r["est_s"], "window": r["window"],
                      "t_in_group_s": r["t_in_group_s"], "duration_s": e.get("duration_s"), "status": e.get("status") or "missing",
                      "file": (LIB_REL.format(ep=ep) + "/" + e["fx_file"]) if e.get("fx_file") else ""})
    by_status: dict[str, int] = {}
    for x in lines:
        by_status[x["status"]] = by_status.get(x["status"], 0) + 1
    return {"mode": mode(base), "total": len(lines), "by_status": by_status, "lines": lines,
            "manifest_synced_at": man.get("synced_at"), "stale": is_stale(base, ep, man or None, sl) if lines else False}


# ---------------------------------------------------------------- 写回(预览页 / 宿主)

def set_line(base: Path, ep: str, shot_id: str, idx: int, *, placement: str | None = None, heard_in: list | None = None,
             source_fx: str | None = None, offset_s: float | None = None, reason: dict | str | None = None,
             source: str = "user") -> dict:
    """改一句台词的声源字段并写回 shot_list(json 保真,只动该句)。placement=on 时清掉画外专用字段。
    heard_in 必须与本句所在镜同组;非法输入抛 ValueError。返回更新后的对白行。"""
    base = Path(base)
    path = base / "directing" / ep / "shot_list.json"
    sl = _read(path)
    if not sl:
        raise ValueError(f"读不到 {path}")
    shots_by_id, info = group_timeline(sl)
    shot = shots_by_id.get(shot_id)
    if not shot:
        raise ValueError(f"镜 {shot_id} 不存在")
    lines = dialogue_lines(shot)
    if not isinstance(idx, int) or idx < 0 or idx >= len(lines):
        raise ValueError(f"{shot_id} 没有第 {idx} 句台词")
    ln = lines[idx]
    if placement is not None:
        pl = str(placement).strip().lower()
        if pl not in PLACEMENTS:
            raise ValueError(f"placement 须为 {PLACEMENTS}")
        if pl == "on":
            ln.pop("placement", None)
            for k in ("heard_in", "source_fx", "offset_s", "placement_reason"):
                ln.pop(k, None)
            ln["placement_source"] = source
            path.write_text(json.dumps(sl, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return ln
        ln["placement"] = pl
    pl = ln.get("placement") if ln.get("placement") in PLACEMENTS else None
    if pl is None:
        raise ValueError("该句是画内句,先把 placement 设为 os / vo")
    if heard_in is not None:
        hi = [heard_in] if isinstance(heard_in, str) else list(heard_in or [])
        hi = [str(x).strip() for x in hi if str(x).strip()]
        if not hi:
            raise ValueError("heard_in 不能为空")
        gid = (info.get(shot_id) or {}).get("group_id")
        for h in hi:
            if h not in shots_by_id:
                raise ValueError(f"heard_in 引用不存在的镜 {h}")
            if (info.get(h) or {}).get("group_id") != gid:
                raise ValueError(f"heard_in {h} 与 {shot_id} 不在同一生成组({gid})")
        ln["heard_in"] = hi
    elif not ln.get("heard_in"):
        ln["heard_in"] = [shot_id]
    if source_fx is not None:
        fx = str(source_fx).strip().lower()
        if fx not in SOURCE_FX:
            raise ValueError(f"source_fx 须为 {tuple(SOURCE_FX)}")
        ln["source_fx"] = fx
    elif not ln.get("source_fx"):
        ln["source_fx"] = DEFAULT_FX[pl]
    if offset_s is not None:
        v = _f(offset_s)
        if v is None or v < 0:
            raise ValueError("offset_s 须为 ≥0 的秒数")
        ln["offset_s"] = round(v, 3)
    elif ln.get("offset_s") is None:
        ln["offset_s"] = DEFAULT_OFFSET_S
    if reason is not None:
        if isinstance(reason, str):
            reason = {"trigger": "U" if source == "user" else reason, "evidence": reason}
        if not isinstance(reason, dict) or str(reason.get("trigger") or "").strip() not in TRIGGERS:
            raise ValueError(f"placement_reason.trigger 须为 {list(TRIGGERS)}")
        ln["placement_reason"] = {"trigger": str(reason["trigger"]).strip(), "evidence": str(reason.get("evidence") or "").strip()}
    elif not isinstance(ln.get("placement_reason"), dict):
        ln["placement_reason"] = {"trigger": "U" if source == "user" else "", "evidence": ""}
    ln["placement_source"] = source if source in ("script", "directing", "user") else "user"
    path.write_text(json.dumps(sl, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return ln
