# -*- coding: utf-8 -*-
"""花字时间轴(modules/caption_timeline.py,2026-09-25):后期采纳版本取源 + 删段换算 + 组边界层 + 片头偏移。
合成 lavfi 小视频,不依赖真实项目;不需要 Chromium(只测换算与规划,不烧录)。"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_TMP = tempfile.mkdtemp(prefix="va-captl-")
os.environ.setdefault("VIDEOAGENTS_DATA_DIR", _TMP)
sys.path.insert(0, str(ROOT / "modules"))
sys.path.insert(0, str(ROOT / "code"))

import caption_timeline as ct  # noqa: E402
import speechalign as sa  # noqa: E402
import timemap_layers  # noqa: E402

HAS_FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
pytestmark = pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg")
DATA = Path(os.environ["VIDEOAGENTS_DATA_DIR"])
EP = "ep01"


def _clip(path: Path, color: str, seconds: float):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s=160x90:r=24:d={seconds}",
                    "-f", "lavfi", "-i", f"sine=f=440:d={seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(path)], check=True)


@pytest.fixture(scope="module")
def project():
    """3 组 × 2s;grp002 采纳了删段版本 v1(删 [0.5,1.0) → 1.5s);grp003 前插 1.0s 字卡;正片 = cut_post_v2(转场产物)。"""
    base = DATA / "projects" / "captl"
    if base.exists():
        shutil.rmtree(base)
    ed = base / "edit" / EP
    (base / "directing" / EP).mkdir(parents=True)
    ed.mkdir(parents=True)
    groups = ["grp001", "grp002", "grp003"]
    sl = {"episode": EP, "generation_groups": [
        {"group_id": g, "scene_id": "SCN-0001", "shots": [f"sh{i + 1:03d}"], "total_duration_s": 2,
         **({"transition_in": {"type": "fade_black", "inserts": [{"kind": "title_card", "duration_s": 1.0, "audio": "mute"}]}}
            if g == "grp003" else {})}
        for i, g in enumerate(groups)],
        "shots": [{"shot_id": f"sh{i + 1:03d}", "duration_s": 2} for i in range(3)]}
    (base / "directing" / EP / "shot_list.json").write_text(json.dumps(sl, ensure_ascii=False))
    for g, c in zip(groups, ("red", "blue", "green")):
        _clip(base / "assets" / "clips" / EP / f"{g}.mp4", c, 2.0)
    _clip(base / "assets" / "post" / EP / "grp002" / "v1.mp4", "blue", 1.5)
    plan = {"schema": "post_plan/1.0", "ep": EP, "recipes": [], "current": {"grp002": 1},
            "versions": {"grp002": [{"v": 1, "file": f"assets/post/{EP}/grp002/v1.mp4", "recipes": [], "base_v": 0,
                                     "created_by": "cutout", "adopted_at": "2026-09-25 00:00:00", "cleaned": False,
                                     "time_ops": [{"src_t0": 0.5, "src_t1": 1.0, "out_len": 0.0, "kind": "cutout"}]}]}}
    (ed / "post_plan.json").write_text(json.dumps(plan))
    # timeline(sync-timeline 后:后期拼片基准)
    tracks, cum = [], 0.0
    for g, d in zip(groups, (2.0, 1.5, 2.0)):
        tracks.append({"group_id": g, "src": f"assets/post/{EP}/_cut/{g}.mp4", "in": 0.0, "out": d, "speed": 1.0,
                       "timeline_in": cum, "timeline_out": cum + d, "duration_s": d})
        cum += d
    (ed / "timeline.json").write_text(json.dumps({"episode": EP, "duration_s": cum, "tracks": {"video": tracks, "audio": []},
                                                  "transitions": [], "post": {"versions": {"grp002": {"v": 1}}}}))
    (ed / "timemap.json").write_text(json.dumps({"schema": "timemap/1.0", "layer": "post_versions",
                                                 "ops": [{"src_t0": 2.5, "src_t1": 3.0, "out_len": 0.0, "kind": "cutout", "group_id": "grp002"}]}))
    _clip(ed / "cut_post.mp4", "gray", 5.5)
    _clip(ed / "cut_post_v2.mp4", "gray", 6.5)
    (ed / "transitions_render.json").write_text(json.dumps({
        "src_cut": f"edit/{EP}/cut_post.mp4", "out_cut": f"edit/{EP}/cut_post_v2.mp4",
        "timemap": {"basis": f"edit/{EP}/cut_post.mp4", "ops": [
            {"src_t0": 3.5, "src_t1": 3.5, "out_len": 1.0, "insert_s": 1.0, "audio": "mute", "kind": "boundary_insert",
             "from_group": "grp002", "to_group": "grp003"}]}}))
    _clip(ed / "final.mp4", "gray", 7.5)   # 片头 1.0s + 正片 6.5s
    (ed / "final_layout.json").write_text(json.dumps({"cut_offset_s": 1.0, "segments": [
        {"name": "intro", "file": f"edit/{EP}/intro_outro/intro.mp4"}, {"name": "cut", "file": f"edit/{EP}/cut_post_v2.mp4"}]}))
    (base / "settings.json").write_text("{}")
    return base


def test_resolve_cut_and_offset(project):
    assert timemap_layers.resolve_cut(project, EP).name == "cut_post_v2.mp4"
    assert timemap_layers.cut_offset_s(project, EP) == 1.0


def test_groups_sources_and_windows(project):
    tl = ct.CaptionTimeline.resolve(project, EP)
    assert tl.cut.name == "cut_post_v2.mp4" and tl.final is not None and tl.cut_offset_s == 1.0
    assert tl.order == ["grp001", "grp002", "grp003"]
    assert tl.groups["grp001"].v == 0 and tl.groups["grp001"].src_rel.endswith("clips/ep01/grp001.mp4")
    assert tl.groups["grp002"].v == 1 and tl.groups["grp002"].src_rel.endswith("grp002/v1.mp4")
    assert [(o["src_t0"], o["src_t1"], o["out_len"]) for o in tl.groups["grp002"].local_ops] == [(0.5, 1.0, 0.0)]
    assert not tl.timeline_stale and not tl.warnings
    # 组边界层:grp003 起点排在 1.0s 字卡之后;grp002 组尾排在字卡之前
    assert tl.group_window("grp001") == (0.0, 2.0)
    assert tl.group_window("grp002") == (2.0, 3.5)
    assert tl.group_window("grp003") == (4.5, 6.5)
    assert tl.group_at(3.7) is None and tl.group_at(4.6) == "grp003" and tl.group_at(2.1) == "grp002"
    # 无混音清单:字幕 / 声轨 → cut 都是 post + pads 全表
    assert tl.ops and tl.audio_ops and abs(sum(o["out_len"] - (o["src_t1"] - o["src_t0"]) for o in tl.ops) - 0.5) < 1e-6


def test_local_mapping_through_cutout_and_pads(project):
    tl = ct.CaptionTimeline.resolve(project, EP)
    assert tl.local_to_cut("grp002", 0.3) == 2.3
    assert tl.local_to_cut("grp002", 0.7) is None                 # 被删段
    assert tl.local_to_cut("grp002", 1.5) == 3.0                  # 1.5 - 0.5 删掉的
    assert tl.local_to_cut("grp002", 2.0, end=True) == 3.5        # 组尾恰在插入点:排在字卡前
    assert tl.local_to_cut("grp003", 0.0) == 4.5
    assert tl.final_time("grp003", 0.5) == 5.0 + 1.0              # + 片头
    # 逆向:cut → v0 local
    assert tl.cut_to_local("grp003", 5.0) == 0.5
    assert tl.cut_to_local("grp002", 3.0) == 1.5
    assert tl.cut_to_local("grp002", 2.3) == 0.3


def test_burn_captions_and_final_plan(project):
    tl = ct.CaptionTimeline.resolve(project, EP)
    caps = [{"id": "a", "group_id": "grp002", "local_start": 0.2, "local_end": 1.2, "text": "x"},
            {"id": "b", "group_id": "grp002", "local_start": 0.6, "local_end": 0.9, "text": "y"},
            {"id": "c", "group_id": "grp002", "local_start": 0.7, "local_end": 1.8, "text": "z", "sfx": {"sfx_id": "pop", "offset_s": -0.1}},
            {"id": "d", "group_id": "grp003", "local_start": 0.5, "local_end": 1.5, "text": "w"}]
    kept, dropped = tl.burn_captions("grp002", caps[:3])
    assert dropped == ["b"]
    assert [(k["id"], k["local_start"], k["local_end"]) for k in kept] == [("a", 0.2, 0.7), ("c", 0.5, 1.3)]
    items, drop2 = ct.plan_final_items(tl, {"captions": caps})
    assert drop2 == ["b(落在被删段 / 修剪之外)"]
    got = {it["cap"]["id"]: (it["t0"], it["t1"]) for it in items}
    assert got == {"a": (3.2, 3.7), "c": (3.5, 4.3), "d": (6.0, 7.0)}
    uses = ct.sfx_uses(items)
    assert uses == [{"sfx_id": "pop", "at_s": 3.4, "gain_db": None, "pitch": None, "caption": "c"}]
    assert ct.sfx_plan_fingerprint(items) and ct.sfx_plan_fingerprint([]) is None
    assert tl.audio_authority()[1] == "final"          # 有片头 / 时长编辑:声轨权威 = 干净版成片自带声轨


def test_fingerprint_changes_with_adoption(project):
    tl1 = ct.CaptionTimeline.resolve(project, EP)
    plan_p = project / "edit" / EP / "post_plan.json"
    plan = json.loads(plan_p.read_text())
    plan["current"] = {}
    plan_p.write_text(json.dumps(plan))
    try:
        tl2 = ct.CaptionTimeline.resolve(project, EP)
        assert tl2.fingerprint() != tl1.fingerprint()
        assert tl2.groups["grp002"].v == 0 and tl2.timeline_stale == ["grp002(timeline v1 ≠ 采纳 v0)"]
        assert tl2.warnings
    finally:
        plan["current"] = {"grp002": 1}
        plan_p.write_text(json.dumps(plan))


def test_snap_uses_timeline_windows_and_writes_v0_local(project):
    tl = ct.CaptionTimeline.resolve(project, EP)
    shot_list = json.loads((project / "directing" / EP / "shot_list.json").read_text())
    track = {"schema_version": 1, "words": [
        {"text": "你", "start": 4.60, "end": 4.80, "i": 0}, {"text": "好", "start": 4.80, "end": 5.10, "i": 1}]}
    data = {"captions": [{"id": "k", "group_id": "grp002", "local_start": 0.1, "local_end": 0.4, "start": 2.1, "end": 2.4, "text": "你好"}]}
    rep = sa.snap_captions(data, track, shot_list, tl=tl)
    assert rep["snapped"] and rep["snapped"][0]["moved"] == "grp002"
    c = data["captions"][0]
    assert c["group_id"] == "grp003" and c["start"] == 4.6 and c["end"] == 5.1
    assert c["local_start"] == 0.1 and c["local_end"] == 0.6        # cut → v0 local(grp003 起点 4.5)
    assert sa.check_speech_alignment(data, track, shot_list, tl=tl) == []
    stale = sa.staleness({"schema_version": 1, "words": [], "transcript": {}, "audio": None}, project, EP, tl)
    assert any("time_axis" in x for x in stale)


def test_finalize_still_exposes_load_timemap():
    import finalize_episode
    assert finalize_episode.load_timemap is timemap_layers.load_timemap
