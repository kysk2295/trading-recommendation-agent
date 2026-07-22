from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from tests.test_swing_shadow_reviewer import (
    _completed_trial,
    _registered_trial,
    _session_source,
    _signal_source,
)
from tests.test_swing_shadow_trial import _bounds
from trading_agent.experiment_ledger_store import ExperimentLedgerReader
from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.swing_new_high_rvol import project_new_high_rvol_signals
from trading_agent.swing_shadow_delivery import (
    InvalidSwingShadowDeliveryError,
    project_swing_shadow_cycle_delivery,
    project_swing_shadow_terminal_delivery,
)
from trading_agent.swing_shadow_engine import advance_swing_shadow_session
from trading_agent.swing_shadow_store import SwingShadowReader
from trading_agent.swing_shadow_trial import (
    finalize_swing_shadow_trial,
    start_swing_shadow_trial,
)


def test_signal_cycle_projects_watch_and_exact_replay(tmp_path: Path) -> None:
    source = _signal_source()
    signals = project_new_high_rvol_signals(source)
    store = HermesDeliveryStore(tmp_path / "delivery.sqlite3")

    with store.writer() as writer:
        first = project_swing_shadow_cycle_delivery(source, signals, writer)
        replay = project_swing_shadow_cycle_delivery(source, signals, writer)

    assert (first.inserted, first.replayed) == (1, 0)
    assert (replay.inserted, replay.replayed) == (0, 1)
    event = store.events()[0]
    assert event.kind is HermesDeliveryKind.WATCH
    assert event.source_event_id == signals[0].signal_id
    assert event.root_delivery_id == event.delivery_id
    assert event.status == "conditional"


def test_empty_signal_cycle_projects_daily_no_recommendation(tmp_path: Path) -> None:
    source = _signal_source()
    current = source.bars[-1].model_copy(
        update={"close": Decimal("10"), "high": Decimal("10.2"), "volume": 100_000}
    )
    source = source.model_copy(update={"bars": (*source.bars[:-1], current)})
    assert project_new_high_rvol_signals(source) == ()
    store = HermesDeliveryStore(tmp_path / "delivery.sqlite3")

    with store.writer() as writer:
        result = project_swing_shadow_cycle_delivery(source, (), writer)

    assert result.inserted == 1
    event = store.events()[0]
    assert event.kind is HermesDeliveryKind.NO_RECOMMENDATION
    assert event.root_delivery_id == event.delivery_id
    assert event.status == "no_setup"
    assert "추천 없음" in event.rendered_text


def test_expired_terminal_replies_to_watch_and_exactly_replays(tmp_path: Path) -> None:
    experiments, shadow, signal, terminal = _completed_trial(tmp_path)
    delivery = HermesDeliveryStore(tmp_path / "delivery.sqlite3")
    with delivery.writer() as writer:
        _ = project_swing_shadow_cycle_delivery(_signal_source(), (signal,), writer)

    first = project_swing_shadow_terminal_delivery(
        ExperimentLedgerReader(experiments.path),
        SwingShadowReader(shadow.path),
        delivery,
        signal.signal_id,
    )
    replay = project_swing_shadow_terminal_delivery(
        ExperimentLedgerReader(experiments.path),
        SwingShadowReader(shadow.path),
        delivery,
        signal.signal_id,
    )

    assert (first.inserted, first.replayed) == (1, 0)
    assert (replay.inserted, replay.replayed) == (0, 1)
    watch, outcome = delivery.events()
    assert outcome.kind is HermesDeliveryKind.NO_RECOMMENDATION
    assert outcome.root_delivery_id == watch.delivery_id
    assert outcome.occurred_at == terminal.observed_at
    assert outcome.status == "expired"
    assert "미체결 만료" in outcome.rendered_text


def test_filled_terminal_projects_shadow_exit_reply(tmp_path: Path) -> None:
    experiments, shadow, signal = _registered_trial(tmp_path)
    planned_start = signal.valid_until.astimezone(dt.UTC).date()
    open_at, _ = _bounds(planned_start)
    _ = start_swing_shadow_trial(
        experiment_ledger=experiments,
        shadow_ledger=SwingShadowReader(shadow.path),
        signal_id=signal.signal_id,
        started_at=open_at + dt.timedelta(minutes=1),
    )
    with shadow.writer() as writer:
        _ = advance_swing_shadow_session(
            writer,
            source=_session_source(
                planned_start,
                open_price=Decimal("15"),
                high=Decimal("18"),
                low=Decimal("14"),
                close=Decimal("17.5"),
            ),
        )
    terminal = shadow.events(signal.signal_id)[-1]
    _ = finalize_swing_shadow_trial(
        experiment_ledger=experiments,
        shadow_ledger=SwingShadowReader(shadow.path),
        signal_id=signal.signal_id,
        finalized_at=terminal.observed_at + dt.timedelta(minutes=1),
    )
    delivery = HermesDeliveryStore(tmp_path / "delivery.sqlite3")
    with delivery.writer() as writer:
        _ = project_swing_shadow_cycle_delivery(_signal_source(), (signal,), writer)

    result = project_swing_shadow_terminal_delivery(
        ExperimentLedgerReader(experiments.path),
        SwingShadowReader(shadow.path),
        delivery,
        signal.signal_id,
    )

    assert result.inserted == 1
    watch, outcome = delivery.events()
    assert outcome.kind is HermesDeliveryKind.EXIT
    assert outcome.root_delivery_id == watch.delivery_id
    assert outcome.status == "targeted"
    assert "shadow 종료" in outcome.rendered_text
    assert "주문 없음" in outcome.rendered_text


def test_terminal_without_original_watch_fails_closed(tmp_path: Path) -> None:
    experiments, shadow, signal, _ = _completed_trial(tmp_path)
    delivery = HermesDeliveryStore(tmp_path / "delivery.sqlite3")

    with pytest.raises(InvalidSwingShadowDeliveryError):
        _ = project_swing_shadow_terminal_delivery(
            ExperimentLedgerReader(experiments.path),
            SwingShadowReader(shadow.path),
            delivery,
            signal.signal_id,
        )

    assert delivery.events() == ()
