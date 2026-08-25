from __future__ import annotations

import datetime as dt
import hashlib
import json
from enum import StrEnum, unique
from typing import Annotated, Final, Literal, NewType, Self, assert_never

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from trading_agent.dashboard_agent_family import AgentFamilyId
from trading_agent.research_agent_cycle_models import EvidenceId, MarketId

AutonomousTaskId = NewType("AutonomousTaskId", str)
AutonomousStepId = NewType("AutonomousStepId", str)
_HASH: Final = r"^[a-f0-9]{64}$"
_HashEvidence = Annotated[EvidenceId, Field(pattern=_HASH)]
_HashTask = Annotated[AutonomousTaskId, Field(pattern=_HASH)]
_HashStep = Annotated[AutonomousStepId, Field(pattern=_HASH)]


@unique
class AutonomousAgentRole(StrEnum):
    SUPERVISOR = "supervisor"
    MARKET_OBSERVER = "market_observer"
    OPPORTUNITY = "opportunity"
    TRADING = "trading"
    POSITION = "position"
    RESEARCH = "research"
    CRITIC = "critic"
    LOOP_ENGINEER = "loop_engineer"


@unique
class AutonomousTaskState(StrEnum):
    QUEUED = "queued"
    OBSERVING = "observing"
    RESEARCHING = "researching"
    DELIBERATING = "deliberating"
    ACTING = "acting"
    WAITING_EVENT = "waiting_event"
    WAITING_TIME = "waiting_time"
    BLOCKED = "blocked"
    EVALUATING = "evaluating"
    LEARNING = "learning"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class InvalidAutonomousTaskFieldError(ValueError):
    __slots__ = ("reason",)

    reason: str

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)

    def __str__(self) -> str:
        return self.reason


class AutonomousRunBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    remaining_model_calls: int = Field(ge=0, le=12)
    remaining_tool_calls: int = Field(ge=0, le=24)
    remaining_runtime_seconds: int = Field(ge=0, le=300)


class AutonomousResearchTask(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    schema_version: Literal[1] = 1
    task_id: _HashTask
    goal: str = Field(min_length=8, max_length=2_000)
    owner_role: AutonomousAgentRole
    agent_family_id: AgentFamilyId
    market_scope: MarketId
    state: AutonomousTaskState
    priority: int = Field(ge=0, le=100)
    root_source_evidence_id: _HashEvidence
    source_evidence_ids: tuple[_HashEvidence, ...] = Field(min_length=1, max_length=128)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=128)
    subject_refs: tuple[str, ...] = Field(default=(), max_length=32)
    working_memory_ids: tuple[str, ...] = Field(default=(), max_length=128)
    current_plan: tuple[str, ...] = Field(min_length=1, max_length=32)
    completed_actions: tuple[str, ...] = Field(default=(), max_length=128)
    pending_actions: tuple[str, ...] = Field(default=(), max_length=128)
    next_wake_at: AwareDatetime | None = None
    next_wake_event: str | None = Field(default=None, min_length=1, max_length=160)
    blocked_reason: str | None = Field(default=None, min_length=1, max_length=160)
    retry_count: int = Field(default=0, ge=0)
    agent_version: str = Field(min_length=1, max_length=160)
    created_at: AwareDatetime
    updated_at: AwareDatetime
    terminal_reason: str | None = Field(default=None, min_length=1, max_length=160)

    @field_validator("created_at", "updated_at", "next_wake_at", mode="after")
    @classmethod
    def normalize_time(cls, value: dt.datetime | None) -> dt.datetime | None:
        return None if value is None else value.astimezone(dt.UTC)

    @model_validator(mode="after")
    def require_invariants(self) -> Self:
        if self.task_id != autonomous_task_id(self.agent_family_id, self.market_scope, self.root_source_evidence_id):
            raise InvalidAutonomousTaskFieldError(reason="task_id_identity_mismatch")
        _require_sorted_unique(self.source_evidence_ids, reason="sorted_unique_source_evidence_ids_required")
        _require_sorted_unique(self.evidence_refs, reason="sorted_unique_evidence_refs_required")
        _require_sorted_unique(self.subject_refs, reason="sorted_unique_subject_refs_required")
        _require_sorted_unique(self.working_memory_ids, reason="sorted_unique_working_memory_ids_required")
        _require_sorted_unique(self.current_plan, reason="sorted_unique_current_plan_required")
        _require_sorted_unique(self.completed_actions, reason="sorted_unique_completed_actions_required")
        _require_sorted_unique(self.pending_actions, reason="sorted_unique_pending_actions_required")
        if self.root_source_evidence_id not in self.source_evidence_ids:
            raise InvalidAutonomousTaskFieldError(reason="root_source_evidence_required")
        if self.updated_at < self.created_at:
            raise InvalidAutonomousTaskFieldError(reason="task_time_order_invalid")
        _require_state_invariants(
            state=self.state,
            updated_at=self.updated_at,
            next_wake_at=self.next_wake_at,
            next_wake_event=self.next_wake_event,
            blocked_reason=self.blocked_reason,
            terminal_reason=self.terminal_reason,
            actions=self.completed_actions + self.pending_actions,
        )
        return self


class AutonomousTaskStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    schema_version: Literal[1] = 1
    step_id: _HashStep = Field(default_factory=lambda: AutonomousStepId(""))
    task_id: _HashTask
    sequence: int = Field(ge=1)
    role: AutonomousAgentRole
    agent_family_id: AgentFamilyId
    market_scope: MarketId
    root_source_evidence_id: _HashEvidence
    agent_version: str = Field(min_length=1, max_length=160)
    state: AutonomousTaskState
    payload_json: str = Field(default="{}", min_length=2, max_length=16_384)
    source_evidence_ids: tuple[_HashEvidence, ...] = Field(min_length=1, max_length=128)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=128)
    working_memory_ids: tuple[str, ...] = Field(default=(), max_length=128)
    budget: AutonomousRunBudget
    occurred_at: AwareDatetime
    next_wake_at: AwareDatetime | None = None
    next_wake_event: str | None = Field(default=None, min_length=1, max_length=160)
    terminal_reason: str | None = Field(default=None, min_length=1, max_length=160)

    @field_validator("occurred_at", "next_wake_at", mode="after")
    @classmethod
    def normalize_time(cls, value: dt.datetime | None) -> dt.datetime | None:
        return None if value is None else value.astimezone(dt.UTC)

    @model_validator(mode="after")
    def require_invariants_and_identity(self) -> Self:
        if self.task_id != autonomous_task_id(self.agent_family_id, self.market_scope, self.root_source_evidence_id):
            raise InvalidAutonomousTaskFieldError(reason="step_task_identity_mismatch")
        if self.root_source_evidence_id not in self.source_evidence_ids:
            raise InvalidAutonomousTaskFieldError(reason="step_root_source_evidence_required")
        _require_sorted_unique(self.source_evidence_ids, reason="sorted_unique_source_evidence_ids_required")
        _require_sorted_unique(self.evidence_refs, reason="sorted_unique_evidence_refs_required")
        _require_sorted_unique(self.working_memory_ids, reason="sorted_unique_working_memory_ids_required")
        _validate_payload(self.payload_json)
        if self.state in {AutonomousTaskState.WAITING_TIME, AutonomousTaskState.BLOCKED}:
            _require_wake(self.occurred_at, self.next_wake_at, self.next_wake_event)
        elif self.state is AutonomousTaskState.WAITING_EVENT:
            if (
                self.next_wake_at is not None
                or self.next_wake_event is None
                or self.terminal_reason is not None
            ):
                raise InvalidAutonomousTaskFieldError(reason="waiting_event_wake_invalid")
        elif self.state in {AutonomousTaskState.COMPLETED, AutonomousTaskState.ABANDONED}:
            if self.next_wake_at is not None or self.next_wake_event is not None or self.terminal_reason is None:
                raise InvalidAutonomousTaskFieldError(reason="terminal_step_fields_invalid")
        elif self.next_wake_at is not None or self.next_wake_event is not None or self.terminal_reason is not None:
            raise InvalidAutonomousTaskFieldError(reason="active_step_terminal_fields_invalid")
        if self.step_id:
            expected = autonomous_step_id(self)
            if self.step_id != expected:
                raise InvalidAutonomousTaskFieldError(reason="step_id_mismatch")
        else:
            object.__setattr__(self, "step_id", autonomous_step_id(self))
        return self


class AutonomousSupervisorTickResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    status: Literal["idle", "waiting", "completed", "blocked", "failed"]
    task_id: _HashTask | None = None
    agent_family_id: AgentFamilyId | None = None
    market_scope: MarketId | None = None
    model_calls: int = Field(default=0, ge=0, le=12)
    tool_calls: int = Field(default=0, ge=0, le=24)
    next_wake_at: AwareDatetime | None = None
    next_wake_event: str | None = Field(default=None, min_length=1, max_length=160)

    @field_validator("next_wake_at", mode="after")
    @classmethod
    def normalize_time(cls, value: dt.datetime | None) -> dt.datetime | None:
        return None if value is None else value.astimezone(dt.UTC)

    @model_validator(mode="after")
    def require_result_invariants(self) -> Self:
        has_identity = self.task_id is not None or self.agent_family_id is not None or self.market_scope is not None
        if has_identity and (self.task_id is None or self.agent_family_id is None or self.market_scope is None):
            raise InvalidAutonomousTaskFieldError(reason="tick_identity_incomplete")
        if self.status != "idle" and not has_identity:
            raise InvalidAutonomousTaskFieldError(reason="tick_identity_required")
        if self.status == "idle" and (
            has_identity or self.next_wake_at is not None or self.next_wake_event is not None
        ):
            raise InvalidAutonomousTaskFieldError(reason="idle_result_fields_invalid")
        if self.status == "waiting":
            if not has_identity or (self.next_wake_at is None) == (self.next_wake_event is None):
                raise InvalidAutonomousTaskFieldError(reason="waiting_result_wake_required")
        elif self.status in {"completed", "failed"} and (
            self.next_wake_at is not None or self.next_wake_event is not None
        ):
            raise InvalidAutonomousTaskFieldError(reason="terminal_result_wake_invalid")
        elif self.status == "blocked" and (
            not has_identity or (self.next_wake_at is None) == (self.next_wake_event is None)
        ):
            raise InvalidAutonomousTaskFieldError(reason="blocked_result_wake_required")
        return self


def autonomous_task_id(family: AgentFamilyId, market: MarketId, root: EvidenceId) -> AutonomousTaskId:
    material = f"{family}:{market}:{root}:autonomous-task-v1"
    return AutonomousTaskId(hashlib.sha256(material.encode()).hexdigest())


def autonomous_step_id(step: AutonomousTaskStep) -> AutonomousStepId:
    return AutonomousStepId(hashlib.sha256(autonomous_step_payload(step).encode()).hexdigest())


def autonomous_step_payload(step: AutonomousTaskStep) -> str:
    return json.dumps(
        step.model_dump(mode="json", exclude={"step_id"}, exclude_none=True),
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _require_sorted_unique(values: tuple[str, ...], *, reason: str) -> None:
    if values != tuple(sorted(set(values))) or any(not value for value in values):
        raise InvalidAutonomousTaskFieldError(reason=reason)


def _require_wake(
    reference_time: dt.datetime,
    wake_at: dt.datetime | None,
    wake_event: str | None,
) -> None:
    if (wake_at is None) == (wake_event is None) or (wake_at is not None and wake_at <= reference_time):
        raise InvalidAutonomousTaskFieldError(reason="future_wake_selector_required")


def _require_state_invariants(
    *,
    state: AutonomousTaskState,
    updated_at: dt.datetime,
    next_wake_at: dt.datetime | None,
    next_wake_event: str | None,
    blocked_reason: str | None,
    terminal_reason: str | None,
    actions: tuple[str, ...],
) -> None:
    no_action = {action.lower().replace("-", "_") for action in actions} & {"no_action", "no_trade"}
    match state:
        case AutonomousTaskState.WAITING_EVENT:
            if next_wake_at is not None or next_wake_event is None or terminal_reason is not None:
                raise InvalidAutonomousTaskFieldError(reason="waiting_event_fields_invalid")
        case AutonomousTaskState.WAITING_TIME:
            if terminal_reason is not None:
                raise InvalidAutonomousTaskFieldError(reason="waiting_time_terminal_reason_invalid")
            _require_wake(updated_at, next_wake_at, next_wake_event)
        case AutonomousTaskState.BLOCKED:
            if terminal_reason is not None or blocked_reason is None:
                raise InvalidAutonomousTaskFieldError(reason="blocked_fields_invalid")
            _require_wake(updated_at, next_wake_at, next_wake_event)
        case AutonomousTaskState.COMPLETED | AutonomousTaskState.ABANDONED:
            if next_wake_at is not None or next_wake_event is not None or terminal_reason is None or no_action:
                raise InvalidAutonomousTaskFieldError(reason="terminal_fields_invalid")
        case (
            AutonomousTaskState.QUEUED
            | AutonomousTaskState.OBSERVING
            | AutonomousTaskState.RESEARCHING
            | AutonomousTaskState.DELIBERATING
            | AutonomousTaskState.ACTING
            | AutonomousTaskState.EVALUATING
            | AutonomousTaskState.LEARNING
        ):
            if (
                next_wake_at is not None
                or next_wake_event is not None
                or terminal_reason is not None
                or blocked_reason is not None
            ):
                raise InvalidAutonomousTaskFieldError(reason="active_fields_invalid")
        case unreachable:
            assert_never(unreachable)


def _validate_payload(payload_json: str) -> None:
    try:
        decoded = json.loads(payload_json)
        canonical = json.dumps(decoded, allow_nan=False, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError):
        raise InvalidAutonomousTaskFieldError(reason="step_payload_json_invalid") from None
    if canonical != payload_json:
        raise InvalidAutonomousTaskFieldError(reason="step_payload_json_not_canonical")


__all__ = (
    "AutonomousAgentRole",
    "AutonomousResearchTask",
    "AutonomousRunBudget",
    "AutonomousStepId",
    "AutonomousSupervisorTickResult",
    "AutonomousTaskId",
    "AutonomousTaskState",
    "AutonomousTaskStep",
    "InvalidAutonomousTaskFieldError",
    "autonomous_step_id",
    "autonomous_step_payload",
    "autonomous_task_id",
)
