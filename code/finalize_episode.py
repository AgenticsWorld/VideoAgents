#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""finalize_episode.py — 终版封装宿主 CLI:片头/片尾接入后的时间轴平移 + 机检 intro_offset_ok。

背景(WORKFLOW.md §9B,2026-08-26):片头接在正片之前后,**正片时间轴上的一切都整体后移一个片头时长**——
字幕(subtitles.srt/.ass)、外挂声轨(assets/audio/final/epNN.wav)都是正片 0 秒基准,直接配 final.mp4 就会
整体偏早。这一步以前全靠 Agent 手算 ffmpeg,能力弱的模型经常忘掉或只挪了字幕没挪音频
(前科 2026-07-18 thedoor ep01–06 字幕整体偏早一个片头)。本 CLI 把平移做成确定性工序:

  probe     实测各段时长(ffprobe),与 placement.json 声明值交叉核对,写台账 edit/epNN/final_layout.json
  shift     subtitles.srt(+ .ass)整体 +片头实测时长 → subtitles_final.srt(+ .ass);无片头 = 原样拷贝;
            正片带**时长编辑表**(timemap,见下)时先逐条按表平移,再 +片头
  assemble  intro + cut(画面)+ final_audio(声轨)+ outro + teaser 一次拼成 final.mp4——
            声轨随正片段一起进 concat,片头偏移由拼接**天然**产生,不需要也不允许再手算 -itsoffset;
            拼完自动跑 shift + check
  check     机检 intro_offset_ok(封装后、发布前必跑):
              final_duration_layout     final.mp4 时长 == Σ各段实测时长(±0.25s)
              subtitles_final_present   有 subtitles.srt 就必须有 subtitles_final.srt
              subtitle_offset_all_cues  逐条 cue 起止 = 正片基准 + 片头实测(±200ms),条数一致
              subtitle_ass_offset       .ass 同上(存在时)
              subtitle_within_final     末条 cue 不超出成片
              letterbox_applied         上下黑边开启时成片分辨率 == 最终输出画幅的版式画布
              audio_offset_measured     成片声轨 vs final_audio 互相关实测滞后 == 片头实测(±80ms),
                                        取首/中/尾三段窗口分别测,三段一致 = 无累计漂移
              placement_declared_match  placement.json 声明时长与实测一致(±100ms,WARN 不拦)

时长编辑表 timemap(2026-09-17,§9B/§9C 节奏垫片):后期页的「插黑 / 定格」「删段」与组间黑场停留会改变正片时长,
  外挂声轨/字幕的正片 0 秒基准随之失效。宿主把这些编辑记成确定性映射表(modules/timemap.py):
    edit/epNN/timemap.json               post_apply.py sync-timeline 写,各组后期版本的组内插黑/定格/删段(原粗剪基准)
    edit/epNN/transitions_render.json    render_transitions.py 写,组边界垫片(其 src_cut 基准)
  本 CLI 在正片 = 转场产物(out_cut)或后期拼片(cut_post*)时把两层复合成一张总表:外挂声轨按表重映射为
  edit/epNN/final_audio_timemapped.wav(黑场声音按 hold_audio:延续/淡出/静音)再进 concat;字幕逐条按表平移再 +片头;
  check 的 subtitle_offset_all_cues / audio_offset_measured 按表对位(期望滞后仍 = 片头)。无表 = 行为与以前完全一致。

集尾收束(2026-09-25,§9C):正片为 render_transitions 产物且台账 transitions_render.json#episode_close 在时,画面已在末尾淡出/切黑并停留
  hold_s;本 CLI 在 assemble 时对外挂声轨施加同刻处理——fade_black/fade_white 在 fade_end_s 前 duration_s 内 afade 淡出、cut_black/cut_white
  在 fade_end_s 硬切(20ms 防爆音)、hold_audio=mute 到黑即静音——其后补静音到与画面等长。停留不平移任何时刻,不进 timemap、不改混音基准。

上下黑边(2026-10-06,后期处理页「包装 › 上下黑边」,settings.json#output.letterbox_enabled / letterbox_aspect):开启时
  assemble 把成片画布改成「最终输出画幅」(画布短边 = 正片短边),各段画面等比缩放后居中补黑边(2.35:1 → 16:9、16:9 → 9:16 等);
  正片、粗剪、组 clip 都不动,黑边只在这一步加。版式写进台账 final_layout.json#letterbox(花字版成片按其中 picture 落位),
  check 多一项 letterbox_applied(开启时成片分辨率 ≠ 版式画布 → FAIL,重新 assemble)。

约定:
  - 段序默认 intro,cut,outro,teaser(--layout 可改);settings.json#packaging 关闭的段与不存在的文件自动跳过;
  - 正片段时长 = max(cut 画面时长, final_audio 时长)——严禁 -shortest 截音频,画面不足用末帧补齐;
  - av(音频锁定)插件项目要求母带零重编码,与片头接入互斥:检测到 av/audio_map.json 且启用片头时拒绝 assemble。

用法:
  python3 code/finalize_episode.py probe    --project <slug> --ep epNN
  python3 code/finalize_episode.py shift    --project <slug> --ep epNN
  python3 code/finalize_episode.py assemble --project <slug> --ep epNN [--cut cut_v2.mp4] [--audio ...] [--out final.mp4]
  python3 code/finalize_episode.py check    --project <slug> --ep epNN [--final ep01_final_v6.mp4] [--tol-ms 80]
退出码:全 PASS=0,任一 FAIL=1(WARN 不影响退出码)。
"""
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from _common import parse_args  # 副作用:modules/ 入 sys.path
from avsync import probe_duration, require_tools
import mix_manifest
import timemap
from output_format import letterbox_layout, resolve_letterbox

SEGMENTS = ("intro", "cut", "outro", "teaser")
SEG_FILES = {"intro": ["intro.mp4"], "outro": ["outro.mp4"],
             "teaser": ["teaser.mp4", "next_ep_teaser.mp4"]}
SEG_SETTING = {"intro": "intro_enabled", "outro": "outro_enabled", "teaser": "teaser_enabled"}
LEDGER = "final_layout.json"
FPS = 24
SUB_TOL_S = 0.200      # 字幕时间码容差(WORKFLOW 口径 ±200ms)
DUR_TOL_S = 0.25       # 成片总时长容差
PLACEMENT_TOL_S = 0.10
AUDIO_SR = 8000        # 互相关采样率(8k 足够定位 ±10ms)
AUDIO_WIN_S = 20.0     # 每个互相关窗口长度


# ---------------------------------------------------------------- 基础

def _run(cmd, timeout=1800):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"命令失败({p.returncode}):{' '.join(map(str, cmd))}\n{p.stderr[-2000:]}")
    return p.stdout


def _probe_streams(path):
    out = _run(["ffprobe", "-v", "error", "-show_entries",
                "stream=codec_type,width,height,r_frame_rate,sample_rate,channels",
                "-of", "json", str(path)], timeout=60)
    info = {"video": None, "audio": None}
    for s in json.loads(out).get("streams", []):
        t = s.get("codec_type")
        if t in info and info[t] is None:
            info[t] = s
    return info


def _sha256(path, limit=1 << 24):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read(limit))
    return h.hexdigest()[:16]


def _settings(proj):
    try:
        return json.loads((proj / "settings.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def _packaging(proj):
    return _settings(proj).get("packaging") or {}


def letterbox_plan(proj, segs):
    """上下黑边版式(output_format.letterbox_layout);未开启 / 正片画幅已与最终输出画幅一致 -> None。"""
    aspect = resolve_letterbox(_settings(proj))
    cut = next((s for s in segs if s["name"] == "cut"), None)
    if not aspect or cut is None:
        return None
    vs = (cut.get("streams") or {}).get("video") or {}
    return letterbox_layout(int(vs.get("width") or 0), int(vs.get("height") or 0), aspect)


def _is_av_project(proj):
    return (proj / "av" / "audio_map.json").is_file()


def _fmt(x):
    return f"{x:.3f}s"


# ---------------------------------------------------------------- 段落解析

def find_cut(proj, ep, override=None):
    ed = proj / "edit" / ep
    if override:
        p = Path(override)
        p = p if p.is_absolute() else ed / p
        if not p.is_file():
            raise SystemExit(f"[FAIL] 正片不存在:{p}")
        return p
    # 无 --cut:与 render_captions / caption_timeline 同一口径(timemap_layers.resolve_cut,#79)——台账记录的正片优先,
    # 台账过期/被写坏时改取后期拼片 cut_post_v2 / cut_post(须有后期证据)或更新的 cut_v*
    cut = resolve_cut(proj, ep)
    if cut is None:
        raise SystemExit(f"[FAIL] {ed} 下没有 cut_v*.mp4(edit 粗剪/transition 产物)")
    return cut


def find_audio(proj, ep, override=None):
    """外挂声轨:assets/audio/final/epNN.wav(主流程)→ assets/audio/master/epNN.mp3(av 插件)。
    override='none' 表示用 cut 自带音轨。"""
    if override == "none":
        return None
    if override:
        p = Path(override)
        p = p if p.is_absolute() else proj / p
        if not p.is_file():
            raise SystemExit(f"[FAIL] 声轨不存在:{p}")
        return p
    for rel in (f"assets/audio/final/{ep}.wav", f"assets/audio/final/{ep}.m4a",
                f"assets/audio/final/{ep}.mp3", f"assets/audio/master/{ep}.mp3",
                f"assets/audio/master/{ep}.wav"):
        p = proj / rel
        if p.is_file():
            return p
    return None


def find_final(proj, ep, override=None):
    ed = proj / "edit" / ep
    if override:
        p = Path(override)
        p = p if p.is_absolute() else ed / p
        return p if p.is_file() else None
    p = ed / "final.mp4"
    if p.is_file():
        return p
    cands = [c for c in ed.glob("*final*.mp4") if "caption" not in c.name]
    if not cands:
        return None
    return max(cands, key=lambda c: c.stat().st_mtime)


def load_placement(proj, ep):
    p = proj / "edit" / ep / "intro_outro" / "placement.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _declared_duration(placement, seg):
    node = placement.get(seg)
    if not isinstance(node, dict):
        return None, None
    for k in ("duration_s", "duration_s_measured_ffprobe", "duration"):
        if isinstance(node.get(k), (int, float)):
            return float(node[k]), node.get("file")
    return None, node.get("file")


from timemap_layers import load_timemap, _apply_mix_basis, resolve_cut  # noqa: E402,F401  2026-09-25 抽到 modules/,与 render_captions 共用


def load_episode_close(proj, ep, cut):
    """正片 cut 的集尾收束台账(transitions_render.json#episode_close,仅当 cut 就是该台账的 out_cut);无 = None。"""
    tr = proj / "edit" / ep / "transitions_render.json"
    if not tr.is_file():
        return None
    try:
        d = json.loads(tr.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    ec = d.get("episode_close")
    if not isinstance(ec, dict) or not d.get("out_cut") or Path(d["out_cut"]).name != Path(cut).name:
        return None
    if ec.get("fade_end_s") is None:
        return None
    return ec


def close_audio_filter(ec, cut_dur):
    """外挂声轨的集尾处理滤镜(assemble 时插在 aresample 之后):淡出类在 fade_end_s 前 duration_s 内 afade(mute 策略 = 到黑即切),
    切黑类在 fade_end_s 硬切(20ms 防爆音淡出);黑场停留段由 apad 补静音。返回滤镜串(可为空)。"""
    if not ec:
        return ""
    end = float(ec.get("fade_end_s") or 0.0)
    if end <= 0:
        return ""
    ty = str(ec.get("type") or "")
    d = float(ec.get("duration_s") or 0.0) if ty in ("fade_black", "fade_white") else 0.0
    if str(ec.get("hold_audio") or "fade") == "mute" or d <= 0:
        d = 0.02
    return f"afade=t=out:st={max(0.0, end - d):.6f}:d={d:.6f}"


def timemapped_audio(proj, ep, audio, ops, notes=None):
    """外挂声轨按 timemap 重映射(缓存于 edit/epNN/final_audio_timemapped.wav);无 ops 原样返回。"""
    if not ops or audio is None:
        return audio
    dst = proj / "edit" / ep / "final_audio_timemapped.wav"
    out, res = timemap.remap_audio_cached(audio, dst, ops)
    if notes is not None:
        notes.append(f"外挂声轨 {Path(audio).name} 按 timemap 重映射 → {dst.name}({res.get('src_duration')}s → {res.get('out_duration')}s)")
    return out


def resolve_layout(proj, ep, layout, cut_override=None, audio_override=None, notes=None):
    """返回有序段列表:[{name, path, v_dur, a_dur, dur, has_audio, declared}]。"""
    notes = notes if notes is not None else []
    pk = _packaging(proj)
    placement = load_placement(proj, ep)
    io_dir = proj / "edit" / ep / "intro_outro"
    segs = []
    for name in layout:
        if name == "cut":
            cut = find_cut(proj, ep, cut_override)
            if not cut_override:
                notes.append(f"正片未指定 --cut,自动解析为 {cut.name}(台账 final_layout.json 优先,台账过期时取后期拼片 cut_post*/更新的 cut_v*)")
            audio = find_audio(proj, ep, audio_override)
            ops, tm_info = load_timemap(proj, ep, cut, notes, audio_used=audio is not None)
            audio_src = audio
            audio_ops = tm_info.get("audio_ops", ops)   # 混音已含组边界层时 = 去掉边界层的表(2026-09-24)
            if audio is not None and audio_ops:
                audio = timemapped_audio(proj, ep, audio, audio_ops, notes)
            v = probe_duration(cut)
            st = _probe_streams(cut)
            if audio is not None:
                a = probe_duration(audio)
            elif st["audio"] is not None:
                a = v
                notes.append(f"cut 段使用 {cut.name} 自带音轨(未找到 assets/audio/final/{ep}.*)")
            else:
                raise SystemExit(f"[FAIL] 正片 {cut.name} 无音轨且找不到外挂声轨 assets/audio/final/{ep}.wav")
            close = load_episode_close(proj, ep, cut)
            close_hold = float((close or {}).get("hold_s") or 0.0)
            if close:
                notes.append(f"集尾收束 {close.get('type')}:画面全黑 @{float(close['fade_end_s']):.3f}s,停留 {close_hold:.3f}s;"
                             f"外挂声轨同刻{'切断' if (str(close.get('type', '')).startswith('cut') or close.get('hold_audio') == 'mute') else '淡出'}并补静音")
            if audio is not None and abs(a - (v - close_hold)) > 0.5:
                notes.append(f"⚠ 正片画面 {v:.3f}s" + (f"(含尾停留 {close_hold:.3f}s)" if close_hold else "") + f" 与声轨 {a:.3f}s 相差 {abs(a - (v - close_hold)):.3f}s(>0.5s);"
                             "按较长者封装,画面不足用末帧补齐、严禁截音频;差异过大请先回 edit 对齐")
            segs.append({"name": "cut", "path": str(cut), "audio": str(audio) if audio else None,
                         "audio_src": str(audio_src) if audio_src else None, "timemap": tm_info,
                         "v_dur": v, "a_dur": a, "dur": max(v, a), "has_audio": True,
                         "declared": None, "streams": st, "episode_close": close})
            continue
        if name not in SEG_FILES:
            raise SystemExit(f"[FAIL] 未知段名 {name!r},可用:{','.join(SEGMENTS)}")
        if pk.get(SEG_SETTING[name], True) is False:
            notes.append(f"{name}:项目设置已禁用,跳过")
            continue
        declared, decl_file = _declared_duration(placement, name)
        cands = ([decl_file] if decl_file else []) + SEG_FILES[name]
        path = next((io_dir / f for f in cands if (io_dir / f).is_file()), None)
        if path is None:
            notes.append(f"{name}:intro_outro/ 下无 {'/'.join(cands)},跳过")
            continue
        v = probe_duration(path)
        st = _probe_streams(path)
        segs.append({"name": name, "path": str(path), "audio": None, "v_dur": v, "a_dur": v,
                     "dur": v, "has_audio": st["audio"] is not None, "declared": declared,
                     "streams": st})
    if not any(s["name"] == "cut" for s in segs):
        raise SystemExit("[FAIL] 段序里必须包含 cut(正片)")
    return segs


def offsets_of(segs):
    """各段在成片时间轴上的起点;cut 起点 = 片头偏移。"""
    t = 0.0
    out = {}
    for s in segs:
        out[s["name"]] = t
        t += s["dur"]
    return out, t


# ---------------------------------------------------------------- 字幕平移

_SRT_TIME = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})")
_SRT_LINE = re.compile(r"^\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})(.*)$")


def srt_to_s(t):
    m = _SRT_TIME.fullmatch(t.strip())
    if not m:
        raise ValueError(f"非法 SRT 时间码 {t!r}")
    h, mi, s, ms = m.groups()
    return int(h) * 3600 + int(mi) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


def s_to_srt(x):
    ms = int(round(max(0.0, x) * 1000))
    h, ms = divmod(ms, 3600000)
    mi, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{mi:02d}:{s:02d},{ms:03d}"


def parse_srt(path):
    """返回 [(start_s, end_s)],只读时间行,容忍 BOM/CRLF/多行文本。"""
    cues = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        m = _SRT_LINE.match(line)
        if m:
            cues.append((srt_to_s(m.group(1)), srt_to_s(m.group(2))))
    return cues


def shift_srt_text(text, delta):
    out = []
    for line in text.splitlines():
        m = _SRT_LINE.match(line)
        if m:
            a, b = srt_to_s(m.group(1)) + delta, srt_to_s(m.group(2)) + delta
            line = f"{s_to_srt(a)} --> {s_to_srt(b)}{m.group(3)}"
        out.append(line)
    return "\n".join(out) + "\n"


_ASS_EVENT = re.compile(r"^(Dialogue|Comment):\s*(\d+),([^,]+),([^,]+),(.*)$")


def ass_to_s(t):
    h, mi, rest = t.strip().split(":")
    return int(h) * 3600 + int(mi) * 60 + float(rest)


def s_to_ass(x):
    cs = int(round(max(0.0, x) * 100))
    h, cs = divmod(cs, 360000)
    mi, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{mi:02d}:{s:02d}.{cs:02d}"


def parse_ass(path):
    cues = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        m = _ASS_EVENT.match(line)
        if m:
            cues.append((ass_to_s(m.group(3)), ass_to_s(m.group(4))))
    return cues


def shift_ass_text(text, delta):
    out = []
    for line in text.splitlines():
        m = _ASS_EVENT.match(line)
        if m:
            a, b = ass_to_s(m.group(3)) + delta, ass_to_s(m.group(4)) + delta
            line = f"{m.group(1)}: {m.group(2)},{s_to_ass(a)},{s_to_ass(b)},{m.group(5)}"
        out.append(line)
    return "\n".join(out) + "\n"


def do_shift(proj, ep, intro_s, ops=None):
    ed = proj / "edit" / ep
    done = []
    ops = timemap.normalize_ops(ops or [])
    fn = timemap.map_fn(ops, intro_s)
    for ext, shifter, mapper in ((".srt", shift_srt_text, timemap.remap_srt_text), (".ass", shift_ass_text, timemap.remap_ass_text)):
        src, dst = ed / f"subtitles{ext}", ed / f"subtitles_final{ext}"
        if not src.is_file():
            continue
        text = src.read_text(encoding="utf-8-sig")
        if ops:
            out = mapper(text, fn)
        else:
            out = shifter(text, intro_s) if intro_s > 0 else text
        dst.write_text(out, encoding="utf-8")
        n = len(parse_srt(src) if ext == ".srt" else parse_ass(src))
        done.append((dst, n))
        print(f"[DONE] {dst.relative_to(proj)}:{n} 条 cue " + (f"按 timemap 逐条平移({timemap.describe(ops)})再 " if ops else "整体 ")
              + f"+{intro_s:.3f}s" + ("(无片头,原样拷贝)" if intro_s <= 0 and not ops else ""))
    if not done:
        print(f"[WARN ] {ed} 下无 subtitles.srt/.ass,未生成成片基准字幕(subtitle 尚未交付?)")
    return done


# ---------------------------------------------------------------- 台账

def write_ledger(proj, ep, segs, extra=None):
    offs, total = offsets_of(segs)
    intro_s = offs["cut"]
    ledger = {
        "schema": "final_layout/1.0",
        "episode": ep,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generated_by": "code/finalize_episode.py",
        "note": "成片时间轴台账:cut_offset_s 即片头偏移,正片基准(subtitles.srt / final_audio)上的一切时间码"
                "进入 final.mp4 都要 +cut_offset_s;字幕/发布只用 subtitles_final.*。",
        "cut_offset_s": round(intro_s, 6),
        "total_expected_s": round(total, 6),
        "timemap": next((s.get("timemap") for s in segs if s["name"] == "cut"), None),
        "episode_close": next((s.get("episode_close") for s in segs if s["name"] == "cut"), None),
        "letterbox": letterbox_plan(proj, segs),
        "segments": [{
            "name": s["name"], "file": str(Path(s["path"]).relative_to(proj)),
            "audio": (str(Path(s["audio"]).relative_to(proj)) if s.get("audio") else None),
            "audio_src": (str(Path(s["audio_src"]).relative_to(proj)) if s.get("audio_src") else None),
            "start_s": round(offs[s["name"]], 6), "duration_s": round(s["dur"], 6),
            "video_s": round(s["v_dur"], 6), "audio_s": round(s["a_dur"], 6),
            "declared_s": s["declared"], "sha256_16": _sha256(s["path"]),
        } for s in segs],
    }
    if extra:
        ledger.update(extra)
    p = proj / "edit" / ep / LEDGER
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")
    return p, ledger


def print_layout(proj, segs, notes):
    offs, total = offsets_of(segs)
    for n in notes:
        print(f"[NOTE ] {n}")
    for s in segs:
        extra = ""
        if s["name"] == "cut":
            extra = f"  画面 {_fmt(s['v_dur'])} / 声轨 {_fmt(s['a_dur'])}" \
                    + (f"({Path(s['audio']).name})" if s.get("audio") else "(自带)")
        elif s["declared"] is not None:
            d = s["dur"] - s["declared"]
            extra = f"  placement 声明 {_fmt(s['declared'])}" + (
                f"  ⚠ 实测差 {d:+.3f}s,以实测为准" if abs(d) > PLACEMENT_TOL_S else "  ✓")
        print(f"[SEG  ] {s['name']:<6} start={_fmt(offs[s['name']]):>10}  dur={_fmt(s['dur']):>10}"
              f"  {Path(s['path']).name}{'' if s['has_audio'] else '  (无音轨→补静音)'}{extra}")
    print(f"[LAYOUT] cut_offset(片头偏移)={_fmt(offs['cut'])}  成片应为 {_fmt(total)}")


# ---------------------------------------------------------------- assemble

def do_assemble(proj, ep, segs, out_path, crf=18, preset="medium"):
    require_tools("ffmpeg", "ffprobe")
    cut = next(s for s in segs if s["name"] == "cut")
    vs = cut["streams"]["video"] or {}
    W, H = int(vs.get("width") or 0), int(vs.get("height") or 0)
    if not W or not H:
        raise SystemExit("[FAIL] 无法读取正片分辨率")
    lb = letterbox_plan(proj, segs)
    if lb:   # 上下黑边:画布改成最终输出画幅,正片按台账版式落位,其余段照常等比缩放居中
        src_w, src_h = W, H
        (W, H), (px, py, pw, ph) = lb["canvas"], lb["picture"]
        print(f"[NOTE ] 上下黑边:正片 {src_w}x{src_h} → 最终输出画幅 {lb['aspect']} 画布 {W}x{H},"
              f"画面 {pw}x{ph} 居中,黑边在{'上下' if lb['bars'] == 'top_bottom' else '左右'}")
    inputs, fc, vlabels, alabels = [], [], [], []
    idx = 0
    for s in segs:
        vi = idx
        inputs += ["-i", s["path"]]
        idx += 1
        ai = None
        if s["name"] == "cut" and s.get("audio"):
            inputs += ["-i", s["audio"]]
            ai = idx
            idx += 1
        elif s["has_audio"]:
            ai = vi
        pad_v = max(0.0, s["dur"] - s["v_dur"])
        if lb and s["name"] == "cut":
            vf = f"[{vi}:v]scale={pw}:{ph},pad={W}:{H}:{px}:{py},fps={FPS},setsar=1,format=yuv420p"
        else:
            vf = (f"[{vi}:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
                  f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,fps={FPS},setsar=1,format=yuv420p")
        if pad_v > 0.001:
            vf += f",tpad=stop_mode=clone:stop_duration={pad_v:.6f}"
        vf += f",trim=duration={s['dur']:.6f},setpts=PTS-STARTPTS[v{vi}]"
        fc.append(vf)
        if ai is not None:
            close_af = close_audio_filter(s.get("episode_close"), s["dur"]) if s["name"] == "cut" else ""
            af = (f"[{ai}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                  + (close_af + "," if close_af else "")
                  + f"apad=whole_dur={s['dur']:.6f},atrim=duration={s['dur']:.6f},asetpts=PTS-STARTPTS[a{vi}]")
        else:
            af = (f"anullsrc=r=48000:cl=stereo,atrim=duration={s['dur']:.6f},"
                  f"asetpts=PTS-STARTPTS[a{vi}]")
        fc.append(af)
        vlabels.append(f"[v{vi}]")
        alabels.append(f"[a{vi}]")
    n = len(segs)
    fc.append("".join(v + a for v, a in zip(vlabels, alabels)) + f"concat=n={n}:v=1:a=1[vout][aout]")
    tmp = out_path.with_name(out_path.stem + ".assembling.mp4")
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
           "-filter_complex", ";".join(fc), "-map", "[vout]", "-map", "[aout]",
           "-c:v", "libx264", "-crf", str(crf), "-preset", preset, "-pix_fmt", "yuv420p", "-r", str(FPS),
           "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(tmp)]
    print(f"[RUN  ] ffmpeg concat {n} 段 → {out_path.relative_to(proj)}(libx264 crf{crf} / aac 192k)")
    _run(cmd, timeout=7200)
    if out_path.exists():
        out_path.unlink()
    tmp.rename(out_path)
    print(f"[DONE ] {out_path.relative_to(proj)} = {_fmt(probe_duration(out_path))}")
    return out_path


# ---------------------------------------------------------------- check

def _decode_mono(path, sr=AUDIO_SR, start=None, dur=None):
    import numpy as np
    cmd = ["ffmpeg", "-v", "error", "-nostdin"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(path)]
    if dur is not None:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    p = subprocess.run(cmd, capture_output=True, timeout=1800)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode(errors="replace")[-1000:])
    return np.frombuffer(p.stdout, dtype=np.float32).astype("float64")


def measure_audio_lag(final_path, ref_path, ref_dur, expected, sr=AUDIO_SR, win_s=AUDIO_WIN_S):
    """final 声轨相对 ref(final_audio)的滞后,取 ref 首/中/尾三段有能量的窗口各测一次。
    返回 [{"ref_t": t, "lag_s": lag, "ncc": 归一化相关峰值}]。"""
    import numpy as np
    from scipy.signal import correlate
    full = _decode_mono(final_path, sr)
    if full.size < sr:
        raise RuntimeError("成片声轨为空或过短")
    full = full - full.mean()
    win = int(win_s * sr)
    anchors = sorted({round(a, 3) for a in
                      (0.0, max(0.0, ref_dur / 2 - win_s / 2), max(0.0, ref_dur - win_s))})
    results = []
    for t0 in anchors:
        # 窗口内能量太低(静音段)往后挪,最多试 6 次
        seg = None
        for k in range(6):
            t = min(t0 + k * win_s, max(0.0, ref_dur - win_s))
            cand = _decode_mono(ref_path, sr, start=t, dur=win_s)
            if cand.size >= win // 2 and np.sqrt(np.mean(cand ** 2)) > 1e-3:
                seg, t0 = cand, t
                break
            if t >= ref_dur - win_s:
                break
        if seg is None:
            continue
        seg = seg - seg.mean()
        # 只在合理范围内搜索:final 中 [t0-2, t0+expected+2+尾包装] 都可能,直接全局搜
        c = correlate(full, seg, mode="valid", method="fft")
        # 归一化:除以滑动能量(累计和 O(N)),避免响段虚高
        cs = np.concatenate(([0.0], np.cumsum(full ** 2)))
        e_full = np.sqrt(np.maximum(cs[seg.size:] - cs[:-seg.size], 0.0)) + 1e-9
        ncc = c / (e_full * np.sqrt(np.sum(seg ** 2)) + 1e-9)
        i = int(np.argmax(ncc))
        lag = i / sr - t0
        results.append({"ref_t": round(t0, 3), "lag_s": round(lag, 4), "ncc": round(float(ncc[i]), 3)})
    return results


def do_check(proj, ep, segs, notes, final_path, tol_ms=80, write=True):
    checks = []

    def rec(name, ok, detail="", warn=False):
        tag = "WARN" if warn else ("PASS" if ok else "FAIL")
        checks.append({"name": name, "status": tag, "detail": detail})
        print(f"[CHECK] {name}: {tag}" + (f"  {detail}" if detail else ""))
        return ok or warn

    offs, total = offsets_of(segs)
    intro_s = offs["cut"]
    ed = proj / "edit" / ep
    print_layout(proj, segs, notes)
    cut_seg = next(s for s in segs if s["name"] == "cut")
    tm_ops = ((cut_seg.get("timemap") or {}).get("ops")) or []
    cue_fn = timemap.map_fn(tm_ops, intro_s)

    # 0. placement 声明 vs 实测(WARN)
    mism = [s for s in segs if s["declared"] is not None and abs(s["dur"] - s["declared"]) > PLACEMENT_TOL_S]
    rec("placement_declared_match", not mism,
        "; ".join(f"{s['name']} 声明 {s['declared']}s 实测 {s['dur']:.3f}s" for s in mism)
        if mism else "声明时长与实测一致(或未声明)", warn=bool(mism))

    # 1. 成片总时长
    if final_path is None:
        rec("final_duration_layout", False, f"找不到成片 {ed}/final.mp4(或 *final*.mp4)")
        fdur = None
    else:
        fdur = probe_duration(final_path)
        d = fdur - total
        rec("final_duration_layout", abs(d) <= DUR_TOL_S,
            f"{final_path.name} {fdur:.3f}s vs Σ段 {total:.3f}s(Δ{d:+.3f}s;"
            + ("片头/片尾漏拼或多拼、或正片被 -shortest 截断" if abs(d) > DUR_TOL_S else "一致") + ")")

    # 1b. 上下黑边:开启时成片分辨率须 = 版式画布(改了设置没重新 assemble 即 FAIL);关闭时成片画幅与正片不同只提醒
    if final_path is not None:
        lb_aspect = resolve_letterbox(_settings(proj))
        fv = _probe_streams(final_path)["video"] or {}
        cv = cut_seg["streams"]["video"] or {}
        fw, fh, cw, ch = (int(x.get(k) or 0) for x in (fv, cv) for k in ("width", "height"))
        if lb_aspect:
            lb = letterbox_plan(proj, segs)
            want = lb["canvas"] if lb else [cw, ch]
            rec("letterbox_applied", [fw, fh] == want,
                f"{final_path.name} {fw}x{fh};最终输出画幅 {lb_aspect} 应为 {want[0]}x{want[1]}"
                + ("" if [fw, fh] == want else ",重新 assemble")
                + ("" if lb else "(正片画幅已与最终输出画幅一致,不加黑边)"))
        elif fw and fh and cw and ch and abs((fw / fh) / (cw / ch) - 1) >= 0.01:
            rec("letterbox_applied", True, f"上下黑边已关闭,但 {final_path.name} {fw}x{fh} 与正片 {cw}x{ch} 画幅不同;"
                                           "若是之前加过黑边的成片请重新 assemble", warn=True)

    # 2. 字幕
    base_srt, fin_srt = ed / "subtitles.srt", ed / "subtitles_final.srt"
    if base_srt.is_file():
        if not fin_srt.is_file():
            rec("subtitles_final_present", False, "有 subtitles.srt 但缺 subtitles_final.srt(先跑 shift)")
        else:
            rec("subtitles_final_present", True, fin_srt.name)
            _check_cues("subtitle_offset_all_cues", rec, parse_srt(base_srt), parse_srt(fin_srt), intro_s, cue_fn if tm_ops else None)
            if fdur is not None:
                fin = parse_srt(fin_srt)
                last = max((e for _, e in fin), default=0.0)
                rec("subtitle_within_final", last <= fdur + 0.5,
                    f"末条 cue 止于 {last:.3f}s,成片 {fdur:.3f}s")
    else:
        rec("subtitles_final_present", True, "无 subtitles.srt(subtitle 未交付,跳过字幕项)", warn=True)
    base_ass, fin_ass = ed / "subtitles.ass", ed / "subtitles_final.ass"
    if base_ass.is_file():
        if not fin_ass.is_file():
            rec("subtitle_ass_offset", False, "有 subtitles.ass 但缺 subtitles_final.ass")
        else:
            _check_cues("subtitle_ass_offset", rec, parse_ass(base_ass), parse_ass(fin_ass), intro_s, cue_fn if tm_ops else None)

    # 3. 声轨偏移实测
    cut = next(s for s in segs if s["name"] == "cut")
    ref = cut.get("audio")
    if final_path is None:
        rec("audio_offset_measured", False, "无成片,无法实测")
    elif ref is None:
        rec("audio_offset_measured", True, "正片用自带音轨,无外挂 final_audio 可对照(跳过)", warn=True)
    else:
        if tm_ops:
            print(f"[INFO ] 声轨对照基准 = 按 timemap 重映射后的 {Path(ref).name}(源 {Path(cut.get('audio_src') or ref).name});期望滞后仍 = 片头")
        try:
            res = measure_audio_lag(final_path, ref, cut["a_dur"], intro_s)
        except Exception as e:  # noqa: BLE001
            res = []
            rec("audio_offset_measured", False, f"互相关失败:{e}")
        if res:
            tol = tol_ms / 1000.0
            bad = [r for r in res if abs(r["lag_s"] - intro_s) > tol]
            weak = [r for r in res if r["ncc"] < 0.2]
            lags = ", ".join(f"ref@{r['ref_t']}s→lag {r['lag_s']:+.3f}s(ncc {r['ncc']})" for r in res)
            if bad:
                spread = max(r["lag_s"] for r in res) - min(r["lag_s"] for r in res)
                hint = ("三窗口滞后不一致 → 正片段内有累计漂移(变速/掉帧/-shortest)"
                        if spread > tol else
                        f"成片声轨整体偏 {res[0]['lag_s'] - intro_s:+.3f}s → 拼片头后声轨没有随之后移"
                        "(或用了旧 final_audio)")
                rec("audio_offset_measured", False, f"期望 lag={intro_s:.3f}s;{lags};{hint}")
            elif weak:
                rec("audio_offset_measured", True, f"滞后吻合但相关峰弱({lags}),请人工抽听", warn=True)
            else:
                rec("audio_offset_measured", True, f"期望 lag={intro_s:.3f}s(±{tol_ms}ms);{lags}")

    burn = (_settings(proj).get("output") or {}).get("subtitle_burn_in")
    if burn:
        print("[INFO ] 项目开启字幕烧录:烧录须用 subtitles_final.*,请抽帧核对首句出现时刻 ≈ "
              f"{(parse_srt(fin_srt)[0][0] if fin_srt.is_file() and parse_srt(fin_srt) else 0):.2f}s")

    ok = all(c["status"] != "FAIL" for c in checks)
    print(f"[RESULT] intro_offset_ok: {'PASS' if ok else 'FAIL'}")
    if write:
        write_ledger(proj, ep, segs, {"check": {
            "result": "PASS" if ok else "FAIL",
            "final": str(final_path.relative_to(proj)) if final_path else None,
            "final_duration_s": round(fdur, 6) if fdur else None,
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "items": checks}})
    return ok


def _check_cues(name, rec, base, fin, intro_s, fn=None):
    """fn:正片基准 → 成片基准的映射(含片头);缺省 = +intro_s。"""
    if not base:
        rec(name, True, "基准字幕无 cue", warn=True)
        return
    if len(base) != len(fin):
        rec(name, False, f"cue 条数不一致:基准 {len(base)} vs final {len(fin)}")
        return
    mapped = fn is not None
    fn = fn or (lambda t: t + intro_s)
    worst = 0.0
    worst_i = None
    for i, ((a0, a1), (b0, b1)) in enumerate(zip(base, fin)):
        d = max(abs(b0 - fn(a0)), abs(b1 - fn(a1)))
        if d > worst:
            worst, worst_i = d, i + 1
    ok = worst <= SUB_TOL_S
    detail = f"{len(base)} 条,期望整体 +{intro_s:.3f}s" + ("(另按 timemap 逐条平移)" if mapped else "") + f",最大偏差 {worst * 1000:.0f}ms"
    if not ok:
        detail += f"(cue #{worst_i};若 ≈{intro_s * 1000:.0f}ms 即未平移,直接拷贝了正片基准 SRT)"
    rec(name, ok, detail)


# ---------------------------------------------------------------- CLI

def main(argv=None):
    def configure(ap):
        ap.add_argument("cmd", choices=("probe", "shift", "assemble", "check"))
        ap.add_argument("--layout", default=",".join(SEGMENTS),
                        help="段序,默认 intro,cut,outro,teaser(禁用/缺失的段自动跳过)")
        ap.add_argument("--cut", default=None, help="正片文件(默认按 timemap_layers.resolve_cut:台账 final_layout.json 记录的正片优先,"
                             "有后期拼片时取 cut_post_v2/cut_post,否则最新 cut_v*;与花字 render_captions 同口径)")
        ap.add_argument("--audio", default=None,
                        help="外挂声轨(默认 assets/audio/final/epNN.wav;'none' = 用正片自带音轨)")
        ap.add_argument("--final", default=None, help="check:成片文件(默认 final.mp4,退回最新 *final*.mp4)")
        ap.add_argument("--out", default="final.mp4", help="assemble:输出文件名(须含 final)")
        ap.add_argument("--tol-ms", type=int, default=80, help="check:声轨偏移容差,默认 80ms")
        ap.add_argument("--crf", type=int, default=18)
        ap.add_argument("--preset", default="medium")
        ap.add_argument("--force", action="store_true", help="assemble:av 项目也允许重编码封装")

    args, proj = parse_args(__doc__.splitlines()[0], configure=configure, argv=argv)
    require_tools("ffprobe")
    layout = [x.strip() for x in args.layout.split(",") if x.strip()]
    notes = []
    segs = resolve_layout(proj, args.ep, layout, args.cut, args.audio, notes)
    has_pack = any(s["name"] != "cut" for s in segs)

    if args.cmd == "probe":
        print_layout(proj, segs, notes)
        p, _ = write_ledger(proj, args.ep, segs)
        print(f"[DONE ] 台账 {p.relative_to(proj)}")
        return 0

    cut_ops = ((next(s for s in segs if s["name"] == "cut").get("timemap") or {}).get("ops")) or []
    if args.cmd == "shift":
        print_layout(proj, segs, notes)
        do_shift(proj, args.ep, offsets_of(segs)[0]["cut"], cut_ops)
        p, _ = write_ledger(proj, args.ep, segs)
        print(f"[DONE ] 台账 {p.relative_to(proj)}")
        return 0

    if args.cmd == "assemble":
        if "final" not in args.out:
            raise SystemExit("[FAIL] 成片文件名必须含 final(WORKFLOW G9 命名红线)")
        if _is_av_project(proj) and has_pack and not args.force:
            raise SystemExit("[FAIL] av(音频锁定)项目要求母带零重编码,与片头/片尾接入互斥;"
                             "请关闭包装或 --force(将破坏 final_audio_no_transcode 机检)")
        print_layout(proj, segs, notes)
        out = proj / "edit" / args.ep / args.out
        do_assemble(proj, args.ep, segs, out, crf=args.crf, preset=args.preset)
        do_shift(proj, args.ep, offsets_of(segs)[0]["cut"], cut_ops)
        ok = do_check(proj, args.ep, segs, [], out, tol_ms=args.tol_ms)
        return 0 if ok else 1

    final_path = find_final(proj, args.ep, args.final)
    ok = do_check(proj, args.ep, segs, notes, final_path, tol_ms=args.tol_ms)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
