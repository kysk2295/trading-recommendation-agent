from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from pathlib import Path

from trading_agent.sec_edgar_models import (
    SecCollectionStatus,
    SecSubmissionRawResponse,
    SecSubmissionRun,
)
from trading_agent.sec_edgar_parser import parse_sec_submission_snapshot
from trading_agent.sec_edgar_store import SecEdgarStore

FIXTURE = Path(__file__).parent / "fixtures/sec_edgar/submissions.json"
RECEIVED_AT = dt.datetime(2026, 7, 20, 14, tzinfo=dt.UTC)


def test_sec_store_replays_legacy_v1_run_payload_without_history_fields(tmp_path: Path) -> None:
    store = SecEdgarStore(tmp_path / "sec.sqlite3")
    response = _response("sec-cycle-legacy", FIXTURE.read_bytes())
    snapshot = parse_sec_submission_snapshot(response)
    _ = store.append_receipt(response)
    run = store.append_collection(_run(response), snapshot).run
    with sqlite3.connect(store.path) as connection:
        row = connection.execute(
            "SELECT payload_json FROM sec_submission_runs WHERE run_id=?",
            (run.run_id,),
        ).fetchone()
        payload = json.loads(row[0])
        del payload["source_kind"]
        del payload["parent_receipt_id"]
        del payload["history_file"]
        payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        trigger_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='sec_submission_runs_no_update'"
        ).fetchone()[0]
        _ = connection.execute("DROP TRIGGER sec_submission_runs_no_update")
        _ = connection.execute(
            "UPDATE sec_submission_runs SET payload_sha256=?,payload_json=? WHERE run_id=?",
            (hashlib.sha256(payload_json.encode()).hexdigest(), payload_json, run.run_id),
        )
        _ = connection.execute(trigger_sql)

    assert store.collection_run(run.collection_id, run.cik) == run


def test_sec_store_replays_legacy_opaque_history_manifest_receipt(tmp_path: Path) -> None:
    store = SecEdgarStore(tmp_path / "sec.sqlite3")
    response = _response("sec-cycle-legacy-opaque", FIXTURE.read_bytes())
    snapshot = parse_sec_submission_snapshot(response)
    _ = store.append_receipt(response)
    run = store.append_collection(_run(response), snapshot).run
    document = json.loads(FIXTURE.read_bytes())
    document["filings"]["files"] = [{"unrecognized": [1, 2, 3]}]
    legacy_response = _response(
        response.collection_id,
        json.dumps(document, separators=(",", ":"), sort_keys=True).encode(),
    )
    legacy_run = run.model_copy(update={"receipt_id": legacy_response.receipt_id})
    legacy_payload = legacy_run.model_dump(mode="json")
    del legacy_payload["source_kind"]
    del legacy_payload["parent_receipt_id"]
    del legacy_payload["history_file"]
    legacy_payload_json = json.dumps(legacy_payload, separators=(",", ":"), sort_keys=True)
    trigger_names = (
        "sec_submission_receipts_no_update",
        "sec_submission_runs_no_update",
        "sec_filing_observations_no_update",
    )
    with sqlite3.connect(store.path) as connection:
        trigger_rows = connection.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='trigger' "
            f"AND name IN ({','.join('?' for _ in trigger_names)})",
            trigger_names,
        ).fetchall()
        for name, _sql in trigger_rows:
            _ = connection.execute(f"DROP TRIGGER {name}")
        _ = connection.execute(
            "UPDATE sec_submission_receipts SET receipt_id=?,payload_sha256=?,raw_payload=? "
            "WHERE collection_id=? AND cik=?",
            (
                legacy_response.receipt_id,
                hashlib.sha256(legacy_response.raw_payload).hexdigest(),
                legacy_response.raw_payload,
                legacy_response.collection_id,
                legacy_response.cik,
            ),
        )
        _ = connection.execute(
            "UPDATE sec_submission_runs SET receipt_id=?,payload_sha256=?,payload_json=? "
            "WHERE run_id=?",
            (
                legacy_response.receipt_id,
                hashlib.sha256(legacy_payload_json.encode()).hexdigest(),
                legacy_payload_json,
                run.run_id,
            ),
        )
        _ = connection.execute(
            "UPDATE sec_filing_observations SET receipt_id=? WHERE run_id=?",
            (legacy_response.receipt_id, run.run_id),
        )
        for _name, sql in trigger_rows:
            _ = connection.execute(sql)

    expected = SecSubmissionRun.model_validate_json(legacy_payload_json)
    assert "source_kind" not in expected.model_fields_set
    assert store.collection_run(run.collection_id, run.cik) == expected
    assert tuple(item.event for item in store.filings_for_run(run.run_id)) == snapshot.filings


def _response(collection_id: str, payload: bytes) -> SecSubmissionRawResponse:
    return SecSubmissionRawResponse(
        collection_id=collection_id,
        cik="0000320193",
        received_at=RECEIVED_AT,
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
