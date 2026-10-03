# -*- coding: utf-8 -*-
"""色彩脚本兼容读取 / 机检 color_script_ok(modules/color_script.py、code/check_color_script.py)的离线测试。"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "modules"))

import color_script as c  # noqa: E402

NEW = {
    "variants": {"flashback": {"note": "降对比一档、暖偏 +150K", "palette": ["#D8C9AE", "#8C7355"],
                               "grade": {"contrast": 0.9, "temperature_shift_k": 150, "soften": 0.4, "bogus": 1}}},
    "episodes": [{"episode_id": "ep01", "key_palette": ["#2B3A55", "#C9A66B", "#E8DCC4"], "acts": [
        {"act": 1, "event_refs": ["ev0001"], "scene_ids": ["SCN-0108"], "palette": ["#DCE3E8", "#8C7355"], "mood": "旧事", "saturation": "low",
         "brightness": "mid", "rationale": "闪回", "variant": "flashback", "variant_grade": {"temperature_shift_k": 80}},
        {"act": 2, "event_refs": ["ev0002"], "scene_ids": ["SCN-0108", "SCN-0044"], "palette": ["#2B3A55", "#C9A66B"], "mood": "压抑",
         "saturation": "low", "brightness": "low", "rationale": "现实"}]}],
    "peaks": [{"episode_id": "ep01", "act": 2, "type": "dark_moment"}]}


def test_norm_ep_and_entry_lookup():
    assert [c.norm_ep(v) for v in (1, "1", "ep1", "EP01", "ep12", "第3集", True, "x", None)] == \
        ["ep01", "ep01", "ep01", "ep01", "ep12", "ep03", "", "", ""]
    for key, val in (("ep", 6), ("ep", "ep06"), ("episode", "ep06"), ("episode_id", "ep06")):
        assert c.episode_entry({"episodes": [{key: val, "acts": []}]}, "ep06") is not None
    assert c.episode_entry({"episodes": {"ep06": {"acts": []}}}, "ep06")["episode_id"] == "ep06"     # 以集号为键的写法
    assert c.episode_entry({"episodes": [{"ep": 5}]}, "ep06") is None


def test_new_contract_reads_and_passes_strict():
    t = c.scene_palettes(NEW, "ep01")
    # SCN-0108 同被闪回段与现实段引用 → 场次色板取现实段的
    assert t["SCN-0108"] == ["#2B3A55", "#C9A66B"] and t["SCN-0044"] == ["#2B3A55", "#C9A66B"]
    assert t["_episode"] == ["#2B3A55", "#C9A66B", "#E8DCC4"]
    assert c.palette_for(NEW, "ep01", "S99", "SCN-0044") == ["#2B3A55", "#C9A66B"]
    assert c.palette_for(NEW, "ep01", "S99", "SCN-9999") == t["_episode"]
    v = c.variants(NEW)["flashback"]
    assert v["grade"] == {"contrast": 0.9, "temperature_shift_k": 150, "soften": 0.4} and v["palette"] == ["#D8C9AE", "#8C7355"]
    seg = c.segments(c.episode_entry(NEW, "ep01"))[0]
    assert seg["variant"] == "flashback" and seg["variant_grade"] == {"temperature_shift_k": 80}
    assert c.validate(NEW, ["ep01"], strict=True) == ([], [])


def test_legacy_shapes_are_readable():
    dzg = {"episodes": [{"episode": "ep01", "key_palette": ["#808080"], "segments": [{"id": "s1", "scenes": ["S01", "S02"], "palette": ["#C0A080"], "mood": "m", "rationale": "r"}],
                         "beats": [{"scene": "S01", "mood": "x"}]}]}
    assert c.scene_palettes(dzg, "ep01") == {"S01": ["#C0A080"], "S02": ["#C0A080"], "_episode": ["#808080"]}
    polan = {"episodes": [{"ep": 1, "beats": [{"beat_id": "b1", "scenes": ["SCN-0003"], "dominant_hex": "#e2a15c", "accent_hex": "#6E8CA0", "mood": "m", "rationale": "r"}]}]}
    assert c.scene_palettes(polan, "ep01") == {"SCN-0003": ["#E2A15C", "#6E8CA0"], "_episode": ["#E2A15C", "#6E8CA0"]}
    named = {"palette_legend": {"夜航靛蓝": "#172A46", "纸页暖白": "#F2E7D3"},
             "episodes": [{"episode": "ep01", "acts": [{"act": 1, "palette": ["夜航靛蓝", "纸页暖白", "没登记"], "emotion": "凉", "rationale": "r"}]}]}
    assert c.scene_palettes(named, "ep01")["_episode"] == ["#172A46", "#F2E7D3"]
    old_fb = {"_meta": {"palette_library": {"flashback_variant": {"note": "降对比一档", "colors": {"旧纸暖白": "#D8C9AE"}}}},
              "episodes": [{"ep": 1, "acts": [{"act": 3, "palette": ["#D8C9AE"], "palette_family": "①暖米白(闪回变体)", "mood": "m", "rationale": "r",
                                               "flashback_note": "按 flashback_variant"}]}]}
    assert c.variants(old_fb)["flashback"]["palette"] == ["#D8C9AE"] and c.variants(old_fb)["flashback"]["legacy"] is True
    assert c.segments(c.episode_entry(old_fb, "ep01"))[0]["variant"] == "flashback"
    errs, warns = c.validate(old_fb)                                   # 存量:内容合格,契约字段只 WARN
    assert errs == [] and any("episode_id" in w for w in warns) and any("variants.flashback" in w for w in warns)
    assert c.validate(old_fb, strict=True)[0]                          # --strict:同样的问题按 FAIL


def test_validate_content_failures():
    bad = json.loads(json.dumps(NEW))
    bad["episodes"][0]["acts"][1]["palette"] = ["暖米白"]
    bad["episodes"][0]["acts"][1]["mood"] = ""
    bad["episodes"][0]["acts"][0]["variant"] = "memory"
    bad["peaks"][0]["episode_id"] = "ep09"
    errs, _ = c.validate(bad, ["ep01", "ep02"])
    text = "\n".join(errs)
    for needle in ("ep01/act 2: palette 没有合法色值", "ep01/act 2: mood 为空", "variant='memory'", "ep02: episode_plan 里有这一集", "peaks[0]: 指向不存在的 ep09"):
        assert needle in text, (needle, text)
    nodef = json.loads(json.dumps(NEW))
    del nodef["variants"]
    assert any("却没有变体定义" in e for e in c.validate(nodef)[0])
    assert c.validate({"episodes": []})[0] == ["episodes 为空或不是数组"]


def test_cli_exit_codes(tmp_path):
    def run(root, *extra):
        return subprocess.run([sys.executable, str(ROOT / "code" / "check_color_script.py"), "--out-root", str(root), *extra],
                              capture_output=True, text=True, cwd=ROOT)
    assert run(tmp_path).returncode == 2
    (tmp_path / "bible").mkdir()
    (tmp_path / "story").mkdir()
    (tmp_path / "bible" / "color_script.json").write_text(json.dumps(NEW, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "story" / "episode_plan.json").write_text(json.dumps({"episodes": [{"ep": "ep01"}]}), encoding="utf-8")
    p = run(tmp_path, "--strict")
    assert p.returncode == 0 and "OK color_script_ok" in p.stdout, p.stdout + p.stderr
    (tmp_path / "story" / "episode_plan.json").write_text(json.dumps({"episodes": [{"ep": "ep01"}, {"ep": "ep02"}]}), encoding="utf-8")
    p = run(tmp_path)
    assert p.returncode == 1 and "FAIL ep02" in p.stdout
