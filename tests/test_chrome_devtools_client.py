from __future__ import annotations

import base64
import hashlib
import json
import struct
import zlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trading_agent.chrome_devtools_client import (
    CdpCommand,
    CdpMethod,
    ChromeDevToolsClient,
    ChromeDevToolsStatus,
    ChromeTarget,
    InvalidChromeDevToolsError,
)
from trading_agent.local_browser_protocol import InvalidLocalBrowserProtocolError

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


def _target() -> ChromeTarget:
    return ChromeTarget(
        target_id="target-1",
        url="about:blank",
        title="",
        websocket_url="ws://127.0.0.1:9222/devtools/page/target-1",
    )


@dataclass(slots=True)
class FixtureCdpTransport:
    """Mutable fixture replays boundary-realistic CDP response bytes."""

    responses: list[bytes]
    commands: list[tuple[str, CdpCommand]] = field(default_factory=list)
    guarded_navigations: list[tuple[str, str]] = field(default_factory=list)
    target: ChromeTarget = field(default_factory=_target)
    created: int = 0

    def status(self) -> ChromeDevToolsStatus:
        return ChromeDevToolsStatus(ready=True, active_page_count=1)

    def create_target(self) -> ChromeTarget:
        self.created += 1
        return self.target

    def command(
        self,
        target_id: str,
        command: CdpCommand,
        *,
        timeout_seconds: float | None = None,
    ) -> bytes:
        _ = timeout_seconds
        self.commands.append((target_id, command))
        return self.responses.pop(0)

    def navigate_guarded(
        self,
        target_id: str,
        url: str,
        *,
        timeout_seconds: float | None = None,
    ) -> bytes:
        _ = timeout_seconds
        self.guarded_navigations.append((target_id, url))
        return self.responses.pop(0)


def _response(result: str) -> bytes:
    return json.dumps(
        {"id": 1, "result": {"result": {"type": "string", "value": result}}},
        separators=(",", ":"),
    ).encode()


def _navigation_response() -> bytes:
    return b'{"id":1,"result":{"frameId":"frame-1"}}'


def _png(width: int = 2, height: int = 3) -> bytes:
    signature = b"\x89PNG\r\n\x1a\n"
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    chunk = b"IHDR" + header
    return signature + struct.pack(">I", len(header)) + chunk + struct.pack(">I", zlib.crc32(chunk))


def test_read_returns_only_bounded_visible_text_and_public_https_links() -> None:
    # Given: Chrome's constant DOM extractor returns long text and mixed links.
    links = [{"label": f" visible {index} ", "url": f"https://example.com/{index}"} for index in range(45)] + [
        {"label": "local", "url": "http://127.0.0.1/private"}
    ]
    page = json.dumps({"title": "Story", "url": "https://example.com/story", "text": "x" * 13_000, "links": links})
    transport = FixtureCdpTransport([_response(page)])
    # When: the current page is read.
    observation = ChromeDevToolsClient(transport).read("target-1", captured_at=NOW)
    # Then: only deterministic protocol-sized public observations escape.
    assert len(observation.visible_text) == 12_000
    assert len(observation.links) == 40
    assert all(link.url.startswith("https://example.com/") for link in observation.links)
    assert transport.commands[0][1].method is CdpMethod.RUNTIME_EVALUATE


def test_open_revalidates_url_and_waits_for_ready_state() -> None:
    # Given: a fresh target that becomes interactive and reports its final location.
    page = json.dumps({"title": "Final", "url": "https://example.com/final", "text": "not-open-output", "links": []})
    transport = FixtureCdpTransport([_navigation_response(), _response("interactive"), _response(page)])
    # When: an allowed URL is opened.
    observation = ChromeDevToolsClient(transport).open("https://EXAMPLE.com/start#fragment", captured_at=NOW)
    # Then: navigation uses the normalized URL and returns bounded final metadata.
    assert transport.guarded_navigations == [("target-1", "https://example.com/start")]
    assert observation.target_id == "target-1"
    assert (observation.url, observation.title, observation.visible_text) == (
        "https://example.com/final",
        "Final",
        "",
    )


def test_open_rejects_private_url_before_creating_target() -> None:
    # Given: a transport that records browser mutations.
    transport = FixtureCdpTransport([])
    # When: a loopback URL reaches the client boundary.
    with pytest.raises(InvalidLocalBrowserProtocolError):
        _ = ChromeDevToolsClient(transport).open("https://127.0.0.1/private", captured_at=NOW)
    # Then: URL policy rejects it before Chrome is touched.
    assert transport.created == 0


def test_search_url_encodes_query_before_navigation() -> None:
    # Given: a target with an immediately complete Google result page.
    page = json.dumps({"title": "Results", "url": "https://www.google.com/search?q=AI+chips", "text": "", "links": []})
    transport = FixtureCdpTransport([_navigation_response(), _response("complete"), _response(page)])
    # When: the agent searches with spaces.
    _ = ChromeDevToolsClient(transport).search("AI chips", captured_at=NOW)
    # Then: only an HTTPS Google URL reaches Page.navigate.
    assert transport.guarded_navigations == [("target-1", "https://www.google.com/search?q=AI+chips")]


def test_read_fails_honestly_when_visible_text_is_empty() -> None:
    # Given: a visual-only page with no DOM text.
    page = json.dumps({"title": "Challenge", "url": "https://example.com/blocked", "text": "  ", "links": []})
    # When: the current page is read.
    with pytest.raises(InvalidChromeDevToolsError) as raised:
        _ = ChromeDevToolsClient(FixtureCdpTransport([_response(page)])).read("target-1", captured_at=NOW)
    # Then: it reports a stable blocked observation instead of inventing content.
    assert raised.value.reason == "browser_visible_text_unavailable"


def test_follow_navigates_same_target_to_selected_revalidated_link() -> None:
    # Given: a readable page with one public visible link.
    current = json.dumps(
        {
            "title": "Index",
            "url": "https://example.com/index",
            "text": "",
            "links": [{"label": "Story", "url": "https://example.org/story"}],
        }
    )
    final = json.dumps({"title": "Story", "url": "https://example.org/story", "text": "hidden-until-read", "links": []})
    transport = FixtureCdpTransport(
        [_response(current), _navigation_response(), _response("complete"), _response(final)]
    )
    # When: the first bounded link is followed.
    observation = ChromeDevToolsClient(transport).follow("target-1", 0, captured_at=NOW)
    # Then: Page.navigate stays on the same target and returns metadata only.
    assert transport.guarded_navigations == [("target-1", "https://example.org/story")]
    assert (observation.url, observation.visible_text, observation.links) == (
        "https://example.org/story",
        "",
        (),
    )


def test_read_skips_oversized_https_link_without_losing_valid_links() -> None:
    # Given: one oversized HTTPS href precedes an otherwise valid visible link.
    page = json.dumps(
        {
            "title": "Index",
            "url": "https://example.com/index",
            "text": "stories",
            "links": [
                {"label": "oversized", "url": "https://example.com/" + "x" * 2_100},
                {"label": "valid", "url": "https://example.org/story"},
            ],
        }
    )
    # When: the bounded page is read.
    observation = ChromeDevToolsClient(FixtureCdpTransport([_response(page)])).read("target-1", captured_at=NOW)
    # Then: the malformed candidate is excluded without discarding the page.
    assert tuple(link.url for link in observation.links) == ("https://example.org/story",)


def test_capture_writes_private_png_and_returns_digest_not_bytes(tmp_path: Path) -> None:
    # Given: Chrome returns a small valid PNG screenshot.
    png = _png()
    payload = base64.b64encode(png).decode()
    response = json.dumps({"id": 1, "result": {"data": payload}}, separators=(",", ":")).encode()
    root = tmp_path / "screenshots"
    # When: the screenshot is captured.
    receipt = ChromeDevToolsClient(FixtureCdpTransport([response])).capture("target-1", root, captured_at=NOW)
    # Then: only immutable private metadata is returned.
    path = Path(receipt.path)
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert receipt.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert (receipt.width, receipt.height, receipt.captured_at) == (2, 3, NOW)


def test_capture_rejects_png_over_eight_mib_without_writing(tmp_path: Path) -> None:
    # Given: a PNG-shaped payload beyond the reviewed decoded limit.
    payload = _png() + b"x" * (8 * 1024 * 1024)
    response = json.dumps(
        {"id": 1, "result": {"data": base64.b64encode(payload).decode()}}, separators=(",", ":")
    ).encode()
    root = tmp_path / "screenshots"
    # When: capture parses the oversized response.
    with pytest.raises(InvalidChromeDevToolsError) as raised:
        _ = ChromeDevToolsClient(FixtureCdpTransport([response])).capture("target-1", root, captured_at=NOW)
    # Then: it fails with a stable reason and publishes nothing.
    assert raised.value.reason == "browser_navigation_blocked"
    assert not root.exists()


def test_capture_rejects_symlinked_screenshot_root(tmp_path: Path) -> None:
    # Given: the configured screenshot root is redirected through a symlink.
    destination = tmp_path / "destination"
    destination.mkdir(mode=0o700)
    root = tmp_path / "screenshots"
    root.symlink_to(destination, target_is_directory=True)
    response = json.dumps(
        {"id": 1, "result": {"data": base64.b64encode(_png()).decode()}}, separators=(",", ":")
    ).encode()
    # When: capture reaches private publication.
    with pytest.raises(InvalidChromeDevToolsError) as raised:
        _ = ChromeDevToolsClient(FixtureCdpTransport([response])).capture("target-1", root, captured_at=NOW)
    # Then: publication fails closed and writes no artifact through the link.
    assert raised.value.reason == "browser_navigation_blocked"
    assert tuple(destination.iterdir()) == ()
