"""发布渠道(output.platforms):2026-10-07 起在「成片发布」页勾选并逐渠道发布,输出设置 / 新建向导不再露;
可不选(空数组合法);小红书回到可选清单但不进默认;存量值仍受理。"""
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
    core._validate_output(_out(platforms=["xiaohongshu"]))     # 2026-10-07 回到可选清单
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
    """输出设置 / 新建向导不再有发布平台多选;成片发布页按接口的 channels 渲染(含小红书),新建项目默认不选。"""
    html = (ROOT / "apps" / "web" / "static" / "index.html").read_text(encoding="utf-8")
    assert 'class="npo-platform"' not in html and 'class="out-platform"' not in html
    assert "至少选择一个发布平台" not in html
    assert "platforms:[]" in html                      # 新建向导:默认一个渠道都不选
    assert "<label>发布渠道</label>" not in html         # 输出设置里不显示发布渠道(连指路行也不留)
    page = (ROOT / "apps" / "web" / "static" / "preview_videos.html").read_text(encoding="utf-8")
    assert "📡 发布渠道" in page and "/publish/channels" in page and "/dispatch" in page
    assert "xiaohongshu" not in page and "youtube" not in page   # 渠道清单只来自服务端 OUTPUT_PLATFORMS,页面不写死
    assert list(core.OUTPUT_PLATFORMS) == ["youtube", "bilibili", "tiktok", "douyin", "xiaohongshu"]
    assert set(core.PUBLISH_PLATFORM_SKILLS) == set(core.OUTPUT_PLATFORMS)
    for d in core.PUBLISH_PLATFORM_SKILLS.values():
        assert (ROOT / "agents" / "12-publishing" / "publisher" / "skills" / d / "SKILL.md").is_file(), d


def _proj(tmp_path, monkeypatch, name="p"):
    monkeypatch.setattr(core, "PROJECTS_DIR", tmp_path / "projects")

    async def no_op(*args, **kwargs):
        return {"ok": True}
    monkeypatch.setattr(core, "_notify_settings_change", no_op)
    base = core.PROJECTS_DIR / name
    base.mkdir(parents=True)
    (base / "settings.json").write_text("{}")
    return base


def test_publish_channels_rows(tmp_path, monkeypatch):
    base = _proj(tmp_path, monkeypatch)
    (base / "settings.json").write_text('{"output": {"platforms": ["douyin", "xiaohongshu"]}}')
    pkg = base / "publish" / "douyin" / "package" / "ep01"
    pkg.mkdir(parents=True)
    (pkg / "a.mp4").write_bytes(b"0")
    rc = base / "publish" / "receipts"
    rc.mkdir()
    (rc / "ep01_douyin_receipt.json").write_text('{"ep": "ep01", "platform": "douyin", "status": "draft_ready_pending_human"}')
    (rc / "other.json").write_text('{"ep": "ep01", "platform": "xiaohongshu", "status": "success", "video_id": "v1"}')
    rows = {r["key"]: r for r in core._ep_publish_channels(base, "ep01")}
    assert list(rows) == list(core.OUTPUT_PLATFORMS)
    assert rows["douyin"]["selected"] and rows["xiaohongshu"]["selected"] and not rows["youtube"]["selected"]
    assert rows["douyin"]["package_ready"] and rows["douyin"]["package_dir"] == "publish/douyin/package/ep01"
    assert not rows["youtube"]["package_ready"] and rows["youtube"]["receipt"] is None
    assert rows["douyin"]["receipt"]["label"] == "草稿已填好,待人工提交"
    assert rows["xiaohongshu"]["receipt"]["status"] == "success" and rows["xiaohongshu"]["receipt"]["video_id"] == "v1"
    assert rows["xiaohongshu"]["skill"] == "skill-xhs-cdp-draft"
    # 预览接口带 channels
    assert [c["key"] for c in core._preview_videos("p", "ep01")["channels"]] == list(core.OUTPUT_PLATFORMS)


def test_publish_channels_set_and_dispatch(tmp_path, monkeypatch):
    base = _proj(tmp_path, monkeypatch)
    sent = []

    async def fake_chat(body):
        sent.append(body)
        return {"run_id": "r1"}
    monkeypatch.setattr(core, "api_chat", fake_chat)

    r = asyncio.run(core.api_publish_channels_set("p", {"platforms": ["xiaohongshu", "youtube", "nope"], "ep": "ep01"}))
    assert r["platforms"] == ["youtube", "xiaohongshu"]        # 按注册表顺序、未知值丢弃
    assert core.load_project_settings("p")["output"]["platforms"] == ["youtube", "xiaohongshu"]
    assert [c["key"] for c in r["channels"] if c["selected"]] == ["youtube", "xiaohongshu"]
    with pytest.raises(core.ServiceError):
        asyncio.run(core.api_publish_channels_set("p", {"platforms": "youtube"}))

    # 没有 final 成片 → 409,不派单
    with pytest.raises(core.ServiceError) as ei:
        asyncio.run(core.api_publish_dispatch("p", "ep01", {"platform": "douyin"}))
    assert ei.value.status_code == 409 and not sent
    with pytest.raises(core.ServiceError):
        asyncio.run(core.api_publish_dispatch("p", "ep01", {"platform": "weibo"}))

    (base / "edit" / "ep01").mkdir(parents=True)
    (base / "edit" / "ep01" / "ep01_final.mp4").write_bytes(b"0")
    r = asyncio.run(core.api_publish_dispatch("p", "ep01", {"platform": "douyin"}))
    assert r["run_id"] == "r1" and r["package_ready"] is False
    assert sent[-1]["agent"] == "12-publishing/publisher" and sent[-1]["project"] == "p"
    msg = sent[-1]["message"]
    assert "ep01 发布到 抖音(douyin)" in msg and "skills/skill-douyin-cdp-draft/SKILL.md" in msg
    assert "edit/ep01/ep01_final.mp4" in msg and "永不代点" in msg and "ep01_douyin_receipt.json" in msg
    # 发布到未勾选的渠道 → 顺手勾上
    assert core.load_project_settings("p")["output"]["platforms"] == ["youtube", "douyin", "xiaohongshu"]
    pkg = base / "publish" / "douyin" / "package" / "ep01"
    pkg.mkdir(parents=True)
    (pkg / "a.mp4").write_bytes(b"0")
    r = asyncio.run(core.api_publish_dispatch("p", "ep01", {"platform": "douyin"}))
    assert r["package_ready"] is True and "发布包 publish/douyin/package/ep01/ 已就绪" in sent[-1]["message"]


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

