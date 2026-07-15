from __future__ import annotations

import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from trading_agent.kis_us_quote import KisUsLevelOneQuote
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
from trading_agent.trade_signal_publication import TradeSignalPublication
from trading_agent.us_quote_actionability import (
    MAX_ENTRY_SLIPPAGE_BPS,
    MAX_QUOTE_SPREAD_BPS,
    QUOTE_FRESHNESS,
    QuoteActionabilityAssessment,
    QuoteAssessmentStatus,
    UsQuoteSnapshot,
    assess_us_quote,
    provider_failed_assessment,
)

NEW_YORK = ZoneInfo("America/New_York")
AT = dt.datetime(2026, 7, 15, 13, 20, tzinfo=NEW_YORK)
SCAN_STARTED_AT = AT - dt.timedelta(seconds=20)


def test_fresh_quote_below_trigger_creates_waiting_signal() -> None:
    base = _conditional_publication(entry="10.10", stop="9.90")
    quote = _quote(provider_at=AT - dt.timedelta(seconds=1), bid="10.07", ask="10.08")

    decision = assess_us_quote(
        base,
        quote,
        scan_started_at=SCAN_STARTED_AT,
        evaluated_at=AT,
    )

    assert decision.assessment.status is QuoteAssessmentStatus.VALIDATED_WAITING
    assert decision.snapshot is not None
    assert decision.assessment.quote_id == decision.snapshot.quote_id
    assert decision.derived_publication is not None
    signal = decision.derived_publication.signal
    assert signal.actionability is SignalActionability.CURRENT_QUOTE_VALIDATED
    assert signal.signal_id.startswith("us-quote-signal:")
    assert decision.assessment.derived_signal_id == signal.signal_id
    assert signal.strategy_lane == base.signal.strategy_lane
    assert signal.producer_strategy_version == base.signal.producer_strategy_version
    assert signal.entry_price == base.signal.entry_price
    assert signal.stop_price == base.signal.stop_price
    assert signal.targets == base.signal.targets
    assert signal.rationale == base.signal.rationale
    assert signal.invalidation_rule == base.signal.invalidation_rule
    assert signal.opportunity_id == base.signal.opportunity_id
    assert signal.quote_validation is not None
    assert signal.quote_validation.valid_until == quote.provider_observed_at + QUOTE_FRESHNESS
    assert signal.quote_validation.max_slippage_bps == MAX_QUOTE_SPREAD_BPS
    assert tuple(item.namespace for item in signal.evidence_refs) == (
        "paper/recommendation",
        "quote/snapshot",
        "signal/conditional",
    )


def test_quote_at_trigger_creates_trigger_reached_signal() -> None:
    decision = _assess(_quote(bid="10.09", ask="10.10"))

    assert decision.assessment.status is QuoteAssessmentStatus.VALIDATED_TRIGGER_REACHED
    assert decision.derived_publication is not None


@pytest.mark.parametrize(
    ("age", "expected", "published"),
    (
        (
            dt.timedelta(seconds=4, microseconds=999_000),
            QuoteAssessmentStatus.VALIDATED_WAITING,
            True,
        ),
        (
            dt.timedelta(seconds=5),
            QuoteAssessmentStatus.STALE_QUOTE,
            False,
        ),
    ),
)
def test_quote_freshness_has_strict_five_second_boundary(
    age: dt.timedelta,
    expected: QuoteAssessmentStatus,
    published: bool,
) -> None:
    decision = _assess(_quote(provider_at=AT - age))

    assert decision.assessment.status is expected
    assert (decision.derived_publication is not None) is published
    assert decision.snapshot is not None


def test_future_quote_is_blocked_with_snapshot_evidence() -> None:
    decision = _assess(_quote(provider_at=AT + dt.timedelta(microseconds=1)))

    assert decision.assessment.status is QuoteAssessmentStatus.FUTURE_QUOTE
    assert decision.snapshot is not None
    assert decision.derived_publication is None


def test_closed_market_is_blocked_before_quote_normalization() -> None:
    weekend = dt.datetime(2026, 7, 18, 13, 20, tzinfo=NEW_YORK)
    decision = assess_us_quote(
        _conditional_publication(anchor=weekend),
        _quote(provider_at=weekend - dt.timedelta(seconds=1)),
        scan_started_at=weekend - dt.timedelta(seconds=20),
        evaluated_at=weekend,
    )

    assert decision.assessment.status is QuoteAssessmentStatus.MARKET_CLOSED
    assert decision.snapshot is None
    assert decision.derived_publication is None


def test_previous_session_quote_is_stale() -> None:
    decision = _assess(_quote(provider_at=AT - dt.timedelta(days=1)))

    assert decision.assessment.status is QuoteAssessmentStatus.STALE_QUOTE
    assert decision.snapshot is not None
    assert decision.derived_publication is None


def test_pre_market_quote_is_stale_even_when_less_than_five_seconds_old() -> None:
    evaluated_at = dt.datetime(2026, 7, 15, 9, 30, 1, tzinfo=NEW_YORK)
    decision = assess_us_quote(
        _conditional_publication(anchor=evaluated_at),
        _quote(
            provider_at=evaluated_at - dt.timedelta(seconds=2),
            received_at=evaluated_at,
        ),
        scan_started_at=evaluated_at - dt.timedelta(seconds=20),
        evaluated_at=evaluated_at,
    )

    assert decision.assessment.status is QuoteAssessmentStatus.STALE_QUOTE
    assert decision.derived_publication is None


@pytest.mark.parametrize(
    ("bid", "ask", "expected", "published"),
    (
        (
            "9.9875",
            "10.0125",
            QuoteAssessmentStatus.VALIDATED_WAITING,
            True,
        ),
        (
            "9.9875",
            "10.0125001",
            QuoteAssessmentStatus.SPREAD_TOO_WIDE,
            False,
        ),
    ),
)
def test_quote_spread_has_inclusive_twenty_five_bp_boundary(
    bid: str,
    ask: str,
    expected: QuoteAssessmentStatus,
    published: bool,
) -> None:
    decision = _assess(_quote(bid=bid, ask=ask))

    assert decision.assessment.status is expected
    assert (decision.derived_publication is not None) is published
    assert decision.snapshot is not None
    if published:
        assert decision.snapshot.spread_bps == MAX_QUOTE_SPREAD_BPS


def test_bid_at_stop_invalidates_setup() -> None:
    decision = _assess(_quote(bid="9.90", ask="9.91"))

    assert decision.assessment.status is QuoteAssessmentStatus.SETUP_INVALIDATED
    assert decision.snapshot is not None
    assert decision.derived_publication is None


@pytest.mark.parametrize(
    ("ask", "expected", "published"),
    (
        (
            "10.1202",
            QuoteAssessmentStatus.VALIDATED_TRIGGER_REACHED,
            True,
        ),
        (
            "10.120201",
            QuoteAssessmentStatus.ENTRY_SLIPPAGE_EXCEEDED,
            False,
        ),
    ),
)
def test_entry_slippage_has_inclusive_twenty_bp_boundary(
    ask: str,
    expected: QuoteAssessmentStatus,
    published: bool,
) -> None:
    decision = _assess(_quote(bid="10.1192", ask=ask))

    assert decision.assessment.status is expected
    assert (decision.derived_publication is not None) is published
    assert Decimal("20") == MAX_ENTRY_SLIPPAGE_BPS


def test_expired_base_signal_is_blocked_before_quote_normalization() -> None:
    decision = assess_us_quote(
        _conditional_publication(valid_until=AT),
        _quote(bid="10.08", ask="10.09"),
        scan_started_at=SCAN_STARTED_AT,
        evaluated_at=AT,
    )

    assert decision.assessment.status is QuoteAssessmentStatus.SETUP_INVALIDATED
    assert decision.snapshot is None
    assert decision.derived_publication is None


def test_invalid_quote_is_blocked_without_snapshot_identity() -> None:
    decision = _assess(_quote(bid="NaN", ask="10.09"))

    assert decision.assessment.status is QuoteAssessmentStatus.INVALID_QUOTE
    assert decision.assessment.quote_id is None
    assert decision.snapshot is None
    assert decision.derived_publication is None


def test_quote_symbol_mismatch_is_invalid_quote() -> None:
    decision = _assess(_quote(symbol="OTHER"))

    assert decision.assessment.status is QuoteAssessmentStatus.INVALID_QUOTE
    assert decision.snapshot is None


def test_quote_assessment_is_deterministic() -> None:
    base = _conditional_publication(signal_id="base:" + "x" * 500)
    quote = _quote()

    first = assess_us_quote(
        base,
        quote,
        scan_started_at=SCAN_STARTED_AT,
        evaluated_at=AT,
    )
    second = assess_us_quote(
        base,
        quote,
        scan_started_at=SCAN_STARTED_AT,
        evaluated_at=AT,
    )

    assert second == first
    assert first.snapshot is not None
    assert len(first.snapshot.quote_id) < 128
    assert first.derived_publication is not None
    assert len(first.derived_publication.signal.signal_id) < 128


def test_independent_receipts_create_distinct_quote_and_signal_identities() -> None:
    first = _assess(
        _quote(received_at=AT - dt.timedelta(milliseconds=100))
    )
    second = _assess(
        _quote(received_at=AT - dt.timedelta(milliseconds=50))
    )

    assert first.snapshot is not None
    assert second.snapshot is not None
    assert first.snapshot.quote_id != second.snapshot.quote_id
    assert first.derived_publication is not None
    assert second.derived_publication is not None
    assert (
        first.derived_publication.signal.signal_id
        != second.derived_publication.signal.signal_id
    )


def test_one_base_and_scan_cycle_have_one_assessment_identity() -> None:
    base = _conditional_publication()
    provider_failed = provider_failed_assessment(
        base,
        scan_started_at=SCAN_STARTED_AT,
        evaluated_at=AT,
    )
    validated = assess_us_quote(
        base,
        _quote(),
        scan_started_at=SCAN_STARTED_AT,
        evaluated_at=AT + dt.timedelta(milliseconds=100),
    ).assessment
    next_cycle = provider_failed_assessment(
        base,
        scan_started_at=SCAN_STARTED_AT + dt.timedelta(microseconds=1),
        evaluated_at=AT,
    )

    assert validated.assessment_id == provider_failed.assessment_id
    assert next_cycle.assessment_id != provider_failed.assessment_id


def test_snapshot_identity_and_assessment_geometry_are_validated() -> None:
    decision = _assess(_quote())
    assert decision.snapshot is not None
    assert decision.derived_publication is not None

    with pytest.raises(ValidationError):
        _ = UsQuoteSnapshot.model_validate(
            {
                **decision.snapshot.model_dump(),
                "quote_id": f"us-quote:{'0' * 64}",
            }
        )
    with pytest.raises(ValidationError):
        _ = QuoteActionabilityAssessment.model_validate(
            {
                **decision.assessment.model_dump(),
                "derived_signal_id": None,
            }
        )


def test_provider_failure_assessment_contains_no_quote_claim() -> None:
    base = _conditional_publication()

    first = provider_failed_assessment(
        base,
        scan_started_at=SCAN_STARTED_AT,
        evaluated_at=AT,
    )
    second = provider_failed_assessment(
        base,
        scan_started_at=SCAN_STARTED_AT,
        evaluated_at=AT,
    )

    assert second == first
    assert first.status is QuoteAssessmentStatus.PROVIDER_FAILED
    assert first.quote_id is None
    assert first.derived_signal_id is None


def _assess(quote: KisUsLevelOneQuote):
    return assess_us_quote(
        _conditional_publication(),
        quote,
        scan_started_at=SCAN_STARTED_AT,
        evaluated_at=AT,
    )


def _quote(
    *,
    provider_at: dt.datetime = AT - dt.timedelta(seconds=1),
    received_at: dt.datetime = AT - dt.timedelta(milliseconds=100),
    exchange: str = "NAS",
    symbol: str = "ACME",
    bid: str = "10.08",
    ask: str = "10.09",
) -> KisUsLevelOneQuote:
    return KisUsLevelOneQuote(
        exchange=exchange,
        symbol=symbol,
        provider_observed_at=provider_at,
        received_at=received_at,
        bid=Decimal(bid),
        ask=Decimal(ask),
        bid_size=1_000,
        ask_size=900,
    )


def _conditional_publication(
    *,
    anchor: dt.datetime = AT,
    entry: str = "10.10",
    stop: str = "9.90",
    signal_id: str = "base-signal-1",
    valid_until: dt.datetime | None = None,
) -> TradeSignalPublication:
    observed_at = anchor - dt.timedelta(seconds=10)
    entry_price = Decimal(entry)
    signal = TradeSignalEnvelope(
        signal_id=signal_id,
        strategy_lane=StrategyLaneRef(
            market_id=MarketId.US_EQUITIES,
            agent_family=AgentFamily.DAY_TRADING,
            strategy_id="orb",
        ),
        producer_strategy_version="orb-v1",
        symbol="ACME",
        observed_at=observed_at,
        valid_until=(
            anchor + dt.timedelta(seconds=30)
            if valid_until is None
            else valid_until
        ),
        side=SignalSide.LONG,
        entry_type=SignalEntryType.STOP_TRIGGER,
        entry_price=entry_price,
        stop_price=Decimal(stop),
        targets=(
            TradeTarget(label="1r", price=entry_price + Decimal("0.20")),
            TradeTarget(label="2r", price=entry_price + Decimal("0.40")),
        ),
        actionability=SignalActionability.CONDITIONAL,
        invalidation_rule="진입 전 stop 이하면 무효",
        rationale="현재 세션 ORB와 거래량 확대",
        evidence_refs=(
            EvidenceRef(
                namespace="paper/recommendation",
                record_id="recommendation-1",
                observed_at=observed_at,
            ),
        ),
        opportunity_id="us-opportunity-1",
    )
    return TradeSignalPublication(
        published_at=anchor - dt.timedelta(seconds=9),
        signal=signal,
    )
