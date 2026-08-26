from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from trading_agent.autonomous_reasoning import AutonomousToolArguments, AutonomousToolCall, AutonomousToolObservation
from trading_agent.autonomous_task_models import AutonomousAgentRole, AutonomousResearchTask, AutonomousTaskId
from trading_agent.dashboard_agent_family import AgentFamilyId
from trading_agent.research_agent_cycle_models import MarketId

_MAX_CONTENT_BYTES: Final = 16_384
_SECRET_ARGUMENT: Final = re.compile(r"key|secret|token|password|authorization|account|credential", re.IGNORECASE)
_TOOL_NAME: Final = re.compile(r"^[a-z][a-z0-9_.-]{2,63}$")


class AutonomousToolRuntimeError(RuntimeError):
    __slots__ = ("reason",)

    reason: str

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)

    def __str__(self) -> str:
        return self.reason


class AutonomousToolInvocationError(RuntimeError):
    __slots__ = ("reason",)

    reason: str

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)

    def __str__(self) -> str:
        return self.reason


class AutonomousToolExecutionContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    task_id: AutonomousTaskId = Field(pattern=r"^[a-f0-9]{64}$")
    agent_family_id: AgentFamilyId
    market_scope: MarketId


def trusted_tool_context(task: AutonomousResearchTask) -> AutonomousToolExecutionContext:
    return AutonomousToolExecutionContext(
        task_id=task.task_id,
        agent_family_id=task.agent_family_id,
        market_scope=task.market_scope,
    )


@dataclass(frozen=True, slots=True)
class AutonomousToolBinding:
    name: str
    allowed_roles: frozenset[AutonomousAgentRole]
    allowed_arguments: frozenset[str]
    invoke: Callable[[AutonomousToolArguments, AutonomousToolExecutionContext], str]
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            _TOOL_NAME.fullmatch(self.name) is None
            or not self.allowed_roles
            or any(not isinstance(role, AutonomousAgentRole) for role in self.allowed_roles)
            or any(_TOOL_NAME.fullmatch(argument) is None for argument in self.allowed_arguments)
            or self.evidence_refs != tuple(sorted(set(self.evidence_refs)))
            or any(not reference for reference in self.evidence_refs)
        ):
            raise AutonomousToolRuntimeError(reason="autonomous_tool_binding_invalid")


@final
class AutonomousToolRuntime:
    __slots__ = ("_bindings", "_clock", "_worker_modules")

    def __init__(
        self,
        bindings: tuple[AutonomousToolBinding, ...],
        clock: Callable[[], dt.datetime],
        *,
        worker_modules: frozenset[str] = frozenset(),
    ) -> None:
        names = tuple(binding.name for binding in bindings)
        if len(names) != len(set(names)) or any(not module or module.startswith("_") for module in worker_modules):
            raise AutonomousToolRuntimeError(reason="autonomous_tool_binding_duplicate")
        self._bindings = {binding.name: binding for binding in bindings}
        self._clock = clock
        self._worker_modules = worker_modules

    @property
    def allowed_tool_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._bindings))

    def allowed_tools(self, role: AutonomousAgentRole) -> tuple[str, ...]:
        return tuple(sorted(binding.name for binding in self._bindings.values() if role in binding.allowed_roles))

    def allowed_tool_signatures(self, role: AutonomousAgentRole) -> tuple[str, ...]:
        return tuple(
            sorted(
                f"{binding.name}({','.join(sorted(binding.allowed_arguments))})"
                for binding in self._bindings.values()
                if role in binding.allowed_roles
            )
        )

    def dispatch(
        self,
        role: AutonomousAgentRole,
        call: AutonomousToolCall,
        context: AutonomousToolExecutionContext,
    ) -> AutonomousToolObservation:
        binding = self._authorized_binding(role, call)
        try:
            raw_output = binding.invoke(call.args, context)
        except AutonomousToolInvocationError:
            raise AutonomousToolRuntimeError(reason="autonomous_tool_invocation_failed") from None
        except Exception:  # noqa: RUF100 # noqa: BROAD_EXCEPT_OK: untrusted host callback must not leak implementation details or sensitive plugin exception text
            raise AutonomousToolRuntimeError(reason="autonomous_tool_invocation_failed") from None
        bounded_json = _canonical_result(raw_output)
        call_json = _canonical_call(call)
        content_sha256 = hashlib.sha256(bounded_json.encode()).hexdigest()
        try:
            observed_at = _utc_clock(self._clock())
            return AutonomousToolObservation(
                tool_name=call.tool_name,
                call_json=call_json,
                bounded_json=bounded_json,
                evidence_refs=tuple(sorted({*binding.evidence_refs, content_sha256})),
                observed_at=observed_at,
                call_sha256=hashlib.sha256(call_json.encode()).hexdigest(),
                content_sha256=content_sha256,
            )
        except ValidationError:
            raise AutonomousToolRuntimeError(reason="autonomous_tool_result_invalid") from None

    def _authorized_binding(self, role: AutonomousAgentRole, call: AutonomousToolCall) -> AutonomousToolBinding:
        binding = self._bindings.get(call.tool_name)
        argument_names = call.args.root
        if (
            binding is None
            or role not in binding.allowed_roles
            or not set(argument_names).issubset(binding.allowed_arguments)
            or any(_SECRET_ARGUMENT.search(name) is not None for name in argument_names)
        ):
            raise AutonomousToolRuntimeError(reason="autonomous_tool_authority_denied")
        return binding


def _canonical_call(call: AutonomousToolCall) -> str:
    return json.dumps(
        call.model_dump(mode="json"), allow_nan=False, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    )


def _canonical_result(raw_output: str) -> str:
    try:
        decoded = json.loads(raw_output)
        bounded_json = json.dumps(decoded, allow_nan=False, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError):
        raise AutonomousToolRuntimeError(reason="autonomous_tool_result_invalid") from None
    if len(bounded_json.encode()) > _MAX_CONTENT_BYTES:
        raise AutonomousToolRuntimeError(reason="autonomous_tool_result_too_large")
    return bounded_json


def _utc_clock(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise AutonomousToolRuntimeError(reason="autonomous_tool_clock_invalid")
    return value.astimezone(dt.UTC)


__all__ = (
    "AutonomousToolBinding",
    "AutonomousToolExecutionContext",
    "AutonomousToolInvocationError",
    "AutonomousToolRuntime",
    "AutonomousToolRuntimeError",
    "trusted_tool_context",
)
