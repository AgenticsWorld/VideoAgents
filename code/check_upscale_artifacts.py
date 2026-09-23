#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_upscale_artifacts.py — 超分伪影机检 upscale_artifact_ok(WORKFLOW.md §7B,2026-09-23;
**宿主 CLI,Agent 只准调用,禁止复制/改写到项目 code/**)。

用法:
  python3 code/check_upscale_artifacts.py --project <slug> --ep ep06 --group grp004 [--source <草稿mp4>]
  python3 code/check_upscale_artifacts.py --input <终版mp4> --source <草稿mp4> [--kind interp|generative]

判据按 `grpNNN.meta.json#upscale` 的渠道自动选(--kind 可强制):
  interp     插值型(ffmpeg 渠道 / 降级 ffmpeg):终版下采样回源尺寸后应贴合源——
             halo   强边邻域超出源局部极值的像素占比(振铃/过锐)      ≤ 0.01
             smear  往返后高频能量 / 源高频能量(涂抹)               ≥ 0.25
             flicker 终版相邻帧亮度差 / 源相邻帧亮度差(闪烁)          ≤ 1.6
  generative 生成型(comfyui SeedVR2 / minimax Regenerate-2K / volcengine 样片原片):模型会画出源里
             没有的细节,往返 halo 必然超阈(fengshen3 grp004 反例 halo=0.117),故改看——
             flicker 同上                                            ≤ 1.6
             ssim   终版下采样回源尺寸与源逐帧灰度 SSIM 均值(结构一致)  ≥ 0.80
             minssim 单帧最低 SSIM(局部整帧跑偏)                      ≥ 0.65
             同时时长 / fps 必须与源一致(音轨若源有则终版须有)。

源 clip 缺省找 `assets/clips/<ep>/archive/upscale_*_<grp>/<grp>.480p.mp4`(upscale 工位归档口径)或
`archive/*/<grp>.mp4`;找不到须 --source 指定。抽 N 帧(默认 10)按时刻对齐比较。
输出:一行摘要 + 一行 JSON(供回执 artifact_sample_check 原样引用);FAIL 退出码 1,输入缺失退出码 2。
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from _common import DATA_DIR  # noqa: E402  (副作用:modules/ 入 sys.path)

HALO_THRESH = 0.01
SMEAR_THRESH = 0.25
FLICKER_RATIO = 1.6
SSIM_MEAN_THRESH = 0.80
SSIM_MIN_THRESH = 0.65
GENERATIVE_METHODS = {"comfyui", "minimax", "volcengine"}


def _probe(path: str) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error",
                          "-show_entries", "stream=codec_type,width,height,r_frame_rate",
                          "-show_entries", "format=duration", "-of", "json", path],
                         capture_output=True, text=True, timeout=60)
    info = json.loads(out.stdout or "{}")
    meta = {"width": 0, "height": 0, "fps": 0.0, "has_audio": False,
            "duration": float((info.get("format") or {}).get("duration") or 0)}
    for st in info.get("streams") or []:
        if st.get("codec_type") == "audio":
            meta["has_audio"] = True
        elif st.get("codec_type") == "video" and not meta["width"]:
            meta["width"] = int(st.get("width") or 0)
            meta["height"] = int(st.get("height") or 0)
            num, _, den = (st.get("r_frame_rate") or "0/1").partition("/")
            meta["fps"] = float(num) / float(den) if den and float(den) else 0.0
    return meta


def _extract(path: str, outdir: str, duration: float, n: int) -> list:
    frames = []
    for k in range(n):
        t = duration * (k + 0.5) / n
        p = os.path.join(outdir, f"f{k:03d}.png")
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                        "-ss", f"{t:.4f}", "-i", path, "-frames:v", "1", p], check=True)
        frames.append(p)
    return frames


def _gray(p: str, size=None) -> np.ndarray:
    im = Image.open(p).convert("L")
    if size:
        im = im.resize(size, Image.LANCZOS)
    return np.asarray(im, dtype=np.float32)


def _grad_energy(img: np.ndarray) -> float:
    gy, gx = np.gradient(img)
    return float((gy ** 2 + gx ** 2).sum())


def _local_minmax(img: np.ndarray, k: int = 3):
    """3×3 邻域 min/max(纯 numpy,免 scipy)。"""
    pad = k // 2
    padded = np.pad(img, pad, mode="edge")
    stacks = [padded[dy:dy + img.shape[0], dx:dx + img.shape[1]]
              for dy in range(k) for dx in range(k)]
    st = np.stack(stacks)
    return st.min(axis=0), st.max(axis=0)


def _ssim(a: np.ndarray, b: np.ndarray, win: int = 8) -> float:
    """分块(win×win)SSIM 均值(纯 numpy,灰度 0-255)。"""
    h = (a.shape[0] // win) * win
    w = (a.shape[1] // win) * win
    if h == 0 or w == 0:
        return 1.0
    a = a[:h, :w].reshape(h // win, win, w // win, win).transpose(0, 2, 1, 3).reshape(-1, win * win)
    b = b[:h, :w].reshape(h // win, win, w // win, win).transpose(0, 2, 1, 3).reshape(-1, win * win)
    mu_a, mu_b = a.mean(1), b.mean(1)
    va, vb = a.var(1), b.var(1)
    cov = ((a - mu_a[:, None]) * (b - mu_b[:, None])).mean(1)
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    s = ((2 * mu_a * mu_b + c1) * (2 * cov + c2)) / ((mu_a ** 2 + mu_b ** 2 + c1) * (va + vb + c2))
    return float(s.mean())


def _find_source(base: Path, ep: str, grp: str) -> str:
    adir = base / "assets" / "clips" / ep / "archive"
    if not adir.is_dir():
        return ""
    cands = []
    for d in sorted(adir.iterdir(), reverse=True):
        if not d.is_dir() or not d.name.startswith("upscale_"):
            continue
        for name in (f"{grp}.480p.mp4", f"{grp}.mp4"):
            f = d / name
            if f.is_file():
                cands.append(f)
        for f in d.glob(f"{grp}.*.mp4"):
            if f not in cands:
                cands.append(f)
    return str(cands[0]) if cands else ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", default=os.environ.get("VIDEOAGENTS_PROJECT", ""))
    ap.add_argument("--ep", default="")
    ap.add_argument("--group", default="")
    ap.add_argument("--input", default="", help="终版 clip(缺省 assets/clips/<ep>/<grp>.mp4)")
    ap.add_argument("--source", default="", help="草稿 clip(缺省从 archive/upscale_* 找)")
    ap.add_argument("--kind", choices=["auto", "interp", "generative"], default="auto")
    ap.add_argument("--frames", type=int, default=10)
    args = ap.parse_args()

    base = DATA_DIR / "projects" / args.project if args.project else None
    up_path = args.input
    if not up_path:
        if not (base and args.ep and args.group):
            print("需要 --input,或 --project/--ep/--group", file=sys.stderr)
            return 2
        up_path = str(base / "assets" / "clips" / args.ep / f"{args.group}.mp4")
    src_path = args.source or (_find_source(base, args.ep, args.group) if base and args.ep and args.group else "")
    if not Path(up_path).is_file():
        print(f"终版 clip 不存在:{up_path}", file=sys.stderr)
        return 2
    if not src_path or not Path(src_path).is_file():
        print(f"找不到草稿源 clip(--source 指定;或归档到 assets/clips/<ep>/archive/upscale_*_{args.group or 'grp'}/)",
              file=sys.stderr)
        return 2

    meta = {}
    mp = Path(up_path).with_suffix(".meta.json")
    if mp.is_file():
        try:
            meta = json.loads(mp.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    up = meta.get("upscale") if isinstance(meta, dict) and isinstance(meta.get("upscale"), dict) else {}
    method = str(up.get("method") or up.get("provider") or "")
    kind = args.kind
    if kind == "auto":
        kind = "generative" if method in GENERATIVE_METHODS else "interp"
        if not up:
            print("[warn] meta.json 无 upscale 段(未经宿主 CLI 超分?),按插值型判据", file=sys.stderr)

    src_m, up_m = _probe(src_path), _probe(up_path)
    spec = {
        "fps_unchanged": round(src_m["fps"], 2) == round(up_m["fps"], 2),
        "duration_unchanged": abs(src_m["duration"] - up_m["duration"]) <= max(0.06, 1.5 / max(src_m["fps"], 1)),
        "audio_preserved": (not src_m["has_audio"]) or up_m["has_audio"],
        "upscaled": up_m["width"] * up_m["height"] > src_m["width"] * src_m["height"],
    }

    tmp = tempfile.mkdtemp(prefix="upscale_chk_")
    sd, ud = os.path.join(tmp, "src"), os.path.join(tmp, "up")
    os.makedirs(sd)
    os.makedirs(ud)
    n = max(3, args.frames)
    src_f = _extract(src_path, sd, src_m["duration"], n)
    up_f = _extract(up_path, ud, up_m["duration"], n)
    size = (src_m["width"], src_m["height"])

    halos, smears, ssims = [], [], []
    for sp, upf in zip(src_f, up_f):
        src = _gray(sp)
        back = _gray(upf, size)
        if kind == "interp":
            lmin, lmax = _local_minmax(src)
            strong = (lmax - lmin) > 40
            halo = (back > lmax + 6.0) | (back < lmin - 6.0)
            halos.append(float((halo & strong).sum()) / float(strong.sum()) if strong.sum() else 0.0)
            ge = _grad_energy(src)
            smears.append(_grad_energy(back) / ge if ge else 1.0)
        else:
            ssims.append(_ssim(src, back))

    def _flicker(frames):
        lum = [float(_gray(p).mean()) for p in frames]
        return float(np.max(np.abs(np.diff(lum)))) if len(lum) > 1 else 0.0
    sf, uf = _flicker(src_f), _flicker(up_f)
    flicker_ratio = (uf / sf) if sf > 0.5 else (0.0 if uf <= 1.0 else uf)

    res = {"kind": kind, "method": method or None, "frames_checked": n,
           "source": Path(src_path).name, "output": Path(up_path).name,
           "source_resolution": f"{src_m['width']}x{src_m['height']}",
           "output_resolution": f"{up_m['width']}x{up_m['height']}",
           "flicker_ratio": round(flicker_ratio, 3), "spec": spec}
    checks = {"flicker": flicker_ratio <= FLICKER_RATIO}
    if kind == "interp":
        res["halo_ratio"] = round(float(np.mean(halos)), 4)
        res["smear_energy_ratio"] = round(float(np.mean(smears)), 3)
        checks["halo"] = res["halo_ratio"] <= HALO_THRESH
        checks["smear"] = res["smear_energy_ratio"] >= SMEAR_THRESH
    else:
        res["ssim_mean"] = round(float(np.mean(ssims)), 4)
        res["ssim_min"] = round(float(np.min(ssims)), 4)
        checks["ssim_mean"] = res["ssim_mean"] >= SSIM_MEAN_THRESH
        checks["ssim_min"] = res["ssim_min"] >= SSIM_MIN_THRESH
    checks.update({f"spec_{k}": v for k, v in spec.items()})
    failed = [k for k, v in checks.items() if not v]
    res["checks"] = checks
    res["failed"] = failed
    res["artifacts_found"] = len(failed)
    res["verdict"] = "PASS" if not failed else "FAIL"
    tag = f"{args.ep}/{args.group}" if args.group else Path(up_path).name
    nums = (f"halo={res.get('halo_ratio')} smear={res.get('smear_energy_ratio')}" if kind == "interp"
            else f"ssim={res.get('ssim_mean')}/min {res.get('ssim_min')}")
    print(f"{tag}: kind={kind} method={method or '-'} {nums} flicker={res['flicker_ratio']} "
          f"-> {res['verdict']}" + (f" ({', '.join(failed)})" if failed else ""))
    print(json.dumps(res, ensure_ascii=False))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
