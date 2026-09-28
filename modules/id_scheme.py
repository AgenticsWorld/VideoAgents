"""编号制(场次 / 分镜组 / 分镜):项目级,新建项目时盖章进 settings.json#numbering,此后不变。

- 逐一制(step=1;存量项目,settings.json 无 numbering 段一律按此):S01 / grp001 / sh001,
  场次两位、组/镜三位零填充,逐一递增。
- 预留插入位制(step=10;2026-09-28 起新建的项目):S010 / grp0010 / sh0010,场次三位、
  组/镜四位零填充,首次编号按 10 递增(末位恒 0);之后要在两个编号之间插入时取中间值
  (S011 / grp0011 / sh0011),已有编号永不重排。

零第三方依赖:宿主服务、genmedia 与 code/ 下各机检共用。
"""
import json
import os
import re
from pathlib import Path

STEP_LEGACY = 1
STEP_SPACED = 10
STEPS = (STEP_LEGACY, STEP_SPACED)
DEFAULT = {"step": STEP_LEGACY}
# 前缀 → (逐一制位数, 预留插入位制位数)
_WIDTHS = {"S": (2, 3), "grp": (3, 4), "sh": (3, 4)}


def normalize(value) -> dict:
    """settings.json#numbering 规范化;缺段/非法值 = 逐一制(存量项目不受影响)。"""
    try:
        step = int((value or {}).get("step", STEP_LEGACY))
    except (AttributeError, TypeError, ValueError):
        step = STEP_LEGACY
    return {"step": step if step in STEPS else STEP_LEGACY}


def project_step(project_dir) -> int:
    """项目根目录 → 编号步长;读不到 settings.json 按逐一制。"""
    try:
        saved = json.loads((Path(project_dir) / "settings.json").read_text())
    except Exception:
        return STEP_LEGACY
    return normalize(saved.get("numbering") if isinstance(saved, dict) else None)["step"]


def project_dir_of(path) -> Path | None:
    """产物路径 → 所属项目根(…/projects/<项目>/…);路径里认不出时回落环境变量里的当前项目。"""
    try:
        parts = Path(path).resolve().parts
    except Exception:
        parts = ()
    for i in range(len(parts) - 2, -1, -1):
        if parts[i] == "projects" and (Path(*parts[:i + 2]) / "settings.json").is_file():
            return Path(*parts[:i + 2])
    proj = os.environ.get("VIDEOAGENTS_PROJECT") or os.environ.get("WEBUI_PROJECT") or ""
    if proj and re.fullmatch(r"[\w-]+", proj):
        data_dir = Path(os.environ.get("VIDEOAGENTS_DATA_DIR",
                                       Path(__file__).resolve().parents[1] / "data")).expanduser()
        return data_dir / "projects" / proj
    return None


def width(prefix: str, step: int) -> int:
    """编号位数:prefix ∈ S / grp / sh。"""
    return _WIDTHS[prefix][1 if step == STEP_SPACED else 0]


def format_id(prefix: str, ordinal: int, step: int) -> str:
    """第 ordinal 个(从 1 起)的首次编号:逐一制 grp001,预留插入位制 grp0010。"""
    return f"{prefix}{int(ordinal) * (STEP_SPACED if step == STEP_SPACED else 1):0{width(prefix, step)}d}"


def prompt_section(step: int) -> str:
    """运行提示词注入节(仅预留插入位制项目注入;条件式,存量项目无此节、照旧按逐一制)。"""
    if step != STEP_SPACED:
        return ""
    return (
        "\n\n## 编号制:预留插入位(本项目新建时已锁定,写在 settings.json#numbering,不可更改;"
        "优先级高于 SOUL.md 与 WORKFLOW.md 中的「三位零填充」及一切 S01 / grp001 / sh001 式写法)\n"
        "- **场次**三位、末位 0:`S010`、`S020`、`S030`…(不是 S01、S02);"
        "**分镜组**四位、末位 0:`grp0010`、`grp0020`…(不是 grp001、grp002);"
        "**分镜**四位、末位 0:`sh0010`、`sh0020`…(不是 sh001、sh002)。"
        "首次编号一律按 10 递增,第 N 个 = N×10(第 12 组 = `grp0120`)。\n"
        "- **插入**:之后要在两个已有编号之间加场次/组/镜时,取两者之间的空号,从前一个编号 +1 起"
        "(`S010` 与 `S020` 之间插 `S011`,再插 `S012`;`grp0010` 后插 `grp0011`;`sh0010` 后插 `sh0011`);"
        "**已有编号永不重排、不顺延**,下游产物(prompt、锚点、clip、白模、配音等)不因插入而改名。"
        "顺序一律按编号数值从小到大,不按写入先后。\n"
        "- 文档、示例命令、工单模板里出现的 `S01` / `grp001` / `sh001` / `grpNNN` / `shNNN` 都是逐一制示例,"
        "本项目照上面换算后使用;文件名、目录名与 shot_list 的 group_id / shot_id / scene_no **逐字符一致**。\n"
        "- genmedia 提交前硬校验随之改为四位:输出路径里 grp / sh 编号不是四位直接拒单。\n"
        "- 只改场次 / 分镜组 / 分镜三类编号;集号(ep01)、场景(SCN-)、人物(CHAR-)、道具、旁白(N-01)、"
        "场内草案镜序(`S010-03` 的 `-03`)等其余编号照旧。")
