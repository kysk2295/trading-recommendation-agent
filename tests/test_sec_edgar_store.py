from __future__ import annotations

import datetime as dt
import json
import sqlite3
import stat
from pathlib import Path

import pytest

from trading_agent.sec_edgar_models import (
    SecCollectionStatus,
    SecSubmissionRawResponse,
    SecSubmissionRun,
    SecSubmissionSnapshot,
)
from trading_agent.sec_edgar_parser import parse_sec_submission_snapshot
from trading_agent.sec_edgar_store import SecEdgarStore

FIXTURE = Path(__file__).parent / "fixtures/sec_edgar/submissions.json"
FIRST_AT = dt.datetime(2026, 7, 20, 14, tzinfo=dt.UTC)
SECOND_AT = FIRST_AT + dt.timedelta(minutes=1)


def test_sec_store_appends_raw_before_replayable_collection(tmp_path: Path) -> None:
    # Given
    store = SecEdgarStore(tmp_path / "sec.sqlite3")
    response = _response("sec-cycle-001", FIRST_AT, FIXTURE.read_bytes())
    snapshot = parse_sec_submission_snapshot(response)

    # When
    receipt = store.append_receipt(response)
    first = store.append_collection(_run(response, len(snapshot.filings), 1), snapshot)
    replay = store.append_collection(_run(response, len(snapshot.filings), 1), snapshot)

    # Then
    assert receipt.created is True
    assert first.created is True
    assert first.new_filing_version_count == 2
    assert replay.created is False
    assert replay.new_filing_version_count == 0
    assert store.collection_run("sec-cycle-001", response.cik) == first.run
    assert tuple(item.event for item in store.filings_for_run(first.run.run_id)) == snapshot.filings
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


def test_sec_store_versions_changed_accession_without_update(tmp_path: Path) -> None:
    # Given
    store = SecEdgarStore(tmp_path / "sec.sqlite3")
    first_response = _response("sec-cycle-001", FIRST_AT, FIXTURE.read_bytes())
    first_snapshot = parse_sec_submission_snapshot(first_response)
    _ = store.append_receipt(first_response)
    first = store.append_collection(_run(first_response, 2, 1), first_snapshot)
    changed = json.loads(FIXTURE.read_bytes())
    changed["filings"]["recent"]["primaryDocDescription"][0] = "Corrected current report"
    second_response = _response("sec-cycle-002", SECOND_AT, json.dumps(changed).encode())
    second_snapshot = parse_sec_submission_snapshot(second_response)

    # When
    _ = store.append_receipt(second_response)
    second = store.append_collection(_run(second_response, 2, 1), second_snapshot)

    # Then
    assert second.new_filing_version_count == 1
    assert second.filings[0].previous_version_id == first.filings[0].version_id
    assert second.filings[0].version_id != first.filings[0].version_id
    assert second.filings[1].version_id == first.filings[1].version_id


def test_sec_store_rejects_conflicting_receipt_and_sql_update(tmp_path: Path) -> None:
    store = SecEdgarStore(tmp_path / "sec.sqlite3")
    response = _response("sec-cycle-001", FIRST_AT, FIXTURE.read_bytes())
    _ = store.append_receipt(response)
    conflict = _response("sec-cycle-001", FIRST_AT, b"{}")

    with pytest.raises(ValueError):
        _ = store.append_receipt(conflict)
    with sqlite3.connect(store.path) as connection, pytest.raises(sqlite3.IntegrityError):
        _ = connection.execute(
            "UPDATE sec_submission_receipts SET status_code=500 WHERE receipt_id=?",
            (response.receipt_id,),
        )


def test_sec_store_rejects_time_regressing_correction(tmp_path: Path) -> None:
    store = SecEdgarStore(tmp_path / "sec.sqlite3")
    first_response = _response("sec-cycle-001", SECOND_AT, FIXTURE.read_bytes())
    first_snapshot = parse_sec_submission_snapshot(first_response)
    _ = store.append_receipt(first_response)
    _ = store.append_collection(_run(first_response, 2, 1), first_snapshot)
    changed = json.loads(FIXTURE.read_bytes())
    changed["filings"]["recent"]["primaryDocDescription"][0] = "Regressing correction"
    regressing = _response("sec-cycle-002", FIRST_AT, json.dumps(changed).encode())
    snapshot = parse_sec_submission_snapshot(regressing)
    _ = store.append_receipt(regressing)

    with pytest.raises(ValueError):
        _ = store.append_collection(_run(regressing, 2, 1), snapshot)


def test_sec_snapshot_rejects_filing_from_another_cik() -> None:
    response = _response("sec-cycle-001", FIRST_AT, FIXTURE.read_bytes())
    snapshot = parse_sec_submission_snapshot(response)
    foreign = snapshot.filings[0].model_copy(update={"cik": "0000000001"})

    with pytest.raises(ValueError):
        _ = SecSubmissionSnapshot(
            cik=response.cik,
            filings=(foreign, *snapshot.filings[1:]),
            additional_history_file_count=1,
        )


def test_sec_store_rejects_filing_accepted_after_receipt(tmp_path: Path) -> None:
    store = SecEdgarStore(tmp_path / "sec.sqlite3")
    response = _response("sec-cycle-001", FIRST_AT, FIXTURE.read_bytes())
    snapshot = parse_sec_submission_snapshot(response)
    future = snapshot.filings[0].model_copy(update={"accepted_at": SECOND_AT})
    impossible = snapshot.model_copy(update={"filings": (future, *snapshot.filings[1:])})
    _ = store.append_receipt(response)

    with pytest.raises(ValueError):
        _ = store.append_collection(_run(response, 2, 1), impossible)


def test_sec_failed_run_rejects_discovered_history_without_receipt() -> None:
    with pytest.raises(ValueError):
        _ = SecSubmissionRun(
            collection_id="sec-cycle-001",
            cik="0000320193",
            started_at=FIRST_AT,
            completed_at=FIRST_AT,
            status=SecCollectionStatus.FAILED,
            failure_code="transport",
            receipt_id=None,
            filing_count=0,
            additional_history_file_count=3,
        )


def test_sec_store_rejects_receiptless_failure_when_receipt_exists(tmp_path: Path) -> None:
    store = SecEdgarStore(tmp_path / "sec.sqlite3")
    response = _response("sec-cycle-001", FIRST_AT, FIXTURE.read_bytes())
    _ = store.append_receipt(response)
    run = SecSubmissionRun(
        collection_id=response.collection_id,
        cik=response.cik,
        started_at=FIRST_AT,
        completed_at=FIRST_AT,
        status=SecCollectionStatus.FAILED,
        failure_code="transport",
        receipt_id=None,
        filing_count=0,
        additional_history_file_count=0,
    )

    with pytest.raises(ValueError):
        _ = store.append_failed_run(run)


def _response(collection_id: str, received_at: dt.datetime, payload: bytes) -> SecSubmissionRawResponse:
    return SecSubmissionRawResponse(
        collection_id=collection_id,
        cik="0000320193",
        received_at=received_at,
        status_code=200,
        content_type="application/json",
        raw_payload=payload,
    )


def _run(response: SecSubmissionRawResponse, filing_count: int, history_count: int) -> SecSubmissionRun:
    return SecSubmissionRun(
        collection_id=response.collection_id,
        cik=response.cik,
        started_at=response.received_at,
        completed_at=response.received_at,
        status=SecCollectionStatus.SUCCESS,
        failure_code=None,
        receipt_id=response.receipt_id,
        filing_count=filing_count,
        additional_history_file_count=history_count,
    )
