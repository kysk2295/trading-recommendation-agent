from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Final, override

from trading_agent.experiment_ledger_models import (
    StrategyLifecycleEvent,
    StrategyLifecycleState,
)
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.intraday_promotion_evidence import (
    IntradayPromotionEvidencePaths,
    VerifiedIntradayPromotionEvidence,
    load_intraday_promotion_evidence,
)
from trading_agent.intraday_promotion_models import (
    IntradayPromotionApproval,
    IntradayPromotionAssessment,
    PromotionApprovalContent,
    PromotionAssessmentContent,
    PromotionAssessmentStatus,
    approval_id,
    assessment_id,
)
from trading_agent.intraday_promotion_store import (
    load_promotion_assessment,
    publish_promotion_approval,
    publish_promotion_assessment,
    require_private_authoritative_file,
)
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds

INTRADAY_PROMOTION_POLICY_VERSION: Final = "intraday_promotion_controller_v1"
_MANUAL_BLOCKER: Final = "manual_approval_required"


@dataclass(frozen=True, slots=True)
class IntradayPromotionRequest:
    experiment_ledger: Path
    evidence: IntradayPromotionEvidencePaths
    session_date: dt.date


@dataclass(frozen=True, slots=True)
class IntradayPromotionControlResult:
    strategy_version: str
    target_state: StrategyLifecycleState
    authority_bindings_created: int
    lifecycle_events_created: int
    event: StrategyLifecycleEvent


@dataclass(frozen=True, slots=True)
class IntradayPromotionApprovalRequest:
    assessment_path: Path
    approver: str
    approved_at: dt.datetime
    output_root: Path


@dataclass(frozen=True, slots=True)
class IntradayPromotionControlCommand:
    request: IntradayPromotionRequest
    assessment_path: Path
    approval_path: Path
    decided_at: dt.datetime


@dataclass(frozen=True, slots=True)
class InvalidIntradayPromotionError(ValueError):
    reason: str = "invalid_or_untrusted_source"

    @override
    def __str__(self) -> str:
        return f"intraday promotion blocked: {self.reason}"


def assess_intraday_promotion(
    request: IntradayPromotionRequest,
    assessed_at: dt.datetime,
    output_root: Path,
) -> tuple[IntradayPromotionAssessment, Path, bool]:
    evidence, target, _ = _verified_request(request, assessed_at)
    blockers = tuple(sorted((*evidence.blockers, _MANUAL_BLOCKER)))
    content = PromotionAssessmentContent(
        strategy_version=evidence.strategy_version,
        decision_session_date=request.session_date,
        assessed_at=assessed_at,
        target_state=target,
        evidence_keys=evidence.evidence_keys,
        status=PromotionAssessmentStatus.BLOCKED,
        blockers=blockers,
    )
    assessment = IntradayPromotionAssessment(
        assessment_id=assessment_id(content),
        content=content,
    )
    path, created = publish_promotion_assessment(output_root, assessment)
    return assessment, path, created


def approve_intraday_promotion(
    request: IntradayPromotionApprovalRequest,
) -> tuple[IntradayPromotionApproval, Path, bool]:
    assessment = load_promotion_assessment(request.assessment_path)
    if (
        assessment.content.blockers != (_MANUAL_BLOCKER,)
        or not _same_session(
            request.approved_at,
            assessment.content.decision_session_date,
        )
        or request.approved_at < assessment.content.assessed_at
    ):
        raise InvalidIntradayPromotionError("assessment_not_approvable")
    content = PromotionApprovalContent(
        assessment_id=assessment.assessment_id,
        strategy_version=assessment.content.strategy_version,
        decision_session_date=assessment.content.decision_session_date,
        target_state=assessment.content.target_state,
        approver=request.approver,
        approved_at=request.approved_at,
    )
    approval = IntradayPromotionApproval(
        approval_id=approval_id(content),
        content=content,
    )
    path, created = publish_promotion_approval(request.output_root, approval)
    return approval, path, created


def control_intraday_promotion(
    command: IntradayPromotionControlCommand,
) -> IntradayPromotionControlResult:
    from trading_agent.intraday_promotion_control import apply_intraday_promotion

    return apply_intraday_promotion(command)


def _verified_request(
    request: IntradayPromotionRequest,
    observed_at: dt.datetime,
) -> tuple[VerifiedIntradayPromotionEvidence, StrategyLifecycleState, str]:
    require_private_authoritative_file(request.experiment_ledger)
    if not _same_session(observed_at, request.session_date) or regular_session_bounds(request.session_date) is None:
        raise InvalidIntradayPromotionError("decision_session_invalid")
    evidence = load_intraday_promotion_evidence(request.evidence, request.session_date)
    if not evidence.strategy_version or any(value > observed_at for value in evidence.observed_at):
        raise InvalidIntradayPromotionError("evidence_time_invalid")
    ledger = ExperimentLedgerStore(request.experiment_ledger)
    versions = tuple(
        stored.registration
        for stored in ledger.strategy_versions()
        if stored.registration.strategy_version == evidence.strategy_version
    )
    events = ledger.lifecycle_events(evidence.strategy_version)
    if len(versions) != 1 or not events:
        raise InvalidIntradayPromotionError("strategy_lineage_invalid")
    target = (
        StrategyLifecycleState.PAPER_CHAMPION
        if any(event.event.to_state is StrategyLifecycleState.EXPERIMENTAL_PAPER for event in events)
        else StrategyLifecycleState.SHADOW_CHAMPION
    )
    return evidence, target, versions[0].strategy_id


def _same_session(value: dt.datetime, session_date: dt.date) -> bool:
    return (
        value.tzinfo is not None and value.utcoffset() is not None and value.astimezone(NEW_YORK).date() == session_date
    )


__all__ = (
    "INTRADAY_PROMOTION_POLICY_VERSION",
    "IntradayPromotionApprovalRequest",
    "IntradayPromotionControlCommand",
    "IntradayPromotionControlResult",
    "IntradayPromotionRequest",
    "InvalidIntradayPromotionError",
    "approve_intraday_promotion",
    "assess_intraday_promotion",
    "control_intraday_promotion",
)
