from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Annotated, Final, Literal, assert_never

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from trading_agent.autonomous_memory_models import AutonomousMemoryRecord, AutonomousMemoryScope
from trading_agent.autonomous_memory_store import AutonomousMemoryStore
from trading_agent.autonomous_reasoning import (
    AUTONOMOUS_REASONING_RESPONSE_ADAPTER,
    AutonomousReasoningRequest,
    AutonomousReasoningResponse,
    AutonomousToolObservation,
)
from trading_agent.autonomous_task_models import (
    AutonomousAgentRole,
    AutonomousResearchTask,
    AutonomousRunBudget,
    AutonomousSupervisorTickResult,
    AutonomousTaskState,
    AutonomousTaskStep,
)
from trading_agent.research_agent_cycle_models import EvidenceId, ResearchAgentEvidenceV1

_HASH: Final = r"^[a-f0-9]{64}$"
type JsonValue = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
type TickStatus = Literal["waiting", "completed", "blocked", "failed"]


class InvalidAutonomousSupervisorError(RuntimeError):
    __slots__ = ("reason",)

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)

    def __str__(self) -> str:
        return self.reason


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)


class DecisionPayload(_Payload):
    kind: Literal["decision"] = "decision"
    decision_sequence: int = Field(ge=1)
    decision_hash: str = Field(pattern=_HASH)
    response_json: str = Field(min_length=2, max_length=16_000)

    @model_validator(mode="after")
    def require_response(self) -> DecisionPayload:
        response = AUTONOMOUS_REASONING_RESPONSE_ADAPTER.validate_json(self.response_json)
        canonical = canonical_json(response.model_dump(mode="json", exclude_none=True))
        if canonical != self.response_json or hashlib.sha256(canonical.encode()).hexdigest() != self.decision_hash:
            raise InvalidSupervisorPayloadError(reason="decision_payload_invalid")
        return self


class ObservationPayload(_Payload):
    kind: Literal["observation"] = "observation"
    decision_hash: str = Field(pattern=_HASH)
    observation: AutonomousToolObservation


class DelegatePayload(_Payload):
    kind: Literal["delegate"] = "delegate"
    decision_hash: str = Field(pattern=_HASH)
    role: AutonomousAgentRole
    objective: str


class MemoryPayload(_Payload):
    kind: Literal["memory"] = "memory"
    decision_hash: str = Field(pattern=_HASH)
    memory_id: str = Field(pattern=_HASH)
    memory_key: str
    version: int = Field(ge=1)


class ArtifactPayload(_Payload):
    kind: Literal["artifact"] = "artifact"
    decision_hash: str = Field(pattern=_HASH)
    artifact_kind: Literal["context", "hypothesis", "recommendation", "no_trade", "review"]
    artifact_json: str
    evidence_refs: tuple[str, ...]


class WaitPayload(_Payload):
    kind: Literal["wait"] = "wait"
    decision_hash: str | None = Field(default=None, pattern=_HASH)
    cause: Literal["defer", "no_trade", "budget"]
    resume_condition: str | None = None


class CompletionPayload(_Payload):
    kind: Literal["completion"] = "completion"
    decision_hash: str = Field(pattern=_HASH)
    summary: str
    completion_evidence_refs: tuple[str, ...]


class FailurePayload(_Payload):
    kind: Literal["failure"] = "failure"
    decision_hash: str | None = Field(default=None, pattern=_HASH)
    source: Literal["reasoning", "tool", "memory", "supervisor"]
    stable_reason: str
    retry_count: int = Field(ge=1)


class SourceAdmissionPayload(_Payload):
    kind: Literal["source_admission"] = "source_admission"
    evidence_id: EvidenceId = Field(pattern=_HASH)
    evidence_json: str = Field(min_length=2, max_length=16_000)

    @model_validator(mode="after")
    def require_exact_evidence(self) -> SourceAdmissionPayload:
        evidence = ResearchAgentEvidenceV1.model_validate_json(self.evidence_json)
        if (
            evidence.evidence_id != self.evidence_id
            or canonical_json(evidence.model_dump(mode="json")) != self.evidence_json
        ):
            raise InvalidSupervisorPayloadError(reason="source_admission_payload_invalid")
        return self


SupervisorStepPayload = Annotated[
    DecisionPayload
    | ObservationPayload
    | DelegatePayload
    | MemoryPayload
    | ArtifactPayload
    | WaitPayload
    | CompletionPayload
    | FailurePayload
    | SourceAdmissionPayload,
    Field(discriminator="kind"),
]
PAYLOAD_ADAPTER: Final = TypeAdapter(SupervisorStepPayload)


class InvalidSupervisorPayloadError(InvalidAutonomousSupervisorError, ValueError):
    __slots__ = ()


def canonical_json(value: JsonValue) -> str:
    return json.dumps(value, allow_nan=False, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def decision_payload(response: AutonomousReasoningResponse, sequence: int) -> DecisionPayload:
    response_json = canonical_json(response.model_dump(mode="json", exclude_none=True))
    return DecisionPayload(
        decision_sequence=sequence,
        decision_hash=hashlib.sha256(response_json.encode()).hexdigest(),
        response_json=response_json,
    )


def parse_payload(payload_json: str) -> SupervisorStepPayload:
    return PAYLOAD_ADAPTER.validate_json(payload_json)


def payload_json(payload: SupervisorStepPayload) -> str:
    return canonical_json(payload.model_dump(mode="json", exclude_none=True))


def parsed_response(payload: DecisionPayload) -> AutonomousReasoningResponse:
    return AUTONOMOUS_REASONING_RESPONSE_ADAPTER.validate_json(payload.response_json)


def plain_step(
    task: AutonomousResearchTask,
    sequence: int,
    now: dt.datetime,
    state: AutonomousTaskState,
    data: str,
    sources: tuple[EvidenceId, ...],
    refs: tuple[str, ...],
    budget: AutonomousRunBudget | None = None,
    wake: dt.datetime | None = None,
    blocked: str | None = None,
) -> AutonomousTaskStep:
    return AutonomousTaskStep(
        task_id=task.task_id,
        sequence=sequence,
        role=task.owner_role,
        agent_family_id=task.agent_family_id,
        market_scope=task.market_scope,
        root_source_evidence_id=task.root_source_evidence_id,
        agent_version=task.agent_version,
        state=state,
        payload_json=data,
        source_evidence_ids=sources,
        evidence_refs=refs,
        working_memory_ids=task.working_memory_ids,
        budget=budget or run_budget(0, 0, 0),
        occurred_at=now,
        next_wake_at=wake,
        blocked_reason=blocked,
    )


def run_budget(models: int, tools: int, elapsed: float) -> AutonomousRunBudget:
    return AutonomousRunBudget(
        remaining_model_calls=max(0, 8 - models),
        remaining_tool_calls=max(0, 16 - tools),
        remaining_runtime_seconds=max(0, 120 - int(elapsed)),
    )


def safe_payload(step: AutonomousTaskStep) -> SupervisorStepPayload | None:
    try:
        return parse_payload(step.payload_json)
    except (ValidationError, ValueError):
        return None


def unapplied_decision(
    steps: tuple[AutonomousTaskStep, ...],
) -> tuple[AutonomousTaskStep, DecisionPayload] | None:
    applied: set[str | None] = set()
    decisions: list[tuple[AutonomousTaskStep, DecisionPayload]] = []
    for step in steps:
        match safe_payload(step):
            case DecisionPayload() as decision:
                decisions.append((step, decision))
            case SourceAdmissionPayload() | None:
                continue
            case WaitPayload(decision_hash=None) | FailurePayload(decision_hash=None):
                continue
            case (
                ObservationPayload()
                | DelegatePayload()
                | MemoryPayload()
                | ArtifactPayload()
                | CompletionPayload()
                | WaitPayload()
                | FailurePayload()
            ) as payload:
                applied.add(payload.decision_hash)
            case unreachable:
                assert_never(unreachable)
    for step, decision in reversed(decisions):
        if decision.decision_hash not in applied:
            return step, decision
    return None


def linked_memories(store: AutonomousMemoryStore, task: AutonomousResearchTask) -> tuple[AutonomousMemoryRecord, ...]:
    if not task.subject_refs:
        return ()
    records = tuple(
        record
        for scope in AutonomousMemoryScope
        for record in store.reader().search(scope, task.subject_refs, limit=16)
        if set(record.evidence_refs) & set(task.evidence_refs)
    )
    return tuple(sorted(records, key=lambda item: (-item.recorded_at.timestamp(), item.memory_id))[:16])


def reasoning_request(
    store: AutonomousMemoryStore,
    allowed_tools: tuple[str, ...],
    task: AutonomousResearchTask,
    steps: tuple[AutonomousTaskStep, ...],
    now: dt.datetime,
    budget: AutonomousRunBudget,
) -> AutonomousReasoningRequest:
    observations = tuple(
        payload.observation
        for step in steps
        for payload in (safe_payload(step),)
        if isinstance(payload, ObservationPayload)
    )[-16:]
    failures = sum(isinstance(safe_payload(step), FailurePayload) for step in steps)
    return AutonomousReasoningRequest(
        now=now,
        task=task.model_copy(update={"retry_count": failures}),
        prior_steps=steps[-32:],
        observations=observations,
        memories=linked_memories(store, task),
        allowed_tool_names=allowed_tools,
        remaining_budget=budget,
        current_role=task.owner_role,
    )


def tick_result(
    task: AutonomousResearchTask, status: TickStatus, models: int, tools: int
) -> AutonomousSupervisorTickResult:
    return AutonomousSupervisorTickResult(
        status=status,
        task_id=task.task_id,
        agent_family_id=task.agent_family_id,
        market_scope=task.market_scope,
        model_calls=models,
        tool_calls=tools,
        next_wake_at=task.next_wake_at,
        next_wake_event=task.next_wake_event,
    )


def utc_time(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidAutonomousSupervisorError(reason="autonomous_supervisor_clock_invalid")
    return value.astimezone(dt.UTC)
