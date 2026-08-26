from __future__ import annotations

import functools
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from trading_agent.autonomous_browser_tool_actions import (
    browser_capture_tool,
    browser_follow_tool,
    browser_open_tool,
    browser_read_tool,
    browser_search_tool,
    browser_status_tool,
    social_evidence_search_tool,
)
from trading_agent.autonomous_task_models import AutonomousAgentRole
from trading_agent.autonomous_tool_runtime import AutonomousToolBinding, AutonomousToolInvocationError

_BROWSER_TIMEOUT_SECONDS: Final = 20.0
_OBSERVERS: Final = frozenset({AutonomousAgentRole.MARKET_OBSERVER, AutonomousAgentRole.RESEARCH})
_READERS: Final = _OBSERVERS | frozenset({AutonomousAgentRole.CRITIC})
_EVIDENCE_SEARCHERS: Final = _READERS | frozenset({AutonomousAgentRole.OPPORTUNITY})


@dataclass(frozen=True, slots=True)
class BrowserToolServices:
    gateway_socket: Path
    evidence_database: Path
    timeout_seconds: float = _BROWSER_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        if not 0 < self.timeout_seconds <= 60:
            raise AutonomousToolInvocationError(reason="browser_tool_timeout_invalid")
        object.__setattr__(self, "gateway_socket", self.gateway_socket.absolute())
        object.__setattr__(self, "evidence_database", self.evidence_database.absolute())


def browser_bindings(services: BrowserToolServices) -> tuple[AutonomousToolBinding, ...]:
    return (
        _binding("browser.status", _READERS, frozenset(), browser_status_tool, services),
        _binding("browser.search", _OBSERVERS, frozenset({"query"}), browser_search_tool, services),
        _binding("browser.open", _OBSERVERS, frozenset({"url"}), browser_open_tool, services),
        _binding("browser.read", _READERS, frozenset({"target_id"}), browser_read_tool, services),
        _binding("browser.follow", _OBSERVERS, frozenset({"target_id", "link_index"}), browser_follow_tool, services),
        _binding("browser.capture", _READERS, frozenset({"target_id"}), browser_capture_tool, services),
        _binding(
            "social.evidence.search",
            _EVIDENCE_SEARCHERS,
            frozenset({"query", "limit"}),
            social_evidence_search_tool,
            services,
        ),
    )


def _binding(
    name: str,
    roles: frozenset[AutonomousAgentRole],
    arguments: frozenset[str],
    callback: Callable[..., str],
    services: BrowserToolServices,
) -> AutonomousToolBinding:
    return AutonomousToolBinding(
        name,
        roles,
        arguments,
        functools.partial(
            callback,
            gateway_socket=str(services.gateway_socket),
            evidence_database=str(services.evidence_database),
            timeout_seconds=services.timeout_seconds,
        ),
        (),
    )


__all__ = ("BrowserToolServices", "browser_bindings")
