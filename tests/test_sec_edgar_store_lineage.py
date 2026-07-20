from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

import pytest

from trading_agent.sec_edgar_models import (
    SecCollectionStatus,
    SecSubmissionRawResponse,
    SecSubmissionRun,
)
from trading_agent.sec_edgar_parser import parse_sec_submission_snapshot
from trading_agent.sec_edgar_store import SecEdgarStore

FIXTURE = Path(__file__).parent / "fixtures/sec_edgar/submissions.json"
FIRST_AT = dt.datetime(2026, 7, 20, 14, tzinfo=dt.UTC)
SECOND_AT = FIRST_AT + dt.timedelta(minutes=1)


def test_sec_store_rejects_observation_substituted_from_same_filing_chain(tmp_path: Path) -> None:
    store = SecEdgarStore(tmp_path / "sec.sqlite3")
    original_response = _response("sec-cycle-001", FIRST_AT, FIXTURE.read_bytes())
    original_snapshot = parse_sec_submission_snapshot(original_response)
    _ = store.append_receipt(original_response)
    original = store.append_collection(_run(original_response), original_snapshot)
    corrected_payload = _changed_payload("Corrected current report")
    corrected_response = _response("sec-cycle-002", SECOND_AT, corrected_payload)
    corrected_snapshot = parse_sec_submission_snapshot(corrected_response)
    _ = store.append_receipt(corrected_response)
    corrected = store.append_collection(_run(corrected_response), corrected_snapshot)
    repeated_response = _response("sec-cycle-003", SECOND_AT + dt.timedelta(minutes=1), corrected_payload)
    repeated_snapshot = parse_sec_submission_snapshot(repeated_response)
    _ = store.append_receipt(repeated_response)
    _ = store.append_collection(_run(repeated_response), repeated_snapshot)
    pending_response = _response(
        "sec-cycle-pending",
        SECOND_AT + dt.timedelta(minutes=2),
        _changed_payload("Pending correction"),
    )
    pending_snapshot = parse_sec_submission_snapshot(pending_response)
    _ = store.append_receipt(pending_response)
    with sqlite3.connect(store.path) as connection:
        trigger_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='sec_filing_observations_no_update'"
        ).fetchone()[0]
        _ = connection.execute("DROP TRIGGER sec_filing_observations_no_update")
        _ = connection.execute(
            "UPDATE sec_filing_observations SET version_id=? WHERE run_id=? AND item_index=0",
            (original.filings[0].version_id, corrected.run.run_id),
        )
        _ = connection.execute(trigger_sql)

    before = store.path.read_bytes()
    new_response = _response(
        "sec-cycle-new",
        SECOND_AT + dt.timedelta(minutes=3),
        corrected_payload,
    )
    failed_run = SecSubmissionRun(
        collection_id="sec-cycle-failed",
        cik=corrected_response.cik,
        started_at=new_response.received_at,
        completed_at=new_response.received_at,
        status=SecCollectionStatus.FAILED,
        failure_code="transport",
        receipt_id=None,
        filing_count=0,
        additional_history_file_count=0,
    )

    with pytest.raises(ValueError):
        store.preflight_write()
    assert store.path.read_bytes() == before
    with pytest.raises(ValueError):
        _ = store.append_receipt(new_response)
    assert store.path.read_bytes() == before
    with pytest.raises(ValueError):
        _ = store.append_collection(_run(pending_response), pending_snapshot)
    assert store.path.read_bytes() == before
    with pytest.raises(ValueError):
        _ = store.append_failed_run(failed_run)
    assert store.path.read_bytes() == before
    with pytest.raises(ValueError):
        _ = store.filings_for_run(corrected.run.run_id)


def test_sec_store_rejects_snapshot_that_does_not_match_receipt(tmp_path: Path) -> None:
    store = SecEdgarStore(tmp_path / "sec.sqlite3")
    response = _response("sec-cycle-001", FIRST_AT, FIXTURE.read_bytes())
    other_response = _response("sec-cycle-other", FIRST_AT, _changed_payload("Injected correction"))
    mismatched_snapshot = parse_sec_submission_snapshot(other_response)
    _ = store.append_receipt(response)

    with pytest.raises(ValueError):
        _ = store.append_collection(_run(response), mismatched_snapshot)


def test_sec_store_rejects_tampered_version_ancestor_before_public_write(tmp_path: Path) -> None:
    store = SecEdgarStore(tmp_path / "sec.sqlite3")
    first_response = _response("sec-cycle-001", FIRST_AT, FIXTURE.read_bytes())
    first_snapshot = parse_sec_submission_snapshot(first_response)
    _ = store.append_receipt(first_response)
    first = store.append_collection(_run(first_response), first_snapshot)
    second_response = _response("sec-cycle-002", SECOND_AT, _changed_payload("Corrected current report"))
    second_snapshot = parse_sec_submission_snapshot(second_response)
    _ = store.append_receipt(second_response)
    second = store.append_collection(_run(second_response), second_snapshot)
    with sqlite3.connect(store.path) as connection:
        trigger_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='sec_filing_versions_no_update'"
        ).fetchone()[0]
        _ = connection.execute("DROP TRIGGER sec_filing_versions_no_update")
        _ = connection.execute(
            "UPDATE sec_filing_versions SET payload_json='{}' WHERE version_id=?",
            (first.filings[0].version_id,),
        )
        _ = connection.execute(trigger_sql)
    third_response = _response(
        "sec-cycle-003",
        SECOND_AT + dt.timedelta(minutes=1),
        _changed_payload("Second correction"),
    )

    with pytest.raises(ValueError):
        _ = store.append_receipt(third_response)
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sec_submission_receipts WHERE collection_id=?",
            (third_response.collection_id,),
        ).fetchone() == (0,)
    with pytest.raises(ValueError):
        _ = store.filings_for_run(second.run.run_id)


def test_sec_store_rejects_receipt_after_receiptless_transport_terminal(tmp_path: Path) -> None:
    store = SecEdgarStore(tmp_path / "sec.sqlite3")
    terminal = SecSubmissionRun(
        collection_id="sec-cycle-terminal",
        cik="0000320193",
        started_at=FIRST_AT,
        completed_at=FIRST_AT,
        status=SecCollectionStatus.FAILED,
        failure_code="transport",
        receipt_id=None,
        filing_count=0,
        additional_history_file_count=0,
    )
    assert store.append_failed_run(terminal) is True
    response = _response(terminal.collection_id, FIRST_AT, FIXTURE.read_bytes())
    before = store.path.read_bytes()

    with pytest.raises(ValueError):
        _ = store.append_receipt(response)

    assert store.path.read_bytes() == before
    assert store.collection_run(terminal.collection_id, terminal.cik) == terminal


def test_sec_store_selects_correction_parent_from_chain_not_rowid(tmp_path: Path) -> None:
    store = SecEdgarStore(tmp_path / "sec.sqlite3")
    first_response = _response("sec-cycle-001", FIRST_AT, FIXTURE.read_bytes())
    first_snapshot = parse_sec_submission_snapshot(first_response)
    _ = store.append_receipt(first_response)
    first = store.append_collection(_run(first_response), first_snapshot)
    second_response = _response(
        "sec-cycle-002",
        SECOND_AT,
        _changed_payload("First correction"),
    )
    _ = store.append_receipt(second_response)
    second = store.append_collection(_run(second_response), parse_sec_submission_snapshot(second_response))
    with sqlite3.connect(store.path) as connection:
        trigger_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='sec_filing_versions_no_update'"
        ).fetchone()[0]
        _ = connection.execute("DROP TRIGGER sec_filing_versions_no_update")
        _ = connection.execute(
            "UPDATE sec_filing_versions SET rowid=(SELECT MAX(rowid)+100 "
            "FROM sec_filing_versions) WHERE version_id=?",
            (first.filings[0].version_id,),
        )
        _ = connection.execute(trigger_sql)
    store.preflight_write()
    third_response = _response(
        "sec-cycle-003",
        SECOND_AT + dt.timedelta(minutes=1),
        _changed_payload("Second correction"),
    )
    _ = store.append_receipt(third_response)

    third = store.append_collection(_run(third_response), parse_sec_submission_snapshot(third_response))

    assert third.filings[0].previous_version_id == second.filings[0].version_id
    store.preflight_write()


def _changed_payload(description: str) -> bytes:
    changed = json.loads(FIXTURE.read_bytes())
    changed["filings"]["recent"]["primaryDocDescription"][0] = description
    return json.dumps(changed).encode()


def _response(collection_id: str, received_at: dt.datetime, payload: bytes) -> SecSubmissionRawResponse:
    return SecSubmissionRawResponse(
        collection_id=collection_id,
        cik="0000320193",
        received_at=received_at,
        status_code=200,
        content_type="application/json",
        raw_payload=payload,
    )


def _run(response: SecSubmissionRawResponse) -> SecSubmissionRun:
    return SecSubmissionRun(
        collection_id=response.collection_id,
        cik=response.cik,
        started_at=response.received_at,
        completed_at=response.received_at,
        status=SecCollectionStatus.SUCCESS,
        failure_code=None,
        receipt_id=response.receipt_id,
        filing_count=2,
        additional_history_file_count=1,
    )
