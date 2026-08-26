from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Callable

import pytest

from tests.test_autonomous_task_models import NOW
from trading_agent.autonomous_reasoning import AutonomousToolArguments, AutonomousToolCall
from trading_agent.autonomous_task_models import AutonomousAgentRole
from trading_agent.autonomous_tool_runtime import (
    AutonomousToolBinding,
    AutonomousToolExecutionContext,
    AutonomousToolInvocationError,
    AutonomousToolRuntime,
    AutonomousToolRuntimeError,
)

CONTEXT = AutonomousToolExecutionContext.model_validate(
    {"task_id": "b" * 64, "agent_family_id": "day_trading", "market_scope": "us_equities"}
)


def _call(**arguments: str) -> AutonomousToolCall:
    return AutonomousToolCall(
        tool_name="evidence.read",
        args=AutonomousToolArguments(arguments or {"evidence_id": "a" * 64}),
        reason="Read one bounded evidence payload before continuing the research task.",
    )


def _binding(
    *,
    invoke: Callable[[AutonomousToolArguments, AutonomousToolExecutionContext], str] | None = None,
    name: str = "evidence.read",
) -> AutonomousToolBinding:
    def read(arguments: AutonomousToolArguments, context: AutonomousToolExecutionContext) -> str:
        del arguments, context
        return '{"symbol":"NVDA","source":"fixture"}'

    return AutonomousToolBinding(
        name=name,
        allowed_roles=frozenset({AutonomousAgentRole.MARKET_OBSERVER}),
        allowed_arguments=frozenset({"evidence_id"}),
        invoke=read if invoke is None else invoke,
        evidence_refs=("evidence:root",),
    )


def test_dispatch_canonicalizes_hashes_and_normalizes_clock() -> None:
    # Given: one allowlisted read-only binding and an offset-aware injected clock.
    runtime = AutonomousToolRuntime(
        bindings=(_binding(),),
        clock=lambda: NOW.astimezone(dt.timezone(dt.timedelta(hours=9))),
    )
    call = _call()

    # When: an authorized role invokes its registered tool.
    observation = runtime.dispatch(AutonomousAgentRole.MARKET_OBSERVER, call, CONTEXT)

    # Then: output is canonical, content-addressed, and timestamped in UTC.
    assert runtime.allowed_tool_names == ("evidence.read",)
    assert observation.bounded_json == '{"source":"fixture","symbol":"NVDA"}'
    assert observation.observed_at == NOW
    canonical_call = json.dumps(call.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    assert observation.call_sha256 == hashlib.sha256(canonical_call.encode()).hexdigest()
    assert observation.content_sha256 in observation.evidence_refs


@pytest.mark.parametrize(
    ("role", "call"),
    (
        (AutonomousAgentRole.RESEARCH, _call()),
        (AutonomousAgentRole.MARKET_OBSERVER, _call(extra="denied")),
        (AutonomousAgentRole.MARKET_OBSERVER, _call(access_token="denied")),
    ),
)
def test_dispatch_denies_authority_before_binding_invocation(
    role: AutonomousAgentRole, call: AutonomousToolCall
) -> None:
    # Given: a binding records whether it was invoked.
    invoked: list[bool] = []

    def forbidden(arguments: AutonomousToolArguments, context: AutonomousToolExecutionContext) -> str:
        del arguments, context
        invoked.append(True)
        return "{}"

    runtime = AutonomousToolRuntime(bindings=(_binding(invoke=forbidden),), clock=lambda: NOW)

    # When / Then: denied authority never crosses into host code.
    with pytest.raises(AutonomousToolRuntimeError, match="autonomous_tool_authority_denied"):
        runtime.dispatch(role, call, CONTEXT)
    assert invoked == []


def test_runtime_rejects_duplicate_unknown_failure_invalid_output_and_invalid_clock() -> None:
    # Given / When / Then: unsafe construction and invocation boundaries fail closed.
    with pytest.raises(AutonomousToolRuntimeError, match="autonomous_tool_binding_duplicate"):
        AutonomousToolRuntime(bindings=(_binding(), _binding()), clock=lambda: NOW)
    runtime = AutonomousToolRuntime(bindings=(_binding(),), clock=lambda: dt.datetime(2026, 8, 26, 12, 0))
    with pytest.raises(AutonomousToolRuntimeError, match="autonomous_tool_authority_denied"):
        runtime.dispatch(
            AutonomousAgentRole.MARKET_OBSERVER,
            _call().model_copy(update={"tool_name": "missing.tool"}),
            CONTEXT,
        )
    with pytest.raises(AutonomousToolRuntimeError, match="autonomous_tool_clock_invalid"):
        runtime.dispatch(AutonomousAgentRole.MARKET_OBSERVER, _call(), CONTEXT)

    def failed(arguments: AutonomousToolArguments, context: AutonomousToolExecutionContext) -> str:
        del arguments, context
        raise AutonomousToolInvocationError(reason="fixture_failed")

    with pytest.raises(AutonomousToolRuntimeError, match="autonomous_tool_invocation_failed"):
        AutonomousToolRuntime(bindings=(_binding(invoke=failed),), clock=lambda: NOW).dispatch(
            AutonomousAgentRole.MARKET_OBSERVER, _call(), CONTEXT
        )
    with pytest.raises(AutonomousToolRuntimeError, match="autonomous_tool_result_invalid"):
        AutonomousToolRuntime(
            bindings=(_binding(invoke=lambda arguments, context: "not-json"),),
            clock=lambda: NOW,
        ).dispatch(
            AutonomousAgentRole.MARKET_OBSERVER, _call(), CONTEXT
        )
    with pytest.raises(AutonomousToolRuntimeError, match="autonomous_tool_result_too_large"):
        AutonomousToolRuntime(
            bindings=(_binding(invoke=lambda arguments, context: '"' + "x" * 16_384 + '"'),), clock=lambda: NOW
        ).dispatch(AutonomousAgentRole.MARKET_OBSERVER, _call(), CONTEXT)
