# -*- coding: utf-8 -*-
"""上下黑边(后期处理页「包装 › 上下黑边」,settings.json#output.letterbox_enabled / letterbox_aspect)。

生产画幅不变,黑边只在成片封装(finalize_episode.py assemble)这一步加:画布 = 最终输出画幅、短边 = 正片短边,
画面等比缩放后居中。这里锁:① 版式算得对;② 封装出来的成片真的是那个画布、画面真的在版式写的位置;
③ 机检 letterbox_applied 抓得住「改了设置没重出成片」;④ 花字版成片按画面区域落位;⑤ 设置校验 / 提示词 / 界面。
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "modules"))
sys.path.insert(0, str(ROOT / "code"))
sys.path.insert(0, str(ROOT))

import finalize_episode as fe  # noqa: E402
from modules.output_format import letterbox_layout, ratio_value, resolve_letterbox  # noqa: E402
from test_finalize_intro_offset import _build, _layout, needs_ffmpeg  # noqa: E402


@pytest.mark.parametrize("src,aspect,canvas,picture,bars", [
    ((2520, 1080), "16:9", [1920, 1080], [0, 129, 1920, 822], "top_bottom"),    # 2.35:1(21:9 档)→ 16:9
    ((1920, 1080), "9:16", [1080, 1920], [0, 656, 1080, 608], "top_bottom"),    # 16:9 → 9:16
    ((1920, 1080), "4:3", [1440, 1080], [0, 135, 1440, 810], "top_bottom"),
    ((1080, 1920), "16:9", [1920, 1080], [656, 0, 608, 1080], "left_right"),    # 画面比目标更高:黑边落在左右
    ((320, 180), "2.35 : 1", [424, 180], [52, 0, 320, 180], "left_right"),
])
def test_layout(src, aspect, canvas, picture, bars):
    lb = letterbox_layout(*src, aspect)
    assert (lb["canvas"], lb["picture"], lb["bars"]) == (canvas, picture, bars)
    assert all(v % 2 == 0 for v in lb["canvas"] + lb["picture"][2:])


def test_layout_noop_and_settings():
    assert letterbox_layout(1920, 1080, "16:9") is None          # 画幅已一致
    assert letterbox_layout(1918, 1080, "16:9") is None          # 取整误差不算
    assert letterbox_layout(1920, 1080, "wide") is None
    assert ratio_value("21:9") == pytest.approx(21 / 9) and ratio_value("0:9") is None and ratio_value("16x9") is None
    assert resolve_letterbox({"output": {"letterbox_enabled": True, "letterbox_aspect": " 9:16 "}}) == "9:16"
    assert resolve_letterbox({"output": {"letterbox_aspect": "9:16"}}) == ""          # 未开启
    assert resolve_letterbox({"output": {"letterbox_enabled": True, "letterbox_aspect": "tall"}}) == ""


def _settings(proj: Path, **output):
    (proj / "settings.json").write_text(json.dumps({"output": output}), encoding="utf-8")


def _pixel(video: Path, t: float, x: int, y: int):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(video), "-frames:v", "1",
                          "-vf", f"crop=2:2:{x}:{y}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         check=True, capture_output=True).stdout
    return tuple(raw[:3])


@needs_ffmpeg
def test_assemble_adds_bars_and_check_tracks_setting(tmp_path):
    proj = tmp_path / "demo"
    ed = _build(proj)                                              # 片头 2 s(红)+ 正片 12 s(蓝),均 320x180
    _settings(proj, letterbox_enabled=True, letterbox_aspect="9:16")
    segs, _ = _layout(proj)
    final = ed / "final.mp4"
    fe.do_assemble(proj, "ep01", segs, final, preset="ultrafast")
    fe.do_shift(proj, "ep01", fe.offsets_of(segs)[0]["cut"])
    v = fe._probe_streams(final)["video"]
    assert (v["width"], v["height"]) == (180, 320)
    assert fe.do_check(proj, "ep01", segs, [], final)
    led = json.loads((ed / "final_layout.json").read_text(encoding="utf-8"))
    lb = led["letterbox"]
    assert lb["canvas"] == [180, 320] and lb["picture"] == [0, 109, 180, 102] and lb["source"] == [320, 180]
    assert {c["name"]: c["status"] for c in led["check"]["items"]}["letterbox_applied"] == "PASS"
    x, y, w, h = lb["picture"]
    for t, colour in ((1.0, 0), (6.0, 2)):                         # 片头段红、正片段蓝:画面在版式位置,上下是黑边
        mid = _pixel(final, t, x + w // 2, y + h // 2)
        assert mid[colour] > 150 and sum(mid) - mid[colour] < 120
        assert max(_pixel(final, t, 90, 20)) < 40 and max(_pixel(final, t, 90, 300)) < 40
    assert abs(fe.probe_duration(final) - 14.0) < 0.25             # 加黑边不动时长

    # 花字版成片按画面区域落位;台账画布与成片对不上(改了设置没重出)时按整幅处理
    import render_captions as rc
    assert rc._picture_rect(proj, "ep01", final) == [0, 109, 180, 102]

    _settings(proj, letterbox_enabled=True, letterbox_aspect="4:3")   # 改了最终输出画幅没重出成片 → FAIL
    segs, _ = _layout(proj)
    assert not fe.do_check(proj, "ep01", segs, [], final)
    assert rc._picture_rect(proj, "ep01", final) is None
    _settings(proj, letterbox_enabled=False)                           # 关掉后旧的带黑边成片只提醒
    segs, _ = _layout(proj)
    assert fe.do_check(proj, "ep01", segs, [], final)
    led = json.loads((ed / "final_layout.json").read_text(encoding="utf-8"))
    assert led["letterbox"] is None
    assert {c["name"]: c["status"] for c in led["check"]["items"]}["letterbox_applied"] == "WARN"


@needs_ffmpeg
def test_off_keeps_cut_canvas(tmp_path):
    proj = tmp_path / "demo"
    ed = _build(proj)
    segs, _ = _layout(proj)
    fe.do_assemble(proj, "ep01", segs, ed / "final.mp4", preset="ultrafast")
    fe.do_shift(proj, "ep01", fe.offsets_of(segs)[0]["cut"])
    v = fe._probe_streams(ed / "final.mp4")["video"]
    assert (v["width"], v["height"]) == (320, 180)
    assert fe.do_check(proj, "ep01", segs, [], ed / "final.mp4")
    led = json.loads((ed / "final_layout.json").read_text(encoding="utf-8"))
    assert led["letterbox"] is None and "letterbox_applied" not in {c["name"] for c in led["check"]["items"]}


def test_caption_anchor_uses_picture_rect(monkeypatch, tmp_path):
    """_composite:字号 / 锚点按画面区域算,再整体平移到画面左上角。"""
    import captions_html as chtml
    seen = {}

    class Renderer:
        def render(self, html, text, params, dur, fps, dsf, tag=""):
            seen["dsf"] = dsf
            return tmp_path / "s.mov", {"bbox": {"x": 0, "y": 0, "w": 100, "h": 20}}

    monkeypatch.setattr(chtml, "_style_v3", lambda c: ("tpl", 10.0, {}))
    monkeypatch.setattr(chtml, "resolve_template", lambda *a: ("<html>", None))
    monkeypatch.setattr(chtml, "template_font_files", lambda *a: [])
    monkeypatch.setattr(chtml, "glyph_missing", lambda *a: [])
    monkeypatch.setattr(chtml, "estimate_css_width", lambda text: 100.0)
    monkeypatch.setattr(chtml, "ffmpeg_bin", lambda: "ffmpeg")

    def fake_run(cmd, **kw):
        seen["fc"] = cmd[cmd.index("-filter_complex") + 1]
        raise RuntimeError("stop")
    monkeypatch.setattr(chtml.subprocess, "run", fake_run)
    info = {"width": 1080, "height": 1920, "fps": 24, "duration_s": 10.0, "has_audio": False}
    items = [{"cap": {"id": "c1", "text": "字", "position": "center"}, "t0": 1.0, "t1": 3.0}]
    with pytest.raises(RuntimeError, match="stop"):
        chtml._composite(tmp_path / "in.mp4", tmp_path / "out.mp4", items, info, {}, tmp_path, Renderer(),
                         picture_rect=[0, 656, 1080, 608])
    x, y = (float(v) for v in re.search(r"overlay=(-?\d+):(-?\d+)", seen["fc"]).groups())
    assert seen["dsf"] == pytest.approx(0.10 * 608 / chtml.BASE_FONT_PX)      # 字号按画面高 608,不按画布高 1920
    assert 656 <= y and y + 20 <= 656 + 608 and x == pytest.approx((1080 - 100) / 2, abs=1)


def test_settings_prompt_and_ui(tmp_path, monkeypatch):
    from services.runtime import core
    out = dict(core.DEFAULT_GENCONFIG["output"])
    assert out["letterbox_enabled"] is False and out["letterbox_aspect"] == "16:9"
    core._validate_output({**out, "letterbox_enabled": True, "letterbox_aspect": "2.35:1"})
    for bad in ({"letterbox_enabled": "yes"}, {"letterbox_aspect": "wide"}, {"letterbox_aspect": "16:0"}):
        with pytest.raises(core.ServiceError):
            core._validate_output({**out, **bad})

    monkeypatch.setattr(core, "PROJECTS_DIR", tmp_path / "projects")
    (core.PROJECTS_DIR / "p").mkdir(parents=True)
    line = lambda: next(ln for ln in core.build_role_prompt("10-editing/edit", "p").splitlines()  # noqa: E731
                        if ln.startswith("- 上下黑边:"))
    (core.PROJECTS_DIR / "p" / "settings.json").write_text("{}")
    assert line().startswith("- 上下黑边:关闭")
    (core.PROJECTS_DIR / "p" / "settings.json").write_text(json.dumps(
        {"output": {"aspect_preset": "cinema", "letterbox_enabled": True, "letterbox_aspect": "16:9"}}))
    assert "最终输出画幅 16:9" in line() and "输出画幅 21:9" in line() and "finalize_episode.py assemble" in line()

    # 后期处理页逐项保存:只带改动的那一个键,其余输出设置不动
    import asyncio

    async def no_op(*args, **kwargs):
        return {"ok": True}
    monkeypatch.setattr(core, "api_chat", no_op)
    monkeypatch.setattr(core, "_notify_settings_change", no_op)
    asyncio.run(core.api_projconfig_set({"project": "p", "output": {"letterbox_aspect": "9:16"}}))
    saved = core.load_project_settings("p")["output"]
    assert (saved["letterbox_enabled"], saved["letterbox_aspect"], saved["aspect_preset"]) == (True, "9:16", "cinema")
    with pytest.raises(core.ServiceError):
        asyncio.run(core.api_projconfig_set({"project": "p", "output": {"letterbox_aspect": "tall"}}))

    static = ROOT / "apps" / "web" / "static"
    html = (static / "preview_post.html").read_text(encoding="utf-8")
    assert html.index("data-ou=\"caption_enabled\"") < html.index("data-ou=\"letterbox_enabled\"")   # 花字下面
    keys = ["上下黑边", "启用上下黑边", "最终输出画幅", "输出画幅 {a}", "出成片时把画面等比缩放后补黑边到最终输出画幅,改动后需重新出成片"]
    for k in keys:
        assert f"'{k}'" in html, k
    for js in (static / "i18n").glob("??.js"):
        text = js.read_text(encoding="utf-8")
        for k in keys:
            assert f'"{k}":' in text, (js.name, k)
