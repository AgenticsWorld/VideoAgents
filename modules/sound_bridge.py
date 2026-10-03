"""声桥(sound bridge,J-cut / L-cut,2026-10-03;过场设计四期改版,docs/transition_design.md「四期」)。

原「音先入 audio_lead_s」规约写的是「本组原生轨从 cum_start_s − lead 起铺」= 整条原生轨提前 lead 秒,对白组会整组口型错位——作废。
新契约 `transition_in.sound_bridge {kind: j|l, s ∈ (0,1.5], carry: bed|line}`(存量 audio_lead_s 由 check_generation_groups.sound_bridge_of
归一为 {kind: j, carry: bed}):
- J 声先入:下组声音先到、压在本组尾画面上;切过去后下组原生轨仍从切点同步起(不提前)。
- L 声延续:本组声音拖过切点、压在下组首画面上;下组原生轨前 s 秒从 −12 dB 渐强。
- carry=bed:桥声是**去人声底床**(环境声 / 音乐感收尾),由本模块按混音取源文件切出:J = 下组取源音频开头 s 秒**镜像(时间反转)**后
  放在切点前(切点处与下组 t=0 连续,噪声态环境声反转听不出);L = 本组取源音频结尾 s 秒镜像放在切点后。去人声走 modules/audio_separation
  (MDX-Net ONNX),模型不可用回落低通 4 kHz + −6 dB 并 WARN。J 文件 s 秒渐强、L 文件 s 秒渐弱,两端 40 ms 淡化;48 kHz 立体声 wav。
- carry=line:不出文件——桥声是切点旁的画外句(声画分离一期),由 offscreen_lines 的窗口放宽(J:下组首镜画外句 offset_s 可为负;
  L:本组末镜画外句窗口 end + s)与 mix_basis sources.offscreen_lines[] 的 t0 承担。

产物 edit/<ep>/sound_bridges/<B-id>.j.wav|.l.wav + manifest.json(逐边界 kind/s/carry/src/file/status/fingerprint);幂等:取源文件与
kind/s/carry 不变不重出。mix_basis sources 的 boundaries[].sound_bridge 带 file/status;改了声桥或未 build = mix_basis_current / sound_bridge_built FAIL。
BGM / 旁白 / wav 总长都不变、不进 timemap(render_transitions 不动画面)。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

try:
    import mix_manifest as mb
    import post_plan as pp
except ImportError:  # 服务端以 modules.* 包路径导入时
    from modules import mix_manifest as mb
    from modules import post_plan as pp

SCHEMA = "sound_bridge/v1"
DIR_REL = "edit/{ep}/sound_bridges"
MANIFEST = "manifest.json"
EDGE_FADE_S = 0.04
L_DUCK_DB = -12.0        # L 型:下组原生轨前 s 秒从该电平渐强到 0(写进 sources 给 audio-mixing)
FALLBACK_AF = "lowpass=f=4000,volume=-6dB"   # 去人声模型不可用时的回落处理


def _read(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
    except (ValueError, OSError):
        return None


def bridge_dir(proj: Path, ep: str) -> Path:
    return Path(proj) / DIR_REL.format(ep=ep)


def load_manifest(proj: Path, ep: str) -> dict | None:
    return _read(bridge_dir(proj, ep) / MANIFEST)


def _file_fp(p: Path | None) -> str:
    if not p or not p.is_file():
        return ""
    st = p.stat()
    return hashlib.sha256(f"{p.name}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:12]


def expected(proj: Path, ep: str, rows: list[dict] | None = None, bounds: list[dict] | None = None) -> list[dict]:
    """需要声桥的边界清单(从混音取源行与边界层推导):[{id, from_group, to_group, kind, s, carry, src_from, src_to, cum_start_s, key}]。
    key = 取源文件指纹 + kind/s/carry(构建幂等依据)。"""
    proj = Path(proj)
    if rows is None:
        plan = pp.load_plan(proj, ep)
        basis = mb.current_basis(proj, ep, plan)
        bounds = mb.boundary_layer(proj, ep, basis)
        rows = mb.with_timing(proj, ep, basis, bounds)
    elif bounds is None:
        bounds = mb.boundary_layer(proj, ep, rows)
    by = {r["group_id"]: r for r in rows}
    out = []
    for b in bounds:
        sb = b.get("sound_bridge") if isinstance(b.get("sound_bridge"), dict) else None
        if not sb:
            continue
        ra, rb = by.get(b["from_group"]) or {}, by.get(b["to_group"]) or {}
        src_from = str(ra.get("src") or ""), str(rb.get("src") or "")
        src_file = (proj / src_from[1]) if sb["kind"] == "j" else (proj / src_from[0])
        key = hashlib.sha256("|".join([sb["kind"], f"{float(sb['s']):.3f}", sb["carry"], _file_fp(src_file if src_file.is_file() else None),
                                       str(src_file.relative_to(proj)) if src_file.is_file() else ""]).encode()).hexdigest()[:16]
        out.append({"id": f"{b['from_group']}-{b['to_group']}", "from_group": b["from_group"], "to_group": b["to_group"],
                    "kind": sb["kind"], "s": float(sb["s"]), "carry": sb["carry"], "src_from": src_from[0], "src_to": src_from[1],
                    "src_duration_from": ra.get("duration_s"), "src_duration_to": rb.get("duration_s"),
                    "cum_start_s": rb.get("cum_start_s"), "key": key})
    return out


def fingerprint(manifest: dict | None) -> str | None:
    """已构建声桥的指纹(进 mix.json):逐边界 kind/s/carry/file key;无台账或无边界 = None。"""
    if not isinstance(manifest, dict):
        return None
    rows = [[e.get("id"), e.get("kind"), e.get("s"), e.get("carry"), e.get("key"), e.get("status")]
            for e in manifest.get("bridges") or [] if isinstance(e, dict)]
    if not rows:
        return None
    return hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def is_stale(proj: Path, ep: str, manifest: dict | None = None, rows: list[dict] | None = None, bounds: list[dict] | None = None) -> bool:
    """台账是否落后于当前取源 / 声桥设计(新增 / 删边界 / 改 kind-s-carry / 取源版本变)。"""
    man = manifest if manifest is not None else load_manifest(proj, ep)
    exp = expected(proj, ep, rows, bounds)
    if not exp:
        return bool(man and (man.get("bridges") or []))
    if not man:
        return True
    by = {e.get("id"): e for e in man.get("bridges") or [] if isinstance(e, dict)}
    for e in exp:
        m = by.get(e["id"])
        if not m or m.get("key") != e["key"]:
            return True
        if e["carry"] == "bed" and not (bridge_dir(proj, ep) / str(m.get("file") or "")).is_file():
            return True
    return len(by) != len(exp)


def _ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def _probe_duration(p: Path) -> float | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    r = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(p)],
                       capture_output=True, text=True, timeout=60)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return None


def _separated_bed(seg_src: Path, dst: Path, log) -> dict:
    """seg_src(已切好的片段)→ dst 去人声底床。模型不可用回落低通 + 衰减(status=fallback)。"""
    try:
        try:
            import audio_separation as asep
        except ImportError:
            from modules import audio_separation as asep
        from _common import DATA_DIR
        sess = asep.load_session(asep.ensure_model(DATA_DIR, log))
        mix = asep.decode(seg_src)
        if mix.shape[1] < asep.SR // 20:
            raise RuntimeError("片段太短")
        bed, _voice = asep.separate(sess, mix)
        asep.write_wav(dst, bed)
        return {"status": "removed", "model": asep.MODEL_FILE}
    except Exception as e:  # noqa: BLE001
        ff = _ffmpeg()
        if not ff:
            raise RuntimeError("缺 ffmpeg") from None
        r = subprocess.run([ff, "-hide_banner", "-nostats", "-loglevel", "error", "-y", "-i", str(seg_src), "-af", FALLBACK_AF,
                            "-c:a", "pcm_s16le", str(dst)], capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            raise RuntimeError(f"回落处理失败:{(r.stderr or '').strip()[-200:]}") from None
        return {"status": "fallback", "reason": str(e)[-200:]}


def render_bridge(src: Path, dst: Path, kind: str, s: float, *, src_duration: float | None = None, log=print, workdir: Path | None = None) -> dict:
    """从取源文件切出 s 秒(J=开头 / L=结尾),去人声,镜像(时间反转),加渐强 / 渐弱与两端淡化,写 48k 立体声 wav。返回 {status, model?, reason?, duration_s}。"""
    ff = _ffmpeg()
    if not ff:
        raise RuntimeError("缺 ffmpeg,无法构建声桥")
    workdir = workdir or dst.parent
    seg = workdir / (dst.stem + ".seg.wav")
    bed = workdir / (dst.stem + ".bed.wav")
    total = src_duration if src_duration else _probe_duration(src)
    if kind == "j":
        cut = ["-ss", "0", "-t", f"{s:.3f}"]
    else:
        if not total:
            raise RuntimeError("读不到取源文件时长(L 型要切结尾)")
        cut = ["-ss", f"{max(0.0, total - s):.3f}", "-t", f"{s:.3f}"]
    r = subprocess.run([ff, "-hide_banner", "-nostats", "-loglevel", "error", "-y", *cut, "-i", str(src), "-vn", "-ac", "2",
                        "-c:a", "pcm_s16le", str(seg)], capture_output=True, text=True, timeout=300)
    if r.returncode != 0 or not seg.is_file():
        raise RuntimeError(f"切片失败:{(r.stderr or '').strip()[-200:]}")
    info = _separated_bed(seg, bed, log)
    # 镜像:J 把开头 s 秒反转放在切点前(切点处与下组 t=0 连续);L 把结尾 s 秒反转放在切点后(切点处与本组末样本连续)
    fade = f"afade=t=in:st=0:d={s:.3f}" if kind == "j" else f"afade=t=out:st=0:d={s:.3f}"
    edge_in = f"afade=t=in:st=0:d={EDGE_FADE_S}"
    edge_out = f"afade=t=out:st={max(0.0, s - EDGE_FADE_S):.3f}:d={EDGE_FADE_S}"
    af = ",".join(["areverse", fade, edge_in, edge_out, "aresample=48000", "aformat=channel_layouts=stereo"])
    r = subprocess.run([ff, "-hide_banner", "-nostats", "-loglevel", "error", "-y", "-i", str(bed), "-af", af, "-c:a", "pcm_s16le", str(dst)],
                       capture_output=True, text=True, timeout=300)
    if r.returncode != 0 or not dst.is_file():
        raise RuntimeError(f"声桥渲染失败:{(r.stderr or '').strip()[-200:]}")
    for tmp in (seg, bed):
        tmp.unlink(missing_ok=True)
    info["duration_s"] = round(_probe_duration(dst) or s, 3)
    return info


def build(proj: Path, ep: str, *, force: bool = False, log=print, rows: list[dict] | None = None, bounds: list[dict] | None = None,
          renderer=None) -> dict:
    """构建本集全部声桥文件并写台账(幂等:key 不变且文件在就沿用;carry=line 不出文件)。renderer / rows / bounds 可注入(测试)。"""
    proj = Path(proj)
    exp = expected(proj, ep, rows, bounds)
    bdir = bridge_dir(proj, ep)
    old = load_manifest(proj, ep) or {}
    old_by = {e.get("id"): e for e in old.get("bridges") or [] if isinstance(e, dict)}
    renderer = renderer or render_bridge
    bridges = []
    n_built = n_fail = 0
    if exp:
        bdir.mkdir(parents=True, exist_ok=True)
    for e in exp:
        prev = old_by.get(e["id"]) or {}
        entry = {k: e[k] for k in ("id", "from_group", "to_group", "kind", "s", "carry", "src_from", "src_to", "cum_start_s", "key")}
        if e["carry"] == "line":
            entry.update(status="line", file=None, note="桥声由切点旁的画外句承担(offscreen_lines),不出文件")
            bridges.append(entry)
            continue
        fname = f"{e['id']}.{e['kind']}.wav"
        dst = bdir / fname
        src_rel = e["src_to"] if e["kind"] == "j" else e["src_from"]
        if not src_rel or not (proj / src_rel).is_file():
            n_fail += 1
            entry.update(status="failed", file=None, error=f"取源文件缺失:{src_rel or '(无)'}")
            log(f"[FAIL] {e['id']} 声桥取源文件缺失")
            bridges.append(entry)
            continue
        if not force and prev.get("key") == e["key"] and prev.get("status") in ("built", "fallback") and dst.is_file():
            bridges.append({**prev, **{k: e[k] for k in ("cum_start_s",)}})
            continue
        try:
            info = renderer(proj / src_rel, dst, e["kind"], e["s"],
                            src_duration=e["src_duration_to"] if e["kind"] == "j" else e["src_duration_from"], log=log)
            entry.update(status="fallback" if info.get("status") == "fallback" else "built", file=fname,
                         separation=info.get("status"), model=info.get("model"), duration_s=info.get("duration_s"),
                         built_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
            if info.get("status") == "fallback":
                entry["warning"] = f"去人声模型不可用,回落低通 + 衰减:{info.get('reason') or ''}"
                log(f"[WARN] {e['id']} {entry['warning']}")
            n_built += 1
        except Exception as exc:  # noqa: BLE001
            n_fail += 1
            entry.update(status="failed", file=None, error=str(exc)[-300:])
            log(f"[FAIL] {e['id']} 声桥构建失败:{exc}")
        bridges.append(entry)
    wanted = {e.get("file") for e in bridges if e.get("file")}
    if bdir.is_dir():
        for f in bdir.glob("*.wav"):
            if f.name not in wanted:
                f.unlink(missing_ok=True)
    man = {"schema": SCHEMA, "ep": ep, "generated_by": "modules/sound_bridge.py", "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "l_duck_db": L_DUCK_DB, "bridges": bridges,
           "summary": {"total": len(bridges), "built": sum(1 for b in bridges if b.get("status") == "built"),
                       "fallback": sum(1 for b in bridges if b.get("status") == "fallback"),
                       "line": sum(1 for b in bridges if b.get("status") == "line"),
                       "failed": n_fail, "rendered_now": n_built}}
    man["fingerprint"] = fingerprint(man)
    if exp or old:
        bdir.mkdir(parents=True, exist_ok=True)
        (bdir / MANIFEST).write_text(json.dumps(man, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return man


def rows_for_sources(proj: Path, ep: str, bounds: list[dict]) -> list[dict]:
    """把台账状态并进 mix_basis sources 的 boundaries[].sound_bridge:{kind, s, carry, file(绝对路径|None), status, duck_db(L)}。
    无台账 / 落后 → status=missing|stale(audio-mixing 先跑 build)。"""
    proj = Path(proj)
    man = load_manifest(proj, ep) or {}
    by = {e.get("id"): e for e in man.get("bridges") or [] if isinstance(e, dict)}
    out = []
    for b in bounds:
        sb = b.get("sound_bridge") if isinstance(b.get("sound_bridge"), dict) else None
        if not sb:
            out.append(b)
            continue
        bid = f"{b['from_group']}-{b['to_group']}"
        m = by.get(bid) or {}
        row = dict(sb)
        if sb["carry"] == "line":
            row.update(file=None, status="line")
        elif m.get("file") and (bridge_dir(proj, ep) / m["file"]).is_file():
            row.update(file=str(bridge_dir(proj, ep) / m["file"]), status=m.get("status") or "built", key=m.get("key"))
        else:
            row.update(file=None, status="missing" if not m else (m.get("status") or "missing"))
        if sb["kind"] == "l":
            row["duck_db"] = L_DUCK_DB
        out.append({**b, "sound_bridge": row})
    return out


def check(proj: Path, ep: str, rows: list[dict] | None = None, bounds: list[dict] | None = None) -> dict:
    """sound_bridge_built:每个声桥边界(carry=bed)都有当前 key 的文件;carry=line 只核画外句存在性由 transition_sound_bridge_valid 负责。"""
    proj = Path(proj)
    exp = expected(proj, ep, rows, bounds)
    man = load_manifest(proj, ep)
    errors, warnings = [], []
    if not exp:
        return {"checks": {"sound_bridge_built": "skipped: no sound bridges"}, "errors": [], "warnings": [], "count": 0}
    by = {e.get("id"): e for e in (man or {}).get("bridges") or [] if isinstance(e, dict)}
    for e in exp:
        m = by.get(e["id"])
        if e["carry"] == "line":
            continue
        if not m:
            errors.append(f"{e['id']} sound_bridge_built: 未构建(跑 code/sound_bridge.py build)")
        elif m.get("key") != e["key"]:
            errors.append(f"{e['id']} sound_bridge_built: 取源版本或声桥参数已变(stale),重跑 build")
        elif m.get("status") == "failed" or not m.get("file") or not (bridge_dir(proj, ep) / m["file"]).is_file():
            errors.append(f"{e['id']} sound_bridge_built: 文件缺失 / 构建失败:{m.get('error') or ''}")
        elif m.get("status") == "fallback":
            warnings.append(f"{e['id']} sound_bridge_built: 去人声模型不可用,底床为回落处理(WARN)")
    return {"checks": {"sound_bridge_built": "FAIL" if errors else ("WARN" if warnings else "PASS")},
            "errors": errors, "warnings": warnings, "count": len(exp)}
