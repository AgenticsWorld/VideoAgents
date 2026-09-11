#!/usr/bin/env python3
"""Validate that the distributable WebUI is complete and uses only API v1."""

from __future__ import annotations

import re
from pathlib import Path


STATIC = Path(__file__).parent / "static"
REQUIRED = {
    "index.html", "models.html", "storage.html", "versions.html", "draw.html",
    "preview_refs.html", "preview_characters.html", "preview_props.html", "preview_creatures.html",
    "preview_scenes.html", "preview_script.html", "preview_storyboard.html", "preview_videos.html",
    "preview_workflow.html", "preview_worldview.html", "i18n/i18n.js",
    "styles/style_library.json", "whitebox-ui.js", "whitebox-renderer.js", "whitebox-format.js",
    "whitebox.css", "whitebox-export.html", "whitebox-i18n.js", "vendor/three/three.module.js",
    "vendor/three/three.core.js", "vendor/three/OrbitControls.js", "vendor/three/LICENSE",
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
    print("WebUI static package is complete; all application requests use /api/v1.")


if __name__ == "__main__":
    main()
