"""#121 白模手部通道左右口径迁移:键名互换、坐标与画面不变、审查指纹/导演台批准续用、幂等、回滚。"""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

import modules.whitebox as wb
from modules import director as dm
from modules.whitebox_camera import placement_fingerprint, source_fingerprint

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('whitebox_hand_migrate', ROOT / 'code/whitebox_hand_migrate.py')
mig = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mig)


def write(path, data, indent=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=indent, ensure_ascii=False), encoding='utf-8')


def legacy_compile(base):
    keep = wb.HAND_SIDES
    wb.HAND_SIDES = mig.LEGACY_SIDES
    try:
        return wb.compile_episode(base, 'ep01')
    finally:
        wb.HAND_SIDES = keep


@pytest.fixture
def project(tmp_path):
    base = tmp_path / 'projects' / 'demo'
    scene = 'SCN-1'
    write(base / 'assets/concepts/scenes' / scene / 'layout.json', {
        'scene_name': 'Room', 'landmarks': [{'id': 'door', 'xy': [.1, .5]}, {'id': 'desk', 'xy': [.7, .5]}],
        'views': [{'tile': 1, 'camera_from': 'door', 'looking_at': 'desk'}]})
    write(base / 'bible/scenes' / scene / 'whitebox.json', {'dimensions_m': [10, 3, 8], 'inferred': False, 'objects': [
        {'id': 'desk', 'size_m': [1, .75, .5], 'xy': [.7, .5]}]})
    shot = {'shot_id': 'sh1', 'scene_id': scene, 'duration_s': 4, 'characters': ['CHAR-1'], 'view_tile': 1}
    group = {'group_id': 'grp1', 'scene_id': scene, 'scene_no': 'S1', 'shots': ['sh1'], 'total_duration_s': 4,
             'characters_union': ['CHAR-1'], 'blocking_map': {'characters': [
                 {'id': 'CHAR-1', 'label': 'A', 'start': {'landmark': 'door'}, 'end': {'landmark': 'desk'}}]}}
    write(base / 'directing/ep01/shot_list.json', {'shots': [shot], 'generation_groups': [group]})
    write(base / 'directing/ep01/shots/sh1/camera.json', {'whitebox_contract': {'origin_m': [5.0, 1.6, 7.5]}})
    key = lambda t: {'t': t, 'position': [5.0, 1.6, 7.5], 'target': [5.0, 1.0, 4.0], 'fov': 40.0}
    plan = {'schema_version': 'whitebox_plan.v1', 'basis': {'actor': '右手写在 left_hand 通道(渲染器镜像)'},
            'actors': [{'id': 'CHAR-1', 'size_m': [.48, 1.7, .38], 'keyframes': [
                # 旧口径:right_hand 挂 +X 肩(= 人物左肩)
                {'t': 0, 'position': [1.0, 0, 4.0], 'yaw': 1.5708, 'right_hand': [0.25, 1.0, 0.3]},
                {'t': 4.0, 'position': [3.0, 0, 4.0], 'yaw': 1.5708, 'right_hand': [0.3, 1.2, 0.35]}]}],
            'cameras': [{'shot_id': 'sh1', 'start': 0, 'duration_s': 4, 'movement': 'static', 'keyframes': [key(0), key(4)]}]}
    plan_p = base / 'directing/ep01/whitebox_plans/grp1.json'
    write(plan_p, plan, indent=2)
    plan['cameras'][0]['source_fingerprint'] = source_fingerprint(base, 'ep01', shot)
    plan['cameras'][0]['placement_fingerprint'] = placement_fingerprint(base, 'ep01', 'grp1')
    write(plan_p, plan, indent=2)
    episode = legacy_compile(base)           # 迁移前由旧版宿主编译、批准、落快照
    assert not episode['errors'], episode['errors']
    g = episode['groups'][0]
    sha = dm.group_sha(episode, g)
    write(base / 'directing/ep01/whitebox/director/versions/grp1/v1.json',
          {'schema': dm.VERSION_SCHEMA, 'group_id': 'grp1', 'v': 1, 'sha': sha,
           'scene': episode['scenes'][g['scene_id']], 'group': g}, indent=2)
    write(base / 'directing/ep01/whitebox/director/notes.json',
          {'schema': dm.SCHEMA, 'notes': [], 'batches': [], 'current': {'grp1': 1},
           'versions': {'grp1': [{'v': 1, 'file': 'x', 'sha': sha}]}, 'approvals': {'grp1': {'sha': sha, 'by': 'user'}}}, indent=2)
    write(base / 'directing/ep01/whitebox/episode.json', episode, indent=2)
    return base


def tree(base):
    return {p.relative_to(base).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in base.rglob('*') if p.is_file()}


def physical(episode):
    sides = dict(wb.HAND_SIDES)
    return {(a['id'], i, sides[n]): k[n] for g in episode['groups'] for a in g['actors']
            for i, k in enumerate(a['keyframes']) for n in sides if n in k}


def test_dry_run_apply_keeps_geometry_reviews_and_rolls_back(project):
    root = project.parent
    before_tree = tree(project)
    before = legacy_compile(project)
    assert mig.main(['--data-root', str(root), '--project', 'demo']) == 0
    assert tree(project) == before_tree                       # dry-run 一个字节都不写
    with pytest.raises(SystemExit):
        mig.main(['--data-root', str(root), '--apply'])       # 没指定项目不许写

    assert mig.main(['--data-root', str(root), '--project', 'demo', '--apply']) == 0
    text = (project / 'directing/ep01/whitebox_plans/grp1.json').read_text()
    plan = json.loads(text)
    assert plan['hand_convention'] == 'anatomical' and text.startswith('{\n  "hand_convention": "anatomical",\n')
    keys = plan['actors'][0]['keyframes']
    assert all('left_hand' in k and 'right_hand' not in k for k in keys)
    assert keys[0]['left_hand'] == [0.25, 1.0, 0.3] and '"t": 4.0' in text   # 坐标不动、浮点写法不塌
    assert plan['basis']['actor'] == '右手写在 left_hand 通道(渲染器镜像)'   # 散文不改

    after = wb.compile_episode(project, 'ep01')               # 新版宿主
    assert not after['errors'], after['errors']               # 放置指纹已按迁移后内容重打,机位契约仍有效
    # 同一条手臂仍挂在 +X 肩:迁移前 right_hand(+1 旧口径)= 迁移后 left_hand(+1 新口径)
    keep = wb.HAND_SIDES
    wb.HAND_SIDES = mig.LEGACY_SIDES
    try:
        before_phys = physical(before)
    finally:
        wb.HAND_SIDES = keep
    old = {(a, i): v for (a, i, s), v in before_phys.items() if s == 1}
    new = {(a, i): v for (a, i, s), v in physical(after).items() if s == 1}
    assert old == new and old
    notes = json.loads((project / 'directing/ep01/whitebox/director/notes.json').read_text())
    assert dm.approvals_public(notes, after)['grp1']['stale'] is False
    assert dm.latest_sha(notes, 'grp1') == dm.group_sha(after, after['groups'][0])
    snap = json.loads((project / 'directing/ep01/whitebox/director/versions/grp1/v1.json').read_text())
    assert 'left_hand' in snap['group']['actors'][0]['keyframes'][0] and snap['hand_convention'] == 'anatomical'
    epj = json.loads((project / 'directing/ep01/whitebox/episode.json').read_text())
    assert 'left_hand' in epj['groups'][0]['actors'][0]['keyframes'][0]
    ledger = json.loads((project / mig.LEDGER_REL).read_text())
    assert ledger['convention'] == 'anatomical' and len(ledger['files']) == 4   # 计划、快照、notes、episode.json

    migrated_tree = tree(project)
    assert mig.main(['--data-root', str(root), '--project', 'demo', '--apply']) == 0
    assert tree(project) == migrated_tree                     # 幂等:第二次什么都不改

    backup = next((project / 'qa').glob(mig.BACKUP_PREFIX + '*'))
    assert mig.main(['--data-root', str(root), '--project', 'demo', '--rollback', str(backup)]) == 0
    restored = {k: v for k, v in tree(project).items() if not k.startswith('qa/')}
    assert restored == before_tree


def test_new_convention_files_written_after_migration_are_left_alone(project):
    root = project.parent
    assert mig.main(['--data-root', str(root), '--project', 'demo', '--apply']) == 0
    # 迁移后新写的计划(新口径、没带标记)不得被再次互换
    plan_p = project / 'directing/ep01/whitebox_plans/grp1.json'
    plan = json.loads(plan_p.read_text())
    plan.pop('hand_convention')
    import os
    import time
    plan_p.write_text(json.dumps(plan, indent=2))
    future = time.time() + 5
    os.utime(plan_p, (future, future))
    ledger = json.loads((project / mig.LEDGER_REL).read_text())
    ledger['files'].pop('directing/ep01/whitebox_plans/grp1.json')
    (project / mig.LEDGER_REL).write_text(json.dumps(ledger))
    snapshot = plan_p.read_bytes()
    assert mig.main(['--data-root', str(root), '--project', 'demo', '--apply']) == 0
    assert plan_p.read_bytes() == snapshot


def test_text_swap_only_touches_keyframe_keys():
    text = '{"actors": [{"id": "A", "keyframes": [{"t": 0.0, "right_hand": [1.0, 2, -0.0]}]}],' \
           ' "action_geometry": {"right_hand": "x"}}'
    new, stat = mig.swap_text(text, mig.PATTERNS['plan'])
    assert stat == {'keys': 1, 'tracks': 1}
    assert new == text.replace('"keyframes": [{"t": 0.0, "right_hand"', '"keyframes": [{"t": 0.0, "left_hand"')
    assert mig.add_marker(new).startswith('{"hand_convention": "anatomical", "actors"')
    compact = '{"a":1}'
    assert mig.add_marker(compact) == '{"hand_convention":"anatomical","a":1}'
