from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trading_agent.chrome_devtools_client import ChromeDevToolsClient
from trading_agent.chrome_devtools_transport import LoopbackChromeDevToolsTransport, LoopbackChromeHealthProbe
from trading_agent.chrome_devtools_types import CdpCommand, CdpMethod
from trading_agent.chrome_visible_dom import parse_visible_page
from trading_agent.local_browser_gateway_config import LocalBrowserGatewayConfig
from trading_agent.local_chrome_controller import LocalChromeController

_CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
_NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


def test_visible_page_parser_revalidates_and_bounds_candidates() -> None:
    # Given: a bounded DOM payload with public and rejected link candidates.
    payload = json.dumps(
        {
            "title": "Story",
            "url": "https://example.com/story",
            "text": "visible",
            "links": [
                {"label": "public", "url": "https://example.org/article"},
                {"label": "local", "url": "https://127.0.0.1/private"},
            ],
        }
    )
    # When: Python parses the browser boundary.
    observation = parse_visible_page("target-1", payload, _NOW)
    # Then: it keeps only the revalidated public HTTPS candidate.
    assert observation.visible_text == "visible"
    assert tuple(link.url for link in observation.links) == ("https://example.org/article",)


@pytest.mark.skipif(not _CHROME.is_file(), reason="installed Chrome integration requires macOS Chrome")
def test_real_chrome_excludes_opacity_hidden_text_and_offscreen_link(tmp_path: Path) -> None:
    # Given: the production client controls an isolated installed Chrome target.
    config = _config(tmp_path)
    controller = LocalChromeController(
        config,
        probe=LoopbackChromeHealthProbe(timeout_seconds=2.0),
    )
    try:
        endpoint = controller.ensure_ready()
        transport = LoopbackChromeDevToolsTransport(endpoint.port, timeout_seconds=10.0)
        client = ChromeDevToolsClient(transport, command_timeout_seconds=10.0)
        opened = client.open("https://example.com", captured_at=_NOW)
        fixture = """
        document.body.innerHTML = `
          <main><p>visible-story</p><a href="https://example.org/visible">visible-link</a></main>
          <section style="opacity:0"><p>opacity-secret</p><a href="https://example.org/opacity">opacity-link</a></section>
          <a style="position:absolute;left:-5000px;top:0" href="https://example.org/offscreen">offscreen-link</a>
          <script>window.hiddenSecret = 'script-secret'</script>`;
        document.title = 'Visibility fixture';
        """
        params = json.dumps(
            {"expression": fixture, "returnByValue": True, "awaitPromise": False},
            separators=(",", ":"),
        )
        _ = transport.command(opened.target_id, CdpCommand(CdpMethod.RUNTIME_EVALUATE, params))
        # When: production visible-DOM extraction reads the actual rendered page.
        observation = client.read(opened.target_id, captured_at=_NOW)
        # Then: hidden and offscreen content cannot enter text or link evidence.
        assert "visible-story" in observation.visible_text
        assert "opacity-secret" not in observation.visible_text
        assert "script-secret" not in observation.visible_text
        assert tuple(link.url for link in observation.links) == ("https://example.org/visible",)
    finally:
        controller.close()


def _config(tmp_path: Path) -> LocalBrowserGatewayConfig:
    private = (tmp_path / "browser-dom").absolute()
    state = private / "state"
    return LocalBrowserGatewayConfig(
        project_root=Path.cwd(),
        uv_path=Path(shutil.which("uv") or "/usr/local/bin/uv"),
        chrome_executable=_CHROME,
        state_root=state,
        profile_root=private / "profile",
        socket_path=state / "browser.sock",
        receipt_database=state / "receipts.sqlite3",
        screenshot_root=state / "screenshots",
        startup_timeout_seconds=15.0,
        command_timeout_seconds=10.0,
    )
