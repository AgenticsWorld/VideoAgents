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
  check    机检 transition_render_ok(封装前必跑,FAIL 即不交付):
             transitions_planned          timeline.transitions 覆盖全部组边界,每边界恰一条
             transitions_match_shot_list  非硬切条目与 shot_list transition_in 一一对应(类型/时长);
                                          timeline 里多出的非硬切 = Agent 自创,FAIL
             duration_unchanged           cut_v2 时长 = cut_v1 ±1 帧
             audio_stream_intact          有/无声轨与源一致,声轨时长一致(流拷贝)
             transition_frames_verified   逐处抽帧:叠化中点 ≈ 前后帧 50/50 混合;dip 中点近黑/近白;
                                          fade 尾帧近黑/近白;硬切边界两侧帧与源一致(无时间漂移)
           台账 edit/epNN/transitions_render.json(含 dip/fade 黑场白名单窗口,供 no_black_frames 豁免)

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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_generation_groups import (TRANSITION_ANNOTATION, TRANSITION_RENDERABLE,  # noqa: E402
                                     transition_of)

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
        if ty != "hard_cut":
            ent["intent"] = t.get("intent")
            ent["reason"] = t.get("reason")
            ent["source"] = "shot_list.transition_in"
        else:
            ent["reason"] = ent.get("reason") or "default hard cut"
        entries.append(ent)

    # shot_list 有非硬切设计、timeline 却没有对应边界(组缺席 / 不在边界)= 设计落不了地
    planned_to = {e["to_group"] for e in entries if e["type"] != "hard_cut"}
    for g in groups:
        t = transition_of(g)
        if t["type"] != "hard_cut" and g.get("group_id") not in planned_to:
            problems.append(f"{g.get('group_id')} transition_in={t['type']} 在 timeline video 轨上没有对应的组入口边界"
                            f"(组缺席或不在边界)")
    renderable = [e for e in entries if e["type"] in TRANSITION_RENDERABLE]
    policy = {
        "cli": "code/render_transitions.py", "compensation": "pad",
        "source": "shot_list.generation_groups[].transition_in",
        "planned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "src_cut": str(src_path.name) if src_path else None,
        "boundaries": len(bounds), "renderable": len(renderable),
        "renderable_total_s": round(sum(e["duration_s"] for e in renderable), 3),
        "timeline_total_s": round(total, 3),
    }
    return entries, policy, problems


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
        if e["type"] != "hard_cut":
            print(f"         {e['at_shot']:<18} @{_fmt(e['cut_time_s'])}  {e['type']}"
                  f"{'' if 'duration_s' not in e else ' ' + str(e['duration_s']) + 's'}  {e.get('intent') or ''}")
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
    head = "[0:v]" + ",".join(["format=yuv420p"] + fades)
    if not xf:
        return head + "[vout]", 0
    cut_f = [_frames(e["cut_time_s"], fps) for e in xf]
    dur_f = [max(2, _frames(e["duration_s"], fps)) for e in xf]
    half_a = [df // 2 for df in dur_f]            # 前侧(前组尾)克隆帧数
    half_b = [df - ha for df, ha in zip(dur_f, half_a)]   # 后侧(本组首)克隆帧数
    k = len(xf) + 1
    parts = [head + f",split={k}" + "".join(f"[p{i}]" for i in range(k))]
    starts = [0] + cut_f
    ends = cut_f + [_frames(total_s, fps) + 5 * int(fps)]   # 末段 end 给足余量,trim 到源尾
    for i in range(k):
        f = f"[p{i}]trim=start_frame={starts[i]}:end_frame={ends[i]},setpts=PTS-STARTPTS"
        if i > 0:
            f += f",tpad=start_mode=clone:start_duration={half_b[i - 1] / fps:.6f}"
        if i < k - 1:
            f += f",tpad=stop_mode=clone:stop_duration={half_a[i] / fps:.6f}"
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
    fc, n_xf = build_filter(entries, src_dur, fps)
    n_fade = sum(1 for e in entries if e["type"] in FADE_COLOR)
    if not n_xf and not n_fade:
        print("[INFO ] 无可渲染转场:全片硬切,不产出 cut_v2(cut_v1 即转场定稿)")
        write_ledger(proj, ep, src_path, None, entries, policy, checks=None)
        return None
    tmp = out_path.with_name(out_path.stem + ".rendering.mp4")
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src_path),
           "-filter_complex", fc, "-map", "[vout]", "-map", "0:a?", "-c:a", "copy",
           "-c:v", "libx264", "-crf", str(crf), "-preset", preset, "-pix_fmt", "yuv420p",
           "-movflags", "+faststart", str(tmp)]
    print(f"[RUN  ] ffmpeg 转场渲染:xfade {n_xf} 处 + fade {n_fade} 处 → {out_path.relative_to(proj)}"
          f"(libx264 crf{crf},声轨流拷贝)")
    _run(cmd, timeout=7200)
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
    planned_to = {e.get("to_group") for e in planned if e.get("type") not in (None, "hard_cut")}
    for gid, t in by_gid.items():
        if t["type"] != "hard_cut" and gid not in planned_to:
            mism.append(f"{gid}:shot_list {t['type']} 未进 timeline")
    rec("transitions_match_shot_list", not mism, f"非硬切 {len(planned_to)} 处" + (f";不一致 {mism[:5]}" if mism else ""))

    renderable = [e for e in planned if e.get("type") in TRANSITION_RENDERABLE]
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
    rec("duration_unchanged", abs(sd - od) <= tol, f"源 {_fmt(sd)} / 转场后 {_fmt(od)}(容差 ±1 帧 = {tol:.3f}s)")
    if bool(src_i["audio"]) != bool(out_i["audio"]):
        rec("audio_stream_intact", False, f"声轨有无不一致:源 {bool(src_i['audio'])} / 转场后 {bool(out_i['audio'])}")
    elif src_i["audio"]:
        sa, oa = float(src_i["audio"].get("duration") or sd), float(out_i["audio"].get("duration") or od)
        rec("audio_stream_intact", abs(sa - oa) <= 0.05, f"声轨时长 源 {_fmt(sa)} / 转场后 {_fmt(oa)}(流拷贝)")
    else:
        rec("audio_stream_intact", True, "源无声轨,转场后亦无")

    # 抽帧核对
    w, h = 160, 90
    problems, verified, whitelist = [], 0, []
    step = 1.0 / fps
    for e in planned:
        ty, t, d = e.get("type"), float(e.get("cut_time_s") or 0), float(e.get("duration_s") or 0)
        try:
            if ty == "dissolve":
                a = _gray_frame(src_path, t - step, w, h)
                b = _gray_frame(src_path, t + step, w, h)
                x = _gray_frame(out_path, t, w, h)
                m = _blend_mad(x, a, b)
                if m > BLEND_TOL:
                    problems.append(f"{e['at_shot']} dissolve 中点与前后帧 50/50 混合差 {m:.1f}>{BLEND_TOL}")
                verified += 1
            elif ty in ("dip_black", "dip_white"):
                # ffmpeg fadeblack/fadewhite 的纯黑/纯白峰值不在窗口正中(实测约 1/3 处),窗口内取三点极值
                mus = [_mean(_gray_frame(out_path, t + k * d / 4, w, h)) for k in (-1, 0, 1)]
                mu = min(mus) if ty == "dip_black" else max(mus)
                ok = mu <= DARK_MAX if ty == "dip_black" else mu >= BRIGHT_MIN
                if not ok:
                    problems.append(f"{e['at_shot']} {ty} 窗口内灰度极值 {mu:.0f}(三点 {[round(m) for m in mus]})")
                whitelist.append({"at_shot": e["at_shot"], "type": ty, "start_s": round(t - d / 2, 3), "end_s": round(t + d / 2, 3)})
                verified += 1
            elif ty in FADE_COLOR:
                if e.get("from_group") is None:
                    x = _gray_frame(out_path, 0.0, w, h)
                    whitelist.append({"at_shot": e["at_shot"], "type": ty, "start_s": 0.0, "end_s": round(d, 3)})
                else:
                    x = _gray_frame(out_path, t - step, w, h)
                    whitelist.append({"at_shot": e["at_shot"], "type": ty, "start_s": round(t - d, 3), "end_s": round(t, 3)})
                mu = _mean(x)
                ok = mu <= DARK_MAX if ty == "fade_black" else mu >= BRIGHT_MIN
                if not ok:
                    problems.append(f"{e['at_shot']} {ty} 端帧灰度均值 {mu:.0f}")
                verified += 1
        except RuntimeError as ex:
            problems.append(f"{e.get('at_shot')} 抽帧失败:{str(ex)[-160:]}")
    # 硬切边界两侧 + 非转场区不得漂移:抽最多 4 个硬切边界(均匀取)与片尾前 0.5s
    hard = [e for e in planned if e.get("type") in (None, "hard_cut") or e.get("renders_as") == "hard_cut"]
    pick = hard[:: max(1, len(hard) // 4)][:4] if hard else []
    samples = [(f"{e['at_shot']} +0.25s", float(e["cut_time_s"]) + 0.25) for e in pick]
    samples.append(("片尾 −0.5s", max(0.0, sd - 0.5)))
    drift = []
    for label, t in samples:
        try:
            m = _mad(_gray_frame(out_path, t, w, h), _gray_frame(src_path, t, w, h))
            if m > SAME_TOL:
                drift.append(f"{label} 差 {m:.1f}")
        except RuntimeError as ex:
            drift.append(f"{label} 抽帧失败:{str(ex)[-120:]}")
    rec("transition_frames_verified", not problems, f"核 {verified}/{len(renderable)} 处"
        + (f";异常 {problems[:4]}" if problems else ""))
    rec("hard_cut_positions_intact", not drift, f"抽样 {len(samples)} 点与源帧比对" + (f";漂移 {drift[:4]}" if drift else ""))

    ok = fails == 0
    if write:
        write_ledger(proj, ep, src_path, out_path, planned, timeline.get("transitions_policy") or {}, results, whitelist)
    print(f"[{'PASS' if ok else 'FAIL'} ] transition_render_ok:{len(results)} 项,FAIL {fails}")
    return ok, results


def write_ledger(proj, ep, src_path, out_path, entries, policy, checks, whitelist=None):
    ed = proj / "edit" / ep
    ed.mkdir(parents=True, exist_ok=True)
    data = {
        "file": f"edit/{ep}/{LEDGER}", "episode": ep,
        "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cli": "code/render_transitions.py", "policy": policy,
        "src_cut": str(src_path.relative_to(proj)) if src_path and src_path.exists() else None,
        "out_cut": str(out_path.relative_to(proj)) if out_path and out_path.exists() else None,
        "transitions": entries,
        "black_frame_whitelist": whitelist or [],
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
