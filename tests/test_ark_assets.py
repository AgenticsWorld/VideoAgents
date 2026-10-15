"""长镜头续接素材自动入虚拟人像库(modules/ark_assets.py)+ genmedia 方舟请求体用 asset:// 提交。
方舟素材资产接口用内存假实现,不联网。"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'modules'))
from modules import ark_assets as aa  # noqa: E402
from modules import genmedia as g  # noqa: E402

VIDEO_INFO = {'width': 480, 'height': 854, 'fps': 24., 'duration': 2.96}
IMAGE_INFO = {'width': 854, 'height': 480, 'fps': 0., 'duration': 0.}


class FakeArk:
    """方舟素材资产接口假实现:新建资产的 GetAsset 状态按 seq 依次返回(末项保持)。"""

    def __init__(self, seq=('Processing', 'Active'), error=None):
        self.seq, self.error = list(seq), error
        self.assets, self.calls, self.n = {}, [], 0

    def __call__(self, conf, action, body):
        self.calls.append((action, dict(body)))
        if action == 'CreateAsset':
            self.n += 1
            aid = f'asset-{self.n}'
            self.assets[aid] = list(self.seq)
            return {'Id': aid}
        if action == 'GetAsset':
            if body['Id'] not in self.assets:
                raise aa.AssetsError('GetAsset HTTP 404:NotFound: asset not found')
            seq = self.assets[body['Id']]
            st = seq.pop(0) if len(seq) > 1 else seq[0]
            return {'Status': st, **({'Error': self.error} if st == 'Failed' and self.error else {})}
        if action == 'DeleteAsset':
            self.assets.pop(body['Id'], None)
            return {}
        if action == 'CreateAssetGroup':
            return {'Id': 'group-new'}
        raise AssertionError(action)

    def actions(self, name):
        return [b for a, b in self.calls if a == name]


@pytest.fixture
def env(tmp_path, monkeypatch):
    base = tmp_path / 'data' / 'projects' / 'demo'
    (base / 'assets' / 'clips' / 'ep01').mkdir(parents=True)
    (base / 'assets' / 'continuity' / 'ep01').mkdir(parents=True)
    (base / 'settings.json').write_text(json.dumps({'duration': {'long_take': True}}))
    image = base / 'assets' / 'clips' / 'ep01' / 'grp001.last_frame.png'
    video = base / 'assets' / 'continuity' / 'ep01' / 'grp001.continuation.mp4'
    image.write_bytes(b'png-1')
    video.write_bytes(b'mp4-1')
    config = tmp_path / 'genconfig.json'
    config.write_text(json.dumps({'avatar_assets': {'enabled': True, 'access_key': 'a',
                                                    'secret_key': 'b', 'group_id': 'g1'}}))
    ark = FakeArk()
    monkeypatch.setattr(aa, 'call', ark)
    monkeypatch.setattr(aa, 'probe_media', lambda p: VIDEO_INFO if str(p).endswith('.mp4') else IMAGE_INFO)
    monkeypatch.setattr(aa.time, 'sleep', lambda s: None)
    uploads, logs = [], []

    def upload(p):
        uploads.append(p)
        return f'https://bucket.example/{Path(p).name}'

    def resolve(path):
        return aa.continuity_asset_uri(str(path), config_path=config, ledger_path=tmp_path / 'ledger.json',
                                       upload=upload, log=logs.append)
    return {'base': base, 'image': image, 'video': video, 'config': config, 'ark': ark,
            'uploads': uploads, 'logs': logs, 'resolve': resolve, 'ledger': tmp_path / 'ledger.json'}


def ledger(env):
    return json.loads(env['ledger'].read_text())['assets']


def test_tail_kind():
    assert aa.tail_kind('assets/clips/ep01/grp003.last_frame.png') == 'Image'
    assert aa.tail_kind('/x/data/projects/p/assets/continuity/ep01/grp003.continuation.mp4') == 'Video'
    assert aa.tail_kind('assets/concepts/characters/CHAR-1/sheet.png') is None
    assert aa.tail_kind('assets/continuity/ep01/grp003.last_frame.png') is None    # 尾帧不在 continuity 目录
    assert aa.tail_kind('grp003.continuation.mp4') is None


def test_tail_video_is_ingested_then_submitted_as_asset(env):
    assert env['resolve'](env['video']) == 'asset://asset-1'
    create = env['ark'].actions('CreateAsset')
    assert len(create) == 1
    assert create[0]['AssetType'] == 'Video' and create[0]['GroupId'] == 'g1'
    assert create[0]['URL'] == 'https://bucket.example/grp001.continuation.mp4'
    ent = next(iter(ledger(env).values()))
    assert ent['status'] == 'Active' and ent['kind'] == 'continuity'
    assert ent['slot'] == 'demo|ep01|grp001|Video'


def test_tail_image_is_ingested_as_image(env):
    assert env['resolve'](env['image']) == 'asset://asset-1'
    assert env['ark'].actions('CreateAsset')[0]['AssetType'] == 'Image'


def test_active_entry_is_rechecked_not_reuploaded(env):
    env['resolve'](env['video'])
    env['ark'].calls.clear()
    assert env['resolve'](env['video']) == 'asset://asset-1'
    assert [a for a, _ in env['ark'].calls] == ['GetAsset']
    assert len(env['uploads']) == 1


def test_cleared_library_is_reingested(env):
    env['resolve'](env['video'])
    env['ark'].assets.clear()                       # 全自动管理换集清库 / 手动删除
    assert env['resolve'](env['video']) == 'asset://asset-2'
    assert len(env['ark'].actions('CreateAsset')) == 2


def test_regenerated_predecessor_replaces_old_asset(env):
    env['resolve'](env['video'])
    env['video'].write_bytes(b'mp4-2')              # 前组重出,尾段内容变了
    assert env['resolve'](env['video']) == 'asset://asset-2'
    assert {'Id': 'asset-1', 'ProjectName': 'default'} in env['ark'].actions('DeleteAsset')
    assert [v['asset_id'] for v in ledger(env).values()] == ['asset-2']


def test_gates(env):
    cfg = json.loads(env['config'].read_text())
    cfg['avatar_assets']['enabled'] = False
    env['config'].write_text(json.dumps(cfg))
    assert env['resolve'](env['video']) is None
    cfg['avatar_assets']['enabled'] = True
    env['config'].write_text(json.dumps(cfg))
    (env['base'] / 'settings.json').write_text(json.dumps({'duration': {'long_take': False}}))
    assert env['resolve'](env['video']) is None
    (env['base'] / 'settings.json').write_text(json.dumps({'duration': {'long_take': True}}))
    sheet = env['base'] / 'assets' / 'concepts' / 'characters' / 'CHAR-1' / 'sheet.png'
    sheet.parent.mkdir(parents=True)
    sheet.write_bytes(b'x')
    assert env['resolve'](sheet) is None
    assert env['ark'].calls == []


def test_failed_review_falls_back_and_is_not_retried(env):
    env['ark'].seq = ['Processing', 'Failed']
    env['ark'].error = {'Code': 'InputVideoSensitiveContentDetected', 'Message': 'x'}
    assert env['resolve'](env['video']) is None
    ent = next(iter(ledger(env).values()))
    assert ent['status'] == 'Failed' and ent['asset_id'] is None
    assert env['ark'].actions('DeleteAsset')        # 失败资产删掉不占额度
    env['ark'].calls.clear()
    assert env['resolve'](env['video']) is None
    assert env['ark'].calls == []


def test_transient_failure_is_retried_next_time(env):
    env['ark'].seq = ['Failed']
    env['ark'].error = {'Code': 'DownloadFailed', 'Message': 'x'}
    assert env['resolve'](env['video']) is None
    assert ledger(env) == {}
    env['ark'].seq, env['ark'].error = ['Active'], None
    assert env['resolve'](env['video']) == 'asset://asset-2'


def test_wait_timeout_falls_back(env, monkeypatch):
    env['ark'].seq = ['Processing']
    monkeypatch.setattr(aa, 'WAIT_S', {'Image': 0, 'Video': 0})
    assert env['resolve'](env['video']) is None
    assert next(iter(ledger(env).values()))['status'] == 'Processing'
    env['ark'].assets['asset-1'] = ['Active']       # 下次提交复核到已过审
    assert env['resolve'](env['video']) == 'asset://asset-1'
    assert len(env['ark'].actions('CreateAsset')) == 1


def test_upload_failure_falls_back(env):
    def broken(p):
        raise RuntimeError('reference_video 需公网 URL:请配置文件托管')
    out = aa.continuity_asset_uri(str(env['video']), config_path=env['config'], ledger_path=env['ledger'],
                                  upload=broken, log=env['logs'].append)
    assert out is None and '入库失败' in env['logs'][-1]


def test_missing_group_is_created_and_saved(env):
    cfg = json.loads(env['config'].read_text())
    del cfg['avatar_assets']['group_id']
    env['config'].write_text(json.dumps(cfg))
    assert env['resolve'](env['video']) == 'asset://asset-1'
    assert env['ark'].actions('CreateAsset')[0]['GroupId'] == 'group-new'
    assert json.loads(env['config'].read_text())['avatar_assets']['group_id'] == 'group-new'


def test_spec_problem(tmp_path):
    v = tmp_path / 'a.continuation.mp4'
    v.write_bytes(b'x')
    assert aa.spec_problem(v, 'Video', VIDEO_INFO) is None
    assert '总像素' in aa.spec_problem(v, 'Video', dict(VIDEO_INFO, width=832, height=480))
    assert '时长' in aa.spec_problem(v, 'Video', dict(VIDEO_INFO, duration=1.5))
    assert '帧率' in aa.spec_problem(v, 'Video', dict(VIDEO_INFO, fps=23.976))
    i = tmp_path / 'a.last_frame.png'
    i.write_bytes(b'x')
    assert aa.spec_problem(i, 'Image', IMAGE_INFO) is None
    assert '尺寸' in aa.spec_problem(i, 'Image', dict(IMAGE_INFO, height=300))


def test_ark_body_uses_resolved_asset_uris(monkeypatch):
    monkeypatch.setattr(g, '_avatar_lib_enabled', lambda: False)
    tail_v = 'assets/continuity/ep01/grp001.continuation.mp4'
    tail_i = 'assets/clips/ep01/grp001.last_frame.png'
    uris = {tail_v: 'asset://v1', tail_i: 'asset://i1'}
    body = g._ark_video_body({'model': 'doubao-seedance-2-0-260128'}, 'p', None, None, 5, '480p', '16:9',
                             None, ['scene.png', tail_i], None, None, False,
                             video_refs=['camera.mp4', tail_v], to_url=lambda p: f'data:{p}',
                             video_to_url=lambda p: f'https://u/{p}', asset_uri_for=uris.get)
    urls = [c.get('image_url', c.get('video_url', {})).get('url') for c in body['content'][1:]]
    assert urls == ['data:scene.png', 'asset://i1', 'https://u/camera.mp4', 'asset://v1']


def test_resolver_only_for_ark_seedance2():
    assert g._continuity_asset_resolver({'provider': 'byteplus', 'model': 'seedance-2-0-260128'}) is None
    assert g._continuity_asset_resolver({'provider': 'volcengine', 'model': 'doubao-seedance-1-0-pro'}) is None
    assert callable(g._continuity_asset_resolver({'provider': 'volcengine',
                                                  'model': 'doubao-seedance-2-0-260128'}))
