"""code/pano 共用:仓库根、项目根、genconfig 读取、台账。"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def project_root(project: str) -> Path:
    base = Path(os.environ.get('VIDEOAGENTS_DATA_DIR') or (ROOT / 'data')) / 'projects' / project
    if not base.is_dir():
        raise SystemExit(f'项目目录不存在:{base}')
    return base


def genconfig() -> dict:
    path = os.environ.get('VIDEOAGENTS_CONFIG_PATH') or (ROOT / 'data' / '.videoagents' / 'genconfig.json')
    return json.load(open(path))


def ark_key() -> str:
    key = genconfig().get('image', {}).get('volcengine', {}).get('api_key') or os.environ.get('ARK_API_KEY', '')
    if not key:
        raise SystemExit('缺少火山方舟 API Key(genconfig.json image.volcengine.api_key 或环境变量 ARK_API_KEY)')
    return key


def ledger_write(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {'ts': time.strftime('%Y-%m-%dT%H:%M:%S'), **record}
    with open(path, 'a') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')


def load_landmarks_m(project_dir: Path, scene_id: str, override: Path | None = None) -> dict:
    """地标世界坐标 {id: {'xz': (x, z), 'desc': name_en, 'soft': bool}}。
    默认由 layout.json 归一化 xy 按白模 dimensions_m 映射(u,v)→((u-.5)*W, (v-.5)*D);
    override JSON 可逐地标改写 xz / desc / soft(白模里有精确物体中心时建议给)。"""
    layout = json.load(open(project_dir / 'assets/concepts/scenes' / scene_id / 'layout.json'))
    wb = json.load(open(project_dir / 'bible/scenes' / scene_id / 'whitebox.json'))
    W, _, D = wb['dimensions_m']
    soft_kinds = {'spot', 'furniture', 'path', 'ground', 'direction', 'space', 'vegetation'}
    out = {}
    for lm in layout['landmarks']:
        u, v = lm['xy']
        out[lm['id']] = {'xz': ((u - .5) * W, (v - .5) * D), 'desc': lm.get('name_en') or lm['id'],
                         'soft': lm.get('kind') in soft_kinds, 'ground': True}
    if override:
        for k, v in json.load(open(override)).items():
            if k in out:
                out[k].update(v)
            else:
                out[k] = {'ground': True, 'soft': False, **v}
    return out
