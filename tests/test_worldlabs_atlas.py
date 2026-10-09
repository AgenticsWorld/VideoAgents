"""世界模型 Atlas 渠道(modules/worldlabs_atlas.py)测试:相机换算、深度编码、SPZ 读写、视图规划、坐标约定,
以及假 API 下的生成全链(请求体形状 → 融合落盘 → 与 Marble 共用的世界模型目录/摘要/截图判定)。不联网。"""
import base64
import io
import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules import scene_panos as sp  # noqa: E402
from modules import worldlabs as wl  # noqa: E402
from modules import worldlabs_atlas as atlas  # noqa: E402

SID = 'SCN-T'


def quat_forward(q):
    """XYZW 四元数 → 相机前向(RUB 相机看 -Z)。"""
    x, y, z, w = q
    r = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                  [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                  [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    return -r[:, 2], r[:, 1]


@pytest.mark.parametrize('target', [(0, 1.6, -5), (5, 1.6, 0), (-3, 0.2, 2), (0.5, 1.6, 4)])
def test_pinhole_is_camera_to_world_rub(target):
    cam = atlas.pinhole({'position': [0, 1.6, 0], 'target': list(target), 'fov_v_deg': 60})
    ex, intr = cam['extrinsics'], cam['intrinsics']
    assert ex['coordinateSystem'] == 'rub' and ex['position'] == [0, 1.6, 0]
    f, up = quat_forward(ex['quaternion'])
    want = np.array(target, dtype=float) - [0, 1.6, 0]
    assert np.allclose(f, want / np.linalg.norm(want), atol=1e-5)
    assert up[1] > 0                                       # 相机上方朝世界 +Y(不翻转)
    assert (intr['width'], intr['height'], intr['cx'], intr['cy']) == (1280, 720, 640, 360)
    assert intr['fx'] == intr['fy'] == pytest.approx(360 / math.tan(math.radians(30)), rel=1e-4)


def test_yaw_pitch_target_follows_threejs_yaw():
    assert np.allclose(atlas.yaw_pitch_target([0, 0, 0], 0, 0, 1), [0, 0, -1])          # yaw 0 看 -Z
    assert np.allclose(atlas.yaw_pitch_target([0, 0, 0], 90, 0, 1), [-1, 0, 0], atol=1e-9)   # 逆时针 90° 看 -X
    assert atlas.yaw_pitch_target([0, 0, 0], 0, -40, 1)[1] < 0


def test_log_depth_png_roundtrip():
    z = np.linspace(0.5, 12.0, 1280 * 720).reshape(720, 1280)
    z[-10:, :] = np.inf                                    # 无几何 → 按最远
    png, z_min, z_max = atlas.encode_log_depth(z)
    im = np.asarray(Image.open(io.BytesIO(png)))
    assert im.shape == (720, 1280) and im.dtype == np.uint8
    assert z_min == pytest.approx(0.5) and z_max == pytest.approx(z[np.isfinite(z)].max() * 1.5, rel=1e-4)   # 有无几何处时远端放宽 1.5 倍
    dec = np.exp(math.log(z_min) + (1 - im / 255.0) * (math.log(z_max) - math.log(z_min)))
    fin = np.isfinite(z)
    assert np.all(np.abs(dec[fin] / z[fin] - 1) < 0.02)    # 8 bit log 编码相对误差 < 2%
    assert im[-1, 0] == 0                                  # 无几何 = 黑(最远)


def test_spz_roundtrip(tmp_path):
    rng = np.random.default_rng(1)
    xyz = rng.uniform(-50, 50, (500, 3))
    rgb = rng.uniform(0, 1, (500, 3))
    scale = rng.uniform(0.002, 0.5, 500)
    p = atlas.write_spz(tmp_path / 's.spz', xyz, rgb, scale, np.full(500, 0.9))
    d = atlas.read_spz(p)
    assert d['count'] == 500 and d['sh_degree'] == 0
    assert np.allclose(d['xyz'], xyz, atol=1 / 4096)
    assert np.allclose(d['rgb'], rgb, atol=0.02)
    assert np.all(np.abs(np.log(d['scale'] / scale)) < 1 / 32 + 1e-6)
    assert np.allclose(d['alpha'], 0.9, atol=1 / 255)


def test_to_raw_inverts_viewer_alignment():
    """SPZ 原始坐标经视窗的 rotX(180°) → rotY(yaw) → +camera(scale=1、offset=0)回到白模坐标。"""
    cam = {'position': [2.0, 1.6, -3.0], 'yaw_deg': 90.0}
    pts = np.array([[0, 0, 0], [5, 2, 1], [-1, 0.3, 7.5]], dtype=float)
    raw = atlas.to_raw(pts, cam)
    three = raw * [1, -1, -1]
    y = math.radians(cam['yaw_deg'])
    world = np.stack([math.cos(y) * three[:, 0] + math.sin(y) * three[:, 2], three[:, 1],
                      -math.sin(y) * three[:, 0] + math.cos(y) * three[:, 2]], axis=1) + cam['position']
    assert np.allclose(world, pts)


def test_plan_views_budget_order_and_shot_dedupe():
    scene = {'dimensions_m': [12, 3, 8], 'objects': []}
    shots = [{'ep': 'ep01', 'shot_id': 'sh001', 'role': 'start', 'position': [4, 1.5, 2], 'target': [4, 1.2, -3], 'fov': 30},
             {'ep': 'ep01', 'shot_id': 'sh002', 'role': 'start', 'position': [4.1, 1.5, 2], 'target': [4.1, 1.2, -3], 'fov': 40},
             {'ep': 'ep02', 'shot_id': 'sh009', 'role': 'start', 'position': [-4, 1.5, -2], 'target': [0, 1.2, 0], 'fov': 50}]
    views = atlas.plan_views(scene, {'position': [0, 1.6, 0], 'yaw_deg': 30}, shots, budget=20)
    assert len(views) <= 20
    kinds = [v['kind'] for v in views]
    assert kinds[:9] == ['anchor'] * 6 + ['anchor_floor'] * 3
    shot_views = [v for v in views if v['kind'] == 'shot']
    assert {v['label'] for v in shot_views} == {'ep01/sh001:start', 'ep02/sh009:start'}      # sh002 与 sh001 同位同向,去重
    assert all(v['fov_v_deg'] == atlas.SHOT_FOV_V for v in shot_views)
    assert kinds.count('station') % 4 == 0 and kinds.count('station') >= 4
    f0, _ = quat_forward(atlas.pinhole(views[0])['extrinsics']['quaternion'])
    assert np.allclose(f0, atlas.yaw_pitch_target([0, 0, 0], 30, 0, 1), atol=1e-5)          # 首张 = 全景正前方
    assert len(atlas.plan_views(scene, {'position': [0, 1.6, 0]}, shots, budget=6)) == 6
    assert atlas.max_targets(7) == 28 and atlas.max_targets(0) == 32


def test_cut_pano_samples_anchor_relative_direction():
    pano = np.zeros((256, 512, 3), dtype=np.uint8)
    pano[:, 240:272] = (255, 0, 0)                          # 全景中心列(锚点 yaw 正前方)红色
    anchor = {'position': [0, 1.6, 0], 'yaw_deg': 90}
    front = atlas.cut_pano(pano, anchor, {'position': [0, 1.6, 0], 'target': atlas.yaw_pitch_target([0, 1.6, 0], 90, 0), 'fov_v_deg': 30}, size=(64, 36))
    side = atlas.cut_pano(pano, anchor, {'position': [0, 1.6, 0], 'target': atlas.yaw_pitch_target([0, 1.6, 0], 0, 0), 'fov_v_deg': 30}, size=(64, 36))
    assert front[18, 32, 0] > 200 and side[18, 32, 0] < 50


def test_unproject_flat_wall_lands_on_plane():
    z = np.full((36, 64), 4.0)
    view = {'position': [1.0, 1.5, 0.0], 'target': [1.0, 1.5, -5.0], 'fov_v_deg': 50}
    pts, col, foot = atlas.unproject_view(np.full((36, 64, 3), 128, dtype=np.uint8), z, view)
    assert len(pts) == 32 * 18
    assert np.allclose(pts[:, 2], -4.0) and np.all(foot > 0)
    assert np.allclose(col, 128 / 255.0, atol=1e-3)


# ---------------------------------------------------------------- 假 API 全链
def _project(tmp_path: Path) -> Path:
    base = tmp_path / 'proj'
    sdir = base / 'assets/concepts/scenes' / SID
    (sdir / 'panos/A1').mkdir(parents=True)
    (sdir / 'layout.json').write_text(json.dumps({'scene_name': 'Test room', 'scene_name_en': 'test room', 'dimensions_m': [8, 3, 6]}))
    walls = [{'id': 'n', 'position': [0, 1.5, -3], 'size_m': [8, 3, 0.2]}, {'id': 's', 'position': [0, 1.5, 3], 'size_m': [8, 3, 0.2]},
             {'id': 'w', 'position': [-4, 1.5, 0], 'size_m': [0.2, 3, 6]}, {'id': 'e', 'position': [4, 1.5, 0], 'size_m': [0.2, 3, 6]},
             {'id': 'pillar', 'position': [1.5, 1.5, -1], 'size_m': [0.5, 3, 0.5]}]
    (base / 'bible/scenes' / SID).mkdir(parents=True)
    (base / 'bible/scenes' / SID / 'whitebox.json').write_text(json.dumps({'dimensions_m': [8, 3, 6], 'objects': walls}))
    pano = np.zeros((256, 512, 3), dtype=np.uint8)
    pano[..., 0] = np.linspace(0, 255, 512, dtype=np.uint8)[None, :]
    Image.fromarray(pano).save(sdir / 'panos/A1/day.png')
    (sdir / 'panos/A1/depth_pano.json').write_text(json.dumps({'camera': {'position': [-1.0, 1.6, 0.5], 'yaw_deg': 30.0}}))
    (sdir / 'panos/index.json').write_text(json.dumps({'schema_version': sp.SCHEMA, 'scene_id': SID, 'anchors': [
        {'anchor_id': 'A1', 'position': [-1.0, 1.6, 0.5], 'yaw_deg': 30.0, 'panos': {'day': {'file': 'day.png'}}}]}))
    return base


def test_generate_world_with_fake_api(tmp_path, monkeypatch):
    base = _project(tmp_path)
    monkeypatch.setattr(sp, 'scene_indoor', lambda b, s: True)
    calls = []

    def fake_request(method, path, body=None, **kw):
        calls.append((method, path))
        assert (method, path) == ('POST', '/tasks:atlasChisel')
        assert kw.get('idempotency_key', '').startswith('videoagents-')
        n = len(body['targetCameras'])
        assert n == len(body['contextFrames']) and 2 * n + len(body['sourceFrames']) <= atlas.MAX_ROLLOUT_VIEWS
        assert len(body['sourceFrames']) == 7 and body['prompt'].startswith('Views of an empty real interior location')
        for fr in body['contextFrames']:
            assert set(fr) == {'camera', 'depth'} and set(fr['depth']) == {'logDepthAsset', 'zMin', 'zMax'}
            raw = base64.b64decode(fr['depth']['logDepthAsset']['base64'].split(',', 1)[1])
            assert Image.open(io.BytesIO(raw)).size == (1280, 720)
        for fr in body['sourceFrames']:
            assert set(fr) == {'imageAsset', 'camera'} and fr['imageAsset']['base64'].startswith('data:image/jpeg;base64,')
        buf = io.BytesIO()
        Image.new('RGB', (1280, 720), (200, 120, 40)).save(buf, format='PNG')
        img = base64.b64encode(buf.getvalue()).decode()
        return {'id': 'op_test', 'done': True, 'response': {'frames': [{'imageAsset': {'base64': img}, 'camera': c} for c in body['targetCameras']],
                                                            'promptUsed': 'test prompt'}}

    monkeypatch.setattr(atlas, '_request', fake_request)
    key, out = wl.new_world_dir(base, SID)
    wl.prepare_pano(base, SID, source='scene_pano', anchor_id='A1', text_prompt='', force=True, out=out, log=lambda m: None)
    rec = atlas.generate_world(base, SID, '', out=out, key=key, views=6, log=lambda m: None)
    assert calls == [('POST', '/tasks:atlasChisel')]                    # 提交即完成:不再轮询
    assert rec['provider'] == 'atlas' and rec['model'] == atlas.TASK and rec['world_id'] == 'atlas-op_test'
    assert rec['alignment']['metric_scale_factor'] == 1.0 and rec['alignment']['ground_plane_offset'] == 0.0
    assert rec['alignment']['camera']['position'] == [-1.0, 1.6, 0.5]
    assert set(rec['files']['splats']) == {'full_res', '500k'}
    assert len(rec['atlas']['views']) == 6 and (out / 'thumbnail.jpg').is_file()
    assert json.loads((out / 'generate.json').read_text())['provider'] == 'atlas'
    # 融合出的点回到白模坐标后落在房间里(墙内表面 / 地面 / 顶板)
    d = atlas.read_spz(out / rec['files']['splats']['full_res'])
    raw = d['xyz'] * [1, -1, -1]
    y = math.radians(30.0)
    world = np.stack([math.cos(y) * raw[:, 0] + math.sin(y) * raw[:, 2], raw[:, 1],
                      -math.sin(y) * raw[:, 0] + math.cos(y) * raw[:, 2]], axis=1) + [-1.0, 1.6, 0.5]
    assert d['count'] > 10000
    above = world[:, 1] > 0.01            # 地面板顶在 y=-0.025、墙底在 0:贴地掠射会从这道缝打到墙外地面,墙脚一线不算
    assert np.all(np.abs(world[above, 0]) <= 3.91) and np.all(np.abs(world[above, 2]) <= 2.91)
    assert world[:, 1].min() >= -0.03 and world[:, 1].max() <= 3.01
    # 与 Marble 共用的世界模型目录 / 摘要 / 截图判定
    assert [w['key'] for w in wl.list_worlds(base, SID)] == [key]
    summ = wl.worlds_summary(base, SID, '/projects/proj')[0]
    assert summ['provider'] == 'atlas' and summ['world_marble_url'] is None and set(summ['files']['splats']) == {'full_res', '500k'}
    assert not wl.world_missing(base, SID)


def test_finish_world_refetches_expired_url(tmp_path, monkeypatch):
    """输出读链接过期 → 按 assetId 调 :createReadUrl 重新签。"""
    target = tmp_path / 'v.png'
    seen = []

    def fake_download(url, path, timeout=600):
        seen.append(url)
        if url == 'https://old':
            raise OSError('403')
        path.write_bytes(b'ok')
        return path

    monkeypatch.setattr(wl, 'download', fake_download)
    monkeypatch.setattr(atlas, '_request', lambda m, p, b=None, **kw: {'readUrl': 'https://fresh'} if p == '/assets/asset_1:createReadUrl' else {})
    atlas.fetch_asset({'assetId': 'asset_1', 'url': 'https://old'}, target)
    assert seen == ['https://old', 'https://fresh'] and target.read_bytes() == b'ok'


def test_world_provider_and_keys(monkeypatch):
    monkeypatch.delenv('ATLAS_API_KEY', raising=False)
    monkeypatch.delenv('WORLDLABS_API_KEY', raising=False)
    monkeypatch.setattr(wl, '_genconfig', lambda: {'world': {'provider': 'atlas', 'marble': {'api_key': 'm'}, 'atlas': {'api_key': 'a'}}})
    assert wl.world_provider() == 'atlas'
    assert wl.provider_api_key('atlas') == 'a' and wl.provider_api_key('marble') == 'm'
    monkeypatch.setattr(wl, '_genconfig', lambda: {'world': {'provider': 'nope'}})
    assert wl.world_provider() == 'marble' and wl.provider_api_key('atlas') == ''
    monkeypatch.setenv('ATLAS_API_KEY', 'env')
    assert wl.provider_api_key('atlas') == 'env'

