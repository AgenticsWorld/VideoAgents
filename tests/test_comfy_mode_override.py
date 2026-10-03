"""预览页顶部选 ComfyUI 时二级下拉 = 运行方式(2026-09-13):model 槽存 local|cloud|rh_cn|rh_ai,
genmedia.get_config 按之改写 mode(RunningHub 工作流按站点重选),集级视频覆盖可切运行方式。"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules import genmedia  # noqa: E402

COMFY = {"mode": "local", "url": "http://127.0.0.1:8188", "workflow": "comfy/a.json", "cloud_api_key": "",
         "rh_api_key_cn": "kcn", "rh_api_key_ai": "kai", "rh_workflow_id": "111",
         "rh_workflows": [{"id": "111", "note": "cn", "site": "rh_cn"}, {"id": "222", "note": "ai", "site": "rh_ai"}]}


def _cfg(tmp_path, monkeypatch, video_mode="local"):
    cfg = tmp_path / "genconfig.json"
    cfg.write_text(json.dumps({"image": {"provider": "volcengine", "volcengine": {"api_key": "x", "model": "seedream"},
                                         "comfyui": COMFY},
                               "video": {"provider": "comfyui", "comfyui": {**COMFY, "mode": video_mode}}}))
    monkeypatch.setattr(genmedia, "CONFIG_PATH", cfg)
    for k in ("VIDEOAGENTS_IMAGE_PROVIDER", "VIDEOAGENTS_IMAGE_MODEL", "RUNNINGHUB_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    return cfg


def test_mode_configured_and_site_workflow():
    assert genmedia.comfy_mode_configured(COMFY, "local")
    assert not genmedia.comfy_mode_configured(COMFY, "cloud")
    assert genmedia.comfy_mode_configured(COMFY, "rh_cn") and genmedia.comfy_mode_configured(COMFY, "rh_ai")
    assert genmedia.rh_workflow_for_site(COMFY, "rh_cn") == "111"
    assert genmedia.rh_workflow_for_site(COMFY, "rh_ai") == "222"       # 当前 id 属 .cn,换站点取该站首个
    assert not genmedia.comfy_mode_configured({**COMFY, "rh_workflows": [], "rh_workflow_id": ""}, "rh_ai")


def test_get_config_image_comfy_mode_override(tmp_path, monkeypatch):
    _cfg(tmp_path, monkeypatch)
    c = genmedia.get_config("image", provider_override="comfyui", model_override="rh_ai")
    assert c["provider"] == "comfyui" and c["mode"] == "rh_ai" and c["rh_workflow_id"] == "222"
    c = genmedia.get_config("image", provider_override="comfyui", model_override="local")
    assert c["mode"] == "local" and c["rh_workflow_id"] == "111"
    with pytest.raises(RuntimeError, match="未配置"):
        genmedia.get_config("image", provider_override="comfyui", model_override="cloud")
    with pytest.raises(RuntimeError, match="运行方式"):
        genmedia.get_config("image", provider_override="comfyui", model_override="flux")
    # 预览页偏好经环境变量同样生效
    monkeypatch.setenv("VIDEOAGENTS_IMAGE_PROVIDER", "comfyui")
    monkeypatch.setenv("VIDEOAGENTS_IMAGE_MODEL", "rh_cn")
    assert genmedia.get_config("image")["mode"] == "rh_cn"


def test_resolve_video_override_comfy_mode(tmp_path, monkeypatch):
    _cfg(tmp_path, monkeypatch)
    sdir = tmp_path / "gs"
    sdir.mkdir()
    (sdir / "episode.json").write_text(json.dumps({"provider": "comfyui", "provider_override": False, "video_model": "rh_ai"}))
    r = genmedia.resolve_video_override(sdir, "grp001", "comfyui", "")
    assert (r["provider"], r["video_model"], r["source"]) == ("comfyui", "rh_ai", "episode") and not r["notes"]
    (sdir / "episode.json").write_text(json.dumps({"provider": "comfyui", "provider_override": False, "video_model": "cloud"}))
    r = genmedia.resolve_video_override(sdir, "grp001", "comfyui", "")
    assert r["source"] == "global" and any("cloud" in n for n in r["notes"])
    # 全局是别的渠道、集级切到 ComfyUI 某运行方式
    (sdir / "episode.json").write_text(json.dumps({"provider": "comfyui", "provider_override": True, "video_model": "rh_cn"}))
    r = genmedia.resolve_video_override(sdir, "grp001", "volcengine", "seedance")
    assert (r["provider"], r["video_model"], r["source"]) == ("comfyui", "rh_cn", "episode")
    # 组级不能切运行方式
    (sdir / "grp001.json").write_text(json.dumps({"provider": "comfyui", "video_model": "local"}))
    r = genmedia.resolve_video_override(sdir, "grp001", "volcengine", "seedance")
    assert r["video_model"] == "rh_cn" and any("组级" in n for n in r["notes"])


def test_core_candidates_and_options(monkeypatch):
    from services.runtime import core
    cfg = {"video": {"provider": "comfyui", "comfyui": {**COMFY, "mode": "rh_cn"}, "volcengine": {"api_key": "k", "model": "m"}}}
    monkeypatch.delenv("RUNNINGHUB_API_KEY", raising=False)
    opts = {p["id"]: p for p in core.video_provider_options(cfg)}
    assert opts["comfyui"]["configured"] and opts["comfyui"]["default_model"] == "rh_cn"
    assert {m["id"]: m["configured"] for m in opts["comfyui"]["models"]} == {"local": True, "cloud": False, "rh_cn": True, "rh_ai": True}
    assert core.video_model_label("rh_ai", "comfyui") == "RunningHub 国际(.ai)"
    es = {}
    monkeypatch.setattr(core, "_epsettings_get", lambda project, ep: es)
    c = core.group_video_candidates("p", cfg, "ep01")
    assert c["mode_switchable"] and not c["overridable"] and c["global_model"] == "rh_cn" and c["base_source"] == "global"
    es.update({"provider": "comfyui", "provider_override": False, "video_model": "local"})
    c = core.group_video_candidates("p", cfg, "ep01")
    assert (c["base_model"], c["base_source"]) == ("local", "episode") and not c["episode_warning"]
    es.update({"video_model": "cloud"})
    c = core.group_video_candidates("p", cfg, "ep01")
    assert c["base_source"] == "global" and "云端" in c["episode_warning"]
    # 全局火山、集级切 ComfyUI 运行方式
    cfg["video"]["provider"] = "volcengine"
    es.update({"provider_override": True, "video_model": "rh_ai"})
    c = core.group_video_candidates("p", cfg, "ep01")
    assert (c["provider"], c["base_model"], c["provider_source"]) == ("comfyui", "rh_ai", "episode")
    assert c["mode_switchable"] and c["candidates"][0]["id"] == "local"


def test_core_agentics_video_provider_and_profiles(monkeypatch):
    """2026-09-18:分镜预览顶部渠道下拉可选 Agentics,二级 = 登录账号的视频 profile;全局是 Agentics 时可按集/按组换 profile。"""
    from services.runtime import core
    profiles = [{"id": "seedance-pro", "label": "Seedance Pro"}, {"id": "kling-3", "label": "Kling 3"}]
    monkeypatch.setattr(core, "agentics_video_profiles", lambda: profiles)
    monkeypatch.setattr(core, "_AGENTICS_VIDEO_LAST", profiles)
    cfg = {"video": {"provider": "volcengine", "volcengine": {"api_key": "k", "model": "doubao-seedance-2-5-260628"},
                     "agentics": {"profile_code": "my-saved"}}}
    monkeypatch.delenv("VIDEOAGENTS_USER_JWT", raising=False)
    opts = {p["id"]: p for p in core.video_provider_options(cfg)}
    assert list(opts)[0] == "agentics" and not opts["agentics"]["configured"]
    monkeypatch.setenv("VIDEOAGENTS_USER_JWT", "jwt")
    opts = {p["id"]: p for p in core.video_provider_options(cfg)}
    assert opts["agentics"]["configured"] and opts["agentics"]["default_model"] == "my-saved"
    assert [m["id"] for m in opts["agentics"]["models"]] == ["my-saved", "seedance-pro", "kling-3"]
    assert core.episode_provider_models(cfg, "agentics") == {"my-saved": True, "seedance-pro": True, "kling-3": True}
    assert core.video_model_label("kling-3", "agentics") == "Kling 3"
    assert core.video_provider_configured(cfg, "agentics")
    es = {}
    monkeypatch.setattr(core, "_epsettings_get", lambda project, ep: es)
    # 全局火山、集级切到 Agentics 某 profile
    es.update({"provider": "agentics", "provider_override": True, "video_model": "kling-3"})
    c = core.group_video_candidates("p", cfg, "ep01")
    assert (c["provider"], c["base_model"], c["provider_source"]) == ("agentics", "kling-3", "episode")
    assert c["overridable"] and [r["id"] for r in c["candidates"]] == ["my-saved", "seedance-pro", "kling-3"]
    # 全局就是 Agentics:候选 = profile 目录,集级只换 profile
    cfg["video"]["provider"] = "agentics"
    es.clear(); es.update({"provider": "agentics", "provider_override": False, "video_model": "seedance-pro"})
    c = core.group_video_candidates("p", cfg, "ep01")
    assert (c["global_model"], c["base_model"], c["base_source"]) == ("my-saved", "seedance-pro", "episode")
    assert c["overridable"] and not c["episode_warning"]


def test_genmedia_episode_override_to_agentics_profile(monkeypatch, tmp_path):
    """集级切到 Agentics 某 profile:genmedia 按该 profile 取整份视频配置(video_model 槽 = profile_code)。"""
    from modules import genmedia
    cfg_path = tmp_path / "genconfig.json"
    cfg_path.write_text(json.dumps({"video": {"provider": "volcengine", "volcengine": {"api_key": "k", "model": "m"},
                                              "agentics": {"profile_code": "saved"}}}))
    monkeypatch.setattr(genmedia, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(genmedia, "_agentics_connection", lambda: ("https://api.agentics.world", "jwt"))
    sdir = tmp_path / "ep01"; sdir.mkdir()
    (sdir / "episode.json").write_text(json.dumps({"provider": "agentics", "provider_override": True, "video_model": "kling-3"}))
    r = genmedia.resolve_video_override(sdir, "grp001", "volcengine", "m")
    assert (r["provider"], r["video_model"], r["source"]) == ("agentics", "kling-3", "episode") and not r["notes"]
    vc = genmedia.video_cfg_for(genmedia.get_config("video"), r)
    assert vc["provider"] == "agentics" and vc["profile_code"] == "kling-3" and vc["model"] == "kling-3"
    # 组级在 Agentics 集渠道下换 profile
    (sdir / "grp001.json").write_text(json.dumps({"provider": "agentics", "video_model": "seedance-pro"}))
    r = genmedia.resolve_video_override(sdir, "grp001", "volcengine", "m")
    assert (r["provider"], r["video_model"], r["source"]) == ("agentics", "seedance-pro", "group")
