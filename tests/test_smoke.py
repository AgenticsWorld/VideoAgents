"""CI smoke tests: package imports and release version consistency.

The full regression suite in this directory lives outside version control
(local-only since v1.0.4); this file is the one tracked exception — a minimal
always-versioned check that keeps CI meaningful without shipping tests that
depend on private local data.
"""

import asyncio
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_api_package_imports():
    from services.api import __version__, app

    assert __version__
    assert app.app.title


def test_web_server_imports():
    import apps.web.server  # noqa: F401


def test_release_versions_consistent():
    from services.api import __version__

    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE)
    assert match and match.group(1) == __version__

    root_pkg = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert root_pkg["version"] == __version__

    desktop_pkg = json.loads((ROOT / "apps/desktop/package.json").read_text(encoding="utf-8"))
    assert desktop_pkg["version"] == __version__

    citation = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    match = re.search(r"^version:\s*(\S+)", citation, re.MULTILINE)
    assert match and match.group(1) == __version__


def test_web_streaming_deltas_are_coalesced():
    html = (ROOT / "apps/web/static/index.html").read_text(encoding="utf-8")
    assert "function appendLiveText(container,text)" in html
    assert "appendLiveText(d,ev.text)" in html
    assert "t.className='livetext';t.textContent=ev.text" not in html
    assert ".run .eng{" in html and "text-overflow:ellipsis" in html
    assert '<span class="eng" title="${esc(engLabel)}">' in html


def test_api_event_stream_stops_on_shutdown():
    from services.api import app as api_app

    async def probe():
        api_app._shutdown_event = asyncio.Event()
        response = await api_app.events()
        stream = response.body_iterator
        assert "hello" in await anext(stream)
        waiting = asyncio.create_task(anext(stream))
        api_app.request_shutdown()
        try:
            await asyncio.wait_for(waiting, timeout=1)
        except StopAsyncIteration:
            pass
        else:
            raise AssertionError("SSE stream did not close during shutdown")
        finally:
            api_app._shutdown_event = None

    asyncio.run(probe())
