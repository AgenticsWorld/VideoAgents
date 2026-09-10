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
    顶栏「会话」菜单:新建会话=清空当前会话/设置回默认/清空参考图(不删会话目录);历史会话=列表里
    加载(切为当前会话回放片段,可选恢复其提示词/设置与参考图快照)或单独删除;运行中均不允许。

生成循环在子进程 `python3 modules/live_stream.py job <sid>` 里跑(与素材库同款隔离:API
进程不阻塞、停止=结束进程组);子进程收到 SIGTERM 后先向 Fal 发 cancel 再退出,尽量不为
已被放弃的片段付费。请求体沿用 modules/genmedia.py 的 _fal_video_body(家族/分辨率/时长/
参考上限校验与正式流水线同口径),轮询与取消在本模块实现。

导演模式(link_mode=director,模型固定 minimax/h3-max/director):不走队列端点,而是 Fal 的 WMA
实时协议——浏览器把 SDP offer POST 到 wma.fal.run/session(本模块代为附加 Key,Key 不下发页面),
视频/音频经 WebRTC 直达页面 <video>,提示词经 data channel 随时改,fal 侧连续生成(自带前文记忆,
无需尾帧衔接)。没有子进程:会话按页面轮询/心跳判活(DIRECTOR_STALE_S 秒无心跳视为断线);页面用
MediaRecorder 把收到的流分块上传(segments/recording.*),停止后转码为 seg_0001.mp4 并抽尾帧,
历史会话回放与「从上次尾帧继续」由此仍然可用。官方限制:单会话默认最长 2 分钟(更长需申请),
最少按 60 秒计费。

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
LINK_MODES = ("refs_tail", "first_frame", "none", "director")   # 参考图+尾帧一起作参考 / 尾帧作首帧(不带参考图) / 不衔接(纯文生视频) / 导演模式(实时 WebRTC)
DIRECTOR_MODEL = "minimax/h3-max/director"   # 导演模式固定模型:Fal WMA 实时端点,不在 genmedia 目录内
WMA_URL = "https://wma.fal.run"              # Fal WMA 信令桥(/ice、/session、/session/heartbeat)
FAL_REST_URL = "https://rest.fal.ai"         # 首帧图上传到 fal 存储(数据通道单条消息有体积上限,不内联)
WMA_BRIDGE_PATHS = ("ice", "session", "session/heartbeat")
DIRECTOR_STALE_S = 45                        # 导演模式:页面轮询/心跳中断超过此秒数视为断线(fal 侧心跳 5s 一停会话即失效)
DIRECTOR_MEMORY_RANGE = (1, 50)              # configure.memory:保留多少段前文提示词作上下文
DIRECTOR_INLINE_MAX_SIDE = 480               # fal 存储不可用时首帧内联 data URI 的长边(受数据通道消息上限约束)
RECORD_MAX_BYTES = 4 * 1024 ** 3             # 单会话录像上限
PROVIDERS = ("fal",)
DEFAULT_MODEL = "minimax/h3-max"
DEFAULTS = {"prompt": "", "provider": "fal", "model": DEFAULT_MODEL, "duration": 10,
            "aspect": "16:9", "resolution": DEFAULT_RESOLUTION, "link_mode": "refs_tail",
            "max_rounds": 0, "idle_stop_min": 10, "max_pending": 3,
            "generate_audio": True, "continue_last": False, "memory": 12}
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
_DIRECTOR_SEEN: dict[str, float] = {}       # sid -> 导演模式最近一次页面轮询/心跳时间(内存,判活用)

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
    # 导演模式模型固定;离开导演模式时把固定模型换回默认(它不是队列端点,循环模式用不了)
    if out["link_mode"] == "director":
        out["model"] = DIRECTOR_MODEL
    elif out["model"] == DIRECTOR_MODEL:
        out["model"] = DEFAULT_MODEL
    try:
        mem = int(s.get("memory", DEFAULTS["memory"]) or DEFAULTS["memory"])
    except (TypeError, ValueError):
        mem = DEFAULTS["memory"]
    out["memory"] = min(max(mem, DIRECTOR_MEMORY_RANGE[0]), DIRECTOR_MEMORY_RANGE[1])
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
    if sess.get("status") not in RUNNING_STATES:
        return False
    if sess.get("mode") == "director":
        return _director_alive(sid)     # 无子进程:按页面心跳判活
    return _pid_alive(int(sess.get("pid") or 0))


def _director_alive(sid: str) -> bool:
    seen = max(float(_load_control(sid).get("last_seen") or 0), _DIRECTOR_SEEN.get(sid, 0.0))
    return time.time() - seen < DIRECTOR_STALE_S


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
    if sess.get("mode") == "director":
        if _director_alive(sid):
            return
        sess.update(status="stopped", gen_started_at=None,
                    message=f"页面心跳中断超过 {DIRECTOR_STALE_S}s,导演模式直播已结束(fal 会话随心跳停止而失效)")
        _save_session(sid, sess)
        _finalize_recording_async(sid)
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
                if sess.get("mode") == "director":
                    _DIRECTOR_SEEN[sid] = now
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
            "director_model": DIRECTOR_MODEL, "director_stale_s": DIRECTOR_STALE_S,
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
        if s["link_mode"] == "none" and not s["prompt"]:
            raise LiveError(400, "不衔接(文生视频)模式不提交参考图与尾帧,必须填提示词")
        director = s["link_mode"] == "director"
        if director and not s["prompt"]:
            raise LiveError(400, "导演模式必须填提示词(参考图只取第 1 张作首帧,可不填)")
        family = "director" if director else _genmedia_family(s["model"])
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
                "mode": "director" if director else "loop",
                "refs": snap, "start_frame": start_frame,
                "segments": [], "round": 0, "fails": 0, "message": "启动中…", "error": "",
                "gen_started_at": None, "family": family,
                **{k: s[k] for k in ("prompt", "provider", "model", "duration", "aspect", "resolution",
                                     "link_mode", "max_rounds", "idle_stop_min", "max_pending",
                                     "generate_audio", "memory")}}
        if director:
            sess["message"] = "等待页面建立 WebRTC 连接…"
            sess["director"] = {"wma_session_id": "", "connected": False, "started_at": None,
                                "prompt_version": 1, "chunks": 0, "playback_s": 0.0, "gen_s": 0.0,
                                "buffer_depth": 0, "route": "", "rec_seq": 0, "rec_bytes": 0, "rec_ext": ""}
        _save_session(sid, sess)
        _atomic_write_json(d / "control.json", {"prompt": None, "last_seen": time.time(), "stop": False,
                                                "played_seq": 0})
        _LAST_PLAYED.pop(sid, None)
        _DIRECTOR_SEEN[sid] = time.time()
        _set_state(current=sid)
        if director:
            _prepare_director_first_frame(sid, d, start_frame or (snap[0] if snap else ""))
        else:
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
        if sid and SID_RE.fullmatch(sid):
            sess = _read_json(SESSIONS_DIR / sid / "session.json", {})
            if sess.get("mode") == "director":
                # 导演模式没有子进程:页面已关闭 WebRTC(fal 会话随心跳停止失效),这里只收口状态并转码录像
                if sess.get("status") in RUNNING_STATES:
                    sess.update(status="stopped", message="已停止", gen_started_at=None)
                    _save_session(sid, sess)
                    _finalize_recording_async(sid)
                return status(touch=False)
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
        with _LOCK:
            sess = load_session(sid)
            if sess.get("mode") == "director":
                # 导演模式:立即生效——服务端发号 prompt_version,页面拿到后经 data channel 发 prompt 消息
                sess["prompt"] = prompt
                dd = sess.setdefault("director", {})
                dd["prompt_version"] = int(dd.get("prompt_version") or 1) + 1
                _save_session(sid, sess)
            else:
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


def _session_row(sid: str, cur: str) -> dict | None:
    d = SESSIONS_DIR / sid
    sess = _read_json(d / "session.json", None)
    if not isinstance(sess, dict):
        return None
    segs = sess.get("segments") or []
    size = 0
    for p in d.rglob("*"):
        if p.is_file():
            size += p.stat().st_size
    last = str(segs[-1].get("frame") or "") if segs else ""
    return {"id": sid, "created_at": sess.get("created_at") or "", "updated_at": sess.get("updated_at") or "",
            "status": sess.get("status") or "", "current": sid == cur, "mode": sess.get("mode") or "loop",
            "running": sess.get("status") in RUNNING_STATES and _job_running(sid),
            "model": sess.get("model") or "", "aspect": sess.get("aspect") or "",
            "resolution": sess.get("resolution") or DEFAULT_RESOLUTION, "duration": sess.get("duration") or 0,
            "prompt": str(sess.get("prompt") or "")[:300], "refs": len(sess.get("refs") or []),
            "segments": len(segs), "total_s": round(sum(float(x.get("duration_s") or 0) for x in segs), 1),
            "size": size, "message": sess.get("message") or "", "error": str(sess.get("error") or "")[:300],
            "frame": f"sessions/{sid}/{last}" if last and (d / last).is_file() else ""}


def list_sessions() -> list[dict]:
    """历史会话列表(新→旧),供页面「📚 历史会话」加载/删除。"""
    rows = []
    if not SESSIONS_DIR.is_dir():
        return rows
    cur = str(_state().get("current") or "")
    for d in sorted(SESSIONS_DIR.iterdir(), reverse=True):
        if not d.is_dir() or not SID_RE.fullmatch(d.name):
            continue
        with _LOCK:
            _reap(d.name)
        row = _session_row(d.name, cur)
        if row:
            rows.append(row)
    return rows


def select_session(sid: str, apply_settings: bool = True, restore_refs: bool = True) -> dict:
    """把历史会话切为当前会话(页面回放其片段,「从上次直播的尾帧继续」也以它为准);
    apply_settings=用它的提示词/模型/时长等覆盖页面设置;restore_refs=用它的参考图快照替换当前参考图。
    运行中不允许(会话切换会让轮询/闸门错位)。"""
    with _LOCK:
        if _current_running():
            raise LiveError(409, "直播进行中,停止后才能加载历史会话")
        d = session_dir(sid)
        _reap(sid)
        sess = load_session(sid)
        if apply_settings:
            s = load_settings()
            for k in ("prompt", "provider", "model", "duration", "aspect", "resolution", "link_mode",
                      "max_rounds", "idle_stop_min", "max_pending", "generate_audio", "memory"):
                if k in sess:
                    s[k] = sess[k]
            save_settings(s)
        if restore_refs:
            REFS_DIR.mkdir(parents=True, exist_ok=True)
            for p in list(REFS_DIR.iterdir()):
                if p.is_file() and not p.name.startswith("."):
                    p.unlink()
            for rel in (sess.get("refs") or [])[:MAX_REFS]:
                src = d / str(rel)
                if src.is_file() and src.suffix.lower() in IMAGE_EXTS:
                    shutil.copyfile(src, REFS_DIR / src.name)
        _LAST_PLAYED.pop(sid, None)
        _set_state(current=sid)
    return status(touch=False)


def new_session() -> dict:
    """页面「会话 → 新建会话」:清空当前会话(不删目录)、设置恢复默认、清空参考图;运行中拒绝。"""
    with _LOCK:
        if _current_running():
            raise LiveError(409, "直播进行中,停止后才能新建会话")
        _set_state(current="")
        save_settings(dict(DEFAULTS))
        if REFS_DIR.is_dir():
            for p in list(REFS_DIR.iterdir()):
                if p.is_file() and not p.name.startswith("."):
                    p.unlink()
    return status(touch=False)


def delete_session(sid: str) -> dict:
    """删除单个历史会话目录;运行中的会话不能删;删的是当前会话则清空 current。"""
    with _LOCK:
        d = session_dir(sid)
        if _job_running(sid):
            raise LiveError(409, "该会话正在直播中,先停止再删除")
        shutil.rmtree(d, ignore_errors=True)
        _JOBS.pop(sid, None)
        _LAST_PLAYED.pop(sid, None)
        if str(_state().get("current") or "") == sid:
            _set_state(current="")
    return status(touch=False)


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
    if "wan-3" in m or "wan3" in m:
        return "wan"
    return "generic"


# ---------------- 导演模式(minimax/h3-max/director:Fal WMA 实时 WebRTC) ----------------
# 页面直连 WebRTC(媒体不经本服务);本节只做三件事:代附 Key 转发信令桥请求、记账(状态/事件/
# 提示词版本)、录像分块落盘 + 停止后转码。Key 不下发页面。

def _http_json(url: str, payload: dict | None = None, headers: dict | None = None,
               method: str | None = None, timeout: int = 60, raw: bytes | None = None,
               content_type: str | None = None) -> tuple[int, object]:
    """小型 HTTP 工具(API 进程内用,不引入 genmedia):返回 (HTTP 状态码, JSON 或文本)。"""
    import urllib.error
    import urllib.request
    h = dict(headers or {})
    data = raw
    if payload is not None:
        data = json.dumps(payload).encode()
        h.setdefault("Content-Type", "application/json")
    elif content_type:
        h.setdefault("Content-Type", content_type)
    req = urllib.request.Request(url, data=data, headers=h, method=method or ("POST" if data is not None else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            code = r.status
    except urllib.error.HTTPError as e:
        body = e.read()
        code = e.code
    except Exception as exc:  # noqa: BLE001
        raise LiveError(502, f"请求 {url} 失败:{str(exc)[:300]}") from exc
    text = body.decode("utf-8", "replace")
    try:
        return code, json.loads(text) if text.strip() else {}
    except json.JSONDecodeError:
        return code, text[:800]


def _fal_storage_upload(key: str, data: bytes, content_type: str, filename: str) -> str:
    """把文件上传到 fal 存储(与 @fal-ai/client storage.upload 同协议),返回可被端点读取的 URL。"""
    code, j = _http_json(f"{FAL_REST_URL}/storage/upload/initiate?storage_type=fal-cdn-v3",
                         {"content_type": content_type, "file_name": filename},
                         {"Authorization": f"Key {key}"}, timeout=30)
    if code >= 400 or not isinstance(j, dict) or not j.get("upload_url") or not j.get("file_url"):
        raise RuntimeError(f"initiate HTTP {code}:{str(j)[:300]}")
    code2, j2 = _http_json(str(j["upload_url"]), raw=data, content_type=content_type, method="PUT", timeout=120)
    if code2 >= 400:
        raise RuntimeError(f"PUT HTTP {code2}:{str(j2)[:300]}")
    return str(j["file_url"])


def _prepare_director_first_frame(sid: str, d: Path, rel: str) -> None:
    """导演模式首帧(configure.image_url):优先上传 fal 存储;失败则缩到很小内联 data URI
    (data channel 单条消息有上限,大图会被拒)。结果写 director.json,由 /live/director/config 下发。"""
    info = {"image_src": rel, "image_url": "", "note": ""}
    if rel:
        src = d / rel
        try:
            small = d / _shrink_refs(d, [rel])[0]
            mime = "image/png" if small.suffix.lower() == ".png" else "image/jpeg"
            if small.suffix.lower() == ".webp":
                mime = "image/webp"
            key = fal_api_key()
            if not key:
                raise RuntimeError("Fal Key 未配置")
            info["image_url"] = _fal_storage_upload(key, small.read_bytes(), mime, small.name)
            info["note"] = "首帧已上传 fal 存储"
        except Exception as exc:  # noqa: BLE001
            try:
                from PIL import Image  # noqa: WPS433
                import base64
                import io
                with Image.open(src) as im:
                    w, h = im.size
                    scale = min(1.0, DIRECTOR_INLINE_MAX_SIDE / max(w, h))
                    im = im.convert("RGB").resize((max(1, int(w * scale)), max(1, int(h * scale))))
                    buf = io.BytesIO()
                    im.save(buf, "JPEG", quality=70)
                info["image_url"] = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
                info["note"] = f"fal 存储上传失败({str(exc)[:160]}),首帧改为 {DIRECTOR_INLINE_MAX_SIDE}px 内联提交"
            except Exception as exc2:  # noqa: BLE001
                info["note"] = f"首帧不可用(上传失败:{str(exc)[:160]};内联失败:{str(exc2)[:120]}),按纯提示词开播"
    _atomic_write_json(d / "director.json", info)
    with _LOCK:
        sess = load_session(sid)
        if info["note"]:
            sess["message"] = info["note"] + ";等待页面建立 WebRTC 连接…"
            _save_session(sid, sess)


def _current_director(require_running: bool = True) -> tuple[str, dict]:
    sid = str(_state().get("current") or "")
    if not sid or not SID_RE.fullmatch(sid):
        raise LiveError(409, "当前没有直播会话")
    sess = _read_json(SESSIONS_DIR / sid / "session.json", {})
    if sess.get("mode") != "director":
        raise LiveError(409, "当前会话不是导演模式")
    if require_running and not (sess.get("status") in RUNNING_STATES and _director_alive(sid)):
        raise LiveError(409, "导演模式直播未在进行中")
    return sid, sess


def director_bridge(path: str, body: dict) -> dict:
    """代页面向 Fal WMA 信令桥发请求(附 Key):ice / session(SDP offer→answer)/ session/heartbeat。"""
    path = (path or "").strip("/")
    if path not in WMA_BRIDGE_PATHS:
        raise LiveError(404, f"未知信令路径:{path}")
    key = fal_api_key()
    if not key:
        raise LiveError(400, "Fal API Key 未配置:请先在「🎨 生成模型」页 视频 → Fal 标签页填写并保存")
    sid, sess = _current_director(require_running=True)
    body = dict(body or {})
    if path in ("ice", "session"):
        body["app_id"] = DIRECTOR_MODEL
    if path == "session":
        if not body.get("sdp") or body.get("type") != "offer":
            raise LiveError(400, "session 请求须带 SDP offer(sdp/type)")
    if path == "session/heartbeat":
        body = {"session_id": str(body.get("session_id") or (sess.get("director") or {}).get("wma_session_id") or "")}
        if not body["session_id"]:
            raise LiveError(400, "缺少 session_id")
    code, data = _http_json(f"{WMA_URL}/{path}", body, {"Authorization": f"Key {key}"},
                            timeout=120 if path == "session" else 20)
    _DIRECTOR_SEEN[sid] = time.time()
    if code >= 400:
        detail = data
        if isinstance(data, dict):
            detail = data.get("error") or data.get("message") or data.get("detail") or data
        raise LiveError(502, f"Fal WMA /{path} HTTP {code}:{str(detail)[:400]}")
    if not isinstance(data, dict):
        raise LiveError(502, f"Fal WMA /{path} 返回非 JSON:{str(data)[:200]}")
    if path == "session":
        with _LOCK:
            sess = load_session(sid)
            dd = sess.setdefault("director", {})
            dd["wma_session_id"] = str(data.get("session_id") or "")
            sess["message"] = "信令完成,建立媒体连接…"
            _save_session(sid, sess)
    return data


def director_config() -> dict:
    """页面建立连接后发 configure 消息所需的字段(首帧 URL 不进状态轮询,单独取)。"""
    sid, sess = _current_director(require_running=False)
    info = _read_json(SESSIONS_DIR / sid / "director.json", {})
    dd = sess.get("director") or {}
    return {"session": sid, "app_id": DIRECTOR_MODEL, "prompt": str(sess.get("prompt") or ""),
            "prompt_version": int(dd.get("prompt_version") or 1),
            "image_url": str(info.get("image_url") or ""), "image_source": str(info.get("image_src") or ""),
            "image_note": str(info.get("note") or ""),
            "resolution": str(sess.get("resolution") or DEFAULT_RESOLUTION),
            "aspect_ratio": str(sess.get("aspect") or "16:9"),
            "memory": int(sess.get("memory") or DEFAULTS["memory"])}


def director_event(body: dict) -> dict:
    """页面上报连接/画面块/结束/失败事件,写进 session.json 供轮询与历史列表显示。"""
    body = dict(body or {})
    sid, sess = _current_director(require_running=False)
    if sess.get("status") not in RUNNING_STATES:
        return status(touch=False)
    kind = str(body.get("type") or "")
    finalize = False
    with _LOCK:
        sess = load_session(sid)
        dd = sess.setdefault("director", {})
        for k in ("chunks", "buffer_depth"):
            if body.get(k) is not None:
                dd[k] = max(0, int(body[k]))
        for k in ("playback_s", "gen_s"):
            if body.get(k) is not None:
                dd[k] = round(max(0.0, float(body[k])), 1)
        if body.get("route"):
            dd["route"] = str(body["route"])[:40]
        if body.get("wma_session_id"):
            dd["wma_session_id"] = str(body["wma_session_id"])[:80]
        if kind == "connected":
            dd["connected"] = True
            dd["started_at"] = dd.get("started_at") or time.time()
            sess["gen_started_at"] = dd["started_at"]
            sess["message"] = "已连接,已发送 configure,等待首个画面块…"
        elif kind == "chunk":
            sess["message"] = (f"实时生成中:已收 {dd.get('chunks', 0)} 块 / 播放 {dd.get('playback_s', 0)}s"
                               f" / 生成耗时 {dd.get('gen_s', 0)}s / 缓冲 {dd.get('buffer_depth', 0)} 块")
        elif kind == "message":
            sess["message"] = str(body.get("message") or "")[:300]
        elif kind == "ended":
            sess.update(status="stopped", gen_started_at=None,
                        message=str(body.get("message") or "fal 会话已结束")[:300])
            finalize = True
        elif kind == "failed":
            sess.update(status="failed", gen_started_at=None,
                        error=str(body.get("error") or "连接失败")[:600],
                        message=str(body.get("message") or "导演模式连接失败")[:300])
            finalize = True
        else:
            raise LiveError(400, f"未知事件类型:{kind}")
        if body.get("error") and kind not in ("failed",):
            sess["error"] = str(body["error"])[:600]
        _save_session(sid, sess)
    _DIRECTOR_SEEN[sid] = time.time()
    if finalize:
        _finalize_recording_async(sid)
    return status(touch=not finalize)


def director_record(seq: int, data: bytes, ext: str = "") -> dict:
    """页面 MediaRecorder 分块上传:按序追加到 segments/recording.<ext>(乱序/重复块丢弃)。"""
    sid, sess = _current_director(require_running=False)
    if sess.get("status") not in RUNNING_STATES:
        raise LiveError(409, "会话已结束,不再接收录像块")
    if not data:
        return {"ok": True, "skipped": True}
    ext = re.sub(r"[^a-z0-9]", "", (ext or "").lower())[:8] or "webm"
    with _LOCK:
        sess = load_session(sid)
        dd = sess.setdefault("director", {})
        if int(dd.get("rec_bytes") or 0) + len(data) > RECORD_MAX_BYTES:
            raise LiveError(413, "录像已达上限,不再追加")
        last = int(dd.get("rec_seq") or 0)
        if seq <= last:
            return {"ok": True, "skipped": True, "seq": last}
        if not dd.get("rec_ext"):
            dd["rec_ext"] = ext
        path = SESSIONS_DIR / sid / "segments" / f"recording.{dd['rec_ext']}"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as f:
            f.write(data)
        dd["rec_seq"] = seq
        dd["rec_bytes"] = int(dd.get("rec_bytes") or 0) + len(data)
        _save_session(sid, sess)
    _DIRECTOR_SEEN[sid] = time.time()
    return {"ok": True, "seq": seq, "bytes": dd["rec_bytes"]}


def _finalize_recording_async(sid: str) -> None:
    threading.Thread(target=_finalize_recording, args=(sid,), daemon=True, name=f"live-finalize-{sid}").start()


def _finalize_recording(sid: str) -> None:
    """停止后把录像转成 seg_0001.mp4(fMP4 优先直接 remux,失败/WebM 则转码 H.264+AAC)并抽尾帧,
    作为该会话唯一片段——历史回放与「从上次尾帧继续」由此可用。"""
    d = SESSIONS_DIR / sid
    with _LOCK:
        sess = _read_json(d / "session.json", {})
        dd = sess.get("director") or {}
        ext = str(dd.get("rec_ext") or "")
        src = d / "segments" / f"recording.{ext}" if ext else None
        if sess.get("finalizing") or any(str(x.get("file") or "").endswith("seg_0001.mp4") for x in sess.get("segments") or []):
            return
        if not src or not src.is_file() or src.stat().st_size == 0:
            sess["message"] = (sess.get("message") or "已停止") + "(无录像)"
            _save_session(sid, sess)
            return
        sess["finalizing"] = True
        sess["message"] = "正在整理录像(转码/抽尾帧)…"
        _save_session(sid, sess)
    out = d / "segments" / "seg_0001.mp4"
    frame = d / "segments" / "seg_0001.png"
    err = ""
    try:
        cmds = []
        if ext == "mp4":
            cmds.append(["ffmpeg", "-y", "-i", str(src), "-c", "copy", "-movflags", "+faststart", str(out)])
        cmds.append(["ffmpeg", "-y", "-i", str(src), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                     "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out)])
        ok = False
        for cmd in cmds:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
            if r.returncode == 0 and out.is_file() and out.stat().st_size > 0:
                ok = True
                break
            err = (r.stderr or r.stdout).strip()[-500:]
        if not ok:
            raise RuntimeError(f"ffmpeg 转码失败:{err}")
        r = subprocess.run(["ffmpeg", "-y", "-sseof", "-0.1", "-i", str(out), "-frames:v", "1", str(frame)],
                           capture_output=True, text=True, timeout=120)
        if r.returncode or not frame.is_file():
            raise RuntimeError(f"尾帧提取失败:{(r.stderr or r.stdout).strip()[-300:]}")
        dur = _probe_duration(out)
        src.unlink(missing_ok=True)
        with _LOCK:
            sess = _read_json(d / "session.json", {})
            sess["segments"] = [{"seq": 1, "file": f"segments/{out.name}", "frame": f"segments/{frame.name}",
                                 "duration_s": dur, "size": out.stat().st_size, "created_at": _now(),
                                 "gen_seconds": int((sess.get("director") or {}).get("gen_s") or 0),
                                 "prompt": str(sess.get("prompt") or "")}]
            sess["finalizing"] = False
            sess["message"] = f"录像已保存({dur:.1f}s),可回放或作为下次直播的首帧"
            _save_session(sid, sess)
    except Exception as exc:  # noqa: BLE001
        with _LOCK:
            sess = _read_json(d / "session.json", {})
            sess["finalizing"] = False
            sess["error"] = (str(sess.get("error") or "") + f"\n录像整理失败:{str(exc)[:500]}").strip()
            sess["message"] = "已停止(录像整理失败,原始录像仍在 segments/ 下)"
            _save_session(sid, sess)


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
    # 仅首帧端点(无 reference-to-video):Kling 系列、MiniMax H3 Max Turbo——参考图不生效,
    # 首轮用第 1 张参考图作首帧,之后每轮用上一段尾帧作首帧(link_mode 不起作用)
    first_only = family == "kling" or (family == "h3" and "turbo" in str(sess["model"]).lower())
    refs = _shrink_refs(d, list(sess.get("refs") or []))
    prev_frame = str(sess.get("start_frame") or "")
    if sess.get("segments"):
        prev_frame = _last_frame_of(sess) or prev_frame
    no_link = (sess.get("link_mode") or "refs_tail") == "none"
    if no_link:
        print("[live] 衔接模式=不衔接:每段仅按提示词文生视频,参考图与尾帧都不提交", flush=True)
    elif first_only and refs:
        print(f"[live] {sess['model']} 端点不支持参考图:首轮用第 1 张参考图作首帧,其余参考图不生效",
              flush=True)
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
        if link_mode == "none":
            pass                                   # 不衔接:纯文生视频,不带参考图与尾帧,也不加续接句
        elif first_only:
            first = prev_frame or (refs[0] if refs else "")
        elif link_mode == "first_frame" and prev_frame:
            first = prev_frame
        else:
            round_refs = list(refs) + ([prev_frame] if prev_frame else [])
        if prev_frame and link_mode != "none":
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
