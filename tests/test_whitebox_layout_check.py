"""#78:whitebox_layout_check 脚印按 yaw 旋转(three.js rotation.y 约定),越界用旋转后四角,叠图画旋转多边形。"""
import importlib.util
import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _mod():
    sys.path.insert(0, str(ROOT / 'code'))
    spec = importlib.util.spec_from_file_location('wlc', ROOT / 'code' / 'whitebox_layout_check.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding='utf-8')


def test_footprint_rotation_matches_threejs_convention():
    wlc = _mod()
    dims = [10, 3, 10]
    obj = {'size_m': [4, 1, 1], 'position': [0, .5, 0], 'yaw': math.pi / 2}
    x0, y0, x1, y1 = wlc.footprint(obj, dims)
    # 长边 4 m 沿 X 的条块转 90° 后沿 Z:归一化 x 宽 0.1、y 高 0.4
    assert x1 - x0 == pytest.approx(.1) and y1 - y0 == pytest.approx(.4)
    # 局部 +X 端(sx/2, 0)→ 世界 (cos yaw, −sin yaw)·2 = (0, −2) → 图上 y 更小
    yaw = .7
    obj = {'size_m': [2, 1, 0.0001], 'position': [0, .5, 0], 'yaw': yaw}
    corners = wlc.footprint_polygon(obj, dims)
    tip = corners[1]   # (sx/2, -sz/2)
    assert tip[0] == pytest.approx(.5 + math.cos(yaw) / 10, abs=1e-4)
    assert tip[1] == pytest.approx(.5 - math.sin(yaw) / 10, abs=1e-4)
    # yaw=0 与旧轴对齐结果一致
    assert wlc.footprint({'size_m': [2, 1, 1], 'position': [1, .5, 2]}, dims) == pytest.approx((.5, .65, .7, .75))


@pytest.fixture
def scene_project(tmp_path):
    from PIL import Image
    base = tmp_path / 'demo'; sid = 'SCN-1'
    write(base / 'assets/concepts/scenes' / sid / 'layout.json', {'scene_name': 'Room', 'landmarks': []})
    Image.new('RGB', (200, 200), 'white').save(base / 'assets/concepts/scenes' / sid / 'layout_top.png')
    return base, sid


def _scene(base, sid, objects):
    write(base / 'bible/scenes' / sid / 'whitebox.json', {'dimensions_m': [10, 3, 10], 'inferred': False, 'objects': objects})


def test_rotated_wall_out_of_bounds_detected(scene_project):
    """贴边 9.8 m 横墙轴对齐不越界,转 90° 后竖向 9.8 m 贴在 x 边缘:按四角 min/max 判,旋转后越界。"""
    wlc = _mod()
    base, sid = scene_project
    _scene(base, sid, [{'id': 'wall_n', 'size_m': [4, 2, .2], 'xy': [.5, .005], 'collision_role': 'wall'}])
    assert wlc.check_scene(base, sid, write_overlay=False)['status'] == 'FAIL'   # 轴对齐:z 半厚 0.1 越上边
    _scene(base, sid, [{'id': 'wall_e', 'size_m': [4, 2, .2], 'xy': [.99, .5], 'yaw': math.pi / 2, 'collision_role': 'wall'}])
    report = wlc.check_scene(base, sid, write_overlay=False)
    # 旋转后 x 方向只占 0.2 m:0.99±0.01 恰在图内;若忽略 yaw 按 4 m 宽算会误报越界
    assert report['status'] == 'PASS', report['problems']
    wall = report['walls'][0]
    assert wall['x'] == pytest.approx([.98, 1.0], abs=1e-3) and wall['y'] == pytest.approx([.3, .7], abs=1e-3)
    _scene(base, sid, [{'id': 'wall_s', 'size_m': [4, 2, .2], 'xy': [.5, .85], 'yaw': math.pi / 2}])
    report = wlc.check_scene(base, sid, write_overlay=False)
    # 忽略 yaw 时 z 半厚 0.1 m 不越界;旋转后 z 半长 2 m → 0.85+0.2>1 须越界
    assert report['status'] == 'FAIL' and '脚印越出图幅' in report['problems'][0]


def test_overlay_draws_rotated_polygon(scene_project):
    from PIL import Image
    wlc = _mod()
    base, sid = scene_project
    _scene(base, sid, [{'id': 'bar', 'size_m': [6, 1, .4], 'xy': [.5, .5], 'yaw': math.pi / 2, 'collision_role': 'wall'}])
    report = wlc.check_scene(base, sid)
    im = Image.open(base / report['overlay']).convert('RGB')
    red = lambda p: im.getpixel(p)[0] > 200 and im.getpixel(p)[1] < 80
    # 旋转后竖条:左右边 x≈96/104,纵向 y 40..160;不旋转的话横条上下边在 y≈96/104、横跨 x 40..160
    assert any(red((x, 120)) for x in (95, 96, 97))
    assert any(red((100, y)) for y in (39, 40, 41))
    assert not any(red((60, y)) for y in range(94, 107)) and not any(red((140, y)) for y in range(94, 107))
