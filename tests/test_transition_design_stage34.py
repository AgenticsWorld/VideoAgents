# -*- coding: utf-8 -*-
"""过场设计三期 / 四期(2026-09-26):生成式桥接 bridge(设计 / 首尾帧 / clip 机检 / build 消费)、成对运镜 motion_pair(契约 / prompt 同步 / 机检)、
音先入 audio_lead_s(契约 / 混音边界层暴露与指纹)。合成小项目。"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_TMP = tempfile.mkdtemp(prefix="va-td34-")
os.environ.setdefault("VIDEOAGENTS_DATA_DIR", _TMP)
os.environ["VIDEOAGENTS_UI_LANG"] = "zh"
sys.path.insert(0, str(ROOT / "modules"))
sys.path.insert(0, str(ROOT / "code"))
sys.path.insert(0, str(ROOT))

import check_generation_groups as cgg  # noqa: E402
from modules import motion_pairs as mpx  # noqa: E402
from modules import transition_design as td  # noqa: E402

HAS_FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
DATA = Path(os.environ["VIDEOAGENTS_DATA_DIR"])
BID_BLOCK = "B-grp002-grp003"      # 进闪回块
BID_SCENE = "B-grp003-grp004"      # 出块 + 换场景


def _clip(path: Path, color: str, seconds: float = 2.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s=320x180:r=24:d={seconds}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)


def _project(name: str, mode: str, block: bool = True, **extra) -> Path:
    base = DATA / "projects" / name
    if base.exists():
        shutil.rmtree(base)
    (base / "directing" / "ep01").mkdir(parents=True)
    (base / "bible" / "scenes").mkdir(parents=True)
    (base / "assets" / "prompts" / "ep01").mkdir(parents=True)
    (base / "settings.json").write_text(json.dumps({"output": {"aspect": "16:9"}, "transitions": {"mode": mode, **extra}}, ensure_ascii=False))
    (base / "bible" / "scenes" / "index.json").write_text(json.dumps({"scenes": [
        {"id": "SCN-0001", "name": "书房"}, {"id": "SCN-0002", "name": "南天门"}]}, ensure_ascii=False))
    shots = [{"shot_id": f"sh00{i}", "scene_no": "S01", "size": "中景", "duration_s": 2.0} for i in range(1, 9)]
    groups = [
        {"group_id": "grp001", "scene_id": "SCN-0001", "scene_no": "S01", "shots": ["sh001", "sh002"], "total_duration_s": 4, "time_of_day": "日间", "characters_union": ["CHAR-0001"]},
        {"group_id": "grp002", "scene_id": "SCN-0001", "scene_no": "S01", "shots": ["sh003", "sh004"], "total_duration_s": 4, "time_of_day": "日间", "characters_union": ["CHAR-0001"]},
        {"group_id": "grp003", "scene_id": "SCN-0001", "scene_no": "S01", "shots": ["sh005", "sh006"], "total_duration_s": 4, "time_of_day": "日间", "characters_union": ["CHAR-0001"],
         **({"narrative_block": {"id": "fb-1", "kind": "flashback", "role": "single"}} if block else {})},
        {"group_id": "grp004", "scene_id": "SCN-0002", "scene_no": "S01", "shots": ["sh007", "sh008"], "total_duration_s": 4, "time_of_day": "日间", "characters_union": ["CHAR-0002"]},
    ]
    (base / "directing" / "ep01" / "shot_list.json").write_text(json.dumps(
        {"episode": "ep01", "budget_s": 60, "shots": shots, "generation_groups": groups}, ensure_ascii=False, indent=2))
    return base


def _row(base, bid):
    return next(b for b in td.load_design(base, "ep01")["boundaries"] if b["id"] == bid)


def _sl(base):
    return json.loads((base / "directing" / "ep01" / "shot_list.json").read_text())


# ---------------------------------------------------------------- 三期:桥接

def test_bridge_design_in_cinematic_block_enter_and_clip_pipeline():
    base = _project("td34-bridge", "cinematic")
    rows = td.diagnose(base, "ep01")
    r = next(b for b in rows if b["id"] == BID_BLOCK)
    assert r["diagnosis"]["class"] == "block_enter" and r["scene_from"]["name"] == "书房"
    td.propose(base, "ep01")
    b = _row(base, BID_BLOCK)
    br = [x for x in b["design"]["inserts"] if x["kind"] == "bridge"]
    assert br, b["design"]
    br = br[0]
    assert br["file"] == f"assets/transitions/ep01/{BID_BLOCK}.bridge.mp4"
    assert br["first_frame"].endswith(".bridge.first.jpg") and br["last_frame"].endswith(".bridge.last.jpg")
    assert "记忆" in br["prompt"] and "不出现任何新的人物" in br["prompt"]
    assert any("白场" in a["label"] for a in b["alternatives"])       # 不生成的候选仍在
    # 契约:bridge 须给 file;与 motion_pair 互斥
    bad = json.loads(json.dumps(b["design"]))
    bad["inserts"][0].pop("file")
    errs = cgg.check_transitions({"budget_s": 60, "generation_groups": [{"group_id": "a", "shots": ["s1"]}, {"group_id": "b", "shots": ["s2"], "transition_in": bad}]}, 10.0)
    assert any("bridge 须给 file" in e for e in errs)
    bad2 = json.loads(json.dumps(b["design"]))
    bad2["motion_pair"] = {"out": "pan_right", "in": "pan_right"}
    errs = cgg.check_transitions({"budget_s": 60, "generation_groups": [{"group_id": "a", "shots": ["s1"]}, {"group_id": "b", "shots": ["s2"], "transition_in": bad2}]}, 10.0)
    assert any("互斥" in e for e in errs)
    # 首尾帧:两侧组视频未出 → 准备不了
    td.accept_all_proposed(base, "ep01", by="sign:g6")
    rows = td.clips_needed(base, "ep01")
    brow = next(x for x in rows if x["kind"] == "bridge")
    assert brow["still"] == brow["first_frame"] and not brow["still_exists"]
    done = td.prepare_clip_stills(base, "ep01", rows)
    assert not [x for x in done if x["kind"] == "bridge"]
    assert "组视频尚未生成" in brow.get("still_error", "")
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg 不可用")
    _clip(base / "assets" / "clips" / "ep01" / "grp002.mp4", "red", 3.0)
    _clip(base / "assets" / "clips" / "ep01" / "grp003.mp4", "blue", 3.0)
    rows = td.clips_needed(base, "ep01")
    done = td.prepare_clip_stills(base, "ep01", rows)
    brow = next(x for x in done if x["kind"] == "bridge")
    assert (base / brow["first_frame"]).is_file() and (base / brow["last_frame"]).is_file()
    ok, items = td.check_clips(base, "ep01")
    assert not ok and items[0]["result"] == "FAIL" and items[1]["result"] == "PASS"     # 缺 clip、首尾帧齐
    _clip(base / brow["file"], "green", 4.0)
    ok, items = td.check_clips(base, "ep01")
    assert ok, items
    # build 消费桥接 clip
    import render_transitions as rt
    for gid, c in (("grp001", "gray"), ("grp004", "white")):
        _clip(base / "assets" / "clips" / "ep01" / f"{gid}.mp4", c, 3.0)
    built = rt.do_build(base, "ep01", None, log=lambda *a, **k: None)
    ins = [x for x in built[BID_BLOCK]["inserts"] if x["kind"] == "bridge"][0]
    assert not ins.get("missing") and Path(ins["file"]).is_file() and ins["frames"] == round(ins["duration_s"] * 24)
    pl = td.payload(base, "ep01")
    prow = next(x for x in pl["boundaries"] if x["id"] == BID_BLOCK)
    c = next(x for x in prow["clips"] if x["kind"] == "bridge")
    assert c["exists"] and c["still_url"] and c["last_url"]


def test_classic_never_proposes_bridge():
    base = _project("td34-classic", "classic")
    td.propose(base, "ep01")
    for b in td.load_design(base, "ep01")["boundaries"]:
        for t in [b.get("design")] + [a["transition_in"] for a in b.get("alternatives") or []]:
            if isinstance(t, dict):
                assert not any(x.get("kind") == "bridge" for x in t.get("inserts") or [])
                assert not t.get("motion_pair") and not t.get("audio_lead_s") and not t.get("sound_bridge")


# ---------------------------------------------------------------- 三期:成对运镜

def test_motion_pair_contract_and_prompt_sync():
    base = _project("td34-motion", "cinematic", block=False)   # grp003→grp004 纯换场景
    td.propose(base, "ep01")
    b = _row(base, BID_SCENE)
    alt = next(a for a in b["alternatives"] if "成对运镜" in a["label"])
    mp = alt["transition_in"]
    assert mp["motion_pair"] == {"out": "pan_right", "in": "pan_right", "speed": "medium"}
    assert mp["sound_bridge"] == {"kind": "j", "s": td.SOUND_BRIDGE_S, "carry": "bed"}   # 2026-10-03 改版:声桥取代 audio_lead_s
    # 契约
    def errs_for(t, first=False):
        gs = ([{"group_id": "b", "shots": ["s2"], "transition_in": t}] if first else
              [{"group_id": "a", "shots": ["s1"]}, {"group_id": "b", "shots": ["s2"], "transition_in": t}])
        return cgg.check_transitions({"budget_s": 60, "generation_groups": gs}, 10.0)
    assert not [e for e in errs_for(mp) if "motion_pair" in e or "sound_bridge" in e]
    assert any("不配对" in e for e in errs_for({**mp, "motion_pair": {"out": "push_in", "in": "push_in"}}))
    assert any("不在枚举" in e for e in errs_for({**mp, "motion_pair": {"out": "orbit", "in": "orbit"}}))
    assert any("只配 hard_cut / dissolve" in e for e in errs_for({**mp, "type": "dip_black", "duration_s": 0.8}))
    assert any("首组" in e for e in errs_for(mp, first=True))
    # 工位把候选升为主设计,H3A 接受 → prompt 同步
    idx = b["alternatives"].index(alt)
    td.set_design(base, "ep01", BID_SCENE, alt=idx, note="两侧都是横向构图")
    td.accept_all_proposed(base, "ep01", by="sign:g6")
    assert _sl(base)["generation_groups"][3]["transition_in"]["motion_pair"]["out"] == "pan_right"
    pairs = mpx.pairs_of(_sl(base))
    assert pairs == [{"from_group": "grp003", "to_group": "grp004", "motion_pair": {"out": "pan_right", "in": "pan_right", "speed": "medium"}}]
    # 组 prompt 未写 → skipped,不算违规
    res = mpx.sync_episode(base, "ep01", write=True, zh=True)
    assert not res["errors"] and all(r["skipped"] for r in res["groups"] if r["group_id"] in ("grp003", "grp004"))
    # 写两侧组 prompt(Seedance 结构),--write 后句子落在正确 Shot 段
    vp_a = "Overall visual style: 水墨。\n\nShot 1: 甲走进书房。\n\nShot 2: 甲翻书,镜头缓慢推近。\n\nGlobal constraints: no text."
    vp_b = "Overall visual style: 水墨。\n\nShot 1: 南天门全景,云海翻涌。\n\nShot 2: 乙迎面走来。\n\nGlobal constraints: no text."
    (base / "assets" / "prompts" / "ep01" / "grp003.json").write_text(json.dumps({"group_id": "grp003", "video_prompt": vp_a}, ensure_ascii=False))
    (base / "assets" / "prompts" / "ep01" / "grp004.json").write_text(json.dumps({"group_id": "grp004", "video_prompt": vp_b}, ensure_ascii=False))
    res = mpx.sync_episode(base, "ep01", write=False, zh=True)
    assert len([e for e in res["errors"] if "缺" in e]) == 2          # 未 --write 先报缺句
    res = mpx.sync_episode(base, "ep01", write=True, zh=True)
    assert not res["errors"] and set(res["updated_prompts"]) == {"grp003", "grp004"}
    a = json.loads((base / "assets" / "prompts" / "ep01" / "grp003.json").read_text())["video_prompt"]
    bb = json.loads((base / "assets" / "prompts" / "ep01" / "grp004.json").read_text())["video_prompt"]
    assert "Shot 2: 甲翻书,镜头缓慢推近。【运镜对接】本镜结尾以中速向右横摇带出画面" in a and a.count("【运镜对接】") == 1
    assert "Shot 1: 南天门全景,云海翻涌。【运镜对接】本镜开头延续上一组的中速向右横摇接入" in bb and bb.count("【运镜对接】") == 1
    assert "Global constraints: no text." in a and "Global constraints: no text." in bb
    # 幂等
    res2 = mpx.sync_episode(base, "ep01", write=True, zh=True)
    assert not res2["updated_prompts"] and not res2["errors"]
    # 改方向 → 旧句成残留,机检报;--write 换句
    sl = _sl(base)
    sl["generation_groups"][3]["transition_in"]["motion_pair"] = {"out": "push_in", "in": "pull_out", "speed": "slow"}
    (base / "directing" / "ep01" / "shot_list.json").write_text(json.dumps(sl, ensure_ascii=False))
    res = mpx.sync_episode(base, "ep01", write=False, zh=True)
    assert any("残留过期" in e for e in res["errors"])
    res = mpx.sync_episode(base, "ep01", write=True, zh=True)
    assert not res["errors"]
    a = json.loads((base / "assets" / "prompts" / "ep01" / "grp003.json").read_text())["video_prompt"]
    assert "缓慢推近带出画面" in a and a.count("【运镜对接】") == 1
    bb = json.loads((base / "assets" / "prompts" / "ep01" / "grp004.json").read_text())["video_prompt"]
    assert "缓慢拉远接入" in bb
    # 设计撤了 → 剔除
    sl["generation_groups"][3]["transition_in"].pop("motion_pair")
    (base / "directing" / "ep01" / "shot_list.json").write_text(json.dumps(sl, ensure_ascii=False))
    res = mpx.sync_episode(base, "ep01", write=False, zh=True)
    assert any("设计已撤" in e for e in res["errors"])
    res = mpx.sync_episode(base, "ep01", write=True, zh=True)
    assert not res["errors"]
    assert "【运镜对接】" not in json.loads((base / "assets" / "prompts" / "ep01" / "grp003.json").read_text())["video_prompt"]
    # 英文句
    o, i = mpx.sentences({"out": "push_in", "in": "pull_out", "speed": "fast"}, zh=False)
    assert o.startswith("Motion pair:") and "fast push in" in o and "pull out" in i


# ---------------------------------------------------------------- 四期:声桥(2026-10-03 改版,原音先入)

def test_sound_bridge_contract_and_mix_boundary_layer():
    base = _project("td34-lead", "cinematic")
    td.propose(base, "ep01")
    b = _row(base, BID_SCENE)
    jc = next(a for a in b["alternatives"] if "声先入" in a["label"] and "运镜" not in a["label"])
    t = jc["transition_in"]
    assert t["sound_bridge"] == {"kind": "j", "s": td.SOUND_BRIDGE_S, "carry": "bed"} and t["type"] == "hard_cut" and "J-cut" in t["reason"]
    assert "audio_lead_s" not in t

    def errs_for(tt, first=False, sound_split=None, shots=None):
        gs = ([{"group_id": "b", "shots": ["s2"], "transition_in": tt}] if first else
              [{"group_id": "a", "shots": ["s1"]}, {"group_id": "b", "shots": ["s2"], "transition_in": tt}])
        return [e for e in cgg.check_transitions({"budget_s": 60, "generation_groups": gs, "shots": shots or []}, 10.0, sound_split)
                if "sound_bridge" in e]
    assert not errs_for(t)
    assert errs_for({**t, "sound_bridge": {"kind": "j", "s": 1.6, "carry": "bed"}})
    assert errs_for({**t, "sound_bridge": {"kind": "x", "s": 0.5, "carry": "bed"}})
    assert errs_for({**t, "type": "dip_black", "duration_s": 0.8})
    assert errs_for({**t, "hold_s": 0.5})
    assert errs_for({**t, "inserts": [{"kind": "title_card", "duration_s": 2.0, "card": {"lines": ["x"]}, "join_out": "hard_cut", "audio": "mute"}]})
    assert errs_for(t, first=True)
    # 存量 audio_lead_s 归一为 j/bed,>1.5 仍拦
    legacy = {k: v for k, v in t.items() if k != "sound_bridge"}
    assert cgg.sound_bridge_of({**legacy, "audio_lead_s": 0.5}) == {"kind": "j", "s": 0.5, "carry": "bed"}
    assert not errs_for({**legacy, "audio_lead_s": 0.5}) and errs_for({**legacy, "audio_lead_s": 2.0})
    # carry=line:声画分离关 / 切点旁无画外句 → 拦;下组首镜有 heard_in 含首镜的画外句 → 过
    line = {**t, "sound_bridge": {"kind": "j", "s": 0.5, "carry": "line"}}
    assert any("声画分离" in e for e in errs_for(line, sound_split="off"))
    assert any("没有 heard_in" in e for e in errs_for(line, sound_split="auto"))
    shots = [{"shot_id": "s2", "duration_s": 3, "dialogue_lines": [{"speaker": "CHAR-0001", "text": "来了", "placement": "os", "heard_in": ["s2"]}]}]
    assert not errs_for(line, sound_split="auto", shots=shots)
    lline = {**t, "sound_bridge": {"kind": "l", "s": 0.5, "carry": "line"}}
    assert any("前组末镜" in e for e in errs_for(lline, sound_split="auto", shots=shots))
    # 接受 → 混音边界层暴露 sound_bridge、进指纹、不占时
    idx = b["alternatives"].index(jc)
    td.set_design(base, "ep01", BID_SCENE, alt=idx)
    td.accept_all_proposed(base, "ep01", by="sign:g6")
    from modules import mix_manifest as mm
    rows = [{"group_id": g} for g in ("grp001", "grp002", "grp003", "grp004")]
    bounds = mm.boundary_layer(base, "ep01", rows, fps=24.0)
    br = [x for x in bounds if x.get("sound_bridge")]
    assert len(br) == 1 and br[0]["to_group"] == "grp004" and br[0]["total_s"] == 0 and br[0]["sound_bridge"]["kind"] == "j"
    assert mm.boundary_delta(bounds) == sum(x["total_s"] for x in bounds) == 2.0
    fp_lead = mm.boundary_fingerprint(bounds)
    sl = _sl(base)
    sl["generation_groups"][3]["transition_in"].pop("sound_bridge")
    (base / "directing" / "ep01" / "shot_list.json").write_text(json.dumps(sl, ensure_ascii=False))
    bounds2 = mm.boundary_layer(base, "ep01", rows, fps=24.0)
    assert not [x for x in bounds2 if x.get("sound_bridge")]
    assert mm.boundary_fingerprint(bounds2) != fp_lead and mm.boundary_delta(bounds2) == 2.0
    # 无声桥项目的指纹与旧口径一致(后三项不出现);存量 audio_lead_s 行(旧口径第 4 项)指纹必变 → 重混
    assert mm.boundary_fingerprint([{"from_group": "a", "to_group": "b", "total_s": 1.0}]) == \
        mm.boundary_fingerprint([{"from_group": "a", "to_group": "b", "total_s": 1.0, "sound_bridge": None}])
    assert mm.boundary_fingerprint([{"from_group": "a", "to_group": "b", "total_s": 0.0, "sound_bridge": {"kind": "j", "s": 0.5, "carry": "bed"}}]) != \
        mm.boundary_fingerprint([{"from_group": "a", "to_group": "b", "total_s": 0.0, "sound_bridge": {"kind": "l", "s": 0.5, "carry": "bed"}}])
