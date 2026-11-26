"""Seedance 2.5 Flash(2026-11-26 接入,火山 doubao-seedance-2-5-flash-261115 / BytePlus dreamina-seedance-2-5-flash-261115)。

官方教程 docs/ark/seedance-2-5:同属 2.5 系列——时长 [4,30] 或 -1、参考音 / 视频总时长各 ≤30s、仅 480p/720p、
提示词写法同 2.5;差别是参考素材上限 10 图 / 5 视频 / 5 音频(2.5 为 30 / 10 / 10),且不支持样片(Draft)模式。
各处按「含 seedance-2-5」判 2.5 的地方都要单独收窄,否则 Flash 会被放行到 2.5 的上限、提交即被方舟拒收。
"""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import modules.genmedia as g  # noqa: E402
from modules import continuity_refs, shot_timing, whitebox_refs  # noqa: E402
from services.runtime import core  # noqa: E402

FLASH = ("doubao-seedance-2-5-flash-261115", "dreamina-seedance-2-5-flash-261115")
V25 = "doubao-seedance-2-5-260628"


def _precheck(model, refs=(), audio_refs=(), video_refs=(), resolution="720p", aspect="16:9"):
    return g._seedance_precheck(model, "a girl walks", None, None, 10, resolution, aspect,
                                list(refs), list(audio_refs), list(video_refs))


@pytest.mark.parametrize("model", FLASH)
def test_flash_is_25_series_with_narrower_ref_caps(model):
    assert g._seedance_gen(model) == 2.5 and g._is_seedance25_flash(model)
    assert g._seedance_ref_caps(model) == (10, 5, 5, "Seedance 2.5 Flash")
    assert not g._is_seedance25_flash(V25) and g._seedance_ref_caps(V25)[:3] == (30, 10, 10)
    assert g._seedance_ref_caps("doubao-seedance-2-0-260128")[:3] == (9, 3, 3)


@pytest.mark.parametrize("model", FLASH)
def test_flash_precheck_enforces_10_images_5_videos_5_audios(model):
    _precheck(model, refs=[f"r{i}.png" for i in range(10)])
    with pytest.raises(RuntimeError, match=r"参考图最多 10 张\(Seedance 2.5 Flash\)"):
        _precheck(model, refs=[f"r{i}.png" for i in range(11)])
    with pytest.raises(RuntimeError, match=r"参考音频最多 5 段\(Seedance 2.5 Flash\)"):
        _precheck(model, refs=["a.png"], audio_refs=[f"a{i}.mp3" for i in range(6)])
    with pytest.raises(RuntimeError, match=r"参考视频最多 5 个\(Seedance 2.5 Flash\)"):
        _precheck(model, video_refs=[f"v{i}.mp4" for i in range(6)])
    _precheck(V25, refs=[f"r{i}.png" for i in range(30)])          # 2.5 本体上限不受影响


def test_flash_keeps_25_resolution_and_ratio_rules(capsys):
    assert _precheck(FLASH[0], resolution="1080p") == ("720p", "16:9")
    assert "已改为 720p" in capsys.readouterr().err
    assert g._seedance_precheck(FLASH[0], "p", None, None, 5, "4k", "16:9", [], [], [], allow_hd=True)[0] == "720p"
    res, aspect = g._seedance_precheck(FLASH[0], "p", "first.png", None, 5, "720p", "16:9", [], [], [])
    assert aspect == "adaptive"                                        # 首帧任务 ratio 仅 adaptive,同 2.5


@pytest.fixture
def draft_upscale(tmp_path, monkeypatch):
    cfg_path = tmp_path / "genconfig.json"
    cfg_path.write_text(json.dumps({"upscale": {"provider": "volcengine"},
                                    "video": {"provider": "volcengine",
                                              "volcengine": {"api_key": "k", "model": FLASH[0]}}}), encoding="utf-8")
    monkeypatch.setattr(g, "CONFIG_PATH", cfg_path)
    monkeypatch.delenv("VIDEOAGENTS_PROJECT", raising=False)


@pytest.mark.parametrize("model", FLASH)
def test_flash_never_uses_draft_mode(draft_upscale, model, capsys):
    provider = "volcengine" if model.startswith("doubao") else "byteplus"
    assert g._draft_mode_active({"provider": provider, "model": model}, "480p") is False
    assert "Seedance 2.5 Flash 不支持样片模式" in capsys.readouterr().err
    assert g._draft_mode_active({"provider": "volcengine", "model": V25}, "480p") is True
    with pytest.raises(RuntimeError, match="Flash 不支持"):
        g._ark_video_body({"provider": provider, "model": model, "api_key": "k"}, "a girl", "", "", 5, "480p",
                          "16:9", None, [], [], None, False, draft=True, to_url=lambda p: f"file://{p}")


@pytest.mark.parametrize("model", FLASH)
def test_core_caps_skill_and_family(model):
    assert core.video_model_caps(model) == {"max_ref_images": 10, "max_ref_videos": 5, "max_ref_audios": 5,
                                            "max_group_s": 30, "max_ref_video_s": 30}
    assert core.video_model_caps(V25)["max_ref_images"] == 30
    assert core.is_seedance25(model) and core.is_seedance25_flash(model) and not core.is_seedance20(model)
    assert core.auto_prompt_skill_for_model(model) == core.PROMPT_SKILL_SD25   # 提示词写法同 2.5(sd25-pe)
    assert core.video_family_for(model) == "sd25f"
    assert shot_timing.model_kind(model) == shot_timing.KIND_SD25


@pytest.mark.parametrize("model, provider", [(FLASH[0], "volcengine"), (FLASH[1], "byteplus")])
def test_reference_video_caps_for_whitebox_and_continuity(model, provider):
    assert whitebox_refs._model_caps(model) == {"max_ref_videos": 5, "max_total_s": 30}
    assert continuity_refs.video_caps(model, provider) == (5, 30.)
    assert whitebox_refs._model_caps(V25) == {"max_ref_videos": 10, "max_total_s": 30}
    assert continuity_refs.video_caps(V25, "volcengine") == (10, 30.)


def test_shot_group_preset_matches_core_caps():
    """「模型限制」预设 sd25f:前端 SG_PRESETS 数值与 core.video_model_caps 一致,服务端校验认这个 key。"""
    html = (ROOT / "apps/web/static/index.html").read_text(encoding="utf-8")
    m = re.search(r"\n  sd25f:\{max_group_s:(\d+),max_ref_images:(\d+),max_ref_videos:(\d+),max_ref_audios:(\d+)\}", html)
    caps = core.video_model_caps(FLASH[0])
    assert m and tuple(map(int, m.groups())) == (caps["max_group_s"], caps["max_ref_images"],
                                                 caps["max_ref_videos"], caps["max_ref_audios"])
    assert html.count('data-preset="sd25f">Seedance 2.5 Flash</button>') == 2          # 设置弹窗 + 新建向导
    assert "sd25f:'Seedance 2.5 Flash'" in html
    assert "sd25f" in core.SHOT_GROUP_PRESETS
    core._validate_shot_group({"max_group_s": 30, "max_ref_images": 10, "max_ref_videos": 5, "max_ref_audios": 5,
                               "preset": "sd25f"})
