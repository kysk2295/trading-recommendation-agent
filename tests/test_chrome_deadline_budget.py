from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from trading_agent.chrome_devtools_client import ChromeDevToolsClient
from trading_agent.chrome_devtools_types import (
    CdpCommand,
    CdpMethod,
    ChromeDevToolsStatus,
    ChromeTarget,
    InvalidChromeDevToolsError,
)

_NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


@dataclass(slots=True)  # noqa: MUTABLE_OK — monotonic fixture clock advances during waits
class _Clock:
    now: float = 10.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@dataclass(slots=True)  # noqa: MUTABLE_OK — fixture records commands and consumes scripted responses
class _LateReadyTransport:
    clock: _Clock
    commands: list[tuple[CdpMethod, float | None]] = field(default_factory=list)
    guarded_navigations: list[tuple[str, str, float | None]] = field(default_factory=list)

    def status(self) -> ChromeDevToolsStatus:
        return ChromeDevToolsStatus(True, 1)

    def create_target(self) -> ChromeTarget:
        return ChromeTarget("page-1", "about:blank", "", "ws://127.0.0.1:9222/devtools/page/page-1")

    def command(
        self,
        target_id: str,
        command: CdpCommand,
        *,
        timeout_seconds: float | None = None,
    ) -> bytes:
        _ = target_id
        self.commands.append((command.method, timeout_seconds))
        if command.method is CdpMethod.PAGE_NAVIGATE:
            return b'{"id":1,"result":{"frameId":"frame-1"}}'
        expression = json.loads(command.params_json)["expression"]
        if expression == "document.readyState":
            self.clock.now += (timeout_seconds or 1.0) + 0.01
            value = "interactive"
        else:
            value = json.dumps({"title": "late", "url": "https://example.com"})
        return json.dumps({"id": 1, "result": {"result": {"type": "string", "value": value}}}).encode()

    def navigate_guarded(
        self,
        target_id: str,
        url: str,
        *,
        timeout_seconds: float | None = None,
    ) -> bytes:
        self.guarded_navigations.append((target_id, url, timeout_seconds))
        return b'{"id":1,"result":{"frameId":"frame-1"}}'


def test_ready_state_arriving_after_deadline_is_not_accepted() -> None:
    # Given: the CDP command returns interactive only after consuming its entire remaining budget.
    clock = _Clock()
    transport = _LateReadyTransport(clock)
    client = ChromeDevToolsClient(transport, command_timeout_seconds=1.0, clock=clock)
    # When: navigation waits for the ready state.
    with pytest.raises(InvalidChromeDevToolsError) as raised:
        _ = client.open("https://example.com", captured_at=_NOW)
    # Then: the late success is rejected and the bounded remaining budget crossed the typed boundary.
    assert raised.value.reason == "browser_cdp_timeout"
    assert transport.guarded_navigations == [("page-1", "https://example.com", None)]
    assert transport.commands[0] == (CdpMethod.RUNTIME_EVALUATE, pytest.approx(1.0))
