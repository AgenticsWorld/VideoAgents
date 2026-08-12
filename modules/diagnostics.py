"""诊断数据(Diagnostics):本地结构化事件与经验卡的采集、汇总与手动导出。

定位(Phase 0/2):只落盘、只导出,**本模块永远不出网**——导出的 zip 由用户自行
提交(如贴到 GitHub issue)。隐私模型是「白名单字段」:事件只允许 ALLOWED_FIELDS
里的枚举字段入盘,提示词/小说内容/文件路径/API Key 在任何入口都进不来;经验卡
(lesson.md,约定见 WORKFLOW.md §6.1)导出前由用户在设置页逐张预览勾选。

三类数据:
- 事件:runtime(run 收敛处)与 genmedia(CLI 统一出口)调用 record_event(),
  追加写 data/.videoagents/telemetry/outbox/events-YYYYMM.jsonl;错误消息经
  error_fingerprint() 模板化(路径/URL/数字/引号内容置占位符)后只存模板与签名。
- 经验卡:data/projects/<slug>/runs/<task_id>/lesson.md,导出时扫描解析。
- 汇总:缺陷单(qa/defects/*.json)只聚合可枚举字段(severity/root_cause/status),
  genconfig 只取各段 provider+model 指纹,正文与凭证不落入导出包。

开关:state.json 的 diagnostics_enabled(设置→高级→诊断数据),默认开
(仅本地采集,无上传);关闭即停写,已落盘文件保留、可在设置页清空。
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import time
import uuid
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("VIDEOAGENTS_DATA_DIR", ROOT / "data")).expanduser().resolve()
RUNTIME_DIR = Path(os.environ.get(
    "VIDEOAGENTS_RUNTIME_DIR", DATA_DIR / ".videoagents"
)).expanduser().resolve()
TELEMETRY_DIR = RUNTIME_DIR / "telemetry"
OUTBOX_DIR = TELEMETRY_DIR / "outbox"
EXPORT_DIR = TELEMETRY_DIR / "export"
STATE_PATH = RUNTIME_DIR / "state.json"
GENCONFIG_PATH = Path(os.environ.get(
    "VIDEOAGENTS_CONFIG_PATH", RUNTIME_DIR / "genconfig.json"
)).expanduser().resolve()

SCHEMA_VERSION = 1
# 事件字段白名单:record_event 只放行这些键,新增字段必须先在此登记并确认不含
# 用户内容。值一律标量;error_template 是唯一的自由文本,入盘前已模板化+截断。
ALLOWED_FIELDS = frozenset({
    "kind", "engine", "agent", "provider", "model", "status", "exit",
    "error_signature", "error_template", "duration_s", "retry_count",
    "session_reset", "project_hash", "run_id",
})
MAX_FILE_BYTES = 2 * 1024 * 1024     # 单月事件文件上限,超限静默停写(诊断绝不拖垮宿主)
MAX_OUTBOX_FILES = 4                 # 最多保留 4 个月度文件,滚动删最旧
MAX_TEMPLATE_CHARS = 200

_ENABLED_CACHE: tuple[float, bool] = (0.0, True)


def enabled() -> bool:
    """诊断采集开关(state.json diagnostics_enabled,默认开;3s TTL 缓存)。"""
    global _ENABLED_CACHE
    now = time.time()
    if now - _ENABLED_CACHE[0] < 3.0:
        return _ENABLED_CACHE[1]
    try:
        val = bool(json.loads(STATE_PATH.read_text()).get("diagnostics_enabled", True))
    except Exception:
        val = True
    _ENABLED_CACHE = (now, val)
    return val


def _app_version() -> str:
    try:
        from services.api import __version__
        return __version__
    except Exception:
        return ""


def install_id() -> str:
    """匿名安装 ID(随机 UUID,只进导出包 manifest,用于同一用户多次导出的关联;
    删除 telemetry/install_id 文件即重置)。"""
    p = TELEMETRY_DIR / "install_id"
    try:
        val = p.read_text().strip()
        if val:
            return val
    except OSError:
        pass
    val = uuid.uuid4().hex
    try:
        TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)
        p.write_text(val)
    except OSError:
        pass
    return val


def project_hash(project: str) -> str:
    """项目名脱敏:稳定短哈希,可跨事件聚类但不泄露项目名。"""
    if not project:
        return ""
    return hashlib.sha256(project.encode("utf-8")).hexdigest()[:12]


# ---------------- 错误模板化 ----------------

_RE_URL = re.compile(r"https?://\S+")
_RE_PATH = re.compile(r"(?:[A-Za-z]:)?[\\/](?:[\w.\-@一-鿿]+[\\/])+[\w.\-@一-鿿]*")
_RE_HEX = re.compile(r"\b[0-9a-fA-F]{8,}\b")
_RE_QUOTED = re.compile(r"[「『\"'“‘]([^」』\"'”’]{1,80})[」』\"'”’]")
_RE_NUM = re.compile(r"\d+(?:\.\d+)?")


def error_fingerprint(message: str) -> tuple[str, str]:
    """错误消息 → (模板, 签名)。剥离 URL/路径/引号内容/长十六进制/数字等变量部分,
    使同类错误在不同机器/项目上聚成同一签名;模板本身即已脱敏,可直接导出。"""
    msg = str(message or "").strip().splitlines()[0] if message else ""
    msg = _RE_URL.sub("{URL}", msg)
    msg = _RE_PATH.sub("{PATH}", msg)
    msg = _RE_QUOTED.sub("{Q}", msg)
    msg = _RE_HEX.sub("{HEX}", msg)
    msg = _RE_NUM.sub("{N}", msg)
    msg = msg[:MAX_TEMPLATE_CHARS]
    sig = hashlib.sha256(msg.encode("utf-8")).hexdigest()[:16]
    return msg, sig


# ---------------- 事件落盘 ----------------

def record_event(event: str, **fields) -> None:
    """追加一条结构化事件到 outbox。白名单外字段丢弃;任何异常静默吞掉——
    诊断是旁路,绝不允许影响生成/派单主链路。"""
    try:
        if not enabled():
            return
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "event": str(event)[:40],
               "app_version": _app_version(),
               "platform": platform.system().lower()}
        for k, v in fields.items():
            if k not in ALLOWED_FIELDS or v is None or v == "":
                continue
            if isinstance(v, float):
                v = round(v, 1)
            elif not isinstance(v, (str, int, bool)):
                continue
            if isinstance(v, str):
                v = v[:MAX_TEMPLATE_CHARS]
            row[k] = v
        OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
        path = OUTBOX_DIR / f"events-{time.strftime('%Y%m')}.jsonl"
        if path.exists() and path.stat().st_size > MAX_FILE_BYTES:
            return
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        _rotate_outbox()
    except Exception:
        pass


def _rotate_outbox() -> None:
    files = sorted(OUTBOX_DIR.glob("events-*.jsonl"))
    for old in files[:-MAX_OUTBOX_FILES]:
        old.unlink(missing_ok=True)


def record_run_event(run: dict) -> None:
    """runtime run 生命周期收敛处调用(core.execute_run finally 块)。
    只取运行元数据;错误消息模板化后入盘,项目名只存哈希。"""
    try:
        fields = {
            "engine": run.get("engine", "claude"),
            "agent": run.get("agent", ""),
            "status": run.get("status", ""),
            "run_id": run.get("id", ""),
            "project_hash": project_hash(run.get("project", "")),
            "session_reset": bool(run.get("session_reset")) or None,
        }
        started, ended = run.get("started"), run.get("ended")
        if started and ended:
            fields["duration_s"] = max(0.0, float(ended) - float(started))
        if run.get("status") == "error" and run.get("error"):
            tpl, sig = error_fingerprint(run["error"])
            fields.update(error_template=tpl, error_signature=sig)
        record_event("run_finish", **fields)
    except Exception:
        pass


def record_gen_event(kind: str, ok: bool, error: str = "",
                     provider: str = "", model: str = "",
                     duration_s: float | None = None) -> None:
    """genmedia CLI 统一出口调用(main 的 try/except)。"""
    try:
        fields = {"kind": kind, "provider": provider, "model": model,
                  "status": "ok" if ok else "error",
                  "agent": os.environ.get("VIDEOAGENTS_AGENT", ""),
                  "project_hash": project_hash(os.environ.get("VIDEOAGENTS_PROJECT", "")),
                  "duration_s": duration_s}
        if not ok and error:
            tpl, sig = error_fingerprint(error)
            fields.update(error_template=tpl, error_signature=sig)
        record_event("gen_finish", **fields)
    except Exception:
        pass


# ---------------- 经验卡(lesson.md) ----------------

# frontmatter 允许的元字段(约定见 WORKFLOW.md §6.1;服务端零 yaml 依赖,
# 只解析扁平 `key: value` 行)
LESSON_META_FIELDS = ("title", "category", "severity", "provider", "model",
                      "agents", "date", "evidence")


def _parse_lesson(path: Path) -> dict | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    meta, body = {}, text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            body = parts[2].strip()
            for line in parts[1].splitlines():
                if ":" not in line:
                    continue
                k, v = line.split(":", 1)
                k = k.strip()
                if k in LESSON_META_FIELDS:
                    meta[k] = v.strip()
    return {"path": str(path), "meta": meta, "body": body,
            "title": meta.get("title") or path.parent.name,
            "task_id": path.parent.name,
            "project": path.parent.parent.parent.name}


def scan_lessons() -> list[dict]:
    """扫描全部项目的 runs/<task_id>/lesson.md,按日期倒序。只做枚举,
    是否导出由用户在设置页逐张勾选决定。"""
    out = []
    projects = DATA_DIR / "projects"
    if not projects.is_dir():
        return out
    for p in sorted(projects.glob("*/runs/*/lesson.md")):
        card = _parse_lesson(p)
        if card:
            out.append(card)
    out.sort(key=lambda c: c["meta"].get("date", ""), reverse=True)
    return out


# ---------------- 汇总与导出 ----------------

def _iter_events():
    for f in sorted(OUTBOX_DIR.glob("events-*.jsonl")):
        try:
            with f.open(encoding="utf-8") as fh:
                for line in fh:
                    try:
                        yield json.loads(line)
                    except Exception:
                        continue
        except OSError:
            continue


def _defect_stats() -> dict:
    """缺陷单聚合:只统计可枚举字段,正文(含项目内容)绝不读入导出链路。"""
    stats = {"total": 0, "by_severity": {}, "by_root_cause": {}, "by_status": {}}
    projects = DATA_DIR / "projects"
    if not projects.is_dir():
        return stats
    for p in projects.glob("*/qa/defects/*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        stats["total"] += 1
        for key, field in (("by_severity", d.get("severity")),
                           ("by_status", d.get("status")),
                           ("by_root_cause", (d.get("analysis") or {}).get("root_cause"))):
            if isinstance(field, str) and field:
                stats[key][field[:40]] = stats[key].get(field[:40], 0) + 1
    return stats


def _genconfig_fingerprint() -> dict:
    """各能力段生效渠道+模型指纹;白名单取值,Key/URL/账号等一概不取。"""
    out = {}
    try:
        cfg = json.loads(GENCONFIG_PATH.read_text())
    except Exception:
        return out
    for kind, section in cfg.items():
        if not isinstance(section, dict):
            continue
        provider = section.get("provider")
        if not isinstance(provider, str):
            continue
        pc = section.get(provider) if isinstance(section.get(provider), dict) else {}
        model = pc.get("custom_model") or pc.get("model") or ""
        out[kind] = {"provider": provider, "model": str(model)[:80]}
    return out


def summary() -> dict:
    """设置页「诊断数据」区块的统计:开关、事件量、错误 Top 签名、经验卡数。"""
    counts, errors, latest = {}, {}, ""
    total = 0
    for row in _iter_events():
        total += 1
        ev = row.get("event", "?")
        counts[ev] = counts.get(ev, 0) + 1
        latest = max(latest, row.get("ts", ""))
        if row.get("status") == "error" and row.get("error_signature"):
            key = row["error_signature"]
            e = errors.setdefault(key, {"signature": key, "count": 0,
                                        "template": row.get("error_template", "")})
            e["count"] += 1
    top_errors = sorted(errors.values(), key=lambda e: -e["count"])[:10]
    outbox_bytes = sum(f.stat().st_size for f in OUTBOX_DIR.glob("events-*.jsonl")) \
        if OUTBOX_DIR.is_dir() else 0
    return {"enabled": enabled(), "total_events": total, "by_event": counts,
            "top_errors": top_errors, "latest_ts": latest,
            "outbox_bytes": outbox_bytes, "lessons": len(scan_lessons()),
            "defects": _defect_stats()}


def clear_outbox() -> int:
    """清空已采集事件(设置页操作);返回删除文件数。"""
    n = 0
    if OUTBOX_DIR.is_dir():
        for f in OUTBOX_DIR.glob("events-*.jsonl"):
            f.unlink(missing_ok=True)
            n += 1
    return n


def build_export(lesson_paths: list[str] | None = None,
                 include_events: bool = True) -> str:
    """打包诊断导出 zip,返回绝对路径。内容:manifest(版本/平台/匿名安装 ID)、
    事件 jsonl、聚合摘要、用户勾选的经验卡。lesson_paths 必须是 scan_lessons()
    返回的路径(防任意文件打包);未勾选即不含任何经验卡。"""
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    allowed = {c["path"] for c in scan_lessons()}
    picked = [p for p in (lesson_paths or []) if p in allowed]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    zpath = EXPORT_DIR / f"diagnostics-{stamp}.zip"
    manifest = {"schema_version": SCHEMA_VERSION,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "app_version": _app_version(),
                "platform": {"system": platform.system().lower(),
                             "release": platform.release(),
                             "python": platform.python_version()},
                "install_id": install_id(),
                "include_events": bool(include_events),
                "lessons": len(picked)}
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
        s = summary()
        s["genconfig"] = _genconfig_fingerprint()
        z.writestr("summary.json", json.dumps(s, ensure_ascii=False, indent=1))
        if include_events and OUTBOX_DIR.is_dir():
            for f in sorted(OUTBOX_DIR.glob("events-*.jsonl")):
                z.write(f, f"events/{f.name}")
        for i, p in enumerate(picked, 1):
            card = _parse_lesson(Path(p))
            if not card:
                continue
            content = card["body"]
            if card["meta"]:
                fm = "\n".join(f"{k}: {v}" for k, v in card["meta"].items())
                content = f"---\n{fm}\n---\n\n{card['body']}"
            # 文件名不带项目名;正文由用户预览勾选确认后原样导出
            z.writestr(f"lessons/{i:03d}-{card['task_id']}.md", content)
    return str(zpath)
