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
- 嗓音模板:走 modules/genmedia.generate_tts(character/variant/project 三参数),与 dub_group 同一套——
  seed-audio 按声纹卡描述+项目 voiceprint 样本锚定,ComfyUI 自动选参考音频,云渠道用 casting.json 的音色 ID。
  speaker 不是 CHAR-/CRE- 编号、或云渠道缺 casting 条目 → 该句 status=unbound 跳过并 WARN(样片照出、不阻断)。
- 顺带机检:实测时长与 est_duration_s 偏差 >30% 记入 checks.est_vs_actual(WARN 级,给分镜规划做反馈)。
- 语速(2026-09-15):每句 speed = casting 条目的数字 speed(倍率,如 1.15;描述文字视为未填)> 项目输出设置
  output.dialogue_tts_speed(默认 1.0;CLI --speed 临时覆盖该默认值)。speed 进 key,改了自动重出。
- 静音修剪(2026-09-15):合成原声落 _raw/,库文件为裁掉首尾静音的版本(首留 0.10s、尾留 0.15s;seed-audio
  首尾常各带 0.4–1.0s 空白,短句尤甚);output.dialogue_tts_max_pause>0 时句中超过该秒数的停顿也压到该值(默认 0=不动)。
  修剪是后处理,不进 key:参数变了从 _raw/ 重裁,不重新调 TTS;台账每句记 trim{lead_s,tail_s,pause_s,params}。
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
_ID_RE = re.compile(r"^(CHAR|CRE)-\d+$")
_VP_RE = re.compile(r"(CHAR-\d+)(?:_([A-Za-z0-9-]+))?_voiceprint")
EST_TOLERANCE = 0.30      # 实测/估时偏差超过该比例记 WARN
RAW_DIR = "_raw"          # 合成原声(未修剪)存放子目录
TRIM_NOISE_DB = -35.0     # 静音判定阈值
TRIM_MIN_SIL = 0.20       # 短于此的空白不算静音段
TRIM_KEEP_HEAD = 0.10     # 开头保留的静音
TRIM_KEEP_TAIL = 0.15     # 结尾保留的静音
SPEED_RANGE = (0.5, 2.0)  # 语速倍率合法区间(火山 speech_rate -50..100 / minimax 0.5..2.0 同口径)


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
    return provider, model


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
                if isinstance(a, str) and a.strip():
                    out.setdefault(a.strip(), c["id"])
    cr = _read(Path(base) / "bible" / "creatures" / "index.json") or {}
    for c in cr.get("creatures") or []:
        if isinstance(c, dict) and c.get("id") and c.get("name"):
            out.setdefault(str(c["name"]).strip(), c["id"])
    return out


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
            raw = str(ln.get("speaker") or ln.get("character_id") or "").strip()
            m = re.match(r"^((?:CHAR|CRE)-\d+)", raw)
            speaker = m.group(1) if m else names.get(raw, "")
            out.append({"seq": seq, "shot_id": s["shot_id"], "idx": idx, "group_id": group_of.get(s["shot_id"], ""),
                        "speaker": speaker, "speaker_raw": raw, "text": text,
                        "emotion": str(ln.get("emotion") or ln.get("tone") or "").strip(),
                        "est_duration_s": _f(ln.get("est_duration_s"))})
            idx += 1
            seq += 1
    return out


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


def infer_variants(base: Path, ep: str) -> dict[str, dict[str, str]]:
    """各组说话人形态:{group_id: {CHAR: variant}},按组 prompt audio_refs 样本文件名 <CHAR>[_<variant>]_voiceprint 推断
    (与 dub_group.infer_variants 同一规则)。"""
    out: dict[str, dict[str, str]] = {}
    pdir = Path(base) / "assets" / "prompts" / ep
    if not pdir.is_dir():
        return out
    for p in sorted(pdir.glob("grp*.json")):
        d = _read(p) or {}
        res = {}
        for ref in d.get("audio_refs") or []:
            m = _VP_RE.match(Path(str(ref)).name)
            if m:
                res[m.group(1)] = m.group(2) or "default"
        if res:
            out[p.stem] = res
    return out


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


def _desc_mode(provider: str, model: str) -> bool:
    return provider == "volcengine" and model == "seed-audio-1.0"


# ---------------------------------------------------------------- 计划(纯读,不合成)

def plan(base: Path, ep: str, shot_list: dict | None = None, channel: tuple[str, str] | None = None,
         speed: float | None = None) -> dict:
    """逐句计算 key 与现状:status ∈ fresh(库里有且 key 一致)| stale(key 变了)| missing(未合成)| unbound(不能合成)。
    speed:本次默认语速倍率(None=项目设置 output.dialogue_tts_speed);casting 条目有数字 speed 的句子优先用条目值。
    返回 {lines:[...], manifest, provider, model, speed, removed:[旧台账里已不在台词中的文件]}。"""
    base = Path(base)
    provider, model = channel if channel else tts_channel()
    desc = _desc_mode(provider, model)
    base_speed = num_speed(speed) or default_speed(base)
    casting = load_casting(base)
    variants = infer_variants(base, ep)
    old = load_manifest(base, ep) or {}
    old_by_file = {e.get("file"): e for e in old.get("lines") or [] if isinstance(e, dict) and e.get("file")}
    ldir = lib_dir(base, ep)
    sha_cache: dict = {}
    lines = []
    for ln in collect_lines(base, ep, shot_list):
        ch = ln["speaker"]
        var = (variants.get(ln["group_id"]) or {}).get(ch, "default") if ch else "default"
        entry = dict(ln, variant=var, reason="", tts_voice="", tts_model="", speed=base_speed, key="", file="")
        if not ch:
            entry.update(status="unbound", reason=f"speaker 不是人物/生物编号:{ln['speaker_raw'] or '(空)'}")
            lines.append(entry)
            continue
        c = casting.get((ch, var)) or casting.get((ch, "default")) or {}
        if c:
            entry.update(tts_voice=str(c.get("tts_voice") or ""), tts_model=str(c.get("tts_model") or ""),
                         speed=num_speed(c.get("speed")) or base_speed)
        vp = voiceprint_path(base, ch, var)
        if not c and not (desc or provider == "comfyui"):
            # 云渠道按音色 ID 合成,没有选角条目就没有该角色的嗓音;不用默认音色顶替(会全员同声)
            entry.update(status="unbound", reason=f"casting.json 无 {ch}/{var} 条目(云渠道 {provider or '?'} 需先选角)")
            lines.append(entry)
            continue
        if not c and not vp and not (base / "bible" / "characters" / ch / "voice.json").is_file():
            entry.update(status="unbound", reason=f"{ch} 既无 casting 条目也无声纹卡/voiceprint 样本")
            lines.append(entry)
            continue
        payload = "|".join([ch, var, ln["text"], ln["emotion"], entry["tts_model"], entry["tts_voice"],
                            f"{entry['speed']:.3f}", str(c.get("voice_desc") or ""), provider, model, _sha_file(vp, sha_cache)])
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


def apply_trim(src: Path, dst: Path, keep: list[tuple[float, float]]) -> None:
    """按 keep 区间 atrim+concat 重编码到 dst(mp3 128k / 其它按扩展名默认编码器)。"""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("缺 ffmpeg,无法修剪静音")
    parts = "".join(f"[0:a]atrim=start={a}:end={b},asetpts=PTS-STARTPTS[s{i}];" for i, (a, b) in enumerate(keep))
    chain = "".join(f"[s{i}]" for i in range(len(keep)))
    fc = f"{parts}{chain}concat=n={len(keep)}:v=0:a=1[out]"
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


def _default_tts(base: Path):
    from modules.genmedia import generate_tts

    def run(entry: dict, out: Path):
        var = entry["variant"] if entry["variant"] != "default" else ""
        voice = "" if entry.get("_voice_by_character") else entry["tts_voice"]
        generate_tts(entry["text"], str(out), voice, entry["speed"], entry["emotion"], entry["speaker"], var, str(base))
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
         speed: float | None = None, trim: bool = True, max_pause: float | None = None) -> dict:
    """把库同步到当前台词:只合成 stale/missing(force=全部重出),删掉的句子文件移到 _prev/,写 tts_manifest.json。
    tts(entry, out_path) / probe(path)->秒 可注入(测试或替换合成器);任一句合成失败记 status=failed 不中断。
    only_shots:仅同步这些镜(后期配音按组取用时用),其余句子沿用旧台账记录。
    speed:本次默认语速倍率(None=项目设置);trim:合成后裁首尾静音(原声留 _raw/);max_pause:句中停顿上限秒
    (None=项目设置 output.dialogue_tts_max_pause,0=不压缩)。已有句子修剪参数变了只从 _raw/ 重裁,不重新合成。"""
    base = Path(base)
    ldir = lib_dir(base, ep)
    rdir = ldir / RAW_DIR
    max_pause = float(max_pause) if max_pause is not None else default_max_pause(base)
    with _Lock(ldir / ".lock"):
        p = plan(base, ep, speed=speed)
        provider, model = p["provider"], p["model"]
        by_char = _desc_mode(provider, model) or provider == "comfyui"
        tts = tts or _default_tts(base)
        probe = probe or probe_duration
        old_lines = {e.get("file"): e for e in (p["manifest"] or {}).get("lines") or [] if isinstance(e, dict)}
        done, failed, synth, retrim = [], 0, 0, 0
        t0 = time.time()

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
                         generated_at=prev.get("generated_at"), reason=prev.get("reason", ""), trim=prev.get("trim"))
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
                        log(f"[dialogue-tts] 重裁 {e['shot_id']} l{e['idx']:02d} {e['speaker']} → {dur if dur is None else f'{dur:.2f}s'}")
                    except Exception as err:  # noqa: BLE001  重裁失败沿用现有文件
                        log(f"[dialogue-tts] WARN 重裁失败 {e['shot_id']} l{e['idx']:02d}:{str(err)[:160]}")
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
            except Exception as err:  # noqa: BLE001  单句失败不中断整集
                failed += 1
                e.update(status="failed", reason=str(err)[:300], duration_s=None)
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
        est_warn = []
        for e in done:
            est, act = e.get("est_duration_s"), e.get("duration_s")
            if e["status"] == "ok" and est and act and abs(act - est) / est > EST_TOLERANCE:
                est_warn.append({"shot_id": e["shot_id"], "idx": e["idx"], "speaker": e["speaker"],
                                 "est_duration_s": est, "actual_s": act, "ratio": round(act / est, 2)})
        sl_path = base / "directing" / ep / "shot_list.json"
        manifest = {
            "schema": SCHEMA, "ep": ep, "generated_by": "modules/dialogue_tts.py",
            "note": "对白语音库:按 shot_list dialogue_lines 逐句、人物嗓音模板合成的自然语速 TTS(库文件已裁首尾静音,合成原声在 _raw/);消费方(动态样片/白模样片/后期配音)只读此表",
            "source": f"directing/{ep}/shot_list.json", "source_mtime": int(sl_path.stat().st_mtime) if sl_path.is_file() else 0,
            "tts_provider": provider, "tts_model": model, "synced_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "sync_seconds": round(time.time() - t0, 1), "synthesized": synth, "retrimmed": retrim,
            "default_speed": p["speed"], "trim": {"enabled": bool(trim), "max_pause": round(max_pause, 3)},
            "lines": done,
            "summary": {"total": len(done), "ok": sum(1 for e in done if e["status"] == "ok"),
                        "unbound": sum(1 for e in done if e["status"] == "unbound"), "failed": failed,
                        "removed": len(p["removed"])},
            "checks": {"dialogue_tts_all_bound": all(e["status"] == "ok" for e in done),
                       "unbound_lines": [f"{e['shot_id']}/l{e['idx']:02d}" for e in done if e["status"] == "unbound"],
                       "failed_lines": [f"{e['shot_id']}/l{e['idx']:02d}" for e in done if e["status"] == "failed"],
                       "est_vs_actual": est_warn},
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


def line_audio(base: Path, ep: str, manifest: dict | None = None) -> dict[tuple[str, int], dict]:
    """可用的逐句音频:{(shot_id, idx): {file(绝对路径), duration_s, speaker, text, ...}},只含 status=ok 且文件存在的句子。"""
    base = Path(base)
    m = manifest if manifest is not None else load_manifest(base, ep)
    ldir = lib_dir(base, ep)
    out = {}
    for e in (m or {}).get("lines") or []:
        if not (isinstance(e, dict) and e.get("status") == "ok" and e.get("file")):
            continue
        f = ldir / e["file"]
        if f.is_file() and e.get("duration_s"):
            out[(e["shot_id"], int(e["idx"]))] = dict(e, path=f)
    return out


def library_fingerprint(manifest: dict | None) -> str:
    """库内容指纹(进样片清单,任一句音频换了样片判过期)。"""
    payload = sorted((e.get("file"), e.get("key"), e.get("status"), e.get("duration_s"))
                     for e in (manifest or {}).get("lines") or [] if isinstance(e, dict))
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode("utf-8")).hexdigest() if payload else ""
