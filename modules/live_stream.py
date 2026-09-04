#!/usr/bin/env python3
"""直播(live stream):参考图(≤8 张)+ 提示词 → Fal 渠道视频模型循环生成 480p 短段,
每一轮把上一段的尾帧截图一并作为参考(或作首帧),前端把新段接在已播内容之后连续播放,
形成"一直在播"的直播效果。设置菜单「高级 → 直播」页(/live)的业务实现;API 由
services/runtime/core.py 的 api_live_* 薄封装调用,本模块不依赖 core。

目录布局(data/live/):
    settings.json                 页面最近一次的输入(提示词/模型/时长/画幅/衔接模式…)
    state.json                    当前会话 id
    refs/<id>.<ext>               参考图(页面上传,最多 8 张;启动时快照进会话,运行中冻结)
    sessions/<sid>/
      session.json                会话状态(作业子进程独占写):status/round/message/error/segments[]
      control.json                页面控制(API 进程独占写):运行中改提示词 / 观看心跳 / 停止标志
      refs/                       参考图快照(按需缩到长边 ≤1536 再内联为 data URI 提交)
      segments/seg_0001.mp4       生成的视频段(480p)
      segments/seg_0001.png       该段尾帧(下一轮参考)
      job.log                     作业日志

生成循环在子进程 `python3 modules/live_stream.py job <sid>` 里跑(与素材库同款隔离:API
进程不阻塞、停止=结束进程组);子进程收到 SIGTERM 后先向 Fal 发 cancel 再退出,尽量不为
已被放弃的片段付费。请求体沿用 modules/genmedia.py 的 _fal_video_body(家族/分辨率/时长/
参考上限校验与正式流水线同口径),轮询与取消在本模块实现。

已知取舍(页面提示里也有说明):
  * 单段生成通常要 1-3 分钟而片段只有几秒到十几秒,真正的"实时"做不到——前端在新段未到时
    重播最新一段,新段到了再接上;段越长,重播占比越低,但单段等得越久。
  * 尾帧是 480p 视频抽出的低清截图,连续多轮以它为参考会逐渐掉画质/人物走样;每轮始终同时
    带原始参考图作身份锚,能缓解但不能根除。
  * 每段音频独立生成,段间音频会有跳变;段间首尾帧也可能有轻微跳变。
  * 不停就一直计费:提供最多段数与"无人观看自动停止"两道闸。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("VIDEOAGENTS_DATA_DIR", ROOT / "data")).expanduser().resolve()
RUNTIME_DIR = Path(os.environ.get("VIDEOAGENTS_RUNTIME_DIR", DATA_DIR / ".videoagents")).expanduser().resolve()
CONFIG_PATH = Path(os.environ.get("VIDEOAGENTS_CONFIG_PATH", RUNTIME_DIR / "genconfig.json")).expanduser().resolve()
LIVE_DIR = DATA_DIR / "live"
REFS_DIR = LIVE_DIR / "refs"
SESSIONS_DIR = LIVE_DIR / "sessions"
SCHEMA_SESSION = "videoagents.live.session.v1"

MAX_REFS = 8
MAX_REF_BYTES = 20 * 1024 * 1024
REF_MAX_SIDE = 1536            # 快照时长边超过此值缩小(内联 data URI 提交,控制请求体)
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
RESOLUTIONS = ("480p", "768p")   # 页面档位;768p 提交时按 genmedia 口径传 720p(H3 → 768P,Seedance → 720p)
DEFAULT_RESOLUTION = "480p"
ASPECTS = ("16:9", "9:16")
LINK_MODES = ("refs_tail", "first_frame")   # 参考图+尾帧一起作参考 / 尾帧作首帧(不带参考图)
PROVIDERS = ("fal",)
DEFAULT_MODEL = "minimax/h3-max"
DEFAULTS = {"prompt": "", "provider": "fal", "model": DEFAULT_MODEL, "duration": 10,
            "aspect": "16:9", "resolution": DEFAULT_RESOLUTION, "link_mode": "refs_tail",
            "max_rounds": 0, "idle_stop_min": 10, "max_pending": 3,
            "generate_audio": True, "continue_last": False}
POLL_S = 3                     # Fal 轮询间隔(短:停止时要尽快发 cancel)
ROUND_TIMEOUT_S = 1800         # 单段生成上限
MAX_CONSEC_FAILS = 5           # 连续失败次数达到即停
MAX_PENDING_LIMIT = 10         # 待播队列上限的可设最大值(默认 3:未播片段达此数即暂停生成,降到之下再继续)
BACKOFF_S = (5, 15, 30, 60)    # 失败重试等待
SID_RE = re.compile(r"^ls\d{8}-\d{6}(-\d+)?$")
REF_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,80}$")
RUNNING_STATES = ("running",)

_LOCK = threading.RLock()
_JOBS: dict[str, subprocess.Popen] = {}     # sid -> 作业子进程
_LAST_SEEN_WRITE: dict[str, float] = {}     # sid -> 上次落盘心跳时间(限频)
_LAST_PLAYED: dict[str, int] = {}           # sid -> 上次上报的已播段号(变化才落盘)

CONTINUITY_REFS = ("\n\n[Continuity] The last reference image is the final frame of the previous "
                   "segment of this continuous live stream. Start exactly from that frame (same "
                   "scene, framing, characters, lighting) and continue the action naturally with "
                   "no cut, then keep it going.")
CONTINUITY_FIRST = ("\n\n[Continuity] The first frame is the final frame of the previous segment "
                    "of this continuous live stream: continue the action naturally with no cut.")


class LiveError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


# ---------------- 基础工具 ----------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _genmedia():
    mods = str(ROOT / "modules")
    if mods not in sys.path:
        sys.path.insert(0, mods)
    import genmedia  # noqa: WPS433
    return genmedia


def _fake_mode() -> bool:
    """本地联调开关:不调 Fal,用 ffmpeg 合成测试片段(见 _fake_generate)。"""
    return os.environ.get("VIDEOAGENTS_LIVE_FAKE", "").lower() in ("1", "true", "yes")


def fal_api_key() -> str:
    """与「🎨 生成模型」页视频 → Fal 标签页共用同一个 Key(环境变量 FAL_KEY 兜底)。"""
    cfg = _read_json(CONFIG_PATH, {})
    fal = ((cfg.get("video") or {}).get("fal") or {})
    return str(fal.get("api_key") or os.environ.get("FAL_KEY") or "").strip()


def _settings_path() -> Path:
    return LIVE_DIR / "settings.json"


def load_settings() -> dict:
    s = {**DEFAULTS, **_read_json(_settings_path(), {})}
    return normalize_settings(s, strict=False)


def normalize_settings(s: dict, strict: bool = True) -> dict:
    """页面输入 → 规整后的设置;strict 时非法值报 400(启动前校验),否则回落默认。"""
    out = dict(DEFAULTS)
    out["prompt"] = str(s.get("prompt") or "").strip()
    provider = str(s.get("provider") or "fal").strip()
    if provider not in PROVIDERS:
        if strict:
            raise LiveError(400, "直播目前只支持 Fal 渠道")
        provider = "fal"
    out["provider"] = provider
    out["model"] = str(s.get("model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    try:
        out["duration"] = int(round(float(s.get("duration", DEFAULTS["duration"]))))
    except (TypeError, ValueError):
        if strict:
            raise LiveError(400, "单段时长须为整数秒")
        out["duration"] = DEFAULTS["duration"]
    if not 3 <= out["duration"] <= 30:
        if strict:
            raise LiveError(400, "单段时长须在 3-30 秒之间(各模型上限不同,提交时再按模型校验)")
        out["duration"] = DEFAULTS["duration"]
    aspect = str(s.get("aspect") or "16:9")
    if aspect not in ASPECTS:
        if strict:
            raise LiveError(400, "画幅只能选 16:9 或 9:16")
        aspect = "16:9"
    out["aspect"] = aspect
    res = str(s.get("resolution") or DEFAULT_RESOLUTION).lower()
    if res not in RESOLUTIONS:
        if strict:
            raise LiveError(400, "分辨率只能选 480p 或 768p")
        res = DEFAULT_RESOLUTION
    out["resolution"] = res
    lm = str(s.get("link_mode") or "refs_tail")
    out["link_mode"] = lm if lm in LINK_MODES else "refs_tail"
    for key, lo, hi in (("max_rounds", 0, 100000), ("idle_stop_min", 0, 100000),
                        ("max_pending", 1, MAX_PENDING_LIMIT)):
        try:
            v = int(s.get(key, DEFAULTS[key]) or 0)
        except (TypeError, ValueError):
            v = DEFAULTS[key]
        out[key] = min(max(v, lo), hi)
    out["generate_audio"] = bool(s.get("generate_audio", True))
    out["continue_last"] = bool(s.get("continue_last", False))
    return out


def save_settings(s: dict) -> dict:
    s = normalize_settings(s, strict=False)
    _atomic_write_json(_settings_path(), s)
    return s


# ---------------- 参考图 ----------------

def _ref_ext(filename: str, data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    ext = Path(filename or "").suffix.lower()
    if ext in IMAGE_EXTS:
        return ".jpg" if ext == ".jpeg" else ext
    raise LiveError(400, "参考图只支持 PNG / JPG / WebP")


def list_refs() -> list[dict]:
    REFS_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for p in sorted(REFS_DIR.iterdir()):
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS and not p.name.startswith("."):
            rows.append({"id": p.stem, "file": f"refs/{p.name}", "name": p.name,
                         "size": p.stat().st_size})
    return rows


def add_ref(data: bytes, filename: str) -> dict:
    if not data:
        raise LiveError(400, "空文件")
    if len(data) > MAX_REF_BYTES:
        raise LiveError(400, "单张参考图不能超过 20MB")
    ext = _ref_ext(filename, data)
    with _LOCK:
        if _current_running():
            raise LiveError(409, "直播进行中,参考图已冻结;停止后再修改")
        if len(list_refs()) >= MAX_REFS:
            raise LiveError(400, f"参考图最多 {MAX_REFS} 张")
        stem = re.sub(r"[^A-Za-z0-9_-]", "", Path(filename or "ref").stem)[:40] or "ref"
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        name, i = f"{ts}_{stem}{ext}", 1
        while (REFS_DIR / name).exists():
            name, i = f"{ts}_{stem}-{i}{ext}", i + 1
        REFS_DIR.mkdir(parents=True, exist_ok=True)
        (REFS_DIR / name).write_bytes(data)
    return {"ok": True, "refs": list_refs()}


def delete_ref(ref_id: str) -> dict:
    if not REF_ID_RE.fullmatch(ref_id or ""):
        raise LiveError(400, "非法参考图 id")
    with _LOCK:
        if _current_running():
            raise LiveError(409, "直播进行中,参考图已冻结;停止后再修改")
        hit = [p for p in REFS_DIR.glob(f"{ref_id}.*") if p.is_file()]
        if not hit:
            raise LiveError(404, "参考图不存在")
        for p in hit:
            p.unlink()
    return {"ok": True, "refs": list_refs()}


# ---------------- 会话 ----------------

def _state() -> dict:
    return _read_json(LIVE_DIR / "state.json", {})


def _set_state(**fields) -> None:
    st = _state()
    st.update(fields)
    _atomic_write_json(LIVE_DIR / "state.json", st)


def session_dir(sid: str, must_exist: bool = True) -> Path:
    if not SID_RE.fullmatch(sid or ""):
        raise LiveError(400, "非法会话 id")
    d = SESSIONS_DIR / sid
    if must_exist and not (d / "session.json").is_file():
        raise LiveError(404, f"直播会话不存在:{sid}")
    return d


def load_session(sid: str) -> dict:
    return _read_json(session_dir(sid) / "session.json", {})


def _save_session(sid: str, sess: dict) -> None:
    sess["updated_at"] = _now()
    _atomic_write_json(session_dir(sid, must_exist=False) / "session.json", sess)


def _load_control(sid: str) -> dict:
    return _read_json(session_dir(sid, must_exist=False) / "control.json", {})


def _save_control(sid: str, **fields) -> dict:
    with _LOCK:
        c = _load_control(sid)
        c.update(fields)
        _atomic_write_json(session_dir(sid, must_exist=False) / "control.json", c)
    return c


def _new_sid() -> str:
    base = datetime.now().strftime("ls%Y%m%d-%H%M%S")
    sid, i = base, 1
    while (SESSIONS_DIR / sid).exists():
        sid, i = f"{base}-{i}", i + 1
    return sid


def _pid_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:  # noqa: BLE001
        return False


def _job_running(sid: str) -> bool:
    proc = _JOBS.get(sid)
    if proc is not None:
        return proc.poll() is None
    # 服务重启后 Popen 句柄丢失:按 session.json 里作业自报的 pid 判活(孤儿作业仍可被停止)
    sess = _read_json(SESSIONS_DIR / sid / "session.json", {})
    return sess.get("status") in RUNNING_STATES and _pid_alive(int(sess.get("pid") or 0))


def _current_running() -> bool:
    sid = str(_state().get("current") or "")
    return bool(sid) and _job_running(sid)


def _reap(sid: str) -> None:
    """作业子进程已退出但 session.json 仍是运行态(崩溃/被杀/服务重启)→ 收口为停止/失败。"""
    proc = _JOBS.get(sid)
    if proc is not None and proc.poll() is None:
        return
    sess = _read_json(SESSIONS_DIR / sid / "session.json", {})
    if sess.get("status") not in RUNNING_STATES:
        _JOBS.pop(sid, None)
        return
    if proc is None:
        if _pid_alive(int(sess.get("pid") or 0)):
            return   # 服务重启前拉起的作业仍在跑,继续接管
        sess.update(status="stopped", message="服务重启导致直播中断", gen_started_at=None)
    else:
        _JOBS.pop(sid, None)
        tail = ""
        log = SESSIONS_DIR / sid / "job.log"
        if log.is_file():
            tail = log.read_text(encoding="utf-8", errors="replace")[-800:]
        if proc.returncode == 0:
            sess.update(status="stopped", message=sess.get("message") or "已停止", gen_started_at=None)
        else:
            sess.update(status="failed", gen_started_at=None,
                        error=f"生成进程异常退出(code {proc.returncode})\n{tail}".strip()[:1200])
    _save_session(sid, sess)


def _last_frame_of(sess: dict) -> str:
    segs = sess.get("segments") or []
    return str(segs[-1].get("frame") or "") if segs else ""


def status(touch: bool = True, played: int | None = None) -> dict:
    """页面轮询用:参考图 + 设置 + 当前会话;touch=True 记录观看心跳(限频 15s 落盘),
    played=页面当前播到的段号(待播队列闸门依据,变化即落盘)。"""
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    sid = str(_state().get("current") or "")
    sess = None
    if sid and SID_RE.fullmatch(sid):
        with _LOCK:
            _reap(sid)
        sess = _read_json(SESSIONS_DIR / sid / "session.json", None)
        if sess is not None:
            running = sess.get("status") in RUNNING_STATES and _job_running(sid)
            sess["running"] = running
            if touch and running:
                now = time.time()
                fields = {}
                if now - _LAST_SEEN_WRITE.get(sid, 0) >= 15:
                    fields["last_seen"] = now
                if played is not None and played >= 0 and played != _LAST_PLAYED.get(sid):
                    fields["played_seq"] = int(played)
                if fields:
                    _LAST_SEEN_WRITE[sid] = now
                    _LAST_PLAYED[sid] = fields.get("played_seq", _LAST_PLAYED.get(sid, -1))
                    _save_control(sid, last_seen=now, **{k: v for k, v in fields.items() if k != "last_seen"})
            ctrl = _load_control(sid)
            if ctrl.get("prompt") is not None:
                sess["pending_prompt"] = ctrl["prompt"]
            gs = sess.get("gen_started_at")
            sess["gen_elapsed_s"] = int(time.time() - gs) if running and gs else None
            sess["dir"] = str(SESSIONS_DIR / sid)
    prev_frame = _last_frame_of(sess) if sess else ""
    return {"refs": list_refs(), "settings": load_settings(), "session": sess,
            "dir": str(LIVE_DIR), "max_refs": MAX_REFS, "resolutions": list(RESOLUTIONS),
            "max_pending_limit": MAX_PENDING_LIMIT,
            "prev_frame": f"sessions/{sid}/{prev_frame}" if prev_frame else "",
            "disk_bytes": _disk_bytes(), "fal_key_configured": bool(fal_api_key())}


def _disk_bytes() -> int:
    total = 0
    if SESSIONS_DIR.is_dir():
        for p in SESSIONS_DIR.rglob("*"):
            if p.is_file():
                total += p.stat().st_size
    return total


def start(fields: dict) -> dict:
    """新建会话并拉起生成子进程;参考图快照进会话目录(运行中冻结)。"""
    s = normalize_settings(fields or {}, strict=True)
    with _LOCK:
        if _current_running():
            raise LiveError(409, "直播已在进行中,先停止再启动")
        if not fal_api_key() and not _fake_mode():
            raise LiveError(400, "Fal API Key 未配置:请先在「🎨 生成模型」页 视频 → Fal 标签页填写并保存")
        refs = list_refs()
        if not s["prompt"] and not refs:
            raise LiveError(400, "提示词与参考图至少填一样")
        family = _genmedia_family(s["model"])
        prev = _state().get("current")
        start_frame = ""
        if s["continue_last"] and prev and SID_RE.fullmatch(str(prev)):
            old = _read_json(SESSIONS_DIR / prev / "session.json", {})
            lf = _last_frame_of(old)
            if lf and (SESSIONS_DIR / prev / lf).is_file():
                start_frame = str(SESSIONS_DIR / prev / lf)
        save_settings(s)
        sid = _new_sid()
        d = SESSIONS_DIR / sid
        (d / "refs").mkdir(parents=True, exist_ok=True)
        (d / "segments").mkdir(parents=True, exist_ok=True)
        snap = []
        for r in refs:
            src = REFS_DIR / r["name"]
            dst = d / "refs" / r["name"]
            shutil.copyfile(src, dst)
            snap.append(f"refs/{dst.name}")
        if start_frame:
            dst = d / "refs" / "start_frame.png"
            shutil.copyfile(start_frame, dst)
            start_frame = "refs/start_frame.png"
        sess = {"schema": SCHEMA_SESSION, "id": sid, "created_at": _now(), "status": "running",
                "refs": snap, "start_frame": start_frame,
                "segments": [], "round": 0, "fails": 0, "message": "启动中…", "error": "",
                "gen_started_at": None, "family": family,
                **{k: s[k] for k in ("prompt", "provider", "model", "duration", "aspect", "resolution",
                                     "link_mode", "max_rounds", "idle_stop_min", "max_pending",
                                     "generate_audio")}}
        _save_session(sid, sess)
        _atomic_write_json(d / "control.json", {"prompt": None, "last_seen": time.time(), "stop": False,
                                                "played_seq": 0})
        _LAST_PLAYED.pop(sid, None)
        _set_state(current=sid)
        _start_job(sid)
    return status(touch=True)


def _start_job(sid: str) -> None:
    d = session_dir(sid)
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    log = (d / "job.log").open("w", encoding="utf-8")
    kw = {"start_new_session": True} if os.name != "nt" else {}
    proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "job", sid],
                            cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT, **kw)
    log.close()
    _JOBS[sid] = proc


def stop() -> dict:
    """停止当前会话:先写停止标志,再 SIGTERM 进程组(子进程会先向 Fal 发 cancel),10s 不退强杀。"""
    with _LOCK:
        sid = str(_state().get("current") or "")
        if not sid or not _job_running(sid):
            if sid and SID_RE.fullmatch(sid):
                _reap(sid)
            return status(touch=False)
        _save_control(sid, stop=True)
        proc = _JOBS.get(sid)
        pid = proc.pid if proc is not None else int(_read_json(SESSIONS_DIR / sid / "session.json", {}).get("pid") or 0)
        try:
            if os.name != "nt":
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            else:
                proc.terminate() if proc is not None else os.kill(pid, signal.SIGTERM)
            if proc is not None:
                proc.wait(timeout=12)
            else:
                deadline = time.time() + 12
                while _pid_alive(pid) and time.time() < deadline:
                    time.sleep(0.3)
                if _pid_alive(pid):
                    raise TimeoutError
        except Exception:  # noqa: BLE001
            try:
                if proc is not None:
                    proc.kill()
                    proc.wait(timeout=5)
                else:
                    os.kill(pid, signal.SIGKILL)
            except Exception:  # noqa: BLE001
                pass
        _JOBS.pop(sid, None)
        sess = _read_json(SESSIONS_DIR / sid / "session.json", {})
        if sess.get("status") in RUNNING_STATES:
            sess.update(status="stopped", message="已停止", gen_started_at=None)
            _save_session(sid, sess)
    return status(touch=False)


def update_prompt(prompt: str) -> dict:
    """运行中改提示词:写 control.json,作业下一轮开始前读取生效;未运行则只更新设置。"""
    prompt = str(prompt or "").strip()
    s = load_settings()
    s["prompt"] = prompt
    save_settings(s)
    sid = str(_state().get("current") or "")
    if sid and _job_running(sid):
        _save_control(sid, prompt=prompt)
    return status(touch=True)


def clear_history() -> dict:
    """删除所有非运行中的会话目录(片段视频/尾帧),释放磁盘。"""
    with _LOCK:
        cur = str(_state().get("current") or "")
        removed = 0
        if SESSIONS_DIR.is_dir():
            for d in SESSIONS_DIR.iterdir():
                if not d.is_dir():
                    continue
                if d.name == cur and _job_running(cur):
                    continue
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
        if cur and not _job_running(cur):
            _set_state(current="")
    return {**status(touch=False), "removed": removed}


def resolve_file(rel: str) -> Path:
    p = (LIVE_DIR / rel).resolve()
    try:
        p.relative_to(LIVE_DIR.resolve())
    except ValueError as exc:
        raise LiveError(400, "非法路径") from exc
    if not p.is_file():
        raise LiveError(404, "文件不存在")
    return p


def _genmedia_family(model: str) -> str:
    m = (model or "").lower()
    if "seedance" in m:
        return "seedance"
    if "minimax" in m and "h3" in m:
        return "h3"
    if "kling" in m:
        return "kling"
    return "generic"


# ---------------- 作业子进程:生成循环 ----------------

class _Cancelled(Exception):
    pass


def _probe_duration(path: Path) -> float:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", str(path)], capture_output=True, text=True, timeout=30)
        return round(float(r.stdout.strip()), 2)
    except Exception:  # noqa: BLE001
        return 0.0


def _shrink_refs(d: Path, rels: list[str]) -> list[str]:
    """参考图快照缩到长边 ≤1536(JPEG q92);Pillow 缺失或失败时原样使用。"""
    out = []
    for rel in rels:
        src = d / rel
        try:
            from PIL import Image  # noqa: WPS433
            with Image.open(src) as im:
                w, h = im.size
                if max(w, h) <= REF_MAX_SIDE and src.stat().st_size <= 3 * 1024 * 1024:
                    out.append(rel)
                    continue
                scale = min(1.0, REF_MAX_SIDE / max(w, h))
                im = im.convert("RGB").resize((max(1, int(w * scale)), max(1, int(h * scale))))
                dst = src.with_name(src.stem + ".small.jpg")
                im.save(dst, "JPEG", quality=92)
                out.append(f"{Path(rel).parent}/{dst.name}")
        except Exception as exc:  # noqa: BLE001
            print(f"[live] 参考图 {rel} 未缩放,原样提交:{exc}", flush=True)
            out.append(rel)
    return out


def _fal_generate(gm, key: str, endpoint: str, body: dict, output: Path, stop: threading.Event,
                  on_status, should_stop) -> Path:
    headers = {"Authorization": f"Key {key}"}
    submit_url = f"{gm.FAL_QUEUE_BASE}/{endpoint}"
    job = gm._post_json(submit_url, body, headers)  # noqa: SLF001
    rid = job.get("request_id")
    if not rid:
        raise RuntimeError(f"Fal 任务创建失败:{json.dumps(job, ensure_ascii=False)[:400]}")
    status_url = job.get("status_url") or f"{submit_url}/requests/{rid}/status"
    response_url = job.get("response_url") or f"{submit_url}/requests/{rid}"
    cancel_url = job.get("cancel_url") or f"{submit_url}/requests/{rid}/cancel"
    print(f"[live] Fal 任务已创建 {rid}({endpoint})", flush=True)
    started = time.time()

    def cancel():
        try:
            gm._request(cancel_url, b"", headers, method="PUT", timeout=20)  # noqa: SLF001
            print(f"[live] 已向 Fal 发送取消 {rid}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[live] 取消 {rid} 失败(忽略):{str(exc)[:200]}", flush=True)

    while True:
        if stop.is_set() or should_stop():
            cancel()
            raise _Cancelled()
        if time.time() - started > ROUND_TIMEOUT_S:
            cancel()
            raise RuntimeError(f"Fal 视频超时({ROUND_TIMEOUT_S}s),request={rid}")
        stop.wait(POLL_S)
        try:
            st = gm._get_json(status_url, headers)  # noqa: SLF001
        except Exception as exc:  # noqa: BLE001
            print(f"[live] Fal 轮询异常(继续等待):{str(exc)[:200]}", flush=True)
            continue
        state = str(st.get("status") or "")
        on_status(state, st.get("queue_position"))
        if state == "COMPLETED":
            if st.get("error"):
                raise RuntimeError(f"Fal 视频任务失败({st.get('error_type') or 'error'}):"
                                   f"{str(st.get('error'))[:400]}")
            res = gm._get_json(response_url, headers, timeout=120)  # noqa: SLF001
            video = res.get("video")
            vurl = video.get("url") if isinstance(video, dict) else ""
            if not vurl:
                raise RuntimeError(f"Fal 任务成功但无视频 URL:{json.dumps(res, ensure_ascii=False)[:400]}")
            gm._save(gm._decode_data_url(vurl), str(output))  # noqa: SLF001
            return output


def _fake_generate(seq: int, sess: dict, output: Path, stop: threading.Event, on_status) -> Path:
    """本地联调用(VIDEOAGENTS_LIVE_FAKE=1):不调 Fal,ffmpeg 合成带段号的测试画面 + 提示音,
    模拟 6s 生成等待;用于验证循环/闸门/停止/播放,不产生费用。"""
    for i in range(3):
        if stop.is_set():
            raise _Cancelled()
        on_status("IN_QUEUE" if i == 0 else "IN_PROGRESS", 2 - i if i == 0 else None)
        stop.wait(2)
    w, h = (1366, 768) if sess.get("resolution") == "768p" else (854, 480)
    if sess.get("aspect") == "9:16":
        w, h = h, w
    dur = int(sess.get("duration") or 5)
    # testsrc2 自带走秒时钟,hue 偏移区分段号(不依赖 drawtext/字体)
    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc2=s={w}x{h}:d={dur}:r=24",
           "-f", "lavfi", "-i", f"sine=frequency={220 + seq * 40}:duration={dur}",
           "-vf", f"hue=h={(seq * 60) % 360}",
           "-shortest", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac",
           "-movflags", "+faststart", str(output)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode:
        raise RuntimeError(f"fake ffmpeg 失败:{r.stderr[-400:]}")
    return output


def _job(sid: str) -> int:
    d = session_dir(sid)
    stop = threading.Event()

    def on_term(signum, frame):  # noqa: ARG001
        print(f"[live] 收到信号 {signum},停止中…", flush=True)
        stop.set()

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)

    sess = load_session(sid)

    def save(**fields):
        sess.update(fields)
        _save_session(sid, sess)

    save(pid=os.getpid())

    fake = _fake_mode()
    key = fal_api_key() or ("fake" if fake else "")
    if not key:
        save(status="failed", error="Fal API Key 未配置", gen_started_at=None)
        return 1
    gm = _genmedia()
    cfg = {"provider": "fal", "api_key": key, "model": sess["model"]}
    family = gm._fal_family(sess["model"])  # noqa: SLF001
    refs = _shrink_refs(d, list(sess.get("refs") or []))
    prev_frame = str(sess.get("start_frame") or "")
    if sess.get("segments"):
        prev_frame = _last_frame_of(sess) or prev_frame
    if family == "kling" and refs:
        print("[live] Kling 端点不支持参考图:首轮用第 1 张参考图作首帧,其余参考图不生效", flush=True)
    seq = len(sess.get("segments") or [])
    fails = 0
    while not stop.is_set():
        ctrl = _load_control(sid)
        if ctrl.get("stop"):
            break
        idle_min = int(sess.get("idle_stop_min") or 0)
        if idle_min and time.time() - float(ctrl.get("last_seen") or time.time()) > idle_min * 60:
            save(status="stopped", message=f"超过 {idle_min} 分钟无人观看,已自动停止", gen_started_at=None)
            return 0
        max_rounds = int(sess.get("max_rounds") or 0)
        if max_rounds and seq >= max_rounds:
            save(status="stopped", message=f"已生成 {seq} 段,达到最多段数上限", gen_started_at=None)
            return 0
        # 待播队列闸:未播片段(已生成段号 - 页面播到的段号)达上限即暂停生成,降到之下再继续
        held = False
        max_pending = int(sess.get("max_pending") or 3)
        while not stop.is_set() and not ctrl.get("stop"):
            pending = seq - max(int(ctrl.get("played_seq") or 0), 0)
            if pending < max_pending:
                break
            if not held:
                held = True
                save(message=f"待播队列已满({pending} 段未播,上限 {max_pending}),等待播放后继续生成…",
                     gen_started_at=None)
            stop.wait(2)
            ctrl = _load_control(sid)
            idle_min = int(sess.get("idle_stop_min") or 0)
            if idle_min and time.time() - float(ctrl.get("last_seen") or time.time()) > idle_min * 60:
                save(status="stopped", message=f"超过 {idle_min} 分钟无人观看,已自动停止", gen_started_at=None)
                return 0
        if stop.is_set() or ctrl.get("stop"):
            break
        if ctrl.get("prompt") is not None and ctrl["prompt"] != sess.get("prompt"):
            save(prompt=ctrl["prompt"])
        seq += 1
        prompt = str(sess.get("prompt") or "")
        link_mode = sess.get("link_mode") or "refs_tail"
        first, round_refs = "", []
        if family == "kling":
            first = prev_frame or (refs[0] if refs else "")
        elif link_mode == "first_frame" and prev_frame:
            first = prev_frame
        else:
            round_refs = list(refs) + ([prev_frame] if prev_frame else [])
        if prev_frame:
            prompt = (prompt + (CONTINUITY_FIRST if first else CONTINUITY_REFS)).strip()
        elif not prompt:
            prompt = "A continuous live-stream shot of the subject in the reference images."
        out = d / "segments" / f"seg_{seq:04d}.mp4"
        frame = d / "segments" / f"seg_{seq:04d}.png"
        save(round=seq, message=f"第 {seq} 段:提交中…", gen_started_at=time.time(), error="")
        try:
            # 768p 档按 genmedia 口径传 720p:H3 映射为 768P,Seedance 为 720p,Kling 无分辨率参数
            gm_res = "720p" if str(sess.get("resolution") or "480p") == "768p" else "480p"
            endpoint, body = gm._fal_video_body(  # noqa: SLF001
                cfg, prompt, str(d / first) if first else "", "", sess["duration"], gm_res,
                sess["aspect"], None, [str(d / r) for r in round_refs], None,
                bool(sess.get("generate_audio", True)) if family != "h3" else None, None)

            def on_status(state, pos, _seq=seq):
                waited = int(time.time() - (sess.get("gen_started_at") or time.time()))
                txt = {"IN_QUEUE": "排队中", "IN_PROGRESS": "生成中", "COMPLETED": "下载中"}.get(state, state or "?")
                save(message=f"第 {_seq} 段:{txt}" + (f"(排队位 {pos})" if pos is not None else "")
                     + f",已等待 {waited}s")

            if fake:
                _fake_generate(seq, sess, out, stop, on_status)
            else:
                _fal_generate(gm, key, endpoint, body, out, stop, on_status,
                              lambda: bool(_load_control(sid).get("stop")))
            save(message=f"第 {seq} 段:抽取尾帧…")
            gm._extract_last_frame(str(out), str(frame))  # noqa: SLF001
            dur = _probe_duration(out) or float(sess["duration"])
            segs = list(sess.get("segments") or [])
            segs.append({"seq": seq, "file": f"segments/{out.name}", "frame": f"segments/{frame.name}",
                         "duration_s": dur, "size": out.stat().st_size, "created_at": _now(),
                         "gen_seconds": int(time.time() - (sess.get("gen_started_at") or time.time())),
                         "prompt": prompt})
            prev_frame = f"segments/{frame.name}"
            fails = 0
            save(segments=segs, fails=0, message=f"第 {seq} 段完成,准备下一段…", gen_started_at=None)
        except _Cancelled:
            seq -= 1
            for p in (out, frame):
                if p.exists():
                    p.unlink()
            break
        except Exception as exc:  # noqa: BLE001
            seq -= 1
            fails += 1
            err = f"第 {seq + 1} 段生成失败({fails}/{MAX_CONSEC_FAILS}):{str(exc)[:600]}"
            print(f"[live] {err}", flush=True)
            for p in (out, frame):
                if p.exists():
                    p.unlink()
            if fails >= MAX_CONSEC_FAILS:
                save(status="failed", error=err, fails=fails, gen_started_at=None,
                     message=f"连续失败 {fails} 次,已停止")
                return 1
            delay = BACKOFF_S[min(fails, len(BACKOFF_S)) - 1]
            save(error=err, fails=fails, gen_started_at=None, message=f"{delay}s 后重试…")
            stop.wait(delay)
    save(status="stopped", message="已停止", gen_started_at=None)
    return 0


# ---------------- CLI ----------------

def _cli() -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    p = sub.add_parser("job", help="作业子进程入口(由 API 进程拉起)")
    p.add_argument("sid")
    a = ap.parse_args()
    if a.cmd == "job":
        return _job(a.sid)
    print(json.dumps(status(touch=False), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
