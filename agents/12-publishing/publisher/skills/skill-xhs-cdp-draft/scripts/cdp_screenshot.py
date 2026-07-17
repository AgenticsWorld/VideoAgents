#!/usr/bin/env python3
"""Capture a screenshot of the active Xiaohongshu CDP tab for human review.

Usage:
    python3 cdp_screenshot.py OUTPUT.png [PORT]

Connects to the built-in Chromium CDP endpoint (127.0.0.1:PORT, default 9222),
prefers a xiaohongshu.com page, and saves a full PNG screenshot.
Uses websocket-client with suppress_origin=True (see SKILL.md, 坑 1/坑 2).
"""

import base64
import json
import subprocess
import sys

import websocket  # websocket-client


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: cdp_screenshot.py OUTPUT.png [PORT]", file=sys.stderr)
        return 2
    out_path = sys.argv[1]
    port = sys.argv[2] if len(sys.argv) > 2 else "9222"

    targets = json.loads(
        subprocess.check_output(["curl", "-s", f"http://127.0.0.1:{port}/json/list"])
    )
    pages = [t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
    if not pages:
        print("No page targets found.", file=sys.stderr)
        return 1
    target = next((t for t in pages if "xiaohongshu.com" in t.get("url", "")), pages[0])
    print(f"capturing: {target.get('url', '')[:80]}")

    ws = websocket.create_connection(
        target["webSocketDebuggerUrl"],
        suppress_origin=True,
        max_size=None,
        enable_multithread=True,
    )

    def cmd(i, method, params=None):
        ws.send(json.dumps({"id": i, "method": method, "params": params or {}}))
        while True:
            r = json.loads(ws.recv())
            if r.get("id") == i:
                return r

    res = cmd(1, "Page.captureScreenshot", {"format": "png"})
    ws.close()
    data = res.get("result", {}).get("data")
    if not data:
        print(f"captureScreenshot failed: {res}", file=sys.stderr)
        return 1
    with open(out_path, "wb") as f:
        f.write(base64.b64decode(data))
    print(f"saved: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
