"""成对运镜(motion_pair_bound,三期 2026-09-26;docs/transition_design.md「三期」,WORKFLOW.md §9C)。

过场设计给组边界定的 `transition_in.motion_pair {out, in, speed}`(前组尾镜以 out 运镜带出画面、本组首镜以 in 运镜接入)
要真的出现在两侧组的 video_prompt 里才有用。本模块把它写成两句**固定标记句**(幂等、可剔除、可机检):
  前组最后一个 `Shot N:` 段末尾  → 【运镜对接】本镜结尾以中速向右横摇带出画面,主体移出画外;下一组从同一横摇接入。
  本组第一个 `Shot 1:` 段末尾    → 【运镜对接】本镜开头延续上一组的向右横摇接入,前 0.5 秒画面从横摇中稳定下来。
非中文界面写英文(`Motion pair:`)。段落定位复用 modules.shot_timing.shot_paragraphs(Seedance / Wan `Shot N:`、H3 `[Shot N]`)。
机检 motion_pair_bound:有 motion_pair 的边界,两侧组 prompt 都含与当前 out/in/speed 一致的标记句;旧方向的残留句 = 违规;
组 prompt 尚未写 = skipped(prompt 工位写完必跑 --write)。与 shot_timing 一样:原 prompt 首次备份到 directing/<ep>/whitebox/prompt_backups/。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from modules.shot_timing import KIND_H3, group_kind, shot_paragraphs, ui_lang_is_zh
from modules.whitebox import component, read

MARK_ZH, MARK_EN = "【运镜对接】", "Motion pair:"
# 标记句:从标记到句末(。/ .),不跨行
MARK_RE = re.compile(r"[ \t]*(?:【运镜对接】|Motion pair:)[^\n]*?[。.](?=[ \t]*(?:\n|$))")

# 运镜词(out 用于前组尾镜、in 用于本组首镜)
MOTION_ZH = {
    "pan_left": ("向左横摇", "向左横摇"), "pan_right": ("向右横摇", "向右横摇"),
    "tilt_up": ("向上仰摇", "向上仰摇"), "tilt_down": ("向下俯摇", "向下俯摇"),
    "whip_pan_left": ("向左甩镜", "向左甩镜"), "whip_pan_right": ("向右甩镜", "向右甩镜"),
    "dolly_left": ("向左横移", "向左横移"), "dolly_right": ("向右横移", "向右横移"),
    "push_in": ("推近", "拉远"), "pull_out": ("拉远", "推近"),
}
MOTION_EN = {
    "pan_left": ("pan left", "pan left"), "pan_right": ("pan right", "pan right"),
    "tilt_up": ("tilt up", "tilt up"), "tilt_down": ("tilt down", "tilt down"),
    "whip_pan_left": ("whip pan left", "whip pan left"), "whip_pan_right": ("whip pan right", "whip pan right"),
    "dolly_left": ("dolly left", "dolly left"), "dolly_right": ("dolly right", "dolly right"),
    "push_in": ("push in", "pull out"), "pull_out": ("pull out", "push in"),
}
SPEED_ZH = {"slow": "缓慢", "medium": "中速", "fast": "快速"}
SPEED_EN = {"slow": "slow", "medium": "medium-speed", "fast": "fast"}


def sentences(mp: dict, zh: bool) -> tuple[str, str]:
    """(前组尾镜句, 本组首镜句)。"""
    out, inn, speed = str(mp.get("out") or ""), str(mp.get("in") or ""), str(mp.get("speed") or "medium")
    if zh:
        o = MOTION_ZH.get(out, (out, out))[0]
        i = MOTION_ZH.get(inn, (inn, inn))[0]
        sp = SPEED_ZH.get(speed, speed)
        same = "同一" if MOTION_ZH.get(out, ("",))[0] == i else "对应的"
        return (f"{MARK_ZH}本镜结尾以{sp}{o}带出画面,收尾时主体已移出画外,运镜不停、不减速;下一组从{same}{i}接入。",
                f"{MARK_ZH}本镜开头延续上一组的{sp}{i}接入,前 0.5 秒画面仍在{i}中,随后稳定到本镜构图;开头不切镜、不叠化。")
    o = MOTION_EN.get(out, (out, out))[0]
    i = MOTION_EN.get(inn, (inn, inn))[0]
    sp = SPEED_EN.get(speed, speed)
    same = "the same" if MOTION_EN.get(out, ("",))[0] == i else "the matching"
    return (f"{MARK_EN} end this shot with a {sp} {o} that carries the subject out of frame; the move does not stop or slow down; the next group picks up {same} {i}.",
            f"{MARK_EN} open this shot continuing the previous group's {sp} {i}; the first 0.5 s is still mid-{i}, then it settles into this shot's composition; no cut or dissolve at the head.")


def strip_marks(vp: str) -> str:
    return re.sub(r"[ \t]{2,}", " ", MARK_RE.sub("", vp))


def _append_to_paragraph(vp: str, head: dict, sentence: str) -> str:
    """把句子追加到该 Shot 段体末尾(段体尾部空白之前),与前文之间留一个空格 / 中文不留。"""
    body_end = head["body_end"]
    body = vp[head["head_end"]:body_end]
    stripped = body.rstrip()
    trail = body[len(stripped):]
    sep = "" if (stripped.endswith(("。", ";", "；", "!", "！", "?", "？")) and sentence.startswith(MARK_ZH)) else " "
    if not stripped:
        sep = ""
    return vp[:head["head_end"]] + stripped + sep + sentence + trail + vp[body_end:]


def apply_prompt(vp: str, sentence: str, *, first: bool, h3: bool) -> str:
    """剔除旧标记句后把句子写进首个(first=True)或最后一个 Shot 段;没有 Shot 段 = 原样返回(机检会报)。"""
    vp = strip_marks(vp)
    heads = shot_paragraphs(vp, h3)
    if not heads:
        return vp
    return _append_to_paragraph(vp, heads[0] if first else heads[-1], sentence)


def pairs_of(shot_list: dict) -> list[dict]:
    """shot_list → [{from_group, to_group, motion_pair}](按组序,只含带 motion_pair 的边界)。"""
    groups = [g for g in (shot_list.get("generation_groups") or []) if isinstance(g, dict) and g.get("group_id")]
    out = []
    for a, b in zip(groups, groups[1:]):
        t = b.get("transition_in") if isinstance(b.get("transition_in"), dict) else {}
        mp = t.get("motion_pair")
        if isinstance(mp, dict) and mp.get("out"):
            out.append({"from_group": a["group_id"], "to_group": b["group_id"],
                        "motion_pair": {"out": str(mp["out"]), "in": str(mp.get("in") or ""), "speed": str(mp.get("speed") or "medium")}})
    return out


def _write_prompt(base: Path, ep: str, pp: Path, prompt: dict, new_vp: str) -> None:
    from modules.prompt_layout import paragraphize
    updated = dict(prompt)
    updated["video_prompt"] = paragraphize(new_vp)
    backup = base / "directing" / ep / "whitebox" / "prompt_backups" / pp.name
    backup.parent.mkdir(parents=True, exist_ok=True)
    if not backup.exists():
        backup.write_bytes(pp.read_bytes())
    tmp = pp.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, pp)


def sync_episode(base: Path, ep: str, groups=None, write: bool = False, zh=None) -> dict:
    """逐边界把成对运镜写进两侧组 prompt(--write)并机检。groups 给了只处理涉及这些组的边界;没有 motion_pair 的组只剔除残留标记句。
    返回 {pairs: [...], groups: [{group_id, role, updated, skipped, errors}], updated_prompts, errors, warnings}。"""
    ep = component(ep)
    base = Path(base)
    zh = ui_lang_is_zh() if zh is None else zh
    sl = read(base / "directing" / ep / "shot_list.json", {}) or {}
    pairs = pairs_of(sl)
    want = set(groups or [])
    # 每组要写的句子:role out(尾镜)/ in(首镜);一组可能既是某边界的 to 又是下一边界的 from
    todo: dict[str, dict] = {}
    for p in pairs:
        o, i = sentences(p["motion_pair"], zh)
        todo.setdefault(p["from_group"], {})["out"] = o
        todo.setdefault(p["to_group"], {})["in"] = i
    all_gids = [g.get("group_id") for g in sl.get("generation_groups") or [] if isinstance(g, dict) and g.get("group_id")]
    rows, errors, warnings, updated = [], [], [], []
    for gid in all_gids:
        if want and gid not in want:
            continue
        pp = base / "assets" / "prompts" / ep / f"{gid}.json"
        need = todo.get(gid) or {}
        row = {"group_id": gid, "role": "+".join(sorted(need)) or "", "updated": False, "skipped": "", "errors": []}
        if not pp.is_file():
            if need:
                row["skipped"] = "尚无组 prompt(prompt 工位写完后 --write)"
            rows.append(row)
            continue
        prompt = read(pp, {}) or {}
        vp = str(prompt.get("video_prompt") or "")
        h3 = group_kind(base, ep, gid) == KIND_H3
        if write:
            new_vp = strip_marks(vp)
            if need:
                heads = shot_paragraphs(new_vp, h3)
                if not heads:
                    row["errors"].append(f"{gid}: prompt 无 Shot 段,写不进运镜对接句(prompt 工位按规范分段后重跑)")
                else:
                    if need.get("out"):
                        new_vp = _append_to_paragraph(new_vp, shot_paragraphs(new_vp, h3)[-1], need["out"])
                    if need.get("in"):
                        new_vp = _append_to_paragraph(new_vp, shot_paragraphs(new_vp, h3)[0], need["in"])
            if new_vp != vp:
                _write_prompt(base, ep, pp, prompt, new_vp)
                row["updated"] = True
                updated.append(gid)
                vp = new_vp
        # 机检
        marks = MARK_RE.findall(vp)
        if need:
            heads = shot_paragraphs(vp, h3)
            if not heads:
                row["errors"].append(f"{gid}: prompt 无 Shot 段,无法核运镜对接句")
            for role, sent in need.items():
                if sent not in vp:
                    row["errors"].append(f"{gid}: 缺{'尾镜带出' if role == 'out' else '首镜接入'}句 motion_pair_bound(跑 sync_motion_pairs --write)")
                elif heads:
                    idx = vp.find(sent)
                    target = heads[-1] if role == "out" else heads[0]
                    if not (target["head_end"] <= idx <= target["body_end"]):
                        row["errors"].append(f"{gid}: {'尾镜带出' if role == 'out' else '首镜接入'}句不在 {'最后一个' if role == 'out' else '第一个'} Shot 段内")
            stale = [m for m in marks if m.strip() not in {s.strip() for s in need.values()}]
            if stale:
                row["errors"].append(f"{gid}: 残留过期运镜对接句 {len(stale)} 句(方向 / 速度已改,重跑 --write)")
        elif marks:
            row["errors"].append(f"{gid}: shot_list 无 motion_pair 却有运镜对接句 {len(marks)} 句(设计已撤,重跑 --write 剔除)")
        errors.extend(row["errors"])
        rows.append(row)
    return {"pairs": pairs, "groups": rows, "updated_prompts": updated, "errors": errors, "warnings": warnings}
