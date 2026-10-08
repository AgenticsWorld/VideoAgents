"""对白语音库(2026-09-13,输出设置「生成对白语音」output.dialogue_tts,默认关)。

开启后,不论项目「对白配音」选视频原声还是后期配音,项目都维护一份**按 shot_list 对白逐句、用人物嗓音模板合成**
的 TTS 语音库,供三处消费:① 故事板页动态样片(code/animatic.py)挂对白轨;② 分镜页白模样片
(modules/whitebox_export.concat_episode)挂对白轨;③ 对白配音=后期配音时 code/dub_group.py 先取库里的自然语速
音频再贴合口型(只有需要改语速时才重新合成)。库本身是唯一事实源,消费方不再各自调 TTS。

- 事实源:directing/<ep>/shot_list.json shots[].dialogue_lines[](speaker/text,兼容 line / character_id 写法),
  镜内序号 idx 只数有台词的句子,与 dub_group 的组内序号口径一致(按镜序累加)。
- 产物:assets/audio/voice/<ep>/tts/<shot>_l<idx>_<CHAR>.mp3 + tts_manifest.json(schema dialogue_tts/v1,
  逐句 speaker/variant/text/emotion/key/file/duration_s/status,summary 与 checks)。
- 惰性同步:每句一个 key = sha256(speaker, variant, text, emotion, casting 条目的 model/voice/speed/desc,
  voiceprint 样本 sha256, TTS 渠道/模型);消费方调用前 sync() 一次,只补合成 key 变了或文件缺失的句子,
  台词删掉的句子文件移到 _prev/。同一集加文件锁,动态样片与白模样片同时触发也不会重复合成。
- 台词演法(2026-10-02,modules/dialogue_direction.py):对白行带有效 delivery(演法 / 场景 / 目标时长)时,合成改走
  「导演式提示词」(火山 Doubao-音频生成 1.0 整套用上、时长按目标出;其余渠道只把演法当语气指令),演法进 key;
  没写演法的句子照旧。节奏贴合与估时偏差对有演法的句子以目标时长为准。
- 说话人形态(2026-10-02):多形态人物(声纹卡 age_variants)每句用哪个形态由 modules/voice_variants.py 判定
  (组 prompt 样本名 → 声纹卡章节范围 → 本集其它组 → 唯一已登记形态 → default),来源记入每句 variant_source;
  判不出或该形态未登记 → status=unbound 跳过并 WARN,不拿别的年龄形态硬出声。
- 嗓音模板:走 modules/genmedia.generate_tts(character/variant/project 三参数),与 dub_group 同一套——
  按生效渠道的语音模式(modules/voice_library.py,2026-10-02):音色设计=按声纹卡描述+项目 voiceprint 样本当参考;
  音色库=用 casting.json 登记的音色(渠道音色 ID,或本地音色库的参考音频文件名——本地库没登记时生成层按人物自动选)。
  speaker 不是 CHAR-/CRE- 编号、渠道音色库缺 casting 条目、音色设计模式缺样本(火山音频生成 1.0 除外)
  → 该句 status=unbound 跳过并 WARN(样片照出、不阻断)。
- 顺带机检:实测时长与 est_duration_s 偏差 >30% 记入 checks.est_vs_actual(WARN 级,给分镜规划做反馈)。
- 语速(2026-09-15):每句 speed = casting 条目的数字 speed(倍率,如 1.15;描述文字视为未填)> 项目输出设置
  output.dialogue_tts_speed(默认 1.0;CLI --speed 临时覆盖该默认值)。speed 进 key,改了自动重出。
- 静音修剪(2026-09-15):合成原声落 _raw/,库文件为裁掉首尾静音的版本(首留 0.10s、尾留 0.15s;seed-audio
  首尾常各带 0.4–1.0s 空白,短句尤甚);output.dialogue_tts_max_pause>0 时句中超过该秒数的停顿也压到该值(默认 0=不动)。
  修剪是后处理,不进 key:参数变了从 _raw/ 重裁,不重新调 TTS;台账每句记 trim{lead_s,tail_s,pause_s,params}。
- 节奏贴合(2026-09-28):seed-audio 自然语速约 3 字/秒且标点处停顿 0.6–1.7s,而分镜按 est_duration_s(约 4.2 字/秒)
  定镜长,样片里对白普遍拖到下一镜、与下一句重叠。库文件(自然语速)不动——后期配音仍取它按开口时段贴合;
  另出一份**节奏贴合版** _paced/<同名文件> 供动态样片/白模样片用:自然时长超过 est_duration_s 的句子先把句中
  停顿压到 PACE_MAX_PAUSE,仍超出再 atempo 变速不变调贴到估时,倍率上限 output.dialogue_tts_max_tempo
  (默认 1.5,1.0=关闭)。同为后处理不进 key、不重新调 TTS;台账每句记 pace{file,duration_s,tempo,pause_s,target_s,params},
  消费方用 line_audio(paced=True) 取用。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

SCHEMA = "dialogue_tts/v1"
MANIFEST = "tts_manifest.json"
LIB_REL = "assets/audio/voice/{ep}/tts"
_ID_RE = re.compile(r"^(CHAR|CRE)-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$")
EST_TOLERANCE = 0.30      # 实测/估时偏差超过该比例记 WARN
RAW_DIR = "_raw"          # 合成原声(未修剪)存放子目录
TRIM_NOISE_DB = -35.0     # 静音判定阈值
TRIM_MIN_SIL = 0.20       # 短于此的空白不算静音段
TRIM_KEEP_HEAD = 0.10     # 开头保留的静音
TRIM_KEEP_TAIL = 0.15     # 结尾保留的静音
SPEED_RANGE = (0.5, 2.0)  # 语速倍率合法区间(火山 speech_rate -50..100 / minimax 0.5..2.0 同口径)
PACED_DIR = "_paced"      # 节奏贴合版(样片用)存放子目录
PACE_MAX_TEMPO = 1.5      # 贴合变速倍率上限的默认值(output.dialogue_tts_max_tempo;1.0=关闭节奏贴合)
PACE_MAX_PAUSE = 0.30     # 贴合时句中停顿压到该秒数
PACE_TOLERANCE = 0.02     # 自然时长超出估时不到该比例视为已贴合,不另出贴合版
PACE_KEEP_HEAD = 0.05     # 贴合版开头保留的静音(排轨另有 LEAD_S)
PACE_KEEP_TAIL = 0.08     # 贴合版结尾保留的静音(排轨另有 GAP_S)
TEMPO_RANGE = (1.0, 2.0)  # 贴合变速倍率合法区间(单级 atempo 上限 2.0)


# ---------------------------------------------------------------- 基础读取

def _read(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
    except (ValueError, OSError):
        return None


def enabled(base: Path) -> bool:
    """项目输出设置 output.dialogue_tts(默认关);对白配音=后期配音(output.dialogue_voice=dubbing)时必开
    (2026-09-23 用户拍板:UI 锁死、服务端保存归一,这里再兜底让未重新保存的存量项目同口径)。"""
    st = _read(Path(base) / "settings.json") or {}
    out = st.get("output") or {}
    return out.get("dialogue_tts") is True or out.get("dialogue_voice") == "dubbing"


def dialogue_voice_mode(base: Path) -> str:
    st = _read(Path(base) / "settings.json") or {}
    return (st.get("output") or {}).get("dialogue_voice") or "native"


def num_speed(v) -> float | None:
    """语速倍率:数字(或数字串)且在 SPEED_RANGE 内才算;casting 里的描述文字(如「常态(未传 --speed)」)视为未填。"""
    if isinstance(v, bool) or v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if SPEED_RANGE[0] <= f <= SPEED_RANGE[1] else None


def default_speed(base: Path) -> float:
    """项目输出设置 output.dialogue_tts_speed(全局默认语速倍率,默认 1.0);casting 条目有数字 speed 的句子不受影响。"""
    st = _read(Path(base) / "settings.json") or {}
    return num_speed((st.get("output") or {}).get("dialogue_tts_speed")) or 1.0


def default_max_pause(base: Path) -> float:
    """项目输出设置 output.dialogue_tts_max_pause:句中停顿上限(秒),0=不压缩。"""
    st = _read(Path(base) / "settings.json") or {}
    v = _f((st.get("output") or {}).get("dialogue_tts_max_pause"))
    return v if v and v > 0 else 0.0


def default_max_tempo(base: Path) -> float:
    """项目输出设置 output.dialogue_tts_max_tempo:节奏贴合的变速倍率上限(1.0–2.0,默认 1.5;1.0=关闭贴合)。"""
    st = _read(Path(base) / "settings.json") or {}
    v = (st.get("output") or {}).get("dialogue_tts_max_tempo")
    f = None if isinstance(v, bool) else _f(v)
    return f if f is not None and TEMPO_RANGE[0] <= f <= TEMPO_RANGE[1] else PACE_MAX_TEMPO


def lib_dir(base: Path, ep: str) -> Path:
    return Path(base) / LIB_REL.format(ep=ep)


def load_manifest(base: Path, ep: str) -> dict | None:
    return _read(lib_dir(base, ep) / MANIFEST)


def tts_channel() -> tuple[str, str]:
    """生效 TTS 渠道与模型(genconfig tts 段);读不到时返回空串,不阻断。"""
    try:
        from modules.genmedia import CONFIG_PATH
        cfg = (json.loads(Path(CONFIG_PATH).read_text(encoding="utf-8")) or {}).get("tts") or {}
    except Exception:  # noqa: BLE001
        return "", ""
    provider = str(cfg.get("provider") or "")
    pc = cfg.get(provider) if isinstance(cfg.get(provider), dict) else {}
    model = str((pc or {}).get("custom_model") or (pc or {}).get("model") or cfg.get("model") or "")
    if provider == "volcengine":                      # 火山按语音模式定模型:音色设计=音频生成 1.0,音色库=Seed-TTS 档
        model = _voice_library().effective_model(provider, pc)
    return provider, model


def _voice_library():
    try:
        from modules import voice_library
    except ImportError:                               # 脚本直跑时无包前缀
        import voice_library
    return voice_library


def voice_mode(provider: str, model: str) -> tuple[str, str]:
    """生效 TTS 渠道的语音模式(modules/voice_library.py)→ (design|library, 音色库种类 local|provider|'')。
    音色设计:按声纹卡描述出样本、对白拿样本当参考,不看 casting 的音色;音色库:按 casting 登记的音色合成
    (local=本地参考音频库,没登记时生成层按人物自动选;provider=渠道音色 ID,没登记就合成不了)。"""
    vl = _voice_library()
    if provider == "volcengine":
        return ("design", "") if model == vl.SEEDAUDIO_MODEL else ("library", "provider")
    if provider in vl.DESIGN_PROVIDERS:
        pc = vl.load_tts_config().get(provider)
        pc = pc if isinstance(pc, dict) else {}
        return vl.voice_mode(provider, pc), vl.voice_library_kind(provider, pc)
    return "library", "provider"


def _name_index(base: Path) -> dict[str, str]:
    """规范名/别名 → CHAR-/CRE- 编号(speaker 写成人名的兜底解析)。"""
    out: dict[str, str] = {}
    cidx = _read(Path(base) / "bible" / "characters" / "index.json") or {}
    for c in cidx.get("characters") or []:
        if isinstance(c, dict) and c.get("id"):
            for k in ("canonical_name", "name"):
                if c.get(k):
                    out.setdefault(str(c[k]).strip(), c["id"])
            for a in c.get("aliases") or []:
                # 别名两种落盘形态:纯字符串,或 {name, source_chapter…}(#58)
                a = a.get("name") or a.get("alias") if isinstance(a, dict) else a
                if isinstance(a, str) and a.strip():
                    out.setdefault(a.strip(), c["id"])
    cr = _read(Path(base) / "bible" / "creatures" / "index.json") or {}
    for c in cr.get("creatures") or []:
        if isinstance(c, dict) and c.get("id") and c.get("name"):
            out.setdefault(str(c["name"]).strip(), c["id"])
    return out


name_index = _name_index   # 公共名:check_dialogue_fit 等机检共用同一套别名表(#58)

_SPK_ID_RE = re.compile(r"((?:CHAR|CRE)-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)")  # 数字编号 CHAR-0001 与拼音 slug CHAR-jie-rui-er 都认


def resolve_speaker(ln: dict, names: dict[str, str]) -> tuple[str, str]:
    """对白行 → (说话人编号 或 '', 原始说话人文本)。与 code/check_dialogue_fit.py 共用口径(#58):
    ① speaker / char 中的 CHAR-/CRE- 编号 → ② 同级 speaker_char / character_id 编号
    → ③ 规范名/别名表反查 speaker(全文,再去括注)→ 仍无返回 ''。"""
    raw = str(ln.get("speaker") or ln.get("char") or ln.get("character_id") or "").strip()
    m = _SPK_ID_RE.search(raw)
    if m:
        return m.group(1), raw
    for k in ("speaker_char", "character_id"):
        m = _SPK_ID_RE.search(str(ln.get(k) or "").strip())
        if m:
            return m.group(1), raw
    hit = names.get(raw) or names.get(re.sub(r"[〔【(\[（].*$", "", raw).strip())
    return (hit or ""), raw


def collect_lines(base: Path, ep: str, shot_list: dict | None = None) -> list[dict]:
    """shot_list 全部对白句(镜序 × 句序):[{seq, shot_id, idx, group_id, speaker, speaker_raw, text, emotion, est_duration_s}]。
    idx = 镜内有台词句序号(0 起),与 whitebox_subtitles / dub_group 的过滤口径一致(空文本不计)。"""
    base = Path(base)
    sl = shot_list if shot_list is not None else (_read(base / "directing" / ep / "shot_list.json") or {})
    group_of: dict[str, str] = {}
    for g in sl.get("generation_groups") or []:
        if isinstance(g, dict) and g.get("group_id"):
            for sid in g.get("shots") or []:
                if isinstance(sid, str):
                    group_of[sid] = g["group_id"]
    names = _name_index(base)
    out: list[dict] = []
    seq = 0
    for s in sl.get("shots") or []:
        if not isinstance(s, dict) or not s.get("shot_id"):
            continue
        idx = 0
        for ln in s.get("dialogue_lines") or []:
            if not isinstance(ln, dict):
                continue
            text = str(ln.get("text") or ln.get("line") or "").strip()
            if not text:
                continue
            speaker, raw = resolve_speaker(ln, names)
            item = {"seq": seq, "shot_id": s["shot_id"], "idx": idx, "group_id": group_of.get(s["shot_id"], ""),
                    "speaker": speaker, "speaker_raw": raw, "text": text,
                    "emotion": str(ln.get("emotion") or ln.get("tone") or "").strip(),
                    "est_duration_s": _f(ln.get("est_duration_s"))}
            delivery = _direction().line_delivery(ln)     # 台词演法(仍有效的才算;台词改过的作废)
            if delivery:
                item.update(direction=delivery["direction"], scene=delivery["scene"], target_s=delivery["target_s"])
            item.update(_placement_fields(ln, s["shot_id"]))   # 声画分离(2026-10-03):画内/画外/V.O. 与听见的镜
            out.append(item)
            idx += 1
            seq += 1
    return out


# ---------------------------------------------------------------- 声画分离(2026-10-03,docs/sound_split.md)

def _offscreen():
    """modules/offscreen_lines(画外句契约);模块缺失时退化为「全部画内」。"""
    try:
        from modules import offscreen_lines
    except ImportError:
        try:
            import offscreen_lines                        # 脚本直跑时无包前缀
        except ImportError:
            return None
    return offscreen_lines


def _placement_fields(ln: dict, shot_id: str) -> dict:
    """对白行的声源位置字段(透传给台账 / 排轨 / 后期配音):placement on|os|vo、heard_in(听见的镜)、source_fx、offset_s。
    os/vo 句照常进库合成(库是自然语速事实源,offscreen_lines.synth 复用库文件),idx 序号不变。"""
    osl = _offscreen()
    if osl is None:
        return {"placement": "on", "heard_in": [shot_id]}
    pl = osl.placement(ln)
    out = {"placement": pl, "heard_in": osl.heard_in(ln, shot_id) if pl != "on" else [shot_id]}
    if pl != "on":
        fx = str(ln.get("source_fx") or "").strip()
        out["source_fx"] = fx if fx in getattr(osl, "SOURCE_FX", {}) else ("inner" if pl == "vo" else "plain")
        off = _f(ln.get("offset_s"))
        out["offset_s"] = off if off is not None and off >= 0 else 0.4
    return out


def onscreen_only(items: list[dict]) -> list[dict]:
    """只留画内开口的句子(placement 缺省即画内)。"""
    return [e for e in items if (e.get("placement") or "on") == "on"]


def _direction():
    try:
        from modules import dialogue_direction
    except ImportError:                               # 脚本直跑时无包前缀
        import dialogue_direction
    return dialogue_direction


def _f(v) -> float | None:
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def load_casting(base: Path) -> dict[tuple[str, str], dict]:
    """assets/audio/voice/casting.json → {(character_id, variant): 条目};缺文件返回空表(不报错,库允许未选角)。"""
    d = _read(Path(base) / "assets" / "audio" / "voice" / "casting.json") or {}
    out = {}
    # 规约键 castings;voice-generation 实际落表用过 entries,两者都认
    for c in d.get("castings") or d.get("entries") or []:
        if isinstance(c, dict) and (c.get("character_id") or c.get("char_id")):
            out[(c.get("character_id") or c.get("char_id"), c.get("variant") or "default")] = c
    return out


def _variant_resolver(base: Path, ep: str, casting: dict | None = None):
    """说话人形态判定(modules/voice_variants.py,与 dub_group 同一口径):组 prompt 样本名 → 声纹卡章节范围
    → 本集其它组 → 唯一已登记形态 → default。"""
    try:
        from modules.voice_variants import Resolver
    except ImportError:                               # 脚本直跑时无包前缀
        from voice_variants import Resolver
    return Resolver(base, ep, casting)


def voiceprint_path(base: Path, character: str, variant: str) -> Path | None:
    refs = Path(base) / "assets" / "audio" / "voice" / "refs"
    names = [f"{character}_{variant}_voiceprint.mp3"] if variant and variant != "default" else []
    names.append(f"{character}_voiceprint.mp3")
    for n in names:
        if (refs / n).is_file():
            return refs / n
    return None


def _sha_file(p: Path | None, cache: dict) -> str:
    if not p:
        return ""
    k = str(p)
    if k not in cache:
        try:
            cache[k] = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
        except OSError:
            cache[k] = ""
    return cache[k]


# ---------------------------------------------------------------- 计划(纯读,不合成)

def plan(base: Path, ep: str, shot_list: dict | None = None, channel: tuple[str, str] | None = None,
         speed: float | None = None) -> dict:
    """逐句计算 key 与现状:status ∈ fresh(库里有且 key 一致)| stale(key 变了)| missing(未合成)| unbound(不能合成)。
    speed:本次默认语速倍率(None=项目设置 output.dialogue_tts_speed);casting 条目有数字 speed 的句子优先用条目值。
    返回 {lines:[...], manifest, provider, model, speed, removed:[旧台账里已不在台词中的文件]}。"""
    base = Path(base)
    provider, model = channel if channel else tts_channel()
    mode, library = voice_mode(provider, model)
    base_speed = num_speed(speed) or default_speed(base)
    casting = load_casting(base)
    resolver = _variant_resolver(base, ep, casting)
    old = load_manifest(base, ep) or {}
    old_by_file = {e.get("file"): e for e in old.get("lines") or [] if isinstance(e, dict) and e.get("file")}
    ldir = lib_dir(base, ep)
    sha_cache: dict = {}
    lines = []
    for ln in collect_lines(base, ep, shot_list):
        ch = ln["speaker"]
        vr = resolver.resolve(ln["group_id"], ch) if ch else {"variant": "default", "source": "default", "problem": "", "warning": ""}
        var = vr["variant"]
        entry = dict(ln, variant=var, variant_source=vr["source"], reason="", tts_voice="", tts_model="",
                     speed=base_speed, key="", file="")
        if vr["warning"]:
            entry["variant_warning"] = vr["warning"]
        if not ch:
            entry.update(status="unbound", reason=f"speaker 不是人物/生物编号:{ln['speaker_raw'] or '(空)'}")
            lines.append(entry)
            continue
        if vr["problem"]:
            # 多形态人物的形态没判出来/没登记:不拿另一个年龄的描述硬出声(出了也挂不上样本,逐句漂音色)
            entry.update(status="unbound", reason=vr["problem"])
            lines.append(entry)
            continue
        c = casting.get((ch, var)) or casting.get((ch, "default")) or {}
        if c:
            entry.update(tts_voice=str(c.get("tts_voice") or ""), tts_model=str(c.get("tts_model") or ""),
                         speed=num_speed(c.get("speed")) or base_speed)
        vp = voiceprint_path(base, ch, var)
        entry["anchored"] = bool(vp)
        if not c and library == "provider":
            # 渠道音色库按音色 ID 合成,没有选角条目就没有该角色的嗓音;不用默认音色顶替(会全员同声)
            entry.update(status="unbound", reason=f"casting.json 无 {ch}/{var} 条目(音色库模式 {provider or '?'} 需先选角)")
            lines.append(entry)
            continue
        if mode == "design" and not vp and provider != "volcengine":
            # 音色设计模式的 Voice Clone 模型必须拿嗓音样本当参考(火山音频生成 1.0 例外:没样本也能按描述出声)
            entry.update(status="unbound", reason=f"{ch}/{var} 还没有嗓音样本(音色设计模式先出 {ch}_voiceprint 样本)")
            lines.append(entry)
            continue
        if not c and not vp and not (base / "bible" / "characters" / ch / "voice.json").is_file():
            entry.update(status="unbound", reason=f"{ch} 既无 casting 条目也无声纹卡/voiceprint 样本")
            lines.append(entry)
            continue
        payload = "|".join([ch, var, ln["text"], ln["emotion"], entry["tts_model"], entry["tts_voice"],
                            f"{entry['speed']:.3f}", str(c.get("voice_desc") or ""), provider, model, _sha_file(vp, sha_cache)])
        if ln.get("direction"):
            # 有演法的句子:演法 / 场景 / 目标时长进 key(改了就重出);没演法的句子 key 口径不变,存量库不会集体过期
            payload += "|D:" + "|".join([ln["direction"], ln.get("scene") or "", f"{ln.get('target_s') or 0:.2f}"])
        entry["key"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
        entry["file"] = f"{ln['shot_id']}_l{ln['idx']:02d}_{ch}.mp3"
        prev = old_by_file.get(entry["file"])
        f = ldir / entry["file"]
        if prev and prev.get("key") == entry["key"] and prev.get("status") == "ok" and f.is_file():
            entry.update(status="fresh", duration_s=prev.get("duration_s"), generated_at=prev.get("generated_at"))
        elif prev and prev.get("status") == "ok" and f.is_file():
            entry["status"] = "stale"
        else:
            entry["status"] = "missing"
        lines.append(entry)
    wanted = {e["file"] for e in lines if e.get("file")}
    removed = [f for f in old_by_file if f not in wanted]
    return {"lines": lines, "manifest": old, "provider": provider, "model": model, "speed": base_speed, "removed": removed}


def _speaker_variants(lines: list[dict]) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for e in lines:
        if e.get("speaker") and e.get("variant_source"):
            out.setdefault(e["speaker"], {})[e["variant"]] = e["variant_source"]
    return out


def status(base: Path, ep: str) -> dict:
    """页面/接口用的摘要:enabled/total/fresh/stale/missing/unbound/synced_at/needs_sync。"""
    base = Path(base)
    on = enabled(base)
    if not (base / "directing" / ep / "shot_list.json").is_file():
        return {"enabled": on, "total": 0, "fresh": 0, "stale": 0, "missing": 0, "unbound": 0, "failed": 0,
                "needs_sync": False, "synced_at": "", "has_shot_list": False, "path": LIB_REL.format(ep=ep)}
    p = plan(base, ep)
    cnt = {k: sum(1 for e in p["lines"] if e["status"] == k) for k in ("fresh", "stale", "missing", "unbound")}
    old = p["manifest"] or {}
    failed = sum(1 for e in old.get("lines") or [] if isinstance(e, dict) and e.get("status") == "failed")
    return {"enabled": on, "total": len(p["lines"]), **cnt, "failed": failed, "removed": len(p["removed"]),
            "needs_sync": bool(cnt["stale"] or cnt["missing"] or p["removed"]),
            "synced_at": old.get("synced_at") or "", "has_shot_list": True, "path": LIB_REL.format(ep=ep),
            "provider": p["provider"], "model": p["model"],
            "unbound_reasons": sorted({e["reason"] for e in p["lines"] if e["status"] == "unbound"})[:6]}


# ---------------------------------------------------------------- 同步(合成)

def probe_duration(path: Path) -> float | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        r = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                           capture_output=True, text=True, timeout=60)
        return float(r.stdout.strip().splitlines()[-1]) if r.returncode == 0 and r.stdout.strip() else None
    except (ValueError, subprocess.SubprocessError, IndexError):
        return None


# ---------------------------------------------------------------- 静音修剪(后处理)

_SIL_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SIL_END = re.compile(r"silence_end:\s*(-?[\d.]+)")


def detect_silences(path: Path, noise_db: float = TRIM_NOISE_DB, min_sil: float = TRIM_MIN_SIL) -> list[tuple[float, float]]:
    """ffmpeg silencedetect → [(start, end)],按时间排序;末尾未闭合的静音段以文件时长封口。"""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return []
    r = subprocess.run([ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-vn",
                        "-af", f"silencedetect=noise={noise_db}dB:d={min_sil}", "-f", "null", "-"],
                       capture_output=True, text=True, timeout=120)
    out = (r.stderr or "") + (r.stdout or "")
    starts = [float(x) for x in _SIL_START.findall(out)]
    ends = [float(x) for x in _SIL_END.findall(out)]
    if len(ends) < len(starts):
        total = probe_duration(path)
        ends.append(total if total is not None else starts[-1])
    return sorted((max(0.0, a), b) for a, b in zip(starts, ends) if b > a)


def trim_plan(silences: list[tuple[float, float]], total: float, keep_head: float = TRIM_KEEP_HEAD,
              keep_tail: float = TRIM_KEEP_TAIL, max_pause: float = 0.0, eps: float = 0.05) -> dict:
    """纯计算:给定静音段与总时长,算要保留的区间 keep=[(a,b),...] 与各处裁掉的秒数 lead_s/tail_s/pause_s。
    开头静音留 keep_head、结尾静音留 keep_tail;max_pause>0 时句中长于它的静音压到 max_pause(中间掐掉)。
    全是静音或无需裁剪时 keep=[(0,total)]。"""
    lead = tail = pause = 0.0
    start, end = 0.0, float(total)
    sil = [(a, b) for a, b in silences if b > a]
    if sil and sil[0][0] <= eps:
        a, b = sil[0]
        if b >= total - eps:                      # 整段静音:不裁
            return {"keep": [(0.0, total)], "lead_s": 0.0, "tail_s": 0.0, "pause_s": 0.0}
        start = max(0.0, b - keep_head)
        lead = start
        sil = sil[1:]
    if sil and sil[-1][1] >= total - eps:
        a, b = sil[-1]
        end = min(total, a + keep_tail)
        tail = total - end
        sil = sil[:-1]
    keep: list[tuple[float, float]] = []
    cur = start
    if max_pause > 0:
        for a, b in sil:
            if a < start or b > end or (b - a) <= max_pause:
                continue
            cut = (b - a) - max_pause
            mid = (a + b) / 2
            keep.append((cur, mid - cut / 2))
            cur = mid + cut / 2
            pause += cut
    keep.append((cur, end))
    keep = [(round(a, 4), round(b, 4)) for a, b in keep if b - a > 0.01]
    if not keep:
        keep = [(0.0, total)]
        lead = tail = pause = 0.0
    return {"keep": keep, "lead_s": round(lead, 3), "tail_s": round(tail, 3), "pause_s": round(pause, 3)}


def apply_trim(src: Path, dst: Path, keep: list[tuple[float, float]], tempo: float = 1.0) -> None:
    """按 keep 区间 atrim+concat 重编码到 dst(mp3 128k / 其它按扩展名默认编码器);tempo≠1 时拼完再 atempo 变速不变调。"""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("缺 ffmpeg,无法修剪静音")
    parts = "".join(f"[0:a]atrim=start={a}:end={b},asetpts=PTS-STARTPTS[s{i}];" for i, (a, b) in enumerate(keep))
    chain = "".join(f"[s{i}]" for i in range(len(keep)))
    fc = f"{parts}{chain}concat=n={len(keep)}:v=0:a=1" + (f",atempo={tempo:.4f}" if abs(tempo - 1.0) > 1e-3 else "") + "[out]"
    codec = ["-c:a", "libmp3lame", "-b:a", "128k"] if dst.suffix.lower() == ".mp3" else []
    tmp = dst.with_name(dst.stem + ".trim.tmp" + dst.suffix)
    r = subprocess.run([ffmpeg, "-hide_banner", "-nostats", "-loglevel", "error", "-y", "-i", str(src),
                        "-filter_complex", fc, "-map", "[out]", *codec, str(tmp)],
                       capture_output=True, text=True, timeout=300)
    if r.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg 修剪失败:{(r.stderr or '').strip()[-200:]}")
    os.replace(tmp, dst)


def trim_file(raw: Path, dst: Path, max_pause: float = 0.0, probe=None) -> dict:
    """raw(合成原声)→ dst(修剪版);返回台账 trim 段 {lead_s, tail_s, pause_s, params}。无 ffmpeg 或无可裁时直接复制。"""
    probe = probe or probe_duration
    params = {"noise_db": TRIM_NOISE_DB, "keep_head": TRIM_KEEP_HEAD, "keep_tail": TRIM_KEEP_TAIL,
              "max_pause": round(float(max_pause or 0.0), 3)}
    total = probe(raw)
    if not total or not shutil.which("ffmpeg"):
        if raw.resolve() != dst.resolve():
            shutil.copyfile(raw, dst)
        return {"lead_s": 0.0, "tail_s": 0.0, "pause_s": 0.0, "params": params, "skipped": "no ffmpeg/duration"}
    tp = trim_plan(detect_silences(raw), total, max_pause=max_pause)
    if tp["keep"] == [(0.0, total)] or (tp["lead_s"] + tp["tail_s"] + tp["pause_s"]) < 0.02:
        if raw.resolve() != dst.resolve():
            shutil.copyfile(raw, dst)
        return {"lead_s": 0.0, "tail_s": 0.0, "pause_s": 0.0, "params": params}
    apply_trim(raw, dst, tp["keep"])
    return {"lead_s": tp["lead_s"], "tail_s": tp["tail_s"], "pause_s": tp["pause_s"], "params": params}


# ---------------------------------------------------------------- 节奏贴合(后处理,样片用)

def pace_plan(silences: list[tuple[float, float]], total: float, target: float, max_tempo: float = PACE_MAX_TEMPO,
              max_pause: float = PACE_MAX_PAUSE) -> dict:
    """纯计算:先按 max_pause 压句中停顿(首尾静音留 PACE_KEEP_HEAD/TAIL),压完仍超过 target 的部分用变速补,
    倍率封顶 max_tempo。返回 {keep, tempo, pause_s, duration_s(贴合后预计时长)}。"""
    tp = trim_plan(silences, total, keep_head=PACE_KEEP_HEAD, keep_tail=PACE_KEEP_TAIL, max_pause=max_pause)
    kept = sum(b - a for a, b in tp["keep"])
    tempo = min(float(max_tempo), max(1.0, kept / target)) if target > 0 else 1.0
    if tempo < 1.02:                                  # 2% 以内听不出,免得白白重采样
        tempo = 1.0
    return {"keep": tp["keep"], "tempo": round(tempo, 4), "pause_s": tp["pause_s"], "duration_s": round(kept / tempo, 3)}


def pace_params(target: float, max_tempo: float) -> dict:
    return {"target_s": round(float(target), 3), "max_tempo": round(float(max_tempo), 3), "max_pause": PACE_MAX_PAUSE,
            "keep_head": PACE_KEEP_HEAD, "keep_tail": PACE_KEEP_TAIL, "noise_db": TRIM_NOISE_DB}


def pace_file(src: Path, dst: Path, target: float, max_tempo: float = PACE_MAX_TEMPO, probe=None) -> dict:
    """src(合成原声,没有则库文件)→ dst(节奏贴合版);返回台账 pace 段(不含 file)。"""
    probe = probe or probe_duration
    total = probe(src)
    if not total:
        raise RuntimeError("读不到音频时长")
    pp = pace_plan(detect_silences(src), total, target, max_tempo=max_tempo)
    dst.parent.mkdir(parents=True, exist_ok=True)
    apply_trim(src, dst, pp["keep"], tempo=pp["tempo"])
    dur = probe(dst)
    return {"duration_s": round(dur, 3) if dur is not None else pp["duration_s"], "tempo": pp["tempo"],
            "pause_s": pp["pause_s"], "target_s": round(float(target), 3), "params": pace_params(target, max_tempo)}


def _default_tts(base: Path):
    from modules.genmedia import generate_tts

    def run(entry: dict, out: Path):
        var = entry["variant"] if entry["variant"] != "default" else ""
        voice = "" if entry.get("_voice_by_character") else entry["tts_voice"]
        generate_tts(entry["text"], str(out), voice, entry["speed"], entry["emotion"], entry["speaker"], var, str(base),
                     entry.get("direction") or "", entry.get("scene") or "", entry.get("target_s"))
    return run


class _Lock:
    """同一集的库目录文件锁(fcntl 独占;非 POSIX 退化为空操作)。"""

    def __init__(self, path: Path):
        self.path, self.fh = path, None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "a+")
        try:
            import fcntl
            fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX)
        except (ImportError, OSError):
            pass
        return self

    def __exit__(self, *exc):
        try:
            import fcntl
            fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
        except (ImportError, OSError):
            pass
        self.fh.close()


def sync(base: Path, ep: str, *, force: bool = False, tts=None, probe=None, log=print, only_shots=None,
         speed: float | None = None, trim: bool = True, max_pause: float | None = None,
         max_tempo: float | None = None) -> dict:
    """把库同步到当前台词:只合成 stale/missing(force=全部重出),删掉的句子文件移到 _prev/,写 tts_manifest.json。
    tts(entry, out_path) / probe(path)->秒 可注入(测试或替换合成器);任一句合成失败记 status=failed 不中断。
    only_shots:仅同步这些镜(后期配音按组取用时用),其余句子沿用旧台账记录。
    speed:本次默认语速倍率(None=项目设置);trim:合成后裁首尾静音(原声留 _raw/);max_pause:句中停顿上限秒
    (None=项目设置 output.dialogue_tts_max_pause,0=不压缩)。已有句子修剪参数变了只从 _raw/ 重裁,不重新合成。
    max_tempo:节奏贴合变速倍率上限(None=项目设置 output.dialogue_tts_max_tempo,1.0=不出贴合版);贴合版落 _paced/,
    库文件本身保持自然语速。"""
    base = Path(base)
    ldir = lib_dir(base, ep)
    rdir = ldir / RAW_DIR
    pdir = ldir / PACED_DIR
    max_pause = float(max_pause) if max_pause is not None else default_max_pause(base)
    max_tempo = min(TEMPO_RANGE[1], max(TEMPO_RANGE[0], float(max_tempo))) if max_tempo is not None else default_max_tempo(base)
    with _Lock(ldir / ".lock"):
        p = plan(base, ep, speed=speed)
        provider, model = p["provider"], p["model"]
        by_char = voice_mode(provider, model)[0] == "design"   # 音色设计:不传 casting 的音色,生成层按人物取样本
        tts = tts or _default_tts(base)
        probe = probe or probe_duration
        old_lines = {e.get("file"): e for e in (p["manifest"] or {}).get("lines") or [] if isinstance(e, dict)}
        done, failed, synth, retrim, paced = [], 0, 0, 0, 0
        t0 = time.time()

        def pace(e: dict, prev: dict, changed: bool) -> None:
            """节奏贴合版:自然时长超出估时的句子才出;参数与上次一致且文件在就沿用。失败只 WARN,样片退回自然语速。"""
            nonlocal paced
            target, dur = e.get("target_s") or e.get("est_duration_s"), e.get("duration_s")   # 有演法的句子贴目标时长
            dst = pdir / e["file"]
            e["pace"] = None
            if max_tempo <= 1.0 or not target or not dur or dur <= target * (1 + PACE_TOLERANCE) or not shutil.which("ffmpeg"):
                dst.unlink(missing_ok=True)
                return
            old = prev.get("pace") if isinstance(prev.get("pace"), dict) else None
            if not changed and old and old.get("params") == pace_params(target, max_tempo) and dst.is_file():
                e["pace"] = old
                return
            try:
                src = rdir / e["file"] if (rdir / e["file"]).is_file() else ldir / e["file"]
                e["pace"] = dict(pace_file(src, dst, target, max_tempo=max_tempo, probe=probe), file=f"{PACED_DIR}/{e['file']}")
                paced += 1
                log(f"[dialogue-tts] 贴合 {e['shot_id']} l{e['idx']:02d} {e['speaker']} {dur:.2f}s → {e['pace']['duration_s']:.2f}s"
                    f"(估时 {target:.2f}s,压停顿 {e['pace']['pause_s']:.2f}s,x{e['pace']['tempo']:g})")
            except Exception as err:  # noqa: BLE001
                dst.unlink(missing_ok=True)
                log(f"[dialogue-tts] WARN 节奏贴合失败 {e['shot_id']} l{e['idx']:02d}:{str(err)[:160]}")

        def finish(e: dict, raw: Path, out: Path) -> float | None:
            """raw → out(修剪或原样),回填 trim 段与时长。"""
            if trim:
                e["trim"] = trim_file(raw, out, max_pause=max_pause, probe=probe)
            else:
                if raw.resolve() != out.resolve():
                    shutil.copyfile(raw, out)
                e["trim"] = None
            return probe(out)

        for e in p["lines"]:
            e = dict(e)
            e.pop("speaker_raw", None)
            if e["status"] == "unbound":
                log(f"[dialogue-tts] WARN {e['shot_id']} l{e['idx']:02d} 跳过:{e['reason']}")
                done.append(e)
                continue
            need = force or e["status"] in ("stale", "missing")
            if only_shots is not None and e["shot_id"] not in only_shots and e["status"] != "fresh":
                prev = old_lines.get(e["file"]) or {}
                e.update(status=prev.get("status") or "missing", duration_s=prev.get("duration_s"),
                         generated_at=prev.get("generated_at"), reason=prev.get("reason", ""), trim=prev.get("trim"),
                         pace=prev.get("pace"))
                done.append(e)
                continue
            out = ldir / e["file"]
            raw = rdir / e["file"]
            if not need:
                prev = old_lines.get(e["file"]) or {}
                e.update(status="ok", trim=prev.get("trim"))
                # 修剪参数变了(或旧库从未修剪):从 _raw/ 重裁;没有原声则就地裁库文件(首尾静音再裁无损失)
                want = {"noise_db": TRIM_NOISE_DB, "keep_head": TRIM_KEEP_HEAD, "keep_tail": TRIM_KEEP_TAIL,
                        "max_pause": round(max_pause, 3)} if trim else None
                have = (prev.get("trim") or {}).get("params") if prev.get("trim") else None
                changed = False
                if trim and have != want:
                    try:
                        src = raw if raw.is_file() else out
                        if src is out:
                            rdir.mkdir(parents=True, exist_ok=True)
                            shutil.copyfile(out, raw)
                            src = raw
                        dur = finish(e, src, out)
                        e["duration_s"] = round(dur, 3) if dur is not None else e.get("duration_s")
                        retrim += 1
                        changed = True
                        log(f"[dialogue-tts] 重裁 {e['shot_id']} l{e['idx']:02d} {e['speaker']} → {dur if dur is None else f'{dur:.2f}s'}")
                    except Exception as err:  # noqa: BLE001  重裁失败沿用现有文件
                        log(f"[dialogue-tts] WARN 重裁失败 {e['shot_id']} l{e['idx']:02d}:{str(err)[:160]}")
                pace(e, prev, changed)
                done.append(e)
                continue
            e["_voice_by_character"] = by_char
            try:
                ldir.mkdir(parents=True, exist_ok=True)
                rdir.mkdir(parents=True, exist_ok=True)
                tts(e, raw)
                if not raw.is_file() or raw.stat().st_size == 0:
                    raise RuntimeError("合成器没有产出文件")
                dur = finish(e, raw, out)
                e.update(status="ok", duration_s=round(dur, 3) if dur is not None else None,
                         generated_at=time.strftime("%Y-%m-%dT%H:%M:%S"), reason="")
                synth += 1
                tr = e.get("trim") or {}
                cut = (tr.get("lead_s") or 0) + (tr.get("tail_s") or 0) + (tr.get("pause_s") or 0)
                log(f"[dialogue-tts] {e['shot_id']} l{e['idx']:02d} {e['speaker']}/{e['variant']} "
                    f"{dur if dur is None else f'{dur:.2f}s'}{f' (裁 {cut:.2f}s)' if cut else ''} x{e['speed']:g}  {e['text'][:24]}")
                pace(e, {}, True)
            except Exception as err:  # noqa: BLE001  单句失败不中断整集
                failed += 1
                e.update(status="failed", reason=str(err)[:300], duration_s=None, pace=None)
                log(f"[dialogue-tts] FAIL {e['shot_id']} l{e['idx']:02d} {e['speaker']}:{str(err)[:200]}")
            e.pop("_voice_by_character", None)
            done.append(e)
        # 台词里已没有的句子:文件移到 _prev/(不直接删,便于回看)
        if p["removed"]:
            prev_dir = ldir / "_prev"
            prev_dir.mkdir(parents=True, exist_ok=True)
            for f in p["removed"]:
                src = ldir / f
                if src.is_file():
                    dst = prev_dir / f
                    if dst.exists():
                        dst = prev_dir / f"{Path(f).stem}.{int(time.time())}{Path(f).suffix}"
                    os.replace(src, dst)
                (rdir / f).unlink(missing_ok=True)
                (pdir / f).unlink(missing_ok=True)
        est_warn = []
        for e in done:
            est, act = e.get("target_s") or e.get("est_duration_s"), e.get("duration_s")
            if e["status"] == "ok" and est and act and abs(act - est) / est > EST_TOLERANCE:
                est_warn.append({"shot_id": e["shot_id"], "idx": e["idx"], "speaker": e["speaker"],
                                 "est_duration_s": est, "actual_s": act, "ratio": round(act / est, 2)})
        sl_path = base / "directing" / ep / "shot_list.json"
        manifest = {
            "schema": SCHEMA, "ep": ep, "generated_by": "modules/dialogue_tts.py",
            "note": "对白语音库:按 shot_list dialogue_lines 逐句、人物嗓音模板合成的自然语速 TTS(库文件已裁首尾静音,合成原声在 _raw/;样片用的节奏贴合版在 _paced/,见逐句 pace 段);消费方(动态样片/白模样片/后期配音)只读此表",
            "source": f"directing/{ep}/shot_list.json", "source_mtime": int(sl_path.stat().st_mtime) if sl_path.is_file() else 0,
            "tts_provider": provider, "tts_model": model, "synced_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "sync_seconds": round(time.time() - t0, 1), "synthesized": synth, "retrimmed": retrim,
            "default_speed": p["speed"], "trim": {"enabled": bool(trim), "max_pause": round(max_pause, 3)},
            "pace": {"max_tempo": round(max_tempo, 3), "max_pause": PACE_MAX_PAUSE, "repaced": paced,
                     "lines": sum(1 for e in done if e.get("pace"))},
            "lines": done,
            "summary": {"total": len(done), "ok": sum(1 for e in done if e["status"] == "ok"),
                        "unbound": sum(1 for e in done if e["status"] == "unbound"), "failed": failed,
                        "removed": len(p["removed"])},
            "checks": {"dialogue_tts_all_bound": all(e["status"] == "ok" for e in done),
                       "unbound_lines": [f"{e['shot_id']}/l{e['idx']:02d}" for e in done if e["status"] == "unbound"],
                       "failed_lines": [f"{e['shot_id']}/l{e['idx']:02d}" for e in done if e["status"] == "failed"],
                       "est_vs_actual": est_warn,
                       # 形态判定(modules/voice_variants.py):逐人物 {形态: 判定来源};prompt 与章节范围不一致的告警;
                       # 没挂参考样本(纯描述出声,句与句之间嗓子会漂)的说话人
                       "speaker_variants": _speaker_variants(done),
                       "variant_warnings": sorted({e["variant_warning"] for e in done if e.get("variant_warning")}),
                       # 台词演法(modules/dialogue_direction.py):没写演法的句子仍按旧写法(只带情绪标签)合成
                       "undirected_lines": [f"{e['shot_id']}/l{e['idx']:02d}" for e in done
                                            if e["status"] == "ok" and not e.get("direction")],
                       "unanchored_speakers": sorted({e["speaker"] for e in done
                                                      if e["status"] == "ok" and e.get("anchored") is False})
                       if voice_mode(provider, model)[0] == "design" else []},
        }
        ldir.mkdir(parents=True, exist_ok=True)
        tmp = ldir / (MANIFEST + ".tmp")
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, ldir / MANIFEST)
    return manifest


def ensure(base: Path, ep: str, *, log=print, **kw) -> dict | None:
    """消费方入口:开关关闭返回 None;开启则同步(只补缺/过期)并返回台账。合成异常不抛出(返回已有台账或 None)。"""
    base = Path(base)
    if not enabled(base):
        return None
    try:
        return sync(base, ep, log=log, **kw)
    except Exception as err:  # noqa: BLE001
        log(f"[dialogue-tts] 同步失败,改用现有库:{err}")
        return load_manifest(base, ep)


def line_audio(base: Path, ep: str, manifest: dict | None = None, paced: bool = False) -> dict[tuple[str, int], dict]:
    """可用的逐句音频:{(shot_id, idx): {path(绝对路径), duration_s, speaker, text, ...}},只含 status=ok 且文件存在的句子。
    paced=True(样片用):有节奏贴合版的句子改给贴合版的 path/duration_s,自然时长另记 natural_duration_s;
    后期配音用默认的自然语速版。"""
    base = Path(base)
    m = manifest if manifest is not None else load_manifest(base, ep)
    ldir = lib_dir(base, ep)
    out = {}
    for e in (m or {}).get("lines") or []:
        if not (isinstance(e, dict) and e.get("status") == "ok" and e.get("file")):
            continue
        f = ldir / e["file"]
        if f.is_file() and e.get("duration_s"):
            item = dict(e, path=f)
            pc = e.get("pace") if paced and isinstance(e.get("pace"), dict) else None
            if pc and pc.get("file") and pc.get("duration_s") and (ldir / pc["file"]).is_file():
                item.update(path=ldir / pc["file"], duration_s=pc["duration_s"], natural_duration_s=e["duration_s"],
                            tempo=pc.get("tempo"))
            out[(e["shot_id"], int(e["idx"]))] = item
    return out


def library_fingerprint(manifest: dict | None) -> str:
    """库内容指纹(进样片清单,任一句音频换了样片判过期)。有节奏贴合版的句子把贴合时长/倍率也算进去
    (没有贴合版的句子指纹口径不变,存量样片不会因此集体判过期)。"""
    def item(e: dict) -> tuple:
        pc = e.get("pace") if isinstance(e.get("pace"), dict) else None
        return (e.get("file"), e.get("key"), e.get("status"), e.get("duration_s")) + ((pc.get("duration_s"), pc.get("tempo")) if pc else ())
    payload = sorted(item(e) for e in (manifest or {}).get("lines") or [] if isinstance(e, dict))
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode("utf-8")).hexdigest() if payload else ""
