"""⑥ 用组 prompt(refs = 角色 sheet + 俯视图 + 九格图 + 尾帧 + 机位场景图,video_refs = 白模 camera.mp4)提交 Seedance。

用法:
  python code/pano/run_seedance.py --project offer --ep ep01 --gid grp005 [--suffix _pano_v2] [--resolution 1080p] [--dry-run]
读 assets/prompts/<ep>/<gid>.json 的 video_prompt / refs / video_refs / audio_refs,输出 assets/clips/<ep>/<gid><suffix>.mp4
与尾帧 <gid><suffix>_last.png;实际提交命令落盘到 assets/clips/<ep>/<gid><suffix>.cmd.json。
不改宿主 genmedia,只是把 modules/genmedia.py video 的参数拼好;上限校验(2.0 图 ≤9 / 参考视频 ≤3)由 genmedia 负责。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import ROOT, project_root  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--project', required=True)
    ap.add_argument('--ep', required=True)
    ap.add_argument('--gid', required=True)
    ap.add_argument('--suffix', default='_pano')
    ap.add_argument('--resolution', default='1080p')
    ap.add_argument('--aspect', default='16:9')
    ap.add_argument('--python', default=str(ROOT / '.venv' / 'bin' / 'python'))
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    proj = project_root(a.project)
    pf = proj / 'assets/prompts' / a.ep / f'{a.gid}.json'
    p = json.load(open(pf))
    missing = [r for r in p['refs'] + p.get('video_refs', []) + p.get('audio_refs', []) if not (proj / r).exists()]
    if missing:
        raise SystemExit('参考文件缺失:' + ', '.join(missing))
    out = f'assets/clips/{a.ep}/{a.gid}{a.suffix}.mp4'
    cmd = [a.python, str(ROOT / 'modules/genmedia.py'), 'video', '--prompt', p['video_prompt'], '--output', out,
           '--duration', str(int(p.get('total_duration_s') or 10)), '--resolution', a.resolution, '--aspect', a.aspect,
           '--group', a.gid, '--return-last-frame', out.replace('.mp4', '_last.png')]
    for r in p['refs']:
        cmd += ['--ref', r]
    for r in p.get('video_refs', []):
        cmd += ['--ref-video', r]
    for r in p.get('audio_refs', []):
        cmd += ['--audio-ref', r]
    (proj / out).parent.mkdir(parents=True, exist_ok=True)
    json.dump({'cwd': str(proj), 'cmd': cmd, 'prompt_file': str(pf.relative_to(proj)), 'attempt': p.get('attempt')},
              open(proj / out.replace('.mp4', '.cmd.json'), 'w'), ensure_ascii=False, indent=1)
    print(f'refs={len(p["refs"])} video_refs={len(p.get("video_refs", []))} audio_refs={len(p.get("audio_refs", []))} → {out}')
    t0 = time.time()
    res = subprocess.run(cmd + (['--dry-run'] if a.dry_run else []), cwd=proj, capture_output=True, text=True)
    print(res.stdout[-3000:])
    if res.stderr.strip():
        print(res.stderr[-1500:], file=sys.stderr)
    print('exit', res.returncode, 'secs', round(time.time() - t0))
    sys.exit(res.returncode)


if __name__ == '__main__':
    main()
