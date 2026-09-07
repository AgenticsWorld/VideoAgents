"""Pure project output-format resolution shared by runtime and whitebox tools."""
import re

OUTPUT_ASPECTS = {"youtube": ("16:9", "YouTube 横屏"), "douyin": ("9:16", "抖音竖屏")}


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
