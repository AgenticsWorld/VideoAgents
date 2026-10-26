"""Fal Kling 4.0(kling/v4)内置家族:端点选择、请求体字段、参考素材上限、参考视频预检 / 放大、
提示词点名改写,以及 core / 白模 / 续接 / 直播页对该模型的上限口径;不联网、不提交。"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import modules.genmedia as g  # noqa: E402

U = lambda p: f"URL:{p}"  # noqa: E731
K4 = {"model": "kling/v4", "api_key": ""}
LABEL = "Kling 4.0(Fal 托管;文生/首尾帧/多模态参考,3-30 秒,参考 10 图/5 视频(视频合计 ≤30s),不收参考音频,720p/1080p)"
needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="需要 ffmpeg/ffprobe")


def body(first="", last="", refs=(), audio=(), videos=(), duration=8, res="1080p", aspect="16:9", seed=None,
         gen_audio=None, prompt="p", cfg=K4):
    return g._fal_video_body(cfg, prompt, first, last, duration, res, aspect, seed,
                             list(refs), list(audio), gen_audio, list(videos), U, U)


def _clip(path: Path, size: str, seconds: float, fps: int = 24) -> Path:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=s={size}:d={seconds}:r={fps}",
                    "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def _size(path) -> tuple[int, int]:
    meta = g._probe_video_meta(str(path))
    return meta["width"], meta["height"]


@pytest.mark.parametrize("model, family", [
    ("kling/v4", "kling4"), ("kling/v4/reference-to-video", "kling4"), ("fal-ai/kling-video/v4", "kling4"),
    ("fal-ai/kling-video/v3/pro", "kling"), ("fal-ai/kling-video/v3/4k/text-to-video", "kling"),
    ("fal-ai/kling-video/o3/pro/image-to-video", "kling"), ("kling-v4/omni-video", "kling"),
])
def test_family(model, family):
    assert g._fal_family(model) == family


def test_text_to_video_fields_and_mapping(capsys):
    ep, b = body(seed=7, gen_audio=True)
    assert ep == "kling/v4/text-to-video"
    assert b == {"prompt": "p", "duration": 8, "resolution": "1080p", "aspect_ratio": "16:9"}   # 整数时长,无 seed / 音频开关
    err = capsys.readouterr().err
    assert "seed 已忽略" in err and "generate-audio 已忽略" in err
    assert body(res="480p")[1]["resolution"] == "720p"
    assert body(res="4k")[1]["resolution"] == "1080p"
    assert body(duration=30)[1]["duration"] == 30
    assert body(duration=None)[1]["duration"] == 5
    with pytest.raises(RuntimeError, match=r"\[3,30\]"):
        body(duration=31)
    with pytest.raises(RuntimeError, match="文生视频画幅仅支持"):
        body(aspect="4:3")


def test_first_last_frame():
    ep, b = body(first="a.png", last="b.png")
    assert ep == "kling/v4/image-to-video"
    assert b == {"prompt": "p", "duration": 8, "resolution": "1080p",
                 "start_image_url": "URL:a.png", "end_image_url": "URL:b.png"}      # 画幅随首帧,不发 aspect_ratio


def test_reference_mode_images_tags_and_limits():
    ep, b = body(refs=["a.png", "b.png"], aspect="4:3",
                 prompt="[Image 1] 走向 @Image 2,镜头参考 [Video 1];@image10 不动")
    assert ep == "kling/v4/reference-to-video"
    assert b["image_urls"] == ["URL:a.png", "URL:b.png"] and "video_urls" not in b
    assert b["aspect_ratio"] == "auto"                                                 # 不支持的画幅按素材自适应
    assert b["prompt"] == "@Image1 走向 @Image2,镜头参考 @Video1;@Image10 不动"
    said = body(refs=["a.png"], prompt='塔尔用低沉的中文说：{雪化了是清的。} 阿雅答“{嗯。}”,又说 "{走吧}"。')[1]["prompt"]
    assert said == '塔尔用低沉的中文说：“雪化了是清的。” 阿雅答“嗯。”,又说 “走吧”。'      # 团队 {} 台词 → 官方引号
    assert body(refs=["a.png"], aspect="21:9")[1]["aspect_ratio"] == "21:9"
    assert body(refs=["a.png"] * 10)[1]["image_urls"] == ["URL:a.png"] * 10
    with pytest.raises(RuntimeError, match="参考图最多 10 张"):
        body(refs=["a.png"] * 11)
    with pytest.raises(RuntimeError, match="不收参考音频"):
        body(refs=["a.png"], audio=["v.wav"])
    with pytest.raises(RuntimeError, match="参考视频最多 5 个"):
        body(videos=["v.mp4"] * 6)
    with pytest.raises(RuntimeError, match="互斥"):
        body(first="a.png", refs=["b.png"])


def test_kling3_unchanged_and_points_to_kling4():
    ep, b = body(first="a.png", cfg={"model": "fal-ai/kling-video/v3/pro", "api_key": ""})
    assert ep == "fal-ai/kling-video/v3/pro/image-to-video"
    assert b["duration"] == "8" and b["start_image_url"] == "URL:a.png" and "resolution" not in b
    with pytest.raises(RuntimeError, match="Kling 4.0"):
        body(refs=["a.png"], cfg={"model": "fal-ai/kling-video/v3/pro", "api_key": ""})


@needs_ffmpeg
def test_reference_videos_upscaled_cached_and_checked(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(g, "KLING4_REF_VIDEO_CACHE_DIR", tmp_path / "cache")
    camera = _clip(tmp_path / "camera.mp4", "960x540", 4)                              # 白模 camera.mp4 规格
    ok = _clip(tmp_path / "ok.mp4", "1280x720", 3)
    ep, b = body(refs=["a.png"], videos=[camera, ok])
    assert ep == "kling/v4/reference-to-video"
    up, kept = (u[len("URL:"):] for u in b["video_urls"])
    assert kept == str(ok)                                                             # 合规的原样上传
    assert Path(up).parent == tmp_path / "cache" and _size(up) == (1280, 720)
    assert "960x540 24fps)已转为 1280x720" in capsys.readouterr().err
    mtime = Path(up).stat().st_mtime_ns
    assert body(videos=[camera])[1]["video_urls"] == [f"URL:{up}"] and Path(up).stat().st_mtime_ns == mtime   # 重试不重转

    portrait = _clip(tmp_path / "portrait.mp4", "480x854", 3)
    assert _size(g._kling4_prepare_ref_videos([portrait])[0]) == (720, 1280)

    short = _clip(tmp_path / "tail.mp4", "1280x720", 2.5)                               # 续接尾段规格
    with pytest.raises(RuntimeError, match="每段须 3-30 秒,tail.mp4 实测 2.50s"):
        body(videos=[short])
    longs = [_clip(tmp_path / f"l{i}.mp4", "64x64", 16, fps=4) for i in range(2)]
    with pytest.raises(RuntimeError, match="合计须 ≤30s"):
        body(videos=longs)
    wide = _clip(tmp_path / "wide.mp4", "1920x700", 3)
    with pytest.raises(RuntimeError, match="宽高比须在 0.4-2.5"):
        body(videos=[wide])
    assert not list((tmp_path / "cache").glob("l*")) and not list((tmp_path / "cache").glob("wide*"))   # 预检失败不转码


def test_core_catalog_caps_and_page_lists():
    from services.runtime import core
    assert ("kling/v4", LABEL) in core.VIDEO_MODEL_CATALOG["fal"]
    assert core.video_model_label("kling/v4", "fal") == "Kling 4.0"
    caps = {"max_ref_images": 10, "max_ref_videos": 5, "max_ref_audios": 0, "max_group_s": 30, "max_ref_video_s": 30}
    assert core.video_model_caps("kling/v4") == caps
    assert core.video_model_caps("kling/v4/reference-to-video") == caps
    assert core.video_model_caps("kling-v4/omni-video") is None                       # RunningHub 端点按其目录判
    assert core.video_model_caps("fal-ai/kling-video/v3/pro") is None
    assert core.video_family_for("kling/v4") == "kling4"                               # 「模型限制」Kling 4.0 预设
    assert core.video_family_for("kling-v4/omni-video") == ""
    assert core.auto_prompt_skill_for_model("kling/v4") == core.PROMPT_SKILL_KLING4
    assert core.auto_prompt_skill_for_model("kling-v4/omni-video") == ""
    assert "kling4" in core.SHOT_GROUP_PRESETS

    html = (ROOT / "apps/web/static/models.html").read_text(encoding="utf-8")
    page = re.findall(r"\['([^']+)','([^']+)'\]", re.search(r"const FAL_VIDEO_MODELS=\[(.*?)\];", html, re.S).group(1))
    assert page == core.VIDEO_MODEL_CATALOG["fal"]                                     # 页面下拉与 core 目录同步
    for path in sorted((ROOT / "apps/web/static/i18n").glob("??.js")):
        assert f"\n{json.dumps(LABEL, ensure_ascii=False)}: " in path.read_text(encoding="utf-8"), path.name


def test_whitebox_continuity_and_live_caps():
    from modules import continuity_refs, live_stream, whitebox_refs
    assert whitebox_refs._model_caps("kling/v4") == {"max_ref_videos": 5, "max_total_s": 30}
    assert whitebox_refs._model_caps("fal-ai/kling-video/v3/pro") == {"max_ref_videos": 0, "max_total_s": 0}
    assert whitebox_refs._model_caps("kling-v4/omni-video") == {"max_ref_videos": 0, "max_total_s": 0}
    assert continuity_refs.video_caps("kling/v4", "fal") is None                       # 尾段 ≤3s 不够 Kling 4.0 下限
    assert live_stream.allowed_link_modes("fal", "kling/v4") == live_stream.LINK_MODES
    assert live_stream.allowed_link_modes("fal", "fal-ai/kling-video/v3/pro") == ("first_frame", "none", "director")
    live = (ROOT / "apps/web/static/live.html").read_text(encoding="utf-8")
    assert "kling4:[3,30]" in live


def test_model_limit_preset_matches_caps():
    """「模型限制」Kling 4.0 预设(设置弹窗与新建向导)与 core.video_model_caps 同口径,两处按钮都有。"""
    from services.runtime import core
    html = (ROOT / "apps/web/static/index.html").read_text(encoding="utf-8")
    preset = re.search(r"\n\s*kling4:\{([^}]*)\}", html).group(1)
    vals = {k: int(v) for k, v in re.findall(r"(\w+):(\d+)", preset)}
    caps = core.video_model_caps("kling/v4")
    assert vals == {k: caps[k] for k in ("max_group_s", "max_ref_images", "max_ref_videos", "max_ref_audios")}
    assert html.count('data-preset="kling4">Kling 4.0</button>') == 2
    assert "kling4:'Kling 4.0'" in html


def test_kling4_prompt_skill_wired():
    """kling4-pe 随仓库安装、注册为条件技能,prompt 工位系统提示词注入加载指令与不收参考音频的说明。"""
    from services.runtime import core
    skill = ROOT / core.KLING4_PE_SKILL
    text = skill.read_text(encoding="utf-8")
    assert text.startswith("---\nname: kling4-pe\n")
    assert core._parse_skill_frontmatter(text)["name"] == "kling4-pe"
    assert core.SKILL_ACTIVATIONS["08-video-gen/prompt/kling4-pe"]["kind"] == "conditional"
    soul = (ROOT / "agents/08-video-gen/prompt/SOUL.md").read_text(encoding="utf-8")
    assert "skills/kling4-pe/SKILL.md" in soul


def test_prompt_agent_contract_injects_kling4_skill(tmp_path, monkeypatch):
    """生效视频模型为 Fal kling/v4 时,prompt 工位系统提示词按 auto 解析出 kling4-pe 并注入加载指令。"""
    from services.runtime import core
    cfg = tmp_path / "genconfig.json"
    cfg.write_text(json.dumps({"video": {"provider": "fal", "fal": {"model": "kling/v4"}}}), encoding="utf-8")
    monkeypatch.setattr(core, "GENCONFIG_PATH", cfg)
    monkeypatch.delenv("VIDEOAGENTS_USER_JWT", raising=False)
    monkeypatch.setattr(core, "PROJECTS_DIR", tmp_path / "projects")
    (tmp_path / "projects" / "demo").mkdir(parents=True)
    (tmp_path / "projects" / "demo" / "settings.json").write_text("{}", encoding="utf-8")
    assert core.resolve_prompt_skill("demo")["skill_id"] == core.PROMPT_SKILL_KLING4
    p = core.build_role_prompt(core.PROMPT_AGENT_ID, "demo")
    assert p.count("## Kling 4.0 提示词优化 Skill") == 1 and core.KLING4_PE_SKILL in p
    assert "`{台词}`" in p and "audio_refs 一律留空" in p
    assert "## 提示词技能 `kling4-pe`" not in p                                        # 不再落到通用技能段
