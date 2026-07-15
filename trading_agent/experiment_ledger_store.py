from __future__ import annotations

import fcntl
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import final, override

from trading_agent.experiment_ledger_keys import (
    ExperimentTrialRegistrationKey,
    HypothesisRegistrationKey,
    StrategyVersionRegistrationKey,
    canonical_experiment_ledger_json,
    experiment_trial_registration_key,
    hypothesis_registration_key,
    strategy_version_registration_key,
)
from trading_agent.experiment_ledger_models import (
    ExperimentTrialRegistration,
    HypothesisRegistration,
    StrategyVersionRegistration,
)
from trading_agent.experiment_ledger_schema import (
    CREATE_EXPERIMENT_LEDGER_SCHEMA,
    EXPERIMENT_LEDGER_SCHEMA_VERSION,
)


class ExperimentLedgerConflictError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "전역 experiment ledger immutable identity의 내용이 다릅니다"


class InvalidExperimentLedgerSourceError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "전역 experiment ledger의 immutable source 계약이 유효하지 않습니다"


class ExperimentLedgerWriterLeaseUnavailableError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "전역 experiment ledger single Writer lease를 획득하지 못했습니다"


class UnsupportedExperimentLedgerSchemaError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "지원하지 않는 전역 experiment ledger schema입니다"


class InactiveExperimentLedgerWriterError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "종료된 전역 experiment ledger Writer는 사용할 수 없습니다"


@dataclass(frozen=True, slots=True)
class StoredHypothesisRegistration:
    registration_key: HypothesisRegistrationKey
    registration: HypothesisRegistration


@dataclass(frozen=True, slots=True)
class StoredStrategyVersionRegistration:
    registration_key: StrategyVersionRegistrationKey
    registration: StrategyVersionRegistration


@dataclass(frozen=True, slots=True)
class StoredExperimentTrialRegistration:
    registration_key: ExperimentTrialRegistrationKey
    registration: ExperimentTrialRegistration


class ExperimentLedgerReader:
    __slots__ = ("path",)

    path: Path

    def __init__(self, path: Path) -> None:
        self.path = path.resolve(strict=False)

    def is_initialized(self) -> bool:
        if not self.path.is_file():
            return False
        with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as connection:
            version: tuple[int] | None = connection.execute("PRAGMA user_version").fetchone()
        return version == (EXPERIMENT_LEDGER_SCHEMA_VERSION,)

    def hypotheses(self) -> tuple[StoredHypothesisRegistration, ...]:
        if not self.path.is_file():
            return ()
        with self._reader_connection() as connection:
            rows: list[tuple[str, str, str, str, str]] = connection.execute(
                """SELECT registration_key, hypothesis_id, experiment_scope_key,
                lane_id, payload_json FROM hypotheses ORDER BY rowid"""
            ).fetchall()
        return tuple(_stored_hypothesis(row) for row in rows)

    def strategy_versions(self) -> tuple[StoredStrategyVersionRegistration, ...]:
        if not self.path.is_file():
            return ()
        with self._reader_connection() as connection:
            rows: list[tuple[str, str, str, str, str, str, str]] = connection.execute(
                """SELECT registration_key, strategy_version, strategy_id,
                hypothesis_id, experiment_scope_key, lane_id, payload_json
                FROM strategy_versions ORDER BY rowid"""
            ).fetchall()
        return tuple(_stored_strategy_version(row) for row in rows)

    def trials(self) -> tuple[StoredExperimentTrialRegistration, ...]:
        if not self.path.is_file():
            return ()
        with self._reader_connection() as connection:
            rows: list[tuple[str, str, str, str, str, str]] = connection.execute(
                """SELECT registration_key, trial_id, strategy_version,
                experiment_scope_key, trial_kind, payload_json
                FROM experiment_trials ORDER BY rowid"""
            ).fetchall()
        return tuple(_stored_trial(row) for row in rows)

    def _reader_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        _ = connection.execute("PRAGMA query_only = ON")
        _ = connection.execute("PRAGMA foreign_keys = ON")
        _require_current_schema(connection)
        return connection


@final
class ExperimentLedgerStore(ExperimentLedgerReader):
    __slots__ = ()

    @contextmanager
    def writer(self) -> Iterator[ExperimentLedgerWriter]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = Path(f"{self.path}.writer.lock")
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "a+", encoding="utf-8") as lock_handle:
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ExperimentLedgerWriterLeaseUnavailableError from error
            connection = sqlite3.connect(self.path, timeout=0.0)
            os.chmod(self.path, 0o600)
            try:
                _prepare_writer_connection(connection)
                _ = connection.execute("BEGIN IMMEDIATE")
                writer = ExperimentLedgerWriter(connection)
                try:
                    yield writer
                except BaseException:
                    connection.rollback()
                    raise
                else:
                    connection.commit()
                finally:
                    writer._close()
            finally:
                connection.close()
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


@final
class ExperimentLedgerWriter:
    __slots__ = ("_active", "_connection")

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._active = True

    def register_hypothesis(self, registration: HypothesisRegistration) -> bool:
        self._require_active()
        registration = _validated_hypothesis(registration)
        key = hypothesis_registration_key(registration)
        existing = _hypothesis_by_id(self._connection, registration.hypothesis_id)
        if existing is not None:
            if existing.registration_key == key and existing.registration == registration:
                return False
            raise ExperimentLedgerConflictError
        return self._insert_immutable(
            table="hypotheses",
            key_column="registration_key",
            key=key,
            insert_sql="INSERT INTO hypotheses VALUES (?, ?, ?, ?, ?)",
            insert_values=(
                key,
                registration.hypothesis_id,
                registration.experiment_scope_key,
                registration.primary_lane.value,
                canonical_experiment_ledger_json(registration),
            ),
        )

    def register_strategy_version(self, registration: StrategyVersionRegistration) -> bool:
        self._require_active()
        registration = _validated_strategy_version(registration)
        parent = _hypothesis_by_id(self._connection, registration.hypothesis_id)
        if parent is None or not _version_matches_hypothesis(registration, parent.registration):
            raise InvalidExperimentLedgerSourceError
        key = strategy_version_registration_key(registration)
        existing = _strategy_version_by_id(self._connection, registration.strategy_version)
        if existing is not None:
            if existing.registration_key == key and existing.registration == registration:
                return False
            raise ExperimentLedgerConflictError
        return self._insert_immutable(
            table="strategy_versions",
            key_column="registration_key",
            key=key,
            insert_sql="INSERT INTO strategy_versions VALUES (?, ?, ?, ?, ?, ?, ?)",
            insert_values=(
                key,
                registration.strategy_version,
                registration.strategy_id,
                registration.hypothesis_id,
                registration.experiment_scope_key,
                registration.lane_id.value,
                canonical_experiment_ledger_json(registration),
            ),
        )

    def register_trial(self, registration: ExperimentTrialRegistration) -> bool:
        self._require_active()
        registration = _validated_trial(registration)
        parent = _strategy_version_by_id(self._connection, registration.strategy_version)
        if parent is None or not _trial_matches_version(registration, parent.registration):
            raise InvalidExperimentLedgerSourceError
        key = experiment_trial_registration_key(registration)
        existing = _trial_by_id(self._connection, registration.trial_id)
        if existing is not None:
            if existing.registration_key == key and existing.registration == registration:
                return False
            raise ExperimentLedgerConflictError
        return self._insert_immutable(
            table="experiment_trials",
            key_column="registration_key",
            key=key,
            insert_sql="INSERT INTO experiment_trials VALUES (?, ?, ?, ?, ?, ?)",
            insert_values=(
                key,
                registration.trial_id,
                registration.strategy_version,
                registration.experiment_scope_key,
                registration.trial_kind.value,
                canonical_experiment_ledger_json(registration),
            ),
        )

    def _insert_immutable(
        self,
        *,
        table: str,
        key_column: str,
        key: str,
        insert_sql: str,
        insert_values: tuple[object, ...],
    ) -> bool:
        collision = self._connection.execute(
            f"SELECT 1 FROM {table} WHERE {key_column} = ?",
            (key,),
        ).fetchone()
        if collision is not None:
            raise ExperimentLedgerConflictError
        try:
            _ = self._connection.execute(insert_sql, insert_values)
        except sqlite3.IntegrityError as error:
            raise ExperimentLedgerConflictError from error
        return True

    def _require_active(self) -> None:
        if not self._active:
            raise InactiveExperimentLedgerWriterError

    def _close(self) -> None:
        self._active = False


def _validated_hypothesis(registration: HypothesisRegistration) -> HypothesisRegistration:
    try:
        return HypothesisRegistration.model_validate(registration.model_dump(mode="python"))
    except ValueError:
        raise InvalidExperimentLedgerSourceError from None


def _validated_strategy_version(registration: StrategyVersionRegistration) -> StrategyVersionRegistration:
    try:
        return StrategyVersionRegistration.model_validate(registration.model_dump(mode="python"))
    except ValueError:
        raise InvalidExperimentLedgerSourceError from None


def _validated_trial(registration: ExperimentTrialRegistration) -> ExperimentTrialRegistration:
    try:
        return ExperimentTrialRegistration.model_validate(registration.model_dump(mode="python"))
    except ValueError:
        raise InvalidExperimentLedgerSourceError from None


def _version_matches_hypothesis(
    version: StrategyVersionRegistration,
    hypothesis: HypothesisRegistration,
) -> bool:
    return (
        version.hypothesis_id == hypothesis.hypothesis_id
        and version.experiment_scope_key == hypothesis.experiment_scope_key
        and version.lane_id is hypothesis.primary_lane
        and version.source_registered_at == hypothesis.source_registered_at
        and version.ledger_recorded_at >= hypothesis.ledger_recorded_at
    )


def _trial_matches_version(
    trial: ExperimentTrialRegistration,
    version: StrategyVersionRegistration,
) -> bool:
    return (
        trial.strategy_version == version.strategy_version
        and trial.experiment_scope_key == version.experiment_scope_key
        and trial.experiment_scope.hypothesis_id == version.hypothesis_id
        and trial.experiment_scope.primary_lane is version.lane_id
        and trial.registered_at >= version.ledger_recorded_at
    )


def _hypothesis_by_id(
    connection: sqlite3.Connection,
    hypothesis_id: str,
) -> StoredHypothesisRegistration | None:
    row: tuple[str, str, str, str, str] | None = connection.execute(
        """SELECT registration_key, hypothesis_id, experiment_scope_key,
        lane_id, payload_json FROM hypotheses WHERE hypothesis_id = ?""",
        (hypothesis_id,),
    ).fetchone()
    return None if row is None else _stored_hypothesis(row)


def _strategy_version_by_id(
    connection: sqlite3.Connection,
    strategy_version: str,
) -> StoredStrategyVersionRegistration | None:
    row: tuple[str, str, str, str, str, str, str] | None = connection.execute(
        """SELECT registration_key, strategy_version, strategy_id,
        hypothesis_id, experiment_scope_key, lane_id, payload_json
        FROM strategy_versions WHERE strategy_version = ?""",
        (strategy_version,),
    ).fetchone()
    return None if row is None else _stored_strategy_version(row)


def _trial_by_id(
    connection: sqlite3.Connection,
    trial_id: str,
) -> StoredExperimentTrialRegistration | None:
    row: tuple[str, str, str, str, str, str] | None = connection.execute(
        """SELECT registration_key, trial_id, strategy_version,
        experiment_scope_key, trial_kind, payload_json
        FROM experiment_trials WHERE trial_id = ?""",
        (trial_id,),
    ).fetchone()
    return None if row is None else _stored_trial(row)


def _stored_hypothesis(row: tuple[str, str, str, str, str]) -> StoredHypothesisRegistration:
    key, hypothesis_id, scope_key, lane_id, payload = row
    try:
        registration = HypothesisRegistration.model_validate_json(payload)
    except ValueError:
        raise InvalidExperimentLedgerSourceError from None
    typed_key = HypothesisRegistrationKey(key)
    if (
        typed_key != hypothesis_registration_key(registration)
        or hypothesis_id != registration.hypothesis_id
        or scope_key != registration.experiment_scope_key
        or lane_id != registration.primary_lane.value
    ):
        raise InvalidExperimentLedgerSourceError
    return StoredHypothesisRegistration(typed_key, registration)


def _stored_strategy_version(
    row: tuple[str, str, str, str, str, str, str],
) -> StoredStrategyVersionRegistration:
    key, strategy_version, strategy_id, hypothesis_id, scope_key, lane_id, payload = row
    try:
        registration = StrategyVersionRegistration.model_validate_json(payload)
    except ValueError:
        raise InvalidExperimentLedgerSourceError from None
    typed_key = StrategyVersionRegistrationKey(key)
    if (
        typed_key != strategy_version_registration_key(registration)
        or strategy_version != registration.strategy_version
        or strategy_id != registration.strategy_id
        or hypothesis_id != registration.hypothesis_id
        or scope_key != registration.experiment_scope_key
        or lane_id != registration.lane_id.value
    ):
        raise InvalidExperimentLedgerSourceError
    return StoredStrategyVersionRegistration(typed_key, registration)


def _stored_trial(row: tuple[str, str, str, str, str, str]) -> StoredExperimentTrialRegistration:
    key, trial_id, strategy_version, scope_key, trial_kind, payload = row
    try:
        registration = ExperimentTrialRegistration.model_validate_json(payload)
    except ValueError:
        raise InvalidExperimentLedgerSourceError from None
    typed_key = ExperimentTrialRegistrationKey(key)
    if (
        typed_key != experiment_trial_registration_key(registration)
        or trial_id != registration.trial_id
        or strategy_version != registration.strategy_version
        or scope_key != registration.experiment_scope_key
        or trial_kind != registration.trial_kind.value
    ):
        raise InvalidExperimentLedgerSourceError
    return StoredExperimentTrialRegistration(typed_key, registration)


def _prepare_writer_connection(connection: sqlite3.Connection) -> None:
    _ = connection.execute("PRAGMA foreign_keys = ON")
    _ = connection.execute("PRAGMA journal_mode = WAL").fetchone()
    version: tuple[int] | None = connection.execute("PRAGMA user_version").fetchone()
    current = 0 if version is None else version[0]
    if current == 0:
        objects = tuple(
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
        )
        if objects:
            raise UnsupportedExperimentLedgerSchemaError
        connection.executescript(CREATE_EXPERIMENT_LEDGER_SCHEMA)
        _ = connection.execute(f"PRAGMA user_version = {EXPERIMENT_LEDGER_SCHEMA_VERSION}")
        connection.commit()
        return
    _require_current_schema(connection)


def _require_current_schema(connection: sqlite3.Connection) -> None:
    version: tuple[int] | None = connection.execute("PRAGMA user_version").fetchone()
    if version != (EXPERIMENT_LEDGER_SCHEMA_VERSION,):
        raise UnsupportedExperimentLedgerSchemaError
