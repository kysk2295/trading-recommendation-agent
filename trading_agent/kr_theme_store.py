from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import final, override

from pydantic import ValidationError

from trading_agent.kr_theme_models import (
    KrCatalystCollectionCycle,
    KrCatalystObservation,
    KrCatalystRecord,
    KrCatalystSource,
    KrThemeClassification,
)
from trading_agent.kr_theme_schema import (
    CREATE_KR_THEME_SCHEMA,
    KR_THEME_SCHEMA_VERSION,
)


class KrThemeConflictError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "KR theme ledger immutable identity의 내용이 다릅니다"


class InvalidKrThemeSourceError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "KR theme ledger의 source·coverage·checksum 계보가 유효하지 않습니다"


class KrThemeWriterLeaseUnavailableError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "KR theme ledger single Writer lease를 획득하지 못했습니다"


class UnsupportedKrThemeSchemaError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "지원하지 않는 KR theme ledger schema입니다"


class InactiveKrThemeWriterError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "종료된 KR theme ledger Writer는 사용할 수 없습니다"


@dataclass(frozen=True, slots=True)
class KrCatalystAppendResult:
    catalyst_inserted: bool
    observation_inserted: bool


@dataclass(frozen=True, slots=True)
class StoredKrCatalyst:
    record: KrCatalystRecord
    raw_payload: bytes


class KrThemeReader:
    __slots__ = ("path",)

    path: Path

    def __init__(self, path: Path) -> None:
        self.path = path.resolve(strict=False)

    def is_initialized(self) -> bool:
        if not self.path.is_file():
            return False
        with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as connection:
            version: tuple[int] | None = connection.execute("PRAGMA user_version").fetchone()
        return version == (KR_THEME_SCHEMA_VERSION,)

    def catalysts(self) -> tuple[StoredKrCatalyst, ...]:
        if not self.path.is_file():
            return ()
        with self.reader_connection() as connection:
            rows: list[tuple[str, str, str, str | None, str | None, str, str, str, bytes]] = (
                connection.execute(
                    """SELECT catalyst_id, source, source_record_id, publisher_id,
                    published_at, first_observed_at, content_type, payload_sha256,
                    payload_blob FROM kr_catalysts ORDER BY rowid"""
                ).fetchall()
            )
        return tuple(_stored_catalyst(row) for row in rows)

    def observations(self) -> tuple[KrCatalystObservation, ...]:
        if not self.path.is_file():
            return ()
        with self.reader_connection() as connection:
            rows: list[tuple[str, str, str]] = connection.execute(
                """SELECT collection_cycle_id, catalyst_id, observed_at
                FROM kr_catalyst_observations ORDER BY rowid"""
            ).fetchall()
        try:
            return tuple(
                KrCatalystObservation(
                    collection_cycle_id=cycle_id,
                    catalyst_id=catalyst_id,
                    observed_at=dt.datetime.fromisoformat(observed_at),
                )
                for cycle_id, catalyst_id, observed_at in rows
            )
        except (ValidationError, ValueError) as error:
            raise InvalidKrThemeSourceError from error

    def cycles(self) -> tuple[KrCatalystCollectionCycle, ...]:
        if not self.path.is_file():
            return ()
        with self.reader_connection() as connection:
            rows: list[tuple[str, str, str, int, str]] = connection.execute(
                """SELECT collection_cycle_id, started_at, completed_at,
                complete, payload_json FROM kr_collection_cycles ORDER BY rowid"""
            ).fetchall()
        return tuple(_stored_cycle(row) for row in rows)

    def classifications(self) -> tuple[KrThemeClassification, ...]:
        if not self.path.is_file():
            return ()
        with self.reader_connection() as connection:
            rows: list[tuple[str, str, str, str, str, str, str, str]] = connection.execute(
                """SELECT classification_id, catalyst_id, classifier_kind,
                classifier_version, prompt_version, classification_run_id,
                classified_at, payload_json FROM kr_theme_classifications ORDER BY rowid"""
            ).fetchall()
            first_observed_rows: list[tuple[str, str]] = connection.execute(
                "SELECT catalyst_id, first_observed_at FROM kr_catalysts"
            ).fetchall()
        first_observed = {
            catalyst_id: dt.datetime.fromisoformat(value)
            for catalyst_id, value in first_observed_rows
        }
        return tuple(_stored_classification(row, first_observed) for row in rows)

    def reader_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        _ = connection.execute("PRAGMA query_only = ON")
        _ = connection.execute("PRAGMA foreign_keys = ON")
        _require_current_schema(connection)
        return connection


@final
class KrThemeStore(KrThemeReader):
    __slots__ = ()

    @contextmanager
    def writer(self) -> Iterator[KrThemeWriter]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = Path(f"{self.path}.writer.lock")
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "a+", encoding="utf-8") as lock_handle:
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise KrThemeWriterLeaseUnavailableError from error
            connection = sqlite3.connect(self.path, timeout=0.0)
            os.chmod(self.path, 0o600)
            try:
                _prepare_writer_connection(connection)
                writer = KrThemeWriter(connection)
                try:
                    yield writer
                finally:
                    writer._close()
            finally:
                connection.close()
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


@final
class KrThemeWriter:
    __slots__ = ("_active", "_connection")

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._active = True

    def append_catalyst(
        self,
        record: KrCatalystRecord,
        observation: KrCatalystObservation,
        raw_payload: bytes,
    ) -> KrCatalystAppendResult:
        self._require_active()
        record = KrCatalystRecord.model_validate(record.model_dump(mode="python"))
        observation = KrCatalystObservation.model_validate(observation.model_dump(mode="python"))
        payload = bytes(raw_payload)
        if (
            not payload
            or hashlib.sha256(payload).hexdigest() != record.payload_sha256
            or observation.catalyst_id != record.catalyst_id
            or observation.observed_at < record.first_observed_at
        ):
            raise InvalidKrThemeSourceError

        observation_row: tuple[str] | None = self._connection.execute(
            """SELECT observed_at FROM kr_catalyst_observations
            WHERE collection_cycle_id = ? AND catalyst_id = ?""",
            (observation.collection_cycle_id, observation.catalyst_id),
        ).fetchone()
        cycle_row: tuple[int] | None = self._connection.execute(
            "SELECT 1 FROM kr_collection_cycles WHERE collection_cycle_id = ?",
            (observation.collection_cycle_id,),
        ).fetchone()
        if cycle_row is not None and observation_row is None:
            raise InvalidKrThemeSourceError

        existing = self._catalyst(record.catalyst_id)
        catalyst_inserted = existing is None
        if existing is None:
            if observation.observed_at != record.first_observed_at:
                raise InvalidKrThemeSourceError
            self._insert_catalyst(record, payload)
        elif (
            record.first_observed_at < existing.record.first_observed_at
            or not _same_catalyst_content(existing, record, payload)
        ):
            raise KrThemeConflictError

        observation_inserted = observation_row is None
        if observation_row is None:
            _ = self._connection.execute(
                "INSERT INTO kr_catalyst_observations VALUES (?, ?, ?)",
                (
                    observation.collection_cycle_id,
                    observation.catalyst_id,
                    observation.observed_at.isoformat(),
                ),
            )
        elif observation.observed_at < dt.datetime.fromisoformat(observation_row[0]):
            raise KrThemeConflictError
        self._connection.commit()
        return KrCatalystAppendResult(catalyst_inserted, observation_inserted)

    def append_cycle(self, cycle: KrCatalystCollectionCycle) -> bool:
        self._require_active()
        cycle = KrCatalystCollectionCycle.model_validate(cycle.model_dump(mode="python"))
        payload = _canonical_json(cycle)
        existing: tuple[str] | None = self._connection.execute(
            "SELECT payload_json FROM kr_collection_cycles WHERE collection_cycle_id = ?",
            (cycle.collection_cycle_id,),
        ).fetchone()
        if existing is not None:
            if existing == (payload,):
                return False
            raise KrThemeConflictError
        self._validate_cycle_sources(cycle)
        _ = self._connection.execute(
            "INSERT INTO kr_collection_cycles VALUES (?, ?, ?, ?, ?)",
            (
                cycle.collection_cycle_id,
                cycle.started_at.isoformat(),
                cycle.completed_at.isoformat(),
                int(cycle.complete),
                payload,
            ),
        )
        self._connection.commit()
        return True

    def append_classification(self, classification: KrThemeClassification) -> bool:
        self._require_active()
        classification = KrThemeClassification.model_validate(
            classification.model_dump(mode="python")
        )
        source: tuple[str] | None = self._connection.execute(
            "SELECT first_observed_at FROM kr_catalysts WHERE catalyst_id = ?",
            (classification.catalyst_id,),
        ).fetchone()
        if (
            source is None
            or classification.classified_at < dt.datetime.fromisoformat(source[0])
        ):
            raise InvalidKrThemeSourceError
        payload = _canonical_json(classification)
        existing: tuple[str] | None = self._connection.execute(
            "SELECT payload_json FROM kr_theme_classifications WHERE classification_id = ?",
            (classification.classification_id,),
        ).fetchone()
        if existing is not None:
            if existing == (payload,):
                return False
            raise KrThemeConflictError
        try:
            _ = self._connection.execute(
                "INSERT INTO kr_theme_classifications VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    classification.classification_id,
                    classification.catalyst_id,
                    classification.classifier_kind.value,
                    classification.classifier_version,
                    classification.prompt_version,
                    classification.classification_run_id,
                    classification.classified_at.isoformat(),
                    payload,
                ),
            )
            self._connection.commit()
        except sqlite3.IntegrityError as error:
            raise KrThemeConflictError from error
        return True

    def _catalyst(self, catalyst_id: str) -> StoredKrCatalyst | None:
        row: tuple[str, str, str, str | None, str | None, str, str, str, bytes] | None = (
            self._connection.execute(
                """SELECT catalyst_id, source, source_record_id, publisher_id,
                published_at, first_observed_at, content_type, payload_sha256,
                payload_blob FROM kr_catalysts WHERE catalyst_id = ?""",
                (catalyst_id,),
            ).fetchone()
        )
        return None if row is None else _stored_catalyst(row)

    def _insert_catalyst(self, record: KrCatalystRecord, payload: bytes) -> None:
        try:
            _ = self._connection.execute(
                "INSERT INTO kr_catalysts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.catalyst_id,
                    record.source.value,
                    record.source_record_id,
                    record.publisher_id,
                    None if record.published_at is None else record.published_at.isoformat(),
                    record.first_observed_at.isoformat(),
                    record.content_type,
                    record.payload_sha256,
                    payload,
                ),
            )
        except sqlite3.IntegrityError as error:
            raise KrThemeConflictError from error

    def _validate_cycle_sources(self, cycle: KrCatalystCollectionCycle) -> None:
        rows: list[tuple[str, int]] = self._connection.execute(
            """SELECT catalyst.source, COUNT(*) FROM kr_catalyst_observations observation
            JOIN kr_catalysts catalyst ON catalyst.catalyst_id = observation.catalyst_id
            WHERE observation.collection_cycle_id = ? GROUP BY catalyst.source""",
            (cycle.collection_cycle_id,),
        ).fetchall()
        actual = {source: count for source, count in rows}
        declared = {item.source.value: item.record_count for item in cycle.coverage}
        if any(actual.get(source.value, 0) != declared[source.value] for source in KrCatalystSource):
            raise InvalidKrThemeSourceError
        observed_rows: list[tuple[str]] = self._connection.execute(
            """SELECT observed_at FROM kr_catalyst_observations
            WHERE collection_cycle_id = ?""",
            (cycle.collection_cycle_id,),
        ).fetchall()
        if any(
            not cycle.started_at <= dt.datetime.fromisoformat(value) <= cycle.completed_at
            for (value,) in observed_rows
        ):
            raise InvalidKrThemeSourceError

    def _require_active(self) -> None:
        if not self._active:
            raise InactiveKrThemeWriterError

    def _close(self) -> None:
        self._active = False


def _prepare_writer_connection(connection: sqlite3.Connection) -> None:
    _ = connection.execute("PRAGMA foreign_keys = ON")
    _ = connection.execute("PRAGMA journal_mode = WAL").fetchone()
    version: tuple[int] | None = connection.execute("PRAGMA user_version").fetchone()
    current = 0 if version is None else version[0]
    if current == 0:
        objects = tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            ).fetchall()
        )
        if objects:
            raise UnsupportedKrThemeSchemaError
        connection.executescript(CREATE_KR_THEME_SCHEMA)
        _ = connection.execute(f"PRAGMA user_version = {KR_THEME_SCHEMA_VERSION}")
        connection.commit()
        return
    _require_current_schema(connection)


def _require_current_schema(connection: sqlite3.Connection) -> None:
    version: tuple[int] | None = connection.execute("PRAGMA user_version").fetchone()
    if version != (KR_THEME_SCHEMA_VERSION,):
        raise UnsupportedKrThemeSchemaError


def _stored_catalyst(
    row: tuple[str, str, str, str | None, str | None, str, str, str, bytes],
) -> StoredKrCatalyst:
    (
        catalyst_id,
        source,
        source_record_id,
        publisher_id,
        published_at,
        first_observed_at,
        content_type,
        payload_sha256,
        raw_payload,
    ) = row
    try:
        payload = bytes(raw_payload)
        record = KrCatalystRecord(
            source=KrCatalystSource(source),
            source_record_id=source_record_id,
            publisher_id=publisher_id,
            published_at=(
                None if published_at is None else dt.datetime.fromisoformat(published_at)
            ),
            first_observed_at=dt.datetime.fromisoformat(first_observed_at),
            content_type=content_type,
            payload_sha256=payload_sha256,
        )
        if (
            record.catalyst_id != catalyst_id
            or hashlib.sha256(payload).hexdigest() != record.payload_sha256
        ):
            raise ValueError
        return StoredKrCatalyst(record, payload)
    except (ValidationError, ValueError) as error:
        raise InvalidKrThemeSourceError from error


def _stored_cycle(
    row: tuple[str, str, str, int, str],
) -> KrCatalystCollectionCycle:
    cycle_id, started_at, completed_at, complete, payload = row
    try:
        cycle = KrCatalystCollectionCycle.model_validate_json(payload)
        if (
            cycle.collection_cycle_id != cycle_id
            or cycle.started_at != dt.datetime.fromisoformat(started_at)
            or cycle.completed_at != dt.datetime.fromisoformat(completed_at)
            or cycle.complete is not bool(complete)
        ):
            raise ValueError
        return cycle
    except (ValidationError, ValueError) as error:
        raise InvalidKrThemeSourceError from error


def _stored_classification(
    row: tuple[str, str, str, str, str, str, str, str],
    first_observed: dict[str, dt.datetime],
) -> KrThemeClassification:
    classification_id, catalyst_id, kind, version, prompt, run_id, classified_at, payload = row
    try:
        classification = KrThemeClassification.model_validate_json(payload)
        observed_at = first_observed[classification.catalyst_id]
        if (
            classification.classification_id != classification_id
            or classification.catalyst_id != catalyst_id
            or classification.classifier_kind.value != kind
            or classification.classifier_version != version
            or classification.prompt_version != prompt
            or classification.classification_run_id != run_id
            or classification.classified_at != dt.datetime.fromisoformat(classified_at)
            or classification.classified_at < observed_at
        ):
            raise ValueError
        return classification
    except (KeyError, ValidationError, ValueError) as error:
        raise InvalidKrThemeSourceError from error


def _same_catalyst_content(
    stored: StoredKrCatalyst,
    incoming: KrCatalystRecord,
    payload: bytes,
) -> bool:
    existing = stored.record
    return (
        existing.source is incoming.source
        and existing.source_record_id == incoming.source_record_id
        and existing.publisher_id == incoming.publisher_id
        and existing.published_at == incoming.published_at
        and existing.content_type == incoming.content_type
        and existing.payload_sha256 == incoming.payload_sha256
        and stored.raw_payload == payload
    )


def _canonical_json(model: KrCatalystCollectionCycle | KrThemeClassification) -> str:
    return json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
