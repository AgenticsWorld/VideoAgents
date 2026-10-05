"""time_cost.py — 时间尺:台词 + 动作节拍的统一估时口径(2026-10-03,docs/time_cost.md)。

背景:fengshen3 ep07 grp011(28s/21 镜)视频丢了一句 5 秒台词、后半段乱序。根因不是提示词太长,而是
上游三层都在超额分配时间——台词估时只算「字数 ÷ 语速」(实测偏短约三成)、对白镜镜长 = 估时(零余量)、
动作节拍从来没人估过秒数(pacing 自上而下分时长,blocking 一个 1.2s 镜塞 3 拍,prompt 再展开成 5 个动作)。

本模块是唯一的时间口径,四处调用(都是纯函数,文件读写在各 CLI):
  pacing   场级:剧本动作行节拍 + 台词 ≤ 分配时长;Σ需求 > 集预算 → 加长单集(≤ 设置「可加长单集」%)> 精简台词 > 合并动作短镜
  shots    镜级:镜长 ≥ Σ节拍单价 + 台词估时 + 余量;密集组(平均镜长 < 1.5s)≤ 15s
  blocking 守门:beats[] 节拍单价之和 ≤ 镜长 − 台词 − 余量;节拍间隔 ≥ 0.3s
  prompt   守门:Shot 段顺序连接词数 ≤ blocking 节拍数

台词估时 = ONSET_S + 有效字数 ÷ 语速 + PAUSE_S × 句中停顿数
  语速 = 角色 voice.json speed_cpm 中点 ÷ 60 × 语速档倍率(fast/medium/slow),无角色语速时直接取档位字/秒。
  参数来源:ep07 对白语音库 44 句自然时长拟合 dur = 0.68 + 字数/4.8 + 0.41×停顿(MAE 0.41s;旧公式 0.9s)。
  旧公式(字数 ÷ 语速,无余量)只给 legacy 文件做一致性比对,新写的集一律用新公式。
动作估时 = 节拍单价表(按动作关键词取最大匹配,缺省 0.7s;停住/保持类 0.3s)。
"""
from __future__ import annotations

import re
import unicodedata

CUTOFF_LEGACY = "2026-10-03"          # 产物日期早于此 = 存量,只 WARN、估时沿用旧公式比对

# ---------------------------------------------------------------- 台词
ONSET_S = 0.7                          # 句首起声 + 句尾收声
PAUSE_S = 0.4                          # 句中每个停顿标点
PACE_RATE = {"fast": 5.0, "medium": 4.2, "slow": 3.4}        # 字/秒(无角色语速时)
PACE_FACTOR = {"fast": 1.19, "medium": 1.0, "slow": 0.81}    # 有角色语速时按倍率(4.2 × 1.19 ≈ 5.0,× 0.81 ≈ 3.4)
DEFAULT_CPM = 252.0                    # 无语速设定且无档位:4.2 字/秒
PRE_SPEECH_S = 0.4                     # 对白镜:开口前余量
POST_SPEECH_S = 0.3                    # 对白镜:说完后余量
SHOT_MARGIN_S = 0.2                    # 动作镜:首尾各留
MIN_LINE_S = 0.6                       # 再短模型出不稳

_EFF_RE = re.compile(r"[0-9A-Za-z぀-ヿ㐀-䶿一-鿿豈-﫿가-힯]")
_PAUSE_RE = re.compile(r"[,,、;;:：。.!!??—…]+")
_END_RE = re.compile(r"[。.!!??—…]+\s*$")

# 情绪标签 → 语速档猜测(只在对白层没写 pace 时兜底;dialogue-rewrite 写了 pace 一律以它为准)
_SLOW_WORDS = ("哭", "泣", "哀", "恳求", "乞", "低语", "喃", "沉吟", "迟疑", "犹豫", "喘", "虚弱", "弥留", "追忆",
               "温柔", "慢", "压着", "冷", "念出", "叹", "whisper", "plead", "sob", "weak", "slow", "cold", "murmur")
_FAST_WORDS = ("急", "喝", "斥", "吼", "叫", "喊", "惊", "催", "怒", "慌", "抢", "快", "急报", "下令",
               "shout", "yell", "urgent", "panic", "bark", "snap", "fast", "rush")


def eff_chars(text: str) -> int:
    """有效字符数:汉字/假名/谚文 + 拉丁字母数字,标点空白不计(与 check_dialogue_fit 同口径)。"""
    return len(_EFF_RE.findall(unicodedata.normalize("NFKC", str(text or ""))))


def inner_pauses(text: str) -> int:
    """句中停顿数:标点串个数,不算句末那一处。"""
    s = str(text or "").strip()
    marks = _PAUSE_RE.findall(s)
    return max(0, len(marks) - (1 if _END_RE.search(s) else 0))


def norm_pace(pace) -> str:
    p = str(pace or "").strip().lower()
    return p if p in PACE_RATE else ""


def guess_pace(emotion: str) -> str:
    """情绪标签 → fast/slow;认不出返回空(= medium)。"""
    e = str(emotion or "").lower()
    if not e:
        return ""
    if any(w in e for w in _FAST_WORDS):
        return "fast"
    if any(w in e for w in _SLOW_WORDS):
        return "slow"
    return ""


def speech_rate(pace: str = "", cpm: float | None = None) -> float:
    """字/秒:有角色语速按档位倍率,没有按档位固定值。"""
    p = norm_pace(pace) or "medium"
    if cpm and cpm > 0:
        return cpm / 60.0 * PACE_FACTOR[p]
    return PACE_RATE[p]


def line_est(text: str, pace: str = "", cpm: float | None = None) -> float:
    """新口径台词估时(秒,两位小数)。"""
    n = eff_chars(text)
    if n <= 0:
        return 0.0
    return round(max(MIN_LINE_S, ONSET_S + n / speech_rate(pace, cpm) + PAUSE_S * inner_pauses(text)), 2)


def line_est_legacy(text: str, cpm: float | None = None) -> float:
    """旧口径(2026-08-30 check_dialogue_fit):字数 ÷ 语速,一位小数,无余量——只用于存量文件的一致性比对。"""
    return round(eff_chars(text) / ((cpm or DEFAULT_CPM) / 60.0), 1)


def dialogue_need(lines: list[dict], legacy: bool = False) -> float:
    """一镜/一场的台词总需求:Σ估时 + 每句开口前后余量。lines: [{text, pace?, cpm?, est?}]。"""
    total = 0.0
    for ln in lines or []:
        text = str(ln.get("text") or "")
        if not text:
            continue
        est = ln.get("est")
        if est is None:
            est = line_est_legacy(text, ln.get("cpm")) if legacy else line_est(text, ln.get("pace") or "", ln.get("cpm"))
        total += float(est) + PRE_SPEECH_S + POST_SPEECH_S
    return round(total, 2)


# ---------------------------------------------------------------- 动作
DEFAULT_BEAT_S = 0.7
DESC_CLAUSE_S = 0.25                   # 散文里没有动作词的短句(描写/状态),按描写计
HOLD_BEAT_S = 0.3
MIN_BEAT_GAP_S = 0.3                   # blocking 同一人物相邻节拍最小间隔

# (类型, 秒, 关键词…) —— 一个节拍命中多类取最大秒数;关键词按子串匹配(中英都认)
BEAT_COSTS: tuple[tuple[str, float, tuple[str, ...]], ...] = (
    ("hold", HOLD_BEAT_S, ("话落", "镜首", "镜尾", "镜末", "停住", "不动", "站定", "保持", "原地", "停在", "钉在",
                            "hold", "stays", "remains", "freeze", "static")),
    ("glance", 0.5, ("转头", "回头", "看向", "望向", "目光", "视线", "瞥", "眼帘", "抬眼", "低头", "抬头", "点头", "摇头",
                     "glance", "look", "gaze", "nod", "turns head", "eyes")),
    ("face", 0.4, ("皱眉", "眉头", "眉心", "眯眼", "嘴角", "咬牙", "下颌", "鼻翼", "挑眉", "冷笑", "讥笑", "一笑",
                   "brow", "smirk", "frown", "lips", "jaw", "sneer")),
    ("hand", 0.5, ("抬手", "举手", "伸手", "摊手", "指", "捏诀", "按剑", "握", "松手", "攥", "拂",
                   "raise", "reach", "hand", "grip", "point", "fist")),
    ("turn", 0.7, ("转身", "侧身", "回身", "turn around", "turns", "pivot")),
    ("step", 0.8, ("走", "步", "迈", "上前", "退", "蹭退", "跨", "踏", "挪", "walk", "step", "pace", "back off", "retreat")),
    ("run", 1.0, ("跑", "奔", "冲", "追", "扑", "run", "dash", "charge", "chase", "lunge")),
    ("throw", 0.7, ("掷", "扔", "甩", "抛", "射", "挥", "劈", "刺", "砍", "斩", "递", "抖", "掷出",
                    "throw", "hurl", "swing", "slash", "stab", "thrust", "fling")),
    ("catch", 0.5, ("接住", "接圈", "截", "抓", "夺", "catch", "grab", "snatch", "seize")),
    ("fall", 1.2, ("撞", "摔", "倒", "跌", "滑倒", "跪倒", "瘫", "蜷", "crash", "fall", "slam", "collapse", "slide down", "curl")),
    ("rise", 1.5, ("起身", "站起", "爬起", "坐起", "stand up", "rise", "gets up")),
    ("sit", 1.0, ("坐下", "蹲下", "跪下", "伏下", "sit", "crouch", "kneel")),
    ("draw", 0.8, ("拔剑", "出鞘", "抽剑", "收剑", "收进袖", "收圈", "解下", "系", "draw", "sheathe", "unties", "tuck")),
    ("fx", 1.0, ("亮起", "熄灭", "腾起", "化火", "光芒", "光弧", "遁光", "剑光", "符纹", "烟", "雾",
                 "glow", "lights up", "flare", "fades out", "burst", "shimmer")),
    ("drop", 0.8, ("落下", "坠", "碎", "溅", "掉", "滚落", "drop", "shatter", "splash", "tumble")),
    ("leap", 1.0, ("纵身", "跃", "跳", "飞", "腾空", "遁", "掠", "leap", "jump", "fly", "vault", "soar")),
    ("speak", 0.6, ("开口", "说", "喝", "喊", "笑", "哭", "吼", "叹", "speak", "says", "shout", "laugh", "cry")),
)
_SPLIT_RE = re.compile(r"[。;;!!??\n]+|[,,、]+|——|—|…+|\s+(?:then|and then|next|after that)\s+", re.I)
_CONNECT_WORDS = ("先", "随后", "接着", "然后", "再", "之后", "紧接着", "最后", "继而", "一边", "同时", "跟着", "顺势", "随即")
_META_LINE_RE = re.compile(r"^\s*(?:[-*]\s*)?(拍|出场|事件|对白装载|动作段|时长|转场|时段|声音|音效|音乐|BEAT|CAST|EVENTS|DURATION)\s*([:：]|\s)")
# 场头说明 bullet(#106):键名后可带一段括注,须紧跟冒号——「空间:…」「场地(ab08-1 ①):…」「三级尺度(设计风格 §5):…」
_META_NOTE_RE = re.compile(r"^\s*(?:[-*]\s*)?\**(空间|场地|环境|方位|三级尺度|内容处理|生成分段|画外角色|光照|天气|备注|说明)\**"
                           r"\s*(?:[（(][^）)\n]*[）)])?\s*[:：]")
_BEAT_TAG_RE = re.compile(r"【[^】]{1,12}】|\[BEAT:[^\]]*\]", re.I)
_SEQ_RE = re.compile(r"随后|接着|然后|紧接着|继而|之后|跟着|顺势|随即|最后|再(?=[^次]|$)|先(?=[^生前后头])"
                     r"|\bthen\b|\bnext\b|\bafter that\b|\bfollowed by\b|\bfinally\b|\bafterwards\b", re.I)
_LINE_SYNC_RE = re.compile(r"[『「“\"]|台词|line|dialog", re.I)


def beat_cost(action: str) -> tuple[float, str]:
    """一个节拍的最短用时与类型:命中多类取最大;没命中按缺省。"""
    a = str(action or "").strip().lower()
    if not a:
        return 0.0, ""
    best, kind = 0.0, ""
    for name, secs, words in BEAT_COSTS:
        if any(w in a for w in words):
            if secs > best:
                best, kind = secs, name
    return (best if kind else DEFAULT_BEAT_S), (kind or "default")


def split_beats(text: str) -> list[str]:
    """动作文本 → 节拍短句(按句读/逗号/破折号切,去节拍标签与元信息行)。"""
    t = _BEAT_TAG_RE.sub(" ", str(text or ""))
    out = []
    for part in _SPLIT_RE.split(t):
        p = (part or "").strip(" \t-–·:：()（）「」『』")
        if eff_chars(p) >= 2:
            out.append(p)
    return out


def action_cost(text: str, prose: bool = False) -> tuple[float, list[dict]]:
    """一段动作描写的总需求与逐拍明细。
    prose=True(剧本动作行 / 分镜 content 这类散文):没有动作词的短句按描写计 DESC_CLAUSE_S,
    相邻同类动作短句(「手一抖」「乾坤圈掷出」)并成一拍取最大;prose=False(poses.action 这类动作短语)每句都是一拍。"""
    beats = []
    total = 0.0
    for b in split_beats(text):
        s, kind = beat_cost(b)
        if prose and kind == "default":
            s = DESC_CLAUSE_S
        if prose and beats and kind != "default" and beats[-1]["kind"] == kind:
            prev = beats[-1]
            total -= prev["cost_s"]
            prev["beat"] = prev["beat"] + "/" + b
            prev["cost_s"] = max(prev["cost_s"], s)
            total += prev["cost_s"]
            continue
        beats.append({"beat": b, "cost_s": s, "kind": kind})
        total += s
    return round(total, 2), beats


def is_meta_action_line(line: str) -> bool:
    """剧本场内的说明性 bullet(拍:/出场:/对白装载:/空间:/场地(…):…)不算动作。
    含「≤」的行多是装载/时长核算,也算说明;但「≤」只出现在节拍标签里的动作行(「【闪回①·≤4s】他转身…」)照常计时(#106)。"""
    s = str(line or "")
    if _META_LINE_RE.match(s) or _META_NOTE_RE.match(s):
        return True
    return "≤" in _BEAT_TAG_RE.sub("", s)


def count_sequence_connectors(text: str) -> int:
    """Shot 段里的顺序连接词个数(先/随后/接着/然后/再…/then/next…)。"""
    return len(_SEQ_RE.findall(str(text or "")))


def is_line_synced(beat: dict) -> bool:
    """blocking 节拍挂在台词上(sync 引台词)= 与说话同时发生,不另占时间。"""
    return bool(_LINE_SYNC_RE.search(str((beat or {}).get("sync") or "")))


def blocking_beats(blocking: dict) -> list[dict]:
    """blocking.json → 占时间的节拍 [{char, t, action, cost_s, kind}](排除挂台词的与纯保持)。"""
    out = []
    for ch in (blocking or {}).get("characters") or []:
        cid = ch.get("id") or ch.get("label") or "?"
        for bt in ch.get("beats") or []:
            if not isinstance(bt, dict):
                continue
            action = str(bt.get("action") or "").strip()
            if not action:
                continue
            cost, kind = beat_cost(action)
            synced = is_line_synced(bt)
            out.append({"char": cid, "t": bt.get("t"), "action": action, "cost_s": 0.0 if synced else cost,
                        "kind": "synced" if synced else kind})
    return out


def beat_spacing_issues(blocking: dict, min_gap: float = MIN_BEAT_GAP_S) -> list[str]:
    """同一人物相邻两拍 t 间隔 < min_gap 的,逐条列出。"""
    issues = []
    for ch in (blocking or {}).get("characters") or []:
        cid = ch.get("id") or ch.get("label") or "?"
        ts = []
        for bt in ch.get("beats") or []:
            if isinstance(bt, dict) and str(bt.get("action") or "").strip() and not is_line_synced(bt):
                try:
                    ts.append((float(bt.get("t")), str(bt.get("action"))[:16]))
                except (TypeError, ValueError):
                    pass
        ts.sort()
        for (t0, a0), (t1, a1) in zip(ts, ts[1:]):
            if t1 - t0 < min_gap - 1e-9:
                issues.append(f"{cid} t={t0:g}「{a0}」→ t={t1:g}「{a1}」间隔 {t1 - t0:.2f}s < {min_gap}s")
    return issues


# ---------------------------------------------------------------- 镜 / 组
DENSE_ASL_S = 1.5                      # 平均镜长低于此 = 密集组
DENSE_GROUP_MAX_S = 15.0               # 密集组总时长上限(用户 2026-10-02 拍板)


def shot_need(lines: list[dict], actions: list[str] | None = None, beat_costs: list[float] | None = None,
              legacy: bool = False, prose: bool = False) -> dict:
    """一镜的时间需求:台词(含开口前后余量)+ 动作节拍 + 动作镜首尾余量。
    actions = 动作文本(分镜层 poses.action / content);beat_costs = 已算好的节拍单价(blocking 层)。"""
    dlg = dialogue_need(lines, legacy)
    act = 0.0
    beats = []
    if beat_costs is not None:
        act = round(sum(float(c) for c in beat_costs), 2)
    else:
        for a in actions or []:
            s, bs = action_cost(a, prose=prose)
            act += s
            beats.extend(bs)
    margin = 0.0 if lines and any(str(l.get("text") or "") for l in lines) else (SHOT_MARGIN_S * 2 if act > 0 else 0.0)
    return {"dialogue_s": dlg, "action_s": round(act, 2), "margin_s": margin,
            "need_s": round(dlg + act + margin, 2), "beats": beats}


def group_density_issue(durations: list[float], total: float | None = None,
                        max_s: float = DENSE_GROUP_MAX_S, asl: float = DENSE_ASL_S) -> str | None:
    """密集组(平均镜长 < asl)总时长 > max_s 时返回原因,否则 None。"""
    ds = [float(d) for d in durations or [] if d is not None]
    if len(ds) < 2:
        return None
    tot = float(total) if total is not None else sum(ds)
    mean = sum(ds) / len(ds)
    if mean < asl and tot > max_s + 1e-9:
        return f"平均镜长 {mean:.2f}s < {asl:g}s 的密集组总时长 {tot:g}s > {max_s:g}s(按节拍链拆组)"
    return None


# ---------------------------------------------------------------- 预算
DEFAULT_EXTEND_PCT = 50.0


def extend_pct(settings: dict | None) -> float:
    """项目设置「视频节奏 → 可加长单集」百分比(默认 50)。"""
    try:
        v = float(((settings or {}).get("duration") or {}).get("episode_extend_pct", DEFAULT_EXTEND_PCT))
        return max(0.0, v)
    except (TypeError, ValueError):
        return DEFAULT_EXTEND_PCT


def budget_plan(base_budget_s: float, need_s: float, pct: float) -> dict:
    """超预算三步(用户 2026-10-02 拍板:加长单集 > 精简台词 > 合并动作短镜)。
    返回 {status: ok|extend|over_cap, cap_s, recommended_budget_s, extension_pct, need_cut_s}。"""
    base = float(base_budget_s or 0)
    cap = round(base * (1 + pct / 100.0), 1)
    need = float(need_s or 0)
    if base <= 0:
        return {"status": "no_budget", "cap_s": None, "recommended_budget_s": None, "extension_pct": None, "need_cut_s": 0.0}
    if need <= base + 1e-9:
        return {"status": "ok", "cap_s": cap, "recommended_budget_s": base, "extension_pct": 0.0, "need_cut_s": 0.0}
    if need <= cap + 1e-9:
        rec = float(int(need + 0.999))
        return {"status": "extend", "cap_s": cap, "recommended_budget_s": rec,
                "extension_pct": round((rec / base - 1) * 100, 1), "need_cut_s": 0.0}
    return {"status": "over_cap", "cap_s": cap, "recommended_budget_s": cap,
            "extension_pct": round(pct, 1), "need_cut_s": round(need - cap, 1)}


# ---------------------------------------------------------------- 角色语速
def character_cpm(base) -> dict[str, float]:
    """{CHAR-id: speed_cpm 中点}(bible/characters/<id>/voice.json;数值 / [lo,hi] / {min,max} 都认)。"""
    import json
    from pathlib import Path
    out: dict[str, float] = {}
    cdir = Path(base) / "bible" / "characters"
    if not cdir.is_dir():
        return out
    for vj in sorted(cdir.glob("*/voice.json")):
        try:
            v = json.loads(vj.read_text(encoding="utf-8"))
        except Exception:
            continue
        sp = v.get("speed_cpm")
        if sp is None and isinstance(v.get("speech_rate"), dict):
            sp = v["speech_rate"].get("cpm")
        cpm = None
        try:
            if isinstance(sp, (int, float)):
                cpm = float(sp)
            elif isinstance(sp, (list, tuple)) and len(sp) >= 2:
                cpm = (float(sp[0]) + float(sp[1])) / 2
            elif isinstance(sp, dict) and sp.get("min") is not None and sp.get("max") is not None:
                cpm = (float(sp["min"]) + float(sp["max"])) / 2
        except (TypeError, ValueError):
            cpm = None
        if cpm and cpm > 0:
            out[vj.parent.name] = cpm
    return out


# ---------------------------------------------------------------- 存量判定
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def is_legacy(date_like) -> bool:
    """产物日期(任意含 YYYY-MM-DD 的字符串)早于 CUTOFF_LEGACY 或缺失 → 存量。"""
    m = _DATE_RE.search(str(date_like or ""))
    return (m.group(1) < CUTOFF_LEGACY) if m else True


def doc_date(obj) -> str:
    """从 JSON 顶层 / _meta 里找产物日期(generated_at / written_at / date / synced_at)。"""
    if not isinstance(obj, dict):
        return ""
    for src in (obj, obj.get("_meta") or {}):
        for k in ("generated_at", "written_at", "date", "updated_at", "synced_at", "frozen_at"):
            v = src.get(k) if isinstance(src, dict) else None
            if v and _DATE_RE.search(str(v)):
                return str(v)
    return ""


def frontmatter_date(md_text: str) -> str:
    m = re.search(r"^generated_at:\s*(\S+)", str(md_text or ""), re.M)
    return m.group(1) if m else ""
