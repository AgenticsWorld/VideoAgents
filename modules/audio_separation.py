# -*- coding: utf-8 -*-
"""audio_separation.py — 本机人声分离(后期处理页「音效与声音」的 去人声 / 去环境声,2026-10-01)。

组视频原生声轨是近似单声道的立体声(左右相减整体掉 16–24 dB),ffmpeg 的「消中置」会把对白、音效、环境声一起消掉,
所以去人声只能靠分离模型。这里用 MDX-Net 的 ONNX 权重(UVR-MDX-NET-Inst_HQ_3,约 64 MB),只依赖运行时已带的
onnxruntime + numpy + ffmpeg,不引入 torch:

  模型主输出 = 伴奏(bed:音效 / 环境声 / 配乐),人声 voice = 原声 − bed;
  去人声   remove_vocals   → bed + g·voice
  去环境声 remove_ambience → voice + g·bed          (g 由处方参数 keep_db 决定,≤ -60 dB 视为完全去掉)

模型首次使用时下载到 $VIDEOAGENTS_DATA_DIR/models/audio-separation/(sha256 校验);下载地址可用环境变量
VIDEOAGENTS_SEPARATION_MODEL_URL 换成镜像,也可以手工把文件放进该目录。只由宿主 CLI code/post_apply.py 调用
(本身就是服务端拉起的子进程,模型崩溃不影响服务)。
"""
from __future__ import annotations

import hashlib
import math
import os
import subprocess
import tempfile
import urllib.request
from pathlib import Path

import numpy as np

MODEL_FILE = "UVR-MDX-NET-Inst_HQ_3.onnx"
MODEL_URL = "https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/" + MODEL_FILE
MODEL_SHA256 = "317554b07fe1ea5279a77f2b1520a41ea4b93432560c4ffd08792c30fddf9adc"
MODEL_SIZE_MB = 64
MODEL_SUBDIR = Path("models") / "audio-separation"

# 取自 UVR model_data(按模型哈希 55657dd70583b0fedfba5f67df11d711 核对):主输出 = Instrumental
SR = 44100
N_FFT, HOP, DIM_F, DIM_T, COMPENSATE = 6144, 1024, 3072, 256, 1.022
N_BINS = N_FFT // 2 + 1
CHUNK = HOP * (DIM_T - 1)
TRIM = N_FFT // 2
GEN = CHUNK - 2 * TRIM
WIN = np.hanning(N_FFT + 1)[:-1]                 # 周期 hann,同 torch.hann_window(periodic=True)

KEEP_FLOOR_DB = -60.0                            # keep_db ≤ 此值 = 完全去掉
RAMP_S = 0.03                                    # 时间段两端的渐变,避免接缝爆音
KINDS = ("remove_vocals", "remove_ambience")


class SeparationError(RuntimeError):
    pass


# ---------------------------------------------------------------- 模型
def model_path(data_dir: Path) -> Path:
    return Path(data_dir) / MODEL_SUBDIR / MODEL_FILE


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def ensure_model(data_dir: Path, log=None) -> Path:
    """模型就位则直接返回;否则下载(.part → 校验 → 改名)。失败抛 SeparationError 并说明手工放置的位置。"""
    dst = model_path(data_dir)
    if dst.is_file() and dst.stat().st_size > (1 << 20):
        return dst
    url = os.environ.get("VIDEOAGENTS_SEPARATION_MODEL_URL") or MODEL_URL
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(dst.name + ".part")
    if log:
        log(f"首次使用:下载人声分离模型 {MODEL_FILE}(约 {MODEL_SIZE_MB} MB)…")
    try:
        with urllib.request.urlopen(url, timeout=60) as resp, part.open("wb") as f:
            total = int(resp.headers.get("Content-Length") or 0)
            done, mark = 0, 0
            for blk in iter(lambda: resp.read(1 << 20), b""):
                f.write(blk)
                done += len(blk)
                if log and total and done * 4 // total > mark:
                    mark = done * 4 // total
                    log(f"下载 {done >> 20}/{total >> 20} MB")
        if _sha256(part) != MODEL_SHA256:
            raise SeparationError("模型文件校验不通过(下载不完整或来源不对)")
        os.replace(part, dst)
    except Exception as e:  # noqa: BLE001
        part.unlink(missing_ok=True)
        raise SeparationError(f"人声分离模型下载失败:{e};可手工下载 {url} 放到 {dst}"
                              "(或设环境变量 VIDEOAGENTS_SEPARATION_MODEL_URL 指向镜像)") from None
    return dst


def load_session(path: Path):
    try:
        import onnxruntime as ort
    except ImportError:
        raise SeparationError("缺少 onnxruntime(pip install onnxruntime),去人声 / 去环境声无法在本机运行") from None
    opts = ort.SessionOptions()
    opts.log_severity_level = 3
    return ort.InferenceSession(str(path), sess_options=opts, providers=["CPUExecutionProvider"])


# ---------------------------------------------------------------- 音频读写
def decode(src: Path, t0: float | None = None, dur: float | None = None) -> np.ndarray:
    """→ [2, n] float64,44.1 kHz 立体声;t0/dur 只取一段(预览用)。"""
    cmd = ["ffmpeg", "-v", "error", "-nostdin"]
    if t0 is not None:
        cmd += ["-ss", f"{max(0.0, t0):.3f}"]
    cmd += ["-i", str(src)]
    if dur is not None:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-vn", "-ac", "2", "-ar", str(SR), "-f", "f32le", "-"]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600)
    if p.returncode != 0:
        raise SeparationError("读取声轨失败:" + p.stderr.decode("utf-8", "replace")[-400:])
    return np.frombuffer(p.stdout, dtype=np.float32).reshape(-1, 2).T.astype(np.float64)


def write_wav(dst: Path, audio: np.ndarray) -> None:
    pcm = np.clip(audio.T, -1.0, 1.0).astype(np.float32).tobytes()
    p = subprocess.run(["ffmpeg", "-y", "-v", "error", "-nostdin", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "-",
                        "-c:a", "pcm_s16le", str(dst)], input=pcm, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600)
    if p.returncode != 0:
        raise SeparationError("写出声轨失败:" + p.stderr.decode("utf-8", "replace")[-400:])


# ---------------------------------------------------------------- 分离
def stft(x: np.ndarray) -> np.ndarray:
    """[C, CHUNK] → [C, N_BINS, DIM_T] 复数;与 torch.stft(center=True, reflect) 同口径(不归一)。"""
    xp = np.pad(x, ((0, 0), (TRIM, TRIM)), mode="reflect")
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(DIM_T)[:, None]
    return np.fft.rfft(xp[:, idx] * WIN, axis=-1).transpose(0, 2, 1)


def istft(spec: np.ndarray) -> np.ndarray:
    """[C, N_BINS, DIM_T] → [C, CHUNK];窗平方和归一的重叠相加。"""
    frames = np.fft.irfft(spec.transpose(0, 2, 1), n=N_FFT, axis=-1) * WIN
    out = np.zeros((spec.shape[0], N_FFT + HOP * (DIM_T - 1)))
    wsum = np.zeros(out.shape[1])
    for t in range(DIM_T):
        out[:, t * HOP:t * HOP + N_FFT] += frames[:, t]
        wsum[t * HOP:t * HOP + N_FFT] += WIN ** 2
    out /= np.where(wsum > 1e-11, wsum, 1.0)
    return out[:, TRIM:TRIM + CHUNK]


def _to_model(spec: np.ndarray) -> np.ndarray:
    """[2, N_BINS, T] 复数 → [1, 4, DIM_F, T] 实数(ch0_re, ch0_im, ch1_re, ch1_im),最低 3 个频点清零。"""
    x = np.stack([spec[0].real, spec[0].imag, spec[1].real, spec[1].imag])[:, :DIM_F].astype(np.float32)
    x[:, :3] = 0
    return x[None]


def _from_model(y: np.ndarray) -> np.ndarray:
    full = np.zeros((4, N_BINS, DIM_T))
    full[:, :DIM_F] = y[0]
    return np.stack([full[0] + 1j * full[1], full[2] + 1j * full[3]])


def separate(sess, mix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """mix [2, n] → (bed, voice):bed = 去人声后的音效 / 环境声 / 配乐,voice = mix − bed。按 5.8 s 分块,块两端各留半窗重叠。"""
    n = mix.shape[1]
    if n == 0:
        return mix.copy(), mix.copy()
    pad = GEN - n % GEN
    mp = np.concatenate([np.zeros((2, TRIM)), mix, np.zeros((2, pad + TRIM))], axis=1)
    name = sess.get_inputs()[0].name
    outs = []
    for i in range(0, n + pad, GEN):
        y = sess.run(None, {name: _to_model(stft(mp[:, i:i + CHUNK]))})[0]
        outs.append(istft(_from_model(y))[:, TRIM:-TRIM])
    bed = np.concatenate(outs, axis=1)[:, :n] * COMPENSATE
    return bed, mix - bed


# ---------------------------------------------------------------- 处方 → 声轨
def keep_gain(keep_db) -> float:
    try:
        db = float(keep_db)
    except (TypeError, ValueError):
        db = KEEP_FLOOR_DB
    return 0.0 if db <= KEEP_FLOOR_DB else min(1.0, 10 ** (db / 20.0))


def range_mask(n: int, t0: float | None, t1: float | None, offset_s: float = 0.0) -> np.ndarray:
    """处方作用时间段的权重(0–1):整组 = 全 1;时间段 = [t0, t1) 内为 1,两端 RAMP_S 线性渐变。offset_s = 本段音频在组内的起点。"""
    if t0 is None or t1 is None:
        return np.ones(n)
    t = np.arange(n) / SR + offset_s
    ramp = max(RAMP_S, 1.0 / SR)
    return np.clip(np.minimum((t - t0) / ramp, (t1 - t) / ramp), 0.0, 1.0)


def render(mix: np.ndarray, bed: np.ndarray, voice: np.ndarray, recipes: list[dict], offset_s: float = 0.0) -> np.ndarray:
    """按处方顺序把目标声(去人声 / 去环境声)在各自时间段内叠到原声上;后一条在重叠处覆盖前一条。"""
    out = mix.copy()
    for r in recipes:
        g = keep_gain((r.get("params") or {}).get("keep_db"))
        if r.get("kind") == "remove_vocals":
            target = bed + g * voice
        elif r.get("kind") == "remove_ambience":
            target = voice + g * bed
        else:
            raise SeparationError(f"{r.get('kind')} 不是人声分离类做法")
        sc = r.get("scope") or {}
        ranged = sc.get("level") == "range"
        w = range_mask(mix.shape[1], sc.get("t0") if ranged else None, sc.get("t1") if ranged else None, offset_s)
        out = out * (1.0 - w) + target * w
    return out


def level_db(x: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean(x ** 2))) if x.size else 0.0
    return round(20 * math.log10(rms), 1) if rms > 1e-9 else -120.0


def process(src: Path, recipes: list[dict], dst_wav: Path, data_dir: Path, window: tuple[float, float] | None = None, log=None) -> dict:
    """src 的原生声轨按处方处理后写成 dst_wav(44.1 kHz 立体声 PCM)。window=(起点秒, 时长秒) 只处理一段(预览)。
    返回 {mix_db, out_db, voice_db, bed_db, seconds}。"""
    sess = load_session(ensure_model(data_dir, log))
    t0, dur = window if window else (None, None)
    mix = decode(src, t0, dur)
    if mix.shape[1] < SR // 10:
        raise SeparationError("声轨太短或为空,无法分离")
    bed, voice = separate(sess, mix)
    out = render(mix, bed, voice, recipes, offset_s=float(t0 or 0.0))
    write_wav(dst_wav, out)
    return {"mix_db": level_db(mix), "out_db": level_db(out), "voice_db": level_db(voice), "bed_db": level_db(bed),
            "seconds": round(mix.shape[1] / SR, 2)}


def replace_audio(video_src: Path, wav: Path, dst: Path, sample_rate: int = 0, channels: int = 0,
                  video_args: list[str] | None = None, seek: tuple[float, float] | None = None) -> None:
    """画面取 video_src(默认流拷贝,不重编码)、声轨换成 wav,封装到 dst;sample_rate / channels 给了就还原成源声轨规格。
    video_args 给了则按它编码画面(预览缩小用),seek=(起点秒, 时长秒) 只取画面的一段。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkstemp(prefix=dst.stem + ".", suffix=".muxing" + dst.suffix, dir=str(dst.parent))[1])
    cmd = ["ffmpeg", "-y", "-v", "error", "-nostdin"]
    if seek:
        cmd += ["-ss", f"{max(0.0, seek[0]):.3f}", "-t", f"{seek[1]:.3f}"]
    cmd += ["-i", str(video_src), "-i", str(wav), "-map", "0:v:0", "-map", "1:a:0"]
    cmd += video_args if video_args else ["-c:v", "copy"]
    cmd += ["-c:a", "aac", "-b:a", "192k"]
    if sample_rate:
        cmd += ["-ar", str(int(sample_rate))]
    if channels:
        cmd += ["-ac", str(int(channels))]
    cmd += ["-movflags", "+faststart", str(tmp)]      # 不加 -shortest:画面帧数必须与源一致
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3600)
    if p.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise SeparationError("封装失败:" + p.stderr.decode("utf-8", "replace")[-600:])
    os.replace(tmp, dst)


def audio_format(src: Path) -> tuple[int, int]:
    """源声轨的 (采样率, 声道数);没有声轨返回 (0, 0)。"""
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=sample_rate,channels",
                        "-of", "csv=p=0", str(src)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    parts = p.stdout.decode("utf-8", "replace").strip().split(",")
    try:
        return int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return 0, 0
