from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import TracebackType

import pytest

import trading_agent.chrome_devtools_websocket as cdp_websocket
from trading_agent.chrome_devtools_types import CdpCommand, CdpMethod, InvalidChromeDevToolsError
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


def _paused(url: str) -> bytes:
    return json.dumps(
        {
            "method": "Fetch.requestPaused",
            "params": {"requestId": "intercept-1", "request": {"url": url}},
        },
        separators=(",", ":"),
    ).encode()


def _methods(socket: _FixtureWebSocket) -> list[str]:
    return [json.loads(payload)["method"] for payload in socket.sent]


def _guarded(socket: _FixtureWebSocket, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cdp_websocket, "connect", lambda *_args, **_kwargs: socket)
    _ = SerializedChromeWebSocket(_Clock()).navigate_guarded(
        "ws://127.0.0.1:9222/devtools/page/page-1",
        "https://example.com/start",
        20.0,
    )


@pytest.mark.parametrize(
    "response",
    (
        b'{"id":1,"error":{"code":-32601,"message":"sensitive-provider-detail"}}',
        b'{"id":1}',
        b'{"id":1,"result":null}',
        b'{"id":1,"result":[]}',
    ),
)
def test_normal_command_requires_same_id_success_result_object(
    monkeypatch: pytest.MonkeyPatch,
    response: bytes,
) -> None:
    socket = _FixtureWebSocket([response])
    monkeypatch.setattr(cdp_websocket, "connect", lambda *_args, **_kwargs: socket)
    command = CdpCommand(CdpMethod.RUNTIME_EVALUATE, '{"expression":"document.readyState"}')

    with pytest.raises(InvalidChromeDevToolsError) as raised:
        _ = SerializedChromeWebSocket(_Clock()).command("ws://127.0.0.1:9222/devtools/page/page-1", command, 20.0)

    assert raised.value.reason == "browser_navigation_blocked"
    assert "sensitive-provider-detail" not in str(raised.value)
    assert socket.closed is True


@pytest.mark.parametrize(
    "enable_response",
    (
        b'{"id":1,"error":{"code":-32601,"message":"enable-secret"}}',
        b'{"id":1}',
    ),
)
def test_enable_failure_closes_without_navigation_or_disable(
    monkeypatch: pytest.MonkeyPatch,
    enable_response: bytes,
) -> None:
    socket = _FixtureWebSocket([enable_response, b'{"id":2,"result":{}}', b'{"id":3,"result":{}}'])

    with pytest.raises(InvalidChromeDevToolsError) as raised:
        _guarded(socket, monkeypatch)

    assert raised.value.reason == "browser_navigation_blocked"
    assert "enable-secret" not in str(raised.value)
    assert _methods(socket) == ["Fetch.enable"]
    assert socket.closed is True


@pytest.mark.parametrize(
    ("redirect_url", "action_method"),
    (
        ("https://example.org/final", "Fetch.continueRequest"),
        ("https://127.0.0.1/private", "Fetch.failRequest"),
    ),
)
def test_interception_action_error_remains_pending_and_skips_disable(
    monkeypatch: pytest.MonkeyPatch,
    redirect_url: str,
    action_method: str,
) -> None:
    socket = _FixtureWebSocket(
        [
            b'{"id":1,"result":{}}',
            _paused(redirect_url),
            b'{"id":3,"error":{"code":-32602,"message":"action-secret"}}',
            b'{"id":2,"result":{"frameId":"frame-1"}}',
            b'{"id":4,"result":{}}',
        ]
    )

    with pytest.raises(InvalidChromeDevToolsError) as raised:
        _guarded(socket, monkeypatch)

    assert raised.value.reason == "browser_navigation_blocked"
    assert "action-secret" not in str(raised.value)
    assert _methods(socket) == ["Fetch.enable", "Page.navigate", action_method]
    assert socket.closed is True


def test_navigation_error_disables_only_after_no_paused_request_is_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    socket = _FixtureWebSocket(
        [
            b'{"id":1,"result":{}}',
            b'{"id":2,"error":{"code":-32000,"message":"navigate-secret"}}',
            b'{"id":3,"result":{}}',
        ]
    )

    with pytest.raises(InvalidChromeDevToolsError) as raised:
        _guarded(socket, monkeypatch)

    assert raised.value.reason == "browser_navigation_blocked"
    assert "navigate-secret" not in str(raised.value)
    assert _methods(socket) == ["Fetch.enable", "Page.navigate", "Fetch.disable"]
    assert socket.closed is True


def test_navigation_error_skips_disable_while_interception_action_is_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    socket = _FixtureWebSocket(
        [
            b'{"id":1,"result":{}}',
            _paused("https://example.org/final"),
            b'{"id":2,"error":{"code":-32000,"message":"navigate-secret"}}',
        ]
    )

    with pytest.raises(InvalidChromeDevToolsError) as raised:
        _guarded(socket, monkeypatch)

    assert raised.value.reason == "browser_navigation_blocked"
    assert "navigate-secret" not in str(raised.value)
    assert _methods(socket) == ["Fetch.enable", "Page.navigate", "Fetch.continueRequest"]
    assert socket.closed is True


def test_disable_error_closes_and_never_returns_navigation_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    socket = _FixtureWebSocket(
        [
            b'{"id":1,"result":{}}',
            b'{"id":2,"result":{"frameId":"frame-1"}}',
            b'{"id":3,"error":{"code":-32601,"message":"disable-secret"}}',
        ]
    )

    with pytest.raises(InvalidChromeDevToolsError) as raised:
        _guarded(socket, monkeypatch)

    assert raised.value.reason == "browser_navigation_blocked"
    assert "disable-secret" not in str(raised.value)
    assert _methods(socket) == ["Fetch.enable", "Page.navigate", "Fetch.disable"]
    assert socket.closed is True
