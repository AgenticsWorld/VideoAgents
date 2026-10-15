"""后期处理页分轨随播(2026-09-16):_post_audio_lanes 给旁白 / BGM / 音效条目带可播放 file URL、gain_db、fade。"""

import json
from pathlib import Path

import pytest

from services.runtime import core


def _w(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def proj(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(core, "DATA_DIR", data)
    base = data / "projects" / "demo"
    base.mkdir(parents=True)
    (base / "assets/audio/bgm/ep01").mkdir(parents=True)
    (base / "assets/audio/bgm/ep01/bgm_cue01.mp3").write_bytes(b"x")
    (base / "assets/audio/narration/ep01").mkdir(parents=True)
    (base / "assets/audio/narration/ep01/ep01_nar_01.wav").write_bytes(b"x")
    (base / "assets/audio/sfx/ep01/patches").mkdir(parents=True)
    (base / "assets/audio/sfx/ep01/patches/sfx-001_door.wav").write_bytes(b"x")
    (data / "sfx/impact").mkdir(parents=True)
    (data / "sfx/impact/thud.wav").write_bytes(b"x")
    return base


GROUPS = [{"group_id": "grp001", "cum_start_s": 0.0, "duration": 10.0, "dialogue": []},
          {"group_id": "grp002", "cum_start_s": 10.0, "duration": 8.0, "dialogue": [{"t0": 1.0, "t1": 2.5, "speakers": ["A"]}]}]


def test_lanes_bgm_music_cues_with_root_relative_file_and_fade(proj):
    _w(proj / "assets/audio/bgm/ep01/music_cues.json", {"cues": [
        {"cue_id": "c1", "file": "assets/audio/bgm/ep01/bgm_cue01.mp3", "in_s": 3, "out_s": 9, "fade": {"in_s": 1.5, "out_s": 2}, "gain_db": -6},
        {"cue_id": "c2", "file": "missing.mp3", "in_s": 12, "out_s": 15, "fade": {"in": 0.5, "out": 1}},
        {"cue_id": "c3", "file": "bgm_cue01.mp3", "in_s": 0, "out_s": 1, "status": "dropped"}]})
    lanes = core._post_audio_lanes(proj, "ep01", GROUPS, {"rows": []})
    bgm = lanes["bgm"]
    assert [b["label"].split()[0] for b in bgm] == ["c1", "c2"]
    assert [b["cue_id"] for b in bgm] == ["c1", "c2"] and bgm[0]["sheet"] == "music_cues.json"
    assert bgm[0]["file"].startswith("/projects/demo/assets/audio/bgm/ep01/bgm_cue01.mp3?v=")
    assert (bgm[0]["t0"], bgm[0]["t1"], bgm[0]["gain_db"], bgm[0]["fade_in"], bgm[0]["fade_out"]) == (3.0, 9.0, -6.0, 1.5, 2.0)
    assert bgm[1]["file"] is None and (bgm[1]["fade_in"], bgm[1]["fade_out"]) == (0.5, 1.0)


def test_lanes_bgm_cue_sheet_filename_form(proj):
    _w(proj / "assets/audio/bgm/ep01/cue_sheet.json", {"cues": [{"cue_id": "c1", "file": "bgm_cue01.mp3", "in_s": 0, "out_s": 5}]})
    lanes = core._post_audio_lanes(proj, "ep01", GROUPS, {"rows": []})
    assert lanes["bgm"][0]["file"].startswith("/projects/demo/assets/audio/bgm/ep01/bgm_cue01.mp3?v=")
    assert lanes["bgm"][0]["gain_db"] == 0.0 and lanes["bgm"][0]["sheet"] == "cue_sheet.json"


def test_lanes_narration_anchor_group_and_file(proj):
    _w(proj / "assets/audio/narration/ep01/manifest.json", {"segments": [
        {"num": "N-01", "anchor": {"group": "grp002", "offset_s": 0.5}, "duration_s": 4.2, "file": "ep01_nar_01.wav"},
        {"num": "N-02", "anchor": {"group": "grp999"}, "duration_s": 1, "file": "ep01_nar_01.wav"},
        {"num": "N-03", "anchor": {"group": "grp001"}, "duration_s": 2, "file": "nope.wav"}]})
    lanes = core._post_audio_lanes(proj, "ep01", GROUPS, {"rows": []})
    n = lanes["narration"]
    assert [x["label"] for x in n] == ["N-01", "N-03"]
    assert (n[0]["t0"], n[0]["t1"]) == (10.5, 14.7)
    assert n[0]["file"].startswith("/projects/demo/assets/audio/narration/ep01/ep01_nar_01.wav?v=")
    assert n[1]["file"] is None
    assert lanes["dialogue"] == [{"group_id": "grp002", "shot_id": "", "t0": 11.0, "t1": 12.5, "label": "A"}]
    assert (n[0]["num"], n[0]["seg_id"]) == ("N-01", "")


def test_lanes_sfx_project_patch_library_skip_and_missing(proj):
    rows = [{"id": "sfx-001", "group_id": "grp001", "t": 0.5, "t_abs": 0.5, "event": "door", "source": "generate",
             "file": "assets/audio/sfx/ep01/patches/sfx-001_door.wav", "gain_db": -9},
            {"id": "sfx-002", "group_id": "grp001", "t": 1, "t_abs": 1, "event": "thud", "source": "library", "file": "impact/thud.wav"},
            {"id": "sfx-003", "group_id": "grp001", "t": 2, "t_abs": 2, "event": "x", "source": "skip", "file": "impact/thud.wav"},
            {"id": "sfx-004", "group_id": "grp001", "t": 3, "t_abs": 3, "event": "y", "source": "library", "file": "../../secret.wav"},
            {"id": "sfx-005", "group_id": "grp001", "t": 4, "t_abs": 4, "event": "z", "source": "", "file": ""}]
    lanes = core._post_audio_lanes(proj, "ep01", GROUPS, {"rows": rows})
    s = {x["id"]: x for x in lanes["sfx"]}
    assert s["sfx-001"]["file"].startswith("/projects/demo/assets/audio/sfx/ep01/patches/sfx-001_door.wav?v=")
    assert s["sfx-001"]["gain_db"] == -9.0 and s["sfx-001"]["resolved"]
    assert s["sfx-002"]["file"].startswith("/api/v1/sfx-library/impact/thud.wav?v=")
    assert s["sfx-003"]["file"] is None and s["sfx-003"]["resolved"]
    assert s["sfx-004"]["file"] is None
    assert s["sfx-005"]["file"] is None and not s["sfx-005"]["resolved"] and s["sfx-005"]["gain_db"] == -12.0


def test_sfx_library_route_rejects_escape_and_non_audio(proj):
    from fastapi.testclient import TestClient
    from services.api import app as api_app

    (core.DATA_DIR / "sfx/notes.txt").write_text("x")
    c = TestClient(api_app.app)
    assert c.get("/api/v1/sfx-library/impact/thud.wav").status_code == 200
    assert c.get("/api/v1/sfx-library/notes.txt").status_code == 404
    assert c.get("/api/v1/sfx-library/nope.wav").status_code == 404
    assert c.get("/api/v1/sfx-library/..%2F..%2Fx.wav").status_code in (400, 404)


def test_revision_kinds_for_post_lanes_are_mapped():
    assert core.REVISION_KIND_AGENTS["dialogue"] and core.REVISION_KIND_AGENTS["sfx"]
    for kind in ("dialogue", "sfx", "bgm", "narration_audio"):
        for aid in core.REVISION_KIND_AGENTS[kind]:
            assert (core.ROOT / "agents" / aid / "SOUL.md").is_file(), aid
