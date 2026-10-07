# -*- coding: utf-8 -*-
"""项目音乐库(music_library,2026-09-27):入库去重、跨集复用的使用记录、返工换曲标 superseded、机检 music_library_synced;
2026-10-07 主题表 / 变奏 / 复用配额(只复用主题曲、≤40% 原样复用、≥30% 新生成或变奏、同曲同集 1 次、一曲 3 集、60% 时长、强度 0.25)。
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
    r = subprocess.run(CLI + ["use", "--out-root", str(proj), "--ep", "ep02", "--track", "MUS-0001", "--output", "ep02_bgm_01.mp3", "--json"],
                       capture_output=True, text=True)
    assert r.returncode == 2 and not (proj / ml.BGM_REL / "ep02" / "ep02_bgm_01.mp3").exists()   # 没挂主题:use 拒绝,不复制
    subprocess.run(CLI + ["theme", "--out-root", str(proj), "--add", "T-gate", "--name", "山门", "--motif", "low strings motif"], check=True, capture_output=True)
    subprocess.run(CLI + ["annotate", "--out-root", str(proj), "--track", "MUS-0001", "--theme", "T-gate"], check=True, capture_output=True)
    assert ml.find_track(ml.load_index(proj), "MUS-0001")["theme_id"] == "T-GATE"
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
    rep_ok = {"consulted": True, "generated": [{"cue_id": "ep02_bgm_02", "reason": "库内无市井段"}]}
    _sheet(proj, "ep02", cues, music_library_report=rep_ok)
    names = {i["name"]: i["status"] for i in ml.check_episode(proj, "ep02")["items"]}
    assert names["library_report"] == "PASS" and names["library_reuse_reason"] == "FAIL"   # 复用也要写 reason
    assert names["library_reuse_theme_only"] == "PASS"
    assert names["library_reuse_quota"] == "PASS" and names["library_fresh_quota"] == "PASS"   # 2 条:复用 1 + 新 1 在配额内
    rep_ok["reused"] = [{"cue_id": "ep02_bgm_01", "track_id": "MUS-0001", "reason": "山门主题再现"}]
    _sheet(proj, "ep02", cues, music_library_report=rep_ok)
    assert ml.check_episode(proj, "ep02")["status"] == "PASS"
    idx = ml.load_index(proj)
    ml.find_track(idx, "MUS-0001")["theme_id"] = ""                                    # 摘掉主题 → 一次性 cue 不跨集
    ml.save_index(proj, idx)
    assert {i["name"]: i["status"] for i in ml.check_episode(proj, "ep02")["items"]}["library_reuse_theme_only"] == "FAIL"
    assert ml.check_episode(proj, "ep01")["status"] == "PASS"                          # ep01 先于 ep02 曲目入库,不被追溯


def _theme(proj, tid, *tracks):
    idx = ml.load_index(proj)
    ml.add_theme(idx, tid, {"name": tid, "motif": "motif"})
    for t in tracks:
        ml.find_track(idx, t)["theme_id"] = tid.upper()
    ml.save_index(proj, idx)


def _reuse_cue(proj, ep, cue_id, tid, a, b, **kw):
    """原样复用库内曲目的 cue:复制库内文件到本集目录(等价 use 命令)。"""
    idx = ml.load_index(proj)
    t = ml.find_track(idx, tid)
    d = proj / ml.BGM_REL / ep
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ml.lib_dir(proj) / t["file"], d / f"{cue_id}.mp3")
    return _cue(cue_id, f"{cue_id}.mp3", a, b, license=ml.license_block(t), **kw)


def _report(cues, kinds):
    """按取法给每条 cue 补 reason(kinds: cue_id → reuse|variation|fresh)。"""
    rep = {"consulted": True, "reused": [], "variations": [], "generated": []}
    for c in cues:
        k = kinds.get(c["cue_id"], "fresh")
        if k == "reuse":
            rep["reused"].append({"cue_id": c["cue_id"], "track_id": c["license"]["track_id"], "reason": "主题再现"})
        elif k == "variation":
            rep["variations"].append({"cue_id": c["cue_id"], "variant_of": c["variant_of"], "reason": "同动机换配器"})
        else:
            rep["generated"].append({"cue_id": c["cue_id"], "reason": "库内无"})
    return rep


def test_reuse_quota_theme_only_and_fit_rules(proj):
    _sync(proj, "ep01")
    _theme(proj, "T-a", "MUS-0001", "MUS-0002")
    # 5 条:复用 3(上限 2)、其中 MUS-0001 两次、一条只用 1s / 3s、一条强度硬凑;新生成 1 + 变奏 1
    cues = [_reuse_cue(proj, "ep02", "ep02_bgm_01", "MUS-0001", 0, 3, mood="紧张,强度 0.9"),
            _reuse_cue(proj, "ep02", "ep02_bgm_02", "MUS-0001", 10, 11, mood="紧张"),
            _reuse_cue(proj, "ep02", "ep02_bgm_03", "MUS-0002", 20, 22, mood="轻快"),
            _cue("ep02_bgm_04", "ep02_bgm_04.mp3", 30, 32, mood="市井"),
            _cue("ep02_bgm_05", "ep02_bgm_05.mp3", 40, 42, mood="紧张", variant_of="MUS-0001")]
    _tone(proj / ml.BGM_REL / "ep02" / "ep02_bgm_04.mp3", 900, 2)
    _tone(proj / ml.BGM_REL / "ep02" / "ep02_bgm_05.mp3", 950, 2)
    idx = ml.load_index(proj)
    ml.find_track(idx, "MUS-0001")["mood"] = "紧张,强度 0.2→0.4"
    ml.save_index(proj, idx)
    kinds = {"ep02_bgm_01": "reuse", "ep02_bgm_02": "reuse", "ep02_bgm_03": "reuse", "ep02_bgm_05": "variation"}
    _sheet(proj, "ep02", cues, music_library_report=_report(cues, kinds))
    rep = _sync(proj, "ep02")
    assert rep["variations"] == [{"cue_id": "ep02_bgm_05", "track_id": "MUS-0004", "variant_of": "MUS-0001"}]
    v = ml.find_track(ml.load_index(proj), "MUS-0004")
    assert v["variant_of"] == "MUS-0001" and v["theme_id"] == "T-A"                    # 变奏继承基曲主题
    names = {i["name"]: i["status"] for i in ml.check_episode(proj, "ep02")["items"]}
    assert names["library_reuse_reason"] == "PASS" and names["library_variation_reason"] == "PASS"
    assert names["library_reuse_theme_only"] == "PASS"
    assert names["library_reuse_quota"] == "FAIL" and names["library_fresh_quota"] == "PASS"   # 3/5 复用 > 2;新 2/5 ≥ 2
    assert names["library_track_once_per_episode"] == "FAIL"
    assert names["library_reuse_cover"] == "FAIL" and names["library_reuse_intensity"] == "FAIL"
    # 只留一条复用、强度改对:复用 1/3(上限 1)、新 2/3 → 全 PASS
    cues2 = [dict(cues[0], mood="紧张,强度 0.5"), cues[3], cues[4]]
    _sheet(proj, "ep02", cues2, music_library_report=_report(cues2, kinds))
    _sync(proj, "ep02")
    res = ml.check_episode(proj, "ep02")
    assert res["status"] == "PASS", [i for i in res["items"] if i["status"] != "PASS"]


def test_track_reuse_span_and_use_gate(proj):
    _sync(proj, "ep01")
    _theme(proj, "T-a", "MUS-0001")
    for i, ep in enumerate(("ep02", "ep03", "ep04")):
        _tone(proj / ml.BGM_REL / ep / f"{ep}_bgm_02.mp3", 700 + i * 50, 2)       # 频率各异:同内容会被按指纹认成复用
        cues = [_reuse_cue(proj, ep, f"{ep}_bgm_01", "MUS-0001", 0, 3), _cue(f"{ep}_bgm_02", f"{ep}_bgm_02.mp3", 5, 7)]
        _sheet(proj, ep, cues, music_library_report=_report(cues, {f"{ep}_bgm_01": "reuse"}))
        _sync(proj, ep)
        assert ml.check_episode(proj, ep)["status"] == "PASS"
    assert ml.reuse_episodes(ml.find_track(ml.load_index(proj), "MUS-0001")) == ["ep02", "ep03", "ep04"]
    r = subprocess.run(CLI + ["use", "--out-root", str(proj), "--ep", "ep05", "--track", "MUS-0001"], capture_output=True, text=True)
    assert r.returncode == 2 and "复用满" in r.stdout                                   # 第 4 集:use 直接拒绝
    r = subprocess.run(CLI + ["use", "--out-root", str(proj), "--ep", "ep05", "--track", "MUS-0002"], capture_output=True, text=True)
    assert r.returncode == 2 and "一次性" in r.stdout                                   # 没挂主题也拒绝
    r = subprocess.run(CLI + ["use", "--out-root", str(proj), "--ep", "ep03", "--track", "MUS-0001", "--need-duration", "1"], capture_output=True, text=True)
    assert r.returncode == 2 and "60%" in r.stdout                                      # 配乐点太短
    _tone(proj / ml.BGM_REL / "ep05" / "ep05_bgm_02.mp3", 990, 2)
    cues = [_reuse_cue(proj, "ep05", "ep05_bgm_01", "MUS-0001", 0, 3), _cue("ep05_bgm_02", "ep05_bgm_02.mp3", 5, 7)]
    _sheet(proj, "ep05", cues, music_library_report=_report(cues, {"ep05_bgm_01": "reuse"}))
    _sync(proj, "ep05")
    names = {i["name"]: i["status"] for i in ml.check_episode(proj, "ep05")["items"]}
    assert names["library_track_reuse_span"] == "FAIL"
    assert ml.check_episode(proj, "ep04")["status"] == "PASS"                          # 第 3 集仍合规,不被追溯


def test_variant_and_unknown_theme(proj):
    _sync(proj, "ep01")
    _tone(proj / ml.BGM_REL / "ep02" / "ep02_bgm_01.mp3", 700, 2)
    cues = [_cue("ep02_bgm_01", "ep02_bgm_01.mp3", 0, 2, variant_of="MUS-0099", theme_id="T-nope")]
    _sheet(proj, "ep02", cues, music_library_report=_report(cues, {"ep02_bgm_01": "variation"}))
    rep = _sync(proj, "ep02")
    assert rep["unknown_variants"][0]["variant_of"] == "MUS-0099" and rep["unknown_themes"][0]["theme_id"] == "T-NOPE"
    names = {i["name"]: i["status"] for i in ml.check_episode(proj, "ep02")["items"]}
    assert names["library_variant_valid"] == "FAIL" and names["library_theme_valid"] == "FAIL"
    out = subprocess.run(CLI + ["variant", "--out-root", str(proj), "--track", "MUS-0001", "--json"], capture_output=True, text=True, check=True)
    assert json.loads(out.stdout)["cue_fields"] == {"variant_of": "MUS-0001", "theme_id": ""}


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
