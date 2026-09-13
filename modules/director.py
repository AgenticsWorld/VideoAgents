"""导演台(/preview/director,2026-09-13):白模现场的修改注释台账 + 版本快照 + 批量派单。

文件(全部宿主写,Agent 只读):
  directing/<ep>/whitebox/director/notes.json               台账(schema director_notes.v1)
  directing/<ep>/whitebox/director/versions/<grp>/v<N>.json  版本快照 = 该组编译后的 group JSON + 所用场景模型
                                                             (schema director_version.v1)

设计(daoyantai-research.md D1–D4,用户 2026-09-13 拍板):
- 注释(note)= 用户在 3D 现场对某对象(人物/群演/道具/摄影机/场景/整组)在某时刻写的一句修改要求;
  状态机 draft → submitted → applied | failed。draft 可改可删,其余只读。
- 提交(submit)= 把若干组的 draft 注释 + 该组已裁决未套用的待决项打成一个批次(batch),一次派单给
  白模调度 Agent(07-directing/whitebox-staging),Agent 改 whitebox_plans/<grp>.json、回写源文件、
  --compile-only 重编译;宿主在运行结束后回收(reconcile):组指纹变了 → 落一份新版本快照,注释置 applied;
  没变 → 注释置 failed(运行结束但白模未变)。
- 版本 = 编译组快照,不出 mp4;A|B 由前端用两份快照实时渲染。首次提交前自动落 v1 基线,便于比对。
- 拖动改数值(覆盖层)属 v2,本文件不涉及。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

SCHEMA = "director_notes.v1"
VERSION_SCHEMA = "director_version.v1"
DIR_REL = "directing/{ep}/whitebox/director"
LEDGER_REL = DIR_REL + "/notes.json"
AGENT_ID = "07-directing/whitebox-staging"

TARGET_KINDS = ("actor", "extra", "prop", "camera", "scene", "group")
TARGET_LABEL = {"actor": "人物", "extra": "群演", "prop": "道具", "camera": "摄影机", "scene": "场景", "group": "整组"}
NOTE_STATUS = ("draft", "submitted", "applied", "failed")
NOTE_STATUS_LABEL = {"draft": "待提交", "submitted": "已提交", "applied": "已套用", "failed": "未生效"}
BATCH_STATUS = ("running", "done", "nochange", "failed")
BATCH_STATUS_LABEL = {"running": "运行中", "done": "已出新版", "nochange": "白模未变", "failed": "失败"}
MAX_TEXT = 2000


# ---------------------------------------------------------------- 文件读写
def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def read_json(p: Path, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(p: Path, obj) -> None:
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def ledger_path(base: Path, ep: str) -> Path:
    return base / LEDGER_REL.format(ep=ep)


def versions_dir(base: Path, ep: str, gid: str) -> Path:
    return base / DIR_REL.format(ep=ep) / "versions" / gid


def empty_ledger(ep: str) -> dict:
    return {"schema": SCHEMA, "ep": ep, "updated_at": _now(), "notes": [], "batches": [], "versions": {}, "current": {}}


def load_ledger(base: Path, ep: str) -> dict:
    doc = read_json(ledger_path(base, ep))
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        return empty_ledger(ep)
    for k, d in (("notes", []), ("batches", []), ("versions", {}), ("current", {})):
        if not isinstance(doc.get(k), type(d)):
            doc[k] = d
    return doc


def save_ledger(base: Path, ep: str, doc: dict) -> None:
    doc["schema"] = SCHEMA
    doc["ep"] = ep
    doc["updated_at"] = _now()
    write_json(ledger_path(base, ep), doc)


# ---------------------------------------------------------------- 注释
_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def _next_id(items: list, prefix: str) -> str:
    n = 0
    for it in items:
        m = re.match(rf"^{prefix}(\d+)$", str(it.get("id") or ""))
        if m:
            n = max(n, int(m.group(1)))
    return f"{prefix}{n + 1:04d}"


def normalize_target(target) -> dict:
    if not isinstance(target, dict):
        raise ValueError("target 须为对象 {kind, id, label}")
    kind = str(target.get("kind") or "").strip()
    if kind not in TARGET_KINDS:
        raise ValueError(f"target.kind 须为 {'|'.join(TARGET_KINDS)}")
    tid = str(target.get("id") or "").strip()
    if kind not in ("group", "scene") and not _ID_RE.match(tid):
        raise ValueError("target.id 非法")
    return {"kind": kind, "id": tid, "label": str(target.get("label") or tid or TARGET_LABEL[kind])[:80]}


def make_note(group_id: str, target, text: str, *, t=None, shot_id=None, base_v=None, view=None) -> dict:
    if not _ID_RE.match(str(group_id or "")):
        raise ValueError("group_id 非法")
    text = str(text or "").strip()
    if not text:
        raise ValueError("注释内容不能为空")
    if len(text) > MAX_TEXT:
        raise ValueError(f"注释过长(>{MAX_TEXT} 字)")
    note = {"id": "", "group_id": group_id, "target": normalize_target(target), "text": text,
            "t": None if t is None or t == "" else round(float(t), 2),
            "shot_id": (str(shot_id).strip() or None) if shot_id else None,
            "view": str(view)[:16] if view else None,
            "status": "draft", "batch_id": None, "version_after": None, "error": "",
            "base_v": int(base_v) if base_v not in (None, "") else None,
            "created_at": _now(), "updated_at": _now()}
    if note["t"] is not None and note["t"] < 0:
        raise ValueError("t 不能为负")
    return note


def find_note(doc: dict, nid: str) -> dict | None:
    return next((n for n in doc.get("notes", []) if n.get("id") == nid), None)


def add_note(doc: dict, note: dict) -> dict:
    note["id"] = _next_id(doc["notes"], "N-")
    doc["notes"].append(note)
    return note


def update_note(note: dict, patch: dict) -> dict:
    if note.get("status") != "draft":
        raise ValueError("只有待提交的注释可以修改")
    if "text" in patch:
        text = str(patch.get("text") or "").strip()
        if not text:
            raise ValueError("注释内容不能为空")
        note["text"] = text[:MAX_TEXT]
    if "t" in patch:
        note["t"] = None if patch["t"] in (None, "") else round(float(patch["t"]), 2)
    if "shot_id" in patch:
        note["shot_id"] = (str(patch["shot_id"]).strip() or None) if patch["shot_id"] else None
    if "target" in patch and patch["target"] is not None:
        note["target"] = normalize_target(patch["target"])
    note["updated_at"] = _now()
    return note


def delete_note(doc: dict, nid: str) -> dict:
    note = find_note(doc, nid)
    if not note:
        raise KeyError(nid)
    if note.get("status") != "draft":
        raise ValueError("只有待提交的注释可以删除")
    doc["notes"] = [n for n in doc["notes"] if n.get("id") != nid]
    return note


# ---------------------------------------------------------------- 版本快照
def group_sha(episode: dict, group: dict) -> str:
    """与 modules.whitebox_export.fingerprint 同口径:场景模型 + 组(去 issues)。"""
    scene = episode["scenes"][group["scene_id"]]
    slim = {k: v for k, v in group.items() if k not in ("issues", "artifact_status")}
    return hashlib.sha256(json.dumps([scene, slim], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def versions_of(doc: dict, gid: str) -> list:
    return sorted((v for v in doc.get("versions", {}).get(gid, []) if isinstance(v, dict)), key=lambda v: v.get("v", 0))


def current_version(doc: dict, gid: str) -> int:
    vs = versions_of(doc, gid)
    return int(doc.get("current", {}).get(gid) or (vs[-1]["v"] if vs else 0))


def latest_sha(doc: dict, gid: str) -> str | None:
    vs = versions_of(doc, gid)
    return vs[-1].get("sha") if vs else None


def snapshot(base: Path, ep: str, doc: dict, episode: dict, gid: str, *, batch_id=None, note_ids=(), label="") -> dict:
    """把该组当前编译结果落成新版本;返回版本记录(已写进 doc,未存盘)。"""
    group = next((g for g in episode.get("groups", []) if g.get("group_id") == gid), None)
    if not group:
        err = next((e.get("error") for e in episode.get("errors", []) if e.get("group_id") == gid), "本组没有白模编译结果")
        raise ValueError(f"{gid}:{err}")
    vs = versions_of(doc, gid)
    v = (vs[-1]["v"] + 1) if vs else 1
    rel = f"{DIR_REL.format(ep=ep)}/versions/{gid}/v{v}.json"
    sha = group_sha(episode, group)
    payload = {"schema": VERSION_SCHEMA, "project": episode.get("project"), "ep": ep, "group_id": gid, "v": v,
               "created_at": _now(), "batch_id": batch_id, "label": label, "sha": sha,
               "render": episode.get("render"), "actor_colors": episode.get("actor_colors") or {},
               "scene": episode["scenes"][group["scene_id"]], "group": group}
    write_json(base / rel, payload)
    rec = {"v": v, "file": rel, "sha": sha, "created_at": payload["created_at"], "batch_id": batch_id,
           "label": label, "note_ids": list(note_ids), "shots": [c.get("shot_id") for c in group.get("cameras", [])],
           "duration_s": group.get("duration_s")}
    doc.setdefault("versions", {}).setdefault(gid, []).append(rec)
    doc.setdefault("current", {})[gid] = v
    return rec


def load_version(base: Path, ep: str, gid: str, v: int) -> dict:
    p = versions_dir(base, ep, gid) / f"v{int(v)}.json"
    data = read_json(p)
    if not isinstance(data, dict) or data.get("schema") != VERSION_SCHEMA:
        raise FileNotFoundError(f"{gid} v{v} 快照不存在")
    return data


# ---------------------------------------------------------------- 批次
def _issue_lines(issue: dict) -> str:
    d = issue.get("decision") or {}
    choice = d.get("choice")
    opt = next((o for o in issue.get("options", []) if o.get("id") == choice), None)
    what = (f"选「{choice}. {opt.get('label')}」" if opt else
            "接受默认取舍:" + str(issue.get("provisional") or "") if choice == "provisional" else
            f"自定义:{d.get('note') or ''}" if choice == "custom" else f"选「{choice}」")
    if d.get("note") and choice != "custom":
        what += f"(说明:{d['note']})"
    return f"  - 待决项 {issue.get('issue_id')}:{issue.get('question')} → {what}"


def prepare_batch(base: Path, ep: str, doc: dict, episode: dict, *, group_ids=None, note_ids=None) -> dict:
    """挑出要提交的 draft 注释与已裁决未套用的待决项,生成派单指令。不改 doc、不派单。"""
    notes = [n for n in doc.get("notes", []) if n.get("status") == "draft"]
    if note_ids:
        wanted = set(note_ids)
        notes = [n for n in notes if n.get("id") in wanted]
    if group_ids:
        gs = set(group_ids)
        notes = [n for n in notes if n.get("group_id") in gs]
    groups_in = sorted({n["group_id"] for n in notes} | (set(group_ids or []) if not note_ids else set()))
    compiled = {g["group_id"]: g for g in episode.get("groups", [])}
    issues = {}
    for gid in groups_in:
        g = compiled.get(gid)
        if not g:
            continue
        rows = [i for i in g.get("issues", []) if i.get("status") == "decided"]
        if rows:
            issues[gid] = rows
    groups = [gid for gid in groups_in if any(n["group_id"] == gid for n in notes) or gid in issues]
    if not groups:
        raise ValueError("没有可提交的注释或已裁决的待决项")
    missing = [gid for gid in groups if gid not in compiled]
    if missing:
        raise ValueError("这些组没有白模编译结果,无法提交:" + ", ".join(missing))
    return {"group_ids": groups, "notes": [n for n in notes if n["group_id"] in groups], "issues": issues,
            "message": build_message(base, ep, groups, [n for n in notes if n["group_id"] in groups], issues, compiled)}


def build_message(base: Path, ep: str, groups: list, notes: list, issues: dict, compiled: dict) -> str:
    """派单指令(中文,同后期页 _post_agent_message / 白模面板 applyDecisions 口径)。"""
    proj = base.name
    lines = [f"导演台修改批次(项目 {proj} · {ep} · 组 {', '.join(groups)}):用户在 3D 白模现场逐对象写的修改要求,",
             "请按 docs/whitebox.md「导演台」节逐条套用到白模调度计划并重编译。规则:",
             "- 只改 directing/{ep}/whitebox_plans/<组>.json 里对应对象的关键帧/机位/可见名单,并同步回写权威源文件"
             "(shots/<镜>/blocking.json、camera.json(含 whitebox_contract)、shot_list.json 的 blocking_map),不得只改计划不改源;".format(ep=ep),
             "- 注释里的数值(位置米制、朝向弧度、组内秒)照抄不改;文字要求按规约换算成关键帧,时刻 t 是组内秒,shot 是所在镜;",
             "- 改完跑碰撞与朝向自检(docs/whitebox.md「防穿模检查」「对话身体朝向」),不得靠换机位、隐藏人、缩小人掩盖;",
             f"- 最后执行 python code/render_whitebox.py --project {proj} --ep {ep} --compile-only {' '.join(groups)} 重编译落盘;",
             "- 回执逐条列出「注释 id → 改了什么/改了哪些文件」,做不到的注明原因;本批次的注释 id 请原样保留在回执里。",
             "- 不要改 directing/{ep}/whitebox/director/ 下任何文件(那是宿主台账)。".format(ep=ep), ""]
    for gid in groups:
        g = compiled.get(gid) or {}
        shots = ", ".join(c.get("shot_id", "") for c in g.get("cameras", []))
        lines.append(f"## {gid}(场景 {g.get('scene_id')};镜 {shots};时长 {g.get('duration_s')}s;计划 directing/{ep}/whitebox_plans/{gid}.json)")
        for n in (x for x in notes if x["group_id"] == gid):
            tg = n.get("target") or {}
            where = []
            if n.get("shot_id"):
                where.append(f"镜 {n['shot_id']}")
            if n.get("t") is not None:
                where.append(f"t={n['t']}s")
            if n.get("view"):
                where.append(f"视角 {n['view']}")
            head = f"  - [{n['id']}] {TARGET_LABEL.get(tg.get('kind'), tg.get('kind'))} {tg.get('label') or ''}"
            if tg.get("id") and tg.get("id") != tg.get("label"):
                head += f"({tg['id']})"
            if where:
                head += " · " + " · ".join(where)
            lines.append(head + ":" + n["text"].replace("\n", " "))
        for issue in issues.get(gid, []):
            lines.append(_issue_lines(issue))
        lines.append("")
    if issues:
        lines.append("待决项按 docs/whitebox.md「待决项与用户裁决」套用:改 issues[].status=applied 并写 applied:{choice,at,note},"
                     f"先 python code/whitebox_issues.py --project {proj} --ep {ep} --pending 核对。")
    return "\n".join(lines).rstrip() + "\n"


def commit_batch(base: Path, ep: str, doc: dict, episode: dict, prepared: dict, *, run_id, agent=AGENT_ID) -> dict:
    """派单成功后:首次提交的组先落基线版本;注释置 submitted;登记批次。"""
    batch = {"id": _next_id(doc["batches"], "B-"), "group_ids": list(prepared["group_ids"]),
             "note_ids": [n["id"] for n in prepared["notes"]],
             "issue_ids": [i.get("issue_id") for rows in prepared["issues"].values() for i in rows],
             "run_id": run_id, "agent": agent, "status": "running", "error": "",
             "base_v": {}, "versions": {}, "created_at": _now(), "finished_at": None}
    for gid in batch["group_ids"]:
        if not versions_of(doc, gid):
            snapshot(base, ep, doc, episode, gid, label="提交前基线")
        batch["base_v"][gid] = current_version(doc, gid)
    for n in prepared["notes"]:
        note = find_note(doc, n["id"])
        if note:
            note["status"] = "submitted"
            note["batch_id"] = batch["id"]
            note["base_v"] = batch["base_v"].get(note["group_id"])
            note["updated_at"] = _now()
    doc["batches"].append(batch)
    return batch


def reconcile(base: Path, ep: str, doc: dict, episode: dict, run_alive) -> list:
    """回收已结束的批次:组指纹变了 → 新版本 + 注释 applied;没变 → nochange + 注释 failed。返回新落的版本记录。"""
    created = []
    compiled = {g["group_id"]: g for g in episode.get("groups", [])}
    errors = {e.get("group_id"): e.get("error") for e in episode.get("errors", [])}
    for batch in doc.get("batches", []):
        if batch.get("status") != "running":
            continue
        alive = run_alive(batch.get("run_id"))
        if alive:
            continue
        # alive False = 运行已结束;None = 运行记录不存在(服务重启)→ 同样按结束处理
        changed_any, failed_any = False, False
        for gid in batch.get("group_ids", []):
            notes = [n for n in doc["notes"] if n.get("batch_id") == batch["id"] and n.get("group_id") == gid]
            if gid not in compiled:
                failed_any = True
                for n in notes:
                    n["status"], n["error"], n["updated_at"] = "failed", f"重编译失败:{errors.get(gid) or '本组没有白模编译结果'}", _now()
                continue
            sha = group_sha(episode, compiled[gid])
            if sha != latest_sha(doc, gid):
                rec = snapshot(base, ep, doc, episode, gid, batch_id=batch["id"], note_ids=[n["id"] for n in notes],
                               label=f"批次 {batch['id']}")
                batch["versions"][gid] = rec["v"]
                created.append(rec)
                changed_any = True
                for n in notes:
                    n["status"], n["version_after"], n["updated_at"] = "applied", rec["v"], _now()
            else:
                for n in notes:
                    n["status"], n["error"], n["updated_at"] = "failed", "运行已结束但本组白模没有变化(Agent 未改计划或未重编译)", _now()
        batch["status"] = "done" if changed_any else ("failed" if failed_any else "nochange")
        batch["finished_at"] = _now()
        if alive is None:
            batch["error"] = "运行记录不存在(服务重启后回收)"
    return created


def summary(doc: dict) -> dict:
    out = {k: 0 for k in NOTE_STATUS}
    for n in doc.get("notes", []):
        out[n.get("status", "draft")] = out.get(n.get("status", "draft"), 0) + 1
    out["total"] = len(doc.get("notes", []))
    out["batches"] = len(doc.get("batches", []))
    out["running"] = sum(1 for b in doc.get("batches", []) if b.get("status") == "running")
    out["versions"] = sum(len(v) for v in doc.get("versions", {}).values())
    return out


# ---------------------------------------------------------------- 头像
_IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp")
_PORTRAIT_NAMES = ("portrait.png", "thumb.png", "head.png", "sheet.png", "main_01.png", "main.png")


def avatar_rel(base: Path, oid: str) -> str | None:
    """人物/生物/道具的代表图相对路径(裁头像属 v3;先给整图供放大查看)。"""
    kind = {"CHAR": "characters", "CRE": "creatures", "PROP": "props"}.get(str(oid).split("-")[0].upper())
    if not kind:
        return None
    d = base / "assets" / "concepts" / kind / oid
    if not d.is_dir():
        return None
    for name in _PORTRAIT_NAMES:
        if (d / name).is_file():
            return f"assets/concepts/{kind}/{oid}/{name}"
    for f in sorted(d.iterdir()):
        if f.suffix.lower() in _IMG_EXTS and f.is_file():
            return f"assets/concepts/{kind}/{oid}/{f.name}"
    return None
