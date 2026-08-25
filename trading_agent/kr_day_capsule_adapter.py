from __future__ import annotations

import datetime as dt
import hashlib
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from typing import Final, override
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from trading_agent.day_strategy_capsule_models import (
    CapsuleAuthorityCeiling,
    StrategyCapsule,
)
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.kis_kr_session_calendar_models import KrSessionCalendarSnapshot
from trading_agent.kr_day_capsule_models import (
    KrDayCapsuleEvaluation,
    KrDayCapsuleEvaluationPayload,
    KrDayCapsuleEvaluationRequest,
)
from trading_agent.kr_intraday_market_gate import (
    KrIntradayGateStatus,
    assess_kr_shadow_entry,
)
from trading_agent.kr_theme_day_setup import (
    KrCompletedMinuteBar,
    KrThemeDaySetupInput,
)
from trading_agent.kr_theme_lane import KR_THEME_OPPORTUNITY_LANE
from trading_agent.research_identity_models import MarketId
from trading_agent.signal_contract_models import OpportunitySnapshot

SEOUL: Final = ZoneInfo("Asia/Seoul")
_SESSION_OPEN: Final = dt.time(9)
_SESSION_CLOSE: Final = dt.time(15, 30)
_LATEST_BAR_DELAY: Final = dt.timedelta(seconds=30)
_REQUIRED_LEADER_FEATURES: Final = frozenset({"is_leader", "theme_name", "trading_value_krw", "volume_ratio"})


class InvalidKrDayCapsuleEvaluationError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR day capsule evaluation input is invalid"


def adapt_kr_day_capsule_evaluation(
    source: KrDayCapsuleEvaluationRequest,
    *,
    allow_market_blocked: bool = False,
) -> KrDayCapsuleEvaluation:
    try:
        request = KrDayCapsuleEvaluationRequest.model_validate(source.model_dump(mode="python"))
        session_date = _require_current_open_session(request.calendar, request.evaluated_at)
        collection_cycle_id, symbol = _require_research_lineage(
            request.capsule,
            request.opportunity,
            request.evaluated_at,
            allow_expired=False,
        )
        bars = _require_completed_bar_chain(request, symbol, session_date)
        _require_market(request, symbol, bars[-1], allow_market_blocked=allow_market_blocked)
        setup_input = KrThemeDaySetupInput(
            opportunity=request.opportunity,
            bars=bars,
            producer_strategy_version=request.capsule.capsule_id,
            evaluated_at=request.evaluated_at,
            max_slippage_bps=request.max_slippage_bps,
        )
        payload = {
            "capsule_id": request.capsule.capsule_id,
            "hypothesis_version_id": request.capsule.hypothesis_version_id,
            "decision_input_sha256": hashlib.sha256(
                canonical_experiment_ledger_json(request).encode()
            ).hexdigest(),
            "session_date": session_date,
            "calendar_snapshot_id": request.calendar.snapshot_id,
            "calendar_receipt_sha256": request.calendar.payload.receipt_sha256,
            "collection_cycle_id": collection_cycle_id,
            "opportunity_id": request.opportunity.opportunity_id,
            "symbol": symbol,
            "evaluated_at": request.evaluated_at,
            "completed_bar_cursor": bars[-1].end_at,
            "setup_input": setup_input,
            "market": request.market,
            "authority_ceiling": CapsuleAuthorityCeiling.RESEARCH_ONLY,
            "trading_authority": False,
        }
        parsed = KrDayCapsuleEvaluationPayload.model_validate(payload)
        return KrDayCapsuleEvaluation.model_validate(
            payload | {"evaluation_id": KrDayCapsuleEvaluation.canonical_id_for(parsed)}
        )
    except (AttributeError, TypeError, ValidationError, ValueError):
        raise InvalidKrDayCapsuleEvaluationError from None


def adapt_kr_day_capsule_management_evaluation(
    source: KrDayCapsuleEvaluationRequest,
    *,
    allow_market_blocked: bool = False,
) -> KrDayCapsuleEvaluation:
    try:
        request = KrDayCapsuleEvaluationRequest.model_validate(source.model_dump(mode="python"))
        session_date = _require_current_open_session(request.calendar, request.evaluated_at)
        collection_cycle_id, symbol = _require_research_lineage(
            request.capsule, request.opportunity, request.evaluated_at, allow_expired=True
        )
        bars = _require_completed_bar_chain(request, symbol, session_date)
        _require_market(request, symbol, bars[-1], allow_market_blocked=allow_market_blocked)
        setup_input = KrThemeDaySetupInput(
            opportunity=request.opportunity,
            bars=bars,
            producer_strategy_version=request.capsule.capsule_id,
            evaluated_at=request.evaluated_at,
            max_slippage_bps=request.max_slippage_bps,
        )
        payload = {
            "capsule_id": request.capsule.capsule_id,
            "hypothesis_version_id": request.capsule.hypothesis_version_id,
            "decision_input_sha256": hashlib.sha256(
                canonical_experiment_ledger_json(request).encode()
            ).hexdigest(),
            "session_date": session_date,
            "calendar_snapshot_id": request.calendar.snapshot_id,
            "calendar_receipt_sha256": request.calendar.payload.receipt_sha256,
            "collection_cycle_id": collection_cycle_id,
            "opportunity_id": request.opportunity.opportunity_id,
            "symbol": symbol,
            "evaluated_at": request.evaluated_at,
            "completed_bar_cursor": bars[-1].end_at,
            "setup_input": setup_input,
            "market": request.market,
            "authority_ceiling": CapsuleAuthorityCeiling.RESEARCH_ONLY,
            "trading_authority": False,
        }
        parsed = KrDayCapsuleEvaluationPayload.model_validate(payload)
        return KrDayCapsuleEvaluation.model_validate(
            payload | {"evaluation_id": KrDayCapsuleEvaluation.canonical_id_for(parsed)}
        )
    except (AttributeError, TypeError, ValidationError, ValueError):
        raise InvalidKrDayCapsuleEvaluationError from None


def _require_current_open_session(
    calendar: KrSessionCalendarSnapshot,
    evaluated_at: dt.datetime,
) -> dt.date:
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise InvalidKrDayCapsuleEvaluationError
    local = evaluated_at.astimezone(SEOUL)
    matches = tuple(day for day in calendar.payload.days if day.session_date == local.date())
    if (
        calendar.payload.observed_at > evaluated_at
        or len(matches) != 1
        or not matches[0].business_day
        or not matches[0].trading_day
        or not matches[0].open_day
        or not _SESSION_OPEN <= local.time() < _SESSION_CLOSE
    ):
        raise InvalidKrDayCapsuleEvaluationError
    return local.date()


def _require_research_lineage(
    capsule: StrategyCapsule,
    opportunity: OpportunitySnapshot,
    evaluated_at: dt.datetime,
    *,
    allow_expired: bool,
) -> tuple[str, str]:
    leader = opportunity.candidates[0]
    features = {item.name: item.value for item in leader.features}
    cycle_ids = tuple(item.record_id for item in opportunity.evidence_refs if item.namespace == "kr/collection_cycle")
    if (
        capsule.market_id is not MarketId.KR_EQUITIES
        or capsule.authority_ceiling is not CapsuleAuthorityCeiling.RESEARCH_ONLY
        or capsule.trading_authority is not False
        or capsule.evaluation_cadence != "each_completed_bar"
        or capsule.published_at > evaluated_at.astimezone(dt.UTC)
        or opportunity.strategy_lane != KR_THEME_OPPORTUNITY_LANE
        or opportunity.observed_at > evaluated_at
        or (not allow_expired and evaluated_at >= opportunity.valid_until)
        or (allow_expired and opportunity.observed_at >= opportunity.valid_until)
        or leader.rank != 1
        or not features.keys() >= _REQUIRED_LEADER_FEATURES
        or features["is_leader"] != "true"
        or not _positive_feature(features, "trading_value_krw")
        or not _positive_feature(features, "volume_ratio")
        or len(cycle_ids) != 1
        or not opportunity.source_coverage
        or any(not item.complete or item.observed_at > evaluated_at for item in opportunity.source_coverage)
    ):
        raise InvalidKrDayCapsuleEvaluationError
    return cycle_ids[0], leader.symbol


def _positive_feature(features: dict[str, str], name: str) -> bool:
    try:
        value = Decimal(features[name])
    except (InvalidOperation, KeyError):
        return False
    return value.is_finite() and value > 0


def _require_completed_bar_chain(
    request: KrDayCapsuleEvaluationRequest,
    symbol: str,
    session_date: dt.date,
) -> tuple[KrCompletedMinuteBar, ...]:
    checked = tuple(KrCompletedMinuteBar.model_validate(item.model_dump(mode="python")) for item in request.bars)
    first_local = checked[0].start_at.astimezone(SEOUL)
    latest = checked[-1]
    if (
        first_local.date() != session_date
        or first_local.time() != _SESSION_OPEN
        or latest.end_at > request.evaluated_at
        or latest.observed_at > request.evaluated_at
        or request.evaluated_at - latest.observed_at > _LATEST_BAR_DELAY
        or any(
            item.symbol != symbol
            or item.start_at.astimezone(SEOUL).date() != session_date
            or item.end_at > request.evaluated_at
            or item.observed_at > request.evaluated_at
            for item in checked
        )
        or any(
            current.start_at != previous.end_at or current.observed_at < previous.observed_at
            for previous, current in pairwise(checked)
        )
    ):
        raise InvalidKrDayCapsuleEvaluationError
    return checked


def _require_market(
    request: KrDayCapsuleEvaluationRequest,
    symbol: str,
    latest: KrCompletedMinuteBar,
    *,
    allow_market_blocked: bool,
) -> None:
    gate = assess_kr_shadow_entry(request.market, request.evaluated_at)
    if (
        request.market.symbol != symbol
        or not request.opportunity.observed_at
        <= latest.observed_at
        <= request.market.observed_at
        <= request.evaluated_at
        or (not allow_market_blocked and gate.status is not KrIntradayGateStatus.ELIGIBLE)
    ):
        raise InvalidKrDayCapsuleEvaluationError


__all__ = (
    "InvalidKrDayCapsuleEvaluationError",
    "adapt_kr_day_capsule_evaluation",
    "adapt_kr_day_capsule_management_evaluation",
)
