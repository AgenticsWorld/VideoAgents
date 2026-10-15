"""逐字注入片段的批注污染检测(prose_clean,2026-09-29)。

blocking `space_fragment_en` 与 shot_list `blocking_map.characters[].route_en` 会被 prompt 工位**逐字**拼进
video_prompt。它们只能是画面散文(谁在哪、朝哪、怎么走);裁决出处、白模数值、时间码一律不得写进去——
前科 fengshen3 ep07:白模调度工位套用导演裁决时把「导演裁决A：…[1.9,0,-0.3375]…28.2秒visible:false」
追加进这两个字段(grp009 还重复追加两遍),9 组 prompt 被逐字污染,可能被画成画面文字、打乱动作时序。
裁决出处/说明写 blocking.json `director_decisions` / `whitebox_staging.review`、计划 issues[].applied.note;
数值写 keyframes / whitebox_contract;时序写 beats[].t。
"""
import re

from modules.entity_ids import ACTOR_ID_PAT

# (标签, 严重度, 正则):error = 必须清理(prompt 机检 FAIL);warn = 提示
_RULES = [
    ('裁决/修订批注', 'error', re.compile(r'裁决|暂案|审看|本批|WBI-|(?<![A-Za-z])issue|用户(?:白模)?(?:修正|修订|纠正|要求|裁定)|按用户|导演台', re.I)),
    ('选项引用', 'error', re.compile(r'(?<![A-Za-z0-9])\d{3}-[A-F](?![A-Za-z])|选[A-F](?![A-Za-z])|方案[A-F](?![A-Za-z])')),
    ('白模/关键帧术语', 'error', re.compile(r'白模|关键帧|keyframe|whitebox|visible\s*[:=]|yaw\s*[:=]|pitch\s*[:=]|fov\s*[:=]', re.I)),
    ('内部编号', 'error', re.compile(r'(?<![A-Za-z])(?:grp|sh)\d{3,}|(?<![A-Za-z])(?:' + ACTOR_ID_PAT + r'|(?:PROP|SCN)-\d+)|(?<![A-Za-z])N-\d{4}|20\d\d-\d\d-\d\d')),
    ('坐标', 'error', re.compile(r'[\[(（]\s*-?\d+(?:\.\d+)?\s*[,，]\s*-?\d+(?:\.\d+)?(?:\s*[,，]\s*-?\d+(?:\.\d+)?)?\s*[\])）]')),
    ('时间码', 'error', re.compile(r'组内\s*\d|镜内\s*\d|\d+(?:\.\d+)?\s*[-–~～至到]\s*\d+(?:\.\d+)?\s*秒|\d+\.\d+\s*(?:秒|s\b)|\d+\s*秒(?:后|起|时|前|内|末|处)|\bt\s*=\s*\d')),
    ('小数米数', 'warn', re.compile(r'\d+\.\d+\s*(?:米|m\b)')),
]


def annotation_hits(text, include_warn=False):
    """返回命中的污染类别及片段 [(标签, 命中文本)];空列表 = 干净。默认只报 error 级。"""
    if not isinstance(text, str) or not text:
        return []
    hits = []
    for label, level, rx in _RULES:
        if level == 'warn' and not include_warn:
            continue
        m = rx.search(text)
        if m:
            hits.append((label, m.group(0)))
    # 同一长句重复追加(同一裁决回写两次)
    sentences = [s.strip() for s in re.split(r'[。;；\n]', text) if len(s.strip()) >= 12]
    dup = next((s for i, s in enumerate(sentences) if s in sentences[:i]), None)
    if dup:
        hits.append(('重复句', dup[:30]))
    return hits


# 整段 video_prompt 正文只查强标记(正文合法含「白模外观」、Shot 段「0-1秒：」时间头、宿主 Whitebox 固定段)
_PROMPT_RULES = [
    ('裁决/修订批注', re.compile(r'裁决|暂案|WBI-|用户(?:白模)?(?:修正|修订|纠正)|白模审看|白模修订')),
    ('白模数值', re.compile(r'visible\s*[:=]\s*(?:false|true)|yaw\s*[:=]\s*-?\d|pitch\s*[:=]\s*-?\d', re.I)),
    ('坐标', _RULES[4][2]),
]
_HOST_BLOCK_RE = re.compile(r'Whitebox (?:reference|legend|facing):.*?(?=\n\n|$)', re.S)


def prompt_annotation_hits(video_prompt):
    """video_prompt 正文里的批注残留 [(标签, 命中文本, 上下文)](剔除宿主 Whitebox 固定段)。"""
    body = _HOST_BLOCK_RE.sub('', video_prompt or '')
    out = []
    for label, rx in _PROMPT_RULES:
        for m in rx.finditer(body):
            out.append((label, m.group(0), body[max(0, m.start() - 20):m.end() + 20].replace('\n', ' ')))
    return out


def describe(hits):
    return '、'.join(f'{label}「{frag}」' for label, frag in hits)


def scan_episode(base, ep):
    """扫一集 shot_list route_en + 各镜 blocking space_fragment_en。返回 [{group_id, where, field, hits, text}]。"""
    import json
    from pathlib import Path
    root = Path(base) / 'directing' / ep
    try:
        sl = json.loads((root / 'shot_list.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    out = []
    shot_group = {}
    for g in sl.get('generation_groups') or []:
        gid = g.get('group_id', '?')
        for sid in g.get('shots') or []:
            shot_group[sid if isinstance(sid, str) else (sid or {}).get('shot_id')] = gid
        rows = ((g.get('blocking_map') or {}).get('characters') or []) + ((g.get('blocking_map') or {}).get('creatures') or [])
        for r in rows:
            if not isinstance(r, dict):
                continue
            hits = annotation_hits(r.get('route_en'))
            if hits:
                out.append({'group_id': gid, 'where': f"shot_list blocking_map {r.get('id', '?')}", 'field': 'route_en',
                            'hits': hits, 'text': r.get('route_en')})
    for s in sl.get('shots') or []:
        if s.get('group_id') and s.get('shot_id'):
            shot_group.setdefault(s['shot_id'], s['group_id'])
    for bf in sorted((root / 'shots').glob('*/blocking.json')):
        try:
            b = json.loads(bf.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        sid = bf.parent.name
        gid = b.get('group_id') or shot_group.get(sid, '?')
        for ch in b.get('characters') or []:
            if not isinstance(ch, dict):
                continue
            hits = annotation_hits(ch.get('space_fragment_en'))
            if hits:
                out.append({'group_id': gid, 'where': f"shots/{sid}/blocking.json {ch.get('id', '?')}",
                            'field': 'space_fragment_en', 'hits': hits, 'text': ch.get('space_fragment_en')})
    return out
