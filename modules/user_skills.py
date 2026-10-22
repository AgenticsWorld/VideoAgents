"""用户自装技能目录(对话面板「技能」弹窗「加载技能」的安装处)。

内置技能随安装包放在 agents/<类别>/<agent>/skills/<技能目录>/SKILL.md——桌面端那是已签名 .app 里的
Resources/backend,换新版整个替换,往里写还会破坏签名(见 apps/desktop/src/main.ts 的 __pycache__ 注释)。
用户上传的技能改装到数据目录 <数据目录>/skills/<类别>/<agent>/<技能目录>/SKILL.md(与 agents/ 同构),
升级换包不丢;可用 VIDEOAGENTS_SKILLS_DIR 覆盖。宿主服务(services/runtime/core.py)与成员调用的 code/ 机检
(prompt_skill_check.py)按同一口径解析。只用标准库。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_AGENT_ID_RE = re.compile(r"[A-Za-z0-9_\-]+/[A-Za-z0-9_\-]+")
_SKILL_DIR_RE = re.compile(r"[A-Za-z0-9_\-]+")


def user_skills_dir() -> Path:
    env = os.environ.get("VIDEOAGENTS_SKILLS_DIR")
    if env:
        return Path(env).expanduser().resolve()
    data = Path(os.environ.get("VIDEOAGENTS_DATA_DIR", ROOT / "data")).expanduser().resolve()
    return data / "skills"


def skill_md_path(skill_id: str, root: Path = ROOT, user_dir: Path | None = None) -> Path:
    """skill_id = "<agent_id>/<技能目录>" → SKILL.md 路径:内置(agents/<agent_id>/skills/<目录>)优先,
    其次用户目录;两处都没有时返回内置路径(调用方据此报「找不到」)。"""
    agent_id, _, sdir = str(skill_id or "").rpartition("/")
    bundled = root / "agents" / agent_id / "skills" / sdir / "SKILL.md"
    if not (_AGENT_ID_RE.fullmatch(agent_id) and _SKILL_DIR_RE.fullmatch(sdir)):
        return bundled                      # 非法 id 不去用户目录找(防路径穿越),调用方按「找不到」处理
    if bundled.is_file():
        return bundled
    user = (user_dir or user_skills_dir()) / agent_id / sdir / "SKILL.md"
    return user if user.is_file() else bundled
