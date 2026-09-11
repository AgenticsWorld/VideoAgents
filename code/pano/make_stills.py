"""⑤ 场景图精修:把机位投影图(projected)交给 Seedream 做单参考图生图,补细节、修拉花,构图不变;
   可选:抠掉指定白模物体(inpaint)、给某镜追加参考图(如角色 sheet,让物件按 sheet 出)。

用法:
  python code/pano/make_stills.py --project offer --ep ep01 --gid grp005 --shots-dir <shots.py 的 outdir> --desc <desc.json> \
      [--out assets/panorama_shots/ep01/grp005] [--only ep01-sh010] [--size 2560x1440]

desc.json 每镜一条,键为 shot_id:
  {"ep01-sh010": {"desc": "Low-angle close shot ...", "negative": ", grand staircase", "extra_refs": ["assets/concepts/characters/CHAR-0005/sheet.png"],
                  "inpaint_objects": ["obj11"]}}
  desc          必填,该镜的场景文字(无人)
  negative      追加负面词(逗号开头)
  extra_refs    追加参考图(项目相对路径),[Image 2] 起
  inpaint_objects 先把这些白模物体在投影图里的区域 inpaint 掉再精修(用于不想让全景里的物件造型进成片)
  daylight      默认 true:精修提示词强制日光晴天(全景可能被风格串带成阴天)
产出:<out>/<sid>_still.png(sid 如 sh010),并在 shots-dir 写 stills_sheet.jpg 对照表。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import ROOT, ledger_write, project_root  # noqa: E402
from panolib import camera_yaw_pitch, dirs_for_perspective  # noqa: E402
from shots import load_boxes, ray_boxes  # noqa: E402

sys.path.insert(0, str(ROOT))
from modules.genmedia import generate_image  # noqa: E402

REFINE = ('Re-render [Image 1] as a clean, sharp, fully detailed photoreal still with exactly the same framing: the camera does not move, '
          'does not zoom in or out; keep the position and size of every wall, gate, step, object and the ground plane; only add surface detail '
          'and fix smearing, stretching or blur. ')
NEG_BASE = ('people, human figures, silhouettes of people, human hands, faces, text, labels, watermark, collage, multiple views, panorama, '
            'equirectangular, zoomed out, zoomed in, fisheye distortion, lowres, blurry')


def style_strings(project_dir: Path) -> tuple[str, str]:
    """从 bible/style.json 取风格串与日光 register(缺则空串)。"""
    try:
        st = json.load(open(project_dir / 'bible/style.json'))['style_prompt_en']
        base = st.get('base', '')
        for drop in ('dark low-key environment, ', '16:9 cinematic framing, '):
            base = base.replace(drop, '')
        return base, st.get('register_day', '')
    except Exception:  # noqa: BLE001
        return '', ''


def inpaint_objects(proj_img: np.ndarray, plan: dict, cam_k: dict, boxes, obj_ids: list[str]) -> np.ndarray:
    h, w = proj_img.shape[:2]
    yaw, pitch = camera_yaw_pitch(cam_k['position'], cam_k['target'])
    d = dirs_for_perspective(w, h, yaw, pitch, cam_k['fov']).reshape(-1, 3)
    _, hid = ray_boxes(np.array(cam_k['position'], float), d, boxes)
    idx = {b[0]: i for i, b in enumerate(boxes)}
    mask = np.zeros(h * w, np.uint8)
    for oid in obj_ids:
        if oid in idx:
            mask[hid == idx[oid]] = 255
    mask = cv2.dilate(mask.reshape(h, w), np.ones((25, 25), np.uint8))
    return cv2.inpaint(proj_img, mask, 7, cv2.INPAINT_TELEA)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--project', required=True)
    ap.add_argument('--ep', required=True)
    ap.add_argument('--gid', required=True)
    ap.add_argument('--shots-dir', required=True)
    ap.add_argument('--desc', required=True)
    ap.add_argument('--out', help='精修图输出目录(项目相对);缺省 assets/panorama_shots/<ep>/<gid>')
    ap.add_argument('--only', action='append', default=[], help='只做这些 shot_id')
    ap.add_argument('--size', default='2560x1440', help='Seedream 5.0 lite 最小 3686400 px')
    a = ap.parse_args()
    proj = project_root(a.project)
    shots_dir = Path(a.shots_dir)
    out = proj / (a.out or f'assets/panorama_shots/{a.ep}/{a.gid}')
    out.mkdir(parents=True, exist_ok=True)
    desc = json.load(open(a.desc))
    plan = json.load(open(proj / 'directing' / a.ep / 'whitebox_plans' / f'{a.gid}.json'))
    boxes = load_boxes(proj, plan['scene_id'], plan)
    base, reg_day = style_strings(proj)
    rows = []
    for cam in plan['cameras']:
        full = cam['shot_id']
        sid = full.split('-')[-1]
        if a.only and full not in a.only:
            continue
        if full not in desc:
            print('skip(no desc):', full)
            continue
        cfg = desc[full]
        proj_p = shots_dir / f'{sid}_projected.png'
        if not proj_p.exists():
            raise SystemExit(f'缺少投影图 {proj_p},先运行 shots.py')
        ref_img = proj_p
        if cfg.get('inpaint_objects'):
            img = inpaint_objects(cv2.imread(str(proj_p)), plan, cam['keyframes'][0], boxes, cfg['inpaint_objects'])
            ref_img = shots_dir / f'{sid}_projected_inpainted.png'
            cv2.imwrite(str(ref_img), img)
        refs = [str(ref_img)] + [str(proj / r) for r in cfg.get('extra_refs', [])]
        light = 'Clear bright autumn daylight. ' if cfg.get('daylight', True) else ''
        prompt = f"Image-to-image refinement. {cfg['desc']} {REFINE}{light}{base}, {reg_day}. Absolutely no people, no text."
        neg = NEG_BASE + (', storm clouds, overcast, night, dusk' if cfg.get('daylight', True) else '') + cfg.get('negative', '')
        still = out / f'{sid}_still.png'
        t0 = time.time()
        generate_image(prompt, str(still), negative=neg, refs=refs, size=a.size)
        ledger_write(out / 'usage_ledger.jsonl', {'kind': 'still', 'shot': full, 'refs': refs, 'output': str(still.relative_to(proj)),
                                                  'size': a.size, 'secs': round(time.time() - t0, 1), 'prompt': prompt})
        print('refined', full, round(time.time() - t0, 1), 's')
        row = np.hstack([cv2.resize(cv2.imread(str(ref_img)), (640, 360)), cv2.resize(cv2.imread(str(still)), (640, 360))])
        cv2.putText(row, f'{full} projected | still', (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        rows.append(row)
    if rows:
        cv2.imwrite(str(shots_dir / 'stills_sheet.jpg'), np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 85])
        print('sheet:', shots_dir / 'stills_sheet.jpg')


if __name__ == '__main__':
    main()
