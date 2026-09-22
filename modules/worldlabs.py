"""World Labs(Marble)世界模型(2026-09-12):按场景白模/全景生成可漫游的 3D world(高斯泼溅),落盘场景资产目录。

入口:场景预览页「🌍 世界模型」板块的「生成世界模型」按钮(仅项目「白模」选项开启时显示)→ 宿主后台跑
code/worldlabs_world.py → 本模块;产物由预览页用 Spark(apps/web/static/world-viewer.js)渲染,按白模坐标对齐,WASD 漫游。
只做「生成 + 预览」;在 world 里按分镜机位截图、全景重投影作参考图等试验流程(dev-eric-worldlabs 分支)不并入。

world 输入全景(二选一,预览页下拉):
  scene_pano  场景全景图(modules/scene_panos.py 出的锚点全景 panos/<anchor>/<scheme>.png,内容按设定卡、机位已知)—— 首选;
  depth2rgb   Marble API 深度→RGB(pano:depth_to_rgb):用锚点的白模径向深度全景(scene_panos 渲的 depth_pano.npy,缺则现渲)
              log 编码送 API 出全景(计费);场景还没规划锚点时自动在场景最空旷处取机位、渲进 world/ 目录。

产物目录:assets/concepts/scenes/<sid>/world/
  input.json          本次输入:来源/锚点/方案/相机位/提示词          pano.png   送 worlds:generate 的等距柱状全景(2:1)
  depth_pano.png      (depth2rgb)log 编码 8bit 深度全景(白=近)      whitebox_pano.jpg (depth2rgb)白模彩色全景
  generate.json       worlds:generate 的 operation 记录               world.json  world 响应 + alignment(白模坐标对齐)+ 各步骤
  splats_<res>.spz    高斯泼溅(500k / full_res 等)                    collider.glb  碰撞网格   world_pano.jpg / thumbnail.jpg
  variants/<label>/   重新生成前归档的上一版 world 产物(预览页只读根下当前 world)

坐标对齐(推导自 docs.worldlabs.ai/api/rendering-spz 与 web-chisel-depth-png 示例):world 原点 = 全景相机位,
OpenCV 系(y 向下、z 向前 = 全景中心列);metric_scale_factor 乘坐标、ground_plane_offset 减 y 得米制,
绕 X 轴转 180° 进 three.js(y 向上);再绕 Y 转全景相机 yaw、平移到全景相机位即为白模坐标。ground_plane_offset
应≈全景相机离地高度,可作尺度自检(scale_fix = 相机高 / ground_plane_offset)。

配置:控制台「🎨 生成模型」→「🌍 世界模型」板块(genconfig.json#world.marble:api_key / model / custom_model);
Key 也可用环境变量 WORLDLABS_API_KEY。API 文档 https://docs.worldlabs.ai/api,Key 在 https://platform.worldlabs.ai/api-keys 创建。
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import math
import os
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path

from modules.whitebox import component, load_scene, read

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = 'https://api.worldlabs.ai'
SCHEMA = 'worldlabs_world.v1'
WORLD_DIR = 'world'
PROVIDER = 'marble'
DEFAULT_MODEL = 'marble-1.1'
# 与 docs.worldlabs.ai/api/models 一致;models.html 的 WORLD_MODELS 同步维护
MODELS = ('marble-1.1-plus', 'marble-1.1', 'marble-1.0', 'marble-1.0-draft')
SOURCES = ('scene_pano', 'depth2rgb')
AUTO_ANCHOR_ID = 'W0'          # 场景没有全景锚点时自动取的机位(只存 world/,不进 panos/index.json)
CAMERA_HEIGHT_M = 1.6


class WorldLabsError(RuntimeError):
    pass


# ---------------------------------------------------------------- config / http
def _genconfig() -> dict:
    from modules.genmedia import CONFIG_PATH
    try:
        return json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    except Exception:  # noqa: BLE001
        return {}


def marble_config() -> dict:
    """生成模型配置里的世界模型段(world.marble),Key 缺省回落环境变量。"""
    world = (_genconfig().get('world') or {}) if isinstance(_genconfig(), dict) else {}
    cfg = dict((world.get(PROVIDER) or {}) if isinstance(world, dict) else {})
    cfg['api_key'] = (os.environ.get('WORLDLABS_API_KEY') or str(cfg.get('api_key') or '')).strip()
    return cfg


def api_key() -> str:
    key = marble_config().get('api_key') or ''
    if not key:
        raise WorldLabsError('缺少 World Labs API Key:请到控制台「🎨 生成模型」→「🌍 世界模型」填写(或设环境变量 WORLDLABS_API_KEY)')
    return key


def default_model() -> str:
    cfg = marble_config()
    return str(cfg.get('custom_model') or cfg.get('model') or DEFAULT_MODEL).strip() or DEFAULT_MODEL


def base_url() -> str:
    return (os.environ.get('WORLDLABS_BASE_URL') or DEFAULT_BASE_URL).rstrip('/')


def _request(method: str, path: str, body: dict | None = None, timeout: int = 120, key: str | None = None) -> dict:
    url = base_url() + path
    data = json.dumps(body).encode('utf-8') if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={'WLT-Api-Key': key or api_key(), 'Content-Type': 'application/json', 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8') or '{}')
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', errors='replace')[:2000]
        raise WorldLabsError(f'{method} {path} → HTTP {e.code}: {detail}') from e
    except urllib.error.URLError as e:
        raise WorldLabsError(f'{method} {path} 连接失败:{e.reason}') from e


def get_credits(key: str | None = None) -> float:
    return float(_request('GET', '/marble/v1/credits', key=key).get('remaining_credits') or 0)


def poll_operation(operation_id: str, *, timeout_s: int = 1800, interval_s: int = 10, progress=None) -> dict:
    """轮询 GET /operations/{id} 直到 done;返回完整 Operation(含 response / cost)。"""
    started = time.time()
    while True:
        op = _request('GET', f'/marble/v1/operations/{operation_id}')
        if op.get('done'):
            if op.get('error'):
                raise WorldLabsError(f'操作 {operation_id} 失败:{json.dumps(op["error"], ensure_ascii=False)[:1500]}')
            return op
        if time.time() - started > timeout_s:
            raise WorldLabsError(f'操作 {operation_id} 超时({timeout_s}s 未完成),稍后可用 --resume {operation_id} 继续')
        if progress:
            progress(op)
        time.sleep(interval_s)


def download(url: str, target: Path, timeout: int = 600) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + '.part')
    req = urllib.request.Request(url, headers={'User-Agent': 'VideoAgents/worldlabs'})
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(tmp, 'wb') as f:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    os.replace(tmp, target)
    return target


# ---------------------------------------------------------------- paths / records
def scene_dir(base: Path, sid: str) -> Path:
    return base / 'assets/concepts/scenes' / component(sid)


def world_dir(base: Path, sid: str) -> Path:
    return scene_dir(base, sid) / WORLD_DIR


def read_world(base: Path, sid: str) -> dict | None:
    p = world_dir(base, sid) / 'world.json'
    rec = read(p, None) if p.is_file() else None
    return rec if isinstance(rec, dict) and rec.get('world_id') else None


def _write_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(tmp, path)


def _now():
    return dt.datetime.now().isoformat(timespec='seconds')


def _progress_text(op: dict) -> str:
    return json.dumps((op.get('metadata') or {}).get('progress'), ensure_ascii=False)


# ---------------------------------------------------------------- pano sources
def list_sources(base: Path, sid: str) -> dict:
    """预览页/CLI 用:可作 world 输入的全景清单——各锚点(位置/yaw)、已成全景方案、白模深度是否已渲。"""
    from modules import scene_panos as sp
    idx = sp.load_index(base, sid)
    pdir = sp.panos_dir(base, sid)
    anchors = []
    for a in idx['anchors']:
        adir = pdir / a['anchor_id']
        schemes = sorted(s for s, rec in (a.get('panos') or {}).items()
                         if isinstance(rec, dict) and rec.get('file') and (adir / rec['file']).is_file())
        anchors.append({'anchor_id': a['anchor_id'], 'position': a.get('position'), 'yaw_deg': a.get('yaw_deg', 0),
                        'schemes': schemes, 'whitebox_ready': not sp.whitebox_pano_stale(base, sid, a)})
    return {'anchors': anchors, 'has_whitebox': (scene_dir(base, sid) / 'layout.json').is_file()}


def _anchor(base: Path, sid: str, anchor_id: str | None) -> dict | None:
    from modules import scene_panos as sp
    anchors = sp.load_index(base, sid)['anchors']
    if anchor_id:
        a = next((x for x in anchors if x['anchor_id'] == anchor_id), None)
        if not a:
            raise WorldLabsError(f'{sid}: 没有全景锚点 {anchor_id}(现有:{", ".join(x["anchor_id"] for x in anchors) or "无"})')
        return a
    return anchors[0] if anchors else None


def _camera_record(adir: Path, anchor: dict) -> dict:
    rec = read(adir / 'depth_pano.json', None) or {}
    cam = rec.get('camera') or {}
    position = [float(v) for v in (cam.get('position') or anchor['position'])]
    return {'position': position, 'yaw_deg': float(cam.get('yaw_deg', anchor.get('yaw_deg', 0)) or 0), 'height_m': position[1]}


# ---------------------------------------------------------------- auto camera(场景没有锚点时)
def _inside(p, obj) -> bool:
    x, y, z = p
    cx, cy, cz = obj['position']
    sx, sy, sz = obj['size_m']
    yaw = float(obj.get('yaw') or 0)
    dx, dz = x - cx, z - cz
    c, s = math.cos(-yaw), math.sin(-yaw)
    lx, lz = dx * c - dz * s, dx * s + dz * c
    return abs(lx) <= sx / 2 and abs(lz) <= sz / 2 and abs(y - cy) <= sy / 2


def _clearance(p, objects) -> float:
    x, _, z = p
    best = float('inf')
    for o in objects:
        cx, _, cz = o['position']
        sx, _, sz = o['size_m']
        dx = max(abs(x - cx) - sx / 2, 0)
        dz = max(abs(z - cz) - sz / 2, 0)
        best = min(best, math.hypot(dx, dz))
    return best


def choose_camera(scene: dict, height_m: float = CAMERA_HEIGHT_M) -> list[float]:
    """缺省全景相机位:场景内离墙/家具最远的可站立点(0.25 m 网格,只算与相机同高的实体),离地 height_m,同等空旷时偏好场景中心。"""
    w, _, d = scene['dimensions_m']
    objects = [o for o in scene['objects'] if (o['position'][1] + o['size_m'][1] / 2) > height_m - 0.3]
    best, best_p = -1.0, [0.0, height_m, 0.0]
    for i in range(1, int(w / 0.25)):
        for j in range(1, int(d / 0.25)):
            p = [-w / 2 + i * 0.25, height_m, -d / 2 + j * 0.25]
            if any(_inside(p, o) for o in scene['objects']):
                continue
            score = _clearance(p, objects) - 0.05 * math.hypot(p[0], p[2])
            if score > best:
                best, best_p = score, p
    return [round(v, 3) for v in best_p]


# ---------------------------------------------------------------- 1. pano input
def prepare_pano(base: Path, sid: str, *, source: str, anchor_id: str | None = None, scheme: str | None = None,
                 text_prompt: str = '', seed: int | None = None, force: bool = False, log=print) -> dict:
    """准备送 worlds:generate 的全景 world/pano.png,写 world/input.json(来源/锚点/相机位)。"""
    from modules import scene_panos as sp
    if source not in SOURCES:
        raise WorldLabsError(f'source 须为 {SOURCES} 之一:{source}')
    out = world_dir(base, sid)
    out.mkdir(parents=True, exist_ok=True)
    prev = read(out / 'input.json', None) or {}
    if source == 'scene_pano':
        anchor = _anchor(base, sid, anchor_id)
        if not anchor:
            raise WorldLabsError(f'{sid}: 还没有场景全景图(锚点),请先出全景(code/render_scene_panos.py)或改用 Marble 深度转全景')
        adir = sp.panos_dir(base, sid) / anchor['anchor_id']
        ready = [s for s, rec in (anchor.get('panos') or {}).items() if isinstance(rec, dict) and rec.get('file') and (adir / rec['file']).is_file()]
        scheme = scheme or (sorted(ready)[0] if ready else None)
        if not scheme or scheme not in ready:
            raise WorldLabsError(f"{sid}/{anchor['anchor_id']}: 没有光照方案 {scheme or '?'} 的全景(已有:{', '.join(sorted(ready)) or '无'})")
        src = adir / anchor['panos'][scheme]['file']
        shutil.copyfile(src, out / 'pano.png')
        record = {'schema_version': SCHEMA, 'scene_id': sid, 'written_at': _now(), 'source': source,
                  'anchor_id': anchor['anchor_id'], 'scheme': scheme, 'origin': str(src.relative_to(base)),
                  'camera': _camera_record(adir, anchor), 'file': 'pano.png', 'text_prompt': text_prompt}
        _write_json(out / 'input.json', record)
        log(f"全景来源:场景全景图 {anchor['anchor_id']}/{scheme} → {out / 'pano.png'}")
        return record
    # depth2rgb:锚点的白模深度全景 → Marble API 出 RGB 全景
    anchor = _anchor(base, sid, anchor_id)
    indoor = sp.anchor_indoor(base, sid, load_scene(base, sid), anchor) if anchor else sp.is_indoor(base, sid)
    if anchor:
        adir = sp.panos_dir(base, sid) / anchor['anchor_id']
        if sp.whitebox_pano_stale(base, sid, anchor):
            sp.render_whitebox_pano(base, sid, anchor, indoor=indoor, log=log)
    else:
        scene = load_scene(base, sid)
        anchor = {'anchor_id': AUTO_ANCHOR_ID, 'position': choose_camera(scene), 'yaw_deg': 0.0, 'source': 'auto'}
        indoor = sp.anchor_indoor(base, sid, scene, anchor)
        adir = out
        if force or sp.whitebox_pano_stale_at(adir, anchor):
            log(f"场景没有全景锚点,自动取机位 {anchor['position']}(离墙/家具最远处,离地 {CAMERA_HEIGHT_M} m)")
            sp.render_whitebox_pano(base, sid, anchor, indoor=indoor, log=log, out_dir=adir)
    camera = _camera_record(adir, anchor)
    same = (prev.get('source') == source and prev.get('anchor_id') == anchor['anchor_id'] and prev.get('camera') == camera
            and (out / 'pano.png').is_file())
    if same and not force:
        log(f"跳过 depth_to_rgb:已有 {out / 'pano.png'}(同锚点 {anchor['anchor_id']};--force 重出)")
        return prev
    depth_png, z_min, z_max = encode_depth_png(adir, out / 'depth_pano.png')
    if adir != out and (adir / 'whitebox_pano.jpg').is_file():
        shutil.copyfile(adir / 'whitebox_pano.jpg', out / 'whitebox_pano.jpg')
    record = {'schema_version': SCHEMA, 'scene_id': sid, 'written_at': _now(), 'source': source,
              'anchor_id': anchor['anchor_id'], 'scheme': None, 'camera': camera, 'indoor': indoor,
              'z_min': round(z_min, 4), 'z_max': round(z_max, 4), 'seed': seed, 'text_prompt': text_prompt,
              'depth_png': 'depth_pano.png', 'file': 'pano.png'}
    _write_json(out / 'input.json', record)
    op_id = depth_to_rgb(depth_png, text_prompt, z_min, z_max, seed=seed, log=log)
    record['operation_id'] = op_id
    _write_json(out / 'input.json', record)
    step = finish_pano(out, op_id, log=log)
    record.update({'pano_url': step.get('pano_url'), 'cost': step.get('cost'), 'finished_at': _now()})
    _write_json(out / 'input.json', record)
    return record


def encode_depth_png(adir: Path, target: Path) -> tuple[bytes, float, float]:
    """scene_panos 的径向深度(米,无几何处 = z_max×4)→ 官方 web-chisel-depth-png 示例的 log 编码 8bit PNG(白 = 近)。"""
    import numpy as np
    from PIL import Image
    npy = adir / 'depth_pano.npy'
    if not npy.is_file():
        raise WorldLabsError(f'缺少白模深度全景 {npy}')
    depth = np.load(npy).astype(np.float32)
    rec = read(adir / 'depth_pano.json', None) or {}
    far = float(rec.get('z_max') or 0) * 4
    valid = (depth > 0) & ((depth < far * 0.99) if far > 0 else True)
    if valid.mean() < 0.1:
        raise WorldLabsError(f'深度全景有效像素只有 {valid.mean():.0%},场景几何可能没渲染出来')
    z_min = float(max(depth[valid].min(), 0.05))
    z_max = float(depth[valid].max())
    depth[~valid] = z_max                        # 无几何(漏天/漏地)按最远处理
    norm = (np.log(np.clip(depth, z_min, z_max)) - math.log(z_min)) / max(math.log(z_max) - math.log(z_min), 1e-5)
    png8 = np.round((1.0 - norm) * 255).astype(np.uint8)
    Image.fromarray(png8, mode='L').save(target)
    return target.read_bytes(), z_min, z_max


def depth_to_rgb(png: bytes, text_prompt: str, z_min: float, z_max: float, *, seed: int | None = None, log=print) -> str:
    body = {'depth_pano_image': {'source': 'data_base64', 'data_base64': base64.b64encode(png).decode('ascii'), 'extension': 'png'},
            'text_prompt': text_prompt, 'z_min': z_min, 'z_max': z_max}
    if seed is not None:
        body['seed'] = seed
    log(f'提交 pano:depth_to_rgb(深度 PNG {len(png) // 1024} KB,z {z_min:.2f}–{z_max:.2f} m)…')
    op = _request('POST', '/marble/v1/pano:depth_to_rgb', body, timeout=300)
    log(f"operation {op['operation_id']},轮询中…")
    return op['operation_id']


def finish_pano(out: Path, operation_id: str, *, log=print) -> dict:
    """轮询 depth_to_rgb 结果并下载全景。实测(2026-09-10)pano_url 在 response.assets.imagery.pano_url(文档写 response.pano_url),两种都兼容。"""
    op = poll_operation(operation_id, progress=lambda o: log(f'  … {_progress_text(o)}'))
    resp = op.get('response') or {}
    pano_url = resp.get('pano_url') or ((resp.get('assets') or {}).get('imagery') or {}).get('pano_url')
    if not pano_url:
        raise WorldLabsError(f'depth_to_rgb 完成但没有 pano_url:{json.dumps(op, ensure_ascii=False)[:1000]}')
    download(pano_url, out / 'pano.png')
    log(f"saved: {out / 'pano.png'}  cost={json.dumps(op.get('cost'), ensure_ascii=False)}")
    return {'operation_id': operation_id, 'pano_url': pano_url, 'cost': op.get('cost')}


# ---------------------------------------------------------------- 2. world generation
def archive_world(base: Path, sid: str, label: str | None = None, *, log=print) -> Path | None:
    """把当前 world 产物挪到 world/variants/<label>/(重新生成前保留上一版);预览页只读根下当前 world。"""
    out = world_dir(base, sid)
    if not (out / 'world.json').is_file():
        return None
    label = component(label or dt.datetime.now().strftime('%Y%m%d-%H%M%S'))
    dest = out / 'variants' / label
    if dest.exists():
        raise WorldLabsError(f'归档目录已存在:{dest}')
    dest.mkdir(parents=True)
    moved = []
    for f in list(out.iterdir()):
        if f.is_file() and (f.name in ('world.json', 'generate.json', 'collider.glb', 'thumbnail.jpg', 'world_pano.jpg', 'input.json',
                                       'pano.png', 'depth_pano.png', 'whitebox_pano.jpg') or f.name.startswith('splats_')):
            os.replace(f, dest / f.name)
            moved.append(f.name)
    log(f'已归档上一版 world → {dest}({", ".join(moved)})')
    return dest


def generate_world(base: Path, sid: str, text_prompt: str | None, *, model: str | None = None, seed: int | None = None,
                   display_name: str | None = None, log=print) -> dict:
    out = world_dir(base, sid)
    pano = out / 'pano.png'
    if not pano.is_file():
        raise WorldLabsError(f'缺少全景 {pano}(先 prepare_pano)')
    model = model or default_model()
    body = {'display_name': (display_name or f'{base.name} {sid}')[:64], 'model': model,
            'world_prompt': {'type': 'image', 'is_pano': True,   # 实测须 JSON 布尔,字符串 'true' 422
                             'image_prompt': {'source': 'data_base64', 'data_base64': base64.b64encode(pano.read_bytes()).decode('ascii'), 'extension': 'png'}}}
    if text_prompt:
        body['world_prompt']['text_prompt'] = text_prompt
    if seed is not None:
        body['seed'] = seed
    log(f'提交 worlds:generate({model},全景 {pano.stat().st_size // 1024} KB)…')
    op = _request('POST', '/marble/v1/worlds:generate', body, timeout=300)
    op_id = op['operation_id']
    _write_json(out / 'generate.json', {'operation_id': op_id, 'submitted_at': _now(), 'model': model, 'seed': seed, 'text_prompt': text_prompt})
    log(f'operation {op_id},约 5 分钟,轮询中…')
    return finish_world(base, sid, op_id, log=log)


def finish_world(base: Path, sid: str, operation_id: str, *, log=print) -> dict:
    """轮询 world 生成结果并下载全部资产,写 world.json。可用于中断后续接(--resume)。"""
    out = world_dir(base, sid)
    op = poll_operation(operation_id, progress=lambda o: log(f'  … {_progress_text(o)}'))
    world = op.get('response') or {}
    if not world.get('world_id'):
        raise WorldLabsError(f'world 生成完成但响应异常:{json.dumps(op, ensure_ascii=False)[:1000]}')
    assets = world.get('assets') or {}
    files: dict = {}
    for res, url in ((assets.get('splats') or {}).get('spz_urls') or {}).items():
        name = f'splats_{component(res)}.spz'
        log(f'下载 splats {res} …')
        download(url, out / name)
        files.setdefault('splats', {})[res] = name
    mesh = assets.get('mesh') or {}
    if mesh.get('collider_mesh_url'):
        log('下载 collider.glb …')
        download(mesh['collider_mesh_url'], out / 'collider.glb')
        files['collider'] = 'collider.glb'
    imagery = assets.get('imagery') or {}
    if imagery.get('pano_url'):
        download(imagery['pano_url'], out / 'world_pano.jpg')
        files['world_pano'] = 'world_pano.jpg'
    if assets.get('thumbnail_url'):
        download(assets['thumbnail_url'], out / 'thumbnail.jpg')
        files['thumbnail'] = 'thumbnail.jpg'
    inp = read(out / 'input.json', None) or {}
    gen_rec = read(out / 'generate.json', None) or {}
    semantics = (assets.get('splats') or {}).get('semantics_metadata') or {}
    cam = inp.get('camera') or {}
    gpo = semantics.get('ground_plane_offset')
    record = {
        'schema_version': SCHEMA, 'scene_id': sid, 'written_at': _now(),
        'world_id': world['world_id'], 'display_name': world.get('display_name'), 'model': world.get('model') or gen_rec.get('model'),
        'world_marble_url': world.get('world_marble_url'), 'caption': assets.get('caption'),
        'semantics_metadata': semantics, 'files': files,
        'alignment': {
            'convention': 'marble_raw_opencv → ×metric_scale_factor, y −= ground_plane_offset → rotX(180°) → rotY(yaw) + camera',
            'camera': cam, 'yaw_deg': cam.get('yaw_deg', 0.0),
            'metric_scale_factor': semantics.get('metric_scale_factor'), 'ground_plane_offset': gpo,
            # 尺度标定:全景相机离地高度是渲染深度全景时精确已知的量,模型估的 ground_plane_offset 就是它眼里的相机高,
            # 二者之比即米制修正;viewer 把 scale_fix 同时乘到坐标与 offset 上
            'scale_fix': round(cam['height_m'] / gpo, 4) if cam.get('height_m') and gpo else 1.0,
            'scale_fix_basis': 'camera_height_m / ground_plane_offset', 'yaw_fix_deg': 0.0,
        },
        'input': inp,
        'steps': {'generate': {**gen_rec, 'operation_id': operation_id, 'cost': op.get('cost'), 'finished_at': _now()}},
        'world_response': world,
    }
    _write_json(out / 'world.json', record)
    log(f"saved: {out / 'world.json'}  world_id={world['world_id']}  cost={json.dumps(op.get('cost'), ensure_ascii=False)}")
    log(f"尺度自检:全景相机离地 {cam.get('height_m')} m,ground_plane_offset={gpo},metric_scale_factor={semantics.get('metric_scale_factor')}")
    return record


# ---------------------------------------------------------------- prompt
def default_prompt(base: Path, sid: str) -> str:
    """从场景设定卡拼英文描述:空场景声明 + 布局图四边说明 + 光照方案 + 建筑材质;不写人物。"""
    sdir = scene_dir(base, sid)
    bdir = base / 'bible/scenes' / component(sid)
    layout = read(sdir / 'layout.json', {}) or {}
    arch = read(bdir / 'architecture.json', {}) or {}
    lighting = read(bdir / 'lighting.json', {}) or {}
    scheme = next(iter(lighting.get('schemes') or []), {}) or {}
    o = layout.get('orientation') or {}
    from modules import scene_panos as sp
    kind = 'interior' if sp.is_indoor(base, sid) else 'exterior'
    parts = [f'A 360 panorama of an empty real {kind} location, photographed with nobody present, realistic live-action film look.',
             f"Location: {layout.get('scene_name_en') or layout.get('scene_name') or sid}, {layout.get('sub_space') or ''}.".replace(' ,', ','),
             'Looking straight ahead (image centre) is ' + (o.get('top_of_map') or 'the far side') + '.',
             'To the right is ' + (o.get('right_of_map') or 'the right side') + '.',
             'Behind the camera is ' + (o.get('bottom_of_map') or 'the near side') + '.',
             'To the left is ' + (o.get('left_of_map') or 'the left side') + '.']
    if o.get('note_en'):
        parts.append(o['note_en'])
    if scheme.get('prompt_fragment_en'):
        parts.append('Lighting: ' + scheme['prompt_fragment_en'])
    mats = arch.get('materials') or []
    if mats:
        parts.append('Materials and era (reference only): ' + '; '.join(str(m) for m in mats) + f". Style: {arch.get('arch_style') or ''}.")
    parts.append('No people, no text, no watermark.')
    return ' '.join(p.strip() for p in parts if p and p.strip())


# ---------------------------------------------------------------- preview summary
def preview_summary(base: Path, sid: str, url_prefix: str) -> dict | None:
    """预览 API 用:当前 world(文件清单只列实际存在的)+ 对齐参数 + 预览图 URL(url_prefix = /projects/<slug>)。"""
    wj = read_world(base, sid)
    if not wj:
        return None
    wdir = world_dir(base, sid)
    files = wj.get('files') or {}
    world = {k: wj.get(k) for k in ('world_id', 'model', 'world_marble_url', 'caption', 'written_at', 'semantics_metadata', 'alignment')}
    world['input'] = {k: (wj.get('input') or {}).get(k) for k in ('source', 'anchor_id', 'scheme', 'camera')}
    world['files'] = {'splats': {res: name for res, name in (files.get('splats') or {}).items() if (wdir / name).is_file()},
                      **{k: files[k] for k in ('collider', 'thumbnail', 'world_pano') if files.get(k) and (wdir / files[k]).is_file()}}
    rel = f'assets/concepts/scenes/{component(sid)}/{WORLD_DIR}'
    world['base_url'] = f'{url_prefix}/{rel}/'
    world['previews'] = {k: f'{url_prefix}/{rel}/{n}?v={int((wdir / n).stat().st_mtime)}'
                         for k, n in (('pano', 'pano.png'), ('whitebox_pano', 'whitebox_pano.jpg'), ('depth_pano', 'depth_pano.png'),
                                      ('thumbnail', 'thumbnail.jpg'))
                         if (wdir / n).is_file()}
    return world


# ---------------------------------------------------------------- 背景图模式「世界模型」:在 world 里按母图机位截图(2026-09-22)
STATIC = ROOT / 'apps/web/static'
VIEW_RES_ORDER = ('full_res', '1000k', '500k', '150k', '100k')   # 截图用的 splats 精度:有全精度用全精度


def world_missing(base: Path, sid: str) -> bool:
    """场景是否还没有可用 world(world.json + 至少一个 splats 文件)。"""
    wj = read_world(base, sid)
    if not wj:
        return True
    splats = (wj.get('files') or {}).get('splats') or {}
    return not any((world_dir(base, sid) / n).is_file() for n in splats.values())


def _pick_splat(base: Path, sid: str, wj: dict, res: str | None = None) -> tuple[str, str]:
    splats = {r: n for r, n in ((wj.get('files') or {}).get('splats') or {}).items() if (world_dir(base, sid) / n).is_file()}
    if not splats:
        raise WorldLabsError(f'{sid}: world.json 没有可用的 splats 文件(先生成世界模型)')
    if res and res in splats:
        return res, splats[res]
    for r in VIEW_RES_ORDER:
        if r in splats:
            return r, splats[r]
    r = sorted(splats)[0]
    return r, splats[r]


def _launch_kwargs(gpu: bool) -> dict:
    """无头 Chromium 启动参数:gpu=True 先试真 GPU(mac 用 Metal;高斯泼溅 swiftshader 每帧 8–30 s,GPU 不到 1 s),
    不可用再退回 swiftshader(与 scene_panos.render_whitebox_pano 同参数)。VIDEOAGENTS_WORLD_GPU=0 强制 swiftshader。"""
    import sys as _sys
    if gpu:
        args = ['--enable-webgl', '--ignore-gpu-blocklist', '--enable-gpu-rasterization', '--allow-file-access-from-files']
        if _sys.platform == 'darwin':
            args.append('--use-angle=metal')
    else:
        args = ['--enable-webgl', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--allow-file-access-from-files']
    kwargs = {'headless': True, 'args': args}
    if os.environ.get('VIDEOAGENTS_CHROMIUM'):
        kwargs['executable_path'] = os.environ['VIDEOAGENTS_CHROMIUM']
    return kwargs


def render_world_views(base: Path, sid: str, requests: list[dict], *, width: int, height: int, res: str | None = None,
                       log=print) -> list[dict]:
    """在无头 Chromium(Spark)里加载场景 world,按各机位截图落盘(JPEG)。requests[i] = {'camera': {position, target, fov_v_deg},
    'output': Path};返回与 requests 等长的记录 [{file, kind:'world', world_id, anchor_id, scheme, res, size, render_s}]。
    一次进程只加载一次 world,逐张出图;渲染页报错逐张记 error 字段(不抛),缺 Playwright / world 才抛 WorldLabsError。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise WorldLabsError('缺少 Playwright,请安装项目依赖并运行 python -m playwright install chromium') from e
    wj = read_world(base, sid)
    if not wj or world_missing(base, sid):
        raise WorldLabsError(f'{sid}: 还没有世界模型(assets/concepts/scenes/{sid}/world/world.json),请先在场景预览页「🌍 世界模型」板块生成')
    res_used, splat_name = _pick_splat(base, sid, wj, res)
    splat_path = world_dir(base, sid) / splat_name
    data = splat_path.read_bytes()
    inp = wj.get('input') or {}
    results: list[dict] = []
    gpu_pref = os.environ.get('VIDEOAGENTS_WORLD_GPU', '1') != '0'
    with sync_playwright() as p:
        attempts = [True, False] if gpu_pref else [False]
        browser = page = None
        info = None
        for gpu in attempts:
            browser = p.chromium.launch(**_launch_kwargs(gpu))
            try:
                page = browser.new_page(viewport={'width': width, 'height': height})
                errors: list[str] = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                # 本地 SPZ 经 route 以 http 供给 Spark 的 fetch(file:// 下 fetch 本地文件不可靠)
                page.route('http://world-assets.local/**', lambda route: route.fulfill(status=200, body=data, headers={
                    'Content-Type': 'application/octet-stream', 'Access-Control-Allow-Origin': '*'}))
                page.goto((STATIC / 'world-view-export.html').as_uri())
                page.wait_for_function('window.worldViewReady === true', timeout=60000)
                log(f"   加载世界模型 {sid} {splat_name}({len(data) // 1024 // 1024} MB,{'GPU' if gpu else 'swiftshader'})…")
                info = page.evaluate('(o) => window.worldView.load(o)', {
                    'url': f'http://world-assets.local/{splat_name}', 'alignment': wj.get('alignment') or {}, 'width': width, 'height': height})
                if errors:
                    raise RuntimeError(' | '.join(errors)[:600])
                break
            except Exception as e:  # noqa: BLE001
                browser.close()
                browser = page = None
                if gpu and len(attempts) > 1:
                    log(f'   GPU 无头渲染不可用({str(e)[:160]}),改用 swiftshader(慢)')
                    continue
                raise WorldLabsError(f'{sid}: 世界模型渲染页加载失败:{e}') from e
        assert browser is not None and page is not None
        try:
            log(f"   {info.get('numSplats')} splats · {info.get('renderer') or '?'} · 待出 {len(requests)} 张 {width}x{height}")
            for r in requests:
                cam = r['camera']
                out = Path(r['output'])
                out.parent.mkdir(parents=True, exist_ok=True)
                t0 = time.time()
                rec = {'kind': 'world', 'file': str(out), 'world_id': wj.get('world_id'), 'anchor_id': inp.get('anchor_id'),
                       'scheme': inp.get('scheme'), 'res': res_used, 'size': [width, height], 'hole_fraction': None}
                try:
                    errors.clear()
                    jpg = page.evaluate('(c) => window.worldView.shoot(c)',
                                        {'position': list(cam['position']), 'target': list(cam['target']), 'fov': float(cam['fov_v_deg'])})
                    if errors:
                        raise RuntimeError(' | '.join(errors)[:300])
                    out.write_bytes(base64.b64decode(jpg))
                    rec['render_s'] = round(time.time() - t0, 1)
                except Exception as e:  # noqa: BLE001
                    rec['error'] = str(e)[:300]
                results.append(rec)
        finally:
            browser.close()
    return results
