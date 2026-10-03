"""参考图内联上限:超大 PNG 在内存里转 JPEG 后发送,原图不动;MIME 按文件头判。"""
import base64
import io
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'modules'))
from modules import genmedia as g  # noqa: E402


def test_oversized_png_ref_is_reencoded_in_memory(tmp_path):
    big = tmp_path / 'layout_top.png'
    Image.fromarray(np.random.default_rng(0).integers(0, 255, (1440, 2560, 3), dtype='uint8')).save(big)   # 噪声图:真 PNG ≈ 11 MB
    before = big.read_bytes()
    assert len(before) > g.REF_INLINE_MAX_BYTES
    head, b64 = g._file_to_data_url(str(big), g.REF_INLINE_MAX_BYTES).split(',', 1)
    raw = base64.b64decode(b64)
    assert head == 'data:image/jpeg;base64' and len(raw) <= g.REF_INLINE_MAX_BYTES
    assert Image.open(io.BytesIO(raw)).size[0] / Image.open(io.BytesIO(raw)).size[1] == 2560 / 1440
    assert big.read_bytes() == before                                                                     # 原图未改
    assert g._file_to_data_url(str(big)).startswith('data:image/png;base64,')                             # 不传上限:原样


def test_mime_follows_magic_bytes_not_extension(tmp_path):
    f = tmp_path / 'concept.png'
    Image.new('RGB', (64, 64), (10, 20, 30)).save(f, 'JPEG')                                             # JPEG 字节配 .png 名
    assert g._file_to_data_url(str(f)).startswith('data:image/jpeg;base64,')
