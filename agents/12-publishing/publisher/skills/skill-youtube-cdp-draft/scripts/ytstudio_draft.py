#!/usr/bin/env python3
"""YouTube Studio draft uploader via CDP (127.0.0.1:9222).

Drives the user's own Chrome (launched with a debug port, see
launch_cdp_chrome.sh in skill-xhs-cdp-draft) to upload a video to
studio.youtube.com and fill its metadata, then STOPS — the final
save/publish click is always left to the user.

Commands:
    python3 ytstudio_draft.py check-login
    python3 ytstudio_draft.py upload --video V.mp4 --title-file t.txt \
        --desc-file d.txt [--thumbnail th.png] [--tags "a,b,c"] \
        [--made-for-kids] [--stop-at visibility|details]
    python3 ytstudio_draft.py screenshot out.png

Selector notes: YouTube Studio (Polymer, mostly light DOM) keeps stable
element ids across UI languages — #create-icon, #title-textarea #textbox,
#description-textarea #textbox, #next-button, #done-button,
tp-yt-paper-radio-button[name=VIDEO_MADE_FOR_KIDS_NOT_MFK], #file-loader.
Never click #done-button from this script.
"""

import argparse
import base64
import json
import re
import sys
import time

import requests
import websocket  # websocket-client

STUDIO_URL = "https://studio.youtube.com/"
UPLOAD_URL = "https://www.youtube.com/upload"  # redirects into studio upload dialog
# Seconds of *no progress change* before the upload wait gives up (the upload
# itself keeps running in the browser; this only stops the monitoring).
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
        self.events: list[dict] = []  # CDP events buffered during send()
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
        """Attach to the first tab whose URL contains prefer_substr.

        Falls back to a blank tab; as a last resort opens a new tab (never
        steals a tab that belongs to another flow, e.g. the XHS draft).
        """
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
        print(f"[ytstudio] Attaching to tab: {target.get('url', '')[:90]}")
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
        """Return the next buffered/incoming CDP event with the given method."""
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
        # user_gesture grants transient activation — required for clicks that
        # open a file chooser (Chrome silently ignores them otherwise).
        res = self.send("Runtime.evaluate", {
            "expression": expr, "returnByValue": True, "awaitPromise": True,
            "userGesture": user_gesture,
        })
        if res.get("exceptionDetails"):
            raise CDPError(f"JS exception: {res['exceptionDetails'].get('text')}")
        return res.get("result", {}).get("value")

    def navigate(self, url: str, settle: float = 3.0):
        print(f"[ytstudio] Navigating to {url}")
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
        print(f"[ytstudio] Screenshot saved: {path}")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def insert_text(cdp: CDP, selector: str, text: str, what: str):
    """Fill a Studio contenteditable (#textbox) via execCommand so Polymer
    sees real input events."""
    ok = cdp.evaluate(f"""
        (function() {{
            var el = document.querySelector({json.dumps(selector)});
            if (!el) return false;
            el.focus();
            document.execCommand('selectAll', false, null);
            document.execCommand('insertText', false, {json.dumps(text)});
            return true;
        }})()
    """)
    if not ok:
        raise CDPError(f"Could not find {what} field ({selector}).")
    print(f"[ytstudio] {what} set.")
    time.sleep(1)


def expand_advanced(cdp: CDP):
    """Expand the Details page 'Show more' section (idempotent)."""
    cdp.evaluate("""
        (function() {
            var l = document.querySelector('#language-input');
            if (l && l.offsetParent) return;  // already expanded
            var b = document.querySelector('#toggle-button');
            if (b) b.click();
        })()
    """)
    time.sleep(2)


def set_video_language(cdp: CDP, code: str) -> bool:
    """Select the video language (BCP-47 code, e.g. zh-CN) on the Details page.

    Required before Studio enables the Add-subtitles entry. The language menu
    items carry test-id attributes with the code, stable across UI languages.
    """
    expand_advanced(cdp)
    opened = cdp.evaluate("""
        (function() {
            var s = document.querySelector('#language-input');
            if (!s || !s.offsetParent) return false;
            var t = s.querySelector('ytcp-dropdown-trigger') || s;
            t.click(); return true;
        })()
    """)
    if not opened:
        print("[ytstudio] WARNING: video language selector not found.")
        return False
    time.sleep(1.5)
    picked = cdp.evaluate(f"""
        (function() {{
            var boxes = Array.from(document.querySelectorAll('tp-yt-paper-listbox'))
                .filter(function(b) {{ return b.querySelectorAll('tp-yt-paper-item').length > 100; }});
            for (var i = 0; i < boxes.length; i++) {{
                var it = boxes[i].querySelector('tp-yt-paper-item[test-id={json.dumps(code)}]');
                if (it) {{ it.click(); return true; }}
            }}
            return false;
        }})()
    """)
    print(f"[ytstudio] Video language set ({code})." if picked
          else f"[ytstudio] WARNING: language option {code} not found in menu.")
    time.sleep(1)
    return bool(picked)


def add_subtitles(cdp: CDP, srt_path: str):
    """Upload a timed subtitle file on the Video elements page.

    The upload triggers a NATIVE file chooser — intercepted via
    Page.setInterceptFileChooserDialog; the triggering click must carry a
    user gesture or Chrome ignores it entirely (no dialog, no event).
    """
    print(f"[ytstudio] Adding subtitles: {srt_path}")
    cdp.wait_for(
        "(function(){var b=document.querySelector('#subtitles-button');"
        "return b && !b.hasAttribute('disabled');})()",
        30, "subtitles button (needs video language set)",
    )
    cdp.evaluate("document.querySelector('#subtitles-button').click()")
    cdp.wait_for("document.querySelector('#choose-upload-file')", 20, "subtitle method picker")
    time.sleep(1)
    cdp.evaluate("document.querySelector('#choose-upload-file').click()")
    cdp.wait_for(
        "document.querySelector('tp-yt-paper-radio-button[name=with-timing]')",
        15, "timing options",
    )
    cdp.evaluate("document.querySelector('tp-yt-paper-radio-button[name=with-timing]').click()")
    time.sleep(1)

    cdp.send("Page.setInterceptFileChooserDialog", {"enabled": True})
    try:
        cdp.evaluate(
            "document.querySelector('#confirm-button').click()",
            user_gesture=True,
        )
        ev = cdp.wait_event("Page.fileChooserOpened", timeout=20)
        cdp.send("DOM.setFileInputFiles", {
            "files": [srt_path],
            "backendNodeId": ev["params"]["backendNodeId"],
        })
    finally:
        cdp.send("Page.setInterceptFileChooserDialog", {"enabled": False})
    print("[ytstudio] Subtitle file submitted. Waiting for the editor to ingest...")
    time.sleep(5)

    # A stale "file type" dialog can linger over the editor — dismiss it.
    cdp.evaluate("""
        (function() {
            var b = document.querySelector('#cancel-button');
            if (b && b.offsetParent) b.click();
        })()
    """)
    time.sleep(1)

    # Close the caption editor via its Done button (no stable id; small
    # cross-language text set — extend if the Studio UI language differs).
    done = cdp.evaluate("""
        (function() {
            var names = ['Done', '完成'];
            var btns = Array.from(document.querySelectorAll('ytcp-button'))
                .filter(function(b) { return b.offsetParent; });
            for (var i = 0; i < btns.length; i++) {
                if (names.indexOf((btns[i].textContent || '').trim()) !== -1) {
                    btns[i].click(); return true;
                }
            }
            return false;
        })()
    """)
    if not done:
        print("[ytstudio] WARNING: caption editor Done button not found — "
              "close the editor manually before publishing.")
        return
    time.sleep(2)
    print("[ytstudio] Subtitles attached.")


def is_logged_in(cdp: CDP) -> bool:
    url = cdp.current_url()
    if "accounts.google.com" in url or "/signin" in url:
        return False
    return "studio.youtube.com" in url


def cmd_check_login(cdp: CDP) -> int:
    cdp.navigate(STUDIO_URL, settle=5)
    for _ in range(3):  # allow auth redirects to settle
        if "accounts.google.com" in cdp.current_url():
            break
        time.sleep(2)
    if is_logged_in(cdp):
        print("[ytstudio] Login confirmed.")
        return 0
    print(
        "[ytstudio] NOT LOGGED IN.\n"
        "  Please sign in to Google/YouTube in the Chrome window,\n"
        "  then run this script again."
    )
    return 1


def cmd_upload(cdp: CDP, args) -> int:
    title = open(args.title_file, encoding="utf-8").read().strip()
    desc = open(args.desc_file, encoding="utf-8").read().strip()
    if not title or not desc:
        print("Error: empty title or description.", file=sys.stderr)
        return 2

    # 1) Open the upload dialog (language-independent deep link).
    cdp.navigate(UPLOAD_URL, settle=5)
    if not is_logged_in(cdp):
        print("[ytstudio] NOT LOGGED IN — run check-login first.", file=sys.stderr)
        return 1

    # 2) Feed the video file.
    print(f"[ytstudio] Uploading video: {args.video}")
    cdp.set_files('ytcp-uploads-file-picker input[type="file"]', [args.video])

    # 3) Wait for the details form, then fill metadata (upload continues
    #    in the background while we type).
    cdp.wait_for(
        "document.querySelector('#title-textarea #textbox')",
        60, "details form",
    )
    time.sleep(2)
    insert_text(cdp, "#title-textarea #textbox", title, "Title")
    insert_text(cdp, "#description-textarea #textbox", desc, "Description")

    # 4) Audience (required before Next enables).
    kids = "VIDEO_MADE_FOR_KIDS_MFK" if args.made_for_kids else "VIDEO_MADE_FOR_KIDS_NOT_MFK"
    clicked = cdp.evaluate(f"""
        (function() {{
            var r = document.querySelector('tp-yt-paper-radio-button[name={json.dumps(kids)}]');
            if (!r) return false;
            r.click(); return true;
        }})()
    """)
    print(f"[ytstudio] Audience set ({kids})." if clicked
          else "[ytstudio] WARNING: audience radio not found — set it manually.")
    time.sleep(1)

    # 4.5) Video language (under "Show more"): required for subtitles and
    #      good metadata regardless; non-fatal.
    if args.language:
        try:
            set_video_language(cdp, args.language)
        except CDPError as e:
            print(f"[ytstudio] WARNING: video language not set ({e}); continuing.")

    # 5) Optional tags (under "Show more"); non-fatal.
    if args.tags:
        try:
            expand_advanced(cdp)
            insert_text(
                cdp, "#tags-container input#text-input",
                ",".join(t.strip() for t in args.tags.split(",") if t.strip()) + ",",
                "Tags",
            )
        except CDPError as e:
            print(f"[ytstudio] WARNING: tags not set ({e}); continuing.")

    # 6) Optional custom thumbnail; non-fatal (needs a phone-verified channel —
    #    without it the feed is SILENTLY ignored, so verify a preview appeared).
    if args.thumbnail:
        try:
            cdp.set_files("#file-loader", [args.thumbnail], timeout=15)
            time.sleep(4)
            applied = cdp.evaluate("""
                (function() {
                    var t = document.querySelector(
                        'ytcp-thumbnail-uploader, ytcp-thumbnails-compact-editor');
                    return !!(t && t.querySelector('img[src^="data:"], img[src*="ytimg"]'));
                })()
            """)
            print("[ytstudio] Thumbnail applied." if applied else
                  "[ytstudio] WARNING: thumbnail submitted but no preview appeared — "
                  "the channel may lack custom-thumbnail permission (phone verification).")
        except CDPError as e:
            print(f"[ytstudio] WARNING: thumbnail not set ({e}); continuing.")

    # 7) Step through to the Visibility page (never click #done-button),
    #    adding subtitles on the Video elements step when requested.
    def click_next(step: int) -> bool:
        try:
            cdp.wait_for(
                "(function(){var b=document.querySelector('#next-button');"
                "return b && !b.hasAttribute('disabled');})()",
                30, f"next button (step {step})",
            )
        except CDPError as e:
            print(f"[ytstudio] WARNING: {e} — leaving remaining steps to the user.")
            return False
        cdp.evaluate("document.querySelector('#next-button').click()")
        print(f"[ytstudio] Next ({step}/3).")
        time.sleep(3)
        return True

    if args.stop_at == "visibility":
        advanced = click_next(1)  # Details → Video elements
        if advanced and args.subtitles:
            try:
                add_subtitles(cdp, args.subtitles)
            except CDPError as e:
                print(f"[ytstudio] WARNING: subtitles not added ({e}); continuing.")
        if advanced:
            click_next(2) and click_next(3)
    elif args.subtitles:
        print("[ytstudio] WARNING: --subtitles needs the Video elements step; "
              "skipped because of --stop-at details.")

    # 7.5) Safety default: preselect a visibility so a stray Publish click
    #      can't accidentally go public (Studio remembers the last choice).
    if args.stop_at == "visibility" and args.visibility != "none":
        vis = {"private": "PRIVATE", "unlisted": "UNLISTED", "public": "PUBLIC"}[args.visibility]
        set_vis = cdp.evaluate(f"""
            (function() {{
                var r = document.querySelector('tp-yt-paper-radio-button[name={json.dumps(vis)}]');
                if (!r) return false;
                r.click(); return true;
            }})()
        """)
        print(f"[ytstudio] Visibility preselected: {vis}." if set_vis
              else "[ytstudio] WARNING: visibility radio not found — user must pick it.")
        time.sleep(1)

    # 8) Monitor the upload until it finishes (stall-based; the upload keeps
    #    running in the browser even if this monitoring gives up).
    print("[ytstudio] Waiting for upload to finish...")
    last, deadline = "", time.time() + UPLOAD_STALL_TIMEOUT
    while time.time() < deadline:
        label = (cdp.evaluate("""
            (function() {
                var el = document.querySelector('ytcp-video-upload-progress .progress-label, span.progress-label');
                return el ? el.textContent.trim() : '';
            })()
        """) or "").strip()
        if label and label != last:
            print(f"[ytstudio] Upload: {label}")
            last = label
            deadline = time.time() + UPLOAD_STALL_TIMEOUT
        if label and not re.search(r"\d+\s*%", label):
            print("[ytstudio] Upload finished (processing may continue server-side).")
            break
        time.sleep(POLL)
    else:
        print("[ytstudio] WARNING: upload progress stalled; check the browser window.")

    link = cdp.evaluate("""
        (function() {
            var a = document.querySelector('a.ytcp-video-info, ytcp-video-info a');
            return a ? a.href : '';
        })()
    """) or ""
    if link:
        print(f"[ytstudio] Draft video link: {link}")
    print("FILL_STATUS: READY_FOR_HUMAN_SUBMIT")
    print("[ytstudio] Metadata filled. The user must review and click save/publish.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="YouTube Studio CDP draft uploader")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9222)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check-login")

    up = sub.add_parser("upload")
    up.add_argument("--video", required=True, help="Absolute path to the video file")
    up.add_argument("--title-file", required=True)
    up.add_argument("--desc-file", required=True)
    up.add_argument("--thumbnail", help="Optional custom thumbnail image")
    up.add_argument("--subtitles", help="Optional timed subtitle file (.srt/.sbv/.vtt), "
                                        "uploaded on the Video elements step")
    up.add_argument("--language", default="zh-CN",
                    help="Video language BCP-47 code set on the Details page "
                         "(default zh-CN; required for subtitles; '' to skip)")
    up.add_argument("--tags", help="Comma-separated tags")
    up.add_argument("--made-for-kids", action="store_true")
    up.add_argument("--stop-at", choices=["visibility", "details"], default="visibility",
                    help="How far to advance the dialog before handing off (default: visibility)")
    up.add_argument("--visibility", choices=["private", "unlisted", "public", "none"],
                    default="private",
                    help="Visibility to preselect on the last page — Studio remembers the "
                         "previous choice, so default to private to prevent accidental "
                         "public publishes; 'none' leaves it untouched")

    shot = sub.add_parser("screenshot")
    shot.add_argument("output", help="PNG output path")

    args = parser.parse_args()
    cdp = CDP(args.host, args.port)
    try:
        cdp.attach("studio.youtube.com", create_url=STUDIO_URL)
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
