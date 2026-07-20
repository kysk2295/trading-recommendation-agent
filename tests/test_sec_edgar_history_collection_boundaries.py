from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from trading_agent.sec_edgar_client import SecEdgarTransportError
from trading_agent.sec_edgar_collection import collect_sec_submissions
from trading_agent.sec_edgar_history_collection import collect_sec_additional_history
from trading_agent.sec_edgar_models import SecSubmissionRawResponse
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


class FailSecondHistoryFetcher(HistoryFetcher):
    def fetch_additional_history(
        self,
        collection_id: str,
        cik: str,
        file_name: str,
    ) -> SecSubmissionRawResponse:
        self.calls.append((collection_id, cik, file_name))
        failed = len(self.calls) == 2
        return SecSubmissionRawResponse(
            collection_id=collection_id,
            cik=cik,
            received_at=HISTORY_AT,
            status_code=503 if failed else 200,
            content_type="application/json",
            raw_payload=b"unavailable" if failed else self.payload,
        )


class TransportHistoryFetcher(HistoryFetcher):
    def fetch_additional_history(
        self,
        collection_id: str,
        cik: str,
        file_name: str,
    ) -> SecSubmissionRawResponse:
        self.calls.append((collection_id, cik, file_name))
        raise SecEdgarTransportError


def test_sec_history_collection_fetches_exactly_eight_files_in_manifest_order(
    tmp_path: Path,
) -> None:
    document = json.loads(RECENT.read_bytes())
    document["filings"]["files"] = [_history_manifest(index) for index in range(1, 9)]
    store = SecEdgarStore(tmp_path / "sec.sqlite3")
    primary = collect_sec_submissions(
        RecentFetcher(json.dumps(document).encode()),
        store,
        "sec-cycle-eight",
        "0000320193",
        _clock=lambda: PRIMARY_AT,
    )
    fetcher = HistoryFetcher()

    result = collect_sec_additional_history(
        fetcher,
        store,
        primary.run.collection_id,
        primary.run.cik,
        max_files=8,
    )

    assert result.completed_file_count == 8
    assert result.filing_count == 8
    assert tuple(call[2] for call in fetcher.calls) == tuple(
        f"CIK0000320193-submissions-{index:03d}.json" for index in range(1, 9)
    )


def test_sec_history_collection_stops_after_first_terminal_failure(tmp_path: Path) -> None:
    document = json.loads(RECENT.read_bytes())
    document["filings"]["files"] = [_history_manifest(index) for index in range(1, 4)]
    store = SecEdgarStore(tmp_path / "sec.sqlite3")
    primary = collect_sec_submissions(
        RecentFetcher(json.dumps(document).encode()),
        store,
        "sec-cycle-failure",
        "0000320193",
        _clock=lambda: PRIMARY_AT,
    )
    fetcher = FailSecondHistoryFetcher()

    result = collect_sec_additional_history(
        fetcher,
        store,
        primary.run.collection_id,
        primary.run.cik,
        max_files=3,
    )

    assert result.selected_file_count == 3
    assert len(result.files) == 2
    assert result.files[1].run.failure_code == "http_503"
    assert len(fetcher.calls) == 2


def test_sec_history_collection_replays_transport_terminal_without_fetch(tmp_path: Path) -> None:
    store = SecEdgarStore(tmp_path / "sec.sqlite3")
    primary = collect_sec_submissions(
        RecentFetcher(),
        store,
        "sec-cycle-transport",
        "0000320193",
        _clock=lambda: PRIMARY_AT,
    )
    failed = TransportHistoryFetcher()

    first = collect_sec_additional_history(
        failed,
        store,
        primary.run.collection_id,
        primary.run.cik,
        _clock=lambda: HISTORY_AT,
    )
    reject = HistoryFetcher()
    replay = collect_sec_additional_history(
        reject,
        store,
        primary.run.collection_id,
        primary.run.cik,
    )

    assert first.files[0].run.failure_code == "transport"
    assert first.files[0].run.receipt_id is None
    assert replay.replayed_file_count == 1
    assert reject.calls == []


def _history_manifest(index: int) -> dict[str, str | int]:
    return {
        "name": f"CIK0000320193-submissions-{index:03d}.json",
        "filingCount": 1,
        "filingFrom": "1994-01-01",
        "filingTo": "2025-12-31",
    }
