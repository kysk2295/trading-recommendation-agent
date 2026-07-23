from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from trading_agent.data_capability_models import DataSourceId
from trading_agent.signal_contract_models import SignalActionability
from trading_agent.swing_shadow_models import SwingDailyBar, SwingDailySource
from trading_agent.systematic_regime_engine import (
    InvalidSystematicRegimeSourceError,
    build_systematic_card,
    replay_systematic_regime,
)
from trading_agent.systematic_regime_models import (
    RegimeLabel,
    SystematicDecisionKind,
)
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds

SYMBOLS = ("GLD", "IEF", "IWM", "QQQ", "SHY", "SPY")
STRATEGY_VERSION = "us-regime-rotation-v1-code-0123456789abcdef"


def test_card_recommends_strongest_risk_on_etfs_when_breadth_confirms() -> None:
    # Given: an aligned completed-day ETF source with a strong equity regime.
    source = _source("risk_on")

    # When: the causal replay and latest next-session card are projected.
    replay = replay_systematic_regime(source)
    card = build_systematic_card(source, replay, STRATEGY_VERSION)

    # Then: the two strongest risk-on ETFs are conditional shadow candidates.
    assert card.decision_kind is SystematicDecisionKind.RECOMMENDATION
    assert card.context.regime is RegimeLabel.RISK_ON
    assert card.candidate_symbols == ("QQQ", "SPY")
    assert card.order_authority is False
    assert tuple(signal.symbol for signal in card.signals) == card.candidate_symbols
    assert all(signal.actionability is SignalActionability.CONDITIONAL for signal in card.signals)


def test_card_recommends_defensive_etfs_when_breadth_confirms_risk_off() -> None:
    # Given: an aligned source with falling equities and rising defensive ETFs.
    source = _source("risk_off")

    # When: the current card is built.
    card = build_systematic_card(source, replay_systematic_regime(source), STRATEGY_VERSION)

    # Then: the two strongest defensive ETFs are selected without order authority.
    assert card.context.regime is RegimeLabel.RISK_OFF
    assert card.candidate_symbols == ("GLD", "IEF")
    assert card.order_authority is False


def test_card_preserves_explicit_no_recommendation_when_regime_is_mixed() -> None:
    # Given: SPY trend and equity breadth disagree.
    source = _source("mixed")

    # When: the current card is built.
    card = build_systematic_card(source, replay_systematic_regime(source), STRATEGY_VERSION)

    # Then: no candidate or signal is invented.
    assert card.decision_kind is SystematicDecisionKind.NO_RECOMMENDATION
    assert card.context.regime is RegimeLabel.MIXED
    assert card.candidate_symbols == ()
    assert card.signals == ()


def test_replay_decision_does_not_use_the_next_session_close() -> None:
    # Given: two sources that differ only in the final target-session close.
    original = _source("risk_on")
    changed = original.model_copy(
        update={
            "bars": tuple(
                bar.model_copy(update={"close": bar.close * Decimal("0.5"), "low": bar.low * Decimal("0.5")})
                if bar.session_date == original.session_date and bar.symbol == "SPY"
                else bar
                for bar in original.bars
            )
        }
    )

    # When: both histories are replayed.
    first = replay_systematic_regime(original).observations[-1]
    second = replay_systematic_regime(changed).observations[-1]

    # Then: the prior-close decision is identical while the later outcome may differ.
    assert first.decision_session == second.decision_session
    assert first.regime is second.regime
    assert first.candidate_symbols == second.candidate_symbols
    assert first.net_return_bps != second.net_return_bps


def test_replay_rejects_an_aligned_history_with_a_missing_market_session() -> None:
    # Given: every symbol omits the same completed NYSE session.
    source = _source("risk_on")
    missing = source.bars_for("SPY")[-5].session_date
    gapped = source.model_copy(
        update={"bars": tuple(bar for bar in source.bars if bar.session_date != missing)},
    )

    # When/Then: replay refuses to relabel the later bar as the next-session outcome.
    with pytest.raises(InvalidSystematicRegimeSourceError):
        _ = replay_systematic_regime(gapped)


def _source(regime: str) -> SwingDailySource:
    sessions = _sessions(211)
    bounds = regular_session_bounds(sessions[-1])
    assert bounds is not None
    observed_at = bounds[1] + dt.timedelta(minutes=5)
    bars = tuple(
        _bar(symbol, session, index, observed_at, regime)
        for symbol in SYMBOLS
        for index, session in enumerate(sessions)
    )
    return SwingDailySource(
        session_date=sessions[-1],
        observed_at=observed_at,
        source_id=DataSourceId(provider="fixture", feed="completed_daily"),
        universe_id="us_systematic_regime_etf_v1",
        symbols=SYMBOLS,
        bars=bars,
    )


def _bar(
    symbol: str,
    session: dt.date,
    index: int,
    observed_at: dt.datetime,
    regime: str,
) -> SwingDailyBar:
    close = _close(symbol, index, regime)
    return SwingDailyBar(
        symbol=symbol,
        session_date=session,
        observed_at=observed_at,
        open=close * Decimal("0.999"),
        high=close * Decimal("1.01"),
        low=close * Decimal("0.99"),
        close=close,
        volume=1_000_000 + index,
    )


def _close(symbol: str, index: int, regime: str) -> Decimal:
    step = Decimal(index)
    if regime == "risk_on":
        slope = {"GLD": "0.10", "IEF": "0.05", "IWM": "0.30", "QQQ": "0.80", "SHY": "0.01", "SPY": "0.50"}[symbol]
        base = {"GLD": "100", "IEF": "100", "IWM": "90", "QQQ": "80", "SHY": "100", "SPY": "100"}[symbol]
        return Decimal(base) + step * Decimal(slope)
    if regime == "risk_off":
        slope = {"GLD": "0.40", "IEF": "0.30", "IWM": "-0.30", "QQQ": "-0.60", "SHY": "0.05", "SPY": "-0.50"}[symbol]
        base = {"GLD": "100", "IEF": "100", "IWM": "200", "QQQ": "260", "SHY": "100", "SPY": "260"}[symbol]
        return Decimal(base) + step * Decimal(slope)
    slope = {"GLD": "0.10", "IEF": "0.05", "IWM": "-0.30", "QQQ": "-0.50", "SHY": "0.01", "SPY": "0.50"}[symbol]
    base = {"GLD": "100", "IEF": "100", "IWM": "200", "QQQ": "240", "SHY": "100", "SPY": "100"}[symbol]
    return Decimal(base) + step * Decimal(slope)


def _sessions(count: int) -> tuple[dt.date, ...]:
    sessions: list[dt.date] = []
    current = dt.date(2025, 9, 2)
    while len(sessions) < count:
        if regular_session_bounds(current) is not None:
            sessions.append(current)
        current += dt.timedelta(days=1)
    assert sessions[-1] < dt.datetime.now(NEW_YORK).date()
    return tuple(sessions)
