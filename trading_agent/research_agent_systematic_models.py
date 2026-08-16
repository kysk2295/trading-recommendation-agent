from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Self, assert_never, final

from pydantic import BaseModel, ConfigDict, Field, model_validator

from run_autonomous_research_cycle import AutonomousCycleCliResult
from trading_agent.research_agent_actions import ResearchAgentActionContext
from trading_agent.research_agent_cycle_models import (
    ResearchAgentCycleV1,
    ResearchAgentDecisionV1,
    ResearchAgentResultStatus,
    ResearchAgentResultV1,
    ResearchAgentWakeKind,
    research_agent_result_id,
)


class InvalidSystematicResearchActionError(RuntimeError):
    __slots__ = ("reason",)

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)

    def __str__(self) -> str:
        return self.reason


class SystematicResearchActionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    project_root: Path
    uv_executable: Path
    python_executable: Path
    context: Path
    response_fixture: Path | None
    hermes_executable: Path | None
    model_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{2,127}$")
    provider_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,127}$")
    experiment_ledger: Path
    receipt_root: Path
    strategy_root: Path
    manifest_root: Path
    queue_root: Path
    input_activation: Path
    artifact_root: Path
    review_root: Path
    runs_root: Path
    max_runtime_seconds: float = Field(gt=0, le=3_600)
    max_bars: int = Field(default=100_000, ge=1, le=100_000)
    max_sessions: int = Field(default=60, ge=1, le=60)
    rss_limit_gib: float = Field(default=9.5, gt=0, le=9.5)

    @model_validator(mode="after")
    def require_absolute_provider_binding(self) -> Self:
        paths = (
            self.project_root,
            self.uv_executable,
            self.python_executable,
            self.context,
            self.experiment_ledger,
            self.receipt_root,
            self.strategy_root,
            self.manifest_root,
            self.queue_root,
            self.input_activation,
            self.artifact_root,
            self.review_root,
            self.runs_root,
        )
        if any(not path.is_absolute() for path in paths):
            raise InvalidSystematicResearchActionError(reason="systematic_path_not_absolute")
        if self.response_fixture is not None and not self.response_fixture.is_absolute():
            raise InvalidSystematicResearchActionError(reason="systematic_path_not_absolute")
        if self.hermes_executable is not None and not self.hermes_executable.is_absolute():
            raise InvalidSystematicResearchActionError(reason="systematic_path_not_absolute")
        if (self.response_fixture is None) == (self.hermes_executable is None):
            raise InvalidSystematicResearchActionError(reason="systematic_provider_binding_invalid")
        return self


@final
@dataclass(frozen=True, slots=True)
class SystematicResultContext:
    cycle: ResearchAgentCycleV1
    decision: ResearchAgentDecisionV1
    report: AutonomousCycleCliResult
    occurred_at: dt.datetime


@dataclass(frozen=True, slots=True)
class SystematicFailureContext:
    cycle: ResearchAgentCycleV1
    decision: ResearchAgentDecisionV1
    occurred_at: dt.datetime
    reason: str
    summary: str
    continuation: str


def result_from_report(context: SystematicResultContext) -> ResearchAgentResultV1:
    cycle = context.cycle
    decision = context.decision
    report = context.report
    occurred_at = context.occurred_at
    match report.status:
        case "blocked":
            return failed_result(
                SystematicFailureContext(
                    cycle=cycle,
                    decision=decision,
                    occurred_at=occurred_at,
                    reason=report.reason_codes[0],
                    summary="The bounded generated strategy cycle was blocked.",
                    continuation="Retry the same evidence after the fixed failure backoff.",
                )
            )
        case "complete":
            return ResearchAgentResultV1(
                result_id=research_agent_result_id(cycle.cycle_id),
                cycle_id=cycle.cycle_id,
                agent_family_id=cycle.agent_family_id,
                market_id=cycle.market_id,
                status=ResearchAgentResultStatus.COMPLETED,
                question=decision.question,
                summary="The generated strategy cycle completed under the deterministic Reviewer.",
                reason=f"reviewer_{report.reviewer_decision}",
                continuation=None,
                evidence_refs=decision.evidence_refs,
                artifact_refs=_complete_artifacts(report),
                occurred_at=occurred_at,
                next_wake_kind=decision.next_wake_kind,
                next_wake_at=decision.next_wake_at,
            )
        case unreachable:
            assert_never(unreachable)


def failed_result(context: SystematicFailureContext) -> ResearchAgentResultV1:
    cycle = context.cycle
    decision = context.decision
    return ResearchAgentResultV1(
        result_id=research_agent_result_id(cycle.cycle_id),
        cycle_id=cycle.cycle_id,
        agent_family_id=cycle.agent_family_id,
        market_id=cycle.market_id,
        status=ResearchAgentResultStatus.FAILED,
        question=decision.question,
        summary=context.summary,
        reason=context.reason,
        continuation=context.continuation,
        evidence_refs=decision.evidence_refs,
        artifact_refs=(),
        occurred_at=context.occurred_at,
        next_wake_kind=ResearchAgentWakeKind.SCHEDULED,
        next_wake_at=context.occurred_at + dt.timedelta(minutes=15),
    )


def pending_result(
    context: ResearchAgentActionContext,
    open_work_ref: str | None,
) -> ResearchAgentResultV1:
    if open_work_ref is None:
        raise InvalidSystematicResearchActionError(reason="systematic_open_work_unresolved")
    return ResearchAgentResultV1(
        result_id=research_agent_result_id(context.cycle.cycle_id),
        cycle_id=context.cycle.cycle_id,
        agent_family_id="systematic_quant",
        market_id=context.cycle.market_id,
        status=ResearchAgentResultStatus.NO_ACTION,
        question=context.decision.question,
        summary="The generated strategy child is still running outside the fast actor loop.",
        reason="systematic_run_pending",
        continuation="Poll the same immutable Systematic request at the next scheduled wake.",
        open_work_ref=open_work_ref,
        evidence_refs=context.decision.evidence_refs,
        artifact_refs=(),
        occurred_at=context.observed_at,
        next_wake_kind=ResearchAgentWakeKind.SCHEDULED,
        next_wake_at=context.observed_at + dt.timedelta(seconds=30),
    )


def no_open_work_result(context: ResearchAgentActionContext) -> ResearchAgentResultV1:
    return ResearchAgentResultV1(
        result_id=research_agent_result_id(context.cycle.cycle_id),
        cycle_id=context.cycle.cycle_id,
        agent_family_id="systematic_quant",
        market_id=context.cycle.market_id,
        status=ResearchAgentResultStatus.NO_ACTION,
        question=context.decision.question,
        summary="No generated strategy request is open for review.",
        reason="systematic_no_open_work",
        continuation="Wait for new evidence or request a bounded experiment.",
        evidence_refs=context.decision.evidence_refs,
        artifact_refs=(),
        occurred_at=context.observed_at,
        next_wake_kind=context.decision.next_wake_kind,
        next_wake_at=context.decision.next_wake_at,
    )


def launched_result(context: ResearchAgentActionContext, request_sha: str) -> ResearchAgentResultV1:
    cycle = context.cycle
    decision = context.decision
    return ResearchAgentResultV1(
        result_id=research_agent_result_id(cycle.cycle_id),
        cycle_id=cycle.cycle_id,
        agent_family_id=cycle.agent_family_id,
        market_id=cycle.market_id,
        status=ResearchAgentResultStatus.COMPLETED,
        question=decision.question,
        summary="The bounded generated strategy experiment was launched outside the fast actor loop.",
        reason="review_pending",
        continuation="Review the immutable experiment and Reviewer report at the scheduled wake.",
        open_work_ref=f"systematic.run.{cycle.cycle_id}",
        evidence_refs=decision.evidence_refs,
        artifact_refs=(f"systematic_request.{request_sha}",),
        occurred_at=context.observed_at,
        next_wake_kind=ResearchAgentWakeKind.SCHEDULED,
        next_wake_at=context.observed_at + dt.timedelta(seconds=30),
    )


def _complete_artifacts(report: AutonomousCycleCliResult) -> tuple[str, ...]:
    values = (
        report.strategy_artifact_id,
        report.trial_id,
        report.experiment_artifact_id,
        report.review_artifact_id,
    )
    if any(value is None for value in values):
        raise InvalidSystematicResearchActionError(reason="systematic_complete_artifact_missing")
    return tuple(sorted(value for value in values if value is not None))


__all__ = (
    "InvalidSystematicResearchActionError",
    "SystematicFailureContext",
    "SystematicResearchActionConfig",
    "SystematicResultContext",
    "failed_result",
    "launched_result",
    "no_open_work_result",
    "pending_result",
    "result_from_report",
)
