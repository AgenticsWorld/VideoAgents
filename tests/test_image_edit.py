"""code/image_edit.py:改图按原编码写回;超限的无透明真 PNG 归一成 JPEG 字节(文件名不变);带透明的不转。"""
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
CLI = [sys.executable, str(ROOT / 'code/image_edit.py')]


def _noise(w=1600, h=900, alpha=False):
    arr = np.random.default_rng(3).integers(0, 255, (h, w, 4 if alpha else 3), dtype='uint8')
    return Image.fromarray(arr, 'RGBA' if alpha else 'RGB')


def test_flip_keeps_jpeg_bytes_under_png_name(tmp_path):
    f = tmp_path / 'layout_top.png'
    im = Image.new('RGB', (200, 100), (0, 0, 0)); im.putpixel((0, 0), (255, 255, 255)); im.save(f, 'JPEG', quality=95)
    assert subprocess.run(CLI + [str(f), '--flip-h', '--no-backup']).returncode == 0
    assert f.read_bytes()[:3] == b'\xff\xd8\xff'
    assert Image.open(f).getpixel((199, 0))[0] > 128


def test_normalize_true_png_and_backup(tmp_path):
    f = tmp_path / 'layout_top.png'; _noise().save(f)
    (tmp_path / 'candidates').mkdir()
    assert subprocess.run(CLI + [str(f), '--check']).returncode == 1
    assert subprocess.run(CLI + [str(f), '--normalize']).returncode == 0
    assert f.read_bytes()[:3] == b'\xff\xd8\xff' and Image.open(f).size == (1600, 900)
    assert len(list((tmp_path / 'candidates').glob('layout_top.*.orig.png'))) == 1
    assert subprocess.run(CLI + [str(f), '--check']).returncode == 0


def test_alpha_png_stays_png(tmp_path):
    f = tmp_path / 'cutout.png'; _noise(alpha=True).save(f)
    assert subprocess.run(CLI + [str(f), '--normalize', '--no-backup']).returncode == 0
    assert f.read_bytes()[:8] == b'\x89PNG\r\n\x1a\n'
