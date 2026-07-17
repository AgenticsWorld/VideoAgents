#!/usr/bin/env python3
"""Bilibili 投稿 draft uploader via CDP (127.0.0.1:9222).

Drives the user's own Chrome (launched with a debug port, see
launch_cdp_chrome.sh in skill-xhs-cdp-draft) to upload a video to
https://member.bilibili.com/platform/upload/video/ and fill its metadata,
then STOPS — the final 立即投稿 click is always left to the user.

Commands:
    python3 blbl_draft.py check-login
    python3 blbl_draft.py upload --video V.mp4 --title-file t.txt \
        --desc-file d.txt [--tags "a,b,c"] [--cover th.png]
    python3 blbl_draft.py screenshot out.png

Notes:
- Bilibili pre-fills the title with the video filename after upload starts —
  the title fill verifies and refills once (same trap as TikTok).
- Never click 立即投稿 (or 存草稿) from this script.
"""

import argparse
import base64
import json
import re
import sys
import time

import requests
import websocket  # websocket-client

UPLOAD_URL = "https://member.bilibili.com/platform/upload/video/"
UPLOAD_STALL_TIMEOUT = 180
POLL = 3


class CDPError(RuntimeError):
    pass


class CDP:
    """Minimal CDP client over websocket-client (suppress_origin=True)."""

    def __init__(self, host: str = "127.0.0.1", port: int = 9222):
        self.base = f"http://{host}:{port}"
        self.ws = None
        self._id = 0
        self.events: list[dict] = []
        # Never route the local CDP endpoint through a system proxy
        # (macOS system proxies would 503 the /json/* calls).
        self.http = requests.Session()
        self.http.trust_env = False

    # -- tab management -----------------------------------------------------
    def _tabs(self):
        r = self.http.get(f"{self.base}/json/list", timeout=5)
        r.raise_for_status()
        return [t for t in r.json() if t.get("type") == "page"]

    def attach(self, prefer_substr: str, create_url: str | None = None):
        """Attach to the first tab whose URL contains prefer_substr; fall back
        to a blank tab; as a last resort open a new tab (never steal a tab
        that belongs to another flow)."""
        tabs = self._tabs()
        target = next((t for t in tabs if prefer_substr in t.get("url", "")), None)
        if target is None:
            target = next(
                (t for t in tabs
                 if t.get("url", "").startswith(("about:blank", "chrome://newtab",
                                                 "chrome://new-tab-page"))),
                None,
            )
        if target is None:
            url = create_url or "about:blank"
            r = self.http.put(f"{self.base}/json/new?{url}", timeout=5)
            if r.status_code >= 400:  # older Chrome used GET
                r = self.http.get(f"{self.base}/json/new?{url}", timeout=5)
            r.raise_for_status()
            target = r.json()
        ws_url = target.get("webSocketDebuggerUrl")
        if not ws_url:
            raise CDPError(f"Tab has no webSocketDebuggerUrl: {target.get('url')}")
        print(f"[blbl] Attaching to tab: {target.get('url', '')[:90]}")
        self.ws = websocket.create_connection(
            ws_url, suppress_origin=True, max_size=None, enable_multithread=True,
        )
        self.send("Page.enable")
        self.send("Runtime.enable")

    def close(self):
        if self.ws:
            self.ws.close()
            self.ws = None

    # -- protocol -----------------------------------------------------------
    def send(self, method: str, params: dict | None = None, timeout: float = 30.0):
        self._id += 1
        msg_id = self._id
        self.ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.ws.settimeout(max(0.1, deadline - time.time()))
            try:
                raw = self.ws.recv()
            except websocket.WebSocketTimeoutException:
                break
            resp = json.loads(raw)
            if resp.get("id") == msg_id:
                if "error" in resp:
                    raise CDPError(f"{method}: {resp['error']}")
                return resp.get("result", {})
            if resp.get("method"):
                self.events.append(resp)
        raise CDPError(f"{method}: no response within {timeout}s")

    def wait_event(self, method: str, timeout: float = 30.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            for i, ev in enumerate(self.events):
                if ev.get("method") == method:
                    return self.events.pop(i)
            self.ws.settimeout(max(0.1, deadline - time.time()))
            try:
                resp = json.loads(self.ws.recv())
            except websocket.WebSocketTimeoutException:
                break
            if resp.get("method"):
                self.events.append(resp)
        raise CDPError(f"Event {method} not received within {timeout}s")

    def evaluate(self, expr: str, user_gesture: bool = False):
        res = self.send("Runtime.evaluate", {
            "expression": expr, "returnByValue": True, "awaitPromise": True,
            "userGesture": user_gesture,
        })
        if res.get("exceptionDetails"):
            raise CDPError(f"JS exception: {res['exceptionDetails'].get('text')}")
        return res.get("result", {}).get("value")

    def navigate(self, url: str, settle: float = 3.0):
        print(f"[blbl] Navigating to {url}")
        self.send("Page.navigate", {"url": url})
        time.sleep(settle)

    def current_url(self) -> str:
        return self.evaluate("location.href") or ""

    def query_node_id(self, selector: str) -> int:
        self.send("DOM.enable")
        doc = self.send("DOM.getDocument")
        res = self.send("DOM.querySelector", {
            "nodeId": doc["root"]["nodeId"], "selector": selector,
        })
        return int(res.get("nodeId", 0) or 0)

    def set_files(self, selector: str, files: list[str], timeout: float = 30.0):
        deadline = time.time() + timeout
        node_id = 0
        while time.time() < deadline and not node_id:
            node_id = self.query_node_id(selector)
            if not node_id:
                time.sleep(1)
        if not node_id:
            raise CDPError(f"File input not found: {selector}")
        self.send("DOM.setFileInputFiles", {"nodeId": node_id, "files": files})

    def wait_for(self, js_condition: str, timeout: float, what: str):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.evaluate(f"!!({js_condition})"):
                return
            time.sleep(1)
        raise CDPError(f"Timed out waiting for {what} ({int(timeout)}s)")

    def screenshot(self, path: str):
        res = self.send("Page.captureScreenshot", {"format": "png"}, timeout=60)
        with open(path, "wb") as f:
            f.write(base64.b64decode(res["data"]))
        print(f"[blbl] Screenshot saved: {path}")


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def is_logged_in(cdp: CDP) -> bool:
    url = cdp.current_url()
    return "member.bilibili.com" in url and "passport" not in url and "/login" not in url


def cmd_check_login(cdp: CDP) -> int:
    cdp.navigate(UPLOAD_URL, settle=6)
    for _ in range(3):  # allow auth redirects to settle
        if "passport" in cdp.current_url():
            break
        time.sleep(2)
    if is_logged_in(cdp):
        print("[blbl] Login confirmed.")
        return 0
    print(
        "[blbl] NOT LOGGED IN.\n"
        "  Please log in to member.bilibili.com in the Chrome window\n"
        "  (Bilibili app QR scan), then run this script again."
    )
    return 1


def fill_title(cdp: CDP, title: str):
    """Fill the 稿件标题 input (React/Vue controlled — native setter + input
    event), verifying against Bilibili's filename pre-fill."""
    for attempt in (1, 2):
        ok = cdp.evaluate(f"""
            (function() {{
                var inputs = Array.from(document.querySelectorAll('input'));
                var el = inputs.find(function(i) {{
                    return /标题/.test(i.placeholder || '') ||
                           (i.maxLength === 80 && i.offsetParent);
                }});
                if (!el) return false;
                var setter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value').set;
                setter.call(el, {json.dumps(title)});
                el.dispatchEvent(new Event('input', {{bubbles: true}}));
                el.dispatchEvent(new Event('change', {{bubbles: true}}));
                return true;
            }})()
        """)
        if not ok:
            raise CDPError("Title input not found.")
        time.sleep(2)
        got = cdp.evaluate("""
            (function() {
                var inputs = Array.from(document.querySelectorAll('input'));
                var el = inputs.find(function(i) {
                    return /标题/.test(i.placeholder || '') ||
                           (i.maxLength === 80 && i.offsetParent);
                });
                return el ? el.value : '';
            })()
        """) or ""
        if got.strip() == title.strip():
            print("[blbl] Title set.")
            return
        print(f"[blbl] Title mismatch (attempt {attempt}) — refilling...")
    print("[blbl] WARNING: title still differs — review it in the browser.")


def fill_desc(cdp: CDP, desc: str):
    """Fill the 简介 editor — probed live; supports both textarea and
    contenteditable variants of the member page."""
    ok = cdp.evaluate(f"""
        (function() {{
            var ta = Array.from(document.querySelectorAll('textarea')).find(function(t) {{
                return t.offsetParent && /简介|描述/.test(t.placeholder || '');
            }});
            if (ta) {{
                var setter = Object.getOwnPropertyDescriptor(
                    window.HTMLTextAreaElement.prototype, 'value').set;
                setter.call(ta, {json.dumps(desc)});
                ta.dispatchEvent(new Event('input', {{bubbles: true}}));
                return 'textarea';
            }}
            var ed = Array.from(document.querySelectorAll('[contenteditable=true]'))
                .find(function(e) {{ return e.offsetParent; }});
            if (ed) {{
                ed.focus();
                document.execCommand('selectAll', false, null);
                return 'editable';
            }}
            return '';
        }})()
    """)
    if not ok:
        raise CDPError("Description editor not found.")
    if ok == "editable":
        cdp.send("Input.insertText", {"text": desc})
    print(f"[blbl] Description set ({ok}).")
    time.sleep(1)


def add_tags(cdp: CDP, tags: list[str]):
    """Type each tag into the 标签 input and confirm with Enter."""
    for tag in tags:
        tag = tag.strip().lstrip("#")
        if not tag:
            continue
        ok = cdp.evaluate(f"""
            (function() {{
                var el = Array.from(document.querySelectorAll('input')).find(function(i) {{
                    return i.offsetParent && /标签|按回车|Enter/.test(i.placeholder || '');
                }});
                if (!el) return false;
                el.focus();
                return true;
            }})()
        """)
        if not ok:
            print("[blbl] WARNING: tag input not found — add tags manually.")
            return
        cdp.send("Input.insertText", {"text": tag})
        time.sleep(0.8)
        for t in ("keyDown", "keyUp"):
            cdp.send("Input.dispatchKeyEvent", {
                "type": t, "key": "Enter", "code": "Enter",
                "windowsVirtualKeyCode": 13,
            })
        print(f"[blbl] Tag added: {tag}")
        time.sleep(1)


def upload_cover(cdp: CDP, cover_path: str):
    """Set a custom cover via the 更改封面 dialog (probed live; see SKILL.md)."""
    print(f"[blbl] Uploading cover: {cover_path}")
    opened = cdp.evaluate("""
        (function() {
            var els = Array.from(document.querySelectorAll('div,span,button'));
            var best = null;
            for (var i = 0; i < els.length; i++) {
                var own = Array.from(els[i].childNodes)
                    .filter(function(n) { return n.nodeType === 3; })
                    .map(function(n) { return n.textContent.trim(); }).join('');
                if (/^(更改封面|上传封面|编辑封面)$/.test(own) && els[i].offsetParent)
                    best = els[i];
            }
            if (best) { best.click(); return true; }
            return false;
        })()
    """)
    if not opened:
        raise CDPError("Cover entry not found on the page.")
    time.sleep(3)

    # Feed the dialog's image input directly; intercept a native chooser as
    # fallback (click needs a user gesture either way).
    node_id = cdp.query_node_id('input[type="file"][accept*="image"]')
    if node_id:
        cdp.send("DOM.setFileInputFiles", {"nodeId": node_id, "files": [cover_path]})
    else:
        cdp.send("Page.setInterceptFileChooserDialog", {"enabled": True})
        try:
            cdp.evaluate("""
                (function() {
                    var els = Array.from(document.querySelectorAll('div,span,button'));
                    for (var i = 0; i < els.length; i++) {
                        var own = Array.from(els[i].childNodes)
                            .filter(function(n) { return n.nodeType === 3; })
                            .map(function(n) { return n.textContent.trim(); }).join('');
                        if (own.indexOf('上传') !== -1 && els[i].offsetParent) {
                            els[i].click(); return;
                        }
                    }
                })()
            """, user_gesture=True)
            ev = cdp.wait_event("Page.fileChooserOpened", timeout=15)
            cdp.send("DOM.setFileInputFiles", {
                "files": [cover_path],
                "backendNodeId": ev["params"]["backendNodeId"],
            })
        finally:
            cdp.send("Page.setInterceptFileChooserDialog", {"enabled": False})
    print("[blbl] Cover image submitted. Waiting for the editor...")
    time.sleep(4)

    # Confirm through the dialog buttons until it closes.
    for _ in range(4):
        clicked = cdp.evaluate("""
            (function() {
                var names = ['完成', '确定', '确认', '保存'];
                var btns = Array.from(document.querySelectorAll('button, .bcc-button'))
                    .filter(function(b) { return b.offsetParent && !b.disabled; });
                for (var j = 0; j < names.length; j++) {
                    for (var i = 0; i < btns.length; i++) {
                        if ((btns[i].textContent || '').trim() === names[j]) {
                            btns[i].click(); return names[j];
                        }
                    }
                }
                return '';
            })()
        """)
        if not clicked:
            break
        print(f"[blbl] Cover dialog: clicked {clicked}.")
        time.sleep(3)
    print("[blbl] Cover set.")


def wait_upload(cdp: CDP):
    """Stall-based wait for the upload to finish (上传完成 state)."""
    print("[blbl] Waiting for upload to finish...")
    last, deadline = "", time.time() + UPLOAD_STALL_TIMEOUT
    while time.time() < deadline:
        state = cdp.evaluate("""
            (function() {
                var body = document.body.innerText || '';
                var m = body.match(/(\\d+)%/);
                var done = /上传完成|上传成功/.test(body);
                return JSON.stringify({pct: m ? m[0] : '', done: done});
            })()
        """) or "{}"
        state = json.loads(state)
        if state.get("done") and not state.get("pct"):
            print("[blbl] Upload finished.")
            return
        pct = state.get("pct", "")
        if pct and pct != last:
            print(f"[blbl] Upload: {pct}")
            last = pct
            deadline = time.time() + UPLOAD_STALL_TIMEOUT
        time.sleep(POLL)
    print("[blbl] WARNING: upload progress stalled; check the browser window.")


def cmd_upload(cdp: CDP, args) -> int:
    title = open(args.title_file, encoding="utf-8").read().strip()
    desc = open(args.desc_file, encoding="utf-8").read().strip()
    if not title or not desc:
        print("Error: empty title or description.", file=sys.stderr)
        return 2

    cdp.navigate(UPLOAD_URL, settle=6)
    if not is_logged_in(cdp):
        print("[blbl] NOT LOGGED IN — run check-login first.", file=sys.stderr)
        return 1

    # 1) Feed the video file.
    print(f"[blbl] Uploading video: {args.video}")
    cdp.set_files('input[type="file"]', [args.video])

    # 2) Wait for the editing form.
    cdp.wait_for(
        "(function(){var t=document.body.innerText||'';"
        "return t.indexOf('基本设置')!==-1 || t.indexOf('稿件标题')!==-1"
        " || Array.from(document.querySelectorAll('input')).some("
        "function(i){return /标题/.test(i.placeholder||'');});})()",
        90, "editing form",
    )
    time.sleep(3)

    fill_title(cdp, title)
    fill_desc(cdp, desc)
    if args.tags:
        add_tags(cdp, args.tags.split(","))
    if args.cover:
        try:
            upload_cover(cdp, args.cover)
        except CDPError as e:
            print(f"[blbl] WARNING: cover not set ({e}); continuing.")

    # 3) Wait for the upload to finish.
    wait_upload(cdp)

    print("FILL_STATUS: READY_FOR_HUMAN_SUBMIT")
    print("[blbl] Metadata filled. The user must review (分区/活动等) and click 立即投稿.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Bilibili 投稿 CDP draft uploader")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9222)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check-login")

    up = sub.add_parser("upload")
    up.add_argument("--video", required=True, help="Absolute path to the video file")
    up.add_argument("--title-file", required=True)
    up.add_argument("--desc-file", required=True)
    up.add_argument("--tags", help="Comma-separated tags (each confirmed with Enter)")
    up.add_argument("--cover", help="Optional custom cover image")

    shot = sub.add_parser("screenshot")
    shot.add_argument("output", help="PNG output path")

    args = parser.parse_args()
    cdp = CDP(args.host, args.port)
    try:
        cdp.attach("member.bilibili.com", create_url=UPLOAD_URL)
        if args.cmd == "check-login":
            return cmd_check_login(cdp)
        if args.cmd == "upload":
            return cmd_upload(cdp, args)
        if args.cmd == "screenshot":
            cdp.screenshot(args.output)
            return 0
        return 2
    finally:
        cdp.close()


if __name__ == "__main__":
    sys.exit(main())
