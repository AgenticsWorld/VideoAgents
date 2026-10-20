#!/usr/bin/env python3
"""Validate that the distributable WebUI is complete and uses only API v1."""

from __future__ import annotations

import re
from pathlib import Path


STATIC = Path(__file__).parent / "static"
REQUIRED = {
    "index.html", "models.html", "storage.html", "versions.html", "defects.html", "music_library.html", "draw.html",
    "preview_refs.html", "preview_characters.html", "preview_props.html", "preview_creatures.html",
    "preview_scenes.html", "preview_script.html", "preview_board.html", "preview_storyboard.html", "preview_videos.html", "preview_post.html",
    "preview_workflow.html", "preview_worldview.html", "i18n/i18n.js",
    "styles/style_library.json", "whitebox-ui.js", "whitebox-renderer.js", "whitebox-format.js",
    "whitebox.css", "whitebox-export.html", "whitebox-i18n.js", "vendor/three/three.module.js",
    "vendor/three/three.core.js", "vendor/three/OrbitControls.js", "vendor/three/LICENSE",
    "vendor/three/addons/postprocessing/Pass.js", "vendor/spark/spark.module.js", "vendor/spark/LICENSE",
    "world-viewer.js", "director-world.js", "preview_director.html", "world-view-export.html",
    "preview_canvas.html", "canvas-entry.js", "preview_code.html", "styles/code/code_styles.json", "styles/code/opuscar98.json",
}


def main() -> None:
    missing = sorted(name for name in REQUIRED if not (STATIC / name).is_file())
    if missing:
        raise SystemExit("Missing WebUI assets: " + ", ".join(missing))
    legacy: list[str] = []
    for page in [*STATIC.glob("*.html"), *STATIC.rglob("*.js")]:
        text = page.read_text(encoding="utf-8")
        # External links are not application requests; only relative paths count.
        text = re.sub(r"https?://[^\s'\"<>]+", "", text)
        if re.search(r"/api/(?!v1(?:/|['\"]))", text):
            legacy.append(page.name)
    if legacy:
        raise SystemExit("Legacy API paths remain in: " + ", ".join(legacy))
    if len(list((STATIC / "styles" / "images").glob("*.webp"))) != 152:
        raise SystemExit("The original style image library is incomplete")
    # 风格库多语言:每种界面语言(中文除外)一份 styles/i18n/<lang>.json,须覆盖全部风格 id
    import json
    ids = {s["id"] for s in json.loads((STATIC / "styles" / "style_library.json").read_text(encoding="utf-8"))}
    langs = sorted(p.stem for p in (STATIC / "i18n").glob("*.js") if p.stem != "i18n")
    for lang in langs:
        f = STATIC / "styles" / "i18n" / f"{lang}.json"
        if not f.is_file():
            raise SystemExit(f"Missing style library translation: styles/i18n/{lang}.json")
        tr = json.loads(f.read_text(encoding="utf-8"))
        bad = [k for k in ids if not (tr.get(k, {}).get("name") and tr.get(k, {}).get("prompt"))]
        if set(tr) != ids or bad:
            raise SystemExit(f"Style library translation styles/i18n/{lang}.json does not cover all styles")
    # 演示视频风格画廊(2026-10-20):43 张封面 + 每种界面语言(英文除外;中文只取用途标签)一份 styles/code/i18n/<lang>.json
    code = json.loads((STATIC / "styles" / "code" / "code_styles.json").read_text(encoding="utf-8"))
    slugs = {s["slug"] for s in code["styles"]}
    if len(slugs) != 43 or any(not (STATIC / "styles" / "code" / s["frame"]).is_file() for s in code["styles"]):
        raise SystemExit("The code-video style gallery is incomplete")
    light = json.loads((STATIC / "styles" / "code" / "opuscar98.json").read_text(encoding="utf-8"))
    if len(light["styles"]) != 98 or any(not (STATIC / "styles" / "code" / s["frame"]).is_file() for s in light["styles"]):
        raise SystemExit("The OPUSCAR 98 light style gallery is incomplete")
    for lang in [*(x for x in langs if x != "en"), "zh"]:
        f = STATIC / "styles" / "code" / "i18n" / f"{lang}.json"
        if not f.is_file():
            raise SystemExit(f"Missing code-video style translation: styles/code/i18n/{lang}.json")
        tr = json.loads(f.read_text(encoding="utf-8"))
        bad = [k for k in slugs if not (tr.get(k, {}).get("name") and tr.get(k, {}).get("uses"))]
        if bad or len(tr.get("_categories") or {}) != len(code["categories"]):
            raise SystemExit(f"Code-video style translation styles/code/i18n/{lang}.json does not cover all styles")
        # OPUSCAR 98 轻量风格(三期):中文用原文,其余语言须有风格名、混合手法与章节名
        if lang != "zh":
            bad = [x["id"] for x in light["styles"] if not (tr.get(x["id"], {}).get("name")
                                                              and len(tr.get(x["id"], {}).get("blend") or []) == len(x["blend_en"]))]
            if bad or len(tr.get("_chapters98") or {}) != len(light["chapters"]):
                raise SystemExit(f"Code-video style translation styles/code/i18n/{lang}.json does not cover the OPUSCAR 98 light styles")
    print("WebUI static package is complete; all application requests use /api/v1.")


if __name__ == "__main__":
    main()
