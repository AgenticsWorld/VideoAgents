#!/usr/bin/env python3
"""check_dialogue_fit.py — 对白时长适配机检 dialogue_fit(WORKFLOW.md §7D ①,2026-08-30)+ 精简目标清单。

背景:对白在 Phase 5 写完,镜头时长在 Phase 6 分镜/定稿镜头表时才确定——台词长度必然漂移。
组总时长是视频生成硬约束(±1s),`{}` 台词由模型原生合成,装不下就赶词提速、只能整组重 roll。
本脚本把原先「由 shot-planning 自查」的估时级机检落成宿主 CLI,并给 dialogue-rewrite 产出逐句精简目标,
作为分镜定稿后的固定环节 p6-dialogue-fit(检查 → 超限精简 → 复检,H3A 签字前必 PASS)。

估时口径(2026-10-03 起走时间尺 modules/time_cost.py,docs/time_cost.md):
  est_duration_s = 0.7s 起止余量 + 有效字符数 ÷ 语速 + 0.4s × 句中停顿数
  语速 = 该说话角色 bible/characters/<CHAR>/voice.json#speed_cpm 中点 ÷ 60 × 语速档倍率(对白行 `pace: fast|medium|slow`,
  由 dialogue-rewrite 随情绪写;没写按情绪标签猜,猜不出 medium);
  有效字符 = 汉字/假名/谚文 + 拉丁字母数字(每字符 1),标点、空白、括注不计;
  无 voice.json / 无 speed_cpm 的说话人(群演等):有记录 est_duration_s 则信记录值,否则按本项目各角色语速中点的中位数估
  (项目无 voice.json 时 240);一律报 WARN speaker_speed_unknown,不参与 line_est_consistent。
  存量(剧本 generated_at / shot_list 日期早于 2026-10-03)沿用旧公式「字数 ÷ 语速」比对,镜级只 WARN。

机检项(FAIL 退出码 1;WARN 不影响退出码):
  1. dialogue_fit_group     每个 audio_plan=dialogue(或 has_dialogue)组:Σ台词估时 ≤ total_duration_s × ratio(默认 0.7)   FAIL
  2. dialogue_fit_shot      每镜:Σ本镜台词估时 + 开口前 0.4s + 说完后 0.3s ≤ duration_s(新集 FAIL;存量按旧口径
                            Σ估时 ≤ duration_s × shot_ratio FAIL、> 0.85 WARN)
  3. line_le_cap            单句估时 ≤ settings.json#duration.shot_max_s × ratio(缺省 shot_max 10s)——Phase 5 line_duration_fits 同口径  FAIL
  4. line_est_consistent    shot_list / screenplay 记录的 est_duration_s 与按文本+语速重算值一致(容差 max(0.25s, 10%))——
                            台词改短后没同步估时 = 闸门失效                                                                  FAIL
  5. lines_text_match_source  shot_list 每句台词(说话人+有效字符)能在 screenplay.md 对白层(退回 dialogue.md)找到——
                            改稿只改了一边 = 生成时念的不是定稿                                                              FAIL
  6. source_lines_covered   screenplay 对白层每句都落在某镜 dialogue_lines(漏排的台词)                                     WARN
  7. speaker_speed_unknown  说话人无语速设定                                                                                  WARN
  8. placement_valid        声画分离(2026-10-03,docs/sound_split.md):项目 output.sound_split=off 时 shot_list 不得有 os/vo 句;
                            script_only 时分镜层不得把剧本画内句自行转画外                                                   FAIL
  9. placement_matches_source  剧本标了 (O.S.)/(V.O.) 的句分镜不得改回画内;剧本画内句转画外须写 placement_source
                            (directing|user)+placement_reason.trigger                                                        FAIL
 10. audio_plan_mismatch    组 audio_plan 与台词事实不符(有画内句=dialogue;只有画外句/旁白挂点=voice_over;皆无=ambient_only)   WARN
  画外 / V.O. 句(placement=os|vo)不占说话人嘴时间:1/2 项只累计画内句;其窗口由 offscreen_lines 的 offscreen_fit 另查。

产出:directing/epNN/dialogue_fit.json(--report 改路径,--no-report 不写):逐组/逐镜/逐句估时、超限量、
`trim_targets[]`(每个超限组:需削减秒数、按比例分摊到各句的 target_chars,供 dialogue-rewrite 逐句精简)。

--write-est:按文本+语速重算并回写 est_duration_s(shot_list.json 的 dialogue_lines/dialogue_est_s/dialogue_ratio/
  dialogue_capacity_s/dialogue_est_total_s;screenplay.md 与 dialogue.md 对白行 `{… est_duration_s: X …}` 只改数字),
  供改短台词后同步估时;回写后仍以本脚本复检为准。
无 shot_list.json 时自动退化为「仅剧本层」模式(3/4/7 项;Phase 5 dialogue-rewrite 交付自检可用)。

用法:python3 code/check_dialogue_fit.py --project <slug> --ep ep01              # 全批机检 + 写报告
     python3 code/check_dialogue_fit.py --project <slug> --ep ep01 grp003 grp007  # 只查指定组
     python3 code/check_dialogue_fit.py --project <slug> --ep ep01 --write-est    # 重算并回写估时后机检
     python3 code/check_dialogue_fit.py --project <slug> --ep ep01 --ratio 0.7 --shot-ratio 1.0 --strict
退出码:0=通过(可含 WARN),1=有 FAIL(逐条打印);--strict 时 WARN 也算 FAIL。
"""
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules.dialogue_tts import name_index, resolve_speaker  # noqa: E402  与逐句语音库同一套说话人解析(#58)
from modules.entity_ids import CHAR_ID_PAT, extract_actor_id, is_actor_id, known_actor_ids  # noqa: E402  编号口径(#117)
from modules import time_cost as tc  # noqa: E402  时间尺(2026-10-03)
try:
    from modules import offscreen_lines as osl  # noqa: E402  声画分离(2026-10-03):placement on|os|vo
except Exception:  # pragma: no cover  模块缺失时全部台词视同画内
    osl = None

GROUP_RATIO = 0.7          # §7D ①:Σ台词估时 ≤ 组总时长 × 0.7
SHOT_RATIO = 1.0           # 镜级:物理装不下即 FAIL
SHOT_TIGHT_RATIO = 0.85    # 镜级:超此比例 WARN
DEFAULT_CPM = 240.0        # 无语速设定的说话人
DEFAULT_SHOT_MAX_S = 10.0   # settings.json 缺 duration.shot_max_s 时
EST_TOL_S = 0.25           # 估时一致性绝对容差
EST_TOL_REL = 0.10         # 估时一致性相对容差

_EFF_RE = re.compile(r"[0-9A-Za-z぀-ヿ㐀-䶿一-鿿豈-﫿가-힯]")
_META_RE = re.compile(r"\s*\{[^{}]*\}\s*$")
_EST_RE = re.compile(r"(est_duration_s\s*[:：]\s*)([0-9]+(?:\.[0-9]+)?)")
_PACE_RE = re.compile(r"\bpace\s*[:：]\s*(fast|medium|slow)\b", re.I)
_EMO_RE = re.compile(r"emotion\s*[:：]\s*([^,，}]+)")
# screenplay 对白行:- **老道儿(CHAR-0002)**(括注):台词 {emotion: …, est_duration_s: 1.6, …}
_SP_LINE_RE = re.compile(r"^\s*[-*]\s*\*\*(?P<who>[^*]+?)\*\*\s*(?P<paren>(?:[(（][^()（）]*[)）]\s*)*)[:：]\s*(?P<body>.+?)\s*$")
# dialogue.md 编号形态(与 services/runtime/core.py _dialogue_index 同口径)
_DLG_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+$")
_DLG_TEXT_COL = ("台词", "对白", "定稿", "line", "text")
_DLG_SPK_COL = ("说话人", "角色", "speaker", "char")


def eff_chars(text: str) -> int:
    """有效字符数:汉字/假名/谚文 + 拉丁字母数字,标点空白不计(与 dialogue.md 估时口径一致)。"""
    return len(_EFF_RE.findall(unicodedata.normalize("NFKC", text or "")))


def norm_key(text: str) -> str:
    return "".join(_EFF_RE.findall(unicodedata.normalize("NFKC", text or ""))).lower()


def strip_meta(body: str) -> str:
    return _META_RE.sub("", body or "").strip()


def meta_est(body: str):
    m = _EST_RE.search(body or "")
    return float(m.group(2)) if m else None


def recorded_est(v):
    """记录的估时 → 正数秒;缺失 / 非数字 / ≤0(初稿占位 0)一律视为没有记录(#127)。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def meta_pace(body: str) -> str:
    """对白行花括号元信息里的 `pace: fast|medium|slow`;没写按 emotion 猜(猜不出返回空 = medium)。"""
    m = _PACE_RE.search(body or "")
    if m:
        return m.group(1).lower()
    e = _EMO_RE.search(body or "")
    return tc.guess_pace(e.group(1)) if e else ""


_VO_MARK_RE = re.compile(r"V\.\s?O\.|画外音|内心|voice[- ]?over", re.I)
_OS_MARK_RE = re.compile(r"O\.\s?[SC]\.|画外|off[- ]?screen", re.I)
_PLACEMENT_SOURCES = ("script", "directing", "user")


def source_placement(who: str, paren: str = "") -> str:
    """剧本对白行说话人/括注里的画外标记 → placement(2026-10-03 声画分离):
    `(V.O.)` / 画外音 / 内心 → vo;`(O.S.)` / `(O.C.)` / 画外 → os;其余 on。先判 vo(「画外音」含「画外」)。"""
    s = f"{who or ''} {paren or ''}"
    if _VO_MARK_RE.search(s):
        return "vo"
    if _OS_MARK_RE.search(s):
        return "os"
    return "on"


def line_placement(ln: dict) -> str:
    """shot_list 对白行 placement(缺省/非法 = on);有 offscreen_lines 模块时按它归一。"""
    if osl is not None:
        return osl.placement(ln)
    p = str((ln or {}).get("placement") or "").strip().lower()
    return p if p in ("on", "os", "vo") else "on"


def sound_split_mode(proj_root: Path) -> str:
    """项目输出设置 output.sound_split ∈ off|script_only|auto(默认 off)。"""
    if osl is not None:
        try:
            return osl.mode(proj_root)
        except Exception:
            pass
    try:
        st = json.loads((Path(proj_root) / "settings.json").read_text())
        m = (st.get("output") or {}).get("sound_split")
    except Exception:
        m = None
    return m if m in ("off", "script_only", "auto") else "off"


def speaker_id(who: str, names: dict | None = None, known: frozenset | None = None):
    """说话人文本 → CHAR 编号;抓不到编号时按规范名/别名表反查(names,#58),仍无返回去括注的名字。
    编号口径见 modules/entity_ids(#117):数字编号只取到数字段(`CHAR-0093-v1` → CHAR-0093),
    slug 编号按 known(项目已登记编号)取最长已知前缀。"""
    cid = extract_actor_id(who, known, char_only=True)
    if cid:
        return cid
    name = re.sub(r"[〔【(\[（].*$", "", who or "").strip() or None
    if names:
        return names.get((who or "").strip()) or names.get(name or "") or name
    return name


# ---------------------------------------------------------------- 语速
def load_speeds(proj_root: Path) -> dict:
    """{CHAR-id: cpm 中点};voice.json#speed_cpm 支持数值 / [lo,hi] / {min,max}。"""
    out = {}
    cdir = proj_root / "bible" / "characters"
    if not cdir.is_dir():
        return out
    for vj in sorted(cdir.glob("*/voice.json")):
        try:
            v = json.loads(vj.read_text())
        except Exception:
            continue
        sp = v.get("speed_cpm")
        if sp is None:
            sp = (v.get("speech_rate") or {}).get("cpm") if isinstance(v.get("speech_rate"), dict) else None
        cpm = None
        if isinstance(sp, (int, float)):
            cpm = float(sp)
        elif isinstance(sp, (list, tuple)) and len(sp) >= 2:
            try:
                cpm = (float(sp[0]) + float(sp[1])) / 2
            except (TypeError, ValueError):
                cpm = None
        elif isinstance(sp, dict):
            lo, hi = sp.get("min"), sp.get("max")
            if lo is not None and hi is not None:
                cpm = (float(lo) + float(hi)) / 2
        if cpm and cpm > 0:
            out[vj.parent.name] = cpm
    return out


def est_seconds(text: str, cpm: float, pace: str = "", legacy: bool = False) -> float:
    """台词估时:新口径走时间尺(起止余量 + 字数 ÷ 语速档 + 停顿);legacy=True 沿用旧公式(字数 ÷ 语速)。"""
    if legacy:
        return tc.line_est_legacy(text, cpm)
    return tc.line_est(text, pace, cpm)


# ---------------------------------------------------------------- 剧本对白层
def parse_screenplay_lines(md_text: str, known: frozenset | None = None) -> list:
    """screenplay.md 【对白】小节内的对白行 → [{scene, speaker, who, text, est_recorded, pace, placement, lineno}]。"""
    # 对白行可能出现在【对白】小节,也可能穿插在【画面/动作】小节里(前科:dzg5 ep01 S02 L76);
    # 只排除【旁白】小节(旁白者文本),其余带 `- **谁**:` 形态且能定位说话人的行都算对白。
    # 2026-10-03 声画分离:人物的 (V.O.) / (O.S.) 句不再剔除,记 placement=vo/os(下游后期合成,仍须与 shot_list 对得上)。
    out, scene, in_narr = [], None, False
    for i, raw in enumerate(md_text.splitlines(), 1):
        s = raw.strip()
        if s.startswith("## "):
            scene = s[3:].split("|")[0].strip()
            in_narr = False
            continue
        if s.startswith("### ") or s.startswith("#### "):
            in_narr = "旁白" in s or "narration" in s.lower() or "narrator" in s.lower()
            continue
        if in_narr:
            continue
        m = _SP_LINE_RE.match(raw)
        if not m:
            continue
        who = m.group("who").strip()
        if re.search(r"旁白候选|NARRATION|narrator", who, re.I) or re.search(r"旁白|narrat", m.group("paren") or "", re.I):
            continue                                   # 旁白候选 / 旁白者:不是人物台词
        body = m.group("body")
        text = strip_meta(body)
        if not text:
            continue
        out.append({"scene": scene, "speaker": speaker_id(who, known=known), "who": who,
                    "text": text, "est_recorded": meta_est(body), "pace": meta_pace(body),
                    "placement": source_placement(who, m.group("paren")), "lineno": i})
    return out


def parse_dialogue_md(md_text: str, known: frozenset | None = None) -> tuple:
    """dialogue.md → (编号索引 {id: {speaker,text}}, 无编号对白行列表[{speaker,text,est_recorded,lineno}])。"""
    idx, bullets = {}, []

    def _put(did, spk, line):
        line = strip_meta((line or "").strip().strip("*").strip())
        if did and line and did not in idx:
            idx[did] = {"speaker": speaker_id(spk or "", known=known), "text": line}

    text_col = spk_col = None
    cur_id = cur_spk = None
    for i, raw in enumerate(md_text.splitlines(), 1):
        line = raw.strip()
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(re.fullmatch(r":?-+:?", c or "-") for c in cells):
                continue
            if not cells or not _DLG_ID_RE.match(cells[0]):
                low = [c.lower() for c in cells]
                text_col = next((k for k, c in enumerate(low) if any(t in c for t in _DLG_TEXT_COL)
                                 and not any(t in c for t in ("原句", "字数", "编号", "id", "original", "source", "chars", "count", "length", "index", "no."))), None)
                spk_col = next((k for k, c in enumerate(low) if any(t in c for t in _DLG_SPK_COL)), None)
                continue
            if text_col is not None and text_col < len(cells):
                _put(cells[0], cells[spk_col] if spk_col is not None and spk_col < len(cells) else None,
                     cells[text_col])
            continue
        m = re.match(r"^(?:#{1,6}\s*)?\[([A-Za-z][A-Za-z0-9-]+)\]\s*(.*)$", line)
        if m and _DLG_ID_RE.match(m.group(1)):
            rest = m.group(2)
            m2 = re.match(r"^(.*?)[:：]\s*(.+?)(?:\s*\{[^{}]*\})?\s*$", rest)
            if m2 and m2.group(2) and not line.startswith("#"):
                _put(m.group(1), m2.group(1), m2.group(2))
                cur_id = None
            else:
                cur_id, cur_spk = m.group(1), rest
            continue
        if cur_id:
            m3 = re.match(r"^-\s*\*\*(?:定稿|final|final line)\*\*\s*[:：]\s*(.+)$", line)
            if m3:
                _put(cur_id, cur_spk, m3.group(1))
                cur_id = None
            elif line.startswith("#"):
                cur_id = None
            continue
        mb = _SP_LINE_RE.match(raw)
        if mb and not re.search(r"旁白|NARRATION|narrator", mb.group("who"), re.I):
            body = mb.group("body")
            text = strip_meta(body)
            if text:
                bullets.append({"speaker": speaker_id(mb.group("who"), known=known), "who": mb.group("who").strip(),
                                "text": text, "est_recorded": meta_est(body), "pace": meta_pace(body),
                                "placement": source_placement(mb.group("who"), mb.group("paren")), "lineno": i})
    return idx, bullets


# ---------------------------------------------------------------- shot_list 台词
def shot_lines(shot: dict, dlg_idx: dict, names: dict | None = None, known: frozenset | None = None) -> list:
    """一镜的台词 [{speaker, speaker_name, text, est_recorded, ref}];内嵌 dialogue_lines 优先,
    其次编号 dialogue_refs/dialogue_ref 回查 dialogue.md。
    说话人按 modules.dialogue_tts.resolve_speaker 同一口径(#58):speaker/char 中的编号 → speaker_char /
    character_id → 规范名/别名反查;都没有时保留显示名。speaker_name = 原始显示名(对白层文本比对的兜底键)。"""
    out = []
    lines = shot.get("dialogue_lines")
    if isinstance(lines, list) and lines:
        for raw_i, ln in enumerate(lines):
            if isinstance(ln, dict):
                text = ln.get("text") or ln.get("line") or ""
                if text:
                    sid, raw = resolve_speaker(ln, names or {}, known)
                    pr = ln.get("placement_reason") if isinstance(ln.get("placement_reason"), dict) else {}
                    out.append({"speaker": sid or raw or None, "speaker_name": raw or None, "text": text,
                                "est_recorded": ln.get("est_duration_s"), "ref": ln.get("id") or ln.get("ref"),
                                "pace": tc.norm_pace(ln.get("pace")) or tc.guess_pace(ln.get("emotion") or ln.get("tone") or ""),
                                # 声画分离(2026-10-03):画外/旁白式台词不占说话人嘴时间,下游按 placement 分流
                                "placement": line_placement(ln),
                                "heard_in": [x for x in (ln.get("heard_in") or []) if isinstance(x, str)],
                                "placement_source": str(ln.get("placement_source") or "").strip().lower(),
                                "placement_trigger": str(pr.get("trigger") or "").strip(),
                                "line_index": raw_i})
            elif isinstance(ln, str) and ln.strip():
                mm = re.match(rf"^(?:S\d+/)?({CHAR_ID_PAT})\s*[:：]\s*(.+)$", ln.strip())
                out.append({"speaker": mm.group(1) if mm else None, "text": mm.group(2) if mm else ln.strip(),
                            "est_recorded": None, "ref": None, "placement": "on"})
        return out
    refs = shot.get("dialogue_refs")
    if not refs and shot.get("dialogue_ref"):
        refs = [shot["dialogue_ref"]]
    for r in refs or []:
        if not isinstance(r, str):
            continue
        hit = dlg_idx.get(r.strip())
        if hit:
            out.append({"speaker": hit.get("speaker"), "text": hit["text"], "est_recorded": None, "ref": r})
        else:
            out.append({"speaker": None, "text": "", "est_recorded": None, "ref": r, "unresolved": True})
    return out


# ---------------------------------------------------------------- 主检
def run(proj_root: Path, ep: str, groups_filter=None, ratio=GROUP_RATIO, shot_ratio=SHOT_RATIO,
        write_est=False, strict=False, legacy_override=None):
    ddir = proj_root / "directing" / ep
    sdir = proj_root / "story" / "episodes" / ep
    sl_path = ddir / "shot_list.json"
    sp_path = sdir / "screenplay.md"
    dm_path = sdir / "dialogue.md"
    errs, warns = [], []

    speeds = load_speeds(proj_root)
    names = name_index(proj_root)
    known_ids = known_actor_ids(proj_root)
    try:
        st = json.loads((proj_root / "settings.json").read_text())
        shot_max_s = float((st.get("duration") or {}).get("shot_max_s") or DEFAULT_SHOT_MAX_S)
    except Exception:
        shot_max_s = DEFAULT_SHOT_MAX_S
    line_cap_s = round(shot_max_s * ratio, 1)

    sp_text = sp_path.read_text() if sp_path.exists() else ""
    dm_text = dm_path.read_text() if dm_path.exists() else ""
    # 存量判定(2026-10-03 时间尺):剧本 front matter generated_at / shot_list 日期早于截止 = 旧公式比对、镜级只 WARN
    sl_doc = None
    if sl_path.exists():
        try:
            sl_doc = json.loads(sl_path.read_text())
        except Exception:
            sl_doc = None
    legacy = tc.is_legacy(tc.doc_date(sl_doc) if sl_doc else tc.frontmatter_date(sp_text))
    if legacy_override is not None:
        legacy = legacy_override
    sp_lines = parse_screenplay_lines(sp_text, known_ids) if sp_text else []
    dlg_idx, dm_bullets = parse_dialogue_md(dm_text, known_ids) if dm_text else ({}, [])
    source_name = "screenplay.md" if sp_lines else ("dialogue.md" if dm_bullets or dlg_idx else None)
    source_lines = sp_lines if sp_lines else dm_bullets
    if not source_lines and dlg_idx:
        source_lines = [{"speaker": v["speaker"], "text": v["text"], "est_recorded": None, "lineno": None}
                        for v in dlg_idx.values()]
    # 对白层说话人同样按别名表归一到编号(与 shot_list 侧同口径;认不出的保留名字)
    for ln in source_lines:
        if ln.get("speaker") and not is_actor_id(ln["speaker"], char_only=True):
            ln["speaker"] = names.get(ln["speaker"]) or ln["speaker"]
    if source_name is None:
        warns.append("source_unparsed: 未找到可解析的对白层(screenplay.md 【对白】小节 / dialogue.md),跳过文本一致性核对")

    unknown_speakers = set()
    # 无语速设定的说话人(群演等):取本项目各角色语速中点的中位数,项目无 voice.json 时退 DEFAULT_CPM
    fallback_cpm = round(sorted(speeds.values())[len(speeds) // 2], 1) if speeds else DEFAULT_CPM

    def cpm_of(spk):
        if spk in speeds:
            return speeds[spk]
        unknown_speakers.add(spk or "?")
        return fallback_cpm

    # ---- 剧本层:单句上限 + 记录估时一致性
    src_keys = {}
    src_over_cap = []
    for ln in source_lines:
        spk = ln.get("speaker")
        known = spk in speeds
        cpm = cpm_of(spk)
        est = est_seconds(ln["text"], cpm, ln.get("pace") or "", legacy)
        rec = ln.get("est_recorded")
        if not known and recorded_est(rec):
            est = round(float(rec), 1)   # 无语速设定的说话人:信记录值(初稿占位 0 不算记录,#127)
        ln["est"], ln["cpm"], ln["chars"] = est, cpm, eff_chars(ln["text"])
        src_keys.setdefault((spk, norm_key(ln["text"])), []).append(ln)
        if est > line_cap_s + 1e-9:
            src_over_cap.append(ln)
        if known and rec is not None and abs(rec - est) > max(EST_TOL_S, EST_TOL_REL * est):
            errs.append(f"{source_name}:L{ln.get('lineno')} line_est_consistent: {spk} 「{ln['text'][:24]}」记录 {rec}s,"
                        f"按 {ln['chars']} 字 × {cpm:.0f} cpm 重算 {est}s(--write-est 可回写)")
    for ln in src_over_cap:
        errs.append(f"{source_name}:L{ln.get('lineno')} line_le_cap: {ln.get('speaker')} 「{ln['text'][:30]}」估时 {ln['est']}s"
                    f" > 单句上限 {line_cap_s}s(shot_max {shot_max_s:g}s × {ratio});需拆句或精简")

    report = {
        "generated_by": "code/check_dialogue_fit.py",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "episode": ep, "ratio": ratio, "shot_ratio": shot_ratio, "shot_tight_ratio": SHOT_TIGHT_RATIO,
        "line_cap_s": line_cap_s, "shot_max_s": shot_max_s,
        "est_formula": ("eff_chars / (speed_cpm_mid / 60); eff_chars = CJK + [A-Za-z0-9]  [legacy]" if legacy else
                        f"{tc.ONSET_S} + eff_chars / (speed_cpm_mid / 60 × pace_factor) + {tc.PAUSE_S} × inner_pauses; "
                        f"shot: Σest + {tc.PRE_SPEECH_S} + {tc.POST_SPEECH_S} per line ≤ duration (modules/time_cost.py)"),
        "legacy": legacy, "pre_speech_s": tc.PRE_SPEECH_S, "post_speech_s": tc.POST_SPEECH_S,
        "speed_source": "bible/characters/<CHAR>/voice.json#speed_cpm midpoint", "fallback_cpm": fallback_cpm,
        "dialogue_source": source_name, "source_lines_total": len(source_lines),
        "mode": "full" if sl_path.exists() else "source_only",
        "groups": [], "trim_targets": [], "checks": {}, "summary": {},
    }

    if not sl_path.exists():
        warns.append(f"shot_list_missing: 未找到 {sl_path},仅做剧本层检查(line_le_cap / line_est_consistent)")
        if write_est:
            _write_est_md(sp_path, sp_lines, speeds, legacy=legacy)
            _write_est_md(dm_path, dm_bullets, speeds, legacy=legacy)
        return _finish(report, errs, warns, unknown_speakers, ddir, strict, fallback_cpm)

    sl = sl_doc if sl_doc is not None else json.loads(sl_path.read_text())
    shots = {s.get("shot_id"): s for s in sl.get("shots") or []}
    groups = sl.get("generation_groups") or []
    if groups_filter:
        groups = [g for g in groups if g.get("group_id") in set(groups_filter)]
        if not groups:
            errs.append(f"groups_not_found: {groups_filter}")
            return _finish(report, errs, warns, unknown_speakers, ddir, strict, fallback_cpm)

    covered_keys = set()
    g_checked = g_over = shot_over = shot_tight = lines_total = 0
    est_total = cap_total = 0.0
    # 声画分离(2026-10-03):off = 全部台词画内(shot_list 出现 os/vo 即 FAIL);script_only = 只认剧本层标记;auto = 剧本 + 分镜白名单
    ss_mode = sound_split_mode(proj_root)
    report["sound_split"] = ss_mode
    narr_anchor_groups = set()
    for a in sl.get("narration_anchors") or []:
        if isinstance(a, dict) and a.get("anchor_group"):
            narr_anchor_groups.add(a["anchor_group"])
    for g in groups:
        gid = g.get("group_id")
        is_dlg = (g.get("audio_plan") == "dialogue") or bool(g.get("has_dialogue"))
        total = float(g.get("total_duration_s") or 0)
        cap = round(total * ratio, 1)
        grec = {"group_id": gid, "audio_plan": g.get("audio_plan"), "total_duration_s": total,
                "capacity_s": cap, "est_s": 0.0, "over_s": 0.0, "ratio": 0.0, "status": "skipped", "shots": [], "lines": []}
        gest = 0.0
        g_on = g_off = 0
        for sid in g.get("shots") or []:
            s = shots.get(sid)
            if not s:
                errs.append(f"{gid} shot_missing: 组引用的镜 {sid} 不在 shots[]")
                continue
            dur = float(s.get("duration_s") or 0)
            lines = shot_lines(s, dlg_idx, names, known_ids)
            sest = 0.0
            srec = {"shot_id": sid, "duration_s": dur, "est_s": 0.0, "ratio": 0.0, "status": "ok", "lines": []}
            for ln in lines:
                if ln.get("unresolved"):
                    errs.append(f"{gid}/{sid} lines_text_match_source: 对白编号 {ln.get('ref')} 在 dialogue.md 无法解析")
                    continue
                pl = ln.get("placement") or "on"
                if pl != "on" and ss_mode == "off":
                    errs.append(f"{gid}/{sid} placement_valid: 「{ln['text'][:24]}」placement={pl},但项目「声画分离」已关闭"
                                f"(改回画内或在输出设置开启)")
                    pl = "on"
                spk = speaker_id(ln.get("speaker") or "", names, known_ids)
                known = spk in speeds
                cpm = cpm_of(spk)
                est = est_seconds(ln["text"], cpm, ln.get("pace") or "", legacy)
                if not known and recorded_est(ln.get("est_recorded")):
                    est = round(float(ln["est_recorded"]), 1)   # 无语速设定的说话人:信记录值,只报 WARN(占位 0 不算,#127)
                chars = eff_chars(ln["text"])
                lines_total += 1
                if pl == "on":
                    sest += est          # 画外 / V.O. 句不占说话人嘴时间(窗口由 offscreen_fit 另查)
                    g_on += 1
                else:
                    g_off += 1
                lrec = {"shot_id": sid, "speaker": spk, "text": ln["text"], "chars": chars, "cpm": cpm,
                        "est_s": est, "est_recorded_s": ln.get("est_recorded"), "placement": pl,
                        "line_index": ln.get("line_index")}
                if pl != "on":
                    lrec["heard_in"] = ln.get("heard_in") or [sid]
                srec["lines"].append(lrec)
                grec["lines"].append(lrec)
                key = (spk, norm_key(ln["text"]))
                if source_name and key not in src_keys and ln.get("speaker_name"):
                    # 编号来自 speaker_char 而对白层仍写显示名(别名表未登记):按显示名比对,不新增 FAIL
                    alt_key = (speaker_id(ln["speaker_name"], known=known_ids), key[1])
                    if alt_key in src_keys:
                        key = alt_key
                if source_name:
                    if key in src_keys:
                        covered_keys.add(key)
                        # 画外标记一致性(2026-10-03):剧本标了 (O.S.)/(V.O.) 的句分镜不得改回画内;
                        # 剧本画内句被分镜转画外须注明来源(directing/user)与触发条款 placement_reason.trigger
                        src_pl = src_keys[key][0].get("placement") or "on"
                        psrc = ln.get("placement_source") or ""
                        trig = ln.get("placement_trigger") or ""
                        if not psrc and trig:
                            psrc = "user" if trig.upper().startswith("U") else "directing" if trig.upper().startswith("D") else ""
                        if src_pl != "on" and pl == "on" and ss_mode != "off":
                            errs.append(f"{gid}/{sid} placement_matches_source: 「{ln['text'][:24]}」剧本标记画外({src_pl}),分镜改回画内"
                                        f"(剧本层 (O.S.)/(V.O.) 对下游是绑定的)")
                        elif src_pl == "on" and pl != "on":
                            if ss_mode == "script_only":
                                errs.append(f"{gid}/{sid} placement_valid: 「{ln['text'][:24]}」剧本为画内句,分镜转 {pl};"
                                            f"项目「声画分离=仅剧本标记」不允许分镜层自行转画外")
                            elif psrc not in ("directing", "user") or not trig:
                                errs.append(f"{gid}/{sid} placement_matches_source: 「{ln['text'][:24]}」剧本为画内句,分镜转 {pl} 须写"
                                            f" placement_source(directing|user)+placement_reason.trigger(D1–D5 / U)")
                    else:
                        # 同文不同说话人 → 明确提示说话人错位
                        alt = [k[0] for k in src_keys if k[1] == key[1]]
                        hint = f"(对白层该句说话人为 {alt[0]})" if alt else "(对白层无此句)"
                        errs.append(f"{gid}/{sid} lines_text_match_source: {spk} 「{ln['text'][:30]}」{hint}")
                rec = ln.get("est_recorded")
                if known and rec is not None and abs(float(rec) - est) > max(EST_TOL_S, EST_TOL_REL * est):
                    errs.append(f"{gid}/{sid} line_est_consistent: {spk} 「{ln['text'][:24]}」shot_list 记录 {rec}s,"
                                f"重算 {est}s({chars} 字 × {cpm:.0f} cpm;--write-est 可回写)")
                if est > line_cap_s + 1e-9:
                    errs.append(f"{gid}/{sid} line_le_cap: {spk} 「{ln['text'][:30]}」估时 {est}s > 单句上限 {line_cap_s}s")
            sest = round(sest, 1)
            srec["est_s"] = sest
            srec["ratio"] = round(sest / dur, 3) if dur else None
            n_lines = len([x for x in srec["lines"] if x.get("placement", "on") == "on"])   # 只数画内句
            srec["onscreen_lines"] = n_lines
            margin = round(n_lines * (tc.PRE_SPEECH_S + tc.POST_SPEECH_S), 1) if n_lines else 0.0
            srec["margin_s"] = margin
            if not legacy and dur and n_lines and sest + margin > dur + 1e-9:
                # 新口径:对白镜必须留开口前后余量(fengshen3 ep07 前科:镜长 = 估时,5s 台词被模型整句丢掉)
                srec["status"] = "over"
                shot_over += 1
                errs.append(f"{gid}/{sid} dialogue_fit_shot: Σ台词估时 {sest}s + 开口前后余量 {margin}s = {round(sest + margin, 1)}s"
                            f" > 镜长 {dur:g}s(超 {round(sest + margin - dur, 1)}s;加镜长或精简台词)")
            elif legacy and dur and sest > dur * shot_ratio + 1e-9:
                srec["status"] = "over"
                shot_over += 1
                errs.append(f"{gid}/{sid} dialogue_fit_shot: Σ台词估时 {sest}s > 镜长 {dur:g}s × {shot_ratio}"
                            f"(超 {round(sest - dur * shot_ratio, 1)}s)")
            elif dur and n_lines and sest > dur * SHOT_TIGHT_RATIO + 1e-9:
                srec["status"] = "tight"
                shot_tight += 1
                warns.append(f"{gid}/{sid} dialogue_fit_shot: Σ台词估时 {sest}s 占镜长 {dur:g}s 的 {sest / dur:.0%}(>{SHOT_TIGHT_RATIO:.0%},紧)")
            if write_est and lines and isinstance(s.get("dialogue_lines"), list):
                # 按原数组下标回写(画外句也回写估时——窗口机检要用),不按位置 zip(2026-10-03)
                for lrec in srec["lines"]:
                    li = lrec.get("line_index")
                    if isinstance(li, int) and 0 <= li < len(s["dialogue_lines"]) and isinstance(s["dialogue_lines"][li], dict):
                        s["dialogue_lines"][li]["est_duration_s"] = lrec["est_s"]
                s["dialogue_est_s"] = sest
                if dur:
                    s["dialogue_ratio"] = round(sest / dur, 3)
            grec["shots"].append(srec)
            gest += sest
        gest = round(gest, 1)
        grec["est_s"] = gest
        grec["ratio"] = round(gest / total, 3) if total else None
        grec["onscreen_lines"], grec["offscreen_lines"] = g_on, g_off
        # 组音频形态与台词事实的一致性(2026-10-03):有画内句 = dialogue;只有画外句 / 旁白挂点 = voice_over(旧名 narration_over);
        # 两者皆无 = ambient_only
        expected = None
        if osl is not None and hasattr(osl, "expected_audio_plan"):
            try:
                expected = osl.expected_audio_plan(g, shots, sl.get("narration_anchors") or [])
            except Exception:
                expected = None
        if expected is None:
            expected = "dialogue" if g_on else ("voice_over" if (g_off or gid in narr_anchor_groups) else "ambient_only")
        ap = g.get("audio_plan")
        ap_norm = "voice_over" if ap == "narration_over" else ap
        if ap and ap_norm != expected and not (ap_norm == "voice_over" and expected == "ambient_only"):
            # voice_over 但既无画外句也无挂点:旁白挂点可能在别处登记,不在这里判,留给 check_generation_groups
            warns.append(f"{gid} audio_plan_mismatch: audio_plan={ap},按台词事实应为 {expected}"
                         f"(画内 {g_on} 句 / 画外 {g_off} 句)")
        elif not ap and grec["lines"]:
            warns.append(f"{gid} audio_plan_mismatch: audio_plan 缺失,组内镜带 {len(grec['lines'])} 句台词(应为 {expected})")
        if is_dlg:
            g_checked += 1
            est_total += gest
            cap_total += cap
            grec["over_s"] = round(max(0.0, gest - cap), 1)
            if gest > cap + 1e-9:
                grec["status"] = "over"
                g_over += 1
                errs.append(f"{gid} dialogue_fit_group: Σ台词估时 {gest}s > 组时长 {total:g}s × {ratio} = {cap}s"
                            f"(超 {grec['over_s']}s,{len(grec['lines'])} 句)")
                report["trim_targets"].append(_trim_target(grec))
            else:
                grec["status"] = "ok"
            if write_est:
                g["dialogue_est_s"] = gest
                g["dialogue_capacity_s"] = cap
                if total:
                    g["dialogue_ratio"] = round(gest / total, 3)
        report["groups"].append(grec)

    if source_name and not groups_filter:
        for key, lns in src_keys.items():
            if key not in covered_keys:
                ln = lns[0]
                warns.append(f"{source_name}:L{ln.get('lineno')} source_lines_covered: {key[0]} 「{ln['text'][:30]}」未落到任何镜的 dialogue_lines")

    if write_est:
        if not groups_filter:
            sl["dialogue_est_total_s"] = round(sum(gr["est_s"] for gr in report["groups"]), 1)
        sl_path.write_text(json.dumps(sl, ensure_ascii=False, indent=2) + "\n")
        _write_est_md(sp_path, sp_lines, speeds, legacy=legacy)
        _write_est_md(dm_path, dm_bullets, speeds, legacy=legacy)
        report["write_est"] = True

    report["summary"] = {"groups_total": len(groups), "dialogue_groups_checked": g_checked, "groups_over": g_over,
                         "shots_over": shot_over, "shots_tight": shot_tight, "lines_total": lines_total,
                         "est_total_s": round(est_total, 1), "capacity_total_s": round(cap_total, 1)}
    return _finish(report, errs, warns, unknown_speakers, ddir, strict, fallback_cpm)


def _trim_target(grec: dict) -> dict:
    """超限组的精简目标:需削减秒数按各句估时占比分摊 → 每句 target_chars(按该句说话人语速换算)。"""
    cap, est = grec["capacity_s"], grec["est_s"]
    need = max(0.0, est - cap)
    on_lines = [ln for ln in grec["lines"] if ln.get("placement", "on") == "on"]   # 画外句不占承载,不进精简目标
    # 短句(<6 有效字,应答/感叹)豁免;削减秒数按估时占比摊到其余句;长句不够摊时再摊到全部句
    cands = [ln for ln in on_lines if ln["chars"] >= 6]
    if sum(ln["est_s"] for ln in cands) <= need + 1e-9:
        cands = list(on_lines)
    pool = sum(ln["est_s"] for ln in cands) or 1.0
    lines = []
    for ln in on_lines:
        cut_s = need * ln["est_s"] / pool if ln in cands else 0.0
        tgt_chars = int(max(0.0, ln["est_s"] - cut_s) * ln["cpm"] / 60.0)  # 向下取整保证 Σ ≤ cap(新口径含余量,偏保守)
        tgt_chars = max(0, min(ln["chars"], tgt_chars))
        lines.append({"shot_id": ln["shot_id"], "speaker": ln["speaker"], "text": ln["text"], "chars": ln["chars"],
                      "est_s": ln["est_s"], "target_chars": tgt_chars, "cut_chars": ln["chars"] - tgt_chars})
    lines.sort(key=lambda x: -x["cut_chars"])
    return {"group_id": grec["group_id"], "total_duration_s": grec["total_duration_s"], "capacity_s": cap,
            "est_s": est, "need_cut_s": grec["over_s"],
            "hint": "按 target_chars 逐句精简(文本层,语义与人设不丢;<6 字短句已豁免);合并/删句亦可,Σ估时 ≤ capacity_s 即通过",
            "lines": lines}


def _write_est_md(path: Path, lines: list, speeds: dict, fallback: float = DEFAULT_CPM, legacy: bool = False):
    """把对白行 `{… est_duration_s: X …}` 的数字按重算值回写(只改数字,其余一字不动)。"""
    if not path.exists() or not lines:
        return
    src = path.read_text().splitlines(keepends=True)
    changed = 0
    for ln in lines:
        i = ln.get("lineno")
        if not i or i > len(src) or ln.get("est_recorded") is None:
            continue
        est = ln.get("est")
        if est is None:
            est = est_seconds(ln["text"], speeds.get(ln.get("speaker"), fallback), ln.get("pace") or "", legacy)
        new = _EST_RE.sub(lambda m: f"{m.group(1)}{est}", src[i - 1], count=1)
        if new != src[i - 1]:
            src[i - 1] = new
            changed += 1
    if changed:
        path.write_text("".join(src))
        print(f"write-est: {path.name} 回写 {changed} 句估时")


def _finish(report, errs, warns, unknown_speakers, ddir, strict, fallback_cpm=DEFAULT_CPM):
    if unknown_speakers:
        warns.append(f"speaker_speed_unknown: {sorted(unknown_speakers)} 无 voice.json#speed_cpm,按项目中位语速 {fallback_cpm:g} cpm 估")
    names = ["dialogue_fit_group", "dialogue_fit_shot", "line_le_cap", "line_est_consistent",
             "lines_text_match_source", "source_lines_covered", "speaker_speed_unknown",
             "placement_valid", "placement_matches_source", "audio_plan_mismatch"]   # 后三项 2026-10-03 声画分离
    for n in names:
        if any(f" {n}:" in e or e.startswith(n) for e in errs):
            report["checks"][n] = "FAIL"
        elif any(f" {n}:" in w or w.startswith(n) for w in warns):
            report["checks"][n] = "WARN"
        else:
            report["checks"][n] = "PASS"
    report["pass"] = not errs and not (strict and warns)
    report["errors"], report["warnings"] = errs, warns
    return report


def main():
    def cfg(ap):
        ap.add_argument("groups", nargs="*", help="只查指定组 id(如 grp003);缺省全批")
        ap.add_argument("--ratio", type=float, default=GROUP_RATIO, help=f"组级承载比(默认 {GROUP_RATIO})")
        ap.add_argument("--shot-ratio", type=float, default=SHOT_RATIO, help=f"镜级承载比(默认 {SHOT_RATIO})")
        ap.add_argument("--write-est", action="store_true", help="按文本+语速重算并回写 est_duration_s(shot_list/screenplay/dialogue.md)")
        ap.add_argument("--report", default=None, help="报告路径(默认 directing/epNN/dialogue_fit.json)")
        ap.add_argument("--no-report", action="store_true", help="不写报告文件")
        ap.add_argument("--strict", action="store_true", help="WARN 也视为 FAIL")
        ap.add_argument("--legacy", dest="legacy", action="store_true", default=None, help="强制按旧口径(字数 ÷ 语速,无余量)")
        ap.add_argument("--new", dest="legacy", action="store_false", help="强制按新口径(时间尺)")
    args, proj_root = parse_args("对白时长适配机检 dialogue_fit(WORKFLOW.md §7D ①)", configure=cfg)
    rep = run(proj_root, args.ep, args.groups or None, ratio=args.ratio, shot_ratio=args.shot_ratio,
              write_est=args.write_est, strict=args.strict, legacy_override=args.legacy)
    for w in rep["warnings"]:
        print("WARN", w)
    for e in rep["errors"]:
        print("FAIL", e)
    s = rep.get("summary") or {}
    if not args.no_report:
        out = Path(args.report) if args.report else proj_root / "directing" / args.ep / "dialogue_fit.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n")
        print(f"报告 → {out}")
    if rep["mode"] == "full":
        print(f"dialogue_fit: 对白组 {s.get('dialogue_groups_checked', 0)} 组已查,超限 {s.get('groups_over', 0)} 组,"
              f"镜级超限 {s.get('shots_over', 0)} / 紧 {s.get('shots_tight', 0)} 镜,台词 {s.get('lines_total', 0)} 句 "
              f"Σ估时 {s.get('est_total_s', 0)}s / 承载 {s.get('capacity_total_s', 0)}s;"
              f"{len(rep['errors'])} FAIL / {len(rep['warnings'])} WARN")
    else:
        print(f"dialogue_fit(仅剧本层): 对白 {rep['source_lines_total']} 句;{len(rep['errors'])} FAIL / {len(rep['warnings'])} WARN")
    if rep["trim_targets"]:
        print(f"trim_targets: {[t['group_id'] for t in rep['trim_targets']]} → 回派 01-story/dialogue-rewrite 按报告逐句精简")
    return 0 if rep["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
