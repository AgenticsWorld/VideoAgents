"""白模待决项(issues)与用户裁决(decisions)——docs/whitebox.md「待决项与用户裁决」(2026-09-09)。"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from modules.whitebox import compile_episode
from modules.whitebox_export import fingerprint
from modules.whitebox_issues import (accept_provisional, collect, decide, decided_pending, format_summary,
                                     issue_hash, load_decisions)

ROOT = Path(__file__).resolve().parents[1]


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')


def plan_with(issues):
    return {'schema_version': 'whitebox_plan.v1', 'group_id': 'grp1', 'issues': issues}


ISSUE_A = {'issue_id': 'WBI-ep01-grp1-001', 'kind': 'facing', 'severity': 'advisory',
           'question': '血描说 CHAR-1 面向门,坐标算出背对镜头,以谁为准?',
           'provisional': '按坐标求 yaw,人物转身面向门', 'shots': ['sh1'], 't_range_s': [1, 3],
           'actors': ['CHAR-1'], 'camera_view': {'t': 1.5},
           'options': [{'id': 'A', 'label': '信坐标,改文字', 'rewrites': ['blocking.json']},
                       {'id': 'B', 'label': '信文字,挪站位', 'cost': '同场 3 组重编译'}],
           'recommended': 'A', 'sources': [{'file': 'directing/ep01/shots/sh1/blocking.json', 'quote': '面向门'}]}
ISSUE_B = {'issue_id': 'WBI-ep01-grp1-002', 'kind': 'timing', 'severity': 'blocking',
           'question': 'CHAR-1 要在 4 秒内走 12 米,做不到;延长镜头还是缩短路程?',
           'options': [{'id': 'A', 'label': '镜头延长到 8 秒'}, {'id': 'B', 'label': '起点挪到桌边'}]}


@pytest.fixture
def project(tmp_path):
    base = tmp_path / 'demo'; scene = 'SCN-1'
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
    return base


def test_issues_compile_and_merge(project):
    write(project / 'directing/ep01/whitebox_plans/grp1.json', plan_with([ISSUE_A, ISSUE_B]))
    episode = compile_episode(project, 'ep01')
    assert not episode['errors']
    issues = episode['groups'][0]['issues']
    assert [i['status'] for i in issues] == ['open', 'open']
    assert issues[0]['camera_view'] == {'t': 1.5} and issues[0]['recommended'] == 'A'
    assert episode['issues_summary']['blocking_open'] == 1 and episode['issues_summary']['groups_open'] == ['grp1']
    assert episode['issues_summary']['blocking_ids'] == ['WBI-ep01-grp1-002']
    # 裁决状态不进视频指纹:答题前后 camera.mp4 不应显示过期
    before = fingerprint(episode, episode['groups'][0])
    decide(project, 'ep01', 'WBI-ep01-grp1-002', 'A', by='user:page')
    after = compile_episode(project, 'ep01')
    assert fingerprint(after, after['groups'][0]) == before
    merged = {i['issue_id']: i for i in after['groups'][0]['issues']}
    assert merged['WBI-ep01-grp1-002']['status'] == 'decided'
    assert merged['WBI-ep01-grp1-002']['decision']['choice'] == 'A'
    assert after['issues_summary']['blocking_open'] == 0 and after['issues_summary']['decided'] == 1
    assert decided_pending(collect(project, 'ep01')) == [
        {'group_id': 'grp1', 'issue_id': 'WBI-ep01-grp1-002', 'choice': 'A', 'note': '', 'by': 'user:page'}]
    # 计划的 issue 文本改了 → 旧答复失效(stale)
    changed = dict(ISSUE_B, question='CHAR-1 要在 4 秒内走 20 米')
    write(project / 'directing/ep01/whitebox_plans/grp1.json', plan_with([ISSUE_A, changed]))
    stale = {i['issue_id']: i for i in compile_episode(project, 'ep01')['groups'][0]['issues']}
    assert stale['WBI-ep01-grp1-002']['status'] == 'stale'
    assert issue_hash(changed) != issue_hash(ISSUE_B)
    # Agent 套用后置 applied,decision 不再影响
    applied = dict(changed, status='applied', applied={'choice': 'A', 'at': '2026-09-09T10:00:00+08:00', 'note': '镜头延长到 8 秒'})
    write(project / 'directing/ep01/whitebox_plans/grp1.json', plan_with([ISSUE_A, applied]))
    done = compile_episode(project, 'ep01')
    assert {i['issue_id']: i['status'] for i in done['groups'][0]['issues']} == {
        'WBI-ep01-grp1-001': 'open', 'WBI-ep01-grp1-002': 'applied'}
    assert done['issues_summary']['applied'] == 1
    with pytest.raises(ValueError, match='already applied'):
        decide(project, 'ep01', 'WBI-ep01-grp1-002', 'B')


def test_decide_validation_and_provisional(project):
    write(project / 'directing/ep01/whitebox_plans/grp1.json', plan_with([ISSUE_A, ISSUE_B]))
    with pytest.raises(ValueError, match='choice must be one of'):
        decide(project, 'ep01', 'WBI-ep01-grp1-001', 'Z')
    with pytest.raises(ValueError, match='note must be'):
        decide(project, 'ep01', 'WBI-ep01-grp1-001', 'custom')
    with pytest.raises(ValueError, match='unknown whitebox issue'):
        decide(project, 'ep01', 'WBI-ep01-grp1-009', 'A')
    with pytest.raises(ValueError, match='by must look like'):
        decide(project, 'ep01', 'WBI-ep01-grp1-001', 'A', by='bad by')
    # 阻断级没有 provisional 时不能被签字自动接受;建议级按 recommended 记 sign:g6w
    assert accept_provisional(project, 'ep01') == ['WBI-ep01-grp1-001']
    decisions = load_decisions(project, 'ep01')
    assert decisions['WBI-ep01-grp1-001']['choice'] == 'A' and decisions['WBI-ep01-grp1-001']['by'] == 'sign:g6w'
    assert 'WBI-ep01-grp1-002' not in decisions
    summary = collect(project, 'ep01')['summary']
    assert summary['blocking_open'] == 1 and summary['decided'] == 1
    assert '阻断 1' in format_summary(summary) and 'grp1' in format_summary(summary)
    custom = decide(project, 'ep01', 'WBI-ep01-grp1-001', 'custom', '把门挪到北墙', by='user:chat')
    assert custom['status'] == 'decided' and custom['decision']['note'] == '把门挪到北墙'


@pytest.mark.parametrize('bad, message', [
    (dict(ISSUE_A, issue_id='WBI-ep01-grp9-001'), 'must look like WBI-ep01-grp1-001'),
    (dict(ISSUE_A, kind='vibes'), 'kind must be one of'),
    (dict(ISSUE_A, severity='fatal'), 'severity must be'),
    (dict(ISSUE_A, shots=['sh9']), 'shots must belong'),
    (dict(ISSUE_A, t_range_s=[3, 9]), 't_range_s must be'),
    (dict(ISSUE_A, actors=['CHAR-9']), 'actors must reference'),
    (dict(ISSUE_A, camera_view={'t': 99}), 'camera_view.t must be'),
    (dict(ISSUE_A, recommended='Z'), 'recommended must name'),
    (dict(ISSUE_A, options=[{'id': 'A', 'label': 'x'}, {'id': 'A', 'label': 'y'}]), 'duplicated or reserved'),
    (dict(ISSUE_A, provisional=None), 'provisional must be a non-empty string'),
    (dict(ISSUE_A, status='applied'), 'need an applied'),
    ({'issue_id': 'WBI-ep01-grp1-003', 'kind': 'other', 'severity': 'blocking'}, 'question must be'),
])
def test_issue_schema_errors(project, bad, message):
    write(project / 'directing/ep01/whitebox_plans/grp1.json', plan_with([bad]))
    episode = compile_episode(project, 'ep01')
    assert episode['errors'] and message in episode['errors'][0]['error']
    assert message in collect(project, 'ep01')['summary']['errors'][0]['error']


def test_duplicate_issue_ids_rejected(project):
    write(project / 'directing/ep01/whitebox_plans/grp1.json', plan_with([ISSUE_A, dict(ISSUE_A)]))
    assert 'duplicate issue_id' in compile_episode(project, 'ep01')['errors'][0]['error']


def test_cli_status_and_decide(project):
    write(project / 'directing/ep01/whitebox_plans/grp1.json', plan_with([ISSUE_A, ISSUE_B]))
    data = project.parent   # $VIDEOAGENTS_DATA_DIR/projects/<slug>
    env = {'VIDEOAGENTS_DATA_DIR': str(data), 'PATH': '/usr/bin:/bin'}
    (data / 'projects').mkdir(exist_ok=True)
    (data / 'projects' / 'demo').symlink_to(project)
    run = lambda *args: subprocess.run([sys.executable, str(ROOT / 'code/whitebox_issues.py'), '--project', 'demo', '--ep', 'ep01', *args],
                                       capture_output=True, text=True, env=env, cwd=ROOT)
    status = run('--status')
    assert status.returncode == 1 and '"blocking_open": 1' in status.stdout and 'WBI-ep01-grp1-002' in status.stdout
    decided = run('--decide', 'WBI-ep01-grp1-002', '--choice', 'B', '--note', '起点挪到桌边', '--by', 'user:chat')
    assert decided.returncode == 0, decided.stderr
    assert json.loads(decided.stdout)['decision']['by'] == 'user:chat'
    pending = run('--pending')
    assert json.loads(pending.stdout)['pending_apply'][0]['issue_id'] == 'WBI-ep01-grp1-002'
    assert run('--status').returncode == 0
    render = subprocess.run([sys.executable, str(ROOT / 'code/render_whitebox.py'), '--project', 'demo', '--ep', 'ep01', '--check-only'],
                            capture_output=True, text=True, env=env, cwd=ROOT)
    assert render.returncode == 0, render.stderr
    assert '"issues_text"' in render.stdout and '"decided": 1' in render.stdout


def test_group_approval_waives_open_issues(project):
    from modules.whitebox_issues import waive_group
    write(project / 'directing/ep01/whitebox_plans/grp1.json', plan_with([ISSUE_A, ISSUE_B]))
    decide(project, 'ep01', ISSUE_A['issue_id'], 'B')
    assert waive_group(project, 'ep01', 'grp1', True) == [ISSUE_A['issue_id'], ISSUE_B['issue_id']]   # 未套用的选择也让位
    summary = collect(project, 'ep01')['summary']
    assert summary['blocking_open'] == 0 and summary['waived'] == 2 and summary['decided'] == 0
    assert not summary['groups_open'] and '随组批准接受 2' in format_summary(summary)
    assert decided_pending(collect(project, 'ep01')) == []   # waived 无须套用
    assert compile_episode(project, 'ep01')['groups'][0]['issues'][1]['status'] == 'waived'
    # 问题文本变了 → 批准时的接受失效,重新待裁决
    write(project / 'directing/ep01/whitebox_plans/grp1.json', plan_with([ISSUE_A, {**ISSUE_B, 'question': ISSUE_B['question'] + '(改)'}]))
    assert collect(project, 'ep01')['summary']['blocking_open'] == 1
    write(project / 'directing/ep01/whitebox_plans/grp1.json', plan_with([ISSUE_A, ISSUE_B]))
    # 取消批准 → 撤回,被顶掉的用户裁决还原
    assert waive_group(project, 'ep01', 'grp1', False) == [ISSUE_A['issue_id'], ISSUE_B['issue_id']]
    collected = collect(project, 'ep01')
    assert collected['summary']['blocking_open'] == 1 and collected['summary']['decided'] == 1
    assert decided_pending(collected)[0]['choice'] == 'B'


def test_choose_recommended_answers_all_open_issues(project):
    """导演台「全部选择推荐方案」:未答复的不分阻断/建议都选推荐(没推荐用默认取舍),两者都没有的跳过;已答复的不动。"""
    from modules.whitebox_issues import choose_recommended
    issue_c = {**ISSUE_A, 'issue_id': 'WBI-ep01-grp1-003', 'question': ISSUE_A['question'] + '(三)', 'recommended': None}
    issue_d = {**ISSUE_B, 'issue_id': 'WBI-ep01-grp1-004', 'question': ISSUE_B['question'] + '(四)', 'recommended': 'B'}
    issue_e = {**ISSUE_A, 'issue_id': 'WBI-ep01-grp1-005', 'question': ISSUE_A['question'] + '(五)'}
    write(project / 'directing/ep01/whitebox_plans/grp1.json', plan_with([ISSUE_A, ISSUE_B, issue_c, issue_d, issue_e]))
    decide(project, 'ep01', issue_e['issue_id'], 'B')                       # 用户已自己选过:不覆盖
    result = choose_recommended(project, 'ep01')
    assert result == {'chosen': [ISSUE_A['issue_id'], issue_c['issue_id'], issue_d['issue_id']], 'skipped': [ISSUE_B['issue_id']]}
    decisions = load_decisions(project, 'ep01')
    assert decisions[ISSUE_A['issue_id']]['choice'] == 'A' and decisions[issue_c['issue_id']]['choice'] == 'provisional'
    assert decisions[issue_d['issue_id']]['choice'] == 'B' and decisions[issue_e['issue_id']]['choice'] == 'B'
    summary = collect(project, 'ep01')['summary']
    assert summary['decided'] == 4 and summary['open'] == 1 and summary['blocking_ids'] == [ISSUE_B['issue_id']]
    assert choose_recommended(project, 'ep01') == {'chosen': [], 'skipped': [ISSUE_B['issue_id']]}   # 幂等


def test_waive_groups_batch(project):
    from modules.whitebox_issues import waive_groups
    write(project / 'directing/ep01/whitebox_plans/grp1.json', plan_with([ISSUE_A, ISSUE_B]))
    assert waive_groups(project, 'ep01', ['grp1', 'grp9'], True) == {'grp1': [ISSUE_A['issue_id'], ISSUE_B['issue_id']]}
    assert collect(project, 'ep01')['summary']['waived'] == 2
    assert waive_groups(project, 'ep01', ['grp9'], False) == {}
