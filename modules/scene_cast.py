"""Scene-instance cast shared by staging, reference preparation and previews.

Shot characters describe narrative subjects, not everyone occupying the space.
Never join different scene numbers merely because they reuse a location asset.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path


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
    for index, group in enumerate(groups):
        gid = group['group_id']; context = contexts[gid]
        peers = sorted((i for i, g in enumerate(groups) if contexts[g['group_id']]['key'] == context['key']),
                       key=lambda i: (i != index, abs(i-index), i > index))
        rows = []
        for cid in context['actor_ids']:
            kind = 'creatures' if cid.startswith('CRE-') else 'characters'
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


CAST_BEGIN = '\nScene presence references:\n'
CAST_END = '\nEnd scene presence references.\n'


def complete_prompt_cast(prompt, rows):
    """Append references without renumbering existing Image bindings. Pure/idempotent."""
    out = copy.deepcopy(prompt)
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
    text = out.get('video_prompt') or ''
    text = re.sub(re.escape(CAST_BEGIN)+r'.*?'+re.escape(CAST_END), '', text, flags=re.S)
    # Old group-wide counts incorrectly force off-camera occupants to disappear.
    text = re.sub(r'(?:Identity lock:\s*)?exactly\s+\d+\s+(?:named\s+)?characters?\s+(?:on screen|across the video|in this group)[^.。]*(?:[.。]|$)',
                  'Identity lock: every depicted registered character must match their own reference; '
                  'no duplicate or twin characters; visibility follows each shot’s framing.', text, flags=re.I)
    text = re.sub(r'there are exactly\s+\d+\s+distinct characters across the whole video,?\s*', '', text, flags=re.I)
    text = re.sub(r'no extra, third, or duplicate person', 'no duplicate person', text, flags=re.I)
    if bindings:
        block = CAST_BEGIN+' '.join(bindings)+'\n同场人物保持既定位置与进退场连续性；'+\
                '镜头外人物仍在场，不要求每镜全部入画。仅按原台词安排说话，其余人物保持沉默。'+CAST_END
        # Definitions precede shot instructions, while existing reference indices stay stable.
        match = re.search(r'Shot\s+1\s*:', text)
        at = match.start() if match else 0
        text = text[:at]+block+text[at:]
    out['video_prompt'] = text
    out['scene_cast'] = [r['id'] for r in rows]
    out['scene_cast_refs'] = copy.deepcopy(rows)
    return out


def check_prompt_cast(prompt, rows):
    errors = []
    for row in rows:
        if row['missing']:
            errors.append(f"scene_cast_ref: {row['id']} 缺少本场次身份/服装参考图")
        elif row['ref'] not in (prompt.get('refs') or []):
            errors.append(f"scene_cast_ref: 未关联在场人物 {row['id']} 的参考图 {row['ref']}")
    return errors
