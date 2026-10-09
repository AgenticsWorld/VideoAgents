"""World Labs Atlas(Marble 2 beta)世界模型渠道(2026-10-09):与 Marble 同一入口、同一产物格式,换一条生成路线。

Atlas 没有「一次出整个 3D 世界」的任务,它按机位出图:atlasChisel 收每个目标机位的深度(白模/灰盒)+ 场景实拍图(带机位),
在这些机位出与深度一致、与实拍图外观一致的 RGB 视图(一次最多 32 个目标机位,上下文 + 实拍 + 目标合计 64 视图)。本模块:
  1. 视图规划:全景锚点处 6 向平视环 + 3 向俯视环;本场景各集分镜机位(按母图视场 55°,去重后按位姿最远点取样);
     余下预算在白模空地上按最远点取站位,每站 4 向。机位一律白模米制坐标(three.js,Y 向上)。
  2. 输入:每个目标机位的白模 z 深度(modules/scene_panos.raycast 纯 numpy 求交,1280×720,反向 log 8bit PNG,白 = 近)作 contextFrames;
     场景全景图在锚点处切 6 张平视 + 1 张俯视透视图作 sourceFrames(机位精确已知)。
  3. 提交 POST /tasks:atlasChisel → 轮询 GET /operations/{id} → 下载各目标视图。
  4. 融合:每张视图按「本机位白模深度」反投影成点(隔 2 像素取一点,深度断层处剔除),体素去重(同体素留离机位最近的),
     写成 SPZ v2 高斯泼溅(splats_full_res.spz / splats_500k.spz),坐标与 Marble 产物同一约定(以全景相机为原点、OpenCV 轴),
     world.json#alignment 填 metric_scale_factor=1、ground_plane_offset=0、scale_fix=1——场景预览页视窗、背景图模式「世界模型」的截图、
     导演台世界背景都不用改就能用。
画质:融合出的是按视图贴出来的点云,机位附近清楚、远离所有机位处会有空洞;背景图截图在规划时已含本场景分镜机位附近的视图。

配置:控制台「🎨 生成模型」→「🌍 世界模型」→ Atlas 标签页(genconfig.json#world.atlas.api_key;world.provider = atlas 时生效);
Key 也可用环境变量 ATLAS_API_KEY;API 根地址默认 https://api.atlas-beta.worldlabs.ai/api/v2(beta,可用 ATLAS_API_BASE_URL 覆盖)。
Key 在 https://atlas-beta.worldlabs.ai/api-keys 创建,须含 tasks.create / operations.read / assets.create / assets.read 权限。
"""
from __future__ import annotations

import base64
import gzip
import io
import json
import math
import os
import random
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from modules import worldlabs as wl
from modules.whitebox import load_scene, read

PROVIDER = 'atlas'
DEFAULT_BASE_URL = 'https://api.atlas-beta.worldlabs.ai/api/v2'
TASK = 'atlasChisel'
GRID = (1280, 720)              # atlasChisel 的上下文深度与目标机位固定 1280×720
MAX_ROLLOUT_VIEWS = 64          # contextFrames + sourceFrames + targetCameras 合计上限(每个目标连深度占 2 个)
MAX_TARGETS = 32
VIEW_FOV_V = 60.0               # 锚点环 / 空地站位环的垂直视场(16:9 下水平 ≈ 92°)
SHOT_FOV_V = 55.0               # 分镜机位按母图视场出(modules/shot_plates.py 母图 55°)
SOURCE_YAWS = (0, 60, 120, 180, 240, 300)
SOURCE_FLOOR_PITCH = -60.0
FLOOR_PITCH = -40.0
STATION_EYE_M = 1.6
SPZ_MAGIC = 0x5053474E          # 'NGSP'
SH_C0 = 0.28209479177387814
FULL_RES_CAP = 3_000_000
PREVIEW_CAP = 500_000


class AtlasError(wl.WorldLabsError):
    """status = HTTP 状态码(接口报错时);english() = 英文界面用的同义文案(服务端文案非中文界面一律英文)。"""

    def __init__(self, msg: str, *, status: int | None = None, en: str | None = None):
        super().__init__(msg)
        self.status, self.en = status, en

    def english(self) -> str:
        return self.en or str(self)


# ---------------------------------------------------------------- config / http
def atlas_config() -> dict:
    """生成模型配置里的 Atlas 段(world.atlas),Key 缺省回落环境变量 ATLAS_API_KEY。"""
    world = wl._genconfig().get('world') or {}
    cfg = dict((world.get(PROVIDER) or {}) if isinstance(world, dict) else {})
    cfg['api_key'] = (os.environ.get('ATLAS_API_KEY') or str(cfg.get('api_key') or '')).strip()
    return cfg


def api_key() -> str:
    key = atlas_config().get('api_key') or ''
    if not key:
        raise AtlasError('缺少 Atlas API Key:请到控制台「🎨 生成模型」→「🌍 世界模型」→ Atlas 填写(或设环境变量 ATLAS_API_KEY)')
    return key


def base_url() -> str:
    return (os.environ.get('ATLAS_API_BASE_URL') or str(atlas_config().get('base_url') or '') or DEFAULT_BASE_URL).rstrip('/')


_HINTS = {401: 'API Key 无效或已撤销', 402: '账户余额不足或触发了月度花费上限(Settings → Billing)',
          403: 'API Key 缺少权限(须含 tasks.create / operations.read / assets.create / assets.read)或不属于该项目'}
_HINTS_EN = {401: 'invalid or revoked API key', 402: 'out of credits or monthly spend limit reached (Settings → Billing)',
             403: 'API key lacks a permission (needs tasks.create / operations.read / assets.create / assets.read) or belongs to another project'}


def _request(method: str, path: str, body: dict | None = None, *, key: str | None = None, timeout: int = 120,
             idempotency_key: str | None = None, retries: int = 4) -> dict:
    """调 Atlas Developer API。429 / 500 / 503 按 Retry-After(无则指数退避加抖动)重试,提交带同一 Idempotency-Key 不会重复计费。"""
    url = base_url() + path
    data = json.dumps(body).encode('utf-8') if body is not None else None
    headers = {'WLT-Api-Key': key or api_key(), 'Accept': 'application/json'}
    if data is not None:
        headers['Content-Type'] = 'application/json'
    if idempotency_key:
        headers['Idempotency-Key'] = idempotency_key
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode('utf-8') or '{}')
        except urllib.error.HTTPError as e:
            detail = e.read().decode('utf-8', errors='replace')[:2000]
            if e.code in (429, 500, 503) and attempt < retries:
                try:
                    wait = float(e.headers.get('Retry-After') or 0)
                except ValueError:
                    wait = 0.0
                time.sleep(min(max(wait, 2 ** attempt * 2) + random.uniform(0, 1), 90))
                continue
            hint, hint_en = (f'({_HINTS[e.code]})', f' ({_HINTS_EN[e.code]})') if e.code in _HINTS else ('', '')
            raise AtlasError(f'{method} {path} → HTTP {e.code}{hint}: {detail}', status=e.code,
                             en=f'{method} {path} → HTTP {e.code}{hint_en}: {detail}') from e
        except urllib.error.URLError as e:
            if attempt < retries and idempotency_key:
                time.sleep(2 ** attempt * 2)
                continue
            raise AtlasError(f'{method} {path} 连接失败:{e.reason}', en=f'{method} {path} connection failed: {e.reason}') from e
    raise AtlasError(f'{method} {path} 重试 {retries} 次仍失败')


def check_key(key: str | None = None) -> dict:
    """验证 Key:列一条本项目的操作记录(Atlas 没有余额接口;需 operations.read 权限)。"""
    r = _request('GET', '/operations?pageSize=1', key=key, retries=1)
    return {'ok': True, 'operations': len(r.get('operations') or [])}


def poll_operation(operation_id: str, *, timeout_s: int = 3600, interval_s: int = 10, progress=None) -> dict:
    """轮询 GET /operations/{id} 直到 done;刚提交的短暂 404 照文档重试。返回完整 Operation(response 为任务结果)。"""
    started = time.time()
    while True:
        try:
            op = _request('GET', f'/operations/{urllib.parse.quote(operation_id)}')
        except AtlasError as e:
            if 'HTTP 404' not in str(e) or time.time() - started > 120:
                raise
            op = {}
        if op.get('done'):
            if op.get('error'):
                raise AtlasError(f'操作 {operation_id} 失败:{json.dumps(op["error"], ensure_ascii=False)[:1500]}')
            return op
        if time.time() - started > timeout_s:
            raise AtlasError(f'操作 {operation_id} 超时({timeout_s}s 未完成),稍后可用 --resume {operation_id} 继续')
        if progress and op:
            progress(op)
        time.sleep(interval_s)


def fetch_asset(asset: dict, target: Path) -> Path:
    """下载任务输出的一个 TaskAsset:优先内联 base64,其次 url;url 过期(返回的读链接有时效)时按 assetId 重新签一个。"""
    if asset.get('base64'):
        raw = str(asset['base64'])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(raw.split(',', 1)[1] if raw.startswith('data:') else raw))
        return target
    if asset.get('url'):
        try:
            return wl.download(asset['url'], target)
        except Exception:  # noqa: BLE001
            if not asset.get('assetId'):
                raise
    if not asset.get('assetId'):
        raise AtlasError(f'输出没有可下载的地址:{json.dumps(asset, ensure_ascii=False)[:300]}')
    r = _request('POST', f"/assets/{urllib.parse.quote(asset['assetId'])}:createReadUrl", {})
    return wl.download(r['readUrl'], target)


def _data_url(data: bytes, mime: str) -> str:
    return f'data:{mime};base64,' + base64.b64encode(data).decode('ascii')


# ---------------------------------------------------------------- cameras(白模米制坐标,three.js:Y 向上,yaw 0 = 看 -Z,逆时针为正)
def yaw_pitch_target(position, yaw_deg: float, pitch_deg: float, dist: float = 5.0) -> list[float]:
    y, p = math.radians(yaw_deg), math.radians(pitch_deg)
    return [position[0] - math.sin(y) * math.cos(p) * dist, position[1] + math.sin(p) * dist, position[2] - math.cos(y) * math.cos(p) * dist]


def _basis(position, target):
    import numpy as np
    pos = np.asarray(position, dtype=np.float64)
    f = np.asarray(target, dtype=np.float64) - pos
    f /= np.linalg.norm(f)
    up = np.array([0.0, 1.0, 0.0])
    if abs(float(np.dot(f, up))) > .999:
        up = np.array([0.0, 0.0, -1.0])
    r = np.cross(f, up)
    r /= np.linalg.norm(r)
    return pos, r, np.cross(r, f), f


def _quat_xyzw(m) -> list[float]:
    """3×3 旋转矩阵 → 单位四元数 XYZW(w ≥ 0)。"""
    t = m[0][0] + m[1][1] + m[2][2]
    if t > 0:
        s = math.sqrt(t + 1.0) * 2
        q = [(m[2][1] - m[1][2]) / s, (m[0][2] - m[2][0]) / s, (m[1][0] - m[0][1]) / s, 0.25 * s]
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2]) * 2
        q = [0.25 * s, (m[0][1] + m[1][0]) / s, (m[0][2] + m[2][0]) / s, (m[2][1] - m[1][2]) / s]
    elif m[1][1] > m[2][2]:
        s = math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2]) * 2
        q = [(m[0][1] + m[1][0]) / s, 0.25 * s, (m[1][2] + m[2][1]) / s, (m[0][2] - m[2][0]) / s]
    else:
        s = math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1]) * 2
        q = [(m[0][2] + m[2][0]) / s, (m[1][2] + m[2][1]) / s, 0.25 * s, (m[1][0] - m[0][1]) / s]
    n = math.sqrt(sum(v * v for v in q)) or 1.0
    q = [v / n for v in q]
    return [-v for v in q] if q[3] < 0 else q


def pinhole(view: dict, size=GRID) -> dict:
    """{position, target, fov_v_deg} → Atlas PinholeCamera(相机到世界,RUB:右 / 上 / 后,看 -Z;内参按像素,主点居中)。"""
    pos, r, u, f = _basis(view['position'], view['target'])
    m = [[r[i], u[i], -f[i]] for i in range(3)]
    w, h = size
    fy = (h / 2) / math.tan(math.radians(float(view['fov_v_deg'])) / 2)
    return {'extrinsics': {'position': [round(float(v), 5) for v in pos], 'quaternion': [round(v, 7) for v in _quat_xyzw(m)],
                           'coordinateSystem': 'rub'},
            'intrinsics': {'fx': round(fy, 4), 'fy': round(fy, 4), 'cx': w / 2, 'cy': h / 2, 'width': w, 'height': h}}


def _ang(a, b) -> float:
    import numpy as np
    _, _, _, fa = _basis(a['position'], a['target'])
    _, _, _, fb = _basis(b['position'], b['target'])
    return math.degrees(math.acos(max(-1.0, min(1.0, float(np.dot(fa, fb))))))


def _pose_dist(a, b) -> float:
    """位姿距离:位置差(米)+ 朝向差折 2 m / 180°,用于去重与最远点取样。"""
    return math.dist(a['position'], b['position']) + 2.0 * _ang(a, b) / 180.0


def plan_views(scene: dict, anchor: dict, shot_cams: list | None = None, *, budget: int = 28) -> list[dict]:
    """目标机位清单 [{position, target, fov_v_deg, kind, label}],长度 ≤ budget。anchor = {position, yaw_deg}(全景相机)。
    顺序:锚点平视环 6 → 锚点俯视环 3 → 分镜机位(去重后最远点取样,至多剩余预算一半)→ 空地站位 4 向环。"""
    from modules import scene_panos as sp
    pos_a, yaw_a = [float(v) for v in anchor['position']], float(anchor.get('yaw_deg') or 0)
    views: list[dict] = []

    def add(position, yaw, pitch, fov, kind, label):
        if len(views) < budget:
            views.append({'position': [round(float(v), 4) for v in position], 'target': [round(v, 4) for v in yaw_pitch_target(position, yaw, pitch)],
                          'fov_v_deg': fov, 'kind': kind, 'label': label})

    for d in SOURCE_YAWS:
        add(pos_a, yaw_a + d, 0.0, VIEW_FOV_V, 'anchor', f'anchor yaw+{d}')
    for d in (0, 120, 240):
        add(pos_a, yaw_a + d, FLOOR_PITCH, VIEW_FOV_V, 'anchor_floor', f'anchor floor yaw+{d}')
    # 分镜机位:各集白模镜首/镜尾,按母图视场;同位同向的去重
    cands = []
    for c in shot_cams or []:
        v = {'position': [round(float(x), 4) for x in c['position']], 'target': [round(float(x), 4) for x in c['target']],
             'fov_v_deg': SHOT_FOV_V, 'kind': 'shot', 'label': f"{c.get('ep', '')}/{c.get('shot_id', '')}:{c.get('role', 'start')}"}
        if math.dist(v['position'], v['target']) < 1e-3 or any(_pose_dist(v, o) < 0.8 for o in cands):
            continue
        cands.append(v)
    quota = min(len(cands), max(0, (budget - len(views)) // 2))      # 至少留一半给空地站位
    while cands and quota > 0 and len(views) < budget:
        best = max(cands, key=lambda c: min(_pose_dist(c, o) for o in views))
        cands.remove(best)
        if min(_pose_dist(best, o) for o in views) < 0.8:
            break
        views.append(best)
        quota -= 1
    # 空地站位:白模可站立网格点里离已有机位最远的,每站 4 向平视
    pts = sp.candidate_points(scene, STATION_EYE_M)
    while pts and budget - len(views) >= 4:
        p = max(pts, key=lambda q: min(math.dist(q, o['position']) for o in views))
        if min(math.dist(p, o['position']) for o in views) < 1.5:
            break
        pts.remove(p)
        n = sum(1 for v in views if v['kind'] == 'station') // 4 + 1
        for d in (0, 90, 180, 270):
            add(p, yaw_a + d, -5.0, VIEW_FOV_V, 'station', f'station {n} yaw+{d}')
    return views


# ---------------------------------------------------------------- inputs
def whitebox_depth(scene: dict, indoor: bool, view: dict, size=GRID):
    """本机位白模 z 深度(米,沿光轴;无几何处 inf),与 pinhole() 内参逐像素对齐。"""
    from modules import scene_panos as sp
    w, h = size
    origin, dirs = sp._view_rays(view, w, h)
    t = sp.raycast(origin, dirs, sp.scene_boxes(scene, indoor, reach=origin))
    _, _, _, f = _basis(view['position'], view['target'])
    return (t * (dirs @ f)).reshape(h, w)


def encode_log_depth(z) -> tuple[bytes, float, float]:
    """z 深度 → Atlas LogDepthBufferAsset 的反向 log 8bit PNG(白 = zMin 近,黑 = zMax 远);无几何处按最远。"""
    import numpy as np
    from PIL import Image
    finite = np.isfinite(z) & (z > 0)
    if not finite.any():
        raise AtlasError('该机位看不到任何白模几何')
    z_min = float(max(z[finite].min(), 0.05))
    z_max = float(z[finite].max())
    if not finite.all():
        z_max *= 1.5
    z_max = max(z_max, z_min * 1.05)
    zz = np.clip(np.where(finite, z, z_max), z_min, z_max)
    norm = (np.log(zz) - math.log(z_min)) / (math.log(z_max) - math.log(z_min))
    buf = io.BytesIO()
    Image.fromarray(np.round((1.0 - norm) * 255).astype(np.uint8)).save(buf, format='PNG', optimize=True)
    return buf.getvalue(), round(z_min, 5), round(z_max, 5)


def cut_pano(pano, anchor: dict, view: dict, size=GRID):
    """等距柱状全景(RGB ndarray,中心列 = 锚点 yaw 正前方)在锚点处按 view 的朝向/视场切透视图(纯旋转,双线性)。"""
    import cv2
    import numpy as np
    from modules import scene_panos as sp
    w, h = size
    _, dirs = sp._view_rays(view, w, h)
    yaw = math.radians(float(anchor.get('yaw_deg') or 0))
    cy, sy = math.cos(yaw), math.sin(yaw)
    lx = cy * dirs[:, 0] - sy * dirs[:, 2]
    lz = sy * dirs[:, 0] + cy * dirs[:, 2]
    theta = np.arctan2(lx, -lz)
    phi = np.arccos(np.clip(dirs[:, 1], -1, 1))
    ph, pw = pano.shape[:2]
    mx = ((theta + np.pi) / (2 * np.pi) * pw - .5).astype(np.float32).reshape(h, w)
    my = np.clip(phi / np.pi * ph - .5, 0, ph - 1).astype(np.float32).reshape(h, w)
    return cv2.remap(pano, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)


def source_views(anchor: dict) -> list[dict]:
    pos, yaw = [float(v) for v in anchor['position']], float(anchor.get('yaw_deg') or 0)
    out = [{'position': pos, 'target': yaw_pitch_target(pos, yaw + d, 0.0), 'fov_v_deg': VIEW_FOV_V, 'label': f'pano yaw+{d}'} for d in SOURCE_YAWS]
    out.append({'position': pos, 'target': yaw_pitch_target(pos, yaw, SOURCE_FLOOR_PITCH), 'fov_v_deg': VIEW_FOV_V, 'label': 'pano floor'})
    return out


def max_targets(n_sources: int) -> int:
    return min(MAX_TARGETS, (MAX_ROLLOUT_VIEWS - n_sources) // 2)


# ---------------------------------------------------------------- SPZ(Spark 读法:位置 24 bit 定点、alpha/颜色/对数尺度各 8 bit、v2 四元数 xyz 各 8 bit)
def write_spz(path: Path, xyz, rgb, scale, alpha, *, fractional_bits: int = 12) -> Path:
    """写 SPZ v2(gzip,SH 0 阶)。xyz (N,3) 米;rgb (N,3) 0–1;scale (N,) 各向同性标准差(米);alpha (N,) 0–1。"""
    import numpy as np
    n = int(len(xyz))
    lim = (1 << 23) - 1
    pos = np.clip(np.round(np.asarray(xyz, dtype=np.float64) * (1 << fractional_bits)), -lim, lim).astype('<i4')
    pos_b = pos.reshape(-1, 1).view(np.uint8).reshape(n * 3, 4)[:, :3]
    alpha_b = np.clip(np.round(np.asarray(alpha, dtype=np.float64) * 255), 0, 255).astype(np.uint8)
    col_b = np.clip(np.round(((np.asarray(rgb, dtype=np.float64) - 0.5) * (0.15 / SH_C0) + 0.5) * 255), 0, 255).astype(np.uint8)
    s = np.clip(np.round((np.log(np.maximum(np.asarray(scale, dtype=np.float64), 1e-6)) + 10) * 16), 0, 255).astype(np.uint8)
    rot_b = np.full((n, 3), 128, dtype=np.uint8)            # 各向同性,旋转取单位四元数
    raw = (struct.pack('<IIIBBBB', SPZ_MAGIC, 2, n, 0, fractional_bits, 0, 0) + pos_b.tobytes() + alpha_b.tobytes()
           + col_b.tobytes() + np.repeat(s[:, None], 3, axis=1).tobytes() + rot_b.tobytes())
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.part')
    tmp.write_bytes(gzip.compress(raw, compresslevel=6))
    os.replace(tmp, path)
    return path


def read_spz(path: Path) -> dict:
    """SPZ v2 读回(测试 / 自检用):{xyz, rgb, scale, alpha, count}。"""
    import numpy as np
    raw = gzip.decompress(Path(path).read_bytes())
    magic, ver, n, sh, fb, _, _ = struct.unpack('<IIIBBBB', raw[:16])
    if magic != SPZ_MAGIC or ver != 2:
        raise AtlasError(f'不是 SPZ v2:{path}')
    o = 16
    b = np.frombuffer(raw, dtype=np.uint8, count=n * 9, offset=o).reshape(n * 3, 3)
    v = (b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16))
    v = np.where(v >= 1 << 23, v - (1 << 24), v)
    xyz = (v / float(1 << fb)).reshape(n, 3)
    o += n * 9
    alpha = np.frombuffer(raw, dtype=np.uint8, count=n, offset=o) / 255.0
    o += n
    rgb = (np.frombuffer(raw, dtype=np.uint8, count=n * 3, offset=o).reshape(n, 3) / 255.0 - 0.5) * (SH_C0 / 0.15) + 0.5
    o += n * 3
    scale = np.exp(np.frombuffer(raw, dtype=np.uint8, count=n * 3, offset=o).reshape(n, 3)[:, 0] / 16.0 - 10)
    return {'xyz': xyz, 'rgb': rgb, 'scale': scale, 'alpha': alpha, 'count': n, 'sh_degree': sh}


# ---------------------------------------------------------------- fusion
def unproject_view(rgb, z, view: dict, *, stride: int = 2, sky_m: float = 200.0):
    """一张视图 → 点:世界坐标 (N,3)、颜色 (N,3) 0–1、像素足迹(米,点间距)。z = 本机位白模 z 深度(inf = 无几何,放到 sky_m 远处);
    深度断层(与邻点比 > 8%)处的像素剔除,免得生成图在物体边缘的颜色抹到前后两层上。"""
    import cv2
    import numpy as np
    h, w = z.shape
    if rgb.shape[:2] != (h, w):
        rgb = cv2.resize(rgb, (w, h), interpolation=cv2.INTER_AREA)
    pos, r, u, f = _basis(view['position'], view['target'])
    tan_h = math.tan(math.radians(float(view['fov_v_deg'])) / 2)
    fy = (h / 2) / tan_h
    hs, ws = h // stride, w // stride
    col = cv2.resize(rgb, (ws, hs), interpolation=cv2.INTER_AREA).reshape(-1, 3).astype(np.float32) / 255.0
    ys = (np.arange(hs) * stride + stride / 2)
    xs = (np.arange(ws) * stride + stride / 2)
    zz = z[ys.astype(int)][:, xs.astype(int)]
    finite = np.isfinite(zz)
    zf = np.where(finite, zz, np.nan)
    pad = np.pad(zf, 1, mode='edge')
    with np.errstate(invalid='ignore', divide='ignore'):
        ratio = np.ones_like(zf)
        for dy, dx in ((0, 1), (1, 0), (0, -1), (-1, 0)):
            nb = pad[1 + dy:1 + dy + hs, 1 + dx:1 + dx + ws]
            ratio = np.fmax(ratio, np.fmax(zf / nb, nb / zf))
    edge = finite & (ratio > 1.08)
    keep = (~edge).ravel()
    gx, gy = np.meshgrid((xs - w / 2) / fy, -(ys - h / 2) / fy)
    dirs = gx[..., None] * r + gy[..., None] * u + f                      # 光轴分量 = 1,乘 z 深度即相机系坐标
    zv = np.where(finite, zz, 0.0)
    sky = ~finite
    foot = stride * zv / fy
    if sky.any():                                                       # 无几何(天空 / 门窗外远处):放到 sky_m 远处,隔 4 点取 1、足迹 ×4
        dn = dirs / np.linalg.norm(dirs, axis=2, keepdims=True)
        dirs = np.where(sky[..., None], dn, dirs)
        zv = np.where(sky, sky_m, zv)
        lattice = np.zeros_like(sky)
        lattice[::4, ::4] = True
        foot = np.where(sky, stride * 4 * sky_m / fy, foot)
        keep &= (~sky | lattice).ravel()
    pts = (pos + dirs * zv[..., None]).reshape(-1, 3)
    return pts[keep], col[keep], foot.ravel()[keep]


def _voxel_keep(pts, foot, voxel: float):
    """分级体素去重:每个点按自己的像素足迹落到 voxel×2^n 的格子(远处 / 天空的点格子大),同级同格只留足迹最小
    (离自己机位最近、最清楚)的点。返回保留下标。"""
    import numpy as np
    order = np.argsort(foot, kind='stable')
    f = foot[order]
    level = np.clip(np.floor(np.log2(np.maximum(f, voxel) / voxel)), 0, 31).astype(np.int64)
    cell = np.floor(pts[order] / (voxel * np.exp2(level))[:, None]).astype(np.int64)
    cell = np.clip(cell + (1 << 17), 0, (1 << 18) - 1)              # 每轴 18 bit:1.5 cm 格子覆盖 ±1966 m
    key = (((level << 18 | cell[:, 0]) << 18 | cell[:, 1]) << 18) | cell[:, 2]
    _, first = np.unique(key, return_index=True)
    return order[first]


def to_raw(pts, camera: dict):
    """白模世界坐标 → 与 Marble 产物同一约定的 SPZ 原始坐标:以全景相机为原点、转掉锚点 yaw、OpenCV 轴(y 向下、z 向前)。
    视窗按 world.json#alignment 做 rotX(180°) → rotY(yaw) → +camera 就回到白模坐标。"""
    import numpy as np
    v = pts - np.asarray(camera['position'], dtype=np.float64)
    yaw = math.radians(float(camera.get('yaw_deg') or 0))
    c, s = math.cos(yaw), math.sin(yaw)
    lx = c * v[:, 0] - s * v[:, 2]
    lz = s * v[:, 0] + c * v[:, 2]
    return np.stack([lx, -v[:, 1], -lz], axis=1)


def fuse(views: list[dict], camera: dict, out: Path, *, voxel: float = 0.015, log=print) -> dict:
    """views[i] = {rgb, z, view};按白模深度反投影、体素去重,写 splats_full_res.spz 与 splats_500k.spz。返回 files 段与统计。"""
    import numpy as np
    parts = [unproject_view(v['rgb'], v['z'], v['view']) for v in views]
    pts = np.concatenate([p[0] for p in parts])
    col = np.concatenate([p[1] for p in parts])
    foot = np.concatenate([p[2] for p in parts])
    total = len(pts)
    if not total:
        raise AtlasError('视图里没有可用的像素,融合不出世界模型')
    files, stats = {}, {'points_in': int(total)}
    for name, cap in (('full_res', FULL_RES_CAP), ('500k', PREVIEW_CAP)):
        vx = voxel
        idx = _voxel_keep(pts, foot, vx)
        while len(idx) > cap:
            vx *= 1.35
            idx = _voxel_keep(pts, foot, vx)
        scale = np.maximum(foot[idx], vx) * 0.65            # 0.75 偏软、0.55 近景露网格纹(2026-10-09 实测)
        fname = f'splats_{name}.spz'
        write_spz(out / fname, to_raw(pts[idx], camera), col[idx], scale, np.full(len(idx), 0.9))
        files[name] = fname
        stats[name] = {'splats': int(len(idx)), 'voxel_m': round(vx, 4)}
        log(f'   {fname}: {len(idx):,} splats(体素 {vx * 100:.1f} cm)')
    return {'splats': files, 'stats': stats}


# ---------------------------------------------------------------- world generation(与 worldlabs.generate_world / finish_world 同一产物)
def _anchor_record(base: Path, sid: str, inp: dict) -> tuple[dict, bool]:
    """input.json 记的全景相机 → {position, yaw_deg} 与室内判定(室内补顶板参与深度求交)。"""
    from modules import scene_panos as sp
    cam = inp.get('camera') or {}
    anchor = {'anchor_id': inp.get('anchor_id'), 'position': [float(v) for v in cam.get('position') or [0, STATION_EYE_M, 0]],
              'yaw_deg': float(cam.get('yaw_deg') or 0)}
    scene = load_scene(base, sid)
    try:
        indoor = sp.anchor_indoor(base, sid, scene, anchor)
    except Exception:  # noqa: BLE001
        indoor = sp.is_indoor(base, sid)
    return anchor, bool(indoor)


def _depths(scene, indoor, views, log=print) -> list:
    out = []
    for i, v in enumerate(views):
        t0 = time.time()
        out.append(whitebox_depth(scene, indoor, v))
        if i == 0 or (i + 1) % 7 == 0 or i == len(views) - 1:
            log(f'   白模深度 {i + 1}/{len(views)}({time.time() - t0:.1f}s/张)')
    return out


def generate_world(base: Path, sid: str, text_prompt: str | None, *, out: Path, key: str | None = None, seed: int | None = None,
                   views: int | None = None, voxel_size: float | None = None, log=print) -> dict:
    """prepare_pano 之后调用:规划机位 → 白模深度 + 全景切图 → atlasChisel → 融合落盘。返回 world.json 记录。"""
    import cv2
    import numpy as np
    from modules import scene_panos as sp
    pano_path = out / 'pano.png'
    inp = read(out / 'input.json', None) or {}
    if not pano_path.is_file() or inp.get('source') != 'scene_pano':
        raise AtlasError('Atlas 世界模型须基于场景全景图(--source scene_pano,先 prepare_pano)')
    scene = load_scene(base, sid)
    anchor, indoor = _anchor_record(base, sid, inp)
    sources = source_views(anchor)
    budget = max_targets(len(sources))
    if views:
        budget = max(1, min(int(views), budget))
    plan = plan_views(scene, anchor, sp.scene_cameras(base, sid), budget=budget)
    log(f"Atlas {TASK}:锚点 {anchor['anchor_id']} @ {anchor['position']} yaw {anchor['yaw_deg']}°({'室内' if indoor else '室外'}),"
        f"目标机位 {len(plan)} 个(锚点环 {sum(v['kind'].startswith('anchor') for v in plan)} · 分镜 {sum(v['kind'] == 'shot' for v in plan)}"
        f" · 空地站位 {sum(v['kind'] == 'station' for v in plan)}),全景切图 {len(sources)} 张")
    depths = _depths(scene, indoor, plan, log=log)
    keep = [i for i, z in enumerate(depths) if np.isfinite(z).mean() >= 0.05]
    if len(keep) < len(plan):
        log(f'   {len(plan) - len(keep)} 个机位几乎看不到白模几何,去掉')
        plan, depths = [plan[i] for i in keep], [depths[i] for i in keep]
    if not plan:
        raise AtlasError(f'{sid}: 没有可用的目标机位')
    adir = out / 'atlas'
    adir.mkdir(parents=True, exist_ok=True)
    context = []
    for i, (v, z) in enumerate(zip(plan, depths)):
        png, z_min, z_max = encode_log_depth(z)
        (adir / f'ctx_{i:02d}_depth.png').write_bytes(png)
        v.update({'z_min': z_min, 'z_max': z_max})
        context.append({'camera': pinhole(v), 'depth': {'logDepthAsset': {'base64': _data_url(png, 'image/png')}, 'zMin': z_min, 'zMax': z_max}})
    pano = cv2.cvtColor(cv2.imread(str(pano_path), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
    source_frames = []
    for i, s in enumerate(sources):
        ok, jpg = cv2.imencode('.jpg', cv2.cvtColor(cut_pano(pano, anchor, s), cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 92])
        if not ok:
            raise AtlasError('全景切图编码失败')
        (adir / f'src_{i:02d}.jpg').write_bytes(jpg.tobytes())
        source_frames.append({'imageAsset': {'base64': _data_url(jpg.tobytes(), 'image/jpeg')}, 'camera': pinhole(s)})
    prompt = (text_prompt or '').strip() or wl.default_prompt(base, sid, panorama=False)
    body = {'contextFrames': context, 'sourceFrames': source_frames, 'targetCameras': [c['camera'] for c in context],
            'prompt': prompt, 'enhancePrompt': True}
    if seed is not None:
        body['modelParameters'] = {'seed': int(seed)}
    if voxel_size:
        body['voxelSize'] = float(voxel_size)
    idem = f'videoagents-{uuid.uuid4().hex}'
    wl._write_json(adir / 'request.json', {'task': TASK, 'anchor': anchor, 'indoor': indoor, 'views': plan, 'sources': sources,
                                           'prompt': prompt, 'seed': seed, 'voxel_size': voxel_size, 'idempotency_key': idem,
                                           'written_at': wl._now()})
    mb = len(json.dumps(body)) / 1e6
    log(f'提交 {TASK}({len(plan)} 个目标机位,请求 {mb:.1f} MB)…')
    op = _request('POST', f'/tasks:{TASK}', body, timeout=600, idempotency_key=idem)
    op_id = op.get('id') or op.get('operation_id')
    if not op_id:
        raise AtlasError(f'{TASK} 提交成功但没有返回 operation id:{json.dumps(op, ensure_ascii=False)[:600]}')
    wl._write_json(out / 'generate.json', {'provider': PROVIDER, 'operation_id': op_id, 'submitted_at': wl._now(), 'model': TASK,
                                           'seed': seed, 'text_prompt': prompt, 'targets': len(plan)})
    log(f'operation {op_id},轮询中…')
    return finish_world(base, sid, op_id, out=out, key=key, log=log, depths=depths, done_op=op if op.get('done') else None)


def finish_world(base: Path, sid: str, operation_id: str, *, out: Path, key: str | None = None, log=print,
                 depths: list | None = None, done_op: dict | None = None) -> dict:
    """轮询 atlasChisel 结果、下载各视图、融合成高斯泼溅并写 <out>/world.json。可用于中断后续接(--resume,白模深度按 request.json 重算)。"""
    import cv2
    adir = out / 'atlas'
    req = read(adir / 'request.json', None) or {}
    plan = req.get('views') or []
    if not plan:
        raise AtlasError(f'{out}: 缺少 atlas/request.json(机位清单),无法续接')
    op = done_op or poll_operation(operation_id, progress=lambda o: log(f"  … {(o.get('metadata') or {}).get('state') or 'running'}"))
    resp = op.get('response') or {}
    frames = resp.get('frames') or []
    if len(frames) != len(plan):
        raise AtlasError(f'{TASK} 返回 {len(frames)} 帧,与目标机位 {len(plan)} 个不符:{json.dumps(op, ensure_ascii=False)[:800]}')
    if depths is None:
        log('续接:按机位清单重算白模深度…')
        depths = _depths(load_scene(base, sid), bool(req.get('indoor')), plan, log=log)
    views = []
    for i, fr in enumerate(frames):
        target = fetch_asset(fr.get('imageAsset') or {}, adir / f'view_{i:02d}.png')
        img = cv2.imread(str(target), cv2.IMREAD_COLOR)
        if img is None:
            raise AtlasError(f'视图 {i} 下载后无法解码:{target}')
        views.append({'rgb': cv2.cvtColor(img, cv2.COLOR_BGR2RGB), 'z': depths[i], 'view': plan[i]})
    log(f'已下载 {len(views)} 张视图,按白模深度融合成高斯泼溅…')
    inp = read(out / 'input.json', None) or {}
    cam = inp.get('camera') or {'position': req['anchor']['position'], 'yaw_deg': req['anchor'].get('yaw_deg', 0)}
    fused = fuse(views, cam, out, log=log)
    thumb = cv2.resize(cv2.cvtColor(views[0]['rgb'], cv2.COLOR_RGB2BGR), (640, 360), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(out / 'thumbnail.jpg'), thumb, [cv2.IMWRITE_JPEG_QUALITY, 90])
    gen_rec = read(out / 'generate.json', None) or {}
    record = {
        'schema_version': wl.SCHEMA, 'scene_id': sid, 'key': key, 'written_at': wl._now(), 'provider': PROVIDER,
        'world_id': f'atlas-{operation_id}', 'display_name': ' '.join(x for x in (base.name, sid, key) if x), 'model': TASK,
        'world_marble_url': None, 'caption': resp.get('promptUsed'), 'semantics_metadata': {},
        'files': {'splats': fused['splats'], 'thumbnail': 'thumbnail.jpg'},
        'alignment': {
            'convention': 'atlas: splats written in OpenCV axes relative to the pano camera (whitebox metres) → rotX(180°) → rotY(yaw) + camera',
            'camera': cam, 'yaw_deg': cam.get('yaw_deg', 0.0), 'metric_scale_factor': 1.0, 'ground_plane_offset': 0.0,
            'scale_fix': 1.0, 'scale_fix_basis': 'atlas: depth from whitebox (metric)', 'yaw_fix_deg': 0.0,
        },
        'input': inp,
        'atlas': {'task': TASK, 'operation_id': operation_id, 'request_id': resp.get('requestId'), 'prompt_used': resp.get('promptUsed'),
                  'views': [{**{k: v[k] for k in ('kind', 'label', 'position', 'target', 'fov_v_deg')}, 'file': f'atlas/view_{i:02d}.png'}
                            for i, v in enumerate(plan)],
                  'fusion': fused['stats']},
        'steps': {'generate': {**gen_rec, 'operation_id': operation_id, 'finished_at': wl._now()}},
    }
    wl._write_json(out / 'world.json', record)
    log(f"saved: {out / 'world.json'}  world_id={record['world_id']}")
    return record

