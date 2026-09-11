"""② 把全景切成 8 个 90° 透视窗(yaw 0,45,…,315°;pitch 0;1024²),供视觉模型找地标;顺带出 8 窗缩略表。

用法: python code/pano/make_windows.py <pano.png> <workdir>
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from panolib import perspective_from_equirect  # noqa: E402


def main() -> None:
    src, outdir = Path(sys.argv[1]), Path(sys.argv[2])
    outdir.mkdir(parents=True, exist_ok=True)
    pano = cv2.imread(str(src), cv2.IMREAD_COLOR)
    if pano is None:
        raise SystemExit(f'读不到全景:{src}')
    thumbs = []
    for k in range(8):
        img = perspective_from_equirect(pano, 1024, 1024, np.radians(45 * k), 0.0, 90.0)   # 全景像素 yaw0=0
        cv2.imwrite(str(outdir / f'win_{k}_yaw{45 * k:03d}.jpg'), img, [cv2.IMWRITE_JPEG_QUALITY, 90])
        t = cv2.resize(img, (400, 400))
        cv2.putText(t, f'win{k} yaw{45 * k}', (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        thumbs.append(t)
    cv2.imwrite(str(outdir / 'windows_sheet.jpg'), np.vstack([np.hstack(thumbs[:4]), np.hstack(thumbs[4:])]), [cv2.IMWRITE_JPEG_QUALITY, 80])
    print('ok', outdir)


if __name__ == '__main__':
    main()
