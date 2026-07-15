from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Final, override

from trading_agent.models import Recommendation, RecommendationState
from trading_agent.research_identity_models import (
    AgentFamily,
    MarketId,
    StrategyLaneRef,
)
from trading_agent.signal_contract_models import (
    EvidenceRef,
    SignalActionability,
    SignalEntryType,
    SignalSide,
    TradeSignalEnvelope,
    TradeTarget,
)

_LEGACY_STRATEGY_NAME_BY_ID: Final = {
    "gap_and_go": "five_minute_gap_hold",
    "hod_breakout": "first_hod_volume_breakout",
    "orb": "opening_range_breakout",
    "vwap_reclaim": "first_pullback_vwap_reclaim",
}


class InvalidRecommendationSignalProjectionError(ValueError):
    @override
    def __str__(self) -> str:
        return "기존 추천이 승인된 US day SETUP 신호 계약과 일치하지 않습니다"


def project_intraday_recommendation(
    recommendation: Recommendation,
    *,
    strategy_lane: StrategyLaneRef,
    strategy_version: str,
    valid_until: dt.datetime,
    evidence_refs: tuple[EvidenceRef, ...],
    opportunity_id: str | None = None,
) -> TradeSignalEnvelope:
    if (
        recommendation.state is not RecommendationState.SETUP
        or not _aware(recommendation.created_at)
        or strategy_lane.market_id is not MarketId.US_EQUITIES
        or strategy_lane.agent_family is not AgentFamily.DAY_TRADING
        or _LEGACY_STRATEGY_NAME_BY_ID.get(strategy_lane.strategy_id) != recommendation.strategy
    ):
        raise InvalidRecommendationSignalProjectionError

    entry = Decimal(str(recommendation.entry))
    stop = Decimal(str(recommendation.stop))
    return TradeSignalEnvelope(
        signal_id=recommendation.recommendation_id,
        strategy_lane=strategy_lane,
        producer_strategy_version=strategy_version,
        symbol=recommendation.symbol,
        observed_at=recommendation.created_at,
        valid_until=valid_until,
        side=SignalSide.LONG,
        entry_type=SignalEntryType.STOP_TRIGGER,
        entry_price=entry,
        stop_price=stop,
        targets=(
            TradeTarget(label="1r", price=Decimal(str(recommendation.target_1r))),
            TradeTarget(label="2r", price=Decimal(str(recommendation.target_2r))),
        ),
        actionability=SignalActionability.CONDITIONAL,
        invalidation_rule=f"Invalidate below {stop} before entry or when market/data gates fail.",
        rationale=recommendation.rationale,
        evidence_refs=evidence_refs,
        opportunity_id=opportunity_id,
    )


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
