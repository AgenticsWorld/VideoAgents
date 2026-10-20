"""演示视频的声音:代码作曲 / 代码音效 / 混音脚本在宿主库环境下运行,旁白逐句 TTS,成片两遍 loudnorm。

脚本环境(run_script):LIB = modules/codevideo/lib(脚本里 `sys.path.insert(0, LIB)` 后 `from core.audio import sampler as S`、
`from core.audio import sfx`,与上游片子工程写法一致);CODE_AUDIO_OUT = 必须写出的 wav;CODE_AUDIO_DURATION = 目标秒数(可无);
CODEVIDEO_INSTRUMENTS = 按需下载的采样库目录;CODEVIDEO_AUTO_FETCH = 1 时缺库自动下载(「生成模型」页代码作曲开关)。
运行器 audio_run.py 在脚本跑完后读出用到的采样乐器,写 <输出>.credits.json(署名行进 CREDITS)。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import CLI, EP, INSTRUMENTS_DIR, LIB_DIR, NS, ROOT, CodeVideoError, ffmpeg, ffprobe

SCRIPT_TIMEOUT_S = 3600


def auto_fetch_enabled() -> bool:
    """「生成模型」页代码作曲「缺采样库时自动下载」(genconfig music.code.auto_fetch,默认开)。"""
    from . import DATA_DIR
    path = Path(os.environ.get("VIDEOAGENTS_CONFIG_PATH") or DATA_DIR / ".videoagents" / "genconfig.json")
    try:
        sec = json.loads(path.read_text(encoding="utf-8")).get("music") or {}
        return bool((sec.get("code") or {}).get("auto_fetch", True))
    except (OSError, ValueError, AttributeError):
        return True


def lib_env(extra: dict | None = None) -> dict:
    env = dict(os.environ)
    env["LIB"] = str(LIB_DIR)
    env["PYTHONPATH"] = os.pathsep.join([str(LIB_DIR), str(ROOT), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    env["CODEVIDEO_INSTRUMENTS"] = str(INSTRUMENTS_DIR)
    env["CODEVIDEO_FETCH_CLI"] = str(CLI)
    env["CODEVIDEO_PYTHON"] = sys.executable
    env.setdefault("CODEVIDEO_AUTO_FETCH", "1" if auto_fetch_enabled() else "0")
    env["PYTHONUNBUFFERED"] = "1"
    env.update({k: str(v) for k, v in (extra or {}).items() if v is not None})
    return env


def probe(path: Path) -> dict:
    r = subprocess.run([ffprobe(), "-v", "error", "-show_entries", "format=duration:stream=codec_type,sample_rate,channels,"
                        "nb_frames,width,height,r_frame_rate", "-of", "json", str(path)], capture_output=True, text=True)
    if r.returncode:
        raise CodeVideoError(f"ffprobe 读不了 {path.name}:{r.stderr.strip()[-300:]}")
    return json.loads(r.stdout or "{}")


def audio_stats(path: Path) -> dict:
    import numpy as np
    import soundfile as sf
    data, sr = sf.read(str(path), always_2d=True, dtype="float32")
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    rms = float(np.sqrt(np.mean(np.square(data)))) if data.size else 0.0
    to_db = (lambda v: round(float(20 * np.log10(v)), 1) if v > 0 else None)
    return {"duration_s": round(len(data) / sr, 3), "sr": sr, "channels": data.shape[1],
            "peak_db": to_db(peak), "rms_db": to_db(rms)}


def run_script(script: Path, out_wav: Path, duration: float | None = None, cwd: Path | None = None,
               extra_env: dict | None = None, log=print) -> dict:
    """在宿主库环境里跑作曲 / 音效 / 混音脚本;脚本必须写出 out_wav(或它自己声明的路径,经 CODE_AUDIO_OUT 传入)。"""
    script = script.resolve()
    if not script.is_file():
        raise CodeVideoError(f"脚本不存在:{script}")
    out_wav = out_wav.resolve()
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    stale = out_wav.stat().st_mtime if out_wav.is_file() else None
    env = lib_env({"CODE_AUDIO_OUT": out_wav, "CODE_AUDIO_DURATION": duration, **(extra_env or {})})
    t0 = time.time()
    r = subprocess.run([sys.executable, "-m", "modules.codevideo.audio_run", str(script), str(out_wav)],
                       cwd=str(cwd or script.parent), env=env, capture_output=True, text=True, timeout=SCRIPT_TIMEOUT_S)
    if r.stdout.strip():
        log(r.stdout.strip()[-2000:])
    if r.returncode:
        raise CodeVideoError(f"{script.name} 运行失败(退出码 {r.returncode}):{r.stderr.strip()[-1500:]}")
    if not out_wav.is_file() or (stale is not None and out_wav.stat().st_mtime <= stale and out_wav.stat().st_mtime < t0):
        raise CodeVideoError(f"{script.name} 跑完了但没有写出 {out_wav}(脚本要把结果写到环境变量 CODE_AUDIO_OUT 指的路径)")
    st = audio_stats(out_wav)
    if st["peak_db"] is None:
        raise CodeVideoError(f"{out_wav.name} 是全静音")
    credits = out_wav.with_name(out_wav.name + ".credits.json")
    st["credits"] = json.loads(credits.read_text()) if credits.is_file() else []
    st["seconds"] = round(time.time() - t0, 1)
    return st


def convert(src: Path, dst: Path):
    if src.resolve() == dst.resolve():
        return
    r = subprocess.run([ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), *(
        ["-c:a", "libmp3lame", "-b:a", "256k"] if dst.suffix.lower() == ".mp3" else
        ["-c:a", "flac"] if dst.suffix.lower() == ".flac" else
        ["-c:a", "libopus", "-b:a", "192k"] if dst.suffix.lower() == ".opus" else []), str(dst)],
        capture_output=True, text=True)
    if r.returncode:
        raise CodeVideoError(f"转格式失败:{r.stderr[-400:]}")


# ---------------------------------------------------------------- 旁白

def voice(base: Path, force: bool = False, log=print) -> dict:
    """codevideo/lines.json 逐句 genmedia tts(按旁白声线卡)→ voices/<id>.wav + dur.json;只补新句与改过的句。"""
    film = base / NS
    lp = film / "lines.json"
    if not lp.is_file():
        raise CodeVideoError("缺少 codevideo/lines.json(无旁白的片子不派本节点)")
    lines = json.loads(lp.read_text(encoding="utf-8"))
    if isinstance(lines, dict):
        lines = lines.get("lines") or []
    vd = film / "voices"
    vd.mkdir(parents=True, exist_ok=True)
    ledger_p = vd / "voices.json"
    try:
        ledger = json.loads(ledger_p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ledger = {}
    durs = {}
    made = kept = 0
    import hashlib
    for ln in lines:
        lid, text = str(ln.get("id") or ""), str(ln.get("say") or ln.get("text") or "").strip()
        if not lid or not text:
            raise CodeVideoError(f"lines.json 有条目缺 id 或 text:{ln}")
        key = hashlib.sha256(json.dumps([text, ln.get("voice"), ln.get("delivery")], ensure_ascii=False).encode()).hexdigest()[:16]
        wav = vd / f"{lid}.wav"
        if not force and wav.is_file() and (ledger.get(lid) or {}).get("key") == key:
            kept += 1
        else:
            # 旁白 = 不带 --character(genmedia 按项目旁白声线卡 assets/audio/voice/narrator.json 出声);tts 只出 .mp3
            mp3 = vd / f".{lid}.tts.mp3"
            cmd = [sys.executable, str(ROOT / "modules" / "genmedia.py"), "tts", "--text", text, "--output", str(mp3),
                   "--project", base.name]
            if ln.get("delivery"):
                cmd += ["--instructions", str(ln["delivery"])]
            r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                               env={**os.environ, "VIDEOAGENTS_PROJECT": base.name})
            if r.returncode or not mp3.is_file():
                raise CodeVideoError(f"第 {lid} 句 TTS 失败:{(r.stderr or r.stdout).strip()[-600:]}")
            _trim(mp3, wav)
            mp3.unlink(missing_ok=True)
            ledger[lid] = {"key": key, "text": text, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
            made += 1
            log(f"  ✓ {lid} {text[:30]}")
        durs[lid] = audio_stats(wav)["duration_s"]
    (vd / "dur.json").write_text(json.dumps(durs, ensure_ascii=False, indent=1), encoding="utf-8")
    ledger_p.write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"lines": len(lines), "synthesized": made, "kept": kept, "total_s": round(sum(durs.values()), 2)}


def _trim(src: Path, wav: Path):
    """修首尾静音(-50 dB),转 48 kHz 单声道 wav。"""
    tmp = wav.with_name(wav.stem + ".trim.wav")
    af = ("silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.02,areverse,"
          "silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.05,areverse")
    for filt in (af, "anull"):      # 修静音把整句修没了(极轻的句子)就原样转格式
        r = subprocess.run([ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-af", filt,
                            "-ar", "48000", "-ac", "1", str(tmp)], capture_output=True, text=True)
        if r.returncode == 0 and tmp.is_file() and tmp.stat().st_size > 4800:
            tmp.replace(wav)
            return
    tmp.unlink(missing_ok=True)
    raise CodeVideoError(f"TTS 结果转 wav 失败:{src.name}")


# ---------------------------------------------------------------- 混音

STEMS = ("voice", "music", "sfx", "ambience")


def mix(base: Path, log=print) -> dict:
    film = base / NS
    script = film / "mix.py"
    if not script.is_file():
        raise CodeVideoError("缺少 codevideo/mix.py(cv4-mix 写)")
    if not (film / "events.json").is_file():
        raise CodeVideoError("缺少 codevideo/events.json:先跑 codevideo.py events")
    out = film / "out" / "mix.wav"
    st = run_script(script, out, cwd=film, extra_env={"CODEVIDEO_FILM": film, "CODEVIDEO_STEMS": film / "out" / "stems"},
                    log=log)
    from . import timeline as TL
    dur = TL.load(base)["dur"]
    if st["duration_s"] < dur - 0.05:
        raise CodeVideoError(f"mix.wav 只有 {st['duration_s']} s,短于片长 {dur} s")
    stems = {}
    for name in STEMS:
        p = film / "out" / "stems" / f"{name}.wav"
        if p.is_file():
            stems[name] = audio_stats(p)
    _write_mix_manifest(base, st, stems)
    return {"mix": st, "stems": stems}


def _write_mix_manifest(base: Path, st: dict, stems: dict):
    """登记 assets/audio/final/ep01.wav 与分轨,供后期页声音轨与成片发布页读取。"""
    film = base / NS
    final_dir = base / "assets" / "audio" / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    import shutil
    shutil.copyfile(film / "out" / "mix.wav", final_dir / f"{EP}.wav")
    man = {"schema": "codevideo_mix/1", "episode": EP, "generator": "codevideo", "mix": f"assets/audio/final/{EP}.wav",
           "duration_s": st["duration_s"], "peak_db": st["peak_db"],
           "stems": {k: {"file": f"{NS}/out/stems/{k}.wav", **v} for k, v in stems.items()},
           "written_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    (film / "out" / "mix.json").write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- 成片

def loudness(path: Path) -> dict:
    r = subprocess.run([ffmpeg(), "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True)
    out = {}
    import re
    for key, pat in (("I", r"^\s+I:\s+(-?[\d.]+|-inf) LUFS"), ("LRA", r"^\s+LRA:\s+([\d.]+) LU"),
                     ("TP", r"^\s+Peak:\s+(-?[\d.]+|-inf) dBFS")):
        m = re.findall(pat, r.stderr, re.M)
        if m:
            out[key] = float(m[-1]) if m[-1] != "-inf" else float("-inf")
    return out


def _loudnorm_filter(wav: Path, lufs: float, tp: float) -> str:
    r = subprocess.run([ffmpeg(), "-hide_banner", "-nostats", "-i", str(wav), "-af",
                        f"loudnorm=I={lufs}:TP={tp}:LRA=11:print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True)
    if r.returncode:
        raise CodeVideoError(f"ffmpeg 读不了混音 {wav.name}:{r.stderr[-400:]}")
    txt = r.stderr[r.stderr.rfind("{"):r.stderr.rfind("}") + 1]
    try:
        m = json.loads(txt)
    except ValueError:
        return "anull"
    vals = [m.get(k) for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")]
    if any(v is None or "inf" in str(v) or "nan" in str(v) for v in vals):
        return "anull"    # 静音或低于 −70 LUFS:不做归一
    return (f"loudnorm=I={lufs}:TP={tp}:LRA=11:measured_I={vals[0]}:measured_TP={vals[1]}:measured_LRA={vals[2]}:"
            f"measured_thresh={vals[3]}:offset={vals[4]}:linear=true")


def master(base: Path, grain: float = 0, lufs: float = -14, tp: float = -1.2, log=print) -> dict:
    from . import render as R
    from . import timeline as TL
    tl = TL.load(base)
    fps = tl["fps"]
    clips = []
    for s in tl["sections"]:
        mp4, _, _ = R.clip_paths(base, s["group_id"])
        if not mp4.is_file():
            raise CodeVideoError(f"段 {s['id']} 还没渲染({mp4.relative_to(base)} 不存在)")
        clips.append(mp4)
    stale = [r["section"] for r in R.status(base) if r["state"] != "fresh"]
    if stale:
        raise CodeVideoError(f"这些段的画面已过期(代码或时间轴改过):{', '.join(stale)};先跑 codevideo.py render")
    mixwav = base / NS / "out" / "mix.wav"
    if not mixwav.is_file():
        raise CodeVideoError("缺少 codevideo/out/mix.wav:先跑 codevideo.py mix")
    edit = base / "edit" / EP
    edit.mkdir(parents=True, exist_ok=True)
    work = edit / ".codevideo"
    work.mkdir(exist_ok=True)
    lst = work / "concat.txt"
    lst.write_text("".join(f"file '{c.resolve().as_posix()}'\n" for c in clips))
    video = work / "video.mp4"
    r = subprocess.run([ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-c", "copy", str(video)], capture_output=True, text=True)
    if r.returncode:
        raise CodeVideoError(f"拼段失败:{r.stderr[-500:]}")
    ln = _loudnorm_filter(mixwav, lufs, tp)
    vf = f"fps={fps}" + (f",noise=c0s={grain:g}:allf=t" if grain else "") + ",format=yuv420p"
    vdur = tl["frames"] / fps
    out = edit / "final.mp4"
    tmp = edit / "final.codevideo.tmp.mp4"
    r = subprocess.run([ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(video), "-i", str(mixwav),
                        "-filter_complex", f"[0:v]{vf}[v];[1:a]{ln},aresample=48000,apad=whole_dur={vdur:.6f}[a]",
                        "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "slow", "-crf", "19", "-r", str(fps),
                        "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", "-t", f"{vdur:.6f}", str(tmp)],
                       capture_output=True, text=True)
    if r.returncode or not tmp.is_file() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise CodeVideoError(f"成片封装失败:{r.stderr[-600:]}")
    tmp.replace(out)
    for p in (video, lst):
        p.unlink(missing_ok=True)
    try:
        work.rmdir()
    except OSError:
        pass
    meas = loudness(out)
    res = {"final": out.relative_to(base).as_posix(), "frames": tl["frames"], "dur": round(vdur, 3), "loudness": meas,
           "normalized": ln != "anull"}
    srt = subtitles(base)
    if srt:
        res["srt"] = srt
    res["poster"] = poster(base, tl)
    if meas.get("I") is not None and (abs(meas["I"] - lufs) > 1 or meas.get("TP", -99) > -1.0):
        log(f"⚠ 响度没达标:{meas.get('I')} LUFS / 真峰值 {meas.get('TP')} dB(目标 {lufs} LUFS / ≤ −1 dB):混音削波或峰值太高,"
            "降低 mix 并压住峰值后重跑 master")
    write_build_sh(base)
    return res


def poster(base: Path, tl: dict) -> str | None:
    """海报:timeline.json 的 poster_t(没有就取最后一个 key 镜的中点,再没有取 85% 处)。"""
    from . import render as R
    t = tl.get("poster_t")
    if not isinstance(t, (int, float)):
        keys = [sh for s in tl["sections"] for sh in s["shots"] if sh.get("key")]
        t = ((keys[-1]["f0"] + keys[-1]["f1"]) / 2 / tl["fps"]) if keys else tl["dur"] * 0.85
    out = base / "edit" / EP / "poster.jpg"
    try:
        p = R.stills(base, [float(t)], out_dir=out.parent / ".poster", prefix="p_")[0]
        p.replace(out)
        (out.parent / ".poster").rmdir()
    except (CodeVideoError, OSError):
        return None
    return out.relative_to(base).as_posix()


def subtitles(base: Path) -> str | None:
    """codevideo/cues.json([{t0,t1,text}],页面字幕时间线原样导出)→ edit/ep01/subtitles.srt;相邻重叠截到下一条开始。"""
    cp = base / NS / "cues.json"
    if not cp.is_file():
        return None
    cues = sorted(json.loads(cp.read_text(encoding="utf-8")), key=lambda c: c["t0"])
    for k in range(len(cues) - 1):
        cues[k]["t1"] = min(cues[k]["t1"], cues[k + 1]["t0"])

    def fmt(s):
        ms = int(round(max(0, s) * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"

    out = base / "edit" / EP / "subtitles.srt"
    out.write_text("\n".join(f"{k + 1}\n{fmt(c['t0'])} --> {fmt(c['t1'])}\n{c['text']}\n" for k, c in enumerate(cues)),
                   encoding="utf-8")
    return out.relative_to(base).as_posix()


def write_build_sh(base: Path):
    p = base / NS / "build.sh"
    p.write_text(
        "#!/bin/sh\n"
        f"# 一键重建《{base.name}》:事件 → 旁白 → 配乐 → 按段渲染 → 混音 → 成片(VideoAgents 演示视频项目类型)\n"
        "# 在 VideoAgents 仓库根目录(或设 VIDEOAGENTS_ROOT)运行:sh data/projects/<项目>/codevideo/build.sh\n"
        "set -e\n"
        'HERE=$(cd "$(dirname "$0")" && pwd)\n'
        'ROOT=${VIDEOAGENTS_ROOT:-$(cd "$HERE/../../../.." && pwd)}\n'
        f'exec python3 "$ROOT/code/codevideo.py" build --project {base.name} "$@"\n', encoding="utf-8")
    p.chmod(0o755)
