"""NSFW 模式(2026-09-25):genmedia 三层路由(显式标记 / 审核拒收兜底 / 粘性回写)、审核错误判定、
nsfw_hint 词表机检、宿主侧工单字段/拒绝句式识别。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules import genmedia, nsfw_hint  # noqa: E402


def _setup(tmp_path, monkeypatch, nsfw):
    st = tmp_path / "state.json"
    st.write_text(json.dumps({"nsfw": nsfw}))
    cfg = tmp_path / "genconfig.json"
    cfg.write_text(json.dumps({
        "video": {"provider": "volcengine", "volcengine": {"api_key": "k", "model": "doubao-seedance-2-0"},
                  "fal": {"api_key": "fk", "model": "fal-ai/wan"}},
        "image": {"provider": "volcengine", "volcengine": {"api_key": "k", "model": "seedream"},
                  "fal": {"api_key": "fk", "model": "fal-ai/flux"}}}))
    monkeypatch.setattr(genmedia, "STATE_PATH", st)
    monkeypatch.setattr(genmedia, "CONFIG_PATH", cfg)
    monkeypatch.setattr(genmedia, "DATA_DIR", tmp_path)
    monkeypatch.setenv("VIDEOAGENTS_PROJECT", "pj")
    monkeypatch.delenv("VIDEOAGENTS_NSFW", raising=False)
    return tmp_path / "projects" / "pj"


NSFW_ON = {"enabled": True, "failover": True, "video": {"provider": "fal", "model": "fal-ai/wan-x"},
           "image": {"provider": "", "model": ""}, "text": {"engine": "codex", "model": "gpt-6-sol"}}


def test_route_sources(tmp_path, monkeypatch):
    root = _setup(tmp_path, monkeypatch, NSFW_ON)
    assert genmedia.nsfw_route_reason("ep01/grp005") == ""
    gs = root / "assets/group_settings/ep01"
    gs.mkdir(parents=True)
    (gs / "grp005.json").write_text(json.dumps({"nsfw": True, "nsfw_reason": "manual"}))
    assert genmedia.nsfw_route_reason("ep01/grp005") == "group:manual"
    sl = root / "directing/ep01"
    sl.mkdir(parents=True)
    (sl / "shot_list.json").write_text(json.dumps({"generation_groups": [
        {"group_id": "grp006", "nsfw": True}, {"group_id": "grp007", "nsfw": True}]}))
    assert genmedia.nsfw_route_reason("ep01/grp006") == "group:shot_list"
    (gs / "grp007.json").write_text(json.dumps({"nsfw": False, "nsfw_reason": "manual"}))   # 手动 false 压掉推导
    assert genmedia.nsfw_route_reason("ep01/grp007") == ""
    monkeypatch.setenv("VIDEOAGENTS_NSFW", "1")
    assert genmedia.nsfw_route_reason("") == "workorder"


def test_apply_route_swaps_video_and_warns_unconfigured_image(tmp_path, monkeypatch):
    root = _setup(tmp_path, monkeypatch, NSFW_ON)
    monkeypatch.setenv("VIDEOAGENTS_NSFW", "1")
    cfg = genmedia.apply_nsfw_route("video", genmedia.get_config("video"), "")
    assert (cfg["provider"], cfg["model"], cfg["_nsfw_route"]) == ("fal", "fal-ai/wan-x", "workorder")
    img = genmedia.apply_nsfw_route("image", genmedia.get_config("image"), "")
    assert img["provider"] == "volcengine" and "_nsfw_route" not in img     # 未配备用图像渠道:照常主渠道


def test_route_off_when_disabled(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, dict(NSFW_ON, enabled=False))
    monkeypatch.setenv("VIDEOAGENTS_NSFW", "1")
    cfg = genmedia.apply_nsfw_route("video", genmedia.get_config("video"), "")
    assert cfg["provider"] == "volcengine" and "_nsfw_route" not in cfg


def test_fallback_unavailable_raises_not_silent(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, dict(NSFW_ON, video={"provider": "minimax", "model": "x"}))
    monkeypatch.setenv("VIDEOAGENTS_NSFW", "1")
    try:
        genmedia.apply_nsfw_route("video", genmedia.get_config("video"), "")
    except RuntimeError as e:
        assert "不回落主模型" in str(e)
    else:
        raise AssertionError("备用渠道未配置应报错而非偷跑主模型")


def test_moderation_error_detection():
    E = genmedia._HTTPStatusError
    assert genmedia.is_moderation_error(E(400, "u", '{"error":{"code":"InputTextSensitiveContentDetected"}}'))
    assert genmedia.is_moderation_error(RuntimeError("方舟视频生成失败:{\"code\": \"OutputVideoSensitiveContentDetected\"}"))
    assert genmedia.is_moderation_error(RuntimeError("MiniMax /video_generation 失败(code=1026):sensitive"))
    assert genmedia.is_moderation_error(RuntimeError("Fal video任务失败(content_policy_violation):x"))
    assert not genmedia.is_moderation_error(E(400, "u", '{"error":{"code":"InputImagePrivacyInformationDetected"}}'))
    # #112:方舟真人拒收的真实错误码带 SensitiveContentDetected 前缀,同样不算内容审核
    privacy = E(400, "u", '{"error":{"code":"InputVideoSensitiveContentDetected.PrivacyInformation","message":"may contain real person"}}')
    assert not genmedia.is_moderation_error(privacy)
    assert genmedia._error_code(privacy) == "InputVideoSensitiveContentDetected.PrivacyInformation"
    assert genmedia._error_code(RuntimeError("no code here")) == ""
    assert not genmedia.is_moderation_error(E(429, "u", "moderation rate limit"))
    assert not genmedia.is_moderation_error(genmedia._TransportError("u", OSError("reset")))
    assert not genmedia.is_moderation_error(RuntimeError("Error when parsing request"))


def test_failover_once_and_sticky(tmp_path, monkeypatch):
    root = _setup(tmp_path, monkeypatch, NSFW_ON)
    cfg = genmedia.get_config("video")
    err = RuntimeError("方舟视频生成失败:OutputVideoSensitiveContentDetected")
    alt = genmedia.nsfw_failover_cfg("video", cfg, err, "ep01/grp009")
    assert alt["provider"] == "fal" and alt["_nsfw_route"] == "failover"
    gs = json.loads((root / "assets/group_settings/ep01/grp009.json").read_text())
    assert gs["nsfw"] is True and gs["nsfw_reason"] == "failover"
    assert genmedia.nsfw_route_reason("ep01/grp009") == "group:failover"     # 粘性:下次直接走备用
    assert genmedia.nsfw_failover_cfg("video", alt, err, "ep01/grp009") is None   # 备用也拒:不再切,原样抛
    assert genmedia.nsfw_failover_cfg("video", cfg, RuntimeError("HTTP 401"), "ep01/grp010") is None
    _setup(tmp_path, monkeypatch, dict(NSFW_ON, failover=False))
    assert genmedia.nsfw_failover_cfg("video", cfg, err, "ep01/grp011") is None


def test_nsfw_hint_lexicon_and_negatives():
    assert nsfw_hint.scan_text("She stands nude by the window. Negative: gore, severed limbs.") == {"nudity": ["nude"]}
    assert "self_harm" in nsfw_hint.scan_text("剧情推向高潮,他割腕自杀。禁止出现血腥。")
    assert nsfw_hint.scan_text("a gorgeous sunset, no nudity") == {}


def test_nsfw_hint_scan_episode(tmp_path):
    root = tmp_path / "pj"
    (root / "directing/ep01").mkdir(parents=True)
    (root / "directing/ep01/shot_list.json").write_text(json.dumps({
        "shots": [{"shot_id": "sh001", "poses": {"c1": {"action": "全裸走向浴池"}}}],
        "generation_groups": [{"group_id": "grp001", "shots": ["sh001"]},
                              {"group_id": "grp002", "shots": [], "nsfw": True, "audio_plan": "血腥"}]}, ensure_ascii=False))
    res = nsfw_hint.scan_episode(root, "ep01")
    assert [r["grp"] for r in res["groups"]] == ["grp001", "grp002"]
    assert len(res["warns"]) == 1 and "grp001" in res["warns"][0]     # 已标记的 grp002 不提示


def test_host_workorder_regexes():
    from services.runtime import core
    msg = "task_id: p7-ep01-grp005-videogen\nagent: 08-video-gen/video-generation\nnsfw: true\ninstruction: |\n  x"
    assert core._wo_nsfw_flag(msg) and core._wo_task_id(msg) == "p7-ep01-grp005-videogen"
    assert not core._wo_nsfw_flag("nsfw: false\ntask_id: a")
    assert core.llm_refusal_suspect("I'm sorry, but I can't help with creating sexually explicit content.")
    assert core.llm_refusal_suspect("抱歉,我无法协助生成这类内容,因为它违反内容政策。")
    assert not core.llm_refusal_suspect("已完成 grp005 视频生成,产物 assets/clips/ep01/grp005.mp4。")


def test_host_route_for_run(tmp_path, monkeypatch):
    from services.runtime import core
    monkeypatch.setattr(core, "PROJECTS_DIR", tmp_path)
    monkeypatch.setitem(core.STATE, "nsfw", {"enabled": True, "text": {"engine": "codex", "model": "gpt-6-sol"}})
    wo = "task_id: p7-ep01-grp005-videogen\nnsfw: true\ninstruction: x"
    r = core.nsfw_route_for_run("pj", wo, False)
    assert r == {"reason": "workorder", "engine": "codex", "model": "gpt-6-sol"}
    assert core.nsfw_route_for_run("pj", "task_id: t1\ninstruction: x", True)["reason"] == "workorder"
    assert core.nsfw_route_for_run("pj", "task_id: t1\ninstruction: x", False) is None
    core.nsfw_mark_suspect("pj", "t1", "run1", "01-story/x", "I can't help with that")
    r2 = core.nsfw_route_for_run("pj", "task_id: t1\ninstruction: retry", False)
    assert r2["reason"] == "retry_after_refusal" and r2["engine"] == "codex"      # 第二次尝试才换
    monkeypatch.setitem(core.STATE, "nsfw", {"enabled": True, "text": {"engine": "", "model": ""}})
    r3 = core.nsfw_route_for_run("pj", wo, False)
    assert r3 == {"reason": "workorder"}                                          # 未配备用语言模型:只打标记
    monkeypatch.setitem(core.STATE, "nsfw", {"enabled": False, "text": {"engine": "codex", "model": ""}})
    assert core.nsfw_route_for_run("pj", wo, True) is None


def test_group_nsfw_state_precedence(tmp_path, monkeypatch):
    from services.runtime import core
    monkeypatch.setattr(core, "PROJECTS_DIR", tmp_path)
    (tmp_path / "pj/assets/group_settings/ep01").mkdir(parents=True)
    st = core.group_nsfw_state("pj", "ep01", "grp001", {"group_id": "grp001", "nsfw": True})
    assert st == {"nsfw": True, "source": "shot_list", "reason": "content_flags"}
    (tmp_path / "pj/assets/group_settings/ep01/grp001.json").write_text(json.dumps({"nsfw": False, "nsfw_reason": "manual"}))
    assert core.group_nsfw_state("pj", "ep01", "grp001", {"nsfw": True})["nsfw"] is False
    (tmp_path / "pj/assets/group_settings/ep01/grp001.json").write_text(json.dumps({"nsfw": True, "nsfw_reason": "failover"}))
    assert core.group_nsfw_state("pj", "ep01", "grp001", None)["source"] == "failover"
