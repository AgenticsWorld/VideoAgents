"""人物嗓音形态(年龄形态)判定:对白语音库 / 后期配音共用的唯一口径(2026-10-02)。

多形态人物(声纹卡 voice.json 有 age_variants,如莲花化身前后的哪吒)每个形态一份嗓音样本
assets/audio/voice/refs/<CHAR>_<variant>_voiceprint.mp3 与一条 casting 条目。合成对白前要先定「这一组里
这个人物是哪个形态」,定错=拿另一个年龄的描述出声且挂不上参考样本(逐句漂音色)。

判定顺序(先命中先用,来源记入台账 variant_source):
  ① prompt   本组 prompt audio_refs 挂的该人物样本文件名(组级事实,闪回组靠它与本集其它组区分);
  ② timeline 声纹卡 age_variants[].chapter_range 与本集章节范围(story/episode_plan.json)相交的形态恰好一个;
  ③ episode  本集其它组 prompt 里该人物挂的形态恰好一种;
  ④ sole     该人物没有基础样本/default 选角条目,已登记(casting 条目或样本文件)的形态恰好一个;
  ⑤ default  声纹卡主字段。
判出的形态既没登记、也没有基础样本可回退,而该人物另有已登记形态 → problem 非空(调用方跳过不合成并告警:
先补该形态的样本/选角,或给组 prompt 挂对应形态样本)。①与②不一致只记 warning(闪回是合法情形)。

样本文件名里的形态段允许下划线(pre_lotus_child):旧正则 [A-Za-z0-9-]+ 不认下划线,整条匹配失败后静默回落
default,是 2026-10-02 fengshen3 ep06/ep07 哪吒对白未挂样本的直接原因。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

try:
    from modules.entity_ids import ACTOR_ID_PAT
except ImportError:                                      # 脚本直跑时无包前缀
    from entity_ids import ACTOR_ID_PAT

_VP_NAME = re.compile(rf"^({ACTOR_ID_PAT})(?:_(.+))?_voiceprint$")   # 编号口径见 modules/entity_ids(#117)
_CH_NUM = re.compile(r"(\d+)")
SOURCES = ("prompt", "timeline", "episode", "sole", "default")


def _read(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
    except (ValueError, OSError):
        return None


def parse_voiceprint(name) -> tuple[str, str] | None:
    """样本文件名/路径 → (人物编号, 形态);基础样本形态为 'default';不是 voiceprint 样本返回 None。"""
    m = _VP_NAME.match(Path(str(name)).stem)
    return (m.group(1), m.group(2) or "default") if m else None


def prompt_variants(base: Path, ep: str) -> dict[str, dict[str, str]]:
    """{group_id: {CHAR: variant}}:各组 prompt audio_refs 里挂的人物样本形态。"""
    out: dict[str, dict[str, str]] = {}
    pdir = Path(base) / "assets" / "prompts" / ep
    if not pdir.is_dir():
        return out
    for p in sorted(pdir.glob("grp*.json")):
        res = {}
        for ref in (_read(p) or {}).get("audio_refs") or []:
            hit = parse_voiceprint(ref)
            if hit:
                res[hit[0]] = hit[1]
        if res:
            out[p.stem] = res
    return out


def _span(lo, hi) -> tuple[int, int] | None:
    nums = [int(m.group(1)) for m in (_CH_NUM.search(str(v or "")) for v in (lo, hi)) if m]
    return (min(nums), max(nums)) if nums else None


def episode_chapters(base: Path, ep: str) -> tuple[int, int] | None:
    """本集章节范围(story/episode_plan.json 的 chapter_range{start,end} 或 chapters[] 首尾);读不到返回 None。"""
    plan = _read(Path(base) / "story" / "episode_plan.json") or {}
    for e in plan.get("episodes") or []:
        if not isinstance(e, dict) or ep not in (e.get("ep"), e.get("episode_id"), e.get("id")):
            continue
        cr = e.get("chapter_range")
        if isinstance(cr, dict):
            return _span(cr.get("start") or cr.get("from"), cr.get("end") or cr.get("to"))
        ch = e.get("chapters")
        if isinstance(ch, list) and ch:
            return _span(ch[0], ch[-1])
    return None


def card_variants(base: Path, character: str) -> list[tuple[str, tuple[int, int] | None]]:
    """声纹卡 age_variants → [(形态键, 章节范围或 None)]。形态键取 variant / version_id / id / name 首个非空。"""
    v = _read(Path(base) / "bible" / "characters" / character / "voice.json") or {}
    out = []
    for item in v.get("age_variants") or []:
        if not isinstance(item, dict):
            continue
        key = next((str(item[k]).strip() for k in ("variant", "version_id", "id", "name") if item.get(k)), "")
        cr = item.get("chapter_range")
        span = _span(cr.get("from") or cr.get("start"), cr.get("to") or cr.get("end")) if isinstance(cr, dict) else None
        if key:
            out.append((key, span))
    return out


def registered_forms(base: Path, character: str, casting: dict | None = None) -> dict[str, dict]:
    """该人物已登记的形态:{variant: {casting: bool, sample: bool}}(casting 条目或 refs/ 下的样本文件,任一即算)。"""
    base = Path(base)
    forms: dict[str, dict] = {}
    if casting is None:
        d = _read(base / "assets" / "audio" / "voice" / "casting.json") or {}
        casting = {(c.get("character_id") or c.get("char_id"), c.get("variant") or "default"): c
                   for c in d.get("castings") or d.get("entries") or [] if isinstance(c, dict)}
    for ch, var in casting:
        if ch == character:
            forms.setdefault(var or "default", {"casting": False, "sample": False})["casting"] = True
    refs = base / "assets" / "audio" / "voice" / "refs"
    if refs.is_dir():
        for f in refs.glob(f"{character}*_voiceprint.*"):
            hit = parse_voiceprint(f.name)
            if hit and hit[0] == character:
                forms.setdefault(hit[1], {"casting": False, "sample": False})["sample"] = True
    return forms


class Resolver:
    """一集内反复判定用:prompt 样本表、章节范围、casting、声纹卡各读一次。"""

    def __init__(self, base: Path, ep: str, casting: dict | None = None):
        self.base, self.ep = Path(base), ep
        self.prompt = prompt_variants(self.base, ep)
        self.chapters = episode_chapters(self.base, ep)
        self._casting = casting
        self._forms: dict[str, dict] = {}
        self._cards: dict[str, list] = {}

    def forms(self, character: str) -> dict[str, dict]:
        if character not in self._forms:
            self._forms[character] = registered_forms(self.base, character, self._casting)
        return self._forms[character]

    def card(self, character: str) -> list:
        if character not in self._cards:
            self._cards[character] = card_variants(self.base, character)
        return self._cards[character]

    def timeline(self, character: str) -> str:
        """按章节范围能唯一确定的形态;卡上没写章节、本集章节读不到、或相交形态不止一个时返回 ''。"""
        if not self.chapters:
            return ""
        lo, hi = self.chapters
        hits = [k for k, span in self.card(character) if span and span[0] <= hi and lo <= span[1]]
        return hits[0] if len(hits) == 1 else ""

    def resolve(self, group_id: str, character: str) -> dict:
        """→ {variant, source, problem, warning}。problem 非空=不该合成(形态未登记且无基础样本可回退)。"""
        forms = self.forms(character)
        tl = self.timeline(character)
        v = (self.prompt.get(group_id) or {}).get(character)
        warning = ""
        if v:
            src = "prompt"
            if tl and tl != v:
                warning = (f"{character} 在 {group_id} 的 prompt 挂的是 {v} 形态样本,按本集章节应为 {tl}"
                           "(闪回可忽略;否则改 prompt 的 audio_refs)")
        elif tl:
            v, src = tl, "timeline"
        else:
            peers = {m[character] for m in self.prompt.values() if character in m}
            named = [k for k in forms if k != "default"]
            if len(peers) == 1:
                v, src = peers.pop(), "episode"
            elif "default" not in forms and len(named) == 1:
                v, src = named[0], "sole"
            else:
                v, src = "default", "default"
        problem = ""
        if forms and v not in forms and "default" not in forms:
            have = "、".join(sorted(forms))
            if src == "default":
                problem = (f"{character} 有多个嗓音形态({have}),判定不了本组用哪个:给本组 prompt 的 audio_refs 挂对应形态样本,"
                           "或在声纹卡 age_variants 写 chapter_range")
            else:
                problem = (f"{character}/{v} 形态还没有嗓音样本和选角条目(已登记:{have}):"
                           "先派 voice-generation 补出该形态样本")
        return {"variant": v, "source": src, "problem": problem, "warning": warning}
