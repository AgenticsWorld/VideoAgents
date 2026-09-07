#!/usr/bin/env python3
"""自动续接规划/准备。--write 允许前向引用；--prepare 在前组出片后截取并同步。"""
import json
import sys
import subprocess
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _common import parse_args
from modules.continuity_refs import sync_group
from modules.whitebox import read


def main():
    args, base = parse_args(__doc__, configure=lambda ap: (
        ap.add_argument('groups', nargs='*'),
        ap.add_argument('--write', action='store_true'),
        ap.add_argument('--prepare', action='store_true')))
    ids = [g['group_id'] for g in (read(base/'directing'/args.ep/'shot_list.json', {}) or {}).get('generation_groups', [])]
    errors = []
    for gid in args.groups or ids:
        try:
            if gid not in ids:
                raise ValueError(f'未知分镜组: {gid}')
            print(json.dumps(sync_group(base, args.ep, gid, args.write, args.prepare), ensure_ascii=False))
        except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as e:
            errors.append(f'{gid}: {e}')
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
