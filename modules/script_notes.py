"""剧本预览页「🗒 注释」(2026-09-27):用户对本集剧本各对象写的意见,存 story/episodes/<ep>/script_notes.json。

键(key)= 页面对象的稳定标识,与「✏️ 反馈」按钮一一对应:
  *              整集                      S01            场次
  S01/b3         场次内第 3 个正文块(动作/对白/旁白候选/转场,按拆解表 blocks 顺序)
  nar:N-001      旁白定稿条目             hook:HK-01 / hook:*   钩子(单条 / 本集钩子选定)
  event:ev0109   事件卡                    pacing:S01     场次节奏/情绪
  trim:S01/2     删减建议                  plan           分集计划
每条随存页面的定位文本 label(与反馈按钮的定位文本同源:对象描述 + 文件路径),剧本重写后块序变了也能对回。
「📨 提交注释」把本集全部条目拼成一张修改单发修改师并清空本文件(services/runtime/core.py api_script_notes_submit)。
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import time
from pathlib import Path

NOTES_REL = "story/episodes/{ep}/script_notes.json"
NOTES_SCHEMA = "script_notes/1.0"
NOTE_MAX_CHARS = 2000
LABEL_MAX_CHARS = 400
_KEY_RE = re.compile(r"^(\*|[A-Za-z][A-Za-z0-9_\-]{0,15}(:[A-Za-z0-9_\-*]{1,40})?(/[A-Za-z0-9_\-]{1,20})?)$")


def notes_path(base: Path, ep: str) -> Path:
    return base / NOTES_REL.format(ep=ep)


def note_key_ok(key: str) -> bool:
    return bool(_KEY_RE.match(key or ""))


def note_level(key: str) -> str:
    if key == "*":
        return "episode"
    if ":" in key:
        return key.split(":", 1)[0]          # nar / hook / event / pacing / trim
    if key == "plan":
        return "plan"
    return "block" if "/" in key else "scene"


def _read(p: Path) -> dict | None:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def load_notes(base: Path, ep: str) -> dict:
    """{schema, ep, notes: {key: {text, level, label, created_at, updated_at}}};文件缺失/坏 → 空。"""
    d = _read(notes_path(base, ep)) or {}
    if not isinstance(d.get("notes"), dict):
        d = {"schema": NOTES_SCHEMA, "ep": ep, "notes": {}}
    d.setdefault("schema", NOTES_SCHEMA)
    d.setdefault("ep", ep)
    d["notes"] = {k: v for k, v in d["notes"].items() if isinstance(v, dict) and (v.get("text") or "").strip()}
    return d


def update_note(base: Path, ep: str, key: str, text: str, label: str = "") -> dict:
    """写/改/删一条(flock 串行);text 空 = 删除;全空则删文件。返回改后的整份内容。"""
    if not note_key_ok(key):
        raise ValueError(f"bad note key: {key!r}")
    text = (text or "").strip()
    if len(text) > NOTE_MAX_CHARS:
        raise ValueError(f"note too long (>{NOTE_MAX_CHARS} chars)")
    p = notes_path(base, ep)
    p.parent.mkdir(parents=True, exist_ok=True)
    lock = p.with_suffix(".lock")
    with open(lock, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            d = load_notes(base, ep)
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            if not text:
                d["notes"].pop(key, None)
            else:
                rec = d["notes"].get(key) or {"created_at": now}
                if (label or "").strip():
                    rec["label"] = label.strip()[:LABEL_MAX_CHARS]
                rec.update({"text": text, "level": note_level(key), "updated_at": now})
                d["notes"][key] = rec
            d["updated_at"] = now
            d["_readme"] = ("用户在「📜 剧本预览」页写的注释:键 = 页面对象(* 整集 / S01 场次 / S01/b3 场内第 3 块 / nar:ID 旁白 / "
                            "hook:ID 钩子 / event:ID 事件卡 / pacing:S01 节奏 / trim:S01/n 删减建议 / plan 分集计划),"
                            "label = 写注释时该对象的定位文本(含文件路径)。修改师改本集剧本层文件时有则必读,按 label 对回对象逐条落实;"
                            "用户点「📨 提交注释」后本文件会被清空、内容转入修改单。")
            if not d["notes"]:
                p.unlink(missing_ok=True)
                d["notes"] = {}
            else:
                tmp = p.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1))
                os.replace(tmp, p)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)
    return d


def clear_notes(base: Path, ep: str) -> int:
    """「提交注释」后清空:删文件,返回清掉的条数。"""
    p = notes_path(base, ep)
    lock = p.with_suffix(".lock")
    with open(lock, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            n = len(load_notes(base, ep)["notes"])
            p.unlink(missing_ok=True)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)
    return n
