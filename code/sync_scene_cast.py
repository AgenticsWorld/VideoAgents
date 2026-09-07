#!/usr/bin/env python3
"""Prepare/check scene occupants and identity references before staging or generation."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _common import parse_args
from modules.scene_cast import scene_cast_groups, scene_reference_rows, complete_prompt_cast, check_prompt_cast, read_json


def sync(base, ep, selected=None, write=False):
    path = base/'directing'/ep/'shot_list.json'
    source = read_json(path, {})
    contexts = scene_cast_groups(source)
    rows = scene_reference_rows(base, ep, source, contexts)
    errors, changed, reports = [], [], []
    selected = set(selected or contexts)
    for gid in sorted(selected-set(contexts)):
        errors.append(f'{gid}: unknown group')
    for group in source.get('generation_groups', []):
        gid = group['group_id']
        if gid not in selected:
            continue
        group['scene_cast'] = contexts[gid]['actor_ids']
        group['scene_cast_source'] = 'scene_cast.v1'
        group['scene_cast_refs'] = rows[gid]
        pp = base/'assets/prompts'/ep/f'{gid}.json'
        prompt = read_json(pp, None)
        if prompt is not None:
            updated = complete_prompt_cast(prompt, rows[gid]) if write else prompt
            errors.extend(f'{gid}: {e}' for e in check_prompt_cast(updated, rows[gid]))
            if write and updated != prompt:
                # Preserve the original once for review/recovery; no generated media touched.
                backup = base/'directing'/ep/'scene_cast_backups'/'prompts'/pp.name
                backup.parent.mkdir(parents=True, exist_ok=True)
                if not backup.exists():
                    backup.write_bytes(pp.read_bytes())
                pp.write_text(json.dumps(updated, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
                changed.append(gid)
        else:
            errors.extend(f'{gid}: {r["id"]} missing scene reference' for r in rows[gid] if r['missing'])
        reports.append({'group_id': gid, 'scene_no': contexts[gid]['scene_no'], 'scene_id': group['scene_id'],
                        'scene_cast': contexts[gid]['actor_ids'], 'references': rows[gid], 'has_prompt': prompt is not None})
    if write:
        backup = base/'directing'/ep/'scene_cast_backups'/'shot_list.json'
        backup.parent.mkdir(parents=True, exist_ok=True)
        if not backup.exists():
            backup.write_bytes(path.read_bytes())
        path.write_text(json.dumps(source, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return {'groups': reports, 'updated_prompts': changed, 'errors': errors}


def main():
    args, base = parse_args(__doc__, configure=lambda p: (
        p.add_argument('groups', nargs='*'),
        p.add_argument('--write', action='store_true', help='Append missing references and save scene cast associations')))
    result = sync(base, args.ep, args.groups, args.write)
    output = base/'directing'/args.ep/'scene_cast_review.json'
    if args.write:
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'groups': len(result['groups']), 'updated_prompts': result['updated_prompts'], 'errors': result['errors']}, ensure_ascii=False))
    return bool(result['errors'])


if __name__ == '__main__':
    sys.exit(main())
