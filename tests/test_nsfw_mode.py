"""NSFW 模式(2026-09-25):genmedia 三层路由(显式标记 / 审核拒收兜底 / 粘性回写)、审核错误判定、
nsfw_hint 词表机检、宿主侧工单字段/拒绝句式识别;拒绝判定用审核模型(2026-10-20)。"""
import asyncio
import io
import json
import sys
from pathlib import Path

import pytest

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


# ---------------------------------------------------------------- 拒绝判定用审核模型(2026-10-20)
def test_refusal_judge_request_and_parse():
    from modules import refusal_judge as rj
    body = rj.request_body("x" * 3000 + "我不做这种编辑", "typesafe/jev-1.13")
    reply = body["state"]["final_reply"]
    assert body["model"] == "typesafe/jev-1.13" and len(reply) == rj.TAIL and reply.endswith("我不做这种编辑")
    q = body["questions"]["kind"]
    assert q["type"] == "choice" and set(q["criteria"]) == set(rj.KINDS)
    ok = {"model": "typesafe/jev-1.13-20260917", "provider": "TypeSafe",
          "answers": {"kind": {"type": "choice", "choice": "policy", "confidence": 0.97,
                               "probabilities": {"policy": 0.97, "missing_input": 0.03, "clarification": 0}}},
          "usage": {"input_tokens": 732, "output_tokens": 55, "cost": 3.07e-05}}
    assert rj.parse(ok) == {"kind": "policy", "refusal": True, "confidence": 0.97,
                            "probabilities": {"policy": 0.97, "missing_input": 0.03, "clarification": 0},
                            "snapshot": "typesafe/jev-1.13-20260917", "cost": 3.07e-05}
    assert rj.parse({"answers": {"kind": {"choice": "missing_input"}}})["refusal"] is False
    assert rj.parse({"answers": {"kind": {"choice": "weird"}}})["kind"] == "other"
    for bad in ({}, {"answers": {}}, {"answers": {"kind": {"choice": ""}}}):
        with pytest.raises(rj.JudgeError):
            rj.parse(bad)


def test_refusal_judge_classify_posts_decisions_api(monkeypatch):
    from modules import refusal_judge as rj
    seen = {}

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def fake(req, timeout):
        seen.update(url=req.full_url, auth=req.headers.get("Authorization"), body=json.loads(req.data))
        return Resp(json.dumps({"model": "typesafe/jev-1.13-20260917",
                                "answers": {"kind": {"choice": "clarification", "confidence": 1}}}).encode())
    monkeypatch.setattr(rj.urllib.request, "urlopen", fake)
    v = rj.classify("要不要继续?", api_key="sk", model="typesafe/jev-1.13")
    assert seen["url"] == "https://openrouter.ai/api/alpha/decisions" and seen["auth"] == "Bearer sk"
    assert seen["body"]["questions"]["kind"] == rj.QUESTION and seen["body"]["state"]["final_reply"] == "要不要继续?"
    assert v["kind"] == "clarification" and v["refusal"] is False and v["model"] == "typesafe/jev-1.13"
    rj.classify("x", api_key="jwt", model="typesafe/jev-1.14",
                url="https://api.agentics.world/wrapper/openrouter" + rj.PATH)              # Agentics 渠道传 wrapper 地址
    assert seen["url"] == "https://api.agentics.world/wrapper/openrouter/api/alpha/decisions" and seen["auth"] == "Bearer jwt"

    def boom(req, timeout):
        raise rj.urllib.error.HTTPError(req.full_url, 401, "no", {}, io.BytesIO(b'{"error":"bad key"}'))
    monkeypatch.setattr(rj.urllib.request, "urlopen", boom)
    with pytest.raises(rj.JudgeError, match="HTTP 401"):
        rj.classify("x", api_key="sk", model="typesafe/jev-1.13")


def test_moderation_config_gates_judge_switch(monkeypatch):
    """OpenRouter 渠道:没填 Key 时拒绝打开 judge;只认清单里的模型;首版存下的 jev-router 回落 jev-1.13。"""
    from services.runtime import core
    monkeypatch.setattr(core, "ui_lang_code", lambda cfg=None: "zh")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    old = {"moderation": {"provider": "openrouter", "agentics": {"model": "typesafe/jev-router"},
                          "openrouter": {"api_key": "", "model": "typesafe/jev-router"}}}
    core._migrate_genconfig(old)
    gc = core._merge(core.DEFAULT_GENCONFIG, old)
    assert gc["moderation"] == {"provider": "openrouter", "agentics": {"model": "typesafe/jev-1.13"},
                                "openrouter": {"api_key": "", "model": "typesafe/jev-1.13"}}
    monkeypatch.setattr(core, "load_genconfig", lambda: gc)
    monkeypatch.setattr(core, "video_provider_options", lambda cfg=None: [])
    monkeypatch.setattr(core, "_image_channels", lambda: [])
    saved = []
    monkeypatch.setattr(core, "save_state", lambda st: saved.append(dict(st.get("nsfw") or {})))
    monkeypatch.setitem(core.STATE, "nsfw", {"enabled": True})
    st = core.moderation_status()
    assert st["ready"] is False and "API Key" in st["error"]
    with pytest.raises(core.ServiceError) as e:
        asyncio.run(core.api_nsfw_set({"judge": True}))
    assert e.value.status_code == 400 and "审核模型" in e.value.detail and not saved
    gc["moderation"]["openrouter"]["api_key"] = "sk-or"
    assert core.resolve_moderation() == {"provider": "openrouter", "url": "https://openrouter.ai/api/alpha/decisions",
                                         "api_key": "sk-or", "model": "typesafe/jev-1.13"}
    d = asyncio.run(core.api_nsfw_set({"judge": True}))
    assert d["judge"] is True and d["judge_model"]["ready"] is True and saved[-1]["judge"] is True
    assert asyncio.run(core.api_nsfw_set({"judge": False}))["judge"] is False     # 关闭不需要审核模型可用
    gc["moderation"]["openrouter"]["model"] = "typesafe/jev-1.14"
    assert core.resolve_moderation()["model"] == "typesafe/jev-1.14"               # 2026-10-21 加入清单
    keep = {"moderation": {"openrouter": {"api_key": "", "model": "typesafe/jev-1.14"}}}
    core._migrate_genconfig(keep)
    assert keep["moderation"]["openrouter"]["model"] == "typesafe/jev-1.14"        # 迁移不把 1.14 改回 1.13
    gc["moderation"]["openrouter"]["model"] = "typesafe/jev-router"
    assert core.moderation_status()["ready"] is False                              # 只认清单里的模型
    gc["moderation"]["openrouter"].update(api_key="", model="typesafe/jev-1.13")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-env")
    assert core.resolve_moderation()["api_key"] == "sk-env"                       # 没填 Key 时用环境变量
    r = asyncio.run(core.api_test_moderation({"provider": "openrouter", "api_key": "", "model": "typesafe/jev-router"}))
    assert r["ok"] is False and "不支持" in r["error"]


def test_moderation_agentics_default_and_migration(monkeypatch):
    """2026-10-22 Agentics 支持 Decisions API:默认渠道 Agentics(桌面端账号,地址 = wrapper + /api/alpha/decisions);
    只有 OpenRouter 时存下的配置,没填 Key 的改走 Agentics,填了 Key 的留在 OpenRouter;新版存的配置按所选不再改。"""
    from services.runtime import core
    monkeypatch.setattr(core, "ui_lang_code", lambda cfg=None: "zh")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    for var in ("VIDEOAGENTS_USER_JWT", "VIDEOAGENTS_SERVICE_API_ORIGIN", "VIDEOAGENTS_OPENROUTER_WRAPPER_URL"):
        monkeypatch.delenv(var, raising=False)
    assert core.DEFAULT_GENCONFIG["moderation"]["provider"] == "agentics"
    nokey = {"moderation": {"provider": "openrouter", "openrouter": {"api_key": "", "model": "typesafe/jev-1.14"}}}
    core._migrate_genconfig(nokey)
    assert nokey["moderation"]["provider"] == "agentics" and nokey["moderation"]["agentics"] == {"model": "typesafe/jev-1.14"}
    assert nokey["moderation"]["openrouter"]["model"] == "typesafe/jev-1.14"      # 沿用选过的模型
    keyed = {"moderation": {"provider": "openrouter", "openrouter": {"api_key": "sk-or", "model": "typesafe/jev-1.13"}}}
    core._migrate_genconfig(keyed)
    assert keyed["moderation"]["provider"] == "openrouter"
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-env")
    env = {"moderation": {"provider": "openrouter", "openrouter": {"api_key": "", "model": "typesafe/jev-1.13"}}}
    core._migrate_genconfig(env)
    assert env["moderation"]["provider"] == "openrouter"                           # 靠环境变量 Key 在用的不动
    monkeypatch.delenv("OPENROUTER_API_KEY")
    chosen = {"moderation": {"provider": "openrouter", "agentics": {"model": "typesafe/jev-1.13"},
                             "openrouter": {"api_key": "", "model": "typesafe/jev-1.13"}}}
    core._migrate_genconfig(chosen)
    assert chosen["moderation"]["provider"] == "openrouter"                        # 新版保存过:按用户所选

    gc = core._merge(core.DEFAULT_GENCONFIG, {"moderation": {"agentics": {"model": "typesafe/jev-1.14"}}})
    monkeypatch.setattr(core, "load_genconfig", lambda: gc)
    st = core.moderation_status()
    assert st["ready"] is False and st["provider"] == "agentics" and "没有登录" in st["error"]
    r = asyncio.run(core.api_test_moderation({"provider": "agentics", "model": "typesafe/jev-1.14"}))
    assert r["ok"] is False and "没有登录" in r["error"]
    monkeypatch.setenv("VIDEOAGENTS_USER_JWT", "jwt-1")
    monkeypatch.setenv("VIDEOAGENTS_OPENROUTER_WRAPPER_URL", "https://api.agentics.world/wrapper/openrouter")
    assert core.resolve_moderation() == {
        "provider": "agentics", "url": "https://api.agentics.world/wrapper/openrouter/api/alpha/decisions",
        "api_key": "jwt-1", "model": "typesafe/jev-1.14"}
    assert core.moderation_status() == {"ready": True, "provider": "agentics", "model": "typesafe/jev-1.14"}
    seen = {}

    def fake(text, **kw):
        seen.update(kw)
        return {"kind": "policy", "refusal": True, "confidence": 1, "cost": None, "model": kw["model"], "ms": 1}
    monkeypatch.setattr(core._refusal_judge, "classify", fake)
    r = asyncio.run(core.api_test_moderation({"provider": "agentics", "model": "typesafe/jev-1.13"}))
    assert r["ok"] is True and r["refusal"] is True
    assert seen == {"api_key": "jwt-1", "model": "typesafe/jev-1.13",
                    "url": "https://api.agentics.world/wrapper/openrouter/api/alpha/decisions"}
    assert core._run_refusal_judge("x")["provider"] == "agentics" and seen["model"] == "typesafe/jev-1.14"
    with pytest.raises(core.ServiceError):
        asyncio.run(core.api_test_moderation({"provider": "fal"}))


def test_refusal_postcheck_prescreen_then_judge(tmp_path, monkeypatch):
    """套话直接认;没认出时只有「正常结束、回复不长、带 task_id、没写文件」的运行才调审核模型,policy 才标 nsfw_suspect。"""
    from services.runtime import core
    monkeypatch.setattr(core, "PROJECTS_DIR", tmp_path)
    cfg = {"enabled": True, "failover": True, "judge": True, "text": {"engine": "codex", "model": ""},
           "image": {"provider": "", "model": ""}, "video": {"provider": "", "model": ""}}
    monkeypatch.setattr(core, "nsfw_settings", lambda: cfg)
    monkeypatch.setattr(core, "resolve_moderation", lambda cfg=None: {
        "provider": "openrouter", "url": "https://openrouter.ai/api/alpha/decisions", "api_key": "k",
        "model": "typesafe/jev-1.13"})
    verdict, calls = ["policy"], []

    def fake(text, **kw):
        calls.append(text)
        if verdict[0] == "boom":
            raise core._refusal_judge.JudgeError("HTTP 500: upstream")
        return {"kind": verdict[0], "refusal": verdict[0] == "policy", "confidence": 1, "cost": 0.00003,
                "model": kw["model"], "ms": 1}
    monkeypatch.setattr(core._refusal_judge, "classify", fake)
    reply = "这一段我不写:要求把强迫的性场面写细,这类内容我不接。"
    assert not core.llm_refusal_suspect(reply)

    def check(**kw):
        run = {"id": "r1", "agent": "01-story/script", "project": "pj", "message": "task_id: p5-ep01-script\ninstruction: x",
               "status": "done", "result": reply, **kw}
        asyncio.run(core._nsfw_refusal_postcheck(run))
        return run
    r = check()
    assert calls == [reply] and r["nsfw_suspect"]["by"] == "judge" and r["refusal_judge"]["kind"] == "policy"
    assert "p5-ep01-script" in core.nsfw_suspect_tasks("pj")
    for kw in ({"files": ["a.md"]}, {"message": "instruction: x"}, {"result": reply * 200}, {"status": "error"},
               {"stopped": "user"}):
        assert "nsfw_suspect" not in check(**kw)                                    # 预筛不过:不调审核模型
    assert len(calls) == 1
    r = check(result="I'm sorry, but I can't help with that request.")
    assert r["nsfw_suspect"]["by"] == "regex" and len(calls) == 1                   # 套话直接认,不花钱
    verdict[0] = "missing_input"
    r = check()
    assert "nsfw_suspect" not in r and r["refusal_judge"]["kind"] == "missing_input"
    verdict[0] = "boom"
    r = check()
    assert "nsfw_suspect" not in r and "HTTP 500" in r["refusal_judge"]["error"]    # 调用失败:只记录,退回套话
    cfg["judge"] = False
    assert "nsfw_suspect" not in check()
    cfg["judge"] = True
    assert "nsfw_suspect" not in check(nsfw_route={"reason": "workorder", "engine": "codex"})   # 备用模型也拒:交用户
    assert len(calls) == 3
