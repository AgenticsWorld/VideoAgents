#!/usr/bin/env python3
"""白模待决项(whitebox issues)宿主 CLI——查看/裁决(docs/whitebox.md「待决项与用户裁决」,2026-09-09)。

  python code/whitebox_issues.py --project <slug> --ep ep01 --status                       # 汇总 + 逐条列出(默认)
  python code/whitebox_issues.py --project <slug> --ep ep01 --decide WBI-ep01-grp028-001 --choice A [--note "…"] [--by user:chat]
  python code/whitebox_issues.py --project <slug> --ep ep01 --decide WBI-… --choice custom --note "把王三合挪到灶台北侧"
  python code/whitebox_issues.py --project <slug> --ep ep01 --accept-provisional            # 未答复的建议级按默认取舍记为已决(签字时宿主自动做)
  python code/whitebox_issues.py --project <slug> --ep ep01 --pending                       # 只列已裁决待套用项(派 whitebox-staging 套用时内联)

总制片收到用户在聊天里对某条待决项的答复时,用 --decide 落盘(--by user:chat),再派 whitebox-staging 套用;
禁止手改 decisions.json。退出码:--status 有阻断级未清为 1,其余错误 1。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _common import parse_args
from modules.whitebox import component
from modules.whitebox_issues import accept_provisional, collect, decide, decided_pending, format_summary


def main():
    def configure(parser):
        parser.add_argument('--status', action='store_true', help='汇总并逐条列出(默认)')
        parser.add_argument('--pending', action='store_true', help='只列已裁决待套用项')
        parser.add_argument('--decide', metavar='ISSUE_ID', help='写一条裁决')
        parser.add_argument('--choice', help='选项 id | provisional | custom')
        parser.add_argument('--note', default='', help='补充说明;choice=custom 时必填')
        parser.add_argument('--by', default='user:chat', help='裁决来源标识,默认 user:chat')
        parser.add_argument('--accept-provisional', action='store_true', help='未答复的建议级按默认取舍记为已决(by=sign:g6w)')
    args, base = parse_args(__doc__, configure=configure)
    component(args.project); component(args.ep)
    if args.decide:
        if not args.choice:
            raise ValueError('--decide needs --choice')
        issue = decide(base, args.ep, args.decide, args.choice, args.note, args.by)
        print(json.dumps({'decided': issue['issue_id'], 'status': issue['status'], 'decision': issue['decision']}, ensure_ascii=False))
        return 0
    if args.accept_provisional:
        accepted = accept_provisional(base, args.ep)
        print(json.dumps({'accepted_provisional': accepted}, ensure_ascii=False))
        return 0
    collected = collect(base, args.ep)
    if args.pending:
        print(json.dumps({'pending_apply': decided_pending(collected)}, ensure_ascii=False, indent=2))
        return 0
    summary = collected['summary']
    print(json.dumps({'summary': summary, 'text': format_summary(summary)}, ensure_ascii=False))
    for group in collected['groups']:
        for issue in group['issues']:
            decision = issue.get('decision') or {}
            line = f"[{issue['status']:>7}] {issue['severity']:<8} {issue['kind']:<15} {issue['issue_id']}: {issue['question']}"
            if decision:
                line += f"  → {decision.get('choice')}({decision.get('by')}) {decision.get('note') or ''}"
            print(line)
    for error in summary['errors']:
        print(f"[error] {error['group_id']}: {error['error']}", file=sys.stderr)
    return 1 if summary['blocking_open'] or summary['errors'] else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        print(f'whitebox_issues: {error}', file=sys.stderr)
        sys.exit(1)
