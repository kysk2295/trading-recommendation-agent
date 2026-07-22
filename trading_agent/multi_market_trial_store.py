from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.experiment_ledger_models import ExperimentTrialEvent, TrialEventKind
from trading_agent.multi_market_experiment_store import (
    InvalidMultiMarketExperimentSourceError,
    MultiMarketExperimentConflictError,
    read_multi_market_strategy_versions,
)
from trading_agent.multi_market_trial_keys import (
    MultiMarketTrialEventKey,
    MultiMarketTrialRegistrationKey,
    multi_market_trial_event_key,
    multi_market_trial_registration_key,
)
from trading_agent.multi_market_trial_models import (
    MultiMarketExperimentTrialRegistration,
    market_local_date,
    market_session_open,
)
from trading_agent.multi_market_trial_replay import events_by_trial, stored_trial, trial_by_id
from trading_agent.research_identity_models import AgentOperatingMode


@dataclass(frozen=True, slots=True)
class StoredMultiMarketTrialRegistration:
    registration_key: MultiMarketTrialRegistrationKey
    registration: MultiMarketExperimentTrialRegistration


@dataclass(frozen=True, slots=True)
class StoredMultiMarketTrialEvent:
    event_key: MultiMarketTrialEventKey
    event: ExperimentTrialEvent


def read_multi_market_trials(
    connection: sqlite3.Connection,
) -> tuple[StoredMultiMarketTrialRegistration, ...]:
    rows: list[tuple[str, str, str, str, str, str, str, str, str]] = connection.execute(
        """SELECT registration_key, trial_id, strategy_version, experiment_scope_key,
        strategy_lane_id, market_id, agent_family, trial_kind, payload_json
        FROM multi_market_trials ORDER BY rowid"""
    ).fetchall()
    trials = tuple(StoredMultiMarketTrialRegistration(*stored_trial(row)) for row in rows)
    for trial in trials:
        _require_parent(connection, trial.registration)
    return trials


def read_multi_market_trial_events(
    connection: sqlite3.Connection,
    trial_id: str,
) -> tuple[StoredMultiMarketTrialEvent, ...]:
    replayed = trial_by_id(connection, trial_id)
    if replayed is None:
        raise InvalidMultiMarketExperimentSourceError
    parent = StoredMultiMarketTrialRegistration(*replayed)
    _require_parent(connection, parent.registration)
    events = tuple(StoredMultiMarketTrialEvent(*item) for item in events_by_trial(connection, trial_id))
    _require_chain(parent.registration, events)
    return events


def register_multi_market_trial(
    connection: sqlite3.Connection,
    registration: MultiMarketExperimentTrialRegistration,
) -> bool:
    registration = _validated_registration(registration)
    if _legacy_trial_exists(connection, registration.trial_id):
        raise MultiMarketExperimentConflictError
    _require_parent(connection, registration)
    key = multi_market_trial_registration_key(registration)
    replayed = trial_by_id(connection, registration.trial_id)
    existing = None if replayed is None else StoredMultiMarketTrialRegistration(*replayed)
    if existing is not None:
        if existing.registration_key == key and existing.registration == registration:
            return False
        raise MultiMarketExperimentConflictError
    try:
        _ = connection.execute(
            "INSERT INTO multi_market_trials VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                key,
                registration.trial_id,
                registration.strategy_version,
                registration.experiment_scope_key,
                registration.strategy_lane.canonical_id,
                registration.strategy_lane.market_id.value,
                registration.strategy_lane.agent_family.value,
                registration.trial_kind.value,
                canonical_experiment_ledger_json(registration),
            ),
        )
    except sqlite3.IntegrityError as error:
        raise MultiMarketExperimentConflictError from error
    return True


def append_multi_market_trial_event(
    connection: sqlite3.Connection,
    event: ExperimentTrialEvent,
) -> bool:
    event = _validated_event(event)
    replayed = trial_by_id(connection, event.trial_id)
    if replayed is None:
        raise InvalidMultiMarketExperimentSourceError
    parent = StoredMultiMarketTrialRegistration(*replayed)
    _require_parent(connection, parent.registration)
    events = tuple(StoredMultiMarketTrialEvent(*item) for item in events_by_trial(connection, event.trial_id))
    _require_chain(parent.registration, events)
    existing = next((item for item in events if item.event.sequence == event.sequence), None)
    key = multi_market_trial_event_key(event)
    if existing is not None:
        if existing.event_key == key and existing.event == event:
            return False
        raise MultiMarketExperimentConflictError
    _require_chain(parent.registration, (*events, StoredMultiMarketTrialEvent(key, event)))
    try:
        _ = connection.execute(
            "INSERT INTO multi_market_trial_events VALUES (?, ?, ?, ?, ?, ?)",
            (
                key,
                event.trial_id,
                event.sequence,
                event.event_kind.value,
                event.previous_event_key,
                canonical_experiment_ledger_json(event),
            ),
        )
    except sqlite3.IntegrityError as error:
        raise MultiMarketExperimentConflictError from error
    return True


def _require_parent(
    connection: sqlite3.Connection,
    trial: MultiMarketExperimentTrialRegistration,
) -> None:
    matches = tuple(
        stored.registration
        for stored in read_multi_market_strategy_versions(connection)
        if stored.registration.strategy_version == trial.strategy_version
    )
    if len(matches) != 1:
        raise InvalidMultiMarketExperimentSourceError
    version = matches[0]
    if (
        version.operating_mode is not AgentOperatingMode.SHADOW
        or trial.experiment_scope.hypothesis_id != version.hypothesis_id
        or trial.experiment_scope_key != version.experiment_scope_key
        or trial.strategy_lane != version.strategy_lane
        or trial.registered_at < version.ledger_recorded_at
    ):
        raise InvalidMultiMarketExperimentSourceError


def _require_chain(
    parent: MultiMarketExperimentTrialRegistration,
    events: tuple[StoredMultiMarketTrialEvent, ...],
) -> None:
    if len(events) > 2:
        raise InvalidMultiMarketExperimentSourceError
    previous: StoredMultiMarketTrialEvent | None = None
    for sequence, stored in enumerate(events, start=1):
        event = stored.event
        local_date = market_local_date(parent.strategy_lane.market_id, event.occurred_at)
        if (
            event.sequence != sequence
            or event.trial_id != parent.trial_id
            or event.occurred_at < parent.registered_at
            or not parent.planned_start <= local_date <= parent.planned_end
            or (
                sequence == 1
                and event.occurred_at < market_session_open(parent.strategy_lane.market_id, parent.planned_start)
            )
            or (
                sequence == 1
                and event.event_kind not in (TrialEventKind.STARTED, TrialEventKind.CENSORED)
            )
            or (sequence == 2 and event.event_kind is TrialEventKind.STARTED)
            or (
                previous is not None
                and previous.event.event_kind is not TrialEventKind.STARTED
            )
            or (previous is None and event.previous_event_key is not None)
            or (previous is not None and event.previous_event_key != previous.event_key)
            or (previous is not None and event.occurred_at < previous.event.occurred_at)
        ):
            raise InvalidMultiMarketExperimentSourceError
        previous = stored


def _validated_registration(
    registration: MultiMarketExperimentTrialRegistration,
) -> MultiMarketExperimentTrialRegistration:
    try:
        return MultiMarketExperimentTrialRegistration.model_validate(registration.model_dump(mode="python"))
    except ValueError:
        raise InvalidMultiMarketExperimentSourceError from None


def _validated_event(event: ExperimentTrialEvent) -> ExperimentTrialEvent:
    try:
        return ExperimentTrialEvent.model_validate(event.model_dump(mode="python"))
    except ValueError:
        raise InvalidMultiMarketExperimentSourceError from None


def _legacy_trial_exists(connection: sqlite3.Connection, trial_id: str) -> bool:
    return connection.execute("SELECT 1 FROM experiment_trials WHERE trial_id = ?", (trial_id,)).fetchone() is not None
