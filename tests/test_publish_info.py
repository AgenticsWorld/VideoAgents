"""成片发布页「发布相关」聚合:publish/<ep>/ 集级布局与 publish/ 项目级布局(12-publishing SOUL 口径)都要读出来。"""
import json
from pathlib import Path

from services.runtime import core


def _w(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def test_project_level_layout(tmp_path):
    base = tmp_path / "proj"
    _w(base / "publish" / "metadata.json", {
        "_meta": {"generated_at": "2026-09-25", "season_episode_count": 64},
        "episodes": [{"ep": "ep05", "episode_title": "别集"},
                     {"ep": "ep06", "series": "朝歌传", "episode_no": 6, "episode_title": "九湾河闹海",
                      "thumbnail": "edit/ep06/thumbnail_B.png",
                      "platform_fields": {"bilibili": {"分辨率": "1920x1080", "帧率": 24, "时长_秒": 318.8,
                                                       "parts": [{"video": "publish/bilibili/package/ep06/a.mp4"}]}}}]})
    _w(base / "publish" / "seo.json", {
        "items": [{"ep": "ep05", "platform": "bilibili", "titles": ["x"], "picked": None},
                  {"ep": "ep06", "platform": "bilibili", "titles": ["悬念", "冲突", "关键词"],
                   "title_angles": ["悬念型", "冲突型", "关键词检索型"], "tags": ["哪吒"],
                   "description": "简介", "picked": "关键词", "picked_index": 2}]})
    pkg = base / "publish" / "bilibili" / "package" / "ep06"
    pkg.mkdir(parents=True)
    (pkg / "a.mp4").write_bytes(b"0" * 10)
    (pkg / "cover.jpg").write_bytes(b"0")
    _w(pkg / "parts.json", {"lint": {"resolution": "pass", "bitrate": "fail: 超码率"}})

    info = core._ep_publish_info(base, "ep06")
    assert info["source"] == "publish/"
    m = info["metadata"]
    assert m["episode_title"] == "九湾河闹海" and m["series"] == "朝歌传"
    assert m["episode_total"] == 64 and m["resolution"] == "1920x1080" and m["fps"] == 24
    assert m["date"] == "2026-09-25" and m["thumbnail"] == "edit/ep06/thumbnail_B.png"
    s = info["seo"]
    assert [t["text"] for t in s["titles"]] == ["悬念", "冲突", "关键词"]
    assert s["titles"][2]["orientation"] == "关键词检索型" and s["picked"] == "t3"
    assert s["platform"] == "bilibili" and s["tags"] == ["哪吒"]
    assert [p["platform"] for p in info["platforms"]] == ["bilibili"]
    p = info["platforms"][0]
    assert {f["name"] for f in p["files"]} == {"a.mp4", "cover.jpg", "parts.json"}
    assert p["lint_total"] == 2 and p["lint_flagged"] == {"bitrate": "fail: 超码率"}
    # 别集没有物料 → None;无 publish/ 目录 → None
    assert core._ep_publish_info(base, "ep07") is None
    assert core._ep_publish_info(tmp_path / "nothing", "ep06") is None


def test_episode_level_layout_still_works(tmp_path):
    base = tmp_path / "proj"
    _w(base / "publish" / "ep01" / "metadata.json", {
        "date": "2026-09-01", "series": {"display_name": "聊斋", "note": "x"}, "episode_no": 1,
        "episode_total": 32, "episode_title": "夜", "video": {"resolution": "1080p", "fps": 24,
                                                          "duration_s_measured": 300.2, "h5_gate_verdict": "PASS"},
        "next_episode": {"ep": "ep02", "title": "画皮"}})
    _w(base / "publish" / "ep01" / "seo.json", {
        "meta": {"platform": "youtube"},
        "items": [{"titles": [{"id": "T1", "text": "A", "orientation": "悬念型"}, {"id": "T2", "text": "B"}],
                   "picked": {"title_id": "T2"}, "tags": [], "description": ""}]})
    (base / "publish" / "ep01" / "package").mkdir()          # 非平台目录不当平台
    _w(base / "publish" / "ep01" / "youtube" / "spec_report.json",
       {"overall_verdict": "PASS", "uploaded": False, "lint_summary": {"fps": "pass (24)", "dur": "fail"}})
    (base / "publish" / "ep01" / "youtube" / "package").mkdir()
    (base / "publish" / "ep01" / "youtube" / "package" / "f.mp4").write_bytes(b"0")
    # 项目级平台目录也认(liaozhai2 口径:publish/bilibili/package/ 下直接放文件、无分集子目录)
    (base / "publish" / "bilibili" / "package").mkdir(parents=True)
    (base / "publish" / "bilibili" / "package" / "b.mp4").write_bytes(b"0")

    info = core._ep_publish_info(base, "ep01")
    assert info["source"] == "publish/ep01/"
    m = info["metadata"]
    assert m["series"] == "聊斋" and m["gate_verdict"] == "PASS" and m["next_episode"] == "ep02 画皮"
    assert m["duration_s"] == 300.2
    s = info["seo"]
    assert s["platform"] == "youtube" and s["picked"] == "T2"
    assert s["titles"][1]["orientation"] == ""
    names = [p["platform"] for p in info["platforms"]]
    assert names == ["youtube", "bilibili"]
    yt = info["platforms"][0]
    assert yt["verdict"] == "PASS" and yt["uploaded"] is False
    assert yt["lint_total"] == 2 and yt["lint_flagged"] == {"dur": "fail"}
