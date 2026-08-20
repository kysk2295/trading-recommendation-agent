from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import assert_never

from trading_agent.day_forward_trial_chain import (
    DayForwardTrialLedgerConflictError,
    InvalidDayForwardTrialLedgerSourceError,
    event_replay,
    gap_censor,
    require_event_chain,
    require_event_parent,
    validated_event,
)
from trading_agent.day_forward_trial_models import (
    DayForwardOutcomeRef,
    DayForwardTrial,
    DayForwardTrialEvent,
    DayForwardTrialEventKind,
)
from trading_agent.day_forward_trial_store_support import (
    events,
    require_trial_parent,
    trial_by_id,
    validated_trial,
)
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.research_identity_models import MarketId


@dataclass(frozen=True, slots=True)
class DayForwardTrialState:
    trial: DayForwardTrial
    events: tuple[DayForwardTrialEvent, ...]

    @property
    def terminal(self) -> bool:
        if not self.events:
            return False
        match self.events[-1].event_kind:
            case DayForwardTrialEventKind.EXIT | DayForwardTrialEventKind.FAILED | DayForwardTrialEventKind.CENSORED:
                return True
            case (
                DayForwardTrialEventKind.SIGNAL
                | DayForwardTrialEventKind.ENTRY
                | DayForwardTrialEventKind.NO_SIGNAL
                | DayForwardTrialEventKind.BLOCKED
            ):
                return False
            case unreachable:
                assert_never(unreachable)

    @property
    def outcome_refs(self) -> tuple[DayForwardOutcomeRef, ...]:
        return tuple(event.outcome_ref for event in self.events if event.outcome_ref is not None)


def register_day_forward_trial(
    connection: sqlite3.Connection,
    trial: DayForwardTrial,
) -> bool:
    checked = validated_trial(trial)
    existing = trial_by_id(connection, checked.trial_id)
    if existing is not None:
        if existing == checked:
            return False
        raise DayForwardTrialLedgerConflictError("forward_trial_identity_conflict")
    sibling: tuple[str] | None = connection.execute(
        "SELECT trial_id FROM day_forward_trials WHERE capsule_id=? AND "
        "hypothesis_version_id=? AND market_id=? AND session_date=?",
        (
            checked.capsule_id,
            checked.hypothesis_version_id,
            checked.market_id.value,
            checked.session_date.isoformat(),
        ),
    ).fetchone()
    if sibling is not None:
        raise DayForwardTrialLedgerConflictError("forward_trial_session_conflict")
    require_trial_parent(connection, checked)
    try:
        connection.execute(
            "INSERT INTO day_forward_trials VALUES (?,?,?,?,?,?,?)",
            (
                checked.trial_id,
                checked.capsule_id,
                checked.hypothesis_version_id,
                checked.market_id.value,
                checked.session_date.isoformat(),
                checked.preregistered_at.isoformat(),
                canonical_experiment_ledger_json(checked),
            ),
        )
    except sqlite3.IntegrityError as error:
        raise DayForwardTrialLedgerConflictError("forward_trial_identity_conflict") from error
    return True


def append_day_forward_trial_event(
    connection: sqlite3.Connection,
    event: DayForwardTrialEvent,
) -> DayForwardTrialEvent:
    checked = validated_event(event)
    state = read_day_forward_trial_state(connection, checked.trial_id)
    require_event_parent(state.trial, checked)
    replayed = event_replay(state.events, checked)
    if replayed is not None:
        return replayed
    if state.terminal:
        raise DayForwardTrialLedgerConflictError("forward_trial_already_terminal")
    previous = state.events[-1] if state.events else None
    if previous is not None and checked.completed_bar_sequence > previous.completed_bar_sequence + 1:
        checked = gap_censor(checked)
    require_event_chain(state.trial, (*state.events, checked))
    try:
        connection.execute(
            "INSERT INTO day_forward_trial_events VALUES (?,?,?,?,?,?,?,?)",
            (
                checked.event_id,
                checked.trial_id,
                checked.market_id.value,
                checked.session_date.isoformat(),
                checked.sequence,
                checked.previous_event_id,
                checked.event_at.isoformat(),
                canonical_experiment_ledger_json(checked),
            ),
        )
    except sqlite3.IntegrityError as error:
        raise DayForwardTrialLedgerConflictError("forward_trial_event_identity_conflict") from error
    return checked


def read_day_forward_trial_state(
    connection: sqlite3.Connection,
    trial_id: str,
) -> DayForwardTrialState:
    trial = trial_by_id(connection, trial_id)
    if trial is None:
        raise InvalidDayForwardTrialLedgerSourceError("forward_trial_missing")
    require_trial_parent(connection, trial)
    stored_events = events(connection, trial.trial_id)
    require_event_chain(trial, stored_events)
    return DayForwardTrialState(trial=trial, events=stored_events)


def day_forward_trials(
    connection: sqlite3.Connection,
    market_id: MarketId | None = None,
) -> tuple[DayForwardTrialState, ...]:
    rows: list[tuple[str]] = connection.execute(
        "SELECT trial_id FROM day_forward_trials ORDER BY rowid"
    ).fetchall()
    states = tuple(read_day_forward_trial_state(connection, row[0]) for row in rows)
    return tuple(
        state for state in states if market_id is None or state.trial.market_id is market_id
    )


__all__ = (
    "DayForwardTrialLedgerConflictError",
    "DayForwardTrialState",
    "InvalidDayForwardTrialLedgerSourceError",
    "append_day_forward_trial_event",
    "day_forward_trials",
    "read_day_forward_trial_state",
    "register_day_forward_trial",
)
