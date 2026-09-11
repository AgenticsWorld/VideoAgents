"""③ 对齐:全景 → 白模坐标(事后反算观察点 P 与中央列朝向 yaw0)。

  locate: 8 窗 → 视觉模型(火山 doubao vision)逐窗给地标框 → <workdir>/observations.json
  solve : 硬地标 2D 后方交会 + 触地距离约束(3 轮重关联压幻觉框)→ <workdir>/pano.json + annot.jpg

用法:
  python code/pano/align.py locate --project offer --scene SCN-0004 --pano <pano.png> --workdir <dir> [--landmarks <override.json>]
  python code/pano/align.py solve  --project offer --scene SCN-0004 --pano <pano.png> --workdir <dir> \
      --intent-xz 0 0.5 --intent-yaw0-deg 180 [--eye-h 1.6] [--landmarks <override.json>]

地标坐标默认由 layout.json 归一化 xy 按白模 dimensions_m 映射;override JSON 形如
  {"MAIN_GATE": {"xz": [0, -3.9]}, "PLAQUE": {"xz": [0, -3.84], "ground": false}, "CART_SPOT": {"soft": true}}
硬地标(建筑固定物)参与解算,软地标(可移动物/模糊点)只报告偏差。通过标准:rms ≤ 5° 且 |P−intent| ≤ 2 m。
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import urllib.request
from pathlib import Path

import cv2
import numpy as np
from scipy.optimize import least_squares

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import ark_key, load_landmarks_m, project_root  # noqa: E402
from panolib import dir_to_uv, window_pixel_to_dir  # noqa: E402

ARK_CHAT = 'https://ark.cn-beijing.volces.com/api/v3/chat/completions'


def vlm(model: str, img_path: Path, prompt: str) -> str:
    b64 = base64.b64encode(img_path.read_bytes()).decode()
    body = {'model': model, 'temperature': 0.1, 'max_tokens': 1500,
            'messages': [{'role': 'user', 'content': [
                {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + b64}},
                {'type': 'text', 'text': prompt}]}]}
    req = urllib.request.Request(ARK_CHAT, data=json.dumps(body).encode(),
                                 headers={'Authorization': 'Bearer ' + ark_key(), 'Content-Type': 'application/json'})
    return json.load(urllib.request.urlopen(req, timeout=120))['choices'][0]['message']['content']


def locate(a, LM: dict) -> None:
    workdir = Path(a.workdir)
    prompt = ('This is a 90-degree perspective view (1024x1024) cut from a 360 panorama of a location. '
              'For EACH of the following landmarks, decide whether it is clearly visible in THIS image. If visible, give a tight bounding box '
              'in normalized coordinates [x1,y1,x2,y2] (0..1, origin top-left) and a confidence 0..1. Only report a landmark if it is clearly '
              'and unambiguously present; do not guess. Landmarks:\n' + '\n'.join(f'- {k}: {v["desc"]}' for k, v in LM.items()) +
              '\nAnswer with JSON only: {"found": [{"id": "...", "bbox": [x1,y1,x2,y2], "confidence": 0.0}]}')
    obs = []
    for k in range(8):
        yaw = np.radians(45 * k)
        img = workdir / f'win_{k}_yaw{45 * k:03d}.jpg'
        if not img.exists():
            raise SystemExit(f'缺少透视窗 {img},先运行 make_windows.py')
        t0 = time.time()
        try:
            txt = vlm(a.vlm, img, prompt)
        except Exception as e:  # noqa: BLE001
            print('win', k, 'ERR', str(e)[:200])
            continue
        s = txt[txt.find('{'): txt.rfind('}') + 1]
        try:
            found = json.loads(s).get('found', [])
        except Exception:  # noqa: BLE001
            print('win', k, 'bad json:', txt[:200])
            found = []
        print(f'win {k} yaw {45 * k:3d}: ' + ', '.join(f"{f['id']}({f.get('confidence', 0):.2f})" for f in found), f'{time.time() - t0:.1f}s')
        for f in found:
            if f.get('id') not in LM:
                continue
            x1, y1, x2, y2 = f['bbox']
            cx, cy = (x1 + x2) / 2 * 1024, (y1 + y2) / 2 * 1024
            u_c, v_c = dir_to_uv(window_pixel_to_dir(cx, cy, 1024, 1024, yaw, 0.0, 90.0))
            u_g, v_g = dir_to_uv(window_pixel_to_dir(cx, y2 * 1024, 1024, 1024, yaw, 0.0, 90.0))
            obs.append({'id': f['id'], 'win': k, 'bbox': f['bbox'], 'confidence': f.get('confidence', 0),
                        'u_center': float(u_c), 'v_center': float(v_c), 'u_ground': float(u_g), 'v_ground': float(v_g)})
    json.dump(obs, open(workdir / 'observations.json', 'w'), indent=1)
    print('observations:', len(obs))


def wrap(x):
    return (x + np.pi) % (2 * np.pi) - np.pi


def _pack(LM, used, eye_h):
    ids = [o['id'] for o in used]
    L = np.array([LM[i]['xz'] for i in ids])
    az_obs = np.array([-(o['u_center'] - 0.5) * 2 * np.pi for o in used])   # 世界方位 = az_obs + yaw0
    grounded = np.array([LM[i].get('ground', True) for i in ids])
    el_g = np.array([(0.5 - o['v_ground']) * np.pi for o in used])

    def resid(p):
        px, pz, yaw0 = p
        pred = np.arctan2(L[:, 0] - px, L[:, 1] - pz)
        r_b = wrap(az_obs + yaw0 - pred)
        d_pred = np.hypot(L[:, 0] - px, L[:, 1] - pz)
        with np.errstate(divide='ignore', invalid='ignore'):
            d_obs = np.where(el_g < -0.01, eye_h / np.tan(-el_g), np.nan)
        r_d = np.where(grounded & np.isfinite(d_obs), 0.3 * (np.log(np.maximum(d_obs, 0.3)) - np.log(np.maximum(d_pred, 0.3))), 0.0)
        return np.concatenate([r_b, r_d])
    return ids, L, el_g, resid


def solve(a, LM: dict) -> None:
    workdir = Path(a.workdir)
    obs = json.load(open(workdir / 'observations.json'))
    eye_h = a.eye_h
    intent = tuple(a.intent_xz)
    yaw0_intent = np.radians(a.intent_yaw0_deg)
    best = {}
    for o in obs:
        x1, _, x2, _ = o['bbox']
        score = o['confidence'] - (0.3 if min(x1, 1 - x2) < 0.02 else 0)
        if o['id'] not in best or score > best[o['id']][0]:
            best[o['id']] = (score, o)
    used = [o for s, o in best.values() if s >= 0.5 and not LM[o['id']].get('soft')]
    if len(used) < 3:
        raise SystemExit(f'硬地标观测不足 3 个({len(used)}),无法交会')
    x0 = [intent[0], intent[1], yaw0_intent]
    for _ in range(3):
        ids, L, el_g, resid = _pack(LM, used, eye_h)
        sol = least_squares(resid, x0=x0, loss='soft_l1', f_scale=0.1)
        px, pz, yaw0 = sol.x
        x0 = list(sol.x)
        re_used = {}
        for o in obs:
            if LM[o['id']].get('soft'):
                continue
            L0 = LM[o['id']]['xz']
            err = abs(wrap(-(o['u_center'] - 0.5) * 2 * np.pi + yaw0 - np.arctan2(L0[0] - px, L0[1] - pz)))
            if o['id'] not in re_used or err < re_used[o['id']][0]:
                re_used[o['id']] = (err, o)
        used = [o for _, o in re_used.values()]
    ids, L, el_g, resid = _pack(LM, used, eye_h)
    rb = np.degrees(resid(sol.x)[:len(ids)])
    rms = float(np.sqrt(np.mean(rb ** 2)))
    dist = float(np.hypot(px - intent[0], pz - intent[1]))
    soft = []
    for o in obs:
        if not LM[o['id']].get('soft'):
            continue
        L0 = LM[o['id']]['xz']
        err = float(np.degrees(wrap(-(o['u_center'] - 0.5) * 2 * np.pi + yaw0 - np.arctan2(L0[0] - px, L0[1] - pz))))
        el = (0.5 - o['v_ground']) * np.pi
        soft.append({'id': o['id'], 'win': o['win'], 'confidence': o['confidence'], 'bearing_residual_deg': round(err, 1),
                     'd_pred_m': round(float(np.hypot(L0[0] - px, L0[1] - pz)), 2),
                     'd_obs_m': round(float(eye_h / np.tan(-el)), 2) if el < -0.01 else None})
    out = {'schema': 'scene_panorama.v1', 'scene_id': a.scene, 'pano': str(Path(a.pano)),
           'origin_m': [round(float(px), 3), eye_h, round(float(pz), 3)],
           'yaw0_rad': round(float(wrap(yaw0)), 4), 'yaw0_deg': round(float(np.degrees(wrap(yaw0))), 1),
           'projection': 'u=((yaw0-az)/2pi+0.5) mod 1, v=0.5-el/pi, az=atan2(dx,dz), el=asin(dy)',
           'intent': {'origin_m': [intent[0], eye_h, intent[1]], 'yaw0_rad': float(yaw0_intent)},
           'alignment': {'method': 'resection2d+ground_distance', 'n_landmarks': len(ids), 'rms_deg': round(rms, 2),
                         'dist_to_intent_m': round(dist, 2),
                         'landmarks': [{'id': i, 'win': used[j]['win'], 'bearing_residual_deg': round(float(rb[j]), 2),
                                        'confidence': used[j]['confidence'],
                                        'd_pred_m': round(float(np.hypot(L[j, 0] - px, L[j, 1] - pz)), 2),
                                        'd_obs_m': round(float(eye_h / np.tan(-el_g[j])), 2) if el_g[j] < -0.01 else None}
                                       for j, i in enumerate(ids)],
                         'soft_landmarks': soft,
                         'status': 'passed' if (rms <= 5 and dist <= 2) else 'failed'}}
    json.dump(out, open(workdir / 'pano.json', 'w'), indent=1, ensure_ascii=False)
    print(json.dumps({k: v for k, v in out['alignment'].items() if k != 'soft_landmarks'}, indent=1, ensure_ascii=False))
    print('origin', out['origin_m'], 'yaw0_deg', out['yaw0_deg'], out['alignment']['status'])
    # 核对图:按解出的位姿把地标与 N/E/S/W 投到全景;绿=参与解算,橙=软地标,红圈=视觉模型观测
    pano = cv2.imread(a.pano)
    small = cv2.resize(pano, (2048, 1024))
    for n, (i, lm) in enumerate(LM.items()):
        d = np.array([lm['xz'][0] - px, 0.0, lm['xz'][1] - pz])
        d /= np.linalg.norm(d)
        u, _ = dir_to_uv(d, yaw0)
        x = int(u * 2048)
        col = (0, 255, 0) if i in ids else ((0, 165, 255) if lm.get('soft') else (0, 0, 255))
        cv2.line(small, (x, 0), (x, 1023), col, 1)
        cv2.putText(small, i, (x + 3, 40 + 30 * (n % 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)
    for name, az in [('N(-Z)', np.pi), ('E(+X)', np.pi / 2), ('S(+Z)', 0.0), ('W(-X)', -np.pi / 2)]:
        x = int((((yaw0 - az) / (2 * np.pi) + 0.5) % 1) * 2048)
        cv2.line(small, (x, 900), (x, 1023), (255, 255, 0), 2)
        cv2.putText(small, name, (x + 3, 990), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 0), 2)
    for o in used:
        cv2.circle(small, (int(o['u_center'] * 2048), int(o['v_center'] * 1024)), 10, (0, 0, 255), 2)
    cv2.imwrite(str(workdir / 'annot.jpg'), small, [cv2.IMWRITE_JPEG_QUALITY, 85])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cmd', choices=['locate', 'solve'])
    ap.add_argument('--project', required=True)
    ap.add_argument('--scene', required=True)
    ap.add_argument('--pano', required=True)
    ap.add_argument('--workdir', required=True)
    ap.add_argument('--landmarks', help='地标坐标 override JSON')
    ap.add_argument('--vlm', default='doubao-seed-2-0-pro-260215')
    ap.add_argument('--eye-h', type=float, default=1.6)
    ap.add_argument('--intent-xz', type=float, nargs=2, default=[0.0, 0.0], help='生成时声明的站位(白模米制 x z)')
    ap.add_argument('--intent-yaw0-deg', type=float, default=180.0, help='中央列的设定朝向(白模 yaw,度;180=朝北 −Z,−90=朝西)')
    a = ap.parse_args()
    LM = load_landmarks_m(project_root(a.project), a.scene, Path(a.landmarks) if a.landmarks else None)
    (locate if a.cmd == 'locate' else solve)(a, LM)


if __name__ == '__main__':
    main()
