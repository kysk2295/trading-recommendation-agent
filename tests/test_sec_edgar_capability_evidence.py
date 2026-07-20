from __future__ import annotations

import datetime as dt
from pathlib import Path

from trading_agent.sec_edgar_collection import collect_sec_submissions
from trading_agent.sec_edgar_history_collection import collect_sec_additional_history
from trading_agent.sec_edgar_models import SecCollectionStatus, SecSubmissionRawResponse
from trading_agent.sec_edgar_store import SecEdgarStore

RECENT = Path(__file__).parent / "fixtures/sec_edgar/submissions.json"
HISTORY = Path(__file__).parent / "fixtures/sec_edgar/additional-history-001.json"
CIK = "0000320193"
COLLECTION_ID = "sec-capability-001"
RECENT_AT = dt.datetime(2026, 7, 20, 14, tzinfo=dt.UTC)
RECENT_COMPLETED_AT = RECENT_AT + dt.timedelta(seconds=5)
HISTORY_AT = RECENT_AT + dt.timedelta(minutes=1)
HISTORY_COMPLETED_AT = HISTORY_AT + dt.timedelta(seconds=5)


class _RecentFetcher:
    def __init__(self, *, status_code: int = 200) -> None:
        self.status_code = status_code

    def fetch_submissions(self, collection_id: str, cik: str) -> SecSubmissionRawResponse:
        return SecSubmissionRawResponse(
            collection_id=collection_id,
            cik=cik,
            received_at=RECENT_AT,
            status_code=self.status_code,
            content_type="application/json" if self.status_code == 200 else "text/plain",
            raw_payload=RECENT.read_bytes() if self.status_code == 200 else b"unavailable",
        )


class _HistoryFetcher:
    def __init__(self, payload: bytes | None = None) -> None:
        self.payload = HISTORY.read_bytes() if payload is None else payload

    def fetch_additional_history(
        self,
        collection_id: str,
        cik: str,
        file_name: str,
    ) -> SecSubmissionRawResponse:
        _ = file_name
        return SecSubmissionRawResponse(
            collection_id=collection_id,
            cik=cik,
            received_at=HISTORY_AT,
            status_code=200,
            content_type="application/json",
            raw_payload=self.payload,
        )


def test_complete_history_evidence_reports_actual_successful_coverage(tmp_path: Path) -> None:
    store = _successful_parent(tmp_path)
    _ = collect_sec_additional_history(
        _HistoryFetcher(),
        store,
        COLLECTION_ID,
        CIK,
        _clock=lambda: HISTORY_COMPLETED_AT,
    )

    evidence = store.capability_evidence(COLLECTION_ID, CIK)

    assert evidence is not None
    assert evidence.parent_status is SecCollectionStatus.SUCCESS
    assert evidence.declared_slice_count == 2
    assert evidence.successful_slice_count == 2
    assert evidence.failed_slice_count == 0
    assert evidence.missing_slice_count == 0
    assert evidence.filing_count == 3
    assert evidence.historical_from == dt.date(2025, 12, 30)
    assert evidence.latest_event_received_at == HISTORY_AT
    assert evidence.latest_source_heartbeat_at == HISTORY_COMPLETED_AT
    assert evidence.assessed_at == HISTORY_COMPLETED_AT


def test_missing_history_evidence_is_explicit_not_market_complete(tmp_path: Path) -> None:
    store = _successful_parent(tmp_path)

    evidence = store.capability_evidence(COLLECTION_ID, CIK)

    assert evidence is not None
    assert evidence.declared_slice_count == 2
    assert evidence.successful_slice_count == 1
    assert evidence.failed_slice_count == 0
    assert evidence.missing_slice_count == 1
    assert evidence.filing_count == 2
    assert evidence.historical_from == dt.date(2026, 7, 18)
    assert evidence.latest_event_received_at == RECENT_AT
    assert evidence.latest_source_heartbeat_at == RECENT_COMPLETED_AT


def test_failed_history_terminal_is_preserved_in_evidence(tmp_path: Path) -> None:
    store = _successful_parent(tmp_path)
    _ = collect_sec_additional_history(
        _HistoryFetcher(b"{}"),
        store,
        COLLECTION_ID,
        CIK,
        _clock=lambda: HISTORY_COMPLETED_AT,
    )

    evidence = store.capability_evidence(COLLECTION_ID, CIK)

    assert evidence is not None
    assert evidence.successful_slice_count == 1
    assert evidence.failed_slice_count == 1
    assert evidence.missing_slice_count == 0
    assert evidence.filing_count == 2
    assert evidence.latest_event_received_at == RECENT_AT
    assert evidence.latest_source_heartbeat_at == HISTORY_COMPLETED_AT


def test_failed_recent_terminal_has_zero_coverage_and_no_event_time(tmp_path: Path) -> None:
    store = SecEdgarStore(tmp_path / "ledger" / "sec.sqlite3")
    _ = collect_sec_submissions(
        _RecentFetcher(status_code=503),
        store,
        COLLECTION_ID,
        CIK,
        _clock=lambda: RECENT_COMPLETED_AT,
    )

    evidence = store.capability_evidence(COLLECTION_ID, CIK)

    assert evidence is not None
    assert evidence.parent_status is SecCollectionStatus.FAILED
    assert evidence.declared_slice_count == 1
    assert evidence.successful_slice_count == 0
    assert evidence.failed_slice_count == 1
    assert evidence.missing_slice_count == 0
    assert evidence.filing_count == 0
    assert evidence.historical_from is None
    assert evidence.latest_event_received_at is None
    assert evidence.latest_source_heartbeat_at == RECENT_COMPLETED_AT


def test_missing_parent_has_no_capability_evidence(tmp_path: Path) -> None:
    store = SecEdgarStore(tmp_path / "ledger" / "sec.sqlite3")

    assert store.capability_evidence(COLLECTION_ID, CIK) is None


def _successful_parent(tmp_path: Path) -> SecEdgarStore:
    store = SecEdgarStore(tmp_path / "ledger" / "sec.sqlite3")
    _ = collect_sec_submissions(
        _RecentFetcher(),
        store,
        COLLECTION_ID,
        CIK,
        _clock=lambda: RECENT_COMPLETED_AT,
    )
    return store
