"""花字用途目录 / 策略(modules/caption_catalog.py)与宿主校验的回归测试(2026-09-24)。"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "modules"))
import caption_catalog as cc  # noqa: E402


def test_catalog_consistency():
    cat = cc.load_catalog()
    ids = [t["id"] for t in cat["types"]]
    assert len(ids) == len(set(ids))
    cats = {c["id"] for c in cat["categories"]}
    for t in cat["types"]:
        assert t["category"] in cats, t["id"]
        assert t["default_tier"] in cat["tiers"], t["id"]
    for v in cat["presets"].values():
        assert set(v["types"]) <= set(ids)
    # 历史 7 值仍在目录内(存量 captions.json 不失效)
    assert {"headline", "keyword", "location", "time", "skill", "faction", "other"} <= set(ids)


def test_normalize_and_validate():
    assert cc.normalize_policy({}) == {"mode": "auto", "types": []}
    assert cc.normalize_policy({"caption_mode": "manual", "caption_types": ["keyword", "x", "headline", "keyword"]}) \
        == {"mode": "manual", "types": ["headline", "keyword"]}
    assert cc.normalize_policy({"caption_mode": "auto", "caption_types": ["keyword"]})["types"] == []
    assert cc.validate_output({"caption_mode": "auto"}) == []
    assert cc.validate_output({"caption_mode": "manual", "caption_types": []})
    assert cc.validate_output({"caption_mode": "manual"})
    assert cc.validate_output({"caption_types": ["nope"]})
    assert cc.validate_output({"caption_types": "keyword"})
    assert cc.validate_output({"caption_mode": "manual", "caption_types": ["keyword"]}) == []


def test_tier_inference_and_ranges():
    assert cc.tier_of({"type": "headline"}) == "headline"
    assert cc.tier_of({"type": "location"}) == "label"
    assert cc.tier_of({"type": "location", "tier": "headline"}) == "headline"
    assert cc.tier_of({"type": "bogus"}) == "label"
    lo, hi = cc.tier_em_range("headline")
    assert lo < hi


def test_policy_issues_and_stamp():
    data = {"captions": [{"id": "a", "type": "location"}, {"id": "b", "type": "keyword"}]}
    assert cc.policy_issues(data, {"mode": "auto", "types": []}) == ([], [])
    allowed, fresh = cc.policy_issues(data, {"mode": "manual", "types": ["keyword"]})
    assert len(allowed) == 1 and "location" in allowed[0]
    assert fresh and "缺 caption_policy" in fresh[0]
    cc.stamp(data, {"mode": "manual", "types": ["keyword", "location"]})
    assert cc.policy_issues(data, {"mode": "manual", "types": ["location", "keyword"]}) == ([], [])
    _, fresh = cc.policy_issues(data, {"mode": "auto", "types": []})
    assert fresh and "过期" in fresh[0]


def test_coverage_issues(tmp_path):
    data = {"captions": [{"id": "a", "type": "location"}]}
    pol = {"mode": "manual", "types": ["location", "creature_card", "prop_card"]}
    assert cc.coverage_issues(data, {"mode": "auto", "types": []}) == []
    bad = cc.coverage_issues(data, pol)
    assert len(bad) == 2 and "creature_card" in bad[0] and "prop_card" in bad[1]
    data["type_skips"] = {"creature_card": "本集无生物出场", "prop_card": "项目暂无 prop_card 模版"}
    bad = cc.coverage_issues(data, pol)
    assert len(bad) == 1 and "缺模版不构成理由" in bad[0]
    # 本项目不可用的类型(缺 bible/props.json)不计
    (tmp_path / "bible" / "creatures").mkdir(parents=True)
    data["type_skips"] = {"creature_card": "本集无生物出场"}
    assert cc.coverage_issues(data, pol, tmp_path) == []
    data["type_skips"] = "x"
    assert "须为对象" in cc.coverage_issues(data, pol)[0]


def test_availability(tmp_path):
    av = {a["id"]: a for a in cc.availability(tmp_path)}
    assert not av["skill"]["available"] and "cultivation" in av["skill"]["reason"]
    assert av["location"]["available"]
    (tmp_path / "bible").mkdir()
    (tmp_path / "bible" / "cultivation.json").write_text("{}")
    assert {a["id"]: a for a in cc.availability(tmp_path)}["skill"]["available"]
    (tmp_path / "av").mkdir()
    (tmp_path / "av" / "beat_track.json").write_text("{}")
    av = {a["id"]: a for a in cc.availability(tmp_path)}
    assert not av["onomatopoeia"]["available"] and av["keyword"]["available"]


def test_captions_module_uses_catalog():
    import captions as cap
    assert cap.CAPTION_TYPES == cc.type_ids()


def test_host_validate_output_rejects_empty_manual():
    from services.runtime import core
    with pytest.raises(core.ServiceError):
        core._validate_output({**core.DEFAULT_GENCONFIG["output"], "caption_mode": "manual", "caption_types": []})
    core._validate_output({**core.DEFAULT_GENCONFIG["output"], "caption_mode": "manual", "caption_types": ["keyword"]})
    assert core.DEFAULT_GENCONFIG["output"]["caption_mode"] == "auto"
