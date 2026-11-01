#!/usr/bin/env python3
"""Instagram (web) Reel draft filler via CDP (127.0.0.1:9222).

Drives the user's own Chrome (launched with a debug port, see
launch_cdp_chrome.sh in skill-xhs-cdp-draft) through instagram.com's
"New post" dialog: video -> crop (Original) -> cover -> caption, then
STOPS on the last step — the final Share click is always left to the user.

Commands:
    python3 igweb_draft.py check-login [--new-tab | --tab-id ID]
    python3 igweb_draft.py upload --video V.mp4 --caption-file c.txt \
        [--cover cover.jpg] [--ai-label] [--max-hashtags 5] \
        [--new-tab | --tab-id ID]
    python3 igweb_draft.py screenshot out.png [--tab-id ID]

Facts found live (2026-11-01, desktop web):
- Every video posted from the web is a Reel ("Video posts are now shared as
  reels" notice after picking the file; the script dismisses it).
- There are NO drafts on the web: closing the dialog discards everything
  ("Discard post? If you leave, your edits won't be saved."). The video is
  only uploaded AFTER the user clicks Share, so the tab must stay open until
  Instagram confirms the reel was shared. One tab per reel (--new-tab).
- The crop step defaults to a 1:1 crop; the script picks "Original" (the one
  crop option that is not a W:H ratio), so 9:16 stays 9:16.
- The edit step takes a custom cover from `input[type=file][accept*=image/jpeg]`
  (JPG/PNG only).
- The caption editor is Lexical (`[data-lexical-editor=true]`, role=textbox):
  JS selectAll + CDP Input.insertText works, newlines and #hashtags included.
  Typing a #tag opens a suggestion list; blurring the editor closes it.
- Caption limit 2,200 characters; since late 2025 Instagram allows at most 5
  hashtags per post (the page shows no warning before Share, so the script
  trims extras itself and prints HASHTAGS_TRIMMED).
- "Add AI label" switch on the last step — Instagram requires it for realistic
  AI-generated photos and videos; --ai-label turns it on.

Selectors avoid UI text (the interface follows the account language): the
"New post" entry is the sidebar link whose icon is the plus shape (aria-label
used only as a fallback), Next is the text button at the top right of the
dialog header, the crop menu is found by its W:H options. If the entry
cannot be found the script asks the user to click "New post" and waits.
Never clicks Share.
"""

import argparse
import base64
import json
import re
import shutil
import subprocess
import sys
import time

import requests
import websocket  # websocket-client

HOME_URL = "https://www.instagram.com/"
CAPTION_MAX = 2200          # Instagram counter (UTF-16 units, like the page's 0/2,200)
HASHTAG_MAX = 5             # per post since late 2025
REACH_S = 180.0             # Reels over 3 minutes are rarely recommended to non-followers
COVER_EXTS = (".jpg", ".jpeg", ".png")
TAB_PROBE_TIMEOUT = 5.0
STEP_TIMEOUT = 60           # seconds for the dialog to reach the caption step
MANUAL_ENTRY_WAIT = 90      # seconds to wait for the user to click "New post" by hand

# Known labels of the sidebar "New post" icon (fallback only; the plus-shaped
# path is tried first).
NEW_POST_LABELS = ("New post", "Create", "新帖子", "创建", "新貼文", "建立", "新規投稿", "作成",
                   "새 게시물", "만들기", "Nueva publicación", "Crear", "Nova publicação", "Criar",
                   "Nouvelle publication", "Créer", "Neuer Beitrag", "Erstellen", "Новая публикация",
                   "Создать", "Postingan baru", "Buat", "Bài viết mới", "Tạo", "منشور جديد", "إنشاء")
HASHTAG_RE = re.compile(r"(?<![\w#&])#(\w+)")

# In-page helpers, prepended to every dialog script.
_JS_LIB = r"""
var IG = (function() {
  function vis(e) { return !!(e && e.getClientRects().length); }
  function dialogs() {
    return Array.from(document.querySelectorAll('[role=dialog]')).filter(vis);
  }
  var MEDIA = 'video, canvas, input[type=file], [data-lexical-editor=true]';
  function createDialog() {
    var ds = dialogs().filter(function(d) { return d.querySelector(MEDIA); });
    ds.sort(function(a, b) {
      var ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
      return rb.width * rb.height - ra.width * ra.height;
    });
    return ds[0] || null;
  }
  function buttons(root) {
    return Array.from(root.querySelectorAll('[role=button], button')).filter(vis);
  }
  function text(b) { return (b.innerText || '').trim(); }
  // Next / Share: the text-only button at the top right of the dialog header.
  function headerButton(d) {
    var r = d.getBoundingClientRect();
    var c = buttons(d).filter(function(b) {
      if (b.querySelector('svg') || !text(b)) return false;
      var br = b.getBoundingClientRect();
      return br.top - r.top < 70 && br.left > r.left + r.width / 2;
    });
    c.sort(function(a, b) { return b.getBoundingClientRect().right - a.getBoundingClientRect().right; });
    return c[0] || null;
  }
  function editor(d) { return d && d.querySelector('[data-lexical-editor=true], [contenteditable=true][role=textbox]'); }
  function coverInput(d) { return d && d.querySelector('input[type=file][accept*="image/jpeg"], input[type=file][accept*="image/png"]'); }
  var RATIO = /^\d+\s*[:：]\s*\d+$/;
  function ratioButtons(d) { return buttons(d).filter(function(b) { return RATIO.test(text(b)); }); }
  // Crop toggle: icon-only button in the media area (below the header), left-most.
  // It is a div[role=button] wrapping a <button>; only the inner one reacts to click().
  function cropToggle(d) {
    var r = d.getBoundingClientRect();
    var c = buttons(d).filter(function(b) {
      if (!b.querySelector('svg') || text(b)) return false;
      return b.getBoundingClientRect().top - r.top > 70;
    });
    c = c.filter(function(b) { return !c.some(function(o) { return o !== b && b.contains(o); }); });
    c.sort(function(a, b) {
      var ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
      return (ra.left - rb.left) || (rb.top - ra.top);
    });
    return c[0] || null;
  }
  function step() {
    var d = createDialog();
    if (!d) return 'none';
    if (editor(d)) return 'caption';
    if (coverInput(d)) return 'edit';
    if (headerButton(d)) return 'crop';
    return 'select';
  }
  return {vis: vis, dialogs: dialogs, createDialog: createDialog, buttons: buttons, text: text,
          headerButton: headerButton, editor: editor, coverInput: coverInput,
          ratioButtons: ratioButtons, cropToggle: cropToggle, step: step};
})();
"""

_BUSY_JS = _JS_LIB + "IG.step() !== 'none'"


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

    def _new_tab(self, url: str) -> dict:
        r = self.http.put(f"{self.base}/json/new?{url}", timeout=5)
        if r.status_code >= 400:  # older Chrome used GET
            r = self.http.get(f"{self.base}/json/new?{url}", timeout=5)
        r.raise_for_status()
        target = r.json()
        try:  # bring it to front: background tabs get throttled
            self.http.get(f"{self.base}/json/activate/{target.get('id', '')}", timeout=5)
        except requests.RequestException:
            pass
        return target

    def _probe_busy(self, ws_url: str) -> bool | None:
        """True/False = a create dialog is open or not; None = tab did not answer."""
        try:
            ws = websocket.create_connection(ws_url, suppress_origin=True, timeout=TAB_PROBE_TIMEOUT)
        except (websocket.WebSocketException, OSError):
            return None
        try:
            ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {
                "expression": _BUSY_JS, "returnByValue": True}}))
            deadline = time.time() + TAB_PROBE_TIMEOUT
            while time.time() < deadline:
                ws.settimeout(max(0.1, deadline - time.time()))
                resp = json.loads(ws.recv())
                if resp.get("id") == 1:
                    if "error" in resp:
                        return None
                    return bool(resp.get("result", {}).get("result", {}).get("value"))
            return None
        except (websocket.WebSocketException, OSError, ValueError):
            return None
        finally:
            ws.close()

    def _tab_busy(self, tab: dict) -> str:
        """Why this instagram tab must not be reused ('' = free to reuse)."""
        ws_url = tab.get("webSocketDebuggerUrl")
        if not ws_url:
            return "no debugger url"
        state = self._probe_busy(ws_url)
        if state is None:
            return "tab not responding"
        return "post dialog open (a reel waiting for Share?)" if state else ""

    def attach(self, prefer_substr: str, create_url: str | None = None, *,
               new_tab: bool = False, tab_id: str | None = None, skip_busy: bool = False):
        """Attach to the first tab whose URL contains prefer_substr; fall back to
        a blank tab, then a new tab (never steal a tab of another flow).
        skip_busy: pass over instagram tabs holding an open post dialog — the
        web has no drafts, navigating away would discard that reel."""
        tabs = self._tabs()
        if tab_id:
            target = next((t for t in tabs if t.get("id") == tab_id), None)
            if target is None:
                raise CDPError(f"No page tab with id {tab_id} (closed?)")
        elif new_tab:
            target = self._new_tab(create_url or "about:blank")
        else:
            matches = [t for t in tabs if prefer_substr in t.get("url", "")]
            target = None
            for t in matches:
                why = self._tab_busy(t) if skip_busy else ""
                if not why:
                    target = t
                    break
                print(f"[igweb] Leaving busy tab alone ({why}): {t.get('url', '')[:90]}")
            if target is None:
                target = next((t for t in tabs if t.get("url", "").startswith(
                    ("about:blank", "chrome://newtab", "chrome://new-tab-page"))), None)
            if target is None:
                target = self._new_tab(create_url or "about:blank")
            elif not skip_busy and len(matches) > 1:
                print(f"[igweb] WARNING: {len(matches)} instagram tabs open — pass --tab-id to pick one.")
        ws_url = target.get("webSocketDebuggerUrl")
        if not ws_url:
            raise CDPError(f"Tab has no webSocketDebuggerUrl: {target.get('url')}")
        print(f"[igweb] Attaching to tab: {target.get('url', '')[:90]}")
        print(f"TAB_ID: {target.get('id', '')}")
        self.ws = websocket.create_connection(
            ws_url, suppress_origin=True, max_size=None, enable_multithread=True)
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

    def evaluate(self, expr: str, user_gesture: bool = False):
        res = self.send("Runtime.evaluate", {
            "expression": expr, "returnByValue": True, "awaitPromise": True,
            "userGesture": user_gesture,
        })
        if res.get("exceptionDetails"):
            raise CDPError(f"JS exception: {res['exceptionDetails'].get('text')}")
        return res.get("result", {}).get("value")

    def ig(self, body: str, user_gesture: bool = True):
        """Run `body` (an expression using IG.*) with the helper library loaded."""
        return self.evaluate(_JS_LIB + body, user_gesture=user_gesture)

    def navigate(self, url: str, settle: float = 3.0):
        print(f"[igweb] Navigating to {url}")
        self.send("Page.navigate", {"url": url})
        time.sleep(settle)

    def current_url(self) -> str:
        return self.evaluate("location.href") or ""

    def set_files_js(self, js_element: str, files: list[str]):
        """DOM.setFileInputFiles on the element a JS expression returns."""
        res = self.send("Runtime.evaluate", {"expression": _JS_LIB + js_element})
        obj = (res.get("result") or {}).get("objectId")
        if not obj:
            raise CDPError(f"File input not found ({js_element})")
        self.send("DOM.enable")
        self.send("DOM.setFileInputFiles", {"objectId": obj, "files": files})

    def screenshot(self, path: str):
        res = self.send("Page.captureScreenshot", {"format": "png"}, timeout=60)
        with open(path, "wb") as f:
            f.write(base64.b64decode(res["data"]))
        print(f"[igweb] Screenshot saved: {path}")


# ---------------------------------------------------------------------------
# pre-browser checks
# ---------------------------------------------------------------------------

def utf16_len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def trim_hashtags(caption: str, max_n: int) -> tuple[str, list[str]]:
    """Keep the first max_n #hashtags, drop the rest (Instagram rejects posts over the limit)."""
    tags = list(HASHTAG_RE.finditer(caption))
    if max_n < 0 or len(tags) <= max_n:
        return caption, []
    drop = tags[max_n:]
    out = caption
    for m in reversed(drop):
        out = out[:m.start()] + out[m.end():]
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out, ["#" + m.group(1) for m in drop]


def probe_video(path: str) -> dict | None:
    """Display width/height (rotation applied) and duration via ffprobe;
    None when ffprobe is missing or cannot read the file."""
    exe = shutil.which("ffprobe")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height:stream_tags=rotate:stream_side_data=rotation:format=duration",
             "-of", "json", path], capture_output=True, text=True, timeout=30)
        info = json.loads(out.stdout or "{}")
        s = (info.get("streams") or [{}])[0]
        w, h = int(s.get("width") or 0), int(s.get("height") or 0)
        rot = (s.get("tags") or {}).get("rotate") or next(
            (sd.get("rotation") for sd in s.get("side_data_list") or [] if "rotation" in sd), 0)
        if int(float(rot or 0)) % 180:
            w, h = h, w
        dur = float((info.get("format") or {}).get("duration") or 0)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return {"width": w, "height": h, "duration": dur} if w and h and dur > 0 else None


def check_video(path: str) -> None:
    """Report size/length; landscape and over-3-minute only warn (still a Reel)."""
    m = probe_video(path)
    if m is None:
        print("[igweb] WARNING: could not read the video with ffprobe; size and length not checked.")
        return
    w, h, dur = m["width"], m["height"], m["duration"]
    print(f"VIDEO_CHECK: {w}x{h} {dur:.1f}s")
    if w > h:
        print("[igweb] WARNING: landscape video — it is still posted as a Reel, with bars above "
              "and below. Note it in the receipt.")
    if dur > REACH_S:
        print("[igweb] WARNING: over 3 minutes — Instagram rarely recommends such Reels to "
              "non-followers. Tell the user.")


def prepare_caption(path: str, max_tags: int) -> str | None:
    caption = open(path, encoding="utf-8").read().strip()
    if not caption:
        print("Error: empty caption.", file=sys.stderr)
        return None
    caption, dropped = trim_hashtags(caption, max_tags)
    if dropped:
        print(f"HASHTAGS_TRIMMED: kept {max_tags}, dropped {' '.join(dropped)}")
    n = utf16_len(caption)
    if n > CAPTION_MAX:
        print(f"Error: caption is {n} characters; Instagram allows {CAPTION_MAX}. Shorten it.",
              file=sys.stderr)
        return None
    return caption


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

_LOGGED_IN_JS = r"""
(function() {
  if (/\/accounts\/(login|onetap|suspended)|\/challenge\/|two_step/.test(location.pathname + location.search))
    return false;
  if (document.querySelector('input[name=username]')) return false;
  return !!document.querySelector('a[href^="/direct/"]');
})()
"""


def is_logged_in(cdp: CDP) -> bool:
    for _ in range(4):  # the sidebar renders a moment after load
        if cdp.evaluate(_LOGGED_IN_JS):
            return True
        time.sleep(1.5)
    return False


def cmd_check_login(cdp: CDP) -> int:
    cdp.navigate(HOME_URL, settle=5)
    if is_logged_in(cdp):
        print("[igweb] Login confirmed.")
        return 0
    print("[igweb] NOT LOGGED IN.\n"
          "  Please log in to instagram.com in the Chrome window (two-step\n"
          "  verification included), then run this script again.")
    return 1


def open_create_dialog(cdp: CDP) -> None:
    """Click the sidebar "New post" entry; ask the user if it cannot be found."""
    clicked = cdp.evaluate(r"""
        (function() {
          var labels = %s;
          var links = Array.from(document.querySelectorAll('a, [role=link], [role=button]'))
            .filter(function(a) { return a.getClientRects().length && a.querySelector('svg'); });
          function plus(a) {
            return Array.from(a.querySelectorAll('svg path')).some(function(p) {
              return /^M21 11h-8V3/.test(p.getAttribute('d') || '');
            });
          }
          function label(a) {
            var s = a.querySelector('svg[aria-label]');
            return s && labels.indexOf(s.getAttribute('aria-label')) !== -1;
          }
          var hit = links.find(plus) || links.find(label);
          if (!hit) return '';
          hit.click();
          return plus(hit) ? 'plus icon' : 'label';
        })()
    """ % json.dumps(list(NEW_POST_LABELS)), user_gesture=True)
    if clicked:
        print(f"[igweb] Opened New post (matched by {clicked}).")
        wait_s = 15
    else:
        print('[igweb] Could not find the "New post" entry. Please click "New post" (the + icon '
              f'in the left sidebar) in the Chrome window — waiting up to {MANUAL_ENTRY_WAIT}s...')
        wait_s = MANUAL_ENTRY_WAIT
    deadline = time.time() + wait_s
    while time.time() < deadline:
        if cdp.ig("!!(IG.createDialog() && IG.createDialog().querySelector('input[type=file][accept*=video]'))"):
            return
        time.sleep(1)
    raise CDPError("The New post dialog did not open (no video file input).")


def dismiss_notices(cdp: CDP) -> int:
    """Close info dialogs stacked over the create dialog (e.g. "Video posts are now
    shared as reels" -> OK) by clicking their last button."""
    n = 0
    for _ in range(3):
        hit = cdp.ig(r"""
            (function() {
              var main = IG.createDialog();
              var others = IG.dialogs().filter(function(d) {
                return d !== main && !d.contains(main) && !(main && main.contains(d))
                  && !d.querySelector('input, textarea, [contenteditable=true]');
              });
              if (!others.length) return '';
              var d = others[others.length - 1];
              var bs = IG.buttons(d).filter(function(b) { return IG.text(b); });
              if (!bs.length) return '';
              var b = bs[bs.length - 1];
              var t = IG.text(b);
              b.click();
              return (IG.text(d).split('\n')[0] || '').slice(0, 60) + ' -> ' + t;
            })()
        """)
        if not hit:
            break
        print(f"[igweb] Dismissed notice: {hit}")
        n += 1
        time.sleep(1)
    return n


def pick_original_crop(cdp: CDP) -> str:
    """Open the crop menu and choose the option that is not a W:H ratio ("Original")."""
    res = cdp.ig(r"""
        (async function() {
          var sleep = function(ms) { return new Promise(function(r) { setTimeout(r, ms); }); };
          var d = IG.createDialog();
          if (!IG.ratioButtons(d).length) {
            var t = IG.cropToggle(d);
            if (!t) return 'no crop toggle';
            t.click();
            await sleep(800);
          }
          var rs = IG.ratioButtons(d);
          if (rs.length < 2) return 'no crop menu';
          var box = rs[0].parentElement;
          while (box && !rs.every(function(r) { return box.contains(r); })) box = box.parentElement;
          var orig = box && IG.buttons(box).find(function(b) {
            var t = IG.text(b);
            return t && !/^\d+\s*[:：]\s*\d+$/.test(t) && rs.indexOf(b) === -1
              && !rs.some(function(r) { return b.contains(r) || r.contains(b); });
          });
          if (!orig) return 'no original option';
          orig.click();
          await sleep(800);
          return 'ok:' + IG.text(orig);
        })()
    """)
    return res or ""


def click_next(cdp: CDP) -> str:
    """Click Next in the dialog header — refuses on the caption step (that button is Share)."""
    return cdp.ig(r"""
        (function() {
          var d = IG.createDialog();
          if (!d || IG.editor(d)) return '';
          var b = IG.headerButton(d);
          if (!b) return '';
          b.click();
          return IG.text(b);
        })()
    """) or ""


def walk_to_caption(cdp: CDP, cover: str | None) -> None:
    """Crop (Original) -> Next -> cover -> Next, until the caption editor shows."""
    cropped, covered, last = False, False, ""
    deadline = time.time() + STEP_TIMEOUT
    while time.time() < deadline:
        dismiss_notices(cdp)
        step = cdp.ig("IG.step()")
        if step != last:
            print(f"[igweb] Dialog step: {step}")
            last = step
        if step == "caption":
            return
        if step == "crop" and not cropped:
            res = pick_original_crop(cdp)
            if res.startswith("ok:"):
                print(f"[igweb] Crop set to {res[3:]} (keeps the video's own aspect).")
            else:
                print(f"[igweb] WARNING: could not set crop to Original ({res}) — "
                      "check the crop in the browser before sharing.")
            cropped = True
            time.sleep(0.5)
            print(f"[igweb] Next ({click_next(cdp) or 'not found'}).")
            time.sleep(2)
            continue
        if step == "edit":
            if cover and not covered:
                try:
                    cdp.set_files_js("IG.coverInput(IG.createDialog())", [cover])
                    print(f"[igweb] Cover image submitted: {cover}")
                    time.sleep(3)
                except CDPError as e:
                    print(f"[igweb] WARNING: cover not set ({e}); pick it in the browser.")
            covered = True
            print(f"[igweb] Next ({click_next(cdp) or 'not found'}).")
            time.sleep(2)
            continue
        if step == "crop" and cropped:  # Next did not take yet
            click_next(cdp)
        time.sleep(1.5)
    raise CDPError(f"Did not reach the caption step within {STEP_TIMEOUT}s (stuck at '{last}').")


def fill_caption(cdp: CDP, caption: str) -> bool:
    """Lexical editor: JS selectAll + CDP Input.insertText, verify, refill once; then
    blur so the #hashtag suggestion list closes without picking anything."""
    for attempt in (1, 2):
        ok = cdp.ig("""(function() {
            var e = IG.editor(IG.createDialog());
            if (!e) return false;
            e.focus();
            document.execCommand('selectAll', false, null);
            return true;
        })()""")
        if not ok:
            raise CDPError("Caption editor not found.")
        time.sleep(0.4)
        cdp.send("Input.insertText", {"text": caption})
        time.sleep(1.5)
        got = cdp.ig("(function() { var e = IG.editor(IG.createDialog()); return e ? e.innerText : ''; })()") or ""
        if "".join(got.split()) == "".join(caption.split()):
            cdp.evaluate("document.activeElement && document.activeElement.blur()")
            time.sleep(0.8)
            print("[igweb] Caption set.")
            return True
        print(f"[igweb] Caption mismatch (attempt {attempt}) — refilling...")
    cdp.evaluate("document.activeElement && document.activeElement.blur()")
    print("[igweb] WARNING: caption text still differs from the source — review it in the browser.")
    return False


def set_ai_label(cdp: CDP) -> str:
    """Turn on the "Add AI label" switch (found by the AI wording next to it)."""
    return cdp.ig(r"""
        (async function() {
          var d = IG.createDialog();
          var re = /(^|[^A-Za-z])(AI|IA|KI)([^A-Za-z]|$)|ИИ|الذكاء|인공지능|人工智能|人工知能/;
          var sw = Array.from(d.querySelectorAll('input[role=switch], [role=switch]')).find(function(s) {
            var a = s;
            for (var i = 0; i < 8 && a; i++) {
              a = a.parentElement;
              var t = a && (a.innerText || '').trim();
              if (t) return re.test(t.split('\n')[0]);
            }
            return false;
          });
          if (!sw) return 'not found';
          var on = function() { return sw.checked === true || sw.getAttribute('aria-checked') === 'true'; };
          if (on()) return 'already on';
          sw.click();
          await new Promise(function(r) { setTimeout(r, 800); });
          return on() ? 'on' : 'click did not stick';
        })()
    """) or ""


def tab_has_post_dialog(cdp: CDP) -> bool:
    """Checked BEFORE navigating: leaving the page discards an unshared reel without asking."""
    try:
        return cdp.ig("IG.step()", user_gesture=False) != "none"
    except CDPError:
        return False


def cmd_check_login_guarded(cdp: CDP) -> int:
    if tab_has_post_dialog(cdp):
        print("[igweb] This tab has a post dialog open — not navigating it (that would discard "
              "the reel). Rerun with --new-tab.", file=sys.stderr)
        return 1
    return cmd_check_login(cdp)


def cmd_upload(cdp: CDP, args, caption: str) -> int:
    if tab_has_post_dialog(cdp):
        print("[igweb] This tab already has a post dialog open — rerun with --new-tab "
              "(the web has no drafts; navigating away would discard that reel).", file=sys.stderr)
        return 1
    cdp.navigate(HOME_URL, settle=5)
    if not is_logged_in(cdp):
        print("[igweb] NOT LOGGED IN — run check-login first.", file=sys.stderr)
        return 1

    open_create_dialog(cdp)
    print(f"[igweb] Uploading video: {args.video}")
    cdp.set_files_js("IG.createDialog().querySelector('input[type=file][accept*=video]')", [args.video])
    time.sleep(3)

    walk_to_caption(cdp, args.cover)
    fill_caption(cdp, caption)
    if args.ai_label:
        res = set_ai_label(cdp)
        print(f"[igweb] AI label: {res}.")
        if res not in ("on", "already on"):
            print("[igweb] WARNING: turn on \"Add AI label\" by hand before sharing.")

    print("FILL_STATUS: READY_FOR_HUMAN_SUBMIT")
    print("[igweb] Caption filled. The user must review and click Share. The video only "
          "starts uploading after Share — keep this tab open until Instagram says the reel "
          "was shared.")
    return 0


def main(argv: list[str] | None = None) -> int:
    new_tab_help = "Open a fresh tab (one tab per reel: the web keeps no drafts)"
    tab_id_help = "Attach to exactly this tab (id printed as TAB_ID by an earlier run)"
    parser = argparse.ArgumentParser(description="Instagram web Reel draft filler (CDP)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9222)
    parser.add_argument("--new-tab", action="store_true", help=new_tab_help)
    parser.add_argument("--tab-id", help=tab_id_help)
    tab_opts = argparse.ArgumentParser(add_help=False)
    tab_opts.add_argument("--new-tab", action="store_true", default=argparse.SUPPRESS, help=new_tab_help)
    tab_opts.add_argument("--tab-id", default=argparse.SUPPRESS, help=tab_id_help)
    tab_id_opt = argparse.ArgumentParser(add_help=False)
    tab_id_opt.add_argument("--tab-id", default=argparse.SUPPRESS, help=tab_id_help)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check-login", parents=[tab_opts])

    up = sub.add_parser("upload", parents=[tab_opts])
    up.add_argument("--video", required=True, help="Absolute path to the video file (MP4/MOV)")
    up.add_argument("--caption-file", required=True,
                    help="Caption text: title line + description + #hashtags (<= 2,200 characters)")
    up.add_argument("--cover", help="Optional cover image (JPG/PNG, 9:16)")
    up.add_argument("--ai-label", action="store_true",
                    help='Turn on "Add AI label" (realistic AI-generated video)')
    up.add_argument("--max-hashtags", type=int, default=HASHTAG_MAX,
                    help=f"Keep at most this many #hashtags (default {HASHTAG_MAX}, Instagram's limit)")

    shot = sub.add_parser("screenshot", parents=[tab_id_opt])
    shot.add_argument("output", help="PNG output path")

    args = parser.parse_args(argv)
    if args.cmd == "screenshot" and args.new_tab:
        parser.error("screenshot captures an existing tab; use --tab-id, not --new-tab")
    if args.new_tab and args.tab_id:
        parser.error("--new-tab and --tab-id are mutually exclusive")

    caption = None
    if args.cmd == "upload":
        caption = prepare_caption(args.caption_file, args.max_hashtags)
        if caption is None:
            return 2
        if args.cover and not args.cover.lower().endswith(COVER_EXTS):
            print(f"[igweb] WARNING: cover must be JPG or PNG; skipping {args.cover}.")
            args.cover = None
        check_video(args.video)

    cdp = CDP(args.host, args.port)
    try:
        # check-login / upload navigate the tab: never reuse one whose post
        # dialog still waits for Share. screenshot wants exactly such a tab.
        cdp.attach("instagram.com", create_url=HOME_URL, new_tab=args.new_tab,
                   tab_id=args.tab_id, skip_busy=args.cmd != "screenshot")
        if args.cmd == "check-login":
            return cmd_check_login_guarded(cdp)
        if args.cmd == "upload":
            return cmd_upload(cdp, args, caption)
        if args.cmd == "screenshot":
            cdp.screenshot(args.output)
            return 0
        return 2
    finally:
        cdp.close()


if __name__ == "__main__":
    sys.exit(main())
