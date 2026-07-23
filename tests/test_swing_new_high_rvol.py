from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from trading_agent.data_capability_models import DataSourceId
from trading_agent.signal_contract_models import (
    SignalActionability,
    SignalEntryType,
    SignalSide,
)
from trading_agent.swing_new_high_rvol import (
    InvalidNewHighRvolProjectionError,
    NewHighRvolConfig,
    project_new_high_rvol_signals,
)
from trading_agent.swing_shadow_models import SwingDailyBar, SwingDailySource
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds

SESSION = dt.date(2026, 7, 15)
OBSERVED_AT = dt.datetime(2026, 7, 15, 16, 5, tzinfo=NEW_YORK)


def test_projects_deterministic_conditional_signal_for_new_high_and_rvol() -> None:
    source = _source()

    signals = project_new_high_rvol_signals(source)

    assert len(signals) == 1
    signal = signals[0]
    next_session_bounds = regular_session_bounds(dt.date(2026, 7, 16))
    assert next_session_bounds is not None
    assert signal.signal_id == project_new_high_rvol_signals(source)[0].signal_id
    assert signal.strategy_lane.canonical_id == ("us_equities/swing_trading/new_high_momentum")
    assert signal.producer_strategy_version == "new_high_rvol_20d_1p5_v1"
    assert signal.symbol == "ACME"
    assert signal.observed_at == OBSERVED_AT
    assert signal.valid_until == next_session_bounds[1]
    assert signal.side is SignalSide.LONG
    assert signal.entry_type is SignalEntryType.STOP_TRIGGER
    assert signal.actionability is SignalActionability.CONDITIONAL
    assert signal.quote_validation is None
    assert signal.entry_price == Decimal("15.075")
    assert signal.stop_price == Decimal("13.86900")
    assert signal.targets[0].price == Decimal("17.48700")
    assert signal.evidence_refs[0].observed_at == OBSERVED_AT
    assert signal.evidence_refs[0].record_id == source.source_key


def test_uses_one_logical_signal_id_when_source_observation_is_revised() -> None:
    source = _source()
    revised_source = source.model_copy(update={"observed_at": OBSERVED_AT + dt.timedelta(minutes=1)})

    first = project_new_high_rvol_signals(source)[0]
    revised = project_new_high_rvol_signals(revised_source)[0]

    assert first.signal_id == revised.signal_id
    assert first.evidence_refs[0].record_id != revised.evidence_refs[0].record_id


@pytest.mark.parametrize(
    ("final_close", "final_volume"),
    ((Decimal("10"), 200_000), (Decimal("15"), 149_999)),
)
def test_projects_no_signal_without_new_high_or_minimum_rvol(
    final_close: Decimal,
    final_volume: int,
) -> None:
    source = _source(final_close=final_close, final_volume=final_volume)

    assert project_new_high_rvol_signals(source) == ()


def test_rejects_source_without_complete_twenty_session_history() -> None:
    source = _source(history_count=20)

    with pytest.raises(InvalidNewHighRvolProjectionError):
        _ = project_new_high_rvol_signals(source)


def test_rejects_source_observed_before_its_regular_close() -> None:
    source = _source().model_copy(update={"observed_at": dt.datetime(2026, 7, 15, 15, 59, tzinfo=NEW_YORK)})

    with pytest.raises(InvalidNewHighRvolProjectionError):
        _ = project_new_high_rvol_signals(source)


def test_rejects_session_without_a_known_next_regular_close() -> None:
    source = _source().model_copy(
        update={
            "session_date": dt.date(2028, 12, 29),
            "observed_at": dt.datetime(2028, 12, 29, 16, 5, tzinfo=NEW_YORK),
        }
    )

    with pytest.raises(InvalidNewHighRvolProjectionError):
        _ = project_new_high_rvol_signals(source)


def test_config_is_fixed_to_the_first_registered_strategy_version() -> None:
    config = NewHighRvolConfig()

    assert config.lookback_sessions == 20
    assert config.minimum_rvol == Decimal("1.5")
    assert config.entry_buffer_bps == Decimal("50")
    assert config.stop_loss_bps == Decimal("800")
    assert config.target_r_multiple == Decimal("2")
    assert config.max_holding_sessions == 10


def _source(
    *,
    history_count: int = 21,
    final_close: Decimal = Decimal("15"),
    final_volume: int = 200_000,
) -> SwingDailySource:
    sessions = _sessions_ending(SESSION, count=history_count)
    bars = tuple(
        SwingDailyBar(
            symbol=symbol,
            session_date=session_date,
            observed_at=OBSERVED_AT,
            open=Decimal("10"),
            high=max(close, Decimal("10.2")),
            low=Decimal("9.9"),
            close=close,
            volume=volume,
        )
        for symbol in ("ACME", "BETA")
        for index, session_date in enumerate(sessions)
        for close, volume in (
            (
                final_close if symbol == "ACME" and index == len(sessions) - 1 else Decimal("10"),
                final_volume if symbol == "ACME" and index == len(sessions) - 1 else 100_000,
            ),
        )
    )
    return SwingDailySource(
        session_date=SESSION,
        observed_at=OBSERVED_AT,
        source_id=DataSourceId(provider="fixture", feed="completed_daily"),
        universe_id="fixture-universe-v1",
        symbols=("ACME", "BETA"),
        bars=bars,
    )


def _sessions_ending(end: dt.date, *, count: int) -> tuple[dt.date, ...]:
    sessions: list[dt.date] = []
    current = end
    while len(sessions) < count:
        if regular_session_bounds(current) is not None:
            sessions.append(current)
        current -= dt.timedelta(days=1)
    return tuple(reversed(sessions))
