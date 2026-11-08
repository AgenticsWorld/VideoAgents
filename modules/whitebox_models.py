"""白模模型库(2026-11-08,docs/whitebox.md「自定义形状:连续软体 tube 与模型库 model」)的列出 / 校验 / 预览。

模型文件:`directing/whitebox_models/<MDL-…>.json`(项目级,跨集复用),白模调度按剧情自行编写;计划 props 用
`"model": "MDL-…"` 引用。结构校验与包围盒在 modules/whitebox_shapes.validate_model,渲染在 whitebox-renderer.js buildModel。

预览:与预览 / 导出同一渲染器(借自检静帧页 whitebox-stills.html,不另开渲染页),把模型放在空场地上、旁边站一个
1.7 m 灰色参考人,出正 / 右 / 左后 3/4 / 俯视四格(有铰链时再加「铰链最小 / 最大」两格),拼成
`directing/whitebox_models/previews/<MDL>.jpg` 供白模调度读图自查形状与尺度。全本地渲染,无生成费用。
"""
from __future__ import annotations

import base64
import json
import math
import os
from pathlib import Path

from modules.whitebox_shapes import MODEL_DIR, load_model, model_ids

PREVIEW_DIR = 'previews'
TILE_W, TILE_H = 448, 336
REF_FIGURE = [.48, 1.7, .37]
DEFAULT_SWING = .6


def references(base) -> dict:
    """模型 id → 引用它的 [ep/grp, …](各集白模计划 props[].model)。"""
    out = {}
    for plan_path in sorted(Path(base).glob('directing/*/whitebox_plans/*.json')):
        try:
            plan = json.loads(plan_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            continue
        for prop in plan.get('props') or [] if isinstance(plan, dict) else []:
            mid = prop.get('model') if isinstance(prop, dict) else None
            if isinstance(mid, str):
                ref = f'{plan_path.parent.parent.name}/{plan_path.stem}'
                if ref not in out.setdefault(mid, []):
                    out[mid].append(ref)
    return out


def describe(base, ids=None) -> dict:
    """逐个校验模型:{'models': [{id, label, parts, hinges, bounds, size_m, used_by}], 'errors': [{id, error}]}。"""
    base = Path(base)
    explicit = bool(ids)
    ids = list(ids) if ids else model_ids(base)
    refs = references(base)
    rows, errors = [], []
    for mid in ids:
        try:
            model_def, meta = load_model(base, mid)
        except ValueError as error:
            errors.append({'id': mid, 'error': str(error)})
            continue
        lo, hi = meta['bounds']
        rows.append({'id': mid, 'label': meta['label'], 'legend_en': meta['legend_en'], 'parts': len(model_def['parts']),
                     'hinges': meta['hinges'], 'bounds': meta['bounds'],
                     'size_m': [round(b - a, 3) for a, b in zip(lo, hi)], 'used_by': refs.get(mid, [])})
    for mid in [] if explicit else sorted(set(refs) - set(ids)):   # 整库校验时顺带报「计划引用了、库里没有」
        errors.append({'id': mid, 'error': f'被 {", ".join(refs[mid])} 引用但 {MODEL_DIR}/{mid}.json 不存在'})
    return {'models': rows, 'errors': errors}


def _camera(center, direction, distance, fov=35):
    n = math.sqrt(sum(d * d for d in direction))
    pos = [c + d / n * distance for c, d in zip(center, direction)]
    return {'t': 0, 'position': [round(v, 4) for v in pos], 'target': [round(v, 4) for v in center], 'fov': fov}


def preview_payload(model_def, meta, mid, width=TILE_W, height=TILE_H):
    """合成一个只含该模型 + 参考人的场景与组(每个视角一个「镜头」,各 1 s),给 whitebox-stills.html 渲染。"""
    lo, hi = meta['bounds']
    lift = -lo[1] if lo[1] < 0 else 0.0                                    # 模型最低点落到地面
    center = [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2 + lift, (lo[2] + hi[2]) / 2]
    # 参考人站在模型右后斜侧(+X、−Z 外 0.6 m):正视、侧视、3/4、俯视里都在模型旁边,不挡模型
    figure_x, figure_z = hi[0] + .6 + REF_FIGURE[0] / 2, lo[2] - .6 - REF_FIGURE[2] / 2
    span_lo = [min(lo[0], figure_x - REF_FIGURE[0]), min(lo[1] + lift, 0), min(lo[2], figure_z - REF_FIGURE[2])]
    span_hi = [max(hi[0], figure_x + REF_FIGURE[0]), max(hi[1] + lift, REF_FIGURE[1]), max(hi[2], figure_z + REF_FIGURE[2])]
    focus = [(a + b) / 2 for a, b in zip(span_lo, span_hi)]
    radius = max(.3, .5 * math.dist(span_lo, span_hi))
    fov = 35
    distance = radius / math.sin(math.radians(fov / 2) * min(1.0, width / height)) * 1.08
    views = [('front', [0, .25, 1]), ('right', [1, .25, 0]), ('back-left 3/4', [-1, .45, -1]), ('top', [0, 1, .18])]
    hinges = meta['hinge_limits']
    swings = []
    if hinges:
        low = {h: v.get('min', -DEFAULT_SWING if v.get('max') is None else min(0.0, v['max'])) for h, v in hinges.items()}
        high = {h: v.get('max', DEFAULT_SWING if v.get('min') is None else max(0.0, v['min'])) for h, v in hinges.items()}
        swings = [('hinges min', [1, .35, 1], low), ('hinges max', [1, .35, 1], high)]
    shots = [(name, direction, {h: 0 for h in hinges}) for name, direction in views] + swings
    duration = len(shots)
    cameras, keys = [], []
    for i, (name, direction, joints) in enumerate(shots):
        cam = _camera(focus, direction, distance, fov)
        cameras.append({'shot_id': f'v{i}', 'start': i, 'duration_s': 1, 'keyframes': [cam, {**cam, 't': 1}], 'label': name})
        key = {'t': i, 'position': [0, round(lift, 4), 0], 'hold': True}
        if hinges:
            key['joints'] = joints
        keys.append(key)
    keys.append({**keys[-1], 't': duration, 'hold': False})
    prop = {'id': mid, 'shape': 'model', 'model_def': model_def, 'size_m': [max(b - a, .001) for a, b in zip(lo, hi)],
            'position': [0, lift, 0], 'keyframes': keys}
    figure = {'id': 'REF-1.7m', 'label': '1.7 m', 'kind': 'person', 'color': '#9e9e9e', 'size_m': REF_FIGURE,
              'keyframes': [{'t': 0, 'position': [round(figure_x, 4), 0, round(figure_z, 4)], 'pose': 'stand', 'yaw': 0},
                            {'t': duration, 'position': [round(figure_x, 4), 0, round(figure_z, 4)], 'pose': 'stand', 'yaw': 0}]}
    extent = max(8.0, 4 * radius)
    scene = {'scene_id': '_model', 'dimensions_m': [extent, max(3.0, span_hi[1] + 1), extent], 'objects': []}
    group = {'group_id': '_model', 'scene_id': '_model', 'duration_s': duration, 'actors': [figure], 'extras': [],
             'props': [prop], 'cameras': cameras}
    note = f' (origin +{lift:.2f} m)' if lift > 1e-3 else ''   # 模型有部分低于原点:整体抬到地面上看,标出原点离地高度
    return {'episode': {'scenes': {'_model': scene}, 'groups': [group]}, 'width': width, 'height': height}, \
        [(c['label'] + note, c['start'] + .5) for c in cameras]


def render_previews(base, ids, *, progress=None) -> list:
    """逐个模型出预览联系表;返回 [{id, sheet, views}]。依赖同 render_whitebox.py --stills(Playwright + Chromium)。"""
    from modules.whitebox_export import _EXPORT_LOCK
    from modules.whitebox_stills import STATIC, _sheet
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError('缺少 Playwright，请安装项目依赖并运行 python -m playwright install chromium。') from e
    base = Path(base)
    jobs = []
    for mid in ids:
        model_def, meta = load_model(base, mid)
        jobs.append((mid, *preview_payload(model_def, meta, mid)))
    out_dir = base / MODEL_DIR / PREVIEW_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    with _EXPORT_LOCK, sync_playwright() as p:
        kwargs = {'headless': True, 'args': ['--enable-webgl', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--allow-file-access-from-files']}
        if os.environ.get('VIDEOAGENTS_CHROMIUM'):
            kwargs['executable_path'] = os.environ['VIDEOAGENTS_CHROMIUM']
        browser = p.chromium.launch(**kwargs)
        try:
            for mid, payload, views in jobs:
                page = browser.new_page(viewport={'width': payload['width'], 'height': payload['height']})
                page.add_init_script('window.whiteboxExportData = ' + json.dumps(payload, ensure_ascii=False) + ';')
                page.goto((STATIC / 'whitebox-stills.html').as_uri())
                page.wait_for_function('window.whiteboxReady === true', timeout=60000)
                page.evaluate('(gid)=>window.whiteboxStills.load(gid)', '_model')
                tiles = [(f'{mid} {name}', base64.b64decode(page.evaluate('([t,view])=>window.whiteboxStills.frame(t,view)', [t, 'camera'])))
                         for name, t in views]
                page.close()
                path = out_dir / f'{mid}.jpg'
                staging = out_dir / f'.{mid}.jpg.tmp'
                _sheet([(mid, tiles)], payload['width'], payload['height']).save(staging, 'JPEG', quality=88)
                os.replace(staging, path)
                results.append({'id': mid, 'sheet': f'{MODEL_DIR}/{PREVIEW_DIR}/{mid}.jpg', 'views': [v[0] for v in views]})
                if progress:
                    progress(mid)
        finally:
            browser.close()
    return results

