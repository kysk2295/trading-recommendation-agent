from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import final

from trading_agent.private_directory_identity import absolute_private_path
from trading_agent.sec_filing_document_models import (
    SecFilingDocumentRawResponse,
    SecFilingDocumentRun,
    SecFilingDocumentStatus,
    SecFilingDocumentTarget,
)
from trading_agent.sec_filing_document_store_sql import (
    InvalidSecFilingDocumentStoreError,
    document_reader,
    document_writer,
)


@dataclass(frozen=True, slots=True)
class SecStoredFilingDocumentReceipt:
    target: SecFilingDocumentTarget = field(repr=False)
    response: SecFilingDocumentRawResponse = field(repr=False)


@final
class SecFilingDocumentStore:
    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        self.path = absolute_private_path(path)

    def preflight_write(self) -> None:
        with document_writer(self.path) as connection:
            _require_all_rows(connection)
            connection.rollback()

    def append_receipt(
        self,
        target: SecFilingDocumentTarget,
        response: SecFilingDocumentRawResponse,
    ) -> bool:
        if response.target_id != target.target_id:
            raise InvalidSecFilingDocumentStoreError
        row = _receipt_row(target, response)
        with document_writer(self.path) as connection:
            _require_all_rows(connection)
            existing = connection.execute(
                "SELECT receipt_id,target_id,target_payload_sha256,target_payload_json,"
                "received_at,status_code,content_type,content_encoding,payload_sha256,raw_payload "
                "FROM sec_filing_document_receipts WHERE target_id=?",
                (target.target_id,),
            ).fetchone()
            if existing is not None:
                if tuple(existing) != row:
                    raise InvalidSecFilingDocumentStoreError
                return False
            if _run_from_connection(connection, target.target_id) is not None:
                raise InvalidSecFilingDocumentStoreError
            _ = connection.execute(
                "INSERT INTO sec_filing_document_receipts VALUES (?,?,?,?,?,?,?,?,?,?)",
                row,
            )
        return True

    def append_run(self, run: SecFilingDocumentRun) -> bool:
        row = _run_row(run)
        with document_writer(self.path) as connection:
            _require_all_rows(connection)
            receipt = _receipt_from_connection(connection, run.target.target_id)
            if not _run_matches_receipt(run, receipt):
                raise InvalidSecFilingDocumentStoreError
            existing = _run_row_from_connection(connection, run.target.target_id)
            if existing is not None:
                if tuple(existing) != row:
                    raise InvalidSecFilingDocumentStoreError
                return False
            _ = connection.execute(
                "INSERT INTO sec_filing_document_runs VALUES (?,?,?,?,?,?,?,?,?,?)",
                row,
            )
        return True

    def receipt_for_target(self, target_id: str) -> SecStoredFilingDocumentReceipt | None:
        with document_reader(self.path) as connection:
            _require_all_rows(connection)
            return _receipt_from_connection(connection, target_id)

    def run_for_target(self, target_id: str) -> SecFilingDocumentRun | None:
        with document_reader(self.path) as connection:
            _require_all_rows(connection)
            return _run_from_connection(connection, target_id)

    def counts(self) -> tuple[int, int]:
        with document_reader(self.path) as connection:
            _require_all_rows(connection)
            receipts = connection.execute("SELECT COUNT(*) FROM sec_filing_document_receipts").fetchone()
            runs = connection.execute("SELECT COUNT(*) FROM sec_filing_document_runs").fetchone()
            if receipts is None or runs is None:
                raise InvalidSecFilingDocumentStoreError
            return int(receipts[0]), int(runs[0])


def _require_all_rows(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        "SELECT target_id FROM sec_filing_document_receipts UNION SELECT target_id FROM sec_filing_document_runs"
    ).fetchall()
    for (target_id,) in rows:
        if not isinstance(target_id, str):
            raise InvalidSecFilingDocumentStoreError
        receipt = _receipt_from_connection(connection, target_id)
        run = _run_from_connection(connection, target_id)
        if receipt is None and run is None:
            raise InvalidSecFilingDocumentStoreError


def _receipt_from_connection(
    connection: sqlite3.Connection,
    target_id: str,
) -> SecStoredFilingDocumentReceipt | None:
    row = connection.execute(
        "SELECT receipt_id,target_id,target_payload_sha256,target_payload_json,received_at,"
        "status_code,content_type,content_encoding,payload_sha256,raw_payload "
        "FROM sec_filing_document_receipts WHERE target_id=?",
        (target_id,),
    ).fetchone()
    if row is None:
        return None
    target = _target_from_json(row[3])
    response = SecFilingDocumentRawResponse(
        target_id=row[1],
        received_at=dt.datetime.fromisoformat(row[4]),
        status_code=row[5],
        content_type=row[6],
        content_encoding=row[7],
        raw_payload=row[9],
    )
    if tuple(row) != _receipt_row(target, response) or row[8] != _sha(response.raw_payload):
        raise InvalidSecFilingDocumentStoreError
    return SecStoredFilingDocumentReceipt(target, response)


def _run_from_connection(
    connection: sqlite3.Connection,
    target_id: str,
) -> SecFilingDocumentRun | None:
    row = _run_row_from_connection(connection, target_id)
    if row is None:
        return None
    payload = row[9]
    if not isinstance(payload, str):
        raise InvalidSecFilingDocumentStoreError
    run = _run_from_json(payload)
    receipt = _receipt_from_connection(connection, target_id)
    if tuple(row) != _run_row(run) or not _run_matches_receipt(run, receipt):
        raise InvalidSecFilingDocumentStoreError
    return run


def _run_row_from_connection(
    connection: sqlite3.Connection,
    target_id: str,
) -> tuple[object, ...] | None:
    return connection.execute(
        "SELECT run_id,target_id,receipt_id,status,failure_code,started_at,completed_at,"
        "byte_count,payload_sha256,payload_json FROM sec_filing_document_runs WHERE target_id=?",
        (target_id,),
    ).fetchone()


def _receipt_row(
    target: SecFilingDocumentTarget,
    response: SecFilingDocumentRawResponse,
) -> tuple[object, ...]:
    target_json = _target_json(target)
    return (
        response.receipt_id,
        target.target_id,
        _sha(target_json.encode()),
        target_json,
        response.received_at.isoformat(),
        response.status_code,
        response.content_type,
        response.content_encoding,
        _sha(response.raw_payload),
        response.raw_payload,
    )


def _run_row(run: SecFilingDocumentRun) -> tuple[object, ...]:
    payload = _run_json(run)
    return (
        run.run_id,
        run.target.target_id,
        run.receipt_id,
        run.status.value,
        run.failure_code,
        run.started_at.isoformat(),
        run.completed_at.isoformat(),
        run.byte_count,
        _sha(payload.encode()),
        payload,
    )


def _run_matches_receipt(
    run: SecFilingDocumentRun,
    stored: SecStoredFilingDocumentReceipt | None,
) -> bool:
    if stored is None:
        return run.receipt_id is None and run.failure_code == "transport"
    response = stored.response
    expected_failure = (
        "http_status" if response.status_code != 200 else "empty_payload" if not response.raw_payload else None
    )
    return (
        run.target == stored.target
        and run.receipt_id == response.receipt_id
        and run.byte_count == len(response.raw_payload)
        and run.failure_code == expected_failure
        and (run.status is SecFilingDocumentStatus.SUCCESS) == (expected_failure is None)
        and run.started_at <= response.received_at <= run.completed_at
    )


def _target_json(target: SecFilingDocumentTarget) -> str:
    return json.dumps(
        {
            "accepted_at": target.accepted_at.isoformat(),
            "accession_number": target.accession_number,
            "cik": target.cik,
            "observed_at": target.observed_at.isoformat(),
            "primary_document": target.primary_document,
            "source_receipt_id": target.source_receipt_id,
            "source_version_id": target.source_version_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _target_from_json(payload: str) -> SecFilingDocumentTarget:
    data = json.loads(payload)
    data["accepted_at"] = dt.datetime.fromisoformat(data["accepted_at"])
    data["observed_at"] = dt.datetime.fromisoformat(data["observed_at"])
    return SecFilingDocumentTarget(**data)


def _run_json(run: SecFilingDocumentRun) -> str:
    return json.dumps(
        {
            "byte_count": run.byte_count,
            "completed_at": run.completed_at.isoformat(),
            "failure_code": run.failure_code,
            "receipt_id": run.receipt_id,
            "started_at": run.started_at.isoformat(),
            "status": run.status.value,
            "target": json.loads(_target_json(run.target)),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _run_from_json(payload: str) -> SecFilingDocumentRun:
    data = json.loads(payload)
    target_data = data.pop("target")
    target_data["accepted_at"] = dt.datetime.fromisoformat(target_data["accepted_at"])
    target_data["observed_at"] = dt.datetime.fromisoformat(target_data["observed_at"])
    data["target"] = SecFilingDocumentTarget(**target_data)
    data["started_at"] = dt.datetime.fromisoformat(data["started_at"])
    data["completed_at"] = dt.datetime.fromisoformat(data["completed_at"])
    data["status"] = SecFilingDocumentStatus(data["status"])
    return SecFilingDocumentRun(**data)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
