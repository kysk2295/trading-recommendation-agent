from __future__ import annotations

import datetime as dt
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


def test_sec_store_rejects_same_name_trigger_replacement(tmp_path: Path) -> None:
    store = SecEdgarStore(tmp_path / "sec.sqlite3")
    response = _response("sec-cycle-001", FIRST_AT, FIXTURE.read_bytes())
    snapshot = parse_sec_submission_snapshot(response)
    _ = store.append_receipt(response)
    _ = store.append_collection(_run(response), snapshot)
    with sqlite3.connect(store.path) as connection:
        _ = connection.execute("DROP TRIGGER sec_submission_runs_no_update")
        _ = connection.execute(
            "CREATE TRIGGER sec_submission_runs_no_update BEFORE UPDATE "
            "ON sec_submission_runs BEGIN SELECT 1; END"
        )

    with pytest.raises(ValueError):
        _ = store.collection_run(response.collection_id, response.cik)


def test_sec_store_rejects_foreign_version_zero_database_without_mutation(tmp_path: Path) -> None:
    path = tmp_path / "foreign.sqlite3"
    with sqlite3.connect(path) as connection:
        _ = connection.execute("CREATE TABLE unrelated(value TEXT)")
    path.chmod(0o600)
    before = path.read_bytes()

    with pytest.raises(ValueError):
        SecEdgarStore(path).preflight_write()

    assert path.read_bytes() == before
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (0,)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall() == [("unrelated",)]


def test_sec_store_preflight_rejects_structurally_corrupt_database(tmp_path: Path) -> None:
    store = SecEdgarStore(tmp_path / "sec.sqlite3")
    store.preflight_write()
    with sqlite3.connect(store.path) as connection:
        table_page = connection.execute(
            "SELECT rootpage FROM sqlite_master WHERE name='sec_filing_versions'"
        ).fetchone()[0]
        _ = connection.execute("PRAGMA writable_schema=ON")
        _ = connection.execute(
            "UPDATE sqlite_master SET rootpage=? WHERE name='sec_filing_versions_by_accession'",
            (table_page,),
        )
        _ = connection.execute("PRAGMA writable_schema=OFF")

    with pytest.raises(ValueError):
        store.preflight_write()


def test_sec_store_rejects_receipt_payload_tampering_after_trigger_restore(tmp_path: Path) -> None:
    store = SecEdgarStore(tmp_path / "sec.sqlite3")
    response = _response("sec-cycle-001", FIRST_AT, FIXTURE.read_bytes())
    snapshot = parse_sec_submission_snapshot(response)
    _ = store.append_receipt(response)
    _ = store.append_collection(_run(response), snapshot)
    with sqlite3.connect(store.path) as connection:
        trigger_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='sec_submission_receipts_no_update'"
        ).fetchone()[0]
        _ = connection.execute("DROP TRIGGER sec_submission_receipts_no_update")
        _ = connection.execute("UPDATE sec_submission_receipts SET raw_payload=X'00'")
        _ = connection.execute(trigger_sql)

    with pytest.raises(ValueError):
        _ = store.collection_run(response.collection_id, response.cik)


def test_sec_store_rejects_observation_bound_to_another_receipt(tmp_path: Path) -> None:
    store = SecEdgarStore(tmp_path / "sec.sqlite3")
    response = _response("sec-cycle-001", FIRST_AT, FIXTURE.read_bytes())
    snapshot = parse_sec_submission_snapshot(response)
    _ = store.append_receipt(response)
    run = store.append_collection(_run(response), snapshot).run
    other = _response("sec-cycle-002", SECOND_AT, FIXTURE.read_bytes())
    _ = store.append_receipt(other)
    with sqlite3.connect(store.path) as connection:
        trigger_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='sec_filing_observations_no_update'"
        ).fetchone()[0]
        _ = connection.execute("DROP TRIGGER sec_filing_observations_no_update")
        _ = connection.execute(
            "UPDATE sec_filing_observations SET receipt_id=? WHERE item_index=0",
            (other.receipt_id,),
        )
        _ = connection.execute(trigger_sql)

    with pytest.raises(ValueError):
        _ = store.filings_for_run(run.run_id)


def test_sec_store_rejects_run_columns_inconsistent_with_payload(tmp_path: Path) -> None:
    store = SecEdgarStore(tmp_path / "sec.sqlite3")
    response = _response("sec-cycle-001", FIRST_AT, FIXTURE.read_bytes())
    snapshot = parse_sec_submission_snapshot(response)
    _ = store.append_receipt(response)
    _ = store.append_collection(_run(response), snapshot)
    with sqlite3.connect(store.path) as connection:
        trigger_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='sec_submission_runs_no_update'"
        ).fetchone()[0]
        _ = connection.execute("DROP TRIGGER sec_submission_runs_no_update")
        _ = connection.execute("UPDATE sec_submission_runs SET filing_count=99")
        _ = connection.execute(trigger_sql)

    with pytest.raises(ValueError):
        _ = store.collection_run(response.collection_id, response.cik)


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
