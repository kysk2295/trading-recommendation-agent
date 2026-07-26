from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from trading_agent.dashboard_agent_family import AgentFamilyId
from trading_agent.dashboard_agent_tool_jobs import AutonomousTool, NetworkPolicy

TriggerType = Literal[
    "new_data",
    "market_event",
    "experiment_result",
    "reviewer_feedback",
    "approved_schedule",
]
TriggerAuthority = Literal[
    "source_receipt",
    "market_event_authority",
    "experiment_ledger",
    "independent_reviewer",
    "approved_scheduler",
]
TaskState = Literal[
    "claimed",
    "running",
    "completed",
    "failed",
    "uncertain",
    "blocked",
    "duplicate",
]
ReceiptKind = Literal["blocker", "claim", "progress", "evidence", "result", "cleanup"]

_AUTHORITY_BY_TRIGGER: dict[TriggerType, TriggerAuthority] = {
    "new_data": "source_receipt",
    "market_event": "market_event_authority",
    "experiment_result": "experiment_ledger",
    "reviewer_feedback": "independent_reviewer",
    "approved_schedule": "approved_scheduler",
}


@dataclass(frozen=True, slots=True)
class InvalidAutonomousTriggerFieldError(ValueError):
    reason: str

    def __str__(self) -> str:
        return self.reason


class BudgetEnvelopeV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_tokens: int = Field(ge=1, le=1_000_000)
    max_cost_microusd: int = Field(ge=1, le=100_000_000)
    max_runtime_seconds: int = Field(ge=1, le=3_600)
    max_model_processes: Literal[1] = 1


class AutonomousEnvironmentSpecV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pinned_code_sha: str = Field(pattern=r"^[a-f0-9]{40}(?:[a-f0-9]{24})?$")
    allowed_read_roots: tuple[Literal["isolated_worktree", "source_evidence"], ...] = Field(
        min_length=1,
        max_length=2,
    )
    allowed_write_roots: tuple[Literal["experiment"], ...] = Field(min_length=1, max_length=1)
    allowed_tools: tuple[AutonomousTool, ...] = Field(min_length=1, max_length=4)
    network_policy: NetworkPolicy
    requested_read_paths: tuple[str, ...] = Field(default=("source_evidence",), max_length=16)
    requested_write_paths: tuple[str, ...] = Field(default=("experiment/candidate.json",), max_length=16)
    requested_tool_argv: tuple[str, ...] = Field(default=(), max_length=16)
    requested_network_targets: tuple[str, ...] = Field(default=(), max_length=8)

    @model_validator(mode="after")
    def require_isolated_roots(self) -> Self:
        if (
            "isolated_worktree" not in self.allowed_read_roots
            or self.allowed_write_roots != ("experiment",)
            or len(set(self.allowed_tools)) != len(self.allowed_tools)
        ):
            raise InvalidAutonomousTriggerFieldError(reason="isolated_environment_required")
        return self


class AutonomousTriggerV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    trigger_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{8,100}$")
    trigger_type: TriggerType
    authority: TriggerAuthority
    agent_family_id: AgentFamilyId
    source_receipt_ids: tuple[str, ...] = Field(min_length=1, max_length=32)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=32)
    observed_at: AwareDatetime
    authorized_at: AwareDatetime
    expires_at: AwareDatetime
    policy_version: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{3,80}$")
    dedupe_key: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{8,160}$")
    budget_envelope: BudgetEnvelopeV1
    environment_spec: AutonomousEnvironmentSpecV1
    payload_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def require_authority_and_time_order(self) -> Self:
        if self.authority != _AUTHORITY_BY_TRIGGER[self.trigger_type]:
            raise InvalidAutonomousTriggerFieldError(reason="trigger_authority_mismatch")
        if not self.observed_at <= self.authorized_at <= self.expires_at:
            raise InvalidAutonomousTriggerFieldError(reason="trigger_time_order_invalid")
        if len(set(self.source_receipt_ids)) != len(self.source_receipt_ids):
            raise InvalidAutonomousTriggerFieldError(reason="duplicate_source_receipt")
        if any(not _safe_reference(value) for value in (*self.source_receipt_ids, *self.evidence_refs)):
            raise InvalidAutonomousTriggerFieldError(reason="unsafe_evidence_reference")
        return self


class AutonomousTaskReceiptV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    public_task_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    event_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    agent_family_id: AgentFamilyId
    channel: Literal["autonomous_research"] = "autonomous_research"
    trigger_type: TriggerType
    policy_version: str
    code_version: str
    sequence: int = Field(ge=0, le=10_000)
    kind: ReceiptKind
    state: TaskState
    occurred_at: AwareDatetime
    reason: str | None = Field(pattern=r"^[a-z0-9_]{3,80}$")
    evidence_refs: tuple[str, ...] = Field(max_length=32)
    result_sha256: str | None = Field(pattern=r"^[a-f0-9]{64}$")
    summary: str | None = Field(max_length=240)
    consumed_tokens: int = Field(ge=0, le=1_000_000)
    consumed_cost_microusd: int = Field(ge=0, le=100_000_000)
    redaction_status: Literal["passed"] = "passed"
    reviewer_state: Literal["pending", "accepted", "rejected", "needs_evidence"] = "pending"
    lifecycle_state: Literal["unchanged"] = "unchanged"


def trigger_fixture(
    *, now: dt.datetime
) -> dict[str, int | str | tuple[str, ...] | dt.datetime | dict[str, int | str | tuple[str, ...]]]:
    return {
        "schema_version": 1,
        "trigger_id": "trigger-new-data-001",
        "trigger_type": "new_data",
        "authority": "source_receipt",
        "agent_family_id": "systematic_quant",
        "source_receipt_ids": ("source-receipt-001",),
        "evidence_refs": ("e" * 64,),
        "observed_at": now,
        "authorized_at": now,
        "expires_at": now + dt.timedelta(minutes=10),
        "policy_version": "autonomous-policy-v1",
        "dedupe_key": "new-data-source-receipt-001",
        "budget_envelope": {
            "max_tokens": 10_000,
            "max_cost_microusd": 1_000_000,
            "max_runtime_seconds": 300,
            "max_model_processes": 1,
        },
        "environment_spec": {
            "pinned_code_sha": "a" * 40,
            "allowed_read_roots": ("isolated_worktree", "source_evidence"),
            "allowed_write_roots": ("experiment",),
            "allowed_tools": ("read_evidence", "write_candidate", "run_tests"),
            "network_policy": "model_provider_only",
            "requested_read_paths": ("source_evidence",),
            "requested_write_paths": ("experiment/candidate.json",),
            "requested_tool_argv": (),
            "requested_network_targets": (),
        },
        "payload_sha256": "f" * 64,
    }


def _safe_reference(value: str) -> bool:
    return 1 <= len(value) <= 160 and all(character.isalnum() or character in "._:-" for character in value)


__all__ = (
    "AutonomousEnvironmentSpecV1",
    "AutonomousTaskReceiptV1",
    "AutonomousTriggerV1",
    "BudgetEnvelopeV1",
    "ReceiptKind",
    "TaskState",
    "TriggerAuthority",
    "TriggerType",
    "trigger_fixture",
)
