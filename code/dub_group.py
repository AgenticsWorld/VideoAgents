#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dub_group.py — 对白后期配音(输出设置「对白配音=后期配音」,workflow p7-dub,09-audio/voice-generation 执行)。

前提:项目 output.dialogue_voice == "dubbing"(视频原声模式严禁调用——§8A 红线:TTS 不进成片对白)。
组视频仍按对白组常规生成(prompt 照写 `{}` 台词、挂 voiceprint 音色锚),人物开口表演由模型原生生成;
本脚本在 p7-video 交付后,把该组 clip 的**对白轨**替换成按角色声线合成、按画面开口时段贴合的 TTS:

  1. 台词事实源:directing/epNN/shot_list.json 组内各镜 dialogue_lines(speaker/text,冻结版,一字不改);
  2. 开口时段:组 clip 原生音轨 ffmpeg silencedetect 求语音区间(可 --noise-db/--min-silence 调阈),
     按台词顺序对位(区间多则按最小间隙合并、少则按台词字数比例拆分);模型原生轨杂音重、自动检测
     不可靠时,Agent 目检/听审后用 --segments <json> 手工给定 [{"line":0,"start":1.2,"end":4.0},...];
  3. 逐句 TTS:按项目级 assets/audio/voice/casting.json 该角色×形态条目(tts_voice/speed;形态按组
     audio_refs 样本文件名 <CHAR>_<variant>_voiceprint → 声纹卡章节范围 → 唯一已登记形态,modules/voice_variants.py;
     可 --variant CHAR=variant 覆盖),
     走 modules/genmedia.generate_tts(云渠道传 casting 的 voice;ComfyUI 渠道传 --character/--variant 自动选型);
  4. 口型贴合:实测 TTS 时长与开口时段比对,先按比例重合成(--speed,受 --speed-min/--speed-max 约束,
     默认 0.75–1.25),残差用 atempo 微调(±10% 内),起点对齐开口起点;仍装不下的句子记 overflow 上报
     (回派 dialogue-rewrite 改短或整组重生成,不硬塞);
  5. 混轨:原生轨在开口时段压低(--duck-db,默认 -26dB,保留环境声/音效),叠上逐句 TTS(电平对齐原生
     开口段),画面流 -c:v copy 原样封装回 assets/clips/epNN/grpNNN.mp4(时长/fps/分辨率不变);
     原生轨首次替换前备份到 assets/clips/epNN/grpNNN.native_audio.wav,重跑时以备份为源(幂等)。

产物:assets/audio/voice/epNN/dub/grpNNN/{lNN_<CHAR>.mp3, lNN_<CHAR>.fit.wav, dub_manifest.json}
      + 组 clip 新版本(meta.json 追加 dialogue_voice 段)。manifest 的 checks 供机检:
      dub_lines_text_match_frozen_script / dub_speaker_casting_bound / dub_fit_ok / clip_duration_unchanged。

用法:
  python3 code/dub_group.py --project <slug> --ep ep01 --group grp012            # 全流程
  python3 code/dub_group.py --project <slug> --ep ep01 --group grp012 --detect-only   # 只输出开口时段供人工核对
  python3 code/dub_group.py ... --segments dub/grp012/segments.json                   # 手工时段
  python3 code/dub_group.py ... --dry-run                                            # 不调 TTS、不改 clip
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

from _common import parse_args, REPO_ROOT  # noqa: F401  (副作用:modules/ 入 sys.path)

_SIL_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SIL_END = re.compile(r"silence_end:\s*(-?[\d.]+)")
_MEAN_VOL = re.compile(r"mean_volume:\s*(-?[\d.]+)\s*dB")


# ---------------------------------------------------------------- 外部工具

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


# ---------------------------------------------------------------- 开口时段检测与对位(纯函数,可单测)

def detect_speech(path, total: float, noise_db: float = -30.0, min_silence: float = 0.25,
                  min_speech: float = 0.30, merge_gap: float = 0.20) -> list[list[float]]:
    """silencedetect 的补集 = 语音区间;短间隙合并、过短区间剔除。"""
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
        # 台词到区间的临时分配:第 i 区间承接第 i 句起若干句;简单起见把该区间按其覆盖的两句权重切
        li = min(i, m - 2)
        w1 = _text_weight(lines[li]["text"])
        w2 = _text_weight(lines[li + 1]["text"])
        a, b = segs[i]
        cut = a + (b - a) * w1 / (w1 + w2)
        segs[i:i + 1] = [[a, cut], [cut, b]]
    return [[round(a, 3), round(b, 3)] for a, b in segs]


def plan_fit(tts_dur: float, target: float, base_speed: float,
             speed_min: float, speed_max: float, tempo_tol: float = 0.10) -> dict:
    """给定首版 TTS 时长与目标时段,算重合成语速与残差 atempo。返回 {speed, atempo, overflow}。
    speed>1 更快;atempo>1 更快(时长变短)。"""
    if target <= 0:
        return {"speed": base_speed, "atempo": 1.0, "overflow": True}
    ratio = tts_dur / target                       # >1 说明 TTS 比开口时段长
    want = base_speed * ratio
    speed = min(speed_max, max(speed_min, want))
    est = tts_dur * base_speed / speed             # 重合成后估计时长
    resid = est / target                           # 仍需 atempo 的倍率
    atempo = min(1.0 + tempo_tol, max(1.0 - tempo_tol, resid))
    overflow = (est / atempo) > target * 1.05      # 用尽语速与微调仍装不下
    return {"speed": round(speed, 3), "atempo": round(atempo, 4), "overflow": overflow,
            "ratio_first": round(ratio, 3)}


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
                          "emotion": ln.get("emotion") or ln.get("tone") or ""})
            idx += 1
    return g, lines


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
            print(f"[dub] WARN {vr['warning']}", file=sys.stderr)
    res["_problems"] = problems
    return res


def project_dialogue_mode(proj: Path) -> str:
    try:
        st = json.loads((proj / "settings.json").read_text(encoding="utf-8"))
        return ((st.get("output") or {}).get("dialogue_voice")) or "native"
    except Exception:
        return "native"


# ---------------------------------------------------------------- 主流程

def main(argv=None):
    def conf(ap: argparse.ArgumentParser):
        ap.add_argument("--group", required=True, help="组号,如 grp012")
        ap.add_argument("--segments", default=None,
                        help="手工开口时段 JSON:[{\"line\":0,\"start\":1.2,\"end\":4.0},...](line 为组内台词序号)")
        ap.add_argument("--variant", action="append", default=[],
                        help="覆盖说话人形态,CHAR-0003=child(可多次)")
        ap.add_argument("--noise-db", type=float, default=-30.0, help="silencedetect 噪声门限 dB(默认 -30)")
        ap.add_argument("--min-silence", type=float, default=0.25, help="判为静音的最短时长 s(默认 0.25)")
        ap.add_argument("--speed-min", type=float, default=0.75)
        ap.add_argument("--speed-max", type=float, default=1.25)
        ap.add_argument("--duck-db", type=float, default=-26.0, help="开口时段原生轨压低量 dB(默认 -26)")
        ap.add_argument("--detect-only", action="store_true", help="只检测并打印开口时段,不合成不改 clip")
        ap.add_argument("--dry-run", action="store_true", help="不调 TTS、不改 clip,只打印计划")
        ap.add_argument("--force-native-mode", action="store_true",
                        help="项目未设 dubbing 也执行(仅验证/对拍用;正式流程禁用)")
    args, proj = parse_args("对白后期配音:按开口时段用角色声线 TTS 替换组 clip 对白轨", configure=conf, argv=argv)
    _require_tools()
    ep, gid = args.ep, args.group
    mode = project_dialogue_mode(proj)
    if mode != "dubbing" and not args.force_native_mode:
        raise SystemExit(f"项目 output.dialogue_voice={mode}(视频原声):TTS 严禁进成片对白(§8A 红线),不执行;"
                         "如需后期配音请在「输出设置→对白配音」改为后期配音")

    clip = proj / "assets" / "clips" / ep / f"{gid}.mp4"
    if not clip.is_file():
        raise SystemExit(f"缺组 clip:{clip}")
    meta_p = clip.with_suffix(".meta.json")
    dub_dir = proj / "assets" / "audio" / "voice" / ep / "dub" / gid
    dub_dir.mkdir(parents=True, exist_ok=True)
    native_bak = clip.with_name(f"{gid}.native_audio.wav")

    group, lines = load_group(proj, ep, gid)
    if not lines:
        raise SystemExit(f"{gid} 无 dialogue_lines(audio_plan={group.get('audio_plan')}),非对白组不派 p7-dub")
    total = probe_duration(clip)

    # 原生轨源:首跑从 clip 抽,重跑用备份(幂等,不在配音轨上再配音)
    if native_bak.is_file():
        native_src = native_bak
    else:
        native_src = dub_dir / "_native_probe.wav"
        rc, out = _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(clip),
                        "-vn", "-acodec", "pcm_s16le", str(native_src)])
        if rc:
            raise SystemExit(f"抽原生轨失败:{out}")

    # 开口时段
    if args.segments:
        manual = json.loads(Path(args.segments).read_text(encoding="utf-8"))
        by_line = {int(m["line"]): [float(m["start"]), float(m["end"])] for m in manual}
        missing = [i for i in range(len(lines)) if i not in by_line]
        if missing:
            raise SystemExit(f"--segments 缺台词序号 {missing}(共 {len(lines)} 句)")
        segs = [by_line[i] for i in range(len(lines))]
        seg_source = "manual"
    else:
        raw = detect_speech(native_src, total, args.noise_db, args.min_silence)
        segs = align_segments(raw, lines)
        seg_source = f"silencedetect(noise={args.noise_db}dB,d={args.min_silence})"
        if args.detect_only:
            print(json.dumps({"group": gid, "clip_duration_s": round(total, 3), "raw_speech": raw,
                              "aligned": [{"line": i, "speaker": ln["speaker"], "text": ln["text"],
                                           "start": s[0], "end": s[1]} for i, (ln, s) in enumerate(zip(lines, segs))]},
                             ensure_ascii=False, indent=2))
            return 0
    for i, (a, b) in enumerate(segs):
        if not (0 <= a < b <= total + 0.05):
            raise SystemExit(f"第 {i} 句时段非法 [{a},{b}](clip {total:.2f}s)")

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
    # 火山 seed-audio-1.0=描述定制嗓音:不传 casting 的 speaker 名(genmedia 按声纹卡
    # 描述+项目 voiceprint 样本自动锚定同一副嗓子,逐句合成不漂音色)
    desc_mode = provider == "volcengine" and tts_cfg.get("model") == "seed-audio-1.0"

    # 对白语音库(2026-09-13,输出设置「生成对白语音」):开着时先惰性同步本组各镜,首轮直接取库里自然语速音频,
    # 只有贴合需要改语速时才重新合成(重出仍落 dub 目录,不回写库)
    from modules import dialogue_tts as dt
    lib_audio = {}
    if dt.enabled(proj) and not args.dry_run:
        lib = dt.ensure(proj, ep, only_shots=set(group.get("shots") or []))
        lib_audio = dt.line_audio(proj, ep, lib) if lib else {}
        print(f"[dub] 对白语音库:本组 {sum(1 for ln in lines if (ln['shot_id'], ln['idx']) in lib_audio)}/{len(lines)} 句可直接取用")

    manifest = {"schema": "dub_manifest/v1", "group_id": gid, "episode": ep, "mode": "dubbing",
                "clip": str(clip.relative_to(proj)), "clip_duration_s": round(total, 3),
                "segment_source": seg_source, "tts_provider": provider,
                "speed_range": [args.speed_min, args.speed_max], "duck_db": args.duck_db,
                "dialogue_tts_library": bool(lib_audio),
                "lines": [], "checks": {}}
    plan_only = args.dry_run
    fitted = []
    for i, (ln, (a, b)) in enumerate(zip(lines, segs)):
        ch = ln["speaker"]
        var = variants.get(ch, "default")
        c = casting.get((ch, var)) or casting.get((ch, "default"))
        if not c:
            raise SystemExit(f"casting.json 无 {ch}/{var} 条目——先登记再合成(dub_speaker_casting_bound)")
        # casting 数字 speed 优先;描述文字(「常态(未传 --speed)」)视为未填 → 项目 output.dialogue_tts_speed(默认 1.0)
        base_speed = dt.num_speed(c.get("speed")) or dt.default_speed(proj)
        voice = "" if (provider == "comfyui" or desc_mode) else (c.get("tts_voice") or "")
        target = b - a
        raw_mp3 = dub_dir / f"l{i:02d}_{ch}.mp3"
        fit_wav = dub_dir / f"l{i:02d}_{ch}.fit.wav"
        entry = {"line": i, "shot_id": ln["shot_id"], "speaker": ch, "variant": var,
                 "text": ln["text"], "casting_ref": f"casting.json#{ch}/{var}",
                 "tts_voice": c.get("tts_voice"), "tts_model": c.get("tts_model"),
                 "segment": {"start": a, "end": b, "duration_s": round(target, 3)},
                 "tts_file": str(raw_mp3.relative_to(proj)), "fit_file": str(fit_wav.relative_to(proj))}
        if plan_only:
            entry.update({"status": "planned"})
            manifest["lines"].append(entry)
            continue
        lib_entry = lib_audio.get((ln["shot_id"], ln["idx"]))
        if lib_entry and lib_entry.get("variant", "default") == var and abs(float(lib_entry.get("speed") or 1.0) - base_speed) <= 0.02:
            shutil.copyfile(lib_entry["path"], raw_mp3)
            entry["source"] = f"dialogue_tts:{lib_entry['file']}"
        else:
            generate_tts(ln["text"], str(raw_mp3), voice, base_speed, ln.get("emotion") or "",
                         ch, var if var != "default" else "", str(proj))
            entry["source"] = "tts"
        d1 = probe_duration(raw_mp3)
        fit = plan_fit(d1, target, base_speed, args.speed_min, args.speed_max)
        used_speed = base_speed
        if abs(fit["speed"] - base_speed) > 0.02:
            generate_tts(ln["text"], str(raw_mp3), voice, fit["speed"], ln.get("emotion") or "",
                         ch, var if var != "default" else "", str(proj))
            used_speed = fit["speed"]
            d1 = probe_duration(raw_mp3)
        atempo = fit["atempo"]
        # 残差 atempo 按实测重算(重合成后的真实时长)
        resid = d1 / target if target > 0 else 1.0
        atempo = min(1.10, max(0.90, resid)) if abs(resid - 1) > 0.03 else 1.0
        # 电平对齐:TTS 均值 → 原生开口段均值
        nat_db = mean_volume_db(native_src, a, b)
        tts_db = mean_volume_db(raw_mp3)
        gain = 0.0 if nat_db is None or tts_db is None else max(-12.0, min(12.0, nat_db - tts_db))
        af = f"atempo={atempo:.4f},volume={gain:.2f}dB,aresample=48000"
        rc, out = _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(raw_mp3),
                        "-af", af, "-ac", "2", str(fit_wav)])
        if rc:
            raise SystemExit(f"贴合处理失败 l{i:02d}:{out}")
        d2 = probe_duration(fit_wav)
        overflow = d2 > target * 1.05
        entry.update({"speed_used": used_speed, "atempo": round(atempo, 4), "gain_db": round(gain, 2),
                      "tts_duration_s": round(d1, 3), "fit_duration_s": round(d2, 3),
                      "fit_ratio": round(d2 / target, 3) if target else None,
                      "overflow": overflow, "status": "overflow" if overflow else "ok"})
        manifest["lines"].append(entry)
        fitted.append((a, fit_wav))
        print(f"[dub] l{i:02d} {ch} seg={a:.2f}-{b:.2f}s tts={d1:.2f}s speed={used_speed} atempo={atempo:.3f}"
              f" fit={d2:.2f}s{'  ⚠ overflow' if overflow else ''}")

    if plan_only:
        manifest["checks"] = {"dry_run": True}
        (dub_dir / "dub_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0

    # 混轨并封装回 clip
    if not native_bak.is_file():
        shutil.copyfile(native_src, native_bak)
    duck = "".join(f",volume=enable='between(t,{a:.3f},{b:.3f})':volume={args.duck_db}dB" for a, b in segs)
    fc = [f"[0:a]aresample=48000,aformat=channel_layouts=stereo{duck}[nat]"]
    inputs = ["-i", str(native_bak)]
    labels = ["[nat]"]
    for k, (a, w) in enumerate(fitted):
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
    for p in (dub_dir / "_native_probe.wav", mixed):
        p.unlink(missing_ok=True)

    manifest["native_audio_backup"] = str(native_bak.relative_to(proj))
    manifest["checks"] = {
        "dub_lines_text_match_frozen_script": True,        # 台词直接取自 shot_list 冻结版
        "dub_speaker_casting_bound": True,                # 缺条目已在合成前 SystemExit
        "dub_fit_ok": all(not e["overflow"] and abs((e["fit_ratio"] or 1) - 1) <= 0.10 for e in manifest["lines"]),
        "clip_duration_unchanged": dur_ok, "video_stream_unchanged": stream_ok,
        "overflow_lines": [e["line"] for e in manifest["lines"] if e["overflow"]],
    }
    manifest["dubbed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    (dub_dir / "dub_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    try:
        meta = json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.is_file() else {}
        meta["dialogue_voice"] = {"mode": "dubbing", "manifest": str((dub_dir / "dub_manifest.json").relative_to(proj)),
                                  "native_audio_backup": manifest["native_audio_backup"],
                                  "dubbed_at": manifest["dubbed_at"], "checks": manifest["checks"]}
        meta_p.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    except Exception as e:  # noqa: BLE001
        print(f"[dub] meta.json 更新失败(不影响 clip):{e}")
    print(json.dumps(manifest["checks"], ensure_ascii=False))
    if manifest["checks"]["overflow_lines"]:
        print(f"[dub] ⚠ 有 {len(manifest['checks']['overflow_lines'])} 句在语速上限内仍装不下开口时段:"
              "上报 orchestrator 回派 dialogue-rewrite 改短或整组重生成,不硬塞")
    return 0


if __name__ == "__main__":
    sys.exit(main())
