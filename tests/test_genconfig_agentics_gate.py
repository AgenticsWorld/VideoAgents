"""保存生成模型配置时的 Agentics 登录校验:只拦「本次新切到 agentics」的类别。

图像/视频/音乐/语音/数字人/deepagents 默认渠道都是 agentics;按合并后整份配置校验会让浏览器版
(无桌面端登录)保存任何无关设置都 401(切引擎时的分配策略、界面语言…)。
"""
import asyncio
import json

import pytest

from services.runtime import core


@pytest.fixture
def cfg_file(tmp_path, monkeypatch):
    path = tmp_path / "genconfig.json"
    monkeypatch.setattr(core, "GENCONFIG_PATH", path)
    monkeypatch.delenv("VIDEOAGENTS_USER_JWT", raising=False)
    monkeypatch.setattr(core, "save_state", lambda *a, **k: None)
    monkeypatch.setitem(core.STATE, "ui_lang", "")

    async def no_refresh(cfg):
        return []
    monkeypatch.setattr(core, "_refresh_rh_wf_caches", no_refresh)
    return path


def _save(body):
    return asyncio.run(core.api_genconfig_set(body))


def test_unrelated_saves_pass_with_default_agentics_and_no_login(cfg_file):
    assert core.load_genconfig()["deepagents"]["provider"] == "agentics"     # 默认渠道
    assert _save({"ui_language": "en", "project": "demo"})["ok"]
    assert _save({"deepagents": {"local": {"model": "qwen3"}}})["ok"]
    saved = json.loads(cfg_file.read_text())
    assert saved["ui_language"] == "en" and saved["deepagents"]["local"]["model"] == "qwen3"
    assert saved["deepagents"]["provider"] == "agentics"                       # 原样保留,不偷改渠道


def test_local_cloud_vision_checkbox_defaults_on_and_saves_bool(cfg_file):
    """「语言模型/Agentics」本地 / 云端的「图片输入」勾选:没有这个键的存量配置算勾上,保存只存布尔。"""
    cfg = core.load_genconfig()
    assert cfg["deepagents"]["local"]["vision"] is True and cfg["deepagents"]["cloud"]["vision"] is True
    cfg_file.write_text(json.dumps({"deepagents": {"provider": "local", "local": {"model": "qwen3"}}}))
    assert core.resolve_deepagents()["vision"] is True                         # 升级前保存的配置
    _save({"deepagents": {"local": {"vision": False}}})
    saved = json.loads(cfg_file.read_text())["deepagents"]
    assert saved["local"] == {**saved["local"], "model": "qwen3", "vision": False}
    assert saved["cloud"]["vision"] is True
    assert core.resolve_deepagents()["vision"] is False
    _save({"deepagents": {"provider": "cloud", "local": {"vision": "yes"}, "cloud": {"vision": False}}})
    saved = json.loads(cfg_file.read_text())["deepagents"]
    assert saved["local"]["vision"] is True and saved["cloud"]["vision"] is False
    assert core.resolve_deepagents()["vision"] is False


def test_switching_to_agentics_still_requires_login(cfg_file, monkeypatch):
    _save({"deepagents": {"provider": "local"}})
    with pytest.raises(core.ServiceError) as ei:
        _save({"deepagents": {"provider": "agentics"}})
    assert ei.value.status_code == 401
    assert json.loads(cfg_file.read_text())["deepagents"]["provider"] == "local"   # 拒绝时不落盘
    monkeypatch.setattr(core, "resolve_agentics_connection", lambda: {
        "api_origin": "https://api.agentics.world", "api_key": "jwt", "base_url": "https://x/api/v1"})
    assert _save({"deepagents": {"provider": "agentics"}})["config"]["deepagents"]["provider"] == "agentics"


def test_switching_other_kind_to_agentics_is_checked(cfg_file):
    _save({"video": {"provider": "volcengine"}})
    with pytest.raises(core.ServiceError) as ei:
        _save({"video": {"provider": "agentics"}, "ui_language": "ja"})
    assert ei.value.status_code == 401


def test_cloud_channel_has_no_default_endpoint_or_model(cfg_file):
    """云端模型渠道(2026-10-26 恢复显示)没有默认端点 / 模型;旧版存下的 DeepSeek 默认值没填过 Key 就清掉,填了 Key 的不动。"""
    cl = core.load_genconfig()["deepagents"]["cloud"]
    assert (cl["base_url"], cl["api_key"], cl["model"], cl["custom_model"]) == ("", "", "", "")
    cfg_file.write_text(json.dumps({"deepagents": {"provider": "cloud", "cloud": {
        "base_url": "https://api.deepseek.com", "api_key": "", "model": "deepseek-v4-flash", "custom_model": ""}}}))
    da = core.resolve_deepagents()
    assert da["provider"] == "cloud" and da["base_url"] == "" and da["model"] == ""   # 不再回落 DeepSeek
    used = {"base_url": "https://api.deepseek.com/", "api_key": "sk-x", "model": "deepseek-v4-flash", "custom_model": ""}
    cfg_file.write_text(json.dumps({"deepagents": {"provider": "cloud", "cloud": used}}))
    assert core.load_genconfig()["deepagents"]["cloud"] == {**used, "vision": True}
    typed = {"base_url": "https://api.example.test/v1", "api_key": "", "model": "deepseek-v4-pro",
             "custom_model": "deepseek-v4-pro"}                                        # 手填(自定义)的模型不算旧默认
    cfg_file.write_text(json.dumps({"deepagents": {"provider": "cloud", "cloud": typed}}))
    assert core.load_genconfig()["deepagents"]["cloud"] == {**typed, "vision": True}
