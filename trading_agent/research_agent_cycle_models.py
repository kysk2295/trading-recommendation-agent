from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum, unique
from typing import Literal, NewType, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from trading_agent.dashboard_agent_family import AgentFamilyId

EvidenceId = NewType("EvidenceId", str)
CycleId = NewType("CycleId", str)
ActionId = NewType("ActionId", str)
DecisionId = NewType("DecisionId", str)
ResultId = NewType("ResultId", str)

MarketId = Literal["us_equities", "kr_equities", "cross_market", "none"]


@dataclass(frozen=True, slots=True)
class InvalidResearchAgentCycleFieldError(ValueError):
    reason: str

    def __str__(self) -> str:
        return self.reason


@unique
class ResearchAgentTriggerKind(StrEnum):
    NEW_DATA = "new_data"
    MARKET_EVENT = "market_event"
    EXPERIMENT_RESULT = "experiment_result"
    REVIEWER_FEEDBACK = "reviewer_feedback"
    SCHEDULED_WAKE = "scheduled_wake"
    OPEN_WORK = "open_work"


@unique
class ResearchAgentDecisionKind(StrEnum):
    INVESTIGATE_CANDIDATE = "investigate_candidate"
    PROPOSE_HYPOTHESIS = "propose_hypothesis"
    RUN_LIGHT_EXPERIMENT = "run_light_experiment"
    REQUEST_HEAVY_EXPERIMENT = "request_heavy_experiment"
    PUBLISH_CONTEXT = "publish_context"
    PUBLISH_RECOMMENDATION = "publish_recommendation"
    REVIEW_OPEN_STATE = "review_open_state"
    NO_ACTION = "no_action"


@unique
class ResearchAgentWakeKind(StrEnum):
    NEW_EVIDENCE = "new_evidence"
    SCHEDULED = "scheduled"
    OPEN_WORK = "open_work"
    TERMINAL = "terminal"


@unique
class ResearchAgentCycleState(StrEnum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    BLOCKED = "blocked"


@unique
class ResearchAgentResultStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    NO_ACTION = "no_action"


@unique
class ResearchAgentOpenWorkState(StrEnum):
    OPEN = "open"
    TERMINAL = "terminal"


class ResearchAgentEvidenceV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    evidence_id: EvidenceId = Field(pattern=r"^[a-f0-9]{64}$")
    agent_family_id: AgentFamilyId
    trigger_kind: ResearchAgentTriggerKind
    source_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=32)
    observed_at: AwareDatetime
    available_at: AwareDatetime
    payload_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    market_id: MarketId

    @model_validator(mode="after")
    def require_ordered_evidence(self) -> Self:
        if self.available_at < self.observed_at:
            raise InvalidResearchAgentCycleFieldError(reason="evidence_time_order_invalid")
        _require_references(self.evidence_refs, allow_empty=False)
        return self


class ResearchAgentDecisionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    decision_id: DecisionId = Field(pattern=r"^[a-f0-9]{64}$")
    cycle_id: CycleId = Field(pattern=r"^[a-f0-9]{64}$")
    agent_family_id: AgentFamilyId
    primary_decision: ResearchAgentDecisionKind
    question: str = Field(min_length=8, max_length=500)
    summary: str = Field(min_length=8, max_length=1_000)
    reason: str | None = Field(default=None, min_length=3, max_length=160)
    continuation: str | None = Field(default=None, min_length=8, max_length=500)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=32)
    decided_at: AwareDatetime
    next_wake_kind: ResearchAgentWakeKind
    next_wake_at: AwareDatetime | None

    @model_validator(mode="after")
    def require_decision_invariants(self) -> Self:
        _require_references(self.evidence_refs, allow_empty=False)
        _require_wake_time(self.next_wake_kind, self.next_wake_at)
        if self.primary_decision is ResearchAgentDecisionKind.NO_ACTION and (
            self.reason is None or self.continuation is None
        ):
            raise InvalidResearchAgentCycleFieldError(reason="no_action_continuation_required")
        return self


class ResearchAgentResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    result_id: ResultId = Field(pattern=r"^[a-f0-9]{64}$")
    cycle_id: CycleId = Field(pattern=r"^[a-f0-9]{64}$")
    agent_family_id: AgentFamilyId
    market_id: MarketId
    status: ResearchAgentResultStatus
    question: str = Field(min_length=8, max_length=500)
    summary: str = Field(min_length=8, max_length=1_000)
    reason: str | None = Field(default=None, min_length=3, max_length=160)
    continuation: str | None = Field(default=None, min_length=8, max_length=500)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=32)
    artifact_refs: tuple[str, ...] = Field(max_length=32)
    occurred_at: AwareDatetime
    next_wake_kind: ResearchAgentWakeKind
    next_wake_at: AwareDatetime | None
    order_authority: Literal[False] = False
    lifecycle_authority: Literal[False] = False
    allocation_authority: Literal[False] = False

    @model_validator(mode="after")
    def require_result_invariants(self) -> Self:
        _require_references(self.evidence_refs, allow_empty=False)
        _require_references(self.artifact_refs, allow_empty=True)
        _require_wake_time(self.next_wake_kind, self.next_wake_at)
        return self


class ResearchAgentCycleV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    cycle_id: CycleId = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_id: EvidenceId = Field(pattern=r"^[a-f0-9]{64}$")
    action_request_id: ActionId = Field(pattern=r"^[a-f0-9]{64}$")
    agent_family_id: AgentFamilyId
    cursor_before: int = Field(ge=0)
    state: ResearchAgentCycleState
    started_at: AwareDatetime
    terminal_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def require_cycle_time(self) -> Self:
        is_terminal = self.state is not ResearchAgentCycleState.STARTED
        if is_terminal != (self.terminal_at is not None):
            raise InvalidResearchAgentCycleFieldError(reason="cycle_terminal_time_mismatch")
        if self.terminal_at is not None and self.terminal_at < self.started_at:
            raise InvalidResearchAgentCycleFieldError(reason="cycle_time_order_invalid")
        return self


class ResearchAgentOpenWorkV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    work_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,159}$")
    cycle_id: CycleId = Field(pattern=r"^[a-f0-9]{64}$")
    agent_family_id: AgentFamilyId
    state: ResearchAgentOpenWorkState
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=32)
    next_wake_at: AwareDatetime | None
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def require_open_work_invariants(self) -> Self:
        _require_references(self.evidence_refs, allow_empty=False)
        if self.state is ResearchAgentOpenWorkState.OPEN and self.next_wake_at is None:
            raise InvalidResearchAgentCycleFieldError(reason="open_work_wake_time_required")
        return self


def research_agent_cycle_id(evidence: ResearchAgentEvidenceV1, *, cursor_before: int) -> CycleId:
    material = (
        f"{evidence.agent_family_id}:{evidence.trigger_kind}:{evidence.evidence_id}:"
        f"{cursor_before}:cycle-v1"
    )
    return CycleId(hashlib.sha256(material.encode()).hexdigest())


def research_agent_action_id(cycle_id: CycleId) -> ActionId:
    return ActionId(hashlib.sha256(f"{cycle_id}:action-v1".encode()).hexdigest())


def research_agent_result_id(cycle_id: CycleId) -> ResultId:
    return ResultId(hashlib.sha256(f"{cycle_id}:result-v1".encode()).hexdigest())


def _require_references(references: tuple[str, ...], *, allow_empty: bool) -> None:
    if not allow_empty and not references:
        raise InvalidResearchAgentCycleFieldError(reason="evidence_reference_required")
    if references != tuple(sorted(set(references))):
        raise InvalidResearchAgentCycleFieldError(reason="sorted_unique_references_required")
    if any(not _safe_reference(reference) for reference in references):
        raise InvalidResearchAgentCycleFieldError(reason="unsafe_reference")


def _safe_reference(reference: str) -> bool:
    return 1 <= len(reference) <= 160 and all(character.isalnum() or character in "._:-" for character in reference)


def _require_wake_time(kind: ResearchAgentWakeKind, wake_at: AwareDatetime | None) -> None:
    if (kind is ResearchAgentWakeKind.SCHEDULED) != (wake_at is not None):
        raise InvalidResearchAgentCycleFieldError(reason="scheduled_wake_time_required")


__all__ = (
    "ActionId",
    "CycleId",
    "DecisionId",
    "EvidenceId",
    "InvalidResearchAgentCycleFieldError",
    "MarketId",
    "ResearchAgentCycleState",
    "ResearchAgentCycleV1",
    "ResearchAgentDecisionKind",
    "ResearchAgentDecisionV1",
    "ResearchAgentEvidenceV1",
    "ResearchAgentOpenWorkState",
    "ResearchAgentOpenWorkV1",
    "ResearchAgentResultStatus",
    "ResearchAgentResultV1",
    "ResearchAgentTriggerKind",
    "ResearchAgentWakeKind",
    "ResultId",
    "research_agent_action_id",
    "research_agent_cycle_id",
    "research_agent_result_id",
)
