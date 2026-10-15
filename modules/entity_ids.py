"""实体 ID 类别判定(2026-09-18)。

生物 ID 在仓库里有两套历史写法:分镜/白模规约示例写 `CRE-0001`,生物 Agent 规约(04-creatures/creature SOUL)
与 fengshen3 等项目的 bible 实际写 `cre_bishuishou`(小写下划线 slug)。宿主以前只认 `CRE-` 大写前缀,
导致 blocking_map 机检把生物条目当人物、白模按 1.7 m 人形建模、scene_cast 把生物归到 characters
(qa/defects/DEF-p6-storyboard-ep06-0001)。统一改为不区分大小写的 `cre-` / `cre_` 前缀判定;
任何按 ID 分人/兽的地方都应经这里,不要再各自 startswith('CRE-')。
"""
from __future__ import annotations

import re

_CREATURE_RE = re.compile(r"^cre[-_]", re.IGNORECASE)


def is_creature_id(cid) -> bool:
    """`CRE-0001` / `cre_bishuishou` / `Cre-x` 都算生物;其它(CHAR-*、None、非字符串)不算。"""
    return isinstance(cid, str) and bool(_CREATURE_RE.match(cid))


# ---------------------------------------------------------------- 人物 / 生物编号提取(#117)
# 编号两种形态:数字 `CHAR-0001`(编号段只到数字为止,后面的 `-v1` / `-age01` 是嗓音 / 年龄形态后缀,
# 与 2026-10-08 之前的 `CHAR-\d+` 逐字同口径)与拼音 / 英文 slug `CHAR-jie-rui-er`(字母开头,连字符分段,仅 ASCII,
# 遇到中文 / `_` / 括号 / 空白自然截断)。b794426 曾一律改成贪婪 slug 正则,把 liaozhai `CHAR-0093-v1` 整串当成编号。
# 任何从文本里抓说话人 / 出场人物编号的地方都应经这里,不要再各写一份正则。
CHAR_ID_PAT = r"CHAR-(?:[0-9]+|[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*)"
ACTOR_ID_PAT = r"(?:CHAR|CRE)-(?:[0-9]+|[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*)"
_CHAR_ID_RE = re.compile(CHAR_ID_PAT)
_ACTOR_ID_RE = re.compile(ACTOR_ID_PAT)


def normalize_actor_id(raw, known_ids=None) -> str:
    """抓到的编号串 → 实际编号。slug 编号给了 known_ids(人物 + 生物编号集合)时,在连字符边界取最长的已知编号
    (`CHAR-alice-v1` → `CHAR-alice`,`CHAR-alice-sister` 仍是自己);一个都不认识或没给 known_ids 时原样返回。
    数字编号已由正则截在数字段,原样返回。"""
    s = str(raw or "")
    if known_ids and s not in known_ids:
        parts = s.split("-")
        for k in range(len(parts) - 1, 1, -1):
            cand = "-".join(parts[:k])
            if cand in known_ids:
                return cand
    return s


def extract_actor_id(text, known_ids=None, char_only: bool = False) -> str | None:
    """文本里第一个人物(char_only=False 时含生物)编号;没有返回 None。"""
    m = (_CHAR_ID_RE if char_only else _ACTOR_ID_RE).search(str(text or ""))
    return normalize_actor_id(m.group(0), known_ids) if m else None


def is_actor_id(s, char_only: bool = False) -> bool:
    """整串就是一个人物(/生物)编号。"""
    return isinstance(s, str) and bool((_CHAR_ID_RE if char_only else _ACTOR_ID_RE).fullmatch(s))


def known_actor_ids(base) -> frozenset:
    """项目已登记的人物 + 生物编号:bible/characters|creatures/index.json 的 id 与同目录下的人物卡目录名。"""
    import json
    from pathlib import Path
    out: set[str] = set()
    for kind in ("characters", "creatures"):
        d = Path(base) / "bible" / kind
        try:
            idx = json.loads((d / "index.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            idx = None
        items = idx.get(kind) if isinstance(idx, dict) else idx
        for c in items if isinstance(items, list) else []:
            if isinstance(c, dict) and isinstance(c.get("id"), str) and c["id"].strip():
                out.add(c["id"].strip())
        if d.is_dir():
            out.update(p.name for p in d.iterdir() if p.is_dir() and is_actor_id(p.name))
    return frozenset(out)
