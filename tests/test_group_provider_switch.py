"""2026-11-08:分镜预览组卡「🎛 组视频模型」弹窗可为本组单独切换视频渠道(同顶部集级口径:须已配 Key;
ComfyUI 二级 = 运行方式)。组文件 assets/group_settings/<ep>/<grp>.json {provider, provider_override, video_model}。"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.runtime import core  # noqa: E402
from modules import genmedia  # noqa: E402

COMFY = {"mode": "local", "url": "http://127.0.0.1:8188", "workflow": "comfy/a.json", "cloud_api_key": "",
         "rh_api_key_cn": "kcn", "rh_api_key_ai": "", "rh_workflow_id": "111",
         "rh_workflows": [{"id": "111", "note": "cn", "site": "rh_cn"}]}


def _cfg():
    return {"video": {"provider": "volcengine",
                      "volcengine": {"api_key": "kv", "model": "doubao-seedance-2-0-260128"},
                      "fal": {"api_key": "kf", "model": "bytedance/seedance-2.0"},
                      "byteplus": {"api_key": ""},
                      "comfyui": COMFY}}


@pytest.fixture
def proj(tmp_path, monkeypatch):
    pdir = tmp_path / "projects"
    (pdir / "p" / "directing" / "ep01").mkdir(parents=True)
    monkeypatch.setattr(core, "PROJECTS_DIR", pdir)
    monkeypatch.setattr(core, "load_genconfig", _cfg)
    for k in ("BYTEPLUS_API_KEY", "VIDEOAGENTS_USER_JWT", "RUNNINGHUB_API_KEY", "FAL_KEY", "ARK_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    published = []
    monkeypatch.setattr(core.HUB, "publish", lambda ev: published.append(ev))
    return pdir / "p"


def _set(body):
    return asyncio.run(core.api_grpsettings_set({"project": "p", "ep": "ep01", "grp": "grp001", **body}))


def _gs(proj):
    return json.loads((proj / "assets/group_settings/ep01/grp001.json").read_text())


def test_group_switch_provider_roundtrip(proj):
    out = _set({"provider": "fal", "video_model": "", "prompt_skill": {"mode": "global"}})
    gs = _gs(proj)
    # 模型空 = 该渠道在「生成模型」页保存的模型
    assert (gs["provider"], gs["provider_override"], gs["video_model"]) == ("fal", True, "bytedance/seedance-2.0")
    r = out["resolved"]
    assert (r["provider"], r["provider_source"], r["video_model"], r["model_source"]) == \
        ("fal", "group", "bytedance/seedance-2.0", "group")
    assert r["base_provider"] == "volcengine" and not r["warning"]
    assert out["config"]["provider"] == "fal" and any(p["id"] == "fal" for p in out["providers"])
    # 选回本集生效渠道 = 渠道跟随;只换模型时 provider 记本集渠道、provider_override=false
    _set({"provider": "volcengine", "video_model": "doubao-seedance-2-5-260628", "prompt_skill": {"mode": "global"}})
    gs = _gs(proj)
    assert (gs["provider"], gs["provider_override"], gs["video_model"]) == \
        ("volcengine", False, "doubao-seedance-2-5-260628")
    # 全跟随 = 删文件
    _set({"provider": "", "video_model": "", "prompt_skill": {"mode": "global"}})
    assert not (proj / "assets/group_settings/ep01/grp001.json").exists()


def test_group_switch_rejects_unconfigured_and_foreign_model(proj):
    with pytest.raises(core.ServiceError, match="未配置 API Key"):
        _set({"provider": "byteplus", "video_model": "", "prompt_skill": {"mode": "global"}})
    with pytest.raises(core.ServiceError, match="可选目录"):
        _set({"provider": "fal", "video_model": "doubao-seedance-2-5-260628", "prompt_skill": {"mode": "global"}})
    with pytest.raises(core.ServiceError, match="未配置"):
        _set({"provider": "comfyui", "video_model": "cloud", "prompt_skill": {"mode": "global"}})
    out = _set({"provider": "comfyui", "video_model": "rh_cn", "prompt_skill": {"mode": "global"}})
    assert (out["resolved"]["provider"], out["resolved"]["video_model"]) == ("comfyui", "rh_cn")


def test_group_switch_over_episode_provider(proj):
    """本集已切到 Fal:组可切回全局渠道火山,也可在本集 ComfyUI 时换运行方式。"""
    sdir = proj / "assets/group_settings/ep01"
    sdir.mkdir(parents=True)
    (sdir / "episode.json").write_text(json.dumps({"provider": "fal", "provider_override": True,
                                                    "video_model": "bytedance/seedance-2.0"}))
    out = _set({"provider": "volcengine", "video_model": "doubao-seedance-2-5-260628", "prompt_skill": {"mode": "global"}})
    r = out["resolved"]
    assert (r["provider"], r["provider_source"], r["video_model"]) == ("volcengine", "group", "doubao-seedance-2-5-260628")
    assert r["ref_cap"] == 30 and r["base_provider"] == "fal"
    assert "组级切换" in core.group_overrides_prompt("p") and "**volcengine**" in core.group_overrides_prompt("p")
    # 本集切到 ComfyUI 后:组跟随渠道,只换运行方式
    (sdir / "episode.json").write_text(json.dumps({"provider": "comfyui", "provider_override": True,
                                                    "video_model": "local"}))
    out = _set({"provider": "", "video_model": "rh_cn", "prompt_skill": {"mode": "global"}})
    r = out["resolved"]
    assert (r["provider"], r["provider_source"], r["video_model"], r["model_source"]) == \
        ("comfyui", "episode", "rh_cn", "group")
    with pytest.raises(core.ServiceError, match="未配置"):
        _set({"provider": "", "video_model": "cloud", "prompt_skill": {"mode": "global"}})


def test_group_switch_invalid_falls_back_with_warning(proj, monkeypatch):
    sdir = proj / "assets/group_settings/ep01"
    sdir.mkdir(parents=True)
    (sdir / "grp001.json").write_text(json.dumps({"provider": "byteplus", "provider_override": True,
                                                   "video_model": "dreamina-seedance-2-5-260628"}))
    r = core.resolve_group_settings("p", "ep01", "grp001")
    assert (r["provider"], r["model_source"]) == ("volcengine", "global") and "byteplus" in r["warning"]
    # 🔞 清除时 provider_override 不算残留键:有覆盖保留文件,无覆盖删文件
    (sdir / "grp001.json").write_text(json.dumps({"provider": "", "provider_override": False, "video_model": "",
                                                   "prompt_skill": {"mode": "global"}, "nsfw": True,
                                                   "nsfw_reason": "manual"}))
    asyncio.run(core.api_grpsettings_set({"project": "p", "ep": "ep01", "grp": "grp001", "nsfw": None}))
    assert not (sdir / "grp001.json").exists()


def test_genmedia_group_provider_override(tmp_path, monkeypatch):
    cfg_path = tmp_path / "genconfig.json"
    cfg_path.write_text(json.dumps(_cfg()))
    monkeypatch.setattr(genmedia, "CONFIG_PATH", cfg_path)
    monkeypatch.setenv("VIDEOAGENTS_CONFIG_PATH", str(cfg_path))
    for k in ("BYTEPLUS_API_KEY", "RUNNINGHUB_API_KEY", "FAL_KEY", "VIDEOAGENTS_IMAGE_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    sdir = tmp_path / "ep01"
    sdir.mkdir()
    gf = sdir / "grp001.json"
    gf.write_text(json.dumps({"provider": "fal", "provider_override": True, "video_model": "bytedance/seedance-2.0"}))
    r = genmedia.resolve_video_override(sdir, "grp001", "volcengine", "doubao-seedance-2-0-260128")
    assert (r["provider"], r["video_model"], r["source"]) == ("fal", "bytedance/seedance-2.0", "group") and not r["notes"]
    vc = genmedia.video_cfg_for(genmedia.get_config("video"), r)
    assert vc["provider"] == "fal" and vc["model"] == "bytedance/seedance-2.0" and vc["api_key"] == "kf"
    # 本集切到 Fal、组切回火山:同全局渠道,只换模型
    (sdir / "episode.json").write_text(json.dumps({"provider": "fal", "provider_override": True,
                                                    "video_model": "bytedance/seedance-2.0"}))
    gf.write_text(json.dumps({"provider": "volcengine", "provider_override": True,
                              "video_model": "doubao-seedance-2-5-260628"}))
    r = genmedia.resolve_video_override(sdir, "grp001", "volcengine", "doubao-seedance-2-0-260128")
    assert (r["provider"], r["video_model"], r["source"]) == ("volcengine", "doubao-seedance-2-5-260628", "group")
    vc = genmedia.video_cfg_for(genmedia.get_config("video"), r)
    assert vc["provider"] == "volcengine" and vc["model"] == "doubao-seedance-2-5-260628"
    # 未配 Key 的渠道:组级不生效,按本集执行并给提示
    gf.write_text(json.dumps({"provider": "byteplus", "provider_override": True,
                              "video_model": "dreamina-seedance-2-5-260628"}))
    r = genmedia.resolve_video_override(sdir, "grp001", "volcengine", "doubao-seedance-2-0-260128")
    assert (r["provider"], r["source"]) == ("fal", "episode") and any("byteplus" in n for n in r["notes"])
    # 老组文件(无 provider_override):属别的渠道仍判失效
    gf.write_text(json.dumps({"provider": "volcengine", "video_model": "doubao-seedance-2-5-260628"}))
    r = genmedia.resolve_video_override(sdir, "grp001", "volcengine", "doubao-seedance-2-0-260128")
    assert r["source"] == "episode" and any("组级模型" in n for n in r["notes"])
