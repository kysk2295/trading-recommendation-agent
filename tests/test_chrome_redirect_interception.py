from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import TracebackType

import pytest

import trading_agent.chrome_devtools_websocket as cdp_websocket
from trading_agent.chrome_devtools_types import InvalidChromeDevToolsError
from trading_agent.chrome_devtools_websocket import SerializedChromeWebSocket


@dataclass(slots=True)
class _Clock:
    now: float = 10.0

    def monotonic(self) -> float:
        return self.now


@dataclass(slots=True)
class _FixtureWebSocket:
    responses: list[bytes]
    sent: list[str] = field(default_factory=list)
    close_timeout: float | None = None
    closed: bool = False

    def __enter__(self) -> _FixtureWebSocket:
        return self

    def __exit__(
        self,
        _kind: type[BaseException] | None,
        _value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.closed = True

    def send(self, payload: str) -> None:
        self.sent.append(payload)

    def recv(self, *, timeout: float, decode: bool) -> bytes:
        assert timeout > 0 and decode is False
        if not self.responses:
            raise TimeoutError
        return self.responses.pop(0)


@dataclass(slots=True)
class _TimedWebSocket:
    clock: _Clock
    responses: list[bytes]
    receive_seconds: list[float]
    sent: list[str] = field(default_factory=list)
    close_timeout: float | None = None
    closed: bool = False

    def __enter__(self) -> _TimedWebSocket:
        return self

    def __exit__(
        self,
        _kind: type[BaseException] | None,
        _value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.closed = True

    def send(self, payload: str) -> None:
        self.sent.append(payload)

    def recv(self, *, timeout: float, decode: bool) -> bytes:
        assert timeout > 0 and decode is False
        self.clock.now += self.receive_seconds.pop(0)
        return self.responses.pop(0)


def _paused(url: str) -> bytes:
    return json.dumps(
        {
            "method": "Fetch.requestPaused",
            "params": {
                "requestId": "intercept-1",
                "request": {
                    "url": url,
                    "headers": {"Authorization": "must-not-cross-boundary"},
                    "method": "GET",
                },
            },
        },
        separators=(",", ":"),
    ).encode()


def _sent_commands(socket: _FixtureWebSocket | _TimedWebSocket) -> list[dict[str, object]]:
    return [json.loads(payload) for payload in socket.sent]


@pytest.mark.parametrize(
    "redirect_url",
    (
        "https://127.0.0.1/private",
        "http://example.com/private",
        "file:///etc/passwd",
        "https://example.com:8443/private",
    ),
)
def test_guarded_navigation_fails_invalid_redirect_without_continuing(
    monkeypatch: pytest.MonkeyPatch,
    redirect_url: str,
) -> None:
    # Given: Chrome pauses a redirected document request before network dispatch.
    socket = _FixtureWebSocket(
        [
            b'{"id":1,"result":{}}',
            _paused(redirect_url),
            b'{"id":3,"result":{}}',
            b'{"id":2,"result":{"errorText":"net::ERR_BLOCKED_BY_CLIENT"}}',
            b'{"id":4,"result":{}}',
        ]
    )
    monkeypatch.setattr(cdp_websocket, "connect", lambda *_args, **_kwargs: socket)
    websocket = SerializedChromeWebSocket(_Clock())
    # When: guarded navigation evaluates the redirect hop.
    with pytest.raises(InvalidChromeDevToolsError) as raised:
        _ = websocket.navigate_guarded(
            "ws://127.0.0.1:9222/devtools/page/page-1",
            "https://example.com/start",
            20.0,
        )
    # Then: the request is failed, interception is disabled, and no sensitive event field is reflected.
    commands = _sent_commands(socket)
    assert raised.value.reason == "browser_navigation_blocked"
    assert [command["method"] for command in commands] == [
        "Fetch.enable",
        "Page.navigate",
        "Fetch.failRequest",
        "Fetch.disable",
    ]
    assert [command["id"] for command in commands] == [1, 2, 3, 4]
    assert commands[0]["params"] == {
        "patterns": [{"urlPattern": "*", "resourceType": "Document", "requestStage": "Request"}]
    }
    assert commands[2]["params"] == {"requestId": "intercept-1", "errorReason": "BlockedByClient"}
    assert all(command["method"] != "Fetch.continueRequest" for command in commands)
    assert "must-not-cross-boundary" not in "".join(socket.sent)
    assert socket.responses == []
    assert socket.closed is True and socket.close_timeout == pytest.approx(10.0)


def test_guarded_navigation_continues_public_https_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a redirected document request remains within the public HTTPS policy.
    socket = _FixtureWebSocket(
        [
            b'{"id":1,"result":{}}',
            _paused("https://example.org/final"),
            b'{"id":3,"result":{}}',
            b'{"id":2,"result":{"frameId":"frame-1"}}',
            b'{"id":4,"result":{}}',
        ]
    )
    monkeypatch.setattr(cdp_websocket, "connect", lambda *_args, **_kwargs: socket)
    websocket = SerializedChromeWebSocket(_Clock())
    # When: guarded navigation handles the redirect and matching navigation response.
    response = websocket.navigate_guarded(
        "ws://127.0.0.1:9222/devtools/page/page-1",
        "https://example.com/start",
        20.0,
    )
    # Then: only the public request continues and interception is explicitly disabled.
    commands = _sent_commands(socket)
    assert response == b'{"id":2,"result":{"frameId":"frame-1"}}'
    assert [command["method"] for command in commands] == [
        "Fetch.enable",
        "Page.navigate",
        "Fetch.continueRequest",
        "Fetch.disable",
    ]
    assert [command["id"] for command in commands] == [1, 2, 3, 4]
    assert commands[2]["params"] == {"requestId": "intercept-1"}
    assert socket.closed is True


def test_guarded_navigation_disable_and_close_share_original_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    socket = _TimedWebSocket(
        clock,
        [
            b'{"id":1,"result":{}}',
            b'{"id":2,"result":{"frameId":"frame-1"}}',
            b'{"id":3,"result":{}}',
        ],
        [0.2, 0.2, 0.7],
    )
    monkeypatch.setattr(cdp_websocket, "connect", lambda *_args, **_kwargs: socket)
    websocket = SerializedChromeWebSocket(clock)
    with pytest.raises(InvalidChromeDevToolsError) as raised:
        _ = websocket.navigate_guarded(
            "ws://127.0.0.1:9222/devtools/page/page-1",
            "https://example.com/start",
            11.0,
        )
    assert raised.value.reason == "browser_cdp_timeout"
    assert [command["method"] for command in _sent_commands(socket)] == [
        "Fetch.enable",
        "Page.navigate",
        "Fetch.disable",
    ]
    assert socket.closed is True and socket.close_timeout == 0.0
