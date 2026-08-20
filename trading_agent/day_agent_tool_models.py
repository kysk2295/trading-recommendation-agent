from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Annotated, Final, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    field_validator,
    model_validator,
)

from trading_agent.day_agent_task_models import (
    DayAgentAction,
    DayAgentBudget,
    DayAgentResearchTask,
    DayAgentTaskStep,
)

_TOOL_ACTIONS: Final = frozenset(
    {
        DayAgentAction.INSPECT_SITUATION,
        DayAgentAction.READ_CATALYSTS,
        DayAgentAction.COMPARE_LEADERS,
        DayAgentAction.SEARCH_PAST_CASES,
        DayAgentAction.RUN_LIGHT_EXPERIMENT,
        DayAgentAction.ASK_CRITIC,
    }
)


class InvalidDayAgentToolModelError(ValueError):
    __slots__ = ("reason",)

    reason: str

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)

    def __str__(self) -> str:
        return self.reason


class DayAgentToolArguments(RootModel[dict[str, str]]):
    model_config = ConfigDict(frozen=True, strict=True)

    root: dict[str, str]

    @model_validator(mode="after")
    def require_bounds(self) -> Self:
        invalid_value = any(
            not key or not value or len(key) > 64 or len(value) > 500
            for key, value in self.root.items()
        )
        if len(self.root) > 8 or invalid_value:
            raise InvalidDayAgentToolModelError(reason="day_agent_tool_arguments_invalid")
        return self


class DayAgentToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: Literal["tool_call"] = "tool_call"
    action: DayAgentAction
    arguments: DayAgentToolArguments
    reason: str = Field(min_length=8, max_length=500)

    @model_validator(mode="after")
    def require_read_only_action(self) -> Self:
        if self.action not in _TOOL_ACTIONS:
            raise InvalidDayAgentToolModelError(reason="day_agent_tool_action_invalid")
        return self


class DayAgentThesisSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: Literal["thesis_submission"] = "thesis_submission"
    action: Literal[DayAgentAction.SUBMIT_TRADE_THESIS] = DayAgentAction.SUBMIT_TRADE_THESIS
    thesis: str = Field(min_length=8, max_length=4_000)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=8, max_length=500)

    @model_validator(mode="after")
    def require_evidence_identity(self) -> Self:
        _require_sorted_unique(self.evidence_refs)
        return self


class DayAgentHypothesisSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: Literal["hypothesis_submission"] = "hypothesis_submission"
    action: Literal[DayAgentAction.SUBMIT_RESEARCH_HYPOTHESIS] = DayAgentAction.SUBMIT_RESEARCH_HYPOTHESIS
    hypothesis: str = Field(min_length=8, max_length=4_000)
    falsification_conditions: tuple[str, ...] = Field(min_length=1, max_length=32)
    evidence_refs: tuple[str, ...] = Field(max_length=64)
    experiment_code: str | None = Field(default=None, min_length=8, max_length=8_000)
    reason: str = Field(min_length=8, max_length=500)

    @model_validator(mode="after")
    def require_canonical_values(self) -> Self:
        _require_sorted_unique(self.falsification_conditions)
        _require_sorted_unique(self.evidence_refs)
        return self


class DayAgentDefer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: Literal["defer"] = "defer"
    action: Literal[DayAgentAction.DEFER] = DayAgentAction.DEFER
    reason: str = Field(min_length=8, max_length=500)
    resume_condition: str = Field(min_length=8, max_length=2_000)
    scheduled_wake_at: AwareDatetime

    @field_validator("scheduled_wake_at", mode="after")
    @classmethod
    def normalize_time(cls, value: dt.datetime) -> dt.datetime:
        return value.astimezone(dt.UTC)


DayAgentReasoningResponse = Annotated[
    DayAgentToolCall | DayAgentThesisSubmission | DayAgentHypothesisSubmission | DayAgentDefer,
    Field(discriminator="kind"),
]


class DayAgentToolObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    action: DayAgentAction
    bounded_json: str = Field(min_length=2, max_length=16_384)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=64)
    observed_at: AwareDatetime
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("observed_at", mode="after")
    @classmethod
    def normalize_time(cls, value: dt.datetime) -> dt.datetime:
        return value.astimezone(dt.UTC)

    @model_validator(mode="after")
    def require_identity(self) -> Self:
        try:
            decoded = json.loads(self.bounded_json)
            _ = json.dumps(decoded, allow_nan=False)
        except (TypeError, ValueError):
            raise InvalidDayAgentToolModelError(reason="day_agent_observation_json_invalid") from None
        if hashlib.sha256(self.bounded_json.encode()).hexdigest() != self.content_sha256:
            raise InvalidDayAgentToolModelError(reason="day_agent_observation_hash_invalid")
        _require_sorted_unique(self.evidence_refs)
        if self.content_sha256 not in self.evidence_refs:
            raise InvalidDayAgentToolModelError(reason="day_agent_observation_hash_ref_required")
        return self


class DayAgentReasoningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    task: DayAgentResearchTask
    prior_steps: tuple[DayAgentTaskStep, ...] = Field(max_length=24)
    observations: tuple[DayAgentToolObservation, ...] = Field(max_length=12)
    allowed_tool_names: tuple[str, ...] = Field(max_length=8)
    remaining_budget: DayAgentBudget

    @model_validator(mode="after")
    def require_allowed_tool_identity(self) -> Self:
        _require_sorted_unique(self.allowed_tool_names)
        return self


def _require_sorted_unique(values: tuple[str, ...]) -> None:
    if values != tuple(sorted(set(values))) or any(not value for value in values):
        raise InvalidDayAgentToolModelError(reason="day_agent_values_not_canonical")


__all__ = (
    "DayAgentDefer",
    "DayAgentHypothesisSubmission",
    "DayAgentReasoningRequest",
    "DayAgentReasoningResponse",
    "DayAgentThesisSubmission",
    "DayAgentToolArguments",
    "DayAgentToolCall",
    "DayAgentToolObservation",
    "InvalidDayAgentToolModelError",
)
