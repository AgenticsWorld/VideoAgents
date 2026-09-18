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
