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
