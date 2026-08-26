from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field

import pytest
from pydantic import TypeAdapter, ValidationError

from tests.test_autonomous_memory_store import record_fixture
from tests.test_autonomous_task_models import NOW, budget, step_fixture, task_fixture
from trading_agent.autonomous_memory_models import AutonomousMemoryRecord, AutonomousMemoryScope
from trading_agent.autonomous_reasoning import (
    AutonomousComplete,
    AutonomousDefer,
    AutonomousDelegate,
    AutonomousReasoningRequest,
    AutonomousRecordMemory,
    AutonomousStructuredReasoner,
    AutonomousSubmitArtifact,
    AutonomousToolArguments,
    AutonomousToolCall,
    AutonomousToolObservation,
    InvalidAutonomousReasoningError,
    canonical_reasoning_prompt,
)
from trading_agent.autonomous_task_models import (
    AutonomousAgentRole,
    AutonomousResearchTask,
    AutonomousRunBudget,
    AutonomousTaskStep,
)

_ADAPTER = TypeAdapter(
    AutonomousToolCall
    | AutonomousDelegate
    | AutonomousSubmitArtifact
    | AutonomousRecordMemory
    | AutonomousDefer
    | AutonomousComplete
)
type _RequestValue = (
    dt.datetime
    | AutonomousResearchTask
    | tuple[AutonomousTaskStep, ...]
    | tuple[AutonomousToolObservation, ...]
    | tuple[AutonomousMemoryRecord, ...]
    | tuple[str, ...]
    | AutonomousRunBudget
    | AutonomousAgentRole
    | None
)


def _call() -> AutonomousToolCall:
    return AutonomousToolCall(
        tool_name="evidence.read",
        args=AutonomousToolArguments({"evidence_id": "a" * 64}),
        reason="Inspect root evidence before making a bounded research decision.",
    )


def _request(**updates: _RequestValue) -> AutonomousReasoningRequest:
    values: dict[str, _RequestValue] = {
        "now": NOW,
        "task": task_fixture(),
        "prior_steps": (step_fixture(),),
        "observations": (),
        "memories": (record_fixture(),),
        "allowed_tool_names": ("evidence.read",),
        "allowed_tool_signatures": ("evidence.read(evidence_id)",),
        "remaining_budget": budget(),
        "current_role": AutonomousAgentRole.MARKET_OBSERVER,
    }
    values.update(updates)
    return AutonomousReasoningRequest.model_validate(values)


@pytest.mark.parametrize(
    ("response", "kind"),
    (
        (_call(), "tool_call"),
        (
            AutonomousDelegate(
                role=AutonomousAgentRole.RESEARCH,
                objective="Assess the evidence-linked catalyst hypothesis for disconfirming conditions.",
                reason="A dedicated research review can narrow the bounded evidence uncertainty.",
            ),
            "delegate",
        ),
        (
            AutonomousSubmitArtifact(
                artifact_kind="context",
                artifact_json='{"symbol":"NVDA"}',
                evidence_refs=("evidence:root",),
                reason="The bounded context artifact preserves the current evidence assessment.",
            ),
            "submit_artifact",
        ),
        (
            AutonomousRecordMemory(
                scope=AutonomousMemoryScope.MARKET,
                memory_key="market.nvda.catalyst-v1",
                summary="The evidence-linked catalyst requires confirmation before any recommendation.",
                fact_refs=("fact:catalyst",),
                subject_refs=("symbol:NVDA",),
                evidence_refs=("evidence:root",),
                reason="The durable fact is relevant to the next evidence-bounded task version.",
            ),
            "record_memory",
        ),
        (
            AutonomousDefer(
                reason="Wait for the next completed session bar before continuing the evaluation.",
                resume_condition="A current-session completed bar is available for the selected symbol.",
                next_wake_event="completed_bar",
            ),
            "defer",
        ),
        (
            AutonomousComplete(
                summary="The evidence review is complete and has a durable outcome reference.",
                completion_evidence_refs=("evidence:root",),
                reason="All bounded actions have completed and the outcome is fully evidenced.",
            ),
            "complete",
        ),
    ),
)
def test_response_variants_round_trip_through_strict_union(
    response: AutonomousToolCall
    | AutonomousDelegate
    | AutonomousSubmitArtifact
    | AutonomousRecordMemory
    | AutonomousDefer
    | AutonomousComplete,
    kind: str,
) -> None:
    # Given: each response kind has its minimum authority-bearing fields.
    payload = json.dumps(response.model_dump(mode="json"), separators=(",", ":"))

    # When: an untrusted structured response crosses the union boundary.
    parsed = _ADAPTER.validate_json(payload)

    # Then: the discriminator preserves the exact response variant.
    assert parsed.kind == kind


def test_models_reject_extra_invalid_wakes_unsorted_refs_and_invalid_delegation() -> None:
    # Given / When / Then: each malformed authority shape is rejected at its boundary.
    with pytest.raises(ValidationError):
        _ADAPTER.validate_json('{"kind":"unknown"}')
    with pytest.raises(ValidationError):
        _call().model_validate({**_call().model_dump(), "extra": "denied"})
    with pytest.raises((InvalidAutonomousReasoningError, ValidationError)):
        AutonomousToolArguments({"bad key": "value"})
    with pytest.raises((InvalidAutonomousReasoningError, ValidationError)):
        AutonomousDelegate(
            role=AutonomousAgentRole.SUPERVISOR,
            objective="A valid objective that should still be denied by the role boundary.",
            reason="The caller must not create a hidden supervisory delegation authority.",
        )
    with pytest.raises((InvalidAutonomousReasoningError, ValidationError)):
        AutonomousSubmitArtifact(
            artifact_kind="no_trade",
            artifact_json="{}",
            evidence_refs=("evidence:root",),
            reason="No trade remains nonterminal and must name a single continuation wake.",
        )
    with pytest.raises((InvalidAutonomousReasoningError, ValidationError)):
        AutonomousComplete(
            summary="A completion without evidence is invalid and cannot become terminal.",
            completion_evidence_refs=(),
            reason="Completion requires durable evidence that makes the terminal outcome reviewable.",
        )
    with pytest.raises((InvalidAutonomousReasoningError, ValidationError)):
        AutonomousRecordMemory(
            scope=AutonomousMemoryScope.MARKET,
            memory_key="market.nvda.catalyst-v1",
            summary="A memory needs a fact or inference linked to durable evidence.",
            evidence_refs=("evidence:z", "evidence:a"),
            reason="The memory request must retain canonical supporting references.",
        )


def test_request_prompt_is_deterministic_and_validates_observation_attribution() -> None:
    # Given: a canonical observation carries both call and content hashes.
    call = _call()
    call_json = json.dumps(call.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    output = '{"status":"current"}'
    observation = AutonomousToolObservation(
        tool_name=call.tool_name,
        call_json=call_json,
        bounded_json=output,
        evidence_refs=(hashlib.sha256(output.encode()).hexdigest(),),
        observed_at=NOW,
        call_sha256=hashlib.sha256(call_json.encode()).hexdigest(),
        content_sha256=hashlib.sha256(output.encode()).hexdigest(),
    )
    request = _request(observations=(observation,))

    # When: the durable request is rendered twice without any provider session state.
    first = canonical_reasoning_prompt(request)
    second = canonical_reasoning_prompt(request)

    # Then: routing fields, schema, hashes, and rehydrated observations are machine-readable and stable.
    payload = json.loads(first)
    assert first == second
    assert payload["schema_version"] == 2
    assert payload["allowed_tool_names"] == ["evidence.read"]
    assert payload["allowed_tool_signatures"] == ["evidence.read(evidence_id)"]
    assert payload["provider"]["model_id"] == "unbound"
    assert payload["observations"][0]["call_sha256"] == observation.call_sha256
    assert "oneOf" in payload["response_schema"]
    with pytest.raises(InvalidAutonomousReasoningError):
        _request(observations=(observation.model_copy(update={"tool_name": "not.allowed"}),))
    with pytest.raises(InvalidAutonomousReasoningError):
        _request(allowed_tool_signatures=("browser.status()",))


@dataclass(frozen=True, slots=True)
class _Client:
    responses: tuple[bytes, ...]
    model_id: str = "fixture-reasoner-v1"
    seed: int | None = 7
    temperature: float = 0.0
    prompts: list[str] = field(default_factory=list, compare=False)
    fail_on_call: int | None = None

    def complete(self, prompt: str) -> bytes:
        self.prompts.append(prompt)
        if self.fail_on_call == len(self.prompts):
            from trading_agent.researcher_llm import ResearcherLlmError

            raise ResearcherLlmError
        return self.responses[len(self.prompts) - 1]


def test_structured_reasoner_rehydrates_state_without_session_and_hides_failures() -> None:
    # Given: sequential raw responses use only the request state supplied per invocation.
    first = _call().model_dump_json().encode()
    second = (
        AutonomousComplete(
            summary="The observed evidence is now sufficient to close the bounded research task.",
            completion_evidence_refs=("evidence:root",),
            reason="The durable observation is present in the second stateless request payload.",
        )
        .model_dump_json()
        .encode()
    )
    invalid = b"not-json"
    delegate = (
        AutonomousDelegate(
            role=AutonomousAgentRole.MARKET_OBSERVER,
            objective="A same-role delegation must not bypass the explicit current-role boundary.",
            reason="The request explicitly exposes its role, so identical delegation is unauthorized.",
        )
        .model_dump_json()
        .encode()
    )
    client = _Client((first, second, invalid, delegate), fail_on_call=5)
    reasoner = AutonomousStructuredReasoner(client)

    # When: a first request asks for a tool and a second request supplies its observation.
    assert reasoner.next_step(_request()).kind == "tool_call"
    assert reasoner.next_step(_request(observations=())).kind == "complete"

    # Then: each call is independent, and malformed provider output maps to a stable error.
    assert len(client.prompts) == 2
    with pytest.raises(InvalidAutonomousReasoningError, match="autonomous_reasoning_response_invalid"):
        reasoner.next_step(_request())
    with pytest.raises(InvalidAutonomousReasoningError, match="autonomous_delegate_role_denied"):
        reasoner.next_step(_request())
    with pytest.raises(InvalidAutonomousReasoningError, match="autonomous_reasoning_response_invalid"):
        reasoner.next_step(_request())
