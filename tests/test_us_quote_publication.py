from __future__ import annotations

import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from trading_agent.kis_us_quote import (
    KisUsLevelOneQuote,
    KisUsQuoteUnavailableError,
)
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
from trading_agent.us_quote_actionability import QuoteAssessmentStatus
from trading_agent.us_quote_publication import evaluate_quote_publications

NEW_YORK = ZoneInfo("America/New_York")
AT = dt.datetime(2026, 7, 15, 13, 20, tzinfo=NEW_YORK)
STARTED_AT = AT - dt.timedelta(seconds=20)


def test_quote_batch_fetches_each_signal_symbol_once() -> None:
    calls: list[tuple[str, str]] = []

    batch = evaluate_quote_publications(
        (
            _publication("ACME", "signal-2"),
            _publication("ACME", "signal-1"),
            _publication("BETA", "signal-3"),
        ),
        exchange_by_symbol={"ACME": "NAS", "BETA": "NYS"},
        fetch_quote=lambda exchange, symbol: calls.append((exchange, symbol))
        or _quote(symbol),
        scan_started_at=STARTED_AT,
        clock=lambda: AT,
    )

    assert calls == [("NAS", "ACME"), ("NYS", "BETA")]
    assert len(batch.snapshots) == 2
    assert len(batch.assessments) == 3
    assert len(batch.derived_publications) == 3
    assert tuple(item.base_signal_id for item in batch.assessments) == (
        "signal-1",
        "signal-2",
        "signal-3",
    )


def test_closed_market_makes_zero_provider_calls() -> None:
    weekend = dt.datetime(2026, 7, 18, 13, 20, tzinfo=NEW_YORK)

    batch = evaluate_quote_publications(
        (_publication("ACME", "signal-1", anchor=weekend),),
        exchange_by_symbol={"ACME": "NAS"},
        fetch_quote=lambda *_: pytest.fail("provider called"),
        scan_started_at=weekend - dt.timedelta(seconds=20),
        clock=lambda: weekend,
    )

    assert batch.snapshots == ()
    assert batch.derived_publications == ()
    assert batch.assessments[0].status is QuoteAssessmentStatus.MARKET_CLOSED


def test_expired_base_signal_makes_zero_provider_calls() -> None:
    batch = evaluate_quote_publications(
        (
            _publication(
                "ACME",
                "signal-1",
                valid_until=AT,
            ),
        ),
        exchange_by_symbol={"ACME": "NAS"},
        fetch_quote=lambda *_: pytest.fail("provider called"),
        scan_started_at=STARTED_AT,
        clock=lambda: AT,
    )

    assert batch.snapshots == ()
    assert batch.derived_publications == ()
    assert batch.assessments[0].status is QuoteAssessmentStatus.SETUP_INVALIDATED


def test_missing_exchange_fails_closed_without_provider_call() -> None:
    batch = evaluate_quote_publications(
        (_publication("ACME", "signal-1"),),
        exchange_by_symbol={},
        fetch_quote=lambda *_: pytest.fail("provider called"),
        scan_started_at=STARTED_AT,
        clock=lambda: AT,
    )

    assert batch.snapshots == ()
    assert batch.derived_publications == ()
    assert batch.assessments[0].status is QuoteAssessmentStatus.PROVIDER_FAILED


def test_provider_failure_is_isolated_per_symbol() -> None:
    calls: list[str] = []

    def fetch(_: str, symbol: str) -> KisUsLevelOneQuote:
        calls.append(symbol)
        if symbol == "ACME":
            raise KisUsQuoteUnavailableError("http_error")
        return _quote(symbol)

    batch = evaluate_quote_publications(
        (
            _publication("BETA", "signal-2"),
            _publication("ACME", "signal-1"),
        ),
        exchange_by_symbol={"ACME": "NAS", "BETA": "NYS"},
        fetch_quote=fetch,
        scan_started_at=STARTED_AT,
        clock=lambda: AT,
    )

    assert calls == ["ACME", "BETA"]
    assert tuple(item.status for item in batch.assessments) == (
        QuoteAssessmentStatus.PROVIDER_FAILED,
        QuoteAssessmentStatus.VALIDATED_WAITING,
    )
    assert tuple(item.signal.symbol for item in batch.derived_publications) == (
        "BETA",
    )
    assert tuple(item.symbol for item in batch.snapshots) == ("BETA",)


def test_unexpected_provider_programming_error_propagates() -> None:
    def fail(_: str, __: str) -> KisUsLevelOneQuote:
        raise RuntimeError("programming error")

    with pytest.raises(RuntimeError, match="programming error"):
        _ = evaluate_quote_publications(
            (_publication("ACME", "signal-1"),),
            exchange_by_symbol={"ACME": "NAS"},
            fetch_quote=fail,
            scan_started_at=STARTED_AT,
            clock=lambda: AT,
        )


def test_empty_publication_batch_calls_neither_clock_nor_provider() -> None:
    batch = evaluate_quote_publications(
        (),
        exchange_by_symbol={},
        fetch_quote=lambda *_: pytest.fail("provider called"),
        scan_started_at=STARTED_AT,
        clock=lambda: pytest.fail("clock called"),
    )

    assert batch.snapshots == ()
    assert batch.assessments == ()
    assert batch.derived_publications == ()


def _publication(
    symbol: str,
    signal_id: str,
    *,
    anchor: dt.datetime = AT,
    valid_until: dt.datetime | None = None,
) -> TradeSignalPublication:
    observed_at = anchor - dt.timedelta(seconds=10)
    return TradeSignalPublication(
        published_at=anchor - dt.timedelta(seconds=9),
        signal=TradeSignalEnvelope(
            signal_id=signal_id,
            strategy_lane=StrategyLaneRef(
                market_id=MarketId.US_EQUITIES,
                agent_family=AgentFamily.DAY_TRADING,
                strategy_id="orb",
            ),
            producer_strategy_version="orb-v1",
            symbol=symbol,
            observed_at=observed_at,
            valid_until=(
                anchor + dt.timedelta(seconds=30)
                if valid_until is None
                else valid_until
            ),
            side=SignalSide.LONG,
            entry_type=SignalEntryType.STOP_TRIGGER,
            entry_price=Decimal("10.10"),
            stop_price=Decimal("9.90"),
            targets=(
                TradeTarget(label="1r", price=Decimal("10.30")),
                TradeTarget(label="2r", price=Decimal("10.50")),
            ),
            actionability=SignalActionability.CONDITIONAL,
            invalidation_rule="진입 전 stop 이하면 무효",
            rationale="현재 세션 ORB와 거래량 확대",
            evidence_refs=(
                EvidenceRef(
                    namespace="paper/recommendation",
                    record_id=f"recommendation-{signal_id}",
                    observed_at=observed_at,
                ),
            ),
            opportunity_id="us-opportunity-1",
        ),
    )


def _quote(symbol: str) -> KisUsLevelOneQuote:
    return KisUsLevelOneQuote(
        exchange="NAS" if symbol == "ACME" else "NYS",
        symbol=symbol,
        provider_observed_at=AT - dt.timedelta(seconds=1),
        received_at=AT - dt.timedelta(milliseconds=100),
        bid=Decimal("10.08"),
        ask=Decimal("10.09"),
        bid_size=1_000,
        ask_size=900,
    )
