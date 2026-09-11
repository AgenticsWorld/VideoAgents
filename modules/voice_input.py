#!/usr/bin/env python3
"""语音输入(设置 → 高级 → 语音输入):浏览器录音 → 本机 faster-whisper 转文字。

两层职责:
  1. 纯文件系统的模型目录/状态查询(CATALOG / model_status / all_status),供 API 进程直接 import——
     **模块顶层不 import faster_whisper / av / ctranslate2**(PyAV 与 OpenCV 同进程会崩,
     见 footage_library 的子进程隔离约定),重依赖只在子进程命令里懒加载。
  2. 子进程 CLI(API 进程用 sys.executable 调用):
       python3 modules/voice_input.py download   --model small --progress-file P
           从 Hugging Face 拉取模型到 data/models/faster-whisper/(与 transcription.py 同一缓存),
           下载期间每 0.5s 把 {status, done_bytes, total_bytes} 写到 P,结束写 done/error。
       python3 modules/voice_input.py transcribe --audio x.wav --model small [--language zh]
           stdout 输出 JSON {text, language, duration_s}。音频由 API 侧先用 ffmpeg 转成 16k 单声道 WAV。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("VIDEOAGENTS_DATA_DIR", ROOT / "data")).expanduser().resolve()
MODEL_ROOT = DATA_DIR / "models" / "faster-whisper"     # 与 modules/transcription.py 同一目录

# 可选模型(id → HF 仓库、约占空间、界面提示键)。size_mb 只作展示与拿不到远端元数据时的进度分母。
# 中文口语输入推荐 small 起步;large-v3-turbo 精度接近 large-v3 而速度快得多,Apple 芯片可用。
CATALOG = [
    {"id": "tiny", "repo": "Systran/faster-whisper-tiny", "size_mb": 75, "hint": "最快,精度最低"},
    {"id": "base", "repo": "Systran/faster-whisper-base", "size_mb": 145, "hint": "快,精度一般"},
    {"id": "small", "repo": "Systran/faster-whisper-small", "size_mb": 484, "hint": "均衡(推荐)"},
    {"id": "medium", "repo": "Systran/faster-whisper-medium", "size_mb": 1530, "hint": "较慢,精度高"},
    {"id": "large-v3-turbo", "repo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
     "size_mb": 1620, "hint": "精度接近 large-v3,速度快得多"},
    {"id": "large-v3", "repo": "Systran/faster-whisper-large-v3", "size_mb": 3090, "hint": "慢,精度最高"},
]
CATALOG_BY_ID = {m["id"]: m for m in CATALOG}
DEFAULT_MODEL = "small"
# 与 faster_whisper.utils.download_model 同一份文件白名单(下载与状态判定口径一致)
ALLOW_PATTERNS = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]
MAX_AUDIO_S = 120          # 单次语音输入上限(前端 60s 自动停,服务端再兜一层)
# 中文提示句:让 whisper 稳定输出简体+标点(不给提示常出繁体/无标点)
_ZH_PROMPT = "以下是普通话的句子,带标点。"


def cache_dir(model_id: str) -> Path:
    repo = CATALOG_BY_ID[model_id]["repo"]
    return MODEL_ROOT / ("models--" + repo.replace("/", "--"))


def progress_path(model_id: str) -> Path:
    return MODEL_ROOT / f".download-{model_id}.json"


def _blob_bytes(model_id: str) -> int:
    """blobs/ 下已落盘字节(含 *.incomplete 半成品),作为下载进度分子。"""
    blobs = cache_dir(model_id) / "blobs"
    if not blobs.is_dir():
        return 0
    total = 0
    for p in blobs.iterdir():
        try:
            total += p.stat().st_size
        except OSError:
            pass
    return total


def is_ready(model_id: str) -> bool:
    """模型可用 = snapshots/<rev>/model.bin 与 config.json 都在(符号链接指向完整 blob)。
    半途中断的下载只会留下 blobs/*.incomplete,不会出现 model.bin,故判为未下载。"""
    snaps = cache_dir(model_id) / "snapshots"
    if not snaps.is_dir():
        return False
    for rev in snaps.iterdir():
        mb, cfg = rev / "model.bin", rev / "config.json"
        try:
            if mb.exists() and cfg.exists() and mb.stat().st_size > 1_000_000:
                return True
        except OSError:
            continue
    return False


def read_progress(model_id: str) -> dict | None:
    p = progress_path(model_id)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def model_status(model_id: str, downloading: bool = False) -> dict:
    """单个模型状态:ready | downloading | missing | error(+ progress)。
    downloading=True 表示 API 侧确认下载子进程仍活着;进度文件残留但进程已死 → 按 missing/error 报。"""
    m = CATALOG_BY_ID[model_id]
    item = {"id": m["id"], "repo": m["repo"], "size_mb": m["size_mb"], "hint": m["hint"],
            "status": "missing", "progress": None, "error": ""}
    if is_ready(model_id):
        item["status"] = "ready"
        return item
    prog = read_progress(model_id)
    if prog and prog.get("status") == "downloading" and downloading:
        total = int(prog.get("total_bytes") or 0) or m["size_mb"] * 1024 * 1024
        done = max(int(prog.get("done_bytes") or 0), _blob_bytes(model_id))
        item["status"] = "downloading"
        item["progress"] = {"done_bytes": done, "total_bytes": total,
                            "pct": max(0, min(99, int(done * 100 / total))) if total else 0}
    elif prog and prog.get("status") == "error":
        item["status"] = "error"
        item["error"] = str(prog.get("error") or "")[:300]
    return item


def all_status(downloading: set[str] | None = None) -> list[dict]:
    downloading = downloading or set()
    return [model_status(m["id"], m["id"] in downloading) for m in CATALOG]


# ---------------- 子进程命令 ----------------

def _write_progress(model_id: str, **fields) -> None:
    MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    p = progress_path(model_id)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"model": model_id, "ts": time.time(), **fields}, ensure_ascii=False),
                   encoding="utf-8")
    os.replace(tmp, p)


def _remote_total_bytes(repo: str) -> int:
    """按白名单汇总远端文件大小作进度分母;拿不到(离线/限流)返回 0 由调用方回退目录估算值。"""
    try:
        from fnmatch import fnmatch
        from huggingface_hub import HfApi
        info = HfApi().model_info(repo, files_metadata=True)
        total = 0
        for s in info.siblings or []:
            name = s.rfilename
            if any(fnmatch(name, pat) for pat in ALLOW_PATTERNS):
                total += int(getattr(s, "size", 0) or 0)
        return total
    except Exception:  # noqa: BLE001
        return 0


STALL_S = 180              # 字节数无变化超过此秒数判为停滞(本机代理卡住的常见形态)
RETRIES = 5                # 整体下载重试轮数(SSLEOF/连接重置等瞬时网络错误)


def cmd_download(model_id: str) -> int:
    if model_id not in CATALOG_BY_ID:
        print(json.dumps({"error": f"unknown model {model_id}"}), file=sys.stderr)
        return 2
    # 关掉 hf_xet 传输后端:2026-09-11 实测经本机系统代理时 xet 在最后一块永久挂起
    # (全部字节已落盘但不收尾),纯 HTTP 下载可断点续传且 .incomplete 字节数就是真实进度。
    # 须在 import huggingface_hub 之前设置(常量在导入期读取)。
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    m = CATALOG_BY_ID[model_id]
    total = _remote_total_bytes(m["repo"]) or m["size_mb"] * 1024 * 1024
    _write_progress(model_id, status="downloading", done_bytes=_blob_bytes(model_id), total_bytes=total)
    result: dict = {}

    def worker():
        from huggingface_hub import snapshot_download
        # 经代理/CDN 偶发 SSLEOF / 连接重置:整体重试几轮,.incomplete 断点续传不重头来
        for attempt in range(RETRIES):
            try:
                snapshot_download(m["repo"], allow_patterns=ALLOW_PATTERNS, cache_dir=str(MODEL_ROOT))
                result["ok"] = True
                return
            except Exception as exc:  # noqa: BLE001
                result["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
                print(f"[retry {attempt + 1}/{RETRIES}] {result['error']}", file=sys.stderr, flush=True)
                time.sleep(3 * (attempt + 1))

    th = threading.Thread(target=worker, daemon=True)
    th.start()
    last_bytes, last_change = -1, time.time()
    while th.is_alive():
        done = _blob_bytes(model_id)
        if done != last_bytes:
            last_bytes, last_change = done, time.time()
        elif time.time() - last_change > STALL_S:
            err = f"下载停滞超过 {STALL_S}s(网络/代理无响应),请重试"
            _write_progress(model_id, status="error", error=err, done_bytes=done, total_bytes=total)
            print(err, file=sys.stderr)
            os._exit(1)          # 下载线程卡在网络层无法中断,直接退出进程;.incomplete 可续传
        _write_progress(model_id, status="downloading", done_bytes=done, total_bytes=total)
        th.join(0.5)
    if result.get("ok") and is_ready(model_id):
        _write_progress(model_id, status="done", done_bytes=total, total_bytes=total)
        return 0
    err = result.get("error") or "下载结束但模型文件不完整"
    _write_progress(model_id, status="error", error=err[:500], done_bytes=_blob_bytes(model_id),
                    total_bytes=total)
    print(err, file=sys.stderr)
    return 1


def cmd_transcribe(audio: str, model_id: str, language: str | None) -> int:
    path = Path(audio)
    if not path.is_file():
        print(json.dumps({"error": f"音频不存在:{path}"}), file=sys.stderr)
        return 2
    if model_id not in CATALOG_BY_ID or not is_ready(model_id):
        print(json.dumps({"error": f"模型未下载:{model_id}"}), file=sys.stderr)
        return 3
    from faster_whisper import WhisperModel  # 重依赖只在子进程加载
    t0 = time.time()
    # cache_dir 指向同一目录,local_files_only 保证不会在识别时偷偷联网重拉
    model = WhisperModel(model_id, device="auto", compute_type="default",
                         download_root=str(MODEL_ROOT), local_files_only=True)
    lang = language or None
    prompt = _ZH_PROMPT if lang == "zh" else None
    segments, info = model.transcribe(str(path), language=lang, initial_prompt=prompt,
                                      vad_filter=True, vad_parameters={"min_silence_duration_ms": 400},
                                      beam_size=5, condition_on_previous_text=False)
    parts = []
    for seg in segments:
        s = str(getattr(seg, "text", "")).strip()
        if s:
            parts.append(s)
    detected = str(getattr(info, "language", lang or "") or "")
    # 中日韩文之间不加空格,其它语言按空格连接
    joiner = "" if detected in ("zh", "ja", "ko") else " "
    text = joiner.join(parts).strip()
    out = {"text": text, "language": detected,
           "duration_s": round(float(getattr(info, "duration", 0.0) or 0.0), 2),
           "elapsed_ms": int((time.time() - t0) * 1000), "model": model_id}
    print(json.dumps(out, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="语音输入:模型下载与本机转写(供 API 子进程调用)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download")
    d.add_argument("--model", default=DEFAULT_MODEL)
    t = sub.add_parser("transcribe")
    t.add_argument("--audio", required=True)
    t.add_argument("--model", default=DEFAULT_MODEL)
    t.add_argument("--language", default="")
    a = ap.parse_args(argv)
    if a.cmd == "download":
        return cmd_download(a.model)
    return cmd_transcribe(a.audio, a.model, a.language or None)


if __name__ == "__main__":
    sys.exit(main())
