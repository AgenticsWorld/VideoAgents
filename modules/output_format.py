"""Pure project output-format resolution shared by runtime and whitebox tools."""
import re

# cinema(2026-10-06):院线宽银幕 2.35:1。视频/图像模型的画幅只收固定枚举,宽银幕档统一是 21:9(≈2.33:1),
# 所以比例串给 21:9(全链 --aspect / 白模 / 超分 / 出图规格都认),名称里写明 2.35:1 口径
OUTPUT_ASPECTS = {"youtube": ("16:9", "YouTube 横屏"), "douyin": ("9:16", "抖音竖屏"),
                  "cinema": ("21:9", "电影宽银幕 2.35:1,按 21:9 档生成")}


def resolve_output(cfg: dict) -> tuple[str, str, str]:
    """Project settings -> (aspect ratio, display name, output language)."""
    out = cfg.get("output") or {}
    preset = out.get("aspect_preset") or "youtube"
    if preset == "custom":
        aspect = re.sub(r"\s", "", out.get("aspect_custom") or "") or "16:9"
        name = "自定义"
    else:
        aspect, name = OUTPUT_ASPECTS.get(preset, OUTPUT_ASPECTS["youtube"])
    return aspect, name, out.get("language") or "English"


# ---------------------------------------------------------------- 上下黑边(2026-10-06)
# 后期处理页「包装 › 上下黑边」:output.letterbox_enabled + output.letterbox_aspect(最终输出画幅)。
# 生产画幅不变;成片封装(code/finalize_episode.py assemble)把各段画面等比缩放后补黑边到最终输出画幅,
# 例:2.35:1 的片子出 16:9 成片、16:9 的片子出 9:16 成片。画面比目标更高时黑边落在左右。
LETTERBOX_ASPECTS = ("16:9", "9:16", "4:3", "3:4", "1:1")   # 界面下拉;后端受理任意 宽:高
_RATIO_RE = re.compile(r"^\d+(?:\.\d+)?:\d+(?:\.\d+)?$")


def ratio_value(aspect) -> float | None:
    """"16:9" / "2.35:1" -> 宽高比;写法不对或非正数 -> None。"""
    text = re.sub(r"\s", "", str(aspect or ""))
    if not _RATIO_RE.match(text):
        return None
    a, b = (float(x) for x in text.split(":"))
    return a / b if a > 0 and b > 0 else None


def resolve_letterbox(cfg: dict) -> str:
    """项目设置 -> 最终输出画幅串;未开启或写法无效 -> ""。"""
    out = (cfg or {}).get("output") or {}
    if out.get("letterbox_enabled") is not True:
        return ""
    aspect = re.sub(r"\s", "", str(out.get("letterbox_aspect") or ""))
    return aspect if ratio_value(aspect) else ""


def _even(n: float) -> int:
    return max(2, int(round(n / 2)) * 2)


def letterbox_layout(src_w: int, src_h: int, aspect: str) -> dict | None:
    """正片 src_w×src_h 放进最终输出画幅 aspect 的版式;画幅已一致(差 <1%)或参数无效 -> None。

    画布短边 = 正片短边(1080p 的片子出来还是 1080p 档:2520×1080 → 1920×1080 画布、16:9 → 1080×1920 画布),
    画面等比缩放后居中,宽高都取偶数。返回 canvas [宽,高]、picture [x,y,宽,高]、bars top_bottom|left_right。"""
    ratio = ratio_value(aspect)
    if not ratio or src_w <= 0 or src_h <= 0 or abs(ratio / (src_w / src_h) - 1) < 0.01:
        return None
    short = _even(min(src_w, src_h))
    cw, ch = (_even(short * ratio), short) if ratio >= 1 else (short, _even(short / ratio))
    scale = min(cw / src_w, ch / src_h)
    pw, ph = min(cw, _even(src_w * scale)), min(ch, _even(src_h * scale))
    return {"aspect": re.sub(r"\s", "", aspect), "source": [src_w, src_h], "canvas": [cw, ch],
            "picture": [(cw - pw) // 2, (ch - ph) // 2, pw, ph],
            "bars": "top_bottom" if ratio < src_w / src_h else "left_right"}

