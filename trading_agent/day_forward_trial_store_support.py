from __future__ import annotations

import hashlib
import json
import sqlite3

from pydantic import BaseModel

from trading_agent.day_forward_trial_chain import InvalidDayForwardTrialLedgerSourceError
from trading_agent.day_forward_trial_models import DayForwardTrial, DayForwardTrialEvent
from trading_agent.day_research_ledger import (
    _capsule_by_id,
    _require_capsule_parent_coherence,
    _version_by_id,
)
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json


def trial_by_id(
    connection: sqlite3.Connection,
    trial_id: str,
) -> DayForwardTrial | None:
    rows: list[tuple[str, str, str, str, str, str, str]] = connection.execute(
        "SELECT trial_id,capsule_id,hypothesis_version_id,market_id,session_date,created_at,payload_json "
        "FROM day_forward_trials WHERE trial_id=?",
        (trial_id,),
    ).fetchall()
    if not rows:
        return None
    if len(rows) != 1:
        raise InvalidDayForwardTrialLedgerSourceError("stored_forward_trial_duplicate")
    row = rows[0]
    try:
        trial = DayForwardTrial.model_validate_json(row[6])
    except ValueError:
        raise InvalidDayForwardTrialLedgerSourceError("stored_forward_trial_payload_invalid") from None
    if (
        row[:6]
        != (
            trial.trial_id,
            trial.capsule_id,
            trial.hypothesis_version_id,
            trial.market_id.value,
            trial.session_date.isoformat(),
            trial.preregistered_at.isoformat(),
        )
        or row[6] != canonical_experiment_ledger_json(trial)
    ):
        raise InvalidDayForwardTrialLedgerSourceError("stored_forward_trial_index_invalid")
    return trial


def events(
    connection: sqlite3.Connection,
    trial_id: str,
) -> tuple[DayForwardTrialEvent, ...]:
    rows: list[tuple[str, str, str, str, int, str | None, str, str]] = connection.execute(
        "SELECT event_id,trial_id,market_id,session_date,sequence,previous_event_id,event_at,payload_json "
        "FROM day_forward_trial_events WHERE trial_id=? ORDER BY sequence",
        (trial_id,),
    ).fetchall()
    parsed: list[DayForwardTrialEvent] = []
    for row in rows:
        try:
            event = DayForwardTrialEvent.model_validate_json(row[7])
        except ValueError:
            raise InvalidDayForwardTrialLedgerSourceError("stored_forward_trial_event_payload_invalid") from None
        if (
            row[:7]
            != (
                event.event_id,
                event.trial_id,
                event.market_id.value,
                event.session_date.isoformat(),
                event.sequence,
                event.previous_event_id,
                event.event_at.isoformat(),
            )
            or row[7] != canonical_experiment_ledger_json(event)
        ):
            raise InvalidDayForwardTrialLedgerSourceError("stored_forward_trial_event_index_invalid")
        parsed.append(event)
    return tuple(parsed)


def require_trial_parent(connection: sqlite3.Connection, trial: DayForwardTrial) -> None:
    stored_capsule = _capsule_by_id(connection, trial.capsule_id)
    stored_version = _version_by_id(connection, trial.hypothesis_version_id)
    if stored_capsule is None or stored_version is None:
        raise InvalidDayForwardTrialLedgerSourceError("forward_trial_parent_missing")
    capsule = stored_capsule.capsule
    version = stored_version.version
    _require_capsule_parent_coherence(connection, capsule)
    if (
        capsule.hypothesis_version_id != trial.hypothesis_version_id
        or capsule.market_id is not trial.market_id
        or version.market_id is not trial.market_id
        or trial.cost_model_sha256 != _model_sha256(capsule.cost_model)
        or trial.source_refs_sha256 != _text_tuple_sha256(version.source_refs)
        or trial.evidence_schema_sha256 != _text_tuple_sha256(capsule.evidence_schema)
        or trial.preregistered_at < capsule.published_at
        or trial.registration_completed_bar_at < version.registration_completed_bar_at
        or trial.first_eligible_completed_bar_at < version.first_shadow_eligible_at
    ):
        raise InvalidDayForwardTrialLedgerSourceError("forward_trial_parent_mismatch")


def validated_trial(trial: DayForwardTrial) -> DayForwardTrial:
    try:
        return DayForwardTrial.model_validate(trial.model_dump(mode="python"))
    except ValueError:
        raise InvalidDayForwardTrialLedgerSourceError("forward_trial_invalid") from None


def _model_sha256(model: BaseModel) -> str:
    return hashlib.sha256(canonical_experiment_ledger_json(model).encode()).hexdigest()


def _text_tuple_sha256(values: tuple[str, ...]) -> str:
    encoded = json.dumps(values, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


__all__ = (
    "events",
    "require_trial_parent",
    "trial_by_id",
    "validated_trial",
)
