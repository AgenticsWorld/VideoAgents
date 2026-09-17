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
           **节奏垫片(2026-09-17,§9C)**:transition_in 带 hold_s(本组前黑场停留)/ freeze_s(前组尾帧定格)
           时在组边界**插入**帧——前组尾(可定格 freeze_s)→ 黑场 hold_s → 本组首;dip_black 配 hold 时前组尾
           淡出半个 duration 到黑、本组首自黑淡入半个 duration,fade_black 配 hold 时前组尾淡出整段、本组硬入,
           hard_cut 配 hold 即切黑停留再硬入。成片按 Σ垫片变长,同时把这张「时长编辑表」写进台账
           (timemap.ops,源 cut 基准),源 cut 若带声轨则按表重映射(黑场声音按 hold_audio:sustain 延续前段
           /fade 淡出/mute 静音);外挂声轨与字幕由 finalize_episode.py 读同一张表平移(§9B)。
  check    机检 transition_render_ok(封装前必跑,FAIL 即不交付):
             transitions_planned          timeline.transitions 覆盖全部组边界,每边界恰一条
             transitions_match_shot_list  非硬切条目与 shot_list transition_in 一一对应(类型/时长/垫片);
                                          timeline 里多出的非硬切 = Agent 自创,FAIL
             duration_unchanged           cut_v2 时长 = cut_v1 ±1 帧(无垫片时)
             duration_as_planned          cut_v2 时长 = cut_v1 + Σ垫片 ±1 帧(有垫片时)
             audio_stream_intact          有/无声轨与源一致,声轨时长一致(流拷贝;有垫片时 = 源 + Σ垫片)
             transition_frames_verified   逐处抽帧:叠化中点 ≈ 前后帧 50/50 混合;dip 中点近黑/近白;
                                          fade 尾帧近黑/近白;黑场垫片中点近黑、定格帧 = 前组末帧;
                                          硬切边界两侧帧与源一致(无时间漂移,有垫片时按 timemap 对位)
           台账 edit/epNN/transitions_render.json(含 dip/fade/黑场垫片白名单窗口,供 no_black_frames 豁免;
           pads[] 与 timemap.ops 供 finalize_episode.py 平移外挂声轨/字幕)

转场类型(与 code/check_generation_groups.py 的 transition_ok 同源):
  可渲染  dissolve(xfade=fade)· dip_black / dip_white(xfade=fadeblack/fadewhite)·
          fade_black / fade_white(前组尾淡出到黑/白,本组硬入;首组 = 从黑/白淡入)
  标注型  smash_cut / match_cut(不渲染 = 硬切,只供 continuity / QA 核对构图对位)

用法:
  python3 code/render_transitions.py plan   --project <slug> --ep epNN [--dry-run]
  python3 code/render_transitions.py render --project <slug> --ep epNN [--src cut_v1.mp4] [--out cut_v2.mp4]
  python3 code/render_transitions.py check  --project <slug> --ep epNN [--src cut_v1.mp4] [--out cut_v2.mp4]
退出码:全 PASS=0,任一 FAIL=1。
"""
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from _common import parse_args  # 副作用:modules/ 入 sys.path
from avsync import probe_duration, require_tools
from footage import count_frames as count_frames_of

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_generation_groups import (TRANSITION_ANNOTATION, TRANSITION_RENDERABLE,  # noqa: E402
                                     transition_of, pad_of)
import timemap  # noqa: E402  modules/(_common 副作用入 sys.path)

LEDGER = "transitions_render.json"
DEFAULT_SRC = "cut_v1.mp4"
DEFAULT_OUT = "cut_v2.mp4"
XFADE_OF = {"dissolve": "fade", "dip_black": "fadeblack", "dip_white": "fadewhite"}
FADE_COLOR = {"fade_black": "black", "fade_white": "white"}
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


def requantize_cuts(entries, timeline, proj, fps):
    """把非硬切条目的 cut_time_s 重量化为**各组片段实际帧数的累计**(物理口径)。

    timeline 的 in/out 常见毫秒截断(3.545),几十组浮点累加后可偏过半帧线,
    round(累计秒×fps) 会与 concat 实拼的帧边界差 1 帧——渲染窗口与像素核验双双
    错位(2026-09-03 leijun2 实测)。concat 拼的就是组片段文件,按文件帧数累计
    即与成片逐帧一致。就地更新 entries,返回重量化条数。"""
    vids = ((timeline.get("tracks") or {}).get("video")) or timeline.get("video") or []
    if not vids:
        return 0
    cum, walk = 0, []                   # walk: 有序边界表 [(from,to,t), ...]
    prev = None
    for i, e in enumerate(vids):
        gid = _entry_gid(e, i)
        if prev is not None and gid != prev:
            walk.append([prev, gid, cum / fps])
        src = e.get("src")
        if not src:
            return 0                    # 条目缺 src,无从按帧累计,保持原值
        # 防御:条目做了子区间修剪或变速时,文件帧数 ≠ 成片占帧,帧准口径不成立,整体放弃
        if e.get("timeline_in_s") is not None or e.get("timeline_in") is not None                 or (e.get("speed") or 1.0) != 1.0:
            return 0
        frames = count_frames_of(str(proj / src))
        dur = _entry_dur(e, proj)
        if abs(frames / fps - dur) > 1.5 / fps:   # in/out 只取了文件一段 → 同样放弃
            return 0
        cum += frames
        prev = gid
    # 有序消费匹配:同一 (from,to) 邻接重复出现时按边界顺序一一对应,不坍缩到最后一处
    n = 0
    for e in entries:
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
        if ty != "hard_cut" or hold_s or freeze_s:
            ent["intent"] = t.get("intent")
            ent["reason"] = t.get("reason")
            ent["source"] = "shot_list.transition_in"
        else:
            ent["reason"] = ent.get("reason") or "default hard cut"
        entries.append(ent)

    # shot_list 有非硬切设计、timeline 却没有对应边界(组缺席 / 不在边界)= 设计落不了地
    planned_to = {e["to_group"] for e in entries if e["type"] != "hard_cut" or _pad_s(e)}
    for g in groups:
        t = transition_of(g)
        if (t["type"] != "hard_cut" or sum(pad_of(t)[:2]) > 0) and g.get("group_id") not in planned_to:
            problems.append(f"{g.get('group_id')} transition_in={t['type']} 在 timeline video 轨上没有对应的组入口边界"
                            f"(组缺席或不在边界)")
    renderable = [e for e in entries if e["type"] in TRANSITION_RENDERABLE]
    pads = [e for e in entries if _pad_s(e)]
    policy = {
        "cli": "code/render_transitions.py", "compensation": "pad" + ("+insert" if pads else ""),
        "source": "shot_list.generation_groups[].transition_in",
        "planned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "src_cut": str(src_path.name) if src_path else None,
        "boundaries": len(bounds), "renderable": len(renderable),
        "renderable_total_s": round(sum(e["duration_s"] for e in renderable), 3),
        "pads": len(pads), "pad_total_s": round(sum(_pad_s(e) for e in pads), 3),
        "timeline_total_s": round(total, 3),
    }
    return entries, policy, problems


def _pad_s(e):
    """条目的垫片总秒数(定格 + 黑场)。"""
    return float(e.get("freeze_s") or 0.0) + float(e.get("hold_s") or 0.0)


def _pad_frames(e, fps):
    """(定格帧数, 黑场帧数),按帧量化。"""
    return _frames(e.get("freeze_s") or 0.0, fps), _frames(e.get("hold_s") or 0.0, fps)


def pad_ops(entries, fps):
    """垫片 → timemap ops(源 cut 基准;插入点 = 组边界源时刻,块 = 定格 + 黑场)。"""
    ops = []
    for e in entries:
        fz, hd = _pad_frames(e, fps)
        if not (fz or hd) or e.get("from_group") is None:
            continue
        t = _frames(e.get("cut_time_s") or 0.0, fps) / fps
        ops.append({"src_t0": round(t, 6), "src_t1": round(t, 6), "out_len": round((fz + hd) / fps, 6),
                    "freeze_s": round(fz / fps, 6), "hold_s": round(hd / fps, 6),
                    "audio": e.get("hold_audio") or "sustain", "kind": "boundary_pad",
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
        if e["type"] != "hard_cut" or _pad_s(e):
            pad = (f"  定格 {e['freeze_s']}s" if e.get("freeze_s") else "") + (f"  黑场 {e['hold_s']}s/{e.get('hold_audio')}" if e.get("hold_s") else "")
            print(f"         {e['at_shot']:<18} @{_fmt(e['cut_time_s'])}  {e['type']}"
                  f"{'' if 'duration_s' not in e else ' ' + str(e['duration_s']) + 's'}{pad}  {e.get('intent') or ''}")
    if policy.get("pads"):
        print(f"[PLAN ] 节奏垫片 {policy['pads']} 处,成片将变长 +{policy['pad_total_s']}s(声轨/字幕按 timemap 重映射)")
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
    xf = sorted((e for e in entries if e["type"] in XFADE_OF), key=lambda e: e["cut_time_s"])
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
        parts.append(f"{prev}[s{i + 1}]xfade=transition={XFADE_OF[e['type']]}:duration={dur_f[i] / fps:.6f}"
                     f":offset={off:.6f},setpts=N/({fps:g}*TB){lab}")
        prev = lab
    return ";".join(parts), len(xf)


def build_run_filter(chunks, bounds, fps, w, h, open_fade=None):
    """渲染段(run)的通用滤镜图:chunks = 各段帧数(段 = 相邻无边界组合并),bounds[i] = 段 i|i+1 之间的条目
    (xfade 类 / 垫片类 / fade 类);open_fade = 集首淡入条目(仅 run 从位置 0 起时)。
    xfade 边界:两侧 tpad 克隆半长再 xfade(pad 补偿,长度不变);
    垫片 / fade 边界:前段尾 [定格 freeze 帧] → [黑场 hold 帧] → 后段首,dip 类前段尾淡出 d/2、后段首淡入 d/2,
    fade 类前段尾淡出 d、后段硬入;一律按帧量化,返回 (filter, 期望输出帧数)。"""
    k = len(chunks)
    def is_xf(e):
        # 叠化/穿黑穿白仍走 xfade;但带黑场停留(hold)的 dip 走垫片路径(前段淡出 → 黑场 → 后段淡入);
        # 只带定格(freeze)的 xfade 类:前段尾先克隆定格帧,再照常 xfade
        return e is not None and e.get("type") in XFADE_OF and _pad_frames(e, fps)[1] == 0
    def half(e):
        df = max(2, 2 * round(_frames(e.get("duration_s") or 0, fps) / 2))
        return df // 2, df - df // 2, df
    labels = "".join(f"[p{i}]" for i in range(k))
    parts = [f"[0:v]scale=in_range=auto:out_range=tv,format=yuv420p,split={k}{labels}"]
    starts = [sum(chunks[:i]) for i in range(k)]
    seg_len = []
    for i in range(k):
        L, R = bounds.get(i - 1), bounds.get(i)
        # 统一时基(settb=AVTB):color 黑场源默认 1/1000000,与解码段 1/12288 不同,xfade 拒绝不同时基的输入
        f = f"[p{i}]trim=start_frame={starts[i]}:end_frame={starts[i] + chunks[i]},setpts=PTS-STARTPTS,settb=AVTB"
        length = chunks[i]
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
        parts.append(f + f",setpts=N/({fps:g}*TB)[s{i}]")
        seg_len.append(length)
    cur, cur_len = "[s0]", seg_len[0]
    for i in range(k - 1):
        e = bounds.get(i)
        lab = "[vout]" if i == k - 2 else f"[x{i}]"
        if e is None:
            parts.append(f"{cur}[s{i + 1}]concat=n=2:v=1:a=0,setpts=N/({fps:g}*TB){lab}")
            cur_len += seg_len[i + 1]
        elif is_xf(e):
            _, _, df = half(e)
            # 前段已含 half_b 的尾克隆:窗口 = 前段末 df 帧,offset = 前段长 − df(与 build_filter 的 cut − half_a 同值)
            parts.append(f"{cur}[s{i + 1}]xfade=transition={XFADE_OF[e['type']]}:duration={df / fps:.6f}"
                         f":offset={(cur_len - df) / fps:.6f},setpts=N/({fps:g}*TB){lab}")
            cur_len += seg_len[i + 1] - df
        else:
            _, hd = _pad_frames(e, fps)
            if hd:
                parts.append(f"color=c=black:s={w}x{h}:r={fps:g}:d={hd / fps:.6f},format=yuv420p,setsar=1,settb=AVTB,"
                             f"trim=end_frame={hd},setpts=N/({fps:g}*TB)[blk{i}]")
                parts.append(f"{cur}[blk{i}][s{i + 1}]concat=n=3:v=1:a=0,setpts=N/({fps:g}*TB){lab}")
            else:
                parts.append(f"{cur}[s{i + 1}]concat=n=2:v=1:a=0,setpts=N/({fps:g}*TB){lab}")
            cur_len += hd + seg_len[i + 1]
        cur = lab
    if k == 1:
        parts[-1] = parts[-1].replace("[s0]", "[vout]")
    return ";".join(parts), cur_len


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
    pads = [e for e in entries if sum(_pad_frames(e, fps)) > 0 and e.get("from_group") is not None]
    if not xf_entries and not n_fade and not pads:
        print("[INFO ] 无可渲染转场:全片硬切,不产出 cut_v2(cut_v1 即转场定稿)")
        write_ledger(proj, ep, src_path, None, entries, policy, checks=None)
        return None
    if n_fade and not pads:
        # fade 类须对整片施加,退回整片路径(现役场景只有 dissolve,此路径极少走)
        return _render_wholefile(proj, ep, src_path, out_path, entries, src_dur, fps,
                                 crf, preset, policy)
    return _render_segmented(proj, ep, src_path, out_path, entries, tl, fps, crf, preset, src_dur, info, bool(pads))


def _render_segmented(proj, ep, src_path, out_path, entries, tl, fps, crf, preset, src_dur, info, with_pads):
    """★分段式渲染(v4.2,清晰度改造;2026-09-17 扩展垫片/fade 边界):成片 = 各组片段 concat 而来,组边界必是
    关键帧——**未涉边界效果的组直接引用原组片段文件流拷贝(零再编码)**,只把叠化 / 垫片 / 淡出边界两侧
    的组合并重编码。相比整片重编码(两遍式=全片 3 代),成片除边界段外保持 1 代编码;且每个渲染段输入
    参数恒定,天然规避 ffmpeg 8 的 filtergraph reinit 帧计数清零坑。有垫片时成片按 Σ垫片变长,源声轨按
    timemap 重映射(黑场声音按 hold_audio)。"""
    xf_entries = [e for e in entries if e["type"] in XFADE_OF]
    vids = ((tl.get("tracks") or {}).get("video")) or tl.get("video") or []
    order, files, gframes = [], [], []          # 按**位置**索引:同组 id 重复出现也不坍缩
    for i, v in enumerate(vids):
        gid = _entry_gid(v, i)
        src = v.get("src")
        if not src:
            raise SystemExit(f"[FAIL] timeline video 条目 {i}({gid})缺 src,分段渲染无从引用组片段")
        order.append(gid)
        files.append(proj / src)
        gframes.append(count_frames_of(str(proj / src)))
    # 热边界 = 叠化 + 垫片 +(有垫片时)fade 类;按相邻位置对有序消费匹配(重复邻接不坍缩)
    def hot(e):
        if e.get("from_group") is None:
            return False
        return e["type"] in XFADE_OF or sum(_pad_frames(e, fps)) > 0 or (with_pads and e["type"] in FADE_COLOR)
    remaining = [e for e in entries if hot(e)]
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
        lst = tmp_dir / f".xfrun{r}.txt"
        lst.write_text("".join(f"file '{files[i].resolve()}'\n" for i in run))
        run_src = tmp_dir / f".xfrun{r}.src.mp4"
        # run 源直接归一重编码(不 -c copy):组片段间哪怕只有 color_range 标签之差,
        # concat 后进 filter 也会触发 ffmpeg 8 的 filtergraph reinit 清零 trim 计数
        # (实测 143 帧渲染段截成 97);run 仅数秒,重编码代价近零且反正要过 xfade
        _run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
              "-i", str(lst),
              "-vf", "scale=in_range=auto:out_range=tv,format=yuv420p,setsar=1",
              "-fps_mode", "passthrough", "-frames:v", str(run_frames),
              "-c:v", "libx264", "-crf", "14", "-preset", "fast",
              "-pix_fmt", "yuv420p", "-color_range", "tv", "-an",
              str(run_src)], timeout=1200)
        if count_frames_of(str(run_src)) != run_frames:
            raise SystemExit(f"[FAIL] 渲染段源 {run_gids[0]}..{run_gids[-1]} 归一后帧数不符")
        local_entries = [boundary_at[i] for i in run[:-1] if i in boundary_at]
        only_xf = all(e["type"] in XFADE_OF for e in local_entries) and not (open_fade is not None and run[0] == 0)
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
            # 段 = 相邻且中间无边界的组合并;bounds 按段索引
            chunks, bounds, acc = [], {}, 0
            for i in run:
                acc += gframes[i]
                if i in boundary_at and i != run[-1]:
                    chunks.append(acc)
                    bounds[len(chunks) - 1] = boundary_at[i]
                    acc = 0
            if acc:
                chunks.append(acc)
            fc, expect = build_run_filter(chunks, bounds, fps, w, h, open_fade if run[0] == 0 else None)
            pad_frames_total += sum(sum(_pad_frames(e, fps)) for e in local_entries)
        run_out = tmp_dir / f".xfrun{r}.out.mp4"
        _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(run_src),
              "-filter_complex", fc, "-map", "[vout]", "-an",
              "-c:v", "libx264", "-crf", str(max(14, crf - 2)), "-preset", preset,
              "-pix_fmt", "yuv420p", "-color_range", "tv", str(run_out)], timeout=7200)
        got = count_frames_of(str(run_out))
        if got != expect:
            raise SystemExit(f"[FAIL] 渲染段 {run_gids[0]}..{run_gids[-1]} 帧数不符:"
                             f"want {expect} got {got}")
        seg_paths.append((run[0], run_out))
        cleanup += [lst, run_src, run_out]
    # 总装 concat 列表:copy 组按原文件、渲染 run 按渲染段,严格组序
    final_lst = tmp_dir / ".xf_final.txt"
    lines, i = [], 0
    seg_by_start = {s: p for s, p in seg_paths}
    while i < len(order):
        if i in run_of:
            run = runs[run_of[i]]
            lines.append(f"file '{seg_by_start[run[0]].resolve()}'\n")
            i = run[-1] + 1
        else:
            lines.append(f"file '{files[i].resolve()}'\n")
            i += 1
    final_lst.write_text("".join(lines))
    cleanup.append(final_lst)
    tmp = out_path.with_name(out_path.stem + ".rendering.mp4")
    n_copy = sum(1 for i in range(len(order)) if i not in run_of)
    n_pad = sum(1 for e in boundary_at.values() if sum(_pad_frames(e, fps)) > 0)
    print(f"[RUN  ] 分段式转场渲染:{len(xf_entries)} 处叠化 + {n_pad} 处垫片 / {len(runs)} 个渲染段"
          f"(重编码 {len(order) - n_copy} 组)+ {n_copy} 组流拷贝 → {out_path.relative_to(proj)}")
    ops = pad_ops(entries, fps)
    # 源 cut 带声轨时随总装带回:无垫片流拷贝(模块契约:声轨不碰);有垫片按 timemap 重映射(黑场声音按 hold_audio)
    has_audio = bool((_probe(src_path).get("audio") or {}))
    a_args, a_tmp = [], None
    if has_audio and ops:
        a_tmp = tmp_dir / ".xf_audio.remap.wav"
        res = timemap.remap_audio(src_path, a_tmp, ops, src_dur=src_dur)
        cleanup.append(a_tmp)
        print(f"[RUN  ] 源声轨按 timemap 重映射:{timemap.describe(ops)} → {res['out_duration']:.3f}s")
        a_args = ["-i", str(a_tmp), "-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-b:a", "192k"]
    elif has_audio:
        a_args = ["-i", str(src_path), "-map", "0:v:0", "-map", "1:a:0", "-c:a", "copy"]
    _run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
          "-i", str(final_lst), *a_args, "-c:v", "copy",
          "-movflags", "+faststart", str(tmp)],
         timeout=1200)
    total_frames = sum(gframes) + pad_frames_total
    got = count_frames_of(str(tmp))
    if got != total_frames:
        raise SystemExit(f"[FAIL] 总装帧数不符:want {total_frames} got {got}")
    for p in cleanup:
        p.unlink(missing_ok=True)
    if out_path.exists():
        out_path.unlink()
    tmp.rename(out_path)
    print(f"[DONE ] {out_path.relative_to(proj)} = {_fmt(probe_duration(out_path))}"
          f"(源 {_fmt(src_dur)};{got} 帧,{n_copy}/{len(order)} 组零再编码"
          + (f",垫片 +{pad_frames_total} 帧" if pad_frames_total else "") + ")")
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


# ---------------------------------------------------------------- check

def _gray_frame(path, t, w, h):
    """t 秒处一帧灰度字节(缩放到 w×h)。"""
    return _run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.6f}", "-i", str(path), "-frames:v", "1",
                 "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "gray", "-"], timeout=120, binary=True)


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
        extra = [k for k in keys if k not in want and k[0] is not None]
        dup = [k for k in set(keys) if keys.count(k) > 1]
        rec("transitions_planned", not (missing or extra or dup),
            f"组边界 {len(bounds)} / 条目 {len(planned)};缺 {missing[:4]} 多 {extra[:4]} 重 {dup[:4]}")

    # 非硬切条目 ⇔ shot_list transition_in
    by_gid = {g.get("group_id"): transition_of(g) for g in (shot_list.get("generation_groups") or [])}
    mism = []
    for e in planned:
        if e.get("type") in (None, "hard_cut"):
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
    planned_to = {e.get("to_group") for e in planned if e.get("type") not in (None, "hard_cut") or _pad_s(e)}
    for gid, t in by_gid.items():
        if (t["type"] != "hard_cut" or sum(pad_of(t)[:2]) > 0) and gid not in planned_to:
            mism.append(f"{gid}:shot_list {t['type']} 未进 timeline")
    rec("transitions_match_shot_list", not mism, f"非硬切 {len(planned_to)} 处" + (f";不一致 {mism[:5]}" if mism else ""))

    renderable = [e for e in planned if e.get("type") in TRANSITION_RENDERABLE or _pad_s(e)]
    pads = [e for e in planned if _pad_s(e) and e.get("from_group") is not None]
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
    if pads:
        rec("duration_as_planned", abs(sd + pad_s - od) <= tol,
            f"源 {_fmt(sd)} + 垫片 {pad_s:.3f}s = 期望 {_fmt(sd + pad_s)} / 转场后 {_fmt(od)}(容差 ±1 帧 = {tol:.3f}s;{len(pads)} 处垫片)")
    else:
        rec("duration_unchanged", abs(sd - od) <= tol, f"源 {_fmt(sd)} / 转场后 {_fmt(od)}(容差 ±1 帧 = {tol:.3f}s)")
    if bool(src_i["audio"]) != bool(out_i["audio"]):
        rec("audio_stream_intact", False, f"声轨有无不一致:源 {bool(src_i['audio'])} / 转场后 {bool(out_i['audio'])}")
    elif src_i["audio"]:
        sa, oa = float(src_i["audio"].get("duration") or sd), float(out_i["audio"].get("duration") or od)
        if pads:
            rec("audio_stream_intact", abs(sa + pad_s - oa) <= 0.10, f"声轨时长 源 {_fmt(sa)} + 垫片 {pad_s:.3f}s / 转场后 {_fmt(oa)}(timemap 重映射)")
        else:
            rec("audio_stream_intact", abs(sa - oa) <= 0.05, f"声轨时长 源 {_fmt(sa)} / 转场后 {_fmt(oa)}(流拷贝)")
    else:
        rec("audio_stream_intact", True, "源无声轨,转场后亦无")

    w, h = 160, 90
    problems, verified, whitelist = [], 0, []
    step = 1.0 / fps
    for e in planned:
        ty, t, d = e.get("type"), float(e.get("cut_time_s") or 0), float(e.get("duration_s") or 0)
        t = round(t * fps) / fps        # 量化到整帧:渲染按帧号切界,-ss 用未量化秒会错位 1 帧
        fz, hd = (x / fps for x in _pad_frames(e, fps))
        to = m(t)                        # 本组首帧在成片上的时刻(垫片之后)
        try:
            if (fz or hd) and e.get("from_group") is not None:
                # 垫片:黑场中点近黑;定格帧 ≈ 前组末帧(源 t−1 帧);dip/fade 类前组尾淡出末帧近黑
                if hd:
                    mu = _mean(_gray_frame(out_path, to - hd / 2, w, h))
                    if mu > DARK_MAX:
                        problems.append(f"{e['at_shot']} 黑场垫片中点灰度均值 {mu:.0f}")
                    whitelist.append({"at_shot": e["at_shot"], "type": f"{ty}+hold", "start_s": round(to - hd, 3), "end_s": round(to, 3)})
                if fz:
                    ref = _gray_frame(src_path, t - step, w, h)
                    x = _gray_frame(out_path, to - hd - fz / 2, w, h)
                    mad = _mad(x, ref)
                    if ty in ("dip_black", "dip_white") or ty in FADE_COLOR:
                        pass                      # 定格段叠着淡出,不与源帧比对
                    elif mad > SAME_TOL + 4:
                        problems.append(f"{e['at_shot']} 定格帧与前组末帧差 {mad:.1f}")
                if ty in ("dip_black", "fade_black"):
                    mu = _mean(_gray_frame(out_path, to - hd - step, w, h))
                    if mu > DARK_MAX:
                        problems.append(f"{e['at_shot']} {ty}+垫片 前组尾淡出末帧灰度均值 {mu:.0f}")
                    dd = d / 2 if ty == "dip_black" else d
                    whitelist.append({"at_shot": e["at_shot"], "type": ty, "start_s": round(to - hd - dd, 3), "end_s": round(to - hd, 3)})
                    if ty == "dip_black":
                        whitelist.append({"at_shot": e["at_shot"], "type": ty, "start_s": round(to, 3), "end_s": round(to + d / 2, 3)})
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
    hard = [e for e in planned if (e.get("type") in (None, "hard_cut") or e.get("renders_as") == "hard_cut") and not _pad_s(e)]
    pick = hard[:: max(1, len(hard) // 4)][:4] if hard else []
    samples = [(f"{e['at_shot']} +0.25s", float(e["cut_time_s"]) + 0.25) for e in pick]
    samples.append(("片尾 −0.5s", max(0.0, sd - 0.5)))
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
    rec("hard_cut_positions_intact", not drift, f"抽样 {len(samples)} 点与源帧比对" + ("(按 timemap 对位)" if pads else "")
        + (f";漂移 {drift[:4]}" if drift else ""))

    ok = fails == 0
    if write:
        write_ledger(proj, ep, src_path, out_path, planned, timeline.get("transitions_policy") or {}, results, whitelist, ops)
    print(f"[{'PASS' if ok else 'FAIL'} ] transition_render_ok:{len(results)} 项,FAIL {fails}")
    return ok, results


def write_ledger(proj, ep, src_path, out_path, entries, policy, checks, whitelist=None, ops=None):
    ed = proj / "edit" / ep
    ed.mkdir(parents=True, exist_ok=True)
    ops = timemap.normalize_ops(ops or [])
    data = {
        "file": f"edit/{ep}/{LEDGER}", "episode": ep,
        "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cli": "code/render_transitions.py", "policy": policy,
        "src_cut": str(src_path.relative_to(proj)) if src_path and src_path.exists() else None,
        "out_cut": str(out_path.relative_to(proj)) if out_path and out_path.exists() else None,
        "transitions": entries,
        "black_frame_whitelist": whitelist or [],
        # 节奏垫片(2026-09-17):成片相对 src_cut 的时长编辑表,finalize_episode.py 据此平移外挂声轨/字幕(§9B)
        "pads": [e for e in entries if _pad_s(e) and e.get("from_group") is not None],
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
        ap.add_argument("cmd", choices=("plan", "render", "check"))
        ap.add_argument("--src", default=DEFAULT_SRC, help=f"源粗成片(edit 产物),默认 {DEFAULT_SRC}")
        ap.add_argument("--out", default=DEFAULT_OUT, help=f"转场后成片,默认 {DEFAULT_OUT}")
        ap.add_argument("--dry-run", action="store_true", help="plan:只打印,不写回 timeline.json")
        ap.add_argument("--crf", type=int, default=18)
        ap.add_argument("--preset", default="medium")

    args, proj = parse_args(__doc__.splitlines()[0], configure=configure, argv=argv)
    ed, _, _, src_p, out_p = _ep_paths(proj, args.ep, args.src, args.out)
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
