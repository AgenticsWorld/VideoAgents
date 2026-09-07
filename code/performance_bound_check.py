#!/usr/bin/env python3
"""performance_bound 机检:核对对白组 prompt 的表演证据层是否绑定 blocking 的表演意图层节拍。

规则(WORKFLOW §7A / prompt SOUL 职责 2「表演证据层」,2026-08-26;规范见
agents/08-video-gen/prompt/skills/performance-direction/SKILL.md):
  - 仅在当前项目「项目技能」勾选 performance-direction 后执行,默认 skipped;
  - 仅查 shot_list `generation_groups[].audio_plan == "dialogue"`(或 has_dialogue)的组,
    其余组报 skipped;
  - blocking.json 每个说话角色带 `performance`(意图层:goal / arc_from / arc_to /
    trigger{line_ref, word} / forbidden_early[] / end_state,由 blocking agent 产出);
  - prompt agent 写组级 video_prompt 时,该镜对应 Shot 段必须:
      ① `trigger.word` 出现在该段某个 `{}` 台词内(触发词就在本镜台词里);
      ② `trigger.word` 在该段 `{}` 之外至少再出现一次(触发词绑定短语,如「说到『X』时」);
      ③ `end_state` 逐字命中(忽略大小写与连续空白);
      ④ `forbidden_early` 各词组不出现在该段第一处绑定短语之前的文本中(WARN);
  - 全文不得出现 `AU\\d` 编码字样(au_not_in_prompt,FAIL)——编码会被渲染成画面文字;
  - grpNNN.json 应带 `performance[]`(逐镜逐角色 trigger_word / end_state / au_calibration),
    与 blocking 不一致按 WARN 报;
  - 存量 blocking.json 无 `performance` 的对白镜按 WARN 报(待回派 blocking 补写),
    加 --strict 时按 FAIL。

用法:python3 code/performance_bound_check.py --project <slug> --ep ep05           # 查全批
     python3 code/performance_bound_check.py --project <slug> --ep ep05 grp002 …  # 只查指定组
退出码:0=通过(可含 WARN / skipped),1=有违规(逐条打印)。
prompt 批产出后必须全批跑一遍;video-generation 开跑前对单组复核。
"""
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import DATA_DIR, parse_args

AU_RE = re.compile(r"\bAU\s?\d{1,2}\b", re.IGNORECASE)
BRACE_RE = re.compile(r"\{([^{}]*)\}")
PERF_FIELDS = ("goal", "arc_from", "arc_to", "end_state")


PERFORMANCE_SKILL_ID = "08-video-gen/prompt/performance-direction"


def performance_enabled(proj_root: Path) -> bool:
    """独立 CLI 同样遵循项目技能开关,存量/新项目均默认关闭。"""
    try:
        settings = json.loads((proj_root / "settings.json").read_text())
        selected = ((settings.get("project_skills") or {}).get("overrides") or {}).get(PERFORMANCE_SKILL_ID) is True
    except (OSError, ValueError, TypeError, AttributeError):
        return False
    if not selected:
        return False
    state_path = Path(os.environ.get("VIDEOAGENTS_RUNTIME_DIR", DATA_DIR / ".videoagents")) / "state.json"
    try:
        disabled = json.loads(state_path.read_text()).get("skills_disabled") or []
    except (OSError, ValueError, TypeError, AttributeError):
        disabled = []
    return PERFORMANCE_SKILL_ID not in disabled


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def shot_segments(video_prompt: str, n_shots: int):
    """按 `Shot N:` 切分;段数与镜数一致返回 {shot_index: 段文本},否则返回 None(降级全文匹配)。"""
    parts = re.split(r"Shot\s*(\d+)\s*[:：]", video_prompt)
    segs = {}
    for i in range(1, len(parts) - 1, 2):
        segs[int(parts[i])] = parts[i + 1]
    if set(segs) != set(range(1, n_shots + 1)):
        return None
    return segs


def split_dialogue(seg: str):
    """返回 (台词列表, 去掉 `{}` 台词后的正文, 正文中各字符对应原文偏移列表)。"""
    lines = [m.group(1) for m in BRACE_RE.finditer(seg)]
    outside, offsets = [], []
    last = 0
    for m in BRACE_RE.finditer(seg):
        outside.append(seg[last:m.start()])
        offsets.append(last)
        last = m.end()
    outside.append(seg[last:])
    offsets.append(last)
    return lines, outside, offsets


def load_groups(proj_root: Path, ep: str):
    sl = proj_root / "directing" / ep / "shot_list.json"
    if not sl.is_file():
        return {}
    try:
        data = json.loads(sl.read_text())
    except Exception:
        return {}
    out = {}
    for g in data.get("generation_groups", []):
        gid = g.get("group_id")
        if gid:
            out[gid] = g
    return out


def is_dialogue_group(g: dict | None, pj: dict) -> bool:
    if g:
        if g.get("audio_plan"):
            return g.get("audio_plan") == "dialogue"
        if "has_dialogue" in g:
            return bool(g.get("has_dialogue"))
    return bool(pj.get("audio_refs")) or "{" in pj.get("video_prompt", "")


def check_group(pf: Path, proj_root: Path, ep: str, groups: dict, strict: bool):
    if not performance_enabled(proj_root):
        return [], [], True
    pj = json.loads(pf.read_text())
    gid = pj.get("group_id", pf.stem)
    g = groups.get(gid)
    if not is_dialogue_group(g, pj):
        return [], [], True
    raw_shots = pj.get("shots", [])
    shots = [s.get("shot_id") if isinstance(s, dict) else s for s in raw_shots]
    vp = pj.get("video_prompt", "")
    errs, warns = [], []

    for m in AU_RE.finditer(vp):
        errs.append(f"{gid}: video_prompt 含 AU 编码字样 \"{m.group(0)}\"(au_not_in_prompt;编码只许记入 performance[].au_calibration)")
        break

    perf_json = pj.get("performance")
    perf_index = {}
    if isinstance(perf_json, list):
        for e in perf_json:
            if isinstance(e, dict):
                perf_index[(e.get("shot_id"), e.get("char_id"))] = e
    else:
        warns.append(f"{gid}: grpNNN.json 缺 performance[] 字段(AU 校准与触发词记录)")

    segs = shot_segments(vp, len(shots))
    if segs is None and shots:
        warns.append(f"{gid}: Shot 段数与镜数({len(shots)})不符,降级为全文匹配")

    for i, shot_id in enumerate(shots, start=1):
        if not shot_id:
            warns.append(f"{gid}: shots[{i-1}] 缺 shot_id,跳过")
            continue
        bf = proj_root / "directing" / ep / "shots" / shot_id / "blocking.json"
        if not bf.is_file():
            warns.append(f"{gid}/{shot_id}: 无 blocking.json,跳过")
            continue
        bj = json.loads(bf.read_text())
        seg = segs[i] if segs else vp
        where = f"Shot {i}" if segs else "全文"
        lines, outside, offsets = split_dialogue(seg)
        has_dialogue_here = bool(lines)
        for ch in bj.get("characters", []):
            cid = ch.get("id", "?")
            perf = ch.get("performance")
            if not perf:
                if has_dialogue_here:
                    warns.append(f"{gid}/{shot_id}: {cid} 缺 performance(对白镜;若该角色在本镜开口须回派 blocking 补写,纯听者可忽略)")
                continue
            missing = [k for k in PERF_FIELDS if not perf.get(k)]
            trig = (perf.get("trigger") or {})
            word = (trig.get("word") or "").strip()
            if not word:
                missing.append("trigger.word")
            if missing:
                errs.append(f"{gid}/{shot_id}: {cid} performance 缺字段 {missing}")
                continue
            # ① 触发词在本段台词内
            if not any(word in ln for ln in lines):
                errs.append(f"{gid}/{shot_id}: {cid} 触发词『{word}』不在{where}的任何 {{}} 台词内")
            # ② 触发词绑定短语(台词之外再出现一次)
            first_bind = -1
            for k, chunk in enumerate(outside):
                pos = chunk.find(word)
                if pos >= 0:
                    first_bind = offsets[k] + pos
                    break
            if first_bind < 0:
                errs.append(f"{gid}/{shot_id}: {cid} 触发词『{word}』未在{where}的 {{}} 之外再出现(缺触发词绑定短语,如「说到『{word}』时」)")
            # ③ end_state 逐字命中
            end_state = perf.get("end_state", "")
            if norm(end_state) not in norm(seg):
                errs.append(f"{gid}/{shot_id}: {cid} 结束状态未逐字命中{where} —— \"{end_state}\"")
            # ④ 禁止提前反应
            if first_bind >= 0:
                before = seg[:first_bind]
                for fb in perf.get("forbidden_early") or []:
                    if fb and fb in before:
                        warns.append(f"{gid}/{shot_id}: {cid} 触发词之前出现禁止提前反应「{fb}」(检查是否为否定句;若为正面描写须删)")
            # grpNNN.json performance[] 一致性
            e = perf_index.get((shot_id, cid))
            if perf_index and not e:
                warns.append(f"{gid}/{shot_id}: {cid} 未登记于 grpNNN.json performance[]")
            elif e and (e.get("trigger_word") != word or norm(e.get("end_state", "")) != norm(end_state)):
                warns.append(f"{gid}/{shot_id}: {cid} grpNNN.json performance[] 的 trigger_word/end_state 与 blocking 不一致")
    if strict:
        errs += [w for w in warns if "缺 performance(" in w]
        warns = [w for w in warns if "缺 performance(" not in w]
    return errs, warns, False


def main():
    def configure(ap):
        ap.add_argument("groups", nargs="*", help="只查指定组 id(如 grp002);缺省全批")
        ap.add_argument("--strict", action="store_true", help="blocking 缺 performance 的对白镜按 FAIL")
    args, proj_root = parse_args("performance_bound 机检", configure=configure)
    if not performance_enabled(proj_root):
        print("performance_bound: skipped — 项目技能未启用表演控制")
        return 0
    pdir = proj_root / "assets" / "prompts" / args.ep
    if not pdir.is_dir():
        print(f"未找到 {pdir}")
        return 1
    files = sorted(pdir.glob("grp*.json"))
    if args.groups:
        want = set(args.groups)
        files = [f for f in files if f.stem in want]
    groups = load_groups(proj_root, args.ep)
    all_errs, all_warns, skipped, checked = [], [], 0, 0
    for f in files:
        errs, warns, skip = check_group(f, proj_root, args.ep, groups, args.strict)
        if skip:
            skipped += 1
            continue
        checked += 1
        all_errs += errs
        all_warns += warns
    for w in all_warns:
        print("WARN", w)
    for e in all_errs:
        print("FAIL", e)
    print(f"performance_bound: 对白组 {checked} 组已查,非对白组 {skipped} 组 skipped,"
          f"{len(all_errs)} FAIL / {len(all_warns)} WARN")
    return 1 if all_errs else 0


if __name__ == "__main__":
    sys.exit(main())
