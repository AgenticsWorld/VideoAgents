"""镜次时长契约(shot_timing_bound,2026-09-23):把 shot_list 的逐镜 duration_s 按生效视频模型的官方写法写进组级 video_prompt。

背景:出片时只把组 total_duration_s 作 `--duration` 传给 genmedia,正文各 `Shot N:` 段从不带时长,模型自行分配组内节奏。
各官方提示词技能(agents/08-video-gen/prompt/skills/<模型>-pe)对「时间」的口径不同,本模块按组生效模型分四种写法:

  * Seedance 2.5(sd25)/ Wan 3.0(wan3)——**连续整数秒时间段**(官方 `0-5秒：…；5-10秒：…` / `镜头N xx-xx秒`):
    时间段是「事件预算」不是帧级剪辑点,官方明说不能精确到 0.5 秒,事件过密应减段不细分。故按 shot_list 累计边界四舍五入
    到整数秒切段,亚秒级快切的相邻几镜并成一段;每段的标签写在该段**第一镜**的 `Shot N:` 段头之后:
      中文界面  `Shot 1: 0-2秒：…`            合并段 `Shot 5: 8-10秒（Shot 5–Shot 8）：…`
      其他语言  `Shot 1: 0-2s: …`             合并段 `Shot 5: 8-10s (Shot 5–Shot 8): …`
    段内其余镜不带标签。段与段首尾相接,自 0 起、终于 round(total_duration_s)。
  * MiniMax H3(h3)——**切点时间戳**(官方 `[Shot N] At MM:SS.mmm, …`,首镜不写):Shot k(k≥2)段头后写 `At MM:SS.mmm,`,
    值 = 前 k-1 镜 duration_s 之和(H3 支持毫秒,逐镜精确到 0.1s 可表达);严格递增且小于组总时长。
  * Seedance 2.0(sd20)——官方指南「模型对精确时间(如 0–3 秒)的支持不稳定,强行限制时长可能导致生成结果异常」:**不写**,
    存量标签由 --write 剔除,机检发现标签即违规。
  * 其他/未知模型(ComfyUI 未知工作流等)——机检 skipped,不改正文。

机检(shot_timing_bound)三件事:① 该写的组每段/每切点都写了;② 标签连续不重叠、自 0 起、末段止于 round(total_duration_s)
(H3:切点 = 累计时长);③ shot_list 组 total_duration_s 与 Σ duration_s 一致(±0.5s,否则是 shot-planning 数据问题)。
每段时长与其覆盖各镜 duration_s 之和相差 >1s 也算违规(防 agent 乱切)。

用法:见 code/sync_shot_timing.py(不带 --write 只机检;--write 幂等回写,首次备份原 prompt)。
"""
from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path

from modules.whitebox import component, read

KIND_SD25, KIND_SD20, KIND_H3, KIND_WAN3 = 'sd25', 'sd20', 'h3', 'wan3'
SEGMENT_KINDS = (KIND_SD25, KIND_WAN3)
KIND_LABEL = {KIND_SD25: 'Seedance 2.5', KIND_SD20: 'Seedance 2.0', KIND_H3: 'MiniMax H3', KIND_WAN3: 'Wan 3.0'}

CUM_TOL = 0.06        # H3 切点与累计时长允许误差(秒)
TOTAL_TOL = 0.5       # 组 total_duration_s 与 Σ duration_s 允许误差
SEG_LEN_TOL = 1.0     # 时间段长度与覆盖各镜时长之和允许误差

# 段头:`Shot 1:` / `Shot 1：` / `Shot 1｜标题。`(2.5 变体) / `[Shot 1]`(H3 官方)
_HEAD_RE = re.compile(r'\[Shot\s*(\d+)\]|Shot\s*(\d+)\s*(?:[:：]|[｜|][^。.\n]*[。.])')
# 时间段标签:`0-2秒：` / `8-10秒（Shot 5–Shot 8）：` / `0-2s:` / `8-10s (Shot 5–Shot 8):`
TAG_RE = re.compile(r'(?<![\d.\-–~])(\d+)\s*[-–]\s*(\d+)\s*(秒|s)\s*'
                    r'(?:[（(]\s*Shot\s*(\d+)\s*[–\-~]\s*Shot\s*(\d+)\s*[）)])?\s*[：:]')
# H3 切点:`At 00:06.000,`
CUT_RE = re.compile(r'\bAt\s+(\d{1,2}):(\d{2})\.(\d{3})\s*,')
_TAIL_LABELS = ('Global constraints:', 'overall_soundscape:', 'non_diegetic_music:')


# ---------------------------------------------------------------- 生效模型
def model_kind(text) -> str:
    """模型 id / 技能 id / 工作流名 → 四种写法之一('' = 未知)。"""
    t = str(text or '').lower()
    if not t:
        return ''
    if ('minimax' in t and 'h3' in t) or t.endswith('/h3-pe') or 'hailuo-3' in t or 'hailuo3' in t:
        return KIND_H3
    if re.search(r'seedance[-_ .]?2[-_.]5', t) or t.endswith('/sd25-pe') or 'sd25' in t:
        return KIND_SD25
    if 'seedance' in t or t.endswith('/sd20-pe') or 'sd20' in t:
        return KIND_SD20
    if re.search(r'wan[-_ .]?3(?:[-_.]0)?(?![\d])', t) or t.endswith('/wan3-pe'):
        return KIND_WAN3
    return ''


def group_kind(base: Path, ep: str, gid: str) -> str:
    """本组生效视频模型的写法:组级覆盖(assets/group_settings,无组级时集级 episode.json)→ 项目提示词技能快照 → genmedia 当前视频模型。
    判定链与 modules.shot_plates.group_is_v25 / group_is_h3 一致。"""
    from modules.shot_plates import _group_or_episode_settings
    gs = _group_or_episode_settings(base, ep, gid)
    eff = gs.get('effective') or {}
    for t in (gs.get('video_model'), eff.get('skill_id'), eff.get('resolved_from')):
        k = model_kind(t)
        if k:
            return k
    if gs.get('video_model'):
        return ''
    peff = ((read(base / 'settings.json', {}) or {}).get('prompt_skill') or {}).get('effective') or {}
    for t in (peff.get('skill_id'), peff.get('resolved_from')):
        k = model_kind(t)
        if k:
            return k
    try:
        from modules.whitebox_refs import video_budget
        b = video_budget(base, ep, gid)
        return model_kind(b.get('model')) or model_kind(b.get('reason'))
    except Exception:  # noqa: BLE001
        return ''


def ui_lang_is_zh() -> bool:
    from modules.shot_plates import ui_lang_is_zh as _zh
    return _zh()


# ---------------------------------------------------------------- 纯函数:切段 / 标签
def _round_half_up(x: float) -> int:
    return int(math.floor(float(x) + 0.5))


def plan_segments(durations, total) -> list[tuple[int, int, list[int]]]:
    """逐镜时长 → 连续整数秒时间段 [(start, end, [镜下标…]), …]。
    累计边界四舍五入到整数秒;边界没前进(亚秒快切)的镜并入下一段;末段强制止于 round(total);尾部不足 1s 的镜并入末段。"""
    T = _round_half_up(total or 0)
    durs = [float(d or 0) for d in durations]
    n = len(durs)
    if n == 0 or T <= 0:
        return []
    segs: list[list] = []
    cum, last, cur = 0.0, 0, []
    for i, d in enumerate(durs):
        cum += d
        cur.append(i)
        b = _round_half_up(cum)
        if b - last >= 1 and i < n - 1:
            segs.append([last, b, cur])
            last, cur = b, []
    if cur:
        segs.append([last, T, cur])
    else:
        segs[-1][1] = T
    while len(segs) > 1 and segs[-1][0] >= segs[-1][1]:   # 四舍五入把末段挤没了:并入前一段
        prev = segs.pop()
        segs[-1][1] = T
        segs[-1][2] = segs[-1][2] + prev[2]
    return [(int(s), int(e), list(ids)) for s, e, ids in segs]


def cut_times(durations) -> list[float]:
    """H3 切点:第 k 镜(k≥2)的切入时刻 = 前 k-1 镜时长之和。返回长度 = 镜数,首项 0。"""
    out, cum = [], 0.0
    for d in durations:
        out.append(round(cum, 3))
        cum += float(d or 0)
    return out


def segment_tag(start: int, end: int, first_shot: int, last_shot: int, zh: bool) -> str:
    if zh:
        span = f'（Shot {first_shot}–Shot {last_shot}）' if last_shot > first_shot else ''
        return f'{start}-{end}秒{span}：'
    span = f' (Shot {first_shot}–Shot {last_shot})' if last_shot > first_shot else ''
    return f'{start}-{end}s{span}:'


def cut_tag(t: float) -> str:
    ms = int(round(float(t) * 1000))
    m, rem = divmod(ms, 60000)
    s, ms = divmod(rem, 1000)
    return f'At {m:02d}:{s:02d}.{ms:03d},'


# ---------------------------------------------------------------- 正文解析
def shot_paragraphs(vp: str, h3: bool) -> list[dict]:
    """依序找 Shot 1..n 段头:[{no, head_start, head_end, body_end}, …];找不到连续序号即截断。
    H3 正文从 detailed_description: 之后找(前面的 retention_analysis 会提到 [Shot 2]);非 H3 只认 `Shot N:`/`Shot N｜…。` 形式。"""
    start = 0
    if h3:
        m = re.search(r'detailed_description\s*[:：]', vp)
        if m:
            start = m.end()
    tail = len(vp)
    for lab in _TAIL_LABELS:
        j = vp.find(lab, start)
        if j >= 0:
            tail = min(tail, j)
    heads = []
    pos, k = start, 1
    while True:
        found = None
        for m in _HEAD_RE.finditer(vp, pos, tail):
            no = m.group(1) or m.group(2)
            if m.group(1) is not None and not h3:
                continue
            if int(no) == k:
                found = m
                break
        if not found:
            break
        heads.append({'no': k, 'head_start': found.start(), 'head_end': found.end()})
        pos, k = found.end(), k + 1
    for i, h in enumerate(heads):
        h['body_end'] = heads[i + 1]['head_start'] if i + 1 < len(heads) else tail
    return heads


def _strip_in_bodies(vp: str, heads: list[dict], pattern: re.Pattern) -> str:
    """剔除各 Shot 段体内的标签;标签前后的空格归一为单个(中文散文本无空格,英文散文本就单空格)。"""
    out, last = [], 0
    for h in heads:
        out.append(vp[last:h['head_end']])
        body = vp[h['head_end']:h['body_end']]
        if pattern.search(body):
            body = pattern.sub(' ', body)
            body = re.sub(r'[ \t]{2,}', ' ', body)
            if not vp[h['head_start']:h['head_end']].startswith('['):   # `Shot N:` 后中文散文/段落标签不留空格;`[Shot N]` 官方式保留一个空格
                body = re.sub(r'^ (?=[\n\u3000-\u9fff\uff00-\uffef【])', '', body)
        out.append(body)
        last = h['body_end']
    out.append(vp[last:])
    return ''.join(out)


def strip_tags(vp: str, h3: bool) -> str:
    heads = shot_paragraphs(vp, h3)
    if not heads:
        return vp
    vp2 = _strip_in_bodies(vp, heads, TAG_RE)
    heads = shot_paragraphs(vp2, h3)
    return _strip_in_bodies(vp2, heads, CUT_RE)


def _insert_after_heads(vp: str, heads: list[dict], tags: dict[int, str]) -> str:
    """标签紧跟段头(`Shot 1: 0-2秒：…` / `[Shot 2] At 00:06.000, …`),与后文之间留一个空格。"""
    out, last = [], 0
    for h in heads:
        out.append(vp[last:h['head_end']])
        t = tags.get(h['no'])
        if t:
            rest = vp[h['head_end']:h['body_end']]
            nxt = rest[:1]
            cjk_next = bool(re.match(r'[\u3000-\u9fff\uff00-\uffef【]', nxt))
            out.append(' ' + t + ('' if (nxt in (' ', '\n', '') or (t.endswith('：') and cjk_next)) else ' '))
        last = h['head_end']
    out.append(vp[last:])
    return ''.join(out)


def apply_prompt(vp: str, durations, total, kind: str, zh: bool) -> str:
    """按写法重写正文里的时长标签(幂等)。镜数与 Shot 段数不符时只剔除、不写入。"""
    h3 = kind == KIND_H3
    vp = strip_tags(vp, h3)
    if kind not in SEGMENT_KINDS and kind != KIND_H3:
        return vp
    heads = shot_paragraphs(vp, h3)
    if len(heads) != len(durations) or not heads:
        return vp
    tags: dict[int, str] = {}
    if h3:
        for k, t in enumerate(cut_times(durations), 1):
            if k >= 2:
                tags[k] = cut_tag(t)
    else:
        for s, e, ids in plan_segments(durations, total):
            tags[ids[0] + 1] = segment_tag(s, e, ids[0] + 1, ids[-1] + 1, zh)
    return _insert_after_heads(vp, heads, tags)


# ---------------------------------------------------------------- 机检
def check_prompt(vp: str, durations, total, kind: str, gid: str) -> tuple[list[str], list[str]]:
    errs, warns = [], []
    durs = [float(d or 0) for d in durations]
    T = _round_half_up(total or 0)
    if durs and abs(sum(durs) - float(total or 0)) > TOTAL_TOL:
        errs.append(f'{gid}: shot_list 组 total_duration_s={total} 与 Σ duration_s={round(sum(durs), 2)} 不一致(shot-planning 数据问题,先改 shot_list)')
    h3 = kind == KIND_H3
    heads = shot_paragraphs(vp, h3)
    if kind == KIND_SD20:
        n_tag = sum(1 for h in heads if TAG_RE.search(vp[h['head_end']:h['body_end']]) or CUT_RE.search(vp[h['head_end']:h['body_end']]))
        if n_tag:
            errs.append(f'{gid}: Seedance 2.0 官方指南「精确时间段支持不稳定」,正文不得写时长标签(现 {n_tag} 处;跑 code/sync_shot_timing.py --write 剔除)')
        return errs, warns
    if kind not in SEGMENT_KINDS and kind != KIND_H3:
        return errs, warns
    if not heads:
        errs.append(f'{gid}: 正文无 Shot N 段,无法核对镜次时长')
        return errs, warns
    if len(heads) != len(durs):
        errs.append(f'{gid}: Shot 段数 {len(heads)} ≠ 组内镜数 {len(durs)},先修正组结构再写时长')
        return errs, warns
    bodies = {h['no']: vp[h['head_end']:h['body_end']] for h in heads}
    n = len(durs)
    if h3:
        cums = cut_times(durs)
        if CUT_RE.search(bodies[1]):
            errs.append(f'{gid}: H3 首镜 Shot 1 不写切点时间戳(官方:first shot has no timestamp)')
        prev = 0.0
        for k in range(2, n + 1):
            m = CUT_RE.search(bodies[k])
            if not m:
                errs.append(f'{gid}: Shot {k} 缺 H3 切点 `At MM:SS.mmm,`(应为 {cut_tag(cums[k - 1])})')
                continue
            t = int(m.group(1)) * 60 + int(m.group(2)) + int(m.group(3)) / 1000
            if t <= prev:
                errs.append(f'{gid}: Shot {k} 切点 {t:.3f}s 未严格递增(上一切点 {prev:.3f}s)')
            if abs(t - cums[k - 1]) > CUM_TOL:
                errs.append(f'{gid}: Shot {k} 切点 {t:.3f}s ≠ 前 {k - 1} 镜累计 {cums[k - 1]:.3f}s(shot_list duration_s)')
            if t >= float(total or 0) - 1e-9:
                errs.append(f'{gid}: Shot {k} 切点 {t:.3f}s 不小于组总时长 {total}s')
            prev = t
        return errs, warns
    # Seedance 2.5 / Wan 3.0:时间段
    found = []
    for k in range(1, n + 1):
        ms = list(TAG_RE.finditer(bodies[k]))
        if len(ms) > 1:
            errs.append(f'{gid}: Shot {k} 段有 {len(ms)} 个时间段标签,只能一个')
        if ms:
            m = ms[0]
            if bodies[k][:m.start()].strip():
                warns.append(f'{gid}: Shot {k} 时间段标签未紧跟段头(前有「{bodies[k][:m.start()].strip()[:20]}…」;跑 code/sync_shot_timing.py --write 复位)')
            found.append((k, int(m.group(1)), int(m.group(2)), m.group(3), m.group(4), m.group(5)))
    if not found:
        exp = '；'.join(segment_tag(s, e, ids[0] + 1, ids[-1] + 1, True).rstrip('：') for s, e, ids in plan_segments(durs, total))
        errs.append(f'{gid}: {KIND_LABEL[kind]} 正文无时间段标签(应写 {exp};跑 code/sync_shot_timing.py --write 写入)')
        return errs, warns
    units = {f[3] for f in found}
    if len(units) > 1:
        warns.append(f'{gid}: 时间段单位混用 {sorted(units)}')
    for i, (k, s, e, _u, a, b) in enumerate(found):
        nxt = found[i + 1][0] if i + 1 < len(found) else n + 1
        covered = list(range(k, nxt))
        if i == 0:
            if k != 1:
                errs.append(f'{gid}: 首段标签在 Shot {k},应从 Shot 1 起')
            if s != 0:
                errs.append(f'{gid}: 首段 {s}-{e} 未从 0 秒起')
        else:
            if s != found[i - 1][2]:
                errs.append(f'{gid}: Shot {k} 段 {s}-{e} 与上一段 {found[i - 1][1]}-{found[i - 1][2]} 不相接(须连续不重叠)')
        if e <= s:
            errs.append(f'{gid}: Shot {k} 段 {s}-{e} 长度须 ≥1 秒')
        if i == len(found) - 1 and e != T:
            errs.append(f'{gid}: 末段 {s}-{e} 未止于组总时长 {T} 秒(total_duration_s={total})')
        if a is not None or b is not None:
            if int(a) != k or int(b) != covered[-1]:
                errs.append(f'{gid}: Shot {k} 段合并范围写 Shot {a}–Shot {b},按标签分布应为 Shot {k}–Shot {covered[-1]}')
        elif len(covered) > 1:
            errs.append(f'{gid}: Shot {k} 段覆盖 Shot {k}–Shot {covered[-1]} 但未标合并范围(写 `{s}-{e}秒（Shot {k}–Shot {covered[-1]}）：`)')
        want = sum(durs[j - 1] for j in covered)
        if abs((e - s) - want) > SEG_LEN_TOL:
            errs.append(f'{gid}: Shot {k} 段 {s}-{e}({e - s}s)与覆盖各镜 duration_s 之和 {round(want, 2)}s 相差超过 {SEG_LEN_TOL}s')
    return errs, warns


# ---------------------------------------------------------------- 按集同步
def _group_durations(source: dict, gid: str):
    raw = next((g for g in source.get('generation_groups', []) if g.get('group_id') == gid), None)
    if not raw:
        return None, [], None
    shots = {s.get('shot_id'): s for s in source.get('shots', [])}
    durs = [float((shots.get(sid) or {}).get('duration_s') or 0) for sid in raw.get('shots', [])]
    return raw, durs, raw.get('total_duration_s')


def sync_group(base: Path, ep: str, gid: str, write: bool, source: dict, zh=None) -> dict:
    ep, gid = component(ep), component(gid)
    pp = base / 'assets' / 'prompts' / ep / f'{gid}.json'
    result = {'group_id': gid, 'kind': '', 'updated': False, 'errors': [], 'warnings': [], 'skipped': ''}
    raw, durs, total = _group_durations(source, gid)
    if raw is None:
        result['errors'].append(f'{gid}: shot_list 无此组')
        return result
    if not pp.is_file():
        result['skipped'] = '尚无组 prompt'
        return result
    prompt = read(pp, {}) or {}
    vp = str(prompt.get('video_prompt') or '')
    kind = group_kind(base, ep, gid)
    result['kind'] = kind
    if not kind:
        result['skipped'] = '生效视频模型未知(非 Seedance 2.x / MiniMax H3 / Wan 3.0),不写镜次时长'
        return result
    if total is None:
        result['errors'].append(f'{gid}: shot_list 组缺 total_duration_s')
        return result
    if write:
        zh = ui_lang_is_zh() if zh is None else zh
        new_vp = apply_prompt(vp, durs, total, kind, zh)
        if new_vp != vp:
            from modules.prompt_layout import paragraphize
            updated = dict(prompt)
            updated['video_prompt'] = paragraphize(new_vp)
            backup = base / 'directing' / ep / 'whitebox' / 'prompt_backups' / pp.name
            backup.parent.mkdir(parents=True, exist_ok=True)
            if not backup.exists():
                backup.write_bytes(pp.read_bytes())
            tmp = pp.with_suffix('.json.tmp')
            tmp.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            os.replace(tmp, pp)
            result['updated'] = True
            vp = updated['video_prompt']
    e, w = check_prompt(vp, durs, total, kind, gid)
    result['errors'].extend(e)
    result['warnings'].extend(w)
    if kind == KIND_SD20 and not e:
        result['skipped'] = 'Seedance 2.0 不写时间段(官方:精确时间支持不稳定)'
    return result


def sync_episode(base: Path, ep: str, groups=None, write: bool = False, zh=None) -> dict:
    ep = component(ep)
    source = read(base / 'directing' / ep / 'shot_list.json', {}) or {}
    ids = [g['group_id'] for g in source.get('generation_groups', []) if g.get('group_id')]
    unknown = sorted(set(groups or []) - set(ids))
    if groups:
        want = set(groups)
        ids = [g for g in ids if g in want]
    rows = [sync_group(base, ep, gid, write, source, zh) for gid in ids]
    errors = [f'{g}: unknown group' for g in unknown] + [e for r in rows for e in r['errors']]
    warnings = [w for r in rows for w in r['warnings']]
    return {'groups': rows, 'updated_prompts': [r['group_id'] for r in rows if r['updated']],
            'errors': errors, 'warnings': warnings}
