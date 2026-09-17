"""组级 video_prompt 段落排版(2026-09-14):段与段之间空一行(两个换行),方便用户在分镜预览逐段检查。

规约见 agents/08-video-gen/prompt/SOUL.md「段落排版」条。Agent 按段写正文;宿主各 sync 插入/刷新固定段后统一
调用 paragraphize() 兜底——只在已知段首锚点前断段,不改任何文字(机检均按子串/忽略连续空白核对,不受影响),幂等。
"""
import re

# 段首锚点:Shot 段头、宿主固定段、Global constraints、用户组注释句
_HEADS = (
    r"Shot\s*\d+\s*(?:[:：]|[｜|])",
    r"Scene presence references:",
    r"Continuation reference:",
    r"Whitebox reference:",
    r"Whitebox legend:",
    r"Whitebox facing:",
    r"Shot plates:",
    r"Scene plates:",
    r"Director's note \(user instruction",
    r"Global constraints:",
)
_HEAD_RE = re.compile(r"(?<=\S)[ \t\r\n]*(?=(?:" + "|".join(_HEADS) + r"))")
# Seedance 2.5 官方段落标签:只在句末/行首处断段(「Shot plates: 【场景】」这类段内标签不拆)
_LABEL_RE = re.compile(r"(?<=[。.;；!！?？}>）)])[ \t\r\n]*(?=【(?:人物|场景|道具|动作与声音|主体设定|主体与关系|参考素材职责|"
                       r"生成目标|事件脚本|时间线继承|未采用素材|保持一致)[^】\n]{0,20}】)")
# MiniMax H3 六段字段:已在行首时才补成空行(summary: 之类词可能出现在散文里)
_H3_RE = re.compile(r"(?<=\S)[ \t]*\n\s*(?=(?:subject_definitions|summary|retention_analysis|detailed_description|"
                    r"overall_soundscape|non_diegetic_music):)")
# 固定段收尾句之后另起一段
_TAIL_RE = re.compile(r"(End scene presence references\.|End continuation reference\.)[ \t\r\n]*(?=\S)")
_BLANK_RE = re.compile(r"[ \t]*\n[ \t]*\n\s*")


def paragraphize(text: str) -> str:
    if not isinstance(text, str) or not text:
        return text
    t = _HEAD_RE.sub("\n\n", text)
    t = _LABEL_RE.sub("\n\n", t)
    t = _H3_RE.sub("\n\n", t)
    t = _TAIL_RE.sub(r"\1\n\n", t)
    t = _BLANK_RE.sub("\n\n", t)
    t = t.replace("detailed_description:\n\n", "detailed_description:\n")   # H3 的 Shot 段属于该字段,字段名不单独成段
    return t.strip()


def same_layout_insensitive(a: str, b: str) -> bool:
    """两段 prompt 仅空白排版不同(存量单行正文 vs 分段正文)时视为相同。"""
    return re.sub(r"\s+", "", a or "") == re.sub(r"\s+", "", b or "")
