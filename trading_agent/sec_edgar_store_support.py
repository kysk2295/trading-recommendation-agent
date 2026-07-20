from __future__ import annotations

import datetime as dt
import hashlib
import sqlite3

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.sec_edgar_models import (
    SecFilingEvent,
    SecSubmissionRawResponse,
    SecSubmissionRun,
    SecSubmissionSnapshot,
)
from trading_agent.sec_edgar_store_types import InvalidSecEdgarStoreError, SecStoredFilingVersion


def append_filings(
    connection: sqlite3.Connection,
    run: SecSubmissionRun,
    snapshot: SecSubmissionSnapshot,
) -> tuple[tuple[SecStoredFilingVersion, ...], int]:
    stored: list[SecStoredFilingVersion] = []
    new_count = 0
    if run.receipt_id is None:
        raise InvalidSecEdgarStoreError
    receipt_row = connection.execute(
        "SELECT received_at FROM sec_submission_receipts WHERE receipt_id=?",
        (run.receipt_id,),
    ).fetchone()
    if receipt_row is None:
        raise InvalidSecEdgarStoreError
    observed_at = dt.datetime.fromisoformat(receipt_row[0])
    if observed_at > run.completed_at:
        raise InvalidSecEdgarStoreError
    for item_index, event in enumerate(snapshot.filings):
        previous = connection.execute(
            "SELECT v.version_id,v.event_id,v.previous_version_id,"
            "(SELECT MAX(o.observed_at) FROM sec_filing_observations o "
            "WHERE o.version_id=v.version_id) FROM sec_filing_versions v "
            "WHERE cik=? AND accession_number=? ORDER BY v.rowid DESC LIMIT 1",
            (event.cik, event.accession_number),
        ).fetchone()
        if previous is not None and (
            previous[3] is None or dt.datetime.fromisoformat(previous[3]) > observed_at
        ):
            raise InvalidSecEdgarStoreError
        if previous is not None and previous[1] == event.event_id:
            version_id = previous[0]
            previous_version_id = previous[2]
        else:
            previous_version_id = None if previous is None else previous[0]
            version_id = version_identity(previous_version_id, event.event_id)
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
                observed_at,
                item_index,
            )
        )
    return tuple(stored), new_count


def filings_from_connection(
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
            or version_identity(previous_id, event_id) != version_id
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


def insert_run(connection: sqlite3.Connection, run: SecSubmissionRun) -> None:
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


def run_from_connection(connection: sqlite3.Connection, run_id: str) -> SecSubmissionRun | None:
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


def require_receipt(connection: sqlite3.Connection, run: SecSubmissionRun) -> None:
    row = connection.execute(
        "SELECT collection_id,cik,received_at FROM sec_submission_receipts WHERE receipt_id=?",
        (run.receipt_id,),
    ).fetchone()
    if row is None or row[0] != run.collection_id or row[1] != run.cik or row[2] > run.completed_at.isoformat():
        raise InvalidSecEdgarStoreError


def receipt_row(response: SecSubmissionRawResponse) -> tuple[str | int | bytes, ...]:
    return (
        response.receipt_id,
        response.collection_id,
        response.cik,
        response.received_at.isoformat(),
        response.status_code,
        response.content_type,
        response.content_encoding,
        hashlib.sha256(response.raw_payload).hexdigest(),
        response.raw_payload,
    )


def validated_response(response: SecSubmissionRawResponse) -> SecSubmissionRawResponse:
    return SecSubmissionRawResponse(
        collection_id=response.collection_id,
        cik=response.cik,
        received_at=response.received_at,
        status_code=response.status_code,
        content_type=response.content_type,
        raw_payload=response.raw_payload,
        content_encoding=response.content_encoding,
    )


def validated_run(run: SecSubmissionRun) -> SecSubmissionRun:
    return SecSubmissionRun.model_validate(run.model_dump(mode="json"))


def version_identity(previous_id: str | None, event_id: str) -> str:
    return hashlib.sha256(f"sec-filing-version|{previous_id or 'root'}|{event_id}".encode()).hexdigest()


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
