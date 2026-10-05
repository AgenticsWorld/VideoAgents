"""资产 → 出现分集 反查(场景/人物/生物/道具 预览页的「按集过滤」下拉,2026-09-17)。

各集语料 = 剧本层 + 导演层 + 提示词层的一组定长文件(见 _EP_FILES / _EP_GLOBS;白模/备份等大文件不读)。
命中规则:
  ① 语料里出现资产 ID(右侧须非字母数字,避免 PROP-0001 命中 PROP-00010);
  ② 设定卡自带 episodes[] 声明(道具卡常见);
  ③ 名字命中:道具/生物按 名字+别名(≥2 字);人物/场景只按规范名,且仅当该集语料里完全没有该类 ID 时才启用
     (老项目剧本不写 ID;人物/场景别名含「我」「家中」之类,不能拿来匹配)。
结果按集缓存:签名 = 该集语料文件 (路径, mtime, size) + 资产目录内容,哪一集的文件变了只重扫那一集
(整项目一个签名时,agent 正在写某一集就会让每次请求都全量重扫——64 集的项目一次 5 秒多)。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

KINDS = ("scenes", "characters", "creatures", "props")

_EP_FILES = ("story/episodes/{ep}/screenplay.md", "story/episodes/{ep}/dialogue.md",
             "story/episodes/{ep}/narration.md", "story/episodes/{ep}/script_breakdown.json",
             "directing/{ep}/shot_list.json", "directing/{ep}/storyboard.json",
             "directing/{ep}/directing_plan.md", "directing/{ep}/continuity_plan.json",
             "directing/{ep}/concept_coverage.json")
_EP_GLOBS = ("directing/{ep}/shots/*/blocking.json", "assets/prompts/{ep}/*.json")
_EP_DIR_RE = re.compile(r"^ep\d+[A-Za-z]?$")
_MAX_BYTES = 8 * 1024 * 1024
_CACHE: dict[tuple[str, str], tuple[tuple, dict]] = {}   # (项目, 集) → ((语料签名, 资产目录指纹), {kind: 命中的资产 id})


def _read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
    except Exception:
        return None


def _names(entry: dict, *, with_aliases: bool) -> list[str]:
    out = [entry.get("canonical_name"), entry.get("name")]
    if with_aliases:
        for a in entry.get("aliases") or []:
            out.append(a.get("name") if isinstance(a, dict) else a)
    seen, res = set(), []
    for n in out:
        n = n.strip() if isinstance(n, str) else ""
        if len(n) >= 2 and n not in seen:
            seen.add(n)
            res.append(n)
    return res


def _catalog(base: Path) -> dict[str, dict[str, dict]]:
    """{kind: {id: {"names": [...], "episodes": [...]}}};id 全集与各预览页聚合口径一致(登记表 ∪ 设定目录 ∪ 概念图目录)。"""
    cat: dict[str, dict[str, dict]] = {k: {} for k in KINDS}

    def put(kind: str, aid, entry: dict | None, with_aliases: bool):
        if not isinstance(aid, str) or not aid:
            return
        rec = cat[kind].setdefault(aid, {"names": [], "episodes": []})
        if isinstance(entry, dict):
            for n in _names(entry, with_aliases=with_aliases):
                if n not in rec["names"]:
                    rec["names"].append(n)
            eps = entry.get("episodes")
            if isinstance(eps, list):
                rec["episodes"] += [e for e in eps if isinstance(e, str)]

    for kind, key in (("scenes", "scenes"), ("characters", "characters")):
        idx = _read_json(base / "bible" / kind / "index.json") or {}
        for e in idx.get(key) or idx.get("entries") or []:
            if isinstance(e, dict):
                put(kind, e.get("id"), e, False)
    cdir = base / "bible" / "creatures"
    for e in (_read_json(cdir / "index.json") or {}).get("creatures") or []:
        if isinstance(e, dict):
            put("creatures", e.get("id"), e, True)
    for fname, key in (("creature.json", "creatures"), ("mount.json", "mounts")):
        doc = _read_json(cdir / fname) or {}
        for e in doc.get(key) or doc.get("entries") or []:
            if isinstance(e, dict):
                put("creatures", e.get("creature_ref") or e.get("id"), e, True)
    doc = _read_json(base / "bible" / "props.json") or {}
    for e in doc.get("props") or doc.get("entries") or []:
        if isinstance(e, dict):
            put("props", e.get("id"), e, True)
    for kind in KINDS:
        for d in (base / "bible" / kind, base / "assets" / "concepts" / kind):
            if d.is_dir():
                for x in d.iterdir():
                    if x.is_dir() and not x.name.startswith("."):
                        put(kind, x.name, None, False)
    return cat


def episode_ids(base: Path) -> list[str]:
    eps: set[str] = set()
    for d in (base / "story" / "episodes", base / "directing", base / "assets" / "prompts"):
        if d.is_dir():
            eps |= {x.name for x in d.iterdir() if x.is_dir() and _EP_DIR_RE.match(x.name)}
    plan = _read_json(base / "story" / "episode_plan.json") or {}
    for e in plan.get("episodes") or []:
        if isinstance(e, dict):
            eid = e.get("episode_id") or e.get("ep") or e.get("id")
            if isinstance(eid, str) and _EP_DIR_RE.match(eid):
                eps.add(eid)
    return sorted(eps)


def _ep_files(base: Path, ep: str) -> list[Path]:
    files = [base / rel.format(ep=ep) for rel in _EP_FILES]
    for g in _EP_GLOBS:
        files += sorted(base.glob(g.format(ep=ep)))
    return [f for f in files if f.is_file()]


def _corpus(files: list[Path]) -> str:
    parts = []
    for f in files:
        try:
            if f.stat().st_size > _MAX_BYTES:
                continue
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if f.suffix == ".json" and "\\u" in text:   # ensure_ascii 写出的 JSON:中文名要还原才能按名字命中
            try:
                text = json.dumps(json.loads(text), ensure_ascii=False)
            except Exception:
                pass
        parts.append(text)
    return "\n".join(parts)


def _ids_hit(ids, text: str) -> set[str]:
    """一次扫描取出语料里出现的全部资产 ID(逐 ID 搜在大项目上要数秒)。"""
    if not ids:
        return set()
    pat = "(?:" + "|".join(re.escape(i) for i in sorted(ids, key=len, reverse=True)) + r")(?![A-Za-z0-9])"
    return set(re.findall(pat, text))


def _names_pattern(names) -> re.Pattern | None:
    names = sorted(set(names), key=len, reverse=True)   # 长名在前:同一起点先取最长的那个
    return re.compile("|".join(re.escape(n) for n in names)) if names else None


def _names_hit(pat: re.Pattern | None, text: str) -> set[str]:
    """一次扫描取出语料里出现的全部名字,结果与逐个 `name in text` 相同(逐个搜在十几 MB 的语料上要近 10 秒)。
    每次从上一个命中起点的下一个字符接着找,所以互相重叠的名字不会漏;同一起点只报最长的,更短的同起点名字必是它的前缀,扫完补上。"""
    if pat is None:
        return set()
    found: set[str] = set()
    pos, search = 0, pat.search
    while m := search(text, pos):
        found.add(m.group())
        pos = m.start() + 1
    return {g[:i] for g in found for i in range(2, len(g) + 1)}


def _scan_episode(cat: dict, fam: dict, files: list[Path]) -> dict[str, set[str]]:
    """一集语料里命中的资产:{kind: {id…}}(规则 ①③)。"""
    text = _corpus(files)
    if not text:
        return {}
    by_name = [k for k in KINDS if k in ("creatures", "props") or not (fam[k] and fam[k].search(text))]
    named = _names_hit(_names_pattern(n for k in by_name for r in cat[k].values() for n in r["names"]), text)
    out = {}
    for kind in KINDS:
        hits = _ids_hit(cat[kind], text)
        if kind in by_name:
            hits |= {aid for aid, rec in cat[kind].items() if not named.isdisjoint(rec["names"])}
        out[kind] = hits
    return out


def _id_family(ids) -> re.Pattern | None:
    """该类 ID 的前缀族(CHAR- / SCN- …),用来判断「这集语料到底写不写这类 ID」。"""
    pres = {m.group(1) for i in ids if (m := re.match(r"^([A-Za-z]+[-_])\d", i))}
    return re.compile("(?:" + "|".join(re.escape(p) for p in sorted(pres)) + r")\d") if pres else None


def build(base: Path) -> dict:
    """{"episodes": [{"ep","title"}], "scenes": {id: [ep…]}, "characters": {...}, "creatures": {...}, "props": {...}}"""
    base = Path(base)
    eps = episode_ids(base)
    ep_files = {ep: _ep_files(base, ep) for ep in eps}
    cat = _catalog(base)
    cat_fp = hashlib.md5(json.dumps(cat, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    out: dict = {k: {aid: set(r["episodes"]) & set(eps) for aid, r in cat[k].items()} for k in KINDS}
    fam = {k: _id_family(cat[k]) for k in KINDS}
    for ep in eps:
        sig = []
        for f in ep_files[ep]:
            try:
                st = f.stat()
                sig.append((str(f), st.st_mtime_ns, st.st_size))
            except OSError:
                pass
        key = (tuple(sig), cat_fp)
        hit = _CACHE.get((str(base), ep))
        if hit and hit[0] == key:
            found = hit[1]
        else:
            found = _scan_episode(cat, fam, ep_files[ep])
            _CACHE[(str(base), ep)] = (key, found)
        for kind, aids in found.items():
            for aid in aids:
                out[kind][aid].add(ep)
    plan = _read_json(base / "story" / "episode_plan.json") or {}
    titles = {}
    for e in plan.get("episodes") or []:
        if isinstance(e, dict):
            eid = e.get("episode_id") or e.get("ep") or e.get("id")
            if isinstance(eid, str) and isinstance(e.get("title"), str):
                titles[eid] = e["title"]
    # 下拉只列有语料或有资产命中的集(episode_plan 规划了但还没写的集不列)
    live = {ep for ep in eps if ep_files[ep]} | {e for k in KINDS for v in out[k].values() for e in v}
    res = {"episodes": [{"ep": ep, "title": titles.get(ep, "")} for ep in eps if ep in live]}
    for k in KINDS:
        res[k] = {aid: sorted(v) for aid, v in out[k].items() if v}
    return res
