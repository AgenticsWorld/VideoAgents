"""场间衔接句(scene_link_bound,二期 2026-10-10;docs/scene_links.md)。

导演采纳的剧本场间衔接登记在 shot_list 组入口 `transition_in.link {kind, out, in, out_shot, in_shot, …}`(宿主过场设计写)。
要让视频模型真的把两头拍得接上,这张登记卡得出现在两侧组的 video_prompt 里。本模块把它写成两句**固定标记句**(幂等、可剔除、可机检),
做法与成对运镜(modules/motion_pairs.py)相同:
  前组最后一个 `Shot N:` 段  → 【场间衔接】本镜最后一个画面落在「…」上…
  本组第一个 `Shot 1:` 段    → 【场间衔接】本镜第一帧就是「…」…
非中文界面写英文(`Scene link:`)。前组句只用登记卡的 out、本组句只用 in(每句只写本镜自己的内容,不提另一场的画面);
导演写的对位要求(link.note)带场次号和行内说明,是给写提示词的工位看的,不进提示词。

与其它宿主标记句(【运镜对接】【原生先入】)同段共存:那两种的剔除口径是「从标记到行尾」,所以衔接句**总是插在它们之前**,
本模块剔除自己的句子时遇到它们即止。机检 scene_link_bound:带登记卡的边界两侧组 prompt 都含与当前登记卡一致的句子且落在正确的
Shot 段;内容已改的残留句 / 登记卡已撤却还留着的句 = 违规;组 prompt 尚未写 = skipped(prompt 工位写完必跑 --write)。
"""
from __future__ import annotations

import re
from pathlib import Path

from modules import scene_links
from modules.motion_pairs import _write_prompt
from modules.shot_timing import KIND_H3, group_kind, shot_paragraphs, ui_lang_is_zh
from modules.whitebox import component, read

MARK_ZH, MARK_EN = "【场间衔接】", "Scene link:"
_OTHER_MARKS = ("【运镜对接】", "Motion pair:", "【原生先入】", "Native lead:")
_STOP = "|".join(re.escape(m) for m in (MARK_ZH, MARK_EN) + _OTHER_MARKS)
# 标记句:从标记到句末(。/ .),不跨行;句末之后须是行尾或另一句宿主标记句
MARK_RE = re.compile(r"[ \t]*(?:【场间衔接】|Scene link:)[^\n]*?[。.](?=[ \t]*(?:\n|$|" + _STOP + "))")
_OTHER_RE = re.compile("|".join(re.escape(m) for m in _OTHER_MARKS))
TEXT_MAX = 80        # 两头内容进提示词的长度上限(字符);剧本写得再长也只取前面这些

# 每种衔接两句:(前组尾镜句, 本组首镜句);{o} = 出,{i} = 入。
# 两条原则:① 每句只写**本镜自己**的内容,不提另一场的画面——两段视频各自生成、互相看不见,写了对面的东西模型会把它画进本镜;
# ② 形状匹配两侧写同一个固定摆位(画面正中、占画面高度约三分之一、停住半秒),文字能约定的对位只有这么多,靠它让两头重合。
_ZH = {
    "shape": ("本镜最后一个画面落在「{o}」上:收尾前让它位于画面正中、占画面高度约三分之一并停住约半秒,镜头不再运动、不切走。",
              "本镜第一帧就是「{i}」:位于画面正中、占画面高度约三分之一,开头停住约半秒;不先交代环境、不叠化、不从黑起。"),
    "action": ("本镜结尾停在「{o}」这个动作的中途:不做完、不收势,运动方向和速度保持到最后一帧。",
               "本镜第一帧接着一个进行到一半的动作往下做:{i};从第一帧起动作已在进行中,方向和速度不变,开头不重新起势、不先交代环境。"),
    "position": ("本镜最后一个画面是「{o}」:收尾时机位、景别、构图固定不动并停住约半秒。",
                 "本镜第一帧是「{i}」:机位、景别、构图固定不动并停住约半秒(与上一场结尾同一个机位、同一个构图)。"),
    "motion": ("本镜结尾:{o};运动方向保持到最后一帧,不减速、不停下、不回头。",
               "本镜第一帧:{i};开头已经在运动中,不从静止起,运动方向不变。"),
    "sound": ("本镜结尾的声音:{o};持续到最后一帧,不提前收、不渐弱。",
              "本镜开头的声音:{i};从第一帧起就在,不渐入。"),
    "line": ("本镜最后一句台词说完即收({o}):不留反应镜头、不拖尾。",
             "本镜第一个画面:{i};开头不铺垫、不先给别的画面。"),
    "contrast": ("本镜结尾:{o};保持到最后一帧,不渐变、不淡出、不提前转向别的画面。",
                 "本镜第一帧即「{i}」:开头不渐入、不铺垫、不从黑起。"),
}
_EN = {
    "shape": ("this shot's last image lands on \"{o}\": before the end it sits at the center of frame, about one third of the frame height, and holds for about half a second; the camera stops moving and does not cut away.",
              "the first frame is \"{i}\": at the center of frame, about one third of the frame height, held for about half a second; do not establish the setting first, no dissolve, no fade from black."),
    "action": ("this shot ends midway through \"{o}\": the action is not completed or settled, and keeps its direction and speed to the last frame.",
               "the first frame continues an action already halfway done: {i}; it is in progress from the first frame with the same direction and speed, no restart of the move and no establishing first."),
    "position": ("this shot's last image is \"{o}\": camera position, shot size and composition stay locked and hold for about half a second.",
                 "the first frame is \"{i}\": camera position, shot size and composition stay locked and hold for about half a second (same position and composition as the end of the previous scene)."),
    "motion": ("this shot ends on: {o}; the movement keeps its direction to the last frame without slowing, stopping or turning back.",
               "the first frame: {i}; it is already in motion, not starting from rest, and the direction of movement does not change."),
    "sound": ("the sound at the end of this shot: {o}; it lasts to the last frame without ending early or fading.",
              "the sound at the head of this shot: {i}; it is there from the first frame with no fade-in."),
    "line": ("this shot ends as soon as its last line is spoken ({o}): no reaction shot and no lingering.",
             "the first image: {i}; no lead-in and no other image first."),
    "contrast": ("this shot ends on: {o}; it holds to the last frame without easing, fading out or turning to another image early.",
                 "the first frame is \"{i}\": no fade-in, no lead-in, no fade from black."),
}


def _clean(text: str) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip().strip("。.;,!?\uff1b\uff0c\uff01\uff1f").strip()
    return s if len(s) <= TEXT_MAX else s[: TEXT_MAX - 1].rstrip() + "…"


def sentences(link: dict, zh: bool) -> tuple[str, str]:
    """(前组尾镜句, 本组首镜句)。"""
    o, i = _clean(link.get("out")), _clean(link.get("in"))
    out_t, in_t = (_ZH if zh else _EN)[link["kind"]]
    if zh:
        return MARK_ZH + out_t.format(o=o, i=i), MARK_ZH + in_t.format(o=o, i=i)
    return f"{MARK_EN} " + out_t.format(o=o, i=i), f"{MARK_EN} " + in_t.format(o=o, i=i)


def strip_marks(vp: str) -> str:
    return re.sub(r"[ \t]{2,}", " ", MARK_RE.sub("", vp))


def _insert(vp: str, head: dict, sentence: str) -> str:
    """把句子放进该 Shot 段:段内已有其它宿主标记句时插在最早那句之前,否则追加到段体末尾。"""
    body = vp[head["head_end"]:head["body_end"]]
    m = _OTHER_RE.search(body)
    if m:
        cut = head["head_end"] + m.start()
        left = vp[:cut].rstrip(" \t")
        return left + ("" if not left or left.endswith("\n") else " ") + sentence + " " + vp[cut:]
    stripped = body.rstrip()
    trail = body[len(stripped):]
    sep = "" if (not stripped or (stripped.endswith(("。", ";", "；", "!", "！", "?", "？")) and sentence.startswith(MARK_ZH))) else " "
    return vp[:head["head_end"]] + stripped + sep + sentence + trail + vp[head["body_end"]:]


def pairs_of(shot_list: dict) -> list[dict]:
    """shot_list → [{from_group, to_group, link}](按组序,只含带合法登记卡的边界)。"""
    groups = [g for g in (shot_list.get("generation_groups") or []) if isinstance(g, dict) and g.get("group_id")]
    out = []
    for a, b in zip(groups, groups[1:]):
        lk = scene_links.link_of(b.get("transition_in"))
        if lk:
            out.append({"from_group": a["group_id"], "to_group": b["group_id"], "link": lk})
    return out


def sync_episode(base: Path, ep: str, groups=None, write: bool = False, zh=None) -> dict:
    """逐边界把衔接句写进两侧组 prompt(write=True)并机检。groups 给了只处理这些组;没有登记卡的组只剔除残留句。
    返回 {pairs, groups: [{group_id, role, updated, skipped, errors}], updated_prompts, errors}。"""
    ep = component(ep)
    base = Path(base)
    zh = ui_lang_is_zh() if zh is None else zh
    sl = read(base / "directing" / ep / "shot_list.json", {}) or {}
    pairs = pairs_of(sl)
    want = set(groups or [])
    todo: dict[str, dict] = {}       # 组 → {in: 首镜句, out: 尾镜句};一组可以既是上一条边界的 to 又是下一条的 from
    for p in pairs:
        o, i = sentences(p["link"], zh)
        todo.setdefault(p["from_group"], {})["out"] = o
        todo.setdefault(p["to_group"], {})["in"] = i
    all_gids = [g.get("group_id") for g in sl.get("generation_groups") or [] if isinstance(g, dict) and g.get("group_id")]
    rows, errors, updated = [], [], []
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
                if not shot_paragraphs(new_vp, h3):
                    row["errors"].append(f"{gid}: prompt 无 Shot 段,写不进场间衔接句(prompt 工位按规范分段后重跑)")
                else:
                    if need.get("in"):
                        new_vp = _insert(new_vp, shot_paragraphs(new_vp, h3)[0], need["in"])
                    if need.get("out"):
                        new_vp = _insert(new_vp, shot_paragraphs(new_vp, h3)[-1], need["out"])
            if new_vp != vp:
                _write_prompt(base, ep, pp, prompt, new_vp)
                row["updated"] = True
                updated.append(gid)
                vp = str((read(pp, {}) or {}).get("video_prompt") or "")
        marks = [m.strip() for m in MARK_RE.findall(vp)]
        if need:
            heads = shot_paragraphs(vp, h3)
            if not heads and not row["errors"]:
                row["errors"].append(f"{gid}: prompt 无 Shot 段,无法核场间衔接句")
            for role, sent in need.items():
                what = "尾镜落点" if role == "out" else "首镜起点"
                if sent not in vp:
                    row["errors"].append(f"{gid}: 缺{what}句 scene_link_bound(跑 sync_scene_links --write)")
                elif heads:
                    idx = vp.find(sent)
                    target = heads[-1] if role == "out" else heads[0]
                    if not (target["head_end"] <= idx <= target["body_end"]):
                        row["errors"].append(f"{gid}: {what}句不在{'最后一个' if role == 'out' else '第一个'} Shot 段内")
            stale = [m for m in marks if m not in {s.strip() for s in need.values()}]
            if stale:
                row["errors"].append(f"{gid}: 残留过期场间衔接句 {len(stale)} 句(登记卡内容已改,重跑 --write)")
        elif marks:
            row["errors"].append(f"{gid}: shot_list 无场间衔接登记卡却有衔接句 {len(marks)} 句(衔接已撤,重跑 --write 剔除)")
        errors.extend(row["errors"])
        rows.append(row)
    return {"pairs": pairs, "groups": rows, "updated_prompts": updated, "errors": errors}
