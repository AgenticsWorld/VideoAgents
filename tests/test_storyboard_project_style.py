"""故事板草图「项目画风」(2026-11-01):画风取创作定调 brief.md 的画面风格;整集出草图「一次 4 张 / 一次 1 张」。"""
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from modules import storyboard_board as sbb
from services.runtime import core

ROOT = Path(__file__).resolve().parent.parent

BRIEF = """# 主创构想

## 主要构想
悬疑短剧。

## 设计风格
**整体**:水彩手绘质感,暖黄与灰绿为主。
> 光影柔和、逆光勾边。
- 人物线条干净

## 参考
颗粒感 {轻微}。

## 叙事节奏
起承转合
"""


def _shot():
    return {"cast": ["CHAR-1"], "size_hint": "近景", "content": "甲回头看向门口"}


def test_brief_style_section_matches_console_and_is_flattened(tmp_path):
    """与创作定调弹窗同口径:「## 设计风格」小节 + 其后的未知小节,到「## 叙事节奏」为止;Markdown 标记去掉并成一段。"""
    section = sbb.brief_style_section(BRIEF)
    assert section == core.parse_brief(BRIEF)[1]
    assert "## 参考" in section and "起承转合" not in section and "悬疑短剧" not in section
    (tmp_path / "brief.md").write_text(BRIEF, encoding="utf-8")
    info = sbb.project_style_info(tmp_path)
    assert info["source"] == "brief"
    assert info["text"] == "整体:水彩手绘质感,暖黄与灰绿为主。 光影柔和、逆光勾边。 人物线条干净 参考 颗粒感 {轻微}。"
    # 没有画面风格:不可用
    (tmp_path / "brief.md").write_text("# 主创构想\n\n## 主要构想\n只有构想\n", encoding="utf-8")
    assert sbb.project_style_info(tmp_path) == {"text": "", "source": "", "brief_chars": 0}
    (tmp_path / "brief.md").unlink()
    assert sbb.project_style_info(tmp_path)["text"] == ""


def test_long_brief_style_uses_bible_fragment_then_truncates(tmp_path):
    long = "胶片质感," * 200          # 1000 字 > PROJECT_STYLE_MAX
    (tmp_path / "brief.md").write_text(f"## 设计风格\n{long}\n", encoding="utf-8")
    cut = sbb.project_style_info(tmp_path)
    assert cut["source"] == "brief_cut" and len(cut["text"]) == sbb.PROJECT_STYLE_MAX and cut["brief_chars"] == 1000
    (tmp_path / "bible").mkdir()
    (tmp_path / "bible" / "style.json").write_text(json.dumps({"style_fragment_en": "35mm film grain, muted earth tones"}))
    frag = sbb.project_style_info(tmp_path)
    assert frag == {"text": "35mm film grain, muted earth tones", "source": "bible", "brief_chars": 1000}
    # 原文不长时一律用原文,不看 bible
    (tmp_path / "brief.md").write_text("## 设计风格\n短风格\n", encoding="utf-8")
    assert sbb.project_style_info(tmp_path)["source"] == "brief"


def test_resolve_style_text_falls_back_to_film(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"output": {"sketch_style": "project"}}))
    assert sbb.resolve_sketch_style(tmp_path) == "project"
    assert sbb.resolve_style_text(tmp_path) == ("film", "", "")           # 创作定调没填画面风格
    (tmp_path / "brief.md").write_text("## 设计风格\n赛璐璐动画,高饱和\n", encoding="utf-8")
    assert sbb.resolve_style_text(tmp_path) == ("project", "赛璐璐动画,高饱和", "brief")
    assert sbb.resolve_style_text(tmp_path, "konte") == ("konte", "", "")   # --style 覆盖
    assert sbb.SKETCH_STYLE_LABELS["project"] == "项目画风"


def test_project_style_prompts_follow_brief_and_keep_framing_rules():
    style = "水彩手绘质感,暖黄与灰绿为主,颗粒感 {轻微}"
    prompt, neg = sbb.build_prompt({}, _shot(), {"CHAR-1": "甲"}, style="project", project_style=style)
    assert prompt.startswith("A storyboard frame for this production, rendered in this project's own visual style — " + style)
    assert "FIGURES FIRST" in prompt and "camera height and lens" in prompt and "Characters in frame: 甲" in prompt
    assert "black-and-white" not in prompt and "pencil" not in prompt.lower()
    assert "colors and signature props" in prompt                     # 人物 sheet 就是项目画风,配色一起保留
    for word in ("color", "photo", "3d", "anime", "painting"):
        assert word not in neg.split(", ")
    assert "text" in neg and "watermark" in neg
    # 纯文生图:以 TEXT_ONLY_TAIL 结尾、风格句里没有否定句
    t_only, _ = sbb.build_prompt({}, _shot(), {}, with_refs=False, style="project", project_style=style)
    pack_text = sbb.project_style_pack(style)["text_only"]
    head = pack_text[:-len(sbb.SKETCH_TEXT_ONLY_TAIL)]
    assert t_only.startswith(pack_text) and pack_text.endswith(sbb.SKETCH_TEXT_ONLY_TAIL) and style in head
    assert " no " not in head.replace(style, "").lower() and " not " not in head.lower()
    plain, _ = sbb.build_prompt({}, _shot(), {}, plain_style=True, ref_names=["甲"], style="project", project_style=style)
    assert "Picture 1 is 甲" in plain and "drawn in the project's visual style described here" in plain
    hand, _ = sbb.build_prompt({}, _shot(), {}, layout=True, style="project", project_style=style)
    assert "hand-drawn layout" in hand and "redraw it cleanly in the project's visual style" in hand
    wb, _ = sbb.build_prompt({}, _shot(), {}, layout=True, whitebox_legend=[("甲", "red")], style="project",
                             project_style=style)
    assert "drawn in the project's visual style, in the pose" in wb
    # 宫格:风格原文里的花括号不破坏 .format;白缝隔开
    grid, gneg = sbb.build_grid_prompt([({"scene_no": "S01"}, _shot()), ({"scene_no": "S01"}, {})], {"CHAR-1": "甲"},
                                       2, 2, style="project", project_style=style)
    assert grid.startswith("A storyboard page for this production: a strict 2x2 grid") and style in grid
    assert "The last 2 cell(s) of the grid stay blank white." in grid and "uneven panels" in gneg
    assert "color" not in gneg.split(", ")
    # 文字为空 = film
    assert sbb.build_prompt({}, _shot(), {}, style="project")[0] == sbb.build_prompt({}, _shot(), {})[0]


def test_other_styles_unchanged_by_manner_parameter():
    """参考图点名句 / 白模句加了 manner 参数后,原有四种画风的文字不变。"""
    plain, _ = sbb.build_prompt({}, _shot(), {}, plain_style=True, ref_names=["甲"])
    assert "drawn in the simplified storyboard manner described here" in plain
    wb, _ = sbb.build_prompt({}, _shot(), {}, layout=True, whitebox_legend=[])
    assert "that character drawn in the storyboard style, in the pose and action stated below" in wb
    hand, _ = sbb.build_prompt({}, _shot(), {}, layout=True)
    assert sbb.SKETCH_LAYOUT_SENTENCE in hand


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "PROJECTS_DIR", tmp_path)
    base = tmp_path / "demo"
    (base / "directing" / "ep01").mkdir(parents=True)
    (base / "directing" / "ep01" / "storyboard.json").write_text(json.dumps({"scenes": [
        {"scene_no": "S01", "shots_draft": [{"order": i, "content": f"第{i}镜"} for i in range(1, 6)]},
    ]}, ensure_ascii=False), encoding="utf-8")
    (base / "settings.json").write_text(json.dumps({"output": {"sketch_style": "film", "aspect_preset": "youtube"}}))
    return base


def test_style_api_reports_and_guards_project_style(project):
    got = asyncio.run(core.api_board_sketch_style_get("demo"))
    assert "project" in got["styles"] and got["project_style"]["available"] is False
    with pytest.raises(core.ServiceError) as e:
        asyncio.run(core.api_board_sketch_style_set("demo", {"style": "project"}))
    assert e.value.status_code == 400
    assert json.loads((project / "settings.json").read_text())["output"]["sketch_style"] == "film"
    (project / "brief.md").write_text("## 设计风格\n" + "水墨淡彩," * 60 + "\n", encoding="utf-8")
    res = asyncio.run(core.api_board_sketch_style_set("demo", {"style": "project"}))
    assert res["style"] == "project" and res["project_style"]["available"] and res["project_style"]["source"] == "brief"
    assert json.loads((project / "settings.json").read_text())["output"]["sketch_style"] == "project"
    got = asyncio.run(core.api_board_sketch_style_get("demo"))
    ps = got["project_style"]
    assert got["style"] == "project" and ps["brief_chars"] == 300 and len(ps["preview"]) == 200
    assert ps["max_chars"] == sbb.PROJECT_STYLE_MAX


def test_whole_episode_per_one_runs_one_key_per_batch(project, monkeypatch):
    """「一次 1 张」:宿主每批只放一个键(CLI 对单镜批次走单张);默认 / per=4 仍每 4 镜一批;其它值 400。"""
    started = []

    class FakeThread:
        def __init__(self, target, args, daemon):
            started.append((target, args))

        def start(self):
            pass

    monkeypatch.setattr(core.threading, "Thread", FakeThread)
    monkeypatch.setattr(core.HUB, "publish", lambda *_a, **_k: None)
    with pytest.raises(core.ServiceError) as e:
        asyncio.run(core.api_board_sketch_start("demo", "ep01", {"all": True, "per": 3}))
    assert e.value.status_code == 400
    r = asyncio.run(core.api_board_sketch_start("demo", "ep01", {"all": True, "per": 1}))
    assert r["queued"] == 5 and r["per"] == 1 and r["batches"] == 5
    job = core.BOARD_SKETCH_JOBS["demo/ep01/*"]
    assert job["mode"] == "single" and job["per"] == 1
    target, args = started[-1]
    assert target is core._board_sketch_batch_worker and args[-1] == 1

    cmds = []

    def fake_run(cmd, **_kw):
        cmds.append(cmd)
        keys = cmd[cmd.index("--keys") + 1].split(",")
        for k in keys:
            sbb.update_index(project, "ep01", k, {"status": "done", "file": f"assets/storyboard/ep01/{k}.png"})
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(core.subprocess, "run", fake_run)
    target(*args)
    assert [c[c.index("--keys") + 1] for c in cmds] == ["S01-01", "S01-02", "S01-03", "S01-04", "S01-05"]
    assert all("--grid" in c for c in cmds) and job["done"] == 5 and job["status"] == "done"

    # 缺省 per = 4:每 4 镜一批
    job["status"] = "done"
    cmds.clear()
    r = asyncio.run(core.api_board_sketch_start("demo", "ep01", {"all": True, "force": True}))
    assert r["per"] == 4 and r["batches"] == 2 and core.BOARD_SKETCH_JOBS["demo/ep01/*"]["mode"] == "grid"
    target, args = started[-1]
    target(*args)
    assert [c[c.index("--keys") + 1] for c in cmds] == ["S01-01,S01-02,S01-03,S01-04", "S01-05"]


def test_cli_dry_run_uses_project_style(tmp_path):
    """CLI:项目画风写进提示词并标来源;创作定调没填画面风格时打印 INFO 按 film 出。"""
    data = tmp_path / "data"
    base = data / "projects" / "demo"
    (base / "directing" / "ep01").mkdir(parents=True)
    (base / "directing" / "ep01" / "storyboard.json").write_text(json.dumps({"scenes": [
        {"scene_no": "S01", "shots_draft": [{"order": 1, "content": "开场"}, {"order": 2, "content": "第二镜"}]}]},
        ensure_ascii=False), encoding="utf-8")
    (base / "settings.json").write_text(json.dumps({"output": {"sketch_style": "project"}}))
    cfg = tmp_path / "genconfig.json"
    cfg.write_text(json.dumps({"image": {"provider": "fal", "fal": {"api_key": "x", "model": "fal-ai/flux-2-pro"}}}))
    env = dict(os.environ, VIDEOAGENTS_DATA_DIR=str(data), VIDEOAGENTS_CONFIG_PATH=str(cfg))
    env.pop("VIDEOAGENTS_IMAGE_PROVIDER", None)
    env.pop("VIDEOAGENTS_IMAGE_MODEL", None)

    def run(*extra):
        r = subprocess.run([sys.executable, str(ROOT / "code" / "storyboard_sketch.py"), "--project", "demo", "--ep", "ep01",
                            "--dry-run", *extra], capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=120)
        assert r.returncode == 0, r.stdout + r.stderr
        return r.stdout

    out = run("--grid")
    assert out.count("INFO 项目画风不可用") == 1 and "style=film " in out and "A page from a feature film's storyboard" in out
    (base / "brief.md").write_text("## 设计风格\n水彩手绘质感,暖黄与灰绿为主\n", encoding="utf-8")
    out = run("--grid")
    assert "style=project(source=brief)" in out and "rendered in this project's own visual style — 水彩手绘质感" in out
    assert "INFO" not in out
    out = run("--scene", "S01", "--order", "1")
    assert "style=project(source=brief)" in out and out.count("A storyboard frame for this production") == 1
    out = run("--scene", "S01", "--order", "1", "--style", "konte")
    assert "style=konte " in out and "e-konte" in out
