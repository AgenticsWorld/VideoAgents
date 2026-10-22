"""演示视频(项目类型 code-video,2026-10-20):lemo-opuscar 页面契约 + 宿主渲染工具链 + 项目类型接入 + 代码作曲 / 代码音效。"""
import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from modules import codevideo as CV
from modules.codevideo import timeline as TL
from services.runtime import core, project_types

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _restore_event_loop():
    """本文件(及被测模块)用 asyncio.run,跑完会把当前事件循环置空;排在后面、用 get_event_loop() 的测试会因此失败。"""
    yield
    asyncio.set_event_loop(asyncio.new_event_loop())


def _tl(**kw):
    tl = {"schema": "codevideo_timeline/1", "title": "t", "fps": 24, "size": [96, 54], "dur": 1.0,
          "sections": [{"id": "a", "title": "开场", "t0": 0, "t1": 0.5}, {"id": "b", "title": "收尾", "t0": 0.5, "t1": 1.0}],
          "shots": [{"id": "a1", "section": "a", "t0": 0, "t1": 0.5, "size": "wide", "key": True},
                    {"id": "b1", "section": "b", "t0": 0.5, "t1": 0.8, "size": "close"},
                    {"id": "b2", "section": "b", "t0": 0.8, "t1": 1.0, "size": "insert"}],
          "lines": [{"id": "l1", "t": 0.1}], "hits": [{"id": "h", "t": 0.5}]}
    tl.update(kw)
    return tl


# ---------------------------------------------------------------- 上游同步与风格库

def test_vendored_library_and_styles_match_manifest():
    up = CV.upstream()
    assert up["repo"] == "lemomo-ai/lemo-opuscar" and len(up["rev"]) == 40 and up["three"].startswith("0.170.")
    assert (CV.LIB_DIR / "core" / "lib.js").is_file() and (CV.VENDOR_THREE / "three.module.js").is_file()
    sampler = (CV.LIB_DIR / "core" / "audio" / "sampler.py").read_text(encoding="utf-8")
    assert "CODEVIDEO_INSTRUMENTS" in sampler and "_auto_fetch" in sampler and "fetch.sh" not in sampler
    slugs = CV.style_slugs()
    assert len(slugs) == 43 and len(set(slugs)) == 43
    for s in slugs:
        for name in ("STYLE.md", "DEMO.md", "style.json"):
            assert (CV.STYLES_DIR / s / name).is_file(), (s, name)
        assert (CV.GALLERY_JSON.parent / "frames" / f"{s}.webp").is_file()
        assert f"demo:{s}" in up["files"]
    assert {p for p in up["release"]["packs"]} >= {"hdri", "instruments-vsco2ce", "instruments-salamander"}
    # 仓库 .gitignore 忽略 node_modules/ 与 build/:宿主库不能落在这两种路径下
    r = subprocess.run(["git", "check-ignore", "-q", str(CV.VENDOR_THREE / "three.module.js")], cwd=ROOT)
    assert r.returncode == 1


def test_sync_script_check_is_clean():
    src = Path("/Users/ericfish/Downloads/333_Agentics/_archive/51-zhuguang/lemo-opuscar")
    if not (src / "AGENTS.md").is_file():
        pytest.skip("本机没有 lemo-opuscar 克隆")
    r = subprocess.run([sys.executable, "scripts/sync_lemo_opuscar.py", "--src", str(src), "--check"], cwd=ROOT,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_render_size_by_aspect_and_resolution():
    assert CV.render_size({"output": {"aspect_preset": "youtube"}}) == (1920, 1080)
    assert CV.render_size({"output": {"aspect_preset": "douyin", "final_resolution": "720p"}}) == (720, 1280)
    assert CV.render_size({"output": {"aspect_preset": "custom", "aspect_custom": "1:1", "final_resolution": "4k"}}) == (2160, 2160)
    w, h = CV.render_size({"output": {"aspect_preset": "cinema"}})
    assert h == 1080 and w % 2 == 0 and abs(w / h - 2.35) < 0.01


# ---------------------------------------------------------------- 时间轴

def test_timeline_validate_and_frames():
    tl = TL.validate(_tl())
    assert [s["group_id"] for s in tl["sections"]] == ["grp0010", "grp0020"]
    assert [s["scene_no"] for s in tl["sections"]] == ["S010", "S020"]
    assert [sh["shot_id"] for s in tl["sections"] for sh in s["shots"]] == ["sh0010", "sh0020", "sh0030"]
    assert tl["frames"] == 24 and sum(s["f1"] - s["f0"] for s in tl["sections"]) == 24
    # 累计取整:三段 1/3 秒,各段帧数之和恰好等于整片帧数
    odd = TL.validate(_tl(dur=1.0, sections=[{"id": f"s{i}", "t0": i / 3, "t1": (i + 1) / 3} for i in range(3)], shots=[]))
    assert sum(s["f1"] - s["f0"] for s in odd["sections"]) == 24
    assert TL.at(tl, 0.85)[1]["id"] == "b2" and TL.at(tl, 1.0)[0]["id"] == "b"


@pytest.mark.parametrize("patch,needle", [
    ({"fps": 60}, "fps"),
    ({"size": [97, 54]}, "size"),
    ({"sections": [{"id": "a", "t0": 0, "t1": 0.4}, {"id": "b", "t0": 0.5, "t1": 1.0}]}, "不相接"),
    ({"sections": [{"id": "a", "t0": 0, "t1": 0.5}, {"id": "a", "t0": 0.5, "t1": 1.0}]}, "重复"),
    ({"shots": [{"id": "x", "section": "a", "t0": 0, "t1": 0.3}]}, "没有覆盖到段尾"),
    ({"dur": 2.0}, "末段结尾"),
    ({"hits": [{"t": 5}]}, "越界"),
])
def test_timeline_rejects(patch, needle):
    with pytest.raises(CV.CodeVideoError) as e:
        TL.validate(_tl(**patch))
    assert needle in str(e.value)


def test_sync_writes_directing_and_refuses_foreign_files(tmp_path):
    base = tmp_path / "p"
    (base / CV.NS).mkdir(parents=True)
    (base / CV.NS / "timeline.json").write_text(json.dumps(_tl()), encoding="utf-8")
    res = TL.sync(base)
    assert res == {"sections": 2, "shots": 3, "frames": 24, "dur": 1.0, "fps": 24, "size": [96, 54]}
    sl = json.loads((base / "directing" / "ep01" / "shot_list.json").read_text(encoding="utf-8"))
    assert [g["group_id"] for g in sl["generation_groups"]] == ["grp0010", "grp0020"]
    assert sl["shots"][1]["storyboard_ref"] == "S020/order:1" and sl["total_duration_s"] == 1.0
    from modules import storyboard_board as sbb
    board = sbb.load_board(base, "ep01")
    assert [sc["scene_no"] for sc in board["scenes"]] == ["S010", "S020"]
    assert board["scenes"][1]["shots"][0]["final"][0]["group"] == "grp0020"
    (base / "directing" / "ep01" / "storyboard.json").write_text(json.dumps({"scenes": []}))
    with pytest.raises(CV.CodeVideoError):
        TL.sync(base)


# ---------------------------------------------------------------- 静态服务

def test_serve_mapping_traversal_host_and_preview_bridge(tmp_path):
    from modules.codevideo.serve import FilmServer, _host_ok
    film = tmp_path / "film"
    film.mkdir()
    (film / "index.html").write_text("<html><head><title>x</title></head><body></body></html>")
    srv = FilmServer(film=film)
    assert srv.resolve("/core/lib.js") == CV.LIB_DIR / "core" / "lib.js"
    assert srv.resolve("/node_modules/three/build/three.module.js") == CV.VENDOR_THREE / "three.module.js"
    assert srv.resolve("/node_modules/three/examples/jsm/postprocessing/Pass.js").is_file()
    assert srv.resolve("/@film/../secret") is None and srv.resolve("/core/../../x") is None
    srv.httpd.server_close()
    assert _host_ok("127.0.0.1:8632", {"localhost"}) and _host_ok("[::1]:8632", set()) and _host_ok("localhost:1", {"localhost"})
    assert not _host_ok("evil.example:8632", {"localhost"})

    projects = tmp_path / "projects"
    (projects / "demo1" / CV.NS).mkdir(parents=True)
    (projects / "demo1" / CV.NS / "index.html").write_text("<html><head></head><body>hi</body></html>")
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with FilmServer(projects=projects, preview=True) as pv:
        with opener.open(pv.url("index.html", "demo1"), timeout=5) as r:
            body, csp = r.read().decode(), r.headers.get("Content-Security-Policy") or ""
        assert '<script src="/@bridge.js"></script>' in body and "connect-src 'self'" in csp and "form-action 'none'" in csp
        with opener.open(f"http://127.0.0.1:{pv.port}/@bridge.js", timeout=5) as r:
            assert b"window.render" in r.read()
        req = urllib.request.Request(pv.url("index.html", "demo1"), method="POST", data=b"x")
        with pytest.raises(urllib.error.HTTPError) as e:
            opener.open(req, timeout=5)
        assert e.value.code == 405


# ---------------------------------------------------------------- 项目类型

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "PROJECTS_DIR", tmp_path / "projects")
    core.PROJECTS_DIR.mkdir()
    sent = []

    async def fake_chat(body):
        sent.append(body)
        return {"ok": True, "run_id": "r1"}

    async def no_op(*a, **k):
        return {"ok": True}
    monkeypatch.setattr(core, "api_chat", fake_chat)
    monkeypatch.setattr(core, "_notify_settings_change", no_op)
    return sent


def _create(body):
    return asyncio.run(core.api_projects_create(body))


def test_code_video_project_create_settings_brief_and_init(env):
    with pytest.raises(core.ServiceError) as e:
        _create({"name": "cv0", "project_type": "code-video", "code_video": {}})
    assert e.value.status_code == 400 and "code_style" in e.value.detail
    with pytest.raises(core.ServiceError):
        _create({"name": "cv0", "project_type": "code-video", "code_video": {"code_style": "blueprint", "fps": 60}})
    with pytest.raises(core.ServiceError):
        _create({"name": "cv0", "project_type": "code-video", "code_video": {"code_style": "nope"}})
    assert not (core.PROJECTS_DIR / "cv0").exists()
    _create({"name": "cv1", "project_type": "code-video", "novel": "咖啡从种子到杯子", "brief": "给新员工看",
             "code_video": {"code_style": "iso-infographic", "length_s": 50, "narration": False},
             "settings": {"output": {"aspect_preset": "douyin", "narration_enabled": True}}})
    cfg = core.load_project_settings("cv1")
    assert cfg["project"] == {"type": "code-video", "code_style": "iso-infographic", "storyboard_review": True,
                              "narration": False, "subtitles": True, "length_s": 50, "fps": 24, "music_source": "code"}
    assert cfg["output"]["narration_enabled"] is False          # 主流程旁白开关照常强制关,本类型用自己的 narration
    pdir = core.PROJECTS_DIR / "cv1"
    brief = (pdir / "brief.md").read_text(encoding="utf-8")
    assert "agents/workflows/codevideo/styles/iso-infographic/STYLE.md" in brief and "给新员工看" in brief
    assert (pdir / "refs" / "text" / "transcript.txt").read_text(encoding="utf-8").strip() == "咖啡从种子到杯子"
    msg = env[0]["message"]
    assert "agents/workflows/codevideo.yaml" in msg and "iso-infographic" in msg and "先审故事板:开" in msg
    assert "cv0-ingest" in msg and "现在不要派单" in msg
    sec = project_types.orchestrator_section(cfg["project"], "data/projects/cv1", [a["id"] for a in core.list_agents()])
    assert "cv2-voice 置 skipped" in sec and "cv1-board 与 gc1(CVH1)照常派" in sec and "18-code-video/scene-coder" in sec


def test_code_video_agents_dag_and_lanes():
    import yaml
    dag = yaml.safe_load((ROOT / "agents/workflows/codevideo.yaml").read_text(encoding="utf-8"))
    assert dag["project_type"] == "code-video"
    nodes = {t["id"]: t for ph in dag["phases"] for t in ph["tasks"]}
    for nid in ("cv0-ingest", "cv1-treatment", "cv1-board", "gc1", "cv2-voice", "cv2-score", "cv3-engine", "cv3-sec",
                "cv3-render", "cv4-mix", "cv4-master", "cv5-qa", "gc2"):
        assert nid in nodes, nid
    for t in nodes.values():
        if t.get("agent"):
            assert (ROOT / "agents" / t["agent"] / "SOUL.md").is_file(), t["agent"]
    assert core._wf_phase_of({"id": "cv3-sec-grp0010"}) == "cv3" and core._wf_phase_of({"id": "gc1"}) == "cv1"
    assert core._wf_phase_of({"id": "gc2"}) == "cv5"
    assert project_types.of_agent("18-code-video/scene-coder") == "code-video"
    assert project_types.of_agent("11-qa/code-video-qa") == "code-video"
    assert core.REVISION_KIND_AGENTS["codevideo"][0] == "18-code-video/scene-coder"


def test_post_picture_recipes_blocked_for_code_video(env):
    _create({"name": "cv2", "project_type": "code-video", "code_video": {"code_style": "blueprint"}})
    with pytest.raises(core.ServiceError) as e:
        asyncio.run(core.api_post_recipe_create("cv2", "ep01", {"kind": "deflicker", "scope": {"group_id": "grp0010"}}))
    assert e.value.status_code == 400 and "代码预览" in e.value.detail
    with pytest.raises(core.ServiceError):
        asyncio.run(core.api_post_cutout("cv2", "ep01", {"group_id": "grp0010", "base_v": 0, "cuts": [{"t0": 0, "t1": 0.1}]}))


def test_code_video_notes_and_board_notes_route_to_code_reviser(env):
    from services.runtime import codevideo_api as cva
    _create({"name": "cv3", "project_type": "code-video", "code_video": {"code_style": "blueprint"}})
    base = core.PROJECTS_DIR / "cv3"
    (base / CV.NS).mkdir(exist_ok=True)
    (base / CV.NS / "timeline.json").write_text(json.dumps(_tl()), encoding="utf-8")
    TL.sync(base)
    d = cva.note_set("cv3", {"t": 0.85, "text": "这里太快"})
    assert d["notes"][0]["section"] == "b" and d["notes"][0]["shot"] == "b2"
    nid = d["notes"][0]["id"]
    assert cva.note_set("cv3", {"id": nid, "text": "这里太快,慢一点"})["notes"][0]["text"].endswith("慢一点")
    info = cva.preview_info("cv3")
    assert info["timeline"]["frames"] == 24 and info["page"] == "/@film/cv3/index.html" and not info["has_page"]
    env.clear()
    res = asyncio.run(cva.notes_submit("cv3", {}))
    assert res["count"] == 1 and env[0]["agent"] == core.REVISER_ID and env[0]["target"]["kind"] == "codevideo"
    assert "0.85s" in env[0]["message"] and cva.load_notes(base)["notes"] == []
    from modules import storyboard_board as sbb
    sbb.update_note(base, "ep01", "S020-02", "收尾镜头换个角度")
    env.clear()
    asyncio.run(core.api_board_notes_submit("cv3", "ep01", {}))
    assert env[0]["target"]["kind"] == "codevideo" and "[故事板 S020-02]" in env[0]["message"]
    assert sbb.load_notes(base, "ep01")["notes"] == {}
    with pytest.raises(core.ServiceError):
        cva.preview_info("nonexistent")


# ---------------------------------------------------------------- 代码作曲 / 代码音效

SCORE = """
import os, sys
import numpy as np, soundfile as sf
sys.path.insert(0, os.environ['LIB'])
from core.audio import sfx
d = float(os.environ.get('CODE_AUDIO_DURATION') or 1.5)
t = np.arange(int(sfx.SR * d)) / sfx.SR
x = (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
sf.write(os.environ['CODE_AUDIO_OUT'], np.stack([x, x], 1), sfx.SR)
"""


@pytest.fixture
def gencfg(tmp_path, monkeypatch):
    from modules import genmedia
    p = tmp_path / "genconfig.json"
    p.write_text(json.dumps({"music": {"provider": "code", "code": {"auto_fetch": False}}, "sfx": {"provider": "code"}}))
    monkeypatch.setenv("VIDEOAGENTS_CONFIG_PATH", str(p))
    monkeypatch.setattr(genmedia, "CONFIG_PATH", p)
    return p


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="需要 ffmpeg")
def test_genmedia_code_music_and_sfx(tmp_path, gencfg):
    from modules import genmedia
    script = tmp_path / "score.py"
    script.write_text(SCORE)
    out = genmedia.generate_music("test", str(tmp_path / "m.mp3"), duration_s=1.2, script=str(script))
    meta = json.loads((tmp_path / "m.meta.json").read_text(encoding="utf-8"))
    assert Path(out).is_file() and meta["music"]["provider"] == "code" and meta["music"]["audio"]["duration_s"] == 1.2
    with pytest.raises(RuntimeError) as e:
        genmedia.generate_music("test", str(tmp_path / "x.wav"))
    assert "--script" in str(e.value)
    outs = genmedia.generate_sfx("click", str(tmp_path / "fx.wav"), script=str(script))
    assert Path(outs[0]).is_file() and json.loads((tmp_path / "fx.meta.json").read_text(encoding="utf-8"))["sfx"]["provider"] == "code"
    with pytest.raises(RuntimeError):
        genmedia.generate_sfx("click", str(tmp_path / "fx2.wav"), provider="openverse", script=str(script))


def test_genconfig_defaults_and_validation(monkeypatch, tmp_path):
    assert core.DEFAULT_GENCONFIG["music"]["provider"] == "code"
    assert core.DEFAULT_GENCONFIG["music"]["code"] == {"auto_fetch": True}
    assert "code" in core.DEFAULT_GENCONFIG["sfx"] and core.DEFAULT_GENCONFIG["sfx"]["provider"] == "code"


@pytest.mark.parametrize("client_version, want", [("", "code"), ("1.0.40", "agentics")])
def test_music_default_provider_desktop(tmp_path, client_version, want):
    """桌面端(拉起服务时设 VIDEOAGENTS_CLIENT_VERSION)新装音乐默认 Agentics,网页版仍为代码作曲;
    默认值在 import 时定,另起进程读。已保存的音乐渠道照旧优先,音效默认不随之变。"""
    env = {**os.environ, "VIDEOAGENTS_DATA_DIR": str(tmp_path / "data"),
           "VIDEOAGENTS_RUNTIME_DIR": str(tmp_path / "data" / ".videoagents"),
           "VIDEOAGENTS_CLIENT_VERSION": client_version}
    out = subprocess.run([sys.executable, "-c", (
        "import json; from services.runtime import core; from modules import genmedia;"
        "core.save_genconfig({'music': {'provider': 'minimax'}});"
        "print(json.dumps([core.DEFAULT_GENCONFIG['music']['provider'], genmedia.MUSIC_DEFAULT_PROVIDER,"
        " core.DEFAULT_GENCONFIG['sfx']['provider'], core.load_genconfig()['music']['provider']]))")],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout.strip().splitlines()[-1]) == [want, want, "code", "minimax"]


# ---------------------------------------------------------------- 端到端渲染(需 Chromium + ffmpeg)

PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;background:#000}</style></head>
<body><canvas id="c"></canvas><script type="module" src="main.js"></script></body></html>"""
MAIN = """import { clamp } from '/core/lib.js';
import { drawA } from './sections/a.js';
import { drawB } from './sections/b.js';
const c = document.getElementById('c'); c.width = innerWidth; c.height = innerHeight; const g = c.getContext('2d');
window.DUR = 1.0;
window.EV = [{t: 0.5, type: 'hit'}];
window.TEXTS = t => t < 0.5 ? [{id: 'title', text: 'Hi', x0: 2, y0: 2, x1: 30, y1: 14}] : [];
window.render = t => { g.fillStyle = '#000'; g.fillRect(0, 0, c.width, c.height); (t < 0.5 ? drawA : drawB)(g, clamp(t, 0, 1), c.width, c.height); };
window.READY = true;
"""
SEC_A = "export function drawA(g, t, w, h) { g.fillStyle = `rgb(${Math.round(t * 400)},40,40)`; g.fillRect(0, 0, w * (0.2 + t), h); }\n"
SEC_B = "export function drawB(g, t, w, h) { g.fillStyle = '#28a'; g.fillRect(0, 0, w * t, h); }\n"
MIX = """import os, numpy as np, soundfile as sf
sr = 48000; t = np.arange(int(sr * 1.2)) / sr
x = (0.25 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
os.makedirs(os.environ['CODEVIDEO_STEMS'], exist_ok=True)
sf.write(os.path.join(os.environ['CODEVIDEO_STEMS'], 'music.wav'), np.stack([x, x], 1), sr)
sf.write(os.environ['CODE_AUDIO_OUT'], np.stack([x, x], 1), sr)
"""


def _chromium_ok() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            b.close()
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not shutil.which("ffmpeg") or not _chromium_ok(), reason="需要 ffmpeg 与 Playwright Chromium")
def test_render_sections_fingerprints_board_mix_master_and_checks(tmp_path, gencfg):
    from modules.codevideo import audio as A
    from modules.codevideo import checks as C
    from modules.codevideo import render as R
    base = tmp_path / "proj"
    film = base / CV.NS
    (film / "sections").mkdir(parents=True)
    (film / "index.html").write_text(PAGE)
    (film / "main.js").write_text(MAIN)
    (film / "sections" / "a.js").write_text(SEC_A)
    (film / "sections" / "b.js").write_text(SEC_B)
    (film / "timeline.json").write_text(json.dumps(_tl()))
    (film / "mix.py").write_text(MIX)
    (film / "CREDITS").write_text("Code only.\n")
    (base / "settings.json").write_text(json.dumps({"project": {"type": "code-video", "code_style": "blueprint"}}))
    TL.sync(base)
    log = []
    r1 = R.render(base, workers=2, log=log.append)
    assert r1["rendered"] == ["a", "b"]
    assert {r["state"] for r in R.status(base)} == {"fresh"}
    meta = json.loads((base / "assets/clips/ep01/grp0020.meta.json").read_text(encoding="utf-8"))
    assert meta["frames"] == 12 and meta["boundary_map"][1] == {"shot_id": "sh0030", "start_s": 0.291667, "end_s": 0.5}  # 0.8 s 量化到第 19 帧
    assert R.render(base, log=log.append)["rendered"] == []           # 指纹没变:跳过
    (film / "sections" / "b.js").write_text(SEC_B.replace("#28a", "#2a8"))
    assert [r["state"] for r in R.status(base)] == ["fresh", "stale"]  # 只改 b 段专属文件:只有 b 过期
    assert R.render(base, log=log.append)["rendered"] == ["b"]
    (film / "main.js").write_text(MAIN + "\n// engine changed\n")
    assert [r["state"] for r in R.status(base)] == ["stale", "stale"]  # 改引擎:全部过期
    R.render(base, log=log.append)
    b = R.board(base, log=log.append)
    assert b["shots"] and (base / b["sheet"]).is_file()
    assert R.events(base)["events"] == 1
    rc = R.readcheck(base)
    assert rc["checked"] and rc["bad"]                                  # "Hi" 只在画 0.5 s,不够读
    A.mix(base, log=log.append)
    assert (base / "assets/audio/final/ep01.wav").is_file()
    res = A.master(base, log=log.append)
    assert res["frames"] == 24 and (base / "edit/ep01/final.mp4").is_file() and (base / "edit/ep01/poster.jpg").is_file()
    assert (film / "build.sh").is_file()
    out = C.run(base, ["contract", "timeline", "clips", "delivery"])
    bad = [i for items in out["stages"].values() for i in items if not i["ok"]]
    assert not bad, bad
    fin = {i["id"]: i for i in C.final(base)}
    assert fin["final_frames"]["ok"] and fin["final_has_audio"]["ok"]
    # 不确定的页面(Math.random)被机检抓到
    (film / "main.js").write_text(MAIN.replace("g.fillRect(0, 0, c.width, c.height);",
                                               "g.fillRect(0, 0, c.width, c.height); g.fillStyle='#fff'; g.fillRect(Math.random()*80, 0, 4, 4);"))
    det = {i["id"]: i for i in C.contract(base)}
    assert not det["deterministic"]["ok"]


def test_cli_doctor_runs_without_gl():
    r = subprocess.run([sys.executable, "code/codevideo.py", "doctor", "--no-gl", "--json"], cwd=ROOT, capture_output=True, text=True)
    d = json.loads(r.stdout)
    ids = {i["id"] for i in d["items"]}
    assert {"ffmpeg", "host_library", "style_library", "playwright"} <= ids
    r = subprocess.run([sys.executable, "code/codevideo.py", "packs", "--json"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0 and any(x["name"] == "hdri" for x in json.loads(r.stdout))


def test_demo_requires_treatment(tmp_path):
    from modules.codevideo import project as P
    base = tmp_path / "p"
    (base / CV.NS).mkdir(parents=True)
    (base / "settings.json").write_text(json.dumps({"project": {"type": "code-video", "code_style": "whiteboard"}}))
    lock = P.lock_style(base)
    assert lock["slug"] == "whiteboard" and (base / CV.NS / "style" / "STYLE.md").is_file()
    assert not (base / CV.NS / "style" / "DEMO.md").exists()           # 先方案后样片:锁定时不放 DEMO.md
    with pytest.raises(P.DemoBeforeTreatment):
        P.demo(base)


# ---------------------------------------------------------------- 三期:外来配乐 + 节拍对齐

def _drums(path, bpm=120.0, off=0.5, dur=30.0, sr=48000):
    """合成鼓点:每拍一声踩镲,每小节第一拍加底鼓(强拍)。"""
    import numpy as np
    import soundfile as sf
    rng = np.random.default_rng(3)
    y = np.zeros(int(sr * dur))
    period = 60.0 / bpm
    k = 0
    while off + k * period < dur - 0.2:
        s = int((off + k * period) * sr)
        n = min(int(0.15 * sr), len(y) - s)
        tt = np.arange(n) / sr
        if k % 4 == 0:
            y[s:s + n] += 0.9 * np.sin(2 * np.pi * 55 * tt) * np.exp(-tt * 25)
        y[s:s + n] += 0.3 * rng.standard_normal(n) * np.exp(-tt * 60)
        k += 1
    y += 0.003 * rng.standard_normal(len(y))
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), np.stack([y, y], 1).astype(np.float32), sr)
    return [off + i * period for i in range(k)]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="需要 ffmpeg")
def test_beat_analysis_tempo_beats_and_downbeats(tmp_path):
    from modules.codevideo import beats as BT
    truth = _drums(tmp_path / "d.wav", bpm=100, off=0.37, dur=20)
    a = BT.analyze(tmp_path / "d.wav")
    assert abs(a["bpm"] - 100) < 0.5
    err = [min(abs(b - t) for t in truth) for b in a["beats"]]
    assert len(a["beats"]) >= len(truth) - 2 and max(err) < 0.03          # 24 fps 一帧 42 ms
    assert min(abs(a["downbeats"][0] - t) for t in truth[::4]) < 0.03      # 强拍相位对


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="需要 ffmpeg")
def test_music_fit_snap_and_beats_check(tmp_path):
    from modules import music_library as ML
    from modules.codevideo import beats as BT
    from modules.codevideo import checks as C
    base = tmp_path / "proj"
    (base / CV.NS).mkdir(parents=True)
    (base / "settings.json").write_text(json.dumps({"project": {"type": "code-video", "code_style": "blueprint",
                                                                "music_source": "library"}}))
    tl = {"schema": "codevideo_timeline/1", "fps": 24, "size": [96, 54], "dur": 10.0, "meter": 4,
          "sections": [{"id": "a", "t0": 0, "t1": 3.1}, {"id": "b", "t0": 3.1, "t1": 6.3}, {"id": "c", "t0": 6.3, "t1": 10.0}],
          "shots": [{"id": "a1", "section": "a", "t0": 0, "t1": 3.1}, {"id": "b1", "section": "b", "t0": 3.1, "t1": 4.4},
                    {"id": "b2", "section": "b", "t0": 4.4, "t1": 6.3}, {"id": "c1", "section": "c", "t0": 6.3, "t1": 10.0}],
          "hits": [{"id": "stamp", "t": 7.95}], "lines": [{"id": "l1", "t": 4.0}]}
    (base / CV.NS / "timeline.json").write_text(json.dumps(tl))
    TL.sync(base)
    _drums(base / "refs" / "music" / "song.wav", bpm=120, off=0.5, dur=30)
    res = BT.fit_project(base, file="refs/music/song.wav", mood="明快")
    assert res["bpm"] and abs(res["bpm"] - 120) < 0.5 and not res["looped"]
    assert abs(res["start_s"] - 0.5) < 0.03                                   # 从开头静音后的第一个强拍起
    grid = BT.load_beats(base)
    assert grid["license"]["source"] == "user_provided" and grid["audio_sha256"]
    sheet = json.loads((base / "assets/audio/bgm/ep01/cue_sheet.json").read_text(encoding="utf-8"))
    assert sheet["cues"][0]["out_s"] == 10.0 and sheet["cues"][0]["license"]["file"] == "refs/music/song.wav"
    assert ML.load_index(base)["tracks"]                                       # 用户音乐经 cue sheet 对账入库
    before = {i["id"]: i["ok"] for i in BT.check(base)}
    assert before["beats_match_score"] and not before["sections_on_beat"]      # 段边界 3.1 / 6.3 还没对拍
    rep = BT.snap_project(base)
    assert rep["sections"] and rep["backup"] and (base / rep["backup"]).is_file()
    after = {i["id"]: i for i in BT.check(base)}
    assert after["sections_on_beat"]["ok"] and after["hits_on_beat"]["ok"], after
    new = json.loads((base / CV.NS / "timeline.json").read_text(encoding="utf-8"))
    assert new["dur"] == 10.0 and new["beat_sync"]["bpm"] == grid["bpm"]
    downs = grid["downbeats"]
    assert min(abs(new["sections"][1]["t0"] - d) for d in downs) < 1 / 24    # 段边界落在强拍
    assert new["lines"][0]["t"] != 4.0                                         # 段内台词时刻随段边界平移
    sl = json.loads((base / "directing/ep01/shot_list.json").read_text(encoding="utf-8"))
    assert abs(sl["total_duration_s"] - 10.0) < 1e-6                           # 片长不变,已自动 sync
    stages = C.run(base, ["beats"])
    assert stages["ok"]
    # 曲库曲目:按曲目号适配,cue sheet 记 library 来源
    idx = ML.load_index(base)
    tid = idx["tracks"][0]["track_id"]
    res2 = BT.fit_project(base, track=tid)
    sheet2 = json.loads((base / "assets/audio/bgm/ep01/cue_sheet.json").read_text(encoding="utf-8"))
    assert sheet2["cues"][0]["license"]["source"] == "library" and sheet2["cues"][0]["license"]["track_id"] == tid
    assert res2["bpm"]
    with pytest.raises(CV.CodeVideoError):
        BT.fit_project(base, track="MUS-9999")
    with pytest.raises(CV.CodeVideoError):
        BT.fit_project(base)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="需要 ffmpeg")
def test_music_fit_loops_short_track(tmp_path):
    from modules.codevideo import beats as BT
    _drums(tmp_path / "short.wav", bpm=120, off=0.2, dur=8)
    g = BT.fit(tmp_path / "short.wav", tmp_path / "out.wav", 20.0)
    import soundfile as sf
    assert g["source"]["looped"] and abs(sf.info(str(tmp_path / "out.wav")).duration - 20.0) < 0.01
    assert abs(g["bpm"] - 120) < 1.0


def test_music_source_library_and_beatsync_condition(env):
    _create({"name": "cvm", "project_type": "code-video", "code_video": {"code_style": "dataviz", "music_source": "library"}})
    cfg = core.load_project_settings("cvm")
    assert cfg["project"]["music_source"] == "library"
    sec = project_types.orchestrator_section(cfg["project"], "data/projects/cvm", [])
    assert "cv2-beatsync 照常派" in sec
    assert "从项目音乐库" in env[0]["message"]
    code_sec = project_types.orchestrator_section({"type": "code-video", "code_style": "dataviz"}, "x", [])
    assert "cv2-beatsync 置 skipped" in code_sec
    with pytest.raises(core.ServiceError):
        _create({"name": "cvx", "project_type": "code-video", "code_video": {"code_style": "dataviz", "music_source": "radio"}})


# ---------------------------------------------------------------- 三期:OPUSCAR 98 轻量风格

def test_light_styles_catalog_and_gallery():
    ids = CV.light_ids()
    assert len(ids) == 98 and ids[0] == "o98-01" and ids[-1] == "o98-98"
    data = CV.light_styles()
    assert len(data["chapters"]) == 10
    for x in data["styles"]:
        assert x["prompt_zh"] and x["prompt_en"] and len(x["related"]) == 3
        assert all(r in CV.style_slugs() for r in x["related"])
        assert "shots" not in x and "direction_en" not in x                     # 不带 OPUSCAR 98 的分镜 / 主角
    gal = json.loads((CV.GALLERY_JSON.parent / "opuscar98.json").read_text(encoding="utf-8"))
    assert len(gal["styles"]) == 98
    assert all((CV.GALLERY_JSON.parent / s["frame"]).is_file() for s in gal["styles"])
    row = CV.style_row("o98-12")
    assert row["light"] and row["en"] == "Technicolor Impasto Oil" and row["film"] == "Gone with the Wind"


def test_light_style_project_lock_and_demo(env, tmp_path):
    from modules.codevideo import project as P
    _create({"name": "cvl", "project_type": "code-video", "code_video": {"code_style": "o98-12"}})
    cfg = core.load_project_settings("cvl")
    assert cfg["project"]["code_style"] == "o98-12"
    base = core.PROJECTS_DIR / "cvl"
    assert "轻量风格" in (base / "brief.md").read_text(encoding="utf-8") and "风格补全" in project_types.orchestrator_section(cfg["project"], "x", [])
    assert "轻量风格" in env[0]["message"]
    lock = P.lock_style(base)
    md = (base / CV.NS / "style" / "STYLE.md").read_text(encoding="utf-8")
    entry = next(x for x in CV.light_styles()["styles"] if x["id"] == "o98-12")
    assert entry["prompt_zh"] in md and entry["prompt_en"] in md and "风格补全" in md
    assert lock["light"] and lock["related"] == entry["related"]
    assert json.loads((base / CV.NS / "style" / "style.json").read_text(encoding="utf-8"))["light"] is True
    assert not (base / CV.NS / "style" / "DEMO.md").exists()
    assert P.lock_style(base)["changed"] is False                              # 重锁幂等
    with pytest.raises(P.DemoBeforeTreatment):
        P.demo(base)
    (base / CV.NS / "TREATMENT.md").write_text("# t")
    with pytest.raises(CV.CodeVideoError) as e:
        P.demo(base)
    assert entry["related"][0] in str(e.value)                                  # 提示可用的相近完整风格
    with pytest.raises(CV.CodeVideoError):
        P.demo(base, slug="o98-01")                                             # --slug 只收完整风格


def test_beats_check_honours_off_and_kept(tmp_path):
    """snap 时设成 off 的类别、附近没拍而原地保留的段 / 击点,机检豁免;其余照判。"""
    import soundfile as sf
    import numpy as np
    from modules.codevideo import beats as BT
    base = tmp_path / "p"
    (base / CV.NS / "music").mkdir(parents=True)
    sf.write(str(base / CV.NS / "music" / "score.wav"), np.zeros((48000, 2), np.float32), 48000)
    grid = {"bpm": 120, "meter": 4, "beats": [i * 0.5 for i in range(20)], "downbeats": [i * 2.0 for i in range(5)],
            "audio_sha256": BT.sha256(base / CV.NS / "music" / "score.wav")}
    BT.write_beats(base, grid)
    tl = {"fps": 24, "size": [96, 54], "dur": 9.0,
          "sections": [{"id": "a", "t0": 0, "t1": 4.23}, {"id": "b", "t0": 4.23, "t1": 9.0}], "shots": [],
          "hits": [{"id": "h1", "t": 6.21}]}
    (base / CV.NS / "timeline.json").write_text(json.dumps(tl))
    res = {i["id"]: i["ok"] for i in BT.check(base)}
    assert res == {"beats_match_score": True, "hits_on_beat": False, "sections_on_beat": False}
    tl["beat_sync"] = {"sections": "off", "hits": "beat", "kept": {"sections": [], "hits": ["h1"]}}
    (base / CV.NS / "timeline.json").write_text(json.dumps(tl))
    res = {i["id"]: i["ok"] for i in BT.check(base)}
    assert res == {"beats_match_score": True, "hits_on_beat": True, "sections_on_beat": True}


def test_code_video_agents_default_to_high_tier():
    """演示视频新增的 4 个创作成员智能分配默认高档;终审 11-qa/code-video-qa 随审核类低档(2026-10-21 用户要求)。"""
    members = ["18-code-video/film-director", "18-code-video/scene-coder", "18-code-video/code-composer",
               "18-code-video/sound-designer"]
    for aid in members:
        assert (ROOT / "agents" / aid / "SOUL.md").is_file(), aid
        for mode in ("smart_claude", "smart_codex"):
            assert core.default_agent_model(aid, mode) == core.AM_MODE_MODELS[mode]["high"] | {}, (aid, mode)
    for aid in ("11-qa/code-video-qa", "11-qa/visual-qa"):
        assert core.default_agent_model(aid, "smart_claude")["model"] == core.AM_MODE_MODELS["smart_claude"]["low"]["model"], aid
