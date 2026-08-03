from __future__ import annotations

import datetime as dt
from enum import StrEnum, unique
from pathlib import Path
from typing import Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from trading_agent.dashboard_agent_family import PRIMARY_AGENT_FAMILIES, AgentFamilyId
from trading_agent.dashboard_autonomous_research import AutonomousTaskReceiptV1
from trading_agent.research_agent_cycle_models import (
    ResearchAgentCycleV1,
    ResearchAgentEvidenceV1,
    ResearchAgentResultV1,
)


@unique
class OperationsAlertReason(StrEnum):
    CYCLE_STORE_MISSING = "cycle_store_missing"
    CYCLE_STORE_NONPRIVATE = "cycle_store_nonprivate"
    CYCLE_STORE_SYMLINK = "cycle_store_symlink"
    CYCLE_STORE_HARDLINK = "cycle_store_hardlink"
    CYCLE_STORE_MALFORMED = "cycle_store_malformed"
    CYCLE_STORE_WRONG_SCHEMA = "cycle_store_wrong_schema"
    RECEIPT_STORE_MISSING = "receipt_store_missing"
    RECEIPT_STORE_NONPRIVATE = "receipt_store_nonprivate"
    RECEIPT_STORE_SYMLINK = "receipt_store_symlink"
    RECEIPT_STORE_HARDLINK = "receipt_store_hardlink"
    RECEIPT_STORE_MALFORMED = "receipt_store_malformed"
    RUNS_STORE_MISSING = "runs_store_missing"
    RUNS_STORE_NONPRIVATE = "runs_store_nonprivate"
    RUNS_STORE_SYMLINK = "runs_store_symlink"
    RUNS_STORE_HARDLINK = "runs_store_hardlink"
    RUNS_STORE_MALFORMED = "runs_store_malformed"
    EVIDENCE_MISSING = "evidence_missing"
    EVIDENCE_STALE = "evidence_stale"
    TOKEN_BUDGET_EXHAUSTED = "token_budget_exhausted"
    COST_BUDGET_EXHAUSTED = "cost_budget_exhausted"
    HEAVY_EXPERIMENT_BUDGET_EXHAUSTED = "heavy_experiment_budget_exhausted"
    STORAGE_LIMIT_EXCEEDED = "storage_limit_exceeded"


class ResearchAgentOperationsInputs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    cycle_database: Path
    task_receipt_root: Path
    systematic_runs_root: Path
    as_of: AwareDatetime


class ResearchAgentOperationsLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    max_evidence_age_seconds: int = Field(ge=1, le=604_800)
    daily_token_limit_per_family: int = Field(ge=0, le=10_000_000)
    daily_cost_limit_microusd_per_family: int = Field(ge=0, le=1_000_000_000)
    systematic_heavy_experiment_limit: int = Field(ge=0, le=10_000)
    storage_limit_bytes: int = Field(ge=1, le=10 * 1024**3)


class OperationsBlockedSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    as_of: AwareDatetime
    limits: ResearchAgentOperationsLimits
    reason: OperationsAlertReason


class FamilyOperationsStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    family_id: AgentFamilyId
    last_terminal_at: dt.datetime | None
    last_success_at: dt.datetime | None
    consecutive_failures: int = Field(ge=0)
    last_evidence_at: dt.datetime | None
    evidence_age_seconds: int | None = Field(ge=0)
    evidence_state: Literal["fresh", "stale", "missing"]
    reserved_model_calls: int = Field(ge=0)
    reserved_tokens: int = Field(ge=0)
    reserved_cost_microusd: int = Field(ge=0)
    reservation_status: Literal["available", "exhausted"]
    alerts: tuple[OperationsAlertReason, ...]


class SystematicHeavyExperimentStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    completions: int = Field(ge=0)
    limit: int = Field(ge=0)
    status: Literal["available", "exhausted"]


class BoundedStorageStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    used_bytes: int = Field(ge=0)
    limit_bytes: int = Field(ge=1)
    status: Literal["within_limit", "over_limit"]


class OperationsInvocationEffects(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    provider_calls: Literal[0] = 0
    model_calls: Literal[0] = 0
    heavy_processes: Literal[0] = 0
    broker_mutation: Literal[0] = 0


class ResearchAgentOperationsStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    status: Literal["ready", "blocked"]
    as_of: AwareDatetime
    families: tuple[FamilyOperationsStatus, ...]
    systematic_heavy_experiments: SystematicHeavyExperimentStatus
    storage: BoundedStorageStatus
    alerts: tuple[OperationsAlertReason, ...]
    invocation_effects: OperationsInvocationEffects = OperationsInvocationEffects()

    @model_validator(mode="after")
    def require_six_family_contract(self) -> Self:
        if tuple(item.family_id for item in self.families) != PRIMARY_AGENT_FAMILIES:
            raise InvalidResearchAgentOperationsModelError(reason="exact_primary_agent_families_required")
        if (self.status == "ready") != (not self.alerts):
            raise InvalidResearchAgentOperationsModelError(reason="status_alert_mismatch")
        return self

    @classmethod
    def blocked_source(
        cls,
        context: OperationsBlockedSource,
    ) -> ResearchAgentOperationsStatus:
        families = tuple(
            FamilyOperationsStatus(
                family_id=family,
                last_terminal_at=None,
                last_success_at=None,
                consecutive_failures=0,
                last_evidence_at=None,
                evidence_age_seconds=None,
                evidence_state="missing",
                reserved_model_calls=0,
                reserved_tokens=0,
                reserved_cost_microusd=0,
                reservation_status="available",
                alerts=(context.reason,),
            )
            for family in PRIMARY_AGENT_FAMILIES
        )
        return cls(
            status="blocked",
            as_of=context.as_of,
            families=families,
            systematic_heavy_experiments=SystematicHeavyExperimentStatus(
                completions=0,
                limit=context.limits.systematic_heavy_experiment_limit,
                status="available",
            ),
            storage=BoundedStorageStatus(
                used_bytes=0,
                limit_bytes=context.limits.storage_limit_bytes,
                status="within_limit",
            ),
            alerts=(context.reason,),
        )


class InvalidResearchAgentOperationsModelError(ValueError):
    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class InvalidResearchAgentOperationsSourceError(RuntimeError):
    def __init__(self, reason: OperationsAlertReason) -> None:
        self.reason = reason
        super().__init__(reason)


class CycleOperationsFacts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    family: AgentFamilyId
    last_terminal_at: dt.datetime | None
    last_success_at: dt.datetime | None
    consecutive_failures: int = Field(ge=0)
    last_evidence_at: dt.datetime | None


class CycleOperationsHistory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    evidence: tuple[ResearchAgentEvidenceV1, ...]
    cycles: tuple[ResearchAgentCycleV1, ...]
    results: tuple[ResearchAgentResultV1, ...]
    as_of: AwareDatetime


class OperationsEvaluationContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    as_of: AwareDatetime
    limits: ResearchAgentOperationsLimits
    receipts: tuple[AutonomousTaskReceiptV1, ...]


class OperationsSourceSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    cycles: tuple[CycleOperationsFacts, ...]
    receipts: tuple[AutonomousTaskReceiptV1, ...]
    heavy_completions: int = Field(ge=0)
    storage_bytes: int = Field(ge=0)


def evaluate_family_operations(
    fact: CycleOperationsFacts,
    context: OperationsEvaluationContext,
) -> FamilyOperationsStatus:
    now, limits, receipts = context.as_of, context.limits, context.receipts
    age = None if fact.last_evidence_at is None else int((now - fact.last_evidence_at).total_seconds())
    claims = tuple(
        item
        for item in receipts
        if item.kind == "claim"
        and item.agent_family_id == fact.family
        and item.occurred_at.astimezone(dt.UTC).date() == now.astimezone(dt.UTC).date()
        and item.occurred_at <= now
    )
    tokens = sum(item.consumed_tokens for item in claims)
    cost = sum(item.consumed_cost_microusd for item in claims)
    alerts: list[OperationsAlertReason] = []
    if age is None:
        evidence_state = "missing"
        alerts.append(OperationsAlertReason.EVIDENCE_MISSING)
    elif age > limits.max_evidence_age_seconds:
        evidence_state = "stale"
        alerts.append(OperationsAlertReason.EVIDENCE_STALE)
    else:
        evidence_state = "fresh"
    if tokens >= limits.daily_token_limit_per_family:
        alerts.append(OperationsAlertReason.TOKEN_BUDGET_EXHAUSTED)
    if cost >= limits.daily_cost_limit_microusd_per_family:
        alerts.append(OperationsAlertReason.COST_BUDGET_EXHAUSTED)
    budget_exhausted = any(alert in alerts for alert in _BUDGET_ALERTS)
    return FamilyOperationsStatus(
        family_id=fact.family,
        last_terminal_at=fact.last_terminal_at,
        last_success_at=fact.last_success_at,
        consecutive_failures=fact.consecutive_failures,
        last_evidence_at=fact.last_evidence_at,
        evidence_age_seconds=age,
        evidence_state=evidence_state,
        reserved_model_calls=len(claims),
        reserved_tokens=tokens,
        reserved_cost_microusd=cost,
        reservation_status="exhausted" if budget_exhausted else "available",
        alerts=tuple(alerts),
    )


_BUDGET_ALERTS = frozenset({OperationsAlertReason.TOKEN_BUDGET_EXHAUSTED, OperationsAlertReason.COST_BUDGET_EXHAUSTED})


__all__ = (
    "BoundedStorageStatus",
    "CycleOperationsFacts",
    "CycleOperationsHistory",
    "FamilyOperationsStatus",
    "InvalidResearchAgentOperationsModelError",
    "InvalidResearchAgentOperationsSourceError",
    "OperationsAlertReason",
    "OperationsBlockedSource",
    "OperationsEvaluationContext",
    "OperationsInvocationEffects",
    "OperationsSourceSnapshot",
    "ResearchAgentOperationsInputs",
    "ResearchAgentOperationsLimits",
    "ResearchAgentOperationsStatus",
    "SystematicHeavyExperimentStatus",
    "evaluate_family_operations",
)
