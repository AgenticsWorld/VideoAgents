"""视频提示词签字(H3V,workflow.yaml 人工闸门 g7p,每集实例 g7p-epNN,2026-10-05)。

流程位置:本集全部生成组的 p7-prompt 关单 → 用户在「🎦 分镜预览」页逐组审看视频提示词(正文 + 参考图 / 参考视频 / 参考音频)
→ 签字「H3V-视频提示词确认」→ 才派 p7-image(锚点包)与 p7-video(组视频生成)。

本模块只管三件事,服务端(core.py)与 genmedia 共用同一口径:
  - gate_node / video_blocked:读 runs/dag.json 的 g7p(-epNN)节点。**节点在 DAG 里且未放行 = 本集不得生成组视频**
    (genmedia video 提交前硬校验 video_prompt_signed);DAG 里没有该节点(改版前已展开的存量集)= 不拦。
  - missing_prompts:本集 shot_list 的生成组里还没有视频提示词的组(签字前置,缺 = 拒签)。
  - fingerprints / changed_groups:签字时逐组记提示词指纹,页面据此提示「签字后有 N 组改动」。
    签字放行的是「首次开跑」——签字后用户自己在分镜预览页改某组提示词、修改师或缺陷返工改写,都不需要重签。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

GATE_ID = "g7p"
CODE = "H3V"
CHECKPOINT = "H3V-视频提示词确认"
SIGNOFF_REL = "assets/prompts/{ep}/prompt_signoff.json"
DONE_STATES = {"done", "passed", "passed_human_override"}
SKIP_STATES = {"skipped", "cancelled", "waived"}      # 不再执行的终态:不拦
_FP_FIELDS = ("video_prompt", "refs", "audio_refs", "video_refs")
_CLIP_RE = re.compile(r"^grp\d+")


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def dag_nodes(base: Path) -> list[dict]:
    """runs/dag.json → [{id, ...}](三种落盘格式同 core._dag_load_nodes)。"""
    doc = _read_json(Path(base) / "runs" / "dag.json")
    if not isinstance(doc, dict):
        return []
    nodes = doc.get("nodes") or doc.get("tasks") or []
    if isinstance(nodes, dict):
        return [{**v, "id": k} for k, v in nodes.items() if isinstance(v, dict)]
    return [{**n, "id": n.get("id") or n.get("task_id", "")} for n in nodes if isinstance(n, dict)]


def _checkpoint(node: dict) -> str:
    gate = node.get("gate")
    if isinstance(gate, dict) and gate.get("checkpoint"):
        return str(gate["checkpoint"])
    return str(node.get("checkpoint") or "")


def is_gate_node(node: dict, ep: str = "") -> bool:
    """节点是不是(本集的)视频提示词签字闸门:id = g7p / g7p-epNN,或 checkpoint 以 H3V 开头。"""
    nid = str(node.get("id") or "")
    if not (nid == GATE_ID or nid.startswith(GATE_ID + "-") or _checkpoint(node).upper().startswith(CODE)):
        return False
    if not ep or nid == GATE_ID:
        return True
    fe = node.get("for_each")
    if isinstance(fe, dict) and fe.get("episode"):
        return str(fe["episode"]) == ep
    return bool(re.search(rf"(?<![A-Za-z0-9]){re.escape(ep)}(?![A-Za-z0-9])", nid))


def gate_node(base: Path, ep: str) -> dict | None:
    return next((n for n in dag_nodes(base) if is_gate_node(n, ep)), None)


def video_blocked(base: Path, ep: str) -> str:
    """本集组视频现在能不能出:能出返回空串;被闸门拦住返回节点 id(状态)。"""
    node = gate_node(base, ep)
    if not node:
        return ""
    state = str(node.get("state") or "pending")
    if state in DONE_STATES or state in SKIP_STATES:
        return ""
    return f"{node.get('id') or GATE_ID}({state})"


def clip_target(output) -> tuple[Path, str, str] | None:
    """组视频输出路径 …/projects/<项目>/assets/clips/<ep>/<grp>[后缀].mp4 → (项目根, ep, 文件名主干);不是组 clip 返回 None。"""
    try:
        p = Path(output).resolve()
    except Exception:
        return None
    parts = p.parts
    for i in range(len(parts) - 4):
        if parts[i] == "projects" and parts[i + 2:i + 4] == ("assets", "clips") and i + 5 == len(parts) - 1:
            if _CLIP_RE.match(p.stem) and p.suffix.lower() == ".mp4":
                return Path(*parts[:i + 2]), parts[i + 4], p.stem
    return None


def group_ids(base: Path, ep: str) -> list[str]:
    sl = _read_json(Path(base) / "directing" / ep / "shot_list.json") or {}
    return [str(g["group_id"]) for g in (sl.get("generation_groups") or [])
            if isinstance(g, dict) and g.get("group_id")]


def _group_doc(base: Path, ep: str, gid: str) -> dict:
    d = _read_json(Path(base) / "assets" / "prompts" / ep / f"{gid}.json")
    return d if isinstance(d, dict) else {}


def missing_prompts(base: Path, ep: str) -> list[str]:
    """本集生成组里还没有视频提示词(组 json 不在或 video_prompt 为空)的组。"""
    return [gid for gid in group_ids(base, ep)
            if not str(_group_doc(base, ep, gid).get("video_prompt") or "").strip()]


def fingerprints(base: Path, ep: str) -> dict[str, str]:
    """逐组提示词指纹:正文 + 三类参考素材清单(其余字段——回执、注释、meta——改动不算)。"""
    out = {}
    for gid in group_ids(base, ep):
        d = _group_doc(base, ep, gid)
        if not d:
            continue
        blob = json.dumps({k: d.get(k) for k in _FP_FIELDS}, ensure_ascii=False, sort_keys=True)
        out[gid] = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
    return out


def changed_groups(base: Path, ep: str, record: dict | None) -> list[str]:
    """签字后提示词有改动(或新增)的组;没有签字记录 / 记录没带指纹返回空。"""
    signed = (record or {}).get("groups")
    if not isinstance(signed, dict) or not signed:
        return []
    now = fingerprints(base, ep)
    return [gid for gid, fp in now.items() if signed.get(gid) != fp]


def load_record(base: Path, ep: str) -> dict:
    d = _read_json(Path(base) / SIGNOFF_REL.format(ep=ep))
    return d if isinstance(d, dict) else {}
