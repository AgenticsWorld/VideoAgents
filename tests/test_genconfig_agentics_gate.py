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
