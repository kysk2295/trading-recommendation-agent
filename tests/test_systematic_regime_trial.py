from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from tests.test_systematic_regime_engine import _source
from trading_agent.experiment_ledger_models import (
    StrategyLifecycleState,
    TrialEventKind,
)
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.research_identity_models import AgentFamily, AgentOperatingMode
from trading_agent.swing_shadow_models import SwingDailyBar, SwingDailySource
from trading_agent.systematic_regime_engine import build_systematic_card, replay_systematic_regime
from trading_agent.systematic_regime_research import systematic_regime_strategy_version
from trading_agent.systematic_regime_store import SystematicRegimeStore
from trading_agent.systematic_regime_trial import (
    InvalidSystematicRegimeTrialError,
    finalize_systematic_regime_trial,
    register_systematic_regime_trial,
    start_systematic_regime_trial,
)
from trading_agent.us_equity_calendar import regular_session_bounds

CODE_VERSION = "a" * 40


def test_recommendation_card_completes_a_shadow_only_global_trial(tmp_path: Path) -> None:
    # Given: a persisted post-close card for the next regular session.
    source = _source("risk_on")
    version = systematic_regime_strategy_version(CODE_VERSION)
    card = build_systematic_card(source, replay_systematic_regime(source), version)
    experiment = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    cards = SystematicRegimeStore(tmp_path / "systematic.sqlite3")
    with cards.writer() as writer:
        _ = writer.append_card(card)

    # When: the trial is registered, started during its session, and finalized after close.
    registered = register_systematic_regime_trial(experiment, card, CODE_VERSION)
    bounds = regular_session_bounds(card.target_session)
    assert bounds is not None
    started = start_systematic_regime_trial(experiment, card, bounds[0] + dt.timedelta(hours=1))
    target_source = _extend_source(source, card.target_session)
    finalized = finalize_systematic_regime_trial(experiment, cards, card, target_source)

    # Then: the canonical lane stays shadow-only and has a complete immutable trial chain.
    version_row = experiment.multi_market_strategy_versions()[0].registration
    lifecycle = experiment.multi_market_lifecycle_events(version)[0].event
    events = experiment.multi_market_trial_events(registered.registration.trial_id)
    assert version_row.operating_mode is AgentOperatingMode.SHADOW
    assert version_row.strategy_lane.agent_family is AgentFamily.SYSTEMATIC_QUANT
    assert lifecycle.to_state is StrategyLifecycleState.EXPERIMENTAL_SHADOW
    assert registered.created is True
    assert started.created is True
    assert finalized.created is True
    assert tuple(item.event.event_kind for item in events) == (
        TrialEventKind.STARTED,
        TrialEventKind.COMPLETED,
    )
    assert finalized.outcome.net_return_bps is not None


def test_mixed_regime_completes_as_an_explicit_no_position_observation(tmp_path: Path) -> None:
    # Given: an immutable no-recommendation card.
    source = _source("mixed")
    version = systematic_regime_strategy_version(CODE_VERSION)
    card = build_systematic_card(source, replay_systematic_regime(source), version)
    experiment = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    cards = SystematicRegimeStore(tmp_path / "systematic.sqlite3")
    with cards.writer() as writer:
        _ = writer.append_card(card)
    registration = register_systematic_regime_trial(experiment, card, CODE_VERSION)
    bounds = regular_session_bounds(card.target_session)
    assert bounds is not None
    _ = start_systematic_regime_trial(experiment, card, bounds[0] + dt.timedelta(minutes=1))

    # When: the target session closes.
    finalized = finalize_systematic_regime_trial(
        experiment,
        cards,
        card,
        _extend_source(source, card.target_session),
    )

    # Then: no synthetic return or position is created, while the trial completes.
    assert registration.registration.trial_id == finalized.event.trial_id
    assert finalized.outcome.no_position is True
    assert finalized.outcome.net_return_bps is None


def test_existing_strategy_lifecycle_accepts_the_next_daily_trial(tmp_path: Path) -> None:
    # Given: one completed daily trial for an unchanged strategy version.
    first_source = _source("risk_on")
    version = systematic_regime_strategy_version(CODE_VERSION)
    first_card = build_systematic_card(
        first_source,
        replay_systematic_regime(first_source),
        version,
    )
    experiment = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    cards = SystematicRegimeStore(tmp_path / "systematic.sqlite3")
    with cards.writer() as writer:
        _ = writer.append_card(first_card)
    _ = register_systematic_regime_trial(experiment, first_card, CODE_VERSION)
    bounds = regular_session_bounds(first_card.target_session)
    assert bounds is not None
    _ = start_systematic_regime_trial(experiment, first_card, bounds[0] + dt.timedelta(minutes=1))
    second_source = _extend_source(first_source, first_card.target_session)
    _ = finalize_systematic_regime_trial(experiment, cards, first_card, second_source)
    second_card = build_systematic_card(
        second_source,
        replay_systematic_regime(second_source),
        version,
    )
    with cards.writer() as writer:
        _ = writer.append_card(second_card)

    # When: the next session trial is registered under the same version.
    second = register_systematic_regime_trial(experiment, second_card, CODE_VERSION)

    # Then: a new trial is appended without rewriting the one strategy lifecycle registration.
    assert second.created is True
    assert len(experiment.multi_market_lifecycle_events(version)) == 1
    assert len(experiment.multi_market_trials()) == 2


def test_invalid_card_version_leaves_research_ledger_unchanged(tmp_path: Path) -> None:
    # Given: a card whose strategy version does not match the requested code version.
    source = _source("risk_on")
    card = build_systematic_card(source, replay_systematic_regime(source), "wrong-version")
    ledger = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")

    # When: registration rejects the mismatched card.
    with pytest.raises(InvalidSystematicRegimeTrialError):
        _ = register_systematic_regime_trial(ledger, card, CODE_VERSION)

    # Then: immutable research state was not partially appended first.
    assert ledger.multi_market_hypotheses() == ()
    assert ledger.multi_market_strategy_versions() == ()


def _extend_source(source: SwingDailySource, target_session: dt.date) -> SwingDailySource:
    bounds = regular_session_bounds(target_session)
    assert bounds is not None
    observed_at = bounds[1] + dt.timedelta(minutes=5)
    bars: list[SwingDailyBar] = []
    for symbol in source.symbols:
        bars.extend(source.bars_for(symbol))
        prior = source.bars_for(symbol)[-1].close
        bars.append(
            SwingDailyBar(
                symbol=symbol,
                session_date=target_session,
                observed_at=observed_at,
                open=prior,
                high=prior * Decimal("1.02"),
                low=prior * Decimal("0.99"),
                close=prior * Decimal("1.01"),
                volume=1_100_000,
            )
        )
    return SwingDailySource(
        session_date=target_session,
        observed_at=observed_at,
        universe_id=source.universe_id,
        symbols=source.symbols,
        bars=tuple(bars),
    )
