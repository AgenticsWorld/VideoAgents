"""Deterministic character-to-reference-audio selection for local TTS."""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from urllib.parse import quote

try:
    from modules.entity_ids import known_actor_ids, normalize_actor_id
except ImportError:                                      # 脚本直跑时无包前缀
    from entity_ids import known_actor_ids, normalize_actor_id


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(
    os.environ.get("VIDEOAGENTS_DATA_DIR") or ROOT / "data"
).expanduser().resolve()
DEFAULT_TIMBRE_DIR = DATA_DIR / "TimbreModel"
DEFAULT_CATALOG = DEFAULT_TIMBRE_DIR / "catalog.json"
# 音色库不再随仓库携带音频文件:内置目录索引远端
# https://github.com/chenpipi0807/ComfyUI-Index-TTS/tree/main/TimbreModel,
# 选中的参考音频按需下载并缓存到 data/TimbreModel/。
BUNDLED_CATALOG = Path(__file__).resolve().parent / "timbre_catalog.json"
AUDIO_SUFFIXES = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}
DOWNLOAD_TIMEOUT = 300


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _normalize_character(value: str) -> str:
    """人物参数 / 输出文件名 → CHAR 编号:数字编号 `CHAR-0001` / `char_0001`(下划线旧写法)照旧;
    拼音 / 英文 slug `CHAR-jie-rui-er`(连字符分段,遇 `_` 截断:`CHAR-alice-sister_young_voiceprint`)也认(#117)。"""
    match = re.search(r"CHAR(?:[-_](\d+)|-([A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*))", value or "", re.IGNORECASE)
    if not match:
        return ""
    return f"CHAR-{match.group(1)}" if match.group(1) else f"CHAR-{match.group(2)}"


def infer_character(output: str) -> str:
    return _normalize_character(Path(output).name)


def _resolve_project(project: str, output: str) -> Path | None:
    explicit_root = (os.environ.get("VIDEOAGENTS_PROJECT_ROOT") or "").strip()
    if explicit_root:
        candidate = Path(explicit_root).expanduser().resolve()
        if candidate.is_dir():
            return candidate

    raw = (project or os.environ.get("VIDEOAGENTS_PROJECT")
           or os.environ.get("WEBUI_PROJECT") or "").strip()
    if raw:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = DATA_DIR / "projects" / raw
        if candidate.is_dir():
            return candidate

    try:
        resolved = Path(output).resolve()
    except OSError:
        return None
    projects = DATA_DIR / "projects"
    for candidate in projects.iterdir() if projects.is_dir() else ():
        try:
            resolved.relative_to(candidate.resolve())
            return candidate
        except (OSError, ValueError):
            continue
    return None


def _load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _profile(project_root: Path | None, character: str, variant: str,
             text: str, instructions: str) -> dict:
    documents = []
    voice = {}
    if project_root and character:
        base = project_root / "bible" / "characters" / character
        for name in ("voice.json", "personality.json", "appearance.json"):
            doc = _load_json(base / name)
            if doc:
                if name == "voice.json":
                    voice = doc
                    documents.append({k: value for k, value in doc.items()
                                      if k != "age_variants"})
                else:
                    documents.append(doc)

    selected_variant = {}
    if variant:
        wanted = variant.casefold()
        for item in voice.get("age_variants") or []:
            identity = " ".join(str(item.get(k) or "")
                                for k in ("id", "name", "variant", "label"))
            if wanted in identity.casefold():
                selected_variant = item
                documents.append(item)
                break

    voice_base = {k: value for k, value in voice.items() if k != "age_variants"}
    voice_text = " ".join(_strings([voice_base, selected_variant]))
    combined = " ".join([text, instructions, variant]
                        + [part for doc in documents for part in _strings(doc)])
    gender_raw = str(voice.get("presented_gender") or voice.get("gender") or "")
    if not gender_raw:
        gender_raw = combined
    gender = ""
    if any(token in gender_raw for token in ("女", "female", "woman", "girl")):
        gender = "female"
    elif any(token in gender_raw for token in ("男", "male", "man", "boy")):
        gender = "male"

    pitch_raw = str(selected_variant.get("pitch") or voice.get("pitch") or combined)
    pitch = ""
    if any(token in pitch_raw for token in ("高", "尖", "high")):
        pitch = "high"
    elif any(token in pitch_raw for token in ("低", "沉", "low")):
        pitch = "low"
    elif any(token in pitch_raw for token in ("中", "mid")):
        pitch = "mid"

    age = "adult"
    if any(token in combined for token in ("老年", "年迈", "奶奶", "爷爷", "大爷", "花甲", "elderly")):
        age = "elderly"
    elif any(token in combined for token in ("儿童", "童声", "幼年", "孩童", "child")):
        age = "child"
    elif any(token in combined for token in ("少年", "少女", "青年", "young", "teen")):
        age = "young"
    elif any(token in combined for token in ("中年", "middle-aged")):
        age = "middle"

    return {
        "character": character,
        "variant": variant or "default",
        "gender": gender,
        "pitch": pitch,
        "age": age,
        "text": combined.casefold(),
        "voice_text": voice_text.casefold(),
        "has_character_profile": bool(documents),
    }


def _resolve_config_path(value: str | Path, default: Path) -> Path:
    path = Path(value) if value else default
    return path if path.is_absolute() else ROOT / path


def load_catalog(timbre_dir: str | Path = "",
                 catalog_path: str | Path = "") -> tuple[Path, list[dict]]:
    directory = _resolve_config_path(timbre_dir, DEFAULT_TIMBRE_DIR)
    catalog = _resolve_config_path(catalog_path, directory / "catalog.json")
    data = _load_json(catalog)
    if not data.get("entries"):
        catalog = BUNDLED_CATALOG
        data = _load_json(catalog)
    remote_base = str(data.get("remote_base") or "").rstrip("/")
    entries = []
    for item in data.get("entries") or []:
        if not isinstance(item, dict) or not item.get("file"):
            continue
        name = str(item["file"])
        path = directory / name
        if path.suffix.lower() not in AUDIO_SUFFIXES:
            continue
        url = str(item.get("url") or "")
        if not url and remote_base:
            url = f"{remote_base}/{quote(name)}"
        if path.is_file() or url:
            entries.append({**item, "path": path, "url": url})
    if not entries:
        raise RuntimeError(f"音色目录没有可用索引:{catalog}")
    return directory, entries


def _ensure_audio(entry: dict) -> Path:
    path = entry["path"]
    if path.is_file():
        return path
    url = entry.get("url")
    if not url:
        raise RuntimeError(f"参考音频缺失且无远端来源:{path}")
    print(f"[timbre] 下载参考音频:{entry['file']}{path}", file=sys.stderr)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    try:
        with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT) as resp:
            tmp.write_bytes(resp.read())
        tmp.replace(path)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"下载参考音频失败:{url}{exc}") from exc
    return path


def find_timbre(name: str, timbre_dir: str | Path = "", catalog_path: str | Path = "") -> Path | None:
    """按目录条目文件名取参考音频(选角表 casting.json 登记的本地音色库音色;首次使用自动下载)。
    不在目录里返回 None。"""
    wanted = Path(str(name or "")).name.casefold()
    if not wanted:
        return None
    try:
        _, entries = load_catalog(timbre_dir, catalog_path)
    except RuntimeError:
        return None
    for entry in entries:
        if str(entry["file"]).casefold() == wanted:
            return _ensure_audio(entry)
    return None


def rank_timbres(text: str, output: str, character: str = "", variant: str = "",
                 project: str = "", instructions: str = "",
                 timbre_dir: str | Path = "", catalog_path: str | Path = "") -> tuple[dict, list]:
    """本地音色库按人物内容打分排序 → (人物画像, [(分数, 条目, 理由列表)] 高分在前)。"""
    character = _normalize_character(character) or infer_character(output)
    project_root = _resolve_project(project, output)
    if character and project_root:
        character = normalize_actor_id(character, known_actor_ids(project_root))   # slug 后缀形态 → 已登记编号
    profile = _profile(project_root, character, variant, text, instructions)
    _, entries = load_catalog(timbre_dir, catalog_path)

    if character and not profile["has_character_profile"]:
        location = project_root or "未识别项目"
        raise RuntimeError(f"无法读取 {character} 的角色内容:{location}")

    is_narrator = not character
    ranked = []
    for entry in entries:
        entry_gender = str(entry.get("gender") or "neutral")
        if profile["gender"] and entry_gender not in (profile["gender"], "neutral"):
            continue
        score = int(entry.get("priority") or 0)
        reasons = []
        if profile["gender"] and entry_gender == profile["gender"]:
            score += 30
            reasons.append(f"gender={entry_gender}")
        ages = entry.get("ages") or [entry.get("age") or "adult"]
        if profile["age"] in ages:
            score += 8
            reasons.append(f"age={profile['age']}")
        elif "all" not in ages:
            score -= 3
        pitch = str(entry.get("pitch") or "")
        if profile["pitch"] and pitch == profile["pitch"]:
            score += 7
            reasons.append(f"pitch={pitch}")
        roles = entry.get("roles") or []
        if is_narrator and "narrator" in roles:
            score += 16
            reasons.append("narrator")
        matched = []
        avoided = []
        for raw_tag in entry.get("tags") or []:
            tag = str(raw_tag).casefold()
            if any(prefix + tag in profile["text"]
                   for prefix in ("不", "非", "避免", "不要", "拒绝",
                                  "no ", "not ", "avoid ", "avoiding ", "without ", "never ", "don't ", "do not ", "isn't ", "is not ")):   # 英文卡片否定写法
                score -= 10
                avoided.append(str(raw_tag))
            elif tag in profile["voice_text"]:
                score += 8
                matched.append(str(raw_tag))
            elif tag in profile["text"]:
                score += 2
                matched.append(str(raw_tag))
        if matched:
            reasons.append("tags=" + "/".join(matched[:5]))
        if avoided:
            reasons.append("avoid=" + "/".join(avoided[:3]))
        ranked.append((score, str(entry["file"]).casefold(), entry, reasons))

    if not ranked:
        raise RuntimeError(f"TimbreModel 中没有匹配性别的音色(character={character or 'narrator'})")
    profile["character"] = character
    return profile, [(score, entry, reasons)
                     for score, _, entry, reasons in sorted(ranked, key=lambda row: (-row[0], row[1]))]


def select_timbre(text: str, output: str, character: str = "", variant: str = "",
                   project: str = "", instructions: str = "",
                   timbre_dir: str | Path = "", catalog_path: str | Path = "") -> dict:
    profile, ranked = rank_timbres(text, output, character, variant, project, instructions,
                                   timbre_dir, catalog_path)
    character = profile["character"]
    score, selected, reasons = ranked[0]
    return {
        "path": str(_ensure_audio(selected)),
        "file": selected["file"],
        "score": score,
        "reason": ", ".join(reasons) or "catalog priority",
        "character": character or "narrator",
        "variant": profile["variant"],
        "profile": {k: profile[k] for k in ("gender", "age", "pitch")},
    }
