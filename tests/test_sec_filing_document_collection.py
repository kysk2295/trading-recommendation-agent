from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

import pytest

from trading_agent.sec_filing_document_collection import (
    collect_sec_filing_document,
    collect_sec_filing_documents,
)
from trading_agent.sec_filing_document_models import (
    SecFilingDocumentRawResponse,
    SecFilingDocumentRun,
    SecFilingDocumentStatus,
    SecFilingDocumentTarget,
)
from trading_agent.sec_filing_document_store import (
    InvalidSecFilingDocumentStoreError,
    SecFilingDocumentStore,
)

STARTED_AT = dt.datetime(2026, 7, 20, 14, tzinfo=dt.UTC)
RECEIVED_AT = STARTED_AT + dt.timedelta(seconds=1)
COMPLETED_AT = RECEIVED_AT + dt.timedelta(seconds=1)


def _target(suffix: int = 1) -> SecFilingDocumentTarget:
    return SecFilingDocumentTarget(
        source_version_id=f"{suffix:x}" * 64,
        source_receipt_id="f" * 64,
        cik="0000320193",
        accession_number=f"0000320193-26-{suffix:06d}",
        primary_document=f"filing-{suffix}.htm",
        accepted_at=STARTED_AT - dt.timedelta(minutes=suffix),
        observed_at=STARTED_AT,
    )


class _Fetcher:
    def __init__(self, *, status_code: int = 200, payload: bytes = b"<html>ok</html>") -> None:
        self.status_code = status_code
        self.payload = payload
        self.calls: list[str] = []

    def fetch(self, target: SecFilingDocumentTarget) -> SecFilingDocumentRawResponse:
        self.calls.append(target.target_id)
        return SecFilingDocumentRawResponse(
            target_id=target.target_id,
            received_at=RECEIVED_AT,
            status_code=self.status_code,
            content_type="text/html" if self.status_code == 200 else "text/plain",
            raw_payload=self.payload,
        )


class _FailingFetcher:
    calls = 0

    def fetch(self, target: SecFilingDocumentTarget) -> SecFilingDocumentRawResponse:
        _ = target
        self.calls += 1
        raise RuntimeError("provider detail must not escape")


def _clock():
    moments = iter((STARTED_AT, COMPLETED_AT))
    return lambda: next(moments)


def test_collection_persists_raw_receipt_before_success_terminal(tmp_path: Path) -> None:
    store = SecFilingDocumentStore(tmp_path / "documents" / "sec.sqlite3")
    target = _target()
    fetcher = _Fetcher()

    run = collect_sec_filing_document(fetcher, store, target, _clock=_clock())

    assert run.status is SecFilingDocumentStatus.SUCCESS
    assert run.byte_count == len(b"<html>ok</html>")
    receipt = store.receipt_for_target(target.target_id)
    assert receipt is not None
    assert receipt.response.raw_payload == b"<html>ok</html>"
    assert store.run_for_target(target.target_id) == run
    assert fetcher.calls == [target.target_id]


def test_terminal_replay_never_calls_provider_or_appends_rows(tmp_path: Path) -> None:
    store = SecFilingDocumentStore(tmp_path / "documents" / "sec.sqlite3")
    target = _target()
    first = collect_sec_filing_document(_Fetcher(), store, target, _clock=_clock())
    failing = _FailingFetcher()

    replay = collect_sec_filing_document(failing, store, target, _clock=lambda: COMPLETED_AT)

    assert replay == first
    assert failing.calls == 0
    assert store.counts() == (1, 1)


def test_orphan_receipt_restart_finishes_without_provider(tmp_path: Path) -> None:
    store = SecFilingDocumentStore(tmp_path / "documents" / "sec.sqlite3")
    target = _target()
    response = _Fetcher().fetch(target)
    assert store.append_receipt(target, response) is True
    failing = _FailingFetcher()

    run = collect_sec_filing_document(
        failing,
        store,
        target,
        _clock=_clock(),
    )

    assert run.status is SecFilingDocumentStatus.SUCCESS
    assert run.receipt_id == response.receipt_id
    assert failing.calls == 0
    assert store.counts() == (1, 1)


def test_http_failure_keeps_raw_receipt_and_becomes_terminal(tmp_path: Path) -> None:
    store = SecFilingDocumentStore(tmp_path / "documents" / "sec.sqlite3")
    target = _target()

    run = collect_sec_filing_document(
        _Fetcher(status_code=503, payload=b"unavailable"),
        store,
        target,
        _clock=_clock(),
    )

    assert run.status is SecFilingDocumentStatus.FAILED
    assert run.failure_code == "http_status"
    assert run.receipt_id is not None
    assert store.receipt_for_target(target.target_id) is not None


def test_transport_failure_is_receiptless_and_redacted(tmp_path: Path) -> None:
    store = SecFilingDocumentStore(tmp_path / "documents" / "sec.sqlite3")
    target = _target()

    run = collect_sec_filing_document(
        _FailingFetcher(),
        store,
        target,
        _clock=_clock(),
    )

    assert run.status is SecFilingDocumentStatus.FAILED
    assert run.failure_code == "transport"
    assert run.receipt_id is None
    assert store.receipt_for_target(target.target_id) is None


def test_bounded_batch_stops_after_first_failure(tmp_path: Path) -> None:
    store = SecFilingDocumentStore(tmp_path / "documents" / "sec.sqlite3")
    fetcher = _Fetcher(status_code=503, payload=b"unavailable")

    runs = collect_sec_filing_documents(
        fetcher,
        store,
        (_target(1), _target(2)),
        _clock=_clock(),
    )

    assert len(runs) == 1
    assert runs[0].status is SecFilingDocumentStatus.FAILED
    assert fetcher.calls == [_target(1).target_id]


def test_store_rejects_terminal_that_disagrees_with_raw_http_response(tmp_path: Path) -> None:
    store = SecFilingDocumentStore(tmp_path / "documents" / "sec.sqlite3")
    target = _target()
    response = _Fetcher(status_code=503, payload=b"unavailable").fetch(target)
    _ = store.append_receipt(target, response)
    invalid = SecFilingDocumentRun(
        target=target,
        started_at=STARTED_AT,
        completed_at=COMPLETED_AT,
        status=SecFilingDocumentStatus.SUCCESS,
        failure_code=None,
        receipt_id=response.receipt_id,
        byte_count=len(response.raw_payload),
    )

    with pytest.raises(InvalidSecFilingDocumentStoreError):
        _ = store.append_run(invalid)


def test_store_append_only_triggers_reject_mutation(tmp_path: Path) -> None:
    store = SecFilingDocumentStore(tmp_path / "documents" / "sec.sqlite3")
    target = _target()
    _ = collect_sec_filing_document(_Fetcher(), store, target, _clock=_clock())

    with sqlite3.connect(store.path) as connection, pytest.raises(sqlite3.IntegrityError):
        _ = connection.execute(
            "DELETE FROM sec_filing_document_receipts WHERE target_id=?",
            (target.target_id,),
        )


def test_unrelated_tampered_receipt_blocks_all_public_reads(tmp_path: Path) -> None:
    store = SecFilingDocumentStore(tmp_path / "documents" / "sec.sqlite3")
    first = _target(1)
    second = _target(2)
    _ = collect_sec_filing_document(_Fetcher(), store, first, _clock=_clock())
    _ = collect_sec_filing_document(_Fetcher(), store, second, _clock=_clock())
    with sqlite3.connect(store.path) as connection:
        connection.executescript(
            "DROP TRIGGER sec_filing_document_receipts_no_update;"
            "UPDATE sec_filing_document_receipts SET raw_payload=X'00' "
            f"WHERE target_id='{first.target_id}';"
            "CREATE TRIGGER sec_filing_document_receipts_no_update "
            "BEFORE UPDATE ON sec_filing_document_receipts "
            "BEGIN SELECT RAISE(ABORT, 'append-only'); END;"
        )

    with pytest.raises(InvalidSecFilingDocumentStoreError):
        _ = store.receipt_for_target(second.target_id)
