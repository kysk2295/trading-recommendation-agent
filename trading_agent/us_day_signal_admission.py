from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import override

from trading_agent.day_research_review_models import ExecutionEligibility, PromotionDecision
from trading_agent.day_research_review_types import DayExecutionEligibilityStatus, DayPromotionStatus
from trading_agent.lane_identity_models import LaneId
from trading_agent.paper_execution_models import IntentId, PaperOrderIntent, PaperOrderSide
from trading_agent.paper_operating_session_models import PaperOrderAdmissionRequest
from trading_agent.paper_order_gate_models import LatestCompletedBar
from trading_agent.paper_risk import DEFAULT_PAPER_RISK_CONFIG
from trading_agent.research_identity_models import AgentFamily, MarketId
from trading_agent.signal_contract_models import SignalActionability, SignalSide, TradeSignalEnvelope
from trading_agent.us_day_situation_models import UsDaySituationMap
from trading_agent.us_day_thesis_models import (
    DayTradeDecision,
    UsDayChampion,
    UsDayCurrentMarket,
    UsDayTradeThesis,
    situation_id_for,
)
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds


@dataclass(frozen=True, slots=True)
class InvalidUsDaySignalAdmissionError(ValueError):
    reason: str

    @override
    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class UsDaySignalAdmissionRequest:
    session_id: str
    lane_id: LaneId
    thesis: UsDayTradeThesis
    signal: TradeSignalEnvelope
    champion: UsDayChampion
    situation: UsDaySituationMap
    current_market: UsDayCurrentMarket
    promotion: PromotionDecision
    execution_eligibility: ExecutionEligibility
    liquidity_allowed_quantity: int
    evaluated_at: dt.datetime


def admit_us_day_signal(request: UsDaySignalAdmissionRequest) -> PaperOrderAdmissionRequest:
    _require_current_champion_signal(request)
    signal = request.signal
    market = request.current_market
    match signal.side:
        case SignalSide.LONG:
            side = PaperOrderSide.BUY
        case SignalSide.SHORT:
            raise InvalidUsDaySignalAdmissionError("short_signal_forbidden")
    return PaperOrderAdmissionRequest(
        latest_bar=LatestCompletedBar(market.symbol, market.current_bar.timestamp, request.thesis.observed_at),
        candidate_intent=PaperOrderIntent(
            IntentId(signal.signal_id),
            signal.strategy_lane.strategy_id,
            signal.producer_strategy_version,
            signal.symbol,
            signal.observed_at,
            side,
            float(signal.entry_price),
            float(signal.stop_price),
            float(signal.targets[0].price),
            float(signal.targets[1].price),
        ),
        liquidity_allowed_quantity=request.liquidity_allowed_quantity,
        estimated_spread_bps=float(market.quote.spread_bps),
        config=DEFAULT_PAPER_RISK_CONFIG,
    )


def _require_current_champion_signal(request: UsDaySignalAdmissionRequest) -> None:
    thesis = request.thesis
    signal = request.signal
    champion = request.champion
    situation = request.situation
    market = request.current_market
    promotion = request.promotion.payload
    eligibility = request.execution_eligibility.payload
    now = request.evaluated_at
    bounds = regular_session_bounds(now.astimezone(NEW_YORK).date()) if _aware(now) else None
    current_session = bounds is not None and bounds[0] <= now.astimezone(NEW_YORK) < bounds[1]
    expected_bar = now.astimezone(NEW_YORK).replace(second=0, microsecond=0) - dt.timedelta(minutes=1)
    if (
        request.lane_id is not LaneId.INTRADAY_MOMENTUM
        or thesis.decision is not DayTradeDecision.RECOMMEND
        or signal.actionability is not SignalActionability.CURRENT_QUOTE_VALIDATED
        or signal.quote_validation is None
        or signal.signal_id != thesis.thesis_id
        or signal.symbol != thesis.symbol
        or signal.observed_at != thesis.observed_at
        or signal.valid_until != thesis.valid_until
        or signal.entry_price != thesis.entry_price
        or signal.stop_price != thesis.stop_price
        or signal.targets != thesis.targets
        or signal.invalidation_rule != thesis.invalidation_rule
        or signal.rationale != thesis.rationale
        or signal.producer_strategy_version != champion.strategy_version
        or signal.strategy_lane != champion.strategy_lane
        or champion.version_id != thesis.agent_version_id
        or champion.version_id != promotion.hypothesis_version_id
        or promotion.status is not DayPromotionStatus.PAPER_CHAMPION_CANDIDATE
        or promotion.market_id is not MarketId.US_EQUITIES
        or eligibility.decision_id != request.promotion.decision_id
        or eligibility.hypothesis_version_id != champion.version_id
        or eligibility.status is not DayExecutionEligibilityStatus.ELIGIBLE
        or not eligibility.paper_order_authority
        or eligibility.session_date != situation.session_date
        or not eligibility.effective_at <= now < eligibility.expires_at
        or signal.strategy_lane.market_id is not MarketId.US_EQUITIES
        or signal.strategy_lane.agent_family is not AgentFamily.DAY_TRADING
        or request.session_id != situation.session_id
        or situation.session_date != now.astimezone(NEW_YORK).date()
        or thesis.situation_id != situation_id_for(situation)
        or market.symbol != signal.symbol
        or market.current_bar.timestamp.astimezone(NEW_YORK) != expected_bar
        or situation.completed_bar_at != market.current_bar.timestamp
        or market.current_bar_ref not in situation.evidence_refs
        or market.quote != signal.quote_validation
        or not market.quote.observed_at <= signal.observed_at <= now <= market.quote.valid_until
        or market.quote.spread_bps > market.quote.max_slippage_bps
        or request.liquidity_allowed_quantity <= 0
        or not current_session
    ):
        raise InvalidUsDaySignalAdmissionError("signal_not_eligible_for_paper")


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = ("InvalidUsDaySignalAdmissionError", "UsDaySignalAdmissionRequest", "admit_us_day_signal")
