"""Scene-instance cast shared by staging, reference preparation and previews.

Shot characters describe narrative subjects, not everyone occupying the space.
Never join different scene numbers merely because they reuse a location asset.

白模项目(output.spatial_blocking 开)且本组已编译进 directing/<ep>/whitebox/episode.json 时,
只有在本组白模摄影机视频里实际出现的人物/生物才关联参考图(modules.whitebox_refs.appearing_cast,2026-09-09):
其余同场次人物记 whitebox_hidden、不补图,--write 时把已挂的图移出 refs 并重排 [Image N]。
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from modules.entity_ids import is_creature_id
from modules.prompt_layout import paragraphize


def read_json(path, default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else copy.deepcopy(default)


def scene_cast_groups(source):
    shots = {s['shot_id']: s for s in source.get('shots', [])}
    table = source.get('scene_table') or []
    if isinstance(table, dict):
        table = list(table.values())
    table = [row for row in table if isinstance(row, dict)]
    result, pools = {}, {}
    for group in source.get('generation_groups', []):
        gid = group['group_id']
        members = [shots[s] for s in group.get('shots', []) if s in shots]
        numbers = {s.get('scene_no') for s in members if s.get('scene_no')}
        number = group.get('scene_no') or (next(iter(numbers)) if len(numbers) == 1 else None)
        # Legacy groups without a scene number remain isolated, not location-wide.
        key = (number or f'group:{gid}', group.get('scene_id'))
        ids = set(group.get('characters_union') or []) | set(group.get('creatures_union') or [])
        if group.get('scene_cast_source') != 'scene_cast.v1':
            ids.update(group.get('scene_cast') or [])
        for row in table:
            locations = row.get('scene_ids') or [row.get('scene_id')]
            if number and row.get('scene_no') == number and locations == [group.get('scene_id')]:
                ids.update(row.get('characters') or [])
                ids.update(row.get('creatures') or [])
                ids.update(row.get('cast') or [])
        for shot in members:
            ids.update(shot.get('characters') or [])
            ids.update(shot.get('creatures') or [])
        for route in (group.get('blocking_map') or {}).get('characters', []):
            ids.add(route['id'])
            if route.get('mounted'):
                ids.add(route['mounted'])
        ids = {i for i in ids if isinstance(i, str) and re.fullmatch(r'(CHAR|CRE)-[A-Za-z0-9_-]+', i)}
        pools.setdefault(key, set()).update(ids)
        result[gid] = {'key': key, 'scene_no': number, 'scene_id': group.get('scene_id')}
    for entry in result.values():
        entry['actor_ids'] = sorted(pools[entry['key']])
    # A scene-wide identity pool is not a timeline of physical occupancy.
    # Explicit departures persist within this scene instance until re-entry is
    # declared. A present declaration clears the departure, but must not keep
    # forcing visibility after a later keyframed exit.
    departures = {}
    for group in source.get('generation_groups', []):
        entry = result[group['group_id']]
        states = departures.setdefault(entry['key'], {})
        declared = {}
        for cid, presence in (group.get('scene_presence') or {}).items():
            if (cid not in entry['actor_ids'] or not isinstance(presence, dict)
                    or presence.get('state') not in ('absent', 'remote', 'present')
                    or not presence.get('reason')):
                continue  # The whitebox compiler reports malformed declarations.
            declared[cid] = copy.deepcopy(presence)
            if presence['state'] == 'present':
                states.pop(cid, None)
            else:
                states[cid] = copy.deepcopy(presence)
        entry['presence'] = {**copy.deepcopy(states), **declared}
    return result


def scene_reference_rows(base: Path, ep: str, source, contexts=None):
    """Associate each participant with an existing, scene-appropriate reference.

    Reuse the current group's choice, then a same-scene group's choice. Costume
    ledgers supply an explicit costume choice; never silently substitute another
    costume when the requested sheet is missing.
    """
    contexts = contexts or scene_cast_groups(source)
    groups = source.get('generation_groups', [])
    prompts = {g['group_id']: read_json(base/'assets/prompts'/ep/f"{g['group_id']}.json", {}) for g in groups}
    names = {}
    for kind, key in [('characters', 'characters'), ('creatures', 'creatures')]:
        for row in read_json(base/'bible'/kind/'index.json', {}).get(key, []):
            names[row['id']] = row.get('canonical_name') or row.get('name') or row['id']
    result = {}
    wardrobe = None   # bible/costumes.json 归一结果,首次需要时读
    for index, group in enumerate(groups):
        gid = group['group_id']; context = contexts[gid]
        peers = sorted((i for i, g in enumerate(groups) if contexts[g['group_id']]['key'] == context['key']),
                       key=lambda i: (i != index, abs(i-index), i > index))
        rows = []
        cast = whitebox_cast(base, ep, gid)
        for cid in context['actor_ids']:
            if cast is not None and cid not in cast['visible']:
                rows.append({'id': cid, 'name': names.get(cid, cid), 'ref': None, 'costume': None, 'missing': False,
                             'whitebox_hidden': cast['hidden'].get(cid) or '不在本组白模人物列表'})
                continue
            if context.get('presence', {}).get(cid, {}).get('state') == 'absent':
                continue
            kind = 'creatures' if is_creature_id(cid) else 'characters'
            prefix = f'assets/concepts/{kind}/{cid}/'
            costume = None
            for i in peers:
                peer = groups[i]; pd = prompts[peer['group_id']]
                costume = (peer.get('costumes_by_char') or pd.get('costume_by_char') or {}).get(cid)
                if costume:
                    break
            candidates = []
            if costume and kind == 'characters':
                ledger = read_json(base/prefix/'costume_sheets.json', {})
                sheet = next((r for r in ledger.get('sheets', []) if r.get('costume_ref') == costume), {})
                if sheet.get('file'):
                    candidates.append(prefix+sheet['file'])
                # #59:默认装(或该角色只有这一套)按 SOUL 取 sheet.png;台账未命中时回落。非默认装缺图照报缺
                if wardrobe is None:
                    wardrobe = costume_wardrobe(base)
                if is_default_costume(wardrobe, cid, costume):
                    candidates.append(prefix+'sheet.png')
            else:
                for i in peers:
                    candidates.extend(r for r in prompts[groups[i]['group_id']].get('refs', [])
                                      if isinstance(r, str) and r.startswith(prefix) and '/candidates/' not in r)
                candidates.append(prefix+'sheet.png')
            ref = next((r for r in candidates if (base/r).is_file()), None)
            rows.append({'id': cid, 'name': names.get(cid, cid), 'ref': ref, 'costume': costume,
                         'missing': ref is None})
        result[gid] = rows
    return result


_ACTOR_ID_RE = re.compile(r'(CHAR|CRE)-[A-Za-z0-9_-]+')
_COSTUME_LIST_KEYS = {'costumes', 'outfits', 'entries', 'wardrobe', 'looks'}
_DEFAULT_PTR_KEYS = ('default_outfit_id', 'default_outfit', 'default_costume', 'default_costume_id')
_TRUE_WORDS = {'true', 'yes', 'y', '1', 'default', '是'}


def _is_actor_id(v):
    return isinstance(v, str) and bool(_ACTOR_ID_RE.fullmatch(v))


def _truthy(v):
    return v is True or (isinstance(v, (int, float)) and not isinstance(v, bool) and v == 1) \
        or (isinstance(v, str) and v.strip().lower() in _TRUE_WORDS)


def costume_wardrobe(base: Path) -> dict:
    """bible/costumes.json → {角色 id: {'costumes': {套装 id}, 'defaults': {默认套装 id}}}。

    各项目结构不一(顶层 costumes[] 带 character/character_ref/character_id;characters[] 内嵌 outfits[]
    + default_outfit(_id);character_bindings[] 的 default 为套装 id;dict 以 CHAR-/COS- 为键…),
    这里宽容遍历:归属取自身或最近祖先的角色字段,套装只认 costumes/outfits/entries 等列表(或以 id 为键的字典)
    里带 id 的条目,默认标记认 default/is_default 真值与 default_* 指针。读不到/解析失败返回 {}。"""
    try:
        doc = json.loads((base/'bible'/'costumes.json').read_text(encoding='utf-8'))
    except Exception:
        return {}
    out = {}

    def slot(owner):
        return out.setdefault(owner, {'costumes': set(), 'defaults': set()})

    def owner_of(d, parent_key):
        for k in ('character_id', 'character_ref', 'character', 'char_id', 'owner'):
            if _is_actor_id(d.get(k)):
                return d[k]
        # 套装条目的 id 常以角色编号打头(CHAR-0001_daily_01),套装列表里的 id 不当角色
        if parent_key not in _COSTUME_LIST_KEYS and _is_actor_id(d.get('id')):
            return d['id']
        return None

    def walk(node, owner, parent_key=None, implicit_id=None):
        if isinstance(node, list):
            for item in node:
                walk(item, owner, parent_key)
            return
        if not isinstance(node, dict):
            return
        own = owner_of(node, parent_key) or owner
        cos_id = node.get('id') or node.get('outfit_id') or node.get('costume_id') or implicit_id
        if own and parent_key in _COSTUME_LIST_KEYS and isinstance(cos_id, str) and cos_id and cos_id != own:
            slot(own)['costumes'].add(cos_id)
            if _truthy(node.get('default')) or _truthy(node.get('is_default')):
                slot(own)['defaults'].add(cos_id)
        if own:
            for k in _DEFAULT_PTR_KEYS:
                v = node.get(k)
                v = v.get('id') if isinstance(v, dict) else v
                if isinstance(v, str) and v.strip():
                    slot(own)['defaults'].add(v.strip())
            # character_bindings 式:{character_id, default: "<套装 id>"}
            v = node.get('default')
            if isinstance(v, str) and v.strip() and not _truthy(v) and v.strip().lower() not in ('false', 'no'):
                slot(own)['defaults'].add(v.strip())
        for k, v in node.items():
            if _is_actor_id(k) and isinstance(v, (dict, list)):
                walk(v, k, k)
            elif k in _COSTUME_LIST_KEYS and isinstance(v, dict) and all(isinstance(x, dict) for x in v.values()):
                for kk, vv in v.items():
                    walk(vv, own, k, kk)
            elif isinstance(v, (dict, list)):
                walk(v, own, k)

    walk(doc, None)
    return out


def is_default_costume(wardrobe: dict, cid: str, costume: str) -> bool:
    """该套装是否为该角色默认装:标了默认,或该角色在 costumes.json 里只登记了这一套。"""
    entry = (wardrobe or {}).get(cid) or {}
    return costume in entry.get('defaults', ()) or entry.get('costumes') == {costume}


def whitebox_cast(base: Path, ep: str, gid: str):
    """本组白模实际出现人物(None=项目未开白模链/本组未编译,不限制)。延迟导入避免 whitebox→scene_cast 循环。"""
    from modules.whitebox_refs import cast_filter
    try:
        return cast_filter(base, ep, gid)
    except (ValueError, KeyError, TypeError):
        return None


CAST_BEGIN = '\nScene presence references:\n'
CAST_END = '\nEnd scene presence references.\n'
CAST_BLOCK_RE = re.compile(r'\n?' + re.escape(CAST_BEGIN.strip()) + r'\n.*?' + re.escape(CAST_END.strip()) + r'\n?', re.S)


def block_anchor(text, *later_keys):
    """本段应插入的位置:后置固定段(默认 Whitebox reference / Shot plates)与 `Shot 1:` 中最靠前者;都没有时为文首。"""
    keys = list(later_keys) or ['Whitebox reference:', 'Shot plates:']
    positions = [text.index(k) for k in keys if k in text]
    match = re.search(r'Shot\s+1\s*:', text)
    if match:
        positions.append(match.start())
    return min(positions) if positions else 0


def complete_prompt_cast(prompt, rows, presence=None):
    """Append references without renumbering existing Image bindings. Pure/idempotent."""
    out = copy.deepcopy(prompt)
    text = out.get('video_prompt') or ''
    # 段首尾换行可能被其他 sync(白模段插入时 rstrip)吃掉,按标题文本匹配以免旧段残留、重复堆叠
    text = CAST_BLOCK_RE.sub('', text)
    out['video_prompt'] = text
    # 白模未出现的人物:已挂的参考图移出 refs 并重排序号(正文其他地方仍引用时保留,由 check_prompt_cast 报错)
    hidden = {row['id'] for row in rows if row.get('whitebox_hidden')}
    if hidden:
        from modules.whitebox_refs import cast_ref_id, strip_cast_refs
        drop = [r for r in (out.get('refs') or []) if cast_ref_id(r) in hidden]
        if drop:
            out, _ = strip_cast_refs(out, drop)
            text = out.get('video_prompt') or ''
    refs = out.setdefault('refs', [])
    bindings = []
    for row in rows:
        ref = row['ref']
        if not ref:
            continue
        if ref not in refs:
            refs.append(ref)
        n = refs.index(ref)+1
        bindings.append(f"{row['name']}@Image {n}: {row['id']}，本场次人物的身份与服装参考。")
    # Old group-wide counts incorrectly force off-camera occupants to disappear.
    text = re.sub(r'(?:Identity lock:\s*)?exactly\s+\d+\s+(?:named\s+)?characters?\s+(?:on screen|across the video|in this group)[^.。]*(?:[.。]|$)',
                  'Identity lock: every depicted registered character must match their own reference; '
                  'no duplicate or twin characters; visibility follows each shot’s framing.', text, flags=re.I)
    text = re.sub(r'there are exactly\s+\d+\s+distinct characters across the whole video,?\s*', '', text, flags=re.I)
    text = re.sub(r'no extra, third, or duplicate person', 'no duplicate person', text, flags=re.I)
    excluded = []
    for cid, p in (presence or {}).items():
        if p.get('state') not in ('absent', 'remote'):
            continue
        retained = [f'[Image {i}]' for i, ref in enumerate(refs, 1)
                    if isinstance(ref, str) and cid in ref.split('/')]
        reference_note = ('（保留的 '+'、'.join(retained)+' 仅供身份核对，不要求出场）') if retained else ''
        excluded.append(f"{cid}：{p['reason']}"+reference_note)
    if bindings or excluded:
        block = CAST_BEGIN+' '.join(bindings)+'\n同场人物保持既定位置与进退场连续性；'+\
                '镜头外人物仍在场，不要求每镜全部入画。仅按原台词安排说话，其余人物保持沉默。'
        if excluded:
            block += '\n本组不在场，不得生成实体人物：'+'；'.join(excluded)+'。已有身份参考不代表人物在场。'
        block += CAST_END
        # Definitions precede shot instructions, while existing reference indices stay stable.
        # 固定段规范顺序:Scene presence → Whitebox reference → Shot plates → Shot 1(各 sync 各自锚定,重跑不互换位置)
        text = text[:block_anchor(text)]+block+text[block_anchor(text):]
    out['video_prompt'] = paragraphize(text)
    out['scene_cast'] = [r['id'] for r in rows]
    out['scene_cast_refs'] = copy.deepcopy(rows)
    if presence is not None:
        out['scene_presence'] = copy.deepcopy(presence)
    return out


def check_prompt_cast(prompt, rows, presence=None):
    errors = []
    for row in rows:
        if row.get('whitebox_hidden'):
            stale = [r for r in (prompt.get('refs') or []) if isinstance(r, str) and row['id'] in r.split('/')]
            for r in stale:
                errors.append(f"whitebox_cast_ref: {row['id']} 未在本组白模出现({row['whitebox_hidden']}),其参考图 {r} 不得进 refs;"
                              "删去正文对该图的引用后运行 sync_scene_cast.py --write 移出")
            continue
        if row['missing']:
            errors.append(f"scene_cast_ref: {row['id']} 缺少本场次身份/服装参考图")
        elif row['ref'] not in (prompt.get('refs') or []):
            errors.append(f"scene_cast_ref: 未关联在场人物 {row['id']} 的参考图 {row['ref']}")
    for cid, state in (presence or {}).items():
        if state.get('state') not in ('absent', 'remote'):
            continue
        recorded = (prompt.get('scene_presence') or {}).get(cid, {})
        if recorded.get('state') != state['state']:
            errors.append(f"scene_presence: {cid} 的 {state['state']} 状态未同步到 prompt，请运行 sync_scene_cast.py --write")
    return errors
