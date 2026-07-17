#!/usr/bin/env python3
"""Douyin creator-center draft uploader via CDP (127.0.0.1:9222).

Drives the user's own Chrome (launched with a debug port, see
launch_cdp_chrome.sh in skill-xhs-cdp-draft) to upload a video to
https://creator.douyin.com/ and fill its metadata, then STOPS — the final
发布 click is always left to the user.

Commands:
    python3 dycreator_draft.py check-login
    python3 dycreator_draft.py upload --video V.mp4 --title-file t.txt \
        --desc-file d.txt [--cover th.png]
    python3 dycreator_draft.py screenshot out.png

The creator center is a React app with obfuscated class names — selectors
lean on stable semantics (placeholders, input[type=file] accept attrs,
button text) rather than ids. Never click the 发布 button from this script.
"""

import argparse
import base64
import json
import re
import sys
import time

import requests
import websocket  # websocket-client

CREATOR_URL = "https://creator.douyin.com/"
UPLOAD_URL = "https://creator.douyin.com/creator-micro/content/upload"
HOME_MARKER = "creator-micro"  # path segment present only when logged in
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
        print(f"[dycreator] Attaching to tab: {target.get('url', '')[:90]}")
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
        print(f"[dycreator] Navigating to {url}")
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
        print(f"[dycreator] Screenshot saved: {path}")


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def is_logged_in(cdp: CDP) -> bool:
    """Logged-in creator center lives under /creator-micro/…"""
    return HOME_MARKER in cdp.current_url()


def cmd_check_login(cdp: CDP) -> int:
    cdp.navigate(CREATOR_URL, settle=5)
    for _ in range(3):  # allow auth redirects to settle
        if is_logged_in(cdp):
            break
        time.sleep(2)
    if is_logged_in(cdp):
        print("[dycreator] Login confirmed.")
        return 0
    print(
        "[dycreator] NOT LOGGED IN.\n"
        "  Please log in to creator.douyin.com in the Chrome window\n"
        "  (Douyin app QR scan), then run this script again."
    )
    return 1


def cmd_upload(cdp: CDP, args) -> int:
    title = open(args.title_file, encoding="utf-8").read().strip()
    desc = open(args.desc_file, encoding="utf-8").read().strip()
    if not title or not desc:
        print("Error: empty title or description.", file=sys.stderr)
        return 2

    cdp.navigate(UPLOAD_URL, settle=5)
    if not is_logged_in(cdp):
        print("[dycreator] NOT LOGGED IN — run check-login first.", file=sys.stderr)
        return 1

    # 1) Feed the video file (the upload page has a file input accepting video).
    print(f"[dycreator] Uploading video: {args.video}")
    cdp.set_files('input[type="file"][accept*="video"]', [args.video])

    # 2) Wait for the editing form (appears once the upload has started).
    cdp.wait_for(
        "(function(){var t=document.body.innerText||'';"
        "return t.indexOf('作品描述')!==-1 || t.indexOf('添加作品简介')!==-1"
        " || !!document.querySelector('.editor-kit-container, [data-placeholder]');})()",
        90, "editing form",
    )
    time.sleep(2)

    fill_title(cdp, title)
    fill_desc(cdp, desc)
    if args.cover:
        try:
            upload_cover(cdp, args.cover)
        except CDPError as e:
            print(f"[dycreator] WARNING: cover not set ({e}); continuing.")

    # 3) Monitor the upload until it finishes (stall-based).
    wait_upload(cdp)

    print("FILL_STATUS: READY_FOR_HUMAN_SUBMIT")
    print("[dycreator] Metadata filled. The user must review and click 发布.")
    return 0


def fill_title(cdp: CDP, title: str):
    """Fill the 作品标题 input (plain <input> with a title placeholder)."""
    ok = cdp.evaluate(f"""
        (function() {{
            var inputs = Array.from(document.querySelectorAll('input'));
            var el = inputs.find(function(i) {{
                return /标题/.test(i.placeholder || '');
            }});
            if (!el) return false;
            var setter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value').set;
            setter.call(el, {json.dumps(title)});
            el.dispatchEvent(new Event('input', {{bubbles: true}}));
            return true;
        }})()
    """)
    if not ok:
        raise CDPError("Title input not found.")
    print("[dycreator] Title set.")
    time.sleep(1)


DESC_EDITOR_SELECTOR = (
    '.editor-kit-container[contenteditable=true], '
    '.editor-kit-container [contenteditable=true], '
    '[data-placeholder*="简介"], [contenteditable=true]'
)


def _focus_desc_editor(cdp: CDP) -> bool:
    return bool(cdp.evaluate(f"""
        (function() {{
            var el = document.querySelector({json.dumps(DESC_EDITOR_SELECTOR)});
            if (!el) return false;
            el.focus();
            return true;
        }})()
    """))


def fill_desc(cdp: CDP, desc: str):
    """Fill the 作品简介 editor via keyboard-level CDP Input events.

    execCommand selectAll/insertText is unreliable in this editor-kit
    (partial replaces observed live) — real key events are not.
    """
    if not _focus_desc_editor(cdp):
        raise CDPError("Description editor not found.")
    time.sleep(0.5)
    modifiers = 4 if sys.platform == "darwin" else 2  # Meta vs Ctrl
    cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "a", "code": "KeyA",
                                        "modifiers": modifiers, "windowsVirtualKeyCode": 65})
    cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "a", "code": "KeyA",
                                        "modifiers": modifiers, "windowsVirtualKeyCode": 65})
    time.sleep(0.3)
    cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Backspace",
                                        "code": "Backspace", "windowsVirtualKeyCode": 8})
    cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Backspace",
                                        "code": "Backspace", "windowsVirtualKeyCode": 8})
    time.sleep(0.5)
    cdp.send("Input.insertText", {"text": desc + " "})
    print("[dycreator] Description set.")
    time.sleep(1)


def upload_cover(cdp: CDP, cover_path: str, orientation: str = "横"):
    """Upload a custom cover via the 选择封面 editor dialog.

    Flow (probed live): open the editor → dismiss onboarding tips → switch to
    设置横封面 (16:9 masters) → the visible 上传封面 tile hides a semi-design
    file input in an ancestor container — tag and feed it directly (no native
    chooser involved) → click 完成 until the dialog closes (the first 完成
    may belong to the 发文助手 tip, so loop).
    """
    print(f"[dycreator] Uploading cover: {cover_path}")
    opened = cdp.evaluate("""
        (function() {
            var els = Array.from(document.querySelectorAll('div,span'));
            var best = null;
            for (var i = 0; i < els.length; i++) {
                var t = (els[i].textContent || '').trim();
                if ((t === '选择封面' || t === '编辑封面' || t === '设置封面')
                    && els[i].offsetParent) best = els[i];
            }
            if (best) { best.click(); return true; }
            return false;
        })()
    """)
    if not opened:
        raise CDPError("Cover entry not found on the page.")
    time.sleep(3)

    # Dismiss onboarding tips that overlay the editor.
    cdp.evaluate("""
        (function() {
            var b = Array.from(document.querySelectorAll('button')).find(function(x) {
                return (x.textContent || '').trim() === '我知道了' && x.offsetParent;
            });
            if (b) b.click();
        })()
    """)
    time.sleep(1)

    # Switch cover orientation tab (default 横封面 for 16:9 masters).
    tab = f"设置{orientation}封面"
    cdp.evaluate(f"""
        (function() {{
            var els = Array.from(document.querySelectorAll('div,span'));
            for (var i = 0; i < els.length; i++) {{
                var own = Array.from(els[i].childNodes)
                    .filter(function(n) {{ return n.nodeType === 3; }})
                    .map(function(n) {{ return n.textContent.trim(); }}).join('');
                if (own === {json.dumps(tab)} && els[i].offsetParent) {{
                    els[i].click(); return;
                }}
            }}
        }})()
    """)
    time.sleep(2)

    # The 上传封面 tile's file input sits a few ancestors up — tag it, feed it.
    tagged = cdp.evaluate("""
        (function() {
            var tiles = Array.from(document.querySelectorAll('div,span,button')).filter(function(e) {
                var own = Array.from(e.childNodes)
                    .filter(function(n) { return n.nodeType === 3; })
                    .map(function(n) { return n.textContent.trim(); }).join('');
                return own.indexOf('上传封面') !== -1 && e.offsetParent;
            });
            if (!tiles.length) return false;
            var node = tiles[0];
            for (var up = 0; up < 8 && node; up++) {
                var inp = node.querySelector &&
                    node.querySelector('input[type=file][accept*="image"]');
                if (inp) { inp.setAttribute('data-cdp-cover', '1'); return true; }
                node = node.parentElement;
            }
            return false;
        })()
    """)
    if not tagged:
        raise CDPError("Cover upload tile / file input not found in the dialog.")
    cdp.set_files('input[data-cdp-cover="1"]', [cover_path], timeout=15)
    print("[dycreator] Cover image submitted. Waiting for the crop view...")
    time.sleep(5)

    # Click 完成/确定 until the editor closes (first hit may be a tip button).
    for _ in range(4):
        clicked = cdp.evaluate("""
            (function() {
                var names = ['完成裁剪', '完成', '确定', '确认'];
                var btns = Array.from(document.querySelectorAll('button'))
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
        print(f"[dycreator] Cover dialog: clicked {clicked}.")
        time.sleep(3)
    still_open = cdp.evaluate("""
        (function() {
            var els = Array.from(document.querySelectorAll('div,span'));
            return els.some(function(e) {
                var own = Array.from(e.childNodes)
                    .filter(function(n) { return n.nodeType === 3; })
                    .map(function(n) { return n.textContent.trim(); }).join('');
                return /^设置(横|竖)封面$/.test(own) && e.offsetParent;
            });
        })()
    """)
    if still_open:
        print("[dycreator] WARNING: cover editor still open — finish it manually.")
    else:
        print("[dycreator] Cover set.")


def wait_upload(cdp: CDP):
    """Stall-based wait for the video upload to finish.

    Note: the page shows a PERSISTENT "上传中，请勿关闭页面" banner that is not
    a live progress state — completion is signalled by the 重新上传 control
    with no percentage left anywhere on the page.
    """
    print("[dycreator] Waiting for upload to finish...")
    last, deadline = "", time.time() + UPLOAD_STALL_TIMEOUT
    while time.time() < deadline:
        state = cdp.evaluate("""
            (function() {
                var body = document.body.innerText || '';
                var m = body.match(/(\\d+)%/);
                return JSON.stringify({
                    pct: m ? m[0] : '',
                    reupload: body.indexOf('重新上传') !== -1,
                });
            })()
        """) or "{}"
        state = json.loads(state)
        if state.get("reupload") and not state.get("pct"):
            print("[dycreator] Upload finished (重新上传 control present).")
            return
        pct = state.get("pct", "")
        if pct and pct != last:
            print(f"[dycreator] Upload: {pct}")
            last = pct
            deadline = time.time() + UPLOAD_STALL_TIMEOUT
        time.sleep(POLL)
    print("[dycreator] WARNING: upload progress stalled; check the browser window.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Douyin creator-center CDP draft uploader")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9222)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check-login")

    up = sub.add_parser("upload")
    up.add_argument("--video", required=True, help="Absolute path to the video file")
    up.add_argument("--title-file", required=True)
    up.add_argument("--desc-file", required=True,
                    help="Description text; #话题 tags inline are kept as typed")
    up.add_argument("--cover", help="Optional custom cover image")

    shot = sub.add_parser("screenshot")
    shot.add_argument("output", help="PNG output path")

    args = parser.parse_args()
    cdp = CDP(args.host, args.port)
    try:
        cdp.attach("creator.douyin.com", create_url=CREATOR_URL)
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
