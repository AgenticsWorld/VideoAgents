#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dub_group.py — 对白后期配音(输出设置「对白配音=后期配音」,workflow p7-dub,09-audio/voice-generation 执行)。

前提:项目 output.dialogue_voice == "dubbing"(视频原声模式严禁调用——§8A 红线:TTS 不进成片对白)。
组视频仍按对白组常规生成(prompt 照写 `{}` 台词、挂 voiceprint 音色锚),人物开口表演由模型原生生成;
本脚本在 p7-video 交付后,把该组 clip 的**对白轨**替换成按角色声线合成、按画面开口位置贴合的 TTS(2026-10-03 改版):

  0. 去人声:原生轨先过本机人声分离模型(modules/audio_separation.py,MDX-Net ONNX,同后期页「去人声」)拆成
     bed(音效 / 环境声 / 配乐)+ voice(模型原生人声);成片底床只用 bed,原生人声按 --keep-native-voice-db
     保留(默认 -60 = 完全去掉)。模型不可用(缺 onnxruntime / 下载失败)自动回落旧做法:原生轨在开口时段压低
     -26dB(manifest vocal_removal.status=fallback_duck,机检 WARN);
  1. 台词事实源:directing/epNN/shot_list.json 组内各镜 dialogue_lines(speaker/text,冻结版,一字不改);
  2. 开口时段:在 voice stem 上按自相关基频找有声段(modules/voice_activity.py,与 dialogue_audible 同口径;
     --detector silence 退回 silencedetect),再按**镜次时窗**对位——先用 meta boundary_map(无则按 shot_list
     时长累计)把每句台词限定到它所在镜的时窗 ±--pad 秒内,镜内再按台词顺序对位(区间多则按最小间隙合并、
     少则按台词字数比例拆分);某镜窗内找不到有声段 → 整组退回全局顺序对位并在 manifest 记 notes。
     自动检测不可靠时 Agent 目检/听审后用 --segments <json> 手工给定 [{"line":0,"start":1.2,"end":4.0},...];
  3. 逐句 TTS:按项目级 assets/audio/voice/casting.json 该角色×形态条目(tts_voice/speed;形态按组
     audio_refs 样本文件名 <CHAR>_<variant>_voiceprint → 声纹卡章节范围 → 唯一已登记形态,modules/voice_variants.py;
     可 --variant CHAR=variant 覆盖),走 modules/genmedia.generate_tts;
  4. 口型贴合(起点优先):每句 TTS 起点对齐该句开口起点;时长按比例重合成(--speed,受 --speed-min/--speed-max
     约束,默认 0.75–1.25)+ atempo 微调(±10%)尽量贴开口时长,但**只有撞到下一句开口起点 / clip 末尾才算 overflow**
     (回派 dialogue-rewrite 改短或整组重生成,不硬塞);没撞上的句子可以比画面开口长一点(manifest 记 loose_fit);
  5. 混轨:bed 底床(回落时为压低后的原生轨)叠上逐句 TTS(电平对齐原生开口段),画面流 -c:v copy 原样封装回
     assets/clips/epNN/grpNNN.mp4(时长/fps/分辨率不变);原生轨首次替换前备份到 assets/clips/epNN/grpNNN.native_audio.wav,
     重跑时以备份为源(幂等);bed 另存 assets/clips/epNN/grpNNN.bed.wav 供后期页「原声」轨单独播放。

产物:assets/audio/voice/epNN/dub/grpNNN/{lNN_<CHAR>.mp3, lNN_<CHAR>.fit.wav, native_voice.wav, dub_manifest.json}
      + 组 clip 新版本(meta.json 追加 dialogue_voice 段)。manifest 的 checks 供机检:
      dub_lines_text_match_frozen_script / dub_speaker_casting_bound / dub_fit_ok / clip_duration_unchanged / vocal_removal_ok。
混音:mix_basis.py 把 dub_manifest 指纹盖进混音清单,重配音后未重混 = mix_basis_current FAIL;后期页若已有早于本次
      配音的版本,采纳版本不含配音,manifest 记 post_versions_predate_dub 并 WARN。

用法:
  python3 code/dub_group.py --project <slug> --ep ep01 --group grp012            # 全流程
  python3 code/dub_group.py --project <slug> --ep ep01 --group grp012 --detect-only   # 只分离 + 检测,打印开口时段供人工核对
  python3 code/dub_group.py ... --segments dub/grp012/segments.json                   # 手工时段
  python3 code/dub_group.py ... --dry-run                                            # 分离 + 检测,不调 TTS、不改 clip
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from _common import parse_args, DATA_DIR  # noqa: F401  (副作用:modules/ 入 sys.path)

_SIL_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SIL_END = re.compile(r"silence_end:\s*(-?[\d.]+)")
_MEAN_VOL = re.compile(r"mean_volume:\s*(-?[\d.]+)\s*dB")

FALLBACK_DUCK_DB = -26.0        # 分离模型不可用时的旧做法:开口时段压低原生轨
COLLISION_TOL_S = 0.05          # 贴合后与下一句起点 / clip 末尾的允许重叠
LOOSE_FIT = 0.10                # fit_ratio 偏离 1 超过此值记 loose_fit(只提示,不 FAIL)


# ---------------------------------------------------------------- 外部工具

def _log(msg: str) -> None:
    """进度 / 警告一律走 stderr,stdout 只留机器可读输出(--detect-only / --dry-run 的 JSON、末尾 checks)。"""
    print(f"[dub] {msg}", file=sys.stderr, flush=True)


def _run(cmd, timeout=600):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _require_tools():
    missing = [t for t in ("ffmpeg", "ffprobe") if shutil.which(t) is None]
    if missing:
        raise SystemExit(f"缺少外部工具 {missing}(macOS: brew install ffmpeg)")


def probe_duration(path) -> float:
    rc, out = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                    "-of", "csv=p=0", str(path)])
    try:
        return float(out.strip().splitlines()[-1])
    except Exception:
        raise SystemExit(f"ffprobe 读不到时长:{path}\n{out}")


def probe_stream(path, kind: str) -> dict:
    rc, out = _run(["ffprobe", "-v", "error", "-select_streams", kind, "-show_entries",
                    "stream=codec_name,width,height,r_frame_rate,sample_rate,channels",
                    "-of", "json", str(path)])
    try:
        return (json.loads(out).get("streams") or [{}])[0]
    except Exception:
        return {}


def mean_volume_db(path, start=None, end=None) -> float | None:
    cmd = ["ffmpeg", "-hide_banner", "-nostats"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    if end is not None and start is not None:
        cmd += ["-t", f"{max(0.05, end - start):.3f}"]
    cmd += ["-i", str(path), "-vn", "-af", "volumedetect", "-f", "null", "-"]
    rc, out = _run(cmd)
    m = _MEAN_VOL.search(out)
    return float(m.group(1)) if m else None


# ---------------------------------------------------------------- ⓪ 去人声(人声分离)

def separate_native(native_src: Path, bed_path: Path, voice_path: Path, keep_db: float, log=print) -> dict:
    """原生轨 → bed(去人声底床,含 keep_db 的残留人声)+ voice(人声 stem,只用于开口检测)。
    模型不可用返回 status=fallback_duck(调用方退回压低法),不抛异常。"""
    try:
        import audio_separation as asep
    except Exception as e:  # noqa: BLE001
        return {"status": "fallback_duck", "reason": f"audio_separation 不可用:{e}"}
    try:
        sess = asep.load_session(asep.ensure_model(DATA_DIR, log))
        mix = asep.decode(native_src)
        if mix.shape[1] < asep.SR // 10:
            return {"status": "fallback_duck", "reason": "原生轨太短或为空"}
        bed, voice = asep.separate(sess, mix)
        g = asep.keep_gain(keep_db)
        asep.write_wav(bed_path, bed + g * voice)
        asep.write_wav(voice_path, voice)
        return {"status": "removed", "keep_db": keep_db, "model": asep.MODEL_FILE,
                "mix_db": asep.level_db(mix), "voice_db": asep.level_db(voice), "bed_db": asep.level_db(bed),
                "bed_file": None, "voice_file": None}
    except Exception as e:  # noqa: BLE001  (SeparationError / onnxruntime 缺失 / 下载失败)
        return {"status": "fallback_duck", "reason": str(e)[-300:]}


# ---------------------------------------------------------------- ② 开口时段检测与对位(纯函数,可单测)

def detect_speech(path, total: float, noise_db: float = -30.0, min_silence: float = 0.25,
                  min_speech: float = 0.30, merge_gap: float = 0.20) -> list[list[float]]:
    """silencedetect 的补集 = 语音区间;短间隙合并、过短区间剔除(--detector silence)。"""
    rc, out = _run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn",
                    "-af", f"silencedetect=noise={noise_db}dB:d={min_silence}", "-f", "null", "-"])
    starts = [float(x) for x in _SIL_START.findall(out)]
    ends = [float(x) for x in _SIL_END.findall(out)]
    silences = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else total
        silences.append((max(0.0, s), min(total, e)))
    return speech_from_silences(silences, total, min_speech, merge_gap)


def speech_from_silences(silences, total, min_speech=0.30, merge_gap=0.20) -> list[list[float]]:
    cur = 0.0
    speech = []
    for s, e in sorted(silences):
        if s > cur:
            speech.append([cur, s])
        cur = max(cur, e)
    if cur < total:
        speech.append([cur, total])
    merged: list[list[float]] = []
    for seg in speech:
        if merged and seg[0] - merged[-1][1] <= merge_gap:
            merged[-1][1] = seg[1]
        else:
            merged.append(list(seg))
    return [[round(a, 3), round(b, 3)] for a, b in merged if b - a >= min_speech]


def detect_voiced(path, merge_gap: float = 0.20, min_speech: float = 0.30) -> list[list[float]]:
    """人声 stem(或原生轨)上的有声段:自相关基频 + 能量(modules/voice_activity.py,--detector voiced,默认)。"""
    from voice_activity import voiced_runs
    return voiced_runs(Path(path), merge_gap=merge_gap, min_run=min_speech)


def _text_weight(t: str) -> float:
    """台词长度权重:CJK 逐字计 1、其余按词计 1(粗略,只用于拆分/对位)。"""
    cjk = len(re.findall(r"[㐀-鿿぀-ヿ가-힯]", t))
    words = len(re.findall(r"[A-Za-z0-9À-ɏЀ-ӿ؀-ۿ']+", t))
    return float(max(1, cjk + words))


def align_segments(segments: list[list[float]], lines: list[dict]) -> list[list[float]]:
    """把 N 个语音区间对位到 M 句台词(按顺序):N==M 一一对应;N>M 按最小间隙合并相邻区间;
    N<M 把最长区间按台词权重比例拆分,直到数量相等。"""
    segs = [list(s) for s in segments]
    m = len(lines)
    if m == 0:
        return []
    if not segs:
        raise ValueError("未检测到任何语音区间;请调 --noise-db(如 -35)或用 --segments 手工给定")
    while len(segs) > m:
        gaps = [(segs[i + 1][0] - segs[i][1], i) for i in range(len(segs) - 1)]
        _, i = min(gaps)
        segs[i][1] = segs[i + 1][1]
        del segs[i + 1]
    while len(segs) < m:
        # 拆最长区间:该区间当前对应的连续台词按权重切两半
        i = max(range(len(segs)), key=lambda k: segs[k][1] - segs[k][0])
        li = min(i, m - 2)
        w1 = _text_weight(lines[li]["text"])
        w2 = _text_weight(lines[li + 1]["text"])
        a, b = segs[i]
        cut = a + (b - a) * w1 / (w1 + w2)
        segs[i:i + 1] = [[a, cut], [cut, b]]
    return [[round(a, 3), round(b, 3)] for a, b in segs]


def shot_windows(group: dict, shots: dict, meta: dict) -> dict:
    """组内各镜在 clip 里的时窗 shot_id → (start, end):优先 meta boundary_map(出片时实测切点),
    缺的镜按 shot_list duration_s 顺序累计(与 check_dialogue_audible 同口径)。"""
    bmap = {}
    for b in (meta or {}).get("boundary_map") or []:
        try:
            bmap[b.get("shot_id")] = (float(b.get("start_s")), float(b.get("end_s")))
        except (TypeError, ValueError):
            continue
    out, t = {}, 0.0
    for sid in group.get("shots") or []:
        dur = float((shots.get(sid) or {}).get("duration_s") or 0)
        out[sid] = bmap.get(sid, (t, t + dur))
        t += dur
    return out


def assign_by_shots(runs: list[list[float]], lines: list[dict], windows: dict, pad: float,
                    total: float) -> tuple[list[list[float]], list[str]]:
    """按镜次时窗把有声段对位到台词:逐镜取「窗 ±pad 内、且在前一镜已用区间之后」的有声段,镜内再 align_segments;
    任一镜窗内无有声段 → 退回全局顺序对位(notes 记原因)。返回 (segs, notes)。"""
    notes: list[str] = []
    if not lines:
        return [], notes
    if not runs:
        raise ValueError("未检测到任何有声段;请换 --detector silence / 调阈值,或用 --segments 手工给定")
    by_shot: list[tuple[str, list[int]]] = []
    for i, ln in enumerate(lines):
        if by_shot and by_shot[-1][0] == ln["shot_id"]:
            by_shot[-1][1].append(i)
        else:
            by_shot.append((ln["shot_id"], [i]))
    segs: list = [None] * len(lines)
    cursor = 0.0
    for sid, idxs in by_shot:
        a, b = windows.get(sid) or (0.0, total)
        lo, hi = max(cursor, a - pad), min(total, b + pad)
        cands = [[max(r[0], lo), r[1]] for r in runs if r[1] > lo and r[0] < hi and r[1] - max(r[0], lo) >= 0.1]
        if not cands:
            notes.append(f"{sid} 时窗 {a:.2f}–{b:.2f}s(±{pad:g}s)内无有声段")
            continue
        sub = align_segments(cands, [lines[i] for i in idxs])
        for i, s in zip(idxs, sub):
            segs[i] = s
        cursor = sub[-1][1]
    if any(s is None for s in segs):
        notes.append("有台词所在镜窗内找不到有声段,整组退回全局顺序对位(请 --detect-only 核对或 --segments 手工给定)")
        segs = align_segments(runs, lines)
    return segs, notes


def plan_fit(tts_dur: float, target: float, base_speed: float,
             speed_min: float, speed_max: float, tempo_tol: float = 0.10) -> dict:
    """给定首版 TTS 时长与目标时段,算重合成语速与残差 atempo。返回 {speed, atempo, overflow}(overflow 只是「贴不上开口
    时长」的提示;是否真的装不下由 fit_overflow 按下一句起点判)。speed>1 更快;atempo>1 更快(时长变短)。"""
    if target <= 0:
        return {"speed": base_speed, "atempo": 1.0, "overflow": True}
    ratio = tts_dur / target                       # >1 说明 TTS 比开口时段长
    want = base_speed * ratio
    speed = min(speed_max, max(speed_min, want))
    est = tts_dur * base_speed / speed             # 重合成后估计时长
    resid = est / target                           # 仍需 atempo 的倍率
    atempo = min(1.0 + tempo_tol, max(1.0 - tempo_tol, resid))
    overflow = (est / atempo) > target * 1.05      # 用尽语速与微调仍贴不上开口时长
    return {"speed": round(speed, 3), "atempo": round(atempo, 4), "overflow": overflow,
            "ratio_first": round(ratio, 3)}


def fit_room(segs: list[list[float]], i: int, total: float) -> float:
    """第 i 句从起点起可占用的最长时长:到下一句开口起点(或 clip 末尾)。起点优先 = 只要不撞下一句就不算 overflow。"""
    a = segs[i][0]
    nxt = segs[i + 1][0] if i + 1 < len(segs) else total
    return max(0.0, nxt - a)


def fit_overflow(fit_dur: float, room: float, tol: float = COLLISION_TOL_S) -> bool:
    return fit_dur > room + tol


# ---------------------------------------------------------------- 项目数据

def load_group(proj: Path, ep: str, gid: str):
    sl = json.loads((proj / "directing" / ep / "shot_list.json").read_text(encoding="utf-8"))
    groups = {g["group_id"]: g for g in sl.get("generation_groups", [])}
    if gid not in groups:
        raise SystemExit(f"shot_list 无组 {gid}")
    g = groups[gid]
    shots = {s["shot_id"]: s for s in sl.get("shots", [])}
    lines = []
    for sid in g.get("shots", []):
        idx = 0   # 镜内有台词句序号,与对白语音库(modules/dialogue_tts)的 (shot_id, idx) 口径一致
        for ln in (shots.get(sid) or {}).get("dialogue_lines") or []:
            # 规约键 text;兼容写成 line 的出稿(同 check_dialogue_fit / 预览接口)
            text = (ln.get("text") or ln.get("line") or "").strip()
            if not text:
                continue
            lines.append({"shot_id": sid, "idx": idx, "speaker": ln.get("speaker") or ln.get("character_id"),
                          "text": text, "est_duration_s": ln.get("est_duration_s"),
                          "emotion": ln.get("emotion") or ln.get("tone") or "",
                          "delivery": ln.get("delivery")})   # 台词演法(modules/dialogue_direction.py)
            idx += 1
    return g, lines, shots


def load_casting(proj: Path) -> dict:
    p = proj / "assets" / "audio" / "voice" / "casting.json"
    if not p.is_file():
        raise SystemExit(f"缺 {p}(选角注册表,合成前必查;缺条目先登记,SOUL.md §职责1)")
    d = json.loads(p.read_text(encoding="utf-8"))
    out = {}
    # 规约键 castings;voice-generation 实际落表用过 entries,两者都认(与 modules/dialogue_tts.load_casting 同口径)
    for c in d.get("castings") or d.get("entries") or []:
        if isinstance(c, dict) and (c.get("character_id") or c.get("char_id")):
            out[(c.get("character_id") or c.get("char_id"), c.get("variant") or "default")] = c
    return out


def infer_variants(proj: Path, ep: str, gid: str, speakers, casting: dict | None = None) -> dict:
    """本组各说话人的嗓音形态(modules/voice_variants.py,与对白语音库同一口径):组 prompt audio_refs 样本名
    → 声纹卡章节范围 → 本集其它组 → 唯一已登记形态 → default。判不出/该形态未登记的人物不进返回表,
    另列在 '_problems'(调用方报错,由 --variant 显式指定)。"""
    from modules.voice_variants import Resolver
    resolver = Resolver(proj, ep, casting)
    res, problems = {}, {}
    for ch in dict.fromkeys(speakers):
        vr = resolver.resolve(gid, ch)
        if vr["problem"]:
            problems[ch] = vr["problem"]
        else:
            res[ch] = vr["variant"]
        if vr["warning"]:
            _log(f"WARN {vr['warning']}")
    res["_problems"] = problems
    return res


def project_dialogue_mode(proj: Path) -> str:
    try:
        st = json.loads((proj / "settings.json").read_text(encoding="utf-8"))
        return ((st.get("output") or {}).get("dialogue_voice")) or "native"
    except Exception:
        return "native"


def post_versions_before(proj: Path, ep: str, gid: str) -> list[int]:
    """后期页已登记的本组版本号(它们都从配音前的母本派生,采纳版本不含本次配音)。"""
    try:
        import post_plan as pp
        plan = pp.load_plan(proj, ep)
        return sorted(int(v.get("v") or 0) for v in pp.group_versions(plan, gid) if not v.get("cleaned"))
    except Exception:
        return []


# ---------------------------------------------------------------- 主流程

def main(argv=None):
    def conf(ap: argparse.ArgumentParser):
        ap.add_argument("--group", required=True, help="组号,如 grp012")
        ap.add_argument("--segments", default=None,
                        help="手工开口时段 JSON:[{\"line\":0,\"start\":1.2,\"end\":4.0},...](line 为组内台词序号)")
        ap.add_argument("--variant", action="append", default=[],
                        help="覆盖说话人形态,CHAR-0003=child(可多次)")
        ap.add_argument("--detector", choices=("voiced", "silence"), default="voiced",
                        help="开口检测:voiced=人声 stem 上找有声段(默认);silence=silencedetect 补集(旧法)")
        ap.add_argument("--pad", type=float, default=1.0, help="镜次时窗前后放宽秒数(默认 1.0)")
        ap.add_argument("--noise-db", type=float, default=-30.0, help="--detector silence 的噪声门限 dB(默认 -30)")
        ap.add_argument("--min-silence", type=float, default=0.25, help="--detector silence 判为静音的最短时长 s(默认 0.25)")
        ap.add_argument("--speed-min", type=float, default=0.75)
        ap.add_argument("--speed-max", type=float, default=1.25)
        ap.add_argument("--no-separation", action="store_true", help="不做人声分离,直接用旧的开口时段压低法")
        ap.add_argument("--keep-native-voice-db", type=float, default=-60.0,
                        help="去人声后原生人声的保留电平 dB(默认 -60 = 完全去掉)")
        ap.add_argument("--duck-db", type=float, default=None,
                        help="TTS 时段底床压低量 dB(默认:已去人声 0,回落压低法 -26)")
        ap.add_argument("--resplit", action="store_true", help="忽略已有的分离结果重新分离")
        ap.add_argument("--detect-only", action="store_true", help="只分离 + 检测并打印开口时段,不合成不改 clip")
        ap.add_argument("--dry-run", action="store_true", help="分离 + 检测,不调 TTS、不改 clip,只打印计划")
        ap.add_argument("--force-native-mode", action="store_true",
                        help="项目未设 dubbing 也执行(仅验证/对拍用;正式流程禁用)")
    args, proj = parse_args("对白后期配音:去人声后按开口位置用角色声线 TTS 替换组 clip 对白轨", configure=conf, argv=argv)
    _require_tools()
    ep, gid = args.ep, args.group
    mode = project_dialogue_mode(proj)
    if mode != "dubbing" and not args.force_native_mode:
        raise SystemExit(f"项目 output.dialogue_voice={mode}(视频原声):TTS 严禁进成片对白(§8A 红线),不执行;"
                         "如需后期配音请在「输出设置→对白配音」改为后期配音")
    plan_only = args.dry_run or args.detect_only

    clip = proj / "assets" / "clips" / ep / f"{gid}.mp4"
    if not clip.is_file():
        raise SystemExit(f"缺组 clip:{clip}")
    meta_p = clip.with_suffix(".meta.json")
    try:
        meta = json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.is_file() else {}
    except (ValueError, OSError):
        meta = {}
    dub_dir = proj / "assets" / "audio" / "voice" / ep / "dub" / gid
    dub_dir.mkdir(parents=True, exist_ok=True)
    native_bak = clip.with_name(f"{gid}.native_audio.wav")
    bed_path = clip.with_name(f"{gid}.bed.wav")
    voice_path = dub_dir / "native_voice.wav"
    if plan_only:   # 试跑不往 clips/ 落文件,分离结果放 dub 目录 _dryrun/
        bed_path = dub_dir / "_dryrun" / "bed.wav"
        voice_path = dub_dir / "_dryrun" / "native_voice.wav"
        bed_path.parent.mkdir(parents=True, exist_ok=True)

    group, lines, shots = load_group(proj, ep, gid)
    if not lines:
        raise SystemExit(f"{gid} 无 dialogue_lines(audio_plan={group.get('audio_plan')}),非对白组不派 p7-dub")
    total = probe_duration(clip)

    # 原生轨源:首跑从 clip 抽并备份,重跑用备份(幂等,不在配音轨上再配音)
    if native_bak.is_file():
        native_src = native_bak
    else:
        native_src = (dub_dir / "_dryrun" / "native_probe.wav") if plan_only else native_bak
        native_src.parent.mkdir(parents=True, exist_ok=True)
        rc, out = _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(clip),
                        "-vn", "-acodec", "pcm_s16le", str(native_src)])
        if rc:
            raise SystemExit(f"抽原生轨失败:{out}")

    # ⓪ 去人声
    sep_meta_p = dub_dir / ("_dryrun/separation.json" if plan_only else "separation.json")
    sep: dict
    if args.no_separation:
        sep = {"status": "disabled", "reason": "--no-separation"}
    else:
        prev = None
        try:
            prev = json.loads(sep_meta_p.read_text(encoding="utf-8")) if sep_meta_p.is_file() else None
        except (ValueError, OSError):
            prev = None
        if (not args.resplit and prev and prev.get("status") == "removed" and bed_path.is_file() and voice_path.is_file()
                and abs(float(prev.get("keep_db", -60)) - args.keep_native_voice_db) < 1e-6):
            sep = dict(prev, reused=True)
            _log(f"人声分离:复用上次结果 {bed_path.name}(--resplit 可重做)")
        else:
            _log(f"人声分离:{native_src.name} → bed + voice(原生人声保留 {args.keep_native_voice_db:g} dB)…")
            t0 = time.time()
            sep = separate_native(native_src, bed_path, voice_path, args.keep_native_voice_db,
                                  log=_log)
            sep["seconds"] = round(time.time() - t0, 1)
            if sep["status"] == "removed":
                sep_meta_p.write_text(json.dumps(sep, ensure_ascii=False, indent=2))
                _log(f"人声分离完成 {sep['seconds']}s:原声 {sep['mix_db']} dB / 人声 {sep['voice_db']} dB / 底床 {sep['bed_db']} dB")
            else:
                _log(f"⚠ 人声分离不可用({sep.get('reason')}),回落开口时段压低法")
    separated = sep["status"] == "removed"
    if separated:
        sep["bed_file"] = str(bed_path.relative_to(proj))
        sep["voice_file"] = str(voice_path.relative_to(proj))
    duck_db = args.duck_db if args.duck_db is not None else (0.0 if separated else FALLBACK_DUCK_DB)

    # ② 开口时段
    windows = shot_windows(group, shots, meta)
    detect_src = voice_path if separated else native_src
    notes: list[str] = []
    raw: list[list[float]] = []
    if args.segments:
        manual = json.loads(Path(args.segments).read_text(encoding="utf-8"))
        by_line = {int(m["line"]): [float(m["start"]), float(m["end"])] for m in manual}
        missing = [i for i in range(len(lines)) if i not in by_line]
        if missing:
            raise SystemExit(f"--segments 缺台词序号 {missing}(共 {len(lines)} 句)")
        segs = [by_line[i] for i in range(len(lines))]
        seg_source = "manual"
    else:
        if args.detector == "silence":
            raw = detect_speech(detect_src, total, args.noise_db, args.min_silence)
            seg_source = f"silencedetect(noise={args.noise_db}dB,d={args.min_silence})"
        else:
            raw = detect_voiced(detect_src)
            seg_source = "voiced(autocorr f0)"
        seg_source += " on " + ("voice_stem" if separated else "native") + f" + shot_windows(pad={args.pad:g})"
        try:
            segs, notes = assign_by_shots(raw, lines, windows, args.pad, total)
        except ValueError as e:
            raise SystemExit(str(e))
        for n in notes:
            _log(f"⚠ {n}")
    for i, (a, b) in enumerate(segs):
        if not (0 <= a < b <= total + 0.05):
            raise SystemExit(f"第 {i} 句时段非法 [{a},{b}](clip {total:.2f}s)")
    if args.detect_only:
        print(json.dumps({"group": gid, "clip_duration_s": round(total, 3), "vocal_removal": sep,
                          "segment_source": seg_source, "raw_voiced": raw, "notes": notes,
                          "shot_windows": {k: [round(v[0], 3), round(v[1], 3)] for k, v in windows.items()
                                           if any(ln["shot_id"] == k for ln in lines)},
                          "aligned": [{"line": i, "shot_id": ln["shot_id"], "speaker": ln["speaker"], "text": ln["text"],
                                       "start": s[0], "end": s[1], "room_s": round(fit_room(segs, i, total), 3)}
                                      for i, (ln, s) in enumerate(zip(lines, segs))]},
                         ensure_ascii=False, indent=2))
        return 0

    casting = load_casting(proj)
    variants = infer_variants(proj, ep, gid, [ln["speaker"] for ln in lines], casting)
    problems = variants.pop("_problems")
    for ov in args.variant:
        k, _, v = ov.partition("=")
        variants[k] = v or "default"
        problems.pop(k, None)
    if problems:
        raise SystemExit("说话人嗓音形态未定(dub_speaker_casting_bound):" + ";".join(problems.values())
                         + ";或用 --variant CHAR=形态 显式指定")
    try:
        from genmedia import get_config, generate_tts
        tts_cfg = get_config("tts")
        provider = tts_cfg.get("provider", "")
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"加载 genmedia 失败:{e}")
    # 语音模式(modules/voice_library.py):音色设计=不传 casting 的音色(genmedia 按声纹卡描述+项目 voiceprint
    # 样本当参考,逐句合成不漂音色);音色库=传 casting 登记的音色(渠道音色 ID / 本地音色库文件名)
    design_mode = tts_cfg.get("voice_mode") == "design"

    # 对白语音库(2026-09-13,输出设置「生成对白语音」):开着时先惰性同步本组各镜,首轮直接取库里自然语速音频,
    # 只有贴合需要改语速时才重新合成(重出仍落 dub 目录,不回写库)
    from modules import dialogue_tts as dt
    from modules import dialogue_direction as dd
    lib_audio = {}
    if dt.enabled(proj) and not args.dry_run:
        lib = dt.ensure(proj, ep, only_shots=set(group.get("shots") or []))
        lib_audio = dt.line_audio(proj, ep, lib) if lib else {}
        _log(f"对白语音库:本组 {sum(1 for ln in lines if (ln['shot_id'], ln['idx']) in lib_audio)}/{len(lines)} 句可直接取用")

    manifest = {"schema": "dub_manifest/v2", "group_id": gid, "episode": ep, "mode": "dubbing",
                "clip": str(clip.relative_to(proj)), "clip_duration_s": round(total, 3),
                "vocal_removal": sep, "duck_db": duck_db,
                "segment_source": seg_source, "segment_notes": notes, "fit_policy": "onset",
                "shot_windows": {k: [round(v[0], 3), round(v[1], 3)] for k, v in windows.items()},
                "tts_provider": provider, "speed_range": [args.speed_min, args.speed_max],
                "dialogue_tts_library": bool(lib_audio),
                "lines": [], "checks": {}}
    fitted = []
    for i, (ln, (a, b)) in enumerate(zip(lines, segs)):
        ch = ln["speaker"]
        var = variants.get(ch, "default")
        c = casting.get((ch, var)) or casting.get((ch, "default"))
        if not c:
            raise SystemExit(f"casting.json 无 {ch}/{var} 条目——先登记再合成(dub_speaker_casting_bound)")
        # casting 数字 speed 优先;描述文字(「常态(未传 --speed)」)视为未填 → 项目 output.dialogue_tts_speed(默认 1.0)
        base_speed = dt.num_speed(c.get("speed")) or dt.default_speed(proj)
        voice = "" if design_mode else (c.get("tts_voice") or "")
        target = b - a
        room = fit_room(segs, i, total)
        raw_mp3 = dub_dir / f"l{i:02d}_{ch}.mp3"
        fit_wav = dub_dir / f"l{i:02d}_{ch}.fit.wav"
        entry = {"line": i, "shot_id": ln["shot_id"], "speaker": ch, "variant": var,
                 "text": ln["text"], "casting_ref": f"casting.json#{ch}/{var}",
                 "tts_voice": c.get("tts_voice"), "tts_model": c.get("tts_model"),
                 "segment": {"start": a, "end": b, "duration_s": round(target, 3), "room_s": round(room, 3),
                             "shot_window": [round(windows.get(ln["shot_id"], (0, 0))[0], 3), round(windows.get(ln["shot_id"], (0, 0))[1], 3)]},
                 "tts_file": str(raw_mp3.relative_to(proj)), "fit_file": str(fit_wav.relative_to(proj))}
        if args.dry_run:
            entry.update({"status": "planned"})
            manifest["lines"].append(entry)
            continue
        # 台词演法:有演法的句子把演法带进合成;火山 Doubao-音频生成 1.0 能按目标时长出声,直接把开口时段当目标时长,
        # 不再靠改语速重合成去贴(其余渠道演法只当语气指令,仍走语速贴合)
        dv = dd.line_delivery({"text": ln["text"], "delivery": ln.get("delivery")}) or {}
        timed = bool(dv) and design_mode and provider == "volcengine"

        def synth(speed):
            generate_tts(ln["text"], str(raw_mp3), voice, None if timed else speed, ln.get("emotion") or "",
                         ch, var if var != "default" else "", str(proj),
                         dv.get("direction") or "", dv.get("scene") or "", round(target, 2) if timed else None)
        lib_entry = lib_audio.get((ln["shot_id"], ln["idx"]))
        if lib_entry and lib_entry.get("variant", "default") == var and abs(float(lib_entry.get("speed") or 1.0) - base_speed) <= 0.02:
            shutil.copyfile(lib_entry["path"], raw_mp3)
            entry["source"] = f"dialogue_tts:{lib_entry['file']}"
        else:
            synth(base_speed)
            entry["source"] = "tts"
        d1 = probe_duration(raw_mp3)
        fit = plan_fit(d1, target, base_speed, args.speed_min, args.speed_max)
        used_speed = base_speed
        if abs(fit["speed"] - base_speed) > 0.02:
            synth(fit["speed"])
            used_speed = base_speed if timed else fit["speed"]
            entry["source"] = "tts"
            d1 = probe_duration(raw_mp3)
        # 残差 atempo 按实测重算(重合成后的真实时长)
        resid = d1 / target if target > 0 else 1.0
        atempo = min(1.10, max(0.90, resid)) if abs(resid - 1) > 0.03 else 1.0
        # 电平对齐:TTS 均值 → 原生开口段均值(原生轨备份里的真人声段,不是去人声后的底床)
        nat_db = mean_volume_db(native_src, a, b)
        tts_db = mean_volume_db(raw_mp3)
        gain = 0.0 if nat_db is None or tts_db is None else max(-12.0, min(12.0, nat_db - tts_db))
        af = f"atempo={atempo:.4f},volume={gain:.2f}dB,aresample=48000"
        rc, out = _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(raw_mp3),
                        "-af", af, "-ac", "2", str(fit_wav)])
        if rc:
            raise SystemExit(f"贴合处理失败 l{i:02d}:{out}")
        d2 = probe_duration(fit_wav)
        overflow = fit_overflow(d2, room)                      # 起点优先:撞到下一句 / clip 末尾才算装不下
        ratio = round(d2 / target, 3) if target else None
        loose = ratio is not None and abs(ratio - 1) > LOOSE_FIT
        entry.update({"speed_used": used_speed, "atempo": round(atempo, 4), "gain_db": round(gain, 2),
                      "tts_duration_s": round(d1, 3), "fit_duration_s": round(d2, 3), "fit_ratio": ratio,
                      "loose_fit": loose, "overflow": overflow, "status": "overflow" if overflow else "ok"})
        manifest["lines"].append(entry)
        fitted.append((a, d2, fit_wav))
        _log(f"l{i:02d} {ch} seg={a:.2f}-{b:.2f}s room={room:.2f}s tts={d1:.2f}s speed={used_speed} atempo={atempo:.3f}"
              f" fit={d2:.2f}s{'  ⚠ overflow' if overflow else ('  ~loose' if loose else '')}")

    if args.dry_run:
        manifest["checks"] = {"dry_run": True, "vocal_removal_ok": separated}
        out_p = dub_dir / "dub_manifest.dryrun.json"      # 不覆盖正式 manifest
        out_p.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        _log(f"dry-run 计划已写 {out_p.relative_to(proj)}(未调 TTS、未改 clip)")
        return 0

    # ⑤ 混轨并封装回 clip:底床 = bed(已去人声)或压低后的原生轨;TTS 实际占用时段压低底床(duck_db=0 即不压)
    base_src = bed_path if separated else native_bak
    duck = ""
    if duck_db:
        duck = "".join(f",volume=enable='between(t,{a:.3f},{a + d:.3f})':volume={duck_db}dB" for a, d, _ in fitted)
    fc = [f"[0:a]aresample=48000,aformat=channel_layouts=stereo{duck}[nat]"]
    inputs = ["-i", str(base_src)]
    labels = ["[nat]"]
    for k, (a, _, w) in enumerate(fitted):
        inputs += ["-i", str(w)]
        ms = int(round(a * 1000))
        fc.append(f"[{k + 1}:a]adelay={ms}|{ms}[d{k}]")
        labels.append(f"[d{k}]")
    fc.append(f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:normalize=0,"
              f"alimiter=limit=0.95[out]")
    mixed = dub_dir / "_dubbed_track.wav"
    rc, out = _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
                    "-filter_complex", ";".join(fc), "-map", "[out]", "-ac", "2", str(mixed)])
    if rc:
        raise SystemExit(f"混轨失败:{out}\nfilter: {';'.join(fc)}")
    tmp_clip = clip.with_name(f"{gid}.dub_tmp.mp4")
    rc, out = _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(clip), "-i", str(mixed),
                    "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                    "-t", f"{total:.3f}", "-movflags", "+faststart", str(tmp_clip)])
    if rc:
        raise SystemExit(f"封装失败:{out}")
    new_total = probe_duration(tmp_clip)
    v0, v1 = probe_stream(clip, "v"), probe_stream(tmp_clip, "v")
    dur_ok = abs(new_total - total) <= 0.10
    stream_ok = (v0.get("width"), v0.get("height"), v0.get("r_frame_rate")) == \
                (v1.get("width"), v1.get("height"), v1.get("r_frame_rate"))
    if not (dur_ok and stream_ok):
        tmp_clip.unlink(missing_ok=True)
        raise SystemExit(f"封装后时长/画面流不一致(dur {total:.3f}→{new_total:.3f}, stream_ok={stream_ok}),已放弃替换")
    os.replace(tmp_clip, clip)
    mixed.unlink(missing_ok=True)

    manifest["native_audio_backup"] = str(native_bak.relative_to(proj))
    manifest["bed_file"] = sep.get("bed_file")
    manifest["dubbed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    manifest["post_versions_predate_dub"] = post_versions_before(proj, ep, gid)
    manifest["checks"] = {
        "dub_lines_text_match_frozen_script": True,        # 台词直接取自 shot_list 冻结版
        "dub_speaker_casting_bound": True,                # 缺条目已在合成前 SystemExit
        "dub_fit_ok": all(not e["overflow"] for e in manifest["lines"]),   # 起点对齐且不撞下一句
        "vocal_removal_ok": separated,                    # False = 回落压低法(原生人声残留),WARN
        "clip_duration_unchanged": dur_ok, "video_stream_unchanged": stream_ok,
        "overflow_lines": [e["line"] for e in manifest["lines"] if e["overflow"]],
        "loose_fit_lines": [e["line"] for e in manifest["lines"] if e.get("loose_fit")],
    }
    (dub_dir / "dub_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    try:
        meta = json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.is_file() else {}
        meta["dialogue_voice"] = {"mode": "dubbing", "manifest": str((dub_dir / "dub_manifest.json").relative_to(proj)),
                                  "native_audio_backup": manifest["native_audio_backup"], "bed_file": manifest["bed_file"],
                                  "vocal_removal": sep["status"],
                                  "dubbed_at": manifest["dubbed_at"], "checks": manifest["checks"]}
        meta_p.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    except Exception as e:  # noqa: BLE001
        _log(f"meta.json 更新失败(不影响 clip):{e}")
    print(json.dumps(manifest["checks"], ensure_ascii=False))
    if not separated:
        _log("⚠ 本次未去人声(人声分离不可用),原生人声只在 TTS 时段压低;装好 onnxruntime / 放好模型后 --resplit 重跑可去掉")
    if manifest["checks"]["overflow_lines"]:
        _log(f"⚠ 有 {len(manifest['checks']['overflow_lines'])} 句贴合后撞到下一句开口 / clip 末尾:"
              "上报 orchestrator 回派 dialogue-rewrite 改短或整组重生成,不硬塞")
    if manifest["post_versions_predate_dub"]:
        _log(f"⚠ 后期页本组已有版本 v{manifest['post_versions_predate_dub']} 早于本次配音,采纳版本不含配音:"
              "须在后期页回滚到母本或重做这些版本,再 p8-mix")
    return 0


if __name__ == "__main__":
    sys.exit(main())
