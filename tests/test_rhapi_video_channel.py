"""2026-11-07:分镜预览顶部集级视频渠道下拉(以及共用同一清单的组卡「🎛 模型」、画板动图、NSFW 视频渠道)补上 RH 渠道
(rhapi = RunningHub 标准模型 API)。二级 = 当前站点的视频模型目录,只读本地缓存、不联网;没缓存时只列「生成模型」页已保存的模型。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules import rh_models  # noqa: E402

AI_MODELS = [
    {"id": "kling/v3/text-to-video", "name": "Kling 3.0 T2V", "category": "Kling > text-to-video",
     "task": "text-to-video", "kind": "video", "schema": {}},
    {"id": "vidu/q2/image-to-video", "name": "Vidu Q2 I2V", "category": "Vidu > image-to-video",
     "task": "image-to-video", "kind": "video", "schema": {}},
    # 文档分类挂错(挂在图像分类下),按端点末段归回视频
    {"id": "vidu/reference-to-video-q2-pro", "name": "Vidu Q2 Pro Ref", "category": "Vidu > reference-to-image",
     "task": "reference-to-image", "kind": "image", "schema": {}},
    {"id": "seedream-v5-lite/text-to-image", "name": "Seedream T2I", "category": "Seedream > text-to-image",
     "task": "text-to-image", "kind": "image", "schema": {}},
]
CN_MODELS = [
    {"id": "alibaba/wan-2.6/image-to-video", "name": "万相2.6-图生视频", "category": "万相 > image-to-video",
     "task": "image-to-video", "kind": "video", "schema": {}},
]


def _cache(tmp_path, monkeypatch):
    d = tmp_path / "rh_models"
    d.mkdir()
    (d / "ai.json").write_text(json.dumps({"fetched_at": 0, "models": AI_MODELS}), encoding="utf-8")   # 已过期也照用
    (d / "cn.json").write_text(json.dumps({"fetched_at": 0, "models": CN_MODELS}), encoding="utf-8")
    monkeypatch.setattr(rh_models, "CACHE_DIR", d)
    monkeypatch.setattr(rh_models, "_CACHED_LIST_MEMO", {})
    monkeypatch.setattr(rh_models, "_fetch_catalog", lambda site: (_ for _ in ()).throw(AssertionError("不该联网")))
    monkeypatch.delenv("RUNNINGHUB_API_KEY", raising=False)
    return d


def _cfg(site="ai", key=True, model="vidu/q2/image-to-video"):
    return {"video": {"provider": "volcengine", "volcengine": {"api_key": "k", "model": "doubao-seedance-2-5-260628"},
                      "rhapi": {"site": site, "api_key_ai": "kai" if key and site == "ai" else "",
                                "api_key_cn": "kcn" if key and site == "cn" else "", "model": model}}}


def test_cached_list_reads_cache_only(tmp_path, monkeypatch):
    d = _cache(tmp_path, monkeypatch)
    assert [r["id"] for r in rh_models.cached_list("ai", "video")] == [
        "kling/v3/text-to-video", "vidu/q2/image-to-video", "vidu/reference-to-video-q2-pro"]
    assert [r["id"] for r in rh_models.cached_list("ai", "image")] == ["seedream-v5-lite/text-to-image"]
    assert rh_models.cached_list("cn", "video")[0]["name"] == "万相2.6-图生视频"
    (d / "cn.json").unlink()
    monkeypatch.setattr(rh_models, "_CACHED_LIST_MEMO", {})
    assert rh_models.cached_list("cn", "video") == []        # 没缓存:空,不联网


def test_rhapi_in_episode_provider_options(tmp_path, monkeypatch):
    from services.runtime import core
    _cache(tmp_path, monkeypatch)
    opts = core.video_provider_options(_cfg())
    ids = [o["id"] for o in opts]
    assert "rhapi" in ids and ids.index("rhapi") == ids.index("comfyui") - 1     # 同「生成模型」页:RH 在 ComfyUI 前
    rh = opts[ids.index("rhapi")]
    assert rh["configured"] and rh["default_model"] == "vidu/q2/image-to-video"
    # 排序:多模态参考 → 图生 → 文生
    assert [m["id"] for m in rh["models"]] == ["vidu/reference-to-video-q2-pro", "vidu/q2/image-to-video",
                                               "kling/v3/text-to-video"]
    assert rh["models"][0]["label"] == "Vidu Q2 Pro Ref"
    assert set(core.episode_provider_models(_cfg(), "rhapi")) == {m["id"] for m in rh["models"]}
    # 当前站点没 Key → 置灰(另一站点的 Key 不算)
    cfg = _cfg(site="cn", key=False)
    cfg["video"]["rhapi"]["api_key_ai"] = "kai"
    assert not next(o for o in core.video_provider_options(cfg) if o["id"] == "rhapi")["configured"]
    # 换站点 = 换目录;已保存的模型不在该站目录里也列出(置首位)
    rh = next(o for o in core.video_provider_options(_cfg(site="cn")) if o["id"] == "rhapi")
    assert [m["id"] for m in rh["models"]] == ["vidu/q2/image-to-video", "alibaba/wan-2.6/image-to-video"]
    # 标签取当前站点目录名,目录里没有给端点 id
    monkeypatch.setattr(core, "load_genconfig", lambda: _cfg(site="cn"))
    assert core.video_model_label("alibaba/wan-2.6/image-to-video", "rhapi") == "万相2.6-图生视频"
    assert core.video_model_label("nope/x", "rhapi") == "nope/x"


def test_episode_and_group_candidates_on_rhapi(tmp_path, monkeypatch):
    from services.runtime import core
    _cache(tmp_path, monkeypatch)
    monkeypatch.setattr(core, "load_genconfig", lambda: _cfg())
    es = {}
    monkeypatch.setattr(core, "_epsettings_get", lambda project, ep: es)
    # 全局火山、集级切到 RH 某模型:组卡候选 = RH 目录
    es.update({"provider": "rhapi", "provider_override": True, "video_model": "kling/v3/text-to-video"})
    c = core.group_video_candidates("p", _cfg(), "ep01")
    assert (c["provider"], c["base_model"], c["provider_source"]) == ("rhapi", "kling/v3/text-to-video", "episode")
    assert c["overridable"] and not c["episode_warning"]
    assert [r["id"] for r in c["candidates"]] == ["vidu/reference-to-video-q2-pro", "vidu/q2/image-to-video",
                                                  "kling/v3/text-to-video"]
    # 当前站点没 Key:集级设定不生效,按全局执行并给出提示
    c = core.group_video_candidates("p", _cfg(key=False), "ep01")
    assert c["provider"] == "volcengine" and "rhapi" in c["episode_warning"]
    # 全局就是 RH:顶部二级下拉可为本集换 RH 模型
    cfg = _cfg()
    cfg["video"]["provider"] = "rhapi"
    es.clear()
    es.update({"provider": "rhapi", "provider_override": False, "video_model": "kling/v3/text-to-video"})
    c = core.group_video_candidates("p", cfg, "ep01")
    assert (c["global_model"], c["base_model"], c["base_source"]) == ("vidu/q2/image-to-video", "kling/v3/text-to-video", "episode")
    assert len(c["candidates"]) == 3


def test_genmedia_episode_override_to_rhapi(tmp_path, monkeypatch):
    """集级切到 RH:genmedia 按 RH 取整份视频配置(Key 按站点,model 槽 = 端点路径)。"""
    from modules import genmedia
    cfg_path = tmp_path / "genconfig.json"
    cfg_path.write_text(json.dumps(_cfg()))
    monkeypatch.setattr(genmedia, "CONFIG_PATH", cfg_path)
    monkeypatch.delenv("RUNNINGHUB_API_KEY", raising=False)
    sdir = tmp_path / "ep01"
    sdir.mkdir()
    (sdir / "episode.json").write_text(json.dumps({"provider": "rhapi", "provider_override": True,
                                                   "video_model": "kling/v3/text-to-video"}))
    r = genmedia.resolve_video_override(sdir, "grp001", "volcengine", "doubao-seedance-2-5-260628")
    assert (r["provider"], r["video_model"], r["source"]) == ("rhapi", "kling/v3/text-to-video", "episode") and not r["notes"]
    vc = genmedia.video_cfg_for(genmedia.get_config("video"), r)
    assert vc["provider"] == "rhapi" and vc["model"] == "kling/v3/text-to-video" and vc["api_key"] == "kai"
