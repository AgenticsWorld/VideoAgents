# -*- coding: utf-8 -*-
"""混音基准(mix_basis,2026-09-23 §8B ④):p8-mix 按采纳版本混音并盖章;finalize 按清单决定 post_versions 层是否套用。
合成 lavfi 小视频,不依赖真实项目。"""
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_TMP = tempfile.mkdtemp(prefix="va-mix-")
os.environ.setdefault("VIDEOAGENTS_DATA_DIR", _TMP)
sys.path.insert(0, str(ROOT / "modules"))
sys.path.insert(0, str(ROOT / "code"))

import mix_manifest as mb  # noqa: E402
import post_plan as pp  # noqa: E402

HAS_FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
DATA = Path(os.environ["VIDEOAGENTS_DATA_DIR"])
pytestmark = pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg")


def _clip(path: Path, color: str, seconds: float = 2.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s=320x180:r=24:d={seconds}",
                    "-f", "lavfi", "-i", f"sine=f=440:d={seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(path)], check=True)


def _wav(path: Path, seconds: float):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=f=440:d={seconds}", "-ar", "48000", str(path)], check=True)


@pytest.fixture(scope="module")
def project():
    base = DATA / "projects" / "mixtest"
    if base.exists():
        shutil.rmtree(base)
    (base / "directing" / "ep01").mkdir(parents=True)
    (base / "edit" / "ep01").mkdir(parents=True)
    groups = [("grp001", "red"), ("grp002", "blue"), ("grp003", "green")]
    sl = {"episode": "ep01", "generation_groups": [
        {"group_id": g, "scene_id": "SCN-0001", "scene_no": "S01", "shots": [f"sh{i + 1:03d}"], "total_duration_s": 2}
        for i, (g, _c) in enumerate(groups)],
        "shots": [{"shot_id": f"sh{i + 1:03d}", "duration_s": 2, "is_dialogue": False, "dialogue_lines": []} for i in range(3)]}
    (base / "directing" / "ep01" / "shot_list.json").write_text(json.dumps(sl, ensure_ascii=False))
    tracks, cum = [], 0.0
    for g, c in groups:
        _clip(base / "assets" / "clips" / "ep01" / f"{g}.mp4", c)
        tracks.append({"group_id": g, "src": f"assets/clips/ep01/{g}.mp4", "in": 0.0, "out": 2.0, "cum_start_s": cum, "cum_end_s": cum + 2})
        cum += 2
    (base / "edit" / "ep01" / "timeline.json").write_text(json.dumps({"episode": "ep01", "duration_s": cum,
                                                                       "tracks": {"video": tracks, "audio": []}, "transitions": []}))
    (base / "settings.json").write_text("{}")
    return base


def cli(script, *args, expect=0):
    env = dict(os.environ, VIDEOAGENTS_DATA_DIR=str(DATA))
    p = subprocess.run([sys.executable, str(ROOT / "code" / script), *args, "--project", "mixtest", "--ep", "ep01"],
                       capture_output=True, text=True, env=env, cwd=ROOT)
    assert p.returncode == expect, p.stdout + p.stderr
    return p.stdout


def cli_all(script, *args, expect=0):
    """同 cli,返回 stdout + stderr(SystemExit 的 FAIL 文案在 stderr)。"""
    env = dict(os.environ, VIDEOAGENTS_DATA_DIR=str(DATA))
    p = subprocess.run([sys.executable, str(ROOT / "code" / script), *args, "--project", "mixtest", "--ep", "ep01"],
                       capture_output=True, text=True, env=env, cwd=ROOT)
    assert p.returncode == expect, p.stdout + p.stderr
    return p.stdout + p.stderr


def _cutout(project, gid, base_v, t0, t1):
    from services.runtime import core
    r = asyncio.run(core.api_post_cutout("mixtest", "ep01", {"group_id": gid, "base_v": base_v, "cuts": [{"t0": t0, "t1": t1}]}))
    plan = pp.load_plan(project, "ep01")
    pp.adopt_version(plan, gid, r["v"])
    pp.save_plan(project, "ep01", plan)
    return r["v"]


def test_sources_stamp_check_and_finalize_decision(project):
    import finalize_episode as fe

    ed = project / "edit" / "ep01"
    cut_post, cut_v1 = ed / "cut_post.mp4", ed / "cut_v1.mp4"

    # ① 无后期版本:sources 全 v0,起点 0/2/4
    j = json.loads(cli("mix_basis.py", "sources", "--json"))
    assert [g["v"] for g in j["groups"]] == [0, 0, 0] and j["groups_on_post_version"] == 0
    assert [round(g["cum_start_s"], 2) for g in j["groups"]] == [0.0, 2.0, 4.0] and j["delta_vs_v0_s"] == 0
    # 无混音产物:stamp 拒绝;check = WARN(none)
    cli("mix_basis.py", "stamp", "--task-id", "p8-ep01-mix", expect=1)
    assert mb.compare(project, "ep01")["status"] == mb.STATUS_NONE
    assert "mix_basis_current: WARN" in cli("mix_basis.py", "check")

    # ② 纯 v0 混音盖章 → current
    _wav(project / "assets" / "audio" / "final" / "ep01.wav", 6.0)
    out = cli("mix_basis.py", "stamp", "--task-id", "p8-ep01-mix")
    assert "盖章" in out
    man = mb.load_manifest(project, "ep01")
    assert man["all_v0"] is True and man["delta_s"] == 0 and len(man["groups"]) == 3
    assert mb.compare(project, "ep01")["status"] == mb.STATUS_CURRENT
    assert "mix_basis_current: PASS" in cli("mix_basis.py", "check")

    # ③ 采纳一个删段版本(grp002 0.5–1.0s)→ 清单过期,但混音是纯 v0:WARN + finalize 旧口径兜底(层 1 照旧)
    _cutout(project, "grp002", 0, 0.5, 1.0)
    res = mb.compare(project, "ep01")
    assert res["status"] == mb.STATUS_STALE and res["mix_has_ops"] is False and "grp002" in res["changed_groups"][0]
    assert "mix_basis_current: WARN" in cli("mix_basis.py", "check")
    assert "mix_basis_current: WARN" in cli("post_apply.py", "check")
    cli("post_apply.py", "build-cut")
    cli("post_apply.py", "sync-timeline")
    notes = []
    ops, info = fe.load_timemap(project, "ep01", cut_post, notes)
    assert ops and info["mix_basis"]["status"] == "stale" and any("兜底" in n for n in notes)
    assert any(l["layer"] == "post_versions" and not l.get("skipped") for l in info["layers"])

    # ④ 按采纳版本重混盖章(5.5s)→ current;finalize 对后期拼片跳过层 1,对原粗剪拒封装
    _wav(project / "assets" / "audio" / "final" / "ep01.wav", 5.5)
    cli("mix_basis.py", "stamp", "--task-id", "p8-ep01-mix-r2")
    man = mb.load_manifest(project, "ep01")
    assert man["all_v0"] is False and man["delta_s"] == pytest.approx(-0.5, abs=0.05) and man["groups_on_post_version"] == 1
    j = json.loads(cli("mix_basis.py", "sources", "--json"))
    assert j["groups"][1]["v"] > 0 and j["groups"][1]["src"].startswith("assets/post/ep01/grp002/")
    assert [round(g["cum_start_s"], 2) for g in j["groups"]] == [0.0, 2.0, 3.5]
    assert [round(g["cum_start_v0_s"], 2) for g in j["groups"]] == [0.0, 2.0, 4.0]
    assert mb.compare(project, "ep01")["status"] == mb.STATUS_CURRENT
    assert "mix_basis_current: PASS" in cli("post_apply.py", "check")
    notes = []
    ops, info = fe.load_timemap(project, "ep01", cut_post, notes)
    assert ops == [] and any(l["layer"] == "post_versions" and l.get("skipped") for l in info["layers"])
    assert any("不再套" in n for n in notes)
    # ④b 字幕基准声明(issue #114):声明 original → 字幕照套层 1(−0.5s),声轨表不变;未声明且末条超出正片 → shift FAIL 并提示
    assert info["subs_basis"] == {"basis": "auto", "source": None, "post_layer": "skipped", "post_delta_s": pytest.approx(-0.5, abs=0.05)}
    ops_o, info_o = fe.load_timemap(project, "ep01", cut_post, [], subs_basis="original")
    assert info_o["subs_basis"]["post_layer"] == "applied" and info_o["audio_ops"] == info["audio_ops"] == []
    assert info_o["ops"] == ops_o and fe.timemap.total_delta(ops_o) == pytest.approx(-0.5, abs=0.05)
    (ed / "subtitles.srt").write_text("1\n00:00:00,500 --> 00:00:01,500\n甲\n\n2\n00:00:05,000 --> 00:00:06,200\n乙\n", encoding="utf-8")
    out = cli_all("finalize_episode.py", "shift", "--cut", "cut_post.mp4", expect=1)
    assert "subtitle_basis" in out and "--subs-basis original" in out and not (ed / "subtitles_final.srt").exists()
    cli("finalize_episode.py", "shift", "--cut", "cut_post.mp4", "--subs-basis", "original")
    assert json.loads((ed / "subtitles.basis.json").read_text(encoding="utf-8"))["basis"] == "original"
    assert fe.parse_srt(ed / "subtitles_final.srt")[1] == (pytest.approx(4.5, abs=0.05), pytest.approx(5.7, abs=0.05))
    ops_d, info_d = fe.load_timemap(project, "ep01", cut_post, [])          # 不带参数也按声明文件
    assert ops_d == ops_o and info_d["subs_basis"]["source"] == "edit/ep01/subtitles.basis.json"
    cli("finalize_episode.py", "shift", "--cut", "cut_post.mp4", "--subs-basis", "auto", expect=1)   # 删声明 → 回到默认口径
    assert not (ed / "subtitles.basis.json").exists()
    for n in ("subtitles.srt", "subtitles_final.srt", "final_layout.json"):
        (ed / n).unlink(missing_ok=True)
    with pytest.raises(SystemExit, match="原粗剪基准"):
        fe.load_timemap(project, "ep01", cut_v1, [])
    # 不用外挂声轨时只 WARN 不拦
    notes = []
    fe.load_timemap(project, "ep01", cut_v1, notes, audio_used=False)
    assert any("原粗剪基准" in n for n in notes)

    # ⑤ 再采纳一个删段(grp003)→ 混音带后期时轴且过期:FAIL,post_ok 拒签,finalize 拒封装
    v3 = _cutout(project, "grp003", 0, 1.0, 1.5)
    res = mb.compare(project, "ep01")
    assert res["status"] == mb.STATUS_STALE and res["mix_has_ops"] is True
    assert "mix_basis_current: FAIL" in cli("mix_basis.py", "check", expect=1)
    out = cli("post_apply.py", "check", expect=1)
    assert "mix_basis_current: FAIL" in out and "重跑 p8-mix" in out
    with pytest.raises(SystemExit, match="重跑 p8-mix"):
        fe.load_timemap(project, "ep01", cut_post, [])

    # ⑥ 回滚 grp003 到母本 → 与清单一致,恢复 PASS;版本号变但时轴不变 → 只 WARN
    cli("post_apply.py", "rollback", "--group", "grp003", "--to", "0")
    assert mb.compare(project, "ep01")["status"] == mb.STATUS_CURRENT
    plan = pp.load_plan(project, "ep01")
    pp.register_version(plan, project, "ep01", "grp003", "assets/clips/ep01/grp003.mp4", [], 0, by="test")
    pp.adopt_version(plan, "grp003", max(int(x["v"]) for x in pp.group_versions(plan, "grp003")))
    pp.save_plan(project, "ep01", plan)
    res = mb.compare(project, "ep01")
    assert res["status"] == mb.STATUS_VERSIONS_CHANGED and "仅版本号" in res["changed_groups"][0]
    assert mb.check_row(res)[0] == "WARN"
    assert v3 > 0


def test_check_row_fails_on_offscreen_changed_and_compare_reads_stamp(monkeypatch):
    """画外对白轨(声画分离 2026-10-03):盖章指纹 vs 当前台账指纹变了 = FAIL;两边都没有(None)= 一致。"""
    base = {"status": mb.STATUS_CURRENT, "boundary_status": mb.BND_NONE, "detail": "x"}
    assert mb.check_row(base)[0] == "PASS"
    assert mb.check_row({**base, "offscreen_changed": True})[0] == "FAIL"
    # 模块缺失 / 无台账:指纹 None、rows 空、不抛
    monkeypatch.setattr(mb, "_offscreen", lambda: None)
    assert mb.offscreen_fingerprint(Path("/nonexistent"), "ep01") is None
    assert mb.offscreen_rows(Path("/nonexistent"), "ep01", []) == []
    assert mb.offscreen_stale(Path("/nonexistent"), "ep01") is None
    import types
    fake = types.SimpleNamespace(load_manifest=lambda p, ep: {"lines": [1]}, fingerprint=lambda m: "abc123",
                                 mix_rows=lambda p, ep, rows: [{"group_id": "g", "t0": 1.0}, {"group_id": "g", "t0": None, "dropped": "cut"}],
                                 is_stale=lambda p, ep, man=None: True)
    monkeypatch.setattr(mb, "_offscreen", lambda: fake)
    assert mb.offscreen_fingerprint(Path("/x"), "ep01") == "abc123"
    assert [o["t0"] for o in mb.offscreen_rows(Path("/x"), "ep01", [])] == [1.0]                      # 删段内的句子不列
    assert len(mb.offscreen_rows(Path("/x"), "ep01", [], include_dropped=True)) == 2
    assert mb.offscreen_stale(Path("/x"), "ep01") is True


def test_compare_flags_offscreen_changed(project, monkeypatch):
    """盖章时无画外句(None),之后台账出现画外句 → offscreen_changed FAIL;指纹一致 → 不变。"""
    if mb.compare(project, "ep01")["status"] == mb.STATUS_NONE:
        pytest.skip("依赖前一个用例盖章")
    res = mb.compare(project, "ep01")
    assert res["offscreen_changed"] is False and res["mix_offscreen_fp"] is None
    monkeypatch.setattr(mb, "offscreen_fingerprint", lambda proj, ep, man=None: "deadbeef")
    res = mb.compare(project, "ep01")
    assert res["offscreen_changed"] is True and "画外对白轨已变" in res["detail"]
    assert mb.check_row(res)[0] == "FAIL"
