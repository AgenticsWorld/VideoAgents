"""Instagram 发布渠道(instagram,2026-11-01):9:16、网页版 instagram.com「新帖子」半自动填 Reel,用户亲手点 Share;
成片发布页与派单前 ffprobe 实测只作提醒(横屏 / 超过 3 分钟不拦);工单按项目类型写 AI 标签;
igweb_draft.py:#标签 ≤ 5 个、caption ≤ 2,200 字符在开浏览器前处理,导航前先查标签页里有没有待分享的对话框。"""
import asyncio
import importlib.util
import json
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.runtime import core  # noqa: E402

SKILL_DIR = ROOT / "agents" / "12-publishing" / "publisher" / "skills" / "skill-instagram-cdp-draft"
IG = core.INSTAGRAM_PLATFORM


def _proj(tmp_path, monkeypatch, name="p", settings="{}"):
    monkeypatch.setattr(core, "PROJECTS_DIR", tmp_path / "projects")

    async def no_op(*args, **kwargs):
        return {"ok": True}
    monkeypatch.setattr(core, "_notify_settings_change", no_op)
    monkeypatch.setattr(core, "ui_lang_code", lambda cfg=None: "zh")
    base = core.PROJECTS_DIR / name
    base.mkdir(parents=True)
    (base / "settings.json").write_text(settings)
    return base


def _fake_probe(monkeypatch, table, calls=None):
    """按文件名给 _video_probe 结论:(宽, 高, 秒) 或 None。"""
    def probe(path):
        if calls is not None:
            calls.append(path.name)
        v = table.get(path.name)
        return None if v is None else {"width": v[0], "height": v[1], "duration": v[2]}
    monkeypatch.setattr(core, "_video_probe", probe)


def _touch(p: Path):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"0")
    return p


def test_registry():
    keys = list(core.OUTPUT_PLATFORMS)
    assert keys.index(IG) == keys.index("tiktok") + 1
    assert core.OUTPUT_PLATFORMS[IG] == ("Instagram", "9:16")
    assert core.PUBLISH_PLATFORM_SKILLS[IG] == "skill-instagram-cdp-draft"
    assert IG not in core.DEFAULT_GENCONFIG["output"]["platforms"]      # 不进存量回退默认清单
    assert core.SKILL_ACTIVATIONS["12-publishing/publisher/skill-instagram-cdp-draft"] == {
        "kind": "soul", "condition": "发布到 Instagram"}
    assert (SKILL_DIR / "SKILL.md").is_file() and (SKILL_DIR / "scripts" / "igweb_draft.py").is_file()
    assert core.INSTAGRAM_AI_LABEL_TYPES <= set(core.project_types.TYPES)
    assert "code-video" not in core.INSTAGRAM_AI_LABEL_TYPES and "mashup" not in core.INSTAGRAM_AI_LABEL_TYPES


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="需要 ffmpeg / ffprobe")
def test_video_probe_real_shared_with_shorts(tmp_path):
    raw, v = tmp_path / "l.mp4", tmp_path / "lr.mp4"     # 存的是横屏、显示为竖屏
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=640x360:r=10:d=2",
                    "-c:v", "mpeg4", str(raw)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-display_rotation", "90", "-i", str(raw), "-c", "copy", str(v)],
                   check=True)
    r = core._video_probe(v)
    assert r == {"width": 360, "height": 640, "duration": pytest.approx(2, abs=0.2)}
    assert set(core._shorts_probe(v)) == {"width", "height", "duration", "ok", "problems"} and core._shorts_probe(v)["ok"]
    assert core._video_probe(tmp_path / "missing.mp4") is None


def test_instagram_check_master_and_package(tmp_path, monkeypatch):
    base = _proj(tmp_path, monkeypatch)
    calls = []
    _fake_probe(monkeypatch, {"final.mp4": (1920, 1080, 200), "final_caption.mp4": (1080, 1920, 45),
                              "part1.mp4": (1080, 1920, 170), "part2.mp4": (1080, 1080, 185)}, calls)
    rows = {r["key"]: r for r in core._ep_publish_channels(base, "ep01")}
    assert rows[IG]["reels"] is None and "Reels" in rows[IG]["note"]      # 没有成片 → 无可核视频
    assert "reels" not in rows["tiktok"] and "reels" not in rows[core.SHORTS_PLATFORM]

    _touch(base / "edit" / "ep01" / "final.mp4")
    rl = {r["key"]: r for r in core._ep_publish_channels(base, "ep01")}[IG]["reels"]
    assert rl["source"] == "master" and not rl["ok"] and rl["landscape"] and rl["long"]
    assert rl["files"] == [{"file": "edit/ep01/final.mp4", "width": 1920, "height": 1080, "duration": 200,
                            "warnings": ["landscape", "long"]}]

    _touch(base / "edit" / "ep01" / "final_caption.mp4")           # 花字版优先(与派单降级口径一致)
    calls.clear()
    rl = {r["key"]: r for r in core._ep_publish_channels(base, "ep01")}[IG]["reels"]
    assert set(calls) == {"final_caption.mp4"} and rl["ok"] and not rl["landscape"] and not rl["long"]  # Shorts 行也探测同一条

    pkg = base / "publish" / IG / "package" / "ep01"
    _touch(pkg / "part1.mp4")
    _touch(pkg / "part2.mp4")
    row = {r["key"]: r for r in core._ep_publish_channels(base, "ep01")}[IG]
    assert row["package_ready"] and row["package_dir"] == f"publish/{IG}/package/ep01"
    rl = row["reels"]
    assert rl["source"] == "package" and not rl["ok"] and rl["long"] and not rl["landscape"]   # 正方形不算横屏
    assert [f["warnings"] for f in rl["files"]] == [[], ["long"]]

    _fake_probe(monkeypatch, {"part1.mp4": (1080, 1920, 10)})       # 有一条读不出 → 整体不下结论
    assert {r["key"]: r for r in core._ep_publish_channels(base, "ep01")}[IG]["reels"] is None


def test_dispatch_never_blocks_and_message(tmp_path, monkeypatch):
    base = _proj(tmp_path, monkeypatch)
    sent = []

    async def fake_chat(body):
        sent.append(body)
        return {"run_id": "r1"}
    monkeypatch.setattr(core, "api_chat", fake_chat)
    _fake_probe(monkeypatch, {"final.mp4": (1920, 1080, 200)})
    _touch(base / "edit" / "ep01" / "final.mp4")

    # 横屏、超过 3 分钟的母版也照派(Reels 照样能发),工单写实测提醒;渠道顺手勾上
    r = asyncio.run(core.api_publish_dispatch("p", "ep01", {"platform": IG}))
    assert r["ok"] and not r["package_ready"] and len(sent) == 1
    msg = sent[-1]["message"]
    assert f"ep01 发布到 Instagram({IG})" in msg and "skills/skill-instagram-cdp-draft/SKILL.md" in msg
    assert "网页版发出的视频一律是 Reels" in msg and "edit/ep01/final.mp4 1920×1080 200 秒" in msg
    assert "有横屏视频" in msg and "超过 3 分钟" in msg and "不自行裁切" in msg
    assert f"≤ {core.INSTAGRAM_CAPTION_MAX} 字符" in msg and f"#标签 ≤ {core.INSTAGRAM_HASHTAG_MAX} 个" in msg
    assert "不能传字幕文件" in msg and "「分享 / Share」" in msg and "--new-tab" in msg
    assert "项目类型「影视剧」" in msg and "upload 加 --ai-label" in msg       # 默认影视剧 → 加 AI 标签
    assert f"ep01_{IG}_receipt.json" in msg
    assert IG in core.load_project_settings("p")["output"]["platforms"]

    # 读不出视频 → 工单写未能核对,照派
    _fake_probe(monkeypatch, {})
    asyncio.run(core.api_publish_dispatch("p", "ep01", {"platform": IG}))
    assert "宿主未能核对视频尺寸与时长" in sent[-1]["message"]

    # 其它渠道不探测 Reels、不写 Instagram 口径
    def boom(path):
        raise AssertionError("tiktok 渠道不该做 Reels 实测")
    monkeypatch.setattr(core, "_ep_instagram_check", boom)
    asyncio.run(core.api_publish_dispatch("p", "ep01", {"platform": "tiktok"}))
    assert "Reels" not in sent[-1]["message"] and "--ai-label" not in sent[-1]["message"]


@pytest.mark.parametrize("ptype,label,ai", [("code-video", "动效短片", False), ("mashup", "混剪", False),
                                            ("digital-human", "数字人", True), ("audio-to-video", "音频配画", True)])
def test_dispatch_ai_label_by_project_type(tmp_path, monkeypatch, ptype, label, ai):
    _proj(tmp_path, monkeypatch, settings=json.dumps({"project": {"type": ptype}}))
    msg = core.publish_dispatch_message("p", "ep01", IG, False, ["final.mp4"], reels=None)
    assert f"项目类型「{label}」" in msg
    assert ("upload 加 --ai-label" in msg) is ai and ("upload 不加 --ai-label" in msg) is (not ai)


def test_role_prompt_instagram_line(tmp_path, monkeypatch):
    _proj(tmp_path, monkeypatch)

    def line():
        return next(ln for ln in core.build_role_prompt("12-publishing/platform-adapter", "p").splitlines()
                    if ln.startswith("- 发布平台:"))
    out = core.DEFAULT_GENCONFIG["output"]
    asyncio.run(core.api_projconfig_set({"project": "p", "output": {**out, "platforms": ["tiktok"]}}))
    assert "Instagram" not in line()                                   # 条件式:没勾 Instagram 不注入
    asyncio.run(core.api_projconfig_set({"project": "p", "output": {**out, "platforms": ["tiktok", IG]}}))
    ln = line()
    assert "TikTok(9:16)、Instagram(9:16)" in ln and "Instagram 网页版发出的视频一律是 Reels" in ln
    assert f"publish/{IG}/package/<ep>/" in ln and "不收字幕文件" in ln and "#标签 ≤ 5 个" in ln
    assert "3:4" in ln and "工单没写时不切" in ln


# ---------------------------------------------------------------------------
# igweb_draft.py
# ---------------------------------------------------------------------------

@pytest.fixture
def script(monkeypatch):
    for name in ("requests", "websocket"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    spec = importlib.util.spec_from_file_location("igweb_draft_test", SKILL_DIR / "scripts" / "igweb_draft.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    return mod


def test_trim_hashtags_and_caption_limits(script, tmp_path, capsys):
    cap = "第一集 标题\n简介里有 C# 和 &#38; 不算\n\n#短剧 #悬疑 #ep01 #Drama #AI短剧 #多余1  #多余2"
    out, dropped = script.trim_hashtags(cap, 5)
    assert dropped == ["#多余1", "#多余2"]
    assert out.endswith("#短剧 #悬疑 #ep01 #Drama #AI短剧") and "C# 和 &#38;" in out
    assert script.trim_hashtags("a #x #y", 5) == ("a #x #y", [])
    assert script.utf16_len("😀a") == 3                              # 与页面计数器同口径

    f = tmp_path / "c.txt"
    f.write_text(cap, encoding="utf-8")
    assert script.prepare_caption(str(f), 5) == out
    assert "HASHTAGS_TRIMMED: kept 5, dropped #多余1 #多余2" in capsys.readouterr().out
    f.write_text("x" * 2201, encoding="utf-8")
    assert script.prepare_caption(str(f), 5) is None
    assert "2201 characters" in capsys.readouterr().err
    f.write_text("  ", encoding="utf-8")
    assert script.prepare_caption(str(f), 5) is None


def test_main_checks_before_browser(script, tmp_path, monkeypatch, capsys):
    made = []

    class FakeCDP:
        def __init__(self, *a):
            made.append(a)

        def attach(self, *a, **kw):
            self.kw = kw

        def close(self):
            pass
    monkeypatch.setattr(script, "CDP", FakeCDP)
    got = {}
    monkeypatch.setattr(script, "cmd_upload", lambda cdp, args, caption: got.update(args=args, caption=caption) or 0)
    monkeypatch.setattr(script, "probe_video", lambda p: {"width": 1920, "height": 1080, "duration": 200.0})
    cap = tmp_path / "c.txt"
    argv = ["upload", "--video", str(tmp_path / "v.mp4"), "--caption-file", str(cap)]

    cap.write_text("y" * 2300, encoding="utf-8")                    # caption 超长:不碰浏览器
    assert script.main(argv) == 2 and made == []

    cap.write_text("标题\n#a #b", encoding="utf-8")
    assert script.main(argv + ["--cover", str(tmp_path / "c.webp"), "--ai-label", "--new-tab"]) == 0
    out = capsys.readouterr().out
    assert "VIDEO_CHECK: 1920x1080 200.0s" in out and "landscape video" in out and "over 3 minutes" in out
    assert "cover must be JPG or PNG" in out and got["args"].cover is None and got["args"].ai_label
    assert got["caption"] == "标题\n#a #b" and len(made) == 1

    with pytest.raises(SystemExit):
        script.main(["screenshot", str(tmp_path / "s.png"), "--new-tab"])


class FlowCDP:
    """按 JS 内容回放页面状态的假 CDP:记录所有脚本与文件输入,供流程与 node 语法检查用。"""

    def __init__(self, steps, dialog_open=False):
        self.steps, self.js, self.files, self.typed = list(steps), [], [], []
        self.dialog_open = dialog_open
        self.navigated = False
        self.caption = ""

    def navigate(self, url, settle=0):
        self.navigated = True

    def send(self, method, params=None, timeout=30):
        if method == "Input.insertText":
            self.typed.append(params["text"])
            self.caption = params["text"]
        return {}

    def set_files_js(self, js, files):
        self.js.append(js)
        self.files.append((js, files))

    def evaluate(self, expr, user_gesture=False):
        self.js.append(expr)
        if "a[href^=\"/direct/\"]" in expr:
            return True
        if "M21 11h-8V3" in expr:
            return "plus icon"
        if "blur()" in expr and "IG." not in expr:
            return None
        body = expr[len(self._lib):] if expr.startswith(self._lib) else expr
        if body == "IG.step()":
            if not self.navigated:
                return "caption" if self.dialog_open else "none"
            return self.steps.pop(0) if len(self.steps) > 1 else self.steps[0]
        if "input[type=file][accept*=video]" in body:
            return True
        if "others" in body:              # dismiss_notices
            return ""
        if "ratioButtons" in body:
            return "ok:Original"
        if "IG.headerButton(d)" in body and "b.click()" in body:
            return "Next"
        if "execCommand('selectAll'" in body:
            return True
        if "e.innerText" in body:
            return self.caption
        if "ИИ" in body:
            return "on"
        return None

    def ig(self, body, user_gesture=True):
        return self.evaluate(self._lib + body, user_gesture)


def test_upload_flow_order_and_js_syntax(script, tmp_path, capsys):
    FlowCDP._lib = script._JS_LIB
    args = types.SimpleNamespace(video="/abs/v.mp4", cover="/abs/c.png", ai_label=True)
    cdp = FlowCDP(["crop", "edit", "caption"])
    assert script.cmd_upload(cdp, args, "标题\n#a") == 0
    out = capsys.readouterr().out
    order = [ln for ln in out.splitlines() if ln.startswith("[igweb]") or ln.startswith("FILL_STATUS")]
    assert [x for x in ("Opened New post", "Uploading video", "Crop set to Original", "Cover image submitted",
                        "Caption set", "AI label: on", "FILL_STATUS: READY_FOR_HUMAN_SUBMIT")
            if any(x in ln for ln in order)] == ["Opened New post", "Uploading video", "Crop set to Original",
                                                "Cover image submitted", "Caption set", "AI label: on",
                                                "FILL_STATUS: READY_FOR_HUMAN_SUBMIT"]
    assert [f for _, f in cdp.files] == [["/abs/v.mp4"], ["/abs/c.png"]] and cdp.typed == ["标题\n#a"]
    assert "keep this tab open" in out

    # 标签页里已有待分享的对话框 → 导航前就拒绝,什么都不碰
    busy = FlowCDP(["none"], dialog_open=True)
    assert script.cmd_upload(busy, args, "x") == 1 and not busy.navigated and not busy.files
    assert "--new-tab" in capsys.readouterr().err

    # 页面里跑的每段脚本都能被 JS 解析(node 编译,不执行)
    node = shutil.which("node")
    if not node:
        pytest.skip("需要 node")
    lib = script._JS_LIB
    pairs = [[lib, x[len(lib):]] if x.startswith(lib) else ["", x]
             for x in sorted(set(cdp.js) | {script._BUSY_JS, script._LOGGED_IN_JS})]
    assert len(pairs) >= 12
    src = "for (const [p, b] of %s) { new Function(p + '\\nreturn (' + b + ');'); }" % json.dumps(pairs)
    r = subprocess.run([node, "-e", src], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    # 库本身可加载,导出流程要用的函数
    probe = "var document={querySelectorAll:()=>[]};" + script._JS_LIB + \
        "console.log(JSON.stringify(Object.keys(IG).sort()), IG.step());"
    r = subprocess.run([node, "-e", probe], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    keys, step = r.stdout.strip().rsplit(" ", 1)
    assert step == "none" and {"createDialog", "headerButton", "editor", "coverInput", "cropToggle",
                               "ratioButtons", "step"} <= set(json.loads(keys))


def test_page_dictionaries_and_docs():
    page = (ROOT / "apps" / "web" / "static" / "preview_videos.html").read_text(encoding="utf-8")
    assert "c.reels" in page and "instagram" not in page.lower().replace("instagram:", "")  # 渠道键不写死
    page_keys = ["{n} 条视频均为竖屏或正方形、≤ 3 分钟",
                 "{file}({w}×{h})是横屏:发成 Reels 上下会有黑边",
                 "{file}({dur})超过 3 分钟:这样的 Reels 基本不会推荐给非粉丝"]
    for k in page_keys:
        assert f"'{k}'" in page, k
    row = next(r for r in core._ep_publish_channels(ROOT / "nonexistent", "ep01") if r["key"] == IG)
    server_keys = [row["note"], core.SKILL_ACTIVATIONS["12-publishing/publisher/skill-instagram-cdp-draft"]["condition"]]
    dicts = sorted((ROOT / "apps" / "web" / "static" / "i18n").glob("??.js"))
    assert len(dicts) == 11
    for js in dicts:
        text = js.read_text(encoding="utf-8")
        for k in page_keys + server_keys:
            assert f'"{k}":' in text, (js.name, k)

    skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    for s in ("永不代点", "没有草稿", "--new-tab", "HASHTAGS_TRIMMED", "--ai-label", "Original",
              "<ep>_instagram_receipt.json", "工单没写时"):
        assert s in skill, s
    soul = (ROOT / "agents" / "12-publishing" / "publisher" / "SOUL.md").read_text(encoding="utf-8")
    assert "**Instagram(instagram)**" in soul and "skills/skill-instagram-cdp-draft/SKILL.md" in soul
    adapter = (ROOT / "agents" / "12-publishing" / "platform-adapter" / "SOUL.md").read_text(encoding="utf-8")
    assert "**仅当目标平台含 Instagram(`instagram`)时**" in adapter and "不得自行烧录" in adapter
    seo = (ROOT / "agents" / "12-publishing" / "seo" / "SOUL.md").read_text(encoding="utf-8")
    assert "**仅当目标平台含 Instagram(`instagram`)时**" in seo and "#标签每条最多 5 个" in seo
    for soul_path in ("06-art/aspect-ratio/SOUL.md", "12-publishing/platform-adapter/SOUL.md"):
        assert "TikTok/Instagram/抖音" in (ROOT / "agents" / soul_path).read_text(encoding="utf-8")
    for readme in ("README.md", "README.zh-CN.md"):
        assert "TikTok · Instagram ·" in (ROOT / readme).read_text(encoding="utf-8")
