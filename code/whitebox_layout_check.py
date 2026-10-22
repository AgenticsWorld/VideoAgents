#!/usr/bin/env python3
"""whitebox_layout_check.py — 场景白模 × 俯视图对齐机检(whitebox_layout_ok)。

背景(2026-09-09,dzg6 SCN-0001/SCN-0004 反馈):场景预览「实景图」视图把 layout_top.png 按 dimensions_m
[X, Z] 铺满地面,白模墙线若不是按同一张图量出来的,就会出现墙偏出地面、门洞错位、凭空多墙;dimensions_m
的 X:Z 若不等于图幅宽高比,整张图还会被单向拉伸(米制家具尺寸随之失真)。本脚本:
  1. 比例:dimensions_m X/Z 与 layout_top.png 宽/高比偏差 > 2% → FAIL(图幅整体铺地,必须同比);
  2. 出图:在 layout_top.png 上叠 1m 网格 + 全部几何体脚印(按 yaw 旋转的多边形;墙体红、其余蓝、地标黄点)写
     assets/concepts/scenes/<sid>/whitebox_overlay.png,供建模 Agent 与用户目视核对墙线是否压在图上墙位;
  3. 越界:任一几何体旋转后脚印四角超出图幅 → FAIL;
  4. 列出墙体归一化范围便于逐段比对。
比例豁免(#154,2026-10-22):仅当用户已签字豁免比例偏差时(俯视图是生成模型出的示意图、用户接受图面不等比而不重出图),
  whitebox.json 写可选键 `layout_check_waiver: {"defect_id": "DEF-…", "items": ["aspect"]}`;本脚本读
  qa/defects/<defect_id>.json,须 status 为 waived(或 waived_by_user)、waiver.signed_by 为用户(user… / 用户…,
  Agent 不能自签)、scene_id 为本场景,比例项才记为豁免(报告 waived 列出该项与 defect_id,不算不合格);
  items 只认 "aspect"——越界等其它检查项一律不能豁免;条件不满足时比例项照旧 FAIL 并写明豁免为何无效。
  该键只供本机检读,不进编译产物,也不计入白模审查指纹(modules/whitebox_camera.SCENE_DESCRIPTIVE_KEYS)。
用法:python code/whitebox_layout_check.py --project <slug> --scene SCN-0001 [--scene SCN-0004 ...]
      不带 --scene 时检查项目内所有已建白模(bible/scenes/*/whitebox.json 含 objects)的场景。
本脚本是宿主 CLI:Agent 只准按上方用法调用,禁止复制/改写到项目 code/。退出码:全部 PASS 0,否则 1。
"""
import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _common import parse_args
from modules.whitebox import component, load_scene, read

ASPECT_TOLERANCE = 0.02
WALL_ROLES = ('wall',)
WAIVABLE_ITEMS = ('aspect',)                     # #154:只有比例项可凭用户签字的缺陷单豁免
WAIVED_STATUSES = ('waived', 'waived_by_user')   # 缺陷单已豁免的状态写法(与 core._post_defects 的已关闭口径一致)
USER_SIGNER_RE = re.compile(r'^(user|用户)(?![A-Za-z0-9_-])', re.I)   # user / user:chat / user(…) / 用户(…);full-auto、Agent 名不算


def aspect_waiver(base, sid, authored):
    """#154:whitebox.json#layout_check_waiver 能否豁免比例项。返回 (defect_id, None) 有效;(None, 原因) 无效;未声明 (None, None)。"""
    decl = authored.get('layout_check_waiver') if isinstance(authored, dict) else None
    if decl is None:
        return None, None
    if not isinstance(decl, dict):
        return None, 'layout_check_waiver 须为 {"defect_id": "DEF-…", "items": ["aspect"]}'
    items = decl.get('items')
    if not isinstance(items, list) or not items or any(item not in WAIVABLE_ITEMS for item in items):
        return None, 'items 只能写 ["aspect"](只有比例项可豁免,越界等其它检查项不能豁免)'
    did = decl.get('defect_id')
    try:
        component(did)
    except ValueError:
        return None, f'defect_id {did!r} 不是合法缺陷单号'
    path = base / 'qa' / 'defects' / f'{did}.json'
    try:
        defect = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None, f'缺陷单 qa/defects/{did}.json 不存在'
    except (OSError, ValueError):
        return None, f'缺陷单 qa/defects/{did}.json 读不出 JSON'
    if not isinstance(defect, dict) or defect.get('defect_id', did) != did:
        return None, f'qa/defects/{did}.json 不是缺陷单 {did}'
    status = str(defect.get('status') or '').strip()
    if status.lower() not in WAIVED_STATUSES:
        return None, f'缺陷单 {did} 状态为 {status or "空"},不是 waived(须用户签字豁免)'
    waiver = defect.get('waiver')
    signer = str((waiver.get('signed_by') if isinstance(waiver, dict) else '') or '').strip()
    if not USER_SIGNER_RE.match(signer):
        return None, f'缺陷单 {did} 的 waiver.signed_by 为 {signer or "空"},不是用户签字(Agent 不能自签豁免)'
    scene_ids = defect.get('scene_ids') if isinstance(defect.get('scene_ids'), list) else []
    if defect.get('scene_id') != sid and sid not in scene_ids:
        return None, f'缺陷单 {did} 的 scene_id 为 {defect.get("scene_id")!r},不是本场景 {sid}'
    return did, None


def footprint_polygon(obj, dims):
    """脚印四角(归一化图幅坐标)。按渲染器 whitebox-renderer.js mesh.rotation.set(pitch, yaw, roll) 的
    three.js rotation.y 约定旋转(#78):局部 X → (cos yaw, −sin yaw),局部 Z → (sin yaw, cos yaw)(世界 X, Z)。
    俯视脚印只计 yaw;pitch/roll 不影响(白模场景物体几乎不用)。"""
    sx, _, sz = obj['size_m']
    px, _, pz = obj['position']
    W, _, D = dims
    yaw = float(obj.get('yaw') or 0)
    c, s = math.cos(yaw), math.sin(yaw)
    corners = []
    for lx, lz in ((-sx / 2, -sz / 2), (sx / 2, -sz / 2), (sx / 2, sz / 2), (-sx / 2, sz / 2)):
        wx = px + lx * c + lz * s
        wz = pz - lx * s + lz * c
        corners.append((wx / W + .5, wz / D + .5))
    return corners


def footprint(obj, dims):
    """旋转后脚印的轴对齐范围 (x0, y0, x1, y1),越界判定与 walls 输出用。"""
    corners = footprint_polygon(obj, dims)
    xs, ys = [c[0] for c in corners], [c[1] for c in corners]
    return (min(xs), min(ys), max(xs), max(ys))


def is_wall(obj):
    return obj.get('collision_role') in WALL_ROLES or any(k in obj['id'] for k in ('wall', 'partition', 'sill', 'lintel', 'head', 'jamb'))


def check_scene(base, sid, write_overlay=True):
    from PIL import Image, ImageDraw, ImageFont
    scene = load_scene(base, sid)
    authored = read(base / 'bible/scenes' / sid / 'whitebox.json', {})
    dims = scene['dimensions_m']
    image = base / scene['layout_top']
    problems, notes, waived = [], [], []
    if not image.is_file():
        return {'scene_id': sid, 'status': 'FAIL', 'problems': [f'俯视图不存在: {scene["layout_top"]}'], 'notes': notes}
    im = Image.open(image).convert('RGB')
    iw, ih = im.size
    ratio_model, ratio_image = dims[0] / dims[2], iw / ih
    deviation = abs(ratio_model - ratio_image) / ratio_image
    if deviation > ASPECT_TOLERANCE:
        z_fix = round(dims[0] / ratio_image, 3)
        message = (f'dimensions_m X:Z={dims[0]}:{dims[2]}({ratio_model:.3f}) 与图幅 {iw}x{ih}({ratio_image:.3f}) 偏差 {deviation*100:.1f}%,'
                   f'整图铺地会被单向拉伸;保持 X 则 Z 应为 {z_fix},或按 Z 反推 X={round(dims[2]*ratio_image,3)}')
        defect_id, invalid = aspect_waiver(base, sid, authored)
        if defect_id:
            waived.append({'item': 'aspect', 'status': 'WAIVED', 'defect_id': defect_id, 'detail': message})
        else:
            problems.append(message + (f';比例豁免无效:{invalid}' if invalid else ''))
    elif isinstance(authored, dict) and authored.get('layout_check_waiver') is not None:
        notes.append('已声明 layout_check_waiver,但比例偏差在容差内,无须豁免')
    if not scene.get('objects'):
        notes.append('objects 为空:仅核比例')
    walls = []
    for obj in scene.get('objects', []):
        x0, y0, x1, y1 = footprint(obj, dims)
        if x0 < -1e-6 or y0 < -1e-6 or x1 > 1 + 1e-6 or y1 > 1 + 1e-6:
            problems.append(f'{obj["id"]} 脚印越出图幅 x[{x0:.3f},{x1:.3f}] y[{y0:.3f},{y1:.3f}]')
        if is_wall(obj):
            walls.append({'id': obj['id'], 'x': [round(x0, 3), round(x1, 3)], 'y': [round(y0, 3), round(y1, 3)],
                          'height_m': obj['size_m'][1], 'elevation_m': round(obj['position'][1] - obj['size_m'][1] / 2, 2)})
    overlay = None
    if write_overlay:
        draw = ImageDraw.Draw(im)
        try:
            font = ImageFont.load_default(size=max(14, iw // 110))
        except (TypeError, AttributeError):  # Pillow < 10.1 has no scalable default font
            font = ImageFont.load_default()
        W, D = dims[0], dims[2]
        for metre in range(1, int(W) + 1):
            x = metre / W * iw
            draw.line([(x, 0), (x, ih)], fill=(0, 190, 255), width=1)
        for metre in range(1, int(D) + 1):
            y = metre / D * ih
            draw.line([(0, y), (iw, y)], fill=(0, 190, 255), width=1)
        for obj in scene.get('objects', []):
            corners = footprint_polygon(obj, dims)
            x0, y0, _, _ = footprint(obj, dims)
            colour = (255, 0, 0) if is_wall(obj) else (30, 90, 255)
            draw.polygon([(x * iw, y * ih) for x, y in corners], outline=colour, width=max(2, iw // 640))
            draw.text((x0 * iw + 4, y0 * ih + 2), obj['id'], fill=colour, font=font)
        for lm in scene.get('landmarks', []):
            x, y = lm['xy'][0] * iw, lm['xy'][1] * ih
            r = max(4, iw // 320)
            draw.ellipse([x - r, y - r, x + r, y + r], fill=(255, 210, 0), outline=(0, 0, 0))
            draw.text((x + r + 2, y - r), lm['id'], fill=(120, 80, 0), font=font)
        draw.text((8, 8), f'{sid} dimensions_m={dims} grid=1m image={iw}x{ih}', fill=(255, 0, 0), font=font)
        overlay = image.parent / 'whitebox_overlay.png'
        im.save(overlay)
    return {'scene_id': sid, 'status': 'FAIL' if problems else 'PASS', 'dimensions_m': dims, 'image': [iw, ih],
            'aspect_deviation_pct': round(deviation * 100, 2), 'problems': problems, 'waived': waived, 'notes': notes, 'walls': walls,
            'overlay': str(overlay.relative_to(base)) if overlay else None}


def main():
    def configure(parser):
        parser.add_argument('--scene', action='append', default=[], help='场景 ID,可重复;缺省=项目内所有已建白模的场景')
        parser.add_argument('--no-overlay', action='store_true', help='只检查不出叠图')
    args, base = parse_args(__doc__, ep=False, configure=configure)
    scenes = [component(s) for s in args.scene]
    if not scenes:
        for path in sorted((base / 'bible/scenes').glob('SCN-*/whitebox.json')):
            if read(path, {}).get('objects') is not None:
                scenes.append(path.parent.name)
    if not scenes:
        raise ValueError('项目内没有已建白模的场景(bible/scenes/*/whitebox.json 含 objects)')
    reports = [check_scene(base, sid, not args.no_overlay) for sid in scenes]
    print(json.dumps({'whitebox_layout_ok': reports}, ensure_ascii=False, indent=1), flush=True)
    failed = [r['scene_id'] for r in reports if r['status'] != 'PASS']
    waived = [f"{r['scene_id']}({w['defect_id']})" for r in reports for w in r.get('waived', [])]
    tail = f",{len(waived)} 场景比例项已按用户签字豁免 {','.join(waived)}" if waived else ''
    print(f"[whitebox_layout_ok] {args.project}: {len(reports)} 场景,{len(failed)} 场景不合格{(' ' + ','.join(failed)) if failed else ''}{tail} -> {'FAIL' if failed else 'PASS'}", flush=True)
    return 1 if failed else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        print(f'whitebox_layout_check: {error}', file=sys.stderr)
        sys.exit(1)
