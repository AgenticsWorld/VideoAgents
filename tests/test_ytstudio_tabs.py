"""ytstudio_draft.py tab selection (#129): --new-tab / --tab-id and skipping
studio tabs that still hold an earlier episode's upload dialog.

Pure unit tests — requests / websocket are replaced by in-memory fakes that
answer the CDP calls, no browser involved."""
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

SCRIPT = (Path(__file__).resolve().parents[1] / "agents/12-publishing/publisher/skills"
          / "skill-youtube-cdp-draft/scripts/ytstudio_draft.py")


class FakeResp:
    def __init__(self, data=None, status=200):
        self._data, self.status_code = data, status

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class World:
    """Chrome stand-in: page tabs + per-tab upload dialog state."""

    def __init__(self, tabs, dialog=None):
        self.tabs = tabs
        self.dialog = dialog or {}  # ws_url -> True | False | "hang"
        self.http_calls = []
        self.probed = []
        self.connected = []


def make_modules(world):
    req = types.ModuleType("requests")

    class RequestException(Exception):
        pass

    class Session:
        trust_env = True

        def get(self, url, timeout=None):
            world.http_calls.append(("GET", url))
            if url.endswith("/json/list"):
                return FakeResp(world.tabs)
            return FakeResp({})

        def put(self, url, timeout=None):
            world.http_calls.append(("PUT", url))
            if "/json/new?" in url:
                tab = {"id": "NEW1", "type": "page", "url": url.split("?", 1)[1],
                       "webSocketDebuggerUrl": "ws://new1"}
                world.tabs.append(tab)
                return FakeResp(tab)
            return FakeResp({}, 404)

    req.Session, req.RequestException = Session, RequestException

    ws_mod = types.ModuleType("websocket")

    class WebSocketException(Exception):
        pass

    class WebSocketTimeoutException(WebSocketException):
        pass

    class FakeWS:
        def __init__(self, url, probe):
            self.url, self.probe, self.pending = url, probe, []

        def send(self, raw):
            msg = json.loads(raw)
            if self.probe:
                world.probed.append(self.url)
            state = world.dialog.get(self.url, False)
            if state == "hang":
                return
            result = {}
            if msg["method"] == "Runtime.evaluate" and self.probe:
                result = {"result": {"value": bool(state)}}
            self.pending.append({"id": msg["id"], "result": result})

        def recv(self):
            if not self.pending:
                raise WebSocketTimeoutException()
            return json.dumps(self.pending.pop(0))

        def settimeout(self, t):
            pass

        def close(self):
            pass

    def create_connection(url, **kw):
        probe = "timeout" in kw  # the probe opens a short-lived connection
        if not probe:
            world.connected.append(url)
        return FakeWS(url, probe)

    ws_mod.WebSocketException = WebSocketException
    ws_mod.WebSocketTimeoutException = WebSocketTimeoutException
    ws_mod.create_connection = create_connection
    return req, ws_mod


@pytest.fixture
def load(monkeypatch):
    def _load(world):
        req, ws_mod = make_modules(world)
        monkeypatch.setitem(sys.modules, "requests", req)
        monkeypatch.setitem(sys.modules, "websocket", ws_mod)
        spec = importlib.util.spec_from_file_location("ytstudio_draft_t", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        monkeypatch.setattr(mod, "TAB_PROBE_TIMEOUT", 0.2)
        return mod
    return _load


def studio(i, url=None):
    return {"id": f"S{i}", "type": "page",
            "url": url or f"https://studio.youtube.com/channel/UC{i}/videos",
            "webSocketDebuggerUrl": f"ws://s{i}"}


BLANK = {"id": "B1", "type": "page", "url": "about:blank", "webSocketDebuggerUrl": "ws://b1"}


def attach(mod, **kw):
    cdp = mod.CDP()
    cdp.attach("studio.youtube.com", create_url=mod.STUDIO_URL, **kw)
    return cdp


def test_new_tab_goes_through_json_new(load, capsys):
    w = World([studio(1)])
    mod = load(w)
    attach(mod, new_tab=True, skip_busy=True)
    assert ("PUT", "http://127.0.0.1:9222/json/new?https://studio.youtube.com/") in w.http_calls
    assert ("GET", "http://127.0.0.1:9222/json/activate/NEW1") in w.http_calls
    assert w.connected == ["ws://new1"] and w.probed == []
    assert "TAB_ID: NEW1" in capsys.readouterr().out


def test_open_upload_dialog_triggers_new_tab(load, capsys):
    w = World([studio(1)], dialog={"ws://s1": True})
    mod = load(w)
    attach(mod, skip_busy=True)
    assert w.probed == ["ws://s1"]
    assert w.connected == ["ws://new1"]
    out = capsys.readouterr().out
    assert "Leaving busy tab alone (upload dialog open)" in out and "TAB_ID: NEW1" in out


def test_upload_url_counts_as_busy_without_probe(load):
    w = World([studio(1, "https://studio.youtube.com/channel/UC1/videos/upload?d=ud")])
    mod = load(w)
    attach(mod, skip_busy=True)
    assert w.probed == [] and w.connected == ["ws://new1"]


def test_unresponsive_tab_is_skipped(load, capsys):
    w = World([studio(1)], dialog={"ws://s1": "hang"})
    mod = load(w)
    attach(mod, skip_busy=True)
    assert w.connected == ["ws://new1"]
    assert "tab not responding" in capsys.readouterr().out


def test_free_studio_tab_is_reused(load, capsys):
    w = World([studio(1)], dialog={"ws://s1": False})
    mod = load(w)
    attach(mod, skip_busy=True)
    assert w.connected == ["ws://s1"]
    assert not any(m == "PUT" for m, _ in w.http_calls)
    assert "TAB_ID: S1" in capsys.readouterr().out


def test_busy_first_free_second_reuses_second(load):
    w = World([studio(1), studio(2)], dialog={"ws://s1": True, "ws://s2": False})
    mod = load(w)
    attach(mod, skip_busy=True)
    assert w.connected == ["ws://s2"]


def test_busy_tab_falls_back_to_blank_tab(load):
    w = World([studio(1), dict(BLANK)], dialog={"ws://s1": True})
    mod = load(w)
    attach(mod, skip_busy=True)
    assert w.connected == ["ws://b1"]


def test_screenshot_mode_keeps_tab_with_dialog(load):
    w = World([studio(1)], dialog={"ws://s1": True})
    mod = load(w)
    attach(mod)  # skip_busy=False
    assert w.probed == [] and w.connected == ["ws://s1"]


def test_tab_id_exact_and_unknown(load):
    w = World([studio(1), studio(2)])
    mod = load(w)
    attach(mod, tab_id="S2")
    assert w.connected == ["ws://s2"]
    with pytest.raises(mod.CDPError):
        attach(mod, tab_id="GONE")


class _RecCDP:
    calls = []

    def __init__(self, host, port):
        pass

    def attach(self, prefer, create_url=None, **kw):
        _RecCDP.calls.append(kw)

    def screenshot(self, path):
        pass

    def close(self):
        pass


@pytest.mark.parametrize("argv,expect", [
    (["check-login", "--new-tab"], {"new_tab": True, "tab_id": None, "skip_busy": True}),
    (["--new-tab", "check-login"], {"new_tab": True, "tab_id": None, "skip_busy": True}),
    (["check-login"], {"new_tab": False, "tab_id": None, "skip_busy": True}),
    (["screenshot", "x.png", "--tab-id", "S2"], {"new_tab": False, "tab_id": "S2", "skip_busy": False}),
    (["--tab-id", "S2", "screenshot", "x.png"], {"new_tab": False, "tab_id": "S2", "skip_busy": False}),
])
def test_cli_tab_options(load, monkeypatch, argv, expect):
    mod = load(World([]))
    _RecCDP.calls = []
    monkeypatch.setattr(mod, "CDP", _RecCDP)
    monkeypatch.setattr(mod, "cmd_check_login", lambda cdp: 0)
    assert mod.main(argv) == 0
    assert _RecCDP.calls == [expect]


@pytest.mark.parametrize("argv", [
    ["--new-tab", "screenshot", "x.png"],
    ["check-login", "--new-tab", "--tab-id", "S1"],
])
def test_cli_rejects_conflicting_tab_options(load, monkeypatch, argv):
    mod = load(World([]))
    monkeypatch.setattr(mod, "CDP", _RecCDP)
    with pytest.raises(SystemExit):
        mod.main(argv)
