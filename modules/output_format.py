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
