from __future__ import annotations

import datetime as dt
import hashlib
import json
from enum import StrEnum, unique
from typing import Self, assert_never

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


@unique
class DayAgentTaskState(StrEnum):
    OPEN = "open"
    WAITING = "waiting"
    COMPLETED = "completed"
    BLOCKED = "blocked"


@unique
class DayAgentAction(StrEnum):
    INSPECT_SITUATION = "inspect_situation"
    READ_CATALYSTS = "read_catalysts"
    COMPARE_LEADERS = "compare_leaders"
    SEARCH_PAST_CASES = "search_past_cases"
    RUN_LIGHT_EXPERIMENT = "run_light_experiment"
    ASK_CRITIC = "ask_critic"
    SUBMIT_TRADE_THESIS = "submit_trade_thesis"
    SUBMIT_RESEARCH_HYPOTHESIS = "submit_research_hypothesis"
    DEFER = "defer"


class InvalidDayAgentTaskFieldError(ValueError):
    __slots__ = ("reason",)

    reason: str

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)

    def __str__(self) -> str:
        return self.reason


class DayAgentBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    remaining_model_calls: int = Field(ge=0, le=12)
    remaining_tool_calls: int = Field(ge=0, le=24)
    remaining_runtime_seconds: int = Field(ge=0, le=300)


class DayAgentResearchTask(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: int = Field(default=1, frozen=True)
    task_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,159}$")
    objective: str = Field(min_length=1, max_length=2_000)
    question: str = Field(min_length=1, max_length=2_000)
    current_hypothesis: str | None = Field(default=None, max_length=4_000)
    falsification_conditions: tuple[str, ...] = Field(default=(), max_length=32)
    open_questions: tuple[str, ...] = Field(default=(), max_length=32)
    resume_condition: str | None = Field(default=None, max_length=2_000)
    state: DayAgentTaskState
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=64)
    budget: DayAgentBudget
    created_at: AwareDatetime
    updated_at: AwareDatetime
    scheduled_wake_at: AwareDatetime | None = None
    terminal_reason: str | None = Field(default=None, max_length=160)

    @field_validator("created_at", "updated_at", "scheduled_wake_at", mode="after")
    @classmethod
    def normalize_time(cls, value: dt.datetime | None) -> dt.datetime | None:
        return None if value is None else value.astimezone(dt.UTC)

    @model_validator(mode="after")
    def require_invariants(self) -> Self:
        _require_sorted_unique(self.evidence_refs, reason="sorted_unique_evidence_refs_required")
        _require_sorted_unique(self.falsification_conditions, reason="sorted_unique_falsification_conditions_required")
        _require_sorted_unique(self.open_questions, reason="sorted_unique_open_questions_required")
        if self.updated_at < self.created_at:
            raise InvalidDayAgentTaskFieldError(reason="task_time_order_invalid")
        _require_state_invariants(
            state=self.state,
            budget=self.budget,
            scheduled_wake_at=self.scheduled_wake_at,
            terminal_reason=self.terminal_reason,
            label="task",
        )
        return self


class DayAgentTaskStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: int = Field(default=1, frozen=True)
    step_id: str = Field(default="", pattern=r"^[a-f0-9]{64}$")
    task_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,159}$")
    sequence: int = Field(ge=1)
    action: DayAgentAction
    reason: str = Field(min_length=1, max_length=2_000)
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=64)
    budget: DayAgentBudget
    state: DayAgentTaskState
    occurred_at: AwareDatetime
    scheduled_wake_at: AwareDatetime | None = None
    terminal_reason: str | None = Field(default=None, max_length=160)
    current_hypothesis: str | None = Field(default=None, max_length=4_000)
    falsification_conditions: tuple[str, ...] | None = Field(default=None, max_length=32)
    open_questions: tuple[str, ...] | None = Field(default=None, max_length=32)
    resume_condition: str | None = Field(default=None, max_length=2_000)

    @field_validator("occurred_at", "scheduled_wake_at", mode="after")
    @classmethod
    def normalize_time(cls, value: dt.datetime | None) -> dt.datetime | None:
        return None if value is None else value.astimezone(dt.UTC)

    @model_validator(mode="after")
    def require_invariants_and_identity(self) -> Self:
        _require_sorted_unique(self.evidence_refs, reason="sorted_unique_evidence_refs_required")
        if self.falsification_conditions is not None:
            _require_sorted_unique(
                self.falsification_conditions,
                reason="sorted_unique_falsification_conditions_required",
            )
        if self.open_questions is not None:
            _require_sorted_unique(self.open_questions, reason="sorted_unique_open_questions_required")
        _require_state_invariants(
            state=self.state,
            budget=self.budget,
            scheduled_wake_at=self.scheduled_wake_at,
            terminal_reason=self.terminal_reason,
            label="step",
        )
        expected = day_agent_step_id(self)
        if not self.step_id:
            object.__setattr__(self, "step_id", expected)
        elif self.step_id != expected:
            raise InvalidDayAgentTaskFieldError(reason="step_id_mismatch")
        return self


def day_agent_step_id(step: DayAgentTaskStep) -> str:
    payload = step.model_dump(mode="json", exclude={"step_id"})
    material = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(material.encode()).hexdigest()


def _require_sorted_unique(values: tuple[str, ...], *, reason: str) -> None:
    if values != tuple(sorted(set(values))) or any(not value for value in values):
        raise InvalidDayAgentTaskFieldError(reason=reason)


def _require_state_invariants(
    *,
    state: DayAgentTaskState,
    budget: DayAgentBudget,
    scheduled_wake_at: dt.datetime | None,
    terminal_reason: str | None,
    label: str,
) -> None:
    match state:
        case DayAgentTaskState.OPEN:
            if scheduled_wake_at is not None or terminal_reason is not None:
                raise InvalidDayAgentTaskFieldError(reason=f"open_{label}_terminal_fields_invalid")
            if (
                budget.remaining_model_calls == 0
                or budget.remaining_tool_calls == 0
                or budget.remaining_runtime_seconds == 0
            ):
                raise InvalidDayAgentTaskFieldError(reason="active_task_budget_exhausted")
        case DayAgentTaskState.WAITING:
            if scheduled_wake_at is None:
                raise InvalidDayAgentTaskFieldError(reason=f"waiting_{label}_wake_required")
            if terminal_reason is not None:
                raise InvalidDayAgentTaskFieldError(reason=f"waiting_{label}_terminal_reason_invalid")
        case DayAgentTaskState.COMPLETED | DayAgentTaskState.BLOCKED:
            if scheduled_wake_at is not None:
                raise InvalidDayAgentTaskFieldError(reason=f"terminal_{label}_wake_invalid")
            if terminal_reason is None:
                raise InvalidDayAgentTaskFieldError(reason=f"terminal_{label}_reason_required")
        case unreachable:
            assert_never(unreachable)


__all__ = (
    "DayAgentAction",
    "DayAgentBudget",
    "DayAgentResearchTask",
    "DayAgentTaskState",
    "DayAgentTaskStep",
    "InvalidDayAgentTaskFieldError",
    "day_agent_step_id",
)
