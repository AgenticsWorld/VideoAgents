"""④ 机位截图:按白模计划 camera 从全景出每镜首帧的场景图(crop / projected 两种模式),并与白模 camera.mp4 同帧并排。

  crop      :纯旋转重采样,只用 yaw/pitch/fov;机位离全景观察点很近且主体远时成立。
  projected :全景以观察点 P 为中心投射到场景白模几何(box/cylinder 当 AABB+yaw,地面 y=0),再从机位 C 渲染;
             未命中几何按 C 的射线方向直接采样(远景),P 看不见的命中点标灰并出 mask。默认模式。

用法:
  python code/pano/shots.py --project offer --ep ep01 --gid grp005 --pano <pano.png> --meta <pano.json> --outdir <dir> [--mode projected|crop|both]
输出:<outdir>/<sid>_projected.png / _crop.png / _mask.png / _whitebox.png / <sid>_compare.jpg / manifest.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import project_root  # noqa: E402
from panolib import camera_yaw_pitch, dir_to_uv, dirs_for_perspective, perspective_from_equirect, sample_equirect  # noqa: E402


def load_boxes(project_dir: Path, scene_id: str, plan: dict | None = None):
    """场景白模物体 → [(id, center, half_size, yaw)];计划里同名 props 覆盖场景物体(与渲染器约定一致)。"""
    wb = json.load(open(project_dir / 'bible/scenes' / scene_id / 'whitebox.json'))
    W, _, D = wb['dimensions_m']
    objs = {}
    for o in wb['objects']:
        if 'position' in o:
            c = list(o['position'])
        else:
            u, v = o['xy']
            h = o['size_m'][1]
            c = [(u - .5) * W, o.get('elevation_m', h / 2), (v - .5) * D]
        objs[o['id']] = (o['id'], np.array(c, float), np.array(o['size_m'], float) / 2, float(o.get('yaw') or 0.0))
    for p in (plan or {}).get('props', []):
        if p.get('id') in objs and 'position' in p:
            objs[p['id']] = (p['id'], np.array(p['position'], float), np.array(p['size_m'], float) / 2, float(p.get('yaw') or 0.0))
    return list(objs.values())


def ray_boxes(orig, dirs, boxes):
    N = dirs.shape[0]
    tmin = np.full(N, np.inf)
    hid = np.full(N, -1)
    for bi, (_, c, s, yaw) in enumerate(boxes):
        cy, sy = np.cos(-yaw), np.sin(-yaw)
        o = orig - c
        ol = np.array([o[0] * cy + o[2] * sy, o[1], -o[0] * sy + o[2] * cy])
        dl = np.stack([dirs[:, 0] * cy + dirs[:, 2] * sy, dirs[:, 1], -dirs[:, 0] * sy + dirs[:, 2] * cy], 1)
        with np.errstate(divide='ignore', invalid='ignore'):
            t1 = (-s - ol) / dl
            t2 = (s - ol) / dl
        tn = np.nanmax(np.minimum(t1, t2), axis=1)
        tf = np.nanmin(np.maximum(t1, t2), axis=1)
        hit = (tf >= np.maximum(tn, 1e-4)) & (tn > 1e-4)
        t = np.where(hit, tn, np.inf)
        better = t < tmin
        tmin[better] = t[better]
        hid[better] = bi
    with np.errstate(divide='ignore', invalid='ignore'):
        tg = -orig[1] / dirs[:, 1]
    tg = np.where((dirs[:, 1] < -1e-6) & (tg > 1e-4), tg, np.inf)
    better = tg < tmin
    tmin[better] = tg[better]
    hid[better] = 999
    return tmin, hid


def render_projected(pano, meta, position, target, vfov, w, h, boxes):
    P = np.array(meta['origin_m'], float)
    yaw0 = meta['yaw0_rad']
    C = np.array(position, float)
    yaw, pitch = camera_yaw_pitch(position, target)
    d = dirs_for_perspective(w, h, yaw, pitch, vfov).reshape(-1, 3)
    t, hid = ray_boxes(C, d, boxes)
    hitmask = np.isfinite(t)
    Wp = C + d * np.where(hitmask, t, 1.0)[:, None]
    v = Wp - P
    dist = np.linalg.norm(v, axis=1)
    v = v / np.maximum(dist, 1e-6)[:, None]
    samp = np.where(hitmask[:, None], v, d)
    tv, _ = ray_boxes(P, v, boxes)
    occluded = hitmask & (tv < dist - 0.05)
    u, vv = dir_to_uv(samp, yaw0)
    img = sample_equirect(pano, u.reshape(h, w), vv.reshape(h, w))
    mask = (occluded.reshape(h, w) * 255).astype(np.uint8)
    img_m = img.copy()
    occ = occluded.reshape(h, w)
    img_m[occ] = (img_m[occ] * 0.4 + np.array([128, 128, 128]) * 0.6).astype(np.uint8)
    return img, img_m, mask, hid.reshape(h, w)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--project', required=True)
    ap.add_argument('--ep', required=True)
    ap.add_argument('--gid', required=True)
    ap.add_argument('--pano', required=True)
    ap.add_argument('--meta', required=True, help='align.py solve 产出的 pano.json')
    ap.add_argument('--outdir', required=True)
    ap.add_argument('--mode', choices=['projected', 'crop', 'both'], default='projected')
    ap.add_argument('--w', type=int, default=1280)
    ap.add_argument('--h', type=int, default=720)
    a = ap.parse_args()
    proj = project_root(a.project)
    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    pano = cv2.imread(a.pano)
    meta = json.load(open(a.meta))
    plan = json.load(open(proj / 'directing' / a.ep / 'whitebox_plans' / f'{a.gid}.json'))
    boxes = load_boxes(proj, plan['scene_id'], plan)
    wb_mp4 = proj / 'assets/whitebox' / a.ep / a.gid / 'camera.mp4'
    P = np.array(meta['origin_m'])
    manifest = []
    rows = []
    for cam in plan['cameras']:
        k = cam['keyframes'][0]
        t_group = cam['start'] + k['t']
        sid = cam['shot_id'].split('-')[-1]
        yaw, pitch = camera_yaw_pitch(k['position'], k['target'])
        rec = {'shot_id': cam['shot_id'], 't_group': t_group, 'yaw_deg': round(np.degrees(yaw), 1), 'pitch_deg': round(np.degrees(pitch), 1),
               'vfov_deg': k['fov'], 'camera_position': k['position'], 'dist_to_pano_m': round(float(np.linalg.norm(np.array(k['position']) - P)), 2)}
        panels = []
        if a.mode in ('crop', 'both'):
            crop = perspective_from_equirect(pano, a.w, a.h, yaw, pitch, k['fov'], meta['yaw0_rad'])
            cv2.imwrite(str(out / f'{sid}_crop.png'), crop)
            rec['crop'] = f'{sid}_crop.png'
            panels.append(crop)
        if a.mode in ('projected', 'both'):
            img, img_m, mask, _ = render_projected(pano, meta, k['position'], k['target'], k['fov'], a.w, a.h, boxes)
            cv2.imwrite(str(out / f'{sid}_projected.png'), img)
            cv2.imwrite(str(out / f'{sid}_mask.png'), mask)
            rec.update(projected=f'{sid}_projected.png', occluded_pct=round(float(mask.mean() / 255 * 100), 1))
            panels.append(img_m)
        if wb_mp4.exists():
            wb_p = out / f'{sid}_whitebox.png'
            subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-ss', f'{t_group + 0.02:.3f}', '-i', str(wb_mp4), '-frames:v', '1',
                            '-s', f'{a.w}x{a.h}', str(wb_p)], check=True)
            panels.append(cv2.imread(str(wb_p)))
            rec['whitebox'] = wb_p.name
        row = np.hstack(panels)
        cv2.putText(row, f"{cam['shot_id']} t={t_group:.1f}s yaw={rec['yaw_deg']} pitch={rec['pitch_deg']} vfov={k['fov']} |C-P|={rec['dist_to_pano_m']}m",
                    (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        cv2.imwrite(str(out / f'{sid}_compare.jpg'), row, [cv2.IMWRITE_JPEG_QUALITY, 88])
        rows.append(row)
        manifest.append(rec)
        print(rec['shot_id'], f"|C-P|={rec['dist_to_pano_m']}m", f"occluded={rec.get('occluded_pct')}%")
    cv2.imwrite(str(out / 'all_compare.jpg'), np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 85])
    json.dump({'pano': a.pano, 'meta': a.meta, 'mode': a.mode, 'shots': manifest}, open(out / 'manifest.json', 'w'), indent=1)


if __name__ == '__main__':
    main()
