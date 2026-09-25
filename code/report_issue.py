#!/usr/bin/env python3
"""Issue 反馈 CLI:Agent 运行中确认问题出在宿主代码(code/ modules/ services/ apps/ 等仓库
自带程序)或需要宿主新增功能时,整理成 issue 登记到宿主(modules/issue_feedback.py),
由用户在 Web 客户端左下角「待提交问题」一键到浏览器提交 GitHub。

用法(仅当运行提示词含「Issue 反馈:开启」一节时才调用,触发条件见该节):
  python code/report_issue.py --type bug --title "<一句话标题>" --component code/xxx.py \
      --agent <你的 agent id> --body-file <正文.md>
  python code/report_issue.py --type feature --title "..." --body "..."

正文写:现象 / 复现步骤(命令与参数)/ 期望行为 / 已定位的原因或建议方案。
**只写机制,不写项目内容**:禁止出现剧情、人物名、台词、提示词原文、API Key;
路径用仓库相对路径(宿主会再脱敏一遍)。同一问题重复提交会被签名去重,不会重复开 issue。
退出码恒 0(反馈是旁路,不得因其失败影响工单);结果看 stdout 的 JSON。
"""
import argparse
import json
import os
from pathlib import Path

import _common  # noqa: F401  (sys.path 注入)
from modules import issue_feedback


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--type", required=True, choices=sorted(issue_feedback.KINDS), dest="kind",
                    help="bug=宿主代码缺陷;feature=需要宿主新增的功能")
    ap.add_argument("--title", required=True)
    ap.add_argument("--body", default="")
    ap.add_argument("--body-file", default="", help="正文 markdown 文件(与 --body 二选一)")
    ap.add_argument("--component", default="", help="涉及的宿主文件/模块(仓库相对路径)")
    ap.add_argument("--agent", default=os.environ.get("VIDEOAGENTS_AGENT", ""))
    ap.add_argument("--engine", default=os.environ.get("VIDEOAGENTS_ENGINE", ""))
    args = ap.parse_args()

    if not issue_feedback.enabled():
        print(json.dumps({"ok": False, "state": "disabled",
                          "message": "Issue 反馈已关闭(state.json issue_feedback=false),未提交"}, ensure_ascii=False))
        return
    try:
        body = Path(args.body_file).read_text() if args.body_file else args.body
        rec = issue_feedback.file_issue(args.kind, args.title, body, component=args.component,
                                        agent=args.agent, engine=args.engine)
        if not rec.get("duplicate_of_local"):
            rec = issue_feedback.publish(rec)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "state": "error", "message": str(e)}, ensure_ascii=False))
        return
    print(json.dumps({"ok": True, "id": rec["id"], "state": rec["state"], "url": rec.get("url", ""),
                      "duplicate": bool(rec.get("duplicate_of_local")) or rec["state"] == "duplicate",
                      "message": {"pending": "已登记,待用户在左下角「待提交问题」确认提交",
                                  "failed": "已登记,直接发布失败,待用户在「待提交问题」提交:" + rec.get("error", "")
                                  }.get(rec["state"], "")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
