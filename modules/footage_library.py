#!/usr/bin/env python3
"""素材库(footage library):本地视频 / YouTube·Bilibili 链接 → 智能镜头分割 → 分镜 clip
+ 字幕(faster-whisper)+ 画面含义(联系图 + 项目可编辑的问题交给当前默认 CLI 引擎,纯文本回答直接入框)→ 结构化 JSON。

源自 zhiyou-fenjing(镜构·智能分镜工作台)的宿主化移植:项目落盘 data/footage/<name>/,
API 由 services/runtime/core.py 的 api_footage_* 薄封装调用;本模块本身不依赖 core。

目录布局(一个素材项目 = 一个文件夹,删项目 = 删文件夹):
    data/footage/<name>/
      project.json            项目元数据 + 流水线状态(status/progress/message)
      clips.json              结构化输出:每个分镜 clip 的文件、时间区间、字幕、画面信息
      source/source.<ext>     原始视频(上传件 / yt-dlp 1080p 下载件)
      source/proxy.mp4        小画幅代理(默认 480p 无声,只用于分析:镜头检测/缩略图/联系图)
      source/audio.wav        16k 单声道,ASR 用
      clips/<name>_clip_001.mp4        分镜 clip(从原片重编码导出,带音轨)
      clips/<name>_clip_001.jpg        缩略图
      clips/<name>_clip_001.sheet.jpg  AI 分析用联系图(多时间点拼图)

镜头分割:代理片先跑 PySceneDetect(AdaptiveDetector → ContentDetector),都检不出硬切
(动画/溶解转场)再退回 2fps 抽帧的整幅画面差分峰值法(zhiyou-fenjing 原算法)。

外部依赖:ffmpeg/ffprobe、yt-dlp(链接下载)、scenedetect+opencv(镜头检测)、Pillow(联系图)、
faster-whisper(字幕;模型首次使用时下载到 data/models/faster-whisper/)。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("VIDEOAGENTS_DATA_DIR", ROOT / "data")).expanduser().resolve()
FOOTAGE_DIR = DATA_DIR / "footage"
SCHEMA_PROJECT = "videoagents.footage.project.v1"
SCHEMA_CLIPS = "videoagents.footage.clips.v1"

PROXY_HEIGHT = int(os.environ.get("VIDEOAGENTS_FOOTAGE_PROXY_HEIGHT", "480"))
DOWNLOAD_HEIGHT = 1080
MIN_SCENE_LEN_S = 2.0
VISUAL_DIFF_THRESHOLD = float(os.environ.get("VISUAL_DIFF_THRESHOLD", "0.025"))
MAX_UPLOAD_CHUNK = 64 * 1024 * 1024
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
CLIP_ID_RE = re.compile(r"^clip_\d{3,}$")
VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".flv", ".ts", ".mpg", ".mpeg", ".wmv")
LANG_NAMES = {"zh": "中文", "en": "English", "ja": "日本語", "ko": "한국어", "vi": "Tiếng Việt",
              "es": "Español", "fr": "Français", "de": "Deutsch", "id": "Bahasa Indonesia",
              "pt": "Português", "ru": "Русский", "ar": "العربية"}

_LOCK = threading.RLock()   # 可重入:analyze_clip/update_clip 持锁内再调 _update
# name -> 运行中的作业子进程。流水线(scenedetect/opencv、faster-whisper/av、numpy)一律在
# 子进程 `python3 modules/footage_library.py job <mode> <name>` 里跑:API 进程不加载 cv2/torch,
# 且 macOS 上 cv2 与 av 各自捆绑的 libavdevice 同进程共存会无声崩溃(2026-08-27 实测),
# 子进程里 ASR 再套一层 transcription.py 子进程隔离。进度经 project.json 回传。
_JOBS: dict[str, subprocess.Popen] = {}
RUNNING_STATES = ("queued", "downloading", "processing", "transcribing", "analyzing")
SKIP_ASR = os.environ.get("VIDEOAGENTS_FOOTAGE_SKIP_ASR", "").lower() in ("1", "true", "yes")


class FootageLibError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


# ---------------- 基础工具 ----------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_write_json(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _run(cmd: list[str], timeout: int = 600, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)


def _require_tools(*names: str) -> None:
    missing = [n for n in names if shutil.which(n) is None]
    if missing:
        raise FootageLibError(500, "缺少外部工具:" + ", ".join(missing))


def safe_name(name: str) -> str:
    name = str(name or "").strip()
    if not NAME_RE.fullmatch(name):
        raise FootageLibError(400, "项目名只能用英文字母、数字、下划线与连字符(1–64 位,字母数字开头)")
    return name


def default_name() -> str:
    """默认项目名:英文数字,带时间戳保证唯一。"""
    base = datetime.now().strftime("fp%Y%m%d-%H%M%S")
    name, i = base, 1
    while (FOOTAGE_DIR / name).exists():
        name, i = f"{base}-{i}", i + 1
    return name


def project_dir(name: str, must_exist: bool = True) -> Path:
    d = FOOTAGE_DIR / safe_name(name)
    if must_exist and not (d / "project.json").is_file():
        raise FootageLibError(404, f"素材项目不存在:{name}")
    return d


def load_project(name: str) -> dict:
    return _read_json(project_dir(name) / "project.json", {})


def save_project(name: str, proj: dict) -> None:
    proj["updated_at"] = _now()
    _atomic_write_json(project_dir(name, must_exist=False) / "project.json", proj)


def load_clips(name: str) -> dict:
    return _read_json(project_dir(name) / "clips.json",
                      {"schema": SCHEMA_CLIPS, "project": name, "source": {}, "clips": []})


def save_clips(name: str, doc: dict) -> None:
    doc["schema"] = SCHEMA_CLIPS
    doc["project"] = name
    doc["updated_at"] = _now()
    _atomic_write_json(project_dir(name) / "clips.json", doc)


def _update(name: str, **fields) -> None:
    """流水线进度写回 project.json(前端轮询即读此文件)。"""
    with _LOCK:
        proj = load_project(name)
        proj.update(fields)
        save_project(name, proj)


def _probe(path: Path) -> dict:
    _require_tools("ffprobe")
    r = _run(["ffprobe", "-v", "quiet", "-show_entries",
              "format=duration:stream=codec_type,width,height,r_frame_rate",
              "-of", "json", str(path)], timeout=120)
    if r.returncode != 0 or not r.stdout:
        raise FootageLibError(500, f"ffprobe 读取失败:{path.name}")
    j = json.loads(r.stdout)
    info = {"duration_s": round(float(j.get("format", {}).get("duration") or 0), 3),
            "width": 0, "height": 0, "fps": 0.0, "has_audio": False}
    for s in j.get("streams", []):
        if s.get("codec_type") == "audio":
            info["has_audio"] = True
        elif s.get("codec_type") == "video" and not info["width"]:
            info["width"], info["height"] = int(s.get("width") or 0), int(s.get("height") or 0)
            num, _, den = (s.get("r_frame_rate") or "0/1").partition("/")
            try:
                info["fps"] = round(float(num) / float(den or 1), 3)
            except (ValueError, ZeroDivisionError):
                info["fps"] = 0.0
    return info


def format_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}.{int(round((seconds % 1) * 1000)):03d}"


# ---------------- 项目 CRUD ----------------

def list_projects() -> list[dict]:
    FOOTAGE_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for d in sorted(FOOTAGE_DIR.iterdir()):
        pj = d / "project.json"
        if not d.is_dir() or not pj.is_file():
            continue
        _reap(d.name)
        proj = _read_json(pj, {})
        src = proj.get("source") or {}
        out.append({"name": d.name, "created_at": proj.get("created_at", ""),
                    "updated_at": proj.get("updated_at", ""),
                    "status": proj.get("status", "empty"), "message": proj.get("message", ""),
                    "clip_count": int(proj.get("clip_count") or 0),
                    "analyzed_count": int(proj.get("analyzed_count") or 0),
                    "running": _job_running(d.name),
                    "source": {"type": src.get("type", ""), "title": src.get("title", ""),
                               "url": src.get("url", ""), "filename": src.get("filename", ""),
                               "duration_s": src.get("duration_s", 0)}})
    out.sort(key=lambda p: p.get("created_at") or "", reverse=True)
    return out


def create_project(name: str | None = None) -> dict:
    FOOTAGE_DIR.mkdir(parents=True, exist_ok=True)
    name = safe_name(name) if name else default_name()
    d = FOOTAGE_DIR / name
    if d.exists():
        raise FootageLibError(409, f"素材项目已存在:{name}")
    (d / "source").mkdir(parents=True)
    (d / "clips").mkdir()
    proj = {"schema": SCHEMA_PROJECT, "name": name, "created_at": _now(),
            "status": "empty", "progress": 0, "message": "", "error": "",
            "source": {}, "clip_count": 0, "analyzed_count": 0, "asr": {}}
    save_project(name, proj)
    save_clips(name, {"source": {}, "clips": []})
    return get_project(name)


def delete_project(name: str) -> dict:
    d = project_dir(name)
    _kill_job(name)
    shutil.rmtree(d, ignore_errors=False)
    return {"ok": True, "name": name}


def update_settings(name: str, fields: dict) -> dict:
    """项目级设置:analysis_prompt(AI 分析问题,空=用默认)。"""
    with _LOCK:
        proj = load_project(name)
        if "analysis_prompt" in fields:
            proj["analysis_prompt"] = str(fields.get("analysis_prompt") or "").strip()[:4000]
        save_project(name, proj)
    return get_project(name)


def get_project(name: str) -> dict:
    _reap(name)
    proj = load_project(name)
    proj["analysis_prompt_default"] = DEFAULT_ANALYSIS_QUESTION
    doc = load_clips(name)
    proj["running"] = _job_running(name)
    proj["dir"] = str(project_dir(name))
    proj["clips"] = doc.get("clips", [])
    return proj


# ---------------- 输入:上传分块 / 链接下载 ----------------

def _clean_ext(filename: str) -> str:
    ext = Path(filename or "").suffix.lower()
    ext = re.sub(r"[^a-z0-9.]", "", ext)[:8]
    return ext if ext in VIDEO_EXTS else ".mp4"


def receive_upload_chunk(name: str, data: bytes, upload_id: str, index: int, total: int,
                         filename: str) -> dict:
    """请求体即分块原始字节(避免 multipart 依赖;经 Web 代理整体进内存,分块勿超 64MB)。
    最后一块落盘后清空旧产物并启动流水线。"""
    d = project_dir(name)
    if _job_running(name):
        raise FootageLibError(409, "该项目正在处理中,请等待完成后再导入")
    upload_id = re.sub(r"[^A-Za-z0-9_-]", "", upload_id or "")[:40]
    if not upload_id or total < 1 or index < 0 or index >= total:
        raise FootageLibError(400, "上传分块参数无效")
    if not data:
        raise FootageLibError(400, "空的上传分块")
    if len(data) > MAX_UPLOAD_CHUNK:
        raise FootageLibError(400, "单个分块超过 64MB")
    part = d / "source" / f".{upload_id}.part"
    if index == 0 and part.exists():
        part.unlink()
    with part.open("ab") as f:
        f.write(data)
    if index + 1 < total:
        return {"ok": True, "received": index + 1, "total": total}
    _reset_outputs(d)
    target = d / "source" / f"source{_clean_ext(filename)}"
    part.replace(target)
    proj = load_project(name)
    proj["source"] = {"type": "upload", "filename": os.path.basename(filename or "video"),
                      "title": Path(filename or "video").stem, "url": "",
                      "file": f"source/{target.name}", "size": target.stat().st_size}
    proj.update(status="queued", progress=0, message="上传完成,排队处理…", error="",
                clip_count=0, analyzed_count=0, asr={})
    save_project(name, proj)
    _start(name, "process")
    return {"ok": True, "received": total, "total": total, "started": True}


def start_download(name: str, url: str) -> dict:
    d = project_dir(name)
    if _job_running(name):
        raise FootageLibError(409, "该项目正在处理中,请等待完成后再导入")
    url = str(url or "").strip()
    if not re.match(r"^https?://", url):
        raise FootageLibError(400, "请输入 http(s) 开头的视频链接")
    _require_tools("yt-dlp", "ffmpeg")
    _reset_outputs(d)
    for old in (d / "source").glob("source.*"):
        old.unlink(missing_ok=True)
    proj = load_project(name)
    proj["source"] = {"type": "url", "url": url, "filename": "", "title": "", "file": ""}
    proj.update(status="queued", progress=0, message="准备下载…", error="",
                clip_count=0, analyzed_count=0, asr={})
    save_project(name, proj)
    _start(name, "download", url)
    return {"ok": True, "started": True}


def _reset_outputs(d: Path) -> None:
    clips = d / "clips"
    if clips.exists():
        shutil.rmtree(clips, ignore_errors=True)
    clips.mkdir(parents=True, exist_ok=True)
    for f in ("proxy.mp4", "audio.wav"):
        (d / "source" / f).unlink(missing_ok=True)
    cj = d / "clips.json"
    if cj.exists():
        _atomic_write_json(cj, {"schema": SCHEMA_CLIPS, "project": d.name, "source": {}, "clips": []})


def _ensure_modules_path() -> None:
    mods = str(ROOT / "modules")
    if mods not in sys.path:
        sys.path.insert(0, mods)


def _ytdlp_base() -> list[str]:
    try:
        _ensure_modules_path()
        import footage as _footage  # 复用 mashup 宿主工具链的 yt-dlp 基础参数(JS runtime 探测)
        return _footage._ytdlp_base()
    except Exception:
        return ["yt-dlp", "--no-playlist", "--no-warnings", "--socket-timeout", "30", "--retries", "3"]


def _download_then_pipeline(name: str, url: str) -> None:
    d = project_dir(name)
    try:
        _update(name, status="downloading", progress=2, message="读取视频信息…")
        title = ""
        r = _run(_ytdlp_base() + ["-J", url], timeout=180)
        if r.returncode == 0 and r.stdout:
            try:
                meta = json.loads(r.stdout)
                title = str(meta.get("title") or "")
            except json.JSONDecodeError:
                pass
        _update(name, progress=5, message=f"下载中(≤{DOWNLOAD_HEIGHT}p)… {title}".strip())
        out = d / "source" / "source.mp4"
        cmd = _ytdlp_base() + ["-S", f"res:{DOWNLOAD_HEIGHT}", "--force-overwrites",
                               "--merge-output-format", "mp4", "-o", str(out), url]
        r = _run(cmd, timeout=3600)
        if _cancelled(name):
            return
        if not out.exists():
            got = [g for g in sorted(out.parent.glob("source.*"))
                   if g.suffix.lower() in VIDEO_EXTS and g.name != "proxy.mp4"]
            if got:
                r2 = _run(["ffmpeg", "-y", "-v", "error", "-i", str(got[0]), "-c", "copy", str(out)],
                          timeout=1200)
                if r2.returncode == 0 and out.exists():
                    got[0].unlink(missing_ok=True)
                else:
                    out = got[0]
            else:
                raise FootageLibError(500, "下载失败:" + (r.stderr or r.stdout or "").strip()[-400:])
        with _LOCK:
            proj = load_project(name)
            proj["source"].update(title=title or url, file=f"source/{out.name}",
                                  filename=out.name, size=out.stat().st_size)
            save_project(name, proj)
        _pipeline(name)
    except Exception as exc:  # noqa: BLE001
        _update(name, status="failed", error=f"下载失败:{exc}", message="")


# ---------------- 流水线:代理 → 镜头分割 → 导出 clip → 字幕 ----------------

def _job_running(name: str) -> bool:
    proc = _JOBS.get(name)
    return bool(proc and proc.poll() is None)


def _cancelled(name: str) -> bool:
    """作业子进程被取消即被杀,无需协作式检查;保留接口便于将来改回线程。"""
    return False


def _reap(name: str) -> None:
    """作业子进程已退出但 project.json 仍是运行态(崩溃/被杀)→ 标记失败并附日志尾部。"""
    proc = _JOBS.get(name)
    if proc is None or proc.poll() is None:
        return
    _JOBS.pop(name, None)
    proj = _read_json(FOOTAGE_DIR / name / "project.json", {})
    if proj.get("status") in RUNNING_STATES:
        tail = ""
        log = FOOTAGE_DIR / name / "job.log"
        if log.is_file():
            tail = log.read_text(encoding="utf-8", errors="replace")[-800:]
        _update(name, status="failed", message="",
                error=f"处理进程异常退出(code {proc.returncode})\n{tail}".strip()[:1200])


def _start(name: str, mode: str, *args: str, job_args: dict | None = None) -> None:
    d = project_dir(name)
    env = os.environ.copy()
    env["FOOTAGE_JOB_ARGS"] = json.dumps(job_args or {}, ensure_ascii=False)
    env["PYTHONUNBUFFERED"] = "1"
    log = (d / "job.log").open("w", encoding="utf-8")
    kw = {"start_new_session": True} if os.name != "nt" else {}
    proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "job", mode, name, *args],
                            cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT, **kw)
    log.close()
    _JOBS[name] = proc


def _kill_job(name: str) -> None:
    proc = _JOBS.pop(name, None)
    if proc is None or proc.poll() is not None:
        return
    try:
        if os.name != "nt":
            import signal
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        else:
            proc.terminate()
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


def _source_path(d: Path, proj: dict) -> Path:
    rel = (proj.get("source") or {}).get("file") or ""
    p = d / rel if rel else None
    if not p or not p.is_file():
        raise FootageLibError(400, "尚未导入原始视频")
    return p


def _make_proxy(src: Path, proxy: Path) -> None:
    _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-an",
          "-vf", f"scale=-2:'min({PROXY_HEIGHT},ih)'", "-c:v", "libx264", "-preset", "veryfast",
          "-crf", "26", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(proxy)],
         timeout=3600)
    if not proxy.is_file() or proxy.stat().st_size == 0:
        raise FootageLibError(500, "代理小画幅生成失败")


def _extract_audio(src: Path, wav: Path) -> bool:
    r = _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vn", "-ac", "1",
              "-ar", "16000", "-c:a", "pcm_s16le", str(wav)], timeout=3600)
    return r.returncode == 0 and wav.is_file() and wav.stat().st_size > 1000


def _scenedetect_boundaries(proxy: Path) -> list[float]:
    try:
        from scenedetect import AdaptiveDetector, ContentDetector, detect
    except ImportError as exc:
        raise FootageLibError(500, f"未安装 scenedetect:{exc}") from exc
    for det in (AdaptiveDetector(adaptive_threshold=3.0, min_scene_len=f"{MIN_SCENE_LEN_S}s"),
                ContentDetector(threshold=27.0, min_scene_len=f"{MIN_SCENE_LEN_S}s")):
        scenes = detect(str(proxy), det, show_progress=False)
        if len(scenes) > 1:
            return [float(s[1].get_seconds()) for s in scenes[:-1]]
    return []


def _visual_diff_boundaries(proxy: Path, duration: float, name: str) -> list[float]:
    """2fps 抽帧 64x64 整幅差分,取峰值并抑制 3s 内邻近峰(动画/溶解转场兜底)。"""
    import numpy as np
    fps, w, h = 2, 64, 64
    per = w * h * 3
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(proxy), "-vf",
           f"fps={fps},scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    prev, scores, idx = None, [], 0
    while True:
        chunk = proc.stdout.read(per)
        if len(chunk) < per:
            break
        cur = np.frombuffer(chunk, dtype=np.uint8).astype(np.float32) / 255.0
        if prev is not None:
            ts = idx / fps
            scores.append((float(np.abs(cur - prev).mean()), ts))
            if idx % 40 == 0:
                _update(name, progress=min(40, 15 + ts / max(duration, 1) * 25), message="比较画面变化…")
        prev, idx = cur, idx + 1
    proc.wait()
    if not scores:
        return []
    vals = [s for s, _ in scores]
    adaptive = max(VISUAL_DIFF_THRESHOLD, float(np.mean(vals) + 1.8 * np.std(vals)))
    chosen: list[float] = []
    for score, ts in sorted(scores, reverse=True):
        if score < adaptive or ts < 1.0 or ts > duration - 1.0:
            continue
        if all(abs(ts - c) >= 3.0 for c in chosen):
            chosen.append(ts)
    return sorted(chosen)


def _clip_stem(name: str, index: int) -> str:
    return f"{name}_clip_{index:03d}"


def _export_clip(src: Path, proxy: Path, out: Path, thumb: Path, start: float, dur: float) -> None:
    r = _run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", str(src),
              "-t", f"{dur:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
              "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
              str(out)], timeout=3600)
    if r.returncode != 0 or not out.is_file():
        raise FootageLibError(500, f"导出 clip 失败:{out.name} {(r.stderr or '')[-200:]}")
    _run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start + min(0.5, dur / 2):.3f}",
          "-i", str(proxy), "-frames:v", "1", "-q:v", "3", str(thumb)], timeout=120)


def _assign_subtitles(rows: list[dict], clips: list[dict]) -> None:
    """整段 ASR 结果按中点落区/时间重叠分配到各 clip(zhiyou-fenjing 原逻辑)。"""
    matched: list[list[str]] = [[] for _ in clips]
    for row in rows:
        start, end, text = float(row["start"]), float(row["end"]), str(row["text"]).strip()
        if not text:
            continue
        mid = (start + end) / 2
        target = next((i for i, c in enumerate(clips) if c["start_s"] <= mid < c["end_s"]), None)
        if target is None and clips:
            overlaps = [max(0.0, min(end, c["end_s"]) - max(start, c["start_s"])) for c in clips]
            target = max(range(len(clips)), key=lambda i: overlaps[i])
            if overlaps[target] <= 0:
                target = None
        if target is not None:
            matched[target].append(text)
    for clip, texts in zip(clips, matched):
        clip["subtitle"] = "\n".join(dict.fromkeys(texts))
        clip["subtitle_auto"] = clip["subtitle"]


def _run_asr(name: str, wav: Path, clips: list[dict]) -> dict:
    """faster-whisper 转写:子进程调 modules/transcription.py(模型缓存 data/models/faster-whisper/,
    首次使用自动下载),读回 JSON 段落后按时间分配到各 clip。失败不阻断流水线。"""
    if SKIP_ASR:
        return {"ok": False, "language": "", "segments": 0, "error": "已按 VIDEOAGENTS_FOOTAGE_SKIP_ASR 跳过"}
    _update(name, status="transcribing", progress=92, message="语音转文字(首次使用会下载 Whisper 模型)…")
    try:
        src_dir = wav.parent
        txt, js = src_dir / "transcript.txt", src_dir / "transcript.json"
        r = _run([sys.executable, str(ROOT / "modules" / "transcription.py"), "transcribe",
                  "--audio", str(wav), "--output", str(txt), "--json-output", str(js)],
                 timeout=7200, cwd=str(ROOT))
        if r.returncode != 0 or not js.is_file():
            err = (r.stderr or r.stdout or "").strip().splitlines()
            raise FootageLibError(500, (err[-1] if err else f"exit {r.returncode}")[:300])
        payload = json.loads(js.read_text(encoding="utf-8"))
        rows = payload.get("segments") or []
        _assign_subtitles(rows, clips)
        return {"ok": True, "language": (payload.get("asr") or {}).get("language", ""),
                "segments": len(rows), "model": (payload.get("model") or {}).get("name", ""), "error": ""}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "language": "", "segments": 0,
                "error": str(getattr(exc, "detail", None) or f"{type(exc).__name__}: {exc}")[:400]}


def _pipeline(name: str) -> None:
    d = project_dir(name)
    try:
        _require_tools("ffmpeg", "ffprobe")
        proj = load_project(name)
        src = _source_path(d, proj)
        _update(name, status="processing", progress=3, message="读取视频信息…", error="")
        info = _probe(src)
        if info["duration_s"] <= 0:
            raise FootageLibError(500, "无法读取视频时长(文件可能损坏)")
        with _LOCK:
            proj = load_project(name)
            proj["source"].update(info)
            save_project(name, proj)
        duration = info["duration_s"]

        _update(name, progress=6, message=f"生成 {PROXY_HEIGHT}p 分析代理…")
        proxy = d / "source" / "proxy.mp4"
        _make_proxy(src, proxy)
        if _cancelled(name):
            return

        wav = d / "source" / "audio.wav"
        has_audio = info["has_audio"] and _extract_audio(src, wav)

        _update(name, progress=15, message="检测镜头边界(PySceneDetect)…")
        boundaries = _scenedetect_boundaries(proxy)
        if not boundaries:
            _update(name, progress=15, message="未检出硬切,改用画面差分峰值法…")
            boundaries = _visual_diff_boundaries(proxy, duration, name)
        if _cancelled(name):
            return
        points = [0.0] + [round(b, 3) for b in boundaries if 0.2 < b < duration - 0.2] + [duration]
        points = sorted(set(points))
        clips: list[dict] = []
        for i, (a, b) in enumerate(zip(points, points[1:]), start=1):
            if b - a < 0.1:
                continue
            stem = _clip_stem(name, i)
            clips.append({"id": f"clip_{i:03d}", "index": i, "title": f"Clip {i:03d}",
                          "file": f"clips/{stem}.mp4", "thumbnail": f"clips/{stem}.jpg",
                          "sheet": "", "start_s": round(a, 3), "end_s": round(b, 3),
                          "duration_s": round(b - a, 3), "start_tc": format_time(a),
                          "end_tc": format_time(b), "subtitle": "", "subtitle_auto": "",
                          "info": "", "analysis": {}, "analyzed_at": "", "updated_at": ""})
        _update(name, progress=42, message=f"检测完成,导出 {len(clips)} 个分镜 clip…")
        for n, clip in enumerate(clips, start=1):
            if _cancelled(name):
                return
            _export_clip(src, proxy, d / clip["file"], d / clip["thumbnail"],
                         clip["start_s"], clip["duration_s"])
            _update(name, progress=42 + n / max(len(clips), 1) * 48,
                    message=f"导出分镜 {n}/{len(clips)}…")
        source_meta = {**(load_project(name).get("source") or {})}
        save_clips(name, {"source": source_meta, "clips": clips})

        asr = {"ok": False, "error": "视频无音轨", "segments": 0, "language": ""} if not has_audio \
            else _run_asr(name, wav, clips)
        save_clips(name, {"source": source_meta, "clips": clips})
        msg = f"完成:{len(clips)} 个分镜"
        msg += f",字幕 {asr.get('segments', 0)} 段({asr.get('language') or '?'})" if asr.get("ok") \
            else f",字幕未生成({asr.get('error', '')})"
        _update(name, status="ready", progress=100, message=msg, clip_count=len(clips),
                analyzed_count=0, asr=asr, error="")
    except Exception as exc:  # noqa: BLE001
        detail = getattr(exc, "detail", None) or f"{type(exc).__name__}: {exc}"
        _update(name, status="failed", error=str(detail)[:600], message="")


def reprocess(name: str) -> dict:
    """重新分割(清空 clip 与已编辑内容,原片保留)。"""
    d = project_dir(name)
    if _job_running(name):
        raise FootageLibError(409, "该项目正在处理中")
    proj = load_project(name)
    _source_path(d, proj)
    _reset_outputs(d)
    proj.update(status="queued", progress=0, message="重新分割…", error="", clip_count=0,
                analyzed_count=0, asr={})
    save_project(name, proj)
    _start(name, "process")
    return {"ok": True, "started": True}


def retranscribe(name: str) -> dict:
    """只重跑字幕(保留 clip 与画面信息;会覆盖字幕框)。"""
    d = project_dir(name)
    if _job_running(name):
        raise FootageLibError(409, "该项目正在处理中")
    proj = load_project(name)
    src = _source_path(d, proj)
    doc = load_clips(name)
    if not doc.get("clips"):
        raise FootageLibError(400, "还没有分镜 clip,请先处理视频")
    _update(name, status="transcribing", progress=90, message="语音转文字…", error="")
    _start(name, "transcribe")
    return {"ok": True, "started": True}


def _transcribe_job(name: str) -> None:
    d = project_dir(name)
    try:
        src = _source_path(d, load_project(name))
        doc = load_clips(name)
        wav = d / "source" / "audio.wav"
        if not wav.is_file() and not _extract_audio(src, wav):
            raise FootageLibError(500, "提取音频失败(视频可能无音轨)")
        asr = _run_asr(name, wav, doc["clips"])
        save_clips(name, doc)
        _update(name, status="ready", progress=100, asr=asr,
                message=(f"字幕已更新:{asr['segments']} 段" if asr.get("ok")
                         else f"字幕未生成({asr.get('error', '')})"))
    except Exception as exc:  # noqa: BLE001
        _update(name, status="ready", error=str(getattr(exc, "detail", exc))[:600], message="")


def cancel(name: str) -> dict:
    project_dir(name)
    if _job_running(name):
        _kill_job(name)
        proj = load_project(name)
        status = "ready" if (proj.get("clip_count") or 0) > 0 else "failed"
        _update(name, status=status, error="已取消", message="", progress=0)
    return {"ok": True}


# ---------------- 分镜 clip:编辑保存 / 联系图 / AI 画面分析 ----------------

def _find_clip(doc: dict, clip_id: str) -> dict:
    if not CLIP_ID_RE.fullmatch(clip_id or ""):
        raise FootageLibError(400, "clip id 无效")
    clip = next((c for c in doc.get("clips", []) if c.get("id") == clip_id), None)
    if clip is None:
        raise FootageLibError(404, f"分镜不存在:{clip_id}")
    return clip


def update_clip(name: str, clip_id: str, fields: dict) -> dict:
    with _LOCK:
        doc = load_clips(name)
        clip = _find_clip(doc, clip_id)
        changed = False
        for key in ("subtitle", "info", "title"):
            if key in fields and fields[key] is not None:
                val = str(fields[key])
                if clip.get(key) != val:
                    clip[key] = val
                    changed = True
        if changed:
            clip["updated_at"] = _now()
            save_clips(name, doc)
            _update(name, analyzed_count=sum(1 for c in doc["clips"] if str(c.get("info") or "").strip()))
    return clip


def make_contact_sheet(name: str, clip_id: str) -> Path:
    """按 clip 时长取 2–8 个时间点,从代理片抽帧拼成带编号的联系图。"""
    try:
        from PIL import Image, ImageDraw, ImageOps
    except ImportError as exc:
        raise FootageLibError(500, f"未安装 Pillow:{exc}") from exc
    d = project_dir(name)
    doc = load_clips(name)
    clip = _find_clip(doc, clip_id)
    proxy = d / "source" / "proxy.mp4"
    source = proxy if proxy.is_file() else d / clip["file"]
    offset = clip["start_s"] if source == proxy else 0.0
    dur = max(0.1, float(clip["duration_s"]))
    n = 8 if dur >= 60 else 6 if dur >= 30 else 4 if dur >= 10 else 3 if dur >= 3 else 2
    stamps = [dur * (i + 0.5) / n for i in range(n)]
    cols = n if n <= 3 else 2 if n == 4 else 4      # 网格恰好填满,不留空黑格误导模型
    tw, th = (640, 360) if n <= 4 else (512, 288)
    rows = (n + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tw, rows * th), "#111318")
    with tempfile.TemporaryDirectory(prefix="footage-sheet-") as tmp:
        for i, ts in enumerate(stamps):
            fp = Path(tmp) / f"f{i}.jpg"
            _run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{offset + ts:.3f}", "-i", str(source),
                  "-frames:v", "1", "-q:v", "4", str(fp)], timeout=120)
            if not fp.is_file():
                continue
            frame = ImageOps.fit(Image.open(fp).convert("RGB"), (tw, th), method=Image.Resampling.LANCZOS)
            draw = ImageDraw.Draw(frame)
            draw.rectangle((10, 10, 92, 42), fill="#111318")
            draw.text((20, 18), f"#{i + 1}", fill="white")
            sheet.paste(frame, ((i % cols) * tw, (i // cols) * th))
    out = d / "clips" / f"{_clip_stem(name, int(clip['index']))}.sheet.jpg"
    sheet.save(out, quality=88, optimize=True)
    clip["sheet"] = f"clips/{out.name}"
    with _LOCK:
        doc2 = load_clips(name)
        _find_clip(doc2, clip_id)["sheet"] = clip["sheet"]
        save_clips(name, doc2)
    return out


DEFAULT_ANALYSIS_QUESTION = (
    "只分析画面,不分析音频/对白。综合多个画面后,用纯文本分四行回答(不要 JSON、不要 markdown、不要代码块):\n"
    "画面:这个分镜整体呈现了什么,包括人物、物体、场景、动作与镜头运动。\n"
    "主旨:提炼这个分镜传递的主旨或叙事信息。\n"
    "风格:画面整体风格——媒介/艺术形式、色彩、质感、光影与氛围。\n"
    "标签:3 到 6 个简短的画面标签,用顿号分隔。\n"
    "不要只根据单个画面下结论,看不清的内容不要猜测。"
)


def build_vision_prompt(image_path: Path, question: str, lang: str = "zh") -> str:
    """技术性前置(读图路径、联系图说明、语言、禁改文件)由程序拼在用户问题前,用户只维护问题本身。"""
    lang_name = LANG_NAMES.get(lang or "zh", "中文")
    q = (question or "").strip() or DEFAULT_ANALYSIS_QUESTION
    return (
        f"请读取图片文件 {image_path} 。这是一张由同一个视频分镜多个时间点(按 #1、#2… 顺序)拼成的画面联系图。\n\n"
        f"{q}\n\n"
        f"回答用{lang_name},只输出回答正文(纯文本),不要任何前言、解释或格式标记。"
        "不要修改任何文件,不要运行命令,读完图片直接回答。"
    )


_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*\n?(.*?)\n?```\s*$", re.S)


def _strip_fences(text: str) -> str:
    text = (text or "").strip()
    m = _FENCE_RE.match(text)
    return m.group(1).strip() if m else text


def _iter_json_values(blob: str):
    """引擎输出可能是:单个(多行美化)JSON、JSONL 事件流、或前后夹杂文本的若干 JSON 对象。"""
    try:
        yield json.loads(blob)
        return
    except json.JSONDecodeError:
        pass
    got = False
    for line in blob.splitlines():
        line = line.strip()
        if line[:1] in "{[":
            try:
                yield json.loads(line)
                got = True
            except json.JSONDecodeError:
                pass
    if got:
        return
    dec, i = json.JSONDecoder(), 0
    while True:
        j = blob.find("{", i)
        if j < 0:
            break
        try:
            obj, end = dec.raw_decode(blob, j)
            yield obj
            i = end
        except json.JSONDecodeError:
            i = j + 1


def _walk_strings(obj, key=None, acc=None):
    """(key, value) 顺序遍历所有字符串叶子。"""
    if acc is None:
        acc = []
    if isinstance(obj, str):
        acc.append((key, obj))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _walk_strings(v, k, acc)
    elif isinstance(obj, list):
        for v in obj:
            _walk_strings(v, key, acc)
    return acc


def extract_final_text(blob: str, prompt: str = "") -> str:
    """从引擎输出里取最终回答文本(纯文本口径,不再解析 JSON 结构):
    1. 事件里最后一个 result 字段(claude/grok/kimi 的 result 汇总、deepagents runner);
    2. 否则最后一个非提示词回显的 text 字段(opencode/pi 事件流);
    3. 否则整段输出。统一剥掉 ``` 围栏。"""
    echo = (prompt or "").strip()[:60]
    values = list(_iter_json_values(blob))
    if not values:
        return _strip_fences(blob)
    result_text, last_text = "", ""
    for val in values:
        for key, text in _walk_strings(val):
            t = text.strip()
            if not t or (echo and echo in t):
                continue
            if key == "result":
                result_text = t
            elif key in ("text", "content", "output_text"):
                last_text = t
    return _strip_fences(result_text or last_text or blob)


def run_engine_vision(image_path: Path, prompt: str, spec: dict, cwd: Path,
                      timeout: int = 240) -> dict:
    """用当前默认 CLI 引擎做一次性画面分析,返回 {"text": 纯文本回答, "_meta": {...}}。

    spec = {"engine", "model", "executable", "permission_mode", "deepagents": {python, runner, base_url, api_key}}
    claude/kimi/grok/opencode/pi:提示词里给图片绝对路径,由 CLI 自带的文件读取工具看图;
    codex:走 `--image` + `-o` 取最后一条消息;deepagents:runner 无看图能力,仅文本兜底。"""
    engine = str(spec.get("engine") or "claude")
    model = str(spec.get("model") or "")
    exe = spec.get("executable")
    if engine != "deepagents" and not exe:
        raise FootageLibError(500, f"未找到 {engine} 命令行工具,请先安装或在设置里切换默认引擎")
    env = os.environ.copy()
    stdin_data = None
    with tempfile.TemporaryDirectory(prefix="footage-vision-") as tmp:
        tmpd = Path(tmp)
        if engine == "codex":
            outp = tmpd / "out.txt"
            cmd = [exe, "exec", "--skip-git-repo-check", "--sandbox", "workspace-write",
                   "-C", str(cwd), "--image", str(image_path), "-o", str(outp)]
            if model:
                cmd += ["-c", f"model={model}"]
            cmd.append("-")
            stdin_data = prompt.encode("utf-8")
        elif engine == "kimi":
            cmd = [exe, "--output-format", "stream-json"] + (["-m", model] if model else []) + ["-p", prompt]
        elif engine == "opencode":
            cmd = [exe, "run", "--format", "json", "--auto"] + (["-m", model] if model else []) + [prompt]
        elif engine == "grok":
            cmd = [exe, "-p", prompt, "--output-format", "streaming-messages-json", "--always-approve",
                   "--max-turns", "8"] + (["-m", model] if model else [])
        elif engine == "pi":
            cmd = [exe, "--mode", "json", "--approve"] + (["--model", model] if model else []) + [prompt]
        elif engine == "deepagents":
            da = spec.get("deepagents") or {}
            if not da.get("python") or not da.get("runner"):
                raise FootageLibError(500, "deepagents 引擎未配置")
            cmd = [da["python"], da["runner"], "--model", model or da.get("model", ""),
                   "--base-url", da.get("base_url", ""), "--api-key", da.get("api_key", "")]
            env["DA_SYSTEM"] = "你是视频画面分析助手,只输出 JSON。"
            env["DA_PROMPT"] = prompt
        else:  # claude
            cmd = [exe, "-p", prompt, "--output-format", "json", "--max-turns", "8",
                   "--permission-mode", str(spec.get("permission_mode") or "acceptEdits")]
            if model:
                cmd += ["--model", model]
        started = time.perf_counter()
        try:
            r = subprocess.run(cmd, cwd=str(cwd), env=env, input=stdin_data, capture_output=True,
                               timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise FootageLibError(504, f"{engine} 分析超时({timeout}s)") from exc
        stdout = (r.stdout or b"").decode("utf-8", errors="replace")
        stderr = (r.stderr or b"").decode("utf-8", errors="replace")
        text = ""
        if engine == "codex" and outp.is_file() and outp.stat().st_size:
            text = _strip_fences(outp.read_text(encoding="utf-8"))
        if not text:
            text = extract_final_text(stdout, prompt)
        if not text or (r.returncode != 0 and len(text) < 20):
            log = image_path.with_suffix(".vision.log")
            try:
                log.write_text(f"$ {' '.join(cmd)}\n--- exit {r.returncode}\n--- stdout\n{stdout}\n--- stderr\n{stderr}\n",
                               encoding="utf-8")
            except OSError:
                pass
            detail = (stderr.strip() or stdout.strip())[-400:] or f"退出码 {r.returncode}"
            raise FootageLibError(502, f"{engine} 未返回分析文本(原始输出见 {log.name}):{detail}")
        return {"text": text, "_meta": {"engine": engine, "model": model,
                                        "elapsed_s": round(time.perf_counter() - started, 1),
                                        "returncode": r.returncode}}


def analyze_clip(name: str, clip_id: str, spec: dict, lang: str = "zh") -> dict:
    """联系图 + 项目的分析问题 → 默认 CLI 引擎 → 纯文本回答直接写入 info 框并落盘。"""
    d = project_dir(name)
    sheet = make_contact_sheet(name, clip_id)
    question = str(load_project(name).get("analysis_prompt") or "").strip() or DEFAULT_ANALYSIS_QUESTION
    result = run_engine_vision(sheet, build_vision_prompt(sheet, question, lang), spec, d)
    meta = result.pop("_meta", {})
    with _LOCK:
        doc = load_clips(name)
        clip = _find_clip(doc, clip_id)
        clip["analysis"] = {"text": result["text"], "question": question}
        clip["analysis_meta"] = meta
        clip["info"] = result["text"]
        clip["analyzed_at"] = _now()
        clip["updated_at"] = clip["analyzed_at"]
        save_clips(name, doc)
        _update(name, analyzed_count=sum(1 for c in doc["clips"] if str(c.get("info") or "").strip()))
    return clip


def analyze_all(name: str, spec: dict, lang: str = "zh", force: bool = False) -> dict:
    """后台逐个分析(默认跳过已有信息的 clip);进度写 project.json。"""
    project_dir(name)
    if _job_running(name):
        raise FootageLibError(409, "该项目正在处理中")
    doc = load_clips(name)
    todo = [c["id"] for c in doc.get("clips", []) if force or not str(c.get("info") or "").strip()]
    if not todo:
        return {"ok": True, "started": False, "todo": 0}
    _update(name, status="analyzing", progress=0, message=f"AI 分析 0/{len(todo)}…", error="")
    _start(name, "analyze-all", job_args={"spec": spec, "lang": lang, "todo": todo})
    return {"ok": True, "started": True, "todo": len(todo)}


def _analyze_all_job(name: str, spec: dict, lang: str, todo: list[str]) -> None:
    failed = 0
    for n, cid in enumerate(todo, start=1):
        _update(name, status="analyzing", progress=n / len(todo) * 100,
                message=f"AI 分析 {n}/{len(todo)}({cid})…")
        try:
            analyze_clip(name, cid, spec, lang)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            _update(name, error=f"{cid}: {getattr(exc, 'detail', exc)}"[:600])
    _update(name, status="ready", progress=100,
            message=f"AI 分析完成:{len(todo) - failed}/{len(todo)}" + (f",失败 {failed}" if failed else ""))


def resolve_file(name: str, rel: str) -> Path:
    d = project_dir(name)
    p = (d / rel).resolve()
    try:
        p.relative_to(d.resolve())
    except ValueError as exc:
        raise FootageLibError(400, "非法路径") from exc
    if not p.is_file():
        raise FootageLibError(404, "文件不存在")
    return p


# ---------------- CLI(排障用) ----------------

def _cli() -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    p = sub.add_parser("create"); p.add_argument("--name")
    p = sub.add_parser("process"); p.add_argument("name")
    p = sub.add_parser("download"); p.add_argument("name"); p.add_argument("url")
    p = sub.add_parser("show"); p.add_argument("name")
    p = sub.add_parser("job", help="作业子进程入口(由 API 进程 _start 拉起)")
    p.add_argument("mode", choices=("process", "download", "transcribe", "analyze-all"))
    p.add_argument("name"); p.add_argument("extra", nargs="*")
    a = ap.parse_args()
    if a.cmd == "job":
        args = json.loads(os.environ.get("FOOTAGE_JOB_ARGS") or "{}")
        if a.mode == "process":
            _pipeline(a.name)
        elif a.mode == "download":
            _download_then_pipeline(a.name, a.extra[0])
        elif a.mode == "transcribe":
            _transcribe_job(a.name)
        elif a.mode == "analyze-all":
            _analyze_all_job(a.name, args.get("spec") or {}, args.get("lang") or "zh", args.get("todo") or [])
        return 0
    if a.cmd == "list":
        print(json.dumps(list_projects(), ensure_ascii=False, indent=2))
    elif a.cmd == "create":
        print(json.dumps(create_project(a.name), ensure_ascii=False, indent=2))
    elif a.cmd == "process":
        _pipeline(a.name)
        print(json.dumps(load_project(a.name), ensure_ascii=False, indent=2))
    elif a.cmd == "download":
        start_download(a.name, a.url)
        _JOBS[a.name].wait()
        print(json.dumps(load_project(a.name), ensure_ascii=False, indent=2))
    elif a.cmd == "show":
        print(json.dumps(get_project(a.name), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
