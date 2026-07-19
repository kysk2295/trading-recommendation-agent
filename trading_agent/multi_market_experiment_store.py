from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.multi_market_experiment_keys import (
    MultiMarketHypothesisRegistrationKey,
    MultiMarketStrategyVersionRegistrationKey,
    multi_market_hypothesis_registration_key,
    multi_market_strategy_version_registration_key,
)
from trading_agent.multi_market_experiment_models import (
    MultiMarketHypothesisRegistration,
    MultiMarketStrategyVersionRegistration,
)


@dataclass(frozen=True, slots=True)
class MultiMarketExperimentConflictError(RuntimeError):
    def __str__(self) -> str:
        return "multi-market experiment immutable identity conflicts"


@dataclass(frozen=True, slots=True)
class InvalidMultiMarketExperimentSourceError(RuntimeError):
    def __str__(self) -> str:
        return "multi-market experiment source is invalid"


@dataclass(frozen=True, slots=True)
class StoredMultiMarketHypothesisRegistration:
    registration_key: MultiMarketHypothesisRegistrationKey
    registration: MultiMarketHypothesisRegistration


@dataclass(frozen=True, slots=True)
class StoredMultiMarketStrategyVersionRegistration:
    registration_key: MultiMarketStrategyVersionRegistrationKey
    registration: MultiMarketStrategyVersionRegistration


def read_multi_market_hypotheses(
    connection: sqlite3.Connection,
) -> tuple[StoredMultiMarketHypothesisRegistration, ...]:
    rows: list[tuple[str, str, str, str, str, str, str]] = connection.execute(
        """SELECT registration_key, hypothesis_id, experiment_scope_key,
        primary_lane_id, market_id, agent_family, payload_json
        FROM multi_market_hypotheses ORDER BY rowid"""
    ).fetchall()
    return tuple(_stored_hypothesis(row) for row in rows)


def read_multi_market_strategy_versions(
    connection: sqlite3.Connection,
) -> tuple[StoredMultiMarketStrategyVersionRegistration, ...]:
    rows: list[tuple[str, str, str, str, str, str, str, str, str, str]] = connection.execute(
        """SELECT registration_key, strategy_version, strategy_id, hypothesis_id,
        experiment_scope_key, strategy_lane_id, market_id, agent_family,
        operating_mode, payload_json FROM multi_market_strategy_versions ORDER BY rowid"""
    ).fetchall()
    versions = tuple(_stored_version(row) for row in rows)
    for version in versions:
        _require_version_parent(connection, version.registration)
    return versions


def register_multi_market_hypothesis(
    connection: sqlite3.Connection,
    registration: MultiMarketHypothesisRegistration,
) -> bool:
    registration = _validated_hypothesis(registration)
    key = multi_market_hypothesis_registration_key(registration)
    if _legacy_identity_exists(connection, "hypotheses", "hypothesis_id", registration.hypothesis_id):
        raise MultiMarketExperimentConflictError
    existing = _hypothesis_by_id(connection, registration.hypothesis_id)
    if existing is not None:
        if existing.registration_key == key and existing.registration == registration:
            return False
        raise MultiMarketExperimentConflictError
    try:
        _ = connection.execute(
            "INSERT INTO multi_market_hypotheses VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                key,
                registration.hypothesis_id,
                registration.experiment_scope_key,
                registration.experiment_scope.primary_lane.canonical_id,
                registration.experiment_scope.primary_lane.market_id.value,
                registration.experiment_scope.primary_lane.agent_family.value,
                canonical_experiment_ledger_json(registration),
            ),
        )
    except sqlite3.IntegrityError as error:
        raise MultiMarketExperimentConflictError from error
    return True


def register_multi_market_strategy_version(
    connection: sqlite3.Connection,
    registration: MultiMarketStrategyVersionRegistration,
) -> bool:
    registration = _validated_version(registration)
    if _legacy_identity_exists(
        connection,
        "strategy_versions",
        "strategy_version",
        registration.strategy_version,
    ):
        raise MultiMarketExperimentConflictError
    _require_version_parent(connection, registration)
    key = multi_market_strategy_version_registration_key(registration)
    existing = _version_by_id(connection, registration.strategy_version)
    if existing is not None:
        if existing.registration_key == key and existing.registration == registration:
            return False
        raise MultiMarketExperimentConflictError
    try:
        _ = connection.execute(
            "INSERT INTO multi_market_strategy_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                key,
                registration.strategy_version,
                registration.strategy_lane.strategy_id,
                registration.hypothesis_id,
                registration.experiment_scope_key,
                registration.strategy_lane.canonical_id,
                registration.strategy_lane.market_id.value,
                registration.strategy_lane.agent_family.value,
                registration.operating_mode.value,
                canonical_experiment_ledger_json(registration),
            ),
        )
    except sqlite3.IntegrityError as error:
        raise MultiMarketExperimentConflictError from error
    return True


def _require_version_parent(
    connection: sqlite3.Connection,
    registration: MultiMarketStrategyVersionRegistration,
) -> None:
    parent = _hypothesis_by_id(connection, registration.hypothesis_id)
    if (
        parent is None
        or registration.experiment_scope_key != parent.registration.experiment_scope_key
        or registration.strategy_lane not in parent.registration.experiment_scope.lanes
        or registration.source_registered_at != parent.registration.source_registered_at
        or registration.ledger_recorded_at < parent.registration.ledger_recorded_at
    ):
        raise InvalidMultiMarketExperimentSourceError


def _hypothesis_by_id(
    connection: sqlite3.Connection,
    hypothesis_id: str,
) -> StoredMultiMarketHypothesisRegistration | None:
    row: tuple[str, str, str, str, str, str, str] | None = connection.execute(
        """SELECT registration_key, hypothesis_id, experiment_scope_key,
        primary_lane_id, market_id, agent_family, payload_json
        FROM multi_market_hypotheses WHERE hypothesis_id = ?""",
        (hypothesis_id,),
    ).fetchone()
    return None if row is None else _stored_hypothesis(row)


def _version_by_id(
    connection: sqlite3.Connection,
    strategy_version: str,
) -> StoredMultiMarketStrategyVersionRegistration | None:
    row: tuple[str, str, str, str, str, str, str, str, str, str] | None = connection.execute(
        """SELECT registration_key, strategy_version, strategy_id, hypothesis_id,
        experiment_scope_key, strategy_lane_id, market_id, agent_family,
        operating_mode, payload_json FROM multi_market_strategy_versions
        WHERE strategy_version = ?""",
        (strategy_version,),
    ).fetchone()
    return None if row is None else _stored_version(row)


def _stored_hypothesis(
    row: tuple[str, str, str, str, str, str, str],
) -> StoredMultiMarketHypothesisRegistration:
    key, hypothesis_id, scope_key, lane_id, market_id, family, payload = row
    try:
        registration = MultiMarketHypothesisRegistration.model_validate_json(payload)
    except ValueError:
        raise InvalidMultiMarketExperimentSourceError from None
    typed_key = MultiMarketHypothesisRegistrationKey(key)
    primary = registration.experiment_scope.primary_lane
    if (
        typed_key != multi_market_hypothesis_registration_key(registration)
        or hypothesis_id != registration.hypothesis_id
        or scope_key != registration.experiment_scope_key
        or lane_id != primary.canonical_id
        or market_id != primary.market_id.value
        or family != primary.agent_family.value
    ):
        raise InvalidMultiMarketExperimentSourceError
    return StoredMultiMarketHypothesisRegistration(typed_key, registration)


def _stored_version(
    row: tuple[str, str, str, str, str, str, str, str, str, str],
) -> StoredMultiMarketStrategyVersionRegistration:
    key, version, strategy_id, hypothesis_id, scope_key, lane_id, market_id, family, mode, payload = row
    try:
        registration = MultiMarketStrategyVersionRegistration.model_validate_json(payload)
    except ValueError:
        raise InvalidMultiMarketExperimentSourceError from None
    typed_key = MultiMarketStrategyVersionRegistrationKey(key)
    lane = registration.strategy_lane
    if (
        typed_key != multi_market_strategy_version_registration_key(registration)
        or version != registration.strategy_version
        or strategy_id != lane.strategy_id
        or hypothesis_id != registration.hypothesis_id
        or scope_key != registration.experiment_scope_key
        or lane_id != lane.canonical_id
        or market_id != lane.market_id.value
        or family != lane.agent_family.value
        or mode != registration.operating_mode.value
    ):
        raise InvalidMultiMarketExperimentSourceError
    return StoredMultiMarketStrategyVersionRegistration(typed_key, registration)


def _validated_hypothesis(
    registration: MultiMarketHypothesisRegistration,
) -> MultiMarketHypothesisRegistration:
    try:
        return MultiMarketHypothesisRegistration.model_validate(registration.model_dump(mode="python"))
    except ValueError:
        raise InvalidMultiMarketExperimentSourceError from None


def _validated_version(
    registration: MultiMarketStrategyVersionRegistration,
) -> MultiMarketStrategyVersionRegistration:
    try:
        return MultiMarketStrategyVersionRegistration.model_validate(registration.model_dump(mode="python"))
    except ValueError:
        raise InvalidMultiMarketExperimentSourceError from None


def _legacy_identity_exists(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    identity: str,
) -> bool:
    return (
        connection.execute(
            f"SELECT 1 FROM {table} WHERE {column} = ?",
            (identity,),
        ).fetchone()
        is not None
    )
