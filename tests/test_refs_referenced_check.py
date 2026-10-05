# -*- coding: utf-8 -*-
"""道具参考图挂法机检(code/refs_referenced_check.py 的 prop_ref_single / prop_ref_bound)。

2026-10-05 起道具只出道具图 main_01.png、不出尺寸对比图(比例锚图 scale_ref_01.png):
组 prompt refs 每个道具默认只挂道具图一张,尺寸靠正文 scale.prompt_token 文字。
存量组仍挂比例锚图的只提示不判违规。
"""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "code" / "refs_referenced_check.py"


@pytest.fixture()
def rrc(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "code"))
    spec = importlib.util.spec_from_file_location("rrc_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _group(tmp_path, refs, prompt):
    pf = tmp_path / "grp001.json"
    pf.write_text(json.dumps({"group_id": "grp001", "refs": refs, "video_prompt": prompt}, ensure_ascii=False))
    return pf


CHAR = "assets/concepts/characters/CHAR-1/sheet.png"
MAIN = "assets/concepts/props/PROP-1/main_01.png"
SCALE = "assets/concepts/props/PROP-1/scale_ref_01.png"


def test_prop_image_alone_passes_clean(rrc, tmp_path):
    pf = _group(tmp_path, [CHAR, MAIN], "阿青@Image 1:少年。Shot 1: 托盘@Image 2:约两掌宽的大托盘,须双手捧持。")
    errs, warns = rrc.check_group(pf, strict=True)
    assert errs == [] and warns == []


def test_legacy_scale_ref_only_warns(rrc, tmp_path):
    pf = _group(tmp_path, [CHAR, SCALE], "阿青@Image 1:少年。Shot 1: 托盘@Image 2:约两掌宽的大托盘。")
    errs, warns = rrc.check_group(pf, strict=True)
    assert errs == []
    assert len(warns) == 1 and "比例锚图" in warns[0] and "main_01.png" in warns[0]


def test_two_images_of_one_prop_warn(rrc, tmp_path):
    pf = _group(tmp_path, [MAIN, SCALE], "Shot 1: 托盘@Image 1:约两掌宽的大托盘。托盘@Image 2 同一件。")
    errs, warns = rrc.check_group(pf, strict=True)
    assert errs == []
    assert len(warns) == 1 and "2 张图" in warns[0]


def test_prop_image_without_binding_sentence(rrc, tmp_path):
    pf = _group(tmp_path, [MAIN], "Shot 1: 桌上放着托盘 [Image 1]。")
    errs, warns = rrc.check_group(pf, strict=False)
    assert errs == [] and any("绑定句" in w for w in warns)
    errs, _ = rrc.check_group(pf, strict=True)
    assert any("绑定句" in e for e in errs)


def test_unreferenced_prop_image_is_violation(rrc, tmp_path):
    pf = _group(tmp_path, [MAIN], "Shot 1: 桌上放着一只托盘。")
    errs, _ = rrc.check_group(pf, strict=False)
    assert len(errs) == 1 and "正文未引用" in errs[0]
