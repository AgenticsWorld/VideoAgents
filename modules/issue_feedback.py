"""Issue 反馈:Agent 运行中发现「宿主代码缺陷 / 需要宿主新增功能」时,整理成 issue
自动发布到 GitHub(https://github.com/AgenticsWorld/VideoAgents/issues)。

链路:Agent 调 `code/report_issue.py`(用法由运行提示词在开关开启时注入)→ file_issue()
脱敏 + 签名去重后落盘 data/.videoagents/issues/<id>.json(state=pending)。默认由用户在
设置页点「在浏览器中提交」(manual_url 预填 issues/new,GitHub 不支持匿名建 issue)→
mark_submitted();显式设了环境变量 VIDEOAGENTS_GITHUB_TOKEN 时宿主直接经 API 发布
(publish / run 收敛处 publish_pending 补发)。旁路故障一律静默,绝不影响主链路。

开关:state.json `issue_feedback`(设置→高级→诊断数据「Issue 反馈」,默认开)。

隐私:issue 公开可见范围 = 仓库可见范围。正文由 Agent 按「只写机制、不写项目内容」
的规约撰写,宿主侧 scrub() 再兜一层:家目录/仓库根/项目目录置占位符,疑似密钥打码。
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("VIDEOAGENTS_DATA_DIR", ROOT / "data")).expanduser().resolve()
RUNTIME_DIR = Path(os.environ.get(
    "VIDEOAGENTS_RUNTIME_DIR", DATA_DIR / ".videoagents"
)).expanduser().resolve()
ISSUES_DIR = RUNTIME_DIR / "issues"
STATE_PATH = RUNTIME_DIR / "state.json"

REPO = "AgenticsWorld/VideoAgents"
ISSUES_URL = f"https://github.com/{REPO}/issues"
API = "https://api.github.com"
KINDS = {"bug": "bug", "feature": "enhancement"}   # kind → GitHub 标签
BASE_LABEL = "agent-report"
MAX_TITLE_CHARS = 120
MAX_BODY_CHARS = 8000
MAX_RECORDS = 200            # 本地台账上限,滚动删最旧的已发布/重复记录
HTTP_TIMEOUT = 20
MAX_ATTEMPTS = 5             # 单条自动补发次数上限(权限不足等永久性失败不无限重试)


def _state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def enabled() -> bool:
    return bool(_state().get("issue_feedback", True))


def _app_version() -> str:
    try:
        from services.api import __version__
        return __version__
    except Exception:
        return ""


# ---------------- 凭据 ----------------

def _token() -> tuple[str, str]:
    """(token, 来源)。默认无凭据=走「在浏览器中提交」;只有显式设置环境变量
    VIDEOAGENTS_GITHUB_TOKEN(classic PAT,public_repo)才由宿主直接发布——不读通用的
    GITHUB_TOKEN / gh 登录,免得在用户不知情时以其账号发帖。"""
    v = os.environ.get("VIDEOAGENTS_GITHUB_TOKEN", "").strip()
    return (v, "env") if v else ("", "")


def auth_source() -> str:
    return _token()[1]


# ---------------- 脱敏 ----------------

_RE_SECRET = re.compile(
    r"\b(?:sk|pk|rk|ghp|gho|ghs|github_pat|xox[abp]|AKIA|AIza)[-_A-Za-z0-9]{12,}\b"
    r"|\bBearer\s+[-._~+/A-Za-z0-9]{16,}=*"
    r"|(?i:(?:api[-_ ]?key|token|secret|password)\s*[=:]\s*)[\"']?[-._~+/A-Za-z0-9]{12,}")
_RE_PROJECT = re.compile(r"(data[\\/]projects[\\/])[^\\/\s\"'`)]+")


def scrub(text: str) -> str:
    """路径占位 + 密钥打码。项目 slug 常是作品名,一并置 <project>。"""
    s = str(text or "")
    for real, tag in ((str(DATA_DIR), "<data>"), (str(ROOT), "<repo>"),
                      (str(Path.home()), "~")):
        if real and real != "/":
            s = s.replace(real, tag)
    s = _RE_PROJECT.sub(r"\1<project>", s)
    s = re.sub(r"(<data>[\\/]projects[\\/])[^\\/\s\"'`)]+", r"\1<project>", s)
    return _RE_SECRET.sub("<redacted>", s)


def signature(kind: str, title: str) -> str:
    """去重签名:类型 + 归一化标题(去空白/标点/大小写;数字保留,行号之类差异算新 issue)。"""
    norm = re.sub(r"[\W_]+", "", title.lower())
    return hashlib.sha1(f"{kind}:{norm}".encode()).hexdigest()[:16]


# ---------------- 本地台账 ----------------

def _load(path: Path) -> dict | None:
    try:
        d = json.loads(path.read_text())
        return d if isinstance(d, dict) and d.get("id") else None
    except Exception:
        return None


def _save(rec: dict) -> None:
    ISSUES_DIR.mkdir(parents=True, exist_ok=True)
    p = ISSUES_DIR / f"{rec['id']}.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=2))
    tmp.replace(p)


def list_issues(limit: int = 50) -> list[dict]:
    """新→旧。"""
    recs = [r for r in (_load(p) for p in ISSUES_DIR.glob("*.json")) if r] \
        if ISSUES_DIR.is_dir() else []
    recs.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return recs[:limit]


def _rotate() -> None:
    recs = list_issues(limit=10 ** 6)
    for r in recs[MAX_RECORDS:]:
        if r.get("state") in ("published", "duplicate", "submitted"):
            try:
                (ISSUES_DIR / f"{r['id']}.json").unlink()
            except OSError:
                pass


def file_issue(kind: str, title: str, body: str, *, component: str = "",
               agent: str = "", engine: str = "") -> dict:
    """登记一条 issue(不发布)。同签名已登记过 → 返回旧记录并带 duplicate_of_local=True。"""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {sorted(KINDS)}")
    title = scrub(" ".join(str(title or "").split()))[:MAX_TITLE_CHARS]
    body = scrub(str(body or "").strip())[:MAX_BODY_CHARS]
    if not title or not body:
        raise ValueError("title and body are required")
    sig = signature(kind, title)
    for r in list_issues(limit=10 ** 6):
        if r.get("signature") == sig:
            return {**r, "duplicate_of_local": True}
    rec = {
        "id": time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6],
        "kind": kind, "title": title, "body": body,
        "component": scrub(component)[:200], "agent": agent[:80], "engine": engine[:40],
        "signature": sig, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "app_version": _app_version(),
        "platform": f"{platform.system()} {platform.release()} {platform.machine()}",
        "state": "pending", "url": "", "error": "",
    }
    _save(rec)
    _rotate()
    return rec


# ---------------- GitHub ----------------

def _gh_api(method: str, path: str, token: str, payload: dict | None = None) -> dict:
    req = urllib.request.Request(
        API + path, method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28",
                 "User-Agent": "VideoAgents-issue-feedback",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("message", "")
        except Exception:
            msg = ""
        raise RuntimeError(f"GitHub HTTP {e.code} {msg}".strip()) from None
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise RuntimeError(f"GitHub 不可达: {getattr(e, 'reason', e)}") from None


def _render_body(rec: dict) -> str:
    meta = [("类型", "宿主缺陷" if rec["kind"] == "bug" else "功能需求"),
            ("涉及代码", rec.get("component")), ("上报 Agent", rec.get("agent")),
            ("引擎", rec.get("engine")), ("版本", rec.get("app_version")),
            ("平台", rec.get("platform"))]
    lines = [rec["body"], "", "---",
             "_由 VideoAgents「Issue 反馈」自动提交(Agent 运行中整理,已脱敏)_", ""]
    lines += [f"- {k}:{v}" for k, v in meta if v]
    lines.append(f"\n<!-- va-sig:{rec['signature']} -->")
    return "\n".join(lines)


def manual_url(rec: dict, max_len: int = 7000) -> str:
    """无凭据时的兜底:预填标题/正文的 issues/new 链接,用户在浏览器里点提交。
    URL 过长 GitHub 会拒,正文按编码后长度截断(签名注释保留,去重照常)。"""
    base = f"{ISSUES_URL}/new?title={urllib.parse.quote(rec['title'])}&body="
    full = _render_body(rec)
    tail = f"\n\n<!-- va-sig:{rec['signature']} -->"
    head = full[:-len(tail)] if full.endswith(tail) else full
    while head and len(base) + len(urllib.parse.quote(head + "\n…" + tail)) > max_len:
        head = head[:int(len(head) * 0.8)]
    body = full if len(base) + len(urllib.parse.quote(full)) <= max_len else head + "\n…" + tail
    return base + urllib.parse.quote(body)


def mark_submitted(issue_id: str) -> dict | None:
    """用户点了「在浏览器中提交」:记为 submitted(是否真的提交无从得知,链接仍保留可再点)。"""
    if not re.fullmatch(r"[\w-]{1,64}", issue_id or ""):
        return None
    rec = _load(ISSUES_DIR / f"{issue_id}.json")
    if rec and rec.get("state") in ("pending", "failed"):
        rec.update(state="submitted", error="", published_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        _save(rec)
    return rec


def pending_count() -> int:
    return sum(1 for r in list_issues(limit=10 ** 6) if r.get("state") in ("pending", "failed"))


def publish(rec: dict) -> dict:
    """发布一条 pending/failed 记录;结果写回台账并返回。远端已有同签名 issue → duplicate。"""
    if rec.get("state") in ("published", "duplicate", "submitted"):
        return rec
    token, _src = _token()
    rec["attempts"] = int(rec.get("attempts") or 0) + (1 if token else 0)
    if not token:
        rec.update(state="pending", error="")
        _save(rec)
        return rec
    try:
        q = urllib.parse.quote(f'repo:{REPO} is:issue in:body "va-sig:{rec["signature"]}"')
        hits = _gh_api("GET", f"/search/issues?q={q}&per_page=1", token).get("items") or []
        if hits:
            rec.update(state="duplicate", url=hits[0].get("html_url", ""), error="")
        else:
            r = _gh_api("POST", f"/repos/{REPO}/issues", token, {
                "title": rec["title"], "body": _render_body(rec),
                "labels": [BASE_LABEL, KINDS[rec["kind"]]]})
            rec.update(state="published", url=r.get("html_url", ""),
                       number=r.get("number"), error="")
    except RuntimeError as e:
        rec.update(state="failed", error=str(e)[:300])
    rec["published_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    _save(rec)
    return rec


def publish_pending(force: bool = False) -> list[dict]:
    """补发全部 pending/failed(宿主 run 收敛处自动调;设置页「重新发布」force=True 不受
    MAX_ATTEMPTS 限制)。开关关闭时不发。"""
    if not enabled():
        return []
    out = []
    for r in reversed(list_issues(limit=10 ** 6)):
        if r.get("state") in ("pending", "failed") \
                and (force or int(r.get("attempts") or 0) < MAX_ATTEMPTS):
            out.append(publish(r))
    return out
