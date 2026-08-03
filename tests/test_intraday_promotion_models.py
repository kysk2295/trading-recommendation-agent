from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from trading_agent.experiment_ledger_models import StrategyLifecycleState
from trading_agent.intraday_promotion_models import (
    IntradayPromotionApproval,
    IntradayPromotionAssessment,
    PromotionApprovalContent,
    PromotionAssessmentContent,
    PromotionAssessmentStatus,
    approval_id,
    assessment_id,
)

NOW = dt.datetime(2026, 7, 27, 20, 30, tzinfo=dt.UTC)


def _assessment() -> IntradayPromotionAssessment:
    content = PromotionAssessmentContent(
        strategy_version="challenger.v1",
        decision_session_date=dt.date(2026, 7, 27),
        assessed_at=NOW,
        target_state=StrategyLifecycleState.SHADOW_CHAMPION,
        evidence_keys=tuple(chr(code) * 64 for code in range(ord("a"), ord("g"))),
        status=PromotionAssessmentStatus.ELIGIBLE,
        blockers=(),
    )
    return IntradayPromotionAssessment(assessment_id=assessment_id(content), content=content)


def test_approval_is_a_distinct_content_addressed_manual_event() -> None:
    # Given: an eligible automatic assessment
    assessment = _assessment()
    content = PromotionApprovalContent(
        assessment_id=assessment.assessment_id,
        strategy_version=assessment.content.strategy_version,
        decision_session_date=assessment.content.decision_session_date,
        target_state=assessment.content.target_state,
        approver="operator_1",
        approved_at=NOW + dt.timedelta(minutes=5),
    )

    # When: a manual approval is constructed
    approval = IntradayPromotionApproval(approval_id=approval_id(content), content=content)

    # Then: its immutable identity differs from the assessment
    assert approval.approval_id != assessment.assessment_id


def test_approval_rejects_a_noncanonical_identity() -> None:
    # Given: approval fields with an unrelated identity
    assessment = _assessment()

    # When / Then: boundary parsing rejects the receipt
    with pytest.raises(ValidationError):
        _ = IntradayPromotionApproval(
            approval_id="f" * 64,
            content=PromotionApprovalContent(
                assessment_id=assessment.assessment_id,
                strategy_version=assessment.content.strategy_version,
                decision_session_date=assessment.content.decision_session_date,
                target_state=assessment.content.target_state,
                approver="operator_1",
                approved_at=NOW,
            ),
        )
