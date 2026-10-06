"""输出设置「发布平台」:可不选(空数组合法);小红书界面隐藏、不进默认,存量值仍受理。"""
import asyncio
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.runtime import core  # noqa: E402


def _out(**kw):
    return {**core.DEFAULT_GENCONFIG["output"], **kw}


def test_validate_accepts_empty_and_rejects_bad():
    core._validate_output(_out(platforms=[]))
    core._validate_output(_out(platforms=["xiaohongshu"]))     # 界面隐藏,后端仍受理
    for bad in ("youtube", None, ["nope"]):
        with pytest.raises(core.ServiceError):
            core._validate_output(_out(platforms=bad))


def test_resolve_platforms():
    assert "xiaohongshu" not in core.DEFAULT_GENCONFIG["output"]["platforms"]
    assert core.resolve_platforms({"output": {"platforms": []}}) == []
    assert [k for k, _, _ in core.resolve_platforms({"output": {}})] == ["youtube", "bilibili", "tiktok", "douyin"]
    assert [k for k, _, _ in core.resolve_platforms({"output": {"platforms": ["xiaohongshu", "douyin"]}})] == [
        "douyin", "xiaohongshu"]


def test_role_prompt_says_unselected(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "PROJECTS_DIR", tmp_path / "projects")

    async def no_op(*args, **kwargs):
        return {"ok": True}
    monkeypatch.setattr(core, "api_chat", no_op)
    monkeypatch.setattr(core, "_notify_settings_change", no_op)
    (core.PROJECTS_DIR / "p").mkdir(parents=True)
    (core.PROJECTS_DIR / "p" / "settings.json").write_text("{}")
    agent = "12-publishing/platform-adapter"

    asyncio.run(core.api_projconfig_set({"project": "p", "output": _out(platforms=["youtube", "douyin"])}))
    line = next(ln for ln in core.build_role_prompt(agent, "p").splitlines() if ln.startswith("- 发布平台:"))
    assert "YouTube(16:9)、抖音(9:16)" in line and "仅面向这些平台" in line

    asyncio.run(core.api_projconfig_set({"project": "p", "output": _out(platforms=[])}))
    assert core.load_project_settings("p")["output"]["platforms"] == []
    line = next(ln for ln in core.build_role_prompt(agent, "p").splitlines() if ln.startswith("- 发布平台:"))
    assert line.startswith("- 发布平台:未选") and "不做分平台发布" in line


def test_webui_platform_lists():
    html = (ROOT / "apps" / "web" / "static" / "index.html").read_text(encoding="utf-8")
    for cls in ("npo-platform", "out-platform"):
        labels = re.findall(rf'<input type="checkbox" class="{cls}" value="(\w+)"[^>]*>([^<]*)<', html)
        assert [v for v, _ in labels] == ["youtube", "bilibili", "tiktok", "douyin"]
        assert all(not re.search(r"\d+:\d+", txt) for _, txt in labels)
    assert not re.search(r'class="(?:npo|out)-platform"[^\n]*<span', html)      # 平台名后不带默认比例
    assert "至少选择一个发布平台" not in html


def test_cinema_aspect_preset(tmp_path):
    """电影画幅(2.35:1):预设受理;比例串给模型都认的宽银幕档 21:9,提示词/下游尺寸按它走。"""
    from modules import genmedia, transition_design
    from modules.output_format import resolve_output
    core._validate_output(_out(aspect_preset="cinema"))
    with pytest.raises(core.ServiceError):
        core._validate_output(_out(aspect_preset="imax"))
    aspect, name, _ = resolve_output({"output": {"aspect_preset": "cinema"}})
    assert aspect == "21:9" and "2.35:1" in name
    for ratios in (genmedia.ASPECT_SIZES, genmedia.MINIMAX_VIDEO_RATIOS, genmedia.FAL_VIDEO_RATIOS,
                   genmedia.OPENROUTER_VIDEO_RATIOS):
        assert aspect in ratios
    assert genmedia._upscale_dimensions(aspect, "1080p") == (2520, 1080)
    assert core._sketchgen_size(aspect) == "2936x1264"
    (tmp_path / "settings.json").write_text('{"output": {"aspect_preset": "cinema"}}')
    assert transition_design._frame_size(tmp_path) == (2520, 1080)
    (tmp_path / "settings.json").write_text('{"output": {"aspect_preset": "douyin"}}')
    assert transition_design._frame_size(tmp_path) == (1080, 1920)
    html = (ROOT / "apps" / "web" / "static" / "index.html").read_text(encoding="utf-8")
    for sel in ("npo-aspect", "out-aspect"):
        block = html.split(f'<select id="{sel}"', 1)[1].split("</select>", 1)[0]
        assert re.findall(r'<option value="(\w+)"', block) == ["youtube", "douyin", "cinema", "custom"]
    for js in (ROOT / "apps" / "web" / "static" / "i18n").glob("??.js"):
        assert '"电影画幅(2.35:1)":' in js.read_text(encoding="utf-8"), js.name

