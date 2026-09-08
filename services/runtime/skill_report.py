#!/usr/bin/env python3
"""Register skill lifecycle metadata for the current Agent run; standard library only."""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skill_id")
    parser.add_argument("status", choices=("running", "completed", "skipped", "failed", "unverified"))
    parser.add_argument("--reason", required=True)
    parser.add_argument("--ep", dest="episode")
    parser.add_argument("--group")
    args = parser.parse_args(argv)
    run_id = os.environ.get("VIDEOAGENTS_RUN_ID")
    if not run_id:
        parser.error("VIDEOAGENTS_RUN_ID is missing; invoke within an Agent run")
    base = os.environ.get("VIDEOAGENTS_API_URL", "http://127.0.0.1:" + os.environ.get("VIDEOAGENTS_PORT", "8630")).rstrip("/")
    body = {k: v for k, v in vars(args).items() if v is not None}
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("VIDEOAGENTS_API_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(base + f"/api/v1/runs/{run_id}/skills",
                                 data=json.dumps(body).encode(), headers=headers, method="POST")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=30) as response:
            print(response.read().decode())
    except urllib.error.HTTPError as error:
        print(error.read().decode(), file=sys.stderr)
        return 1
    except (OSError, urllib.error.URLError) as error:
        print(f"技能登记失败: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
