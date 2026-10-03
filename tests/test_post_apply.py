# -*- coding: utf-8 -*-
"""后期处方台账 / 宿主 CLI code/post_apply.py / 后期预览 API 的离线测试(合成 lavfi 小视频,不依赖真实项目)。"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_TMP = tempfile.mkdtemp(prefix="va-post-")
os.environ.setdefault("VIDEOAGENTS_DATA_DIR", _TMP)
sys.path.insert(0, str(ROOT / "modules"))

import post_plan as pp  # noqa: E402

HAS_FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
DATA = Path(os.environ["VIDEOAGENTS_DATA_DIR"])


def _clip(path: Path, color: str, seconds: float = 2.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s=320x180:r=24:d={seconds}",
                    "-f", "lavfi", "-i", f"sine=f=440:d={seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(path)], check=True)


@pytest.fixture(scope="module")
def project():
    base = DATA / "projects" / "posttest"
    if base.exists():
        shutil.rmtree(base)
    (base / "directing" / "ep01").mkdir(parents=True)
    (base / "edit" / "ep01").mkdir(parents=True)
    groups = [("grp001", "SCN-0001", "S01", "red"), ("grp002", "SCN-0001", "S01", "blue"), ("grp003", "SCN-0002", "S02", "green")]
    sl = {"episode": "ep01", "generation_groups": [
        {"group_id": g, "scene_id": s, "scene_no": n, "shots": [f"sh{i + 1:03d}"], "total_duration_s": 2, "time_of_day": "夜"}
        for i, (g, s, n, _c) in enumerate(groups)],
        "shots": [{"shot_id": f"sh{i + 1:03d}", "duration_s": 2, "is_dialogue": i == 0,
                   "dialogue_lines": [{"speaker": "A", "text": "hi"}] if i == 0 else []} for i in range(3)]}
    (base / "directing" / "ep01" / "shot_list.json").write_text(json.dumps(sl, ensure_ascii=False))
    tracks, cum = [], 0.0
    for g, _s, _n, c in groups:
        if HAS_FFMPEG:
            _clip(base / "assets" / "clips" / "ep01" / f"{g}.mp4", c)
        tracks.append({"group_id": g, "src": f"assets/clips/ep01/{g}.mp4", "in": 0.0, "out": 2.0, "cum_start_s": cum, "cum_end_s": cum + 2})
        cum += 2
    (base / "edit" / "ep01" / "timeline.json").write_text(json.dumps({"episode": "ep01", "duration_s": cum, "tracks": {"video": tracks, "audio": []}, "transitions": []}))
    (base / "bible").mkdir(exist_ok=True)
    (base / "bible" / "color_script.json").write_text(json.dumps({"episodes": [{"episode": "ep01", "key_palette": ["#808080"],
                                                                   "segments": [{"id": "s1", "scenes": ["S01"], "palette": ["#C0A080", "#A08060"]}]}]}))
    (base / "settings.json").write_text("{}")
    return base


def cli(*args, expect=0):
    env = dict(os.environ, VIDEOAGENTS_DATA_DIR=str(DATA))
    p = subprocess.run([sys.executable, str(ROOT / "code" / "post_apply.py"), *args, "--project", "posttest", "--ep", "ep01"],
                       capture_output=True, text=True, env=env, cwd=ROOT)
    assert p.returncode == expect, p.stdout + p.stderr
    return p.stdout


# ---------------------------------------------------------------- 台账纯函数
def test_recipe_catalog_and_scope_rules():
    kinds = {k["id"] for k in pp.KINDS}
    assert {"basic", "lut", "scene_palette", "match_ref", "atmos", "deflicker", "delogo", "overlay_asset", "watermark",
            "upscale", "subtitle_style", "transition", "level", "mix_target"} <= kinds
    assert all(k["section"] in pp.SECTION_BY_ID for k in pp.KINDS)
    with pytest.raises(ValueError):
        pp.make_recipe("basic", {"level": "group", "group_id": "grp001"}, {}, note="")     # note 必填
    with pytest.raises(ValueError):
        pp.make_recipe("watermark", {"level": "group", "group_id": "grp001"}, {}, note="x")  # 水印只允许整集
    r = pp.make_recipe("basic", {"level": "range", "group_id": "grp001", "t0": 1, "t1": 1.5}, {"contrast": 9, "temperature": "abc"}, note="n")
    assert r["params"]["contrast"] == 1.8 and r["params"]["temperature"] == 6500 and r["scope"]["level"] == "range"


def test_effective_recipes_inherit_and_override():
    plan = pp.empty_plan("ep01")
    ep = pp.make_recipe("lut", {"level": "episode"}, {"preset": "bleach"}, note="全集")
    sc = pp.make_recipe("lut", {"level": "scene", "scene_id": "SCN-0001"}, {"preset": "warm_film"}, note="场次")
    g = pp.make_recipe("lut", {"level": "group", "group_id": "grp001"}, {"preset": "vintage"}, note="本组")
    plan["recipes"] += [ep, sc, g]
    rows = pp.effective_recipes(plan, {"group_id": "grp001", "scene_id": "SCN-0001"})
    assert [(r["scope"]["level"], r["inherited"], r["overridden"]) for r in rows] == [("episode", True, True), ("scene", True, True), ("group", False, False)]
    rows2 = pp.effective_recipes(plan, {"group_id": "grp009", "scene_id": "SCN-0002"})
    assert [r["scope"]["level"] for r in rows2] == ["episode"] and rows2[0]["overridden"] is False


def test_version_chain_and_cleanup(tmp_path):
    plan = pp.empty_plan("ep01")
    for v in range(1, 5):
        f = tmp_path / f"v{v}.mp4"
        f.write_bytes(b"x" * (100 + v))
        pp.register_version(plan, tmp_path, "ep01", "grp001", f.name, [f"r{v}"], v - 1)
        if v in (1, 2, 3):
            pp.adopt_version(plan, "grp001", v)
    assert pp.current_version(plan, "grp001") == 3
    # v4 未采纳但有「已出片」处方引用 → 签字清理时保留(不替用户做 A|B 决定)
    r4 = pp.make_recipe("basic", {"level": "group", "group_id": "grp001"}, {}, note="pending")
    pp.set_status(r4, "applied", output={"versions": {"grp001": {"v": 4, "file": "v4.mp4"}}})
    plan["recipes"].append(r4)
    cleaned = pp.cleanup_versions(plan, tmp_path, "ep01")
    assert cleaned == ["grp001/v1"] and not (tmp_path / "v1.mp4").exists() and (tmp_path / "v4.mp4").exists()
    with pytest.raises(ValueError):
        pp.adopt_version(plan, "grp001", 1)     # 已清理的版本不能再采纳
    fp1 = pp.plan_fingerprint(plan)
    pp.adopt_version(plan, "grp001", 0)
    assert pp.plan_fingerprint(plan) != fp1


# ---------------------------------------------------------------- CLI(需 ffmpeg)
@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe 不可用")
def test_cli_apply_adopt_check_build_cut(project):
    plan = pp.load_plan(project, "ep01")
    r_basic = pp.make_recipe("basic", {"level": "group", "group_id": "grp001", "scene_id": "SCN-0001"}, {"saturation": 0.5}, note="降饱和")
    r_pal = pp.make_recipe("scene_palette", {"level": "scene", "scene_id": "SCN-0001"}, {"strength": 0.5}, note="色板")
    r_atm = pp.make_recipe("atmos", {"level": "range", "group_id": "grp003", "t0": 0.5, "t1": 1.5}, {"glow": 0.5, "vignette": 0.5, "grain": 0.3, "flicker": 0.5, "tint": "candle"}, note="氛围")
    r_tr = pp.make_recipe("transition", {"level": "group", "group_id": "grp002"}, {"type": "dissolve", "duration_s": 0.5}, note="叠化")
    r_agent = pp.make_recipe("upscale", {"level": "group", "group_id": "grp002"}, {}, note="超分")
    plan["recipes"] += [r_basic, r_pal, r_atm, r_tr, r_agent]
    pp.save_plan(project, "ep01", plan)

    out = cli("apply", "--recipe", r_basic["id"], "--preview")
    assert "preview_" in out and (project / "assets/post/ep01/grp001/refs" / f"preview_{r_basic['id']}.mp4").is_file()
    cli("apply", "--recipe", r_basic["id"])
    cli("apply", "--recipe", r_pal["id"])           # 场次作用域 → grp001/grp002 各出一版
    cli("apply", "--recipe", r_atm["id"])
    plan = pp.load_plan(project, "ep01")
    assert pp.find_recipe(plan, r_basic["id"])["status"] == "applied"
    assert set(pp.find_recipe(plan, r_pal["id"])["output"]["versions"]) == {"grp001", "grp002"}
    assert [v["v"] for v in pp.group_versions(plan, "grp001")] == [1, 2]      # 未采纳时都基于 v0

    # agent 类:register 登记外部产物
    ext = project / "assets/post/ep01/grp002/agent_x.mp4"
    shutil.copy(project / "assets/clips/ep01/grp002.mp4", ext)
    cli("register", "--recipe", r_agent["id"], "--file", "assets/post/ep01/grp002/agent_x.mp4")
    cli("adopt", "--recipe", r_basic["id"])
    cli("adopt", "--recipe", r_tr["id"])
    sl = json.loads((project / "directing/ep01/shot_list.json").read_text())
    assert [g for g in sl["generation_groups"] if g["group_id"] == "grp002"][0]["transition_in"]["type"] == "dissolve"
    cli("rollback", "--group", "grp003", "--to", "1")
    plan = pp.load_plan(project, "ep01")
    assert plan["current"] == {"grp001": 1, "grp003": 1}
    # 采纳后再出片基于 v1
    cli("apply", "--recipe", r_atm["id"], "--group", "grp003")
    plan = pp.load_plan(project, "ep01")
    assert pp.group_versions(plan, "grp003")[-1]["base_v"] == 1

    out = cli("check")
    chk = json.loads((project / "edit/ep01/post_check.json").read_text())
    names = {i["name"]: i["status"] for i in chk["check"]["items"]}
    assert names["post_plan_applied"] == "PASS" and names["transitions_synced"] == "PASS" and names["post_no_pending"] == "WARN"
    assert chk["plan_fingerprint"] == pp.plan_fingerprint(plan)

    cli("build-cut")
    cli("sync-timeline")
    tl = json.loads((project / "edit/ep01/timeline.json").read_text())
    assert (project / "edit/ep01/cut_post.mp4").is_file() and (project / "edit/ep01/timeline.pre_post.json").is_file()
    assert tl["tracks"]["video"][0]["src"].startswith("assets/post/ep01/_cut/") and tl["tracks"]["video"][0]["src_orig"] == "assets/clips/ep01/grp001.mp4"
    assert tl["post"]["versions"]["grp001"]["v"] == 1
    # 弃用 → 指针退回 base_v;删除转场处方 → shot_list 还原
    cli("discard", "--recipe", r_basic["id"])
    cli("discard", "--recipe", r_tr["id"])
    plan = pp.load_plan(project, "ep01")
    assert plan["current"]["grp001"] == 0
    sl = json.loads((project / "directing/ep01/shot_list.json").read_text())
    assert not [g for g in sl["generation_groups"] if g["group_id"] == "grp002"][0].get("transition_in")


# ---------------------------------------------------------------- core API
@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe 不可用")
def test_core_preview_and_recipe_endpoints(project):
    import asyncio
    from services.runtime import core

    d = core._preview_post("posttest", "ep01")
    assert d["ep"] == "ep01" and [g["group_id"] for g in d["groups"]] == ["grp001", "grp002", "grp003"]
    assert d["scenes"][0]["palette"] == ["#C0A080", "#A08060"] and d["groups"][0]["versions"][0]["v"] == 0
    assert d["lanes"]["dialogue"][0]["group_id"] == "grp001"
    j = asyncio.run(core.api_post_recipe_create("posttest", "ep01", {"kind": "basic", "scope": {"level": "group", "group_id": "grp003"}, "params": {}, "note": "x"}))
    rid = j["recipe"]["id"]
    with pytest.raises(core.ServiceError):
        asyncio.run(core.api_post_recipe_create("posttest", "ep01", {"kind": "basic", "scope": {"level": "group", "group_id": "grp003"}, "params": {}, "note": ""}))
    j = asyncio.run(core.api_post_recipe_update("posttest", "ep01", rid, {"params": {"contrast": 1.2}}))
    assert j["recipe"]["params"]["contrast"] == 1.2
    j = asyncio.run(core.api_post_recipe_action("posttest", "ep01", rid, "copy", {"groups": ["grp001", "grp002"]}))
    assert len(j["created"]) == 2
    fr = asyncio.run(core.api_post_frame("posttest", "ep01", {"group_id": "grp003", "t": 0.5}))
    assert (project / fr["path"]).is_file() and fr["url"].startswith("/projects/posttest/")
    pre = core._post_precheck_sync("posttest", "ep01", run_check=False)
    assert pre["blocked"] is False and "summary" in pre
    asyncio.run(core.api_post_recipe_delete("posttest", "ep01", rid))
    with pytest.raises(core.ServiceError):
        asyncio.run(core.api_post_recipe_delete("posttest", "ep01", rid))


def test_timeline_track_key_variants():
    """后期页组起点须兼容各版剪辑 agent 的 timeline 字段名(liaozhai2 曾因只认 cum_start_s 全组叠在 0 秒)。"""
    from services.runtime import core
    # dzg6 式
    assert core._tl_entry_span({"cum_start_s": 12.0, "cum_end_s": 22.0, "in": 0, "out": 10}) == (12.0, 10.0)
    # liaozhai2 式:timeline_in_s/out_s + in_s/out_s
    assert core._tl_entry_span({"in_s": 0.0, "out_s": 11.0, "timeline_in_s": 12.0, "timeline_out_s": 23.0}) == (12.0, 11.0)
    # thedoor 式:in/out + timeline_in(无终点)→ 起点取 timeline_in,时长取 out-in
    assert core._tl_entry_span({"in": 0, "out": 8.0, "timeline_in": 30.0}) == (30.0, 8.0)
    # 只有 in/out → 无起点,交给按序累加;speed 2x 折半
    assert core._tl_entry_span({"in": 0, "out": 8.0, "speed": 2.0}) == (None, 4.0)
    # 只有 duration_s
    assert core._tl_entry_span({"duration_s": 3.0}) == (None, 3.0)
    tl = {"tracks": {"video": [{"id": "T", "type": "title", "in": 0, "out": 2.0},
                               {"group_id": "grp001", "in": 0, "out": 10.0},
                               {"group_id": "grp002", "in": 0, "out": 5.0}]}}
    assert core._post_episode_duration(tl, []) == 17.0
    assert core._post_episode_duration({"duration_s": 99.0}, []) == 99.0


# ---------------------------------------------------------------- 插黑 / 定格 + timemap(2026-09-17)
@pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg")
def test_pad_recipe_insert_hold_and_timemap(project):
    import asyncio
    from services.runtime import core
    import timemap as tm

    # ① 组边界:transition 处方带垫片,创建即提交 → 回写 shot_list.transition_in
    j = asyncio.run(core.api_post_recipe_create("posttest", "ep01", {
        "kind": "transition", "scope": {"level": "group", "group_id": "grp002"},
        "params": {"type": "hard_cut", "hold_s": 0.5, "freeze_s": 0.25, "hold_audio": "sustain"}, "note": "beat", "adopt": True}))
    assert j["adopted"] is True
    sl = json.loads((project / "directing" / "ep01" / "shot_list.json").read_text())
    tin = next(g for g in sl["generation_groups"] if g["group_id"] == "grp002")["transition_in"]
    assert tin["type"] == "hard_cut" and tin["hold_s"] == 0.5 and tin["freeze_s"] == 0.25 and "duration_s" not in tin
    # grp001 是对白组且对白覆盖整组 → 前组尾有语音,延续策略应被宿主改为淡出
    assert tin["hold_audio"] == "fade"
    # 黑场停留配 dissolve 非法 → 400
    with pytest.raises(core.ServiceError):
        asyncio.run(core.api_post_recipe_create("posttest", "ep01", {
            "kind": "transition", "scope": {"level": "group", "group_id": "grp003"},
            "params": {"type": "dissolve", "duration_s": 0.5, "hold_s": 0.5}, "note": "x", "adopt": True}))
    # post_ok 的 transitions_synced 认垫片字段
    out = cli("check")
    assert "transitions_synced" in out and "不一致" not in out

    # ② 组内当前时刻:insert-hold → 新版本(时长 +0.75s)带 time_ops
    r = asyncio.run(core.api_post_insert_hold("posttest", "ep01", {"group_id": "grp003", "base_v": 0, "t": 1.0,
                                                                     "freeze_s": 0.25, "hold_s": 0.5, "audio": "mute"}))
    assert r["duration"] == pytest.approx(2.75, abs=0.05) and r["time_ops"][0]["out_len"] == pytest.approx(0.75)
    plan = pp.load_plan(project, "ep01")
    ver = next(x for x in pp.group_versions(plan, "grp003") if x["v"] == r["v"])
    assert ver["created_by"] == "pad" and ver["time_ops"] and ver["pad"]["audio"] == "mute"
    # 版本链上再删一段(基准 = 刚插黑的版本,0.5–1.0s)→ effective ops 合成
    r2 = asyncio.run(core.api_post_cutout("posttest", "ep01", {"group_id": "grp003", "base_v": r["v"], "cuts": [{"t0": 0.5, "t1": 1.0}]}))
    plan = pp.load_plan(project, "ep01")
    eff = pp.effective_time_ops(plan, "grp003", r2["v"])
    assert tm.total_delta(eff) == pytest.approx(0.75 - 0.5, abs=1e-3)
    pp.adopt_version(plan, "grp003", r2["v"])
    pp.save_plan(project, "ep01", plan)

    # ③ sync-timeline 写 timemap.json:grp003 起点 4.0s → ops 平移到集基准
    cli("build-cut")
    cli("sync-timeline")
    tmj = json.loads((project / "edit" / "ep01" / "timemap.json").read_text())
    ops = tmj["ops"]
    assert ops and all(4.0 <= o["src_t0"] <= 6.0 for o in ops) and tmj["delta_s"] == pytest.approx(0.25, abs=1e-3)
    d = core._preview_post("posttest", "ep01")
    v = next(x for x in d["groups"][2]["versions"] if x["v"] == r2["v"])
    assert v["time_ops"]


@pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg")
def test_insert_clip_start_end_here(project):
    """插段(2026-09-30,取代换段):导入版本整段插到最前面 / 最后面 / 当前时刻,时长变长并登记 time_ops。"""
    import asyncio
    from services.runtime import core
    import post_fx
    import timemap as tm

    src = project / "scratch_ins.mp4"
    _clip(src, "yellow", 1.0)
    imp = asyncio.run(core.api_post_import("posttest", "ep01", {"group_id": "grp001", "path": str(src)}))
    for pos, t, want_t in (("start", 0, 0.0), ("end", 0, 2.0), ("here", 0.5, 0.5)):
        r = asyncio.run(core.api_post_insert_clip("posttest", "ep01", {"group_id": "grp001", "base_v": 0, "ins_v": imp["v"],
                                                                         "pos": pos, "t": t}))
        assert r["duration"] == pytest.approx(3.0, abs=0.05) and r["t"] == pytest.approx(want_t, abs=0.05)
        assert r["time_ops"][0]["out_len"] == pytest.approx(1.0, abs=0.05) and tm.total_delta(r["time_ops"]) > 0.9
        out = project / r["file"]
        # 插入块是黄色(R、G 高,B 低),其余是母本红色
        mid = post_fx.frame_stats(out, want_t + 0.5)
        assert mid["g"] > 0.6 and mid["b"] < 0.3
        plan = pp.load_plan(project, "ep01")
        ver = next(x for x in pp.group_versions(plan, "grp001") if x["v"] == r["v"])
        assert ver["created_by"] == "insert" and ver["insert"]["ins_v"] == imp["v"] and ver["insert"]["pos"] == pos
    with pytest.raises(core.ServiceError):
        asyncio.run(core.api_post_insert_clip("posttest", "ep01", {"group_id": "grp001", "base_v": 0, "ins_v": imp["v"], "pos": "middle"}))

    # 宽高比不同(竖屏插横屏):预检报 aspect_differs,三种填充方式都能出片并记入版本
    tall = project / "scratch_tall.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=yellow:s=90x160:r=24:d=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(tall)], check=True)
    imp2 = asyncio.run(core.api_post_import("posttest", "ep01", {"group_id": "grp001", "path": str(tall)}))
    pr = asyncio.run(core.api_post_insert_probe("posttest", "ep01", "grp001", 0, imp2["v"]))
    assert pr["aspect_differs"] is True and pr["ins"]["width"] == 90 and pr["base"]["width"] == 320
    assert pr["upscale"]["crop"] > pr["upscale"]["pad"] == pr["upscale"]["blur"]
    edge = {}
    for fit in ("pad", "crop", "blur"):
        r = asyncio.run(core.api_post_insert_clip("posttest", "ep01", {"group_id": "grp001", "base_v": 0, "ins_v": imp2["v"],
                                                                         "pos": "start", "fit": fit}))
        assert r["fit"] == fit and r["duration"] == pytest.approx(3.0, abs=0.05)
        edge[fit] = post_fx.frame_stats(project / r["file"], 0.5)
    # 同为黄色:黑边版平均亮度最低,裁切版铺满最亮
    assert edge["pad"]["luma"] < edge["blur"]["luma"] <= edge["crop"]["luma"] + 1e-3
    with pytest.raises(core.ServiceError):
        asyncio.run(core.api_post_insert_clip("posttest", "ep01", {"group_id": "grp001", "base_v": 0, "ins_v": imp2["v"], "fit": "stretch"}))


@pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg")
def test_slow_motion_recipe_retime_register_and_build_cut(project):
    """慢动作处方(2026-09-23):agent 处方可以改时长——slowmo 宿主出片 → 版本 time_ops → 采纳 → build-cut 按映射后的入出点
    归一(变长版本不再被截回原长)→ sync-timeline timemap 汇总;register 对改时长产物的核对与 retime_auto 兜底。"""
    import asyncio
    from services.runtime import core
    import timemap as tm
    from modules import post_fx

    # grp002 第 0.5–1.5 秒 ×2 → 组 2.0s → 3.0s
    j = asyncio.run(core.api_post_recipe_create("posttest", "ep01", {
        "kind": "slow_motion", "scope": {"level": "range", "group_id": "grp002", "t0": 0.5, "t1": 1.5},
        "params": {"rate": "2", "audio": "mute"}, "note": "落刀一瞬放慢"}))
    rid = j["recipe"]["id"]
    assert j["recipe"]["exec"] == "agent" and j["recipe"]["params"]["rate"] == "2"
    # 派单指令走 slowmo 路线而不是「严禁改时长」
    plan = pp.load_plan(project, "ep01")
    groups, _ = core._post_groups(project, "ep01", plan)
    agent, msg = core._post_agent_message(project, "ep01", plan, pp.find_recipe(plan, rid), groups)
    assert agent == pp.AGENT_ID and "slowmo" in msg and "严禁改时长" not in msg
    # 用 RIFE 补帧片段登记(片段 = 该时间段 48fps)
    seg = project / "assets" / "post" / "ep01" / "grp002" / "refs" / "rife_48.mp4"
    seg.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(project / "assets/clips/ep01/grp002.mp4"), "-vf",
                    "trim=start=0.5:end=1.5,setpts=PTS-STARTPTS,fps=48", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(seg)], check=True)
    out = cli("slowmo", "--recipe", rid, "--group", "grp002", "--interp", str(seg.relative_to(project)))
    assert "已出片" in out and "3.00s" in out
    plan = pp.load_plan(project, "ep01")
    r = pp.find_recipe(plan, rid)
    v = r["output"]["versions"]["grp002"]["v"]
    ver = next(x for x in pp.group_versions(plan, "grp002") if x["v"] == v)
    assert ver["time_ops"] == pp.recipe_time_ops(r, 2.0, 24) and ver["time_ops"][0]["out_len"] == pytest.approx(2.0)
    assert post_fx.probe(project / ver["file"])["duration"] == pytest.approx(3.0, abs=0.05)
    # register 核对:改时长类处方产物若不等于源 + 变长量 → 拒登记(退出码 1)
    bad = project / "assets" / "post" / "ep01" / "grp002" / "agent_bad.mp4"
    shutil.copy2(project / "assets/clips/ep01/grp002.mp4", bad)
    out = cli("register", "--recipe", rid, "--file", str(bad.relative_to(project)), "--group", "grp002", expect=1)
    assert "≠" in out
    # 采纳 → build-cut:grp002 段按映射后的入出点归一成 3.0s(以前会被截回 2.0s)
    cli("adopt", "--recipe", rid)
    cli("build-cut")
    segf = project / "assets" / "post" / "ep01" / "_cut" / "grp002.mp4"
    assert post_fx.probe(segf)["duration"] == pytest.approx(3.0, abs=0.05)
    cli("sync-timeline")
    tmj = json.loads((project / "edit" / "ep01" / "timemap.json").read_text())
    sm = [o for o in tmj["ops"] if o.get("kind") == "slow_motion"]
    assert sm and sm[0]["src_t0"] == pytest.approx(2.5) and sm[0]["src_t1"] == pytest.approx(3.5) and sm[0]["group_id"] == "grp002"
    # 非改时长做法改了时长又没给 --time-ops → retime_auto 兜底(WARN 但登记)
    j2 = asyncio.run(core.api_post_recipe_create("posttest", "ep01", {
        "kind": "upscale", "scope": {"level": "group", "group_id": "grp001"}, "params": {}, "note": "x"}))
    longer = project / "assets" / "post" / "ep01" / "grp001" / "agent_long.mp4"
    longer.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(project / "assets/clips/ep01/grp001.mp4"), "-vf", "setpts=1.25*PTS",
                    "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(longer)], check=True)
    out = cli("register", "--recipe", j2["recipe"]["id"], "--file", str(longer.relative_to(project)), "--group", "grp001")
    assert "retime_auto" in out
    plan = pp.load_plan(project, "ep01")
    v1 = pp.group_versions(plan, "grp001")[-1]
    assert v1["time_ops"][0]["kind"] == "retime_auto" and tm.total_delta(v1["time_ops"]) == pytest.approx(0.5, abs=0.05)


# ---------------------------------------------------------------- 叙事块作用域 / propose / soften(2026-09-17)
BY = "10-editing/grade-planner"


@pytest.fixture(scope="module")
def grade_project():
    base = DATA / "projects" / "gradetest"
    if base.exists():
        shutil.rmtree(base)
    (base / "directing" / "ep01").mkdir(parents=True)
    # 同一场景 SCN-A 被现实段(grp001/grp004)与闪回段(grp002)共用;闪回块跨两个场景
    rows = [("grp001", "SCN-A", None, "gray"), ("grp002", "SCN-A", "start", "gray"), ("grp003", "SCN-B", "end", "gray"), ("grp004", "SCN-A", None, "gray")]
    gg = []
    for g, s, role, c in rows:
        d = {"group_id": g, "scene_id": s, "scene_no": "S01", "shots": [], "total_duration_s": 2}
        if role:
            d["narrative_block"] = {"id": "fb1", "kind": "flashback", "role": role}
        gg.append(d)
        if HAS_FFMPEG:
            _clip(base / "assets" / "clips" / "ep01" / f"{g}.mp4", c)
    (base / "directing" / "ep01" / "shot_list.json").write_text(json.dumps({"episode": "ep01", "generation_groups": gg, "shots": []}))
    (base / "settings.json").write_text("{}")
    return base


def gcli(*args, expect=0):
    env = dict(os.environ, VIDEOAGENTS_DATA_DIR=str(DATA))
    p = subprocess.run([sys.executable, str(ROOT / "code" / "post_apply.py"), *args, "--project", "gradetest", "--ep", "ep01"],
                       capture_output=True, text=True, env=env, cwd=ROOT)
    assert p.returncode == expect, p.stdout + p.stderr
    return p.stdout


def test_block_scope_matching_and_inheritance():
    sc = pp.normalize_scope({"block_id": "fb1"})
    assert sc == {"level": "block", "block_id": "fb1"}
    with pytest.raises(ValueError):
        pp.normalize_scope({"level": "block"})
    fb = {"group_id": "grp002", "scene_id": "SCN-A", "narrative_block": {"id": "fb1", "kind": "flashback"}}
    real = {"group_id": "grp001", "scene_id": "SCN-A"}
    assert pp.scope_matches_group(sc, fb) and not pp.scope_matches_group(sc, real)
    assert pp.scope_matches_group(sc, {"group_id": "grp003", "block_id": "fb1"})      # 已摊平的组行
    plan = pp.empty_plan("ep01")
    plan["recipes"] += [pp.make_recipe("basic", {"level": "scene", "scene_id": "SCN-A"}, {}, note="场"),
                        pp.make_recipe("basic", sc, {"contrast": 0.9}, note="块"),
                        pp.make_recipe("soften", sc, {"radius": 99, "strength": 0.4}, note="柔化")]
    rows = pp.effective_recipes(plan, fb)
    assert [(r["kind"], r["scope"]["level"], r["inherited"], r["overridden"]) for r in rows] == [
        ("basic", "scene", True, True), ("basic", "block", True, False), ("soften", "block", True, False)]
    assert rows[2]["params"]["radius"] == 8                                          # 参数按目录收敛
    assert [r["scope"]["level"] for r in pp.effective_recipes(plan, real)] == ["scene"]
    with pytest.raises(ValueError):
        pp.make_recipe("watermark", sc, {}, note="x")                                # 不支持 block 的做法照旧拒绝


def test_propose_is_idempotent_and_never_touches_decided():
    plan = pp.empty_plan("ep01")
    sc = {"level": "block", "block_id": "fb1"}
    with pytest.raises(ValueError):
        pp.propose_recipe(plan, "basic", sc, {}, {}, "n", "user")
    r1, how1 = pp.propose_recipe(plan, "basic", sc, {"contrast": 0.9}, {}, "意图", BY)
    r2, how2 = pp.propose_recipe(plan, "basic", sc, {"contrast": 0.9}, {}, "意图", BY)
    assert (how1, how2) == ("created", "unchanged") and r1 is r2 and r1["created_by"] == BY
    pp.set_status(r1, "applied", output={"versions": {"grp002": {"v": 1, "file": "x"}}})
    r3, how3 = pp.propose_recipe(plan, "basic", sc, {"contrast": 0.95}, {}, "意图", BY)
    assert how3 == "updated" and r3 is r1 and r1["status"] == "draft" and r1["params"]["contrast"] == 0.95
    pp.set_status(r1, "adopted")
    r4, how4 = pp.propose_recipe(plan, "basic", sc, {"contrast": 0.8}, {}, "意图", BY)
    assert how4 == "created" and r4 is not r1 and r1["params"]["contrast"] == 0.95    # 已采纳的不动
    assert len(plan["recipes"]) == 2


@pytest.mark.skipif(not HAS_FFMPEG, reason="needs ffmpeg")
def test_cli_blocks_propose_apply_verify(grade_project):
    out = json.loads(gcli("blocks", "--json"))
    assert [(b["block_id"], b["kind"], b["groups"], b["scene_ids"], b["neighbors"]) for b in out["blocks"]] == [
        ("fb1", "flashback", ["grp002", "grp003"], ["SCN-A", "SCN-B"], ["grp001", "grp004"])]
    gcli("propose", "--kind", "basic", "--block", "nope", "--note", "n", "--by", BY, expect=2)
    gcli("propose", "--kind", "basic", "--block", "fb1", "--note", "", "--by", BY, expect=2)          # note 必填
    gcli("propose", "--kind", "basic", "--block", "fb1", "--scene", "SCN-A", "--note", "n", "--by", BY, expect=2)
    ids = []
    for kind, params in (("basic", {"contrast": 0.9, "temperature": 6350, "brightness": -0.12}),
                         ("scene_palette", {"palette": ["#D8C9AE", "#8C7355", "#3A3128"], "strength": 0.4}),
                         ("soften", {"radius": 2, "strength": 0.4}), ("atmos", {"grain": 0.2})):
        j = json.loads(gcli("propose", "--kind", kind, "--block", "fb1", "--params", json.dumps(params), "--note", "flashback_variant", "--by", BY).splitlines()[-1])
        assert j["result"] == "created" and j["groups"] == 2
        ids.append(j["recipe"])
    assert "unchanged" in gcli("propose", "--kind", "atmos", "--block", "fb1", "--params", '{"grain": 0.2}', "--note", "flashback_variant", "--by", BY)
    gcli("blocks", "--verify", "--by", BY, expect=1)                                  # 草稿未出片 → FAIL
    gcli("apply", *sum((["--recipe", i] for i in ids), []))
    plan = pp.load_plan(grade_project, "ep01")
    assert set(plan["versions"]) == {"grp002", "grp003"}                              # 现实段 grp001/grp004(同场景 SCN-A)不受影响
    assert all(pp.find_recipe(plan, i)["status"] == "applied" for i in ids)
    assert plan["versions"]["grp002"][0]["recipes"] == ids and not plan["current"]    # 一次串链出一个版本;不采纳、指针不动
    assert "grade_plan_proposed: PASS" in gcli("blocks", "--verify", "--by", BY)
    st = json.loads(gcli("blocks", "--json", "--stats"))["blocks"][0]
    assert st["luma"]["step_pct"] > 5 and st["versions"]["grp002"] == {"current": 0, "latest": 1}
    # 色温口径:数值越低越暖(R 升 B 降)
    import post_fx as fx
    a = fx.frame_stats(grade_project / "assets/clips/ep01/grp002.mp4", 1.0)
    b = fx.frame_stats(grade_project / plan["versions"]["grp002"][0]["file"], 1.0)
    assert b["r"] - b["b"] > a["r"] - a["b"]
    # 时长不变
    assert abs(fx.probe(grade_project / plan["versions"]["grp002"][0]["file"])["duration"] - 2.0) < 0.05
    # 机检:闪回块未采纳 → flashback_graded WARN;采纳任一处方后 PASS
    assert "flashback_graded: WARN" in gcli("check")
    gcli("adopt", "--recipe", ids[0])
    assert "flashback_graded: PASS" in gcli("check")
