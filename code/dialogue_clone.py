#!/usr/bin/env python3
"""数字人克隆配音宿主 CLI(2026-10-20,数字人项目「声音来源 = 克隆声音」,agents/workflows/digitalhuman.yaml 的 dh0-voice)。

用户没有原始录音:在参考文件「克隆声音」分类(refs/voices/)放每个说话人一段声音样本,写一份台词稿;本 CLI 逐句用
该说话人的样本零样本克隆合成(modules/genmedia.py tts --clone-from 同一口径),拼成母带 assets/audio/master/<ep>.mp3
和逐段带时间戳与人物的稿 digital-human/transcript_cloned.txt,之后数字人流程照「用户已有录音 + 文稿」走
(dh0-ingest 只核对母带指纹、dh0-transcribe 跳过)。母带一经产出即权威,下游零重编码。

子命令:
  plan   纯读:解析台词稿与声音映射,核对样本、TTS 渠道能否克隆,列出待合成句;不调用 TTS
  synth  逐句克隆合成(只补新句 / 改过的句;--force 全部重出)→ 修首尾静音 → 拼母带 → 写人物稿与台账
  check  机检 voice_clone_ok:台账齐、文稿与样本未改、母带指纹一致、人物稿连续覆盖整条母带

台词稿默认 refs/text/transcript.txt:每行「说话人:台词」(中英文冒号均可);只有一个说话人时可以不写人名;
空行与 # 开头的行忽略,行首的 [时间戳] 去掉。
声音映射 digital-human/voice_cast.json:{"speakers": {"主持人": {"sample": "refs/voices/host.mp3"}}};
--voice 主持人=refs/voices/host.mp3 可重复,会写回该文件。只有一个说话人且只有一段样本,或样本文件名(去扩展名)
与说话人同名(不分大小写)时自动对上。

用法:python3 code/dialogue_clone.py plan  --project <slug> [--ep ep01] [--voice 名=refs/voices/x.mp3 ...] [--json]
     python3 code/dialogue_clone.py synth --project <slug> [--ep ep01] [--gap 0.4] [--force]
     python3 code/dialogue_clone.py check --project <slug> [--ep ep01] [--json]
退出码:0 正常 / PASS;1 有问题(plan 列出的问题、synth 有失败句、check FAIL);2 缺台词稿。
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args  # noqa: E402  副作用:modules/ 与仓库根入 sys.path

SCRIPT_REL = "refs/text/transcript.txt"
VOICES_REL = "refs/voices"
CAST_REL = "digital-human/voice_cast.json"
MANIFEST_REL = "digital-human/voice_clone.json"
TRANSCRIPT_REL = "digital-human/transcript_cloned.txt"
DEFAULT_SPEAKER = "主持人"            # 与 dialogue-ingest「只有一张图且没命名」的缺省人物名一致
AUDIO_EXTS = (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg")
SR = 44100
LEAD_S, TAIL_S, GAP_S = 0.3, 0.6, 0.4
SAMPLE_MIN_S, SAMPLE_WARN_LO, SAMPLE_WARN_HI = 2.0, 5.0, 30.0
LONG_LINE_CHARS = 300
_TS = re.compile(r"^\s*\[[^\]]*\d[^\]]*\]\s*")
_LINE = re.compile(r"^(?P<speaker>[^:：\s][^:：]{0,23}?)\s*[:：]\s*(?P<text>\S.*)$")


def _rel(proj: Path, p: Path) -> str:
    try:
        return p.resolve().relative_to(proj.resolve()).as_posix()
    except ValueError:
        return str(p)


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def probe_duration(path: Path) -> float | None:
    if not shutil.which("ffprobe"):
        return None
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


def parse_script(text: str, known: set[str] | None = None) -> tuple[list[dict], list[str]]:
    """台词稿 → [{idx, speaker, text}], 问题清单。known = 声音映射里已有的说话人:有它时只把这些名字当说话人前缀,
    台词里的冒号(「他说:好」)不会被误认成人名;没有映射时,行行都是「名:台词」才算多人稿,否则整份归唯一说话人。"""
    rows, issues = [], []
    raw = [(n, _TS.sub("", s).strip()) for n, s in enumerate(text.splitlines(), 1)]
    raw = [(n, s) for n, s in raw if s and not s.startswith("#")]
    known = {k for k in (known or set()) if k}
    if known:
        only = next(iter(known)) if len(known) == 1 else None
        for n, s in raw:
            m = _LINE.match(s)
            if m and m.group("speaker").strip() in known:
                rows.append({"line": n, "speaker": m.group("speaker").strip(), "text": m.group("text").strip()})
            elif only:
                rows.append({"line": n, "speaker": only, "text": s})
            else:
                issues.append(f"台词稿第 {n} 行没有说话人:多人稿每行写「说话人:台词」,说话人取 {'、'.join(sorted(known))}")
    else:
        matches = [_LINE.match(s) for _, s in raw]
        labeled = bool(raw) and all(matches)
        for (n, s), m in zip(raw, matches):
            if labeled:
                rows.append({"line": n, "speaker": m.group("speaker").strip(), "text": m.group("text").strip()})
            else:
                rows.append({"line": n, "speaker": DEFAULT_SPEAKER, "text": s})
    if not raw:
        issues.append("台词稿是空的")
    for i, r in enumerate(rows, 1):
        r["idx"] = i
    return rows, issues


def list_samples(proj: Path) -> list[Path]:
    d = proj / VOICES_REL
    return sorted(p for p in d.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO_EXTS) if d.is_dir() else []


def resolve_cast(proj: Path, speakers: list[str], overrides: dict[str, str] | None = None) -> tuple[dict, list[str]]:
    """说话人 → 样本相对路径。显式映射(voice_cast.json + --voice)优先,再按同名文件 / 一人一样本自动对上。"""
    saved = (_read_json(proj / CAST_REL).get("speakers") or {})
    cast = {k: str((v or {}).get("sample") or "") for k, v in saved.items() if isinstance(v, dict)}
    cast.update({k: v for k, v in (overrides or {}).items() if k and v})
    notes = []
    samples = list_samples(proj)
    used = {Path(v).name for v in cast.values() if v}
    for name in speakers:
        if cast.get(name):
            continue
        same = [p for p in samples if p.stem.casefold() == name.casefold()]
        if len(same) == 1:
            cast[name] = _rel(proj, same[0])
            notes.append(f"{name} ← {cast[name]}(文件名同名,自动对上)")
    missing = [n for n in speakers if not cast.get(n)]
    free = [p for p in samples if p.name not in used and _rel(proj, p) not in cast.values()]
    if len(missing) == 1 and len(free) == 1:
        cast[missing[0]] = _rel(proj, free[0])
        notes.append(f"{missing[0]} ← {cast[missing[0]]}(只剩这一段样本,自动对上)")
    return cast, notes


def _engine() -> tuple[bool, str, str]:
    """(能否克隆, 渠道说明, 渠道名);读当前「生成模型」页 TTS 配置。"""
    try:
        from modules import genmedia
        cfg = genmedia.get_config("tts")
        ok, why = genmedia.clone_support(cfg)
        return ok, why, cfg.get("provider") or ""
    except Exception as exc:  # noqa: BLE001  配置缺失等,如实上报
        return False, f"读不到 TTS 配置:{exc}", ""


def _key(text: str, sample_sha: str, engine: str) -> str:
    return hashlib.sha256(f"{text}\n{sample_sha}\n{engine}".encode("utf-8")).hexdigest()[:16]


def plan(proj: Path, ep: str, overrides: dict[str, str] | None = None, engine=None, script_rel: str = SCRIPT_REL) -> dict:
    proj = Path(proj)
    script = proj / script_rel
    out = {"episode": ep, "script": script_rel, "issues": [], "warnings": [], "notes": [], "lines": [], "voices": {}}
    if not script.is_file():
        out["issues"].append(f"缺台词稿 {script_rel}(新建向导的「台词稿」或参考文件「文本」分类)")
        out["missing_script"] = True
        return out
    out["script_sha256"] = _sha(script)
    saved_names = set((_read_json(proj / CAST_REL).get("speakers") or {})) | set((overrides or {}))
    rows, issues = parse_script(script.read_text(encoding="utf-8"), saved_names)
    out["issues"] += issues
    speakers = list(dict.fromkeys(r["speaker"] for r in rows))
    cast, notes = resolve_cast(proj, speakers, overrides)
    out["notes"] += notes
    out["speakers"] = speakers
    for name in speakers:
        rel = cast.get(name) or ""
        if not rel:
            out["issues"].append(f"{name} 没有对应的克隆声音样本:把样本上传到参考文件「克隆声音」分类(refs/voices/),"
                                 f"再用 --voice {name}=refs/voices/<文件> 或在 {CAST_REL} 登记")
            continue
        p = proj / rel
        if not p.is_file() or p.suffix.lower() not in AUDIO_EXTS:
            out["issues"].append(f"{name} 的样本不存在或不是音频:{rel}")
            continue
        dur = probe_duration(p)
        v = {"sample": rel, "sample_sha256": _sha(p), "duration_s": round(dur, 2) if dur else None}
        out["voices"][name] = v
        if dur is not None and dur < SAMPLE_MIN_S:
            out["issues"].append(f"{name} 的样本只有 {dur:.1f} 秒,太短克隆不出音色(建议 {SAMPLE_WARN_LO:g}–{SAMPLE_WARN_HI:g} 秒)")
        elif dur is not None and not (SAMPLE_WARN_LO <= dur <= SAMPLE_WARN_HI):
            out["warnings"].append(f"{name} 的样本 {dur:.1f} 秒,建议 {SAMPLE_WARN_LO:g}–{SAMPLE_WARN_HI:g} 秒、只有这一个人说话的干净录音")
    ok, why, provider = engine if engine is not None else _engine()
    out.update(clone_ok=ok, engine=why, provider=provider)
    if not ok:
        out["issues"].append(why)
    old = {(e.get("idx"), e.get("key")): e for e in (_read_json(proj / MANIFEST_REL).get("lines") or [])}
    for r in rows:
        v = out["voices"].get(r["speaker"])
        key = _key(r["text"], v["sample_sha256"], why) if v else ""
        prev = old.get((r["idx"], key))
        done = bool(key and prev and prev.get("status") == "ok" and (proj / prev.get("file", "")).is_file())
        if len(r["text"]) > LONG_LINE_CHARS:
            out["warnings"].append(f"第 {r['line']} 行 {len(r['text'])} 字,建议拆成几句(一句一个数字人片段,太长渠道会再切)")
        out["lines"].append({**r, "key": key, "status": "ok" if done else "todo"})
    out["cast"] = cast
    return out


def _decode_pcm(path: Path) -> bytes:
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le", "-ac", "1", "-ar", str(SR), "-"],
                       capture_output=True)
    if r.returncode:
        raise RuntimeError(f"解码失败 {path.name}:{r.stderr.decode('utf-8', 'replace')[-300:]}")
    return r.stdout


def _fmt(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:06.3f}" if h >= 1 else f"{int(m):02d}:{s:06.3f}"


def _default_tts(proj: Path):
    from modules.genmedia import generate_tts

    def run(text: str, sample: Path, out: Path):
        generate_tts(text, str(out), project=str(proj), clone_from=str(sample))
    return run


def synth(proj: Path, ep: str, overrides: dict[str, str] | None = None, *, gap: float = GAP_S, force: bool = False,
          tts=None, engine=None, log=print) -> dict:
    """逐句克隆 → 拼母带 → 人物稿 + 台账。tts(text, sample_path, out_path) 可注入(测试);任一句失败不中断,失败则不出母带。"""
    proj = Path(proj)
    p = plan(proj, ep, overrides, engine)
    if p["issues"]:
        return {**p, "ok": False}
    if not shutil.which("ffmpeg"):
        return {**p, "ok": False, "issues": ["宿主缺 ffmpeg(brew install ffmpeg)"]}
    master = proj / "assets" / "audio" / "master" / f"{ep}.mp3"
    old = _read_json(proj / MANIFEST_REL)
    if master.is_file() and not force and _sha(master) != old.get("master_sha256"):
        return {**p, "ok": False, "issues": [f"{_rel(proj, master)} 已存在且不是克隆配音产出的(可能是用户上传的录音);"
                                             "确认要用克隆声音覆盖它再加 --force"]}
    if overrides:   # --voice 写回映射,下游 dh0-ingest 按它命名人物
        _write_json(proj / CAST_REL, {"speakers": {k: {"sample": v} for k, v in p["cast"].items()}})
    tts = tts or _default_tts(proj)
    ldir = proj / "assets" / "audio" / "voice" / "clone" / ep
    (ldir / "_raw").mkdir(parents=True, exist_ok=True)
    try:
        from modules import dialogue_tts as dt
    except ImportError:
        import dialogue_tts as dt
    lines, n_new, n_fail = [], 0, 0
    old_by = {(e.get("idx"), e.get("key")): e for e in old.get("lines") or []}
    for r in p["lines"]:
        f = f"l{r['idx']:04d}.wav"
        e = {k: r[k] for k in ("idx", "line", "speaker", "text", "key")}
        e["file"] = _rel(proj, ldir / f)
        prev = old_by.get((r["idx"], r["key"]))
        if not force and r["status"] == "ok" and prev:
            e.update(status="ok", duration_s=prev.get("duration_s"), generated_at=prev.get("generated_at"))
            lines.append(e)
            continue
        raw = ldir / "_raw" / f"l{r['idx']:04d}.mp3"
        try:
            tts(r["text"], proj / p["voices"][r["speaker"]]["sample"], raw)
            dt.trim_file(raw, ldir / f, max_pause=0.0)
            dur = probe_duration(ldir / f)
            if not dur:
                raise RuntimeError("合成结果读不到时长")
            e.update(status="ok", duration_s=round(dur, 3), generated_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
            n_new += 1
            log(f"[OK] 第 {r['idx']} 句 {r['speaker']} {dur:.2f}s")
        except Exception as exc:  # noqa: BLE001
            n_fail += 1
            e.update(status="failed", error=str(exc)[-400:])
            log(f"[WARN] 第 {r['idx']} 句 {r['speaker']} 合成失败:{exc}")
        lines.append(e)
    manifest = {"schema": "voice_clone@v1", "episode": ep, "master_origin": "tts_cloned",
                "provider": p["provider"], "engine": p["engine"], "script": p["script"],
                "script_sha256": p["script_sha256"], "voices": p["voices"], "gap_s": gap, "lead_s": LEAD_S,
                "tail_s": TAIL_S, "lines": lines, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if n_fail:
        manifest.update(master="", master_sha256="", transcript="")
        _write_json(proj / MANIFEST_REL, manifest)
        return {**p, "ok": False, "lines": lines, "synthesized": n_new, "failed": n_fail,
                "issues": [f"{n_fail} 句合成失败,母带未拼;修好后重跑 synth(已成功的句子不会重出)"]}
    # 拼母带:句间留 gap 秒静音,首尾各留一点;时间按采样点计,人物稿边界取相邻两句的静音正中
    bps = 2
    pcm, spans = bytearray(b"\0" * int(LEAD_S * SR) * bps), []
    for i, e in enumerate(lines):
        if i:
            pcm += b"\0" * int(gap * SR) * bps
        start = len(pcm) / bps / SR
        pcm += _decode_pcm(proj / e["file"])
        spans.append((start, len(pcm) / bps / SR))
    pcm += b"\0" * int(TAIL_S * SR) * bps
    master.parent.mkdir(parents=True, exist_ok=True)
    tmp = master.with_name(master.stem + ".tmp.mp3")
    enc = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "s16le", "-ar", str(SR), "-ac", "1", "-i", "-",
                          "-c:a", "libmp3lame", "-b:a", "192k", str(tmp)], input=bytes(pcm), capture_output=True)
    if enc.returncode:
        raise RuntimeError(f"母带编码失败:{enc.stderr.decode('utf-8', 'replace')[-300:]}")
    tmp.replace(master)
    total = probe_duration(master) or len(pcm) / bps / SR
    bounds = [0.0] + [(spans[i - 1][1] + spans[i][0]) / 2 for i in range(1, len(spans))] + [total]
    rows = []
    for i, e in enumerate(lines):
        e.update(start=round(spans[i][0], 3), end=round(spans[i][1], 3),
                 seg_start=round(bounds[i], 3), seg_end=round(bounds[i + 1], 3))
        rows.append(f"[{_fmt(bounds[i])}-{_fmt(bounds[i + 1])}] {e['speaker']}：{e['text']}")
    tr = proj / TRANSCRIPT_REL
    tr.parent.mkdir(parents=True, exist_ok=True)
    tr.write_text("\n".join(rows) + "\n", encoding="utf-8")
    manifest.update(master=_rel(proj, master), master_sha256=_sha(master), master_duration_s=round(total, 3),
                    transcript=TRANSCRIPT_REL, transcript_sha256=_sha(tr))
    _write_json(proj / MANIFEST_REL, manifest)
    log(f"[OK] 母带 {_rel(proj, master)} {total:.2f}s,{len(lines)} 句(新合成 {n_new});人物稿 {TRANSCRIPT_REL}")
    return {**p, "ok": True, "lines": lines, "synthesized": n_new, "failed": 0,
            "master": _rel(proj, master), "master_duration_s": round(total, 3)}


def check(proj: Path, ep: str) -> dict:
    """机检 voice_clone_ok。"""
    proj = Path(proj)
    errors, warnings = [], []
    m = _read_json(proj / MANIFEST_REL)
    if not m:
        return {"check": "voice_clone_ok", "ok": False, "errors": [f"缺台账 {MANIFEST_REL}(先跑 synth)"], "warnings": []}
    script = proj / (m.get("script") or SCRIPT_REL)
    if not script.is_file() or _sha(script) != m.get("script_sha256"):
        errors.append(f"台词稿 {m.get('script')} 在合成后改过或不见了,重跑 synth")
    for name, v in (m.get("voices") or {}).items():
        sp = proj / (v.get("sample") or "")
        if not sp.is_file() or _sha(sp) != v.get("sample_sha256"):
            errors.append(f"{name} 的样本 {v.get('sample')} 在合成后换过或不见了,重跑 synth")
    lines = m.get("lines") or []
    bad = [e.get("idx") for e in lines if e.get("status") != "ok" or not (proj / (e.get("file") or "")).is_file()]
    if not lines or bad:
        errors.append(f"有句子没合成好:{bad or '台账为空'}")
    master = proj / (m.get("master") or f"assets/audio/master/{ep}.mp3")
    if not m.get("master") or not master.is_file():
        errors.append("母带不存在(synth 没走完)")
    elif _sha(master) != m.get("master_sha256"):
        errors.append(f"{_rel(proj, master)} 与台账指纹不一致(合成后被改动或被别的录音覆盖)")
    tr = proj / (m.get("transcript") or TRANSCRIPT_REL)
    if not tr.is_file():
        errors.append(f"缺人物稿 {TRANSCRIPT_REL}")
    elif not errors:
        try:
            from modules.dialogue_video import parse_labeled_transcript
        except ImportError:
            from dialogue_video import parse_labeled_transcript
        rows = parse_labeled_transcript(tr.read_text(encoding="utf-8"))
        dur = probe_duration(master) or float(m.get("master_duration_s") or 0)
        if len(rows) != len(lines):
            errors.append(f"人物稿 {len(rows)} 段,台账 {len(lines)} 句,对不上")
        if rows and (abs(rows[0]["start"]) > 0.05 or abs(rows[-1]["end"] - dur) > 0.05):
            errors.append(f"人物稿没有覆盖整条母带 0–{dur:.3f}s")
        for a, b in zip(rows, rows[1:]):
            if abs(a["end"] - b["start"]) > 0.05:
                errors.append(f"人物稿 {_fmt(a['end'])} 处不连续")
                break
        for r, e in zip(rows, lines):
            if r["speaker"] != e.get("speaker") or r["text"] != e.get("text"):
                errors.append(f"人物稿第 {e.get('idx')} 段与台账不一致")
                break
    return {"check": "voice_clone_ok", "ok": not errors, "errors": errors, "warnings": warnings}


def _print_plan(p: dict) -> None:
    print(f"渠道:{p.get('engine') or '-'}")
    for name in p.get("speakers") or []:
        v = (p.get("voices") or {}).get(name)
        if not v:
            print(f"  {name}:(没有样本)")
        else:
            print(f"  {name}:{v['sample']}" + (f"({v['duration_s']}s)" if v.get("duration_s") else ""))
    todo = [x for x in p.get("lines") or [] if x["status"] != "ok"]
    print(f"台词 {len(p.get('lines') or [])} 句,待合成 {len(todo)} 句")
    for note in p.get("notes") or []:
        print(f"[NOTE] {note}")
    for w in p.get("warnings") or []:
        print(f"[WARN] {w}")
    for i in p.get("issues") or []:
        print(f"[FAIL] {i}")


def _overrides(values: list[str]) -> dict[str, str]:
    out = {}
    for v in values or []:
        if "=" not in v:
            raise SystemExit(f"--voice 格式:说话人=refs/voices/<文件>,收到 {v!r}")
        k, path = v.split("=", 1)
        out[k.strip()] = path.strip()
    return out


def main(argv=None) -> int:
    def configure(ap):
        ap.add_argument("cmd", choices=("plan", "synth", "check"))
        ap.add_argument("--voice", action="append", default=[], metavar="说话人=样本路径",
                        help="说话人与克隆样本的对应(相对项目根,如 主持人=refs/voices/host.mp3),可重复")
        ap.add_argument("--gap", type=float, default=GAP_S, help=f"句间静音秒数(缺省 {GAP_S})")
        ap.add_argument("--force", action="store_true", help="全部重出;母带已存在且不是本 CLI 产出的也覆盖")
        ap.add_argument("--json", action="store_true")
    args, proj = parse_args(__doc__.splitlines()[0], argv=argv, configure=configure)
    ov = _overrides(args.voice)
    if args.cmd == "check":
        r = check(proj, args.ep)
        print(json.dumps(r, ensure_ascii=False, indent=1) if args.json else
              ("PASS voice_clone_ok" if r["ok"] else "FAIL voice_clone_ok\n" + "\n".join(f"  - {e}" for e in r["errors"])))
        return 0 if r["ok"] else 1
    if args.cmd == "plan":
        r = plan(proj, args.ep, ov)
        print(json.dumps(r, ensure_ascii=False, indent=1)) if args.json else _print_plan(r)
        return 2 if r.get("missing_script") else (1 if r["issues"] else 0)
    if not 0 <= args.gap <= 3:
        raise SystemExit("--gap 须在 0–3 秒之间")
    r = synth(proj, args.ep, ov, gap=args.gap, force=args.force)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
    elif r["issues"]:
        _print_plan(r)
    return 2 if r.get("missing_script") else (0 if r["ok"] else 1)


if __name__ == "__main__":
    sys.exit(main())
