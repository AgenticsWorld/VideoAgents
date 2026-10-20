"""HunyuanWorld 客户端(modules/hunyuan_world.py)自检:配置读取、world.json 契约、标定公式、
multipart 构造、轮询推进。HTTP 层全部 mock,不触网。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules import hunyuan_world as hw  # noqa: E402
from modules import worldlabs as wl  # noqa: E402


# ---------------------------------------------------------------- config
def test_provider_dispatch_reads_genconfig(tmp_path, monkeypatch):
    cfg = tmp_path / 'genconfig.json'
    monkeypatch.setattr('modules.genmedia.CONFIG_PATH', cfg)
    cfg.write_text(json.dumps({'world': {'provider': 'hunyuan',
                                         'hunyuan': {'base_url': 'http://gpu:8310/', 'token': 't'}}}), encoding='utf-8')
    assert wl.world_provider() == 'hunyuan'
    assert hw.base_url() == 'http://gpu:8310'          # 尾斜杠剥掉
    cfg.write_text(json.dumps({'world': {}}), encoding='utf-8')
    assert wl.world_provider() == 'marble'             # 缺省回落
    cfg.write_text(json.dumps({'world': {'provider': 'bogus'}}), encoding='utf-8')
    assert wl.world_provider() == 'marble'             # 未知值回落


def test_genconfig_save_accepts_hunyuan_provider():
    """api_genconfig_set 的白名单 = set(DEFAULT_GENCONFIG['world']) - {'provider'},须含 hunyuan。"""
    from services.runtime.core import DEFAULT_GENCONFIG
    allowed = set(DEFAULT_GENCONFIG['world']) - {'provider'}
    assert {'marble', 'hunyuan'} <= allowed
    section = DEFAULT_GENCONFIG['world']['hunyuan']
    assert {'base_url', 'token', 'fp8'} <= set(section)
    assert section['fp8'] is False                     # 与 gen_params 缺省一致(bf16;Windows 不推荐 FP8)


def test_env_overrides_config(tmp_path, monkeypatch):
    cfg = tmp_path / 'genconfig.json'
    cfg.write_text(json.dumps({'world': {'hunyuan': {'base_url': 'http://a:1', 'token': 'x'}}}), encoding='utf-8')
    monkeypatch.setattr('modules.genmedia.CONFIG_PATH', cfg)
    monkeypatch.setenv('HUNYUAN_WORKER_URL', 'http://b:2')
    monkeypatch.setenv('HUNYUAN_WORKER_TOKEN', 'y')
    assert hw.hunyuan_config()['base_url'] == 'http://b:2'
    assert hw.hunyuan_config()['token'] == 'y'


# ---------------------------------------------------------------- multipart
def test_multipart_structure():
    body, boundary = hw._multipart({'params': '{"a":1}'}, {'pano': ('pano.png', b'PNGDATA', 'image/png')})
    text = body.decode('latin1')
    assert f'--{boundary}' in text and text.rstrip().endswith(f'--{boundary}--')
    assert 'name="params"' in text and '{"a":1}' in text
    assert 'name="pano"; filename="pano.png"' in text and 'Content-Type: image/png' in text
    assert 'PNGDATA' in text


# ---------------------------------------------------------------- finish_world 契约
def fake_artifacts_job(gpo=1.62):
    return {'status': 'done', 'error': None, 'log_tail': [],
            'artifacts': {'splats': {'full_res': 'splats_full_res.spz', '500k': 'splats_500k.spz'},
                          'collider': 'collider.glb', 'thumbnail': 'thumbnail.jpg', 'world_pano': 'world_pano.jpg'},
            'pack_meta': {'gpo_raw': gpo, 'steps': 8000, 'n_final': 900000}}


def _prepare_world_dir(tmp_path, height_m=1.6):
    out = tmp_path / 'world' / 'worlds' / 'W1'
    out.mkdir(parents=True)
    (out / 'input.json').write_text(json.dumps(
        {'schema_version': wl.SCHEMA, 'source': 'scene_pano', 'anchor_id': 'A1', 'scheme': 'day',
         'camera': {'position': [1.0, 1.75, 2.0], 'yaw_deg': 30.0, 'height_m': height_m}}), encoding='utf-8')
    (out / 'generate.json').write_text(json.dumps({'job_id': 'j1'}), encoding='utf-8')
    return out


def test_finish_world_writes_marble_contract(tmp_path, monkeypatch):
    out = _prepare_world_dir(tmp_path)
    monkeypatch.setattr(hw, 'poll_job', lambda *a, **k: fake_artifacts_job(gpo=1.7))
    monkeypatch.setattr(hw, 'download', lambda job, name, target, **k: target.write_bytes(b'x') or target)
    rec = hw.finish_world(tmp_path, 'SCN-0001', 'j1', log=lambda *a: None, out=out, key='W1')

    # 契约关键字段(与 Marble world.json 同构,viewer/_summary 直接可读)
    assert rec['schema_version'] == wl.SCHEMA and rec['provider'] == 'hunyuan'
    assert rec['world_id'] == 'hy-j1' and rec['model'] == hw.MODEL_NAME
    assert rec['files']['splats']['full_res'] == 'splats_full_res.spz'
    assert rec['files']['collider'] == 'collider.glb'
    al = rec['alignment']
    assert al['metric_scale_factor'] == 1.0 and al['ground_plane_offset'] == 1.7
    assert al['scale_fix'] == round(1.6 / 1.7, 4)      # 标定公式 = 相机真实高 / 拟合原始高
    assert al['yaw_deg'] == 30.0 and al['yaw_fix_deg'] == 0.0
    # _summary 消费口径:provider 透出、world_marble_url 缺省不炸、splats 文件须真实存在
    monkeypatch.setattr(wl, 'list_worlds', lambda b, s: [{'key': 'W1', 'dir': out, 'record': rec}])
    monkeypatch.setattr(wl, 'default_key', lambda b, s, worlds=None: 'W1')
    summary = wl.worlds_summary(tmp_path, 'SCN-0001', '/projects/p')[0]
    assert summary['provider'] == 'hunyuan' and summary['default'] is True
    assert summary['files']['splats'].get('full_res')  # download mock 已落盘
    assert summary['world_marble_url'] is None


def test_finish_world_rejects_missing_splats(tmp_path, monkeypatch):
    out = _prepare_world_dir(tmp_path)
    job = fake_artifacts_job()
    job['artifacts'] = {'collider': 'collider.glb'}
    monkeypatch.setattr(hw, 'poll_job', lambda *a, **k: job)
    with pytest.raises(hw.HunyuanWorldError, match='splats'):
        hw.finish_world(tmp_path, 'SCN-0001', 'j1', log=lambda *a: None, out=out, key='W1')


def test_poll_job_raises_on_worker_error(monkeypatch):
    monkeypatch.setattr(hw, '_request', lambda *a, **k: {'status': 'error', 'error': 'stage gs 失败', 'log_tail': []})
    with pytest.raises(hw.HunyuanWorldError, match='stage gs 失败'):
        hw.poll_job('j1', interval_s=0)


def test_poll_job_progresses_log_tail(monkeypatch):
    states = [
        {'status': 'running', 'stage': 'scenegen', 'log_tail': ['a']},
        {'status': 'running', 'stage': 'gs', 'log_tail': ['a', 'b']},
        {'status': 'done', 'log_tail': ['a', 'b', 'c'], 'artifacts': {}, 'pack_meta': {}},
    ]
    monkeypatch.setattr(hw, '_request', lambda *a, **k: states.pop(0))
    seen = []
    st = hw.poll_job('j1', interval_s=0, log=lambda m: seen.append(m))
    assert st['status'] == 'done'
    assert len(seen) == 3 and seen[0].endswith('a') and seen[-1].endswith('c')   # 增量打印不重复
