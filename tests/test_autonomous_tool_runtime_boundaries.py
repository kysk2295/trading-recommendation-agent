from __future__ import annotations

import datetime as dt
from collections.abc import Callable

import pytest

from tests.test_autonomous_task_models import NOW
from trading_agent.autonomous_reasoning import AutonomousToolArguments, AutonomousToolCall
from trading_agent.autonomous_task_models import AutonomousAgentRole
from trading_agent.autonomous_tool_runtime import (
    AutonomousToolBinding,
    AutonomousToolRuntime,
    AutonomousToolRuntimeError,
)

_ROLES = frozenset({AutonomousAgentRole.MARKET_OBSERVER})
_ARGUMENTS = frozenset({"evidence_id"})


def _call(**values: str) -> AutonomousToolCall:
    return AutonomousToolCall(
        tool_name="evidence.read",
        args=AutonomousToolArguments(values or {"evidence_id": "a" * 64}),
        reason="Read bounded evidence before continuing a research decision.",
    )


def _binding(invoke: Callable[[AutonomousToolArguments], str]) -> AutonomousToolBinding:
    return AutonomousToolBinding(
        name="evidence.read",
        allowed_roles=_ROLES,
        allowed_arguments=_ARGUMENTS,
        invoke=invoke,
        evidence_refs=("evidence:root",),
    )


@pytest.mark.parametrize(
    ("name", "roles", "arguments", "refs"),
    (
        ("x", _ROLES, _ARGUMENTS, ("evidence:root",)),
        ("evidence.read", frozenset(), _ARGUMENTS, ("evidence:root",)),
        ("evidence.read", _ROLES, frozenset({"bad key"}), ("evidence:root",)),
        ("evidence.read", _ROLES, _ARGUMENTS, ("evidence:z", "evidence:a")),
        ("evidence.read", _ROLES, _ARGUMENTS, ("evidence:a", "evidence:a")),
        ("evidence.read", _ROLES, _ARGUMENTS, ("",)),
    ),
)
def test_binding_rejects_exact_invalid_authority(
    name: str,
    roles: frozenset[AutonomousAgentRole],
    arguments: frozenset[str],
    refs: tuple[str, ...],
) -> None:
    # Given / When / Then: every binding declaration is checked before runtime installation.
    with pytest.raises(AutonomousToolRuntimeError, match=r"^autonomous_tool_binding_invalid$"):
        AutonomousToolBinding(
            name=name,
            allowed_roles=roles,
            allowed_arguments=arguments,
            invoke=lambda arguments: "{}",
            evidence_refs=refs,
        )


@pytest.mark.parametrize("argument_name", ("API_KEY", "SecretValue", "AUTHORIZATION", "credentialId"))
def test_secret_argument_names_are_denied_before_invocation(argument_name: str) -> None:
    # Given: an authorized tool would expose whether host code ran.
    invoked: list[str] = []

    def invoke(arguments: AutonomousToolArguments) -> str:
        invoked.append("called")
        return "{}"

    runtime = AutonomousToolRuntime((_binding(invoke),), clock=lambda: NOW)

    # When / Then: case-insensitive secret names cannot cross the host boundary.
    with pytest.raises(AutonomousToolRuntimeError, match=r"^autonomous_tool_authority_denied$"):
        runtime.dispatch(AutonomousAgentRole.MARKET_OBSERVER, _call(**{argument_name: "denied"}))
    assert invoked == []


def test_dispatch_cannot_observe_post_validation_argument_changes() -> None:
    # Given: a caller changes its original mutable mapping after structured argument parsing.
    raw = {"evidence_id": "a" * 64}
    arguments = AutonomousToolArguments(raw)
    raw["evidence_id"] = "changed-after-validation"
    observed: list[dict[str, str]] = []

    def invoke(values: AutonomousToolArguments) -> str:
        observed.append(dict(values.root))
        return "{}"

    runtime = AutonomousToolRuntime((_binding(invoke),), clock=lambda: NOW)
    call = AutonomousToolCall(
        tool_name="evidence.read",
        args=arguments,
        reason="Read bounded evidence before continuing a research decision.",
    )

    # When / Then: host code sees only the frozen validation-time values.
    runtime.dispatch(AutonomousAgentRole.MARKET_OBSERVER, call)
    assert observed == [{"evidence_id": "a" * 64}]


def test_callback_exception_is_stable_and_does_not_leak_detail() -> None:
    # Given: a plugin violates its typed error contract with sensitive exception text.
    def invoke(arguments: AutonomousToolArguments) -> str:
        raise ValueError("sensitive detail")  # noqa: RUF100 # noqa: GENERIC_ERR_OK: deliberate untyped plugin-boundary fixture validates stable host redaction

    runtime = AutonomousToolRuntime((_binding(invoke),), clock=lambda: NOW)

    # When / Then: the host callback boundary emits only its stable failure reason.
    with pytest.raises(AutonomousToolRuntimeError, match=r"^autonomous_tool_invocation_failed$") as raised:
        runtime.dispatch(AutonomousAgentRole.MARKET_OBSERVER, _call())
    assert "sensitive detail" not in str(raised.value)


def test_runtime_maps_invalid_output_size_and_clock_failures() -> None:
    # Given / When / Then: output and clock boundaries report exact stable reasons.
    invalid = AutonomousToolRuntime((_binding(lambda arguments: "not-json"),), clock=lambda: NOW)
    with pytest.raises(AutonomousToolRuntimeError, match=r"^autonomous_tool_result_invalid$"):
        invalid.dispatch(AutonomousAgentRole.MARKET_OBSERVER, _call())
    oversized = AutonomousToolRuntime((_binding(lambda arguments: '"' + "x" * 16_384 + '"'),), clock=lambda: NOW)
    with pytest.raises(AutonomousToolRuntimeError, match=r"^autonomous_tool_result_too_large$"):
        oversized.dispatch(AutonomousAgentRole.MARKET_OBSERVER, _call())
    clock = AutonomousToolRuntime((_binding(lambda arguments: "{}"),), clock=lambda: dt.datetime(2026, 8, 26, 12, 0))
    with pytest.raises(AutonomousToolRuntimeError, match=r"^autonomous_tool_clock_invalid$"):
        clock.dispatch(AutonomousAgentRole.MARKET_OBSERVER, _call())
