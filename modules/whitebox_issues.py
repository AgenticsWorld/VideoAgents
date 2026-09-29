"""白模待决项(whitebox issues)与用户裁决(decisions)——2026-09-09。

白模调度 Agent 在编译计划 `directing/<ep>/whitebox_plans/<grp>.json` 里写 `issues[]`:
调度时发现的缺信息/冲突(人物朝向、机位隔墙、时长内动作做不完…),每条带问题、默认取舍
(provisional)、可选方案(options)。计划照样按默认取舍编译落盘,用户在「分镜设定」预览页
组卡「🧊白模」面板逐条裁决,答复写到用户所有的 `directing/<ep>/whitebox/decisions.json`
(计划归 Agent、决定归用户,重编译/重派互不覆盖)。裁决后回派 whitebox-staging 套用,
Agent 把该条 `status` 置 `applied` 并回写源文件。规约见 docs/whitebox.md「待决项与用户裁决」。

有效状态(编译时合并):applied(Agent 已套用)> decided(有答复且答复对应的问题文本未变)
> stale(问题文本已变,旧答复失效)> open;另有 waived(2026-09-20:导演台「批准本组」时未答复的项
按当前白模原样接受,不再待裁决、无须套用;问题文本再变同样转 stale)。阻断级(blocking)待决未清时 H3W 签字被拒;
建议级(advisory)未答复的在签字时自动按默认取舍记为已决(by=sign:g6w)。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

ISSUE_KINDS = ('facing', 'occlusion', 'timing', 'presence', 'source_conflict',
               'model_gap', 'missing_info', 'continuity', 'other')
SEVERITIES = ('blocking', 'advisory')
PLAN_STATUSES = ('open', 'applied')
PROVISIONAL = 'provisional'          # 用户/签字接受默认取舍时的 choice 值
CUSTOM = 'custom'                    # 自定义答复(note 必填)
APPROVED = 'approved'                # 导演台「批准本组」写入:按当前白模原样接受(有效状态 waived,无须套用)
APPROVED_BY = 'approve:director'
SCHEMA = 'whitebox_decisions.v1'
_ID_RE = re.compile(r'^WBI-(?P<ep>[A-Za-z0-9_-]+)-(?P<gid>[A-Za-z0-9_-]+)-(?P<seq>\d{3})$')


def _issue_id_ok(iid: str, ep: str, gid: str) -> bool:
    """issue_id 须恰为 WBI-{ep}-{gid}-NNN。ep/gid 已知,按前缀精确比对,不靠 _ID_RE 分组解析。"""
    return re.fullmatch(re.escape(f'WBI-{ep}-{gid}-') + r'\d{3}', iid) is not None


def _text(value, name, limit, required=True):
    if value is None and not required:
        return ''
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValueError(f'{name} must be a non-empty string')
    if len(value) > limit:
        raise ValueError(f'{name} exceeds {limit} characters')
    return value.strip()


def issue_hash(issue: dict) -> str:
    """答复绑定的是「这一问题文本」:问题/默认取舍/选项/严重级任一变化即旧答复失效(stale)。"""
    payload = {'question': issue.get('question'), 'provisional': issue.get('provisional'),
               'severity': issue.get('severity'),
               'options': [[o.get('id'), o.get('label')] for o in issue.get('options', [])]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


def validate_issues(plan: dict, ep: str, gid: str, duration: float, shot_ids: list, actor_ids: set) -> list:
    """校验计划里 Agent 写的 issues[],返回规范化副本(附 issue_hash)。结构错误抛 ValueError(编译报错)。"""
    raw = plan.get('issues', [])
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f'{gid}: issues must be a list')
    issues, seen = [], set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f'{gid}: issues[{index}] must be an object')
        iid = _text(item.get('issue_id'), f'{gid}: issues[{index}].issue_id', 80)
        # 已知 ep/gid 前缀精确匹配(#87):_ID_RE 的 ep/gid 都含连字符且贪婪,按组解析会歧义
        if not _issue_id_ok(iid, ep, gid):
            raise ValueError(f'{gid}: issue_id {iid!r} must look like WBI-{ep}-{gid}-001')
        if iid in seen:
            raise ValueError(f'{gid}: duplicate issue_id {iid}')
        seen.add(iid)
        kind = item.get('kind', 'other')
        if kind not in ISSUE_KINDS:
            raise ValueError(f'{iid}: kind must be one of {", ".join(ISSUE_KINDS)}')
        severity = item.get('severity', 'advisory')
        if severity not in SEVERITIES:
            raise ValueError(f'{iid}: severity must be blocking or advisory')
        status = item.get('status', 'open')
        if status not in PLAN_STATUSES:
            raise ValueError(f'{iid}: status must be open or applied')
        question = _text(item.get('question'), f'{iid}.question', 400)
        provisional = _text(item.get('provisional'), f'{iid}.provisional', 400, required=(severity == 'advisory'))
        options, option_ids = [], set()
        for oi, opt in enumerate(item.get('options') or []):
            if not isinstance(opt, dict):
                raise ValueError(f'{iid}: options[{oi}] must be an object')
            oid = _text(opt.get('id'), f'{iid}.options[{oi}].id', 8)
            if oid in option_ids or oid in (PROVISIONAL, CUSTOM):
                raise ValueError(f'{iid}: option id {oid!r} duplicated or reserved')
            option_ids.add(oid)
            entry = {'id': oid, 'label': _text(opt.get('label'), f'{iid}.options[{oi}].label', 120)}
            for key, limit in (('consequence', 300), ('cost', 120)):
                if opt.get(key):
                    entry[key] = _text(opt[key], f'{iid}.options[{oi}].{key}', limit)
            rewrites = opt.get('rewrites') or []
            if not isinstance(rewrites, list) or any(not isinstance(r, str) for r in rewrites):
                raise ValueError(f'{iid}: options[{oi}].rewrites must be a list of paths')
            if rewrites:
                entry['rewrites'] = rewrites
            options.append(entry)
        recommended = item.get('recommended')
        if recommended is not None and recommended not in option_ids:
            raise ValueError(f'{iid}: recommended must name one of the options')
        shots = item.get('shots') or []
        if not isinstance(shots, list) or set(shots) - set(shot_ids):
            raise ValueError(f'{iid}: shots must belong to the group')
        t_range = item.get('t_range_s')
        if t_range is not None:
            if (not isinstance(t_range, list) or len(t_range) != 2 or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in t_range)
                    or not 0 <= t_range[0] <= t_range[1] <= duration + 1e-6):
                raise ValueError(f'{iid}: t_range_s must be [start, end] within 0..{duration} group seconds')
        actors = item.get('actors') or []
        if not isinstance(actors, list) or set(actors) - actor_ids:
            raise ValueError(f'{iid}: actors must reference actors/extras in the group')
        view = item.get('camera_view')
        if view is not None:
            t = view.get('t') if isinstance(view, dict) else None
            if isinstance(t, bool) or not isinstance(t, (int, float)) or not 0 <= t <= duration + 1e-6:
                raise ValueError(f'{iid}: camera_view.t must be a group second within 0..{duration}')
            view = {'t': float(t)}
        sources = item.get('sources') or []
        if not isinstance(sources, list) or any(not isinstance(s, dict) or not isinstance(s.get('file'), str) for s in sources):
            raise ValueError(f'{iid}: sources must be [{{file, quote}}]')
        evidence = item.get('evidence') or {}
        if not isinstance(evidence, dict):
            raise ValueError(f'{iid}: evidence must be an object')
        applied = item.get('applied')
        if status == 'applied' and not isinstance(applied, dict):
            raise ValueError(f'{iid}: applied issues need an applied {{choice, at, note}} record')
        issue = {'issue_id': iid, 'kind': kind, 'severity': severity, 'plan_status': status,
                 'question': question, 'provisional': provisional, 'options': options,
                 'recommended': recommended, 'shots': shots, 't_range_s': t_range, 'actors': actors,
                 'camera_view': view, 'sources': sources, 'evidence': evidence,
                 'applied': applied if status == 'applied' else None}
        issue['issue_hash'] = issue_hash(issue)
        issues.append(issue)
    return issues


def decisions_path(base: Path, ep: str) -> Path:
    return base / 'directing' / ep / 'whitebox' / 'decisions.json'


def load_decisions(base: Path, ep: str) -> dict:
    path = decisions_path(base, ep)
    if not path.is_file():
        return {}
    try:
        doc = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as error:
        raise ValueError(f'{path}: invalid decisions.json ({error})') from error
    decisions = doc.get('decisions') if isinstance(doc, dict) else None
    return decisions if isinstance(decisions, dict) else {}


def merge_decision(issue: dict, decision: dict | None) -> dict:
    """给编译出的 issue 附上 decision 与有效 status。"""
    out = dict(issue)
    if issue.get('plan_status') == 'applied':
        out['status'] = 'applied'
    elif decision:
        if decision.get('issue_hash') != issue['issue_hash']:
            out['status'] = 'stale'
        else:
            # 导演台「批准本组」= 按当前白模原样接受:不再待裁决、也无须 Agent 套用
            out['status'] = 'waived' if decision.get('choice') == APPROVED else 'decided'
    else:
        out['status'] = 'open'
    out['decision'] = decision
    return out


def summarize(groups: list) -> dict:
    """按有效状态汇总:open/blocking_open/decided/applied/stale/waived;groups_open 列有未清项的组。"""
    counts = {'total': 0, 'open': 0, 'blocking_open': 0, 'decided': 0, 'applied': 0, 'stale': 0, 'waived': 0}
    groups_open, blocking_ids = [], []
    for group in groups:
        pending = False
        for issue in group.get('issues') or []:
            counts['total'] += 1
            counts[issue['status']] += 1
            if issue['status'] in ('open', 'stale'):
                pending = True
                if issue['severity'] == 'blocking':
                    counts['blocking_open'] += 1
                    blocking_ids.append(issue['issue_id'])
        if pending:
            groups_open.append(group['group_id'])
    counts['groups_open'] = groups_open
    counts['blocking_ids'] = blocking_ids
    return counts


def collect(base: Path, ep: str) -> dict:
    """不经完整编译,直接读各组计划 + decisions 汇总待决项(闸门判定/CLI 用;结构错误的计划单独列 errors)。"""
    from modules.whitebox import read
    plans = sorted((base / 'directing' / ep / 'whitebox_plans').glob('grp*.json'))
    source = read(base / 'directing' / ep / 'shot_list.json', {}) or {}
    shots = {s['shot_id']: s for s in source.get('shots', [])}
    raw_groups = {g['group_id']: g for g in source.get('generation_groups', [])}
    decisions = load_decisions(base, ep)
    groups, errors = [], []
    for path in plans:
        gid = path.stem
        try:
            plan = read(path, {})
            raw = raw_groups.get(gid) or {}
            duration = sum(shots[s]['duration_s'] for s in raw.get('shots', []) if s in shots) or float('inf')
            actor_ids = {a['id'] for a in plan.get('actors', []) if isinstance(a, dict) and 'id' in a}
            actor_ids |= {a['id'] for a in plan.get('scene_actors', []) if isinstance(a, dict) and 'id' in a}
            actor_ids |= {a['id'] for a in plan.get('extras', []) if isinstance(a, dict) and 'id' in a}
            actor_ids |= {c['id'] for c in (raw.get('blocking_map') or {}).get('characters', []) if 'id' in c}
            issues = validate_issues(plan, ep, gid, duration, raw.get('shots') or [s for s in shots], actor_ids)
        except (ValueError, KeyError, TypeError) as error:
            errors.append({'group_id': gid, 'error': str(error)})
            continue
        groups.append({'group_id': gid, 'issues': [merge_decision(i, decisions.get(i['issue_id'])) for i in issues]})
    summary = summarize(groups)
    summary['errors'] = errors
    return {'ep': ep, 'groups': groups, 'summary': summary}


def find_issue(collected: dict, issue_id: str) -> dict | None:
    for group in collected['groups']:
        for issue in group['issues']:
            if issue['issue_id'] == issue_id:
                return issue
    return None


def decide(base: Path, ep: str, issue_id: str, choice: str, note: str = '', by: str = 'user:page') -> dict:
    """写一条裁决到 decisions.json(原子替换)。choice ∈ 选项 id | provisional | custom(note 必填)。返回合并后的 issue。"""
    collected = collect(base, ep)
    issue = find_issue(collected, issue_id)
    if not issue:
        raise ValueError(f'unknown whitebox issue {issue_id} in {ep}')
    if issue['status'] == 'applied':
        raise ValueError(f'{issue_id} already applied; ask whitebox-staging to reopen it if it needs another decision')
    choice = _text(choice, 'choice', 40)
    note = _text(note, 'note', 1000, required=(choice == CUSTOM))
    valid = {o['id'] for o in issue['options']} | {CUSTOM} | ({PROVISIONAL} if issue['provisional'] else set())
    if choice not in valid:
        raise ValueError(f'{issue_id}: choice must be one of {", ".join(sorted(valid))}')
    if not re.fullmatch(r'[\w:.-]{1,40}', by or ''):
        raise ValueError('by must look like user:page / user:chat / sign:g6w')
    path = decisions_path(base, ep)
    path.parent.mkdir(parents=True, exist_ok=True)
    decisions = load_decisions(base, ep)
    decisions[issue_id] = {'choice': choice, 'note': note, 'by': by,
                           'at': time.strftime('%Y-%m-%dT%H:%M:%S+08:00'), 'issue_hash': issue['issue_hash']}
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps({'schema_version': SCHEMA, 'ep': ep, 'decisions': decisions},
                              ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)
    return merge_decision(issue, decisions[issue_id])


def _save_decisions(base: Path, ep: str, decisions: dict) -> None:
    path = decisions_path(base, ep)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps({'schema_version': SCHEMA, 'ep': ep, 'decisions': decisions},
                              ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def waive_group(base: Path, ep: str, gid: str, on: bool, by: str = APPROVED_BY) -> list:
    """导演台「批准本组」:该组未套用(open/stale/decided)的待决项记为 approved=按当前白模原样接受(有效状态 waived,
    阻断级也不再拦 H3W 签字/导出);取消批准则撤回这些记录(被顶掉的用户答复还原)。返回处理的 issue_id。"""
    decisions = load_decisions(base, ep)
    group = next((g for g in collect(base, ep)['groups'] if g['group_id'] == gid), None)
    touched = []
    for issue in (group or {}).get('issues', []):
        iid = issue['issue_id']
        if on and issue['status'] in ('open', 'stale', 'decided'):
            # decided = 用户选过方案但 Agent 还没套用:批准的是当前白模,未套用的选择同样让位(原答复留在 superseded,取消批准时还原)
            decisions[iid] = {'choice': APPROVED, 'note': '批准本组即按当前白模接受', 'by': by,
                              'at': time.strftime('%Y-%m-%dT%H:%M:%S+08:00'), 'issue_hash': issue['issue_hash']}
            if issue['status'] == 'decided':
                decisions[iid]['superseded'] = issue['decision']
            touched.append(iid)
        elif not on and (decisions.get(iid) or {}).get('choice') == APPROVED:
            old = decisions.pop(iid).get('superseded')
            if old:
                decisions[iid] = old
            touched.append(iid)
    if touched:
        _save_decisions(base, ep, decisions)
    return touched


def accept_provisional(base: Path, ep: str, by: str = 'sign:g6w') -> list:
    """签字时把未答复的建议级待决项按默认取舍(recommended 选项,否则 provisional)记为已决;返回处理的 issue_id。"""
    accepted = []
    for group in collect(base, ep)['groups']:
        for issue in group['issues']:
            if issue['severity'] != 'advisory' or issue['status'] not in ('open', 'stale'):
                continue
            choice = issue['recommended'] or (PROVISIONAL if issue['provisional'] else None)
            if not choice:
                continue
            decide(base, ep, issue['issue_id'], choice, '签字即接受默认取舍', by)
            accepted.append(issue['issue_id'])
    return accepted


def decided_pending(collected: dict, group_ids: list | None = None) -> list:
    """已裁决、尚待 Agent 套用的项(status=decided),供「应用决定并重编译」派单内联。"""
    rows = []
    for group in collected['groups']:
        if group_ids and group['group_id'] not in group_ids:
            continue
        for issue in group['issues']:
            if issue['status'] == 'decided':
                rows.append({'group_id': group['group_id'], 'issue_id': issue['issue_id'],
                             'choice': issue['decision']['choice'], 'note': issue['decision'].get('note', ''),
                             'by': issue['decision'].get('by', '')})
    return rows


def format_summary(summary: dict, lang: str = 'zh') -> str:
    """签字卡/回执用的一行摘要。lang='zh' 出中文,其余一律英文(2026-09-22,随界面语言)。"""
    groups = summary.get('groups_open') or []
    tail_groups = ', '.join(groups[:8]) + ('…' if len(groups) > 8 else '')
    if lang != 'zh':
        if not summary.get('total'):
            return 'Whitebox open decisions: 0'
        pending = summary['open'] + summary['stale']
        text = (f"Whitebox open decisions: {pending} pending ({summary['blocking_open']} blocking), "
                f"{summary['decided']} decided awaiting apply, {summary['applied']} applied")
        if summary.get('waived'):
            text += f", {summary['waived']} accepted with group approval"
        if groups:
            text += '; groups: ' + tail_groups
        return text
    if not summary.get('total'):
        return '白模待决项:0'
    pending = summary['open'] + summary['stale']
    text = f"白模待决项:待处理 {pending}(阻断 {summary['blocking_open']})、已裁决待套用 {summary['decided']}、已套用 {summary['applied']}"
    if summary.get('waived'):
        text += f"、随组批准接受 {summary['waived']}"
    if groups:
        text += ';涉及 ' + tail_groups
    return text
