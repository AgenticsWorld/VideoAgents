# -*- coding: utf-8 -*-
"""项目音乐库(music_library,2026-09-27):入库去重、跨集复用的使用记录、返工换曲标 superseded、机检 music_library_synced。
合成 lavfi 小音频,不依赖真实项目。"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules import music_library as ml  # noqa: E402

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="需要 ffmpeg")
CLI = [sys.executable, str(ROOT / "code" / "music_library.py")]


def _tone(path: Path, freq: int, seconds: float):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=f={freq}:d={seconds}", str(path)], check=True)


def _sheet(proj: Path, ep: str, cues: list, **extra):
    d = proj / ml.BGM_REL / ep
    d.mkdir(parents=True, exist_ok=True)
    (d / "cue_sheet.json").write_text(json.dumps({"cues": cues, **extra}, ensure_ascii=False))


def _cue(cue_id, file, a, b, **kw):
    return {"cue_id": cue_id, "file": file, "in_s": a, "out_s": b, "scene": "SCN-0001", "mood": kw.pop("mood", "紧张"),
            "license": kw.pop("license", {"source": "generated", "model": "m", "prompt": "tense strings"}), **kw}


@pytest.fixture()
def proj():
    base = Path(tempfile.mkdtemp(prefix="va-musiclib-")) / "p"
    _tone(base / ml.BGM_REL / "ep01" / "ep01_bgm_01.mp3", 440, 3)
    _tone(base / ml.BGM_REL / "ep01" / "ep01_bgm_02.mp3", 660, 2)
    _sheet(base, "ep01", [_cue("ep01_bgm_01", "ep01_bgm_01.mp3", 0, 3, genre="管弦", tempo_bpm=96, instrumentation="弦乐"),
                          _cue("ep01_bgm_02", "ep01_bgm_02.mp3", 10, 12, mood="轻快"),
                          _cue("ep01_bgm_03", "ep01_bgm_02.mp3", 20, 22, mood="轻快")])
    yield base
    shutil.rmtree(base.parent, ignore_errors=True)


def _sync(proj, ep, **kw):
    idx = ml.load_index(proj)
    rep = ml.sync_episode(proj, idx, ep, write=True, **kw)
    ml.save_index(proj, idx)
    return rep


def test_sync_adds_tracks_with_metadata_and_is_idempotent(proj):
    rep = _sync(proj, "ep01")
    assert [a["track_id"] for a in rep["added"]] == ["MUS-0001", "MUS-0002"] and rep["usage"] == 3
    idx = ml.load_index(proj)
    t1, t2 = idx["tracks"]
    assert (t1["genre"], t1["tempo_bpm"], t1["instrumentation"], t1["prompt"]) == ("管弦", 96.0, "弦乐", "tense strings")
    assert abs(t1["duration_s"] - 3) < 0.2 and ml.track_file(proj, t1).is_file()
    assert [u["cue_id"] for u in t2["used_in"]] == ["ep01_bgm_02", "ep01_bgm_03"]      # 同一文件两个 cue = 一首曲目两条使用
    rep2 = _sync(proj, "ep01")
    assert not rep2["added"] and not rep2["changed"] and len(ml.load_index(proj)["tracks"]) == 2
    assert ml.check_episode(proj, "ep01")["status"] == "PASS"                          # 首集:库里没有别集曲目,不要求报告


def test_reuse_from_library_and_report_gate(proj):
    _sync(proj, "ep01")
    out = subprocess.run(CLI + ["use", "--out-root", str(proj), "--ep", "ep02", "--track", "MUS-0001", "--output", "ep02_bgm_01.mp3", "--json"],
                         capture_output=True, text=True, check=True)
    lic = json.loads(out.stdout)["license"]
    assert lic["source"] == "library" and lic["track_id"] == "MUS-0001" and lic["original"]["model"] == "m"
    _tone(proj / ml.BGM_REL / "ep02" / "ep02_bgm_02.mp3", 880, 2)
    cues = [_cue("ep02_bgm_01", "ep02_bgm_01.mp3", 0, 3, license=lic), _cue("ep02_bgm_02", "ep02_bgm_02.mp3", 5, 7, mood="市井")]
    _sheet(proj, "ep02", cues)
    names = {i["name"]: i["status"] for i in ml.check_episode(proj, "ep02")["items"]}
    assert names["library_in_sync"] == "FAIL" and names["library_report"] == "FAIL"
    rep = _sync(proj, "ep02")
    assert [a["track_id"] for a in rep["added"]] == ["MUS-0003"] and rep["reused"] == [{"track_id": "MUS-0001", "cue_id": "ep02_bgm_01"}]
    t1 = ml.find_track(ml.load_index(proj), "MUS-0001")
    assert [(u["ep"], u["reused"]) for u in t1["used_in"]] == [("ep01", False), ("ep02", True)]
    assert ml.check_episode(proj, "ep02")["status"] == "FAIL"                          # 已同步但没写 music_library_report
    _sheet(proj, "ep02", cues, music_library_report={"consulted": True, "generated": []})
    assert ml.check_episode(proj, "ep02")["status"] == "FAIL"                          # 新生成的 cue 没给原因
    _sheet(proj, "ep02", cues, music_library_report={"consulted": True, "generated": [{"cue_id": "ep02_bgm_02", "reason": "库内无市井段"}]})
    assert ml.check_episode(proj, "ep02")["status"] == "PASS"
    assert ml.check_episode(proj, "ep01")["status"] == "PASS"                          # ep01 先于 ep02 曲目入库,不被追溯


def test_rework_supersedes_old_track_but_keeps_reused_one(proj):
    _sync(proj, "ep01")
    (proj / ml.BGM_REL / "ep02").mkdir(parents=True, exist_ok=True)
    shutil.copy2(ml.lib_dir(proj) / "MUS-0002.mp3", proj / ml.BGM_REL / "ep02" / "ep02_bgm_01.mp3")
    _sheet(proj, "ep02", [_cue("ep02_bgm_01", "ep02_bgm_01.mp3", 0, 2)])              # 没写 track_id:按内容指纹认出库内曲目
    assert _sync(proj, "ep02")["reused"] == [{"track_id": "MUS-0002", "cue_id": "ep02_bgm_01"}]
    _tone(proj / ml.BGM_REL / "ep01" / "ep01_bgm_01.mp3", 300, 3)                      # ep01 返工:两首都换掉
    _tone(proj / ml.BGM_REL / "ep01" / "ep01_bgm_02.mp3", 310, 2)
    rep = _sync(proj, "ep01")
    assert rep["superseded"] == ["MUS-0001"]                                            # MUS-0002 还被 ep02 用着,不标
    idx = ml.load_index(proj)
    assert ml.find_track(idx, "MUS-0002")["status"] == "active"
    assert [t["track_id"] for t in ml.search(idx)] == ["MUS-0002", "MUS-0003", "MUS-0004"]
    assert len(ml.search(idx, include_superseded=True)) == 4


def test_backfill_is_soft_until_sheet_rewritten(proj):
    _sync(proj, "ep01", backfill=True)
    _tone(proj / ml.BGM_REL / "ep02" / "ep02_bgm_01.mp3", 880, 2)
    cues = [_cue("ep02_bgm_01", "ep02_bgm_01.mp3", 0, 2)]
    _sheet(proj, "ep02", cues)
    _sync(proj, "ep02", backfill=True)
    assert ml.check_episode(proj, "ep02")["status"] == "WARN"
    _sheet(proj, "ep02", cues, notes="重写过")
    assert ml.check_episode(proj, "ep02")["status"] == "FAIL"


def test_missing_file_and_unknown_track(proj):
    _sync(proj, "ep01")
    _sheet(proj, "ep03", [_cue("ep03_bgm_01", "nope.mp3", 0, 2),
                          _cue("ep03_bgm_02", "also.mp3", 3, 5, license={"source": "library", "track_id": "MUS-0099"})])
    rep = _sync(proj, "ep03")
    assert rep["missing_files"] == [f"{ml.BGM_REL}/ep03/nope.mp3"] and rep["unknown_tracks"][0]["track_id"] == "MUS-0099"
    names = {i["name"]: i["status"] for i in ml.check_episode(proj, "ep03")["items"]}
    assert names["cue_files_exist"] == "FAIL" and names["library_track_id_valid"] == "FAIL"
