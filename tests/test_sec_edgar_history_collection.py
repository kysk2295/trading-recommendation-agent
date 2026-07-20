from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from trading_agent.sec_edgar_collection import collect_sec_submissions
from trading_agent.sec_edgar_history_collection import collect_sec_additional_history
from trading_agent.sec_edgar_models import (
    SecCollectionStatus,
    SecSubmissionRawResponse,
    SecSubmissionSourceKind,
    sec_additional_history_collection_id,
)
from trading_agent.sec_edgar_parser import parse_sec_submission_snapshot
from trading_agent.sec_edgar_store import SecEdgarStore

RECENT = Path(__file__).parent / "fixtures/sec_edgar/submissions.json"
HISTORY = Path(__file__).parent / "fixtures/sec_edgar/additional-history-001.json"
PRIMARY_AT = dt.datetime(2026, 7, 20, 14, tzinfo=dt.UTC)
HISTORY_AT = PRIMARY_AT + dt.timedelta(minutes=1)


class RecentFetcher:
    def __init__(self, payload: bytes = RECENT.read_bytes()) -> None:
        self.payload = payload

    def fetch_submissions(self, collection_id: str, cik: str) -> SecSubmissionRawResponse:
        return SecSubmissionRawResponse(
            collection_id=collection_id,
            cik=cik,
            received_at=PRIMARY_AT,
            status_code=200,
            content_type="application/json",
            raw_payload=self.payload,
        )


class HistoryFetcher:
    def __init__(self, payload: bytes = HISTORY.read_bytes()) -> None:
        self.payload = payload
        self.calls: list[tuple[str, str, str]] = []

    def fetch_additional_history(
        self,
        collection_id: str,
        cik: str,
        file_name: str,
    ) -> SecSubmissionRawResponse:
        self.calls.append((collection_id, cik, file_name))
        return SecSubmissionRawResponse(
            collection_id=collection_id,
            cik=cik,
            received_at=HISTORY_AT,
            status_code=200,
            content_type="application/json",
            raw_payload=self.payload,
        )


def test_sec_history_collection_is_raw_first_parent_bound_and_replayable(tmp_path: Path) -> None:
    store = SecEdgarStore(tmp_path / "sec.sqlite3")
    primary = collect_sec_submissions(
        RecentFetcher(),
        store,
        "sec-cycle-001",
        "0000320193",
        _clock=lambda: PRIMARY_AT,
    )
    fetcher = HistoryFetcher()

    first = collect_sec_additional_history(fetcher, store, primary.run.collection_id, primary.run.cik)
    reject = HistoryFetcher()
    replay = collect_sec_additional_history(reject, store, primary.run.collection_id, primary.run.cik)

    assert first.discovered_file_count == 1
    assert first.selected_file_count == 1
    assert first.completed_file_count == 1
    assert first.filing_count == 1
    assert first.new_filing_version_count == 1
    assert first.replayed_file_count == 0
    assert replay.replayed_file_count == 1
    assert len(fetcher.calls) == 1
    assert reject.calls == []
    child = first.files[0].run
    assert child.source_kind is SecSubmissionSourceKind.ADDITIONAL_HISTORY
    assert child.parent_receipt_id == primary.run.receipt_id
    assert child.history_file is not None
    assert child.history_file.name == "CIK0000320193-submissions-001.json"


def test_sec_history_collection_preserves_raw_before_manifest_failure(tmp_path: Path) -> None:
    store = SecEdgarStore(tmp_path / "sec.sqlite3")
    primary = collect_sec_submissions(
        RecentFetcher(),
        store,
        "sec-cycle-001",
        "0000320193",
        _clock=lambda: PRIMARY_AT,
    )
    fetcher = HistoryFetcher(b"{}")

    result = collect_sec_additional_history(fetcher, store, primary.run.collection_id, primary.run.cik)

    child = result.files[0].run
    assert child.status is SecCollectionStatus.FAILED
    assert child.failure_code == "response_structure"
    assert store.receipt_for_collection(child.collection_id, child.cik) is not None


def test_sec_history_collection_recovers_orphan_after_parent_terminal(tmp_path: Path) -> None:
    store = SecEdgarStore(tmp_path / "sec.sqlite3")
    parent_completed_at = PRIMARY_AT + dt.timedelta(minutes=10)
    primary = collect_sec_submissions(
        RecentFetcher(),
        store,
        "sec-cycle-001",
        "0000320193",
        _clock=lambda: parent_completed_at,
    )
    parent_receipt = store.receipt_for_collection(primary.run.collection_id, primary.run.cik)
    assert parent_receipt is not None
    history_file = parse_sec_submission_snapshot(
        parent_receipt.response
    ).additional_history_files[0]
    assert primary.run.receipt_id is not None
    child_id = sec_additional_history_collection_id(primary.run.receipt_id, history_file)
    fetcher = HistoryFetcher()
    response = fetcher.fetch_additional_history(child_id, primary.run.cik, history_file.name)
    _ = store.append_receipt(response)
    fetcher.calls.clear()

    result = collect_sec_additional_history(
        fetcher,
        store,
        primary.run.collection_id,
        primary.run.cik,
    )

    assert result.files[0].run.status is SecCollectionStatus.SUCCESS
    assert result.files[0].run.completed_at == parent_completed_at
    assert fetcher.calls == []


@pytest.mark.parametrize("max_files", (0, 9))
def test_sec_history_collection_rejects_unsafe_bound_before_fetch(
    tmp_path: Path,
    max_files: int,
) -> None:
    store = SecEdgarStore(tmp_path / "sec.sqlite3")
    primary = collect_sec_submissions(
        RecentFetcher(),
        store,
        "sec-cycle-001",
        "0000320193",
        _clock=lambda: PRIMARY_AT,
    )
    fetcher = HistoryFetcher()

    with pytest.raises(ValueError):
        _ = collect_sec_additional_history(
            fetcher,
            store,
            primary.run.collection_id,
            primary.run.cik,
            max_files=max_files,
        )

    assert fetcher.calls == []
