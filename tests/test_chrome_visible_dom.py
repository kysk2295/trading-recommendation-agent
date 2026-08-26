from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trading_agent.chrome_devtools_client import ChromeDevToolsClient
from trading_agent.chrome_devtools_transport import LoopbackChromeDevToolsTransport, LoopbackChromeHealthProbe
from trading_agent.chrome_devtools_types import CdpCommand, CdpMethod, InvalidChromeDevToolsError
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
                {"label": "sensitive", "url": "https://example.net/?auth%2Dtoken=withheld"},
            ],
        }
    )
    # When: Python parses the browser boundary.
    observation = parse_visible_page("target-1", payload, _NOW)
    # Then: it keeps only the revalidated public HTTPS candidate.
    assert observation.visible_text == "visible"
    assert tuple(link.url for link in observation.links) == ("https://example.org/article",)


def test_visible_page_parser_rejects_sensitive_page_query() -> None:
    # Given: Chrome reports a page URL whose decoded query holds credential metadata.
    payload = json.dumps(
        {"title": "Story", "url": "https://example.com/?q=api_key%3Dwithheld", "text": "visible", "links": []}
    )
    # When: the page observation crosses the Python boundary.
    with pytest.raises(InvalidChromeDevToolsError) as error:
        parse_visible_page("target-1", payload, _NOW)
    # Then: no page observation can carry the sensitive URL into receipts.
    assert error.value.reason == "browser_navigation_blocked"


def test_visible_page_parser_redacts_credentials_and_account_identifiers() -> None:
    # Given: every Chrome-derived prose field contains a sensitive sentinel and ordinary public prose.
    sentinel = "Bearer SECRET-TOKEN account_id=12345 @victim"
    payload = json.dumps(
        {
            "title": f"Market update {sentinel}",
            "url": "https://example.com/story",
            "text": f"Public author @analyst says chips rallied. {sentinel}",
            "links": [{"label": f"Public filing {sentinel}", "url": "https://example.org/article"}],
        }
    )
    # When: Python parses the untrusted browser observation.
    observation = parse_visible_page("target-1", payload, _NOW)
    projected = " ".join((observation.title, observation.visible_text, *(link.label for link in observation.links)))
    # Then: secret/token/account values are absent while public prose and handles remain usable.
    assert all(value not in projected for value in ("SECRET-TOKEN", "12345"))
    assert "@victim" in projected
    assert "Public author @analyst says chips rallied." in projected


@pytest.mark.parametrize(
    ("sensitive", "forbidden"),
    [
        ("Authorization: Basic basic-canary", "basic-canary"),
        ("ACCESS-TOKEN=access-canary", "access-canary"),
        ("Refresh_Token: refresh-canary", "refresh-canary"),
        ("API Key = api-canary", "api-canary"),
        ("sessionId=session-canary", "session-canary"),
        ("Cookie: cookie-canary", "cookie-canary"),
        ("PASSWORD = password-canary", "password-canary"),
        ("clientSecret: client-canary", "client-canary"),
        ("Account-Number=67890", "67890"),
        (
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJjYW5hcnkifQ.signaturecanary",
            "eyJhbGciOiJIUzI1NiJ9",
        ),
    ],
)
def test_visible_page_parser_redacts_supported_sensitive_variants(sensitive: str, forbidden: str) -> None:
    # Given: one labelled or JWT-shaped canary follows ordinary market prose.
    payload = json.dumps(
        {
            "title": "Public market title",
            "url": "https://example.com/story",
            "text": f"Semiconductors gained. {sensitive} @public_author",
            "links": [],
        }
    )
    # When: the visible page crosses the Python observation boundary.
    observation = parse_visible_page("target-1", payload, _NOW)
    # Then: the sensitive value is removed without broadly filtering prose or handles.
    assert forbidden not in observation.visible_text
    assert "Semiconductors gained." in observation.visible_text
    assert "@public_author" in observation.visible_text


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
          <main><p>visible-story Bearer SECRET-TOKEN account_id=12345 @victim</p>
            <a href="https://example.org/visible">visible-link</a></main>
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
        assert "SECRET-TOKEN" not in observation.visible_text
        assert "12345" not in observation.visible_text
        assert "@victim" in observation.visible_text
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
