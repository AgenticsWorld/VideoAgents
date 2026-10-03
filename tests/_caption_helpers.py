# -*- coding: utf-8 -*-
"""花字测试共用的两样素材:一份满足 captpl.v1 协议的最小模版、一个只带 cmap 的最小字体。"""
from pathlib import Path

TEMPLATE = ('<!doctype html><html><body><div id="t"></div><script>'
            'window.__anchorEl="t";window.seek=function(t){document.getElementById("t").style.opacity=t;};'
            '</script></body></html>')


def font_template(font_id: str) -> str:
    return TEMPLATE.replace("<div", f'<style>@font-face{{font-family:k;src:url("font://{font_id}")}}</style><div')


def make_font(path: Path, family: str, chars: str) -> Path:
    """只带 cmap 的最小 TrueType(字形为空轮廓):够 name 表识别 family、cmap 判覆盖。"""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    names = [".notdef"] + [f"g{ord(c):04X}" for c in chars]
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder(names)
    fb.setupCharacterMap({ord(c): f"g{ord(c):04X}" for c in chars})
    fb.setupGlyf({n: TTGlyphPen(None).glyph() for n in names})
    fb.setupHorizontalMetrics({n: (1000, 0) for n in names})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": family, "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    path.parent.mkdir(parents=True, exist_ok=True)
    fb.save(str(path))
    return path
