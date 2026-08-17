#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""migrate_captions_v3.py — captions.json v2(libass)→ v3(HTML 引擎)一次性迁移。

用法:
  python3 migrate_captions_v3.py --project <slug> --ep epNN \
      --map preset:keyword_pop=template:keyword_pop [--map ...] [--em-factor 1.17]

迁移规则:
  - text/时间轴(start/end/local_*)/group_id/position/sfx/segments/type 原样保留;
  - style_ref preset:X → template_ref(按 --map);style_presets/animation/
    style_override 删除(动画与视觉封装进模版);
  - 字号:em_pct = size_pct × em-factor(默认 1.17,2026-08-14 按 history demo
    对旧成片实测标定;size_pct 取 override 优先、preset 兜底);
  - params:segments/glow/char_box 等模版参数从旧样式提炼;
  - 原文件备份为 captions.v2.json,新文件就地覆盖 captions.json。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args  # noqa: E402,F401  (项目根解析沿用 CLI 惯例)


def migrate(data: dict, mapping: dict[str, str], em_factor: float) -> dict:
    presets = data.get("style_presets") or {}
    out_caps = []
    for c in data.get("captions", []):
        sref = c.get("style_ref") or ""
        if sref not in mapping:
            raise SystemExit(f"[FAIL] {c.get('id')}: style_ref {sref!r} 无 --map 映射")
        ov = c.get("style_override") or {}
        base = presets.get(sref[7:], {}) if sref.startswith("preset:") else {}
        size_pct = float(ov.get("size_pct") or base.get("size_pct") or 13.0)
        params = {}
        if c.get("segments"):
            params["segments"] = c["segments"]
        glow = ov.get("glow") or base.get("glow")
        if glow and glow.get("color"):
            params["glow"] = glow["color"]
        box = ov.get("char_box") or base.get("char_box")
        if box and box.get("color"):
            params["box"] = box["color"]
            stroke = (ov.get("stroke") or base.get("stroke") or {}).get("color")
            if stroke:
                params["stroke"] = stroke
        nc = {k: v for k, v in c.items()
              if k not in ("style_ref", "style_override", "animation")}
        nc["template_ref"] = mapping[sref]
        nc["em_pct"] = round(size_pct * em_factor, 1)
        if params:
            nc["params"] = params
        out_caps.append(nc)
    out = {k: v for k, v in data.items() if k != "style_presets"}
    out["schema_version"] = 3
    out["captions"] = out_caps
    out["migrated_from"] = "v2:" + str(data.get("task_id") or "?")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--ep", required=True)
    ap.add_argument("--map", action="append", default=[],
                    help="preset:X=template:Y(可多个)")
    ap.add_argument("--em-factor", type=float, default=1.17)
    args = ap.parse_args()
    proj = Path(__file__).resolve().parents[1] / "data" / "projects" / args.project
    cj = proj / "edit" / args.ep / "captions.json"
    data = json.loads(cj.read_text(encoding="utf-8"))
    if data.get("schema_version") == 3:
        print("[SKIP] 已是 v3")
        return
    mapping = dict(m.split("=", 1) for m in args.map)
    out = migrate(data, mapping, args.em_factor)
    bak = cj.with_name("captions.v2.json")
    if not bak.exists():
        bak.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                       encoding="utf-8")
    cj.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[DONE] {cj} → schema v3({len(out['captions'])} 条;备份 {bak.name})")


if __name__ == "__main__":
    main()
