"""YouTube Shorts 发布渠道(youtube_shorts,2026-10-28):独立渠道、9:16、与 YouTube 共用上传技能;
成片发布页与派单前 ffprobe 核发布包 / 母版(竖屏或正方形、≤ 3 分钟),不符合不派单;
回执按最长渠道键认领(ep01_youtube_shorts_* 不算 YouTube 的);ytstudio_draft.py upload --shorts 在开浏览器前再核。"""
import asyncio
import importlib.util
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.runtime import core  # noqa: E402

SKILL_DIR = ROOT / "agents" / "12-publishing" / "publisher" / "skills" / "skill-youtube-cdp-draft"
SP = core.SHORTS_PLATFORM


def _proj(tmp_path, monkeypatch, name="p"):
    monkeypatch.setattr(core, "PROJECTS_DIR", tmp_path / "projects")

    async def no_op(*args, **kwargs):
        return {"ok": True}
    monkeypatch.setattr(core, "_notify_settings_change", no_op)
    monkeypatch.setattr(core, "ui_lang_code", lambda cfg=None: "zh")
    base = core.PROJECTS_DIR / name
    base.mkdir(parents=True)
    (base / "settings.json").write_text("{}")
    return base


def _fake_probe(monkeypatch, table, calls=None):
    """按文件名给 _shorts_probe 结论:(宽, 高, 秒) 或 None。"""
    def probe(path):
        if calls is not None:
            calls.append(path.name)
        v = table.get(path.name)
        if v is None:
            return None
        w, h, dur = v
        problems = ([] if w <= h else ["landscape"]) + ([] if dur <= core.SHORTS_MAX_S else ["too_long"])
        return {"width": w, "height": h, "duration": dur, "ok": not problems, "problems": problems}
    monkeypatch.setattr(core, "_shorts_probe", probe)


def _touch(p: Path):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"0")
    return p


def test_registry():
    keys = list(core.OUTPUT_PLATFORMS)
    assert keys.index(SP) == keys.index("youtube") + 1
    assert core.OUTPUT_PLATFORMS[SP] == ("YouTube Shorts", "9:16")
    assert core.PUBLISH_PLATFORM_SKILLS[SP] == core.PUBLISH_PLATFORM_SKILLS["youtube"] == "skill-youtube-cdp-draft"
    assert SP not in core.DEFAULT_GENCONFIG["output"]["platforms"]          # 不进默认清单
    core._validate_output({**core.DEFAULT_GENCONFIG["output"], "platforms": [SP]})
    assert "YouTube Shorts" in core.SKILL_ACTIVATIONS["12-publishing/publisher/skill-youtube-cdp-draft"]["condition"]


def test_receipt_attribution(tmp_path, monkeypatch):
    base = _proj(tmp_path, monkeypatch)
    rc = base / "publish" / "receipts"
    rc.mkdir(parents=True)
    assert core._receipt_name_platform("ep01_youtube_receipt.json", "ep01") == "youtube"
    assert core._receipt_name_platform("ep01_youtube_shorts_receipt.json", "ep01") == SP
    assert core._receipt_name_platform("ep01_youtube.json", "ep01") == "youtube"
    assert core._receipt_name_platform("EP01_YouTube_Shorts.json", "ep01") == SP
    assert core._receipt_name_platform("ep01_youtubex.json", "ep01") is None
    assert core._receipt_name_platform("ep02_youtube.json", "ep01") is None

    (rc / "ep01_youtube_receipt.json").write_text('{"status": "success", "video_id": "L1"}')     # 只有文件名
    sh = rc / "ep01_youtube_shorts_receipt.json"
    sh.write_text('{"ep": "ep01", "platform": "youtube_shorts", "status": "draft_ready_pending_human"}')
    odd = rc / "ep01_youtube_part2.json"           # 文件名像 youtube,内容写明是 Shorts
    odd.write_text('{"ep": "ep01", "platform": "youtube_shorts", "status": "submitted_by_human", "video_id": "S2"}')
    now = os.path.getmtime(rc / "ep01_youtube_receipt.json")
    os.utime(sh, (now + 10, now + 10))
    os.utime(odd, (now + 20, now + 20))
    yt = core._ep_publish_receipt(base, "ep01", "youtube")
    assert yt["status"] == "success" and yt["video_id"] == "L1"          # Shorts 回执更新也不串到 YouTube
    shorts = core._ep_publish_receipt(base, "ep01", SP)
    assert shorts["status"] == "submitted_by_human" and shorts["video_id"] == "S2"
    rows = {r["key"]: r for r in core._ep_publish_channels(base, "ep01")}
    assert rows["youtube"]["receipt"]["video_id"] == "L1" and rows[SP]["receipt"]["video_id"] == "S2"


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="需要 ffmpeg / ffprobe")
def test_shorts_probe_real(tmp_path):
    def mk(name, size, dur, rate=10, rotate=None):
        out = tmp_path / name
        cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=blue:s={size}:d={dur}:r={rate}",
               "-c:v", "mpeg4", str(out)]
        subprocess.run(cmd, check=True)
        if rotate is not None:
            rot = tmp_path / ("r_" + name)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-display_rotation", str(rotate), "-i", str(out),
                            "-c", "copy", str(rot)], check=True)
            return rot
        return out
    v = core._shorts_probe(mk("v.mp4", "360x640", 2))
    assert v["ok"] and (v["width"], v["height"]) == (360, 640) and v["duration"] == pytest.approx(2, abs=0.2)
    sq = core._shorts_probe(mk("sq.mp4", "480x480", 2))
    assert sq["ok"]                                                   # 正方形算 Shorts
    land = core._shorts_probe(mk("l.mp4", "640x360", 2))
    assert not land["ok"] and land["problems"] == ["landscape"]
    rot = core._shorts_probe(mk("lr.mp4", "640x360", 2, rotate=90))   # 存的是横屏、显示为竖屏
    assert rot["ok"] and (rot["width"], rot["height"]) == (360, 640)
    long_ = core._shorts_probe(mk("long.mp4", "36x64", 181, rate=1))
    assert not long_["ok"] and long_["problems"] == ["too_long"]
    assert core._shorts_probe(tmp_path / "missing.mp4") is None
    (tmp_path / "junk.mp4").write_bytes(b"not a video")
    assert core._shorts_probe(tmp_path / "junk.mp4") is None


def test_channel_row_master_and_package(tmp_path, monkeypatch):
    base = _proj(tmp_path, monkeypatch)
    calls = []
    _fake_probe(monkeypatch, {"final.mp4": (1920, 1080, 600), "final_caption.mp4": (1080, 1920, 45),
                              "part1.mp4": (1080, 1920, 170), "part2.mp4": (1080, 1920, 185)}, calls)
    rows = {r["key"]: r for r in core._ep_publish_channels(base, "ep01")}
    assert rows[SP]["shorts"] is None and "note" in rows[SP]      # 没有成片 → 无可核视频
    assert "shorts" not in rows["youtube"] and "note" not in rows["youtube"]

    _touch(base / "edit" / "ep01" / "final.mp4")
    sh = {r["key"]: r for r in core._ep_publish_channels(base, "ep01")}[SP]["shorts"]
    assert sh["source"] == "master" and not sh["ok"] and sh["files"][0]["file"] == "edit/ep01/final.mp4"
    assert sh["files"][0]["problems"] == ["landscape", "too_long"]

    _touch(base / "edit" / "ep01" / "final_caption.mp4")         # 花字版优先(与派单降级口径一致)
    calls.clear()
    sh = {r["key"]: r for r in core._ep_publish_channels(base, "ep01")}[SP]["shorts"]
    assert calls == ["final_caption.mp4"] and sh["ok"] and not sh["over_content_id"]

    pkg = base / "publish" / SP / "package" / "ep01"
    _touch(pkg / "part1.mp4")
    _touch(pkg / "part2.mp4")
    row = {r["key"]: r for r in core._ep_publish_channels(base, "ep01")}[SP]
    assert row["package_ready"] and row["package_dir"] == f"publish/{SP}/package/ep01"
    sh = row["shorts"]
    assert sh["source"] == "package" and not sh["ok"] and sh["over_content_id"]
    assert [f["ok"] for f in sh["files"]] == [True, False] and sh["files"][1]["problems"] == ["too_long"]
    assert [c["key"] for c in core._preview_videos("p", "ep01")["channels"]] == list(core.OUTPUT_PLATFORMS)


def test_dispatch_guard_and_message(tmp_path, monkeypatch):
    base = _proj(tmp_path, monkeypatch)
    sent = []

    async def fake_chat(body):
        sent.append(body)
        return {"run_id": "r1"}
    monkeypatch.setattr(core, "api_chat", fake_chat)
    table = {"final.mp4": (1920, 1080, 600)}
    _fake_probe(monkeypatch, table)
    _touch(base / "edit" / "ep01" / "final.mp4")

    # 横屏母版、没有 Shorts 包 → 409,不派单、不顺手勾选
    with pytest.raises(core.ServiceError) as ei:
        asyncio.run(core.api_publish_dispatch("p", "ep01", {"platform": SP}))
    assert ei.value.status_code == 409 and not sent
    assert "不符合 YouTube Shorts" in ei.value.detail and "edit/ep01/final.mp4 1920x1080 600s" in ei.value.detail
    assert f"publish/{SP}/package/<ep>/" in ei.value.detail
    assert SP not in (core.load_project_settings("p").get("output") or {}).get("platforms", [])
    monkeypatch.setattr(core, "ui_lang_code", lambda cfg=None: "ja")             # 非中文界面出英文
    with pytest.raises(core.ServiceError) as ei:
        asyncio.run(core.api_publish_dispatch("p", "ep01", {"platform": SP}))
    assert ei.value.detail.startswith("Not eligible for YouTube Shorts")
    monkeypatch.setattr(core, "ui_lang_code", lambda cfg=None: "zh")

    # 普通 YouTube 不核 Shorts,横屏母版照派
    def boom(path):
        raise AssertionError("youtube 渠道不该探测 Shorts")
    monkeypatch.setattr(core, "_shorts_probe", boom)
    asyncio.run(core.api_publish_dispatch("p", "ep01", {"platform": "youtube"}))
    assert "--shorts" not in sent[-1]["message"] and "YouTube Shorts" not in sent[-1]["message"]
    _fake_probe(monkeypatch, table)

    # Shorts 包:有一条超过 3 分钟 → 409
    pkg = base / "publish" / SP / "package" / "ep01"
    _touch(pkg / "part1.mp4")
    _touch(pkg / "part2.mp4")
    table.update({"part1.mp4": (1080, 1920, 90), "part2.mp4": (1080, 1920, 200)})
    with pytest.raises(core.ServiceError) as ei:
        asyncio.run(core.api_publish_dispatch("p", "ep01", {"platform": SP}))
    assert "part2.mp4" in ei.value.detail and "part1.mp4" not in ei.value.detail

    # 包全部符合(一条超过 1 分钟)→ 派单,工单写 Shorts 口径、实测结论与 Content ID 提醒,渠道顺手勾上
    table["part2.mp4"] = (1080, 1920, 170)
    n = len(sent)
    r = asyncio.run(core.api_publish_dispatch("p", "ep01", {"platform": SP}))
    assert r["package_ready"] and len(sent) == n + 1
    msg = sent[-1]["message"]
    assert f"ep01 发布到 YouTube Shorts({SP})" in msg and "skills/skill-youtube-cdp-draft/SKILL.md" in msg
    assert f"发布包 publish/{SP}/package/ep01/ 已就绪" in msg and "「YouTube Shorts」一节" in msg
    assert "--shorts" in msg and "宿主已用 ffprobe 核对" in msg and "part1.mp4 1080×1920 90 秒" in msg
    assert "Content ID" in msg and "--new-tab" in msg and f"ep01_{SP}_receipt.json" in msg
    assert SP in core.load_project_settings("p")["output"]["platforms"]

    # 探测不了(无 ffprobe)→ 不拦,工单写明以 upload --shorts 为准
    _fake_probe(monkeypatch, {})
    asyncio.run(core.api_publish_dispatch("p", "ep01", {"platform": SP}))
    assert "宿主未能核对" in sent[-1]["message"] and "Content ID" not in sent[-1]["message"]


def test_role_prompt_shorts_line(tmp_path, monkeypatch):
    _proj(tmp_path, monkeypatch)

    def line():
        return next(ln for ln in core.build_role_prompt("12-publishing/platform-adapter", "p").splitlines()
                    if ln.startswith("- 发布平台:"))
    out = core.DEFAULT_GENCONFIG["output"]
    asyncio.run(core.api_projconfig_set({"project": "p", "output": {**out, "platforms": ["youtube"]}}))
    assert "Shorts" not in line()                                      # 条件式:没勾 Shorts 不注入
    asyncio.run(core.api_projconfig_set({"project": "p", "output": {**out, "platforms": ["youtube", SP]}}))
    ln = line()
    assert "YouTube(16:9)、YouTube Shorts(9:16)" in ln and "YouTube Shorts 只认竖屏或正方形" in ln
    assert f"每条 ≤ {int(core.SHORTS_MAX_S) - 1} 秒" in ln and f"publish/{SP}/package/<ep>/" in ln


@pytest.fixture
def script(monkeypatch):
    for name in ("requests", "websocket"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    spec = importlib.util.spec_from_file_location("ytstudio_draft_shorts", SKILL_DIR / "scripts" / "ytstudio_draft.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    made = []

    class FakeCDP:
        def __init__(self, *a):
            made.append(a)

        def attach(self, *a, **kw):
            pass

        def close(self):
            pass
    monkeypatch.setattr(mod, "CDP", FakeCDP)
    monkeypatch.setattr(mod, "cmd_upload", lambda cdp, args: 0)
    mod.made = made
    return mod


def _upload_argv(tmp_path, *extra):
    (tmp_path / "t.txt").write_text("t")
    (tmp_path / "d.txt").write_text("d")
    return ["upload", "--video", str(tmp_path / "v.mp4"), "--title-file", str(tmp_path / "t.txt"),
            "--desc-file", str(tmp_path / "d.txt"), *extra]


def test_script_shorts_gate(script, tmp_path, monkeypatch, capsys):
    probed = []

    def probe(m):
        def f(path):
            probed.append(path)
            return m
        monkeypatch.setattr(script, "probe_video", f)
    probe({"width": 1920, "height": 1080, "duration": 30.0})
    assert script.main(_upload_argv(tmp_path, "--shorts")) == 2 and script.made == []   # 不碰浏览器
    out = capsys.readouterr()
    assert "SHORTS_CHECK: FAIL 1920x1080 30.0s (landscape 1920x1080)" in out.out and "not eligible" in out.err

    probe({"width": 1080, "height": 1920, "duration": 181.0})
    assert script.main(_upload_argv(tmp_path, "--shorts")) == 2 and script.made == []
    assert "181.0s > 180s" in capsys.readouterr().out

    probe({"width": 1080, "height": 1920, "duration": 75.0})
    assert script.main(_upload_argv(tmp_path, "--shorts")) == 0 and len(script.made) == 1
    out = capsys.readouterr().out
    assert "SHORTS_CHECK: OK 1080x1920 75.0s" in out and "Content ID" in out

    probe(None)                                                       # 没有 ffprobe:告警后照常
    assert script.main(_upload_argv(tmp_path, "--shorts")) == 0 and len(script.made) == 2
    assert "could not verify Shorts eligibility" in capsys.readouterr().out

    probed.clear()
    assert script.main(_upload_argv(tmp_path)) == 0 and probed == []  # 不加 --shorts 不核


def test_page_dictionaries_and_docs():
    page = (ROOT / "apps" / "web" / "static" / "preview_videos.html").read_text(encoding="utf-8")
    assert "c.shorts" in page and "c.note" in page and "function chDur" in page and "shBad" in page
    assert "youtube" not in page                                     # 渠道清单仍只来自服务端
    page_keys = ["符合 Shorts:{n} 条视频均为竖屏或正方形、≤ 3 分钟",
                 "{file}({w}×{h} · {dur})不符合 Shorts:需竖屏或正方形、≤ 3 分钟",
                 "请先让平台适配(platform-adapter)出 9:16、每条 ≤ 3 分钟的 Shorts 发布包",
                 "有视频超过 1 分钟:带任何 Content ID 版权声明的 Shorts 会被全球屏蔽"]
    for k in page_keys:
        assert f"'{k}'" in page, k
    # 服务端给的渠道说明(页面 t() 翻译)与技能适用条件
    row = next(r for r in core._ep_publish_channels(ROOT / "nonexistent", "ep01") if r["key"] == SP)
    server_keys = [row["note"],
                   core.SKILL_ACTIVATIONS["12-publishing/publisher/skill-youtube-cdp-draft"]["condition"]]
    dicts = sorted((ROOT / "apps" / "web" / "static" / "i18n").glob("??.js"))
    assert len(dicts) == 11
    for js in dicts:
        text = js.read_text(encoding="utf-8")
        for k in page_keys + server_keys:
            assert f'"{k}":' in text, (js.name, k)

    skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "## YouTube Shorts（仅当工单渠道是 youtube_shorts 时）" in skill and "--shorts" in skill
    assert "SHORTS_CHECK: FAIL" in skill and "platform` 写 `youtube_shorts`" in skill
    soul = (ROOT / "agents" / "12-publishing" / "publisher" / "SOUL.md").read_text(encoding="utf-8")
    assert "**YouTube Shorts(youtube_shorts)**" in soul and "仅当工单渠道是 youtube_shorts 时" in soul
    adapter = (ROOT / "agents" / "12-publishing" / "platform-adapter" / "SOUL.md").read_text(encoding="utf-8")
    assert "**仅当目标平台含 YouTube Shorts(`youtube_shorts`)时**" in adapter and "每条 ≤ 179 秒" in adapter
    for soul_path in ("06-art/aspect-ratio/SOUL.md", "12-publishing/platform-adapter/SOUL.md"):
        assert "YouTube/YouTube Shorts/Bilibili" in (ROOT / "agents" / soul_path).read_text(encoding="utf-8")
