# -*- coding: utf-8 -*-
"""正片解析口径(#79):finalize_episode.py 不带 --cut 与花字链路共用 timemap_layers.resolve_cut。
只建空文件 + 设 mtime,不依赖 ffmpeg。"""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "modules"))
sys.path.insert(0, str(ROOT / "code"))

import timemap_layers  # noqa: E402

EP = "ep01"


def _touch(p: Path, t: float):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    os.utime(p, (t, t))


def _ledger(ed: Path, cut: str):
    (ed / "final_layout.json").write_text(json.dumps({"cut_offset_s": 0.0, "segments": [
        {"name": "cut", "file": f"edit/{EP}/{cut}"}]}))


@pytest.fixture
def proj(tmp_path):
    (tmp_path / "edit" / EP).mkdir(parents=True)
    return tmp_path


def _ed(proj):
    return proj / "edit" / EP


def test_no_ledger_plain_latest_cut_v(proj):
    ed = _ed(proj)
    _touch(ed / "cut_v1.mp4", 1000)
    _touch(ed / "cut_v2.mp4", 1100)
    assert timemap_layers.resolve_cut(proj, EP).name == "cut_v2.mp4"


def test_no_cut_returns_none(proj):
    assert timemap_layers.resolve_cut(proj, EP) is None


def test_no_ledger_prefers_post(proj):
    ed = _ed(proj)
    _touch(ed / "cut_v2.mp4", 1000)
    _touch(ed / "cut_post.mp4", 1100)
    _touch(ed / "cut_post_v2.mp4", 1200)
    assert timemap_layers.resolve_cut(proj, EP).name == "cut_post_v2.mp4"


def test_build_cut_only_is_not_post_evidence(proj):
    """单跑 build-cut 只有 cut_post.mp4、timeline 无 post 节 → 不算后期已封装。"""
    ed = _ed(proj)
    _touch(ed / "cut_v2.mp4", 1000)
    _touch(ed / "cut_post.mp4", 1100)
    (ed / "timeline.json").write_text(json.dumps({"tracks": {"video": []}}))
    assert timemap_layers.resolve_cut(proj, EP).name == "cut_v2.mp4"
    (ed / "timeline.json").write_text(json.dumps({"tracks": {"video": []}, "post": {"versions": {}}}))
    assert timemap_layers.resolve_cut(proj, EP).name == "cut_post.mp4"


def test_correct_ledger_post_kept(proj):
    ed = _ed(proj)
    _touch(ed / "cut_v2.mp4", 1300)          # 比后期拼片新也不动:台账记的就是后期系
    _touch(ed / "cut_post_v2.mp4", 1200)
    _touch(ed / "final.mp4", 1400)
    _ledger(ed, "cut_post_v2.mp4")
    assert timemap_layers.resolve_cut(proj, EP).name == "cut_post_v2.mp4"


def test_corrupted_ledger_falls_back_to_post(proj):
    """不带 --cut 的 check 曾把台账回写成 cut_v2(#79 存量)→ 改取 cut_post_v2。"""
    ed = _ed(proj)
    _touch(ed / "cut_v2.mp4", 1000)
    _touch(ed / "cut_post.mp4", 1100)
    _touch(ed / "cut_post_v2.mp4", 1200)
    _touch(ed / "final.mp4", 1300)
    _ledger(ed, "cut_v2.mp4")
    assert timemap_layers.resolve_cut(proj, EP).name == "cut_post_v2.mp4"


def test_ledger_newer_than_post_kept(proj):
    """后期之后又回到常规流程重出正片并封装 → 台账 cut 比后期拼片新,信台账。"""
    ed = _ed(proj)
    _touch(ed / "cut_post_v2.mp4", 1000)
    _touch(ed / "cut_v3.mp4", 2000)
    _touch(ed / "final.mp4", 2100)
    _ledger(ed, "cut_v3.mp4")
    assert timemap_layers.resolve_cut(proj, EP).name == "cut_v3.mp4"


def test_transition_out_of_post_is_post_family(proj):
    ed = _ed(proj)
    _touch(ed / "cut_post.mp4", 1000)
    _touch(ed / "cut_v5.mp4", 900)
    _touch(ed / "cut_post_v2.mp4", 1200)
    (ed / "transitions_render.json").write_text(json.dumps({"src_cut": f"edit/{EP}/cut_post.mp4",
                                                            "out_cut": f"edit/{EP}/cut_v5.mp4"}))
    _ledger(ed, "cut_v5.mp4")
    assert timemap_layers.resolve_cut(proj, EP).name == "cut_v5.mp4"


def test_newer_cut_v_after_final_wins(proj):
    """常规流程:封装过 cut_v2 后重出了 cut_v3(尚未封装)→ assemble 默认取 cut_v3(与旧行为一致)。"""
    ed = _ed(proj)
    _touch(ed / "cut_v2.mp4", 1000)
    _touch(ed / "final.mp4", 1100)
    _touch(ed / "cut_v3.mp4", 1200)
    _ledger(ed, "cut_v2.mp4")
    assert timemap_layers.resolve_cut(proj, EP).name == "cut_v3.mp4"


def test_explicit_older_cut_ledger_kept(proj):
    """assemble 显式 --cut cut_v1 封装(final 比 cut_v2 新)→ 之后不带 --cut 仍认台账 cut_v1。"""
    ed = _ed(proj)
    _touch(ed / "cut_v1.mp4", 1000)
    _touch(ed / "cut_v2.mp4", 1100)
    _touch(ed / "final.mp4", 1200)
    _ledger(ed, "cut_v1.mp4")
    assert timemap_layers.resolve_cut(proj, EP).name == "cut_v1.mp4"


def test_finalize_find_cut_uses_resolve_cut(proj):
    import finalize_episode as fe
    ed = _ed(proj)
    _touch(ed / "cut_v2.mp4", 1000)
    _touch(ed / "cut_post_v2.mp4", 1200)
    _touch(ed / "final.mp4", 1300)
    _ledger(ed, "cut_v2.mp4")
    assert fe.find_cut(proj, EP).name == "cut_post_v2.mp4"
    assert fe.find_cut(proj, EP, "cut_v2.mp4").name == "cut_v2.mp4"


def test_finalize_find_cut_none_keeps_fail_text(proj):
    import finalize_episode as fe
    with pytest.raises(SystemExit) as ei:
        fe.find_cut(proj, EP)
    assert "没有 cut_v*.mp4" in str(ei.value)
