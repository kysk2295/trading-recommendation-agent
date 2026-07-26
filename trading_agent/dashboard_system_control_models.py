from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from trading_agent.dashboard_agent_family import AgentFamilyId
from trading_agent.dashboard_autonomous_research import TriggerType

Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class ControlReceiptBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[2]
    evidence_type: Literal["autonomous_control"]
    evidence_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    run_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    agent_family_id: AgentFamilyId
    trigger_type: TriggerType
    observed_at: AwareDatetime
    blocker_code: str | None = Field(default=None, pattern=r"^[a-z0-9_]{3,80}$")
    previous_receipt_sha256: Sha256 | None
    receipt_sha256: Sha256


class SchedulerReceipt(ControlReceiptBase):
    component: Literal["scheduler"]
    state: Literal["scheduled", "blocked"]
    schedule_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")

    @model_validator(mode="after")
    def validate_scheduler(self) -> Self:
        if self.previous_receipt_sha256 is not None:
            raise ValueError
        _validate_blocker(self.state, self.blocker_code)
        return self


class TriggerReceipt(ControlReceiptBase):
    component: Literal["trigger"]
    state: Literal["accepted", "blocked"]
    trigger_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")

    @model_validator(mode="after")
    def validate_trigger(self) -> Self:
        _require_previous(self.previous_receipt_sha256)
        _validate_blocker(self.state, self.blocker_code)
        return self


class ClaimReceipt(ControlReceiptBase):
    component: Literal["claim"]
    state: Literal["claimed", "blocked"]
    claim_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")

    @model_validator(mode="after")
    def validate_claim(self) -> Self:
        _require_previous(self.previous_receipt_sha256)
        _validate_blocker(self.state, self.blocker_code)
        return self


class BudgetReceipt(ControlReceiptBase):
    component: Literal["budget"]
    state: Literal["authorized", "blocked"]
    token_budget: int = Field(ge=1)
    tokens_remaining: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_budget(self) -> Self:
        _require_previous(self.previous_receipt_sha256)
        _validate_blocker(self.state, self.blocker_code)
        if self.tokens_remaining > self.token_budget:
            raise ValueError
        return self


class CooldownReceipt(ControlReceiptBase):
    component: Literal["cooldown"]
    state: Literal["passed", "blocked"]
    cooldown_until: AwareDatetime

    @model_validator(mode="after")
    def validate_cooldown(self) -> Self:
        _require_previous(self.previous_receipt_sha256)
        _validate_blocker(self.state, self.blocker_code)
        if (self.state == "passed") != (self.cooldown_until <= self.observed_at):
            raise ValueError
        return self


class ConcurrencyReceipt(ControlReceiptBase):
    component: Literal["concurrency"]
    state: Literal["passed", "blocked"]
    active_count: int = Field(ge=0)
    max_concurrency: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_concurrency(self) -> Self:
        _require_previous(self.previous_receipt_sha256)
        _validate_blocker(self.state, self.blocker_code)
        if (self.state == "passed") != (self.active_count < self.max_concurrency):
            raise ValueError
        return self


class FailureBudgetReceipt(ControlReceiptBase):
    component: Literal["failure_budget"]
    state: Literal["passed", "blocked"]
    failure_count: int = Field(ge=0)
    max_failures: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_failure_budget(self) -> Self:
        _require_previous(self.previous_receipt_sha256)
        _validate_blocker(self.state, self.blocker_code)
        if (self.state == "passed") != (self.failure_count < self.max_failures):
            raise ValueError
        return self


class WorktreeReceipt(ControlReceiptBase):
    component: Literal["worktree"]
    state: Literal["authorized", "blocked"]
    isolation_receipt_sha256: Sha256

    @model_validator(mode="after")
    def validate_worktree(self) -> Self:
        _require_previous(self.previous_receipt_sha256)
        _validate_blocker(self.state, self.blocker_code)
        return self


class CleanupReceipt(ControlReceiptBase):
    component: Literal["cleanup"]
    state: Literal["completed", "running", "failed"]
    terminal_receipt_sha256: Sha256 | None

    @model_validator(mode="after")
    def validate_cleanup(self) -> Self:
        _require_previous(self.previous_receipt_sha256)
        _validate_blocker(self.state, self.blocker_code)
        terminal = self.terminal_receipt_sha256 is not None
        if (self.state in {"completed", "failed"}) != terminal:
            raise ValueError
        return self


def _require_previous(previous: str | None) -> None:
    if previous is None:
        raise ValueError


def _validate_blocker(state: str, blocker_code: str | None) -> None:
    blocked = state in {"blocked", "failed"}
    if blocked != (blocker_code is not None):
        raise ValueError


AutonomousControlReceipt = (
    SchedulerReceipt
    | TriggerReceipt
    | ClaimReceipt
    | BudgetReceipt
    | CooldownReceipt
    | ConcurrencyReceipt
    | FailureBudgetReceipt
    | WorktreeReceipt
    | CleanupReceipt
)

__all__ = (
    "AutonomousControlReceipt",
    "BudgetReceipt",
    "ClaimReceipt",
    "CleanupReceipt",
    "ConcurrencyReceipt",
    "CooldownReceipt",
    "FailureBudgetReceipt",
    "SchedulerReceipt",
    "TriggerReceipt",
    "WorktreeReceipt",
)
