from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from pydantic import ValidationError

from trading_agent.research_identity_models import (
    AgentFamily,
    MarketId,
    StrategyLaneRef,
)
from trading_agent.signal_contract_models import (
    EvidenceRef,
    FeatureValue,
    OpportunityCandidate,
    OpportunitySnapshot,
    QuoteValidation,
    SignalActionability,
    SignalEntryType,
    SignalSide,
    SourceCoverage,
    TradeSignalEnvelope,
    TradeTarget,
)

OBSERVED_AT = dt.datetime(2026, 7, 15, 14, 31, tzinfo=dt.UTC)


def test_opportunity_snapshot_requires_causal_complete_sources() -> None:
    snapshot = _opportunity_snapshot()

    assert snapshot.candidates[0].symbol == "ABCD"
    assert snapshot.strategy_lane.canonical_id == "us_equities/opportunity_manager/ranking_momentum"


def test_opportunity_snapshot_accepts_a_kr_six_digit_symbol() -> None:
    valid = _opportunity_snapshot()
    kr_snapshot = OpportunitySnapshot.model_validate(
        {
            **valid.model_dump(),
            "strategy_lane": _lane(
                AgentFamily.OPPORTUNITY_MANAGER,
                "theme_momentum",
                market_id=MarketId.KR_EQUITIES,
            ),
            "candidates": (
                OpportunityCandidate(
                    symbol="005930",
                    rank=1,
                    score=Decimal("9.5"),
                    features=(FeatureValue(name="theme_strength", value="4.2"),),
                ),
            ),
        }
    )

    assert kr_snapshot.candidates[0].symbol == "005930"


def test_opportunity_snapshot_rejects_incomplete_or_future_sources() -> None:
    valid = _opportunity_snapshot()
    with pytest.raises(ValidationError):
        OpportunitySnapshot.model_validate(
            {
                **valid.model_dump(),
                "source_coverage": (
                    SourceCoverage(
                        source_id="kis_us_rankings",
                        observed_at=OBSERVED_AT,
                        record_count=0,
                        complete=False,
                        failure_reason="provider_timeout",
                    ),
                ),
            }
        )
    with pytest.raises(ValidationError):
        OpportunitySnapshot.model_validate(
            {
                **valid.model_dump(),
                "evidence_refs": (_evidence("future-ranking", OBSERVED_AT + dt.timedelta(seconds=1)),),
            }
        )


def test_opportunity_snapshot_rejects_market_symbol_mismatch() -> None:
    valid = _opportunity_snapshot()
    with pytest.raises(ValidationError):
        OpportunitySnapshot.model_validate(
            {
                **valid.model_dump(),
                "strategy_lane": _lane(
                    AgentFamily.OPPORTUNITY_MANAGER,
                    "theme_momentum",
                    market_id=MarketId.KR_EQUITIES,
                ),
            }
        )


def test_opportunity_snapshot_rejects_wrong_family_rank_or_expiry() -> None:
    valid = _opportunity_snapshot()
    with pytest.raises(ValidationError):
        OpportunitySnapshot.model_validate(
            {
                **valid.model_dump(),
                "strategy_lane": _lane(AgentFamily.DAY_TRADING, "orb"),
            }
        )
    with pytest.raises(ValidationError):
        OpportunitySnapshot.model_validate(
            {
                **valid.model_dump(),
                "candidates": (
                    OpportunityCandidate(
                        symbol="ABCD",
                        rank=2,
                        score=Decimal("9.5"),
                        features=(FeatureValue(name="relative_volume", value="4.2"),),
                    ),
                ),
            }
        )
    with pytest.raises(ValidationError):
        OpportunitySnapshot.model_validate(
            {
                **valid.model_dump(),
                "valid_until": OBSERVED_AT,
            }
        )


def test_conditional_trade_signal_has_prices_expiry_and_provenance() -> None:
    signal = _conditional_signal()

    assert signal.quote_validation is None
    assert signal.entry_price == Decimal("10.10")
    assert signal.evidence_refs[0].observed_at == OBSERVED_AT


def test_current_quote_actionability_requires_a_fresh_acceptable_quote() -> None:
    conditional = _conditional_signal()
    with pytest.raises(ValidationError):
        TradeSignalEnvelope.model_validate(
            {
                **conditional.model_dump(),
                "actionability": SignalActionability.CURRENT_QUOTE_VALIDATED,
            }
        )

    quote = _quote_validation()
    actionable = TradeSignalEnvelope.model_validate(
        {
            **conditional.model_dump(),
            "actionability": SignalActionability.CURRENT_QUOTE_VALIDATED,
            "quote_validation": quote,
        }
    )
    assert actionable.quote_validation == quote

    with pytest.raises(ValidationError):
        TradeSignalEnvelope.model_validate(
            {
                **actionable.model_dump(),
                "quote_validation": quote.model_copy(update={"spread_bps": Decimal("26")}),
            }
        )


def test_conditional_signal_cannot_claim_quote_validation() -> None:
    conditional = _conditional_signal()

    with pytest.raises(ValidationError):
        TradeSignalEnvelope.model_validate(
            {
                **conditional.model_dump(),
                "quote_validation": _quote_validation(),
            }
        )


def test_trade_signal_rejects_invalid_price_geometry_or_future_evidence() -> None:
    valid = _conditional_signal()
    with pytest.raises(ValidationError):
        TradeSignalEnvelope.model_validate({**valid.model_dump(), "stop_price": Decimal("10.10")})
    with pytest.raises(ValidationError):
        TradeSignalEnvelope.model_validate(
            {
                **valid.model_dump(),
                "targets": (TradeTarget(label="1r", price=Decimal("10.10")),),
            }
        )
    with pytest.raises(ValidationError):
        TradeSignalEnvelope.model_validate(
            {
                **valid.model_dump(),
                "evidence_refs": (_evidence("future-signal", OBSERVED_AT + dt.timedelta(seconds=1)),),
            }
        )


def test_trade_signal_supports_directional_short_geometry() -> None:
    valid = _conditional_signal()
    short = TradeSignalEnvelope.model_validate(
        {
            **valid.model_dump(),
            "side": SignalSide.SHORT,
            "entry_price": Decimal("10.00"),
            "stop_price": Decimal("10.20"),
            "targets": (TradeTarget(label="1r", price=Decimal("9.80")),),
        }
    )

    assert short.side is SignalSide.SHORT


def test_trade_signal_rejects_non_trading_family_or_market_symbol() -> None:
    valid = _conditional_signal()
    with pytest.raises(ValidationError):
        TradeSignalEnvelope.model_validate(
            {
                **valid.model_dump(),
                "strategy_lane": _lane(AgentFamily.OPPORTUNITY_MANAGER, "ranking_momentum"),
            }
        )
    with pytest.raises(ValidationError):
        TradeSignalEnvelope.model_validate(
            {
                **valid.model_dump(),
                "strategy_lane": _lane(
                    AgentFamily.DAY_TRADING,
                    "theme_pullback",
                    market_id=MarketId.KR_EQUITIES,
                ),
            }
        )


def _opportunity_snapshot() -> OpportunitySnapshot:
    return OpportunitySnapshot(
        opportunity_id="US-RANKING-20260715T143100Z",
        strategy_lane=_lane(AgentFamily.OPPORTUNITY_MANAGER, "ranking_momentum"),
        producer_strategy_version="ranking-momentum-v1",
        observed_at=OBSERVED_AT,
        valid_until=OBSERVED_AT + dt.timedelta(minutes=1),
        candidates=(
            OpportunityCandidate(
                symbol="ABCD",
                rank=1,
                score=Decimal("9.5"),
                features=(FeatureValue(name="relative_volume", value="4.2"),),
            ),
        ),
        evidence_refs=(_evidence("ranking", OBSERVED_AT),),
        source_coverage=(
            SourceCoverage(
                source_id="kis_us_rankings",
                observed_at=OBSERVED_AT,
                record_count=25,
                complete=True,
            ),
        ),
    )


def _conditional_signal() -> TradeSignalEnvelope:
    return TradeSignalEnvelope(
        signal_id="signal-1",
        strategy_lane=_lane(AgentFamily.DAY_TRADING, "orb"),
        producer_strategy_version="orb-v1",
        symbol="ABCD",
        observed_at=OBSERVED_AT,
        valid_until=OBSERVED_AT + dt.timedelta(minutes=2),
        side=SignalSide.LONG,
        entry_type=SignalEntryType.STOP_TRIGGER,
        entry_price=Decimal("10.10"),
        stop_price=Decimal("9.90"),
        targets=(TradeTarget(label="1r", price=Decimal("10.30")),),
        actionability=SignalActionability.CONDITIONAL,
        invalidation_rule="Invalidate below the stop before entry or on stale data.",
        rationale="Opening range breakout with confirmed relative volume.",
        evidence_refs=(_evidence("recommendations", OBSERVED_AT),),
    )


def _quote_validation() -> QuoteValidation:
    return QuoteValidation(
        bid=Decimal("10.08"),
        ask=Decimal("10.10"),
        observed_at=OBSERVED_AT - dt.timedelta(seconds=1),
        valid_until=OBSERVED_AT + dt.timedelta(seconds=5),
        spread_bps=Decimal("19.82"),
        max_slippage_bps=Decimal("25"),
    )


def _lane(
    family: AgentFamily,
    strategy_id: str,
    *,
    market_id: MarketId = MarketId.US_EQUITIES,
) -> StrategyLaneRef:
    return StrategyLaneRef(
        market_id=market_id,
        agent_family=family,
        strategy_id=strategy_id,
    )


def _evidence(record_id: str, observed_at: dt.datetime) -> EvidenceRef:
    return EvidenceRef(
        namespace="candidate_inputs",
        record_id=record_id,
        observed_at=observed_at,
    )
