"""白模自检静帧:无头渲染每镜若干时刻的摄影机视角 + 一张空间视角,拼成每组一张联系表供 Agent 读图核对取景。

只在白模调度阶段做视觉自检用(Agent 高级设置「白模自检」):全本地渲染、无生成成本;不写 manifest、不进 refs、
不产出 camera.mp4,因此不属于「H3W 签字前不得导出」的范围。渲染页独立(whitebox-stills.html),不影响导出指纹。
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
from pathlib import Path

from modules.whitebox import component, read, render_format
from modules.whitebox_export import _EXPORT_LOCK

STATIC = Path(__file__).resolve().parents[1] / 'apps/web/static'
TILE_LONG_EDGE = 448     # 单帧长边像素:够看清主体/遮挡/朝向,整张表仍在读图工具的舒适尺寸内
MAX_CAMERA_TILES = 5     # 每镜摄影机视角上限(首/尾必采,其余取机位关键帧与均分点)
MAX_EVENT_TILES = 3      # 每镜另加的剧情动作格(evt)上限(2026-11-05)
EVENT_MIN_GAP_S = 0.1    # 离已有采样时刻这么近的动作时刻不另采
EDGE_INSET_S = 1 / 24    # 首尾各向内收一帧,避免采到相邻镜头
# 镜头表 content 里的时间码「(4.0–4.5)」「(4.3–4.8,与…重叠)」(与 shot t_range_s 同一时间轴,即整集秒);取区间中点 = 动作进行中
TIMECODE_RE = re.compile(r'[（(]\s*(\d+(?:\.\d+)?)\s*(?:s|秒)?\s*[–—\-~～至到]\s*(\d+(?:\.\d+)?)\s*(?:s|秒)?\s*(?:[,，;；、][^()（）]*)?[)）]')


def _thin(ordered, limit):
    if len(ordered) <= limit:
        return ordered
    if limit == 1:
        return [ordered[len(ordered) // 2]]
    step = (len(ordered) - 1) / (limit - 1)
    return sorted({ordered[round(i * step)] for i in range(limit)})


def sample_times(camera, limit=MAX_CAMERA_TILES):
    """一镜的采样时刻(组内秒):首、尾、镜内机位关键帧,不足 3 个补中点,超过上限按时间均匀抽稀。"""
    start, duration = float(camera['start']), float(camera['duration_s'])
    inset = min(EDGE_INSET_S, duration / 4)
    first, last = start + inset, start + duration - inset
    times = {round(first, 3), round(last, 3)}
    for key in camera.get('keyframes') or []:
        t = start + float(key.get('t', 0))
        if first < t < last:
            times.add(round(t, 3))
    if len(times) < 3:
        times.add(round(start + duration / 2, 3))
    return _thin(sorted(times), limit)


def event_times(camera, group, shot=None, taken=(), limit=MAX_EVENT_TILES):
    """一镜的剧情动作采样时刻(组内秒,2026-11-05):镜头表 content 时间码区间中点(按该镜 t_range_s 换算)+ 计划道具
    运动段的中点与显隐/挂手切换时刻;去掉离 taken 太近的,超过上限均匀抽稀。自检时这些格(evt)核对剧情动作在白模里看得见。"""
    start, duration = float(camera['start']), float(camera['duration_s'])
    inset = min(EDGE_INSET_S, duration / 4)
    lo, hi = start + inset, start + duration - inset
    found = set()
    t_range = (shot or {}).get('t_range_s') or (shot or {}).get('t_range')
    if isinstance(t_range, list) and len(t_range) == 2:
        a, b = float(t_range[0]), float(t_range[1])
        for m in TIMECODE_RE.finditer(str((shot or {}).get('content') or '')):
            x, y = float(m.group(1)), float(m.group(2))
            if a - 1e-6 <= x <= y <= b + 1e-6:
                found.add(start + (x + y) / 2 - a)
    for prop in group.get('props') or []:
        keys = prop.get('keyframes') or []
        for k0, k1 in zip(keys, keys[1:]):
            if any(k0.get(f) != k1.get(f) for f in ('position', 'scale', 'pitch', 'yaw', 'roll', 'path', 'extent', 'joints')):   # 软体 / 模型通道(2026-11-08)
                found.add((float(k0['t']) + float(k1['t'])) / 2)
            if k0.get('visible', True) != k1.get('visible', True) or k0.get('attached', True) != k1.get('attached', True):
                found.add(float(k1['t']))
    picked = sorted(round(t, 3) for t in found
                    if lo <= t <= hi and all(abs(t - u) >= EVENT_MIN_GAP_S for u in taken))
    return _thin(picked, limit)


def _font(size):
    from PIL import ImageFont
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _sheet(rows, tile_w, tile_h):
    """rows: [(shot_id, [(label, jpeg_bytes), …])] → 一张联系表(每镜一行,末格为空间视角)。"""
    from PIL import Image, ImageDraw
    pad, band = 6, 20
    columns = max(len(tiles) for _, tiles in rows)
    sheet = Image.new('RGB', (pad + columns * (tile_w + pad), pad + len(rows) * (tile_h + band + pad)), (24, 24, 24))
    draw, font = ImageDraw.Draw(sheet), _font(14)
    for r, (_, tiles) in enumerate(rows):
        for c, (label, data) in enumerate(tiles):
            x, y = pad + c * (tile_w + pad), pad + r * (tile_h + band + pad)
            draw.text((x + 2, y + 2), label, fill=(235, 235, 235), font=font)
            sheet.paste(Image.open(io.BytesIO(data)).convert('RGB').resize((tile_w, tile_h)), (x, y + band))
    return sheet


def render_stills(base, episode, group_ids=None, *, progress=None):
    fmt = render_format(read(base / 'settings.json', {}), None, None)
    if episode.get('render', {}).get('aspect_ratio', fmt['aspect_ratio']) != fmt['aspect_ratio']:
        raise ValueError('Project aspect changed; recompile the episode before rendering stills')
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError('缺少 Playwright，请安装项目依赖并运行 python -m playwright install chromium。') from e
    ep = component(episode['ep'])
    groups = episode['groups'] if group_ids is None else [g for g in episode['groups'] if g['group_id'] in group_ids]
    if not groups or (group_ids is not None and set(group_ids) != {g['group_id'] for g in groups}):
        raise ValueError('Requested groups are unavailable; check compilation errors')
    scale = TILE_LONG_EDGE / max(fmt['width'], fmt['height'])
    tile_w, tile_h = round(fmt['width'] * scale), round(fmt['height'] * scale)
    output = base / 'directing' / ep / 'whitebox' / 'stills'
    output.mkdir(parents=True, exist_ok=True)
    payload = {'episode': episode, 'width': fmt['width'], 'height': fmt['height']}
    shot_rows = {s.get('shot_id'): s for s in (read(base / 'directing' / ep / 'shot_list.json', {}) or {}).get('shots', [])}
    results = []
    with _EXPORT_LOCK, sync_playwright() as p:
        kwargs = {'headless': True, 'args': ['--enable-webgl', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--allow-file-access-from-files']}
        executable = os.environ.get('VIDEOAGENTS_CHROMIUM')
        if executable:
            kwargs['executable_path'] = executable
        browser = p.chromium.launch(**kwargs)
        try:
            page = browser.new_page(viewport={'width': fmt['width'], 'height': fmt['height']})
            page.add_init_script('window.whiteboxExportData = ' + json.dumps(payload, ensure_ascii=False) + ';')
            page.goto((STATIC / 'whitebox-stills.html').as_uri())
            page.wait_for_function('window.whiteboxReady === true', timeout=60000)
            for group in groups:
                gid = component(group['group_id'])
                page.evaluate('(gid)=>window.whiteboxStills.load(gid)', gid)
                rows, index = [], []
                for camera in group.get('cameras') or []:
                    times = sample_times(camera)
                    events = event_times(camera, group, shot_rows.get(camera['shot_id']), times)
                    shots = sorted([('camera', t) for t in times] + [('event', t) for t in events], key=lambda x: x[1]) \
                        + [('overview', times[len(times) // 2])]
                    tiles = []
                    for view, t in shots:
                        frame = page.evaluate('([t,view])=>window.whiteboxStills.frame(t,view)', [t, 'overview' if view == 'overview' else 'camera'])
                        tag = {'overview': 'space', 'event': 'evt'}.get(view, 'cam')
                        tiles.append((f"{camera['shot_id']} {tag} t={t:.2f}s", base64.b64decode(frame)))
                    rows.append((camera['shot_id'], tiles))
                    index.append({'shot_id': camera['shot_id'], 'camera_t_s': times, 'event_t_s': events, 'overview_t_s': shots[-1][1]})
                if not rows:
                    raise ValueError(f'{gid}: group has no cameras to render')
                path = output / f'{gid}.jpg'
                staging = output / f'.{gid}.jpg.tmp'
                _sheet(rows, tile_w, tile_h).save(staging, 'JPEG', quality=88)
                os.replace(staging, path)
                results.append({'group_id': gid, 'sheet': f'directing/{ep}/whitebox/stills/{gid}.jpg', 'shots': index})
                if progress:
                    progress(gid)
        finally:
            browser.close()
    return results
