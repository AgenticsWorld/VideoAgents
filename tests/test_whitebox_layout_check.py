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


# ---------------- #154:比例项凭用户签字的缺陷单豁免 ----------------

DEFECT = {'defect_id': 'DEF-p6-env-1-001', 'scene_id': 'SCN-1', 'severity': 'major', 'status': 'waived',
          'waiver': {'defect_id': 'DEF-p6-env-1-001', 'reason': '用户接受本图、不改卡', 'signed_by': 'user'}}


def _waived_scene(base, sid, waiver, objects=(), defect=DEFECT):
    """dimensions_m 20:10 对 200x200 俯视图 → 比例偏差 100%;按需登记豁免与缺陷单。"""
    data = {'dimensions_m': [20, 3, 10], 'inferred': False, 'objects': list(objects)}
    if waiver is not None:
        data['layout_check_waiver'] = waiver
    write(base / 'bible/scenes' / sid / 'whitebox.json', data)
    if defect is not None:
        write(base / 'qa/defects' / f"{defect['defect_id']}.json", defect)


def test_aspect_waiver_signed_by_user_passes(scene_project, tmp_path):
    import subprocess
    wlc = _mod()
    base, sid = scene_project
    _waived_scene(base, sid, None)
    report = wlc.check_scene(base, sid, write_overlay=False)
    assert report['status'] == 'FAIL' and report['waived'] == [] and '偏差 100.0%' in report['problems'][0]
    _waived_scene(base, sid, {'defect_id': DEFECT['defect_id'], 'items': ['aspect']})
    report = wlc.check_scene(base, sid, write_overlay=False)
    assert report['status'] == 'PASS' and report['problems'] == []
    assert [(w['item'], w['status'], w['defect_id']) for w in report['waived']] == [('aspect', 'WAIVED', DEFECT['defect_id'])]
    assert '偏差 100.0%' in report['waived'][0]['detail'] and report['aspect_deviation_pct'] == 100.0
    # 状态写法 waived_by_user、签名 user:chat / 用户(…) 同样认
    for status, signer in (('waived_by_user', 'user:chat'), ('waived', '用户(控制台确认)'), ('Waived', 'user (via webui --confirm)')):
        _waived_scene(base, sid, {'defect_id': DEFECT['defect_id'], 'items': ['aspect']},
                      defect={**DEFECT, 'status': status, 'waiver': {**DEFECT['waiver'], 'signed_by': signer}})
        assert wlc.check_scene(base, sid, write_overlay=False)['status'] == 'PASS', (status, signer)
    run = subprocess.run([sys.executable, str(ROOT / 'code/whitebox_layout_check.py'), '--project', 'demo', '--out-root', str(base),
                          '--scene', sid, '--no-overlay'], capture_output=True, text=True, cwd=ROOT,
                         env={'PATH': '/usr/bin:/bin', 'VIDEOAGENTS_DATA_DIR': str(tmp_path / 'data')})
    assert run.returncode == 0, run.stdout + run.stderr
    assert f'1 场景比例项已按用户签字豁免 {sid}({DEFECT["defect_id"]})' in run.stdout and '-> PASS' in run.stdout


@pytest.mark.parametrize('waiver, defect, reason', [
    ({'defect_id': 'DEF-p6-env-1-001', 'items': ['aspect']}, {**DEFECT, 'waiver': {**DEFECT['waiver'], 'signed_by': 'workflow-orchestrator'}}, '不是用户签字'),
    ({'defect_id': 'DEF-p6-env-1-001', 'items': ['aspect']}, {**DEFECT, 'waiver': {**DEFECT['waiver'], 'signed_by': 'full-auto'}}, '不是用户签字'),
    ({'defect_id': 'DEF-p6-env-1-001', 'items': ['aspect']}, {**DEFECT, 'waiver': {**DEFECT['waiver'], 'signed_by': 'users-bot'}}, '不是用户签字'),
    ({'defect_id': 'DEF-p6-env-1-001', 'items': ['aspect']}, {**DEFECT, 'waiver': None}, '不是用户签字'),
    ({'defect_id': 'DEF-p6-env-1-001', 'items': ['aspect']}, {**DEFECT, 'scene_id': 'SCN-2'}, '不是本场景 SCN-1'),
    ({'defect_id': 'DEF-p6-env-1-001', 'items': ['aspect']}, {k: v for k, v in DEFECT.items() if k != 'scene_id'}, '不是本场景 SCN-1'),
    ({'defect_id': 'DEF-p6-env-1-001', 'items': ['aspect']}, {**DEFECT, 'status': 'open'}, '不是 waived'),
    ({'defect_id': 'DEF-p6-env-1-001', 'items': ['aspect']}, {**DEFECT, 'status': 'resolved'}, '不是 waived'),
    ({'defect_id': 'DEF-p6-env-1-001', 'items': ['aspect', 'bounds']}, DEFECT, 'items 只能写'),
    ({'defect_id': 'DEF-p6-env-1-001', 'items': []}, DEFECT, 'items 只能写'),
    ({'defect_id': 'DEF-p6-env-1-001'}, DEFECT, 'items 只能写'),
    ('DEF-p6-env-1-001', DEFECT, '须为'),
    ({'defect_id': '../defects/DEF-p6-env-1-001', 'items': ['aspect']}, DEFECT, '不是合法缺陷单号'),
    ({'defect_id': 'DEF-p6-env-1-001/../DEF-p6-env-1-001', 'items': ['aspect']}, DEFECT, '不是合法缺陷单号'),
    ({'defect_id': None, 'items': ['aspect']}, DEFECT, '不是合法缺陷单号'),
    ({'defect_id': 'DEF-missing-001', 'items': ['aspect']}, DEFECT, '不存在'),
])
def test_aspect_waiver_invalid_still_fails(scene_project, waiver, defect, reason):
    wlc = _mod()
    base, sid = scene_project
    _waived_scene(base, sid, waiver, defect=defect)
    report = wlc.check_scene(base, sid, write_overlay=False)
    assert report['status'] == 'FAIL' and report['waived'] == []
    assert len(report['problems']) == 1 and '偏差 100.0%' in report['problems'][0]
    assert '比例豁免无效' in report['problems'][0] and reason in report['problems'][0], report['problems']


def test_aspect_waiver_never_covers_out_of_bounds(scene_project):
    """豁免只管比例:几何越出图幅照旧 FAIL;比例在容差内时声明豁免只记一条说明。"""
    wlc = _mod()
    base, sid = scene_project
    _waived_scene(base, sid, {'defect_id': DEFECT['defect_id'], 'items': ['aspect']},
                  objects=[{'id': 'wall_n', 'size_m': [4, 2, .2], 'xy': [.5, .005], 'collision_role': 'wall'}])
    report = wlc.check_scene(base, sid, write_overlay=False)
    assert report['status'] == 'FAIL' and [w['item'] for w in report['waived']] == ['aspect']
    assert len(report['problems']) == 1 and '脚印越出图幅' in report['problems'][0]
    write(base / 'bible/scenes' / sid / 'whitebox.json', {'dimensions_m': [10, 3, 10], 'inferred': False, 'objects': [],
                                                         'layout_check_waiver': {'defect_id': DEFECT['defect_id'], 'items': ['aspect']}})
    report = wlc.check_scene(base, sid, write_overlay=False)
    assert report['status'] == 'PASS' and report['waived'] == [] and any('无须豁免' in n for n in report['notes'])
