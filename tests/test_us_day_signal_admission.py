from __future__ import annotations

import datetime as dt
from dataclasses import replace

import pytest

from tests.day_research_review_support import decision
from tests.test_us_day_situation_projection import EVALUATED_AT, _inputs, _project
from tests.test_us_day_thesis_runtime import _champion, _markets, _valid_response
from trading_agent.day_research_review import DayExecutionSessionContext, build_execution_eligibility
from trading_agent.day_research_review_models import PromotionDecision
from trading_agent.day_research_review_types import DayExecutionEligibilityStatus, DayPromotionStatus
from trading_agent.lane_identity_models import LaneId
from trading_agent.paper_execution_models import IntentId
from trading_agent.paper_risk import DEFAULT_PAPER_RISK_CONFIG
from trading_agent.signal_contract_models import SignalActionability
from trading_agent.us_day_signal_admission import (
    InvalidUsDaySignalAdmissionError,
    UsDaySignalAdmissionRequest,
    admit_us_day_signal,
)
from trading_agent.us_day_thesis_runtime import reason_trade_thesis


def test_champion_current_quote_signal_is_admitted_with_exact_identity() -> None:
    # Given
    request = _eligible_request()

    # When
    admitted = admit_us_day_signal(request)

    # Then
    assert admitted.candidate_intent.intent_id == IntentId(request.thesis.thesis_id)
    assert admitted.latest_bar.symbol == request.signal.symbol
    assert admitted.estimated_spread_bps == float(request.current_market.quote.spread_bps)
    assert admitted.config == DEFAULT_PAPER_RISK_CONFIG


@pytest.mark.parametrize(
    "change",
    (
        "conditional_signal",
        "wrong_champion",
        "wrong_session",
        "expired_eligibility",
    ),
)
def test_signal_admission_fails_closed_when_lineage_or_current_evidence_differs(change: str) -> None:
    # Given
    request = _eligible_request()
    match change:
        case "conditional_signal":
            changed = replace(
                request,
                signal=request.signal.model_copy(update={"actionability": SignalActionability.CONDITIONAL}),
            )
        case "wrong_champion":
            changed = replace(
                request,
                champion=request.champion.model_copy(update={"strategy_version": "other-version"}),
            )
        case "wrong_session":
            changed = replace(request, session_id="XNYS-2026-07-13")
        case "expired_eligibility":
            payload = request.execution_eligibility.payload.model_copy(
                update={
                    "status": DayExecutionEligibilityStatus.EXPIRED,
                    "expires_at": request.evaluated_at,
                    "blockers": ("expired",),
                    "paper_order_authority": False,
                }
            )
            changed = replace(
                request,
                execution_eligibility=request.execution_eligibility.model_copy(update={"payload": payload}),
            )
        case unreachable:
            raise AssertionError(unreachable)

    # When / Then
    with pytest.raises(InvalidUsDaySignalAdmissionError):
        admit_us_day_signal(changed)


def _eligible_request() -> UsDaySignalAdmissionRequest:
    situation = _project(_inputs())
    promotion = decision(status=DayPromotionStatus.PAPER_CHAMPION_CANDIDATE)
    champion = _champion().model_copy(update={"version_id": promotion.payload.hypothesis_version_id})
    response = _valid_response() | {"agent_version_id": champion.version_id}
    reasoned = reason_trade_thesis(response, champion, situation, _markets())
    assert reasoned.signal is not None
    market = next(item for item in _markets() if item.symbol == reasoned.signal.symbol)
    context = DayExecutionSessionContext(
        session_date=EVALUATED_AT.astimezone(dt.UTC).date(),
        sequence=1,
        previous_event_id=None,
        clean_commit_sha256="a" * 64,
        risk_policy_sha256="b" * 64,
        effective_at=EVALUATED_AT - dt.timedelta(seconds=1),
        expires_at=EVALUATED_AT + dt.timedelta(minutes=1),
    )
    eligibility = build_execution_eligibility(promotion, context, _champion_authority(promotion, EVALUATED_AT))
    return UsDaySignalAdmissionRequest(
        session_id=situation.session_id,
        lane_id=LaneId.INTRADAY_MOMENTUM,
        thesis=reasoned.thesis,
        signal=reasoned.signal,
        champion=champion,
        situation=situation,
        current_market=market,
        promotion=promotion,
        execution_eligibility=eligibility,
        liquidity_allowed_quantity=100,
        evaluated_at=EVALUATED_AT,
    )


def _champion_authority(promotion: PromotionDecision, now: dt.datetime):
    from trading_agent.day_research_review import seal_owner_authority_event
    from trading_agent.day_research_review_models import DayOwnerAuthorityEventPayload
    from trading_agent.day_research_review_types import DayExecutionAuthorityClass

    payload = promotion.payload
    return seal_owner_authority_event(
        DayOwnerAuthorityEventPayload(
            decision_id=promotion.decision_id,
            capsule_id=payload.capsule_id,
            hypothesis_version_id=payload.hypothesis_version_id,
            market_id=payload.market_id,
            authority_class=DayExecutionAuthorityClass.PAPER_CHAMPION,
            owner_id="owner_1",
            approved_at=now - dt.timedelta(seconds=2),
            effective_after_session=payload.effective_after_session,
        )
    )
