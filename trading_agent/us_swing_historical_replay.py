from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Final, override

from trading_agent.experiment_ledger_models import TrialEventKind
from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.swing_new_high_rvol import project_new_high_rvol_signals
from trading_agent.swing_research_contract import SWING_RESEARCH_CONTRACT
from trading_agent.swing_shadow_delivery import project_swing_shadow_cycle_delivery
from trading_agent.swing_shadow_engine import advance_swing_shadow_session
from trading_agent.swing_shadow_models import SwingDailySource
from trading_agent.swing_shadow_source import load_swing_daily_source
from trading_agent.swing_shadow_store import ShadowEventKind, SwingShadowStore
from trading_agent.swing_shadow_trial import swing_shadow_trial_id
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds
from trading_agent.us_swing_operating_coordinator import run_us_swing_operating_tick
from trading_agent.us_swing_operating_models import (
    SwingOperatingConfig,
    SwingOperatingRequest,
    SwingScanCompleted,
    SwingScanOutcome,
)

_MAX_REPLAY_SESSIONS: Final = 32
_TERMINAL_KINDS: Final = frozenset(
    {
        ShadowEventKind.EXPIRED,
        ShadowEventKind.STOPPED,
        ShadowEventKind.TARGETED,
        ShadowEventKind.TIME_EXIT,
    }
)


class InvalidSwingHistoricalReplayError(ValueError):
    @override
    def __str__(self) -> str:
        return "US swing historical replay의 현재시점 인과성을 확인하지 못했습니다"


@dataclass(frozen=True, slots=True)
class SwingHistoricalReplayFixture:
    session_date: dt.date
    fixture_root: Path


@dataclass(frozen=True, slots=True)
class SwingHistoricalReplayRequest:
    fixtures: tuple[SwingHistoricalReplayFixture, ...]
    runtime_code_version: str


@dataclass(frozen=True, slots=True)
class SwingHistoricalReplayResult:
    sessions_replayed: int
    causal_snapshots: int
    recommendation_cards: int
    no_recommendation_cards: int
    shadow_entries: int
    shadow_terminals: int
    reviewer_evidence: int
    external_broker_mutations: int = 0


@dataclass(frozen=True, slots=True)
class HistoricalSwingFixtureScanner:
    fixtures: tuple[SwingHistoricalReplayFixture, ...]
    shadow_store: SwingShadowStore
    delivery_store: HermesDeliveryStore

    def run(self, session_date: dt.date) -> SwingScanOutcome:
        matching = tuple(
            fixture for fixture in self.fixtures if fixture.session_date == session_date
        )
        if len(matching) != 1:
            raise InvalidSwingHistoricalReplayError
        source = load_swing_daily_source(
            matching[0].fixture_root,
            session_date=session_date,
        )
        _require_causal_snapshot(source)
        signals = project_new_high_rvol_signals(source)
        with self.shadow_store.writer() as writer:
            _ = advance_swing_shadow_session(writer, source=source, signals=signals)
        with self.delivery_store.writer() as writer:
            _ = project_swing_shadow_cycle_delivery(source, signals, writer)
        return SwingScanCompleted(source.observed_at)


def run_swing_historical_replay(
    request: SwingHistoricalReplayRequest,
    config: SwingOperatingConfig,
) -> SwingHistoricalReplayResult:
    _require_request(request)
    if not _replay_is_complete(request, config):
        persisted_signals = config.shadow_ledger.signals()
        persisted_deliveries = config.delivery_store.events()
        replay_floor = min(
            (signal.observed_at for signal in persisted_signals),
            default=None,
        )
        for fixture in request.fixtures:
            bounds = regular_session_bounds(fixture.session_date)
            if bounds is None:
                raise InvalidSwingHistoricalReplayError
            persisted_times = tuple(
                signal.observed_at
                for signal in persisted_signals
                if signal.observed_at.astimezone(NEW_YORK).date() == fixture.session_date
            ) + tuple(
                event.occurred_at
                for event in persisted_deliveries
                if event.occurred_at.astimezone(NEW_YORK).date() == fixture.session_date
            )
            persisted_post_close = max(persisted_times, default=bounds[1])
            moments = (
                bounds[0] - dt.timedelta(minutes=5),
                bounds[0] + dt.timedelta(minutes=1),
                persisted_post_close,
            )
            for now in moments:
                if replay_floor is not None and now < replay_floor:
                    continue
                outcome = run_us_swing_operating_tick(
                    SwingOperatingRequest(now, request.runtime_code_version),
                    config,
                )
                if outcome.incidents:
                    raise InvalidSwingHistoricalReplayError
    if not _replay_is_complete(request, config):
        raise InvalidSwingHistoricalReplayError
    deliveries = config.delivery_store.events()
    signals = config.shadow_ledger.signals()
    events = tuple(
        event
        for signal in signals
        for event in config.shadow_ledger.events(signal.signal_id)
    )
    root_cards = tuple(
        event for event in deliveries if event.root_delivery_id == event.delivery_id
    )
    return SwingHistoricalReplayResult(
        sessions_replayed=len(request.fixtures),
        causal_snapshots=len(request.fixtures),
        recommendation_cards=sum(
            event.kind is HermesDeliveryKind.WATCH for event in root_cards
        ),
        no_recommendation_cards=sum(
            event.kind is HermesDeliveryKind.NO_RECOMMENDATION for event in root_cards
        ),
        shadow_entries=sum(event.kind is ShadowEventKind.ENTRY_FILLED for event in events),
        shadow_terminals=sum(event.kind in _TERMINAL_KINDS for event in events),
        reviewer_evidence=len(config.review_store.events()),
    )


def _replay_is_complete(
    request: SwingHistoricalReplayRequest,
    config: SwingOperatingConfig,
) -> bool:
    requested_dates = frozenset(fixture.session_date for fixture in request.fixtures)
    deliveries = config.delivery_store.events()
    stored_source_dates = frozenset(
        event.occurred_at.astimezone(NEW_YORK).date()
        for event in deliveries
        if event.root_delivery_id == event.delivery_id
        and event.kind in {HermesDeliveryKind.WATCH, HermesDeliveryKind.NO_RECOMMENDATION}
        and event.strategy_version == SWING_RESEARCH_CONTRACT.strategy_version
    )
    if not requested_dates.issubset(stored_source_dates):
        return False
    reviews = {stored.event.signal_id for stored in config.review_store.events()}
    for signal in config.shadow_ledger.signals():
        if signal.observed_at.astimezone(NEW_YORK).date() not in requested_dates:
            continue
        shadow_events = config.shadow_ledger.events(signal.signal_id)
        trial_events = config.experiment_ledger.trial_events(swing_shadow_trial_id(signal))
        watch = tuple(event for event in deliveries if event.source_event_id == signal.signal_id)
        if (
            not shadow_events
            or shadow_events[-1].kind not in _TERMINAL_KINDS
            or len(trial_events) != 2
            or trial_events[-1].event.event_kind is not TrialEventKind.COMPLETED
            or signal.signal_id not in reviews
            or len(watch) != 1
            or not any(
                event.root_delivery_id == watch[0].delivery_id
                and event.delivery_id != event.root_delivery_id
                for event in deliveries
            )
        ):
            return False
    return True


def _require_request(request: SwingHistoricalReplayRequest) -> None:
    dates = tuple(fixture.session_date for fixture in request.fixtures)
    roots = tuple(fixture.fixture_root.resolve(strict=False) for fixture in request.fixtures)
    if (
        not request.runtime_code_version
        or request.runtime_code_version != request.runtime_code_version.strip()
        or not 2 <= len(dates) <= _MAX_REPLAY_SESSIONS
        or dates != tuple(sorted(set(dates)))
        or len(roots) != len(set(roots))
        or dates != _regular_sessions_between(dates[0], dates[-1])
    ):
        raise InvalidSwingHistoricalReplayError


def _require_causal_snapshot(source: SwingDailySource) -> None:
    bounds = regular_session_bounds(source.session_date)
    if (
        bounds is None
        or source.observed_at.astimezone(NEW_YORK) < bounds[1]
        or any(bar.observed_at != source.observed_at for bar in source.bars)
    ):
        raise InvalidSwingHistoricalReplayError


def _regular_sessions_between(start: dt.date, end: dt.date) -> tuple[dt.date, ...]:
    sessions: list[dt.date] = []
    current = start
    while current <= end:
        if regular_session_bounds(current) is not None:
            sessions.append(current)
        current += dt.timedelta(days=1)
    return tuple(sessions)


__all__ = (
    "HistoricalSwingFixtureScanner",
    "InvalidSwingHistoricalReplayError",
    "SwingHistoricalReplayFixture",
    "SwingHistoricalReplayRequest",
    "SwingHistoricalReplayResult",
    "run_swing_historical_replay",
)
