from __future__ import annotations

import datetime as dt
import hashlib
import sqlite3
from typing import assert_never

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.sec_edgar_models import (
    SecFilingEvent,
    SecSubmissionRawResponse,
    SecSubmissionRun,
    SecSubmissionSnapshot,
    SecSubmissionSourceKind,
    sec_additional_history_collection_id,
)
from trading_agent.sec_edgar_parser import parse_sec_submission_snapshot
from trading_agent.sec_edgar_store_projection import (
    receipt_bounds_valid,
    require_receipt_projection,
)
from trading_agent.sec_edgar_store_types import (
    InvalidSecEdgarStoreError,
    SecStoredFilingVersion,
    SecStoredReceipt,
)
from trading_agent.sec_edgar_store_version_chain import require_version_chain


def receipt_from_connection(
    connection: sqlite3.Connection,
    collection_id: str,
    cik: str,
) -> SecStoredReceipt | None:
    row = connection.execute(
        "SELECT receipt_id,collection_id,cik,received_at,status_code,content_type,"
        "content_encoding,payload_sha256,raw_payload FROM sec_submission_receipts "
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
        content_encoding=row[6],
        raw_payload=row[8],
    )
    if receipt_row(response) != tuple(row):
        raise InvalidSecEdgarStoreError
    return SecStoredReceipt(response)


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
        if event.cik != run.cik or event.accepted_at > observed_at:
            raise InvalidSecEdgarStoreError
        previous_rows = connection.execute(
            "SELECT v.version_id,v.event_id,v.previous_version_id,"
            "(SELECT MAX(o.observed_at) FROM sec_filing_observations o "
            "WHERE o.version_id=v.version_id) FROM sec_filing_versions v "
            "WHERE v.cik=? AND v.accession_number=? AND NOT EXISTS ("
            "SELECT 1 FROM sec_filing_versions child "
            "WHERE child.previous_version_id=v.version_id)",
            (event.cik, event.accession_number),
        ).fetchall()
        if len(previous_rows) > 1:
            raise InvalidSecEdgarStoreError
        previous = None if not previous_rows else previous_rows[0]
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
    run = run_from_connection(connection, run_id)
    if run is None:
        return ()
    rows = connection.execute(
        "SELECT v.version_id,v.event_id,v.previous_version_id,v.payload_sha256,v.payload_json,"
        "o.receipt_id,o.observed_at,o.item_index,v.cik,v.accession_number "
        "FROM sec_filing_observations o JOIN sec_filing_versions v ON v.version_id=o.version_id "
        "WHERE o.run_id=? ORDER BY o.item_index",
        (run_id,),
    ).fetchall()
    if len(rows) != run.filing_count:
        raise InvalidSecEdgarStoreError
    receipt = receipt_from_connection(connection, run.collection_id, run.cik)
    if run.receipt_id is None:
        if rows:
            raise InvalidSecEdgarStoreError
        return ()
    if receipt is None or receipt.response.receipt_id != run.receipt_id:
        raise InvalidSecEdgarStoreError
    result: list[SecStoredFilingVersion] = []
    for row in rows:
        version_id, event_id, previous_id, payload_sha, payload_json = row[:5]
        receipt_id, observed_at, item_index, cik, accession_number = row[5:]
        observed = dt.datetime.fromisoformat(observed_at)
        event = SecFilingEvent.model_validate_json(payload_json)
        require_version_chain(connection, version_id, cik, accession_number)
        if (
            event.event_id != event_id
            or event.cik != cik
            or event.accession_number != accession_number
            or hashlib.sha256(payload_json.encode()).hexdigest() != payload_sha
            or version_identity(previous_id, event_id) != version_id
            or event.cik != run.cik
            or event.accepted_at > observed
            or receipt_id != run.receipt_id
            or observed != receipt.response.received_at
            or item_index != len(result)
        ):
            raise InvalidSecEdgarStoreError
        result.append(
            SecStoredFilingVersion(
                version_id,
                event,
                previous_id,
                receipt_id,
                observed,
                item_index,
            )
        )
    require_receipt_projection(receipt.response, run, tuple(item.event for item in result))
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
        "SELECT run_id,collection_id,cik,receipt_id,status,failure_code,filing_count,"
        "additional_history_file_count,payload_sha256,payload_json "
        "FROM sec_submission_runs WHERE run_id=?",
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    run = SecSubmissionRun.model_validate_json(row[9])
    receipt = receipt_from_connection(connection, run.collection_id, run.cik)
    relational = (
        run.run_id,
        run.collection_id,
        run.cik,
        run.receipt_id,
        run.status.value,
        run.failure_code,
        run.filing_count,
        run.additional_history_file_count,
    )
    if (
        tuple(row[:8]) != relational
        or hashlib.sha256(row[9].encode()).hexdigest() != row[8]
        or (run.receipt_id is None) != (receipt is None)
        or (receipt is not None and receipt.response.receipt_id != run.receipt_id)
        or (receipt is not None and not receipt_bounds_valid(receipt.response, run))
    ):
        raise InvalidSecEdgarStoreError
    require_run_parent_binding(connection, run)
    return run


def require_run_parent_binding(
    connection: sqlite3.Connection,
    run: SecSubmissionRun,
) -> None:
    match run.source_kind:
        case SecSubmissionSourceKind.RECENT:
            return
        case SecSubmissionSourceKind.ADDITIONAL_HISTORY:
            if (
                run.parent_receipt_id is None
                or run.history_file is None
                or run.parent_receipt_id == run.receipt_id
            ):
                raise InvalidSecEdgarStoreError
            parent_row = connection.execute(
                "SELECT run_id,payload_json FROM sec_submission_runs WHERE receipt_id=?",
                (run.parent_receipt_id,),
            ).fetchone()
            if parent_row is None:
                raise InvalidSecEdgarStoreError
            parent_candidate = SecSubmissionRun.model_validate_json(parent_row[1])
            if parent_candidate.source_kind is not SecSubmissionSourceKind.RECENT:
                raise InvalidSecEdgarStoreError
            parent = run_from_connection(connection, parent_row[0])
            parent_receipt = receipt_by_id_from_connection(connection, run.parent_receipt_id)
            child_receipt = (
                None
                if run.receipt_id is None
                else receipt_by_id_from_connection(connection, run.receipt_id)
            )
            if (
                parent is None
                or parent.status.value != "success"
                or parent.receipt_id != run.parent_receipt_id
                or parent_receipt is None
                or run.completed_at < parent.completed_at
                or (
                    child_receipt is not None
                    and child_receipt.response.received_at
                    < parent_receipt.response.received_at
                )
                or run.collection_id
                != sec_additional_history_collection_id(run.parent_receipt_id, run.history_file)
            ):
                raise InvalidSecEdgarStoreError
            try:
                parent_snapshot = parse_sec_submission_snapshot(parent_receipt.response)
            except ValueError:
                raise InvalidSecEdgarStoreError from None
            if run.history_file not in parent_snapshot.additional_history_files:
                raise InvalidSecEdgarStoreError
        case unreachable:
            assert_never(unreachable)


def receipt_by_id_from_connection(
    connection: sqlite3.Connection,
    receipt_id: str,
) -> SecStoredReceipt | None:
    row = connection.execute(
        "SELECT collection_id,cik FROM sec_submission_receipts WHERE receipt_id=?",
        (receipt_id,),
    ).fetchone()
    if row is None:
        return None
    receipt = receipt_from_connection(connection, row[0], row[1])
    if receipt is None or receipt.response.receipt_id != receipt_id:
        raise InvalidSecEdgarStoreError
    return receipt


def require_receipt(connection: sqlite3.Connection, run: SecSubmissionRun) -> None:
    receipt = receipt_from_connection(connection, run.collection_id, run.cik)
    if (
        receipt is None
        or receipt.response.receipt_id != run.receipt_id
        or not receipt_bounds_valid(receipt.response, run)
    ):
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
