from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass

import pytest
from pydantic import TypeAdapter, ValidationError

from tests.test_autonomous_memory_store import record_fixture
from tests.test_autonomous_task_models import NOW, budget, step_fixture, task_fixture
from trading_agent.autonomous_reasoning import (
    AutonomousDefer,
    AutonomousDelegate,
    AutonomousReasoningRequest,
    AutonomousStructuredReasoner,
    AutonomousSubmitArtifact,
    AutonomousToolArguments,
    AutonomousToolCall,
    AutonomousToolObservation,
    InvalidAutonomousReasoningError,
    validate_reasoning_response,
)
from trading_agent.autonomous_task_models import AutonomousAgentRole


def _call(arguments: AutonomousToolArguments | None = None) -> AutonomousToolCall:
    return AutonomousToolCall(
        tool_name="evidence.read",
        args=arguments or AutonomousToolArguments({"evidence_id": "a" * 64}),
        reason="Read bounded evidence before continuing a research decision.",
    )


def _request() -> AutonomousReasoningRequest:
    return AutonomousReasoningRequest(
        now=NOW,
        task=task_fixture(),
        prior_steps=(step_fixture(),),
        observations=(),
        memories=(record_fixture(),),
        allowed_tool_names=("evidence.read",),
        remaining_budget=budget(),
        current_role=AutonomousAgentRole.MARKET_OBSERVER,
    )


@pytest.mark.parametrize(
    "arguments",
    (
        {str(index): "value" for index in range(9)},
        {"valid": ""},
        {"valid": "v" * 501},
        {"bad key": "value"},
    ),
)
def test_tool_arguments_reject_exact_invalid_bounds(arguments: dict[str, str]) -> None:
    # Given / When / Then: malformed host arguments fail at their typed boundary.
    with pytest.raises(InvalidAutonomousReasoningError, match=r"^autonomous_tool_arguments_invalid$"):
        AutonomousToolArguments(arguments)


def test_tool_arguments_copy_input_and_reject_root_mutation() -> None:
    # Given: a caller retains the original mutable input after parsing.
    raw = {"evidence_id": "a" * 64}
    arguments = AutonomousToolArguments(raw)
    raw["changed"] = "after-validation"

    # When / Then: neither direct root mutation nor original input mutation can alter the call.
    mutation = "__setitem__"
    with pytest.raises(AttributeError):
        getattr(arguments.root, mutation)("changed", "denied")
    assert arguments.root == {"evidence_id": "a" * 64}
    assert _call(arguments).args.root == {"evidence_id": "a" * 64}


def test_observation_rejects_exact_canonical_and_hash_failures() -> None:
    # Given: a valid canonical call and output have independent hashes.
    call = _call()
    call_json = json.dumps(call.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    output = '{"status":"current"}'
    content_hash = hashlib.sha256(output.encode()).hexdigest()
    values = {
        "tool_name": call.tool_name,
        "call_json": call_json,
        "bounded_json": output,
        "evidence_refs": (content_hash,),
        "observed_at": NOW,
        "call_sha256": hashlib.sha256(call_json.encode()).hexdigest(),
        "content_sha256": content_hash,
    }

    # When / Then: each independently tampered identity field fails closed.
    for update, reason in (
        ({"call_sha256": "0" * 64}, "autonomous_observation_hash_invalid"),
        ({"content_sha256": "0" * 64}, "autonomous_observation_hash_invalid"),
        ({"evidence_refs": ("a" * 64,)}, "autonomous_observation_hash_invalid"),
        ({"call_json": call_json.replace(',"kind"', ', "kind"')}, "autonomous_observation_call_invalid"),
        ({"bounded_json": '{"status": "current"}'}, "autonomous_observation_json_invalid"),
    ):
        with pytest.raises(InvalidAutonomousReasoningError, match=rf"^{reason}$"):
            AutonomousToolObservation.model_validate({**values, **update})


def test_request_boundary_rejects_nonfuture_wakes_and_same_role_delegate() -> None:
    # Given: model-valid responses carry a past/equal time wake or a same-role handoff.
    request = _request()
    for response in (
        AutonomousDefer(
            reason="Wait for the next completed bar before resuming the bounded analysis.",
            resume_condition="A completed market bar is available for the current session.",
            next_wake_at=NOW,
        ),
        AutonomousSubmitArtifact(
            artifact_kind="no_trade",
            artifact_json="{}",
            evidence_refs=("evidence:root",),
            next_wake_at=NOW - dt.timedelta(seconds=1),
            reason="No trade remains nonterminal until a current-session evidence event arrives.",
        ),
        AutonomousDelegate(
            role=AutonomousAgentRole.MARKET_OBSERVER,
            objective="Inspect the durable evidence without creating a same-role authority loop.",
            reason="The current request names its role and therefore prohibits the duplicate handoff.",
        ),
    ):
        # When / Then: the request-aware validator rejects only the unsafe transition.
        with pytest.raises(InvalidAutonomousReasoningError):
            validate_reasoning_response(request, response)


def test_wake_models_require_exact_selector_and_forbid_artifact_wakes() -> None:
    # Given / When / Then: wake selector rules are enforced before request-time comparison.
    for response, reason in (
        (lambda: AutonomousDefer(
            reason="Wait for the next completed bar before resuming the bounded analysis.",
            resume_condition="A completed market bar is available for the current session.",
        ), "autonomous_defer_wake_required"),
        (lambda: AutonomousDefer(
            reason="Wait for the next completed bar before resuming the bounded analysis.",
            resume_condition="A completed market bar is available for the current session.",
            next_wake_at=NOW + dt.timedelta(minutes=1),
            next_wake_event="completed_bar",
        ), "autonomous_defer_wake_required"),
        (lambda: AutonomousSubmitArtifact(
            artifact_kind="no_trade",
            artifact_json="{}",
            evidence_refs=("evidence:root",),
            reason="No trade remains nonterminal until a current-session evidence event arrives.",
        ), "autonomous_no_trade_wake_required"),
        (lambda: AutonomousSubmitArtifact(
            artifact_kind="no_trade",
            artifact_json="{}",
            evidence_refs=("evidence:root",),
            next_wake_at=NOW + dt.timedelta(minutes=1),
            next_wake_event="completed_bar",
            reason="No trade remains nonterminal until a current-session evidence event arrives.",
        ), "autonomous_no_trade_wake_required"),
        (lambda: AutonomousSubmitArtifact(
            artifact_kind="context",
            artifact_json="{}",
            evidence_refs=("evidence:root",),
            next_wake_event="completed_bar",
            reason="A terminal context artifact cannot carry a continuation wake selector.",
        ), "autonomous_artifact_wake_forbidden"),
        (lambda: AutonomousSubmitArtifact(
            artifact_kind="review",
            artifact_json="{}",
            evidence_refs=("evidence:root",),
            next_wake_at=NOW + dt.timedelta(minutes=1),
            reason="A review artifact cannot carry a continuation wake selector after submission.",
        ), "autonomous_artifact_wake_forbidden"),
    ):
        with pytest.raises(InvalidAutonomousReasoningError, match=rf"^{reason}$"):
            response()


@dataclass(frozen=True, slots=True)
class _Client:
    response: bytes
    model_id: str = "fixture-boundary-v1"
    seed: int | None = 1
    temperature: float = 0.0

    def complete(self, prompt: str) -> bytes:
        del prompt
        return self.response


def test_reasoner_rejects_past_wake_from_provider_response() -> None:
    # Given: the provider emits an otherwise valid defer response tied to the current request.
    response = AutonomousDefer(
        reason="Wait for the next completed bar before resuming the bounded analysis.",
        resume_condition="A completed market bar is available for the current session.",
        next_wake_at=NOW,
    )

    # When / Then: stateless reasoning applies the request-time wake guard.
    with pytest.raises(InvalidAutonomousReasoningError, match=r"^autonomous_response_wake_not_future$"):
        AutonomousStructuredReasoner(_Client(response.model_dump_json().encode())).next_step(_request())


def test_response_union_rejects_invalid_discriminator_extra_and_bounds() -> None:
    # Given: raw provider JSON crosses the strict tagged-union boundary.
    adapter = TypeAdapter(AutonomousToolCall | AutonomousDelegate | AutonomousDefer | AutonomousSubmitArtifact)

    # When / Then: discriminator, extra field, and length failures remain Pydantic validation errors.
    with pytest.raises(ValidationError):
        adapter.validate_json('{"kind":"unknown"}')
    with pytest.raises(ValidationError):
        adapter.validate_json(
            '{"args":{},"extra":true,"kind":"tool_call","reason":"long enough","tool_name":"evidence.read"}'
        )
    with pytest.raises(ValidationError):
        adapter.validate_json('{"args":{},"kind":"tool_call","reason":"short","tool_name":"evidence.read"}')
