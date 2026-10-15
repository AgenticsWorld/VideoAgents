"""#132:布局包俯视图像素闸门按文件头读尺寸(不依赖 Pillow),读不到尺寸按违规「像素闸门未执行」。"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SID = 'SCN-0001'


def _mod():
    sys.path.insert(0, str(ROOT / 'code'))
    spec = importlib.util.spec_from_file_location('bmc', ROOT / 'code' / 'blocking_map_check.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _jpeg(width, height):
    """只含 SOI + APP0 + SOF0 段头的 JPEG 载荷(足够让文件头解析读出宽高)。"""
    app0 = b'\xff\xe0' + (16).to_bytes(2, 'big') + b'JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00'
    sof0 = (b'\xff\xc0' + (17).to_bytes(2, 'big') + b'\x08' + height.to_bytes(2, 'big')
            + width.to_bytes(2, 'big') + b'\x03' + b'\x01\x22\x00\x02\x11\x01\x03\x11\x01')
    return b'\xff\xd8' + app0 + sof0 + b'\xff\xd9'


@pytest.fixture
def pack(tmp_path):
    d = tmp_path / 'assets/concepts/scenes' / SID
    d.mkdir(parents=True)
    (d / 'layout.json').write_text(json.dumps({
        'schema_version': 'scene_layout.v1',
        'landmarks': [{'id': f'L{i}', 'xy': [0.2 * i, 0.5], 'name_en': f'mark {i}'} for i in (1, 2, 3)],
        'views': [{'tile': t, 'desc_en': f'view {t}'} for t in range(1, 10)],
    }), encoding='utf-8')
    return tmp_path, d / 'layout_top.png'


def test_png_name_with_jpeg_payload_checked_without_pillow(pack, monkeypatch):
    root, top = pack
    monkeypatch.setitem(sys.modules, 'PIL', None)   # 模拟缺 Pillow 的解释器
    mod = _mod()
    top.write_bytes(_jpeg(2560, 1440))
    _, errs, warns = mod.load_layout(root, SID)
    assert errs == [] and warns == []
    top.write_bytes(_jpeg(1280, 720))
    _, errs, _ = mod.load_layout(root, SID)
    assert any('1280x720' in e and '像素' in e for e in errs)


def test_unreadable_size_is_violation(pack):
    root, top = pack
    top.write_bytes(b'RIFF\x00\x00\x00\x00WEBPVP8 ' + b'\x00' * 32)
    _, errs, warns = _mod().load_layout(root, SID)
    assert any('像素闸门未执行' in e for e in errs)
    assert not any('无法读取' in w for w in warns)
