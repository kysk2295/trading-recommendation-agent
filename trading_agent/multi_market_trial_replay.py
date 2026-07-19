from __future__ import annotations

import sqlite3

from trading_agent.experiment_ledger_models import ExperimentTrialEvent
from trading_agent.multi_market_experiment_store import InvalidMultiMarketExperimentSourceError
from trading_agent.multi_market_trial_keys import (
    MultiMarketTrialEventKey,
    MultiMarketTrialRegistrationKey,
    multi_market_trial_event_key,
    multi_market_trial_registration_key,
)
from trading_agent.multi_market_trial_models import MultiMarketExperimentTrialRegistration


def trial_by_id(
    connection: sqlite3.Connection,
    trial_id: str,
) -> tuple[MultiMarketTrialRegistrationKey, MultiMarketExperimentTrialRegistration] | None:
    row: tuple[str, str, str, str, str, str, str, str, str] | None = connection.execute(
        """SELECT registration_key, trial_id, strategy_version, experiment_scope_key,
        strategy_lane_id, market_id, agent_family, trial_kind, payload_json
        FROM multi_market_trials WHERE trial_id = ?""",
        (trial_id,),
    ).fetchone()
    return None if row is None else stored_trial(row)


def events_by_trial(
    connection: sqlite3.Connection,
    trial_id: str,
) -> tuple[tuple[MultiMarketTrialEventKey, ExperimentTrialEvent], ...]:
    rows: list[tuple[str, str, int, str, str | None, str]] = connection.execute(
        """SELECT event_key, trial_id, sequence, event_kind, previous_event_key, payload_json
        FROM multi_market_trial_events WHERE trial_id = ? ORDER BY sequence""",
        (trial_id,),
    ).fetchall()
    return tuple(stored_event(row) for row in rows)


def stored_trial(
    row: tuple[str, str, str, str, str, str, str, str, str],
) -> tuple[MultiMarketTrialRegistrationKey, MultiMarketExperimentTrialRegistration]:
    key, trial_id, version, scope_key, lane_id, market, family, kind, payload = row
    try:
        registration = MultiMarketExperimentTrialRegistration.model_validate_json(payload)
    except ValueError:
        raise InvalidMultiMarketExperimentSourceError from None
    typed_key = MultiMarketTrialRegistrationKey(key)
    lane = registration.strategy_lane
    if (
        typed_key != multi_market_trial_registration_key(registration)
        or trial_id != registration.trial_id
        or version != registration.strategy_version
        or scope_key != registration.experiment_scope_key
        or lane_id != lane.canonical_id
        or market != lane.market_id.value
        or family != lane.agent_family.value
        or kind != registration.trial_kind.value
    ):
        raise InvalidMultiMarketExperimentSourceError
    return typed_key, registration


def stored_event(
    row: tuple[str, str, int, str, str | None, str],
) -> tuple[MultiMarketTrialEventKey, ExperimentTrialEvent]:
    key, trial_id, sequence, kind, previous, payload = row
    try:
        event = ExperimentTrialEvent.model_validate_json(payload)
    except ValueError:
        raise InvalidMultiMarketExperimentSourceError from None
    typed_key = MultiMarketTrialEventKey(key)
    if (
        typed_key != multi_market_trial_event_key(event)
        or trial_id != event.trial_id
        or sequence != event.sequence
        or kind != event.event_kind.value
        or previous != event.previous_event_key
    ):
        raise InvalidMultiMarketExperimentSourceError
    return typed_key, event
