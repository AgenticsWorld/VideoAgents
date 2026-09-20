"""场景全景锚点(scene panos,2026-09-10):按白模几何在场景内少数「锚点」出 360° 等距柱状全景,分镜背景图一律由全景按本镜机位
重投影后二次生成,不再直接基于白模帧 + 俯视图出图(那样多张背景图互相不一致)。规则见 docs/scene_panos.md。

数据(场景级资产,跨组跨集复用):assets/concepts/scenes/<sid>/panos/
  index.json                 schema scene_panos.v1:anchors[]{anchor_id, position[x,y,z], yaw_deg, source auto|manual, locked,
                             serves[](镜号/角色), panos{<scheme_id>: {file, mode fresh|chain|relight, parent, channel, seed, size, …}}},
                             indoor, planned_at, blocked{reason, provider, model, at}(图像模型不支持全景时写入,预览页据此提示用户换模型)
  <anchor_id>/whitebox_pano.jpg   白模彩色全景(出全景的第一参考图)          depth_pano.npy / depth_pano.json  径向深度(米)+ 相机记录
  <anchor_id>/<scheme>.png        该光照方案的真实全景(2:1)                <scheme>.json  提示词/参考图/渠道
  <anchor_id>/<scheme>.chain_<parent>.jpg   由父锚点全景重投影到本锚点球面的参考图(链式补洞用,带空洞)

锚点规划(问题「不想每个分镜位置都出全景」):全景数量由机位覆盖决定——锚点 a 可服务机位 c 须同时满足
  ① 水平距离 ≤ max(SERVE_MIN_M, SERVE_RATIO × 机位到主体距离) 且 ≤ SERVE_MAX_M(视差可接受,重投影空洞少);
  ② 锚点到机位、锚点到主体两条线段不穿过白模实体(白模包围盒,按高度判);
贪心集合覆盖:候选点 = 场景 0.5 m 网格上不在实体内、离高实体 ≥ ANCHOR_CLEARANCE_M 的点(贴墙全景一半是墙,信息量低),
每轮取能服务最多未覆盖机位的候选,直到全覆盖;仍落网的机位以自身机位为锚点(全景与一张背景图成本相同,不会更贵)。
手动:index.json 里 locked=true 的锚点规划时原样保留(CLI --anchor x,z 添加),只为未覆盖机位补锚点。

多全景一致性(问题「一个场景多张全景会不一致」):同一光照方案第二个起的锚点全景走「链式补洞」——把已成全景用白模深度
重投影到新锚点球面作参考图,提示词声明「同一地点换位置拍的,已有物体保持外观,只补空白区」;出图顺序按与已成锚点距离近的先出。
时段变化(白天/夜晚):同一锚点已有其它方案的全景时走「保结构重打光」(以已成全景为第一参考图、白模全景第二,只改光照),
不重画内容;两条路都以 `mode` 记在索引里。

全景中心(问题「自动还是手动」):默认自动(上述规划),预览页「全景图」板块显示每个锚点在俯视图中的坐标;用户要改就
`code/render_scene_panos.py --anchor x,z --force` 或直接改 index.json 后 --force 重出。
预览页「创建全景图」(2026-09-13):俯视图上点一个坐标 → add_manual_anchor 加锁定锚点 → ensure_scene_panos(only=[新锚点], schemes={所选方案})
只出这一张(链式/重打光规则照旧),其它锚点与背景图不动;后台任务 = CLI --anchor x,z[,yaw] --only-new --scheme <slug>。
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import math
import os
import re
from pathlib import Path

from modules.whitebox import component, read

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'apps/web/static'
SCHEMA = 'scene_panos.v1'
PANOS_DIR = 'panos'
PANO_SIZE = '2880x1440'          # 图像模型出图尺寸(严格 2:1)
DEPTH_SIZE = (2048, 1024)        # 白模深度全景尺寸(重投影时按全景尺寸重采样)
DEPTH_CUBE = 1024
SERVE_RATIO = 0.5                # 锚点—机位距离 ≤ 机位到主体距离的 50%(重投影只作参考,模型重绘,中等视差可接受)
SERVE_MIN_M = 3.0
SERVE_MAX_M = 10.0
ANCHOR_CLEARANCE_M = 1.2         # 锚点离高实体(墙/柱/货架)的最小水平距离
ANCHOR_LOW_CLEARANCE_M = 0.8     # 锚点离任何实体(含座椅等矮家具)的最小水平距离:贴着矮家具的全景里它们成大片拉伸,模型会重新解释
GRID_STEP_M = 0.5
ASPECT_TOLERANCE = 0.03          # 返回全景宽高比偏离 2:1 超过此值 = 模型不按全景尺寸出图
HOLE_INPAINT_MAX = 0.6
PLATE_HOLE_MAX = 0.5             # 分镜重投影空洞超过此值 → 换锚点/加锚点
CHAIN_INCLUDE_SOURCE = False      # 链式补洞是否再挂母全景原图:实测(dzg6 SCN-0002 A4/A5)模型会整张照抄原图、无视本锚点几何,默认关

PANO_PROJECTION_RULES = (
    "Output a single seamless 360-degree equirectangular panorama with an exact 2:1 aspect ratio covering the full sphere: "
    "the sky or ceiling zenith is stretched along the top edge, the ground directly below the camera (nadir) along the bottom edge, "
    "the horizon runs along the middle row, and the left and right edges continue into each other. "
)
# 开阔外景的成图先验是「一张 2:1 的广角风景照」(fengshen3 SCN-0110):外景另加硬约束,并在提示词末尾再说一遍(首段会被长清单稀释)
EXTERIOR_PROJECTION_RULES = (
    "This is NOT a wide-angle landscape photograph and has no single viewing direction: the image width is the full 360-degree turn, so the "
    "horizon line crosses the entire width at mid-height, the scenery continues all the way around, and what lies behind the camera appears "
    "at the far left and far right edges, which must join seamlessly. The top rows are the sky straight overhead smeared across the full "
    "width; the bottom rows are the ground right under the camera smeared across the full width, soft and heavily stretched, never a sharp "
    "detailed foreground. Straight things (shorelines, paths, boats, walls, poles rows) bend into curves exactly as the blocks in the "
    "blockout do. The sun or moon occupies one single compass direction only; the rest of the sky is lit accordingly. "
)
GUIDES_REF_RULE = (
    "The curved grid lines on the sky and ground of [Image {n}] and the short tick marks on its horizon are projection guides only: they "
    "show how straight lines on the ground and overhead bend in this equirectangular projection and where the nadir and zenith are — "
    "reproduce that curvature and stretching in the real ground texture, shoreline and clouds, and never draw the lines or ticks themselves. "
)
PANO_PROJECTION_TAIL = (
    "Final check before output: full-sphere equirectangular projection, zenith smeared along the top edge, nadir smeared along the bottom "
    "edge, horizon on the middle row across the whole width, left and right edges are the same direction and match pixel for pixel."
)
# 风格段里的单镜头构图 / 布光 / 人物用语:全景没有「主体」和「前中后景」,留着会把模型推回单视角电影画面
STYLE_COMPOSITION_WORDS = ('subject', 'layers of depth', 'depth of field', 'foreground', 'middle ground', 'midground', 'background',
                           'backlight', 'rim-lit', 'rim light', 'rimming', 'god ray', 'composition', 'framing', 'framed', 'close-up',
                           'bokeh', 'lens', 'silhouette', 'hair', 'skin', 'face', 'costume', 'hemp', 'silk', 'gauze', 'leather',
                           'colossal structure', 'sea of clouds')


def pano_style(style: str) -> str:
    """全景用的风格段:只留材质 / 色调 / 颗粒等与视角无关的分句。"""
    keep = [c.strip() for c in re.split(r'[;,。;,]', style or '') if c.strip()
            and not any(w in c.lower() for w in STYLE_COMPOSITION_WORDS)]
    return ', '.join(keep)
WHITEBOX_REF_RULE = (
    "[Image {n}] is a plain untextured 3D blockout render of exactly this panorama from exactly this camera: keep its "
    "projection, horizon height, and the position, size and outline of every block (walls, columns, counters, shelves, seats, "
    "gate posts, poles, trees, buildings, kerbs, road edges, furniture) exactly where they are, and turn them into the real "
    "photographic objects described below. Do not draw its grey blocks, flat shading, grid or any labels. "
)
LAYOUT_REF_RULE = (
    "[Image {n}] is the top-down plan of the same location; use it only to decide what each block is and what lies in each "
    "direction. Never reproduce the map, its top-down viewpoint, colors or graphics. "
)
CHAIN_REF_RULE = (
    "[Image {n}] is the finished panorama of this same location photographed from another standpoint a few metres away and "
    "re-projected to this camera, so it shows stretching and blank holes: every object, facade, material, colour, weather and "
    "light it shows is authoritative — keep all of it identical and in place, and paint only the blank or smeared areas with "
    "matching content, so the two panoramas read as one place at the same moment. "
)
CHAIN_SOURCE_RULE = (
    "[Image {n}] is that other panorama itself, un-warped: use it for the exact design, material, colour and orientation of every "
    "object (the same chairs facing the same way, the same shelves, counters, signs, floor and ceiling) — same place, same minute; "
    "take positions from [Image {m}] and the block layout, not from this image. "
)
RELIGHT_RULE = (
    "[Image {n}] is the finished panorama of exactly this location and camera at a different time of day: reproduce it "
    "pixel-aligned — identical geometry, objects, set dressing, materials and layout — and change only the lighting, sky, "
    "shadows, practicals and colour grade to the lighting described below. "
)
NEGATIVE = ('people, person, human figure, crowd, vehicles in motion, text, watermark, logo, grid lines, wireframe, grey untextured '
            'blocks, 3D render look, CGI, map, floor plan, split screen, collage, black borders, fisheye circle, cropped panorama, '
            'cube map, tiled grid')


class PanoError(RuntimeError):
    pass


class PanoProjectionError(PanoError):
    """成图不是等距柱状投影(2:1 的广角照片),或没有跟白模(画成了别的视点 / 建筑外观)→ 成图已改名 .rejected,本批停下(链式补洞会把错误投影一路传下去)。"""


class PanoUnsupported(PanoError):
    """当前图像模型不支持 2:1 全景 → 调用方停下并通知用户换模型。"""


def _now():
    return dt.datetime.now().isoformat(timespec='seconds')


# ---------------------------------------------------------------- paths / index
def panos_dir(base: Path, sid: str) -> Path:
    return base / 'assets/concepts/scenes' / component(sid) / PANOS_DIR


def load_index(base: Path, sid: str) -> dict:
    idx = read(panos_dir(base, sid) / 'index.json', None)
    if not isinstance(idx, dict) or idx.get('schema_version') != SCHEMA:
        idx = {'schema_version': SCHEMA, 'scene_id': component(sid), 'anchors': []}
    idx['anchors'] = [a for a in idx.get('anchors', []) if isinstance(a, dict) and a.get('anchor_id')]
    for a in idx['anchors']:
        a.setdefault('panos', {}); a.setdefault('serves', [])
    return idx


def save_index(base: Path, sid: str, idx: dict):
    d = panos_dir(base, sid); d.mkdir(parents=True, exist_ok=True)
    idx['written_at'] = _now()
    tmp = d / 'index.json.tmp'
    tmp.write_text(json.dumps(idx, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(tmp, d / 'index.json')


def scheme_slug(scheme_id: str | None, time_of_day: str | None = None) -> str:
    s = re.sub(r'[^A-Za-z0-9_-]+', '-', str(scheme_id or '').strip())
    if s:
        return s
    t = re.sub(r'[^A-Za-z0-9_-]+', '-', str(time_of_day or '').strip())
    return t or 'default'


# ---------------------------------------------------------------- geometry
def _inside(p, obj, margin=0.0) -> bool:
    x, y, z = p
    cx, cy, cz = obj['position']; sx, sy, sz = obj['size_m']
    yaw = float(obj.get('yaw') or 0)
    dx, dz = x - cx, z - cz
    c, s = math.cos(-yaw), math.sin(-yaw)
    lx, lz = dx * c - dz * s, dx * s + dz * c
    return abs(lx) <= sx / 2 + margin and abs(lz) <= sz / 2 + margin and abs(y - cy) <= sy / 2 + margin


def _clearance(p, objects) -> float:
    x, _, z = p
    best = float('inf')
    for o in objects:
        cx, _, cz = o['position']; sx, _, sz = o['size_m']
        yaw = float(o.get('yaw') or 0)
        dx, dz = x - cx, z - cz
        c, s = math.cos(-yaw), math.sin(-yaw)
        lx, lz = dx * c - dz * s, dx * s + dz * c
        best = min(best, math.hypot(max(abs(lx) - sx / 2, 0), max(abs(lz) - sz / 2, 0)))
    return best


def segment_blocked(a, b, objects, step=0.25) -> bool:
    """线段 a→b 是否穿过任一白模实体(按三维包围盒;两端各留 0.3 m 不算,机位/主体本身可能贴着实体)。"""
    length = math.dist(a, b)
    if length < 1e-6:
        return False
    n = max(2, int(length / step))
    for i in range(n + 1):
        t = i / n
        if t * length < .3 or (1 - t) * length < .3:
            continue
        p = [a[k] + (b[k] - a[k]) * t for k in range(3)]
        if any(_inside(p, o) for o in objects):
            return True
    return False


def tall_objects(scene: dict, height: float) -> list:
    """与全景相机同高的实体(墙/柱/货架…);矮家具不算「贴墙」。"""
    return [o for o in scene.get('objects', []) if (o['position'][1] + o['size_m'][1] / 2) > height - 0.3]


def can_serve(anchor_pos, cam: dict, scene: dict) -> bool:
    pos, tgt = cam['position'], cam['target']
    subject = math.hypot(tgt[0] - pos[0], tgt[2] - pos[2])
    limit = min(SERVE_MAX_M, max(SERVE_MIN_M, SERVE_RATIO * subject))
    if math.hypot(anchor_pos[0] - pos[0], anchor_pos[2] - pos[2]) > limit:
        return False
    objs = scene.get('objects', [])
    if segment_blocked(anchor_pos, [pos[0], anchor_pos[1], pos[2]], objs):
        return False
    return not segment_blocked(anchor_pos, tgt, objs)


def default_anchor_height(cameras: list) -> float:
    # 不低于 1.6 m:锚点高于座椅背/柜台等中高家具,遮挡少(重投影按机位射线求交,锚点高度不必等于机高)
    hs = sorted(c['position'][1] for c in cameras) or [1.6]
    return round(min(2.0, max(1.6, hs[len(hs) // 2])), 2)


def candidate_points(scene: dict, height: float) -> list:
    w, _, d = scene['dimensions_m']
    tall = tall_objects(scene, height)
    pts = []
    nx, nz = int(w / GRID_STEP_M), int(d / GRID_STEP_M)
    for i in range(1, nx):
        for j in range(1, nz):
            p = [round(-w / 2 + i * GRID_STEP_M, 3), height, round(-d / 2 + j * GRID_STEP_M, 3)]
            if any(_inside(p, o) for o in scene.get('objects', [])):
                continue
            if _clearance(p, tall) < ANCHOR_CLEARANCE_M or _clearance(p, scene.get('objects', [])) < ANCHOR_LOW_CLEARANCE_M:
                continue
            pts.append(p)
    return pts


def _cam_key(c):
    return f"{c.get('ep', '')}/{c['shot_id']}:{c.get('role', 'start')}"


def plan_anchors(scene: dict, cameras: list, existing: list | None = None, height: float | None = None) -> list:
    """贪心集合覆盖 → anchors[]{anchor_id, position, yaw_deg, source, locked, serves}。existing 里 locked 的锚点原样保留。"""
    height = height or default_anchor_height(cameras)
    anchors = [dict(a, serves=[]) for a in (existing or []) if a.get('locked')]
    uncovered = list(cameras)
    for a in anchors:
        a['position'][1] = a['position'][1] if a.get('position') and len(a['position']) == 3 else height
        served = [c for c in uncovered if can_serve(a['position'], c, scene)]
        a['serves'] = [_cam_key(c) for c in served]
        uncovered = [c for c in uncovered if c not in served]
    cands = candidate_points(scene, height)
    # 机位本身也作候选(空旷处网格点可能都太远);机位在白模地面之外时夹回地面边缘内 0.5 m(白模外半球没有几何,全景只能瞎补)
    w, _, d = scene['dimensions_m']
    for c in cameras:
        p = [max(-w / 2 + .5, min(w / 2 - .5, c['position'][0])), height, max(-d / 2 + .5, min(d / 2 - .5, c['position'][2]))]
        if not any(_inside(p, o) for o in scene.get('objects', [])):
            cands.append(p)
    while uncovered and cands:
        best = None
        for p in cands:
            served = [c for c in uncovered if can_serve(p, c, scene)]
            if not served:
                continue
            spread = sum(math.hypot(p[0] - c['position'][0], p[2] - c['position'][2]) for c in served) / len(served)
            score = (len(served), -spread, _clearance(p, tall_objects(scene, height)))
            if best is None or score > best[0]:
                best = (score, p, served)
        if best is None:
            break
        _, p, served = best
        anchors.append({'anchor_id': '', 'position': [round(v, 3) for v in p], 'yaw_deg': 0.0, 'source': 'auto',
                        'locked': False, 'serves': [_cam_key(c) for c in served]})
        uncovered = [c for c in uncovered if c not in served]
    for c in uncovered:   # 仍无法覆盖(被实体包死的机位):以机位自身为锚点(同样夹回地面范围)
        anchors.append({'anchor_id': '', 'position': [round(max(-w / 2 + .5, min(w / 2 - .5, c['position'][0])), 3), height,
                                                       round(max(-d / 2 + .5, min(d / 2 - .5, c['position'][2])), 3)],
                        'yaw_deg': 0.0, 'source': 'auto-self', 'locked': False, 'serves': [_cam_key(c)]})
    used = {a['anchor_id'] for a in anchors if a.get('anchor_id')}
    n = 1
    for a in anchors:
        if not a.get('anchor_id'):
            while f'A{n}' in used:
                n += 1
            a['anchor_id'] = f'A{n}'; used.add(a['anchor_id']); n += 1
    return anchors


# ---------------------------------------------------------------- cameras across episodes
def scene_cameras(base: Path, sid: str, eps: list | None = None) -> list:
    """本场景在各集白模里的全部机位(镜首 + 镜尾各一条)+ 组光照方案。"""
    return cameras_by_scene(base, eps).get(sid, [])


def cameras_by_scene(base: Path, eps: list | None = None) -> dict[str, list]:
    """一次读完各集白模 episode.json + shot_list.json,按 scene_id 分组返回机位。
    场景预览页要给每个场景算方案候选,逐场景调 scene_cameras 会把整集 JSON 反复解析(场景数 × 集数),须先调本函数再传 cameras=。"""
    from modules.shot_plates import plate_roles
    out: dict[str, list] = {}
    for f in sorted((base / 'directing').glob('ep*/whitebox/episode.json')):
        ep = f.parent.parent.name
        if eps and ep not in eps:
            continue
        episode = read(f, {}) or {}
        source = read(base / 'directing' / ep / 'shot_list.json', {}) or {}
        raw = {g['group_id']: g for g in source.get('generation_groups', [])}
        for g in episode.get('groups', []):
            sid = g.get('scene_id')
            if not sid:
                continue
            rg = raw.get(g['group_id'], {})
            scheme = scheme_slug(rg.get('lighting_scheme_id'), rg.get('time_of_day'))
            for cam in g.get('cameras', []):
                roles = plate_roles(cam)['roles']
                for role in roles:
                    key = cam['keyframes'][0] if role == 'start' else cam['keyframes'][-1]
                    out.setdefault(sid, []).append({'ep': ep, 'group_id': g['group_id'], 'shot_id': cam['shot_id'], 'role': role,
                                'position': list(key['position']), 'target': list(key['target']), 'fov': float(key['fov']),
                                'scheme': scheme, 'time_of_day': rg.get('time_of_day')})
    return out


def is_indoor(base: Path, sid: str) -> bool:
    """室内 = 渲染全景时补天花板。依据:lighting.json 任一方案 weather 含「室内」/indoor,或 architecture.json 文本含室内词。"""
    bdir = base / 'bible/scenes' / component(sid)
    text = json.dumps(read(bdir / 'lighting.json', {}) or {}, ensure_ascii=False)
    arch = json.dumps((read(bdir / 'architecture.json', {}) or {}).get('form') or '', ensure_ascii=False)
    blob = (text + arch).lower()
    if any(k in blob for k in ('室内', 'indoor', 'interior')) and not any(k in arch.lower() for k in ('室外', 'outdoor', 'exterior', 'street')):
        return True
    # 子场景常常只有 whitebox.json、没有 lighting / architecture(fengshen3 SCN-0140「…前厅正堂(内景)」被判成室外 → 不补顶、厅堂上方画成天空):
    # 再看场景登记的 int_ext 与场景名
    layout = read(base / 'assets/concepts/scenes' / component(sid) / 'layout.json', {}) or {}
    entry = {}
    idx = read(base / 'bible/scenes/index.json', {}) or {}
    for it in (idx.get('scenes') if isinstance(idx, dict) else idx) or []:
        if isinstance(it, dict) and it.get('id') == sid:
            entry = it
    int_ext = str(entry.get('int_ext') or '').upper()
    if int_ext:
        return int_ext.startswith('INT')
    name = f"{entry.get('name') or ''} {layout.get('scene_name') or ''}".lower()
    return any(k in name for k in ('内景', '室内', 'interior', '(int')) and not any(k in name for k in ('外景', '室外', 'exterior'))


# ---------------------------------------------------------------- whitebox depth pano (Playwright)
GUIDES_VERSION = 1               # 外景白模全景投影引导线版本;depth_pano.json 的 guides 低于此值 → 出全景前重渲白模(本机、不花钱)
SKY_PLANE_M = 25.0               # 虚拟天空网格平面离镜头的高度


def draw_projection_guides(color, valid, depth, cam_h: float, yaw_deg: float):
    """开阔外景白模只有几个小盒子贴着地平线,上半幅纯色、下半幅淡地面,看上去就是一张普通广角构图,图像模型读不出等距柱状投影
    (fengshen3 SCN-0110:2:1 成图是广角照片)。给无几何的天空与地面补世界直角网格:直线在等距柱状里弯成向天顶/天底汇聚的曲线,
    是这种投影最强的视觉签名;地平线补四向刻度。只画在无几何的天空与地面像素上,不盖白模块体;不动深度全景。"""
    import numpy as np
    from PIL import Image
    h, w = depth.shape
    img = np.asarray(color.convert('RGB').resize((w, h)), dtype=np.float64).copy()
    dirs = _equirect_rays(yaw_deg, w, h).reshape(h, w, 3)
    dy = dirs[..., 1]
    pix = math.pi / h                                        # 每像素弧度
    cam_h = max(float(cam_h), 0.3)

    def grid(t, spacing):
        """平面上世界直角网格的线覆盖度 0–1:线宽恒 ~1.6 px,格子小于 ~6 px 时淡出(免地平线处摩尔纹)。"""
        x = dirs[..., 0] * t; z = dirs[..., 2] * t
        foot = np.maximum(t * pix / np.maximum(np.abs(dy), 1e-3), 1e-6)   # 一像素在该平面上的跨度(含掠射拉长)
        # 网格线落在相对镜头 (k+½)·spacing 处:镜头在格心,没有线穿过天顶/天底(穿过的线会成整幅高的竖直线,反而像分屏)
        d = np.minimum(np.abs(x / spacing % 1 - .5), np.abs(z / spacing % 1 - .5)) * spacing
        line = np.clip(1.6 - d / foot, 0, 1)
        return line * np.clip((spacing / foot - 6) / 10, 0, 1)

    with np.errstate(divide='ignore', invalid='ignore'):
        t_ground = np.where(dy < -1e-4, cam_h / -dy, np.inf)
        t_sky = np.where(dy > 1e-4, SKY_PLANE_M / dy, np.inf)
    # 地面像素 = 下半球且(无几何 或 深度落在 y=0 平面上);天空像素 = 上半球无几何
    ground = (dy < 0) & (~valid | (np.abs(depth - t_ground) < 0.03 * t_ground + 0.05))
    sky = (dy > 0) & ~valid
    tg = np.where(np.isfinite(t_ground), t_ground, 1e9); ts = np.where(np.isfinite(t_sky), t_sky, 1e9)
    a_ground = np.maximum(grid(tg, 1.0) * .8, grid(tg, 5.0)) * ground      # 5 = 奇数倍,粗线与细线重合
    a_sky = np.maximum(grid(ts, 10.0) * .8, grid(ts, 50.0)) * sky
    for alpha, rgb in ((a_ground, (70, 96, 70)), (a_sky, (96, 108, 128))):
        img += (np.array(rgb, dtype=np.float64) - img) * (alpha * .85)[..., None]
    # 地平线四向刻度(无文字,免模型照抄标签):前/右/后/左各一道竖线,只落在天空/地面像素
    free = ground | sky
    mid = h // 2
    for frac in (0.0, .25, .5, .75, 1.0):
        c = min(w - 1, int(round(frac * (w - 1))))
        band = np.zeros((h, w), dtype=bool); band[mid - h // 24: mid + h // 24, max(0, c - 1): c + 2] = True
        img[band & free] = (60, 60, 60)
    return Image.fromarray(np.clip(img, 0, 255).astype('uint8'))


def render_whitebox_pano(base: Path, sid: str, anchor: dict, *, indoor: bool, log=print, out_dir: Path | None = None) -> dict:
    """无头 Chromium 渲径向深度全景 + 白模彩色全景到 panos/<anchor>/(out_dir 指定则写到该目录,
    世界模型链 modules/worldlabs.py 在场景没有锚点时把自动机位的全景渲进 world/,不进 panos 索引)。"""
    try:
        import numpy as np
        from PIL import Image
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise PanoError('缺少 numpy / Pillow / Playwright,请安装项目依赖并运行 python -m playwright install chromium') from e
    from modules.whitebox import load_scene
    scene = load_scene(base, sid)
    out = out_dir if out_dir is not None else panos_dir(base, sid) / anchor['anchor_id']
    out.mkdir(parents=True, exist_ok=True)
    width, height = DEPTH_SIZE
    camera = [float(v) for v in anchor['position']]
    yaw = math.radians(float(anchor.get('yaw_deg') or 0))
    payload = {'scene': scene, 'ceiling': bool(indoor)}
    with sync_playwright() as p:
        kwargs = {'headless': True, 'args': ['--enable-webgl', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--allow-file-access-from-files']}
        if os.environ.get('VIDEOAGENTS_CHROMIUM'):
            kwargs['executable_path'] = os.environ['VIDEOAGENTS_CHROMIUM']
        browser = p.chromium.launch(**kwargs)
        try:
            page = browser.new_page(viewport={'width': 256, 'height': 128})
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.add_init_script('window.whiteboxPanoData = ' + json.dumps(payload, ensure_ascii=False) + ';')
            page.goto((STATIC / 'whitebox-pano-export.html').as_uri())
            page.wait_for_function('window.whiteboxPanoReady === true', timeout=60000)
            page.evaluate('() => window.whiteboxPano.load()')
            log(f"   渲白模全景 {anchor['anchor_id']} @ {camera} yaw {anchor.get('yaw_deg', 0)}° {'室内(补顶)' if indoor else '室外'} …")
            result = page.evaluate('(o) => window.whiteboxPano.render(o)',
                                   {'position': camera, 'yaw': yaw, 'cube': DEPTH_CUBE, 'width': width, 'height': height})
            if errors:
                raise PanoError('渲染页报错:' + ' | '.join(errors)[:1500])
        finally:
            browser.close()
    depth = np.frombuffer(base64.b64decode(result['depth_base64']), dtype='<f4').reshape(height, width).copy()
    valid = depth > 0
    raw_depth = depth.copy()
    if valid.mean() < 0.1:   # 开放场景(室外/机位在白模地面外)天地本就无几何,只拦「什么都没渲出来」
        raise PanoError(f"{anchor['anchor_id']}: 深度全景有效像素只有 {valid.mean():.0%},场景几何可能没渲染出来")
    z_max = float(depth[valid].max())
    depth[~valid] = z_max * 4      # 无几何(漏天/漏地)按远处理
    np.save(out / 'depth_pano.npy', depth)
    color_jpeg = base64.b64decode(result['color_jpeg'])
    if indoor:
        (out / 'whitebox_pano.jpg').write_bytes(color_jpeg)
    else:
        import io
        draw_projection_guides(Image.open(io.BytesIO(color_jpeg)), valid, raw_depth, camera[1],
                               float(anchor.get('yaw_deg') or 0)).save(out / 'whitebox_pano.jpg', quality=92)
    record = {'schema_version': SCHEMA, 'scene_id': sid, 'anchor_id': anchor['anchor_id'], 'written_at': _now(),
              'camera': {'position': camera, 'yaw_deg': float(anchor.get('yaw_deg') or 0), 'height_m': camera[1]},
              'size': [width, height], 'cube': DEPTH_CUBE, 'indoor': indoor, 'guides': 0 if indoor else GUIDES_VERSION, 'valid_fraction': round(float(valid.mean()), 4),
              'z_max': round(z_max, 3)}
    (out / 'depth_pano.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return record


def whitebox_pano_stale(base: Path, sid: str, anchor: dict) -> bool:
    return whitebox_pano_stale_at(panos_dir(base, sid) / anchor['anchor_id'], anchor)


def whitebox_pano_stale_at(out: Path, anchor: dict) -> bool:
    """目录 out 下的白模深度全景是否缺失/与锚点位置、yaw 不符。"""
    rec = read(out / 'depth_pano.json', None)
    if not rec or not (out / 'depth_pano.npy').is_file() or not (out / 'whitebox_pano.jpg').is_file():
        return True
    cam = rec.get('camera') or {}
    return (math.dist(cam.get('position', [9e9] * 3), anchor['position']) > .05
            or abs(float(cam.get('yaw_deg') or 0) - float(anchor.get('yaw_deg') or 0)) > .5)


# ---------------------------------------------------------------- image model capability
def pano_support(cfg: dict) -> tuple[bool, str]:
    """当前图像渠道能否按任意宽高(2880×1440,2:1)出图。不能 → 全景链停下,让用户换模型(不自行换)。"""
    provider = str(cfg.get('provider') or '')
    model = str(cfg.get('model') or '')
    m = model.lower()
    if provider in ('volcengine', 'byteplus'):
        return True, 'Seedream 系列接受任意 size'
    if provider in ('comfyui', 'agentics'):
        return True, '按 width/height 传入工作流'
    if provider == 'fal':
        if 'nano-banana' in m or 'gpt-image' in m or 'kontext' in m:
            return False, 'Fal 该家族只接受固定宽高比枚举(无 2:1)'
        if 'hunyuan-image' in m:
            return False, 'Fal HunyuanImage 无参考图端点'
        return True, 'image_size {width,height}'
    if provider in ('minimax', 'openrouter'):
        return False, f'{provider} 图像模型只接受固定宽高比,不支持 2:1 全景'
    return False, f'{provider} 渠道未验证支持 2:1 全景'


def mark_blocked(base: Path, sid: str, idx: dict, cfg: dict, reason: str):
    idx['blocked'] = {'reason': 'pano_unsupported', 'provider': cfg.get('provider'), 'model': cfg.get('model'),
                      'detail': reason, 'at': _now()}
    save_index(base, sid, idx)


def check_pano_support(base: Path, sid: str, idx: dict, log=print) -> dict:
    from modules.genmedia import get_config, image_pref_env
    with image_pref_env('panos'):   # 场景预览页「🌐 全景模型」的选择(空=全局,不回退到本页「🎨 图像模型」)
        cfg = get_config('image')
    ok, reason = pano_support(cfg)
    if not ok:
        mark_blocked(base, sid, idx, cfg, reason)
        raise PanoUnsupported(f"当前图像模型 {cfg.get('provider')}/{cfg.get('model')} 不支持 2:1 等距柱状全景({reason});"
                              "全景图与分镜背景图已暂停。请用户切换图像模型(如火山 Seedream 5.0 pro)后重跑:场景预览页顶部「🌐 全景模型」有选则改那里,否则改控制台「🎨 生成模型」。")
    if idx.get('blocked'):
        idx.pop('blocked', None); save_index(base, sid, idx)
    return cfg


# ---------------------------------------------------------------- prompt
def _flat(v):
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, list):
        return '; '.join(x for x in (_flat(i) for i in v) if x)
    if isinstance(v, dict):
        return '; '.join(x for x in (_flat(i) for i in v.values()) if x)
    return ''


def lighting_scheme(base: Path, sid: str, scheme: str) -> dict:
    doc = read(base / 'bible/scenes' / component(sid) / 'lighting.json', {}) or {}
    for s in doc.get('schemes', []):
        if scheme_slug(s.get('scheme_id') or s.get('id')) == scheme:
            return s
    return {}


# ---------------------------------------------------------------- 窗/门洞口(只供视频提示词接线 sync_shot_plates 的【场景】段用;
# 2026-09-14 曾接入全景提示词/全景透光机检与母图提示词/母图保真机检,两处实测效果不佳均已回退,只保留视频提示词里的洞口暗面句。)
NIGHT_WORDS = ('夜', '晚', 'night', 'midnight')
WINDOW_SOURCE_WORDS = ('窗', 'window', 'daylight', 'sunlight', 'sun ', '日光', '月光', 'moon', 'skylight', '天光', 'outside', '门外')


def opening_apertures(scene: dict, layout: dict) -> list[dict]:
    """白模里的窗/门洞口盒:墙段命名 room-<side>-<地标>-lintel(过梁)/-sill(窗台)→ 洞口 = 窗台顶到过梁底(无窗台的门洞自地面起),
    平面范围取过梁段;名称/kind 取布局地标(opening / entrance)。返回 [{id, name, kind, position, size_m}]。"""
    objs = {o['id']: o for o in scene.get('objects', []) if isinstance(o, dict) and o.get('id')}
    landmarks = {lm['id']: lm for lm in layout.get('landmarks', []) if isinstance(lm, dict) and lm.get('id')}
    out = []
    for oid, o in objs.items():
        m = re.fullmatch(r'room-([nsew])-(.+)-lintel', oid)
        if not m:
            continue
        side, lid = m.groups()
        sill = objs.get(f'room-{side}-{lid}-sill')
        lx, ly, lz = o['position']; sx, sy, sz = o['size_m']
        top = ly - sy / 2
        bottom = (sill['position'][1] + sill['size_m'][1] / 2) if sill else 0.0
        if top - bottom < 0.2:
            continue
        lm = landmarks.get(lid) or {}
        out.append({'id': lid, 'name': lm.get('name_en') or lm.get('name') or lid.replace('_', ' '),
                    'kind': lm.get('kind') or ('opening' if sill else 'entrance'),
                    'position': [lx, (top + bottom) / 2, lz], 'size_m': [sx, top - bottom, sz]})
    return out


def openings_rule(scheme: dict, apertures: list, time_of_day: str | None = None) -> tuple[str, str]:
    """(提示词句, 负面词):夜间方案且主光不是窗/门外来光 → 全部洞口写成不透光暗面;否则 ('', '')。"""
    tod = str(((scheme.get('condition') or {}).get('time_of_day')) or time_of_day or '').lower()
    blob = ' '.join(str(scheme.get(k) or '') for k in ('key_source', 'direction')).lower()
    if not apertures or not any(w in tod for w in NIGHT_WORDS) or any(w in blob for w in WINDOW_SOURCE_WORDS):
        return '', ''
    names = ', '.join(dict.fromkeys(a['name'] for a in apertures))
    sentence = (f"Every window and door opening of this location ({names}) is dark and unlit: paper or lattice windows read as dull, "
                "opaque surfaces darker than the surrounding wall, door openings show only unlit darkness beyond, and no light of any kind "
                "glows through or comes in from any window or doorway — the only light is the practical source named in the lighting sentence.")
    negative = ('glowing window, light shining through window, backlit window, moonlight through window, daylight, sunlight, sky light, '
                'bright doorway, light from outside')
    return sentence, negative


def openings_rule_zh(scheme: dict, apertures: list, time_of_day: str | None = None) -> str:
    """同 openings_rule 的中文句(Seedance 2.5 中文提示词的【场景】段用)。"""
    if not openings_rule(scheme, apertures, time_of_day)[0]:
        return ''
    names = '、'.join(dict.fromkeys(a['name'] for a in apertures))
    return f"本场为夜景，所有窗户与门口（{names}）都是不透光的暗面，没有任何光从窗或门透进来；画内唯一光源按本组光照描述。"


def openings_for(base: Path, sid: str, scheme_id: str | None, time_of_day: str | None = None) -> dict:
    """场景洞口 + 本方案的洞口光照规则:{'apertures', 'rule', 'rule_zh', 'negative', 'scheme'}。scheme_id 可为 id 或 slug。"""
    from modules.whitebox import load_scene
    sid = component(sid)
    try:
        scene = load_scene(base, sid)
    except Exception:  # noqa: BLE001
        scene = read(base / 'assets/concepts/scenes' / sid / 'whitebox.scene.json', {}) or {}
    layout = read(base / 'assets/concepts/scenes' / sid / 'layout.json', {}) or {}
    sch = lighting_scheme(base, sid, scheme_slug(scheme_id, time_of_day))
    apertures = opening_apertures(scene, layout)
    rule, negative = openings_rule(sch, apertures, time_of_day)
    return {'apertures': apertures, 'rule': rule, 'rule_zh': openings_rule_zh(sch, apertures, time_of_day), 'negative': negative, 'scheme': sch}


# ---------------------------------------------------------------- what this anchor can actually see
# 一个 SCN 同时含室内外(fengshen3 SCN-0046:云台 + 洞内主室)时,按整场景写的四向文字 / 物体清单 / 全域说明 / 俯视图会把墙外的
# 东西全喂给模型,洞内锚点被画成洞府外观定场图。以下按锚点对白模几何逐射线判可见性,提示词只写看得见的。
VIS_SIZE = (720, 360)
VIS_MIN_PX = 10                  # 少于此像素(720×360)= 看不见,不进清单
VIS_PROMINENT = 0.35             # 可见像素 / 无遮挡时应占像素 ≥ 此值 = 看得清;否则只是「从开口里瞥见」
SECTOR_OPEN_MAX = 0.10           # 某向地平线带里「看得出去」的像素占比低于此值 = 该向被墙挡死
OPEN_DISTANCE_M = 25.0
OUTDOOR_WORDS = ('云海', '云面', '云台', '台外', '山道', '星月', '星空', '月光', '月色', '天空', '天幕', '日光', '阳光', '崖前', '下不见底',
                 'sky', 'cloud', 'moon', 'star', 'sunlight', 'terrace', 'horizon', 'outdoor', 'exterior')
ENCLOSED_RULE = (
    "The camera is INSIDE an enclosed room: solid walls stand on every side and a solid ceiling is overhead, exactly as the blockout "
    "shows. This is not an exterior or establishing view of the building: never show the outside of the structure, open sky, moon, stars, "
    "clouds or a distant landscape, except the little that is visible through the door or window openings of the blockout, at exactly "
    "their position and size. "
)
_VIEW_CACHE: dict = {}
_FUNCTION_CHARS = set('的那这一处根株片座排块与和在被向自其之上下里内外旁侧边前后左右东西南北')
_EN_STOP = {'that', 'this', 'with', 'from', 'side', 'into', 'near', 'wall', 'room', 'main', 'inner', 'outer', 'stone', 'door'}


def anchor_view(scene: dict, anchor: dict, indoor: bool) -> dict:
    """锚点处对白模几何逐射线求最近命中 → 每物体可见像素 / 无遮挡应占像素,以及前右后左四向「看得出去」的占比。"""
    import numpy as np
    key = (json.dumps(scene.get('objects', []), sort_keys=True, default=str), tuple(anchor['position']), float(anchor.get('yaw_deg') or 0), indoor)
    if key in _VIEW_CACHE:
        return _VIEW_CACHE[key]
    w, h = VIS_SIZE
    dirs = _equirect_rays(float(anchor.get('yaw_deg') or 0), w, h)
    origin = [float(v) for v in anchor['position']]
    boxes = scene_boxes(scene, indoor)
    n_fixed = len(boxes) - len(scene.get('objects', []))          # 地板(+ 天花板)
    ts = np.stack([raycast(origin, dirs, [b]) for b in boxes], axis=0)
    nearest = np.argmin(ts, axis=0); tmin = ts.min(axis=0)
    nearest = np.where(np.isfinite(tmin), nearest, -1)
    # 贴地的薄块(水池面、铺装、门槛线;顶面 ≤ 8 cm)与地板同高,射线最近命中会判给地板:按地板命中点落在其占地内归还给它
    hitp = np.asarray(origin)[None, :] + dirs * np.where(np.isfinite(ts[0]), ts[0], 0.0)[:, None]
    down = np.isfinite(ts[0])
    for i, o in enumerate(scene.get('objects', [])):
        cy_, sy_ = float(o['position'][1]), float(o['size_m'][1])
        if cy_ + sy_ / 2 > 0.08:
            continue
        c, sn = math.cos(-float(o.get('yaw') or 0)), math.sin(-float(o.get('yaw') or 0))
        dx = hitp[:, 0] - float(o['position'][0]); dz = hitp[:, 2] - float(o['position'][2])
        inside = down & (np.abs(c * dx + sn * dz) <= float(o['size_m'][0]) / 2) & (np.abs(-sn * dx + c * dz) <= float(o['size_m'][2]) / 2)
        ts[n_fixed + i] = np.where(inside, ts[0], np.inf)
        nearest = np.where(inside & (nearest == 0), n_fixed + i, nearest)
    objects = {}
    for i, o in enumerate(scene.get('objects', [])):
        full = int(np.isfinite(ts[n_fixed + i]).sum()); seen = int((nearest == n_fixed + i).sum())
        objects[o['id']] = {'px': seen, 'full': full, 'fraction': round(seen / full, 3) if full else 0.0,
                            'cols': np.nonzero((nearest == n_fixed + i).reshape(h, w).any(axis=0))[0]}
    ids = nearest.reshape(h, w); t = tmin.reshape(h, w)
    band = slice(int(h * (90 - 20) / 180), int(h * (90 + 3) / 180))   # 地平线上 20° 到下 3°:越过矮家具、不看脚下地面
    sectors = {}
    for name, c0 in (('centre', .5), ('right', .75), ('behind', 0.0), ('left', .25)):
        cols = (np.arange(int((c0 - .125) * w), int((c0 + .125) * w)) % w)
        tt = t[band][:, cols]
        sectors[name] = {'open': round(float((~np.isfinite(tt) | (tt > OPEN_DISTANCE_M)).mean()), 3),
                         'wall_m': round(float(np.median(tt[np.isfinite(tt)])), 1) if np.isfinite(tt).any() else None,
                         'cols': set(cols.tolist())}
    view = {'objects': objects, 'sectors': sectors,
            'enclosed': bool(indoor and all(v['open'] < SECTOR_OPEN_MAX for v in sectors.values()))}
    _VIEW_CACHE[key] = view
    return view


def _object_name(oid: str, landmarks: dict, names: dict) -> tuple[str, str | None]:
    """(显示名, 地标 id 或 None)。只有物体基名与地标 id 同名/互为前缀才借用地标名(curtain_column 不该叫成 curtain_wall)。"""
    from modules.shot_plates import base_name, resolve_landmark
    bn = base_name(oid)
    lid = resolve_landmark(bn, landmarks)
    if lid and (lid == bn or lid.startswith(bn) or bn.startswith(lid)):
        return names[lid], lid
    return bn.replace('_', ' '), None


def sector_sentence(scene: dict, layout: dict, view: dict, sector: str) -> str:
    """被墙挡死的方向写什么:该向看得见的具名地标(看得清的在前,只从开口瞥见的另说),其余就是近处的墙。"""
    landmarks = {lm['id']: lm for lm in layout.get('landmarks', []) if 'xy' in lm}
    names = {lid: lm.get('name_en') or lm.get('name') or lid for lid, lm in landmarks.items()}
    sec = view['sectors'][sector]
    clear, glimpsed = [], []
    for o in scene.get('objects', []):
        v = view['objects'].get(o['id']) or {}
        if v.get('px', 0) < VIS_MIN_PX or not len(v['cols']):
            continue
        inside = sum(1 for c in v['cols'] if int(c) in sec['cols']) / len(v['cols'])
        name, lid = _object_name(o['id'], landmarks, names)
        if inside < 0.5 or not lid:
            continue
        bucket = clear if v['fraction'] >= VIS_PROMINENT else glimpsed
        if name not in bucket and name not in clear:
            bucket.append(name)
    wall = f"the solid enclosing wall of this space about {sec['wall_m']} m away" if sec.get('wall_m') else 'the solid enclosing wall of this space'
    text = (', '.join(clear) + ', in front of ' + wall) if clear else wall
    if glimpsed:
        text += '; only a narrow glimpse, through the opening in that wall, of ' + ', '.join(glimpsed)
    return text


def visible_landmark_words(scene: dict, layout: dict, view: dict) -> tuple[set, set]:
    """(看得清的地标的中文名/英文名, 看不清或看不见的地标名),给材质 / 光照分句过滤用。"""
    landmarks = {lm['id']: lm for lm in layout.get('landmarks', []) if 'xy' in lm}
    names = {lid: lm.get('name_en') or lm.get('name') or lid for lid, lm in landmarks.items()}
    seen = set()
    for o in scene.get('objects', []):
        v = view['objects'].get(o['id']) or {}
        lid = _object_name(o['id'], landmarks, names)[1]
        if lid and v.get('px', 0) >= VIS_MIN_PX and v.get('fraction', 0) >= VIS_PROMINENT:
            seen.add(lid)
    with_objects = {_object_name(o['id'], landmarks, names)[1] for o in scene.get('objects', [])} - {None}
    def words(lids):
        out = set()
        for lid in lids:
            for text in (landmarks[lid].get('name'), landmarks[lid].get('name_en')):
                text = str(text or '')
                for run in re.findall(r'[\u4e00-\u9fff]+', text):      # 中文:相邻二字(去掉带虚字的),「莲花池」→ 莲花 / 花池
                    out |= {run[i:i + 2] for i in range(len(run) - 1) if not set(run[i:i + 2]) & _FUNCTION_CHARS}
                out |= {w for w in re.findall(r'[a-z]{4,}', text.lower()) if w not in _EN_STOP}
        return out
    vis = words(seen)
    return vis, words(with_objects - seen) - vis


def indoor_clauses(text: str, visible: set, hidden: set, *, fine: bool = False) -> str:
    """封闭室内锚点:材质 / 光照段里讲墙外的分句剔掉——分句里看不见的地标用词 + 室外词的命中数 > 看得清的地标用词命中数。
    fine=True 连逗号也切(光照段一句里常是「洞内炉火,洞外星月云海」并列)。"""
    keep = []
    for clause in re.split(r'(?<=[;;。,,])' if fine else r'(?<=[;;。])', text or ''):
        c = clause.strip()
        if not c:
            continue
        low = c.lower()
        outside = sum(1 for w in hidden if w in low) + sum(1 for w in OUTDOOR_WORDS if w in low)
        if outside > sum(1 for w in visible if w in low):
            continue
        keep.append(c)
    return ' '.join(keep).strip(' ;;,,')


def object_inventory(scene: dict, layout: dict, anchor: dict, view: dict | None = None) -> list[str]:
    """锚点四周每个白模物体一句:名称、尺寸、离机位距离、罗盘方位与画面横向位置、长轴走向、朝向(有 <id>_back 靠背块的座椅
    自动推出「靠背在 X 侧,面朝 Y」)。白模只是方块,不写这些模型会在每个锚点各自猜(dzg6 SCN-0002 A1/A2 座椅方向相反)。"""
    from modules.shot_plates import bearing_deg, compass, orientation_axes
    ex, ez, _texts = orientation_axes(layout)
    landmarks = {lm['id']: lm for lm in layout.get('landmarks', []) if 'xy' in lm}
    names = {lid: lm.get('name_en') or lm.get('name') or lid for lid, lm in landmarks.items()}
    objs = {o['id']: o for o in scene.get('objects', [])}
    ax, _, az = anchor['position']
    yaw = math.radians(float(anchor.get('yaw_deg') or 0))
    lines = []
    for oid, o in objs.items():
        if oid.endswith('_back') and oid[:-5] in objs:
            continue
        vis = (view or {}).get('objects', {}).get(oid)
        if vis is not None and vis['px'] < VIS_MIN_PX:
            continue                                            # 被墙 / 别的块体完全挡住:不写,写了模型就会画出来
        cx, cy, cz = o['position']; sx, sy, sz = o['size_m']
        oyaw = float(o.get('yaw') or 0)
        dx, dz = cx - ax, cz - az
        dist = math.hypot(dx, dz)
        b = bearing_deg(dx, dz, ex, ez)
        lx = math.cos(-yaw) * dx - math.sin(-yaw) * dz; lz = math.sin(-yaw) * dx + math.cos(-yaw) * dz
        col = int(round((math.atan2(lx, -lz) + math.pi) / (2 * math.pi) * 100))
        name = _object_name(oid, landmarks, names)[0]
        top = cy + sy / 2
        desc = f"{name} ({oid}): {max(sx, sz):.1f} m long, {min(sx, sz):.1f} m deep, top {top:.1f} m above the floor"
        if dist < 0.6:
            desc += ', directly beneath/around the camera'
        else:
            desc += f", centred {dist:.1f} m from the camera to the {compass(b)} (about {col}% across the image from the left edge)"
        if max(sx, sz) >= 3 * min(sx, sz) and max(sx, sz) >= 2:
            axis = [math.cos(oyaw), -math.sin(oyaw)] if sx >= sz else [math.sin(oyaw), math.cos(oyaw)]
            b1 = bearing_deg(axis[0], axis[1], ex, ez)
            desc += f", its long side running {compass(b1)}–{compass((b1 + 180) % 360)}"
            if dist >= 0.6:
                rel = abs(((b1 - b + 180) % 360) - 180)
                desc += ' (seen broadside, crossing the view)' if min(rel, 180 - rel) > 60 else ' (receding away from the camera)' if min(rel, 180 - rel) < 30 else ''
        back = objs.get(oid + '_back')
        if back:
            fb = bearing_deg(cx - back['position'][0], cz - back['position'][2], ex, ez)
            desc += f"; its backrest is on the {compass((fb + 180) % 360)} side, so it faces {compass(fb)}"
            desc += ' — toward the camera' if abs(((fb - (b + 180)) % 360 + 180) % 360 - 180) < 45 else ' — away from the camera' if abs(((fb - b) % 360 + 180) % 360 - 180) < 45 else ''
        if vis is not None and vis['fraction'] < VIS_PROMINENT and vis['full'] > 4 * VIS_MIN_PX:
            desc += (f"; mostly hidden from this camera — only about {max(1, round(vis['fraction'] * 100))}% of it shows past the nearer "
                     "blocks or through an opening, exactly as in [Image 1], never bring it into full view")
        lines.append(desc + '.')
    return lines


def pano_prompt(base: Path, sid: str, scheme: str, anchor: dict, *, indoor: bool, mode: str, time_of_day: str | None = None) -> str:
    """空场景全景文字:场所 + 锚点站位 + 四向内容(俯视图四边说明,按 yaw 归到画面中心/左右/身后)+ 光照方案 + 材质年代 + 风格。"""
    from modules.shot_plates import standing_on, orientation_axes, strip_compass
    from modules.whitebox import load_scene
    sdir = base / 'assets/concepts/scenes' / component(sid)
    bdir = base / 'bible/scenes' / component(sid)
    layout = read(sdir / 'layout.json', {}) or {}
    arch = read(bdir / 'architecture.json', {}) or {}
    style = (read(base / 'bible/style.json', {}) or {}).get('style_fragment_en') or ''
    sch = lighting_scheme(base, sid, scheme)
    scene = load_scene(base, sid)
    name = re.sub(r'[(（].*?[)）]', '', layout.get('scene_name_en') or scene.get('name') or sid).strip()
    o = layout.get('orientation') or {}
    yaw = float(anchor.get('yaw_deg') or 0)
    # 画面中心 = 相机局部 -Z = 图上方(yaw 0);yaw 每 +90° 中心转向图左(绕 Y 正转)
    sides = ['top_of_map', 'left_of_map', 'bottom_of_map', 'right_of_map']
    k = int(round(yaw / 90)) % 4
    centre, left, behind, right = (strip_compass(o.get(sides[(k + i) % 4]) or '').lstrip('—-:: ') for i in range(4))
    # 俯视图四边的说明讲的是「朝图的那一边有什么」:该向被墙挡死、且墙外还有一大截地图(墙距 < 到图边距离的 60%)时,说的就是墙外
    # 看不见的东西 → 改写成这个锚点实际看得见的。单间房(墙即图边)的四边说明讲的正是那面墙,照旧。
    view = anchor_view(scene, anchor, indoor)
    sw, _, sd = scene['dimensions_m']; ax, _, az = anchor['position']
    to_edge = dict(zip(('centre', 'left', 'behind', 'right'),
                       ([az + sd / 2, ax + sw / 2, sd / 2 - az, sw / 2 - ax][(k + i) % 4] for i in range(4))))
    blocked = {n for n, v in view['sectors'].items()
               if v['open'] < SECTOR_OPEN_MAX and v.get('wall_m') and v['wall_m'] < 0.6 * max(to_edge[n], 0.1)}
    centre, right, behind, left = (sector_sentence(scene, layout, view, n) if n in blocked else t
                                   for n, t in (('centre', centre), ('right', right), ('behind', behind), ('left', left)))
    enclosed = view['enclosed']
    vis_words, hid_words = visible_landmark_words(scene, layout, view) if enclosed else (set(), set())
    where = 'interior' if indoor else 'exterior'
    parts = [f"A 360 panorama of an empty real {where} location, photographed with nobody present and nothing moving, "
             f"realistic live-action film look. Location: {name}."]
    tod = (sch.get('condition') or {}).get('time_of_day') or time_of_day or ''
    if tod:
        parts.append(f"Time of day: {tod}.")
    if not indoor:
        parts.append(EXTERIOR_PROJECTION_RULES.strip())
    if enclosed:
        parts.append(ENCLOSED_RULE.strip())
    parts.append(f"The camera stands {standing_on(scene, layout, {'position': anchor['position'], 'target': anchor['position']})}, "
                 f"lens {anchor['position'][1]} m above the floor, level horizon.")
    if centre:
        parts.append('Looking straight ahead (image centre): ' + centre + '.')
    if right:
        parts.append('To the right (right quarter of the image): ' + right + '.')
    if behind:
        parts.append('Behind the camera (both outer edges of the image): ' + behind + '.')
    if left:
        parts.append('To the left (left quarter of the image): ' + left + '.')
    if o.get('note_en') and not enclosed:       # 全域说明讲的是整个场景(含墙外),封闭室内锚点不下发
        parts.append(str(o['note_en']))
    inv = object_inventory(scene, layout, anchor, view)
    if inv:
        parts.append('Exact placement of every block in [Image 1], measured from this camera (image column 0% = left edge, 50% = centre, 100% = right edge): '
                     + ' '.join(inv) + ' Every long row, counter or wall must keep exactly this direction and every seat must face exactly the stated way.')
    lighting = sch.get('prompt_fragment_en') or ''
    if enclosed:
        lighting = indoor_clauses(lighting, vis_words, hid_words, fine=True)
    if lighting:
        parts.append('Lighting: ' + lighting.rstrip('.。') + '.')
    desc = ' '.join(p.rstrip('.。;') + '.' for p in (_flat(arch.get(k2)) for k2 in ('form', 'arch_style', 'era_region', 'scale', 'materials', 'details')) if p)
    if enclosed:
        desc = indoor_clauses(desc, vis_words, hid_words)
    if desc:
        parts.append('Materials and era (reference only): ' + desc[:1200].rstrip('.。;; ') + '.')
    parts.append('Empty location: no people, no characters, no human figures or silhouettes, no animals, no moving vehicles, no text, '
                 'no watermark, one single seamless equirectangular photograph.')
    style = pano_style(style)
    if style:
        parts.append('Look (materials, palette and grain only): ' + style.rstrip('.') + '.')
    parts.append(PANO_PROJECTION_TAIL)
    return ' '.join(p.strip() for p in parts if p and p.strip())


# ---------------------------------------------------------------- reprojection (numpy, backward warp)
# 目标视图每个像素先对白模几何射线求交得 3D 点(密集、无散射空洞),再回到源全景取色,并用源深度全景做遮挡判定;
# 只有真被遮挡处才是空洞。几何 = 白模 objects(盒;球/柱按盒近似)+ 地板(外扩 20 m,机位可在白模地面外)+ 室内天花板。
def scene_boxes(scene: dict, indoor: bool) -> list:
    w, h, d = scene['dimensions_m']
    boxes = [((0.0, -0.05, 0.0), (w + 40.0, 0.05, d + 40.0), 0.0)]
    if indoor:
        boxes.append(((0.0, h + 0.025, 0.0), (w, 0.05, d), 0.0))
    for o in scene.get('objects', []):
        boxes.append((tuple(float(v) for v in o['position']), tuple(float(v) for v in o['size_m']), float(o.get('yaw') or 0)))
    return boxes


def raycast(origin, dirs, boxes):
    """origin (3,), dirs (N,3) 单位向量 → 每条射线最近命中距离 t (N,),无命中 inf。"""
    import numpy as np
    o = np.asarray(origin, dtype=np.float64)
    best = np.full(dirs.shape[0], np.inf)
    for (c, size, yaw) in boxes:
        cy, sy = math.cos(-yaw), math.sin(-yaw)
        ox, oy, oz = o[0] - c[0], o[1] - c[1], o[2] - c[2]
        lo = np.array([cy * ox + sy * oz, oy, -sy * ox + cy * oz])
        ld = np.stack([cy * dirs[:, 0] + sy * dirs[:, 2], dirs[:, 1], -sy * dirs[:, 0] + cy * dirs[:, 2]], axis=1)
        half = np.array(size, dtype=np.float64) / 2
        with np.errstate(divide='ignore', invalid='ignore'):
            inv = 1.0 / ld
            t1 = (-half - lo) * inv; t2 = (half - lo) * inv
        tmin = np.max(np.minimum(t1, t2), axis=1); tmax = np.min(np.maximum(t1, t2), axis=1)
        hit = (tmax >= np.maximum(tmin, 0.02)) & (tmax > 0.02)
        t = np.where(tmin > 0.02, tmin, tmax)
        best = np.where(hit & (t < best), t, best)
    return best


def _load_pano(base: Path, sid: str, anchor: dict, scheme: str):
    import cv2
    import numpy as np
    out = panos_dir(base, sid) / anchor['anchor_id']
    pano_path = out / f'{scheme}.png'
    if not pano_path.is_file():
        raise PanoError(f"{sid}/{anchor['anchor_id']}: 缺少全景 {pano_path.name}")
    rec = read(out / 'depth_pano.json', None) or {}
    if not rec or not (out / 'depth_pano.npy').is_file():
        raise PanoError(f"{sid}/{anchor['anchor_id']}: 缺少白模深度全景")
    pano = cv2.cvtColor(cv2.imread(str(pano_path), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
    depth = np.load(out / 'depth_pano.npy').astype(np.float32)
    H, W = pano.shape[:2]
    if depth.shape != (H, W):
        depth = cv2.resize(depth, (W, H), interpolation=cv2.INTER_NEAREST)
    cam0 = np.array(rec['camera']['position'], dtype=np.float64)
    yaw0 = math.radians(float(rec['camera'].get('yaw_deg') or 0))
    return pano, depth, cam0, yaw0, bool(rec.get('indoor')), float(rec.get('z_max') or depth.max() / 4)


def _sample_pano(points, infinite, pano, depth0, cam0, yaw0, far):
    """3D 点 (N,3) → 源全景颜色 (N,3) 与可见掩码。infinite(目标射线无几何)按纯方向取色,但源在该方向若有几何(深度 < far)
    即被遮挡——否则机位越过货架看到的远处会被抹成货架颜色。"""
    import numpy as np
    H, W = depth0.shape
    v = points - cam0
    dist = np.linalg.norm(v, axis=1) + 1e-9
    cy, sy = math.cos(yaw0), math.sin(yaw0)
    lx = cy * v[:, 0] - sy * v[:, 2]; lz = sy * v[:, 0] + cy * v[:, 2]; ly = v[:, 1]
    theta = np.arctan2(lx, -lz); phi = np.arccos(np.clip(ly / dist, -1, 1))
    u = np.clip(((theta + np.pi) / (2 * np.pi) * W).astype(np.int64), 0, W - 1)
    r = np.clip((phi / np.pi * H).astype(np.int64), 0, H - 1)
    d0 = depth0[r, u].astype(np.float64)
    visible = np.where(infinite, d0 > far, d0 >= dist * 0.97 - 0.15)
    return pano[r, u], visible


def _view_rays(camera: dict, bw: int, bh: int):
    import numpy as np
    pos = np.array(camera['position'], dtype=np.float64); tgt = np.array(camera['target'], dtype=np.float64)
    f = tgt - pos; f /= np.linalg.norm(f)
    up = np.array([0, 1, 0], dtype=np.float64)
    if abs(np.dot(f, up)) > .999:
        up = np.array([0, 0, -1], dtype=np.float64)
    rgt = np.cross(f, up); rgt /= np.linalg.norm(rgt); u = np.cross(rgt, f)
    tan_half = math.tan(math.radians(float(camera['fov_v_deg'])) / 2); aspect = bw / bh
    xs = ((np.arange(bw) + .5) / bw * 2 - 1) * tan_half * aspect
    ys = (1 - (np.arange(bh) + .5) / bh * 2) * tan_half
    gx, gy = np.meshgrid(xs, ys)
    dirs = gx[..., None] * rgt + gy[..., None] * u + f
    dirs /= np.linalg.norm(dirs, axis=2, keepdims=True)
    return pos, dirs.reshape(-1, 3)


def _equirect_rays(yaw_deg: float, bw: int, bh: int):
    import numpy as np
    c = (np.arange(bw, dtype=np.float64) + .5) / bw * 2 * np.pi - np.pi
    r = (np.arange(bh, dtype=np.float64) + .5) / bh * np.pi
    theta, phi = np.meshgrid(c, r)
    dx = np.sin(phi) * np.sin(theta); dy = np.cos(phi); dz = -np.cos(theta) * np.sin(phi)
    yaw = math.radians(yaw_deg); cy, sy = math.cos(yaw), math.sin(yaw)
    wx = cy * dx + sy * dz; wz = -sy * dx + cy * dz
    return np.stack([wx.ravel(), dy.ravel(), wz.ravel()], axis=1)


def _warp(base, sid, anchor, scheme, origin, dirs, bw, bh):
    import numpy as np
    from modules.whitebox import load_scene
    pano, depth0, cam0, yaw0, indoor, z_max = _load_pano(base, sid, anchor, scheme)
    boxes = scene_boxes(load_scene(base, sid), indoor)
    t = raycast(origin, dirs, boxes)
    infinite = ~np.isfinite(t)
    tt = np.where(infinite, 1e4, t)
    points = np.asarray(origin, dtype=np.float64) + dirs * tt[:, None]
    cols, visible = _sample_pano(points, infinite, pano, depth0, cam0, yaw0, z_max * 2)
    img = np.where(visible[:, None], cols, 0).astype(np.uint8).reshape(bh, bw, 3)
    hit = visible.astype(np.uint8).reshape(bh, bw)
    return img, hit, cam0


def _finish(img, hit, out_w, out_h, output: Path, *, inpaint=True):
    import cv2
    import numpy as np
    holes = (hit == 0).astype(np.uint8)
    hole_frac = float(holes.mean())
    if inpaint and 0 < hole_frac < HOLE_INPAINT_MAX:
        img = cv2.inpaint(cv2.cvtColor(img, cv2.COLOR_RGB2BGR), holes, 5, cv2.INPAINT_TELEA)
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    if img.shape[1] != out_w or img.shape[0] != out_h:
        img = cv2.resize(img, (out_w, out_h), interpolation=cv2.INTER_CUBIC)
    output.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output), cv2.cvtColor(img, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 92])
    return hole_frac


def reproject_to_camera(base: Path, sid: str, anchor: dict, scheme: str, camera: dict, width: int, height: int, output: Path) -> dict:
    """全景按分镜机位重投影成透视图(白模几何射线求交 + 源深度遮挡判定 + 空洞 inpaint)。camera={position,target,fov_v_deg}。"""
    import numpy as np
    bw = min(width, 960); bw -= bw % 2
    bh = int(round(bw * height / width)); bh -= bh % 2
    origin, dirs = _view_rays(camera, bw, bh)
    img, hit, cam0 = _warp(base, sid, anchor, scheme, origin, dirs, bw, bh)
    if not hit.any():
        raise PanoError(f"{sid}/{anchor['anchor_id']}: 该机位看不到全景的任何内容")
    hole_frac = _finish(img, hit, width, height, output)
    return {'file': str(output), 'anchor_id': anchor['anchor_id'], 'scheme': scheme, 'hole_fraction': round(hole_frac, 3),
            'buffer': [bw, bh], 'distance_from_anchor_m': round(float(np.linalg.norm(np.asarray(origin) - cam0)), 2)}


def reproject_to_anchor(base: Path, sid: str, src: dict, scheme: str, dst: dict, width: int, height: int, output: Path) -> dict:
    """父锚点全景 → 目标锚点球面(等距柱状),链式补洞的参考图;遮挡空洞留黑给图像模型补。"""
    import numpy as np
    bw, bh = min(width, 1440), min(height, 720)
    dirs = _equirect_rays(float(dst.get('yaw_deg') or 0), bw, bh)
    img, hit, cam0 = _warp(base, sid, src, scheme, dst['position'], dirs, bw, bh)
    hole_frac = _finish(img, hit, width, height, output, inpaint=False)
    return {'file': str(output), 'from': src['anchor_id'], 'hole_fraction': round(hole_frac, 3),
            'distance_m': round(float(np.linalg.norm(np.asarray(dst['position'], dtype=np.float64) - cam0)), 2)}


# ---------------------------------------------------------------- pano generation
def _pano_dims():
    w, h = (int(x) for x in PANO_SIZE.split('x'))
    return w, h


def generate_pano(base: Path, sid: str, idx: dict, anchor: dict, scheme: str, *, indoor: bool, seed: int | None = None,
                  time_of_day: str | None = None, log=print) -> dict:
    """出一张 (锚点, 光照方案) 全景。模式:relight(同锚点已有其它方案)> chain(其它锚点已有同方案)> fresh。"""
    from PIL import Image
    from modules.genmedia import generate_image, get_config, image_pref_env
    with image_pref_env('panos'):   # 场景预览页「🌐 全景模型」的选择(空=全局,不回退到本页「🎨 图像模型」)
        cfg = get_config('image')
    out = panos_dir(base, sid) / anchor['anchor_id']
    out.mkdir(parents=True, exist_ok=True)
    wb = out / 'whitebox_pano.jpg'
    if not wb.is_file():
        raise PanoError(f"{anchor['anchor_id']}: 先渲白模全景")
    wb_rec = read(out / 'depth_pano.json', {}) or {}
    if bool(wb_rec.get('indoor')) != bool(indoor) or (not indoor and int(wb_rec.get('guides') or 0) < GUIDES_VERSION):
        render_whitebox_pano(base, sid, anchor, indoor=indoor, log=log)   # 室内外判定变了 / 存量外景白模没有投影引导线:本机重渲,不作废已有全景
    w, h = _pano_dims()
    refs, rules, parent, mode = [], [], None, 'fresh'
    others = {s: p for s, p in anchor.get('panos', {}).items() if s != scheme and (out / p.get('file', '')).is_file()}
    if others:
        s0, p0 = next(iter(others.items()))
        mode, parent = 'relight', {'anchor_id': anchor['anchor_id'], 'scheme': s0}
        refs.append(out / p0['file']); rules.append(RELIGHT_RULE.format(n=1))
        refs.append(wb); rules.append(WHITEBOX_REF_RULE.format(n=2))
    else:
        refs.append(wb); rules.append(WHITEBOX_REF_RULE.format(n=1))
        donors = [a for a in idx['anchors'] if a['anchor_id'] != anchor['anchor_id']
                  and (panos_dir(base, sid) / a['anchor_id'] / f'{scheme}.png').is_file()]
        if donors:
            donors.sort(key=lambda a: math.dist(a['position'], anchor['position']))
            src = donors[0]
            chain = out / f"{scheme}.chain_{src['anchor_id']}.jpg"
            info = reproject_to_anchor(base, sid, src, scheme, anchor, w, h, chain)
            mode, parent = 'chain', {'anchor_id': src['anchor_id'], 'scheme': scheme, **{k: info[k] for k in ('hole_fraction', 'distance_m')}}
            refs.append(chain); rules.append(CHAIN_REF_RULE.format(n=2))
            if CHAIN_INCLUDE_SOURCE:
                refs.append(panos_dir(base, sid) / src['anchor_id'] / f'{scheme}.png'); rules.append(CHAIN_SOURCE_RULE.format(n=3, m=2))
            log(f"   链式补洞:参考 {src['anchor_id']} 全景重投影(空洞 {info['hole_fraction']:.0%},距 {info['distance_m']} m)" + ('+ 其原图' if CHAIN_INCLUDE_SOURCE else ''))
        layout = base / 'assets/concepts/scenes' / sid / ((read(base / 'assets/concepts/scenes' / sid / 'layout.json', {}) or {}).get('layout_top') or 'layout_top.png')
        from modules.whitebox import load_scene
        enclosed = anchor_view(load_scene(base, sid), anchor, indoor)['enclosed']
        if enclosed:
            log('   封闭室内锚点:不挂整场景俯视图(航拍里的墙外内容会被画进来),四向 / 清单 / 材质 / 光照只写本锚点看得见的')
        if layout.is_file() and not enclosed:
            refs.append(layout); rules.append(LAYOUT_REF_RULE.format(n=len(refs)))
    if not indoor:
        rules.append(GUIDES_REF_RULE.format(n=refs.index(wb) + 1))
    prompt = PANO_PROJECTION_RULES + ''.join(rules) + pano_prompt(base, sid, scheme, anchor, indoor=indoor, mode=mode, time_of_day=time_of_day)
    if seed is None:
        import random
        seed = random.randint(1, 2 ** 31 - 1)
    target = out / f'{scheme}.png'
    log(f"   出全景 {anchor['anchor_id']}/{scheme} [{mode}] {cfg.get('provider')}/{cfg.get('model')} {PANO_SIZE},参考图 {len(refs)} 张 …")
    generate_image(prompt, str(target), negative=NEGATIVE, refs=[str(r) for r in refs], size=PANO_SIZE, seed=seed)
    im = Image.open(target); rw, rh = im.size
    if abs(rw / rh - 2.0) > ASPECT_TOLERANCE:
        target.rename(target.with_suffix('.rejected.png'))
        mark_blocked(base, sid, idx, cfg, f'返回 {rw}x{rh},不是 2:1 全景')
        raise PanoUnsupported(f"当前图像模型 {cfg.get('provider')}/{cfg.get('model')} 返回 {rw}x{rh},不是 2:1 全景;"
                              "全景图与分镜背景图已暂停。请用户切换图像模型后重跑:场景预览页顶部「🌐 全景模型」有选则改那里,否则改控制台「🎨 生成模型」。")
    proj = projection_check(target)
    if proj and proj['verdict'] == 'FAIL':
        rejected = target.with_suffix('.rejected-projection-' + dt.datetime.now().strftime('%Y%m%d-%H%M%S') + '.png')
        target.rename(rejected)
        raise PanoProjectionError(
            f"{sid}/{anchor['anchor_id']}/{scheme}: 成图不是等距柱状全景({proj['reason']}),已改名 {rejected.name},本批停下。"
            f"重出:render_scene_panos.py --only {anchor['anchor_id']}(换 seed);重出次数计入用户设定的重跑次数,用尽即上报用户,不得无限重出。")
    if proj and proj['verdict'] == 'WARN':
        log(f"   WARN 投影机检:{proj['reason']}")
    conf = conformity_check(target, out / 'depth_pano.npy')
    if conf and conf['verdict'] == 'FAIL':
        rejected = target.with_suffix('.rejected-conformity-' + dt.datetime.now().strftime('%Y%m%d-%H%M%S') + '.png')
        target.rename(rejected)
        raise PanoProjectionError(
            f"{sid}/{anchor['anchor_id']}/{scheme}: {conf['reason']};已改名 {rejected.name},本批停下。"
            f"重出:render_scene_panos.py --only {anchor['anchor_id']}(换 seed);重出次数计入用户设定的重跑次数,用尽即上报用户,不得无限重出。")
    if conf and conf['verdict'] == 'WARN':
        log(f"   WARN 白模一致性:{conf['reason']}")
    if mode == 'chain':
        score = chain_consistency(chain, target)
        parent['consistency'] = score
        if score is not None and score < CONSISTENCY_WARN:
            log(f"   WARN 链式一致性 {score:.2f} < {CONSISTENCY_WARN}:请在预览页对照 {parent['anchor_id']} 全景核对,不一致用 render_scene_panos.py --redo {anchor['anchor_id']} 重出")
    rec = {'file': target.name, 'scheme': scheme, 'mode': mode, 'parent': parent, 'time_of_day': time_of_day,
           'channel': {'provider': cfg.get('provider'), 'model': cfg.get('model')}, 'size': [rw, rh], 'seed': seed,
           'refs': [str(r.relative_to(base)) if str(r).startswith(str(base)) else str(r) for r in refs],
           'prompt': prompt, 'negative': NEGATIVE, 'projection_check': proj, 'conformity_check': conf, 'anchor': {'position': anchor['position'], 'yaw_deg': anchor.get('yaw_deg', 0)},
           'written_at': _now()}
    (out / f'{scheme}.json').write_text(json.dumps(rec, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    anchor.setdefault('panos', {})[scheme] = {k: rec[k] for k in ('file', 'mode', 'parent', 'channel', 'seed', 'size', 'time_of_day', 'written_at')}
    save_index(base, sid, idx)
    log(f"saved: {target.relative_to(base)}")
    return rec


NADIR_DETAIL_FAIL = 0.7          # 底部 5% 行的横向细节 / 中段横向细节;等距柱状里天底被横向拉伸,实测合格 0.14–0.55、广角照片 0.85–2.6
NADIR_DETAIL_SOFT = 0.4
SEAM_RATIO_WARN = 3.0            # 左右缘色差 / 相邻列色差基线;接缝不上只 WARN(合格全景也常见),与极区指标同时超限才 FAIL


def projection_check(path: Path) -> dict | None:
    """成图是不是等距柱状:① 天底横向细节比(主判据)② 天顶/天底行方差 ③ 左右缘接缝。
    标定样本:dzg6 SCN-0002 / liaozhai3 SCN-0005 / fengshen3 SCN-0140、0046(合格)对 fengshen3 SCN-0110 A1–A12(2:1 广角照片)。"""
    try:
        import numpy as np
        from PIL import Image
        im = Image.open(path).convert('RGB')
        g = np.asarray(im.convert('L').resize((1024, 512), Image.BOX), dtype=np.float64)
        dx = np.abs(np.diff(g, axis=1))
        mid = max(float(dx[154:358].mean()), 1e-3)
        nadir, zenith = float(dx[-26:].mean()) / mid, float(dx[:26].mean()) / mid
        s = np.asarray(im.resize((256, 128), Image.BOX), dtype=np.float64)
        seam = float(np.abs(s[:, 0] - s[:, -1]).mean())
        inner = float(np.mean([np.abs(s[:, c] - s[:, c + 1]).mean() for c in range(8, 247, 8)]))
        seam_ratio = seam / max(inner, 2.0)
        top_std, bot_std = float(s[:3].std(axis=1).mean()), float(s[-3:].std(axis=1).mean())
    except Exception:  # noqa: BLE001
        return None
    rec = {'nadir_detail': round(nadir, 2), 'zenith_detail': round(zenith, 2), 'seam_ratio': round(seam_ratio, 1),
           'top_row_std': round(top_std, 1), 'bottom_row_std': round(bot_std, 1)}
    if nadir > NADIR_DETAIL_FAIL:
        return {**rec, 'verdict': 'FAIL', 'reason': f"天底未拉伸:底部横向细节比 {nadir:.2f} > {NADIR_DETAIL_FAIL}(像广角照片的清晰前景)"}
    if nadir > NADIR_DETAIL_SOFT and top_std > 25 and bot_std > 15 and seam_ratio > SEAM_RATIO_WARN:
        return {**rec, 'verdict': 'FAIL', 'reason': f"天顶/天底不成色带(行方差 {top_std:.0f}/{bot_std:.0f})且左右缘接不上(接缝比 {seam_ratio:.1f})"}
    if seam_ratio > SEAM_RATIO_WARN:
        return {**rec, 'verdict': 'WARN', 'reason': f"左右缘接不上(接缝比 {seam_ratio:.1f} > {SEAM_RATIO_WARN}),请在预览页核对"}
    return {**rec, 'verdict': 'PASS', 'reason': ''}


CONFORMITY_BUSY_NULL = 0.75      # 成图处处是边缘(机场大厅)时错位也能对上,指标失效 → 不判
CONFORMITY_FAIL_S0 = 0.30
CONFORMITY_WARN_Z = 2.0


def conformity_check(result: Path, depth_npy: Path) -> dict | None:
    """成图有没有跟白模:白模深度全景的轮廓线(墙脚 / 墙角 / 门洞 / 家具外缘)在成图里 3 px 内找得到边缘的比例 s0,
    与把轮廓横向错开后的比例(null)比。跟了白模:s0 明显高于 null(liaozhai3 SCN-0005 0.88 对 0.31、fengshen3 SCN-0140 A1 0.86 对 0.53);
    画成了别的视点(fengshen3 SCN-0046 A1 洞内锚点画成洞府外观):s0 0.13、低于 null。只看投影形态的 projection_check 拦不住这类。"""
    try:
        import cv2
        import numpy as np
        w, h = 512, 256
        depth = cv2.resize(np.load(depth_npy), (w, h), interpolation=cv2.INTER_NEAREST)
        ld = np.log(np.clip(depth, 0.1, 200))
        edges = (np.abs(np.diff(ld, axis=1, append=ld[:, :1])) > 0.25) | (np.abs(np.diff(ld, axis=0, append=ld[-1:])) > 0.25)
        edges[:20] = False; edges[-40:] = False            # 两极拉伸区不计
        if edges.sum() < 150:
            return None                                     # 白模几乎没有轮廓(开阔外景)
        img = cv2.imread(str(result), cv2.IMREAD_COLOR)
        gray = cv2.GaussianBlur(cv2.cvtColor(cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY), (3, 3), 0)
        dist = cv2.distanceTransform(255 - cv2.Canny(gray, 40, 110), cv2.DIST_L2, 3)
        score = lambda shift: float((dist[np.roll(edges, shift, axis=1)] <= 3).mean())
        s0 = score(0); null = [score(k) for k in range(40, w - 40, 24)]
        mean, std = float(np.mean(null)), max(float(np.std(null)), 0.02)
    except Exception:  # noqa: BLE001
        return None
    z = (s0 - mean) / std
    rec = {'aligned': round(s0, 2), 'null': round(mean, 2), 'z': round(z, 1)}
    if mean >= CONFORMITY_BUSY_NULL:
        return {**rec, 'verdict': 'N/A', 'reason': '成图边缘过密,对齐度指标失效'}
    if s0 < CONFORMITY_FAIL_S0 and z < 1.0:
        return {**rec, 'verdict': 'FAIL', 'reason': f"成图没有跟白模:白模轮廓只有 {s0:.0%} 在成图里找得到(错位基线 {mean:.0%}),多半画成了别的视点 / 建筑外观"}
    if z < CONFORMITY_WARN_Z:
        return {**rec, 'verdict': 'WARN', 'reason': f"成图与白模轮廓对齐度不高于错位基线(对齐 {s0:.0%} / 基线 {mean:.0%}),请在预览页对照白模全景核对"}
    return {**rec, 'verdict': 'PASS', 'reason': ''}


def chain_consistency(chain_ref: Path, result: Path) -> float | None:
    """链式补洞成图与重投影参考图在有内容区域的相似度 0–1(灰度结构相关 × 亮度接近度),只作 WARN 与预览展示。"""
    try:
        import cv2
        import numpy as np
        a = cv2.imread(str(chain_ref), cv2.IMREAD_COLOR); b = cv2.imread(str(result), cv2.IMREAD_COLOR)
        if a is None or b is None:
            return None
        a = cv2.resize(a, (512, 256)); b = cv2.resize(b, (512, 256))
        mask = (a.sum(axis=2) > 30)
        mask[192:] = False                    # 地面近处拉伸最重,不计
        ga = cv2.GaussianBlur(cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), (5, 5), 0).astype(np.float64)
        gb = cv2.GaussianBlur(cv2.cvtColor(b, cv2.COLOR_BGR2GRAY), (5, 5), 0).astype(np.float64)
        if mask.sum() < 500:
            return None
        va, vb = ga[mask], gb[mask]
        corr = float(np.corrcoef(va, vb)[0, 1]) if va.std() > 1 and vb.std() > 1 else 0.0
        lum = 1.0 - min(1.0, float(np.abs(va - vb).mean()) / 96.0)
        return round(max(0.0, 0.6 * max(corr, 0) + 0.4 * lum), 3)
    except Exception:  # noqa: BLE001
        return None


CONSISTENCY_WARN = 0.55


def pano_ready(base: Path, sid: str, anchor: dict, scheme: str) -> bool:
    p = anchor.get('panos', {}).get(scheme)
    return bool(p) and (panos_dir(base, sid) / anchor['anchor_id'] / p.get('file', '')).is_file() and not whitebox_pano_stale(base, sid, anchor)


def scene_scheme_options(base: Path, sid: str, cameras: list | None = None) -> list[dict]:
    """可出全景的光照方案候选:各集机位实际用到的方案在前,其后补 bible lighting.json 里其余方案;都没有则 default。
    每项 {scheme, time_of_day, in_use}。预览页「创建全景图」下拉与 CLI --scheme 校验共用。"""
    sid = component(sid)
    cams = scene_cameras(base, sid) if cameras is None else cameras
    out, seen = [], set()
    for c in cams:
        if c['scheme'] not in seen:
            seen.add(c['scheme'])
            out.append({'scheme': c['scheme'], 'time_of_day': c.get('time_of_day'), 'in_use': True})
    doc = read(base / 'bible/scenes' / sid / 'lighting.json', {}) or {}
    for s in doc.get('schemes', []) if isinstance(doc, dict) else []:
        slug = scheme_slug(s.get('scheme_id') or s.get('id'), s.get('time_of_day'))
        if slug not in seen:
            seen.add(slug)
            out.append({'scheme': slug, 'time_of_day': s.get('time_of_day'), 'in_use': False})
    if not out:
        out.append({'scheme': scheme_slug(None), 'time_of_day': None, 'in_use': False})
    return out


def add_manual_anchor(base: Path, sid: str, x: float, z: float, yaw_deg: float = 0.0, *, cameras: list | None = None) -> dict:
    """手动加一个锁定锚点(预览页俯视图点选 / CLI --anchor):坐标夹回白模地面内 0.5 m,高度同规划默认,
    serves = 尚无锚点服务且它能服务的机位(不抢已有锚点的机位,不改动其它锚点)。写回 index.json,返回新锚点。"""
    from modules.whitebox import load_scene
    sid = component(sid)
    scene = load_scene(base, sid)
    cams = scene_cameras(base, sid) if cameras is None else cameras
    idx = load_index(base, sid)
    w, _, d = scene['dimensions_m']
    px = round(max(-w / 2 + .5, min(w / 2 - .5, float(x))), 3)
    pz = round(max(-d / 2 + .5, min(d / 2 - .5, float(z))), 3)
    height = idx['anchors'][0]['position'][1] if idx['anchors'] else default_anchor_height(cams)
    used = {a['anchor_id'] for a in idx['anchors']}
    n = len(idx['anchors']) + 1
    while f'A{n}' in used:
        n += 1
    served = {k for a in idx['anchors'] for k in a.get('serves', [])}
    pos = [px, height, pz]
    serves = sorted(_cam_key(c) for c in cams if _cam_key(c) not in served and can_serve(pos, c, scene))
    anchor = {'anchor_id': f'A{n}', 'position': pos, 'yaw_deg': float(yaw_deg or 0.0), 'source': 'manual', 'locked': True,
              'serves': serves, 'panos': {}}
    idx['anchors'].append(anchor)
    save_index(base, sid, idx)
    return anchor


def ensure_scene_panos(base: Path, sid: str, *, cameras: list | None = None, schemes: dict | None = None, dry_run=False,
                       force=False, replan=False, indoor: bool | None = None, seed=None, redo: list | None = None,
                       only: list | None = None, log=print) -> dict:
    """本场景全景齐备:规划锚点(增量)→ 渲白模全景 → 逐 (锚点, 方案) 出图。schemes={scheme: time_of_day};缺省取各集机位所用方案。
    返回 {'anchors', 'new', 'pending', 'indoor'}。dry_run 只规划 + 渲白模全景,不调图像模型。
    only=[anchor_id…](2026-09-13 预览页「创建全景图」):不规划/不补锚点,只给这些锚点出图(不看它们服务哪些机位),
    没有机位也允许(方案须显式传 schemes)。"""
    from modules.whitebox import load_scene
    sid = component(sid)
    scene = load_scene(base, sid)
    cams = cameras if cameras is not None else scene_cameras(base, sid)
    if not cams and not (only and schemes):
        raise PanoError(f'{sid}: 没有任何白模机位(先 render_whitebox.py --compile-only)')
    idx = load_index(base, sid)
    indoor = is_indoor(base, sid) if indoor is None else indoor
    idx['indoor'] = indoor
    served = {k for a in idx['anchors'] for k in a.get('serves', [])}
    new_cams = [c for c in cams if _cam_key(c) not in served]
    if only:
        missing = sorted(set(only) - {a['anchor_id'] for a in idx['anchors']})
        if missing:
            raise PanoError(f'{sid}: 锚点不存在:{missing}')
    elif replan or not idx['anchors']:
        idx['anchors'] = plan_anchors(scene, cams, existing=idx['anchors'])
        idx['planned_at'] = _now()
    elif new_cams:
        # 增量:先看已有锚点能否服务新机位,剩下的再补锚点
        for a in idx['anchors']:
            add = [c for c in new_cams if can_serve(a['position'], c, scene)]
            a['serves'] = sorted(set(a['serves']) | {_cam_key(c) for c in add})
            new_cams = [c for c in new_cams if c not in add]
        if new_cams:
            extra = plan_anchors(scene, new_cams, existing=[], height=idx['anchors'][0]['position'][1])
            used = {a['anchor_id'] for a in idx['anchors']}
            n = 1
            for a in extra:
                while f'A{n}' in used:
                    n += 1
                a['anchor_id'] = f'A{n}'; used.add(a['anchor_id'])
            idx['anchors'].extend(extra)
    for a in idx['anchors']:
        if redo and a['anchor_id'] in redo:
            for rec in a.get('panos', {}).values():
                f = panos_dir(base, sid) / a['anchor_id'] / str(rec.get('file') or '')
                if f.is_file():
                    f.rename(f.with_suffix('.redo-' + dt.datetime.now().strftime('%Y%m%d-%H%M%S') + '.png'))
            a['panos'] = {}
    save_index(base, sid, idx)
    need = dict(schemes or {})
    if not need:
        for c in cams:
            need.setdefault(c['scheme'], c.get('time_of_day'))
    stats = {'anchors': [a['anchor_id'] for a in idx['anchors']], 'new': 0, 'pending': [], 'indoor': indoor, 'scene_id': sid}
    for a in idx['anchors']:
        if only and a['anchor_id'] not in only:
            continue
        if force or whitebox_pano_stale(base, sid, a):
            render_whitebox_pano(base, sid, a, indoor=indoor, log=log)
            # 锚点位置/朝向变了或强制重出:旧全景与新几何不再对应,作废(文件改名保留)
            for rec in a.get('panos', {}).values():
                f = panos_dir(base, sid) / a['anchor_id'] / str(rec.get('file') or '')
                if f.is_file():
                    f.rename(f.with_suffix('.stale-' + dt.datetime.now().strftime('%Y%m%d-%H%M%S') + '.png'))
            a['panos'] = {}
            save_index(base, sid, idx)
    # 只出本次机位用到的锚点(传了 cameras 时);顺序:先出服务机位最多的锚点(母全景),其余链式补洞
    keys = {_cam_key(c) for c in cams}
    if only:
        order = [a for a in idx['anchors'] if a['anchor_id'] in only]
    else:
        order = sorted((a for a in idx['anchors'] if cameras is None or set(a.get('serves', [])) & keys),
                       key=lambda a: -len(a.get('serves', [])))
    todo = [(a, s, t) for s, t in need.items() for a in order if force or not pano_ready(base, sid, a, s)]
    if todo and not dry_run:
        check_pano_support(base, sid, idx, log=log)
    for a, s, t in todo:
        if dry_run:
            stats['pending'].append(f"{a['anchor_id']}/{s}")
            continue
        generate_pano(base, sid, idx, a, s, indoor=indoor, seed=seed, time_of_day=t, log=log)
        stats['new'] += 1
    return stats


def anchor_for_camera(base: Path, sid: str, idx: dict, cam: dict, scheme: str) -> list:
    """按优先级返回可用于该机位的锚点列表:规划里服务它的 → 其余按距离(且有该方案全景)。"""
    key = _cam_key(cam)
    ready = [a for a in idx['anchors'] if pano_ready(base, sid, a, scheme)]
    primary = [a for a in ready if key in a.get('serves', [])]
    rest = sorted((a for a in ready if a not in primary), key=lambda a: math.dist(a['position'], cam['position']))
    return primary + rest


def preview_summary(base: Path, sid: str) -> dict | None:
    """预览 API 用:锚点坐标(白模米制 + 俯视图归一化 xy)+ 各方案全景文件 + blocked。"""
    idx = read(panos_dir(base, sid) / 'index.json', None)
    if not isinstance(idx, dict) or idx.get('schema_version') != SCHEMA:
        return None
    scene = read(base / 'assets/concepts/scenes' / component(sid) / 'whitebox.scene.json', {}) or {}
    dims = scene.get('dimensions_m') or [1, 1, 1]
    anchors = []
    for a in idx.get('anchors', []):
        p = a.get('position') or [0, 0, 0]
        anchors.append({'anchor_id': a.get('anchor_id'), 'position': p, 'yaw_deg': a.get('yaw_deg', 0), 'source': a.get('source'),
                        'locked': bool(a.get('locked')), 'serves': a.get('serves', []),
                        'xy': [round(p[0] / dims[0] + .5, 4), round(p[2] / dims[2] + .5, 4)],
                        'panos': {s: {k: v for k, v in (rec or {}).items() if k in ('file', 'mode', 'parent', 'time_of_day', 'written_at', 'channel')}
                                  for s, rec in (a.get('panos') or {}).items()}})
    return {'anchors': anchors, 'indoor': idx.get('indoor'), 'planned_at': idx.get('planned_at'), 'blocked': idx.get('blocked'),
            'dimensions_m': dims}
