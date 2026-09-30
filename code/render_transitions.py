#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""render_transitions.py — 组间转场宿主 CLI:按 shot_list 的转场设计在粗成片上实施 + 机检 transition_render_ok。

背景(WORKFLOW.md §9C,2026-08-28):组间转场**设计在 Phase 6**(shot-planning 定稿
`generation_groups[].transition_in`,依据 director 转场清单),**实施在 Phase 9**(本 CLI)。以前
transition Agent 按 directing_plan 自由文本自写 ffmpeg xfade,一项目一格式、无法机检、且常忘记
补偿 xfade 吃掉的重叠时长(成片缩短 → 外挂声轨/字幕/旁白挂点整体偏早)。本 CLI 把它做成确定性工序:

  plan     读 shot_list.generation_groups[].transition_in + edit/epNN/timeline.json 的 video 轨,
           为**每一个组边界**生成 timeline.transitions[](缺省 hard_cut;非硬切只能来自 shot_list,
           Agent 不得自创),写回 timeline.json;
  render   先 plan,再对粗成片(默认 cut_v1.mp4)施加转场 → cut_v2.mp4。**pad 补偿**:叠化/黑白场
           两侧各用 tpad 克隆 duration/2 的尾帧/首帧再 xfade,**成片总时长严格不变**,边界之外的一切
           时刻都不动,外挂声轨(final_audio)/字幕/旁白挂点/intro_offset_ok 全部不受影响;声轨原样
           流拷贝(-c:a copy),不碰;
           **组占帧以 timeline 为准(2026-09-24,DEF-ep06-edit-0002)**:每条 video 条目的成片占帧
           n = round(占时×fps),从组片段取 in 起的帧,不足以尾帧克隆补齐(edit 为对齐组 clip 原生声轨
           常在组尾补 1 帧,timeline 记 tail_pad_frames);整文件原样的组才流拷贝,带补帧/修剪的组单独
           重编码——以前直接引用整个组片段文件,补帧丢失 → 成片少帧、片尾画面超前声轨;
           **节奏垫片(2026-09-17,§9C)**:transition_in 带 hold_s(本组前黑场停留)/ freeze_s(前组尾帧定格)
           时在组边界**插入**帧——前组尾(可定格 freeze_s)→ 黑场 hold_s → 本组首;dip_black 配 hold 时前组尾
           淡出半个 duration 到黑、本组首自黑淡入半个 duration,fade_black 配 hold 时前组尾淡出整段、本组硬入,
           hard_cut 配 hold 即切黑停留再硬入。成片按 Σ垫片变长,同时把这张「时长编辑表」写进台账
           (timemap.ops,源 cut 基准),源 cut 若带声轨则按表重映射(黑场声音按 hold_audio:sustain 延续前段
           /fade 淡出/mute 静音);外挂声轨与字幕由 finalize_episode.py 读同一张表平移(§9B)。
           **集尾收束(2026-09-25,§9C)**:成片末尾按 episode_close(shot_list 顶层,缺省 = 项目设置 transitions.episode_close,默认淡出到黑
           1.0s + 黑场 0.5s)给最后一组尾部加 fade=out,再补 hold_s 的黑/白场帧——以前画面停在末帧硬结束。停留帧在尾部**不平移**任何时刻,
           故不进 timemap、不进混音边界层;声轨:源 cut 自带声轨同刻淡出 + 补静音,外挂声轨由 finalize_episode.py 在同一时刻淡出。
           条目 to_group=null、at_shot「末镜->episode_close」,台账 episode_close{fade_end_s,…} 供 finalize;`preview --boundary episode_close` 出小片。
  build    过场设计(2026-09-24,docs/transition_design.md):渲染 transition_in.inserts[](title_card 字幕卡 / establishing 定场空镜 /
           timelapse 时光流转 / bridge 生成式桥接)与 overlay_card 叠字幕 PNG 到 edit/epNN/transitions/<B-from-to>/(按设计+素材指纹幂等);
           render 内部自动调用。dissolve 可带 join.style(受控 xfade 风格族);插入段沿垫片路径在组边界插入、成片按 Σ 变长、timemap 同表。
  preview  边界预览小片:前组尾 2s + 接缝/垫片/插入段 + 本组首 2s → edit/epNN/transitions/<B-id>/preview.mp4(与成片同一滤镜链,
           分镜预览页「过场卡」▶ 用;--boundary B-grpA-grpB / --to grpNNN,不给 = 全部非默认边界)
  check    机检 transition_render_ok(封装前必跑,FAIL 即不交付):
             transitions_planned          timeline.transitions 覆盖全部组边界,每边界恰一条
             transitions_match_shot_list  非硬切条目与 shot_list transition_in 一一对应(类型/时长/垫片);
                                          timeline 里多出的非硬切 = Agent 自创,FAIL
             duration_unchanged           cut_v2 时长 = cut_v1 ±1 帧(无垫片时)
             duration_as_planned          cut_v2 时长 = cut_v1 + Σ垫片 ±1 帧(有垫片时)
             audio_stream_intact          有/无声轨与源一致,声轨时长一致(流拷贝;有垫片时 = 源 + Σ垫片)
             transition_frames_verified   逐处抽帧:叠化中点 ≈ 前后帧 50/50 混合;dip 中点近黑/近白;
                                          fade 尾帧近黑/近白;黑场垫片中点近黑、定格帧 = 前组末帧;
                                          风格接缝中点与两侧都不同且非纯黑;
                                          硬切边界两侧帧与源一致(无时间漂移,有垫片时按 timemap 对位)
             insert_budget_ok             Σ插入段 ≤ 集预算 × 项目「过场模式」预算%(极简 0 / 经典 8 / 电影感 10 / 自定义)
             inserts_built                插入段/叠字构建台账在、指纹与 timeline 条目一致、段文件在(生成式 clip 缺失 = FAIL:
                                          桥接 <B-id>.bridge.mp4 / i2v 定场 <B-id>.establishing.mp4,均由 Phase 7 p7-transition-clips 出)
             insert_frames_verified       每段中点帧 ≈ 段文件中点帧;黑底字卡中点非黑(文字在);叠字窗口内与源有差、窗口后一致;
                                          桥接首/末帧贴合前组尾/本组首
             (集尾收束并入上述各项:时长 = 源 + Σ垫片 + 尾停留;末帧近黑/近白、停留中点近黑;声轨随之延长)
           台账 edit/epNN/transitions_render.json(含 dip/fade/黑场垫片白名单窗口,供 no_black_frames 豁免;
           pads[] 与 timemap.ops 供 finalize_episode.py 平移外挂声轨/字幕)

转场类型(与 code/check_generation_groups.py 的 transition_ok 同源):
  可渲染  dissolve(xfade=fade)· dip_black / dip_white(xfade=fadeblack/fadewhite)·
          fade_black / fade_white(前组尾淡出到黑/白,本组硬入;首组 = 从黑/白淡入)
  标注型  smash_cut / match_cut(不渲染 = 硬切,只供 continuity / QA 核对构图对位)

用法:
  python3 code/render_transitions.py plan   --project <slug> --ep epNN [--dry-run]
  python3 code/render_transitions.py build  --project <slug> --ep epNN [--force] [--boundary B-grpA-grpB] [--to grpNNN]
  python3 code/render_transitions.py preview --project <slug> --ep epNN [--boundary …|--to …] [--force]
  python3 code/render_transitions.py render --project <slug> --ep epNN [--src cut_v1.mp4] [--out cut_v2.mp4]
  python3 code/render_transitions.py check  --project <slug> --ep epNN [--src cut_v1.mp4] [--out cut_v2.mp4]
退出码:全 PASS=0,任一 FAIL=1。
"""
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from _common import parse_args  # 副作用:modules/ 入 sys.path
from avsync import probe_duration, require_tools
from footage import count_frames as count_frames_of

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_generation_groups import (TRANSITION_ANNOTATION, TRANSITION_RENDERABLE,  # noqa: E402
                                     transition_of, pad_of, inserts_of, insert_total_s)
import timemap  # noqa: E402  modules/(_common 副作用入 sys.path)
from modules import transition_design as td  # noqa: E402  过场设计(2026-09-24):字卡/全景视窗渲染、指纹、生效模式

LEDGER = "transitions_render.json"
DEFAULT_SRC = "cut_v1.mp4"
DEFAULT_OUT = "cut_v2.mp4"
XFADE_OF = {"dissolve": "fade", "dip_black": "fadeblack", "dip_white": "fadewhite"}
INSERT_DIR = "transitions"          # edit/epNN/transitions/<B-from-to>/:插入段(ins{k}.mp4)、字卡/叠字 PNG、预览小片、meta.json
PREVIEW_PAD_S = 2.0                 # 边界预览小片:前组尾 / 本组首各取秒数
OVERLAY_FADE_S = 0.3
BRIDGE_DIR = "assets/transitions"   # 生成式 clip:assets/transitions/epNN/<B-id>.bridge.mp4(桥接)/ <B-id>.establishing.mp4(i2v 定场),Phase 7 p7-transition-clips 由 video-generation 出
FADE_COLOR = {"fade_black": "black", "fade_white": "white"}
CUT_COLOR = {"cut_black": "black", "cut_white": "white"}   # 集尾「切黑 / 切白」:不淡出,末帧直接切到纯色停留(悬念收束)
CLOSE_COLOR = {**FADE_COLOR, **CUT_COLOR}
CLOSE_ID = "episode_close"          # 集尾收束条目 id(preview --boundary episode_close;edit/epNN/transitions/episode_close/)
DARK_MAX = 24        # 近黑判定:灰度均值 ≤ 24/255
BRIGHT_MIN = 231     # 近白判定:灰度均值 ≥ 231/255
BLEND_TOL = 14.0     # 叠化中点 vs 前后帧 50/50 混合的平均绝对差上限
SAME_TOL = 8.0       # 硬切/非转场区帧 vs 源帧的平均绝对差上限(重编码噪声内)


# ---------------------------------------------------------------- 基础

def _run(cmd, timeout=1800, binary=False):
    p = subprocess.run(cmd, capture_output=not binary, stdout=subprocess.PIPE if binary else None,
                       stderr=subprocess.PIPE if binary else None, text=not binary, timeout=timeout)
    if p.returncode != 0:
        err = p.stderr if isinstance(p.stderr, str) else (p.stderr or b"").decode("utf-8", "replace")
        raise RuntimeError(f"命令失败({p.returncode}):{' '.join(map(str, cmd))}\n{err[-2000:]}")
    return p.stdout


def _probe(path):
    out = _run(["ffprobe", "-v", "error", "-show_entries",
                "stream=codec_type,width,height,r_frame_rate,duration",
                "-of", "json", str(path)], timeout=60)
    info = {"video": None, "audio": None}
    for s in json.loads(out).get("streams", []):
        t = s.get("codec_type")
        if t in info and info[t] is None:
            info[t] = s
    return info


def _fps(stream):
    try:
        n, d = (stream or {}).get("r_frame_rate", "24/1").split("/")
        return float(n) / float(d)
    except Exception:
        return 24.0


def _read_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _write_json(p, data):
    Path(p).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _fmt(x):
    return f"{x:.3f}s"


def xfade_name(e):
    """条目 → xfade transition 名:dissolve 可带 join.style(过场设计 2026-09-24,受控风格族;check_generation_groups.JOIN_STYLES 同源)。"""
    ty = e.get("type")
    if ty == "dissolve":
        j = e.get("join") if isinstance(e.get("join"), dict) else {}
        st = j.get("style")
        if st == "wipe":
            soft = {"hard": "wipe", "slide": "slide", "smooth": "smooth"}.get(j.get("softness") or "smooth", "smooth")
            return soft + (j.get("direction") or "left")
        if st == "blur_through":
            return "hblur"
        if st == "zoom_through":
            return "zoomin"
        if st == "iris":
            return "circleopen" if (j.get("direction") or "open") == "open" else "circleclose"
        if st in ("pixelize", "fadegrays"):
            return st
        return "fade"
    return XFADE_OF[ty]


def _ins_s(e):
    """条目的插入段总秒数(字卡 / 定场 / 时光流转 / 桥接)。"""
    return float(sum(float(x.get("duration_s") or 0.0) for x in (e.get("inserts") or [])))


def _ins_frames(e, fps):
    return [max(2, _frames(x.get("duration_s") or 0.0, fps)) for x in (e.get("inserts") or [])]


def _extra_s(e):
    """边界插入的总秒数 = 垫片(定格 + 黑场)+ 插入段。"""
    return _pad_s(e) + _ins_s(e)


def _has_extra(e):
    return bool(_extra_s(e) > 0 or e.get("overlay_card"))


def _is_close(e):
    """集尾收束条目(2026-09-25):from_group = 末组,to_group = None。"""
    return bool(e) and e.get("from_group") is not None and e.get("to_group") is None


def _close_frames(e, fps):
    """(淡出帧数, 尾停留帧数);切黑类淡出恒 0。"""
    cd = 0 if e.get("type") in CUT_COLOR else _frames(e.get("duration_s") or 0.0, fps)
    return cd, _frames(e.get("hold_s") or 0.0, fps)


def _bid(e):
    if _is_close(e):
        return CLOSE_ID
    return td.boundary_id(e.get("from_group") or "open", e.get("to_group") or "?")


def _ep_paths(proj, ep, src=None, out=None):
    ed = proj / "edit" / ep
    sl = proj / "directing" / ep / "shot_list.json"
    tl = ed / "timeline.json"
    src_p = ed / (src or DEFAULT_SRC)
    out_p = ed / (out or DEFAULT_OUT)
    return ed, sl, tl, src_p, out_p


# ---------------------------------------------------------------- 时间线 → 组边界

def _entry_dur(e, proj):
    """timeline video 条目的成片占时(秒)。优先条目自带的成片区间,其次 (out-in)/speed(+定格帧)。"""
    for a, b in (("timeline_in_s", "timeline_out_s"), ("timeline_in", "timeline_out")):
        if isinstance(e.get(a), (int, float)) and isinstance(e.get(b), (int, float)):
            return float(e[b]) - float(e[a])
    i, o = e.get("in"), e.get("out")
    if not isinstance(o, (int, float)):
        src = e.get("src")
        if not src:
            raise SystemExit(f"[FAIL] timeline 条目缺 out 且无 src,无法定时长:{json.dumps(e, ensure_ascii=False)[:200]}")
        o = probe_duration(proj / src)
    d = (float(o) - float(i or 0.0)) / float(e.get("speed") or 1.0)
    if isinstance(e.get("hold_frames"), (int, float)) and e.get("hold_frames"):
        d += float(e["hold_frames"]) / 24.0
    return d


def _entry_gid(e, idx):
    return str(e.get("group_id") or e.get("id") or e.get("name") or f"entry{idx:03d}")


_FRAMES_CACHE = {}


def _file_frames(path):
    """组片段逐帧计数(进程内缓存:plan/render/check 反复用同一批文件)。"""
    key = str(Path(path).resolve())
    if key not in _FRAMES_CACHE:
        _FRAMES_CACHE[key] = count_frames_of(key)
    return _FRAMES_CACHE[key]


def _entry_frames(e, idx, proj, fps):
    """timeline video 条目的**成片占帧计划**(物理口径;渲染 / 边界重量化 / 核验同源):
      n     = round(成片占时 × fps):条目在成片上占的帧数——edit 的 cut 就是按这个时长拼的;
      start = round(in × fps):从组片段取帧的起点;
      take  = min(n, 文件帧数 − start):实际取自文件的帧数;
      pad   = n − take:不足部分以尾帧克隆补齐(edit 为对齐组 clip 原生声轨常在组尾补 1 帧,timeline
              记作 tail_pad_frames;2026-09-24 DEF-ep06-edit-0002:渲染曾忽略它 → 成片少帧、片尾画面超前声轨)。
    start == 0 且 n == 文件帧数 ⇔ 整文件原样,可流拷贝。变速条目分段渲染无法表达 → FAIL。"""
    gid = _entry_gid(e, idx)
    src = e.get("src")
    if not src:
        raise SystemExit(f"[FAIL] timeline video 条目 {idx}({gid})缺 src,无从按帧规划")
    if abs(float(e.get("speed") or 1.0) - 1.0) > 1e-9:
        raise SystemExit(f"[FAIL] timeline 条目 {idx}({gid})speed={e.get('speed')}:分段渲染不支持变速条目,"
                         "请 edit 先把变速烘进组片段再落 timeline")
    n = int(round(_entry_dur(e, proj) * fps))
    start = int(round(float(e.get("in") or 0.0) * fps))
    total = _file_frames(proj / src)
    avail = total - start
    if n <= 0 or avail <= 0:
        raise SystemExit(f"[FAIL] timeline 条目 {idx}({gid})in={e.get('in')} 超出组片段({total} 帧)或占时为 0")
    take = min(n, avail)
    return {"gid": gid, "src": proj / src, "start": start, "n": n, "take": take, "pad": n - take,
            "file_frames": total, "verbatim": start == 0 and n == total}


def requantize_cuts(entries, timeline, proj, fps):
    """把非硬切条目的 cut_time_s 重量化为**各条目成片占帧的累计**(物理口径,_entry_frames)。

    timeline 的 in/out 常见毫秒截断(3.545),几十组浮点累加后可偏过半帧线,
    round(累计秒×fps) 会与实拼的帧边界差 1 帧——渲染窗口与像素核验双双错位
    (2026-09-03 leijun2 实测)。渲染按每条目 round(占时×fps) 取帧/补帧,按同一口径
    逐条累计即与成片逐帧一致。就地更新 entries,返回重量化条数。"""
    vids = ((timeline.get("tracks") or {}).get("video")) or timeline.get("video") or []
    if not vids or any(not e.get("src") for e in vids):
        return 0                        # 条目缺 src,无从按帧累计,保持原值
    cum, walk = 0, []                   # walk: 有序边界表 [(from,to,t), ...]
    prev = None
    for i, e in enumerate(vids):
        gid = _entry_gid(e, i)
        if prev is not None and gid != prev:
            walk.append([prev, gid, cum / fps])
        cum += _entry_frames(e, i, proj, fps)["n"]
        prev = gid
    # 有序消费匹配:同一 (from,to) 邻接重复出现时按边界顺序一一对应,不坍缩到最后一处
    n = 0
    for e in entries:
        if _is_close(e):
            if abs(cum / fps - float(e.get("cut_time_s") or 0)) > 1e-6:
                e["cut_time_s"] = round(cum / fps, 6)
                n += 1
            continue
        if e.get("type") in (None, "hard_cut"):
            continue
        key = (e.get("from_group"), e.get("to_group"))
        for w in walk:
            if w is not None and (w[0], w[1]) == key:
                t = w[2]
                walk[walk.index(w)] = None
                if abs(t - float(e.get("cut_time_s") or 0)) > 1e-6:
                    e["cut_time_s"] = round(t, 6)
                    n += 1
                break
    return n


def boundaries_of(timeline, proj, shot_list=None):
    """按 video 轨顺序找组变化点:[{from_group, to_group, cut_time_s, at_shot}],以及成片总时长。"""
    entries = ((timeline.get("tracks") or {}).get("video")) or timeline.get("video") or []
    if not entries:
        raise SystemExit("[FAIL] timeline.json 无 tracks.video 条目(edit 粗剪未落盘?)")
    g_shots = {}
    if shot_list:
        g_shots = {g.get("group_id"): g.get("shots") or [] for g in (shot_list.get("generation_groups") or [])}
    bounds, t, prev = [], 0.0, None
    for i, e in enumerate(entries):
        gid = _entry_gid(e, i)
        if prev is not None and gid != prev:
            fs = (g_shots.get(prev) or [None])[-1]
            ts = (g_shots.get(gid) or [None])[0]
            bounds.append({"from_group": prev, "to_group": gid, "cut_time_s": round(t, 6),
                           "at_shot": f"{fs or prev}->{ts or gid}"})
        t += _entry_dur(e, proj)
        prev = gid
    return bounds, t, [_entry_gid(e, i) for i, e in enumerate(entries)]


# ---------------------------------------------------------------- plan

def make_plan(shot_list, timeline, proj, src_path=None):
    groups = shot_list.get("generation_groups") or []
    by_gid = {g.get("group_id"): g for g in groups}
    first_gid = groups[0].get("group_id") if groups else None
    bounds, total, order = boundaries_of(timeline, proj, shot_list)
    entries, problems = [], []

    # 首组淡入(fade_black / fade_white 于集首)
    if first_gid and order and order[0] == first_gid:
        t0 = transition_of(by_gid[first_gid])
        if t0["type"] in FADE_COLOR:
            entries.append({"from_group": None, "to_group": first_gid, "at_shot": f"episode_open->{(by_gid[first_gid].get('shots') or [first_gid])[0]}",
                            "cut_time_s": 0.0, "type": t0["type"], "duration_s": float(t0["duration_s"]),
                            "intent": t0.get("intent"), "reason": t0.get("reason"), "source": "shot_list.transition_in"})
        elif t0["type"] != "hard_cut":
            problems.append(f"{first_gid} 首组 transition_in={t0['type']} 无前组可叠,忽略(transition_ok 应已拦)")

    for b in bounds:
        g = by_gid.get(b["to_group"])
        t = transition_of(g) if g else {"type": "hard_cut"}
        ty = t["type"]
        ent = dict(b)
        ent["type"] = ty
        if ty in TRANSITION_RENDERABLE:
            ent["duration_s"] = float(t.get("duration_s") or 0.0)
        if ty in TRANSITION_ANNOTATION:
            ent["renders_as"] = "hard_cut"
        freeze_s, hold_s, hold_audio = pad_of(t)
        if freeze_s > 0:
            ent["freeze_s"] = round(freeze_s, 4)
        if hold_s > 0:
            ent["hold_s"] = round(hold_s, 4)
            ent["hold_audio"] = hold_audio
        # 过场设计(2026-09-24):接缝风格 / 插入段 / 叠字幕随条目进 timeline,渲染与核验同源
        if t.get("join"):
            ent["join"] = dict(t["join"])
        ins = inserts_of(t)
        if ins:
            ent["inserts"] = [dict(x) for x in ins]
            ent["insert_s"] = round(insert_total_s(t), 4)
        if t.get("overlay_card"):
            ent["overlay_card"] = dict(t["overlay_card"])
        if ty != "hard_cut" or hold_s or freeze_s or ins or t.get("overlay_card"):
            ent["intent"] = t.get("intent")
            ent["reason"] = t.get("reason")
            ent["source"] = "shot_list.transition_in"
            ent["fingerprint"] = td.source_fingerprint(proj, t)
        else:
            ent["reason"] = ent.get("reason") or "default hard cut"
        entries.append(ent)

    # shot_list 有非硬切设计、timeline 却没有对应边界(组缺席 / 不在边界)= 设计落不了地
    planned_to = {e["to_group"] for e in entries if e["type"] != "hard_cut" or _has_extra(e)}
    for g in groups:
        t = transition_of(g)
        if (t["type"] != "hard_cut" or sum(pad_of(t)[:2]) > 0 or inserts_of(t) or t.get("overlay_card")) and g.get("group_id") not in planned_to:
            problems.append(f"{g.get('group_id')} transition_in={t['type']} 在 timeline video 轨上没有对应的组入口边界"
                            f"(组缺席或不在边界)")
    # 集尾收束(2026-09-25):末组尾部淡出 + 停留;缺省按项目设置,shot_list 顶层 episode_close 写了就按它(hard_cut = 不处理)
    close = td.effective_episode_close(proj, None, shot_list)
    close_entry = None
    if close and order:
        last_gid = order[-1]
        g_last = by_gid.get(last_gid) or {}
        close_entry = {"from_group": last_gid, "to_group": None, "at_shot": f"{(g_last.get('shots') or [last_gid])[-1]}->{CLOSE_ID}",
                       "cut_time_s": round(total, 6), "type": close["type"], "duration_s": float(close["duration_s"]),
                       "intent": "episode_close", "reason": close.get("reason") or "集尾收束(项目设置 transitions.episode_close)",
                       "source": close.get("source") or "settings.transitions.episode_close", "fingerprint": td.close_fingerprint(close)}
        if float(close.get("hold_s") or 0) > 0:
            close_entry["hold_s"] = round(float(close["hold_s"]), 4)
            close_entry["hold_audio"] = str(close.get("hold_audio") or "fade")
        entries.append(close_entry)
    renderable = [e for e in entries if e["type"] in TRANSITION_RENDERABLE and not _is_close(e)]
    pads = [e for e in entries if _pad_s(e) and not _is_close(e)]
    inserts = [e for e in entries if e.get("inserts")]
    overlays = [e for e in entries if e.get("overlay_card")]
    policy = {
        "cli": "code/render_transitions.py", "compensation": "pad" + ("+insert" if (pads or inserts) else ""),
        "source": "shot_list.generation_groups[].transition_in",
        "planned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "src_cut": str(src_path.name) if src_path else None,
        "boundaries": len(bounds), "renderable": len(renderable),
        "renderable_total_s": round(sum(e["duration_s"] for e in renderable), 3),
        "pads": len(pads), "pad_total_s": round(sum(_pad_s(e) for e in pads), 3),
        "inserts": len(inserts), "insert_total_s": round(sum(_ins_s(e) for e in inserts), 3),
        "insert_segments": sum(len(e["inserts"]) for e in inserts), "overlays": len(overlays),
        "timeline_total_s": round(total, 3),
        "episode_close": ({k: close_entry.get(k) for k in ("type", "duration_s", "hold_s", "hold_audio", "source")} if close_entry else None),
    }
    return entries, policy, problems


def _pad_s(e):
    """条目的垫片总秒数(定格 + 黑场)。"""
    return float(e.get("freeze_s") or 0.0) + float(e.get("hold_s") or 0.0)


def _pad_frames(e, fps):
    """(定格帧数, 黑场帧数),按帧量化。"""
    return _frames(e.get("freeze_s") or 0.0, fps), _frames(e.get("hold_s") or 0.0, fps)


def pad_ops(entries, fps):
    """垫片 + 插入段 → timemap ops(源 cut 基准;插入点 = 组边界源时刻,块 = 定格 → 黑场 → 插入段)。
    声音策略:有黑场停留按 hold_audio;否则按首个插入段的 audio(mute / sustain;bed 本期无底噪库,按 mute)。"""
    ops = []
    for e in entries:
        fz, hd = _pad_frames(e, fps)
        ins = sum(_ins_frames(e, fps)) if e.get("inserts") else 0
        if not (fz or hd or ins) or e.get("from_group") is None or _is_close(e):
            continue                      # 集尾停留在片尾之后,不平移任何时刻,不进 timemap
        t = _frames(e.get("cut_time_s") or 0.0, fps) / fps
        if hd:
            audio = e.get("hold_audio") or "sustain"
        elif ins:
            a0 = str((e["inserts"][0].get("audio") or "mute"))
            audio = "sustain" if a0 == "sustain" else "mute"
        else:
            audio = e.get("hold_audio") or "sustain"
        ops.append({"src_t0": round(t, 6), "src_t1": round(t, 6), "out_len": round((fz + hd + ins) / fps, 6),
                    "freeze_s": round(fz / fps, 6), "hold_s": round(hd / fps, 6), "insert_s": round(ins / fps, 6),
                    "audio": audio, "kind": "boundary_insert" if ins else "boundary_pad",
                    "at_shot": e.get("at_shot"), "from_group": e.get("from_group"), "to_group": e.get("to_group"),
                    "type": e.get("type")})
    return timemap.normalize_ops(ops)


def do_plan(proj, ep, src_path, dry_run=False):
    ed, sl_p, tl_p, _, _ = _ep_paths(proj, ep)
    if not sl_p.is_file():
        raise SystemExit(f"[FAIL] 缺 {sl_p.relative_to(proj)}")
    if not tl_p.is_file():
        raise SystemExit(f"[FAIL] 缺 {tl_p.relative_to(proj)}(edit 粗剪产物)")
    shot_list, timeline = _read_json(sl_p), _read_json(tl_p)
    old = timeline.get("transitions")
    if isinstance(old, list):
        stray = [e for e in old if isinstance(e, dict) and e.get("type") not in (None, "hard_cut")
                 and e.get("source") != "shot_list.transition_in"]
        if stray:
            print(f"[WARN ] timeline 原有 {len(stray)} 条非 shot_list 来源的非硬切转场将被覆盖:"
                  f"{[(e.get('at_shot') or e.get('to_group'), e.get('type')) for e in stray][:6]}")
    entries, policy, problems = make_plan(shot_list, timeline, proj, src_path)
    n_non = sum(1 for e in entries if e["type"] != "hard_cut")
    print(f"[PLAN ] 组边界 {policy['boundaries']} 处 → 转场条目 {len(entries)}(非硬切 {n_non},"
          f"可渲染 {policy['renderable']} 处 / Σ{policy['renderable_total_s']}s);成片 {_fmt(policy['timeline_total_s'])}")
    for e in entries:
        if e["type"] != "hard_cut" or _has_extra(e):
            pad = (f"  定格 {e['freeze_s']}s" if e.get("freeze_s") else "") + (f"  黑场 {e['hold_s']}s/{e.get('hold_audio')}" if e.get("hold_s") else "")
            ins = ("  插入 " + "+".join(f"{x['kind']}{x['duration_s']:g}s" for x in e["inserts"])) if e.get("inserts") else ""
            ov = f"  叠字 {e['overlay_card'].get('duration_s')}s" if e.get("overlay_card") else ""
            st = f"/{e['join'].get('style')}" if e.get("join") else ""
            print(f"         {e['at_shot']:<18} @{_fmt(e['cut_time_s'])}  {e['type']}{st}"
                  f"{'' if 'duration_s' not in e else ' ' + str(e['duration_s']) + 's'}{pad}{ins}{ov}  {e.get('intent') or ''}")
    if policy.get("pads") or policy.get("inserts"):
        print(f"[PLAN ] 节奏垫片 {policy.get('pads', 0)} 处 +{policy.get('pad_total_s', 0)}s;插入段 {policy.get('insert_segments', 0)} 段/"
              f"{policy.get('inserts', 0)} 处 +{policy.get('insert_total_s', 0)}s;叠字幕 {policy.get('overlays', 0)} 处——成片按 Σ 变长(声轨/字幕按 timemap 重映射)")
    ce = policy.get("episode_close")
    if ce:
        print(f"[PLAN ] 集尾收束:{ce['type']} {ce['duration_s']:g}s" + (f" + 停留 {ce['hold_s']:g}s/{ce.get('hold_audio')}" if ce.get('hold_s') else "")
              + f"(来源 {ce.get('source')};成片末尾 +{ce.get('hold_s') or 0:g}s,不进 timemap)")
    else:
        print("[PLAN ] 集尾收束:不处理(episode_close=hard_cut),画面停在末帧")
    for p in problems:
        print(f"[FAIL ] {p}")
    if dry_run:
        return entries, policy, not problems
    timeline["transitions"] = entries
    timeline["transitions_policy"] = policy
    _write_json(tl_p, timeline)
    print(f"[DONE ] 写回 {tl_p.relative_to(proj)}#transitions")
    return entries, policy, not problems


# ---------------------------------------------------------------- render

def _frames(x, fps):
    return int(round(float(x) * fps))


def build_filter(entries, total_s, fps):
    """单输入 [0:v] → 转场后 [vout]。一切时长/时刻先量化到整帧(0.2s@24fps=4.8 帧这类非整帧值会让
    tpad/xfade 各自取整,时间戳漂移),fade 类先按源绝对时间施加(enable 限定窗口,不改长度、淡出后不留黑),
    再按 xfade 类边界切段、两侧 tpad 克隆 ⌊d/2⌋/⌈d/2⌉ 帧、链式 xfade(offset = 前序原长之和 − 前侧半长),
    各段原长之和 = 输出长 = 输入长。"""
    fades = []
    for e in entries:
        ty = e["type"]
        if ty in FADE_COLOR:
            d = _frames(e.get("duration_s") or 0, fps) / fps
            t = _frames(e["cut_time_s"], fps) / fps
            if e.get("from_group") is None:            # 集首淡入
                fades.append(f"fade=t=in:st=0:d={d:.6f}:color={FADE_COLOR[ty]}")
            else:                                      # 前组尾 [t−d, t) 淡出,本组自 t 硬入(fade=out 不加 enable 会黑到片尾)
                st = max(0.0, t - d)
                fades.append(f"fade=t=out:st={st:.6f}:d={d:.6f}:color={FADE_COLOR[ty]}"
                             f":enable='gte(t\\,{st:.6f})*lt(t\\,{t:.6f})'")
    xf = sorted((e for e in entries if e["type"] in XFADE_OF), key=lambda e: e["cut_time_s"])   # 风格接缝走 xfade_name
    # 防御性归一:历史 cut 可能混有 full-range 片段(concat -c copy 不转码),ffmpeg 8 的
    # filter 图在输入流参数切换处会断流截断输出——先统一 range/格式再进转场链
    head = "[0:v]" + ",".join(["scale=in_range=auto:out_range=tv", "format=yuv420p"] + fades)
    if not xf:
        return head + "[vout]", 0
    cut_f = [_frames(e["cut_time_s"], fps) for e in xf]
    # 叠化帧长取**偶**(0.3s@24fps 的 7 帧 → 8 帧):窗口关于边界对称、边界时刻恰为
    # 50/50 混合中点(check transition_frames_verified 的口径),且两侧克隆帧数相等,
    # 消除奇数帧长下前侧比 xfade 窗口短 1 帧的 EOF 隐患(2026-09-03 leijun2 实测)
    dur_f = [max(2, 2 * round(_frames(e["duration_s"], fps) / 2)) for e in xf]
    half_a = [df // 2 for df in dur_f]            # 前侧(前组尾)克隆帧数
    half_b = [df - ha for df, ha in zip(dur_f, half_a)]   # 后侧(本组首)克隆帧数(=half_a)
    k = len(xf) + 1
    parts = [head + f",split={k}" + "".join(f"[p{i}]" for i in range(k))]
    starts = [0] + cut_f
    ends = cut_f + [_frames(total_s, fps) + 5 * int(fps)]   # 末段 end 给足余量,trim 到源尾
    for i in range(k):
        f = f"[p{i}]trim=start_frame={starts[i]}:end_frame={ends[i]},setpts=PTS-STARTPTS"
        # 叠化窗口跨边界 [cut−half_a, cut+half_b):前段尾部须冻结到窗口**末端**(垫 half_b 帧),
        # 后段头部须冻结到窗口**起点**(垫 half_a 帧)。奇数帧长叠化时两者差 1 帧——垫反会让
        # xfade 第一输入比 offset+duration 短 1 帧,ffmpeg 8.x 对此严格按 EOF 截断整条输出
        # (7.x 宽容复用末帧,故偶数帧长/旧 ffmpeg 下无症状;2026-09-03 leijun2 实测修正)
        if i > 0:
            f += f",tpad=start_mode=clone:start_duration={half_a[i - 1] / fps:.6f}"
        if i < k - 1:
            f += f",tpad=stop_mode=clone:stop_duration={half_b[i] / fps:.6f}"
        parts.append(f + f",setpts=N/({fps:g}*TB)[s{i}]")   # 段内按帧序重打时间戳,tpad 克隆帧不留 pts 缝
    prev = "[s0]"
    for i, e in enumerate(xf):
        off = (cut_f[i] - half_a[i]) / fps          # 前序各段原长之和(= 本边界源时刻)− 前侧半长
        lab = "[vout]" if i == len(xf) - 1 else f"[x{i + 1}]"
        # xfade 之后同样按帧序重打 pts:链式 xfade 时第二段的 pts 会相对前一段回跳(实测 −half_a),不重打则
        # 编码器按 CFR 丢帧、成片总长看似不变而画面漂移
        parts.append(f"{prev}[s{i + 1}]xfade=transition={xfade_name(e)}:duration={dur_f[i] / fps:.6f}"
                     f":offset={off:.6f},setpts=N/({fps:g}*TB){lab}")
        prev = lab
    return ";".join(parts), len(xf)


def _tb(fps):
    """段链统一时基 = 1/fps(整数帧率)或精确分数:AVTB(1/1e6)下 10 帧(0.4s)的 xfade 会因微秒取整多出 1 帧(2026-09-24 实测),
    按帧时基则 xfade 起止落在整帧上。"""
    if abs(fps - round(fps)) < 1e-6:
        return f"1/{int(round(fps))}"
    from fractions import Fraction
    fr = Fraction(fps).limit_denominator(100000)
    return f"{fr.denominator}/{fr.numerator}"


def build_chain_filter(items, joins, fps, w, h, open_fade=None, close_fade=None):
    """通用「段链」滤镜图(2026-09-24 过场设计:由 build_run_filter 泛化):items[i] = {"in": "0:v"|"k:v", "start": 帧, "n": 帧,
    "overlay": None | {"in": "k:v", "frames": F, "fade_f": f}};joins[i] = items[i]|items[i+1] 之间的条目(xfade 类 / 垫片类 / fade 类 /
    None=直拼);open_fade = 集首淡入(仅链从位置 0 起)。非 0 号输入(插入段 / 叠字 PNG)先归一到 w×h。
    xfade 边界:两侧 tpad 克隆半长再 xfade(pad 补偿,长度不变);垫片 / fade 边界:前段尾 [定格] → [黑场] → 后段首,dip 类前段尾淡出 d/2、
    后段首淡入 d/2,fade 类前段尾淡出 d、后段硬入;叠字幕:后段首 F 帧叠透明 PNG(alpha 淡入淡出各 fade_f 帧)。
    close_fade = 集尾收束(2026-09-25,仅链含末组时):末段尾 d 帧 fade=out 到黑/白,再 tpad 补 hold 帧同色停留(段变长)。按帧量化,返回 (filter, 期望输出帧数)。"""
    k = len(items)
    def is_xf(e):
        return e is not None and e.get("type") in XFADE_OF and _pad_frames(e, fps)[1] == 0
    def half(e):
        df = max(2, 2 * round(_frames(e.get("duration_s") or 0, fps) / 2))
        return df // 2, df - df // 2, df
    parts, seg_len = [], []
    for i, it in enumerate(items):
        L = joins[i - 1] if i > 0 else None
        R = joins[i] if i < k - 1 else None
        src = it.get("in") or "0:v"
        f = f"[{src}]"
        if src != "0:v":
            f += f"scale={w}:{h}:flags=bicubic,setsar=1,format=yuv420p,"
        else:
            f += "scale=in_range=auto:out_range=tv,format=yuv420p,"
        f += f"trim=start_frame={it['start']}:end_frame={it['start'] + it['n']},setpts=PTS-STARTPTS,settb={_tb(fps)}"
        length = it["n"]
        if i == 0 and open_fade is not None:
            d = _frames(open_fade.get("duration_s") or 0, fps)
            if d > 0:
                f += f",fade=t=in:st=0:d={d / fps:.6f}:color={FADE_COLOR.get(open_fade['type'], 'black')}"
        if L is not None:
            if is_xf(L):
                ha, _, _ = half(L)
                f += f",tpad=start_mode=clone:start_duration={ha / fps:.6f}"
                length += ha
            elif L.get("type") in ("dip_black", "dip_white"):
                _, hb, _ = half(L)
                f += f",setpts=N/({fps:g}*TB),fade=t=in:st=0:d={hb / fps:.6f}:color={'black' if L['type'] == 'dip_black' else 'white'}"
        if R is not None:
            if is_xf(R):
                _, hb, _ = half(R)
                fz, _ = _pad_frames(R, fps)
                f += f",tpad=stop_mode=clone:stop_duration={(fz + hb) / fps:.6f}"
                length += fz + hb
            else:
                fz, _ = _pad_frames(R, fps)
                if fz:
                    f += f",tpad=stop_mode=clone:stop_duration={fz / fps:.6f}"
                    length += fz
                f += f",setpts=N/({fps:g}*TB)"
                if R.get("type") in ("dip_black", "dip_white"):
                    ha, _, _ = half(R)
                    f += f",fade=t=out:st={(length - ha) / fps:.6f}:d={ha / fps:.6f}:color={'black' if R['type'] == 'dip_black' else 'white'}"
                elif R.get("type") in FADE_COLOR:
                    d = _frames(R.get("duration_s") or 0, fps)
                    if d > 0:
                        f += f",fade=t=out:st={(length - d) / fps:.6f}:d={d / fps:.6f}:color={FADE_COLOR[R['type']]}"
        if i == k - 1 and close_fade is not None:
            # 集尾收束:末段尾淡出 d 帧到黑/白,再补 hold 帧同色停留(tpad stop_mode=add 生成纯色帧)
            cd, ch = _close_frames(close_fade, fps)
            col = CLOSE_COLOR.get(close_fade.get("type"), "black")
            f += f",setpts=N/({fps:g}*TB)"
            if close_fade.get("type") in CUT_COLOR:
                cd = 0                                    # 切黑:不淡出
            if cd > 0:
                f += f",fade=t=out:st={max(0, length - cd) / fps:.6f}:d={cd / fps:.6f}:color={col}"
            if ch > 0:
                f += f",tpad=stop_mode=add:stop_duration={ch / fps:.6f}:color={col}"
                length += ch
        ov = it.get("overlay")
        if ov:
            # 叠字幕:PNG 输入(-loop 1 -framerate fps -t)→ rgba + alpha 淡入淡出 → 叠到本段首 F 帧,之后 eof_action=pass 直通
            F, fd = int(ov["frames"]), max(1, int(ov.get("fade_f") or 1))
            parts.append(f + f",setpts=N/({fps:g}*TB)[c{i}]")
            parts.append(f"[{ov['in']}]scale={w}:{h}:flags=bicubic,format=rgba,fade=t=in:st=0:d={fd / fps:.6f}:alpha=1,"
                         f"fade=t=out:st={max(0, F - fd) / fps:.6f}:d={fd / fps:.6f}:alpha=1,trim=end_frame={F},setpts=N/({fps:g}*TB)[ov{i}]")
            parts.append(f"[c{i}][ov{i}]overlay=0:0:eof_action=pass:format=auto,format=yuv420p,settb={_tb(fps)},setpts=N/({fps:g}*TB)[s{i}]")
        else:
            parts.append(f + f",setpts=N/({fps:g}*TB)[s{i}]")
        seg_len.append(length)
    cur, cur_len = "[s0]", seg_len[0]
    for i in range(k - 1):
        e = joins[i]
        lab = "[vout]" if i == k - 2 else f"[x{i}]"
        if e is None:
            parts.append(f"{cur}[s{i + 1}]concat=n=2:v=1:a=0,settb={_tb(fps)},setpts=N/({fps:g}*TB){lab}")
            cur_len += seg_len[i + 1]
        elif is_xf(e):
            _, _, df = half(e)
            parts.append(f"{cur}[s{i + 1}]xfade=transition={xfade_name(e)}:duration={df / fps:.6f}"
                         f":offset={(cur_len - df) / fps:.6f},setpts=N/({fps:g}*TB){lab}")
            cur_len += seg_len[i + 1] - df
        else:
            _, hd = _pad_frames(e, fps)
            if hd:
                parts.append(f"color=c=black:s={w}x{h}:r={fps:g}:d={hd / fps:.6f},format=yuv420p,setsar=1,settb={_tb(fps)},"
                             f"trim=end_frame={hd},setpts=N/({fps:g}*TB)[blk{i}]")
                parts.append(f"{cur}[blk{i}][s{i + 1}]concat=n=3:v=1:a=0,settb={_tb(fps)},setpts=N/({fps:g}*TB){lab}")
            else:
                parts.append(f"{cur}[s{i + 1}]concat=n=2:v=1:a=0,settb={_tb(fps)},setpts=N/({fps:g}*TB){lab}")
            cur_len += hd + seg_len[i + 1]
        cur = lab
    if k == 1:
        parts[-1] = parts[-1].replace(f"[s0]", "[vout]") if "[s0]" in parts[-1] else parts[-1]
        if "[vout]" not in parts[-1]:
            parts.append("[s0]null[vout]")
    return ";".join(parts), cur_len


def build_run_filter(chunks, bounds, fps, w, h, open_fade=None):
    """兼容入口:chunks = 各段帧数(连续取自 [0:v]),bounds[i] = 段 i|i+1 之间的条目 → build_chain_filter。"""
    items, starts = [], 0
    for n in chunks:
        items.append({"in": "0:v", "start": starts, "n": n, "overlay": None})
        starts += n
    joins = [bounds.get(i) for i in range(len(chunks) - 1)]
    return build_chain_filter(items, joins, fps, w, h, open_fade)


def _run_chain(run, gframes, boundary_at, fps, built, tmp_dir):
    """把一个渲染 run(组位置列表)展开成段链 items/joins + 额外输入(插入段 mp4 / 叠字 PNG)。built = do_build 的产物索引
    {bid: {"inserts": [{file, frames}], "overlay": {file, frames, fade_f}}}。返回 (items, joins, extra_inputs[(opts, path)], extra_frames)。"""
    items, joins, extra, extra_frames = [], [], [], 0
    cursor, acc, pending_overlay = 0, 0, None
    for pos, i in enumerate(run):
        acc += gframes[i]
        e = boundary_at.get(i) if pos < len(run) - 1 else None
        if e is None and pos < len(run) - 1:
            continue
        items.append({"in": "0:v", "start": cursor, "n": acc, "overlay": pending_overlay})
        pending_overlay = None
        cursor += acc
        acc = 0
        if e is None:
            continue
        joins.append(e)
        b = built.get(_bid(e)) or {}
        for k, x in enumerate(e.get("inserts") or []):
            rec = (b.get("inserts") or [None] * (k + 1))[k] if b.get("inserts") and k < len(b["inserts"]) else None
            if not rec or not Path(rec["file"]).is_file():
                raise SystemExit(f"[FAIL] {_bid(e)} 插入段 {k}({x.get('kind')})未构建,先跑 build")
            extra.append(([], Path(rec["file"])))
            items.append({"in": f"{len(extra)}:v", "start": 0, "n": int(rec["frames"]), "overlay": None})
            extra_frames += int(rec["frames"])
            jo = {"type": x.get("join_out") or "hard_cut"}
            if jo["type"] != "hard_cut":
                jo["duration_s"] = float(x.get("join_out_s") or 0.4)
            joins.append(jo)
        if e.get("overlay_card"):
            ov = b.get("overlay") or {}
            if not ov.get("file") or not Path(ov["file"]).is_file():
                raise SystemExit(f"[FAIL] {_bid(e)} 叠字幕 PNG 未构建,先跑 build")
            extra.append((["-loop", "1", "-framerate", f"{fps:g}", "-t", f"{(int(ov['frames']) + 2) / fps:.4f}"], Path(ov["file"])))
            pending_overlay = {"in": f"{len(extra)}:v", "frames": int(ov["frames"]), "fade_f": int(ov.get("fade_f") or 1)}
    if pending_overlay is not None:
        # 叠字幕的目标组不在本 run 末尾之外——run 的最后一项已含它(items 最后一项在循环里已 append)
        items[-1]["overlay"] = pending_overlay
    return items, joins, extra, extra_frames


def do_render(proj, ep, src_path, out_path, crf=18, preset="medium"):
    require_tools("ffmpeg", "ffprobe")
    if not src_path.is_file():
        raise SystemExit(f"[FAIL] 源粗成片不存在:{src_path}")
    if out_path.resolve() == src_path.resolve():
        raise SystemExit("[FAIL] --out 不得与 --src 同文件(源 cut 永不改动)")
    entries, policy, ok = do_plan(proj, ep, src_path)
    if not ok:
        raise SystemExit("[FAIL] plan 有未落地的转场设计,不渲染")
    src_dur = probe_duration(src_path)
    if abs(src_dur - policy["timeline_total_s"]) > 0.5:
        print(f"[WARN ] 源 cut 实测 {_fmt(src_dur)} 与 timeline 推算 {_fmt(policy['timeline_total_s'])} 差 >0.5s,"
              f"边界时刻以 timeline 为准——请先核对 edit 的 timeline 与 cut 是否同版")
    info = _probe(src_path)
    fps = _fps(info["video"])
    tl = _read_json(_ep_paths(proj, ep)[2])
    rq = requantize_cuts(entries, tl, proj, fps)
    if rq:
        print(f"[INFO ] {rq} 处边界按组片段实际帧数重量化(timeline 浮点截断补正)")
    xf_entries = [e for e in entries if e["type"] in XFADE_OF]
    n_fade = sum(1 for e in entries if e["type"] in FADE_COLOR)
    # 垫片 / 插入段 / 叠字幕都要走分段路径(成片变长或需额外输入)
    pads = [e for e in entries if (sum(_pad_frames(e, fps)) > 0 or e.get("inserts") or e.get("overlay_card")) and e.get("from_group") is not None]
    close = next((e for e in entries if _is_close(e)), None)
    if close is not None and close not in pads:
        pads.append(close)          # 集尾收束一律走分段路径(末组重编码加淡出/停留,其余组仍流拷贝)
    if not xf_entries and not n_fade and not pads:
        print("[INFO ] 无可渲染转场:全片硬切,不产出 cut_v2(cut_v1 即转场定稿)")
        write_ledger(proj, ep, src_path, None, entries, policy, checks=None)
        return None
    built = do_build(proj, ep, src_path, entries=entries, spec=(int(info["video"]["width"]), int(info["video"]["height"]), fps)) if pads else {}
    if n_fade and not pads:
        # fade 类须对整片施加,退回整片路径(现役场景只有 dissolve,此路径极少走)
        return _render_wholefile(proj, ep, src_path, out_path, entries, src_dur, fps,
                                 crf, preset, policy)
    return _render_segmented(proj, ep, src_path, out_path, entries, tl, fps, crf, preset, src_dur, info, bool(pads), built)


def _norm_chain(k, p, fps):
    """输入 k 的归一链:按帧计划修剪(start..start+take)→ 尾帧克隆补 pad 帧 → 色域/像素格式/SAR 归一 → 逐帧重打时间戳。"""
    f = f"[{k}:v]trim=start_frame={p['start']}:end_frame={p['start'] + p['take']},setpts=PTS-STARTPTS"
    if p["pad"]:
        f += f",tpad=stop_mode=clone:stop={p['pad']}"
    return f + f",scale=in_range=auto:out_range=tv,format=yuv420p,setsar=1,setpts=N/({fps:g}*TB)"


def _encode_entries(plans_sel, out, fps, n_frames, crf, preset, timeout, label):
    """若干条目(按序)各自修剪 + 尾帧补齐 + 归一后拼成一段,重编码为恰好 n_frames 帧(不符即 FAIL)。"""
    ins = [a for p in plans_sel for a in ("-i", str(p["src"]))]
    chains = [_norm_chain(k, p, fps) for k, p in enumerate(plans_sel)]
    if len(chains) == 1:
        fc = chains[0] + "[v]"
    else:
        fc = ";".join(c + f"[g{k}]" for k, c in enumerate(chains)) + ";" + \
            "".join(f"[g{k}]" for k in range(len(chains))) + f"concat=n={len(chains)}:v=1:a=0,setpts=N/({fps:g}*TB)[v]"
    _run(["ffmpeg", "-y", "-v", "error", *ins, "-filter_complex", fc, "-map", "[v]",
          "-fps_mode", "passthrough", "-frames:v", str(n_frames),
          "-c:v", "libx264", "-crf", str(crf), "-preset", preset,
          "-pix_fmt", "yuv420p", "-color_range", "tv", "-an", str(out)], timeout=timeout)
    got = count_frames_of(str(out))
    if got != n_frames:
        raise SystemExit(f"[FAIL] {label} 归一后帧数不符:want {n_frames} got {got}")
    return out


def _render_segmented(proj, ep, src_path, out_path, entries, tl, fps, crf, preset, src_dur, info, with_pads, built=None):
    """★分段式渲染(v4.2,清晰度改造;2026-09-17 扩展垫片/fade 边界):成片 = 各组片段 concat 而来,组边界必是
    关键帧——**未涉边界效果的组直接引用原组片段文件流拷贝(零再编码)**,只把叠化 / 垫片 / 淡出边界两侧
    的组合并重编码。相比整片重编码(两遍式=全片 3 代),成片除边界段外保持 1 代编码;且每个渲染段输入
    参数恒定,天然规避 ffmpeg 8 的 filtergraph reinit 帧计数清零坑。有垫片时成片按 Σ垫片变长,源声轨按
    timemap 重映射(黑场声音按 hold_audio)。"""
    xf_entries = [e for e in entries if e["type"] in XFADE_OF]
    vids = ((tl.get("tracks") or {}).get("video")) or tl.get("video") or []
    # 按**位置**索引(同组 id 重复出现也不坍缩);每条目一份帧计划:成片占帧 n = 文件取帧 take + 尾帧补齐 pad
    plans = [_entry_frames(v, i, proj, fps) for i, v in enumerate(vids)]
    order = [p["gid"] for p in plans]
    files = [p["src"] for p in plans]
    gframes = [p["n"] for p in plans]
    for v, p in zip(vids, plans):
        tp = v.get("tail_pad_frames")
        if isinstance(tp, (int, float)) and int(tp) != p["pad"]:
            print(f"[WARN ] {p['gid']} timeline 标注 tail_pad_frames={int(tp)} 与按占时推算的补帧 {p['pad']} 不符,以占时为准")
    # 热边界 = 叠化 + 垫片 +(有垫片时)fade 类;按相邻位置对有序消费匹配(重复邻接不坍缩)
    built = built or {}
    def hot(e):
        if e.get("from_group") is None:
            return False
        return e["type"] in XFADE_OF or _has_extra(e) or (with_pads and e["type"] in FADE_COLOR)
    close_fade = next((e for e in entries if _is_close(e)), None)
    remaining = [e for e in entries if hot(e) and not _is_close(e)]
    open_fade = next((e for e in entries if e.get("from_group") is None and e["type"] in FADE_COLOR), None) if with_pads else None
    boundary_at = {}                            # 位置 i → 该条目(边界在 order[i]|order[i+1] 之间)
    for i in range(len(order) - 1):
        key = (order[i], order[i + 1])
        for e in remaining:
            if (e["from_group"], e["to_group"]) == key:
                boundary_at[i] = e
                remaining.remove(e)
                break
    if remaining:
        raise SystemExit(f"[FAIL] {len(remaining)} 条转场边界在 timeline 组序里找不到相邻位置:"
                         f"{[(e['from_group'], e['to_group']) for e in remaining][:3]}")
    hot_pos = {i for i in boundary_at} | {i + 1 for i in boundary_at}
    if open_fade is not None and order:
        hot_pos.add(0)
    if close_fade is not None and order:
        hot_pos.add(len(order) - 1)
    runs, cur = [], []
    for i in sorted(hot_pos):
        if cur and i == cur[-1] + 1:
            cur.append(i)
        else:
            if cur:
                runs.append(cur)
            cur = [i]
    if cur:
        runs.append(cur)
    run_of = {i: r for r, run in enumerate(runs) for i in run}
    w, h = int((info.get("video") or {}).get("width") or 0), int((info.get("video") or {}).get("height") or 0)
    tmp_dir = out_path.parent
    seg_paths, cleanup = [], []
    pad_frames_total = 0
    for r, run in enumerate(runs):
        run_gids = [order[i] for i in run]
        run_frames = sum(gframes[i] for i in run)
        run_src = tmp_dir / f".xfrun{r}.src.mp4"
        # run 源直接归一重编码(不 -c copy):各组片段各走一条输入链(修剪 + 尾帧补齐 + 归一)再 concat 滤镜,
        # 输入参数逐链恒定,规避 ffmpeg 8 concat demuxer 混 color_range 触发 filtergraph reinit 清零 trim
        # 计数的坑(实测 143 帧渲染段截成 97);run 仅数秒,重编码代价近零且反正要过 xfade
        _encode_entries([plans[i] for i in run], run_src, fps, run_frames, crf=14, preset="fast", timeout=1200,
                        label=f"渲染段源 {run_gids[0]}..{run_gids[-1]}")
        local_entries = [boundary_at[i] for i in run[:-1] if i in boundary_at]
        run_close = close_fade if (close_fade is not None and run[-1] == len(order) - 1) else None
        only_xf = all(e["type"] in XFADE_OF and not _has_extra(e) for e in local_entries) and not (open_fade is not None and run[0] == 0) \
            and run_close is None
        extra_inputs = []
        if only_xf:
            # run 内边界的相对时刻 = run 内前序组帧和(帧准;boundary_at 按位置,无键碰撞)
            local, acc = [], 0
            for i in run[:-1]:
                acc += gframes[i]
                e = boundary_at.get(i)
                if e:
                    local.append({**e, "cut_time_s": acc / fps})
            fc, _ = build_filter(local, run_frames / fps, fps)
            expect = run_frames
        else:
            # 段链:相邻无边界的组合并成段;边界处按条目接缝,插入段 / 叠字 PNG 作额外输入(过场设计 2026-09-24)
            items, joins, extra_inputs, ins_frames = _run_chain(run, gframes, boundary_at, fps, built, tmp_dir)
            fc, expect = build_chain_filter(items, joins, fps, w, h, open_fade if run[0] == 0 else None, close_fade=run_close)
            pad_frames_total += sum(sum(_pad_frames(e, fps)) for e in local_entries) + ins_frames
            if run_close is not None:
                pad_frames_total += _close_frames(run_close, fps)[1]
        run_out = tmp_dir / f".xfrun{r}.out.mp4"
        _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(run_src),
              *[a for opts, pth in extra_inputs for a in (*opts, "-i", str(pth))],
              "-filter_complex", fc, "-map", "[vout]", "-an",
              "-c:v", "libx264", "-crf", str(max(14, crf - 2)), "-preset", preset,
              "-pix_fmt", "yuv420p", "-color_range", "tv", str(run_out)], timeout=7200)
        got = count_frames_of(str(run_out))
        if got != expect:
            raise SystemExit(f"[FAIL] 渲染段 {run_gids[0]}..{run_gids[-1]} 帧数不符:"
                             f"want {expect} got {got}")
        seg_paths.append((run[0], run_out, got))
        cleanup += [run_src, run_out]
    # 非渲染段的组:整文件原样 → 流拷贝;带补帧/修剪的 → 单独按成片画质重编码为恰好 n 帧(DEF-ep06-edit-0002)
    fixed = []
    for i, p in enumerate(plans):
        if i in run_of:
            continue
        if p["verbatim"]:
            # 只取视频流(流拷贝,零再编码):组 clip 自带 AAC 声轨首包 pts 为负(编码器 priming ≈ −0.032s),
            # concat demuxer 即使只 -map 视频也会把这个偏移带进成片时间戳(2026-09-24 实测整片 +0.031s 脱格)
            gp = tmp_dir / f".xfgrp{i}.v.mp4"
            _run(["ffmpeg", "-y", "-v", "error", "-i", str(p["src"]), "-map", "0:v:0", "-c", "copy", str(gp)], timeout=600)
            files[i] = gp
            cleanup.append(gp)
            continue
        gp = tmp_dir / f".xfgrp{i}.mp4"
        _encode_entries([p], gp, fps, p["n"], crf=crf, preset=preset, timeout=1800, label=f"组 {p['gid']}")
        files[i] = gp
        cleanup.append(gp)
        fixed.append(p["gid"] + (f" +{p['pad']}帧" if p["pad"] else "")
                     + (" 修剪" if p["start"] or p["take"] < p["file_frames"] else ""))
    # 总装 concat 列表:copy 组按原文件(或补帧版)、渲染 run 按渲染段,严格组序
    # 每个文件显式钉 duration = 占帧/fps:concat demuxer 默认按**容器时长**(组 clip 原生声轨常比画面长几十 ms)
    # 推下一文件的起点,视频 pts 会脱离 1/fps 网格、成片流时长虚长(2026-09-24 ep06 实测 +0.12s,抽帧核对漂移)
    final_lst = tmp_dir / ".xf_final.txt"
    lines, i = [], 0
    seg_by_start = {s: (p, n) for s, p, n in seg_paths}
    while i < len(order):
        if i in run_of:
            run = runs[run_of[i]]
            seg, n = seg_by_start[run[0]]
            lines.append(f"file '{seg.resolve()}'\nduration {n / fps:.6f}\n")
            i = run[-1] + 1
        else:
            lines.append(f"file '{files[i].resolve()}'\nduration {gframes[i] / fps:.6f}\n")
            i += 1
    final_lst.write_text("".join(lines))
    cleanup.append(final_lst)
    tmp = out_path.with_name(out_path.stem + ".rendering.mp4")
    n_copy = sum(1 for i in range(len(order)) if i not in run_of) - len(fixed)
    n_pad = sum(1 for e in boundary_at.values() if sum(_pad_frames(e, fps)) > 0)
    n_ins = sum(1 for e in boundary_at.values() if e.get("inserts"))
    n_ov = sum(1 for e in boundary_at.values() if e.get("overlay_card"))
    if close_fade is not None:
        cd, ch = _close_frames(close_fade, fps)
        print(f"[RUN  ] 集尾收束:{close_fade['type']} 淡出 {cd} 帧" + (f" + 停留 {ch} 帧/{close_fade.get('hold_audio')}" if ch else "") + f"(来源 {close_fade.get('source')})")
    print(f"[RUN  ] 分段式转场渲染:{len(xf_entries)} 处叠化 + {n_pad} 处垫片 + {n_ins} 处插入段 + {n_ov} 处叠字 / {len(runs)} 个渲染段"
          f"(重编码 {len(order) - n_copy - len(fixed)} 组)+ {len(fixed)} 组补帧/修剪重编码 + {n_copy} 组流拷贝"
          f" → {out_path.relative_to(proj)}")
    if fixed:
        print(f"[INFO ] 按 timeline 占帧补齐/修剪的组:{', '.join(fixed)}")
    ops = pad_ops(entries, fps)
    # 源 cut 带声轨时随总装带回:无垫片流拷贝(模块契约:声轨不碰);有垫片按 timemap 重映射(黑场声音按 hold_audio)
    has_audio = bool((_probe(src_path).get("audio") or {}))
    a_args, a_tmp, a_src = [], None, None
    if has_audio and ops:
        a_tmp = tmp_dir / ".xf_audio.remap.wav"
        res = timemap.remap_audio(src_path, a_tmp, ops, src_dur=src_dur)
        cleanup.append(a_tmp)
        print(f"[RUN  ] 源声轨按 timemap 重映射:{timemap.describe(ops)} → {res['out_duration']:.3f}s")
        a_src, a_args = a_tmp, ["-i", str(a_tmp), "-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-b:a", "192k"]
    elif has_audio:
        a_src, a_args = src_path, ["-i", str(src_path), "-map", "0:v:0", "-map", "1:a:0", "-c:a", "copy"]
    total_frames = sum(gframes) + pad_frames_total
    if has_audio and close_fade is not None:
        # 源 cut 自带声轨随集尾收束:画面全黑时刻 = 声轨(已按 timemap 重映射)末尾 − 尾停留;fade 策略在淡出窗口内 afade(切黑类 = 1 帧防爆音),
        # mute 策略到黑即静音;再补静音到与画面等长(停留段)。滤镜不能配 -c:a copy,此时改 aac 编码
        cd, ch = _close_frames(close_fade, fps)
        black_at = max(0.0, src_dur + timemap.total_delta(ops))   # 停留之前的声轨长度(= 画面长 − 停留)
        if str(close_fade.get("hold_audio") or "fade") == "mute":
            af = f"atrim=end={black_at:.6f},asetpts=PTS-STARTPTS"
        else:
            af = f"afade=t=out:st={max(0.0, black_at - max(cd, 1) / fps):.6f}:d={max(cd, 1) / fps:.6f}"
        af += f",apad=whole_dur={total_frames / fps:.6f}"
        a_args = ["-i", str(a_src), "-map", "0:v:0", "-map", "1:a:0", "-af", af, "-c:a", "aac", "-b:a", "192k"]
    _run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
          "-i", str(final_lst), *a_args, "-c:v", "copy",
          "-movflags", "+faststart", str(tmp)],
         timeout=1200)
    got = count_frames_of(str(tmp))
    if got != total_frames:
        raise SystemExit(f"[FAIL] 总装帧数不符:want {total_frames} got {got}")
    vdur = float((_probe(tmp).get("video") or {}).get("duration") or 0)
    if abs(vdur - total_frames / fps) > 1.0 / fps + 0.005:
        raise SystemExit(f"[FAIL] 总装视频流时长 {_fmt(vdur)} ≠ 帧数/fps {_fmt(total_frames / fps)}(时间戳脱离帧网格)")
    for p in cleanup:
        p.unlink(missing_ok=True)
    if out_path.exists():
        out_path.unlink()
    tmp.rename(out_path)
    print(f"[DONE ] {out_path.relative_to(proj)} = {_fmt(probe_duration(out_path))}"
          f"(源 {_fmt(src_dur)};{got} 帧,{n_copy}/{len(order)} 组零再编码"
          + (f",垫片/插入 +{pad_frames_total} 帧" if pad_frames_total else "") + ")")
    return out_path


def _render_wholefile(proj, ep, src_path, out_path, entries, src_dur, fps, crf, preset, policy):
    """整片路径(仅 fade 类需要;两遍式规避 ffmpeg 8 filtergraph reinit)。"""
    fc, n_xf = build_filter(entries, src_dur, fps)
    n_fade = sum(1 for e in entries if e["type"] in FADE_COLOR)
    norm = out_path.with_name(out_path.stem + ".norm.tmp.mp4")
    src_frames = count_frames_of(src_path)
    _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src_path),
          "-vf", "scale=in_range=auto:out_range=tv,format=yuv420p,setsar=1",
          "-fps_mode", "passthrough",
          "-frames:v", str(src_frames), "-c:v", "libx264", "-crf", "14",
          "-preset", "fast", "-pix_fmt", "yuv420p", "-color_range", "tv",
          "-an", str(norm)], timeout=7200)
    got = count_frames_of(str(norm))
    if got != src_frames:
        norm.unlink(missing_ok=True)
        raise SystemExit(f"[FAIL] 归一中间件帧数不符:want {src_frames} got {got}(源 cut 时间戳异常?)")
    tmp = out_path.with_name(out_path.stem + ".rendering.mp4")
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-i", str(norm), "-i", str(src_path),
           "-filter_complex", fc, "-map", "[vout]", "-map", "1:a?", "-c:a", "copy",
           "-c:v", "libx264", "-crf", str(crf), "-preset", preset, "-pix_fmt", "yuv420p",
           "-color_range", "tv", "-movflags", "+faststart", str(tmp)]
    print(f"[RUN  ] ffmpeg 转场渲染(整片):xfade {n_xf} 处 + fade {n_fade} 处 → "
          f"{out_path.relative_to(proj)}(两遍式,libx264 crf{crf})")
    _run(cmd, timeout=7200)
    norm.unlink(missing_ok=True)
    if out_path.exists():
        out_path.unlink()
    tmp.rename(out_path)
    print(f"[DONE ] {out_path.relative_to(proj)} = {_fmt(probe_duration(out_path))}(源 {_fmt(src_dur)})")
    return out_path



# ---------------------------------------------------------------- build(插入段 / 叠字幕,过场设计 2026-09-24)

def _spec_of(proj, ep, src_path):
    """(w, h, fps):源 cut 有则用它;否则 timeline 首条目 clip;否则 assets/clips/epNN 首个 mp4。"""
    cands = []
    if src_path and Path(src_path).is_file():
        cands.append(Path(src_path))
    tl = _read_json(_ep_paths(proj, ep)[2]) if _ep_paths(proj, ep)[2].is_file() else {}
    for v in ((tl.get("tracks") or {}).get("video")) or tl.get("video") or []:
        if v.get("src") and (proj / v["src"]).is_file():
            cands.append(proj / v["src"])
            break
    cdir = proj / "assets" / "clips" / ep
    if cdir.is_dir():
        cands += sorted(cdir.glob("grp*.mp4"))[:1]
    for c in cands:
        info = _probe(c)
        if info.get("video") and info["video"].get("width"):
            return int(info["video"]["width"]), int(info["video"]["height"]), _fps(info["video"])
    raise SystemExit(f"[FAIL] 无法确定画幅/帧率:缺源 cut 与组 clip({ep})")


def _ins_dir(proj, ep, e):
    return proj / "edit" / ep / INSERT_DIR / _bid(e)


def _entries_from_shot_list(proj, ep):
    """无 timeline(尚未粗剪)时按 shot_list 组序造条目(cut_time_s 未知 = None),供 build / preview 用。"""
    sl_p = _ep_paths(proj, ep)[1]
    if not sl_p.is_file():
        raise SystemExit(f"[FAIL] 缺 {sl_p.relative_to(proj)}")
    sl = _read_json(sl_p)
    groups = sl.get("generation_groups") or []
    out = []
    for a, b in zip(groups, groups[1:]):
        t = transition_of(b)
        e = {"from_group": a.get("group_id"), "to_group": b.get("group_id"), "cut_time_s": None,
             "at_shot": f"{(a.get('shots') or [a.get('group_id')])[-1]}->{(b.get('shots') or [b.get('group_id')])[0]}",
             "type": t["type"]}
        if t["type"] in TRANSITION_RENDERABLE:
            e["duration_s"] = float(t.get("duration_s") or 0.0)
        fz, hd, ha = pad_of(t)
        if fz:
            e["freeze_s"] = fz
        if hd:
            e["hold_s"], e["hold_audio"] = hd, ha
        if t.get("join"):
            e["join"] = dict(t["join"])
        if inserts_of(t):
            e["inserts"] = [dict(x) for x in inserts_of(t)]
        if t.get("overlay_card"):
            e["overlay_card"] = dict(t["overlay_card"])
        e["intent"], e["reason"] = t.get("intent"), t.get("reason")
        e["fingerprint"] = td.source_fingerprint(proj, t)
        out.append(e)
    close = td.effective_episode_close(proj, None, sl)
    if close and groups:
        g_last = groups[-1]
        e = {"from_group": g_last.get("group_id"), "to_group": None, "cut_time_s": None,
             "at_shot": f"{(g_last.get('shots') or [g_last.get('group_id')])[-1]}->{CLOSE_ID}", "type": close["type"],
             "duration_s": float(close["duration_s"]), "intent": "episode_close", "reason": close.get("reason"),
             "source": close.get("source"), "fingerprint": td.close_fingerprint(close)}
        if float(close.get("hold_s") or 0) > 0:
            e["hold_s"], e["hold_audio"] = float(close["hold_s"]), str(close.get("hold_audio") or "fade")
        out.append(e)
    return out


def _plan_entries(proj, ep, src_path):
    """有 timeline 走 make_plan(不写回),否则按 shot_list 组序。"""
    ed, sl_p, tl_p, _, _ = _ep_paths(proj, ep)
    if tl_p.is_file() and sl_p.is_file():
        entries, _, _ = make_plan(_read_json(sl_p), _read_json(tl_p), proj, src_path if src_path and Path(src_path).is_file() else None)
        return entries
    return _entries_from_shot_list(proj, ep)


def _prev_tail_png(proj, ep, e, src_path, fps, out):
    """前组尾帧(字卡 blur_prev 底):源 cut 边界前一帧 > clips/<from>.last_frame.png。"""
    t = e.get("cut_time_s")
    if src_path and Path(src_path).is_file() and isinstance(t, (int, float)):
        try:
            _run(["ffmpeg", "-y", "-v", "error", "-ss", f"{max(0.0, float(t) - 1.0 / fps):.6f}", "-i", str(src_path), "-frames:v", "1", str(out)], timeout=120)
            return out
        except RuntimeError:
            pass
    lf = proj / "assets" / "clips" / ep / f"{e.get('from_group')}.last_frame.png"
    return lf if lf.is_file() else None


def _x264(out, extra=()):
    return ["-c:v", "libx264", "-crf", "14", "-preset", "fast", "-pix_fmt", "yuv420p", "-color_range", "tv", *extra, str(out)]


def _overlay_inputs_and_filter(base_label, ov_png, ov_frames, fps, w, h, k_in):
    fd = max(1, _frames(OVERLAY_FADE_S, fps))
    flt = (f"[{k_in}:v]scale={w}:{h}:flags=bicubic,format=rgba,fade=t=in:st=0:d={fd / fps:.6f}:alpha=1,"
           f"fade=t=out:st={max(0, ov_frames - fd) / fps:.6f}:d={fd / fps:.6f}:alpha=1,trim=end_frame={ov_frames},setpts=N/({fps:g}*TB)[ovx];"
           f"{base_label}[ovx]overlay=0:0:eof_action=pass:format=auto,format=yuv420p,setpts=N/({fps:g}*TB)[v]")
    ins = ["-loop", "1", "-framerate", f"{fps:g}", "-t", f"{(ov_frames + 2) / fps:.4f}", "-i", str(ov_png)]
    return ins, flt


def _build_insert(proj, ep, e, k, x, spec, d, src_path, log):
    w, h, fps = spec
    n = max(2, _frames(x.get("duration_s") or 0.0, fps))
    kind = x.get("kind")
    out = d / f"ins{k}.mp4"
    rec = {"kind": kind, "file": str(out), "frames": n, "duration_s": round(n / fps, 6)}
    ov = x.get("overlay_card") if isinstance(x.get("overlay_card"), dict) else None
    ov_args, ov_flt, ov_png = [], None, None
    font = td.resolve_card_font(proj)          # 项目字体 refs/fonts/ 优先(settings.transitions.card_font 可指定)
    if ov and kind in ("establishing", "timelapse"):
        ov_png = d / f"ins{k}.overlay.png"
        info = td.render_overlay_png(ov.get("lines") or [], w, h, ov_png, position=str(ov.get("position") or "bottom_left"), font=font)
        ovf = min(n, max(2, _frames(ov.get("duration_s") or 2.5, fps)))
        ov_args, ov_flt = _overlay_inputs_and_filter("[base]", ov_png, ovf, fps, w, h, 1)
        rec["overlay"] = {"file": str(ov_png), "frames": ovf, "lines": info["lines"]}
    if kind == "title_card":
        card = x.get("card") if isinstance(x.get("card"), dict) else {}
        bg = str(card.get("bg") or "black")
        bg_img = _prev_tail_png(proj, ep, e, src_path, fps, d / "prev_tail.png") if bg == "blur_prev" else None
        png = d / ("card.png" if k == 0 else f"card{k}.png")
        info = td.render_card_png(card.get("lines") or [], w, h, png, bg=bg, color=card.get("color"), bg_image=bg_img, font=font)
        ff = max(1, _frames(card.get("fade_s", 0.4) or 0.0, fps))
        color = "white" if bg == "white" else "black"
        vf = (f"scale={w}:{h},setsar=1,format=yuv420p,fade=t=in:st=0:d={ff / fps:.6f}:color={color},"
              f"fade=t=out:st={max(0, n - ff) / fps:.6f}:d={ff / fps:.6f}:color={color},setpts=N/({fps:g}*TB)")
        _run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-framerate", f"{fps:g}", "-t", f"{(n + 2) / fps:.4f}", "-i", str(png),
              "-vf", vf, "-frames:v", str(n), "-an", *_x264(out)], timeout=600)
        rec.update({"card": {**info, "bg": bg}, "thumb": str(png)})
        log(f"         ins{k} 字卡 {n} 帧 {info['lines']} bg={bg} 字体 {Path(info['font']).name if info.get('font') else '默认'}"
            f" 对比度 {info['contrast']}{'' if info['in_safe_area'] else ' ⚠ 文字出安全区'}")
    elif kind == "establishing":
        src = x.get("source") if isinstance(x.get("source"), dict) else {}
        mode = str(src.get("mode") or "pano_sweep")
        if mode == "pano_sweep":
            seq = d / f"seq{k}"
            files = td.pano_sweep_frames(proj, src, w, h, n, seq)
            thumb = d / ("establishing.jpg" if k == 0 else f"establishing{k}.jpg")
            shutil.copyfile(files[0], thumb)
            base_flt = f"[0:v]scale={w}:{h},setsar=1,format=yuv420p,setpts=N/({fps:g}*TB)"
            if ov_flt:
                fc = base_flt + "[base];" + ov_flt
                _run(["ffmpeg", "-y", "-v", "error", "-framerate", f"{fps:g}", "-i", str(seq / "f%04d.jpg"), *ov_args,
                      "-filter_complex", fc, "-map", "[v]", "-frames:v", str(n), "-an", *_x264(out)], timeout=900)
            else:
                _run(["ffmpeg", "-y", "-v", "error", "-framerate", f"{fps:g}", "-i", str(seq / "f%04d.jpg"),
                      "-vf", base_flt[5:], "-frames:v", str(n), "-an", *_x264(out)], timeout=900)
            for f in files:
                f.unlink(missing_ok=True)
            try:
                seq.rmdir()
            except OSError:
                pass
            rec.update({"thumb": str(thumb), "source": {k2: src.get(k2) for k2 in ("scene_id", "mode", "anchor_id", "scheme", "sweep_deg", "fov_v_deg")}})
            log(f"         ins{k} 定场(全景扫动 {src.get('scene_id')}/{src.get('anchor_id')}/{src.get('scheme')}){n} 帧" + (" +叠字" if ov else ""))
        elif mode == "plate_kenburns":
            plate = proj / str(src.get("file") or "")
            if not plate.is_file():
                raise SystemExit(f"[FAIL] {_bid(e)} 定场母图不存在:{src.get('file')}")
            z = float(src.get("zoom") or 1.08)
            base_flt = (f"[0:v]scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,crop={w * 2}:{h * 2},"
                        f"zoompan=z='1+({z:.4f}-1)*on/{n}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={w}x{h}:fps={fps:g},"
                        f"setsar=1,format=yuv420p,setpts=N/({fps:g}*TB)")
            args = ["ffmpeg", "-y", "-v", "error", "-loop", "1", "-framerate", f"{fps:g}", "-t", f"{(n + 2) / fps:.4f}", "-i", str(plate)]
            if ov_flt:
                _run([*args, *ov_args, "-filter_complex", base_flt + "[base];" + ov_flt, "-map", "[v]", "-frames:v", str(n), "-an", *_x264(out)], timeout=900)
            else:
                _run([*args, "-vf", base_flt[5:], "-frames:v", str(n), "-an", *_x264(out)], timeout=900)
            thumb = d / ("establishing.jpg" if k == 0 else f"establishing{k}.jpg")
            _run(["ffmpeg", "-y", "-v", "error", "-i", str(out), "-frames:v", "1", str(thumb)], timeout=120)
            rec.update({"thumb": str(thumb), "source": {k2: src.get(k2) for k2 in ("scene_id", "mode", "file", "scheme", "zoom")}})
            log(f"         ins{k} 定场(母图推进 {plate.name}){n} 帧" + (" +叠字" if ov else ""))
        elif mode == "i2v":
            # 生成式定场空镜(2026-09-26):Phase 7 p7-transition-clips 图生视频出的 clip;缺失 = 记 missing(check inserts_built FAIL),
            # 由 orchestrator 派 video-generation 出片后再 build;不得在此退回全景横摇顶替(设计已定稿为 i2v)
            f = proj / str(src.get("file") or td.clip_paths(ep, _bid(e), "establishing")["file"])
            if not f.is_file():
                rec.update({"missing": True, "expected": str(f.relative_to(proj)), "source": {k2: src.get(k2) for k2 in ("scene_id", "mode", "file", "still", "scheme")}})
                log(f"         ins{k} 定场 clip 缺失:{f.relative_to(proj)}(由 p7-transition-clips / video-generation 按首帧静帧生成后再 build)")
                return rec
            base_flt = (f"[0:v]fps={fps:g},scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,format=yuv420p,"
                        f"tpad=stop_mode=clone:stop_duration=10,setpts=N/({fps:g}*TB)")
            if ov_flt:
                _run(["ffmpeg", "-y", "-v", "error", "-i", str(f), *ov_args, "-filter_complex", base_flt + "[base];" + ov_flt,
                      "-map", "[v]", "-frames:v", str(n), "-an", *_x264(out)], timeout=900)
            else:
                _run(["ffmpeg", "-y", "-v", "error", "-i", str(f), "-vf", base_flt[5:], "-frames:v", str(n), "-an", *_x264(out)], timeout=900)
            thumb = d / ("establishing.jpg" if k == 0 else f"establishing{k}.jpg")
            _run(["ffmpeg", "-y", "-v", "error", "-i", str(out), "-frames:v", "1", str(thumb)], timeout=120)
            rec.update({"thumb": str(thumb), "source_file": str(f.relative_to(proj)),
                        "source": {k2: src.get(k2) for k2 in ("scene_id", "mode", "file", "still", "scheme")}})
            log(f"         ins{k} 定场(图生视频 {f.name})→ {n} 帧" + (" +叠字" if ov else ""))
        else:
            raise SystemExit(f"[FAIL] {_bid(e)} 定场模式 {mode} 不支持(可用 pano_sweep / plate_kenburns / i2v)")
    elif kind == "timelapse":
        src = x.get("source") if isinstance(x.get("source"), dict) else {}
        a, b = d / f"tl{k}_a.jpg", d / f"tl{k}_b.jpg"
        td.pano_still(proj, src, w, h, a, scheme=src.get("scheme_from"))
        td.pano_still(proj, src, w, h, b, scheme=src.get("scheme_to"))
        hold = max(2, n // 4)
        xf = n - 2 * hold
        seg = hold + xf
        fc = (f"[0:v]scale={w}:{h},setsar=1,format=yuv420p,settb=AVTB,setpts=N/({fps:g}*TB)[a];[1:v]scale={w}:{h},setsar=1,format=yuv420p,settb=AVTB,setpts=N/({fps:g}*TB)[b];"
              f"[a][b]xfade=transition=fade:duration={xf / fps:.6f}:offset={hold / fps:.6f},setpts=N/({fps:g}*TB)[base]")
        args = ["ffmpeg", "-y", "-v", "error", "-loop", "1", "-framerate", f"{fps:g}", "-t", f"{(seg + 1) / fps:.4f}", "-i", str(a),
                "-loop", "1", "-framerate", f"{fps:g}", "-t", f"{(seg + 1) / fps:.4f}", "-i", str(b)]
        if ov_flt:
            ov_args2, ov_flt2 = _overlay_inputs_and_filter("[base]", ov_png, rec["overlay"]["frames"], fps, w, h, 2)
            _run([*args, *ov_args2, "-filter_complex", fc + ";" + ov_flt2, "-map", "[v]", "-frames:v", str(n), "-an", *_x264(out)], timeout=900)
        else:
            _run([*args, "-filter_complex", fc + ";[base]null[v]", "-map", "[v]", "-frames:v", str(n), "-an", *_x264(out)], timeout=900)
        thumb = d / ("establishing.jpg" if k == 0 else f"establishing{k}.jpg")
        shutil.copyfile(a, thumb)
        rec.update({"thumb": str(thumb), "source": {k2: src.get(k2) for k2 in ("scene_id", "anchor_id", "scheme_from", "scheme_to")}})
        log(f"         ins{k} 时光流转 {src.get('scheme_from')} → {src.get('scheme_to')} {n} 帧")
    elif kind == "bridge":
        f = proj / (x.get("file") or f"{BRIDGE_DIR}/{ep}/{_bid(e)}.bridge.mp4")
        if not f.is_file():
            rec.update({"missing": True, "expected": str(f.relative_to(proj))})
            log(f"         ins{k} 桥接 clip 缺失:{f.relative_to(proj)}(由 video-generation 按首尾帧生成后再 build)")
            return rec
        _run(["ffmpeg", "-y", "-v", "error", "-i", str(f), "-vf", f"fps={fps:g},scale={w}:{h}:flags=bicubic,setsar=1,format=yuv420p,"
              f"tpad=stop_mode=clone:stop_duration=10,setpts=N/({fps:g}*TB)", "-frames:v", str(n), "-an", *_x264(out)], timeout=900)
        rec.update({"source_file": str(f.relative_to(proj))})
        log(f"         ins{k} 桥接 {f.name} → {n} 帧")
    else:
        raise SystemExit(f"[FAIL] 未知插入段类型 {kind}")
    got = count_frames_of(str(out))
    if got != n:
        raise SystemExit(f"[FAIL] {_bid(e)} ins{k} 帧数不符:want {n} got {got}")
    return rec


def do_build(proj, ep, src_path, entries=None, spec=None, force=False, only=None, log=print):
    """渲染各边界的插入段与叠字幕 PNG 到 edit/epNN/transitions/<B-from-to>/(按指纹幂等)。返回 {bid: meta}。"""
    require_tools("ffmpeg", "ffprobe")
    entries = entries if entries is not None else _plan_entries(proj, ep, src_path)
    spec = spec or _spec_of(proj, ep, src_path)
    w, h, fps = spec
    built, n_new = {}, 0
    for e in entries:
        if e.get("from_group") is None or not (e.get("inserts") or e.get("overlay_card")):
            continue
        bid = _bid(e)
        if only and bid not in only and e.get("to_group") not in only:
            continue
        d = _ins_dir(proj, ep, e)
        d.mkdir(parents=True, exist_ok=True)
        meta = _read_json(d / "meta.json") if (d / "meta.json").is_file() else {}
        fp = e.get("fingerprint") or ""
        fresh = (meta.get("fingerprint") == fp and meta.get("spec") == [w, h, round(fps, 4)]
                 and all(Path(r.get("file", "")).is_file() or r.get("missing") for r in (meta.get("inserts") or []))
                 and (not e.get("overlay_card") or Path((meta.get("overlay") or {}).get("file", "")).is_file()))
        if fresh and not force:
            built[bid] = meta
            continue
        log(f"[BUILD] {bid} {e.get('at_shot')}:{len(e.get('inserts') or [])} 段插入" + (" + 叠字幕" if e.get("overlay_card") else ""))
        recs = [_build_insert(proj, ep, e, k, x, spec, d, src_path, log) for k, x in enumerate(e.get("inserts") or [])]
        ov_meta = None
        if e.get("overlay_card"):
            oc = e["overlay_card"]
            png = d / "overlay.png"
            info = td.render_overlay_png(oc.get("lines") or [], w, h, png, position=str(oc.get("position") or "bottom_left"),
                                         font=td.resolve_card_font(proj))
            ov_meta = {"file": str(png), "frames": max(2, _frames(oc.get("duration_s") or 2.5, fps)), "fade_f": max(1, _frames(OVERLAY_FADE_S, fps)),
                       "lines": info["lines"], "position": oc.get("position") or "bottom_left", "font": info.get("font")}
            log(f"         叠字幕 {info['lines']} {ov_meta['frames']} 帧 @{ov_meta['position']} 字体 {Path(info['font']).name if info.get('font') else '默认'}")
        for r in recs:
            for key in ("file", "thumb"):
                if r.get(key):
                    r[key + "_rel"] = str(Path(r[key]).relative_to(proj))
        meta = {"id": bid, "from_group": e.get("from_group"), "to_group": e.get("to_group"), "fingerprint": fp, "spec": [w, h, round(fps, 4)],
                "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "inserts": recs, "overlay": ov_meta,
                "preview": (meta.get("preview") if meta.get("preview_fingerprint") == fp else None), "preview_fingerprint": meta.get("preview_fingerprint")}
        _write_json(d / "meta.json", meta)
        built[bid] = meta
        n_new += 1
    if n_new:
        log(f"[DONE ] 插入段构建 {n_new} 处(其余按指纹复用)")
    return built


# ---------------------------------------------------------------- preview(边界小片)

def _clip_plan(proj, ep, gid, fps, tl):
    """组 gid 的取帧计划:timeline 首个该组条目(修剪/补帧口径与成片一致)> assets/clips/epNN/gid.mp4 整文件。"""
    vids = (((tl.get("tracks") or {}).get("video")) or tl.get("video") or []) if tl else []
    for i, v in enumerate(vids):
        if _entry_gid(v, i) == gid and v.get("src") and (proj / v["src"]).is_file():
            return _entry_frames(v, i, proj, fps)
    clip = proj / "assets" / "clips" / ep / f"{gid}.mp4"
    if not clip.is_file():
        raise SystemExit(f"[FAIL] 组 {gid} 没有视频(timeline 无条目且缺 {clip.relative_to(proj)}),无法出预览")
    n = _file_frames(clip)
    return {"gid": gid, "src": clip, "start": 0, "n": n, "take": n, "pad": 0, "file_frames": n, "verbatim": True}


def do_preview(proj, ep, src_path, targets, force=False, log=print):
    """边界预览小片:前组尾 2s + [接缝/垫片/插入段] + 本组首 2s → edit/epNN/transitions/<bid>/preview.mp4(静音,与成片同一滤镜链)。
    targets = {B-id 或 to_group} 集合;空 = 全部非默认边界。"""
    require_tools("ffmpeg", "ffprobe")
    spec = _spec_of(proj, ep, src_path)
    w, h, fps = spec
    entries = _plan_entries(proj, ep, src_path)
    tl_p = _ep_paths(proj, ep)[2]
    tl = _read_json(tl_p) if tl_p.is_file() else {}
    picked = [e for e in entries if e.get("from_group") is not None
              and ((not targets and (e["type"] != "hard_cut" or _has_extra(e))) or (targets and (_bid(e) in targets or (e.get("to_group") or CLOSE_ID) in targets)))]
    if not picked:
        raise SystemExit("[FAIL] 没有匹配的边界(--boundary B-grpA-grpB 或 --to grpNNN)")
    built = do_build(proj, ep, src_path, entries=picked, spec=spec, force=force, log=log)
    out_paths = []
    pre = _frames(PREVIEW_PAD_S, fps)
    for e in picked:
        bid = _bid(e)
        d = _ins_dir(proj, ep, e)
        d.mkdir(parents=True, exist_ok=True)
        meta = _read_json(d / "meta.json") if (d / "meta.json").is_file() else {}
        out = d / "preview.mp4"
        if out.is_file() and meta.get("preview_fingerprint") == e.get("fingerprint") and not force and (meta.get("preview") or {}).get("spec") == [w, h, round(fps, 4)]:
            out_paths.append(out)
            continue
        pa = _clip_plan(proj, ep, e["from_group"], fps, tl)
        ta = min(pre, pa["take"])
        a = {**pa, "start": pa["start"] + pa["take"] - ta, "take": ta, "n": ta, "pad": 0}
        run_src = d / ".preview.src.mp4"
        if _is_close(e):
            # 集尾收束预览:末组尾 2s + 淡出 + 停留(无后组)
            tb, extra, ins_frames = 0, [], 0
            _encode_entries([a], run_src, fps, ta, crf=14, preset="fast", timeout=600, label=f"预览源 {bid}")
            fc, expect = build_chain_filter([{"in": "0:v", "start": 0, "n": ta, "overlay": None}], [], fps, w, h, None, close_fade=e)
        else:
            pb = _clip_plan(proj, ep, e["to_group"], fps, tl)
            tb = min(pre, pb["take"])
            b = {**pb, "take": tb, "n": tb, "pad": 0}
            _encode_entries([a, b], run_src, fps, ta + tb, crf=14, preset="fast", timeout=600, label=f"预览源 {bid}")
            items, joins, extra, ins_frames = _run_chain([0, 1], [ta, tb], {0: e}, fps, built, d)
            fc, expect = build_chain_filter(items, joins, fps, w, h, None)
        tmp = d / ".preview.out.mp4"
        _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(run_src),
              *[x for opts, pth in extra for x in (*opts, "-i", str(pth))],
              "-filter_complex", fc, "-map", "[vout]", "-an", "-c:v", "libx264", "-crf", "18", "-preset", "fast",
              "-pix_fmt", "yuv420p", "-color_range", "tv", "-movflags", "+faststart", str(tmp)], timeout=1800)
        got = count_frames_of(str(tmp))
        if got != expect:
            raise SystemExit(f"[FAIL] 预览 {bid} 帧数不符:want {expect} got {got}")
        run_src.unlink(missing_ok=True)
        if out.exists():
            out.unlink()
        tmp.rename(out)
        meta = {**meta, "id": bid, "from_group": e["from_group"], "to_group": e["to_group"], "fingerprint": meta.get("fingerprint") or e.get("fingerprint"),
                "spec": meta.get("spec") or [w, h, round(fps, 4)],
                "preview": {"file": str(out.relative_to(proj)), "frames": got, "pre_s": round(ta / fps, 3), "post_s": round(tb / fps, 3),
                            "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "spec": [w, h, round(fps, 4)]},
                "preview_fingerprint": e.get("fingerprint")}
        _write_json(d / "meta.json", meta)
        log(f"[DONE ] 预览 {bid} {e.get('at_shot')} → {out.relative_to(proj)}({got} 帧 = 前 {ta} + 插入 {ins_frames} + 后 {tb} − 接缝重叠"
            + (f" + 尾停留 {_close_frames(e, fps)[1]}" if _is_close(e) else "") + ")")
        out_paths.append(out)
    return out_paths

# ---------------------------------------------------------------- check

def _gray_frame(path, t, w, h):
    """t 秒处一帧灰度字节(缩放到 w×h)。末帧处 -ss 常因时间基取整落到末帧之后而抽空(issue #88),
    抽空时逐级回退(≈半帧/一帧/0.1s)重取;仍空返回 b"",调用方须按「取帧失败」处理而非比对差值。"""
    for back in (0.0, 0.02, 0.04, 0.1):
        if back and t - back < 0:
            break
        b = _run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t - back):.6f}", "-i", str(path), "-frames:v", "1",
                  "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "gray", "-"], timeout=120, binary=True)
        if b:
            return b
    return b""


def _mean(b):
    return sum(b) / len(b) if b else 0.0


def _mad(a, b):
    n = min(len(a), len(b))
    return sum(abs(a[i] - b[i]) for i in range(n)) / n if n else 999.0


def _blend_mad(x, a, b):
    n = min(len(x), len(a), len(b))
    return sum(abs(x[i] - (a[i] + b[i]) / 2) for i in range(n)) / n if n else 999.0


def do_check(proj, ep, src_path, out_path, write=True):
    require_tools("ffmpeg", "ffprobe")
    ed, sl_p, tl_p, _, _ = _ep_paths(proj, ep)
    results, fails = [], 0

    def rec(name, ok, detail="", warn=False):
        nonlocal fails
        tag = "PASS" if ok else ("WARN" if warn else "FAIL")
        if not ok and not warn:
            fails += 1
        results.append({"check": name, "result": tag, "detail": detail})
        print(f"[{tag:<5}] {name}: {detail}")

    if not (sl_p.is_file() and tl_p.is_file()):
        rec("transitions_planned", False, "缺 shot_list.json 或 timeline.json")
        return False, results
    shot_list, timeline = _read_json(sl_p), _read_json(tl_p)
    planned = timeline.get("transitions")
    bounds, total, _ = boundaries_of(timeline, proj, shot_list)
    if not isinstance(planned, list):
        rec("transitions_planned", False, "timeline.json 无 transitions(先跑 plan)")
        planned = []
    else:
        keys = [(e.get("from_group"), e.get("to_group")) for e in planned]
        want = [(b["from_group"], b["to_group"]) for b in bounds]
        missing = [k for k in want if k not in keys]
        extra = [k for k in keys if k not in want and k[0] is not None and k[1] is not None]
        dup = [k for k in set(keys) if keys.count(k) > 1]
        rec("transitions_planned", not (missing or extra or dup),
            f"组边界 {len(bounds)} / 条目 {len(planned)};缺 {missing[:4]} 多 {extra[:4]} 重 {dup[:4]}")

    # 非硬切条目 ⇔ shot_list transition_in
    by_gid = {g.get("group_id"): transition_of(g) for g in (shot_list.get("generation_groups") or [])}
    mism = []
    # 集尾收束(2026-09-25):timeline 条目 ⇔ 生效值(shot_list 顶层 episode_close > 项目设置);类型/时长/停留/声音策略一致
    close = next((e for e in planned if _is_close(e)), None)
    close_eff = td.effective_episode_close(proj, ep, shot_list)
    if close_eff and not close:
        mism.append(f"episode_close:生效 {close_eff['type']} 未进 timeline(重跑 plan+render)")
    elif close and not close_eff:
        mism.append(f"episode_close:timeline 有 {close.get('type')} 而现已设为不处理(重跑 plan+render)")
    elif close and close_eff:
        if close.get("type") != close_eff["type"] or abs(float(close.get("duration_s") or 0) - float(close_eff.get("duration_s") or 0)) > 1e-6 \
                or abs(float(close.get("hold_s") or 0) - float(close_eff.get("hold_s") or 0)) > 1e-6 \
                or (float(close_eff.get("hold_s") or 0) > 0 and str(close.get("hold_audio") or "fade") != str(close_eff.get("hold_audio") or "fade")):
            mism.append(f"episode_close:timeline {close.get('type')} {close.get('duration_s')}/{close.get('hold_s')}/{close.get('hold_audio')}"
                        f"≠生效 {close_eff['type']} {close_eff.get('duration_s')}/{close_eff.get('hold_s')}/{close_eff.get('hold_audio')}(重跑 plan+render)")
    close_d = (float(close.get("duration_s") or 0) if close.get("type") not in CUT_COLOR else 0.0) if close else 0.0
    for e in planned:
        if e.get("type") in (None, "hard_cut") or _is_close(e):
            continue
        t = by_gid.get(e.get("to_group"))
        if not t or t["type"] != e.get("type"):
            mism.append(f"{e.get('at_shot')}:{e.get('type')}≠shot_list {t['type'] if t else None}")
        elif e.get("type") in TRANSITION_RENDERABLE and abs(float(e.get("duration_s") or 0) - float(t.get("duration_s") or 0)) > 1e-6:
            mism.append(f"{e.get('at_shot')}:duration {e.get('duration_s')}≠{t.get('duration_s')}")
        pf, ph, pa = pad_of(t)
        if abs(float(e.get("freeze_s") or 0) - pf) > 1e-6 or abs(float(e.get("hold_s") or 0) - ph) > 1e-6 \
                or (ph > 0 and (e.get("hold_audio") or "sustain") != pa):
            mism.append(f"{e.get('at_shot')}:垫片 freeze/hold/audio {e.get('freeze_s')}/{e.get('hold_s')}/{e.get('hold_audio')}"
                        f"≠shot_list {pf}/{ph}/{pa}")
        if t and (e.get("inserts") or e.get("overlay_card") or inserts_of(t) or t.get("overlay_card") or e.get("join") or t.get("join")):
            if e.get("fingerprint") != td.source_fingerprint(proj, t):
                mism.append(f"{e.get('at_shot')}:插入段/叠字/接缝风格设计指纹与 shot_list 不一致(重跑 plan+build+render)")
    for e in planned:
        if e.get("type") in (None, "hard_cut") and _has_extra(e):
            t = by_gid.get(e.get("to_group"))
            if t and e.get("fingerprint") != td.source_fingerprint(proj, t):
                mism.append(f"{e.get('at_shot')}:硬切+插入段设计指纹与 shot_list 不一致")
    planned_to = {e.get("to_group") for e in planned if e.get("type") not in (None, "hard_cut") or _has_extra(e)}
    for gid, t in by_gid.items():
        if (t["type"] != "hard_cut" or sum(pad_of(t)[:2]) > 0 or inserts_of(t) or t.get("overlay_card")) and gid not in planned_to:
            mism.append(f"{gid}:shot_list {t['type']} 未进 timeline")
    rec("transitions_match_shot_list", not mism, f"非硬切 {len(planned_to)} 处" + (f";不一致 {mism[:5]}" if mism else ""))

    renderable = [e for e in planned if e.get("type") in TRANSITION_RENDERABLE or _has_extra(e) or _is_close(e)]
    pads = [e for e in planned if _extra_s(e) > 0 and e.get("from_group") is not None and not _is_close(e)]
    ins_entries = [e for e in planned if e.get("inserts") and e.get("from_group") is not None]
    # 插入段预算(项目「过场模式」;极简 0%):Σinserts ≤ 集预算 × pct
    try:
        eff = td.effective(proj, ep)
        budget_s = float(shot_list.get("budget_s") or shot_list.get("total_duration_s") or 0)
        cap = budget_s * float(eff["insert_budget_pct"]) / 100.0
        tot_ins = sum(_ins_s(e) for e in ins_entries)
        if ins_entries or cap:
            rec("insert_budget_ok", tot_ins <= cap + 1e-6, f"Σ插入段 {tot_ins:.2f}s / 上限 {cap:.2f}s(过场模式 {eff['mode']} {eff['insert_budget_pct']:g}% × 集预算 {budget_s:g}s)")
    except Exception as ex:  # noqa: BLE001
        rec("insert_budget_ok", True, f"预算读取失败,跳过:{str(ex)[-120:]}", warn=True)
    if not renderable:
        rec("duration_unchanged", True, "无可渲染转场,cut_v1 即定稿,无 cut_v2 需核")
        ok = fails == 0
        if write:
            write_ledger(proj, ep, src_path, None, planned, timeline.get("transitions_policy") or {}, results)
        return ok, results
    if not (src_path.is_file() and out_path.is_file()):
        rec("duration_unchanged", False, f"缺 {src_path.name} 或 {out_path.name}(先跑 render)")
        return False, results

    src_i, out_i = _probe(src_path), _probe(out_path)
    fps = _fps(src_i["video"])
    tol = 1.0 / fps + 0.005
    sd, od = probe_duration(src_path), probe_duration(out_path)
    # 抽帧核对(边界先按组片段实际帧数重量化,与渲染同口径);垫片 → timemap(源基准 → 成片基准)
    requantize_cuts(planned, timeline, proj, fps)
    ops = pad_ops(planned, fps)
    m = timemap.map_fn(ops)
    pad_s = timemap.total_delta(ops)
    close_h = round(_close_frames(close, fps)[1] / fps, 6) if close else 0.0     # 尾停留按帧量化,不在 timemap 里
    if pads or close_h:
        rec("duration_as_planned", abs(sd + pad_s + close_h - od) <= tol,
            f"源 {_fmt(sd)} + 垫片/插入 {pad_s:.3f}s" + (f" + 尾停留 {close_h:.3f}s" if close_h else "")
            + f" = 期望 {_fmt(sd + pad_s + close_h)} / 转场后 {_fmt(od)}(容差 ±1 帧 = {tol:.3f}s;{len(pads)} 处)")
    # 插入段构建台账:指纹与 timeline 条目一致、段文件在
    ins_meta, ins_problems = [], []
    for e in ins_entries + [e for e in planned if e.get("overlay_card") and not e.get("inserts") and e.get("from_group") is not None]:
        d = _ins_dir(proj, ep, e)
        meta = _read_json(d / "meta.json") if (d / "meta.json").is_file() else {}
        if not meta:
            ins_problems.append(f"{e['at_shot']} 未构建(build)")
            continue
        if meta.get("fingerprint") != e.get("fingerprint"):
            ins_problems.append(f"{e['at_shot']} 构建指纹过期(重跑 build+render)")
        # 构建晚于成片(如换了字体后只 build 未 render):成片里仍是旧段,不得算已渲染
        try:
            built_ts = datetime.fromisoformat(str(meta.get("built_at") or "")).timestamp()
        except ValueError:
            built_ts = None
        if built_ts and out_path and out_path.exists() and built_ts > out_path.stat().st_mtime + 1:
            ins_problems.append(f"{e['at_shot']} 插入段/叠字构建晚于成片(重跑 render)")
        for k, r in enumerate(meta.get("inserts") or []):
            if r.get("missing"):
                ins_problems.append(f"{e['at_shot']} ins{k} 生成式 clip({r.get('kind')})缺失 {r.get('expected')}(p7-transition-clips 出片后重跑 build+render)")
            elif not Path(r.get("file", "")).is_file():
                ins_problems.append(f"{e['at_shot']} ins{k} 段文件缺失")
        ins_meta.append(meta)
    if ins_entries or any(e.get("overlay_card") for e in planned):
        rec("inserts_built", not ins_problems, f"{len(ins_meta)} 处插入段/叠字构建台账" + (f";异常 {ins_problems[:4]}" if ins_problems else ""))
    elif not (pads or close_h):
        rec("duration_unchanged", abs(sd - od) <= tol, f"源 {_fmt(sd)} / 转场后 {_fmt(od)}(容差 ±1 帧 = {tol:.3f}s)")
    if bool(src_i["audio"]) != bool(out_i["audio"]):
        rec("audio_stream_intact", False, f"声轨有无不一致:源 {bool(src_i['audio'])} / 转场后 {bool(out_i['audio'])}")
    elif src_i["audio"]:
        sa, oa = float(src_i["audio"].get("duration") or sd), float(out_i["audio"].get("duration") or od)
        if pads or close_h:
            rec("audio_stream_intact", abs(sa + pad_s + close_h - oa) <= 0.10,
                f"声轨时长 源 {_fmt(sa)} + 垫片 {pad_s:.3f}s" + (f" + 尾停留 {close_h:.3f}s" if close_h else "") + f" / 转场后 {_fmt(oa)}(重映射/补静音)")
        else:
            rec("audio_stream_intact", abs(sa - oa) <= 0.05, f"声轨时长 源 {_fmt(sa)} / 转场后 {_fmt(oa)}(流拷贝)")
    else:
        rec("audio_stream_intact", True, "源无声轨,转场后亦无")

    w, h = 160, 90
    problems, verified, whitelist = [], 0, []
    step = 1.0 / fps
    ins_verified, ins_issues = 0, []
    for e in planned:
        ty, t, d = e.get("type"), float(e.get("cut_time_s") or 0), float(e.get("duration_s") or 0)
        t = round(t * fps) / fps        # 量化到整帧:渲染按帧号切界,-ss 用未量化秒会错位 1 帧
        fz, hd = (x / fps for x in _pad_frames(e, fps))
        to = m(t)                        # 本组首帧在成片上的时刻(垫片 + 插入段之后)
        insf = _ins_frames(e, fps) if e.get("inserts") else []
        ins_total_s = sum(insf) / fps
        try:
            if _is_close(e):
                # 集尾收束:淡出类核淡出末帧(全黑时刻前 1 帧)近黑/近白,切黑类核末内容帧**不**近黑(确认是硬切而非提前变黑);停留中点同色;整段窗口进黑帧白名单
                dark = ty in ("fade_black", "cut_black")
                black_at = od - close_h                     # 画面全黑时刻 = 成片末 − 停留
                mu_last = _mean(_gray_frame(out_path, max(0.0, black_at - step), w, h))
                if ty in FADE_COLOR and ((mu_last > DARK_MAX) if dark else (mu_last < BRIGHT_MIN)):
                    problems.append(f"{e['at_shot']} {ty} 淡出末帧灰度均值 {mu_last:.0f}")
                if close_h:
                    mu = _mean(_gray_frame(out_path, od - close_h / 2, w, h))
                    if (mu > DARK_MAX) if dark else (mu < BRIGHT_MIN):
                        problems.append(f"{e['at_shot']} {ty} 停留中点灰度均值 {mu:.0f}")
                    if ty in CUT_COLOR:
                        # 源末内容帧按视频流帧数定位(容器时长常被更长的声轨撑大,-ss 到声轨末会抽不到帧)
                        ref = _gray_frame(src_path, _file_frames(src_path) / fps - step, w, h)
                        x = _gray_frame(out_path, black_at - step, w, h)
                        if not ref or not x:
                            problems.append(f"{e['at_shot']} {ty} 末内容帧取帧失败({'源片' if not ref else '成片'} t={_file_frames(src_path) / fps - step if not ref else black_at - step:.3f}s 抽不到帧)")
                        elif _mad(x, ref) > SAME_TOL + 4:
                            problems.append(f"{e['at_shot']} {ty} 末内容帧与源末帧差 {_mad(x, ref):.1f}(切黑前画面被改动)")
                whitelist.append({"at_shot": e["at_shot"], "type": ty, "start_s": round(max(0.0, black_at - d), 3), "end_s": round(od, 3)})
                verified += 1
                continue
            # 插入段:块内顺序 定格 → 黑场 → 插入段;段 k 中点帧 ≈ 段文件中点帧;字卡(黑底)中点不得近黑(证明文字在);黑底字卡窗口进白名单
            if insf and e.get("from_group") is not None:
                meta = _read_json(_ins_dir(proj, ep, e) / "meta.json") if (_ins_dir(proj, ep, e) / "meta.json").is_file() else {}
                block0 = to - ins_total_s
                off = 0.0
                for k, nf in enumerate(insf):
                    st = block0 + off
                    mid = st + nf / fps / 2
                    r = (meta.get("inserts") or [])[k] if k < len(meta.get("inserts") or []) else None
                    x = e["inserts"][k]
                    if r and Path(r.get("file", "")).is_file():
                        ref = _gray_frame(Path(r["file"]), nf / fps / 2, w, h)
                        got = _gray_frame(out_path, mid, w, h)
                        mad = _mad(got, ref)
                        if mad > SAME_TOL + 6:
                            ins_issues.append(f"{e['at_shot']} ins{k}({x.get('kind')}) 中点帧与段文件差 {mad:.1f}")
                        if x.get("kind") == "title_card":
                            bg = ((x.get("card") or {}).get("bg") or "black")
                            # 文字在:亮/暗像素占比(黑底看亮像素、白底看暗像素)≥ 0.2%(灰度均值对小面积文字不敏感)
                            frac = (sum(1 for v in got if v > 48) if bg != "white" else sum(1 for v in got if v < 200)) / max(1, len(got))
                            if frac < 0.002:
                                ins_issues.append(f"{e['at_shot']} ins{k} 字卡中点文字像素占比 {frac * 100:.2f}%(文字未渲出?)")
                            if bg in ("black", "blur_prev"):
                                whitelist.append({"at_shot": e["at_shot"], "type": "title_card", "start_s": round(st, 3), "end_s": round(st + nf / fps, 3)})
                        if x.get("kind") == "bridge":
                            a0 = _gray_frame(Path(r["file"]), 0.0, w, h)
                            b1 = _gray_frame(Path(r["file"]), max(0.0, nf / fps - step), w, h)
                            sa, sb = _gray_frame(src_path, t - step, w, h), _gray_frame(src_path, t, w, h)
                            m0, m1 = _mad(a0, sa), _mad(b1, sb)
                            if not (a0 and b1 and sa and sb):
                                ins_issues.append(f"{e['at_shot']} 桥接首/末帧或前组尾/本组首取帧失败")
                            elif m0 > 40 or m1 > 40:
                                ins_issues.append(f"{e['at_shot']} 桥接首/末帧与前组尾/本组首差 {m0:.0f}/{m1:.0f}(模型未贴合首尾帧)")
                        ins_verified += 1
                    else:
                        ins_issues.append(f"{e['at_shot']} ins{k} 无段文件可比对")
                    off += nf / fps
            # 叠字幕:本组首 F 帧内画面应与源帧有差(文字在),F 帧之后恢复与源一致
            if e.get("overlay_card") and e.get("from_group") is not None:
                od_s = float(e["overlay_card"].get("duration_s") or 2.5)
                x1 = _gray_frame(out_path, to + od_s / 2, w, h)
                s1 = _gray_frame(src_path, t + od_s / 2, w, h)
                if _mad(x1, s1) < 0.4:
                    ins_issues.append(f"{e['at_shot']} 叠字幕窗口中点与源帧无差(文字未叠上?)")
                x2 = _gray_frame(out_path, to + od_s + 0.5, w, h)
                s2 = _gray_frame(src_path, t + od_s + 0.5, w, h)
                if _mad(x2, s2) > SAME_TOL + 2:
                    ins_issues.append(f"{e['at_shot']} 叠字幕结束后 0.5s 画面与源差 {_mad(x2, s2):.1f}")
                ins_verified += 1
            if (fz or hd) and e.get("from_group") is not None:
                # 垫片:黑场中点近黑;定格帧 ≈ 前组末帧(源 t−1 帧);dip/fade 类前组尾淡出末帧近黑
                if hd:
                    hb = to - ins_total_s          # 黑场块末 = 插入段块首
                    mu = _mean(_gray_frame(out_path, hb - hd / 2, w, h))
                    if mu > DARK_MAX:
                        problems.append(f"{e['at_shot']} 黑场垫片中点灰度均值 {mu:.0f}")
                    whitelist.append({"at_shot": e["at_shot"], "type": f"{ty}+hold", "start_s": round(hb - hd, 3), "end_s": round(hb, 3)})
                if fz:
                    ref = _gray_frame(src_path, t - step, w, h)
                    x = _gray_frame(out_path, to - ins_total_s - hd - fz / 2, w, h)
                    mad = _mad(x, ref)
                    if ty in ("dip_black", "dip_white") or ty in FADE_COLOR:
                        pass                      # 定格段叠着淡出,不与源帧比对
                    elif mad > SAME_TOL + 4:
                        problems.append(f"{e['at_shot']} 定格帧与前组末帧差 {mad:.1f}")
                if ty in ("dip_black", "fade_black"):
                    hb = to - ins_total_s
                    mu = _mean(_gray_frame(out_path, hb - hd - step, w, h))
                    if mu > DARK_MAX:
                        problems.append(f"{e['at_shot']} {ty}+垫片 前组尾淡出末帧灰度均值 {mu:.0f}")
                    dd = d / 2 if ty == "dip_black" else d
                    whitelist.append({"at_shot": e["at_shot"], "type": ty, "start_s": round(hb - hd - dd, 3), "end_s": round(hb - hd, 3)})
                    if ty == "dip_black" and not insf:
                        whitelist.append({"at_shot": e["at_shot"], "type": ty, "start_s": round(to, 3), "end_s": round(to + d / 2, 3)})
                verified += 1
            elif insf and e.get("from_group") is not None:
                # 只有插入段(接缝为硬切 / 叠化 / 淡出)的边界:接缝落在段链内,各段中点已核;前组尾淡出到黑/白时核末帧
                if ty in ("fade_black", "fade_white"):
                    hb = to - ins_total_s
                    mu = _mean(_gray_frame(out_path, hb - step, w, h))
                    ok = mu <= DARK_MAX if ty == "fade_black" else mu >= BRIGHT_MIN
                    if not ok:
                        problems.append(f"{e['at_shot']} {ty}→插入段 前组尾端帧灰度均值 {mu:.0f}")
                    whitelist.append({"at_shot": e["at_shot"], "type": ty, "start_s": round(hb - d, 3), "end_s": round(hb, 3)})
                verified += 1
            elif ty == "dissolve" and xfade_name(e) != "fade":
                # 风格接缝(wipe / hblur / zoomin / iris / pixelize / fadegrays):中点既不是前帧也不是后帧、且非纯黑
                margin = round(_frames(d, fps) / 2) / fps + 2 * step
                a = _gray_frame(src_path, t - margin, w, h)
                b = _gray_frame(src_path, t + margin, w, h)
                x = _gray_frame(out_path, to, w, h)
                if _mad(x, a) < 2.0 and _mad(x, b) < 2.0:
                    problems.append(f"{e['at_shot']} 风格接缝 {xfade_name(e)} 中点与两侧都无差(未生效?)")
                if _mean(x) <= DARK_MAX and _mean(a) > DARK_MAX and _mean(b) > DARK_MAX:
                    problems.append(f"{e['at_shot']} 风格接缝 {xfade_name(e)} 中点近黑")
                verified += 1
            elif ty == "dissolve":
                # 参考帧取**叠化窗口之外**两侧(±(d/2+2 帧)):纯前组帧与纯后组帧的
                # 50/50 合成对比窗口中点。窗口内帧是克隆冻结的,窗口外 2 帧余量还
                # 容忍名义边界与实拼内容 1–2 帧的历史偏差(timeline 浮点截断遗留)
                # (2026-09-03 首次实跑校准)
                margin = round(_frames(d, fps) / 2) / fps + 2 * step
                a = _gray_frame(src_path, t - margin, w, h)
                b = _gray_frame(src_path, t + margin, w, h)
                x = _gray_frame(out_path, to, w, h)
                mm = _blend_mad(x, a, b)
                # 阈值随前后组画面差自适应:参考帧在窗口外 ±2 帧,运动内容下与窗口内
                # 冻结克隆帧的差 ∝ 前后组差;静止组 _mad(a,b)≈0 仍按 BLEND_TOL 严卡
                tol = max(BLEND_TOL, 0.45 * _mad(a, b))
                if mm > tol:
                    problems.append(f"{e['at_shot']} dissolve 中点与前后帧 50/50 混合差 {mm:.1f}>{tol:.1f}")
                verified += 1
            elif ty in ("dip_black", "dip_white"):
                # ffmpeg fadeblack/fadewhite 的纯黑/纯白峰值不在窗口正中(实测约 1/3 处),窗口内取三点极值
                mus = [_mean(_gray_frame(out_path, to + k * d / 4, w, h)) for k in (-1, 0, 1)]
                mu = min(mus) if ty == "dip_black" else max(mus)
                ok = mu <= DARK_MAX if ty == "dip_black" else mu >= BRIGHT_MIN
                if not ok:
                    problems.append(f"{e['at_shot']} {ty} 窗口内灰度极值 {mu:.0f}(三点 {[round(m) for m in mus]})")
                whitelist.append({"at_shot": e["at_shot"], "type": ty, "start_s": round(to - d / 2, 3), "end_s": round(to + d / 2, 3)})
                verified += 1
            elif ty in FADE_COLOR:
                if e.get("from_group") is None:
                    x = _gray_frame(out_path, 0.0, w, h)
                    whitelist.append({"at_shot": e["at_shot"], "type": ty, "start_s": 0.0, "end_s": round(d, 3)})
                else:
                    x = _gray_frame(out_path, to - step, w, h)
                    whitelist.append({"at_shot": e["at_shot"], "type": ty, "start_s": round(to - d, 3), "end_s": round(to, 3)})
                mu = _mean(x)
                ok = mu <= DARK_MAX if ty == "fade_black" else mu >= BRIGHT_MIN
                if not ok:
                    problems.append(f"{e['at_shot']} {ty} 端帧灰度均值 {mu:.0f}")
                verified += 1
        except RuntimeError as ex:
            problems.append(f"{e.get('at_shot')} 抽帧失败:{str(ex)[-160:]}")
    # 硬切边界两侧 + 非转场区不得漂移:抽最多 4 个硬切边界(均匀取)与片尾前 0.5s
    hard = [e for e in planned if (e.get("type") in (None, "hard_cut") or e.get("renders_as") == "hard_cut") and not _has_extra(e)]
    pick = hard[:: max(1, len(hard) // 4)][:4] if hard else []
    samples = [(f"{e['at_shot']} +0.25s", float(e["cut_time_s"]) + 0.25) for e in pick]
    samples.append(("片尾 −0.5s" + ("(淡出前)" if close else ""), max(0.0, sd - close_d - 0.5)))
    drift = []
    for label, t in samples:
        try:
            mm = _mad(_gray_frame(out_path, m(t), w, h), _gray_frame(src_path, t, w, h))
            if mm > SAME_TOL:
                drift.append(f"{label} 差 {mm:.1f}")
        except RuntimeError as ex:
            drift.append(f"{label} 抽帧失败:{str(ex)[-120:]}")
    rec("transition_frames_verified", not problems, f"核 {verified}/{len(renderable)} 处"
        + (f";异常 {problems[:4]}" if problems else ""))
    if ins_verified or ins_issues:
        rec("insert_frames_verified", not ins_issues, f"核插入段/叠字 {ins_verified} 项" + (f";异常 {ins_issues[:4]}" if ins_issues else ""))
    rec("hard_cut_positions_intact", not drift, f"抽样 {len(samples)} 点与源帧比对" + ("(按 timemap 对位)" if pads else "")
        + (f";漂移 {drift[:4]}" if drift else ""))

    ok = fails == 0
    if write:
        write_ledger(proj, ep, src_path, out_path, planned, timeline.get("transitions_policy") or {}, results, whitelist, ops, inserts=ins_meta,
                     out_dur=od)
    print(f"[{'PASS' if ok else 'FAIL'} ] transition_render_ok:{len(results)} 项,FAIL {fails}")
    return ok, results


def write_ledger(proj, ep, src_path, out_path, entries, policy, checks, whitelist=None, ops=None, inserts=None, out_dur=None):
    ed = proj / "edit" / ep
    ed.mkdir(parents=True, exist_ok=True)
    ops = timemap.normalize_ops(ops or [])
    close = next((e for e in (entries or []) if _is_close(e)), None)
    close_rec = None
    if close:
        # 集尾收束台账(finalize_episode.py 读):fade_end_s = 成片(out_cut)上画面全黑时刻,外挂声轨在其前 duration_s 内淡出(切黑类 = 硬切)、其后静音
        if out_dur is None and out_path and Path(out_path).is_file():
            out_dur = probe_duration(out_path)
        hold = float(close.get("hold_s") or 0.0)
        close_rec = {k: close.get(k) for k in ("type", "duration_s", "hold_s", "hold_audio", "source", "reason", "at_shot", "fingerprint")}
        close_rec["hold_s"] = hold
        close_rec["end_s"] = round(float(out_dur), 6) if out_dur else None
        close_rec["fade_end_s"] = round(float(out_dur) - hold, 6) if out_dur else None
    data = {
        "file": f"edit/{ep}/{LEDGER}", "episode": ep,
        "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cli": "code/render_transitions.py", "policy": policy,
        "src_cut": str(src_path.relative_to(proj)) if src_path and src_path.exists() else None,
        "out_cut": str(out_path.relative_to(proj)) if out_path and out_path.exists() else None,
        "transitions": entries,
        "black_frame_whitelist": whitelist or [],
        # 节奏垫片(2026-09-17):成片相对 src_cut 的时长编辑表,finalize_episode.py 据此平移外挂声轨/字幕(§9B)
        "pads": [e for e in entries if _extra_s(e) > 0 and e.get("from_group") is not None and not _is_close(e)],
        # 集尾收束(2026-09-25):不进 timemap(尾部停留不平移任何时刻);finalize_episode.py 按 fade_end_s 淡出/切断外挂声轨并补静音
        "episode_close": close_rec,
        # 过场设计(2026-09-24):插入段 / 叠字幕构建台账(edit/epNN/transitions/<B-id>/meta.json 汇总,页面据此显示已构建/已渲染)
        "inserts": inserts or [],
        "timemap": {"basis": str(src_path.relative_to(proj)) if src_path and src_path.exists() else None,
                    "ops": ops, "delta_s": timemap.total_delta(ops)},
        "check": {"name": "transition_render_ok",
                  "result": "PASS" if checks is not None and all(r["result"] != "FAIL" for r in checks) else ("n/a" if checks is None else "FAIL"),
                  "items": checks or []},
    }
    p = ed / LEDGER
    _write_json(p, data)
    print(f"[DONE ] 台账 {p.relative_to(proj)}")
    return p


# ---------------------------------------------------------------- main

def main(argv=None):
    def configure(ap):
        ap.add_argument("cmd", choices=("plan", "build", "preview", "render", "check"))
        ap.add_argument("--src", default=DEFAULT_SRC, help=f"源粗成片(edit 产物),默认 {DEFAULT_SRC}")
        ap.add_argument("--out", default=DEFAULT_OUT, help=f"转场后成片,默认 {DEFAULT_OUT}")
        ap.add_argument("--dry-run", action="store_true", help="plan:只打印,不写回 timeline.json")
        ap.add_argument("--crf", type=int, default=18)
        ap.add_argument("--preset", default="medium")
        ap.add_argument("--force", action="store_true", help="build/preview:忽略指纹强制重建")
        ap.add_argument("--boundary", action="append", default=[], help="build/preview:只处理该边界(B-grpA-grpB;episode_close = 集尾收束),可重复")
        ap.add_argument("--to", action="append", default=[], help="build/preview:只处理进入该组的边界(grpNNN),可重复")

    args, proj = parse_args(__doc__.splitlines()[0], configure=configure, argv=argv)
    ed, _, _, src_p, out_p = _ep_paths(proj, args.ep, args.src, args.out)
    targets = set(args.boundary) | set(args.to)
    if args.cmd == "build":
        built = do_build(proj, args.ep, src_p if src_p.is_file() else None, force=args.force, only=targets or None)
        missing = [(b, r.get("expected")) for b, m in built.items() for r in (m.get("inserts") or []) if r.get("missing")]
        print(f"[DONE ] 插入段台账 {len(built)} 处" + (f";桥接 clip 缺失 {missing}" if missing else ""))
        return 0 if not missing else 1
    if args.cmd == "preview":
        outs = do_preview(proj, args.ep, src_p if src_p.is_file() else None, targets, force=args.force)
        print(f"[DONE ] 预览 {len(outs)} 处")
        return 0
    if args.cmd == "plan":
        _, _, ok = do_plan(proj, args.ep, src_p if src_p.is_file() else None, dry_run=args.dry_run)
        return 0 if ok else 1
    if args.cmd == "render":
        out = do_render(proj, args.ep, src_p, out_p, crf=args.crf, preset=args.preset)
        ok, _ = do_check(proj, args.ep, src_p, out_p if out else out_p)
        return 0 if ok else 1
    ok, _ = do_check(proj, args.ep, src_p, out_p)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
