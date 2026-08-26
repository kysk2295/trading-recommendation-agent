from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import TracebackType

import pytest

import trading_agent.chrome_devtools_websocket as cdp_websocket
from trading_agent.chrome_devtools_types import InvalidChromeDevToolsError
from trading_agent.chrome_devtools_websocket import SerializedChromeWebSocket


@dataclass(frozen=True, slots=True)
class _Clock:
    def monotonic(self) -> float:
        return 10.0


@dataclass(slots=True)  # noqa: MUTABLE_OK — response FIFO, sent log, and close state are asserted
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


def _paused(params_json: str) -> bytes:
    return f'{{"method":"Fetch.requestPaused","params":{params_json}}}'.encode()


def _methods(socket: _FixtureWebSocket) -> list[str]:
    return [json.loads(payload)["method"] for payload in socket.sent]


@pytest.mark.parametrize(
    "request_json",
    (
        json.dumps({"requestId": "intercept-1", "request": {"url": "http://127.0.0.1/" + "x" * 2_100}}),
        json.dumps({"requestId": "intercept-1", "request": {}}),
        json.dumps({"requestId": "intercept-1", "request": {"url": 7}}),
    ),
)
def test_guarded_navigation_fails_malformed_url_with_trustworthy_request_id(
    monkeypatch: pytest.MonkeyPatch,
    request_json: str,
) -> None:
    socket = _FixtureWebSocket(
        [
            b'{"id":1,"result":{}}',
            _paused(request_json),
            b'{"id":3,"result":{}}',
            b'{"id":2,"result":{"errorText":"net::ERR_BLOCKED_BY_CLIENT"}}',
            b'{"id":4,"result":{}}',
        ]
    )
    monkeypatch.setattr(cdp_websocket, "connect", lambda *_args, **_kwargs: socket)
    with pytest.raises(InvalidChromeDevToolsError) as raised:
        _ = SerializedChromeWebSocket(_Clock()).navigate_guarded(
            "ws://127.0.0.1:9222/devtools/page/page-1",
            "https://example.com/start",
            20.0,
        )
    commands = [json.loads(payload) for payload in socket.sent]
    assert raised.value.reason == "browser_navigation_blocked"
    assert _methods(socket) == ["Fetch.enable", "Page.navigate", "Fetch.failRequest", "Fetch.disable"]
    assert commands[2]["params"] == {"requestId": "intercept-1", "errorReason": "BlockedByClient"}


@pytest.mark.parametrize(
    "request_json",
    (
        json.dumps({"requestId": "x" * 257, "request": {"url": "https://127.0.0.1/private"}}),
        json.dumps({"requestId": 7, "request": {"url": "https://127.0.0.1/private"}}),
        json.dumps({"request": {"url": "https://127.0.0.1/private"}}),
    ),
)
def test_guarded_navigation_aborts_without_disable_when_request_id_is_untrustworthy(
    monkeypatch: pytest.MonkeyPatch,
    request_json: str,
) -> None:
    socket = _FixtureWebSocket([b'{"id":1,"result":{}}', _paused(request_json), b'{"id":3,"result":{}}'])
    monkeypatch.setattr(cdp_websocket, "connect", lambda *_args, **_kwargs: socket)
    with pytest.raises(InvalidChromeDevToolsError) as raised:
        _ = SerializedChromeWebSocket(_Clock()).navigate_guarded(
            "ws://127.0.0.1:9222/devtools/page/page-1",
            "https://example.com/start",
            20.0,
        )
    assert raised.value.reason == "browser_navigation_blocked"
    assert _methods(socket) == ["Fetch.enable", "Page.navigate"]
    assert socket.closed is True


def test_guarded_navigation_does_not_disable_before_fail_ack(monkeypatch: pytest.MonkeyPatch) -> None:
    request = json.dumps({"requestId": "intercept-1", "request": {"url": "https://127.0.0.1/private"}})
    socket = _FixtureWebSocket([b'{"id":1,"result":{}}', _paused(request)])
    monkeypatch.setattr(cdp_websocket, "connect", lambda *_args, **_kwargs: socket)
    with pytest.raises(InvalidChromeDevToolsError) as raised:
        _ = SerializedChromeWebSocket(_Clock()).navigate_guarded(
            "ws://127.0.0.1:9222/devtools/page/page-1",
            "https://example.com/start",
            20.0,
        )
    assert raised.value.reason == "browser_cdp_timeout"
    assert _methods(socket) == ["Fetch.enable", "Page.navigate", "Fetch.failRequest"]
    assert socket.closed is True
