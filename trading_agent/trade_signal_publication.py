from __future__ import annotations

import datetime as dt
from typing import Final, Literal, Self, override

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.models import Recommendation, RecommendationState
from trading_agent.recommendation_signal_projection import (
    InvalidRecommendationSignalProjectionError,
    project_intraday_recommendation,
)
from trading_agent.research_identity_models import (
    AgentFamily,
    MarketId,
    StrategyLaneRef,
)
from trading_agent.signal_contract_models import (
    EvidenceRef,
    OpportunitySnapshot,
    TradeSignalEnvelope,
)

MAX_PUBLICATION_AGE: Final = dt.timedelta(minutes=5)
SIGNAL_VALIDITY: Final = dt.timedelta(seconds=60)


class InvalidTradeSignalPublicationInputError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

    @override
    def __str__(self) -> str:
        return f"트레이딩 신호 발행 입력이 유효하지 않습니다: {self.reason}"


class TradeSignalPublication(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    published_at: dt.datetime
    signal: TradeSignalEnvelope

    @model_validator(mode="after")
    def validate_publication(self) -> Self:
        if (
            not _aware(self.published_at)
            or self.published_at < self.signal.observed_at
            or self.published_at >= self.signal.valid_until
            or self.published_at - self.signal.observed_at >= MAX_PUBLICATION_AGE
        ):
            raise ValueError("invalid trade signal publication")
        return self


def project_trade_signal_publications(
    recommendations: tuple[Recommendation, ...],
    *,
    strategy_lane: StrategyLaneRef,
    strategy_version: str,
    opportunity: OpportunitySnapshot,
    published_at: dt.datetime,
    created_after: dt.datetime,
) -> tuple[TradeSignalPublication, ...]:
    _validate_projection_inputs(
        recommendations,
        strategy_lane=strategy_lane,
        opportunity=opportunity,
        published_at=published_at,
        created_after=created_after,
    )
    candidate_symbols = {candidate.symbol for candidate in opportunity.candidates}
    publications: list[TradeSignalPublication] = []
    for recommendation in sorted(
        recommendations,
        key=lambda item: (item.created_at, item.symbol, item.recommendation_id),
    ):
        if not _eligible(
            recommendation,
            opportunity=opportunity,
            candidate_symbols=candidate_symbols,
            published_at=published_at,
            created_after=created_after,
        ):
            continue
        valid_until = min(
            recommendation.created_at + SIGNAL_VALIDITY,
            opportunity.valid_until,
        )
        if published_at >= valid_until:
            continue
        evidence_refs = tuple(
            sorted(
                (
                    EvidenceRef(
                        namespace="opportunity/snapshot",
                        record_id=opportunity.opportunity_id,
                        observed_at=opportunity.observed_at,
                    ),
                    EvidenceRef(
                        namespace="paper/recommendation",
                        record_id=recommendation.recommendation_id,
                        observed_at=recommendation.created_at,
                    ),
                ),
                key=lambda item: item.canonical_id,
            )
        )
        try:
            signal = project_intraday_recommendation(
                recommendation,
                strategy_lane=strategy_lane,
                strategy_version=strategy_version,
                valid_until=valid_until,
                evidence_refs=evidence_refs,
                opportunity_id=opportunity.opportunity_id,
            )
        except InvalidRecommendationSignalProjectionError:
            continue
        publications.append(
            TradeSignalPublication(
                published_at=published_at,
                signal=signal,
            )
        )
    return tuple(publications)


def _validate_projection_inputs(
    recommendations: tuple[Recommendation, ...],
    *,
    strategy_lane: StrategyLaneRef,
    opportunity: OpportunitySnapshot,
    published_at: dt.datetime,
    created_after: dt.datetime,
) -> None:
    recommendation_ids = tuple(item.recommendation_id for item in recommendations)
    if (
        not _aware(published_at)
        or not _aware(created_after)
        or created_after > published_at
        or opportunity.observed_at > published_at
        or strategy_lane.market_id is not MarketId.US_EQUITIES
        or strategy_lane.agent_family is not AgentFamily.DAY_TRADING
        or opportunity.strategy_lane.market_id is not strategy_lane.market_id
        or opportunity.strategy_lane.agent_family is not AgentFamily.OPPORTUNITY_MANAGER
        or len(recommendation_ids) != len(set(recommendation_ids))
        or any(not _aware(item.created_at) for item in recommendations)
    ):
        raise InvalidTradeSignalPublicationInputError("시각, lane 또는 추천 ID가 일관되지 않습니다")


def _eligible(
    recommendation: Recommendation,
    *,
    opportunity: OpportunitySnapshot,
    candidate_symbols: set[str],
    published_at: dt.datetime,
    created_after: dt.datetime,
) -> bool:
    return (
        recommendation.state is RecommendationState.SETUP
        and recommendation.symbol in candidate_symbols
        and created_after <= recommendation.created_at <= published_at
        and opportunity.observed_at <= recommendation.created_at < opportunity.valid_until
        and published_at - recommendation.created_at < MAX_PUBLICATION_AGE
    )


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
