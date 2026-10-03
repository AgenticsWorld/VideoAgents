# -*- coding: utf-8 -*-
"""过场设计二期(2026-09-26):工位 set_design / H3A accept_all_proposed / 生成式定场空镜 i2v(clips_needed / prepare_clip_stills /
check_clips)/ render_transitions build 消费 i2v clip。合成小项目,不依赖真实数据。"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_TMP = tempfile.mkdtemp(prefix="va-td2-")
os.environ.setdefault("VIDEOAGENTS_DATA_DIR", _TMP)
sys.path.insert(0, str(ROOT / "modules"))
sys.path.insert(0, str(ROOT / "code"))
sys.path.insert(0, str(ROOT))

import check_generation_groups as cgg  # noqa: E402
from modules import transition_design as td  # noqa: E402

HAS_FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
DATA = Path(os.environ["VIDEOAGENTS_DATA_DIR"])
BID = "B-grp002-grp003"


def _clip(path: Path, color: str, seconds: float = 2.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s=320x180:r=24:d={seconds}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)


def _project(name: str, mode: str, **extra) -> Path:
    from PIL import Image
    base = DATA / "projects" / name
    if base.exists():
        shutil.rmtree(base)
    (base / "directing" / "ep01").mkdir(parents=True)
    (base / "bible" / "scenes").mkdir(parents=True)
    (base / "assets" / "concepts" / "scenes" / "SCN-0002" / "plates").mkdir(parents=True)
    tr = {"mode": mode, **extra}
    (base / "settings.json").write_text(json.dumps({"output": {"aspect": "16:9"}, "transitions": tr}, ensure_ascii=False))
    (base / "bible" / "scenes" / "index.json").write_text(json.dumps({"scenes": [
        {"id": "SCN-0001", "name": "书房"}, {"id": "SCN-0002", "name": "南天门"}]}, ensure_ascii=False))
    Image.new("RGB", (1200, 700), (90, 120, 160)).save(base / "assets" / "concepts" / "scenes" / "SCN-0002" / "plates" / "p1.png")
    shots = [{"shot_id": f"sh00{i}", "scene_no": "S01" if i < 5 else "S02", "size": "中景", "duration_s": 2.0} for i in range(1, 7)]
    groups = [
        {"group_id": "grp001", "scene_id": "SCN-0001", "scene_no": "S01", "shots": ["sh001", "sh002"], "total_duration_s": 4, "time_of_day": "日间", "characters_union": ["CHAR-0001"]},
        {"group_id": "grp002", "scene_id": "SCN-0001", "scene_no": "S01", "shots": ["sh003", "sh004"], "total_duration_s": 4, "time_of_day": "日间", "characters_union": ["CHAR-0001"]},
        {"group_id": "grp003", "scene_id": "SCN-0002", "scene_no": "S02", "shots": ["sh005", "sh006"], "total_duration_s": 4, "time_of_day": "日间", "characters_union": ["CHAR-0002"]},
    ]
    (base / "directing" / "ep01" / "shot_list.json").write_text(json.dumps(
        {"episode": "ep01", "budget_s": 60, "shots": shots, "generation_groups": groups}, ensure_ascii=False, indent=2))
    (base / "directing" / "ep01" / "shot_plates.json").write_text(json.dumps(
        {"shots": {"sh005": {"lighting_scheme_id": "day", "plates": [{"file": "assets/concepts/scenes/SCN-0002/plates/p1.png", "role": "start"}]}}}))
    return base


def _design(base):
    return td.load_design(base, "ep01")


def _row(base, bid=BID):
    return next(b for b in _design(base)["boundaries"] if b["id"] == bid)


def test_classic_keeps_ffmpeg_establishing():
    base = _project("td2-classic", "classic")
    assert td.enabled(base, "ep01")
    rows = td.diagnose(base, "ep01")
    r = next(b for b in rows if b["id"] == BID)
    assert r["diagnosis"]["class"] == "scene_change"
    assert r["establishing"]["mode"] == "plate_kenburns"
    assert r["ep"] == "ep01" and r["scene"]["name"] == "南天门"
    td.propose(base, "ep01")
    b = _row(base)
    assert b["status"] == "proposed"
    est = [x for x in b["design"]["inserts"] if x["kind"] == "establishing"]
    assert est and est[0]["source"]["mode"] == "plate_kenburns"      # 经典档不花视频生成费
    assert not td.clips_needed(base, "ep01", include_proposed=True)
    ok, items = td.check(base, "ep01")
    by = {i["check"]: i for i in items}
    assert by["transition_design_generative_allowed"]["result"] == "PASS"
    assert "transition_clips_ready" not in by


def test_minimal_disables_workstation():
    base = _project("td2-minimal", "minimal")
    assert not td.enabled(base, "ep01")


def test_cinematic_i2v_design_agent_and_clips():
    base = _project("td2-cine", "cinematic")
    td.propose(base, "ep01")
    b = _row(base)
    est = next(x for x in b["design"]["inserts"] if x["kind"] == "establishing")
    src = est["source"]
    assert src["mode"] == "i2v"
    assert src["file"] == f"assets/transitions/ep01/{BID}.establishing.mp4"
    assert src["still"] == f"assets/transitions/ep01/{BID}.establishing.still.jpg"
    assert src["base"]["mode"] == "plate_kenburns" and src["base"]["file"].endswith("p1.png")
    assert "南天门" in src["prompt"] and "无人物" in src["prompt"]
    assert est.get("overlay_card") and "南天门" in " ".join(est["overlay_card"]["lines"])   # 电影感:定场 + 叠地点字幕
    # 契约:i2v 须给 source.file
    errs = cgg.check_transitions({"budget_s": 60, "generation_groups": [
        {"group_id": "a", "shots": ["s1"]}, {"group_id": "b", "shots": ["s2"], "transition_in": {**b["design"], "inserts": [
            {**est, "source": {"scene_id": "SCN-0002", "mode": "i2v"}}]}}]}, 10.0)
    assert any("i2v 定场须给 source.file" in e for e in errs)
    # 工位换主设计(候选「只叠地点字幕」),仍 proposed、不写 shot_list
    alts = b["alternatives"]
    idx = next(i for i, a in enumerate(alts) if "只叠" in a["label"])
    b2 = td.set_design(base, "ep01", BID, alt=idx, note="首镜构图已交代地点")
    assert b2["status"] == "proposed" and b2["source"] == "agent"
    assert b2["alternatives"][0]["label"] == "原建议"
    assert "工位:首镜构图已交代地点" in b2["design"]["reason"]
    sl = json.loads((base / "directing" / "ep01" / "shot_list.json").read_text())
    assert "transition_in" not in sl["generation_groups"][2]
    # 重出建议不覆盖工位的建议
    td.propose(base, "ep01")
    assert _row(base)["design"] == b2["design"] and _row(base)["source"] == "agent"
    # 换回带 i2v 定场的原建议,渲首帧
    b3 = td.set_design(base, "ep01", BID, alt=0)
    assert any(x["kind"] == "establishing" for x in b3["design"]["inserts"])
    rows = td.clips_needed(base, "ep01", include_proposed=True)
    assert len(rows) == 1 and rows[0]["status"] == "proposed" and not rows[0]["exists"] and not rows[0]["still_exists"]
    assert rows[0]["request_duration_s"] >= td.I2V_MIN_DURATION_S
    done = td.prepare_clip_stills(base, "ep01", rows)
    assert done and (base / rows[0]["still"]).is_file()
    from PIL import Image
    assert Image.open(base / rows[0]["still"]).size == (1920, 1080)
    # 未定稿:shot_list 无 → clips_needed 空;check 只 WARN
    assert not td.clips_needed(base, "ep01")
    ok, items = td.check(base, "ep01")
    by = {i["check"]: i for i in items}
    assert by["transition_design_generative_allowed"]["result"] == "PASS"
    assert by["transition_clips_ready"]["result"] == "PASS"     # 定稿设计尚无生成式插入段
    # H3A 签字即接受剩余建议 → apply 进 shot_list
    accepted = td.accept_all_proposed(base, "ep01", by="sign:g6")
    assert BID in accepted
    sl = json.loads((base / "directing" / "ep01" / "shot_list.json").read_text())
    t = sl["generation_groups"][2]["transition_in"]
    assert t["inserts"][0]["source"]["mode"] == "i2v"
    assert _row(base)["status"] == "accepted" and _row(base)["decided_by"] == "sign:g6"
    rows = td.clips_needed(base, "ep01")
    assert len(rows) == 1 and rows[0]["status"] == "accepted" and not rows[0]["exists"]
    ok, items = td.check_clips(base, "ep01")
    assert not ok and items[0]["result"] == "FAIL"
    ok2, items2 = td.check(base, "ep01")
    assert {i["check"]: i for i in items2}["transition_clips_ready"]["result"] == "WARN"
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg 不可用")
    # 视频生成工位出 clip → 机检 PASS
    _clip(base / rows[0]["file"], "blue", 5.0)
    ok, items = td.check_clips(base, "ep01")
    assert ok, items
    rows = td.clips_needed(base, "ep01")
    assert rows[0]["exists"] and rows[0]["duration_ok"] and rows[0]["clip_duration_s"] >= 4.9
    # 过短 clip → FAIL
    _clip(base / rows[0]["file"], "blue", 1.0)
    ok, items = td.check_clips(base, "ep01")
    assert not ok and "时长不足" in items[0]["detail"]
    _clip(base / rows[0]["file"], "blue", 5.0)
    # 页面载荷:clips 状态
    pl = td.payload(base, "ep01")
    prow = next(r for r in pl["boundaries"] if r["id"] == BID)
    assert prow["clips"] and prow["clips"][0]["exists"] and prow["clips"][0]["still_url"]
    assert prow["render_state"] == "pending"


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg 不可用")
def test_render_build_consumes_i2v_clip_and_reports_missing():
    import render_transitions as rt
    base = _project("td2-build", "cinematic")
    for gid, c in (("grp001", "red"), ("grp002", "green"), ("grp003", "gray")):
        _clip(base / "assets" / "clips" / "ep01" / f"{gid}.mp4", c, 4.0)
    td.propose(base, "ep01")
    td.accept_all_proposed(base, "ep01", by="sign:g6")
    rows = td.clips_needed(base, "ep01")
    assert len(rows) == 1
    # clip 未出:build 记 missing,不顶替
    built = rt.do_build(base, "ep01", None, log=lambda *a, **k: None)
    meta = built[BID]
    ins = [r for r in meta["inserts"] if r["kind"] == "establishing"][0]
    assert ins.get("missing") and ins["expected"] == rows[0]["file"]
    pl = td.payload(base, "ep01")
    assert next(r for r in pl["boundaries"] if r["id"] == BID)["render_state"] == "awaiting_clip"
    # 出了 clip:指纹变化 → 重建,段帧数 = 插入段时长 × fps
    _clip(base / rows[0]["file"], "blue", 5.0)
    built = rt.do_build(base, "ep01", None, log=lambda *a, **k: None)
    ins = [r for r in built[BID]["inserts"] if r["kind"] == "establishing"][0]
    assert not ins.get("missing") and Path(ins["file"]).is_file()
    assert ins["frames"] == round(ins["duration_s"] * 24)
    assert ins.get("overlay")   # 叠地点字幕合成在定场段上
    assert Path(ins["thumb"]).is_file()


def test_custom_without_generative_rejects_i2v():
    base = _project("td2-custom", "custom", allow_generative=False, custom_map={"scene_change": "establishing_overlay"})
    td.propose(base, "ep01")
    b = _row(base)
    est = next(x for x in b["design"]["inserts"] if x["kind"] == "establishing")
    assert est["source"]["mode"] == "plate_kenburns"
    # 工位硬塞 i2v → check FAIL(模式不允许生成式过场)
    i2v = json.loads(json.dumps(b["design"]))
    i2v["inserts"][0]["source"] = td.i2v_source("ep01", b, est["source"])
    td.set_design(base, "ep01", BID, transition_in=i2v)
    ok, items = td.check(base, "ep01")
    by = {i["check"]: i for i in items}
    assert by["transition_design_generative_allowed"]["result"] == "FAIL"
    # 已裁决的边界工位不得改
    td.reject(base, "ep01", BID, by="user")
    with pytest.raises(ValueError):
        td.set_design(base, "ep01", BID, alt=0)
