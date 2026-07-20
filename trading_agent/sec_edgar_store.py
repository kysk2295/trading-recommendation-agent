from __future__ import annotations

import datetime as dt
import hashlib
import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import final, override

from pydantic import ValidationError

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.private_directory_identity import (
    absolute_private_path,
    open_private_parent,
    require_private_directory,
)
from trading_agent.sec_edgar_models import (
    SecCollectionStatus,
    SecFilingEvent,
    SecSubmissionRawResponse,
    SecSubmissionRun,
    SecSubmissionSnapshot,
)
from trading_agent.sec_edgar_schema import (
    SEC_EDGAR_SCHEMA,
    SEC_EDGAR_SCHEMA_OBJECTS,
    SEC_EDGAR_SCHEMA_VERSION,
)
from trading_agent.sqlite_uri import sqlite_read_only_uri


class InvalidSecEdgarStoreError(ValueError):
    @override
    def __str__(self) -> str:
        return "SEC EDGAR store is invalid"


@dataclass(frozen=True, slots=True)
class SecStoredReceipt:
    response: SecSubmissionRawResponse = field(repr=False)


@dataclass(frozen=True, slots=True)
class SecStoredFilingVersion:
    version_id: str
    event: SecFilingEvent
    previous_version_id: str | None
    receipt_id: str
    observed_at: dt.datetime
    item_index: int


@dataclass(frozen=True, slots=True)
class SecReceiptAppendResult:
    stored: SecStoredReceipt
    created: bool


@dataclass(frozen=True, slots=True)
class SecCollectionAppendResult:
    run: SecSubmissionRun
    filings: tuple[SecStoredFilingVersion, ...]
    created: bool
    new_filing_version_count: int


@final
class SecEdgarStore:
    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        self.path = absolute_private_path(path)

    def append_receipt(self, response: SecSubmissionRawResponse) -> SecReceiptAppendResult:
        try:
            response = _validated_response(response)
            row = _receipt_row(response)
            with _writer(self.path) as connection:
                existing = connection.execute(
                    "SELECT receipt_id,collection_id,cik,received_at,status_code,content_type,"
                    "payload_sha256,raw_payload FROM sec_submission_receipts "
                    "WHERE collection_id=? AND cik=?",
                    (response.collection_id, response.cik),
                ).fetchone()
                if existing is not None:
                    if tuple(existing) != row:
                        raise InvalidSecEdgarStoreError
                    return SecReceiptAppendResult(SecStoredReceipt(response), False)
                _ = connection.execute(
                    "INSERT INTO sec_submission_receipts VALUES (?,?,?,?,?,?,?,?)",
                    row,
                )
                connection.commit()
            return SecReceiptAppendResult(SecStoredReceipt(response), True)
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise InvalidSecEdgarStoreError from None

    def append_collection(
        self,
        run: SecSubmissionRun,
        snapshot: SecSubmissionSnapshot,
    ) -> SecCollectionAppendResult:
        try:
            run = _validated_run(run)
            snapshot = SecSubmissionSnapshot.model_validate(snapshot.model_dump(mode="json"))
            if (
                run.status is not SecCollectionStatus.SUCCESS
                or run.cik != snapshot.cik
                or run.filing_count != len(snapshot.filings)
                or run.additional_history_file_count != snapshot.additional_history_file_count
                or run.receipt_id is None
            ):
                raise InvalidSecEdgarStoreError
            with _writer(self.path) as connection:
                _require_receipt(connection, run)
                existing = _run_from_connection(connection, run.run_id)
                if existing is not None:
                    filings = _filings_from_connection(connection, run.run_id)
                    if existing != run or tuple(item.event for item in filings) != snapshot.filings:
                        raise InvalidSecEdgarStoreError
                    return SecCollectionAppendResult(existing, filings, False, 0)
                filings, new_count = _append_filings(connection, run, snapshot)
                _insert_run(connection, run)
                for item in filings:
                    _ = connection.execute(
                        "INSERT INTO sec_filing_observations VALUES (?,?,?,?,?)",
                        (
                            run.run_id,
                            item.receipt_id,
                            item.version_id,
                            item.item_index,
                            item.observed_at.isoformat(),
                        ),
                    )
                connection.commit()
            return SecCollectionAppendResult(run, filings, True, new_count)
        except (OSError, sqlite3.Error, TypeError, ValidationError, ValueError):
            raise InvalidSecEdgarStoreError from None

    def append_failed_run(self, run: SecSubmissionRun) -> bool:
        try:
            run = _validated_run(run)
            if run.status is not SecCollectionStatus.FAILED:
                raise InvalidSecEdgarStoreError
            with _writer(self.path) as connection:
                if run.receipt_id is not None:
                    _require_receipt(connection, run)
                existing = _run_from_connection(connection, run.run_id)
                if existing is not None:
                    if existing != run or _filings_from_connection(connection, run.run_id):
                        raise InvalidSecEdgarStoreError
                    return False
                _insert_run(connection, run)
                connection.commit()
            return True
        except (OSError, sqlite3.Error, TypeError, ValidationError, ValueError):
            raise InvalidSecEdgarStoreError from None

    def collection_run(self, collection_id: str, cik: str) -> SecSubmissionRun | None:
        if not self.path.exists():
            return None
        try:
            run_id = hashlib.sha256(f"{collection_id}|{cik}".encode()).hexdigest()
            with _reader(self.path) as connection:
                return _run_from_connection(connection, run_id)
        except (OSError, sqlite3.Error, TypeError, ValidationError, ValueError):
            raise InvalidSecEdgarStoreError from None

    def receipt_for_collection(self, collection_id: str, cik: str) -> SecStoredReceipt | None:
        if not self.path.exists():
            return None
        try:
            with _reader(self.path) as connection:
                row = connection.execute(
                    "SELECT receipt_id,collection_id,cik,received_at,status_code,content_type,"
                    "payload_sha256,raw_payload FROM sec_submission_receipts "
                    "WHERE collection_id=? AND cik=?",
                    (collection_id, cik),
                ).fetchone()
            if row is None:
                return None
            response = SecSubmissionRawResponse(
                collection_id=row[1],
                cik=row[2],
                received_at=dt.datetime.fromisoformat(row[3]),
                status_code=row[4],
                content_type=row[5],
                raw_payload=row[7],
            )
            if _receipt_row(response) != tuple(row):
                raise InvalidSecEdgarStoreError
            return SecStoredReceipt(response)
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise InvalidSecEdgarStoreError from None

    def filings_for_run(self, run_id: str) -> tuple[SecStoredFilingVersion, ...]:
        if not self.path.exists():
            return ()
        try:
            with _reader(self.path) as connection:
                return _filings_from_connection(connection, run_id)
        except (OSError, sqlite3.Error, TypeError, ValidationError, ValueError):
            raise InvalidSecEdgarStoreError from None


def _append_filings(
    connection: sqlite3.Connection,
    run: SecSubmissionRun,
    snapshot: SecSubmissionSnapshot,
) -> tuple[tuple[SecStoredFilingVersion, ...], int]:
    stored: list[SecStoredFilingVersion] = []
    new_count = 0
    if run.receipt_id is None:
        raise InvalidSecEdgarStoreError
    for item_index, event in enumerate(snapshot.filings):
        previous = connection.execute(
            "SELECT version_id,event_id,previous_version_id FROM sec_filing_versions "
            "WHERE cik=? AND accession_number=? ORDER BY rowid DESC LIMIT 1",
            (event.cik, event.accession_number),
        ).fetchone()
        if previous is not None and previous[1] == event.event_id:
            version_id = previous[0]
            previous_version_id = previous[2]
        else:
            previous_version_id = None if previous is None else previous[0]
            version_id = _version_id(previous_version_id, event.event_id)
            payload_json = canonical_experiment_ledger_json(event)
            _ = connection.execute(
                "INSERT INTO sec_filing_versions VALUES (?,?,?,?,?,?,?)",
                (
                    version_id,
                    event.event_id,
                    event.cik,
                    event.accession_number,
                    previous_version_id,
                    hashlib.sha256(payload_json.encode()).hexdigest(),
                    payload_json,
                ),
            )
            new_count += 1
        stored.append(
            SecStoredFilingVersion(
                version_id,
                event,
                previous_version_id,
                run.receipt_id,
                run.completed_at,
                item_index,
            )
        )
    return tuple(stored), new_count


def _filings_from_connection(
    connection: sqlite3.Connection,
    run_id: str,
) -> tuple[SecStoredFilingVersion, ...]:
    rows = connection.execute(
        "SELECT v.version_id,v.event_id,v.previous_version_id,v.payload_sha256,v.payload_json,"
        "o.receipt_id,o.observed_at,o.item_index,v.cik,v.accession_number "
        "FROM sec_filing_observations o JOIN sec_filing_versions v ON v.version_id=o.version_id "
        "WHERE o.run_id=? ORDER BY o.item_index",
        (run_id,),
    ).fetchall()
    result: list[SecStoredFilingVersion] = []
    for row in rows:
        version_id, event_id, previous_id, payload_sha, payload_json = row[:5]
        receipt_id, observed_at, item_index, cik, accession_number = row[5:]
        event = SecFilingEvent.model_validate_json(payload_json)
        if (
            event.event_id != event_id
            or event.cik != cik
            or event.accession_number != accession_number
            or hashlib.sha256(payload_json.encode()).hexdigest() != payload_sha
            or _version_id(previous_id, event_id) != version_id
            or not _valid_previous(connection, previous_id, cik, accession_number)
        ):
            raise InvalidSecEdgarStoreError
        result.append(
            SecStoredFilingVersion(
                version_id,
                event,
                previous_id,
                receipt_id,
                dt.datetime.fromisoformat(observed_at),
                item_index,
            )
        )
    return tuple(result)


def _insert_run(connection: sqlite3.Connection, run: SecSubmissionRun) -> None:
    payload_json = canonical_experiment_ledger_json(run)
    _ = connection.execute(
        "INSERT INTO sec_submission_runs VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            run.run_id,
            run.collection_id,
            run.cik,
            run.receipt_id,
            run.status.value,
            run.failure_code,
            run.filing_count,
            run.additional_history_file_count,
            hashlib.sha256(payload_json.encode()).hexdigest(),
            payload_json,
        ),
    )


def _run_from_connection(connection: sqlite3.Connection, run_id: str) -> SecSubmissionRun | None:
    row = connection.execute(
        "SELECT payload_sha256,payload_json FROM sec_submission_runs WHERE run_id=?",
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    run = SecSubmissionRun.model_validate_json(row[1])
    if run.run_id != run_id or hashlib.sha256(row[1].encode()).hexdigest() != row[0]:
        raise InvalidSecEdgarStoreError
    return run


def _require_receipt(connection: sqlite3.Connection, run: SecSubmissionRun) -> None:
    row = connection.execute(
        "SELECT collection_id,cik,received_at FROM sec_submission_receipts WHERE receipt_id=?",
        (run.receipt_id,),
    ).fetchone()
    if row is None or row[0] != run.collection_id or row[1] != run.cik or row[2] > run.completed_at.isoformat():
        raise InvalidSecEdgarStoreError


def _valid_previous(
    connection: sqlite3.Connection,
    previous_id: str | None,
    cik: str,
    accession_number: str,
) -> bool:
    if previous_id is None:
        return True
    row = connection.execute(
        "SELECT cik,accession_number FROM sec_filing_versions WHERE version_id=?",
        (previous_id,),
    ).fetchone()
    return row == (cik, accession_number)


def _version_id(previous_id: str | None, event_id: str) -> str:
    return hashlib.sha256(f"sec-filing-version|{previous_id or 'root'}|{event_id}".encode()).hexdigest()


def _receipt_row(response: SecSubmissionRawResponse) -> tuple[object, ...]:
    return (
        response.receipt_id,
        response.collection_id,
        response.cik,
        response.received_at.isoformat(),
        response.status_code,
        response.content_type,
        hashlib.sha256(response.raw_payload).hexdigest(),
        response.raw_payload,
    )


def _validated_response(response: SecSubmissionRawResponse) -> SecSubmissionRawResponse:
    return SecSubmissionRawResponse(
        response.collection_id,
        response.cik,
        response.received_at,
        response.status_code,
        response.content_type,
        response.raw_payload,
    )


def _validated_run(run: SecSubmissionRun) -> SecSubmissionRun:
    return SecSubmissionRun.model_validate(run.model_dump(mode="json"))


@contextmanager
def _writer(path: Path) -> Iterator[sqlite3.Connection]:
    if path.is_symlink():
        raise InvalidSecEdgarStoreError
    parent_descriptor = open_private_parent(path.parent, create=True)
    try:
        require_private_directory(parent_descriptor)
    finally:
        os.close(parent_descriptor)
    if path.exists():
        _require_private_file(path)
    connection = sqlite3.connect(path, timeout=0.0)
    try:
        os.chmod(path, 0o600)
        _ = connection.execute("PRAGMA foreign_keys = ON")
        _prepare(connection)
        connection.execute("BEGIN IMMEDIATE")
        yield connection
    finally:
        connection.close()


@contextmanager
def _reader(path: Path) -> Iterator[sqlite3.Connection]:
    if path.is_symlink():
        raise InvalidSecEdgarStoreError
    _require_private_file(path)
    with closing(sqlite3.connect(sqlite_read_only_uri(path), uri=True)) as connection:
        _ = connection.execute("PRAGMA query_only = ON")
        _ = connection.execute("PRAGMA foreign_keys = ON")
        _require_schema(connection)
        yield connection


def _prepare(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA user_version").fetchone() == (0,):
        connection.executescript(
            f"BEGIN IMMEDIATE;{SEC_EDGAR_SCHEMA}PRAGMA user_version={SEC_EDGAR_SCHEMA_VERSION};COMMIT;"
        )
    _require_schema(connection)


def _require_schema(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA user_version").fetchone() != (SEC_EDGAR_SCHEMA_VERSION,):
        raise InvalidSecEdgarStoreError
    objects = frozenset(
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")
    )
    if objects != SEC_EDGAR_SCHEMA_OBJECTS or connection.execute("PRAGMA foreign_keys").fetchone() != (1,):
        raise InvalidSecEdgarStoreError


def _require_private_file(path: Path) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise InvalidSecEdgarStoreError
