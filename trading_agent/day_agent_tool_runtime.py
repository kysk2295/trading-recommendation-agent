from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, final

from pydantic import ValidationError

from trading_agent.day_agent_task_models import DayAgentAction
from trading_agent.day_agent_tool_models import (
    DayAgentToolArguments,
    DayAgentToolCall,
    DayAgentToolObservation,
)

_MAX_CONTENT_BYTES: Final = 16_384


class DayAgentToolRuntimeError(RuntimeError):
    __slots__ = ("reason",)

    reason: str

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class DayAgentToolBinding:
    action: DayAgentAction
    allowed_arguments: frozenset[str]
    invoke: Callable[[DayAgentToolArguments], str]
    evidence_refs: tuple[str, ...]


@final
class DayAgentToolRuntime:
    __slots__ = ("_bindings", "_clock")

    def __init__(
        self,
        bindings: tuple[DayAgentToolBinding, ...],
        clock: Callable[[], dt.datetime],
    ) -> None:
        actions = tuple(binding.action for binding in bindings)
        if len(actions) != len(set(actions)):
            raise DayAgentToolRuntimeError(reason="day_agent_tool_binding_duplicate")
        self._bindings = {binding.action: binding for binding in bindings}
        self._clock = clock

    @property
    def allowed_tool_names(self) -> tuple[str, ...]:
        return tuple(sorted(action.value for action in self._bindings))

    def dispatch(self, call: DayAgentToolCall) -> DayAgentToolObservation:
        binding = self._bindings.get(call.action)
        if binding is None or not set(call.arguments.root).issubset(binding.allowed_arguments):
            raise DayAgentToolRuntimeError(reason="day_agent_tool_authority_denied")
        try:
            raw_result = binding.invoke(call.arguments)
        except Exception:
            raise DayAgentToolRuntimeError(reason="day_agent_tool_call_failed") from None
        try:
            decoded = json.loads(raw_result)
            bounded_json = json.dumps(decoded, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        except (TypeError, ValueError):
            raise DayAgentToolRuntimeError(reason="day_agent_tool_result_invalid") from None
        if len(bounded_json.encode()) > _MAX_CONTENT_BYTES:
            raise DayAgentToolRuntimeError(reason="day_agent_tool_result_too_large")
        digest = hashlib.sha256(bounded_json.encode()).hexdigest()
        try:
            return DayAgentToolObservation(
                action=call.action,
                bounded_json=bounded_json,
                evidence_refs=tuple(sorted({*binding.evidence_refs, digest})),
                observed_at=self._clock(),
                content_sha256=digest,
            )
        except ValidationError:
            raise DayAgentToolRuntimeError(reason="day_agent_tool_result_invalid") from None


__all__ = (
    "DayAgentToolBinding",
    "DayAgentToolRuntime",
    "DayAgentToolRuntimeError",
)
