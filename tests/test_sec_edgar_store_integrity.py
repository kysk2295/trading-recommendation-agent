from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

import trading_agent.sec_edgar_store_sql as store_sql
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.sec_edgar_history_collection import collect_sec_additional_history
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


class _HistoryFetcher:
    def fetch_additional_history(
        self,
        collection_id: str,
        cik: str,
        _file_name: str,
    ) -> SecSubmissionRawResponse:
        payload = Path(__file__).parent / "fixtures/sec_edgar/additional-history-001.json"
        return SecSubmissionRawResponse(
            collection_id=collection_id,
            cik=cik,
            received_at=SECOND_AT,
            status_code=200,
            content_type="application/json",
            raw_payload=payload.read_bytes(),
        )


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


def test_sec_writer_rejects_final_path_swap_during_sqlite_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SecEdgarStore(tmp_path / "sec.sqlite3")
    redirected = tmp_path / "redirected.sqlite3"
    real_connect = store_sql.sqlite3.connect

    def racing_connect(database: object, *args: object, **kwargs: object):
        if str(database) == str(store.path) or str(database).startswith(store.path.as_uri()):
            if store.path.exists():
                store.path.unlink()
            store.path.symlink_to(redirected)
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(store_sql.sqlite3, "connect", racing_connect)

    with pytest.raises(ValueError):
        store.preflight_write()

    assert not redirected.exists()


def test_sec_reader_rejects_final_path_swap_during_sqlite_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SecEdgarStore(tmp_path / "sec.sqlite3")
    redirected = SecEdgarStore(tmp_path / "redirected.sqlite3")
    store.preflight_write()
    redirected.preflight_write()
    real_connect = store_sql.sqlite3.connect

    def racing_connect(database: object, *args: object, **kwargs: object):
        if str(database).startswith(store.path.as_uri()):
            store.path.unlink()
            store.path.symlink_to(redirected.path)
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(store_sql.sqlite3, "connect", racing_connect)

    with pytest.raises(ValueError):
        _ = store.collection_run("sec-cycle-missing", "0000320193")


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


def test_sec_store_rejects_history_parent_binding_tamper(tmp_path: Path) -> None:
    store = SecEdgarStore(tmp_path / "sec.sqlite3")
    response = _response("sec-cycle-001", FIRST_AT, FIXTURE.read_bytes())
    snapshot = parse_sec_submission_snapshot(response)
    _ = store.append_receipt(response)
    parent = store.append_collection(_run(response), snapshot).run
    history = collect_sec_additional_history(
        _HistoryFetcher(),
        store,
        parent.collection_id,
        parent.cik,
        _clock=lambda: SECOND_AT,
    ).files[0].run
    with sqlite3.connect(store.path) as connection:
        row = connection.execute(
            "SELECT payload_json FROM sec_submission_runs WHERE run_id=?",
            (history.run_id,),
        ).fetchone()
        payload = json.loads(row[0])
        payload["parent_receipt_id"] = "0" * 64
        payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        trigger_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='sec_submission_runs_no_update'"
        ).fetchone()[0]
        _ = connection.execute("DROP TRIGGER sec_submission_runs_no_update")
        _ = connection.execute(
            "UPDATE sec_submission_runs SET payload_sha256=?,payload_json=? WHERE run_id=?",
            (hashlib.sha256(payload_json.encode()).hexdigest(), payload_json, history.run_id),
        )
        _ = connection.execute(trigger_sql)

    with pytest.raises(ValueError):
        _ = store.collection_run(history.collection_id, history.cik)


def test_sec_store_replays_legacy_v1_run_payload_without_history_fields(tmp_path: Path) -> None:
    store = SecEdgarStore(tmp_path / "sec.sqlite3")
    response = _response("sec-cycle-legacy", FIRST_AT, FIXTURE.read_bytes())
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


def test_sec_store_rejects_run_starting_after_receipt_on_append_and_replay(tmp_path: Path) -> None:
    store = SecEdgarStore(tmp_path / "sec.sqlite3")
    response = _response("sec-cycle-001", FIRST_AT, FIXTURE.read_bytes())
    snapshot = parse_sec_submission_snapshot(response)
    _ = store.append_receipt(response)
    noncausal = _run(response).model_copy(
        update={"started_at": SECOND_AT, "completed_at": SECOND_AT}
    )

    with pytest.raises(ValueError):
        _ = store.append_collection(noncausal, snapshot)

    valid = store.append_collection(_run(response), snapshot).run
    payload_json = canonical_experiment_ledger_json(noncausal)
    with sqlite3.connect(store.path) as connection:
        trigger_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='sec_submission_runs_no_update'"
        ).fetchone()[0]
        _ = connection.execute("DROP TRIGGER sec_submission_runs_no_update")
        _ = connection.execute(
            "UPDATE sec_submission_runs SET payload_sha256=?,payload_json=? WHERE run_id=?",
            (hashlib.sha256(payload_json.encode()).hexdigest(), payload_json, valid.run_id),
        )
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
