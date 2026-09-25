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
GROUND_PLAN_RULE = (
    "The ground of [Image {n}] carries the top-down plan of this location re-projected onto the floor from this exact camera: it shows "
    "precisely what the ground is under and around the camera — open water, shallows, sand, gravel, grass, mud, paving, paths — and where "
    "each shoreline or edge runs, already bent into this projection. Follow it exactly: where it shows water directly beneath the camera, "
    "the camera is standing in the water, so the nadir and the whole near foreground are water surface (ripples, reflections, the bed "
    "showing through shallows), never dry land; keep every shoreline, path edge and patch boundary where the texture puts it. It is a "
    "map, not a photo: render real ground seen from eye level, and ignore the flattened top views of objects printed on it — the blocks "
    "are the objects. "
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


OUTDOOR_SERVE_SIDE_RATIO = 0.25  # 室外场景的最小服务半径 = 白模地面短边 × 此值,夹在 [SERVE_MIN_M, OUTDOOR_SERVE_MIN_MAX_M]
OUTDOOR_SERVE_MIN_MAX_M = 8.0


WALLED_MIN = 0.7                 # 地平线带里「最近命中是通顶高墙」的射线占比 ≥ 此值 = 这个点在屋里
_WALLED_CACHE: dict = {}


def point_walled(scene: dict, pos) -> float:
    """白模里某点四周被通顶高墙围住的程度 0–1:地平线上 0°/10°/20° 三圈各 48 条射线,最近命中是高墙
    (顶 ≥ max(0.6 × 场景高, 该点高 + 1.5 m))的占比。院墙、家具矮于此不算。内外混合场景逐点判室内外用(锚点、机位、规划候选点)。"""
    import numpy as np
    objs = scene.get('objects', [])
    key = (id(objs), len(objs), round(float(pos[0]), 2), round(float(pos[1]), 2), round(float(pos[2]), 2))
    if key in _WALLED_CACHE:
        return _WALLED_CACHE[key]
    az = np.linspace(0, 2 * np.pi, 48, endpoint=False)
    dirs = np.concatenate([np.stack([np.cos(e) * np.sin(az), np.full_like(az, np.sin(e)), -np.cos(e) * np.cos(az)], axis=1)
                           for e in np.radians([0.0, 10.0, 20.0])])
    origin = [float(v) for v in pos]
    tall_top = max(0.6 * float(scene['dimensions_m'][1]), origin[1] + 1.5)
    best = np.full(len(dirs), np.inf); tall_hit = np.zeros(len(dirs), dtype=bool)
    for o in objs:
        box = (tuple(float(v) for v in o['position']), tuple(float(v) for v in o['size_m']), float(o.get('yaw') or 0))
        t = raycast(origin, dirs, [box])
        nearer = t < best
        tall_hit = np.where(nearer, float(o['position'][1]) + float(o['size_m'][1]) / 2 >= tall_top, tall_hit)
        best = np.where(nearer, t, best)
    val = round(float(tall_hit.mean()), 3)
    if len(_WALLED_CACHE) > 20000:
        _WALLED_CACHE.clear()
    _WALLED_CACHE[key] = val
    return val


def _wall_clearance(scene: dict, pos) -> float:
    tall_top = max(0.6 * float(scene['dimensions_m'][1]), float(pos[1]) + 1.5)
    tall = [o for o in scene.get('objects', []) if float(o['position'][1]) + float(o['size_m'][1]) / 2 >= tall_top]
    return _clearance(pos, tall) if tall else 1e9


def serve_min_m(scene: dict, anchor_pos=None, cam_pos=None) -> float:
    """锚点最小服务半径。室内 3 m(近处家具多,锚点一偏遮挡关系就变)。室外按白模地面短边放宽(2026-09-20 用户定):场景越大越开阔、
    背景越远视差越小;地面是白模已知几何,重投影对地面精确;近处块体背后的空洞由 PLATE_HOLE_MAX 换锚点 / 加锚点兜底。
    取短边不取宽:狭长场景(街道 60×8)不被长边带偏。fengshen3 SCN-0110(40×22.5,63 机位)5.6 m:锚点 13 → 7;SCN-0121(320×180)8 m:9 → 4。
    内外混合场景(scene['_mixed'],如 SCN-0046 云台 + 洞内主室)逐点判:锚点与机位都在室外(point_walled < WALLED_MIN)才放宽,
    且不超过锚点到最近通顶高墙的距离——整块地面的短边含着室内那一半,开阔的只是墙外那片。
    室内外由调用方写在 scene['_outdoor'] / scene['_mixed'](ensure_scene_panos / add_manual_anchor),没写按室内。"""
    w, _, d = scene['dimensions_m']
    wide = round(min(OUTDOOR_SERVE_MIN_MAX_M, max(SERVE_MIN_M, OUTDOOR_SERVE_SIDE_RATIO * min(w, d))), 2)
    if scene.get('_outdoor'):
        return wide
    if scene.get('_mixed') and anchor_pos is not None and cam_pos is not None:
        if point_walled(scene, anchor_pos) < WALLED_MIN and point_walled(scene, [cam_pos[0], anchor_pos[1], cam_pos[2]]) < WALLED_MIN:
            return round(max(SERVE_MIN_M, min(wide, _wall_clearance(scene, anchor_pos))), 2)
    return SERVE_MIN_M


def can_serve(anchor_pos, cam: dict, scene: dict) -> bool:
    pos, tgt = cam['position'], cam['target']
    subject = math.hypot(tgt[0] - pos[0], tgt[2] - pos[2])
    # 机位在白模地面之外(远景 / 高空大全景,SCN-0110 有 17 个,最远 126 m):它的锚点本来就只能夹回地面边缘内 0.5 m,
    # 所以按夹回点算距离——否则这些机位谁也服务不了,各自落成一个 auto-self 锚点(各出一张全景),而夹回点彼此只隔几米。
    w, _, d = scene['dimensions_m']
    pos = [max(-w / 2 + .5, min(w / 2 - .5, pos[0])), pos[1], max(-d / 2 + .5, min(d / 2 - .5, pos[2]))]
    if math.hypot(anchor_pos[0] - pos[0], anchor_pos[2] - pos[2]) > SERVE_MAX_M:
        return False                        # 先按上限粗筛,混合场景的逐点围合判定只对够近的候选做
    limit = min(SERVE_MAX_M, max(serve_min_m(scene, anchor_pos, pos), SERVE_RATIO * subject))
    if math.hypot(anchor_pos[0] - pos[0], anchor_pos[2] - pos[2]) > limit:
        return False
    objs = scene.get('objects', [])
    if segment_blocked(anchor_pos, [pos[0], anchor_pos[1], pos[2]], objs):
        return False
    if abs(anchor_pos[1] - pos[1]) > SURFACE_LEVEL_TOL_M and segment_blocked(anchor_pos, pos, objs):
        return False                        # 不同层(墙下锚点 ↔ 墙顶机位):到机位本身的连线也不能穿实体
    return not segment_blocked(anchor_pos, tgt, objs)


# ---------------------------------------------------------------- standing surface (2026-09-21)
# 锚点高度 = 脚下站立面 + 眼高,不再按绝对 y 夹在 1.6–2.0 m:机位在城墙顶 / 高台 / 楼上时(fengshen3 SCN-0036 关墙顶 y≈15 m),
# 绝对 2 m 落在墙体实心体块里,白模全景渲的是体块内部、分镜背景重投影全空(「该机位看不到全景的任何内容」)。
SURFACE_MIN_M = 1.0              # 顶面低于此的体块(台基/矮凳/门槛)不算站立面,按地面算——眼高本来就高过它们
SURFACE_LEVEL_TOL_M = 1.0        # 候选点的站立面与某个机位的站立面相差 ≤ 此值才算同一层(规划只在有机位的那几层撒候选点)


def _top(o) -> float:
    return float(o['position'][1]) + float(o['size_m'][1]) / 2


def surfaces_at(scene: dict, x: float, z: float) -> list:
    """(x, z) 处自下而上的站立面高度:地面 0 + 水平盖住该点、顶面 ≥ SURFACE_MIN_M 的体块顶面。"""
    tops = {0.0}
    for o in scene.get('objects', []):
        if _top(o) >= SURFACE_MIN_M and _inside([x, o['position'][1], z], o):
            tops.add(round(_top(o), 3))
    return sorted(tops)


def standing_surface(scene: dict, pos) -> float:
    """机位 pos 脚下的站立面:它正下方最高的那个面(顶面不高于机位);机位下面没有体块 = 地面 0。"""
    below = [t for t in surfaces_at(scene, pos[0], pos[2]) if t <= float(pos[1]) + .05]
    return below[-1] if below else 0.0


def _free_at(scene: dict, p) -> bool:
    return not any(_inside(p, o) for o in scene.get('objects', []))


def default_anchor_height(cameras: list, scene: dict | None = None) -> float:
    """眼高(相对脚下站立面,不是绝对 y)。不低于 1.6 m:锚点高于座椅背/柜台等中高家具,遮挡少
    (重投影按机位射线求交,锚点高度不必等于机高)。不传 scene 时按地面 0 算(老口径)。"""
    hs = sorted(c['position'][1] - (standing_surface(scene, c['position']) if scene else 0.0) for c in cameras) or [1.6]
    return round(min(2.0, max(1.6, hs[len(hs) // 2])), 2)


def anchor_pos_at(scene: dict, x: float, z: float, cameras: list, eye: float) -> list:
    """(x, z) 处的锚点位置 [x, 站立面 + 眼高, z]:先取与最近机位同层的站立面,没有同层的自下而上取第一个不在实体内的;
    都不行(被体块包死)就用最近机位自己的高度——机位本身总在可见空间里。"""
    surfs = surfaces_at(scene, x, z)
    near = sorted(cameras or [], key=lambda c: math.hypot(c['position'][0] - x, c['position'][2] - z))
    for c in near:
        level = standing_surface(scene, c['position'])
        for s in surfs:
            p = [round(x, 3), round(s + eye, 3), round(z, 3)]
            if abs(s - level) <= SURFACE_LEVEL_TOL_M and _free_at(scene, p):
                return p
    for s in surfs:
        p = [round(x, 3), round(s + eye, 3), round(z, 3)]
        if _free_at(scene, p):
            return p
    y = near[0]['position'][1] if near else surfs[-1] + eye
    return [round(x, 3), round(float(y), 3), round(z, 3)]


def candidate_points(scene: dict, height: float, levels: list | None = None) -> list:
    """网格候选点。height = 眼高;levels = 各机位的站立面(缺省只有地面):每个网格点在与某层机位同层的站立面上各出一个候选。
    贴墙/贴家具的间距只看高出本站立面的实体(脚下的墙体、低处的房子不算)。"""
    w, _, d = scene['dimensions_m']
    levels = sorted(set(levels or [0.0]))
    objs = scene.get('objects', [])
    elevated = any(l > 0 for l in levels)
    pts = []
    nx, nz = int(w / GRID_STEP_M), int(d / GRID_STEP_M)
    for i in range(1, nx):
        for j in range(1, nz):
            x, z = round(-w / 2 + i * GRID_STEP_M, 3), round(-d / 2 + j * GRID_STEP_M, 3)
            for s in (surfaces_at(scene, x, z) if elevated else [0.0]):
                if not any(abs(s - l) <= SURFACE_LEVEL_TOL_M for l in levels):
                    continue
                p = [x, round(s + height, 3), z]
                if any(_inside(p, o) for o in objs):
                    continue
                around = [o for o in objs if _top(o) > s + .05]
                tall = [o for o in around if _top(o) > p[1] - 0.3]
                if _clearance(p, tall) < ANCHOR_CLEARANCE_M or _clearance(p, around) < ANCHOR_LOW_CLEARANCE_M:
                    continue
                pts.append(p)
    return pts


def _cam_key(c):
    return f"{c.get('ep', '')}/{c['shot_id']}:{c.get('role', 'start')}"


# ---------------------------------------------------------------- which part of the pano the served cameras actually use
# 分镜背景图按母图(垂直 55°、16:9 下水平 ≈85°)从全景重投影:一个机位用到的全景范围 ≈ 朝向 ±50°(含母图朝向取平均与锚点视差的余量)、
# 俯仰 ±32°。接缝(画面左右缘 = 镜头身后)是图像模型最容易画坏的一列;天底是第二处。
USE_HALF_H_DEG = 50.0
USE_HALF_V_DEG = 32.0
NADIR_USE_LAT_DEG = -45.0        # 机位视域下沿低于此纬度 = 用到天底带(全景最下 ~25%)


def _cam_angles(cam: dict) -> tuple[float, float, float, float]:
    """(方位角°, 俯仰角°, 水平半视角°, 垂直半视角°);方位角 0 = 世界 -Z(俯视图上方),顺时针 90 = +X(图右)。"""
    dx, dy, dz = (cam['target'][i] - cam['position'][i] for i in range(3))
    az = math.degrees(math.atan2(dx, -dz)) % 360
    el = math.degrees(math.atan2(dy, max(math.hypot(dx, dz), 1e-6)))
    fov = float(cam.get('fov') or cam.get('fov_v_deg') or 0)
    return az, el, max(USE_HALF_H_DEG, fov * 16 / 9 / 2 + 8), max(USE_HALF_V_DEG, fov / 2 + 5)


def _ang_dist(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def seam_azimuth(yaw_deg: float) -> float:
    """接缝(镜头身后)的世界方位角。画面中心 = 局部 -Z 绕 Y 转 yaw → 世界 (-sin yaw, -cos yaw);身后反向。"""
    y = math.radians(yaw_deg)
    return math.degrees(math.atan2(math.sin(y), -math.cos(y))) % 360


def pick_seam_yaw(cams: list) -> float:
    """在 0/90/180/270 里选 yaw(四向文字按 90° 取整,不取任意角),让接缝落在服务机位最少看到的方向。
    代价 = Σ 机位视域与接缝的重叠深度;并列时让画面中心(模型画得最好的一列)尽量朝机位最集中的方向,再并列取 0。"""
    if not cams:
        return 0.0
    angles = [_cam_angles(c) for c in cams]
    best = None
    for yaw in (0.0, 90.0, 180.0, 270.0):
        seam = seam_azimuth(yaw); centre = (seam + 180) % 360
        cost = sum(max(0.0, 1 - _ang_dist(az, seam) / half) for az, _el, half, _v in angles)
        facing = sum(1 for az, _el, half, _v in angles if _ang_dist(az, centre) <= 45)
        key = (round(cost, 3), -facing, yaw)
        if best is None or key < best[0]:
            best = (key, yaw)
    return best[1]


def anchor_usage(anchor: dict, cams: list) -> dict:
    """本锚点服务的机位里,哪些的视域碰到接缝、哪些用到天底带。cams 里没有它服务的机位时返回 known=False(不知道就按碰到算)。"""
    serves = set(anchor.get('serves', []))
    mine = [c for c in cams or [] if _cam_key(c) in serves]
    if not mine:
        return {'known': False, 'cameras': 0, 'seam': [], 'nadir': []}
    seam = seam_azimuth(float(anchor.get('yaw_deg') or 0))
    out = {'known': True, 'cameras': len(mine), 'seam': [], 'nadir': []}
    for c in mine:
        az, el, half_h, half_v = _cam_angles(c)
        if _ang_dist(az, seam) <= half_h:
            out['seam'].append(_cam_key(c))
        if el - half_v <= NADIR_USE_LAT_DEG:
            out['nadir'].append(_cam_key(c))
    return out


def plan_anchors(scene: dict, cameras: list, existing: list | None = None, height: float | None = None) -> list:
    """贪心集合覆盖 → anchors[]{anchor_id, position, yaw_deg, source, locked, serves}。existing 里 locked 的锚点原样保留。
    height = 眼高(相对脚下站立面);锚点绝对高度逐点 = 站立面 + 眼高(城墙顶/高台/楼上的机位,锚点跟着上去)。"""
    height = height or default_anchor_height(cameras, scene)
    levels = [standing_surface(scene, c['position']) for c in cameras]
    anchors = [dict(a, serves=[]) for a in (existing or []) if a.get('locked')]
    uncovered = list(cameras)
    for a in anchors:
        a['position'][1] = a['position'][1] if a.get('position') and len(a['position']) == 3 else height
        served = [c for c in uncovered if can_serve(a['position'], c, scene)]
        a['serves'] = [_cam_key(c) for c in served]
        uncovered = [c for c in uncovered if c not in served]
    cands = candidate_points(scene, height, levels)
    # 机位本身也作候选(空旷处网格点可能都太远);机位在白模地面之外时夹回地面边缘内 0.5 m(白模外半球没有几何,全景只能瞎补)
    w, _, d = scene['dimensions_m']

    def at_camera(c):
        return anchor_pos_at(scene, max(-w / 2 + .5, min(w / 2 - .5, c['position'][0])),
                             max(-d / 2 + .5, min(d / 2 - .5, c['position'][2])), [c], height)
    for c in cameras:
        p = at_camera(c)
        if _free_at(scene, p):
            cands.append(p)
    while uncovered and cands:
        best = None
        for p in cands:
            served = [c for c in uncovered if can_serve(p, c, scene)]
            if not served:
                continue
            spread = sum(math.hypot(p[0] - c['position'][0], p[2] - c['position'][2]) for c in served) / len(served)
            score = (len(served), -spread, _clearance(p, tall_objects(scene, p[1])))
            if best is None or score > best[0]:
                best = (score, p, served)
        if best is None:
            break
        _, p, served = best
        anchors.append({'anchor_id': '', 'position': [round(v, 3) for v in p], 'yaw_deg': pick_seam_yaw(served), 'source': 'auto',
                        'locked': False, 'serves': [_cam_key(c) for c in served]})
        uncovered = [c for c in uncovered if c not in served]
    for c in uncovered:   # 仍无法覆盖(被实体包死的机位):以机位自身为锚点(同样夹回地面范围;高度随机位那一层,不落进实体)
        anchors.append({'anchor_id': '', 'position': at_camera(c),
                        'yaw_deg': pick_seam_yaw([c]), 'source': 'auto-self', 'locked': False, 'serves': [_cam_key(c)]})
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


_INDOOR_WORDS = ('室内', '内景', 'indoor', 'interior')
_OUTDOOR_NAME_WORDS = ('室外', '外景', 'outdoor', 'exterior', 'street')
_MIXED_WORDS = ('内外交替', '半室外', '半室内', '内外各半', 'int/ext', 'ext/int')


def _json_values(o) -> str:
    """JSON 里所有**值**拼成的文本(不含键名)。2026-09-20:旧判定对整份 lighting.json 做子串搜索,搜到的是字段名 "indoor" 自己——
    fengshen3 65 个场景里 57 个明写 "indoor": false 的室外场景(陈塘关全城、战场、山道…)全被判成室内、补了天花板。"""
    if isinstance(o, dict):
        return ' '.join(_json_values(v) for v in o.values())
    if isinstance(o, list):
        return ' '.join(_json_values(v) for v in o)
    return '' if isinstance(o, bool) or o is None else str(o)


def scene_indoor(base: Path, sid: str, _depth: int = 0) -> bool | None:
    """场景级室内外:True 室内 / False 室外 / None 内外混合或判不出(→ 逐锚点按白模围合判,见 anchor_indoor)。依据按可信度:
    ① 场景登记 index.json#int_ext(INT / EXT;INT/EXT = 混合)② lighting.json 显式 indoor 布尔 + enclosure(写「内外交替 / 半室外」= 混合)
    ③ 场景名里的「内景 / 外景」(子场景常只有 whitebox.json)④ 旧版设定沿用旧口径(值里有室内词、形制没有室外词)⑤ 什么设定都没有 → 跟上级场景。"""
    sid = component(sid)
    bdir = base / 'bible/scenes' / sid
    entry = {}
    idx = read(base / 'bible/scenes/index.json', {}) or {}
    for it in (idx.get('scenes') if isinstance(idx, dict) else idx) or []:
        if isinstance(it, dict) and it.get('id') == sid:
            entry = it
    int_ext = str(entry.get('int_ext') or '').upper().replace(' ', '')
    if int_ext:
        if 'INT' in int_ext and 'EXT' in int_ext:
            return None
        return int_ext.startswith('INT')
    lighting = read(bdir / 'lighting.json', {}) or {}
    if isinstance(lighting.get('indoor'), bool):
        if any(w in str(lighting.get('enclosure') or '').lower() for w in _MIXED_WORDS):
            return None
        return lighting['indoor']
    layout = read(base / 'assets/concepts/scenes' / sid / 'layout.json', {}) or {}
    name = f"{entry.get('name') or ''} {layout.get('scene_name') or ''}".lower()
    if any(w in name for w in _INDOOR_WORDS) != any(w in name for w in _OUTDOOR_NAME_WORDS):
        return any(w in name for w in _INDOOR_WORDS)
    # 旧版 lighting.json(dzg6 / liaozhai3:没有 indoor 字段)沿用旧口径——整份光照设定的**值**里有室内词、且建筑形制没有室外词
    form = _json_values((read(bdir / 'architecture.json', {}) or {}).get('form'))
    blob = (_json_values(lighting) + ' ' + form).lower()
    if lighting or form:
        if any(w in _json_values(lighting.get('enclosure')).lower() for w in _MIXED_WORDS):
            return None
        return any(w in blob for w in _INDOOR_WORDS if w != '内景') and not any(w in form.lower() for w in _OUTDOOR_NAME_WORDS)
    # 只有白模的子场景(fengshen3 SCN-0110 ← SCN-0037):名字也看不出 → 跟上级;上级也没有定论 → None,逐锚点按围合判
    if _depth < 5:
        for owner in scene_ancestors(base, sid)[1:2]:
            return scene_indoor(base, owner, _depth + 1)
    return None


def is_indoor(base: Path, sid: str) -> bool:
    """场景级布尔口径(世界模型、服务半径等不分锚点的地方用):混合 / 判不出按室外。分锚点的地方用 anchor_indoor。"""
    return scene_indoor(base, sid) is True


def anchor_indoor(base: Path, sid: str, scene: dict, anchor: dict, override: bool | None = None) -> bool:
    """这个锚点出全景时要不要补天花板 / 按室内写提示词。场景级有定论就用它;内外混合(fengshen3 SCN-0046 云台 + 洞内主室、
    SCN-0127 宫门前 + 正殿)按白模围合逐锚点判:四周最近命中大多是通顶高墙(顶 ≥ max(0.6 × 场景高, 镜头高 + 1.5 m))= 室内;
    院墙矮于此,不算。override = CLI --indoor / --outdoor。"""
    if override is not None:
        return bool(override)
    flag = scene_indoor(base, sid)
    if flag is not None:
        return flag
    return point_walled(scene, anchor['position']) >= WALLED_MIN      # 与服务半径的逐点判定同一口径


# ---------------------------------------------------------------- whitebox depth pano (Playwright)
GUIDES_VERSION = 2               # 外景白模全景投影引导线版本;depth_pano.json 的 guides 低于此值 → 出全景前重渲白模(本机、不花钱)
SKY_PLANE_M = 25.0               # 虚拟天空网格平面离镜头的高度


def draw_projection_guides(color, valid, depth, cam_h: float, yaw_deg: float, *, ground_plan=None, cam_xz=(0.0, 0.0), floor_wd=None):
    """开阔外景白模只有几个小盒子贴着地平线,上半幅纯色、下半幅淡地面,看上去就是一张普通广角构图,图像模型读不出等距柱状投影
    (fengshen3 SCN-0110:2:1 成图是广角照片)。给无几何的天空与地面补世界直角网格:直线在等距柱状里弯成向天顶/天底汇聚的曲线,
    是这种投影最强的视觉签名;地平线补四向刻度。只画在无几何的天空与地面像素上,不盖白模块体;不动深度全景。
    ground_plan(俯视布局图)+ cam_xz + floor_wd=(宽, 深):把俯视图按地面世界坐标贴到白模地面上(v2,2026-09-20)。白模地面是一整块
    平面,不分水 / 沙 / 草 / 路——fengshen3 SCN-0110 站在水里的 A2 / A5 / A7 锚点,成图脚下全是砂砾滩,水只在远处。贴上俯视图后
    [Image 1] 直接给出这个锚点脚下与四周是什么、岸线在哪(已按等距柱状弯好)。按像素在地面上的跨度选降采样层,免掠射处闪烁。"""
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
    if ground_plan is not None and floor_wd:
        fw, fd = float(floor_wd[0]), float(floor_wd[1])
        gx = cam_xz[0] + dirs[..., 0] * tg; gz = cam_xz[1] + dirs[..., 2] * tg
        u = gx / fw + .5; v = gz / fd + .5                                 # 俯视图:上缘 = -Z,左缘 = -X,整幅铺满白模地面
        inside = ground                                                    # 图幅之外按边缘延伸(水边外还是水),不留一圈「白地」被读成陆地
        u = np.clip(u, 0, 1 - 1e-6); v = np.clip(v, 0, 1 - 1e-6)
        foot = tg * pix / np.maximum(np.abs(dy), 1e-3)                     # 一像素在地面上的跨度(米)
        plan = ground_plan.convert('RGB')
        m_per_px = fw / plan.width
        level = np.clip(np.floor(np.log2(np.maximum(foot / m_per_px, 1.0))), 0, 6).astype(int)
        for lv in range(7):
            sel = inside & (level == lv)
            if not sel.any():
                continue
            small = np.asarray(plan.resize((max(1, plan.width >> lv), max(1, plan.height >> lv)), Image.BOX), dtype=np.float64)
            yy = np.clip((v[sel] * small.shape[0]).astype(int), 0, small.shape[0] - 1)
            xx = np.clip((u[sel] * small.shape[1]).astype(int), 0, small.shape[1] - 1)
            img[sel] = img[sel] * .15 + small[yy, xx] * .85
    a_ground = np.maximum(grid(tg, 1.0) * .8, grid(tg, 5.0)) * ground      # 5 = 奇数倍,粗线与细线重合
    if ground_plan is not None:
        a_ground = a_ground * .45                                           # 地面已有俯视图纹理:网格只留淡淡一层示意投影
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
        sdir = base / 'assets/concepts/scenes' / component(sid)
        plan_file = sdir / ((read(sdir / 'layout.json', {}) or {}).get('layout_top') or 'layout_top.png')
        plan = Image.open(plan_file) if plan_file.is_file() else None
        draw_projection_guides(Image.open(io.BytesIO(color_jpeg)), valid, raw_depth, camera[1], float(anchor.get('yaw_deg') or 0),
                               ground_plan=plan, cam_xz=(camera[0], camera[2]),
                               floor_wd=(scene['dimensions_m'][0], scene['dimensions_m'][2])).save(out / 'whitebox_pano.jpg', quality=92)
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


def scene_ancestors(base: Path, sid: str) -> list[str]:
    """[sid, 父, 祖父 …]:子场景(index.json#parent)常常只有 whitebox.json,光照方案 / 建筑设定挂在上级
    (fengshen3 SCN-0140 → SCN-0108 → SCN-0036,机位用的方案就叫 LGT-SCN-0036-DAY-B)。"""
    idx = read(base / 'bible/scenes/index.json', {}) or {}
    parents = {it.get('id'): it.get('parent') or it.get('parent_id')
               for it in ((idx.get('scenes') if isinstance(idx, dict) else idx) or []) if isinstance(it, dict)}
    chain = [sid]
    while parents.get(chain[-1]) and parents[chain[-1]] not in chain and len(chain) < 6:
        chain.append(parents[chain[-1]])
    return chain


def scene_name_of(base: Path, sid: str) -> str:
    idx = read(base / 'bible/scenes/index.json', {}) or {}
    for it in (idx.get('scenes') if isinstance(idx, dict) else idx) or []:
        if isinstance(it, dict) and it.get('id') == sid:
            return str(it.get('name') or '')
    return ''


def lighting_scheme(base: Path, sid: str, scheme: str) -> dict:
    for owner in scene_ancestors(base, sid):
        doc = read(base / 'bible/scenes' / component(owner) / 'lighting.json', {}) or {}
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
INDOOR_RULE = (
    "The camera is INSIDE a roofed room: the dark band across the top of the blockout is the solid ceiling / roof structure overhead and "
    "the tall blocks are solid walls — paint them as the real ceiling and walls. Open sky, clouds or outdoor scenery may appear only "
    "through the door and window openings of the blockout, at exactly their position and size, never above the walls or in place of them. "
)
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
    tall_top = max(0.6 * float(scene['dimensions_m'][1]), origin[1] + 1.5)
    tall_ids = [n_fixed + i for i, o in enumerate(scene.get('objects', [])) if float(o['position'][1]) + float(o['size_m'][1]) / 2 >= tall_top]
    walled = np.isin(ids, tall_ids)
    sectors = {}
    for name, c0 in (('centre', .5), ('right', .75), ('behind', 0.0), ('left', .25)):
        cols = (np.arange(int((c0 - .125) * w), int((c0 + .125) * w)) % w)
        tt = t[band][:, cols]
        sectors[name] = {'open': round(float((~np.isfinite(tt) | (tt > OPEN_DISTANCE_M)).mean()), 3),
                         'walled': round(float(walled[band][:, cols].mean()), 3),
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
        # 世界 → 相机局部:全景渲染是 世界 = M·局部(_equirect_rays 的 wx/wz),这里必须用 M 的逆,
        # 用成 M 本身会把列位转反 2×yaw —— yaw 90/270 时整整差 180°,左右半幅对调,成图与白模参考图永远对不上
        # (DEF-p6-pano-001,2026-09-21 fengshen3 SCN-0110:提示词说石台 23%,白模全景里在 72%)。
        lx = math.cos(yaw) * dx - math.sin(yaw) * dz; lz = math.sin(yaw) * dx + math.cos(yaw) * dz
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


def _lens_height_words(scene: dict, pos) -> str:
    floor = standing_surface(scene, pos)
    eye = round(float(pos[1]) - floor, 2)
    if floor < SURFACE_MIN_M:
        return f'{eye} m above the floor'
    return f'{eye} m above the surface it stands on, which is itself raised {round(floor, 1)} m above the ground below'


def pano_prompt(base: Path, sid: str, scheme: str, anchor: dict, *, indoor: bool, mode: str, time_of_day: str | None = None) -> str:
    """空场景全景文字:场所 + 锚点站位 + 四向内容(俯视图四边说明,按 yaw 归到画面中心/左右/身后)+ 光照方案 + 材质年代 + 风格。"""
    from modules.shot_plates import standing_on, orientation_axes, strip_compass
    from modules.whitebox import load_scene
    sdir = base / 'assets/concepts/scenes' / component(sid)
    bdir = base / 'bible/scenes' / component(sid)
    layout = read(sdir / 'layout.json', {}) or {}
    arch = read(bdir / 'architecture.json', {}) or {}
    if not arch:      # 子场景没有建筑设定:只从上级继承与空间无关的样式 / 年代 / 材质,形制、尺度、细部讲的是上级那个大空间,不继承
        for owner in scene_ancestors(base, sid)[1:]:
            up = read(base / 'bible/scenes' / component(owner) / 'architecture.json', {}) or {}
            if up:
                # 上级的材质表是整个大场景的(陈塘关:关墙 / 河滩 / 渔村 / 行宫 / 总兵府…):只留与本场景名或本场景地标同词的分句
                own = ' '.join([str(layout.get('scene_name') or ''), str(scene_name_of(base, sid))]
                               + [str(lm.get('name') or '') for lm in layout.get('landmarks', [])])
                own_words = {run[i:i + 2] for run in re.findall(r'[\u4e00-\u9fff]+', own) for i in range(len(run) - 1)
                             if not set(run[i:i + 2]) & _FUNCTION_CHARS}
                mats = [c.strip() for c in re.split(r'[;;。]', _flat(up.get('materials')) or '') if c.strip()
                        and any(w in c for w in own_words)]
                arch = {'arch_style': up.get('arch_style'), 'era_region': up.get('era_region'), 'materials': '; '.join(mats)}
                break
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
    elif indoor:
        parts.append(INDOOR_RULE.strip())
    parts.append(f"The camera stands {standing_on(scene, layout, {'position': anchor['position'], 'target': anchor['position']})}, "
                 f"lens {_lens_height_words(scene, anchor['position'])}, level horizon.")
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
# reach = 目标机位坐标:地板再外扩到机位脚下。超长焦远景机位(fengshen3 SCN-0052 ep06 sh001,场外 178 m)的下半幅射线否则从地板盒
# 底下穿过算成空洞(54.7% 里占 43%),白白触发 auto-self 兜底锚点。
def scene_boxes(scene: dict, indoor: bool, reach=None) -> list:
    w, h, d = scene['dimensions_m']
    gw, gd = w + 40.0, d + 40.0
    if reach is not None:
        gw, gd = max(gw, 2 * abs(float(reach[0])) + 40.0), max(gd, 2 * abs(float(reach[2])) + 40.0)
    boxes = [((0.0, -0.05, 0.0), (gw, 0.05, gd), 0.0)]
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
    boxes = scene_boxes(load_scene(base, sid), indoor, reach=origin)
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
                  time_of_day: str | None = None, log=print, cameras: list | None = None) -> dict:
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
        # 没跟白模的全景不当链式父图:重投影用的是白模深度,成图与白模对不上时参考图是一片拉花、还与白模参考图互相矛盾,
        # 子锚点两张都不跟(fengshen3 SCN-0121:A1 对齐 30% / 基线 34% → A2、A3、A4 整条链带歪)。出图前停,不花钱。
        # 环保护(含「正在重出的就是带歪整条链的那个根锚点」:后代直接不用,没有别的父图就不带父图新出,不拦它自己):
        # 任何(直接或间接)从本锚点链出来的全景都不当父图 —— 重出本锚点时拿自己的子孙当参考,等于把上一版的偏差
        # 绕回来再叠一层(fengshen3 SCN-0110:A7 链自旧 A2,重出 A2 时按距离又选中 A7,接缝比 4.8 → 20.8)。不看一致性,一律排除。
        offspring = [a for a in donors if _descends_from(base, sid, a['anchor_id'], anchor['anchor_id'], scheme)]
        if offspring:
            donors = [a for a in donors if a not in offspring]
            log(f"   跳过本锚点的子孙全景 {[a['anchor_id'] for a in offspring]}(它们是从本锚点链出来的,当父图会把偏差绕回来)")
        untrusted = [a for a in donors if chain_donor_distrust(base, sid, a, scheme)]
        donors = [a for a in donors if a not in untrusted]
        if untrusted and not donors:
            why = ';'.join(f"{a['anchor_id']}({chain_donor_distrust(base, sid, a, scheme)})" for a in untrusted)
            raise PanoProjectionError(
                f"{sid}/{anchor['anchor_id']}/{scheme}: 可作链式父图的全景都没跟白模:{why},未出图、本批停下。"
                f"请用户在预览页对照白模全景核对父锚点:确实画偏 → render_scene_panos.py --redo <父锚点>(重出次数计入用户设定的重跑次数);"
                f"用户目视认可 → render_scene_panos.py --trust <父锚点>(不花钱;Agent 不得自行使用)。")
        if untrusted:
            log(f"   跳过没跟白模的链式父图 {[a['anchor_id'] for a in untrusted]},改用次近的锚点")
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
        if int((read(out / 'depth_pano.json', {}) or {}).get('guides') or 0) >= 2:
            rules.append(GROUND_PLAN_RULE.format(n=refs.index(wb) + 1))
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
    rec = {'file': target.name, 'scheme': scheme, 'mode': mode, 'parent': parent, 'time_of_day': time_of_day,
           'channel': {'provider': cfg.get('provider'), 'model': cfg.get('model')}, 'size': [rw, rh], 'seed': seed,
           'refs': [str(r.relative_to(base)) if str(r).startswith(str(base)) else str(r) for r in refs],
           'prompt': prompt, 'negative': NEGATIVE, 'anchor': {'position': anchor['position'], 'yaw_deg': anchor.get('yaw_deg', 0)},
           'written_at': _now()}
    rec['projection_check'] = proj = projection_check(target, usage=anchor_usage(anchor, cameras if cameras is not None else scene_cameras(base, sid)))
    rec['conformity_check'] = conf = conformity_check(target, out / 'depth_pano.npy')
    for kind, res, what in (('projection', proj, '成图不是等距柱状全景'), ('conformity', conf, '成图没有跟白模')):
        if res and res['verdict'] == 'FAIL':
            rejected = target.with_suffix(f'.rejected-{kind}-' + dt.datetime.now().strftime('%Y%m%d-%H%M%S') + '.png')
            target.rename(rejected)
            # 被拒图也留 sidecar(提示词 / seed / 机检数值):判据有误拒时用户目视认可后 --adopt 免费认领,不必再花钱重出
            rejected.with_suffix('.json').write_text(json.dumps({**rec, 'file': rejected.name, 'rejected': kind}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            raise PanoProjectionError(
                f"{sid}/{anchor['anchor_id']}/{scheme}: {what}({res['reason']}),已改名 {rejected.name},本批停下。"
                f"重出:render_scene_panos.py --redo {anchor['anchor_id']}(换 seed),重出次数计入用户设定的重跑次数,用尽即上报用户,不得无限重出;"
                f"用户目视认可这张时:render_scene_panos.py --adopt {anchor['anchor_id']}(不花钱;Agent 不得自行认领)。")
        if res and res['verdict'] == 'WARN':
            log(f"   WARN {'投影机检' if kind == 'projection' else '白模一致性'}:{res['reason']}")
    if mode == 'chain':
        score = chain_consistency(chain, target)
        parent['consistency'] = score
        if score is not None and score < CONSISTENCY_WARN:
            log(f"   WARN 链式一致性 {score:.2f} < {CONSISTENCY_WARN}:请在预览页对照 {parent['anchor_id']} 全景核对,不一致用 render_scene_panos.py --redo {anchor['anchor_id']} 重出")
    _commit_pano(base, sid, idx, anchor, scheme, rec)
    log(f"saved: {target.relative_to(base)}")
    return rec


def _commit_pano(base: Path, sid: str, idx: dict, anchor: dict, scheme: str, rec: dict):
    out = panos_dir(base, sid) / anchor['anchor_id']
    (out / f'{scheme}.json').write_text(json.dumps(rec, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    anchor.setdefault('panos', {})[scheme] = {k: rec.get(k) for k in ('file', 'mode', 'parent', 'channel', 'seed', 'size', 'time_of_day', 'written_at')}
    save_index(base, sid, idx)


ARCHIVED_PANO_RE = re.compile(r'\.(?:rejected-[a-z]+|redo)-')   # 归档成图文件名:<scheme>.rejected-<判据>-<时间>.png / <scheme>.redo-<时间>.png


def _pose_differs(side: dict, anchor: dict) -> bool:
    """归档图 sidecar 记下的出图位姿与锚点当前位姿不符(出图后锚点挪过 / 改过 yaw)。sidecar 没记位姿的存量图按相符算。"""
    rec = side.get('anchor') or {}
    if not rec.get('position'):
        return False
    return (math.dist(rec['position'], anchor['position']) > .05
            or abs(float(rec.get('yaw_deg') or 0) - float(anchor.get('yaw_deg') or 0)) > .5)


def adopt_rejected(base: Path, sid: str, anchor_id: str, scheme: str | None = None, log=print, pick: str | None = None) -> dict:
    """用户目视认可后把该锚点最新一张归档成图认领为正式全景(不花钱):既认 .rejected-*(判据拒掉的),也认 .redo-*
    (--redo 重出时归档的上一版正式全景——重出反而更差时用户可以要回旧的)。仍过一遍 2:1 与当前判据,结果照实记入 sidecar
    (adopted.checks_at_adopt);已有正式全景时不覆盖。存量归档图没有 sidecar 时 seed / 提示词记空。
    pick = 文件名片段(如时间戳 20260921-084915)时只认领那一张。按别的位姿出的归档图不认领:列位随 yaw 平移,认领后重投影的
    分镜背景图整体转向(fengshen3 SCN-0110 A2:yaw 270 / 180 两种被拒图并存,最新一张恰是试过又撤回的 180)。"""
    from PIL import Image
    idx = load_index(base, sid)
    anchor = next((a for a in idx['anchors'] if a['anchor_id'] == anchor_id), None)
    if not anchor:
        raise PanoError(f'{sid}: 锚点不存在:{anchor_id}')
    out = panos_dir(base, sid) / anchor_id
    files = sorted((f for f in out.glob('*.png') if ARCHIVED_PANO_RE.search(f.name)
                    and (scheme is None or f.name.startswith(scheme + '.')) and (not pick or pick in f.name)),
                   key=lambda f: f.stat().st_mtime)
    if not files:
        raise PanoError(f'{sid}/{anchor_id}: 没有可认领的归档成图(.rejected-* / .redo-*)' + (f'(文件名含 {pick})' if pick else ''))
    moved = [f for f in files if _pose_differs(read(f.with_suffix('.json'), {}) or {}, anchor)]
    files = [f for f in files if f not in moved]
    if moved:
        log(f"   跳过按别的位姿出的被拒图 {[f.name for f in moved]}(锚点当前 yaw {anchor.get('yaw_deg', 0)}°)")
    if not files:
        raise PanoError(f'{sid}/{anchor_id}: 被拒成图都是按别的锚点位姿出的,不能认领(锚点当前 yaw {anchor.get("yaw_deg", 0)}°)')
    src = files[-1]
    scheme = scheme or ARCHIVED_PANO_RE.split(src.name)[0]
    target = out / f'{scheme}.png'
    if target.is_file():
        raise PanoError(f'{sid}/{anchor_id}/{scheme}: 已有正式全景 {target.name},不覆盖(要换先 --redo 或手工挪走)')
    rw, rh = Image.open(src).size
    if abs(rw / rh - 2.0) > ASPECT_TOLERANCE:
        raise PanoError(f'{src.name}: {rw}x{rh} 不是 2:1,不能认领')
    side = read(src.with_suffix('.json'), {}) or {}
    if (out / 'depth_pano.json').is_file() and whitebox_pano_stale_at(out, anchor):                 # 白模深度是按别的位姿渲的(改过 yaw 又撤回):一致性判据与之后的重投影都会错位
        render_whitebox_pano(base, sid, anchor, indoor=bool(anchor.get('indoor', (read(out / 'depth_pano.json', {}) or {}).get('indoor'))), log=log)
    checks = {'projection_check': projection_check(src), 'conformity_check': conformity_check(src, out / 'depth_pano.npy')}
    rec = {'mode': 'fresh', 'parent': None, 'time_of_day': None, 'channel': None, 'seed': None, 'refs': [], 'prompt': None, 'negative': None,
           **{k: v for k, v in side.items() if k not in ('rejected',)}, **checks,
           'file': target.name, 'scheme': scheme, 'size': [rw, rh], 'anchor': {'position': anchor['position'], 'yaw_deg': anchor.get('yaw_deg', 0)},
           'adopted': {'from': src.name, 'by': 'user', 'at': _now(),
                       'rejected_as': side.get('rejected') or (src.name.split('.rejected-')[1].split('-')[0] if '.rejected-' in src.name else 'redo')},
           'written_at': _now()}
    src.rename(target)
    if src.with_suffix('.json').is_file():
        src.with_suffix('.json').unlink()
    _commit_pano(base, sid, idx, anchor, scheme, rec)
    log(f"   已认领 {src.name} → {target.name};当前判据:投影 {(checks['projection_check'] or {}).get('verdict')} / 白模一致性 {(checks['conformity_check'] or {}).get('verdict')}")
    return rec


NADIR_ANISO_FAIL = 0.65          # 天底带 横向细节 / 纵向细节:等距柱状里天底被横向拉伸,纹理成横向拉丝 → 实测合格 0.34–0.58、广角照片 0.71–0.76
NADIR_ANISO_SOFT = 0.55
NADIR_DETAIL_WARN = 0.7          # 底部横向细节 / 中段横向细节:中段是水面 / 雾 / 纯墙时会被放大(SCN-0110 重出图 1.37 却是合格全景,误拒)→ 只作 WARN
SEAM_RATIO_WARN = 3.0            # 左右缘色差 / 相邻列色差基线;接缝不上只 WARN(合格全景也常见),与极区指标同时超限才 FAIL


SEAM_RATIO_FAIL = 5.0            # 接缝比超过此值、且确有服务机位的视域跨过接缝 → FAIL(接缝会落进背景图正中)


def projection_check(path: Path, usage: dict | None = None) -> dict | None:
    """成图是不是等距柱状:① 天底带各向异性(主判据,自归一,不受中段内容影响)② 天顶/天底行方差 ③ 左右缘接缝。
    标定样本:dzg6 SCN-0002 / liaozhai3 SCN-0005 / fengshen3 SCN-0140、0046(合格)对 fengshen3 SCN-0110 首批 A1–A12(2:1 广角照片);
    2026-09-20 重出的 SCN-0110 A1(地平线居中、直线已弯、天底已拉丝,但中段是平滑水面)曾被旧主判据「底部/中段横向细节比」误拒。"""
    try:
        import numpy as np
        from PIL import Image
        im = Image.open(path).convert('RGB')
        g = np.asarray(im.convert('L').resize((1024, 512), Image.BOX), dtype=np.float64)
        dx = np.abs(np.diff(g, axis=1))
        mid = max(float(dx[154:358].mean()), 1e-3)
        nadir, zenith = float(dx[-26:].mean()) / mid, float(dx[:26].mean()) / mid
        band = g[-40:-4]
        aniso = float(np.abs(np.diff(band, axis=1)).mean()) / max(float(np.abs(np.diff(band, axis=0)).mean()), 1e-3)
        s = np.asarray(im.resize((256, 128), Image.BOX), dtype=np.float64)
        seam = float(np.abs(s[:, 0] - s[:, -1]).mean())
        inner = float(np.mean([np.abs(s[:, c] - s[:, c + 1]).mean() for c in range(8, 247, 8)]))
        seam_ratio = seam / max(inner, 2.0)
        top_std, bot_std = float(s[:3].std(axis=1).mean()), float(s[-3:].std(axis=1).mean())
    except Exception:  # noqa: BLE001
        return None
    rec = {'nadir_aniso': round(aniso, 2), 'nadir_detail': round(nadir, 2), 'zenith_detail': round(zenith, 2), 'seam_ratio': round(seam_ratio, 1),
           'top_row_std': round(top_std, 1), 'bottom_row_std': round(bot_std, 1)}
    if aniso > NADIR_ANISO_FAIL:
        return {**rec, 'verdict': 'FAIL', 'reason': f"天底未拉伸:底部纹理横/纵细节比 {aniso:.2f} > {NADIR_ANISO_FAIL}(像广角照片的清晰前景)"}
    if aniso > NADIR_ANISO_SOFT and top_std > 25 and bot_std > 15 and seam_ratio > SEAM_RATIO_WARN:
        return {**rec, 'verdict': 'FAIL', 'reason': f"天顶/天底不成色带(行方差 {top_std:.0f}/{bot_std:.0f})且左右缘接不上(接缝比 {seam_ratio:.1f})"}
    # 局部缺陷按服务机位的实际视域判:没有机位碰到的只记 notes 不拦不警;碰到的才 WARN / FAIL。usage 未知(没传机位)按碰到算。
    # 注意主判据(天底各向异性)不在此列:它超限说明整张图不是等距柱状,哪个方向重投影都是错的。
    known = bool(usage and usage.get('known'))
    seam_hit = (usage or {}).get('seam') or []; nadir_hit = (usage or {}).get('nadir') or []
    if known:
        rec['usage'] = {'cameras': usage['cameras'], 'seam': seam_hit, 'nadir': nadir_hit}
    names = lambda ks: '、'.join(ks[:4]) + (f' 等 {len(ks)} 个' if len(ks) > 4 else '')
    warns, notes = [], []
    if nadir > NADIR_DETAIL_WARN:
        if known and not nadir_hit:
            notes.append(f"近地前景偏实({nadir:.2f}),但没有服务机位俯拍到天底带,不影响")
        else:
            warns.append(f"近地前景偏实(底部/中段横向细节比 {nadir:.2f})" + (f",俯拍机位 {names(nadir_hit)} 的背景图留意地面纹理尺度" if nadir_hit else ''))
    if seam_ratio > SEAM_RATIO_WARN:
        if known and not seam_hit:
            notes.append(f"左右缘接不上(接缝比 {seam_ratio:.1f}),但接缝方向没有服务机位看到,不影响")
        elif known and seam_ratio > SEAM_RATIO_FAIL:
            return {**rec, 'verdict': 'FAIL', 'reason': f"左右缘接不上(接缝比 {seam_ratio:.1f} > {SEAM_RATIO_FAIL}),且机位 {names(seam_hit)} 的视域跨过接缝,接缝会落进背景图"}
        else:
            warns.append(f"左右缘接不上(接缝比 {seam_ratio:.1f} > {SEAM_RATIO_WARN})" + (f",机位 {names(seam_hit)} 的视域跨过接缝,其背景图留意接缝" if seam_hit else ''))
    if warns:
        return {**rec, 'verdict': 'WARN', 'reason': ';'.join(warns) + ',请在预览页核对', 'notes': notes}
    return {**rec, 'verdict': 'PASS', 'reason': '', 'notes': notes}


CONFORMITY_BUSY_NULL = 0.75      # 成图处处是边缘(机场大厅)时错位也能对上,指标失效 → 不判
CONFORMITY_FAIL_S0 = 0.30
CONFORMITY_WARN_Z = 2.0
CHAIN_DONOR_MIN_ALIGNED = 0.50   # 链式父图保护:对齐度低于此且不高于错位基线才不当父图


def conformity_check(result: Path, depth_npy: Path) -> dict | None:
    """成图有没有跟白模:白模深度全景的轮廓线(墙脚 / 墙角 / 门洞 / 家具外缘)在成图里 3 px 内找得到边缘的比例 s0,
    与把轮廓横向错开后的比例(null)比。跟了白模:s0 明显高于 null(liaozhai3 SCN-0005 0.88 对 0.31、fengshen3 SCN-0140 A1 0.86 对 0.53);
    画成了别的视点(fengshen3 SCN-0046 A1 洞内锚点画成洞府外观):s0 0.13、低于 null。
    **只出 WARN / PASS / N/A,不出 FAIL**(见函数内说明)。"""
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
    # 只警不拦(2026-09-20 晚订正):本指标看的是白模轮廓在成图里有没有边缘,而白模轮廓大头是墙顶线 / 墙角线——岩洞、暗场、
    # 有机形体里模型把它们画成连续岩面是对的,没有边缘;暗图 Canny 也提不出边。实测 SCN-0046 A1 两张同样跟了白模的洞内全景
    # (法宝架、丹炉逐块对位)对齐度 27% 与 26%,只因错位基线 22% / 15% 的噪声一张被拒一张放行。不足以当花钱重出的闸门。
    if s0 < CONFORMITY_FAIL_S0 and z < 1.0:
        return {**rec, 'verdict': 'WARN', 'reason': f"白模轮廓只有 {s0:.0%} 在成图里找得到(错位基线 {mean:.0%}):若成图是别的视点 / 建筑外观请重出;"
                                                     "暗场、岩洞等墙顶墙角不成线的空间属正常,目视对照白模全景即可"}
    if z < CONFORMITY_WARN_Z:
        return {**rec, 'verdict': 'WARN', 'reason': f"成图与白模轮廓对齐度不高于错位基线(对齐 {s0:.0%} / 基线 {mean:.0%}),请在预览页对照白模全景核对"}
    return {**rec, 'verdict': 'PASS', 'reason': ''}


def _descends_from(base: Path, sid: str, anchor_id: str, ancestor_id: str, scheme: str, _seen: tuple = ()) -> bool:
    """anchor_id 的全景是否(直接或间接)从 ancestor_id 链式补洞而来。重出 ancestor_id 时用它排除自己的子孙,避免偏差绕环放大。"""
    if anchor_id == ancestor_id:
        return True
    if anchor_id in _seen:
        return False
    rec = read(panos_dir(base, sid) / anchor_id / f'{scheme}.json', {}) or {}
    if rec.get('mode') != 'chain':
        return False
    up = (rec.get('parent') or {}).get('anchor_id')
    if not up or up == anchor_id:
        return False
    return _descends_from(base, sid, up, ancestor_id, scheme, _seen + (anchor_id,))


def _chain_distrust(base: Path, sid: str, anchor_id: str, scheme: str, _seen: tuple = ()) -> tuple:
    """(根锚点, 原因);可用则 (None, '')。"""
    rec = read(panos_dir(base, sid) / anchor_id / f'{scheme}.json', {}) or {}
    conf = rec.get('conformity_check') or {}
    if rec.get('adopted') or rec.get('conformity_ack'):
        return None, ''
    up = (rec.get('parent') or {}).get('anchor_id') if rec.get('mode') == 'chain' else None
    if up and up != anchor_id and up not in _seen:          # 它自己就是从没跟白模的父图链出来的:同样带歪
        # 父锚点在它之后重出 / 换过图:它链的是旧版父图。链式参考图压过文字,子全景的朝向整张继承父图——旧父图画反,子图跟着反,
        # 且白模一致性 z 在开阔外景分不出来(fengshen3 SCN-0110:四张提示词列位同样转反 50%,A3←A1、A6←A3 方向正确,
        # A5、A7←旧 A2(空洞 37% 文字占上风画反)跟着反;z 却是 A3 −2.7 最低)。父图换了就不再当父图,等用户核对(--trust)或重出。
        up_rec = read(panos_dir(base, sid) / up / f'{scheme}.json', {}) or {}
        if up_rec.get('written_at') and rec.get('written_at') and str(up_rec['written_at']) > str(rec['written_at']):
            return anchor_id, f"链自 {up} 的旧版全景({up} 已于 {up_rec['written_at']} 换图)"
        root, why = _chain_distrust(base, sid, up, scheme, _seen + (anchor_id,))
        if why:
            return root, f"链自 {up}:{why}"
    if conf.get('verdict') != 'WARN' or float(conf.get('z') or 0) > 0:
        return None, ''
    if float(conf.get('aligned') or 0) >= CHAIN_DONOR_MIN_ALIGNED:
        return None, ''                                     # 错位基线本身就高的开阔外景(fengshen3 SCN-0110 对齐 50–68% / 基线 65–73%):指标不灵,不拦
                                                            # (0.40 → 0.50:SCN-0110 对齐 < 50% 的几张是列位文字转反画偏的 DEF-p6-pano-001,不是指标失灵)
    return anchor_id, f"对齐 {conf.get('aligned', 0):.0%} 不高于错位基线 {conf.get('null', 0):.0%}"


def chain_donor_distrust(base: Path, sid: str, anchor: dict, scheme: str) -> str:
    """该锚点全景不宜当链式父图的原因,可用则返回 ''。判据比 conformity_check 的 WARN 窄:白模轮廓对齐度不高于错位基线(z ≤ 0)且绝对值
    < CHAIN_DONOR_MIN_ALIGNED 才算,岩洞 / 暗场那类「对齐度低但仍高于基线」的 WARN 照常可用;从这种父图链出来的子全景同样不用。
    用户认领(--adopt)或认可(--trust)过的不拦。"""
    return _chain_distrust(base, sid, anchor['anchor_id'], scheme)[1]


def trust_pano(base: Path, sid: str, anchor_id: str, scheme: str | None = None, log=print) -> list:
    """用户目视认可该锚点全景跟了白模(机检误报):sidecar 记 conformity_ack,之后可当链式父图。不花钱。"""
    out = panos_dir(base, sid) / anchor_id
    sides = [out / f'{scheme}.json'] if scheme else [p for p in sorted(out.glob('*.json')) if p.name != 'depth_pano.json' and '.re' not in p.name]
    done = []
    for side in sides:
        rec = read(side, None)
        if not rec or not (out / rec.get('file', '')).is_file():
            continue
        rec['conformity_ack'] = {'by': 'user', 'at': _now()}
        side.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        done.append(side.stem); log(f"   已认可 {anchor_id}/{side.stem} 可作链式父图")
    if not done:
        raise PanoError(f"{sid}/{anchor_id}: 没有可认可的全景" + (f"(方案 {scheme})" if scheme else ''))
    return done


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


def add_manual_anchor(base: Path, sid: str, x: float, z: float, yaw_deg: float = 0.0, *, cameras: list | None = None,
                      persist: bool = True) -> dict:
    """手动加一个锁定锚点(预览页俯视图点选 / CLI --anchor):坐标夹回白模地面内 0.5 m,高度 = 该点站立面 + 眼高(城墙顶/楼上随最近机位那一层),
    serves = 尚无锚点服务且它能服务的机位(不抢已有锚点的机位,不改动其它锚点)。写回 index.json,返回新锚点。
    persist=False(CLI --dry-run):只算出这个锚点会是什么样,不写索引。"""
    from modules.whitebox import load_scene
    sid = component(sid)
    scene = load_scene(base, sid)
    scene['_outdoor'] = scene_indoor(base, sid) is False
    scene['_mixed'] = scene_indoor(base, sid) is None
    cams = scene_cameras(base, sid) if cameras is None else cameras
    idx = load_index(base, sid)
    w, _, d = scene['dimensions_m']
    px = round(max(-w / 2 + .5, min(w / 2 - .5, float(x))), 3)
    pz = round(max(-d / 2 + .5, min(d / 2 - .5, float(z))), 3)
    used = {a['anchor_id'] for a in idx['anchors']}
    n = len(idx['anchors']) + 1
    while f'A{n}' in used:
        n += 1
    served = {k for a in idx['anchors'] for k in a.get('serves', [])}
    pos = anchor_pos_at(scene, px, pz, cams, default_anchor_height(cams, scene))   # 该点站立面 + 眼高(与最近机位同层)
    serves = sorted(_cam_key(c) for c in cams if _cam_key(c) not in served and can_serve(pos, c, scene))
    anchor = {'anchor_id': f'A{n}', 'position': pos, 'yaw_deg': float(yaw_deg or 0.0), 'source': 'manual', 'locked': True,
              'serves': serves, 'panos': {}}
    if persist:
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
    override = indoor
    flag = scene_indoor(base, sid) if override is None else bool(override)
    idx['indoor'] = flag                     # None = 内外混合,逐锚点见 anchors[].indoor
    scene['_outdoor'] = flag is False
    scene['_mixed'] = flag is None           # 混合场景的服务半径逐点判(锚点与机位都在室外才放宽)
    idx['serve_min_m'] = serve_min_m(scene)
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
            extra = plan_anchors(scene, new_cams, existing=[])
            used = {a['anchor_id'] for a in idx['anchors']}
            n = 1
            for a in extra:
                while f'A{n}' in used:
                    n += 1
                a['anchor_id'] = f'A{n}'; used.add(a['anchor_id'])
            idx['anchors'].extend(extra)
    # 存量自动锚点 yaw 都是 0:还没出过任何图(含被拒待认领的)的,按服务机位重选接缝朝向——只重渲白模,不花钱;出过图的不动
    by_key = {_cam_key(c): c for c in cams}
    for a in idx['anchors']:
        adir = panos_dir(base, sid) / a['anchor_id']
        if a.get('locked') or not str(a.get('source') or '').startswith('auto') or a.get('panos') or (adir.is_dir() and any(adir.glob('*.png'))):
            continue
        yaw = pick_seam_yaw([by_key[k] for k in a.get('serves', []) if k in by_key])
        if abs(yaw - float(a.get('yaw_deg') or 0)) > .5:
            log(f"   {a['anchor_id']}: 接缝朝向改到机位最少看到的一侧,yaw {a.get('yaw_deg', 0)}° → {yaw}°")
            a['yaw_deg'] = yaw
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
    stats = {'anchors': [a['anchor_id'] for a in idx['anchors']], 'new': 0, 'pending': [], 'indoor': flag, 'scene_id': sid}   # indoor: None = 内外混合(逐锚点判)
    for a in idx['anchors']:
        if only and a['anchor_id'] not in only:
            continue
        a['indoor'] = anchor_indoor(base, sid, scene, a, override)
        if force or whitebox_pano_stale(base, sid, a):
            render_whitebox_pano(base, sid, a, indoor=a['indoor'], log=log)
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
        generate_pano(base, sid, idx, a, s, indoor=a['indoor'], seed=seed, time_of_day=t, log=log, cameras=cams)
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
                                  for s, rec in (a.get('panos') or {}).items()},
                        'archived': archived_panos(base, sid, a)})
    return {'anchors': anchors, 'indoor': idx.get('indoor'), 'planned_at': idx.get('planned_at'), 'blocked': idx.get('blocked'),
            'dimensions_m': dims}


def archived_panos(base: Path, sid: str, anchor: dict) -> list[dict]:
    """预览页「归档图」子行(2026-09-25):该锚点目录下不入索引的成图——机检拒掉的 <scheme>.rejected-<判据>-<时间>.png、
    --redo 时归档的上一版 <scheme>.redo-<时间>.png、非 2:1 的 <scheme>.rejected.png。用户目视判断误拒与否要看得到这些图
    (SCN-long-hall A1 接缝比 5.4 被拒,页面上却无处可看)。sidecar 有就带出拒因 / 机检数值 / 渠道 / seed;adoptable = --adopt
    会认领(归档正则匹配 + 位姿与锚点当前一致 + 2:1)。按时间倒序,最新在前。"""
    out = panos_dir(base, sid) / str(anchor.get('anchor_id') or '')
    if not out.is_dir():
        return []
    rows = []
    for f in out.glob('*.png'):
        archived = bool(ARCHIVED_PANO_RE.search(f.name))
        aspect_rejected = f.name.endswith('.rejected.png')
        if not (archived or aspect_rejected):
            continue
        side = read(f.with_suffix('.json'), {}) or {}
        m = re.search(r'\.(rejected-([a-z]+)|redo)-(\d{8}-\d{6})\.png$', f.name)
        kind = 'aspect' if aspect_rejected else (side.get('rejected') or (m.group(2) if m and m.group(2) else 'redo'))
        scheme = ARCHIVED_PANO_RE.split(f.name)[0] if archived else f.name[:-len('.rejected.png')]
        checks = {k: {kk: side[k][kk] for kk in ('verdict', 'reason') if kk in side[k]}
                  for k in ('projection_check', 'conformity_check') if isinstance(side.get(k), dict)}
        size = side.get('size')
        rows.append({'file': f.name, 'scheme': scheme, 'kind': kind, 'stamp': m.group(3) if m else None,
                     'mtime': int(f.stat().st_mtime), 'written_at': side.get('written_at'),
                     'channel': side.get('channel'), 'seed': side.get('seed'), 'size': size, 'mode': side.get('mode'),
                     'checks': checks, 'pose_differs': _pose_differs(side, anchor),
                     'adoptable': archived and not _pose_differs(side, anchor)
                                  and not (isinstance(size, list) and len(size) == 2 and size[1] and abs(size[0] / size[1] - 2.0) > ASPECT_TOLERANCE)})
    rows.sort(key=lambda r: r['mtime'], reverse=True)
    return rows
