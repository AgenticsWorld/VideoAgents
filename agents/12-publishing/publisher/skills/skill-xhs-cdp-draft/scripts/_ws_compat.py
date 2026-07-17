"""CDP WebSocket compatibility shim.

The `websockets` (v16) sync client fails to handshake against the bundled
Chromium 136 CDP endpoint (Chrome closes the socket with no HTTP response).
The mature `websocket-client` library connects fine when origin is suppressed.

This shim exposes a `connect()` that returns an object compatible with the
subset of the `websockets.sync` API used by cdp_publish.py:
`.send(str)`, `.recv(timeout=...)` (raising TimeoutError on timeout), `.close()`.
It transparently prefers `websocket-client`, falling back to `websockets`.
"""

from __future__ import annotations

try:
    import websocket as _wsc  # websocket-client
    _HAS_WSCLIENT = True
except Exception:  # pragma: no cover
    _HAS_WSCLIENT = False


class _WebSocketClientConnection:
    """Adapter over websocket-client matching the websockets.sync surface."""

    def __init__(self, ws_url: str):
        # suppress_origin avoids the Origin header that Chrome 136 rejects;
        # enable_multithread lets send/recv be called from helper threads.
        self._ws = _wsc.create_connection(
            ws_url,
            suppress_origin=True,
            enable_multithread=True,
            max_size=None,
        )

    def send(self, data):
        self._ws.send(data)

    def recv(self, timeout=None):
        if timeout is not None:
            self._ws.settimeout(timeout)
        try:
            return self._ws.recv()
        except _wsc.WebSocketTimeoutException as exc:
            # cdp_publish expects builtin TimeoutError on read timeout.
            raise TimeoutError(str(exc)) from exc

    def close(self):
        try:
            self._ws.close()
        except Exception:
            pass


def connect(ws_url: str, *args, **kwargs):
    """Connect to a CDP WebSocket, preferring websocket-client."""
    if _HAS_WSCLIENT:
        return _WebSocketClientConnection(ws_url)
    import websockets.sync.client as _ws_sync
    return _ws_sync.connect(ws_url, *args, **kwargs)
