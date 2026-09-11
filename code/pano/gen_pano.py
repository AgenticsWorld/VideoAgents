"""① 生成场景 720 全景(equirect 2:1):俯视图 + 九格图 + 提示词 → Seedream(火山方舟直调)。

用法:
  python code/pano/gen_pano.py --project offer --scene SCN-0004 --prompt-file <prompt.txt> \
      --out assets/panorama/SCN-0004/pano_01.png [--model doubao-seedream-5-0-pro-260628] [--size 3040x1520] [--n 1]

提示词写法要点(见 qa 报告 pano_spike.md):按「画面横向位置」描述方位(左四分之一 / 中央 / 右四分之一),
不要用「身后」这类会被拆到左右两边的说法;接缝方向选树丛/山体等低细节处。
尺寸:5.0 lite 最小 3686400 px、可收 4096x2048;5.0 pro 上限 4624220 px,2:1 用 3040x1520。
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import ark_key, ledger_write, project_root  # noqa: E402

ARK = 'https://ark.cn-beijing.volces.com/api/v3/images/generations'
NEG_DEFAULT = ('fisheye distortion, tiny planet, little planet, split panels, grid, collage, multiple panels, people, human figures, '
               'silhouettes of people, human hands, faces, arrows, labels, numbers, compass rose, floor plan, map, top-down view, '
               "bird's-eye view, stretched sky, black poles, watermark, text, logo, signature, subtitles, caption, UI overlay, "
               'jpeg artifacts, blurry, lowres, letterbox bars')


def durl(p: Path) -> str:
    return 'data:image/jpeg;base64,' + base64.b64encode(p.read_bytes()).decode()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--project', required=True)
    ap.add_argument('--scene', required=True)
    ap.add_argument('--prompt-file', required=True, help='全景提示词全文(英文)')
    ap.add_argument('--negative-file', help='负面串;缺省用内置通用串')
    ap.add_argument('--out', required=True, help='输出 png(项目相对路径);n>1 时加 _cNN 后缀')
    ap.add_argument('--model', default='doubao-seedream-5-0-pro-260628')
    ap.add_argument('--size', default='3040x1520')
    ap.add_argument('--n', type=int, default=1)
    ap.add_argument('--ref', action='append', default=[], help='额外参考图(项目相对路径);缺省自动挂 layout_top.png + grid_9views.png')
    a = ap.parse_args()
    proj = project_root(a.project)
    prompt = Path(a.prompt_file).read_text().strip()
    neg = Path(a.negative_file).read_text().strip() if a.negative_file else NEG_DEFAULT
    refs = [proj / r for r in a.ref] or [proj / 'assets/concepts/scenes' / a.scene / 'layout_top.png',
                                         proj / 'assets/concepts/scenes' / a.scene / 'grid_9views.png']
    for r in refs:
        if not r.exists():
            raise SystemExit(f'参考图不存在:{r}')
    key = ark_key()
    out = proj / a.out
    out.parent.mkdir(parents=True, exist_ok=True)
    (out.with_suffix('.prompt.txt')).write_text(prompt + '\n\nNEGATIVE: ' + neg + '\n\nREFS: ' + '\n'.join(map(str, refs)) + '\n')
    ledger = out.parent / 'usage_ledger.jsonl'
    for i in range(a.n):
        target = out if a.n == 1 else out.with_name(f'{out.stem}_c{i + 1:02d}{out.suffix}')
        body = {'model': a.model, 'prompt': prompt + '\n避免出现:' + neg, 'size': a.size, 'response_format': 'url',
                'watermark': False, 'image': [durl(r) for r in refs]}
        req = urllib.request.Request(ARK, data=json.dumps(body).encode(),
                                     headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
        t0 = time.time()
        rec = {'kind': 'pano', 'scene': a.scene, 'model': a.model, 'size': a.size, 'refs': [str(r.relative_to(proj)) for r in refs]}
        try:
            resp = json.load(urllib.request.urlopen(req, timeout=600))
            target.write_bytes(urllib.request.urlopen(resp['data'][0]['url']).read())
            rec.update(status='ok', output=str(target.relative_to(proj)), secs=round(time.time() - t0, 1), usage=resp.get('usage'))
            print('OK', target, rec['secs'], 's')
        except urllib.error.HTTPError as e:
            rec.update(status='error', error=e.read().decode()[:400])
            print('HTTP', e.code, rec['error'])
        ledger_write(ledger, rec)


if __name__ == '__main__':
    main()
