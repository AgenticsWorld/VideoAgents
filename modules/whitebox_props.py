"""剧情道具挂手占位(2026-11-05,docs/whitebox.md「道具占位:code/whitebox_props.py」)。

白模计划漏建剧情道具(prop_coverage.missing)时,按道具库 bible/props.json 给「主人在本组、且主人有手部通道」的道具
生成一个挂在主人手上的简单几何体占位(attach),尺寸取 scale.canonical_size 首个分句里的长度,颜色取 palette/visual
里第一个颜色词,形状按长宽比取圆柱/方块。白模调度只需再按镜头表 content 写脱手、运动、回手的关键帧。

只生成、不自动套用:编译器若自动插入道具,会改变已签字导出组的视频指纹(整批显示过期);所以由 Agent 在调度时
显式调用 `--write` 写进计划(先备份),之后照常复核取景、重打机位审查指纹。没有主人、主人不在本组或没有手部通道的道具
只列出原因,留给 Agent 手工建模或写 not_modeled。
"""
from __future__ import annotations

import copy
import json
import os
import re
import time
from pathlib import Path

from modules.whitebox import HAND_SIDES, bible_props, component, read

SOURCE = 'whitebox_props.v1'
UNITS = {'mm': .001, '毫米': .001, 'cm': .01, '厘米': .01, 'm': 1.0, '米': 1.0}
LENGTH_RE = re.compile(r'(\d+(?:\.\d+)?)\s*(?:[x×*]\s*(\d+(?:\.\d+)?)\s*)?(?:[x×*]\s*(\d+(?:\.\d+)?)\s*)?(mm|cm|m|毫米|厘米|米)(?![A-Za-z])')
# 颜色词 → 色值:按在文字里出现的先后取第一个(先写的通常是主体色);长词在前,避免「薄荷绿」先命中「绿」
COLOR_WORDS = (
    ('薄荷绿', '#a8e6cf'), ('金褐', '#c8a165'), ('原木', '#c8a165'), ('赭红', '#c0603a'), ('土红', '#c0603a'),
    ('银灰', '#a7b1b8'), ('乳白', '#f3f0e8'), ('奶白', '#f3f0e8'), ('米白', '#f3f0e8'),
    ('正红', '#d62828'), ('鲜红', '#d62828'), ('橙', '#f28c28'), ('金', '#d4a537'), ('黄', '#f2d14b'),
    ('绿', '#2e8b3e'), ('青', '#0d9488'), ('蓝', '#1d78d8'), ('紫', '#8e44ad'), ('粉', '#f48fb1'),
    ('褐', '#795548'), ('棕', '#795548'), ('红', '#d62828'), ('灰', '#9e9e9e'), ('白', '#f3f0e8'), ('黑', '#1a1a1a'),
    ('mint', '#a8e6cf'), ('golden', '#d4a537'), ('gold', '#d4a537'), ('silver', '#a7b1b8'), ('brass', '#c9a227'),
    ('orange', '#f28c28'), ('yellow', '#f2d14b'), ('green', '#2e8b3e'), ('blue', '#1d78d8'), ('purple', '#8e44ad'),
    ('pink', '#f48fb1'), ('brown', '#795548'), ('wooden', '#c8a165'), ('wood', '#c8a165'), ('red', '#d62828'),
    ('grey', '#9e9e9e'), ('gray', '#9e9e9e'), ('white', '#f3f0e8'), ('black', '#1a1a1a'), ('glass', '#bfe6ff'),
)
DEFAULT_COLOR = '#d2b48c'
STICK_MIN_M = .03        # 杆状占位最细 3 cm(画面可读性优先于实物粗细)


def bible_dims(prop: dict) -> list:
    """道具库尺寸文字里的长度(米,降序):先取 scale.canonical_size 首个分句(「;」前,后面多是配件/对比物),没有再看全文。"""
    text = str((prop.get('scale') or {}).get('canonical_size') or prop.get('size') or '')
    for part in (re.split(r'[;；]', text)[0], text):
        values = []
        for m in LENGTH_RE.finditer(part):
            values += [float(x) * UNITS[m.group(4)] for x in m.groups()[:3] if x]
        values = sorted((v for v in values if v >= .003), reverse=True)
        if values:
            return values
    return []


def placeholder_geometry(dims: list) -> tuple:
    """(shape, size_m),最长边朝上:一个尺寸 → 方块;两个 → 圆柱(直径取较小者);三个 → 最长/次长 > 4 为杆状圆柱,否则方块;
    更多(多部件描述,如「全长 95 cm,圈径 35 cm,柄粗 2 cm…」)→ 最长/最短 > 10 为杆状圆柱,否则方块;没有 → 0.1 m 方块。
    杆状占位粗细至少 STICK_MIN_M:按实际 1–2 cm 粗,中景画面里只有一两个像素,参考视频里认不出手里有东西。"""
    if not dims:
        return 'box', [.1, .1, .1]
    clamp = lambda v, low=.01: round(max(v, low), 4)
    longest = round(dims[0], 4)
    if len(dims) == 1:
        return 'box', [round(longest * .6, 4), longest, round(longest * .6, 4)]
    if len(dims) == 2:
        low = STICK_MIN_M if dims[0] / dims[1] > 4 else .01
        return 'cylinder', [clamp(dims[1], low), longest, clamp(dims[1], low)]
    if len(dims) == 3:
        if dims[0] / dims[1] > 4:
            return 'cylinder', [clamp(dims[1], STICK_MIN_M), longest, clamp(dims[1], STICK_MIN_M)]
        return 'box', [clamp(dims[1]), longest, clamp(dims[2])]
    if dims[0] / dims[-1] > 10:
        return 'cylinder', [clamp(dims[-1], STICK_MIN_M), longest, clamp(dims[-1], STICK_MIN_M)]
    return 'box', [clamp(dims[1]), longest, clamp(dims[-1])]


def bible_color(prop: dict) -> str:
    for text in (prop.get('palette'), prop.get('visual'), prop.get('name_en'), prop.get('name')):
        text = str(text or '')
        low = text.lower()
        hits = []
        for word, color in COLOR_WORDS:
            # 英文词按整词匹配(red 不能命中 covered);中文词直接找
            m = re.search(r'\b%s\b' % word, low) if word.isascii() else (re.search(re.escape(word), low))
            if m:
                hits.append((m.start(), -len(word), color))
        if hits:
            return min(hits)[2]
    return DEFAULT_COLOR


def preferred_hand(prop: dict, owner_id: str) -> str:
    text = ' '.join([str((prop.get('scale') or {}).get('relative_anchor') or '')]
                    + [str(o.get('how') or '') for o in prop.get('owners') or [] if o.get('character_id') == owner_id]).lower()
    if '左手' in text or 'left hand' in text:
        return 'left_hand'
    return 'right_hand'


def placeholder_for(pid: str, entry: dict, group: dict) -> tuple:
    """(占位道具 dict, None) 或 (None, 不能生成的原因)。"""
    if not entry:
        return None, '道具库 bible/props.json 里没有这个道具'
    owners = [o.get('character_id') for o in entry.get('owners') or [] if isinstance(o, dict) and o.get('character_id')]
    if not owners:
        return None, '道具库没有登记主人(owners),宿主不知道放在谁手上——按镜头表 content 手工建模,或写 not_modeled'
    people = {a['id']: a for a in list(group.get('actors') or []) + list(group.get('extras') or [])
              if a.get('kind', 'person') != 'creature'}
    holder = next((people[o] for o in owners if o in people), None)
    if not holder:
        return None, f"道具主人 {'/'.join(owners)} 不在本组白模人物里(或是生物)——按镜头表 content 手工建模,或写 not_modeled"
    channels = [name for name, _ in HAND_SIDES if any(isinstance(k.get(name), list) for k in holder.get('keyframes') or [])]
    hand = preferred_hand(entry, holder['id'])
    shape, size = placeholder_geometry(bible_dims(entry))
    lift = round(size[1] * (.4 if size[1] / max(min(size[0], size[2]), 1e-6) > 4 else .3), 4)
    prop = {'id': pid, 'ref': pid, 'label': entry.get('name') or pid, 'shape': shape, 'size_m': size,
            'color': bible_color(entry), 'placeholder': SOURCE}
    if channels:
        prop['attach'] = {'actor': holder['id'], 'hand': hand if hand in channels else channels[0], 'offset': [0, lift, .02]}
        return prop, None
    # 主人没有手部通道(渲染器不生成手臂,挂不上):退而求其次,在主人每个关键帧时刻把道具放在「手垂在身前」的位置,
    # 跟着人走、跟着转身;镜内转身时线性插值与人物的角度插值略有出入,属占位精度
    prop['keyframes'] = follow_keys(holder, group, hand, lift)
    prop['placeholder_mode'] = 'follow'
    return prop, None


def follow_keys(holder: dict, group: dict, hand: str, lift: float) -> list:
    from modules.whitebox import hand_anchor, sample
    width, height = (holder.get('size_m') or [.5, 1.7, .4])[:2]
    rest = [(-.55 if hand == 'right_hand' else .55) * width, .5 * height, .15]
    duration = float(group.get('duration_s') or holder['keyframes'][-1]['t'])
    times = sorted({0.0, duration, *(float(k['t']) for k in holder['keyframes'] if 0 <= float(k['t']) <= duration)})
    keys = []
    for t in times:
        key = sample(holder['keyframes'], t)
        point, _ = hand_anchor(holder, {**key, hand: rest}, hand)
        frame = {'t': round(t, 4), 'position': [round(point[0], 4), round(point[1] + lift, 4), round(point[2], 4)],
                 'yaw': round(float(key.get('yaw') or 0), 4)}
        if key.get('visible') is False:
            frame['visible'] = False
        keys.append(frame)
    return keys


def suggest(base: Path, ep: str, groups=None) -> dict:
    """按当前编译结果(不含导演台覆盖层)列出各组缺失剧情道具的占位方案。"""
    from modules.whitebox import compile_episode
    ep = component(ep)
    episode = compile_episode(base, ep, apply_overrides=False)
    bible = bible_props(base)
    wanted = set(groups or [])
    rows = []
    for group in episode['groups']:
        gid = group['group_id']
        if wanted and gid not in wanted:
            continue
        missing = (group.get('prop_coverage') or {}).get('missing') or []
        if not missing:
            continue
        added, skipped = [], []
        for pid in missing:
            prop, reason = placeholder_for(pid, bible.get(pid), group)
            (added.append(prop) if prop else skipped.append({'id': pid, 'name': (bible.get(pid) or {}).get('name') or '', 'reason': reason}))
        rows.append({'group_id': gid, 'added': added, 'skipped': skipped})
    errors = [e for e in episode['errors'] if not wanted or e['group_id'] in wanted]
    return {'groups': rows, 'errors': errors}


def write(base: Path, ep: str, rows: list) -> list:
    """把占位写进 directing/<ep>/whitebox_plans/<grp>.json(先备份到 whitebox/backups/props-<时间>/);返回写过的组。"""
    ep = component(ep)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    written = []
    for row in rows:
        if not row['added']:
            continue
        gid = component(row['group_id'])
        path = base / 'directing' / ep / 'whitebox_plans' / f'{gid}.json'
        plan = read(path, None)
        if not isinstance(plan, dict):
            continue
        backup = base / 'directing' / ep / 'whitebox' / 'backups' / f'props-{stamp}' / f'{gid}.json'
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(path.read_bytes())
        props = list(plan.get('props') or [])
        have = {p.get('id') for p in props if isinstance(p, dict)}
        props += [copy.deepcopy(p) for p in row['added'] if p['id'] not in have]
        plan['props'] = props
        tmp = path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(plan, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        os.replace(tmp, path)
        written.append({'group_id': gid, 'plan': f'directing/{ep}/whitebox_plans/{gid}.json',
                        'backup': str(backup.relative_to(base)), 'props': [p['id'] for p in row['added']]})
    return written
