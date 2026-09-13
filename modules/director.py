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
- 覆盖层(v2,2026-09-13):用户在导演台拖动/改数值的结果写 directing/<ep>/whitebox/director/overrides.json
  (宿主所有,schema director_overrides.v1),按组按对象**整条关键帧数组**覆盖 Agent 计划;编译器
  (modules.whitebox.compile_episode)合并在计划之上,所以预览/导出/接线都立即看到,零 Agent 成本。
  提交时批次指令附上覆盖层原文要 Agent 原样固化进计划并回写源文件;回收时对比「不带覆盖层的编译结果」,
  已一致的对象自动从覆盖层删掉,未一致的保留并标「未固化」。
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
OVERRIDES_SCHEMA = "director_overrides.v1"
DIR_REL = "directing/{ep}/whitebox/director"
LEDGER_REL = DIR_REL + "/notes.json"
OVERRIDES_REL = DIR_REL + "/overrides.json"
OVERRIDE_KINDS = ("actors", "extras", "props", "cameras")
AGENT_ID = "07-directing/whitebox-staging"
# 部门路由(v3):注释可发给不同工位;白模调度是默认,也是唯一会带待决项与覆盖层、并按编译指纹回收版本的路由
ROUTES = {
    "07-directing/whitebox-staging": "白模调度",
    "05-scenes/scene-modeling": "场景建模",
    "07-directing/shot-planning": "分镜",
    "08-video-gen/prompt": "提示词",
    "00-orchestration/reviser": "修改师",
}
MARKS_SCHEMA = "director_marks.v1"
MARKS_REL = DIR_REL + "/marks.json"

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
    for k, d in (("notes", []), ("batches", []), ("versions", {}), ("current", {}), ("approvals", {})):
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


def normalize_agent(agent) -> str:
    agent = str(agent or "").strip() or AGENT_ID
    if agent not in ROUTES:
        raise ValueError("target_agent 须为 " + " | ".join(ROUTES))
    return agent


def make_note(group_id: str, target, text: str, *, t=None, shot_id=None, base_v=None, view=None, target_agent=None) -> dict:
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
            "target_agent": normalize_agent(target_agent),
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
    if "target_agent" in patch:
        note["target_agent"] = normalize_agent(patch["target_agent"])
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


def prepare_batch(base: Path, ep: str, doc: dict, episode: dict, *, group_ids=None, note_ids=None, overrides=None, agent=AGENT_ID) -> dict:
    """挑出要提交的 draft 注释、已裁决未套用的待决项与覆盖层,生成派单指令。不改 doc、不派单。
    agent ≠ 白模调度时只带该路由的注释(通用指令),不带待决项与覆盖层。"""
    agent = normalize_agent(agent)
    if agent != AGENT_ID:
        return _prepare_generic(base, ep, doc, episode, agent, group_ids=group_ids, note_ids=note_ids)
    overrides = overrides if overrides is not None else load_overrides(base, ep)
    notes = [n for n in doc.get("notes", []) if n.get("status") == "draft" and (n.get("target_agent") or AGENT_ID) == AGENT_ID]
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
    ov_groups = {g: v for g, v in overrides.get("groups", {}).items() if any(v.get(k) for k in OVERRIDE_KINDS)}
    if not note_ids and not group_ids:
        groups_in = sorted(set(groups_in) | set(ov_groups))
    groups = [gid for gid in groups_in if any(n["group_id"] == gid for n in notes) or gid in issues or gid in ov_groups]
    if not groups:
        raise ValueError("没有可提交的注释、已裁决的待决项或覆盖层改动")
    missing = [gid for gid in groups if gid not in compiled]
    if missing:
        raise ValueError("这些组没有白模编译结果,无法提交:" + ", ".join(missing))
    ovs = {g: ov_groups[g] for g in groups if g in ov_groups}
    return {"agent": AGENT_ID, "group_ids": groups, "notes": [n for n in notes if n["group_id"] in groups], "issues": issues, "overrides": ovs,
            "message": build_message(base, ep, groups, [n for n in notes if n["group_id"] in groups], issues, compiled, ovs)}


def _prepare_generic(base: Path, ep: str, doc: dict, episode: dict, agent: str, *, group_ids=None, note_ids=None) -> dict:
    notes = [n for n in doc.get("notes", []) if n.get("status") == "draft" and n.get("target_agent") == agent]
    if note_ids:
        wanted = set(note_ids)
        notes = [n for n in notes if n.get("id") in wanted]
    if group_ids:
        gs = set(group_ids)
        notes = [n for n in notes if n.get("group_id") in gs]
    if not notes:
        raise ValueError(f"没有发给「{ROUTES[agent]}」的待提交注释")
    groups = sorted({n["group_id"] for n in notes})
    compiled = {g["group_id"]: g for g in episode.get("groups", [])}
    proj = base.name
    lines = [f"导演台注释批次(项目 {proj} · {ep} · 组 {', '.join(groups)} · 发给 {ROUTES[agent]}):",
             "用户在导演台 3D 白模现场看着分镜组逐对象写下的修改要求,按本工位规约处理并回写你负责的产物;",
             "对象 id 为人物/生物/道具/摄影机(镜号)/场景 id,t 为组内秒;超出本工位职责的条目回执说明并建议转给哪个工位。",
             "回执按注释 id 逐条列出「做了什么 / 改了哪些文件」;不要改 directing/{ep}/whitebox/director/ 下任何文件(宿主台账)。".format(ep=ep), ""]
    for gid in groups:
        g = compiled.get(gid) or {}
        shots = ", ".join(c.get("shot_id", "") for c in g.get("cameras", []))
        lines.append(f"## {gid}(场景 {g.get('scene_id')};镜 {shots};时长 {g.get('duration_s')}s)")
        for n in (x for x in notes if x["group_id"] == gid):
            tg = n.get("target") or {}
            where = [w for w in (f"镜 {n['shot_id']}" if n.get("shot_id") else "", f"t={n['t']}s" if n.get("t") is not None else "") if w]
            head = f"  - [{n['id']}] {TARGET_LABEL.get(tg.get('kind'), tg.get('kind'))} {tg.get('label') or ''}"
            if tg.get("id") and tg.get("id") != tg.get("label"):
                head += f"({tg['id']})"
            if where:
                head += " · " + " · ".join(where)
            lines.append(head + ":" + n["text"].replace("\n", " "))
        lines.append("")
    return {"agent": agent, "group_ids": groups, "notes": notes, "issues": {}, "overrides": {}, "message": "\n".join(lines).rstrip() + "\n"}


def draft_agents(doc: dict, *, group_ids=None, note_ids=None) -> list:
    """待提交注释涉及的路由(按首次出现顺序),白模调度永远排第一。"""
    seen = []
    for n in doc.get("notes", []):
        if n.get("status") != "draft":
            continue
        if note_ids and n.get("id") not in set(note_ids):
            continue
        if group_ids and n.get("group_id") not in set(group_ids):
            continue
        a = n.get("target_agent") or AGENT_ID
        if a not in seen:
            seen.append(a)
    return sorted(seen, key=lambda a: (a != AGENT_ID, a))


def build_message(base: Path, ep: str, groups: list, notes: list, issues: dict, compiled: dict, overrides: dict | None = None) -> str:
    """派单指令(中文,同后期页 _post_agent_message / 白模面板 applyDecisions 口径)。"""
    proj = base.name
    lines = [f"导演台修改批次(项目 {proj} · {ep} · 组 {', '.join(groups)}):用户在 3D 白模现场逐对象写的修改要求,",
             "请按 docs/whitebox.md「导演台」节逐条套用到白模调度计划并重编译。规则:",
             "- 只改 directing/{ep}/whitebox_plans/<组>.json 里对应对象的关键帧/机位/可见名单,并同步回写权威源文件"
             "(shots/<镜>/blocking.json、camera.json(含 whitebox_contract)、shot_list.json 的 blocking_map),不得只改计划不改源;".format(ep=ep),
             "- 注释里的数值(位置米制、朝向弧度、组内秒)照抄不改;文字要求按规约换算成关键帧,时刻 t 是组内秒,shot 是所在镜;",
             "- 改完跑碰撞与朝向自检(docs/whitebox.md「防穿模检查」「对话身体朝向」),不得靠换机位、隐藏人、缩小人掩盖;",
             "- 全部源文件回写与人物同步完成后,复核受影响组全部机位及跨组 match,再用宿主 modules.whitebox_camera 的指纹函数记录审查;禁止只刷指纹或删除契约消错;",
             f"- 交付前执行 python code/render_whitebox.py --project {proj} --ep {ep} --check-only (不带组号),必须退出码0、errors为空且组数齐全,不能只验选中组;",
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
        lines.extend(overrides_message(ep, gid, (overrides or {}).get(gid) or {}))
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
             "override_objects": {g: [f"{k}/{o}" for k in OVERRIDE_KINDS for o in (v.get(k) or {})] for g, v in (prepared.get("overrides") or {}).items()},
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


def reconcile(base: Path, ep: str, doc: dict, episode: dict, run_alive, plain_episode=None) -> list:
    """回收已结束的批次:组指纹变了 → 新版本 + 注释 applied;没变 → nochange + 注释 failed。返回新落的版本记录。
    plain_episode = 不带覆盖层的编译结果(有则用来判断覆盖层是否已固化并自动清理)。"""
    created = []
    ov_doc = load_overrides(base, ep) if plain_episode is not None else None
    ov_changed = False
    compiled = {g["group_id"]: g for g in episode.get("groups", [])}
    errors = {e.get("group_id"): e.get("error") for e in episode.get("errors", [])}
    for batch in doc.get("batches", []):
        if batch.get("status") != "running":
            continue
        alive = run_alive(batch.get("run_id"))
        if alive:
            continue
        # alive False = 运行已结束;None = 运行记录不存在(服务重启)→ 同样按结束处理
        if (batch.get("agent") or AGENT_ID) != AGENT_ID:
            # 其它工位:宿主没有可核验的产物指纹,运行结束即算已处理(注释 verified=false,回执在运行记录里)
            for n in doc["notes"]:
                if n.get("batch_id") == batch["id"]:
                    n["status"], n["verified"], n["updated_at"] = "applied", False, _now()
            batch["status"], batch["finished_at"] = "done", _now()
            if alive is None:
                batch["error"] = "运行记录不存在(服务重启后回收)"
            continue
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
        consolidated_any = False
        if ov_doc is not None:
            plain = {g["group_id"]: g for g in plain_episode.get("groups", [])}
            still = {}
            for gid in batch.get("group_ids", []):
                if gid in plain and ov_doc.get("groups", {}).get(gid):
                    before = json.dumps(ov_doc["groups"].get(gid), sort_keys=True)
                    pending = consolidate_overrides(ov_doc, gid, plain[gid])
                    if pending:
                        still[gid] = pending
                    if json.dumps(ov_doc.get("groups", {}).get(gid), sort_keys=True) != before:
                        ov_changed = consolidated_any = True
            if still:
                batch["overrides_pending"] = still
        # 覆盖层只固化不改画面:编译结果指纹不变也算 done(白模本来就已是用户改定的样子)
        batch["status"] = "done" if (changed_any or (consolidated_any and not failed_any)) else ("failed" if failed_any else "nochange")
        batch["finished_at"] = _now()
        if alive is None:
            batch["error"] = "运行记录不存在(服务重启后回收)"
    if ov_changed:
        save_overrides(base, ep, ov_doc)
    return created


def summary(doc: dict) -> dict:
    out = {k: 0 for k in NOTE_STATUS}
    for n in doc.get("notes", []):
        out[n.get("status", "draft")] = out.get(n.get("status", "draft"), 0) + 1
    out["total"] = len(doc.get("notes", []))
    out["batches"] = len(doc.get("batches", []))
    out["running"] = sum(1 for b in doc.get("batches", []) if b.get("status") == "running")
    out["versions"] = sum(len(v) for v in doc.get("versions", {}).values())
    out["approved"] = len(doc.get("approvals", {}) or {})
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


# ---------------------------------------------------------------- 覆盖层(v2)
def overrides_path(base: Path, ep: str) -> Path:
    return base / OVERRIDES_REL.format(ep=ep)


def load_overrides(base: Path, ep: str) -> dict:
    doc = read_json(overrides_path(base, ep))
    if not isinstance(doc, dict) or doc.get("schema") != OVERRIDES_SCHEMA or not isinstance(doc.get("groups"), dict):
        return {"schema": OVERRIDES_SCHEMA, "ep": ep, "updated_at": _now(), "groups": {}}
    return doc


def save_overrides(base: Path, ep: str, doc: dict) -> None:
    doc["schema"] = OVERRIDES_SCHEMA
    doc["ep"] = ep
    doc["updated_at"] = _now()
    doc["groups"] = {g: v for g, v in doc.get("groups", {}).items() if any(v.get(k) for k in OVERRIDE_KINDS)}
    if doc["groups"]:
        write_json(overrides_path(base, ep), doc)
    elif overrides_path(base, ep).is_file():
        overrides_path(base, ep).unlink()


def _clean_keyframes(keys) -> list:
    if not isinstance(keys, list) or not keys:
        raise ValueError("keyframes 须为非空数组")
    out = []
    for k in keys:
        if not isinstance(k, dict) or "t" not in k or "position" not in k:
            raise ValueError("每个关键帧须含 t 与 position")
        item = {}
        for name, v in k.items():
            if name in ("position", "target", "left_hand", "right_hand", "scale"):
                if not (isinstance(v, list) and len(v) == 3):
                    raise ValueError(f"{name} 须为三维数组")
                item[name] = [round(float(x), 4) for x in v]
            elif name == "t":
                item[name] = round(float(v), 3)
            elif name in ("pose", "easing"):
                item[name] = str(v)
            elif name in ("visible", "hold"):
                item[name] = bool(v)
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                item[name] = round(float(v), 4)
            else:
                item[name] = v
        out.append(item)
    out.sort(key=lambda k: k["t"])
    return out


def set_group_overrides(doc: dict, gid: str, patch: dict) -> dict:
    """按对象合并:patch = {actors:{id:{keyframes}|null}, extras:{}, props:{id:{keyframes}|{position,yaw,..}|null}, cameras:{shot:{keyframes}|null}}。
    值为 null = 删除该对象的覆盖。返回该组合并后的覆盖。"""
    if not _ID_RE.match(str(gid or "")):
        raise ValueError("group_id 非法")
    if not isinstance(patch, dict):
        raise ValueError("patch 须为对象")
    group = doc.setdefault("groups", {}).setdefault(gid, {})
    for kind in OVERRIDE_KINDS:
        items = patch.get(kind)
        if items is None:
            continue
        if not isinstance(items, dict):
            raise ValueError(f"{kind} 须为对象")
        bucket = group.setdefault(kind, {})
        for oid, val in items.items():
            if not _ID_RE.match(str(oid or "")):
                raise ValueError(f"{kind} id 非法:{oid}")
            if val is None:
                bucket.pop(oid, None)
                continue
            if not isinstance(val, dict):
                raise ValueError(f"{kind}.{oid} 须为对象")
            entry = {}
            if "keyframes" in val:
                entry["keyframes"] = _clean_keyframes(val["keyframes"])
            if kind == "props":
                for name in ("position", "yaw", "pitch", "roll", "scale", "visible"):
                    if name in val and val[name] is not None:
                        entry[name] = val[name]
            if not entry:
                raise ValueError(f"{kind}.{oid} 没有可用字段")
            entry["updated_at"] = _now()
            bucket[oid] = entry
        if not bucket:
            group.pop(kind, None)
    group["updated_at"] = _now()
    if not any(group.get(k) for k in OVERRIDE_KINDS):
        doc["groups"].pop(gid, None)
        return {}
    return group


def clear_group_overrides(doc: dict, gid: str, kind: str | None = None, oid: str | None = None) -> None:
    group = doc.get("groups", {}).get(gid)
    if not group:
        return
    if kind and oid:
        group.get(kind, {}).pop(oid, None)
        if not group.get(kind):
            group.pop(kind, None)
    elif kind:
        group.pop(kind, None)
    else:
        doc["groups"].pop(gid, None)
        return
    if not any(group.get(k) for k in OVERRIDE_KINDS):
        doc["groups"].pop(gid, None)


def apply_overrides(group: dict, ov: dict) -> dict:
    """把一组的覆盖层套到编译后的 group 上(原地);返回 {kind:[ids]} 记录哪些对象被覆盖。无效覆盖只警告不套用。"""
    from modules.whitebox import validate_keys
    applied = {k: [] for k in OVERRIDE_KINDS}
    duration = group.get("duration_s", 0)
    for kind in ("actors", "extras", "props"):
        entries = ov.get(kind) or {}
        for obj in group.get(kind, []):
            e = entries.get(obj.get("id"))
            if not e:
                continue
            try:
                if "keyframes" in e:
                    keys = json.loads(json.dumps(e["keyframes"]))
                    validate_keys(keys, duration)
                    obj["keyframes"] = keys
                if kind == "props":
                    for name in ("position", "yaw", "pitch", "roll", "scale", "visible"):
                        if name in e:
                            obj[name] = e[name]
                applied[kind].append(obj["id"])
            except (ValueError, KeyError, TypeError) as err:
                group.setdefault("warnings", []).append(f"导演台覆盖层 {kind}/{obj.get('id')} 无效已忽略:{err}")
    entries = ov.get("cameras") or {}
    for cam in group.get("cameras", []):
        e = entries.get(cam.get("shot_id"))
        if not e or "keyframes" not in e:
            continue
        try:
            keys = json.loads(json.dumps(e["keyframes"]))
            validate_keys(keys, cam.get("duration_s", 0), camera=True)
            cam["keyframes"] = keys
            cam["overridden"] = True
            applied["cameras"].append(cam["shot_id"])
        except (ValueError, KeyError, TypeError) as err:
            group.setdefault("warnings", []).append(f"导演台覆盖层 cameras/{cam.get('shot_id')} 无效已忽略:{err}")
    applied = {k: v for k, v in applied.items() if v}
    if applied:
        group["overrides"] = applied
    return applied


def _same_keys(a, b) -> bool:
    try:
        return json.dumps(_clean_keyframes(a), sort_keys=True) == json.dumps(_clean_keyframes(b), sort_keys=True)
    except ValueError:
        return False


def consolidate_overrides(doc: dict, gid: str, plain_group: dict) -> list:
    """回收:plain_group = 不带覆盖层编译出的组。计划已与覆盖一致的对象从覆盖层删掉;返回仍未固化的 "kind/id" 列表。"""
    group = doc.get("groups", {}).get(gid)
    if not group:
        return []
    pending = []
    for kind in ("actors", "extras", "props"):
        objs = {o.get("id"): o for o in plain_group.get(kind, [])}
        for oid, e in list((group.get(kind) or {}).items()):
            obj = objs.get(oid)
            ok = bool(obj) and ("keyframes" not in e or _same_keys(e["keyframes"], obj.get("keyframes") or [])) and \
                all(obj.get(n) == e[n] for n in ("position", "yaw", "pitch", "roll", "scale", "visible") if n in e)
            if ok:
                group[kind].pop(oid)
            else:
                pending.append(f"{kind}/{oid}")
        if kind in group and not group[kind]:
            group.pop(kind)
    cams = {c.get("shot_id"): c for c in plain_group.get("cameras", [])}
    for sid, e in list((group.get("cameras") or {}).items()):
        cam = cams.get(sid)
        if cam and _same_keys(e.get("keyframes") or [], cam.get("keyframes") or []):
            group["cameras"].pop(sid)
        else:
            pending.append(f"cameras/{sid}")
    if "cameras" in group and not group["cameras"]:
        group.pop("cameras")
    if not any(group.get(k) for k in OVERRIDE_KINDS):
        doc["groups"].pop(gid, None)
    return pending


def overrides_message(ep: str, gid: str, ov: dict) -> list:
    """派单指令里的覆盖层段:逐对象给出完整关键帧 JSON,要求原样固化。"""
    lines = []
    for kind in OVERRIDE_KINDS:
        for oid, e in (ov.get(kind) or {}).items():
            body = {k: v for k, v in e.items() if k != "updated_at"}
            lines.append(f"  - 覆盖层 {kind}/{oid}(用户在导演台直接改定,**数值原样写进计划**,不要重新推断):"
                         f"{json.dumps(body, ensure_ascii=False, separators=(',', ':'))}")
    if lines:
        lines.insert(0, f"  覆盖层(directing/{ep}/whitebox/director/overrides.json 的 {gid} 段,编译时已合并在计划之上;固化进 whitebox_plans/{gid}.json 后宿主会自动清掉):")
    return lines


# ---------------------------------------------------------------- 批准(v3)
def set_approval(doc: dict, gid: str, sha: str | None, on: bool, by: str = "user:page") -> dict:
    """组级「批准 ✓」:记当时编译指纹;白模再变即显示过期。"""
    if not _ID_RE.match(str(gid or "")):
        raise ValueError("group_id 非法")
    approvals = doc.setdefault("approvals", {})
    if not on:
        approvals.pop(gid, None)
        return {}
    approvals[gid] = {"sha": sha, "at": _now(), "by": by}
    return approvals[gid]


def approvals_public(doc: dict, episode: dict) -> dict:
    """{gid: {sha, at, by, stale}}:stale = 批准后该组编译指纹变了。"""
    out = {}
    current = {g["group_id"]: group_sha(episode, g) for g in episode.get("groups", []) if g.get("scene_id") in episode.get("scenes", {})}
    for gid, rec in (doc.get("approvals") or {}).items():
        out[gid] = {**rec, "stale": bool(rec.get("sha")) and current.get(gid) not in (None, rec.get("sha"))}
    return out


# ---------------------------------------------------------------- 地面定位标记(v3)
def marks_path(base: Path, ep: str) -> Path:
    return base / MARKS_REL.format(ep=ep)


def load_marks(base: Path, ep: str) -> dict:
    doc = read_json(marks_path(base, ep))
    if not isinstance(doc, dict) or doc.get("schema") != MARKS_SCHEMA or not isinstance(doc.get("scenes"), dict):
        return {"schema": MARKS_SCHEMA, "ep": ep, "updated_at": _now(), "scenes": {}}
    return doc


def save_marks(base: Path, ep: str, doc: dict) -> None:
    doc["schema"] = MARKS_SCHEMA
    doc["ep"] = ep
    doc["updated_at"] = _now()
    doc["scenes"] = {k: v for k, v in doc.get("scenes", {}).items() if v}
    write_json(marks_path(base, ep), doc)


def set_scene_marks(doc: dict, sid: str, marks) -> list:
    """整表替换某场景的标记:[{id?, name, position:[x,y,z], color?}]。id 缺省按序补;名字不能空。"""
    if not _ID_RE.match(str(sid or "")):
        raise ValueError("scene_id 非法")
    if not isinstance(marks, list):
        raise ValueError("marks 须为数组")
    out, used = [], set()
    for i, m in enumerate(marks):
        if not isinstance(m, dict):
            raise ValueError("标记须为对象")
        name = str(m.get("name") or "").strip()[:40]
        if not name:
            raise ValueError("标记名不能为空")
        pos = m.get("position")
        if not (isinstance(pos, list) and len(pos) == 3):
            raise ValueError(f"标记 {name} 的 position 须为三维数组")
        mid = str(m.get("id") or "").strip() or f"M{i + 1:02d}"
        if not _ID_RE.match(mid) or mid in used:
            mid = f"M{len(out) + 1:02d}"
            while mid in used:
                mid = mid + "x"
        used.add(mid)
        out.append({"id": mid, "name": name, "position": [round(float(x), 3) for x in pos],
                    "color": str(m.get("color") or "")[:16] or None, "updated_at": _now()})
    doc.setdefault("scenes", {})[sid] = out
    return out
