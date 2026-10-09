"""新建向导「模型限制」默认预设:按生效视频模型的 id / 名称 / 工作流名关键字归入 sd25|sd20|mmh3|wan30。

模型名取自各渠道实际目录(火山/BytePlus/Fal/MiniMax/OpenRouter/RH 模型 API/ComfyUI 工作流);
不命中的模型由前端保留默认 MiniMax H3 并提示核对。
"""
import asyncio
import json

import pytest

from services.runtime import core


@pytest.mark.parametrize("names, family", [
    # MiniMax H3:同时含 minimax 与 h3,或 OpenRouter 的 hailuo-3(不含 h3 字样)
    (["minimax/h3-max"], "mmh3"),
    (["minimax/h3-max-turbo"], "mmh3"),
    (["minimax/h3-max/lip-sync/image-to-video"], "mmh3"),
    (["MiniMax-H3"], "mmh3"),
    (["minimax/hailuo-3"], "mmh3"),
    (["minimax/hailuo-3-max"], "mmh3"),
    (["minimax/hailuo-h3/multimodal-to-video"], "mmh3"),
    (["rhart-video/minimax-h3-oss/fl2va-advanced"], "mmh3"),
    (["rhart-video/minimax-h3-rh-enhanced/ref2va"], "mmh3"),
    (["comfy/video-minimax-h3-ref2va-api.json"], "mmh3"),
    (['{"1": {"class_type": "MiniMaxH3ReferenceToVideo"}}'], "mmh3"),
    # Seedance 2.5
    (["doubao-seedance-2-5-260628"], "sd25"),
    (["dreamina-seedance-2-5-260628"], "sd25"),
    (["bytedance/seedance-2.5"], "sd25"),
    (["bytedance/seedance-2.5-global-token/multimodal-video"], "sd25"),
    (["video-a", "seedance2.5/多模态视频 Token"], "sd25"),            # 代号不含模型名,靠名称
    # Seedance 2.0 系列(含 fast/mini/global 与 RH 别名 sparkvideo)
    (["doubao-seedance-2-0-260128"], "sd20"),
    (["dreamina-seedance-2-0-fast-260128"], "sd20"),
    (["doubao-seedance-2-0-mini-260615"], "sd20"),
    (["bytedance/seedance-2.0-mini"], "sd20"),
    (["bytedance/seedance-2.0-global-fast/text-to-video"], "sd20"),
    (["rhart-video/sparkvideo-2.0/multimodal-video"], "sd20"),
    (["rhart-video/sparkvideo-2.0-fast/multimodal-video"], "sd20"),
    # Wan 3.0
    (["alibaba/wan-3.0"], "wan30"),
    (["alibaba/wan-3.0-prime/reference-to-video"], "wan30"),
    # 不命中
    (["doubao-seedance-1-5-pro-251215"], ""),
    (["seedance-1-0-pro-fast-251015"], ""),
    (["seedance-v1.5-pro/image-to-video"], ""),
    (["minimax/hailuo-2.3"], ""),
    (["minimax/hailuo-02/i2v-pro"], ""),
    (["alibaba/wan-2.7"], ""),
    (["fal-ai/kling-video/v3/pro"], ""),
    (["google/veo-3.1"], ""),
    (["FastVideo/FastVideo-FastH3-4-step-Preview-v1-VSA-DataFree"], ""),
    (["seedvr2-scale"], ""),
    ([""], ""),
])
def test_video_family_for(names, family):
    assert core.video_family_for(*names) == family


def test_every_catalog_model_is_classified_as_its_label_says():
    """目录里每个模型按 id 判出的口径与其中文标签一致(防目录新增写法漏判/误判)。"""
    expect = {"Seedance 2.5": "sd25", "Seedance 2.0": "sd20", "MiniMax H3": "mmh3", "Wan 3.0": "wan30"}
    for prov, rows in core.VIDEO_MODEL_CATALOG.items():
        for mid, label in rows:
            want = next((f for k, f in expect.items() if label.startswith(k)), "")
            if prov == "comfyui":
                want = ""                                                   # FastH3 非 MiniMax 官方 H3 预设
            assert core.video_family_for(mid) == want, (prov, mid, label)


@pytest.fixture
def cfg_file(tmp_path, monkeypatch):
    path = tmp_path / "genconfig.json"
    monkeypatch.setattr(core, "GENCONFIG_PATH", path)
    monkeypatch.delenv("VIDEOAGENTS_USER_JWT", raising=False)
    return path


def _video(cfg_file, provider, **pc):
    cfg_file.write_text(json.dumps({"video": {"provider": provider, provider: pc}}))
    return asyncio.run(core.api_video_model_get())


def test_api_reports_family_per_provider(cfg_file):
    assert _video(cfg_file, "volcengine", model="doubao-seedance-2-5-260628")["family"] == "sd25"
    assert _video(cfg_file, "byteplus", model="dreamina-seedance-2-0-fast-260128")["family"] == "sd20"
    assert _video(cfg_file, "openrouter", model="minimax/hailuo-3")["family"] == "mmh3"
    assert _video(cfg_file, "rhapi", model="rhart-video/sparkvideo-2.0/multimodal-video")["family"] == "sd20"
    assert _video(cfg_file, "fal", model="minimax/h3", custom_model="alibaba/wan-3.0")["family"] == "wan30"
    r = _video(cfg_file, "openrouter", model="google/veo-3.1")
    assert r["family"] == "" and r["model"] == "google/veo-3.1"


def test_api_agentics_uses_profile_name_and_catalog_default(cfg_file, monkeypatch):
    rows = [{"id": "video-a", "label": "Seedance 2.0 Fast"}, {"id": "video-b", "label": "MiniMax H3"}]
    monkeypatch.setattr(core, "agentics_video_profiles", lambda: rows)
    r = _video(cfg_file, "agentics", profile_code="video-b")
    assert (r["model"], r["label"], r["family"]) == ("video-b", "MiniMax H3", "mmh3")
    r = _video(cfg_file, "agentics", profile_code="")                    # 未选 = genmedia 取目录首项
    assert (r["model"], r["family"]) == ("video-a", "sd20")
    monkeypatch.setattr(core, "agentics_video_profiles", lambda: [])     # 未登录拉不到目录
    r = _video(cfg_file, "agentics", profile_code="")
    assert (r["label"], r["family"]) == ("agentics", "")


def test_api_comfyui_matches_workflow_name_then_content(cfg_file, tmp_path, monkeypatch):
    assert _video(cfg_file, "comfyui", mode="local",
                  workflow="comfy/video-minimax-h3-ref2va-api.json")["family"] == "mmh3"
    wf = tmp_path / "my_flow.json"
    wf.write_text(json.dumps({"3": {"class_type": "ByteDance2ReferenceNode",
                                    "inputs": {"model": "Seedance 2.5"}}}))
    assert _video(cfg_file, "comfyui", mode="cloud", workflow=str(wf))["family"] == "sd25"
    # RunningHub:先看所选工作流备注,不命中再读本地缓存的工作流 JSON(不联网)
    assert _video(cfg_file, "comfyui", mode="rh_ai", rh_workflow_id="42",
                  rh_workflows=[{"id": "42", "note": "minimax-h3-int8", "site": "rh_ai"}])["family"] == "mmh3"
    monkeypatch.setattr(core, "RH_CACHE_DIR", tmp_path)
    (tmp_path / "rh_cn-7.json").write_text('{"1": {"class_type": "MiniMaxH3ReferenceToVideo"}}')
    assert _video(cfg_file, "comfyui", mode="rh_cn", rh_workflow_id="7",
                  rh_workflows=[{"id": "7", "note": "my flow"}])["family"] == "mmh3"
    assert _video(cfg_file, "comfyui", mode="rh_cn", rh_workflow_id="8")["family"] == ""
